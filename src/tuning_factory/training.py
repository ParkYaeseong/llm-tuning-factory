"""Training engine interface and the only production engine adapter."""
import json
import os
import signal
import subprocess
import time
from abc import ABC, abstractmethod
from pathlib import Path

import yaml

from .config import AIConfig
from .registry import digest


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
        (directory / "data" / "dataset.json").write_bytes(dataset)
        (directory / "data" / "dataset_info.json").write_text(json.dumps({
            "factory_dataset": {"file_name": "dataset.json", "formatting": "alpaca",
                                "columns": {"prompt": "instruction", "query": "input", "response": "output"}}
        }), encoding="utf-8")
        plan = {"model_name_or_path": config.base_model, "model_revision": config.model_revision,
                "trust_remote_code": False, "stage": "sft", "do_train": True,
                "finetuning_type": "lora", "template": config.template,
                "dataset": "factory_dataset", "dataset_dir": "./data", "output_dir": "./output",
                "overwrite_output_dir": False, "report_to": "none", "logging_steps": 1,
                "save_only_model": False, "fp16": True, **config.hyperparameters.model_dump()}
        if config.training_method == "qlora":
            plan.update(quantization_bit=4, quantization_method="bitsandbytes")
        (directory / "train.yaml").write_text(yaml.safe_dump(plan), encoding="utf-8")
        (directory / "config.json").write_text(config.model_dump_json(), encoding="utf-8")

    def train(self, directory, timeout, canceled=lambda: False):
        directory = Path(directory).resolve()
        with (directory / "train.log").open("ab") as log:
            process = subprocess.Popen([*self.command, "train.yaml"], cwd=directory,
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
                and p.name not in {"artifacts.json", "tracking-error.txt"}}

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
