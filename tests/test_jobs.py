from concurrent.futures import ThreadPoolExecutor

import pytest

from tuning_factory.jobs import JobStore


def test_idempotency_and_transition(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    first = store.submit({"sha256": "abc"}, "request1")
    assert store.submit({"sha256": "abc"}, "request1")["id"] == first["id"]
    with pytest.raises(ValueError):
        store.submit({"sha256": "different"}, "request1")
    with pytest.raises(ValueError):
        store.transition(first["id"], "COMPLETED")
    store.transition(first["id"], "QUEUED")
    assert store.claim("gpu1", max_concurrent=1)["id"] == first["id"]
    store.transition(first["id"], "RUNNING")
    store.cancel(first["id"])
    assert store.get(first["id"])["cancel_requested"]


def test_atomic_claim_and_gpu_lock(tmp_path):
    path = tmp_path / "jobs.db"
    store = JobStore(path)
    for i in range(4):
        job = store.submit({"sha256": str(i)}, str(i), priority=i)
        store.transition(job["id"], "QUEUED")
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda _: JobStore(path).claim("same-gpu", 4), range(4)))
    assert sum(r is not None for r in results) == 1
    assert next(r for r in results if r)["priority"] == 3
    assert store.claim("other-gpu", 1) is None


def test_recovery_does_not_release_unknown_remote_execution(tmp_path):
    store = JobStore(tmp_path / "jobs.db")
    job = store.submit({"sha256": "abc"}, "x")
    store.transition(job["id"], "QUEUED")
    store.claim("pod1", 1)
    store.transition(job["id"], "RUNNING")
    store.recover(stale_after_seconds=0)
    recovered = store.get(job["id"])
    assert recovered["status"] == "FAILED"
    assert "reconcile" in recovered["error"]
    assert store.gpu_locked("pod1")
    store.release_gpu(job["id"])
    assert not store.gpu_locked("pod1")
