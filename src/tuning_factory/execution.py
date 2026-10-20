"""Infrastructure policy and execution adapters; no implicit paid actions."""
import json
import math
import re
import shlex
import stat
import time
import uuid
from abc import ABC, abstractmethod
from pathlib import Path, PurePosixPath

import httpx
import paramiko
from pydantic import Field

from .config import StrictModel
from .gpu_servers import SSHServerFacts, safe_remote_root, validate_server_binding
from .registry import file_digest


class CapacityUnavailable(RuntimeError):
    pass


class ExecutionUncertain(RuntimeError):
    """Remote process termination could not be established; retain its GPU lease."""


V2_LAUNCH_MARKERS = ("command.json", "start.json")


def _refuse_v2_directory(directory: Path) -> None:
    """The v1 launch path never uploads or launches a v2 directory (and so never overwrites its result)."""
    v2 = any((directory / name).exists() or (directory / name).is_symlink() for name in V2_LAUNCH_MARKERS)
    if not v2:
        try:
            manifest = json.loads((directory / "manifest.json").read_bytes())
        except (OSError, ValueError):
            manifest = None
        v2 = isinstance(manifest, dict) and manifest.get("schema_version") == 2
    if v2:
        raise ValueError("v2 operation directories launch only through run_operation; refusing the v1 launch path")


class Policy(StrictModel):
    max_gpus: int = Field(default=1, ge=1, le=8)
    max_concurrent: int = Field(default=1, ge=1)
    max_timeout_seconds: int = Field(default=14400, ge=1)
    max_cost_usd: float = Field(default=5, gt=0)
    max_attempts: int = Field(default=2, ge=1, le=5)

    def check(self, config, dataset, execute=False, approved_transfer=False, *, server_facts=None):
        resource = config.resource_profile
        estimate = resource.hourly_cost_usd * resource.timeout_seconds / 3600 * resource.gpu_count
        if resource.gpu_count > self.max_gpus:
            raise ValueError("GPU count exceeds policy")
        if resource.timeout_seconds > self.max_timeout_seconds:
            raise ValueError("runtime exceeds policy")
        if estimate > min(resource.max_cost_usd, self.max_cost_usd):
            raise ValueError("estimated cost exceeds policy")
        if execute:
            if not dataset.get("export_allowed"):
                raise ValueError("dataset export has not been authorized in registry")
            if not approved_transfer:
                raise ValueError("explicit data-transfer and execution approval required")
            if server_facts is not None:
                if not isinstance(server_facts, SSHServerFacts):
                    raise ValueError("typed verified SSH server observation required")
                SSHServerFacts.model_validate(server_facts.model_dump()).validate_config(config)
            if resource.hourly_cost_usd <= 0 and server_facts is None:
                raise ValueError("positive GPU price required before paid execution")
        return estimate


class RunPodController:
    def __init__(self, api_key, client=None):
        self.client = client or httpx.Client(timeout=30)
        self.api_key = api_key

    def request(self, method, path, payload=None):
        response = self.client.request(method, "https://rest.runpod.io/v1" + path,
                                       headers={"Authorization": f"Bearer {self.api_key}"}, json=payload)
        if response.status_code in {409, 429, 503}:
            raise CapacityUnavailable(f"RunPod capacity/rate limit: HTTP {response.status_code}")
        # Do not include HTTP request objects containing Authorization in operator errors.
        if response.is_error:
            raise RuntimeError(f"RunPod request failed: HTTP {response.status_code}")
        return response.json() if response.content else {}

    def status(self, pod_id):
        return self.normalize(self.request("GET", f"/pods/{self.identifier(pod_id)}?includeMachine=true&includeNetworkVolume=true"))

    @staticmethod
    def normalize(pod):
        pod = dict(pod)
        if not pod.get("gpu"):
            machine = pod.get("machine") or {}
            gpu_type = machine.get("gpuType") or {}
            pod["gpu"] = {"id": machine.get("gpuTypeId") or pod.get("gpuTypeId"),
                          "displayName": machine.get("gpuDisplayName") or gpu_type.get("displayName"),
                          "count": pod.get("gpuCount") or gpu_type.get("count")}
        return pod

    @staticmethod
    def identifier(value):
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", value):
            raise ValueError("invalid Pod ID")
        return value

    def create(self, payload, approved=False):
        if not approved:
            raise ValueError("new paid Pod creation requires explicit approval")
        if not payload.get("networkVolumeId"):
            raise ValueError("network volume required for managed Pods")
        return self.request("POST", "/pods", payload)

    def start(self, pod_id, approved=False):
        if not approved:
            raise ValueError("starting paid compute requires explicit approval")
        return self.request("POST", f"/pods/{self.identifier(pod_id)}/start")

    def stop(self, pod_id, owned=False, persisted=False):
        self.cleanup_guard(owned, persisted)
        return self.request("POST", f"/pods/{self.identifier(pod_id)}/stop")

    def delete(self, pod_id, owned=False, persisted=False):
        self.cleanup_guard(owned, persisted)
        return self.request("DELETE", f"/pods/{self.identifier(pod_id)}")

    @staticmethod
    def cleanup_guard(owned, persisted):
        if not owned:
            raise ValueError("only factory-owned Pods may be changed")
        if not persisted:
            raise ValueError("artifact persistence must be verified before cleanup")


class ExecutionBackend(ABC):
    remote = False

    def validate_config(self, config):
        pass

    @abstractmethod
    def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None): ...


class LocalExecutor(ExecutionBackend):
    def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
        if checkpoint:
            backend.resume(directory, checkpoint, timeout, canceled)
        else:
            backend.train(directory, timeout, canceled)


class SSHExecutor(ExecutionBackend):
    """Existing Pod with a preinstalled factory image. Strict known_hosts verification."""
    remote = True

    def __init__(self, host, port, username, key_file, remote_root="/workspace/factory", client=None, pod=None,
                 remote_python="python", worker_lock_file=None, hf_home=None,
                 server_profile=None, server_facts=None, recovery_only=False):
        if recovery_only and server_profile is None:
            raise ValueError("recovery-only execution requires the original server destination")
        if server_profile is not None or server_facts is not None:
            if client is None:
                raise ValueError("inspected SSH connection required for server execution")
            if pod is not None or server_profile is None or server_facts is None:
                raise ValueError("use one truthful provider/resource observation")
            validate_server_binding(server_profile, server_facts)
            expected_root = server_profile.transport.remote_root + "/jobs"
            if remote_root == "/workspace/factory":
                remote_root = expected_root
            elif remote_root != expected_root:
                raise ValueError("remote root differs from pinned server destination")
        if server_profile is not None:
            safe_remote_root(remote_root)
        elif not re.fullmatch(r"/[a-zA-Z0-9_/-]+", remote_root) or ".." in remote_root:
            raise ValueError("remote_root must be a safe absolute Linux path")
        self.client = client or paramiko.SSHClient()
        if client is None:
            self.client.load_system_host_keys()
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
            self.client.connect(host, port=port, username=username, key_filename=str(key_file), timeout=20,
                                allow_agent=False, look_for_keys=False)
        self.root = remote_root
        self.pod = pod
        self.server_profile = server_profile
        self.server_facts = server_facts
        # Recovery carries the original approval facts, not a fresh launch observation.
        self.recovery_only = recovery_only
        self.remote_python = remote_python
        self.worker_lock_file = worker_lock_file
        self.hf_home = hf_home

    def validate_config(self, config):
        if self.server_profile is not None:
            validate_server_binding(self.server_profile, self.server_facts)
            self.server_facts.validate_config(config)
            return
        pod = self.pod
        resource = config.resource_profile
        if not pod:
            raise ValueError("live RunPod metadata required before transfer")
        if pod.get("gpu", {}).get("count") != resource.gpu_count:
            raise ValueError("claimed job GPU count differs from live Pod")
        if resource.gpu_type not in {pod.get("gpu", {}).get("id"), pod.get("gpu", {}).get("displayName")}:
            raise ValueError("claimed job GPU type differs from live Pod")
        try:
            price = float(pod["costPerHr"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("positive finite live RunPod API price required") from None
        if not math.isfinite(price) or price <= 0:
            raise ValueError("positive finite live RunPod API price required")
        if price > resource.hourly_cost_usd * resource.gpu_count:
            raise ValueError("claimed job price exceeds approved resource budget")

    def mkdir(self, sftp, path):
        current = ""
        for part in PurePosixPath(path).parts[1:]:
            current += "/" + part
            try:
                sftp.mkdir(current)
            except OSError:
                if not stat.S_ISDIR(sftp.lstat(current).st_mode):
                    raise ValueError("remote path is not an ordinary directory") from None

    def upload(self, sftp, local, remote):
        self.mkdir(sftp, remote)
        for path in local.rglob("*"):
            if path.parent == local and path.name in {
                "result.json", "result.tmp", "cancel", "remote-result.json", "remote-artifacts.json"
            }:
                continue
            if path.is_symlink():
                raise ValueError("symlinks are not transferable")
            target = remote + "/" + path.relative_to(local).as_posix()
            if path.is_dir():
                self.mkdir(sftp, target)
            elif path.is_file():
                sftp.put(str(path), target)

    def download(self, sftp, remote, local, hashes=None, root=None):
        root = root or local
        local.mkdir(parents=True, exist_ok=True)
        # Preserve small top-level evidence before descending into large checkpoints.
        entries = sorted(sftp.listdir_attr(remote), key=lambda item: (stat.S_ISDIR(item.st_mode), item.filename))
        for entry in entries:
            if entry.filename in {".", ".."} or "/" in entry.filename or "\\" in entry.filename:
                raise ValueError("unsafe remote artifact name")
            if local == root and entry.filename == "cancel":
                continue  # Mutable control signal, never a model artifact.
            source, target = remote + "/" + entry.filename, local / entry.filename
            if stat.S_ISLNK(entry.st_mode):
                raise ValueError("remote symlink artifact rejected")
            if stat.S_ISDIR(entry.st_mode):
                self.download(sftp, source, target, hashes, root)
            elif stat.S_ISREG(entry.st_mode):
                expected = (hashes or {}).get(target.relative_to(root).as_posix())
                if expected and target.is_file() and file_digest(target) == expected:
                    continue
                temporary = target.with_name(target.name + ".download-" + uuid.uuid4().hex)
                try:
                    sftp.get(source, str(temporary))
                    if expected and file_digest(temporary) != expected:
                        raise ValueError("remote artifact hash mismatch during transfer")
                    temporary.replace(target)
                finally:
                    temporary.unlink(missing_ok=True)

    def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
        if self.recovery_only:
            raise ValueError("recovery-only executor cannot launch training")
        directory = Path(directory).resolve()
        if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
            raise ValueError("invalid remote job directory")
        _refuse_v2_directory(directory)
        # Fresh remote attempt namespace prevents stale completion markers after resume/retry.
        if launch_id is not None and not re.fullmatch(r"[0-9a-f]{32}", launch_id):
            raise ValueError("invalid persisted launch ID")
        remote = self.root + "/" + directory.name + "/" + (launch_id or uuid.uuid4().hex)
        receipt = {"remote_path": remote, "pod_id": self.pod.get("id") if self.pod else None,
                   "created": time.time()}
        if self.server_profile is not None:
            receipt = {"remote_path": remote, "created": time.time(),
                       "destination": self.server_profile.model_dump(mode="json")}
        (directory / "remote-attempt.json").write_text(json.dumps(receipt), encoding="utf-8")
        submitted = False
        finished = False
        try:
            with self.client.open_sftp() as sftp:
                self.upload(sftp, directory, remote)
                command = [self.remote_python, "-m", "tuning_factory.worker", remote, str(timeout)]
                environment = []
                if self.server_profile is not None:
                    environment.append("CUDA_VISIBLE_DEVICES=" + ",".join(self.server_facts.selected_gpu_uuids))
                if self.worker_lock_file:
                    environment.append("FACTORY_GPU_LOCK_FILE=" + self.worker_lock_file)
                if self.hf_home:
                    environment.append("HF_HOME=" + self.hf_home)
                if environment:
                    command = ["env", *environment, *command]
                if checkpoint:
                    command += ["--checkpoint", Path(checkpoint).resolve().relative_to(directory).as_posix()]
                # Worker remains alive on controller disconnect and enforces its own deadline.
                launch = "nohup " + shlex.join(command) + " > " + shlex.quote(remote + "/worker.log") + " 2>&1 < /dev/null &"
                submitted = True
                _, stdout, stderr = self.client.exec_command(launch, timeout=20)
                if stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError("remote worker launch failed: " + stderr.read().decode()[:200])
                deadline = time.monotonic() + timeout + 60
                cancellation_sent = False
                while True:
                    if canceled() and not cancellation_sent:
                        try:
                            with sftp.open(remote + "/cancel", "x"):
                                pass
                        except OSError:
                            # SFTP v3 may report existing-file errors without an errno.
                            # Confirm the competing signal exists; propagate transport errors.
                            sftp.lstat(remote + "/cancel")
                        cancellation_sent = True
                    try:
                        with sftp.open(remote + "/result.json") as f:
                            result = json.loads(f.read())
                        finished = True
                        break
                    except OSError as exc:
                        if exc.errno != 2:
                            raise
                    if time.monotonic() >= deadline:
                        raise ExecutionUncertain("remote worker deadline exceeded; reconcile the remote GPU host "
                                                 "and retain GPU lock")
                    time.sleep(1)
                hashes = self.save_remote_evidence(sftp, remote, directory, result)
                self.download(sftp, remote, directory, hashes)
                for name, sha in hashes.items():
                    target = (directory / name).resolve()
                    if not target.is_relative_to(directory) or file_digest(target) != sha:
                        raise ValueError("remote artifact hash mismatch")
                if result["status"] == "CANCELED":
                    from .training import CanceledError
                    raise CanceledError("remote training canceled")
                if result["status"] != "COMPLETED":
                    raise RuntimeError(result.get("error", "remote training failed"))
        except BaseException as exc:
            if submitted and not finished:
                raise ExecutionUncertain("remote execution uncertain; reconcile before releasing GPU lock") from exc
            raise
        finally:
            self.client.close()

    @staticmethod
    def save_remote_evidence(sftp, remote, directory, result):
        (directory / "remote-result.json").write_text(json.dumps(result), encoding="utf-8")
        with sftp.open(remote + "/artifacts.json") as stream:
            hashes = json.loads(stream.read())
        (directory / "remote-artifacts.json").write_text(json.dumps(hashes), encoding="utf-8")
        # Older workers included the signal while another writer could still change it.
        return {name: value for name, value in hashes.items() if name != "cancel"}

    def collect_result(self, directory):
        directory = Path(directory).resolve()
        record = json.loads((directory / "remote-attempt.json").read_text())
        self.validate_attempt_destination(record)
        remote = record["remote_path"]
        expected_prefix = self.root + "/" + directory.name + "/"
        if not remote.startswith(expected_prefix) or not re.fullmatch(r"[0-9a-f]{32}", remote[len(expected_prefix):]):
            raise ValueError("remote attempt path is outside the original job namespace")
        if self.pod and record.get("pod_id") != self.pod.get("id"):
            raise ValueError("reconciliation Pod differs from original execution")
        try:
            with self.client.open_sftp() as sftp:
                try:
                    with sftp.open(remote + "/result.json") as f:
                        result = json.loads(f.read())
                except OSError as exc:
                    if exc.errno == 2:
                        return None
                    raise
                with sftp.open(remote + "/manifest.json") as f:
                    recovered_manifest = json.loads(f.read())
                original = json.loads((directory / "manifest.json").read_text())
                if recovered_manifest != original:
                    raise ValueError("remote manifest differs from original job")
                hashes = self.save_remote_evidence(sftp, remote, directory, result)
                self.download(sftp, remote, directory, hashes)
                for name, expected in hashes.items():
                    path = (directory / name).resolve()
                    if not path.is_relative_to(directory) or file_digest(path) != expected:
                        raise ValueError("recovered artifact hash mismatch")
                return result
        finally:
            self.client.close()

    def validate_attempt_destination(self, record):
        if self.server_profile is not None:
            if record.get("destination") != self.server_profile.model_dump(mode="json"):
                raise ValueError("remote attempt destination differs from original execution")
        elif record.get("destination") is not None:
            raise ValueError("remote attempt destination differs from original execution")
