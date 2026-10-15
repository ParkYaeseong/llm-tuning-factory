"""Reviewed text-model profiles and deterministic engine compatibility rules.

Profiles describe supported architecture paths, not proof of GPU training on every model.
Keep these rules independent of credentials and infrastructure discovery.
"""
from copy import deepcopy

from .model_hub import ModelProfile, imported_model

STAGES = {
    "pt": ("Pre-training", "text"), "sft": ("Supervised fine-tuning", "instruction"),
    "rm": ("Reward modeling", "preference"), "ppo": ("PPO", "prompt"),
    "dpo": ("DPO / ORPO / SimPO", "preference"), "kto": ("KTO", "kto"),
}
METHODS = ["lora", "qlora", "full", "freeze", "oft"]


def model_catalog():
    models = []
    for family, architecture, template, variants, length, gated in [
        ("Qwen", "qwen2", "qwen", [
            ("Qwen2.5-0.5B-Instruct", 0.5), ("Qwen2.5-1.5B-Instruct", 1.5),
            ("Qwen2.5-3B-Instruct", 3), ("Qwen2.5-7B-Instruct", 7), ("Qwen2.5-14B-Instruct", 14),
            ("Qwen2.5-0.5B", 0.5), ("Qwen2.5-1.5B", 1.5), ("Qwen2.5-7B", 7),
        ], 32768, False),
        ("meta-llama", "llama", "llama3", [
            ("Llama-3.1-8B-Instruct", 8), ("Llama-3.2-1B-Instruct", 1),
            ("Llama-3.2-3B-Instruct", 3), ("Llama-3.1-8B", 8),
        ], 32768, True),
        ("mistralai", "mistral", "mistral", [("Mistral-7B-Instruct-v0.3", 7)], 32768, False),
        ("google", "gemma2", "gemma", [("gemma-2-2b-it", 2), ("gemma-2-9b-it", 9)], 8192, True),
    ]:
        for name, size in variants:
            model_id = f"{family}/{name}"
            models.append({"id": model_id, "template": template, "architecture": architecture,
                           "max_length": length, "parameters_b": size, "gated": gated,
                           "gpu_verified": model_id == "Qwen/Qwen2.5-0.5B-Instruct",
                           "verification_scope": ("0.2.3 SFT LoRA/4-bit QLoRA synthetic H100 only"
                                                  if model_id == "Qwen/Qwen2.5-0.5B-Instruct"
                                                  else "architecture profile; GPU verification pending")})
    return models


def training_stages():
    return [{"id": stage, "label": label, "dataset_kind": kind, "resume_supported": stage != "ppo",
             "available": True, "engine_version": "0.9.3" if stage == "ppo" else "0.9.5",
             "reason": ("Dedicated legacy PPO runtime; checkpoint resume unsupported upstream"
                        if stage == "ppo" else "New stage/method combinations require GPU verification")}
            for stage, (label, kind) in STAGES.items()]


def compatibility(config):
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
            model = imported_model(profile)
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
        if not stage_options.get("reward_model"):
            errors.append("reward_model is required for PPO")
        if not stage_options.get("reward_model_sha256"):
            errors.append("reward_model_sha256 inventory hash is required for reproducible PPO")
        if stage_options.get("reward_model_type", "lora") == "lora" and not lora:
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
            errors.append(f"{stage} is unavailable for this architecture/runtime profile; choose a supported stage")
        if method not in model.get("methods", METHODS):
            errors.append(f"{method} is unavailable for this architecture profile")
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
