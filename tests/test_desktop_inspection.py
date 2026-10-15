import json

import pytest

from tuning_factory.desktop_inspection import inspect_dataset, parameter_changes, parse_progress


def test_preview_validates_and_bounds_samples_without_copying(tmp_path):
    path = tmp_path / "data.jsonl"
    row = {"instruction": "x" * 5000, "output": "answer"}
    path.write_text("\n".join(json.dumps(row) for _ in range(6)))
    report = inspect_dataset(path)
    assert report["rows"] == 6
    assert report["duplicate_rows"] == 5
    assert report["format"] == "alpaca"
    assert len(report["samples"]) == 3
    assert len(report["samples"][0]["instruction"]) <= 2001
    assert list(tmp_path.iterdir()) == [path]


def test_preview_invalid_data_is_not_accepted(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text('[{"instruction":"missing answer"}]')
    with pytest.raises(ValueError):
        inspect_dataset(path)


def test_native_progress_handles_partial_and_nonfinite_records():
    text = '\n'.join([
        '{"current_steps":1,"total_steps":10,"loss":2.5,"elapsed_time":"1s"}',
        '{"current_steps":2,"total_steps":10,"loss":2.0}',
        '{"current_steps":3,"total_steps":10,"loss":NaN}',
        '{"current_steps":4,"loss":',
    ])
    report = parse_progress(text)
    assert report["current_step"] == 3
    assert report["percent"] == 30
    assert report["loss"] == 2.0
    assert len(report["points"]) == 2
    assert parse_progress("")["available"] is False


def test_progress_downsamples_without_losing_final_point():
    text = "\n".join(json.dumps({"current_steps": i, "total_steps": 1000, "loss": 1 / i})
                     for i in range(1, 1001))
    report = parse_progress(text)
    assert len(report["points"]) <= 200
    assert report["points"][-1]["step"] == 1000
    assert report["percent"] == 100


def test_parameter_diff_is_nested_and_distinguishes_missing_values():
    report = parameter_changes({"model": "q", "hyper": {"rank": 8}, "a": None},
                               {"model": "q", "hyper": {"rank": 16}, "new": True})
    assert {x["parameter"] for x in report} == {"hyper.rank", "a", "new"}
    assert next(x for x in report if x["parameter"] == "a")["candidate_missing"]
