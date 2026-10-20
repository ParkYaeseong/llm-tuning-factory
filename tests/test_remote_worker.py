import json
import re
import shlex
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest
import yaml

from tuning_factory import worker
from tuning_factory.config import load_config
from tuning_factory.execution import ExecutionUncertain, SSHExecutor
from tuning_factory.manifest import resolve
from tuning_factory.registry import Registry, canonical, digest
from tuning_factory.training import LlamaFactoryBackend


def test_worker_checks_integrity_and_publishes_real_completion_contract(tmp_path, monkeypatch):
    registry = Registry(tmp_path / "registry")
    registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True)
    config = load_config("configs/ai/paper_extraction.yaml")
    manifest = resolve(config, registry, simulation=False)
    directory = tmp_path / "job"
    backend = LlamaFactoryBackend()
    backend.prepare(config, directory, Path("examples/dummy.json").read_bytes())
    (directory / "manifest.json").write_bytes(canonical(manifest))

    class FixtureBackend(LlamaFactoryBackend):
        def train(self, directory, timeout, canceled=lambda: False):
            (directory / "output").mkdir(exist_ok=True)
            (directory / "output" / "adapter_config.json").write_text("{}")
            (directory / "output" / "adapter_model.safetensors").write_bytes(b"unit-test fixture; not weights")

    monkeypatch.setattr(worker, "LlamaFactoryBackend", FixtureBackend)
    monkeypatch.setattr(worker, "runtime_preflight", lambda _: {"simulation_fixture": True})
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "0.9.5")
    monkeypatch.setenv("FACTORY_GPU_LOCK_FILE", str(Path("containers/gpu-requirements.lock").resolve()))
    monkeypatch.setattr("sys.argv", ["worker", str(directory), "5"])
    worker.main()
    assert json.loads((directory / "result.json").read_text())["status"] == "COMPLETED"
    assert (directory / "artifacts.json").exists()
    (directory / "train.yaml").write_text("model_name_or_path: altered\n")
    worker.main()
    result = json.loads((directory / "result.json").read_text())
    assert result["status"] == "FAILED"
    assert "YAML hash" in result["error"]


class FakeSFTP:
    def __init__(self, root):
        self.root = root

    def path(self, path):
        return self.root.joinpath(*PurePosixPath(path).parts[1:])

    def __enter__(self):
        return self

    def __exit__(self, *args):
        pass

    def mkdir(self, path):
        self.path(path).mkdir()

    def lstat(self, path):
        return self.path(path).lstat()

    def put(self, source, target):
        self.path(target).write_bytes(Path(source).read_bytes())

    def get(self, source, target):
        Path(target).write_bytes(self.path(source).read_bytes())

    def open(self, path, mode="r"):
        return self.path(path).open(mode)

    def listdir_attr(self, path):
        return [SimpleNamespace(filename=p.name, st_mode=p.lstat().st_mode) for p in self.path(path).iterdir()]


class FakeSSH:
    def __init__(self, root):
        self.sftp = FakeSFTP(root)
        self.commands = []

    def open_sftp(self):
        return self.sftp

    def exec_command(self, command, timeout):
        self.commands.append(command)
        args = shlex.split(command)
        directory = self.sftp.path(args[4])
        (directory / "output").mkdir(exist_ok=True)
        (directory / "output" / "evidence.txt").write_text("mock transport fixture")
        hashes = LlamaFactoryBackend().collect_artifacts(directory)
        (directory / "artifacts.json").write_bytes(canonical(hashes))
        (directory / "result.json").write_text('{"status":"COMPLETED"}')
        stream = SimpleNamespace(channel=SimpleNamespace(recv_exit_status=lambda: 0))
        return None, stream, None

    def close(self):
        pass


def test_worker_deadline_message_names_no_provider(tmp_path, monkeypatch):
    import itertools
    import time

    from tuning_factory import execution

    class SilentWorker(FakeSSH):
        def exec_command(self, command, timeout):
            return None, SimpleNamespace(channel=SimpleNamespace(recv_exit_status=lambda: 0)), None

    clock = itertools.count(0, 1000)
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None,
                                                           time=time.time))
    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    (directory / "train.yaml").write_text("test: true")
    client = SilentWorker(tmp_path / "remote")
    client.sftp.root.mkdir()
    with pytest.raises(ExecutionUncertain) as error:
        SSHExecutor("test-host", 22, "root", "unused", client=client).run(
            LlamaFactoryBackend(), directory, 5, lambda: False)
    assert str(error.value.__cause__) == ("remote worker deadline exceeded; reconcile the remote GPU host "
                                          "and retain GPU lock")


def test_ssh_transfers_and_verifies_artifacts_with_unique_attempt_names(tmp_path):
    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    (directory / "train.yaml").write_text("test: true")
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    assert (directory / "output" / "evidence.txt").read_text() == "mock transport fixture"
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    assert client.commands[0] != client.commands[1]


def test_controller_interrupt_preserves_uncertain_execution(tmp_path):
    class InterruptedSSH(FakeSSH):
        def exec_command(self, command, timeout):
            raise KeyboardInterrupt

    directory = tmp_path / ("a" * 32)
    directory.mkdir()
    client = InterruptedSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)
    with pytest.raises(ExecutionUncertain):
        executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)


def test_sftp_reconciliation_checks_original_manifest(tmp_path):
    directory = tmp_path / ("b" * 32)
    directory.mkdir()
    (directory / "manifest.json").write_text('{"identity":"original"}')
    (directory / "train.yaml").write_text("test: true")
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    assert executor.collect_result(directory)["status"] == "COMPLETED"
    remote = json.loads((directory / "remote-attempt.json").read_text())["remote_path"]
    client.sftp.path(remote + "/manifest.json").write_text('{"identity":"wrong"}')
    with pytest.raises(ValueError, match="manifest"):
        executor.collect_result(directory)


def test_completed_worker_evidence_survives_interrupted_artifact_download(tmp_path, monkeypatch):
    directory = tmp_path / ("c" * 32)
    directory.mkdir()
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)

    def interrupted(*args, **kwargs):
        raise OSError("connection lost during large checkpoint download")

    monkeypatch.setattr(executor, "download", interrupted)
    with pytest.raises(OSError):
        executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    evidence = json.loads((directory / "remote-result.json").read_text())
    assert evidence["status"] == "COMPLETED"
    assert (directory / "remote-artifacts.json").is_file()


def test_reconciliation_reuses_verified_files_and_repairs_partial_downloads(tmp_path, monkeypatch):
    directory = tmp_path / ("d" * 32)
    directory.mkdir()
    (directory / "manifest.json").write_text('{"identity":"original"}')
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    transfers = []
    original_get = client.sftp.get

    def observe(source, target):
        transfers.append(source)
        original_get(source, target)

    monkeypatch.setattr(client.sftp, "get", observe)
    assert executor.collect_result(directory)["status"] == "COMPLETED"
    assert not any(path.endswith("/output/evidence.txt") for path in transfers)
    (directory / "output" / "evidence.txt").write_text("partial or corrupted bytes")
    transfers.clear()
    assert executor.collect_result(directory)["status"] == "COMPLETED"
    assert any(path.endswith("/output/evidence.txt") for path in transfers)
    assert (directory / "output" / "evidence.txt").read_text() == "mock transport fixture"


def test_new_attempt_does_not_upload_previous_completion_or_controller_receipts(tmp_path, monkeypatch):
    directory = tmp_path / ("e" * 32)
    directory.mkdir()
    names = ('result.json', 'result.tmp', 'cancel', 'remote-result.json', 'remote-artifacts.json')
    for name in names:
        (directory / name).write_text('{"status":"COMPLETED"}')
    client = FakeSSH(tmp_path / 'remote')
    client.sftp.root.mkdir()
    original_exec = client.exec_command

    def inspect(command, timeout):
        remote = client.sftp.path(shlex.split(command)[4])
        assert all(not (remote / name).exists() for name in names)
        return original_exec(command, timeout)

    monkeypatch.setattr(client, 'exec_command', inspect)
    executor = SSHExecutor('test-host', 22, 'root', 'unused', client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)


def test_legacy_mutable_cancel_signal_does_not_invalidate_model_artifacts(tmp_path):
    class ChangedCancelSSH(FakeSSH):
        def exec_command(self, command, timeout):
            directory = self.sftp.path(shlex.split(command)[4])
            (directory / 'cancel').touch()
            result = super().exec_command(command, timeout)
            hashes = json.loads((directory / 'artifacts.json').read_text())
            hashes['cancel'] = digest(b'')
            (directory / 'artifacts.json').write_bytes(canonical(hashes))
            (directory / 'cancel').write_text('late control-plane signal')
            return result

    directory = tmp_path / ('f' * 32)
    directory.mkdir()
    client = ChangedCancelSSH(tmp_path / 'remote')
    client.sftp.root.mkdir()
    executor = SSHExecutor('test-host', 22, 'root', 'unused', client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False)
    assert (directory / 'output' / 'evidence.txt').read_text() == 'mock transport fixture'
    assert not (directory / 'cancel').exists()


def test_control_cancel_marker_is_excluded_from_immutable_artifact_inventory(tmp_path):
    (tmp_path / 'cancel').touch()
    (tmp_path / 'checkpoint-state').write_text('immutable trainer fixture')
    hashes = LlamaFactoryBackend().collect_artifacts(tmp_path)
    assert 'cancel' not in hashes
    assert 'checkpoint-state' in hashes


def test_cancel_signal_is_idempotent_with_sftp_v3_existing_file_error(tmp_path):
    class V3SFTP(FakeSFTP):
        def open(self, path, mode='r'):
            if path.endswith('/cancel') and mode == 'x' and self.path(path).exists():
                raise OSError('Failure')  # SFTP v3 does not preserve EEXIST.
            return super().open(path, mode)

    class SignaledSSH(FakeSSH):
        def exec_command(self, command, timeout):
            directory = self.sftp.path(shlex.split(command)[4])
            (directory / 'cancel').write_text('observer signal must remain unchanged')
            return super().exec_command(command, timeout)

    directory = tmp_path / ('1' * 32)
    directory.mkdir()
    client = SignaledSSH(tmp_path / 'remote')
    client.sftp = V3SFTP(client.sftp.root)
    client.sftp.root.mkdir()
    executor = SSHExecutor('test-host', 22, 'root', 'unused', client=client)
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: True)
    remote = json.loads((directory / 'remote-attempt.json').read_text())['remote_path']
    assert client.sftp.path(remote + '/cancel').read_text() == 'observer signal must remain unchanged'


def test_ssh_uses_persisted_launch_identity(tmp_path):
    directory = tmp_path / ('a' * 32)
    directory.mkdir()
    (directory / 'train.yaml').write_text('test: true')
    client = FakeSSH(tmp_path / 'remote')
    client.sftp.root.mkdir()
    executor = SSHExecutor('test-host', 22, 'root', 'unused', client=client)
    launch_id = 'b' * 32
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False, launch_id=launch_id)
    receipt = json.loads((directory / 'remote-attempt.json').read_text())
    assert receipt['remote_path'].endswith('/' + launch_id)
    assert launch_id in client.commands[0]
    with pytest.raises(ValueError, match='launch'):
        executor.run(LlamaFactoryBackend(), directory, 5, lambda: False, launch_id='../unsafe')


# -- Task 7.3: worker v2 protocol (hash-verified command.json, one start per launch directory) ---

JOB_ID, LAUNCH_ID, ATTEMPT_ID = "a" * 32, "b" * 32, "c" * 32


def write_command(directory, request):
    body = canonical(request.model_dump(mode="json"))
    (directory / "command.json").write_bytes(body)
    return digest(body)


def v2_launch(tmp_path, monkeypatch, *, simulation=False, method="lora"):
    """A real v2 launch directory: registered data, a cached parent, manifest, model-inputs and command."""
    from test_model_inputs import EXECUTOR, FACTS, frozen, full_model, full_weights, graph_config, policy

    from tuning_factory.manifest import resolve_operation
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.operation_runner import WorkerRequest, stage_model_inputs
    from tuning_factory.operations import OperationLineage, OperationResources, RuntimeProfile, TrainOperation
    from tuning_factory.projects import DatasetRef
    monkeypatch.setenv("PATH", worker.os.environ.get("PATH", ""))   # main() prepends the venv; restore it after
    registry = Registry(tmp_path / "registry")
    record = registry.register_dataset("data", "1", "examples/dummy.json", export_allowed=True)
    store = ModelArtifactStore(tmp_path / "model-cache")
    sft_ref, _ = frozen(store, full_model(tmp_path, "sft"))
    lock = tmp_path / "gpu-requirements.lock"
    lock.write_bytes(b"pinned primary runtime lock fixture\n")
    model_input = policy(sft_ref) if method == "lora" else full_weights(sft_ref)
    operation = TrainOperation(
        config=graph_config(method=method), model_input=model_input,
        dataset=DatasetRef(dataset_id="data", version="1", sha256=record["sha256"]),
        resources=OperationResources(executor=EXECUTOR, facts=FACTS, timeout_seconds=900, runtime=RuntimeProfile(
            profile="primary", engine_version="0.9.5", dependency_sha256=digest(lock.read_bytes()))),
        lineage=OperationLineage(project_id="project", project_revision="f" * 64, node_id="node",
                                 plan_sha256="d" * 64, simulation=simulation))
    directory = tmp_path / JOB_ID / LAUNCH_ID
    LlamaFactoryBackend().prepare(operation.config, directory, Path("examples/dummy.json").read_bytes(),
                                  operation.model_input)
    stage_model_inputs(operation, store, directory)
    manifest = resolve_operation(operation, registry)
    (directory / "manifest.json").write_bytes(canonical(manifest))
    request = WorkerRequest(attempt_id=ATTEMPT_ID, launch_id=LAUNCH_ID, manifest_sha256=manifest["sha256"],
                            operation_sha256=digest(canonical(manifest["operation"])), timeout_seconds=900)
    monkeypatch.setenv("FACTORY_GPU_LOCK_FILE", str(lock))
    monkeypatch.setenv("FACTORY_MODEL_CACHE", str(store.root))
    monkeypatch.setenv("HF_HOME", str(tmp_path / "hf"))
    monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "0.9.5")
    calls = []

    class FixtureBackend(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            calls.append(filename)
            output = Path(directory) / "output"
            output.mkdir()
            (output / "adapter_config.json").write_text('{"peft_type":"LORA"}')
            (output / "adapter_model.safetensors").write_bytes(b"unit-test fixture; not weights")

    monkeypatch.setattr(worker, "LlamaFactoryBackend", FixtureBackend)
    monkeypatch.setattr(worker, "runtime_preflight_bound",
                        lambda config, paths: {"bound_model": paths.path("policy_base").as_posix()})
    return SimpleNamespace(directory=directory, request=request, sha=write_command(directory, request), calls=calls,
                           manifest=manifest, store=store, sft_ref=sft_ref, operation=operation, lock=lock)


def v2_result(directory):
    return json.loads((directory / "result.json").read_bytes())


def replace_operation(launch, operation_body):
    """Re-sign manifest.json around another operation body and point command.json at it."""
    manifest = {key: value for key, value in launch.manifest.items() if key != "sha256"}
    manifest["operation"] = operation_body
    manifest["sha256"] = digest(canonical(manifest))
    (launch.directory / "manifest.json").write_bytes(canonical(manifest))
    request = launch.request.model_copy(update={"manifest_sha256": manifest["sha256"],
                                                "operation_sha256": digest(canonical(operation_body))})
    return write_command(launch.directory, request)


def test_v2_worker_completes_train_on_bound_yaml(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    monkeypatch.setattr("sys.argv", ["worker", str(launch.directory), "--request", launch.sha])
    assert worker.main() == 0
    assert launch.calls == ["train.bound.yaml"]
    result = v2_result(launch.directory)
    assert {key: result[key] for key in ("schema_version", "status", "launch_id", "attempt_id", "manifest_sha256",
                                         "operation_sha256", "kind", "engine_version", "error")} == {
        "schema_version": 2, "status": "COMPLETED", "launch_id": LAUNCH_ID, "attempt_id": ATTEMPT_ID,
        "manifest_sha256": launch.manifest["sha256"], "operation_sha256": launch.request.operation_sha256,
        "kind": "train", "engine_version": "0.9.5", "error": None}
    for name in ("train.bound.yaml", "model-binding.json", "binding-evidence.json", "model-inputs.json"):
        assert result["evidence"][name] == digest((launch.directory / name).read_bytes()), name
    evidence = json.loads((launch.directory / "binding-evidence.json").read_bytes())
    assert evidence["train_bound_yaml_sha256"] == result["evidence"]["train.bound.yaml"]
    bound = (launch.directory / "train.bound.yaml").read_text()
    assert launch.store.root.as_posix() in bound and launch.operation.config.base_model not in bound
    start = json.loads((launch.directory / "start.json").read_bytes())
    assert (start["launch_id"], start["attempt_id"], start["command_sha256"]) == (LAUNCH_ID, ATTEMPT_ID, launch.sha)
    assert start["manifest_sha256"] == launch.manifest["sha256"]
    assert json.loads((launch.directory / "artifacts.json").read_bytes())["start.json"]
    assert json.loads((launch.directory / "device-preflight.json").read_bytes())["bound_model"].startswith(
        launch.store.root.as_posix())


def test_v2_worker_rejects_command_hash_mismatch(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    assert worker.main_v2(launch.directory, "0" * 64) == 1
    result = v2_result(launch.directory)
    assert (result["status"], result["error"], result["launch_id"]) == (
        "FAILED", "worker request hash mismatch", LAUNCH_ID)
    assert result["attempt_id"] is None and result["manifest_sha256"] is None
    assert launch.calls == []
    start = json.loads((launch.directory / "start.json").read_bytes())
    assert start["launch_id"] == LAUNCH_ID and start["attempt_id"] is None


@pytest.mark.parametrize("field,message", [
    ("launch_id", "worker request launch identity differs from launch directory"),
    ("manifest_sha256", "worker request manifest differs from manifest.json"),
    ("operation_sha256", "worker request operation hash differs from manifest operation"),
])
def test_v2_worker_rejects_attempt_launch_manifest_operation_mismatch(tmp_path, monkeypatch, field, message):
    launch = v2_launch(tmp_path, monkeypatch)
    value = "d" * 32 if field == "launch_id" else "0" * 64
    sha = write_command(launch.directory, launch.request.model_copy(update={field: value}))
    assert worker.main_v2(launch.directory, sha) == 1
    result = v2_result(launch.directory)
    assert (result["status"], result["error"], result["launch_id"]) == ("FAILED", message, LAUNCH_ID)
    # Identities are reported only as far as they were verified, in order: attempt, manifest, operation.
    assert result["attempt_id"] == (None if field == "launch_id" else ATTEMPT_ID)
    assert result["manifest_sha256"] == (launch.manifest["sha256"] if field == "operation_sha256" else None)
    assert result["operation_sha256"] is None and result["kind"] is None
    assert launch.calls == []


def test_v2_worker_writes_nothing_outside_a_job_and_launch_identity(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    for directory in (tmp_path / JOB_ID / "not-a-launch", tmp_path / "not-a-job" / LAUNCH_ID):
        directory.mkdir(parents=True)
        (directory / "command.json").write_bytes((launch.directory / "command.json").read_bytes())
        assert worker.main_v2(directory, launch.sha) == 2
        assert sorted(path.name for path in directory.iterdir()) == ["command.json"]
    assert launch.calls == []


@pytest.mark.parametrize("case,message", [
    ("simulation", "simulation manifest cannot authorize GPU execution"),
    ("select", "local selection never dispatches to a GPU worker"),
    ("unknown", "unsupported operation kind"),
])
def test_v2_worker_refuses_simulation_select_and_unknown_kind(tmp_path, monkeypatch, case, message):
    launch = v2_launch(tmp_path, monkeypatch, simulation=case == "simulation")
    sha = launch.sha
    if case == "select":
        sha = replace_operation(launch, {"kind": "select", "lineage": launch.manifest["operation"]["lineage"]})
    elif case == "unknown":
        sha = replace_operation(launch, {"kind": "shell", "argv": ["sh", "-c", "true"]})
    assert worker.main_v2(launch.directory, sha) == 1
    result = v2_result(launch.directory)
    assert result["status"] == "FAILED" and message in result["error"]
    assert result["kind"] is None and launch.calls == []


def test_v2_second_start_is_refused_without_touching_result(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    assert worker.main_v2(launch.directory, launch.sha) == 0
    files = {path.relative_to(launch.directory).as_posix(): path.read_bytes()
             for path in launch.directory.rglob("*") if path.is_file()}
    # A lost start response must never become a second training run in the same launch directory.
    assert worker.main_v2(launch.directory, launch.sha) == 3
    assert launch.calls == ["train.bound.yaml"]
    assert {path.relative_to(launch.directory).as_posix(): path.read_bytes()
            for path in launch.directory.rglob("*") if path.is_file()} == files
    # A foreign start marker without any result is equally final: nothing at all is written.
    other = v2_launch(tmp_path / "other", monkeypatch)
    (other.directory / "start.json").write_bytes(b'{"pid":1}')
    (other.directory / "result.json").write_bytes(b'{"status":"COMPLETED"}')
    before = sorted(path.name for path in other.directory.iterdir())
    assert worker.main_v2(other.directory, other.sha) == 3
    assert sorted(path.name for path in other.directory.iterdir()) == before
    assert (other.directory / "result.json").read_bytes() == b'{"status":"COMPLETED"}'
    assert other.calls == []


@pytest.mark.parametrize("case,message", [
    ("lock", "worker dependency lock differs from operation runtime"),
    ("engine", "worker engine version differs from manifest"),
])
def test_v2_worker_checks_operation_runtime_lock_and_engine(tmp_path, monkeypatch, case, message):
    launch = v2_launch(tmp_path, monkeypatch)
    if case == "lock":
        launch.lock.write_bytes(b"a different dependency set\n")
    else:
        monkeypatch.setattr(worker.importlib.metadata, "version", lambda _: "0.9.4")
    assert worker.main_v2(launch.directory, launch.sha) == 1
    result = v2_result(launch.directory)
    assert (result["status"], result["error"]) == ("FAILED", message)
    assert result["operation_sha256"] == launch.request.operation_sha256 and launch.calls == []


@pytest.mark.parametrize("case,message", [
    ("dataset", "transferred dataset hash mismatch"),
    ("dataset_info", "dataset_info.json differs from the transferred dataset"),
    ("template", "training YAML differs from resolved configuration"),
    ("weights", "attempt directory must not contain model weights"),
    ("cache", "hash|inventory"),
    ("datasets", "manifest datasets differ from the operation"),
    ("model_cache", "FACTORY_MODEL_CACHE"),
])
def test_v2_worker_verifies_data_against_manifest(tmp_path, monkeypatch, case, message):
    launch = v2_launch(tmp_path, monkeypatch)
    sha, data = launch.sha, launch.directory / "data"
    if case == "dataset":
        (data / "dataset.json").write_bytes((data / "dataset.json").read_bytes().replace(b"DEMO-001", b"DEMO-002"))
    elif case == "dataset_info":
        info = json.loads((data / "dataset_info.json").read_text())
        info["factory_dataset"]["file_name"] = "other.json"
        (data / "dataset_info.json").write_text(json.dumps(info))
    elif case == "template":
        plan = yaml.safe_load((launch.directory / "train.yaml").read_text())
        (launch.directory / "train.yaml").write_text(yaml.safe_dump({**plan, "learning_rate": 0.5}))
    elif case == "weights":
        (launch.directory / "output.safetensors").write_bytes(b"weights must come from the model cache")
    elif case == "cache":
        files = launch.store.root / launch.sft_ref.artifact_id / launch.sft_ref.inventory_sha256 / "files"
        (files / "model.safetensors").write_bytes(b"tampered cache entry")
    elif case == "datasets":
        manifest = {key: value for key, value in launch.manifest.items() if key != "sha256"}
        manifest["datasets"] = []
        manifest["sha256"] = digest(canonical(manifest))
        (launch.directory / "manifest.json").write_bytes(canonical(manifest))
        sha = write_command(launch.directory, launch.request.model_copy(update={"manifest_sha256": manifest["sha256"]}))
    else:
        monkeypatch.delenv("FACTORY_MODEL_CACHE")
    assert worker.main_v2(launch.directory, sha) == 1
    result = v2_result(launch.directory)
    assert result["status"] == "FAILED" and re.search(message, result["error"]), result["error"]
    assert launch.calls == []


def test_v2_worker_completes_evaluate_with_predictions_only(tmp_path, monkeypatch):
    from test_model_inputs import EXECUTOR, FACTS, ROOT, benchmark, frozen, peft_adapter

    from tuning_factory.manifest import resolve_operation
    from tuning_factory.operation_runner import WorkerRequest, stage_model_inputs, write_benchmark_data
    from tuning_factory.operations import (
        EvaluateOperation,
        OperationLineage,
        OperationResources,
        RuntimeProfile,
    )
    from tuning_factory.projects import DatasetRef
    launch = v2_launch(tmp_path, monkeypatch)
    record = Registry(tmp_path / "registry").register_dataset("bench", "1", "examples/dummy.json", export_allowed=True)
    adapter_ref, _ = frozen(launch.store, peft_adapter(tmp_path, "evaluated", base=launch.sft_ref,
                                                       parents=(launch.sft_ref,)))
    spec = benchmark(DatasetRef(dataset_id="bench", version="1", sha256=record["sha256"]),
                     ordered_sample_ids=("row:1", "row:0"), tokenizer_fingerprint=ROOT.tokenizer_fingerprint)
    operation = EvaluateOperation(model=adapter_ref, benchmark=spec, lineage=OperationLineage(
        project_id="project", project_revision="f" * 64, node_id="evaluate", plan_sha256="d" * 64, simulation=False),
        resources=OperationResources(executor=EXECUTOR, facts=FACTS, timeout_seconds=900, runtime=RuntimeProfile(
            profile="primary", engine_version="0.9.5", dependency_sha256=digest(launch.lock.read_bytes()))))
    directory = tmp_path / JOB_ID / ("e" * 32)
    stage_model_inputs(operation, launch.store, directory)
    write_benchmark_data(directory, Path("examples/dummy.json").read_bytes())
    manifest = resolve_operation(operation, Registry(tmp_path / "registry"))
    (directory / "manifest.json").write_bytes(canonical(manifest))
    request = WorkerRequest(attempt_id=ATTEMPT_ID, launch_id="e" * 32, manifest_sha256=manifest["sha256"],
                            operation_sha256=digest(canonical(manifest["operation"])), timeout_seconds=900)
    predictions = []

    class Predictor(LlamaFactoryBackend):
        def predict_bound(self, directory, plan, sample_ids, timeout, canceled=lambda: False):
            predictions.append((plan["adapter_name_or_path"], tuple(sample_ids)))
            (Path(directory) / "predict.yaml").write_text(yaml.safe_dump(plan))
            (Path(directory) / "output").mkdir()
            (Path(directory) / "output" / "predictions.json").write_bytes(b'{"predictions":["x","y"]}')

    monkeypatch.setattr(worker, "LlamaFactoryBackend", Predictor)
    monkeypatch.setattr(worker, "evaluation_preflight_bound", lambda benchmark, paths, gpu_count: {"gpus": gpu_count})
    assert worker.main_v2(directory, write_command(directory, request)) == 0
    result = v2_result(directory)
    assert (result["status"], result["kind"], result["attempt_id"]) == ("COMPLETED", "evaluate", ATTEMPT_ID)
    assert predictions == [((launch.store.root / adapter_ref.artifact_id / adapter_ref.inventory_sha256
                             / "files").as_posix(), ("row:1", "row:0"))]
    assert result["evidence"]["output/predictions.json"] == digest(b'{"predictions":["x","y"]}')
    assert not (directory / "output" / "metrics.json").exists()   # metrics are computed on the control plane


def test_runtime_preflight_uses_bound_model_not_config_base(tmp_path, monkeypatch):
    import sys

    from test_model_inputs import graph_config, manual_paths

    from tuning_factory import operation_runner
    bound = tmp_path / "model-cache" / "policy"
    bound.mkdir(parents=True)
    paths = manual_paths(operation_runner, bound)
    calls, observed = [], {"model_type": "qwen2"}

    class AutoConfig:
        @staticmethod
        def from_pretrained(name, **kwargs):
            calls.append(("config", name, kwargs))
            return SimpleNamespace(num_hidden_layers=2, max_position_embeddings=64, **observed)

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(name, **kwargs):
            calls.append(("tokenizer", name, kwargs))
            return SimpleNamespace(get_vocab=lambda: {"token": 1})

    fake_cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, is_bf16_supported=lambda: True)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=fake_cuda))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoConfig=AutoConfig, AutoTokenizer=AutoTokenizer))
    config = graph_config(hyperparameters={"cutoff_len": 64})
    facts = worker.runtime_preflight_bound(config, paths)
    assert [(kind, name) for kind, name, _ in calls] == [("config", str(bound)), ("tokenizer", str(bound))]
    assert all(kwargs == {"local_files_only": True, "trust_remote_code": False} for _, _, kwargs in calls)
    assert config.base_model not in repr(calls) and config.model_revision not in repr(calls)
    assert (facts["model_type"], facts["bound_model"]) == ("qwen2", bound.as_posix())
    with pytest.raises(ValueError, match="cutoff length exceeds the bound model context length"):
        worker.runtime_preflight_bound(graph_config(hyperparameters={"cutoff_len": 128}), paths)
    observed["model_type"] = "llama"
    with pytest.raises(ValueError, match="pinned model architecture differs"):
        worker.runtime_preflight_bound(config, paths)


def test_ppo_full_reward_tokenizer_check_uses_bound_paths(tmp_path, monkeypatch):
    import sys

    from test_model_inputs import graph_config, manual_entry

    from tuning_factory import operation_runner
    policy_dir, reward_dir = tmp_path / "model-cache" / "policy", tmp_path / "model-cache" / "reward"
    names, vocabularies = [], {str(policy_dir): {"token": 1}, str(reward_dir): {"token": 2}}

    class AutoTokenizer:
        @staticmethod
        def from_pretrained(name, **kwargs):
            names.append(name)
            return SimpleNamespace(get_vocab=lambda: vocabularies[name], bos_token_id=1, eos_token_id=2,
                                   pad_token_id=0, unk_token_id=3)

    class AutoConfig:
        @staticmethod
        def from_pretrained(name, **kwargs):
            return SimpleNamespace(model_type="qwen2", num_hidden_layers=2, max_position_embeddings=2048)

    fake_cuda = SimpleNamespace(is_available=lambda: True, device_count=lambda: 1, is_bf16_supported=lambda: True)
    monkeypatch.setitem(sys.modules, "torch", SimpleNamespace(cuda=fake_cuda))
    monkeypatch.setitem(sys.modules, "transformers", SimpleNamespace(AutoConfig=AutoConfig, AutoTokenizer=AutoTokenizer))
    paths = operation_runner.VerifiedModelPaths(models=(manual_entry(operation_runner, policy_dir, "policy_base"),
                                                        manual_entry(operation_runner, reward_dir, "reward_base")))
    config = graph_config(stage="ppo")
    with pytest.raises(ValueError, match="PPO reward model tokenizer must match the pinned policy tokenizer"):
        worker.runtime_preflight_bound(config, paths)
    assert names == [str(policy_dir), str(reward_dir)]
    vocabularies[str(reward_dir)] = {"token": 1}
    assert worker.runtime_preflight_bound(config, paths)["reward_tokenizer_verified"] is True


def test_v2_verify_model_cache_is_a_fixed_argv_entry(tmp_path, monkeypatch, capsys):
    from test_model_inputs import frozen, full_model

    from tuning_factory.model_artifacts import ModelArtifactStore
    store = ModelArtifactStore(tmp_path / "model-cache")
    ref, _ = frozen(store, full_model(tmp_path, "cached"))
    argv = ["worker", "--verify-model-cache", str(store.root), ref.artifact_id, ref.inventory_sha256]
    monkeypatch.setattr("sys.argv", argv)
    assert worker.main() == 0
    (store.root / ref.artifact_id / ref.inventory_sha256 / "files" / "model.safetensors").write_bytes(b"corrupt")
    assert worker.main() == 1
    assert "hash" in capsys.readouterr().err
    directory = tmp_path / JOB_ID / LAUNCH_ID
    for invalid in (["worker", str(directory), "5", "--request", "0" * 64],
                    ["worker", str(directory), "--request", "not-a-sha256"],
                    [*argv, str(directory)],
                    ["worker", str(directory), "--request", "0" * 64, "--checkpoint", "output/checkpoint-1"],
                    ["worker", "--request", "0" * 64]):
        monkeypatch.setattr("sys.argv", invalid)
        with pytest.raises(SystemExit) as exited:
            worker.main()
        assert exited.value.code == 2
    assert not directory.exists()
