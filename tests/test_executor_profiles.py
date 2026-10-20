import importlib
import json
import socket
import traceback
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Profiles and providers are tested with fakes only: no RunPod API, SSH or other connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("executor profile tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def test_aliases_lock_whole_pod_and_price_is_not_multiplied():
    a = ExecutorProfile(profile_id="a", revision="1", name="first", pod_id="pod1", credential_scope="local")
    b = a.model_copy(update={"profile_id": "b"})
    assert a.exclusivity_key == b.exclusivity_key == "existing_runpod:pod1"
    facts = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=4,
                          total_hourly_cost_usd=8, observed_at="2026-10-18T00:00:00Z")
    assert facts.cost(1800) == 4
    assert facts.legacy_resources(1800).hourly_cost_usd == 2


@pytest.mark.parametrize("extra", [{"api_key": "secret"}, {"ssh_path": "/key"}])
def test_profiles_cannot_embed_credentials(extra):
    with pytest.raises(ValueError):
        ExecutorProfile(profile_id="a", revision="1", name="first", pod_id="pod1",
                        credential_scope="local", **extra)


# -- Task 8.4: executor providers ----------------------------------------------------------------

PIN = "SHA256:" + "A" * 43
# Synthetic secrets only, built at runtime so the history-wide secret scan never sees a token-shaped literal.
API_KEY = "_".join(("rpa", "SYNTHETIC", "API", "KEY", "0123456789", "never", "persisted"))
HF_TOKEN = "_".join(("hf", "SYNTHETIC", "TOKEN", "9876543210", "never", "persisted"))
SECRETS = (API_KEY, HF_TOKEN)
LIVE_POD = {"id": "pod1", "desiredStatus": "RUNNING", "publicIp": "192.0.2.10", "portMappings": {"22": 2222},
            "networkVolume": {"id": "volume"}, "volumeMountPath": "/workspace", "costPerHr": 2.0,
            "gpu": {"id": "A100", "displayName": "A100 80GB", "count": 1}}
OBSERVED = 1_800_000_000.0


def api():
    # A missing implementation is a test failure, rather than a collection error.
    module = importlib.import_module("tuning_factory.executor_profiles")
    for name in ("SettingsExecutorProvider", "SimulatedExecutorProvider", "ProfileSecrets", "ResolvedDestination",
                 "ExecutorProvider", "runpod_facts", "runpod_transport_sha256", "PROFILE_CHANGED", "PROFILE_MISSING",
                 "TRANSPORT_CHANGED", "POD_DIFFERS", "SERVER_DIFFERS", "RUNPOD_PRICE_REQUIRED"):
        if not hasattr(module, name):
            pytest.fail(f"executor_profiles.{name} (Task 8.4) is missing")
    return module


def runpod_settings(tmp_path, **changes):
    return {"pod_id": "pod1", "RUNPOD_API_KEY": API_KEY, "HF_TOKEN": HF_TOKEN, "ssh_fingerprint": PIN,
            "ssh_key_file": str(tmp_path / "pod-key"), **changes}


def runpod_profile(ep, settings, **changes):
    return ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local",
                           transport_settings_sha256=ep.runpod_transport_sha256(settings), **changes)


def fake_pod(monkeypatch, pod, connections):
    """The real executor_for over a fake existing-Pod connection: no RunPod API, no SSH."""
    from tuning_factory import desktop_runtime

    @contextmanager
    def connection(settings, **kwargs):
        connections.append(settings["pod_id"])
        yield dict(pod), SimpleNamespace(close=lambda: None)

    monkeypatch.setattr(desktop_runtime, "connection", connection)
    monkeypatch.setattr(desktop_runtime, "check", lambda *args, **kwargs: {})


def ssh_destination(tmp_path, monkeypatch, *, machine_id="b" * 32):
    """A paramiko-shaped fake server (test_gpu_servers.FakeServer) and its pinned public profile."""
    from test_gpu_servers import FakeServer, settings

    from tuning_factory import desktop_runtime
    server = FakeServer(tmp_path / "remote", machine_id=machine_id).install(monkeypatch)
    monkeypatch.setattr(desktop_runtime, "check", lambda *args, **kwargs: {})
    config = settings()
    facts = server.facts(config)
    pinned = config.profile(facts, profile_id="lab", revision="7", name="Lab server", credential_scope="vault")
    native = {"provider": "ssh_server", **config.model_dump(mode="json"), "HF_TOKEN": HF_TOKEN}
    return SimpleNamespace(server=server, config=config, facts=facts, pinned=pinned, native=native)


def provider_for(ep, tmp_path, public, settings):
    return ep.SettingsExecutorProvider({public.profile_id: ep.ProfileSecrets(public=public, settings=settings)},
                                       tmp_path / "state", clock=lambda: OBSERVED)


def refusal(manager):
    with pytest.raises(ValueError) as error:
        with manager:
            pytest.fail("a refused destination resolved")
    return error.value


@pytest.mark.parametrize("case", ["revision", "pinned_revision", "body", "missing", "runpod_key_file",
                                  "runpod_fingerprint", "runpod_pod", "runpod_unpinned", "ssh_root", "ssh_cost"])
def test_provider_refuses_a_changed_profile_before_connecting(tmp_path, monkeypatch, case):
    ep = api()
    from tuning_factory import desktop_runtime
    calls = []
    monkeypatch.setattr(desktop_runtime, "executor_for", lambda *a, **kw: calls.append(a) or pytest.fail("contact"))
    settings = runpod_settings(tmp_path)
    public = runpod_profile(ep, settings)
    pinned, approved, profile_id, message = public, "1", "p", ep.PROFILE_CHANGED
    if case == "revision":
        approved = "2"
    elif case == "pinned_revision":
        pinned, approved = public.model_copy(update={"revision": "2"}), "2"
    elif case == "body":
        pinned = public.model_copy(update={"name": "Renamed"})
    elif case == "missing":
        profile_id, message = "other", ep.PROFILE_MISSING
    elif case.startswith("runpod"):
        message = ep.TRANSPORT_CHANGED
        changed = {"runpod_key_file": {"ssh_key_file": str(tmp_path / "other-key")},
                   "runpod_fingerprint": {"ssh_fingerprint": "SHA256:" + "B" * 43},
                   "runpod_pod": {"pod_id": "pod2"}, "runpod_unpinned": {}}[case]
        settings = runpod_settings(tmp_path, **changed)
        if case == "runpod_unpinned":
            public = pinned = public.model_copy(update={"transport_settings_sha256": None})
    else:
        destination = ssh_destination(tmp_path, monkeypatch)
        public = pinned = destination.pinned
        profile_id, approved, message = "lab", "7", ep.TRANSPORT_CHANGED
        update = {"ssh_root": {"remote_root": "/home/researcher/other"},
                  "ssh_cost": {"total_hourly_cost_usd": 3}}[case]
        settings = {**destination.native, **update}
    provider = provider_for(ep, tmp_path, public, settings)
    for manager in (provider.resolve(profile_id, approved, pinned=pinned),
                    provider.recover(profile_id, approved, pinned=pinned, pinned_facts=None, manifest_sha256="1" * 64)):
        assert str(refusal(manager)) == message
    assert calls == []


@pytest.mark.parametrize("case", ["live", "other_pod", "zero_price", "missing_price", "nonfinite_price",
                                  "ssh_live", "ssh_other_server"])
def test_provider_compares_the_observed_canonical_pod(tmp_path, monkeypatch, case):
    ep = api()
    from tuning_factory.executor_profiles import observation_fits, observation_key
    if case.startswith("ssh"):
        destination = ssh_destination(tmp_path, monkeypatch)
        if case == "ssh_other_server":
            destination.server.machine_id = "c" * 32   # the same transport now reaches another machine
        provider = provider_for(ep, tmp_path, destination.pinned, destination.native)
        manager = provider.resolve("lab", "7", pinned=destination.pinned, runtime_profile="primary")
        if case == "ssh_other_server":
            assert str(refusal(manager)) == ep.SERVER_DIFFERS
            return
        with manager as resolved:
            assert resolved.profile == destination.pinned and resolved.executor.server_profile == destination.pinned
            assert observation_key(resolved.observation) == observation_key(destination.pinned)
            assert observation_fits(resolved.observation, destination.facts)
        return
    pod = {"live": LIVE_POD, "other_pod": {**LIVE_POD, "id": "pod2"}, "zero_price": {**LIVE_POD, "costPerHr": 0},
           "missing_price": {key: value for key, value in LIVE_POD.items() if key != "costPerHr"},
           "nonfinite_price": {**LIVE_POD, "costPerHr": float("nan")}}[case]
    connections = []
    fake_pod(monkeypatch, pod, connections)
    settings = runpod_settings(tmp_path)
    public = runpod_profile(ep, settings)
    manager = provider_for(ep, tmp_path, public, settings).resolve("p", "1", pinned=public, runtime_profile="primary")
    if case != "live":
        message = ep.POD_DIFFERS if case == "other_pod" else ep.RUNPOD_PRICE_REQUIRED
        assert str(refusal(manager)) == message
        return
    with manager as resolved:
        assert resolved.profile == public and resolved.executor.pod["id"] == "pod1"
        assert resolved.observation == ResourceFacts(
            pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2.0,
            observed_at=datetime.fromtimestamp(OBSERVED, timezone.utc))
        assert observation_key(resolved.observation) == observation_key(public)
    assert connections == ["pod1"]


@pytest.mark.parametrize("change,message", [
    ({"id": None}, "Pod ID"), ({"id": "../pod"}, "Pod ID"), ({"gpu": None}, "GPU type"),
    ({"gpu": {"id": "", "count": 1}}, "GPU type"), ({"gpu": {"id": "A100", "count": 0}}, "GPU type"),
    ({"gpu": {"id": "A100", "count": True}}, "GPU type"), ({"gpu": {"id": "A100", "count": "1"}}, "GPU type"),
    ({"gpu": {"id": "A100", "count": 9}}, "GPU type"), ({"costPerHr": 0}, "price"), ({"costPerHr": -1}, "price"),
    ({"costPerHr": float("inf")}, "price"), ({"costPerHr": float("nan")}, "price"), ({"costPerHr": None}, "price"),
    ({"costPerHr": "cheap"}, "price"), ({"costPerHr": True}, "price")])
def test_runpod_facts_from_a_live_pod(change, message):
    ep = api()
    observed = datetime(2026, 10, 20, 9, 30, tzinfo=timezone(timedelta(hours=9)))
    facts = ep.runpod_facts(LIVE_POD, observed_at=observed)
    assert facts == ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2.0,
                                  observed_at=observed)
    assert facts.observed_at.utcoffset() == timedelta(hours=9)   # the observation keeps its explicit timezone
    assert ep.runpod_facts({**LIVE_POD, "costPerHr": "2.5"}, observed_at=observed).total_hourly_cost_usd == 2.5
    with pytest.raises(ValueError, match="explicit timezone"):
        ep.runpod_facts(LIVE_POD, observed_at=datetime(2026, 10, 20, 9, 30))
    with pytest.raises(ValueError, match=message) as error:
        ep.runpod_facts({**LIVE_POD, **change}, observed_at=observed)
    if message == "price":
        assert str(error.value) == ep.RUNPOD_PRICE_REQUIRED == "positive finite live RunPod API price required"


def test_runpod_transport_hash_covers_the_public_route_and_never_the_api_key(tmp_path):
    ep = api()
    settings = runpod_settings(tmp_path)
    reference = ep.runpod_transport_sha256(settings)
    assert ep.runpod_transport_sha256({**settings, "RUNPOD_API_KEY": "rotated", "HF_TOKEN": "x"}) == reference
    assert ep.runpod_transport_sha256({**settings, "provider": "existing_runpod"}) == reference
    for change in ({"pod_id": "pod2"}, {"ssh_fingerprint": "SHA256:" + "C" * 43},
                   {"ssh_key_file": str(tmp_path / "other")}):
        assert ep.runpod_transport_sha256({**settings, **change}) != reference
    for change in ({"provider": "ssh_server"}, {"pod_id": ""}, {"ssh_fingerprint": "SHA256:short"},
                   {"ssh_key_file": " "}, {"ssh_key_file": "bad\nname"}):
        with pytest.raises(ValueError) as error:
            ep.runpod_transport_sha256({**settings, **change})
        assert API_KEY not in str(error.value)


def scanned(directory):
    """Every byte under ``directory``: jobs.db, launch files, records and error texts."""
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in directory.rglob("*") if path.is_file()}


def test_provider_secrets_stay_in_memory(tmp_path, monkeypatch):
    ep = api()
    from test_operation_runner import EXECUTOR, build_graph

    from tuning_factory.attempt_runner import OperationAttemptRunner
    from tuning_factory.execution import SSHExecutor
    from tuning_factory.worker_protocol import OperationResult
    settings = runpod_settings(tmp_path)
    public = EXECUTOR.model_copy(update={"transport_settings_sha256": ep.runpod_transport_sha256(settings)})
    w = build_graph(tmp_path, ("train",), executor=public)
    state = tmp_path / "state"
    connections = []
    fake_pod(monkeypatch, LIVE_POD, connections)

    def remote_failure(self, job, launch, request, transfers, canceled):
        return OperationResult(status="FAILED", launch_id=request.launch_id, attempt_id=request.attempt_id,
                               manifest_sha256=request.manifest_sha256, operation_sha256=request.operation_sha256,
                               kind="train", error="synthetic remote failure").model_dump(mode="json")

    monkeypatch.setattr(SSHExecutor, "run_operation", remote_failure)
    secrets = ep.ProfileSecrets(public=public, settings=settings)
    provider = ep.SettingsExecutorProvider({"p": secrets}, state, clock=lambda: OBSERVED)
    with provider.resolve("p", "1", pinned=public, runtime_profile="primary") as resolved:
        claimed = w.store.claim_ready(resolved.observation, w.ctl)
        outcome = OperationAttemptRunner(w.store, root=state).run_claimed(claimed, resolved.executor,
                                                                          approval=w.approval)
    assert outcome.status == "FAILED" and outcome.error == "synthetic remote failure" and connections == ["pod1"]
    errors = []
    for pinned, provider_settings in (
            (public.model_copy(update={"name": "Renamed"}), settings),
            (public, {**settings, "ssh_fingerprint": "SHA256:" + "B" * 43}),
            (public, {**settings, "ssh_fingerprint": API_KEY}),
            (public, {**settings, "pod_id": API_KEY})):
        other = ep.SettingsExecutorProvider({"p": ep.ProfileSecrets(public=public, settings=provider_settings)},
                                            state)
        errors.append(refusal(other.resolve("p", "1", pinned=pinned)))
    fake_pod(monkeypatch, {**LIVE_POD, "costPerHr": 0}, connections)
    errors.append(refusal(provider.resolve("p", "1", pinned=public)))
    texts = [str(secrets), repr(secrets), repr(provider), json.dumps(provider.status()), str(resolved),
             *("".join(traceback.format_exception(error)) for error in errors)]
    files = scanned(state)
    assert "jobs.db" in files and any(name.endswith("error.txt") for name in files)
    for secret in SECRETS:
        assert all(secret not in text for text in texts)
        assert all(secret.encode() not in data for data in files.values())
    assert provider.status() == {"provider": "settings", "profiles": [
        {"profile_id": "p", "revision": "1", "provider": "existing_runpod", "resource_id": "existing_runpod:pod1"}]}
    assert dict(secrets.settings) == settings   # the settings themselves stay usable in memory
    with pytest.raises(TypeError):
        secrets.settings["RUNPOD_API_KEY"] = "changed"
