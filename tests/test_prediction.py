import json

import yaml

from tuning_factory.config import AIConfig, load_config
from tuning_factory.manifest import resolve
from tuning_factory.registry import Registry
from tuning_factory.training import LlamaFactoryBackend


def test_registered_validation_native_prediction_yaml_and_output(tmp_path):
    raw = b'[{"instruction":"Synthetic classify","input":"fixture","output":"record"}]'
    source = tmp_path / "val.json"
    source.write_bytes(raw)
    registry = Registry(tmp_path / "registry")
    registry.register_dataset("dummy", "1", "examples/dummy.json")
    registry.register_dataset("validation", "1", source)
    data = load_config("configs/ai/paper_extraction.yaml").model_dump()
    data["evaluation_profile"] = {"kind": "classification", "dataset_id": "validation", "dataset_version": "1", "max_samples": 1}
    config = AIConfig.model_validate(data)
    manifest = resolve(config, registry)
    assert manifest["validation_dataset"]["sha256"] == registry.get_dataset("validation", "1")["sha256"]

    class Fixture(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            plan = yaml.safe_load((directory / filename).read_text())
            assert plan["do_predict"] and not plan["do_train"]
            assert plan["adapter_name_or_path"] == "./output"
            assert plan["max_samples"] == 1
            (directory / "prediction").mkdir()
            (directory / "prediction" / "generated_predictions.jsonl").write_text(
                json.dumps({"predict": "record", "label": "record", "prompt": "fixture"}) + "\n")

    backend = Fixture()
    directory = tmp_path / "job"
    backend.prepare(config, directory, raw)
    backend.prepare_validation(directory, raw)
    backend.predict(config, directory, manifest["validation_dataset"]["sha256"], 5)
    output = json.loads((directory / "output" / "predictions.json").read_text())
    assert output["predictions"] == ["record"]
    assert output["validation_sha256"] == manifest["validation_dataset"]["sha256"]
