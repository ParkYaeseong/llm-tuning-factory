"""GPU-side entrypoint. No credentials or control-plane DB required."""
import argparse
import importlib.metadata
import json
import os
import traceback
from pathlib import Path

import yaml

from .config import AIConfig
from .manifest import source_hash, verify_manifest
from .registry import canonical, digest
from .training import CanceledError, LlamaFactoryBackend


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path)
    parser.add_argument("timeout", type=int)
    parser.add_argument("--checkpoint", type=str)
    args = parser.parse_args()
    directory = args.directory.resolve()
    backend = LlamaFactoryBackend()
    result = {"status": "FAILED"}
    try:
        manifest = json.loads((directory / "manifest.json").read_text())
        verify_manifest(manifest)
        if manifest["simulation"]:
            raise ValueError("simulation manifest cannot authorize GPU training")
        if manifest["source_sha256"] != source_hash():
            raise ValueError("worker source differs from resolved manifest; rebuild image")
        if importlib.metadata.version("llamafactory") != "0.9.5":
            raise ValueError("worker engine version differs from manifest")
        if digest((directory / "data" / "dataset.json").read_bytes()) != manifest["dataset"]["sha256"]:
            raise ValueError("transferred dataset hash mismatch")
        config = AIConfig.model_validate(manifest["config"])
        plan = yaml.safe_load((directory / "train.yaml").read_text())
        if digest(canonical(plan)) != manifest["training_config_sha256"]:
            raise ValueError("training YAML hash differs from resolved manifest")
        if plan != backend.translate(config):
            raise ValueError("training YAML differs from resolved configuration")
        def canceled():
            return (directory / "cancel").exists()
        if args.checkpoint:
            backend.resume(directory, directory / args.checkpoint, args.timeout, canceled)
        else:
            backend.train(directory, args.timeout, canceled)
        required = ["adapter_config.json", "adapter_model.safetensors"]
        if not all((directory / "output" / name).is_file() for name in required):
            raise RuntimeError("training exited without final LoRA adapter")
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
