"""Reviewed text-model profiles and deterministic engine compatibility rules.

Profiles describe supported architecture paths, not proof of GPU training on every model.
Keep these rules independent of credentials and infrastructure discovery.
"""
from copy import deepcopy

from .model_hub import ARCHITECTURES, COMPOSITE_ARCHITECTURES, ModelProfile, imported_model

STAGES = {
    "pt": ("Pre-training", "text"), "sft": ("Supervised fine-tuning", "instruction"),
    "rm": ("Reward modeling", "preference"), "ppo": ("PPO", "prompt"),
    "dpo": ("DPO / ORPO / SimPO", "preference"), "kto": ("KTO", "kto"),
}
METHODS = ["lora", "qlora", "full", "freeze", "oft"]
# Size facts of a model entry, under the same keys in the catalog and model_hub.imported_model.
SIZE_FACTS = ("parameters_total", "parameters_b", "text_parameters", "embedding_parameters", "expert_parameters", "hidden_size", "num_layers",
              "moe", "tie_word_embeddings")
COMPOSITE_STAGE_REFUSALS = {
    ("qwen3_5", "kto"): ("Qwen3.5의 KTO 학습은 현재 학습 엔진(LLaMA-Factory 0.9.5·transformers 5.6.0)에서 멀티모달 위치 "
                         "계산 오류로 실패하므로 지원하지 않습니다. SFT·DPO·RM·PT를 쓰세요."),
    ("gemma4", "rm"): ("Gemma 4의 보상 모델(RM) 학습은 아직 GPU에서 값 헤드를 검증하지 않아 막아 두었습니다. "
                       "SFT·DPO·KTO·PT를 쓰세요."),
}
PPO_REFUSAL = "Qwen3.5·Gemma 4는 PPO 전용 학습 환경(transformers 4.52.4)이 지원하지 않습니다."
# Option labels as the training screen shows them (desktop/src/components/recipe-fields.ts, freeze fields).
FREEZE_SELF_ATTN = ("Qwen3.5는 층에 따라 linear_attn 또는 self_attn을 써서 '학습할 모듈'로 'Attention'·'Attention + MLP'를 "
                    "고를 수 없습니다. '전체' 또는 'MLP'를 고르세요.")
FREEZE_TIED_LM_HEAD = ("이 모델은 입력 임베딩과 LM head가 가중치를 공유해 LM head만 따로 학습할 수 없습니다. "
                       "'추가 학습 모듈'로 'LM head'·'Embedding + LM head' 대신 'Embedding' 또는 '추가 없음'을 고르세요"
                       "('Embedding'을 고르면 공유된 가중치가 함께 학습됩니다).")
# Qwen3.5 and Gemma 4 (vision/audio + text, trained on text only). Per row: repositories, architecture, template,
# safetensors total (vision/audio included), language model + lm_head parameters (from the safetensors headers),
# text_config hidden_size and num_hidden_layers, tied embeddings, MoE. Read 2026-10-30.
COMPOSITE_MODELS = [
    (("Qwen/Qwen3.5-0.8B", "Qwen/Qwen3.5-0.8B-Base"), "qwen3_5", "qwen3_5",
     873_438_784, 752_393_024, 1024, 24, True, False),
    (("Qwen/Qwen3.5-2B", "Qwen/Qwen3.5-2B-Base"), "qwen3_5", "qwen3_5",
     2_274_069_824, 1_881_825_088, 2048, 24, True, False),
    (("Qwen/Qwen3.5-4B", "Qwen/Qwen3.5-4B-Base"), "qwen3_5", "qwen3_5",
     4_659_865_088, 4_205_751_296, 2560, 32, True, False),
    (("Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-9B-Base"), "qwen3_5", "qwen3_5",
     9_653_104_368, 8_953_803_264, 4096, 32, False, False),
    (("Qwen/Qwen3.5-27B",), "qwen3_5", "qwen3_5", 27_781_427_952, 26_895_998_464, 5120, 64, False, False),
    (("Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-35B-A3B-Base"), "qwen3_5_moe", "qwen3_5",
     35_951_822_704, 34_660_610_688, 2048, 40, False, True),
    (("google/gemma-4-E2B-it", "google/gemma-4-E2B"), "gemma4", "gemma4n",
     5_123_178_051, 4_647_449_891, 1536, 35, True, False),
    (("google/gemma-4-E4B-it", "google/gemma-4-E4B"), "gemma4", "gemma4n",
     7_996_156_490, 7_518_069_034, 2560, 42, True, False),
    (("google/gemma-4-26B-A4B-it", "google/gemma-4-26B-A4B"), "gemma4", "gemma4",
     25_805_936_206, 25_233_141_790, 2816, 30, True, True),
    (("google/gemma-4-31B-it", "google/gemma-4-31B"), "gemma4", "gemma4",
     31_273_088_876, 30_697_345_340, 5376, 60, True, False),
]
# Embedding tables, which bitsandbytes leaves unquantized under QLoRA (nn.Embedding and lm_head stay bf16): vocab x
# hidden for the input embedding, again for an untied lm_head, and vocab x layers x 256 for the per-layer embedding
# table of Gemma 4 E2B/E4B (template gemma4n, text_config.hidden_size_per_layer_input 256).
COMPOSITE_VOCABULARY = {"qwen3_5": 248_320, "qwen3_5_moe": 248_320, "gemma4": 262_144}
PER_LAYER_INPUT = 256
# MoE expert weights are 3-D nn.Parameters, not nn.Linear, so bitsandbytes leaves them bf16 too: experts x 3 (gate, up,
# down) x hidden_size x moe_intermediate_size x layers, from each model's HF config.json (the spec table lacks
# moe_intermediate_size). Qwen3.5-35B-A3B: 256 experts, 512; Gemma 4 26B-A4B: 128 experts, 704.
MOE_EXPERTS = {"Qwen/Qwen3.5-35B-A3B": (256, 512), "Qwen/Qwen3.5-35B-A3B-Base": (256, 512),
               "google/gemma-4-26B-A4B": (128, 704), "google/gemma-4-26B-A4B-it": (128, 704)}


def model_catalog():
    models = []
    # (name, nominal parameters in billions, hidden_size, num_hidden_layers): parameters_b are nominal model
    # sizes; hidden_size and num_hidden_layers come from each model's config.json.
    for family, architecture, template, variants, length, gated in [
        ("Qwen", "qwen2", "qwen", [
            ("Qwen2.5-0.5B-Instruct", 0.5, 896, 24), ("Qwen2.5-1.5B-Instruct", 1.5, 1536, 28),
            ("Qwen2.5-3B-Instruct", 3, 2048, 36), ("Qwen2.5-7B-Instruct", 7, 3584, 28),
            ("Qwen2.5-14B-Instruct", 14, 5120, 48),
            ("Qwen2.5-0.5B", 0.5, 896, 24), ("Qwen2.5-1.5B", 1.5, 1536, 28), ("Qwen2.5-7B", 7, 3584, 28),
        ], 32768, False),
        ("meta-llama", "llama", "llama3", [
            ("Llama-3.1-8B-Instruct", 8, 4096, 32), ("Llama-3.2-1B-Instruct", 1, 2048, 16),
            ("Llama-3.2-3B-Instruct", 3, 3072, 28), ("Llama-3.1-8B", 8, 4096, 32),
            ("Llama-3.1-70B-Instruct", 70, 8192, 80), ("Llama-3.1-70B", 70, 8192, 80),
        ], 32768, True),
        ("mistralai", "mistral", "mistral", [("Mistral-7B-Instruct-v0.3", 7, 4096, 32)], 32768, False),
        ("google", "gemma2", "gemma", [("gemma-2-2b-it", 2, 2304, 26), ("gemma-2-9b-it", 9, 3584, 42)], 8192, True),
    ]:
        for name, size, hidden, layers in variants:
            model_id = f"{family}/{name}"
            models.append({"id": model_id, "template": template, "architecture": architecture,
                           "max_length": length, "parameters_b": size, "hidden_size": hidden, "num_layers": layers,
                           "gated": gated,
                           "gpu_verified": model_id == "Qwen/Qwen2.5-0.5B-Instruct",
                           "verified_capabilities": (["8.6", "9.0", "12.0"] if model_id == "Qwen/Qwen2.5-0.5B-Instruct"
                                                     else []),
                           "verification_scope": ("0.2.3 SFT LoRA/4-bit QLoRA synthetic H100; 0.5.0 (CUDA 12.8) SFT LoRA "
                                                  "synthetic A40, H100, RTX PRO 4000 Blackwell"
                                                  if model_id == "Qwen/Qwen2.5-0.5B-Instruct"
                                                  else "architecture profile; GPU verification pending")})
    for ids, architecture, template, total, text, hidden, layers, tied, moe in COMPOSITE_MODELS:
        _, stages, methods = ARCHITECTURES[architecture]
        vocabulary = COMPOSITE_VOCABULARY[architecture]
        embedding = (vocabulary * hidden * (1 if tied else 2)
                     + (vocabulary * layers * PER_LAYER_INPUT if template == "gemma4n" else 0))
        for model_id in ids:
            experts = MOE_EXPERTS.get(model_id)
            models.append({"id": model_id, "template": template, "architecture": architecture, "max_length": 32768,
                           "parameters_b": round(total / 1e9, 2), "parameters_total": total, "text_parameters": text,
                           "embedding_parameters": embedding, "hidden_size": hidden, "num_layers": layers,
                           "tie_word_embeddings": tied, "moe": moe,
                           **({"expert_parameters": experts[0] * 3 * hidden * experts[1] * layers} if experts else {}),
                           "gated": False, "gpu_verified": False, "verified_capabilities": [],
                           "verification_scope": "text data only; vision/audio frozen; GPU verification pending",
                           "supported_stages": list(stages), "methods": list(methods)})
    return models


def _reviewed(entry):
    """The reviewed catalog entry of the same repository and architecture as a model entry, or None."""
    reviewed = next((m for m in model_catalog() if m["id"] == entry["id"]), None)
    return reviewed if reviewed is not None and reviewed["architecture"] == entry["architecture"] else None


def with_catalog_sizes(entry):
    """A hub model entry with every size fact it lacks taken from the reviewed catalog entry of the same repository
    and architecture (profiles registered before they carried sizes; text parameters, which the hub never gives);
    the entry's own values win."""
    reviewed = _reviewed(entry)
    if reviewed is None:
        return entry
    return {**{key: reviewed[key] for key in SIZE_FACTS if key in reviewed}, **entry}


def model_entry(config):
    """The model entry of a (partial) config: its registered HF profile first, else the reviewed catalog; or None."""
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    if config.get("model_profile"):
        try:
            return with_catalog_sizes(imported_model(ModelProfile.model_validate(config["model_profile"])))
        except ValueError:
            return None
    return next((m for m in model_catalog() if m["id"] == config.get("base_model")), None)


def reviewed_model_entry(config):
    """The model entry the GPU compatibility check judges: the reviewed catalog entry (with its GPU verification) when
    the config's registered profile names a catalog repository with the same architecture, else model_entry(config).
    """
    entry = model_entry(config)
    if entry is not None and entry.get("source") == "huggingface":
        return _reviewed(entry) or entry
    return entry


def training_stages():
    return [{"id": stage, "label": label, "dataset_kind": kind, "resume_supported": stage != "ppo",
             "available": True, "engine_version": "0.9.3" if stage == "ppo" else "0.9.5",
             "reason": ("Dedicated legacy PPO runtime; checkpoint resume unsupported upstream"
                        if stage == "ppo" else "New stage/method combinations require GPU verification")}
            for stage, (label, kind) in STAGES.items()]


def compatibility(config, *, graph_reward_binding=False):
    """Inspect partial UI mappings without running pydantic recursively."""
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    errors, warnings = [], []
    model = next((m for m in model_catalog() if m["id"] == config.get("base_model")), None)
    if config.get("model_profile"):
        try:
            profile = ModelProfile.model_validate(config["model_profile"])
            if profile.repo_id != config.get("base_model") or profile.revision != config.get("model_revision"):
                errors.append("registered model identity/revision differs; inspect the model again")
            model = with_catalog_sizes(imported_model(profile))
        except ValueError:
            errors.append("registered model profile is invalid; inspect the model again")
    stage, method = config.get("training_stage", "sft"), config.get("training_method", "lora")
    options, stage_options = config.get("finetuning_options") or {}, config.get("stage_options") or {}
    evaluation = config.get("evaluation_profile") or {}
    if not model:
        errors.append("unsupported model/template combination; extend the reviewed model catalog first")
    elif config.get("template", model["template"]) != model["template"]:
        errors.append(f"model requires template {model['template']}")
    if stage not in STAGES:
        errors.append("unsupported training stage")
    if method not in METHODS:
        errors.append("unsupported training method")
    lora = method in {"lora", "qlora"}
    lora_flags = ("use_rslora", "use_dora", "pissa_init", "pissa_convert", "loraplus_lr_ratio")
    if not lora and any(options.get(key) for key in lora_flags):
        errors.append("LoRA options require LoRA or QLoRA training")
    if method == "oft" and bool(options.get("oft_rank", 0)) == bool(options.get("oft_block_size", 32)):
        errors.append("OFT requires exactly one of oft_rank or oft_block_size to be nonzero")
    if method == "freeze" and options.get("freeze_trainable_layers", 2) == 0:
        errors.append("freeze_trainable_layers must select at least one layer")
    loss = stage_options.get("pref_loss", "sigmoid")
    if options.get("pissa_init"):
        if method == "qlora":
            errors.append("PiSSA initialization on a quantized model requires a separately initialized adapter")
        if stage in {"ppo", "kto"} or (stage == "dpo" and loss not in {"orpo", "simpo"}):
            errors.append("PiSSA is incompatible with this reference-model training stage")
    if options.get("pissa_convert") and not options.get("pissa_init"):
        errors.append("PiSSA conversion requires PiSSA initialization")
    if stage == "dpo" and loss != "sigmoid" and stage_options.get("dpo_label_smoothing", 0) > 0:
        errors.append("DPO label smoothing is only supported with sigmoid loss")
    if stage == "ppo":
        if not graph_reward_binding and not stage_options.get("reward_model"):
            errors.append("reward_model is required for PPO")
        if not graph_reward_binding and not stage_options.get("reward_model_sha256"):
            errors.append("reward_model_sha256 inventory hash is required for reproducible PPO")
        if not graph_reward_binding and stage_options.get("reward_model_type", "lora") == "lora" and not lora:
            errors.append("LoRA reward models require a LoRA/QLoRA PPO policy")
        if method == "oft":
            errors.append("OFT is unavailable in the dedicated LLaMA-Factory 0.9.3 PPO runtime")
        warnings.append("PPO uses the dedicated LLaMA-Factory 0.9.3 / TRL 0.9.6 runtime; PPO resume is unsupported")
    if evaluation.get("dataset_id"):
        if stage == "rm" and evaluation.get("kind") != "reward":
            errors.append("reward models require reward evaluation; text generation evaluation is unavailable")
        if stage != "rm" and evaluation.get("kind") == "reward":
            errors.append("reward evaluation requires the RM training stage")
        if method == "oft":
            errors.append("OFT generation evaluation is blocked by the pinned engine adapter parser")
    if model:
        if stage not in model.get("supported_stages", STAGES):
            family = "qwen3_5" if model["architecture"] == "qwen3_5_moe" else model["architecture"]
            errors.append(PPO_REFUSAL if model["architecture"] in COMPOSITE_ARCHITECTURES and stage == "ppo"
                          else COMPOSITE_STAGE_REFUSALS[family, stage]
                          if model["architecture"] in COMPOSITE_ARCHITECTURES and (family, stage) in COMPOSITE_STAGE_REFUSALS
                          else f"{stage} is unavailable for this architecture/runtime profile; choose a supported stage")
        if method not in model.get("methods", METHODS):
            errors.append(f"{method} is unavailable for this architecture profile")
        if method == "freeze":
            modules = str(options.get("freeze_trainable_modules", "all")).split(",")
            if model["architecture"] in {"qwen3_5", "qwen3_5_moe"} and "self_attn" in modules:
                errors.append(FREEZE_SELF_ATTN)   # layer 0 is linear_attn: LLaMA-Factory finds no self_attn there
            if model.get("tie_word_embeddings") and "lm_head" in str(options.get("freeze_extra_modules") or "").split(","):
                errors.append(FREEZE_TIED_LM_HEAD)   # a tied lm_head is not a separate parameter
        if model.get("source") == "huggingface":
            warnings.append("Imported HF model: metadata verified, actual GPU validation pending; text data only")
        if model["gated"]:
            warnings.append("Gated model requires prior access and a pre-provisioned model cache on the GPU")
        cutoff = (config.get("hyperparameters") or {}).get("cutoff_len", 1024)
        if cutoff > model["max_length"]:
            errors.append(f"cutoff_len exceeds reviewed model limit {model['max_length']}")
        if method in {"full", "freeze"}:
            warnings.append("Full/freeze training retains full model weights and requires substantially more memory/storage")
        warnings.append("Available configuration is not GPU verification of this stage/method/model combination")
    return {"valid": not errors, "errors": errors, "warnings": warnings,
            "supported_stages": list(model.get("supported_stages", STAGES)) if model else list(STAGES),
            "methods": [m for m in (model.get("methods", METHODS) if model else METHODS)
                        if not (stage == "ppo" and m == "oft")],
            "quantization_bits": [4, 8], "model": deepcopy(model),
            "dataset_kind": STAGES.get(stage, ("", "instruction"))[1],
            "engine_version": "0.9.3" if stage == "ppo" else "0.9.5",
            "resume_supported": (stage != "ppo" and method != "oft"
                                 and not (stage == "rm" and method in {"full", "freeze"}))}
