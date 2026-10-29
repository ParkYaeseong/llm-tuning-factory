# GPU 호환성 점검과 CUDA 12.8 학습 환경 (1단계) Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ampere·Hopper·Ada·Blackwell GPU에서 학습 환경이 동작하도록 PyTorch를 CUDA 12.8 빌드로 바꾸고, 실행 전에 GPU 세대·드라이버·메모리·작업 설정 불일치를 한국어로 차단·경고하며, 실행 승인 한 번으로 RunPod 학습 환경 준비와 학습을 잇는다.

**Architecture:** 순수 판정 모듈 `gpu_compat.py`를 새로 둔다. 이 모듈은 nvidia-smi 출력 해석, 세대 지원 규칙, 메모리 추정, 차단/경고 판정, GPU 관측 저장만 하며 SSH·RunPod API를 부르지 않는다. 인프라 어댑터 `desktop_runtime.py`는 GPU 정보를 읽어 저장하고, `check()`는 설치된 환경에서 세대 지원과 실제 CUDA 연산을 확인한다. `desktop.py`는 계획(저장값)·환경 확인·실행(실시간)에서 같은 판정을 쓰고, 화면은 실행 전 확인 결과를 하나의 승인 창에 합친다.

**Tech Stack:** Python 3.12, uv 0.11.23(`--torch-backend cu128`), paramiko, httpx, pytest; TypeScript/Vite, Playwright(mock Tauri invoke).

**Spec:** [설계](../specs/2026-10-29-gpu-compat-runtime-design.md). 사용자 방향 승인 2026-10-29 “가장 좋은 방법으로 진행해”, 설계 문서 승인 “좋아, 구현 계획 작성해줘”.

**Branch:** `claude/gpu-compat-runtime`. PR #4(`codex/general-training-workflows`, 0.4.0)와 섞지 않는다.

---

## Global Constraints

- `uv sync --extra dev --extra tracking --locked`. 각 Task 끝에 해당 시험을, 완료 보고 전에 `uv run pytest`와 `uv run ruff check src tests scripts`를 실행한다. 화면 변경 후에는 `desktop`에서 `npm test`와 `npm run build`를 실행한다.
- Task 1–15는 CPU만 쓴다. 유료 자원, 빌린 Pod 설치, 데이터 전송은 하지 않는다. Task 16(GPU 인수 시험)은 사용자의 별도 명시적 승인 후에만 실행한다. 빌린 Pod는 멈추거나 지우지 않는다.
- 학습 어댑터와 인프라 어댑터를 분리한다. `gpu_compat.py`는 SSH·RunPod·MLflow를 부르지 않는다.
- `engine_profile()`의 반환값은 바꾸지 않는다. 그래프 계획 기록에 들어간다.
- 화면·거부 문구는 한국어로 쓰고 비밀값을 담지 않는다. 내부 예외 영문 문구 중 기존 시험이 맞추는 것은 유지한다.
- `src/**/*.py`는 LF 줄바꿈을 유지한다(`test_source_files_have_lf_line_endings...`).
- Edit 도구로 `\u` 이스케이프를 직접 쓰지 않는다. 한글·기호는 그대로 입력한다.
- 실제 검증과 남은 GPU 작업은 `docs/PROGRESS.md`에 기록한다(Task 14).

## File Structure

| 파일 | 책임 |
|---|---|
| `containers/gpu-requirements.lock`, `containers/gpu-ppo-requirements.lock` (수정) | torch·torchaudio·torchvision `+cu128`, `nvidia-*-cu12` 12.8 |
| `containers/gpu-ppo-overrides.txt` (생성) | PPO 고정 파일 재생성용 `pydantic==2.10.6` 강제값 |
| `containers/Dockerfile`, `scripts/bootstrap_approved_pod.py`, `scripts/update_approved_runtime.py` (수정) | 같은 고정 파일을 PyTorch cu128 색인으로 설치 |
| `scripts/inspect_gpu_wheels.py` (생성) | 출시 전 검사: torch 패키지의 컴파일 세대 목록, bitsandbytes CUDA 12.8 라이브러리 |
| `src/tuning_factory/gpu_compat.py` (생성) | GPU 정보 해석, 세대·드라이버 규칙, 메모리 추정, 판정, 관측 저장 |
| `src/tuning_factory/runtime_profiles.py` (수정) | `runtime_capabilities(profile)` 환경 호환표 |
| `src/tuning_factory/capabilities.py` (수정) | 모델 목록에 `hidden_size`·`num_layers`, Llama 3.1 70B 두 항목 |
| `src/tuning_factory/desktop_runtime.py` (수정) | `check()` 확장과 예외 3종, GPU 정보 수집·저장, `install_runtime()`, `runtime()` 상태 보고, `executor_for(gate, prepare_runtime)` |
| `src/tuning_factory/desktop.py` (수정) | 계획 호환성, `runtime_check` 판정, 실행 게이트·실행 중 준비 |
| `desktop/src/gpu-preflight.ts` (생성) | 계획 호환성 화면, 실행 전 확인 결과 해석, 환경 확인 안내 문구 |
| `desktop/src/main.ts`, `desktop/src/style.css` (수정) | 계획 카드, 실행 전 확인과 승인 창, `prepare_runtime` 전달 |
| `tests/test_gpu_compat.py`, `tests/test_inspect_gpu_wheels.py` (생성) | 순수 모듈·검사 스크립트 시험 |
| `tests/test_runtime_profiles.py`, `tests/test_desktop_runtime.py`, `tests/test_desktop.py`, `tests/test_training_capabilities.py`, `tests/test_model_ops_contract.py`, `tests/test_release_version.py` (수정) | 해당 변경 시험 |
| `desktop/tests/workspace.spec.ts` (수정) | 계획 호환성·실행 전 차단·승인 창 환경 준비 |
| 버전 파일 5개, `docs/OPERATIONS.md`, `docs/WINDOWS_APP.md`, `docs/TRAINING_AND_STORAGE.md`, `docs/PROGRESS.md` (수정) | 0.5.0과 운영 문서 |

---

### Task 1: CUDA 12.8 GPU 고정 파일

**Files:**
- Modify: `containers/gpu-requirements.lock`, `containers/gpu-ppo-requirements.lock` (재생성)
- Create: `containers/gpu-ppo-overrides.txt`
- Modify: `containers/Dockerfile:13`, `scripts/bootstrap_approved_pod.py:33`, `scripts/update_approved_runtime.py:26`
- Modify: `src/tuning_factory/runtime_profiles.py:15` (주석), `tests/test_model_ops_contract.py:978`
- Test: `tests/test_runtime_profiles.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_runtime_profiles.py` 끝에 추가

```python
def test_gpu_locks_pin_the_cu128_pytorch_family():
    import re
    from pathlib import Path

    root = Path(__file__).resolve().parents[1]
    for name in ("gpu-requirements.lock", "gpu-ppo-requirements.lock"):
        text = (root / "containers" / name).read_text(encoding="utf-8")
        for package, version in (("torch", "2.7.1+cu128"), ("torchaudio", "2.7.1+cu128"),
                                 ("torchvision", "0.22.1+cu128")):
            assert re.search(rf"^{package}=={re.escape(version)} \\$", text, re.M), (name, package)
        assert "\nnvidia-cuda-runtime-cu12==12.8." in text, name
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_runtime_profiles.py::test_gpu_locks_pin_the_cu128_pytorch_family -v`
Expected: FAIL (`('gpu-requirements.lock', 'torch')`)

- [ ] **Step 3: PPO 강제값 파일 생성** — `containers/gpu-ppo-overrides.txt`

```text
# PPO keeps LLaMA-Factory 0.9.3's pydantic even though the control plane (pyproject.toml) pins a newer one.
# The worker wheel is installed with --no-deps, as the current PPO lock already does.
pydantic==2.10.6
```

- [ ] **Step 4: 기존 파일 위에 다시 생성** (기존 고정 버전을 선호값으로 쓴다)

```bash
uv pip compile pyproject.toml containers/gpu-requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --torch-backend cu128 --output-file containers/gpu-requirements.lock --no-header
uv pip compile pyproject.toml containers/gpu-ppo-requirements.in --override containers/gpu-ppo-overrides.txt --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --torch-backend cu128 --output-file containers/gpu-ppo-requirements.lock --no-header
```

- [ ] **Step 5: 변경 범위 확인**

Run: `git diff -U0 containers/gpu-requirements.lock containers/gpu-ppo-requirements.lock | grep -E '^[-+][A-Za-z0-9_.-]+==' | grep -v nvidia-`
Expected: 다음 줄만 나온다(2026-10-29 시험 생성 결과와 같다). 다른 패키지 버전이 바뀌었으면 멈추고 원인을 확인한다.
- 두 파일: `-torch==2.7.1`, `+torch==2.7.1+cu128`, torchaudio·torchvision의 같은 쌍.
- 주 파일 추가: `+boto3==1.43.114`, `+botocore==1.43.121`, `+jmespath==1.1.0`, `+s3transfer==0.19.3`. `pyproject.toml`이 요구하지만 이전 고정 파일에 없던 의존성이다.
- PPO 파일 추가: 위 넷과 `+bcrypt`, `+cryptography`, `+invoke`, `+paramiko`, `+pynacl`.

- [ ] **Step 6: 같은 고정 파일을 설치하는 곳 수정**

`containers/Dockerfile` 13행:

```dockerfile
RUN pip install --no-cache-dir --require-hashes --extra-index-url https://download.pytorch.org/whl/cu128 -r containers/gpu-requirements.lock
```

`scripts/bootstrap_approved_pod.py` 33행:

```text
uv pip sync --python /workspace/factory-acceptance/runtime/venv/bin/python --require-hashes --torch-backend cu128 /workspace/factory-acceptance/runtime/containers/gpu-requirements.lock
```

`scripts/update_approved_runtime.py` 26행:

```python
command = ('uv pip sync --python ' + root + '/venv/bin/python --require-hashes --torch-backend cu128 '
           + root + '/containers/gpu-requirements.lock')
```

`src/tuning_factory/runtime_profiles.py` 15행 주석:

```python
# Model-operation (merge/materialize) library pins; base versions (without +cu128) equal containers/gpu-requirements.lock.
```

- [ ] **Step 7: 공유 고정값 시험이 로컬 버전 표시를 무시하도록 수정** — `tests/test_model_ops_contract.py` 978행

```python
    assert dict(MODEL_OPS_PINS) == {name: gpu[name].split("+")[0]
                                    for name in ("torch", "peft", "transformers", "safetensors")}
```

- [ ] **Step 8: 통과 확인**

Run: `uv run pytest tests/test_runtime_profiles.py tests/test_model_ops_contract.py tests/test_ppo_profile.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add containers/gpu-requirements.lock containers/gpu-ppo-requirements.lock containers/gpu-ppo-overrides.txt containers/Dockerfile scripts/bootstrap_approved_pod.py scripts/update_approved_runtime.py src/tuning_factory/runtime_profiles.py tests/test_runtime_profiles.py tests/test_model_ops_contract.py
git commit -m "build: pin the CUDA 12.8 PyTorch family in both GPU locks"
```

---

### Task 2: `gpu_compat` — GPU 정보 해석과 세대·드라이버 규칙

**Files:**
- Create: `src/tuning_factory/gpu_compat.py`
- Test: `tests/test_gpu_compat.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_gpu_compat.py`

```python
import pytest

from tuning_factory import gpu_compat

SMI = "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n"
SASS = ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"]


def test_parse_gpu_facts_reads_the_fixed_query():
    assert gpu_compat.parse_gpu_facts(SMI) == [{
        "uuid": "GPU-8d31ab36-777c-b39c-90aa-fb51cd548d21", "name": "NVIDIA RTX PRO 4000 Blackwell",
        "capability": "12.0", "memory_mib": 24467, "driver": "595.91.07"}]


@pytest.mark.parametrize("text", ["", "GPU-1, NVIDIA RTX A4000, 16376, Disabled\n",
                                  "GPU-1, NVIDIA RTX A4000, x.y, 16376, 570.86.15\n"])
def test_parse_gpu_facts_refuses_unexpected_output(text):
    with pytest.raises(ValueError):
        gpu_compat.parse_gpu_facts(text)


@pytest.mark.parametrize("capability,supported", [
    ("8.0", True), ("8.6", True), ("8.9", True), ("9.0", True), ("10.0", True), ("12.0", True), ("12.1", True),
    ("7.0", False), ("6.1", False), ("11.0", False)])
def test_same_major_machine_code_rule(capability, supported):
    assert gpu_compat.capability_supported(capability, SASS, []) is supported


def test_ptx_covers_newer_devices_only():
    assert gpu_compat.capability_supported("13.0", ["9.0"], ["12.0"])
    assert not gpu_compat.capability_supported("11.0", ["9.0"], ["12.0"])


def test_driver_versions_compare_numerically():
    assert gpu_compat.driver_at_least("595.91.07", "570.26")
    assert not gpu_compat.driver_at_least("525.60.12", "525.60.13")
    assert gpu_compat.driver_at_least("1000.1", "999.99")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_gpu_compat.py -v`
Expected: FAIL (`ModuleNotFoundError: No module named 'tuning_factory.gpu_compat'`)

- [ ] **Step 3: 최소 구현** — `src/tuning_factory/gpu_compat.py`

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_gpu_compat.py -v`
Expected: PASS (17 tests)

- [ ] **Step 5: Commit**

```bash
git add src/tuning_factory/gpu_compat.py tests/test_gpu_compat.py
git commit -m "feat: parse nvidia-smi GPU facts and judge CUDA architecture and driver support"
```

---

### Task 3: 출시 전 검사 스크립트 — torch 세대 목록과 bitsandbytes

**Files:**
- Create: `scripts/inspect_gpu_wheels.py`
- Test: `tests/test_inspect_gpu_wheels.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_inspect_gpu_wheels.py`

```python
import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_gpu_wheels.py"
SPEC = importlib.util.spec_from_file_location("inspect_gpu_wheels", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def archive(member, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr(member, data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_the_longest_compiled_flag_list_becomes_versions():
    data = b"\x00sm_80\x00sm_75 sm_80 sm_86 sm_90 sm_100 sm_120 compute_120\x00tail"
    with archive(gate.TORCH_LIBRARY, data) as wheel:
        flags = gate.parse_arch_flags(wheel.read(gate.TORCH_LIBRARY))
    assert flags == {"sass": ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"], "ptx": ["12.0"]}
    assert gate.unsupported(flags) == []


def test_a_build_without_blackwell_code_is_reported():
    flags = gate.parse_arch_flags(b"sm_50 sm_60 sm_70 sm_75 sm_80 sm_86 sm_90")
    assert gate.unsupported(flags) == ["10.0", "12.0"]


def test_a_missing_flag_list_is_an_error():
    with pytest.raises(ValueError, match="not found"):
        gate.parse_arch_flags(b"sm_80 only once")


def test_bitsandbytes_cuda128_library_is_recognised():
    assert gate.has_cuda128_library(["bitsandbytes/libbitsandbytes_cuda126.so",
                                     "bitsandbytes/libbitsandbytes_cuda128.so"])
    assert not gate.has_cuda128_library(["bitsandbytes/libbitsandbytes_cuda126.so"])
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_inspect_gpu_wheels.py -v`
Expected: FAIL (`FileNotFoundError` … `inspect_gpu_wheels.py`)

- [ ] **Step 3: 구현** — `scripts/inspect_gpu_wheels.py`

```python
"""Release gate for the CUDA 12.8 GPU locks: no install and no GPU.

Reads the CUDA architectures compiled into the pinned torch wheel and checks that bitsandbytes ships a CUDA 12.8
library. Wheel members are read with HTTP range requests, so only the zip directory and one library are fetched.
Usage: uv run python scripts/inspect_gpu_wheels.py [--lock containers/gpu-requirements.lock]
"""
import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx
from tuning_factory.gpu_compat import capability_supported, version_tuple

TORCH_INDEX = "https://download.pytorch.org/whl/cu128/torch/"
TORCH_WHEEL = "torch-2.7.1+cu128-cp312-cp312-manylinux_2_28_x86_64.whl"
TORCH_LIBRARY = "torch/lib/libtorch_python.so"   # holds the flag list torch.cuda.get_arch_list() splits
BITSANDBYTES_JSON = "https://pypi.org/pypi/bitsandbytes/0.48.1/json"
REQUIRED = ("8.0", "8.6", "8.9", "9.0", "10.0", "12.0")   # Ampere, Ada, Hopper, Blackwell (B200, RTX PRO)
ARCH_FLAGS = re.compile(rb"(?:sm|compute)_\d+[a-z]?(?: (?:sm|compute)_\d+[a-z]?)+")


def parse_arch_flags(data):
    """The longest space-separated sm_/compute_ list in a binary, as {"sass": [...], "ptx": [...]} versions."""
    matches = ARCH_FLAGS.findall(data)
    if not matches:
        raise ValueError("compiled CUDA architecture list not found")
    sass, ptx = set(), set()
    for token in max(matches, key=len).decode("ascii").split():
        kind, digits = re.fullmatch(r"(sm|compute)_(\d+)[a-z]?", token).groups()
        (sass if kind == "sm" else ptx).add(f"{int(digits[:-1])}.{digits[-1]}")
    return {"sass": sorted(sass, key=version_tuple), "ptx": sorted(ptx, key=version_tuple)}


def unsupported(flags):
    return [code for code in REQUIRED if not capability_supported(code, flags["sass"], flags["ptx"])]


def has_cuda128_library(names):
    return any(re.search(r"(^|/)libbitsandbytes_cuda128[^/]*\.so$", name) for name in names)


class HTTPRangeFile(io.RawIOBase):
    """A read-only seekable view of one URL for zipfile; refuses servers without byte ranges."""

    def __init__(self, client, url):
        self.client, self.url, self.position = client, url, 0
        head = client.head(url, follow_redirects=True)
        head.raise_for_status()
        if head.headers.get("accept-ranges") != "bytes":
            raise ValueError("server does not support range requests: " + url)
        self.size = int(head.headers["content-length"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, base + offset)
        return self.position

    def readinto(self, buffer):
        if self.position >= self.size:
            return 0
        end = min(self.size, self.position + len(buffer)) - 1
        response = self.client.get(self.url, headers={"Range": f"bytes={self.position}-{end}"},
                                   follow_redirects=True)
        if response.status_code != 206:
            raise ValueError("range request refused: " + self.url)
        data = response.content
        buffer[:len(data)] = data
        self.position += len(data)
        return len(data)


def remote_zip(client, url):
    return zipfile.ZipFile(io.BufferedReader(HTTPRangeFile(client, url), buffer_size=8 << 20))


def torch_wheel_url(client, lock_text):
    page = client.get(TORCH_INDEX, follow_redirects=True)
    page.raise_for_status()
    name = re.escape(TORCH_WHEEL.replace("+", "%2B"))
    match = re.search(r'href="([^"#]*' + name + r')#sha256=([0-9a-f]{64})"', page.text)
    if not match:
        raise ValueError("pinned torch wheel is not listed on the PyTorch cu128 index")
    if match.group(2) not in lock_text:
        raise ValueError("PyTorch index hash differs from the lock")
    return str(httpx.URL(TORCH_INDEX).join(match.group(1)))


def bitsandbytes_wheel_url(client):
    response = client.get(BITSANDBYTES_JSON, follow_redirects=True)
    response.raise_for_status()
    for item in response.json()["urls"]:
        if item["filename"].endswith(".whl") and "manylinux" in item["filename"] and "x86_64" in item["filename"]:
            return item["url"]
    raise ValueError("bitsandbytes 0.48.1 Linux x86_64 wheel not found")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", default="containers/gpu-requirements.lock")
    args = parser.parse_args(argv)
    lock_text = Path(args.lock).read_text(encoding="utf-8")
    with httpx.Client(timeout=60) as client:
        url = torch_wheel_url(client, lock_text)
        with remote_zip(client, url) as wheel:
            flags = parse_arch_flags(wheel.read(TORCH_LIBRARY))
        with remote_zip(client, bitsandbytes_wheel_url(client)) as wheel:
            cuda128 = has_cuda128_library(wheel.namelist())
    missing = unsupported(flags)
    print(json.dumps({"torch_wheel": url, **flags, "unsupported_required": missing,
                      "bitsandbytes_cuda128": cuda128}, indent=2))
    return 0 if not missing and cuda128 else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_inspect_gpu_wheels.py -v && uv run ruff check scripts/inspect_gpu_wheels.py tests/test_inspect_gpu_wheels.py`
Expected: PASS, `All checks passed!`

- [ ] **Step 5: 실제 패키지로 검사(네트워크 읽기만, 설치 없음)**

Run: `uv run python scripts/inspect_gpu_wheels.py`
Expected: exit 0, `"unsupported_required": []`, `"bitsandbytes_cuda128": true`. 출력의 `sass`·`ptx`를 Task 4 Step 3에 그대로 쓴다.
- 예상값은 `["7.5","8.0","8.6","9.0","10.0","12.0"]` / `["12.0"]`이다. 다르면 출력값을 쓴다.
- exit 1이거나 `compiled CUDA architecture list not found`가 나오면 멈춘다. 결과를 PROGRESS에 적고 사용자에게 보고한다.
  그 경우 확인 방법은 Task 16의 승인된 일회용 GPU 검사뿐이다.

- [ ] **Step 6: Commit**

```bash
git add scripts/inspect_gpu_wheels.py tests/test_inspect_gpu_wheels.py
git commit -m "build: add a release gate reading the CUDA architectures of the pinned torch wheel"
```

---

### Task 4: 환경 호환표 `runtime_capabilities`

**Files:**
- Modify: `src/tuning_factory/runtime_profiles.py`
- Test: `tests/test_runtime_profiles.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_runtime_profiles.py` 끝에 추가

```python
def test_runtime_capabilities_match_the_cu128_locks_and_leave_engine_profiles_unchanged():
    import re
    from pathlib import Path

    import pytest

    from tuning_factory.runtime_profiles import engine_profile, runtime_capabilities

    root = Path(__file__).resolve().parents[1]
    for stage, profile in (("sft", "primary"), ("ppo", "ppo")):
        caps = runtime_capabilities(profile)
        lock = (root / engine_profile(stage)["lock_file"]).read_text(encoding="utf-8")
        assert re.search(r"^torch==2\.7\.1\+" + caps["torch_backend"] + r" \\$", lock, re.M)
        assert caps["torch_cuda"] == "12.8" and caps["bf16_min_capability"] == "8.0"
        assert caps["min_driver"] == "525.60.13" and caps["recommended_driver"] == "570.26"
        assert set(engine_profile(stage)) == {"profile", "engine_version", "lock_file"}
    caps = runtime_capabilities("primary")
    assert caps["sass"] == ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"] and caps["ptx"] == ["12.0"]
    caps["sass"].append("1.0")
    assert "1.0" not in runtime_capabilities("primary")["sass"]   # callers receive copies
    with pytest.raises(ValueError, match="unsupported runtime profile"):
        runtime_capabilities("cuda")
```

Task 3 Step 5의 출력이 예상값과 다르면 위 `sass`·`ptx` 기대값을 그 출력으로 바꾼다.

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_runtime_profiles.py::test_runtime_capabilities_match_the_cu128_locks_and_leave_engine_profiles_unchanged -v`
Expected: FAIL (`ImportError: cannot import name 'runtime_capabilities'`)

- [ ] **Step 3: 구현** — `src/tuning_factory/runtime_profiles.py`, `engine_profile()` 아래에 추가

```python
# Both private runtimes use the CUDA 12.8 PyTorch build. sass/ptx are the architectures compiled into the pinned
# torch wheel (scripts/inspect_gpu_wheels.py, recorded in docs/PROGRESS.md). min_driver is the CUDA 12 minor-version
# compatibility floor; recommended_driver is the CUDA 12.8 release driver.
RUNTIME_CAPABILITIES = MappingProxyType({
    "torch_backend": "cu128", "torch_cuda": "12.8",
    "sass": ("7.5", "8.0", "8.6", "9.0", "10.0", "12.0"), "ptx": ("12.0",),
    "bf16_min_capability": "8.0", "min_driver": "525.60.13", "recommended_driver": "570.26"})


def runtime_capabilities(profile):
    """The GPU support sheet of one private runtime (primary or ppo); kept apart from engine_profile() snapshots."""
    if profile not in {"primary", "ppo"}:
        raise ValueError("unsupported runtime profile")
    return {key: list(value) if isinstance(value, tuple) else value for key, value in RUNTIME_CAPABILITIES.items()}
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_runtime_profiles.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tuning_factory/runtime_profiles.py tests/test_runtime_profiles.py
git commit -m "feat: record the GPU support sheet of the CUDA 12.8 runtimes"
```

---

### Task 5: 메모리 추정

**Files:**
- Modify: `src/tuning_factory/gpu_compat.py`
- Test: `tests/test_gpu_compat.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_gpu_compat.py` 끝에 추가

```python
QWEN05 = {"id": "Qwen/Qwen2.5-0.5B-Instruct", "parameters_b": 0.5, "hidden_size": 896, "num_layers": 24,
          "gpu_verified": True}
LLAMA70 = {"id": "meta-llama/Llama-3.1-70B-Instruct", "parameters_b": 70, "hidden_size": 8192, "num_layers": 80,
           "gpu_verified": False}


def training(method="lora", **hyper):
    return {"training_method": method, "quantization_bit": 4,
            "hyperparameters": {"precision": "bf16", "per_device_train_batch_size": 1, "cutoff_len": 1024, **hyper},
            "finetuning_options": {"freeze_trainable_layers": 2}}


def test_memory_estimates_cover_lora_qlora_full_and_freeze():
    small = gpu_compat.estimate_memory(QWEN05, training())
    assert 0.9 < small["weights_gib"] < 1.0 and small["total_gib"] < 3
    assert 8.5 < gpu_compat.estimate_memory(QWEN05, training("full"))["total_gib"] < 9.5
    assert 2.9 < gpu_compat.estimate_memory(QWEN05, training("freeze"))["total_gib"] < 3.2
    assert gpu_compat.estimate_memory(LLAMA70, training())["weights_gib"] > 120
    qlora = gpu_compat.estimate_memory(LLAMA70, training("qlora"))
    assert 34 < qlora["weights_gib"] < 37 and 45 < qlora["total_gib"] < 55


def test_memory_estimate_needs_catalog_sizes():
    assert gpu_compat.estimate_memory(None, training()) is None
    assert gpu_compat.estimate_memory({"parameters_b": 7}, training()) is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_gpu_compat.py -k memory -v`
Expected: FAIL (`AttributeError: module 'tuning_factory.gpu_compat' has no attribute 'estimate_memory'`)

- [ ] **Step 3: 구현** — `src/tuning_factory/gpu_compat.py` 끝에 추가

```python
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
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_gpu_compat.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tuning_factory/gpu_compat.py tests/test_gpu_compat.py
git commit -m "feat: estimate single-GPU training memory for catalog models"
```

---

### Task 6: 모델 목록 — 크기 정보와 Llama 3.1 70B

**Files:**
- Modify: `src/tuning_factory/capabilities.py:18-41`
- Test: `tests/test_training_capabilities.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_training_capabilities.py` 끝에 추가

```python
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
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_training_capabilities.py::test_catalog_records_sizes_for_memory_estimates_and_adds_llama_70b -v`
Expected: FAIL (`KeyError: 'hidden_size'`)

- [ ] **Step 3: 구현** — `model_catalog()` 전체를 다음으로 바꾼다. 값은 각 모델 `config.json`의 `hidden_size`·`num_hidden_layers`다.
  Qwen·Mistral은 2026-10-29에 공개 config로 확인했다. Llama·Gemma 2는 승인 필요 모델이라 공개된 값을 쓴다.

```python
def model_catalog():
    models = []
    # (name, parameters in billions, hidden_size, num_hidden_layers) from each model's config.json
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
                           "verification_scope": ("0.2.3 SFT LoRA/4-bit QLoRA synthetic H100 only"
                                                  if model_id == "Qwen/Qwen2.5-0.5B-Instruct"
                                                  else "architecture profile; GPU verification pending")})
    return models
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_training_capabilities.py tests/test_desktop.py tests/test_model_hub.py -q`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/tuning_factory/capabilities.py tests/test_training_capabilities.py
git commit -m "feat: add model sizes to the catalog and list Llama 3.1 70B for large single GPUs"
```

---

### Task 7: 호환성 판정과 관측 저장

**Files:**
- Modify: `src/tuning_factory/gpu_compat.py`
- Test: `tests/test_gpu_compat.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_gpu_compat.py` 끝에 추가

```python
CAPS = {"torch_cuda": "12.8", "sass": SASS, "ptx": [], "bf16_min_capability": "8.0",
        "min_driver": "525.60.13", "recommended_driver": "570.26"}
BLACKWELL = gpu_compat.parse_gpu_facts(SMI)[0]
POD = {"gpu_id": "NVIDIA RTX PRO 4000 Blackwell", "gpu_display_name": None, "gpu_count": 1, "hourly_cost_usd": 0.57}


def job(method="lora", **resource):
    config = training(method)
    config["resource_profile"] = {"gpu_type": "NVIDIA RTX PRO 4000 Blackwell", "gpu_count": 1,
                                  "hourly_cost_usd": 0.6, **resource}
    return config


def observed(gpus=(BLACKWELL,), pod=POD, at=1000.0):
    return {"key": "runpod-borrowed", "observed_at": at, "gpus": list(gpus), "pod": pod}


def codes(findings):
    return {(item["level"], item["code"]) for item in findings}


def test_a_matching_job_on_a_supported_gpu_has_no_findings():
    assert gpu_compat.evaluate(job(), observed(), CAPS, QWEN05, stage="execute") == []


def test_the_queued_a4000_job_is_refused_on_the_blackwell_pod():
    findings = gpu_compat.evaluate(job(gpu_type="NVIDIA RTX A4000", hourly_cost_usd=0.5), observed(), CAPS, QWEN05,
                                   stage="execute")
    assert codes(findings) == {("block", "GPU_TYPE_MISMATCH"), ("block", "PRICE_OVER_BUDGET")}
    mismatch = next(item for item in findings if item["code"] == "GPU_TYPE_MISMATCH")
    assert "NVIDIA RTX A4000" in mismatch["message"] and "NVIDIA RTX PRO 4000 Blackwell" in mismatch["message"]
    assert gpu_compat.refusal(findings).startswith("실행할 수 없습니다.")
    assert gpu_compat.refusal([]) is None


def test_old_architecture_driver_and_bf16_block():
    old = {**BLACKWELL, "name": "Tesla V100-SXM2-32GB", "capability": "7.0", "memory_mib": 32768, "driver": "470.82"}
    findings = gpu_compat.evaluate(job(gpu_type="Tesla V100-SXM2-32GB"), observed([old], pod=None), CAPS, QWEN05,
                                   stage="execute")
    assert {("block", "GPU_ARCH_UNSUPPORTED"), ("block", "DRIVER_TOO_OLD"), ("block", "BF16_UNSUPPORTED")} <= codes(
        findings)


def test_a_driver_below_the_cuda_12_8_release_warns():
    findings = gpu_compat.evaluate(job(), observed([{**BLACKWELL, "driver": "535.104.05"}]), CAPS, QWEN05,
                                   stage="execute")
    assert codes(findings) == {("warn", "DRIVER_OLD")}


def test_memory_blocks_on_weights_and_warns_when_tight():
    h100 = {**BLACKWELL, "name": "NVIDIA H100 80GB HBM3", "capability": "9.0", "memory_mib": 81559}
    pod = {**POD, "gpu_id": "NVIDIA H100 80GB HBM3", "hourly_cost_usd": 2.0}
    lora = gpu_compat.evaluate(job(gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0), observed([h100], pod),
                               CAPS, LLAMA70, stage="execute")
    assert ("block", "MEMORY_WEIGHTS_EXCEED") in codes(lora)
    qlora = gpu_compat.evaluate(job("qlora", gpu_type="NVIDIA H100 80GB HBM3", hourly_cost_usd=3.0),
                                observed([h100], pod), CAPS, LLAMA70, stage="execute")
    assert codes(qlora) == {("warn", "MODEL_UNVERIFIED")}
    qwen7 = {"id": "Qwen/Qwen2.5-7B-Instruct", "parameters_b": 7, "hidden_size": 3584, "num_layers": 28,
             "gpu_verified": False}
    tight = job()
    tight["hyperparameters"].update(cutoff_len=8192, per_device_train_batch_size=4)
    assert codes(gpu_compat.evaluate(tight, observed(), CAPS, qwen7, stage="execute")) == {
        ("warn", "MEMORY_TIGHT"), ("warn", "MODEL_UNVERIFIED")}


def test_unknown_models_and_unreadable_gpu_facts_warn():
    findings = gpu_compat.evaluate(job(), observed(gpus=[]), CAPS, None, stage="execute")
    assert codes(findings) == {("warn", "MEMORY_UNKNOWN"), ("warn", "MODEL_UNVERIFIED"),
                               ("warn", "GPU_FACTS_UNKNOWN")}


def test_plan_reports_missing_and_stale_observations_only():
    assert codes(gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="plan")) == {("warn", "OBSERVATION_MISSING")}
    stale = gpu_compat.evaluate(job(), observed(at=0.0), CAPS, QWEN05, stage="plan",
                                now=gpu_compat.STALE_SECONDS + 1)
    assert codes(stale) == {("warn", "OBSERVATION_STALE")}
    assert gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="execute") == []
    with pytest.raises(ValueError, match="stage"):
        gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="later")


def test_observation_store_round_trips_and_refuses_unsafe_keys(tmp_path):
    pod = {"gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1}, "costPerHr": "0.57"}
    observation = gpu_compat.make_observation("runpod-borrowed", [BLACKWELL], pod, now=5.0)
    assert observation == {"key": "runpod-borrowed", "observed_at": 5.0, "gpus": [BLACKWELL], "pod": POD}
    gpu_compat.save_observation(tmp_path, observation)
    assert gpu_compat.load_observation(tmp_path, "runpod-borrowed") == observation
    assert gpu_compat.load_observation(tmp_path, "runpod-other") is None
    with pytest.raises(ValueError, match="key"):
        gpu_compat.load_observation(tmp_path, "../escape")
    assert gpu_compat.make_observation("server-0123456789abcdef", [BLACKWELL], None, now=1.0)["pod"] is None
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_gpu_compat.py -v`
Expected: FAIL (`AttributeError: ... 'evaluate'`)

- [ ] **Step 3: 구현** — `src/tuning_factory/gpu_compat.py`. 맨 위 import를 다음으로 바꾸고, 파일 끝에 판정·저장 코드를 추가한다.

```python
import json
import os
import re
import time
from pathlib import Path
```

```python
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


def _finding(level, code, message):
    return {"level": level, "code": code, "message": message}


def evaluate(config, observation, capabilities, model, *, stage, now=None):
    """Block/warn findings for one job config against one observed destination.

    ``stage`` is "plan" (a stored observation; missing or stale facts are reported) or "execute" (live facts)."""
    if stage not in {"plan", "execute"}:
        raise ValueError("unsupported compatibility stage")
    findings = []
    if model is None:
        findings.append(_finding("warn", "MEMORY_UNKNOWN", MEMORY_UNKNOWN))
    if model is None or not model.get("gpu_verified"):
        findings.append(_finding("warn", "MODEL_UNVERIFIED", MODEL_UNVERIFIED))
    if observation is None:
        if stage == "plan":
            findings.append(_finding("warn", "OBSERVATION_MISSING", OBSERVATION_MISSING))
        return findings
    now = time.time() if now is None else now
    if stage == "plan" and now - float(observation.get("observed_at") or 0) > STALE_SECONDS:
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
        if (hyper.get("precision", "bf16") == "bf16"
                and version_tuple(capability) < version_tuple(capabilities["bf16_min_capability"])):
            findings.append(_finding("block", "BF16_UNSUPPORTED",
                                     "이 GPU는 bf16을 지원하지 않습니다. 학습 설정의 정밀도를 fp16으로 바꾸세요."))
    names = ({pod.get("gpu_id"), pod.get("gpu_display_name")} - {None}) if pod else {item["name"] for item in gpus}
    configured = resource.get("gpu_type")
    if names and configured not in names:
        expected = (pod or {}).get("gpu_id") or sorted(names)[0]
        findings.append(_finding("block", "GPU_TYPE_MISMATCH",
                                 f"학습 설정의 GPU 종류({configured})가 연결된 GPU({expected})와 다릅니다. 연결 설정의 "
                                 "'GPU 정보 → 학습 설정에 적용'으로 맞춘 뒤 실행 계획을 다시 확인하세요."))
    count = int(resource.get("gpu_count", 1))
    observed_count = pod.get("gpu_count") if pod else len(gpus)
    if observed_count and count != observed_count:
        findings.append(_finding("block", "GPU_COUNT_MISMATCH",
                                 f"학습 설정의 GPU 수({count})가 연결된 GPU 수({observed_count})와 다릅니다."))
    price = (pod or {}).get("hourly_cost_usd")
    limit = float(resource.get("hourly_cost_usd", 0)) * count
    if price is not None and price > limit + 1e-9:
        findings.append(_finding("block", "PRICE_OVER_BUDGET",
                                 f"현재 시간당 요금 ${price:.2f}가 학습 설정의 한도 ${limit:.2f}를 넘습니다."))
    if gpu is not None and model is not None:
        estimate = estimate_memory(model, config)
        vram = gpu["memory_mib"] / 1024
        if estimate is None:
            findings.append(_finding("warn", "MEMORY_UNKNOWN", MEMORY_UNKNOWN))
        elif estimate["weights_gib"] > vram * WEIGHTS_BLOCK_RATIO:
            findings.append(_finding("block", "MEMORY_WEIGHTS_EXCEED",
                                     f"모델 가중치만 약 {estimate['weights_gib']:.0f}GiB로 GPU 메모리 {vram:.0f}GiB를 "
                                     "넘습니다. 4비트 QLoRA, 더 작은 모델 또는 더 큰 GPU를 선택하세요."))
        elif estimate["total_gib"] > vram * TOTAL_WARN_RATIO:
            findings.append(_finding("warn", "MEMORY_TIGHT",
                                     f"예상 메모리 약 {estimate['total_gib']:.0f}GiB로 GPU 메모리 {vram:.0f}GiB에 "
                                     "빠듯합니다. 4비트 QLoRA, 배치 1, 더 짧은 최대 길이(cutoff_len)를 고려하세요. "
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
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(observation, ensure_ascii=False, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def load_observation(root, key):
    path = _observation_path(root, key)
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
```

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_gpu_compat.py -v && uv run ruff check src/tuning_factory/gpu_compat.py tests/test_gpu_compat.py`
Expected: PASS, `All checks passed!`

- [ ] **Step 5: Commit**

```bash
git add src/tuning_factory/gpu_compat.py tests/test_gpu_compat.py
git commit -m "feat: judge a job against observed GPU facts with block and warn findings"
```

---

### Task 8: 학습 환경 검사 강화 — 세대·연산·bf16과 예외 3종

> **검토 후 변경(2026-10-29):** 구현 후 검토에서 `GPUUnavailable`(GPU 없음·수 불일치·사용 중)이 추가되었다.
> 확인 코드는 장치 수를 assert하지 않고 `devices`로 보고하며, 결과 줄은 `TF_PROBE ` 접두어로 출력한다.
> 아래 코드 블록은 최초 계획이며, 확정 동작은 커밋 `dec7eb9`와 설계 4.7을 따른다.

**Files:**
- Modify: `src/tuning_factory/desktop_runtime.py:41-43` (예외), `:147-183` (`check`)
- Test: `tests/test_desktop_runtime.py`, `tests/test_runtime_profiles.py:11-27`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop_runtime.py` 끝에 추가

```python
def ready_reply(runtime, lock="containers/gpu-requirements.lock", engine="0.9.5", **extra):
    from tuning_factory.desktop import assets_root
    from tuning_factory.registry import digest
    return {"source": runtime.source_hash(), "engine": engine, "cuda": True,
            "lock": digest((assets_root() / lock).read_bytes()), "arch_ok": True, "kernel_ok": True,
            "bf16": True, "capability": "12.0", **extra}


def test_check_separates_a_missing_runtime_from_a_broken_one(monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    calls = []

    def absent(client, argv, **kwargs):
        calls.append(argv)
        if argv[0] == "sh":
            return ""
        raise runtime.RemoteCommandFailed("GPU runtime command failed. not found")

    monkeypatch.setattr(runtime, "command", absent)
    with pytest.raises(runtime.RuntimeMissing, match="GPU runtime command failed"):
        runtime.check(None)
    assert calls[-1] == ["test", "-x", runtime.runtime_path("sft") + "/venv/bin/python"]

    def broken(client, argv, **kwargs):
        if argv[0] in {"sh", "test"}:
            return ""
        raise runtime.RemoteCommandFailed("GPU runtime command failed. ImportError")

    monkeypatch.setattr(runtime, "command", broken)
    with pytest.raises(runtime.RuntimeMismatch, match="preflight mismatch"):
        runtime.check(None)


@pytest.mark.parametrize("field", ["arch_ok", "kernel_ok"])
def test_check_refuses_a_gpu_whose_kernels_cannot_run(monkeypatch, field):
    from tuning_factory import desktop_runtime as runtime

    reply = ready_reply(runtime, **{field: False})
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: "" if argv[0] == "sh" else json.dumps(reply))
    with pytest.raises(runtime.GPUIncompatible, match="CUDA 연산"):
        runtime.check(None)


@pytest.mark.parametrize("capability,arch,ok", [
    ((8, 9), ["sm_75", "sm_80", "sm_86", "sm_90", "sm_100", "sm_120"], True),   # Ada runs sm_86 machine code
    ((12, 0), ["sm_80", "sm_90", "compute_90"], True),                           # PTX JIT
    ((12, 0), ["sm_80", "sm_86", "sm_90"], False),                               # the cu126 situation
    ((7, 0), ["sm_75", "sm_80"], False)])
def test_arch_probe_applies_the_same_major_rule(capability, arch, ok):
    import re
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    class Tensor:
        def __mul__(self, other):
            return self

        def sum(self):
            return SimpleNamespace(item=lambda: 16)

    cuda = SimpleNamespace(is_available=lambda: True, get_device_capability=lambda: capability,
                           get_arch_list=lambda: arch, is_bf16_supported=lambda: True)
    namespace = {"torch": SimpleNamespace(cuda=cuda, ones=lambda n, device: Tensor()), "re": re}
    exec(runtime.ARCH_PROBE, namespace)
    assert namespace["arch_ok"] is ok and namespace["kernel_ok"] is True
    assert namespace["capability"] == "%d.%d" % capability
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_desktop_runtime.py -k "missing_runtime or kernels or arch_probe" -v`
Expected: FAIL (`AttributeError: ... 'RuntimeMissing'`)

- [ ] **Step 3: 예외·문구 추가** — `desktop_runtime.py`의 `class RemoteCommandFailed` 바로 아래

```python
RUNTIME_MISSING = ("GPU runtime command failed. The private runtime is not prepared on this host; "
                   "prepare runtime first")
RUNTIME_MISMATCH = "GPU runtime source, lock, engine or CUDA preflight mismatch; prepare runtime first"
GPU_INCOMPATIBLE = "이 GPU에서 앱 학습 환경의 CUDA 연산이 실행되지 않습니다. 지원하는 GPU 세대인지 확인하세요."


class RuntimeMissing(RemoteCommandFailed):
    """The private runtime's interpreter is absent on the GPU host."""


class RuntimeMismatch(ValueError):
    """The private runtime exists but its source, lock, engine or CUDA availability differs, or its probe failed."""


class GPUIncompatible(ValueError):
    """The installed runtime cannot run CUDA kernels on this GPU."""


# gpu_compat.capability_supported's rule, run inside the private runtime: same-major machine code with a lower or
# equal minor, or PTX at or below the device. A failing kernel is reported, never raised.
ARCH_PROBE = (
    "arch_ok=kernel_ok=bf16=False\n"
    "capability=None\n"
    "if torch.cuda.is_available():\n"
    " cap=tuple(torch.cuda.get_device_capability())\n"
    " capability='%d.%d'%cap\n"
    " codes=[re.fullmatch(r'(sm|compute)_(\\d+)(\\d)[a-z]?',a) for a in torch.cuda.get_arch_list()]\n"
    " codes=[(m[1],int(m[2]),int(m[3])) for m in codes if m]\n"
    " arch_ok=any(k=='sm' and j==cap[0] and n<=cap[1] for k,j,n in codes)"
    " or any(k=='compute' and (j,n)<=cap for k,j,n in codes)\n"
    " try:\n"
    "  kernel_ok=(torch.ones(8,device='cuda')*2).sum().item()==16\n"
    "  bf16=bool(torch.cuda.is_bf16_supported())\n"
    " except Exception:\n"
    "  kernel_ok=False\n"
)
```

- [ ] **Step 4: `check()` 전체를 다음으로 바꾼다**

```python
def check(client, training_stage="sft", remote_root=REMOTE, selected_gpu_uuids=(), *, model_ops=False):
    """Probe the private runtime. ``model_ops=True`` (merge/materialize) also requires the installed
    MODEL_OPS_PINS versions. Raises RuntimeMissing, RuntimeMismatch or GPUIncompatible."""
    profile = engine_profile(training_stage)
    if model_ops and profile["profile"] != "primary":
        raise ValueError(MODEL_OPS_RUNTIME)
    prerequisites(client)
    root = runtime_path(training_stage, remote_root)
    lock_name = Path(profile["lock_file"]).name
    pins = (",model_ops=dict((n,importlib.metadata.version(n).split('+')[0]) for n in "
            + repr(tuple(MODEL_OPS_PINS)) + ")") if model_ops else ""
    probe = (
        "import json,re,torch,importlib.metadata\n"
        + ("from llamafactory.train.ppo.trainer import CustomPPOTrainer\n" if training_stage == "ppo" else "")
        + "from pathlib import Path\n"
        "from tuning_factory.manifest import source_hash\n"
        "from tuning_factory.registry import digest\n"
        + ("assert torch.cuda.device_count()==" + str(len(selected_gpu_uuids)) + "\n" if selected_gpu_uuids else "")
        + ARCH_PROBE
        + "print(json.dumps(dict(source=source_hash(),engine=importlib.metadata.version('llamafactory'),"
        "cuda=torch.cuda.is_available(),lock=digest(Path(" + repr(root + "/" + lock_name) + ").read_bytes()),"
        "arch_ok=arch_ok,kernel_ok=kernel_ok,bf16=bf16,capability=capability" + pins + ")))"
    )
    python = root + "/venv/bin/python"
    argv = [python, "-c", probe]
    if selected_gpu_uuids:
        argv = ["env", "CUDA_VISIBLE_DEVICES=" + ",".join(selected_gpu_uuids), *argv]
    try:
        output = command(client, argv)
    except RemoteCommandFailed:
        try:
            command(client, ["test", "-x", python])
        except RemoteCommandFailed:
            raise RuntimeMissing(RUNTIME_MISSING) from None
        raise RuntimeMismatch(RUNTIME_MISMATCH) from None
    result = json.loads(output.splitlines()[-1])
    from .desktop import assets_root
    expected = hashlib.sha256((assets_root() / profile["lock_file"]).read_bytes()).hexdigest()
    base = {key: result.get(key) for key in ("source", "engine", "cuda", "lock")}
    if (base != {"source": source_hash(), "engine": profile["engine_version"], "cuda": True, "lock": expected}
            or ("model_ops" in result and not model_ops)):
        raise RuntimeMismatch(RUNTIME_MISMATCH)
    if model_ops and result.get("model_ops") != dict(MODEL_OPS_PINS):
        raise ValueError(MODEL_OPS_RUNTIME)
    if result.get("arch_ok") is not True or result.get("kernel_ok") is not True:
        raise GPUIncompatible(GPU_INCOMPATIBLE)
    return result
```

- [ ] **Step 5: 기존 시험을 새 확인 결과에 맞춘다** (의도된 변경)

`tests/test_desktop_runtime.py`:
- `test_runtime_accepts_compiler_with_matching_cuda_and_lock`: `ready = {...}`를 `ready = ready_reply(runtime)`로 바꾼다.
- `test_check_model_ops_pins_only_when_requested`: `ready = {...}`를 `ready = ready_reply(runtime)`로 바꾼다.
  `legacy = (...)`부터 `assert probes[0] == legacy` 줄까지를 다음으로 바꾼다.

```python
    import ast
    ast.parse(probes[0])
    assert "arch_ok=arch_ok,kernel_ok=kernel_ok" in probes[0] and "model_ops" not in probes[0]
    assert repr(runtime.runtime_path("sft") + "/gpu-requirements.lock") in probes[0]
```

  같은 시험의 `and probes[1] != legacy`는 `and probes[1] != probes[0]`로 바꾼다.

`tests/test_runtime_profiles.py::test_ppo_preflight_uses_exact_legacy_lock`: `expected = {...}`에
`'arch_ok': True, 'kernel_ok': True, 'bf16': True, 'capability': '9.0'`를 더한다.

- [ ] **Step 6: 통과 확인**

Run: `uv run pytest tests/test_desktop_runtime.py tests/test_runtime_profiles.py tests/test_gpu_servers.py tests/test_desktop_servers.py tests/test_executor_profiles.py -q`
Expected: PASS

- [ ] **Step 7: Commit**

```bash
git add src/tuning_factory/desktop_runtime.py tests/test_desktop_runtime.py tests/test_runtime_profiles.py
git commit -m "feat: verify GPU architecture support and a real CUDA kernel in the runtime check"
```

---

### Task 9: GPU 정보 수집·저장, `install_runtime`, `runtime()` 상태 보고

**Files:**
- Modify: `src/tuning_factory/desktop_runtime.py` (`runtime()` 186-245를 교체하고 새 함수 추가)
- Test: `tests/test_desktop_runtime.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop_runtime.py` 끝에 추가

```python
SMI = ("GPU-aaaa, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n"
       "GPU-bbbb, NVIDIA RTX PRO 4000 Blackwell, 12.0, 24467, 595.91.07\n")
POD = {"id": "borrowed", "gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1},
       "costPerHr": 0.57}


def test_observation_keys_and_gpu_selection(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.gpu_compat import GPU_QUERY

    assert runtime.observation_key({"pod_id": "yuft63rjggvh5z"}) == "runpod-yuft63rjggvh5z"
    with pytest.raises(ValueError, match="Pod ID"):
        runtime.observation_key({"pod_id": "../x"})
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI if tuple(argv) == GPU_QUERY else "")
    assert [gpu["uuid"] for gpu in runtime.read_gpu_facts(None, ("GPU-bbbb",))] == ["GPU-bbbb"]
    with pytest.raises(ValueError, match="selected GPU"):
        runtime.read_gpu_facts(None, ("GPU-cccc",))


def test_observe_saves_facts_and_tolerates_an_unreadable_query(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    from tuning_factory.gpu_compat import load_observation

    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    observation = runtime.observe(None, {"pod_id": "borrowed"}, tmp_path, POD)
    assert observation["pod"]["hourly_cost_usd"] == 0.57 and len(observation["gpus"]) == 2
    assert load_observation(tmp_path, "runpod-borrowed") == observation

    def refused(client, argv, **kwargs):
        raise runtime.RemoteCommandFailed("GPU runtime command failed. Field compute_cap is not valid")

    monkeypatch.setattr(runtime, "command", refused)
    assert runtime.observe(None, {"pod_id": "borrowed"}, tmp_path, POD)["gpus"] == []


def test_runtime_check_reports_a_missing_runtime_with_gpu_facts(tmp_path, monkeypatch):
    from contextlib import contextmanager

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, object()

    def missing(*args, **kwargs):
        raise runtime.RuntimeMissing(runtime.RUNTIME_MISSING)

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", missing)
    report = runtime.runtime({"pod_id": "borrowed"}, tmp_path)
    assert report["ready"] is False and report["runtime_state"] == "missing" and report["pod_id"] == "borrowed"
    assert report["observation"]["gpus"][0]["capability"] == "12.0"
    monkeypatch.setattr(runtime, "check", lambda *a, **kw: {"source": "fixture"})
    ready = runtime.runtime({"pod_id": "borrowed"}, tmp_path)
    assert ready["ready"] is True and ready["runtime_state"] == "ready" and ready["source"] == "fixture"


def test_install_runtime_uses_the_cu128_backend(tmp_path, monkeypatch):
    from test_remote_worker import FakeSSH

    from tuning_factory import desktop
    from tuning_factory import desktop_runtime as runtime

    assets = tmp_path / "assets"
    (assets / "dist").mkdir(parents=True)
    (assets / "containers").mkdir()
    (assets / "dist/llm_tuning_factory-fixture.whl").write_text("fixture")
    (assets / "containers/gpu-requirements.lock").write_text("fixture")
    monkeypatch.setattr(desktop, "assets_root", lambda: assets)
    client = FakeSSH(tmp_path / "remote")
    client.sftp.root.mkdir()
    scripts = []
    monkeypatch.setattr(runtime, "command", lambda _, argv, **kw: scripts.append(argv[2]) or "")
    preparation = {"started": False, "stopped": False}
    runtime.install_runtime(client, "sft", runtime.REMOTE, POD, preparation)
    assert preparation == {"started": True, "stopped": True}
    assert "--require-hashes --torch-backend cu128 " in scripts[0]
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_desktop_runtime.py -k "observation or observe or missing_runtime_with or cu128" -v`
Expected: FAIL (`AttributeError: ... 'observation_key'`)

- [ ] **Step 3: 새 함수 추가** — `desktop_runtime.py`, `def prerequisites` 위

```python
def observation_key(settings):
    """The local GPU observation key of the saved destination; never contacts it."""
    if settings.get("provider", "existing_runpod") == "ssh_server":
        return "server-" + server_settings(settings).destination_sha256[:16]
    pod_id = str(settings.get("pod_id", ""))
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", pod_id):
        raise ValueError("invalid Pod ID")
    return "runpod-" + pod_id


def read_gpu_facts(client, gpu_uuids=()):
    from .gpu_compat import GPU_QUERY, parse_gpu_facts
    gpus = parse_gpu_facts(command(client, list(GPU_QUERY)))
    if not gpu_uuids:
        return gpus
    selected = [gpu for gpu in gpus if gpu["uuid"] in set(gpu_uuids)]
    if len(selected) != len(set(gpu_uuids)):
        raise ValueError("selected GPU missing from nvidia-smi output")
    return selected


def observe(client, settings, state_root, pod):
    """Read (read-only nvidia-smi) and store this destination's GPU facts. An unreadable query is recorded as no
    facts, so the compatibility judge warns and the runtime check still decides."""
    from .gpu_compat import make_observation, save_observation
    server = isinstance(pod, SSHServerFacts)
    try:
        gpus = read_gpu_facts(client, server_settings(settings).selected_gpu_uuids if server else ())
    except ValueError:   # RemoteCommandFailed included
        gpus = []
    observation = make_observation(observation_key(settings), gpus, None if server else pod)
    save_observation(state_root, observation)
    return observation


def install_runtime(client, training_stage, remote_root, pod, preparation):
    """Upload the pinned lock and worker wheel, then install the private runtime below ``remote_root``.

    The caller holds ``preparation_guard`` and has run prerequisites() and bootstrap_prerequisites()."""
    from .desktop import assets_root
    from .runtime_profiles import runtime_capabilities
    profile = engine_profile(training_stage)
    lock_name = Path(profile["lock_file"]).name
    backend = runtime_capabilities(profile["profile"])["torch_backend"]
    assets = assets_root()
    wheels = list((assets / "dist").glob("llm_tuning_factory-*.whl"))
    if len(wheels) != 1:
        raise ValueError("matching worker wheel missing; rebuild desktop package")
    root = runtime_path(training_stage, remote_root)
    helper = SSHExecutor("", 22, "root", "", client=client, pod=None if isinstance(pod, SSHServerFacts) else pod)
    with client.open_sftp() as sftp:
        helper.mkdir(sftp, root)
        for path in [assets / profile["lock_file"], wheels[0]]:
            sftp.put(str(path), root + "/" + path.name)
    # All installs, Python downloads and caches stay below the private root.
    script = (
        "set -eu\n"
        "command -v flock >/dev/null\n"
        f"exec 9>{shlex.quote(root + '/prepare.lock')}\nflock -n 9\n"
        f"export UV_PYTHON_INSTALL_DIR={shlex.quote(remote_root + '/python')}\n"
        f"export UV_CACHE_DIR={shlex.quote(remote_root + '/uv-cache')}\n"
        f"python3 -m pip install --disable-pip-version-check --no-cache-dir "
        f"--target {shlex.quote(root + '/bootstrap')} uv==0.11.23\n"
        f"export PYTHONPATH={shlex.quote(root + '/bootstrap')}\n"
        f"python3 -m uv venv --python 3.12 --allow-existing {shlex.quote(root + '/venv')}\n"
        f"python3 -m uv pip sync --python {shlex.quote(root + '/venv/bin/python')} --require-hashes "
        f"--torch-backend {backend} {shlex.quote(root + '/' + lock_name)}\n"
        f"python3 -m uv pip install --python {shlex.quote(root + '/venv/bin/python')} --no-deps "
        f"{shlex.quote(root + '/' + wheels[0].name)}\n"
    )
    preparation["started"] = True
    try:
        command(client, ["sh", "-c", script], timeout=1800)
    except RemoteCommandFailed:
        preparation["stopped"] = True
        raise
    preparation["stopped"] = True
```

- [ ] **Step 4: `runtime()` 전체를 다음으로 바꾼다**

```python
def runtime(settings, state_root, prepare=False, training_stage="sft", *, inspection=None):
    """Check (or, approved, prepare) the private runtime and record this destination's GPU facts.

    A check reports ``runtime_state`` ("ready", "missing", "mismatch", "incompatible" or "unavailable") instead of
    raising."""
    if settings.get("provider") == "ssh_server" and prepare:
        if inspection is None:
            raise ValueError("approved destination inspection required for server preparation")
        inspection = SSHServerInspection.model_validate(inspection.model_dump()
            if hasattr(inspection, "model_dump") else inspection)
        inspection.validate_settings(server_settings(settings))
    profile = engine_profile(training_stage)
    with connection(settings) as (pod, client):
        generic = isinstance(pod, SSHServerFacts)
        config = server_settings(settings) if generic else None
        remote_root = config.remote_root if generic else REMOTE
        selected = config.selected_gpu_uuids if generic else ()
        if generic and inspection is not None and (inspection.server_id != pod.server_id
                                                   or inspection.inventory != pod.inventory):
            raise ValueError("server hardware differs from approved preparation inspection")
        observation = observe(client, settings, state_root, pod)
        state = "ready"
        if prepare:
            with preparation_guard(state_root, config.profile(pod) if generic else None, pod) as preparation:
                prerequisites(client)
                bootstrap_prerequisites(client)
                install_runtime(client, training_stage, remote_root, pod, preparation)
                result = check(client, training_stage, remote_root, selected)
        else:
            try:
                result = check(client, training_stage, remote_root, selected)
            except RuntimeMissing:
                result, state = {}, "missing"
            except RuntimeMismatch:
                result, state = {}, "mismatch"
            except GPUIncompatible:
                result, state = {}, "incompatible"
            except GPUUnavailable:
                result, state = {}, "unavailable"
        report = {"ready": state == "ready", "runtime_state": state, "observation": observation,
                  "profile": profile["profile"], **result}
        if generic:
            return {**report, "provider": "ssh_server", "facts": pod.model_dump(mode="json"),
                    "runtime": runtime_path(training_stage, remote_root)}
        return {**report, "pod_id": pod["id"], "runtime": runtime_path(training_stage)}
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_desktop_runtime.py tests/test_gpu_servers.py tests/test_desktop_servers.py tests/test_desktop.py -q`
Expected: PASS. `test_gpu_servers.py`의 준비 시험은 모든 원격 명령에 `""`를 돌려준다. 그래서 GPU 정보 읽기는 빈 목록으로 기록되고 시험은 그대로 통과해야 한다.

- [ ] **Step 6: Commit**

```bash
git add src/tuning_factory/desktop_runtime.py tests/test_desktop_runtime.py
git commit -m "feat: record GPU facts in runtime checks and report a missing runtime instead of failing"
```

---

### Task 10: `executor_for` — 실행 게이트와 RunPod 실행 중 준비

> **검토 후 변경(2026-10-29, 커밋 `f0ffcb0`):** GPU 서버는 `server_receipt.validate_destination`을 게이트보다 먼저 한다.
> 준비 후 확인이 `RuntimeMissing`·`RuntimeMismatch`면 한국어 `POST_PREPARATION_FAILED`로 알린다(재시도 없음).
> 설치 스크립트는 `flock -n 9 || { echo TF_PREPARE_BUSY; exit 75; }`이고, 이를 한국어 `PREPARATION_BUSY`로 바꾼다.
> `GPUIncompatible`·`GPUUnavailable`은 준비를 시작하지 않는다는 점을 시험으로 고정했다. 아래 코드 블록은 최초 계획이다.

**Files:**
- Modify: `src/tuning_factory/desktop_runtime.py` (`executor_for`)
- Test: `tests/test_desktop_runtime.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop_runtime.py` 끝에 추가

```python
def runpod_executor_fixture(monkeypatch, states, events):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime as runtime

    @contextmanager
    def connect(settings, **kwargs):
        yield POD, SimpleNamespace(close=lambda: None)

    def check(client, stage, *args, **kwargs):
        events.append("check")
        if states.pop(0) == "missing":
            raise runtime.RuntimeMissing(runtime.RUNTIME_MISSING)
        return {}

    monkeypatch.setattr(runtime, "connection", connect)
    monkeypatch.setattr(runtime, "command", lambda client, argv, **kw: SMI)
    monkeypatch.setattr(runtime, "check", check)
    monkeypatch.setattr(runtime, "prerequisites", lambda client: events.append("prerequisites"))
    monkeypatch.setattr(runtime, "bootstrap_prerequisites", lambda client: events.append("bootstrap"))
    monkeypatch.setattr(runtime, "install_runtime", lambda *args: events.append("install"))
    return runtime


def test_executor_for_gates_first_and_prepares_only_when_approved(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)

    def gate(observation):
        events.append(("gate", observation["gpus"][0]["capability"]))

    with pytest.raises(ValueError, match="학습 환경이 없습니다"):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate):
            pytest.fail("ran without a runtime")
    assert events == [("gate", "12.0"), "check"]
    events.clear()
    runtime = runpod_executor_fixture(monkeypatch, ["missing", "ready"], events)
    with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate, prepare_runtime=True) as executor:
        assert executor.pod == POD
    assert events == [("gate", "12.0"), "check", "prerequisites", "bootstrap", "install", "check"]


def test_a_gate_refusal_stops_before_any_runtime_work(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["ready"], events)

    def gate(observation):
        raise ValueError("실행할 수 없습니다. fixture")

    with pytest.raises(ValueError, match="fixture"):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path, gate=gate, prepare_runtime=True):
            pytest.fail("refused destination used")
    assert events == []


def test_without_a_gate_the_missing_runtime_error_is_unchanged(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["missing"], events)
    with pytest.raises(runtime.RuntimeMissing):
        with runtime.executor_for({"pod_id": "borrowed"}, tmp_path):
            pytest.fail("ran without a runtime")


def test_execution_preparation_is_runpod_preflight_only(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime as runtime

    monkeypatch.setattr(runtime, "connection", lambda *a, **kw: pytest.fail("GPU host contacted"))
    for settings, kwargs in (({"provider": "ssh_server"}, {}), ({"pod_id": "borrowed"}, {"preflight": False})):
        with pytest.raises(ValueError, match="RunPod preflight only"):
            with runtime.executor_for(settings, tmp_path, prepare_runtime=True, **kwargs):
                pytest.fail("prepared outside a RunPod preflight")
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_desktop_runtime.py -k "gate or preparation_is_runpod or unchanged" -v`
Expected: FAIL (`TypeError: executor_for() got an unexpected keyword argument 'gate'`)

- [ ] **Step 3: 문구 추가** — `GPU_INCOMPATIBLE` 아래

```python
RUNTIME_PREPARATION_REQUIRED = ("Pod에 이 앱 버전의 학습 환경이 없습니다. 실행 승인에서 학습 환경 준비를 함께 "
                                "승인하거나 연결 설정의 '학습 환경 준비'를 먼저 누르세요.")
SERVER_RUNTIME_REQUIRED = ("GPU 서버에 이 앱 버전의 학습 환경이 없습니다. 연결 설정에서 GPU 목록을 조회한 뒤 "
                           "'학습 환경 준비'를 먼저 누르세요.")
PREPARE_IN_EXECUTION_RUNPOD_ONLY = "runtime preparation during execution applies to a RunPod preflight only"
```

- [ ] **Step 4: `executor_for` 수정** — 서명, 문서 문자열 끝, 그리고 연결 이후 부분

서명:

```python
@contextmanager
def executor_for(settings, state_root, preflight=True, training_stage="sft", *, server_receipt=None,
                 recovery=False, runtime_profile=None, model_ops=False, profile_identity=None,
                 gate=None, prepare_runtime=False):
```

문서 문자열 마지막 문장 뒤에 추가:

```text
    ``gate`` (the desktop's compatibility judge) receives this destination's GPU observation before any runtime
    work and raises to refuse it. ``prepare_runtime`` (RunPod preflight only) installs a missing or mismatched
    private runtime instead of refusing.
```

`pins = ...` 줄 바로 위에 추가:

```python
    if prepare_runtime and (recovery or not preflight
                            or settings.get("provider", "existing_runpod") == "ssh_server"):
        raise ValueError(PREPARE_IN_EXECUTION_RUNPOD_ONLY)
```

`with connection(settings) as (pod, client):` 블록 전체를 다음으로 바꾼다.

```python
    with connection(settings) as (pod, client):
        if gate is not None:
            gate(observe(client, settings, state_root, pod))
        if isinstance(pod, SSHServerFacts):
            config = server_settings(settings)
            if server_receipt is not None:
                server_receipt.validate_destination(server_receipt.manifest_sha256, config.transport, pod)
            if preflight:
                try:
                    check(client, training_stage, config.remote_root, config.selected_gpu_uuids, **pins)
                except (RuntimeMissing, RuntimeMismatch):
                    if gate is None:
                        raise
                    raise ValueError(SERVER_RUNTIME_REQUIRED) from None
            root = runtime_path(training_stage, config.remote_root)
            lock_name = Path(engine_profile(training_stage)["lock_file"]).name
            executor = SSHExecutor("", 22, config.ssh_username, "", client=client,
                                   server_profile=server_receipt.profile if server_receipt else config.profile(
                                       pod, **identity),
                                   server_facts=pod,
                                   remote_root=config.remote_root + "/jobs", remote_python=root + "/venv/bin/python",
                                   worker_lock_file=root + "/" + lock_name, hf_home=config.remote_root + "/hf-cache",
                                   model_cache_root=config.remote_root + "/model-cache")
            yield executor
            return
        if preflight:
            try:
                check(client, training_stage, **pins)
            except (RuntimeMissing, RuntimeMismatch):
                if not prepare_runtime:
                    if gate is None:
                        raise
                    raise ValueError(RUNTIME_PREPARATION_REQUIRED) from None
                with preparation_guard(state_root, None, pod) as preparation:
                    prerequisites(client)
                    bootstrap_prerequisites(client)
                    install_runtime(client, training_stage, REMOTE, pod, preparation)
                check(client, training_stage, **pins)
        root = runtime_path(training_stage)
        lock_name = Path(engine_profile(training_stage)["lock_file"]).name
        executor = SSHExecutor("", 22, "root", "", client=client, pod=pod,
                               remote_root=REMOTE + "/jobs", remote_python=root + "/venv/bin/python",
                               worker_lock_file=root + "/" + lock_name, hf_home=REMOTE + "/hf-cache",
                               model_cache_root=REMOTE + "/model-cache")
        yield executor
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_desktop_runtime.py tests/test_executor_profiles.py tests/test_cli_graph.py tests/test_simulated_dispatch.py -q`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/tuning_factory/desktop_runtime.py tests/test_desktop_runtime.py
git commit -m "feat: gate executors on GPU compatibility and prepare a RunPod runtime only when approved"
```

---

### Task 11: 데스크톱 동작 — 계획 호환성, 환경 확인 판정, 실행 게이트

**Files:**
- Modify: `src/tuning_factory/desktop.py` (import, 상수, `plan`, `execute`, `runtime_check`, 새 메서드)
- Test: `tests/test_desktop.py`

- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop.py` 끝에 추가

```python
BLACKWELL = {"uuid": "GPU-1", "name": "NVIDIA RTX PRO 4000 Blackwell", "capability": "12.0", "memory_mib": 24467,
             "driver": "595.91.07"}
POD_API = {"gpu": {"id": "NVIDIA RTX PRO 4000 Blackwell", "displayName": None, "count": 1}, "costPerHr": 0.57}
A4000_JOB = {"training_stage": "sft", "base_model": "Qwen/Qwen2.5-0.5B-Instruct",
             "resource_profile": {"gpu_type": "NVIDIA RTX A4000", "gpu_count": 1, "hourly_cost_usd": 0.5}}


def finding_codes(findings):
    return {(item["level"], item["code"]) for item in findings}


def test_plan_reports_gpu_compatibility_from_the_stored_observation(tmp_path):
    from tuning_factory.gpu_compat import make_observation, save_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    catalog = app.dispatch("catalog", {})
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    source = tmp_path / "input.json"
    source.write_text(json.dumps([{"instruction": "question", "output": "answer"}]))
    app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    first = app.dispatch("plan", {"config": config})["compatibility"]
    assert first["observed_at"] is None and ("warn", "OBSERVATION_MISSING") in finding_codes(first["findings"])
    save_observation(tmp_path, make_observation("runpod-borrowed", [BLACKWELL], POD_API))
    second = app.dispatch("plan", {"config": config})["compatibility"]
    assert second["observed_at"] > 0
    assert ("block", "GPU_TYPE_MISMATCH") in finding_codes(second["findings"])   # the template asks for an A4000
    assert app.dispatch("jobs", {}) == []


def test_runtime_check_judges_a_queued_job_or_a_plan_config(tmp_path, monkeypatch):
    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_compat import make_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    job = app.factory.store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    observation = make_observation("runpod-borrowed", [BLACKWELL], POD_API)
    monkeypatch.setattr(desktop_runtime, "runtime", lambda *a, **kw: {"ready": False, "runtime_state": "missing",
                                                                     "observation": observation})
    report = app.dispatch("runtime_check", {"training_stage": "sft", "job_id": job["id"]})
    assert {("block", "GPU_TYPE_MISMATCH"), ("block", "PRICE_OVER_BUDGET")} <= finding_codes(report["findings"])
    assert "findings" not in app.dispatch("runtime_check", {"training_stage": "sft"})


def test_execute_gates_the_job_and_forwards_runtime_preparation(tmp_path, monkeypatch):
    from contextlib import contextmanager
    from types import SimpleNamespace

    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_compat import make_observation
    app = desktop.DesktopService(tmp_path, {"pod_id": "borrowed"})
    store = app.factory.store
    job = store.submit({"sha256": "a4000", "simulation": False, "config": A4000_JOB}, "a4000")
    store.transition(job["id"], "QUEUED")
    seen = {}

    @contextmanager
    def fake_executor(settings, root, **kwargs):
        seen.update(kwargs)
        kwargs["gate"](make_observation("runpod-borrowed", [BLACKWELL], POD_API))
        yield SimpleNamespace(remote=True)

    monkeypatch.setattr(desktop_runtime, "executor_for", fake_executor)
    monkeypatch.setattr(app.factory, "run_claimed", lambda *args: pytest.fail("refused job launched"))
    with pytest.raises(ValueError, match="실행할 수 없습니다"):
        app.dispatch("execute", {"approved": True, "job_id": job["id"], "prepare_runtime": True})
    assert seen["prepare_runtime"] is True and store.get(job["id"])["attempts"] == 0
    server = desktop.DesktopService(tmp_path / "server", {"provider": "ssh_server"})
    with pytest.raises(ValueError, match="GPU 서버는 실행 승인에서"):
        server.dispatch("execute", {"approved": True, "job_id": "0" * 32, "prepare_runtime": True})
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_desktop.py -k "compatibility or judges or gates_the_job" -v`
Expected: FAIL (`KeyError: 'compatibility'`)

- [ ] **Step 3: import와 상수** — `desktop.py`

`from .runner import Factory` 아래에 추가:

```python
from .runtime_profiles import engine_profile
```

`SERVER_RESERVED = (...)` 아래에 추가:

```python
SERVER_PREPARE_IN_EXECUTION = ("GPU 서버는 실행 승인에서 학습 환경을 함께 준비하지 않습니다. 연결 설정에서 GPU 목록을 "
                               "조회한 뒤 '학습 환경 준비'를 먼저 누르세요.")
POD_RESERVED_FOR_PREPARATION = "Pod is reserved by a job; reconcile and finish it before preparing runtime"
```

`runtime_prepare` 분기의 같은 영문 문자열도 `POD_RESERVED_FOR_PREPARATION`으로 바꾼다.

- [ ] **Step 4: 새 메서드** — `DesktopService`의 `require_server` 아래에 추가

```python
    def compatibility(self, config, observation, stage):
        """gpu_compat findings for one job config; catalog sizes feed the memory estimate."""
        from .gpu_compat import evaluate
        from .runtime_profiles import runtime_capabilities
        profile = engine_profile(config.get("training_stage", "sft"))["profile"]
        model = next((item for item in model_catalog() if item["id"] == config.get("base_model")), None)
        return evaluate(config, observation, runtime_capabilities(profile), model, stage=stage)

    def plan_compatibility(self, config):
        """Offline: the plan judges the last stored GPU observation of the saved destination."""
        from .desktop_runtime import observation_key
        from .gpu_compat import load_observation
        try:
            key = observation_key(self.settings)
        except (ValueError, TypeError):   # incomplete connection settings: nothing was observed yet
            key = None
        observation = load_observation(self.root, key) if key else None
        return {"observed_at": (observation or {}).get("observed_at"),
                "findings": self.compatibility(config, observation, "plan")}

    def add_findings(self, report, data):
        """A runtime check for a queued job (job_id) or an unsubmitted plan (config) also judges that config."""
        if data.get("job_id"):
            self.job_directory(data["job_id"])
            config = self.factory.store.get(data["job_id"])["manifest"]["config"]
        elif data.get("config"):
            config = AIConfig.model_validate(data["config"]).model_dump(mode="json")
        else:
            return
        if report.get("observation") is not None:
            report["findings"] = self.compatibility(config, report["observation"], "execute")

    def execution_gate(self, config):
        from .gpu_compat import refusal

        def gate(observation):
            message = refusal(self.compatibility(config, observation, "execute"))
            if message:
                raise ValueError(message)
        return gate
```

- [ ] **Step 5: `plan` 분기** — `return result` 바로 위에 추가

```python
            result["compatibility"] = self.plan_compatibility(manifest["config"])
```

- [ ] **Step 6: `execute` 분기 전체를 다음으로 바꾼다**

```python
        if action == "execute":
            self.require_approval(data)
            self.require_tracking_ready()
            from .desktop_runtime import executor_for
            server = self.is_server()
            prepare = data.get("prepare_runtime") is True
            if prepare and server:
                raise ValueError(SERVER_PREPARE_IN_EXECUTION)
            job_id = data["job_id"]
            self.job_directory(job_id)
            config = self.factory.store.get(job_id)["manifest"]["config"]
            stage = config.get("training_stage", "sft")
            if prepare and self.factory.store.gpu_locked("existing_runpod:" + self.settings.get("pod_id", "")):
                raise ValueError(POD_RESERVED_FOR_PREPARATION)
            gate = self.execution_gate(config)
            receipt = self.job_receipt(job_id, server)
            if server:
                with executor_for(self.settings, self.root, training_stage=stage,
                                  server_receipt=receipt, gate=gate) as executor:
                    result = self.factory.work(executor=executor, gpu=executor.server_facts.exclusivity_key,
                                               approved_transfer=True, select_mode=True, target_job_id=job_id)
                    if result is None and self.factory.store.foreign_provider_lock(
                            executor.server_facts.exclusivity_key):
                        raise ValueError(CROSS_PROVIDER_LOCK)
                    return result
            with executor_for(self.settings, self.root, training_stage=stage, gate=gate,
                              prepare_runtime=prepare) as executor:
                return self.factory.work(executor=executor, gpu="existing_runpod:" + self.settings["pod_id"],
                                         approved_transfer=True, select_mode=True, target_job_id=job_id)
```

- [ ] **Step 7: `runtime_check` 분기** — 마지막 `return runtime(self.settings, self.root, prepare=action == "runtime_prepare", ...)`를 다음으로 바꾼다

```python
            report = runtime(self.settings, self.root, prepare=action == "runtime_prepare",
                             training_stage=data.get("training_stage", "sft"))
            if action == "runtime_check":
                self.add_findings(report, data)
            return report
```

- [ ] **Step 8: 통과 확인**

Run: `uv run pytest tests/test_desktop.py tests/test_desktop_servers.py tests/test_desktop_extensions.py tests/test_desktop_tutorial.py -q`
Expected: PASS

- [ ] **Step 9: Commit**

```bash
git add src/tuning_factory/desktop.py tests/test_desktop.py
git commit -m "feat: judge plans, runtime checks and executions against the observed GPU"
```

---

### Task 12: 화면 — 계획 호환성, 실행 전 확인, 승인 창 안의 환경 준비

> **검토 후 변경(2026-10-29, 커밋 `9ad85ec`·`41c9e76`):** 실행 전 확인 중 중복 클릭을 막는 `preflightRunning` 플래그를 두고,
> 확인·승인 사이에 실행 계획이 바뀌거나(시작) 선택한 작업이 바뀌면(대기 작업 실행) 실행하지 않는다.
> 대기 작업 실행은 확인·승인한 그 작업(`approvedJob`)만 실행한다. GPU 서버의 환경 없음 안내는 연결 설정을 가리키고,
> `incompatible`·`unavailable`·알 수 없는 상태는 오류로 멈춘다. 아래 코드 블록은 최초 계획이다.

**Files:**
- Create: `desktop/src/gpu-preflight.ts`
- Modify: `desktop/src/main.ts` (import, 계획 카드 448행, `execute()` 695행, 실행 승인 943–951행, 시작·실행 1039–1042행, 환경 확인 983행), `desktop/src/style.css`
- Test: `desktop/tests/workspace.spec.ts`

- [ ] **Step 1: 실패하는 화면 시험 작성** — `desktop/tests/workspace.spec.ts` 끝에 추가

```ts
async function overrideActions(page: import("@playwright/test").Page, answers: Record<string, unknown>) {
  await page.evaluate(answers => {
    const bridge = (window as any).__TAURI_INTERNALS__;
    const original = bridge.invoke;
    (window as any).__overridden = [];
    bridge.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action in answers) {
        (window as any).__overridden.push(args);
        const answer = answers[args.action];
        return args.action === "plan" ? { ...(await original(command, args)), ...(answer as object) } : answer;
      }
      return original(command, args);
    };
  }, answers);
}

test("plan lists escaped GPU compatibility findings from the stored observation", async ({ page }) => {
  await overrideActions(page, { plan: { compatibility: { observed_at: 1793249000, findings: [
    { level: "block", code: "GPU_TYPE_MISMATCH", message: "학습 설정의 GPU 종류(<b>A4000</b>)가 다릅니다." },
    { level: "warn", code: "MODEL_UNVERIFIED", message: "이 모델은 아직 GPU에서 검증되지 않았습니다." }] } } });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  const section = page.getByTestId("plan-compatibility");
  await expect(section).toContainText("실행 시 거부될 항목");
  await expect(section).toContainText("학습 설정의 GPU 종류(<b>A4000</b>)가 다릅니다.");
  await expect(section).toContainText("주의 항목");
  await expect(section.locator("b")).toHaveCount(0);
});

test("blocking preflight findings stop the run before approval or submit", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: true, runtime_state: "ready", findings: [
    { level: "block", code: "GPU_ARCH_UNSUPPORTED", message: "이 GPU는 지원하지 않는 세대입니다." }] } });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("alert")).toContainText("이 GPU는 지원하지 않는 세대입니다.");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("a missing RunPod runtime is folded into the single run approval", async ({ page }) => {
  await overrideActions(page, {
    runtime_check: { ready: false, runtime_state: "missing", findings: [
      { level: "warn", code: "MEMORY_TIGHT", message: "예상 메모리가 GPU 메모리에 빠듯합니다." }] },
    execute: null });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("학습 환경이 없어 학습 전에 먼저 설치합니다");
  await expect(dialog).toContainText("예상 메모리가 GPU 메모리에 빠듯합니다.");
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__overridden
    .find((args: any) => args.action === "execute")?.data?.prepare_runtime)).toBe(true);
  const check = await page.evaluate(() => (window as any).__overridden.find((args: any) => args.action === "runtime_check"));
  expect(check.data.config.base_model).toBeTruthy();
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd desktop && npx playwright test tests/workspace.spec.ts -g "GPU compatibility|preflight|single run approval"`
Expected: FAIL (`plan-compatibility` 없음 / 대화창이 차단되지 않음)

- [ ] **Step 3: 화면 보조 모듈 생성** — `desktop/src/gpu-preflight.ts`

```ts
// Execution preflight: GPU compatibility findings from the sidecar, folded into the single run approval.
import { escapeHtml as esc, formatDate } from "./workbench";

type Obj = Record<string, any>;

export const RUNTIME_PREPARATION_NOTICE = "Pod에 이 앱 버전의 학습 환경이 없어 학습 전에 먼저 설치합니다. "
  + "/workspace/factory-desktop 아래에 수 GB를 내려받으며 최대 30분 걸릴 수 있고, 그동안에도 GPU 요금이 발생합니다. "
  + "빌린 Pod라면 Pod 주인의 동의를 받은 뒤 진행하세요.";
export const SERVER_RUNTIME_REQUIRED = "GPU 서버에 이 앱 버전의 학습 환경이 없습니다. "
  + "연결 설정에서 GPU 목록을 조회한 뒤 '학습 환경 준비'를 먼저 누르세요.";
export const GPU_INCOMPATIBLE = "이 GPU에서 앱 학습 환경의 CUDA 연산이 실행되지 않습니다. 지원하는 GPU 세대인지 확인하세요.";
export const GPU_UNAVAILABLE = "GPU를 사용할 수 없거나 사용 중입니다. 다른 작업이 GPU를 쓰고 있는지, Pod·서버의 GPU 상태를 확인하세요.";

const items = (findings: Obj[]) => `<ul>${findings.map(item => `<li>${esc(item.message)}</li>`).join("")}</ul>`;

/** The plan card's GPU compatibility section; empty when the sidecar sent none. */
export function gpuCompatibilityView(report: Obj | undefined): string {
  if (!report) return "";
  const findings: Obj[] = report.findings ?? [];
  const blocks = findings.filter(item => item.level === "block");
  const warns = findings.filter(item => item.level !== "block");
  return `<section class="compatibility" data-testid="plan-compatibility"><h4>GPU 호환성 점검</h4>`
    + (report.observed_at ? `<p class="muted">저장된 GPU 정보 기준 · ${esc(formatDate(report.observed_at))}</p>` : "")
    + (blocks.length ? `<p class="warning-text">실행 시 거부될 항목</p>${items(blocks)}` : "")
    + (warns.length ? `<p>주의 항목</p>${items(warns)}` : "")
    + (findings.length ? "" : "<p>저장된 GPU 정보 기준으로 문제가 없습니다.</p>")
    + "</section>";
}

/** runtime_check before a run: an error to show, or what to add to the approval and whether to prepare. */
export function preflightOutcome(report: Obj | null | undefined, server: boolean):
    { error: string } | { prepare: boolean; extra: string } {
  const findings: Obj[] = report?.findings ?? [];
  const blocks = findings.filter(item => item.level === "block");
  if (blocks.length) return { error: ["실행할 수 없습니다.", ...blocks.map(item => item.message)].join("\n") };
  const state = report?.ready === false ? report.runtime_state : "ready";
  if (state === "incompatible") return { error: GPU_INCOMPATIBLE };
  if (state === "unavailable") return { error: GPU_UNAVAILABLE };
  const missing = state === "missing" || state === "mismatch";
  if (missing && server) return { error: SERVER_RUNTIME_REQUIRED };
  const warns = findings.filter(item => item.level !== "block").map(item => "· " + item.message);
  return { prepare: missing, extra: (missing ? "\n\n" + RUNTIME_PREPARATION_NOTICE : "")
    + (warns.length ? "\n\n주의 항목\n" + warns.join("\n") : "") };
}

/** The notice after 환경 확인 or 학습 환경 준비, with the observed GPU when there is one. */
export function runtimeNotice(report: Obj, server: boolean): string {
  const gpu = report?.observation?.gpus?.[0];
  const facts = gpu ? `\nGPU: ${gpu.name} · compute capability ${gpu.capability} · `
    + `${(gpu.memory_mib / 1024).toFixed(1)}GiB · 드라이버 ${gpu.driver}` : "";
  const target = server ? "GPU 서버" : "Pod";
  if (report?.ready === false && report.runtime_state === "incompatible") return GPU_INCOMPATIBLE + facts;
  if (report?.ready === false && report.runtime_state === "unavailable") return GPU_UNAVAILABLE + facts;
  if (report?.ready === false) return `선택한 ${target}에 이 앱 버전의 학습 환경이 아직 없습니다. `
    + "'학습 환경 준비'를 누르거나 학습 실행 승인에서 함께 준비하세요." + facts;
  return `선택한 ${target}에서 앱과 일치하는 학습 환경과 CUDA를 확인했습니다.` + facts;
}
```

- [ ] **Step 4: `main.ts` 수정**

① import 줄 추가(기존 `./tutorial-steps` import 아래):

```ts
import { gpuCompatibilityView, preflightOutcome, runtimeNotice } from "./gpu-preflight";
```

② 계획 카드(448행): `· GPU: ${esc(state.plan.manifest.config.resource_profile.gpu_type)}</p>` 바로 뒤에
`${gpuCompatibilityView(state.plan.compatibility)}`를 넣는다.

③ `execute()` 함수(695행)의 서명과 호출:

```ts
async function execute(job: Obj, prepareRuntime = false) {
```

```ts
  api("execute", { job_id: job.id, approved: true, prepare_runtime: prepareRuntime }).then(async result => {
```

④ `execute()` 바로 위에 실행 전 확인 함수를 추가:

```ts
/** runtime_check before a run (read-only SSH): blocking findings stop here; a missing RunPod runtime joins the approval. */
async function executionPreflight(job: Obj | null): Promise<{ prepare: boolean; extra: string } | null> {
  const config = job ? job.manifest?.config : state.plan?.manifest?.config;
  const server = job ? !!job.server_destination : isServer(state.settings);
  state.notice = "실행 전 GPU와 학습 환경을 확인하는 중입니다."; render();
  try {
    const report = await api("runtime_check", { training_stage: config?.training_stage || "sft", ...(job ? { job_id: job.id } : { config }) });
    const outcome = preflightOutcome(report, server);
    state.notice = "";
    if ("error" in outcome) { state.error = outcome.error; render(); return null; }
    return outcome;
  } catch (error) {
    state.notice = ""; state.error = errorText(error); render(); return null;
  }
}
```

⑤ 실행 승인 블록(943–951행)을 다음으로 바꾼다:

```ts
  let prepareRuntime = false;
  if (["start", "execute", "resume"].includes(name)) {
    captureConfig();
    if (name === "start" && !state.plan) { state.error = "변경된 설정으로 실행 계획을 다시 확인하세요."; render(); return; }
    if (name === "start" && planIssues().length) { state.error = planIssues().join("\n"); render(); return; }
    // Execute and resume run where the job was approved; resume resets the job before the sidecar could refuse.
    const job = name === "start" ? null : state.jobs.find((j: Obj) => j.id === state.active);
    const issue = job ? jobDestinationIssue(job, state.settings) : null;
    if (issue) { state.error = issue; render(); return; }
    let extra = "";
    if (name !== "resume") {
      const preflight = await executionPreflight(job);
      if (!preflight) return;
      ({ prepare: prepareRuntime, extra } = preflight);
    }
    if (!await approve("실제 GPU 학습 실행", transferApproval(job ? job.server_destination ?? null : settingsRoute(state.settings)) + extra)) return;
  }
```

⑥ 시작·실행(1040·1042행): `await execute(job)` → `await execute(job, prepareRuntime)`,
`await execute(state.jobs.find((j: Obj) => j.id === state.active))` →
`await execute(state.jobs.find((j: Obj) => j.id === state.active), prepareRuntime)`. 재개(1048행)는 바꾸지 않는다.

⑦ 환경 확인(983행)의 안내 문구:

```ts
    else if (name === "runtime_check" || name === "runtime_prepare") { state.runtimeReport = await api(name, { approved: name === "runtime_prepare", training_stage: state.config?.training_stage || "sft" }); state.notice = runtimeNotice(state.runtimeReport, isServer(state.settings)); }
```

- [ ] **Step 5: 스타일** — `desktop/src/style.css` 끝에 추가

```css
.plan .compatibility{margin:12px 0}.plan .compatibility h4{margin:0 0 6px;font-size:13px}.plan .compatibility ul{margin:4px 0 10px 18px;padding:0}
```

- [ ] **Step 6: 통과 확인**

Run: `cd desktop && npm run build && npm test`
Expected: 빌드 통과, 모든 Playwright 시험 통과(새 3개 포함). `tutorial.spec.ts`는 `runtime_check`를 금지하는데, 튜토리얼은 학습을 시작하지 않으므로 그대로 통과해야 한다.

- [ ] **Step 7: Commit**

```bash
git add desktop/src/gpu-preflight.ts desktop/src/main.ts desktop/src/style.css desktop/tests/workspace.spec.ts
git commit -m "feat(desktop): show GPU compatibility and fold runtime preparation into the run approval"
```

---

### Task 13: 버전 0.5.0

**Files:**
- Modify: `desktop/package.json`, `desktop/package-lock.json`, `desktop/src-tauri/Cargo.toml:3`, `desktop/src-tauri/Cargo.lock:2031`, `desktop/src-tauri/tauri.conf.json:4`
- Test: `tests/test_release_version.py:10`

- [ ] **Step 1: 시험의 출시 버전 변경** — `tests/test_release_version.py`

```python
RELEASE = "0.5.0"
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_release_version.py -v`
Expected: FAIL (`test_every_desktop_version_field_is_the_release`)

- [ ] **Step 3: 버전 필드 변경**

```bash
cd desktop && npm version 0.5.0 --no-git-tag-version && cd ..
```

`desktop/src-tauri/Cargo.toml` 3행: `version = "0.5.0"`
`desktop/src-tauri/Cargo.lock` 2031행(`name = "llm-tuning-factory-desktop"` 다음 줄): `version = "0.5.0"`
`desktop/src-tauri/tauri.conf.json` 4행: `"version": "0.5.0",`

- [ ] **Step 4: 통과 확인**

Run: `uv run pytest tests/test_release_version.py -v && cargo metadata --manifest-path desktop/src-tauri/Cargo.toml --locked --format-version 1 --no-deps > /dev/null`
Expected: PASS, cargo 오류 없음

- [ ] **Step 5: Commit**

```bash
git add tests/test_release_version.py desktop/package.json desktop/package-lock.json desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock desktop/src-tauri/tauri.conf.json
git commit -m "chore: version the desktop app 0.5.0"
```

---

### Task 14: 운영 문서와 진행 기록, 전체 검증

**Files:**
- Modify: `docs/OPERATIONS.md`, `docs/WINDOWS_APP.md`, `docs/TRAINING_AND_STORAGE.md`, `docs/PROGRESS.md`

- [ ] **Step 1: `docs/OPERATIONS.md`**

112–113행의 문장 `including LLaMA-Factory 0.9.5, PyTorch 2.7.1 and bitsandbytes 0.48.1`을 다음으로 바꾼다.
`including LLaMA-Factory 0.9.5, PyTorch 2.7.1 (CUDA 12.8 build, cu128) and bitsandbytes 0.48.1`

123–127행의 고정 파일 재생성 안내를 다음으로 바꾼다.

````markdown
Regenerate the Linux dependency locks deliberately, over the existing files so current pins stay preferred:

```powershell
uv pip compile pyproject.toml containers/gpu-requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --torch-backend cu128 --output-file containers/gpu-requirements.lock --no-header
uv pip compile pyproject.toml containers/gpu-ppo-requirements.in --override containers/gpu-ppo-overrides.txt --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --torch-backend cu128 --output-file containers/gpu-ppo-requirements.lock --no-header
uv run python scripts/inspect_gpu_wheels.py
```

The lock records no index URL, so every install of it passes the same `--torch-backend cu128` (`uv pip sync`) or
`--extra-index-url https://download.pytorch.org/whl/cu128` (pip). `inspect_gpu_wheels.py` must exit 0: the torch
wheel's compiled architectures cover Ampere (8.0, 8.6), Ada (8.9), Hopper (9.0) and Blackwell (10.0, 12.0), and
bitsandbytes ships a CUDA 12.8 library. Copy its `sass`/`ptx` into `runtime_profiles.RUNTIME_CAPABILITIES`.
````

- [ ] **Step 2: `docs/WINDOWS_APP.md`** — "업그레이드 시 주의" 절 바로 앞에 새 절 추가

```markdown
## 실행 전 GPU 점검 (0.5.0)

학습 환경은 PyTorch 2.7.1 CUDA 12.8 빌드를 씁니다. 지원 GPU는 Ampere(A100·A4000·A6000·RTX 3090), Ada(L40S·RTX 4090),
Hopper(H100·H200), Blackwell(B200·RTX PRO 4000/6000)입니다. V100·T4 같은 이전 세대는 지원하지 않습니다.

- **환경 확인**은 학습 환경과 함께 GPU 이름·세대(compute capability)·메모리·드라이버를 읽어 이 PC에 저장합니다.
  환경이 없으면 오류 대신 "학습 환경이 아직 없습니다"라고 알려 줍니다.
- **실행 계획 확인**은 네트워크를 쓰지 않고 저장된 GPU 정보로 미리 판정합니다. "실행 시 거부될 항목"과 "주의 항목"을
  나누어 보여 주며, 계획 단계에서는 제출을 막지 않습니다.
- **학습 시작·대기 작업 실행**은 승인 창을 띄우기 전에 GPU와 학습 환경을 다시 확인합니다(읽기 전용 SSH).
  거부 항목이 있으면 이유를 보여 주고 멈춥니다. RunPod Pod에 이 앱 버전의 학습 환경이 없으면 같은 승인 창에 설치 안내가
  함께 나오고, 한 번 승인으로 설치와 학습이 이어집니다(`/workspace/factory-desktop` 아래, 최대 30분, 수 GB).
  GPU 서버는 지금처럼 연결 설정에서 조회 결과를 검토한 뒤 "학습 환경 준비"를 먼저 눌러야 합니다.
- **거부**: 지원하지 않는 GPU 세대, 오래된 드라이버(525.60.13 미만), bf16을 지원하지 않는 GPU의 bf16 설정,
  작업 설정과 다른 GPU 종류·개수, 한도를 넘는 시간당 요금, 모델 가중치만으로 GPU 메모리를 넘는 경우.
- **주의**: CUDA 12.8 정식 드라이버(570.26) 미만, 빠듯한 예상 메모리, GPU에서 검증되지 않은 모델, 앱 기본 목록에 없는
  모델(메모리 추정 불가), 오래되었거나 없는 GPU 정보.
- 메모리 추정은 근사치입니다. 가중치·학습 상태·활성값(gradient checkpointing 가정)을 더해 계산하며, 실제 사용량은
  다를 수 있습니다. 작업 설정의 GPU 종류·요금은 연결 설정의 "GPU 정보 → 학습 설정에 적용"으로 맞출 수 있습니다.
```

- [ ] **Step 3: `docs/TRAINING_AND_STORAGE.md`** — 파일 끝에 추가

```markdown
## 지원 GPU와 메모리 추정

두 학습 환경(주 환경·PPO 환경)은 PyTorch 2.7.1 CUDA 12.8 빌드를 씁니다. 지원 세대와 드라이버 조건은
`runtime_profiles.runtime_capabilities()`에 있고, 실제 torch 패키지에서 읽은 값입니다(`scripts/inspect_gpu_wheels.py`).
학습 환경 검사는 설치된 환경에서 GPU 세대 지원과 실제 CUDA 연산 한 번을 확인합니다.

앱 기본 목록 모델은 매개변수 수·`hidden_size`·층 수로 단일 GPU 학습 메모리를 추정합니다. 가중치만으로 GPU 메모리의 95%를
넘으면 실행을 거부하고, 전체 추정이 90%를 넘으면 경고합니다. 예: Llama 3.1 70B bf16 LoRA(가중치 약 130GiB)는 80GB GPU에서
거부되고, 4비트 QLoRA(전체 약 50GiB)는 80GB GPU에서 허용됩니다. 여러 GPU를 쓰는 분산 학습은 지원하지 않습니다.
```

- [ ] **Step 4: 전체 검증**

```bash
uv run pytest
uv run ruff check src tests scripts
cd desktop && npm run build && npm test && cargo test --manifest-path src-tauri/Cargo.toml --locked && cd ..
```

Expected: 모두 통과. 실패하면 기록하고 고친 뒤 다시 실행한다. 통과하지 않은 결과를 통과로 쓰지 않는다.

- [ ] **Step 5: `docs/PROGRESS.md`** — 맨 위에 새 절 추가(날짜와 수치는 실제 실행 결과로 채운다)

```markdown
# GPU 호환성 점검과 CUDA 12.8 학습 환경 (1단계) — 소프트웨어 완료, GPU 인수 대기

[설계](superpowers/specs/2026-10-29-gpu-compat-runtime-design.md) · [계획](superpowers/plans/2026-10-29-gpu-compat-runtime.md) ·
브랜치 `claude/gpu-compat-runtime`.

- 고정 파일: 두 GPU 고정 파일이 torch·torchaudio·torchvision `+cu128`, NVIDIA 12.8 계열로 바뀌었다.
  `scripts/inspect_gpu_wheels.py` 결과: sass `<출력>`, ptx `<출력>`, bitsandbytes CUDA 12.8 `<true/false>`.
- 검증: `uv run pytest` `<N passed, M skipped>`, ruff 통과, `npm test` `<N passed>`, `npm run build` 통과,
  `cargo test` 통과.
- GPU·Pod·데이터 전송·MLflow 쓰기는 사용하지 않았다.
- **남은 일(유료, 별도 승인):** Task 16 GPU 인수 시험. Blackwell Pod와 Ampere 또는 Hopper Pod에서 새 환경 준비,
  `arch_ok`·`kernel_ok`, Qwen2.5-0.5B SFT LoRA 회귀, 메모리 추정과 실제 최대 사용량 비교.
- **2단계 남음:** Qwen3.5·Gemma 4 텍스트 학습 GPU 검증과 수정, 허브 모델 메모리 추정.
```

같은 파일 맨 위 기존 상태 줄의 "Real GPU, Hugging Face and MLflow checks (L1–L3) are still pending"은 그대로 둔다.

- [ ] **Step 6: Commit**

```bash
git add docs/OPERATIONS.md docs/WINDOWS_APP.md docs/TRAINING_AND_STORAGE.md docs/PROGRESS.md
git commit -m "docs: describe GPU preflight, the CUDA 12.8 runtimes and memory estimates"
```

---

### Task 15: 설치 파일 빌드와 패키지 검사 (CPU)

**Files:** 없음(빌드 산출물은 커밋하지 않는다)

- [ ] **Step 1: 학습 엔진·설치 파일 빌드**

```bash
uv run python scripts/build_desktop.py
uv run python scripts/verify_desktop_package.py --receipt dist/verify-0.5.0.json
cd desktop && npm ci && npm run tauri -- build --ci && cd ..
```

Expected: 검사 JSON에 오류가 없고 `bridge_mode`가 `frozen`이다. `desktop/src-tauri/target/release/bundle/nsis/`에
0.5.0 설치 파일이 생긴다.

- [ ] **Step 2: 설치는 사용자 확인 후에만**

사용자의 기존 앱(`C:\Users\user\AppData\Local\LLM Tuning Factory`)과 앱 데이터를 덮어쓰기 전에 묻는다.
NSIS는 이전 설치 폴더를 기억하므로, 시험 설치는 `/D=<새 폴더>`를 준다. 결과를 PROGRESS에 기록한다.

---

### Task 16: GPU 인수 시험 (유료 — 사용자의 별도 명시적 승인 필요)

**이 Task는 승인 없이 실행하지 않는다.** 승인 범위(Pod, 시간, 비용 한도, 데이터)를 PROGRESS에 먼저 적는다.
빌린 Pod는 멈추거나 지우지 않는다.

- [ ] **Step 1: Blackwell Pod(`yuft63rjggvh5z`)**
  1. 0.5.0 앱에서 연결 검사를 하고 "환경 확인"을 누른다. GPU `12.0`, `runtime_state: missing`을 확인한다.
  2. "GPU 정보 → 학습 설정에 적용" 후 실행 계획을 확인한다. 거부 항목이 없어야 한다.
  3. 승인된 합성 데이터(전송 허용)로 Qwen2.5-0.5B SFT LoRA를 "학습 시작"한다. 승인 창에 설치 안내가 있어야 한다.
  4. 설치 후 `arch_ok`·`kernel_ok`가 참이고 학습이 끝까지 실행되는지 확인한다.
- [ ] **Step 2: 메모리 비교**: 학습 중 별도 SSH에서 `nvidia-smi --query-gpu=memory.used --format=csv -l 5`로 최대 사용량을
  기록하고, 앱의 추정치와 비교한다. 차이가 크면 `gpu_compat` 계수 보정을 별도 변경으로 제안한다.
- [ ] **Step 3: Ampere 또는 Hopper Pod 하나에서 Step 1과 같은 회귀 시험.**
- [ ] **Step 4: 결과·비용·남은 일을 PROGRESS에 기록하고, `capabilities.model_catalog()`의 `gpu_verified`·`verification_scope`
  변경은 실제로 검증된 범위만 별도 커밋으로 반영한다.**
