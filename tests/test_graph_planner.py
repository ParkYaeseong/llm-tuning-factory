import pytest

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


def evaluation_fixture(tmp_path, role="validation"):
    from test_model_artifacts import fixture_model

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
