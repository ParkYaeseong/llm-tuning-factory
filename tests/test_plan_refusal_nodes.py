"""Task 11.5 re-review m1: a planning refusal of one node names that node.

``graph_plan`` reports ``plan_graph``'s refusals as one ``plan_refused`` issue. A refusal that belongs to one node
(the merge and selection refusals of Task 11.5 and the planner's static routes) carries that node's ID and its
pointer, so the workbench can highlight the card; the English message is unchanged (the UI matches on it). A refusal
of the whole plan keeps ``node_id: null`` and an empty path.

Through the workflow channel on the synthetic seeded root (``test_seed_acceptance_root``), offline.
"""
import pytest
from test_seed_acceptance_root import (
    action,
    estimate,
    evaluate,
    from_root,
    merge,
    output,
    plan_request,
    save_graph,
    save_recipe,
    select,
    train,
    workbench,
)

PROFILES = ["estimate-a"]


def plan_issue(bench, nodes, **graph):
    save_graph(bench, nodes, **graph)
    validated = action(bench.state, "graph_validate", {"project_id": bench.project["project_id"]})
    assert validated["status"] == "ok", validated   # the definition itself is valid: plan_graph refuses it
    refused = action(bench.state, "graph_plan", plan_request(bench.project["project_id"], nodes,
                                                             [estimate("estimate-a")]))
    assert refused["status"] == "invalid", refused
    [found] = refused["issues"]
    return found


def lora(bench, node_id, recipe=None):
    return train(node_id, recipe or bench.recipe, from_root(bench.root), PROFILES)


def graphs(bench):
    """name -> (nodes, the refused node, message)."""
    full = save_recipe(bench.state, "qa-sft-full", training_method="full")
    r128 = save_recipe(bench.state, "qa-sft-r128", hyperparameters={"lora_rank": 128})
    r8 = save_recipe(bench.state, "qa-sft-r8", hyperparameters={"lora_rank": 8})
    a, b, c = lora(bench, "a"), lora(bench, "b"), lora(bench, "c")

    def ev(node_id, model):
        return evaluate(node_id, output(model), bench.benchmark, PROFILES)

    full_model = {"kind": "materialize", "id": "full", "label": "확정", "model": output("a"),
                  "executor_profile_ids": PROFILES}
    return {
        "full_parent": ([a, lora(bench, "f", full), merge("m", [output("a"), output("f")], PROFILES)],
                        "m", "merge requires plain causal LoRA/QLoRA parents"),
        "materialized_parent": ([a, b, full_model, merge("m", [output("full"), output("b")], PROFILES)],
                                "m", "merge requires plain causal LoRA/QLoRA parents"),
        "duplicate_through_selection": ([a, ev("ea", "a"), select("s", ("ea",)),
                                         merge("m", [output("s"), output("a")], PROFILES)],
                                        "m", "merge requires distinct actual artifacts"),
        "repeated_selected_evaluation": ([a, b, ev("ea", "a"), ev("eb", "b"), select("s", ("ea", "eb")),
                                          ev("es", "s"), select("last", ("es", "es"))],
                                         "last", "selection candidates must be distinct models"),
        "same_selected_model_twice": ([a, b, ev("ea", "a"), ev("eb", "b"), select("s", ("ea", "eb")),
                                      ev("es", "s"), ev("es2", "s"), select("last", ("es", "es2"))],
                                     "last", "selection candidates must be distinct models"),
        "selected_model_alias": ([a, b, ev("ea", "a"), ev("eb", "b"), select("s", ("ea", "eb")),
                                  ev("es", "s"), select("t", ("es",)),
                                  merge("last", [output("s"), output("t")], PROFILES)],
                                 "last", "merge requires distinct actual artifacts"),
        "nested_rank": ([lora(bench, "a", r128), lora(bench, "b", r128), lora(bench, "d", r8),
                         merge("ab", [output("a"), output("b")], PROFILES),
                         merge("m", [output("ab"), output("d")], PROFILES)],
                        "m", "merged rank exceeds 256"),
        "same_model_twice": ([a, ev("ea", "a"), ev("ea2", "a"), select("s", ("ea", "ea2"))],
                             "s", "selection candidates must be distinct models"),
        "two_merged_candidates": ([a, b, c, merge("ab", [output("a"), output("b")], PROFILES),
                                   merge("bc", [output("b"), output("c")], PROFILES), ev("ea", "a"),
                                   ev("eab", "ab"), ev("ebc", "bc"), select("s", ("ea", "eab", "ebc"))],
                                  "s", "a selection takes at most one merged candidate"),
        "only_a_merged_candidate": ([a, b, merge("ab", [output("a"), output("b")], PROFILES), ev("eab", "ab"),
                                     select("s", ("eab",))],
                                    "s", "a selection needs an original candidate beside its merged one"),
    }


@pytest.mark.parametrize("name", ["full_parent", "materialized_parent", "duplicate_through_selection", "nested_rank",
                                  "same_model_twice", "two_merged_candidates", "only_a_merged_candidate",
                                  "repeated_selected_evaluation", "same_selected_model_twice",
                                  "selected_model_alias"])
def test_a_node_refusal_of_the_planner_names_its_node(tmp_path, name):
    bench = workbench(tmp_path / "state", tmp_path)
    nodes, node_id, message = graphs(bench)[name]
    found = plan_issue(bench, nodes)
    index = [node["id"] for node in nodes].index(node_id)
    assert found == {"code": "plan_refused", "path": f"/graph/nodes/{index}", "message": message,
                     "node_id": node_id, "row_number": None}, found


def test_a_refusal_of_the_whole_plan_names_no_node(tmp_path):
    bench = workbench(tmp_path / "state", tmp_path)
    found = plan_issue(bench, [lora(bench, "a")], max_total_cost_usd=0.01)
    assert found == {"code": "plan_refused", "path": "", "node_id": None, "row_number": None,
                     "message": "whole graph including retries exceeds maximum total cost"}, found


def test_a_conditional_selection_merge_still_plans_through_the_workflow(tmp_path):
    bench = workbench(tmp_path / "state", tmp_path)
    nodes = [lora(bench, "a"), lora(bench, "b"), evaluate("ea", output("a"), bench.benchmark, PROFILES),
             evaluate("eb", output("b"), bench.benchmark, PROFILES), select("s", ("ea", "eb")),
             merge("last", [output("s"), output("a")], PROFILES)]
    save_graph(bench, nodes)
    planned = action(bench.state, "graph_plan", plan_request(bench.project["project_id"], nodes,
                                                            [estimate("estimate-a")]))
    assert planned["status"] == "ok", planned
