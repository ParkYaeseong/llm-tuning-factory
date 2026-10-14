import pytest

from tuning_factory.evaluation import CommonEvaluator
from tuning_factory.tracking import MLflowTracker


def test_classification_macro_f1_and_baseline():
    ev = CommonEvaluator()
    result = ev.evaluate(["a", "b", "b"], ["a", "a", "b"], "classification")
    assert result["accuracy"] == pytest.approx(2 / 3)
    assert result["macro_f1"] == pytest.approx(2 / 3)
    assert ev.compare_with_baseline(result, {"accuracy": 1})["accuracy"] == pytest.approx(-1 / 3)
    with pytest.raises(ValueError):
        ev.evaluate(["a"], [], "classification")


def test_json_and_extraction_no_invented_scores():
    ev = CommonEvaluator()
    assert ev.evaluate(['{"x":1}', 'bad'], None, "json")["json_validity"] == 0.5
    assert ev.evaluate([{"x": "1", "y": "2"}], [{"x": "1"}], "extraction")["f1"] == pytest.approx(2 / 3)
    assert ev.evaluate([], None, "pipeline") == {}
    with pytest.raises(ValueError, match="ground truth"):
        ev.evaluate(["x"], None, "classification")


def test_mlflow_real_local_tracking(tmp_path, monkeypatch):
    # Local filesystem backend explicitly enabled only inside this isolated test.
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracker = MLflowTracker(tmp_path.as_uri() + "/mlruns")
    run_id = tracker.start("job-test", {"config": {"ai_id": "test", "workflow_id": "test"}, "simulation": True})
    artifact = tmp_path / "manifest.json"
    artifact.write_text('{"simulation":true}')
    tracker.finish(run_id, {"pipeline_completed": 1.0}, tmp_path, "COMPLETED")
    run = tracker.client.get_run(run_id)
    assert run.info.status == "FINISHED"
    assert run.data.tags["factory.job_id"] == "job-test"
    assert run.data.metrics["pipeline_completed"] == 1.0
    assert tracker.client.list_artifacts(run_id, "evidence")


def test_artifact_failure_terminates_run_as_failed(tmp_path, monkeypatch):
    monkeypatch.setenv("MLFLOW_ALLOW_FILE_STORE", "true")
    tracker = MLflowTracker(tmp_path.as_uri() + "/mlruns")
    run_id = tracker.start("failed-persistence", {"config": {"ai_id": "test", "workflow_id": "test"}, "simulation": True})
    (tmp_path / "manifest.json").write_text("{}")

    def broken_upload(*args, **kwargs):
        raise RuntimeError("test artifact failure")

    monkeypatch.setattr(tracker.client, "log_artifact", broken_upload)
    with pytest.raises(RuntimeError, match="artifact failure"):
        tracker.finish(run_id, {}, tmp_path, "COMPLETED")
    assert tracker.client.get_run(run_id).info.status == "FAILED"


def test_invalid_generated_extraction_json_cannot_score_as_empty_object():
    metrics = CommonEvaluator().evaluate(["invalid JSON"], ["{}"], "extraction")
    assert metrics["accuracy"] == 0
    assert metrics["f1"] == 0
