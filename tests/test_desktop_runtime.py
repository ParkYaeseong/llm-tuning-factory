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

    ready = ready_reply(runtime)

    def run(client, argv, **kwargs):
        if argv[0] == "sh":
            raise ValueError("C compiler required for Triton")
        return json.dumps(ready)

    monkeypatch.setattr(runtime, "command", run)
    with pytest.raises(ValueError, match="C compiler"):
        runtime.check(None)


def test_runtime_accepts_compiler_with_matching_cuda_and_lock(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    ready = ready_reply(runtime)
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
    from tuning_factory.runtime_profiles import MODEL_OPS_PINS

    ready = ready_reply(runtime)
    probes, replies = [], []

    def run(client, argv, **kwargs):
        if argv[0] == "sh":
            return ""
        probes.append(argv[-1])
        return json.dumps(replies.pop(0))

    monkeypatch.setattr(runtime, "command", run)
    replies.append(ready)
    assert runtime.check(None) == ready
    import ast
    ast.parse(probes[0])
    assert "arch_ok=arch_ok,kernel_ok=kernel_ok" in probes[0] and "model_ops" not in probes[0]
    assert repr(runtime.runtime_path("sft") + "/gpu-requirements.lock") in probes[0]
    pinned = {**ready, "model_ops": dict(MODEL_OPS_PINS)}
    replies.append(pinned)
    assert runtime.check(None, model_ops=True) == pinned
    assert all(repr(name) in probes[1] for name in MODEL_OPS_PINS) and probes[1] != probes[0]
    replies.append({**ready, "model_ops": {**MODEL_OPS_PINS, "peft": "0.18.0"}})
    with pytest.raises(ValueError, match="pinned PEFT 0.18.1"):
        runtime.check(None, model_ops=True)
    replies.append(pinned)
    with pytest.raises(ValueError, match="preflight mismatch"):   # unrequested keys are never accepted by default
        runtime.check(None)
    with pytest.raises(ValueError, match="primary runtime"):
        runtime.check(None, "ppo", model_ops=True)
    assert len(probes) == 4


# -- Task 8.4: pinned profile identity and the v2 recovery executor --------------------------------

IDENTITY = {"profile_id": "lab", "revision": "7", "name": "Lab server", "credential_scope": "vault"}


def test_ssh_executor_carries_the_pinned_profile_identity(tmp_path, monkeypatch):
    from test_gpu_servers import FakeServer, settings

    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    monkeypatch.setattr(runtime, "check", lambda *args, **kwargs: {})
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    facts = server.facts(config)
    pinned = config.profile(facts, **IDENTITY)
    with runtime.executor_for(native, tmp_path, runtime_profile="primary", profile_identity=IDENTITY) as executor:
        assert executor.server_profile == pinned and executor.server_facts.server_id == facts.server_id
    with runtime.executor_for(native, tmp_path, runtime_profile="primary") as executor:
        assert executor.server_profile == config.profile(facts)   # the default identity is unchanged
        assert (executor.server_profile.profile_id, executor.server_profile.revision) == ("ssh-server", "1")
    connects = len(server.connects)
    receipt = ServerApprovalReceipt.create("1" * 64, pinned, facts)
    for settings_, kwargs in ((native, dict(profile_identity={"profile_id": "lab"})),
                              (native, dict(profile_identity={**IDENTITY, "extra": "x"})),
                              (native, dict(profile_identity=IDENTITY, server_receipt=receipt)),
                              ({"pod_id": "borrowed"}, dict(profile_identity=IDENTITY))):
        with pytest.raises(ValueError, match="profile identity"):
            with runtime.executor_for(settings_, tmp_path, **kwargs):
                pytest.fail("an inconsistent profile identity reached execution")
    assert len(server.connects) == connects   # refused before any contact


def test_v2_recovery_executor_uses_an_in_memory_receipt(tmp_path, monkeypatch):
    import importlib
    import sqlite3
    from contextlib import closing

    from test_gpu_servers import A4000, UUID1, FakeServer, settings

    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts
    from tuning_factory.jobs import JobStore

    ep = importlib.import_module("tuning_factory.executor_profiles")
    if not hasattr(ep, "SettingsExecutorProvider"):
        pytest.fail("executor_profiles.SettingsExecutorProvider (Task 8.4) is missing")
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    facts = server.facts(config)
    pinned = config.profile(facts, **IDENTITY)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    state = tmp_path / "state"
    store = JobStore(state / "jobs.db")
    provider = ep.SettingsExecutorProvider({"lab": ep.ProfileSecrets(public=pinned, settings=native)}, state)
    monkeypatch.setattr(runtime, "check", lambda *args, **kwargs: pytest.fail("recovery never preflights"))
    server.inventory = ((UUID1, A4000, 16376),
                        ("GPU-33333333-3333-3333-3333-333333333333", "NVIDIA H100", 81559))   # unrelated drift
    with provider.recover("lab", "7", pinned=pinned, pinned_facts=facts, manifest_sha256="1" * 64) as executor:
        assert executor.recovery_only and executor.server_profile == pinned and executor.server_facts == facts
    with closing(sqlite3.connect(store.path)) as db:
        assert db.execute("SELECT COUNT(*) FROM job_destinations").fetchone()[0] == 0   # never persisted
    server.machine_id = "c" * 32   # another physical server behind the same transport
    with pytest.raises(ValueError, match="recovery server differs"):
        with provider.recover("lab", "7", pinned=pinned, pinned_facts=facts, manifest_sha256="1" * 64):
            pytest.fail("recovery reached another server")
    # RunPod recovery: no preflight, and the observed Pod must be the pinned one.
    pod = {"id": "pod1"}
    monkeypatch.setattr(runtime, "connection", lambda settings_, **kwargs: _yield(pod))
    public = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local")
    runpod = {"pod_id": "pod1", "ssh_fingerprint": "SHA256:" + "A" * 43, "ssh_key_file": str(tmp_path / "key")}
    public = public.model_copy(update={"transport_settings_sha256": ep.runpod_transport_sha256(runpod)})
    pinned_facts = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2,
                                 observed_at="2026-10-20T00:00:00Z")
    provider = ep.SettingsExecutorProvider({"p": ep.ProfileSecrets(public=public, settings=runpod)}, state)
    with provider.recover("p", "1", pinned=public, pinned_facts=pinned_facts, manifest_sha256="1" * 64) as executor:
        assert executor.pod == pod and not executor.recovery_only
    pod["id"] = "pod2"
    with pytest.raises(ValueError) as error:
        with provider.recover("p", "1", pinned=public, pinned_facts=pinned_facts, manifest_sha256="1" * 64):
            pytest.fail("recovery reached another Pod")
    assert str(error.value) == ep.POD_DIFFERS


def _yield(pod):
    from contextlib import contextmanager
    from types import SimpleNamespace

    @contextmanager
    def manager():
        yield dict(pod), SimpleNamespace(close=lambda: None)

    return manager()


def ready_reply(runtime, lock="containers/gpu-requirements.lock", engine="0.9.5", **extra):
    from tuning_factory.desktop import assets_root
    from tuning_factory.registry import digest
    return {"source": runtime.source_hash(), "engine": engine, "cuda": True,
            "lock": digest((assets_root() / lock).read_bytes()), "arch_ok": True, "kernel_ok": True,
            "bf16": True, "capability": "12.0", **extra}


def test_check_separates_a_missing_runtime_from_a_broken_one(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    calls = []

    def absent(client, argv, **kwargs):
        calls.append(argv)
        if argv[0] == "sh":
            return ""
        raise runtime.RemoteCommandFailed("GPU runtime command failed. not found")

    monkeypatch.setattr(runtime, "command", absent)
    with pytest.raises(runtime.RuntimeMissing, match="GPU runtime command failed"):
        runtime.check(None)
    assert calls[-1] == ["test", "-x", runtime.runtime_path("sft") + "/venv/bin/python"]

    def broken(client, argv, **kwargs):
        if argv[0] in {"sh", "test"}:
            return ""
        raise runtime.RemoteCommandFailed("GPU runtime command failed. ImportError")

    monkeypatch.setattr(runtime, "command", broken)
    with pytest.raises(runtime.RuntimeMismatch, match="preflight mismatch"):
        runtime.check(None)


def probe_command(reply, trailer=""):
    return lambda client, argv, **kw: "" if argv[0] == "sh" else "noise\nTF_PROBE " + json.dumps(reply) + trailer


def test_check_gives_the_probe_a_long_timeout_but_not_the_prerequisites(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    calls = []

    def record(client, argv, **kwargs):
        calls.append((argv, kwargs.get("timeout")))
        return "" if argv[0] == "sh" else "TF_PROBE " + json.dumps(ready_reply(runtime))

    monkeypatch.setattr(runtime, "command", record)
    runtime.check(None)
    assert runtime.PROBE_TIMEOUT_SECONDS >= 600
    probe = [timeout for argv, timeout in calls if argv[0] != "sh"]
    assert probe == [runtime.PROBE_TIMEOUT_SECONDS]
    assert all(timeout in (None, 60) for argv, timeout in calls if argv[0] == "sh")


def test_check_separates_an_incompatible_gpu_from_a_busy_one(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "command", probe_command(ready_reply(runtime, arch_ok=False)))
    with pytest.raises(runtime.GPUIncompatible, match="세대"):
        runtime.check(None)
    monkeypatch.setattr(runtime, "command", probe_command(ready_reply(runtime, kernel_ok=False)))
    with pytest.raises(runtime.GPUUnavailable, match="사용 중"):
        runtime.check(None)


def test_check_reports_a_missing_or_miscounted_gpu_as_unavailable(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "command", probe_command(ready_reply(runtime, cuda=False)))
    with pytest.raises(runtime.GPUUnavailable, match="GPU를 사용할 수 없습니다"):
        runtime.check(None)
    uuids = ("GPU-a", "GPU-b")
    monkeypatch.setattr(runtime, "command", probe_command(ready_reply(runtime, devices=1)))
    with pytest.raises(runtime.GPUUnavailable):
        runtime.check(None, selected_gpu_uuids=uuids)
    reply = ready_reply(runtime, devices=2)
    monkeypatch.setattr(runtime, "command", probe_command(reply))
    assert runtime.check(None, selected_gpu_uuids=uuids) == reply


def test_check_parses_the_marked_line_and_rejects_unparseable_output(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    reply = ready_reply(runtime)
    monkeypatch.setattr(runtime, "command", probe_command(reply, trailer="\nWARNING: garbage"))
    assert runtime.check(None) == reply
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: "" if argv[0] == "sh" else "no json here")
    with pytest.raises(runtime.RuntimeMismatch, match="preflight mismatch"):
        runtime.check(None)


@pytest.mark.parametrize("capability,arch,ok", [
    ((8, 9), ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"], True),   # Ada runs sm_86 machine code
    ((12, 0), ["sm_80", "sm_90", "compute_90"], True),                           # PTX JIT
    ((12, 0), ["sm_80", "sm_86", "sm_90"], False),                               # the cu126 situation
    ((7, 0), ["sm_75", "sm_80"], False),
    ((12, 0), ["sm_80", "sm_86", "sm_90", "sm_100", "sm_120"], True),
    ((12, 1), ["sm_120"], True),
    ((10, 0), ["sm_120"], False)])
def test_arch_probe_applies_the_same_major_rule(capability, arch, ok):
    import re
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    class Tensor:
        def __mul__(self, other):
            return self

        def sum(self):
            return SimpleNamespace(item=lambda: 16)

    cuda = SimpleNamespace(is_available=lambda: True, get_device_capability=lambda: capability,
                           get_arch_list=lambda: arch, is_bf16_supported=lambda: True)
    namespace = {"torch": SimpleNamespace(cuda=cuda, ones=lambda n, device: Tensor()), "re": re}
    exec(runtime.ARCH_PROBE, namespace)
    assert namespace["arch_ok"] is ok and namespace["kernel_ok"] is True
    assert namespace["capability"] == "%d.%d" % capability


def fake_torch(capability=(12, 0), arch=("sm_120",), available=True, devices=1, ones=None):
    from types import SimpleNamespace

    class Tensor:
        def __mul__(self, other):
            return self

        def sum(self):
            return SimpleNamespace(item=lambda: 16)

    cuda = SimpleNamespace(is_available=lambda: available, get_device_capability=lambda: capability,
                           get_arch_list=lambda: list(arch), is_bf16_supported=lambda: True,
                           device_count=lambda: devices)
    return SimpleNamespace(cuda=cuda, ones=ones or (lambda n, device: Tensor()))


def test_arch_probe_reports_a_failing_kernel_and_an_absent_gpu():
    import re

    from tuning_factory import desktop_runtime as runtime

    def broken(n, device):
        raise RuntimeError("no kernel image")

    namespace = {"torch": fake_torch(ones=broken), "re": re}
    exec(runtime.ARCH_PROBE, namespace)
    assert namespace["arch_ok"] is True and namespace["kernel_ok"] is False and namespace["bf16"] is False
    namespace = {"torch": fake_torch(available=False), "re": re}
    exec(runtime.ARCH_PROBE, namespace)
    assert (namespace["arch_ok"], namespace["kernel_ok"], namespace["capability"]) == (False, False, None)


def test_generated_probe_program_runs_with_its_own_imports(monkeypatch, tmp_path, capsys):
    import importlib.metadata
    import sys

    from tuning_factory import desktop_runtime as runtime

    captured = []

    def run(client, argv, **kwargs):
        if argv[0] == "sh":
            return ""
        captured.append(argv)
        return "TF_PROBE " + json.dumps(ready_reply(runtime))

    monkeypatch.setattr(runtime, "command", run)
    runtime.check(None, "sft", remote_root=str(tmp_path))
    lock = __import__("pathlib").Path(runtime.runtime_path("sft", str(tmp_path)) + "/gpu-requirements.lock")
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_bytes(b"pins")
    monkeypatch.setitem(sys.modules, "torch", fake_torch(devices=3))
    monkeypatch.setattr(importlib.metadata, "version", lambda name: "0.9.5")
    exec(captured[-1][-1], {})
    last = capsys.readouterr().out.splitlines()[-1]
    assert last.startswith("TF_PROBE ")
    result = json.loads(last[len("TF_PROBE "):])
    assert result["arch_ok"] is True and result["kernel_ok"] is True
    assert result["devices"] == 3 and result["capability"] == "12.0"


SMI = ("GPU-aaaa, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n"
       "GPU-bbbb, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n")
POD = {"id": "borrowed", "gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1},
       "costPerHr": 0.57}


def test_observation_keys_and_gpu_selection(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.gpu_compat import GPU_QUERY

    assert runtime.observation_key({"pod_id": "yuft63rjggvh5z"}) == "runpod-yuft63rjggvh5z"
    with pytest.raises(ValueError, match="Pod ID"):
        runtime.observation_key({"pod_id": "../x"})
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI if tuple(argv) == GPU_QUERY else "")
    assert [gpu["uuid"] for gpu in runtime.read_gpu_facts(None, ("GPU-bbbb",))] == ["GPU-bbbb"]
    with pytest.raises(ValueError, match="selected GPU"):
        runtime.read_gpu_facts(None, ("GPU-cccc",))


def test_observe_saves_facts_and_tolerates_an_unreadable_query(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.gpu_compat import load_observation

    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    observation = runtime.observe(None, {"pod_id": "borrowed"}, tmp_path, POD)
    assert observation["pod"]["hourly_cost_usd"] == 0.57 and len(observation["gpus"]) == 2
    assert load_observation(tmp_path, "runpod-borrowed") == observation

    def refused(client, argv, **kwargs):
        raise runtime.RemoteCommandFailed("GPU runtime command failed. Field compute_cap is not valid")

    monkeypatch.setattr(runtime, "command", refused)
    assert runtime.observe(None, {"pod_id": "borrowed"}, tmp_path, POD)["gpus"] == []


def test_runtime_check_reports_a_missing_runtime_with_gpu_facts(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, object()

    def missing(*args, **kwargs):
        raise runtime.RuntimeMissing(runtime.RUNTIME_MISSING)

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", missing)
    report = runtime.runtime({"pod_id": "borrowed"}, tmp_path)
    assert report["ready"] is False and report["runtime_state"] == "missing" and report["pod_id"] == "borrowed"
    assert report["observation"]["gpus"][0]["capability"] == "12.0"
    monkeypatch.setattr(runtime, "check", lambda *a, **kw: {"source": "fixture"})
    ready = runtime.runtime({"pod_id": "borrowed"}, tmp_path)
    assert ready["ready"] is True and ready["runtime_state"] == "ready" and ready["source"] == "fixture"


def test_install_runtime_uses_the_cu128_backend(tmp_path, monkeypatch):
    from test_remote_worker import FakeSSH

    from tuning_factory import desktop
    from tuning_factory import desktop_runtime as runtime

    assets = tmp_path / "assets"
    (assets / "dist").mkdir(parents=True)
    (assets / "containers").mkdir()
    (assets / "dist/llm_tuning_factory-fixture.whl").write_text("fixture")
    (assets / "containers/gpu-requirements.lock").write_text("fixture")
    monkeypatch.setattr(desktop, "assets_root", lambda: assets)
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    scripts = []
    monkeypatch.setattr(runtime, "command", lambda _, argv, **kw: scripts.append(argv[2]) or "")
    preparation = {"started": False, "stopped": False}
    runtime.install_runtime(client, "sft", runtime.REMOTE, POD, preparation)
    assert preparation == {"started": True, "stopped": True}
    assert "--require-hashes --torch-backend cu128 " in scripts[0]



def test_observe_survives_an_unwritable_state_file(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory import gpu_compat

    def locked(root, observation):
        raise PermissionError("state file is locked")

    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(gpu_compat, "save_observation", locked)
    observation = runtime.observe(None, {"pod_id": "borrowed"}, tmp_path, POD)
    assert observation["pod"]["hourly_cost_usd"] == 0.57 and len(observation["gpus"]) == 2


def test_check_rejects_a_probe_line_that_is_not_an_object(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: "" if argv[0] == "sh" else "16")
    with pytest.raises(runtime.RuntimeMismatch, match="preflight mismatch"):
        runtime.check(None)


def runpod_executor_fixture(monkeypatch, states, events):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, SimpleNamespace(close=lambda: None)

    def check(client, stage, *args, **kwargs):
        events.append("check")
        if states.pop(0) == "missing":
            raise runtime.RuntimeMissing(runtime.RUNTIME_MISSING)
        return {}

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", check)
    monkeypatch.setattr(runtime, "prerequisites", lambda client: events.append("prerequisites"))
    monkeypatch.setattr(runtime, "bootstrap_prerequisites", lambda client: events.append("bootstrap"))
    monkeypatch.setattr(runtime, "install_runtime", lambda *args: events.append("install"))
    return runtime


def test_executor_for_gates_first_and_prepares_only_when_approved(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)

    def gate(observation):
        events.append(("gate", observation["gpus"][0]["capability"]))

    with pytest.raises(ValueError, match="학습 환경이 없습니다"):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate):
            pytest.fail("ran without a runtime")
    assert events == [("gate", "12.0"), "check"]
    events.clear()
    runtime = runpod_executor_fixture(monkeypatch, ["missing", "ready"], events)
    with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate, prepare_runtime=True) as executor:
        assert executor.pod == POD
    assert events == [("gate", "12.0"), "check", "prerequisites", "bootstrap", "install", "check"]


def test_a_gate_refusal_stops_before_any_runtime_work(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["ready"], events)

    def gate(observation):
        raise ValueError("실행할 수 없습니다. fixture")

    with pytest.raises(ValueError, match="fixture"):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate, prepare_runtime=True):
            pytest.fail("refused destination used")
    assert events == []


def test_without_a_gate_the_missing_runtime_error_is_unchanged(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)
    with pytest.raises(runtime.RuntimeMissing):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path):
            pytest.fail("ran without a runtime")


def test_execution_preparation_is_runpod_preflight_only(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "connection", lambda *a, **kw: pytest.fail("GPU host contacted"))
    for settings, kwargs in (({"provider": "ssh_server"}, {}), ({"pod_id": "borrowed"}, {"preflight": False})):
        with pytest.raises(ValueError, match="RunPod preflight only"):
            with runtime.executor_for(settings, tmp_path, prepare_runtime=True, **kwargs):
                pytest.fail("prepared outside a RunPod preflight")


@pytest.mark.parametrize("error, state", [("RuntimeMissing", "missing"), ("RuntimeMismatch", "mismatch"),
                                          ("GPUIncompatible", "incompatible"), ("GPUUnavailable", "unavailable")])
def test_runtime_reports_each_check_failure_as_a_state(tmp_path, monkeypatch, error, state):
    from contextlib import contextmanager

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, object()

    def failing(*args, **kwargs):
        raise getattr(runtime, error)("fixture")

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", failing)
    report = runtime.runtime({"pod_id": "borrowed"}, tmp_path)
    assert report["ready"] is False and report["runtime_state"] == state
    assert report["observation"]["gpus"][0]["capability"] == "12.0"


def test_observe_an_ssh_server_keeps_only_the_selected_gpus(tmp_path, monkeypatch):
    from test_gpu_servers import UUID1, UUID2, observation, settings

    from tuning_factory import desktop_runtime as runtime

    config = settings()
    facts = observation(config)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    output = (f"{UUID1}, NVIDIA A100, 8.0, 81920, 550.00\n{UUID2}, NVIDIA A100, 8.0, 81920, 550.00\n")
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: output)
    result = runtime.observe(None, native, tmp_path, facts)
    assert result["key"].startswith("server-") and result["key"] == runtime.observation_key(native)
    assert result["pod"] is None
    assert [gpu["uuid"] for gpu in result["gpus"]] == [UUID2]


@pytest.mark.parametrize("error", ["GPUIncompatible", "GPUUnavailable"])
def test_a_bad_gpu_never_triggers_preparation(tmp_path, monkeypatch, error):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    events = []

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, SimpleNamespace(close=lambda: None)

    def check(client, stage, *args, **kwargs):
        events.append("check")
        raise getattr(runtime, error)("fixture")

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", check)
    for name in ("prerequisites", "bootstrap_prerequisites"):
        monkeypatch.setattr(runtime, name, lambda client, name=name: events.append(name))
    monkeypatch.setattr(runtime, "install_runtime", lambda *args: events.append("install"))
    with pytest.raises(getattr(runtime, error)):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=lambda o: events.append("gate"),
                                  prepare_runtime=True):
            pytest.fail("ran with a bad GPU")
    assert events == ["gate", "check"]


@pytest.mark.parametrize("gated", [True, False])
def test_ssh_server_missing_runtime_is_explained_only_when_gated(tmp_path, monkeypatch, gated):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from test_gpu_servers import UUID1, UUID2, observation, settings

    from tuning_factory import desktop_runtime as runtime

    config = settings()
    facts = observation(config)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    events = []

    @contextmanager
    def connect(_settings, **kwargs):
        yield facts, SimpleNamespace(close=lambda: None)

    def check(client, stage, *args, **kwargs):
        events.append("check")
        raise runtime.RuntimeMissing(runtime.RUNTIME_MISSING)

    output = f"{UUID1}, NVIDIA A100, 8.0, 81920, 550.00\n{UUID2}, NVIDIA A100, 8.0, 81920, 550.00\n"
    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: output)
    monkeypatch.setattr(runtime, "check", check)
    kwargs = {"gate": lambda o: events.append("gate")} if gated else {}
    expected, match = (ValueError, "GPU 서버에 이 앱 버전의 학습 환경이 없습니다") if gated else (
        runtime.RuntimeMissing, None)
    with pytest.raises(expected, match=match):
        with runtime.executor_for(native, tmp_path, **kwargs):
            pytest.fail("ran without a runtime")
    assert events == (["gate", "check"] if gated else ["check"])


def test_without_a_gate_no_gpu_query_runs_and_no_observation_is_stored(tmp_path, monkeypatch):
    events = []
    commands = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: commands.append(argv) or SMI)
    with pytest.raises(runtime.RuntimeMissing):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path):
            pytest.fail("ran without a runtime")
    assert commands == [] and not (tmp_path / "gpu-observations").exists()
    assert list(tmp_path.iterdir()) == []


def test_a_failed_post_preparation_check_is_explained(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)
    calls = []

    def check(client, stage, *args, **kwargs):
        events.append("check")
        calls.append(1)
        raise (runtime.RuntimeMissing if len(calls) == 1 else runtime.RuntimeMismatch)("fixture")

    monkeypatch.setattr(runtime, "check", check)
    with pytest.raises(ValueError, match="학습 환경을 준비했지만 확인을 통과하지 못했습니다"):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=lambda o: None, prepare_runtime=True):
            pytest.fail("ran an unverified runtime")
    assert events.count("install") == 1


def test_a_concurrent_preparation_is_reported_as_busy(tmp_path, monkeypatch):
    from test_remote_worker import FakeSSH

    from tuning_factory import desktop
    from tuning_factory import desktop_runtime as runtime

    assets = tmp_path / "assets"
    (assets / "dist").mkdir(parents=True)
    (assets / "containers").mkdir()
    (assets / "dist/llm_tuning_factory-fixture.whl").write_text("fixture")
    (assets / "containers/gpu-requirements.lock").write_text("fixture")
    monkeypatch.setattr(desktop, "assets_root", lambda: assets)
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    scripts = []

    def busy(_, argv, **kw):
        scripts.append(argv[2])
        raise runtime.RemoteCommandFailed("GPU runtime command failed. TF_PREPARE_BUSY")

    monkeypatch.setattr(runtime, "command", busy)
    preparation = {"started": False, "stopped": False}
    with pytest.raises(ValueError, match="다른 학습 환경 준비가 이 GPU에서 진행 중입니다"):
        runtime.install_runtime(client, "sft", runtime.REMOTE, POD, preparation)
    assert preparation == {"started": True, "stopped": True}
    assert "flock -n 9 || { echo TF_PREPARE_BUSY; exit 75; }" in scripts[0]


def test_ssh_destination_receipt_is_validated_before_the_gate(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from test_gpu_servers import observation, settings

    from tuning_factory import desktop_runtime as runtime

    config = settings()
    facts = observation(config)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    events = []

    @contextmanager
    def connect(_settings, **kwargs):
        yield facts, SimpleNamespace(close=lambda: None)

    class Receipt:
        manifest_sha256 = "x"

        def validate_destination(self, *args):
            events.append("receipt")

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "validate_server_receipt", lambda settings, receipt: receipt)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: "")
    with pytest.raises(ValueError, match="stop here"):
        with runtime.executor_for(native, tmp_path, server_receipt=Receipt(), preflight=False,
                                  gate=lambda o: (events.append("gate"), (_ for _ in ()).throw(
                                      ValueError("stop here")))):
            pytest.fail("refused")
    assert events == ["receipt", "gate"]


@pytest.mark.parametrize("error", ["RuntimeMissing", "RuntimeMismatch"])
def test_runtime_preparation_that_fails_its_check_gets_the_fixed_message(tmp_path, monkeypatch, error):
    from contextlib import contextmanager

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, object()

    def failing(*args, **kwargs):
        raise getattr(runtime, error)("fixture detail")

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "prerequisites", lambda *a: None)
    monkeypatch.setattr(runtime, "bootstrap_prerequisites", lambda *a: None)
    monkeypatch.setattr(runtime, "install_runtime", lambda *a: None)
    monkeypatch.setattr(runtime, "check", failing)
    with pytest.raises(ValueError) as caught:
        runtime.runtime({"pod_id": "borrowed"}, tmp_path, prepare=True)
    assert str(caught.value) == runtime.POST_PREPARATION_FAILED
