# LLM Tuning Factory

여러 업무 AI를 같은 설정 규격과 실행 파이프라인으로 관리하는 Python CLI 플랫폼입니다.
LLaMA-Factory 학습 엔진과 RunPod 실행기를 분리하고, 데이터·명세서·산출물을 해시로 추적합니다.
기존 KBF 프로젝트의 ShareGPT 데이터 규격·Keycloak 인증 방식·순수 추출 평가 함수를 재사용합니다.

**운영 방식:** 각자 PC에 설치하여 작업을 관리하고, 학습은 각자 배정받은 RunPod GPU에서 실행합니다.
실행 기록과 업로드한 산출물은 공용 MLflow에 모읍니다. 별도 NCP 관리 서버는 필요하지 않습니다.
현재 배포는 **0.1.0 검증용 버전**이며 아래 미검증 항목이 있습니다.

**현재 검증 범위:** 운영 MLflow 인증·실행 기록·산출물 왕복 저장과 실제 RunPod API·SSH 연결을 확인했습니다.
실제 H200 더미 학습에서 발견한 의존성 및 FP16 수치 오류를 수정했습니다. 최종 GPU 검증 결과와
미완료 항목은 [진행 상태](docs/PROGRESS.md)에 기록합니다. 더미 결과는 실제 업무 AI 품질을 의미하지 않습니다.

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
로컬 설치만으로 RunPod에 학습 환경이 설치되거나 GPU가 자동 시작되지는 않습니다.
PC를 켜 두고 실행해야 작업 관리와 결과 회수가 이어집니다.

각 PC의 `state/`에는 별도의 작업 목록·데이터 사본·GPU 잠금이 저장됩니다.
**서로 다른 PC 사이에서는 GPU 잠금과 비용 한도가 공유되지 않습니다.** 사용자별 Pod를 배정하거나
한 Pod를 사용할 담당자 한 명을 정해야 합니다. 공용 MLflow는 기록 저장소이며 중앙 작업 조정기가 아닙니다.
업로드한 로그·데이터 명세·모델은 공용 서버 권한에 따라 다른 사람이 볼 수 있으므로 민감한 데이터를 넣지 않습니다.

실제 H200 BF16 학습과 생성은 확인했지만 승인 시간 종료로 전체 산출물 회수는 중단됐습니다.
전체 과정 재검증, 실제 체크포인트 복구, QLoRA, GPU 이미지 빌드와 모델 등록은 남아 있습니다.
해당 실행은 성공으로 포장하지 않고 MLflow에 실패 상태로 보존했습니다.

## 업데이트와 개발 검증

작업이 실행 중이지 않을 때 업데이트합니다. 기존 작업은 실행 당시 소스 해시를 사용하므로
업데이트 이후 재시도 시 해당 작업과 일치하는 GPU 패키지·명세를 확인해야 합니다.

```powershell
git pull --ff-only
uv sync --python 3.12 --extra tracking --locked
```

개발자는 `--extra dev`도 설치하고 `uv run pytest -q`, `uv run ruff check src tests`,
`uv run python scripts/check_secrets.py`를 실행합니다. GPU 실험은 별도 승인이 필요합니다.

## 문서

- [기존 환경 조사](docs/EXISTING_SYSTEM_ASSESSMENT.md)
- [구현 계획](docs/IMPLEMENTATION_PLAN.md)
- [아키텍처와 상태 관리](docs/ARCHITECTURE.md)
- [설정·데이터·평가 규격](docs/CONFIGURATION.md)
- [실제 RunPod·MLflow 설치 및 운영](docs/OPERATIONS.md)
- [테스트·진행 상태·미완료 항목](docs/PROGRESS.md)

FastAPI·Kubernetes·다른 학습 엔진·모델 서빙은 도입하지 않았습니다.
SQLite 메타데이터 저장소는 동일 호스트의 여러 실행기에서 공유할 수 있으며 PostgreSQL 분산 실행은 후속 범위입니다.
