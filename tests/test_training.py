import json
import sys
import time

import pytest
import yaml

from tuning_factory.config import load_config
from tuning_factory.training import CanceledError, LlamaFactoryBackend, MockTrainingBackend


def test_yaml_translation_and_real_subprocess(tmp_path):
    config = load_config("configs/ai/paper_extraction.yaml")
    backend = LlamaFactoryBackend(command=[sys.executable, "-c", "print('worker test')"])
    backend.prepare(config, tmp_path, b'[{"instruction":"x","output":"y"}]')
    plan = yaml.safe_load((tmp_path / "train.yaml").read_text())
    assert plan["model_revision"] == config.model_revision
    assert plan["finetuning_type"] == "lora"
    backend.train(tmp_path, 5)
    assert "worker test" in (tmp_path / "train.log").read_text()


def test_failures_and_timeouts(tmp_path):
    for command, exception in [([sys.executable, "-c", "raise SystemExit(2)"], RuntimeError),
                               ([sys.executable, "-c", "import time;time.sleep(10)"], TimeoutError)]:
        backend = LlamaFactoryBackend(command=command)
        with pytest.raises(exception):
            backend.train(tmp_path, 1)


def test_checkpoint_compatibility_and_collection(tmp_path):
    backend = MockTrainingBackend()
    config = load_config("configs/ai/paper_extraction.yaml")
    backend.prepare(config, tmp_path, b'[{"instruction":"test","output":"fixture"}]')
    backend.train(tmp_path, 5)
    artifacts = backend.collect_artifacts(tmp_path)
    assert "output/simulation.json" in artifacts
    assert "output/adapter_model.safetensors" not in artifacts
    cp = tmp_path / "output" / "checkpoint-1"
    cp.mkdir()
    with pytest.raises(ValueError, match="checkpoint"):
        backend.resume(tmp_path, cp, 5)
    (cp / "trainer_state.json").write_text(json.dumps({"global_step": 1}))
    (cp / "adapter_config.json").write_text("{}")
    (cp / "adapter_model.safetensors").write_bytes(b"test-only")
    (cp / "optimizer.pt").write_bytes(b"test-only")
    (cp / "scheduler.pt").write_bytes(b"test-only")
    (cp / "rng_state.pth").write_bytes(b"test-only")
    backend.resume(tmp_path, cp, 5)


def test_qlora_config(tmp_path):
    config = load_config("configs/ai/paper_extraction.yaml")
    config = type(config).model_validate(config.model_dump() | {"training_method": "qlora"})
    LlamaFactoryBackend().prepare(config, tmp_path, b'[{"instruction":"test","output":"fixture"}]')
    plan = yaml.safe_load((tmp_path / "train.yaml").read_text())
    assert plan["quantization_bit"] == 4
    assert plan["quantization_method"] == "bitsandbytes"


def test_active_subprocess_cancellation(tmp_path):
    backend = LlamaFactoryBackend(command=[sys.executable, "-c", "import time; time.sleep(20)"])
    start = time.monotonic()
    with pytest.raises(CanceledError):
        backend.train(tmp_path, 30, canceled=lambda: time.monotonic() - start > 0.2)
    assert time.monotonic() - start < 10


def test_bf16_translation_for_supported_gpu():
    config = load_config("configs/ai/integration_smoke.yaml")
    plan = LlamaFactoryBackend.translate(config)
    assert plan["bf16"] is True
    assert plan["fp16"] is False
    assert "precision" not in plan


def test_nonfinite_gradients_fail_training_health_check(tmp_path):
    (tmp_path / "output").mkdir()
    state = tmp_path / "output" / "trainer_state.json"
    state.write_text('{"global_step":2,"log_history":[{"loss":0.7,"grad_norm":NaN}]}')
    with pytest.raises(ValueError, match="non-finite grad_norm"):
        LlamaFactoryBackend().validate_training_result(tmp_path)
    state.write_text('{"global_step":2,"log_history":[{"loss":0.7,"grad_norm":1.2}]}')
    LlamaFactoryBackend().validate_training_result(tmp_path)
    assert json.loads((tmp_path / "training-health.json").read_text())["global_step"] == 2
