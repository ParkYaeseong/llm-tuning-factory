"""Task 8.3: the tracking outbox and its MLflow publisher (plan §4 "8.3" and §6; decisions D5, D6, D7, D11).

Events are enqueued in the same SQLite transaction as the ledger change they describe (``GraphStore.submit``,
``claim_ready``, ``record_outputs``, ``complete_local_selection``, ``classify_attempt`` and ``finalize_run``), once
per stable idempotency key. The publisher maps them to an MLflow graph -> node -> attempt run hierarchy:

- Every non-idempotent create (a run, a model version) records a durable intent first. After any uncertainty the
  next try searches by the stable key and adopts what it finds: the earliest run or the lowest version, with the
  others listed and never deleted. A search that fails never leads to a create; after the bounded retries the
  event is UNCERTAIN, and an operator's ``rearm`` searches first again.
- Acknowledging an ``attempt_evidence`` event sets ``tracking_outbox.acknowledged`` in the same transaction. That
  is the existing gate that lets dependants compile (D5); a model registration never gates anything.
- Retries back off exponentially on the injected clock and never touch jobs, attempts or locks.
- A real run is published only when ``tracking_settings_sha256(tracker.public_settings())`` equals the plan's
  pinned ``TrackingDestination`` (D11). Otherwise its events wait, PENDING, without consuming a try.
- Simulation runs never contact MLflow: a ``LocalEvidenceSink`` under ``state/tracking/<run_id>/`` acknowledges
  them (D7).

Payloads hold identities, hashes and state-relative evidence paths only. The SHA256 of every local evidence file is
fixed at enqueue time and checked again at send time (``local evidence changed after enqueue``). Credentials never
enter an event, a receipt, a tag, a param or a stored error.
"""
from __future__ import annotations

import json
import math
import os
import re
import tempfile
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, JsonValue

from .config import StrictModel
from .model_artifacts import ModelArtifactRef, ModelDescriptor
from .projects import LocalId
from .registry import canonical, digest, file_digest, immutable_write
from .tracking import TRACKING_UNCERTAIN, tracking_settings_sha256

EventKind = Literal["graph_run", "node_run", "attempt_run", "attempt_evidence", "model_registration",
                    "graph_summary"]
EVENT_STATUSES = ("PENDING", "ACKNOWLEDGED", "FAILED", "UNCERTAIN")
KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,249}$"   # no quote: keys become MLflow filter values
EVENTS_TABLE = '''CREATE TABLE IF NOT EXISTS tracking_events (
  event_id TEXT PRIMARY KEY, idempotency_key TEXT UNIQUE NOT NULL, kind TEXT NOT NULL,
  run_id TEXT NOT NULL, node_id TEXT, job_id TEXT, attempt_id TEXT, depends_on TEXT, payload TEXT NOT NULL,
  status TEXT NOT NULL CHECK (status IN ('PENDING','ACKNOWLEDGED','FAILED','UNCERTAIN')),
  tries INTEGER NOT NULL DEFAULT 0, next_attempt_at REAL NOT NULL, claim_token TEXT, claim_expires REAL,
  intent TEXT, remote_identity TEXT, last_error TEXT, created REAL NOT NULL, updated REAL NOT NULL)'''
EVENTS_INDEX = 'CREATE INDEX IF NOT EXISTS tracking_events_due ON tracking_events(status, next_attempt_at)'

KEY_REUSED = "tracking idempotency key reused with different payload"
PARENT_MISSING = "tracking parent event is missing"
STALE_CLAIM = "stale tracking claim"
EVIDENCE_CHANGED = "local evidence changed after enqueue"
DESTINATION_DIFFERS = ("tracking destination differs from the approved plan; restart the controller with the "
                       "approved MLflow settings")
TRACKER_MISSING = "MLflow tracking is not configured for this controller; the real graph run waits for it"
NO_DESTINATION = "the real graph run has no pinned tracking destination"
PRODUCERS = frozenset({"train", "merge", "materialize"})
TERMINAL = {"COMPLETED": "FINISHED", "FAILED": "FAILED", "CANCELED": "KILLED"}
# What an attempt leaves locally, besides the job manifest and the accepted result; never dataset bytes.
LAUNCH_EVIDENCE = ("command.json", "model-inputs.json", "binding-evidence.json", "remote-result.json",
                   "remote-artifacts.json", "train.log", "worker.log")
SECRET_VARIABLES = ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_PASSWORD")
_SAFE_PART = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")


def redact(text, limit=500) -> str:
    """Text that may be stored or shown: configured secret values, bearer/basic credentials, ``secret=`` style
    pairs and URL userinfo are removed, then it is truncated."""
    text = str(text)
    for name in SECRET_VARIABLES:
        value = os.environ.get(name)
        if value and len(value) >= 6:
            text = text.replace(value, "[redacted]")
    text = re.sub(r"(?i)\b(bearer|basic)\s+[^\s,;'\"]+", r"\1 [redacted]", text)
    text = re.sub(r"(?i)\b(client_secret|access_token|token|password)=[^\s&;,'\"]+", r"\1=[redacted]", text)
    text = re.sub(r"://[^/\s@]+@", "://[redacted]@", text)
    return text[:limit]


def tracking_event_id(kind: str, key: str) -> str:
    return digest(canonical({"kind": kind, "key": key}))


class TrackingEvent(StrictModel):
    """One tracking side effect under a stable idempotency key. ``payload``: identities, hashes and state-relative
    evidence paths only."""
    run_id: LocalId
    node_id: LocalId | None = None
    job_id: LocalId | None = None
    attempt_id: LocalId | None = None
    kind: EventKind
    idempotency_key: str = Field(pattern=KEY_PATTERN)
    depends_on_key: str | None = Field(default=None, pattern=KEY_PATTERN)
    payload: dict[str, JsonValue]

    @property
    def event_id(self) -> str:
        return tracking_event_id(self.kind, self.idempotency_key)


@dataclass(frozen=True)
class ClaimedEvent:
    event_id: str
    claim_token: str
    event: TrackingEvent
    intent: dict | None             # a create may already have been sent: search before any new create
    parent_identity: dict | None    # the receipt of ``depends_on`` (e.g. the MLflow parent run ID)


@dataclass(frozen=True)
class OutboxRetryPolicy:
    """``max_tries`` retries after the first send, waiting ``base * factor**(n-1)`` seconds (capped) after the n-th
    failure: 30, 60, 120, 240, 480, 960, 1920 and 3600 s by default, then FAILED (or UNCERTAIN)."""
    max_tries: int = 8
    base_seconds: float = 30
    factor: float = 2
    cap_seconds: float = 3600

    def __post_init__(self):
        if isinstance(self.max_tries, bool) or not isinstance(self.max_tries, int) or self.max_tries < 1:
            raise ValueError("max_tries must be a positive integer")
        values = (self.base_seconds, self.factor, self.cap_seconds)
        if not all(isinstance(value, (int, float)) and math.isfinite(value) for value in values) or (
                self.base_seconds <= 0 or self.factor < 1 or self.cap_seconds < self.base_seconds):
            raise ValueError("retry delays must be finite, positive and non-decreasing")

    def delay(self, tries: int) -> float:
        return float(min(self.cap_seconds, self.base_seconds * self.factor ** (tries - 1)))


# -- enqueue and event builders (called inside the ledger's own transactions) ------------------------

def enqueue_event(db, event: TrackingEvent, now: float) -> str:
    """Insert ``event`` in the caller's transaction, once per idempotency key. The same key with anything else
    different is refused. Its parent (``depends_on_key``) must already be enqueued."""
    if not isinstance(event, TrackingEvent):
        raise TypeError("a typed TrackingEvent is required")
    payload = canonical(event.payload).decode()
    depends_on = None
    if event.depends_on_key is not None:
        parent = db.execute('SELECT event_id FROM tracking_events WHERE idempotency_key=?',
                            (event.depends_on_key,)).fetchone()
        if parent is None:
            raise ValueError(PARENT_MISSING)
        depends_on = parent[0]
    identity = (event.event_id, event.kind, event.run_id, event.node_id, event.job_id, event.attempt_id, depends_on,
                payload)
    row = db.execute('SELECT event_id,kind,run_id,node_id,job_id,attempt_id,depends_on,payload FROM tracking_events '
                     'WHERE idempotency_key=?', (event.idempotency_key,)).fetchone()
    if row is not None:
        if tuple(row) != identity:
            raise ValueError(KEY_REUSED)
        return event.event_id
    db.execute('INSERT INTO tracking_events (event_id,idempotency_key,kind,run_id,node_id,job_id,attempt_id,'
               'depends_on,payload,status,tries,next_attempt_at,created,updated) VALUES (?,?,?,?,?,?,?,?,?,?,0,?,?,?)',
               (event.event_id, event.idempotency_key, event.kind, event.run_id, event.node_id, event.job_id,
                event.attempt_id, depends_on, payload, 'PENDING', now, now, now))
    return event.event_id


def _plan(plan_json: str) -> dict:
    data = json.loads(plan_json)
    return {"sha256": data["sha256"], "project_id": data["project_revision"]["spec"]["project_id"],
            "simulation": bool(data.get("simulation", True))}


def _graph_tags(run_id, plan):
    return {"factory.graph_run_id": run_id, "factory.plan_sha256": plan["sha256"],
            "factory.simulation": str(plan["simulation"]).lower(), "factory.workflow_id": plan["project_id"]}


def graph_event(run_id: str, plan_json: str, approval_json: str) -> TrackingEvent:
    """The graph run: ``lineage/plan.json`` and ``lineage/approval.json`` (the ledger's exact bytes)."""
    plan = _plan(plan_json)
    return TrackingEvent(run_id=run_id, kind="graph_run", idempotency_key=f"graph:{run_id}", payload={
        "run_name": f"graph {plan['project_id']} {run_id[:8]}", "tags": _graph_tags(run_id, plan),
        "plan_json_sha256": digest(plan_json.encode()), "approval_json_sha256": digest(approval_json.encode()),
        "params": {"factory.plan_sha256": plan["sha256"], "factory.workflow_id": plan["project_id"]}})


def node_event(run_id: str, plan_json: str, node_id: str, job_id: str, manifest: dict) -> TrackingEvent:
    tags = _graph_tags(run_id, _plan(plan_json)) | {
        "factory.node_id": node_id, "factory.job_id": job_id, "factory.node_kind": manifest["operation"]["kind"]}
    return TrackingEvent(run_id=run_id, node_id=node_id, job_id=job_id, kind="node_run",
                         idempotency_key=f"node:{run_id}:{node_id}", depends_on_key=f"graph:{run_id}",
                         payload={"run_name": node_id, "tags": tags})


@dataclass(frozen=True)
class AttemptFacts:
    """Ledger identities of one attempt, read inside the enqueueing transaction."""
    run_id: str
    node_id: str
    job_id: str
    attempt_id: str
    launch_id: str | None
    manifest: dict
    created: float


def attempt_files(state_dir, job_id: str, launch_id: str | None) -> dict:
    """lineage name -> {state-relative path, sha256} of the attempt's local evidence that exists now."""
    state = Path(state_dir)
    if not _SAFE_PART.fullmatch(job_id) or launch_id is not None and not _SAFE_PART.fullmatch(launch_id):
        raise ValueError("unsafe attempt identity")
    candidates = [("manifest.json", f"jobs/{job_id}/manifest.json")]
    if launch_id:
        launch = f"jobs/{job_id}/launches/{launch_id}"
        if not (state / launch).is_symlink():
            candidates += [(name, f"{launch}/{name}") for name in LAUNCH_EVIDENCE]
        candidates.append(("result.json", f"{launch}.result.json"))
    files = {}
    for name, relative in candidates:
        path = state / relative
        if not path.is_symlink() and path.is_file():
            files[name] = {"path": relative, "sha256": file_digest(path)}
    return files


def read_descriptor(artifacts, ref: ModelArtifactRef) -> ModelDescriptor:
    """A stored artifact's descriptor, checked against its content address (the inventory is not re-hashed)."""
    data = (Path(artifacts.root) / ref.artifact_id / ref.inventory_sha256 / "descriptor.json").read_bytes()
    if digest(data) != ref.artifact_id:
        raise ValueError("artifact descriptor hash mismatch")
    return ModelDescriptor.model_validate_json(data)


def _metrics(outputs):
    evaluation = (outputs or {}).get("evaluation")
    metrics = (evaluation or {}).get("evaluation", {}).get("metrics") or {}
    return {key: value for key, value in sorted(metrics.items()) if isinstance(value, (int, float))
            and not isinstance(value, bool) and math.isfinite(value)}


def attempt_events(store, facts: AttemptFacts, plan_json: str, approval_json: str, *, status: str,
                   outputs_json: str | None = None, disposition: str | None = None,
                   error: str | None = None) -> list[TrackingEvent]:
    """The attempt's ancestors (graph, node, enqueued idempotently), its run, its evidence and, for a real eligible
    model of a train/merge/materialize attempt, its registration (D6)."""
    plan, manifest = _plan(plan_json), facts.manifest
    kind = manifest["operation"]["kind"]
    run_id, node_id, attempt_id = facts.run_id, facts.node_id, facts.attempt_id
    identity = dict(run_id=run_id, node_id=node_id, job_id=facts.job_id, attempt_id=attempt_id)
    tags = _graph_tags(run_id, plan) | {
        "factory.node_id": node_id, "factory.job_id": facts.job_id, "factory.attempt_id": attempt_id,
        "factory.launch_id": facts.launch_id or "", "factory.manifest_sha256": manifest["sha256"]}
    events = [graph_event(run_id, plan_json, approval_json),
              node_event(run_id, plan_json, node_id, facts.job_id, manifest),
              TrackingEvent(**identity, kind="attempt_run", idempotency_key=f"attempt:{attempt_id}",
                            depends_on_key=f"node:{run_id}:{node_id}",
                            payload={"run_name": f"{node_id} {attempt_id[:8]}", "tags": tags})]
    outputs = json.loads(outputs_json) if outputs_json else None
    model = (outputs or {}).get("model")
    artifact = ModelArtifactRef.model_validate(model["model"]) if model and kind in PRODUCERS else None
    events.append(TrackingEvent(**identity, kind="attempt_evidence", idempotency_key=f"evidence:{attempt_id}",
                                depends_on_key=f"attempt:{attempt_id}", payload={
        "status": status, "disposition": disposition, "error": redact(error, 2000) if error else None,
        "files": attempt_files(store.state_dir, facts.job_id, facts.launch_id),
        "outputs_sha256": digest(outputs_json.encode()) if outputs_json else None,
        "artifact": artifact.model_dump(mode="json") if artifact else None,
        "metrics": _metrics(outputs), "timestamp_ms": int(facts.created * 1000),
        "params": {"factory.kind": kind, "factory.node_id": node_id, "factory.attempt_id": attempt_id,
                   "factory.launch_id": facts.launch_id or "", "factory.manifest_sha256": manifest["sha256"],
                   "factory.plan_sha256": plan["sha256"], "factory.status": status}}))
    if artifact is not None and not plan["simulation"]:
        descriptor = read_descriptor(store.artifacts, artifact)
        if descriptor.eligible_for_real_use and descriptor.producer_attempt_id == attempt_id:
            key = f"register:{artifact.artifact_id}:{artifact.inventory_sha256}:{attempt_id}"
            root = descriptor.root
            events.append(TrackingEvent(**identity, kind="model_registration", idempotency_key=key,
                                        depends_on_key=f"evidence:{attempt_id}", payload={
                "name": f"factory-{plan['project_id']}-{node_id}", "artifact": artifact.model_dump(mode="json"),
                "tags": {"factory.artifact_id": artifact.artifact_id,
                         "factory.inventory_sha256": artifact.inventory_sha256,
                         "factory.producer_job_id": facts.job_id, "factory.producer_attempt_id": attempt_id,
                         "factory.manifest_sha256": manifest["sha256"], "factory.plan_sha256": plan["sha256"],
                         "base_model": root.repo_id, "model_revision": root.revision,
                         "quality_qualified": "false", "approval": "pending"}}))
    return events


def summary_event(run_id: str, final_status: str, statuses: Mapping[str, str]) -> TrackingEvent:
    return TrackingEvent(run_id=run_id, kind="graph_summary", idempotency_key=f"graph-summary:{run_id}:{final_status}",
                         depends_on_key=f"graph:{run_id}",
                         payload={"final_status": final_status, "nodes": dict(sorted(statuses.items()))})


# -- the outbox ------------------------------------------------------------------------------------

_CLAIMABLE = '''SELECT e.*, p.remote_identity AS parent_identity, p.idempotency_key AS parent_key
FROM tracking_events e LEFT JOIN tracking_events p ON p.event_id = e.depends_on
WHERE e.status = 'PENDING' AND e.next_attempt_at <= ? AND (e.claim_expires IS NULL OR e.claim_expires <= ?)
  AND (e.depends_on IS NULL OR p.status = 'ACKNOWLEDGED')
  AND (e.kind != 'graph_summary' OR NOT EXISTS (SELECT 1 FROM tracking_events s WHERE s.run_id = e.run_id
       AND s.kind != 'graph_summary' AND s.status = 'PENDING'))
ORDER BY e.created, e.rowid LIMIT ?'''


def _loads(text):
    return json.loads(text) if text else None


def _tracking_status(rows, saved="saved"):
    if not rows:
        return "not_started"
    statuses = {row["status"] for row in rows}
    pending = [row for row in rows if row["status"] == "PENDING"]
    if "FAILED" in statuses:
        return "failed"
    if "UNCERTAIN" in statuses:
        return "uncertain"
    if any(row["tries"] for row in pending):
        return "retrying"
    return "pending" if pending else saved


class TrackingOutbox:
    """The durable queue of tracking events. Claims are fenced by the controller owner lease and a claim token."""

    def __init__(self, store, *, clock: Callable[[], float] = time.time, policy: OutboxRetryPolicy = OutboxRetryPolicy(),
                 claim_ttl_seconds: float = 600):
        from .graph_store import GraphStore
        if not isinstance(store, GraphStore):
            raise TypeError("the tracking outbox requires the GraphStore ledger")
        if not isinstance(policy, OutboxRetryPolicy):
            raise TypeError("an OutboxRetryPolicy is required")
        if (isinstance(claim_ttl_seconds, bool) or not isinstance(claim_ttl_seconds, (int, float))
                or not math.isfinite(claim_ttl_seconds) or claim_ttl_seconds <= 0):
            raise ValueError("claim TTL must be positive and finite")
        self.store, self.clock, self.policy, self.claim_ttl = store, clock, policy, claim_ttl_seconds

    def enqueue(self, event: TrackingEvent, transaction) -> str:
        return enqueue_event(transaction, event, self.clock())

    def claim_pending(self, lease, *, limit: int = 16) -> tuple[ClaimedEvent, ...]:
        """Due PENDING events whose parent is acknowledged (a graph summary waits for its run's other events)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or limit < 1:
            raise ValueError("claim limit must be a positive integer")
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self.store.validate_owner(db, lease)
            now = self.clock()
            claimed = []
            for row in db.execute(_CLAIMABLE, (now, now, limit)).fetchall():
                token = uuid.uuid4().hex
                db.execute('UPDATE tracking_events SET claim_token=?,claim_expires=?,updated=? WHERE event_id=?',
                           (token, now + self.claim_ttl, now, row['event_id']))
                event = TrackingEvent(run_id=row['run_id'], node_id=row['node_id'], job_id=row['job_id'],
                                      attempt_id=row['attempt_id'], kind=row['kind'],
                                      idempotency_key=row['idempotency_key'], depends_on_key=row['parent_key'],
                                      payload=json.loads(row['payload']))
                claimed.append(ClaimedEvent(row['event_id'], token, event, _loads(row['intent']),
                                            _loads(row['parent_identity'])))
            return tuple(claimed)

    @staticmethod
    def _claimed(db, event_id, claim_token):
        row = db.execute('SELECT * FROM tracking_events WHERE event_id=?', (event_id,)).fetchone()
        if row is None:
            raise ValueError("unknown tracking event")
        if row['status'] != 'PENDING' or not claim_token or row['claim_token'] != claim_token:
            raise ValueError(STALE_CLAIM)
        return row

    def record_intent(self, event_id: str, intent: dict, *, claim_token: str) -> None:
        """Durable before a non-idempotent create leaves the PC; it stays until the event is acknowledged."""
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._claimed(db, event_id, claim_token)
            db.execute('UPDATE tracking_events SET intent=?,updated=? WHERE event_id=?',
                       (canonical(intent).decode(), self.clock(), event_id))

    def record_receipt(self, event_id: str, remote_identity: dict, *, claim_token: str) -> None:
        """ACKNOWLEDGED. For attempt evidence, the ledger's tracking gate opens in the same transaction (D5)."""
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._claimed(db, event_id, claim_token)
            db.execute("UPDATE tracking_events SET status='ACKNOWLEDGED',remote_identity=?,claim_token=NULL,"
                       "claim_expires=NULL,last_error=NULL,updated=? WHERE event_id=?",
                       (canonical(remote_identity).decode(), self.clock(), event_id))
            if row['kind'] == 'attempt_evidence' and row['attempt_id']:
                db.execute('UPDATE tracking_outbox SET acknowledged=1 WHERE attempt_id=?', (row['attempt_id'],))

    def retry(self, event_id: str, error: str, *, claim_token: str, uncertain: bool = False,
              permanent: bool = False) -> None:
        """Count a failed send: back off, or end FAILED (UNCERTAIN when the server may have committed) once the
        retries are exhausted. ``permanent``: our own refusal, FAILED at once. Jobs and locks are never touched."""
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = self._claimed(db, event_id, claim_token)
            now, tries = self.clock(), row['tries'] + 1
            status, next_at = 'PENDING', row['next_attempt_at']
            if permanent:
                status = 'FAILED'
            elif tries > self.policy.max_tries:
                status = 'UNCERTAIN' if uncertain else 'FAILED'
            else:
                next_at = now + self.policy.delay(tries)
            db.execute('UPDATE tracking_events SET status=?,tries=?,next_attempt_at=?,claim_token=NULL,'
                       'claim_expires=NULL,last_error=?,updated=? WHERE event_id=?',
                       (status, tries, next_at, redact(error), now, event_id))

    def defer(self, event_id: str, reason: str, *, claim_token: str) -> None:
        """Release a claim untried: no client call was made (a destination mismatch, no tracker). PENDING, no try
        consumed; it is looked at again after the base delay."""
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            self._claimed(db, event_id, claim_token)
            now = self.clock()
            db.execute('UPDATE tracking_events SET claim_token=NULL,claim_expires=NULL,next_attempt_at=?,last_error=?,'
                       'updated=? WHERE event_id=?', (now + self.policy.base_seconds, redact(reason), now, event_id))

    def rearm(self, event_id: str) -> None:
        """Operator: FAILED or UNCERTAIN -> PENDING with tries=0. The intent is kept, so it searches first again."""
        with self.store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            row = db.execute('SELECT status FROM tracking_events WHERE event_id=?', (event_id,)).fetchone()
            if row is None:
                raise ValueError("unknown tracking event")
            if row['status'] not in {'FAILED', 'UNCERTAIN'}:
                raise ValueError("only a failed or uncertain tracking event can be re-armed")
            now = self.clock()
            db.execute("UPDATE tracking_events SET status='PENDING',tries=0,next_attempt_at=?,claim_token=NULL,"
                       "claim_expires=NULL,updated=? WHERE event_id=?", (now, now, event_id))

    def backfill(self, lease) -> int:
        """Events for pre-8.3 attempts whose ``tracking_outbox`` row is unacknowledged and has no evidence event.
        Returns how many events were added (a second call adds none)."""
        store = self.store
        with store.jobs.connection() as db:
            db.execute('BEGIN IMMEDIATE')
            store.validate_owner(db, lease)
            before = db.execute('SELECT COUNT(*) FROM tracking_events').fetchone()[0]
            now = self.clock()
            for row in db.execute(
                    "SELECT t.attempt_id,t.payload AS outputs,j.id AS job_id,j.graph_node_id,j.manifest,a.launch_id,"
                    "a.created,r.id AS run_id,r.plan,r.approval FROM tracking_outbox t JOIN jobs j ON j.id=t.job_id "
                    "JOIN job_attempts a ON a.attempt_id=t.attempt_id JOIN graph_runs r ON r.id=j.graph_run_id "
                    "WHERE t.acknowledged=0 AND NOT EXISTS (SELECT 1 FROM tracking_events e "
                    "WHERE e.idempotency_key='evidence:'||t.attempt_id) ORDER BY a.created,t.attempt_id").fetchall():
                facts = AttemptFacts(run_id=row['run_id'], node_id=row['graph_node_id'], job_id=row['job_id'],
                                     attempt_id=row['attempt_id'], launch_id=row['launch_id'],
                                     manifest=json.loads(row['manifest']), created=row['created'])
                for event in attempt_events(store, facts, row['plan'], row['approval'], status='COMPLETED',
                                            outputs_json=row['outputs']):
                    enqueue_event(db, event, now)
            return db.execute('SELECT COUNT(*) FROM tracking_events').fetchone()[0] - before

    def status(self, run_id: str) -> dict:
        """Counts by event status, and per node a tracking status (``not_started``, ``pending``, ``retrying``,
        ``saved``, ``uncertain``, ``failed``) separate from execution, plus its registration status. No secret,
        claim token or remote path."""
        with self.store.jobs.connection() as db:
            rows = [dict(row) for row in db.execute(
                'SELECT kind,node_id,status,tries,last_error FROM tracking_events WHERE run_id=? ORDER BY rowid',
                (run_id,))]
            node_ids = [row['node_id'] for row in db.execute('SELECT node_id FROM nodes WHERE run_id=? ORDER BY node_id',
                                                             (run_id,))]
        counts = {status: sum(row['status'] == status for row in rows) for status in EVENT_STATUSES}

        def error(items):
            return next((row['last_error'] for row in items if row['status'] != 'ACKNOWLEDGED' and row['last_error']),
                        None)

        nodes = {}
        for node_id in node_ids:
            own = [row for row in rows if row['node_id'] == node_id]
            tracked = [row for row in own if row['kind'] != 'model_registration']
            registrations = [row for row in own if row['kind'] == 'model_registration']
            nodes[node_id] = {"tracking_status": _tracking_status(tracked),
                              "registration_status": _tracking_status(registrations, "registered")
                              if registrations else "none", "last_error": error(own)}
        graph = [row for row in rows if row['node_id'] is None]
        return {"run_id": run_id, "events": counts,
                "graph": {"tracking_status": _tracking_status(graph), "last_error": error(graph)}, "nodes": nodes}


# -- publishing ------------------------------------------------------------------------------------

class _Uncertain(Exception):
    """The server may have committed: the next try searches first, and never creates while a search fails."""


class _Permanent(Exception):
    """Our own refusal (changed evidence, an insecure artifact root): FAILED at once, no retry."""


def _insecure(exc) -> bool:
    from .auth import INSECURE_TRACKING
    return isinstance(exc, ValueError) and str(exc) == INSECURE_TRACKING


def _describe(exc) -> str:
    return redact(f"{type(exc).__name__}: {exc}")


class LocalEvidenceSink:
    """D7: simulation runs never contact MLflow. Their events are acknowledged by immutable local receipts,
    ``state/tracking/<run_id>/<event_id>.json``, which reference the verified evidence by hash."""

    def __init__(self, state_root):
        self.state_root = Path(state_root).resolve()

    def publish(self, event_id: str, event: TrackingEvent, files: Mapping[str, bytes]) -> dict:
        receipt = canonical({"event_id": event_id, "kind": event.kind, "idempotency_key": event.idempotency_key,
                             "run_id": event.run_id, "node_id": event.node_id, "job_id": event.job_id,
                             "attempt_id": event.attempt_id, "payload": event.payload,
                             "files": {name: digest(data) for name, data in sorted(files.items())}})
        relative = f"tracking/{event.run_id}/{event_id}.json"
        immutable_write(self.state_root / relative, receipt)   # a retry writes the same bytes
        return {"sink": "local", "receipt": relative, "receipt_sha256": digest(receipt)}


class TrackingPublisher:
    """Sends claimed events: the local sink for simulations, otherwise the MLflow tracker of the one shared auth
    scope (only when its public settings match the plan's pinned destination)."""

    def __init__(self, outbox: TrackingOutbox, store, tracker, *, local_sink: LocalEvidenceSink):
        if not isinstance(outbox, TrackingOutbox) or outbox.store is not store:
            raise ValueError("the publisher requires the outbox of the same GraphStore")
        if not isinstance(local_sink, LocalEvidenceSink):
            raise TypeError("a LocalEvidenceSink is required")
        self.outbox, self.store, self.tracker, self.sink = outbox, store, tracker, local_sink
        self._handlers = {"graph_run": self._graph_run, "node_run": self._child_run, "attempt_run": self._child_run,
                          "attempt_evidence": self._attempt_evidence, "model_registration": self._registration,
                          "graph_summary": self._graph_summary}

    def process(self, lease, *, limit: int = 16) -> int:
        """Publish up to ``limit`` due events; children whose parents are acknowledged meanwhile are included."""
        processed = 0
        while processed < limit:
            batch = self.outbox.claim_pending(lease, limit=limit - processed)
            if not batch:
                break
            for claimed in batch:
                self._publish(claimed)
                processed += 1
        return processed

    def _publish(self, claimed: ClaimedEvent) -> None:
        outbox, event = self.outbox, claimed.event
        plan = self.store.get_run(event.run_id)['plan']
        try:
            if plan.simulation:
                identity = self.sink.publish(claimed.event_id, event, self._stage(claimed))
            else:
                reason = self._destination_issue(plan)
                if reason is not None:
                    outbox.defer(claimed.event_id, reason, claim_token=claimed.claim_token)
                    return
                identity = self._handlers[event.kind](claimed, self._stage(claimed))
        except _Uncertain as exc:
            outbox.retry(claimed.event_id, TRACKING_UNCERTAIN + ": " + _describe(exc.__cause__),
                         claim_token=claimed.claim_token, uncertain=True)
            return
        except Exception as exc:
            outbox.retry(claimed.event_id, _describe(exc), claim_token=claimed.claim_token,
                         permanent=isinstance(exc, _Permanent) or _insecure(exc))
            return
        outbox.record_receipt(claimed.event_id, identity, claim_token=claimed.claim_token)

    def _destination_issue(self, plan) -> str | None:
        destination = plan.execution_choices.tracking_destination
        if destination is None:
            return NO_DESTINATION
        if self.tracker is None:
            return TRACKER_MISSING
        try:
            actual = tracking_settings_sha256(self.tracker.public_settings())
        except ValueError as exc:
            return redact(str(exc))
        return None if actual == destination.public_settings_sha256 else DESTINATION_DIFFERS

    # -- local evidence, verified against the hashes fixed at enqueue time ----------------------------

    def _read(self, name, entry) -> bytes:
        relative = str(entry["path"])
        parts = relative.split("/")
        if not all(_SAFE_PART.fullmatch(part) and part not in {".", ".."} for part in parts):
            raise _Permanent(f"{EVIDENCE_CHANGED}: {name}")
        path = self.store.state_dir.joinpath(*parts)
        try:
            if path.is_symlink() or not path.resolve().is_relative_to(self.store.state_dir.resolve()):
                raise OSError("evidence outside the state directory")
            data = path.read_bytes()
        except OSError as exc:
            raise _Permanent(f"{EVIDENCE_CHANGED}: {name}") from exc
        if digest(data) != entry["sha256"]:
            raise _Permanent(f"{EVIDENCE_CHANGED}: {name}")
        return data

    def _stage(self, claimed: ClaimedEvent) -> dict[str, bytes]:
        event, payload = claimed.event, claimed.event.payload
        files = {name: self._read(name, entry) for name, entry in sorted((payload.get("files") or {}).items())}
        if event.kind == "graph_run":
            with self.store.jobs.connection() as db:
                row = db.execute('SELECT plan,approval FROM graph_runs WHERE id=?', (event.run_id,)).fetchone()
            for name, text, expected in (("plan.json", row['plan'], payload["plan_json_sha256"]),
                                         ("approval.json", row['approval'], payload["approval_json_sha256"])):
                if digest(text.encode()) != expected:
                    raise _Permanent(f"{EVIDENCE_CHANGED}: {name}")
                files[name] = text.encode()
        if payload.get("outputs_sha256"):
            with self.store.jobs.connection() as db:
                row = db.execute('SELECT outputs FROM node_outputs WHERE attempt_id=?', (event.attempt_id,)).fetchone()
            data = row['outputs'].encode() if row else b""
            if digest(data) != payload["outputs_sha256"]:
                raise _Permanent(f"{EVIDENCE_CHANGED}: node-outputs.json")
            files["node-outputs.json"] = data
            outputs = json.loads(data)
            if outputs.get("evaluation"):
                files["evaluation.json"] = canonical(outputs["evaluation"]["evaluation"])
            selection = outputs.get("selection") or (outputs.get("model") or {}).get("selection")
            if selection:
                files["selection.json"] = canonical(selection)
        if event.kind == "attempt_evidence" and payload.get("artifact"):
            ref = ModelArtifactRef.model_validate(payload["artifact"])
            location = Path(self.store.artifacts.root) / ref.artifact_id / ref.inventory_sha256
            for name, expected in (("descriptor.json", ref.artifact_id), ("inventory.json", ref.inventory_sha256)):
                try:
                    data = (location / name).read_bytes()
                except OSError as exc:
                    raise _Permanent(f"{EVIDENCE_CHANGED}: {name}") from exc
                if digest(data) != expected:
                    raise _Permanent(f"{EVIDENCE_CHANGED}: {name}")
                files[name] = data
        if payload.get("error"):
            files["error.txt"] = str(payload["error"]).encode()
        if event.kind == "graph_summary":
            files["summary.json"] = canonical({"final_status": payload["final_status"], "nodes": payload["nodes"]})
        return files

    # -- MLflow: find-or-create with a durable intent, uploads, registration -------------------------

    def _search(self, find):
        try:
            return find()
        except Exception as exc:
            raise _Uncertain() from exc   # never create while a search is uncertain

    def _create(self, claimed, op, create):
        self.outbox.record_intent(claimed.event_id, {"op": op, "key": claimed.event.idempotency_key,
                                                     "at": self.outbox.clock()}, claim_token=claimed.claim_token)
        try:
            return create()
        except Exception as exc:
            if _insecure(exc):
                raise   # the run was created, terminated FAILED and refused before any upload
            raise _Uncertain() from exc   # the server may have committed: the next try searches first

    def _keyed_run(self, claimed, parent_run_id):
        event, tracker = claimed.event, self.tracker
        key = event.idempotency_key
        if claimed.intent is not None:
            found = self._search(lambda: tracker.find_keyed_runs(key))
            if found:
                tracker.check_run_artifacts(found[0]["run_id"])
                return {"run_id": found[0]["run_id"], "adopted": True,
                        "duplicates": [item["run_id"] for item in found[1:]]}
        created = self._create(claimed, "create_run", lambda: tracker.create_keyed_run(
            key, parent_run_id=parent_run_id, run_name=event.payload["run_name"], tags=event.payload["tags"]))
        return {"run_id": created["run_id"], "adopted": False, "duplicates": []}

    def _upload(self, run_id, files, payload, *, metrics=None, timestamp_ms=0):
        if not files and not payload.get("params") and not metrics:
            return
        with tempfile.TemporaryDirectory(prefix="factory-lineage-") as directory:
            staged = {}
            for name, data in files.items():
                staged[name] = Path(directory) / name
                staged[name].write_bytes(data)
            self.tracker.log_lineage(run_id, staged, params=payload.get("params") or {}, metrics=metrics or {},
                                     timestamp_ms=timestamp_ms)

    def _graph_run(self, claimed, files):
        identity = self._keyed_run(claimed, None)
        self._upload(identity["run_id"], files, claimed.event.payload)
        return identity

    def _child_run(self, claimed, files):
        return self._keyed_run(claimed, claimed.parent_identity["run_id"])

    def _attempt_evidence(self, claimed, files):
        payload, run_id = claimed.event.payload, claimed.parent_identity["run_id"]
        self.tracker.check_run_artifacts(run_id)
        self._upload(run_id, files, payload, metrics=payload.get("metrics"), timestamp_ms=payload["timestamp_ms"])
        self.tracker.terminate(run_id, TERMINAL[payload["status"]])
        return {"run_id": run_id, "files": sorted(files)}

    def _registration(self, claimed, files):
        event, tracker = claimed.event, self.tracker
        payload, run_id, key = event.payload, claimed.parent_identity["run_id"], event.idempotency_key
        name = payload["name"]
        tracker.check_run_artifacts(run_id)
        if claimed.intent is not None:
            found = self._search(lambda: tracker.find_keyed_model_versions(name, run_id, key))
            if found:
                return {"name": name, "version": found[0]["version"], "run_id": run_id, "adopted": True,
                        "duplicates": [item["version"] for item in found[1:]]}
        ref = ModelArtifactRef.model_validate(payload["artifact"])
        tracker.upload_model(run_id, self.store.artifacts.path(ref))   # verifies the whole inventory first
        tracker.ensure_registered_model(name)
        version = self._create(claimed, "create_model_version", lambda: tracker.create_keyed_model_version(
            name, run_id=run_id, key=key, tags=payload["tags"]))
        return {"name": name, "version": version["version"], "run_id": run_id, "adopted": False, "duplicates": []}

    def _graph_summary(self, claimed, files):
        event, graph_run = claimed.event, claimed.parent_identity["run_id"]
        self.tracker.check_run_artifacts(graph_run)
        self._upload(graph_run, files, event.payload)
        with self.store.jobs.connection() as db:
            node_runs = {row['node_id']: json.loads(row['remote_identity'])['run_id'] for row in db.execute(
                "SELECT node_id,remote_identity FROM tracking_events WHERE run_id=? AND kind='node_run' "
                "AND status='ACKNOWLEDGED'", (event.run_id,))}
        for node_id, status in sorted(event.payload["nodes"].items()):
            if node_id in node_runs:
                self.tracker.terminate(node_runs[node_id], TERMINAL.get(status, "KILLED"))
        self.tracker.terminate(graph_run, TERMINAL[event.payload["final_status"]])
        return {"run_id": graph_run, "final_status": event.payload["final_status"]}
