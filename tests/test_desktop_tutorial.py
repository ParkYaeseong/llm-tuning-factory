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
    {"dataset_id": "Tutorial_Sample"},
])
def test_reserved_tutorial_id_rejects_changed_registration(tmp_path, overrides):
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(desktop.DesktopService(tmp_path), SAMPLE, **overrides)


def test_reserved_tutorial_id_rejects_other_content(tmp_path):
    other = tmp_path / "real.json"
    other.write_text(json.dumps([{"instruction": "q", "output": "a"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(desktop.DesktopService(tmp_path / "state"), other)


def test_reserved_tutorial_id_cannot_be_imported_from_s3(tmp_path):
    app = desktop.DesktopService(tmp_path)
    with pytest.raises(ValueError, match="tutorial_sample"):
        app.dispatch("s3_import", {"dataset_id": "TUTORIAL_SAMPLE", "version": "1", "approved": True,
                                   "bucket": "bucket", "key": "key"})


def test_tutorial_plan_is_dry_run_and_submission_is_refused(tmp_path):
    app = desktop.DesktopService(tmp_path)
    register(app, app.dispatch("tutorial_sample", {})["path"])
    templates = app.dispatch("catalog", {})["templates"]
    config = next(x["config"] for x in templates if x["name"] == "paper_extraction")
    config.update(dataset_id="tutorial_sample", dataset_version="1")
    plan = app.dispatch("plan", {"config": config})
    assert plan["dry_run"] is True and plan["manifest"]["dataset"]["rows"] == 8
    with pytest.raises(ValueError, match="export has not been authorized"):
        app.dispatch("submit", {"config": config, "approved": True})
    assert app.dispatch("jobs", {}) == []
