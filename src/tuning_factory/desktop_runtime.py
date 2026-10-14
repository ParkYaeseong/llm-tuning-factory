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

from .execution import RunPodController, SSHExecutor
from .manifest import source_hash

REMOTE = "/workspace/factory-desktop"


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
def connection(settings):
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
        raise ValueError("GPU runtime command failed. " + output.decode("utf-8", errors="replace")[-3000:])
    return output.decode("utf-8", errors="replace")


def runtime_path():
    return REMOTE + "/runtime/" + source_hash()[:16]


def check(client):
    root = runtime_path()
    probe = (
        "import json,torch,importlib.metadata;"
        "from pathlib import Path;"
        "from tuning_factory.manifest import source_hash;"
        "from tuning_factory.registry import digest;"
        "print(json.dumps(dict(source=source_hash(),engine=importlib.metadata.version('llamafactory'),"
        "cuda=torch.cuda.is_available(),lock=digest(Path(" + repr(root + "/gpu-requirements.lock") +
        ").read_bytes()))))"
    )
    result = json.loads(command(client, [root + "/venv/bin/python", "-c", probe]).splitlines()[-1])
    from .desktop import assets_root
    expected = hashlib.sha256((assets_root() / "containers/gpu-requirements.lock").read_bytes()).hexdigest()
    if result != {"source": source_hash(), "engine": "0.9.5", "cuda": True, "lock": expected}:
        raise ValueError("GPU runtime source, lock, engine or CUDA preflight mismatch; prepare runtime first")
    return result


def runtime(settings, state_root, prepare=False):
    with connection(settings) as (pod, client):
        if prepare:
            from .desktop import assets_root
            assets = assets_root()
            wheels = list((assets / "dist").glob("llm_tuning_factory-*.whl"))
            if len(wheels) != 1:
                raise ValueError("matching worker wheel missing; rebuild desktop package")
            root = runtime_path()
            helper = SSHExecutor("", 22, "root", "", client=client, pod=pod)
            with client.open_sftp() as sftp:
                helper.mkdir(sftp, root)
                for path in [assets / "containers/gpu-requirements.lock", wheels[0]]:
                    sftp.put(str(path), root + "/" + path.name)
            # uv bootstrap is private to this source-versioned namespace, including Python downloads.
            script = (
                "set -eu\n"
                "command -v flock >/dev/null\n"
                f"exec 9>{shlex.quote(root + '/prepare.lock')}\nflock -n 9\n"
                f"export UV_PYTHON_INSTALL_DIR={shlex.quote(REMOTE + '/python')}\n"
                f"python3 -m pip install --disable-pip-version-check --target {shlex.quote(root + '/bootstrap')} uv==0.11.23\n"
                f"export PYTHONPATH={shlex.quote(root + '/bootstrap')}\n"
                f"python3 -m uv venv --python 3.12 --allow-existing {shlex.quote(root + '/venv')}\n"
                f"python3 -m uv pip sync --python {shlex.quote(root + '/venv/bin/python')} --require-hashes "
                f"{shlex.quote(root + '/gpu-requirements.lock')}\n"
                f"python3 -m uv pip install --python {shlex.quote(root + '/venv/bin/python')} --no-deps "
                f"{shlex.quote(root + '/' + wheels[0].name)}\n"
            )
            command(client, ["sh", "-c", script], timeout=1800)
        result = check(client)
        return {"ready": True, "pod_id": pod["id"], "runtime": runtime_path(), **result}


@contextmanager
def executor_for(settings, state_root, preflight=True):
    with connection(settings) as (pod, client):
        if preflight:
            check(client)
        root = runtime_path()
        executor = SSHExecutor("", 22, "root", "", client=client, pod=pod,
                               remote_root=REMOTE + "/jobs", remote_python=root + "/venv/bin/python",
                               worker_lock_file=root + "/gpu-requirements.lock", hf_home=REMOTE + "/hf-cache")
        yield executor


def live_logs(settings, state_root, directory):
    record = json.loads((Path(directory) / "remote-attempt.json").read_bytes())
    if record.get("pod_id") != settings.get("pod_id"):
        raise ValueError("live logs Pod differs from original execution")
    remote = record["remote_path"]
    expected = REMOTE + "/jobs/" + Path(directory).name + "/"
    if not remote.startswith(expected) or not re.fullmatch("[0-9a-f]{32}", remote[len(expected):]):
        raise ValueError("remote attempt outside desktop namespace")
    with connection(settings) as (_, client):
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
    expected = REMOTE + "/jobs/" + Path(directory).name + "/"
    remote = record["remote_path"]
    if (record.get("pod_id") != settings.get("pod_id") or not remote.startswith(expected)
            or not re.fullmatch("[0-9a-f]{32}", remote[len(expected):])):
        raise ValueError("progress request differs from original Pod or job namespace")
    with connection(settings) as (_, client):
        with client.open_sftp() as sftp:
            try:
                with sftp.open(remote + "/output/trainer_log.jsonl", "rb") as handle:
                    handle.seek(max(0, handle.stat().st_size - 2 * 1024 * 1024))
                    return parse_progress(handle.read(2 * 1024 * 1024).decode("utf-8", errors="replace"))
            except FileNotFoundError:
                return parse_progress("")
