"""Hash-locked CPU numerical acceptance of model operations (Task 7.1); it never skips.

Run only in the separate runtime installed from containers/model-ops-cpu-requirements.lock:

    .factory/model-ops-cpu-venv/Scripts/python.exe -I scripts/verify_model_ops_cpu.py --receipt PATH

Every fixture is generated locally (a WordLevel tokenizer, a tiny seeded Qwen2 and tiny seeded Qwen3.5 and
Gemma 4 composites). Nothing is downloaded, no GPU is used and no institution data is read. A missing runtime
exits with code 2.
The exit code is 0 only if every check passes. Module import touches only the standard library.
"""
from __future__ import annotations

import argparse
import hashlib
import importlib
import json
import os
import shutil
import subprocess
import sys
import tempfile
from importlib import metadata
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

SCHEMA_VERSION = 1
LOCK = REPO / "containers" / "model-ops-cpu-requirements.lock"
EXPECTED_VERSIONS = {
    "torch": "2.7.1+cpu", "peft": "0.18.1", "transformers": "5.6.0", "safetensors": "0.8.0",
    "tokenizers": "0.22.2", "accelerate": "1.11.0", "huggingface-hub": "1.33.0", "numpy": "2.5.4",
}
REQUIRED_MODULES = ("torch", "peft", "transformers", "safetensors", "tokenizers", "accelerate",
                    "huggingface_hub", "numpy")
SPECIAL_TOKENS = {"<unk>": 0, "<pad>": 1, "<s>": 2, "</s>": 3}
# 4 special + 23 "t<i>" + the 5 words of model_ops.PROBE_TEXT = vocabulary 32, so the default probe has no <unk>.
PROBE_WORDS = ("factory", "model", "operations", "numerical", "probe")
QWEN2 = dict(num_hidden_layers=2, hidden_size=16, intermediate_size=32, num_attention_heads=2,
             num_key_value_heads=2, vocab_size=32, max_position_embeddings=64, tie_word_embeddings=False,
             bos_token_id=2, eos_token_id=3, pad_token_id=1)
TARGETS = ("q_proj", "v_proj")
PROBE = (2, 4, 9, 17, 22, 5, 30, 11, 27, 3)
PEFT_PREFIX = "base_model.model."
# Tiny composite (vision + text) models, trained on text only: vocabulary 32 matches the tiny tokenizer. Qwen3.5 mixes
# linear-attention layers (0-2) with a full-attention layer (3). Gemma 4's per-layer input embedding
# (vocab_size_per_layer_input x layers x hidden_size_per_layer_input, 262144 x 3 x 256 by default) and its global
# attention head size (512 by default) are shrunk; its per-layer projections stay, and its last layer shares the key
# and value states of layer 0 (num_kv_shared_layers, as the E models do for 20 of 35 layers). Both have 20 or more
# language-model Linear modules, so an adapter over all of them is one PEFT 0.18.1 condenses to the shortest
# distinguishing suffixes, as it does for real models (Gemma 4's vision tower reuses q_proj..down_proj).
QWEN3_5 = dict(text_config=dict(num_hidden_layers=4, hidden_size=32, intermediate_size=64, num_attention_heads=2,
                                num_key_value_heads=1, head_dim=16, vocab_size=32, linear_num_key_heads=2,
                                linear_num_value_heads=2, linear_key_head_dim=8, linear_value_head_dim=8),
               vision_config=dict(depth=1, hidden_size=16, intermediate_size=32, num_heads=2, out_hidden_size=32))
GEMMA4 = dict(text_config=dict(num_hidden_layers=3, hidden_size=32, intermediate_size=64, num_attention_heads=2,
                               num_key_value_heads=1, head_dim=16, vocab_size=32, vocab_size_per_layer_input=32,
                               hidden_size_per_layer_input=8, global_head_dim=16, num_kv_shared_layers=1,
                               layer_types=["full_attention", "sliding_attention", "full_attention"]),
              vision_config=dict(num_hidden_layers=1, hidden_size=16, intermediate_size=32, num_attention_heads=2,
                                 num_key_value_heads=2, head_dim=8), audio_config=None)
# kind: (config class, settings, saved architecture, language-model Linear modules, frozen non-language prefixes)
COMPOSITES = {
    "qwen3_5": ("Qwen3_5Config", QWEN3_5, "Qwen3_5ForConditionalGeneration", 31, ("model.visual.",)),
    "gemma4": ("Gemma4Config", GEMMA4, "Gemma4ForConditionalGeneration", 26,
               ("model.vision_tower.", "model.embed_vision.")),
}
# Checkpoint tensors real checkpoints hold but transformers never builds, loads or saves; each tiny base gets them:
# Qwen3.5's multi-token-prediction head, the key/value projections and norm of Gemma 4's KV-shared layer.
UNLOADED = {"qwen3_5": {"mtp.fc.weight": (32, 64)},
            "gemma4": {"model.language_model.layers.2.self_attn.k_proj.weight": (16, 32),
                       "model.language_model.layers.2.self_attn.v_proj.weight": (16, 32),
                       "model.language_model.layers.2.self_attn.k_norm.weight": (16,)}}
PROCESSOR_FILE = "preprocessor_config.json"


def check_versions(installed) -> dict:
    """Refuse any drift from the hash-locked pins (torch must be the +cpu build)."""
    drift = sorted(name for name, expected in EXPECTED_VERSIONS.items() if installed.get(name) != expected)
    if drift:
        raise RuntimeError("model-operation CPU runtime differs from the lock: " + ", ".join(
            f"{name} {installed.get(name)} != {EXPECTED_VERSIONS[name]}" for name in drift))
    return {name: installed[name] for name in EXPECTED_VERSIONS}


def sha256_file(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


class PrerequisiteFailed(Exception):
    """A recorded operation failed; later phases that need its outputs are not run."""


def determinism_probe(work: Path) -> dict:
    """Build the fixtures and merge in this process; the caller compares runs under different hash seeds."""
    from tuning_factory.model_artifacts import _inventory, _inventory_bytes
    acceptance = Acceptance(work)
    acceptance.build_base()
    first = acceptance.make_adapter("adapter-r2", 2, 4, 1, base_dir=acceptance.base_dir, effective_base=acceptance.root)
    second = acceptance.make_adapter("adapter-r3", 3, 9, 2, base_dir=acceptance.base_dir,
                                     effective_base=acceptance.root)
    merged = work / "merged"
    acceptance.ops.merge_adapters((first, second), (0.25, 0.75), merged, base=acceptance.base, require_real=False,
                                  probe_ids=PROBE)
    return {"adapters": [model.identity.model_dump(mode="json") for model in (first, second)],
            "merged_adapter_config_sha256": sha256_file(merged / "adapter_config.json"),
            "merged_inventory_sha256": hashlib.sha256(_inventory_bytes(_inventory(merged))).hexdigest(),
            "raw_target_orders": acceptance.raw_target_orders}


def deterministic_output():
    """Merge in two fresh processes whose PYTHONHASHSEED differs; artifact bytes must not change."""
    runs = {}
    for seed in ("1", "3"):
        with tempfile.TemporaryDirectory(prefix="model-ops-seed-") as work:
            # -I would ignore PYTHONHASHSEED; -s -P plus a PYTHON*-free environment keep the same isolation.
            environment = {key: value for key, value in os.environ.items() if not key.upper().startswith("PYTHON")}
            environment.update(PYTHONHASHSEED=seed, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1")
            completed = subprocess.run([sys.executable, "-s", "-P", str(Path(__file__).resolve()),
                                        "--determinism-probe", work], env=environment, capture_output=True,
                                       text=True, timeout=1800)
            if completed.returncode:
                raise RuntimeError(f"hash seed {seed} probe failed: {completed.stderr[-2000:]}")
            runs[seed] = json.loads(completed.stdout.strip().splitlines()[-1])
    outputs = [{key: value for key, value in run.items() if key != "raw_target_orders"} for run in runs.values()]
    orders = {seed: run["raw_target_orders"] for seed, run in runs.items()}
    return {"passed": outputs[0] == outputs[1] and orders["1"] != orders["3"], "hash_seeds": sorted(runs),
            "raw_peft_target_orders": orders, "outputs": {seed: output for seed, output in zip(runs, outputs)}}


class Acceptance:
    """Builds the synthetic fixtures and runs each check; heavy modules are passed in from main()."""

    def __init__(self, work: Path):
        import torch
        import transformers
        from peft import LoraConfig, PeftModel, get_peft_model
        from safetensors.torch import load_file, save_file

        from tuning_factory import model_artifacts, model_ops
        self.torch, self.transformers = torch, transformers
        self.LoraConfig, self.PeftModel, self.get_peft_model = LoraConfig, PeftModel, get_peft_model
        self.load_file, self.save_file = load_file, save_file
        self.artifacts, self.ops = model_artifacts, model_ops
        self.policy = model_ops.TOLERANCE_POLICIES[model_ops.POLICY_F32_V1]
        self.work = work
        self.store = model_artifacts.ModelArtifactStore(work / "store")
        self.checks: dict[str, dict] = {}
        self.receipts: dict[str, dict] = {}
        self.raw_target_orders: list[list[str]] = []

    # -- helpers ---------------------------------------------------------------------------------

    def record(self, name, function):
        try:
            result = function()
        except Exception as exc:  # recorded as a failed check; the run continues to collect evidence
            result = {"passed": False, "max_abs_error": None, "error": f"{type(exc).__name__}: {exc}"}
        result.setdefault("max_abs_error", None)
        result["passed"] = bool(result["passed"])
        self.checks[name] = result
        return result

    def operation(self, name, function):
        """Run a step later checks depend on; its failure is recorded and stops the dependent phases."""
        result = self.record(name, lambda: {"passed": True, **(function() or {})})
        if not result["passed"]:
            raise PrerequisiteFailed(name)

    def load(self, path):
        from tuning_factory.target_inspection import COMPOSITE_MODEL_TYPES
        config = json.loads((Path(path) / "config.json").read_text(encoding="utf-8"))
        transformers = self.transformers
        loader = (transformers.AutoModelForImageTextToText if config.get("model_type") in COMPOSITE_MODEL_TYPES
                  else transformers.AutoModelForCausalLM)
        model = loader.from_pretrained(str(path), dtype=self.torch.float32, local_files_only=True,
                                       use_safetensors=True)
        return model.eval()

    def logits(self, model, ids=PROBE):
        torch = self.torch
        with torch.no_grad():
            tokens = torch.tensor([list(ids)], dtype=torch.long)
            return model(input_ids=tokens, attention_mask=torch.ones_like(tokens)).logits.to(torch.float64)

    def tensors(self, directory):
        result = {}
        for file in sorted(Path(directory).glob("*.safetensors")):
            result.update(self.load_file(str(file)))
        return result

    def headers(self, directory):
        tensors = []
        for file in sorted(Path(directory).glob("*.safetensors")):
            tensors.extend(self.artifacts._tensor_header(file, file.name))
        return tuple(tensors)

    def close(self, actual, expected, kind):
        atol, rtol = ((self.policy.delta_atol, self.policy.delta_rtol) if kind == "delta"
                      else (self.policy.logits_atol, self.policy.logits_rtol))
        error = float((actual - expected).abs().max())
        return bool(self.torch.allclose(actual, expected, atol=atol, rtol=rtol)), error

    def lora_delta(self, directory, scale):
        """Per-module LoRA delta scale·B@A in float64, keyed by the base module name."""
        torch, tensors = self.torch, self.tensors(directory)
        deltas = {}
        for name in tensors:
            if name.endswith(".lora_A.weight"):
                module = name[len(PEFT_PREFIX):-len(".lora_A.weight")]
                a = tensors[name].to(torch.float64)
                b = tensors[name.replace(".lora_A.", ".lora_B.")].to(torch.float64)
                deltas[module] = scale * (b @ a)
        return deltas

    def copy_snapshot(self, source, target, *, config):
        for file in sorted(Path(source).iterdir()):
            if self.artifacts.is_tokenizer_file(file.name) or config and file.name == "config.json":
                shutil.copyfile(file, Path(target) / file.name)

    def local(self, ref):
        descriptor = self.store.verify(ref)
        return self.ops.LocalModel(path=self.store.path(ref), identity=ref, descriptor=descriptor,
                                   config_sha256=descriptor.config_sha256,
                                   tokenizer_fingerprint=descriptor.tokenizer_fingerprint)

    def descriptor(self, directory, *, marker, storage_kind, effective_base, parents=(), adapter=None, root=None):
        root = root or self.root
        return self.artifacts.ModelDescriptor(
            producer_job_id=marker, producer_attempt_id=marker, producer_manifest_sha256="0" * 64,
            storage_kind=storage_kind, semantic_kind="causal_lm", has_value_head=False, root=root,
            effective_base=effective_base, parents=parents,
            config_sha256=sha256_file(Path(directory) / "config.json"),
            tokenizer_fingerprint=root.tokenizer_fingerprint, adapter=adapter,
            tensors=self.headers(directory), synthetic=True)

    # -- fixtures --------------------------------------------------------------------------------

    def check_fixtures(self):
        self.operation("fixture_generation", self.build_base)

        def probe():
            tokenizer = self.transformers.AutoTokenizer.from_pretrained(str(self.base_dir), local_files_only=True)
            ids = tokenizer.encode(self.ops.PROBE_TEXT, add_special_tokens=False)
            return {"passed": ids == [27, 28, 29, 30, 31] and tokenizer.unk_token_id not in ids, "probe_ids": ids}

        self.record("tokenizer_probe_has_no_unknown_tokens", probe)

    def build_base(self):
        from tokenizers import Tokenizer, models, pre_tokenizers
        torch, transformers = self.torch, self.transformers
        self.base_dir = self.work / "base"
        vocab = {**SPECIAL_TOKENS, **{f"t{index}": 4 + index for index in range(23)},
                 **{word: 27 + index for index, word in enumerate(PROBE_WORDS)}}
        assert len(vocab) == QWEN2["vocab_size"]
        tokenizer = Tokenizer(models.WordLevel(vocab=vocab, unk_token="<unk>"))
        tokenizer.pre_tokenizer = pre_tokenizers.WhitespaceSplit()
        transformers.PreTrainedTokenizerFast(tokenizer_object=tokenizer, unk_token="<unk>", pad_token="<pad>",
                                             bos_token="<s>", eos_token="</s>").save_pretrained(str(self.base_dir))
        torch.manual_seed(0)
        model = transformers.AutoModelForCausalLM.from_config(transformers.Qwen2Config(**QWEN2),
                                                              dtype=torch.float32)
        model.save_pretrained(str(self.base_dir), safe_serialization=True)
        assert len(self.headers(self.base_dir)) == 27, "tiny Qwen2 must have 27 tensors"
        self.root = self.artifacts.HFRootRef(
            repo_id="factory-synthetic/tiny-qwen2",
            revision=hashlib.sha1(b"factory-synthetic tiny qwen2 seed 0").hexdigest(),
            config_sha256=sha256_file(self.base_dir / "config.json"),
            tokenizer_fingerprint=self.artifacts.tokenizer_fingerprint(self.base_dir))
        self.base = self.ops.LocalModel(path=self.base_dir, identity=self.root, descriptor=None,
                                        config_sha256=self.root.config_sha256,
                                        tokenizer_fingerprint=self.root.tokenizer_fingerprint)
        self.base_ref = self.store.freeze(self.base_dir, self.descriptor(
            self.base_dir, marker="synthetic-base", storage_kind="full_model", effective_base=self.root))

    def make_adapter(self, marker, rank, alpha, seed, *, base_dir, effective_base, parents=(), target_modules=TARGETS,
                     root=None):
        torch = self.torch
        export = self.work / marker
        model = self.get_peft_model(self.load(base_dir), self.LoraConfig(
            r=rank, lora_alpha=alpha, target_modules=list(target_modules), lora_dropout=0.0, task_type="CAUSAL_LM"))
        generator = torch.Generator().manual_seed(seed)
        with torch.no_grad():
            for name, parameter in model.named_parameters():
                if "lora_" in name:
                    parameter.copy_(torch.randn(parameter.shape, generator=generator) * 0.1)
        model.save_pretrained(str(export))
        (export / "README.md").unlink(missing_ok=True)
        config = json.loads((export / "adapter_config.json").read_text(encoding="utf-8"))
        self.raw_target_orders.append(list(config["target_modules"]))   # PEFT writes them from a set
        self.ops.write_adapter_config(export / "adapter_config.json", config, (root or self.root).repo_id)
        self.copy_snapshot(base_dir, export, config=True)
        adapter = self.ops.adapter_metadata_from_config(config, method="lora")
        ref = self.store.freeze(export, self.descriptor(export, marker=marker, storage_kind="adapter",
                                                        effective_base=effective_base, parents=parents,
                                                        adapter=adapter, root=root))
        return self.local(ref)

    # -- checks ----------------------------------------------------------------------------------

    def check_merge(self):
        ops, torch = self.ops, self.torch
        merged_dir = self.work / "merged"

        def merge():
            self.a1 = self.make_adapter("adapter-r2", 2, 4, 1, base_dir=self.base_dir, effective_base=self.root)
            self.a2 = self.make_adapter("adapter-r3", 3, 9, 2, base_dir=self.base_dir, effective_base=self.root)
            self.merge_receipt = ops.merge_adapters((self.a1, self.a2), (0.25, 0.75), merged_dir, base=self.base,
                                                    require_real=False, probe_ids=PROBE)
            self.receipts["merge"] = self.merge_receipt.model_dump(mode="json")

        self.operation("merge_operation", merge)
        receipt = self.merge_receipt
        self.merged_dir = merged_dir
        config = json.loads((merged_dir / "adapter_config.json").read_text(encoding="utf-8"))

        def shape():
            files = sorted(path.name for path in merged_dir.iterdir())
            return {"passed": config["r"] == config["lora_alpha"] == receipt.output_rank == 5
                    and config["base_model_name_or_path"] == self.root.repo_id and "README.md" not in files
                    and all(tensor.dtype == "F32" for tensor in self.headers(merged_dir)),
                    "output_rank": config["r"], "lora_alpha": config["lora_alpha"], "files": files}

        def delta():
            expected = {}
            for model, weight, scale in ((self.a1, 0.25, 4 / 2), (self.a2, 0.75, 9 / 3)):
                for module, value in self.lora_delta(model.path, weight * scale).items():
                    expected[module] = expected.get(module, 0) + value
            self.merge_expected = expected
            actual = self.lora_delta(merged_dir, config["lora_alpha"] / config["r"])
            results = [self.close(actual[module], expected[module], "delta") for module in expected]
            return {"passed": set(actual) == set(expected) and len(expected) == 4 and all(ok for ok, _ in results),
                    "max_abs_error": max(error for _, error in results), "modules": len(expected),
                    "receipt_max_abs_error": receipt.max_abs_delta_error}

        def logits():
            independent = self.load(self.base_dir)
            with torch.no_grad():
                for module, value in self.merge_expected.items():
                    weight = independent.get_submodule(module).weight
                    weight.copy_((weight.to(torch.float64) + value).to(torch.float32))
            reloaded = self.PeftModel.from_pretrained(self.load(self.base_dir), str(merged_dir)).eval()
            ok, error = self.close(self.logits(reloaded), self.logits(independent), "logits")
            return {"passed": ok, "max_abs_error": error, "receipt_max_abs_error": receipt.max_abs_logits_error}

        def freezes():
            adapter = ops.adapter_metadata_from_config(config, method="lora")
            ref = self.store.freeze(merged_dir, self.descriptor(
                merged_dir, marker="merged", storage_kind="adapter", effective_base=self.root,
                parents=(self.a1.identity, self.a2.identity), adapter=adapter))
            return {"passed": self.store.verify(ref).adapter.rank == 5}

        self.record("merge_rank_alpha_and_layout", shape)
        self.record("merge_delta", delta)
        self.record("merge_reloaded_logits", logits)
        self.record("merge_output_freezes", freezes)

    def check_materialize(self):
        ops, torch = self.ops, self.torch
        out = self.work / "materialized"

        def materialize():
            self.materialize_receipt = ops.materialize_model(ops.StagedLoad(base=self.base, adapter=self.a1), out,
                                                             probe_ids=PROBE)
            self.receipts["materialize"] = self.materialize_receipt.model_dump(mode="json")

        self.operation("materialize_operation", materialize)
        receipt = self.materialize_receipt
        self.materialized = out
        base, result = self.tensors(self.base_dir), self.tensors(out)
        targets = {module + ".weight": value for module, value in self.lora_delta(self.a1.path, 4 / 2).items()}

        def delta():
            results = [self.close(result[name].to(torch.float64) - base[name].to(torch.float64), value, "delta")
                       for name, value in targets.items()]
            return {"passed": len(targets) == 4 and all(ok for ok, _ in results),
                    "max_abs_error": max(error for _, error in results),
                    "receipt_max_abs_error": receipt.max_abs_delta_error}

        def non_target():
            others = [name for name in base if name not in targets]
            return {"passed": set(base) == set(result) and len(others) == 23
                    and all(torch.equal(result[name], base[name]) for name in others), "tensors": len(others)}

        def tokenizer():
            files = [path.name for path in self.base_dir.iterdir() if self.artifacts.is_tokenizer_file(path.name)]
            return {"passed": sorted(files) == ["tokenizer.json", "tokenizer_config.json"] and all(
                (out / name).read_bytes() == (self.base_dir / name).read_bytes() for name in files)
                and self.artifacts.tokenizer_fingerprint(out) == self.root.tokenizer_fingerprint, "files": files}

        def logits():
            before = self.PeftModel.from_pretrained(self.load(self.base_dir), str(self.a1.path)).eval()
            ok, error = self.close(self.logits(self.load(out)), self.logits(before), "logits")
            return {"passed": ok, "max_abs_error": error, "receipt_max_abs_error": receipt.max_abs_logits_error}

        def finite():
            return {"passed": all(tensor.dtype == torch.float32 and bool(torch.isfinite(tensor).all())
                                  for tensor in result.values()) and "README.md" not in os.listdir(out),
                    "tensors": len(result)}

        self.record("materialize_delta", delta)
        self.record("materialize_non_target_identical", non_target)
        self.record("materialize_tokenizer_bytes", tokenizer)
        self.record("materialize_reloaded_logits", logits)
        self.record("materialize_finite_f32", finite)

    def check_parent_child(self):
        ops, torch = self.ops, self.torch
        out = self.work / "materialized-f2"

        def materialize_child():
            f1_ref = self.store.freeze(self.materialized, self.descriptor(
                self.materialized, marker="materialized-f1", storage_kind="full_model", effective_base=self.root,
                parents=(self.a1.identity,)))
            self.f1 = self.local(f1_ref)
            self.child = self.make_adapter("child-on-f1", 2, 4, 7, base_dir=self.f1.path, effective_base=f1_ref,
                                           parents=(f1_ref,))
            receipt = ops.materialize_model(ops.StagedLoad(base=self.f1, adapter=self.child), out)  # tokenizer probe
            self.receipts["materialize_child"] = receipt.model_dump(mode="json")

        self.operation("materialize_child_operation", materialize_child)
        root, f1, f2 = self.tensors(self.base_dir), self.tensors(self.f1.path), self.tensors(out)
        child = {module + ".weight": value for module, value in self.lora_delta(self.child.path, 4 / 2).items()}

        def preserves():
            results = [self.close(f2[name].to(torch.float64) - f1[name].to(torch.float64), value, "delta")
                       for name, value in child.items()]
            identical = all(torch.equal(f2[name], f1[name]) for name in f1 if name not in child)
            return {"passed": set(f1) == set(f2) and identical and all(ok for ok, _ in results),
                    "max_abs_error": max(error for _, error in results)}

        def differs_from_root():
            difference = max(float((f2[name].to(torch.float64) - (root[name].to(torch.float64) + value)).abs().max())
                             for name, value in child.items())
            return {"passed": difference > 1e-3, "observed_difference": difference, "required_minimum": 1e-3}

        def refuses_root():
            target = self.work / "refused-child-on-root"
            try:
                ops.materialize_model(ops.StagedLoad(base=self.base, adapter=self.child), target)
            except ValueError as exc:
                return {"passed": "materialize base differs from adapter effective base" in str(exc)
                        and not target.exists(), "refusal": str(exc)}
            return {"passed": False, "refusal": None}

        self.record("parent_child_preserves_parent", preserves)
        self.record("parent_child_differs_from_root", differs_from_root)
        self.record("child_materialize_refuses_root_base", refuses_root)

    def check_frozen_reference(self):
        torch = self.torch

        def run():
            reference = self.load(self.f1.path)          # LF reference load: is_trainable=False, eval
            reference.requires_grad_(False)
            digests = {name: hashlib.sha256(tensor.detach().contiguous().numpy().tobytes()).hexdigest()
                       for name, tensor in reference.state_dict().items()}
            policy = self.get_peft_model(self.load(self.f1.path), self.LoraConfig(
                r=2, lora_alpha=4, target_modules=list(TARGETS), lora_dropout=0.0, task_type="CAUSAL_LM"))
            policy.train()
            trainable = {name: parameter.detach().clone() for name, parameter in policy.named_parameters()
                         if parameter.requires_grad}
            chosen = torch.tensor([[2, 4, 5, 6, 7, 3]])
            rejected = torch.tensor([[2, 4, 5, 9, 10, 3]])

            def log_probability(model, ids):
                logits = model(input_ids=ids).logits[:, :-1].float()
                return torch.log_softmax(logits, -1).gather(-1, ids[:, 1:, None]).squeeze(-1).sum(-1)

            with torch.no_grad():
                reference_chosen, reference_rejected = log_probability(reference, chosen), log_probability(
                    reference, rejected)
            margin = ((log_probability(policy, chosen) - reference_chosen)
                      - (log_probability(policy, rejected) - reference_rejected))
            loss = -torch.nn.functional.logsigmoid(0.1 * margin).mean()
            optimizer = torch.optim.AdamW([parameter for parameter in policy.parameters() if parameter.requires_grad],
                                          lr=1e-2)
            loss.backward()
            optimizer.step()
            after = {name: hashlib.sha256(tensor.detach().contiguous().numpy().tobytes()).hexdigest()
                     for name, tensor in reference.state_dict().items()}
            update = max(float((parameter.detach() - trainable[name]).abs().max())
                         for name, parameter in policy.named_parameters() if name in trainable)
            return {"passed": digests == after and not reference.training
                    and not any(parameter.requires_grad or parameter.grad is not None
                                for parameter in reference.parameters())
                    and bool(trainable) and update > 0,
                    "reference_tensors": len(digests), "policy_max_update": update,
                    "initial_margin": float(margin.detach())}

        self.record("frozen_reference_unchanged_policy_updated", run)

    def check_nonfinite(self):
        ops, torch = self.ops, self.torch
        self.nan_dir = self.work / "nan-adapter"
        self.inf_dir = self.work / "inf-base"

        def fixtures():
            shutil.copytree(self.a2.path, self.nan_dir)
            file = self.nan_dir / "adapter_model.safetensors"
            tensors = self.load_file(str(file))
            name = sorted(key for key in tensors if ".lora_B." in key)[0]
            tensors[name][0, 0] = float("nan")
            self.save_file(tensors, str(file), metadata={"format": "pt"})
            shutil.copytree(self.base_dir, self.inf_dir)
            file = self.inf_dir / "model.safetensors"
            tensors = self.load_file(str(file))
            tensors["model.norm.weight"][0] = float("inf")
            self.save_file(tensors, str(file), metadata={"format": "pt"})

        self.operation("nonfinite_fixture_generation", fixtures)

        def nan_merge():
            tampered = ops.LocalModel(path=self.nan_dir, identity=self.a2.identity, descriptor=self.a2.descriptor,
                                      config_sha256=self.a2.config_sha256,
                                      tokenizer_fingerprint=self.a2.tokenizer_fingerprint)
            target = self.work / "refused-nan-merge"
            try:
                ops.merge_adapters((self.a1, tampered), (0.25, 0.75), target, base=self.base, require_real=False)
            except ValueError as exc:
                return {"passed": "non-finite tensor values" in str(exc) and not target.exists(), "refusal": str(exc)}
            return {"passed": False, "refusal": None}

        def inf_materialize():
            tampered = ops.LocalModel(path=self.inf_dir, identity=self.root, descriptor=None,
                                      config_sha256=self.root.config_sha256,
                                      tokenizer_fingerprint=self.root.tokenizer_fingerprint)
            target = self.work / "refused-inf-materialize"
            try:
                ops.materialize_model(ops.StagedLoad(base=tampered, adapter=self.a1), target)
            except ValueError as exc:
                return {"passed": "non-finite tensor values" in str(exc) and not target.exists(), "refusal": str(exc)}
            return {"passed": False, "refusal": None}

        def agreement():
            directories = [self.base_dir, self.a1.path, self.a2.path, self.merged_dir, self.materialized,
                           self.f1.path, self.child.path, self.work / "materialized-f2", self.nan_dir, self.inf_dir]
            outcomes = []
            for directory in directories:
                for file in sorted(Path(directory).glob("*.safetensors")):
                    try:
                        stdlib = (True, ops.scan_finite_safetensors(file))
                    except ValueError as exc:
                        if "non-finite tensor values" not in str(exc):
                            raise
                        stdlib = (False, None)
                    tensors = self.load_file(str(file))
                    finite = all(bool(torch.isfinite(tensor).all()) for tensor in tensors.values()
                                 if tensor.is_floating_point())
                    elements = sum(tensor.numel() for tensor in tensors.values() if tensor.is_floating_point())
                    outcomes.append((stdlib, (finite, elements if finite else None)))
            nonfinite = sum(1 for stdlib, _ in outcomes if not stdlib[0])
            return {"passed": all(stdlib == pair for stdlib, pair in outcomes) and nonfinite == 2,
                    "files": len(outcomes), "nonfinite_files": nonfinite}

        self.record("nan_adapter_refused_before_output", nan_merge)
        self.record("inf_base_refused_before_output", inf_materialize)
        self.record("stdlib_and_torch_finite_scans_agree", agreement)

    def check_targets(self):
        ops = self.ops

        def inspection():
            from tuning_factory.graph_planner import TargetModuleEvidence
            descriptor = self.store.verify(self.base_ref)
            evidence = ops.inspect_target_modules(self.store.path(self.base_ref), self.base_ref)
            expected = {}
            for layer in range(2):
                prefix = f"model.layers.{layer}."
                for name in ("q_proj", "k_proj", "v_proj", "o_proj"):
                    expected[prefix + "self_attn." + name] = (16, 16)
                expected[prefix + "mlp.gate_proj"] = (16, 32)
                expected[prefix + "mlp.up_proj"] = (16, 32)
                expected[prefix + "mlp.down_proj"] = (32, 16)
            actual = {item.name: (item.input_dimension, item.output_dimension) for item in evidence.modules}
            accepted = ops.verify_inspected_targets(evidence, descriptor, self.base_ref) == evidence
            refusals = []
            tampered = (
                evidence.model_copy(update={"modules": (evidence.modules[0].model_copy(
                    update={"input_dimension": 17}), *evidence.modules[1:])}),
                evidence.model_copy(update={"modules": evidence.modules[1:]}),
                evidence.model_copy(update={"modules": (*evidence.modules, TargetModuleEvidence(
                    name="model.layers.0.self_attn.rotary", input_dimension=16, output_dimension=16))}),
                evidence.model_copy(update={"base": self.root}),
            )
            for item in tampered:
                try:
                    ops.verify_inspected_targets(item, descriptor, self.base_ref)
                    refusals.append(False)
                except ValueError:
                    refusals.append(True)
            hf = ops.inspect_target_modules(self.base_dir, self.root)
            f1_descriptor = self.store.verify(self.f1.identity)
            f1 = ops.verify_inspected_targets(ops.inspect_target_modules(self.f1.path, self.f1.identity),
                                              f1_descriptor, self.f1.identity)
            return {"passed": actual == expected and len(evidence.modules) == 14 and accepted and all(refusals)
                    and hf.modules == evidence.modules and f1.modules == evidence.modules,
                    "modules": len(evidence.modules), "tampered_refused": sum(refusals)}

        self.record("target_inspection_exact_and_verified", inspection)

    def check_execute_operation(self):
        """Task 7.3: the worker dispatch (stage -> verify -> model_ops -> publish) end to end, CPU only."""
        from datetime import datetime, timezone

        from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts
        from tuning_factory.operation_dispatch import execute_operation
        from tuning_factory.operation_runner import operation_sha256, stage_model_inputs
        from tuning_factory.operations import (
            MaterializeOperation,
            MergeOperation,
            OperationLineage,
            OperationResources,
            RuntimeProfile,
        )
        from tuning_factory.worker_protocol import WorkerRequest
        inventory = self.artifacts.directory_inventory_sha256
        lock = REPO / "containers" / "gpu-requirements.lock"
        resources = OperationResources(
            executor=ExecutorProfile(profile_id="cpu-acceptance", revision="1", name="CPU acceptance",
                                     pod_id="cpu-acceptance", credential_scope="local"),
            facts=ResourceFacts(pod_id="cpu-acceptance", gpu_type="CPU", gpu_count=1, total_hourly_cost_usd=0,
                                observed_at=datetime(2026, 10, 20, tzinfo=timezone.utc)),
            timeout_seconds=1800,
            runtime=RuntimeProfile(profile="primary", engine_version="0.9.5", dependency_sha256=sha256_file(lock)))
        # The fixtures are synthetic, so the operations are simulation lineage (require_real=False).
        lineage = OperationLineage(project_id="cpu-acceptance", project_revision="0" * 64, node_id="operation",
                                   plan_sha256="0" * 64, simulation=True)
        launches = self.work / "launches" / ("a" * 32)
        dispatched = {}

        def run(operation, launch_id):
            directory = launches / launch_id
            stage_model_inputs(operation, self.store, directory)
            # No manifest exists in this acceptance; the request still fences launch and operation identity.
            request = WorkerRequest(attempt_id="d" * 32, launch_id=launch_id, manifest_sha256="0" * 64,
                                    operation_sha256=operation_sha256(operation), timeout_seconds=1800)
            result = execute_operation(operation, directory, 1800, lambda: False, request=request,
                                       model_cache=self.store.root)
            self.receipts[f"execute_{operation.kind}"] = json.loads(
                (directory / "operation-receipt.json").read_text(encoding="utf-8"))
            dispatched[operation.kind] = (operation, directory, result)
            return directory, result

        def adapters():
            # Same seeds and ranks as the direct fixtures, but on the frozen base artifact (no HF download).
            self.op_adapters = tuple(self.make_adapter(f"op-adapter-r{rank}", rank, alpha, seed,
                                                       base_dir=self.base_dir, effective_base=self.base_ref,
                                                       parents=(self.base_ref,))
                                     for rank, alpha, seed in ((2, 4, 1), (3, 9, 2)))

        self.operation("execute_operation_fixtures", adapters)

        def merge():
            operation = MergeOperation(models=tuple(model.identity for model in self.op_adapters),
                                       weights=(0.25, 0.75), resources=resources, lineage=lineage)
            directory, result = run(operation, "b" * 32)
            output = directory / "model-op" / "output"
            receipt = self.receipts["execute_merge"]
            return {"passed": result.status == "COMPLETED" and result.kind == "merge"
                    and receipt["receipt"]["output_rank"] == 5
                    and receipt["output_inventory_sha256"] == inventory(output) == inventory(self.merged_dir)
                    and set(result.evidence) == {"model-inputs.json", "model-binding.json", "operation-receipt.json"},
                    "output_inventory_sha256": inventory(output), "direct_inventory_sha256": inventory(self.merged_dir),
                    "max_abs_error": receipt["receipt"]["max_abs_delta_error"]}

        def materialize():
            operation = MaterializeOperation(model=self.op_adapters[0].identity, resources=resources,
                                             lineage=lineage)
            directory, result = run(operation, "c" * 32)
            output = directory / "model-op" / "output"
            receipt = self.receipts["execute_materialize"]
            targets = json.loads((directory / "target-inventory.json").read_text(encoding="utf-8"))["modules"]
            text = {name: (output / name).read_bytes() for name in ("config.json", "generation_config.json")
                    if (output / name).exists()}
            tokenizer = all((output / name).read_bytes() == (self.base_dir / name).read_bytes()
                            for name in ("tokenizer.json", "tokenizer_config.json"))
            return {"passed": result.status == "COMPLETED" and "config.json" in text
                    and not any(b"\r" in data for data in text.values()) and tokenizer and len(targets) == 14
                    and receipt["output_inventory_sha256"] == inventory(output) == inventory(self.materialized),
                    "output_inventory_sha256": inventory(output),
                    "direct_inventory_sha256": inventory(self.materialized), "lf_only_json": sorted(text),
                    "target_modules": len(targets), "max_abs_error": receipt["receipt"]["max_abs_delta_error"]}

        self.record("execute_operation_merge_end_to_end", merge)
        self.record("execute_operation_materialize_end_to_end", materialize)

        def freeze():
            """Task 7.5: the control plane freezes the real dispatched outputs with exact lineage."""
            from tuning_factory.operation_outputs import freeze_operation_output
            from tuning_factory.operation_runner import ResolvedModelInputs
            frozen = {}
            for kind in ("merge", "materialize"):
                operation, directory, result = dispatched[kind]
                resolved = ResolvedModelInputs.model_validate_json((directory / "model-inputs.json").read_bytes())
                ref, targets = freeze_operation_output(
                    operation, result, job_id="a" * 32, attempt_id="d" * 32, manifest_sha256="0" * 64,
                    launch_dir=directory, resolved=resolved, approved_cache=self.store)
                frozen[kind] = (ref, self.store.verify(ref), targets)
            (merge_ref, merged, _), (full_ref, full, targets) = frozen["merge"], frozen["materialize"]
            adapters = {model.identity for model in self.op_adapters}
            return {"passed": merge_ref.inventory_sha256 == self.receipts["execute_merge"]["output_inventory_sha256"]
                    and full_ref.inventory_sha256 == self.receipts["execute_materialize"]["output_inventory_sha256"]
                    and merged.adapter.rank == 5 and merged.effective_base == self.base_ref
                    and set(merged.parents) == adapters | {self.base_ref} and merged.producer_attempt_id == "d" * 32
                    and (full.storage_kind, full.quantization_bit, full.effective_base) == (
                        "full_model", None, self.base_ref)
                    and set(full.parents) == {self.op_adapters[0].identity, self.base_ref}
                    and len(targets) == 1 and targets[0].base == full_ref and len(targets[0].modules) == 14,
                    "merge_inventory_sha256": merge_ref.inventory_sha256,
                    "materialize_inventory_sha256": full_ref.inventory_sha256,
                    "target_modules": len(targets[0].modules) if targets else 0}

        self.record("freeze_dispatched_outputs", freeze)

    # -- composite (vision + text) models, trained on text only ---------------------------------------

    def check_composite(self):
        """Phase 2: Qwen3.5 and Gemma 4 load through their image-text class; adapters record full language-model
        names, as LLaMA-Factory 0.9.5 writes them. Three checks per architecture: targets, merge, materialize."""
        for kind in COMPOSITES:
            self.check_composite_kind(kind)

    def build_composite(self, kind):
        """A seeded tiny composite model with the tiny tokenizer, a processor file and the raw ``UNLOADED`` tensors.
        Returns (directory, HF root, local base, frozen full-model artifact)."""
        torch, transformers = self.torch, self.transformers
        config_class, settings = COMPOSITES[kind][:2]
        base = self.work / f"composite-{kind}"
        base.mkdir()
        self.copy_snapshot(self.base_dir, base, config=False)
        torch.manual_seed(0)
        model = transformers.AutoModelForImageTextToText.from_config(
            getattr(transformers, config_class)(**settings), dtype=torch.float32)
        model.save_pretrained(str(base), safe_serialization=True)
        del model
        file = base / "model.safetensors"
        tensors = self.load_file(str(file))
        generator = torch.Generator().manual_seed(5)
        for name, shape in UNLOADED[kind].items():
            assert name not in tensors, name
            tensors[name] = torch.randn(shape, generator=generator)
        self.save_file(tensors, str(file), metadata={"format": "pt"})
        (base / PROCESSOR_FILE).write_bytes(json.dumps({"processor_class": f"factory-synthetic-{kind}"}).encode())
        root = self.artifacts.HFRootRef(
            repo_id=f"factory-synthetic/tiny-{kind}",
            revision=hashlib.sha1(f"factory-synthetic tiny {kind} seed 0".encode()).hexdigest(),
            config_sha256=sha256_file(base / "config.json"),
            tokenizer_fingerprint=self.artifacts.tokenizer_fingerprint(base))
        local = self.ops.LocalModel(path=base, identity=root, descriptor=None, config_sha256=root.config_sha256,
                                    tokenizer_fingerprint=root.tokenizer_fingerprint)
        ref = self.store.freeze(base, self.descriptor(base, marker=f"composite-{kind}", storage_kind="full_model",
                                                      effective_base=root, root=root))
        return base, root, local, ref

    def with_deltas(self, directory, deltas):
        """The model with each LoRA delta added to its weight directly (float64 sum, stored as float32)."""
        torch, model = self.torch, self.load(directory)
        with torch.no_grad():
            for module, value in deltas.items():
                weight = model.get_submodule(module).weight
                weight.copy_((weight.to(torch.float64) + value).to(torch.float32))
        return model

    def composite_merge(self, base, root, local, adapters, wanted, out):
        """Merge two adapters (made for the ``wanted`` modules) and check the output independently."""
        ops = self.ops
        receipt = ops.merge_adapters(adapters, (0.25, 0.75), out, base=local, require_real=False, probe_ids=PROBE)
        config = json.loads((out / "adapter_config.json").read_text(encoding="utf-8"))
        expected = {}
        for model, weight, scale in zip(adapters, (0.25, 0.75), (4 / 2, 9 / 3)):
            for module, value in self.lora_delta(model.path, weight * scale).items():
                expected[module] = expected.get(module, 0) + value
        actual = self.lora_delta(out, config["lora_alpha"] / config["r"])
        delta = [self.close(actual[module], expected[module], "delta") for module in expected if module in actual]
        reloaded = self.PeftModel.from_pretrained(self.load(base), str(out)).eval()
        ok, error = self.close(self.logits(reloaded), self.logits(self.with_deltas(base, expected)), "logits")
        frozen = self.store.verify(self.store.freeze(out, self.descriptor(
            out, marker=out.name, storage_kind="adapter", effective_base=root,
            parents=tuple(model.identity for model in adapters),
            adapter=ops.adapter_metadata_from_config(config, method="lora"), root=root)))
        recorded = adapters[0].descriptor.adapter.target_modules
        conditions = {
            "deltas_touch_exactly_the_wanted_modules": set(actual) == set(expected) == set(wanted) and bool(wanted),
            # full names below 20 targets; PEFT condenses 20 or more to suffixes, which the merge keeps
            "recorded_targets_kept": sorted(config["target_modules"]) == sorted(recorded)
            and (list(recorded) == sorted(wanted)) == (len(wanted) < 20),
            "rank_sum": config["r"] == config["lora_alpha"] == receipt.output_rank == frozen.adapter.rank == 5,
            "delta_within_tolerance": all(good for good, _ in delta), "logits_match_base_plus_delta": ok,
            "processor_bytes_copied": (out / PROCESSOR_FILE).read_bytes() == (base / PROCESSOR_FILE).read_bytes(),
        }
        return all(conditions.values()), {
            "failed_conditions": sorted(name for name, value in conditions.items() if not value),
            "targets": len(wanted), "recorded_target_modules": len(recorded),
            "max_abs_delta_error": max(error for _, error in delta), "max_abs_logits_error": error,
            "receipt_max_abs_delta_error": receipt.max_abs_delta_error,
            "receipt_max_abs_logits_error": receipt.max_abs_logits_error}

    def composite_materialized(self, kind, base, root, local, adapter, wanted, out):
        """Materialize one adapter (made for the ``wanted`` modules) into the composite base and check the output
        independently."""
        ops, torch = self.ops, self.torch
        _, _, architecture, module_count, frozen_prefixes = COMPOSITES[kind]
        receipt = ops.materialize_model(ops.StagedLoad(base=local, adapter=adapter), out, probe_ids=PROBE)
        before, result = self.tensors(base), self.tensors(out)
        loaded = set(before) - set(UNLOADED[kind])
        metadata = adapter.descriptor.adapter
        deltas = self.lora_delta(adapter.path, metadata.alpha / metadata.rank)
        targets = {module + ".weight": value for module, value in deltas.items()}
        delta = [self.close(result[name].to(torch.float64) - before[name].to(torch.float64), value, "delta")
                 for name, value in targets.items()]
        frozen = sorted(name for name in before if name.startswith(frozen_prefixes))
        others = sorted(loaded - set(targets))
        merged = self.load(out)
        ok, error = self.close(self.logits(merged), self.logits(self.with_deltas(base, deltas)), "logits")
        unmerged = self.PeftModel.from_pretrained(self.load(base), str(adapter.path)).eval()
        peft_ok, peft_error = self.close(self.logits(merged), self.logits(unmerged), "logits")
        config = json.loads((out / "config.json").read_text(encoding="utf-8"))
        full_ref = self.store.freeze(out, self.descriptor(out, marker=out.name, storage_kind="full_model",
                                                          effective_base=root, parents=(adapter.identity,), root=root))
        full = self.store.verify(full_ref)
        evidence = ops.verify_inspected_targets(ops.inspect_target_modules(self.store.path(full_ref), full_ref),
                                                full, full_ref)    # a transformers save: no config needed
        copied = [name for name in (PROCESSOR_FILE, "tokenizer.json", "tokenizer_config.json")
                  if (out / name).read_bytes() == (base / name).read_bytes()]
        conditions = {
            "key_set_is_base_without_unloaded": set(result) == loaded,
            "every_target_changed_by_its_delta": set(deltas) == set(wanted) and all(good for good, _ in delta),
            "frozen_and_other_tensors_identical": bool(frozen)
            and all(torch.equal(result[name], before[name]) for name in others),
            "composite_architecture": config.get("architectures") == [architecture],
            "logits_match_base_plus_delta": ok, "logits_match_unmerged_adapter": peft_ok,
            "processor_and_tokenizer_bytes_copied": len(copied) == 3,
            "frozen_output_targets_verified": len(evidence.modules) == module_count
            and full.storage_kind == "full_model",
            "finite_f32": all(tensor.dtype == torch.float32 and bool(torch.isfinite(tensor).all())
                              for tensor in result.values()),
        }
        return all(conditions.values()), {
            "failed_conditions": sorted(name for name, value in conditions.items() if not value),
            "targets": len(targets), "recorded_target_modules": len(metadata.target_modules),
            "frozen_tensors_identical": len(frozen), "other_tensors_identical": len(others) - len(frozen),
            "output_tensors": len(result), "base_tensors": len(before), "architectures": config.get("architectures"),
            "max_abs_delta_error": max(error for _, error in delta), "max_abs_logits_error": error,
            "max_abs_logits_error_vs_unmerged": peft_error, "receipt_max_abs_delta_error": receipt.max_abs_delta_error,
            "receipt_max_abs_logits_error": receipt.max_abs_logits_error, "copied_files": copied}

    def check_composite_kind(self, kind):
        from tuning_factory import hf_targets
        from tuning_factory.target_inspection import (
            LANGUAGE_MODEL_PREFIX,
            resolved_lora_targets,
            target_leaves,
            unloaded_modules,
        )
        ops = self.ops
        module_count, frozen_prefixes = COMPOSITES[kind][3:]
        state = {}
        lora_targets = {"qv": "q_proj,v_proj", "all": "all"}

        def targets():
            base, root, _, ref = state["base"] = self.build_composite(kind)
            config = json.loads((base / "config.json").read_text(encoding="utf-8"))
            descriptor = self.store.verify(ref)
            inspected = ops.inspect_target_modules(base, root)                     # meta device, HF root
            frozen = ops.inspect_target_modules(self.store.path(ref), ref)          # the frozen raw checkpoint
            header = hf_targets.target_evidence(root, self.headers(base), config)   # safetensors headers only
            names = state["names"] = [item.name for item in inspected.modules]
            accepted = ops.verify_inspected_targets(frozen, descriptor, ref, config=config) == frozen

            def refused(evidence, **options):
                try:
                    ops.verify_inspected_targets(evidence, descriptor, ref, **options)
                except ValueError:
                    return True
                return False

            unloaded = unloaded_modules(config)
            # transformers' own declaration on the loaded model, mirrored without torch by unloaded_modules
            declared = list(self.load(base)._keys_to_ignore_on_load_unexpected or ())
            mirrored = {pattern for pattern in declared if pattern.startswith(LANGUAGE_MODEL_PREFIX)} == unloaded
            tensors = {tensor.name for tensor in descriptor.tensors}
            return {"passed": len(names) == module_count and all(name.startswith(LANGUAGE_MODEL_PREFIX)
                                                                 for name in names)
                    and accepted and header.modules == inspected.modules == frozen.modules
                    and refused(frozen.model_copy(update={"modules": frozen.modules[1:]}), config=config)
                    and refused(frozen) == bool(unloaded)          # a raw checkpoint needs its config
                    and kind in hf_targets.HEADER_TARGET_ARCHITECTURES and mirrored
                    and any(name.startswith(frozen_prefixes) for name in tensors) and set(UNLOADED[kind]) <= tensors,
                    "modules": len(names), "leaves": list(target_leaves(names)), "base_tensors": len(tensors),
                    "unloaded_patterns": declared, "raw_unloaded_tensors": sorted(UNLOADED[kind])}

        def merge():
            """Two q_proj,v_proj adapters (full names, as LLaMA-Factory passes them) and two over every
            language-model Linear module (lora_target: all, which PEFT records condensed)."""
            base, root, local, _ = state["base"]
            state["wanted"], state["adapters"], results = {}, {}, {}
            for suffix, lora_target in lora_targets.items():
                wanted = state["wanted"][suffix] = resolved_lora_targets(lora_target, state["names"])
                adapters = state["adapters"][suffix] = tuple(
                    self.make_adapter(f"composite-{kind}-{suffix}-r{rank}", rank, alpha, seed, base_dir=base,
                                      effective_base=root, target_modules=wanted, root=root)
                    for rank, alpha, seed in ((2, 4, 1), (3, 9, 2)))
                results[lora_target] = self.composite_merge(base, root, local, adapters, wanted,
                                                            self.work / f"composite-{kind}-merged-{suffix}")
            details = {label: item for label, (_, item) in results.items()}
            return {"passed": all(passed for passed, _ in results.values())
                    and details["all"]["targets"] == module_count,
                    "max_abs_error": max(item["max_abs_delta_error"] for item in details.values()),
                    "max_abs_logits_error": max(item["max_abs_logits_error"] for item in details.values()),
                    "merges": details}

        def materialize():
            """The first q_proj,v_proj adapter and the first adapter over every language-model Linear module
            (Qwen3.5's linear attention, Gemma 4's per-layer projections)."""
            base, root, local, _ = state["base"]
            results = {lora_target: self.composite_materialized(
                kind, base, root, local, state["adapters"][suffix][0], state["wanted"][suffix],
                self.work / f"composite-{kind}-materialized-{suffix}") for suffix, lora_target in lora_targets.items()}
            details = {label: item for label, (_, item) in results.items()}
            return {"passed": all(passed for passed, _ in results.values())
                    and details["all"]["targets"] == module_count,
                    "max_abs_error": max(item["max_abs_delta_error"] for item in details.values()),
                    "max_abs_logits_error": max(item["max_abs_logits_error"] for item in details.values()),
                    "adapters": details}

        self.record(f"composite_{kind}_targets", targets)
        self.record(f"composite_{kind}_merge", merge)
        self.record(f"composite_{kind}_materialize", materialize)

    def run(self):
        from unittest import mock

        import peft.utils.save_and_load as peft_save
        lookups = []

        def hub_lookup(repo_id, filename, **_):   # PEFT's vocabulary check would query the HF Hub
            lookups.append(f"{repo_id}/{filename}")
            return None

        self.stopped_after = None
        with mock.patch.object(peft_save, "check_file_exists_on_hf_hub", hub_lookup):
            try:
                for phase in (self.check_fixtures, self.check_merge, self.check_materialize, self.check_parent_child,
                              self.check_frozen_reference, self.check_nonfinite, self.check_targets,
                              self.check_execute_operation, self.check_composite):
                    phase()
            except PrerequisiteFailed as exc:
                self.stopped_after = str(exc)   # dependent checks did not run; the receipt says so
        self.record("no_hub_lookups", lambda: {"passed": not lookups, "lookups": lookups})
        self.record("deterministic_output", deterministic_output)
        return self.checks


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--receipt", type=Path, help="write the canonical JSON acceptance receipt here")
    parser.add_argument("--determinism-probe", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args(argv)
    os.environ["HF_HUB_OFFLINE"] = "1"
    os.environ["TRANSFORMERS_OFFLINE"] = "1"
    for name in REQUIRED_MODULES:
        try:
            importlib.import_module(name)
        except ImportError as exc:
            print(f"model-operation CPU runtime is missing: {name} ({exc})", file=sys.stderr)
            return 2
    try:
        versions = check_versions({name: metadata.version(name) for name in EXPECTED_VERSIONS})
    except metadata.PackageNotFoundError as exc:
        print(f"model-operation CPU runtime is missing: {exc}", file=sys.stderr)
        return 2
    import torch

    from tuning_factory.manifest import source_hash
    from tuning_factory.registry import canonical
    from tuning_factory.runtime_profiles import MODEL_OPS_PINS
    if torch.version.cuda is not None or torch.cuda.is_available():
        raise RuntimeError("CPU acceptance requires the CPU-only torch build")
    if {name: versions[name].split("+")[0] for name in MODEL_OPS_PINS} != dict(MODEL_OPS_PINS):
        raise RuntimeError("acceptance pins differ from runtime_profiles.MODEL_OPS_PINS")
    if args.determinism_probe:
        print(json.dumps(determinism_probe(args.determinism_probe.absolute()), sort_keys=True))
        return 0
    with tempfile.TemporaryDirectory(prefix="model-ops-cpu-") as work:
        acceptance = Acceptance(Path(work))
        checks = acceptance.run()
    passed = bool(checks) and acceptance.stopped_after is None and all(check["passed"] for check in checks.values())
    receipt = {
        "schema_version": SCHEMA_VERSION, "passed": passed, "stopped_after": acceptance.stopped_after,
        "versions": versions, "lock_sha256": sha256_file(LOCK),
        "source_sha256": source_hash(), "tolerance_policy": acceptance.policy.model_dump(mode="json"),
        "checks": checks, "operation_receipts": acceptance.receipts, "model_downloaded": False,
        "gpu_used": False, "institution_data_used": False, "synthetic_fixture": True,
        "production_promotable": False,
    }
    body = canonical(receipt)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes(body)
    print(json.dumps({name: {key: value for key, value in check.items() if key in {"passed", "max_abs_error", "error"}}
                      for name, check in checks.items()}, indent=2, sort_keys=True))
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
