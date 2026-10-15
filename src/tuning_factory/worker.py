"""GPU-side entrypoint. No credentials or control-plane DB required."""
import argparse
import importlib.metadata
import json
import os
import platform
import sys
import time
import traceback
from pathlib import Path

import yaml

from .capabilities import compatibility
from .config import AIConfig
from .manifest import source_hash, verify_manifest
from .registry import canonical, digest, file_digest
from .runtime_profiles import engine_profile
from .training import CanceledError, LlamaFactoryBackend


def validate_runtime_facts(config, facts):
    """Validate actual GPU/model facts; estimates never stand in for observed hardware."""
    if not facts.get("cuda_available") or facts.get("gpu_count", 0) < config.resource_profile.gpu_count:
        raise ValueError("CUDA GPU availability does not satisfy the resolved resource profile")
    if config.hyperparameters.precision == "bf16" and not facts.get("bf16_supported"):
        raise ValueError("BF16 is unsupported on this GPU; select FP16 or a supported GPU")
    profile = compatibility(config)["model"]
    if facts.get("model_type") != profile["architecture"]:
        raise ValueError("pinned model architecture differs from the reviewed model profile")
    if config.training_method == "freeze" and facts.get("num_hidden_layers") is not None:
        if abs(config.finetuning_options.freeze_trainable_layers) > facts["num_hidden_layers"]:
            raise ValueError("freeze_trainable_layers exceeds the pinned model layer count")


def runtime_preflight(config):
    import torch
    from transformers import AutoConfig
    observed = AutoConfig.from_pretrained(config.base_model, revision=config.model_revision, trust_remote_code=False)
    text = getattr(observed, "text_config", observed)
    facts = {"cuda_available": torch.cuda.is_available(), "gpu_count": torch.cuda.device_count(),
             "bf16_supported": torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False,
             "model_type": observed.model_type, "num_hidden_layers": getattr(text, "num_hidden_layers", None)}
    if config.model_profile:
        from huggingface_hub import hf_hub_download
        cached = hf_hub_download(repo_id=config.base_model, filename="config.json", revision=config.model_revision)
        if file_digest(cached) != config.model_profile.config_sha256:
            raise ValueError("pinned model metadata hash differs from the inspected Hugging Face profile")
        if config.hyperparameters.cutoff_len > getattr(text, "max_position_embeddings", config.model_profile.max_length):
            raise ValueError("cutoff length exceeds the pinned model context length")
    validate_runtime_facts(config, facts)
    if config.training_stage == "ppo" and config.stage_options.reward_model_type == "full":
        from transformers import AutoTokenizer
        policy_tokenizer = AutoTokenizer.from_pretrained(config.base_model, revision=config.tokenizer_revision,
                                                        trust_remote_code=False)
        reward_tokenizer = AutoTokenizer.from_pretrained(config.stage_options.reward_model,
                                                        local_files_only=True, trust_remote_code=False)
        token_ids = ("bos_token_id", "eos_token_id", "pad_token_id", "unk_token_id")
        if (policy_tokenizer.get_vocab() != reward_tokenizer.get_vocab() or any(
            getattr(policy_tokenizer, name, None) != getattr(reward_tokenizer, name, None) for name in token_ids
        )):
            raise ValueError("PPO reward model tokenizer must match the pinned policy tokenizer")
        facts["reward_tokenizer_verified"] = True
    return facts


def main():
    # A worker launched through an isolated venv must delegate to that venv's engine CLI.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("timeout", type=int)
    parser.add_argument("--checkpoint", type=str)
    args = parser.parse_args()
    directory = args.directory.resolve()
    backend = LlamaFactoryBackend()
    result = {"status": "FAILED"}
    started = time.monotonic()
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        verify_manifest(manifest)
        if manifest["simulation"]:
            raise ValueError("simulation manifest cannot authorize GPU training")
        if manifest["source_sha256"] != source_hash():
            raise ValueError("worker source differs from resolved manifest; rebuild image")
        config = AIConfig.model_validate(manifest["config"])
        profile = engine_profile(config.training_stage)
        if importlib.metadata.version("llamafactory") != profile["engine_version"]:
            raise ValueError("worker engine version differs from manifest")
        lock_path = Path(os.environ.get("FACTORY_GPU_LOCK_FILE", "/opt/factory/" + profile["lock_file"]))
        expected_lock = manifest["dependency_file_hashes"].get(profile["lock_file"])
        if not expected_lock or digest(lock_path.read_bytes()) != expected_lock:
            raise ValueError("worker dependency lock differs from resolved manifest")
        runtime = {"python": platform.python_version(), "packages": {name: importlib.metadata.version(name)
                   for name in ("llamafactory", "torch", "transformers", "peft", "datasets", "accelerate", "bitsandbytes")}}
        (directory / "runtime.json").write_bytes(canonical(runtime))
        file_format = manifest["dataset"].get("file_format", "json")
        if file_format not in {"json", "jsonl"}:
            raise ValueError("unsupported dataset file format")
        if digest((directory / "data" / ("dataset." + file_format)).read_bytes()) != manifest["dataset"]["sha256"]:
            raise ValueError("transferred dataset hash mismatch")
        plan = yaml.safe_load((directory / "train.yaml").read_text())
        if digest(canonical(plan)) != manifest["training_config_sha256"]:
            raise ValueError("training YAML hash differs from resolved manifest")
        if plan != backend.translate(config):
            raise ValueError("training YAML differs from resolved configuration")
        validation = manifest.get("validation_dataset")
        if validation:
            validation_format = validation.get("file_format", "json")
            if validation_format not in {"json", "jsonl"}:
                raise ValueError("invalid validation file format")
            if digest((directory / "data" / ("validation." + validation_format)).read_bytes()) != validation["sha256"]:
                raise ValueError("validation data hash mismatch")
        def canceled():
            return (directory / "cancel").exists()
        (directory / "device-preflight.json").write_bytes(canonical(runtime_preflight(config)))
        reward_inventory = backend.verify_reward_model(config)
        if reward_inventory is not None:
            (directory / "reward-model-inventory.json").write_bytes(canonical(reward_inventory))
        if args.checkpoint:
            backend.resume(directory, directory / args.checkpoint, args.timeout, canceled)
        else:
            backend.train(directory, args.timeout, canceled)
        backend.validate_outputs(directory, config)
        backend.validate_training_result(directory, config)
        if validation:
            remaining = args.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("job budget exhausted before evaluation generation")
            if config.training_stage == "rm":
                backend.evaluate_reward(config, directory, validation["sha256"], remaining, canceled)
            else:
                backend.predict(config, directory, validation["sha256"], remaining, canceled)
        result = {"status": "COMPLETED", "engine_version": profile["engine_version"]}
    except CanceledError as exc:
        result = {"status": "CANCELED", "error": str(exc)}
    except Exception as exc:
        result = {"status": "FAILED", "error": str(exc)}
        (directory / "worker-error.log").write_text(traceback.format_exc())
    finally:
        # Result is the completion marker: publish only after artifact enumeration is durable.
        (directory / "artifacts.json").write_bytes(canonical(backend.collect_artifacts(directory)))
        temp = directory / "result.tmp"
        with temp.open("wb") as f:
            f.write(canonical(result))
            f.flush()
            os.fsync(f.fileno())
        temp.replace(directory / "result.json")


if __name__ == "__main__":
    main()
