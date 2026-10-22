import pytest

from tuning_factory.graph_schema import GraphSpec, graph_issues


def materialize(id, parent=None):
    return {"kind": "materialize", "id": id, "label": id, "model": {
        "node_id": parent or id, "output_name": "model"}, "executor_profile_ids": ["pod"]}


def test_cycle_missing_duplicate_and_wrong_port_are_rejected():
    for nodes, code in [([materialize("a", "b"), materialize("b", "a")], "cycle"),
                        ([materialize("a", "missing")], "missing_node"),
                        ([materialize("a"), materialize("a")], "duplicate_node")]:
        assert code in {issue.code for issue in graph_issues(GraphSpec(nodes=nodes))}
    node = materialize("a", "b")
    node["model"]["output_name"] = "evaluation"
    assert "typed_port" in {i.code for i in graph_issues(GraphSpec(nodes=[node, materialize("b")]))}


def test_select_requires_evaluation_port_and_at_least_one_candidate():
    with pytest.raises(ValueError):
        GraphSpec(nodes=[{"kind": "select", "id": "s", "label": "s", "evaluations": [],
                          "selection": {"metric": "accuracy", "direction": "max"}}])
    graph = GraphSpec(nodes=[materialize("a"), {"kind": "select", "id": "s", "label": "s",
        "evaluations": [{"node_id": "a", "output_name": "model"}],
        "selection": {"metric": "accuracy", "direction": "max"}}])
    assert "typed_port" in {issue.code for issue in graph_issues(graph)}


def test_graph_limits_cover_explicit_and_implicit_edges():
    with pytest.raises(ValueError):
        GraphSpec(nodes=[materialize(str(i)) for i in range(65)])
    graph = GraphSpec(nodes=[materialize(str(i), str((i+1) % 64)) for i in range(64)],
                      depends_on=[{"node_id": str(i // 64), "depends_on": str(i % 64)} for i in range(257)])
    assert "edge_limit" in {issue.code for issue in graph_issues(graph)}


def test_adapter_method_change_still_requires_materialization(tmp_path):
    from test_graph_planner import fixture, revise

    from tuning_factory.graph_planner import NodeResources, plan_graph
    from tuning_factory.graph_schema import validate_graph
    from tuning_factory.projects import Revision
    from tuning_factory.recipes import RecipeSpec
    from tuning_factory.registry import canonical, digest

    registry, project, choices, facts = fixture(tmp_path)
    child = RecipeSpec(recipe_id="child", name="Child", training_method="qlora")
    revision = Revision(spec=child, revision_sha256=digest(canonical(child.model_dump(mode="json"))))
    graph = dict(project.spec.graph)
    graph["nodes"] = [*graph["nodes"], {**graph["nodes"][0], "id": "child", "recipe_revision": revision.revision_sha256,
        "model_input": {"node_id": "train.1", "output_name": "model"}}]
    project = revise(project, graph)
    choices = choices.model_copy(update={"recipes": (*choices.recipes, revision), "node_resources": (
        *choices.node_resources, NodeResources(node_id="child", timeout_seconds=900))})
    assert any("materialize" in issue.message for issue in validate_graph(project.spec, choices.recipes, registry))
    with pytest.raises(ValueError, match="materialize"):
        plan_graph(project, choices, (facts,))
