import base64
import hashlib
import json

import paramiko
import pytest

from tuning_factory.desktop_runtime import PinnedHostKey, validate_pod


def test_pin_mismatch_never_accepts_a_host():
    key = paramiko.RSAKey.generate(1024)
    policy = PinnedHostKey("SHA256:wrong")
    with pytest.raises(paramiko.SSHException):
        policy.missing_host_key(None, "example", key)
    fingerprint = "SHA256:" + base64.b64encode(hashlib.sha256(key.asbytes()).digest()).decode().rstrip("=")
    PinnedHostKey(fingerprint).missing_host_key(None, "example", key)


def test_runtime_requires_running_network_volume_pod():
    pod = {"desiredStatus": "EXITED"}
    with pytest.raises(ValueError, match="running"):
        validate_pod(pod)
    pod.update(desiredStatus="RUNNING", publicIp="1.2.3.4", portMappings={"22": 123},
               networkVolume={"id": "disk"}, volumeMountPath="/workspace")
    validate_pod(pod)
    pod["volumeMountPath"] = "/other"
    with pytest.raises(ValueError, match="volume"):
        validate_pod(pod)


@pytest.mark.parametrize("provider", [None, "", "existing_runpod"])
def test_recovery_executor_is_refused_for_runpod_before_any_contact(tmp_path, monkeypatch, provider):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "connection", lambda *a, **kw: pytest.fail("Pod contacted"))
    monkeypatch.setattr(runtime, "RunPodController", lambda *a, **kw: pytest.fail("RunPod called"))
    settings = {"pod_id": "borrowed", "RUNPOD_API_KEY": "fixture-only"}
    if provider is not None:
        settings["provider"] = provider
    with pytest.raises(ValueError, match="recovery"):
        with runtime.executor_for(settings, tmp_path, preflight=False, recovery=True):
            pytest.fail("RunPod recovery executor created")


def test_remote_command_timeout_message_names_no_provider():
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    channel = SimpleNamespace(set_combine_stderr=lambda _: None, recv_ready=lambda: False,
                              exit_status_ready=lambda: False, close=lambda: None)
    client = SimpleNamespace(exec_command=lambda command, timeout: (None, SimpleNamespace(channel=channel), None))
    with pytest.raises(TimeoutError) as error:
        runtime.command(client, ["true"], timeout=0)
    assert str(error.value) == "remote operation timed out; inspect the remote GPU host before retrying preparation"


def test_runtime_rejects_missing_compiler_even_when_cuda_is_available(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.desktop import assets_root
    from tuning_factory.registry import digest

    ready = {"source": runtime.source_hash(), "engine": "0.9.5", "cuda": True,
             "lock": digest((assets_root() / "containers/gpu-requirements.lock").read_bytes())}

    def run(client, argv, **kwargs):
        if argv[0] == "sh":
            raise ValueError("C compiler required for Triton")
        return json.dumps(ready)

    monkeypatch.setattr(runtime, "command", run)
    with pytest.raises(ValueError, match="C compiler"):
        runtime.check(None)


def test_runtime_accepts_compiler_with_matching_cuda_and_lock(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.desktop import assets_root
    from tuning_factory.registry import digest

    ready = {"source": runtime.source_hash(), "engine": "0.9.5", "cuda": True,
             "lock": digest((assets_root() / "containers/gpu-requirements.lock").read_bytes())}
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: "" if argv[0] == "sh" else json.dumps(ready))
    assert runtime.check(None) == ready
