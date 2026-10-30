import json
from pathlib import Path

import pytest

from tuning_factory import desktop


def test_catalog_plan_and_dataset_are_offline(tmp_path):
    app = desktop.DesktopService(tmp_path)
    catalog = app.dispatch("catalog", {})
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    raw = json.dumps([{"instruction": "question", "output": "answer"}])
    source = tmp_path / "input.json"
    source.write_text(raw)
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    result = app.dispatch("plan", {"config": config})
    assert result["dry_run"] is True
    assert result["manifest"]["simulation"] is False
    assert app.dispatch("jobs", {}) == []


def test_real_execution_cannot_be_inferred_from_saved_settings(tmp_path):
    app = desktop.DesktopService(tmp_path, {"RUNPOD_API_KEY": "private-value"})
    with pytest.raises(ValueError, match="approval"):
        app.dispatch("execute", {"config": {}})
    assert "private-value" not in "".join(p.read_text() for p in tmp_path.rglob("*.json"))


def test_log_reader_refuses_paths_and_bounds_output(tmp_path):
    app = desktop.DesktopService(tmp_path)
    with pytest.raises(ValueError):
        app.dispatch("logs", {"job_id": "../../outside"})
    with pytest.raises(ValueError):
        app.dispatch("logs", {"job_id": "a" * 32, "name": "../../secret"})


def test_comparison_requires_same_validation_and_separates_simulation():
    base = {"simulation": False, "ai_id": "a", "validation_sha256": "same", "evaluation_kind": "classification",
            "status": "FINISHED", "metrics": {"accuracy": .5}}
    other = {**base, "metrics": {"accuracy": .75}}
    assert desktop.compare_runs(base, other)["deltas"]["accuracy"] == .25
    assert not desktop.compare_runs(base, {**other, "validation_sha256": "different"})["comparable"]
    assert not desktop.compare_runs(base, {**other, "simulation": True})["comparable"]
    assert not desktop.compare_runs({**base, "validation_sha256": None}, other)["comparable"]
    assert not desktop.compare_runs({**base, "evaluation_kind": None},
                                    {**other, "evaluation_kind": None})["comparable"]


def test_desktop_templates_are_training_defaults_not_two_step_smokes(tmp_path):
    templates = desktop.DesktopService(tmp_path).dispatch("catalog", {})["templates"]
    assert len(templates) == 3
    assert all(x["config"]["hyperparameters"]["max_steps"] == -1 for x in templates)


def test_model_lookup_rejects_arbitrary_repositories_without_network(tmp_path):
    with pytest.raises(ValueError, match="supported model"):
        desktop.DesktopService(tmp_path).dispatch("model_revision", {"model": "untrusted/model"})


def test_packaged_asset_root(monkeypatch, tmp_path):
    monkeypatch.setattr(desktop.sys, "_MEIPASS", str(tmp_path), raising=False)
    assert desktop.assets_root() == Path(tmp_path) / "assets"


def test_authenticated_tracking_cannot_use_plain_http(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        desktop.DesktopService(tmp_path, {"MLFLOW_TRACKING_URI": "http://example.org",
                                         "MLFLOW_TRACKING_TOKEN": "private-token"})


AUTH_SETTINGS = ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                 "MLFLOW_OIDC_CLIENT_SECRET")


@pytest.fixture
def isolated_environment(monkeypatch):
    """DesktopService exports its settings to the process environment; restore it after the case."""
    from tuning_factory.connections import ENV_SETTINGS
    for key in ENV_SETTINGS:
        monkeypatch.setenv(key, "x")  # Registers teardown even for keys that start absent.
        monkeypatch.delenv(key)


def test_unauthenticated_tracking_allows_local_http_and_drops_stale_credentials(tmp_path, isolated_environment):
    import os
    stale = {"MLFLOW_TRACKING_TOKEN": "stale-token-value", "MLFLOW_OIDC_TOKEN_URL": "https://sso.example/token",
             "MLFLOW_OIDC_CLIENT_ID": "mlflow-client", "MLFLOW_OIDC_CLIENT_SECRET": "stale-secret-value"}
    app = desktop.DesktopService(tmp_path, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "auth_mode": "none",
                                           **stale})
    assert os.environ["MLFLOW_TRACKING_URI"] == "http://127.0.0.1:5000"
    assert not any(key in os.environ or key in app.settings for key in AUTH_SETTINGS)
    desktop.DesktopService(tmp_path, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000"})
    for mode in ("token", "oidc", ""):
        with pytest.raises(ValueError, match="HTTPS"):
            desktop.DesktopService(tmp_path, {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "auth_mode": mode,
                                             **stale})


def test_runtime_preparation_refuses_a_reserved_pod(tmp_path, monkeypatch):
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    monkeypatch.setattr(app.factory.store, "gpu_locked", lambda pod: True)
    with pytest.raises(ValueError, match="다른 작업이 이 Pod를 사용 중"):
        app.dispatch("runtime_prepare", {"approved": True})


def test_plan_rejects_identical_training_and_validation_snapshot(tmp_path):
    app = desktop.DesktopService(tmp_path)
    config = app.dispatch("catalog", {})["templates"][0]["config"]
    source = tmp_path / "input.json"
    source.write_text('[{"instruction":"q","output":"a"}]')
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    config["evaluation_profile"].update(kind="json", dataset_id="dummy", dataset_version="1")
    with pytest.raises(ValueError, match="identical"):
        app.dispatch("plan", {"config": config})


@pytest.mark.parametrize('lock_key', ['existing_runpod:borrowed', 'unknown-old-profile'])
def test_desktop_cannot_execute_past_uncertain_shared_resource_lock(tmp_path, monkeypatch, lock_key):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {'pod_id': 'borrowed'})
    store = app.factory.store
    old = store.submit({'sha256': 'old', 'simulation': False}, 'old')
    store.transition(old['id'], 'QUEUED')
    claimed = store.claim(lock_key)
    store.transition(claimed['lease'], 'FAILED')
    app.factory.policy = app.factory.policy.model_copy(update={'max_gpus': 2})
    queued = store.submit({'sha256': 'next', 'simulation': False,
                           'config': {'training_stage': 'sft'}}, 'next')
    store.transition(queued['id'], 'QUEUED')

    @contextmanager
    def fake_executor(*args, **kwargs):
        yield SimpleNamespace(remote=True)

    monkeypatch.setattr(desktop_runtime, 'executor_for', fake_executor)
    monkeypatch.setattr(app.factory, 'run_claimed', lambda *args: pytest.fail('reserved physical resource was claimed'))
    assert app.dispatch('execute', {'approved': True, 'job_id': queued['id']}) is None
    assert store.get(queued['id'])['attempts'] == 0
    assert store.gpu_locked(lock_key)


@pytest.mark.parametrize('lock_key', ['existing_runpod:borrowed', 'unknown-old-profile'])
def test_desktop_runtime_prepare_observes_canonical_and_unknown_locks(tmp_path, monkeypatch, lock_key):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {'pod_id': 'borrowed'})
    store = app.factory.store
    job = store.submit({'sha256': 'old', 'simulation': False}, 'old')
    store.transition(job['id'], 'QUEUED')
    claimed = store.claim(lock_key)
    store.transition(claimed['lease'], 'FAILED')
    monkeypatch.setattr(desktop_runtime, 'runtime', lambda *args, **kwargs: pytest.fail('reserved Pod runtime was prepared'))
    with pytest.raises(ValueError, match="다른 작업이 이 Pod를 사용 중"):
        app.dispatch('runtime_prepare', {'approved': True})
    assert store.gpu_locked(lock_key)


def test_desktop_claim_is_visible_to_canonical_workers(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {'pod_id': 'borrowed'})
    store = app.factory.store
    job = store.submit({'sha256': 'desktop', 'simulation': False,
                        'config': {'training_stage': 'sft'}}, 'desktop')
    store.transition(job['id'], 'QUEUED')

    @contextmanager
    def fake_executor(*args, **kwargs):
        yield SimpleNamespace(remote=True)

    monkeypatch.setattr(desktop_runtime, 'executor_for', fake_executor)
    monkeypatch.setattr(app.factory, 'run_claimed', lambda claimed, *args: claimed)
    claimed = app.dispatch('execute', {'approved': True, 'job_id': job['id']})
    assert claimed['lease'].resource_id == 'existing_runpod:borrowed'
    store.transition(claimed['lease'], 'FAILED')
    other = store.submit({'sha256': 'canonical'}, 'canonical')
    store.transition(other['id'], 'QUEUED')
    assert store.gpu_locked('existing_runpod:borrowed')
    assert store.claim('existing_runpod:borrowed') is None
    # Ambiguous old strings remain unmapped; their uncertainty quarantines real claims.
    with store.connection() as db:
        assert db.execute('SELECT gpu FROM gpu_locks').fetchone()[0] == 'existing_runpod:borrowed'


def test_plan_ignores_a_stored_observation_with_wrong_value_types(tmp_path):
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    catalog = app.dispatch("catalog", {})
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"instruction": "question", "output": "answer"}]))
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    folder = tmp_path / "gpu-observations"
    folder.mkdir()
    broken = {"key": "runpod-borrowed", "observed_at": 1.0, "gpus": [{**BLACKWELL, "memory_mib": "24467"}],
              "pod": POD_API}
    (folder / "runpod-borrowed.json").write_text(json.dumps(broken))
    compat = app.dispatch("plan", {"config": config})["compatibility"]
    assert compat["observed_at"] is None and ("warn", "OBSERVATION_MISSING") in finding_codes(compat["findings"])


BLACKWELL = {"uuid": "GPU-1", "name": "NVIDIA RTX PRO 4000 Blackwell", "capability": "12.0", "memory_mib": 24467,
             "driver": "595.91.07"}
POD_API = {"gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1}, "costPerHr": 0.57}
A4000_JOB = {"training_stage": "sft", "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
             "resource_profile": {"gpu_type": "NVIDIA RTX A4000", "gpu_count": 1, "hourly_cost_usd": 0.5}}


def finding_codes(findings):
    return {(item["level"], item["code"]) for item in findings}


def test_plan_reports_gpu_compatibility_from_the_stored_observation(tmp_path):
    from tuning_factory.gpu_compat import make_observation, save_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    catalog = app.dispatch("catalog", {})
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"instruction": "question", "output": "answer"}]))
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    first = app.dispatch("plan", {"config": config})["compatibility"]
    assert first["observed_at"] is None and ("warn", "OBSERVATION_MISSING") in finding_codes(first["findings"])
    save_observation(tmp_path, make_observation("runpod-borrowed", [BLACKWELL], POD_API))
    second = app.dispatch("plan", {"config": config})["compatibility"]
    assert second["observed_at"] > 0
    assert ("block", "GPU_TYPE_MISMATCH") in finding_codes(second["findings"])   # the template asks for an A4000
    assert app.dispatch("jobs", {}) == []


def test_runtime_check_judges_a_queued_job_or_a_plan_config(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_compat import make_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    job = app.factory.store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    observation = make_observation("runpod-borrowed", [BLACKWELL], POD_API)
    monkeypatch.setattr(desktop_runtime, "runtime", lambda *a, **kw: {"ready": False, "runtime_state": "missing",
                                                                     "observation": observation})
    report = app.dispatch("runtime_check", {"training_stage": "sft", "job_id": job["id"]})
    assert {("block", "GPU_TYPE_MISMATCH"), ("block", "PRICE_OVER_BUDGET")} <= finding_codes(report["findings"])
    assert "findings" not in app.dispatch("runtime_check", {"training_stage": "sft"})


def test_execute_gates_the_job_and_forwards_runtime_preparation(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_compat import make_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    job = store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    store.transition(job["id"], "QUEUED")
    seen = {}

    @contextmanager
    def fake_executor(settings, root, **kwargs):
        seen.update(kwargs)
        kwargs["gate"](make_observation("runpod-borrowed", [BLACKWELL], POD_API))
        yield SimpleNamespace(remote=True)

    monkeypatch.setattr(desktop_runtime, "executor_for", fake_executor)
    monkeypatch.setattr(app.factory, "run_claimed", lambda *args: pytest.fail("refused job launched"))
    with pytest.raises(ValueError, match="실행할 수 없습니다"):
        app.dispatch("execute", {"approved": True, "job_id": job["id"], "prepare_runtime": True})
    assert seen["prepare_runtime"] is True and store.get(job["id"])["attempts"] == 0
    server = desktop.DesktopService(tmp_path / "server", {"provider": "ssh_server"})
    with pytest.raises(ValueError, match="GPU 서버는 실행 승인에서"):
        server.dispatch("execute", {"approved": True, "job_id": "0" * 32, "prepare_runtime": True})


def test_execute_returns_what_its_runtime_preparation_pruned(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    job = store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    store.transition(job["id"], "QUEUED")
    cleanup = {"removed": ["b" * 16], "kept_in_use": [], "free_before": 1, "free_after": 3}
    prepared = {"value": cleanup}

    @contextmanager
    def fake_executor(settings, root, **kwargs):
        yield SimpleNamespace(remote=True, runtime_cleanup=prepared["value"])

    monkeypatch.setattr(desktop_runtime, "executor_for", fake_executor)
    finished = {"id": job["id"], "status": "COMPLETED"}
    monkeypatch.setattr(app.factory, "work", lambda **kwargs: dict(finished))
    request = {"approved": True, "job_id": job["id"], "prepare_runtime": True}
    assert app.dispatch("execute", request) == {**finished, "runtime_cleanup": cleanup}
    prepared["value"] = None                              # no preparation: the job record is unchanged
    assert app.dispatch("execute", request) == finished
    monkeypatch.setattr(app.factory, "work", lambda **kwargs: None)
    assert app.dispatch("execute", request) is None       # nothing claimed: still the waiting answer


def test_execute_with_preparation_refuses_a_pod_held_by_another_job(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    old = store.submit({"sha256": "old", "simulation": False}, "old")
    store.transition(old["id"], "QUEUED")
    claimed = store.claim("existing_runpod:borrowed")
    store.transition(claimed["lease"], "FAILED")
    second = store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    store.transition(second["id"], "QUEUED")
    monkeypatch.setattr(desktop_runtime, "executor_for", lambda *a, **kw: pytest.fail("held Pod was prepared"))
    with pytest.raises(ValueError, match="다른 작업이 이 Pod를 사용 중"):
        app.dispatch("execute", {"approved": True, "job_id": second["id"], "prepare_runtime": True})


def test_plan_survives_an_observation_key_that_cannot_be_derived(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    catalog = app.dispatch("catalog", {})
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"instruction": "question", "output": "answer"}]))
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})

    def broken(settings):
        raise RuntimeError("no home directory")

    monkeypatch.setattr(desktop_runtime, "observation_key", broken)
    compat = app.dispatch("plan", {"config": config})["compatibility"]
    assert ("warn", "OBSERVATION_MISSING") in finding_codes(compat["findings"])


def test_runtime_check_validates_the_job_before_any_remote_contact(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    monkeypatch.setattr(desktop_runtime, "runtime", lambda *a, **kw: pytest.fail("runtime contacted"))
    with pytest.raises(ValueError):
        app.dispatch("runtime_check", {"training_stage": "sft", "job_id": "not-a-job"})


def test_a_job_from_another_app_version_is_refused_before_any_contact(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    job = store.submit({"sha256": "old", "simulation": False, "config": A4000_JOB, "source_sha256": "0" * 64}, "old")
    store.transition(job["id"], "QUEUED")
    monkeypatch.setattr(desktop_runtime, "runtime", lambda *a, **kw: pytest.fail("runtime contacted"))
    monkeypatch.setattr(desktop_runtime, "executor_for", lambda *a, **kw: pytest.fail("Pod was prepared"))
    with pytest.raises(ValueError) as caught:
        app.dispatch("runtime_check", {"training_stage": "sft", "job_id": job["id"]})
    assert str(caught.value) == desktop.STALE_JOB
    with pytest.raises(ValueError) as caught:
        app.dispatch("execute", {"approved": True, "job_id": job["id"], "prepare_runtime": True})
    assert str(caught.value) == desktop.STALE_JOB


def test_a_stale_job_is_refused_before_resume_resets_it(tmp_path):
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    job = store.submit({"sha256": "old", "simulation": False, "config": A4000_JOB, "source_sha256": "0" * 64}, "old")
    store.transition(job["id"], "QUEUED")
    store.transition(job["id"], "CANCELED")
    with pytest.raises(ValueError) as caught:
        app.dispatch("resume", {"approved": True, "job_id": job["id"], "checkpoint": "checkpoint-1"})
    assert str(caught.value) == desktop.STALE_JOB
    assert store.get(job["id"])["status"] == "CANCELED"


def test_runtime_check_judges_a_job_with_its_own_training_stage(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    job = app.factory.store.submit({"sha256": "p", "simulation": False,
                                    "config": {**A4000_JOB, "training_stage": "ppo"}}, "p")
    seen = {}

    def fake(settings, root, **kwargs):
        seen.update(kwargs)
        return {"ready": False, "runtime_state": "missing", "observation": None}

    monkeypatch.setattr(desktop_runtime, "runtime", fake)
    app.dispatch("runtime_check", {"training_stage": "sft", "job_id": job["id"]})
    assert seen["training_stage"] == "ppo"
