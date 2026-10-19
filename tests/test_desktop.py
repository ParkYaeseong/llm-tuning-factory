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
    with pytest.raises(ValueError, match="reserved"):
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
    with pytest.raises(ValueError, match='reserved'):
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
