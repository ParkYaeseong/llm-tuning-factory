"""Desktop sidecar routing for direct-SSH GPU servers; every remote reply is a local fixture."""
import json
from contextlib import contextmanager
from types import SimpleNamespace

import pytest
from test_gpu_servers import (
    A4000,
    UUID1,
    UUID2,
    FakeServer,
    a4000,
    launched_server_attempt,
    observation,
    settings,
)

from tuning_factory import desktop, desktop_runtime
from tuning_factory.connections import ENV_SETTINGS
from tuning_factory.gpu_servers import ServerApprovalReceipt, SSHServerInspection, server_settings

CHANGED = "연결 설정이 실행 계획 이후 바뀌었습니다. 실행 계획을 다시 확인하세요."


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """The sidecar exports settings to the process environment; restore it after every case.
    No case here may reach the RunPod API or an external tracking server."""
    for key in ENV_SETTINGS:
        monkeypatch.delenv(key, raising=False)

    def runpod(*args, **kwargs):
        pytest.fail("RunPod API called")

    for module in ("desktop", "desktop_runtime", "diagnostics", "cli"):
        monkeypatch.setattr(f"tuning_factory.{module}.RunPodController", runpod)
    monkeypatch.setattr("tuning_factory.tracking.LazyMLflowTracker",
                        lambda *a, **kw: pytest.fail("unexpected tracking connection"))


def native(config=None, drop=(), **updates):
    """Native desktop settings: string values under server_ keys, plus an inactive Pod's fields."""
    values = {"provider": "ssh_server", "pod_id": "inactive-pod", "RUNPOD_API_KEY": "inactive-runpod-key",
              "ssh_key_file": "inactive-pod-key", "ssh_fingerprint": "SHA256:" + "P" * 43}
    for key, value in (config or settings()).model_dump(mode="json").items():
        values["server_" + key] = json.dumps(value) if isinstance(value, list) else str(value)
    values.update(updates)
    for key in drop:
        values.pop(key)
    return values


def offline(monkeypatch):
    """Any SSH connection attempt fails the test."""
    @contextmanager
    def refuse(*args, **kwargs):
        pytest.fail("SSH connection opened")
        yield

    monkeypatch.setattr(desktop_runtime, "connection", refuse)
    monkeypatch.setattr(desktop_runtime.paramiko, "SSHClient", lambda: pytest.fail("SSH client created"))


def zero_cost_config(app):
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": "examples/dummy.json",
                             "export_allowed": True})
    config = app.dispatch("catalog", {})["templates"][0]["config"]
    assert config["resource_profile"]["gpu_type"] == A4000
    config["resource_profile"]["hourly_cost_usd"] = 0
    return config


def bound_job(app, facts=None):
    """A real job frozen to the saved server destination, plus its receipt."""
    from tuning_factory.config import AIConfig

    config = AIConfig.model_validate(zero_cost_config(app))
    manifest = app.manifest(config)
    current = server_settings(native())
    facts = facts or a4000(current)
    receipt = ServerApprovalReceipt.create(manifest["sha256"], current.profile(facts), facts)
    return app.factory.submit(config, simulation=False, server_receipt=receipt), receipt


def unbound_job(app):
    from tuning_factory.config import AIConfig

    config = AIConfig.model_validate(zero_cost_config(app))
    return app.factory.submit(config, request_key="unbound", simulation=False)


def recording_executor(monkeypatch, facts=None):
    calls = []

    @contextmanager
    def executor_for(settings, root, *args, **kwargs):
        calls.append(dict(settings=settings, root=root, args=args, **kwargs))
        yield SimpleNamespace(server_facts=facts, remote=True)

    monkeypatch.setattr(desktop_runtime, "executor_for", executor_for)
    return calls


def test_plan_is_offline_and_returns_the_saved_destination_hash(tmp_path, monkeypatch):
    offline(monkeypatch)
    app = desktop.DesktopService(tmp_path, native())
    result = app.dispatch("plan", {"config": zero_cost_config(app)})
    current = server_settings(native())
    inspected = SSHServerInspection(server_id="a" * 64, transport=current.transport,
                                    inventory=observation().inventory,
                                    total_hourly_cost_usd=current.total_hourly_cost_usd,
                                    observed_at=observation().observed_at)
    assert result["dry_run"] is True and result["manifest"]["simulation"] is False
    assert result["destination_sha256"] == inspected.destination_sha256
    assert result["destination_issue"] is None
    edited = desktop.DesktopService(tmp_path, native(server_remote_root="/home/researcher/other"))
    assert edited.dispatch("plan", {"config": zero_cost_config(app)})["destination_sha256"] != \
        result["destination_sha256"]
    runpod = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"}).dispatch(
        "plan", {"config": zero_cost_config(app)})
    assert set(runpod) == {"dry_run", "manifest", "estimated_cost_usd", "note"}


@pytest.mark.parametrize("drop,updates", [
    (("server_ssh_host",), {}), ((), {"server_ssh_port": "port"}), ((), {"server_ssh_fingerprint": "SHA256:x"}),
    ((), {"server_selected_gpu_uuids": "[]"}), ((), {"server_selected_gpu_uuids": "not json"}),
    ((), {"server_total_hourly_cost_usd": ""}), ((), {"server_remote_root": "/"}),
    (("server_ssh_key_file", "server_total_hourly_cost_usd"), {}),
])
def test_plan_reports_incomplete_server_settings_without_raising(tmp_path, monkeypatch, drop, updates):
    offline(monkeypatch)
    app = desktop.DesktopService(tmp_path, native(drop=drop, **updates))
    result = app.dispatch("plan", {"config": zero_cost_config(app)})
    assert result["destination_sha256"] is None
    assert isinstance(result["destination_issue"], str) and result["destination_issue"]
    assert "test-key" not in result["destination_issue"]
    with pytest.raises(ValueError, match=CHANGED):
        app.dispatch("submit", {"approved": True, "config": zero_cost_config(app), "destination_sha256": None})
    assert app.dispatch("jobs", {}) == []


@pytest.mark.parametrize("case", ["missing", "unrelated", "edited_after_plan"])
def test_submit_refuses_a_changed_destination_before_any_connection(tmp_path, monkeypatch, case):
    offline(monkeypatch)
    app = desktop.DesktopService(tmp_path, native())
    config = zero_cost_config(app)
    planned = app.dispatch("plan", {"config": config})["destination_sha256"]
    with pytest.raises(ValueError, match="approval"):
        app.dispatch("submit", {"config": config, "destination_sha256": planned})
    data = {"approved": True, "config": config, "destination_sha256": planned}
    if case == "missing":
        data.pop("destination_sha256")
    elif case == "unrelated":
        data["destination_sha256"] = "0" * 64
    else:
        app = desktop.DesktopService(tmp_path, native(server_selected_gpu_uuids=json.dumps([UUID1])))
    with pytest.raises(ValueError, match=CHANGED):
        app.dispatch("submit", data)
    assert app.dispatch("jobs", {}) == []


def test_zero_cost_server_job_plans_submits_and_executes_on_the_receipt(tmp_path, monkeypatch):
    from tuning_factory.execution import SSHExecutor

    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    launches = []

    class SoftwareServer(SSHExecutor):
        def run(self, backend, directory, timeout, canceled, checkpoint=None, *, launch_id=None):
            launches.append((self.server_facts.exclusivity_key, launch_id))

    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: {})
    monkeypatch.setattr(desktop_runtime, "SSHExecutor", SoftwareServer)
    app = desktop.DesktopService(tmp_path, native())
    config = zero_cost_config(app)
    planned = app.dispatch("plan", {"config": config})
    assert not server.connects
    job = app.dispatch("submit", {"approved": True, "config": config,
                                  "destination_sha256": planned["destination_sha256"]})
    assert job["status"] == "QUEUED" and job["attempts"] == 0
    receipt = app.factory.store.server_receipt(job["id"])
    assert receipt.manifest_sha256 == job["manifest"]["sha256"]
    assert receipt.profile == server_settings(native()).profile(receipt.facts)
    assert receipt.facts.total_hourly_cost_usd == 0 and receipt.facts.gpu_type == A4000
    with pytest.raises(ValueError, match="approval"):
        app.dispatch("execute", {"job_id": job["id"]})
    result = app.dispatch("execute", {"approved": True, "job_id": job["id"]})
    assert result["status"] == "COMPLETED", result["error"]
    assert launches == [(receipt.facts.exclusivity_key, launches[0][1])] and launches[0][1]
    evidence = json.loads((app.factory.directory(job["id"]) / "execution.json").read_text())
    assert evidence["cost_source"] == "operator" and evidence["total_hourly_cost_usd"] == 0
    assert all(argv[0] in {"python3", "nvidia-smi"} for argv in server.commands)


def test_submit_with_a_mismatched_server_gpu_creates_no_job(tmp_path, monkeypatch):
    server = FakeServer(tmp_path / "remote", inventory=((UUID1, "NVIDIA H100", 81559),
                                                        (UUID2, "NVIDIA H100", 81559))).install(monkeypatch)
    app = desktop.DesktopService(tmp_path, native())
    config = zero_cost_config(app)
    planned = app.dispatch("plan", {"config": config})["destination_sha256"]
    with pytest.raises(ValueError, match="GPU type"):
        app.dispatch("submit", {"approved": True, "config": config, "destination_sha256": planned})
    assert server.connects and app.dispatch("jobs", {}) == []


def test_execute_uses_the_job_receipt_and_server_exclusivity_key(tmp_path, monkeypatch):
    app = desktop.DesktopService(tmp_path, native())
    job, receipt = bound_job(app)
    calls = recording_executor(monkeypatch, receipt.facts)
    work = []
    monkeypatch.setattr(app.factory, "work", lambda **kwargs: work.append(kwargs) or {"id": job["id"]})
    assert app.dispatch("execute", {"approved": True, "job_id": job["id"]}) == {"id": job["id"]}
    assert calls[0]["server_receipt"] == receipt and calls[0]["training_stage"] == "sft"
    assert calls[0]["settings"] == app.settings and "recovery" not in calls[0]
    assert work == [dict(executor=work[0]["executor"], gpu="ssh_server:" + receipt.facts.server_id,
                         approved_transfer=True, select_mode=True, target_job_id=job["id"])]
    unbound = unbound_job(app)
    with pytest.raises(ValueError, match="GPU 서버용 승인 기록이 없는 작업입니다"):
        app.dispatch("execute", {"approved": True, "job_id": unbound["id"]})
    assert len(calls) == 1


def test_runpod_execute_refuses_a_job_approved_for_a_server(tmp_path, monkeypatch):
    server_app = desktop.DesktopService(tmp_path, native())
    job, _ = bound_job(server_app)
    calls = recording_executor(monkeypatch)
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed", "RUNPOD_API_KEY": "fixture-only"})
    with pytest.raises(ValueError, match="이 작업은 GPU 서버용으로 승인되었습니다"):
        app.dispatch("execute", {"approved": True, "job_id": job["id"]})
    assert calls == [] and app.factory.store.get(job["id"])["attempts"] == 0


def test_reconcile_uses_a_recovery_executor_with_the_original_receipt(tmp_path, monkeypatch):
    app = desktop.DesktopService(tmp_path, native())
    job, receipt = bound_job(app)
    calls = recording_executor(monkeypatch, receipt.facts)
    monkeypatch.setattr(app.factory, "reconcile", lambda job_id, executor: {"reconciled": job_id})
    assert app.dispatch("reconcile", {"job_id": job["id"]}) == {"reconciled": job["id"]}
    assert calls[0]["server_receipt"] == receipt
    assert calls[0]["preflight"] is False and calls[0]["recovery"] is True
    unbound = unbound_job(app)
    with pytest.raises(ValueError, match="GPU 서버용 승인 기록이 없는 작업입니다"):
        app.dispatch("reconcile", {"job_id": unbound["id"]})
    assert len(calls) == 1


def test_reconcile_collects_the_original_server_attempt(tmp_path, monkeypatch):
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: pytest.fail("recovery never preflights"))
    factory, job, receipt = launched_server_attempt(tmp_path, server, settings())
    server.inventory = ((UUID1, A4000, 16376),)  # Unrelated drift: the selected GPU is now absent.
    app = desktop.DesktopService(factory.root, native())
    assert app.dispatch("reconcile", {"job_id": job["id"]})["status"] == "COMPLETED"
    assert not app.factory.store.gpu_locked(receipt.facts.exclusivity_key)


def inspection_for(config=None):
    config = config or server_settings(native())
    facts = a4000(config)
    return SSHServerInspection(server_id=facts.server_id, transport=config.transport, inventory=facts.inventory,
                               total_hourly_cost_usd=config.total_hourly_cost_usd, observed_at=facts.observed_at)


def test_runtime_prepare_requires_approval_inspection_and_a_free_server(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr(desktop_runtime, "runtime", lambda *args, **kwargs: calls.append((args, kwargs)) or
                        {"ready": True})
    app = desktop.DesktopService(tmp_path, native())
    inspection = inspection_for().model_dump(mode="json")
    with pytest.raises(ValueError, match="approval"):
        app.dispatch("runtime_prepare", {"inspection": inspection})
    for data in ({"approved": True}, {"approved": True, "inspection": None}):
        with pytest.raises(ValueError, match="inspection"):
            app.dispatch("runtime_prepare", data)
    with pytest.raises(ValueError):
        app.dispatch("runtime_prepare", {"approved": True, "inspection": inspection | {"inventory": []}})
    assert calls == []
    assert app.dispatch("runtime_prepare", {"approved": True, "inspection": inspection,
                                            "training_stage": "ppo"}) == {"ready": True}
    (settings_arg, root), kwargs = calls[0]
    assert settings_arg == app.settings and root == app.root
    assert kwargs == {"prepare": True, "training_stage": "ppo",
                      "inspection": SSHServerInspection.model_validate(inspection)}
    assert isinstance(kwargs["inspection"], SSHServerInspection)
    assert app.dispatch("runtime_check", {"training_stage": "sft"}) == {"ready": True}
    assert calls[1][1] == {"prepare": False, "training_stage": "sft"}


def test_runtime_prepare_refuses_a_reserved_server(tmp_path, monkeypatch):
    from test_graph_store import queued

    monkeypatch.setattr(desktop_runtime, "runtime", lambda *a, **kw: pytest.fail("reserved server prepared"))
    app = desktop.DesktopService(tmp_path, native())
    queued(app.factory.store, "blocked")
    app.factory.store.claim("unknown-existing-lock")
    with pytest.raises(ValueError, match="예약"):
        app.dispatch("runtime_prepare", {"approved": True, "inspection": inspection_for().model_dump(mode="json")})
    assert app.factory.store.gpu_locked("unknown-existing-lock")


def test_runtime_reservations_are_listed_and_released_only_with_both_confirmations(tmp_path):
    app = desktop.DesktopService(tmp_path, native())
    current = server_settings(native())
    facts = a4000(current)
    lease = app.factory.store.reserve_preparation(current.profile(facts), facts)
    app.factory.store.mark_preparation_uncertain(lease)
    listed = app.dispatch("runtime_reservations", {})
    assert [item["reservation_id"] for item in listed] == [lease.reservation_id]
    assert listed[0]["status"] == "UNCERTAIN" and "owner_token" not in json.dumps(listed)
    for data in ({"reservation_id": lease.reservation_id},
                 {"approved": True, "reservation_id": lease.reservation_id},
                 {"confirm_preparation_stopped": True, "reservation_id": lease.reservation_id},
                 {"approved": True, "confirm_preparation_stopped": "true", "reservation_id": lease.reservation_id},
                 {"approved": True, "confirm_preparation_stopped": True},
                 {"approved": True, "confirm_preparation_stopped": True, "reservation_id": "../other"}):
        with pytest.raises(ValueError):
            app.dispatch("runtime_release", data)
    assert app.dispatch("runtime_reservations", {})
    assert app.dispatch("runtime_release", {"approved": True, "confirm_preparation_stopped": True,
                                            "reservation_id": lease.reservation_id}) == \
        {"released": lease.reservation_id}
    assert app.dispatch("runtime_reservations", {}) == []


def test_server_inspect_and_facts_route_to_ssh_and_refuse_runpod(tmp_path, monkeypatch):
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    inspected = desktop.DesktopService(tmp_path, native(server_selected_gpu_uuids="[]")).dispatch(
        "server_inspect", {})
    assert [gpu["uuid"] for gpu in inspected["inventory"]] == [UUID1, UUID2]
    assert inspected["transport"]["selected_gpu_uuids"] == [] and inspected["cost_source"] == "operator"
    SSHServerInspection.model_validate(inspected)
    facts = desktop.DesktopService(tmp_path, native()).dispatch("server_facts", {})
    assert (facts["gpu_type"], facts["gpu_count"], facts["total_hourly_cost_usd"]) == (A4000, 1, 0)
    assert facts["selected_gpu_uuids"] == [UUID2]
    assert "test-key" not in json.dumps([inspected, facts])
    assert [host for host, _ in server.connects] == ["gpu.example", "gpu.example"]
    for provider in ({}, {"provider": "existing_runpod", "pod_id": "borrowed"}):
        app = desktop.DesktopService(tmp_path, provider)
        for action in ("server_inspect", "server_facts", "runtime_release"):
            with pytest.raises(ValueError, match="GPU 서버"):
                app.dispatch(action, {"approved": True, "confirm_preparation_stopped": True,
                                      "reservation_id": "a" * 32})
    assert len(server.connects) == 2


def test_connection_checks_the_server_without_a_pod_or_runpod_key(tmp_path, monkeypatch):
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    result = desktop.DesktopService(tmp_path, native()).dispatch("connection", {})
    assert result["gpu_training_verified"] is False and "runpod" not in result
    assert [gpu["uuid"] for gpu in result["ssh_server"]["inventory"]] == [UUID1, UUID2]
    assert server.connects[0][0] == "gpu.example"


def test_connection_passes_the_settings_to_diagnostics_as_the_server(tmp_path, monkeypatch):
    calls = []
    monkeypatch.setattr("tuning_factory.diagnostics.check_connections",
                        lambda *args, **kwargs: calls.append((args, kwargs)) or {"checked": True})
    app = desktop.DesktopService(tmp_path, native())
    assert app.dispatch("connection", {}) == {"checked": True}
    assert calls == [((app.root,), {"server": app.settings})]
    runpod = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    runpod.dispatch("connection", {})
    assert calls[1] == ((runpod.root, "borrowed"), {})


def test_pod_status_is_runpod_only(tmp_path):
    with pytest.raises(ValueError, match="server_facts"):
        desktop.DesktopService(tmp_path, native()).dispatch("pod", {})


def test_unknown_provider_is_refused(tmp_path):
    app = desktop.DesktopService(tmp_path, {"provider": "slurm"})
    for action in ("plan", "execute", "connection", "pod", "runtime_check"):
        with pytest.raises(ValueError, match="provider"):
            app.dispatch(action, {"approved": True, "job_id": "a" * 32, "config": {}})


def test_jobs_show_a_public_server_destination_without_key_fields(tmp_path):
    app = desktop.DesktopService(tmp_path, native())
    job, receipt = bound_job(app)
    other = unbound_job(app)
    jobs = {item["id"]: item for item in app.dispatch("jobs", {})}
    assert jobs[job["id"]]["server_destination"] == {
        "ssh_host": "gpu.example", "ssh_port": 22, "ssh_username": "researcher",
        "remote_root": "/home/researcher/factory", "selected_gpu_uuids": [UUID2]}
    assert jobs[other["id"]]["server_destination"] is None
    public = json.dumps(jobs[job["id"]]["server_destination"])
    assert "test-key" not in public and receipt.profile.transport.key_reference_sha256 not in public
    assert "fingerprint" not in public and "SHA256:" not in public
    runpod = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"}).dispatch("jobs", {})
    assert {item["id"]: item["server_destination"] for item in runpod} == {
        job["id"]: jobs[job["id"]]["server_destination"], other["id"]: None}
