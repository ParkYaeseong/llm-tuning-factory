"""Infrastructure policy and execution adapters; no implicit paid actions."""
import json
import math
import re
import shlex
import stat
import time
import uuid
from abc import ABC, abstractmethod
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

import httpx
import paramiko
from pydantic import Field

from .config import StrictModel
from .gpu_servers import SSHServerFacts, safe_remote_root, validate_server_binding
from .manifest import verify_manifest
from .model_artifacts import ARTIFACT_LAYOUT, ModelArtifactRef, check_link_free
from .registry import canonical, digest, file_digest
from .worker_protocol import COMMAND, RESULT, START_MARKER, OperationResult, WorkerRequest, is_v2_directory


class CapacityUnavailable(RuntimeError):
    pass


class ExecutionUncertain(RuntimeError):
    """Remote process termination could not be established; retain its GPU lease."""


HEX32 = re.compile(r"[0-9a-f]{32}")
ALREADY_STARTED = "remote launch already started for this attempt; reconcile instead of relaunching"
FOREIGN_RESULT = "remote result belongs to a different attempt"
CORRUPT_CACHE = "remote model cache entry failed verification; remove it after inspection"
V2_LOCAL_RECORDS = ("remote-attempt.json",)   # control-plane records in a v2 launch directory; never uploaded
PINNED_FILES = (COMMAND, "manifest.json", "model-inputs.json")   # come back from the host byte-identical
CACHE_COMMAND_TIMEOUT = 3600   # hashing a multi-GB cache entry; the check is read-only and launches nothing
_ARTIFACT_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")


def _refuse_v2_directory(directory: Path, action: str = "launch", entry: str = "run_operation") -> None:
    """The v1 paths never upload, launch or collect a v2 directory, so they never overwrite or trust its result."""
    if is_v2_directory(directory):
        raise ValueError(f"v2 operation directories {action} only through {entry}; refusing the v1 {action} path")


def _remote_exists(sftp, path: str) -> bool:
    try:
        sftp.lstat(path)
    except OSError as exc:
        if exc.errno == 2:
            return False
        raise
    return True


def _remote_regular(sftp, path: str) -> bool:
    try:
        return stat.S_ISREG(sftp.lstat(path).st_mode)
    except OSError as exc:
        if exc.errno == 2:
            return False
        raise


@dataclass(frozen=True)
class CachedTransfer:
    """One staged artifact that the executor caches on the GPU host before a v2 launch (7.5 builds it).

    ``local_location`` is the control-plane entry ``<store>/<artifact_id>/<inventory_sha256>`` holding
    ``files/``, ``descriptor.json`` and ``inventory.json``.
    """
    ref: ModelArtifactRef
    local_location: Path

    def checked(self) -> "CachedTransfer":
        ref = ModelArtifactRef.model_validate(self.ref)
        location = Path(self.local_location).absolute()
        check_link_free(location)
        if ((location.parent.name, location.name) != (ref.artifact_id, ref.inventory_sha256) or not location.is_dir()
                or {item.name for item in location.iterdir()} != ARTIFACT_LAYOUT):
            raise ValueError("transfer is not a control-plane model cache entry for its artifact")
        return CachedTransfer(ref=ref, local_location=location)


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

    def run_operation(self, job_directory, launch_directory, request, transfers, canceled):
        """Launch one v2 operation attempt (worker protocol 2); only a remote executor with a model cache can."""
        raise NotImplementedError("v2 operations require a remote executor with a model cache")


class LocalExecutor(ExecutionBackend):
    def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
        if checkpoint:
            backend.resume(directory, checkpoint, timeout, canceled)
        else:
            backend.train(directory, timeout, canceled)


class SSHExecutor(ExecutionBackend):
    """An existing Pod or a direct-SSH server with the factory runtime. Strict known_hosts verification.

    v1 jobs use ``run``/``collect_result``. v2 operations use ``run_operation``/``collect_operation_result``
    with ``model_cache_root``: a content-addressed cache on the GPU host whose entries are reused only after
    the worker's fixed-argv hash verification and are never deleted or replaced. The v2 methods leave the
    SSH connection open; its owner (``desktop_runtime.connection``) closes it.
    """
    remote = True

    def __init__(self, host, port, username, key_file, remote_root="/workspace/factory", client=None, pod=None,
                 remote_python="python", worker_lock_file=None, hf_home=None,
                 server_profile=None, server_facts=None, recovery_only=False, model_cache_root=None):
        if model_cache_root is not None:
            try:
                safe_remote_root(model_cache_root)
            except ValueError:
                raise ValueError("model_cache_root must be a safe absolute Linux directory below /") from None
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
        self.model_cache_root = model_cache_root

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

    def upload(self, sftp, local, remote, exclude=()):
        self.mkdir(sftp, remote)
        for path in local.rglob("*"):
            if path.parent == local and path.name in {
                "result.json", "result.tmp", "cancel", "remote-result.json", "remote-artifacts.json", START_MARKER,
                *exclude
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
                environment = self._environment()
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
                result = self._await_result(sftp, remote, timeout, canceled)
                finished = True
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

    def _environment(self):
        """Worker environment shared by v1 and v2 launches (selected GPUs, runtime lock, HF cache)."""
        environment = []
        if self.server_profile is not None:
            environment.append("CUDA_VISIBLE_DEVICES=" + ",".join(self.server_facts.selected_gpu_uuids))
        if self.worker_lock_file:
            environment.append("FACTORY_GPU_LOCK_FILE=" + self.worker_lock_file)
        if self.hf_home:
            environment.append("HF_HOME=" + self.hf_home)
        return environment

    def _await_result(self, sftp, remote, timeout, canceled):
        """Poll ``result.json`` and send cancellation once. The worker enforces the attempt deadline itself,
        but an uninterruptible step can overrun it: past timeout + 60 s the execution is uncertain (v1 and v2)."""
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
                    return json.loads(f.read())
            except OSError as exc:
                if exc.errno != 2:
                    raise
            if time.monotonic() >= deadline:
                raise ExecutionUncertain("remote worker deadline exceeded; reconcile the remote GPU host "
                                         "and retain GPU lock")
            time.sleep(1)

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
        _refuse_v2_directory(directory, "collect", "collect_operation_result")
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

    # -- v2 operations (worker protocol 2): one launch per attempt, hash-verified remote model cache ---

    def run_operation(self, job_directory, launch_directory, request, transfers, canceled):
        """Launch one v2 attempt at ``<root>/<job_id>/<launch_id>`` (the persisted launch ID, never a new one)
        and return its identity-checked result for any terminal status.

        Local checks come first (identities, command/manifest, staged transfers), then the attempt record.
        A remote start marker or result means the attempt already started: that raises ExecutionUncertain
        before anything else, so a used launch directory is reconciled. A launch directory holding weights or
        links is refused before any upload. Cache entries are reused only after remote verification, or
        published (verified, then ``mv -T``); the launch files are uploaded without start/result markers, and
        one worker launch appends to ``worker.log``. From the launch until a trusted result, any failure,
        including the timeout + 60 s deadline, is ExecutionUncertain: reconcile, never relaunch.
        """
        from .operation_runner import WEIGHT_SUFFIXES
        if self.recovery_only:
            raise ValueError("recovery-only executor cannot launch an operation")
        job, launch, request, command_sha256, kind = self._v2_attempt(job_directory, launch_directory, request)
        transfers = self._v2_transfers(launch, transfers)
        cache = self._cache_root()
        if self.hf_home:
            safe_remote_root(self.hf_home)
        remote = f"{self.root}/{job.name}/{request.launch_id}"
        record, stored = self._attempt_record(remote, request, command_sha256), launch / "remote-attempt.json"
        if stored.exists():
            if self._stored_record(stored) != record:
                raise ValueError("remote attempt record differs from the persisted launch identity")
        else:
            stored.write_text(json.dumps({**record, "created": time.time()}), encoding="utf-8")
        submitted = finished = False
        try:
            with self.client.open_sftp() as sftp:
                if self._remote_launch_state(sftp, remote):
                    raise ExecutionUncertain(ALREADY_STARTED)
                for path in launch.rglob("*"):
                    if path.is_symlink():
                        raise ValueError("symlinks are not transferable")
                    if path.suffix in WEIGHT_SUFFIXES:
                        raise ValueError("attempt directory must not contain model weights; stage them through the "
                                         "model cache")
                for root in (cache, self.hf_home):
                    if root:
                        self.mkdir(sftp, root)   # each component an ordinary directory: never under a link
                for transfer in transfers:
                    self._ensure_cached(sftp, transfer, request.launch_id)
                if self._remote_launch_state(sftp, remote):   # publishing can take long; check again
                    raise ExecutionUncertain(ALREADY_STARTED)
                self.upload(sftp, launch, remote, exclude=V2_LOCAL_RECORDS)
                command = ["env", *self._environment(), "FACTORY_MODEL_CACHE=" + cache, self.remote_python, "-m",
                           "tuning_factory.worker", remote, "--request", command_sha256]
                # Append, so a duplicate start (refused by the worker's O_EXCL marker) never truncates the log.
                launch_line = ("nohup " + shlex.join(command) + " >> " + shlex.quote(remote + "/worker.log")
                               + " 2>&1 < /dev/null &")
                submitted = True
                _, stdout, stderr = self.client.exec_command(launch_line, timeout=20)
                if stdout.channel.recv_exit_status() != 0:
                    raise RuntimeError("remote worker launch failed: " + stderr.read().decode()[:200])
                raw = self._await_result(sftp, remote, request.timeout_seconds, canceled)
                result = self._accept_result(raw, request, kind)
                finished = True
                self._collect_operation(sftp, remote, launch, raw, result)
                return result.model_dump(mode="json")
        except BaseException as exc:
            if submitted and not finished:
                raise ExecutionUncertain("remote execution uncertain; reconcile before releasing GPU lock") from exc
            raise

    def collect_operation_result(self, job_directory, launch_directory, request):
        """Collect a v2 attempt's result; never launches (recovery-safe). None while there is no result: a start
        marker alone means the worker may still be running, so the attempt stays uncertain."""
        job, launch, request, command_sha256, kind = self._v2_attempt(job_directory, launch_directory, request)
        remote = f"{self.root}/{job.name}/{request.launch_id}"
        record = self._stored_record(launch / "remote-attempt.json")
        self.validate_attempt_destination(record)
        if record != self._attempt_record(remote, request, command_sha256):
            raise ValueError("remote attempt record differs from the persisted launch identity")
        with self.client.open_sftp() as sftp:
            try:
                with sftp.open(remote + "/" + RESULT, "rb") as stream:
                    raw = json.loads(stream.read())
            except OSError as exc:
                if exc.errno == 2:
                    return None
                raise
            result = self._accept_result(raw, request, kind)
            self._collect_operation(sftp, remote, launch, raw, result)
            return result.model_dump(mode="json")

    def publish_cached_artifact(self, transfer, *, source_remote_dir=None, launch_id=None):
        """Make one artifact a verified remote cache entry; a verified entry is reused, none is ever replaced.

        ``source_remote_dir`` (a finished launch's remote output, after 7.5 froze it locally) seeds the entry
        on the host, so a destination never re-uploads its own outputs; that launch names the incoming
        directory. Without it the control-plane copy is uploaded under ``launch_id``'s incoming directory.
        """
        if self.recovery_only:
            raise ValueError("recovery-only executor cannot publish model cache entries")
        if not isinstance(transfer, CachedTransfer):
            raise TypeError("model transfers must be CachedTransfer entries")
        if source_remote_dir is not None:
            match = re.fullmatch(re.escape(self.root) + r"/[0-9a-f]{32}/([0-9a-f]{32})"
                                 r"(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*", source_remote_dir)
            if match is None:
                raise ValueError("source remote directory is outside this executor's launch namespace")
            if launch_id not in (None, match.group(1)):
                raise ValueError("source remote directory belongs to another launch identity")
            launch_id = match.group(1)
        if not isinstance(launch_id, str) or not HEX32.fullmatch(launch_id):
            raise ValueError("publishing a model cache entry requires its 32-hex launch identity")
        with self.client.open_sftp() as sftp:
            self._ensure_cached(sftp, transfer, launch_id, source_remote_dir)

    def _v2_attempt(self, job_directory, launch_directory, request):
        """Persisted identities and local launch files: (job, launch, request, command sha256, operation kind)."""
        request = WorkerRequest.model_validate(request.model_dump(mode="json") if hasattr(request, "model_dump")
                                               else request)
        job, launch = Path(job_directory).resolve(), Path(launch_directory).resolve()
        if not HEX32.fullmatch(job.name):
            raise ValueError("invalid remote job directory")
        if launch.name != request.launch_id or launch == job or not launch.is_relative_to(job):
            raise ValueError("launch directory differs from the persisted launch identity")
        command = (launch / COMMAND).read_bytes()
        if command != canonical(request.model_dump(mode="json")):
            raise ValueError("command.json differs from the worker request")
        manifest = json.loads((launch / "manifest.json").read_bytes())
        verify_manifest(manifest)
        operation = manifest.get("operation")
        if (manifest.get("schema_version") != 2 or manifest.get("sha256") != request.manifest_sha256
                or not isinstance(operation, dict) or digest(canonical(operation)) != request.operation_sha256):
            raise ValueError("manifest.json differs from the worker request")
        return job, launch, request, digest(command), operation.get("kind")

    @staticmethod
    def _v2_transfers(launch, transfers):
        """Exactly the staged artifacts of ``model-inputs.json``; HF roots are resolved on the worker instead."""
        from .operation_runner import MODEL_INPUTS, ResolvedModelInputs
        staged = ResolvedModelInputs.model_validate_json((launch / MODEL_INPUTS).read_bytes()).transfers
        checked = []
        for transfer in transfers:
            if not isinstance(transfer, CachedTransfer):
                raise TypeError("model transfers must be CachedTransfer entries")
            checked.append(transfer.checked())
        if tuple(sorted((item.ref for item in checked), key=lambda ref: (ref.artifact_id, ref.inventory_sha256))
                 ) != staged:
            raise ValueError("model transfers differ from the staged model inputs")
        return tuple(checked)

    def _attempt_record(self, remote, request, command_sha256):
        record = {"remote_path": remote, "pod_id": self.pod.get("id") if self.pod else None}
        if self.server_profile is not None:
            record = {"remote_path": remote, "destination": self.server_profile.model_dump(mode="json")}
        return {**record, "protocol": 2, "attempt_id": request.attempt_id, "launch_id": request.launch_id,
                "command_sha256": command_sha256}

    @staticmethod
    def _stored_record(path):
        return {key: value for key, value in json.loads(Path(path).read_bytes()).items() if key != "created"}

    def _remote_launch_state(self, sftp, remote):
        """True when a start marker or a result exists: the attempt started and is reconciled, never relaunched.
        Both are only existence facts here; ``start.json`` claims nothing that is trusted."""
        return any(_remote_exists(sftp, remote + "/" + name) for name in (START_MARKER, RESULT))

    @staticmethod
    def _accept_result(raw, request, kind):
        """Trust only ``result.json``, and only when it names this attempt: always the launch, and the attempt,
        manifest, operation and kind exactly when COMPLETED (other results report only verified identities)."""
        try:
            result = OperationResult.model_validate(raw)
        except ValueError as exc:
            raise ValueError("remote result is not a v2 operation result") from exc
        expected = {"attempt_id": request.attempt_id, "manifest_sha256": request.manifest_sha256,
                    "operation_sha256": request.operation_sha256, "kind": kind}
        if result.launch_id != request.launch_id or any(
                getattr(result, name) != value and (result.status == "COMPLETED" or getattr(result, name) is not None)
                for name, value in expected.items()):
            raise ValueError(FOREIGN_RESULT)
        return result

    def _collect_operation(self, sftp, remote, launch, raw, result):
        """Transfer a trusted result's files: the pinned launch files are unchanged and every file is verified."""
        for name, label in ((COMMAND, "command"), ("manifest.json", "manifest")):
            with sftp.open(remote + "/" + name, "rb") as stream:
                if stream.read() != (launch / name).read_bytes():
                    raise ValueError(f"remote {label} differs from the original attempt")
        hashes = self.save_remote_evidence(sftp, remote, launch, raw)
        # So a download can never replace the attempt's own command, manifest or staged model inputs.
        if any(hashes.get(name) != file_digest(launch / name) for name in PINNED_FILES):
            raise ValueError("remote artifact inventory differs from the original attempt files")
        self.download(sftp, remote, launch, hashes)
        for name, expected in hashes.items():
            path = (launch / name).resolve()
            if not path.is_relative_to(launch) or file_digest(path) != expected:
                raise ValueError("remote artifact hash mismatch")
        for name, expected in result.evidence.items():
            path = (launch / name).resolve()
            if not path.is_relative_to(launch) or not path.is_file() or file_digest(path) != expected:
                raise ValueError("remote result evidence differs from the transferred files")

    def _cache_root(self):
        if self.model_cache_root is None:
            raise ValueError("v2 operations require a configured remote model cache")
        return self.model_cache_root

    def _status(self, argv, timeout=CACHE_COMMAND_TIMEOUT):
        """Run one fixed argv (quoted with shlex.join, never a free-form command) and return its exit status."""
        _, stdout, _ = self.client.exec_command(shlex.join(argv), timeout=timeout)
        channel = stdout.channel
        deadline = time.monotonic() + timeout
        while not channel.exit_status_ready():
            if time.monotonic() >= deadline:
                channel.close()
                raise TimeoutError("remote model cache command timed out; inspect the remote GPU host")
            time.sleep(1)
        return channel.recv_exit_status()

    def _verified(self, root, ref):
        """The worker's fixed-argv cache check of ``<root>/<artifact_id>/<inventory_sha256>``; it never deletes.
        Exit 1 is a failed verification; any other failure is not evidence about the entry."""
        status = self._status([self.remote_python, "-m", "tuning_factory.worker", "--verify-model-cache", root,
                               ref.artifact_id, ref.inventory_sha256])
        if status not in (0, 1):
            raise RuntimeError(f"remote model cache check could not run (exit {status}); prepare the runtime first")
        return status == 0

    def _ensure_cached(self, sftp, transfer, key, source=None):
        """Reuse a verified entry, or publish one: fill ``.incoming/<launch_id>``, verify it remotely, then rename
        it into place with ``mv -T`` (real directories, no link, never replacing a non-empty entry)."""
        transfer = transfer.checked()
        ref, cache = transfer.ref, self._cache_root()
        final = f"{cache}/{ref.artifact_id}/{ref.inventory_sha256}"
        self.mkdir(sftp, cache)
        if _remote_exists(sftp, final):
            if not self._verified(cache, ref):
                raise ValueError(CORRUPT_CACHE)   # fail closed; an operator inspects and removes it
            return
        staging = f"{cache}/.incoming/{key}"
        entry = f"{staging}/{ref.artifact_id}/{ref.inventory_sha256}"
        self.mkdir(sftp, f"{staging}/{ref.artifact_id}")
        try:
            sftp.mkdir(entry)
        except OSError:
            if _remote_exists(sftp, entry):
                raise ValueError("remote incoming model cache entry already exists; inspect and remove it "
                                 "manually") from None
            raise
        if source is None:
            self.upload(sftp, transfer.local_location, entry)
        else:
            self._copy_remote_output(sftp, transfer.local_location, source, entry)
        if not self._verified(staging, ref):
            raise ValueError("uploaded model cache entry failed remote verification; the incoming copy is kept "
                             "for inspection")
        self.mkdir(sftp, f"{cache}/{ref.artifact_id}")
        if self._status(["mv", "-T", "--", entry, final], 60) != 0:
            # A concurrent publisher may have won: its entry must verify. Ours is left for manual cleanup.
            if not _remote_exists(sftp, final):
                raise RuntimeError("remote model cache publish failed; the incoming copy is kept for inspection")
            if not self._verified(cache, ref):
                raise ValueError(CORRUPT_CACHE)

    def _copy_remote_output(self, sftp, local, source, entry):
        """Seed an incoming entry from a finished launch's remote output: payload files present there are copied
        on the host (fixed argv); metadata and anything the output lacks come from the control-plane entry."""
        for name in ("descriptor.json", "inventory.json"):
            sftp.put(str(local / name), entry + "/" + name)
        self.mkdir(sftp, entry + "/files")
        for item in json.loads((local / "inventory.json").read_bytes()):
            relative = item.get("path") if isinstance(item, dict) else None
            if not isinstance(relative, str) or not _ARTIFACT_PATH.fullmatch(relative):
                raise ValueError("model artifact path cannot be copied safely on the remote host")
            target = entry + "/files/" + relative
            self.mkdir(sftp, target.rsplit("/", 1)[0])
            origin = source + "/" + relative
            if _remote_regular(sftp, origin):
                if self._status(["cp", "-P", "--", origin, target]) != 0:
                    raise RuntimeError("remote model cache copy failed")
            else:
                sftp.put(str(local / "files" / relative), target)
