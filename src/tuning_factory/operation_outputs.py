"""Control-plane freezing of a completed v2 operation's outputs, with exact lineage (Task 7.5).

The worker returns files; this module decides what becomes an immutable artifact. Every input is a file of
the launch directory that the executor downloaded hash-verified, and every rule is re-checked here rather than
trusted from the GPU host:

- The result must name this attempt, launch, manifest, operation and kind, and its evidence files must match.
- Train: an allowlist of the engine's payload (adapter config/weights or model shards and index, value head,
  generation config). README, checkpoints, ``training_args.bin`` and optimizer states never enter an artifact.
  ``config.json`` (adapters) and the tokenizer come byte-for-byte from the effective base, through the worker's
  ``base-snapshot/``, checked against the base's own fingerprints. A tokenizer the engine re-saved may differ
  in bytes, never in vocabulary or special-token ids.
- Merge/materialize: ``model-op/output`` is frozen as it is; its inventory must equal ``operation-receipt.json``.
- Full-model outputs bind ``target-inventory.json`` to the frozen identity through ``verify_inspected_targets``.
- Evaluate: the worker returns predictions only. References come from the registered rows in benchmark order,
  and metrics from ``CommonEvaluator``, under ``BenchmarkSpec.fingerprint()``.

Module import touches only the standard library, pydantic and local contracts: no torch, graph_store or jobs.
"""
from __future__ import annotations

import json
import os
import re
import shutil
import tempfile
from collections.abc import Mapping
from pathlib import Path

from .benchmarks import EvaluationRecord
from .dataset_splits import load_dataset
from .evaluation import CommonEvaluator
from .model_artifacts import (
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    ModelDescriptor,
    check_link_free,
    directory_inventory_sha256,
    is_processor_file,
    is_tokenizer_file,
    list_files,
    read_json,
    tensor_header,
)
from .model_ops import (
    POLICY_F32_V1,
    MaterializeReceipt,
    MergeReceipt,
    adapter_metadata_from_config,
    local_config_sha256,
    local_tokenizer_fingerprint,
    verify_inspected_targets,
)
from .operation_dispatch import (
    BASE_SNAPSHOT,
    MODEL_OP_PARENT,
    OPERATION_RECEIPT,
    TARGET_INVENTORY,
    inspects_full_output,
)
from .operation_runner import ResolvedModelInputs, operation_sha256, validated_operation
from .operations import EvaluateOperation, MaterializeOperation, MergeOperation, TrainOperation
from .registry import canonical, digest, file_digest
from .runtime_profiles import operation_profile
from .training import OFT_UNFREEZABLE
from .worker_protocol import OperationResult

FOREIGN_RESULT = "remote result belongs to a different attempt"   # the executor's identity refusal, verbatim
VOCABULARY_CHANGED = "engine tokenizer output changes the vocabulary; refusing artifact"
TRAIN_OUTPUT = "output"
PREDICTIONS = "output/predictions.json"
_SHARD = re.compile(r"model-\d+-of-\d+\.safetensors")
_COMMON_PAYLOAD = frozenset({"generation_config.json", "value_head.safetensors"})
ADAPTER_PAYLOAD = frozenset({"adapter_config.json", "adapter_model.safetensors", *_COMMON_PAYLOAD})
FULL_PAYLOAD = frozenset({"config.json", "model.safetensors", "model.safetensors.index.json", *_COMMON_PAYLOAD})


def _key(ref: ModelArtifactRef):
    return ref.artifact_id, ref.inventory_sha256


def _sorted_refs(refs) -> tuple[ModelArtifactRef, ...]:
    unique = {_key(ref): ref for ref in refs}
    return tuple(unique[key] for key in sorted(unique))


def _payload_name(name: str, *, adapter: bool) -> bool:
    return name in (ADAPTER_PAYLOAD if adapter else FULL_PAYLOAD) or not adapter and bool(_SHARD.fullmatch(name))


# -- the result and its evidence ----------------------------------------------------------------

def check_result(operation, result: OperationResult, *, attempt_id: str, manifest_sha256: str,
                 launch_dir: Path) -> OperationResult:
    """The result must be COMPLETED for exactly this attempt and launch, and its evidence files must match."""
    result = OperationResult.model_validate(result.model_dump(mode="json"))
    if result.status != "COMPLETED":
        raise ValueError(f"remote operation is not completed: {result.status}")
    launch_dir = Path(launch_dir)
    if ((result.launch_id, result.attempt_id, result.manifest_sha256, result.operation_sha256, result.kind)
            != (launch_dir.name, attempt_id, manifest_sha256, operation_sha256(operation), operation.kind)):
        raise ValueError(FOREIGN_RESULT)
    for name, expected in result.evidence.items():
        path = launch_dir / name
        check_link_free(path)
        if not path.is_file() or file_digest(path) != expected:
            raise ValueError(f"launch evidence differs from the accepted result: {name}")
    return result


def _evidence_file(result: OperationResult, launch_dir: Path, name: str) -> Path:
    if name not in result.evidence:
        raise ValueError(f"accepted result carries no evidence for {name}")
    return Path(launch_dir) / name   # check_result already matched its bytes


# -- tokenizer and base snapshot ----------------------------------------------------------------

def _json_file(path: Path):
    """A tokenizer JSON file, or None. tokenizer.json of a large vocabulary can exceed the metadata limit."""
    if not path.is_file():
        return None
    check_link_free(path)
    return json.loads(path.read_bytes())


def vocabulary(directory: Path) -> dict:
    """The token-id facts a tokenizer snapshot defines: vocabulary, added tokens and special-token ids.

    Special-token *assignments* (an engine may set ``pad_token`` to EOS) are not part of it.
    """
    directory, facts = Path(directory), {}
    tokenizer = _json_file(directory / "tokenizer.json")
    if isinstance(tokenizer, Mapping):
        model = tokenizer.get("model") if isinstance(tokenizer.get("model"), Mapping) else {}
        facts["tokenizer.json"] = {"vocab": model.get("vocab"), "added": sorted(
            (item.get("id"), item.get("content"), bool(item.get("special")))
            for item in tokenizer.get("added_tokens") or () if isinstance(item, Mapping))}
    config = _json_file(directory / "tokenizer_config.json")
    if isinstance(config, Mapping):
        decoder = config.get("added_tokens_decoder") or {}
        facts["tokenizer_config.json"] = sorted(
            (str(key), value.get("content"), bool(value.get("special")))
            for key, value in decoder.items() if isinstance(value, Mapping))
    for name in ("vocab.json", "added_tokens.json"):
        if (directory / name).is_file():
            facts[name] = _json_file(directory / name)
    for name in ("vocab.txt", "tokenizer.model", "spiece.model"):
        if (directory / name).is_file():
            facts[name] = file_digest(directory / name)
    return facts


def check_engine_tokenizer(output: Path, base: Path) -> None:
    """Refuse an engine-written tokenizer whose vocabulary or special-token ids differ from the base's."""
    engine, original = vocabulary(output), vocabulary(base)
    if any(name in original and facts != original[name] for name, facts in engine.items()):
        raise ValueError(VOCABULARY_CHANGED)


def _base_identity(base, verifier) -> tuple[str, str]:
    if isinstance(base, HFRootRef):
        return base.config_sha256, base.tokenizer_fingerprint
    descriptor = verifier.verify(base)
    return descriptor.config_sha256, descriptor.tokenizer_fingerprint


def _base_snapshot(launch_dir: Path, result: OperationResult, identity: tuple[str, str]) -> Path:
    """``base-snapshot/``: exactly config.json, tokenizer and processor files, each in the evidence, with the
    base's config hash and tokenizer fingerprint."""
    snapshot = Path(launch_dir) / BASE_SNAPSHOT
    names = {relative for relative, _ in list_files(snapshot)}
    if any(not (name == "config.json" or is_tokenizer_file(name) or is_processor_file(name)) for name in names) or any(
            f"{BASE_SNAPSHOT}/{name}" not in result.evidence for name in names):
        raise ValueError("base snapshot holds files outside the accepted config/tokenizer evidence")
    if (local_config_sha256(snapshot), local_tokenizer_fingerprint(snapshot)) != identity:
        raise ValueError("base snapshot differs from the effective base config/tokenizer fingerprints")
    return snapshot


# -- descriptors --------------------------------------------------------------------------------

def _place(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    try:
        os.link(source, target)   # a hard link is a regular file; freeze copies it into the store
    except OSError:
        shutil.copyfile(source, target)


def _tensors(directory: Path):
    return tuple(sorted((tensor for relative, file in list_files(directory) if relative.endswith(".safetensors")
                         for tensor in tensor_header(file, relative)), key=lambda item: (item.file, item.name)))


def _synthetic(verifier, refs, *flags) -> bool:
    return any(flags) or any(verifier.verify(ref).synthetic for ref in refs)


def _train_output(operation: TrainOperation, result, launch_dir: Path, verifier, workspace: Path, ids: dict,
                  dataset_synthetic: bool) -> tuple[Path, ModelDescriptor]:
    config, model_input = operation.config, operation.model_input
    if config.training_method == "oft":
        raise ValueError(OFT_UNFREEZABLE)   # refused at planning and before launch; kept as a last guard
    adapter = config.training_method in {"lora", "qlora"}
    policy = model_input.policy
    base_config, base_tokenizer = _base_identity(policy.base, verifier)
    snapshot = _base_snapshot(launch_dir, result, (base_config, base_tokenizer))
    output = Path(launch_dir) / TRAIN_OUTPUT
    check_link_free(output)
    check_engine_tokenizer(output, snapshot)
    payload = sorted(path.name for path in output.iterdir()
                     if path.is_file() and _payload_name(path.name, adapter=adapter))
    for name in payload:
        _place(output / name, workspace / name)
    for relative, file in list_files(snapshot):
        if is_tokenizer_file(relative) or is_processor_file(relative) or adapter and relative == "config.json":
            _place(file, workspace / relative)
    metadata = None
    if adapter:
        options = config.finetuning_options
        metadata = adapter_metadata_from_config(
            read_json(workspace / "adapter_config.json"), method=config.training_method,
            quantization_bit=config.quantization_bit if config.training_method == "qlora" else None,
            loraplus_lr_ratio=options.loraplus_lr_ratio, loraplus_lr_embedding=options.loraplus_lr_embedding)
    else:
        trained, original = read_json(workspace / "config.json"), read_json(snapshot / "config.json")
        if not isinstance(trained, Mapping) or trained.get("vocab_size") != original.get("vocab_size"):
            raise ValueError(VOCABULARY_CHANGED)
    root = policy.base if isinstance(policy.base, HFRootRef) else verifier.verify(policy.base).root
    loads = [load for load in (policy, model_input.frozen_reference, model_input.reward_model) if load]
    inputs = [ref for load in loads for ref in (load.base, load.adapter) if isinstance(ref, ModelArtifactRef)]
    descriptor = ModelDescriptor(
        **ids, storage_kind="adapter" if adapter else "full_model",
        semantic_kind="reward_model" if config.training_stage == "rm" else "causal_lm",
        has_value_head=config.training_stage in {"rm", "ppo"}, root=root, effective_base=policy.base,
        parents=_sorted_refs(ref for ref in (policy.base, policy.adapter) if isinstance(ref, ModelArtifactRef)),
        config_sha256=base_config if adapter else local_config_sha256(workspace),
        tokenizer_fingerprint=base_tokenizer, adapter=metadata, tensors=_tensors(workspace),
        synthetic=_synthetic(verifier, inputs, dataset_synthetic), simulation=operation.lineage.simulation)
    return workspace, descriptor


def operation_receipt(result: OperationResult, launch_dir: Path, operation) -> dict:
    """``operation-receipt.json`` of a merge/materialize, checked against this operation."""
    receipt = read_json(_evidence_file(result, launch_dir, OPERATION_RECEIPT))
    model = MergeReceipt if isinstance(operation, MergeOperation) else MaterializeReceipt
    inner = model.model_validate(receipt.get("receipt") if isinstance(receipt, Mapping) else None)
    if (receipt.get("schema_version"), receipt.get("kind"), receipt.get("output"), inner.policy_id) != (
            1, operation.kind, f"{MODEL_OP_PARENT}/output", POLICY_F32_V1):
        raise ValueError("operation receipt differs from this operation")
    return {**receipt, "receipt": inner}


def _model_op_output(operation, result, launch_dir: Path, verifier, ids: dict) -> tuple[Path, ModelDescriptor]:
    receipt = operation_receipt(result, launch_dir, operation)
    inner, output = receipt["receipt"], Path(launch_dir) / MODEL_OP_PARENT / "output"
    merge = isinstance(operation, MergeOperation)
    names = [relative for relative, _ in list_files(output)]
    if sorted(inner.output_files) != names or any(
            not (_payload_name(name, adapter=merge) or is_tokenizer_file(name) or is_processor_file(name)
                 or name == "config.json")
            for name in names):
        raise ValueError("model-operation output differs from its receipt or the artifact file allowlist")
    if merge:
        inputs = tuple(verifier.verify(ref) for ref in operation.models)
        if inner.inputs != operation.models or inner.output_rank != sum(item.adapter.rank for item in inputs):
            raise ValueError("merge receipt differs from this operation")
        first, refs = inputs[0], operation.models
        variants = {(item.adapter.method, item.adapter.quantization_bit) for item in inputs}
        method, bit = variants.pop() if len(variants) == 1 else ("lora", None)
        metadata = adapter_metadata_from_config(read_json(output / "adapter_config.json"), method=method,
                                                quantization_bit=bit)
        if metadata.rank != inner.output_rank:
            raise ValueError("merged adapter rank differs from its receipt")
    else:
        first = verifier.verify(operation.model)
        refs, metadata = (operation.model,), None
        if (inner.base, inner.adapter) != (first.effective_base, operation.model):
            raise ValueError("materialize receipt differs from this operation")
    base = first.effective_base
    base_config, base_tokenizer = _base_identity(base, verifier)
    config_sha256 = local_config_sha256(output)
    if local_tokenizer_fingerprint(output) != base_tokenizer or merge and config_sha256 != base_config:
        raise ValueError("model-operation output config/tokenizer differs from the effective base")
    parents = (*refs, *((base,) if isinstance(base, ModelArtifactRef) else ()))
    descriptor = ModelDescriptor(
        **ids, storage_kind="adapter" if merge else "full_model", semantic_kind="causal_lm", has_value_head=False,
        root=first.root, effective_base=base, parents=_sorted_refs(parents), config_sha256=config_sha256,
        tokenizer_fingerprint=base_tokenizer, quantization_bit=None, adapter=metadata, tensors=_tensors(output),
        synthetic=_synthetic(verifier, parents), simulation=operation.lineage.simulation)
    return output, descriptor


def build_output_descriptor(operation, result: OperationResult, *, job_id: str, attempt_id: str,
                            manifest_sha256: str, launch_dir: Path, resolved: ResolvedModelInputs,
                            approved_cache: ModelArtifactStore, workspace: Path,
                            dataset_synthetic: bool = False) -> tuple[Path, ModelDescriptor]:
    """The directory to freeze and its descriptor, with the ledger's producer identities.

    ``workspace`` is an empty directory the caller owns (train outputs are assembled there); merge and
    materialize return their receipted ``model-op/output`` itself.
    """
    operation = validated_operation(operation)
    result = check_result(operation, result, attempt_id=attempt_id, manifest_sha256=manifest_sha256,
                          launch_dir=launch_dir)
    resolved = ResolvedModelInputs.model_validate(resolved.model_dump(mode="json"))
    if (resolved.operation_kind, resolved.operation_sha256) != (operation.kind, operation_sha256(operation)):
        raise ValueError("staged model inputs belong to another operation")
    if not isinstance(approved_cache, ModelArtifactStore):
        raise TypeError("freezing requires the approved control-plane ModelArtifactStore")
    verifier = approved_cache.verifier()
    ids = {"producer_job_id": job_id, "producer_attempt_id": attempt_id, "producer_manifest_sha256": manifest_sha256}
    if isinstance(operation, TrainOperation):
        return _train_output(operation, result, Path(launch_dir), verifier, Path(workspace), ids, dataset_synthetic)
    if isinstance(operation, (MergeOperation, MaterializeOperation)):
        return _model_op_output(operation, result, Path(launch_dir), verifier, ids)
    raise ValueError("an evaluation produces an evaluation record, not a model artifact")


def inspected_targets(result: OperationResult, launch_dir: Path, ref: ModelArtifactRef,
                      descriptor: ModelDescriptor) -> tuple:
    """``target-inventory.json`` bound to the frozen full model and accepted only through the verifier."""
    from .graph_planner import ModelTargetEvidence
    if TARGET_INVENTORY not in result.evidence:
        return ()
    inventory = read_json(_evidence_file(result, launch_dir, TARGET_INVENTORY))
    if not isinstance(inventory, Mapping) or inventory.get("schema_version") != 1:
        raise ValueError("invalid target inventory")
    evidence = ModelTargetEvidence(base=ref, modules=tuple(inventory.get("modules") or ()))
    return (verify_inspected_targets(evidence, descriptor, ref),)


def freeze_operation_output(operation, result: OperationResult, *, job_id: str, attempt_id: str,
                            manifest_sha256: str, launch_dir: Path, resolved: ResolvedModelInputs,
                            approved_cache: ModelArtifactStore, dataset_synthetic: bool = False):
    """Freeze a model output (idempotent: identical bytes and descriptor give the same reference).

    Returns ``(ref, inspected_targets)``. A merge/materialize must freeze exactly its receipted inventory, and
    every full model the worker inspects (materialize; full/freeze training in the primary runtime) must carry
    its verified target inventory. Both are checked against the identity the store will give these exact bytes
    (the descriptor hash and ``directory_inventory_sha256``) before the store is written, so a refused output
    never leaves an artifact.
    """
    operation = validated_operation(operation)
    launch_dir = Path(launch_dir)
    model_op = isinstance(operation, (MergeOperation, MaterializeOperation))
    if TARGET_INVENTORY not in result.evidence and inspects_full_output(
            operation, operation_profile(operation.kind, operation.resources.runtime.profile)):
        raise ValueError("a full-model output of the primary runtime requires its inspected target inventory")
    workspace = Path(tempfile.mkdtemp(prefix=".freeze-", dir=launch_dir.parent))
    try:
        source, descriptor = build_output_descriptor(
            operation, result, job_id=job_id, attempt_id=attempt_id, manifest_sha256=manifest_sha256,
            launch_dir=launch_dir, resolved=resolved, approved_cache=approved_cache, workspace=workspace,
            dataset_synthetic=dataset_synthetic)
        expected = ModelArtifactRef(artifact_id=digest(canonical(descriptor.model_dump(mode="json"))),
                                    inventory_sha256=directory_inventory_sha256(source))
        if model_op and expected.inventory_sha256 != operation_receipt(result, launch_dir, operation)[
                "output_inventory_sha256"]:
            raise ValueError("model-operation output inventory differs from operation-receipt.json")
        targets = inspected_targets(result, launch_dir, expected, descriptor)
        ref = approved_cache.freeze(source, descriptor)
    finally:
        shutil.rmtree(workspace, ignore_errors=True)
    if ref != expected:   # only if the bytes changed between the checks and the copy into the store
        raise ValueError("frozen output differs from the identity checked before freezing")
    return ref, targets


# -- evaluation records -------------------------------------------------------------------------

def _reference(row, schema) -> str:
    """The registered answer LLaMA-Factory scores against: alpaca ``output`` or the final assistant turn."""
    if schema["formatting"] == "sharegpt":
        messages = row.get(schema["message_key"]) or []
        modern = schema["message_key"] == "messages"
        last = messages[-1] if messages else {}
        role, content = (last.get("role"), last.get("content")) if modern else (last.get("from"), last.get("value"))
        value = content if role in {"assistant", "gpt"} else None
    else:
        value = row.get("output")
    if not isinstance(value, str):
        raise ValueError("benchmark row has no registered reference answer")
    return value.strip()


def evaluation_record(operation: EvaluateOperation, result: OperationResult, *, attempt_id: str,
                      manifest_sha256: str, launch_dir: Path, registry, approved_cache: ModelArtifactStore
                      ) -> EvaluationRecord:
    """Recompute references and metrics on the control plane from predictions and the registered rows."""
    from .graph_planner import snapshot_dataset, validate_sample_ids
    operation = validated_operation(operation)
    if not isinstance(operation, EvaluateOperation):
        raise ValueError("an evaluation record requires an evaluate operation")
    result = check_result(operation, result, attempt_id=attempt_id, manifest_sha256=manifest_sha256,
                          launch_dir=launch_dir)
    benchmark = operation.benchmark
    data = json.loads(_evidence_file(result, launch_dir, PREDICTIONS).read_bytes())
    data = data if isinstance(data, Mapping) else {}
    predictions = data.get("predictions")
    if (data.get("ordered_sample_ids") != list(benchmark.ordered_sample_ids) or not isinstance(predictions, list)
            or len(predictions) != len(benchmark.ordered_sample_ids)
            or not all(isinstance(item, str) for item in predictions)):
        raise ValueError("predictions differ from the benchmark's ordered samples")
    ordinals = validate_sample_ids(benchmark, snapshot_dataset(registry, benchmark.dataset))
    record, rows, schema = load_dataset(registry, benchmark.dataset)
    references = None
    if benchmark.evaluator_kind not in {"json", "pipeline"}:
        references = [_reference(rows[ordinal], schema) for ordinal in ordinals]
    metrics = CommonEvaluator().evaluate(predictions, references, benchmark.evaluator_kind)
    model = approved_cache.verify(operation.model)
    return EvaluationRecord(model=operation.model, benchmark_sha256=benchmark.fingerprint(), metrics=metrics,
                            status="completed", synthetic=bool(record.get("synthetic")) or model.synthetic,
                            simulation=operation.lineage.simulation, producer_attempt_id=attempt_id)
