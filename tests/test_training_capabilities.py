import json
import sys
import time
from pathlib import Path
from types import SimpleNamespace

import pytest
import yaml
from pydantic import ValidationError

from tuning_factory.config import AIConfig, load_config
from tuning_factory.registry import canonical, digest
from tuning_factory.training import CanceledError, LlamaFactoryBackend


def configuration(**changes):
    return AIConfig.model_validate(load_config("configs/ai/paper_extraction.yaml").model_dump() | changes)


@pytest.mark.parametrize("stage", ["pt", "sft", "rm", "dpo", "kto"])
@pytest.mark.parametrize("method", ["lora", "qlora", "full", "freeze", "oft"])
def test_stage_method_translation(stage, method):
    config = configuration(training_stage=stage, training_method=method)
    plan = LlamaFactoryBackend.translate(config)
    assert plan["stage"] == stage
    assert plan["finetuning_type"] == ("lora" if method == "qlora" else method)
    assert ("quantization_bit" in plan) == (method == "qlora")
    assert ("lora_rank" in plan) == (method in {"lora", "qlora"})
    assert "save_safetensors" not in plan


def test_capabilities_explain_model_rules_and_engine_runtime_limits():
    from tuning_factory.capabilities import compatibility, model_catalog, training_stages
    models = model_catalog()
    assert any(model["architecture"] == "mistral" for model in models)
    assert {stage["id"] for stage in training_stages()} == {"pt", "sft", "rm", "ppo", "dpo", "kto"}
    assert compatibility(configuration().model_dump())["valid"]
    result = compatibility(configuration().model_dump() | {"base_model": "unknown/model"})
    assert not result["valid"] and any("catalog" in error for error in result["errors"])
    ppo = configuration().model_dump() | {"training_stage": "ppo"}
    result = compatibility(ppo)
    assert not result["valid"] and any("reward_model" in error for error in result["errors"])
    assert any("0.9.3" in warning for warning in result["warnings"])


@pytest.mark.parametrize("loss", ["sigmoid", "hinge", "ipo", "kto_pair", "orpo", "simpo"])
def test_all_preference_losses_translate(loss):
    plan = LlamaFactoryBackend.translate(configuration(training_stage="dpo", stage_options={"pref_loss": loss}))
    assert plan["pref_loss"] == loss and plan["pref_beta"] == 0.1


@pytest.mark.parametrize("method", ["lora", "qlora", "full", "freeze"])
def test_ppo_uses_typed_preprovisioned_reward_and_cannot_resume(tmp_path, method):
    config = configuration(training_stage="ppo", training_method=method, stage_options={
        "reward_model": "/workspace/rewards/approved", "reward_model_type": "full", "reward_model_sha256": "a" * 64})
    plan = LlamaFactoryBackend.translate(config)
    assert plan["stage"] == "ppo" and plan["reward_model"] == "/workspace/rewards/approved"
    assert plan["reward_model_type"] == "full" and "reward_model_sha256" not in plan
    assert "pref_loss" not in plan
    (tmp_path / "config.json").write_text(config.model_dump_json())
    checkpoint = tmp_path / "output/checkpoint-1"
    checkpoint.mkdir(parents=True)
    with pytest.raises(ValueError, match="PPO checkpoint resume"):
        LlamaFactoryBackend().resume(tmp_path, checkpoint, 1)


@pytest.mark.parametrize("changes,reason", [
    ({"training_method": "full", "finetuning_options": {"use_dora": True}}, "LoRA"),
    ({"training_method": "qlora", "finetuning_options": {"pissa_init": True}}, "PiSSA"),
    ({"training_stage": "kto", "finetuning_options": {"pissa_init": True}}, "PiSSA"),
    ({"training_stage": "dpo", "stage_options": {"pref_loss": "orpo", "dpo_label_smoothing": 0.1}}, "smoothing"),
    ({"training_method": "oft", "evaluation_profile": {"kind": "classification", "dataset_id": "val", "dataset_version": "1"}}, "OFT"),
    ({"training_stage": "rm", "evaluation_profile": {"kind": "classification", "dataset_id": "val", "dataset_version": "1"}}, "reward"),
])
def test_invalid_combinations_rejected_at_configuration_boundary(changes, reason):
    with pytest.raises(ValidationError, match=reason):
        configuration(**changes)


def test_parameter_mapping_is_typed_and_only_relevant_options_emitted():
    config = configuration(finetuning_options={"lora_alpha": 32, "lora_dropout": 0.1, "use_rslora": True,
                                               "loraplus_lr_ratio": 16})
    plan = LlamaFactoryBackend.translate(config)
    assert plan["lora_alpha"] == 32 and plan["use_rslora"] is True
    assert "oft_rank" not in plan and "pref_loss" not in plan and "freeze_trainable_layers" not in plan
    with pytest.raises(ValidationError):
        configuration(finetuning_options={"trust_remote_code": True})
    with pytest.raises(ValidationError):
        configuration(stage_options={"reward_model": "https://secret@example.com/api"})


def test_oft_targets_match_upstream_loader_and_safe_block_settings():
    plan = LlamaFactoryBackend.translate(configuration(training_method="oft", finetuning_options={
        "oft_rank": 8, "oft_block_size": 0, "oft_target": "q_proj,v_proj"}))
    assert plan["oft_target"] == plan["lora_target"] == "q_proj,v_proj"
    with pytest.raises(ValidationError, match="OFT"):
        configuration(training_method="oft", finetuning_options={"oft_rank": 8, "oft_block_size": 32})


@pytest.mark.parametrize("method", ["full", "freeze"])
def test_final_full_weight_shards_are_complete_and_confined(tmp_path, method):
    config = configuration(training_method=method)
    output = tmp_path / "output"
    output.mkdir()
    (output / "config.json").write_text('{}')
    index = output / "model.safetensors.index.json"
    index.write_text(json.dumps({"weight_map": {"a": "model-00001-of-00002.safetensors",
                                              "b": "model-00002-of-00002.safetensors"}}))
    (output / "model-00001-of-00002.safetensors").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="shard"):
        LlamaFactoryBackend().validate_outputs(tmp_path, config)
    (output / "model-00002-of-00002.safetensors").write_bytes(b"fixture")
    LlamaFactoryBackend().validate_outputs(tmp_path, config)
    index.write_text(json.dumps({"weight_map": {"a": "../outside.safetensors"}}))
    with pytest.raises(ValueError, match="shard"):
        LlamaFactoryBackend().validate_outputs(tmp_path, config)


def test_reward_outputs_require_value_head_and_adapter_type(tmp_path):
    config = configuration(training_stage="rm")
    output = tmp_path / "output"
    output.mkdir()
    (output / "adapter_config.json").write_text('{"peft_type":"LORA"}')
    (output / "adapter_model.safetensors").write_bytes(b"fixture")
    with pytest.raises(ValueError, match="value head"):
        LlamaFactoryBackend().validate_outputs(tmp_path, config)
    (output / "value_head.safetensors").write_bytes(b"fixture")
    LlamaFactoryBackend().validate_outputs(tmp_path, config)
    (output / "adapter_config.json").write_text('{"peft_type":"OFT"}')
    with pytest.raises(ValueError, match="adapter type"):
        LlamaFactoryBackend().validate_outputs(tmp_path, config)


def test_full_checkpoint_and_prediction_use_final_model(tmp_path):
    config = configuration(training_method="full")
    class Backend(LlamaFactoryBackend):
        def train(self, directory, timeout, canceled=lambda: False):
            pass

        def run_config(self, directory, timeout, canceled, filename, log_name):
            plan = yaml.safe_load((directory / filename).read_text())
            assert plan["model_name_or_path"] == "./output"
            assert plan["finetuning_type"] == "full" and "adapter_name_or_path" not in plan
            (directory / "prediction").mkdir()
            (directory / "prediction/generated_predictions.jsonl").write_text('{"predict":"a","label":"a"}\n')
    backend = Backend()
    backend.prepare(config, tmp_path, b'[{"instruction":"a","output":"b"}]')
    checkpoint = tmp_path / "output/checkpoint-1"
    checkpoint.mkdir(parents=True)
    for name in ("trainer_state.json", "config.json"):
        (checkpoint / name).write_text('{"global_step":1}')
    for name in ("model.safetensors", "optimizer.pt", "scheduler.pt", "rng_state.pth"):
        (checkpoint / name).write_bytes(b"fixture")
    backend.resume(tmp_path, checkpoint, 1)
    backend.predict(config, tmp_path, "abc", 1)


def test_explicit_8bit_quantization_requires_8bit_confirmation(tmp_path):
    config = configuration(training_method="qlora", quantization_bit=8)
    assert LlamaFactoryBackend.translate(config)["quantization_bit"] == 8
    (tmp_path / "train.log").write_text("Quantizing model to 4 bit with bitsandbytes.")
    with pytest.raises(ValueError, match="8-bit"):
        LlamaFactoryBackend().validate_training_result(tmp_path, config)


def test_cancellation_does_not_wait_for_training_when_taskkill_is_denied(tmp_path, monkeypatch):
    import tuning_factory.training as training
    if training.os.name != "nt":
        pytest.skip("Windows taskkill fallback")
    monkeypatch.setattr(training.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(returncode=1))
    backend = LlamaFactoryBackend(command=[sys.executable, "-c", "import time;time.sleep(2)"])
    start = time.monotonic()
    with pytest.raises(CanceledError):
        backend.train(tmp_path, 3, canceled=lambda: time.monotonic() - start > 0.1)
    assert time.monotonic() - start < 1.5


@pytest.mark.parametrize("method", ["lora", "full", "freeze"])
def test_reward_evaluation_uses_native_reward_stage_and_finite_metrics(tmp_path, method):
    config = configuration(training_stage="rm", training_method=method, evaluation_profile={
        "kind": "reward", "dataset_id": "val", "dataset_version": "1"})
    class Backend(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            plan = yaml.safe_load((directory / filename).read_text())
            assert plan["stage"] == "rm" and plan["do_eval"] and not plan["do_train"]
            assert "predict_with_generate" not in plan
            assert ("adapter_name_or_path" in plan) == (method == "lora")
            (directory / "reward-evaluation").mkdir(exist_ok=True)
            (directory / "reward-evaluation/all_results.json").write_text('{"eval_accuracy":0.75,"eval_loss":0.4}')
    Backend().evaluate_reward(config, tmp_path, "f" * 64, 1)
    result = json.loads((tmp_path / "output/reward_metrics.json").read_text())
    assert result["metrics"] == {"eval_accuracy": 0.75, "eval_loss": 0.4}
    assert result["validation_sha256"] == "f" * 64
    assert result["source"] == "llamafactory-native-reward-evaluation"


def test_reward_evaluation_rejects_generation_policy_stage():
    with pytest.raises(ValidationError, match="reward evaluation requires"):
        configuration(evaluation_profile={"kind": "reward", "dataset_id": "val", "dataset_version": "1"})


@pytest.mark.parametrize("facts,reason", [
    ({"cuda_available": False, "gpu_count": 0, "bf16_supported": False, "model_type": "qwen2"}, "CUDA"),
    ({"cuda_available": True, "gpu_count": 1, "bf16_supported": False, "model_type": "qwen2"}, "BF16"),
    ({"cuda_available": True, "gpu_count": 1, "bf16_supported": True, "model_type": "unknown"}, "architecture"),
])
def test_worker_device_preflight_rejects_observed_incompatibility(facts, reason):
    from tuning_factory.worker import validate_runtime_facts
    with pytest.raises(ValueError, match=reason):
        validate_runtime_facts(configuration(), facts)


def test_worker_device_preflight_accepts_supported_gpu_and_pinned_architecture():
    from tuning_factory.worker import validate_runtime_facts
    facts = {"cuda_available": True, "gpu_count": 1, "bf16_supported": True, "model_type": "qwen2"}
    validate_runtime_facts(configuration(), facts)


@pytest.mark.parametrize("method", ["full", "freeze"])
def test_reward_full_checkpoint_resume_does_not_silently_ignore_weights(tmp_path, method):
    from tuning_factory.capabilities import compatibility
    config = configuration(training_stage="rm", training_method=method)
    assert compatibility(config)["resume_supported"] is False
    (tmp_path / "config.json").write_text(config.model_dump_json())
    checkpoint = tmp_path / "output/checkpoint-1"
    checkpoint.mkdir(parents=True)
    with pytest.raises(ValueError, match="reward model checkpoint resume"):
        LlamaFactoryBackend().resume(tmp_path, checkpoint, 1)


@pytest.mark.parametrize("thresholds", [{"eval_loss": 0.1}, {"eval_accuracy": -0.1}, {"eval_accuracy": 1.1}])
def test_reward_quality_threshold_is_bounded_minimum_accuracy(thresholds):
    with pytest.raises(ValidationError, match="reward thresholds"):
        configuration(training_stage="rm", evaluation_profile={
            "kind": "reward", "dataset_id": "val", "dataset_version": "1", "thresholds": thresholds})


def test_artifact_collection_hashes_large_model_files_without_reading_them_whole(tmp_path, monkeypatch):
    artifact = tmp_path / "model.safetensors"
    artifact.write_bytes(b"streamed fixture")
    monkeypatch.setattr(Path, "read_bytes", lambda _: (_ for _ in ()).throw(AssertionError("whole file read")))
    assert LlamaFactoryBackend().collect_artifacts(tmp_path)["model.safetensors"] == digest(b"streamed fixture")


@pytest.mark.parametrize("reward_type,metadata,reason", [
    ("lora", {"peft_type": "LORA", "base_model_name_or_path": "other/model"}, "base model"),
    ("lora", {"peft_type": "OFT", "base_model_name_or_path": "Qwen/Qwen2.5-0.5B-Instruct"}, "adapter type"),
    ("full", {"model_type": "llama"}, "architecture"),
])
def test_reward_preflight_rejects_incompatible_pinned_reward_inventory(tmp_path, monkeypatch,
                                                                   reward_type, metadata, reason):
    import tuning_factory.training as training
    reward = tmp_path / "reward"
    reward.mkdir()
    if reward_type == "lora":
        (reward / "adapter_config.json").write_text(json.dumps(metadata))
        (reward / "adapter_model.safetensors").write_bytes(b"fixture")
    else:
        (reward / "config.json").write_text(json.dumps(metadata))
        (reward / "model.safetensors").write_bytes(b"fixture")
    (reward / "value_head.safetensors").write_bytes(b"fixture")
    inventory = {path.name: digest(path.read_bytes()) for path in reward.iterdir()}
    config = configuration(training_stage="ppo", stage_options={"reward_model": "/workspace/reward",
        "reward_model_type": reward_type, "reward_model_sha256": digest(canonical(inventory))})
    monkeypatch.setattr(training, "Path", lambda value: reward if value == "/workspace/reward" else Path(value))
    with pytest.raises(ValueError, match=reason):
        training.LlamaFactoryBackend.verify_reward_model(config)


def test_catalog_records_sizes_for_memory_estimates_and_adds_llama_70b():
    from tuning_factory.capabilities import model_catalog
    models = {model["id"]: model for model in model_catalog()}
    assert all(model["hidden_size"] > 0 and model["num_layers"] > 0 for model in models.values())
    qwen = models["Qwen/Qwen2.5-0.5B-Instruct"]
    assert (qwen["hidden_size"], qwen["num_layers"], qwen["gpu_verified"]) == (896, 24, True)
    for name in ("meta-llama/Llama-3.1-70B-Instruct", "meta-llama/Llama-3.1-70B"):
        model = models[name]
        assert (model["template"], model["architecture"], model["gated"]) == ("llama3", "llama", True)
        assert (model["parameters_b"], model["hidden_size"], model["num_layers"]) == (70, 8192, 80)
        assert model["gpu_verified"] is False


def test_catalog_lists_the_verified_gpu_generations():
    from tuning_factory.capabilities import model_catalog
    models = {model["id"]: model for model in model_catalog()}
    assert all("verified_capabilities" in model for model in models.values())
    qwen = models["Qwen/Qwen2.5-0.5B-Instruct"]
    # 0.5.0 GPU acceptance (2026-10-30): SFT LoRA on A40, H100 and RTX PRO 4000 Blackwell.
    assert qwen["verified_capabilities"] == ["8.6", "9.0", "12.0"]
    assert "0.5.0" in qwen["verification_scope"] and "A40" in qwen["verification_scope"]
    assert all(model["verified_capabilities"] == [] for name, model in models.items()
               if name != "Qwen/Qwen2.5-0.5B-Instruct")


def test_catalog_lists_qwen35_and_gemma4_with_sizes_and_text_scope():
    from tuning_factory.capabilities import model_catalog
    models = {model["id"]: model for model in model_catalog()}
    composite = [m for m in models.values() if m["architecture"] in {"qwen3_5", "qwen3_5_moe", "gemma4"}]
    assert len(composite) == 19 and "Qwen/Qwen3.5-27B-Base" not in models
    assert all(not m["gated"] and not m["gpu_verified"] and m["verified_capabilities"] == []
               and 0 < m["text_parameters"] < m["parameters_total"] and m["max_length"] == 32768
               and m["supported_stages"] == ["pt", "sft", "dpo", "kto"] and "oft" not in m["methods"]
               for m in composite)
    small = models["Qwen/Qwen3.5-0.8B"]
    assert (small["template"], small["parameters_total"], small["text_parameters"], small["hidden_size"],
            small["num_layers"], small["tie_word_embeddings"], small["moe"]) == (
        "qwen3_5_nothink", 873_438_784, 752_393_024, 1024, 24, True, False)
    assert models["Qwen/Qwen3.5-35B-A3B-Base"]["architecture"] == "qwen3_5_moe" and models["Qwen/Qwen3.5-35B-A3B"]["moe"]
    assert not models["Qwen/Qwen3.5-9B"]["tie_word_embeddings"]
    assert {models["google/gemma-4-" + n]["template"] for n in ("E2B", "E2B-it", "E4B", "E4B-it")} == {"gemma4n"}
    assert {models["google/gemma-4-" + n]["template"] for n in ("26B-A4B", "26B-A4B-it", "31B", "31B-it")} == {"gemma4"}
    assert models["google/gemma-4-26B-A4B-it"]["moe"] and models["google/gemma-4-31B"]["parameters_b"] == 31.27


QWEN35 = {"base_model": "Qwen/Qwen3.5-0.8B", "template": "qwen3_5_nothink"}


@pytest.mark.parametrize("change,message", [
    ({"training_stage": "rm"}, "값 헤드"), ({"training_stage": "ppo"}, "PPO 전용 학습 환경"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_trainable_modules": "self_attn"}}, "linear_attn"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_trainable_modules": "self_attn,mlp"}}, "linear_attn"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_extra_modules": "embed_tokens,lm_head"}}, "lm_head"),
    ({"training_method": "oft"}, "oft is unavailable")])
def test_composite_models_refuse_unverified_stages_and_missing_modules(change, message):
    from tuning_factory.capabilities import compatibility
    result = compatibility({**QWEN35, **change})
    assert not result["valid"] and any(message in error for error in result["errors"])


@pytest.mark.parametrize("stage", ["pt", "sft", "dpo", "kto"])
def test_composite_models_allow_text_stages_and_existing_freeze_modules(stage):
    from tuning_factory.capabilities import compatibility
    assert compatibility({**QWEN35, "training_stage": stage})["valid"]
    assert compatibility({**QWEN35, "training_method": "freeze", "finetuning_options": {
        "freeze_trainable_modules": "mlp", "freeze_extra_modules": "embed_tokens"}})["valid"]
    assert compatibility({"base_model": "Qwen/Qwen3.5-9B", "template": "qwen3_5_nothink", "training_method": "freeze",
                          "finetuning_options": {"freeze_extra_modules": "lm_head"}})["valid"]   # untied
    assert compatibility({"base_model": "google/gemma-4-E2B-it", "template": "gemma4n", "training_method": "freeze",
                          "finetuning_options": {"freeze_trainable_modules": "self_attn"}})["valid"]


def test_a_hub_registration_without_size_facts_still_refuses_a_tied_lm_head():
    from tuning_factory.capabilities import compatibility
    from tuning_factory.model_hub import ModelProfile
    # Registered from the hub before profiles carried tie_word_embeddings: the reviewed catalog knows it is tied.
    profile = ModelProfile(repo_id="Qwen/Qwen3.5-0.8B", revision="a" * 40, config_sha256="c" * 64,
                           architecture="qwen3_5", template="qwen3_5_nothink", hidden_layers=24).model_dump()
    config = {**QWEN35, "model_revision": "a" * 40, "model_profile": profile, "training_method": "freeze",
              "finetuning_options": {"freeze_extra_modules": "lm_head"}}
    result = compatibility(config)
    assert not result["valid"] and any("lm_head" in error for error in result["errors"])


FROZEN = ("freeze_vision_tower", "freeze_multi_modal_projector", "freeze_language_model")


def test_composite_training_freezes_vision_and_reasoning_templates_turn_thinking_off():
    qwen = LlamaFactoryBackend.translate(configuration(base_model="Qwen/Qwen3.5-0.8B", template="qwen3_5_nothink"))
    assert tuple(qwen[key] for key in FROZEN) == (True, True, False) and "enable_thinking" not in qwen
    gemma = LlamaFactoryBackend.translate(configuration(base_model="google/gemma-4-E2B-it", template="gemma4n"))
    assert gemma["enable_thinking"] is False and gemma["freeze_vision_tower"] is True
    legacy = LlamaFactoryBackend.translate(load_config("configs/ai/paper_extraction.yaml"))
    assert not {*FROZEN, "enable_thinking"} & set(legacy)


def test_prediction_plans_turn_thinking_off_only_for_reasoning_templates():
    def plan(template):
        benchmark = SimpleNamespace(semantic_model_kind="causal_lm", generation_mode="greedy", precision="bf16",
                                    template=template, max_new_tokens=8, cutoff_len=64, seed=1, quantization_bit=None)
        return LlamaFactoryBackend.prediction_plan(benchmark, model_path="/m", adapter_path=None, max_samples=1)
    assert all(plan(name)["enable_thinking"] is False for name in ("qwen3_5", "qwen3_6", "gemma4", "gemma4n"))
    assert "enable_thinking" not in plan("qwen") and "enable_thinking" not in plan("qwen3_5_nothink")


def test_native_prediction_of_a_reasoning_template_turns_thinking_off(tmp_path):
    config = configuration(base_model="google/gemma-4-E2B-it", template="gemma4n")
    plans = []

    class Backend(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            plans.append(yaml.safe_load((directory / filename).read_text()))
            (directory / "prediction").mkdir()
            (directory / "prediction/generated_predictions.jsonl").write_text('{"predict":"a","label":"a"}\n')
    backend = Backend()
    backend.prepare(config, tmp_path, b'[{"instruction":"a","output":"b"}]')
    backend.predict(config, tmp_path, "abc", 1)
    assert plans[-1]["enable_thinking"] is False and "freeze_vision_tower" not in plans[-1]
