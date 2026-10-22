"""Infrastructure policy and execution adapters; no implicit paid actions."""
import contextlib
import json
import math
import os
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
from .manifest import verify_manifest
from .model_artifacts import WINDOWS_DEVICE_NAME
from .registry import canonical, digest, file_digest
from .remote_model_cache import CachedTransfer, ensure_cached, remote_exists  # CachedTransfer is re-exported
from .worker_protocol import COMMAND, RESULT, START_MARKER, OperationResult, WorkerRequest, is_v2_directory


class CapacityUnavailable(RuntimeError):
    pass


class ExecutionUncertain(RuntimeError):
    """Remote process termination could not be established; retain its GPU lease."""


HEX32 = re.compile(r"[0-9a-f]{32}")
ALREADY_STARTED = "remote launch already started for this attempt; reconcile instead of relaunching"
LAUNCH_DIRECTORY_EXISTS = "remote launch directory already exists for this attempt; reconcile instead of relaunching"
FOREIGN_RESULT = "remote result belongs to a different attempt"
COLLECTION_INTERRUPTED = ("remote result collection was interrupted after the host finished; reconcile the same "
                          "launch")
# After the host finished, these say the result's files are not as listed (a missing or misplaced file, an existing
# name): deterministic content errors, never retried, unlike a sharing violation, a full disk, a permission or a busy
# database, which an operator can fix locally (11.5 re-review M2).
CONTENT_OS_ERRORS = (FileNotFoundError, NotADirectoryError, IsADirectoryError, FileExistsError)
# Windows maps WinError 206 (file name too long) and 53/67 (network path or name not found) to ENOENT, so CPython
# raises FileNotFoundError for them: local and fixable (a shorter state path, the share back), never content. So is
# WinError 3 (path not found) for a path of MAX_PATH or more while long paths are off (11.5 final re-review; with
# them on, or for a ``\\?\`` name, the length is no reason). Only ``os.*`` functions set
# ``winerror``: ``open()`` (paramiko's download, the freeze copy) raises the same failures as a bare FileNotFoundError,
# so on Windows its path decides (``_windows_path_unreachable``).
TRANSIENT_WINERRORS = frozenset({206, 53, 67})
MAX_PATH = 260
WINDOWS = os.name == "nt"
_stat = os.stat


def _long_paths_enabled() -> bool:
    """Whether this process may use paths of MAX_PATH or more (Windows' LongPathsEnabled with a long-path-aware
    executable, which python.exe is); then a long path is no reason for a missing file."""
    if not WINDOWS:
        return False
    try:
        import ctypes
        return bool(ctypes.WinDLL("ntdll").RtlAreLongPathsEnabled())
    except (AttributeError, OSError):
        return False


LONG_PATHS = _long_paths_enabled()


def _too_long(name: str) -> bool:
    """A path Windows refuses for its length: MAX_PATH or more while long paths are off, never a ``\\\\?\\`` name."""
    return not LONG_PATHS and len(name) >= MAX_PATH and not name.startswith("\\\\?\\")


def _windows_path_unreachable(exc: OSError) -> bool:
    """A bare FileNotFoundError from ``open()`` on Windows whose path is too long (``_too_long``) or whose folder cannot
    be reached (its stat fails with WinError 53/67: the network share is gone). Never raises: it runs inside the
    runner's exception handlers."""
    try:
        names = [os.fsdecode(name) for name in (exc.filename, exc.filename2)
                 if isinstance(name, (str, bytes, os.PathLike))]
        # A ``\\?\`` name is already absolute (Windows keeps it as it is; elsewhere abspath would prefix it).
        names = [name if name.startswith("\\\\?\\") else os.path.abspath(name) for name in names]
    except (ValueError, TypeError):
        return False
    if any(_too_long(name) for name in names):
        return True
    for name in names:
        try:
            _stat(os.path.dirname(name))
        except OSError as error:
            if getattr(error, "winerror", None) in TRANSIENT_WINERRORS:
                return True
        except (ValueError, TypeError):
            pass
    return False


def content_os_error(exc: BaseException) -> bool:
    """A ``CONTENT_OS_ERRORS`` file error that says the result's files are not as listed; not a Windows path-length or
    network-path error that only looks like a missing file."""
    if not isinstance(exc, CONTENT_OS_ERRORS):
        return False
    winerror = getattr(exc, "winerror", None)
    if winerror in TRANSIENT_WINERRORS:
        return False
    if winerror == 3 and any(_too_long(str(name)) for name in (exc.filename, exc.filename2) if name):
        return False
    # Windows raises a path too long or a gone share as ENOENT only, so only a bare FileNotFoundError is judged.
    return not (winerror is None and WINDOWS and isinstance(exc, FileNotFoundError) and _windows_path_unreachable(exc))


def transient_error(exc: BaseException) -> bool:
    """Whether a failure after an accepted result leaves the attempt uncertain (collected and frozen again): any
    ``Exception`` but a ``ValueError`` (tamper, foreign or invalid content) or a content file error
    (``content_os_error``)."""
    return isinstance(exc, Exception) and not isinstance(exc, ValueError) and not content_os_error(exc)


INJECTED_CONNECTION = "v2 operations require an injected SSH connection owned by desktop_runtime.connection()"
# Control-plane records of a v2 launch directory: never uploaded, never taken from the host by a download.
V2_CONTROL_RECORDS = frozenset({"result.json", "result.tmp", "remote-attempt.json", "remote-attempt.json.tmp",
                                "remote-result.json", "remote-result.json.tmp", "remote-artifacts.json",
                                "remote-artifacts.json.tmp"})
# Top-level names a v2 upload never sends: control-plane records, markers, signals and worker-written files.
V2_UPLOAD_EXCLUDED = V2_CONTROL_RECORDS | {"cancel", START_MARKER, "artifacts.json", "worker.log"}
REQUIRED_UPLOADS = (COMMAND, "manifest.json", "model-inputs.json")
# Names Windows cannot hold as written: reserved characters and control codes (``:`` also selects a drive or
# an alternate data stream), device names, and a trailing dot or space (Win32 strips both, aliasing the name).
_WINDOWS_UNSAFE = re.compile(r'[<>:"/\\|?*\x00-\x1f]|[. ]$')
_DEVICE_NAME = WINDOWS_DEVICE_NAME   # one rule with artifact paths (11.5 R1 m9)
# The shape of an NTFS 8.3 short name (``REMOTE~1.JSO``), which can open an existing long-named file.
_SHORT_NAME = re.compile(r"~[0-9]")


def _refuse_v2_directory(directory: Path, action: str = "launch", entry: str = "run_operation") -> None:
    """The v1 paths never upload, launch or collect a v2 directory, so they never overwrite or trust its result."""
    if is_v2_directory(directory):
        raise ValueError(f"v2 operation directories {action} only through {entry}; refusing the v1 {action} path")


def _write_json_atomic(path: Path, value) -> None:
    """Write a control-plane record via ``<name>.tmp`` and replace, so a crash never leaves a partial record."""
    temporary = path.with_name(path.name + ".tmp")
    with temporary.open("w", encoding="utf-8") as stream:
        json.dump(value, stream)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(path)


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
        # v2 methods only run on an injected connection whose owner (desktop_runtime.connection) closes it.
        self.connection_injected = client is not None
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
        """A v1 job: its per-GPU price times its GPU count is the hourly budget."""
        resource = config.resource_profile
        self.validate_budget(resource.gpu_count, resource.gpu_type, resource.hourly_cost_usd * resource.gpu_count)

    def validate_budget(self, gpu_count, gpu_type, total_hourly_cost_usd):
        """The live destination against a GPU selection and a total hourly budget. A v2 operation passes its pinned
        total directly: split per GPU and multiplied back it can round below itself (11.5 R1 I-4)."""
        if self.server_profile is not None:
            validate_server_binding(self.server_profile, self.server_facts)
            self.server_facts.validate_budget(gpu_count, gpu_type, total_hourly_cost_usd)
            return
        pod = self.pod
        if not pod:
            raise ValueError("live RunPod metadata required before transfer")
        if pod.get("gpu", {}).get("count") != gpu_count:
            raise ValueError("claimed job GPU count differs from live Pod")
        if gpu_type not in {pod.get("gpu", {}).get("id"), pod.get("gpu", {}).get("displayName")}:
            raise ValueError("claimed job GPU type differs from live Pod")
        try:
            price = float(pod["costPerHr"])
        except (KeyError, ValueError, TypeError):
            raise ValueError("positive finite live RunPod API price required") from None
        if not math.isfinite(price) or price <= 0:
            raise ValueError("positive finite live RunPod API price required")
        if price > total_hourly_cost_usd:
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

    def download(self, sftp, remote, local, hashes=None, root=None, skip=(), protected=None):
        """Hash-verified copy of a remote tree into ``local``, safe on a case-insensitive Windows filesystem.

        Every name of a remote directory is checked before anything in it is written: no separator, ``:``,
        reserved character, device name or trailing dot/space, and no two names equal under ``casefold``
        (NTFS would alias them). At the top level ``cancel`` and ``skip`` (control-plane records) are never
        taken from the host. ``protected`` (relative path -> SHA256 of what the control plane uploaded) is
        never replaced; the caller checks the host's inventory against it first. A name equal to a skipped
        or protected name only under ``casefold`` is refused. A name with an 8.3 short-name shape (``~<digit>``)
        is refused, and so is any name whose existing local target resolves to a different canonical name
        (NTFS short names, case or trailing dot/space aliases of an existing file or directory).
        """
        root = root or local
        local.mkdir(parents=True, exist_ok=True)
        listing = sftp.listdir_attr(remote)
        folded = [entry.filename.casefold() for entry in listing]
        if len(set(folded)) != len(folded):
            raise ValueError("remote artifact names collide on a case-insensitive filesystem")
        skipped = {name.casefold(): name for name in ("cancel", *skip)} if local == root else {}
        guarded = {path.casefold(): path for path in (protected or {})}
        canonical_root = Path(root).resolve()
        entries = []
        for entry in listing:
            name = entry.filename
            if name in {"", ".", ".."} or _WINDOWS_UNSAFE.search(name) or _DEVICE_NAME.fullmatch(name):
                raise ValueError("unsafe remote artifact name")
            if _SHORT_NAME.search(name):
                raise ValueError("remote artifact name has the shape of a Windows short name (8.3) and could alias a "
                                 "local file")
            if name.casefold() in skipped:
                if skipped[name.casefold()] != name:
                    raise ValueError("remote artifact name aliases a control-plane record")
                continue  # Mutable control signal or a control-plane record, never a model artifact.
            target = local / name
            canonical = target.resolve()
            if not canonical.is_relative_to(canonical_root):
                raise ValueError("unsafe remote artifact name")
            relative = target.relative_to(root).as_posix()
            if relative.casefold() in guarded:
                if guarded[relative.casefold()] != relative or not stat.S_ISREG(entry.st_mode):
                    raise ValueError("remote artifact name aliases an uploaded control-plane input")
                continue  # An uploaded input is never replaced by host bytes.
            # An existing target resolves to its canonical long name and case, so a short (8.3), case or
            # trailing-dot/space alias of any existing local file or directory differs here.
            if os.path.lexists(target) and canonical.relative_to(canonical_root).as_posix() != relative:
                raise ValueError("remote artifact name aliases an existing local file")
            if stat.S_ISLNK(entry.st_mode):
                raise ValueError("remote symlink artifact rejected")
            entries.append((entry, target))
        # Preserve small top-level evidence before descending into large checkpoints.
        for entry, target in sorted(entries, key=lambda item: (stat.S_ISDIR(item[0].st_mode), item[0].filename)):
            source = remote + "/" + entry.filename
            if stat.S_ISDIR(entry.st_mode):
                self.download(sftp, source, target, hashes, root, skip, protected)
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
                self._send_cancel(sftp, remote)
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
    def _send_cancel(sftp, remote):
        """Create the worker's ``cancel`` marker; an existing one is the same signal."""
        try:
            with sftp.open(remote + "/cancel", "x"):
                pass
        except OSError:
            # SFTP v3 may report existing-file errors without an errno.
            # Confirm the competing signal exists; propagate transport errors.
            sftp.lstat(remote + "/cancel")

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
        and return its identity-checked result for any terminal status. Callers use this returned result
        (also kept as ``remote-result.json``); the host's ``result.json`` is never downloaded.

        Local checks come first (connection, identities, command/manifest, staged transfers), then the
        attempt record. A remote start marker or result means the attempt already started: that raises
        ExecutionUncertain before anything else, so a used launch directory is reconciled. A launch directory
        holding weights or links is refused before any upload. Cache entries are reused only after remote
        verification, or published (``remote_model_cache.ensure_cached``). The remote launch directory is
        created exclusively (an existing one is uncertain, e.g. another controller), the launch files are
        uploaded without start/result markers or control-plane records, and one worker launch appends to
        ``worker.log``. From the launch until a trusted result, any failure, including the timeout + 60 s
        deadline, is ExecutionUncertain: reconcile, never relaunch.
        """
        from .operation_runner import WEIGHT_SUFFIXES
        if self.recovery_only:
            raise ValueError("recovery-only executor cannot launch an operation")
        self._require_injected_connection()
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
            # The inventory of what this upload sends: host bytes may never replace these files locally.
            _write_json_atomic(stored, {**record, "uploaded": self._uploaded_inventory(launch),
                                        "created": time.time()})
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
                    ensure_cached(self, sftp, transfer, request.launch_id)
                self.mkdir(sftp, remote.rsplit("/", 1)[0])
                try:
                    sftp.mkdir(remote)   # exclusive: of two controllers, only one ever uploads and launches
                except OSError:
                    if remote_exists(sftp, remote):
                        raise ExecutionUncertain(LAUNCH_DIRECTORY_EXISTS) from None
                    raise
                self.upload(sftp, launch, remote, exclude=V2_UPLOAD_EXCLUDED)
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
                try:
                    self._collect_operation(sftp, remote, launch, raw, result)
                except Exception as exc:
                    if not transient_error(exc):
                        raise   # content: a tampered, foreign, invalid or missing transfer is final
                    # The host finished, but its files did not all arrive (a transport or local disk error): the
                    # attempt stays uncertain and reconcile collects the same launch again (11.5 R1 I-3).
                    raise ExecutionUncertain(COLLECTION_INTERRUPTED) from exc
                return result.model_dump(mode="json")
        except BaseException as exc:
            if submitted and not finished:
                raise ExecutionUncertain("remote execution uncertain; reconcile before releasing GPU lock") from exc
            raise

    def collect_operation_result(self, job_directory, launch_directory, request, *, cancel=False):
        """Collect a v2 attempt's result; never launches (recovery-safe). None while there is no result: a start
        marker alone means the worker may still be running, so the attempt stays uncertain. With ``cancel`` (the
        attempt's job or run was canceled) that worker also gets the ``cancel`` marker, as ``_await_result`` sends
        it (11.5 R1 I-1); best effort, since the launch directory may never have been created. Like
        ``run_operation`` it returns the trusted result and never downloads the host's ``result.json``."""
        self._require_injected_connection()
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
                    if cancel:
                        with contextlib.suppress(OSError):
                            self._send_cancel(sftp, remote)
                    return None
                raise
            result = self._accept_result(raw, request, kind)
            self._collect_operation(sftp, remote, launch, raw, result)
            return result.model_dump(mode="json")

    def publish_cached_artifact(self, transfer, *, source_remote_dir=None, launch_id=None):
        """Make one artifact a verified remote cache entry; a verified entry is reused, none is ever replaced.

        ``source_remote_dir`` (a finished launch's remote output, after 7.5 froze it locally) lets weight payload
        files with the frozen size be copied on the host; metadata, configs and tokenizer files are uploaded
        from the control plane (see ``remote_model_cache.ensure_cached``). That launch, or ``launch_id``, names
        the incoming slots; a failed publish leaves its slot for inspection and never blocks a retry.
        Everything is validated before an SFTP session is opened.
        """
        if self.recovery_only:
            raise ValueError("recovery-only executor cannot publish model cache entries")
        self._require_injected_connection()
        if not isinstance(transfer, CachedTransfer):
            raise TypeError("model transfers must be CachedTransfer entries")
        self._cache_root()
        transfer = transfer.checked()   # layout and every inventory path, before any remote change
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
            ensure_cached(self, sftp, transfer, launch_id, source_remote_dir)

    def _require_injected_connection(self):
        if not self.connection_injected:
            raise ValueError(INJECTED_CONNECTION)

    def _cache_root(self):
        if self.model_cache_root is None:
            raise ValueError("v2 operations require a configured remote model cache")
        return self.model_cache_root

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
        return {key: value for key, value in json.loads(Path(path).read_bytes()).items()
                if key not in {"created", "uploaded"}}

    @staticmethod
    def _uploaded_inventory(launch):
        """``relative path -> SHA256`` of every file the v2 upload sends (the same exclusions as ``upload``)."""
        return {path.relative_to(launch).as_posix(): file_digest(path) for path in sorted(launch.rglob("*"))
                if path.is_file() and not path.is_symlink()
                and not (path.parent == launch and path.name in V2_UPLOAD_EXCLUDED)}

    @staticmethod
    def _recorded_uploads(launch):
        uploaded = json.loads((launch / "remote-attempt.json").read_bytes()).get("uploaded")
        if (not isinstance(uploaded, dict) or any(name not in uploaded for name in REQUIRED_UPLOADS)
                or any(not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value)
                       for value in uploaded.values())):
            raise ValueError("remote attempt record lacks the inventory of uploaded launch inputs")
        return uploaded

    def _remote_launch_state(self, sftp, remote):
        """True when a start marker or a result exists: the attempt started and is reconciled, never relaunched.
        Both are only existence facts here; ``start.json`` claims nothing that is trusted."""
        return any(remote_exists(sftp, remote + "/" + name) for name in (START_MARKER, RESULT))

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
        """Transfer a trusted result's files: the pinned launch files are unchanged, control-plane records are
        never taken from the host, every file is hash-verified, and the evidence records are written last."""
        for name, label in ((COMMAND, "command"), ("manifest.json", "manifest")):
            with sftp.open(remote + "/" + name, "rb") as stream:
                if stream.read() != (launch / name).read_bytes():
                    raise ValueError(f"remote {label} differs from the original attempt")
        with sftp.open(remote + "/artifacts.json", "rb") as stream:
            inventory = json.loads(stream.read())
        if not isinstance(inventory, dict):
            raise ValueError("remote artifact inventory is invalid")
        hashes = {name: value for name, value in inventory.items() if name != "cancel"}
        # Every uploaded input (command, manifest, model inputs, YAML, data) must come back unchanged, and the
        # download never replaces it, so host bytes can never stand in for a control-plane input.
        uploaded = self._recorded_uploads(launch)
        if any(hashes.get(name) != sha256 for name, sha256 in uploaded.items()):
            raise ValueError("remote artifact inventory differs from the original attempt files")
        self.download(sftp, remote, launch, hashes, skip=V2_CONTROL_RECORDS, protected=uploaded)
        for name, expected in hashes.items():
            path = (launch / name).resolve()
            if not path.is_relative_to(launch) or file_digest(path) != expected:
                raise ValueError("remote artifact hash mismatch")
        for name, expected in result.evidence.items():
            path = (launch / name).resolve()
            if not path.is_relative_to(launch) or not path.is_file() or file_digest(path) != expected:
                raise ValueError("remote result evidence differs from the transferred files")
        _write_json_atomic(launch / "remote-artifacts.json", inventory)
        _write_json_atomic(launch / "remote-result.json", raw)
