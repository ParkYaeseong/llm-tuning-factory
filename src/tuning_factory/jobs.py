"""SQLite single-host queue with transactional concurrency and independent GPU ownership."""
import json
import sqlite3
import time
import uuid
from contextlib import contextmanager
from pathlib import Path

ACTIVE = {"PROVISIONING", "RUNNING", "EVALUATING"}
TRANSITIONS = {
    "PENDING": {"QUEUED", "CANCELED"}, "QUEUED": {"PROVISIONING", "CANCELED"},
    "PROVISIONING": {"RUNNING", "FAILED", "CANCELED", "RETRYING"},
    "RUNNING": {"EVALUATING", "FAILED", "CANCELED", "RETRYING"},
    "EVALUATING": {"COMPLETED", "FAILED", "CANCELED"},
    "RETRYING": {"QUEUED", "CANCELED"}, "FAILED": {"QUEUED"}, "CANCELED": {"QUEUED"},
    "COMPLETED": set(),
}


class JobStore:
    def __init__(self, path):
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.connection() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS jobs (
                  id TEXT PRIMARY KEY, request_key TEXT UNIQUE NOT NULL, manifest TEXT NOT NULL,
                  status TEXT NOT NULL, priority INTEGER NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
                  cancel_requested INTEGER NOT NULL DEFAULT 0, error TEXT, run_id TEXT,
                  created REAL NOT NULL, updated REAL NOT NULL);
                CREATE TABLE IF NOT EXISTS gpu_locks (gpu TEXT PRIMARY KEY, job_id TEXT UNIQUE NOT NULL);
                CREATE TABLE IF NOT EXISTS events (
                  job_id TEXT NOT NULL, status TEXT NOT NULL, timestamp REAL NOT NULL, detail TEXT);
            """)

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
        data["manifest"] = json.loads(data["manifest"])
        return data

    def submit(self, manifest, request_key, priority=0):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT * FROM jobs WHERE request_key=?", (request_key,)).fetchone()
            if row:
                if json.loads(row["manifest"])["sha256"] != manifest["sha256"]:
                    raise ValueError("idempotency key reused with different manifest")
                return self.decode(row)
            job_id, now = uuid.uuid4().hex, time.time()
            db.execute("INSERT INTO jobs (id,request_key,manifest,status,priority,created,updated) "
                       "VALUES (?,?,?,'PENDING',?,?,?)", (job_id, request_key, json.dumps(manifest), priority, now, now))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (job_id, "PENDING", now, None))
        return self.get(job_id)

    def get(self, job_id):
        with self.connection() as db:
            result = self.decode(db.execute("SELECT * FROM jobs WHERE id=?", (job_id,)).fetchone())
        if result is None:
            raise ValueError("unknown job")
        return result

    def list(self):
        with self.connection() as db:
            return [self.decode(r) for r in db.execute("SELECT * FROM jobs ORDER BY created")]

    def transition(self, job_id, status, error=None):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or status not in TRANSITIONS.get(row["status"], set()):
                raise ValueError(f"invalid state transition to {status}")
            now = time.time()
            db.execute("UPDATE jobs SET status=?,error=?,updated=? WHERE id=?", (status, error, now, job_id))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (job_id, status, now, error))

    def claim(self, gpu, max_concurrent=1, max_gpus=None, simulation=None):
        if max_concurrent < 1:
            raise ValueError("max_concurrent must be positive")
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            active = db.execute("SELECT COUNT(*) FROM jobs WHERE status IN ('PROVISIONING','RUNNING','EVALUATING')").fetchone()[0]
            if active >= max_concurrent or db.execute("SELECT 1 FROM gpu_locks WHERE gpu=?", (gpu,)).fetchone():
                return None
            candidates = db.execute("SELECT * FROM jobs WHERE status='QUEUED' ORDER BY priority DESC,created").fetchall()
            row = next((r for r in candidates if simulation is None
                        or json.loads(r["manifest"]).get("simulation") == simulation), None)
            if row is None:
                return None
            if max_gpus is not None:
                locks = db.execute("SELECT jobs.manifest FROM jobs JOIN gpu_locks ON jobs.id=gpu_locks.job_id").fetchall()
                def gpu_count(raw):
                    manifest = json.loads(raw)
                    return 0 if manifest.get("simulation") else manifest.get("config", {}).get("resource_profile", {}).get("gpu_count", 1)
                if sum(gpu_count(r[0]) for r in locks) + gpu_count(row["manifest"]) > max_gpus:
                    return None
            now = time.time()
            db.execute("INSERT INTO gpu_locks VALUES (?,?)", (gpu, row["id"]))
            db.execute("UPDATE jobs SET status='PROVISIONING',attempts=attempts+1,updated=? WHERE id=?", (now, row["id"]))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (row["id"], "PROVISIONING", now, gpu))
        return self.get(row["id"])

    def heartbeat(self, job_id):
        with self.connection() as db:
            db.execute("UPDATE jobs SET updated=? WHERE id=?", (time.time(), job_id))

    def set_run_id(self, job_id, run_id):
        with self.connection() as db:
            db.execute("UPDATE jobs SET run_id=? WHERE id=?", (run_id, job_id))

    def cancel(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None:
                raise ValueError("unknown job")
            if row["status"] in ACTIVE:
                db.execute("UPDATE jobs SET cancel_requested=1 WHERE id=?", (job_id,))
            elif row["status"] in {"PENDING", "QUEUED", "RETRYING"}:
                db.execute("UPDATE jobs SET status='CANCELED',updated=? WHERE id=?", (time.time(), job_id))
                db.execute("INSERT INTO events VALUES (?,?,?,?)", (job_id, "CANCELED", time.time(), None))

    def release_gpu(self, job_id):
        with self.connection() as db:
            db.execute("DELETE FROM gpu_locks WHERE job_id=?", (job_id,))

    def gpu_locked(self, gpu):
        with self.connection() as db:
            return bool(db.execute("SELECT 1 FROM gpu_locks WHERE gpu=?", (gpu,)).fetchone())

    def recover(self, stale_after_seconds=120):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            rows = db.execute("SELECT id FROM jobs WHERE status IN ('PROVISIONING','RUNNING','EVALUATING') AND updated<?",
                              (time.time() - stale_after_seconds,)).fetchall()
            for row in rows:
                error = "worker heartbeat expired; reconcile execution before releasing GPU lock"
                db.execute("UPDATE jobs SET status='FAILED',error=?,updated=? WHERE id=?", (error, time.time(), row["id"]))
                db.execute("INSERT INTO events VALUES (?,?,?,?)", (row["id"], "FAILED", time.time(), error))
        return len(rows)

    def reset_for_resume(self, job_id):
        with self.connection() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute("SELECT status FROM jobs WHERE id=?", (job_id,)).fetchone()
            if row is None or row["status"] not in {"FAILED", "CANCELED"}:
                raise ValueError("only failed/canceled jobs can resume")
            if db.execute("SELECT 1 FROM gpu_locks WHERE job_id=?", (job_id,)).fetchone():
                raise ValueError("reconcile the locked execution before resuming")
            db.execute("UPDATE jobs SET status='QUEUED',cancel_requested=0,error=NULL,updated=? WHERE id=?",
                       (time.time(), job_id))
            db.execute("INSERT INTO events VALUES (?,?,?,?)", (job_id, "QUEUED", time.time(), "manual resume"))
