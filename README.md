# LLM Tuning Factory

여러 업무 AI를 같은 설정 규격과 실행 파이프라인으로 관리하는 Python CLI 플랫폼입니다.
LLaMA-Factory 학습 엔진과 RunPod 실행기를 분리하고, 데이터·명세서·산출물을 해시로 추적합니다.
기존 `D:/workflow/kbl-llm-repo`의 ShareGPT 데이터 규격·Keycloak 인증 방식·순수 추출 평가 함수를 재사용합니다.

**현재 검증 범위:** 운영 MLflow 인증·실행 기록·산출물 왕복 저장과 실제 RunPod API·SSH 연결을 확인했습니다.
실제 H200 더미 학습에서 발견한 의존성 및 FP16 수치 오류를 수정했습니다. 최종 GPU 검증 결과와
미완료 항목은 [진행 상태](docs/PROGRESS.md)에 기록합니다. 더미 결과는 실제 업무 AI 품질을 의미하지 않습니다.

## 설치

Python 3.12와 [uv](https://docs.astral.sh/uv/)를 사용합니다. 작업 폴더에서 실행하세요.

```powershell
uv sync --python 3.12 --extra dev --extra tracking --locked
uv run pytest -m "not gpu" -q
uv run ruff check src tests
```

전역 Python·기존 서비스를 변경하지 않습니다. `state/`와 `.env`는 Git에서 제외됩니다.
Control Plane은 GPU 라이브러리를 설치하지 않습니다. GPU 컨테이너는 별도의 해시 고정 의존성을 사용합니다.

## GPU 없이 실행

```powershell
uv run factory dataset-register dummy 1 examples/dummy.json
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

## 문서

- [기존 환경 조사](docs/EXISTING_SYSTEM_ASSESSMENT.md)
- [구현 계획](docs/IMPLEMENTATION_PLAN.md)
- [아키텍처와 상태 관리](docs/ARCHITECTURE.md)
- [설정·데이터·평가 규격](docs/CONFIGURATION.md)
- [실제 RunPod·MLflow 설치 및 운영](docs/OPERATIONS.md)
- [테스트·진행 상태·미완료 항목](docs/PROGRESS.md)

FastAPI·Kubernetes·다른 학습 엔진·모델 서빙은 도입하지 않았습니다.
SQLite 메타데이터 저장소는 동일 호스트의 여러 실행기에서 공유할 수 있으며 PostgreSQL 분산 실행은 후속 범위입니다.
