"""GPU and training-runtime compatibility: nvidia-smi facts, architecture and driver rules, memory estimates and
block/warn findings. Pure functions plus a small JSON observation store; no SSH, provider or MLflow calls."""
import re

# One fixed read-only query; compute_cap needs a driver from 2021 or later.
GPU_QUERY = ("nvidia-smi", "--query-gpu=uuid,name,compute_cap,memory.total,driver_version",
             "--format=csv,noheader,nounits")


def version_tuple(text):
    return tuple(int(part) for part in str(text).split("."))


def parse_gpu_facts(text):
    """[{uuid, name, capability, memory_mib, driver}] from GPU_QUERY output; anything else is refused."""
    gpus = []
    for line in text.splitlines():
        if not line.strip():
            continue
        fields = [field.strip() for field in line.split(",")]
        if len(fields) != 5:
            raise ValueError("unexpected nvidia-smi GPU query output")
        uuid, name, capability, memory, driver = fields
        if (not re.fullmatch(r"GPU-[0-9A-Fa-f-]+", uuid) or not name or len(name) > 128 or not name.isprintable()
                or not re.fullmatch(r"\d+\.\d+", capability) or not memory.isdigit()
                or not re.fullmatch(r"\d+(\.\d+)*", driver)):
            raise ValueError("unexpected nvidia-smi GPU query output")
        gpus.append({"uuid": uuid, "name": name, "capability": capability, "memory_mib": int(memory),
                     "driver": driver})
    if not gpus:
        raise ValueError("no NVIDIA GPU reported by nvidia-smi")
    return gpus


def capability_supported(capability, sass, ptx=()):
    """CUDA binary compatibility: same-major machine code with a lower or equal minor, or PTX at or below the
    device. desktop_runtime.ARCH_PROBE applies the same rule inside the installed runtime. The capability and
    every sass/ptx entry are "major.minor" strings (for example "8.6"); callers convert torch's sm_XY/compute_XY
    names before calling."""
    major, minor = version_tuple(capability)
    if any(version_tuple(code)[0] == major and version_tuple(code)[1] <= minor for code in sass):
        return True
    return any(version_tuple(code) <= (major, minor) for code in ptx)


def driver_at_least(driver, minimum):
    left, right = version_tuple(driver), version_tuple(minimum)
    width = max(len(left), len(right))
    return left + (0,) * (width - len(left)) >= right + (0,) * (width - len(right))


GIB = 1024 ** 3
OVERHEAD_GIB = 1.5                  # CUDA context and framework buffers
BYTES_PER_PARAMETER = {"bf16": 2.0, "fp16": 2.0, 8: 1.0, 4: 0.55}   # 4-bit includes quantization constants
FULL_STATE_BYTES = 14.0             # bf16 gradient 2 + Adam fp32 moments 8 + fp32 master 4, beside the weight
ADAPTER_FRACTION, ADAPTER_STATE_BYTES = 0.01, 16.0   # adapter weight, gradient and Adam states (generous)


def estimate_memory(model, config):
    """Rough single-GPU training memory in GiB with gradient checkpointing; None without catalog sizes.

    {"weights_gib": weights alone, "total_gib": weights + training state + activations + overhead}."""
    if not model or not all(model.get(key) for key in ("parameters_b", "hidden_size", "num_layers")):
        return None
    parameters = float(model["parameters_b"]) * 1e9
    layers = int(model["num_layers"])
    method = config.get("training_method", "lora")
    hyper = config.get("hyperparameters") or {}
    if method == "qlora":
        per_parameter = BYTES_PER_PARAMETER[int(config.get("quantization_bit", 4))]
    else:
        per_parameter = BYTES_PER_PARAMETER[hyper.get("precision", "bf16")]
    weights = parameters * per_parameter
    if method == "full":
        state = parameters * FULL_STATE_BYTES
    elif method == "freeze":
        trainable = min(abs(int((config.get("finetuning_options") or {}).get("freeze_trainable_layers", 2))), layers)
        state = parameters * trainable / layers * FULL_STATE_BYTES
    else:   # lora, qlora, oft
        state = parameters * ADAPTER_FRACTION * ADAPTER_STATE_BYTES
    activations = (int(hyper.get("per_device_train_batch_size", 1)) * int(hyper.get("cutoff_len", 1024))
                   * int(model["hidden_size"]) * (2 * layers + 34))
    return {"weights_gib": weights / GIB, "total_gib": (weights + state + activations) / GIB + OVERHEAD_GIB}
