"""Transactional queue, immutable launch identity and fenced actor ownership."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ACTIVE = {'PROVISIONING', 'RUNNING', 'EVALUATING'}
# Lock namespaces that never stand for hardware (with the literal 'mock-cpu'): a graph's local selection and
# simulated attempts. They never quarantine a hardware claim, and they block only an identical lock name.
NON_HARDWARE = ('local:', 'simulation:')
GRAPH_LAUNCH_REFUSED = 'graph run is not running; launch refused'
GRAPH_CANCEL_REFUSED = 'graph job cancel requested; launch refused'
GRAPH_RESUME_REFUSED = ('a graph-owned job is retried only by its graph controller; resume refused '
                        '(retry or re-run the graph instead)')
RECEIPT_CHUNK = 500  # Stay far below SQLite's variable limit.
TERMINAL = {'FAILED', 'CANCELED', 'COMPLETED'}
TRANSITIONS = {
    'PENDING': {'QUEUED', 'CANCELED'}, 'QUEUED': {'PROVISIONING', 'CANCELED'},
    'PROVISIONING': {'RUNNING', 'FAILED', 'CANCELED', 'RETRYING'},
    'RUNNING': {'EVALUATING', 'FAILED', 'CANCELED', 'RETRYING'},
    'EVALUATING': {'COMPLETED', 'FAILED', 'CANCELED'},
    'RETRYING': {'QUEUED', 'CANCELED'}, 'FAILED': {'QUEUED'}, 'CANCELED': {'QUEUED'},
    'COMPLETED': set(),
}


@dataclass(frozen=True)
class AttemptLease:
    job_id: str
    attempt_id: str
    lease_token: str
    resource_id: str


@dataclass(frozen=True)
class PreparationLease:
    reservation_id: str
    owner_token: str
    resource_id: str


class JobStore:
    # A callable the graph controller installs (8.5 review Minor 7): error text from an executor or worker passes
    # through it before it is stored, so a configured credential it echoes never reaches the ledger.
    error_filter = None

    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript('''
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, manifest TEXT NOT NULL,
                  status TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, run_id TEXT,
                  created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS gpu_locks (gpu TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                  job_id TEXT NOT NULL, status TEXT NOT NULL, timestamp REAL NOT NULL, detail TEXT);
                CREATE TABLE IF NOT EXISTS job_attempts (
                  attempt_id TEXT PRIMARY KEY, job_id TEXT NOT NULL, lease_token TEXT NOT NULL,
                  resource_id TEXT NOT NULL, launch_id TEXT UNIQUE, launch_started INTEGER NOT NULL DEFAULT 0,
                  legacy INTEGER NOT NULL DEFAULT 0, created REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_leases (
                  resource_id TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL, attempt_id TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS job_destinations (
                  job_id TEXT PRIMARY KEY, receipt TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS resource_observations (
                  attempt_id TEXT PRIMARY KEY, facts TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS preparation_reservations (
                  reservation_id TEXT PRIMARY KEY, owner_token TEXT NOT NULL,
                  resource_id TEXT UNIQUE NOT NULL, profile TEXT NOT NULL, facts TEXT NOT NULL,
                  status TEXT NOT NULL, created REAL NOT NULL);
            ''')
            db.execute('BEGIN IMMEDIATE')
            columns = {r['name'] for r in db.execute('PRAGMA table_info(jobs)')}
            for name in ('graph_run_id', 'graph_node_id', 'active_attempt_id'):
                if name not in columns:
                    db.execute(f'ALTER TABLE jobs ADD COLUMN {name} TEXT')
            # Adopt only the control lease. Never invent a historical launch/producer receipt.
            rows = db.execute("SELECT j.*,g.gpu FROM jobs j LEFT JOIN gpu_locks g ON j.id=g.job_id "
                              "WHERE active_attempt_id IS NULL AND (g.gpu IS NOT NULL OR "
                              "status IN ('PROVISIONING','RUNNING','EVALUATING'))").fetchall()
            for row in rows:
                attempt, token = uuid.uuid4().hex, uuid.uuid4().hex
                resource = row['gpu'] or 'legacy-unknown:' + row['id']
                db.execute('INSERT INTO job_attempts VALUES (?,?,?,?,NULL,1,1,?)',
                           (attempt, row['id'], token, resource, time.time()))
                db.execute('UPDATE jobs SET active_attempt_id=? WHERE id=?', (attempt, row['id']))
                if row['gpu'] is None:
                    db.execute('INSERT INTO gpu_locks VALUES (?,?)', (resource, row['id']))
                db.execute('INSERT OR IGNORE INTO resource_leases VALUES (?,?,?)', (resource, row['id'], attempt))
                if row['status'] in ACTIVE:
                    error = 'legacy execution uncertain; reconcile original remote path before resuming'
                    db.execute("UPDATE jobs SET status='FAILED',error=? WHERE id=?", (error, row['id']))
                    db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'FAILED', time.time(), error))

    @contextmanager
    def connection(self):
        db = sqlite3.connect(self.path, timeout=30)
        db.row_factory = sqlite3.Row
        try:
            yield db
            db.commit()
        except BaseException:
            db.rollback()
            raise
        finally:
            db.close()

    @staticmethod
    def decode(row):
        if row is None:
            return None
        data = dict(row)
        data['manifest'] = json.loads(data['manifest'])
        return data

    def _submit(self, db, manifest, request_key, priority=0, graph_run_id=None, graph_node_id=None):
        row = db.execute('SELECT * FROM jobs WHERE request_key=?', (request_key,)).fetchone()
        if row:
            if json.loads(row['manifest'])['sha256'] != manifest['sha256']:
                raise ValueError('idempotency key reused with different manifest')
            return self.decode(row)
        job_id, now = uuid.uuid4().hex, time.time()
        from .registry import canonical
        serialized = canonical(manifest).decode() if graph_run_id else json.dumps(manifest)
        db.execute('INSERT INTO jobs (id,request_key,manifest,status,priority,created,updated,graph_run_id,graph_node_id) '
                   "VALUES (?,?,?,'PENDING',?,?,?,?,?)",
                   (job_id, request_key, serialized, priority, now, now, graph_run_id, graph_node_id))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'PENDING', now, None))
        return self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())

    def submit(self, manifest, request_key, priority=0, *, server_receipt=None):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            job = self._submit(db, manifest, request_key, priority)
            if server_receipt is not None:
                self._bind_server_receipt(db, job, server_receipt)
            return job

    def _bind_server_receipt(self, db, job, receipt):
        from .gpu_servers import ServerApprovalReceipt
        from .registry import canonical
        receipt = ServerApprovalReceipt.model_validate(receipt.model_dump() if hasattr(receipt, 'model_dump') else receipt)
        if job['manifest']['sha256'] != receipt.manifest_sha256 or job['manifest'].get('simulation'):
            raise ValueError('server receipt manifest differs from real submitted job')
        existing = db.execute('SELECT receipt FROM job_destinations WHERE job_id=?', (job['id'],)).fetchone()
        serialized = canonical(receipt.model_dump(mode='json')).decode()
        if existing:
            original = ServerApprovalReceipt.model_validate_json(existing['receipt'])
            if original.profile != receipt.profile:
                raise ValueError('job destination receipt is immutable')
            original.validate_destination(receipt.manifest_sha256, receipt.profile.transport, receipt.facts)
            return
        if job['attempts'] or job['status'] not in {'PENDING', 'QUEUED'}:
            raise ValueError('cannot retrofit a destination receipt onto an attempted job')
        db.execute('INSERT INTO job_destinations VALUES (?,?)', (job['id'], serialized))

    def bind_server_receipt(self, job_id, receipt):
        """CLI explicit execution may freeze its first route before the first claim."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('unknown job')
            self._bind_server_receipt(db, self.decode(row), receipt)

    def server_bound_job_ids(self):
        """Jobs frozen to an SSH server destination; RunPod workers never select them."""
        with self.connection() as db:
            return {row['job_id'] for row in db.execute('SELECT job_id FROM job_destinations')}

    def raw_server_receipts(self, job_ids):
        """Stored receipt text of the given jobs; callers parse each row and fail closed."""
        job_ids = list(job_ids)
        if not job_ids:
            return {}
        receipts = {}
        with self.connection() as db:
            for start in range(0, len(job_ids), RECEIPT_CHUNK):
                chunk = job_ids[start:start + RECEIPT_CHUNK]
                rows = db.execute('SELECT job_id,receipt FROM job_destinations WHERE job_id IN (%s)'
                                  % ','.join('?' * len(chunk)), chunk)
                receipts.update({row['job_id']: row['receipt'] for row in rows})
        return receipts

    def server_receipt(self, job_id):
        from .gpu_servers import ServerApprovalReceipt
        with self.connection() as db:
            row = db.execute('SELECT receipt FROM job_destinations WHERE job_id=?', (job_id,)).fetchone()
        return ServerApprovalReceipt.model_validate_json(row['receipt']) if row else None

    def _verify_server_destination(self, db, row, profile, facts):
        from .gpu_servers import ServerApprovalReceipt, validate_server_binding
        validate_server_binding(profile, facts)
        stored = db.execute('SELECT receipt FROM job_destinations WHERE job_id=?', (row['id'],)).fetchone()
        if stored is None:
            raise ValueError('approved server destination receipt required before claim')
        receipt = ServerApprovalReceipt.model_validate_json(stored['receipt'])
        if receipt.profile != profile:
            raise ValueError('job destination profile differs from original approval')
        manifest = json.loads(row['manifest'])
        receipt.validate_destination(manifest['sha256'], profile.transport, facts)
        return receipt

    def verify_server_destination(self, job_id, profile, facts):
        with self.connection() as db:
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('unknown job')
            return self._verify_server_destination(db, row, profile, facts)

    def get(self, job_id):
        with self.connection() as db:
            result = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        if result is None:
            raise ValueError('unknown job')
        return result

    def list(self):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created')]

    def validate_lease(self, db, lease):
        """The job row joined with its current attempt, inside the caller's transaction; else 'stale attempt lease'."""
        if not isinstance(lease, AttemptLease):
            raise ValueError('current attempt lease required')
        row = db.execute('SELECT j.*,a.resource_id,a.launch_id,a.launch_started,a.legacy FROM jobs j '
                         'JOIN job_attempts a ON j.active_attempt_id=a.attempt_id '
                         'WHERE j.id=? AND a.attempt_id=? AND a.lease_token=? AND a.resource_id=?',
                         (lease.job_id, lease.attempt_id, lease.lease_token, lease.resource_id)).fetchone()
        if row is None:
            raise ValueError('stale attempt lease')
        return row

    _validate_lease = validate_lease   # the pre-Task-8 name, kept as an alias

    def validated_attempt(self, lease):
        """``validate_lease`` on its own connection, as a plain dict."""
        with self.connection() as db:
            return dict(self.validate_lease(db, lease))

    def holds_lock(self, lease):
        """Whether this current attempt still holds its resource lease (a final attempt has released it)."""
        with self.connection() as db:
            self.validate_lease(db, lease)
            row = db.execute('SELECT 1 FROM resource_leases WHERE job_id=? AND attempt_id=? AND resource_id=?',
                             (lease.job_id, lease.attempt_id, lease.resource_id)).fetchone()
        return row is not None

    def current_lease(self, job_id):
        """Trusted control-plane hook for explicit reconciliation, never execution restart."""
        with self.connection() as db:
            row = db.execute('SELECT a.* FROM job_attempts a JOIN jobs j ON j.active_attempt_id=a.attempt_id '
                             'WHERE j.id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('no current attempt lease')
            return AttemptLease(job_id, row['attempt_id'], row['lease_token'], row['resource_id'])

    def filtered(self, error):
        """``error`` through the installed ``error_filter`` (unchanged without one)."""
        if error is None or self.error_filter is None:
            return error
        return self.error_filter(str(error))

    def transition(self, subject, status, error=None):
        job_id = subject.job_id if isinstance(subject, AttemptLease) else subject
        error = self.filtered(error)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if isinstance(subject, AttemptLease) or (row and (row['active_attempt_id'] or row['attempts'])):
                self.validate_lease(db, subject)
            if row is not None and row['status'] == status:
                return
            if row is None or status not in TRANSITIONS.get(row['status'], set()):
                raise ValueError(f'invalid state transition to {status}')
            now = time.time()
            db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?', (status, error, now, job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, status, now, error))

    @staticmethod
    def gpu_count(raw):
        manifest = json.loads(raw)
        if manifest.get('schema_version') == 2:
            operation = manifest['operation']
            return 0 if operation['lineage']['simulation'] else operation.get('resources', {}).get('facts', {}).get('gpu_count', 0)
        return 0 if manifest.get('simulation') else manifest.get('config', {}).get('resource_profile', {}).get('gpu_count', 1)

    def _resource_blocked(self, db, resource, resource_facts=None):
        from .gpu_servers import SSHServerFacts, SSHServerInspection
        # Provider labels do not prove distinct physical servers. Preserve unknown
        # locks and quarantine cross-provider claims when hardware cannot be mapped.
        providers = ('existing_runpod:', 'ssh_server:')
        provider = next((prefix for prefix in providers if resource.startswith(prefix)), None)
        held = [(row['resource'], row['facts']) for row in db.execute(
            'SELECT g.gpu AS resource,o.facts FROM gpu_locks g '
            'LEFT JOIN resource_leases l ON l.resource_id=g.gpu AND l.job_id=g.job_id '
            'LEFT JOIN resource_observations o ON o.attempt_id=l.attempt_id')]
        held += [(row['resource_id'], row['facts']) for row in db.execute('SELECT * FROM preparation_reservations')]
        for locked, observed in held:
            if resource == locked:
                return True
            if provider is None or locked == 'mock-cpu' or locked.startswith(NON_HARDWARE):
                continue
            if not locked.startswith(provider):
                return True
            if provider == 'ssh_server:':
                # A read-only inspection carries the same machine identity and full GPU inventory.
                if not isinstance(resource_facts, (SSHServerFacts, SSHServerInspection)) or observed is None:
                    return True
                previous = SSHServerFacts.model_validate_json(observed)
                if previous.server_id == resource_facts.server_id or ({gpu.uuid for gpu in previous.inventory}
                        & {gpu.uuid for gpu in resource_facts.inventory}):
                    return True
        return False

    def _claim(self, db, row, resource, resource_facts=None):
        now, attempt, token, launch = time.time(), uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        db.execute('INSERT INTO job_attempts VALUES (?,?,?,?,?,0,0,?)', (attempt, row['id'], token, resource, launch, now))
        db.execute('INSERT INTO gpu_locks VALUES (?,?)', (resource, row['id']))
        db.execute('INSERT INTO resource_leases VALUES (?,?,?)', (resource, row['id'], attempt))
        if resource_facts is not None:
            db.execute('INSERT INTO resource_observations VALUES (?,?)',
                       (attempt, resource_facts.model_dump_json()))
        db.execute("UPDATE jobs SET status='PROVISIONING',attempts=attempts+1,active_attempt_id=?,updated=? WHERE id=?",
                   (attempt, now, row['id']))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'PROVISIONING', now, resource))
        result = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
        result.update(lease=AttemptLease(row['id'], attempt, token, resource), launch_id=launch)
        return result

    def claim(self, gpu, max_concurrent=1, max_gpus=None, simulation=None, job_id=None,
              *, server_profile=None, server_facts=None):
        if max_concurrent < 1:
            raise ValueError('max_concurrent must be positive')
        if server_facts is not None:
            from .gpu_servers import SSHServerFacts
            if not isinstance(server_facts, SSHServerFacts):
                raise ValueError('server_facts must be verified SSHServerFacts')
        if server_profile is not None:
            from .gpu_servers import validate_server_binding
            validate_server_binding(server_profile, server_facts)
            if gpu != server_facts.exclusivity_key:
                raise ValueError('claim resource differs from verified destination')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('PROVISIONING','RUNNING','EVALUATING')").fetchone()[0]
            if active >= max_concurrent or self._resource_blocked(db, gpu, server_facts):
                return None
            used = sum(self.gpu_count(r[0]) for r in db.execute('SELECT manifest FROM jobs JOIN gpu_locks ON jobs.id=gpu_locks.job_id'))
            for row in db.execute("SELECT * FROM jobs WHERE status='QUEUED' AND graph_run_id IS NULL AND id NOT IN (SELECT job_id FROM gpu_locks) ORDER BY priority DESC,created"):
                if job_id is not None and row['id'] != job_id:
                    continue
                if simulation is not None and json.loads(row['manifest']).get('simulation') != simulation:
                    continue
                destination = db.execute('SELECT 1 FROM job_destinations WHERE job_id=?', (row['id'],)).fetchone()
                if server_profile is not None:
                    try:
                        self._verify_server_destination(db, row, server_profile, server_facts)
                    except ValueError:
                        if job_id is not None:
                            raise  # An explicitly targeted job reports why it cannot run here.
                        continue  # Unbound or bound elsewhere: never blocks other eligible jobs.
                elif destination:
                    continue
                if max_gpus is not None and used + self.gpu_count(row['manifest']) > max_gpus:
                    continue
                return self._claim(db, row, gpu, server_facts)
        return None

    def foreign_provider_lock(self, resource):
        """True when a job lock or preparation reservation belongs to another provider than `resource`."""
        providers = ('existing_runpod:', 'ssh_server:')
        provider = next((prefix for prefix in providers if resource.startswith(prefix)), None)
        if provider is None:
            return False
        with self.connection() as db:
            held = [row[0] for row in db.execute('SELECT gpu FROM gpu_locks')]
            held += [row[0] for row in db.execute('SELECT resource_id FROM preparation_reservations')]
        return any(locked != 'mock-cpu' and not locked.startswith((provider, *NON_HARDWARE)) for locked in held)

    def resource_blocked(self, facts):
        """Shared check for verified hardware (facts or inspection), active jobs and preparation reservations."""
        with self.connection() as db:
            return self._resource_blocked(db, facts.exclusivity_key, facts)

    def reserve_preparation(self, profile, facts):
        from .gpu_servers import validate_server_binding
        validate_server_binding(profile, facts)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            if self._resource_blocked(db, facts.exclusivity_key, facts):
                raise ValueError('server is reserved or has an uncertain resource lock')
            lease = PreparationLease(uuid.uuid4().hex, uuid.uuid4().hex, facts.exclusivity_key)
            db.execute("INSERT INTO preparation_reservations VALUES (?,?,?,?,?,'PREPARING',?)",
                       (lease.reservation_id, lease.owner_token, lease.resource_id,
                        profile.model_dump_json(), facts.model_dump_json(), time.time()))
            return lease

    @staticmethod
    def _preparation(db, lease):
        if not isinstance(lease, PreparationLease):
            raise ValueError('current preparation reservation lease required')
        row = db.execute('SELECT * FROM preparation_reservations WHERE reservation_id=? AND owner_token=? '
                         'AND resource_id=?', (lease.reservation_id, lease.owner_token, lease.resource_id)).fetchone()
        if row is None:
            raise ValueError('stale preparation reservation lease')
        return row

    def finish_preparation(self, lease, *, command_stopped=False):
        if command_stopped is not True:
            raise ValueError('confirmed remote command termination required before reservation release')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._preparation(db, lease)
            db.execute('DELETE FROM preparation_reservations WHERE reservation_id=?', (lease.reservation_id,))

    def mark_preparation_uncertain(self, lease):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._preparation(db, lease)
            db.execute("UPDATE preparation_reservations SET status='UNCERTAIN' WHERE reservation_id=?",
                       (lease.reservation_id,))

    def preparation_reservations(self):
        with self.connection() as db:
            rows = db.execute('SELECT reservation_id,resource_id,profile,facts,status,created '
                              'FROM preparation_reservations ORDER BY created').fetchall()
        return [dict(row) | {'profile': json.loads(row['profile']), 'facts': json.loads(row['facts'])} for row in rows]

    def reconcile_preparation(self, reservation_id, profile, *, confirm_preparation_stopped=False):
        from .gpu_servers import SSHServerProfile
        if confirm_preparation_stopped is not True:
            raise ValueError('explicit operator confirmation of remote preparation termination required')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM preparation_reservations WHERE reservation_id=?',
                             (reservation_id,)).fetchone()
            if row is None:
                raise ValueError('unknown preparation reservation')
            if SSHServerProfile.model_validate_json(row['profile']) != profile:
                raise ValueError('preparation destination differs from original reservation')
            lease = PreparationLease(reservation_id, row['owner_token'], row['resource_id'])
            self._preparation(db, lease)
            db.execute('DELETE FROM preparation_reservations WHERE reservation_id=?', (reservation_id,))

    def heartbeat(self, lease):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            if row['status'] not in ACTIVE:
                raise ValueError('attempt lease is not active')
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), lease.job_id))

    def set_run_id(self, lease, run_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.validate_lease(db, lease)
            db.execute('UPDATE jobs SET run_id=? WHERE id=?', (run_id, lease.job_id))

    def begin_launch(self, lease):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            if row['launch_started'] or row['legacy'] or row['status'] != 'PROVISIONING':
                raise ValueError('launch already started or uncertain; reconcile the same attempt')
            if row['graph_run_id'] is not None:
                # The point of no return: a pause or cancel that committed after the claim wins (Task 8 D12).
                run = db.execute('SELECT status FROM graph_runs WHERE id=?', (row['graph_run_id'],)).fetchone()
                if run is None or run['status'] != 'RUNNING':
                    raise ValueError(GRAPH_LAUNCH_REFUSED)
                if row['cancel_requested']:   # a job-level cancel (CLI/desktop) also wins before the launch
                    raise ValueError(GRAPH_CANCEL_REFUSED)
            db.execute('UPDATE job_attempts SET launch_started=1 WHERE attempt_id=?', (lease.attempt_id,))
            return row['launch_id']

    def abandon_unlaunched(self, lease, error):
        """Pre-launch refusal: begin_launch never ran, so no remote process can exist.
        Fail the attempt and free its lock; a started, legacy or non-provisioning attempt is untouched."""
        error = self.filtered(error)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            if row['launch_started'] or row['legacy'] or row['status'] != 'PROVISIONING':
                return False
            now = time.time()
            db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (error, now, lease.job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (lease.job_id, 'FAILED', now, error))
            self._release(db, lease)
            return True

    def takeover(self, lease):
        """Explicit trusted actor handoff; does not authorize another remote launch."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            return self._takeover(db, lease, row)

    def _takeover(self, db, lease, row):
        token = uuid.uuid4().hex
        db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?', (token, lease.attempt_id))
        result = self.decode(row)
        result.update(lease=AttemptLease(lease.job_id, lease.attempt_id, token, lease.resource_id))
        return result

    def cancel(self, job_id):
        """Request a cancel: an active attempt is flagged, an unclaimed job is canceled at once. A finished graph
        attempt that still holds its lock (uncertain, or a crash before its release) is flagged too, so the late
        result its reconcile collects is CANCELED (11.5 R1 I-1); a final attempt is left unchanged."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,graph_run_id FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('unknown job')
            if row['status'] in ACTIVE or (row['graph_run_id'] is not None and row['status'] in {'FAILED', 'CANCELED'}
                                           and self._holds_any_lock(db, job_id)):
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
            elif row['status'] in {'PENDING', 'QUEUED', 'RETRYING'}:
                db.execute("UPDATE jobs SET status='CANCELED',updated=? WHERE id=?", (time.time(), job_id))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'CANCELED', time.time(), None))

    @staticmethod
    def _holds_any_lock(db, job_id):
        return db.execute('SELECT 1 FROM gpu_locks WHERE job_id=? UNION ALL SELECT 1 FROM resource_leases '
                          'WHERE job_id=?', (job_id, job_id)).fetchone() is not None

    def _release(self, db, lease):
        db.execute('DELETE FROM resource_leases WHERE job_id=? AND attempt_id=?', (lease.job_id, lease.attempt_id))
        db.execute('DELETE FROM gpu_locks WHERE job_id=? AND gpu=?', (lease.job_id, lease.resource_id))

    def release_gpu(self, lease, *, execution_stopped=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            if not execution_stopped or row['status'] in ACTIVE:
                raise ValueError('current attempt termination confirmation required')
            self._release(db, lease)

    def gpu_locked(self, gpu):
        with self.connection() as db:
            return self._resource_blocked(db, gpu)

    def recover(self, stale_after_seconds=120):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            rows = db.execute("SELECT id FROM jobs WHERE status IN ('PROVISIONING','RUNNING','EVALUATING') AND updated<?",
                              (time.time() - stale_after_seconds,)).fetchall()
            for row in rows:
                error = 'worker heartbeat expired; reconcile execution before releasing GPU lock'
                db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (error, time.time(), row['id']))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'FAILED', time.time(), error))
        return len(rows)

    def reset_for_resume(self, job_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status,graph_run_id FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is not None and row['graph_run_id'] is not None:
                # Only the graph controller requeues a graph job: a bounded, classified retry (11.5 R1 m7).
                raise ValueError(GRAPH_RESUME_REFUSED)
            if row is None or row['status'] not in {'FAILED', 'CANCELED'}:
                raise ValueError('only failed/canceled jobs can resume')
            if db.execute('SELECT 1 FROM gpu_locks WHERE job_id=?', (job_id,)).fetchone():
                raise ValueError('reconcile the locked execution before resuming')
            db.execute("UPDATE jobs SET status='QUEUED',cancel_requested=0,error=NULL,active_attempt_id=NULL,updated=? WHERE id=?", (time.time(), job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'QUEUED', time.time(), 'manual resume'))

    def reconcile_result(self, lease, status, error=None):
        if status not in TERMINAL:
            raise ValueError('invalid reconciled terminal status')
        error = self.filtered(error)
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self.validate_lease(db, lease)
            if row['status'] != 'FAILED':
                raise ValueError('only failed jobs may be reconciled')
            db.execute('UPDATE jobs SET status=?,error=?,updated=? WHERE id=?', (status, error, time.time(), lease.job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (lease.job_id, status, time.time(), 'remote result reconciled'))
            self._release(db, lease)

    def materialize_job_evidence(self, job_id):
        from .registry import canonical, immutable_write
        manifest = self.get(job_id)['manifest']
        path = self.path.parent / 'jobs' / job_id / 'manifest.json'
        immutable_write(path, canonical(manifest))
        return path
