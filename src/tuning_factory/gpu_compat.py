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
        if (not re.fullmatch(r"\d+\.\d+", capability) or not memory.isdigit()
                or not re.fullmatch(r"\d+(\.\d+)*", driver)):
            raise ValueError("unexpected nvidia-smi GPU query output")
        gpus.append({"uuid": uuid, "name": name, "capability": capability, "memory_mib": int(memory),
                     "driver": driver})
    if not gpus:
        raise ValueError("no NVIDIA GPU reported by nvidia-smi")
    return gpus


def capability_supported(capability, sass, ptx=()):
    """CUDA binary compatibility: same-major machine code with a lower or equal minor, or PTX at or below the
    device. desktop_runtime.ARCH_PROBE applies the same rule inside the installed runtime."""
    major, minor = version_tuple(capability)
    if any(version_tuple(code)[0] == major and version_tuple(code)[1] <= minor for code in sass):
        return True
    return any(version_tuple(code) <= (major, minor) for code in ptx)


def driver_at_least(driver, minimum):
    return version_tuple(driver) >= version_tuple(minimum)
