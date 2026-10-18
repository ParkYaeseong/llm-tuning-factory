"""Strict executable graphs, separate from the project's incomplete JSON draft."""
from typing import Annotated, Literal

from pydantic import Field, model_validator

from .benchmarks import SelectionSpec
from .config import FinetuningOptions, Hyperparameters, StrictModel
from .model_artifacts import ModelArtifactRef, ModelInput, ModelLoadRef
from .projects import LocalId, Sha256
from .recipes import RecipeStageOptions


class Issue(StrictModel):
    code: str
    message: str
    node_id: str | None = None


class NodeOutputRef(StrictModel):
    node_id: LocalId
    output_name: Literal["model", "evaluation"]


class RecipeOverrides(StrictModel):
    hyperparameters: Hyperparameters | None = None
    finetuning_options: FinetuningOptions | None = None
    stage_options: RecipeStageOptions | None = None


class _Node(StrictModel):
    id: LocalId
    label: str = Field(min_length=1, max_length=200)


class TrainNode(_Node):
    kind: Literal["train"] = "train"
    recipe_revision: Sha256
    overrides: RecipeOverrides = Field(default_factory=RecipeOverrides)
    model_input: ModelInput | NodeOutputRef
    dataset_binding: LocalId
    reward_binding: ModelLoadRef | NodeOutputRef | None = None
    executor_profile_ids: tuple[LocalId, ...] = Field(min_length=1)


class EvaluateNode(_Node):
    kind: Literal["evaluate"] = "evaluate"
    model: ModelArtifactRef | NodeOutputRef
    benchmark_revision: Sha256
    executor_profile_ids: tuple[LocalId, ...] = Field(min_length=1)


class SelectNode(_Node):
    kind: Literal["select"] = "select"
    evaluations: tuple[NodeOutputRef, ...] = Field(min_length=1)
    selection: SelectionSpec


class MergeNode(_Node):
    kind: Literal["merge"] = "merge"
    models: tuple[ModelArtifactRef | NodeOutputRef, ...] = Field(min_length=2, max_length=4)
    weights: tuple[float, ...]
    executor_profile_ids: tuple[LocalId, ...] = Field(min_length=1)

    @model_validator(mode="after")
    def valid_weights(self):
        from fractions import Fraction
        if len(self.weights) != len(self.models) or any(weight <= 0 for weight in self.weights):
            raise ValueError("one positive finite weight is required for each model")
        if sum(Fraction(str(weight)) for weight in self.weights) != 1:
            raise ValueError("weights must sum to one")
        keys = [item.model_dump_json() for item in self.models]
        if len(set(keys)) != len(keys):
            raise ValueError("merge models must be distinct")
        return self


class MaterializeNode(_Node):
    kind: Literal["materialize"] = "materialize"
    model: ModelArtifactRef | NodeOutputRef
    executor_profile_ids: tuple[LocalId, ...] = Field(min_length=1)


Node = Annotated[TrainNode | EvaluateNode | SelectNode | MergeNode | MaterializeNode, Field(discriminator="kind")]


class Dependency(StrictModel):
    node_id: LocalId
    depends_on: LocalId


class RetryPolicy(StrictModel):
    max_attempts: int = Field(default=1, strict=True, ge=1, le=5)
    backoff_seconds: int = Field(default=0, strict=True, ge=0, le=3600)


class GraphSpec(StrictModel):
    nodes: tuple[Node, ...] = Field(min_length=1, max_length=64)
    depends_on: tuple[Dependency, ...] = ()
    max_concurrent: int = Field(default=1, strict=True, ge=1, le=64)
    max_total_cost_usd: float = Field(default=100, gt=0)
    retry_policy: RetryPolicy = Field(default_factory=RetryPolicy)


def node_refs(node):
    """Return (symbolic ref, required port) pairs, including reward dependencies."""
    values = ((node.model_input, node.reward_binding) if isinstance(node, TrainNode)
              else node.evaluations if isinstance(node, SelectNode)
              else node.models if isinstance(node, MergeNode) else (node.model,))
    return tuple((value, "evaluation" if isinstance(node, SelectNode) else "model")
                 for value in values if isinstance(value, NodeOutputRef))


def graph_edges(graph):
    return {(edge.depends_on, edge.node_id) for edge in graph.depends_on} | {
        (ref.node_id, node.id) for node in graph.nodes for ref, _ in node_refs(node)}


def graph_issues(graph: GraphSpec):
    issues = []
    nodes = {node.id: node for node in graph.nodes}
    if len(nodes) != len(graph.nodes):
        issues.append(Issue(code="duplicate_node", message="node IDs must be unique"))
    edges = graph_edges(graph)
    if len(edges) > 256 or len(graph.depends_on) > 256:
        issues.append(Issue(code="edge_limit", message="graph exceeds 256 edges"))
    for parent, child in sorted(edges):
        if parent not in nodes or child not in nodes:
            issues.append(Issue(code="missing_node", message="dependency refers to a missing node", node_id=child))
    for node in graph.nodes:
        for ref, required in node_refs(node):
            parent = nodes.get(ref.node_id)
            provided = "evaluation" if isinstance(parent, EvaluateNode) else "model"
            if ref.output_name != required or parent is not None and provided != required:
                issues.append(Issue(code="typed_port", message=f"input requires {required} output", node_id=node.id))
    remaining = set(nodes)
    while remaining:
        ready = {node for node in remaining if not any(b == node and a in remaining for a, b in edges)}
        if not ready:
            issues.append(Issue(code="cycle", message="graph contains a cycle"))
            break
        remaining -= ready
    return issues


def validate_graph(project, recipes, registry):
    """Validate a saved draft against pinned recipes and local registered bytes."""
    from .graph_planner import _definition_issues, snapshot_dataset
    try:
        graph = GraphSpec.model_validate(project.graph)
    except ValueError as exc:
        return [Issue(code="graph_schema", message=str(exc))]
    try:
        datasets = tuple(snapshot_dataset(registry, binding.dataset) for binding in project.datasets)
        return _definition_issues(project, graph, recipes, datasets)
    except (ValueError, OSError) as exc:
        return [Issue(code="dataset", message=str(exc))]
