import json

from typer.testing import CliRunner

from tuning_factory.cli import app
from tuning_factory.config import load_config
from tuning_factory.execution import CapacityUnavailable, LocalExecutor
from tuning_factory.runner import Factory
from tuning_factory.training import MockTrainingBackend


def setup_factory(tmp_path):
    factory = Factory(tmp_path)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=False)
    return factory


def test_three_independent_ai_runs_and_idempotency(tmp_path):
    factory = setup_factory(tmp_path)
    for name in ("paper_extraction", "labnote_generation", "workflow_classification"):
        config = load_config(f"configs/ai/{name}.yaml")
        first = factory.submit(config)
        assert factory.submit(config)["id"] == first["id"]
    assert len(factory.store.list()) == 3
    for _ in range(3):
        result = factory.work()
        assert result["status"] == "COMPLETED"
        directory = factory.directory(result["id"])
        evaluation = json.loads((directory / "evaluation.json").read_text())
        assert evaluation["quality_status"] == "not_evaluated"
        assert evaluation["simulation"] is True
    assert factory.work() is None


def test_bounded_retry_and_failure_evidence(tmp_path):
    class NoCapacity(LocalExecutor):
        def run(self, *args, **kwargs):
            raise CapacityUnavailable("no GPU")

    factory = setup_factory(tmp_path)
    job = factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    assert factory.work(executor=NoCapacity())["status"] == "QUEUED"
    assert factory.work(executor=NoCapacity())["status"] == "FAILED"
    assert factory.store.get(job["id"])["attempts"] == 2
    assert (factory.directory(job["id"]) / "error.txt").exists()


def test_remote_work_blocked_without_matching_manifest(tmp_path):
    class Remote(LocalExecutor):
        remote = True

    factory = setup_factory(tmp_path)
    factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    job = factory.work(executor=Remote(), approved_transfer=True)
    assert job["status"] == "FAILED"
    assert "simulation" in job["error"]


def test_cli_plan_and_mock_run(tmp_path):
    runner = CliRunner()
    base = ["--state-dir", str(tmp_path)]
    result = runner.invoke(app, [*base, "dataset-register", "dummy", "1", "examples/dummy.json"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, [*base, "validate", "configs/ai/paper_extraction.yaml"])
    assert result.exit_code == 0, result.output
    result = runner.invoke(app, [*base, "plan", "configs/ai/paper_extraction.yaml"])
    assert result.exit_code == 0, result.output
    assert "sha256" in json.loads(result.output)
    result = runner.invoke(app, [*base, "run", "configs/ai/paper_extraction.yaml"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["dry_run"] is True
    result = runner.invoke(app, [*base, "run", "configs/ai/paper_extraction.yaml", "--mock"])
    assert result.exit_code == 0, result.output
    assert json.loads(result.output)["status"] == "COMPLETED"


def test_failed_training_can_resume_with_same_manifest(tmp_path):
    class Broken(MockTrainingBackend):
        def train(self, directory, timeout, canceled=lambda: False):
            raise RuntimeError("intentional test failure")

    factory = setup_factory(tmp_path)
    config = load_config("configs/ai/paper_extraction.yaml")
    job = factory.submit(config)
    assert factory.work(backend=Broken())["status"] == "FAILED"
    sha = factory.store.get(job["id"])["manifest"]["sha256"]
    factory.resume(job["id"])
    assert factory.work()["status"] == "COMPLETED"
    assert factory.store.get(job["id"])["manifest"]["sha256"] == sha


def test_no_execution_for_simulation_with_real_backend(tmp_path):
    from tuning_factory.training import LlamaFactoryBackend
    factory = setup_factory(tmp_path)
    factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    job = factory.work(backend=LlamaFactoryBackend())
    assert job["status"] == "FAILED"
    assert "simulation" in job["error"]


def test_uncertain_remote_failure_keeps_gpu_reserved(tmp_path):
    from tuning_factory.execution import ExecutionUncertain

    class Disconnected(LocalExecutor):
        def run(self, *args, **kwargs):
            raise ExecutionUncertain("connection lost")

    factory = setup_factory(tmp_path)
    factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    job = factory.work(executor=Disconnected(), gpu="locked-pod")
    assert job["status"] == "FAILED"
    assert factory.store.gpu_locked("locked-pod")


def test_training_failure_is_recorded_in_tracking(tmp_path):
    class Tracker:
        calls = []

        def start(self, job_id, manifest):
            return "test-run-id"

        def finish(self, run_id, metrics, directory, status, error):
            self.calls.append((status, error))

    class Broken(MockTrainingBackend):
        def train(self, *args, **kwargs):
            raise RuntimeError("training fixture failure")

    factory = setup_factory(tmp_path)
    factory.tracker = Tracker()
    factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    job = factory.work(backend=Broken())
    assert job["run_id"] == "test-run-id"
    assert factory.tracker.calls[0][0] == "FAILED"
    assert "fixture failure" in factory.tracker.calls[0][1]


def test_parallel_duplicate_factory_submissions(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    factory = setup_factory(tmp_path)
    config = load_config("configs/ai/paper_extraction.yaml")
    with ThreadPoolExecutor(max_workers=4) as pool:
        jobs = list(pool.map(lambda _: factory.submit(config), range(4)))
    assert len({job["id"] for job in jobs}) == 1


def test_real_pipeline_candidate_contract_with_mock_remote_transport(tmp_path):
    from tuning_factory.training import LlamaFactoryBackend

    class TransportFixture(LocalExecutor):
        remote = True

        def run(self, backend, directory, timeout, canceled, checkpoint=None):
            output = directory / "output"
            output.mkdir()
            (output / "adapter_config.json").write_text("{}")
            (output / "adapter_model.safetensors").write_bytes(b"transport fixture only")

    factory = Factory(tmp_path)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True)
    factory.submit(load_config("configs/ai/paper_extraction.yaml"), simulation=False)
    job = factory.work(executor=TransportFixture(), backend=LlamaFactoryBackend(), approved_transfer=True)
    assert job["status"] == "COMPLETED"
    candidates = list((factory.root / "candidates").glob("*.json"))
    assert len(candidates) == 1
    assert json.loads(candidates[0].read_text())["approval"] == "pending"
    factory.verify_artifacts(job["id"])
