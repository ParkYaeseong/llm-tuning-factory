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
