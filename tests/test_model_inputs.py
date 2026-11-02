"""Task 7.2: path-free model-input staging, worker path verification and training-plan binding.

Everything here runs in the main env with fakes: no torch, no downloads, no GPU. The HF Hub client is
replaced by a fake ``huggingface_hub`` module whose ``snapshot_download`` writes a local directory.
"""
import hashlib
import importlib
import json
import math
import struct
import sys
import types
from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError
from test_model_ops_contract import PEFT_0181_EXPORT

from tuning_factory.config import AIConfig, GraphTrainingConfig, load_config
from tuning_factory.executor_profiles import ExecutorProfile, ResourceFacts
from tuning_factory.model_artifacts import (
    HFRootRef,
    ModelArtifactRef,
    ModelArtifactStore,
    ModelDescriptor,
    ModelInput,
    ModelLoadRef,
    TensorMetadata,
)
from tuning_factory.model_hub import ModelProfile
from tuning_factory.projects import DatasetRef
from tuning_factory.registry import Registry, canonical, digest
from tuning_factory.training import LlamaFactoryBackend


def runner():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.operation_runner")
    except ModuleNotFoundError:
        pytest.fail("model-input staging and training-plan binding are missing")


def sha(data):
    return hashlib.sha256(data).hexdigest()


CONFIG = canonical({"model_type": "qwen2", "hidden_size": 2})
# A WordLevel vocabulary with one special token, so tokenizer comparisons have real ids to compare.
TOKENIZER = {
    "tokenizer.json": canonical({"version": "1.0", "added_tokens": [{"id": 0, "content": "<unk>", "special": True}],
                                 "model": {"type": "WordLevel", "unk_token": "<unk>",
                                           "vocab": {"<unk>": 0, "q": 1, "v": 2}}}),
    "tokenizer_config.json": canonical({"added_tokens_decoder": {"0": {"content": "<unk>", "special": True}}}),
}
MODULES = ("model.q_proj", "model.v_proj")


def fingerprint(files=TOKENIZER):
    return sha(canonical([{"path": name, "size": len(data), "sha256": sha(data)}
                          for name, data in sorted(files.items())]))


ROOT = HFRootRef(repo_id="test/tiny", revision="a" * 40, config_sha256=sha(CONFIG), tokenizer_fingerprint=fingerprint())
PROFILE = ModelProfile(repo_id=ROOT.repo_id, revision=ROOT.revision, config_sha256=ROOT.config_sha256,
                       architecture="qwen2", template="qwen")
EXECUTOR = ExecutorProfile(profile_id="p", revision="1", name="Pod", pod_id="pod1", credential_scope="local")
FACTS = ResourceFacts(pod_id="pod1", gpu_type="A100", gpu_count=1, total_hourly_cost_usd=2,
                      observed_at="2026-10-20T00:00:00Z")


# -- on-disk fixtures ----------------------------------------------------------------------------

def write_safetensors(path, shapes):
    header, offset = {}, 0
    for name, shape in shapes.items():
        size = 4 * math.prod(shape)
        header[name] = {"dtype": "F32", "shape": list(shape), "data_offsets": [offset, offset + size]}
        offset += size
    body = canonical(header)
    path.write_bytes(struct.pack("<Q", len(body)) + body + bytes(offset))


def write_snapshot(directory, *, config=CONFIG, tokenizer=TOKENIZER):
    directory.mkdir(parents=True)
    (directory / "config.json").write_bytes(config)
    for name, data in tokenizer.items():
        (directory / name).write_bytes(data)


def tensors_of(shapes, file):
    return [TensorMetadata(file=file, name=name, shape=shape, dtype="F32") for name, shape in shapes.items()]


VALUE_HEAD = {"v_head.summary.weight": (1, 2), "v_head.summary.bias": (1,)}


def full_model(tmp_path, marker, *, modules=MODULES, config=CONFIG, tokenizer=TOKENIZER, quantization_bit=None,
               value_head=None, head=VALUE_HEAD, base=None, parents=(), synthetic=False):
    """A full-model snapshot; value_head names the file that holds the v_head tensors."""
    directory = tmp_path / "src" / marker
    write_snapshot(directory, config=config, tokenizer=tokenizer)
    shapes = {f"{module}.weight": (2, 2) for module in modules}
    if value_head == "model.safetensors":
        shapes.update(head)
    write_safetensors(directory / "model.safetensors", shapes)
    tensors = tensors_of(shapes, "model.safetensors")
    if value_head == "value_head.safetensors":
        write_safetensors(directory / "value_head.safetensors", head)
        tensors += tensors_of(head, "value_head.safetensors")
    descriptor = ModelDescriptor(
        producer_job_id=marker, producer_attempt_id=marker, producer_manifest_sha256="b" * 64,
        storage_kind="full_model", semantic_kind="reward_model" if value_head else "causal_lm",
        has_value_head=bool(value_head), root=ROOT, effective_base=base or ROOT, parents=parents,
        config_sha256=sha(config), tokenizer_fingerprint=fingerprint(tokenizer), quantization_bit=quantization_bit,
        tensors=tuple(tensors), synthetic=synthetic)
    return directory, descriptor


def peft_adapter(tmp_path, marker, *, rank=2, alpha=4, base=None, parents=(), method="lora",
                 quantization_bit=None, value_head=False, targets=("q_proj", "v_proj"), loraplus_lr_ratio=None,
                 config=CONFIG, **export_changes):
    """A LoRA adapter whose adapter_config.json carries every key of a real PEFT 0.18.1 export."""
    from tuning_factory.model_ops import adapter_metadata_from_config
    directory = tmp_path / "src" / marker
    write_snapshot(directory, config=config)
    exported = {**PEFT_0181_EXPORT, "r": rank, "lora_alpha": alpha, "target_modules": sorted(targets),
                **export_changes}
    (directory / "adapter_config.json").write_text(json.dumps(exported, indent=2, sort_keys=True), encoding="utf-8")
    shapes = {}
    for target in targets:
        shapes[f"base_model.model.model.{target}.lora_A.weight"] = (rank, 2)
        shapes[f"base_model.model.model.{target}.lora_B.weight"] = (2, rank)
    write_safetensors(directory / "adapter_model.safetensors", shapes)
    tensors = tensors_of(shapes, "adapter_model.safetensors")
    if value_head:
        write_safetensors(directory / "value_head.safetensors", VALUE_HEAD)
        tensors += tensors_of(VALUE_HEAD, "value_head.safetensors")
    metadata = adapter_metadata_from_config(exported, method=method, quantization_bit=quantization_bit,
                                            loraplus_lr_ratio=loraplus_lr_ratio)
    descriptor = ModelDescriptor(
        producer_job_id=marker, producer_attempt_id=marker, producer_manifest_sha256="b" * 64,
        storage_kind="adapter", semantic_kind="reward_model" if value_head else "causal_lm",
        has_value_head=value_head, root=ROOT, effective_base=base or ROOT, parents=parents,
        config_sha256=sha(config), tokenizer_fingerprint=ROOT.tokenizer_fingerprint, adapter=metadata,
        tensors=tuple(tensors))
    return directory, descriptor


def frozen(store, pair):
    directory, descriptor = pair
    return store.freeze(directory, descriptor), descriptor


def cache_path(store, ref):
    return (store.root / ref.artifact_id / ref.inventory_sha256 / "files").as_posix()


def evidence(base, modules=MODULES):
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    return ModelTargetEvidence(base=base, modules=tuple(
        TargetModuleEvidence(name=name, input_dimension=2, output_dimension=2) for name in modules))


def inspector(path, base):
    return evidence(base)


def producer(ref, descriptor, stage="sft"):
    from tuning_factory.graph_planner import ProducerEvidence
    return ProducerEvidence(model=ref, job_id=descriptor.producer_job_id, attempt_id=descriptor.producer_attempt_id,
                            manifest_sha256=descriptor.producer_manifest_sha256, runtime_profile="primary",
                            training_stage=stage, status="completed", tracking_acknowledged=True)


# -- operation fixtures --------------------------------------------------------------------------

def graph_config(*, stage="sft", method="lora", **changes):
    hyperparameters = {"lora_rank": 2, **changes.pop("hyperparameters", {})}
    return GraphTrainingConfig(
        ai_id="graph_child", workflow_id="project", dataset_id="data", dataset_version="1",
        base_model=ROOT.repo_id, model_profile=PROFILE, model_revision=ROOT.revision,
        tokenizer_revision=ROOT.revision, training_stage=stage, training_method=method,
        hyperparameters=hyperparameters, resource_profile=FACTS.legacy_resources(900), **changes)


def train_operation(model_input, *, stage="sft", method="lora", simulation=False, **changes):
    from tuning_factory.operations import OperationLineage, OperationResources, RuntimeProfile, TrainOperation
    ppo = stage == "ppo"
    runtime = RuntimeProfile(profile="ppo" if ppo else "primary", engine_version="0.9.3" if ppo else "0.9.5",
                             dependency_sha256="e" * 64)
    return TrainOperation(
        config=graph_config(stage=stage, method=method, **changes), model_input=model_input,
        dataset=DatasetRef(dataset_id="data", version="1", sha256="c" * 64),
        resources=OperationResources(executor=EXECUTOR, facts=FACTS, timeout_seconds=900, runtime=runtime),
        lineage=OperationLineage(project_id="project", project_revision="f" * 64, node_id="node",
                                 plan_sha256="d" * 64, simulation=simulation))


def policy(base, adapter=None, *, reference=False, reward=None):
    load = ModelLoadRef(base=base, adapter=adapter)
    initialization = "continue_adapter" if adapter else "new_adapter"
    return ModelInput(policy=load, initialization=initialization, frozen_reference=load if reference else None,
                      reward_model=reward)


def full_weights(base, *, reference=False):
    load = ModelLoadRef(base=base)
    return ModelInput(policy=load, initialization="full_weights", frozen_reference=load if reference else None)


def stage(tmp_path, store, operation, **kwargs):
    return runner().stage_model_inputs(operation, store, tmp_path / "attempt", **kwargs)


def roots_for(store, hf_home=None):
    return (store.root, *((Path(hf_home),) if hf_home is not None else ()))


def bind(tmp_path, store, operation, *, hf_home=None, inspect=None, **kwargs):
    module = runner()
    resolved = stage(tmp_path, store, operation, **kwargs)
    verified = module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=hf_home,
                                         inspect_targets=inspect)
    template = LlamaFactoryBackend.translate(operation.config, operation.model_input)
    return module.bind_training_plan(template, verified, approved_roots=roots_for(store, hf_home)), verified


def sentinel(role):
    from tuning_factory.training import ROLE_PREFIX
    return ROLE_PREFIX + role


def placeholders(template):
    from tuning_factory.training import ROLE_PREFIX
    return {key: value for key, value in template.items() if isinstance(value, str) and value.startswith(ROLE_PREFIX)}


@pytest.fixture
def store(tmp_path):
    return ModelArtifactStore(tmp_path / "models")


@pytest.fixture
def sft(tmp_path, store):
    """A materialized SFT full model: the effective parent for preference or continued training."""
    return frozen(store, full_model(tmp_path, "sft"))


def fake_hub(monkeypatch, populate):
    calls = []

    def snapshot_download(repo_id, *, revision, local_dir, allow_patterns, **kwargs):
        calls.append({"repo_id": repo_id, "revision": revision, "local_dir": local_dir,
                      "allow_patterns": list(allow_patterns), **kwargs})
        target = Path(local_dir)
        target.mkdir(parents=True, exist_ok=True)
        populate(target)
        # huggingface_hub keeps download metadata under local_dir/.cache/huggingface/.
        metadata = target / ".cache" / "huggingface" / "download"
        metadata.mkdir(parents=True, exist_ok=True)
        (metadata / "tokenizer.json.metadata").write_text("etag", encoding="utf-8")
        return str(target.resolve())

    module = types.ModuleType("huggingface_hub")
    module.snapshot_download = snapshot_download
    monkeypatch.setitem(sys.modules, "huggingface_hub", module)
    return calls


def tiny_snapshot(target, *, tokenizer=TOKENIZER, config=CONFIG):
    (target / "config.json").write_bytes(config)
    for name, data in tokenizer.items():
        (target / name).write_bytes(data)
    write_safetensors(target / "model.safetensors", {f"{module}.weight": (2, 2) for module in MODULES})


# -- 1-3. translate: legacy bytes and the logical template ---------------------------------------

def legacy_matrix():
    from tuning_factory.capabilities import METHODS, STAGES
    from tuning_factory.config import FinetuningOptions, StageOptions
    base = AIConfig(ai_id="golden", workflow_id="golden", dataset_id="data", dataset_version="1",
                    base_model="Qwen/Qwen2.5-0.5B-Instruct", model_revision="a" * 40, tokenizer_revision="a" * 40)
    configs = {}
    for training_stage in STAGES:
        for method in METHODS:
            update = {"training_stage": training_stage, "training_method": method}
            if training_stage == "ppo":
                update["stage_options"] = StageOptions(reward_model="/workspace/rm", reward_model_sha256="0" * 64)
            if method == "oft":
                update["finetuning_options"] = FinetuningOptions(oft_rank=8, oft_block_size=0)
            # model_copy skips validation, so every stage x method row exists (as in plan F18).
            configs[f"{training_stage}/{method}"] = base.model_copy(update=update)
    return configs


# Digests of LlamaFactoryBackend.translate(config) computed at HEAD 42064b7, before 7.2 touched training.py
# (translate is unchanged since cc87170, so this equals the plan's baseline 1c5d2d3). The two example-config
# digests reproduce plan F18 exactly; the matrix construction is the one above.
# Re-pinned when save_safetensors was dropped (transformers 5 rejects it); adding it back reproduces the old digests.
LEGACY_MATRIX_SHA256 = "cfc98cd0e0a682d41044e3617a29219f08fd01cb9409eaa278e66fc3e405f40b"
EXAMPLE_SHA256 = {
    "configs/ai/paper_extraction.yaml": "881cef8b8c3840b53d10f35ab1269618cb8739943ee9424c4331a42728484afb",
    "configs/ai/integration_smoke.yaml": "83733974b0a80c5f19b1121a02c1917f1a5e946f9c78b6e736aa92c45428cc97",
}


def test_translate_legacy_output_is_byte_identical():
    configs = legacy_matrix()
    assert len(configs) == 30
    plans = {key: LlamaFactoryBackend.translate(config) for key, config in configs.items()}
    assert digest(canonical(plans)) == LEGACY_MATRIX_SHA256
    assert all(LlamaFactoryBackend.translate(config, None) == plans[key] for key, config in configs.items())
    for path, expected in EXAMPLE_SHA256.items():
        config = load_config(path)
        assert digest(canonical(LlamaFactoryBackend.translate(config))) == expected
        assert LlamaFactoryBackend.translate(config, None) == LlamaFactoryBackend.translate(config)


def test_v1_manifest_training_config_hash_unchanged(tmp_path):
    from tuning_factory.manifest import resolve
    registry = Registry(tmp_path / "registry")
    data = tmp_path / "dummy.json"
    data.write_bytes(canonical([{"instruction": "x", "output": "y"}]))
    registry.register_dataset("dummy", "1", data)
    manifest = resolve(load_config("configs/ai/paper_extraction.yaml"), registry)
    assert manifest["training_config_sha256"] == EXAMPLE_SHA256["configs/ai/paper_extraction.yaml"]


def test_logical_template_holds_only_role_placeholders(sft):
    from tuning_factory.training import ROLE_SLOTS
    sft_ref, _ = sft
    adapter = ModelArtifactRef(artifact_id="1" * 64, inventory_sha256="2" * 64)
    cases = {
        "root": (graph_config(), policy(ROOT), {"model_name_or_path": "policy_base"}),
        "continuation": (graph_config(stage="dpo", method="qlora"), policy(sft_ref, adapter, reference=True), {
            "model_name_or_path": "policy_base", "adapter_name_or_path": "policy_adapter",
            "ref_model": "reference_base", "ref_model_adapters": "reference_adapter"}),
        "ppo": (graph_config(stage="ppo"), policy(sft_ref, reward=ModelLoadRef(base=sft_ref, adapter=adapter)), {
            "model_name_or_path": "policy_base", "reward_model": "reward_adapter"}),
    }
    for config, model_input, expected in cases.values():
        template = LlamaFactoryBackend.translate(config, model_input)
        assert placeholders(template) == {key: sentinel(role) for key, role in expected.items()}
        assert all(ROLE_SLOTS[role] == key for key, role in expected.items())
        assert "model_revision" not in template
        assert config.base_model not in canonical(template).decode()
        assert not any(isinstance(value, str) and value.startswith("/") for value in template.values())
        assert {key: template[key] for key in ("dataset_dir", "output_dir")} == {
            "dataset_dir": "./data", "output_dir": "./output"}
    assert LlamaFactoryBackend.translate(*cases["continuation"][:2])["create_new_adapter"] is False
    assert LlamaFactoryBackend.translate(*cases["continuation"][:2])["ref_model_quantization_bit"] == 4
    assert LlamaFactoryBackend.translate(*cases["ppo"][:2])["reward_model_type"] == "lora"


def test_prepare_writes_the_logical_template_for_graph_configs(tmp_path):
    config = graph_config()
    model_input = policy(ROOT)
    LlamaFactoryBackend().prepare(config, tmp_path, b'[{"instruction":"x","output":"y"}]', model_input)
    written = yaml.safe_load((tmp_path / "train.yaml").read_text(encoding="utf-8"))
    assert written == LlamaFactoryBackend.translate(config, model_input)
    assert type(LlamaFactoryBackend().validate_config(graph_config(stage="ppo"))) is GraphTrainingConfig
    legacy = tmp_path / "legacy"
    with pytest.raises(ValueError, match="graph training requires a typed model_input"):
        LlamaFactoryBackend().prepare(config, legacy, b'[{"instruction":"x","output":"y"}]')
    assert not (legacy / "train.yaml").exists()


@pytest.mark.parametrize("method,model_input,message", [
    ("full", lambda: policy(ROOT, ModelArtifactRef(artifact_id="1" * 64, inventory_sha256="2" * 64)),
     "adapter continuation requires LoRA/QLoRA"),
    ("freeze", lambda: policy(ROOT), "initialization differs from requested training method"),
    ("lora", lambda: full_weights(ROOT), "initialization differs from requested training method"),
])
def test_translate_refuses_initialization_that_differs_from_method(method, model_input, message):
    with pytest.raises(ValueError, match=message):
        LlamaFactoryBackend.translate(graph_config(method=method), model_input())


# -- 4-8. staging, verification and tokenizers ---------------------------------------------------

def test_child_of_materialized_sft_binds_effective_snapshot_not_root(tmp_path, store, sft):
    sft_ref, _ = sft
    operation = train_operation(policy(sft_ref))
    bound, _ = bind(tmp_path, store, operation)
    assert bound.plan["model_name_or_path"] == cache_path(store, sft_ref)
    text = yaml.safe_dump(bound.plan)
    assert operation.config.base_model not in text and ROOT.revision not in text


def test_staging_never_copies_weights_into_attempt_directory(tmp_path, store, sft):
    module = runner()
    sft_ref, _ = sft
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "adapter", base=sft_ref, parents=(sft_ref,)))
    operation = train_operation(policy(sft_ref, adapter_ref))
    resolved = stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
                     producers=(producer(adapter_ref, adapter),))
    attempt = tmp_path / "attempt"
    assert [item.name for item in attempt.rglob("*")] == ["model-inputs.json"]
    assert (attempt / "model-inputs.json").read_bytes() == canonical(resolved.model_dump(mode="json"))
    assert module.ResolvedModelInputs.model_validate_json((attempt / "model-inputs.json").read_bytes()) == resolved
    assert resolved.operation_sha256 == digest(canonical(operation.model_dump(mode="json")))
    assert [(item.role, item.source, item.storage_kind) for item in resolved.models] == [
        ("policy_base", "artifact", "full_model"), ("policy_adapter", "artifact", "adapter")]
    assert resolved.transfers == tuple(sorted((sft_ref, adapter_ref),
                                              key=lambda ref: (ref.artifact_id, ref.inventory_sha256)))


@pytest.mark.parametrize("damage", ["weights", "missing"])
def test_staging_refuses_tampered_or_missing_artifact(tmp_path, store, sft, damage):
    sft_ref, _ = sft
    ref = sft_ref
    if damage == "weights":
        (Path(cache_path(store, sft_ref)) / "model.safetensors").write_bytes(b"tampered")
    else:
        ref = ModelArtifactRef(artifact_id="1" * 64, inventory_sha256="2" * 64)
    with pytest.raises(ValueError, match="hash|inventory|missing|inaccessible"):
        stage(tmp_path, store, train_operation(policy(ref)))
    assert not (tmp_path / "attempt" / "model-inputs.json").exists()


def test_missing_local_tokenizer_is_refused(tmp_path, store, monkeypatch):
    from tuning_factory.model_artifacts import _inventory, _inventory_bytes
    module = runner()
    # A cached base whose consistent inventory lacks tokenizer_config.json (built directly in cache layout).
    directory, descriptor = full_model(tmp_path, "no-tokenizer")
    (directory / "tokenizer_config.json").unlink()
    inventory = _inventory(directory)
    body = canonical(descriptor.model_dump(mode="json"))
    ref = ModelArtifactRef(artifact_id=digest(body), inventory_sha256=digest(_inventory_bytes(inventory)))
    location = store.root / ref.artifact_id / ref.inventory_sha256
    location.mkdir(parents=True)
    directory.rename(location / "files")
    (location / "descriptor.json").write_bytes(body)
    (location / "inventory.json").write_bytes(_inventory_bytes(inventory))
    with pytest.raises(ValueError, match="complete local tokenizer snapshot is required"):
        module.verify_cached_artifact(store.root, ref)
    # An HF snapshot that lacks tokenizer files.
    fake_hub(monkeypatch, lambda target: tiny_snapshot(target, tokenizer={}))
    with pytest.raises(ValueError, match="complete local tokenizer snapshot is required"):
        module.resolve_hf_root(ROOT, tmp_path / "hf")


def test_reference_and_reward_tokenizers_must_match_policy(tmp_path, store):
    module = runner()
    other = {**TOKENIZER, "tokenizer.json": b'{"version":"2.0"}'}
    reward_ref, reward_descriptor = frozen(store, full_model(tmp_path, "reward", tokenizer=other,
                                                             value_head="value_head.safetensors"))
    operation = train_operation(policy(ROOT, reward=ModelLoadRef(base=reward_ref)), stage="ppo")
    with pytest.raises(ValueError, match="tokenizer differs from policy"):
        stage(tmp_path, store, operation)
    # The worker re-derives the roles and applies the same rule to the verified cache descriptors.
    staged = (module.StagedModel(role="policy_base", source="hf_root", hf_root=ROOT, storage_kind="full_model",
                                 config_sha256=ROOT.config_sha256, tokenizer_fingerprint=ROOT.tokenizer_fingerprint),
              module.StagedModel(role="reward_base", source="artifact", artifact=reward_ref, storage_kind="full_model",
                                 config_sha256=reward_descriptor.config_sha256,
                                 tokenizer_fingerprint=reward_descriptor.tokenizer_fingerprint))
    resolved = module.ResolvedModelInputs(operation_kind="train",
                                          operation_sha256=digest(canonical(operation.model_dump(mode="json"))),
                                          models=staged, transfers=(reward_ref,), reward_model_type="full")
    with pytest.raises(ValueError, match="^reward_base tokenizer differs from policy$"):
        module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=tmp_path / "hf")


def test_hf_root_resolves_to_a_link_free_verified_local_directory(tmp_path, store, monkeypatch):
    module = runner()
    calls = fake_hub(monkeypatch, tiny_snapshot)
    path = module.resolve_hf_root(ROOT, tmp_path / "hf")
    assert path == (tmp_path / "hf" / "factory-roots" / "test" / "tiny" / ROOT.revision).absolute()
    assert calls[0]["repo_id"] == ROOT.repo_id and calls[0]["revision"] == ROOT.revision
    assert Path(calls[0]["local_dir"]).absolute() == path and "cache_dir" not in calls[0]
    patterns = calls[0]["allow_patterns"]
    assert {"config.json", "tokenizer.json", "tokenizer_config.json", "model.safetensors",
            "model.safetensors.index.json"} <= set(patterns)
    assert not any(pattern.endswith((".py", ".bin", ".pt")) for pattern in patterns)
    # The worker binds the same verified tree whose fingerprints it computed.
    operation = train_operation(policy(ROOT))
    bound, _ = bind(tmp_path, store, operation, hf_home=tmp_path / "hf")
    assert bound.plan["model_name_or_path"] == path.as_posix()


def test_hf_download_includes_processor_files_and_qwen35_shard_names():
    from fnmatch import fnmatch

    from tuning_factory.model_artifacts import PROCESSOR_FILES
    from tuning_factory.operation_runner import HF_ALLOW_PATTERNS
    for name in (*PROCESSOR_FILES, "chat_template.jinja", "model.safetensors-00001-of-00002.safetensors",
                 "model-00001-of-00002.safetensors"):
        assert any(fnmatch(name, pattern) for pattern in HF_ALLOW_PATTERNS), name
    assert not any(fnmatch(name, pattern) for pattern in HF_ALLOW_PATTERNS
                   for name in ("pytorch_model.bin", "model.py", "consolidated.safetensors"))


@pytest.mark.parametrize("problem,message", [
    ("config", "model config snapshot fingerprint mismatch"),
    ("tokenizer", "tokenizer snapshot fingerprint mismatch"),
    ("redirect", "transformers_weights"),
    ("ambiguous", "ambiguous single and sharded model weights"),
    ("no_safetensors", "safetensors"),
    ("link", "link|reparse"),
])
def test_hf_snapshot_must_match_root_and_be_loadable_without_links(tmp_path, monkeypatch, problem, message):
    from test_model_artifacts import directory_link
    module = runner()
    root = ROOT

    def populate(target):
        tiny_snapshot(target)
        if problem == "config":
            (target / "config.json").write_bytes(canonical({"model_type": "qwen2", "hidden_size": 3}))
        elif problem == "tokenizer":
            (target / "tokenizer.json").write_bytes(b'{"version":"2.0"}')
        elif problem == "ambiguous":
            write_safetensors(target / "model-00001-of-00001.safetensors", {"model.q_proj.weight": (2, 2)})
            (target / "model.safetensors.index.json").write_bytes(canonical(
                {"weight_map": {"model.q_proj.weight": "model-00001-of-00001.safetensors"}}))
        elif problem == "no_safetensors":
            (target / "model.safetensors").unlink()
        elif problem == "link":
            outside = tmp_path / "outside"
            outside.mkdir(exist_ok=True)
            directory_link(target / "chat_templates", outside)

    if problem == "redirect":
        config = canonical({"model_type": "qwen2", "hidden_size": 2, "transformers_weights": "other.safetensors"})
        root = ROOT.model_copy(update={"config_sha256": sha(config)})
        fake_hub(monkeypatch, lambda target: tiny_snapshot(target, config=config))
    else:
        fake_hub(monkeypatch, populate)
    with pytest.raises(ValueError, match=message):
        module.resolve_hf_root(root, tmp_path / "hf")


def test_model_caches_under_a_link_are_refused(tmp_path, store, sft, monkeypatch):
    from test_model_artifacts import directory_link
    module = runner()
    sft_ref, _ = sft
    linked_cache = tmp_path / "linked-cache"
    directory_link(linked_cache, store.root)
    with pytest.raises(ValueError, match="link|reparse"):
        module.verify_cached_artifact(linked_cache, sft_ref)
    fake_hub(monkeypatch, tiny_snapshot)
    real_home = tmp_path / "real-hf"
    real_home.mkdir()
    directory_link(tmp_path / "hf", real_home)
    with pytest.raises(ValueError, match="link|reparse"):
        module.resolve_hf_root(ROOT, tmp_path / "hf")


# -- 9-12. continuation rules and the F10 regression ---------------------------------------------

def test_continuation_alpha_none_resolves_to_twice_rank(tmp_path, store, sft):
    from tuning_factory import model_ops
    sft_ref, _ = sft
    parent_ref, parent = frozen(store, peft_adapter(tmp_path, "r8a16", rank=8, alpha=16, base=sft_ref,
                                                    parents=(sft_ref,)))
    config = graph_config(hyperparameters={"lora_rank": 8})
    assert config.finetuning_options.lora_alpha is None
    assert model_ops.expected_continuation_adapter(config, parent.adapter.target_modules).alpha == 16
    model_ops.check_continuation(config, parent.adapter, evidence(sft_ref))
    operation = train_operation(policy(sft_ref, parent_ref), hyperparameters={"lora_rank": 8})
    stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
          producers=(producer(parent_ref, parent),))
    _, alpha8 = peft_adapter(tmp_path, "r8a8", rank=8, alpha=8)
    with pytest.raises(ValueError, match="parent adapter settings differ; explicit materialize is required: alpha$"):
        model_ops.check_continuation(config, alpha8.adapter, evidence(ROOT))


def continuation_cases():
    q, k, v = "model.q_proj", "model.k_proj", "model.v_proj"
    return [
        pytest.param({}, {"hyperparameters": {"lora_rank": 4}}, MODULES, "rank", id="rank"),
        pytest.param({}, {"finetuning_options": {"lora_alpha": 8}}, MODULES, "alpha", id="alpha"),
        pytest.param({}, {"finetuning_options": {"lora_dropout": 0.1}}, MODULES, "dropout", id="dropout"),
        pytest.param({}, {"hyperparameters": {"lora_target": "all"}}, (q, k, v), "targets", id="targets"),
        pytest.param({}, {"finetuning_options": {"use_rslora": True}}, MODULES, "use_rslora", id="use_rslora"),
        pytest.param({}, {"finetuning_options": {"use_dora": True}}, MODULES, "use_dora", id="use_dora"),
        pytest.param({}, {"finetuning_options": {"pissa_init": True}}, MODULES, "pissa_init", id="pissa_init"),
        pytest.param({}, {"method": "qlora"}, MODULES, "method", id="lora_to_qlora"),
        pytest.param({"method": "qlora", "quantization_bit": 4}, {"method": "lora"}, MODULES, "method",
                     id="qlora_to_lora"),
        pytest.param({"method": "qlora", "quantization_bit": 4}, {"method": "qlora", "quantization_bit": 8},
                     MODULES, "quantization_bit", id="quantization_bit"),
        pytest.param({}, {"finetuning_options": {"loraplus_lr_ratio": 16.0}}, MODULES, "loraplus_lr_ratio",
                     id="loraplus_lr_ratio"),
    ]


@pytest.mark.parametrize("parent_changes,child_changes,modules,field", continuation_cases())
def test_continuation_mismatch_is_refused(tmp_path, parent_changes, child_changes, modules, field):
    from tuning_factory import model_ops
    _, parent = peft_adapter(tmp_path, "parent", **parent_changes)
    child_changes = dict(child_changes)
    config = graph_config(**{"hyperparameters": {"lora_target": "q_proj,v_proj"}, **child_changes})
    with pytest.raises(ValueError, match=field):
        model_ops.check_continuation(config, parent.adapter, evidence(ROOT, modules))
    matching = graph_config(method=parent_changes.get("method", "lora"),
                            hyperparameters={"lora_target": "q_proj,v_proj"},
                            quantization_bit=parent_changes.get("quantization_bit", 4))
    model_ops.check_continuation(matching, parent.adapter, evidence(ROOT, modules))


def test_continuation_accepts_real_peft_export_fields(tmp_path):
    from tuning_factory import model_ops
    _, parent = peft_adapter(tmp_path, "export")
    assert (parent.adapter.task_type, parent.adapter.inference_mode) == ("CAUSAL_LM", True)
    assert model_ops.EXPORT_ONLY_ADAPTER_FIELDS == frozenset({"inference_mode", "task_type"})
    expected = model_ops.expected_continuation_adapter(graph_config(), parent.adapter.target_modules)
    assert (expected.task_type, expected.inference_mode) == (None, False)
    assert model_ops.continuation_mismatches(parent.adapter, expected) == ()
    model_ops.check_continuation(graph_config(), parent.adapter, evidence(ROOT))


def compile_continuation(tmp_path, recipe_changes=None):
    from test_graph_planner import fixture, revise

    from tuning_factory.graph_planner import (
        ModelContext,
        NodeResources,
        VerifiedBindings,
        compile_node,
        plan_graph,
        snapshot_artifact,
    )
    from tuning_factory.projects import Revision
    from tuning_factory.recipes import RecipeSpec
    registry, project, choices, facts = fixture(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    ref, descriptor = frozen(store, peft_adapter(tmp_path, "export"))
    evidence_ = evidence(ROOT)
    recipe = RecipeSpec(recipe_id="continue", name="continue", **(recipe_changes or {"hyperparameters": {
        "lora_rank": 2, "lora_target": "all"}}))
    revision = Revision(spec=recipe, revision_sha256=digest(canonical(recipe.model_dump(mode="json"))))
    graph = dict(project.spec.graph)
    graph["nodes"] = [{**graph["nodes"][0], "recipe_revision": revision.revision_sha256,
                       "model_input": policy(ROOT, ref).model_dump(mode="json")}]
    snapshot = producer(ref, descriptor)
    choices = choices.model_copy(update={"recipes": (revision,), "models": (ModelContext(root=ROOT, profile=PROFILE),),
                                         "artifacts": (snapshot_artifact(store, ref, snapshot),),
                                         "base_targets": (evidence_,),
                                         "node_resources": (NodeResources(node_id="train.1", timeout_seconds=1800),)})
    plan = plan_graph(revise(project, graph), choices, (facts,))
    return compile_node(plan, "train.1", VerifiedBindings(registry, store, choices.executors[0], producers=(snapshot,),
                                                          base_targets=(evidence_,))), ref


def test_compile_node_continuation_accepts_real_peft_export(tmp_path):
    operation, ref = compile_continuation(tmp_path)
    assert operation.model_input.policy.adapter == ref
    assert operation.model_input.initialization == "continue_adapter"


def test_compile_node_continuation_mismatch_names_fields(tmp_path):
    with pytest.raises(ValueError, match="parent adapter settings differ; explicit materialize is required: alpha, rank"):
        compile_continuation(tmp_path, {"hyperparameters": {"lora_rank": 4, "lora_target": "all"}})


# -- 13-17. adapter slots, frozen references -----------------------------------------------------

def test_continuation_resumes_parent_adapter_with_fresh_optimizer(tmp_path, store, sft):
    module = runner()
    sft_ref, _ = sft
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "adapter", base=sft_ref, parents=(sft_ref,)))
    operation = train_operation(policy(sft_ref, adapter_ref))
    bound, verified = bind(tmp_path, store, operation, inspect=inspector, target_evidence=(evidence(sft_ref),),
                           producers=(producer(adapter_ref, adapter),))
    assert bound.plan["adapter_name_or_path"] == cache_path(store, adapter_ref)
    assert bound.plan["model_name_or_path"] == cache_path(store, sft_ref)
    assert bound.plan["create_new_adapter"] is False
    assert "resume_from_checkpoint" not in bound.plan
    assert bound.plan["output_dir"] == "./output"
    assert not list(Path(cache_path(store, adapter_ref)).glob("optimizer*"))
    template = LlamaFactoryBackend.translate(operation.config, operation.model_input)
    roots = roots_for(store)
    with pytest.raises(ValueError, match="resume_from_checkpoint"):
        module.bind_training_plan({**template, "resume_from_checkpoint": "./output/checkpoint-1"}, verified,
                                  approved_roots=roots)
    with pytest.raises(ValueError, match="create_new_adapter"):
        module.bind_training_plan({**template, "create_new_adapter": True}, verified, approved_roots=roots)


def test_worker_continuation_requires_inspected_targets(tmp_path, store):
    module = runner()
    qkv = ("model.q_proj", "model.k_proj", "model.v_proj")
    base_ref, _ = frozen(store, full_model(tmp_path, "qkv", modules=qkv))
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "adapter", base=base_ref, parents=(base_ref,)))
    producers = (producer(adapter_ref, adapter),)
    narrow = train_operation(policy(base_ref, adapter_ref), hyperparameters={"lora_target": "q_proj,v_proj"})
    wide = train_operation(policy(base_ref, adapter_ref), hyperparameters={"lora_target": "all"})
    with pytest.raises(ValueError, match="adapter continuation requires inspected"):
        stage(tmp_path, store, narrow, producers=producers)
    with pytest.raises(ValueError, match="inspected target modules differ"):
        # Artifact-base evidence must be the complete linear inventory of the verified full model.
        stage(tmp_path, store, narrow, target_evidence=(evidence(base_ref),), producers=producers)
    with pytest.raises(ValueError, match="resolved adapter targets differ"):
        stage(tmp_path, store, wide, target_evidence=(evidence(base_ref, qkv),), producers=producers)
    resolved = stage(tmp_path, store, narrow, target_evidence=(evidence(base_ref, qkv),), producers=producers)
    module.verify_model_paths(resolved, narrow, model_cache=store.root, hf_home=None,
                              inspect_targets=lambda path, base: evidence(base, qkv))
    with pytest.raises(ValueError, match="inspected effective-base target"):
        module.verify_model_paths(resolved, narrow, model_cache=store.root, hf_home=None)
    with pytest.raises(ValueError, match="inspected target modules differ"):
        module.verify_model_paths(resolved, narrow, model_cache=store.root, hf_home=None,
                                  inspect_targets=lambda path, base: evidence(base, MODULES))
    # The worker re-checks the child's targets with its own inspection (same roles, other operation hash).
    rehashed = resolved.model_copy(update={"operation_sha256": digest(canonical(wide.model_dump(mode="json")))})
    with pytest.raises(ValueError, match="resolved adapter targets differ"):
        module.verify_model_paths(rehashed, wide, model_cache=store.root, hf_home=None,
                                  inspect_targets=lambda path, base: evidence(base, qkv))


@pytest.mark.parametrize("method,initialization", [("lora", "new_adapter"), ("full", "full_weights")])
def test_new_adapter_on_full_parent_has_no_adapter_slot(tmp_path, store, sft, method, initialization):
    sft_ref, _ = sft
    model_input = policy(sft_ref) if initialization == "new_adapter" else full_weights(sft_ref)
    bound, _ = bind(tmp_path, store, train_operation(model_input, method=method))
    assert bound.plan["model_name_or_path"] == cache_path(store, sft_ref)
    assert "adapter_name_or_path" not in bound.plan and "create_new_adapter" not in bound.plan
    with pytest.raises(ValueError, match="initialization differs from requested training method"):
        stage(tmp_path, store, train_operation(full_weights(sft_ref) if method == "lora" else policy(sft_ref),
                                               method=method))


@pytest.mark.parametrize("case", ["dpo-sigmoid", "kto", "dpo-continuation"])
def test_dpo_kto_frozen_reference_is_exact_confirmed_parent(tmp_path, store, sft, case):
    sft_ref, _ = sft
    kwargs = {}
    if case == "dpo-continuation":
        adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "dpo-parent", base=sft_ref, parents=(sft_ref,)))
        operation = train_operation(policy(sft_ref, adapter_ref, reference=True), stage="dpo")
        kwargs = dict(inspect=inspector, target_evidence=(evidence(sft_ref),),
                      producers=(producer(adapter_ref, adapter, stage="dpo"),))
    elif case == "kto":
        operation = train_operation(policy(sft_ref, reference=True), stage="kto", method="qlora")
    else:
        operation = train_operation(policy(sft_ref, reference=True), stage="dpo")
    bound, _ = bind(tmp_path, store, operation, **kwargs)
    assert bound.plan["ref_model"] == bound.plan["model_name_or_path"] == cache_path(store, sft_ref)
    if case == "dpo-continuation":
        assert bound.plan["ref_model_adapters"] == bound.plan["adapter_name_or_path"] == cache_path(store, adapter_ref)
    else:
        assert "ref_model_adapters" not in bound.plan
    # Q5: the frozen reference of a QLoRA policy is quantized like the policy; otherwise unquantized.
    assert bound.plan.get("ref_model_quantization_bit") == (4 if case == "kto" else None)
    assert bound.binding["reference_quantization_bit"] == (4 if case == "kto" else None)
    missing = operation.model_copy(update={"model_input": operation.model_input.model_copy(
        update={"frozen_reference": None})})
    with pytest.raises(ValueError, match="frozen reference must bind the exact policy parent"):
        LlamaFactoryBackend.translate(missing.config, missing.model_input)
    with pytest.raises(ValueError, match="frozen reference must bind the exact policy parent"):
        stage(tmp_path, store, missing, **{key: value for key, value in kwargs.items() if key != "inspect"})


@pytest.mark.parametrize("training_stage,loss", [("dpo", "orpo"), ("dpo", "simpo"), ("sft", "sigmoid")])
def test_reference_free_losses_bind_no_reference(tmp_path, store, sft, training_stage, loss):
    sft_ref, _ = sft
    changes = {"stage_options": {"pref_loss": loss}} if training_stage == "dpo" else {}
    operation = train_operation(policy(sft_ref), stage=training_stage, **changes)
    bound, _ = bind(tmp_path, store, operation)
    assert not any(key.startswith("ref_model") for key in bound.plan)
    with_reference = train_operation(policy(sft_ref, reference=True), stage=training_stage, **changes)
    with pytest.raises(ValueError, match="reference-free recipe cannot bind a frozen reference"):
        LlamaFactoryBackend.translate(with_reference.config, with_reference.model_input)
    with pytest.raises(ValueError, match="reference-free recipe cannot bind a frozen reference"):
        stage(tmp_path, store, with_reference)


def test_sft_adapter_into_preference_training_is_refused(tmp_path, store, sft):
    sft_ref, sft_descriptor = sft
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "sft-adapter", base=sft_ref, parents=(sft_ref,)))
    for training_stage in ("dpo", "kto"):
        operation = train_operation(policy(sft_ref, adapter_ref, reference=True), stage=training_stage)
        with pytest.raises(ValueError, match="SFT to preference training requires explicit materialize"):
            stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
                  producers=(producer(adapter_ref, adapter, stage="sft"),))
        with pytest.raises(ValueError, match="parent producer evidence differs from the verified artifact"):
            stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
                  producers=(producer(adapter_ref, sft_descriptor, stage="dpo"),))
        with pytest.raises(ValueError, match="parent training stage evidence is required"):
            stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),))


# -- 18-21. PPO reward -------------------------------------------------------------------------

def test_ppo_lora_reward_binds_adapter_on_policy_base(tmp_path, store, sft):
    sft_ref, _ = sft
    reward_ref, _ = frozen(store, peft_adapter(tmp_path, "reward", base=sft_ref, parents=(sft_ref,), value_head=True))
    operation = train_operation(policy(sft_ref, reward=ModelLoadRef(base=sft_ref, adapter=reward_ref)), stage="ppo")
    bound, _ = bind(tmp_path, store, operation)
    assert bound.plan["reward_model"] == cache_path(store, reward_ref)
    assert bound.plan["reward_model_type"] == "lora"
    assert (Path(bound.plan["reward_model"]) / "value_head.safetensors").is_file()
    assert bound.binding["reward_model_type"] == "lora"


def test_ppo_full_reward_binds_reward_base_even_when_config_default_is_lora(tmp_path, store, sft):
    sft_ref, _ = sft
    reward_ref, _ = frozen(store, full_model(tmp_path, "reward", value_head="value_head.safetensors"))
    operation = train_operation(policy(sft_ref, reward=ModelLoadRef(base=reward_ref)), stage="ppo")
    assert operation.config.stage_options.reward_model_type == "lora"
    bound, _ = bind(tmp_path, store, operation)
    assert bound.plan["reward_model"] == cache_path(store, reward_ref)
    assert bound.plan["reward_model_type"] == "full"


def test_ppo_reward_mismatch_is_refused(tmp_path, store, sft):
    sft_ref, _ = sft
    other_base, _ = frozen(store, peft_adapter(tmp_path, "root-reward", value_head=True))
    operation = train_operation(policy(sft_ref, reward=ModelLoadRef(base=ROOT, adapter=other_base)), stage="ppo")
    with pytest.raises(ValueError, match="LoRA reward requires the exact policy effective base"):
        stage(tmp_path, store, operation)
    with pytest.raises(ValueError, match="semantic kind reward_model"):
        stage(tmp_path, store, train_operation(policy(sft_ref, reward=ModelLoadRef(base=sft_ref)), stage="ppo"))
    inline_ref, _ = frozen(store, full_model(tmp_path, "inline-head", value_head="model.safetensors"))
    with pytest.raises(ValueError, match="value_head.safetensors"):
        stage(tmp_path, store, train_operation(policy(sft_ref, reward=ModelLoadRef(base=inline_ref)), stage="ppo"))
    weight_only, _ = frozen(store, full_model(tmp_path, "weight-only-head", value_head="value_head.safetensors",
                                              head={"v_head.summary.weight": (1, 2)}))
    with pytest.raises(ValueError, match="v_head.summary.weight and v_head.summary.bias"):
        stage(tmp_path, store, train_operation(policy(sft_ref, reward=ModelLoadRef(base=weight_only)), stage="ppo"))
    reward = ModelLoadRef(base=inline_ref)
    with pytest.raises(ValueError, match="PPO requires a typed reward_model binding; other stages cannot bind reward"):
        train_operation(policy(sft_ref, reward=reward), stage="sft")
    with pytest.raises(ValueError, match="PPO requires a typed reward_model binding; other stages cannot bind reward"):
        train_operation(policy(sft_ref), stage="ppo")
    with pytest.raises(ValueError, match="PPO requires a typed reward_model binding; other stages cannot bind reward"):
        LlamaFactoryBackend.translate(graph_config(), policy(sft_ref, reward=reward))
    legacy = AIConfig.model_validate({**graph_config(stage="ppo").model_dump(), "stage_options": {
        "reward_model": "/workspace/rm", "reward_model_sha256": "0" * 64}})
    with pytest.raises(ValueError, match="graph training binds only typed model roles"):
        LlamaFactoryBackend.translate(legacy, policy(sft_ref, reward=reward))


def test_ppo_continuation_is_refused(tmp_path, store, sft):
    sft_ref, _ = sft
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "ppo-parent", base=sft_ref, parents=(sft_ref,)))
    reward_ref, _ = frozen(store, full_model(tmp_path, "reward", value_head="value_head.safetensors"))
    operation = train_operation(policy(sft_ref, adapter_ref, reward=ModelLoadRef(base=reward_ref)), stage="ppo")
    message = ("PPO adapter continuation is unsupported: its KL reference would revert to the base; "
               "materialize the parent first")
    with pytest.raises(ValueError, match=message):
        LlamaFactoryBackend.translate(operation.config, operation.model_input)
    with pytest.raises(ValueError, match=message):
        stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
              producers=(producer(adapter_ref, adapter, stage="ppo"),))


# -- 22-25. binding safety, determinism, QLoRA base, resolved drift ------------------------------

def manual_entry(module, path, role="policy_base"):
    staged = module.StagedModel(role=role, source="hf_root", hf_root=ROOT, storage_kind="full_model",
                                config_sha256=ROOT.config_sha256, tokenizer_fingerprint=ROOT.tokenizer_fingerprint)
    return module.VerifiedModelPath(role=role, index=0, path=path, staged=staged, descriptor=None)


def manual_paths(module, path, role="policy_base"):
    return module.VerifiedModelPaths(models=(manual_entry(module, path, role),))


def test_bind_refuses_paths_outside_approved_cache_and_misplaced_slots(tmp_path):
    from test_model_artifacts import directory_link
    module = runner()
    cache = tmp_path / "cache"
    inside = cache / "model"
    inside.mkdir(parents=True)
    template = LlamaFactoryBackend.translate(graph_config(), policy(ROOT))
    roots = (cache,)
    assert module.bind_training_plan(template, manual_paths(module, inside), approved_roots=roots).plan[
        "model_name_or_path"] == inside.as_posix()
    outside = tmp_path / "elsewhere"
    outside.mkdir()
    for path in (cache / ".." / "elsewhere", outside):
        with pytest.raises(ValueError, match="bound model path is outside the approved model caches"):
            module.bind_training_plan(template, manual_paths(module, path), approved_roots=roots)
    directory_link(cache / "linked", outside)
    with pytest.raises(ValueError, match="link|reparse"):
        module.bind_training_plan(template, manual_paths(module, cache / "linked"), approved_roots=roots)
    paths = manual_paths(module, inside)
    with pytest.raises(ValueError, match="unbound or misplaced model role placeholder: output_dir"):
        module.bind_training_plan({**template, "output_dir": sentinel("policy_base")}, paths, approved_roots=roots)
    with pytest.raises(ValueError, match="unbound or misplaced model role placeholder: ref_model"):
        module.bind_training_plan({**template, "ref_model": sentinel("reference_base")}, paths, approved_roots=roots)
    with pytest.raises(ValueError, match="unbound or misplaced model role placeholder: adapter_name_or_path"):
        module.bind_training_plan({**template, "adapter_name_or_path": sentinel("policy_base")}, paths,
                                  approved_roots=roots)
    with pytest.raises(ValueError, match="graph training binds only typed model roles"):
        module.bind_training_plan({**template, "model_name_or_path": "Qwen/Qwen2.5-0.5B-Instruct"}, paths,
                                  approved_roots=roots)
    with pytest.raises(ValueError, match="unapproved path: dataset_dir"):
        module.bind_training_plan({**template, "dataset_dir": "/workspace/other"}, paths, approved_roots=roots)
    reference_paths = manual_paths(module, inside, role="reference_base")
    with pytest.raises(ValueError, match="unbound or misplaced model role placeholder"):
        module.bind_training_plan(template, reference_paths, approved_roots=roots)


def test_bind_uses_configured_roots_and_refuses_duplicate_roles(tmp_path, store, sft):
    module = runner()
    sft_ref, _ = sft
    operation = train_operation(policy(sft_ref))
    _, verified = bind(tmp_path, store, operation)
    assert not hasattr(verified, "roots")   # roots come from the worker configuration, never with the paths
    template = LlamaFactoryBackend.translate(operation.config, operation.model_input)
    elsewhere = tmp_path / "configured-cache"
    elsewhere.mkdir()
    with pytest.raises(ValueError, match="bound model path is outside the approved model caches"):
        module.bind_training_plan(template, verified, approved_roots=(elsewhere,))
    with pytest.raises(TypeError):
        module.bind_training_plan(template, verified)
    entry = verified.models[0]
    with pytest.raises(ValueError, match="duplicate verified model role"):
        module.VerifiedModelPaths(models=(entry, entry))


@pytest.mark.parametrize("key,value", [
    ("tokenized_path", "cache/tok"), ("cache_dir", "hf"), ("deepspeed", "ds.json"), ("logging_dir", "logs"),
    ("output_dir", "./other"), ("dataset_dir", "data"), ("media_dir", "images"), ("adapter_folder", "adapters"),
])
def test_known_path_keys_hold_only_approved_values(tmp_path, key, value):
    module = runner()
    cache = tmp_path / "cache"
    (cache / "model").mkdir(parents=True)
    template = {**LlamaFactoryBackend.translate(graph_config(), policy(ROOT)), key: value}
    with pytest.raises(ValueError, match=f"training plan contains an unapproved path: {key}"):
        module.bind_training_plan(template, manual_paths(module, cache / "model"), approved_roots=(cache,))


def test_bound_plan_evidence_is_deterministic(tmp_path, store, sft):
    module = runner()
    sft_ref, _ = sft
    operation = train_operation(policy(sft_ref, reference=True), stage="dpo")
    first, verified = bind(tmp_path, store, operation)
    template = LlamaFactoryBackend.translate(operation.config, operation.model_input)
    second = module.bind_training_plan(template, verified, approved_roots=roots_for(store))
    assert first == second
    assert first.template_sha256 == digest(canonical(template))
    assert first.bound_sha256 == digest(canonical(first.plan))
    assert first.binding_sha256 == digest(canonical(first.binding))
    written = []
    for name in ("one", "two"):
        directory = tmp_path / name
        directory.mkdir()
        module.write_bound_plan(directory, second if name == "two" else first)
        written.append({path.name: path.read_bytes() for path in sorted(directory.iterdir())})
    assert written[0] == written[1]
    assert set(written[0]) == {"train.bound.yaml", "model-binding.json", "binding-evidence.json"}
    assert yaml.safe_load(written[0]["train.bound.yaml"]) == first.plan
    assert json.loads(written[0]["binding-evidence.json"]) == {
        "template_sha256": first.template_sha256, "bound_sha256": first.bound_sha256,
        "binding_sha256": first.binding_sha256, "train_bound_yaml_sha256": sha(written[0]["train.bound.yaml"])}
    assert json.loads(written[0]["model-binding.json"]) == first.binding
    assert b"\r\n" not in written[0]["train.bound.yaml"]


def test_qlora_child_requires_unquantized_full_base(tmp_path, store):
    module = runner()
    quantized_ref, _ = frozen(store, full_model(tmp_path, "quantized", quantization_bit=4))
    with pytest.raises(ValueError, match="QLoRA requires an unquantized full effective base"):
        stage(tmp_path, store, train_operation(policy(quantized_ref), method="qlora"))
    config = canonical({"model_type": "qwen2", "hidden_size": 2, "quantization_config": {"load_in_4bit": True}})
    prequantized_ref, descriptor = frozen(store, full_model(tmp_path, "prequantized", config=config))
    operation = train_operation(policy(prequantized_ref), method="qlora")
    # The pre-quantized config.json is refused on the control plane, before any attempt directory is written.
    with pytest.raises(ValueError, match="QLoRA requires an unquantized full effective base"):
        stage(tmp_path, store, operation)
    assert not (tmp_path / "attempt" / "model-inputs.json").exists()
    # ...and again by the worker, which re-reads the bound config.json itself.
    resolved = module.ResolvedModelInputs(
        operation_kind="train", operation_sha256=digest(canonical(operation.model_dump(mode="json"))),
        models=(staged_artifact(module, "policy_base", prequantized_ref, descriptor),), transfers=(prequantized_ref,))
    with pytest.raises(ValueError, match="QLoRA requires an unquantized full effective base"):
        module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=None)


def test_worker_verification_rederives_roles_and_rejects_resolved_drift(tmp_path, store, sft):
    module = runner()
    sft_ref, _ = sft
    other_ref, _ = frozen(store, full_model(tmp_path, "other"))
    operation = train_operation(policy(sft_ref))
    resolved = stage(tmp_path, store, operation)
    module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=None)
    drifted = resolved.models[0].model_copy(update={"artifact": other_ref})
    renamed = resolved.models[0].model_copy(update={"role": "reference_base"})
    for models, transfers in (((drifted,), (other_ref,)), ((renamed,), resolved.transfers)):
        changed = resolved.model_copy(update={"models": models, "transfers": transfers})
        with pytest.raises(ValueError, match="staged model inputs differ"):
            module.verify_model_paths(module.ResolvedModelInputs.model_validate(changed.model_dump()), operation,
                                      model_cache=store.root, hf_home=None)
    another = train_operation(policy(other_ref))
    with pytest.raises(ValueError, match="staged model inputs belong to another operation"):
        module.verify_model_paths(resolved, another, model_cache=store.root, hf_home=None)


# -- non-training operations and the M2 planner/worker alignment ---------------------------------

def test_merge_and_materialize_stage_operation_roles(tmp_path, store, sft):
    from tuning_factory.operations import (
        MaterializeOperation,
        MergeOperation,
        OperationLineage,
        OperationResources,
        RuntimeProfile,
    )
    sft_ref, _ = sft
    first, _ = frozen(store, peft_adapter(tmp_path, "first", base=sft_ref, parents=(sft_ref,)))
    second, _ = frozen(store, peft_adapter(tmp_path, "second", rank=3, alpha=9, base=sft_ref, parents=(sft_ref,)))
    resources = OperationResources(executor=EXECUTOR, facts=FACTS, timeout_seconds=900, runtime=RuntimeProfile(
        profile="primary", engine_version="0.9.5", dependency_sha256="e" * 64))
    lineage = OperationLineage(project_id="project", project_revision="f" * 64, node_id="node", plan_sha256="d" * 64,
                               simulation=False)
    merge = MergeOperation(models=(first, second), weights=(0.25, 0.75), resources=resources, lineage=lineage)
    resolved = stage(tmp_path, store, merge)
    assert [(item.role, item.index) for item in resolved.models] == [
        ("operation_base", 0), ("operation_adapter", 0), ("operation_adapter", 1)]
    assert resolved.models[0].artifact == sft_ref
    verified = runner().verify_model_paths(resolved, merge, model_cache=store.root, hf_home=None)
    assert verified.path("operation_adapter", 1).as_posix() == cache_path(store, second)
    materialize = MaterializeOperation(model=first, resources=resources, lineage=lineage)
    assert [item.role for item in stage(tmp_path, store, materialize).models] == ["operation_base", "operation_adapter"]
    fan_in_ref, _ = frozen(store, peft_adapter(tmp_path, "fan-in", base=sft_ref, parents=(sft_ref,),
                                               fan_in_fan_out=True))
    with pytest.raises(ValueError, match="unsupported adapter conversion variant"):
        stage(tmp_path, store, MaterializeOperation(model=fan_in_ref, resources=resources, lineage=lineage))


def test_planner_refuses_fan_in_fan_out_conversions_like_the_worker(tmp_path):
    from test_graph_planner import fixture, revise

    from tuning_factory.graph_planner import NodeResources, plan_graph, snapshot_artifact
    _, project, choices, facts = fixture(tmp_path)
    store = ModelArtifactStore(tmp_path / "models")
    refs, artifacts = [], []
    for marker in ("first", "second"):
        ref, descriptor = frozen(store, peft_adapter(tmp_path, marker, fan_in_fan_out=True))
        refs.append(ref)
        artifacts.append(snapshot_artifact(store, ref, producer(ref, descriptor)))
    choices = choices.model_copy(update={"artifacts": tuple(artifacts), "node_resources": (
        NodeResources(node_id="merge", timeout_seconds=900), NodeResources(node_id="full", timeout_seconds=900))})
    for node in ({"kind": "materialize", "id": "full", "label": "Full", "model": refs[0].model_dump(),
                  "executor_profile_ids": ["p"]},
                 {"kind": "merge", "id": "merge", "label": "Merge", "models": [ref.model_dump() for ref in refs],
                  "weights": [.5, .5], "executor_profile_ids": ["p"]}):
        with pytest.raises(ValueError, match="unsupported adapter conversion variant"):
            plan_graph(revise(project, {"nodes": [node]}), choices, (facts,))


def test_staged_model_requires_one_identity_matching_its_role():
    module = runner()
    ref = ModelArtifactRef(artifact_id="1" * 64, inventory_sha256="2" * 64)
    common = dict(config_sha256="3" * 64, tokenizer_fingerprint="4" * 64)
    with pytest.raises(ValidationError):
        module.StagedModel(role="policy_base", source="artifact", artifact=ref, hf_root=ROOT,
                           storage_kind="full_model", **common)
    with pytest.raises(ValidationError):
        module.StagedModel(role="policy_base", source="hf_root", hf_root=ROOT, storage_kind="adapter",
                           config_sha256=ROOT.config_sha256, tokenizer_fingerprint=ROOT.tokenizer_fingerprint)
    with pytest.raises(ValidationError):
        module.StagedModel(role="policy_adapter", source="artifact", artifact=ref, storage_kind="full_model", **common)
    with pytest.raises(ValidationError):
        module.StagedModel(role="policy_base", source="artifact", artifact=ref, storage_kind="full_model", index=1,
                           **common)


def test_staging_and_worker_hash_each_artifact_once(tmp_path, store, sft, monkeypatch):
    from collections import Counter

    from tuning_factory import model_artifacts
    module = runner()
    sft_ref, _ = sft
    adapter_ref, adapter = frozen(store, peft_adapter(tmp_path, "dpo-parent", base=sft_ref, parents=(sft_ref,)))
    # The policy, its frozen reference and the adapter lineage all name the same full-model base.
    operation = train_operation(policy(sft_ref, adapter_ref, reference=True), stage="dpo")
    original, scans = model_artifacts._inventory, []

    def counting(directory):
        scans.append(Path(directory).absolute())
        return original(directory)

    monkeypatch.setattr(model_artifacts, "_inventory", counting)
    resolved = stage(tmp_path, store, operation, target_evidence=(evidence(sft_ref),),
                     producers=(producer(adapter_ref, adapter, stage="dpo"),))
    expected = {Path(cache_path(store, sft_ref)), Path(cache_path(store, adapter_ref))}
    assert Counter(scans) == Counter(expected)
    scans.clear()
    module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=None, inspect_targets=inspector)
    assert Counter(scans) == Counter(expected)


def test_operation_runner_uses_only_public_artifact_verification():
    import ast

    module = runner()
    tree = ast.parse(Path(module.__file__).read_text(encoding="utf-8"))
    private = [alias.name for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)
               and (node.module or "").endswith("model_artifacts") for alias in node.names if alias.name.startswith("_")]
    assert private == []
    assert not hasattr(module, "_check_load")


# -- Task 7.3: evaluate staging and earlier loader checks ----------------------------------------

def resources(profile="primary"):
    from tuning_factory.operations import OperationResources, RuntimeProfile
    return OperationResources(executor=EXECUTOR, facts=FACTS, timeout_seconds=900, runtime=RuntimeProfile(
        profile=profile, engine_version="0.9.3" if profile == "ppo" else "0.9.5", dependency_sha256="e" * 64))


def lineage(simulation=False):
    from tuning_factory.operations import OperationLineage
    return OperationLineage(project_id="project", project_revision="f" * 64, node_id="node", plan_sha256="d" * 64,
                            simulation=simulation)


def benchmark(dataset=None, **changes):
    from tuning_factory.benchmarks import BenchmarkSpec
    values = dict(dataset=dataset or DatasetRef(dataset_id="bench", version="1", sha256="c" * 64),
                  evaluator_kind="text", evaluator_version="1", ordered_sample_ids=("row:0",), max_new_tokens=16,
                  cutoff_len=128, seed=7, precision="bf16", quantization_bit=None,
                  tokenizer_fingerprint=ROOT.tokenizer_fingerprint, template="qwen", semantic_model_kind="causal_lm")
    return BenchmarkSpec(**{**values, **changes})


def staged_artifact(module, role, ref, descriptor, index=0):
    return module.StagedModel(role=role, index=index, source="artifact", artifact=ref,
                              storage_kind=descriptor.storage_kind, config_sha256=descriptor.config_sha256,
                              tokenizer_fingerprint=descriptor.tokenizer_fingerprint)


def test_evaluate_operation_stages_the_evaluated_model_roles(tmp_path, store, sft):
    from tuning_factory.operations import EvaluateOperation
    module = runner()
    sft_ref, _ = sft
    adapter_ref, _ = frozen(store, peft_adapter(tmp_path, "evaluated", base=sft_ref, parents=(sft_ref,)))
    evaluate = EvaluateOperation(model=adapter_ref, benchmark=benchmark(), resources=resources(), lineage=lineage())
    resolved = stage(tmp_path, store, evaluate)
    assert resolved.operation_kind == "evaluate"
    assert resolved.operation_sha256 == digest(canonical(evaluate.model_dump(mode="json")))
    assert [(item.role, item.index, item.artifact) for item in resolved.models] == [
        ("operation_base", 0, sft_ref), ("operation_adapter", 0, adapter_ref)]
    assert resolved.transfers == tuple(sorted((sft_ref, adapter_ref),
                                              key=lambda ref: (ref.artifact_id, ref.inventory_sha256)))
    assert [item.name for item in (tmp_path / "attempt").rglob("*")] == ["model-inputs.json"]
    verified = module.verify_model_paths(resolved, evaluate, model_cache=store.root, hf_home=None)
    assert verified.path("operation_base").as_posix() == cache_path(store, sft_ref)
    assert verified.path("operation_adapter").as_posix() == cache_path(store, adapter_ref)
    # A full model is evaluated alone, without an adapter role.
    full = EvaluateOperation(model=sft_ref, benchmark=benchmark(), resources=resources(), lineage=lineage())
    resolved_full = module.stage_model_inputs(full, store, tmp_path / "full-attempt")
    assert [(item.role, item.artifact) for item in resolved_full.models] == [("operation_base", sft_ref)]
    # A real evaluation refuses simulated models and accepts synthetic ones (Task 11.5, R1 I-2 option b: their
    # records are synthetic, so never selected); a tampered model is refused before model-inputs.json exists.
    directory, simulated = full_model(tmp_path, "simulated")
    simulated_ref = store.freeze(directory, simulated.model_copy(update={"simulation": True}))
    with pytest.raises(ValueError, match="simulation artifact is ineligible for real use"):
        module.stage_model_inputs(full.model_copy(update={"model": simulated_ref}), store, tmp_path / "simulated")
    synthetic_ref, _ = frozen(store, full_model(tmp_path, "synthetic", synthetic=True))
    staged = module.stage_model_inputs(full.model_copy(update={"model": synthetic_ref}), store, tmp_path / "synthetic")
    assert [(item.role, item.artifact) for item in staged.models] == [("operation_base", synthetic_ref)]
    (Path(cache_path(store, adapter_ref)) / "adapter_model.safetensors").write_bytes(b"tampered")
    with pytest.raises(ValueError, match="hash|inventory"):
        module.stage_model_inputs(evaluate, store, tmp_path / "tampered")
    assert not (tmp_path / "tampered" / "model-inputs.json").exists()


def test_staging_runs_full_model_loader_checks_before_any_attempt(tmp_path, store):
    redirect = canonical({"model_type": "qwen2", "hidden_size": 2, "transformers_weights": "other.safetensors"})
    redirect_ref, _ = frozen(store, full_model(tmp_path, "redirect", config=redirect))
    with pytest.raises(ValueError, match="transformers_weights"):
        stage(tmp_path, store, train_operation(policy(redirect_ref)))
    assert not (tmp_path / "attempt" / "model-inputs.json").exists()


def test_merge_and_materialize_refuse_a_prequantized_base_config_at_staging_and_worker(tmp_path, store):
    from tuning_factory.operations import MaterializeOperation, MergeOperation
    module = runner()
    config = canonical({"model_type": "qwen2", "hidden_size": 2, "quantization_config": {"load_in_4bit": True}})
    base_ref, base = frozen(store, full_model(tmp_path, "prequantized", config=config))
    first_ref, first = frozen(store, peft_adapter(tmp_path, "first", base=base_ref, parents=(base_ref,), config=config))
    second_ref, second = frozen(store, peft_adapter(tmp_path, "second", rank=3, alpha=9, base=base_ref,
                                                    parents=(base_ref,), config=config))
    merge = MergeOperation(models=(first_ref, second_ref), weights=(0.25, 0.75), resources=resources(),
                           lineage=lineage())
    materialize = MaterializeOperation(model=first_ref, resources=resources(), lineage=lineage())
    adapters = {"merge": ((first_ref, first), (second_ref, second)), "materialization": ((first_ref, first),)}
    for operation, name in ((merge, "merge"), (materialize, "materialization")):
        with pytest.raises(ValueError, match=f"^{name} requires unquantized effective base weights$"):
            stage(tmp_path, store, operation)
        assert not (tmp_path / "attempt" / "model-inputs.json").exists()
        models = (staged_artifact(module, "operation_base", base_ref, base),
                  *(staged_artifact(module, "operation_adapter", ref, descriptor, index)
                    for index, (ref, descriptor) in enumerate(adapters[name])))
        resolved = module.ResolvedModelInputs(
            operation_kind=operation.kind, operation_sha256=digest(canonical(operation.model_dump(mode="json"))),
            models=models, transfers=module._transfers(models))
        with pytest.raises(ValueError, match=f"^{name} requires unquantized effective base weights$"):
            module.verify_model_paths(resolved, operation, model_cache=store.root, hf_home=None)
