"""GPU and training-runtime compatibility: nvidia-smi facts, architecture and driver rules, memory estimates and
block/warn findings. Pure functions plus a small JSON observation store; no SSH, provider or MLflow calls."""
import json
import math
import os
import re
import tempfile
import time
from pathlib import Path

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


STALE_SECONDS = 24 * 3600
WEIGHTS_BLOCK_RATIO, TOTAL_WARN_RATIO = 0.95, 0.90
OBSERVATION_KEY = re.compile(r"(runpod|server)-[A-Za-z0-9_-]{1,64}")

MEMORY_UNKNOWN = "앱 기본 목록에 없는 모델이라 메모리 요구량을 추정하지 못했습니다."
MODEL_UNVERIFIED = "이 모델은 아직 GPU에서 검증되지 않았습니다."
OBSERVATION_MISSING = ("저장된 GPU 정보가 없습니다. 연결 설정의 '환경 확인'을 누르면 실행 전에 GPU를 미리 "
                       "점검할 수 있습니다.")
OBSERVATION_STALE = "저장된 GPU 정보가 24시간보다 오래되었습니다. 연결 설정의 '환경 확인'을 다시 누르세요."
GPU_FACTS_UNKNOWN = ("GPU 정보를 읽지 못해 세대·드라이버·메모리를 미리 점검하지 못했습니다. "
                     "학습 직전 환경 검사가 대신 확인합니다.")
POD_FACTS_UNKNOWN = "RunPod에서 Pod의 GPU 종류·개수를 확인하지 못했습니다. 잠시 후 '환경 확인'을 다시 누르세요."
PRICE_UNKNOWN = "RunPod에서 Pod의 현재 시간당 요금을 확인하지 못했습니다. 잠시 후 다시 시도하세요."
SERVER_PRICE_UNKNOWN = ("GPU 서버의 시간당 비용이 비어 있거나 올바르지 않습니다. 연결 설정에서 0 이상의 숫자를 입력하세요"
                        "(내부 서버는 0).")


def _finding(level, code, message):
    return {"level": level, "code": code, "message": message}


def _price_findings(price, resource, *, allow_zero, unknown):
    """PRICE_UNKNOWN or PRICE_OVER_BUDGET for one destination's hourly price against the job's per-GPU budget x GPUs.
    A server may cost 0 (internal hardware); a RunPod price of 0 means the API gave no price."""
    if (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0
            or (price == 0 and not allow_zero)):
        return [_finding("block", "PRICE_UNKNOWN", unknown)]
    limit = float(resource.get("hourly_cost_usd", 0)) * int(resource.get("gpu_count", 1))
    if price > limit:
        return [_finding("block", "PRICE_OVER_BUDGET",
                         f"현재 시간당 요금 ${price:.3f}가 학습 설정의 한도 ${limit:.3f}를 넘습니다.")]
    return []


def evaluate(config, observation, capabilities, model, *, stage, now=None, server=None):
    """Block/warn findings for one job config against one observed destination.

    ``stage`` is "plan" (a stored observation; missing or stale facts are reported) or "execute" (live facts).
    ``server`` is ``{"hourly_cost_usd": float | None}`` for an SSH server destination (judged even without an
    observation)."""
    if stage not in {"plan", "execute"}:
        raise ValueError("unsupported compatibility stage")
    findings = []
    if model is None:
        findings.append(_finding("warn", "MEMORY_UNKNOWN", MEMORY_UNKNOWN))
    if model is None or not model.get("gpu_verified"):
        findings.append(_finding("warn", "MODEL_UNVERIFIED", MODEL_UNVERIFIED))
    if server is not None:
        findings += _price_findings(server.get("hourly_cost_usd"), config.get("resource_profile") or {},
                                    allow_zero=True, unknown=SERVER_PRICE_UNKNOWN)
    if observation is None:
        if stage == "plan":
            findings.append(_finding("warn", "OBSERVATION_MISSING", OBSERVATION_MISSING))
        return findings
    now = time.time() if now is None else now
    if stage == "plan" and now - float(observation.get("observed_at") or 0) >= STALE_SECONDS:
        findings.append(_finding("warn", "OBSERVATION_STALE", OBSERVATION_STALE))
    resource = config.get("resource_profile") or {}
    hyper = config.get("hyperparameters") or {}
    gpus, pod = observation.get("gpus") or [], observation.get("pod")
    gpu = min(gpus, key=lambda item: item["memory_mib"]) if gpus else None   # the smallest GPU bounds the job
    if gpu is None:
        findings.append(_finding("warn", "GPU_FACTS_UNKNOWN", GPU_FACTS_UNKNOWN))
    else:
        capability, driver = gpu["capability"], gpu["driver"]
        if not capability_supported(capability, capabilities["sass"], capabilities["ptx"]):
            findings.append(_finding("block", "GPU_ARCH_UNSUPPORTED",
                                     f"이 GPU({gpu['name']}, compute capability {capability})는 앱 학습 환경"
                                     f"(PyTorch CUDA {capabilities['torch_cuda']})이 지원하지 않는 세대입니다."))
        if not driver_at_least(driver, capabilities["min_driver"]):
            findings.append(_finding("block", "DRIVER_TOO_OLD",
                                     f"NVIDIA 드라이버 {driver}는 너무 오래되었습니다. "
                                     f"{capabilities['min_driver']} 이상이 필요합니다."))
        elif not driver_at_least(driver, capabilities["recommended_driver"]):
            findings.append(_finding("warn", "DRIVER_OLD",
                                     f"NVIDIA 드라이버 {driver}는 CUDA {capabilities['torch_cuda']} 정식 지원 드라이버"
                                     f"({capabilities['recommended_driver']}) 미만입니다. 일부 기능이 제한될 수 "
                                     "있습니다."))
        if (model is not None and model.get("gpu_verified") and "verified_capabilities" in model
                and capability not in model["verified_capabilities"]):
            findings.append(_finding("warn", "MODEL_UNVERIFIED",
                                     f"이 모델은 이 GPU 세대(compute capability {capability})에서 아직 검증되지 "
                                     "않았습니다."))
        if (hyper.get("precision", "bf16") == "bf16"
                and version_tuple(capability) < version_tuple(capabilities["bf16_min_capability"])):
            findings.append(_finding("block", "BF16_UNSUPPORTED",
                                     "이 GPU는 bf16을 지원하지 않습니다. 학습 설정의 정밀도를 fp16으로 바꾸세요."))
    if pod is None:
        names, observed_count = {item["name"] for item in gpus}, len(gpus)
    else:
        names = {pod.get("gpu_id"), pod.get("gpu_display_name")} - {None}
        observed_count = pod.get("gpu_count")
    pod_facts_known = pod is None or (
        bool(names) and isinstance(observed_count, int) and not isinstance(observed_count, bool) and observed_count > 0)
    if not pod_facts_known:
        findings.append(_finding("block", "POD_FACTS_UNKNOWN", POD_FACTS_UNKNOWN))
    else:
        configured = resource.get("gpu_type")
        if names and configured not in names:
            expected = (pod or {}).get("gpu_id") or sorted(names)[0]
            findings.append(_finding("block", "GPU_TYPE_MISMATCH",
                                     f"학습 설정의 GPU 종류({configured})가 연결된 GPU({expected})와 다릅니다. 연결 설정의 "
                                     "'GPU 정보 → 학습 설정에 적용'으로 맞춘 뒤 실행 계획을 다시 확인하세요."))
        count = int(resource.get("gpu_count", 1))
        if observed_count and count != observed_count:
            findings.append(_finding("block", "GPU_COUNT_MISMATCH",
                                     f"학습 설정의 GPU 수({count})가 연결된 GPU 수({observed_count})와 다릅니다."))
    if pod is not None:
        findings += _price_findings(pod.get("hourly_cost_usd"), resource, allow_zero=False, unknown=PRICE_UNKNOWN)
    if gpu is not None and model is not None:
        estimate = estimate_memory(model, config)
        vram = gpu["memory_mib"] / 1024
        if estimate is None:
            findings.append(_finding("warn", "MEMORY_UNKNOWN", MEMORY_UNKNOWN))
        elif estimate["weights_gib"] > vram * WEIGHTS_BLOCK_RATIO:
            findings.append(_finding("block", "MEMORY_WEIGHTS_EXCEED",
                                     f"모델 가중치만 약 {estimate['weights_gib']:.0f}GiB로 GPU 메모리 {vram:.0f}GiB의 "
                                     "95%를 넘습니다. 4비트 QLoRA, 더 작은 모델 또는 더 큰 GPU를 선택하세요."))
        elif estimate["total_gib"] > vram * TOTAL_WARN_RATIO:
            findings.append(_finding("warn", "MEMORY_TIGHT",
                                     f"예상 메모리 약 {estimate['total_gib']:.0f}GiB로 GPU 메모리 {vram:.0f}GiB의 "
                                     "90%를 넘어 부족할 수 있습니다. 4비트 QLoRA, 배치 1, 더 짧은 최대 길이(cutoff_len)를 고려하세요. "
                                     "추정치이므로 실제와 다를 수 있습니다."))
    return findings


def refusal(findings):
    """The Korean refusal for block findings, or None when nothing blocks."""
    blocks = [item["message"] for item in findings if item["level"] == "block"]
    return "실행할 수 없습니다. " + " ".join(blocks) if blocks else None


def make_observation(key, gpus, pod=None, now=None):
    """A destination's GPU facts; ``pod`` is the normalized RunPod API record (None for an SSH server)."""
    observation = {"key": key, "observed_at": time.time() if now is None else now, "gpus": list(gpus), "pod": None}
    if pod is not None:
        gpu = pod.get("gpu") or {}
        try:
            price = float(pod.get("costPerHr"))
        except (TypeError, ValueError):
            price = None
        observation["pod"] = {"gpu_id": gpu.get("id"), "gpu_display_name": gpu.get("displayName"),
                              "gpu_count": gpu.get("count"), "hourly_cost_usd": price}
    return observation


def _observation_path(root, key):
    if not isinstance(key, str) or not OBSERVATION_KEY.fullmatch(key):
        raise ValueError("invalid GPU observation key")
    return Path(root) / "gpu-observations" / f"{key}.json"


def save_observation(root, observation):
    path = _observation_path(root, observation["key"])
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(dir=path.parent, prefix=path.stem + ".", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(observation, ensure_ascii=False, sort_keys=True))
        os.replace(name, path)
    except BaseException:
        try:
            os.unlink(name)
        except OSError:
            pass
        raise


def _valid_observation(data, key):
    if not isinstance(data, dict) or data.get("key") != key:
        return False
    observed_at = data.get("observed_at")
    if isinstance(observed_at, bool) or not isinstance(observed_at, (int, float)):
        return False
    gpus = data.get("gpus")
    if not isinstance(gpus, list) or not all(
            isinstance(gpu, dict) and all(field in gpu for field in ("uuid", "name", "capability", "memory_mib", "driver"))
            for gpu in gpus):
        return False
    return data.get("pod") is None or isinstance(data["pod"], dict)


def load_observation(root, key):
    path = _observation_path(root, key)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if _valid_observation(data, key) else None
