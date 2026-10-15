"""Offline import/API probe for the dedicated PPO runtime; does not load a model or train."""

from __future__ import annotations

import ast
import inspect
import json
import os
import textwrap
from importlib.metadata import version
from typing import Any

EXPECTED_VERSIONS = {
    "llamafactory": "0.9.3", "trl": "0.9.6", "transformers": "4.52.4",
    "peft": "0.15.2", "accelerate": "1.7.0", "datasets": "3.6.0",
    "huggingface-hub": "0.33.5", "torch": "2.7.1", "bitsandbytes": "0.48.1",
}


def inspect_ppo_api(config_type: type, trainer_type: type, factory_trainer_type: type) -> dict[str, Any]:
    """Compare the engine's real call sites to the installed TRL signatures."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(factory_trainer_type.__init__)))
    configuration_keywords: set[str] = set()
    trainer_keywords: set[str] = set()
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Name) and node.func.id == "PPOConfig":
            configuration_keywords.update(keyword.arg for keyword in node.keywords if keyword.arg)
        if (
            isinstance(node.func, ast.Attribute) and node.func.attr == "__init__"
            and isinstance(node.func.value, ast.Name) and node.func.value.id == "PPOTrainer"
        ):
            trainer_keywords.update(keyword.arg for keyword in node.keywords if keyword.arg)
    if not configuration_keywords or not trainer_keywords:
        raise RuntimeError("Could not locate the engine's PPOConfig/PPOTrainer call sites.")
    for label, target, keywords in (
        ("PPOConfig", config_type, configuration_keywords),
        ("PPOTrainer", trainer_type.__init__, trainer_keywords),
    ):
        parameters = inspect.signature(target).parameters
        missing = keywords - set(parameters)
        if missing:
            raise RuntimeError(f"Installed {label} API does not support: {', '.join(sorted(missing))}")
    return {
        "api_compatible": True,
        "configuration_keywords": sorted(configuration_keywords),
        "trainer_keywords": sorted(trainer_keywords),
    }


def main() -> None:
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    installed = {package: version(package) for package in EXPECTED_VERSIONS}
    differences = [
        package for package, expected in EXPECTED_VERSIONS.items()
        if installed[package].split("+")[0] != expected
    ]
    if differences:
        raise RuntimeError(f"Wrong PPO profile versions: {', '.join(differences)}")
    from llamafactory.train.ppo.trainer import CustomPPOTrainer
    from trl import PPOConfig, PPOTrainer

    result = inspect_ppo_api(PPOConfig, PPOTrainer, CustomPPOTrainer)
    configuration = PPOConfig(
        model_name="offline-synthetic-api-probe", mini_batch_size=1, batch_size=1,
        gradient_accumulation_steps=1, ppo_epochs=1, optimize_device_cache=True,
        target=6.0, use_score_scaling=False, use_score_norm=False, whiten_rewards=False,
        accelerator_kwargs={"step_scheduler_with_optimizer": False},
        log_with=None, project_kwargs={"logging_dir": "offline-probe"},
    )
    assert configuration.batch_size == 1
    print(json.dumps({
        **result, "versions": installed, "config_constructed": True,
        "model_downloaded": False, "gpu_training_performed": False,
        "verification_scope": "offline CPU import, call-signature compatibility, PPOConfig construction",
    }))


if __name__ == "__main__":
    main()
