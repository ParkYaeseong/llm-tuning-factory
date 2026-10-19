"""Transactional queue, immutable launch identity and fenced actor ownership."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

ACTIVE = {'PROVISIONING', 'RUNNING', 'EVALUATING'}
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


class JobStore:
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

    def submit(self, manifest, request_key, priority=0):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            return self._submit(db, manifest, request_key, priority)

    def get(self, job_id):
        with self.connection() as db:
            result = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone())
        if result is None:
            raise ValueError('unknown job')
        return result

    def list(self):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute('SELECT * FROM jobs ORDER BY created')]

    def _validate_lease(self, db, lease):
        if not isinstance(lease, AttemptLease):
            raise ValueError('current attempt lease required')
        row = db.execute('SELECT j.*,a.resource_id,a.launch_id,a.launch_started,a.legacy FROM jobs j '
                         'JOIN job_attempts a ON j.active_attempt_id=a.attempt_id '
                         'WHERE j.id=? AND a.attempt_id=? AND a.lease_token=? AND a.resource_id=?',
                         (lease.job_id, lease.attempt_id, lease.lease_token, lease.resource_id)).fetchone()
        if row is None:
            raise ValueError('stale attempt lease')
        return row

    def current_lease(self, job_id):
        """Trusted control-plane hook for explicit reconciliation, never execution restart."""
        with self.connection() as db:
            row = db.execute('SELECT a.* FROM job_attempts a JOIN jobs j ON j.active_attempt_id=a.attempt_id '
                             'WHERE j.id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('no current attempt lease')
            return AttemptLease(job_id, row['attempt_id'], row['lease_token'], row['resource_id'])

    def transition(self, subject, status, error=None):
        job_id = subject.job_id if isinstance(subject, AttemptLease) else subject
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT * FROM jobs WHERE id=?', (job_id,)).fetchone()
            if isinstance(subject, AttemptLease) or (row and (row['active_attempt_id'] or row['attempts'])):
                self._validate_lease(db, subject)
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

    def _resource_blocked(self, db, resource):
        if db.execute('SELECT 1 FROM gpu_locks WHERE gpu=?', (resource,)).fetchone():
            return True
        # Old names cannot safely be mapped to physical Pods. Quarantine all real claims.
        if resource.startswith('existing_runpod:'):
            return bool(db.execute("SELECT 1 FROM gpu_locks g JOIN jobs j ON g.job_id=j.id "
                                   "WHERE g.gpu NOT LIKE 'existing_runpod:%' AND g.gpu != 'mock-cpu'").fetchone())
        return False

    def _claim(self, db, row, resource):
        now, attempt, token, launch = time.time(), uuid.uuid4().hex, uuid.uuid4().hex, uuid.uuid4().hex
        db.execute('INSERT INTO job_attempts VALUES (?,?,?,?,?,0,0,?)', (attempt, row['id'], token, resource, launch, now))
        db.execute('INSERT INTO gpu_locks VALUES (?,?)', (resource, row['id']))
        db.execute('INSERT INTO resource_leases VALUES (?,?,?)', (resource, row['id'], attempt))
        db.execute("UPDATE jobs SET status='PROVISIONING',attempts=attempts+1,active_attempt_id=?,updated=? WHERE id=?",
                   (attempt, now, row['id']))
        db.execute('INSERT INTO events VALUES (?,?,?,?)', (row['id'], 'PROVISIONING', now, resource))
        result = self.decode(db.execute('SELECT * FROM jobs WHERE id=?', (row['id'],)).fetchone())
        result.update(lease=AttemptLease(row['id'], attempt, token, resource), launch_id=launch)
        return result

    def claim(self, gpu, max_concurrent=1, max_gpus=None, simulation=None, job_id=None):
        if max_concurrent < 1:
            raise ValueError('max_concurrent must be positive')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('PROVISIONING','RUNNING','EVALUATING')").fetchone()[0]
            if active >= max_concurrent or self._resource_blocked(db, gpu):
                return None
            used = sum(self.gpu_count(r[0]) for r in db.execute('SELECT manifest FROM jobs JOIN gpu_locks ON jobs.id=gpu_locks.job_id'))
            for row in db.execute("SELECT * FROM jobs WHERE status='QUEUED' AND graph_run_id IS NULL AND id NOT IN (SELECT job_id FROM gpu_locks) ORDER BY priority DESC,created"):
                if job_id is not None and row['id'] != job_id:
                    continue
                if simulation is not None and json.loads(row['manifest']).get('simulation') != simulation:
                    continue
                if max_gpus is not None and used + self.gpu_count(row['manifest']) > max_gpus:
                    continue
                return self._claim(db, row, gpu)
        return None

    def heartbeat(self, lease):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._validate_lease(db, lease)
            if row['status'] not in ACTIVE:
                raise ValueError('attempt lease is not active')
            db.execute('UPDATE jobs SET updated=? WHERE id=?', (time.time(), lease.job_id))

    def set_run_id(self, lease, run_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._validate_lease(db, lease)
            db.execute('UPDATE jobs SET run_id=? WHERE id=?', (run_id, lease.job_id))

    def begin_launch(self, lease):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._validate_lease(db, lease)
            if row['launch_started'] or row['legacy'] or row['status'] != 'PROVISIONING':
                raise ValueError('launch already started or uncertain; reconcile the same attempt')
            db.execute('UPDATE job_attempts SET launch_started=1 WHERE attempt_id=?', (lease.attempt_id,))
            return row['launch_id']

    def takeover(self, lease):
        """Explicit trusted actor handoff; does not authorize another remote launch."""
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._validate_lease(db, lease)
            return self._takeover(db, lease, row)

    def _takeover(self, db, lease, row):
        token = uuid.uuid4().hex
        db.execute('UPDATE job_attempts SET lease_token=? WHERE attempt_id=?', (token, lease.attempt_id))
        result = self.decode(row)
        result.update(lease=AttemptLease(lease.job_id, lease.attempt_id, token, lease.resource_id))
        return result

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None:
                raise ValueError('unknown job')
            if row['status'] in ACTIVE:
                db.execute('UPDATE jobs SET cancel_requested=1 WHERE id=?', (job_id,))
            elif row['status'] in {'PENDING', 'QUEUED', 'RETRYING'}:
                db.execute("UPDATE jobs SET status='CANCELED',updated=? WHERE id=?", (time.time(), job_id))
                db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'CANCELED', time.time(), None))

    def _release(self, db, lease):
        db.execute('DELETE FROM resource_leases WHERE job_id=? AND attempt_id=?', (lease.job_id, lease.attempt_id))
        db.execute('DELETE FROM gpu_locks WHERE job_id=? AND gpu=?', (lease.job_id, lease.resource_id))

    def release_gpu(self, lease, *, execution_stopped=False):
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._validate_lease(db, lease)
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
            row = db.execute('SELECT status FROM jobs WHERE id=?', (job_id,)).fetchone()
            if row is None or row['status'] not in {'FAILED', 'CANCELED'}:
                raise ValueError('only failed/canceled jobs can resume')
            if db.execute('SELECT 1 FROM gpu_locks WHERE job_id=?', (job_id,)).fetchone():
                raise ValueError('reconcile the locked execution before resuming')
            db.execute("UPDATE jobs SET status='QUEUED',cancel_requested=0,error=NULL,active_attempt_id=NULL,updated=? WHERE id=?", (time.time(), job_id))
            db.execute('INSERT INTO events VALUES (?,?,?,?)', (job_id, 'QUEUED', time.time(), 'manual resume'))

    def reconcile_result(self, lease, status, error=None):
        if status not in TERMINAL:
            raise ValueError('invalid reconciled terminal status')
        with self.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._validate_lease(db, lease)
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
