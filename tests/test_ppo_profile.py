import importlib.util
from pathlib import Path

import pytest


def probe_module():
    path = Path(__file__).resolve().parents[1] / "scripts" / "verify_ppo_profile.py"
    specification = importlib.util.spec_from_file_location("verify_ppo_profile", path)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


class LegacyConfig:
    def __init__(self, model_name, mini_batch_size, batch_size, optimize_device_cache):
        pass


class NewIncompatibleConfig:
    def __init__(self, output_dir, per_device_train_batch_size):
        pass


class LegacyTrainer:
    def __init__(self, config, model, ref_model, tokenizer, dataset):
        pass


class FactoryTrainer:
    def __init__(self):
        # These unevaluated calls model the API integration exercised by the real engine.
        ppo_config = PPOConfig(  # noqa: F821
            model_name="synthetic", mini_batch_size=1, batch_size=1, optimize_device_cache=True,
        )
        PPOTrainer.__init__(  # noqa: F821
            self, config=ppo_config, model=None, ref_model=None, tokenizer=None, dataset=None,
        )


def test_probe_checks_actual_engine_call_keywords_against_trl_api():
    result = probe_module().inspect_ppo_api(LegacyConfig, LegacyTrainer, FactoryTrainer)
    assert result["api_compatible"] is True
    assert "model_name" in result["configuration_keywords"]
    assert "config" in result["trainer_keywords"]


def test_probe_rejects_new_trl_api_instead_of_reporting_ppo_ready():
    with pytest.raises(RuntimeError, match="model_name"):
        probe_module().inspect_ppo_api(NewIncompatibleConfig, LegacyTrainer, FactoryTrainer)


def test_ppo_profile_is_separate_hashed_and_uses_original_supported_api():
    root = Path(__file__).resolve().parents[1]
    lock = (root / "containers/gpu-ppo-requirements.lock").read_text()
    assert "llamafactory==0.9.3" in lock
    assert "trl==0.9.6" in lock
    assert "transformers==4.52.4" in lock
    assert "peft==0.15.2" in lock
    assert "huggingface-hub==0.33.5" in lock
    assert "pyarrow==20.0.0" in lock
    assert "--hash=sha256:" in lock
    assert "llamafactory==0.9.5" in (root / "containers/gpu-requirements.lock").read_text()
