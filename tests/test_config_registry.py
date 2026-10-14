import json

import pytest
from pydantic import ValidationError

from tuning_factory.config import AIConfig, load_config
from tuning_factory.registry import Registry
from tuning_factory.manifest import resolve, verify_manifest


def test_examples_resolve_and_verify(tmp_path):
    registry = Registry(tmp_path)
    registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=True)
    config = load_config("configs/ai/paper_extraction.yaml")
    manifest = resolve(config, registry)
    verify_manifest(manifest)
    assert len(manifest["dataset"]["sha256"]) == 64
    assert manifest["simulation"] is True
    manifest["config"]["ai_id"] = "tampered"
    with pytest.raises(ValueError, match="hash"):
        verify_manifest(manifest)


def test_strict_config_rejects_unknown_and_incompatible():
    data = load_config("configs/ai/paper_extraction.yaml").model_dump()
    with pytest.raises(ValidationError):
        AIConfig(**(data | {"typo": True}))
    data["tokenizer_revision"] = "b" * 40
    with pytest.raises(ValidationError, match="tokenizer"):
        AIConfig(**data)
    data["tokenizer_revision"] = data["model_revision"]
    data["base_model"] = "unknown/model"
    with pytest.raises(ValidationError, match="model"):
        AIConfig(**data)


def test_registry_version_is_immutable_and_detects_tampering(tmp_path):
    registry = Registry(tmp_path / "registry")
    source = tmp_path / "data.json"
    source.write_text(json.dumps([{"instruction": "x", "output": "y"}]))
    first = registry.register_dataset("test", "1", source)
    source.write_text(json.dumps([{"instruction": "x", "output": "z"}]))
    with pytest.raises(ValueError, match="immutable"):
        registry.register_dataset("test", "1", source)
    assert registry.get_dataset("test", "1") == first
    registry.dataset_path(first).write_text("[]")
    with pytest.raises(ValueError, match="hash"):
        registry.get_dataset("test", "1")


def test_invalid_data_and_paths(tmp_path):
    r = Registry(tmp_path / "r")
    source = tmp_path / "bad.json"
    source.write_text('[{"instruction":"x"}]')
    with pytest.raises(ValueError):
        r.register_dataset("x", "1", source)
    with pytest.raises(ValueError):
        r.register_dataset("../escape", "1", source)
