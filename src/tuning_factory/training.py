"""Training engine interface and the only production engine adapter."""
import json
import math
import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from .config import AIConfig
from .registry import digest, parse_dataset


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
        return AIConfig.model_validate(config.model_dump())

    def prepare(self, config, directory, dataset):
        self.validate_config(config)
        directory = Path(directory)
        (directory / "data").mkdir(parents=True, exist_ok=True)
        _, formatting, file_format = parse_dataset(dataset)
        file_name = "dataset." + file_format
        (directory / "data" / file_name).write_bytes(dataset)
        descriptor = {"file_name": file_name, "formatting": formatting,
                      "columns": {"prompt": "instruction", "query": "input", "response": "output"}}
        if formatting == "sharegpt":
            # Preserve the canonical kbl-llm-repo ops/dataset_info.json mapping.
            descriptor.update(columns={"messages": "messages"}, tags={
                "role_tag": "role", "content_tag": "content", "user_tag": "user",
                "assistant_tag": "assistant", "system_tag": "system"})
        (directory / "data" / "dataset_info.json").write_text(json.dumps({
            "factory_dataset": descriptor
        }), encoding="utf-8")
        plan = self.translate(config)
        (directory / "train.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        (directory / "config.json").write_text(config.model_dump_json(), encoding="utf-8")

    @staticmethod
    def translate(config):
        plan = {"model_name_or_path": config.base_model, "model_revision": config.model_revision,
                "trust_remote_code": False, "stage": "sft", "do_train": True,
                "finetuning_type": "lora", "template": config.template,
                "dataset": "factory_dataset", "dataset_dir": "./data", "output_dir": "./output",
                "overwrite_output_dir": False, "report_to": "none", "logging_steps": 1,
                "save_only_model": False, "fp16": config.hyperparameters.precision == "fp16",
                "bf16": config.hyperparameters.precision == "bf16",
                **config.hyperparameters.model_dump(exclude={"precision"})}
        if config.training_method == "qlora":
            plan.update(quantization_bit=4, quantization_method="bnb")
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
                        subprocess.run(["taskkill", "/PID", str(process.pid), "/T", "/F"], capture_output=True)
                    else:
                        os.killpg(process.pid, signal.SIGKILL)
                    process.wait()

    def prepare_validation(self, directory, raw):
        directory = Path(directory)
        _, formatting, file_format = parse_dataset(raw)
        filename = "validation." + file_format
        (directory / "data" / filename).write_bytes(raw)
        info_path = directory / "data" / "dataset_info.json"
        info = json.loads(info_path.read_text())
        descriptor = {"file_name": filename, "formatting": formatting,
                      "columns": {"prompt": "instruction", "query": "input", "response": "output"}}
        if formatting == "sharegpt":
            descriptor.update(columns={"messages": "messages"}, tags={
                "role_tag": "role", "content_tag": "content", "user_tag": "user",
                "assistant_tag": "assistant", "system_tag": "system"})
        info["factory_validation"] = descriptor
        info_path.write_text(json.dumps(info), encoding="utf-8")

    def predict(self, config, directory, validation_sha256, timeout, canceled=lambda: False):
        directory = Path(directory)
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
            plan.update(quantization_bit=4, quantization_method="bnb")
        (directory / "predict.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        self.run_config(directory, timeout, canceled, "predict.yaml", "predict.log")
        if config.training_method == "qlora":
            self.confirm_quantization(directory / "predict.log")
        source = directory / "prediction" / "generated_predictions.jsonl"
        rows = [json.loads(line) for line in source.read_text(encoding="utf-8").splitlines() if line.strip()]
        if not rows or len(rows) > profile.max_samples:
            raise ValueError("native prediction result has invalid sample count")
        result = {"predictions": [row["predict"].strip() for row in rows],
                  "references": [row["label"].strip() for row in rows],
                  "validation_sha256": validation_sha256, "source": "llamafactory-native-generation"}
        (directory / "output").mkdir(exist_ok=True)
        (directory / "output" / "predictions.json").write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")

    def resume(self, directory, checkpoint, timeout, canceled=lambda: False):
        directory, checkpoint = Path(directory).resolve(), Path(checkpoint).resolve()
        if not checkpoint.is_relative_to(directory / "output"):
            raise ValueError("checkpoint must belong to this job output")
        required = ["trainer_state.json", "adapter_config.json", "adapter_model.safetensors",
                    "optimizer.pt", "scheduler.pt"]
        if not all((checkpoint / name).is_file() for name in required) or not list(checkpoint.glob("rng_state*.pth")):
            raise ValueError("incomplete resumable checkpoint")
        config = AIConfig.model_validate_json((directory / "config.json").read_text())
        adapter = json.loads((checkpoint / "adapter_config.json").read_text())
        if adapter.get("base_model_name_or_path", config.base_model) != config.base_model:
            raise ValueError("checkpoint model incompatible")
        plan = yaml.safe_load((directory / "train.yaml").read_text())
        plan["resume_from_checkpoint"] = checkpoint.relative_to(directory).as_posix()
        (directory / "train.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        self.train(directory, timeout, canceled)

    def collect_artifacts(self, directory):
        directory = Path(directory)
        return {p.relative_to(directory).as_posix(): digest(p.read_bytes())
                for p in sorted(directory.rglob("*")) if p.is_file() and not p.is_symlink()
                  and p.name not in {"artifacts.json", "tracking-error.txt"}
                  and p != directory / "cancel"}

    @staticmethod
    def confirm_quantization(log_path):
        if not log_path.is_file() or "Quantizing model to 4 bit with bitsandbytes." not in log_path.read_text(
            encoding="utf-8", errors="replace"
        ):
            raise ValueError("4-bit quantization was not confirmed by LLaMA-Factory; refusing full-precision fallback")

    def validate_training_result(self, directory, config=None):
        quantized = config is not None and config.training_method == "qlora"
        if quantized:
            self.confirm_quantization(Path(directory) / "train.log")
        state_path = Path(directory) / "output" / "trainer_state.json"
        if not state_path.is_file():
            return
        state = json.loads(state_path.read_text())
        checked = 0
        for item in state.get("log_history", []):
            for key in ("loss", "train_loss", "grad_norm"):
                if key in item:
                    checked += 1
                    if not math.isfinite(float(item[key])):
                        raise ValueError(f"non-finite {key} in training state; review precision and optimizer configuration")
        health = {"finite_statistics_checked": checked, "global_step": state.get("global_step")}
        if quantized:
            health["quantization_confirmed_bits"] = 4
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
