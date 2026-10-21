# LLM Tuning Factory

여러 업무 AI를 같은 설정 규격과 실행 파이프라인으로 관리하는 Python CLI 플랫폼입니다.
LLaMA-Factory 학습 엔진과 RunPod 실행기를 분리하고, 데이터·명세서·산출물을 해시로 추적합니다.
기존 KBF 프로젝트의 ShareGPT 데이터 규격·Keycloak 인증 방식·순수 추출 평가 함수를 재사용합니다.

**운영 방식:** 각자 PC에 설치하여 작업을 관리하고, 학습은 각자 배정받은 RunPod Pod 또는
SSH로 접속하는 기관 GPU 서버에서 실행합니다.
실행 기록과 업로드한 산출물은 공용 MLflow에 모읍니다. 별도 NCP 관리 서버는 필요하지 않습니다.
Windows 앱은 **0.4.0 내부 검토 버전**입니다.

[Windows 설치 파일 다운로드](https://github.com/ParkYaeseong/llm-tuning-factory/releases/tag/v0.4.0) ·
[Windows 사용 안내](docs/WINDOWS_APP.md) · [학습 프로젝트·그래프 안내](docs/TRAINING_WORKFLOWS.md) ·
[실제 GPU 검증 결과](docs/GPU_ACCEPTANCE_2026-10-15.md)

**0.4.0 범용 학습 흐름:** 목적과 데이터를 직접 정한 프로젝트에서 여러 학습을 직렬·병렬로 묶어 자동 실행합니다.

1. **프로젝트** — 이름과 목적을 자유롭게 정한 빈 프로젝트를 만듭니다. 예제가 필요하지 않습니다.
2. **데이터 가져오기** — CSV·JSON·JSONL의 열을 학습 항목에 연결해 바뀌지 않는 데이터 버전으로 등록하고 학습·검증·시험으로 나눕니다.
3. **레시피** — 학습 단계·방식·옵션을 이름 붙여 저장하고 여러 그래프에서 다시 씁니다.
4. **학습 그래프** — 학습·평가·자동 선택·병합·전체 모델 확정 카드를 직렬·병렬로 연결합니다.
5. **계획·승인 → 그래프 실행** — 기본은 모의 실행입니다. 실제 실행은 실행 승인과 데이터 전송 승인을 따로 받은 뒤
   창을 닫아도 계속 도는 백그라운드 컨트롤러가 진행합니다.

자세한 순서와 주의점은 [학습 프로젝트·그래프 안내](docs/TRAINING_WORKFLOWS.md)에 있습니다.

기존의 연결 설정·로컬 데이터 미리보기·업무별 학습 설정·실행 관리·MLflow 기록 비교도 그대로 제공합니다.
Windows 앱은 Python을 포함하며 키는 연결 화면에서 입력하고 Windows 자격 증명 저장소에 보관합니다.
기존 Python CLI도 계속 사용할 수 있습니다. 아래 소스 설치 안내는 CLI/개발자용입니다.

**실제 검증:** H100 + Qwen2.5-0.5B 합성 데이터에서 LoRA와 실제 4비트 QLoRA, 생성 평가,
취소·체크포인트 재개·연결 중단 복구, 산출물 전체 회수와 공용 MLflow 왕복 해시를 확인했습니다.
더미 결과는 업무 AI 품질을 의미하지 않습니다.

**학습 방식(0.3.0부터):** PT·SFT·RM·PPO·DPO·KTO, Full·Freeze·LoRA·QLoRA·OFT와 단계별 옵션을 제공합니다.
기본 모델 15개와 Hugging Face 검색·모델 ID 추가를 제공하고, 구조별 호환 조건과 고정 revision을 관리합니다.
Qwen3.5/3.6·Gemma4 등은 지원 구조를 검사해 추가하며, GPU 검증 여부와 실행 제한을 따로 표시합니다.
S3 버킷·파일 조회와 버전/해시를 고정한 PC 가져오기(파일당 32 MiB)를 지원합니다.
[학습 방식·S3 사용 안내](docs/TRAINING_AND_STORAGE.md)를 확인하세요.

**검증 구분:** 위 H100 결과는 0.2.3의 SFT 시험입니다. 0.3.0의 학습 조합은 CPU/UI 검사,
PPO 전용 실제 패키지 import, 로컬 S3 서명·전송 계약을 검증했으며 실제 GPU/클라우드 S3 시험은 남아 있습니다.
0.4.0의 프로젝트·그래프 기능은 별도 해시 고정 환경의 CPU 병합·확정 검산과 가짜 RunPod·SSH·MLflow 연결을 쓴 자동 시험으로
확인했습니다. 개발 PC 한 대에서의 설치 앱 검사(11.6)는 결과를 진행 상태에 기록합니다. 실제 Hugging Face 헤더 조회,
실제 RunPod·BOP GPU 학습(직렬·병렬·병합과 재평가 포함), 실제 MLflow 서버 기록은 각각 별도 승인을 받아 시험하며 아직 하지
않았습니다.
정확한 결과와 남은 시험은 [진행 상태](docs/PROGRESS.md)에 구분합니다.
PPO·OFT 등의 upstream 재개/평가 제한은 이유와 함께 차단합니다.
멀티모달·임의 모델/플러그인·대량 스트리밍·다중 PC 스케줄링·서빙·코드 서명은 이번 범위에 포함하지 않습니다.

**GPU 서버:** RunPod 외에 BOP 같은 기관의 Linux/NVIDIA GPU 서버에 SSH 키 인증과 관리자가 전달한
SHA256 지문으로 직접 접속해 학습할 수 있습니다(단일 학습 화면은 작업당 GPU 1개). 서버의 작업 폴더 아래에만 전용 환경을 만들며 서버를 켜거나 끄지 않습니다.
설정 절차는 [Windows 사용 안내](docs/WINDOWS_APP.md), CLI 절차와 요구 조건은 [운영 안내](docs/OPERATIONS.md)를 확인하세요.
학습 그래프에서는 서버를 실행 프로필로 등록합니다([학습 프로젝트·그래프 안내](docs/TRAINING_WORKFLOWS.md)).
실제 서버·GPU 시험은 아직 하지 않았습니다.

**MLflow 인증 없음:** 로컬(`127.0.0.1`)·내부망 MLflow 서버는 앱에서 “인증 없음”을 선택하거나
CLI `.env`의 토큰과 OIDC 세 항목을 비워 연결합니다. 서버에 접근할 수 있는 누구나 기록을 볼 수 있으므로
신뢰할 수 있는 망에서만 사용하세요. 토큰·OIDC 인증값을 쓰면 CLI와 앱 모두 HTTPS 주소만 허용합니다. 자세한 내용은 [운영 안내](docs/OPERATIONS.md)와 [Windows 사용 안내](docs/WINDOWS_APP.md)에 있습니다.

처음 사용하는 Windows 앱 사용자는 작업 공간의 “튜토리얼 시작”으로 합성 예제만 쓰는 16단계·약 7분 실습을 해 볼 수 있습니다
(연결 정보 불필요, GPU·전송 없음). 11~15단계는 프로젝트·실행 프로필·데이터 가져오기·학습 그래프·그래프 실행 화면을
설명만 하며 아무것도 만들지 않습니다. [Windows 사용 안내](docs/WINDOWS_APP.md)를 확인하세요.

## 처음 설치하기

Git과 [uv](https://docs.astral.sh/uv/getting-started/installation/)를 준비한 뒤 아래 순서로 실행합니다.
비공개 저장소이므로 먼저 저장소 접근 권한을 받아야 합니다. Python 3.12는 uv가 준비합니다.

```powershell
git clone https://github.com/ParkYaeseong/llm-tuning-factory.git
cd llm-tuning-factory
uv sync --python 3.12 --extra tracking --locked
Copy-Item .env.example .env
```

macOS/Linux에서는 마지막 명령 대신 `cp .env.example .env`를 사용합니다.
로컬 `.env`를 열어 본인 RunPod API 키, 관리자가 별도로 전달한 MLflow 인증 정보,
기록을 구분할 `FACTORY_OPERATOR`를 입력합니다. 서버 주소는 예시에 들어 있습니다.
OIDC 대신 발급받은 토큰을 쓰면 OIDC 세 항목을 비우고 `MLFLOW_TRACKING_TOKEN`을 채웁니다.
인증 없는 로컬·내부 서버는 토큰과 OIDC 세 항목을 모두 비웁니다.

```powershell
uv run factory doctor
```

`doctor`는 연결 상태를 조회합니다. 실제 기록·작은 파일 저장까지 검사하려면 `doctor --write-test`를 사용합니다.
GPU를 시작하지는 않습니다. RunPod 상태도 조회하려면 `doctor --pod-id 본인_POD_ID`를 사용합니다.
키 없이 먼저 살펴보려면 `.env`를 만들기 전에 아래의 GPU 없는 예시를 실행할 수 있습니다.

CLI는 현재 폴더의 `.env`를 자동으로 읽으며 기존 셸 환경변수가 우선합니다.
다른 파일은 `uv run factory --env-file 경로 명령`으로 지정합니다.
기존 연결 프로필을 쓰는 경우 `--connection-file` 설정이 적용됩니다.
`.env` 원본, SSH 개인키, 실제 데이터와 `state/`는 공유하지 않습니다. 개인키는 PC의 `.ssh`에 보관합니다.
프로그램은 전역 Python과 기존 서비스를 변경하지 않으며 PC에는 GPU 라이브러리를 설치하지 않습니다.

## GPU 없이 실행

```powershell
uv run factory dataset-register dummy 1 examples/dummy.json --synthetic
uv run factory validate configs/ai/paper_extraction.yaml
uv run factory plan configs/ai/paper_extraction.yaml
uv run factory run configs/ai/paper_extraction.yaml
uv run factory run configs/ai/paper_extraction.yaml --mock
uv run factory status
```

`run` 기본값은 실행 계획만 출력합니다. `--mock`은 명시적으로 모의 작업을 실행합니다.
실제 모델·어댑터를 생성하거나 학습 loss·품질 점수를 꾸며내지 않습니다.

```powershell
uv run factory batch configs/ai/paper_extraction.yaml configs/ai/labnote_generation.yaml configs/ai/workflow_classification.yaml --submit
uv run factory work --drain
uv run factory evaluate examples/evaluation.json --kind classification
```

같은 명세서는 기본적으로 같은 작업으로 취급합니다. 의도적인 반복 실험은 `run --submit --request-key <새 식별자>`로 제출합니다.
`--state-dir`·`--policy-file`·`--tracking-uri` 공통 옵션은 하위 명령 앞에 지정합니다.

## 실제 학습과 공유 시 주의점

실제 학습은 [운영 안내](docs/OPERATIONS.md)의 GPU 런타임 준비와 데이터 전송 승인이 필요합니다.
로컬 설치만으로 RunPod나 GPU 서버에 학습 환경이 설치되거나 GPU가 자동 시작되지는 않습니다.
PC를 켜 두고 실행해야 작업 관리와 결과 회수가 이어집니다.

각 PC의 `state/`에는 별도의 작업 목록·데이터 사본·GPU 잠금이 저장됩니다.
**서로 다른 PC 사이에서는 GPU 잠금과 비용 한도가 공유되지 않습니다.** 사용자별 Pod를 배정하거나
한 Pod를 사용할 담당자 한 명을 정해야 합니다. 공용 MLflow는 기록 저장소이며 중앙 작업 조정기가 아닙니다.
업로드한 로그·데이터 명세·모델은 공용 서버 권한에 따라 다른 사람이 볼 수 있으므로 민감한 데이터를 넣지 않습니다.

실제 H200 BF16 학습·생성에 이어 산출물 40개 전체 회수와 MLflow 어댑터 왕복 해시 검증을 완료했습니다.
회수 중단으로 실패했던 실행은 재학습 없이 결과를 복구해 FINISHED로 마무리했습니다.
또한 저장된 체크포인트 10단계에서 12단계까지 실제 학습을 재개하고 이력 보존을 확인했습니다.
후속 시험에서는 실제 학습 도중 취소된 작업을 다시 연결하고, 체크포인트 10단계에서 12단계까지
복구·재개했습니다. 학습 이력 유지, 최종 산출물 해시와 MLflow 어댑터 왕복도 확인했습니다.
후속 Windows H100 시험에서 실제 4비트 QLoRA와 체크포인트 10→100 재개도 검증했습니다.
위 CLI 이력과 최신 Windows 검증은 [진행 상태](docs/PROGRESS.md)에서 구분합니다.
GPU 이미지 자체의 빌드와 학습 어댑터 Model Registry 등록은 이번 검증에 포함하지 않았습니다.

## 업데이트와 개발 검증

**Windows 앱 업데이트·제거.** 새 설치 파일을 실행하거나 앱을 제거하기 전에 앱의 컨트롤러 막대에서 **컨트롤러 정지** →
**정지 요청**을 누르고, 상태가 ‘정지’로 바뀐 뒤 앱 창을 닫습니다. 백그라운드 컨트롤러는 창을 닫아도 계속 돌며 앱과 학습 엔진 파일을 열어 두므로
설치 프로그램이 파일을 바꿀 수 없습니다. 진행 중인 단계가 끝나야 정지되므로 몇 시간이 걸릴 수 있습니다.
0.3.0의 연결 설정과 저장한 인증값은 0.4.0에서 그대로 씁니다. 0.4.0으로 한 번 연 작업 DB는 0.3.0에서 다시 쓸 수 없으므로
이전 버전으로 되돌리지 마세요. 제거 창의 ‘애플리케이션 데이터 삭제하기’를 선택하면 프로젝트·데이터 버전·실행 기록이
모두 지워지므로 의도한 경우에만 선택합니다. 설치 파일은 코드 서명하지 않았으므로 Windows SmartScreen 경고가 나오면
배포 페이지의 SHA256을 확인한 뒤 ‘추가 정보’ → ‘실행’을 누릅니다. 자세한 절차는 [Windows 사용 안내](docs/WINDOWS_APP.md)에 있습니다.

**CLI 업데이트.** 작업이 실행 중이지 않을 때 업데이트합니다. 기존 작업은 실행 당시 소스 해시를 사용하므로
업데이트 이후 재시도 시 해당 작업과 일치하는 GPU 패키지·명세를 확인해야 합니다.

```powershell
git pull --ff-only
uv sync --python 3.12 --extra tracking --locked
```

개발자는 `--extra dev`도 설치하고 `uv run pytest -q`, `uv run ruff check src tests scripts`,
`uv run python scripts/check_secrets.py`를 실행합니다. GPU 실험은 별도 승인이 필요합니다.

## 문서

- [기존 환경 조사](docs/EXISTING_SYSTEM_ASSESSMENT.md)
- [구현 계획](docs/IMPLEMENTATION_PLAN.md)
- [아키텍처와 상태 관리](docs/ARCHITECTURE.md)
- [설정·데이터·평가 규격](docs/CONFIGURATION.md)
- [Windows 앱 사용 안내](docs/WINDOWS_APP.md)
- [학습 프로젝트·학습 그래프 안내](docs/TRAINING_WORKFLOWS.md)
- [실제 RunPod·MLflow 설치 및 운영](docs/OPERATIONS.md)
- [테스트·진행 상태·미완료 항목](docs/PROGRESS.md)

FastAPI·Kubernetes·다른 학습 엔진·모델 서빙은 도입하지 않았습니다.
SQLite 메타데이터 저장소는 동일 호스트의 여러 실행기에서 공유할 수 있으며 PostgreSQL 분산 실행은 후속 범위입니다.
