import json

from tuning_factory.config import load_config
from tuning_factory.execution import ExecutionUncertain, LocalExecutor
from tuning_factory.runner import Factory


def test_reconcile_finished_worker_without_retraining(tmp_path):
    class Disconnected(LocalExecutor):
        def run(self, *args, **kwargs):
            raise ExecutionUncertain("detached")

    class Collector:
        def collect_result(self, directory):
            (directory / "output").mkdir(exist_ok=True)
            (directory / "output" / "simulation.json").write_text('{"simulation":true}')
            return {"status": "COMPLETED"}

    factory = Factory(tmp_path)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json")
    job = factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    factory.work(executor=Disconnected(), gpu="retained")
    assert factory.store.gpu_locked("retained")
    restored = factory.reconcile(job["id"], Collector())
    assert restored["status"] == "COMPLETED"
    assert restored["attempts"] == 1
    assert not factory.store.gpu_locked("retained")
    assert json.loads((factory.directory(job["id"]) / "evaluation.json").read_text())["simulation"]


def test_reconcile_does_not_release_still_running_worker(tmp_path):
    class Disconnected(LocalExecutor):
        def run(self, *args, **kwargs):
            raise ExecutionUncertain("detached")

    class StillRunning:
        def collect_result(self, directory):
            return None

    factory = Factory(tmp_path)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json")
    job = factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    factory.work(executor=Disconnected(), gpu="retained")
    assert factory.reconcile(job["id"], StillRunning())["status"] == "FAILED"
    assert factory.store.gpu_locked("retained")
