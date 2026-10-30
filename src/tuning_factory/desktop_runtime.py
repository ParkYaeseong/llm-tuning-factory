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

from .execution import ExecutionUncertain, RunPodController, SSHExecutor, keep_alive
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
from .runtime_profiles import MODEL_OPS_PINS, MODEL_OPS_RUNTIME, engine_profile

REMOTE = "/workspace/factory-desktop"
MISSING_KEY = "SSH 개인키 파일을 찾을 수 없습니다. 연결 화면에서 키 파일을 다시 선택하세요."


def local_key_file(path):
    """Refuse a missing private key before any network contact, without echoing its path."""
    path = Path(path).expanduser()
    if not path.is_file():
        raise ValueError(MISSING_KEY)
    return str(path)


class RemoteCommandFailed(ValueError):
    """The remote command returned an observed nonzero exit status."""


RUNTIME_MISSING = ("GPU runtime command failed. The private runtime is not prepared on this host; "
                   "prepare runtime first")
RUNTIME_MISMATCH = "GPU runtime source, lock, engine or CUDA preflight mismatch; prepare runtime first"
GPU_INCOMPATIBLE = "이 GPU에서 앱 학습 환경의 CUDA 연산이 실행되지 않습니다. 지원하는 GPU 세대인지 확인하세요."
RUNTIME_PREPARATION_REQUIRED = ("Pod에 이 앱 버전의 학습 환경이 없습니다. 실행 승인에서 학습 환경 준비를 함께 "
                                "승인하거나 연결 설정의 '학습 환경 준비'를 먼저 누르세요.")
SERVER_RUNTIME_REQUIRED = ("GPU 서버에 이 앱 버전의 학습 환경이 없습니다. 연결 설정에서 GPU 목록을 조회한 뒤 "
                           "'학습 환경 준비'를 먼저 누르세요.")
POST_PREPARATION_FAILED = ("학습 환경을 준비했지만 확인을 통과하지 못했습니다. 연결 설정에서 '환경 확인'으로 "
                           "상태를 확인하거나 '학습 환경 준비'를 다시 실행하세요.")
PREPARATION_BUSY = "다른 학습 환경 준비가 이 GPU에서 진행 중입니다. 끝난 뒤 다시 시도하세요."
PREPARE_IN_EXECUTION_RUNPOD_ONLY = "runtime preparation during execution applies to a RunPod preflight only"
# The first import of the pinned torch/LLaMA-Factory and CUDA initialisation from a fresh venv on a network volume can take minutes.
PROBE_TIMEOUT_SECONDS = 900


class RuntimeMissing(RemoteCommandFailed):
    """The private runtime's interpreter is absent on the GPU host."""


class RuntimeMismatch(ValueError):
    """The private runtime exists but its source, lock, engine or CUDA availability differs, or its probe failed."""


class GPUIncompatible(ValueError):
    """The installed runtime cannot run CUDA kernels on this GPU."""


GPU_UNAVAILABLE = "GPU를 사용할 수 없습니다. 인식된 GPU가 없거나 선택한 GPU 수와 다릅니다. Pod·서버의 GPU 상태를 확인하세요."
GPU_BUSY = "GPU가 사용 중이거나 오류 상태라 CUDA 연산을 실행하지 못했습니다. 다른 작업이 GPU를 쓰고 있는지 확인하세요."


class GPUUnavailable(ValueError):
    """The runtime is installed, but the GPU is absent, a different count, busy or in an error state."""


# gpu_compat.capability_supported's rule, run inside the private runtime: same-major machine code with a lower or
# equal minor, or PTX at or below the device. A failing kernel is reported, never raised.
ARCH_PROBE = (
    "arch_ok=kernel_ok=bf16=False\n"
    "capability=None\n"
    "if torch.cuda.is_available():\n"
    " cap=tuple(torch.cuda.get_device_capability())\n"
    " capability='%d.%d'%cap\n"
    " codes=[re.fullmatch(r'(sm|compute)_(\\d+)(\\d)[a-z]?',a) for a in torch.cuda.get_arch_list()]\n"
    " codes=[(m[1],int(m[2]),int(m[3])) for m in codes if m]\n"
    " arch_ok=any(k=='sm' and j==cap[0] and n<=cap[1] for k,j,n in codes)"
    " or any(k=='compute' and (j,n)<=cap for k,j,n in codes)\n"
    " try:\n"
    "  kernel_ok=(torch.ones(8,device='cuda')*2).sum().item()==16\n"
    "  bf16=bool(torch.cuda.is_bf16_supported())\n"
    " except Exception:\n"
    "  kernel_ok=False\n"
)


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
        key_file = local_key_file(config.ssh_key_file)
        client = paramiko.SSHClient()
        client.set_missing_host_key_policy(PinnedHostKey(config.ssh_fingerprint))
        try:
            client.connect(config.ssh_host, port=config.ssh_port, username=config.ssh_username,
                           key_filename=key_file, allow_agent=False, look_for_keys=False,
                           timeout=20, banner_timeout=20, auth_timeout=20)
            keep_alive(client)
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
        keep_alive(client)
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
            raise TimeoutError("remote operation timed out; inspect the remote GPU host "
                               "before retrying preparation")
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


def check(client, training_stage="sft", remote_root=REMOTE, selected_gpu_uuids=(), *, model_ops=False):
    """Probe the private runtime. ``model_ops=True`` (merge/materialize) also requires the installed
    MODEL_OPS_PINS versions. Raises RuntimeMissing (no interpreter), RuntimeMismatch (stale or unparseable
    runtime), GPUUnavailable (runtime fine, GPU absent, miscounted or busy) or GPUIncompatible (no kernel for
    this GPU generation)."""
    profile = engine_profile(training_stage)
    if model_ops and profile["profile"] != "primary":
        raise ValueError(MODEL_OPS_RUNTIME)
    prerequisites(client)
    root = runtime_path(training_stage, remote_root)
    lock_name = Path(profile["lock_file"]).name
    pins = (",model_ops=dict((n,importlib.metadata.version(n).split('+')[0]) for n in "
            + repr(tuple(MODEL_OPS_PINS)) + ")") if model_ops else ""
    probe = (
        "import json,re,torch,importlib.metadata\n"
        + ("from llamafactory.train.ppo.trainer import CustomPPOTrainer\n" if training_stage == "ppo" else "")
        + "from pathlib import Path\n"
        "from tuning_factory.manifest import source_hash\n"
        "from tuning_factory.registry import digest\n"
        + ARCH_PROBE
        + "print('TF_PROBE '+json.dumps(dict(source=source_hash(),engine=importlib.metadata.version('llamafactory'),"
        "cuda=torch.cuda.is_available(),lock=digest(Path(" + repr(root + "/" + lock_name) + ").read_bytes()),"
        "devices=torch.cuda.device_count(),arch_ok=arch_ok,kernel_ok=kernel_ok,bf16=bf16,capability=capability" + pins + ")))"
    )
    python = root + "/venv/bin/python"
    argv = [python, "-c", probe]
    if selected_gpu_uuids:
        argv = ["env", "CUDA_VISIBLE_DEVICES=" + ",".join(selected_gpu_uuids), *argv]
    try:
        output = command(client, argv, timeout=PROBE_TIMEOUT_SECONDS)
    except RemoteCommandFailed:
        try:
            command(client, ["test", "-x", python])
        except RemoteCommandFailed:
            raise RuntimeMissing(RUNTIME_MISSING) from None
        raise RuntimeMismatch(RUNTIME_MISMATCH) from None
    lines = output.splitlines()
    marked = [line for line in lines if line.startswith("TF_PROBE ")]
    try:
        result = json.loads(marked[-1][len("TF_PROBE "):] if marked else lines[-1])
    except (json.JSONDecodeError, IndexError):
        raise RuntimeMismatch(RUNTIME_MISMATCH) from None
    if not isinstance(result, dict):
        raise RuntimeMismatch(RUNTIME_MISMATCH)
    from .desktop import assets_root
    expected = hashlib.sha256((assets_root() / profile["lock_file"]).read_bytes()).hexdigest()
    base = {key: result.get(key) for key in ("source", "engine", "lock")}
    if (base != {"source": source_hash(), "engine": profile["engine_version"], "lock": expected}
            or ("model_ops" in result and not model_ops)):
        raise RuntimeMismatch(RUNTIME_MISMATCH)
    if result.get("cuda") is not True or (selected_gpu_uuids and result.get("devices") != len(selected_gpu_uuids)):
        raise GPUUnavailable(GPU_UNAVAILABLE)
    if model_ops and result.get("model_ops") != dict(MODEL_OPS_PINS):
        raise ValueError(MODEL_OPS_RUNTIME)
    if result.get("arch_ok") is not True:
        raise GPUIncompatible(GPU_INCOMPATIBLE)
    if result.get("kernel_ok") is not True:
        raise GPUUnavailable(GPU_BUSY)
    return result


def observation_key(settings):
    """The local GPU observation key of the saved destination; never contacts it."""
    if settings.get("provider", "existing_runpod") == "ssh_server":
        return "server-" + server_settings(settings).destination_sha256[:16]
    pod_id = str(settings.get("pod_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pod_id):
        raise ValueError("invalid Pod ID")
    return "runpod-" + pod_id


def read_gpu_facts(client, gpu_uuids=()):
    from .gpu_compat import GPU_QUERY, parse_gpu_facts
    gpus = parse_gpu_facts(command(client, list(GPU_QUERY)))
    if not gpu_uuids:
        return gpus
    selected = [gpu for gpu in gpus if gpu["uuid"] in set(gpu_uuids)]
    if len(selected) != len(set(gpu_uuids)):
        raise ValueError("selected GPU missing from nvidia-smi output")
    return selected


def observe(client, settings, state_root, pod):
    """Read (read-only nvidia-smi) and store this destination's GPU facts. An unreadable query is recorded as no
    facts, so the compatibility judge warns and the runtime check still decides."""
    from .gpu_compat import make_observation, save_observation
    server = isinstance(pod, SSHServerFacts)
    try:
        gpus = read_gpu_facts(client, server_settings(settings).selected_gpu_uuids if server else ())
    except ValueError:   # RemoteCommandFailed included
        gpus = []
    observation = make_observation(observation_key(settings), gpus, None if server else pod)
    try:
        save_observation(state_root, observation)
    except OSError:
        pass   # the live observation is still returned; a locked or unwritable state file must not abort a run
    return observation


def install_runtime(client, training_stage, remote_root, pod, preparation):
    """Upload the pinned lock and worker wheel, then install the private runtime below ``remote_root``.

    The caller holds ``preparation_guard`` and has run prerequisites() and bootstrap_prerequisites()."""
    from .desktop import assets_root
    from .runtime_profiles import runtime_capabilities
    profile = engine_profile(training_stage)
    lock_name = Path(profile["lock_file"]).name
    backend = runtime_capabilities(profile["profile"])["torch_backend"]
    assets = assets_root()
    wheels = list((assets / "dist").glob("llm_tuning_factory-*.whl"))
    if len(wheels) != 1:
        raise ValueError("matching worker wheel missing; rebuild desktop package")
    root = runtime_path(training_stage, remote_root)
    helper = SSHExecutor("", 22, "root", "", client=client, pod=None if isinstance(pod, SSHServerFacts) else pod)
    with client.open_sftp() as sftp:
        helper.mkdir(sftp, root)
        for path in [assets / profile["lock_file"], wheels[0]]:
            sftp.put(str(path), root + "/" + path.name)
    # All installs, Python downloads and caches stay below the private root.
    # A preparation always rebuilds the private venv (--clear, under the flock): an interrupted
    # install can leave packages that uv still reports as installed.
    script = (
        "set -eu\n"
        "command -v flock >/dev/null\n"
        f"exec 9>{shlex.quote(root + '/prepare.lock')}\nflock -n 9 || {{ echo TF_PREPARE_BUSY; exit 75; }}\n"
        f"export UV_PYTHON_INSTALL_DIR={shlex.quote(remote_root + '/python')}\n"
        f"export UV_CACHE_DIR={shlex.quote(remote_root + '/uv-cache')}\n"
        f"python3 -m pip install --disable-pip-version-check --no-cache-dir "
        f"--target {shlex.quote(root + '/bootstrap')} uv==0.11.23\n"
        f"export PYTHONPATH={shlex.quote(root + '/bootstrap')}\n"
        f"python3 -m uv venv --python 3.12 --clear {shlex.quote(root + '/venv')}\n"
        f"python3 -m uv pip sync --python {shlex.quote(root + '/venv/bin/python')} --require-hashes "
        f"--torch-backend {backend} {shlex.quote(root + '/' + lock_name)}\n"
        f"python3 -m uv pip install --python {shlex.quote(root + '/venv/bin/python')} --no-deps "
        f"{shlex.quote(root + '/' + wheels[0].name)}\n"
    )
    preparation["started"] = True
    try:
        command(client, ["sh", "-c", script], timeout=1800)
    except RemoteCommandFailed as error:
        preparation["stopped"] = True
        if "TF_PREPARE_BUSY" in str(error):
            raise ValueError(PREPARATION_BUSY) from None
        raise
    preparation["stopped"] = True


def runtime(settings, state_root, prepare=False, training_stage="sft", *, inspection=None):
    """Check (or, approved, prepare) the private runtime and record this destination's GPU facts.

    A check reports ``runtime_state`` ("ready", "missing", "mismatch", "incompatible" or "unavailable") instead of
    raising."""
    if settings.get("provider") == "ssh_server" and prepare:
        if inspection is None:
            raise ValueError("approved destination inspection required for server preparation")
        inspection = SSHServerInspection.model_validate(inspection.model_dump()
            if hasattr(inspection, "model_dump") else inspection)
        inspection.validate_settings(server_settings(settings))
    profile = engine_profile(training_stage)
    with connection(settings) as (pod, client):
        generic = isinstance(pod, SSHServerFacts)
        config = server_settings(settings) if generic else None
        remote_root = config.remote_root if generic else REMOTE
        selected = config.selected_gpu_uuids if generic else ()
        if generic and inspection is not None and (inspection.server_id != pod.server_id
                                                   or inspection.inventory != pod.inventory):
            raise ValueError("server hardware differs from approved preparation inspection")
        observation = observe(client, settings, state_root, pod)
        state = "ready"
        if prepare:
            with preparation_guard(state_root, config.profile(pod) if generic else None, pod) as preparation:
                prerequisites(client)
                bootstrap_prerequisites(client)
                install_runtime(client, training_stage, remote_root, pod, preparation)
                try:
                    result = check(client, training_stage, remote_root, selected)
                except (RuntimeMissing, RuntimeMismatch):
                    raise ValueError(POST_PREPARATION_FAILED) from None
        else:
            try:
                result = check(client, training_stage, remote_root, selected)
            except RuntimeMissing:
                result, state = {}, "missing"
            except RuntimeMismatch:
                result, state = {}, "mismatch"
            except GPUIncompatible:
                result, state = {}, "incompatible"
            except GPUUnavailable:
                result, state = {}, "unavailable"
        report = {"ready": state == "ready", "runtime_state": state, "observation": observation,
                  "profile": profile["profile"], **result}
        if generic:
            return {**report, "provider": "ssh_server", "facts": pod.model_dump(mode="json"),
                    "runtime": runtime_path(training_stage, remote_root)}
        return {**report, "pod_id": pod["id"], "runtime": runtime_path(training_stage)}


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


RESERVATION_SETTINGS_CHANGED = "다른 서버 설정(비용·키 파일 포함)으로 만든 예약입니다. 그 설정으로 바꾼 뒤 해제하세요."


def release_runtime_reservation(settings, state_root, reservation_id, *, confirm_preparation_stopped=False):
    from .jobs import JobStore
    config = server_settings(settings)
    store = JobStore(Path(state_root) / "jobs.db")
    reservation = next((item for item in store.preparation_reservations()
                        if item["reservation_id"] == reservation_id), None)
    if reservation is None:
        raise ValueError("unknown preparation reservation")
    facts = SSHServerFacts.model_validate(reservation["facts"])
    try:
        profile = config.profile(facts)
        store.reconcile_preparation(reservation_id, profile, confirm_preparation_stopped=confirm_preparation_stopped)
    except ValueError as exc:
        # Never released on a mismatch; only the two destination-mismatch refusals get the fixed Korean text.
        if str(exc) in {"SSH server profile differs from verified destination observation",
                        "preparation destination differs from original reservation"}:
            raise ValueError(RESERVATION_SETTINGS_CHANGED) from None
        raise
    return {"released": reservation_id}


def operation_stage(training_stage, runtime_profile):
    """The engine stage whose private runtime serves a v2 operation's runtime profile (primary or ppo)."""
    if runtime_profile is None:
        return training_stage
    if runtime_profile not in {"primary", "ppo"}:
        raise ValueError("unsupported runtime profile")
    if training_stage != "sft" and engine_profile(training_stage)["profile"] != runtime_profile:
        raise ValueError("training stage runtime differs from the operation runtime profile")
    return "ppo" if runtime_profile == "ppo" else training_stage


PROFILE_IDENTITY = ("profile_id", "revision", "name", "credential_scope")


def _profile_identity(settings, profile_identity, server_receipt):
    """The pinned public identity an SSH launch executor's profile carries (Task 8.4); {} keeps the default."""
    if profile_identity is None:
        return {}
    if not isinstance(profile_identity, dict) or set(profile_identity) != set(PROFILE_IDENTITY):
        raise ValueError("profile identity requires exactly profile_id, revision, name and credential_scope")
    if server_receipt is not None:
        raise ValueError("a profile identity and a server receipt cannot both name the destination profile")
    if settings.get("provider", "existing_runpod") != "ssh_server":
        raise ValueError("a profile identity applies to SSH server destinations only")
    return dict(profile_identity)


@contextmanager
def executor_for(settings, state_root, preflight=True, training_stage="sft", *, server_receipt=None,
                 recovery=False, runtime_profile=None, model_ops=False, profile_identity=None,
                 gate=None, prepare_runtime=False):
    """Executor for one destination. ``runtime_profile`` (a v2 operation's runtime) selects the private runtime;
    ``model_ops`` adds the MODEL_OPS_PINS probe for merge/materialize. Launch executors carry the remote model
    cache root (``<remote_root>/model-cache``) for v2 operations; v1 runs ignore it. ``profile_identity`` (Task 8.4)
    gives an SSH launch executor's profile the pinned profile ID, revision, name and credential scope; without it
    the default identity is unchanged.
    ``gate`` (the desktop's compatibility judge) receives this destination's GPU observation before any runtime
    work and raises to refuse it. ``prepare_runtime`` (RunPod preflight only) installs a missing or mismatched
    private runtime instead of refusing."""
    # recovery=True always means the SSH recovery-only executor; RunPod reconciles with preflight=False.
    identity = _profile_identity(settings, profile_identity, server_receipt)   # before any contact
    training_stage = operation_stage(training_stage, runtime_profile)
    if model_ops and engine_profile(training_stage)["profile"] != "primary":
        raise ValueError(MODEL_OPS_RUNTIME)
    if prepare_runtime and (recovery or not preflight
                            or settings.get("provider", "existing_runpod") == "ssh_server"):
        raise ValueError(PREPARE_IN_EXECUTION_RUNPOD_ONLY)
    pins = {"model_ops": True} if model_ops else {}
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
            if server_receipt is not None:   # before the gate: destination drift is then the reported cause
                server_receipt.validate_destination(server_receipt.manifest_sha256, config.transport, pod)
        if gate is not None:
            gate(observe(client, settings, state_root, pod))
        if isinstance(pod, SSHServerFacts):
            if preflight:
                try:
                    check(client, training_stage, config.remote_root, config.selected_gpu_uuids, **pins)
                except (RuntimeMissing, RuntimeMismatch):
                    if gate is None:
                        raise
                    raise ValueError(SERVER_RUNTIME_REQUIRED) from None
            root = runtime_path(training_stage, config.remote_root)
            lock_name = Path(engine_profile(training_stage)["lock_file"]).name
            executor = SSHExecutor("", 22, config.ssh_username, "", client=client,
                                   server_profile=server_receipt.profile if server_receipt else config.profile(
                                       pod, **identity),
                                   server_facts=pod,
                                   remote_root=config.remote_root + "/jobs", remote_python=root + "/venv/bin/python",
                                   worker_lock_file=root + "/" + lock_name, hf_home=config.remote_root + "/hf-cache",
                                   model_cache_root=config.remote_root + "/model-cache")
            yield executor
            return
        if preflight:
            try:
                check(client, training_stage, **pins)
            except (RuntimeMissing, RuntimeMismatch):
                if not prepare_runtime:
                    if gate is None:
                        raise
                    raise ValueError(RUNTIME_PREPARATION_REQUIRED) from None
                with preparation_guard(state_root, None, pod) as preparation:
                    prerequisites(client)
                    bootstrap_prerequisites(client)
                    install_runtime(client, training_stage, REMOTE, pod, preparation)
                try:
                    check(client, training_stage, **pins)
                except (RuntimeMissing, RuntimeMismatch):
                    raise ValueError(POST_PREPARATION_FAILED) from None
        root = runtime_path(training_stage)
        lock_name = Path(engine_profile(training_stage)["lock_file"]).name
        executor = SSHExecutor("", 22, "root", "", client=client, pod=pod,
                               remote_root=REMOTE + "/jobs", remote_python=root + "/venv/bin/python",
                               worker_lock_file=root + "/" + lock_name, hf_home=REMOTE + "/hf-cache",
                               model_cache_root=REMOTE + "/model-cache")
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
        raise ValueError("progress request differs from original remote GPU host or job namespace")
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
