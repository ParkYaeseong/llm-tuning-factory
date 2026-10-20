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


def adapter_tensors(rank, *, modules=MODULES, in_dim=16, out_dim=16):
    from tuning_factory.model_artifacts import TensorMetadata
    tensors = []
    for module in modules:
        tensors.append(TensorMetadata(file="adapter_model.safetensors", name=f"base_model.model.{module}.lora_A.weight",
                                      shape=(rank, in_dim), dtype="F32"))
        tensors.append(TensorMetadata(file="adapter_model.safetensors", name=f"base_model.model.{module}.lora_B.weight",
                                      shape=(out_dim, rank), dtype="F32"))
    return tuple(tensors)


def lora_descriptor(rank=2, alpha=4.0, *, targets=("q_proj", "v_proj"), modules=MODULES, in_dim=16, out_dim=16,
                    effective_base=None, parents=(), config_sha256=None, tokenizer_fingerprint=None,
                    synthetic=False, simulation=False, method="lora", semantic_kind="causal_lm",
                    has_value_head=False, extra_tensors=(), marker="adapter", **adapter_changes):
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
        tensors=(*adapter_tensors(rank, modules=modules, in_dim=in_dim, out_dim=out_dim), *extra_tensors),
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
        tensors[item.name] = f32(item.shape, special=special if "lora_B" in item.name else None)
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
    reloaded = importlib.reload(module)
    assert reloaded.POLICY_F32_V1 == "model-ops-f32-v1"
    assert set(reloaded.TOLERANCE_POLICIES) == {"model-ops-f32-v1"}


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


@pytest.mark.parametrize("changes", [
    {"targets": ("q_proj",), "modules": MODULES[:1]},
    {"fan_in_fan_out": True},
])
def test_merge_refuses_target_module_or_fan_in_mismatch(changes):
    module = api()
    first = lora_descriptor()
    second = lora_descriptor(**changes, marker="other")
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
def test_merge_require_real_refuses_synthetic_or_simulation(flag):
    module = api()
    refs, descriptors = two_adapters(**{flag: True})
    with pytest.raises(ValueError, match="merge requires real completed artifacts"):
        module.validate_merge_inputs(refs, descriptors, (0.25, 0.75))
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
    refs, descriptors = two_adapters(synthetic=True)
    api().validate_merge_inputs(refs, descriptors, (0.25, 0.75), require_real=False)
    with pytest.raises(ValueError, match="merge requires real completed artifacts"):
        _merge_descriptors(refs, descriptors)


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
    assert dict(MODEL_OPS_PINS) == {name: gpu[name] for name in ("torch", "peft", "transformers", "safetensors")}
    with pytest.raises(TypeError):
        MODEL_OPS_PINS["torch"] = "0"
