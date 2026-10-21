import pytest
from pydantic import ValidationError

from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts
from tuning_factory.graph_planner import (
    ApprovalGrant,
    ExecutionChoices,
    NodeResources,
    compile_node,
    plan_graph,
    snapshot_dataset,
)
from tuning_factory.graph_schema import GraphSpec, TrainNode
from tuning_factory.model_artifacts import HFRootRef, ModelInput, ModelLoadRef
from tuning_factory.projects import DatasetBinding, DatasetRef, ProjectSpec, Revision
from tuning_factory.recipes import RecipeSpec
from tuning_factory.registry import Registry, canonical, digest


def fixture(tmp_path):
    registry = Registry(tmp_path / "registry")
    path = tmp_path / "data.json"
    path.write_bytes(canonical([{"instruction": "train", "output": "answer"}]))
    record = registry.register_dataset("data", "1", path)
    dataset = DatasetRef(dataset_id="data", version="1", sha256=record["sha256"])
    recipe = RecipeSpec(recipe_id="r", name="recipe")
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    root = HFRootRef(repo_id="Qwen/Qwen2.5-0.5B-Instruct", revision="a"*40,
                     config_sha256="b"*64, tokenizer_fingerprint="c"*64)
    graph = GraphSpec(nodes=[TrainNode(id="train.1", label="Train", recipe_revision=revision.revision_sha256,
        model_input=ModelInput(policy=ModelLoadRef(base=root), initialization="new_adapter"),
        dataset_binding="data", executor_profile_ids=("p",))], retry_policy={"max_attempts": 2})
    project = ProjectSpec(project_id="project.1", name="Project",
                          datasets=(DatasetBinding(binding_id="data", role="train", dataset=dataset),),
                          graph=graph.model_dump(mode="json"))
    project_revision = Revision(spec=project, revision_sha256=digest(canonical(project.model_dump(mode="json"))))
    executor = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local")
    from tuning_factory.graph_planner import ModelContext
    choices = ExecutionChoices(recipes=(revision,), datasets=(snapshot_dataset(registry, dataset),),
        models=(ModelContext(root=root),), executors=(executor,),
        node_resources=(NodeResources(node_id="train.1", timeout_seconds=1800),))
    facts = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=4,
                          total_hourly_cost_usd=8, observed_at="2026-10-18T00:00:00Z")
    return registry, project_revision, choices, facts


def test_plan_costs_include_attempts_and_bind_approval_to_profile_body(tmp_path):
    _, project, choices, facts = fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    assert plan.max_estimated_cost_usd == 8
    assert plan.simulation is True
    assert plan.nodes[0].max_estimated_cost_usd == 8
    grant = ApprovalGrant.for_plan(plan)
    grant.validate_plan(plan)
    changed = choices.model_copy(update={"executors": (choices.executors[0].model_copy(update={"pod_id": "pod2"}),)})
    second = plan_graph(project, changed, (facts.model_copy(update={"pod_id": "pod2"}),))
    with pytest.raises(ValueError, match="approval"):
        grant.validate_plan(second)


def test_compiler_requires_verified_bindings_and_derives_legacy_safe_id(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    from tuning_factory.graph_planner import VerifiedBindings
    from tuning_factory.model_artifacts import ModelArtifactStore
    bindings = VerifiedBindings(registry=registry, artifact_store=ModelArtifactStore(tmp_path / "models"),
                                destination_profile=choices.executors[0])
    operation = compile_node(plan, "train.1", bindings)
    assert "." not in operation.config.ai_id
    assert operation.config.resource_profile.hourly_cost_usd == 2
    assert operation.config.resource_profile.gpu_count == 4
    assert operation.model_input.policy.base == choices.models[0].root


def revise(project, graph=None, datasets=None):
    values = project.spec.model_dump(mode="json")
    if graph is not None:
        values["graph"] = graph
    if datasets is not None:
        values["datasets"] = datasets
    spec = ProjectSpec.model_validate(values)
    return Revision(spec=spec, revision_sha256=digest(canonical(spec.model_dump(mode="json"))))


def test_registered_cross_file_leakage_and_role_relabeling_fail(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    path = tmp_path / "held.json"
    path.write_bytes(canonical([{"instruction": " train ", "output": "different answer"}]))
    record = registry.register_dataset("held", "1", path)
    ref = DatasetRef(dataset_id="held", version="1", sha256=record["sha256"])
    held = DatasetBinding(binding_id="held", role="validation", dataset=ref)
    updated = revise(project, datasets=[*project.spec.datasets, held])
    choices = choices.model_copy(update={"datasets": (*choices.datasets, snapshot_dataset(registry, ref))})
    with pytest.raises(ValueError, match="input_overlap"):
        plan_graph(updated, choices, (facts,))
    from tuning_factory.graph_schema import validate_graph
    assert "input_overlap" in {issue.code for issue in validate_graph(updated.spec, choices.recipes, registry)}
    relabeled = DatasetBinding(binding_id="test", role="test", dataset=project.spec.datasets[0].dataset)
    updated = revise(project, datasets=[*project.spec.datasets, relabeled])
    with pytest.raises(ValueError, match="dataset_role"):
        plan_graph(updated, choices, (facts,))


def test_train_cannot_use_validation_or_test_binding(tmp_path):
    _, project, choices, facts = fixture(tmp_path)
    for role in ("validation", "test"):
        updated = revise(project, datasets=[project.spec.datasets[0].model_copy(update={"role": role})])
        with pytest.raises(ValueError, match="train dataset"):
            plan_graph(updated, choices, (facts,))


def test_plan_requires_hf_tokenizer_evidence_and_real_destination_revisions(tmp_path):
    _, project, choices, facts = fixture(tmp_path)
    with pytest.raises(ValueError, match="context"):
        plan_graph(project, choices.model_copy(update={"models": ()}), (facts,))
    with pytest.raises(ValueError, match="transport|tracking"):
        plan_graph(project, choices, (facts,), simulation=False)


def test_plan_cost_bound_includes_every_attempt(tmp_path):
    _, project, choices, facts = fixture(tmp_path)
    graph = dict(project.spec.graph, max_total_cost_usd=7.99)
    with pytest.raises(ValueError, match="total cost"):
        plan_graph(revise(project, graph), choices, (facts,))


def test_symbolic_inputs_are_conditional_and_cannot_compile_without_actual_outputs(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {"kind": "materialize", "id": "material", "label": "Materialize",
        "model": {"node_id": "train.1", "output_name": "model"}, "executor_profile_ids": ["p"]}]
    updated = revise(project, graph)
    choices = choices.model_copy(update={"node_resources": (*choices.node_resources,
        NodeResources(node_id="material", timeout_seconds=900))})
    plan = plan_graph(updated, choices, (facts,))
    assert plan.max_estimated_cost_usd == 12
    assert plan.nodes[1].inputs[0].conditional is True
    from tuning_factory.graph_planner import VerifiedBindings
    from tuning_factory.model_artifacts import ModelArtifactStore
    with pytest.raises(ValueError, match="not ready"):
        compile_node(plan, "material", VerifiedBindings(registry, ModelArtifactStore(tmp_path / "models"),
                                                        choices.executors[0]))


def test_sft_to_dpo_and_oft_handoffs_fail_before_launch(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {"kind": "materialize", "id": "material", "label": "Materialize",
        "model": {"node_id": "train.1", "output_name": "model"}, "executor_profile_ids": ["p"]}]
    recipe = RecipeSpec(recipe_id="oft", name="oft", training_method="oft")
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph["nodes"][0] = dict(graph["nodes"][0], recipe_revision=revision.revision_sha256)
    choices = choices.model_copy(update={"recipes": (revision,), "node_resources": (*choices.node_resources,
        NodeResources(node_id="material", timeout_seconds=900))})
    with pytest.raises(ValueError, match="OFT"):
        plan_graph(revise(project, graph), choices, (facts,))


def test_sample_ids_resolve_exact_rows_in_order_and_reject_arbitrary_labels(tmp_path):
    from tuning_factory.benchmarks import BenchmarkSpec
    from tuning_factory.graph_planner import validate_sample_ids
    _, _, choices, _ = fixture(tmp_path)
    snapshot = choices.datasets[0]
    data = dict(dataset=snapshot.dataset, evaluator_kind="classification", evaluator_version="1",
                ordered_sample_ids=("row:0",), max_new_tokens=32, cutoff_len=128, seed=42,
                precision="bf16", quantization_bit=None, tokenizer_fingerprint="c"*64,
                template="qwen", semantic_model_kind="causal_lm")
    assert validate_sample_ids(BenchmarkSpec(**data), snapshot) == (0,)
    for value in ("arbitrary", "row:01", "row:1", "row:-1"):
        with pytest.raises(ValueError, match="sample ID"):
            validate_sample_ids(BenchmarkSpec(**{**data, "ordered_sample_ids": (value,)}), snapshot)


def test_plan_is_read_only_and_tampered_plan_fails_compilation(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    before = {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    plan = plan_graph(project, choices, (facts,))
    assert before == {p.relative_to(tmp_path).as_posix(): p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    from tuning_factory.graph_planner import VerifiedBindings
    from tuning_factory.model_artifacts import ModelArtifactStore
    with pytest.raises(ValueError, match="plan hash"):
        compile_node(plan.model_copy(update={"simulation": False}), "train.1",
                     VerifiedBindings(registry, ModelArtifactStore(tmp_path / "models"), choices.executors[0]))


def evaluation_fixture(tmp_path, role="validation", **flags):
    """``flags`` (``synthetic``/``simulation``) mark the evaluated artifact's descriptor."""
    from test_model_artifacts import fixture_model, updated

    from tuning_factory.benchmarks import BenchmarkSpec
    from tuning_factory.graph_planner import (
        BenchmarkRevision,
        ModelContext,
        ProducerEvidence,
        snapshot_artifact,
    )
    from tuning_factory.model_artifacts import ModelArtifactStore
    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path)
    descriptor = updated(descriptor, **flags) if flags else descriptor
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id,
        attempt_id=descriptor.producer_attempt_id, manifest_sha256=descriptor.producer_manifest_sha256,
        runtime_profile="primary", training_stage="sft", status="completed", tracking_acknowledged=True)
    path = tmp_path / "held.json"
    path.write_bytes(canonical([{"instruction": "held-out", "output": "a"}, {"instruction": "second", "output": "b"}]))
    registered = registry.register_dataset("held", "1", path)
    data = DatasetRef(dataset_id="held", version="1", sha256=registered["sha256"])
    benchmark = BenchmarkSpec(dataset=data, evaluator_kind="classification", evaluator_version="1",
        ordered_sample_ids=("row:1", "row:0"), max_new_tokens=32, cutoff_len=128, seed=42,
        precision="bf16", quantization_bit=None, tokenizer_fingerprint=descriptor.tokenizer_fingerprint,
        template="qwen", semantic_model_kind="causal_lm")
    graph = {"nodes": [{"kind": "evaluate", "id": "eval", "label": "Evaluate", "model": ref.model_dump(),
        "benchmark_revision": benchmark.fingerprint(), "executor_profile_ids": ["p"]},
        {"kind": "select", "id": "select", "label": "Select", "evaluations": [
            {"node_id": "eval", "output_name": "evaluation"}], "selection": {"metric": "accuracy", "direction": "max"}}]}
    project = revise(project, graph, [DatasetBinding(binding_id="held", role=role, dataset=data)])
    choices = ExecutionChoices(benchmarks=(BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        datasets=(snapshot_dataset(registry, data),), models=(ModelContext(root=descriptor.root),),
        artifacts=(snapshot_artifact(store, ref, producer),), executors=choices.executors,
        node_resources=(NodeResources(node_id="eval", timeout_seconds=900),))
    return registry, project, choices, facts, store, ref, producer, benchmark


def test_evaluation_cost_sample_order_and_ready_metadata_checks(tmp_path):
    from tuning_factory.graph_planner import VerifiedBindings, validate_sample_ids
    registry, project, choices, facts, store, ref, producer, benchmark = evaluation_fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    assert plan.max_estimated_cost_usd == 2
    assert validate_sample_ids(benchmark, choices.datasets[0]) == (1, 0)
    operation = compile_node(plan, "eval", VerifiedBindings(registry, store, choices.executors[0], producers=(producer,)))
    assert operation.model == ref
    assert operation.benchmark.ordered_sample_ids == ("row:1", "row:0")
    with pytest.raises(ValueError, match="producer"):
        compile_node(plan, "eval", VerifiedBindings(registry, store, choices.executors[0]))
    with pytest.raises(ValueError, match="tracking"):
        compile_node(plan, "eval", VerifiedBindings(registry, store, choices.executors[0],
            producers=(producer.model_copy(update={"tracking_acknowledged": False}),)))
    with pytest.raises(ValueError, match="runtime|PPO"):
        compile_node(plan, "eval", VerifiedBindings(registry, store, choices.executors[0],
            producers=(producer.model_copy(update={"runtime_profile": "ppo"}),)))
    with pytest.raises(ValueError, match="completed"):
        compile_node(plan, "eval", VerifiedBindings(registry, store, choices.executors[0],
            producers=(producer.model_copy(update={"status": "failed"}),)))


REAL_TRACKING = {"profile_id": "mlflow", "revision": "1", "public_settings_sha256": "2" * 64}


def real_choices(choices):
    """The execution choices of a real plan: a frozen transport revision and a tracking destination."""
    from tuning_factory.graph_planner import TrackingDestination
    return choices.model_copy(update={
        "executors": tuple(item.model_copy(update={"transport_settings_sha256": "1" * 64}) for item in choices.executors),
        "tracking_destination": TrackingDestination.model_validate(REAL_TRACKING)})


@pytest.mark.parametrize("flag", ["synthetic", "simulation"])
def test_real_compilation_refuses_only_simulated_input_models(tmp_path, flag):
    """Task 11.5 (R1 I-2, option b): a synthetic model is a valid input of a real evaluation (its record is synthetic,
    so it never wins a selection); a simulated model never runs for real. A simulation plan accepts both."""
    from tuning_factory.graph_planner import TrackingDestination, VerifiedBindings
    registry, project, choices, facts, store, ref, producer, _ = evaluation_fixture(tmp_path, **{flag: True})
    choices = real_choices(choices)
    plan = plan_graph(project, choices, (facts,), simulation=False)
    bindings = VerifiedBindings(registry, store, choices.executors[0], producers=(producer,),
                                tracking_destination=TrackingDestination.model_validate(REAL_TRACKING))
    if flag == "simulation":
        with pytest.raises(ValueError, match="simulation model is ineligible for real execution"):
            compile_node(plan, "eval", bindings)
    else:
        assert compile_node(plan, "eval", bindings).model == ref
    simulated = plan_graph(project, choices, (facts,))
    assert compile_node(simulated, "eval", VerifiedBindings(registry, store, choices.executors[0],
                                                            producers=(producer,))).model == ref


def test_heldout_test_cannot_influence_selection(tmp_path):
    _, project, choices, facts, *_ = evaluation_fixture(tmp_path, role="test")
    with pytest.raises(ValueError, match="test evaluation"):
        plan_graph(project, choices, (facts,))


def test_selection_compilation_rejects_forged_evaluation_attempt(tmp_path):
    from tuning_factory.benchmarks import EvaluationRecord
    from tuning_factory.graph_planner import NodeEvaluationBinding, VerifiedBindings
    registry, project, choices, facts, store, ref, producer, benchmark = evaluation_fixture(tmp_path)
    plan = plan_graph(project, choices, (facts,))
    record = EvaluationRecord(model=ref, benchmark_sha256=benchmark.fingerprint(), metrics={"accuracy": .8},
        status="completed", synthetic=False, simulation=False, producer_attempt_id="invented")
    with pytest.raises(ValueError, match="evaluation.*producer|evaluation.*attempt"):
        compile_node(plan, "select", VerifiedBindings(registry, store, producers=(producer,), evaluations=(
            NodeEvaluationBinding(node_id="eval", evaluation=record, tracking_acknowledged=True),)))


def test_materialize_full_model_is_rejected_at_planning(tmp_path):
    _, project, choices, facts, _, ref, *_ = evaluation_fixture(tmp_path)
    graph = {"nodes": [{"kind": "materialize", "id": "eval", "label": "Materialize", "model": ref.model_dump(),
                        "executor_profile_ids": ["p"]}]}
    with pytest.raises(ValueError, match="materialize|adapter"):
        plan_graph(revise(project, graph), choices, (facts,))


def test_selected_output_keeps_original_artifact_producer(tmp_path):
    from tuning_factory.benchmarks import EvaluationRecord, select_best
    from tuning_factory.graph_planner import (
        NodeEvaluationBinding,
        NodeExecutionEvidence,
        NodeModelBinding,
        VerifiedBindings,
    )
    registry, project, choices, facts, store, ref, producer, benchmark = evaluation_fixture(tmp_path)
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {"kind": "evaluate", "id": "after", "label": "After select",
        "model": {"node_id": "select", "output_name": "model"}, "benchmark_revision": benchmark.fingerprint(),
        "executor_profile_ids": ["p"]}]
    choices = choices.model_copy(update={"node_resources": (*choices.node_resources,
        NodeResources(node_id="after", timeout_seconds=900))})
    plan = plan_graph(revise(project, graph), choices, (facts,))
    record = EvaluationRecord(model=ref, benchmark_sha256=benchmark.fingerprint(), metrics={"accuracy": .8},
        status="completed", synthetic=False, simulation=False, producer_attempt_id="eval-attempt")
    selection = select_best((record,), GraphSpec.model_validate(graph).nodes[1].selection)
    evidence = NodeExecutionEvidence(node_id="eval", plan_sha256=plan.sha256, job_id="eval-job",
        attempt_id="eval-attempt", manifest_sha256="d"*64, status="completed", tracking_acknowledged=True)
    binding = VerifiedBindings(registry, store, choices.executors[0], producers=(producer,),
        outputs=(NodeModelBinding(node_id="select", model=ref, selection=selection),),
        evaluations=(NodeEvaluationBinding(node_id="eval", evaluation=record, tracking_acknowledged=True, producer=evidence),))
    operation = compile_node(plan, "after", binding)
    assert operation.model == ref


def test_dpo_transition_requires_materialized_parent_and_exact_reference(tmp_path):
    registry, project, choices, facts = fixture(tmp_path)
    path = tmp_path / "preference.json"
    path.write_bytes(canonical([{"instruction": "prefer", "chosen": "yes", "rejected": "no"}]))
    record = registry.register_dataset("pref", "1", path, kind="preference")
    dataset = DatasetRef(dataset_id="pref", version="1", sha256=record["sha256"])
    recipe = RecipeSpec(recipe_id="dpo", name="DPO", training_stage="dpo", input_kind="preference")
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {"kind": "train", "id": "dpo", "label": "DPO",
        "recipe_revision": revision.revision_sha256, "model_input": {"node_id": "train.1", "output_name": "model"},
        "dataset_binding": "pref", "executor_profile_ids": ["p"]}]
    project = revise(project, graph, [*project.spec.datasets, DatasetBinding(binding_id="pref", role="train", dataset=dataset)])
    choices = choices.model_copy(update={"recipes": (*choices.recipes, revision),
        "datasets": (*choices.datasets, snapshot_dataset(registry, dataset)),
        "node_resources": (*choices.node_resources, NodeResources(node_id="dpo", timeout_seconds=900))})
    with pytest.raises(ValueError, match="materialize"):
        plan_graph(project, choices, (facts,))
    from tuning_factory.graph_schema import validate_graph
    assert any("materialize" in issue.message for issue in validate_graph(project.spec, choices.recipes, registry))
    graph["nodes"].insert(1, {"kind": "materialize", "id": "full", "label": "Materialize",
        "model": {"node_id": "train.1", "output_name": "model"}, "executor_profile_ids": ["p"]})
    graph["nodes"][-1]["model_input"]["node_id"] = "full"
    choices = choices.model_copy(update={"node_resources": (*choices.node_resources,
        NodeResources(node_id="full", timeout_seconds=900))})
    assert plan_graph(revise(project, graph), choices, (facts,)).nodes[-1].inputs[0].conditional


def test_lora_sft_materialized_into_dpo_binds_policy_and_reference_to_the_materialized_model(tmp_path):
    """Task 11.5 (R5 M6): LoRA SFT -> materialize -> DPO compiles the DPO node with the policy base, the frozen
    reference base and the materialized artifact all the same full model (a new adapter, no continuation)."""
    from test_model_artifacts import fixture_model, updated

    from tuning_factory.graph_planner import (
        ModelContext,
        NodeModelBinding,
        ProducerEvidence,
        VerifiedBindings,
    )
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts = fixture(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    adapter_directory, adapter = adapter_fixture(tmp_path)
    adapter_ref = store.freeze(adapter_directory, adapter)
    full_directory, full = fixture_model(tmp_path, marker="materialized")
    full = updated(full, producer_job_id="materialized", producer_attempt_id="materialized", parents=(adapter_ref,))
    full_ref = store.freeze(full_directory, full)
    root = full.root
    path = tmp_path / "preference.json"
    path.write_bytes(canonical([{"instruction": "prefer", "chosen": "yes", "rejected": "no"}]))
    record = registry.register_dataset("pref", "1", path, kind="preference")
    dataset = DatasetRef(dataset_id="pref", version="1", sha256=record["sha256"])
    dpo = RecipeSpec(recipe_id="dpo", name="DPO", training_stage="dpo", input_kind="preference")
    revision = Revision(spec=dpo, revision_sha256=digest(canonical(dpo.model_dump(mode="json"))))
    graph = {"nodes": [
        {**project.spec.graph["nodes"][0], "model_input": ModelInput(
            policy=ModelLoadRef(base=root), initialization="new_adapter").model_dump(mode="json")},
        {"kind": "materialize", "id": "full", "label": "Materialize",
         "model": {"node_id": "train.1", "output_name": "model"}, "executor_profile_ids": ["p"]},
        {"kind": "train", "id": "dpo", "label": "DPO", "recipe_revision": revision.revision_sha256,
         "model_input": {"node_id": "full", "output_name": "model"}, "dataset_binding": "pref",
         "executor_profile_ids": ["p"]}]}
    project = revise(project, graph, [*project.spec.datasets,
                                      DatasetBinding(binding_id="pref", role="train", dataset=dataset)])
    profile = ModelProfile(repo_id=root.repo_id, revision=root.revision, config_sha256=root.config_sha256,
                           architecture="qwen2", template="qwen")
    choices = choices.model_copy(update={
        "recipes": (*choices.recipes, revision), "models": (ModelContext(root=root, profile=profile),),
        "datasets": (*choices.datasets, snapshot_dataset(registry, dataset)),
        "node_resources": (*choices.node_resources, NodeResources(node_id="full", timeout_seconds=900),
                           NodeResources(node_id="dpo", timeout_seconds=900))})
    plan = plan_graph(project, choices, (facts,))

    def produced(ref, descriptor, node_id, stage):
        return ProducerEvidence(model=ref, job_id=descriptor.producer_job_id,
                                attempt_id=descriptor.producer_attempt_id,
                                manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary",
                                training_stage=stage, status="completed", tracking_acknowledged=True,
                                plan_sha256=plan.sha256, node_id=node_id)

    operation = compile_node(plan, "dpo", VerifiedBindings(
        registry, store, choices.executors[0],
        outputs=(NodeModelBinding(node_id="train.1", model=adapter_ref), NodeModelBinding(node_id="full", model=full_ref)),
        producers=(produced(adapter_ref, adapter, "train.1", "sft"), produced(full_ref, full, "full", None))))
    model_input = operation.model_input
    assert model_input.initialization == "new_adapter" and model_input.policy == ModelLoadRef(base=full_ref)
    assert model_input.frozen_reference == model_input.policy
    assert model_input.policy.base == model_input.frozen_reference.base == full_ref
    assert operation.config.training_stage == "dpo"


def test_external_full_model_materialization_and_ppo_routes_do_not_hide_behind_select(tmp_path):
    _, project, choices, facts, *_ = evaluation_fixture(tmp_path)
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {"kind": "evaluate", "id": "legacy", "label": "Legacy",
        "model": {"node_id": "select", "output_name": "model"},
        "benchmark_revision": choices.benchmarks[0].revision_sha256, "executor_profile_ids": ["p"]}]
    choices = choices.model_copy(update={"node_resources": (*choices.node_resources,
        NodeResources(node_id="legacy", timeout_seconds=900, runtime_profile="ppo"))})
    with pytest.raises(ValueError, match="PPO|runtime"):
        plan_graph(revise(project, graph), choices, (facts,))


def test_ready_symbolic_output_rejects_wrong_parent_metadata(tmp_path):
    from test_model_artifacts import fixture_model

    from tuning_factory.graph_planner import (
        ModelContext,
        NodeModelBinding,
        ProducerEvidence,
        VerifiedBindings,
    )
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    graph = dict(project.spec.graph)
    graph["nodes"][0] = dict(graph["nodes"][0], model_input=ModelInput(
        policy=ModelLoadRef(base=descriptor.root), initialization="new_adapter").model_dump(mode="json"))
    graph["nodes"] = [*graph["nodes"], {"kind": "materialize", "id": "full", "label": "Full",
        "model": {"node_id": "train.1", "output_name": "model"}, "executor_profile_ids": ["p"]}]
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.config_sha256, architecture="qwen2", template="qwen")
    choices = choices.model_copy(update={"models": (ModelContext(root=descriptor.root, profile=profile),),
        "node_resources": (*choices.node_resources, NodeResources(node_id="full", timeout_seconds=900))})
    plan = plan_graph(revise(project, graph), choices, (facts,))
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary", training_stage="sft",
        status="completed", tracking_acknowledged=True, plan_sha256=plan.sha256, node_id="train.1")
    with pytest.raises(ValueError, match="adapter|contract"):
        compile_node(plan, "full", VerifiedBindings(registry, store, choices.executors[0],
            outputs=(NodeModelBinding(node_id="train.1", model=ref),), producers=(producer,)))


def adapter_fixture(tmp_path, marker="adapter"):
    import json
    import struct

    from test_model_artifacts import fixture_model

    from tuning_factory.model_artifacts import ModelDescriptor, TensorMetadata
    directory, descriptor = fixture_model(tmp_path, adapter=True, marker=marker)
    config = json.loads((directory / "adapter_config.json").read_text())
    config["target_modules"] = ["q_proj", "v_proj"]
    (directory / "adapter_config.json").write_bytes(canonical(config))
    names = [f"model.{module}.lora_{letter}.weight" for module in ("q_proj", "v_proj") for letter in ("A", "B")]
    header = canonical({name: {"dtype": "F32", "shape": [2, 2], "data_offsets": [i*16, (i+1)*16]}
                        for i, name in enumerate(names)})
    (directory / "adapter_model.safetensors").write_bytes(struct.pack("<Q", len(header)) + header + bytes(64))
    values = descriptor.model_dump()
    values.update(producer_job_id=marker, producer_attempt_id=marker,
        adapter={**values["adapter"], "target_modules": ["q_proj", "v_proj"]},
        tensors=[TensorMetadata(file="adapter_model.safetensors", name=name, shape=(2, 2), dtype="F32") for name in names])
    return directory, ModelDescriptor.model_validate(values)


def test_ready_continuation_checks_actual_settings_targets_and_dimensions(tmp_path):
    from tuning_factory.graph_planner import (
        ModelContext,
        ModelTargetEvidence,
        ProducerEvidence,
        TargetModuleEvidence,
        VerifiedBindings,
        snapshot_artifact,
    )
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = adapter_fixture(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary", training_stage="sft",
        status="completed", tracking_acknowledged=True)
    recipe = RecipeSpec(recipe_id="continue", name="continue", hyperparameters={"lora_rank": 2, "lora_target": "all"})
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph = dict(project.spec.graph)
    graph["nodes"] = [{**graph["nodes"][0], "recipe_revision": revision.revision_sha256,
        "model_input": ModelInput(policy=ModelLoadRef(base=descriptor.root, adapter=ref),
                                  initialization="continue_adapter").model_dump(mode="json")}]
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.config_sha256, architecture="qwen2", template="qwen")
    evidence = ModelTargetEvidence(base=descriptor.root, modules=tuple(TargetModuleEvidence(
        name="model." + module, input_dimension=2, output_dimension=2) for module in ("q_proj", "v_proj")))
    choices = choices.model_copy(update={"recipes": (revision,), "models": (ModelContext(root=descriptor.root, profile=profile),),
        "artifacts": (snapshot_artifact(store, ref, producer),), "base_targets": (evidence,)})
    plan = plan_graph(revise(project, graph), choices, (facts,))
    with pytest.raises(ValueError, match="target/dimension"):
        compile_node(plan, "train.1", VerifiedBindings(registry, store, choices.executors[0], producers=(producer,)))
    operation = compile_node(plan, "train.1", VerifiedBindings(registry, store, choices.executors[0],
        producers=(producer,), base_targets=(evidence,)))
    assert operation.model_input.policy.adapter == ref
    bad = evidence.model_copy(update={"modules": (evidence.modules[0].model_copy(update={"input_dimension": 3}), evidence.modules[1])})
    with pytest.raises(ValueError, match="dimensions"):
        compile_node(plan, "train.1", VerifiedBindings(registry, store, choices.executors[0],
            producers=(producer,), base_targets=(bad,)))


def test_merge_and_materialize_compile_verified_actual_adapters(tmp_path):
    from tuning_factory.graph_planner import (
        ModelTargetEvidence,
        ProducerEvidence,
        TargetModuleEvidence,
        VerifiedBindings,
        snapshot_artifact,
    )
    from tuning_factory.model_artifacts import ModelArtifactStore
    registry, project, choices, facts = fixture(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    refs, producers, artifacts = [], [], []
    for marker in ("first", "second"):
        directory, descriptor = adapter_fixture(tmp_path, marker)
        ref = store.freeze(directory, descriptor)
        producer = ProducerEvidence(model=ref, job_id=marker, attempt_id=marker,
            manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary", training_stage="sft",
            status="completed", tracking_acknowledged=True)
        refs.append(ref)
        producers.append(producer)
        artifacts.append(snapshot_artifact(store, ref, producer))
    graph = {"nodes": [{"kind": "merge", "id": "merge", "label": "Merge", "models": [ref.model_dump() for ref in refs],
        "weights": [.5, .5], "executor_profile_ids": ["p"]}, {"kind": "materialize", "id": "full", "label": "Full",
        "model": refs[0].model_dump(), "executor_profile_ids": ["p"]}]}
    choices = choices.model_copy(update={"artifacts": tuple(artifacts), "node_resources": (
        NodeResources(node_id="merge", timeout_seconds=900), NodeResources(node_id="full", timeout_seconds=900))})
    plan = plan_graph(revise(project, graph), choices, (facts,))
    assert plan.max_estimated_cost_usd == 4
    bindings = VerifiedBindings(registry, store, choices.executors[0], producers=tuple(producers))
    with pytest.raises(ValueError, match="target/dimension"):
        compile_node(plan, "merge", bindings)
    evidence = ModelTargetEvidence(base=descriptor.root, modules=tuple(TargetModuleEvidence(
        name="model." + module, input_dimension=2, output_dimension=2) for module in ("q_proj", "v_proj")))
    bindings = VerifiedBindings(registry, store, choices.executors[0], producers=tuple(producers), base_targets=(evidence,))
    assert compile_node(plan, "merge", bindings).models == tuple(refs)
    assert compile_node(plan, "full", bindings).model == refs[0]


def test_ppo_uses_typed_reward_with_legacy_runtime_and_never_serializes_path(tmp_path):
    from test_model_artifacts import fixture_model, with_value_head

    from tuning_factory.graph_planner import (
        ModelContext,
        ProducerEvidence,
        VerifiedBindings,
        snapshot_artifact,
    )
    from tuning_factory.manifest import resolve_operation
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path, marker="reward")
    descriptor = with_value_head(directory, descriptor)
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="ppo", training_stage="rm",
        status="completed", tracking_acknowledged=True)
    path = tmp_path / "prompt.json"
    path.write_bytes(canonical([{"instruction": "question"}]))
    record = registry.register_dataset("prompt", "1", path, kind="prompt")
    dataset = DatasetRef(dataset_id="prompt", version="1", sha256=record["sha256"])
    recipe = RecipeSpec(recipe_id="ppo", name="PPO", training_stage="ppo", input_kind="prompt")
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph = {"nodes": [{"kind": "train", "id": "ppo", "label": "PPO", "recipe_revision": revision.revision_sha256,
        "model_input": ModelInput(policy=ModelLoadRef(base=descriptor.root), initialization="new_adapter").model_dump(mode="json"),
        "reward_binding": ModelLoadRef(base=ref).model_dump(mode="json"), "dataset_binding": "prompt", "executor_profile_ids": ["p"]}]}
    project = revise(project, graph, [DatasetBinding(binding_id="prompt", role="train", dataset=dataset)])
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.config_sha256, architecture="qwen2", template="qwen")
    choices = choices.model_copy(update={"recipes": (revision,), "models": (ModelContext(root=descriptor.root, profile=profile),),
        "datasets": (snapshot_dataset(registry, dataset),), "artifacts": (snapshot_artifact(store, ref, producer),),
        "node_resources": (NodeResources(node_id="ppo", timeout_seconds=900, runtime_profile="ppo"),)})
    plan = plan_graph(project, choices, (facts,))
    operation = compile_node(plan, "ppo", VerifiedBindings(registry, store, choices.executors[0], producers=(producer,)))
    manifest = resolve_operation(operation, registry)
    assert operation.model_input.reward_model.base == ref
    assert operation.resources.runtime.profile == "ppo"
    assert manifest["operation"]["config"]["stage_options"]["reward_model"] is None
    assert str(tmp_path) not in str(manifest)
    wrong = snapshot_artifact(store, ref, producer.model_copy(update={"runtime_profile": "primary"}))
    with pytest.raises(ValueError, match="PPO"):
        plan_graph(project, choices.model_copy(update={"artifacts": (wrong,)}), (facts,))


def test_ready_dpo_reference_is_exact_full_parent_and_orpo_is_reference_free(tmp_path):
    from tuning_factory.graph_planner import ModelContext, VerifiedBindings
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts, store, ref, producer, _ = evaluation_fixture(tmp_path)
    descriptor = store.verify(ref)
    path = tmp_path / "pref.json"
    path.write_bytes(canonical([{"instruction": "preference", "chosen": "yes", "rejected": "no"}]))
    record = registry.register_dataset("pref", "1", path, kind="preference")
    dataset = DatasetRef(dataset_id="pref", version="1", sha256=record["sha256"])
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.config_sha256, architecture="qwen2", template="qwen")
    for loss in ("sigmoid", "orpo", "simpo"):
        recipe = RecipeSpec(recipe_id="dpo", name="DPO", training_stage="dpo", input_kind="preference", stage_options={"pref_loss": loss})
        revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
        graph = {"nodes": [{"kind": "train", "id": "dpo", "label": "DPO", "recipe_revision": revision.revision_sha256,
            "model_input": ModelInput(policy=ModelLoadRef(base=ref), initialization="new_adapter").model_dump(mode="json"),
            "dataset_binding": "pref", "executor_profile_ids": ["p"]}]}
        current = revise(project, graph, [DatasetBinding(binding_id="pref", role="train", dataset=dataset)])
        selected = choices.model_copy(update={"recipes": (revision,), "benchmarks": (),
            "models": (ModelContext(root=descriptor.root, profile=profile),), "datasets": (snapshot_dataset(registry, dataset),),
            "node_resources": (NodeResources(node_id="dpo", timeout_seconds=900),)})
        operation = compile_node(plan_graph(current, selected, (facts,)), "dpo", VerifiedBindings(
            registry, store, choices.executors[0], producers=(producer,)))
        assert operation.model_input.policy.base == ref
        assert operation.model_input.frozen_reference == (ModelLoadRef(base=ref) if loss == "sigmoid" else None)


def test_materialize_rejects_adapter_rank_inconsistent_with_actual_tensors(tmp_path):
    import json

    from tuning_factory.graph_planner import ProducerEvidence, snapshot_artifact
    from tuning_factory.model_artifacts import ModelArtifactStore, ModelDescriptor
    _, project, choices, facts = fixture(tmp_path)
    directory, descriptor = adapter_fixture(tmp_path)
    config = json.loads((directory / "adapter_config.json").read_text())
    config["r"] = 3
    (directory / "adapter_config.json").write_bytes(canonical(config))
    values = descriptor.model_dump()
    values["adapter"]["rank"] = 3
    descriptor = ModelDescriptor.model_validate(values)
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary", training_stage="sft",
        status="completed", tracking_acknowledged=True)
    graph = {"nodes": [{"kind": "materialize", "id": "full", "label": "Full", "model": ref.model_dump(),
                        "executor_profile_ids": ["p"]}]}
    choices = choices.model_copy(update={"artifacts": (snapshot_artifact(store, ref, producer),),
        "node_resources": (NodeResources(node_id="full", timeout_seconds=900),)})
    with pytest.raises(ValueError, match="rank|tensor"):
        plan_graph(revise(project, graph), choices, (facts,))


def test_catalog_root_uses_its_reviewed_template(tmp_path):
    from tuning_factory.graph_planner import ModelContext, VerifiedBindings
    from tuning_factory.model_artifacts import ModelArtifactStore
    registry, project, choices, facts = fixture(tmp_path)
    root = choices.models[0].root.model_copy(update={"repo_id": "meta-llama/Llama-3.2-1B-Instruct"})
    graph = dict(project.spec.graph)
    graph["nodes"][0]["model_input"]["policy"]["base"] = root.model_dump(mode="json")
    choices = choices.model_copy(update={"models": (ModelContext(root=root),)})
    operation = compile_node(plan_graph(revise(project, graph), choices, (facts,)), "train.1", VerifiedBindings(
        registry, ModelArtifactStore(tmp_path / "models"), choices.executors[0]))
    assert operation.config.template == "llama3"


@pytest.mark.parametrize("parent_method", ["full", "freeze"])
@pytest.mark.parametrize("child_stage,kind,row", [
    ("sft", "instruction", {"instruction": "child", "output": "answer"}),
    ("dpo", "preference", {"instruction": "child", "chosen": "yes", "rejected": "no"}),
    ("kto", "kto", {"instruction": "child", "output": "answer", "kto_tag": True}),
])
def test_full_weight_symbolic_parent_initializes_new_adapter_without_materialize(
        tmp_path, parent_method, child_stage, kind, row):
    from test_model_artifacts import fixture_model

    from tuning_factory.graph_planner import (
        ModelContext,
        NodeModelBinding,
        ProducerEvidence,
        VerifiedBindings,
    )
    from tuning_factory.graph_schema import validate_graph
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile

    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    ref = store.freeze(directory, descriptor)
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.root.config_sha256, architecture="qwen2", template="qwen")
    recipes = tuple(Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
        for recipe in (RecipeSpec(recipe_id="parent", name="Parent", training_method=parent_method),
                       RecipeSpec(recipe_id="child", name="Child", training_stage=child_stage, input_kind=kind)))
    path = tmp_path / "child.json"
    path.write_bytes(canonical([row]))
    record = registry.register_dataset("child", "1", path, kind=kind)
    data = DatasetRef(dataset_id="child", version="1", sha256=record["sha256"])
    graph = {"nodes": [{"kind": "train", "id": "parent", "label": "Parent",
        "recipe_revision": recipes[0].revision_sha256, "dataset_binding": "data", "executor_profile_ids": ["p"],
        "model_input": ModelInput(policy=ModelLoadRef(base=descriptor.root), initialization="full_weights").model_dump(mode="json")},
        {"kind": "train", "id": "child", "label": "Child", "recipe_revision": recipes[1].revision_sha256,
         "dataset_binding": "child", "executor_profile_ids": ["p"],
         "model_input": {"node_id": "parent", "output_name": "model"}}]}
    project = revise(project, graph, [*project.spec.datasets, DatasetBinding(binding_id="child", role="train", dataset=data)])
    choices = choices.model_copy(update={"recipes": recipes, "models": (ModelContext(root=descriptor.root, profile=profile),),
        "datasets": (*choices.datasets, snapshot_dataset(registry, data)), "node_resources": (
            NodeResources(node_id="parent", timeout_seconds=900), NodeResources(node_id="child", timeout_seconds=900))})
    assert validate_graph(project.spec, choices.recipes, registry) == []
    plan = plan_graph(project, choices, (facts,))
    producer = ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary", training_stage="sft",
        status="completed", tracking_acknowledged=True, plan_sha256=plan.sha256, node_id="parent")
    operation = compile_node(plan, "child", VerifiedBindings(registry, store, choices.executors[0],
        outputs=(NodeModelBinding(node_id="parent", model=ref),), producers=(producer,)))
    assert operation.model_input.initialization == "new_adapter"
    assert operation.model_input.policy == ModelLoadRef(base=ref)
    assert operation.model_input.frozen_reference == (ModelLoadRef(base=ref) if child_stage in {"dpo", "kto"} else None)


def selected_roots_fixture(tmp_path):
    from test_model_artifacts import fixture_model

    from tuning_factory.graph_planner import ModelContext, snapshot_artifact
    from tuning_factory.model_artifacts import ModelDescriptor
    from tuning_factory.model_hub import ModelProfile

    registry, project, choices, facts, store, first, producer, benchmark = evaluation_fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path, marker="second")
    second_root = descriptor.root.model_copy(update={"revision": "b" * 40})
    descriptor = ModelDescriptor.model_validate({**descriptor.model_dump(), "root": second_root,
        "effective_base": second_root, "producer_job_id": "second-job", "producer_attempt_id": "second-attempt"})
    second = store.freeze(directory, descriptor)
    second_producer = producer.model_copy(update={"model": second, "job_id": "second-job", "attempt_id": "second-attempt"})
    model_contexts = tuple(ModelContext(root=root, profile=ModelProfile(repo_id=root.repo_id, revision=root.revision,
        config_sha256=root.config_sha256, architecture="qwen2", template="qwen"))
        for root in (choices.models[0].root, second_root))
    record = registry.get_dataset("data", "1")
    data = DatasetRef(dataset_id="data", version="1", sha256=record["sha256"])
    recipe = RecipeSpec(recipe_id="child", name="Child")
    recipe_revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph = dict(project.spec.graph)
    graph["nodes"] = [graph["nodes"][0], {**graph["nodes"][0], "id": "second-eval", "model": second.model_dump()},
        {**graph["nodes"][1], "evaluations": [{"node_id": node, "output_name": "evaluation"}
                                               for node in ("eval", "second-eval")]},
        {"kind": "train", "id": "child", "label": "Child", "recipe_revision": recipe_revision.revision_sha256,
         "dataset_binding": "data", "executor_profile_ids": ["p"],
         "model_input": {"node_id": "select", "output_name": "model"}}]
    project = revise(project, graph, [*project.spec.datasets, DatasetBinding(binding_id="data", role="train", dataset=data)])
    choices = choices.model_copy(update={"recipes": (recipe_revision,), "models": model_contexts,
        "datasets": (*choices.datasets, snapshot_dataset(registry, data)),
        "artifacts": (*choices.artifacts, snapshot_artifact(store, second, second_producer)),
        "node_resources": (*choices.node_resources, NodeResources(node_id="second-eval", timeout_seconds=900),
                           NodeResources(node_id="child", timeout_seconds=900))})
    return registry, project, choices, facts, store, (first, second), (producer, second_producer), benchmark


@pytest.mark.parametrize("reverse_candidates", [False, True])
def test_second_selected_root_binds_child_to_actual_winner(tmp_path, reverse_candidates):
    from tuning_factory.benchmarks import EvaluationRecord, select_best
    from tuning_factory.graph_planner import (
        NodeEvaluationBinding,
        NodeExecutionEvidence,
        NodeModelBinding,
        VerifiedBindings,
    )

    registry, project, choices, facts, store, refs, producers, benchmark = selected_roots_fixture(tmp_path)
    if reverse_candidates:
        graph = dict(project.spec.graph)
        graph["nodes"][2]["evaluations"].reverse()
        project = revise(project, graph)
    plan = plan_graph(project, choices, (facts,))
    records = tuple(EvaluationRecord(model=ref, benchmark_sha256=benchmark.fingerprint(), metrics={"accuracy": score},
        status="completed", synthetic=False, simulation=False, producer_attempt_id=node + "-attempt")
        for node, ref, score in zip(("eval", "second-eval"), refs, (.1, .9)))
    selected = select_best(records, plan.graph.nodes[2].selection)
    evaluations = tuple(NodeEvaluationBinding(node_id=node, evaluation=record, tracking_acknowledged=True,
        producer=NodeExecutionEvidence(node_id=node, plan_sha256=plan.sha256, job_id=node + "-job",
            attempt_id=record.producer_attempt_id, manifest_sha256="d" * 64,
            status="completed", tracking_acknowledged=True)) for node, record in zip(("eval", "second-eval"), records))
    operation = compile_node(plan, "child", VerifiedBindings(registry, store, choices.executors[0],
        outputs=(NodeModelBinding(node_id="select", model=refs[1], selection=selected),),
        producers=producers, evaluations=evaluations))
    assert operation.model_input.policy.base == refs[1]
    assert operation.config.model_revision == "b" * 40
    assert set(root.revision for root in plan.nodes[-1].permitted_model_roots) == {"a" * 40, "b" * 40}


@pytest.mark.parametrize("invalid_context", ["missing", "incompatible"])
def test_every_selected_root_must_have_a_compatible_child_context(tmp_path, invalid_context):
    _, project, choices, facts, *_ = selected_roots_fixture(tmp_path)
    contexts = choices.models[:1] if invalid_context == "missing" else (choices.models[0],
        choices.models[1].model_copy(update={"profile": choices.models[1].profile.model_copy(update={"max_length": 64})}))
    with pytest.raises(ValueError, match="context|cutoff_len"):
        plan_graph(project, choices.model_copy(update={"models": contexts}), (facts,))


def ppo_handoff_fixture(tmp_path, parent_method="lora"):
    from test_model_artifacts import fixture_model, with_value_head

    from tuning_factory.graph_planner import ModelContext, ProducerEvidence, snapshot_artifact
    from tuning_factory.model_artifacts import ModelArtifactStore
    from tuning_factory.model_hub import ModelProfile
    registry, project, choices, facts = fixture(tmp_path)
    directory, descriptor = fixture_model(tmp_path, marker="reward")
    descriptor = with_value_head(directory, descriptor)
    store = ModelArtifactStore(tmp_path / "models")
    reward = store.freeze(directory, descriptor)
    producer = ProducerEvidence(model=reward, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
        manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="ppo", training_stage="rm",
        status="completed", tracking_acknowledged=True)
    path = tmp_path / "prompt.json"
    path.write_bytes(canonical([{"instruction": "question"}]))
    record = registry.register_dataset("prompt", "1", path, kind="prompt")
    dataset = DatasetRef(dataset_id="prompt", version="1", sha256=record["sha256"])
    recipes = tuple(Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
        for recipe in (RecipeSpec(recipe_id="parent", name="Parent", training_stage="ppo", input_kind="prompt",
                                  training_method=parent_method),
                       RecipeSpec(recipe_id="child", name="Child", training_stage="ppo", input_kind="prompt",
                                  training_method=parent_method if parent_method == "qlora" else "lora")))
    profile = ModelProfile(repo_id=descriptor.root.repo_id, revision=descriptor.root.revision,
        config_sha256=descriptor.config_sha256, architecture="qwen2", template="qwen")
    choices = choices.model_copy(update={"recipes": recipes, "models": (ModelContext(root=descriptor.root, profile=profile),),
        "datasets": (snapshot_dataset(registry, dataset),), "artifacts": (snapshot_artifact(store, reward, producer),),
        "node_resources": tuple(NodeResources(node_id=node, timeout_seconds=900, runtime_profile="ppo")
                                for node in ("parent", "child"))})

    def train(node_id, recipe, model_input):
        return {"kind": "train", "id": node_id, "label": node_id, "recipe_revision": recipe.revision_sha256,
                "model_input": model_input, "reward_binding": ModelLoadRef(base=reward).model_dump(mode="json"),
                "dataset_binding": "prompt", "executor_profile_ids": ["p"]}
    bindings = [DatasetBinding(binding_id="prompt", role="train", dataset=dataset)]
    return registry, project, choices, facts, store, producer, descriptor, recipes, train, bindings


@pytest.mark.parametrize("parent_method", ["lora", "qlora", "full"])
def test_ppo_adapter_handoff_to_ppo_is_refused_at_planning(tmp_path, parent_method):
    from tuning_factory.graph_schema import validate_graph
    from tuning_factory.training import PPO_CONTINUATION
    registry, project, choices, facts, _, _, descriptor, recipes, train, bindings = ppo_handoff_fixture(
        tmp_path, parent_method)
    root_input = ModelInput(policy=ModelLoadRef(base=descriptor.root), initialization=(
        "full_weights" if parent_method == "full" else "new_adapter")).model_dump(mode="json")
    graph = {"nodes": [train("parent", recipes[0], root_input),
                       train("child", recipes[1], {"node_id": "parent", "output_name": "model"})]}
    project = revise(project, graph, bindings)
    issues = [issue for issue in validate_graph(project.spec, choices.recipes, registry)
              if issue.code == "parent_transition"]
    if parent_method == "full":
        # A full PPO parent hands over full weights; the child trains a new adapter on them.
        assert issues == []
        plan_graph(project, choices, (facts,))
        return
    assert [(issue.node_id, issue.message) for issue in issues] == [("child", PPO_CONTINUATION)]
    with pytest.raises(ValueError, match="PPO adapter continuation is unsupported"):
        plan_graph(project, choices, (facts,))


def explicit_ppo_continuation(tmp_path):
    from tuning_factory.graph_planner import ProducerEvidence, snapshot_artifact
    registry, project, choices, facts, store, producer, descriptor, recipes, train, bindings = ppo_handoff_fixture(tmp_path)
    directory, adapter_descriptor = adapter_fixture(tmp_path, marker="ppo-adapter")
    adapter = store.freeze(directory, adapter_descriptor)
    adapter_producer = ProducerEvidence(model=adapter, job_id="ppo-adapter", attempt_id="ppo-adapter",
        manifest_sha256=adapter_descriptor.producer_manifest_sha256, runtime_profile="ppo", training_stage="ppo",
        status="completed", tracking_acknowledged=True)
    model_input = ModelInput(policy=ModelLoadRef(base=descriptor.root, adapter=adapter),
                             initialization="continue_adapter").model_dump(mode="json")
    project = revise(project, {"nodes": [train("child", recipes[1], model_input)]}, bindings)
    choices = choices.model_copy(update={"artifacts": (*choices.artifacts, snapshot_artifact(
        store, adapter, adapter_producer)), "node_resources": choices.node_resources[1:]})
    return registry, project, choices, facts, store, (producer, adapter_producer)


def test_explicit_ppo_continue_adapter_is_refused_at_planning(tmp_path):
    from tuning_factory.graph_schema import validate_graph
    from tuning_factory.training import PPO_CONTINUATION
    registry, project, choices, facts, *_ = explicit_ppo_continuation(tmp_path)
    issues = validate_graph(project.spec, choices.recipes, registry)
    assert [(issue.code, issue.node_id, issue.message) for issue in issues] == [
        ("train_contract", "child", PPO_CONTINUATION)]
    with pytest.raises(ValueError, match="PPO adapter continuation is unsupported"):
        plan_graph(project, choices, (facts,))


def test_compile_refuses_ppo_continue_adapter_from_a_plan_approved_before_the_rule(tmp_path, monkeypatch):
    from tuning_factory import graph_planner
    from tuning_factory.graph_planner import VerifiedBindings
    registry, project, choices, facts, store, producers = explicit_ppo_continuation(tmp_path)
    # Simulate a plan pinned by a release without the planning-time rule; compilation must still refuse.
    monkeypatch.setattr(graph_planner, "_definition_issues", lambda *args: [])
    monkeypatch.setattr(graph_planner, "_static_routes", lambda *args: None)
    plan = plan_graph(project, choices, (facts,))
    monkeypatch.undo()
    with pytest.raises(ValueError, match="PPO adapter continuation is unsupported"):
        compile_node(plan, "child", VerifiedBindings(registry, store, choices.executors[0], producers=producers))


def test_ppo_continuation_through_selection_is_refused_at_planning(tmp_path):
    from types import SimpleNamespace

    from tuning_factory.graph_planner import _static_routes
    from tuning_factory.graph_schema import EvaluateNode, NodeOutputRef, SelectNode
    *_, recipes, train, _ = ppo_handoff_fixture(tmp_path)
    root = HFRootRef(repo_id="test/tiny", revision="a" * 40, config_sha256="b" * 64, tokenizer_fingerprint="c" * 64)
    root_input = ModelInput(policy=ModelLoadRef(base=root), initialization="new_adapter").model_dump(mode="json")
    nodes = {"parent": TrainNode.model_validate({**train("parent", recipes[0], root_input), "reward_binding": None}),
             "eval": EvaluateNode(id="eval", label="Eval", model=NodeOutputRef(node_id="parent", output_name="model"),
                                  benchmark_revision="d" * 64, executor_profile_ids=("p",)),
             "select": SelectNode(id="select", label="Select", evaluations=(
                 NodeOutputRef(node_id="eval", output_name="evaluation"),),
                 selection={"metric": "accuracy", "direction": "max"}),
             "child": TrainNode.model_validate({**train("child", recipes[1], {"node_id": "select",
                                                                              "output_name": "model"}),
                                                "reward_binding": None})}
    with pytest.raises(ValueError, match="PPO adapter continuation is unsupported"):
        _static_routes(nodes["child"], nodes, {item.revision_sha256: item for item in recipes}, {},
                       (SimpleNamespace(runtime=SimpleNamespace(profile="ppo")),))


# -- Task 8.1: planning refusals the worker would otherwise raise after launch ----------------------

def destination_revisions(choices):
    """What real planning additionally requires: frozen transport and tracking destination revisions."""
    from tuning_factory.graph_planner import TrackingDestination
    return choices.model_copy(update={
        "executors": tuple(item.model_copy(update={"transport_settings_sha256": "1" * 64})
                           for item in choices.executors),
        "tracking_destination": TrackingDestination(profile_id="mlflow", revision="1",
                                                    public_settings_sha256="2" * 64)})


@pytest.mark.parametrize("simulation", [True, False])
@pytest.mark.parametrize("change,message", [
    ({"generation_mode": "sample"}, "v2 evaluation supports greedy causal-LM generation benchmarks only"),
    ({"precision": "fp32"}, "fp32 benchmark precision cannot be enforced by the pinned engine"),
    ({"semantic_model_kind": "reward_model"}, "v2 evaluation supports greedy causal-LM generation benchmarks only"),
])
def test_planning_refuses_benchmarks_the_pinned_engine_cannot_enforce(tmp_path, change, message, simulation):
    from tuning_factory.benchmarks import BenchmarkSpec
    from tuning_factory.graph_planner import BenchmarkRevision
    registry, project, choices, facts, store, ref, producer, benchmark = evaluation_fixture(tmp_path)
    choices = destination_revisions(choices)
    plan_graph(project, choices, (facts,), simulation=simulation)   # the greedy bf16 causal-LM benchmark plans
    changed = BenchmarkSpec.model_validate({**benchmark.model_dump(mode="json"), **change})
    graph = dict(project.spec.graph)
    graph["nodes"] = [dict(graph["nodes"][0], benchmark_revision=changed.fingerprint()), *graph["nodes"][1:]]
    choices = choices.model_copy(update={"benchmarks": (BenchmarkRevision(spec=changed,
                                                                          revision_sha256=changed.fingerprint()),)})
    with pytest.raises(ValueError) as refused:
        plan_graph(revise(project, graph), choices, (facts,), simulation=simulation)
    assert str(refused.value) == message   # the worker's exact words (training.check_generation_benchmark)


@pytest.mark.parametrize("simulation", [True, False])
def test_planning_refuses_a_zero_or_nonfinite_runpod_price(tmp_path, simulation):
    from test_gpu_servers import observation, settings
    _, project, choices, facts = fixture(tmp_path)
    choices = destination_revisions(choices)
    assert plan_graph(project, choices, (facts,), simulation=simulation).max_estimated_cost_usd == 8
    with pytest.raises(ValueError, match="^RunPod resource facts require a positive live hourly price$"):
        plan_graph(project, choices, (facts.model_copy(update={"total_hourly_cost_usd": 0}),), simulation=simulation)
    for price in (float("inf"), float("nan")):
        # 8.1 review M6: plan_graph re-validates the facts, and StrictModel (allow_inf_nan=False) refuses a
        # non-finite price before the planner's own math.isfinite guard (kept as defence in depth) is reached.
        forged = ResourceFacts.model_construct(**{**dict(facts), "total_hourly_cost_usd": price})
        with pytest.raises(ValidationError, match="Input should be a finite number"):
            plan_graph(project, choices, (forged,), simulation=simulation)
    server = observation()   # an SSH server the operator prices at 0 still plans
    profile = settings().profile(server, profile_id=choices.executors[0].profile_id, revision="1")
    plan = plan_graph(project, choices.model_copy(update={"executors": (profile,)}), (server,), simulation=simulation)
    assert plan.max_estimated_cost_usd == 0


# -- Task 11.2 follow-up: merges of adapters whose effective bases differ are refused at planning -------------

MERGE_INPUTS_DIFFER = "merge input effective bases/tokenizers/targets differ"   # check_merge_descriptors' text


def merge_fixture(tmp_path):
    """The fixture plus a second HF root with its context and a ``q_proj,v_proj`` recipe beside the default
    (``lora_target: all``) one."""
    from types import SimpleNamespace

    from tuning_factory.graph_planner import ModelContext
    registry, project, choices, facts = fixture(tmp_path)
    other = HFRootRef(repo_id="Qwen/Qwen2.5-1.5B-Instruct", revision="d" * 40, config_sha256="e" * 64,
                      tokenizer_fingerprint="f" * 64)
    targeted = RecipeSpec(recipe_id="targeted", name="targeted", hyperparameters={"lora_target": "q_proj,v_proj"})
    targeted = Revision(spec=targeted, revision_sha256=digest(canonical(targeted.model_dump(mode="json"))))
    choices = choices.model_copy(update={"recipes": (*choices.recipes, targeted),
                                         "models": (*choices.models, ModelContext(root=other))})
    return SimpleNamespace(registry=registry, project=project, choices=choices, facts=facts,
                           root=choices.models[0].root, other=other, default=choices.recipes[0].revision_sha256,
                           targeted=targeted.revision_sha256)


def lora(node_id, recipe, source):
    """A LoRA training: a new adapter on an HF root, or a continuation of ``source``'s output (a node ID)."""
    model_input = ({"node_id": source, "output_name": "model"} if isinstance(source, str) else
                   ModelInput(policy=ModelLoadRef(base=source), initialization="new_adapter").model_dump(mode="json"))
    return {"kind": "train", "id": node_id, "label": node_id, "recipe_revision": recipe, "model_input": model_input,
            "dataset_binding": "data", "executor_profile_ids": ["p"]}


def merged(node_id, sources):
    return {"kind": "merge", "id": node_id, "label": "Merge", "weights": [0.5, 0.5], "executor_profile_ids": ["p"],
            "models": [{"node_id": source, "output_name": "model"} for source in sources]}


def merge_case(m, nodes, evidence=()):
    project = revise(m.project, {"nodes": nodes, "retry_policy": {"max_attempts": 1}})
    choices = m.choices.model_copy(update={"base_targets": tuple(evidence), "node_resources": tuple(
        NodeResources(node_id=node["id"], timeout_seconds=600) for node in nodes)})
    return project, choices


def merge_issues(m, project, choices):
    from tuning_factory.graph_schema import validate_graph
    return [(issue.code, issue.message, issue.node_id)
            for issue in validate_graph(project.spec, choices.recipes, m.registry)]


def root_evidence(root, *leaves):
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    return ModelTargetEvidence(base=root, modules=tuple(
        TargetModuleEvidence(name=f"model.layers.0.self_attn.{leaf}", input_dimension=2, output_dimension=2)
        for leaf in leaves))


@pytest.mark.parametrize("case", ["direct", "continuation", "nested_merge", "continued_merge"])
def test_merges_of_adapters_from_different_base_roots_are_refused_at_validation_and_planning(tmp_path, case):
    """A real plan would otherwise train both branches on paid GPUs and only then fail at the merge's compile
    (``check_merge_descriptors``). The bases are resolved through the lineage: continuations keep their parent's
    base, a merge keeps its inputs' common base."""
    m = merge_fixture(tmp_path)
    a, b, x = lora("a", m.default, m.root), lora("b", m.default, m.root), lora("x", m.default, m.other)
    nodes = {
        "direct": [a, x, merged("m", ("a", "x"))],
        "continuation": [a, x, lora("c", m.default, "a"), merged("m", ("c", "x"))],
        "nested_merge": [a, b, x, merged("ab", ("a", "b")), merged("m", ("ab", "x"))],
        "continued_merge": [a, b, x, merged("ab", ("a", "b")), lora("c", m.default, "ab"), merged("m", ("x", "c"))],
    }[case]
    project, choices = merge_case(m, nodes)
    # Reported on the merge node only (the inner same-root merge is fine), before any plan exists.
    assert merge_issues(m, project, choices) == [("merge_bases_differ", MERGE_INPUTS_DIFFER, "m")]
    with pytest.raises(ValueError, match=f"^merge_bases_differ: {MERGE_INPUTS_DIFFER}$"):
        plan_graph(project, choices, (m.facts,))


@pytest.mark.parametrize("case", ["siblings", "continuation", "nested_merge"])
def test_merges_of_adapters_on_one_base_root_still_plan(tmp_path, case):
    m = merge_fixture(tmp_path)
    a, b = lora("a", m.default, m.root), lora("b", m.default, m.root)
    nodes = {
        "siblings": [a, b, merged("m", ("a", "b"))],
        "continuation": [a, b, lora("c", m.default, "a"), merged("m", ("c", "b"))],
        "nested_merge": [a, b, lora("d", m.default, m.root), merged("ab", ("a", "b")), merged("m", ("ab", "d"))],
    }[case]
    project, choices = merge_case(m, nodes, (root_evidence(m.root, "q_proj", "k_proj", "v_proj"),))
    assert merge_issues(m, project, choices) == []
    plan = plan_graph(project, choices, (m.facts,))
    assert {entry.node.id for entry in plan.nodes} == {node["id"] for node in nodes}


def test_a_merge_whose_base_is_only_known_at_run_time_still_plans(tmp_path):
    """A new adapter trained on a materialized model has a run-time artifact as its base: compile stays
    authoritative (``check_merge_descriptors``), as before this check."""
    m = merge_fixture(tmp_path)
    nodes = [lora("a", m.default, m.root), lora("x", m.default, m.other),
             {"kind": "materialize", "id": "full", "label": "Materialize", "executor_profile_ids": ["p"],
              "model": {"node_id": "a", "output_name": "model"}},
             lora("y", m.default, "full"), merged("m", ("y", "x"))]
    project, choices = merge_case(m, nodes)
    assert merge_issues(m, project, choices) == []
    assert plan_graph(project, choices, (m.facts,)).nodes[-1].node.id == "m"


def test_merges_of_adapters_with_different_targets_on_one_root_are_refused_when_the_evidence_shows_it(tmp_path):
    """``lora_target: all`` expands over the root's module evidence (LLaMA-Factory's rule, as the simulated worker
    and ``check_continuation_targets`` expand it); ``q_proj,v_proj`` is explicit. Without evidence the expansion is
    unknown and compile stays authoritative; evidence of exactly q_proj and v_proj makes the two the same."""
    m = merge_fixture(tmp_path)
    nodes = [lora("a", m.default, m.root), lora("t", m.targeted, m.root), merged("m", ("a", "t"))]
    project, choices = merge_case(m, nodes, (root_evidence(m.root, "q_proj", "k_proj", "v_proj"),))
    assert merge_issues(m, project, choices) == []   # validation has no module evidence
    with pytest.raises(ValueError, match=f"^{MERGE_INPUTS_DIFFER}$"):
        plan_graph(project, choices, (m.facts,))
    for evidence in ((), (root_evidence(m.root, "q_proj", "v_proj"),)):
        project, choices = merge_case(m, nodes, evidence)
        assert plan_graph(project, choices, (m.facts,)).nodes[-1].node.id == "m"
    # A continuation of the targeted adapter keeps its targets: merged with a same-target sibling, it plans.
    nodes = [lora("t", m.targeted, m.root), lora("u", m.targeted, m.root), lora("c", m.targeted, "t"),
             merged("m", ("c", "u"))]
    project, choices = merge_case(m, nodes, (root_evidence(m.root, "q_proj", "k_proj", "v_proj"),))
    assert plan_graph(project, choices, (m.facts,)).nodes[-1].node.id == "m"


# -- Task 11.5 (R1 m2, m3): provable merge and selection refusals happen at planning -------------------------------

def merged_of(node_id, sources, weights=None):
    """A merge of node outputs (IDs) and external artifacts (refs), equally weighted by default."""
    weights = weights or {2: [0.5, 0.5], 3: [0.5, 0.25, 0.25], 4: [0.25, 0.25, 0.25, 0.25]}[len(sources)]
    return {"kind": "merge", "id": node_id, "label": "Merge", "weights": weights, "executor_profile_ids": ["p"],
            "models": [{"node_id": source, "output_name": "model"} if isinstance(source, str)
                       else source.model_dump(mode="json") for source in sources]}


def materialized(node_id, source):
    return {"kind": "materialize", "id": node_id, "label": "Materialize", "executor_profile_ids": ["p"],
            "model": {"node_id": source, "output_name": "model"}}


def evaluated(node_id, model, benchmark):
    return {"kind": "evaluate", "id": node_id, "label": "Evaluate", "benchmark_revision": benchmark,
            "executor_profile_ids": ["p"], "model": {"node_id": model, "output_name": "model"}}


def chosen(node_id, evaluations):
    return {"kind": "select", "id": node_id, "label": "Select",
            "evaluations": [{"node_id": item, "output_name": "evaluation"} for item in evaluations],
            "selection": {"metric": "accuracy", "direction": "max", "allow_partial": True}}


def ranked(m, rank):
    recipe = RecipeSpec(recipe_id=f"r{rank}", name=f"rank {rank}",
                        hyperparameters={"lora_rank": rank, "lora_target": "q_proj,v_proj"})
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    m.choices = m.choices.model_copy(update={"recipes": (*m.choices.recipes, revision)})
    return revision.revision_sha256


def benchmarked(m, tmp_path):
    """A validation dataset and a benchmark on the fixture root, so the graph can evaluate and select."""
    from tuning_factory.benchmarks import BenchmarkSpec
    from tuning_factory.graph_planner import BenchmarkRevision
    path = tmp_path / "held.json"
    path.write_bytes(canonical([{"instruction": "held-out", "output": "a"}, {"instruction": "second", "output": "b"}]))
    record = m.registry.register_dataset("held", "1", path)
    held = DatasetRef(dataset_id="held", version="1", sha256=record["sha256"])
    benchmark = BenchmarkSpec(dataset=held, evaluator_kind="classification", evaluator_version="1",
                              ordered_sample_ids=("row:1", "row:0"), max_new_tokens=32, cutoff_len=128, seed=42,
                              precision="bf16", quantization_bit=None,
                              tokenizer_fingerprint=m.root.tokenizer_fingerprint, template="qwen",
                              semantic_model_kind="causal_lm")
    m.project = revise(m.project, None, [*m.project.spec.model_dump(mode="json")["datasets"],
                                         {"binding_id": "held", "role": "validation", "dataset": held.model_dump()}])
    m.choices = m.choices.model_copy(update={
        "benchmarks": (BenchmarkRevision(spec=benchmark, revision_sha256=benchmark.fingerprint()),),
        "datasets": (*m.choices.datasets, snapshot_dataset(m.registry, held))})
    return benchmark.fingerprint()


def planned_or_refused(m, nodes, evidence=()):
    project, choices = merge_case(m, nodes, evidence)
    return project, choices, lambda: plan_graph(project, choices, (m.facts,))


@pytest.mark.parametrize("case", ["three_inputs", "nested_with_unknown"])
def test_known_merge_inputs_that_conflict_are_refused_beside_an_unknown_one(tmp_path, case):
    """``_MergeLineage`` compares the inputs whose base is known: one adapter whose base exists only at run time (a
    new adapter on a materialized model) no longer hides two known inputs on different roots."""
    m = merge_fixture(tmp_path)
    head = [lora("a", m.default, m.root), lora("x", m.default, m.other), materialized("full", "a"),
            lora("y", m.default, "full")]
    nodes = {"three_inputs": [*head, merged_of("m", ("a", "x", "y"))],
             "nested_with_unknown": [*head, merged_of("ay", ("a", "y")), merged_of("m", ("ay", "x"))]}[case]
    project, choices, plan = planned_or_refused(m, nodes)
    assert merge_issues(m, project, choices) == [("merge_bases_differ", MERGE_INPUTS_DIFFER, "m")]
    with pytest.raises(ValueError, match=f"^merge_bases_differ: {MERGE_INPUTS_DIFFER}$"):
        plan()
    # One known input beside an unknown one stays for compile to decide.
    project, choices, plan = planned_or_refused(m, [*head, merged_of("m", ("x", "y"))])
    assert merge_issues(m, project, choices) == [] and plan().nodes[-1].node.id == "m"


def test_a_materialized_model_fed_straight_into_a_merge_is_refused_at_planning(tmp_path):
    """Compile refuses a full model as a merge input (``check_materializable``); planning now says so first."""
    m = merge_fixture(tmp_path)
    nodes = [lora("a", m.default, m.root), lora("b", m.default, m.root), materialized("full", "a"),
             merged_of("m", ("full", "b"))]
    with pytest.raises(ValueError, match="^merge requires plain causal LoRA/QLoRA parents$"):
        planned_or_refused(m, nodes)[2]()


@pytest.mark.parametrize("case, refused", [("only_materialized", True), ("one_adapter", False)])
def test_a_selection_of_only_materialized_models_fed_into_a_merge_is_refused_at_planning(tmp_path, case, refused):
    """Provable only when no candidate is an adapter: a selection that may pick the adapter ``b`` still plans."""
    m = merge_fixture(tmp_path)
    benchmark = benchmarked(m, tmp_path)
    nodes = [lora("a", m.default, m.root), lora("b", m.default, m.root), lora("c", m.default, m.root),
             materialized("full_a", "a"), materialized("full_b", "b"), evaluated("efa", "full_a", benchmark),
             evaluated("efb", "full_b", benchmark), evaluated("eb", "b", benchmark),
             chosen("s", ("efa", "efb" if case == "only_materialized" else "eb")), merged_of("m", ("s", "c"))]
    plan = planned_or_refused(m, nodes)[2]
    if refused:
        with pytest.raises(ValueError, match="^merge requires plain causal LoRA/QLoRA parents$"):
            plan()
    else:
        assert plan().nodes[-1].node.id == "m"


@pytest.mark.parametrize("case, refused", [("nested", True), ("nested_at_limit", False), ("selected", True),
                                           ("selected_smaller", False)])
def test_merged_rank_is_carried_through_nested_merges_and_selections(tmp_path, case, refused):
    """merge(merge(a128, b128), d8) has rank 264 > 256: refused before any training. A selection contributes its
    smallest candidate's rank (the least the merge can receive): min(256, 128) + 129 > 256, min(256, 8) + 128 is not."""
    m = merge_fixture(tmp_path)
    r128, r129, r8 = ranked(m, 128), ranked(m, 129), ranked(m, 8)
    benchmark = benchmarked(m, tmp_path)
    head = [lora("a", r128, m.root), lora("b", r128, m.root), lora("d", r8, m.root), merged_of("ab", ("a", "b"))]
    nodes = {
        "nested": [*head, merged_of("m", ("ab", "d"))],
        "nested_at_limit": [*head, lora("e", r128, m.root), merged_of("m", ("a", "e"))],
        "selected": [*head, evaluated("eab", "ab", benchmark), evaluated("ea", "a", benchmark),
                     chosen("s", ("eab", "ea")), lora("f", r129, m.root), merged_of("m", ("s", "f"))],
        "selected_smaller": [*head, evaluated("eab", "ab", benchmark), evaluated("ed", "d", benchmark),
                             chosen("s", ("eab", "ed")), lora("f", r128, m.root), merged_of("m", ("s", "f"))],
    }[case]
    plan = planned_or_refused(m, nodes)[2]
    if refused:
        with pytest.raises(ValueError, match="^merged rank exceeds 256$"):
            plan()
    else:
        assert plan().nodes[-1].node.id == "m"


@pytest.mark.parametrize("case, refused", [("only_candidate", True), ("both_candidates", True),
                                           ("one_of_two", False)])
def test_duplicate_merge_inputs_through_a_selection_are_refused_at_planning(tmp_path, case, refused):
    """A merge whose inputs can only be the same artifact (a selection that can only pick a model the merge already
    takes) fails compile's "distinct actual artifacts"; a selection that may pick another model still plans."""
    m = merge_fixture(tmp_path)
    benchmark = benchmarked(m, tmp_path)
    head = [lora("a", m.default, m.root), lora("b", m.default, m.root), evaluated("ea", "a", benchmark),
            evaluated("eb", "b", benchmark), evaluated("ea2", "a", benchmark)]
    nodes = {
        "only_candidate": [*head, chosen("s", ("ea",)), merged_of("m", ("s", "a"))],
        "both_candidates": [*head, chosen("s", ("ea",)), chosen("t", ("ea2",)), merged_of("m", ("s", "t"))],
        "one_of_two": [*head, chosen("s", ("ea", "eb")), merged_of("m", ("s", "a"))],
    }[case]
    plan = planned_or_refused(m, nodes)[2]
    if refused:
        with pytest.raises(ValueError, match="^merge requires distinct actual artifacts$"):
            plan()
    else:
        assert plan().nodes[-1].node.id == "m"


@pytest.mark.parametrize("case, message", [
    ("repeated_evaluation", "selection candidates must be distinct models"),
    ("same_model_twice", "selection candidates must be distinct models"),
    ("same_model_through_a_selection", "selection candidates must be distinct models"),
    ("two_merged_candidates", "a selection takes at most one merged candidate"),
    ("only_a_merged_candidate", "a selection needs an original candidate beside its merged one"),
    ("distinct", None), ("one_merged_candidate", None)])
def test_selection_shapes_that_can_never_resolve_are_refused_at_planning(tmp_path, case, message):
    """R1 m3: ``s([e1, e1])``, two evaluations of the same model, and more than one merged candidate always fail at
    the selection (``select_best``'s duplicate candidates, ``resolve_local_selection``'s one merged candidate); a
    selection whose only candidate is merged always records no winner."""
    m = merge_fixture(tmp_path)
    benchmark = benchmarked(m, tmp_path)
    head = [lora("a", m.default, m.root), lora("b", m.default, m.root), lora("c", m.default, m.root),
            merged_of("ab", ("a", "b")), merged_of("bc", ("b", "c")), evaluated("ea", "a", benchmark),
            evaluated("ea2", "a", benchmark), evaluated("eb", "b", benchmark), evaluated("eab", "ab", benchmark),
            evaluated("ebc", "bc", benchmark)]
    tail = {
        "repeated_evaluation": [chosen("s", ("ea", "ea", "eb"))],
        "same_model_twice": [chosen("s", ("ea", "ea2", "eb"))],
        "same_model_through_a_selection": [chosen("t", ("ea",)), evaluated("et", "t", benchmark),
                                           chosen("s", ("et", "ea2"))],
        "two_merged_candidates": [chosen("s", ("ea", "eab", "ebc"))],
        "only_a_merged_candidate": [chosen("s", ("eab",))],
        "distinct": [chosen("s", ("ea", "eb"))],
        "one_merged_candidate": [chosen("s", ("ea", "eb", "eab"))],
    }[case]
    plan = planned_or_refused(m, [*head, *tail])[2]
    if message:
        with pytest.raises(ValueError, match=f"^{message}$"):
            plan()
    else:
        assert plan().nodes[-1].node.id == "s"


@pytest.mark.parametrize("case", ["repeated_evaluation", "evaluated_twice", "selection_alias", "nested_alias"])
def test_correlated_selected_models_are_refused_at_planning(tmp_path, case):
    """A selected winner and its forwarding aliases remain one model even with two possible winners."""
    from tuning_factory.graph_planner import NodeRefusal
    m = merge_fixture(tmp_path)
    benchmark = benchmarked(m, tmp_path)
    head = [lora("a", m.default, m.root), lora("b", m.default, m.root), evaluated("ea", "a", benchmark),
            evaluated("eb", "b", benchmark), chosen("s", ("ea", "eb")), evaluated("es", "s", benchmark)]
    tail = {
        "repeated_evaluation": [chosen("last", ("es", "es"))],
        "evaluated_twice": [evaluated("es2", "s", benchmark), chosen("last", ("es", "es2"))],
        "selection_alias": [chosen("t", ("es",)), merged_of("last", ("s", "t"))],
        "nested_alias": [chosen("t", ("es",)), evaluated("et", "t", benchmark), chosen("u", ("et",)),
                         merged_of("last", ("s", "u"))],
    }[case]
    message = ("selection candidates must be distinct models" if case in {"repeated_evaluation", "evaluated_twice"}
               else "merge requires distinct actual artifacts")
    with pytest.raises(NodeRefusal, match=f"^{message}$") as refused:
        planned_or_refused(m, [*head, *tail])[2]()
    assert refused.value.node_id == "last"


@pytest.mark.parametrize("case", ["conditional_merge", "independent_selections"])
def test_independent_selected_model_choices_still_plan(tmp_path, case):
    """Possible winners may overlap when the inputs do not share a selected model's identity."""
    m = merge_fixture(tmp_path)
    benchmark = benchmarked(m, tmp_path)
    head = [lora("a", m.default, m.root), lora("b", m.default, m.root), evaluated("ea", "a", benchmark),
            evaluated("eb", "b", benchmark), chosen("s", ("ea", "eb"))]
    tail = {"conditional_merge": [merged_of("last", ("s", "a"))],
            "independent_selections": [evaluated("ea2", "a", benchmark), evaluated("eb2", "b", benchmark),
                                       chosen("t", ("ea2", "eb2")), merged_of("last", ("s", "t"))]}[case]
    assert planned_or_refused(m, [*head, *tail])[2]().nodes[-1].node.id == "last"
