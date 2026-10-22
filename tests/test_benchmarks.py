"""Common conditions and explicit objectives must govern model selection."""
from dataclasses import FrozenInstanceError

import pytest

from tuning_factory.evaluation import CommonEvaluator
from tuning_factory.model_artifacts import ModelArtifactRef
from tuning_factory.projects import DatasetRef


def benchmark(**changes):
    from tuning_factory.benchmarks import BenchmarkSpec

    values = dict(dataset=DatasetRef(dataset_id="answers", version="v1", sha256="a" * 64),
                  evaluator_kind="text", evaluator_version="1", ordered_sample_ids=("one", "two"),
                  max_new_tokens=100, cutoff_len=512, seed=42, precision="bf16", quantization_bit=None,
                  tokenizer_fingerprint="b" * 64, template="plain", semantic_model_kind="causal_lm")
    return BenchmarkSpec(**(values | changes))


def model(letter):
    return ModelArtifactRef(artifact_id=letter * 64, inventory_sha256="f" * 64)


def record(letter, score=0.8, **changes):
    from tuning_factory.benchmarks import EvaluationRecord

    values = dict(model=model(letter), benchmark_sha256=benchmark().fingerprint(), metrics={"exact_match": score},
                  status="completed", synthetic=False, simulation=False, producer_attempt_id="attempt-1")
    return EvaluationRecord(**(values | changes))


def select(records, **changes):
    from tuning_factory.benchmarks import SelectionSpec, select_best

    return select_best(records, SelectionSpec(metric="exact_match", direction="max", **changes))


def test_text_normalizes_korean_nfc_and_whitespace():
    metrics = CommonEvaluator().evaluate(["한글\t 답  \n"], ["한글 답"], "text")
    assert metrics == {"exact_match": 1.0, "character_f1": 1.0}


@pytest.mark.parametrize(("prediction", "reference", "exact", "f1"), [
    ("가 가 나", "가 나 나", 0, 2 / 3),
    ("가나다", "가나", 0, 0.8),
    ("가나", "나가", 0, 1),
    ("  ", "", 1, 1),
    ("가", "", 0, 0),
])
def test_text_multiset_f1_counts_unicode_characters(prediction, reference, exact, f1):
    result = CommonEvaluator().evaluate([prediction], [reference], "text")
    assert result["exact_match"] == exact
    assert result["character_f1"] == pytest.approx(f1)


def test_text_metrics_are_reference_agreement_and_validate_strings():
    from tuning_factory.evaluation import metric_schema

    schema = metric_schema("text")
    assert {entry.name for entry in schema} == {"exact_match", "character_f1"}
    assert all(entry.category == "text_answer_agreement" for entry in schema)
    assert all(entry.direction == "max" for entry in schema)
    with pytest.raises(ValueError, match="strings"):
        CommonEvaluator().evaluate([{}], ["answer"], "text")
    with pytest.raises(ValueError, match="ground truth"):
        CommonEvaluator().evaluate(["answer"], None, "text")


@pytest.mark.parametrize("changes", [
    {"ordered_sample_ids": ("two", "one")}, {"seed": 43}, {"precision": "fp16"},
    {"quantization_bit": 4}, {"evaluator_version": "2"}, {"max_new_tokens": 101},
    {"cutoff_len": 1024}, {"generation_mode": "sample"}, {"template": "chat"},
    {"tokenizer_fingerprint": "c" * 64}, {"semantic_model_kind": "reward_model"},
    {"dataset": DatasetRef(dataset_id="answers", version="v2", sha256="d" * 64)},
])
def test_benchmark_changes_block_comparison(changes):
    from tuning_factory.benchmarks import SelectionSpec, select_best

    original = benchmark().fingerprint()
    changed = benchmark(**changes).fingerprint()
    assert original != changed
    with pytest.raises(ValueError, match="same benchmark"):
        select_best([record("a"), record("b", benchmark_sha256=changed)],
                    SelectionSpec(metric="exact_match", direction="max"))


def test_benchmark_rejects_storage_kind_and_duplicate_samples():
    with pytest.raises(ValueError):
        benchmark(storage_kind="adapter")
    with pytest.raises(ValueError, match="duplicate"):
        benchmark(ordered_sample_ids=("one", "one"))


@pytest.mark.parametrize("kind", ["text", "kbf_extraction"])
def test_benchmark_fingerprint_changes_with_actual_scoring_source(tmp_path, monkeypatch, kind):
    from tuning_factory import evaluation

    wrapper = tmp_path / "evaluation.py"
    helper = tmp_path / "evaluation_kbf.py"
    wrapper.write_text("scoring wrapper version one", encoding="utf-8")
    helper.write_text("scoring helper version one", encoding="utf-8")
    monkeypatch.setattr(evaluation, "__file__", str(wrapper))
    spec = benchmark(evaluator_kind=kind)
    original = spec.fingerprint()
    changed_source = helper if kind == "kbf_extraction" else wrapper
    changed_source.write_text("scoring version two", encoding="utf-8")
    assert spec.fingerprint() != original


def test_explicit_max_min_select_opposite_winners_and_ties_use_artifact_id():
    from tuning_factory.benchmarks import SelectionSpec, select_best

    records = [record("b", 0.9), record("a", 0.7)]
    assert select(records).winner == model("b")
    assert select_best(records, SelectionSpec(metric="exact_match", direction="min")).winner == model("a")
    assert select([record("b"), record("a")]).winner == model("a")


def test_finite_integer_metric_does_not_overflow_during_eligibility_check():
    assert select([record("a", 10 ** 1000), record("b", 1)]).winner == model("a")


@pytest.mark.parametrize(("changes", "reason"), [
    ({"metrics": {"exact_match": float("nan")}}, "nonfinite_metric"),
    ({"metrics": {"exact_match": float("inf")}}, "nonfinite_metric"),
    ({"metrics": {"exact_match": "0.8"}}, "invalid_metric"),
    ({"metrics": {"exact_match": True}}, "invalid_metric"),
    ({"metrics": {}}, "missing_metric"),
    ({"metrics": {"exact_match": 0.8, "other": float("inf")}}, "nonfinite_metric"),
    ({"synthetic": True}, "synthetic"), ({"simulation": True}, "simulation"),
    ({"status": "unevaluated"}, "unevaluated"), ({"status": "failed"}, "failed"),
    ({"status": "skipped"}, "skipped"), ({"producer_attempt_id": None}, "missing_producer_attempt"),
])
def test_ineligible_evidence_never_selects(changes, reason):
    result = select([record("a", **changes)])
    assert result.winner is None
    assert result.status == "no_winner"
    assert reason in result.excluded[0].reasons


def test_thresholds_are_independent_explicit_objectives():
    from tuning_factory.benchmarks import MetricThreshold

    thresholds = (MetricThreshold(metric="character_f1", direction="min", value=0.2),)
    result = select([record("a", 0.9, metrics={"exact_match": 0.9, "character_f1": 0.3}),
                     record("b", 0.7, metrics={"exact_match": 0.7, "character_f1": 0.1})],
                    thresholds=thresholds)
    assert result.winner == model("b")
    assert "threshold_failed:character_f1" in result.excluded[0].reasons
    assert select([record("a")], thresholds=thresholds).winner is None


def test_default_waits_for_all_requested_candidates_but_terminal_failure_finishes():
    from tuning_factory.benchmarks import SelectionSpec, select_best

    selection = SelectionSpec(metric="exact_match", direction="max")
    waiting = select_best([record("a")], selection, requested_models=(model("a"), model("b")))
    assert waiting.status == "waiting"
    assert waiting.winner is None
    assert waiting.requested_models == (model("a"), model("b"))
    assert waiting.observed_models == (model("a"),)
    assert waiting.excluded[0].reasons == ("missing_record",)
    assert select([record("a"), record("b", status="pending")]).status == "waiting"
    assert select([record("a"), record("b", status="running")]).status == "waiting"
    terminal = select([record("a"), record("b", status="failed")])
    assert terminal.status == "no_winner"
    assert terminal.winner is None
    assert terminal.reasons == ("partial_selection_not_allowed",)
    assert select([record("a")]).winner == model("a")


@pytest.mark.parametrize("direction", ["max", "min"])
@pytest.mark.parametrize("outcome", ["failed", "cancelled", "skipped", "unevaluated"])
@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("success_count", [1, 2])
def test_terminal_evaluation_outcomes_require_explicit_partial_and_minimum_successes(
        direction, outcome, allow_partial, success_count):
    from tuning_factory.benchmarks import SelectionSpec, select_best

    records = [record("a", 0.9), record("b", 0.7)][:success_count]
    records.append(record("c", status=outcome))
    result = select_best(records, SelectionSpec(metric="exact_match", direction=direction,
                                               allow_partial=allow_partial))
    assert result.status != "waiting"
    if allow_partial and success_count == 2:
        assert result.status == "selected"
        assert result.winner == model("a" if direction == "max" else "b")
        assert result.reasons == ("partial_selection",)
    else:
        assert result.status == "no_winner"
        assert result.winner is None
        assert result.reasons == (("insufficient_partial_success",) if allow_partial
                                  else ("partial_selection_not_allowed",))
    assert result.excluded[-1].reasons == (outcome,)


@pytest.mark.parametrize("direction", ["max", "min"])
@pytest.mark.parametrize("allow_partial", [False, True])
def test_complete_evaluation_set_applies_quality_thresholds_independently_of_partial_permission(
        direction, allow_partial):
    from tuning_factory.benchmarks import MetricThreshold, SelectionSpec, select_best

    selection = SelectionSpec(metric="exact_match", direction=direction, allow_partial=allow_partial,
                              thresholds=(MetricThreshold(metric="exact_match", direction=direction, value=0.8),))
    result = select_best([record("a", 0.9), record("b", 0.7)], selection)
    assert result.status == "selected"
    assert result.winner == model("a" if direction == "max" else "b")
    assert result.excluded[0].reasons == ("threshold_failed:exact_match",)
    assert "partial_selection" not in result.reasons
    assert "insufficient_partial_success" not in result.reasons


@pytest.mark.parametrize("direction", ["max", "min"])
def test_actual_partial_join_still_requires_two_quality_eligible_candidates(direction):
    from tuning_factory.benchmarks import MetricThreshold, SelectionSpec, select_best

    selection = SelectionSpec(metric="exact_match", direction=direction, allow_partial=True,
                              thresholds=(MetricThreshold(metric="exact_match", direction=direction, value=0.8),))
    result = select_best([record("a", 0.9), record("b", 0.7), record("c", status="failed")], selection)
    assert result.status == "no_winner"
    assert result.winner is None
    assert result.reasons == ("insufficient_partial_success",)


@pytest.mark.parametrize("direction", ["max", "min"])
@pytest.mark.parametrize("allow_partial", [False, True])
def test_complete_successful_single_candidate_ignores_partial_minimum(direction, allow_partial):
    from tuning_factory.benchmarks import SelectionSpec, select_best

    result = select_best([record("a")], SelectionSpec(metric="exact_match", direction=direction,
                                                     allow_partial=allow_partial))
    assert result.status == "selected"
    assert result.winner == model("a")


def test_partial_selection_requires_explicit_opt_in_and_two_successes():
    records = [record("a", 0.9), record("b", 0.7), record("c", status="pending")]
    assert select(records).winner is None
    assert select(records, allow_partial=True).winner == model("a")
    assert select(records[::2], allow_partial=True).winner is None
    assert select([record("a")], allow_partial=True).winner == model("a")
    from tuning_factory.benchmarks import SelectionSpec
    with pytest.raises(ValueError):
        SelectionSpec(metric="exact_match", direction="max", allow_partial=True, min_success=1)


def test_unknown_and_duplicate_candidates_cannot_change_selection():
    from tuning_factory.benchmarks import SelectionSpec, select_best

    with pytest.raises(ValueError, match="duplicate"):
        select([record("a"), record("a", 0.1)])
    with pytest.raises(ValueError, match="unrequested"):
        select_best([record("a"), record("b")], SelectionSpec(metric="exact_match", direction="max"),
                    requested_models=(model("a"),))


def test_selection_receipt_copies_and_freezes_scores_and_is_json_serializable():
    from tuning_factory.benchmarks import EvaluationRecord, SelectionSpec, select_best

    source = {"exact_match": 0.8}
    candidate = record("a", metrics=source)
    source["exact_match"] = 0
    result = select([candidate])
    assert result.eligible[0].metrics["exact_match"] == 0.8
    with pytest.raises(TypeError):
        result.eligible[0].metrics["exact_match"] = 0
    with pytest.raises((ValueError, FrozenInstanceError)):
        result.winner = model("b")
    assert EvaluationRecord.model_validate_json(candidate.model_dump_json()) == candidate
    assert type(result).model_validate_json(result.model_dump_json()) == result
    assert select_best([], SelectionSpec(metric="json_validity", direction="max")).winner is None


@pytest.mark.parametrize(("direction", "original", "merged", "winner"), [
    ("max", 0.8, 0.7, "a"), ("min", 0.7, 0.8, "a"),
    ("max", 0.8, 0.9, "b"), ("min", 0.8, 0.7, "b"),
    ("max", 0.8, 0.8, "a"),
])
def test_merge_retains_original_on_directional_regression(direction, original, merged, winner):
    from tuning_factory.benchmarks import SelectionSpec, finalize_with_merge

    result = finalize_with_merge(record("a", original), record("b", merged),
                                 SelectionSpec(metric="exact_match", direction=direction))
    assert result.winner == model(winner)
    if (direction == "max" and merged < original) or (direction == "min" and merged > original):
        assert "merge_regression" in result.reasons


def test_merge_requires_eligible_same_benchmark_and_honors_tolerance():
    from tuning_factory.benchmarks import SelectionSpec, finalize_with_merge

    selection = SelectionSpec(metric="exact_match", direction="max", merge_regression_tolerance=0.1)
    assert finalize_with_merge(record("a", 0.8), record("b", 0.75), selection).winner == model("b")
    assert finalize_with_merge(record("a"), record("b", synthetic=True), selection).winner == model("a")
    assert finalize_with_merge(record("a", status="failed"), record("b"), selection).winner is None
    with pytest.raises(ValueError, match="same benchmark"):
        finalize_with_merge(record("a"), record("b", benchmark_sha256="e" * 64), selection)


@pytest.mark.parametrize(("direction", "original", "merged"), [("max", 0.8, 0.7), ("min", 0.7, 0.8)])
def test_merge_accepts_exact_decimal_tolerance_boundary(direction, original, merged):
    from tuning_factory.benchmarks import SelectionSpec, finalize_with_merge

    selection = SelectionSpec(metric="exact_match", direction=direction, merge_regression_tolerance=0.1)
    assert finalize_with_merge(record("a", original), record("b", merged), selection).winner == model("b")


def test_merge_finalization_waits_for_pending_evaluation():
    from tuning_factory.benchmarks import SelectionSpec, finalize_with_merge

    result = finalize_with_merge(record("a"), record("b", status="pending"),
                                 SelectionSpec(metric="exact_match", direction="max"))
    assert result.status == "waiting"
    assert result.winner is None


@pytest.mark.parametrize("allow_partial", [False, True])
@pytest.mark.parametrize("merged", [{"score": float("nan")}, {"metrics": {}}, {"synthetic": True},
                                    {"status": "failed"}])
def test_a_merge_fallback_never_names_a_partial_refusal_next_to_its_winner(allow_partial, merged):
    """Task 11.5 (R5 M4): an ineligible merged evaluation leaves the original selected with the one reason
    ``merged_not_eligible``; the pairwise comparison's ``partial_selection_not_allowed`` or
    ``insufficient_partial_success`` described a refusal, not this receipt. The exclusion evidence stays."""
    from tuning_factory.benchmarks import SelectionSpec, finalize_with_merge

    result = finalize_with_merge(record("a"), record("b", **merged),
                                 SelectionSpec(metric="exact_match", direction="max", allow_partial=allow_partial))
    assert (result.status, result.winner, result.reasons) == ("selected", model("a"), ("merged_not_eligible",))
    assert [item.model for item in result.excluded] == [model("b")] and result.excluded[0].reasons


def test_receipts_with_a_nonfinite_score_are_the_same_evidence_after_a_round_trip():
    from tuning_factory.benchmarks import SelectionResult, same_receipt

    receipt = select((record("a"), record("b", float("nan")), record("c", 0.7)), allow_partial=True)
    again = SelectionResult.model_validate_json(receipt.model_dump_json())
    assert receipt != again   # NaN never equals NaN: the reason receipts compare by canonical JSON
    assert same_receipt(receipt, again)
    assert not same_receipt(receipt, select((record("a"), record("c", 0.7)), allow_partial=True))
