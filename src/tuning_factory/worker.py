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

from .config import AIConfig
from .manifest import source_hash, verify_manifest
from .registry import canonical, digest
from .training import CanceledError, LlamaFactoryBackend


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
        if importlib.metadata.version("llamafactory") != "0.9.5":
            raise ValueError("worker engine version differs from manifest")
        lock_path = Path(os.environ.get("FACTORY_GPU_LOCK_FILE", "/opt/factory/containers/gpu-requirements.lock"))
        expected_lock = manifest["dependency_file_hashes"].get("containers/gpu-requirements.lock")
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
        config = AIConfig.model_validate(manifest["config"])
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
        if args.checkpoint:
            backend.resume(directory, directory / args.checkpoint, args.timeout, canceled)
        else:
            backend.train(directory, args.timeout, canceled)
        required = ["adapter_config.json", "adapter_model.safetensors"]
        if not all((directory / "output" / name).is_file() for name in required):
            raise RuntimeError("training exited without final LoRA adapter")
        backend.validate_training_result(directory, config)
        if validation:
            remaining = args.timeout - (time.monotonic() - started)
            if remaining <= 0:
                raise TimeoutError("job budget exhausted before evaluation generation")
            backend.predict(config, directory, validation["sha256"], remaining, canceled)
        result = {"status": "COMPLETED", "engine_version": "0.9.5"}
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
