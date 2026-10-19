"""Existing-Pod desktop adapter. Never starts/stops Pods or changes global Python."""
import base64
import hashlib
import hmac
import json
import re
import shlex
import time
from contextlib import contextmanager
from pathlib import Path

import paramiko

from .execution import ExecutionUncertain, RunPodController, SSHExecutor
from .gpu_servers import (
    ServerApprovalReceipt,
    SSHServerFacts,
    SSHServerIdentity,
    SSHServerInspection,
    probe_server,
    probe_server_identity,
    server_settings,
    validate_recovery_identity,
)
from .manifest import source_hash
from .runtime_profiles import engine_profile

REMOTE = "/workspace/factory-desktop"


class RemoteCommandFailed(ValueError):
    """The remote command returned an observed nonzero exit status."""


class PinnedHostKey(paramiko.MissingHostKeyPolicy):
    def __init__(self, fingerprint):
        self.fingerprint = fingerprint

    def missing_host_key(self, client, hostname, key):
        observed = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
        if not hmac.compare_digest(observed, self.fingerprint):
            raise paramiko.SSHException("SSH host fingerprint does not match the independently verified pin")


def validate_pod(pod):
    if pod.get("desiredStatus") != "RUNNING":
        raise ValueError("Pod must already be running; start it separately")
    if not pod.get("publicIp") or not pod.get("portMappings", {}).get("22"):
        raise ValueError("Pod public TCP SSH mapping required")
    if not (pod.get("networkVolume") or {}).get("id") or pod.get("volumeMountPath") != "/workspace":
        raise ValueError("persistent network volume at /workspace required")


@contextmanager
def connection(settings, *, inspection_only=False, recovery=False):
    """SSH server modes: launch facts (default), read-only inspection, or recovery identity only."""
    if settings.get("provider", "existing_runpod") == "ssh_server":
        config = server_settings(settings)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHostKey(config.ssh_fingerprint))
        try:
            client.connect(config.ssh_host, port=config.ssh_port, username=config.ssh_username,
                           key_filename=str(config.ssh_key_file), allow_agent=False, look_for_keys=False,
                           timeout=20, banner_timeout=20, auth_timeout=20)
            if recovery:
                facts = probe_server_identity(client, config, command)
            elif inspection_only:
                facts = probe_server(client, config, command, inspection_only=True)
            else:
                facts = probe_server(client, config, command)
            yield facts, client
        finally:
            client.close()
        return
    if settings.get("provider") not in (None, "", "existing_runpod"):
        raise ValueError("unsupported execution provider")
    pod = RunPodController(settings["RUNPOD_API_KEY"]).status(settings["pod_id"])
    validate_pod(pod)
    fingerprint = settings.get("ssh_fingerprint", "")
    if not re.fullmatch(r"SHA256:[A-Za-z0-9+/]{43}", fingerprint):
        raise ValueError("independently verified SSH SHA256 fingerprint required")
    client = paramiko.SSHClient()
    client.set_missing_host_key_policy(PinnedHostKey(fingerprint))
    try:
        client.connect(pod["publicIp"], port=int(pod["portMappings"]["22"]), username="root",
                       key_filename=settings["ssh_key_file"], allow_agent=False, look_for_keys=False,
                       timeout=20, banner_timeout=20, auth_timeout=20)
        yield pod, client
    finally:
        client.close()


def command(client, argv, timeout=60):
    _, stdout, _ = client.exec_command(shlex.join(argv), timeout=timeout)
    channel = stdout.channel
    channel.set_combine_stderr(True)
    deadline, output = time.monotonic() + timeout, bytearray()
    while True:
        while channel.recv_ready():
            output.extend(channel.recv(32768))
            if len(output) > 128000:
                del output[:-128000]
        if channel.exit_status_ready() and not channel.recv_ready():
            break
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError("remote operation timed out; inspect Pod before retrying preparation")
        time.sleep(.1)
    if channel.recv_exit_status():
        raise RemoteCommandFailed("GPU runtime command failed. " + output.decode("utf-8", errors="replace")[-3000:])
    return output.decode("utf-8", errors="replace")


def runtime_path(training_stage="sft", remote_root=REMOTE):
    profile = engine_profile(training_stage)
    return remote_root + "/runtime/" + source_hash()[:16] + ("-ppo" if profile["profile"] == "ppo" else "")


def prerequisites(client):
    # Triton's CUDA driver helper compiles C even when torch reports CUDA available.
    command(client, ["sh", "-c", 'compiler="${CC:-cc}"; '
                    'command -v "$compiler" >/dev/null && "$compiler" --version >/dev/null || '
                    '{ printf "%s\\n" "C compiler required for Triton; use a CUDA devel Pod image "'
                    '"or ask its administrator to install a C toolchain before preparing training." >&2; exit 1; }'])


def bootstrap_prerequisites(client):
    """Fixed check before any transfer: the private uv bootstrap needs `python3 -m pip`."""
    try:
        command(client, ["python3", "-m", "pip", "--version"])
    except RemoteCommandFailed:
        raise ValueError("python3 -m pip is required to bootstrap the private runtime; ask the server "
                         "administrator to install pip for python3 (for example the python3-pip package)") from None


def check(client, training_stage="sft", remote_root=REMOTE, selected_gpu_uuids=()):
    prerequisites(client)
    root = runtime_path(training_stage, remote_root)
    profile = engine_profile(training_stage)
    lock_name = Path(profile["lock_file"]).name
    probe = (
        "import json,torch,importlib.metadata;"
        + ("from llamafactory.train.ppo.trainer import CustomPPOTrainer;" if training_stage == "ppo" else "") +
        "from pathlib import Path;"
        "from tuning_factory.manifest import source_hash;"
        "from tuning_factory.registry import digest;"
        + ("assert torch.cuda.device_count()==" + str(len(selected_gpu_uuids)) + ";" if selected_gpu_uuids else "") +
        "print(json.dumps(dict(source=source_hash(),engine=importlib.metadata.version('llamafactory'),"
        "cuda=torch.cuda.is_available(),lock=digest(Path(" + repr(root + "/" + lock_name) +
        ").read_bytes()))))"
    )
    argv = [root + "/venv/bin/python", "-c", probe]
    if selected_gpu_uuids:
        argv = ["env", "CUDA_VISIBLE_DEVICES=" + ",".join(selected_gpu_uuids), *argv]
    result = json.loads(command(client, argv).splitlines()[-1])
    from .desktop import assets_root
    expected = hashlib.sha256((assets_root() / profile["lock_file"]).read_bytes()).hexdigest()
    if result != {"source": source_hash(), "engine": profile["engine_version"], "cuda": True, "lock": expected}:
        raise ValueError("GPU runtime source, lock, engine or CUDA preflight mismatch; prepare runtime first")
    return result


def runtime(settings, state_root, prepare=False, training_stage="sft", *, inspection=None):
    if settings.get("provider") == "ssh_server" and prepare:
        if inspection is None:
            raise ValueError("approved destination inspection required for server preparation")
        inspection = SSHServerInspection.model_validate(inspection.model_dump()
            if hasattr(inspection, "model_dump") else inspection)
        inspection.validate_settings(server_settings(settings))
    profile = engine_profile(training_stage)
    lock_name = Path(profile["lock_file"]).name
    with connection(settings) as (pod, client):
        generic = isinstance(pod, SSHServerFacts)
        config = server_settings(settings) if generic else None
        remote_root = config.remote_root if generic else REMOTE
        selected = config.selected_gpu_uuids if generic else ()
        if generic and inspection is not None and (inspection.server_id != pod.server_id
                                                   or inspection.inventory != pod.inventory):
            raise ValueError("server hardware differs from approved preparation inspection")
        if prepare:
            with preparation_guard(state_root, config.profile(pod) if generic else None, pod) as preparation:
                prerequisites(client)
                bootstrap_prerequisites(client)
                from .desktop import assets_root
                assets = assets_root()
                wheels = list((assets / "dist").glob("llm_tuning_factory-*.whl"))
                if len(wheels) != 1:
                    raise ValueError("matching worker wheel missing; rebuild desktop package")
                root = runtime_path(training_stage, remote_root)
                helper = SSHExecutor("", 22, "root", "", client=client, pod=None if generic else pod)
                with client.open_sftp() as sftp:
                    helper.mkdir(sftp, root)
                    for path in [assets / profile["lock_file"], wheels[0]]:
                        sftp.put(str(path), root + "/" + path.name)
                # All installs, Python downloads and caches stay below the private root.
                script = (
                    "set -eu\n"
                    "command -v flock >/dev/null\n"
                    f"exec 9>{shlex.quote(root + '/prepare.lock')}\nflock -n 9\n"
                    f"export UV_PYTHON_INSTALL_DIR={shlex.quote(remote_root + '/python')}\n"
                    f"export UV_CACHE_DIR={shlex.quote(remote_root + '/uv-cache')}\n"
                    f"python3 -m pip install --disable-pip-version-check --no-cache-dir "
                    f"--target {shlex.quote(root + '/bootstrap')} uv==0.11.23\n"
                    f"export PYTHONPATH={shlex.quote(root + '/bootstrap')}\n"
                    f"python3 -m uv venv --python 3.12 --allow-existing {shlex.quote(root + '/venv')}\n"
                    f"python3 -m uv pip sync --python {shlex.quote(root + '/venv/bin/python')} --require-hashes "
                    f"{shlex.quote(root + '/' + lock_name)}\n"
                    f"python3 -m uv pip install --python {shlex.quote(root + '/venv/bin/python')} --no-deps "
                    f"{shlex.quote(root + '/' + wheels[0].name)}\n"
                )
                preparation["started"] = True
                try:
                    command(client, ["sh", "-c", script], timeout=1800)
                except RemoteCommandFailed:
                    preparation["stopped"] = True
                    raise
                preparation["stopped"] = True
                result = check(client, training_stage, remote_root, selected) if generic else check(client, training_stage)
        else:
            result = check(client, training_stage, remote_root, selected) if generic else check(client, training_stage)
        if generic:
            return {"ready": True, "provider": "ssh_server", "facts": pod.model_dump(mode="json"),
                    "runtime": runtime_path(training_stage, remote_root), "profile": profile["profile"], **result}
        return {"ready": True, "pod_id": pod["id"], "runtime": runtime_path(training_stage),
                "profile": profile["profile"], **result}


@contextmanager
def preparation_guard(state_root, profile, facts):
    """No DB transaction spans SSH. Uncertain installations retain durable ownership."""
    state = {"started": False, "stopped": False}
    if profile is None:
        yield state
        return
    from .jobs import JobStore
    store = JobStore(Path(state_root) / "jobs.db")
    lease = store.reserve_preparation(profile, facts)
    try:
        yield state
    except BaseException as exc:
        if state["started"] and not state["stopped"]:
            store.mark_preparation_uncertain(lease)
            raise ExecutionUncertain("runtime preparation uncertain; reservation " + lease.reservation_id
                                     + " retained until original-server termination is confirmed") from exc
        raise
    finally:
        if not state["started"] or state["stopped"]:
            store.finish_preparation(lease, command_stopped=True)


def runtime_reservations(state_root):
    from .jobs import JobStore
    return JobStore(Path(state_root) / "jobs.db").preparation_reservations()


def release_runtime_reservation(settings, state_root, reservation_id, *, confirm_preparation_stopped=False):
    from .jobs import JobStore
    config = server_settings(settings)
    store = JobStore(Path(state_root) / "jobs.db")
    reservation = next((item for item in store.preparation_reservations()
                        if item["reservation_id"] == reservation_id), None)
    if reservation is None:
        raise ValueError("unknown preparation reservation")
    facts = SSHServerFacts.model_validate(reservation["facts"])
    profile = config.profile(facts)
    store.reconcile_preparation(reservation_id, profile, confirm_preparation_stopped=confirm_preparation_stopped)
    return {"released": reservation_id}


@contextmanager
def executor_for(settings, state_root, preflight=True, training_stage="sft", *, server_receipt=None,
                 recovery=False):
    # recovery=True always means the SSH recovery-only executor; RunPod reconciles with preflight=False.
    if recovery:
        if settings.get("provider", "existing_runpod") != "ssh_server":
            raise ValueError("recovery-only executor requires an SSH server destination")
        with recovery_executor(settings, server_receipt) as executor:
            yield executor
        return
    if server_receipt is not None:
        server_receipt = validate_server_receipt(settings, server_receipt)
    with connection(settings) as (pod, client):
        if isinstance(pod, SSHServerFacts):
            config = server_settings(settings)
            if server_receipt is not None:
                server_receipt.validate_destination(server_receipt.manifest_sha256, config.transport, pod)
            if preflight:
                check(client, training_stage, config.remote_root, config.selected_gpu_uuids)
            root = runtime_path(training_stage, config.remote_root)
            lock_name = Path(engine_profile(training_stage)["lock_file"]).name
            executor = SSHExecutor("", 22, config.ssh_username, "", client=client,
                                   server_profile=server_receipt.profile if server_receipt else config.profile(pod),
                                   server_facts=pod,
                                   remote_root=config.remote_root + "/jobs", remote_python=root + "/venv/bin/python",
                                   worker_lock_file=root + "/" + lock_name, hf_home=config.remote_root + "/hf-cache")
            yield executor
            return
        if preflight:
            check(client, training_stage)
        root = runtime_path(training_stage)
        lock_name = Path(engine_profile(training_stage)["lock_file"]).name
        executor = SSHExecutor("", 22, "root", "", client=client, pod=pod,
                               remote_root=REMOTE + "/jobs", remote_python=root + "/venv/bin/python",
                               worker_lock_file=root + "/" + lock_name, hf_home=REMOTE + "/hf-cache")
        yield executor


@contextmanager
def recovery_executor(settings, server_receipt):
    """Collect an existing attempt after unrelated GPU inventory drift. It requires the original
    physical server, exact transport and (in collect_result) the original attempt record. It never
    preflights, claims or launches; launch and preparation keep the exact inventory checks."""
    if server_receipt is None:
        raise ValueError("original SSH server destination receipt required for recovery")
    receipt = ServerApprovalReceipt.model_validate(server_receipt.model_dump()
        if hasattr(server_receipt, "model_dump") else server_receipt)
    config = server_settings(settings)
    receipt.validate_recovery(config.transport)  # Edited settings never open SSH.
    with connection(settings, recovery=True) as (identity, client):
        receipt.validate_recovery(config.transport, identity)
        yield SSHExecutor("", 22, config.ssh_username, "", client=client, server_profile=receipt.profile,
                          server_facts=receipt.facts, remote_root=config.remote_root + "/jobs",
                          recovery_only=True)


def live_logs(settings, state_root, directory):
    record = json.loads((Path(directory) / "remote-attempt.json").read_bytes())
    remote_root = validate_original_destination(settings, record)
    remote = record["remote_path"]
    expected = remote_root + "/jobs/" + Path(directory).name + "/"
    if not remote.startswith(expected) or not re.fullmatch("[0-9a-f]{32}", remote[len(expected):]):
        raise ValueError("remote attempt outside desktop namespace")
    with connection(settings, recovery=True) as (facts, client):
        validate_original_observation(record, facts)
        with client.open_sftp() as sftp:
            chunks = []
            for name in ("train.log", "worker.log"):
                try:
                    with sftp.open(remote + "/" + name, "rb") as handle:
                        handle.seek(max(0, handle.stat().st_size - 48000))
                        chunks.append(name + "\n" + handle.read(48000).decode("utf-8", errors="replace"))
                except FileNotFoundError:
                    pass
            return {"text": "\n".join(chunks)}


def read_progress(settings, directory):
    from .desktop_inspection import parse_progress
    record = json.loads((Path(directory) / "remote-attempt.json").read_bytes())
    remote_root = validate_original_destination(settings, record)
    expected = remote_root + "/jobs/" + Path(directory).name + "/"
    remote = record["remote_path"]
    if (not remote.startswith(expected)
            or not re.fullmatch("[0-9a-f]{32}", remote[len(expected):])):
        raise ValueError("progress request differs from original Pod or job namespace")
    with connection(settings, recovery=True) as (facts, client):
        validate_original_observation(record, facts)
        with client.open_sftp() as sftp:
            try:
                with sftp.open(remote + "/output/trainer_log.jsonl", "rb") as handle:
                    handle.seek(max(0, handle.stat().st_size - 2 * 1024 * 1024))
                    return parse_progress(handle.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
            except FileNotFoundError:
                return parse_progress("")


def validate_original_destination(settings, record):
    """Refuse edited settings before opening SSH to read an old attempt."""
    from .gpu_servers import SSHServerProfile
    if settings.get("provider", "existing_runpod") == "ssh_server":
        config = server_settings(settings)
        destination = record.get("destination")
        if destination is None or SSHServerProfile.model_validate(destination).transport != config.transport:
            raise ValueError("request destination differs from original execution")
        return config.remote_root
    if record.get("destination") is not None or record.get("pod_id") != settings.get("pod_id"):
        raise ValueError("request Pod differs from original execution")
    return REMOTE


def validate_original_observation(record, facts):
    """Recovery connections yield SSHServerIdentity (server) or the Pod dictionary (RunPod)."""
    from .gpu_servers import SSHServerProfile
    if isinstance(facts, SSHServerIdentity):
        validate_recovery_identity(SSHServerProfile.model_validate(record.get("destination")), facts.transport, facts)


def inspect_server(settings):
    """Read-only discovery accepts an empty selection and returns public inventory."""
    with connection(settings, inspection_only=True) as (inspection, _):
        return inspection


def plan_server_destination(settings, manifest):
    """Create the external receipt for a resolved real v1 manifest, before approval."""
    from .config import AIConfig
    from .manifest import verify_manifest
    verify_manifest(manifest)
    if manifest.get("simulation") or manifest.get("schema_version") != 1:
        raise ValueError("server destination receipt requires a real v1 manifest")
    config = server_settings(settings)
    with connection(settings) as (facts, _):
        facts.validate_config(AIConfig.model_validate(manifest["config"]))
        return ServerApprovalReceipt.create(manifest["sha256"], config.profile(facts), facts)


def validate_server_receipt(settings, receipt):
    """Reject edited native settings before any submit or remote connection."""
    receipt = ServerApprovalReceipt.model_validate(receipt.model_dump() if hasattr(receipt, "model_dump") else receipt)
    config = server_settings(settings)
    if (receipt.profile.transport != config.transport
            or receipt.profile.total_hourly_cost_usd != config.total_hourly_cost_usd):
        raise ValueError("server receipt destination differs from current settings")
    return receipt
