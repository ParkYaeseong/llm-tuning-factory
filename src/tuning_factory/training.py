"""Training engine interface and the only production engine adapter."""
import hashlib
import json
import math
import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path
from types import MappingProxyType

import yaml

from .capabilities import STAGES, compatibility
from .config import AIConfig, GraphTrainingConfig
from .model_artifacts import ModelInput
from .registry import canonical, dataset_descriptor, digest, file_digest, parse_dataset

# A v2 logical training template holds no model location. Each model slot holds the sentinel
# ROLE_PREFIX + role; only the worker replaces it with a hash-verified local path (operation_runner).
ROLE_PREFIX = "factory-role:"
ROLE_SLOTS = MappingProxyType({
    "policy_base": "model_name_or_path", "policy_adapter": "adapter_name_or_path",
    "reference_base": "ref_model", "reference_adapter": "ref_model_adapters",
    "reward_base": "reward_model", "reward_adapter": "reward_model"})
PPO_CONTINUATION = ("PPO adapter continuation is unsupported: its KL reference would revert to the base; "
                    "materialize the parent first")
# Every graph training output is frozen as a typed artifact, and OFT exports have no typed adapter contract.
OFT_UNFREEZABLE = "OFT training is unsupported in graphs: its output cannot be frozen as a typed model artifact"


def requires_frozen_reference(config) -> bool:
    """LLaMA-Factory 0.9.5 uses a reference model for KTO and for DPO except ORPO/SimPO (plan F2)."""
    return config.training_stage == "kto" or (
        config.training_stage == "dpo" and config.stage_options.pref_loss not in {"orpo", "simpo"})


def check_model_input_contract(config, model_input: ModelInput) -> ModelInput:
    """Typed role rules that depend only on the training config and ModelInput (no artifacts)."""
    model_input = ModelInput.model_validate(model_input)
    if config.stage_options.reward_model is not None or config.stage_options.reward_model_sha256 is not None:
        raise ValueError("graph training binds only typed model roles")
    if (config.training_stage == "ppo") != (model_input.reward_model is not None):
        raise ValueError("PPO requires a typed reward_model binding; other stages cannot bind reward")
    if config.training_stage == "ppo" and model_input.initialization == "continue_adapter":
        raise ValueError(PPO_CONTINUATION)
    if model_input.initialization == "continue_adapter":
        if config.training_method not in {"lora", "qlora"}:
            raise ValueError("adapter continuation requires LoRA/QLoRA")
    elif model_input.initialization != ("full_weights" if config.training_method in {"full", "freeze"}
                                        else "new_adapter"):
        raise ValueError("initialization differs from requested training method")
    if requires_frozen_reference(config):
        if model_input.frozen_reference != model_input.policy:
            raise ValueError("frozen reference must bind the exact policy parent")
    elif model_input.frozen_reference is not None:
        raise ValueError("reference-free recipe cannot bind a frozen reference")
    return model_input


def check_generation_benchmark(benchmark) -> None:
    """The pinned engine's predict path can enforce only greedy causal-LM generation in fp16/bf16 (plan §6 Q8)."""
    if benchmark.semantic_model_kind != "causal_lm" or benchmark.generation_mode != "greedy":
        raise ValueError("v2 evaluation supports greedy causal-LM generation benchmarks only")
    if benchmark.precision == "fp32":
        raise ValueError("fp32 benchmark precision cannot be enforced by the pinned engine")


class CanceledError(RuntimeError):
    pass


class TrainingBackend(ABC):
    @abstractmethod
    def validate_config(self, config): ...

    @abstractmethod
    def prepare(self, config, directory, dataset): ...

    @abstractmethod
    def train(self, directory, timeout, canceled=lambda: False): ...

    @abstractmethod
    def resume(self, directory, checkpoint, timeout, canceled=lambda: False): ...

    @abstractmethod
    def collect_artifacts(self, directory): ...

    @abstractmethod
    def cleanup(self, directory): ...


class LlamaFactoryBackend(TrainingBackend):
    def __init__(self, command=None):
        self.command = command or ["llamafactory-cli", "train"]

    def validate_config(self, config):
        model = GraphTrainingConfig if isinstance(config, GraphTrainingConfig) else AIConfig
        return model.model_validate(config.model_dump())

    def prepare(self, config, directory, dataset, model_input=None):
        self.validate_config(config)
        if isinstance(config, GraphTrainingConfig) and model_input is None:
            raise ValueError("graph training requires a typed model_input; refusing an executable legacy plan")
        directory = Path(directory)
        (directory / "data").mkdir(parents=True, exist_ok=True)
        kind = STAGES[config.training_stage][1]
        _, _, file_format = parse_dataset(dataset, kind)
        file_name = "dataset." + file_format
        (directory / "data" / file_name).write_bytes(dataset)
        descriptor = dataset_descriptor(dataset, kind, file_name)
        (directory / "data" / "dataset_info.json").write_text(json.dumps({
            "factory_dataset": descriptor
        }), encoding="utf-8")
        plan = self.translate(config) if model_input is None else self.translate(config, model_input)
        (directory / "train.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        (directory / "config.json").write_text(config.model_dump_json(), encoding="utf-8")

    @staticmethod
    def translate(config, model_input: ModelInput | None = None):
        """Legacy executable plan (``model_input`` None, byte-identical), else a v2 logical template.

        The template carries no repository ID, revision or path: model slots hold role sentinels, the
        adapter/reference/reward slots exist only for the typed roles present, and ``reward_model_type``
        is derived from the typed reward binding, never from the config.
        """
        plan = LlamaFactoryBackend._legacy_plan(config)
        if model_input is None:
            return plan
        model_input = check_model_input_contract(config, model_input)
        for key in ("model_name_or_path", "model_revision", "reward_model", "reward_model_type"):
            plan.pop(key, None)
        plan["model_name_or_path"] = ROLE_PREFIX + "policy_base"
        if model_input.policy.adapter is not None:
            # Resume the exact parent adapter (fresh optimizer); never stack a new adapter on it.
            plan.update(adapter_name_or_path=ROLE_PREFIX + "policy_adapter", create_new_adapter=False)
        reference = model_input.frozen_reference
        if reference is not None:
            plan["ref_model"] = ROLE_PREFIX + "reference_base"
            if reference.adapter is not None:
                plan["ref_model_adapters"] = ROLE_PREFIX + "reference_adapter"
            if config.training_method == "qlora":
                # Plan §6 Q5: the frozen reference of a QLoRA policy is quantized like the policy.
                plan["ref_model_quantization_bit"] = config.quantization_bit
        reward = model_input.reward_model
        if reward is not None:
            plan["reward_model"] = ROLE_PREFIX + ("reward_adapter" if reward.adapter else "reward_base")
            plan["reward_model_type"] = "lora" if reward.adapter else "full"
        return plan

    @staticmethod
    def _legacy_plan(config):
        plan = {"model_name_or_path": config.base_model, "model_revision": config.model_revision,
                "trust_remote_code": False, "stage": config.training_stage, "do_train": True,
                "finetuning_type": ("lora" if config.training_method == "qlora" else config.training_method),
                "template": config.template,
                "dataset": "factory_dataset", "dataset_dir": "./data", "output_dir": "./output",
                "overwrite_output_dir": False, "report_to": "none", "logging_steps": 1,
                "save_only_model": False, "save_safetensors": True,
                "fp16": config.hyperparameters.precision == "fp16",
                "bf16": config.hyperparameters.precision == "bf16",
                **config.hyperparameters.model_dump(exclude={"precision", "lora_rank", "lora_target"})}
        options = config.finetuning_options
        if config.training_method in {"lora", "qlora"}:
            plan.update(lora_rank=config.hyperparameters.lora_rank, lora_target=config.hyperparameters.lora_target)
            plan.update(options.model_dump(include={"lora_alpha", "lora_dropout", "use_rslora", "use_dora",
                        "loraplus_lr_ratio", "loraplus_lr_embedding", "pissa_init", "pissa_iter", "pissa_convert"},
                                           exclude_none=True))
        elif config.training_method == "freeze":
            plan.update(options.model_dump(include={"freeze_trainable_layers", "freeze_trainable_modules",
                                                    "freeze_extra_modules"}, exclude_none=True))
        elif config.training_method == "oft":
            plan.update(options.model_dump(include={"oft_rank", "oft_block_size", "oft_target", "module_dropout"}))
            # The pinned engine resolves target_modules via lora_target even on its OFT branch.
            plan["lora_target"] = options.oft_target
        if config.training_method == "qlora":
            plan.update(quantization_bit=config.quantization_bit, quantization_method="bnb")
        fields = {
            "dpo": {"pref_loss", "pref_beta", "pref_ftx", "pref_bco_weight", "dpo_label_smoothing", "simpo_gamma"},
            "kto": {"pref_beta", "kto_chosen_weight", "kto_rejected_weight"},
            "ppo": {"ppo_buffer_size", "ppo_epochs", "ppo_score_norm", "ppo_target", "ppo_whiten_rewards",
                    "reward_model", "reward_model_type"},
        }.get(config.training_stage, set())
        plan.update(config.stage_options.model_dump(include=fields, exclude_none=True))
        return plan

    def train(self, directory, timeout, canceled=lambda: False):
        self.run_config(directory, timeout, canceled, "train.yaml", "train.log")

    def run_config(self, directory, timeout, canceled, filename, log_name):
        directory = Path(directory).resolve()
        with (directory / log_name).open("ab") as log:
            process = subprocess.Popen([*self.command, filename], cwd=directory,
                                       stdout=log, stderr=subprocess.STDOUT,
                                       start_new_session=os.name != "nt")
            deadline = time.monotonic() + timeout
            try:
                while process.poll() is None:
                    if canceled():
                        raise CanceledError("cancellation requested")
                    if time.monotonic() >= deadline:
                        raise TimeoutError("training deadline exceeded")
                    time.sleep(0.1)
                if process.returncode:
                    raise RuntimeError(f"training exited with code {process.returncode}; see train.log")
            finally:
                if process.poll() is None:
                    if os.name == "nt":
                        killed = subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"],
                                                capture_output=True)
                        if killed.returncode and process.poll() is None:
                            process.kill()
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    def prepare_validation(self, directory, raw, kind="instruction"):
        directory = Path(directory)
        _, _, file_format = parse_dataset(raw, kind)
        filename = "validation." + file_format
        (directory / "data" / filename).write_bytes(raw)
        info_path = directory / "data" / "dataset_info.json"
        info = json.loads(info_path.read_text())
        descriptor = dataset_descriptor(raw, kind, filename)
        info["factory_validation"] = descriptor
        info_path.write_text(json.dumps(info), encoding="utf-8")

    def predict(self, config, directory, validation_sha256, timeout, canceled=lambda: False):
        directory = Path(directory)
        if config.training_stage == "rm":
            raise ValueError("reward models cannot be evaluated with text generation")
        if config.training_method == "oft":
            raise ValueError("OFT generation evaluation is unsupported by the pinned engine parser")
        profile = config.evaluation_profile
        plan = {"model_name_or_path": config.base_model, "model_revision": config.model_revision,
                "trust_remote_code": False, "stage": "sft", "do_train": False, "do_predict": True,
                "finetuning_type": "lora", "adapter_name_or_path": "./output", "template": config.template,
                "eval_dataset": "factory_validation", "dataset_dir": "./data", "output_dir": "./prediction",
                "predict_with_generate": True, "max_samples": profile.max_samples,
                "max_new_tokens": profile.max_new_tokens, "do_sample": False,
                "cutoff_len": config.hyperparameters.cutoff_len, "per_device_eval_batch_size": 1,
                "report_to": "none", "fp16": config.hyperparameters.precision == "fp16",
                "bf16": config.hyperparameters.precision == "bf16"}
        if config.training_method == "qlora":
            plan.update(quantization_bit=config.quantization_bit, quantization_method="bnb")
        elif config.training_method in {"full", "freeze"}:
            plan.update(model_name_or_path="./output", finetuning_type="full")
            plan.pop("adapter_name_or_path")
        (directory / "predict.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        self.run_config(directory, timeout, canceled, "predict.yaml", "predict.log")
        if config.training_method == "qlora":
            self.confirm_quantization(directory / "predict.log", config.quantization_bit)
        source = directory / "prediction" / "generated_predictions.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows or len(rows) > profile.max_samples:
            raise ValueError("native prediction result has invalid sample count")
        result = {"predictions": [row["predict"].strip() for row in rows],
                  "references": [row["label"].strip() for row in rows],
                  "validation_sha256": validation_sha256, "source": "llamafactory-native-generation"}
        (directory / "output").mkdir(exist_ok=True)
        (directory / "output" / "predictions.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    @staticmethod
    def prediction_plan(benchmark, *, model_path: str, adapter_path: str | None, max_samples: int) -> dict:
        """Greedy generation over the worker-derived ``factory_validation`` subset on verified local paths."""
        check_generation_benchmark(benchmark)
        plan = {"model_name_or_path": model_path, "trust_remote_code": False, "stage": "sft", "do_train": False,
                "do_predict": True, "finetuning_type": "lora" if adapter_path else "full",
                "template": benchmark.template, "eval_dataset": "factory_validation", "dataset_dir": "./data",
                "output_dir": "./prediction", "predict_with_generate": True, "max_samples": max_samples,
                "max_new_tokens": benchmark.max_new_tokens, "do_sample": False, "cutoff_len": benchmark.cutoff_len,
                "seed": benchmark.seed, "per_device_eval_batch_size": 1, "report_to": "none",
                "fp16": benchmark.precision == "fp16", "bf16": benchmark.precision == "bf16"}
        if adapter_path:
            plan["adapter_name_or_path"] = adapter_path
        if benchmark.quantization_bit is not None:
            plan.update(quantization_bit=benchmark.quantization_bit, quantization_method="bnb")
        return plan

    def predict_bound(self, directory, plan, sample_ids, timeout, canceled=lambda: False) -> dict:
        """Run a bound prediction plan; write ``output/predictions.json`` with predictions only, in sample order.

        References and metrics are recomputed by the control plane from the registered rows.
        """
        directory = Path(directory)
        with (directory / "predict.yaml").open("xb") as stream:
            stream.write(yaml.safe_dump(plan, sort_keys=True).encode("utf-8"))
        self.run_config(directory, timeout, canceled, "predict.yaml", "predict.log")
        if plan.get("quantization_bit") is not None:
            self.confirm_quantization(directory / "predict.log", plan["quantization_bit"])
        source = directory / "prediction" / "generated_predictions.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        if len(rows) != len(sample_ids):
            raise ValueError("native prediction result has invalid sample count")
        result = {"ordered_sample_ids": list(sample_ids), "predictions": [row["predict"].strip() for row in rows],
                  "source": "llamafactory-native-generation"}
        (directory / "output").mkdir(exist_ok=True)
        with (directory / "output" / "predictions.json").open("xb") as stream:
            stream.write(canonical(result))
        return result

    def evaluate_reward(self, config, directory, validation_sha256, timeout, canceled=lambda: False):
        if config.training_stage != "rm" or config.evaluation_profile.kind != "reward":
            raise ValueError("reward evaluation requires an RM model and reward evaluation profile")
        if config.training_method == "oft":
            raise ValueError("OFT reward evaluation is unsupported by the pinned engine parser")
        directory = Path(directory)
        plan = {"model_name_or_path": config.base_model, "model_revision": config.model_revision,
                "trust_remote_code": False, "stage": "rm", "do_train": False, "do_eval": True,
                "finetuning_type": "lora", "adapter_name_or_path": "./output", "template": config.template,
                "eval_dataset": "factory_validation", "dataset_dir": "./data", "output_dir": "./reward-evaluation",
                "max_samples": config.evaluation_profile.max_samples, "cutoff_len": config.hyperparameters.cutoff_len,
                "per_device_eval_batch_size": 1, "report_to": "none",
                "fp16": config.hyperparameters.precision == "fp16", "bf16": config.hyperparameters.precision == "bf16"}
        if config.training_method == "qlora":
            plan.update(quantization_bit=config.quantization_bit, quantization_method="bnb")
        elif config.training_method in {"full", "freeze"}:
            plan.update(model_name_or_path="./output", finetuning_type="full")
            plan.pop("adapter_name_or_path")
        (directory / "reward-evaluate.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        self.run_config(directory, timeout, canceled, "reward-evaluate.yaml", "reward-evaluate.log")
        if config.training_method == "qlora":
            self.confirm_quantization(directory / "reward-evaluate.log", config.quantization_bit)
        source = directory / "reward-evaluation/all_results.json"
        raw = json.loads(source.read_text(encoding="utf-8"))
        metrics = {name: value for name, value in raw.items() if name in {"eval_loss", "eval_accuracy"}
                   and isinstance(value, (int, float)) and not isinstance(value, bool)}
        if set(metrics) != {"eval_accuracy", "eval_loss"} or any(
            not math.isfinite(float(value)) for value in metrics.values()
        ) or not 0 <= metrics["eval_accuracy"] <= 1:
            raise ValueError("native reward evaluation did not produce finite reward metrics")
        result = {"metrics": metrics, "validation_sha256": validation_sha256,
                  "source": "llamafactory-native-reward-evaluation"}
        (directory / "output").mkdir(exist_ok=True)
        (directory / "output/reward_metrics.json").write_bytes(canonical(result))

    def resume(self, directory, checkpoint, timeout, canceled=lambda: False):
        directory, checkpoint = Path(directory).resolve(), Path(checkpoint).resolve()
        if not checkpoint.is_relative_to(directory / "output"):
            raise ValueError("checkpoint must belong to this job output")
        config = AIConfig.model_validate_json((directory / "config.json").read_text())
        if config.training_stage == "ppo":
            raise ValueError("PPO checkpoint resume is unsupported by the pinned engine")
        if config.training_method == "oft":
            raise ValueError("OFT checkpoint resume requires an engine loader fix; unsupported in the pinned engine")
        if config.training_stage == "rm" and config.training_method in {"full", "freeze"}:
            raise ValueError("full/freeze reward model checkpoint resume is unsupported by the pinned value-head loader")
        required = ["trainer_state.json", "optimizer.pt", "scheduler.pt"]
        if not all((checkpoint / name).is_file() for name in required) or not list(checkpoint.glob("rng_state*.pth")):
            raise ValueError("incomplete resumable checkpoint")
        try:
            self.validate_outputs(directory, config, checkpoint)
        except ValueError as exc:
            raise ValueError(f"incomplete resumable checkpoint: {exc}") from exc
        if config.training_method in {"lora", "qlora"}:
            adapter = json.loads((checkpoint / "adapter_config.json").read_text())
            if adapter.get("base_model_name_or_path", config.base_model) != config.base_model:
                raise ValueError("checkpoint model incompatible")
        plan = yaml.safe_load((directory / "train.yaml").read_text())
        plan["resume_from_checkpoint"] = checkpoint.relative_to(directory).as_posix()
        (directory / "train.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        self.train(directory, timeout, canceled)

    def collect_artifacts(self, directory):
        directory = Path(directory)
        return {p.relative_to(directory).as_posix(): file_digest(p)
                for p in sorted(directory.rglob("*")) if p.is_file() and not p.is_symlink()
                  and p.name not in {"artifacts.json", "tracking-error.txt"}
                  and p != directory / "cancel"}

    @staticmethod
    def confirm_quantization(log_path, bits=4):
        if not log_path.is_file() or f"Quantizing model to {bits} bit with bitsandbytes." not in log_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            raise ValueError(f"{bits}-bit quantization was not confirmed by LLaMA-Factory; refusing full-precision fallback")

    @staticmethod
    def validate_outputs(directory, config, output=None):
        """Check method-specific final weights, shard completeness, and reward value heads."""
        output = Path(output) if output is not None else Path(directory) / "output"
        if config.training_method in {"lora", "qlora", "oft"}:
            required = ["adapter_config.json", "adapter_model.safetensors"]
            if not all((output / name).is_file() and not (output / name).is_symlink() for name in required):
                raise ValueError("training exited without final adapter weights/configuration")
            adapter = json.loads((output / "adapter_config.json").read_text())
            expected = "OFT" if config.training_method == "oft" else "LORA"
            if adapter.get("peft_type", expected) != expected:
                raise ValueError("final adapter type differs from the configured training method")
        else:
            LlamaFactoryBackend.validate_full_weights(output)
        if config.training_stage in {"rm", "ppo"} and not any(
            (output / name).is_file() and not (output / name).is_symlink()
            for name in ("value_head.safetensors", "value_head.bin")
        ):
            raise ValueError("reward/PPO output is missing its value head")

    @staticmethod
    def validate_full_weights(output):
        if not (output / "config.json").is_file():
            raise ValueError("final full model configuration is missing")
        single = output / "model.safetensors"
        if single.is_file() and not single.is_symlink():
            return
        index = output / "model.safetensors.index.json"
        if not index.is_file() or index.is_symlink():
            raise ValueError("final full model weights or shard index are missing")
        weight_map = json.loads(index.read_text()).get("weight_map")
        if not isinstance(weight_map, dict) or not weight_map:
            raise ValueError("invalid model shard index")
        for name in weight_map.values():
            if not isinstance(name, str) or Path(name).name != name or "/" in name or "\\" in name:
                raise ValueError("model shard path must remain inside the output directory")
            shard = output / name
            if not name.endswith(".safetensors") or not shard.is_file() or shard.is_symlink():
                raise ValueError("model shard referenced by index is missing or unsafe")

    @staticmethod
    def verify_reward_model(config):
        if config.training_stage != "ppo":
            return
        folder = Path(config.stage_options.reward_model).resolve()
        if not folder.is_dir() or folder == Path(folder.anchor):
            raise ValueError("reward_model must be a pre-provisioned model directory")
        files = sorted(folder.rglob("*"))
        if any(path.is_symlink() for path in files):
            raise ValueError("reward model inventory cannot contain symbolic links")
        inventory = {}
        for path in files:
            if path.is_file():
                with path.open("rb") as source:
                    inventory[path.relative_to(folder).as_posix()] = hashlib.file_digest(source, "sha256").hexdigest()
        if not inventory or digest(canonical(inventory)) != config.stage_options.reward_model_sha256:
            raise ValueError("reward model inventory hash mismatch")
        reward_config = config.model_copy(update={"training_stage": "rm", "training_method": (
            "lora" if config.stage_options.reward_model_type == "lora" else "full")})
        LlamaFactoryBackend.validate_outputs(folder.parent, reward_config, folder)
        if config.stage_options.reward_model_type == "lora":
            adapter = json.loads((folder / "adapter_config.json").read_text())
            if adapter.get("peft_type") != "LORA":
                raise ValueError("reward adapter type must be LoRA")
            if adapter.get("base_model_name_or_path") != config.base_model:
                raise ValueError("reward adapter base model differs from the PPO policy base model")
        else:
            model = json.loads((folder / "config.json").read_text())
            if model.get("model_type") != compatibility(config)["model"]["architecture"]:
                raise ValueError("full reward model architecture differs from the PPO policy profile")
        return inventory

    def validate_training_result(self, directory, config=None):
        quantized = config is not None and config.training_method == "qlora"
        if quantized:
            self.confirm_quantization(Path(directory) / "train.log", config.quantization_bit)
        state_path = Path(directory) / "output" / "trainer_state.json"
        if not state_path.is_file():
            return
        state = json.loads(state_path.read_text())
        checked = 0
        for item in state.get("log_history", []):
            for key, value in item.items():
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    checked += 1
                    if not math.isfinite(float(value)):
                        raise ValueError(f"non-finite {key} in training state; review precision and optimizer configuration")
        health = {"finite_statistics_checked": checked, "global_step": state.get("global_step")}
        if quantized:
            health["quantization_confirmed_bits"] = config.quantization_bit
        (Path(directory) / "training-health.json").write_text(json.dumps(health), encoding="utf-8")

    def cleanup(self, directory):
        # Retain all evidence; lifecycle ownership is the executor's responsibility.
        pass


class MockTrainingBackend(LlamaFactoryBackend):
    """Only verifies orchestration. Never emits adapter weights or model quality metrics."""
    def train(self, directory, timeout, canceled=lambda: False):
        if canceled():
            raise CanceledError("cancellation requested")
        directory = Path(directory)
        (directory / "output").mkdir(exist_ok=True)
        (directory / "output" / "simulation.json").write_text('{"simulation":true,"trained":false}')
        (directory / "train.log").write_text("SIMULATION: no GPU, no model training, no model quality measured.\n")
