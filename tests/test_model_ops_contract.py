"""Task 7.1: model-operation validation contracts (main env, no torch).

The numerical merge/materialize acceptance runs only in the separate hash-locked
CPU runtime through scripts/verify_model_ops_cpu.py; these tests cover every rule
that does not need torch, and guard that the main env stays torch-free.
"""
import hashlib
import importlib
import importlib.util
import json
import math
import re
import struct
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_model_ops_cpu.py"
HEAVY = ("torch", "peft", "transformers", "safetensors", "huggingface_hub")
MODULES = ("model.layers.0.self_attn.q_proj", "model.layers.0.self_attn.v_proj")


def api():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.model_ops")
    except ModuleNotFoundError:
        pytest.fail("model-operation numerics and validation contracts are missing")


def sha(data):
    return hashlib.sha256(data).hexdigest()


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode()


def block_heavy_imports(monkeypatch):
    for name in HEAVY:
        monkeypatch.setitem(sys.modules, name, None)


# -- safetensors fixtures (hand-written, stdlib only) ---------------------------------------------

def encode(dtype, values):
    if dtype == "F32":
        return b"".join(struct.pack("<f", value) for value in values)
    if dtype == "F64":
        return b"".join(struct.pack("<d", value) for value in values)
    if dtype == "F16":
        return b"".join(struct.pack("<e", value) for value in values)
    if dtype == "BF16":
        return b"".join(struct.pack("<f", value)[2:] for value in values)
    if dtype == "I64":
        return b"".join(struct.pack("<q", int(value)) for value in values)
    raise AssertionError(dtype)


def write_safetensors(path, tensors):
    """tensors: {name: (dtype, shape, raw bytes)} written in insertion order."""
    header, payload = {}, b""
    for name, (dtype, shape, raw) in tensors.items():
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [len(payload), len(payload) + len(raw)]}
        payload += raw
    body = canonical(header)
    path.write_bytes(struct.pack("<Q", len(body)) + body + payload)


def f32(shape, fill=0.01, special=None):
    count = math.prod(shape)
    values = [fill * ((index % 7) + 1) for index in range(count)]
    if special is not None:
        values[count // 2] = special
    return ("F32", shape, encode("F32", values))


# -- descriptor fixtures -------------------------------------------------------------------------

CONFIG = canonical({"model_type": "qwen2", "hidden_size": 16})


def write_snapshot_files(directory, config=CONFIG):
    directory.mkdir(parents=True, exist_ok=True)
    (directory / "config.json").write_bytes(config)
    (directory / "tokenizer_config.json").write_text("{}", encoding="utf-8")
    (directory / "tokenizer.json").write_text('{"version":"1.0"}', encoding="utf-8")


def root_ref(config=CONFIG):
    from tuning_factory.model_artifacts import HFRootRef
    tokenizer = sha(canonical([
        {"path": "tokenizer.json", "size": 17, "sha256": sha(b'{"version":"1.0"}')},
        {"path": "tokenizer_config.json", "size": 2, "sha256": sha(b"{}")},
    ]))
    return HFRootRef(repo_id="test/tiny", revision="a" * 40, config_sha256=sha(config),
                     tokenizer_fingerprint=tokenizer)


def ref_for(descriptor, marker="inventory"):
    from tuning_factory.model_artifacts import ModelArtifactRef
    return ModelArtifactRef(artifact_id=sha(canonical(descriptor.model_dump(mode="json"))),
                            inventory_sha256=sha(marker.encode()))


def adapter_tensors(rank, *, modules=MODULES, in_dim=16, out_dim=16, dtype="F32"):
    from tuning_factory.model_artifacts import TensorMetadata
    tensors = []
    for module in modules:
        tensors.append(TensorMetadata(file="adapter_model.safetensors", name=f"base_model.model.{module}.lora_A.weight",
                                      shape=(rank, in_dim), dtype=dtype))
        tensors.append(TensorMetadata(file="adapter_model.safetensors", name=f"base_model.model.{module}.lora_B.weight",
                                      shape=(out_dim, rank), dtype=dtype))
    return tuple(tensors)


def lora_descriptor(rank=2, alpha=4.0, *, targets=("q_proj", "v_proj"), modules=MODULES, in_dim=16, out_dim=16,
                    effective_base=None, parents=(), config_sha256=None, tokenizer_fingerprint=None,
                    synthetic=False, simulation=False, method="lora", semantic_kind="causal_lm",
                    has_value_head=False, extra_tensors=(), marker="adapter", dtype="F32", **adapter_changes):
    from tuning_factory.model_artifacts import AdapterMetadata, ModelDescriptor
    root = root_ref()
    adapter_changes = {"task_type": "CAUSAL_LM", "inference_mode": True, **adapter_changes}  # PEFT export state
    adapter = AdapterMetadata(method=method, rank=rank, alpha=alpha, target_modules=targets,
                              quantization_bit=4 if method == "qlora" else None, **adapter_changes)
    return ModelDescriptor(
        producer_job_id=marker, producer_attempt_id=marker, producer_manifest_sha256="b" * 64,
        storage_kind="adapter", semantic_kind=semantic_kind, has_value_head=has_value_head, root=root,
        effective_base=effective_base or root, parents=parents,
        config_sha256=config_sha256 or root.config_sha256,
        tokenizer_fingerprint=tokenizer_fingerprint or root.tokenizer_fingerprint, adapter=adapter,
        tensors=(*adapter_tensors(rank, modules=modules, in_dim=in_dim, out_dim=out_dim, dtype=dtype),
                 *extra_tensors),
        synthetic=synthetic, simulation=simulation)


def full_descriptor(tensors, *, quantization_bit=None, marker="sft", config_sha256=None, synthetic=False):
    from tuning_factory.model_artifacts import ModelDescriptor, TensorMetadata
    root = root_ref()
    return ModelDescriptor(
        producer_job_id=marker, producer_attempt_id=marker, producer_manifest_sha256="b" * 64,
        storage_kind="full_model", semantic_kind="causal_lm", has_value_head=False, root=root,
        effective_base=root, config_sha256=config_sha256 or root.config_sha256,
        tokenizer_fingerprint=root.tokenizer_fingerprint, quantization_bit=quantization_bit,
        tensors=tuple(TensorMetadata(file="model.safetensors", name=name, shape=shape, dtype="F32")
                      for name, shape in tensors.items()),
        synthetic=synthetic)


def qwen_inventory(hidden=16, intermediate=32, vocab=32):
    inventory = {"model.embed_tokens.weight": (vocab, hidden), "model.norm.weight": (hidden,),
                 "lm_head.weight": (vocab, hidden)}
    prefix = "model.layers.0."
    for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
        inventory[prefix + f"self_attn.{name}.weight"] = (hidden, hidden)
    for name in ("q_proj", "k_proj", "v_proj"):
        inventory[prefix + f"self_attn.{name}.bias"] = (hidden,)
    inventory[prefix + "mlp.gate_proj.weight"] = (intermediate, hidden)
    inventory[prefix + "mlp.up_proj.weight"] = (intermediate, hidden)
    inventory[prefix + "mlp.down_proj.weight"] = (hidden, intermediate)
    inventory[prefix + "input_layernorm.weight"] = (hidden,)
    inventory[prefix + "post_attention_layernorm.weight"] = (hidden,)
    return inventory


def base_shapes(modules=MODULES, dims=(16, 16)):
    return {f"{module}.weight": dims for module in modules}


def base_tensors(modules=MODULES, dims=(16, 16)):
    return {name: f32(shape) for name, shape in base_shapes(modules, dims).items()}


def local(path, identity, descriptor, *, config_sha256=None, tokenizer=None):
    module = api()
    root = root_ref()
    return module.LocalModel(path=path, identity=identity, descriptor=descriptor,
                             config_sha256=config_sha256 or (descriptor.config_sha256 if descriptor else root.config_sha256),
                             tokenizer_fingerprint=tokenizer or root.tokenizer_fingerprint)


def adapter_on_disk(tmp_path, marker, descriptor, *, special=None, config_changes=None):
    directory = tmp_path / marker
    write_snapshot_files(directory)
    adapter = descriptor.adapter
    exported = {**PEFT_0181_EXPORT, "r": adapter.rank, "lora_alpha": adapter.alpha,
                "target_modules": sorted(adapter.target_modules), "fan_in_fan_out": adapter.fan_in_fan_out,
                **(config_changes or {})}
    (directory / "adapter_config.json").write_text(json.dumps(exported, indent=2, sort_keys=True), encoding="utf-8")
    tensors = {}
    for item in descriptor.tensors:
        values = f32(item.shape, special=special if "lora_B" in item.name else None)
        tensors[item.name] = (item.dtype, item.shape, values[2] if item.dtype == "F32" else encode(
            item.dtype, struct.unpack(f"<{math.prod(item.shape)}f", values[2])))
    write_safetensors(directory / "adapter_model.safetensors", tensors)
    return local(directory, ref_for(descriptor, marker), descriptor)


def hf_base_on_disk(tmp_path, marker="base", tensors=None, config=CONFIG):
    directory = tmp_path / marker
    write_snapshot_files(directory, config)
    write_safetensors(directory / "model.safetensors", tensors or base_tensors())
    return local(directory, root_ref(), None)


# -- 1. lightweight import -----------------------------------------------------------------------

def test_model_ops_import_is_lightweight(monkeypatch):
    module = api()
    block_heavy_imports(monkeypatch)
    for name in ("safetensors_scan", "target_inspection", "runtime_profiles"):
        importlib.reload(importlib.import_module(f"tuning_factory.{name}"))
    reloaded = importlib.reload(module)
    assert reloaded.POLICY_F32_V1 == "model-ops-f32-v1"
    assert set(reloaded.TOLERANCE_POLICIES) == {"model-ops-f32-v1"}
    assert reloaded.scan_finite_safetensors.__module__ == "tuning_factory.safetensors_scan"
    assert reloaded.inspect_target_modules.__module__ == "tuning_factory.target_inspection"
    assert reloaded.verify_inspected_targets.__module__ == "tuning_factory.target_inspection"


# -- 2-10. merge input refusals ------------------------------------------------------------------

def test_merge_refuses_duplicate_inputs():
    module = api()
    descriptor = lora_descriptor()
    ref = ref_for(descriptor)
    with pytest.raises(ValueError, match="merge requires distinct actual artifacts"):
        module.validate_merge_inputs((ref, ref), (descriptor, descriptor), (0.5, 0.5))


def test_merge_refuses_different_effective_base():
    module = api()
    sft = full_descriptor(base_shapes())
    sft_ref = ref_for(sft, "sft")
    first = lora_descriptor()
    second = lora_descriptor(effective_base=sft_ref, parents=(sft_ref,), marker="other")
    with pytest.raises(ValueError, match="merge input effective bases/tokenizers/targets differ"):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


@pytest.mark.parametrize("field", ["config_sha256", "tokenizer_fingerprint"])
def test_merge_refuses_config_or_tokenizer_mismatch(field):
    module = api()
    first = lora_descriptor()
    second = lora_descriptor(**{field: "d" * 64}, marker="other")
    with pytest.raises(ValueError, match="merge input effective bases/tokenizers/targets differ"):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


def test_merge_refuses_target_module_mismatch():
    module = api()
    first = lora_descriptor()
    second = lora_descriptor(targets=("q_proj",), modules=MODULES[:1], marker="other")
    with pytest.raises(ValueError, match="merge input effective bases/tokenizers/targets differ"):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


@pytest.mark.parametrize("changes", [{"in_dim": 8}, {"out_dim": 8}])
def test_merge_refuses_base_dimension_mismatch(changes):
    module = api()
    first = lora_descriptor()
    second = lora_descriptor(rank=3, alpha=9, **changes, marker="other")
    with pytest.raises(ValueError, match="merge base tensor dimensions differ"):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


def test_merge_refuses_rank_sum_over_256():
    module = api()
    first = lora_descriptor(rank=128, alpha=256)
    second = lora_descriptor(rank=129, alpha=258, marker="other")
    with pytest.raises(ValueError, match="merged rank exceeds 256"):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


def variant_cases():
    from tuning_factory.model_artifacts import AlphaPattern, RankPattern, TensorMetadata
    head = TensorMetadata(file="value_head.safetensors", name="v_head.summary.weight", shape=(1, 16), dtype="F32")
    variant = "unsupported adapter conversion variant"
    semantic = "materialize requires a causal LoRA/QLoRA adapter without value head"
    return [
        pytest.param({"use_dora": True}, variant, id="use_dora"),
        pytest.param({"use_rslora": True}, variant, id="use_rslora"),
        pytest.param({"pissa_init": True}, variant, id="pissa_init"),
        pytest.param({"bias": "all"}, variant, id="bias_all"),
        pytest.param({"modules_to_save": ("lm_head",)}, variant, id="modules_to_save"),
        pytest.param({"rank_pattern": (RankPattern(module="q_proj", rank=4),)}, variant, id="rank_pattern"),
        pytest.param({"alpha_pattern": (AlphaPattern(module="q_proj", alpha=8),)}, variant, id="alpha_pattern"),
        pytest.param({"target_parameters": ("experts.weight",)}, variant, id="target_parameters"),
        pytest.param({"layer_replication": ((0, 1),)}, variant, id="layer_replication"),
        # Conv1D (in, out) layouts: the worker's delta is B @ A for Linear weights only, so planning refuses too.
        pytest.param({"fan_in_fan_out": True}, variant, id="fan_in_fan_out"),
        pytest.param({"method": "oft"}, semantic, id="oft"),
        pytest.param({"semantic_kind": "reward_model", "has_value_head": True, "extra_tensors": (head,)},
                     semantic, id="value_head"),
    ]


@pytest.mark.parametrize("changes,message", variant_cases())
def test_merge_refuses_unsupported_variants(changes, message):
    module = api()
    first = lora_descriptor()
    second = lora_descriptor(**changes, marker="other")
    with pytest.raises(ValueError, match=message):
        module.validate_merge_inputs((ref_for(first, "a"), ref_for(second, "b")), (first, second), (0.5, 0.5))


def two_adapters(**changes):
    first = lora_descriptor(rank=2, alpha=4, **changes)
    second = lora_descriptor(rank=3, alpha=9, marker="other", **changes)
    return (ref_for(first, "a"), ref_for(second, "b")), (first, second)


@pytest.mark.parametrize("weights,message", [
    ((1.0,), "one positive finite weight is required for each model"),
    ((0.25, 0.25, 0.5), "one positive finite weight is required for each model"),
    ((0.0, 1.0), "one positive finite weight is required for each model"),
    ((-0.1, 1.1), "one positive finite weight is required for each model"),
    ((float("nan"), 0.5), "one positive finite weight is required for each model"),
    ((float("inf"), 0.5), "one positive finite weight is required for each model"),
    ((0.25, 0.749), "weights must sum to one"),
])
def test_merge_weights_positive_finite_sum_one(weights, message):
    module = api()
    refs, descriptors = two_adapters()
    with pytest.raises(ValueError, match=message):
        module.validate_merge_inputs(refs, descriptors, weights)


def test_merge_plan_normalizes_weights_exactly_and_records_scales():
    module = api()
    refs, descriptors = two_adapters()
    plan = module.validate_merge_inputs(refs, descriptors, (0.25, 0.75))
    assert plan.inputs == refs
    assert plan.normalized_weights == (0.25, 0.75)
    assert plan.scales == (2.0, 3.0)
    assert plan.ranks == (2, 3)
    assert plan.output_rank == 5
    assert plan.effective_base == root_ref()
    assert plan.target_modules == ("q_proj", "v_proj")


@pytest.mark.parametrize("flag", ["synthetic", "simulation"])
def test_merge_require_real_refuses_simulation_only(flag):
    """Task 11.5 (R1 I-2, option b): synthetic adapters may be merged for real (their merge stays synthetic, so it is
    never selected or registered); simulated adapters never."""
    module = api()
    refs, descriptors = two_adapters(**{flag: True})
    if flag == "simulation":
        with pytest.raises(ValueError, match="merge requires real completed artifacts"):
            module.validate_merge_inputs(refs, descriptors, (0.25, 0.75))
    else:
        assert module.validate_merge_inputs(refs, descriptors, (0.25, 0.75)).output_rank == 5
    plan = module.validate_merge_inputs(refs, descriptors, (0.25, 0.75), require_real=False)
    assert plan.output_rank == 5
    # Relaxing the real-only rule must not relax any other conversion rule.
    with pytest.raises(ValueError, match="merged rank exceeds 256"):
        big = (lora_descriptor(rank=128, alpha=256, **{flag: True}),
               lora_descriptor(rank=129, alpha=258, marker="other", **{flag: True}))
        module.validate_merge_inputs((ref_for(big[0], "a"), ref_for(big[1], "b")), big, (0.5, 0.5),
                                     require_real=False)


def test_graph_merge_compilation_stays_real_only():
    from tuning_factory.graph_planner import _merge_descriptors
    refs, descriptors = two_adapters(simulation=True)
    api().validate_merge_inputs(refs, descriptors, (0.25, 0.75), require_real=False)
    with pytest.raises(ValueError, match="merge requires real completed artifacts"):
        _merge_descriptors(refs, descriptors)
    _merge_descriptors(*two_adapters(synthetic=True))   # Task 11.5: synthetic inputs compile (option b)


def test_model_ops_uses_the_public_conversion_rules():
    import inspect

    from tuning_factory import graph_planner
    refs, descriptors = two_adapters(simulation=True)
    graph_planner.check_merge_descriptors(refs, descriptors, require_real=False)
    with pytest.raises(ValueError, match="merge requires real completed artifacts"):
        graph_planner.check_merge_descriptors(refs, descriptors)
    source = inspect.getsource(api())
    # No private graph_planner helpers and no descriptor rewrite to bypass the real-only rule.
    assert "from .graph_planner import _" not in source
    assert '"synthetic": False' not in source


# -- 11-12. finite scan --------------------------------------------------------------------------

LARGE_FINITE = {"F32": 3.0e38, "F64": 1.0e308, "F16": 65504.0, "BF16": 3.0e38}


@pytest.mark.parametrize("dtype", ["F16", "BF16", "F32", "F64"])
@pytest.mark.parametrize("bad", [float("nan"), float("inf"), float("-inf")], ids=["nan", "+inf", "-inf"])
def test_finite_scan_detects_nan_and_inf_per_dtype(tmp_path, dtype, bad):
    module = api()
    values = [0.0, -0.0, 0.5, -1.25, LARGE_FINITE[dtype], -LARGE_FINITE[dtype], 1e-7]
    clean = tmp_path / "clean.safetensors"
    write_safetensors(clean, {"weight": (dtype, (len(values),), encode(dtype, values)),
                              "position_ids": ("I64", (2,), encode("I64", (0, 1)))})
    assert module.scan_finite_safetensors(clean) == len(values)
    broken = tmp_path / "broken.safetensors"
    write_safetensors(broken, {"fine": (dtype, (2,), encode(dtype, (1.0, 2.0))),
                               "bad.weight": (dtype, (3,), encode(dtype, (1.0, bad, 2.0)))})
    with pytest.raises(ValueError, match=re.escape("non-finite tensor values in broken.safetensors: bad.weight")):
        module.scan_finite_safetensors(broken)


def test_finite_scan_streams_large_tensors_and_refuses_unscannable_dtypes(tmp_path):
    module = api()
    count = 3 * 1024 * 1024 + 5
    raw = bytearray(encode("F32", (0.5,)) * count)
    raw[-4:] = encode("F32", (float("nan"),))
    path = tmp_path / "large.safetensors"
    write_safetensors(path, {"big": ("F32", (count,), bytes(raw))})
    with pytest.raises(ValueError, match="non-finite tensor values in large.safetensors: big"):
        module.scan_finite_safetensors(path)
    fp8 = tmp_path / "fp8.safetensors"
    write_safetensors(fp8, {"q": ("F8_E4M3", (2,), b"\x00\x7f")})
    with pytest.raises(ValueError, match="unsupported tensor dtype for finite scan"):
        module.scan_finite_safetensors(fp8)


def test_finite_scan_refuses_header_that_changed_after_validation(tmp_path, monkeypatch):
    from tuning_factory import safetensors_scan
    validated_file, swapped = tmp_path / "validated.safetensors", tmp_path / "swapped.safetensors"
    write_safetensors(validated_file, {"w": f32((4,))})
    write_safetensors(swapped, {"w": f32((2, 2))})
    validated = safetensors_scan._tensor_header(validated_file, validated_file.name)
    monkeypatch.setattr(safetensors_scan, "_tensor_header", lambda path, name: validated)
    with pytest.raises(ValueError, match="safetensors header changed while scanning"):
        safetensors_scan.scan_finite_safetensors(swapped)


def test_finite_scan_bounds_the_header_it_rereads(tmp_path, monkeypatch):
    from tuning_factory import safetensors_scan
    from tuning_factory.model_artifacts import JSON_LIMIT
    validated_file, oversized = tmp_path / "validated.safetensors", tmp_path / "oversized.safetensors"
    write_safetensors(validated_file, {"w": f32((4,))})
    oversized.write_bytes(struct.pack("<Q", JSON_LIMIT + 1) + b"{}")
    validated = safetensors_scan._tensor_header(validated_file, validated_file.name)
    monkeypatch.setattr(safetensors_scan, "_tensor_header", lambda path, name: validated)
    with pytest.raises(ValueError, match="invalid safetensors header length"):
        safetensors_scan.scan_finite_safetensors(oversized)


def test_merge_refuses_nonfinite_adapter_before_any_output(tmp_path, monkeypatch):
    module = api()
    first = lora_descriptor(rank=2, alpha=4, synthetic=True)
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True)
    inputs = (adapter_on_disk(tmp_path, "a", first), adapter_on_disk(tmp_path, "b", second, special=float("nan")))
    base = hf_base_on_disk(tmp_path)
    output = tmp_path / "merged"
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="non-finite tensor values in adapter_model.safetensors"):
        module.merge_adapters(inputs, (0.25, 0.75), output, base=base, require_real=False)
    assert not output.exists()


@pytest.mark.parametrize("operation", ["merge", "materialize"])
def test_adapter_loader_config_must_match_descriptor_before_heavy_import(tmp_path, monkeypatch, operation):
    # PEFT loads adapter_config.json from the local directory, so it must equal the verified metadata.
    module = api()
    first = lora_descriptor(rank=2, alpha=4, synthetic=True)
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True)
    tampered = adapter_on_disk(tmp_path, "a", first, config_changes={"lora_alpha": 8})
    other = adapter_on_disk(tmp_path, "b", second)
    base = hf_base_on_disk(tmp_path)
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="adapter effective configuration mismatch: lora_alpha"):
        if operation == "merge":
            module.merge_adapters((tampered, other), (0.25, 0.75), tmp_path / "out", base=base, require_real=False)
        else:
            module.materialize_model(module.StagedLoad(base=base, adapter=tampered), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_merge_refuses_base_dimension_mismatch_before_heavy_import(tmp_path, monkeypatch):
    module = api()
    first = lora_descriptor(rank=2, alpha=4, synthetic=True)
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True)
    inputs = (adapter_on_disk(tmp_path, "a", first), adapter_on_disk(tmp_path, "b", second))
    base = hf_base_on_disk(tmp_path, tensors=base_tensors(dims=(16, 8)))
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="merge base tensor dimensions differ"):
        module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "merged", base=base, require_real=False)
    assert not (tmp_path / "merged").exists()


def valid_merge(tmp_path, base=None):
    first = lora_descriptor(rank=2, alpha=4, synthetic=True)
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True)
    inputs = (adapter_on_disk(tmp_path, "a", first), adapter_on_disk(tmp_path, "b", second))
    return inputs, base or hf_base_on_disk(tmp_path)


@pytest.mark.parametrize("operation", ["merge", "materialize"])
def test_valid_inputs_stop_at_the_runtime_gate_without_output_or_staging(tmp_path, monkeypatch, operation):
    module = api()
    inputs, base = valid_merge(tmp_path)
    parent = tmp_path / "outputs"
    parent.mkdir()
    block_heavy_imports(monkeypatch)
    with pytest.raises(RuntimeError, match="model-operation runtime is missing"):
        if operation == "merge":
            module.merge_adapters(inputs, (0.25, 0.75), parent / "out", base=base, require_real=False)
        else:
            module.materialize_model(module.StagedLoad(base=base, adapter=inputs[0]), parent / "out")
    assert list(parent.iterdir()) == []


def test_model_ops_runtime_refuses_version_drift(monkeypatch):
    import types
    from importlib import metadata

    from tuning_factory.runtime_profiles import MODEL_OPS_PINS, model_ops_runtime
    for name in ("torch", "peft", "safetensors", "transformers"):
        monkeypatch.setitem(sys.modules, name, types.ModuleType(name))
    installed = {**MODEL_OPS_PINS, "torch": "2.7.1+cu126"}
    monkeypatch.setattr(metadata, "version", lambda name: installed[name])
    assert [item.__name__ for item in model_ops_runtime()] == ["torch", "peft", "safetensors", "transformers"]
    installed["peft"] = "0.18.0"
    with pytest.raises(RuntimeError, match="model-operation runtime differs from pinned versions: peft"):
        model_ops_runtime()


@pytest.mark.parametrize("operation", ["merge", "materialize"])
def test_existing_output_is_refused_before_heavy_import(tmp_path, monkeypatch, operation):
    module = api()
    inputs, base = valid_merge(tmp_path)
    output = tmp_path / "out"
    output.mkdir()
    (output / "keep.txt").write_text("existing", encoding="utf-8")
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="model-operation output already exists"):
        if operation == "merge":
            module.merge_adapters(inputs, (0.25, 0.75), output, base=base, require_real=False)
        else:
            module.materialize_model(module.StagedLoad(base=base, adapter=inputs[0]), output)
    assert [item.name for item in output.iterdir()] == ["keep.txt"]


@pytest.mark.parametrize("operation", ["merge", "materialization"])
@pytest.mark.parametrize("dtype,values", [("F64", (1.0, 2.0)), ("I64", (0, 1))], ids=["F64", "I64"])
def test_policy_refuses_base_tensors_without_exact_f32_upcast(tmp_path, monkeypatch, operation, dtype, values):
    module = api()
    tensors = {**base_tensors(), "model.layers.0.extra.weight": (dtype, (2,), encode(dtype, values))}
    inputs, base = valid_merge(tmp_path, hf_base_on_disk(tmp_path, tensors=tensors))
    block_heavy_imports(monkeypatch)
    message = f"{operation} requires F16, BF16 or F32 tensors under policy model-ops-f32-v1"
    with pytest.raises(ValueError, match=message):
        if operation == "merge":
            module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "out", base=base, require_real=False)
        else:
            module.validate_materialize_input(module.StagedLoad(base=base, adapter=inputs[0]))


@pytest.mark.parametrize("operation", ["merge", "materialization"])
def test_policy_refuses_f64_adapter_tensors(tmp_path, monkeypatch, operation):
    module = api()
    first = lora_descriptor(rank=2, alpha=4, synthetic=True, dtype="F64")
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True, dtype="F64")
    inputs = (adapter_on_disk(tmp_path, "a", first), adapter_on_disk(tmp_path, "b", second))
    base = hf_base_on_disk(tmp_path)
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match=f"{operation} requires F16, BF16 or F32 tensors"):
        if operation == "merge":
            module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "out", base=base, require_real=False)
        else:
            module.materialize_model(module.StagedLoad(base=base, adapter=inputs[0]), tmp_path / "out")
    assert not (tmp_path / "out").exists()


def test_merge_names_its_own_operation_for_a_quantized_base(tmp_path, monkeypatch):
    module = api()
    config = canonical({"model_type": "qwen2", "hidden_size": 16, "quantization_config": {"load_in_4bit": True}})
    inputs, base = valid_merge(tmp_path, hf_base_on_disk(tmp_path, config=config))
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="^merge requires unquantized effective base weights$"):
        module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "out", base=base, require_real=False)


def write_sharded_base(directory, *, index_shards=None):
    write_snapshot_files(directory)
    shards = {}
    for number, (name, tensor) in enumerate(base_tensors().items(), start=1):
        shard = f"model-0000{number}-of-00002.safetensors"
        write_safetensors(directory / shard, {name: tensor})
        shards[name] = shard
    (directory / "model.safetensors.index.json").write_bytes(canonical(
        {"metadata": {"total_size": 2048}, "weight_map": index_shards or shards}))
    # Mistral-style repositories also ship a consolidated copy that the loader ignores.
    write_safetensors(directory / "consolidated.safetensors", base_tensors())


def test_sharded_base_uses_only_the_indexed_shards(tmp_path):
    module = api()
    write_sharded_base(tmp_path / "base")
    load = module.StagedLoad(base=local(tmp_path / "base", root_ref(), None),
                             adapter=adapter_on_disk(tmp_path, "adapter", lora_descriptor()))
    module.validate_materialize_input(load)
    missing = tmp_path / "missing"
    write_sharded_base(missing, index_shards={f"{MODULES[0]}.weight": "model-00001-of-00002.safetensors",
                                              f"{MODULES[1]}.weight": "model-00003-of-00002.safetensors"})
    with pytest.raises(ValueError, match="missing or mismatched model shard"):
        module.validate_materialize_input(module.StagedLoad(base=local(missing, root_ref(), None),
                                                            adapter=load.adapter))
    swapped = tmp_path / "swapped"
    write_sharded_base(swapped, index_shards={f"{MODULES[0]}.weight": "model-00002-of-00002.safetensors",
                                              f"{MODULES[1]}.weight": "model-00001-of-00002.safetensors"})
    with pytest.raises(ValueError, match="model shard index does not cover tensor inventory"):
        module.validate_materialize_input(module.StagedLoad(base=local(swapped, root_ref(), None),
                                                            adapter=load.adapter))


def test_single_file_base_ignores_a_consolidated_copy(tmp_path):
    module = api()
    base = hf_base_on_disk(tmp_path)
    write_safetensors(base.path / "consolidated.safetensors", base_tensors())
    module.validate_materialize_input(module.StagedLoad(
        base=base, adapter=adapter_on_disk(tmp_path, "adapter", lora_descriptor())))


def test_base_with_single_and_sharded_weights_is_ambiguous(tmp_path):
    # transformers 5.6 loads model.safetensors before the index, so validating the shards would check other bytes.
    module = api()
    write_sharded_base(tmp_path / "base")
    write_safetensors(tmp_path / "base" / "model.safetensors", base_tensors())
    load = module.StagedLoad(base=local(tmp_path / "base", root_ref(), None),
                             adapter=adapter_on_disk(tmp_path, "adapter", lora_descriptor()))
    with pytest.raises(ValueError, match="ambiguous single and sharded model weights"):
        module.validate_materialize_input(load)


def test_full_model_without_loader_weights_is_refused(tmp_path):
    module = api()
    base = hf_base_on_disk(tmp_path)
    (base.path / "model.safetensors").rename(base.path / "consolidated.safetensors")
    with pytest.raises(ValueError, match="model.safetensors or a shard index"):
        module.validate_materialize_input(module.StagedLoad(
            base=base, adapter=adapter_on_disk(tmp_path, "adapter", lora_descriptor())))


def test_adapter_directories_keep_the_all_safetensors_layout(tmp_path):
    module = api()
    adapter = adapter_on_disk(tmp_path, "adapter", lora_descriptor())
    write_safetensors(adapter.path / "value_head.safetensors", {"v_head.summary.weight": f32((1, 16))})
    assert module._weight_files(adapter.path, adapter=True) == ("adapter_model.safetensors", "value_head.safetensors")
    with pytest.raises(ValueError, match="model.safetensors or a shard index"):
        module._weight_files(adapter.path, adapter=False)


def test_base_config_weight_redirect_is_refused(tmp_path, monkeypatch):
    module = api()
    config = canonical({"model_type": "qwen2", "hidden_size": 16, "transformers_weights": "consolidated.safetensors"})
    base = hf_base_on_disk(tmp_path, config=config)
    with pytest.raises(ValueError, match="transformers_weights"):
        module.validate_materialize_input(module.StagedLoad(
            base=base, adapter=adapter_on_disk(tmp_path, "adapter", lora_descriptor())))
    inputs, base = valid_merge(tmp_path, base)
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="transformers_weights"):
        module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "out", base=base, require_real=False)


def test_merge_refuses_base_that_is_not_the_shared_effective_base(tmp_path, monkeypatch):
    module = api()
    first = lora_descriptor(rank=2, alpha=4, synthetic=True)
    second = lora_descriptor(rank=3, alpha=9, marker="other", synthetic=True)
    inputs = (adapter_on_disk(tmp_path, "a", first), adapter_on_disk(tmp_path, "b", second))
    sft = full_descriptor(base_shapes(), synthetic=True)
    directory = tmp_path / "sft"
    write_snapshot_files(directory)
    write_safetensors(directory / "model.safetensors", base_tensors())
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="merge input effective bases/tokenizers/targets differ"):
        module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "merged", require_real=False,
                              base=local(directory, ref_for(sft, "sft"), sft))


# -- 13-16. materialize input refusals and tolerance policy --------------------------------------

def sft_base(tmp_path, *, quantization_bit=None, config=CONFIG, storage="full_model"):
    descriptor = (full_descriptor(base_shapes(), quantization_bit=quantization_bit) if storage == "full_model"
                  else lora_descriptor(marker="adapter-base"))
    directory = tmp_path / "sft"
    write_snapshot_files(directory, config)
    write_safetensors(directory / "model.safetensors", base_tensors())
    ref = ref_for(descriptor, "sft")
    return local(directory, ref, descriptor), ref


def staged(module, base, adapter_descriptor, tmp_path):
    return module.StagedLoad(base=base, adapter=adapter_on_disk(tmp_path, "adapter", adapter_descriptor))


def test_materialize_refuses_root_when_effective_base_is_artifact(tmp_path, monkeypatch):
    module = api()
    _, sft_ref = sft_base(tmp_path)
    adapter = lora_descriptor(effective_base=sft_ref, parents=(sft_ref,))
    load = staged(module, hf_base_on_disk(tmp_path), adapter, tmp_path)
    with pytest.raises(ValueError, match="materialize base differs from adapter effective base"):
        module.validate_materialize_input(load)
    block_heavy_imports(monkeypatch)
    with pytest.raises(ValueError, match="materialize base differs from adapter effective base"):
        module.materialize_model(load, tmp_path / "full")
    assert not (tmp_path / "full").exists()


@pytest.mark.parametrize("case", ["descriptor_bit", "config_quantization", "adapter_base"])
def test_materialize_refuses_quantized_or_adapter_base(tmp_path, case):
    module = api()
    if case == "descriptor_bit":
        base, ref = sft_base(tmp_path, quantization_bit=4)
        message = "materialization requires unquantized effective base weights"
    elif case == "config_quantization":
        base, ref = sft_base(tmp_path)
        (base.path / "config.json").write_bytes(canonical({"model_type": "qwen2", "hidden_size": 16,
                                                           "quantization_config": {"load_in_4bit": True}}))
        message = "materialization requires unquantized effective base weights"
    else:
        base, ref = sft_base(tmp_path, storage="adapter")
        message = "effective base must be a full model"
    adapter = lora_descriptor(effective_base=ref, parents=(ref,))
    with pytest.raises(ValueError, match=message):
        module.validate_materialize_input(staged(module, base, adapter, tmp_path))


def test_materialize_refuses_tampered_local_config_or_tokenizer(tmp_path):
    module = api()
    base, ref = sft_base(tmp_path)
    adapter = lora_descriptor(effective_base=ref, parents=(ref,))
    load = staged(module, base, adapter, tmp_path)
    module.validate_materialize_input(load)
    (base.path / "config.json").write_bytes(canonical({"model_type": "qwen2", "hidden_size": 17}))
    with pytest.raises(ValueError, match="model config snapshot fingerprint mismatch"):
        module.validate_materialize_input(load)
    (base.path / "config.json").write_bytes(CONFIG)
    (base.path / "tokenizer.json").write_text('{"version":"2.0"}', encoding="utf-8")
    with pytest.raises(ValueError, match="tokenizer snapshot fingerprint mismatch"):
        module.validate_materialize_input(load)


def test_materialize_refuses_artifact_base_whose_local_weights_differ_from_descriptor(tmp_path):
    module = api()
    base, ref = sft_base(tmp_path)
    write_safetensors(base.path / "model.safetensors", {**base_tensors(), "lm_head.weight": f32((32, 16))})
    with pytest.raises(ValueError, match="tensor inventory geometry does not match snapshot"):
        module.validate_materialize_input(staged(module, base, lora_descriptor(effective_base=ref, parents=(ref,)),
                                                 tmp_path))


@pytest.mark.parametrize("changes,message", variant_cases())
def test_materialize_refuses_value_head_and_variants(tmp_path, changes, message):
    module = api()
    adapter = lora_descriptor(**changes)
    with pytest.raises(ValueError, match=message):
        module.validate_materialize_input(staged(module, hf_base_on_disk(tmp_path), adapter, tmp_path))


def test_only_f32_tolerance_policy_is_accepted(tmp_path, monkeypatch):
    module = api()
    assert module.TOLERANCE_POLICIES[module.POLICY_F32_V1] == module.TolerancePolicy()
    policy = module.TolerancePolicy()
    assert (policy.storage_dtype, policy.delta_atol, policy.delta_rtol, policy.logits_atol, policy.logits_rtol) == (
        "F32", 1e-6, 1e-5, 1e-5, 1e-4)
    with pytest.raises(ValidationError):
        module.TolerancePolicy(policy_id="model-ops-bf16-v1")
    with pytest.raises(ValidationError):
        module.TolerancePolicy(storage_dtype="BF16")
    block_heavy_imports(monkeypatch)
    refs, descriptors = two_adapters()
    inputs = tuple(local(tmp_path, ref, descriptor) for ref, descriptor in zip(refs, descriptors))
    with pytest.raises(ValueError, match="unsupported tolerance policy"):
        module.merge_adapters(inputs, (0.25, 0.75), tmp_path / "merged", base=hf_base_on_disk(tmp_path),
                              policy="model-ops-bf16-v1")
    load = module.StagedLoad(base=hf_base_on_disk(tmp_path, "base2"), adapter=inputs[0])
    with pytest.raises(ValueError, match="unsupported tolerance policy"):
        module.materialize_model(load, tmp_path / "full", policy="model-ops-bf16-v1")


# -- 17. inspected target evidence ---------------------------------------------------------------

def evidence_for(module, base, inventory, *, skip=(), extra=()):
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    linear = [name[:-len(".weight")] for name, shape in inventory.items()
              if name.endswith(".weight") and len(shape) == 2 and "embed" not in name and "lm_head" not in name]
    modules = [TargetModuleEvidence(name=name, input_dimension=inventory[name + ".weight"][1],
                                    output_dimension=inventory[name + ".weight"][0])
               for name in linear if name not in skip]
    modules.extend(extra)
    return ModelTargetEvidence(base=base, modules=tuple(modules))


def test_verify_inspected_targets_requires_complete_matching_inventory():
    from tuning_factory.graph_planner import TargetModuleEvidence
    module = api()
    inventory = qwen_inventory()
    descriptor = full_descriptor(inventory)
    ref = ref_for(descriptor)
    evidence = evidence_for(module, ref, inventory)
    assert len(evidence.modules) == 7
    assert module.verify_inspected_targets(evidence, descriptor, ref) == evidence
    missing = evidence_for(module, ref, inventory, skip=("model.layers.0.mlp.down_proj",))
    with pytest.raises(ValueError, match="inspected target modules differ from verified tensor inventory"):
        module.verify_inspected_targets(missing, descriptor, ref)
    wrong = evidence.model_copy(update={"modules": (
        evidence.modules[0].model_copy(update={"input_dimension": 15}), *evidence.modules[1:])})
    with pytest.raises(ValueError, match="inspected base dimensions differ from verified tensor inventory"):
        module.verify_inspected_targets(wrong, descriptor, ref)
    phantom = evidence_for(module, ref, inventory, extra=(
        TargetModuleEvidence(name="model.layers.0.self_attn.rotary", input_dimension=16, output_dimension=16),))
    with pytest.raises(ValueError, match="inspected base dimensions differ from verified tensor inventory"):
        module.verify_inspected_targets(phantom, descriptor, ref)
    other = evidence.model_copy(update={"base": root_ref()})
    with pytest.raises(ValueError, match="inspected target evidence belongs to another base"):
        module.verify_inspected_targets(other, descriptor, ref)
    widened = full_descriptor({**inventory, "model.layers.0.mlp.extra_proj.weight": (16, 16)})
    with pytest.raises(ValueError, match="inspected target modules differ from verified tensor inventory"):
        module.verify_inspected_targets(evidence_for(module, ref_for(widened), inventory), widened, ref_for(widened))
    adapter = lora_descriptor()
    with pytest.raises(ValueError, match="inspected targets require a full-model inventory"):
        module.verify_inspected_targets(evidence_for(module, ref_for(adapter), inventory), adapter, ref_for(adapter))
    from tuning_factory.model_artifacts import ModelArtifactRef
    foreign = ModelArtifactRef(artifact_id="e" * 64, inventory_sha256=ref.inventory_sha256)
    with pytest.raises(ValueError, match="inspected target descriptor differs from the artifact identity"):
        module.verify_inspected_targets(evidence_for(module, foreign, inventory), descriptor, foreign)


# -- 18. adapter metadata from a real PEFT 0.18.1 export -----------------------------------------

# adapter_config.json written by PEFT 0.18.1 save_pretrained for LoraConfig(r=2, lora_alpha=4,
# target_modules=["q_proj", "v_proj"], lora_dropout=0.0, task_type="CAUSAL_LM") on the tiny Qwen2
# (captured in the hash-locked CPU runtime; see the plan, F7). Only the host base path is replaced.
PEFT_0181_EXPORT = {
    "alora_invocation_tokens": None, "alpha_pattern": {}, "arrow_config": None, "auto_mapping": None,
    "base_model_name_or_path": "/workspace/model-cache/base/files", "bias": "none", "corda_config": None,
    "ensure_weight_tying": False, "eva_config": None, "exclude_modules": None, "fan_in_fan_out": False,
    "inference_mode": True, "init_lora_weights": True, "layer_replication": None, "layers_pattern": None,
    "layers_to_transform": None, "loftq_config": {}, "lora_alpha": 4, "lora_bias": False, "lora_dropout": 0.0,
    "megatron_config": None, "megatron_core": "megatron.core", "modules_to_save": None, "peft_type": "LORA",
    "peft_version": "0.18.1", "qalora_group_size": 16, "r": 2, "rank_pattern": {}, "revision": None,
    "target_modules": ["q_proj", "v_proj"], "target_parameters": None, "task_type": "CAUSAL_LM",
    "trainable_token_indices": None, "use_dora": False, "use_qalora": False, "use_rslora": False,
}


def test_adapter_metadata_from_real_peft_export_freezes(tmp_path):
    from tuning_factory.model_artifacts import ModelArtifactStore, ModelDescriptor, TensorMetadata
    module = api()
    exported = dict(PEFT_0181_EXPORT)
    metadata = module.adapter_metadata_from_config(exported, method="lora")
    assert metadata.task_type == "CAUSAL_LM" and metadata.inference_mode is True
    assert metadata.target_modules == ("q_proj", "v_proj")
    assert (metadata.rank, metadata.alpha, metadata.dropout) == (2, 4, 0)
    directory = tmp_path / "export"
    write_snapshot_files(directory)
    (directory / "adapter_config.json").write_text(json.dumps(exported, indent=2, sort_keys=True), encoding="utf-8")
    tensors = {}
    for item in adapter_tensors(2):
        tensors[item.name] = f32(item.shape)
    write_safetensors(directory / "adapter_model.safetensors", tensors)
    root = root_ref()
    descriptor = ModelDescriptor(producer_job_id="job", producer_attempt_id="attempt",
        producer_manifest_sha256="b" * 64, storage_kind="adapter", semantic_kind="causal_lm",
        has_value_head=False, root=root, effective_base=root, config_sha256=root.config_sha256,
        tokenizer_fingerprint=root.tokenizer_fingerprint, adapter=metadata,
        tensors=tuple(TensorMetadata(file="adapter_model.safetensors", name=item.name, shape=item.shape,
                                     dtype="F32") for item in adapter_tensors(2)))
    store = ModelArtifactStore(tmp_path / "store")
    assert store.verify(store.freeze(directory, descriptor)).adapter == metadata


def test_written_adapter_config_is_canonical_whatever_the_set_order(tmp_path):
    # PEFT serializes target_modules from a set, so its order follows PYTHONHASHSEED.
    module = api()
    path = tmp_path / "adapter_config.json"
    written = []
    for order in (["v_proj", "q_proj"], ["q_proj", "v_proj"]):
        module.write_adapter_config(path, {**PEFT_0181_EXPORT, "target_modules": order,
                                           "base_model_name_or_path": "C:\\Users\\someone\\base"}, "test/tiny")
        written.append(path.read_bytes())
    assert written[0] == written[1]
    assert b"\r\n" not in written[0] and written[0] == json.dumps(json.loads(written[0]), indent=2,
                                                                     sort_keys=True).encode()
    config = json.loads(written[0])
    assert config["target_modules"] == ["q_proj", "v_proj"]
    assert config["base_model_name_or_path"] == "test/tiny"


def test_adapter_metadata_maps_variants_and_refuses_unknown_initialization():
    module = api()
    exported = {**PEFT_0181_EXPORT, "target_modules": ["v_proj", "q_proj"], "init_lora_weights": "pissa_niter_4",
                "lora_alpha": 8, "rank_pattern": {"q_proj": 4}}
    metadata = module.adapter_metadata_from_config(exported, method="qlora", quantization_bit=4,
                                                   loraplus_lr_ratio=16.0)
    assert metadata.target_modules == ("q_proj", "v_proj")
    assert (metadata.method, metadata.quantization_bit, metadata.alpha) == ("qlora", 4, 8)
    assert (metadata.pissa_init, metadata.pissa_iter) == (True, 4)
    assert metadata.rank_pattern[0].module == "q_proj" and metadata.loraplus_lr_ratio == 16.0
    assert module.adapter_metadata_from_config({**PEFT_0181_EXPORT, "init_lora_weights": "pissa"},
                                               method="lora").pissa_iter == -1
    for value in (False, "gaussian", "olora"):
        with pytest.raises(ValueError, match="unsupported adapter initialization"):
            module.adapter_metadata_from_config({**PEFT_0181_EXPORT, "init_lora_weights": value}, method="lora")
    with pytest.raises(ValueError, match="LoRA export"):
        module.adapter_metadata_from_config({**PEFT_0181_EXPORT, "peft_type": "OFT"}, method="lora")
    with pytest.raises(ValueError, match="explicit target module list"):
        module.adapter_metadata_from_config({**PEFT_0181_EXPORT, "target_modules": "all-linear"}, method="lora")


# -- 19-20. CPU acceptance script guards ---------------------------------------------------------

def cpu_script():
    if not SCRIPT.is_file():
        pytest.fail("scripts/verify_model_ops_cpu.py is missing")
    specification = importlib.util.spec_from_file_location("verify_model_ops_cpu", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_cpu_script_version_gate_refuses_drift(monkeypatch):
    block_heavy_imports(monkeypatch)
    script = cpu_script()
    pinned = dict(script.EXPECTED_VERSIONS)
    assert pinned["torch"] == "2.7.1+cpu" and pinned["peft"] == "0.18.1"
    script.check_versions(pinned)
    with pytest.raises(RuntimeError, match="peft"):
        script.check_versions({**pinned, "peft": "0.18.0"})
    with pytest.raises(RuntimeError, match="torch"):
        script.check_versions({**pinned, "torch": "2.7.1+cu126"})
    with pytest.raises(RuntimeError, match="numpy"):
        script.check_versions({key: value for key, value in pinned.items() if key != "numpy"})


def test_cpu_script_refuses_to_run_without_torch(tmp_path):
    receipt = tmp_path / "receipt.json"
    if not SCRIPT.is_file():
        pytest.fail("scripts/verify_model_ops_cpu.py is missing")
    result = subprocess.run([sys.executable, "-I", str(SCRIPT), "--receipt", str(receipt)],
                            capture_output=True, text=True, timeout=120)
    assert result.returncode == 2, result.stderr
    assert "model-operation CPU runtime is missing" in result.stderr
    assert not receipt.exists()
    assert importlib.util.find_spec("torch") is None


# -- 21. hash-locked CPU runtime -----------------------------------------------------------------

def lock_pins(path):
    pins, hashed, current = {}, {}, None
    for line in path.read_text(encoding="utf-8").splitlines():
        match = re.match(r"^([A-Za-z0-9_.-]+)==(\S+)", line)
        if match:
            current = match.group(1).lower().replace("_", "-")
            pins[current] = match.group(2)
            hashed[current] = False
        elif current and "--hash=sha256:" in line:
            hashed[current] = True
    return pins, hashed


def test_cpu_lock_pins_primary_runtime_versions_with_hashes():
    cpu, cpu_hashed = lock_pins(ROOT / "containers" / "model-ops-cpu-requirements.lock")
    gpu, _ = lock_pins(ROOT / "containers" / "gpu-requirements.lock")
    assert cpu["torch"] == "2.7.1+cpu"
    for package in ("peft", "transformers", "safetensors", "tokenizers", "accelerate", "huggingface-hub", "numpy"):
        assert cpu[package] == gpu[package], package
    assert cpu and all(cpu_hashed.values()), [name for name, ok in cpu_hashed.items() if not ok]
    forbidden = [name for name in cpu if name in {"llamafactory", "bitsandbytes", "triton"}
                 or name.startswith("nvidia-")]
    assert forbidden == []
    from tuning_factory.runtime_profiles import MODEL_OPS_CPU_LOCK, MODEL_OPS_PINS
    assert MODEL_OPS_CPU_LOCK == "containers/model-ops-cpu-requirements.lock"
    assert dict(MODEL_OPS_PINS) == {name: gpu[name].split("+")[0]
                                    for name in ("torch", "peft", "transformers", "safetensors")}
    with pytest.raises(TypeError):
        MODEL_OPS_PINS["torch"] = "0"


def test_source_files_have_lf_line_endings_for_a_checkout_independent_source_hash():
    # manifest.source_hash() hashes raw bytes, so a CRLF checkout would silently change source_sha256.
    carriage = [path.relative_to(ROOT).as_posix() for path in sorted((ROOT / "src").rglob("*.py"))
                if b"\r" in path.read_bytes()]
    assert carriage == []


# -- Task 7.3: output publishing and platform newlines -------------------------------------------

def write_platform_json(path, value):
    # transformers writes JSON in text mode, so its newlines follow the platform's os.linesep.
    import os
    with open(path, "w", encoding="utf-8", newline=os.linesep) as stream:
        stream.write(json.dumps(value, indent=2, sort_keys=True) + "\n")


TRANSFORMERS_TEXT = ("config.json", "generation_config.json", "model.safetensors.index.json")


def fake_transformers_save(directory):
    directory.mkdir(parents=True)
    write_platform_json(directory / "config.json", {"model_type": "qwen2", "hidden_size": 16, "dtype": "float32"})
    write_platform_json(directory / "generation_config.json", {"bos_token_id": 2, "eos_token_id": 3})
    (directory / "tokenizer_config.json").write_bytes(b"{\r\n}\r\n")   # base bytes, copied verbatim
    shard = "model-00001-of-00001.safetensors"
    write_safetensors(directory / shard, base_tensors())
    write_platform_json(directory / "model.safetensors.index.json",
                        {"metadata": {"total_size": 2048}, "weight_map": {name: shard for name in base_tensors()}})


def test_materialized_json_is_normalized_so_the_output_inventory_ignores_os_linesep(tmp_path, monkeypatch):
    import os

    from tuning_factory.model_artifacts import directory_inventory_sha256
    module = api()
    inventories, configs = [], []
    for name, linesep in (("windows", "\r\n"), ("linux", "\n")):
        monkeypatch.setattr(os, "linesep", linesep)
        payload = tmp_path / name / ".model-op-x" / "payload"
        fake_transformers_save(payload)
        configs.append((payload / "config.json").read_bytes())
        module.normalize_json_newlines(payload)
        # Tokenizer files are base bytes (fingerprinted); only transformers' own JSON is normalized.
        assert (payload / "tokenizer_config.json").read_bytes() == b"{\r\n}\r\n"
        output = tmp_path / name / "output"
        module.publish_output(payload, output)
        assert tuple(module.TRANSFORMERS_JSON) == TRANSFORMERS_TEXT
        for file in TRANSFORMERS_TEXT:
            assert b"\r" not in (output / file).read_bytes(), file
        assert json.loads((output / "config.json").read_bytes()) == json.loads(configs[-1])
        inventories.append(directory_inventory_sha256(output))
    assert b"\r\n" in configs[0] and b"\r" not in configs[1]   # the two platforms really wrote different bytes
    assert inventories[0] == inventories[1]


def test_json_newline_normalization_never_changes_content(tmp_path):
    module = api()
    invalid = b'{"a":\r\n"x\ry"}'   # a raw CR inside a string is not JSON
    (tmp_path / "config.json").write_bytes(invalid)
    with pytest.raises(ValueError, match="JSON"):
        module.normalize_json_newlines(tmp_path)
    assert (tmp_path / "config.json").read_bytes() == invalid
    (tmp_path / "config.json").write_bytes(b'{\r\n  "a": 1\r\n}\r\n')
    module.normalize_json_newlines(tmp_path)   # generation_config.json is absent: nothing to do
    assert (tmp_path / "config.json").read_bytes() == b'{\n  "a": 1\n}\n'


def test_publish_output_fsyncs_every_file_and_directory_before_the_move(tmp_path, monkeypatch):
    module = api()
    parent = tmp_path / "model-op"
    payload = parent / ".model-op-x" / "payload"
    (payload / "chat_templates").mkdir(parents=True)
    (payload / "config.json").write_text("{}", encoding="utf-8")
    (payload / "chat_templates" / "default.jinja").write_text("t", encoding="utf-8")
    events = []
    monkeypatch.setattr(module, "_fsync_file", lambda path: events.append(
        ("file", Path(path).relative_to(payload).as_posix())))
    monkeypatch.setattr(module, "_fsync_directory", lambda path: events.append(("directory", Path(path).name)))
    original = module._rename_noreplace

    def rename(source, destination):
        events.append(("rename", Path(destination).name))
        original(source, destination)

    monkeypatch.setattr(module, "_rename_noreplace", rename)
    output = parent / "output"
    module.publish_output(payload, output)
    moved = events.index(("rename", "output"))
    assert {event for event in events[:moved] if event[0] == "file"} == {
        ("file", "config.json"), ("file", "chat_templates/default.jinja")}
    assert {("directory", "payload"), ("directory", "chat_templates")} <= set(events[:moved])
    assert events[moved + 1:] == [("directory", "model-op")]
    assert (output / "chat_templates" / "default.jinja").read_text(encoding="utf-8") == "t"
    assert not payload.exists()


@pytest.mark.parametrize("existing", ["file", "empty_directory", "race"])
def test_publish_output_never_replaces_an_existing_path(tmp_path, monkeypatch, existing):
    module = api()
    payload = tmp_path / ".model-op-x"
    payload.mkdir()
    (payload / "config.json").write_text("{}", encoding="utf-8")
    output = tmp_path / "output"
    if existing == "file":
        output.write_text("keep", encoding="utf-8")
    elif existing == "empty_directory":
        output.mkdir()
    else:
        # An empty directory appears after the existence check, in the window before the rename.
        def racing_fsync(path):
            if Path(path) == payload:
                output.mkdir()

        monkeypatch.setattr(module, "_fsync_directory", racing_fsync)
    with pytest.raises(ValueError, match="model-operation output already exists"):
        module.publish_output(payload, output)
    assert (payload / "config.json").read_text(encoding="utf-8") == "{}"
    if existing == "file":
        assert output.read_text(encoding="utf-8") == "keep"
    else:
        assert output.is_dir() and list(output.iterdir()) == []


def test_posix_no_replace_move_uses_renameat2_or_exclusive_links(tmp_path):
    import ctypes
    import errno
    import os
    module = api()
    payload = tmp_path / "payload"
    (payload / "sub").mkdir(parents=True)
    (payload / "config.json").write_text("{}", encoding="utf-8")
    (payload / "sub" / "file.txt").write_text("x", encoding="utf-8")
    calls = []

    def renamed(*args):
        calls.append(args)
        return 0

    target = tmp_path / "out"
    assert module._renameat2_noreplace(payload, target, function=renamed) is True
    assert calls == [(-100, os.fsencode(payload), -100, os.fsencode(target), 1)]   # AT_FDCWD, RENAME_NOREPLACE

    def failing(code):
        def function(*args):
            ctypes.set_errno(code)
            return -1
        return function

    with pytest.raises(FileExistsError):
        module._renameat2_noreplace(payload, target, function=failing(errno.EEXIST))
    for code in (errno.ENOSYS, errno.EINVAL):
        assert module._renameat2_noreplace(payload, target, function=failing(code)) is False
    with pytest.raises(PermissionError):
        module._renameat2_noreplace(payload, target, function=failing(errno.EACCES))
    # Portable fallback: every directory is created exclusively and every file is hard-linked (never replaced).
    taken = tmp_path / "taken"
    taken.mkdir()
    with pytest.raises(FileExistsError):
        module._move_tree_noreplace(payload, taken)
    assert list(taken.iterdir()) == [] and (payload / "sub" / "file.txt").is_file()
    module._move_tree_noreplace(payload, tmp_path / "moved")
    assert (tmp_path / "moved" / "sub" / "file.txt").read_text(encoding="utf-8") == "x"
    assert (tmp_path / "moved" / "config.json").read_text(encoding="utf-8") == "{}"
    assert not payload.exists()


def test_hardlink_fallback_removes_a_partial_output_when_it_fails(tmp_path, monkeypatch):
    import os
    module = api()
    payload = tmp_path / "payload"
    (payload / "sub").mkdir(parents=True)
    for name in ("a.json", "b.json", "sub/c.json"):
        (payload / name).write_text(name, encoding="utf-8")
    original, linked = os.link, []

    def flaky_link(source, target):
        if len(linked) == 1:
            raise PermissionError(1, "hard links are not permitted on this filesystem")
        linked.append(target)
        original(source, target)

    monkeypatch.setattr(os, "link", flaky_link)
    output = tmp_path / "output"
    with pytest.raises(PermissionError):
        module._move_tree_noreplace(payload, output)
    assert not output.exists() and not output.is_symlink()   # never a half-filled model-op/output
    # A destination that already existed is not ours: it is refused and never removed.
    taken = tmp_path / "taken"
    taken.mkdir()
    (taken / "keep.txt").write_text("keep", encoding="utf-8")
    with pytest.raises(FileExistsError):
        module._move_tree_noreplace(payload, taken)
    assert (taken / "keep.txt").read_text(encoding="utf-8") == "keep"


def test_inspect_linear_modules_is_reexported_and_lazy(monkeypatch):
    module = api()
    from tuning_factory import target_inspection
    assert module.inspect_linear_modules is target_inspection.inspect_linear_modules
    block_heavy_imports(monkeypatch)
    with pytest.raises(RuntimeError, match="model-operation runtime is missing"):
        module.inspect_linear_modules(ROOT)


# -- Task 7.3: execute_operation dispatch (fake model_ops, no torch) -----------------------------

JOB_DIRECTORY = "6" * 32
ATTEMPT = "7" * 32


def dispatch():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.operation_dispatch")
    except ModuleNotFoundError:
        pytest.fail("operation dispatch is missing")


def launch(tmp_path, name):
    directory = tmp_path / JOB_DIRECTORY / name
    directory.mkdir(parents=True)
    return directory


def dispatch_fixture(tmp_path):
    import test_model_inputs as inputs

    from tuning_factory.model_artifacts import ModelArtifactStore
    store = ModelArtifactStore(tmp_path / "model-cache")
    base = inputs.frozen(store, inputs.full_model(tmp_path, "base"))
    first = inputs.frozen(store, inputs.peft_adapter(tmp_path, "first", base=base[0], parents=(base[0],)))
    second = inputs.frozen(store, inputs.peft_adapter(tmp_path, "second", rank=3, alpha=9, base=base[0],
                                                      parents=(base[0],)))
    return inputs, store, base[0], first[0], second[0]


def pinned_runtime(monkeypatch):
    from importlib import metadata

    from tuning_factory.runtime_profiles import MODEL_OPS_PINS
    installed = {**MODEL_OPS_PINS, "llamafactory": "0.9.5"}

    def version(name):
        if name not in installed:
            raise metadata.PackageNotFoundError(name)
        return installed[name]

    monkeypatch.setattr(metadata, "version", version)
    return installed


def full_output_files(output):
    import test_model_inputs as inputs
    output.mkdir(parents=True)
    (output / "config.json").write_bytes(inputs.CONFIG)
    inputs.write_safetensors(output / "model.safetensors", {f"{name}.weight": (2, 2) for name in inputs.MODULES})


def request_for(operation, directory, **changes):
    from tuning_factory.operation_runner import operation_sha256
    from tuning_factory.worker_protocol import WorkerRequest
    launch_id = directory.name if re.fullmatch(r"[0-9a-f]{32}", directory.name) else "8" * 32
    return WorkerRequest(**{"attempt_id": ATTEMPT, "launch_id": launch_id, "manifest_sha256": "9" * 64,
                            "operation_sha256": operation_sha256(operation), "timeout_seconds": 900, **changes})


def run(operation, directory, store, *, timeout=60, canceled=lambda: False, **kwargs):
    return dispatch().execute_operation(operation, directory, timeout, canceled,
                                        request=kwargs.pop("request", request_for(operation, directory)),
                                        model_cache=kwargs.pop("model_cache", store.root), **kwargs)


def bound_preflight(config, paths):
    return {"bound_model": paths.path("policy_base").as_posix()}


class Clock:
    """The fake monotonic clock of operation_dispatch."""

    def __init__(self, monkeypatch):
        self.now = 1000.0
        monkeypatch.setattr(dispatch(), "_clock", lambda: self.now)

    def advance(self, seconds):
        self.now += seconds


class FakeOps:
    """Records every model-operation call; writes small fixture outputs (never real weights)."""

    def __init__(self, receipt_changes=None, *, extra_files=(), after=None, targets=None):
        self.calls, self.receipt_changes = [], receipt_changes or {}
        self.extra_files, self.after, self.targets = extra_files, after, targets

    def _finish(self, output):
        for name in self.extra_files:
            (output / name).parent.mkdir(parents=True, exist_ok=True)
            (output / name).write_text("{}", encoding="utf-8")
        if self.after is not None:
            self.after()

    def merge_adapters(self, inputs, weights, output, *, base, policy, device, require_real, probe_ids=None):
        self.calls.append(("merge", tuple(inputs), tuple(weights), output, base, policy, device, require_real))
        output.mkdir()
        (output / "adapter_config.json").write_text("{}", encoding="utf-8")
        (output / "adapter_model.safetensors").write_bytes(b"fixture")
        self._finish(output)
        return api().MergeReceipt(**{
            "policy_id": policy, "inputs": tuple(model.identity for model in inputs), "normalized_weights": (0.25, 0.75),
            "output_rank": 5, "max_abs_delta_error": 1e-9, "max_abs_logits_error": 1e-8, "finite_tensors_checked": 8,
            "output_files": ("adapter_config.json", "adapter_model.safetensors"), **self.receipt_changes})

    def materialize_model(self, load, output, *, policy, device, probe_ids=None):
        self.calls.append(("materialize", load, output, policy, device))
        full_output_files(output)
        self._finish(output)
        return api().MaterializeReceipt(**{
            "policy_id": policy, "base": load.base.identity, "adapter": load.adapter.identity,
            "max_abs_delta_error": 1e-9, "max_abs_logits_error": 1e-8, "non_target_tensors_identical": True,
            "tokenizer_fingerprint": load.base.tokenizer_fingerprint, "finite_tensors_checked": 4,
            "output_files": ("config.json", "model.safetensors"), **self.receipt_changes})

    def inspect_linear_modules(self, model_dir):
        from tuning_factory.graph_planner import TargetModuleEvidence
        self.calls.append(("inspect", Path(model_dir)))
        return tuple(TargetModuleEvidence(name=name, input_dimension=2, output_dimension=2)
                     for name in ("model.q_proj", "model.v_proj"))

    def inspect_target_modules(self, model_dir, base):
        if self.targets is None:
            raise AssertionError("continuation inspection is not part of this operation")
        self.calls.append(("targets", Path(model_dir), base))
        return self.targets(base)


def test_execute_operation_dispatches_merge_and_materialize_to_model_ops(tmp_path, monkeypatch):
    from tuning_factory.model_artifacts import directory_inventory_sha256
    from tuning_factory.operation_runner import operation_sha256
    from tuning_factory.operations import MaterializeOperation, MergeOperation
    inputs, store, base_ref, first_ref, second_ref = dispatch_fixture(tmp_path)
    pinned_runtime(monkeypatch)
    block_heavy_imports(monkeypatch)   # the device probe needs no torch in the main env: cpu
    merge = MergeOperation(models=(first_ref, second_ref), weights=(0.25, 0.75), resources=inputs.resources(),
                           lineage=inputs.lineage())
    directory = launch(tmp_path, "1" * 32)
    from tuning_factory.operation_runner import stage_model_inputs
    stage_model_inputs(merge, store, directory)
    ops = FakeOps()
    result = run(merge, directory, store, ops=ops)
    _, received, weights, output, base, policy, device, require_real = ops.calls[0]
    assert [model.identity for model in received] == [first_ref, second_ref]
    assert [Path(model.path).as_posix() for model in received] == [inputs.cache_path(store, ref)
                                                                    for ref in (first_ref, second_ref)]
    assert [model.descriptor for model in received] == [store.verify(ref) for ref in (first_ref, second_ref)]
    assert (weights, policy, device, require_real) == ((0.25, 0.75), "model-ops-f32-v1", "cpu", True)
    assert base.identity == base_ref and Path(base.path).as_posix() == inputs.cache_path(store, base_ref)
    assert output == directory / "model-op" / "output"
    assert (result.status, result.kind, result.launch_id) == ("COMPLETED", "merge", directory.name)
    assert (result.attempt_id, result.manifest_sha256) == (ATTEMPT, "9" * 64)
    assert result.operation_sha256 == operation_sha256(merge) and result.engine_version == "0.9.5"
    receipt_bytes = (directory / "operation-receipt.json").read_bytes()
    receipt = json.loads(receipt_bytes)
    assert receipt["receipt"]["kind"] == "merge" and receipt["output"] == "model-op/output"
    assert receipt["output_inventory_sha256"] == directory_inventory_sha256(output)
    assert result.evidence["operation-receipt.json"] == sha(receipt_bytes)
    binding = json.loads((directory / "model-binding.json").read_bytes())
    assert [(item["role"], item["index"]) for item in binding["models"]] == [
        ("operation_adapter", 0), ("operation_adapter", 1), ("operation_base", 0)]
    assert result.evidence["model-binding.json"] == sha((directory / "model-binding.json").read_bytes())
    assert "target-inventory.json" not in result.evidence

    materialize = MaterializeOperation(model=first_ref, resources=inputs.resources(), lineage=inputs.lineage())
    directory = launch(tmp_path, "2" * 32)
    stage_model_inputs(materialize, store, directory)
    result = run(materialize, directory, store, ops=ops)
    _, load, output, policy, device = ops.calls[1]
    assert (load.base.identity, load.adapter.identity) == (base_ref, first_ref)
    assert Path(load.adapter.path).as_posix() == inputs.cache_path(store, first_ref)
    assert (output, policy, device) == (directory / "model-op" / "output", "model-ops-f32-v1", "cpu")
    assert ops.calls[2] == ("inspect", output)
    inventory = json.loads((directory / "target-inventory.json").read_bytes())
    assert inventory == {"schema_version": 1, "modules": [
        {"name": "model.q_proj", "input_dimension": 2, "output_dimension": 2},
        {"name": "model.v_proj", "input_dimension": 2, "output_dimension": 2}]}
    assert result.evidence["target-inventory.json"] == sha((directory / "target-inventory.json").read_bytes())
    assert (result.status, result.kind) == ("COMPLETED", "materialize")


def test_model_ops_refused_in_ppo_runtime_or_on_pin_drift(tmp_path, monkeypatch):
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import MergeOperation
    inputs, store, _, first_ref, second_ref = dispatch_fixture(tmp_path)
    installed = pinned_runtime(monkeypatch)
    ops = FakeOps()
    ppo = MergeOperation(models=(first_ref, second_ref), weights=(0.25, 0.75), resources=inputs.resources("ppo"),
                         lineage=inputs.lineage())
    first_directory = launch(tmp_path, "1" * 32)
    stage_model_inputs(ppo, store, first_directory)
    with pytest.raises(ValueError, match="^model operation requires the primary runtime with pinned PEFT 0.18.1$"):
        run(ppo, first_directory, store, ops=ops)
    primary = MergeOperation(models=(first_ref, second_ref), weights=(0.25, 0.75), resources=inputs.resources(),
                             lineage=inputs.lineage())
    directory = launch(tmp_path, "2" * 32)
    stage_model_inputs(primary, store, directory)
    installed["peft"] = "0.18.0"
    with pytest.raises(ValueError, match="model operation requires the primary runtime with pinned PEFT 0.18.1: peft"):
        run(primary, directory, store, ops=ops)
    assert ops.calls == []
    assert not (first_directory / "model-op").exists() and not (directory / "model-op").exists()


def materialize_launch(tmp_path, monkeypatch, name="1" * 32):
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import MaterializeOperation
    inputs, store, base_ref, first_ref, _ = dispatch_fixture(tmp_path)
    pinned_runtime(monkeypatch)
    materialize = MaterializeOperation(model=first_ref, resources=inputs.resources(), lineage=inputs.lineage())
    directory = launch(tmp_path, name)
    stage_model_inputs(materialize, store, directory)
    return store, materialize, directory


def test_model_operation_owns_an_exclusive_output_parent(tmp_path, monkeypatch):
    from tuning_factory.operation_runner import stage_model_inputs
    store, materialize, directory = materialize_launch(tmp_path, monkeypatch)
    # A crashed run left staging inside this attempt's own output parent: it is never reused or deleted.
    stale = directory / "model-op" / ".model-op-stale" / "payload"
    stale.mkdir(parents=True)
    (stale / "partial.part").write_bytes(b"partial")
    ops = FakeOps()
    with pytest.raises(ValueError, match="model-operation output parent already exists"):
        run(materialize, directory, store, ops=ops)
    assert ops.calls == [] and (stale / "partial.part").read_bytes() == b"partial"
    # The launch directory holds no weights before any operation, and its name is the launch_id.
    for name, setup, message in (
            ("2" * 32, lambda path: (path / "stray.safetensors").write_bytes(b"w"), "must not contain model weights"),
            ("not-a-launch-id", lambda path: None, "launch directory must be named by its 32-hex launch_id")):
        directory = launch(tmp_path, name)
        stage_model_inputs(materialize, store, directory)
        setup(directory)
        with pytest.raises(ValueError, match=message):
            run(materialize, directory, store, ops=ops)
    assert ops.calls == []


@pytest.mark.parametrize("changes", [{"output_files": ("model.safetensors",)}, {"policy_id": "other"},
                                     {"output_files": ("config.json", "model.safetensors", "model.safetensors")}])
def test_model_operation_receipt_must_describe_this_operation_and_output(tmp_path, monkeypatch, changes):
    store, materialize, directory = materialize_launch(tmp_path, monkeypatch)
    with pytest.raises(ValueError, match="model-operation receipt differs from the operation or its output"):
        run(materialize, directory, store, ops=FakeOps(changes))
    assert not (directory / "operation-receipt.json").exists()


def test_receipt_file_order_is_irrelevant_but_the_file_set_is_exact(tmp_path, monkeypatch):
    store, materialize, directory = materialize_launch(tmp_path, monkeypatch)
    # Depth-first order lists x/a.json before its sibling file x.json; global sorting does the opposite.
    depth_first = ("config.json", "model.safetensors", "x/a.json", "x.json")
    ops = FakeOps({"output_files": depth_first}, extra_files=("x.json", "x/a.json"))
    result = run(materialize, directory, store, ops=ops)
    assert result.status == "COMPLETED"
    receipt = json.loads((directory / "operation-receipt.json").read_bytes())
    assert tuple(receipt["receipt"]["output_files"]) == depth_first


def test_execute_operation_requires_its_request_and_absolute_caches(tmp_path, monkeypatch):
    store, materialize, directory = materialize_launch(tmp_path, monkeypatch)
    ops = FakeOps()
    for request, message in (
            (request_for(materialize, directory, launch_id="d" * 32),
             "worker request launch identity differs from launch directory"),
            (request_for(materialize, directory, operation_sha256="0" * 64),
             "worker request operation hash differs from manifest operation")):
        with pytest.raises(ValueError, match=message):
            run(materialize, directory, store, ops=ops, request=request)
    for changes in ({"model_cache": Path("relative-cache")}, {"hf_home": Path("relative-hf")}):
        with pytest.raises(ValueError, match="absolute"):
            run(materialize, directory, store, ops=ops, **changes)
    assert ops.calls == [] and not (directory / "model-op").exists()


def train_launch(tmp_path, monkeypatch, *, method="lora", continuation=False, name="1" * 32):
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import TrainOperation
    from tuning_factory.projects import DatasetRef
    from tuning_factory.training import LlamaFactoryBackend
    inputs, store, base_ref, first_ref, _ = dispatch_fixture(tmp_path)
    pinned_runtime(monkeypatch)
    raw = (ROOT / "examples" / "dummy.json").read_bytes()
    if continuation:
        model_input = inputs.policy(base_ref, first_ref)
    else:
        model_input = inputs.policy(base_ref) if method == "lora" else inputs.full_weights(base_ref)
    operation = TrainOperation(config=inputs.graph_config(method=method), model_input=model_input,
                               dataset=DatasetRef(dataset_id="data", version="1", sha256=sha(raw)),
                               resources=inputs.resources(), lineage=inputs.lineage())
    directory = launch(tmp_path, name)
    LlamaFactoryBackend().prepare(operation.config, directory, raw, operation.model_input)
    staging = {}
    if continuation:
        staging = dict(target_evidence=(inputs.evidence(base_ref),),
                       producers=(inputs.producer(first_ref, store.verify(first_ref)),))
    stage_model_inputs(operation, store, directory, **staging)
    return inputs, store, base_ref, first_ref, operation, directory


def recording_engine(method="lora", runs=None):
    from tuning_factory.training import LlamaFactoryBackend
    runs = [] if runs is None else runs

    class Engine(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            runs.append((filename, timeout))
            output = Path(directory) / "output"
            if method in {"lora", "qlora"}:
                output.mkdir()
                (output / "adapter_config.json").write_text('{"peft_type":"LORA"}', encoding="utf-8")
                (output / "adapter_model.safetensors").write_bytes(b"fixture")
            else:
                full_output_files(output)

    return Engine(), runs


def test_engine_budget_excludes_verification_time(tmp_path, monkeypatch):
    module = dispatch()
    clock = Clock(monkeypatch)
    _, store, _, _, operation, directory = train_launch(tmp_path, monkeypatch)
    verify = module.verify_model_paths

    def slow_verification(*args, **kwargs):
        clock.advance(120)   # cache hashing and an HF snapshot download take time
        return verify(*args, **kwargs)

    monkeypatch.setattr(module, "verify_model_paths", slow_verification)
    engine, runs = recording_engine()
    result = run(operation, directory, store, timeout=600, backend=engine, preflight=bound_preflight)
    assert result.status == "COMPLETED"
    assert runs == [("train.bound.yaml", 480.0)]   # one deadline: the engine gets what verification left
    # The same deadline also stops before the engine when verification used it all up.
    _, store, _, _, operation, directory = train_launch(tmp_path / "late", monkeypatch)
    engine, runs = recording_engine()
    with pytest.raises(TimeoutError, match="deadline"):
        run(operation, directory, store, timeout=100, backend=engine, preflight=bound_preflight)
    assert runs == [] and not (directory / "train.bound.yaml").exists()


def test_cancel_during_verification_is_honoured(tmp_path, monkeypatch):
    from tuning_factory.training import CanceledError
    module = dispatch()
    _, store, _, _, operation, directory = train_launch(tmp_path, monkeypatch)
    verify, flag = module.verify_model_paths, []

    def canceled_while_verifying(*args, **kwargs):
        flag.append(True)   # the control plane writes the cancel marker meanwhile
        return verify(*args, **kwargs)

    monkeypatch.setattr(module, "verify_model_paths", canceled_while_verifying)
    engine, runs = recording_engine()
    with pytest.raises(CanceledError, match="cancellation requested"):
        run(operation, directory, store, canceled=lambda: bool(flag), backend=engine, preflight=bound_preflight)
    assert runs == [] and not (directory / "train.bound.yaml").exists()
    store, materialize, directory = materialize_launch(tmp_path / "materialize", monkeypatch)
    flag.clear()
    ops = FakeOps()
    with pytest.raises(CanceledError):
        run(materialize, directory, store, canceled=lambda: bool(flag), ops=ops)
    assert ops.calls == [] and not (directory / "model-op").exists()


@pytest.mark.parametrize("kind", ["merge", "materialize"])
@pytest.mark.parametrize("case", ["deadline", "cancel"])
def test_model_operation_past_its_deadline_or_canceled_never_completes(tmp_path, monkeypatch, kind, case):
    from tuning_factory.operation_runner import stage_model_inputs
    from tuning_factory.operations import MaterializeOperation, MergeOperation
    from tuning_factory.training import CanceledError
    clock = Clock(monkeypatch)
    inputs, store, _, first_ref, second_ref = dispatch_fixture(tmp_path)
    pinned_runtime(monkeypatch)
    operation = (MergeOperation(models=(first_ref, second_ref), weights=(0.25, 0.75), resources=inputs.resources(),
                                lineage=inputs.lineage()) if kind == "merge"
                 else MaterializeOperation(model=first_ref, resources=inputs.resources(), lineage=inputs.lineage()))
    directory = launch(tmp_path, "1" * 32)
    stage_model_inputs(operation, store, directory)
    flag = []
    # Merge/materialize cannot be interrupted, so the check after they return decides.
    ops = FakeOps(after=(lambda: clock.advance(61)) if case == "deadline" else (lambda: flag.append(True)))
    expected = TimeoutError if case == "deadline" else CanceledError
    with pytest.raises(expected):
        run(operation, directory, store, timeout=60, canceled=lambda: bool(flag), ops=ops)
    assert len([call for call in ops.calls if call[0] in {"merge", "materialize"}]) == 1
    assert not (directory / "operation-receipt.json").exists()


def test_continuation_through_execute_operation_uses_the_worker_inspector(tmp_path, monkeypatch):
    inputs, store, base_ref, first_ref, operation, directory = train_launch(tmp_path, monkeypatch, continuation=True)
    ops = FakeOps(targets=lambda base: inputs.evidence(base))
    engine, runs = recording_engine()
    result = run(operation, directory, store, backend=engine, preflight=bound_preflight, ops=ops)
    assert result.status == "COMPLETED" and [name for name, _ in runs] == ["train.bound.yaml"]
    assert ops.calls == [("targets", Path(inputs.cache_path(store, base_ref)), base_ref)]
    bound = (directory / "train.bound.yaml").read_text(encoding="utf-8")
    assert inputs.cache_path(store, first_ref) in bound and "create_new_adapter: false" in bound
    # The worker's own inspection decides: an incomplete inventory is refused before the engine runs.
    _, store, base_ref, _, operation, directory = train_launch(tmp_path / "narrow", monkeypatch, continuation=True)
    ops = FakeOps(targets=lambda base: inputs.evidence(base, ("model.q_proj",)))
    engine, runs = recording_engine()
    with pytest.raises(ValueError, match="inspected target modules differ"):
        run(operation, directory, store, backend=engine, preflight=bound_preflight, ops=ops)
    assert runs == []


def test_training_requires_the_worker_preflight(tmp_path, monkeypatch):
    _, store, _, _, operation, directory = train_launch(tmp_path, monkeypatch)
    engine, runs = recording_engine()
    with pytest.raises(ValueError, match="bound runtime preflight"):
        run(operation, directory, store, backend=engine)
    assert runs == []


def evaluate_fixture(tmp_path, **benchmark_changes):
    from tuning_factory.operations import EvaluateOperation
    from tuning_factory.projects import DatasetRef
    inputs, store, base_ref, first_ref, _ = dispatch_fixture(tmp_path)
    raw = (ROOT / "examples" / "dummy.json").read_bytes()
    spec = inputs.benchmark(DatasetRef(dataset_id="bench", version="1", sha256=sha(raw)),
                            ordered_sample_ids=("row:2", "row:0"), **benchmark_changes)
    operation = EvaluateOperation(model=first_ref, benchmark=spec, resources=inputs.resources(),
                                  lineage=inputs.lineage())
    return inputs, store, base_ref, first_ref, raw, operation


def evaluate_launch(tmp_path, store, operation, raw, name):
    from tuning_factory.operation_runner import stage_model_inputs
    directory = launch(tmp_path, name)
    stage_model_inputs(operation, store, directory)
    dispatch().write_benchmark_data(directory, raw)
    return directory


def test_evaluate_runs_ordered_subset_and_writes_predictions(tmp_path, monkeypatch):
    from tuning_factory.training import LlamaFactoryBackend
    clock = Clock(monkeypatch)
    inputs, store, base_ref, first_ref, raw, evaluate = evaluate_fixture(tmp_path)
    preflights, calls = [], []

    def preflight(benchmark, paths, gpu_count):
        preflights.append((paths.path("operation_base"), gpu_count))
        clock.advance(15)   # loading the bound config and tokenizer uses part of the budget
        return {"fixture": True}

    class Backend(LlamaFactoryBackend):
        def predict_bound(self, directory, plan, sample_ids, timeout, canceled=lambda: False):
            calls.append((plan, tuple(sample_ids), timeout))
            (Path(directory) / "predict.yaml").write_text(json.dumps(plan), encoding="utf-8")
            (Path(directory) / "output").mkdir()
            (Path(directory) / "output" / "predictions.json").write_bytes(b'{"predictions":[]}')

    def evaluate_once(operation, directory):
        return run(operation, directory, store, backend=Backend(), evaluation_preflight=preflight)

    directory = evaluate_launch(tmp_path, store, evaluate, raw, "1" * 32)
    assert (directory / "data" / "benchmark.json").read_bytes() == raw
    result = evaluate_once(evaluate, directory)
    rows = json.loads(raw)
    subset = (directory / "data" / "validation.json").read_bytes()
    assert json.loads(subset) == [rows[2], rows[0]]   # ordered_sample_ids order, from the registered bytes
    info = json.loads((directory / "data" / "dataset_info.json").read_bytes())
    assert info["factory_validation"]["file_name"] == "validation.json"
    plan, sample_ids, timeout = calls[0]
    assert sample_ids == ("row:2", "row:0") and timeout == 45.0
    assert {key: plan[key] for key in ("max_samples", "template", "cutoff_len", "max_new_tokens", "seed", "do_sample",
                                       "bf16", "fp16", "finetuning_type", "stage", "do_predict")} == {
        "max_samples": 2, "template": "qwen", "cutoff_len": 128, "max_new_tokens": 16, "seed": 7, "do_sample": False,
        "bf16": True, "fp16": False, "finetuning_type": "lora", "stage": "sft", "do_predict": True}
    assert plan["model_name_or_path"] == inputs.cache_path(store, base_ref)
    assert plan["adapter_name_or_path"] == inputs.cache_path(store, first_ref)
    assert "quantization_bit" not in plan and inputs.ROOT.repo_id not in json.dumps(plan)
    assert preflights == [(Path(inputs.cache_path(store, base_ref)), 1)]
    assert (result.status, result.kind) == ("COMPLETED", "evaluate")
    assert result.evidence["data/validation.json"] == sha(subset)
    assert result.evidence["output/predictions.json"] == sha(b'{"predictions":[]}')
    for index, (changes, message) in enumerate((
            ({"generation_mode": "sample"}, "^v2 evaluation supports greedy causal-LM generation benchmarks only$"),
            ({"precision": "fp32"}, "^fp32 benchmark precision cannot be enforced by the pinned engine$"),
            ({"semantic_model_kind": "reward_model"},
             "^v2 evaluation supports greedy causal-LM generation benchmarks only$"),
            ({"tokenizer_fingerprint": "0" * 64}, "benchmark tokenizer differs from the evaluated model"))):
        refused = evaluate.model_copy(update={"benchmark": evaluate.benchmark.model_copy(update=changes)})
        directory = evaluate_launch(tmp_path, store, refused, raw, f"{index + 2}" * 32)
        with pytest.raises(ValueError, match=message):
            evaluate_once(refused, directory)
    assert len(calls) == 1
    directory = evaluate_launch(tmp_path, store, evaluate, raw, "9" * 32)
    (directory / "data" / "benchmark.json").write_bytes(raw.replace(b"DEMO-001", b"DEMO-002"))
    with pytest.raises(ValueError, match="transferred dataset hash mismatch"):
        evaluate_once(evaluate, directory)
    assert len(calls) == 1


def test_predict_bound_runs_the_engine_on_a_path_bound_plan(tmp_path):
    import test_model_inputs as inputs
    import yaml

    from tuning_factory.training import LlamaFactoryBackend
    spec = inputs.benchmark(quantization_bit=4, precision="fp16")
    plan = LlamaFactoryBackend.prediction_plan(spec, model_path="/cache/base/files", adapter_path=None, max_samples=2)
    assert plan["model_name_or_path"] == "/cache/base/files" and "adapter_name_or_path" not in plan
    assert (plan["finetuning_type"], plan["quantization_bit"], plan["quantization_method"]) == ("full", 4, "bnb")
    assert (plan["fp16"], plan["bf16"], plan["do_sample"], plan["max_samples"]) == (True, False, False, 2)
    runs = []

    class Engine(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            runs.append((filename, log_name))
            (Path(directory) / "prediction").mkdir()
            rows = [{"predict": " first ", "label": "a"}, {"predict": "second", "label": "b"}]
            (Path(directory) / "prediction" / "generated_predictions.jsonl").write_text(
                "".join(json.dumps(row) + "\n" for row in rows), encoding="utf-8")
            (Path(directory) / log_name).write_text("Quantizing model to 4 bit with bitsandbytes.\n", encoding="utf-8")

    result = Engine().predict_bound(tmp_path, plan, ("row:2", "row:0"), 60)
    assert runs == [("predict.yaml", "predict.log")]
    assert yaml.safe_load((tmp_path / "predict.yaml").read_text(encoding="utf-8")) == plan
    assert result == {"ordered_sample_ids": ["row:2", "row:0"], "predictions": ["first", "second"],
                      "source": "llamafactory-native-generation"}
    assert json.loads((tmp_path / "output" / "predictions.json").read_bytes()) == result
    again = tmp_path / "again"
    again.mkdir()
    with pytest.raises(ValueError, match="invalid sample count"):
        Engine().predict_bound(again, plan, ("row:2",), 60)


@pytest.mark.parametrize("method", ["full", "freeze", "lora"])
def test_full_model_outputs_emit_target_inventory(tmp_path, monkeypatch, method):
    _, store, _, _, operation, directory = train_launch(tmp_path, monkeypatch, method=method)
    engine, runs = recording_engine(method)
    ops = FakeOps()
    result = run(operation, directory, store, backend=engine, ops=ops, preflight=bound_preflight)
    assert [name for name, _ in runs] == ["train.bound.yaml"] and result.kind == "train"
    assert result.evidence["train.bound.yaml"] == sha((directory / "train.bound.yaml").read_bytes())
    if method == "lora":
        assert ops.calls == [] and not (directory / "target-inventory.json").exists()
        return
    assert ops.calls == [("inspect", directory / "output")]
    inventory = (directory / "target-inventory.json").read_bytes()
    assert [item["name"] for item in json.loads(inventory)["modules"]] == ["model.q_proj", "model.v_proj"]
    assert result.evidence["target-inventory.json"] == sha(inventory)


def test_worker_protocol_dispatch_and_staging_are_layered():
    import ast
    package = ROOT / "src" / "tuning_factory"

    def imported(name):
        tree = ast.parse((package / f"{name}.py").read_text(encoding="utf-8"))
        return {node.module.lstrip(".") for node in ast.walk(tree)
                if isinstance(node, ast.ImportFrom) and node.module} | {
            alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom) and node.module is None
            for alias in node.names}

    # Staging/binding (control plane, 7.5) never reaches the worker; dispatch receives its preflights.
    assert not imported("operation_runner") & {"worker", "worker_protocol", "operation_dispatch", "graph_store",
                                                "jobs"}
    assert not imported("operation_dispatch") & {"worker", "graph_store", "jobs"}
    assert not imported("worker_protocol") & {"worker", "operation_runner", "operation_dispatch", "model_ops",
                                               "training"}
    for name in ("operation_runner", "operation_dispatch", "worker_protocol"):
        assert len((package / f"{name}.py").read_text(encoding="utf-8").splitlines()) < 700, name


def test_snapshot_copy_carries_processor_files_without_changing_the_tokenizer_fingerprint(tmp_path):
    from tuning_factory import model_ops
    from tuning_factory.model_artifacts import tokenizer_fingerprint
    source, target = tmp_path / "base", tmp_path / "out"
    source.mkdir()
    target.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json",
                 "video_preprocessor_config.json", "processor_config.json", "model.safetensors"):
        (source / name).write_text("{}")
    before = tokenizer_fingerprint(source)
    model_ops._copy_snapshot_files(source, target, config=False)
    assert sorted(path.name for path in target.iterdir()) == [
        "preprocessor_config.json", "processor_config.json", "tokenizer.json", "tokenizer_config.json",
        "video_preprocessor_config.json"]
    (source / "processor_config.json").unlink()
    assert tokenizer_fingerprint(source) == before


def test_resolved_lora_targets_follow_llamafactory_for_text_and_composite_models():
    from tuning_factory.target_inspection import resolved_lora_targets, target_leaves
    text = ["model.layers.0.self_attn.q_proj", "model.layers.0.self_attn.v_proj", "model.layers.0.mlp.up_proj"]
    assert resolved_lora_targets("all", text) == ("q_proj", "up_proj", "v_proj")
    assert resolved_lora_targets("q_proj,v_proj", text) == ("q_proj", "v_proj")
    composite = ["model.language_model.layers.0.linear_attn.in_proj_qkv",
                 "model.language_model.layers.3.self_attn.q_proj", "model.language_model.layers.3.self_attn.v_proj"]
    assert resolved_lora_targets("q_proj,v_proj", composite) == tuple(composite[1:])
    assert resolved_lora_targets("all", composite) == tuple(sorted(composite))
    assert target_leaves(composite) == ("in_proj_qkv", "q_proj", "v_proj") == target_leaves(("in_proj_qkv", "q_proj", "v_proj"))


COMPOSITE_INVENTORY = {
    "model.language_model.embed_tokens.weight": (32, 16), "model.language_model.norm.weight": (16,),
    "model.language_model.layers.0.linear_attn.in_proj_qkv.weight": (48, 16),
    "model.language_model.layers.0.linear_attn.conv1d.weight": (48, 1, 4),
    "model.language_model.layers.3.self_attn.q_proj.weight": (32, 16),
    "model.language_model.layers.3.self_attn.v_proj.weight": (8, 16),
    "model.visual.blocks.0.attn.qkv.weight": (24, 8), "model.visual.merger.linear_fc1.weight": (16, 16),
    "mtp.fc.weight": (16, 32), "lm_head.weight": (32, 16)}


def test_composite_inventories_verify_language_model_evidence_and_keep_the_full_rule_otherwise():
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    module = api()
    descriptor = full_descriptor(COMPOSITE_INVENTORY)
    ref = ref_for(descriptor)

    def evidence(*names):
        return ModelTargetEvidence(base=ref, modules=tuple(
            TargetModuleEvidence(name=name, input_dimension=COMPOSITE_INVENTORY[name + ".weight"][1],
                                 output_dimension=COMPOSITE_INVENTORY[name + ".weight"][0]) for name in names))

    language = ("model.language_model.layers.0.linear_attn.in_proj_qkv",
                "model.language_model.layers.3.self_attn.q_proj", "model.language_model.layers.3.self_attn.v_proj")
    assert module.verify_inspected_targets(evidence(*language), descriptor, ref).modules == evidence(*language).modules
    # gemma3's inspector evidence lists its vision tower too: the complete inventory is still accepted.
    everything = (*language, "model.visual.blocks.0.attn.qkv", "model.visual.merger.linear_fc1", "mtp.fc")
    assert module.verify_inspected_targets(evidence(*everything), descriptor, ref) == evidence(*everything)
    for partial in (language[1:], (*language, "model.visual.blocks.0.attn.qkv")):
        with pytest.raises(ValueError, match="inspected target modules differ from verified tensor inventory"):
            module.verify_inspected_targets(evidence(*partial), descriptor, ref)


def test_simulated_composite_adapters_record_full_language_model_names():
    from types import SimpleNamespace as NS

    from tuning_factory.simulated_dispatch import SimulatedWorker
    evidence = NS(modules=tuple(NS(name=name, input_dimension=16, output_dimension=out) for name, out in (
        ("model.language_model.layers.0.linear_attn.in_proj_qkv", 48),
        ("model.language_model.layers.3.self_attn.q_proj", 32), ("model.language_model.layers.3.self_attn.v_proj", 8))))

    def operation(lora_target):
        return NS(kind="train", model_input=NS(initialization="new_adapter"),
                  config=NS(hyperparameters=NS(lora_target=lora_target)))

    targets, modules = SimulatedWorker._adapter_modules(operation("q_proj,v_proj"), evidence, None)
    assert targets == ("model.language_model.layers.3.self_attn.q_proj",
                       "model.language_model.layers.3.self_attn.v_proj")
    assert modules == (("model.language_model.layers.3.self_attn.q_proj", 16, 32),
                       ("model.language_model.layers.3.self_attn.v_proj", 16, 8))
    assert SimulatedWorker._adapter_modules(operation("all"), evidence, None)[0] == tuple(
        sorted(item.name for item in evidence.modules))
    linear_only = NS(modules=evidence.modules[:1])   # no q_proj/v_proj anywhere: nothing would be adapted
    with pytest.raises(ValueError, match="simulated adapter target is absent from the inspected base modules"):
        SimulatedWorker._adapter_modules(operation("q_proj,v_proj"), linear_only, None)


def test_merge_lineage_compares_composite_adapter_targets_by_leaf_name():
    from types import SimpleNamespace as NS

    from tuning_factory.graph_planner import _MergeLineage
    adapter = lora_descriptor(targets=("model.language_model.layers.3.self_attn.q_proj",
                                       "model.language_model.layers.3.self_attn.v_proj"),
                              modules=("model.language_model.layers.3.self_attn.q_proj",
                                       "model.language_model.layers.3.self_attn.v_proj"))
    ref = ref_for(adapter)
    lineage = _MergeLineage({}, {}, {(ref.artifact_id, ref.inventory_sha256): NS(descriptor=adapter)})
    recipe = NS(hyperparameters=NS(lora_target="q_proj,v_proj"))
    assert lineage._targets(recipe, adapter.effective_base) == ("q_proj", "v_proj")
    assert lineage.candidates(ref) == frozenset({(adapter.effective_base.model_dump_json(), ("q_proj", "v_proj"))})


class FakeLinear:
    def __init__(self, inputs, outputs):
        self.in_features, self.out_features = inputs, outputs


class FakeClippableLinear:
    """Gemma 4's vision wrapper: "Linear" in its class name, but no in_features (its inner .linear has them)."""


def test_composite_inspection_uses_the_image_text_class_and_keeps_the_language_model(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS

    from tuning_factory import runtime_profiles, target_inspection
    modules = [("model.visual.blocks.0.attn.qkv", FakeLinear(16, 48)),
               ("model.vision_tower.encoder.layers.0.self_attn.q_proj", FakeClippableLinear()),
               ("model.visual.merger.linear_fc1", FakeLinear(64, 64)),
               ("model.language_model.layers.0.linear_attn.in_proj_qkv", FakeLinear(32, 96)),
               ("model.language_model.layers.3.self_attn.q_proj", FakeLinear(32, 64)), ("lm_head", FakeLinear(32, 64))]
    model = NS(named_modules=lambda: iter(modules))
    transformers = NS(AutoConfig=NS(from_pretrained=lambda *a, **k: NS(model_type="qwen3_5")),
                      AutoModelForCausalLM=NS(from_config=lambda config: pytest.fail("text-only class")),
                      AutoModelForImageTextToText=NS(from_config=lambda config: model))
    monkeypatch.setattr(runtime_profiles, "model_ops_runtime",
                        lambda: (NS(device=lambda name: nullcontext()), None, None, transformers))
    assert [item.name for item in target_inspection.inspect_linear_modules(tmp_path)] == [
        "model.language_model.layers.0.linear_attn.in_proj_qkv", "model.language_model.layers.3.self_attn.q_proj"]


def test_composite_models_load_through_the_image_text_class(tmp_path):
    from types import SimpleNamespace as NS

    from tuning_factory import model_ops
    (tmp_path / "config.json").write_text('{"model_type": "gemma4"}')
    loaded = NS(to=lambda device: NS(eval=lambda: "composite"))
    transformers = NS(AutoModelForCausalLM=NS(from_pretrained=lambda *a, **k: pytest.fail("text-only class")),
                      AutoModelForImageTextToText=NS(from_pretrained=lambda *a, **k: loaded))
    assert model_ops._load_fp32(NS(float32="f32"), transformers, tmp_path, "cpu") == "composite"


def test_materialized_key_set_ignores_only_the_checkpoint_tensors_transformers_never_loads(tmp_path, monkeypatch):
    from types import SimpleNamespace as NS

    from tuning_factory import model_ops

    class Tensor(int):
        def to(self, *args):
            return self

    base, payload = tmp_path / "base", tmp_path / "payload"
    stored = {base: {"model.language_model.norm.weight": Tensor(1), "mtp.fc.weight": Tensor(2)},
              payload: {"model.language_model.norm.weight": Tensor(1)}}
    monkeypatch.setattr(model_ops, "_load_tensors", lambda directory, *, adapter: dict(stored[directory]))
    monkeypatch.setattr(model_ops, "_expected_deltas", lambda *args: {})
    torch = NS(float32="f32", equal=lambda left, right: left == right)
    adapter = NS(descriptor=NS(adapter=NS(alpha=4, rank=2)))
    shared = "model.language_model.layers.2.self_attn.k_proj"      # Gemma 4: a KV-shared layer's projection
    stored[base].update({shared + ".weight": Tensor(3), "model.language_model.layers.12.self_attn.k_proj.weight": Tensor(4)})
    stored[payload]["model.language_model.layers.12.self_attn.k_proj.weight"] = Tensor(4)
    # The loaded model's declarations (transformers' _keys_to_ignore_on_load_unexpected), matched with re.search.
    qwen = NS(_keys_to_ignore_on_load_unexpected=[r"^mtp.*"])
    gemma = NS(_keys_to_ignore_on_load_unexpected=[shared])
    assert model_ops._unloaded_patterns(qwen) == (r"^mtp.*",) and model_ops._unloaded_patterns(NS()) == ()
    unloaded = model_ops._unloaded_patterns(qwen) + model_ops._unloaded_patterns(gemma)
    assert model_ops._verify_materialized(torch, adapter, base, payload, None, unloaded) == 0.0
    for partial in ((), model_ops._unloaded_patterns(qwen), model_ops._unloaded_patterns(gemma)):
        with pytest.raises(ValueError, match=model_ops.DELTA_FAILED):   # a text model keeps the exact key set
            model_ops._verify_materialized(torch, adapter, base, payload, None, partial)
    stored[payload]["mtp.fc.weight"] = Tensor(2)
    with pytest.raises(ValueError, match=model_ops.DELTA_FAILED):     # never loaded, so never saved
        model_ops._verify_materialized(torch, adapter, base, payload, None, unloaded)


def test_condensed_peft_suffixes_compare_by_leaf_and_select_the_same_modules():
    # PEFT 0.18.1 condenses 20+ full names to distinguishing suffixes; recorded by the CPU acceptance for a tiny
    # 3-layer Gemma 4 whose vision tower also has q_proj..down_proj (Task 14).
    from types import SimpleNamespace as NS

    from tuning_factory.target_inspection import target_leaves
    module = api()
    layer = ("mlp.down_proj", "mlp.gate_proj", "mlp.up_proj", "self_attn.k_proj", "self_attn.o_proj",
             "self_attn.q_proj", "self_attn.v_proj", "per_layer_input_gate", "per_layer_projection")
    names = [f"model.language_model.layers.{index}.{leaf}" for index in range(3) for leaf in layer]
    names.append("model.language_model.per_layer_model_projection")
    evidence = NS(modules=tuple(NS(name=name) for name in names))
    recorded = [f"{index}.{leaf}" for index in (1, 2) for leaf in layer[:7]]
    recorded += [f"language_model.layers.0.{leaf}" for leaf in layer[:7]]
    recorded += ["per_layer_input_gate", "per_layer_model_projection", "per_layer_projection"]
    assert target_leaves(recorded) == target_leaves(names)
    module.check_continuation_targets("all", recorded, evidence)
    with pytest.raises(ValueError, match="resolved adapter targets differ from requested targets"):
        module.check_continuation_targets("q_proj,v_proj", recorded, evidence)


def gemma4_config(layers=3, shared=1):
    return {"model_type": "gemma4", "text_config": {"model_type": "gemma4_text", "num_hidden_layers": layers,
                                                    "num_kv_shared_layers": shared}}


def test_unloaded_modules_follow_gemma4_kv_sharing_like_transformers():
    # Gemma4TextModel drops layers.{i}.self_attn.{k_proj,v_proj,k_norm,v_norm} for i >= layers - shared > 0.
    from tuning_factory.target_inspection import unloaded_modules
    assert unloaded_modules(gemma4_config()) == frozenset(
        f"model.language_model.layers.2.self_attn.{name}" for name in ("k_proj", "v_proj", "k_norm", "v_norm"))
    assert len(unloaded_modules(gemma4_config(35, 20))) == 4 * 20
    for config in (gemma4_config(3, 0), gemma4_config(3, 3), {"model_type": "gemma4", "text_config": {}},
                   {"model_type": "qwen3_5", "text_config": {"num_hidden_layers": 4, "num_kv_shared_layers": 1}},
                   {"model_type": "qwen2", "num_hidden_layers": 2}):
        assert unloaded_modules(config) == frozenset()
    for config in (gemma4_config("3", 1), gemma4_config(3, True), {"model_type": "gemma4", "text_config": [3]}):
        with pytest.raises(ValueError, match="invalid Gemma 4 text_config"):
            unloaded_modules(config)


def test_verify_inspected_targets_drops_kv_shared_modules_of_a_raw_gemma4_checkpoint_given_its_config():
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    module = api()
    inventory = {"model.language_model.embed_tokens.weight": (32, 16), "model.vision_tower.q_proj.linear.weight": (16, 8)}
    for index in range(3):
        for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
            inventory[f"model.language_model.layers.{index}.self_attn.{name}.weight"] = (16, 16)
        inventory[f"model.language_model.layers.{index}.self_attn.k_norm.weight"] = (16,)
    descriptor = full_descriptor(inventory)        # raw checkpoint: layer 2 still holds its k/v tensors
    ref = ref_for(descriptor)

    def evidence(skip=()):
        names = sorted(name[:-len(".weight")] for name in inventory if "_proj." in name and "vision" not in name)
        return ModelTargetEvidence(base=ref, modules=tuple(
            TargetModuleEvidence(name=name, input_dimension=16, output_dimension=16) for name in names
            if name not in skip))

    meta = evidence(skip=("model.language_model.layers.2.self_attn.k_proj", "model.language_model.layers.2.self_attn.v_proj"))
    assert module.verify_inspected_targets(meta, descriptor, ref, config=gemma4_config()) == meta
    with pytest.raises(ValueError, match="inspected target modules differ from verified tensor inventory"):
        module.verify_inspected_targets(meta, descriptor, ref)
    with pytest.raises(ValueError, match="inspected base dimensions differ from verified tensor inventory"):
        module.verify_inspected_targets(evidence(), descriptor, ref, config=gemma4_config())   # phantom modules
    assert module.verify_inspected_targets(evidence(), descriptor, ref) == evidence()   # no sharing: as before
