"""GPU-side entrypoint. No credentials or control-plane DB required.

v1: ``worker DIRECTORY TIMEOUT [--checkpoint PATH]`` (unchanged). v2: ``worker DIRECTORY --request SHA256``
runs the hash-verified ``command.json`` at most once per launch directory. ``worker --verify-model-cache
DIR ARTIFACT_ID INVENTORY_SHA256`` verifies one model-cache entry (exit 0/1). No other command is accepted.
"""
import argparse
import importlib.metadata
import json
import os
import platform
import re
import sys
import time
import traceback
from datetime import datetime, timezone
from pathlib import Path

import yaml
from pydantic import TypeAdapter

from .capabilities import compatibility
from .config import AIConfig
from .manifest import source_hash, verify_manifest
from .model_artifacts import ModelArtifactRef, check_link_free
from .operation_dispatch import execute_operation
from .operation_runner import verify_cached_artifact
from .operations import EvaluateOperation, OperationSpec, TrainOperation
from .registry import canonical, digest, file_digest
from .runtime_profiles import engine_profile, operation_profile
from .training import CanceledError, LlamaFactoryBackend
from .worker_protocol import (
    CACHE_ENTRY_INVALID,
    OPERATION_KINDS,
    OperationResult,
    StartMarker,
    create_start_marker,
    is_v2_directory,
    read_worker_request,
    write_operation_result,
)

HEX32 = re.compile(r"^[0-9a-f]{32}$")
HEX64 = re.compile(r"^[0-9a-f]{64}$")
LOCAL_ONLY = {"local_files_only": True, "trust_remote_code": False}
_clock = time.monotonic   # the attempt deadline's clock (replaced in tests)
TOKEN_IDS = ("bos_token_id", "eos_token_id", "pad_token_id", "unk_token_id")


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
        if not _same_tokenizer(policy_tokenizer, reward_tokenizer):
            raise ValueError("PPO reward model tokenizer must match the pinned policy tokenizer")
        facts["reward_tokenizer_verified"] = True
    return facts


def _same_tokenizer(policy_tokenizer, reward_tokenizer):
    return policy_tokenizer.get_vocab() == reward_tokenizer.get_vocab() and all(
        getattr(policy_tokenizer, name, None) == getattr(reward_tokenizer, name, None) for name in TOKEN_IDS)


def _bound_facts(torch, observed, base):
    text = getattr(observed, "text_config", observed)
    return text, {"cuda_available": torch.cuda.is_available(), "gpu_count": torch.cuda.device_count(),
                  "bf16_supported": torch.cuda.is_bf16_supported() if torch.cuda.is_available() else False,
                  "model_type": observed.model_type, "num_hidden_layers": getattr(text, "num_hidden_layers", None),
                  "bound_model": Path(base).as_posix()}


def _check_context(cutoff, text, fallback=None):
    context = getattr(text, "max_position_embeddings", fallback)
    if context is not None and cutoff > context:
        raise ValueError("cutoff length exceeds the bound model context length")


def runtime_preflight_bound(config, paths):
    """runtime_preflight on the bound local policy model and tokenizer; config.base_model is never loaded.

    ``paths`` is operation_runner.VerifiedModelPaths. A full PPO reward's tokenizer is compared on its
    bound path with the same vocabulary/special-token rule as v1.
    """
    import torch
    from transformers import AutoConfig, AutoTokenizer
    base = str(paths.path("policy_base"))
    text, facts = _bound_facts(torch, AutoConfig.from_pretrained(base, **LOCAL_ONLY), base)
    _check_context(config.hyperparameters.cutoff_len, text,
                   config.model_profile.max_length if config.model_profile else None)
    validate_runtime_facts(config, facts)
    policy_tokenizer = AutoTokenizer.from_pretrained(base, **LOCAL_ONLY)
    facts["tokenizer_vocabulary_size"] = len(policy_tokenizer.get_vocab())
    roles = {item.role for item in paths.models}
    if config.training_stage == "ppo" and "reward_base" in roles and "reward_adapter" not in roles:
        reward_tokenizer = AutoTokenizer.from_pretrained(str(paths.path("reward_base")), **LOCAL_ONLY)
        if not _same_tokenizer(policy_tokenizer, reward_tokenizer):
            raise ValueError("PPO reward model tokenizer must match the pinned policy tokenizer")
        facts["reward_tokenizer_verified"] = True
    return facts


def evaluation_preflight_bound(benchmark, paths, gpu_count=1):
    """GPU, precision and context checks for a v2 evaluation on the bound local model and tokenizer."""
    import torch
    from transformers import AutoConfig, AutoTokenizer
    base = str(paths.path("operation_base"))
    text, facts = _bound_facts(torch, AutoConfig.from_pretrained(base, **LOCAL_ONLY), base)
    if not facts["cuda_available"] or facts["gpu_count"] < gpu_count:
        raise ValueError("CUDA GPU availability does not satisfy the resolved resource profile")
    if benchmark.precision == "bf16" and not facts["bf16_supported"]:
        raise ValueError("BF16 is unsupported on this GPU; select FP16 or a supported GPU")
    _check_context(benchmark.cutoff_len, text)
    facts["tokenizer_vocabulary_size"] = len(AutoTokenizer.from_pretrained(base, **LOCAL_ONLY).get_vocab())
    return facts


def verify_model_cache(root, artifact_id, inventory_sha256):
    """Fixed-argv cache check for the SSH transport (7.4); never deletes.

    Exit 0: verified. CACHE_ENTRY_INVALID: the entry was read and fails verification (hash, layout, link).
    1: it could not be read (missing, permissions, I/O), so nothing is known about it. 2: invalid identifiers.
    """
    try:
        ref = ModelArtifactRef(artifact_id=artifact_id, inventory_sha256=inventory_sha256)
    except ValueError as exc:
        print(f"invalid model cache identifiers: {exc}", file=sys.stderr)
        return 2
    try:
        verify_cached_artifact(Path(root), ref)
    except ValueError as exc:
        unreadable = isinstance(exc.__cause__, OSError)   # verify_artifact_directory wraps OSError
        print(f"model cache entry {'could not be read' if unreadable else 'failed verification'}: {exc}",
              file=sys.stderr)
        return 1 if unreadable else CACHE_ENTRY_INVALID
    except Exception as exc:
        print(f"model cache entry could not be checked: {exc}", file=sys.stderr)
        return 1
    return 0


def _absolute_setting(name):
    value = os.environ.get(name)
    if value and not Path(value).is_absolute():
        raise ValueError(f"{name} must be an absolute path")
    return Path(value) if value else None


def _runtime_versions():
    packages = {}
    for name in ("llamafactory", "torch", "transformers", "peft", "safetensors", "datasets", "accelerate",
                 "bitsandbytes"):
        try:
            packages[name] = importlib.metadata.version(name)
        except importlib.metadata.PackageNotFoundError:
            packages[name] = None
    return {"python": platform.python_version(), "packages": packages}


def _check_manifest_datasets(manifest, operation):
    if isinstance(operation, TrainOperation):
        expected = [operation.dataset]
    elif isinstance(operation, EvaluateOperation):
        expected = [operation.benchmark.dataset]
    else:
        expected = []
    datasets = manifest.get("datasets")
    if not isinstance(datasets, list) or [item.get("dataset") if isinstance(item, dict) else None
                                          for item in datasets] != [ref.model_dump(mode="json") for ref in expected]:
        raise ValueError("manifest datasets differ from the operation")
    if isinstance(operation, EvaluateOperation) and datasets[0].get("kind") != "instruction":
        raise ValueError("v2 evaluation supports greedy causal-LM generation benchmarks only")


def main_v2(directory, command_sha256):
    """Run one hash-verified ``command.json`` at most once in ``<job_id>/<launch_id>``.

    Only the process that creates ``start.json`` (O_CREAT|O_EXCL) writes anything else in the
    directory: a second start, for example after a lost start response, exits 3 and touches nothing.
    Exit 2: not a 32-hex job/launch directory (nothing written). Otherwise ``artifacts.json`` and then
    ``result.json`` are always published, with identities only as far as they were verified. One
    monotonic deadline (start + request timeout) bounds the attempt; dispatch gets what is left of it.
    """
    directory = Path(directory).absolute()
    launch_id = directory.name
    if not (HEX32.match(launch_id) and HEX32.match(directory.parent.name) and HEX64.match(command_sha256)):
        print("worker v2 requires a <job_id>/<launch_id> directory and the SHA256 of command.json", file=sys.stderr)
        return 2
    try:
        check_link_free(directory)
        if not directory.is_dir():
            raise ValueError("launch directory is missing")
    except (OSError, ValueError) as exc:
        print(f"invalid launch directory: {exc}", file=sys.stderr)
        return 2
    started = _clock()
    request = failure = None
    try:
        request = read_worker_request(directory, command_sha256, launch_id)
    except Exception as exc:  # recorded in the result, but only by the start marker's creator
        failure = exc
    marker = StartMarker(launch_id=launch_id, command_sha256=command_sha256, pid=os.getpid(),
                         started_at=datetime.now(timezone.utc).isoformat(),
                         **({} if request is None else {"attempt_id": request.attempt_id,
                                                        "manifest_sha256": request.manifest_sha256,
                                                        "operation_sha256": request.operation_sha256}))
    if not create_start_marker(directory, marker):
        print("remote launch already started for this attempt; reconcile instead of relaunching", file=sys.stderr)
        return 3
    backend = LlamaFactoryBackend()
    identity = {"attempt_id": None, "manifest_sha256": None, "operation_sha256": None, "kind": None}
    result = None
    try:
        if failure is not None:
            raise failure
        identity["attempt_id"] = request.attempt_id
        manifest = json.loads((directory / "manifest.json").read_bytes())
        verify_manifest(manifest)
        if manifest.get("schema_version") != 2:
            raise ValueError("worker request requires a schema 2 operation manifest")
        if manifest["sha256"] != request.manifest_sha256:
            raise ValueError("worker request manifest differs from manifest.json")
        identity["manifest_sha256"] = request.manifest_sha256
        body = manifest.get("operation")
        if not isinstance(body, dict) or digest(canonical(body)) != request.operation_sha256:
            raise ValueError("worker request operation hash differs from manifest operation")
        identity["operation_sha256"] = request.operation_sha256
        if body.get("kind") == "select":
            raise ValueError("local selection never dispatches to a GPU worker")
        if body.get("kind") not in OPERATION_KINDS:
            raise ValueError("unsupported operation kind")
        operation = TypeAdapter(OperationSpec).validate_python(body)
        if operation.model_dump(mode="json") != body:
            raise ValueError("manifest operation is not in its resolved form")
        if operation.lineage.simulation:
            raise ValueError("simulation manifest cannot authorize GPU execution")
        if manifest.get("source_sha256") != source_hash():
            raise ValueError("worker source differs from resolved manifest; rebuild image")
        if request.timeout_seconds != operation.resources.timeout_seconds:
            raise ValueError("worker request timeout differs from the operation budget")
        runtime = operation.resources.runtime
        profile = operation_profile(operation.kind, runtime.profile)
        lock_path = Path(os.environ.get("FACTORY_GPU_LOCK_FILE", "/opt/factory/" + profile["lock_file"]))
        if digest(lock_path.read_bytes()) != runtime.dependency_sha256:
            raise ValueError("worker dependency lock differs from operation runtime")
        if importlib.metadata.version("llamafactory") != runtime.engine_version:
            raise ValueError("worker engine version differs from manifest")
        (directory / "runtime.json").write_bytes(canonical(_runtime_versions()))
        _check_manifest_datasets(manifest, operation)
        model_cache, hf_home = _absolute_setting("FACTORY_MODEL_CACHE"), _absolute_setting("HF_HOME")
        if model_cache is None:
            raise ValueError("FACTORY_MODEL_CACHE must name the worker's approved model cache")
        identity["kind"] = operation.kind
        remaining = started + request.timeout_seconds - _clock()   # the one attempt deadline
        if remaining <= 0:
            raise TimeoutError("attempt deadline exceeded before the operation started")

        def canceled():
            return (directory / "cancel").exists()
        completed = execute_operation(operation, directory, remaining, canceled, request=request, backend=backend,
                                      model_cache=model_cache, hf_home=hf_home, preflight=runtime_preflight_bound,
                                      evaluation_preflight=evaluation_preflight_bound)
        result = OperationResult.model_validate({**completed.model_dump(mode="json"), **identity})
    except CanceledError as exc:
        result = OperationResult(status="CANCELED", launch_id=launch_id, error=str(exc), **identity)
    except Exception as exc:
        result = OperationResult(status="FAILED", launch_id=launch_id, error=str(exc), **identity)
        (directory / "worker-error.log").write_text(traceback.format_exc())
    finally:
        # Result is the completion marker: publish only after artifact enumeration is durable.
        (directory / "artifacts.json").write_bytes(canonical(backend.collect_artifacts(directory)))
        write_operation_result(directory, result or OperationResult(
            status="FAILED", launch_id=launch_id, error="worker stopped before producing a result", **identity))
    return 0 if result.status == "COMPLETED" else 1


def main():
    # A worker launched through an isolated venv must delegate to that venv's engine CLI.
    os.environ["PATH"] = str(Path(sys.executable).parent) + os.pathsep + os.environ.get("PATH", "")
    parser = argparse.ArgumentParser()
    parser.add_argument("directory", type=Path, nargs="?")
    parser.add_argument("timeout", type=int, nargs="?")
    parser.add_argument("--checkpoint", type=str)
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--request", metavar="SHA256", help="v2: SHA256 of DIRECTORY/command.json")
    mode.add_argument("--verify-model-cache", nargs=3, metavar=("DIR", "ARTIFACT_ID", "INVENTORY_SHA256"))
    args = parser.parse_args()
    if args.verify_model_cache is not None:
        if args.directory is not None or args.timeout is not None or args.checkpoint is not None:
            parser.error("--verify-model-cache takes no other arguments")
        return verify_model_cache(*args.verify_model_cache)
    if args.request is not None:
        if args.directory is None or args.timeout is not None or args.checkpoint is not None:
            parser.error("--request takes exactly the launch directory")
        if not HEX64.match(args.request):
            parser.error("--request must be the SHA256 of command.json")
        return main_v2(args.directory, args.request)
    if args.directory is None or args.timeout is None:
        parser.error("the following arguments are required: directory, timeout")
    directory = args.directory.resolve()
    if is_v2_directory(directory):
        # Never let a v1 run write (or overwrite) a v2 launch directory's result.json.
        print("v2 operation launch directories require --request; refusing the v1 entry", file=sys.stderr)
        return 2
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
    # v1 exits 0 after publishing its result, or 2 (nothing written) for a v2 directory; v2 and
    # --verify-model-cache return their own status; argument errors exit 2.
    sys.exit(main())
