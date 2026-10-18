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
