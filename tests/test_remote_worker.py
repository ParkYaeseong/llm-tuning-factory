import json
import re
import shlex
import shutil
import socket
import stat
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
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import OperationLineage, OperationResources, RuntimeProfile, TrainOperation
    from tuning_factory.projects import DatasetRef
    from tuning_factory.worker_protocol import WorkerRequest
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
    # A foreign start marker next to an existing result: the result bytes are never touched.
    other = v2_launch(tmp_path / "other", monkeypatch)
    (other.directory / "start.json").write_bytes(b'{"pid":1}')
    (other.directory / "result.json").write_bytes(b'{"status":"COMPLETED"}')
    before = sorted(path.name for path in other.directory.iterdir())
    assert worker.main_v2(other.directory, other.sha) == 3
    assert sorted(path.name for path in other.directory.iterdir()) == before
    assert (other.directory / "result.json").read_bytes() == b'{"status":"COMPLETED"}'
    assert other.calls == []


def test_v2_start_marker_without_a_result_is_final(tmp_path, monkeypatch):
    # The first worker may still be running (or died without a result): a duplicate start writes nothing at all,
    # not even a FAILED result, so the attempt stays uncertain for reconcile instead of being overwritten.
    launch = v2_launch(tmp_path, monkeypatch)
    (launch.directory / "start.json").write_bytes(b'{"pid":1}')
    before = {path.relative_to(launch.directory).as_posix(): path.read_bytes()
              for path in launch.directory.rglob("*") if path.is_file()}
    assert worker.main_v2(launch.directory, launch.sha) == 3
    assert {path.relative_to(launch.directory).as_posix(): path.read_bytes()
            for path in launch.directory.rglob("*") if path.is_file()} == before
    assert not (launch.directory / "result.json").exists() and launch.calls == []


def test_v2_start_marker_and_result_are_made_durable(tmp_path, monkeypatch):
    from tuning_factory import worker_protocol
    launch = v2_launch(tmp_path, monkeypatch)
    synced = []
    monkeypatch.setattr(worker_protocol, "fsync_directory", lambda path: synced.append(
        (Path(path), sorted(item.name for item in Path(path).iterdir()))))
    assert worker.main_v2(launch.directory, launch.sha) == 0
    directories = [path for path, _ in synced]
    assert directories == [launch.directory, launch.directory]   # after start.json, then after result.json
    assert "start.json" in synced[0][1] and "result.json" not in synced[0][1]
    assert "result.json" in synced[1][1] and "result.tmp" not in synced[1][1]


def test_v2_worker_derives_one_deadline_and_passes_its_preflights(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    readings = [1000.0, 1250.0]   # main_v2 starts; then, after its own checks, dispatch begins

    def clock():
        return readings.pop(0) if len(readings) > 1 else readings[0]

    monkeypatch.setattr(worker, "_clock", clock)
    seen = []

    def dispatch(operation, directory, timeout, canceled, **kwargs):
        seen.append((timeout, kwargs["request"].attempt_id, kwargs["preflight"] is worker.runtime_preflight_bound,
                     kwargs["evaluation_preflight"] is worker.evaluation_preflight_bound))
        raise RuntimeError("stopped after recording the budget")

    monkeypatch.setattr(worker, "execute_operation", dispatch)
    assert worker.main_v2(launch.directory, launch.sha) == 1
    assert seen == [(650.0, ATTEMPT_ID, True, True)]   # 900 s budget minus the 250 s already spent
    expired = v2_launch(tmp_path / "expired", monkeypatch)
    readings[:] = [1000.0, 1900.0]
    assert worker.main_v2(expired.directory, expired.sha) == 1
    assert "deadline" in v2_result(expired.directory)["error"] and len(seen) == 1


@pytest.mark.parametrize("variable", ["FACTORY_MODEL_CACHE", "HF_HOME"])
def test_v2_worker_refuses_relative_cache_roots(tmp_path, monkeypatch, variable):
    launch = v2_launch(tmp_path, monkeypatch)
    monkeypatch.setenv(variable, "relative/cache")
    assert worker.main_v2(launch.directory, launch.sha) == 1
    result = v2_result(launch.directory)
    assert result["status"] == "FAILED" and result["error"] == f"{variable} must be an absolute path"
    assert launch.calls == []


def test_completed_result_requires_every_identity():
    from pydantic import ValidationError

    from tuning_factory.worker_protocol import OperationResult
    complete = dict(status="COMPLETED", launch_id=LAUNCH_ID, attempt_id=ATTEMPT_ID, manifest_sha256="1" * 64,
                    operation_sha256="2" * 64, kind="train")
    assert OperationResult(**complete).status == "COMPLETED"
    for missing in ("attempt_id", "manifest_sha256", "operation_sha256", "kind"):
        with pytest.raises(ValidationError, match="completed result"):
            OperationResult(**{**complete, missing: None})
    assert OperationResult(status="FAILED", launch_id=LAUNCH_ID, error="refused").attempt_id is None


def test_v1_paths_refuse_a_v2_launch_directory(tmp_path, monkeypatch):
    launch = v2_launch(tmp_path, monkeypatch)
    assert worker.main_v2(launch.directory, launch.sha) == 0
    result = (launch.directory / "result.json").read_bytes()
    # The v1 executor never uploads or launches a v2 directory, so it can never overwrite a v2 result.json.
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    executor = SSHExecutor("test-host", 22, "root", "unused", client=client)
    with pytest.raises(ValueError, match="run_operation"):
        executor.run(LlamaFactoryBackend(), launch.directory, 5, lambda: False)
    assert client.commands == [] and not (launch.directory / "remote-attempt.json").exists()
    # A schema-2 manifest alone is enough to refuse the v1 launch path.
    bare = tmp_path / ("9" * 32)
    bare.mkdir()
    (bare / "manifest.json").write_bytes(canonical({"schema_version": 2}))
    with pytest.raises(ValueError, match="run_operation"):
        executor.run(LlamaFactoryBackend(), bare, 5, lambda: False)
    # The v1 worker entry refuses it too, before writing anything.
    monkeypatch.setattr("sys.argv", ["worker", str(launch.directory), "5"])
    assert worker.main() == 2
    assert (launch.directory / "result.json").read_bytes() == result


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
    from tuning_factory.operation_dispatch import write_benchmark_data
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import (
        EvaluateOperation,
        OperationLineage,
        OperationResources,
        RuntimeProfile,
    )
    from tuning_factory.projects import DatasetRef
    from tuning_factory.worker_protocol import WorkerRequest
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


# -- Task 7.4: SSH transport for v2 operations (remote model cache, one launch per attempt) ------

RUNPOD_ROOT = "/workspace/factory-desktop"


def reply(status=0):
    """A paramiko-shaped exec_command reply whose remote command has already exited."""
    channel = SimpleNamespace(exit_status_ready=lambda: True, recv_exit_status=lambda: status)
    return None, SimpleNamespace(channel=channel), SimpleNamespace(read=lambda: b"")


class LinuxSFTP(FakeSFTP):
    """Reports a symlink or Windows reparse point as S_IFLNK, as an SFTP server on Linux does; records puts."""

    def __init__(self, root):
        super().__init__(root)
        self.puts = []

    def put(self, source, target):
        self.puts.append(target)
        super().put(source, target)

    def lstat(self, path):
        info = super().lstat(path)
        if stat.S_ISLNK(info.st_mode) or getattr(info, "st_file_attributes", 0) & 0x400:
            return SimpleNamespace(st_mode=stat.S_IFLNK | 0o777)
        return info


class GPUHost:
    """A fake GPU host over a local tree; never a network.

    The fixed-argv cache check runs the real ``worker --verify-model-cache`` entry, ``mv -T``/``cp -P``
    act on the local tree, and a worker launch runs the real ``worker.main_v2`` with the launch command's
    cache and lock environment mapped onto the tree. ``mode`` emulates a lost start response or a worker
    that is still running (both create only the start marker until ``finish``).
    """

    def __init__(self, root, monkeypatch):
        root.mkdir(parents=True, exist_ok=True)
        self.sftp = LinuxSFTP(root)
        self.monkeypatch = monkeypatch
        self.commands = []
        self.mode = "run"            # run | lose_start_response | silent
        self.pending = None
        self.before_move = None      # runs before `mv -T`, e.g. a concurrent publisher
        self.after_worker = None     # runs after the worker published its result

    def open_sftp(self):
        return self.sftp

    def close(self):
        pass

    @property
    def launches(self):
        return [command for command in self.commands if "--request" in shlex.split(command)]

    def exec_command(self, command, timeout):
        self.commands.append(command)
        args = shlex.split(command)
        if "--verify-model-cache" in args:
            directory, artifact_id, inventory = args[args.index("--verify-model-cache") + 1:]
            return reply(worker.verify_model_cache(self.sftp.path(directory), artifact_id, inventory))
        if args[0] == "mv":
            assert args[:3] == ["mv", "-T", "--"] and len(args) == 5
            if self.before_move:
                self.before_move()
            source, target = self.sftp.path(args[3]), self.sftp.path(args[4])
            if target.exists():
                return reply(1)   # rename(2) refuses a non-empty destination directory
            source.rename(target)
            return reply(0)
        if args[0] == "cp":
            assert args[:3] == ["cp", "-P", "--"] and len(args) == 5
            shutil.copyfile(self.sftp.path(args[3]), self.sftp.path(args[4]))
            return reply(0)
        return self.launch(args)

    def launch(self, args):
        from tuning_factory.worker_protocol import StartMarker, create_start_marker
        index = args.index("tuning_factory.worker")
        assert args[:2] == ["nohup", "env"] and args[-6] == ">>" and args[-4:] == ["2>&1", "<", "/dev/null", "&"]
        for item in args[2:index - 2]:
            name, value = item.split("=", 1)
            if name in {"FACTORY_MODEL_CACHE", "HF_HOME", "FACTORY_GPU_LOCK_FILE"}:
                self.monkeypatch.setenv(name, str(self.sftp.path(value)))
        directory, sha = self.sftp.path(args[index + 1]), args[index + 3]
        with self.sftp.path(args[-5]).open("a") as log:
            log.write("worker launched\n")
        if self.mode != "run":
            self.pending = (directory, sha)
            assert create_start_marker(directory, StartMarker(launch_id=directory.name, command_sha256=sha, pid=1,
                                                              started_at="2026-10-20T00:00:00+00:00"))
            if self.mode == "lose_start_response":
                raise socket.timeout("worker launch response lost")
            return reply(0)
        self.run_worker(directory, sha)
        return reply(0)

    def run_worker(self, directory, sha):
        worker.main_v2(directory, sha)
        if self.after_worker:
            self.after_worker(directory)

    def finish(self):
        """The first worker, which created start.json at launch, runs on to its result."""
        directory, sha = self.pending
        self.monkeypatch.setattr(worker, "create_start_marker", lambda *args: True)
        self.run_worker(directory, sha)


def v2_transport(tmp_path, monkeypatch, provider="runpod"):
    """A control-plane v2 launch directory, an executor for one provider and a fake GPU host."""
    from tuning_factory.execution import CachedTransfer
    launch = v2_launch(tmp_path / "local", monkeypatch)
    host = GPUHost(tmp_path / "remote", monkeypatch)
    if provider == "ssh_server":
        from test_gpu_servers import observation, settings
        base = settings().remote_root
        options = dict(server_profile=settings().profile(observation()), server_facts=observation())
    else:
        base, options = RUNPOD_ROOT, dict(pod={"id": "pod1"})
    lock = base + "/runtime/gpu-requirements.lock"
    host.sftp.path(lock).parent.mkdir(parents=True)
    host.sftp.path(lock).write_bytes(launch.lock.read_bytes())
    executor = SSHExecutor("test-host", 22, "root", "unused", client=host, remote_root=base + "/jobs",
                           remote_python=base + "/runtime/venv/bin/python", worker_lock_file=lock,
                           hf_home=base + "/hf-cache", model_cache_root=base + "/model-cache", **options)
    ref = launch.sft_ref
    transfer = CachedTransfer(ref=ref, local_location=launch.store.root / ref.artifact_id / ref.inventory_sha256)
    return SimpleNamespace(launch=launch, host=host, executor=executor, transfers=(transfer,), base=base,
                           cache=base + "/model-cache", remote=f"{base}/jobs/{JOB_ID}/{LAUNCH_ID}",
                           entry=f"{base}/model-cache/{ref.artifact_id}/{ref.inventory_sha256}",
                           job=launch.directory.parent)


def run_v2(t, canceled=lambda: False, request=None):
    return t.executor.run_operation(t.job, t.launch.directory, request or t.launch.request, t.transfers, canceled)


def collect_v2(t, executor=None):
    return (executor or t.executor).collect_operation_result(t.job, t.launch.directory, t.launch.request)


def seed_remote_cache(t, corrupt=False):
    """Place a copy of the control-plane entry in the fake host's cache (optionally corrupted)."""
    target = t.host.sftp.path(t.entry)
    shutil.copytree(t.transfers[0].local_location, target)
    if corrupt:
        (target / "files" / "model.safetensors").write_bytes(b"corrupt cache entry")
    return target


@pytest.mark.parametrize("provider", ["runpod", "ssh_server"])
def test_v2_run_operation_publishes_cache_launches_once_and_collects(tmp_path, monkeypatch, provider):
    from tuning_factory.operation_runner import verify_cached_artifact
    t = v2_transport(tmp_path, monkeypatch, provider)
    result = run_v2(t)
    assert {key: result[key] for key in ("status", "launch_id", "attempt_id", "manifest_sha256", "operation_sha256",
                                         "kind")} == {
        "status": "COMPLETED", "launch_id": LAUNCH_ID, "attempt_id": ATTEMPT_ID,
        "manifest_sha256": t.launch.manifest["sha256"], "operation_sha256": t.launch.request.operation_sha256,
        "kind": "train"}
    assert t.launch.calls == ["train.bound.yaml"] and len(t.host.launches) == 1
    # The weights reached the worker only through the verified remote cache, moved into place (never linked).
    ref = t.launch.sft_ref
    verify_cached_artifact(t.host.sftp.path(t.cache), ref)
    assert not t.host.sftp.path(f"{t.cache}/.incoming/{LAUNCH_ID}/{ref.artifact_id}/{ref.inventory_sha256}").exists()
    assert not any(path.startswith(t.remote + "/") and path.endswith(".safetensors") for path in t.host.sftp.puts)
    bound = t.host.sftp.path(t.remote + "/train.bound.yaml").read_text()
    assert t.host.sftp.path(t.cache).as_posix() in bound and t.launch.store.root.as_posix() not in bound
    record = json.loads((t.launch.directory / "remote-attempt.json").read_bytes())
    assert (record["remote_path"], record["launch_id"], record["attempt_id"]) == (t.remote, LAUNCH_ID, ATTEMPT_ID)
    assert ("destination" in record) == (provider == "ssh_server")
    assert record.get("pod_id") == ("pod1" if provider == "runpod" else None)
    assert not t.host.sftp.path(t.remote + "/remote-attempt.json").exists()
    assert (t.launch.directory / "output" / "adapter_model.safetensors").read_bytes() == b"unit-test fixture; not weights"
    assert json.loads((t.launch.directory / "result.json").read_bytes()) == json.loads(
        (t.launch.directory / "remote-result.json").read_bytes())


def test_v2_requires_persisted_launch_identity(tmp_path, monkeypatch):
    from tuning_factory.execution import LocalExecutor
    t = v2_transport(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="launch"):   # the launch directory is named by the stored launch ID
        run_v2(t, request=t.launch.request.model_copy(update={"launch_id": "d" * 32}))
    malformed = t.launch.request.model_construct(**{**t.launch.request.model_dump(), "launch_id": "../escape"})
    with pytest.raises(ValueError, match="launch_id"):
        run_v2(t, request=malformed)
    with pytest.raises(ValueError, match="job"):
        t.executor.run_operation(t.job.parent, t.launch.directory, t.launch.request, t.transfers, lambda: False)
    assert t.host.commands == [] and t.host.sftp.puts == []
    assert not (t.launch.directory / "remote-attempt.json").exists()
    run_v2(t)
    # No new identity is ever invented: one launch directory, named by the persisted launch ID.
    assert sorted(path.name for path in t.host.sftp.path(t.base + "/jobs/" + JOB_ID).iterdir()) == [LAUNCH_ID]
    assert t.remote in t.host.launches[0]
    with pytest.raises(NotImplementedError):
        LocalExecutor().run_operation(t.job, t.launch.directory, t.launch.request, t.transfers, lambda: False)


def test_v2_lost_start_response_never_relaunches(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    t.host.mode = "lose_start_response"
    with pytest.raises(ExecutionUncertain) as error:
        run_v2(t)
    assert isinstance(error.value.__cause__, socket.timeout)
    assert collect_v2(t) is None   # started, no result yet: uncertain, never relaunched
    commands, puts = list(t.host.commands), list(t.host.sftp.puts)
    with pytest.raises(ExecutionUncertain,
                       match="remote launch already started for this attempt; reconcile instead of relaunching"):
        run_v2(t)
    assert t.host.commands == commands and t.host.sftp.puts == puts
    assert len(t.host.launches) == 1 and t.launch.calls == []
    t.host.finish()
    result = collect_v2(t)
    assert (result["status"], result["attempt_id"], result["launch_id"]) == ("COMPLETED", ATTEMPT_ID, LAUNCH_ID)
    assert t.launch.calls == ["train.bound.yaml"] and len(t.host.launches) == 1
    assert (t.launch.directory / "output" / "adapter_model.safetensors").is_file()


def test_v2_existing_result_is_collected_not_relaunched(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    assert run_v2(t)["status"] == "COMPLETED"
    commands, puts = list(t.host.commands), list(t.host.sftp.puts)
    with pytest.raises(ExecutionUncertain, match="reconcile instead of relaunching"):
        run_v2(t)
    assert t.host.commands == commands and t.host.sftp.puts == puts
    assert collect_v2(t)["status"] == "COMPLETED" and t.launch.calls == ["train.bound.yaml"]
    # A result alone (no start marker) is refused just the same, before any upload or cache work.
    other = v2_transport(tmp_path / "other", monkeypatch)
    other.host.sftp.path(other.remote).mkdir(parents=True)
    other.host.sftp.path(other.remote + "/result.json").write_text('{"status":"COMPLETED"}')
    with pytest.raises(ExecutionUncertain, match="reconcile instead of relaunching"):
        run_v2(other)
    assert other.host.commands == [] and other.host.sftp.puts == []


@pytest.mark.parametrize("case", ["verified", "corrupt", "missing", "concurrent", "incoming_corrupt"])
def test_v2_cache_reused_only_after_remote_hash_verification(tmp_path, monkeypatch, case):
    t = v2_transport(tmp_path, monkeypatch)
    ref = t.launch.sft_ref
    staging = f"{t.cache}/.incoming/{LAUNCH_ID}"
    incoming = f"{staging}/{ref.artifact_id}/{ref.inventory_sha256}"

    def verify(root):
        return shlex.join([t.executor.remote_python, "-m", "tuning_factory.worker", "--verify-model-cache", root,
                           ref.artifact_id, ref.inventory_sha256])
    move = shlex.join(["mv", "-T", "--", incoming, t.entry])
    seeded = seed_remote_cache(t, corrupt=case == "corrupt") if case in {"verified", "corrupt"} else None
    if case == "concurrent":
        t.host.before_move = lambda: seed_remote_cache(t)
    if case == "incoming_corrupt":
        original = t.host.sftp.put

        def corrupting(source, target):
            original(source, target)
            if target.endswith("/model.safetensors"):
                t.host.sftp.path(target).write_bytes(b"corrupted in transit")
        t.host.sftp.put = corrupting
    if case in {"corrupt", "incoming_corrupt"}:
        message = ("remote model cache entry failed verification; remove it after inspection" if case == "corrupt"
                   else "uploaded model cache entry failed remote verification")
        with pytest.raises(ValueError, match=re.escape(message)):
            run_v2(t)
        assert t.host.launches == [] and t.launch.calls == [] and move not in t.host.commands
        assert not t.host.sftp.path(t.remote).exists()   # nothing of the attempt was uploaded
        if case == "corrupt":   # fail closed: the corrupt entry is never deleted or replaced
            assert (seeded / "files" / "model.safetensors").read_bytes() == b"corrupt cache entry"
            assert not any(path.startswith(t.cache) for path in t.host.sftp.puts)
        else:                   # the rejected upload is kept for inspection
            assert verify(staging) in t.host.commands and t.host.sftp.path(incoming).is_dir()
        return
    assert run_v2(t)["status"] == "COMPLETED"
    cache_puts = [path for path in t.host.sftp.puts if path.startswith(t.cache)]
    if case == "verified":
        assert cache_puts == [] and verify(t.cache) in t.host.commands and move not in t.host.commands
    else:
        assert cache_puts and all(path.startswith(staging + "/") for path in cache_puts)
        assert t.host.commands.index(verify(staging)) < t.host.commands.index(move)
        if case == "missing":
            assert not t.host.sftp.path(incoming).exists()
        else:   # another publisher won the race: its entry is verified, ours is left for manual cleanup
            assert verify(t.cache) in t.host.commands and t.host.sftp.path(incoming).is_dir()
    assert t.launch.calls == ["train.bound.yaml"]


def test_v2_cache_check_that_cannot_run_is_not_reported_as_corruption(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    seeded = seed_remote_cache(t)
    original = t.host.exec_command

    def missing_runtime(command, timeout):
        if "--verify-model-cache" in command:
            t.host.commands.append(command)
            return reply(127)   # e.g. the private runtime is not prepared on this host
        return original(command, timeout)
    t.host.exec_command = missing_runtime
    with pytest.raises(RuntimeError, match="remote model cache check could not run"):
        run_v2(t)
    assert (seeded / "files" / "model.safetensors").is_file() and t.host.launches == []
    with pytest.raises(TypeError, match="CachedTransfer"):
        t.executor.publish_cached_artifact(t.launch.sft_ref, launch_id=LAUNCH_ID)


def test_v2_launch_directory_with_weights_is_refused(tmp_path, monkeypatch):
    from tuning_factory.execution import CachedTransfer
    t = v2_transport(tmp_path, monkeypatch)
    # Exactly the staged artifacts (model-inputs.json) are published through the cache; HF roots never are.
    for transfers in ((), t.transfers * 2):
        with pytest.raises(ValueError, match="model transfers differ from the staged model inputs"):
            t.executor.run_operation(t.job, t.launch.directory, t.launch.request, transfers, lambda: False)
    misplaced = (CachedTransfer(ref=t.launch.sft_ref, local_location=t.launch.directory),)
    with pytest.raises(ValueError, match="model cache entry"):
        t.executor.run_operation(t.job, t.launch.directory, t.launch.request, misplaced, lambda: False)
    assert not (t.launch.directory / "remote-attempt.json").exists()
    weights = t.launch.directory / "data" / "model.bin"
    weights.write_bytes(b"weights must come from the model cache")
    with pytest.raises(ValueError, match="attempt directory must not contain model weights; stage them through the "
                                         "model cache"):
        run_v2(t)
    # Refused after only the read-only start/result check: nothing was executed, published or uploaded.
    assert t.host.commands == [] and t.host.sftp.puts == [] and not t.host.sftp.path(t.cache).exists()


def test_v2_launch_command_is_fixed_argv_with_append_redirect(tmp_path, monkeypatch):
    from test_gpu_servers import UUID2
    t = v2_transport(tmp_path, monkeypatch, "ssh_server")
    run_v2(t)
    (launch,) = t.host.launches
    args = shlex.split(launch)
    index = args.index("tuning_factory.worker")
    assert args[:2] == ["nohup", "env"] and dict(item.split("=", 1) for item in args[2:index - 2]) == {
        "CUDA_VISIBLE_DEVICES": UUID2, "FACTORY_GPU_LOCK_FILE": t.base + "/runtime/gpu-requirements.lock",
        "HF_HOME": t.base + "/hf-cache", "FACTORY_MODEL_CACHE": t.cache}
    assert args[index - 2:] == [t.executor.remote_python, "-m", "tuning_factory.worker", t.remote, "--request",
                                digest((t.launch.directory / "command.json").read_bytes()),
                                ">>", t.remote + "/worker.log", "2>&1", "<", "/dev/null", "&"]
    assert " >> " in launch and " > " not in launch   # a duplicate can never truncate the first worker's log
    # Every other remote command is a fixed argv: re-quoting its parsed form gives exactly the same bytes.
    for command in t.host.commands:
        if command != launch:
            assert shlex.join(shlex.split(command)) == command


@pytest.mark.parametrize("field,value,status", [
    ("attempt_id", "d" * 32, "COMPLETED"),
    ("manifest_sha256", "0" * 64, "COMPLETED"),
    ("operation_sha256", "0" * 64, "COMPLETED"),
    ("launch_id", "d" * 32, "COMPLETED"),
    ("kind", "merge", "COMPLETED"),
    ("attempt_id", "d" * 32, "FAILED"),
])
def test_v2_result_identity_mismatch_is_rejected(tmp_path, monkeypatch, field, value, status):
    t = v2_transport(tmp_path, monkeypatch)

    def foreign(directory):
        result = json.loads((directory / "result.json").read_bytes())
        result.update({field: value, "status": status, "error": "elsewhere" if status == "FAILED" else None})
        (directory / "result.json").write_bytes(canonical(result))
    t.host.after_worker = foreign
    with pytest.raises(ExecutionUncertain) as error:
        run_v2(t)
    assert str(error.value.__cause__) == "remote result belongs to a different attempt"
    # Nothing of a foreign result is trusted or transferred.
    assert not (t.launch.directory / "remote-result.json").exists() and not (t.launch.directory / "output").exists()
    with pytest.raises(ValueError, match="remote result belongs to a different attempt"):
        collect_v2(t)


def test_v2_failed_result_reports_only_verified_identities(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    t.host.sftp.path(t.base + "/runtime/gpu-requirements.lock").write_bytes(b"another dependency set\n")
    result = run_v2(t)
    assert (result["status"], result["error"], result["kind"]) == (
        "FAILED", "worker dependency lock differs from operation runtime", None)
    assert (result["attempt_id"], result["operation_sha256"]) == (ATTEMPT_ID, t.launch.request.operation_sha256)
    assert (t.launch.directory / "worker-error.log").is_file() and t.launch.calls == []


def test_v2_collect_checks_original_command_and_manifest(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    run_v2(t)
    remote = t.host.sftp.path(t.remote)
    for name, message in (("command.json", "remote command differs from the original attempt"),
                          ("manifest.json", "remote manifest differs from the original attempt")):
        original = (remote / name).read_bytes()
        (remote / name).write_bytes(original + b" ")
        with pytest.raises(ValueError, match=message):
            collect_v2(t)
        (remote / name).write_bytes(original)
    artifacts = remote / "artifacts.json"
    original = artifacts.read_bytes()
    artifacts.write_bytes(canonical({**json.loads(original), "model-inputs.json": "0" * 64}))
    with pytest.raises(ValueError, match="remote artifact inventory differs from the original attempt files"):
        collect_v2(t)
    artifacts.write_bytes(original)
    assert collect_v2(t)["status"] == "COMPLETED"
    record = t.launch.directory / "remote-attempt.json"
    original = record.read_bytes()
    record.write_text(json.dumps({**json.loads(original), "remote_path": f"{t.base}/jobs/{JOB_ID}/{'d' * 32}"}))
    with pytest.raises(ValueError, match="remote attempt record differs from the persisted launch identity"):
        collect_v2(t)
    record.write_bytes(original)
    command = t.launch.directory / "command.json"
    command.write_bytes(command.read_bytes() + b" ")
    with pytest.raises(ValueError, match="command.json differs from the worker request"):
        collect_v2(t)


def test_v2_upload_excludes_start_and_result_markers(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    stale = ("start.json", "result.json", "result.tmp", "cancel")
    for name in stale:
        (t.launch.directory / name).write_text('{"status":"COMPLETED"}')
    assert run_v2(t)["status"] == "COMPLETED" and t.launch.calls == ["train.bound.yaml"]
    uploaded = {path[len(t.remote) + 1:] for path in t.host.sftp.puts if path.startswith(t.remote + "/")}
    assert uploaded.isdisjoint({*stale, "remote-attempt.json"})
    assert {"command.json", "manifest.json", "model-inputs.json", "train.yaml", "data/dataset.json"} <= uploaded


def test_v2_deadline_overrun_is_uncertain_and_never_relaunched(tmp_path, monkeypatch):
    import itertools
    import time

    from tuning_factory import execution
    t = v2_transport(tmp_path, monkeypatch)
    t.host.mode = "silent"   # started; an uninterruptible model operation runs past its deadline
    clock = itertools.count(0, 1000)
    monkeypatch.setattr(execution, "time", SimpleNamespace(monotonic=lambda: next(clock), sleep=lambda _: None,
                                                           time=time.time))
    with pytest.raises(ExecutionUncertain) as error:
        run_v2(t)
    assert str(error.value.__cause__) == ("remote worker deadline exceeded; reconcile the remote GPU host "
                                          "and retain GPU lock")
    with pytest.raises(ExecutionUncertain, match="reconcile instead of relaunching"):
        run_v2(t)
    assert collect_v2(t) is None and len(t.host.launches) == 1
    monkeypatch.setattr(worker, "_clock", itertools.count(0, 10 ** 6).__next__)   # it returns after its deadline
    t.host.finish()
    result = collect_v2(t)
    assert (result["status"], result["attempt_id"]) == ("FAILED", ATTEMPT_ID) and "deadline" in result["error"]
    assert t.launch.calls == [] and len(t.host.launches) == 1


@pytest.mark.parametrize("root", ["model-cache", "hf-cache"])
def test_v2_remote_cache_roots_must_be_link_free(tmp_path, monkeypatch, root):
    from test_model_artifacts import directory_link
    t = v2_transport(tmp_path, monkeypatch)
    (tmp_path / "elsewhere").mkdir()
    directory_link(t.host.sftp.path(t.base + "/" + root), tmp_path / "elsewhere")
    with pytest.raises(ValueError, match="remote path is not an ordinary directory"):
        run_v2(t)
    assert t.host.launches == [] and not t.host.sftp.path(t.remote).exists()


@pytest.mark.parametrize("variable", ["FACTORY_MODEL_CACHE", "HF_HOME"])
def test_v2_worker_refuses_cache_roots_under_links(tmp_path, monkeypatch, variable):
    from test_model_artifacts import directory_link
    launch = v2_launch(tmp_path, monkeypatch)
    target = launch.store.root if variable == "FACTORY_MODEL_CACHE" else tmp_path / "hf"
    target.mkdir(exist_ok=True)
    directory_link(tmp_path / "linked", target)
    monkeypatch.setenv(variable, str(tmp_path / "linked"))
    assert worker.main_v2(launch.directory, launch.sha) == 1
    result = v2_result(launch.directory)
    assert (result["status"], result["error"]) == ("FAILED", f"{variable} must not sit under a symlink or reparse point")
    assert launch.calls == []


def test_publish_cached_artifact_seeds_from_remote_outputs_and_verifies(tmp_path, monkeypatch):
    from tuning_factory.operation_runner import verify_cached_artifact
    t = v2_transport(tmp_path, monkeypatch)
    ref, transfer, finished = t.launch.sft_ref, t.transfers[0], "e" * 32
    output = f"{t.base}/jobs/{JOB_ID}/{finished}/output"
    shutil.copytree(transfer.local_location / "files", t.host.sftp.path(output))
    (t.host.sftp.path(output) / "tokenizer.json").unlink()   # what the output lacks comes from the control plane
    t.executor.publish_cached_artifact(transfer, source_remote_dir=output)
    verify_cached_artifact(t.host.sftp.path(t.cache), ref)
    assert sorted(path.rsplit("/", 1)[-1] for path in t.host.sftp.puts) == [
        "descriptor.json", "inventory.json", "tokenizer.json"]
    incoming = f"{t.cache}/.incoming/{finished}/{ref.artifact_id}/{ref.inventory_sha256}"
    assert shlex.join(["cp", "-P", "--", output + "/model.safetensors", incoming + "/files/model.safetensors"]) in (
        t.host.commands)
    # A verified entry is reused without any transfer; a source outside this executor's launches is refused.
    t.host.sftp.puts.clear()
    t.executor.publish_cached_artifact(transfer, source_remote_dir=output)
    assert t.host.sftp.puts == []
    for outside in ("/etc", f"{t.base}/jobs/{JOB_ID}", f"{t.base}/jobs/{JOB_ID}/{finished}/../x"):
        with pytest.raises(ValueError, match="launch namespace"):
            t.executor.publish_cached_artifact(transfer, source_remote_dir=outside)
    with pytest.raises(ValueError, match="launch identity"):
        t.executor.publish_cached_artifact(transfer)


def test_v2_recovery_executor_collects_but_never_launches_or_publishes(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch, "ssh_server")
    t.host.mode = "lose_start_response"
    with pytest.raises(ExecutionUncertain):
        run_v2(t)
    recovery = SSHExecutor("", 22, "researcher", "", client=t.host, server_profile=t.executor.server_profile,
                           server_facts=t.executor.server_facts, remote_root=t.base + "/jobs", recovery_only=True)
    with pytest.raises(ValueError, match="recovery-only"):
        recovery.run_operation(t.job, t.launch.directory, t.launch.request, t.transfers, lambda: False)
    with pytest.raises(ValueError, match="recovery-only"):
        recovery.publish_cached_artifact(t.transfers[0], launch_id=LAUNCH_ID)
    assert collect_v2(t, recovery) is None
    t.host.finish()
    assert collect_v2(t, recovery)["status"] == "COMPLETED" and len(t.host.launches) == 1


def test_v1_collect_refuses_a_v2_launch_directory(tmp_path, monkeypatch):
    t = v2_transport(tmp_path, monkeypatch)
    run_v2(t)
    with pytest.raises(ValueError, match="collect_operation_result"):
        t.executor.collect_result(t.launch.directory)


def test_v2_directory_detection_is_one_shared_helper(tmp_path):
    from tuning_factory import execution, worker_protocol
    assert execution.is_v2_directory is worker_protocol.is_v2_directory is worker.is_v2_directory
    cases = {"empty": ({}, False), "command": ({"command.json": b"{}"}, True), "start": ({"start.json": b"{}"}, True),
             "v2": ({"manifest.json": canonical({"schema_version": 2})}, True),
             "v1": ({"manifest.json": canonical({"schema_version": 1})}, False),
             "invalid": ({"manifest.json": b"not json"}, False), "list": ({"manifest.json": b"[2]"}, False)}
    for name, (files, expected) in cases.items():
        directory = tmp_path / name
        directory.mkdir()
        for file, data in files.items():
            (directory / file).write_bytes(data)
        assert worker_protocol.is_v2_directory(directory) is expected, name
