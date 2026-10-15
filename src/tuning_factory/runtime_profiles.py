"""Pinned independent engine environments; never bypass upstream version checks."""


def engine_profile(stage="sft"):
    if stage not in {"pt", "sft", "rm", "ppo", "dpo", "kto"}:
        raise ValueError("unsupported training stage")
    if stage == "ppo":
        return {"profile": "ppo", "engine_version": "0.9.3",
                "lock_file": "containers/gpu-ppo-requirements.lock"}
    return {"profile": "primary", "engine_version": "0.9.5",
            "lock_file": "containers/gpu-requirements.lock"}
