import pytest
from pydantic import TypeAdapter

from tuning_factory.operations import OperationSpec


def test_operation_union_rejects_arbitrary_paths_and_extra_fields():
    with pytest.raises(ValueError):
        TypeAdapter(OperationSpec).validate_python({"kind": "materialize", "model": "/tmp/model",
                                                   "resources": {}, "command": "unsafe"})


def test_graph_ppo_config_is_path_free_and_legacy_still_needs_reward_path():
    from tuning_factory.config import AIConfig, GraphTrainingConfig
    data = dict(ai_id="test", workflow_id="w", dataset_id="data", dataset_version="1",
                base_model="Qwen/Qwen2.5-0.5B-Instruct", model_revision="a"*40,
                tokenizer_revision="a"*40, training_stage="ppo")
    with pytest.raises(ValueError, match="reward_model"):
        AIConfig(**data)
    config = GraphTrainingConfig(**data)
    assert isinstance(config, AIConfig)
    assert config.stage_options.reward_model is None
    with pytest.raises(ValueError, match="path-free"):
        GraphTrainingConfig(**data, stage_options={"reward_model": "/tmp/reward", "reward_model_sha256": "b"*64})


def test_v2_manifest_is_path_free_and_graph_config_is_rejected_by_v1(tmp_path):
    from test_graph_planner import fixture

    from tuning_factory.graph_planner import VerifiedBindings, compile_node, plan_graph
    from tuning_factory.manifest import resolve, resolve_operation, verify_manifest
    from tuning_factory.model_artifacts import ModelArtifactStore
    registry, project, choices, facts = fixture(tmp_path)
    operation = compile_node(plan_graph(project, choices, (facts,)), "train.1",
        VerifiedBindings(registry, ModelArtifactStore(tmp_path / "models"), choices.executors[0]))
    manifest = resolve_operation(operation, registry)
    verify_manifest(manifest)
    assert manifest["schema_version"] == 2
    assert str(tmp_path) not in str(manifest)
    with pytest.raises(ValueError, match="graph training"):
        resolve(operation.config, registry)
    with pytest.raises(ValueError):
        resolve_operation({**operation.model_dump(), "ssh_key": "secret"}, registry)
