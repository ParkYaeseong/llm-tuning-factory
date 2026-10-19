import json
from pathlib import Path

import pytest

from tuning_factory import desktop
from tuning_factory.registry import digest

SAMPLE = Path(__file__).resolve().parents[1] / "examples/tutorial_sample.json"


def register(app, path, **overrides):
    data = {"dataset_id": "tutorial_sample", "version": "1", "kind": "instruction", "path": str(path),
            "synthetic": True, "export_allowed": False, **overrides}
    return app.dispatch("dataset", data)


def test_bundled_sample_is_fictional_korean_alpaca():
    rows = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert len(rows) == 8
    assert all(set(row) == {"instruction", "input", "output"} for row in rows)
    assert all("가상" in row["instruction"] and row["input"].startswith("연습용") for row in rows)


def test_tutorial_sample_copies_the_bundle_into_persistent_state(tmp_path):
    result = desktop.DesktopService(tmp_path).dispatch("tutorial_sample", {})
    copy = tmp_path / "tutorial" / "tutorial_sample.json"
    assert result == {"dataset_id": "tutorial_sample", "version": "1", "kind": "instruction",
                      "path": str(copy), "sha256": digest(SAMPLE.read_bytes()), "rows": 8}
    assert copy.read_bytes() == SAMPLE.read_bytes()


def test_tutorial_sample_registers_repeatably_without_gpu_export(tmp_path):
    app = desktop.DesktopService(tmp_path)
    path = app.dispatch("tutorial_sample", {})["path"]
    first = register(app, path)
    assert register(app, path) == first
    assert first["synthetic"] is True and first["export_allowed"] is False and first["rows"] == 8


@pytest.mark.parametrize("overrides", [
    {"export_allowed": True}, {"synthetic": False}, {"version": "2"}, {"kind": "text"},
    {"dataset_id": "Tutorial_Sample"}, {"dataset_id": "tutorial_sample."}, {"dataset_id": "Tutorial_Sample.."},
])
def test_reserved_tutorial_id_rejects_changed_registration(tmp_path, overrides):
    app = desktop.DesktopService(tmp_path)
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(app, SAMPLE, **overrides)
    assert app.dispatch("catalog", {})["datasets"] == []


def test_reserved_tutorial_id_rejects_other_content(tmp_path):
    other = tmp_path / "real.json"
    other.write_text(json.dumps([{"instruction": "q", "output": "a"}]), encoding="utf-8")
    app = desktop.DesktopService(tmp_path / "state")
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(app, other)
    assert app.dispatch("catalog", {})["datasets"] == []


@pytest.mark.parametrize("dataset_id", ["TUTORIAL_SAMPLE", "tutorial_sample.", "Tutorial_Sample.."])
def test_reserved_tutorial_id_cannot_be_imported_from_s3(tmp_path, dataset_id):
    app = desktop.DesktopService(tmp_path)
    with pytest.raises(ValueError, match="tutorial_sample"):
        app.dispatch("s3_import", {"dataset_id": dataset_id, "version": "1", "approved": True,
                                   "bucket": "bucket", "key": "key"})
    assert app.dispatch("catalog", {})["datasets"] == []


def test_bundled_sample_hash_is_pinned():
    # The v1 record is immutable: editing the sample would break re-registration for existing users.
    assert digest(SAMPLE.read_bytes()) == "18f12ce652c882402c82ed5855ab636b9bf75062dac793901c71d557d9d42604"


def test_registration_without_export_key_stores_false(tmp_path):
    app = desktop.DesktopService(tmp_path)
    path = app.dispatch("tutorial_sample", {})["path"]
    data = {"dataset_id": "tutorial_sample", "version": "1", "path": path, "synthetic": True}
    assert app.dispatch("dataset", data)["export_allowed"] is False


def test_concurrent_copy_permission_error_is_tolerated_when_copy_matches(tmp_path, monkeypatch):
    app = desktop.DesktopService(tmp_path)
    app.dispatch("tutorial_sample", {})

    def locked(*args):
        raise PermissionError(32, "sharing violation")

    # Another process finished the identical copy first, so the replace failure is harmless.
    copy = tmp_path / "tutorial" / "tutorial_sample.json"
    copy.write_bytes(b"stale")
    original = Path.read_bytes
    calls = {"n": 0}

    def racing_read(self):
        if self == copy:
            calls["n"] += 1
            if calls["n"] > 1:
                return SAMPLE.read_bytes()
        return original(self)

    monkeypatch.setattr(desktop.os, "replace", locked)
    monkeypatch.setattr(Path, "read_bytes", racing_read)
    assert app.dispatch("tutorial_sample", {})["rows"] == 8
    assert list((tmp_path / "tutorial").glob("*.tmp")) == []
    assert [p.name for p in (tmp_path / "tutorial").iterdir()] == ["tutorial_sample.json"]


def test_tutorial_plan_is_dry_run_and_submission_is_refused(tmp_path):
    app = desktop.DesktopService(tmp_path)
    register(app, app.dispatch("tutorial_sample", {})["path"])
    templates = app.dispatch("catalog", {})["templates"]
    config = next(x["config"] for x in templates if x["name"] == "paper_extraction")
    config.update(dataset_id="tutorial_sample", dataset_version="1")
    plan = app.dispatch("plan", {"config": config})
    assert plan["dry_run"] is True and plan["manifest"]["dataset"]["rows"] == 8
    with pytest.raises(ValueError) as error:
        app.dispatch("submit", {"config": config, "approved": True})
    assert str(error.value) == desktop.PRACTICE_SUBMIT_REFUSAL
    assert app.dispatch("jobs", {}) == []


def test_practice_dataset_is_refused_at_submit_even_as_validation_data_or_with_export_allowed(tmp_path):
    app = desktop.DesktopService(tmp_path)
    config = next(x["config"] for x in app.dispatch("catalog", {})["templates"] if x["name"] == "paper_extraction")
    app.dispatch("dataset", {"dataset_id": "real", "version": "1", "path": str(SAMPLE), "export_allowed": True})
    config.update(dataset_id="real", dataset_version="1")
    config["evaluation_profile"].update(kind="extraction", dataset_id="Tutorial_Sample", dataset_version="1")
    with pytest.raises(ValueError) as error:
        app.dispatch("submit", {"config": config, "approved": True})
    assert str(error.value) == desktop.PRACTICE_SUBMIT_REFUSAL
    config["evaluation_profile"].update(dataset_id=None, dataset_version=None)
    config.update(dataset_id="TUTORIAL_SAMPLE")
    with pytest.raises(ValueError) as error:
        app.dispatch("submit", {"config": config, "approved": True})
    assert str(error.value) == desktop.PRACTICE_SUBMIT_REFUSAL


def test_unauthorized_export_is_still_refused_for_other_datasets(tmp_path):
    app = desktop.DesktopService(tmp_path)
    app.dispatch("dataset", {"dataset_id": "private", "version": "1", "path": str(SAMPLE)})
    config = next(x["config"] for x in app.dispatch("catalog", {})["templates"] if x["name"] == "paper_extraction")
    config.update(dataset_id="private", dataset_version="1")
    with pytest.raises(ValueError, match="export has not been authorized"):
        app.dispatch("submit", {"config": config, "approved": True})


def test_copy_permission_error_is_reraised_when_copy_differs(tmp_path, monkeypatch):
    app = desktop.DesktopService(tmp_path)

    def locked(*args):
        raise PermissionError(32, "sharing violation")

    monkeypatch.setattr(desktop.os, "replace", locked)
    monkeypatch.setattr(desktop.time, "sleep", lambda seconds: None)
    with pytest.raises(PermissionError):
        app.dispatch("tutorial_sample", {})
    assert list((tmp_path / "tutorial").glob("*.tmp")) == []
