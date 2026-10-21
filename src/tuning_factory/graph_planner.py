"""Pure graph planning and ready-time compilation from explicit typed evidence.

Snapshot builders read local registered bytes. ExecutionChoices is a serialized
planning snapshot, not authorization. VerifiedBindings is constructed by the
controller from its job/attempt ledger and local stores, never from request JSON.
Benchmark sample IDs are ``row:<zero-based registered row ordinal>``; a DatasetRef
pins those ordinals to exact bytes. No provider discovery, transfer or mutation is
performed here. Worker preflight must repeat byte verification before execution.
"""
from __future__ import annotations

import math
import re
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Literal

from pydantic import Field, model_validator

from .benchmarks import BenchmarkSpec, EvaluationRecord, SelectionResult
from .config import GraphTrainingConfig, StrictModel
from .dataset_splits import assert_no_input_overlap, input_fingerprint, load_dataset
from .executor_profiles import (
    PublicExecutorProfile,
    ResourceFacts,
    ResourceObservation,
    approval_destination,
    observation_key,
)
from .graph_schema import (
    EvaluateNode,
    GraphSpec,
    Issue,
    MaterializeNode,
    MergeNode,
    Node,
    NodeOutputRef,
    SelectNode,
    TrainNode,
    graph_issues,
    node_refs,
)
from .model_artifacts import (
    BaseRef,
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    ModelDescriptor,
    ModelInput,
    ModelLoadRef,
)
from .model_hub import ModelProfile
from .operations import (
    EvaluateOperation,
    LocalSelectSpec,
    MaterializeOperation,
    MergeOperation,
    OperationLineage,
    OperationResources,
    RuntimeProfile,
    TrainOperation,
    resolve_local_selection,
)
from .projects import DatasetRef, LocalId, ProjectSpec, Revision, Sha256
from .recipes import RecipeSpec, recipe_training_options
from .registry import Registry, canonical, digest
from .runtime_profiles import engine_profile
from .training import OFT_UNFREEZABLE, PPO_CONTINUATION, check_generation_benchmark, requires_frozen_reference

RUNPOD_PRICE_REQUIRED = "RunPod resource facts require a positive live hourly price"
# check_merge_descriptors' refusal; planning reports it as ``merge_bases_differ`` when the lineage shows it.
MERGE_INPUTS_DIFFER = "merge input effective bases/tokenizers/targets differ"


class DatasetSnapshot(StrictModel):
    dataset: DatasetRef
    kind: Literal["instruction", "text", "preference", "prompt", "kto"]
    input_fingerprints: tuple[Sha256, ...] = Field(min_length=1)
    synthetic: bool
    export_allowed: bool


def snapshot_dataset(registry: Registry, ref: DatasetRef) -> DatasetSnapshot:
    record, rows, schema = load_dataset(registry, ref)
    return DatasetSnapshot(dataset=ref, kind=record.get("kind", "instruction"),
        input_fingerprints=tuple(input_fingerprint(row, record.get("kind", "instruction"), **schema) for row in rows),
        synthetic=record.get("synthetic", False), export_allowed=record.get("export_allowed", False))


def validate_sample_ids(benchmark: BenchmarkSpec, snapshot: DatasetSnapshot) -> tuple[int, ...]:
    if benchmark.dataset != snapshot.dataset:
        raise ValueError("benchmark dataset snapshot mismatch")
    ordinals = []
    for value in benchmark.ordered_sample_ids:
        if not re.fullmatch(r"row:(0|[1-9][0-9]*)", value):
            raise ValueError("sample ID must use canonical row:<ordinal> form")
        ordinal = int(value[4:])
        if ordinal >= len(snapshot.input_fingerprints):
            raise ValueError("sample ID is outside the pinned dataset")
        ordinals.append(ordinal)
    if len(set(ordinals)) != len(ordinals):
        raise ValueError("duplicate sample IDs")
    return tuple(ordinals)


class BenchmarkRevision(StrictModel):
    spec: BenchmarkSpec
    revision_sha256: Sha256

    @model_validator(mode="after")
    def checked_hash(self):
        if self.revision_sha256 != self.spec.fingerprint():
            raise ValueError("benchmark revision hash mismatch")
        return self


class ModelContext(StrictModel):
    """Caller supplies an inspected tokenizer inventory fingerprint in root."""
    root: HFRootRef
    profile: ModelProfile | None = None

    @model_validator(mode="after")
    def consistent_profile(self):
        if self.profile and (self.profile.repo_id, self.profile.revision, self.profile.config_sha256) != (
                self.root.repo_id, self.root.revision, self.root.config_sha256):
            raise ValueError("model profile differs from verified HF root")
        return self


class ProducerEvidence(StrictModel):
    model: ModelArtifactRef
    job_id: LocalId
    attempt_id: LocalId
    manifest_sha256: Sha256
    runtime_profile: Literal["primary", "ppo"]
    training_stage: Literal["pt", "sft", "rm", "ppo", "dpo", "kto"] | None = None
    status: Literal["completed"]
    tracking_acknowledged: bool = Field(strict=True)
    plan_sha256: Sha256 | None = None
    node_id: LocalId | None = None


class TargetModuleEvidence(StrictModel):
    name: str = Field(pattern=r"^[A-Za-z0-9_][A-Za-z0-9_.]*$")
    input_dimension: int = Field(strict=True, ge=1)
    output_dimension: int = Field(strict=True, ge=1)


class ModelTargetEvidence(StrictModel):
    """Complete inspected linear-module inventory of the exact effective base.

    The model inspector supplies this inventory; it must not copy an adapter's
    target list. For artifact bases the compiler additionally checks weight
    dimensions against the verified full-model safetensors inventory.
    """
    base: BaseRef
    modules: tuple[TargetModuleEvidence, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def unique_modules(self):
        if len({item.name for item in self.modules}) != len(self.modules):
            raise ValueError("duplicate inspected target modules")
        return self


class TrackingDestination(StrictModel):
    profile_id: LocalId
    revision: LocalId
    public_settings_sha256: Sha256


class ArtifactSnapshot(StrictModel):
    model: ModelArtifactRef
    descriptor: ModelDescriptor
    producer: ProducerEvidence

    @model_validator(mode="after")
    def evidence_matches(self):
        _check_producer(self.model, self.descriptor, self.producer)
        return self


def _check_producer(ref, descriptor, producer):
    producer = ProducerEvidence.model_validate(producer.model_dump())
    if digest(canonical(descriptor.model_dump(mode="json"))) != ref.artifact_id:
        raise ValueError("artifact descriptor hash mismatch")
    if (producer.model != ref or producer.job_id != descriptor.producer_job_id
            or producer.attempt_id != descriptor.producer_attempt_id
            or producer.manifest_sha256 != descriptor.producer_manifest_sha256):
        raise ValueError("artifact producer evidence mismatch")
    if not producer.tracking_acknowledged:
        raise ValueError("artifact producer tracking acknowledgment is pending")


def snapshot_artifact(store: ModelArtifactStore, ref: ModelArtifactRef, producer: ProducerEvidence):
    return ArtifactSnapshot(model=ref, descriptor=store.verify(ref), producer=producer)


class NodeResources(StrictModel):
    node_id: LocalId
    timeout_seconds: int = Field(strict=True, ge=1, le=604800)
    runtime_profile: Literal["primary", "ppo"] = "primary"


class ExecutionChoices(StrictModel):
    recipes: tuple[Revision[RecipeSpec], ...] = ()
    benchmarks: tuple[BenchmarkRevision, ...] = ()
    datasets: tuple[DatasetSnapshot, ...] = ()
    models: tuple[ModelContext, ...] = ()
    artifacts: tuple[ArtifactSnapshot, ...] = ()
    executors: tuple[PublicExecutorProfile, ...] = ()
    node_resources: tuple[NodeResources, ...] = ()
    base_targets: tuple[ModelTargetEvidence, ...] = ()
    tracking_destination: TrackingDestination | None = None


class InputContract(StrictModel):
    source: NodeOutputRef
    required_port: Literal["model", "evaluation"]
    conditional: Literal[True] = True
    checks: tuple[str, ...] = ("completed_producer", "tracking_acknowledged", "inventory", "runtime", "metadata")


class NodePlan(StrictModel):
    node: Node
    inputs: tuple[InputContract, ...]
    permitted_model_roots: tuple[HFRootRef, ...]
    allowed_resources: tuple[OperationResources, ...]
    max_estimated_cost_usd: float = Field(ge=0)
    max_duration_seconds: int = Field(ge=0)


class GraphPlan(StrictModel):
    project_revision: Revision[ProjectSpec]
    definition_sha256: Sha256
    graph: GraphSpec
    execution_choices: ExecutionChoices
    resource_snapshots: tuple[ResourceObservation, ...]
    nodes: tuple[NodePlan, ...]
    max_estimated_cost_usd: float = Field(ge=0)
    max_duration_seconds: int = Field(ge=0)
    max_concurrent_gpu_count: int = Field(ge=0)
    simulation: bool = Field(default=True, strict=True)
    sha256: Sha256


class DestinationRevision(StrictModel):
    profile_id: LocalId
    revision: LocalId
    profile_sha256: Sha256
    pod_id: LocalId


class ServerDestinationRevision(StrictModel):
    profile_id: LocalId
    revision: LocalId
    profile_sha256: Sha256
    resource_id: str


class ApprovalGrant(StrictModel):
    plan_sha256: Sha256
    destination_profile_revisions: tuple[DestinationRevision | ServerDestinationRevision, ...]

    @classmethod
    def for_plan(cls, plan: GraphPlan):
        """Controller calls only after separate explicit submission approval."""
        _check_plan(plan)
        profiles = {resource.executor.profile_id: resource.executor for node in plan.nodes
                    for resource in node.allowed_resources}
        from pydantic import TypeAdapter
        destination = TypeAdapter(DestinationRevision | ServerDestinationRevision)
        return cls(plan_sha256=plan.sha256, destination_profile_revisions=tuple(
            destination.validate_python(approval_destination(p))
            for p in sorted(profiles.values(), key=lambda value: value.profile_id)))

    def validate_plan(self, plan: GraphPlan):
        if self != ApprovalGrant.for_plan(plan):
            raise ValueError("approval does not match pinned plan/destination revisions")


def _revision(revision):
    if digest(canonical(revision.spec.model_dump(mode="json"))) != revision.revision_sha256:
        raise ValueError("definition revision hash mismatch")


def _unique(items, key, name):
    result = {key(item): item for item in items}
    if len(result) != len(items):
        raise ValueError(f"duplicate {name}")
    return result


def _dataset_key(ref):
    return ref.dataset_id, ref.version, ref.sha256


def _artifact_key(ref):
    return ref.artifact_id, ref.inventory_sha256


def _recipe(node, recipes):
    revision = recipes[node.recipe_revision]
    _revision(revision)
    values = revision.spec.model_dump()
    values.update(node.overrides.model_dump(exclude_none=True))
    return RecipeSpec.model_validate(values)


def _requires_materialization(parent: RecipeSpec, child: RecipeSpec):
    """Only adapter weights need conversion before a changed training contract."""
    return parent.training_method in {"lora", "qlora", "oft"} and (
        parent.training_stage == "sft" and child.training_stage in {"dpo", "kto"}
        or parent.training_method != child.training_method)


def _definition_issues(project, graph, recipes, datasets):
    issues = graph_issues(graph)
    by_recipe = {item.revision_sha256: item for item in recipes}
    by_data = {_dataset_key(item.dataset): item for item in datasets}
    bindings = {item.binding_id: item for item in project.datasets}
    roles = {}
    for binding in project.datasets:
        roles.setdefault(_dataset_key(binding.dataset), set()).add(binding.role)
    if any(len(values) != 1 for values in roles.values()):
        issues.append(Issue(code="dataset_role", message="the same dataset cannot be relabeled between roles"))
    train, heldout = set(), set()
    for binding in project.datasets:
        snapshot = by_data.get(_dataset_key(binding.dataset))
        if snapshot is None:
            issues.append(Issue(code="dataset", message="dataset snapshot is missing"))
        else:
            (train if binding.role == "train" else heldout).update(snapshot.input_fingerprints)
    if train & heldout:
        issues.append(Issue(code="input_overlap", message="training and held-out inputs overlap"))
    for node in graph.nodes:
        if not isinstance(node, TrainNode):
            continue
        try:
            recipe = _recipe(node, by_recipe)
            if recipe.training_method == "oft":   # every graph training output is frozen
                raise ValueError(OFT_UNFREEZABLE)
            binding = bindings[node.dataset_binding]
            if binding.role != "train":
                raise ValueError("training requires a train dataset binding")
            if not re.fullmatch(r"[a-zA-Z0-9_-]+", binding.dataset.dataset_id):
                raise ValueError("dataset ID is unsupported by the training adapter")
            if by_data[_dataset_key(binding.dataset)].kind != recipe.input_kind:
                raise ValueError("training dataset kind differs from recipe input kind")
            reward = node.reward_binding or (node.model_input.reward_model if isinstance(node.model_input, ModelInput) else None)
            if (recipe.training_stage == "ppo") != (reward is not None):
                raise ValueError("PPO requires a typed reward binding and other stages cannot use one")
            if (recipe.training_stage == "ppo" and isinstance(node.model_input, ModelInput)
                    and node.model_input.initialization == "continue_adapter"):
                raise ValueError(PPO_CONTINUATION)
        except (KeyError, ValueError) as exc:
            issues.append(Issue(code="train_contract", message=str(exc), node_id=node.id))
    nodes = {item.id: item for item in graph.nodes}
    for node in graph.nodes:
        for ref, port in node_refs(node):
            parent = nodes.get(ref.node_id)
            if port != "model" or not isinstance(parent, TrainNode):
                continue
            try:
                parent_recipe = _recipe(parent, by_recipe)
                if parent_recipe.training_method == "oft":
                    raise ValueError("OFT artifact handoff/evaluation/merge/materialize is unsupported")
                if isinstance(node, TrainNode):
                    child = _recipe(node, by_recipe)
                    if _requires_materialization(parent_recipe, child):
                        raise ValueError("parent transition requires explicit materialize")
                    if (parent_recipe.training_stage == "ppo") != (child.training_stage == "ppo"):
                        raise ValueError("unsupported primary/legacy PPO artifact route")
                    # The policy handoff of an adapter parent compiles to continue_adapter (plan §6 Q6).
                    if (child.training_stage == "ppo" and ref == node.model_input
                            and parent_recipe.training_method in {"lora", "qlora"}):
                        raise ValueError(PPO_CONTINUATION)
            except (KeyError, ValueError) as exc:
                issues.append(Issue(code="parent_transition", message=str(exc), node_id=node.id))
    lineage = _MergeLineage(nodes, by_recipe)
    for node in graph.nodes:
        if isinstance(node, MergeNode) and lineage.inputs_differ(node):
            issues.append(Issue(code="merge_bases_differ", message=MERGE_INPUTS_DIFFER, node_id=node.id))
    return issues


class _MergeLineage:
    """The effective base and adapter targets each merge input can resolve to, from the graph alone (and, when
    planning, the artifact snapshots and the bases' module evidence), as compilation will resolve them:

    - a new LoRA/QLoRA adapter's effective base is its policy base (an HF root or a model artifact); its targets are
      ``lora_target`` (``all`` expands over the base's module evidence, LLaMA-Factory's rule as the simulated worker
      and ``check_continuation_targets`` expand it; unknown without evidence);
    - a continuation keeps its parent adapter's base and targets; a merge keeps its inputs' common ones; a selection
      offers each candidate's; an artifact adapter has its descriptor's;
    - a full-model or materialized parent's output is a run-time artifact, so an adapter trained on it has an unknown
      base; so do a missing artifact snapshot, an unresolvable recipe and a cycle (reported elsewhere). There the
      check stays silent and compilation (``check_merge_descriptors``) stays authoritative.

    A candidate is ``(base key, sorted targets or None)``; ``None`` instead of a set of candidates means unknown."""

    def __init__(self, nodes, recipes, artifacts=None, evidence=()):
        self.nodes, self.recipes, self.artifacts = nodes, recipes, artifacts
        self.evidence = {item.base.model_dump_json(): item for item in evidence}
        self.cache: dict[str, frozenset | None] = {}

    def _targets(self, recipe, base) -> tuple[str, ...] | None:
        lora_target = recipe.hyperparameters.lora_target
        if lora_target != "all":
            return tuple(sorted(lora_target.split(",")))
        evidence = self.evidence.get(base.model_dump_json())
        return None if evidence is None else tuple(sorted({item.name.rsplit(".", 1)[-1] for item in evidence.modules}))

    def _artifact(self, ref):
        snapshot = self.artifacts.get(_artifact_key(ref)) if self.artifacts is not None else None
        return snapshot.descriptor if snapshot is not None else None

    def candidates(self, value, active=frozenset()) -> frozenset | None:
        if isinstance(value, ModelArtifactRef):
            descriptor = self._artifact(value)
            if descriptor is None or descriptor.adapter is None:
                return None
            return frozenset({(descriptor.effective_base.model_dump_json(),
                               tuple(sorted(descriptor.adapter.target_modules)))})
        if not isinstance(value, NodeOutputRef):
            return None
        node_id = value.node_id
        if node_id in self.cache:
            return self.cache[node_id]
        node = self.nodes.get(node_id)
        if node is None or node_id in active:
            return None
        try:
            result = self._node(node, active | {node_id})
        except (KeyError, ValueError):
            result = None
        self.cache[node_id] = result
        return result

    def _node(self, node, active) -> frozenset | None:
        if isinstance(node, TrainNode):
            recipe = _recipe(node, self.recipes)
            if recipe.training_method not in {"lora", "qlora"}:
                return None
            source = node.model_input
            if isinstance(source, NodeOutputRef):
                return self.candidates(source, active)   # a continuation keeps its parent's base and targets
            policy = source.policy
            if policy.adapter is not None and source.initialization == "continue_adapter":
                descriptor = self._artifact(policy.adapter)
                targets = (tuple(sorted(descriptor.adapter.target_modules)) if descriptor and descriptor.adapter
                           else None)
            else:
                targets = self._targets(recipe, policy.base)
            return frozenset({(policy.base.model_dump_json(), targets)})
        if isinstance(node, MergeNode):
            inputs = [self.candidates(value, active) for value in node.models]
            shared = _shared_candidates(inputs) if all(inputs) else None
            return shared or None   # an empty set is this merge's own refusal, never reported again downstream
        if isinstance(node, SelectNode):
            offered = [self.candidates(self.nodes[ref.node_id].model, active) for ref in node.evaluations
                       if isinstance(self.nodes.get(ref.node_id), EvaluateNode)]
            return frozenset().union(*offered) if offered and all(offered) else None
        return None

    def inputs_differ(self, node: MergeNode) -> bool:
        """True only when every input's candidates are known and no single base and target set fits them all."""
        inputs = [self.candidates(value) for value in node.models]
        return all(inputs) and not _shared_candidates(inputs)


def _shared_candidates(inputs) -> frozenset:
    """The candidates every input can share: a common base, and targets no two known values contradict (an input
    whose targets are unknown fits any)."""
    shared = set()
    for base in frozenset.intersection(*(frozenset(item for item, _ in options) for options in inputs)):
        targets = [frozenset(value for item, value in options if item == base) for options in inputs]
        known = [values for values in targets if None not in values]
        if not known:
            shared.add((base, None))
        else:
            shared.update((base, value) for value in frozenset.intersection(*known))
    return frozenset(shared)


def _project_root():
    """Where the GPU locks live: ``sys._MEIPASS/assets`` in the frozen sidecar (as ``manifest.resolve`` and
    ``desktop.assets_root`` resolve it), else the source tree (D18)."""
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"
    return Path(__file__).resolve().parents[2]


def _runtime(profile):
    details = engine_profile("ppo" if profile == "ppo" else "sft")
    lock = _project_root() / details["lock_file"]
    return RuntimeProfile(profile=profile, engine_version=details["engine_version"], dependency_sha256=digest(lock.read_bytes()))


def _roots_for(node, nodes, artifacts, cache=None, seen=None):
    """Collect every permitted root, including all conditional selection winners."""
    cache = {} if cache is None else cache
    seen = set() if seen is None else seen
    if node.id in seen:
        raise ValueError("cyclic model lineage")
    if node.id in cache:
        return cache[node.id]
    seen = seen | {node.id}
    values = ((node.model_input,) if isinstance(node, TrainNode) else node.models if isinstance(node, MergeNode)
              else node.evaluations if isinstance(node, SelectNode) else (node.model,))
    roots = {}
    for value in values:
        if isinstance(value, NodeOutputRef):
            candidates = _roots_for(nodes[value.node_id], nodes, artifacts, cache, seen)
        else:
            if isinstance(value, ModelInput):
                value = value.policy.base
            candidates = (value if isinstance(value, HFRootRef) else artifacts[_artifact_key(value)].descriptor.root,)
        roots.update((root.model_dump_json(), root) for root in candidates)
    cache[node.id] = tuple(roots[key] for key in sorted(roots))
    return cache[node.id]


def _config(project, node, recipe, root, context, dataset, resources):
    from .capabilities import model_catalog
    options = recipe_training_options(recipe)
    catalog = next((item for item in model_catalog() if item["id"] == root.repo_id), None)
    template = context.profile.template if context.profile else catalog["template"] if catalog else "qwen"
    return GraphTrainingConfig(ai_id="graph_" + digest(canonical([project.project_id, node.id]))[:24],
        workflow_id=project.project_id, dataset_id=dataset.dataset_id, dataset_version=dataset.version,
        base_model=root.repo_id, model_profile=context.profile, model_revision=root.revision,
        tokenizer_revision=root.revision, template=template,
        resource_profile=resources.facts.legacy_resources(resources.timeout_seconds), **options)


def plan_graph(project_revision: Revision[ProjectSpec], execution_choices: ExecutionChoices,
               resource_snapshots: tuple[ResourceObservation, ...], simulation=True) -> GraphPlan:
    project_revision = Revision[ProjectSpec].model_validate(project_revision.model_dump())
    choices = ExecutionChoices.model_validate(execution_choices.model_dump())
    from pydantic import TypeAdapter
    facts = tuple(TypeAdapter(ResourceObservation).validate_python(item.model_dump()) for item in resource_snapshots)
    _revision(project_revision)
    graph = GraphSpec.model_validate(project_revision.spec.graph)
    issues = _definition_issues(project_revision.spec, graph, choices.recipes, choices.datasets)
    if issues:
        raise ValueError("; ".join(f"{i.code}: {i.message}" for i in issues))
    # A RunPod price is observed live; 0 or a non-finite value would void the budget (SSH operators may price 0).
    if any(isinstance(item, ResourceFacts) and not (math.isfinite(item.total_hourly_cost_usd)
                                                    and item.total_hourly_cost_usd > 0) for item in facts):
        raise ValueError(RUNPOD_PRICE_REQUIRED)
    recipes = _unique(choices.recipes, lambda x: x.revision_sha256, "recipe revision")
    benchmarks = _unique(choices.benchmarks, lambda x: x.revision_sha256, "benchmark revision")
    datasets = _unique(choices.datasets, lambda x: _dataset_key(x.dataset), "dataset snapshot")
    models = _unique(choices.models, lambda x: x.root.model_dump_json(), "model context")
    artifacts = _unique(choices.artifacts, lambda x: _artifact_key(x.model), "artifact snapshot")
    profiles = _unique(choices.executors, lambda x: x.profile_id, "executor profile")
    if not simulation and (choices.tracking_destination is None
                           or any(p.transport_settings_sha256 is None for p in profiles.values())):
        raise ValueError("real planning requires frozen transport and tracking destination revisions")
    resources = _unique(choices.node_resources, lambda x: x.node_id, "node resource choice")
    by_resource = _unique(facts, observation_key, "resource snapshot")
    nodes = {node.id: node for node in graph.nodes}
    bindings = {item.binding_id: item for item in project_revision.spec.datasets}
    roles = {_dataset_key(item.dataset): item.role for item in project_revision.spec.datasets}
    planned = []
    root_cache = {}
    for node in graph.nodes:
        permitted_roots = _roots_for(node, nodes, artifacts, root_cache)
        allowed = []
        if not isinstance(node, SelectNode):
            if len(set(node.executor_profile_ids)) != len(node.executor_profile_ids):
                raise ValueError("duplicate node executor profile")
            choice = resources.get(node.id)
            if choice is None:
                raise ValueError(f"missing resource duration/runtime choice for {node.id}")
            runtime = _runtime(choice.runtime_profile)
            for profile_id in node.executor_profile_ids:
                if profile_id not in profiles or observation_key(profiles[profile_id]) not in by_resource:
                    raise ValueError("missing executor profile or resource facts")
                profile = profiles[profile_id]
                allowed.append(OperationResources(executor=profile, facts=by_resource[observation_key(profile)],
                                                 timeout_seconds=choice.timeout_seconds, runtime=runtime))
        if isinstance(node, TrainNode):
            recipe = _recipe(node, recipes)
            expected = "ppo" if recipe.training_stage == "ppo" else "primary"
            if any(resource.runtime.profile != expected for resource in allowed):
                raise ValueError("recipe runtime differs from chosen runtime")
            for root in permitted_roots:
                context = models.get(root.model_dump_json())
                if context is None:
                    raise ValueError("verified root/tokenizer model context is not ready")
                _config(project_revision.spec, node, recipe, root, context, bindings[node.dataset_binding].dataset, allowed[0])
        if isinstance(node, EvaluateNode):
            if node.benchmark_revision not in benchmarks:
                raise ValueError("missing pinned benchmark revision")
            benchmark = benchmarks[node.benchmark_revision].spec
            check_generation_benchmark(benchmark)   # what the worker would refuse after launch (plan Q8)
            if roles.get(_dataset_key(benchmark.dataset)) not in {"validation", "test"}:
                raise ValueError("evaluation requires a held-out project dataset")
            validate_sample_ids(benchmark, datasets[_dataset_key(benchmark.dataset)])
            if any(benchmark.tokenizer_fingerprint != root.tokenizer_fingerprint for root in permitted_roots):
                raise ValueError("benchmark tokenizer differs from model context")
        if isinstance(node, SelectNode):
            evaluation_nodes = [nodes[ref.node_id] for ref in node.evaluations]
            if len({item.benchmark_revision for item in evaluation_nodes}) != 1:
                raise ValueError("selection candidates must use the same benchmark")
            for evaluation in evaluation_nodes:
                benchmark = benchmarks[evaluation.benchmark_revision].spec
                if roles.get(_dataset_key(benchmark.dataset)) != "validation":
                    raise ValueError("held-out test evaluation cannot influence selection")
        _static_routes(node, nodes, recipes, artifacts, allowed, choices.base_targets)
        attempts = graph.retry_policy.max_attempts
        planned.append(NodePlan(node=node, inputs=tuple(InputContract(source=ref, required_port=port)
            for ref, port in node_refs(node)), permitted_model_roots=permitted_roots, allowed_resources=tuple(allowed),
            max_estimated_cost_usd=max((r.facts.cost(r.timeout_seconds) for r in allowed), default=0) * attempts,
            max_duration_seconds=max((r.timeout_seconds for r in allowed), default=0) * attempts))
    total = sum(item.max_estimated_cost_usd for item in planned)
    if total > graph.max_total_cost_usd:
        raise ValueError("whole graph including retries exceeds maximum total cost")
    used_resources = {}
    for node in planned:
        for resource in node.allowed_resources:
            key = resource.facts.exclusivity_key
            used_resources[key] = max(used_resources.get(key, 0), resource.facts.gpu_count)
    body = dict(project_revision=project_revision, definition_sha256=digest(canonical(graph.model_dump(mode="json"))),
                graph=graph, execution_choices=choices, resource_snapshots=facts, nodes=tuple(planned),
                max_estimated_cost_usd=total, max_duration_seconds=sum(item.max_duration_seconds for item in planned),
                max_concurrent_gpu_count=sum(sorted(used_resources.values(), reverse=True)[:graph.max_concurrent]),
                simulation=simulation)
    # Serialize through the schema before hashing to normalize datetime/tuple fields.
    result = GraphPlan(**body, sha256="0" * 64)
    return GraphPlan(**body, sha256=digest(canonical(result.model_dump(mode="json", exclude={"sha256"}))))


def _check_plan(plan):
    checked = GraphPlan.model_validate(plan.model_dump())
    if digest(canonical(checked.model_dump(mode="json", exclude={"sha256"}))) != checked.sha256:
        raise ValueError("plan hash mismatch")
    return checked


def _static_routes(node, nodes, recipes, artifacts, resources, evidence=()):
    """Reject known impossible routes; unknown actual tensor contracts stay conditional."""
    if isinstance(node, SelectNode):
        return
    destination = resources[0].runtime.profile
    values = (node.model_input, node.reward_binding) if isinstance(node, TrainNode) else (
        node.models if isinstance(node, MergeNode) else (node.model,))

    def selected_sources(value):
        if isinstance(value, NodeOutputRef) and isinstance(nodes[value.node_id], SelectNode):
            return tuple(source for evaluation in nodes[value.node_id].evaluations
                         for source in selected_sources(nodes[evaluation.node_id].model))
        return (value,)

    if isinstance(node, TrainNode) and _recipe(node, recipes).training_stage == "ppo" and any(
            _continues_adapter(source, nodes, recipes, artifacts) for source in selected_sources(node.model_input)):
        raise ValueError(PPO_CONTINUATION)
    values = tuple(source for value in values for source in selected_sources(value))
    for value in values:
        if value is None:
            continue
        if isinstance(value, NodeOutputRef):
            parent = nodes[value.node_id]
            if isinstance(parent, TrainNode):
                parent_recipe = _recipe(parent, recipes)
                runtime = "ppo" if parent_recipe.training_stage == "ppo" else "primary"
                if runtime != destination:
                    raise ValueError("unsupported primary/legacy PPO artifact route")
                if parent_recipe.training_method == "oft":
                    raise ValueError("OFT artifact handoff/evaluation/merge/materialize is unsupported")
                if isinstance(node, TrainNode):
                    child_recipe = _recipe(node, recipes)
                    if _requires_materialization(parent_recipe, child_recipe):
                        raise ValueError("parent transition requires an explicit materialize node")
                if isinstance(node, MergeNode) and (parent_recipe.training_method not in {"lora", "qlora"}
                        or parent_recipe.training_stage in {"rm", "ppo"}):
                    raise ValueError("merge requires plain causal LoRA/QLoRA parents")
            elif isinstance(parent, (MaterializeNode, MergeNode)) and destination != "primary":
                raise ValueError("unsupported primary/legacy PPO artifact route")
        else:
            loads = (value.policy, value.frozen_reference, value.reward_model) if isinstance(value, ModelInput) else (
                (value,) if isinstance(value, ModelLoadRef) else ())
            refs = [ref for load in loads if load for ref in (load.base, load.adapter) if isinstance(ref, ModelArtifactRef)]
            if isinstance(value, ModelArtifactRef):
                refs.append(value)
            for ref in refs:
                snapshot = artifacts.get(_artifact_key(ref))
                if snapshot is None:
                    raise ValueError("external artifact producer/inventory snapshot is not ready")
                if snapshot.producer.runtime_profile != destination:
                    raise ValueError("unsupported primary/legacy PPO artifact route")
                if snapshot.descriptor.adapter and snapshot.descriptor.adapter.method == "oft":
                    raise ValueError("OFT artifact handoff is unsupported")
                if isinstance(node, MaterializeNode):
                    check_materializable(snapshot.descriptor)
    if isinstance(node, MergeNode):
        if all(isinstance(value, ModelArtifactRef) for value in node.models):
            _merge_descriptors(node.models, tuple(artifacts[_artifact_key(ref)].descriptor for ref in node.models))
        elif _MergeLineage(nodes, recipes, artifacts, evidence).inputs_differ(node):
            raise ValueError(MERGE_INPUTS_DIFFER)   # artifact adapters or module evidence show it at planning
        ranks = []
        for value in node.models:
            if isinstance(value, NodeOutputRef) and isinstance(nodes[value.node_id], TrainNode):
                recipe = _recipe(nodes[value.node_id], recipes)
                options = recipe.finetuning_options
                if any((options.use_dora, options.use_rslora, options.pissa_init, options.pissa_convert)):
                    raise ValueError("unsupported adapter merge variant")
                ranks.append(recipe.hyperparameters.lora_rank)
            elif isinstance(value, ModelArtifactRef):
                descriptor = artifacts[_artifact_key(value)].descriptor
                check_materializable(descriptor)
                ranks.append(descriptor.adapter.rank)
        if sum(ranks) > 256:
            raise ValueError("merged rank exceeds 256")


def _continues_adapter(source, nodes, recipes, artifacts):
    """Whether a policy source (already resolved through selections) compiles to continue_adapter."""
    if isinstance(source, ModelInput):
        return source.initialization == "continue_adapter"
    if isinstance(source, NodeOutputRef):
        parent = nodes[source.node_id]
        return isinstance(parent, MergeNode) or isinstance(parent, TrainNode) and _recipe(
            parent, recipes).training_method in {"lora", "qlora", "oft"}
    snapshot = artifacts.get(_artifact_key(source)) if isinstance(source, ModelArtifactRef) else None
    return snapshot is not None and snapshot.descriptor.storage_kind == "adapter"


class NodeModelBinding(StrictModel):
    node_id: LocalId
    model: ModelArtifactRef
    selection: SelectionResult | None = None


class NodeExecutionEvidence(StrictModel):
    node_id: LocalId
    plan_sha256: Sha256
    job_id: LocalId
    attempt_id: LocalId
    manifest_sha256: Sha256
    status: Literal["completed", "failed", "cancelled", "skipped"]
    tracking_acknowledged: bool = Field(strict=True)


class NodeEvaluationBinding(StrictModel):
    node_id: LocalId
    evaluation: EvaluationRecord
    tracking_acknowledged: bool = Field(strict=True)
    producer: NodeExecutionEvidence | None = None


@dataclass(frozen=True)
class VerifiedBindings:
    """Trusted controller inputs, not a public request deserializer.

    Producer evidence must come from the persistent job/attempt ledger. The
    compiler compares it with verified descriptor bytes but cannot itself prove
    an arbitrary caller's claim of job completion. Worker repeats inventory checks.
    """
    registry: Registry
    artifact_store: ModelArtifactStore
    destination_profile: PublicExecutorProfile | None = None
    outputs: tuple[NodeModelBinding, ...] = ()
    producers: tuple[ProducerEvidence, ...] = ()
    evaluations: tuple[NodeEvaluationBinding, ...] = ()
    failed_parent_node_ids: tuple[str, ...] = ()
    base_targets: tuple[ModelTargetEvidence, ...] = ()
    tracking_destination: TrackingDestination | None = None
    completed_nodes: tuple[NodeExecutionEvidence, ...] = ()


def compile_node(plan: GraphPlan, node_id: str, verified_bindings: VerifiedBindings):
    plan = _check_plan(plan)
    if not isinstance(verified_bindings, VerifiedBindings):
        raise TypeError("compiler requires typed VerifiedBindings from the controller")
    bindings = verified_bindings
    if not plan.simulation and bindings.tracking_destination != plan.execution_choices.tracking_destination:
        raise ValueError("tracking destination differs from pinned plan")
    entry = next((item for item in plan.nodes if item.node.id == node_id), None)
    if entry is None:
        raise ValueError("unknown plan node")
    node = entry.node
    failed = set(bindings.failed_parent_node_ids)
    graph_nodes = {item.id: item for item in plan.graph.nodes}
    if not isinstance(node, SelectNode):
        # Failed evaluations may belong to any of the node's selection parents; each parent is recompiled with
        # only its own share below (8.2 review I-1).
        selections = [graph_nodes[ref.node_id] for ref, _ in node_refs(node)
                      if isinstance(graph_nodes[ref.node_id], SelectNode)]
        if failed - {ref.node_id for select in selections for ref in select.evaluations}:
            raise ValueError("unrequested failed evaluation parent")
    completed = _unique(bindings.completed_nodes, lambda item: item.node_id, "node completion evidence")
    for edge in plan.graph.depends_on:
        if edge.node_id == node_id:
            evidence = completed.get(edge.depends_on)
            if (evidence is None or evidence.plan_sha256 != plan.sha256 or evidence.status != "completed"
                    or not evidence.tracking_acknowledged):
                raise ValueError("explicit dependency completion/tracking evidence is not ready")
    choices = plan.execution_choices
    outputs = _unique(bindings.outputs, lambda item: item.node_id, "node output")
    producers = _unique(bindings.producers, lambda item: _artifact_key(item.model), "producer evidence")
    resources = next((r for r in entry.allowed_resources if r.executor == bindings.destination_profile), None)
    if not isinstance(node, SelectNode) and resources is None:
        raise ValueError("destination profile differs from approved plan")
    lineage = OperationLineage(project_id=plan.project_revision.spec.project_id,
        project_revision=plan.project_revision.revision_sha256, node_id=node.id,
        plan_sha256=plan.sha256, simulation=plan.simulation)

    def resolve(value):
        if isinstance(value, NodeOutputRef):
            if value.output_name != "model" or value.node_id not in outputs:
                raise ValueError("conditional model input is not ready")
            ref = outputs[value.node_id].model
            parent = next(item for item in plan.graph.nodes if item.id == value.node_id)
            if isinstance(parent, SelectNode):
                select_lineage = lineage.model_copy(update={"node_id": parent.id})
                share = failed & {ref.node_id for ref in parent.evaluations}
                local = _compile_select(plan, parent, bindings, select_lineage, outputs, verified, share)
                receipt = resolve_local_selection(local)
                if (receipt.status != "selected" or receipt.winner != ref
                        or outputs[value.node_id].selection != receipt):
                    raise ValueError("selection output differs from verified evaluation receipt")
                return ref
            producer = producers.get(_artifact_key(ref))
            if producer is None or (producer.plan_sha256, producer.node_id) != (plan.sha256, value.node_id):
                raise ValueError("symbolic output producer differs from planned node/attempt")
            descriptor = verified(ref)
            if isinstance(parent, TrainNode):
                recipe = _recipe(parent, {item.revision_sha256: item for item in choices.recipes})
                expected_storage = "adapter" if recipe.training_method in {"lora", "qlora", "oft"} else "full_model"
                if (descriptor.storage_kind != expected_storage
                        or producer.training_stage != recipe.training_stage
                        or descriptor.adapter and descriptor.adapter.method != recipe.training_method
                        or descriptor.semantic_kind != ("reward_model" if recipe.training_stage == "rm" else "causal_lm")):
                    raise ValueError("actual parent output violates planned training contract")
            elif isinstance(parent, MaterializeNode):
                if descriptor.storage_kind != "full_model" or descriptor.quantization_bit is not None:
                    raise ValueError("materialize output must contain full unquantized weights")
            elif isinstance(parent, MergeNode):
                check_materializable(descriptor)
            return ref
        return value

    def verified(ref):
        descriptor = bindings.artifact_store.verify(ref)
        producer = producers.get(_artifact_key(ref))
        if producer is None:
            raise ValueError("completed producer evidence is required")
        _check_producer(ref, descriptor, producer)
        if not plan.simulation and not descriptor.eligible_for_real_use:
            raise ValueError("synthetic/simulation model is ineligible for real execution")
        if resources and producer.runtime_profile != resources.runtime.profile:
            raise ValueError("unsupported primary/legacy PPO artifact route")
        if descriptor.adapter and descriptor.adapter.method == "oft":
            raise ValueError("OFT artifact handoff is unsupported")
        for parent in descriptor.parents:
            verified(parent)
        return descriptor

    if isinstance(node, SelectNode):
        return _compile_select(plan, node, bindings, lineage, outputs, verified, failed)
    if isinstance(node, TrainNode):
        recipes = {item.revision_sha256: item for item in choices.recipes}
        recipe = _recipe(node, recipes)
        source = node.model_input
        if isinstance(source, NodeOutputRef):
            ref = resolve(source)
            descriptor = verified(ref)
            if descriptor.storage_kind == "adapter":
                model_input = ModelInput(policy=ModelLoadRef(base=descriptor.effective_base, adapter=ref),
                                         initialization="continue_adapter")
            else:
                model_input = ModelInput(policy=ModelLoadRef(base=ref), initialization=(
                    "full_weights" if recipe.training_method in {"full", "freeze"} else "new_adapter"))
        else:
            model_input = ModelInput.model_validate(source.model_dump())
        if recipe.training_stage == "ppo" and model_input.initialization == "continue_adapter":
            raise ValueError(PPO_CONTINUATION)
        if node.reward_binding:
            reward = node.reward_binding
            if isinstance(reward, NodeOutputRef):
                ref = resolve(reward)
                descriptor = verified(ref)
                reward = ModelLoadRef(base=descriptor.effective_base, adapter=ref) if descriptor.adapter else ModelLoadRef(base=ref)
            if model_input.reward_model is not None and model_input.reward_model != reward:
                raise ValueError("conflicting typed reward bindings")
            model_input = ModelInput.model_validate({**model_input.model_dump(), "reward_model": reward})
        if requires_frozen_reference(recipe):
            if model_input.frozen_reference is not None and model_input.frozen_reference != model_input.policy:
                raise ValueError("frozen reference must be the exact policy parent")
            model_input = ModelInput.model_validate({**model_input.model_dump(), "frozen_reference": model_input.policy})
        elif model_input.frozen_reference is not None:
            raise ValueError("reference-free recipe cannot bind a frozen reference")
        for load in (model_input.policy, model_input.frozen_reference, model_input.reward_model):
            if load:
                for ref in (load.base, load.adapter):
                    if isinstance(ref, ModelArtifactRef):
                        verified(ref)
        bindings.artifact_store.validate_input(model_input, require_real=not plan.simulation)
        _training_parent(recipe, model_input, verified, producers, bindings.base_targets)
        root = (model_input.policy.base if isinstance(model_input.policy.base, HFRootRef)
                else verified(model_input.policy.base).root)
        contexts = [item for item in choices.models if item.root == root]
        if len(contexts) != 1:
            raise ValueError("actual model root differs from pinned model context")
        if root not in entry.permitted_model_roots:
            raise ValueError("actual model root differs from planned input lineage")
        dataset_binding = next(item for item in plan.project_revision.spec.datasets if item.binding_id == node.dataset_binding)
        actual = snapshot_dataset(bindings.registry, dataset_binding.dataset)
        if actual not in choices.datasets:
            raise ValueError("dataset evidence changed after planning")
        heldout = [item.dataset for item in plan.project_revision.spec.datasets if item.role != "train"]
        assert_no_input_overlap([dataset_binding.dataset], heldout, registry=bindings.registry)
        config = _config(plan.project_revision.spec, node, recipe, root, contexts[0], dataset_binding.dataset, resources)
        return TrainOperation(config=config, model_input=model_input, dataset=dataset_binding.dataset,
                              resources=resources, lineage=lineage)
    if isinstance(node, EvaluateNode):
        ref = resolve(node.model)
        descriptor = verified(ref)
        benchmark = next(item.spec for item in choices.benchmarks if item.revision_sha256 == node.benchmark_revision)
        actual = snapshot_dataset(bindings.registry, benchmark.dataset)
        if actual not in choices.datasets:
            raise ValueError("benchmark dataset evidence changed after planning")
        validate_sample_ids(benchmark, actual)
        if (descriptor.tokenizer_fingerprint != benchmark.tokenizer_fingerprint
                or descriptor.semantic_kind != benchmark.semantic_model_kind):
            raise ValueError("actual model does not satisfy benchmark input contract")
        return EvaluateOperation(model=ref, benchmark=benchmark, resources=resources, lineage=lineage)
    if isinstance(node, MergeNode):
        refs = tuple(resolve(value) for value in node.models)
        descriptors = tuple(verified(ref) for ref in refs)
        _merge_descriptors(refs, descriptors)
        for descriptor in descriptors:
            _conversion_base_evidence(descriptor, bindings.base_targets, verified)
        return MergeOperation(models=refs, weights=node.weights, resources=resources, lineage=lineage)
    ref = resolve(node.model)
    descriptor = verified(ref)
    check_materializable(descriptor)
    _conversion_base_evidence(descriptor, bindings.base_targets, verified)
    return MaterializeOperation(model=ref, resources=resources, lineage=lineage)


def _training_parent(recipe, model_input, verified, producers, target_evidence=()):
    from .model_ops import CONTINUATION_REFUSAL, continuation_mismatches
    policy = model_input.policy
    if policy.adapter:
        descriptor = verified(policy.adapter)
        adapter_tensor_shapes(descriptor)
        parent_stage = producers[_artifact_key(policy.adapter)].training_stage
        if parent_stage is None:
            raise ValueError("parent training stage evidence is required")
        if parent_stage == "sft" and recipe.training_stage in {"dpo", "kto"}:
            raise ValueError("SFT to preference training requires explicit materialize")
        evidence = next((item for item in target_evidence if item.base == policy.base), None)
        expected = _expected_adapter(recipe, descriptor.adapter.target_modules, evidence)
        # PEFT exports always record task_type/inference_mode (plan F7, F10); they are save state, not settings.
        mismatches = continuation_mismatches(descriptor.adapter, expected)
        if mismatches:
            raise ValueError(f"{CONTINUATION_REFUSAL}: {', '.join(mismatches)}")
        if evidence:
            _verify_target_dimensions(evidence, descriptor, verified)
    elif model_input.initialization != ("full_weights" if recipe.training_method in {"full", "freeze"} else "new_adapter"):
        raise ValueError("initialization differs from requested training method")
    reward = model_input.reward_model
    if reward and reward.adapter and reward.base != policy.base:
        raise ValueError("LoRA reward requires the exact policy effective base")


def _expected_adapter(recipe, actual_targets, evidence=None):
    from .model_ops import check_continuation_targets, expected_continuation_adapter
    if recipe.training_method not in {"lora", "qlora"}:
        raise ValueError("adapter continuation requires LoRA/QLoRA")
    if evidence is None:
        raise ValueError("adapter continuation requires inspected effective-base target/dimension evidence")
    check_continuation_targets(recipe.hyperparameters.lora_target, actual_targets, evidence)
    return expected_continuation_adapter(recipe, actual_targets)


def _verify_target_dimensions(evidence, descriptor, verified):
    if isinstance(evidence.base, ModelArtifactRef):
        base = verified(evidence.base)
        weights = {tensor.name: tensor.shape for tensor in base.tensors}
        for module in evidence.modules:
            if weights.get(module.name + ".weight") != (module.output_dimension, module.input_dimension):
                raise ValueError("inspected base dimensions differ from verified tensor inventory")
    for module in evidence.modules:
        if not any(module.name == target or module.name.endswith("." + target)
                   for target in descriptor.adapter.target_modules):
            continue
        tensors = [tensor for tensor in descriptor.tensors if (module.name + ".lora_") in tensor.name]
        if len(tensors) != 2 or not any("lora_A" in t.name for t in tensors) or not any("lora_B" in t.name for t in tensors):
            raise ValueError("adapter lacks complete inspected module A/B tensor inventory")
        if any(len(t.shape) != 2 or ("lora_A" in t.name and t.shape[1] != module.input_dimension)
               or ("lora_B" in t.name and t.shape[0] != module.output_dimension) for t in tensors):
            raise ValueError("adapter dimensions differ from inspected effective base")


def _conversion_base_evidence(descriptor, evidence_values, verified):
    matches = [item for item in evidence_values if item.base == descriptor.effective_base]
    if len(matches) != 1:
        raise ValueError("conversion requires inspected effective-base target/dimension evidence")
    evidence = matches[0]
    pairs = adapter_tensor_shapes(descriptor)
    for module in pairs:
        if not any(module == item.name or module.endswith("." + item.name) for item in evidence.modules):
            raise ValueError("adapter targets are absent from inspected base module inventory")
    if isinstance(evidence.base, ModelArtifactRef) and verified(evidence.base).quantization_bit is not None:
        raise ValueError("materialization requires unquantized effective base weights")
    _verify_target_dimensions(evidence, descriptor, verified)


# -- adapter conversion rules (public: shared by planning, model_ops and the worker) ---------------

def check_materializable(descriptor):
    """A plain causal LoRA/QLoRA adapter of Linear (out, in) weights that merge/materialize can convert."""
    adapter = descriptor.adapter
    if not adapter or adapter.method not in {"lora", "qlora"} or descriptor.has_value_head:
        raise ValueError("materialize requires a causal LoRA/QLoRA adapter without value head")
    # fan_in_fan_out (Conv1D (in, out) layout) is refused like the worker refuses it: delta = B @ A only.
    if (adapter.use_dora or adapter.use_rslora or adapter.pissa_init or adapter.pissa_convert
            or adapter.fan_in_fan_out or adapter.bias != "none" or adapter.modules_to_save or adapter.rank_pattern
            or adapter.alpha_pattern or adapter.target_parameters or adapter.layer_replication):
        raise ValueError("unsupported adapter conversion variant")
    adapter_tensor_shapes(descriptor)


def adapter_tensor_shapes(descriptor):
    pairs = {}
    rank = descriptor.adapter.rank
    for tensor in descriptor.tensors:
        match = re.fullmatch(r"(.+)\.lora_([AB])(?:\.[A-Za-z0-9_]+)?\.weight", tensor.name)
        if match is None or len(tensor.shape) != 2:
            raise ValueError("adapter requires a complete LoRA A/B tensor inventory")
        module, letter = match.groups()
        if tensor.shape[0 if letter == "A" else 1] != rank:
            raise ValueError("adapter rank differs from actual tensor dimensions")
        if letter in pairs.setdefault(module, {}):
            raise ValueError("duplicate adapter tensor role")
        pairs[module][letter] = tensor
    if not pairs or any(set(pair) != {"A", "B"} for pair in pairs.values()):
        raise ValueError("adapter is missing paired A/B tensors")
    targets = descriptor.adapter.target_modules
    if (any(not any(module == target or module.endswith("." + target) for target in targets) for module in pairs)
            or any(not any(module == target or module.endswith("." + target) for module in pairs) for target in targets)):
        raise ValueError("adapter tensor modules differ from resolved target modules")
    return pairs


def check_merge_descriptors(refs, descriptors, *, require_real=True):
    """Merge conversion rules; only the real-only rule is relaxable (synthetic CPU acceptance fixtures)."""
    if len({_artifact_key(ref) for ref in refs}) != len(refs):
        raise ValueError("merge requires distinct actual artifacts")
    for descriptor in descriptors:
        check_materializable(descriptor)
        if require_real and not descriptor.eligible_for_real_use:
            raise ValueError("merge requires real completed artifacts")
    if sum(item.adapter.rank for item in descriptors) > 256:
        raise ValueError("merged rank exceeds 256")
    first = descriptors[0]
    for item in descriptors[1:]:
        if (item.effective_base, item.config_sha256, item.tokenizer_fingerprint, item.adapter.target_modules,
                item.adapter.fan_in_fan_out) != (
                first.effective_base, first.config_sha256, first.tokenizer_fingerprint, first.adapter.target_modules,
                first.adapter.fan_in_fan_out):
            raise ValueError(MERGE_INPUTS_DIFFER)
        def dimensions(descriptor):
            result = {}
            for tensor in descriptor.tensors:
                if len(tensor.shape) != 2 or not ("lora_A" in tensor.name or "lora_B" in tensor.name):
                    raise ValueError("merge requires resolved LoRA A/B tensor inventory")
                result[tensor.name] = (tensor.shape[1] if "lora_A" in tensor.name else tensor.shape[0], tensor.dtype)
            return result
        if dimensions(item) != dimensions(first):
            raise ValueError("merge base tensor dimensions differ")


def _merge_descriptors(refs, descriptors):
    """Graph compilation and planning: always real-only."""
    check_merge_descriptors(refs, descriptors, require_real=True)


def _compile_select(plan, node, bindings, lineage, outputs, verified, failed):
    """``failed``: the failed evaluation parents of this selection only (a child of several selections passes
    each its own share)."""
    records = _unique(bindings.evaluations, lambda value: value.node_id, "evaluation binding")
    nodes = {item.id: item for item in plan.graph.nodes}
    evaluations, requested, merged = [], [], []
    failed = set(failed)
    allowed_failed = {ref.node_id for ref in node.evaluations}
    if failed - allowed_failed:
        raise ValueError("unrequested failed evaluation parent")
    benchmark_sha = None
    for ref in node.evaluations:
        parent = nodes[ref.node_id]
        benchmark_sha = parent.benchmark_revision
        if ref.node_id in failed:
            if not node.selection.allow_partial:
                raise ValueError("partial selection is not allowed")
            continue
        item = records.get(ref.node_id)
        if item is None or not item.tracking_acknowledged:
            raise ValueError("conditional evaluation input is not ready")
        producer = item.producer
        if (producer is None or producer.plan_sha256 != plan.sha256 or producer.node_id != ref.node_id
                or producer.attempt_id != item.evaluation.producer_attempt_id
                or producer.status != item.evaluation.status or not producer.tracking_acknowledged):
            raise ValueError("evaluation producer attempt evidence differs from planned node")
        expected = parent.model
        if isinstance(expected, NodeOutputRef):
            if expected.node_id not in outputs:
                raise ValueError("evaluation model output is not ready")
            expected = outputs[expected.node_id].model
            if isinstance(nodes[parent.model.node_id], MergeNode):
                merged.append(expected)
        if item.evaluation.model != expected or item.evaluation.benchmark_sha256 != benchmark_sha:
            raise ValueError("evaluation binding differs from pinned model/benchmark")
        verified(expected)
        requested.append(expected)
        evaluations.append(item.evaluation)
    # Too few survivors is not a compile error (Task 8.2): resolve_local_selection applies the same min_success
    # rule to the quality-eligible candidates and records a no-winner receipt (insufficient_partial_success).
    return LocalSelectSpec(lineage=lineage, evaluations=tuple(evaluations), requested_models=tuple(requested),
                           failed_parent_node_ids=tuple(sorted(failed)), selection=node.selection,
                           benchmark_sha256=benchmark_sha, merged_models=tuple(merged))
