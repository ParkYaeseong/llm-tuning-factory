"""Infrastructure policy and execution adapters; no implicit paid actions."""
import json
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
from .registry import digest


class CapacityUnavailable(RuntimeError):
    pass


class ExecutionUncertain(RuntimeError):
    """Remote process termination could not be established; retain its GPU lease."""


class Policy(StrictModel):
    max_gpus: int = Field(default=1, ge=1, le=8)
    max_concurrent: int = Field(default=1, ge=1)
    max_timeout_seconds: int = Field(default=14400, ge=1)
    max_cost_usd: float = Field(default=5, gt=0)
    max_attempts: int = Field(default=2, ge=1, le=5)

    def check(self, config, dataset, execute=False, approved_transfer=False):
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
            if resource.hourly_cost_usd <= 0:
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
    def run(self, backend, directory, timeout, canceled, checkpoint=None): ...


class LocalExecutor(ExecutionBackend):
    def run(self, backend, directory, timeout, canceled, checkpoint=None):
        if checkpoint:
            backend.resume(directory, checkpoint, timeout, canceled)
        else:
            backend.train(directory, timeout, canceled)


class SSHExecutor(ExecutionBackend):
    """Existing Pod with a preinstalled factory image. Strict known_hosts verification."""
    remote = True

    def __init__(self, host, port, username, key_file, remote_root="/workspace/factory", client=None, pod=None):
        if not re.fullmatch(r"/[a-zA-Z0-9_/-]+", remote_root) or ".." in remote_root:
            raise ValueError("remote_root must be a safe absolute Linux path")
        self.client = client or paramiko.SSHClient()
        if client is None:
            self.client.load_system_host_keys()
            self.client.set_missing_host_key_policy(paramiko.RejectPolicy())
            self.client.connect(host, port=port, username=username, key_filename=str(key_file), timeout=20,
                                allow_agent=False, look_for_keys=False)
        self.root = remote_root
        self.pod = pod

    def validate_config(self, config):
        pod = self.pod
        resource = config.resource_profile
        if not pod:
            raise ValueError("live RunPod metadata required before transfer")
        if pod.get("gpu", {}).get("count") != resource.gpu_count:
            raise ValueError("claimed job GPU count differs from live Pod")
        if resource.gpu_type not in {pod.get("gpu", {}).get("id"), pod.get("gpu", {}).get("displayName")}:
            raise ValueError("claimed job GPU type differs from live Pod")
        if float(pod["costPerHr"]) > resource.hourly_cost_usd * resource.gpu_count:
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
            if path.is_symlink():
                raise ValueError("symlinks are not transferable")
            target = remote + "/" + path.relative_to(local).as_posix()
            if path.is_dir():
                self.mkdir(sftp, target)
            elif path.is_file():
                sftp.put(str(path), target)

    def download(self, sftp, remote, local):
        local.mkdir(parents=True, exist_ok=True)
        for entry in sftp.listdir_attr(remote):
            if entry.filename in {".", ".."} or "/" in entry.filename or "\\" in entry.filename:
                raise ValueError("unsafe remote artifact name")
            source, target = remote + "/" + entry.filename, local / entry.filename
            if stat.S_ISLNK(entry.st_mode):
                raise ValueError("remote symlink artifact rejected")
            if stat.S_ISDIR(entry.st_mode):
                self.download(sftp, source, target)
            elif stat.S_ISREG(entry.st_mode):
                sftp.get(source, str(target))

    def run(self, backend, directory, timeout, canceled, checkpoint=None):
        directory = Path(directory).resolve()
        if not re.fullmatch(r"[0-9a-f]{32}", directory.name):
            raise ValueError("invalid remote job directory")
        # Fresh remote attempt namespace prevents stale completion markers after resume/retry.
        remote = self.root + "/" + directory.name + "/" + uuid.uuid4().hex
        (directory / "remote-attempt.json").write_text(json.dumps({"remote_path": remote,
            "pod_id": self.pod.get("id") if self.pod else None, "created": time.time()}), encoding="utf-8")
        submitted = False
        finished = False
        try:
            with self.client.open_sftp() as sftp:
                self.upload(sftp, directory, remote)
                command = ["python", "-m", "tuning_factory.worker", remote, str(timeout)]
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
                        with sftp.open(remote + "/cancel", "w") as f:
                            f.write("cancel")
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
                        raise ExecutionUncertain("remote worker deadline exceeded; reconcile Pod and retain GPU lock")
                    time.sleep(1)
                self.download(sftp, remote, directory)
                hashes = json.loads((directory / "artifacts.json").read_text())
                for name, sha in hashes.items():
                    target = (directory / name).resolve()
                    if not target.is_relative_to(directory) or digest(target.read_bytes()) != sha:
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

    def collect_result(self, directory):
        directory = Path(directory).resolve()
        record = json.loads((directory / "remote-attempt.json").read_text())
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
                self.download(sftp, remote, directory)
                hashes = json.loads((directory / "artifacts.json").read_text())
                for name, expected in hashes.items():
                    path = (directory / name).resolve()
                    if not path.is_relative_to(directory) or digest(path.read_bytes()) != expected:
                        raise ValueError("recovered artifact hash mismatch")
                return result
        finally:
            self.client.close()
