"""Pinned independent engine environments; never bypass upstream version checks."""
from types import MappingProxyType


def engine_profile(stage="sft"):
    if stage not in {"pt", "sft", "rm", "ppo", "dpo", "kto"}:
        raise ValueError("unsupported training stage")
    if stage == "ppo":
        return {"profile": "ppo", "engine_version": "0.9.3",
                "lock_file": "containers/gpu-ppo-requirements.lock"}
    return {"profile": "primary", "engine_version": "0.9.5",
            "lock_file": "containers/gpu-requirements.lock"}


# Model-operation (merge/materialize) library pins; base versions (without +cu128) equal containers/gpu-requirements.lock.
MODEL_OPS_PINS = MappingProxyType({"torch": "2.7.1", "peft": "0.18.1", "transformers": "5.6.0",
                                   "safetensors": "0.8.0"})
# Numerical acceptance environment only; never a GPU runtime.
MODEL_OPS_CPU_LOCK = "containers/model-ops-cpu-requirements.lock"
MODEL_OPS_RUNTIME = "model operation requires the primary runtime with pinned PEFT 0.18.1"


def operation_profile(kind, profile):
    """engine_profile-shaped runtime of one v2 operation; merge/materialize also carry MODEL_OPS_PINS."""
    if kind == "select":
        raise ValueError("local selection never dispatches to a GPU worker")
    if kind not in {"train", "evaluate", "merge", "materialize"}:
        raise ValueError("unsupported operation kind")
    if profile not in {"primary", "ppo"}:
        raise ValueError("unsupported runtime profile")
    details = engine_profile("ppo" if profile == "ppo" else "sft")
    if kind in {"merge", "materialize"}:
        if profile != "primary":
            raise ValueError(MODEL_OPS_RUNTIME)
        details["model_ops_pins"] = dict(MODEL_OPS_PINS)
    return details


def model_ops_pin_drift():
    """Sorted names of MODEL_OPS_PINS whose installed version differs or is missing; imports nothing heavy."""
    from importlib import metadata
    drift = []
    for name, pinned in MODEL_OPS_PINS.items():
        try:
            installed = metadata.version(name).split("+")[0]
        except metadata.PackageNotFoundError:
            installed = None
        if installed != pinned:
            drift.append(name)
    return tuple(sorted(drift))


def model_ops_runtime():
    """Import the pinned model-operation libraries lazily; refuse a missing or drifted runtime."""
    from importlib import metadata
    try:
        import peft
        import safetensors
        import torch
        import transformers
    except ImportError as exc:
        raise RuntimeError("model-operation runtime is missing; use the pinned primary or CPU acceptance "
                           "runtime") from exc
    drift = sorted(name for name, pinned in MODEL_OPS_PINS.items()
                   if metadata.version(name).split("+")[0] != pinned)
    if drift:
        raise RuntimeError(f"model-operation runtime differs from pinned versions: {', '.join(drift)}")
    return torch, peft, safetensors, transformers
