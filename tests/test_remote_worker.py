import json
import shlex
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

import pytest

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
