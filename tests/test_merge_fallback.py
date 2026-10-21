"""Task 11.5 (R5 I3, root's decision on R5 M4): the graph-level merge fallback.

A selection over the evaluations of original models and of a merged model (``evaluate(merge(...))``) keeps the best
original when the merge regresses or its evaluation is not eligible, and selects the merged model when it improves.
Root kept today's fail-closed rule: without ``allow_partial``, a failed merge or a failed merged evaluation blocks the
selection (``graph_store._readiness``); with ``allow_partial`` the best original is chosen.

Driven through ``test_graph_controller``'s world (the real ``GraphController``, ``GraphStore``, compiler and
``resolve_local_selection``; ``ScriptedDispatcher`` at the GPU boundary). No GPU, SSH, network or MLflow write.
"""
import json
import math
import socket

import pytest
import test_graph_controller as gct
import test_graph_readiness as readiness
import test_tracking_outbox as outbox_tests

from tuning_factory.model_artifacts import ModelArtifactRef


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """As ``test_graph_controller``'s fixture: no network, no real MLflow client, no OIDC refresher, none of the
    developer's MLflow settings, and only the outbox's receipt opens the tracking gate."""
    def refuse(*args, **kwargs):
        raise AssertionError("merge fallback tests must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    for key in outbox_tests.SECRET_VARIABLES:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    for key in ("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "MLFLOW_HTTP_REQUEST_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("mlflow.MlflowClient", lambda *a, **k: pytest.fail("a real MLflow client was created"))
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    monkeypatch.setattr("tuning_factory.graph_store.GraphStore.test_acknowledgement", False)


def fallback(**selection):
    """e1 and e2 evaluate the two frozen adapters; m merges them; em evaluates the merge; s selects among all three."""
    def nodes(r):
        merge = {"kind": "merge", "id": "m", "label": "Merge", "weights": [0.5, 0.5],
                 "models": [r.first.model_dump(mode="json"), r.second.model_dump(mode="json")],
                 "executor_profile_ids": ["p1"]}
        return [readiness.evaluate(r, "e1", r.first), readiness.evaluate(r, "e2", r.second), merge,
                readiness.evaluate(r, "em", "m"), readiness.select("s", ("e1", "e2", "em"), **selection)]
    return nodes


def driven(tmp_path, monkeypatch, *, accuracy, script=None, **selection):
    w = gct.world(tmp_path, monkeypatch, fallback(**selection))
    w.dispatcher.accuracy.update(accuracy)
    w.dispatcher.script.update(script or {})
    controller, owner = gct.start(w)
    gct.drive(w, controller, owner)
    return w


def payload(w, node_id):
    with w.store.jobs.connection() as db:
        row = db.execute("SELECT outputs FROM node_outputs WHERE run_id=? AND node_id=?", (w.run_id, node_id)).fetchone()
    return None if row is None else json.loads(row[0])


def errors(w):
    return {item["node_id"]: (item["status"], item["error"]) for item in gct.run_row(w)["nodes"]}


def merged_model(w):
    return ModelArtifactRef.model_validate(payload(w, "m")["model"]["model"])


def selected(w):
    """The selection's winner and its receipt (a winner's receipt is kept with its model binding)."""
    model = payload(w, "s")["model"]
    return ModelArtifactRef.model_validate(model["model"]), model["selection"]


def test_a_merge_that_regresses_keeps_the_best_original(tmp_path, monkeypatch):
    w = driven(tmp_path, monkeypatch, accuracy={"e1": 0.6, "e2": 0.5, "em": 0.4})
    assert set(gct.statuses(w).values()) == {"COMPLETED"} and gct.run_row(w)["status"] == "COMPLETED"
    winner, receipt = selected(w)
    assert winner == w.refs.first and receipt["status"] == "selected"
    assert receipt["reasons"] == ["merge_regression"]
    assert {item["model"]["artifact_id"] for item in receipt["eligible"]} == {
        w.refs.first.artifact_id, merged_model(w).artifact_id}
    assert gct.status_of(w, "s")["selection"] == w.refs.first.artifact_id


def test_a_merge_that_improves_is_selected(tmp_path, monkeypatch):
    w = driven(tmp_path, monkeypatch, accuracy={"e1": 0.6, "e2": 0.5, "em": 0.9})
    assert set(gct.statuses(w).values()) == {"COMPLETED"}
    winner, receipt = selected(w)
    assert winner == merged_model(w) and receipt["status"] == "selected" and receipt["reasons"] == []
    assert gct.status_of(w, "s")["selection"] == merged_model(w).artifact_id


def test_an_ineligible_merged_evaluation_keeps_the_best_original_with_one_consistent_reason(tmp_path, monkeypatch):
    """A completed merged evaluation without a finite score is excluded; the receipt says why the original stands
    (``merged_not_eligible``) and never names a partial-selection refusal next to its winner (R5 M4)."""
    w = driven(tmp_path, monkeypatch, accuracy={"e1": 0.6, "e2": 0.5, "em": math.nan})
    assert set(gct.statuses(w).values()) == {"COMPLETED"}, errors(w)
    winner, receipt = selected(w)
    assert winner == w.refs.first and receipt["status"] == "selected"
    assert receipt["reasons"] == ["merged_not_eligible"]
    [excluded] = receipt["excluded"]
    assert excluded["model"] == merged_model(w).model_dump(mode="json")
    assert excluded["reasons"] == ["nonfinite_metric"]


@pytest.mark.parametrize("failing", ["m", "em"])
def test_without_allow_partial_a_failed_merge_or_merged_evaluation_blocks_the_selection(tmp_path, monkeypatch,
                                                                                         failing):
    """Root's decision: fail closed. Nothing is selected although both originals were evaluated."""
    w = driven(tmp_path, monkeypatch, accuracy={"e1": 0.6, "e2": 0.5, "em": 0.9}, script={failing: ["fail_remote"]})
    expected = {"e1": "COMPLETED", "e2": "COMPLETED", "m": "COMPLETED", "em": "COMPLETED", "s": "BLOCKED",
                failing: "FAILED"}
    if failing == "m":
        expected["em"] = "BLOCKED"
    assert gct.statuses(w) == expected
    assert gct.node(w, "s")["error"] == "partial selection is not allowed" and payload(w, "s") is None
    assert gct.run_row(w)["status"] == "FAILED"


@pytest.mark.parametrize("failing", ["m", "em"])
def test_with_allow_partial_a_failed_merge_or_merged_evaluation_selects_the_best_original(tmp_path, monkeypatch,
                                                                                           failing):
    w = driven(tmp_path, monkeypatch, accuracy={"e1": 0.6, "e2": 0.5, "em": 0.9}, script={failing: ["fail_remote"]},
               allow_partial=True)
    assert gct.statuses(w)["s"] == "COMPLETED" and gct.statuses(w)[failing] == "FAILED"
    winner, receipt = selected(w)
    assert winner == w.refs.first and receipt["status"] == "selected"
    assert {item["model"]["artifact_id"] for item in receipt["eligible"]} == {
        w.refs.first.artifact_id, w.refs.second.artifact_id}
    assert gct.run_row(w)["status"] == "FAILED"   # a failed node still fails the run


def test_a_nonfinite_score_never_breaks_the_selection_receipt(tmp_path, monkeypatch):
    """Found while pinning R5 I3: a receipt that excludes a NaN score was compared with ``==`` after a JSON round
    trip, where NaN never equals NaN, so the selection failed with "selection receipt mismatch" instead of excluding
    the candidate. Receipts now compare by their canonical JSON (the nonfinite marker), in the store and in the
    compiler of the selection's child."""
    def nodes(r):
        return [readiness.evaluate(r, "e1", r.base), readiness.evaluate(r, "e2", r.first),
                readiness.evaluate(r, "e3", r.second), readiness.select("s", ("e1", "e2", "e3"), allow_partial=True),
                readiness.evaluate(r, "after", "s")]

    w = gct.world(tmp_path, monkeypatch, nodes)
    w.dispatcher.accuracy.update(e1=math.nan, e2=0.4, e3=0.7)
    controller, owner = gct.start(w)
    gct.drive(w, controller, owner)
    assert set(gct.statuses(w).values()) == {"COMPLETED"}, errors(w)
    winner, receipt = selected(w)
    assert winner == w.refs.second and receipt["reasons"] == ["partial_selection"]
    [excluded] = receipt["excluded"]
    assert excluded["reasons"] == ["nonfinite_metric"]
    assert excluded["record"]["metrics"] == {"accuracy": {"nonfinite": "nan"}}
    assert w.store.jobs.get(gct.job_of(w, "after"))["manifest"]["operation"]["model"] == w.refs.second.model_dump(
        mode="json")
