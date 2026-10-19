"""Software-only direct SSH contracts; every remote response is a local fixture."""
import json
from datetime import datetime, timezone

import pytest

UUID1 = "GPU-11111111-1111-1111-1111-111111111111"
UUID2 = "GPU-22222222-2222-2222-2222-222222222222"
PIN = "SHA256:" + "A" * 43


@pytest.fixture(autouse=True)
def no_tracking_connections(monkeypatch):
    """These SSH fixtures never exercise external tracking transports."""
    monkeypatch.setattr("tuning_factory.tracking.LazyMLflowTracker",
                        lambda *a, **kw: pytest.fail("unexpected tracking connection in SSH fixture"))


def settings(**updates):
    from tuning_factory.gpu_servers import SSHServerSettings

    return SSHServerSettings(ssh_host="gpu.example", ssh_username="researcher", ssh_key_file="test-key",
                             ssh_fingerprint=PIN, remote_root="/home/researcher/factory",
                             selected_gpu_uuids=(UUID2,), total_hourly_cost_usd=0, **updates)


def observation(config=None, **updates):
    from tuning_factory.gpu_servers import GPUDevice, SSHServerFacts

    config = config or settings()
    body = dict(server_id="a" * 64, transport_settings_sha256=config.transport.sha256,
                inventory=(GPUDevice(uuid=UUID1, name="A100", memory_mib=81920),
                           GPUDevice(uuid=UUID2, name="A100", memory_mib=81920)),
                selected_gpu_uuids=config.selected_gpu_uuids, gpu_type="A100", gpu_count=1,
                total_hourly_cost_usd=0, observed_at=datetime.now(timezone.utc))
    return SSHServerFacts(**(body | updates))


def test_public_server_profile_and_transport_bind_destination_without_credentials():
    config = settings()
    profile = config.profile(observation(), profile_id="lab", revision="1")
    assert profile.provider == "ssh_server"
    assert profile.exclusivity_key == "ssh_server:" + "a" * 64
    public = profile.model_dump_json()
    assert "test-key" not in public and "pod_id" not in public
    changed = config.model_copy(update={"remote_root": "/home/researcher/other"})
    assert changed.transport.sha256 != config.transport.sha256
    assert observation().legacy_resources(60).hourly_cost_usd == 0


@pytest.mark.parametrize("field,value", [
    ("ssh_host", "gpu;touch bad"), ("ssh_username", "u\ncommand"), ("ssh_port", 0),
    ("ssh_fingerprint", "SHA256:wrong"), ("remote_root", "/home/u/../other"),
    ("remote_root", "/home/u;echo bad"), ("remote_root", "/"),
    ("selected_gpu_uuids", ("0",)), ("selected_gpu_uuids", (UUID1, UUID1)),
    ("total_hourly_cost_usd", float("inf")), ("total_hourly_cost_usd", -1),
])
def test_server_settings_reject_unsafe_or_ambiguous_inputs(field, value):
    from tuning_factory.gpu_servers import SSHServerSettings

    with pytest.raises(ValueError):
        SSHServerSettings.model_validate(settings().model_dump() | {field: value})


def test_fixed_readonly_probe_selects_actual_uuid_and_rejects_missing_gpu():
    from tuning_factory.gpu_servers import probe_server

    calls = []

    def run(client, argv, **kwargs):
        calls.append(argv)
        if argv[0] == "nvidia-smi":
            return f"{UUID1}, A100, 81920, Disabled\n{UUID2}, A100, 81920, Disabled\n"
        return json.dumps(dict(system="Linux", machine_id="b" * 32, python=[3, 12, 9],
                               tools={"flock": True, "cc": True}, workspace_safe=True, workspace_writable=True))

    facts = probe_server(None, settings(), run)
    assert facts.selected_gpu_uuids == (UUID2,) and facts.gpu_count == 1
    assert facts.cost_source == "operator" and facts.total_hourly_cost_usd == 0
    assert len(calls) == 2 and all("pip" not in arg for call in calls for arg in call)
    bad = settings().model_copy(update={"selected_gpu_uuids": ("GPU-33333333-3333-3333-3333-333333333333",)})
    with pytest.raises(ValueError, match="selected GPU"):
        probe_server(None, bad, run)


def test_gpu_config_mismatch_and_zero_cost_policy_need_verified_server_context():
    from tuning_factory.config import load_config
    from tuning_factory.execution import Policy, SSHExecutor

    config = load_config("configs/ai/paper_extraction.yaml")
    config = config.model_copy(update={"resource_profile": config.resource_profile.model_copy(
        update={"gpu_type": "A100", "hourly_cost_usd": 0})})
    executor = SSHExecutor("", 22, "researcher", "", client=object(), server_profile=settings().profile(
        observation(), profile_id="lab", revision="1"), server_facts=observation())
    executor.validate_config(config)
    Policy().check(config, {"export_allowed": True}, execute=True, approved_transfer=True,
                   server_facts=executor.server_facts)
    with pytest.raises(ValueError, match="positive"):
        Policy().check(config, {"export_allowed": True}, execute=True, approved_transfer=True)
    with pytest.raises(ValueError, match="GPU count"):
        executor.validate_config(config.model_copy(update={"resource_profile": config.resource_profile.model_copy(
            update={"gpu_count": 2})}))


def test_ssh_connection_uses_nonroot_and_never_runpod(monkeypatch):
    from tuning_factory import desktop_runtime

    class Client:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, host, **kwargs):
            assert host == "gpu.example" and kwargs["username"] == "researcher"
            assert kwargs["key_filename"] == "test-key"

        def close(self):
            pass

    monkeypatch.setattr(desktop_runtime.paramiko, "SSHClient", Client)
    monkeypatch.setattr(desktop_runtime, "RunPodController", lambda *a, **k: pytest.fail("RunPod called"))
    monkeypatch.setattr(desktop_runtime, "probe_server", lambda client, config, run: observation(config))
    with desktop_runtime.connection({"provider": "ssh_server", **settings().model_dump(mode="json")}) as (facts, _):
        assert facts.provider == "ssh_server"


def test_destination_receipt_is_bound_to_manifest_and_edited_settings():
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    config = settings()
    facts = observation(config)
    receipt = ServerApprovalReceipt.create("1" * 64, config.profile(facts, profile_id="lab", revision="1"), facts)
    receipt.validate_destination("1" * 64, config.transport, facts)
    for changed in (config.model_copy(update={"ssh_host": "other.example"}),
                    config.model_copy(update={"remote_root": "/home/researcher/other"}),
                    config.model_copy(update={"selected_gpu_uuids": (UUID1,)})):
        with pytest.raises(ValueError, match="destination"):
            receipt.validate_destination("1" * 64, changed.transport, observation(changed))
    with pytest.raises(ValueError, match="manifest"):
        receipt.validate_destination("2" * 64, config.transport, facts)


def test_alias_claims_and_unknown_or_cross_provider_locks_are_conservative(tmp_path):
    from test_graph_store import queued

    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    queued(store, "one")
    queued(store, "two")
    first = store.claim(observation().exclusivity_key, max_concurrent=4)
    assert first
    assert store.claim(observation().exclusivity_key, max_concurrent=4) is None
    assert store.claim("existing_runpod:could-alias-same-server", max_concurrent=4) is None
    store.transition(first["lease"], "FAILED")
    store.release_gpu(first["lease"], execution_stopped=True)
    unknown = store.claim("unknown-old-resource", max_concurrent=4)
    queued(store, "three")
    assert store.claim(observation().exclusivity_key, max_concurrent=4) is None
    assert store.gpu_locked(unknown["lease"].resource_id)


def test_graph_plan_and_claim_accept_truthful_server_facts(tmp_path):
    from test_graph_planner import fixture

    from tuning_factory.graph_planner import ApprovalGrant, plan_graph
    from tuning_factory.graph_store import GraphStore

    registry, project, choices, _ = fixture(tmp_path)
    facts = observation()
    original = choices.executors[0]
    profile = settings().profile(facts, profile_id=original.profile_id, revision="1")
    choices = choices.model_copy(update={"executors": (profile,)})
    plan = plan_graph(project, choices, (facts,))
    assert plan.max_estimated_cost_usd == 0
    assert "pod_id" not in plan.model_dump_json()
    store = GraphStore(tmp_path / "jobs.db", registry=registry)
    run = store.submit(plan, "server-graph", ApprovalGrant.for_plan(plan))
    lease = store.acquire_controller(run["id"])
    store.prepare_ready(lease)
    claimed = store.claim_ready(facts, lease)
    assert claimed and claimed["lease"].resource_id == facts.exclusivity_key


def test_native_strings_keep_provider_keys_independent_and_key_changes_invalidate_identity():
    from tuning_factory.gpu_servers import server_settings

    native = {"provider": "ssh_server", "ssh_key_file": "old-pod-key", "ssh_fingerprint": "old-pod-pin"}
    native.update({"server_" + key: (json.dumps(value) if isinstance(value, list) else str(value))
                   for key, value in settings().model_dump(mode="json").items()})
    config = server_settings(native)
    assert config == settings()
    assert native["ssh_key_file"] == "old-pod-key" and native["ssh_fingerprint"] == "old-pod-pin"
    changed = config.model_copy(update={"ssh_key_file": "another-key"})
    assert config.transport.sha256 != changed.transport.sha256


def test_readonly_inspection_can_list_inventory_before_selection(monkeypatch):
    from tuning_factory import desktop_runtime

    monkeypatch.setattr(desktop_runtime, "command", lambda _, argv, **kw:
        f"{UUID1}, A100, 81920, Disabled\n" if argv[0] == "nvidia-smi" else json.dumps(dict(
            system="Linux", machine_id="b" * 32, python=[3, 12, 9], tools={"flock": True, "cc": True},
            workspace_safe=True, workspace_writable=True)))
    class Client:
        def set_missing_host_key_policy(self, policy):
            pass

        def connect(self, *a, **kw):
            pass

        def close(self):
            pass

    monkeypatch.setattr(desktop_runtime.paramiko, "SSHClient", Client)
    native = {"provider": "ssh_server", **settings().model_dump(mode="json"), "selected_gpu_uuids": []}
    result = desktop_runtime.inspect_server(native)
    assert result.inventory[0].uuid == UUID1 and result.selected_gpu_uuids == ()
    assert result.cost_source == "operator"
    assert "test-key" not in result.model_dump_json()


def test_actual_worker_launch_binds_selected_uuid_environment(tmp_path):
    import shlex
    from types import SimpleNamespace

    from test_remote_worker import FakeSSH

    from tuning_factory.execution import SSHExecutor
    from tuning_factory.training import LlamaFactoryBackend

    class ServerSSH(FakeSSH):
        def exec_command(self, command, timeout):
            self.commands.append(command)
            args = shlex.split(command)
            assert "CUDA_VISIBLE_DEVICES=" + UUID2 in args
            remote = args[args.index("tuning_factory.worker") + 1]
            directory = self.sftp.path(remote)
            (directory / "artifacts.json").write_text("{}")
            (directory / "result.json").write_text('{"status":"COMPLETED"}')
            return None, SimpleNamespace(channel=SimpleNamespace(recv_exit_status=lambda: 0)), None

    client = ServerSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    directory = tmp_path / ("c" * 32)
    directory.mkdir()
    executor = SSHExecutor("", 22, "researcher", "", client=client, server_profile=settings().profile(
        observation()), server_facts=observation(), remote_root=settings().remote_root + "/jobs")
    executor.run(LlamaFactoryBackend(), directory, 5, lambda: False, launch_id="d" * 32)
    receipt = json.loads((directory / "remote-attempt.json").read_text())
    assert receipt["destination"]["provider"] == "ssh_server" and "pod_id" not in receipt


@pytest.mark.parametrize("change", [dict(ssh_host="different.example"), dict(remote_root="/home/u/other"),
                                    dict(selected_gpu_uuids=(UUID1,)), dict(ssh_key_file="other-key"),
                                    dict(ssh_fingerprint="SHA256:" + "B" * 43)])
def test_old_logs_and_reconciliation_refuse_edited_destinations_before_transfer(tmp_path, monkeypatch, change):
    from tuning_factory import desktop_runtime
    from tuning_factory.execution import SSHExecutor

    directory = tmp_path / ("e" * 32)
    directory.mkdir()
    config = settings()
    record = {"remote_path": config.remote_root + "/jobs/" + directory.name + "/" + "f" * 32,
              "destination": config.profile(observation()).model_dump(mode="json")}
    (directory / "remote-attempt.json").write_text(json.dumps(record))
    changed = config.model_copy(update=change)
    native = {"provider": "ssh_server", **changed.model_dump(mode="json")}
    monkeypatch.setattr(desktop_runtime, "connection", lambda _: pytest.fail("edited destination contacted"))
    for action in (lambda: desktop_runtime.live_logs(native, tmp_path, directory),
                   lambda: desktop_runtime.read_progress(native, directory)):
        with pytest.raises(ValueError, match="destination"):
            action()
    executor = SSHExecutor("", 22, "researcher", "", client=object(), server_profile=changed.profile(
        observation(changed)), server_facts=observation(changed), remote_root=changed.remote_root + "/jobs")
    with pytest.raises(ValueError, match="destination"):
        executor.collect_result(directory)


def test_external_job_receipt_survives_reload_and_rejects_edited_claim(tmp_path):
    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    receipt = ServerApprovalReceipt.create("1" * 64, settings().profile(observation()), observation())
    job = store.submit({"sha256": "1" * 64, "simulation": False,
                        "config": {"resource_profile": {"gpu_count": 1}}}, "receipt", server_receipt=receipt)
    store.transition(job["id"], "QUEUED")
    store = JobStore(store.path)
    assert store.server_receipt(job["id"]) == receipt
    changed = settings().model_copy(update={"remote_root": "/home/researcher/other"})
    with pytest.raises(ValueError, match="destination"):
        store.claim(observation().exclusivity_key, server_profile=changed.profile(observation(changed)),
                    server_facts=observation(changed), job_id=job["id"])
    assert store.get(job["id"])["attempts"] == 0
    assert store.claim(observation().exclusivity_key, server_profile=receipt.profile,
                       server_facts=observation(), job_id=job["id"])


def test_receipt_accepts_fresh_timestamp_but_rejects_changed_hardware_inventory():
    from tuning_factory.gpu_servers import GPUDevice, ServerApprovalReceipt

    facts = observation(observed_at="2026-10-18T01:00:00Z")
    receipt = ServerApprovalReceipt.create("1" * 64, settings().profile(facts), facts)
    receipt.validate_destination("1" * 64, settings().transport,
                                 observation(observed_at="2026-10-19T02:00:00Z"))
    changed = observation(inventory=(GPUDevice(uuid=UUID1, name="H100", memory_mib=81920),
                                    GPUDevice(uuid=UUID2, name="A100", memory_mib=81920)))
    with pytest.raises(ValueError, match="destination"):
        receipt.validate_destination("1" * 64, settings().transport, changed)


def test_repeat_server_submit_retains_original_receipt_for_same_stable_destination(tmp_path):
    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    facts = observation(observed_at="2026-10-18T00:00:00Z")
    first = ServerApprovalReceipt.create("1" * 64, settings().profile(facts), facts)
    manifest = {"sha256": "1" * 64, "simulation": False}
    job = store.submit(manifest, "same", server_receipt=first)
    fresh = observation(observed_at="2026-10-19T00:00:00Z")
    second = ServerApprovalReceipt.create("1" * 64, settings().profile(fresh), fresh)
    assert store.submit(manifest, "same", server_receipt=second)["id"] == job["id"]
    assert store.server_receipt(job["id"]) == first


def test_factory_zero_cost_server_job_requires_receipt_and_verifies_before_launch(tmp_path):
    from tuning_factory.config import load_config
    from tuning_factory.execution import SSHExecutor
    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.manifest import resolve
    from tuning_factory.runner import Factory
    from tuning_factory.training import LlamaFactoryBackend

    factory = Factory(tmp_path)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True)
    config = load_config("configs/ai/paper_extraction.yaml")
    config = config.model_copy(update={"resource_profile": config.resource_profile.model_copy(
        update={"gpu_type": "A100", "hourly_cost_usd": 0})})
    manifest = resolve(config, factory.registry, simulation=False)
    receipt = ServerApprovalReceipt.create(manifest["sha256"], settings().profile(observation()), observation())
    job = factory.submit(config, simulation=False, server_receipt=receipt)

    class SoftwareOnlyServer(SSHExecutor):
        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            assert launch_id
            (directory / "train.log").write_text("software transport fixture")

    executor = SoftwareOnlyServer("", 22, "researcher", "", client=object(), server_profile=receipt.profile,
                                  server_facts=observation())
    result = factory.work(executor=executor, gpu=observation().exclusivity_key,
                          approved_transfer=True, target_job_id=job["id"], backend=LlamaFactoryBackend())
    assert result["status"] == "COMPLETED", result["error"]
    assert factory.store.server_receipt(job["id"]) == receipt
    evidence = json.loads((factory.directory(job["id"]) / "execution.json").read_text())
    assert evidence["cost_source"] == "operator" and evidence["total_hourly_cost_usd"] == 0


def test_connection_profile_ssh_ignores_inactive_runpod_credentials(tmp_path, monkeypatch):
    from tuning_factory.connections import load_connection

    path = tmp_path / "connection.json"
    path.write_text(json.dumps(dict(tracking_uri="https://tracking.example", provider="ssh_server",
        ssh_server=settings().model_dump(mode="json"), runpod_env_file="missing-inactive-runpod.env")))
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", "")
    profile = load_connection(path)
    assert profile.ssh_server.ssh_username == "researcher"
    assert profile.ssh_server.ssh_key_file == (tmp_path / "test-key").resolve()
    import os
    assert "RUNPOD_API_KEY" not in os.environ


def test_cli_server_inspection_and_diagnostics_need_no_runpod_key_or_tracking(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from tuning_factory import desktop_runtime
    from tuning_factory.cli import app
    from tuning_factory.gpu_servers import SSHServerInspection

    config = settings()
    facts = observation(config)
    result = SSHServerInspection(server_id=facts.server_id, transport=config.transport,
        inventory=facts.inventory, total_hourly_cost_usd=0, observed_at=facts.observed_at)
    monkeypatch.setattr(desktop_runtime, "inspect_server", lambda _: result)
    monkeypatch.delenv("RUNPOD_API_KEY", raising=False)
    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    path = tmp_path / "server.json"
    path.write_text(config.model_dump_json())
    runner = CliRunner()
    for args in (["server-inspect", str(path)], ["doctor", "--server-profile", str(path)]):
        invocation = runner.invoke(app, ["--state-dir", str(tmp_path / "state"), *args])
        assert invocation.exit_code == 0, invocation.output
        assert UUID2 in invocation.output and "operator" in invocation.output


def test_server_runtime_preparation_requires_same_inspection_and_unlocked_server(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from test_graph_store import queued

    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_servers import SSHServerInspection
    from tuning_factory.jobs import JobStore

    config = settings()
    facts = observation(config)
    inspected = SSHServerInspection(server_id=facts.server_id, transport=config.transport,
        inventory=facts.inventory, total_hourly_cost_usd=0, observed_at=facts.observed_at)
    @contextmanager
    def connect(_):
        yield facts, object()

    monkeypatch.setattr(desktop_runtime, "connection", connect)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    with pytest.raises(ValueError, match="inspection"):
        desktop_runtime.runtime(native, tmp_path, prepare=True)
    changed = native | {"remote_root": "/home/researcher/other"}
    with pytest.raises(ValueError, match="destination"):
        desktop_runtime.runtime(changed, tmp_path, prepare=True, inspection=inspected)
    store = JobStore(tmp_path / "jobs.db")
    queued(store, "blocked")
    store.claim("unknown-existing-lock")
    with pytest.raises(ValueError, match="reserved|uncertain"):
        desktop_runtime.runtime(native, tmp_path, prepare=True, inspection=inspected)


@pytest.mark.parametrize("field,value", [("system", "Windows"), ("machine_id", "unknown"),
    ("workspace_safe", False), ("workspace_writable", False), ("tools", {"flock": True, "cc": False})])
def test_inspection_refuses_unsupported_or_unsafe_workspaces(field, value):
    from tuning_factory.gpu_servers import probe_server

    response = dict(system="Linux", machine_id="b" * 32, python=[3, 12, 9],
                    tools={"flock": True, "cc": True}, workspace_safe=True, workspace_writable=True)
    with pytest.raises(ValueError):
        probe_server(None, settings(), lambda *_: json.dumps(response | {field: value}))


def test_mig_inventory_is_explicitly_unsupported():
    from tuning_factory.gpu_servers import probe_server

    def run(_, argv):
        if argv[0] == "nvidia-smi":
            return f"{UUID1}, A100, 81920, Enabled\n"
        return json.dumps(dict(system="Linux", machine_id="b" * 32, python=[3, 12, 9],
                               tools={"flock": True, "cc": True}, workspace_safe=True, workspace_writable=True))

    with pytest.raises(ValueError, match="MIG|partition"):
        probe_server(None, settings(), run)


def test_gpu_inventory_order_does_not_change_identity_or_receipt():
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    original = observation()
    receipt = ServerApprovalReceipt.create("1" * 64, settings().profile(original), original)
    reordered = observation(inventory=tuple(reversed(original.inventory)))
    receipt.validate_destination("1" * 64, settings().transport, reordered)


def test_observed_gpu_overlap_blocks_alias_with_different_machine_identity(tmp_path):
    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    first_facts = observation()
    second_facts = observation(server_id="b" * 64)
    profiles = [settings().profile(item) for item in (first_facts, second_facts)]
    jobs = []
    for index, (profile, facts) in enumerate(zip(profiles, (first_facts, second_facts), strict=True)):
        sha = str(index + 1) * 64
        receipt = ServerApprovalReceipt.create(sha, profile, facts)
        job = store.submit({"sha256": sha, "simulation": False,
            "config": {"resource_profile": {"gpu_count": 1}}}, sha, server_receipt=receipt)
        store.transition(job["id"], "QUEUED")
        jobs.append(job)
    assert store.claim(first_facts.exclusivity_key, max_concurrent=4, job_id=jobs[0]["id"],
                       server_profile=profiles[0], server_facts=first_facts)
    assert store.claim(second_facts.exclusivity_key, max_concurrent=4, job_id=jobs[1]["id"],
                       server_profile=profiles[1], server_facts=second_facts) is None


@pytest.mark.parametrize("training_stage", ["sft", "ppo"])
def test_cli_work_freezes_supplied_server_route_without_provider_api(tmp_path, monkeypatch, training_stage):
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from tuning_factory import desktop_runtime
    from tuning_factory.cli import app
    from tuning_factory.config import AIConfig, load_config
    from tuning_factory.connections import load_server_settings
    from tuning_factory.execution import SSHExecutor
    from tuning_factory.runner import Factory

    path = tmp_path / "server.json"
    path.write_text(settings().model_dump_json())
    config = load_config("configs/ai/paper_extraction.yaml")
    config = config.model_copy(update={"resource_profile": config.resource_profile.model_copy(
        update={"gpu_type": "A100", "hourly_cost_usd": 0})})
    if training_stage == "ppo":
        value = config.model_dump()
        value.update(training_stage="ppo", stage_options={"reward_model": "/home/researcher/reward",
            "reward_model_type": "full", "reward_model_sha256": "a" * 64})
        config = AIConfig.model_validate(value)
    factory = Factory(tmp_path / "state")
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True,
                                      kind="prompt" if training_stage == "ppo" else "instruction")
    job = factory.submit(config, simulation=False)
    class SoftwareServer(SSHExecutor):
        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            assert launch_id

    @contextmanager
    def prepared(settings, root, **kwargs):
        assert kwargs.get("training_stage", "sft") == training_stage
        server = load_server_settings(path)
        facts = observation(server)
        yield SoftwareServer("", 22, "researcher", "", client=object(), server_profile=server.profile(facts),
                             server_facts=facts)

    monkeypatch.setattr(desktop_runtime, "executor_for", prepared)
    @contextmanager
    def observed(_):
        yield observation(load_server_settings(path)), object()

    monkeypatch.setattr(desktop_runtime, "connection", observed)
    monkeypatch.setattr("tuning_factory.cli.RunPodController", lambda *a, **k: pytest.fail("RunPod called"))
    result = CliRunner().invoke(app, ["--state-dir", str(factory.root), "work", "--server-profile", str(path),
                                    "--approve-execution-and-transfer"])
    assert result.exit_code == 0, result.output
    assert factory.store.get(job["id"])["status"] == "COMPLETED"
    assert factory.store.server_receipt(job["id"]).profile.provider == "ssh_server"


@pytest.mark.parametrize("price", [0, -1, float("nan"), float("inf"), None])
def test_runpod_requires_positive_finite_live_api_price(price):
    from tuning_factory.config import load_config
    from tuning_factory.execution import SSHExecutor

    config = load_config("configs/ai/paper_extraction.yaml")
    resource = config.resource_profile
    pod = {"gpu": {"id": resource.gpu_type, "count": resource.gpu_count}}
    if price is not None:
        pod["costPerHr"] = price
    executor = SSHExecutor("", 22, "root", "", client=object(), pod=pod)
    with pytest.raises(ValueError, match="price"):
        executor.validate_config(config)


def test_server_executor_needs_inspected_transport_before_any_connect(monkeypatch):
    from tuning_factory import execution

    monkeypatch.setattr(execution.paramiko, "SSHClient", lambda: pytest.fail("uninspected network path"))
    with pytest.raises(ValueError, match="inspected"):
        execution.SSHExecutor("unverified.example", 22, "researcher", "test-key",
                              server_profile=settings().profile(observation()), server_facts=observation())


def test_server_executor_accepts_safe_dotted_private_directory():
    from tuning_factory.execution import SSHExecutor

    config = settings().model_copy(update={"remote_root": "/home/researcher/.factory/v1.2"})
    facts = observation(config)
    executor = SSHExecutor("", 22, "researcher", "", client=object(), server_profile=config.profile(facts),
                           server_facts=facts, remote_root=config.remote_root + "/jobs")
    assert executor.root == config.remote_root + "/jobs"


@pytest.mark.parametrize("key", ["", "\n", "path\x00bad"])
def test_server_key_reference_must_be_a_nonempty_local_path(key):
    from tuning_factory.gpu_servers import SSHServerSettings

    with pytest.raises(ValueError):
        SSHServerSettings.model_validate(settings().model_dump() | {"ssh_key_file": key})


def test_graph_gpu_bound_uses_largest_selection_for_server_aliases(tmp_path):
    from test_graph_planner import fixture

    from tuning_factory.graph_planner import plan_graph
    from tuning_factory.projects import Revision
    from tuning_factory.registry import canonical, digest

    _, project, choices, _ = fixture(tmp_path)
    graph = json.loads(json.dumps(project.spec.graph))
    graph["nodes"][0]["executor_profile_ids"] = ["large", "small"]
    spec = project.spec.model_copy(update={"graph": graph})
    project = Revision(spec=spec, revision_sha256=digest(canonical(spec.model_dump(mode="json"))))
    small = settings()
    large = small.model_copy(update={"selected_gpu_uuids": (UUID1, UUID2)})
    small_facts = observation(small)
    large_facts = observation(large, gpu_count=2)
    choices = choices.model_copy(update={"executors": (large.profile(large_facts, profile_id="large"),
                                                       small.profile(small_facts, profile_id="small"))})
    plan = plan_graph(project, choices, (large_facts, small_facts))
    assert plan.max_concurrent_gpu_count == 2


def test_preparation_reservation_blocks_claim_and_requires_fenced_release(tmp_path):
    from dataclasses import replace

    from test_graph_store import queued

    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    facts = observation()
    profile = settings().profile(facts)
    lease = store.reserve_preparation(profile, facts)
    queued(store, "training")
    assert store.claim(facts.exclusivity_key, max_concurrent=4) is None
    with pytest.raises(ValueError, match="reserved"):
        store.reserve_preparation(profile, facts)
    with pytest.raises(ValueError, match="reservation"):
        store.finish_preparation(replace(lease, owner_token="stale"), command_stopped=True)
    store.mark_preparation_uncertain(lease)
    reloaded = JobStore(store.path)
    assert reloaded.preparation_reservations()[0]["status"] == "UNCERTAIN"
    assert "owner_token" not in json.dumps(reloaded.preparation_reservations())
    with pytest.raises(ValueError, match="confirmation"):
        store.reconcile_preparation(lease.reservation_id, profile)
    store.reconcile_preparation(lease.reservation_id, profile, confirm_preparation_stopped=True)
    assert store.preparation_reservations() == []
    current = store.reserve_preparation(profile, facts)
    with pytest.raises(ValueError, match="reservation"):
        store.finish_preparation(lease, command_stopped=True)
    store.finish_preparation(current, command_stopped=True)


def test_concurrent_prepare_and_claim_have_one_owner(tmp_path):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier

    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    facts = observation()
    profile = settings().profile(facts)
    receipt = ServerApprovalReceipt.create("1" * 64, profile, facts)
    job = store.submit({"sha256": "1" * 64, "simulation": False,
        "config": {"resource_profile": {"gpu_count": 1}}}, "job", server_receipt=receipt)
    store.transition(job["id"], "QUEUED")
    barrier = Barrier(2)
    def prepare():
        barrier.wait()
        try:
            return store.reserve_preparation(profile, facts)
        except ValueError:
            return None

    def claim():
        barrier.wait()
        return store.claim(facts.exclusivity_key, server_profile=profile, server_facts=facts)

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(prepare), pool.submit(claim)]
        results = [future.result() for future in futures]
    assert sum(result is not None for result in results) == 1


@pytest.mark.parametrize("uncertain", [False, True])
def test_private_runtime_prepare_reservation_lifetime_and_paths(tmp_path, monkeypatch, uncertain):
    from contextlib import contextmanager

    from test_remote_worker import FakeSSH

    from tuning_factory import desktop, desktop_runtime
    from tuning_factory.execution import ExecutionUncertain
    from tuning_factory.gpu_servers import SSHServerInspection
    from tuning_factory.jobs import JobStore

    config = settings()
    facts = observation(config)
    inspected = SSHServerInspection(server_id=facts.server_id, transport=config.transport,
        inventory=facts.inventory, total_hourly_cost_usd=0, observed_at=facts.observed_at)
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    assets = tmp_path / "assets"
    (assets / "dist").mkdir(parents=True)
    (assets / "containers").mkdir()
    (assets / "dist/llm_tuning_factory-fixture.whl").write_text("local fixture")
    (assets / "containers/gpu-requirements.lock").write_text("fixture")
    @contextmanager
    def connect(_):
        yield facts, client

    scripts = []
    def run(_, argv, **kwargs):
        if argv[:2] == ["sh", "-c"] and "uv venv" in argv[2]:
            scripts.append(argv[2])
            assert JobStore(tmp_path / "jobs.db").preparation_reservations()
            if uncertain:
                raise TimeoutError("fixture transport lost")
        return ""

    monkeypatch.setattr(desktop_runtime, "connection", connect)
    monkeypatch.setattr(desktop_runtime, "command", run)
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: {"source": "fixture"})
    monkeypatch.setattr(desktop, "assets_root", lambda: assets)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    if uncertain:
        with pytest.raises(ExecutionUncertain, match="reservation"):
            desktop_runtime.runtime(native, tmp_path, prepare=True, inspection=inspected)
        reserved = JobStore(tmp_path / "jobs.db").preparation_reservations()
        assert reserved[0]["status"] == "UNCERTAIN"
        with pytest.raises(ValueError, match="confirmation"):
            desktop_runtime.release_runtime_reservation(native, tmp_path, reserved[0]["reservation_id"])
        desktop_runtime.release_runtime_reservation(native, tmp_path, reserved[0]["reservation_id"],
                                                   confirm_preparation_stopped=True)
    else:
        assert desktop_runtime.runtime(native, tmp_path, prepare=True, inspection=inspected)["ready"]
    assert JobStore(tmp_path / "jobs.db").preparation_reservations() == []
    assert scripts and "/workspace" not in scripts[0] and "sudo" not in scripts[0]
    assert "--python 3.12" in scripts[0] and config.remote_root + "/runtime" in scripts[0]


def test_cli_runtime_reservation_release_is_explicit_and_keeps_original_destination(tmp_path):
    from typer.testing import CliRunner

    from tuning_factory.cli import app
    from tuning_factory.connections import load_server_settings
    from tuning_factory.jobs import JobStore

    path = tmp_path / "server.json"
    path.write_text(settings().model_dump_json())
    config = load_server_settings(path)
    facts = observation(config)
    store = JobStore(tmp_path / "state/jobs.db")
    lease = store.reserve_preparation(config.profile(facts), facts)
    store.mark_preparation_uncertain(lease)
    runner = CliRunner()
    args = ["--state-dir", str(tmp_path / "state")]
    status = runner.invoke(app, [*args, "server-runtime-status"])
    assert status.exit_code == 0 and lease.reservation_id in status.output
    release = [*args, "server-runtime-release", str(path), lease.reservation_id]
    assert runner.invoke(app, release).exit_code != 0
    assert store.preparation_reservations()
    result = runner.invoke(app, [*release, "--confirm-preparation-stopped"])
    assert result.exit_code == 0, result.output
    assert store.preparation_reservations() == []


A4000 = "NVIDIA RTX A4000"


def a4000(config=None, **updates):
    """Facts whose GPU type matches configs/ai/paper_extraction.yaml."""
    from tuning_factory.gpu_servers import GPUDevice

    body = dict(inventory=(GPUDevice(uuid=UUID1, name=A4000, memory_mib=16376),
                           GPUDevice(uuid=UUID2, name=A4000, memory_mib=16376)), gpu_type=A4000)
    return observation(config, **(body | updates))


def real_factory(root):
    from tuning_factory.config import load_config
    from tuning_factory.manifest import resolve
    from tuning_factory.runner import Factory

    factory = Factory(root)
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True)
    config = load_config("configs/ai/paper_extraction.yaml")
    return factory, config, resolve(config, factory.registry, simulation=False)


@pytest.mark.parametrize("bound_elsewhere", ["other_server", "same_server_drifted_inventory"])
def test_server_worker_skips_jobs_bound_to_another_destination(tmp_path, monkeypatch, bound_elsewhere):
    from contextlib import contextmanager

    from typer.testing import CliRunner

    from tuning_factory import desktop_runtime
    from tuning_factory.cli import app
    from tuning_factory.connections import load_server_settings
    from tuning_factory.execution import SSHExecutor
    from tuning_factory.gpu_servers import GPUDevice, ServerApprovalReceipt

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    factory, config, manifest = real_factory(tmp_path / "state")
    path = tmp_path / "server.json"
    path.write_text(settings().model_dump_json())
    current = load_server_settings(path)
    facts = a4000(current, server_id="b" * 64)
    if bound_elsewhere == "other_server":
        other = settings().model_copy(update={"ssh_host": "gpu-a.example"})
        old = a4000(other, server_id="a" * 64)
        profile = other.profile(old)
    else:
        old = a4000(current, server_id="b" * 64, inventory=(
            GPUDevice(uuid=UUID2, name=A4000, memory_mib=16376),))
        profile = current.profile(old)
    bound = factory.submit(config, request_key="bound", priority=5, simulation=False,
                           server_receipt=ServerApprovalReceipt.create(manifest["sha256"], profile, old))
    unbound = factory.submit(config, request_key="unbound", priority=0, simulation=False)

    @contextmanager
    def observed(_, **kwargs):
        yield facts, object()

    class SoftwareServer(SSHExecutor):
        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            assert launch_id

    monkeypatch.setattr(desktop_runtime, "connection", observed)
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: {})
    monkeypatch.setattr(desktop_runtime, "SSHExecutor", SoftwareServer)
    result = CliRunner().invoke(app, ["--state-dir", str(factory.root), "work", "--server-profile", str(path),
                                      "--approve-execution-and-transfer"])
    assert result.exit_code == 0, result.output
    assert factory.store.get(unbound["id"])["status"] == "COMPLETED"
    assert factory.store.get(bound["id"])["status"] == "QUEUED"
    assert factory.store.get(bound["id"])["attempts"] == 0


def test_runpod_worker_ignores_jobs_bound_to_an_ssh_server(tmp_path, monkeypatch):
    from types import SimpleNamespace

    from typer.testing import CliRunner

    from tuning_factory.cli import app
    from tuning_factory.execution import SSHExecutor
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    monkeypatch.delenv("MLFLOW_TRACKING_URI", raising=False)
    monkeypatch.setenv("RUNPOD_API_KEY", "software-fixture-only")
    factory, config, manifest = real_factory(tmp_path / "state")
    facts = a4000()
    bound = factory.submit(config, request_key="bound", priority=5, simulation=False,
                           server_receipt=ServerApprovalReceipt.create(manifest["sha256"],
                                                                       settings().profile(facts), facts))
    unbound = factory.submit(config, request_key="unbound", priority=0, simulation=False)
    pod = {"id": "pod1", "desiredStatus": "RUNNING", "costPerHr": 0.4, "gpu": {"id": A4000, "count": 1},
           "publicIp": "203.0.113.5", "portMappings": {"22": 22022}, "networkVolume": {"id": "volume"},
           "volumeMountPath": "/workspace"}

    class PodFixture(SSHExecutor):
        def __init__(self, host, port, username, key_file, pod=None):
            super().__init__(host, port, username, key_file, client=object(), pod=pod)

        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            assert launch_id

    monkeypatch.setattr("tuning_factory.cli.RunPodController", lambda *a, **k: SimpleNamespace(status=lambda _: pod))
    monkeypatch.setattr("tuning_factory.cli.SSHExecutor", PodFixture)
    result = CliRunner().invoke(app, ["--state-dir", str(factory.root), "work", "--pod-id", "pod1",
                                      "--host", "203.0.113.5", "--port", "22022", "--key-file",
                                      str(tmp_path / "key"), "--approve-execution-and-transfer"])
    assert result.exit_code == 0, result.output
    assert factory.store.get(unbound["id"])["status"] == "COMPLETED"
    assert factory.store.get(bound["id"])["status"] == "QUEUED"
    assert factory.store.get(bound["id"])["attempts"] == 0


def test_unfiltered_server_claim_skips_unbound_and_foreign_receipts(tmp_path):
    from test_graph_store import queued

    from tuning_factory.gpu_servers import ServerApprovalReceipt
    from tuning_factory.jobs import JobStore

    store = JobStore(tmp_path / "jobs.db")
    facts = observation()
    profile = settings().profile(facts)
    queued(store, "unbound", priority=9)
    receipts = {"foreign": (observation(server_id="b" * 64), 5), "drifted": (observation(inventory=(
        observation().inventory[1],)), 3), "own": (facts, 0)}
    jobs = {}
    for index, (name, (observed, priority)) in enumerate(receipts.items()):
        sha = str(index + 1) * 64
        receipt = ServerApprovalReceipt.create(sha, settings().profile(observed), observed)
        jobs[name] = store.submit({"sha256": sha, "simulation": False,
                                   "config": {"resource_profile": {"gpu_count": 1}}}, name, priority,
                                  server_receipt=receipt)
        store.transition(jobs[name]["id"], "QUEUED")
    claimed = store.claim(facts.exclusivity_key, server_profile=profile, server_facts=facts)
    assert claimed and claimed["id"] == jobs["own"]["id"]
    with pytest.raises(ValueError, match="resource"):
        store.claim("ssh_server:" + "c" * 64, max_concurrent=4, server_profile=profile, server_facts=facts)


class StatFile:
    """Local file with the paramiko SFTPFile.stat() used by remote log tails."""

    def __init__(self, handle):
        self.handle = handle

    def stat(self):
        import os
        return os.fstat(self.handle.fileno())

    def __getattr__(self, name):
        return getattr(self.handle, name)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.handle.close()


class FakeServer:
    """Paramiko-shaped software fixture: fixed probe replies and a local SFTP tree; never a network."""

    def __init__(self, root, machine_id="b" * 32,
                 inventory=((UUID1, A4000, 16376), (UUID2, A4000, 16376))):
        from test_remote_worker import FakeSFTP

        class SFTP(FakeSFTP):
            def open(self, path, mode="r"):
                return StatFile(super().open(path, mode))

        root.mkdir(parents=True, exist_ok=True)
        self.sftp = SFTP(root)
        self.machine_id = machine_id
        self.inventory = inventory
        self.policies, self.connects, self.commands = [], [], []

    def install(self, monkeypatch):
        from tuning_factory import desktop_runtime

        server = self

        class Client:
            def set_missing_host_key_policy(self, policy):
                server.policies.append(policy)

            def connect(self, host, **kwargs):
                server.connects.append((host, kwargs))

            def open_sftp(self):
                return server.sftp

            def close(self):
                pass

        monkeypatch.setattr(desktop_runtime.paramiko, "SSHClient", Client)
        monkeypatch.setattr(desktop_runtime, "command", self.command)
        return self

    def command(self, client, argv, timeout=60, **kwargs):
        self.commands.append(argv)
        if argv[0] == "nvidia-smi":
            return "".join(f"{uuid}, {name}, {memory}, Disabled\n" for uuid, name, memory in self.inventory)
        if argv[:2] == ["python3", "-c"]:
            return json.dumps(dict(system="Linux", machine_id=self.machine_id, python=[3, 12, 9],
                                   tools={"flock": True, "cc": True}, workspace_safe=True,
                                   workspace_writable=True))
        raise AssertionError("unexpected remote command " + repr(argv))

    def facts(self, config):
        from tuning_factory.gpu_servers import probe_server

        return probe_server(None, config, self.command)


def launched_server_attempt(tmp_path, server, config):
    """A real job whose original server attempt finished after the controller lost contact."""
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    factory, ai_config, manifest = real_factory(tmp_path / "state")
    facts = server.facts(config)
    receipt = ServerApprovalReceipt.create(manifest["sha256"], config.profile(facts), facts)
    job = factory.submit(ai_config, simulation=False, server_receipt=receipt)
    claimed = factory.store.claim(facts.exclusivity_key, server_profile=receipt.profile, server_facts=facts,
                                  job_id=job["id"])
    launch_id = factory.store.begin_launch(claimed["lease"])
    directory = factory.directory(job["id"])
    remote = config.remote_root + "/jobs/" + job["id"] + "/" + launch_id
    (directory / "remote-attempt.json").write_text(json.dumps(
        {"remote_path": remote, "created": 0, "destination": receipt.profile.model_dump(mode="json")}))
    attempt = server.sftp.path(remote)
    attempt.mkdir(parents=True)
    (attempt / "manifest.json").write_bytes((directory / "manifest.json").read_bytes())
    (attempt / "artifacts.json").write_text("{}")
    (attempt / "result.json").write_text('{"status": "COMPLETED"}')
    (attempt / "train.log").write_text("software fixture step 2/2")
    factory.store.transition(claimed["lease"], "FAILED", "controller lost contact")
    return factory, job, receipt


DRIFT = {"unselected_removed": ((UUID2, A4000, 16376),),
         "gpu_added": ((UUID1, A4000, 16376), (UUID2, A4000, 16376),
                       ("GPU-33333333-3333-3333-3333-333333333333", "NVIDIA H100", 81559)),
         "memory_changed": ((UUID1, A4000, 16376), (UUID2, A4000, 15000)),
         "selected_missing": ((UUID1, A4000, 16376),)}


@pytest.mark.parametrize("drift", sorted(DRIFT))
def test_recovery_collects_original_attempt_after_unrelated_inventory_drift(tmp_path, monkeypatch, drift):
    from tuning_factory import desktop_runtime

    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    factory, job, receipt = launched_server_attempt(tmp_path, server, config)
    server.inventory = DRIFT[drift]
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: pytest.fail("recovery never preflights"))
    directory = factory.directory(job["id"])
    assert "software fixture" in desktop_runtime.live_logs(native, factory.root, directory)["text"]
    assert desktop_runtime.read_progress(native, directory) is not None
    with desktop_runtime.executor_for(native, factory.root, preflight=False, server_receipt=receipt,
                                      recovery=True) as executor:
        assert executor.recovery_only
        result = factory.reconcile(job["id"], executor)
    assert result["status"] == "COMPLETED"
    assert not factory.store.gpu_locked(receipt.facts.exclusivity_key)


@pytest.mark.parametrize("change", [dict(machine_id="c" * 32), dict(ssh_fingerprint="SHA256:" + "B" * 43),
                                    dict(remote_root="/home/researcher/other"),
                                    dict(selected_gpu_uuids=(UUID1,)), dict(ssh_key_file="other-key")])
def test_recovery_refuses_another_server_or_edited_transport(tmp_path, monkeypatch, change):
    from tuning_factory import desktop_runtime

    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    factory, job, receipt = launched_server_attempt(tmp_path, server, config)
    if "machine_id" in change:
        server.machine_id = change["machine_id"]
    else:
        config = config.model_copy(update=change)
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    directory = factory.directory(job["id"])
    connects = len(server.connects)
    for action in (lambda: desktop_runtime.live_logs(native, factory.root, directory),
                   lambda: desktop_runtime.read_progress(native, directory)):
        with pytest.raises(ValueError, match="destination|server"):
            action()
    with pytest.raises(ValueError, match="destination|server"):
        with desktop_runtime.executor_for(native, factory.root, preflight=False, server_receipt=receipt,
                                          recovery=True) as executor:
            factory.reconcile(job["id"], executor)
    if "machine_id" not in change:
        assert len(server.connects) == connects  # Edited settings are refused before SSH.
    assert factory.store.get(job["id"])["status"] == "FAILED"
    assert factory.store.gpu_locked(receipt.facts.exclusivity_key)


@pytest.mark.parametrize("drift", sorted(DRIFT))
def test_launch_still_requires_exact_approved_inventory(tmp_path, monkeypatch, drift):
    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_servers import ServerApprovalReceipt

    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    facts = server.facts(config)
    receipt = ServerApprovalReceipt.create("1" * 64, config.profile(facts), facts)
    server.inventory = DRIFT[drift]
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: pytest.fail("preflight after mismatch"))
    with pytest.raises(ValueError, match="destination|selected GPU"):
        with desktop_runtime.executor_for(native, tmp_path, server_receipt=receipt):
            pytest.fail("drifted hardware reached launch")


def test_recovery_executor_can_never_claim_or_launch(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime

    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    config = settings()
    factory, job, receipt = launched_server_attempt(tmp_path, server, config)
    factory.store.reconcile_result(factory.store.current_lease(job["id"]), "FAILED", "fixture")
    factory.store.reset_for_resume(job["id"])
    native = {"provider": "ssh_server", **config.model_dump(mode="json")}
    with desktop_runtime.executor_for(native, factory.root, server_receipt=receipt, recovery=True) as executor:
        with pytest.raises(ValueError, match="recovery"):
            factory.work(executor=executor, gpu=receipt.facts.exclusivity_key, approved_transfer=True,
                         target_job_id=job["id"])
        with pytest.raises(ValueError, match="recovery"):
            executor.run(None, factory.directory(job["id"]), 5, lambda: False, launch_id="d" * 32)
    assert factory.store.get(job["id"])["status"] == "QUEUED"
