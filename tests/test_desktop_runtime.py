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


# -- Task 7.4: v2 runtime and model-cache selection ------------------------------------------------

def test_executor_for_v2_selects_runtime_profile_and_cache_root(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from test_gpu_servers import FakeServer, settings

    from tuning_factory import desktop_runtime as runtime

    checks = []
    monkeypatch.setattr(runtime, "check", lambda client, *args, **kw: checks.append((args, kw)) or {})
    FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    with runtime.executor_for(native, tmp_path, runtime_profile="ppo") as executor:
        assert executor.model_cache_root == config.remote_root + "/model-cache"
        assert executor.remote_python == runtime.runtime_path("ppo", config.remote_root) + "/venv/bin/python"
        assert executor.worker_lock_file.endswith("/gpu-ppo-requirements.lock")
        assert executor.hf_home == config.remote_root + "/hf-cache"
    with runtime.executor_for(native, tmp_path, runtime_profile="primary", model_ops=True) as executor:
        assert executor.remote_python == runtime.runtime_path("sft", config.remote_root) + "/venv/bin/python"
        assert executor.worker_lock_file.endswith("/gpu-requirements.lock")
    selected = config.selected_gpu_uuids
    assert checks == [(("ppo", config.remote_root, selected), {}),
                      (("sft", config.remote_root, selected), {"model_ops": True})]

    pod = {"id": "borrowed"}

    @contextmanager
    def existing_pod(settings, **kwargs):
        yield pod, SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(runtime, "connection", existing_pod)
    checks.clear()
    with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, runtime_profile="primary") as executor:
        assert executor.model_cache_root == runtime.REMOTE + "/model-cache" and executor.pod == pod
        assert executor.root == runtime.REMOTE + "/jobs" and executor.hf_home == runtime.REMOTE + "/hf-cache"
        assert executor.remote_python == runtime.runtime_path("sft") + "/venv/bin/python"
    assert checks == [(("sft",), {})]
    # Inconsistent selections are refused before any contact with the GPU host.
    monkeypatch.setattr(runtime, "connection", lambda *a, **kw: pytest.fail("GPU host contacted"))
    for kwargs, message in ((dict(runtime_profile="gpu"), "unsupported runtime profile"),
                            (dict(runtime_profile="ppo", model_ops=True), "primary runtime"),
                            (dict(runtime_profile="ppo", training_stage="dpo"), "runtime profile")):
        with pytest.raises(ValueError, match=message):
            with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, **kwargs):
                pytest.fail("inconsistent runtime selection reached execution")


def test_check_model_ops_pins_only_when_requested(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.desktop import assets_root
    from tuning_factory.registry import digest
    from tuning_factory.runtime_profiles import MODEL_OPS_PINS

    ready = {"source": runtime.source_hash(), "engine": "0.9.5", "cuda": True,
             "lock": digest((assets_root() / "containers/gpu-requirements.lock").read_bytes())}
    probes, replies = [], []

    def run(client, argv, **kwargs):
        if argv[0] == "sh":
            return ""
        probes.append(argv[-1])
        return json.dumps(replies.pop(0))

    monkeypatch.setattr(runtime, "command", run)
    replies.append(ready)
    assert runtime.check(None) == ready
    root = runtime.runtime_path("sft")
    legacy = ("import json,torch,importlib.metadata;from pathlib import Path;"
              "from tuning_factory.manifest import source_hash;from tuning_factory.registry import digest;"
              "print(json.dumps(dict(source=source_hash(),engine=importlib.metadata.version('llamafactory'),"
              "cuda=torch.cuda.is_available(),lock=digest(Path(" + repr(root + "/gpu-requirements.lock")
              + ").read_bytes()))))")
    assert probes[0] == legacy   # the default probe and result dict are byte-identical to before
    pinned = {**ready, "model_ops": dict(MODEL_OPS_PINS)}
    replies.append(pinned)
    assert runtime.check(None, model_ops=True) == pinned
    assert all(repr(name) in probes[1] for name in MODEL_OPS_PINS) and probes[1] != legacy
    replies.append({**ready, "model_ops": {**MODEL_OPS_PINS, "peft": "0.18.0"}})
    with pytest.raises(ValueError, match="pinned PEFT 0.18.1"):
        runtime.check(None, model_ops=True)
    replies.append(pinned)
    with pytest.raises(ValueError, match="preflight mismatch"):   # unrequested keys are never accepted by default
        runtime.check(None)
    with pytest.raises(ValueError, match="primary runtime"):
        runtime.check(None, "ppo", model_ops=True)
    assert len(probes) == 4
