"""Task 11.5 (R1 I-2, root's option b): synthetic data may feed real operations; what it produces is never selected or
registered, and simulated artifacts stay refused as inputs of every real operation.

``ModelDescriptor.eligible_for_real_use`` (neither synthetic nor simulated) gates selection and MLflow registration
only. ``ModelDescriptor.usable_in_real_operations`` (not simulated) gates every artifact a real operation takes as an
input: compilation, the model-input checks, merge conversion and the attempt runner's role derivation. Synthetic
taint propagates to every descendant (training, continuation, merge, evaluation records).

The chains run through the real ``OperationAttemptRunner`` against ``test_operation_runner``'s fake GPU host (SSH
boundary) and the fake MLflow client behind the real tracker. No GPU, SSH, network, download or MLflow write.
"""
import json
import socket
from contextlib import contextmanager
from datetime import datetime, timezone

import pytest
import test_desktop_projects as desk
import test_graph_controller as gct
import test_graph_readiness as readiness
import test_model_inputs as inputs
import test_tracking_outbox as outbox_tests
import test_workflow_planning as wpt
from test_operation_runner import gpu_host

from tuning_factory.attempt_runner import OperationAttemptRunner
from tuning_factory.executor_profiles import ResolvedDestination
from tuning_factory.model_artifacts import ModelArtifactRef, ModelArtifactStore, ModelInput, ModelLoadRef


@pytest.fixture(autouse=True)
def isolated(monkeypatch):
    """As ``test_graph_controller``'s fixture: no network, no real MLflow client, no OIDC refresher, none of the
    developer's MLflow settings, and only the outbox's receipt opens the tracking gate."""
    connects = []

    def refuse(*args, **kwargs):
        connects.append(args)
        raise AssertionError("synthetic chain tests must not open a network connection")

    monkeypatch.setattr(socket.socket, "connect", refuse)
    for key in outbox_tests.SECRET_VARIABLES:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    for key in ("MLFLOW_HTTP_REQUEST_MAX_RETRIES", "MLFLOW_HTTP_REQUEST_TIMEOUT"):
        monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("mlflow.MlflowClient", lambda *a, **k: pytest.fail("a real MLflow client was created"))
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    monkeypatch.setattr("tuning_factory.graph_store.GraphStore.test_acknowledgement", False)
    return connects


def chains(r):
    """R1's probe shapes in one real plan over synthetic, exportable data:

    - ``a`` trains on the synthetic data and ``ea`` evaluates it (train a -> evaluate e(a));
    - ``b`` continues ``a`` on *real* data (a -> b (continue a));
    - ``c`` is a second synthetic adapter; ``m`` merges ``a`` and ``c`` (two synthetic adapters);
    - ``s`` selects among ``ea``, ``eb``, ``ec`` and the merged candidate ``em``."""
    return [readiness.train(r, "a"), readiness.evaluate(r, "ea", "a"),
            readiness.train(r, "b", "a", binding="real"), readiness.train(r, "c"),
            readiness.merge("m", ("a", "c")),
            readiness.evaluate(r, "eb", "b"), readiness.evaluate(r, "ec", "c"), readiness.evaluate(r, "em", "m"),
            readiness.select("s", ("ea", "eb", "ec", "em"))]


def real_runner_world(tmp_path, monkeypatch, nodes):
    """``test_graph_controller.world`` driven by the real attempt runner against the fake GPU host."""
    w = gct.world(tmp_path, monkeypatch, nodes, synthetic=True, real_data=True)
    calls = []
    _, executor = gpu_host(tmp_path, monkeypatch, calls)

    class HostProvider(gct.FakeProvider):
        @contextmanager
        def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
            self.resolves.append(profile_id)
            _, facts = self.destinations[profile_id]
            observed = facts.model_copy(update={"observed_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc)})
            yield ResolvedDestination(profile=pinned, observation=observed, executor=executor)

    runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    controller, owner = gct.start(w, dispatcher=runner, provider=HostProvider(readiness.pods("pod1"), w.clock))
    gct.drive(w, controller, owner, ticks=120)
    return w, calls


def outputs(w, node_id):
    with w.store.jobs.connection() as db:
        row = db.execute("SELECT outputs FROM node_outputs WHERE run_id=? AND node_id=?", (w.run_id, node_id)).fetchone()
    return json.loads(row[0])


def descriptor(w, node_id):
    return w.store.artifacts.verify(ModelArtifactRef.model_validate(outputs(w, node_id)["model"]["model"]))


def test_synthetic_chains_complete_on_real_gpus_without_a_winner_or_registration(tmp_path, monkeypatch):
    w, calls = real_runner_world(tmp_path, monkeypatch, chains)
    errors = {node["node_id"]: node["error"] for node in gct.run_row(w)["nodes"] if node["error"]}
    assert gct.run_row(w)["status"] == "COMPLETED", errors
    assert set(gct.statuses(w).values()) == {"COMPLETED"}, (gct.statuses(w), errors)
    # One launch per node: three trainings, four evaluations and one merge, nothing retried.
    assert calls.count("train.bound.yaml") == 3 and calls.count("predict.yaml") == 4 and calls.count("merge") == 1
    # The taint reaches every descendant: a continuation on real data and a merge stay synthetic, never simulated.
    for node_id in ("a", "b", "c", "m"):
        found = descriptor(w, node_id)
        assert found.synthetic and not found.simulation, node_id
        assert found.usable_in_real_operations and not found.eligible_for_real_use, node_id
    manifest = w.store.jobs.get(gct.job_of(w, "b"))["manifest"]
    assert [item["synthetic"] for item in manifest["datasets"]] == [False]   # b's own training data is real
    assert descriptor(w, "b").parents == tuple(sorted(
        (w.refs.base, ModelArtifactRef.model_validate(outputs(w, "a")["model"]["model"])),
        key=lambda ref: (ref.artifact_id, ref.inventory_sha256)))
    for node_id in ("ea", "eb", "ec", "em"):
        assert outputs(w, node_id)["evaluation"]["evaluation"]["synthetic"] is True, node_id
    # Evaluations of synthetic models never make a winner, merged candidate included.
    receipt = outputs(w, "s")["selection"]
    assert receipt["status"] == "no_winner" and receipt["winner"] is None and receipt["eligible"] == []
    assert outputs(w, "s")["model"] is None and gct.status_of(w, "s")["selection"] == "no_winner"
    assert len(receipt["excluded"]) == 3
    for excluded in receipt["excluded"]:
        assert "synthetic" in excluded["reasons"] and "simulation" not in excluded["reasons"], excluded
    # Nothing synthetic is registered: real tracking evidence, never a model registration or a model version.
    kinds = {row["kind"] for row in gct.events(w)}
    assert "attempt_evidence" in kinds and "model_registration" not in kinds
    assert outbox_tests.count(w.fake, "create_model_version") == 0 and not w.fake.versions
    for node_id in ("a", "b", "c", "m"):
        assert gct.status_of(w, node_id)["registration_status"] == "none", node_id
        assert gct.status_of(w, node_id)["tracking_status"] == "saved", node_id


def test_a_real_continuation_of_a_real_model_on_real_data_is_still_registered(tmp_path, monkeypatch):
    """The other side of the split: real data and real parents still register (eligible_for_real_use)."""
    w = gct.world(tmp_path, monkeypatch, lambda r: [readiness.train(r, "a")])
    calls = []
    _, executor = gpu_host(tmp_path, monkeypatch, calls)

    class HostProvider(gct.FakeProvider):
        @contextmanager
        def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
            _, facts = self.destinations[profile_id]
            observed = facts.model_copy(update={"observed_at": datetime.fromtimestamp(self.clock(), tz=timezone.utc)})
            yield ResolvedDestination(profile=pinned, observation=observed, executor=executor)

    runner = OperationAttemptRunner(w.store, root=w.store.state_dir)
    controller, owner = gct.start(w, dispatcher=runner, provider=HostProvider(readiness.pods("pod1"), w.clock))
    gct.drive(w, controller, owner)
    found = descriptor(w, "a")
    assert not found.synthetic and found.eligible_for_real_use and found.usable_in_real_operations
    assert gct.status_of(w, "a")["registration_status"] == "registered"


# -- simulated artifacts stay refused as real inputs at every layer -----------------------------------------------

@pytest.mark.parametrize("flag", ["synthetic", "simulation"])
def test_the_model_input_checks_refuse_only_simulated_artifacts(tmp_path, flag):
    store = ModelArtifactStore(tmp_path / "store")
    directory, found = inputs.full_model(tmp_path, "flagged")
    ref = store.freeze(directory, found.model_copy(update={flag: True}))
    model_input = ModelInput(policy=ModelLoadRef(base=ref), initialization="full_weights")
    if flag == "simulation":
        with pytest.raises(ValueError, match="simulation artifact is ineligible for real use"):
            store.validate_input(model_input, require_real=True)
    else:
        assert store.validate_input(model_input, require_real=True) == model_input
    assert store.validate_input(model_input, require_real=False) == model_input


# -- planning and submission through the channel accept a real plan over synthetic data ----------------------------

def test_real_planning_and_submission_accept_a_chain_over_synthetic_exportable_data(tmp_path, monkeypatch):
    """No planning refusal or data check rejects synthetic, exportable data: only practice data and data that may not
    leave the PC are refused (``_data_checks``, ``_data_issues``)."""
    state = tmp_path / "state"
    rows = [(f"합성 질문 {i}", f"문맥 {i}", f"답 {i}") for i in range(1, 9)]
    held_rows = [(f"합성 검증 {i}", f"문맥 {i}", f"검증 답 {i}") for i in range(1, 9)]
    datasets = {}
    for dataset_id, values in (("synthetic-train", rows), ("synthetic-held", held_rows)):
        source_id = desk.import_source(state, tmp_path, desk.csv_bytes(values), f"{dataset_id}.csv")
        shown = desk.preview(state, source_id)
        registered = desk.register(state, source_id, shown["data"]["preview_sha256"], dataset_id, "1",
                                   export_allowed=True, synthetic=True)
        assert registered["status"] == "ok", registered
        datasets[dataset_id] = registered["data"]["dataset"]
    found = wpt.real_scenario(state, tmp_path, monkeypatch, dataset=datasets["synthetic-train"])
    spec = {"dataset": datasets["synthetic-held"], "evaluator_kind": "classification", "evaluator_version": "1",
            "ordered_sample_ids": ["row:0", "row:1"], "max_new_tokens": 32, "cutoff_len": 256, "seed": 7,
            "precision": "bf16", "quantization_bit": None, "tokenizer_fingerprint": found.root["tokenizer_fingerprint"],
            "template": "qwen", "semantic_model_kind": "causal_lm"}
    saved = wpt.run(state, "benchmark_save", {"spec": spec})
    assert saved["status"] == "ok", saved
    nodes = [wpt.train("train", found.recipe, wpt.from_root(found.root), ("pod-a",)),
             {"kind": "evaluate", "id": "eval", "label": "평가", "model": wpt.output("train"),
              "benchmark_revision": saved["data"]["revision_sha256"], "executor_profile_ids": ["pod-a"]}]
    project_id, _ = wpt.make_project(state, nodes, [wpt.binding("train", "train", found.dataset),
                                                    wpt.binding("val", "validation", datasets["synthetic-held"])])
    request = wpt.plan_request(project_id, simulation=False, estimates=(), resources=(("train", 1800), ("eval", 600)))
    planned = wpt.run(state, "graph_plan", request, settings=wpt.TOKEN_SETTINGS, profiles=[found.profile],
                      clock=wpt.NOW + 60)
    assert planned["status"] == "ok", planned
    assert planned["data"]["simulation"] is False
    submitted = wpt.run(state, "graph_submit", {"plan_sha256": planned["data"]["plan_sha256"], "request_id": "syn-1",
                                                "approve_execution": True, "approve_data_transfer": True},
                        settings=wpt.TOKEN_SETTINGS, profiles=[found.profile], clock=wpt.NOW + 120)
    assert submitted["status"] == "ok" and submitted["data"]["simulation"] is False, submitted
