# GPU 호환성 점검과 CUDA 12.8 학습 환경 (1단계) — 설계안

작성일: 2026-10-29 · 상태: 사용자 방향 승인("가장 좋은 방법으로 진행해"), 명세 검토 대기

이 문서는 설계안이다. 아래 기능은 아직 구현되거나 GPU에서 검증되지 않았다.

## 1. 배경

2026-10-29 배정 Pod `yuft63rjggvh5z`(1 × RTX PRO 4000 Blackwell, compute capability 12.0) 연결 점검에서 드러난 문제다.
근거는 [PROGRESS](../../PROGRESS.md)의 같은 날짜 항목에 있다.

- **GPU 세대 미지원.** `containers/gpu-requirements.lock`은 PyPI `torch==2.7.1`(CUDA 12.6 라이브러리)을 고정한다.
  공개 자료상 CUDA 12.6 빌드에는 sm_120 커널이 없다. `desktop_runtime.check`는 `torch.cuda.is_available()`만 보므로
  환경 준비·확인은 통과하고 첫 CUDA 연산에서 실패할 가능성이 높다. 이전 GPU 검증은 H100/H200(sm_90)뿐이다.
- **늦고 알기 어려운 거부.** 작업 설정 GPU(`NVIDIA RTX A4000`, $0.50)와 실제 Pod(RTX PRO 4000 Blackwell, $0.57)의 차이는
  "실행" 시 Pod 접속 후에야 영어 오류로 드러난다. 학습 환경이 없을 때도 영어 오류만 보이고 별도 버튼을 찾아야 한다.
- **최신 모델.** 고정된 LLaMA-Factory 0.9.5와 transformers 5.6.0은 Qwen3·Qwen3.5·Gemma 3/4·Llama 3.3/4를 이미 등록한다.
  앱의 모델 허브(`model_hub.py`)도 `qwen3_5`·`gemma4` 구조를 SFT(LoRA·QLoRA·full·freeze)용으로 이미 등록한다.
  그러나 두 계열은 이미지 겸용 구조(`*ForConditionalGeneration`)이고, 앱 코드(대상 검사·병합·평가)는
  텍스트 전용 `AutoModelForCausalLM`만 다룬다. GPU 검증도 없다.

## 2. 사용자 결정

| 항목 | 결정 |
|---|---|
| 사용할 GPU | Ampere, Hopper·Ada, Blackwell. 구형(V100·T4·P100) 제외 |
| 환경 방식 | 하나의 학습 환경을 CUDA 12.8 빌드로 전환(대안 A). GPU별 다중 환경(B)·Pod 기존 PyTorch 사용(C)은 기각 |
| 점검 시점 | "환경 확인"이 GPU 정보를 저장하고, "계획"은 저장값으로 미리 경고하고, "실행"은 실시간으로 다시 점검·차단 |
| 학습 환경 설치 | 실행 승인에 포함한다. 환경이 없으면 승인 창에 설치 안내를 넣고, 한 번 승인으로 준비와 학습을 잇는다 |
| 메모리 부족 | 2단계: 가중치만으로 GPU 메모리를 넘으면 차단, 추정치가 빠듯하면 경고·대안 제시 후 확인 시 진행 |
| 모델 | Qwen3.5·Llama 3.1·Gemma 4의 20B 이하, GPU가 감당하면 더 큰 모델도 허용 |
| 단계 | 1단계: 이 문서. 2단계: Qwen3.5·Gemma 4 텍스트 학습 지원(별도 명세) |

## 3. 범위

1단계에 포함한다.

1. 주 학습 환경과 PPO 학습 환경의 PyTorch를 `2.7.1+cu128`로 바꾼다. 다른 고정 버전은 그대로 둔다.
2. 학습 환경마다 지원 GPU 세대·드라이버 조건을 담은 **환경 호환표**를 둔다.
3. Pod·서버의 GPU 정보(이름, compute capability, 메모리, 드라이버)를 읽어 로컬에 저장한다.
4. 작업 설정과 GPU 정보를 비교해 **차단/경고**를 내는 판정 모듈을 만든다. 계획·실행 화면이 같은 모듈을 쓴다.
5. 학습 환경 검사(`check`)에 GPU 세대 지원·실제 GPU 연산·bf16 확인을 추가한다.
6. "대기 작업 실행" 승인에 학습 환경 준비를 포함한다.
7. 메모리 추정과 2단계 판정을 넣는다.
8. 모델 목록에 `meta-llama/Llama-3.1-70B-Instruct`, `meta-llama/Llama-3.1-70B`를 추가한다.
   모든 목록 항목에 `hidden_size`, `num_layers`를 넣는다.
9. 실행 시 GPU 종류·개수·요금·환경 거부를 한국어 문구로 보여 준다.
10. 앱 버전을 0.5.0으로 올리고 운영 문서를 갱신한다.

1단계에서 제외한다.

- Qwen3.5·Gemma 4(2단계, 7절).
- 여러 GPU를 쓰는 분산 학습. 현재 정책 `max_gpus=1`을 유지한다.
- 그래프 실행의 계획 단계 경고. 단, 5번 검사는 공유 함수라 그래프 실행도 실행 직전에 호환되지 않는 GPU를 막는다.
- 연결 검사의 MLflow 재시도·오류 문구 개선(별도 후속).
- Pod 자동 시작·종료, Pod에 설치된 PyTorch 사용.
- 텍스트 전용 Qwen3 추가. 요청 범위가 아니며, 목록 항목만으로 추가할 수 있어 후속으로 남긴다.

## 4. 구조

### 4.1 학습 환경 고정 파일

- `containers/gpu-requirements.lock`과 `containers/gpu-ppo-requirements.lock`을 `uv pip compile --torch-backend cu128`로
  **기존 파일 위에** 다시 만든다. 기존 고정 버전을 선호값으로 쓰므로 바뀌는 것은 PyTorch 계열과 그 의존성뿐이다.
  - torch·torchaudio·torchvision은 `+cu128` 빌드가 된다.
  - `nvidia-*-cu12`는 12.8 계열이 된다.
  - 해시 검증(`--require-hashes`)은 유지한다.
  2026-10-29 시험 생성에서 이 결과를 확인했다.
- 고정 파일에는 색인 주소가 기록되지 않는다. 그래서 원격 설치(`uv pip sync`)에도 같은 `--torch-backend cu128`을 준다.
  uv 0.11.23의 `pip sync`가 이 옵션을 지원한다. 같은 고정 파일을 설치하는 `containers/Dockerfile`과
  `scripts/bootstrap_approved_pod.py`·`scripts/update_approved_runtime.py`도 같이 고친다.
  2026-10-29 대상 Pod에서 `download.pytorch.org` 접속(200)과 `torch-2.7.1+cu128-cp312-cp312-manylinux_2_28_x86_64.whl`
  존재를 확인했다.
- 고정 파일 생성 직후 CPU에서 다음을 검사하고 결과를 PROGRESS에 기록한다. 하나라도 실패하면 고정 파일을 쓰지 않는다.
  - torch 패키지의 실제 지원 세대 목록을 추출한다. sm_80·sm_86·sm_89·sm_90·sm_120을 모두 포함해야 한다.
  - bitsandbytes 0.48.1 패키지에 CUDA 12.8용 라이브러리가 들어 있는지 확인한다.

### 4.2 환경 호환표 — `runtime_profiles.py`

새 함수 `runtime_capabilities(profile)`이 다음을 돌려준다. `engine_profile()` 값은 그래프 계획 기록에 들어가므로 바꾸지 않는다.
값은 4.1 검사 결과에서 옮겨 적는다.

| 필드 | 값(예정) | 용도 |
|---|---|---|
| `torch_backend` | `"cu128"` | 원격 설치 옵션 |
| `torch_cuda` | `"12.8"` | 기록·표시 |
| `sass` | 4.1에서 추출한 기계어 세대 목록(예: `["7.5","8.0","8.6","9.0","10.0","12.0"]`) | 세대 지원 판정 |
| `ptx` | 4.1에서 추출한 PTX 세대 목록(없으면 빈 목록) | 세대 지원 판정 |
| `bf16_min_capability` | `"8.0"` | bf16 판정 |
| `min_driver` | `"525.60.13"` | CUDA 12 하위 버전 호환 최소 드라이버. 미만이면 차단 |
| `recommended_driver` | `"570.26"` | CUDA 12.8 정식 드라이버. 미만이면 경고 |

시험으로 호환표의 `torch_backend`와 고정 파일의 torch 빌드(`+cu128`)가 일치하는지 확인한다.

**세대 지원 규칙**(CUDA 기계어 호환): GPU 세대 `M.m`은 다음 중 하나면 지원된다.
- `sass`에 같은 주 버전 `M.k`(`k ≤ m`)가 있다. 예: Ada `8.9`는 `8.6` 기계어로 동작한다.
- `ptx`에 `≤ M.m`인 세대가 있다.

4.4의 사전 판정과 4.7의 실행 검사가 같은 규칙을 쓴다.

### 4.3 GPU 정보 수집 — 새 모듈 `gpu_compat.py`

- `read_gpu_facts(client, gpu_uuids=())`가 고정 명령
  `nvidia-smi --query-gpu=uuid,name,compute_cap,memory.total,driver_version --format=csv,noheader,nounits`를 실행한다.
  GPU 서버는 선택된 UUID만 남긴다. 읽기 전용이며 아무것도 설치하지 않는다.
- "환경 확인"(`runtime_check`)·"학습 환경 준비"와 데스크톱의 "대기 작업 실행"이 이 함수를 호출한다.
  그래프 실행과 CLI는 호출하지 않고, 4.7 검사로 보호받는다(기존 시험용 가짜 서버에 영향을 줄이기 위함).
  결과를 `state/gpu-observations/<연결 대상 키>.json`에 시각과 함께 저장한다.
  연결 대상 키는 RunPod이면 `runpod-<pod_id>`, GPU 서버이면 `server-<목적지 해시 앞 16자>`다.
  RunPod 요금·GPU 종류는 같은 시점의 RunPod API 조회값을 함께 저장한다.
- 학습 환경이 없어도 GPU 정보 수집은 성공해야 한다. 그래서 `runtime_check`는 환경이 없을 때 예외 대신
  `{"runtime": "missing" | "mismatch" | "ready", "gpu": …, "findings": […]}`를 돌려준다.

### 4.4 호환성 판정 — `gpu_compat.evaluate(config, facts, profile, model)`

결과는 `{level: "block" | "warn", code, message, suggestion}` 목록이다. 문구는 한국어이고 비밀값을 담지 않는다.

단계별 처리는 다음과 같다.
- **계획:** 저장값을 쓰므로 제출을 막지 않는다. 차단 수준은 "실행 시 거부될 항목", 경고 수준은 "주의 항목"으로 보여 준다.
- **실행:** 실시간 값으로 차단 수준을 거부하고, 경고 수준은 승인 창에 표시해 확인을 받는다.

| 코드 | 수준 | 조건 |
|---|---|---|
| `GPU_ARCH_UNSUPPORTED` | 차단 | GPU 세대가 `supported_capabilities`에 없음 |
| `DRIVER_TOO_OLD` | 차단 | 드라이버 < `min_driver` |
| `DRIVER_OLD` | 경고 | `min_driver` ≤ 드라이버 < `recommended_driver` |
| `BF16_UNSUPPORTED` | 차단 | 정밀도 bf16인데 세대 < `bf16_min_capability` |
| `GPU_TYPE_MISMATCH` | 차단 | 작업 GPU 종류가 관측 GPU 이름·RunPod GPU ID와 다름. 입력할 정확한 값을 함께 보여 줌 |
| `GPU_COUNT_MISMATCH` | 차단 | 작업 GPU 개수 ≠ 관측 개수 |
| `PRICE_OVER_BUDGET` | 차단 | RunPod 시간당 요금 > 작업의 GPU당 요금 × 개수 |
| `MEMORY_WEIGHTS_EXCEED` | 차단 | 가중치 추정 > GPU 메모리 × 0.95 |
| `MEMORY_TIGHT` | 경고 | 전체 추정 > GPU 메모리 × 0.90. QLoRA 4비트·배치 1·짧은 길이를 제안 |
| `MEMORY_UNKNOWN` | 경고 | 목록에 없는 모델이라 크기를 모름 |
| `MODEL_UNVERIFIED` | 경고 | 목록의 `gpu_verified`가 거짓이거나 검증된 세대가 아님 |
| `OBSERVATION_MISSING` / `OBSERVATION_STALE` | 경고(계획만) | 저장된 GPU 정보가 없거나 24시간 이상 지남. "환경 확인"을 다시 누르라고 안내 |

기존 `validate_budget`의 GPU 종류·개수·요금 거부는 안전장치로 그대로 둔다. 데스크톱은 그 전에 위 판정을 한국어로 보여 준다.

### 4.5 메모리 추정

추정치이며 GPU 검증 결과로 계수를 보정한다. 입력은 목록의 `parameters_b`·`hidden_size`·`num_layers`와 작업 설정이다.

- 매개변수당 바이트 `b`: bf16·fp16 2, 8비트 1, 4비트 0.55(양자화 상수 포함).
- 가중치 `W = P × b`.
- 학습 상태 `T`: 전체 학습 `P × 16`(bf16 가중치·기울기, Adam fp32 상태·주 가중치). LoRA·OFT `P × 0.01 × 16`.
  freeze는 학습 층 비율 × `P × 16`.
- 활성값 `A = 배치 × cutoff_len × hidden_size × (2 × num_layers + 34)` 바이트(gradient checkpointing 가정).
- 전체 `W + T + A + 1.5 GiB`(CUDA 문맥·프레임워크 여유).

### 4.6 단일 학습 실행 흐름

1. **계획:** 지금처럼 네트워크를 쓰지 않는다. 저장된 GPU 정보로 판정해 계획 결과에 `compatibility: {observed_at, findings}`를
   더한다. 화면은 "실행 시 거부될 항목"과 "주의 항목"을 나누어 보여 준다.
2. **"대기 작업 실행":**
   1. 화면이 먼저 `runtime_check`를 부른다(읽기 전용 SSH).
   2. 차단 항목이 있으면 한국어 사유를 보여 주고 멈춘다.
   3. 환경이 `missing`·`mismatch`이면 승인 창에 설치 안내를 더한다. 안내에는 `/workspace/factory-desktop` 아래 설치,
      최대 30분, 빌린 Pod는 주인의 동의가 필요하다는 점이 들어간다.
   4. `MEMORY_TIGHT`이면 같은 승인 창에 경고와 대안을 넣는다.
   5. 승인하면 `execute`에 `{approved: true, prepare_runtime: <설치 안내를 승인했는지>}`를 보낸다.
3. **실행(백엔드):**
   1. 연결 후 GPU 정보를 실시간으로 다시 읽어 판정한다. 차단 항목이 있으면 한국어 `ValueError`로 거부한다.
   2. 환경이 준비되지 않았으면 `prepare_runtime`이 참일 때만 기존 준비 코드로 설치한다. 아니면 한국어로 거부한다.
      실행 중 준비는 **RunPod만** 한다. GPU 서버는 조회 결과를 검토하는 기존 준비 절차(예약 포함)가 있다.
      그래서 서버는 실행 화면에서 "연결 설정에서 학습 환경 준비를 먼저 누르라"고 안내하고 멈춘다.
      다른 작업이 Pod를 잡고 있으면 준비하지 않는다(기존 `runtime_prepare`와 같은 잠금 검사).
   3. 4.7 검사를 통과하면 `work()`로 학습을 시작한다.
4. "학습 환경 준비" 버튼은 수동 준비용으로 남긴다.

### 4.7 학습 환경 검사 강화 — `desktop_runtime.check`

원격 확인 코드에 다음을 더하고 기대값에 `arch_ok: true`, `kernel_ok: true`를 추가한다.

- `cap = torch.cuda.get_device_capability()`와 `torch.cuda.get_arch_list()`를 읽는다. 4.2의 세대 지원 규칙을 만족하면
  `arch_ok`다(`sm_XY`는 기계어, `compute_XY`는 PTX).
- 환경이 없으면(가상환경 Python이 없음) `RuntimeMissing`, 있지만 고정값이 다르거나 확인 코드가 실패하면 `RuntimeMismatch`,
  연산이 실패하면 `GPUIncompatible`을 낸다. `runtime_check`는 예외 대신 `runtime_state`로 알려 준다.
- `(torch.ones(8, device="cuda") * 2).sum().item() == 16`(`kernel_ok`). 세대 불일치는 여기서 확정된다.
- `torch.cuda.is_bf16_supported()`를 기록한다. 실행 판정에서 작업 정밀도와 비교한다.

이 함수는 그래프 실행과 CLI도 쓰므로, 모든 경로에서 호환되지 않는 GPU의 학습 시작을 막는다.

### 4.8 모델 목록

- `meta-llama/Llama-3.1-70B-Instruct`·`meta-llama/Llama-3.1-70B`를 추가한다(`llama3` 템플릿, `llama` 구조, 승인 필요, 70B).
  bf16 LoRA는 가중치만 약 140GB라 단일 GPU에서 차단된다. 4비트 QLoRA는 80GB급 GPU에서 가능하다고 판정될 것으로 예상한다.
- 모든 항목에 각 모델 `config.json`의 `hidden_size`·`num_hidden_layers`를 넣고, 시험으로 값을 고정한다.
- `gpu_verified`·`verification_scope`는 실제 검증 결과로만 바꾼다.

### 4.9 버전과 문서

앱 0.5.0. `docs/OPERATIONS.md`, `docs/WINDOWS_APP.md`, `docs/TRAINING_AND_STORAGE.md`에 다음을 반영한다.
지원 GPU 세대, 실행 승인 안의 환경 준비, 차단·경고 목록, 메모리 추정의 한계.

## 5. 시험

**CPU 자동 시험**

- 호환표와 고정 파일의 일치.
- `evaluate` 규칙을 표 형식으로 시험한다. 경계값과 계획·실행 단계별 수준을 포함한다.
- 메모리 추정 고정 사례:
  - Qwen2.5-0.5B LoRA, 16GB: 통과.
  - Llama-3.1-70B bf16 LoRA, 80GB: 차단.
  - Llama-3.1-70B 4비트 QLoRA, 80GB: 통과 또는 경고.
  - Llama-3.1-70B 4비트 QLoRA, 24GB: 차단.
- 가짜 SSH 실행 흐름:
  - `prepare_runtime` 없이는 설치하지 않고 한국어로 거부한다.
  - `prepare_runtime`이 있으면 준비 후 학습한다.
  - `arch_ok`·`kernel_ok`가 거짓이면 거부한다.
  - `runtime_check`는 환경이 없어도 GPU 정보를 돌려준다.
- 화면(Playwright): 계획 경고 표시, 설치 안내가 포함된 승인 창, 차단 시 실행 중단, 메모리 경고 확인.
- `uv run pytest`, `uv run ruff check src tests scripts`, 화면 시험과 TypeScript 빌드.

**GPU 인수 시험(유료, 별도 승인)**

1. 이 Blackwell Pod:
   - 새 환경을 실행 승인으로 준비하고 `arch_ok`·`kernel_ok`를 확인한다.
   - Qwen2.5-0.5B SFT LoRA를 끝까지 실행한다(회귀).
   - 메모리 추정치와 실제 최대 사용량을 비교한다.
2. Ampere 또는 Hopper Pod 하나: 같은 회귀 시험.
3. 선택: 80GB 이상 GPU에서 Llama-3.1-70B QLoRA.

결과와 비용은 PROGRESS에 기록하고, 빌린 Pod는 멈추거나 지우지 않는다.

## 6. 위험

- bitsandbytes·Triton의 sm_120 동작과 LLaMA-Factory 0.9.5 + cu128 조합은 GPU 인수 시험 전까지 확인되지 않는다.
  같은 torch 버전의 빌드 차이라 코드 변경은 없을 것으로 예상한다.
- 메모리 추정은 근사치다. 차단은 가중치만 기준으로 해서 잘못 막을 가능성을 줄이고, 나머지는 경고로 둔다.
- CUDA 12.8 라이브러리로 원격 설치 용량이 늘어난다. 설치 파일 크기는 고정 파일만 바뀌므로 거의 그대로다.
- 0.4.0에서 제출된 작업도 0.5.0의 학습 환경과 판정을 따른다. 같은 원격 경로를 쓰지 않으므로 기존 환경을 덮어쓰지 않는다.

## 7. 2단계 예고(별도 명세)

**대상:**
- Qwen3.5: 0.8B·2B·4B·9B와 Base. GPU가 감당하면 27B·35B-A3B.
- Gemma 4: E2B·E4B. GPU가 감당하면 26B-A4B·31B.

**구조:** 모두 `*ForConditionalGeneration` 구조이며, LLaMA-Factory 템플릿은 각각 `qwen3_5`, `gemma4n`·`gemma4`다.
모델 허브는 이미 이 구조를 SFT용으로 등록하므로, 2단계는 **GPU 검증과 그 과정에서 드러나는 문제 수정**이 중심이다.
1단계 메모리 추정은 앱 기본 목록 모델만 다루고, 허브에 등록한 모델은 `MEMORY_UNKNOWN` 경고를 낸다.
2단계에서 허브 조회에 매개변수 수·`hidden_size`를 더해 추정 대상으로 넓힌다.

**해야 할 일:** 텍스트 데이터만으로 학습할 때 다음이 동작하는지 검증하고 고친다.
- LoRA 대상 검사(`target_inspection.py`)
- 병합·확정(`model_ops.py`)
- 평가 생성
- 비전·오디오 부분 동결

PPO 환경(transformers 4.52.4)은 이 모델들을 지원하지 않는다.
