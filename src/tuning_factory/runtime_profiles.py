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


# Model-operation (merge/materialize) library pins; equal to containers/gpu-requirements.lock.
MODEL_OPS_PINS = MappingProxyType({"torch": "2.7.1", "peft": "0.18.1", "transformers": "5.6.0",
                                   "safetensors": "0.8.0"})
# Numerical acceptance environment only; never a GPU runtime.
MODEL_OPS_CPU_LOCK = "containers/model-ops-cpu-requirements.lock"
