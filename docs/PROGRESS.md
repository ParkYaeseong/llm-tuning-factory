# Windows v0.6.1 프리릴리스 공개 — 2026-11-06

- GitHub 릴리스 `v0.6.1`을 공개했다(기존 공개 설치 파일은 v0.4.0까지였다). 영어/한국어 UI·새 GIF를
  포함하며, 기존 릴리스는 보존했다. 빌드·릴리스 태그의 소스 커밋은
  `9f0c7103752fef192da80074aaf9d5b815585f6a`. 데스크톱 버전 필드 6개를 0.6.1로 맞췄으며
  학습 엔진 패키지와 의존성 잠금은 그대로 유지했다.
- 설치 파일 `LLM.Tuning.Factory_0.6.1_x64-setup.exe`: 49,423,915 바이트,
  SHA-256 `d3bd47ca31c40bb05fd1ff04d6dc8716ff4dc09edddf5aa2348fb7a26ca43b06`.
  `.sha256`과 `verification-0.6.1.json`도 함께 올렸고 GitHub 업로드 digest와 로컬 해시가 같은지 확인했다.
  릴리스: https://github.com/ParkYaeseong/llm-tuning-factory/releases/tag/v0.6.1
- 실제 검증: Python 3.12.12 locked sync, `uv run pytest` **2245 passed, 2 skipped**(739.44초),
  `uv run ruff check src tests scripts` 통과. Playwright **291 passed**(3.8분), Rust release 검사
  **54 passed**, TypeScript/Vite·PyInstaller·Tauri NSIS 빌드 통과.
- `scripts/verify_desktop_package.py --receipt dist/verify-0.6.1.json`의 frozen 엔진 검사 5그룹 모두 통과:
  legacy·인자 거부·workflow·controller·wheel. 기본 dry-run, CP949 가져오기·분할, 고정 계획,
  모의 controller 완료·정지·EOF 종료와 worker wheel 소스 일치를 확인했다. dist·Tauri sidecar·release
  폴더 엔진 SHA-256은 모두 `bbe2ae9290b0e64340aa473e2fe4f1fb86dcdc7fa874f2f2a63c6ff0b84879f2`.
- 실제 release 실행 파일의 Windows WebView에서 ENG/KOR 메뉴, 입력 초안 보존, reload 후 언어 유지를
  확인했다. 공유 WebView 프로필 첫 시도는 예상 초기 화면과 달라 실패했으며, 별도 테스트 프로필에서
  overview를 명시해 최종 통과했다. 테스트가 연 프로세스만 닫고 원래 언어 설정을 복구했다.
  연결 정보 저장·프로젝트 제출·사용자 앱 설치/업데이트는 하지 않았다. 설치/업데이트 자체는 이번 세션에서
  재검증하지 않았고 코드 서명은 없다.
- 호스팅 CI는 릴리스 준비 시 Ubuntu 성공, Windows 진행 중이었다. 이를 릴리스 노트와 검증 영수증에
  구분해 기록했다: https://github.com/ParkYaeseong/llm-tuning-factory/actions/runs/42183843307
- 새 실제 GPU 검증, 외부 서비스 접속, 데이터 전송, 유료 자원 생성과 사용자 Pod 변경은 없었다.
  긴 입력 메모리·반복 출력 진단·대형/저속 산출물 회수·실제 그래프/SSH 추가 검증은 계속 남아 있다.

# 데스크톱 ENG/KOR와 영어 GIF — 2026-11-06

- 오른쪽 위 ENG/KOR 선택기, 즉시 전환과 PC별 언어 저장(`factory.language`, 초기 KOR)을 추가했다.
  한국어 원문 키와 영어 번역 1,697개를 공통 렌더링 계층으로 연결했다. 정적 문구를 번역한 뒤 동적 값을
  삽입하므로 사용자 프로젝트명·데이터·경로·모델 ID·원본 로그를 번역하지 않는다. 작성 중인 연결 값은
  메모리에만 유지하며 언어 변경으로 인증값을 저장하거나 실행을 제출하지 않는다.
- 영어 README에는 `factory-*-en.gif`, 한국어 README에는 `factory-*.gif`를 연결한다.
  영어 촬영은 실제 영어 프런트엔드와 합성 영어 예제·모의 브리지로 진행했다. 실제 GPU 실행 영상이 아니다.
  `scripts/render_readme_demos.py --language en|ko`로 재현한다. 개발 안내는 `docs/LOCALIZATION.md`.
- 실제 검증: Python 3.12.12 locked sync 성공. 전체 `uv run pytest`: **2245 passed, 2 skipped**,
  605.54초. `uv run ruff check src tests scripts` 통과. TypeScript/Vite production build 통과.
  영어 사전의 원문 키 누락 0개를 확인했다. 사전 보간 슬롯·언어 전환·재시작 유지·사용자 텍스트·인증 초안·
  계획 보존을 포함한 최종 UI 전체 검사 **291 passed**(3.8분). 최종 GIF 재촬영도 KOR **4 passed**(29.4초),
  ENG **4 passed**(28.9초)로 통과했다. 8개 GIF를 저장했고 영어 프로젝트·데이터·그래프 화면을 육안 확인했다.
  문서 상대 링크 검사와 `git diff --check`도 통과했다. Vite는 번역 사전 포함 번들이 500 kB를 넘는다는
  안내를 출력했다(최종 JS 약 568 kB, gzip 약 177 kB). 빌드는 성공했으며 네이티브 설치 앱 시험은 하지 않았다.
- 설치 앱 업데이트·설치 파일 배포는 하지 않았다. 원본 백엔드 진단은 번역된 설명 옆에 남을 수 있고,
  OS 파일 선택 창은 OS 언어를 따른다. 실제 GPU·외부 서비스 연결·데이터 전송·유료 자원·사용자 Pod
  변경은 없었다. 긴 입력 메모리, 반복 출력 진단, 대형·저속 산출물 회수와 실제 그래프/SSH 추가 검증은
  기존 미완료 상태이며 2026-10-31 기록을 참고한다.

# README 사용 스토리·영어/한국어·GIF — 2026-11-06

- `README.md`를 기본 영어로 재작성하고 `README.ko.md`에 같은 구성의 한국어 안내를 추가했다.
  프로젝트 → 데이터 매핑 → 레시피·학습 그래프 → 모의 계획 → 실행 기록의 사용 흐름으로 설명한다.
  기존 0.4.0 소개를 현재 소스·로컬 빌드 0.6.0에 맞추되 공개 릴리스 여부와 구분했다.
- `docs/images/factory-{project,data,graph,plan}.gif`: 실제 프런트엔드를 Playwright Chromium으로
  촬영했다. 기존 테스트 브리지와 합성 fixture만 사용했으며 자원·가격·계획·실행 응답은 모의 값이다.
  GPU 학습이나 모델 품질 영상으로 표시하지 않는다. 재현 도구는 `scripts/render_readme_demos.py`,
  촬영 설명은 `docs/images/README.md`. 1056×720, 6 fps, 무한 반복 GIF 네 개를 확인했다.
- 실제 검증: Python 3.12.12에서 `uv sync --python 3.12 --extra dev --extra tracking --locked` 성공.
  최종 GIF 촬영에 사용한 기존 UI 시나리오 4개 통과(29.3초). README 두 언어와 촬영 안내의 상대 링크 검사,
  `uv run ruff check src tests scripts`, `git diff --check` 통과.
  전체 `uv run pytest`: **2245 passed, 2 skipped**, 635.74초. GPU 통합 시험 등 opt-in 검사는 실행하지 않았다.
- 이 작업에서는 실제 외부 연결·모델 다운로드·데이터 전송·유료 자원 생성·사용자 Pod 변경을 하지 않았다.
  새 GPU 검증은 없다. 긴 입력 메모리, 반복 출력 원인, 대용량·저속 산출물 회수, 실제 그래프/SSH
  검증 확대 등 기존 GPU 후속 작업은 그대로 남아 있으며 아래 2026-10-31 기록을 참고한다.

# 0.6.0 최종 빌드·설치와 실제 앱 화면 끝까지 시험, 2026-10-31

- 최종 검증(`63d4f67`, 2026-10-31 06:03–06:25): `uv run pytest` 2245 passed, 2 skipped(492초); `uv run ruff check src tests scripts` 통과;
  `desktop`의 `npm run build` 통과, `npm test` 284 passed; `cargo test --manifest-path src-tauri/Cargo.toml --locked` 54 passed;
  `.factory/model-ops-cpu-venv`의 `scripts/verify_model_ops_cpu.py --receipt dist/model-ops-cpu-0.6.0.json` 통과(`stopped_after` null,
  모든 영수증 `model-ops-f32-v2`).
- 설치 프로그램(`63d4f67`): `desktop/src-tauri/target/release/bundle/nsis/LLM Tuning Factory_0.6.0_x64-setup.exe`, 48,355,092 바이트,
  SHA-256 `9be9862efbf9dfbe63deeabdf140d66e71287e79b082511556a99049966d5005`. 영수증 `dist/verify-0.6.0.json`: bridge_mode frozen,
  source_commit `63d4f67526916e506e7187dacb2ca22555da0786`, 모든 검사 true, gpu_started false, bridge_sha256 `9c0f6741…d865a`.
  앱이 꺼진 상태에서 `/S /D=%LOCALAPPDATA%\LLM Tuning Factory`로 같은 위치에 설치했다(0.6.0, 설치된 엔진 해시가 영수증과 같음).
- 사용자 Pod: `amdva0lurw9rv1`은 다시 켜기가 HTTP 500(용량 부족)으로 6번 모두 실패해 EXITED로 남겼다. 같은 볼륨 `cys1ne5znp`에 새
  사용자 Pod `mqpnial878ja1o`(이름 `llm-tuning-factory`, A40, CA-MTL-1, 시간당 $0.59, 공장 소유 기록 없음)를 만들고 SSH 지문
  `SHA256:HQVYDR33XaVZ5SJLF+YyLDmb47QO7q91RzcQE9wZNbI`을 공인 IP와 프록시에서 확인한 뒤, 앱 설정의 `pod_id`·`ssh_fingerprint`만 바꿨다.
- 실제 앱 화면 시험(`scripts/desktop_gui_acceptance.py`, Playwright 1.64.0, WebView2 원격 디버깅 포트 9223): 읽기 전용 예행 통과 후
  `--confirm-paid-run --max-cost 2`로 실행했다. 연결 검사(MLflow 기관 인증 확인, Pod RUNNING) → 환경 확인(A40 8.6, 45.0 GiB,
  드라이버 595.91.07, 환경 없음) → 합성 데이터 32건 등록(합성·전송 허용) → GPU 정보 적용 → Qwen3.5-0.8B SFT LoRA, 버전 고정 →
  실행 계획(예상 $0.59, 점검 항목 없음) → 승인 창(학습 환경 설치 안내와 이전 환경 정리 문구 포함) → 제출 → 대기 → 학습 중.
  학습은 Pod에서 COMPLETED(학습 환경 `0a8f737acf2ab5fd` 새로 준비, 이전 환경 정리), train_loss 1.3593, 학습 103초. 이 PC와
  CA-MTL-1 사이 네트워크가 초당 15–40 KB로 느려(같은 파일의 `scp`도 초당 약 31 KB) 결과 122 MB를 받는 데 08:22까지 걸렸고,
  스크립트는 45분 제한에 걸려 실패로 끝났다(작업 진행 중이라 앱은 열어 둠). 작업이 완료된 뒤 같은 포트로 다시 붙어 실행 관리에서
  "완료"와 학습 로그(train_loss 1.3593)를 확인했다. 이 실행의 기록은 승인 창 문구대로 공용 MLflow에 저장된다. 예전 작업
  `76986bce`는 "취소" 상태다. 끝난 뒤 Pod `mqpnial878ja1o`를 멈췄다(EXITED). 시험이 띄운 앱 창은 Claude Code 권한 검사가 종료를
  막아 열려 있으며 사용자가 닫는다.
- 남은 일: 화면 시험 스크립트의 작업 대기 제한(45분)이 느린 결과 내려받기보다 짧다. 위 GPU 시험의 남은 일과 함께 처리한다.

# 2단계 GPU 인수 시험(A40) — 결과, 2026-10-31

공장 소유 Pod `r0d9wiystzwx0m`(A40 48 GB, compute capability 8.6, 드라이버 595.91.07, CA-MTL-1, 볼륨 `cys1ne5znp`)에서
2026-10-30 23:05–10-31 06:03(약 6.95시간, 약 $4.10) 시험했다. 합성 데이터 32행, MLflow 끔. 앱 엔진은 `dist/factory-bridge.exe`로,
1차는 `560a2b4` 빌드, 2차는 `bc020b0` 빌드(아래 템플릿·허용 오차·단계 수정 포함). SSH 지문은 공인 IP와 RunPod 프록시에서 같았다
(공인 IP 쪽은 `provision.py`의 스캔이 비어 Git Bash에서 다시 읽었다). 끝난 뒤 `ManagedPods.stop`으로 EXITED를 확인했다.

| 사례 | 결과 | train_loss | 최대 GPU 메모리(MiB) |
|---|---|---|---|
| Qwen3.5-0.8B SFT LoRA(평가, 1차 `qwen3_5_nothink`) | COMPLETED, 실행 승인 안 환경 준비·이전 환경 1개 정리 | 0.148 | 3,107 |
| 〃 SFT QLoRA 4비트 | COMPLETED | 0.148 | 2,287 |
| 〃 DPO / PT | COMPLETED / COMPLETED | 0.192 / 0.449 | 5,271 / 5,683 |
| 〃 KTO | FAILED(엔진: M-RoPE `mm_token_type_ids` 없음) → 앱에서 거부 | — | — |
| 〃 SFT full / freeze(mlp) | COMPLETED / COMPLETED(Pod의 `result.json`) | 2.728 / 0.337 | 16,481 / 3,143 |
| 〃 SFT LoRA(평가, 2차 `qwen3_5`) | COMPLETED(Pod의 `result.json`) | 0.123 | — |
| gemma-4-E2B-it SFT LoRA(평가) | COMPLETED, 2차 환경 준비·이전 환경 1개 정리, json_validity 0.84 | 1.115 | 12,761 |
| 〃 DPO / PT | COMPLETED / COMPLETED | 0.221 / 1.169 | 18,711 / 14,823 |
| 〃 KTO | FAILED(엔진: `pixel_position_ids` bool) → 앱에서 거부 | — | — |
| Qwen3.5-9B SFT LoRA | COMPLETED | 0.184 | 19,825 |
| RM(앱 밖, 런타임의 `llamafactory-cli`) Qwen3.5-0.8B / gemma-4-E2B-it | 통과: `value_head.safetensors`, eval_accuracy 1.0, eval_loss 0.0 / 0.0033 | 0.191 / 0.162 | — |

- 대상·확정(`q08-sft-lora` 어댑터, Pod에서): `inspect_linear_modules` 186개 = `resolved_lora_targets("all")` 186개(linear
  attention 5종 18개씩, MLP 3종 24개씩, full attention 4종 6개씩), 어댑터 `target_modules`는 잎 이름 12개로 줄어 기록됐고 PEFT의
  접미사 규칙을 전체 534개 모듈(비전 포함)에 적용해도 정확히 186개만 고른다. 텐서 372개 모두 `model.language_model.` 아래.
  1차 런타임(v1 허용 오차)에서는 확정이 로짓 비교로 거부됐고(아래), `bc020b0` 런타임에서 CUDA 확정이 발행됐다: `model-ops-f32-v2`,
  델타 2.84e-8, 로짓 최대 절대 오차 1.79e-5(한계 1.91e-3), 상대 L2 9.8e-7(한계 1e-5), 출력 473개 = 기본 488개 − `mtp.*` 15개,
  `model.visual.*` 153개 그대로, 토크나이저·processor 파일 7개 바이트 동일. 확정 결과를 LLaMA-Factory 예측(`finetuning_type: full`,
  `qwen3_5`, 생각 끔)으로 불러 4행 생성(생각 블록 없음).
- 시험에서 찾아 고친 결함(각각 시험을 먼저 작성): Qwen3.5 기본 템플릿 `qwen3_5_nothink` → `qwen3_5`(`2c932b9`; 1차 예측이
  모두 `<think>\n\n</think>\n\n`으로 시작해 json_validity 0.0, LLaMA-Factory `qwen3_5`는 생각 끔일 때 빈 생각 블록을 프롬프트에 넣는다),
  확정 로짓 허용 오차 `model-ops-f32-v2`(`382409d`), 단계 정책(`bbc40ca`·`1cd33be`: 두 계열 모두 pt·sft·rm·dpo, KTO 거부),
  MoE 전문가 가중치의 QLoRA 추정(`bc020b0`), 검증 표시와 로짓 메모리 항(`5bce65c`·`df0011f`), 최종 검토 수정(`63d4f67`:
  저장된 `qwen3_5_nothink` 설정에 한국어 경고, 템플릿 불일치 오류 한국어화, RM 로짓 계수 2, 허브 프로필 `vocab_size`,
  v1 영수증 수거 허용, Qwen3.6 이름).
- 2차 `qwen3_5` 평가: 32개 예측 모두 `<think>` 없이 JSON으로 시작했지만, JSON 뒤에 `\n</think>\n\n`과 같은 JSON을 한 번 더 내
  원시 JSON 유효성은 1/32였다. 32행·8단계 학습으로 끝맺음(`<|im_end|>`)을 배우지 못한 것과 합성 정답의 끝 줄바꿈 때문으로
  보지만 확인하지 않았다(같은 조건의 gemma-4-E2B-it는 0.84). 실제 데이터와 더 긴 학습으로 다시 볼 일이다.
- 운영 기록: (1) `provision.py`를 PowerShell `Start-Process`로 부를 때 인자 "NVIDIA A40"이 갈라져 첫 시도가 실패했다(Pod 없음).
  (2) 삭제한 1단계 시험 Pod 7개의 소유 기록이 남아 `ManagedPods.create`가 HTTP 404로 멈췄다 — 404를 확인한 기록과 요청만
  `managed-pods/deleted-20261030/`으로 옮겼다. (3) full 결과(체크포인트 포함 12 GB)를 받는 데 약 2시간 걸렸고, freeze 결과(3.5 GB)는
  네트워크가 초당 약 37 KB로 떨어져 받지 않고 멈췄다(Pod에서 결과 확인). (4) 묶음 실행 스크립트가 full 사례 뒤 이유를 알 수 없이
  끝나 freeze·gemma 사례가 2차로 밀렸다. (5) 앱이 올린 `train.yaml`은 CRLF다(LLaMA-Factory는 문제없이 읽음). (6) 시험 엔진
  저장소 `phase2-a40/engine`에는 중단된 freeze 작업이 RUNNING으로 남아 2차 `qwen3_5` 평가는 같은 Pod에 새 저장소(`phase2-a40b`)로 했다.
- 남은 일: full·freeze 결과에 옵티마이저 상태가 든 체크포인트까지 내려받는 문제(`save_only_model: false`; 0.8B full이 12 GB),
  느린 결과 내려받기, 긴 행의 메모리 측정, 복합 구조 KTO(엔진 수정 대기), 2차 평가의 반복 출력 원인, 볼륨 `cys1ne5znp`에 남은
  시험 작업·HF 캐시(약 30 GB)·`target-check-20261031`·`rm-trial-*` 폴더 정리.

# Qwen3.5·Gemma 4 A40 결과 반영 — 단계 정책, 검증 표시, 로짓 메모리 항, 2026-10-31

- 단계: Gemma 4도 Qwen3.5처럼 pt·sft·rm·dpo(`model_hub.COMPOSITE_STAGES` 하나로 합침). Gemma 4 RM은 엔진 수준 시험
  (`value_head.safetensors`, eval_accuracy 1.0, eval_loss 0.0033)으로 허용하고 값 헤드 거부를 지웠다. Gemma 4 KTO는 A40에서
  `AttributeError: 'bool' object has no attribute 'all'`(transformers `modeling_gemma4.py:1999`의
  `(pixel_position_ids == -1).all(dim=-1)`; LLaMA-Factory KTO의 KL 배치가 텐서가 아닌 멀티모달 입력을 넘김)로 실패해
  "Gemma 4의 KTO 학습은 현재 학습 엔진(...)에서 KL 배치의 멀티모달 입력 처리 오류로 실패하므로 지원하지 않습니다."로 거부한다.
- 검증 표시: `capabilities.VERIFIED_COMPOSITE`로 `Qwen/Qwen3.5-0.8B`("0.6.0 SFT LoRA/QLoRA/full/freeze, DPO, PT, RM (engine-level),
  materialize synthetic A40"), `Qwen/Qwen3.5-9B`("0.6.0 SFT LoRA synthetic A40"), `google/gemma-4-E2B-it`("0.6.0 SFT LoRA, DPO, PT,
  RM (engine-level) synthetic A40")에 `gpu_verified: true`, `verified_capabilities: ["8.6"]`. Base와 다른 크기는 미검증이고,
  검증된 세 모델도 8.6이 아닌 세대(예: H100 9.0)에서는 세대 주의가 나온다.
- 메모리: 복합 기본 목록 항목에 `vocab_size`(Qwen3.5 248320, Gemma 4 262144, `SIZE_FACTS` 포함)를 두고 `estimate_memory`에 LM head
  로짓 `배치 × cutoff_len × vocab_size × 11바이트 × (dpo·kto 3) × (full 2)`를 더했다. A40 nvidia-smi 최대치(배치 1, cutoff_len 1024,
  합성 데이터, SFT·DPO 행은 약 60–100토큰, PT만 cutoff_len까지 채움) 대 추정(GiB, 보정 전 → 후): Qwen3.5-0.8B SFT LoRA 3.03 대
  3.32 → 5.92(1.95배), QLoRA 2.23 대 2.48 → 5.09, DPO 5.15 대 3.32 → 11.13, PT 5.55 대 3.32 → 5.92, full 16.09 대 13.02 → 18.23,
  freeze 3.07 대 4.02 → 6.63; gemma-4-E2B-it SFT LoRA 12.46 대 11.89 → 14.64(1.17배), DPO 18.27 대 11.89 → 20.14, PT 14.48 대
  11.89 → 14.64; Qwen3.5-9B SFT LoRA 19.36 대 21.20 → 23.80(1.23배). 모든 실측이 추정 이하, SFT LoRA는 2배 이하. 근거와 규칙은
  설계 4.5. `vocab_size`가 없는 모델은 기본 목록 모든 모델 × 방법 5 × 단계 6 × 크기 2(1,020개)에서 보정 전과 비트 단위로 같고,
  가중치 추정은 2,160개 모두 같다.
- 검증(2026-10-31): 시험을 먼저 고쳐 실패를 확인했다(단계 9개, 검증 표시 3개, 메모리 9개 실패 → 구현 후 통과).
  `uv run pytest` 2229 passed, 2 skipped, `uv run ruff check src tests scripts` 통과.
- 남은 일: cutoff_len까지 채운 긴 행의 SFT·DPO와 full, RM의 최대 메모리 측정. full 초과분(짧은 행에서 약 3GiB)은 로짓보다 fp32
  학습 가중치·옵티마이저 임시 버퍼(학습 매개변수당 약 4바이트)로 보여 `FULL_STATE_BYTES` 재보정이 맞지만, 모든 모델의 추정이
  바뀌므로 이번에는 로짓 항 2배로 덮었다. 기본 목록 밖의 허브 모델은 `vocab_size`를 기록하지 않아 로짓 항이 없다. 복합 구조 KTO는
  엔진 수정을 기다린다.

# 모델 작업 로짓 허용 오차 `model-ops-f32-v2`, 2026-10-31

- 결함(A40 GPU 인수에서 발견): 실제 Qwen3.5-0.8B LoRA 어댑터의 올바른 확정을 `materialize_model`이
  `reloaded logits differ beyond tolerance policy`로 거부했다. Pod에서 잰 값: 확정 모델과 병합 전 PEFT 모델의 FP32 로짓
  (5토큰 × 어휘 248,320) 최대 절대 오차 CPU 2.24e-5(1,241,600개 중 33개가 v1 경계 atol 1e-5 + rtol 1e-4 × 기대값의 절댓값을
  넘음, 최대 1.35배), CUDA 1.79e-5(4개, 1.13배), 평균 약 1.9e-6, 로짓 최대 절댓값 19.1. 메모리 안 병합 모델과 저장 후 다시 불러온
  모델은 비트 단위로 같았고 델타는 2.8e-8로 통과했다. v1의 원소별 allclose는 0 근처 로짓에 rtol 여유가 없어 24층의 FP32 반올림을
  견디지 못한다(v1의 CPU 시험은 작은 모델만 썼다).
- 수정: 새 정책 `model-ops-f32-v2`(`model_ops.CURRENT_POLICY`). 저장(F32)과 델타 규칙은 v1과 같고, 로짓은 상대 L2 오차 ≤ 1e-5와
  최대 절대 오차 ≤ 1e-4 × max(1, 기대 로짓의 최대 절댓값)을 모두 만족해야 한다(모두 유한). v1은 `TOLERANCE_POLICIES`에 뜻 그대로
  남아 이전 영수증을 읽는다. `merge_adapters`·`materialize_model`의 기본값, `operation_dispatch`(호출과 영수증 검사),
  `operation_outputs`(접수 검사), `simulated_dispatch`, `scripts/verify_model_ops_cpu.py`가 `CURRENT_POLICY`를 쓴다. 판정은 torch
  없는 `scale_aware_logits_ok`(통계 4개)로 분리했고, CPU 시험 스크립트는 같은 규칙을 따로 구현해 상대 L2 오차도 기록한다.
  dtype 거부 문구는 `... under policy model-ops-f32-v2`가 된다.
- 검증(2026-10-31): 시험을 먼저 고쳐 17개 실패를 확인한 뒤 구현했다. 측정값 모양의 합성 로짓(5 × 4096, 최대 절댓값 19.1, 평균
  절대 오차 약 1.9e-6, 0 근처 4개에 2.24e-5)은 v1에서 거부되고 v2에서 통과하며, 값 하나가 1e-2 틀린 로짓과 상대 L2 오차 1e-4인
  로짓은 v2에서 거부된다. 실제 크기(1,241,600개) 통계로는 값 하나의 1e-2 오류가 L2 조건을 통과하고 최대 절대 오차 조건에서
  거부된다. `uv run pytest` 2204 passed, 2 skipped, `uv run ruff check src tests scripts` 통과.
- CPU 수치 검증(`.factory/model-ops-cpu-venv`, 영수증은 임시 폴더): 종료 코드 0, **35/35 통과**, `stopped_after` null, 모든 영수증의
  `policy_id`가 `model-ops-f32-v2`. 로짓 오차(최대 절대 / 상대 L2): 텍스트 병합 3.4e-8 / 1.2e-7, 확정 3.0e-8 / 1.3e-7;
  `qwen3_5` 병합 2.0e-7 / 5.1e-7, 확정 8.9e-8 / 2.2e-7; `gemma4` 병합 7.1e-7 / 1.0e-6, 확정 6.9e-7 / 8.3e-7.
- GPU 재시험(2026-10-31, `bc020b0` 런타임): 0.8B 확정이 발행됐다. 로짓 최대 절대 오차 1.79e-5, 상대 L2 9.8e-7(위 결과 절).
  병합은 Pod에서 다시 재지 않았다.

# 2단계 GPU 인수 시험(A40) — 승인 범위, 2026-10-30

사용자 지시(2026-10-30): "2단계 및 후속 과제도 내 허락 받지 않고 계속 다 테스트해가면서 구현해줘". 이에 따른 유료 시험 범위:

- 공장 소유 Pod만 만든다(`ManagedPods`, 정책: GPU 1개, 실행당 $5, 최대 4시간, 다이제스트 고정 이미지). A40 1대, CA-MTL-1, 시간당 $0.59.
  볼륨 `cys1ne5znp`(80 GB)를 쓴다. 같은 볼륨의 사용자 Pod `amdva0lurw9rv1`은 EXITED로 두고 건드리지 않는다.
- 데이터는 결정적으로 만든 합성 데이터(32행)만 쓰고 MLflow는 끈다. 엔진은 `claude/phase2-models`에서 빌드한 `dist/factory-bridge.exe`.
- 사례: Qwen3.5-0.8B SFT LoRA(평가 포함)·QLoRA·DPO·KTO·PT·full·freeze, gemma-4-E2B-it SFT LoRA, Qwen3.5-9B SFT LoRA, RM 시험(앱 밖),
  0.8B 어댑터의 대상·확정 확인. 끝나면 Pod를 멈추고 EXITED를 확인한다. 드라이버는 저장소 밖 `%TEMP%\gpu_accept\run_phase2.py`.

# Qwen3.5·Gemma 4 텍스트 학습 (2단계) — Task 13–14 대상 규칙·병합·확정과 CPU 수치 검증, 2026-10-30

[설계](superpowers/specs/2026-10-30-phase2-latest-models-design.md) · [계획](superpowers/plans/2026-10-30-phase2-latest-models.md) ·
브랜치 `claude/phase2-models`.

- Task 13(torch 없이): 복합 모델(`qwen3_5`·`qwen3_5_moe`·`gemma4`)의 LoRA 대상은 `model.language_model.` 아래 Linear다. 헤더 증거는
  `qwen3_5`·`gemma4`를 받아 언어 모델 텐서만 본다(비전·오디오·투영·`mtp.*` 무시, `linear_attn.conv1d` 3차원 커널 허용, 전문가 텐서와
  `qwen3_5_moe`는 계속 거부). 계획의 대상 비교는 잎 이름(`target_leaves`)으로 하고, 모의 실행 어댑터는 `resolved_lora_targets`로
  전체 이름을 쓴다. `verify_inspected_targets`는 증거가 모두 언어 모델 안에 있을 때만 기대 집합을 줄인다(비전 모듈을 함께 내는
  gemma3 증거는 전체 규칙 그대로).
- Task 14: 복합 모델은 meta 대상 검사와 병합·확정의 FP32 로드를 `AutoModelForImageTextToText`로 한다. 확정의 키 집합 검사는
  불러온 모델이 선언한 "불러오지 않는 키"(`_keys_to_ignore_on_load_unexpected`, transformers처럼 `re.search`로 맞춤)만 기대 집합에서 뺀다.
  Qwen3.5는 `^mtp.*`, Gemma 4는 KV 공유 층의 `self_attn.k_proj`·`v_proj`·`k_norm`·`v_norm`이고, 텍스트 모델은 선언이 없어 그대로다.
- 검토에서 찾은 결함과 수정(2026-10-30): Gemma 4 E2B는 35층 중 20층이 앞 층의 key/value를 공유하는데(`num_kv_shared_layers`), 체크포인트
  헤더에는 그 층들의 `k_proj`·`v_proj`·`k_norm`이 남아 있고 transformers 5.6은 만들지도 불러오지도 않는다. 그래서 헤더 증거에 없는 모듈이
  생겼고(meta 26개 대 헤더 28개), 그 증거로는 이어 학습 대상 검사가 거부했으며, 확정은 키 집합 검사에서 `DELTA_FAILED`로 실패했다.
  torch 없는 쪽은 config에서 같은 목록을 만드는 `target_inspection.unloaded_modules`를 더해 헤더 증거(`model_root_targets`가 해시로 묶인
  루트 config.json을 읽어 넘김)와 `verify_inspected_targets(config=...)`에 쓰고, torch 쪽은 위 선언을 쓴다. CPU 검증은 transformers의 선언과
  `unloaded_modules`가 같은지도 확인한다.
- PEFT 0.18.1은 20개 이상의 전체 이름을 가장 짧은 구별 접미사로 기록한다(CPU 검증에서 Qwen3.5 `all` 31개 → 잎 이름 12개,
  Gemma 4 `all` 26개 → `language_model.layers.0.self_attn.q_proj`, `1.self_attn.q_proj` 등 22개; Gemma 4 비전 타워도 q_proj~down_proj를 쓰기 때문).
  앱의 비교는 잎 이름과 접미사 일치로 하므로 그대로 동작하고, 접미사로 기록된 두 어댑터의 병합도 통과한다. 실제 0.8B·9B의 `q_proj,v_proj`는
  20개 미만이라 전체 이름으로 남는다.
- CPU 수치 검증(2026-10-30, 수정 후, `.factory/model-ops-cpu-venv`: torch 2.7.1+cpu, transformers 5.6.0, peft 0.18.1):
  `scripts/verify_model_ops_cpu.py --receipt dist/model-ops-cpu-0.6.0.json` 종료 코드 0, **35/35 통과**, `stopped_after` null
  (검사 수는 그대로이고 복합 병합 검사 안에 `all` 어댑터 병합이 더해져 병합 4건·확정 4건을 확인한다). 기존 29개 검사 기록과 텍스트 모델의
  병합·확정 영수증은 이전 영수증(`.factory/model-ops-cpu-receipt.json`)과 같다(텍스트 모델 출력 불변). 새 6개:

| 작은 무작위 모델 | 대상 검사 | 병합(r2+r3 → r5) | 확정(q·v 어댑터와 `all` 어댑터) |
|---|---|---|---|
| `qwen3_5`(linear_attn 3층 + self_attn 1층, 비전 1블록, 가짜 `mtp.fc.weight`) | 언어 모델 Linear 31개, meta = 헤더 = 동결 산출물, 검증 통과 | q·v 2개(전체 이름)와 `all` 31개(접미사 12개): 델타 ≤ 1.1e-8, 로짓 ≤ 2.0e-7 | 텐서 77개 = 기본 78개 − mtp, 비전 21개 동일, 델타 ≤ 1.5e-8, 로짓 ≤ 8.9e-8 |
| `gemma4`(3층 중 마지막 층이 KV 공유, 그 층의 원본 k/v/k_norm 텐서 포함, 층별 입력 투영, 비전 1층, 오디오 없음) | 26개, 같은 일치(config 없이 원본 체크포인트 증거는 거부) | q·v 5개와 `all` 26개(접미사 22개): 델타 ≤ 1.6e-8, 로짓 ≤ 7.1e-7 | 69개 = 기본 72개 − KV 공유 텐서 3개, 비전·투영 16개 동일, 델타 ≤ 1.8e-8, 로짓 ≤ 6.9e-7 |

  로짓은 병합·확정 결과를 기본 모델 가중치에 델타를 직접 더한 모델과 비교했다(허용 오차 정책 `model-ops-f32-v1`). 확정 결과의
  `architectures`는 복합 클래스이고, 토크나이저와 `preprocessor_config.json`이 바이트 그대로 복사되며, 동결 뒤 대상 검사도 통과한다.
  Task 14 소스 전(Task 13 커밋)에는 대상 검사 둘이 계획대로 실패했고(`'Qwen3_5Config' object has no attribute 'vocab_size'`,
  `'Gemma4ClippableLinear' object has no attribute 'in_features'`), 텍스트 전용 로더로는 Qwen3.5 병합·확정이 어댑터 대상을 찾지 못했으며,
  `mtp.*` 제외 없이는 Qwen3.5 확정이 키 집합 검사에서 실패했다. 검토 수정 전 규칙(`mtp.*`만 제외, config 없는 헤더 증거)으로는 KV 공유
  Gemma 4의 대상 검사와 확정(`DELTA_FAILED`)이 실패했다.
- 계획과 다른 점: `GEMMA4` 상수에 `vocab_size_per_layer_input=32`, `hidden_size_per_layer_input=8`, `global_head_dim=16`,
  `num_kv_shared_layers=1`, `layer_types=["full_attention","sliding_attention","full_attention"]`을 더하고 층을 3개로 했다(기본 층별 임베딩은
  fp32로 약 0.5 GB이고, 3층이면 `all`이 20개를 넘어 PEFT 접미사 기록을 시험한다). 병합 검사는 q·v 어댑터에 더해 `all` 어댑터 둘도 병합하고,
  확정 검사는 q·v 어댑터에 더해 `all` 어댑터도 확정한다.
- 남은 일(GPU 인수): 실제 LLaMA-Factory 어댑터의 `target_modules`와 meta 검사 비교, Qwen3.5-0.8B 어댑터 병합, gemma-4-E2B-it의
  헤더 증거와 실제 모듈 비교(KV 공유 20층).

# Task 16 GPU 인수 시험 — 승인 범위와 결과, 2026-10-30

사용자 승인(2026-10-30): Blackwell Pod `yuft63rjggvh5z`(1 × RTX PRO 4000 Blackwell, 시간당 $0.57, 이미 실행 중)에서 먼저 시험하고,
이후 사용자가 알려 줄 Ampere 또는 Hopper Pod에서 회귀 시험한다. 데이터는 합성 데이터(32행, 이전 GPU 인수 시험의
`desktop_gpu_train` 원본)만 쓴다. 이 PC의 앱은 기존 위치에 0.5.0으로 업그레이드한다.

- 범위: Pod의 `/workspace/factory-desktop` 아래 학습 환경 설치, Qwen2.5-0.5B-Instruct SFT LoRA 1회, GPU 메모리 관측.
- 제외: 실제 MLflow 서버 기록(별도 승인 대상이므로 시험 설정에서 MLflow를 끈다), 실제 연구 데이터 전송.
- 빌린 Pod는 멈추거나 지우지 않는다. 시험 상태는 `state/gpu-acceptance-20261030/`(git 제외)에 둔다.

**시도 결과(2026-10-30):** 시작하지 못했다. 첫 단계(환경 확인)에서 RunPod API가 Pod `yuft63rjggvh5z`에 HTTP 404를 돌려주었다.
같은 키로 보이는 Pod 55개(실행·정지 포함) 목록에도 없고, 전날 주소의 SSH도 거부되어 Pod가 삭제된 것으로 본다.
원격 접속 전에 멈췄으므로 설치·데이터 전송·학습은 없었다. 다른 사람의 실행 중 Pod는 쓰지 않았다.
엔진은 최종 코드 `0163b8a`로 빌드한 `dist/factory-bridge.exe`(설치 파일에 들어간 것과 바이트 동일)로 실행했고,
이 PC의 설치된 앱은 실행 중이라 이번에는 업그레이드하지 않았다.

**추가 승인(2026-10-30):** "api key 이용해서 필요한 runpod 진행해". 이 PC의 앱을 닫은 뒤, 저장된 RunPod API 키로 시험용 네트워크 볼륨과
Pod를 직접 만들어 시험했다. Pod는 모두 `ManagedPods`(정책: GPU 1개, 실행당 $5, 4시간, 다이제스트 고정 이미지, 네트워크 볼륨 필수)로
만든 공장 소유 Pod이고, SSH 서버 지문은 공인 IP와 RunPod SSH 프록시 두 경로에서 같은지 확인한 뒤 고정했다. 데이터는 같은 합성 32행,
MLflow는 껐다. Ampere와 Hopper를 모두 시험했다.

**결과(엔진 `9871d8b`로 빌드한 `dist/factory-bridge.exe`, Qwen2.5-0.5B-Instruct SFT LoRA):**

| GPU | compute capability · 드라이버 | Pod · 볼륨 | 환경 | 결과 |
|---|---|---|---|---|
| RTX PRO 4000 Blackwell 24 GB | 12.0 · 595.91.07 | `02o4sst2tbacov` · `ls9ua5ymse`(EU-RO-1) | 실행 승인 안에서 준비, 실행 706초 | COMPLETED(시도 1), train_loss 0.148, train_runtime 12.2초 |
| A40 48 GB | 8.6 · 595.91.07 | `p7qe8lr3a0yzm8` · `cys1ne5znp`(CA-MTL-1) | 실행 승인 안에서 준비, 실행 416초 | COMPLETED(시도 1), train_loss 0.148, train_runtime 9.5초 |
| H100 80GB HBM3 | 9.0 · 595.91.07 | `zi5ftxz6bj0q22` · `cys1ne5znp`(CA-MTL-1) | A40이 만든 환경을 그대로 사용(`ready`), 실행 238초 | COMPLETED(시도 1), train_loss 0.147, train_runtime 10.4초 |

- 시험 방법: 앱 창(화면)은 조작하지 않았다. 계획 Task 16 Step 1의 화면 조작 대신, 화면이 보내는 요청 순서(환경 확인 → 데이터 등록 → 계획 →
  제출 → 실행(`prepare_runtime`) → 작업·로그)를 시험 드라이버가 엔진에 한 줄 JSON으로 직접 보냈다. 계획 카드의 거부·주의 표시, 승인 창의 설치 안내,
  준비 중 안내 같은 화면 동작은 `npm test`로만 확인했다. 0.5.0의 "연결 검사"(MLflow 포함)는 실행하지 않았다. 실제 앱 화면으로 처음부터 하는
  시험이 남아 있다.
- 세 GPU 모두 `simulation: false`, `training_success: true`, bf16 지원, 어댑터(`adapter_model.safetensors`) 저장, 런타임 torch `2.7.1+cu128`,
  transformers 5.6.0, LLaMA-Factory 0.9.5, Python 3.12.3. 한 환경이 sm_86·sm_90·sm_120에서 모두 동작했다.
- 점검 결과: Blackwell·A40에서는 `MODEL_UNVERIFIED` 경고만(그때 검증 목록이 9.0뿐), H100에서는 항목 없음. 거부 항목 없음.
  H100은 시간당 $3.99라 시험 설정의 예산을 $4.00/시간·$4.50로 올렸다(기본 $0.60·$2.00이면 요금 초과로 거부되는 것이 맞다).
- 메모리(학습 중 별도 SSH의 `nvidia-smi` 5초 간격): Blackwell 최대 1,494 MiB, H100 최대 1,977 MiB. 앱 추정치는 합계 2.58 GiB(가중치 0.93 GiB)로
  관측치보다 크다(보수적). 학습이 10초 남짓이라 5초 간격 표본은 순간 최대를 놓칠 수 있다. A40은 측정하지 못했다(아래). 계수 보정 제안은 없다.
- 검증 범위 반영(`2815c03`): `model_catalog()`의 Qwen2.5-0.5B-Instruct `verified_capabilities`를 `["8.6", "9.0", "12.0"]`로,
  `verification_scope`에 0.5.0 SFT LoRA 범위를 적었다. QLoRA와 다른 학습 단계는 이번에 GPU에서 검증하지 않았다.

**시험 중 찾아 고친 결함(각각 테스트를 먼저 작성해 실패를 확인):**
- `8f08390`: 런타임 확인 probe가 60초 제한에 걸렸다. 네트워크 볼륨에서 처음 torch를 import하는 데 60초를 넘는다. probe만 900초로 늘렸다.
- `1685eef`: transformers 5.6.0이 `save_safetensors` 인자를 거부해 학습이 시작 직후 실패했다(설치된 0.5.0 `0163b8a` 빌드에도 있는 결함).
  인자를 넘기지 않는다(5.x는 항상 safetensors, PPO의 4.52.4는 기본값이 참).
- `9871d8b`: 볼륨 용량 초과로 중단된 설치가 torch를 반쯤 설치된 채 남겼고(`module 'torch' has no attribute '__version__'`),
  `uv pip sync`는 이를 설치됨으로 보아(`uv pip check`도 통과) 준비 직후 확인이 계속 실패했다. 준비할 때마다 개인 venv를
  `uv venv --clear`로 다시 만든다(원격 `flock` 안).

**운영 기록:**
- 처음 Blackwell 시도들에서 RunPod 용량 부족(HTTP 500 "does not have the resources")으로 생성·재시작이 실패해 같은 볼륨에 새 Pod를 만들었다.
  40 GB 볼륨이 런타임 여러 벌로 가득 차 `ls9ua5ymse`를 80 GB로 늘렸다. 새 볼륨 `cys1ne5znp`는 80 GB로 만들었다.
- 정책이 실행 중인 Blackwell Pod와 동시에 A40을 만드는 것을 "total owned GPU count exceeds policy"로 거부했다(의도된 동작). 순서대로 실행했다.
- 마지막 Blackwell 실행은 학습이 끝난 뒤 로그 수집 중 Claude Code가 PC 메모리 부족으로 시험 드라이버를 종료했다. Pod는 `ManagedPods.stop`으로 직접 멈췄고,
  메모리 기록은 남은 `gpu-memory.csv`에서 계산했다. 같은 이유로 그때의 전체 테스트 실행도 끝나지 않았다(아래에서 다시 실행).
- SSH 개인 키가 바탕화면에서 사라져, 메신저 임시 폴더(`AppData\Local\AtMessenger\temp`)에 있는 같은 이름의 키를 썼다. Pod에 넣은 공개 키와
  같은 쌍이고 실제 인증되는 것을 확인했다(공개 키 지문 `SHA256:jTHM4Owf2rDiR9HNR12P2LbxNwk+DudZTgpP3z6HaOU`). 이 파일은 상속 권한으로 다른 계정
  (`CodexSandboxUsers` 등)도 수정할 수 있어 Windows OpenSSH가 키를 거부한다. 이 때문에 A40 실행의 메모리 감시(Windows OpenSSH로 실행됨)가
  아무것도 기록하지 못했다. 앱(paramiko)은 영향이 없었다. 앱 설정의 `ssh_key_file`은 여전히 바탕화면 경로를 가리킨다.
- 정리: 공장 소유 시험 Pod 7개(위 3개와 실패한 Blackwell 시도 `36ncz2rp4rqkcu`·`cpc3fjmd36wf3f`·`2u65h3mq0i3svu`·`9w8n2eu6ur1zd2`)를 멈춘 뒤 삭제했다.
  볼륨 `ls9ua5ymse`(EU-RO-1, 80 GB)와 `cys1ne5znp`(CA-MTL-1, 80 GB)는 사용자 결정을 기다리며 남겼다(합성 시험 결과와 런타임만 있음,
  RunPod 네트워크 볼륨 요금으로 둘 합쳐 대략 월 $11). 다른 사람의 Pod는 쓰지 않았다.
- 비용(추정, 정확한 금액은 RunPod 청구 내역): Blackwell Pod들 $0.57/시간로 합계 약 2–3시간, A40 $0.59/시간 약 15분, H100 $3.99/시간 약 12분.

- 검증(2026-10-30, `953168a`): `uv run pytest` 2031 passed, 1 skipped(494.74초); `uv run ruff check src tests scripts` 통과.
  그 전 실행(2815c03)에서 `tests/test_model_inputs.py`의 고정 다이제스트 2개가 실패했다. `1685eef`가 학습 설정에서 `save_safetensors`를 뺐기
  때문이고, 그 키를 다시 넣으면 이전 다이제스트가 그대로 나오는 것을 확인한 뒤 다시 고정했다(`953168a`). 데스크톱(TypeScript·Rust)은 바뀌지 않았다.
- 설치 프로그램 다시 빌드(`953168a`): `desktop/src-tauri/target/release/bundle/nsis/LLM Tuning Factory_0.5.0_x64-setup.exe`,
  48,279,277 바이트, SHA-256 `18b27086335e506ea70eed8ba35ae3b2d4866c41d7692fcbdfe4b28076aaf13e`. 영수증 `dist/verify-0.5.0-acceptance.json`:
  bridge_mode frozen, source_commit `953168a91885cce3cce260868ebbc207a325e066`, 모든 검사 true, gpu_started false.
  이 빌드의 엔진 소스는 GPU 인수에 쓴 `9871d8b` 빌드와 모델 목록의 검증 범위만 다르다.

- 이 PC 재설치(2026-10-30 16:11): 설치된 0.5.0(`0163b8a`)에는 위 세 결함이 있어 transformers 5 학습이 실패하므로, 앱이 꺼진 상태에서 위 설치
  프로그램을 `/S /D=%LOCALAPPDATA%\LLM Tuning Factory`로 같은 위치에 설치했다(종료 코드 0). 설치된 `factory-bridge.exe`의 SHA-256
  `73c3522dbea4fec9472be1d50f8c859e64eea169948ad12764c30b85de400e71`이 영수증의 `bridge_sha256`과 같고, 앱 설정 파일은 바뀌지 않았다.

- 사용자 요청 "ssh 키 옮기고 새 pod도 api key로 해줘"(2026-10-30): 키 쌍을 `%USERPROFILE%\.ssh\id_ed25519_runpod(.pub)`로 옮겼다(내용 해시 동일
  확인 후 메신저 임시 폴더의 원본 삭제). 상속 권한을 끊고 본인 계정과 SYSTEM만 남겨 Windows OpenSSH도 키를 받아들인다.
  새 사용자 Pod `amdva0lurw9rv1`(이름 `llm-tuning-factory`, A40 48 GB, CA-MTL-1, 시간당 $0.59, 볼륨 `cys1ne5znp`)를 공장 소유 기록 없이 만들었다.
  Blackwell은 EU-RO-1 재고가 Low이고 시험 중 생성이 여러 번 실패해 A40을 골랐다. 앱이 빌린 Pod로 다루므로 자동으로 멈추지 않는다.
  SSH 서버 지문 `SHA256:GVLJvq8jBbwib7RXw4CYjUlm3oO+71UyQ+S3Pwidd+M`은 공인 IP와 RunPod 프록시에서 같았다. 앱이 꺼진 상태에서 앱 설정의
  `pod_id`·`ssh_fingerprint`·`ssh_key_file`만 바꿨다(다른 항목 동일, 이전 설정은 백업). 설치된 앱의 엔진으로 `runtime_check`(앱 저장소)를 실행해
  SSH 접속과 GPU 확인(A40, 8.6, 46,068 MiB, 드라이버 595.91.07, 점검 항목 없음)을 확인했다. `runtime_state`는 `missing`이다. 설치된 빌드의 소스 해시가
  인수 시험 빌드와 달라 첫 학습 실행이 승인 창 안에서 환경을 준비한다.

- 사용자 요청으로 Pod `amdva0lurw9rv1`을 멈췄다(EXITED). 공장 소유가 아니라 `RunPodController.stop`의 안전장치 대신 RunPod REST stop을 직접 불렀다.
  다시 켜면 공인 IP·포트와 SSH 서버 지문이 바뀔 수 있어 연결 설정의 지문을 다시 확인해야 한다. 볼륨 `ls9ua5ymse` 삭제는 사용자가 승인했지만
  Claude Code 권한 검사가 되돌릴 수 없는 삭제로 막아 남아 있다.

**남은 일:**
- 설치된 0.5.0 앱 화면으로 처음부터 하는 시험(연결 검사, 환경 확인, 계획, 학습 시작의 설치 안내 승인, 결과 확인). 배포 안내 전에 한다.
- 볼륨 `ls9ua5ymse`(EU-RO-1, 80 GB, 사용하는 Pod 없음) 삭제: RunPod 콘솔에서 사용자가 직접 지운다.
- 앱의 이전 대기 작업 `76986bce`는 삭제된 Pod를 대상으로 한다. 사용자가 앱에서 취소한다.
- 볼륨의 런타임 정리: 앱 소스가 바뀔 때마다 새 런타임(5–12 GB)이 생기고 지우지 않는다(`ls9ua5ymse`에 4벌). 사용자 볼륨이 차는 원인이다.
- 긴 probe 동안 SSH keepalive, GPU 서버(SSH) 연결의 요금은 점검이 판단하지 않는 점, 2단계(Qwen3.5·Gemma 4).

# GPU 호환성 점검과 CUDA 12.8 학습 환경 (1단계) — 소프트웨어 완료, GPU 인수 완료(위 Task 16)

[설계](superpowers/specs/2026-10-29-gpu-compat-runtime-design.md) · [계획](superpowers/plans/2026-10-29-gpu-compat-runtime.md) ·
브랜치 `claude/gpu-compat-runtime` (작업 시작 기준 `96aea2e`). Task 1–13은 구현·검토를 마쳤고 Task 14(문서·전체 검증)를 2026-10-29에 실행했다.

- 고정 파일: 두 GPU 고정 파일이 torch·torchaudio·torchvision `+cu128`, NVIDIA 12.8 계열로 바뀌었다
  (`uv pip compile ... --torch-backend cu128`을 기존 파일 위에 실행, PPO는 `--override containers/gpu-ppo-overrides.txt`,
  pydantic==2.10.6). 재생성이 `pyproject.toml`이 요구하지만 옛 고정 파일에 없던 의존성도 추가했다: 두 파일 모두
  boto3 1.43.114, botocore 1.43.121, jmespath 1.1.0, s3transfer 0.19.3; PPO 파일은 추가로 bcrypt 5.0.0,
  cryptography 50.0.2, invoke 3.0.3, paramiko 5.0.0, pynacl 1.6.2. 그 밖의 버전은 바뀌지 않았다.
- `scripts/inspect_gpu_wheels.py`(2026-10-29, 종료 코드 0): 컴파일된 구조 `sm_75 sm_80 sm_86 sm_90 sm_100 sm_120 compute_120`,
  기타 후보 없음, sass `["7.5","8.0","8.6","9.0","10.0","12.0"]`, ptx `["12.0"]`, bitsandbytes CUDA 12.8 라이브러리 있음,
  torch·bitsandbytes 해시가 고정 파일과 일치.
- 런타임 검사 상태: ready / missing / mismatch / incompatible(GPU 세대 미지원) / unavailable(GPU 없음·개수 불일치·사용 중·오류,
  준비를 제안하지 않음). 준비는 RunPod에서만 실행 시 단일 승인 안에서 하고, GPU 서버는 연결 설정의 검토된 조회 흐름으로만
  준비한다. `factory server-runtime`은 `ready`가 아니면 JSON을 출력하고 종료 코드 1을 돌려준다.
- 런타임 검사는 결과를 `TF_PROBE <json>` 한 줄로 출력하고 `devices`를 보고한다. 실행 중에는 Pod를 다른 작업이 쓰는 경우와 준비 직후 확인이 실패한 경우에도 한국어 문구로 거부한다(동시 준비 거부와 별도).
- 호환성 판정은 계획의 코드에 더해 `POD_FACTS_UNKNOWN`(RunPod API가 GPU 종류·개수를 주지 않음)과 `PRICE_UNKNOWN`
  (유효한 현재 요금 없음)을 차단한다. 요금 비교에 허용 오차는 없다(`execution.validate_budget`과 같음).
  결과는 `{level, code, message}`이며 조언은 `message`에 있다.
- 화면: 계획 카드에 "실행 시 거부될 항목"/"주의 항목"; "학습 시작"/"대기 작업 실행"은 읽기 전용 점검을 먼저 하고, 거부 항목·
  incompatible·unavailable이면 멈추며, RunPod 환경 설치 안내를 한 번의 승인 창에 합치고, 점검 중 중복 클릭을 무시하며,
  그 사이 계획·선택 작업이 바뀌면 멈추고, 승인받은 작업만 실행한다. 데스크톱 앱 버전 0.5.0(Python 패키지는 0.1.0).
- 검증(2026-10-29, 문서 변경 후): `uv run pytest` 2021 passed, 1 skipped(616.82초); `uv run ruff check src tests scripts` 통과;
  `desktop`의 `npm run build` 통과, `npm test` 280 passed; `cargo test --manifest-path src-tauri/Cargo.toml --locked` 54 passed.
  실패·재실행 없음.
- GPU·Pod·데이터 전송·MLflow 쓰기는 사용하지 않았다. GPU 인수 대기.
- Task 15 결과(다시 빌드): 설치 프로그램 `desktop/src-tauri/target/release/bundle/nsis/LLM Tuning Factory_0.5.0_x64-setup.exe`,
  48,285,418 바이트, SHA-256 `9007ef4b500e00004df587c3492674b8913bc18da789f29860ca1f6024b899ed`, 커밋 `0163b8a`에서 빌드
  (`scripts/build_desktop.py` 후 `npm run tauri -- build --ci`; 빌드 뒤 추적 파일 변경 없음).
  `scripts/verify_desktop_package.py` 영수증 `dist/verify-0.5.0-final.json`: bridge_mode frozen,
  source_commit `0163b8af01572ad8e1ae6fef5bbae2a7f0b5abdf`, 모든 검사 true, gpu_started false.
  설치하지 않았다. 설치는 사용자 확인이 필요하며, 설치 프로그램이 이전 설치 폴더를 기억하므로 NSIS `/D=<폴더>`로 실제 설치 폴더를 지정해야 한다.
- 최종 검토 결과와 수정: 다른 앱 버전(소스 해시가 다름)의 작업은 원격 접속·환경 설치 전에 한국어로 거부한다(`runtime_check`와 실행 모두).
  준비 직후 확인 실패는 고정 문구로 거부한다. 검증된 모델도 검증되지 않은 GPU 세대에서는 `MODEL_UNVERIFIED`로 경고한다.
  메모리 문구에 95%·90% 기준을 밝혔다. `inspect_gpu_wheels.py`는 호환표와 어긋나면 종료 코드 1을 돌려준다. 환경 확인은 작업 자체의
  학습 단계를 쓴다. 화면은 점검·승인 뒤 대기 작업의 연결 대상을 다시 확인하고 환경 준비 중 안내를 보여 준다.
- 마지막 수정(`0163b8a`): 실행 전 점검 중이나 승인 창 뒤에 저장된 RunPod Pod ID가 바뀌면 "연결된 Pod가 바뀌었습니다. 실행 전 확인을 다시 진행하세요."로 멈추고,
  `resume`은 작업 매니페스트의 소스 해시를 먼저 확인해 다른 앱 버전의 작업을 QUEUED로 되돌리기 전에 거부한다. 두 수정 모두 테스트를 먼저 작성해 실패를 확인했다.
- 최종 코드 검증(2026-10-29, `0163b8a`): `uv run pytest` 2030 passed, 1 skipped(497.26초); `uv run ruff check src tests scripts` 통과;
  `desktop`의 `npm run build` 통과, `npm test` 282 passed. `cargo test` 54 passed는 Task 14에서 마지막으로 실행했고 그 뒤 Rust 변경은 없다.
- 사용자의 대기 작업 `76986bce…`는 0.4.0으로 제출되어 0.5.0에서 실행할 수 없다. '취소 요청'으로 정리하고 다시 제출한다
  (GPU 종류·요금은 'GPU 정보 → 학습 설정에 적용'으로 맞춘다).
- **남은 일(후속):** SSH 서버의 시간당 요금은 호환성 판정이 아직 보지 않는다(기존 서버 예산 검사만 적용). 판정 표에 넣을 것.
- **남은 일(유료, 별도 승인):** Task 16 GPU 인수 시험. Blackwell Pod와 Ampere 또는 Hopper Pod에서 새 환경 준비,
  `arch_ok`·`kernel_ok`, Qwen2.5-0.5B SFT LoRA 회귀, 메모리 추정과 실제 최대 사용량 비교.
- **2단계 남음:** Qwen3.5·Gemma 4 텍스트 학습 GPU 검증과 수정, 허브 모델 메모리 추정.

# General-purpose workflows — Windows 0.4.0 accepted on one PC, 2026-10-22

User approved the [design](superpowers/specs/2026-10-17-general-training-workflows-design.md) and
[implementation plan](superpowers/plans/2026-10-17-general-training-workflows.md), including task implementers
and independent reviews. Tasks 1–11 are implemented and reviewed on codex/general-training-workflows. The Windows
0.4.0 installer, built from `b4e359a`, passed the installed-app acceptance on the development PC; its receipt is
[`acceptance/2026-10-22-desktop-040.json`](acceptance/2026-10-22-desktop-040.json), and the "Task 11.6" entry below lists
what it did not verify. Real GPU, Hugging Face and MLflow checks (L1–L3) are still pending and each needs the user's
authorization.

Reviewed foundations (Tasks 1–4): blank projects and immutable reusable recipes; mapped CSV/JSON/JSONL import
with original/canonical provenance and input-OR-group component splits; immutable effective-model artifacts;
common benchmark fingerprints, text-answer agreement metrics, explicit objective/threshold selection and merge
regression fallback. Review fixes cover complete adapter-loader metadata, operation-local ancestor verification,
historical descriptor hashes, dataset parser/fingerprint agreement, derived-output format drift, and explicit
partial-success permission separated from normal quality filtering. All four task review gates passed.

Task 5 implementation adds typed operation/graph schemas, immutable destination/resource snapshots,
conditional model handoff checks, explicit whole-plan cost approval, and ready-time compilation. A separate
path-free graph config preserves legacy SFT/PPO manifest behavior. Initial independent review found two
handoff errors; fixes now allow full-weight parents to initialize new adapters and bind selected winners
from all approved model roots. Both findings passed scoped re-review; Task 5 is complete.

Task 6 implementation adds atomic graph/job storage, replayable evidence, fenced attempt ownership,
stable launch identities, controller leases, conservative legacy-lock migration and typed output/tracking
ledger hooks. Initial review found a retired-lease queue mutation and inconsistent desktop resource keys.
Both fixes passed 60 scoped regression tests, amended full-suite and scoped re-review. Task 6 is complete.
v2 worker dispatch and the persistent controller are later tasks.

Actual frozen-source verification after the Task 6 review fixes: Python 3.12.12 locked development/tracking sync;
**538 passed, 1 skipped** in 240.51s; `uv run ruff check src tests` passes. The skip is the existing opt-in
real GPU test. Legacy SFT/PPO canonical bytes match the base resolver under the same current true provenance.
Separate CPU dependency/export preparation passed; it does not yet qualify the unimplemented public
model-operation numerical path. No GPU, institution data, model weights or external dataset transfer was used.

User scope update on 2026-10-19 adds [general Linux/NVIDIA SSH servers](superpowers/specs/2026-10-19-ssh-gpu-servers-extension.md),
including BOP-style servers with direct SSH execution. Tasks 6A/6B will add provider-neutral adapters and Windows
connection settings. Task 6A core adds typed SSH observations/receipts, exact UUID selection, operator-declared
zero cost, alias-aware local resource exclusion and durable runtime-preparation reservations.

Task 6A independent review found Important and Minor issues; all assigned fixes were made test-first. A queued job
bound to another destination no longer blocks workers: the SSH worker selects only unbound jobs or jobs whose
receipt matches the current destination, the RunPod worker ignores SSH-bound jobs, and an untargeted server claim
skips unbound or mismatched rows. Collect, reconcile, live logs and progress of an existing attempt now use an
explicit recovery path: same machine-id server ID, exact transport (host, port, user, pin, remote root, selected
UUIDs, key reference) and the original attempt record, while GPU inventory drift, including a missing selected GPU,
is tolerated; operator cost edits do not block recovery. The recovery executor carries the original approval facts
and can never claim or launch. Launch and preparation keep exact inventory, selection, count and type checks.
Paramiko-shaped fakes now cover the SSH `executor_for` branch, `plan_server_destination`, `validate_server_receipt`,
CLI `plan --real`, `run --submit --real` and `reconcile` with `--server-profile`, GPU type mismatches and
`PinnedHostKey` pinning; these tests exposed no further production bug. A refused pre-launch check now fails the
unlaunched attempt and frees its lock; started or taken-over attempts keep theirs. Minor fixes: LDAP-style
usernames are accepted (no leading `-`, whitespace or control characters); bootstrap pip uses `--no-cache-dir`
after a fixed `python3 -m pip --version` check that reports a missing pip before transfer; an unreadable
/etc/machine-id reports an unsupported server instead of a traceback; `doctor` rejects `--pod-id` with a server
profile. The scoped re-review of these fixes passed.

Native settings contract for Task 6B: `provider: "ssh_server"` with string values under `server_`-prefixed keys
(`server_ssh_host`, `server_ssh_port`, `server_ssh_username`, `server_ssh_fingerprint`, `server_ssh_key_file`,
`server_remote_root`, `server_selected_gpu_uuids` as a JSON-encoded array, `server_total_hourly_cost_usd`), so the
inactive Pod's `ssh_key_file`/`ssh_fingerprint` stay separate. An explicit operator hourly cost is required; 0 is
allowed. Server identity is the SHA-256 of the lowercased /etc/machine-id; lock aliasing also treats any observed
GPU UUID overlap as the same server, and an SSH lock without an observation blocks. The cross-provider lock policy
is deliberate: the cross-provider quarantine in `JobStore._resource_blocked` treats a RunPod Pod and an SSH server as
possibly the same hardware. A held RunPod job lock blocks SSH claims and SSH preparation; an SSH job lock or SSH preparation
reservation blocks RunPod claims and RunPod preparation. RunPod preparation records no reservation, so it does not block
SSH. This is separate from the v1 concurrency default (`Policy.max_concurrent=1`). Distinct-resource concurrency is Task 8.

Deferred: the key identity is a hash of the resolved local key-file path, so moving the key breaks recovery of old
jobs and replacing it at the same path goes undetected (consider the public-key fingerprint later); the server
worker binds an unbound real job to its server before claiming, approved by `--approve-execution-and-transfer`;
graph ResourceFacts still allows a 0 RunPod price (Task 8). `JobStore.resource_blocked`, unused in 6A, is used by 6B-1.

Actual verification after the Task 6A review fixes: Python 3.12.12 locked development/tracking sync;
**652 passed, 1 skipped** in 49.56s (55 new cases; 114 in tests/test_gpu_servers.py); `uv run ruff check src tests`
passes. The skip is the existing opt-in real GPU test. Unverified: no real SSH/BOP server and no GPU were used;
SSH connections, probes and SFTP are software fixtures only.

Task 6B-1 (Python desktop backend) routes sidecar actions by `provider`. For `ssh_server`: `connection` inspects
the server instead of a Pod; new `server_inspect`/`server_facts` return the read-only inventory and selected-GPU
facts; `runtime_prepare` requires the approved inspection and refuses only when that server (machine ID or a shared
GPU UUID), another provider or an unknown lock is held (`JobStore.resource_blocked`, now used); new `runtime_reservations` and
`runtime_release` (approval plus confirmed stop) handle preparation reservations; `plan` stays offline and adds
`destination_sha256`/`destination_issue`; `submit` refuses a changed hash, runs the offline export/policy refusals,
then freezes a receipt from one read-only observation (zero operator cost allowed); `execute` and `reconcile`
(recovery executor) use the job receipt. RunPod `execute` and `reconcile` refuse server-bound jobs before any Pod
contact, `pod` is RunPod-only and `jobs` add a key-free `server_destination` from one bulk read (`{"invalid": true}`
for an unreadable receipt, which execute/reconcile refuse with a fixed message). A missing local SSH key is refused
before connecting. The sidecar forwards the fixed ExecutionUncertain and TimeoutError messages; settings values are
redacted in raw and repr (escaped Windows path) form. MLflow:
`auth_mode: "none"` drops OIDC/token values (HTTP then allowed), a missing OIDC client ID/secret is a clear Korean
ValueError, and the connection report adds `mlflow_read`/`mlflow_auth`. Task 6A follow-ups: `executor_for(recovery=True)`
is SSH-only, a refused pre-launch check also writes error.txt, and an unreachable observation branch was removed.
Actual verification at that point: **711 passed, 1 skipped** in 64.32s (59 new cases); Ruff passes. The Rust native
settings, action allowlist and TypeScript UI followed in 6B-2 and the docs in 6B-3 (below). No real SSH server, GPU or
MLflow server was used.

Task 6B-2 (Windows native settings and connection screen): native settings save `provider` and the eight public
`server_*` strings, refuse an unknown provider or `auth_mode` and a selection that is not a JSON array of at most 8
strings or a key-file field containing pasted `PRIVATE KEY` text, and allow `server_inspect`/`server_facts`/
`runtime_reservations`/`runtime_release`. A pure MLflow filter withholds the OIDC URL, client ID and secret (and, for
`none`, the token) from the sidecar only; the public form view keeps every field and the vault keeps every credential,
so a `none` → `oidc` round trip reuses the saved address, client ID and secret.
The connection screen adds a provider card, GPU server fields (no RunPod key or Pod ID), GPU inventory selection
saved as a JSON string, server facts applied with a per-GPU price that never multiplies back below the server total
(a one-ulp upward correction where float division undercuts it, about 3% of cent-valued cases), inspect → approve →
`runtime_prepare` with the unmodified inspection, a reservation list whose uncertain entries are released only after
an approval stating the remote preparation stopped (refreshed automatically after a prepare error), MLflow
“인증 없음” with a trusted-network note, auth-method connection results and an unverified-training row, provider-specific
overview, plan and approval texts, a disabled start for a null destination hash, `destination_sha256` on submit and
a job destination column. Saving a changed provider or server field clears the plan, inspection, reservations and
reports; a selection-only change clears the plan but keeps the observed inventory, and an unchanged save says so.
The three `data-tour` anchors render once for both providers, and no remote action runs on page load, navigation or
provider/auth switching. The MLflow URI label is now “MLflow 서버 주소” so field names stay unique.
Review fixes: the contract lives in `desktop/src/server-connection.ts` (settings helpers, typed bridge calls, approval
texts, `resourcesFromFacts` and escaped renderers; `main.ts` keeps the state glue). Execute and resume refuse, with the
sidecar's fixed messages and before any dialog, a job whose destination differs from the saved connection (resume
would otherwise reset the job first), and their approval names the job's own server. Preparation refuses unsaved
GPU checkboxes and saved GPUs missing from the fresh inventory before approval; only reservations made with the saved
server settings can be released; the connection check waits for an unsaved provider switch to be saved; invalid
price inputs throw; connection inputs are locked while a task runs so its final re-render cannot drop typing.
Actual verification: Playwright **45 passed** (22 in `desktop/tests/connections.spec.ts`; `workspace.spec.ts`
unchanged), `npm run build` (tsc + Vite) passes, Rust **10 passed** (6 new), Python **711 passed, 1 skipped**, Ruff
passes. The UI is tested against a mocked bridge, so the real Tauri/WebView2 shell and Credential Manager stay unverified.

Task 6B-3 (user documentation) updates README.md, docs/WINDOWS_APP.md, docs/CONFIGURATION.md, docs/OPERATIONS.md and
.env.example for direct-SSH GPU servers (desktop steps, server prerequisites, profile JSON, CLI flow, reservation
release, limits) and the unauthenticated MLflow mode (HTTP only without credentials, local `mlflow server` example run from a
full `mlflow` package, artifact-proxy note, trusted-network warning). **Task 6B is complete in software.**

Commits of the SSH GPU server and MLflow work, in order:
- Task 6A (adapters): 5f7baff (SSH server support), 2a11d4a (destination-bound jobs do not block other workers), 124b9f2
  (collect after unrelated inventory drift), 2328474 (executor/receipt/CLI tests), f5335d2 (release an unlaunched attempt),
  cdf717d (prerequisite/identity/input fixes), 85808a7 (6A review record), 06ceb65 (plan scope for local MLflow).
- Task 6B-1: 6f3c70f (SSH-only recovery executors, refused pre-launch errors), 570498d (desktop actions routed to SSH servers,
  unauthenticated MLflow).
- Task 6B-2: 16235c6 (refuse misrouted server jobs early), 15b118d (robust job listing and preparation), c0ee9a9 (SSH
  server connection screen), 1d40ecd (keep MLflow settings, isolate the connection module), 66d23c0 (shared button actions,
  port comparison, pasted key detection).
- Task 6B-3 docs: 5887114 (guide), 17031ff (HTTPS statement), and the correction commit `docs: correct SSH server and MLflow
  guidance against the current code`.
- Test hygiene: 0f40418 (Rust fake key headers built at runtime).
- Follow-ups since: 78bc0f5 (protected synthetic sample for the desktop tutorial; the tutorial is a later task), 338c7bb
  (`claim()` refuses non-fact observations, receipt lookups are chunked), ace7712 (tutorial sample trailing-dot alias and race),
  dd930e3 (MLflow credentials refused over plain HTTP in every client path), fb76b52 (release stuck preparations, one GPU per
  job, `.ppk` keys refused), 1e0f305 (credentials kept off http artifact roots, fail fast before claiming), a0714ce (count only
  policy-compliant GPU selections as configured), 3463af7 (OIDC counts as configured only with a secret; run artifact roots checked), 3b4e6ae (over-limit plans cannot start,
  `.ppk` trailing-dot check, clearer PREPARING warning), 861d39e (tutorial native allowlist and package asset), 0a0644f (tests pin
  the code revision so concurrent commits cannot break manifests; tighter `run --mock` refusal test), 879e855 (finish the SSH
  server and MLflow doc corrections), 44e0f27 (flush deferred renders before reading form drafts), 7aa6830 (explain cross-provider
  locks; check destinations before resume; refuse the practice dataset at submit; test env isolation; provider-neutral OIDC
  message and fail-fast), 2ba206e (drop a stale tutorial plan on exit after destination changes; cost-aware reservation matching
  and Korean refusal; waiting notice mentions other-provider locks) cf9b86e (the backend returns the fixed Korean
  reservation-release refusal "다른 서버 설정(비용·키 파일 포함)으로 만든 예약입니다. 그 설정으로 바꾼 뒤 해제하세요." for cost and key
  mismatches; `CROSS_PROVIDER_LOCK` now says "다른 연결 대상(RunPod 등)…"), 745e9d6 (the UI test uses the backend's Korean refusal;
  waiting-notice wording aligned), 05a8bb2 (docs: record final verification, upgrade notes and lock remedy) and the docs commit
  `docs: record the final review and follow-ups`.

Final independent whole-branch review (06ceb65..2ba206e, plus cf9b86e and 745e9d6 as minor follow-ups) returned "Ready to merge: Yes"
after fixes.

Remaining follow-ups / deferred: in the reverse direction (an SSH lock or reservation blocking RunPod), RunPod preparation still says
"Pod is reserved by a job" in English and RunPod execute shows only the waiting notice; `CROSS_PROVIDER_LOCK` in server execute can be
misattributed when `work` returns None for another reason while a foreign lock exists; the `main.ts` render deferral and the
per-action provider forks in `desktop.py` should be consolidated in Task 8/9/10.

Upgrade / user-facing changes:
- 0.3.0 settings without `provider` are read as RunPod; the first save writes `existing_runpod`. Without `auth_mode` the mode is `oidc`.
- New refusals when MLflow credentials are present: an http artifact location, an existing experiment's or a run's http artifact
  root, and CLI `work`/`run --mock` with a non-https tracking URI, including `file:`/`sqlite:`.
- An OIDC URL without a secret: the app starts and MLflow actions show "OIDC 인증을 선택했지만 클라이언트 인증값이 없습니다. 인증값을
  입력하거나 토큰·OIDC 값을 비우세요(앱에서는 ‘인증 없음’ 선택)."; the CLI checks before claiming, the desktop before submit and execute.
- Cross-provider quarantine pitfall: a leftover RunPod job lock from 0.3.0 blocks all GPU server work and preparation (the desktop shows
  the "다른 연결 대상(RunPod 등)의 끝나지 않은 작업 잠금…" message). Remedy: 재연결 · 결과 회수 first; if the Pod and process are confirmed
  stopped, `uv run factory --state-dir "$env:APPDATA\kr.kbiofoundry.tuningfactory\state" release-lock JOB_ID ATTEMPT_ID
  --confirm-execution-stopped` (JOB_ID is in the jobs table; ATTEMPT_ID is `active_attempt_id` in `status JOB_ID`).
- The SSH username pattern was widened (institution/LDAP names); `.ppk` and pasted private keys are refused.
- Desktop `submit` refuses the practice dataset ("연습용 데이터(tutorial_sample)로는 실제 학습을 시작할 수 없습니다."); resume checks the
  job's destination before resetting it; legacy jobs show "RunPod" in 실행 위치.

MLflow rule as of 3463af7 (treat OIDC as configured only with a secret and check run artifact roots): if `MLFLOW_TRACKING_TOKEN`
is set, or both `MLFLOW_OIDC_TOKEN_URL` and `MLFLOW_OIDC_CLIENT_SECRET` are set, the tracking URI must be https (CLI and desktop;
`work` and `run --mock` check before claiming), an http artifact location (including a run's server-assigned one) is refused, and even local `file:`/
`sqlite:` URIs are refused with credentials set. No-auth http is allowed.

Latest actual verification (2026-10-19, Windows): Python at the code state of 3463af7 and later, `uv run pytest -q` **743 passed, 1 skipped**
in 81.22s and `uv run ruff check src tests` passes. Desktop numbers are the reviewer's clean-tree results from a `git archive`
export of 0fa046c and of 0a0644f: Playwright **53 passed** (connections 30 + workspace 23), `npm run build` (tsc + Vite) passes and
`cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release` **12 passed**.
Secret scan history (resolved 2026-10-20): Rust test stubs in two commits had contained fake private-key header text (not key
material), and `scripts/check_secrets.py` walks all reachable history, so the first push of this branch failed CI. With the user's
approval the 32 commits after c0ee9a9 were rewritten with `git filter-branch --index-filter`, changing only those header strings in
`desktop/src-tauri/src/main.rs` (trailing `-----` → `----`); the final tree is byte-identical to the previous HEAD. The branch was
force-pushed with lease, and `check_secrets.py` now reports **0 findings**. Commit SHAs in this file were updated to the rewritten
history; a backup bundle of the old history was kept outside the repository.

Still unverified: a real SSH/BOP server, real NVIDIA GPU execution, a real MLflow server (authenticated or not), the real
Tauri/WebView2 shell and Windows Credential Manager, and installer acceptance (Task 11). All SSH behavior was tested with
fake SSH responses.

Deferred hardening: some sidecar refusals are still in English; the key identity is derived from a
hash of the local key path (moving the key breaks recovery, replacing it in place is undetected). (`claim()` now rejects
non-fact observations and receipt lookups are chunked, so those two earlier items are done.)

First-run tutorial (2026-10-19): an 11-step hands-on tour with non-blocking popovers and a highlight ring, started from the overview
card "튜토리얼 시작", the sidebar "튜토리얼" button or Ctrl+K "튜토리얼" (pressing it during a tour restarts it). It fills,
previews and registers the bundled synthetic `tutorial_sample` (8 rows), selects it in the training settings and runs the
dry-run plan; the connection steps only explain RunPod and GPU servers. Esc or "그만하기" ends it (deferred while a request is in
flight) and restores the pre-tour training draft, plan, dataset draft and preview; completion or dismissal is remembered in local
storage. The sample stays registered, marked synthetic with GPU transfer blocked; the ID `tutorial_sample` is reserved (case and
trailing-dot variants and S3 import are refused) and it is excluded from the "시작하기" data checklist. A clicked button is no
longer lost when a field edit re-renders the screen. Commits: 78bc0f5, 338c7bb, ace7712 (backend sample, reserved ID, claim and
receipt hardening), 861d39e (native allowlist and package asset), 9a7966d, 1328621, aa30399 (engine and UI, deferred exit,
on-screen popover, first-click fix), 9c38451, 209528c (checklist exclusion, ASCII path hint), 6684229 (package verifier checks the
tutorial refusal and registration exactly) and the docs commit `docs: guide first-time users through the desktop tutorial`.

Final actual verification at HEAD 745e9d6 (2026-10-19, Windows; the later docs commits change no code): `uv run pytest -q` **754 passed,
1 skipped** in 75.68s; `uv run ruff check src tests` passes; `npx playwright test` **81 passed**; `npm run build` (tsc + Vite)
passes; `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release` **12 passed**;
`git grep` for private-key headers finds nothing (exit 1). After the history rewrite, `uv run python scripts/check_secrets.py`
reports 0 findings (`Checked 617 staged/historical file versions; 0 findings.`). Earlier numbers (752/81 at 2ba206e, 743/72 at
6684229, 743 at 3463af7) are superseded.

Unverified for the tutorial: the real Tauri/WebView2 rendering and layout of the tour, the installed frozen package and
`verify_desktop_package.py` (Task 11), and any real GPU, server or MLflow. Known minor limits: a deferred render can fire mid-press
after a 1 s fallback; screen readers may not announce step 1 when the tour opens; the tour must be updated when Task 10
redesigns screens (the `data-tour` contract tests catch this).

Remaining Tasks 8–11 (Tasks 6A/6B and 7 are complete in software; see "Task 7 complete" below):
persistent multi-Pod controller/tracking repair, secure desktop API/broker, Windows UI/installer and Git sharing.
These foundations are not end-to-end workflow, Windows 0.4.0 or live GPU acceptance. Actual serial handoff,
multi-Pod concurrency and merge/reevaluation on GPU require separate concrete resource/data authorization.

Task 7.1 (2026-10-20, [sub-plan](superpowers/plans/2026-10-20-task7-model-operations.md) §3 7.1 and §4): hash-locked CPU
model-operation runtime and numerics. `containers/model-ops-cpu-requirements.in`/`.lock` (56 packages, all hashed;
`torch==2.7.1+cpu` from the PyTorch CPU index via `--torch-backend cpu`; peft 0.18.1, transformers 5.6.0, safetensors
0.8.0, tokenizers 0.22.2, accelerate 1.11.0, huggingface-hub 1.33.0, numpy 2.5.4, equal to the primary GPU lock; no
LLaMA-Factory, bitsandbytes, triton or `nvidia-*`). The venv is the git-ignored `.factory/model-ops-cpu-venv`
(`uv venv --python 3.12` + `uv pip sync --require-hashes --torch-backend cpu`; first sync verified). The old unlocked
`.factory/model-ops-cpu-env` was not used and is superseded. `runtime_profiles` gains `MODEL_OPS_PINS` and
`MODEL_OPS_CPU_LOCK`. New `model_ops.py` imports no torch at module level and validates before any heavy import:
merge inputs reuse `graph_planner._merge_descriptors`/`_materializable`/`_adapter_tensor_shapes` and `MergeNode`
weights (the real-only rule is relaxable only by `require_real=False`; graph compilation stays real-only), materialize
refuses an HF root for an artifact effective base and quantized or adapter bases, the local loader `adapter_config.json`
must equal the descriptor, `fan_in_fan_out` is refused, and a streamed stdlib F16/BF16/F32/F64 NaN/inf scan runs on
adapters. The numerics are a PEFT `cat` merge in FP32 and `merge_and_unload(safe_merge=True)`, both stored F32 only
(policy `model-ops-f32-v1`), with delta, reload-logits, non-target identity, base-byte tokenizer and finite checks;
merged `base_model_name_or_path` is the root repo ID and `README.md` is dropped. Merge saves with
`save_embedding_layers=False`, because PEFT's `auto` vocabulary probe queried the HF Hub for the provenance repo ID
(caught by the acceptance `no_hub_lookups` check). Also added: `inspect_target_modules` (meta device, LLaMA-Factory's
Linear rule), `verify_inspected_targets` and `adapter_metadata_from_config` (a real PEFT 0.18.1 export freezes).

7.1 review fixes (commit `fix: make model-operation outputs deterministic and tighten 7.1 checks`). Important: PEFT writes
`target_modules` from a set, so adapter and merged-adapter bytes, and therefore `inventory_sha256`, depended on
`PYTHONHASHSEED`. Seeds 1 and 3 gave `["v_proj","q_proj"]` and `["q_proj","v_proj"]`. `model_ops.write_adapter_config`
now writes sorted set-valued lists with LF line endings and sorted keys, for the merge output and the acceptance
fixtures. A new acceptance check, `deterministic_output`, merges in two fresh processes with seeds 1 and 3. It failed
before the fix and now gets identical adapter/merged identities and inventory hashes, while PEFT's raw orders still
differ. Minor fixes:
- Policy `model-ops-f32-v1` accepts only F16/BF16/F32 inputs. F64 (a lossy narrowing) and integer tensors are refused
  with `<merge|materialization> requires F16, BF16 or F32 tensors under policy model-ops-f32-v1`. Quantized bases are
  refused with `<merge|materialization> requires unquantized effective base weights`.
- `verify_inspected_targets` requires `ref.artifact_id == sha256(canonical(descriptor))`.
- The scanner bounds its second header read by `JSON_LIMIT`.
- With `model.safetensors.index.json`, only the indexed shards are read and they must match the index. Otherwise only
  `model.safetensors` is read, so a Mistral-style `consolidated.safetensors` copy is ignored.
- Each acceptance operation is a recorded check, and the receipt has top-level `passed` and `stopped_after`. An injected
  materialize failure gave exit 1 and a receipt with `materialize_operation` failed and `stopped_after` set.
- The tiny tokenizer vocabulary (still 32: 4 special, 23 `t<i>`, 5 probe words) now encodes the default probe without
  `<unk>`.
- Module split: `safetensors_scan.py` (stdlib finite scan) and `target_inspection.py` (inspector and verifier), both
  re-exported by `model_ops`. The pinned-runtime loader is `runtime_profiles.model_ops_runtime()`. The tokenizer-file
  rule is the single `model_artifacts.is_tokenizer_file`.

Actual verification after the review fixes: `uv run pytest tests/test_model_ops_contract.py -q` **91 passed** (initial
RED: 70 failed on the missing module; each review fix was RED first). `uv run pytest -q` **845 passed, 1 skipped**,
`uv run ruff check src tests` passes, `scripts/check_secrets.py` reports 0 findings, `git diff --exit-code` on both GPU
locks exits 0 and the main `.venv` lists no torch/peft/transformers/safetensors. CPU acceptance
`.factory\model-ops-cpu-venv\Scripts\python.exe -I scripts\verify_model_ops_cpu.py --receipt .factory\model-ops-cpu-receipt.json`
exits 0 with all 25 checks passed on the locally generated tiny Qwen2 (2 layers, hidden 16, 27 tensors) and WordLevel
tokenizer. `cat` merge r2/α4 + r3/α9 at 0.25/0.75 gives r = α = 5. Max delta errors: merge 7.78e-9, materialize 9.17e-9,
parent→child 6.42e-9 (atol 1e-6). Max logits errors: merge 3.35e-8, materialize 2.98e-8 (atol 1e-5). F2 differs from
root + ΔC by 0.187 (> 1e-3). The frozen reference keeps all 27 tensor SHA256s while the policy LoRA updates. NaN-adapter
and inf-base inputs are refused before output, the stdlib and torch scans agree on 10 files, 14 target modules are exact
and 4 tampered evidences are refused, no Hub lookup occurs, and outputs are identical under hash seeds 1 and 3. Receipt
SHA256 `e07c52969951d8efd9b8bbde58247752118aada07805c8b0fdb3c19aa1255774`; source_sha256
`94e9f99f3cc63d7600d3b8f6969a1737ac7be9bd7acbeb82119678478227d956`; lock_sha256
`5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`; `passed` is true and `model_downloaded`, `gpu_used`,
`institution_data_used` and `production_promotable` are false. Receipts are now reproducible across hash seeds: four runs
gave byte-identical receipts. Two used `-I`, where Python picks a random hash seed, and two used `-s -P` with
`PYTHONHASHSEED` 1 and 3. The superseded pre-fix receipt `4508012d…8f16` was not reproducible. The CPU lock is Windows-only.
Transformers writes `config.json`/`generation_config.json` with platform newlines, so materialized bytes from Windows and
Linux may differ. (That receipt is for the 7.1 source; the 7.2 re-run below supersedes it.)

Task 7.2 (2026-10-20, sub-plan §3 7.2): path-free model-input staging and training-plan binding. New
`operation_runner.py` (staging/binding section; no graph_store/jobs import, heavy libraries lazy):
- `stage_model_inputs` validates train inputs with `ModelArtifactStore.validate_input` first, verifies every role of a
  train/evaluate/merge/materialize operation in the approved cache, and writes only canonical `model-inputs.json`
  (`ResolvedModelInputs`: roles, identities, sorted transfers, reference quantization, reward type). It never writes
  weights and refuses an attempt directory that holds any.
- `verify_cached_artifact` checks a cache entry's descriptor hash, inventory and payload without lineage.
  `resolve_hf_root` downloads an exact root with `snapshot_download(local_dir=<HF_HOME>/factory-roots/<owner>/<name>/<rev>)`
  and loader-only `allow_patterns` (real files, never the symlinked hub cache). It then requires the whole tree to be
  link-free (`.cache/huggingface/` is walked, never fingerprinted), the config hash and tokenizer fingerprint of that
  same tree to equal the root, and an unambiguous safetensors layout.
- `verify_model_paths` re-derives the roles from the operation and the verified cache descriptors, refuses any drift from
  `model-inputs.json`, and repeats every rule. It reads QLoRA base `config.json` and checks continuation against the
  worker's own inspected targets; artifact-base evidence must pass `verify_inspected_targets`.
- `bind_training_plan` substitutes only role sentinels at `training.ROLE_SLOTS` keys with approved, link-free cache
  paths. It refuses misplaced or leftover sentinels, literal model locations, unapproved paths, `resume_from_checkpoint`
  and inconsistent `create_new_adapter`/`reward_model_type`/`ref_model_quantization_bit`. `write_bound_plan` writes
  deterministic `train.bound.yaml`, `model-binding.json` and `binding-evidence.json`.
- `LlamaFactoryBackend.translate(config, model_input)` returns the logical template. Legacy `translate(config)` is the
  moved `_legacy_plan` (pure move), `validate_config` validates `GraphTrainingConfig` with its own class, and `prepare`
  accepts `model_input`.

Rules (enforced at staging and again on the worker unless noted):
- Continuation compares every effective setting except `inference_mode`/`task_type`, including rank, effective alpha
  (None → 2·rank), dropout, LF-expanded targets, rsLoRA/DoRA/PiSSA, method, quantization bit and LoRA+. It names the
  differing fields, starts a fresh optimizer (`create_new_adapter: false`, no resume) and refuses PPO.
- DPO (non-ORPO/SimPO) and KTO bind `ref_model`(+`ref_model_adapters`) to exactly the policy parent; reference-free
  losses bind none. QLoRA references are quantized like the policy (Q5).
- An SFT adapter cannot continue into DPO/KTO. This needs ledger producer evidence, so it runs at compilation and staging
  only; the worker cannot re-derive a parent's stage.
- A PPO reward comes only from the typed binding, and `reward_model_type` is derived from it. A LoRA reward must sit on
  the policy base, and the reward artifact needs `value_head.safetensors`.
- Every base tokenizer equals the policy's (`<role> tokenizer differs from policy`), and QLoRA needs an unquantized full
  base.

Carry-forward fixes:
- F10: `_training_parent` uses `model_ops.continuation_mismatches`; a realistic PEFT 0.18.1 export now compiles.
- M3: graph_planner exposes `check_materializable`, `adapter_tensor_shapes` and
  `check_merge_descriptors(require_real=)`. model_ops uses them, with no private imports and no descriptor rewrite.
  `_merge_descriptors` stays real-only.
- M2: planning refuses `fan_in_fan_out` like the worker.
- Shard layout: model_ops refuses a full model with both `model.safetensors` and the index, or with neither (the
  all-`*.safetensors` fallback is adapter-only), and refuses `config.json` `transformers_weights`.

Deviations:
- Plan F18's 30-row matrix construction was not recorded and could not be reproduced. The test pins digest
  `d488f35a…3473` of an explicit matrix computed at HEAD `42064b7` from the unmodified `translate`. The F18
  example-config digests match exactly, as does the v1 manifest `training_config_sha256`.
- `stage_model_inputs` gains a `producers=` keyword for the ledger stage rule. Staging also verifies artifact-base
  target evidence.

Actual verification:
- RED: 60 failed on the missing module and helpers, including F10's `parent adapter settings differ` on a real export.
- GREEN: `uv run pytest tests/test_model_inputs.py` **55 passed**. The sub-plan set (`test_model_inputs`, `test_training`,
  `test_graph_planner`, `test_operation_schema`, `test_model_artifacts`, `test_config_registry`, plus
  `test_model_ops_contract`) gave **286 passed**.
- `uv run pytest -q` gave **906 passed, 1 skipped**. Ruff is clean, `check_secrets.py` reports 0 findings, both GPU locks
  are unchanged, and the main `.venv` has no torch/peft/transformers/safetensors/huggingface_hub.
- The CPU acceptance re-run (`-I`) exited 0 with all 25 checks passed and unchanged max errors (merge delta 7.78e-9,
  materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8). On the reviewer's clean LF tree at
  `7f74762` the receipt SHA256 was `2eba5b1049435928447855af5b0960b35eb7189ee9045973d9302c15e4163d4e` and the
  source_sha256 `b26e7b2b4bc0a0cb9e3111c8520aa140af83a9dd4556deb757f5610eaaf3d3a2`, with lock_sha256 unchanged. The
  values first recorded here (`6a62e788…`/`632d468b…`) came from a working tree with CRLF files and are void. The
  review fixes below supersede both.

7.2 review fixes (commits `fix: refuse PPO continuation at planning and compile` and
`fix: refuse PPO continuation at planning and verify each artifact once`):
- PPO continuation is refused at planning and compile too, reusing `training.PPO_CONTINUATION`:
  - `validate_graph`/`plan_graph` refuse a PPO child of a LoRA/QLoRA parent (`parent_transition`), the same handoff
    through a selection (`_static_routes`), and an explicit `continue_adapter` ModelInput with a PPO recipe
    (`train_contract`).
  - `compile_node` refuses PPO `continue_adapter`, for plans pinned before the rule.
- One verifier per call:
  - `model_artifacts` gains `verify_artifact_directory` (one entry, no lineage) and `ArtifactVerifier` (one
    completed-cache shared by `verify`, `path`, `validate_input` and lineage; `lineage=False` for the worker cache).
    `ModelArtifactStore.verifier()` exposes it, and the store's public reads delegate to it.
  - operation_runner's near-copies (`verify_cached_artifact` body, `_check_load`) and all private `model_artifacts`
    imports are gone; `verify_cached_artifact` is a one-line wrapper.
  - A DPO continuation on an artifact base (policy, frozen reference and adapter lineage all naming the base) now hashes
    each distinct artifact exactly once at staging and once on the worker (test counts `_inventory` calls).
- Binding and paths:
  - `VerifiedModelPaths` refuses duplicate (role, index) and no longer carries roots.
  - `bind_training_plan(..., approved_roots=)` takes the cache roots from the worker configuration (7.3 passes them).
  - Known LLaMA-Factory/TrainingArguments path keys (`tokenized_path`, `cache_dir`, `deepspeed`, `logging_dir`,
    `media_dir`, `adapter_folder`, …) may hold only the approved values. The prefix heuristic remains as a catch-all
    for unknown keys.
  - `binding-evidence.json` adds `train_bound_yaml_sha256` of the exact executed bytes.
- Training rules:
  - `check_model_input_contract` refuses an initialization that differs from the method, so `translate(full,
    continue_adapter)` no longer emits an adapter slot.
  - `prepare(GraphTrainingConfig)` without `model_input` refuses instead of writing a legacy plan.
  - A PPO reward needs both `v_head.summary.weight` and `v_head.summary.bias` in `value_head.safetensors`.
- Guards and docs: a test fails if any `src/**/*.py` contains CR bytes, so `source_sha256` cannot depend on the
  checkout. Sub-plan F18 and Review Focus 10 now point at `d488f35a…3473` and its explicit construction.

Verification after the review fixes:
- RED first for each fix: 5 planner/compile tests, then 30 model-input tests (`approved_roots`, path keys, hash count,
  value-head bias, translate/prepare defence). The CR guard failed on a probe CRLF file.
- `uv run pytest -q` gave **927 passed, 1 skipped**. Ruff is clean, `check_secrets.py` reports 0 findings, and
  `git ls-files --eol | awk '$2 ~ /w\/crlf/'` prints nothing.
- The CPU acceptance (`-I`) exited 0 with all 25 checks passed and the same max errors. Receipt SHA256
  `905e7494ff88ad65e543d2d7909f92b32644789051bca76c7ab5e56171897a27` (byte-identical on a second run), source_sha256
  `66d599bb1c57e14a2d6fe0cd33f02f5c93cd891f84cff5f9604b807ef9564b62`, and lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437` unchanged.

Task 7.3 (2026-10-20, sub-plan §3 7.3): worker protocol v2 and operation dispatch.
- `operation_runner` adds `WorkerRequest` (`command.json`: canonical bytes, SHA256 on the worker argv), `StartMarker`
  (`start.json`), `OperationResult` (`result.json`, atomic) and `execute_operation(operation, directory, timeout,
  canceled, *, backend, model_cache, hf_home, ops)`. The dispatch allowlist is train/evaluate/merge/materialize;
  LocalSelect is refused (`local selection never dispatches to a GPU worker`).
- `worker DIRECTORY --request SHA256` runs `main_v2`. It requires `<job_id>/<launch_id>` 32-hex directories (else exit 2,
  nothing written), reads and hash-checks `command.json`, then creates `start.json` with `O_CREAT|O_EXCL`. Only the
  creator of the start marker writes anything else: a second start exits 3 and touches nothing, so a lost start
  response can never become a second training run, and the marker and the result carry the same `launch_id`. The
  checks then run in order: launch identity, manifest hash and schema 2, operation hash, kind allowlist, resolved form,
  simulation refusal, source hash, request timeout equal to the operation budget, `FACTORY_GPU_LOCK_FILE` hash equal to
  `runtime.dependency_sha256`, LLaMA-Factory version, manifest datasets equal to the operation's, and
  `FACTORY_MODEL_CACHE`. A result reports identities only as far as they were verified. `--verify-model-cache DIR
  ARTIFACT_ID INVENTORY_SHA256` is the fixed-argv cache check for 7.4 (exit 0/1, never deletes). The v1 argv, body and
  `test_remote_worker.py` are unchanged (389 lines added, 0 removed); `runtime_preflight`'s tokenizer comparison became
  the identical helper `_same_tokenizer`.
- Train: the logical template is recomputed and must equal the uploaded `train.yaml`; data and `dataset_info.json`
  are hash-checked. Then `verify_model_paths` (with `model_ops.inspect_target_modules` as `inspect_targets`) →
  `bind_training_plan(approved_roots=(FACTORY_MODEL_CACHE, HF_HOME))`, from the worker's environment, never the request
  → `write_bound_plan` → `runtime_preflight_bound` (AutoConfig/AutoTokenizer only on the bound local policy base with
  `local_files_only`; cutoff checked against the bound config; a full PPO reward's tokenizer is compared on its bound
  path) → `run_config(..., "train.bound.yaml")` → output validation. Full/freeze outputs in the primary runtime get
  `target-inventory.json` (modules only, via the new `inspect_linear_modules`); 7.5 binds it to the frozen artifact
  through `verify_inspected_targets`.
- Evaluate: the registered benchmark bytes are uploaded as `data/benchmark.<json|jsonl>` (`write_benchmark_data`). The
  worker derives `data/validation.json` in `ordered_sample_ids` order and runs `predict_bound` on
  `prediction_plan(...)`: bound paths, benchmark template, cutoff, max_new_tokens, seed, `do_sample: false`, fp16/bf16
  and the benchmark quantization. Output is `output/predictions.json` with predictions only. `sample`, `fp32`,
  `reward_model` semantics and a benchmark tokenizer that differs from the model are refused;
  `evaluation_preflight_bound` checks GPU, BF16 and context on the bound model.
- Merge/materialize: these require the primary runtime (`operation_profile`) and installed `MODEL_OPS_PINS` (checked
  with package metadata before any work). Each execution creates its own exclusive output parent `model-op/` (an
  existing one, for example with a crashed run's `.model-op-*` staging, is refused and left untouched). The receipt
  must name this operation, the F32 policy and the exact output files. `operation-receipt.json` records the output
  `directory_inventory_sha256`. Materialize also writes `target-inventory.json`.
- Output safety (7.1 review M6): `model_ops.publish_output` fsyncs every payload file and directory, then moves
  without ever replacing a path. On Windows this uses `os.rename`; on Linux, `renameat2(RENAME_NOREPLACE)`, with a
  portable fallback that creates each directory exclusively and hard-links each file. The parent directory is fsynced
  after the move. An existing file, an empty directory, or an empty directory created in the race window is refused.
- Platform newlines: transformers 5.6.0 writes `config.json`/`generation_config.json` with CRLF on Windows (probed).
  `materialize_model` now rewrites them to LF (the parsed content must be unchanged; tokenizer files are never
  touched) before listing or hashing, so the output inventory no longer depends on `os.linesep`.
- Earlier failures: staging now runs `check_full_model_directory` on every full-model artifact and the pre-quantized
  `config.json` rule (QLoRA policy base, merge/materialize base), and the worker's `verify_model_paths` applies the
  same rule to merge/materialize bases. Both run before any engine starts.
- Verifier hardening: `ArtifactVerifier(completed=)` is gone (each verifier owns a private cache), and
  `ModelArtifactStore.verifier()` takes no `lineage` argument, so entry-only verification is the worker's alone. The
  store's private `_verify` was removed and the cycle test migrated to a verifier.
- Added the missing `EvaluateOperation` staging test.

Deviations from the sub-plan:
- `execute_operation` returns the COMPLETED result and raises on failure. `main_v2` fills `attempt_id` and
  `manifest_sha256` and turns exceptions into FAILED/CANCELED results.
- Merge/materialize outputs live in `model-op/output`, not `output/`.
- `predict_bound(directory, plan, sample_ids, timeout, canceled)` is paired with a static `prediction_plan`;
  `evaluation_preflight_bound` and `inspect_linear_modules` are new.
- `target-inventory.json` has no base reference (the output has no identity yet) and is not written in the PPO runtime,
  whose transformers differs from `MODEL_OPS_PINS` (fail-closed: a PPO full output carries no inspected targets).
- `test_model_inputs.py::test_qlora_child_requires_unquantized_full_base` now expects the staging refusal; the
  test fixture `peft_adapter` gains `config=`.

Actual verification:
- RED: 48 new tests failed on the missing protocol, dispatch, publishing, verifier and staging behaviour.
- GREEN: the sub-plan set (`test_remote_worker`, `test_model_ops_contract`, `test_model_inputs`, `test_runtime_profiles`)
  gave **228 passed**. `uv run pytest -q` gave **975 passed, 1 skipped**.
- Ruff is clean, `check_secrets.py` reports 0 findings, both GPU locks are unchanged, `git ls-files --eol` shows no CRLF,
  and the main `.venv` has no torch/peft/transformers/safetensors/huggingface_hub.
- The CPU acceptance (`-I`) exited 0 with all **28** checks passed: the 25 from 7.1/7.2 with unchanged max errors
  (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8), plus three new 7.3
  checks. `execute_operation_fixtures` builds adapters on the frozen base artifact, so nothing is downloaded.
  `execute_operation_merge_end_to_end` runs stage → `execute_operation` → `model_ops` → `publish_output`.
  `execute_operation_materialize_end_to_end` also checks LF-only JSON, base tokenizer bytes and a 14-module
  `target-inventory.json`. The dispatched outputs are byte-identical to the direct `model_ops` outputs (merge inventory
  `cc5fbab0…b54c`, materialize `e124a915…8682`). Receipt SHA256
  `3890086443760e87afd03bfee924c42fabbba120c94cf8c51de6ee16f6c0cee3` (byte-identical on a second run), source_sha256
  `c58b9786fc27bed0fc5fe8024eadf9cf6479c10d6e737cd98e3be0e4363793fe`, lock_sha256 `5883aae6…1437` unchanged;
  `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false. (Superseded by the
  review-fix receipt below.)

7.3 review fixes (commit `fix: enforce one worker deadline and split worker protocol from dispatch`):
- One deadline: `main_v2` derives a single monotonic deadline (start + request timeout) and hands dispatch what is
  left. `execute_operation` keeps that one deadline: cancel and the deadline are checked before and after model
  verification (cache hashing, HF snapshots), and `run_config`/`predict_bound` receive `deadline - now`, so
  verification time comes out of the engine budget. Merge/materialize cannot be interrupted, so they are checked again
  after they return: past the deadline or after cancellation they raise before `operation-receipt.json` is written and
  are never COMPLETED.
- Module split (the names above moved; no behaviour change):
  - `worker_protocol.py` holds `WorkerRequest`, `StartMarker`, `OperationResult`, `read_worker_request`,
    `create_start_marker` and `write_operation_result`.
  - `operation_dispatch.py` holds `execute_operation`, its helpers and `write_benchmark_data`.
  - `operation_runner.py` keeps staging and binding, with three helpers made public for dispatch
    (`validated_operation`, `approved_model_path`, `WEIGHT_SUFFIXES`).
  - The worker passes `preflight=`/`evaluation_preflight=` in, so no module imports `worker`; a test checks the
    layering. Dispatch refuses to train or evaluate without them.
- `execute_operation(..., request=WorkerRequest)` must match the launch directory and the operation hash. Its result
  carries the request's attempt and manifest identities, and `OperationResult` now requires attempt, manifest,
  operation and kind for COMPLETED.
- Durability and safety:
  - The parent directory is fsynced after `start.json` and after `result.json` (shared
    `model_artifacts.fsync_directory`).
  - A failed hard-link fallback in `publish_output` removes the partial output it created.
  - `model.safetensors.index.json` is newline-normalized too.
  - The receipt's file list is compared order-independently (`sorted` on both sides; the set and count stay exact).
    Note: `_files` already returned paths in global sort order, so `model_ops`' own receipts never hit the reported
    false refusal; the new test covers a depth-first receipt.
- Relative `FACTORY_MODEL_CACHE`/`HF_HOME` are refused, by both the worker and dispatch.
- Cross-protocol guard: `SSHExecutor.run` (v1) refuses a directory with `command.json`/`start.json` or a schema-2
  manifest before writing or uploading anything, and the v1 worker entry exits 2 for such a directory without
  writing. So a v1 run can never overwrite a v2 `result.json`.
- Tests: a real "start.json exists, no result" test (exit 3, nothing written); the misleading comment is fixed; a
  continuation through `execute_operation` proves `inspect_target_modules` is wired (and an incomplete worker
  inspection is refused before the engine runs).

Verification after the review fixes:
- RED: 50 tests failed (new modules missing, plus each new rule).
- GREEN: the sub-plan set gave **248 passed**; `uv run pytest -q` gave **995 passed, 1 skipped**. Ruff (`src tests`) is
  clean, `check_secrets.py` reports 0 findings (678 file versions, new modules staged), both GPU locks are unchanged,
  the eol check prints nothing, and `test_remote_worker.py` is still additions-only against f80e316 (488 added, 0
  removed).
- Module sizes: `operation_runner.py` 611 lines (staging and binding), `operation_dispatch.py` 358,
  `worker_protocol.py` 126, `worker.py` 411.
- The CPU acceptance (`-I`) exited 0 twice, with all 28 checks passed and byte-identical receipts. Max errors are
  unchanged, and the dispatched outputs keep the same inventories (merge `cc5fbab0…b54c`, materialize
  `e124a915…8682`), so the split changed no behaviour. Receipt SHA256
  `a757507a5b4e1399c5cca5a3afcef1e81e4c75b79fa7b4948dab55a58e02d519`, source_sha256
  `070236f1f14c96f28c681d321d6443a2b870b3d0fcbcbd85ef7eb0692541dc71` (equal to the tree), lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437` unchanged.

Task 7.4 (2026-10-20, sub-plan §3 7.4): SSH transport for v2 operations, for both an existing RunPod Pod and an
`ssh_server` (one provider-neutral code path; only the attempt record and `CUDA_VISIBLE_DEVICES` differ).
- `SSHExecutor.run_operation(job_dir, launch_dir, WorkerRequest, transfers, canceled)` launches at
  `<root>/<job_id>/<launch_id>` with the persisted launch ID (no uuid). In order:
  - Local checks: the job directory and launch ID are 32-hex, the launch directory is named by the launch ID,
    `command.json` is exactly `canonical(request)`, `manifest.json` verifies and matches the request's manifest and
    operation hashes, and the `CachedTransfer`s are exactly `model-inputs.json`'s `transfers` (HF roots are never
    uploaded; the worker resolves them). Then it writes the launch directory's `remote-attempt.json` (protocol 2,
    attempt, launch, command SHA256, Pod ID or server destination; a retry must match it).
  - A remote `start.json` or `result.json` raises `ExecutionUncertain("remote launch already started for this attempt;
    reconcile instead of relaunching")` before any other action. It is checked again right before the upload.
  - A launch directory holding weights (`WEIGHT_SUFFIXES`) or links is refused before any upload.
  - The cache root and HF_HOME are created or walked over SFTP, with every component required to be an ordinary
    directory, so neither sits under a symlink.
  - Each transfer: an existing `<cache>/<id>/<inv>` is reused only after the fixed argv
    `[python, -m, tuning_factory.worker, --verify-model-cache, <cache>, <id>, <inv>]` exits 0. Exit 1 refuses with
    `remote model cache entry failed verification; remove it after inspection` (nothing is deleted). Any other exit
    status means the check could not run and is never reported as corruption. A missing entry is uploaded to an
    exclusively created `<cache>/.incoming/<launch_id>/<id>/<inv>`, verified there, then renamed with the fixed argv
    `mv -T -- <incoming> <final>`: real directories, no link, never over a non-empty entry. If another publisher won the
    race, its entry must verify and ours is left for manual cleanup.
  - The launch files are uploaded. `start.json` is in the upload exclusions, plus `remote-attempt.json` for v2.
  - One launch: `nohup env [CUDA_VISIBLE_DEVICES=…] [FACTORY_GPU_LOCK_FILE=…] [HF_HOME=…] FACTORY_MODEL_CACHE=<cache>
    <python> -m tuning_factory.worker <remote> --request <sha256(command.json)> >> worker.log 2>&1 < /dev/null &`.
    `submitted` is set before `exec_command`, as in v1.
  - The wait loop is shared with v1 (`_await_result`; the v1 behaviour is unchanged). Past timeout + 60 s, or on any
    failure between the launch and a trusted result, the outcome is `ExecutionUncertain`: an overrunning merge or
    materialize goes to reconcile and is never relaunched.
- Trust and fencing: only `result.json` is trusted; `start.json` is an existence fact and its claims are never read.
  `result.launch_id` must equal the request. For COMPLETED, attempt, manifest, operation and the manifest's kind must
  all match, and other statuses may only omit identities. Otherwise the error is `remote result belongs to a different
  attempt`, checked before anything is downloaded (inside `run_operation` this is uncertain). Before the hash-verified
  download, the remote `command.json`/`manifest.json` bytes must equal the local ones, and `artifacts.json` must list
  the local hashes of `command.json`, `manifest.json` and `model-inputs.json`, so a download can never replace them.
  Every `result.evidence` file must match after the download. The result dict is returned for any terminal status.
- `collect_operation_result` never launches and works with the recovery-only executor. It checks the stored
  attempt record (destination, path and identities) and returns None while there is no result, so a start marker
  alone stays uncertain.
- `publish_cached_artifact(transfer, *, source_remote_dir=None, launch_id=None)` seeds an entry from a finished launch's
  remote output inside this executor's namespace. Payload files present there are copied with the fixed argv
  `cp -P -- <src> <dst>`; metadata and missing files are uploaded. Then it verifies and renames like above, and a
  verified entry is reused. Recovery-only executors can neither launch nor publish.
- `ExecutionBackend.run_operation` raises NotImplementedError. The v1 `collect_result` now refuses v2 directories too.
- `desktop_runtime`:
  - `executor_for(..., runtime_profile=, model_ops=)` selects the private runtime by operation profile (`ppo` → the PPO
    venv and lock). An inconsistent stage, an unknown profile, or model ops on `ppo` are refused before any contact.
  - Launch executors get `model_cache_root` = `<remote_root>/model-cache` (server) or `/workspace/factory-desktop/
    model-cache` (Pod).
  - `check(..., model_ops=True)` also probes the installed torch/peft/transformers/safetensors versions (local suffix
    stripped), which must equal `MODEL_OPS_PINS`. The default probe string and result dict are byte-identical, and a
    test pins the legacy probe text.
- Worker (M1): `execute_operation` refuses a `FACTORY_MODEL_CACHE` or `HF_HOME` under a symlink or reparse point
  (`<name> must not sit under a symlink or reparse point`); before this change an HF_HOME under a link was accepted.
- 7.3 review nits: the duplicated v2-directory detection is now one `worker_protocol.is_v2_directory`, used by the v1
  executor, the v1 collector and the v1 worker entry, with a test that pins its behaviour. The comment on the
  `worker.py` exit line no longer claims v1 always exits 0.

Deviations from the sub-plan:
- The incoming directory is `.incoming/<launch_id>/<id>/<inv>`, not `<id>-<inv>`, because the fixed-argv verifier
  checks `<DIR>/<id>/<inv>`.
- The identity check runs before `save_remote_evidence` and the download (plan steps 8/9 swapped), so a foreign result
  is never transferred.
- `publish_cached_artifact` gains `launch_id=` for the upload case: the incoming name needs a launch identity and
  no uuid is used.
- `executor_for` gains `model_ops=` so the new probe has a caller.
- `operation_dispatch._absolute_root` gains the link check, outside the plan's file list.
- The v2 methods leave the SSH connection open; v1 `run`/`collect_result` close it. `connection()` owns it, so 7.5 can
  collect and publish on one executor.

Actual verification:
- RED: 32 new tests failed (missing transport, cache, collection, desktop selection, probe, shared helper and worker
  link check). The HF_HOME-link case completed with exit 0 before the fix.
- GREEN: the sub-plan set (`test_remote_worker`, `test_desktop_runtime`, `test_execution`, `test_gpu_servers`) gave
  **202 passed**. `uv run pytest -q` gave **1028 passed, 1 skipped**. Both test files are additions-only (537 and 91
  lines added, 0 removed), so every v1 test runs unmodified.
- Ruff (`src tests`) is clean, both GPU locks are unchanged, and the main `.venv` has no torch/peft/transformers/
  safetensors/huggingface_hub.
- The CPU acceptance (`-I`) exited 0 twice with byte-identical receipts and all **28** checks passed. Max errors are
  unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8), and so
  are the dispatched inventories (merge `cc5fbab0…b54c`, materialize `e124a915…8682`). Receipt SHA256
  `0ca10069572a1b91bb8d446a5c397d3354410f998d0ff0ac25d9fb4c9f813b0c`, source_sha256
  `4f54ba904241bc0fd6582bc85f115ab581fc7da9dcd8cedc4c325887ddc0ff4d` (equal to the tree), lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437` unchanged.
- No real SSH, GPU, download or institution data: every remote action ran against `GPUHost`, a local-tree fake. It runs
  the real `worker --verify-model-cache` and the real `worker.main_v2` against the published cache.

7.4 review fixes (commit `fix: publish verified caches from launch outputs and harden remote transfer`):
- Publishing from a launch output (important). `cp -P` of every file present in the output failed for real training
  outputs, because the engine re-saves tokenizer files (and config) while 7.5 freezes them from the effective base.
  The failed incoming copy then blocked every retry. Now, in the new module `remote_model_cache.py` (`ensure_cached`):
  - Only weight payload files (`WEIGHT_SUFFIXES`) are copied on the host, and only when `lstat` shows a regular
    file with exactly the frozen size. Metadata, configs, tokenizer files and anything else are uploaded from the
    control-plane entry.
  - Every inventory path, size and hash is validated (`CachedTransfer.checked`) before any remote change.
  - Each publish uses a fresh slot `<cache>/.incoming/<launch_id>/<n>`: the first `n` that an exclusive `mkdir`
    creates (at most 100). A failed slot is kept for inspection and never blocks a later attempt. The slot is then
    verified and moved into place with `mv -T`, as before.
- The remote launch directory is created with an exclusive `sftp.mkdir` after its parents. An existing launch
  directory raises `ExecutionUncertain` (`remote launch directory already exists for this attempt; reconcile instead
  of relaunching`), so of two racing controllers only one uploads and launches.
- Exit codes: `worker --verify-model-cache` exits `CACHE_ENTRY_INVALID` (65) for an entry that was read and failed
  verification, 1 for one that could not be read (missing, permissions; also `python -m` on ImportError) and 2 for
  invalid identifiers. The executor treats only 65 as corrupt. Any other status raises `remote model cache check could
  not run (exit N): <stderr[:200]>`, and `run_fixed` keeps stderr for every fixed-argv command. The 7.3 test that
  expected exit 1 for a corrupt entry now expects 65.
- Control-plane records: a v2 download never takes `result.json`, `result.tmp`, `remote-attempt.json`,
  `remote-result.json`, `remote-artifacts.json` (or their `.tmp`) from the host. `remote-artifacts.json` and
  `remote-result.json` are written after the verified download. Callers use the returned result dict; the host's
  `result.json` is never downloaded.
- `download` (v1 and v2) refuses names containing `:` and any target outside the root, so a remote `C:evil` cannot
  create directories outside the launch.
- `remote-attempt.json` and the evidence records are written via `<name>.tmp` and replace.
- Connection ownership is enforced: the v2 methods refuse an executor that opened its own connection (`v2 operations
  require an injected SSH connection owned by desktop_runtime.connection()`). `publish_cached_artifact` validates the
  cache root, the transfer and the source before it opens SFTP.
- Module split: `CachedTransfer`, `ensure_cached`, `run_fixed`, `verified` and the remote `lstat` helpers live in
  `remote_model_cache.py` (180 lines). `execution.CachedTransfer` is re-exported, and a test pins it.
  `execution.py` is 683 lines.
- Fake fidelity: `GPUHost`'s `cp` honours `-P` (`follow_symlinks=False`), its verify stub returns the worker's real
  stderr, and new tests cover the `run_fixed` timeout (the channel is closed) and differing remote bytes.

Verification after the 7.4 review fixes:
- RED first: 17 tests failed. They covered the reviewer's reproduction (a remote `tokenizer.json`/`config.json`
  differing from the frozen bytes makes publishing fail), retry blocking, validation order, the launch-directory race,
  forged control records, `C:evil`/`data:stream` names, exit codes, stderr, the timeout and connection ownership.
- GREEN: the sub-plan set (`test_remote_worker`, `test_desktop_runtime`, `test_execution`, `test_gpu_servers`) gave
  **212 passed**. `uv run pytest -q --ignore=tests/test_operation_runner.py` gave **1038 passed, 1 skipped**. The plain
  `uv run pytest -q` also collects the concurrent 7.5 agent's untracked, in-progress `tests/test_operation_runner.py`,
  whose 30 failures are its own RED tests (1038 passed, 30 failed, 1 skipped, and no other failure).
- Ruff (`src tests`) is clean, both GPU locks are unchanged, and the main `.venv` has no torch.
- The CPU acceptance (`-I`) exited 0 twice with byte-identical receipts and all **28** checks passed. Max errors and the
  dispatched inventories (`cc5fbab0…b54c`, `e124a915…8682`) are unchanged. Receipt SHA256
  `191e83abd63a5d265fd68b9878d33f60f0d807e51c1c877f857b09d9a00630a7`, source_sha256
  `6de616686f3bc25a63000931a303e3a435f762a4abe960b0bdc98be5be97942d` (equal to the tree), lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437` unchanged. This supersedes the 7.4 receipt above.

7.4 follow-up (commit `fix: refuse Windows name aliases and protect uploaded inputs on download`):
- Windows name aliases. NTFS is case-insensitive and Win32 strips a trailing dot or space, so a Linux host could plant
  `REMOTE-ATTEMPT.JSON`, `remote-attempt.json ` or `command.json.` and overwrite local control files. `download` now
  checks every name of a remote directory before writing anything in it, and refuses:
  - reserved characters and control codes, a trailing dot or space, and device names (`NUL.json`, `COM1`, …);
  - two names equal under `casefold`;
  - a symlink;
  - a name equal to a skipped control-plane record (`cancel`, the v2 records) or to a protected path only under
    `casefold`, including through an aliased parent directory (`Data/dataset.json`).
  It deliberately does not restrict names to a portable character set. A test keeps engine output names working,
  e.g. TensorBoard `runs/Oct20_12-34-56_<host>/events.out.tfevents.…` and `checkpoint-10/…`.
- Uploaded inputs are protected. `remote-attempt.json` records an `uploaded` inventory (relative path → SHA256 of every
  file the upload sends, with the same exclusions as the upload). Before any download, the host's `artifacts.json` must
  list exactly those hashes for all of them: command, manifest, model inputs, `train.yaml`, `data/*` and benchmark data.
  The download never replaces them. This replaces the three-file check. v2 uploads also exclude `artifacts.json` and
  `worker.log` (worker-written names).

Verification after the follow-up:
- RED first: 16 tests failed (11 alias forms, engine output names, a case-alias integration test and three
  host-forged inputs with a matching inventory).
- GREEN: the sub-plan set gave **228 passed**. The code landed on top of the 7.5 commits (`7120193`, then `ced56ed`,
  which changed docs only). In a clean detached worktree of `7120193` plus only this change, `uv run pytest -q` gave
  **1090 passed, 1 skipped**, including all 36 `tests/test_operation_runner.py` tests (1054 + 36). The same run on the
  shared tree (`ced56ed` plus this change, nothing else uncommitted) also gave **1090 passed, 1 skipped**. An earlier
  run on the pre-7.5 base hit one failure in the timing-sensitive
  `test_cancellation_does_not_wait_for_training_when_taskkill_is_denied` while two suites ran at once. It passed 3/3
  alone, and every later full run was clean.
- Ruff is clean, `check_secrets.py` reports 0 findings, and the eol check prints nothing.
- The CPU acceptance (`-I`, script and sources from that clean worktree) exited 0 twice with byte-identical receipts
  and all **29** checks passed. Max errors and inventories (`cc5fbab0…b54c`, `e124a915…8682`) are unchanged. Receipt
  SHA256 `a9bece24ded6e84c0637007e7fe78288d61f9125d944a9add348b13b8c7ee45d`, source_sha256
  `247003f6a987096bcbee4739a3a4ffb4aeaf2836f3c75622c0b42f5e28b96c7f` (the tree with this commit), lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437` unchanged. This supersedes every earlier Task 7
  receipt, including 7.5's `811d4177…b673` (source `478e2a41…e5d2` of `7120193`).

7.4 short-name follow-up (commit `fix: refuse 8.3 short-name aliases on download`):
- On NTFS volumes that generate 8.3 names (this machine's C:, including `%TEMP%`, `%LOCALAPPDATA%` and `%APPDATA%`,
  where the desktop app keeps its state), an existing `remote-attempt.json` can also be opened as `REMOTE~1.JSO`. Such
  a name passed every earlier check: no reserved character, no casefold match, and it resolves inside the root. The
  review reproduced a forged `remote-attempt.json` (and still a COMPLETED result) and a replaced protected
  `data/dataset.json`.
- `download` now refuses any target that already exists locally (`os.path.lexists`) whose canonical relative path
  (`resolve()` returns the long name and real case) differs from the remote name. That one check covers 8.3, case
  and trailing-dot/space aliases of any existing file or directory. As a second guard, any remote name containing
  `~<digit>` is refused; engine output never contains one. Device names now also cover `COM0`/`LPT0`, `COM¹`–`COM³`,
  `LPT¹`–`LPT³`, `CONIN$` and `CONOUT$`.
- Tests:
  - The real short names, from `GetShortPathNameW`, of a local `remote-attempt.json` and of a protected
    `data/dataset.json` are refused, with the local bytes unchanged. Each runs with both guards and with the
    canonical-name check alone (the `~<digit>` guard disabled). They skip off Windows or where no 8.3 name exists;
    here they ran.
  - Plain unit cases cover `~<digit>` names and the new device names everywhere.
  - The `Data/dataset.json` parent-alias case is now refused at the existing `data` directory.
- Verification. RED first: 11 tests failed. The two real short-name cases reproduced the overwrite (no refusal).
  - The sub-plan set gave **239 passed**.
  - In a clean detached worktree of `14c1ba4` plus only this change (the next commit, `2075a26`, adds only a plan
    document), `uv run pytest -q` gave **1101 passed,
    1 skipped**. That was the second full run there. The first had a single failure in
    `test_operation_runner.py::test_outputs_rebuild_is_idempotent_after_crash[after_result]` that never reproduced:
    that test file passed 36/36 twice with this change and twice without it, and the exact parametrization passed
    8/8.
  - The shared tree, which also held the 7.5 fix agent's uncommitted edits and tests, gave **1120 passed,
    1 skipped**.
  - Ruff is clean, `check_secrets.py` reports 0 findings, and the eol check prints nothing.
- CPU acceptance (`-I`, script and sources from the clean worktree): exit 0 twice with byte-identical receipts and all
  **29** checks passed. Max errors and inventories are unchanged. Receipt SHA256
  `97fa2c89076ae8687b31e1229dc3b7fa6f5d45c1c3feef96fdf4bcd5bafcdf3b`, source_sha256
  `0ae3419b0bb6a96cba139ce413b43eba266c6977e2f67ede1d963eba5dc9b67a` (the tree with this commit), lock_sha256
  `5883aae6…1437` unchanged. This supersedes `a9bece24…e45d`.

Still open for Task 7 GPU work:
- Hardware and numerics: merge/materialize tolerances at real model sizes on CUDA (TF32 off); memory (FP32 loads;
  about 3 base loads per merge); a BF16 storage policy (none yet; F32 doubles size).
- Worker v2 on a real GPU host: real LF training on `train.bound.yaml`, real `predict_bound` generation, real
  `runtime_preflight_bound`/`evaluation_preflight_bound` facts and `renameat2` on the host filesystem are unverified
  (all tested with fakes). Merge/materialize cannot be interrupted mid-operation; one that overruns its deadline is
  refused after it returns. Since 7.4 an executor whose timeout + 60 s window ends first reports ExecutionUncertain,
  and the late FAILED result is collected by reconcile.
- SSH transport on a real host (7.4 is software-only): SFTP `lstat` link semantics, GNU `mv -T`/`cp -P` on the cache
  filesystem (the incoming copy and the cache must be on one filesystem; `mv` would otherwise copy), the
  `--verify-model-cache` runtime at multi-GB sizes (the check's timeout is 3600 s), cache publishing on a RunPod
  network volume and on a lab server, and a real lost launch response. Abandoned `.incoming/<launch_id>/<n>` slots
  and corrupt entries are never removed automatically and need operator cleanup. If a launch output holds a weight
  file with the frozen size but different bytes, publishing from that output fails closed; publishing without
  `source_remote_dir` uploads the frozen copy instead.
- Attempt runner (7.5, including F11): done in 7.5, below.
- Real DPO/KTO references and PPO remain unverified.

Task 7.5 (2026-10-20, sub-plan §3 7.5): control-plane attempt runner, verified outputs and F11.
- New `attempt_runner.py` (426 lines at `7120193`): `OperationAttemptRunner(graph_store, root=)` and
  `OperationOutcome`. New `operation_outputs.py` (397 lines): output freezing and evaluation records. Neither imports torch; both import no
  worker module. `run_claimed(claimed, executor, *, approval)` mirrors `Factory.run_claimed`:
  1. Refusals before launch call `abandon_unlaunched` (lock freed, `error.txt`, FAILED outcome) and never reach the
     executor. They cover a non-v2 or non-graph job, a local selection, an unresolved operation, simulation, a changed
     source hash, a recovery-only, local or cacheless executor, an untyped approval or one that fails
     `validate_plan`, resources outside the node's approved alternatives, a destination missing from the approved
     revisions, an executor that is not the pinned destination (SSH: same profile and `observation_fits`; Pod: same
     ID; both `validate_config` against the pinned budget), a lease on another resource, manifest datasets that
     differ from the registry or are not `export_allowed`, and a claimed launch ID that differs from the persisted
     one.
  2. `begin_launch` reserves the launch once. It then prepares `jobs/<job>/launches/<launch_id>/`: `manifest.json`,
     `prepare(...)` (train) or `write_benchmark_data` (evaluate), `stage_model_inputs` (continuation evidence and
     producers come from the same `GraphStore._bindings` resolver that compiled the node), and `command.json`, the
     canonical `WorkerRequest` with the persisted attempt and launch IDs. `CachedTransfer`s come from the approved
     store.
  3. `transition RUNNING` → `executor.run_operation`. The returned dict is the only result used. It is re-checked
     against the request and kept as `launches/<launch_id>.result.json` beside the launch directory, which downloads
     never reach. A non-result, or a foreign one, is `ExecutionUncertain`.
  4. COMPLETED: `transition EVALUATING` → outputs → `transition COMPLETED` → `release_gpu`. FAILED or CANCELED
     results, and failures before submission, release the lock. `ExecutionUncertain` keeps it (`uncertain=True`). A
     cancellation requested before a late completion wins (v1 semantics), and nothing is frozen.
  5. The destination's model cache is seeded with `publish_cached_artifact(source_remote_dir=<remote launch>/output`
     or `/model-op/output)`. This is best effort, recorded in `launches/<launch_id>.cache-seed.json`, and skipped
     for recovery executors. It runs after `release_gpu` and is never retried: a crash before or during it only
     skips seeding, and a later operation on that host uploads the frozen copy when it needs it.
- Outputs (`operation_outputs`). The result must be COMPLETED for exactly this launch, attempt, manifest, operation
  and kind (`remote result belongs to a different attempt`), and every evidence file must match.
  - Train: payload allowlist (adapter config/weights, or `config.json` + `model.safetensors` | shards + index, plus
    `value_head.safetensors` and `generation_config.json`). README, checkpoints, `training_args.bin` and optimizer
    states are dropped. Tokenizer files, and an adapter's `config.json`, come byte-for-byte from the effective base.
  - Engine tokenizer files must keep the base's vocabulary, added tokens and special-token ids (`engine tokenizer
    output changes the vocabulary; refusing artifact`). Only facts present in both snapshots are compared; a re-saved
    `pad_token` assignment is allowed. Full models must also keep `config.json` `vocab_size`.
  - Train descriptors: `effective_base = policy.base`; `parents` = the policy base artifact plus the policy adapter
    (sorted); the root is the policy's; `has_value_head` for rm/ppo; `adapter_metadata_from_config` of the engine's
    `adapter_config.json`; synthetic if the dataset or any bound input is.
  - Merge: rank = Σ ranks (checked against the receipt and the file). `parents` = the inputs plus the base artifact.
    The method and quantization are the inputs' if uniform, else `lora`/None.
  - Materialize: `full_model`, `quantization_bit=None`, `parents` = adapter + base artifact.
  - `model-op/output` is frozen as it is; its files must equal the receipt's and the allowlist, and the frozen
    `inventory_sha256` must equal `operation-receipt.json`'s `output_inventory_sha256`.
  - Full-model outputs (materialize; full/freeze training in the primary runtime) bind `target-inventory.json` to the
    frozen ref through `verify_inspected_targets`. They return it as `OperationOutcome.inspected_targets`; the
    `NodeOutputs` carry none, because `record_outputs` still refuses them.
  - OFT training is refused at planning and before launch (7.5 review fixes below); freezing keeps the same
    refusal as a last guard.
- Evaluate: predictions only from the worker (`ordered_sample_ids` must equal the benchmark's). References are the
  registered rows' `output` or final assistant turn, in benchmark order; the engine's labels are never used. Metrics
  are `CommonEvaluator().evaluate(..., evaluator_kind)`. The output is `EvaluationRecord(benchmark_sha256 =
  BenchmarkSpec.fingerprint(), status="completed", producer_attempt_id=attempt)` in
  `NodeEvaluationBinding(tracking_acknowledged=False, producer=None)`.
- F11 (graph_store.py deviation): `record_outputs` compares `BenchmarkSpec.model_validate(operation["benchmark"])
  .fingerprint()`. The end-to-end test runs evaluate → `record_outputs` → `acknowledge_tracking` → ledger →
  `compile_node` select → `resolve_local_selection`. It failed at `record_outputs` (`evaluation receipt differs from
  attempt ledger`) before the fix.
- Recovery: `reconcile(job_id, executor)` never launches, and since the review fixes it takes only an attempt that
  still holds its lock; a released attempt is final.
  - FAILED and still locked: collect the same launch, then `reconcile_result`. It returns None while there is no result, or when the
    launch was reserved but `remote-attempt.json` was never written (unprovable; operator `release-lock
    --confirm-execution-stopped`). A FAILED attempt whose launch was never reserved is released.
  - COMPLETED and still locked (a crash before `release_gpu`): outputs are rebuilt and the lock is released.
  - `outputs_for(job_id)` rebuilds identical outputs from local files; freezing is content-addressed.
  - Crash tests (a `BaseException` escapes every handler) cover: before `begin_launch` (the same attempt resumes after
    takeover and launches once, or reconcile releases it), after `begin_launch` and before contact (uncertain; never
    relaunched), after the result, after the freeze, after COMPLETED and after the release. Each of the last four
    reconciles to one training call, one launch and one frozen artifact, and `record_outputs` accepts the rebuilt
    outputs twice.
- Worker (operation_dispatch.py deviation, 18 net lines, 362 → 380): train writes `base-snapshot/` (the bound policy base's
  `config.json` and tokenizer files, listed in the result evidence). The control plane has no HF root files, so this is
  how an adapter trained on an HF root gets the exact base bytes. The bytes are accepted only if their config hash and
  tokenizer fingerprint equal the base's.
- Cleanup: graph_planner reuses `training.requires_frozen_reference` (988 lines at `7120193`, from 990; the review
  fixes' OFT refusal brings it back to 990). model_ops.py is unchanged (916).
- CPU acceptance gains `freeze_dispatched_outputs`: the real PEFT/transformers merge and materialize outputs freeze
  through `freeze_operation_output`, with inventories equal to their receipts and 14 verified target modules.

Deviations from the sub-plan:
- The runner lives in `attempt_runner.py` and `operation_outputs.py`, not in `operation_runner.py`. The 7.3 layering
  test forbids `graph_store`/`jobs` imports there.
- `build_output_descriptor` gains `workspace=` (an empty directory the caller owns, where train outputs are
  assembled) and `dataset_synthetic=`. New helpers: `freeze_operation_output` and `evaluation_record`.
- `reconcile` returns None while uncertain and also handles a COMPLETED attempt whose lock a crash kept.
- The runner reads continuation evidence through the private `GraphStore._bindings` (the trusted resolver hook)
  rather than growing graph_store.
- The operation_dispatch.py worker change above, and the new CPU acceptance check (29 checks).

Actual verification:
- RED: `tests/test_operation_runner.py` failed on the missing module (30 tests). Once the runner existed, the F11 test
  failed exactly at `record_outputs`. The invalid-executor-result test failed under a mutation of `_accept`.
- GREEN: `tests/test_operation_runner.py` **36 passed**. The sub-plan set plus graph tests (`test_model_inputs`,
  `test_model_ops_contract`, `test_remote_worker`, `test_operation_runner`, `test_graph_store`, `test_graph_planner`)
  gave **372 passed**. `uv run pytest -q` gave **1074 passed, 1 skipped** (1038 + 36).
- Ruff (`src tests`) is clean and `check_secrets.py` reports 0 findings. `git ls-files --eol` prints nothing outside
  LF, both GPU locks are unchanged, and the main `.venv` has no torch/peft/transformers/safetensors/huggingface_hub.
- Tests that rely on the 7.4 publish contract of commit 7115a99: `test_outputs_seed_the_remote_cache_from_their_launch`
  (both cases; the train case fails under the earlier copy-everything publish, because the engine re-saves the
  tokenizer) and `test_runner_launches_on_an_ssh_server_destination`.
- CPU acceptance (`-I`), run on a clean checkout of commit 7120193, exited 0 twice with byte-identical receipts and
  all **29** checks passed. Max errors are
  unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8). The
  frozen dispatched outputs keep the 7.3 inventories (merge `cc5fbab0…b54c`, materialize `e124a915…8682`). Receipt
  SHA256 `811d41778b1338380f3ab2b4efc797ad4e39e8ddc3c00e73cb44717d7467b673`, source_sha256
  `478e2a418363e8d115b2537849603e1b2d045fa6402a795d96bdbb6829a5e5d2` (equal to that commit's tree), lock_sha256
  `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. `model_downloaded`, `gpu_used`,
  `institution_data_used` and `production_promotable` are false. This supersedes every earlier Task 7 receipt. (The
  first recorded value, `a9bece24…e45d` with source `247003f6…6c7f`, came from the shared working tree while another
  agent's uncommitted `execution.py` edit was present, so it is void for `7120193`. A clean checkout reproduces it as
  the receipt of the tree with the 7.4 follow-up commit; see that section.)
- No real SSH, GPU, model download or institution data. Every remote action ran on `GPUHost`, which runs the real
  `worker.main_v2` with fixture engines and model operations that write small, well-formed safetensors.

7.5 review fixes (commit `fix: refuse unfreezable OFT before launch and tighten attempt reconciliation`):
- Important: a terminal OFT train node was planned, launched on the GPU and failed only when its output was frozen
  (the reviewer's probe: one launch, then FAILED). One message, `training.OFT_UNFREEZABLE` ("OFT training is
  unsupported in graphs: its output cannot be frozen as a typed model artifact"), now refuses it:
  - at planning: `validate_graph` and `plan_graph` report a `train_contract` issue for every OFT train node, because
    every graph training output is frozen;
  - in `_verify_before_launch`, through `abandon_unlaunched`, for plans approved before the rule;
  - at freezing, kept as a last guard.
- `reconcile` takes only attempts that still hold their lock: FAILED and uncertain, crashed and marked FAILED by
  `JobStore.recover`, or COMPLETED when a crash interrupted the release. A released attempt is final (`attempt is
  final: its lock is released, so reconcile never reopens it; use outputs_for …`). Before, a FAILED attempt whose
  remote result was COMPLETED could be re-collected and turned COMPLETED. The lock check reads `resource_leases`
  directly; JobStore has no public query for it (noted for Task 8, with `_validate_lease`).
- Full/freeze outputs of the primary runtime need `target-inventory.json`, as materialize does. The rule is shared
  with the worker as `operation_dispatch.inspects_full_output` (formerly private). The presence check, and for
  merge/materialize the target verification against the receipted identity, run before the store is written, so a
  refused output never leaves an artifact.
- `reconcile` rebuilds the `WorkerRequest` from the ledger (attempt, launch, manifest, operation timeout) with the
  builder `_prepare` uses. The executor refuses a local `command.json` that differs (`command.json differs from the
  worker request`).
- Tests (22 new; `test_operation_runner.py` has 58):
  - Pre-launch refusals for a changed source hash, an unresolved operation, resources outside the approved
    alternatives, a destination outside the approved revisions, a lease on another resource, a cacheless executor,
    and changed or missing manifest datasets. Each makes no executor call, writes `error.txt` and frees the lock.
  - Training on an HF-root base, downloaded through the fake hub on the GPU host. The artifact's `config.json` and
    tokenizer are the worker's `base-snapshot/` bytes, checked against the root's fingerprints; the effective base and
    root are the HF root, with no parents.
  - The local-selection refusal now asserts `error.txt`.
  - The shared fixture tokenizer (`test_model_inputs.TOKENIZER`) now has a real WordLevel vocabulary and a special
    token. The engine fixture re-saves it with other bytes and a `pad_token` assignment, which is accepted. Four
    negative cases are refused: an added token, renumbered ids, a grown vocabulary and a changed special-token id.
  - A full-training output without `target-inventory.json` is refused with no artifact. A final attempt is never
    reopened, covering both a remote failure and a COMPLETED result that freezing refused. A forged local
    `command.json` is refused.
  - Heartbeats every 10 ms through a slowed run, a crash and a lost start response change nothing but liveness,
    and every heartbeat thread stops with its run.
- Intermittent, not reproduced: `test_outputs_rebuild_is_idempotent_after_crash[after_result]` failed once in the
  7.4 agent's full run (clean worktree of `14c1ba4` + `880763a`), and `test_lost_start_response_never_trains_twice`
  failed once in a shared-tree run of this fix. No traceback survived.
  - Tried: 116 runs of `[after_result]`, with `[after_freeze]`, the lost-start and ledger tests, in three and four
    parallel loops alongside full suites and the CPU acceptance, plus seven full suites. There was no failure. No
    test-order plugin is installed, so the order is fixed.
  - Checked:
    - shared state: the one in-process module reload (`test_model_ops_import_is_lightweight`) is deterministic,
      environment changes go through `monkeypatch`, and no test writes package source;
    - timing: deadlines are at least 900 s, and `recover(stale_after_seconds=-1)` has a 1 s margin; heartbeats are
      now covered by the test above;
    - file locks: Defender real-time protection is off, and temporary names are per-call UUIDs under `tmp_path`;
    - SQLite: one database per test and a 30 s busy timeout;
    - `GPUHost`: its worker runs synchronously.
  - Likely cause (unproven for the worktree run): `source_hash()` re-reads `src/tuning_factory` on every call. A
    runner test compares it at `prepare_ready`, again before launch, and in the worker, so an edit to the source
    tree during a run makes exactly the straddling test refuse ("source hash differs"), as designed. The shared-tree
    failure coincided with the other agent's uncommitted `execution.py` edits: the next run collected 11 more
    tests.
- Module sizes: `attempt_runner.py` 447 lines (426 at `7120193`), `operation_outputs.py` 412 (397),
  `operation_dispatch.py` 380, `graph_planner.py` 990, `model_ops.py` 916 (unchanged).

Verification, in a clean detached worktree of `880763a` (HEAD) plus only this change. The shared tree also held
another agent's uncommitted `execution.py`/`test_remote_worker.py`/PROGRESS edits until that agent committed them.
- RED first: 8 tests failed for the intended reasons. The OFT runner test showed a real launch. The other new tests
  guard behaviour that already held.
- `tests/test_operation_runner.py` gave **58 passed**. `uv run pytest -q` gave **1123 passed, 1 skipped** (1101 at
  `880763a` + 22). Ruff (`src tests`) is clean, `check_secrets.py` reports 0 findings, the eol check prints
  nothing, both GPU locks are unchanged and the main `.venv` has no torch.
- CPU acceptance (`-I`): exit 0 twice with byte-identical receipts and all **29** checks passed. Max errors and the
  frozen inventories are unchanged. Receipt SHA256 `042bf8feb403123cb81206801bda0010e676514a2b3d6dc32125d85ca29c9c0b`, source_sha256 `6b57e120ab5479dfa8b99bd69c2c079cb7be035b007e471da2d53840f9aa9b5f` (the tree with this commit),
  lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. It supersedes earlier Task 7
  receipts.

## Task 7 complete (software), 2026-10-20

Task 7 is complete in software: 7.1–7.5 and their review fixes, with the 7.4 follow-up code behind it (`14c1ba4`:
Windows name aliases and uploaded-input protection on download; `880763a`: 8.3 short-name aliases). Verified on the
CPU and with fakes:
- A hash-locked CPU runtime with `cat` merge and `safe_merge` materialization numerics, F32 policy `model-ops-f32-v1`.
- Path-free staging, and binding of only typed roles to hash-verified cache paths. The rules for exact
  continuation, frozen reference, PPO reward and tokenizer are enforced twice.
- Worker protocol v2: hash-verified `command.json`, an `O_EXCL` start marker and one deadline.
- An SSH transport with a verified, content-addressed remote cache, one launch per attempt and identity-fenced
  results.
- A control-plane runner that freezes outputs with exact lineage, computes evaluation receipts under
  `BenchmarkSpec.fingerprint()` (F11 fixed) and rebuilds idempotently after a crash.
- Final receipt `811d4177…b673` (29/29 checks, source `478e2a41…e5d2` of commit 7120193). After the 7.4 follow-up
  (Windows name aliases and uploaded-input protection on download) it was `a9bece24…e45d` (source `247003f6…6c7f`).
  After the 8.3 short-name follow-up it was `97fa2c89…df3b` (source `0ae3419b…b67a`). After the 7.5 review fixes
  it was `042bf8fe…9c0b` (source `6b57e120…9b5f`). After Task 8.1 it was `5cc24562…63be` (source
  `0d405733…373a`). After Task 8.2 it was `26a68aee…a117` (source `58adcdc8…ec17`). After Task 8.3 it was
  `1ddbabdc…6ceb` (source `eed26807…fc54`). After Task 8.4 it was `2afbd0b8…88f8` (source `9cd9ca7e…6ba0`).
  After Task 8.5 it was `d796f3ed…7f1d` (source `0888d176…ceab`). After Task 9.1 it was `5147d6d9…ab68`
  (source `ebf51635…76ec`). After Task 9.2 it was `50a64996…9f66` (source `0204b69b…16e1`). After the 8.5 review
  fixes it was `6524aba8…5c1f` (source `b6547200…c3a3`). After Task 9.7 it was `cbad9755…2f8d` (source
  `3b1f546a…1631`). After the 8.5 re-review Minors it was `b4cc02a2…c6d4` (source `2f5200b0…457c`). After
  Task 9.3, the current receipt is `00d0043b…7f93` (29/29 checks, source `ef4e1357…4ff1`; see "Task 9.3 runs").

Not verified (needs explicit GPU/host authorization):
- Any real GPU or host.
- Real LLaMA-Factory 0.9.5 training on `train.bound.yaml` (the engine output layout, including where it writes its
  tokenizer, is a fixture assumption) and real `predict_bound` generation.
- Real DPO/KTO frozen references and PPO with a typed reward.
- Merge/materialize tolerances, memory and time at real model sizes on CUDA. There is no BF16 storage policy.
- `renameat2(RENAME_NOREPLACE)` on the host filesystem; SFTP `lstat` link semantics; `mv -T`/`cp -P` across the
  incoming and cache directories; `--verify-model-cache` at multi-GB sizes; cache seeding on a RunPod volume or a lab
  server; a real lost launch response.
- HF root download on the host.
- Planning-time `ModelTargetEvidence` and tokenizer fingerprints for HF roots still have no producer (plan Q3/Q4).

Task 8 interface (`attempt_runner`):
- Construction: `OperationAttemptRunner(graph_store, root=<the state directory that holds jobs.db>)`.
- Inputs to `run_claimed(claimed, executor, *, approval)`:
  - `claimed`: the dict from `GraphStore.claim_ready` (or `takeover_attempt`) holding the `AttemptLease`.
  - `executor`: from `desktop_runtime.executor_for(..., runtime_profile=operation.resources.runtime.profile,
    model_ops=kind in {merge, materialize})`, inside its `connection()` context. The v2 methods require that
    injected client.
  - `approval`: the run's `ApprovalGrant`.
- Output: `OperationOutcome(job_id, attempt_id, status, uncertain, outputs, inspected_targets, error)`.
  - COMPLETED: the lock is released. `outputs` is `NodeOutputs(model=...)` or `NodeOutputs(evaluation=...)` with no
    inspected targets. Task 8 calls `record_outputs(lease, outputs)`, enqueues tracking, and persists
    `inspected_targets` through its own verifier (`record_outputs` still refuses them).
  - FAILED with `uncertain=False`, or CANCELED: the lock is released and the attempt is final; `reconcile` refuses
    it.
  - FAILED with `uncertain=True`: the lock is kept. Never claim, launch or create a new attempt; call
    `reconcile(job_id, executor)`.
- Exceptions:
  - `run_claimed` raises ValueError for a stale lease, and for `launch already started or uncertain; reconcile the
    same attempt` (`begin_launch`). Both mean the attempt is not this actor's to launch.
  - `reconcile` raises ValueError for an attempt still active (run `JobStore.recover` first), for a final attempt
    whose lock is released (`attempt is final: …`), for a local `command.json` that differs from the ledger, and
    for a foreign remote result (the attempt stays uncertain).
  - Uncertainty itself is never raised: `ExecutionUncertain` becomes `uncertain=True`.
- `reconcile(job_id, executor)` accepts recovery-only executors, and only attempts that still hold their lock. It
  rebuilds the `WorkerRequest` from the ledger.
  - It returns None while no result exists. That includes a reserved launch with no remote record, which needs
    operator `release-lock --confirm-execution-stopped`.
  - It returns an outcome when it finishes the attempt. For a COMPLETED attempt whose release a crash interrupted,
    it rebuilds the outputs and releases the lock.
- `outputs_for(job_id)`: a COMPLETED attempt only. It returns identical outputs on every call; `record_outputs` is
  idempotent for them.
- Idempotency guarantees:
  - `begin_launch` runs once per attempt.
  - Three guards stop a second remote start: the executor's exclusive remote launch directory, its
    start/result check, and the worker's `O_EXCL` start marker.
  - The result is persisted before freezing.
  - Freezing is content-addressed.
  - A crash at any step resumes (before `begin_launch`) or reconciles (after it), without a second launch or a second
    artifact.
- A heartbeat runs every 10 s during `run_claimed`. There are no MLflow calls, scheduling or retries in the runner.
- Open for Task 8:
  - A simulated dispatcher for dry-run graphs (plan §6 Q21): the runner refuses simulation manifests, as the worker
    does. `operation_outputs.build_output_descriptor` can serve one that freezes `simulation=True` descriptors.
  - Planning-time refusal of fp32/sample/reward benchmarks (Q8).
  - Persisting `inspected_targets`.

## Task 8.1 ledger foundations, 2026-10-20

Commit `feat: fence graph controllers and reserve budgets in the graph ledger`, on `088a07d`. It implements plan
§4 "8.1" (decisions D1–D14 as accepted) plus the coordinator's two follow-ups from the `088a07d` re-review.

What changed:
- Lock namespaces (F1, D3):
  - `jobs.NON_HARDWARE = ("local:", "simulation:")`, plus the literal `mock-cpu`, never quarantine a hardware claim.
    They block only an identical lock name, and `foreign_provider_lock` ignores them.
  - Cross-provider and unknown (`legacy-unknown:`) locks quarantine exactly as before.
  - A simulation plan claims `simulation:<exclusivity key>`, so it never holds or waits for the real Pod or server.
- Public ledger APIs (coordinator item 3):
  - `JobStore.validate_lease`, with `_validate_lease` kept as the same object, plus `validated_attempt` and
    `holds_lock` (follow-up 1).
  - `GraphStore.bindings_for(run_id, node_id)` replaces `_bindings`, which is removed. 8.1 keeps today's root-only
    default.
  - `model_artifacts.list_files`/`read_json`/`tensor_header` and `dataset_splits.load_dataset`, with the private
    names kept as aliases.
  - `attempt_runner`, `runner.reconcile`, `graph_store`, `graph_planner` and `operation_outputs` now use only the
    public names. `attempt_runner` no longer opens JobStore connections or reads its tables.
- Additive migration, in one `BEGIN IMMEDIATE` in `GraphStore.__init__`:
  - new tables `controller_owner`, `budget_reservations` and `attempt_dispositions`;
  - new nullable columns `controller_leases.owner_token` and `nodes.retry_not_before`/`wait_reason`/`updated`;
  - a RESERVED reservation backfilled for every pre-existing graph attempt. The cost comes from the manifest's pinned
    facts; a select costs 0.

  No existing row changes.
- Owner lease:
  - `acquire_owner`, `renew_owner`, `release_owner` and `owner_state` (never the token), with `controller already
    running` and `stale controller owner lease`.
  - Owner IDs must fully match `[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}`. The TTL must be positive and finite.
  - `acquire_controller(..., owner=)` validates the owner in the same transaction. It replaces a run lease that
    expired or that another owner token holds, and never an unexpired owner-less lease. `owner=None` is unchanged.
  - `GraphStore(clock=)` drives graph runs, leases, reservations and dispositions. Job rows and events keep
    `time.time()`.
- Run state machine:
  - pause: only RUNNING → PAUSED;
  - resume: only PAUSED → RUNNING (`canceled graph cannot resume` stays);
  - cancel: from RUNNING or PAUSED.

  Repeating the current state is a no-op. COMPLETED and FAILED refuse all three.
- Pause gate (D12): `begin_launch` refuses a graph job whose run is not RUNNING (`graph run is not running; launch
  refused`) and leaves `launch_started=0`. `run_claimed` can therefore raise it. The attempt stays PROVISIONING
  with its lock until the controller calls `return_unlaunched`.
- `claim_ready`:
  - With facts it skips selects; with None it claims only selects (F2).
  - It skips a job whose counted attempts (`job_attempts` minus `counted=0` dispositions) reached `max_attempts`.
  - It skips a job whose cost would push the reservations above the approved total × (1 + 1e-9).
  - It inserts the reservation in the claim transaction. `reserved_budget(run_id)` reports the RESERVED sum.
- `return_unlaunched(controller_lease, attempt_lease, *, reason)`:
  - Only for a PROVISIONING, unstarted, non-legacy attempt of the lease's run.
  - It frees the lock, voids the reservation and records `returned_unlaunched` (uncounted).
  - RUNNING or PAUSED run: RETRYING → QUEUED, node READY. `active_attempt_id` is cleared, as `reset_for_resume`
    does, so the old lease no longer validates.
  - CANCELED run: the job and node become CANCELED, and the attempt token is rotated.
- Planning (Q8, D13), simulation included:
  - EvaluateNode calls `training.check_generation_benchmark`, so the worker's exact messages are raised at planning.
  - RunPod facts priced ≤ 0 or non-finite are refused: `RunPod resource facts require a positive live hourly
    price`. An SSH price of 0 still plans. `ResourceFacts` is unchanged, so stored plans stay readable.
  - The price rule sits after the definition issues, so the OFT refusal is still reported first.
- Orphan artifacts (follow-up 2, Minor A):
  - `freeze_operation_output` now computes the store identity before freezing (descriptor hash plus
    `directory_inventory_sha256(source)`).
  - It compares a merge/materialize inventory with `operation-receipt.json` (`model-operation output inventory differs
    from operation-receipt.json`) and verifies every target inventory against that identity. Only then does it freeze.
  - The comparison after freezing stays, as a guard against bytes changing during the copy.
  - So "a refused output never leaves an artifact" (7.5) now holds for the two cases that froze first. The cost is
    one extra hash pass over each model output.

Deviations from the plan:
- `prepare_ready` validates its controller lease first (read only). Before, a fenced lease with nothing left to
  prepare returned `[]`; test 7 requires `stale controller lease`.
- Follow-up 1 adds `JobStore.holds_lock` (not in the plan's interface list) and removes `attempt_runner._holds_lock`.
  An AST test asserts that `attempt_runner` uses no `_`-prefixed JobStore/GraphStore member, no `.connection(` and no
  `resource_leases`.
- Follow-up 2 changes the freeze order in `operation_outputs` and adds two runner tests (six cases).
- `tests/test_gpu_servers.py::test_graph_plan_and_claim_accept_truthful_server_facts` now expects `simulation:` plus
  the server key. That plan is a simulation, and 8.1 specifies the simulation namespace.
- `graph_planner` also imports `load_dataset` instead of `_dataset`; it was the other private importer.
- `tests/test_graph_store.py` gains an autouse fixture that makes `socket.socket.connect` raise, for all its tests.
- The price rule checks every RunPod snapshot passed to `plan_graph`, not only the ones an allowed resource uses.
- Test placement: 1–13 and a `holds_lock` test are in `test_graph_store.py`, 14–15 in `test_graph_planner.py`, 16 in
  `test_model_artifacts.py`, and 17 with the follow-up tests in `test_operation_runner.py`.

Actual verification (main tree, no other agent's uncommitted changes):
- RED, before any `src` edit: 27 of the 29 new cases failed for the intended reasons:
  - missing APIs (`validate_lease`, `holds_lock`, `clock=`, `return_unlaunched`, `GRAPH_LAUNCH_REFUSED`,
    `bindings_for`, the public helpers);
  - missing tables;
  - a `local:` or simulated lock quarantining a GPU claim;
  - a select returned for a GPU observation;
  - a real-key simulation claim;
  - planning that did not raise;
  - an orphan artifact after the receipt or target refusals.

  Two cases passed already, as guards: test 3 and the materialize forged-targets case, which was already refused
  before freezing. Test 12 first failed on a test-fixture bug (a missing subdirectory), fixed before implementing.
- Mutations, through a pytest plugin in `%TEMP%` with no `src` edit:

  | Mutation | Tests that fail |
  |---|---|
  | `begin_launch` without the gate | 9 |
  | `_reserved` returns 0 | 5 and 12 |
  | `_counted_attempts` returns 0 | 12 |
  | `NON_HARDWARE = ()` | 1, 2, 9, 11 and 13 |
- GREEN:
  - The plan's command (`test_graph_store`, `test_graph_planner`, `test_jobs`, `test_reconciliation`,
    `test_operation_runner`, `test_gpu_servers`, `test_desktop_servers`) gave **313 passed** (285 at `088a07d`).
  - `tests/test_model_artifacts.py` gave 88 passed.
  - `uv run pytest -q` gave **1152 passed, 1 skipped** (1123 + 29).
- Ruff (`src tests`) is clean, and `check_secrets.py` reports 0 findings. Both GPU locks are unchanged.
  `git ls-files --eol` prints nothing outside LF, every changed file is CR-free, and the main env has no
  torch/peft/transformers/safetensors/huggingface_hub.
- CPU acceptance (`-I`):
  - It exited 0 twice with byte-identical receipts, and all **29** checks passed.
  - Max errors are unchanged: merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits
    3.35e-8/2.98e-8.
  - The frozen dispatched inventories are unchanged (merge `cc5fbab0…b54c`, materialize `e124a915…8682`, 14 target
    modules).
  - Receipt SHA256 `5cc245621ed3177723659929c760469224cb2439c142b9c366cabd8e004d63be`, source_sha256
    `0d4057330d93584d8b8d25e9d8a522e6b45f7e30f376b798948612d37537373a`, lock_sha256
    `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  - It supersedes `042bf8fe…9c0b`.
- Module sizes: `graph_store.py` 594 (389), `jobs.py` 561 (536), `attempt_runner.py` 438 (447),
  `operation_outputs.py` 414 (412), `graph_planner.py` 999 (990).

Not verified or still open:
- No GPU, SSH, network, provider or MLflow. Everything ran on fakes, local files and SQLite.
- Multi-process contention is untested. The races are in-process threads on one SQLite file.
- 8.2–8.5 scope is untouched: the resolver (D1), readiness, failure propagation, `requeue_retry`/backoff,
  `release_uncontacted` (D9), `finalize_run`, the tracking outbox, providers and the controller loop.
  - Nothing writes the new node columns or the run statuses COMPLETED/FAILED yet.
  - `record_outputs` still refuses `inspected_targets`.
- The controller (8.5) must catch `GRAPH_LAUNCH_REFUSED` from `run_claimed` and call `return_unlaunched`.
- Minor B (coordinator, for 8.2's safe release): a crash between `transition(CANCELED)` and `release_gpu` in
  `_launch` leaves a CANCELED attempt holding its lock. `reconcile` refuses it, because only FAILED/COMPLETED
  attempts are eligible.
- Owner fencing applies at run-lease replacement: `_controller` does not read the owner lease. An old owner's run
  lease stays valid until a new owner replaces it or it expires (plan design; 8.5 renews both).
- The live RunPod price observation (the second half of D13) is 8.4.
- The backfill assumes every graph manifest parses. `prepare_ready` is their only writer; a malformed one would
  stop the store from opening.
- Legacy `JobStore.claim` still counts graph attempts against `Policy.max_concurrent` (D2, unchanged).

## Task 8.2 readiness, outcomes, failure propagation and safe release, 2026-10-20

Commit `feat: resolve ready graph nodes from verified ledger evidence`, on `ac2209c`. It implements plan §4 "8.2"
(decisions D1–D14 as accepted), the coordinator's 7.5 re-review item Minor B, and the fixes from the independent
review of `ac2209c` (8.1): I1, M1, M4, M5 and M6.

What changed (`graph_store.py` unless noted):
- Trusted resolver: `LedgerBindingResolver(store)`, installed as `store.binding_resolver` (the controller in 8.5;
  tests directly). The store's default stays the root-only bindings, so Task 6/7 behaviour is unchanged.
  - `outputs`, `completed_nodes` and `evaluations` come from `ledger()`. Each evaluation carries its
    `NodeExecutionEvidence` as producer, acknowledged only when its tracking row is.
  - `producers`: the plan's artifacts plus `producer_evidence(current_lease(job), model)` for every completed and
    acknowledged train/merge/materialize node. Unacknowledged producers are left out, so they are never raised for
    an unrelated node.
  - `base_targets`: the plan's evidence plus every persisted `inspected_targets` entry, re-verified with
    `verify_inspected_targets` against the verified artifact on every read.
  - `failed_parent_node_ids`: the FAILED/BLOCKED/CANCELED evaluation parents of the selection being compiled (the
    node itself, or a selection parent whose receipt a child recomputes).
  - Destination (D1): a prepared node keeps its manifest's executor. Otherwise the resolver picks the approved
    alternative ordered by (blocked now on the claim resource, this run's unfinished jobs on the same exclusivity
    key, index). Aliases of one Pod or server therefore share load and lock. "Blocked now" uses
    `JobStore._resource_blocked` on the resource a claim would take (with the `simulation:` prefix for simulation
    plans).
- Readiness: `readiness(run_id)` is a pure read returning `Readiness(node_id, state, reason, failed_parents)` for
  every node.
  - `done`: the node already has a job or is finished.
  - `blocked`: a parent is FAILED, BLOCKED or CANCELED (`parent a failed`), a selection without `allow_partial`
    has a failed evaluation parent (`partial selection is not allowed`), or a model port is fed by a no-winner
    selection (`selection produced no winner`).
  - `waiting`: a parent is unfinished (`waiting for a`) or its attempt evidence is unacknowledged (`waiting for
    tracking of a`).
  - `ready`: otherwise. A partial selection is ready once every evaluation parent finished.
- `prepare_ready(lease, node_ids=None)` is unchanged without `node_ids`. With `node_ids` it compiles only the
  listed nodes that readiness finds ready now, skips the others untouched, and turns a compile `ValueError` into
  `fail_node` + `block_descendants` in one transaction. Unknown IDs raise `unknown graph node`.
- `fail_node` (an unprepared node, or one whose attempt is final with its lock released; idempotent) and
  `block_descendants` (a fixpoint over the descendants that readiness finds blocked; the reason goes to
  `nodes.error`).
- Outputs:
  - `record_outputs` verifies each `inspected_targets` entry of a train/merge/materialize model output with
    `verify_inspected_targets`; the verifier's messages pass through. It stores them sorted and deduplicated, so a
    re-record is idempotent. An evaluation with targets is refused (`evaluation cannot publish inspected targets`),
    and so is a selection with targets.
  - `NodeOutputs.selection`: a no-winner selection records a bare receipt that must equal
    `resolve_local_selection(spec)` with status `no_winner`. The payload gains a `selection` key only then, so
    every earlier receipt keeps its bytes. `ledger()` returns it.
  - `complete_local_selection(lease, claimed)`: one `BEGIN IMMEDIATE` validates both leases. Cancel wins over the
    join, for a run cancel or a job-level cancel: the job and node become CANCELED, the `local:` lock is released,
    nothing is recorded, and an uncounted `canceled` disposition is written. A pause returns the attempt unlaunched.
    Otherwise it writes PROVISIONING → RUNNING → EVALUATING → COMPLETED with one `events` row each, then
    `node_outputs`, the `tracking_outbox` row and the lock release. A rerun returns the recorded receipt.
- Dispositions and retries:
  - `classify_attempt(lease, job_id, kind)` writes one immutable `attempt_dispositions` row per attempt. The same
    kind is idempotent, another raises `attempt disposition is immutable`, and `uncertain` is refused. The kind
    must match the attempt: CANCELED for `canceled`, FAILED otherwise, `launch_started=0` exactly for
    `refused_before_launch`.
  - Counted and retryable: `remote_failed`. Within `max_attempts` it sets `retry_not_before = clock() + backoff`;
    otherwise the node FAILS and its subtree is BLOCKED.
  - Counted and terminal: `output_invalid`, `refused_before_launch` (the runner's own checks) and
    `failed_before_submit`. `canceled` is terminal and cancels the node. Counted attempts keep their reservation.
  - In a CANCELED run the node becomes CANCELED whatever the kind. A job-level cancel never retries.
  - `requeue_retry` needs a RUNNING run, a FAILED job without a lock or cancel request, a retryable disposition
    (`remote_failed`, or uncounted), a due `retry_not_before` on the injected clock and counted attempts below the
    limit. It moves the job to QUEUED (event `graph retry`, `active_attempt_id` cleared, so the old lease is
    fenced) and the node to READY.
  - `attempt_disposition(job_id)` is a read helper for 8.5's settle loop.
- Safe automatic release (D9): `release_uncontacted(lease, job_id)` acts only on a non-legacy PROVISIONING attempt
  of the run after `begin_launch`. It rotates the actor token, then checks `jobs/<job>/launches/<persisted
  launch_id>/remote-attempt.json` (and its `.tmp`, or a symlinked launch directory). If the record is absent, the
  job FAILS with `attempt stopped before remote contact; released after takeover`, the lock is freed, the
  reservation voided, an uncounted `released_before_contact` disposition written and the node made retryable at
  once. Otherwise only the token rotation remains.
- `recover_stale(lease, stale_before=)`: the run-scoped `JobStore.recover` for RUNNING/EVALUATING attempts of the
  run, with the same error text. Locks are kept.
- `finalize_run(lease)`: once every node is finished and no job of the run holds a lock, COMPLETED if every node
  completed, FAILED if any failed or is blocked, otherwise CANCELED. A CANCELED run stays CANCELED, and tracking
  never holds it back (D5). Idempotent.
- `set_wait_reason(lease, node_id, reason)` writes `nodes.wait_reason`.
- Cancel: `_state` cancel also cancels nodes that are waiting out a retry backoff.
- `attempt_runner.failure_kind(job_id)` reads the job, the attempt and the persisted
  `launches/<launch_id>.result.json` (re-read through `graph_store.accepted_result`, which requires the attempt's
  own launch ID).
- Minor B (7.5 re-review): a crash between `transition(CANCELED)` and `release_gpu` left the lock held.
  - `reconcile` now releases a locked CANCELED attempt only when its accepted result exists for its persisted
    launch ID, and returns a CANCELED outcome. Without it, `reconcile` returns None and the lock stays for the
    operator's `release-lock`.
  - `failure_kind` returns `canceled` for it, or `uncertain` without the proof.
  - `classify_attempt(…, 'canceled')` applies the same proof and releases the lock in its own transaction, so 8.5
    needs no executor to settle it.
- `graph_planner._compile_select` (deviation) no longer raises `insufficient partial success`;
  `resolve_local_selection` already applies that rule and records a no-winner receipt.

Fixes from the independent review of `ac2209c` (8.1):
- I1 (required): `return_unlaunched` dropped a job-level cancel. A PROVISIONING graph job with `cancel_requested=1`
  is now returned CANCELED, not requeued: the job and node become CANCELED (descendants BLOCKED in a running run),
  the token is rotated, the lock freed, the reservation voided and the disposition uncounted. `begin_launch` also
  refuses a graph-owned job with `cancel_requested=1` with the new `jobs.GRAPH_CANCEL_REFUSED = 'graph job cancel
  requested; launch refused'`, leaving `launch_started=0`; legacy jobs are unchanged. Tests:
  `test_return_unlaunched_honours_a_job_level_cancel` and `test_begin_launch_refuses_a_canceled_graph_job`.
- M1: the backfill is tolerant. A manifest it cannot price (no `resources`, or facts that fail today's schema)
  reserves its run's whole approved total, or infinity when the stored plan is unreadable. That blocks only that
  run's further GPU claims, and the node's `wait_reason` names it (`graph_store.UNPRICED_ATTEMPT`). The store still
  opens, and the run can still be paused and canceled. Test:
  `test_migration_reserves_an_unpriceable_attempt_without_failing_the_store` (two cases).
- M4: a FAILED attempt with `launch_started=0` that still holds its lock can only come from a stale `JobStore.recover`
  of an attempt whose `begin_launch` never committed: one the launch gate refused (it has no heartbeat before
  `begin_launch`), a crash before `begin_launch` followed by a global recover, or a recover racing the runner's
  pre-launch checks. All of them provably never ran (corrected in 8.3 after the 8.2 review, M-6). `failure_kind` reports
  `refused_before_launch`, as the plan's order says. `classify_attempt` makes it uncounted, voids its reservation,
  releases the lock and makes the node retryable at once: `begin_launch` never committed, and cannot commit now
  that the job is FAILED.
  - The runner's own pre-launch refusals (`abandon_unlaunched` releases the lock in the same transaction) stay
    counted and terminal, as D4 says for deterministic refusals. The distinguishing fact is structural (the lock is
    still held), not an error string.
  - 8.5 must classify such an attempt directly, not reconcile it first: reconcile's unstarted branch releases the
    lock, and the attempt would then count.
  - Tests: `test_an_unlaunched_attempt_failed_by_a_stale_recover_is_uncounted` and the runner case
    `gate_then_recover`.
- M5: `budget_reservations` and `attempt_dispositions` inserts name their columns. Downgrade is unsupported:
  pre-8.1 code cannot use a migrated DB, because its positional `INSERT INTO controller_leases VALUES (?,?,?)`
  fails against the added `owner_token` column.
- M6: the planner's `math.isfinite` check stays as defence in depth. `test_planning_refuses_a_zero_or_nonfinite_runpod_price`
  now asserts explicitly that pydantic refuses a non-finite price first (`ValidationError`, `Input should be a
  finite number`).

Deviations from the plan:
- `LedgerBindingResolver.__call__(plan, node_id, ledger, *, run_id)`: `bindings_for` passes `run_id`, because one plan
  can back several runs. It refuses a resolver bound to another store.
- `recover_stale` also leaves unstarted PROVISIONING attempts alone (the plan named only started ones). 8.5 returns
  them with `takeover_attempt` + `return_unlaunched`, so they stay uncounted (D4) instead of failing.
- `release_uncontacted` raises `current graph attempt required` for another run's job (like `takeover_attempt`)
  rather than returning False.
- Precedence in `failure_kind`: `launch_started=0` comes first, as in the plan. A FAILED attempt that holds its lock
  after `begin_launch` is `uncertain`. A CANCELED attempt is `canceled` once released or proven stopped.
- `finalize_run` also finalizes a PAUSED run whose nodes are all finished. A run with a canceled node and no failure
  ends CANCELED; the plan named only COMPLETED and FAILED.
- A `return_unlaunched` CANCELED in a running run (I1) and a job-level cancel of a local selection block the node's
  subtree.
- Additions: `attempt_disposition`, `accepted_result`, `REMOTE_ATTEMPT`, `RELEASED_BEFORE_CONTACT`,
  `GRAPH_CANCEL_REFUSED` and `UNPRICED_ATTEMPT`. `attempt_runner.REMOTE_ATTEMPT` and `FailureKind` now come from
  `graph_store`.
- `graph_planner.py` was edited (the `_compile_select` refusal above). Test 6's one-survivor case requires a recorded
  receipt.

Actual verification (main tree, no other agent's uncommitted changes):
- RED, before any `src` edit: 43 of the 43 new cases failed for the intended reasons:
  - 25 readiness cases on the missing `LedgerBindingResolver`, and the SSH alias case first on a fixture bug
    (`settings()` already passes `remote_root`), fixed before implementing;
  - 12 runner cases on the missing `failure_kind`, and the crash-release case on the missing `release_uncontacted`;
  - the two M1 cases on the missing `UNPRICED_ATTEMPT` (the migration aborted the open);
  - I1 on `QUEUED != CANCELED` (the requeue bug) and on the missing `GRAPH_CANCEL_REFUSED`.

  The M6 assertion passed already: it is a guard that pydantic refuses first. Three test bugs surfaced only once
  the code existed: a request ID that reused `'run'`, `validated_attempt`'s key (`active_attempt_id`), and a
  `FakeClock` advance that expired the 3600 s run lease. All three were fixed in the tests.
- Mutations, through pytest plugins in `%TEMP%` with no `src` edit:

  | Mutation | Tests that fail |
  |---|---|
  | readiness treats every parent as acknowledged | 3, 4 and 6 (partial) |
  | `release_uncontacted` never looks for the record | 12 (record present) |
  | `return_unlaunched` ignores `cancel_requested` (I1) | both I1 tests |
  | every attempt has an accepted result | the Minor B negatives (2) and two `failure_kind` cases |
- GREEN:
  - The plan's command (`test_graph_readiness`, `test_graph_store`, `test_operation_runner`, `test_graph_planner`)
    gave **181 passed** (138 at `ac2209c` + 43).
  - `uv run pytest -q` gave **1195 passed, 1 skipped** (1152 + 43).
- Ruff (`src tests`) is clean, and `check_secrets.py` reports 0 findings over 730 file versions. Both GPU locks are
  unchanged, every changed file is CR-free, and the main env imports no torch/peft/transformers/safetensors/
  huggingface_hub.
- CPU acceptance (`-I`), run once on the final source:
  - It exited 0, and all **29** checks passed.
  - Max errors are unchanged: merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits
    3.35e-8/2.98e-8.
  - Receipt SHA256 `26a68aee255e5581d3d53e9f146cdd1bf86e7414321d72ec4d130ffd8a33a117`, source_sha256
    `58adcdc807bdf93169d128f5c6915a81aec45ca88b6993af6992a4b23f7aec17` (the committed tree, checked on a
    `git archive` export), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  - It supersedes `5cc24562…63be`.
- Module sizes: `graph_store.py` 1229 (594), `attempt_runner.py` 475 (438), `jobs.py` 564 (561),
  `graph_planner.py` 998 (999).

Not verified or still open:
- No GPU, SSH, network, provider or MLflow. Everything ran on fakes, local files and SQLite, with in-process threads
  only.
- The controller loop is 8.5: settle, `classify_attempt`, `requeue_retry`, `release_uncontacted`, readiness
  scheduling and `finalize_run` are store primitives that nothing calls on a timer yet. Tracking events are 8.3:
  `record_outputs` and `complete_local_selection` still write the single pre-8.3 `tracking_outbox` row.
- For 8.5:
  - On `GRAPH_LAUNCH_REFUSED` or `GRAPH_CANCEL_REFUSED` from `run_claimed`, call `return_unlaunched` at once. An
    attempt left PROVISIONING has no heartbeat.
  - Settle in this order: `attempt_disposition` (already settled?), then `failure_kind`, then `uncertain` →
    `reconcile`, otherwise `classify_attempt`. Never reconcile a `refused_before_launch` attempt that still holds its
    lock (M4).
  - Stale unstarted PROVISIONING attempts: `takeover_attempt` + `return_unlaunched`. Started ones:
    `release_uncontacted`.
  - Use `prepare_ready(lease, node_ids=…)` only. The `None` path records errors on unready nodes.
  - ~~A select whose winner artifact fails verification stays PROVISIONING with its `local:` lock.~~ Superseded
    by 8.3 (8.2 review I-3): returning it would loop uncounted, so `complete_local_selection` now settles the
    refusal itself (counted, terminal `refused_before_launch`; a cancel still wins first).
    `complete_local_selection` raises before its transaction; the controller must fail or return it.
  - Install `LedgerBindingResolver` on the trusted path.
- M3 (review note for 8.5): owner fencing still applies only when a run lease is replaced. 8.5 should `LEFT JOIN
  controller_owner` in `_controller` and require `c.owner_token IS NULL OR (o.lease_token=c.owner_token AND
  o.expires>now)`, so that `release_owner` fences run leases at once (D14).
- M2 (review note): historical attempts can exhaust an old run's budget. 8.5 should set a `wait_reason` when budget
  refuses a claim. An unpriced backfilled attempt already names itself (`UNPRICED_ATTEMPT`).
- The simulated dispatcher (8.4) must write `remote-attempt.json`, or an equivalent record, before it acts. Otherwise
  `release_uncontacted` treats a simulated attempt as uncontacted.
- ~~The compiler accepts only one selection parent with failed evaluations per node. Two selection parents with
  failures would refuse at compile (`unrequested failed evaluation parent`) and fail the node.~~ Corrected by the
  8.2 review (I-1): this understated it. A node with two or more selection parents failed if **any one** of them had
  a failed evaluation, because every parent was recompiled with the merged list. Fixed in 8.3.
- A stale-recovered unlaunched attempt that a deterministic runner check would also refuse is retried once,
  uncounted. Its next refusal is counted and terminal, so retries stay bounded.
- Multi-process contention is untested.

## Task 8.3 tracking outbox and MLflow repair, 2026-10-20

Commit `feat: repair MLflow tracking through an idempotent outbox`, on `dfac977`. It implements plan §4 "8.3" and §6
(decisions D1–D14 as accepted; D5, D6, D7, D10 and D11 here) and folds in the fixes from the independent review of
`dfac977` (8.2), listed separately below.

What changed:
- New `tracking_outbox.py`:
  - `TrackingEvent` (kinds `graph_run`, `node_run`, `attempt_run`, `attempt_evidence`, `model_registration`,
    `graph_summary`). `event_id = sha256(canonical({kind, key}))`. Keys must match
    `[A-Za-z0-9][A-Za-z0-9_.:-]{0,249}`, so no quote can reach an MLflow filter string.
  - Additive table `tracking_events` plus the index `tracking_events_due`, exactly as in §6, created by the
    `GraphStore` migration. The pre-8.3 `tracking_outbox` table stays the per-attempt gate.
  - `enqueue_event(db, event, now)`: one row per key. Reusing a key with anything else different raises
    `tracking idempotency key reused with different payload`. A missing parent raises `tracking parent event is
    missing`.
  - `TrackingOutbox(store, *, clock, policy, claim_ttl_seconds=600)`:
    - `claim_pending(owner_lease)`: fenced by the owner lease and a claim token. A child is claimable only once
      its parent is ACKNOWLEDGED; a graph summary waits until no other event of its run is PENDING.
    - `record_intent`, `record_receipt`, `retry` and `defer`. Each refuses a token another owner has replaced
      (`stale tracking claim`).
    - `rearm` (FAILED/UNCERTAIN → PENDING, tries=0, intent kept), `backfill(owner_lease)` and
      `status(run_id)`.
  - `TrackingPublisher(outbox, store, tracker, *, local_sink)`, `LocalEvidenceSink(state_root)` and `redact`.
- Enqueue points, each inside the ledger transaction it describes (graph_store.py):

  | Method | Events |
  |---|---|
  | `submit` | `graph:{run}` |
  | `claim_ready` | `node:{run}:{node}` |
  | `record_outputs` | `attempt:{a}`, `evidence:{a}` and, for a real eligible train/merge/materialize model, `register:{artifact}:{inventory}:{a}` |
  | `complete_local_selection` | `attempt:{a}` and `evidence:{a}` (a selection registers nothing) |
  | `classify_attempt` | `attempt:{a}` and `evidence:{a}`, with the disposition and the redacted ledger error |
  | `finalize_run` | `graph-summary:{run}:{final}`, once |

  Each point enqueues its ancestors idempotently, with identical payloads, so a run submitted before 8.3 gains its
  graph and node events on its next claim or record.
- The gate (D5): acknowledging an `attempt_evidence` event sets `tracking_outbox.acknowledged=1` in the same
  transaction. `readiness`, `ledger()`, `compile_node` and `producer_evidence` are unchanged. Model registration
  never gates anything.
- Payloads hold identities, hashes and state-relative evidence paths only. They record the SHA256 of each file
  present at enqueue time:
  - `jobs/<job>/manifest.json`;
  - `command.json`, `model-inputs.json`, `binding-evidence.json`, `remote-result.json`, `remote-artifacts.json`,
    `train.log` and `worker.log` of the attempt's own launch;
  - `launches/<launch_id>.result.json`;
  - the content addresses of `descriptor.json`/`inventory.json`;
  - the hash of the `node_outputs` row.

  At send time every byte is re-read and re-hashed. A mismatch, a symlink or a path outside the state directory
  fails the event permanently with `local evidence changed after enqueue`, before any client call for that event.
  `remote-attempt.json`, dataset files and benchmark bytes are never uploaded.
- MLflow mapping (one tracker per shared auth scope):
  - One graph run, carrying `lineage/plan.json`, `lineage/approval.json` and, at the end, `summary.json`.
  - Node runs under it, attempt runs under their node (`mlflow.parentRunId`).
  - The attempt evidence uploads under `lineage/`: the files above, plus `node-outputs.json`, `evaluation.json` (the
    `EvaluationRecord`), `selection.json` and `error.txt`.
  - Finite evaluation metrics at step 0, with the attempt's `created` time as timestamp, so a retry repeats
    identical points.
  - Identical params on every retry. The attempt run is then terminated FINISHED/FAILED/KILLED.
  - Tags: the §6 `factory.*` set and `mlflow.parentRunId`. `factory.operator` comes from `FACTORY_OPERATOR`, as in
    v1.
  - Registration uploads the verified `files/` to `model/` and creates a version of
    `factory-{project_id}-{node_id}` (source `runs:/<attempt run>/model`). Its tags are the registration key, the
    artifact, inventory, producer job/attempt, manifest and plan hashes, `base_model`, `model_revision`,
    `quality_qualified=false` and `approval=pending`.
  - The summary terminates the node runs and the graph run with the final statuses.
- Adoption after a lost receipt:
  - Every create (`create_run`, `create_model_version`) records a durable intent first.
  - With an intent present, the next try searches first. Runs are found by `tags.\`factory.tracking_key\``, and
    versions by `name` + `run_id` over every page, with the key tag checked client-side.
  - Duplicates are adopted earliest-first or lowest-first and listed in the receipt; nothing is ever deleted.
  - A failing search raises an uncertain retry and never creates. An authoritative empty search creates once.
  - The registered model's name is its own key: a failed create is re-searched.
- Bounded retry: `OutboxRetryPolicy(max_tries=8, base_seconds=30, factor=2, cap_seconds=3600)`.
  - The delays are 30, 60, 120, 240, 480, 960, 1920 and 3600 s on the injected clock.
  - Exhausted definite errors end FAILED, exhausted uncertainty ends UNCERTAIN. Our own refusals (an insecure
    artifact root, changed evidence) fail at once.
  - Retries never touch `jobs`, attempts, locks, reservations, dispositions or nodes.
- HTTPS (Task 6B rules, through the `auth.py` helpers):
  - `create_keyed_run` repeats `start`'s per-run artifact-root check (an insecure root is terminated FAILED and
    refused before any upload).
  - `check_run_artifacts` applies it to adopted runs and before every upload into an existing run, so credentials
    configured later never reach an HTTP root.
  - The constructor refusals are unchanged.
- Destination pinning (D11):
  - `tracking.tracking_settings_sha256(public)` covers exactly `tracking_uri`, `experiment`, `artifact_location` and
    `auth_mode` (`oidc`/`token`/`none`, from `current_auth_mode()` on top of `credentials_configured`). It refuses
    extra keys and userinfo in URIs.
  - `MLflowTracker.public_settings()` returns these four values. `LazyMLflowTracker.public_settings()` answers
    without building a client.
  - A real run whose pinned hash differs stays PENDING with `tracking destination differs from the approved plan;
    restart the controller with the approved MLflow settings`. That costs no client call and no try.
- Simulation runs (D7) never touch the tracker. They write immutable receipts at
  `state/tracking/<run_id>/<event_id>.json` and open the gate the same way.
- v1 (D10): `register_candidate(..., attempt_id=)` searches first with the key `v1:{manifest sha256}:{attempt_id}`
  and adopts the lowest version. `runner.run_claimed` and `Factory.reconcile` pass the attempt. A v1 tracking failure
  (including a failing search) is still FAILED with `tracking-error.txt`.
- Secrets:
  - Stored and shown errors pass through `redact`. It removes the configured `MLFLOW_TRACKING_TOKEN`,
    `MLFLOW_OIDC_CLIENT_SECRET` and `MLFLOW_TRACKING_PASSWORD` values, bearer/basic credentials, `secret=`-style
    pairs and URL userinfo.
  - Payloads, receipts, tags, params and `status()` never carry a credential.

8.2 review fixes (from the independent review of `dfac977`):
- I-1 (required): a node with several selection parents FAILED when any one of them had a failed evaluation.
  - `compile_node` now checks the failures against the union of its selection parents' evaluations.
  - It passes each parent's `_compile_select` only that parent's share (`graph_planner.py`).
  - The PROGRESS 8.2 line that understated the issue is corrected.
  - Test: `test_a_child_of_two_selections_gets_each_selections_own_failed_evaluations`.
- I-2 (required): `JobStore.cancel` of a QUEUED graph job (freshly prepared, requeued, or returned unlaunched) left
  its node READY forever.
  - New `GraphStore.settle_canceled_jobs(controller_lease)`: such a node becomes CANCELED and its subtree is BLOCKED
    unless the run is canceled (the 8.1 I1 rule).
  - `finalize_run` and the gated `prepare_ready(node_ids=…)` apply it too.
  - Test: `test_a_graph_job_canceled_while_queued_settles_its_node[prepared|requeued]`.
- I-3 (required): a local selection whose receipt fails verification looped forever, uncounted.
  - `complete_local_selection` now settles the refusal in its own transaction. A cancel still wins first.
  - Otherwise the job FAILS, the lock is released, and a counted, terminal `refused_before_launch` disposition is
    written (chosen over `output_invalid` because a local selection never launches; it matches `failure_kind` and
    `classify_attempt`'s `launch_started=0` rule). The node is FAILED and its subtree BLOCKED, and the failed
    attempt's evidence is enqueued.
  - The 8.2 hand-off to 8.5 is corrected.
  - Test: `test_a_local_selection_refused_at_verification_is_settled_in_its_transaction[False|True]`.
- M-1: both lock-releasing branches of `classify_attempt` (the M4 stale-recover release and the Minor B canceled
  release) rotate the attempt token in that transaction. Test: `test_the_stale_recover_release_fences_the_old_attempt_lease`.
- M-3: a no-winner selection blocks its model children in its own transaction, so `finalize_run` no longer stalls.
  Two 8.2 assertions changed accordingly: `test_no_winner_selection_is_recorded_and_blocks_model_children` (also
  checks `finalize_run` → FAILED) and `test_partial_join_selects_from_surviving_evaluations[one_survivor]`.
- M-4: an existing disposition now wins in both places.
  - `failure_kind` answers from the disposition when there is one: a classified kind as recorded,
    `released_before_contact` as `failed_before_submit`, and `returned_unlaunched` as `canceled`.
  - `classify_attempt` treats those equivalents as no-ops and returns the existing disposition.
  - Test: `test_failure_kind_and_classification_honour_an_existing_settlement`.
- M-5: both `tracking_outbox` inserts name their columns. Test: `test_tracking_outbox_rows_are_inserted_by_column_name`.
- M-6:
  - A test now covers the M1 infinity branch (unreadable plan):
    `test_migration_reserves_infinity_when_even_the_plan_is_unreadable`.
  - `UNPRICED_ATTEMPT` is cleared from `wait_reason` once the node's attempt settles (`_settle_node`,
    `record_outputs`, `_return_unlaunched`). Test: `test_settling_an_unpriced_attempt_clears_its_wait_reason`.
  - The 8.2 M4 rationale is broadened: a crash before `begin_launch` followed by a global recover, or a recover
    racing the pre-launch checks, gives the same shape, and those attempts also never ran.
- M-2 (note for 8.5) is not done; see below.

Deviations from the plan:
- `log_lineage` takes an extra `timestamp_ms` keyword, which carries the payload's timestamp.
- `TrackingOutbox` gains `claim_ttl_seconds` and a public `defer`. `GraphStore` gains `validate_owner(db, owner)`, so
  the outbox uses no private store member.
- A destination mismatch stays PENDING (test 15 and §7) instead of failing permanently, as one §6 sentence lists it.
- `max_tries=8` means eight retries after the first send (§6's eight delays). An exhausted event shows `tries=9`.
- `finalize_run` enqueues the `graph_summary` event. §6's table lists it; §4's file list does not.
- Ancestors are enqueued idempotently at every enqueue point, so `backfill` covers only unacknowledged
  `tracking_outbox` rows (plus their ancestors).
- `error.txt` comes from the ledger's error (redacted) in the payload, not from `jobs/<job>/error.txt`, which is per
  job and overwritten by a retry.
- Attempts that the store settled as never run (`returned_unlaunched`, `released_before_contact`, a canceled local
  selection) get no MLflow attempt run.
- A selection's attempt run uploads `selection.json` and `node-outputs.json`, but not the winner's descriptor (it is
  another node's artifact).
- New helpers in `tracking.py`: `current_auth_mode`, `public_tracking_settings`, and the stored
  `uri`/`experiment`/`artifact_location`/`auth_mode`. `ensure_registered_model` adopts by name after a failed create.
  v1 `register_candidate` uses it, which is within D10.
- The v1 key falls back to `run-{run_id}` when no attempt is passed.
- Files edited beyond the 8.3 list: `graph_planner.py` (I-1) and `attempt_runner.py` (M-4), both for the 8.2 review.
  Neither is an 8.4 file.
- Test placement: the 20 plan tests are in `tests/test_tracking_outbox.py`. The review-fix tests are in
  `tests/test_graph_readiness.py` (7 cases) and `tests/test_graph_store.py` (3).

Actual verification (main tree; no other agent's uncommitted changes):
- RED:
  - All 20 tests in `test_tracking_outbox.py` failed on the missing module/API before any `src` edit.
  - With only the two new `tracking` names stubbed by a temporary plugin, tests 19 and 20 failed for the behavioural
    reason: v1 created a second version after the lost receipt, and v1 never searched.
  - The review-fix tests were written after their fixes, so they were RED-checked against a `git archive HEAD`
    export with the new test files copied in. All 11 changed or new cases failed for the intended reasons; the
    infinity-branch test passed there, as expected (it guards existing behaviour).
  - Test-side fixes before GREEN: an owner lease not released in test 1, the event count of test 4 (8, not 10), a
    missing `prepare` and run-lease renewal in test 17, and a one-node world asserting on `b` in test 18.
- Mutations, as pytest plugins in `%TEMP%` with no `src` edit:

  | Mutation | Tests that fail |
  |---|---|
  | `redact` returns its input | 17 (after test 17 was strengthened to snapshot every pass) |
  | a failing search counts as empty | 8 |
  | no destination check | 15 |
  | evidence bytes not re-hashed | 18 |
  | intent ignored (always create) | 6, 11 and 14 |
  | `record_receipt` leaves the gate closed | 1, 4, 5, 12, 16 and 17 |
- GREEN:
  - The plan's command (`test_tracking_outbox`, `test_evaluation_tracking`, `test_tracking_https`,
    `test_runner_cli`, `test_reconciliation`, `test_graph_store`) gave **82 passed**.
  - `uv run pytest -q` gave **1225 passed, 1 skipped** (1195 + 30 new) on the final tree. An earlier full run,
    while mutation runs loaded the machine, had one failure: the flake below.
  - `tests/test_jobs.py::test_recovery_does_not_release_unknown_remote_execution` is a pre-existing Windows
    clock-resolution flake: `recover(stale_after_seconds=0)` needs `updated < time.time()`. It failed once in a
    loaded full run and once in five isolated runs, with `jobs.py` and `test_jobs.py` untouched. It is not fixed
    here.
- Ruff (`src tests`) is clean, and `check_secrets.py` reports 0 findings. Both GPU locks are unchanged, and every
  changed file is CR-free.
  - The main env imports no torch/peft/transformers/safetensors/huggingface_hub, and importing `graph_store` does
    not import mlflow. httpx was already imported at HEAD.
  - `mlflow-skinny` 3.17.1's own parsers accept both filter strings offline (`SearchUtils`,
    `SearchModelVersionUtils`).
- CPU acceptance (`-I`), run once on the final source:
  - It exited 0, and all **29** checks passed.
  - Receipt SHA256 `1ddbabdc717a7a8fbbc2c06f15d9ad941a97133af1f358bce29bffdab73f6ceb`, source_sha256 `eed2680765e1cb5fe85c7fce7c025c29107c775eb9ca5ade8e08dc7734cafc54` (equal to a `git archive` export of the commit),
    lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  - It supersedes `26a68aee…a117`.
- Module sizes: new `tracking_outbox.py` 727, `tracking.py` 305 (97), `graph_store.py` 1359 (1229),
  `graph_planner.py` 1010 (998), `attempt_runner.py` 495 (475).

Not verified or still open:
- **No real MLflow server was used, and its version is unknown.** Everything ran on `FakeMlflowClient` behind the real
  `MLflowTracker`. Still unverified against a real server:
  - tag-filter and pagination behaviour, `search_model_versions` with `run_id`, and `runs:/…/model` sources;
  - artifact proxying and real upload times.

  The design turns any search error into "wait", never "create".
- The OIDC refresher was not exercised (it is patched in tests). Eventually consistent registries are untested;
  duplicates would be adopted lowest-first.
- Multi-process contention is untested; the races are in-process on one SQLite file.
- For 8.5:
  - Run `backfill(owner)` once after acquiring the owner, and `TrackingPublisher.process(owner)` in the tick.
  - Uploads run inside `process`: renew the owner lease during long model uploads, and keep the claim TTL (600 s
    default) above the longest single upload.
  - Use `TrackingOutbox.status(run_id)` for `tracking_status` and `registration_status`.
  - Call `settle_canceled_jobs` in the settle phase (gated `prepare_ready` and `finalize_run` apply it too).
  - `failure_kind` now honours dispositions.
- Task 9 must build `TrackingDestination.public_settings_sha256` with `tracking_settings_sha256` over
  `public_tracking_settings(...)` (D11); otherwise real runs wait with the destination-mismatch reason.
- M-2 (8.2 review, for 8.5): the resolver eagerly verifies every completed producer, persisted target and active
  job's facts. One tampered or corrupt record raises `ValueError`, and the gated `prepare_ready` then FAILS every
  ready node. 8.5 should separate integrity errors (a run-level `wait_reason` or pause, failing nothing) from
  node-specific compile refusals. Not done here.

## Task 8.4 executor provider and simulated dispatcher, 2026-10-20

Commit `feat: resolve approved destinations and simulate dry-run graph operations`, written on `dfac977` in the
worktree `codex/task-8.4` in parallel with 8.3, then rebased onto 8.3 (`c7f8683`). It implements plan §4 "8.4" and §5
(Q21), decisions D11 and D13, and the coordinator's two points from the `dfac977` review (D9 versus simulation;
refusals release their lock).

What changed:
- `executor_profiles.py` (providers; `desktop_runtime` is imported only inside provider methods):
  - `ProfileSecrets(public, settings)`: the settings are a read-only in-memory copy, and the repr and str name only
    `profile_id@revision`. `ResolvedDestination(profile, observation, executor)` and the `ExecutorProvider` protocol.
  - `SettingsExecutorProvider(profiles, state_root, *, clock)`:
    - `resolve` checks before any connection that the profile exists (`PROFILE_MISSING`), that it is still the
      approved revision and body (`PROFILE_CHANGED`, via `approval_destination`), and that its transport is unchanged
      (`TRANSPORT_CHANGED`: RunPod `runpod_transport_sha256`; SSH transport and declared cost).
    - It then opens `executor_for(..., profile_identity=…)` (SSH) and requires the observed Pod (`POD_DIFFERS`, then
      `runpod_facts`) or SSH server (`SERVER_DIFFERS`, then `server_profile == pinned`) to be the pinned one.
    - `recover`: SSH through a `ServerApprovalReceipt` created in memory only, never in `job_destinations`, and
      tolerant of unrelated inventory drift; RunPod with `preflight=False` on the pinned Pod.
    - `status()` returns public fields only.
  - `SimulatedExecutorProvider(observations, *, clock)` and `.for_plan(plan)`: the plan's own pinned observations,
    stamped with the clock. It contacts nothing, never imports `desktop_runtime`, and its executor is always None.
  - `runpod_facts(pod, *, observed_at)` (D13): `gpu_type` is RunPod's GPU type ID (`gpu.id`), the count is a strict
    int from 1 to 8, an explicit timezone is required, and a price ≤ 0, non-finite, missing, boolean or non-numeric
    is refused with `positive finite live RunPod API price required`.
  - `runpod_transport_sha256(settings)` (D11): provider, Pod ID, SSH fingerprint and a hash of the resolved key path.
    It never covers the API key. Malformed settings raise fixed texts.
- `desktop_runtime.executor_for(..., profile_identity=None)`: an SSH launch executor's profile carries the pinned
  `profile_id`/`revision`/`name`/`credential_scope`. Without the keyword nothing changes. A partial identity, an
  identity together with a server receipt, or an identity for a RunPod destination is refused before any contact.
- New `simulated_dispatch.py`:
  - `SimulatedOperationDispatcher(graph_store, *, root, roots=None)` has the `OperationAttemptRunner` protocol.
  - Every refusal before launch goes through `abandon_unlaunched`, which releases the lock in the same
    transaction. That covers `SIMULATION_ONLY`, `NO_EXECUTOR`, approval and destination checks, a lease other than
    `simulation:<pinned key>`, dataset registrations, the launch ID, `ROOT_SNAPSHOT_MISSING` and `TARGETS_REQUIRED`.
    `failure_kind` reports these refusals as `refused_before_launch`, counted and terminal.
  - Launch order: `begin_launch` (D12 gate), the runner's preparation (`manifest.json`, engine files or benchmark
    bytes, the real `stage_model_inputs`, `command.json`), `transition(RUNNING)`, the local record, and only then
    `SimulatedWorker.execute`. The result is published exclusively as `<launch_id>.result.json`, then RUNNING →
    EVALUATING → the real `freeze_operation_output`/`evaluation_record` → COMPLETED → `release_gpu`.
  - `SimulatedWorker` uses no ledger and writes deterministic bytes: zero-valued F32 tensors, streamed through a
    stdlib safetensors writer, and canonical JSON. It produces the plan §5 layout for LoRA/QLoRA, full/freeze, rm/ppo
    value heads, merge (rank Σr, `MergeReceipt`), materialize (`MaterializeReceipt`, `target-inventory.json`) and
    evaluate (empty predictions). It adds a `simulated.json` marker, and the evidence names every file.
  - `DirectoryRootSnapshots(directory)` serves `<owner>/<name>/<revision>/`. The dispatcher re-verifies whatever any
    `RootSnapshotSource` returns.
  - Every descriptor and evaluation record is `simulation=True`, so `select_best` excludes it with `simulation`.

Decision: simulated attempts versus `release_uncontacted` (D9).
- The dispatcher writes a documented local record, `launches/<launch_id>/remote-attempt.json` =
  `{"schema_version": 1, "simulated": true, "contact": "none", attempt_id, launch_id, manifest_sha256}`. It has no
  `remote_path`, so no real-runner consumer can act on it. It is written after `transition(RUNNING)` and before the
  first effect, as the real executor does (plan F8).
- PROVISIONING after `begin_launch`: nothing has run and no record exists. `release_uncontacted` therefore releases
  it truthfully (uncounted and retryable), and the fenced actor's `transition(RUNNING)` fails before any effect. 8.5
  can treat simulation runs like real ones here.
- RUNNING/EVALUATING after `recover_stale` (FAILED, lock held): `failure_kind` reports `uncertain`, and `reconcile`
  settles from the published result. Without a result, it first publishes a FAILED result exclusively
  (`SIMULATION_STOPPED`, then `remote_failed`, retryable within `max_attempts`). A suspended original actor can then
  publish nothing (hard-link create never replaces), and it can no longer transition a FAILED job, so it freezes
  nothing. Nothing is re-run.
- COMPLETED or CANCELED holding the lock: handled as by the runner (rebuild and release, or release on an accepted
  result).

Deviations from the plan:
- Interfaces:
  - `SimulatedExecutorProvider(observations=(), *, clock)` instead of `(*, clock)`. A pinned profile alone cannot
    yield facts that fit; 8.5 uses `.for_plan(plan)`.
  - Added `SERVER_DIFFERS`, `RUNPOD_PRICE_REQUIRED`, provider `status()` (plan test 4 names it) and an optional
    `runtime_profile` on `recover`. `PROFILE_MISSING` doubles as the simulated provider's unknown-destination
    refusal.
  - The SSH pre-connection check also compares the declared hourly cost with the pinned profile
    (`TRANSPORT_CHANGED`).
- Simulated dispatcher:
  - On the rebase onto 8.3, the simulated `failure_kind` adopted 8.3's M-4 rule as the runner did: an existing
    disposition answers first (`released_before_contact` as `failed_before_submit`, `returned_unlaunched` as
    `canceled` or `refused_before_launch`).
  - `simulated_dispatch.OperationOutcome` copies `attempt_runner.OperationOutcome` field for field (a test pins the
    fields), because `attempt_runner` imports paramiko through `execution`. 8.5 must treat outcomes structurally, or
    move the type to a light module.
  - It does not require dataset export permission (simulated data never leaves the PC); unchanged registered bytes
    are still checked.
  - `graph_store`/`jobs` are imported inside the dispatcher only. `SimulatedWorker` and the module import neither.
  - The coordinator's local record (above).
  - `engine_version` and the receipt `device` are `"simulated"`.
- Tests: test 6 is in `tests/test_desktop_runtime.py`, beside test 5. Additions: the transport hash, refusal of an
  executor or foreign approval, full training with verified targets, untrusted snapshot sources, crashes before
  RUNNING, during RUNNING and of a suspended actor, and the `OperationOutcome` field parity. `test_simulated_dispatch`
  imports only light modules, so its chain also runs in a subprocess.
- The chain of test 10 is train (HF root) → continue → merge → materialize → evaluate → select. The merge takes the
  real adapters `first`/`second`, after `continue` through an explicit `depends_on`, because compile refuses simulated
  merge inputs (`_merge_descriptors` is real-only). The plan adds evidence for the HF root, as Task 9's inspector
  would supply.

Actual verification (worktree `D:\llm-tuning-factory-wt84`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree). RED, mutations and the first GREEN ran on `dfac977`; the final
GREEN, Ruff, secrets and CPU acceptance ran on the tree rebased onto 8.3:
- RED, before any `src` edit: all 58 new cases failed for the intended reasons:
  - 56 on the missing provider names or the missing `simulated_dispatch` module (through `api()`, not collection);
  - one on `executor_for() got an unexpected keyword argument 'profile_identity'`;
  - the subprocess test on `ModuleNotFoundError`.
- Mutations, through a pytest plugin in `%TEMP%` with no `src` edit:

  | Mutation | Tests that fail |
  |---|---|
  | dispatcher skips the simulation check | refuses real manifests |
  | the result record overwrites instead of exclusive create | suspended actor |
  | reconcile publishes no FAILED result | crash while running, suspended actor |
  | the dispatcher trusts the snapshot source | HF root `untrusted_source` |
  | the worker runs before `transition(RUNNING)` | 11 cases, including crash before running |
  | the simulated provider ignores the revision | provider contacts nothing |
  | `lora_target: all` without evidence uses placeholders | targets refused (`all_targets_on_root`) |
  | the settings provider skips the transport check | six transport cases and secrets in memory |
  | `ProfileSecrets.__repr__` shows settings | secrets in memory |

  `require_real=True` in the simulated merge survives: it is equivalent, since compile already requires real merge
  inputs.
- GREEN:
  - The plan's command (`test_simulated_dispatch`, `test_executor_profiles`, `test_desktop_runtime`,
    `test_operation_runner`, `test_model_inputs`) gave **220 passed**, on `dfac977` and after the rebase.
  - The full suite gave **1253 passed, 1 skipped** (1195 + 58) on `dfac977`, and **1283 passed, 1 skipped** (8.3's
    1225 + 58) on the rebased tree.
  - One rerun on `dfac977`, made while the CPU acceptance ran in parallel, failed the pre-existing
    `tests/test_jobs.py::test_recovery_does_not_release_unknown_remote_execution`; `jobs.py` and `test_jobs.py` are
    unchanged from `dfac977`. It is a timing flake: `JobStore.recover(stale_after_seconds=0)` needs
    `updated < time.time()`, and two consecutive Windows `time.time()` reads can be equal. In isolation it failed 2 of
    5 runs, and an unloaded rerun of the suite passed. Not fixed here.
- Ruff (`src tests`) is clean, and `check_secrets.py` reports 0 findings over 759 file versions (rebased tree). It
  first flagged the synthetic RunPod-shaped test key, which is now built at runtime. Both GPU locks are unchanged,
  every changed file is CR-free, and the subprocess checks import no paramiko, `desktop_runtime`, `execution`,
  `attempt_runner`, mlflow, torch, transformers, peft, safetensors or huggingface_hub.
- CPU acceptance (`-I`, the worktree's script and source):
  - On `dfac977` it exited 0 twice with byte-identical receipts (`f7e209dd…4b32`, source `ea6ed0bb…d866`).
  - On the rebased tree it exited 0, and all **29** checks passed. The source hash equals `source_hash()` of the
    committed `src`.
  - Max errors are unchanged: merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits
    3.35e-8/2.98e-8.
  - Receipt SHA256 `2afbd0b8d548715892fff15361bfac67ed563c87e91eb6d979ad8da2874088f8`, source_sha256
    `9cd9ca7eec4a61ba4db68833f46dc0259e1d2c9b4fea31249de3515a51066ba0`, lock_sha256
    `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  - It supersedes 8.3's `1ddbabdc…6ceb`.

Not verified or still open:
- No live RunPod API, SSH server, GPU, network, model download or MLflow was contacted. The RunPod path ran on a
  fake `connection()`, and the SSH path on `test_gpu_servers.FakeServer` (paramiko-shaped). The real
  `RunPodController.status` shape and `gpu.id` values are assumptions until Task 9 observes a live Pod.
- Nothing produces planning-time `ResourceFacts` from `runpod_facts`, or `transport_settings_sha256` from
  `runpod_transport_sha256`, yet (Task 9). Planned facts must use the same `gpu.id` field.
- The controller loop is 8.5: provider selection by plan, the dispatch threads, settle and reconcile calls.
- A simulated full or materialized model of a real-size base writes its whole F32 linear inventory as zeros. It is
  streamed, but it can be many GB on disk.
- The exclusive result publish relies on hard links (NTFS and ext4 support them). A filesystem without them fails
  the attempt.
- Multi-process contention is untested.

## Task 8.5 graph controller and end-to-end recovery / Task 8 complete (software), 2026-10-20

Commit `feat: automate graph scheduling and repair tracking without retraining`, on `f75e628` (the Task 9 plan, docs
only, on `73e1bd7`). It implements plan §4 "8.5" (decisions D1–D14 as accepted), resolves every hand-off note of the
8.1–8.4 entries, and folds in the fixes from the independent reviews of `c7f8683` (8.3) and `73e1bd7` (8.4), listed
separately below. It is the last sub-task of Task 8.

What changed:
- New `graph_controller.py`:
  - `GraphController(store, executor_provider, operation_dispatcher, tracker, clock, *, simulated_provider, outbox,
    pool, settings)` with `acquire_controller(owner_id)`, `tick(owner_lease) -> TickReport`, `run_forever(stop_event)`
    and `status()`. The constructor installs `LedgerBindingResolver` as `store.binding_resolver`.
  - `ControllerSettings`: `lease_ttl_seconds=60`, `tick_seconds=5`, `stale_attempt_seconds=120`,
    `reconcile_interval_seconds=60`, `max_dispatch_threads=8`, plus `lease_heartbeat_seconds=20`,
    `tracking_claim_ttl_seconds=600` and `tracking_batch=16`. The heartbeat must be at most half the lease TTL and a
    third of the claim TTL.
  - `DispatchRouter(real, simulated)` routes by the manifest's `lineage.simulation`. `ThreadDispatchPool` runs one
    thread per in-flight attempt. `controller_tracker(uri)` is the lazy tracker Task 9's broker hands the controller.
  - `graph_status(store, run_id=None, *, now)`: DB-only and secret-free (below).
- `acquire_controller` takes the owner lease (`controller already running` for a duplicate) and runs the outbox
  `backfill` once.
- Each tick, the DB being the source of truth:
  1. Leases: `renew_owner` (a refusal stops the controller), then one run lease per managed run, renewed every tick.
     Managed: RUNNING or PAUSED, an unfinished node, a held lock, a PENDING tracking event, or tracking events but no
     graph summary yet (a crash between the last settle and `finalize_run`). A run that predates the outbox and is
     finished is left alone; an unexpired owner-less (pre-Task-8) run lease is skipped until it expires.
  2. Harvest: a dispatch thread that returned frees its resource. Its outcome is cached; a refused launch is returned
     unlaunched (normally the thread already did that); any other exception becomes the node's wait reason.
  3. Settle every job not in flight in this process, in the table's order, re-reading the row after each step:
     - stale RUNNING/EVALUATING: the run-scoped `recover_stale`, with a threshold that never reaches an attempt in flight
       here;
     - stale PROVISIONING, unstarted: `takeover_attempt`, then `complete_local_selection` (select), the same attempt's
       dispatch (it launches once), or `return_unlaunched` (paused, canceled or stopping);
     - stale PROVISIONING, started: `release_uncontacted` (D9). When it cannot prove non-contact, the actor is already
       fenced and the attempt becomes FAILED with its lock kept (`recover_stale` skips PROVISIONING);
     - FAILED/CANCELED holding the lock: `failure_kind` first. `refused_before_launch` (M4) and a proven `canceled`
       (7.5 Minor B) are classified, uncounted or released by the store. Otherwise, every
       `reconcile_interval_seconds`: `takeover_attempt`, then `provider.recover(...)`, then `dispatcher.reconcile`.
       None keeps the lock and sets the wait reason (`operator confirmation required: release-lock
       --confirm-execution-stopped` without a remote record; "waiting for the result of the same launch; …" with one).
       A defensive `attempt is final` refusal is settled as final;
     - COMPLETED holding the lock: `takeover_attempt` and `reconcile(job, None)` (rebuild and release; no contact);
     - COMPLETED without outputs: the cached outcome, else `outputs_for`, recorded under a takeover lease with the
       outcome's `inspected_targets`. A refusal becomes an operator wait reason;
     - FAILED/CANCELED without a disposition: `classify_attempt(failure_kind)`; a due retry: `requeue_retry`, otherwise
       the backoff is the wait reason (`attempt n of m`);
     - `settle_canceled_jobs` first.

     One job's error is shown on its node and never stops the others.
  4. Prepare (RUNNING): `readiness`, then the gated `prepare_ready(node_ids=ready)`. Waiting nodes get their readiness
     reason (with the parent's tracking failure and its action when it has one); blocked ones are blocked.
  5. Local selections (RUNNING, not stopping): `claim_ready(None)` then `complete_local_selection`.
  6. GPU claims (RUNNING, not stopping): per queued job, its pinned destination unless the run is at
     `max_concurrent`, every dispatch thread is busy, the resource is busy in this process (D8) or in the DB, or the
     budget would be exceeded. Otherwise `provider.resolve(...)` (one `SimulatedExecutorProvider.for_plan` per
     simulation run), `claim_ready(observation)` and a dispatch thread that runs `run_claimed` once and closes the
     destination context. A refusal, a price above the pinned one, or a busy resource consumes no attempt and becomes
     the wait reason. A claimed job of another runtime than the resolved one is returned unlaunched.
  7. Tracking: `TrackingPublisher.process(owner)` inline, or on `run_forever`'s tracking thread.
  8. `finalize_run`, which enqueues the graph summary.
- A heartbeat thread runs during every tick and every tracking pass. Every `lease_heartbeat_seconds` (wall time) it
  renews the owner lease, the run leases and the tracking claim being sent, so a long upload, create or SSH connect never
  lets them lapse.
- `run_forever(stop_event)`: the tick loop, with tracking on its own thread (never concurrent with itself). After the
  stop event it skips phases 5–6, keeps settling until no dispatch thread is left (woken by each thread's
  completion), lets the in-flight publish finish, then calls `release_owner` (D14). A fenced owner stops at once and
  releases nothing.
- `graph_status` (Task 9 `graph_run_get`/`controller_status`, Task 10 polling):
  - `controller`: `owner_id`, `state` (`running` while the heartbeat is fresh, `stale` once it expired, `absent` with
    no owner or after a clean release) and `heartbeat_age_seconds`.
  - `runs[]`: `run_id`, `status`, `simulation`, `plan_sha256`, `approved_cost_usd`, `reserved_cost_usd`,
    `budget_exhausted`, `unpriced_attempts`, `created`, `tracking` (event counts, graph status, action) and `nodes[]`.
  - Each node: `node_id`, `label`, `kind`, `status`, `job_status`, `attempt_id`, `attempts`, `counted_attempts`,
    `max_attempts`, `resource_id`, `destination_profile_id`, `uncertain`, `needs_operator`, `wait_reason` (unfinished
    nodes only), `error`, `retry_at`, `tracking_status`, `registration_status`, `tracking_action`, `output`, `selection`
    (winner artifact or `no_winner`) and `cache_seed` (`published`, `failed` or `missing`).
  - Errors and wait reasons pass through `redact` and lose remote POSIX paths. `status()` adds the in-process facts:
    in-flight jobs, busy resources, stopping, skipped runs, recent errors, backfill skips and the provider's public
    status.

Hand-off notes and how each was resolved:
1. Settle order: `takeover_attempt` comes before every `reconcile`, and `reconcile` is called only for a lock-holding
   attempt (FAILED/CANCELED after `failure_kind`, COMPLETED with `executor=None`). A completed final attempt goes to
   `outputs_for`. Tests 11 and 12.
2. Refused launches: the dispatch thread returns the attempt (`return_unlaunched`) as soon as `run_claimed` raises
   `GRAPH_LAUNCH_REFUSED` or `GRAPH_CANCEL_REFUSED`; harvest repeats it if the thread could not.
   `test_refused_launches_are_returned_at_once[pause|job_cancel]` and test 5.
3. M3: `GraphStore._controller` joins `controller_owner` and requires `owner_token IS NULL OR (lease_token = owner_token
   AND expires > now)`, so `release_owner` and owner expiry fence the owner's run leases at once (D14). Owner-less
   leases are unchanged. `test_owner_release_and_expiry_fence_run_leases_at_once`.
4. M-2: in the gated `prepare_ready`, a `bindings_for` failure (the resolver's run-wide re-verification of producers,
   persisted targets and active jobs' facts) raises the new `LedgerIntegrityError` (`LEDGER_INTEGRITY`) and fails
   nothing; the controller shows it as the ready nodes' wait reason (`needs_operator`). A compile refusal of the node
   itself still fails it and blocks its subtree. The ungated path is unchanged. `test_ledger_integrity_errors_fail_nothing`.
5. M2: when the reservations (the 8.1 backfill's historical ones included) leave no room for a claim, the node's wait
   reason says `approved budget exhausted: R of A USD already reserved (historical attempts reserved by the Task 8
   migration count too); re-plan with a larger budget`, plus `UNPRICED_ATTEMPT` when the run has one. The check runs
   before the provider is contacted. `graph_status` adds `budget_exhausted` and `unpriced_attempts`, and the
   controller never overwrites an `UNPRICED_ATTEMPT` notice. `test_a_budget_exhausted_by_history_is_named`.
6. Long uploads: the heartbeat above renews the owner lease, the run leases and the claim being sent (`renew_claim`,
   new) during every tick and tracking pass, and under `run_forever` uploads run on the tracking thread while the tick
   thread keeps renewing. The claim TTL is `ControllerSettings.tracking_claim_ttl_seconds` (600 s), passed to the
   outbox. It must exceed the longest single create the heartbeat cannot cover (a dead controller); each create of the
   controller's tracker is bounded to 2 retries and a 60 s timeout (below). `test_long_uploads_keep_the_owner_and_the_claim_alive`.
7. D8: a resource stays busy in this process until its dispatch thread returns, cache seeding after `release_gpu`
   included, under the same alias, GPU-UUID and cross-provider rules as `JobStore._resource_blocked`. Seeding is never
   retried; `cache_seed` shows `missing` after a crash while seeding. Test 14.
8. Windows flake: `tests/test_jobs.py::test_recovery_does_not_release_unknown_remote_execution` now calls
   `recover(stale_after_seconds=-1)`, as the other tests do. It was the only `stale_after_seconds=0` call; every other
   `recover`/`recover_stale` call in the tests already uses `-1` or `time.time() + 1`. `jobs.py` is unchanged.
9. Simulated attempts and `release_uncontacted`: a crash in PROVISIONING after `begin_launch` is released truthfully
   (no record); a crash while RUNNING without a result is now terminal (8.4 review Important 1, below).
   `test_a_simulated_attempt_stopped_while_running_is_terminal`.
- The earlier notes for 8.5 are covered too: `backfill(owner)` once after acquiring the owner; `process(owner)` each
  tick; `TrackingOutbox.status` for the tracking and registration fields; `settle_canceled_jobs` in the settle phase;
  only the gated `prepare_ready(node_ids=…)`; `LedgerBindingResolver` installed on the trusted path; outcomes used
  structurally (the two `OperationOutcome` classes stay; nothing is checked by type).

8.3 review fixes (independent review of `c7f8683`, "Ready with fixes"):
- Important 1, a long upload plus a reclaim could register twice:
  - `TrackingOutbox._claimed` refuses an expired claim, and `record_intent`, `record_receipt`, `retry`, `defer` and the
    new `renew_claim` go through it (`StaleTrackingClaim`, a `ValueError` with the old text). `record_intent` also
    validates the owner lease (`ClaimedEvent.owner`) and extends the claim by the TTL.
  - The publisher claims one event at a time (so no claim waits behind another event's upload), never runs
    `process()` concurrently with itself (a lock), exposes `renew_current()` for the controller's heartbeat, and
    abandons an event whose claim it lost.
  - The controller's tracker bounds each create (`create_run`, `create_model_version`) through
    `MLFLOW_HTTP_REQUEST_MAX_RETRIES=2` and `MLFLOW_HTTP_REQUEST_TIMEOUT=60`, set only around that call by a tracker
    built with `bounded_requests` (`controller_tracker`, or set by the controller on the tracker it is given) and
    restored afterwards. v1 trackers never set them. **Corrected by the 8.5 review (Important 2):** the "about
    3 × 60 s" bound written here was wrong. The client's session `Retry` re-sends a POST on 5xx and on read
    timeouts, so 2 retries could create a second version inside one intent, and `create_model_version` also waited
    up to 300 s for READY. Creates now use 0 transport retries and `await_creation_for=0` (see "Task 8.5 review
    fixes").
  - Contract: one controller per state directory (owner lease), one tracking thread, one claim in flight; the claim TTL
    must exceed the longest create a dead controller may leave in flight.
  - Tests:
    - `test_a_long_upload_past_the_claim_ttl_never_registers_twice`, shaped like the reviewer's probe. With a 500 s
      upload and a 200 s create, a second pass during the create finds nothing to claim; before the fix it reclaimed,
      searched an empty registry and created a second version. With a 700 s upload and a reclaim meanwhile, A
      abandons quietly; before the fix its `process()` raised. Each case ends with exactly one version.
    - `test_a_live_tracking_claim_is_renewed_and_an_expired_one_refused`: renewal, expiry and owner refusals.
    - The controller-level `test_long_uploads_keep_the_owner_and_the_claim_alive`: a create lasting 400 s on a 300 s
      claim is renewed by the heartbeat, no second claim is possible, there is one version, and the create is bounded.
- Minor 1: `record_outputs` returns before enqueuing when its `node_outputs` row already exists, so a rerun never
  re-hashes evidence (a later `worker.log`). `test_record_outputs_rerun_survives_a_later_worker_log`.
- Minor 3: `LazyMLflowTracker.build()` constructs once; a settings refusal (`ValueError`) is kept and re-raised without
  another construction, and a failed construction stops its own OIDC refresher (`MLflowTracker.close()`). The
  publisher builds a lazy tracker before any intent: a failure defers the event untried, with no intent
  (`TRACKER_UNAVAILABLE`). `test_a_tracker_that_cannot_be_built_defers_without_an_intent`.
- Minor 4: `tracking_settings_sha256` refuses `token`/`oidc` with a non-https tracking URI or an http artifact
  location (`AUTHENTICATED_HTTPS`).
- Minor 5: `current_auth_mode` moved to `auth.py` beside `credentials_configured` (re-exported by `tracking`), and
  `tracking_outbox.KEY_PATTERN` is built from `tracking.KEY_PATTERN`. Minors 4 and 5: `test_tracking_settings_and_shared_helpers`.
- Minor 6: `GraphStore.acknowledge_tracking` refuses unless the class flag `test_acknowledgement` is set;
  `tests/conftest.py` sets it for the ledger-level tests, `test_graph_controller.py` clears it, and no `src` code calls
  it. The 8.4 subprocess chain probe sets it too.
- Minor 7: `backfill` skips a row it cannot turn into events (savepoint per row) and lists it in
  `backfill_skipped` (shown by the controller's `status()`); `_publish` reads the run inside its try, so a poisoned
  event fails permanently (`UNREADABLE_RUN`) without aborting `process()`.
  `test_unreadable_runs_never_block_backfill_or_processing`.
- Minor 8: a node's `tracking_status` and `registration_status` use its latest attempt's events (and its node run).
  `test_tracking_status_follows_the_latest_attempt`.
- Minor 9: `find_keyed_model_versions` never returns a `FAILED_REGISTRATION` version (a failed create: the next
  authoritative empty search creates once), and `create_keyed_model_version` raises when the server returns one.
  `test_a_failed_registration_version_is_never_adopted`.
- Minor 10: an uncounted classification (the M4 `refused_before_launch`) enqueues no tracking events, like the other
  never-run settlements. `test_an_attempt_that_never_ran_gets_no_mlflow_attempt_run`. Documented: the I-3 refusal of a
  local selection settles as counted and terminal even while the run is PAUSED, because it is deterministic.
- Minor 2: `local evidence changed after enqueue` cannot be repaired by `rearm`. `TrackingOutbox.status` gives it an
  `action` (`EVIDENCE_CHANGED_ACTION`: restore the evidence files, or re-run the node in a new graph run), shown as the
  node's `tracking_action` and in the waiting child's reason. `test_changed_evidence_tells_the_operator_what_to_do` and
  `test_changed_evidence_is_named_for_the_operator`.

8.4 review fixes (independent review of `73e1bd7`, "Ready with fixes"):
- Important 1: the simulated `failure_kind` maps a FAILED result with error `SIMULATION_STOPPED` and no
  `engine_version` (the one `reconcile` publishes itself) to `failed_before_submit`: counted and terminal, as the real
  path after an operator confirms the stop. A result the simulated worker published keeps `remote_failed`.
  `test_simulated_attempt_crashed_while_running_settles_once_by_reconcile` was updated (no retry with `max_attempts=2`;
  the worker ran once), and the controller test above agrees.
- Important 2: `SimulatedWorker.check` sums the layout's tensor bytes and refuses above `max_output_bytes`
  (`SIMULATED_OUTPUT_TOO_LARGE`; default `MAX_SIMULATED_OUTPUT_BYTES` = 1 GiB, configurable on
  `SimulatedOperationDispatcher`) before `begin_launch`, through `abandon_unlaunched` (`refused_before_launch`); nothing
  is written. `test_simulated_outputs_above_the_size_cap_are_refused_before_launch` (a 7B-class root at the default cap,
  and a 16-byte cap through the dispatcher).
- Minor 1: `ExecutorProvider.recover` declares `runtime_profile`.
- Minor 2: `hard_links_supported` probes the launches directory before `begin_launch` (`NO_HARD_LINKS`), and
  `_publish_exclusive` fsyncs the directory after the link where the platform allows it (not on Windows).
  `test_a_filesystem_without_hard_links_is_refused_before_launch`.
- Minor 3: the heartbeat threads of the simulated dispatcher and of `attempt_runner` keep beating after any non-fencing
  error and log it (a `ValueError` still ends them: the lease was fenced).
  `test_attempt_heartbeats_survive_a_transient_database_error[simulated|runner]`.
- Minor 6 (move shared helpers into a light module) is skipped: it would be invasive, and the controller needs no
  shared type.
- Notes for Task 9 (recorded only): Minor 4, SSH `resolve` runs the read-only preflight `check` before
  `SERVER_DIFFERS`/`PROFILE_CHANGED` (low risk: the same host-key pin applies). Minor 5, `recover` goes through
  `_approved`, so a profile edit while an attempt holds its lock blocks result collection: Task 9 must forbid such
  edits, or `recover` must require only the transport identity.
- Merge gap: compile stays real-only for merges, including simulation plans. The simulated diamond of test 20 joins by
  its selection (`a → (b = evaluate a, c = continue a → ec = evaluate c) → d = select(b, ec)`), because two
  evaluations of one model are duplicate candidates. Task 11's mock diamond must not merge simulated outputs, or relax
  the check for simulation plans only.

Deviations from the plan:
- Interfaces:
  - `ControllerSettings` gains `lease_heartbeat_seconds`, `tracking_claim_ttl_seconds` and `tracking_batch`.
  - `DispatchRouter(real, simulated, *, store=None)` falls back to the dispatchers' own `graph` store.
  - New: `controller_tracker`, `CONTROLLER_REQUEST_LIMITS`, the wait-reason constants, `GraphStore.LedgerIntegrityError`
    and `LEDGER_INTEGRITY`, `TrackingOutbox.renew_claim`, `StaleTrackingClaim`, `TrackingPublisher.renew_current`,
    `LazyMLflowTracker.build`, `MLflowTracker.close` and `bounded_requests`, `hard_links_supported`,
    `SIMULATED_OUTPUT_TOO_LARGE`, `MAX_SIMULATED_OUTPUT_BYTES` and `NO_HARD_LINKS`.
- `run_forever` runs tracking on its own thread (the 8.3 reviewer's recommendation), so `tick` tracks inline only
  without it.
- `graph_status`:
  - `absent` also after a clean release (expiry 0);
  - added `budget_exhausted`, `unpriced_attempts`, `tracking.graph`, `tracking.action` and the node's
    `tracking_action`;
  - a wait reason is shown for unfinished nodes only.
- Settle table:
  - A stale started PROVISIONING attempt that `release_uncontacted` cannot release becomes FAILED with its lock kept,
    through `transition` under the current (just rotated) lease. The plan said `recover_stale`, which skips
    PROVISIONING.
  - A job claimed for another runtime than the one its destination was opened for is returned unlaunched, because
    `claim_ready` takes no job filter.
  - Managed runs include those with tracking events but no summary.
- Fakes: `ScriptedDispatcher` freezes `test_model_inputs` fixture artifacts under the attempt's producer identity,
  instead of `SimulatedWorker` + `freeze_operation_output`. Its `failure_kind` is the real runner's, read from the
  records it writes. The real `SimulatedOperationDispatcher` and the real `OperationAttemptRunner` are covered by
  tests 20 and 22.
- Test 20's diamond is `a → (b, c → ec) → d` (the merge gap; two evaluations of one model are duplicate candidates).
- Tests beyond the plan's 24:
  - `test_refused_launches_are_returned_at_once[pause|job_cancel]`, the M3, M-2 and note-6 tests, and
    `test_settings_router_and_test_hooks`;
  - `test_changed_evidence_is_named_for_the_operator`, `test_a_simulated_attempt_stopped_while_running_is_terminal`
    and `test_the_seeding_window_covers_aliases_and_other_providers`.
- Files beyond the 8.5 list:
  - `graph_store.py` (M3, M-2, 8.3 Minors 1, 6 and 10);
  - `tracking_outbox.py`, `tracking.py` and `auth.py` (8.3 review, authorized);
  - `simulated_dispatch.py`, `executor_profiles.py` and `attempt_runner.py` (8.4 review, authorized; Minor 3 in the
    runner too);
  - `tests/conftest.py` (Minor 6 flag), `tests/test_jobs.py` (note 8), `tests/test_tracking_outbox.py` and
    `tests/test_simulated_dispatch.py`.
- The outcome type was not moved; the controller uses outcomes structurally.
- RED was checked on a `git archive HEAD` export with the new test files copied in (as 8.3 did), because parts of the
  implementation were written before their tests. There, the two `test_acknowledgement` monkeypatches used
  `raising=False`, since the attribute is new.

Actual verification (main tree; no other agent's uncommitted changes):
- RED, on the `73e1bd7` export with the new tests (main venv, `PYTHONPATH` on the export's `src`; `tuning_factory`
  resolved into the export): the 60 new or changed cases failed for the intended reasons, and the other 46 cases of
  those files passed:
  - 45 controller cases on `tuning_factory.graph_controller (Task 8.5) is missing`;
  - the M3 test on `DID NOT RAISE` (the run lease outlived its owner);
  - the 8.3 fixes:
    - the slow-create probe produced 2 versions, and then `process()` raised `stale tracking claim`;
    - `renew_claim` was missing;
    - the rerun raised `tracking idempotency key reused with different payload`;
    - the deferred event had `tries=1` and an intent;
    - the http token destination did not raise;
    - `KeyError: 'sha256'` aborted the backfill;
    - the status was `failed` instead of `saved`;
    - `FAILED_REGISTRATION` was missing;
    - the never-run attempt had an `attempt:` event;
    - the status had no `action`;
  - the 8.4 fixes: `remote_failed` instead of `failed_before_submit`, the missing cap and link-probe names, and both
    heartbeats stopping after the first database error.

  The seeding-window alias test was added after the export check. It fails there on the missing module, and the
  `_conflicts` mutation below fails it.
- Mutations, as pytest plugins in `%TEMP%` with no `src` edit:

  | Mutation | Tests that fail |
  |---|---|
  | `_busy` never busy (no D8) | test 14 |
  | `_conflicts` only for identical keys | the seeding-window alias test |
  | `TrackingOutbox._claimed` ignores expiry and the owner | the live-claim test |
  | `GraphStore._controller` without the owner join (M3) | the M3 test |
  | the dispatch thread never returns a refused launch | test 5, both refused-launch cases, simulated cancel |
  | `reconcile` without `takeover_attempt` | test 11 |
- GREEN:
  - `uv run pytest tests/test_graph_controller.py tests/test_tracking_outbox.py -q`: **76 passed (46 + 30)**.
  - The step-4 regressions with the 8.1–8.4 sets (`test_evaluation_tracking`, `test_execution`,
    `test_reconciliation`, `test_runner_cli`, `test_graph_store`, `test_graph_planner`, `test_jobs`,
    `test_operation_runner`, `test_gpu_servers`, `test_desktop_servers`, `test_graph_readiness`,
    `test_simulated_dispatch`, `test_executor_profiles`, `test_desktop_runtime`, `test_model_inputs`): **531 passed**.
  - `uv run pytest -q` twice on the final tree: **1343 passed, 1 skipped** and **1343 passed, 1 skipped** (1283 + 60 new). Neither run hit the old
    Windows flake.
- Ruff (`src tests`) is clean. `check_secrets.py` reports 0 findings over 775 file versions (every change
  staged). Both GPU locks are unchanged, `git ls-files --eol` shows no CRLF, and every changed file is CR-free.
  `graph_controller` imports no heavy library, and the 8.4 subprocess import probe still passes.
- CPU acceptance (`-I`), run once on the final source:
  - It exited 0, and all **29** checks passed. Max errors are unchanged: merge delta 7.78e-9, materialize delta
    9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8.
  - Receipt SHA256 `d796f3edb76c873b483afdefe7c84290ce456bbd48db4694954c7d25043c7f1d`, source_sha256 `0888d176108edd46620467e8df32664da40f5473d7746488d7a15d30d0c9ceab` (equal to `source_hash()` of a `git archive` export of the
    commit), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  - It supersedes 8.4's `2afbd0b8…88f8`.
- Module sizes: new `graph_controller.py` 1065, `graph_store.py` 1401 (1359), `tracking_outbox.py` 832
  (727), `tracking.py` 367 (305), `simulated_dispatch.py` 853 (789).

Not verified or still open (Task 8 as a whole):
- **No real infrastructure was used.** Everything ran on fakes, local files and SQLite, with in-process threads.
  Still unverified:
  - real concurrency across several Pods;
  - real SSH servers (BOP-style included) and real recovery executors;
  - a real MLflow server: its version, tag-filter search and pagination, `search_model_versions` with `run_id`,
    artifact roots and proxying, and real upload and create times;
  - the Windows broker process (Task 9);
  - a real lost launch response;
  - RunPod transport hashes from real settings, and a live RunPod price observation.
- The bounded create relies on mlflow-skinny 3.17.1 reading `MLFLOW_HTTP_REQUEST_MAX_RETRIES`/`_TIMEOUT` per request.
  That was read from the installed source, not exercised against a server. The variables are process-wide during the
  call, which is safe only because the broker process runs no other MLflow work.
- Contention between two controller processes is covered only by the owner lease; the races are in-process.
  `ThreadDispatchPool` ran only in unit form, and long dispatch threads holding SSH are untested.
- A COMPLETED attempt whose outputs `record_outputs` refuses stays COMPLETED with an operator wait reason; no state
  leads out of COMPLETED.
- Heartbeats run on wall time. A wall-clock jump can expire a lease early; the old owner is then fenced, which is safe.
- The simulation merge gap remains (above).

What Task 9 must wire (plan §7):
- The headless broker `--controller`:
  - `GraphStore(state/jobs.db)` and `SettingsExecutorProvider(profiles, state)`;
  - `DispatchRouter(OperationAttemptRunner(store, root=state), SimulatedOperationDispatcher(store, root=state,
    roots=DirectoryRootSnapshots(...)))`;
  - `controller_tracker(uri)` and `clock=time.time`. No `simulated_provider` is needed: the controller builds one per
    simulation run.
  - Then `GraphController(...).acquire_controller(owner_id)`, where `controller already running` maps to a fixed exit
    code, and `run_forever(stop_event)`, set by closing stdin or by a termination signal.
- Desktop actions call `GraphStore.pause/resume/cancel` directly.
- Status polls `graph_status(store, run_id, now=time.time())`: `controller.state`, and per node `wait_reason`,
  `needs_operator`, `tracking_status`, `tracking_action`, `registration_status`, `selection` and `cache_seed`.
- Operator actions:
  - `release-lock --confirm-execution-stopped` for `needs_operator`;
  - `TrackingOutbox.rearm(event_id)` for FAILED/UNCERTAIN tracking, except changed evidence (`tracking_action`).
- Destination hashes: `runpod_transport_sha256` and `tracking_settings_sha256`. An authenticated destination requires
  https.
- Forbid profile edits while an attempt holds its lock (8.4 Minor 5), and keep the 8.4 Minor 4 preflight-order note.

## Task 9.1 workflow channel, projects, recipes and data, 2026-10-20

Commits `feat: add the strict desktop workflow channel for projects, recipes and data` and the review fixes
`fix: tighten workflow channel path, envelope and credential checks`, written on `f75e628` in the worktree
`codex/task-9.1` while 8.5 was still uncommitted (9.1 touches none of 8.5's files), then rebased onto 8.5
(`2362144`). It implements
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.1" with D8, D11 and D12.

What changed:
- New `desktop_workflows.py`: the closed 32-action table and its module routing (`ACTION_MODULES`, read-only),
  `SECRET_SCOPES` (`executor_profile_inspect` → `RUNPOD_API_KEY`, `model_root_snapshot` → `HF_TOKEN`), the
  `WorkflowEnvelope`/`NativeProfile`/`WorkflowContext` models, `WorkflowIssue`/`WorkflowResult`, the
  `workflow_action` registration decorator, `issues_from_validation`, `refusal` with the 409 table, `local_file`,
  `dispatch_action` (for the 9.6 CLI) and `handle`. Handlers for the 14 project, recipe, legacy-import, mapped-import
  and split actions. The reserved practice ID is refused on `dataset_import_register` and on split outputs (preview
  and, again, commit) with `TUTORIAL_REFUSAL`.
- `desktop.main`: only the `request["channel"] == "workflow"` branch and the error redaction of the request's
  `secrets` and profile field values. The legacy branch, its error classes and its output line are unchanged.
- Actions of 9.2–9.4 resolve their module lazily; until it exists they answer `invalid`/`not_available`
  (`NOT_AVAILABLE`), never a crash. A module that exists but misses a dependency is not masked.

Deviations from the plan:
- The legacy allowlist has **34** names, not 35 (F6 miscounts). The test copies the 34 and also parses
  `allowed()` from `main.rs`.
- `FORBIDDEN_FIELD` is matched (`command_like_field`) against the whole name, the name without separators and
  each snake/camel-case word, with an optional `s`, `line` or digit suffix (`run_commands`, `cmdline`, `entry_point`,
  `argv0`). A substring search would flag `RecipeSpec.description` ("script") and 9.3's `approve_execution`
  ("exec"). In the whole package only `worker_protocol.StartMarker.command_sha256` matches, and it is not a request
  model. Registration also refuses such models, and models that are not frozen `StrictModel`s with `extra="forbid"`.
- Credential refusal is broader than the seven names. Names are compared ignoring case, surrounding whitespace and
  `-` versus `_`, and include the environment spellings (AWS, `HUGGING_FACE_HUB_TOKEN`, `MLFLOW_TRACKING_PASSWORD`).
  A `*_saved` flag that is not a boolean is refused (case-insensitive suffix). Pasted private-key markers are refused
  in the key-file fields of settings and profiles only (`ssh_key_file`, `server_ssh_key_file`, as main.rs
  `pasted_key`), so a display name cannot break every action. After model validation, the request data is checked
  with the same name and `*_saved` rules and for key markers in any string value; a hit is `credential_field`, so it
  is never stored.
- Envelope errors raise a fixed `invalid workflow request (<envelope fields>)`, never pydantic's text (it echoes
  inputs). The root must be absolute; profile IDs are ≤64 characters and unique ignoring case and trailing dots
  (Windows file and Credential Manager semantics). A workflow envelope with non-object `settings` is answered with
  that error; the legacy path keeps its behaviour.
- `issues_from_validation(..., data=None)` follows the request data to drop pydantic's union-member tags (for example
  `constrained-str`) from pointers, and escapes `~`/`/` (RFC 6901).
- Re-registering a mapped import's ID and version with other bytes fails in `immutable_write` of the provenance
  pointer (`immutable version already exists`), not with the registry's text. Only `dataset_import_register` maps
  that text to `version_exists`; globally it stays `refused`. `INVALID_CODES` adds `LOCAL_FILE_ONLY` →
  `local_file_only`.
- `local_file` also refuses DOS device names (`CON`, `NUL.csv`, ` NUL`, `COM1`, `CONOUT$`), any `:` after the drive
  letter (`nul:`, `COM1:`, alternate data streams), surrounding whitespace, mixed `\/` UNC prefixes and NUL bytes.
  New codes and Korean texts: `file_unreadable`, `source_unknown`, `dataset_unknown`, `preview_unknown`,
  `legacy_profile_missing`/`legacy_profile_invalid`, warning `legacy_dataset_missing`, `credential_field`.
- `dispatch_action` scrubs scoped secret values from any result (defence in depth for 9.2's two scoped actions).
- Reads on an empty state create no definitions database.
- `project_import_legacy`: project name = `ai_id`, purpose empty, task type = `workflow_id`; train binding plus a
  validation binding from the evaluation profile. A dataset that is not registered becomes the warning
  `legacy_dataset_missing` and no binding. That warning is derived from the returned project's bindings, so a retry
  reports the same gap. A retry returns the current project; so does a revision conflict with a concurrent save. A
  changed profile under the same request ID is `request_reused`.
- `projects_list[].datasets` is the binding list. Splitting the practice dataset itself is allowed: its outputs
  inherit `export_allowed: false`, so a real run still refuses them.

Actual verification (worktree `D:\llm-tuning-factory-wt91`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree):
- RED, before any `src` edit: 32 of the 33 new cases failed. 30 failed on the missing module (`pytest.fail`, not
  collection). Two failed because `desktop.main` had no workflow branch (legacy answered `unsupported desktop
  action`). The legacy-envelope regression guard passed. The device-name check was added later, test-first: RED
  (`<tmp>\CON` reached `open()`), then GREEN.
- Review fixes (M1–M7, M9; the second commit), test-first: seven tests failed first for the intended reasons.
  `<dir>\nul:` returned `ok` with a 0-byte snapshot. `settings: null` crashed `desktop.main` with `AttributeError`
  and no output line. `run_commands` was not command-like. `classify` mapped the generic immutable text to
  `version_exists`. Free-JSON `*_saved`/PEM values and a PEM `purpose` were stored. `Pod-A`/`pod-a.` passed as
  distinct profiles, and a pasted-key marker in a profile name broke the envelope. A retry dropped the warning.
  All 36 cases then passed. Legacy `settings: null` still raises as before (checked ad hoc, not pinned by a test).
- Mutations through a pytest plugin in `%TEMP%` (no `src` edit): UNC allowed, scope check removed, credential check
  removed, reserved guard removed, no secret scrub, pointers without data, no free-JSON credential scan, extra fields
  relabelled, foreign-module registration allowed, every refusal classified `invalid`, random legacy recipe IDs. Each
  one fails at least one test.
- GREEN: the plan's command (`test_desktop_projects`, `test_desktop`, `test_desktop_tutorial`, `test_projects`,
  `test_recipes`, `test_dataset_import`, `test_dataset_splits`, `test_legacy_migration`) gave **153 passed**, and
  **156 passed** with the review fixes. Every desktop test (`tests/test_desktop*.py`, seven files) gave **145
  passed**, and **148 passed** with the review fixes. The full suite gave **1316 passed, 1 skipped** (1283 + 33)
  on `f75e628`. After the review fixes and the rebase onto 8.5 (`2362144`) it gave **1379 passed, 1 skipped**
  (8.5's 1343 + 36). The known `test_jobs.py::test_recovery_does_not_release_unknown_remote_execution` flake did
  not occur.
- Ruff (`src tests`) is clean on the rebased tree. `check_secrets.py` reports 0 findings (781 file
  versions, with the rebased change staged). `git diff --exit-code containers/ pyproject.toml uv.lock` is clean.
  Every changed file is CR-free. A subprocess test shows the channel imports no torch, peft, transformers,
  safetensors, huggingface_hub or keyring.
- CPU acceptance (`-I`, the worktree's script and source) on the rebased tree exited 0, and all **29** checks
  passed. Receipt SHA256 `5147d6d9a8eee2eeaeb38442f95281d315fc4a78ff50be6c9cbd51c98dfbab68`, source_sha256
  `ebf51635ec1b1d09b5421675d2be5892a0a38d084ad907cf3bb6ec5c529a76ec` (equal to `source_hash()` of a `git archive`
  export of the fix commit), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max
  errors are unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits
  3.35e-8/2.98e-8). `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false.
  It supersedes 8.5's `d796f3ed…7f1d`. (The first commit on `f75e628` gave `dd2dc480…ec22`, source `3915741e…32e0`.)

Not verified or still open:
- No Rust, Playwright or UI run: 9.1 changes no TypeScript or Rust. The native side (9.5) does not send workflow
  requests yet, so the envelope is exercised only from Python.
- `local_file` checks strings only. A local symlink or junction to a share, or a mapped network drive letter, is not
  detected.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.5 native executor profiles, workflow channel and headless broker, 2026-10-20

Commit `feat: expose secure desktop workflow automation and controller lifecycle`, written on 9.1's first commit
(`3d119c6`) in the worktree `codex/task-9.5` and rebased onto `1ca866c` (9.1 with its review fixes, on 8.5), plus
`test: pin the native contract against the 9.1 review fixes`. It implements
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.5" with D1, D2, D5, D6, D8, D19 and
D20, and the coordinator's 9.1-review notes M4 (key-marker scope) and M9 (profile-ID identity). Only Rust
(`profiles.rs`, `broker.rs`, `main.rs`) and the new `tests/test_native_contract.py` changed: no `src/tuning_factory`,
TypeScript, crate, `Cargo.lock`, `pyproject.toml` or `uv.lock` change.

What changed:
- `profiles.rs` (new): the `Vault` trait (`KeyringVault` for Credential Manager, same service and error texts as the
  settings commands) and the executor-profile store `<app_root>/executor-profiles.json`
  (`{"schema_version": 1, "profiles": {id: {public fields}}}`, at most 32). Fields are allowlisted per provider
  (the `RUNPOD_PROFILE_FIELDS`/`SERVER_PROFILE_FIELDS` of 9.1), values are strings of ≤2048 bytes, the provider is
  fixed once saved, and a name (≤200 characters) is required. Pasted private keys and `.ppk` paths are refused
  (`main.rs` rules), and so is key content in any profile field. GPU selections must be unique, ports 1–65535, and
  costs plain non-negative decimals. Each RunPod profile has its own vault entry
  `executor-profile/<id>/RUNPOD_API_KEY`. An empty key input keeps the saved key. SSH profiles hold no key.
  `copy_legacy_credential` copies the legacy key only on request. Deleting a profile removes only its own entry.
  Save and delete validate first, then ask the sidecar `executor_profile_edit_check` (9.3), and fail closed.
- `broker.rs` (new): `is_controller_mode` (exactly `[exe, "--controller"]`), `sidecar_binary` (the `factory_action`
  rule), `sidecar_command` and `broker_command` (args exactly `--controller`, no environment change, stdout/stderr
  null; `CREATE_NO_WINDOW`, and for the broker also `CREATE_NEW_PROCESS_GROUP` and `CREATE_BREAKAWAY_FROM_JOB`, with
  one retry without breakaway on OS error 5), the strict `factory-broker/1` parser, `owner_id`, `controller_message`,
  `should_start`, `serve_broker` and `run_broker` (exit 10 bad start line, 11 settings/profiles/vault unreadable or
  start line over 1 MiB, 12 sidecar not started, else the sidecar's code; D19 `state/controller/broker.json`
  `{at, code, error}` with fixed codes and Korean texts only before the sidecar starts).
- `main.rs`: `main()` enters broker mode before `tauri::Builder`; `run_sidecar` is the spawn code of
  `factory_action` moved unchanged (same argv, flags, stdio, error texts and request keys); `read_settings` now
  reads the vault through `Vault` with the same texts; the pasted-key, `.ppk` and GPU-selection rules became named
  helpers used by `merge_public_settings` unchanged. New commands `workflow_action` (closed 32-name allowlist,
  disjoint from the legacy 34), `executor_profiles_get`, `executor_profile_save`, `executor_profile_delete`,
  `controller_start`, `controller_status`, `controller_stop`. `forget_credentials` also deletes every profile's key.

Stdin schemas as implemented (for 9.4):
- GUI → broker, one line, no secret: `{"schema": "factory-broker/1", "app_root": "<absolute app data dir>"}`. Exactly
  these two keys; the root is absolute, ≤4000 bytes, without control characters, a `..` component or a UNC/device
  prefix (`\\`, `//`, mixed); the line is ≤64 KiB. The GUI then closes the pipe.
- Broker → sidecar `factory-bridge --controller`, one UTF-8 JSON line ≤1 MiB, then the pipe stays open until the
  sidecar exits: `{"schema": "factory-controller/1", "state_root": "<app_root>\\state", "owner_id":
  "desktop.<pid>.<unix nanos>", "tracking": {...}, "profiles": [{"profile_id", "fields": {public fields},
  "secrets": {} | {"RUNPOD_API_KEY": "..."}}]}`. No `settings` key (StartSettings defaults apply). `tracking` holds
  only string values of `MLFLOW_TRACKING_URI`, `FACTORY_MLFLOW_ARTIFACT_LOCATION`, `FACTORY_OPERATOR`, `auth_mode`
  and the credentials `filter_mlflow_auth` keeps for that mode (`none`: none; `token`: `MLFLOW_TRACKING_TOKEN`;
  `oidc`/empty: `MLFLOW_OIDC_TOKEN_URL`, `MLFLOW_OIDC_CLIENT_ID`, `MLFLOW_OIDC_CLIENT_SECRET`). Absent settings are
  omitted, not sent empty. `profiles` is sorted by ID; SSH profiles always carry `"secrets": {}`. The broker reads
  from the vault only the MLflow secret its auth mode keeps and each RunPod profile's own entry, never S3, HF or the
  legacy RunPod key. `broker.rs` pins one exact line (`CONTROLLER_MESSAGE_SAMPLE`); the contract test validates it.
- Workflow requests (GUI → one-request sidecar): `{"channel": "workflow", "root": "<app_root>\\state", "settings":
  <public strings of the 28 settings fields without key content, plus <secret>_saved: true>, "profiles": [public
  views], "secrets": {}, "action", "data": {object}}`. `secrets` is `{"RUNPOD_API_KEY"}` only for
  `executor_profile_inspect` on exactly a stored RunPod profile ID, and `{"HF_TOKEN"}` only for `model_root_snapshot`.

Deviations and choices to review:
- 9.5's tests: the plan's 15 Rust tests plus 11 more (pinned contract sample, broker pipe lifetime and exit codes,
  least-privilege vault reads, breakaway fallback, `should_start`, edit check, legacy-key copy, store bounds and
  corruption, ID collisions, settings through the vault, the legacy sidecar's empty argv/env). Python: the plan's 3
  contract tests became 7. `test_rust_controller_message_follows_the_plan_schema` pins the plan's values and the
  sample now. `test_rust_controller_message_matches_python` skips until `controller_entry` (9.4) exists; it then
  validates the sample with `ControllerStart`. Against the landed 9.1 fixes, through `desktop.main`:
  `test_profile_names_with_key_words_are_not_pasted_keys` (M4: `KEY_FILE_FIELDS` equal, a name with marker words
  reaches the channel, key content in either key-file field is refused),
  `test_profile_ids_are_one_identity_on_both_sides` (M9: Rust's `profile_id_key` body equals `casefold().rstrip(".")` on every valid ID character; `GPU-A` with
  `gpu-a.` is refused) and `test_workflow_settings_are_always_an_object` (the fixed `invalid workflow request
  (settings)`; Rust wraps settings in an object). With 9.1's pre-fix `desktop_workflows.py` overlaid, the M4 and M9
  assertions fail, so they pin the fixes.
- Fail closed. A profile save or delete is refused unless `executor_profile_edit_check` answers `ok` with an
  `affected_runs` array. Until 9.3 lands, that action answers `not_available`, so profile edits are refused.
  `controller_start` spawns only for `controller.state` `absent` or `stale`. It returns `already_running` for
  `running` and an error for anything else (until 9.4 lands: `not_available`).
- M9: profile IDs are one identity under `profile_id_key` (ASCII lowercase plus trailing dots stripped, equal to
  9.1's `casefold().rstrip(".")`). A new colliding ID is refused. Deleting another spelling of a stored ID is
  refused, because Credential Manager would resolve it to that profile's entry. The scoped key is given only for the
  exact stored ID. A hand-edited file with colliding IDs serves only the first in ID order. Profile IDs also refuse
  DOS device names and any `estimate-` prefix, case-insensitively.
- M4: Rust refuses key markers in every profile field (stricter). The workflow settings view drops any public value
  with key content instead of sending it, so a legacy stored pasted key cannot break the channel. Non-string or
  unknown keys in `settings.json` are dropped from that view too.
- `executor-profiles.json` writes keep only what is served (invalid or colliding hand-edited entries are dropped on
  the next save). A corrupt file or more than 32 served profiles is an error
  (`실행 프로필 파일이 손상되었습니다.`).
- A successful sidecar start removes a stale `broker.json`.
- `forget_credentials` now takes the `AppHandle`, which Tauri injects, so the TypeScript call is unchanged. It
  deletes the seven legacy entries before it resolves the app folder, as before.
- `executor_profile_delete` returns `{"profile_id", "affected_runs"}` (§5 left it untyped).

Actual verification (worktree `D:\llm-tuning-factory-wt95`; cargo with `CARGO_TARGET_DIR` on the main tree's cache;
main venv with `PYTHONPATH` on the worktree `src`, where `tuning_factory.__file__` resolved):
- RED first. The Python contract tests failed 3 of 3 on the missing Rust constants and files (the parity test
  skipped). With `todo!()` stubs, 26 new Rust tests failed and the 12 existing ones passed.
- GREEN: `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release` → **38 passed**
  (12 + 26). `cargo check --release` (production build) has no warning.
- Mutations (one at a time, sources restored and checked by SHA256): stdin closed before the wait, no auth-mode
  filter, the legacy RunPod key as the scoped secret, the scoped key by normalized ID, an added sidecar env var,
  the edit check failing open, no ID-collision check, key content kept in workflow settings, the broker reading
  every secret, delete by another spelling, extra argv accepted. Each failed at least one test.
- `tests/test_native_contract.py` with `tests/test_desktop_projects.py` → **37 passed, 1 skipped** (the 9.4 parity
  test). Full suite on `3d119c6` → **1320 passed, 2 skipped** (1316 + 4; the skips are the opt-in real-GPU test
  and the 9.4 parity test). The known `test_jobs.py::test_recovery_does_not_release_unknown_remote_execution` flake
  failed once in the baseline run (1315 passed, 1 failed, 1 skipped) and did not occur in the final run.
- Ruff (`src tests`) is clean. `check_secrets.py` → 0 findings over 785 file versions (this change staged).
  `git diff --exit-code desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock containers/ pyproject.toml uv.lock
  desktop/src/` is clean. Every changed file is CR-free.
- From `desktop/` (a copy of the main tree's `node_modules`): `npx playwright test` → **81 passed**; `npm run build`
  passes.
- After the rebase onto `1ca866c` (no conflict; nothing under `desktop/` changed upstream) and the contract-test
  follow-up: `cargo test ... --locked --offline --release` → **38 passed**; `tests/test_native_contract.py` →
  **6 passed, 1 skipped**; full suite → **1385 passed, 2 skipped** (1379 + 6; the same two skips), the known flake did
  not occur; Ruff clean; `npx playwright test` → **81 passed**; `npm run build` passes; the diff check above is clean.
- No CPU acceptance run: `src/tuning_factory` is unchanged; only `tests/test_native_contract.py` was added.

Not verified or still open:
- Everything native was tested with an in-memory vault and fake sidecars. Not exercised: the real Credential
  Manager from a background (broker) process, a real detached broker spawn and breakaway from a Windows job object,
  the PyInstaller onefile child keeping the stdin lifeline, and the broker's EOF stop end to end. All are Task 11
  installed-app acceptance.
- The 9.3 `executor_profile_edit_check` and 9.4 `controller_status`/`controller_stop`/`--controller` contracts are
  coded against the plan. Rust tests cover them with fake answers; the Python parity test runs once 9.4 lands.
  Until 9.3 and 9.4 land, profile save/delete and `controller_start` refuse with `not_available` (fail closed).
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.2 planning: profiles, observations, HF roots, benchmarks, validation and dry plans, 2026-10-20

Commit `feat: plan training graphs from desktop projects with pinned destinations`, written in the worktree
`codex/task-9.2` on 9.1's first commit (`3d119c6`) while 9.1 was under review. It was then rebased, without code
changes, onto 9.1's review fixes (`1ca866c`, on 8.5 `2362144`) and onto 9.5 (`a308949`); only this file
conflicted. It implements
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.2" with D6, D11 and D13–D18.

What changed:
- New `workflow_planning.py`. Its eight actions register through 9.1's closed table: `executor_profile_inspect`,
  `model_root_snapshot`, `model_roots_list`, `benchmark_save`, `benchmarks_list`, `graph_validate`, `graph_plan`
  and `graph_plan_get`. It also has the helpers that 9.3, 9.4 and 9.6 consume: `profile_revision`,
  `public_profile`, `public_auth_mode`, `tracking_destination`, `save_observation`/`load_observation`,
  `root_snapshots_dir`, `BenchmarkStore`, `stored_plan`, `plan_summary` and `current_profile_revisions`.
- Only two actions touch the network, and only in production: a read-only RunPod `status` GET or the fixed SSH probe
  (scope `RUNPOD_API_KEY`), and an explicitly approved HF download of `config.json` and the tokenizer files (scope
  `HF_TOKEN`). `graph_validate` and `graph_plan` are offline. A real plan needs an observation of the current profile
  revision that is ≤3600 s old, every transport hash, and a tracking destination. The destination is
  `tracking_settings_sha256(public_tracking_settings(uri, DEFAULT_EXPERIMENT, artifact_location,
  auth_mode=public_auth_mode(settings)))`, checked against the HTTPS and OIDC rules first. RunPod facts come from
  `runpod_facts` (`gpu.id`) and the transport hash from `runpod_transport_sha256`.
- `projects._RevisionStore.get_by_revision`. `graph_planner._runtime` reads the GPU lock through
  `_project_root()`, which is `sys._MEIPASS/assets` when frozen (D18).
- Seam for 9.7: HF-root target evidence is stored beside the root index, at
  `state/workflows/root-targets/<digest(canonical(root))>.json`, through `save_root_targets`, `load_root_targets`
  and `root_targets_path`. `graph_plan` passes it as `ExecutionChoices.base_targets`. D15's `base_targets_missing`
  refusal, and the simulation warning `simulated_execution_needs_targets`, apply only to HF roots without evidence.
  `model_roots_list` reports `targets_ready`.

Deviations from the plan:
- `plan_summary(plan, state_dir=None)`. `GraphPlan` holds only the tracking hash. A real plan therefore stores its
  public tracking settings content-addressed at `state/workflows/tracking/<sha>.json`. `plan_summary` returns them
  only when given `state_dir`, verified against the hash; otherwise `settings` is null.
- Observation files are keyed by `profile_key` = casefold plus `rstrip(".")` (9.1 M9), not by the raw ID.
  - A record of another ID with the same key is refused as `observation_conflict`. `executor_profile_inspect`
    refuses it before any request.
  - DOS device names are refused.
  - The record carries the exact ID and its own `sha256`, so a damaged or edited record is `observation_invalid`.
- Simulation plans pin no tracking destination: simulated runs publish to the local sink. With a stored observation
  of an older profile revision, a simulation plan pins the profile as it was observed and warns
  `observation_revision_differs`; a real plan is blocked.
- `tracking_incomplete` matches `auth.require_complete_oidc` on the filtered settings exactly. It covers any OIDC
  URL without both a client ID and a saved secret, not only "URL and saved secret without a client ID".
- Issue paths:
  - Tracking issues are at `/tracking`, and profile or observation issues at `/profiles/<id>` (9.3's convention).
  - Estimate issues are at `/estimates/<i>`.
  - Graph issues are at `/graph/nodes/<i>[/field]` with `node_id`; others at `/graph` or `/datasets`.
  - Pydantic's follow-on `too_short` on `nodes`, raised when a node already failed, is dropped.
- `model_root_snapshot`:
  - It downloads every tokenizer file the revision lists, because the fingerprint must equal what the worker
    computes. It refuses, rather than skips, a selected name that is weight-suffixed or unsafe.
  - It refuses repository names that Windows would alter (a trailing dot or space, device names).
  - It reuses an existing directory only if that directory matches the root.
  - The first root-index entry wins.
- Rewrites of immutable records (benchmarks, plans, tracking settings) over other bytes map to this module's own
  refusals, `benchmark_invalid` and `plan_invalid` (9.1 M6: `version_exists` belongs only to
  `dataset_import_register`).
- New codes beyond the plan:
  - `executor_profile_invalid`, `observation_invalid`, `observation_conflict`, `estimate_duplicate`,
    `node_resource_duplicate`, `recipe_invalid`.
  - `model_root_invalid`, `root_targets_invalid`, `tracking_invalid`, `repository_unsupported`,
    `model_unsupported`, `snapshot_refused`, `tokenizer_incomplete`, `config_changed`, `root_snapshot_conflict`,
    `snapshot_failed`.
  - `sample_id_invalid`, `dataset_unknown`, `benchmark_invalid`, `plan_unknown`, `plan_invalid`.
- `graph_validate` runs the definition checks (schema, recipes, `validate_graph`, benchmarks, roots, D15, D16) and
  returns `{project_id, project_revision, node_count}`. `benchmark_save` does not apply
  `check_generation_benchmark`; `plan_graph` still refuses such a benchmark at planning (`plan_refused`).
- `executor_profile_inspect` checks the public fields offline before any request. For RunPod it also requires the
  observed Pod ID to equal the profile's. A real plan also refuses an observation with a negative age.

Actual verification (worktree `D:\llm-tuning-factory-wt92`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree):
- RED, before any `src` edit: all 55 new cases failed. 52 failed on the missing module (`pytest.fail`, not
  collection) and one in the import subprocess. One failed on the missing `get_by_revision`. The frozen-bundle test
  got the source lock's digest instead of the bundle's. Two more cases were added later, test-first (streamed and
  total download caps); a handler-level profile-ID collision check was added to an existing case.
- Mutations through a harness outside the worktree (each restored after its run): 30 source mutations, all killed.
  They covered the age check, weight suffixes, `*_saved` truthiness, the D15 refusal and evidence lookup, the
  HTTPS rule, the config hash, real estimates, the artifact location in the hash, an unconditional Authorization
  header, both collision checks, record integrity, practice and export rules, the frozen lock path, the simulation
  warning, both rewrite texts, pointers without data, D16, streamed and total caps, OIDC completeness, the revision
  check, the missing URI, node resources, estimate collision, type-name-only provider errors and `validate_pod`.
- GREEN: the plan's command (`test_workflow_planning`, `test_desktop_projects`, `test_graph_planner`,
  `test_graph_schema`, `test_projects`, `test_executor_profiles`, `test_model_hub`) gave **204 passed**. The full
  suite on `3d119c6` gave **1372 passed, 1 skipped, 1 failed** (1316 + 57). The failure was the known Windows flake
  `test_jobs.py::test_recovery_does_not_release_unknown_remote_execution`, while the CPU acceptance ran in parallel;
  it passed alone. An earlier full run with 55 of the new cases gave **1371 passed, 1 skipped**.
- Rebased onto `1ca866c`: the plan's command gave **207 passed** (9.1's fixes add three cases), and the full suite
  gave **1436 passed, 1 skipped** (9.1's 1379 + 57). Rebased onto 9.5 (`a308949`, the final tree): the plan's
  command plus `test_native_contract` gave **213 passed, 1 skipped**, and the full suite gave **1442 passed,
  2 skipped** (9.5's 1385 + 57; the same two skips). The known flake did not occur in either run. The
  auth-mode parity test resolved `auth.current_auth_mode` (8.5) on this tree and `tracking.current_auth_mode` on
  `3d119c6`. Ruff (`src tests`) is clean. `check_secrets.py` reports 0 findings with the change staged.
  `git diff --exit-code containers/ pyproject.toml uv.lock desktop/` is clean. Every changed file is CR-free.
  A subprocess test shows `workflow_planning` imports no torch, peft, transformers, safetensors,
  huggingface_hub, keyring, paramiko or mlflow.
- CPU acceptance (`-I`, the worktree's script and source) on both rebased trees exited 0, and all **29** checks
  passed, with a byte-identical receipt (9.5 changed no `src`). Receipt SHA256
  `50a64996f6ebac8547edcc22a359f06af206e1dce4630ead90ba6897aa549f66`, source_sha256
  `0204b69b7e192a5d3ef8e31797222c0b46bbb9167e64a8463222e478236d16e1` (equal to `source_hash()` of the clean,
  committed tree), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max
  errors are unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits
  3.35e-8/2.98e-8). `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are
  false. It supersedes 9.1's `5147d6d9…ab68`. (On `3d119c6` it gave `aee29d26…a733`, source
  `1390ef14…9afd`.)

Not verified or still open:
- Live shapes are exercised only with fakes: `RunPodController.status` and `gpu.id` values, real SSH facts,
  HF `siblings` listings, gated-model access, LFS redirects (httpx drops the Authorization header across origins)
  and real tokenizer file sizes.
- HF-root target evidence is not produced yet (9.7). Until then continuation, merge and materialize on HF roots are
  refused at planning, and simulated execution of default (`lora_target: all`) or full/freeze recipes on HF roots
  stops at the dispatcher (the plan warns).
- External artifact inputs stay refused (D16). The directory rename on publish is untested against antivirus locks.
- No Rust, Playwright or UI run: 9.2 changes no TypeScript or Rust.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.5 review fixes, 2026-10-21

Commit `fix: make executor profile storage atomic and keep credential cleanup complete`, on `a308949`. The independent
review of `7e44cc6` and `a308949` was "Ready with fixes" with nothing Critical or Important (legacy freeze, secret
boundary, pipe lifetime and no-arbitrary-execution hold; a replica confirmed EOF on kill). Changed: `profiles.rs`,
`broker.rs`, `main.rs`, `tests/test_native_contract.py`, the plan's F6 count and this file.

What changed:
- m1, atomic store and vault-free lifecycle requests.
  - `write_store` writes `executor-profiles.tmp`, then `fs::rename`s it over `executor-profiles.json`.
  - `std::fs::rename` replaces an existing file atomically on Windows (`MoveFileExW` with
    `MOVEFILE_REPLACE_EXISTING`) and on POSIX. The copied comment that said otherwise was wrong.
  - A failed rename removes the temporary file. `broker.json` is written the same way.
  - `controller_stop` sends `settings: {}`, `profiles: []`, `secrets: {}`. It reads no file and no vault.
  - `controller_status` sends the public settings strings and the served profiles' public fields, as far as those
    files are readable (otherwise `{}`/`[]`). It sends no `<secret>_saved` or `RUNPOD_API_KEY_saved` flag and never
    touches Credential Manager (`VAULT_FREE_ACTIONS`).
  - All other workflow actions are unchanged.
- m2, no orphaned profile keys.
  - (a) `save_profile` restores the profile's previous vault state when the file write fails: a new profile's key is
    deleted, an existing profile's old key is put back.
  - (b) Chosen: writes never drop an entry. Entries the app cannot serve stay as they are, minus any string with key
    content: an invalid provider or ID, a non-object, or a case/trailing-dot twin of a served ID. `forget_credentials`
    therefore still finds their IDs.
  - ID collisions are checked against every stored ID, served or not: a new ID that collides is refused, and only the
    served profile may delete a shared name. A shadowed hand-edited `a` can no longer delete `A`'s Credential Manager
    entry, which is the same entry because Credential Manager ignores case.
  - (c) `forget_credentials` deletes the seven legacy entries first (failing as before). Then, best effort, it deletes
    every profile key the file names: IDs come from any `profiles` object whatever its `schema_version`, and a failed
    delete does not stop the rest.
  - It returns `{"profile_credentials_forgotten": bool, "profile_warning"?: "<Korean text> (<reason>)"}` instead of
    failing, so the window refreshes after "인증값 삭제". It resolves the app folder without creating it.
  - The command's result changed from `null` to that object; `main.ts` only awaits it.
- m3, test gaps.
  - `legacy_request(root, settings, action, data)` builds `factory_action`'s envelope. A test pins its exact bytes
    (`{"action","data","root","settings"}`: serde_json is built without `preserve_order`, so keys go out sorted,
    exactly as before) and that `factory_action` sends nothing else.
  - New tests: a stale vault entry under an SSH profile's ID is never sent or flagged; forgetting continues past a
    failing entry.
- m5, a reason after a failed exit.
  - After the sidecar exits, `broker.json` `{at, code, error}` gets a fixed code and a fixed Korean text:
    `controller_start_refused` for exit 2, `controller_settings_refused` for 4, and `controller_failed` for any other
    non-zero code except 3.
  - Exit 0 (a drained stop) and exit 3 (`owner_held`; the other controller keeps running) write nothing.
  - The child's output is still never read.
- m6: the plan's F6 and the 9.1 test-1 text now say 34 legacy actions.
- 9.2 review: a profile's `ssh_key_file`/`server_ssh_key_file` must be absolute (`Path::is_absolute`, empty allowed
  for a draft), checked on the merged fields at save time with a fixed Korean text. The D11 transport hash resolves
  the key path against the working directory, so a relative path could hash differently in the planning sidecar
  and in the controller and every real run would be refused with `TRANSPORT_CHANGED`. The legacy connection
  settings are unchanged (neither `merge_public_settings` nor the legacy sidecar requires absolute key paths). The
  matching Python refusal (`executor_profile_invalid`) comes with 9.7 and is not pinned in the contract test yet.

Records for Tasks 9.4, 10 and 11 (no code):
- m4: with redirected AppData (`%APPDATA%` on `\\server\…`), `controller_start` always fails with
  "앱 저장 폴더 경로를 백그라운드 실행기에 전달할 수 없습니다." while the legacy flows keep working. This is the no-UNC
  rule of `factory-broker/1`, a known limitation.
- m7: `controller_status` polls no longer read Credential Manager; before this fix, a poll read up to 39 secrets just
  for the flags.
  - Consequence for 9.4: the status envelope has no `*_saved` flags, so `restart_required.tracking` must compare
    public fields (for example, `current.json` also records the public tracking fields and the saved `auth_mode`).
    `public_auth_mode(settings)` would read the missing flags as "none".
  - `restart_required.profiles` can use the public fields sent, because a profile revision does not include the key.
  - The detached broker and sidecar keep `llm-tuning-factory-desktop.exe` and `factory-bridge.exe` open. NSIS update
    and uninstall (Task 11) must stop the controller first (`controller_stop`, then wait for `absent`).
  - Python's `NativeProfile` accepts `estimate-*` and DOS device-name IDs; Rust never sends them.
- Ordering: profile save and delete fail closed until 9.3's `executor_profile_edit_check` lands, and
  `controller_start` until 9.4's `controller_status`. Task 10 comes after 9.3 and 9.4.

Actual verification (worktree `D:\llm-tuning-factory-wt95`; cargo with `CARGO_TARGET_DIR` on the main tree's cache;
main venv with `PYTHONPATH` on the worktree `src`):
- RED first: 9 of the 10 new or changed Rust tests failed for the intended reason. The reasons: the store was rewritten
  in place (shown by a hard link to the old file); a new profile's key was orphaned; unserved entries were dropped;
  forget failed on a corrupt file; `legacy_request` was a stub; lifecycle requests read 9+ vault entries; no
  `broker.json` was written after exit 7 (twice); and a relative key path was accepted. The two gap tests went RED
  against the reviewer's surviving mutations: M2 (`profile_secret` without `is_runpod`) and M5 (forget stopping at
  the first failed delete). The legacy bytes test went RED against M3 (a `profiles` key added to the legacy
  request).
- Mutations, each restored and checked by SHA256. Reviewer M2, M3 and M5, plus: copy instead of rename, lifecycle
  requests reading the vault, no rollback, unserved entries dropped, key content kept in unserved entries, a shadowed
  ID deleting the shared entry, forget failing on a profile-file problem, and no reason after a failed exit. All 11
  failed at least one test.
- GREEN: `cargo test ... --locked --offline --release` → **48 passed** (38 + 10). `cargo check --release` has no
  warning. `tests/test_native_contract.py` with
  `tests/test_desktop_projects.py` → **42 passed, 1 skipped**. Full suite → **1385 passed, 2 skipped** (the same two
  skips; the known `test_jobs` flake did not occur). Ruff clean; `npx playwright test` → **81 passed**;
  `npm run build` passes; the diff check (no `Cargo.toml`, `Cargo.lock`, `containers/`, `pyproject.toml`, `uv.lock`
  or `desktop/src` change) is clean; every changed file is LF only.
- After the rebase onto 9.2 (`7f3a4bc`, PROGRESS conflict resolved by keeping 9.2's entry and adding this one last)
  and the 9.2-review key-path rule: `cargo test` → **48 passed**; contract and desktop-project tests → **43 passed,
  1 skipped**; full suite → **1442 passed, 2 skipped** (9.2's own count on `a308949`); `npx playwright test` →
  **81 passed**; `npm run build` passes; Ruff clean.

Not verified or still open: as in the 9.5 entry (real Credential Manager from the broker, real detached spawn and
job breakaway, the PyInstaller child and the EOF stop: Task 11).

## Task 8.5 review fixes, 2026-10-20

Commit `fix: tie graph claims to their resolved destination and stop transport-retried creates`, written on `1ca866c`
(Task 9.1) and rebased onto `2b16eee` (Tasks 9.5, 9.2 and the 9.5 review fixes landed meanwhile; none touches these
files). It addresses the independent review of `2362144` (8.5): "Ready with fixes", nothing Critical, the core
invariants held under about 20 probes, and two Important issues to close before Task 9 wires the controller into the
broker.

Required:
- **Important 1: a claim could go to another job's destination.** `_claims` opened the iterated job's destination,
  but `claim_ready(observation)` returned the highest-priority queued job that fits it. RunPod's `observation_key`
  ignores the profile, so the jobs of two alias profiles of one Pod both fit.
  - Probe A2 (x pinned to a refused p1, y to its alias p3): x ran through p3's executor and completed.
  - Probe A3 (different runtimes): x was claimed and returned every tick, so the ledger grew without bound and y never
    ran.
  - Fix:
    - `GraphStore.claim_ready(..., *, job_id=None, executor=None)` claims only that job, or only jobs pinned to that
      public executor profile, and writes nothing otherwise. The controller passes the iterated job and its pinned
      profile.
    - The controller also refuses a destination whose `profile` is not the job's pinned one
      (`DESTINATION_PROFILE_DIFFERS`), when claiming and when resuming a taken-over claim.
    - The different-runtime return path (`RUNTIME_DIFFERS`) is gone: no attempt is ever written for another job.
    - Defence in depth: `SettingsExecutorProvider.resolve` stamps `executor.destination_profile = pinned`, and the
      runner's `_check_destination` refuses an executor stamped for another profile (RunPod and SSH). Executors built
      without a provider (tests, v1) carry no stamp and keep the `pod.id`/`server_profile` checks.
  - Tests:
    - `test_a_claim_never_goes_to_another_jobs_destination[same_runtime|other_runtime]`: 6 ticks; x has no attempt,
      disposition, reservation or extra event, and y completes.
    - `test_the_runner_refuses_an_executor_resolved_for_another_profile`, with the real runner and the GPUHost
      executor.
    - Assertions in `test_provider_compares_the_observed_canonical_pod[live|ssh_live]`.
- **Important 2: the MLflow transport re-POSTed creates.** mlflow-skinny 3.17.1 builds its session `Retry` with
  `allowed_methods=None`, so with 2 retries a create the server committed but whose response was lost was sent again
  inside one intent. That could make a second version with the same `factory.registration_key`, which the outbox cannot
  see. `create_model_version` also polled up to 300 s for READY, so 8.5's "about 3 × 60 s" was wrong (corrected in
  place).
  - Fix: `CONTROLLER_HTTP_MAX_RETRIES = 0` (the timeout stays 60 s), so the outbox's durable intent plus search-first
    adoption is the only retry. `create_keyed_model_version` passes `await_creation_for=0`, and adoption already accepts
    a PENDING version (only `FAILED_REGISTRATION` is excluded). The optional search after a create is not added, to
    keep one authoritative search per try.
  - Tests:
    - `test_controller_creates_are_never_resent_by_the_transport`: during every `create_run`/`create_model_version`,
      mlflow's own `MLFLOW_HTTP_REQUEST_MAX_RETRIES.get()` is 0 and the timeout is 60; `await_creation_for=0`; both are
      restored afterwards.
    - `test_a_pending_registration_version_is_adopted`, a guard that already passed.
    - The bound asserted in `test_long_uploads_keep_the_owner_and_the_claim_alive` is now `0`.

Recommended:
- **Minor 1: the integrity scope was wider than the node.**
  - `LedgerBindingResolver` re-verifies producers and persisted targets only for the node's own transitive ancestors
    (`_ancestors`), so a damaged record of another branch no longer holds it.
  - `bindings_for` turns a resolver failure into `LedgerIntegrityError`.
  - At dispatch: the runner now resolves a continuation's evidence before `begin_launch`, and both dispatchers re-raise
    `LedgerIntegrityError` instead of counting it. The controller returns the attempt unlaunched (uncounted) from the
    dispatch thread and shows the integrity wait reason (`needs_operator`). Before each claim it checks the node's
    bindings, so a damaged lineage holds the node without any attempt.
  - Tests: `test_integrity_checks_are_scoped_to_the_nodes_own_lineage` (unrelated damage: b runs and e waits; at
    dispatch through the simulated dispatcher: one attempt, `returned_unlaunched` uncounted, then no further claim).
    The 8.2 test `test_inspected_targets_are_verified_and_persisted` now checks the resolver on a node downstream of
    `full` and that another branch is unaffected.
- **Minor 2: `_bounded_requests` was process-global and not thread-safe.** It now takes a module lock with a
  reference count: the first caller saves and sets the variables, and the last one restores them.
  `test_bounded_requests_nest_across_threads`.
- **Minor 3: the started-PROVISIONING fallback was not atomic.** The new `GraphStore.settle_stale_launch` (`released`,
  `uncertain` or `unchanged`) rotates the token and then releases, or marks FAILED with the lock kept, in one
  transaction. An actor that passed RUNNING meanwhile is left untouched. `release_uncontacted` keeps its boolean
  contract on the same helper. `test_a_stale_started_launch_is_settled_in_one_transaction`.
- **Minor 4: one unreadable run broke every status.** `graph_status` now isolates each run: an unreadable one is
  reported as `{"run_id", "error": "unreadable graph run: …"}`. A requested unknown run still raises.
  `test_one_unreadable_run_never_hides_the_others`.
- **Minor 5: a BaseException from a dispatch future escaped without releasing the owner.** `run_forever` now stops the
  tracking thread, shuts down its own pool and releases the owner lease (unless fenced) in a `finally`, then lets the
  exception propagate. `test_a_dying_dispatch_thread_still_releases_the_owner`.
- **Minor 6: `needs_operator` was set as soon as an attempt became uncertain.**
  - New node fields `awaiting_result` and `result_deadline`: the attempt's claim time plus its operation's
    `timeout_seconds` plus `RESULT_MARGIN_SECONDS` (600).
  - `needs_operator` is set for an uncertain attempt only without a remote record, or once that deadline has passed.
  - Wait reasons: `AWAITING_RESULT` no longer suggests `release-lock`; `RESULT_OVERDUE` does, after the deadline;
    `OPERATOR_CONFIRMATION` is used without a remote record.
  - `test_ambiguous_launch_waits_for_the_operator` now checks before and after the deadline.
- **Minor 7: raw error text was stored unredacted.**
  - `JobStore.error_filter` (installed by the controller as `GraphController.redact`) filters every error that
    `transition`, `abandon_unlaunched` and `reconcile_result` store. Because `classify_attempt` and `nodes.error` copy
    `jobs.error`, they inherit the redaction.
  - The runner applies it to the accepted `<launch_id>.result.json` error and to `error.txt`; the simulated dispatcher
    applies it to `error.txt`.
  - `GraphController.redact` combines the providers' `redact` (new `SettingsExecutorProvider.redact`: values of
    settings named like keys, tokens, secrets, passwords, passphrases or credentials; `SimulatedExecutorProvider.redact`
    is the identity) with `tracking_outbox.redact` (MLflow's configured secrets, bearer/basic, `secret=` pairs, URL
    userinfo).
  - Tests: `test_secrets_the_controller_knows_never_reach_the_ledger` (the DB dump, the state files and the fake
    MLflow uploads never contain the synthetic secret), `test_the_runner_redacts_results_and_error_files` and
    `test_the_provider_redacts_the_secrets_it_holds`.
  - Remaining for Task 9: remote logs (`train.log`, `worker.log`) uploaded as lineage, and executor exception text that
    never reaches these paths, are not filtered.
- **Minor 8:**
  - A refused or unreachable destination is not reopened for `ControllerSettings.destination_retry_seconds` (60).
  - Finished runs are pruned from `_reasons`, `_simulated`, the backoff map, `_outcomes` and `_reconciled_at`.
  - `_inflight` is guarded by a lock for `status()` callers on other threads.
  - A non-`ValueError` while returning a refused launch is logged and never hides the refusal; harvest returns the
    attempt later.
  - A failing directory fsync after the hard link is logged, so the published result stands.
  - Heartbeat warnings (the controller's, the runner's and the simulated dispatcher's) are logged at the first failure
    and then every 100th.
  - Tests: `test_refused_destinations_back_off_and_finished_runs_are_forgotten`,
    `test_a_failed_return_never_hides_the_refusal`, `test_a_failing_directory_fsync_keeps_the_published_result` and
    `test_heartbeat_warnings_are_rate_limited`.

New `graph_status` fields for Task 9/10:
- Each node gains `awaiting_result` (bool: a late result is still plausible) and `result_deadline` (epoch seconds, or
  null).
- `needs_operator` follows the rule above.
- An unreadable run appears as `{"run_id", "error"}` with no other keys.
- The other fields are unchanged; see "Task 8.5 graph controller and end-to-end recovery".

Deviations:
- `claim_ready` gains two keyword-only filters, which default to the old behaviour.
- `JobStore.error_filter` is a new class attribute, set per instance by the controller.
- `GraphStore.settle_stale_launch` is new.
- Files: `jobs.py` (the filter) and `tests/test_graph_readiness.py` (the scoped-resolver assertion) were edited for
  these items. `desktop_workflows.py`, `workflow_planning.py`, `projects.py`, `graph_planner.py` and `desktop/` were
  not touched.

Actual verification (main tree; RED on `1ca866c`, GREEN and acceptance on the tree rebased onto `7f3a4bc` and
again onto `2b16eee`):
- RED, on a `git archive` export of `1ca866c` with the new test files copied in: 23 cases failed for the intended
  reasons, and the PENDING-adoption guard passed.
  - x claimed through p3, with 1 or 6 attempts;
  - the runner completed through an alias executor;
  - retries were 2 during creates, and `CONTROLLER_HTTP_MAX_RETRIES == 2`;
  - b was held by an unrelated damaged artifact;
  - `settle_stale_launch` was missing;
  - a `ValidationError` broke the whole status;
  - the owner was not released after a dying future;
  - the secret was in the DB dump;
  - 4 resolves in 40 s;
  - the result error was not redacted;
  - 40 heartbeat warnings;
  - the refusal was hidden by `database is locked`;
  - `awaiting_result` was missing;
  - `destination_profile` and `redact` were missing;
  - the fsync `OSError` escaped;
  - the 8.2 resolver test.
- GREEN:
  - `uv run pytest tests/test_graph_controller.py tests/test_tracking_outbox.py -q`: **91 passed (59 + 32)**.
  - The step-4 regressions with the 8.1–8.4 sets: **533 passed**.
  - `uv run pytest -q` twice: on `1ca866c` **1396 passed, 1 skipped** both times (1379 + 17 new); after the rebase
    onto `7f3a4bc`, and again after the rebase onto `2b16eee`, **1459 passed, 2 skipped** both times (1442 + 17 new).
- Ruff (`src tests`) is clean. `check_secrets.py` reports 0 findings over 821 file versions (every change
  staged). Both GPU locks are unchanged, `git ls-files --eol` shows no CRLF, and every changed file is CR-free.
- CPU acceptance (`-I`) exited 0, and all **29** checks passed. Max errors are unchanged.
  - On the rebased tree: receipt SHA256 `6524aba8f6cc9411e2288c3036f3918902996f50495329146e3b46d51a365c1f`,
    source_sha256 `b6547200cd504b815d110bb230efa4bbd9067c8bfb87fdb544726da7cd69c3a3` (equal to `source_hash()` of a
    `git archive` export of the commit), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
    Before the rebase it gave `3609afe9…7b02` (source `f152b16f…6466`).
  - It supersedes 9.2's `50a64996…9f66`.

Remaining, for Task 9:
- The controller must own its process: the bound on creates sets process-wide environment variables while a create
  runs.
- `resolve` and `recover` need timeouts. The heartbeat keeps a hung tick looking `running`.
- Reconcile runs inline on the tick thread, so a long result download stalls scheduling for that time.
- Remote log uploads and executor exception text outside the filtered paths are not redacted (Minor 7 remainder).
- Executors built outside `SettingsExecutorProvider` carry no `destination_profile` stamp; the controller's own profile
  check covers them.

### 8.5 re-review Minors, 2026-10-21

Commit `fix: anchor result deadlines on launch and back off replan waits`, on `02436c8` (9.2 follow-ups and 9.7). The
re-review of `b0d2c90` was "Ready to merge: Yes", which completed Task 8 in software, and left five Minors:
1. **`result_deadline` was anchored on the claim time.** Probe G: an attempt resumed an hour after its claim, launched
   and left uncertain, showed `RESULT_OVERDUE`/`needs_operator` at once, and so did long input uploads.
   - It is now anchored on the attempt's latest job event: the FAILED that made it uncertain.
   - That is never before the remote launch. The actor uploads its inputs and launches while RUNNING, and the attempt
     fails only after its actor stopped (the runner gave up on a lost response, or a stale recover after its last
     heartbeat). Resumed claims and slow uploads therefore never shorten the time limit, and no separate transfer
     allowance is needed.
   - The anchor is conservative: it can be later than the launch by up to the stale-detection time, so the operator
     may wait somewhat longer.
   - Tests: `test_the_result_deadline_starts_when_the_attempt_became_uncertain[resumed_claim|slow_upload]`. Test 10
     now asserts the new anchor.
2. **REPLAN waits re-opened the destination and re-hashed the lineage every tick.** Probe F: 6 resolves and 6 full
   re-hashes in 6 ticks.
   - A claim that returns None for REPLAN (a live price or GPU that differs) now backs off for
     `destination_retry_seconds`, like a refusal.
   - The pre-claim lineage check runs only where a dispatcher re-resolves bindings before the launch: every
     non-evaluation in a simulation, and only an adapter continuation for the real runner.
   - A failed check is held for the same period instead of being re-hashed every tick.
   - `test_replan_waits_back_off_and_never_rehash_the_lineage`: 1 resolve in 6 ticks, at most b's compile plus one
     check, none for a root training, and one more resolve after the period.
3. **The controller did not check its dispatcher's store.** `GraphController` now refuses at construction, with
   `DISPATCHER_STORE_DIFFERS`, a dispatcher (or a router's real or simulated dispatcher) whose `graph`, `store` or
   `jobs` is not its own `store`/`store.jobs`; otherwise the error filter would silently not apply. The filter is
   installed on the store's `JobStore` instance, so the last controller constructed on a store wins.
   `test_the_dispatcher_must_use_the_controllers_store`.
4. **`await_creation_for=0` acknowledges PENDING versions.** No code change, recorded for Task 9: if the server later
   marks such a version `FAILED_REGISTRATION`, `registration_status` still says `registered`. A later check of the
   version's status (or a re-arm path for registrations) is Task 9's to decide.
5. The PROGRESS typo `6524aba8…1c5f` is corrected to `…5c1f`.

Verification (main tree, on `02436c8`):
- RED, on a `git archive` export of `02436c8` with the new tests copied in: the 4 new cases failed for the intended
  reasons:
  - both deadline cases were overdue at once (`awaiting_result` False, `needs_operator` True);
  - the root training's lineage was checked before its claim (2 calls instead of 1);
  - `DISPATCHER_STORE_DIFFERS` was missing.
- GREEN: `test_graph_controller` + `test_tracking_outbox`: **95 passed**. `uv run pytest -q`: **1557 passed, 2 skipped (1553 + 4 new)**.
- Ruff is clean. `check_secrets.py` reports 0 findings over 826 file versions. Both GPU locks are unchanged, and
  `git ls-files --eol` shows no CRLF.
- CPU acceptance (`-I`) exited 0, and all 29 checks passed. Max errors are unchanged.
  - Receipt SHA256 `b4cc02a237003c763f9dd4421d1e61bd1adbab20fb86d526c336fe42d1d2c6d4`, source_sha256 `2f5200b0bbf8c9311748ab5c425b94a8479bda02a114a1a732fdde53e9f5457c` (equal to `source_hash()` of a `git archive` export of the
    commit), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
  - It supersedes 9.7's `cbad9755…2f8d`.

## Task 9.7 header-only HF-root target inspector, with the 9.2 review follow-ups, 2026-10-21

Two commits, written in the worktree `codex/task-9.7` on 9.2 (`7f3a4bc`):
`test: pin D15 lineage branches and planning gates; tighten planning inputs` (the 9.2 review follow-ups, first, so
they land before D15 is lifted), then `feat: inspect HF base targets from safetensors headers`. Both were then
rebased onto the 9.5 review fixes (`2b16eee`) and onto the 8.5 review fixes (`b0d2c90`). Only
`tests/test_native_contract.py` (9.5's new `VAULT_FREE_ACTIONS` assertions are kept beside the Python-only
declaration) and this file conflicted. After 8.5's fix the end-to-end test claims with `job_id` and `executor`, as
the controller now does. They implement
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.7" (root decision Q-A: required
before Task 10) and the review of 9.2 (ready to merge; Minor items only).

9.2 review follow-ups (first commit, `workflow_planning.py` and its tests only):
- Minor 1, tests for behaviour that mutations showed untested: six new tests (9 cases; the repository test has
  4 parameters) pin 8 mutations, each killed by the test that targets it (the D15 test kills 3):
  - The D15 walker: continuation through a selection of HF-root adapters, and from a merge output, is blocked
    without evidence; training after materialize is not.
  - A negative observation age is `observation_stale`; `repository_unsupported`; the `root_snapshot_conflict`
    reuse check; estimate collisions compare casefolded IDs; tokenizer names that collide by case are refused.
- Minor 2: `public_profile`, `executor_profile_inspect` and planning refuse a relative `ssh_key_file` or
  `server_ssh_key_file` with `executor_profile_invalid` (`check_key_paths`), before the D11 transport hash would
  resolve it against the working directory.
- Minor 3: `model_root_snapshot` maps `TypeError`, `AttributeError` and `KeyError` to `snapshot_failed` with the
  type name, instead of escaping the issue envelope.
- Minor 5: `benchmark_save` applies `training.check_generation_benchmark` (`benchmark_unsupported`, the worker's
  exact text).
- Minor 6: a failed remote probe (`RemoteCommandFailed`) is `remote_command_failed` with a fixed text and no
  remote output. The exception carries no exit status, so none is reported.
- Optional nits done: snapshot downloads refuse a redirect to plain HTTP before it is sent (`insecure_redirect`,
  shared hook `https_only`); inspection closes the RunPod controller's HTTP client. Not done: an index on
  `(namespace, revision_sha256)` for `get_by_revision` (a schema change, not trivial).

What 9.7 changed (second commit):
- New `hf_targets.py` with the action `model_root_targets {repo_id, revision, download_approved}` (secret scope
  `HF_TOKEN`). It requires `download_approved: true`, refuses repository names Windows would alter, and needs the
  root indexed by `model_root_snapshot` (`model_root_missing`; two index entries for one `repo@revision` are
  `model_root_invalid`). All of these are checked before any request.
- It reads `model.safetensors.index.json` (HTTP 404 means a single `model.safetensors`) through the resolve URLs of
  the pinned revision, then makes exactly two range requests per shard: `bytes=0-7`, then the header.
  - A range must be answered `206` with exactly the requested `Content-Range` and length. A `200` is closed with its
    body unread. A `Content-Encoding` is refused (`Accept-Encoding: identity`), and so is a total size that changes
    between the two requests.
  - Caps: header ≤16 MiB (checked before the second request), index ≤16 MiB, a redirect body declared ≤64 KiB
    (httpx reads redirect bodies before following), at most 256 shards and 5 redirects, and 64 MiB in total
    (checked before each request).
  - Every hop must be HTTPS. httpx drops the Authorization header on a cross-origin redirect; the `Range` header
    is kept.
- Headers are validated by `model_artifacts.header_tensors`, a pure extraction from `tensor_header`, so local
  files and HTTP reads share one rule set. The index must name exactly the tensors each shard holds.
- The evidence is the rule `verify_inspected_targets` applies to full-model artifacts: 2D `*.weight` tensors kept by
  `target_inspection._not_a_target`, with `(out, in)` dimensions. It is stored through `save_root_targets`.
  - A repeat call with identical evidence is idempotent.
  - Different evidence for the same pinned root is `conflict`/`version_exists`, and the stored file is unchanged.
  - A damaged stored file is `root_targets_invalid`, refused before any request.
- Refused, not guessed (`architecture_unsupported`, one fixed text):
  - Profiles outside the reviewed dense text architectures (`qwen2`, `qwen3`, `llama`, `mistral`, `gemma2`,
    `gemma3_text`, `phi3`) are refused before any request.
  - So is any tensor with three or more dimensions, any `experts` tensor, a non-float target weight, and a
    `.qweight`/`.weight_packed` tensor.
- Errors never echo a server reply or the token: fixed texts or 9.2's English HTTP texts, and the type name for
  anything unexpected (`TypeError`, `AttributeError` and `KeyError` included, per Minor 3).
- `desktop_workflows.py` (the one allowed change): `model_root_targets` appended to the action table, mapped to
  `tuning_factory.hf_targets`, with secret scope `{HF_TOKEN}`.

Deviations:
- **The native allowlist does not offer `model_root_targets` yet.** `desktop/` stays frozen for 9.7, but
  `test_native_contract` pins the Rust `WORKFLOW_ACTIONS` and scopes to the Python table. The test now declares
  `PYTHON_ONLY = ("model_root_targets",)`: native must equal the Python table minus that name, and every other
  name and scope must still match exactly. The GUI can call the action only after `main.rs` adds it to
  `WORKFLOW_ACTIONS` and `HF_SCOPED_ACTIONS`; the CLI and tests can call it now. `test_desktop_projects` pins the
  table at 33 names.
- `model_artifacts.py` (not in the brief): the extraction of `header_tensors`, with no behaviour change.
- The architecture allowlist goes beyond the brief's structural refusals. Primary `transformers==5.6.0` fuses or
  renames MoE, hybrid and multimodal checkpoint tensors at load, so their headers are not the runtime module
  inventory.
- `check_key_paths` allows `~` (expanded per user, as the D11 hash already does). Native `profiles.rs` (9.5 fix)
  is stricter and refuses it.
- A top-level `score.weight` is kept as a target: `_not_a_target` excludes only `score.`-prefixed modules. This is
  the verify rule as written, not changed here.
- The comment "the two scoped actions" in `desktop_workflows.dispatch_action` is now stale (three actions get a
  scope); it was left as the brief forbids other changes there.

Actual verification (worktree `D:\llm-tuning-factory-wt97`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree):
- RED, before any 9.7 `src` edit: **69 failed**. The 67 cases of `tests/test_hf_targets.py` failed on the missing
  module (`pytest.fail`, not collection), the table pin on the missing name, and the native contract on the
  undeclared Python-only action. Added later, test-first: `shifted_range`, `ambiguous_root` and a reversed index
  order (each killing a surviving mutation), and the three Minor 3 envelope cases (red without the wider catch).
- 9.2 follow-ups RED: the 13 behaviour cases failed on unpatched code, and the 9 pin cases (6 tests) failed
  against the 8 mutations they target. GREEN: `test_workflow_planning` gave **78 passed**, and the full suite on that commit gave
  **1464 passed, 2 skipped** (1442 + 22).
- Mutations of `hf_targets.py` through a harness under `%TEMP%` (each restored): **33 of 33 killed**. They covered
  reading a 200 body, the Content-Range equality and exact length, the identity check, each refusal rule, the
  allowlist, the total-size consistency, the header cap, the budget, the HTTPS hook, the redirect bound, an
  unconditional Authorization header, shard name safety, index consistency, `_not_a_target`, swapped dimensions,
  the conflict, the early stored-evidence check, echoed errors, approval, repository names, zero and oversized
  header lengths, the index cap, the shard count, the `.safetensors` suffix, the 404 fallback, shard order and
  the ambiguous root.
- GREEN: `test_hf_targets` (72), `test_workflow_planning`, `test_desktop_projects`, `test_simulated_dispatch`,
  `test_model_artifacts` and `test_graph_planner` gave **349 passed**. The full suite gave **1536 passed,
  2 skipped** (1464 + 72); the known flake did not occur. Rebased onto `2b16eee`: the same set plus
  `test_native_contract` gave **355 passed, 1 skipped**, and the full suite **1536 passed, 2 skipped**. Rebased onto
  `b0d2c90` (the final tree): **356 passed, 1 skipped** and the full suite **1553 passed, 2 skipped** (8.5's 1459 +
  22 + 72). The known `test_jobs` flake did not occur in any run. Ruff is clean. `check_secrets.py` reports 0 findings
  with the change staged. `git diff --exit-code containers/ pyproject.toml uv.lock desktop/` is clean. Every
  changed file is CR-free. A subprocess test shows `hf_targets` imports no torch, peft, transformers,
  safetensors, huggingface_hub, keyring, paramiko or mlflow.
- End to end, with fakes: `model_root_snapshot`, then `model_root_targets`, then a simulation `graph_plan`.
  - The default `lora_target: all` train on the root warned before targets and plans cleanly after them, with
    `base_targets == (evidence,)`. A continuation, merge and materialize graph on the root went from
    `base_targets_missing` to planned.
  - The plan was submitted to a `GraphStore` and claimed through `SimulatedExecutorProvider.for_plan`. The
    `SimulatedOperationDispatcher` (root snapshots from `root_snapshots_dir`) completed the train, and the adapter
    covers every evidence module with its dimensions.
- CPU acceptance (`-I`, the worktree's script and source) exited 0 with all **29** checks passed on every tree. Before
  the rebases (and on `2b16eee`, byte-identical) the receipt was `ee2f52e1…d6f2`, source `6694651d…fc50`. On the final
  tree (`b0d2c90` plus both commits): receipt SHA256
  `cbad9755e3ff210c1f0e5b0e7fe673057abba7d7d8d1aff6ae3d88b48c3d2f8d`, source_sha256
  `3b1f546a0a827938d217c336d2e30e9902bed4ca66f2c05e514e0c99382a1631` (equal to `source_hash()` of the committed
  `src`), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max errors are unchanged
  (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8). `model_downloaded`,
  `gpu_used`, `institution_data_used` and `production_promotable` are false. It supersedes 8.5's review-fix receipt
  `6524aba8…5c1f`.

Not verified or still open:
- Real Hugging Face behaviour is exercised only with MockTransport and synthetic headers: range support on the
  resolve URLs and on the LFS/Xet CDN, the `Content-Range` and `Content-Length` it sends (including on redirects),
  HTTP 404 for a missing index, gated-model access with `HF_TOKEN`, and real checkpoints of the allowed
  architectures (tied embeddings, biases, `q_norm`/`k_norm`, phi3's fused layers).
- That header-derived module names equal the runtime module names of `transformers==5.6.0` for the allowed
  architectures is a reasoned assumption, not a run. The meta-device inspector was not compared on real configs.
- The GUI cannot call `model_root_targets` until `main.rs` lists it (see the first deviation).
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.3 runs: submission, status and steering, 2026-10-21

Commit `feat: submit and steer graph runs from the desktop with explicit approvals`, written in the worktree
`codex/task-9.3` on `b0d2c90` (the 8.5 review fixes). It was rebased onto `02436c8` (Task 9.7 and the 9.2 planning
follow-up `8931b1f`), then onto `960c99e` (the 8.5 re-review Minors). Only this file conflicted: every entry is
kept, this one last, and only the receipt pointer moved. It implements
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.3" with D5, D9, D11 and D21.
Changed: new `src/tuning_factory/workflow_runs.py`, new `tests/test_workflow_runs.py` and this file.

What changed:
- New `workflow_runs.py`. Its eight actions register through 9.1's closed table: `graph_submit`, `graph_runs_list`,
  `graph_run_get`, `graph_pause`, `graph_resume`, `graph_cancel`, `tracking_rearm` and `executor_profile_edit_check`.
  It also has `staleness`, `profile_usage` and `submissions_dir`. `desktop_workflows.py` is unchanged.
- `graph_submit` (D9):
  - It loads the stored plan by its hash (`plan_unknown`, or `plan_invalid` when it no longer verifies).
  - It re-checks freshness; any change is `conflict`/`plan_stale` and creates no run. The project revision is checked
    at `/project` for every plan. A real plan also checks each pinned profile's current native revision at
    `/profiles/<id>` (changed or removed) and the MLflow destination hash at `/tracking`.
  - A real plan needs both `approve_execution` and `approve_data_transfer` (strict booleans; each missing one is an
    `approval_required` issue at its path). It also refuses practice data (`PRACTICE_SUBMIT_REFUSAL`) and
    non-exportable data used by any train or evaluate node: `practice_data` or `export_not_allowed` at
    `/plan_sha256`, with the node ID. This is defence in depth for plans stored by older code.
  - It builds `ApprovalGrant.for_plan(plan)` and calls `GraphStore.submit`, which is idempotent by request ID
    (`request_reused` at `/request_id`). It writes the immutable, public receipt
    `state/workflows/submissions/<run_id>.json` and returns `{run_id, status, simulation, plan_sha256, controller}`.
    It never starts the controller.
- `graph_runs_list` and `graph_run_get` read `graph_controller.graph_status` only, with the 8.5 fields:
  `needs_operator`, `awaiting_result`, `result_deadline`, `budget_exhausted`, `unpriced_attempts`,
  `tracking_action`, and `{run_id, error}` for an unreadable run. `graph_run_get` adds
  `plan_summary(plan, state_dir)`, which includes the hash-verified public tracking settings.
- `graph_pause`, `graph_resume` and `graph_cancel` call `GraphStore` directly. Its fixed refusals become
  `conflict`/`run_state` at `/run_id`, and an unknown run is `invalid`/`run_unknown`. `tracking_rearm` calls
  `TrackingOutbox.rearm` (`conflict`/`tracking_state`, `invalid`/`tracking_event_unknown`).
- `executor_profile_edit_check` (D5, 8.4 review Minor 5) follows 9.5's Rust contract. `{"profile_id"}` comes in.
  The answer is `conflict`/`profile_in_use` with `data.jobs` (`[{run_id, node_id, job_id, status, locked}]`), or
  `ok` with `data.affected_runs`. Profile IDs compare by casefold plus `rstrip(".")`. This unblocks the native profile
  save and delete, which refuse until the check answers.
- D21: no action releases an operator lock. `graph_run_get` shows `needs_operator` and the wait reason.

Deviations from the plan:
- Simulation plans skip the profile and tracking freshness checks (9.2 review Minor 4). They pin a profile as
  observed and never contact it: they route to `SimulatedExecutorProvider` and the local sink. Their project
  revision is still checked. Real plans keep all three checks.
- `graph_run_get` also returns `tracking_failures`: the run's FAILED or UNCERTAIN events, each
  `{event_id, kind, node_id, status, tries, last_error, action}`. Without it, no event ID would reach the desktop or
  the CLI for `tracking_rearm`. `last_error` passes through `graph_controller._display`, as wait reasons do.
  `action` is `EVIDENCE_CHANGED_ACTION` for changed evidence.
- `tracking_rearm` also re-arms a changed-evidence event, because the operator may have restored the files. It then
  adds the warning `tracking_evidence_changed` with `EVIDENCE_CHANGED_ACTION`.
- Pause, resume and cancel return `{run, controller}`: the run's status entry and the controller block.
- `affected_runs` lists only RUNNING or PAUSED **real** runs whose approval pins the profile. A simulation uses its
  plan's own observations, never the native profile, so an edit needs no re-plan.
- `profile_in_use` covers every graph job pinned to the profile, simulated ones included (D5 as written). It counts
  `resource_leases` as well as `gpu_locks`. A lock-holding or active job whose pinned profile cannot be read counts
  too (fail closed).
- Reads never create `jobs.db`. Without it, `graph_runs_list` returns no runs and the absent controller block
  (`NO_CONTROLLER`, pinned against `graph_status` by a test); run reads and steering answer `run_unknown`;
  `tracking_rearm` answers `tracking_event_unknown`; and the edit check answers `ok` with no affected runs.
- The receipt is written once. A retry with other approval flags (possible only for a simulation) keeps the first.
- Freshness is checked before idempotency. Retrying an existing request after the project, a pinned profile or the
  tracking settings changed is `plan_stale`. No new run is made, and the existing run stays listed.
- New codes: `plan_invalid`, and the warning `tracking_evidence_changed`. `ProfileEditCheck.profile_id` is limited
  to 64 characters, like `NativeProfile`.

Actual verification (worktree `D:\llm-tuning-factory-wt93`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree):
- RED, before any `src` edit: all 28 first cases failed on the missing module (`pytest.fail`, not collection).
- Mutations through a harness under `%TEMP%` (each restored after its run, byte-checked): 20 source mutations, all
  killed. They covered the real profile check, the simulation skip, the project check, each approval, the data
  checks (practice, export, the evaluate branch), the tracking check, `_display` of tracking errors, the evidence
  action and warning, locks versus active statuses, casefolding, simulations in `affected_runs`, PAUSED runs, reads
  creating the ledger, the `/request_id` pointer, the receipt flags and the fail-closed unreadable job. The
  first harness run left the unreadable-job mutation alive, so a case was added for it; the evaluate-branch case was
  added for a branch no test reached. Each new case was shown to fail under its mutation.
- GREEN on `b0d2c90`: `test_workflow_runs`, `test_workflow_planning`, `test_graph_store`, `test_tracking_outbox`,
  `test_graph_controller`, `test_desktop_projects` and `test_native_contract` gave **251 passed, 1 skipped** (the
  skip waits for 9.4's controller entry). The full suite gave **1487 passed, 2 skipped** (1459 + the first 28
  cases; the evaluate-data case was added after that run).
- Rebased onto `02436c8`: the same files gave **274 passed, 1 skipped**, and the full suite gave **1582 passed,
  2 skipped** (9.7's 1553 + 29). The CPU acceptance there gave receipt `7e7cd366…c697` (source `28e1e855…1c23`).
- Rebased onto `960c99e` (the final tree): the same files gave **278 passed, 1 skipped**, and the full suite gave
  **1586 passed, 2 skipped** (the re-review's 1557 + 29; the same two skips). Ruff (`src tests`) is clean.
  `check_secrets.py` reports 0 findings with the change staged. `git diff --exit-code containers/ pyproject.toml
  uv.lock desktop/` is clean, `git ls-files --eol` shows `i/lf w/lf`, and every changed file is CR-free. A subprocess
  test shows `workflow_runs` imports no torch, peft, transformers, safetensors, huggingface_hub, keyring, paramiko or
  mlflow.
- CPU acceptance (`-I`, the worktree's script and source) on the final tree exited 0, and all **29** checks passed.
  Receipt SHA256 `00d0043b1c8cf8cff2757205dc935ada7947ebf6d2c8f5b05c708cd098ff7f93`, source_sha256
  `ef4e1357997c523b78d43401235c8535c726cd3d3be618d7c1beed10addc4ff1` (equal to `source_hash()` of the committed
  `src`), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max errors are
  unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8).
  `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false. It supersedes the
  8.5 re-review's `b4cc02a2…c6d4`.

Not verified or still open:
- No controller ran in these tests (9.4 owns the controller entry). Runs were claimed only through `GraphStore` to
  hold a lock for the edit check.
- The native save and delete were not run against a live sidecar. The shapes are pinned by 9.5's Rust tests and by
  these Python tests. No Rust, Playwright or UI run: 9.3 changes no Rust or TypeScript.
- Live shapes stay unverified: RunPod, SSH, HF and MLflow are exercised only with fakes.
- The 8.5 re-review's note 4 (a PENDING model version that MLflow later marks `FAILED_REGISTRATION` still shows
  `registered`) is not addressed: re-checking a version needs an MLflow read, and 9.3's actions are DB-only. It
  stays open for the controller side (9.4) or a later task.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.7 review fixes: native exposure and hardened header reads, 2026-10-21

Commit `fix: expose HF target inspection natively and harden header reads`, written in `D:\llm-tuning-factory-wt95`
(branch `codex/task-9.7-fix`) on `02436c8`, then rebased onto the 8.5 re-review fixes (`960c99e`, no conflict)
and onto 9.3 (`c2f1bbb`; only this file conflicted, every entry kept), then onto the Task 10 plan (`9f2927d`, docs
only). The independent review of 9.7 (`02436c8`) and of the 9.2 follow-ups (`8931b1f`) was "Ready with fixes":
nothing Critical or Important, and every reported number reproduced.

Required before Task 10:
- **Native exposure (9.7 deviation 1 resolved).** `main.rs`: `WORKFLOW_ACTIONS` is `[&str; 33]` with
  `model_root_targets` last (the Python order), and `HF_SCOPED_ACTIONS` is `["model_root_snapshot",
  "model_root_targets"]`. The comments are updated.
  - Rust tests: the closed list has 33 unique names. Both HF actions carry exactly `{"HF_TOKEN": hf}` and no other
    secret, and `{}` when no HF token is saved.
  - `test_native_contract` no longer has `PYTHON_ONLY`: `actions == wf.WORKFLOW_ACTIONS`, 33 names, and
    `native_scopes == dict(wf.SECRET_SCOPES)`.
  - The stale comment in `desktop_workflows.dispatch_action` and the plan's D8, §5 and §7 now say 33 names and
    three scoped actions.
- **Minor 1: `RecursionError` escaped the envelope.** `model_artifacts._json` turns it into
  `ValueError("invalid artifact JSON metadata")`, for local files too. 200,000 `[` give `header_invalid` or
  `index_invalid` over HTTP, and `tensor_header` and `read_json` raise that `ValueError` locally.
- **Minor 2: header names must be runtime names.** `target_evidence` refuses (`architecture_unsupported`) any
  tensor outside `model.` or `lm_head.` (`CAUSAL_LM_PREFIXES`, the `base_model_prefix` of all seven allowed
  architectures). This covers prefix-less checkpoints saved from a bare `XModel`, a classifier's top-level
  `score.weight` and a value head.

Recommended, also done:
- **Redirects** (Minor 3 and the real-HF risk).
  - `_open` follows at most 5 redirects itself. Each redirect is closed with its body unread, so a missing
    `Content-Length` is no longer refused and redirect bodies cost nothing against the 64 MiB total.
  - The next hop is httpx's public `next_request`: it drops Authorization across origins and keeps `Range`.
  - Every hop still passes the HTTPS hook before it is sent. More redirects give `targets_failed`
    (`TOO_MANY_REDIRECTS`).
  - `_bounded_redirect` and `MAX_REDIRECT_BYTES` are gone.
- **Minor 4:** `_index` refuses a declared `Content-Length` above the cap before reading; undeclared sizes still
  stream capped.
- **Minor 5:** a checkpoint without one target weight, or a target with a zero dimension, is
  `architecture_unsupported`, not `header_invalid`.
- **Minor 6:** `test_snapshot_errors_stay_in_the_issue_envelope` has a `KeyError` case. The count wording in the 9.7
  entry above now says: 9 pin cases in 6 tests against 8 mutations.
- **Minor 7: key-path gaps.**
  - `check_key_paths` applies `desktop_workflows.local_file` (after `~`), so UNC and device paths (`\\server\share`,
    `//server/share`, `\\?\`, `\\.\`) are refused.
  - A `~` without a resolvable home is `KEY_PATH_RELATIVE` (`ValueError`) instead of an escaping `RuntimeError`.
  - `profiles.rs` also refuses any key path that starts with two separators (UNC, `\\?\`, `\\?\UNC\`, `\\.\`), and
    still refuses `~`. Python keeps accepting `~`, which the native side never sends.

Deviations:
- Rust coverage was added to the two existing tests the review named and to `profile_key_paths_must_be_absolute`,
  not as new test functions, so `cargo test` still reports 48.
- Python's key-path rule is `local_file`'s, so it also refuses DOS device names and a `:` after the drive. The Rust
  rule refuses the double-separator prefix only (Windows file dialogs never produce the other forms).

Actual verification (main venv with `PYTHONPATH` on the wt95 `src`; `tuning_factory.__file__` resolved into wt95;
`CARGO_TARGET_DIR` on the main tree's target):
- RED, before any source edit:
  - Python: **17 failed**, each for its intended reason: the native contract (Rust still had 32 names), five
    architecture cases, the endless-body redirect, both redirect refusals, the declared index size, both nested-JSON
    cases (`RecursionError` escaped), four UNC/device key paths and the unresolvable home.
  - The new `KeyError` snapshot case passed, because the catch already existed. It failed against the mutation that
    removes `KeyError` from that catch.
  - `cargo test`: **45 passed, 3 failed**: the closed list (32), the HF secret scope (`model_root_targets` not
    allowed) and the UNC/device key paths.
- GREEN:
  - `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release`: **48 passed**.
    `git diff --exit-code desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock` is clean.
  - `test_hf_targets` (80), `test_workflow_planning`, `test_native_contract`, `test_model_artifacts` and
    `test_desktop_projects`: **295 passed, 1 skipped**.
  - Full suite: **1567 passed, 2 skipped** (1553 + 14). The known `test_jobs` flake did not occur.
  - From `desktop/`: `npx playwright test` **81 passed**, and `npm run build` passes.
  - Rebased onto `960c99e`: the same Python set **295 passed, 1 skipped**, the full suite **1571 passed,
    2 skipped** (8.5's 1557 + 14), `cargo test` **48 passed**, Playwright **81 passed** and `npm run build` passes.
  - Rebased onto `c2f1bbb` (the final tree): the same set plus `test_workflow_runs` **324 passed, 1 skipped**, the
    full suite **1600 passed, 2 skipped** (9.3's 1586 + 14), `cargo test` **48 passed**, Playwright **81 passed**
    and `npm run build` passes. On `9f2927d` (docs only, the final tree) the full suite again gave **1600 passed,
    2 skipped**; `source_hash()` is unchanged, so the receipt below stands. The known `test_jobs` flake did not
    occur in any run.
  - Ruff is clean. `check_secrets.py` reports 0 findings with the change staged. Every changed file is CR-free.
- CPU acceptance (`-I`, wt95's script and source) exited 0 and all **29** checks passed on each tree (on `02436c8`:
  receipt `f8cc5d7a…6075`; on `960c99e`: `d0d077a1…a184`). On `c2f1bbb` (the same `src` as the final tree): receipt
  SHA256 `caeb61307f2db610132722bf475ce2348018ff88a3c32776855b28c1f405cce6`
  (`D:\llm-tuning-factory-wt95\.factory\model-ops-cpu-receipt.json`), source_sha256
  `71c10d2190cd14371a411796a9b80ec83f2e0224d4b406fb7c0350781935e338` (equal to `source_hash()` of a `git archive`
  export of the commit), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max errors
  are unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8).
  `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false. It supersedes 9.3's
  `00d0043b…7f93`.

Not verified or still open:
- Real Hugging Face redirects, ranges and gated access are still exercised only with MockTransport. Closing an
  unread redirect drops its connection, which is harmless but not measured.
- No GUI screen calls `model_root_targets` yet (Task 10). No network, GPU, SSH, model download, MLflow, provider
  call or institution data was used.

## Task 9.4 controller entry: stdin start, single auth scope, lifecycle, 2026-10-21

Commit `feat: run the graph controller as a stdin-configured background sidecar`, written on `b0d2c90` in the worktree
`D:\llm-tuning-factory-wt94` and rebased onto `bf9a13b` (the 9.2 follow-ups, 9.7, the 8.5 re-review Minors, 9.3, the
Task 10 plan and the 9.7 review fixes landed meanwhile; only this file conflicted). It follows
[Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.4" with D2–D5, D7, D14 and D19,
checked against the landed 8.5 controller and the 9.5 broker (`broker.rs`, the 9.5 review fixes). Changed: new
`src/tuning_factory/controller_entry.py`, new `tests/test_controller_entry.py`, `scripts/desktop_entry.py` (argv
routing only) and this file. `desktop_workflows.py` is untouched: its table already maps `controller_status` and
`controller_stop` to `controller_entry`, which registers them with `workflow_action`.

What it does:
- `desktop_entry.py`:
  - No argv: the legacy one-request sidecar `desktop.main`, byte for byte. A test compares the bytes with the pre-9.4
    entry.
  - Exactly `--controller`: `controller_entry.main`.
  - Anything else (`--bogus`, `--controller x`, `--Controller`, `controller`, `--controller=`): `unsupported sidecar
    arguments` on stderr and exit 2. Stdin is never read.
- The start message:
  - `read_start` takes exactly one UTF-8 JSON line of at most 1 MiB, newline included (the broker's limit).
  - The line is parsed into a strict `ControllerStart`:
    - `schema` is the only spelling accepted;
    - `state_root` must be absolute and local: no UNC or device prefix, no `..`, no control character;
    - `owner_id` follows `OWNER_ID`;
    - at most 32 profiles, with IDs unique under `casefold().rstrip(".")`;
    - profile fields follow `NativeProfile`'s allowlist, and credential names are refused there;
    - the only secret is `RUNPOD_API_KEY`;
    - tracking string fields are at most 2048 characters;
    - `StartSettings` takes strict floats and Task 8's `ControllerSettings` constraints.
  - Duplicate JSON keys, NaN and an unterminated line are refused.
  - Every refusal is `ValueError("invalid controller start message")`, with no cause and no context. `main` prints
    `{"ok": false, "code": "invalid_start", "error": ...}` and exits 2.
  - `repr` and `str` show only the owner and the profile IDs.
  - The Rust `CONTROLLER_MESSAGE_SAMPLE` validates: `test_rust_controller_message_matches_python` now runs instead of
    skipping.
- One auth scope (D7):
  - `apply_tracking_scope` first pops the 18 credential variables of the plan.
  - It then sets the scope once, before any thread starts, filtered exactly as `filter_mlflow_auth` filters (`none`
    drops all four MLflow auth values even if they were sent).
  - It enforces `require_secure_tracking`, `require_secure_artifacts`, `require_complete_oidc` and the D11 public
    view.
  - A refusal gives a fixed text and leaves no credential variable set.
  - RunPod keys exist only in `ProfileSecrets`. S3 and HF credentials never reach the controller.
- Profiles: `provider_profiles` builds RunPod profiles offline with 9.2's `public_profile`; each needs its own key.
  SSH profiles are built from the stored observation of the same revision. A skipped profile gets a fixed reason:
  `invalid_fields`, `credential_missing`, `observation_missing`, `observation_revision_differs` or
  `observation_invalid`. Since the 9.2 follow-ups, `public_profile` refuses a relative key path, so such a profile is
  skipped as `invalid_fields` (tested), as planning refuses it.
- `build_controller` wires the controller as Task 8 §7 says:
  - `GraphStore` and `SettingsExecutorProvider`, wrapped in `BoundedProvider` (below);
  - `DispatchRouter(OperationAttemptRunner, SimulatedOperationDispatcher(roots=DirectoryRootSnapshots(
    root_snapshots_dir(state))), store=)`;
  - `controller_tracker(uri)`, only when the URI is not blank;
  - `ControllerSettings` from `StartSettings`;
  - no `simulated_provider`: the controller builds one per simulation run.
  - The `GraphController` constructor installs `GraphController.redact` as `JobStore.error_filter`; a test asserts it.
  - `attempt_runner` (it imports paramiko) is imported only inside `build_controller`, so the polling sidecar never
    loads paramiko or MLflow (tested).
- `serve`, in this order:
  - A settings refusal exits 4 before any owner lease.
  - The owner lease: a duplicate prints `{"ok": false, "code": "owner_held"}`, exits 3 and writes no file.
  - `current.json` is written, then the ready line `{"ok": true, "owner_id", "pid"}`; stdout then goes to the null
    device.
  - Two daemon watchers start: EOF on the stdin lifeline, and the poll of `stop-request.json` every tick.
  - `run_forever` drains and releases the owner lease. There is no kill-now.
  - At the end, `current.json` gets `stopped_at` and `code` (`stopped`, or `owner_lost`), and a `stop-request.json`
    naming this owner is removed.
  - Exit codes: 0 for a drained stop, 1 for a lost (fenced) owner lease or an unexpected error (its type name goes
    into `current.json`), 2, 3 and 4 as the broker maps them.
- The stdin lifeline. This was found by the RED subprocess test and is pinned by
  `test_the_lifeline_is_moved_off_standard_input`:
  - On Windows, a thread blocked in a synchronous read of the broker's pipe hangs every child process that inherits
    the pipe as its standard input. The manifest's `git rev-parse` hung, so no node was ever prepared, and the test
    failed for that reason.
  - A daemon thread holding `sys.stdin`'s buffered-reader lock at shutdown is a fatal error (exit `0xC0000005`/fatal
    "could not acquire lock for <stdin>").
  - The fix: after the start line, `main` moves the pipe to its own non-inheritable descriptor and puts the null
    device on standard input. `os.dup2` also updates the Windows standard handle, which was checked. The watcher
    reads that descriptor with `os.read`.
  - The PyInstaller build reads `commit.txt` instead of running git, but any child process would have hung.
- Status files (D19) under `state/controller/` hold only IDs, revisions, hashes, timestamps, fixed codes and texts.
  - `current.json` while running: `{ok, owner_id, pid, started_at, profiles: {id: revision} (every profile the start
    named), skipped_profiles: {id: reason}, tracking: {view_sha256, auth_mode, effective_auth_mode},
    tracking_settings_sha256}`.
  - A refusal or failure before the owner lease writes `{ok: false, code: "settings_refused"|"controller_failed",
    error|error_type, owner_id, at}`, but only when no live controller owns the directory.
  - `stop-request.json` is `{owner_id, requested_at}`.
- `controller_status`:
  - Returns `{controller, current, broker, stop_requested, restart_required: {profiles, tracking}, runs: {active}}`.
  - `controller` is exactly `graph_status(...)["controller"]`; test 13 checks parity at every step.
  - It is read through a read-only SQLite connection: a poll never creates, migrates or write-locks `jobs.db`, never
    reads every run, and creates nothing in a fresh state directory.
  - `runs.active` counts RUNNING and PAUSED graph runs.
  - `restart_required` is filled only while the state is `running` and `current.json` is the same owner's.
- `controller_stop`: `{"requested": true, "owner_id"}` for a running controller; otherwise
  `{"requested": false, "reason": "not_running"}`, and nothing is written.

`restart_required` (the choice to review):
- `profiles`: the sorted IDs whose native revision (the request's public fields, through `profile_revision`) differs
  from the revision snapshotted in `current.json`. That covers added, changed and deleted profiles. A rotated key
  needs no restart, because the revision never includes it.
- `tracking` is computed from public fields only. A poll carries no `*_saved` flag and no vault data.
  - `current.json` records a hash of the public tracking view, not the settings in clear (D19). The view is the URI,
    the artifact location, the operator, `auth_mode`, and the OIDC token URL and client ID when the mode uses OIDC
    (`""` or `oidc`).
  - It also records the saved `auth_mode` and the auth mode the process actually applied (`current_auth_mode()` after
    the scope).
  - `true` when the request's view hash differs.
  - Also `true` when an explicit `auth_mode` (`oidc`, `token`, `none`) differs from the applied mode, for example a
    token saved after the start.
  - The legacy empty `auth_mode` skips that second check. Its applied mode depends on saved credentials that a poll
    cannot see, so it never reports a permanent false restart; a change of its URI, artifact location, operator or
    OIDC values is still reported.
  - `null` when `current.json` has no recorded view.
  - Known consequences:
    - Re-saving or rotating a credential without changing a public field is invisible to polling. Task 10 should say
      "restart the controller after saving credentials".
    - An explicit mode whose credential was never saved (for example `token` with no token) reports `true` until the
      credential is saved and the controller restarted.
    - An unreadable settings or profiles file arrives as `{}`/`[]` (9.5) and reads as changed.

Task 8 review notes:
1. The process is owned. `serve` runs one `GraphController` per process, and nothing else in the sidecar uses MLflow.
   The CLI (9.6) must keep that true when it calls `serve`.
2. Auth scope: as above.
3. Redaction:
   - `JobStore.error_filter = GraphController.redact` (installed by the constructor, asserted here).
   - `BoundedProvider` also redacts the provider's own keys from `resolve`/`recover` exception texts, re-raised
     without a cause; a fixed `ValueError` refusal passes unchanged.
   - Still not covered: remote log uploads (`train.log`, `worker.log` uploaded as lineage); exception text escaping a
     dispatcher, which reaches wait reasons through 8.5's `_display` (MLflow secrets and bearer patterns only, not
     RunPod keys; RunPod errors are fixed texts).
4. Timeouts:
   - Implemented in `build_controller`: `BoundedProvider` opens each `resolve`/`recover` context on a helper thread
     and raises `TimeoutError` after `DESTINATION_TIMEOUT_SECONDS = 300`. The controller then backs off as for any
     unavailable destination.
   - A destination that opens late is closed on that thread at once.
   - Not covered: work inside an open connection (reconcile, result download, runtime probes) relies on the
     connection layer's own limits (paramiko 20 s connect, banner and auth; 20 s exec; httpx 30 s). A hang there still
     stalls the tick while the heartbeat keeps `running`. A tick watchdog is a known limitation for Task 11.
5. Reconcile still runs inline on the tick thread (8.5), unchanged and documented in the module docstring.
6. Stopping: a `controller_stop` request and EOF both drain; there is no kill-now.
7. Status files: D19, as above.

Actual verification (main venv with `PYTHONPATH` on the worktree `src`; `--basetemp` under `%TEMP%`):
- RED first. All 45 tests of the new module failed for the intended reasons:
  - the module was missing (`controller_entry (Task 9.4) is missing`);
  - `--controller` with a broken start exited 0 through the legacy path instead of 2;
  - the import test could not import `controller_entry`.
- The contract parity test skipped as designed.
- During GREEN, the subprocess tests went RED twice for real defects, both fixed above: the Windows stdin
  deadlock (the run never prepared its node), and the fatal shutdown error after a stop request.
- Mutations, each restored and checked by SHA256. All 11 were killed by at least one test:
  - EOF never stops;
  - no env pop;
  - the lifeline left on stdin;
  - a refusal overwriting a live controller's status;
  - `restart_required` for a stale owner;
  - no effective-mode check;
  - a duplicate writing `current.json`;
  - the stop request ignored;
  - no destination time limit;
  - pydantic's default `repr`;
  - the default `str`.
  - One weaker variant first survived (`__str__ = object.__repr__` leaks nothing). The test now also forbids
    settings, URIs and paths in `repr` and `str`.
- GREEN:
  - `tests/test_controller_entry.py`: **47 passed**. The plan's 15 tests; test 1 is parametrized over 26 cases. Four
    tests were added: valid starts, the lifeline, the CLI's `TrackingScope.from_environment`, and the bounded,
    redacted destination.
  - With `tests/test_native_contract.py`: **54 passed**, and the parity test runs instead of skipping.
  - `test_controller_entry`, `test_native_contract`, `test_desktop_projects`, `test_graph_controller`, the six
    `test_desktop*` modules, `test_executor_profiles`, `test_simulated_dispatch` and `test_workflow_planning`:
    **383 passed**.
  - Full suite: **1507 passed, 1 skipped**. The baseline was 1459 passed, 2 skipped; the one skip left is the real-GPU
    test.
- Ruff is clean. `scripts/check_secrets.py` reports 0 findings. `git diff --exit-code containers/ pyproject.toml
  uv.lock desktop/` is clean. Every changed file is LF only.
- CPU acceptance:
  - Command: `model-ops-cpu-venv ... -I scripts/verify_model_ops_cpu.py --receipt .factory/model-ops-cpu-receipt.json`.
  - Result: exit 0, **29/29**, passed.
  - Receipt SHA256 `272cf4e858e53c169b01b74773a311484a58258cee3c64f45f3a54d67ad408b0`.
  - `source_sha256 3ddc44d777f259214867cacfd743772640e662bb1005aac3139fa9ce6516b967`, equal to `source_hash()` of the
    tree.
  - `lock_sha256 5883aae6…1437`.
- After the rebase onto `c2f1bbb`, and again on `9f2927d` (the Task 10 plan only; `src`, `tests` and `scripts` are
  byte-identical, and both runs gave the numbers below):
  - Upstream's `check_key_paths` (now inside `public_profile`) refused the tests' relative SSH key path `test-key`.
    The test profiles now use absolute paths, and test 12 also pins a relative RunPod key path as `invalid_fields`.
  - Upstream's `DISPATCHER_STORE_DIFFERS` check passes: `build_controller` gives every dispatcher the controller's
    own `GraphStore`.
  - `test_controller_entry`, `test_native_contract`, `test_desktop_projects` and `test_workflow_runs`: **120 passed**,
    with no skip.
  - Full suite: **1634 passed, 1 skipped**, which is 9.3's 1586 plus the 47 new tests plus the parity test that no
    longer skips. The one skip is the real-GPU test.
  - Ruff, `check_secrets.py` (0 findings with the change staged), the diff check and the LF check are clean.
  - CPU acceptance: exit 0, **29/29**.
    - Receipt SHA256 `f7f7f459f21ca86def8cbff725efcd44fcc81e8ae11e86b9215911e6e1904975`.
    - `source_sha256 d62d1ecd1e932b30b06eb6d3d5a0e275134aaa3d3f4ea0b427f71948299173c8`, equal to `source_hash()` of
      the committed `src`.
    - `lock_sha256 5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
- On the final tree `bf9a13b` (the 9.7 review fixes; desktop/ changed there, not here):
  - The same four test modules: **120 passed**.
  - Full suite: **1648 passed, 1 skipped**, which is 9.7's 1600 plus the 47 new tests plus the parity test.
  - Ruff, `check_secrets.py` (0 findings), the LF check and `git diff --exit-code origin/... -- desktop/ containers/
    pyproject.toml uv.lock` (no change by this commit) are clean.
  - CPU acceptance: exit 0, **29/29**.
    - Receipt SHA256 `5d8cd5c317c113927d69dd80e4258cad1d6e6ea911ba261d799bbccc323a8fc8`.
    - `source_sha256 989c97fbb79185f51bc075ca825bde6a0ca18b8a87f9d1b84fb631bccac89d7f`, equal to `source_hash()` of
      the committed `src`.
    - `lock_sha256 5883aae6…1437`.
    - It supersedes 9.7's review-fix receipt `caeb6130…cce6`.

Not verified (Task 11):
- The real Rust broker spawning the sidecar. Only the Python stand-in (`broker_standin.py`, written at runtime) was
  run.
- The PyInstaller onefile child: its bootloader and child process, and EOF through it. The venv `python.exe` used here
  is also a launcher with a child, so the ready line's `pid` is the controller's own.
- Windows job objects and `CREATE_BREAKAWAY_FROM_JOB` with the real app.
- Credential Manager read from the background broker.
- A real MLflow server, real RunPod and real SSH from the controller process.
- Still open: the 8.5 re-review's note 4 (a PENDING model version that MLflow later marks `FAILED_REGISTRATION` still
  shows `registered`). Checking it needs an MLflow read in the tracking outbox (8.x files), not in the entry; 9.4
  leaves it to a later task.

Deviations:
- `populate_by_name` is not set. The wire name `schema` is the only accepted spelling (`schema_` is refused), and the
  Python constructor uses `schema=`.
- Skip reasons `credential_missing` and `observation_invalid` were added. `current.json` `profiles` lists every
  profile the start named, usable or skipped, so that `restart_required` can compare them.
- `current.json` keeps the tracking view as a hash (D19), not as clear public settings.
- `controller_status` reads `jobs.db` read-only instead of building a `GraphStore` and calling `graph_status`. Parity
  of the controller block is tested.
- A refusal or failure before the owner lease writes `current.json` only when no live controller owns the directory.
  The plan said it always writes, which could replace a running controller's status.
- A fenced (lost) owner lease exits 1 with `code: "owner_lost"`, not 0, so the broker records `controller_failed`.
- New: the stdin lifeline is moved off standard input, and `BoundedProvider` adds time limits and redaction.
- Tests: the plan's 15 plus 4 more (above). Subprocesses run with a `sitecustomize` socket guard in the child, and a
  Python broker stand-in and GUI proxy written at runtime. Every process is detached and killed in teardown, and every
  wait is at most 30 s.

## Task 10.1 workbench foundations: typed bridge, project drafts, view shell, 2026-10-21

Commit `feat: add the typed workflow bridge, project drafts and workbench navigation shell`. It was written in the
main tree on `9f2927d` and fast-forwarded under the uncommitted work onto `1a9ae29` (9.7 native follow-up `bf9a13b`
and 9.4 `1a9ae29`; no file overlap). It implements
[Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.1" with D1–D4, D10, D12, D15 and
D19. The Task 9.8 replacements of D11 and D20 are not part of 10.1. Changed: new `desktop/src/{workflow-types,api,
issues,poller,project-store,nav-model,view-types,view-registry}.ts`, new `desktop/tests/{workflow-mock.ts,
workflow-core.spec.ts,navigation.spec.ts}`, modified `desktop/src/{main,workbench}.ts`, `desktop/src/style.css` and
this file. No Python, Rust, package or lock file changed.

What changed:
- Typed bridge. `workflow-types.ts` lists the 33 workflow actions and the request and result shapes, checked against
  `desktop_workflows.py`, `workflow_planning.py`, `workflow_runs.py`, `hf_targets.py`, `controller_entry.py`,
  `graph_status`, `profiles.rs` and `broker.rs`. `api.ts` is the only new `invoke` caller. Every workflow answer is
  validated as a `WorkflowResult` (`BridgeError` "엔진 응답 형식이 올바르지 않습니다." otherwise); a native `Err`
  string becomes a `BridgeError` with that text.
- `issues.ts` routes issues by pointer, card (`node_id` or `/graph/nodes/<i>`), import row and plan section. It
  shows Korean texts: Korean (Task 9) messages as received, English Task 1–8 texts by message pattern, else by code,
  with the original under "원문 보기"; unknown codes show the message as received. `issueList` escapes and stops at
  50 ("외 N건").
- `poller.ts` (D10: no overlap, `max(base, 2 × duration)`, doubling backoff to 60 s, inactive skip) and
  `createQueue`. `project-store.ts`: drafts with the saved revision, dirty state by content, CAS saves whose
  conflicts keep the draft, "overwrite" (get the latest, save against it), reload, planning inputs per project for
  the session, a held plan with its basis (project revision, pinned recipe revisions, profile and settings
  generations), one request ID per plan hash, and `bindingIdFor`.
- Shell (`main.ts`): the sidebar groups 워크스페이스, 단일 학습 and 설정 from `navModel`; `nav:<page>[:<arg>]`
  with the views' `leave` and `enter`; `wf:<view>:<verb>[:<arg>]` actions; `captureDrafts` calls every view's
  `capture`; `patch(regionId, html)` keeps the focused control and what was typed in it; overview and datasets slot
  panels; "새 프로젝트" as the overview's primary only when a projects view exists; a child page shows its parent in
  the breadcrumb and sidebar; Ctrl+K lists the sidebar pages. D19 headings: "예제 시작점 · 선택", "실험 설정",
  "목적에 맞는 …".
- D15 (F8 fixed). `refresh()` keeps single jobs only: `isGraphJob` is a `graph_run_id` or no v1 `manifest.config`.
  The overview and 실행 관리 say "그래프 실행 단계 N개는 ‘그래프 실행’에서 확인하세요." (with a link once a runs view
  exists); counts and the jobs marker use single jobs only. The legacy `jobs` response is unchanged. Before, any graph
  job made `render()` throw and the boot notice never cleared.
- `store.markSettingsChanged()` runs after every successful `saveSettings` (the connection form and the GPU
  selection save) and after "저장된 인증값 삭제".
- `workbench.ts`: `openDialog` (refuses a second dialog, focuses its heading, checks gate actions, the first activation
  disables every action, Esc resolves null, focus returns to the opener), `focusKey`/`restoreFocus`, `statusPill`,
  `isGraphJob` and four nav icons. `style.css`: group labels, `.status-pill`, `.issue-list`, `.wf-banner`,
  `.wf-columns`, `.wf-dialog`, `.graph-jobs-note`, and a scrollable sidebar.
- `tests/workflow-mock.ts` is the shared mock (owned by 10.1). Unscripted answers model a sidecar where no workflow
  module answers: workflow actions and controller status/stop answer `not_available`; profile save/delete and
  `controller_start` reject with the native fail-closed texts. Options: `workflow`, `native` (`{reject}`),
  `definitions` (a stateful 9.1 fake with CAS and request-ID semantics), `holds` and `clock`.

Deviations:
- Ten tests, not nine. Test 10 (`navigation.spec.ts`) drives routing, slots, `patch` focus, the busy state, dialogs
  and the render guard through the view contract, with a stand-in `/src/view-registry.ts` served by `page.route` (no
  app hook). It was written after the shell code, except its render-guard part. It fails under each of five mutations
  (focus restore, rebinding a patched region, the dialog nesting guard, the dialog check gate, the bind guard).
- Added a render guard. A view's `render`, slot panel or `bind` that throws shows "이 영역을 표시하지 못했습니다.
  …" (escaped) and leaves the legacy page working. It was RED first.
- `NATIVE_PENDING` is empty: `bf9a13b` added `model_root_targets` to `main.rs`. Test 1 failed on the new base as
  designed until it was emptied.
- `ControllerStatus.broker.at` is a number (broker.rs writes epoch seconds), not a string as in the plan's interface.
  `ControllerStatus` and the `controller_stop` result match the landed `controller_entry.py`.
- `ctx.legacy` accepts only `catalog`, `model_search` and `model_inspect`. `parseWorkflowResult` also requires the
  `data` key and checks each issue's field types. `planSection` maps `/project_id` and `/project_revision` to
  `project` and keys `graph` issues by `node_id`. `markSettingsChanged` also runs after forgetting credentials.
  `createProjectStore` takes an optional request-ID generator. Extra result types: `LegacyImport`,
  `ImportRegistered`, `SplitCommitted`, `RootSnapshot`, `RootTargets`, `GraphValidation`, `ControllerStopResult`,
  `RunList`, `RunSteered`, `TrackingRearmed`.

Actual verification:
- RED on `9f2927d` before any `src` edit: `workflow-core.spec.ts` failed to load (`Cannot find module
  …/src/workflow-types.ts`); test 8 failed on the missing "단일 학습" group; test 9 failed on the boot notice that
  never cleared, with `TypeError: Cannot read properties of undefined (reading 'ai_id')` at `main.ts:281`.
- From `desktop/` on `1a9ae29`: `npx playwright test` **91 passed** (the 81 existing tests unchanged, plus 10 new;
  the baseline at `9f2927d` was 81 passed); `npm run build` passes.
- From the root: `uv run pytest -q` **1648 passed, 1 skipped** (unchanged from 9.4's
  receipt on this tree; the skip is the real-GPU test); `uv run ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; `git diff --exit-code src/ desktop/src-tauri/
  containers/ pyproject.toml uv.lock desktop/package.json desktop/package-lock.json` is clean; every changed file is
  CR-free.

Not verified or still open:
- Real WebView2 (`<dialog>`, focus return, the `import.meta.glob` bundle once views exist): Task 11. No workflow
  view exists yet; the shell plumbing is covered only through the stand-in registry.
- Planning inputs stay in memory for the session; their backend store is Task 9.8.
- Legacy `recover` still fails stale graph jobs too (Q-K; Task 9.8 adds the test).
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 10.2 projects and recipes, 2026-10-21

Commit `feat: create projects and reusable recipes from the Windows workbench`, written in the main tree on `6c4b877`.
It implements [Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.2" with D1, D3–D5
and D17. Changed: new `desktop/src/views/{projects,recipes}.{ts,css}`, `desktop/src/components/recipe-fields.ts`,
`desktop/tests/{projects,recipes}.spec.ts`; modified `desktop/src/main.ts` (the recipe-field functions only),
`desktop/src/workbench.ts` (`field`/`select` moved in), `desktop/tests/navigation.spec.ts` (two assertions, see
deviations) and this file. No Python, Rust, package or lock file changed.

What changed:
- Projects. The overview's first panel "빈 프로젝트 만들기" (`data-tour="project-start"`, once) creates a project
  from a free name (required) with optional purpose and task type. The `request_id` is reused on a retry until those
  values change. The 프로젝트 page lists projects (`projects_list` on entering), offers the same form and "기존 학습
  설정 가져오기" (`project_import_legacy` per saved legacy profile; warnings and the created recipe's revision are
  shown on the project page, by hash). The project page edits name, purpose, task type and the default executor
  profile (from `executor_profiles_get`, optional). Data bindings choose registered catalog versions, take their ID
  from `bindingIdFor`, refuse a version that is already bound and mark `tutorial_sample` "연습 데이터 · 실제 실행
  불가"; removal is refused while a graph card uses the binding. Saving is explicit ("저장", also Enter); a
  `revision_conflict` keeps the draft and offers "최신 버전 불러오기" and "내 변경으로 덮어쓰기", each behind a
  confirm dialog (D5). Nothing runs at boot; a project just created or imported is not re-read on opening.
- Recipes. The 레시피 page lists recipes and offers 새 레시피, 열기, 복제 (`recipe_clone` with the shown revision and
  "… 복사본") and "예제에서 만들기". `recipeFromTemplate` copies stage, method, quantization, hyperparameters,
  fine-tuning options, stage options without the reward binding and evaluation defaults without the dataset; never
  the AI or workflow ID, data, base model, revisions, template or resources. Saving uses CAS (`expected_revision`
  null for a new recipe); a replaced revision calls `store.markRecipeChanged`. Issues show under their field (with
  `aria-invalid`) or in the form summary, in Korean with "원문 보기"; a conflict keeps the draft with the D5 choices.
  Drafts stay in memory per recipe.
- D17. `components/recipe-fields.ts` holds `stageSelect`, `methodSelect`, `trainingOptions`, `readFields` and
  `inputKindFor`, parameterized by `data-config`/`data-recipe` and a value getter; recipe mode omits the three PPO
  reward fields. The training page calls it through `configSource`; `field`/`select` live in `workbench.ts`.

Deviations:
- `tests/navigation.spec.ts` test 8 (a 10.1 test) was edited with root's approval: the sidebar labels are checked as
  D1-ordered (each present label at its D1 position) instead of the exact seven legacy labels, and the overview's
  primary is "새 프로젝트" → `nav:projects`. Both old assertions encoded "no workflow view exists"; the rest of the
  test is unchanged.
- Fifteen tests, not fourteen: test 15 checks that a retried `project_create` reuses its `request_id` and a changed
  name gets a new one (the plan's implementation note).
- `RecipeFieldContext` has an optional `decorate(path, html)` hook (recipe mode puts issues under fields); the training
  form never sets it. `captureConfig` in `main.ts` was left as it is; `readFields` repeats its rules for recipes.
- 9.8's `recipe_get` by `revision_sha256` is not used: pinned revisions are shown by hash (the import note).

Actual verification:
- The characterization test (`recipes.spec.ts` test 1: the ordered `[data-config]` controls, flags and labels for
  sft/lora, dpo/freeze with SimPO, kto/qlora and ppo/oft) was written first and passed on `6c4b877` before any `src`
  edit. Tests 2–14 then failed on the missing overview panel and sidebar entries (13 failed, 1 passed).
- The training page's `#app .content` HTML was captured for six stage/method combinations before the extraction and
  compared after it (and again with the views present): byte-identical. Changing one field's flag in
  `recipe-fields.ts` makes test 1 fail.
- Eleven mutations of the new views (request-ID reuse, the busy submit guard, focus after save, the re-read skip, the
  duplicate-binding refusal, recipe CAS, recipe-mode reward fields, template dataset stripping, issue routing,
  conflict draft keeping, overwrite against the latest revision) each made its test fail.
- From `desktop/`: `npx playwright test` **106 passed** (91 existing, of which `navigation.spec.ts` test 8 has the two
  edited assertions, plus 15 new); `npm run build` passes.
- From the root: `uv run pytest -q` **1648 passed, 1 skipped** (no Python change; same as `6c4b877`); `uv run ruff
  check src tests` passes; `scripts/check_secrets.py` reports 0 findings with the change staged; `git diff
  --exit-code src/ desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json
  desktop/package-lock.json` is clean; every changed file is CR-free.

Not verified or still open:
- Real WebView2 (dialogs, focus, the views in the `import.meta.glob` bundle) and a real sidecar: Task 11.
- Deleting projects or recipes has no action; graph editing (10.5) and data import (10.3) are separate sub-tasks.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 9.6 CLI: project and graph commands, 2026-10-21

Commit `feat: add project and graph commands to the factory CLI`, written on `1a9ae29` (Task 9.4) in the worktree
`D:\llm-tuning-factory-wt94` and rebased onto `0c38b3f` (Tasks 10.1 and 10.2, desktop only; only this file
conflicted). It follows [Task 9 plan](superpowers/plans/2026-10-20-task9-secure-desktop-broker.md) §4 "9.6" with D9,
D10 and D14. Changed: `src/tuning_factory/cli.py` (new commands appended; v1 commands untouched), new
`tests/test_cli_graph.py`, and this file.

What it does:
- The D10 commands: `project-list`, `executor-profile-inspect PROFILE_ID --executor-profiles FILE`,
  `graph-validate PROJECT_ID [--revision]`, `graph-plan PROJECT_ID --request FILE [--executor-profiles FILE] [--real]`,
  `graph-submit PLAN_SHA256 --request-id ID [--approve-execution] [--approve-data-transfer] [--executor-profiles FILE]`,
  `graph-status [RUN_ID]`, `graph-pause`/`graph-resume`/`graph-cancel RUN_ID`, `graph-rearm-tracking EVENT_ID` and
  `graph-work`.
- Every command except `graph-work` runs one desktop workflow action through `desktop_workflows.dispatch_action`, with
  the same strict request model as the app. It prints the `WorkflowResult` as JSON in the v1 `output` format, and
  exits 0 for `ok` and 1 for `invalid` or `conflict` (`emit`).
- `cli_context` builds the `WorkflowContext`:
  - the `--state-dir` directory;
  - `settings` = `tracking_view(scope)`: the desktop's public view (URI, artifact location, `auth_mode`, OIDC URL and
    client ID, `*_saved` flags) of this process's one MLflow scope, `TrackingScope.from_environment()` (the `.env`
    file, `--connection-file` and the environment), with `--tracking-uri` applied as the v1 commands apply it;
  - the profiles file's public fields as `NativeProfile`s;
  - `secrets` = only the RunPod key of the profile that `executor-profile-inspect` observes.
- The profiles file (`CliProfiles`): `{"schema_version": 1, "profiles": [{"profile_id", "fields",
  "credential_file"?}]}`, at most 32 profiles, IDs unique under `casefold().rstrip(".")`, at most 1 MiB, no duplicate
  JSON key.
  - `fields` follow `NativeProfile`'s per-provider allowlist. A credential name is refused first, as
    `credential_field`, with "reference a credential file instead". Extra keys (`RUNPOD_API_KEY`, `secrets`) are
    `extra_field`.
  - `credential_file` (RunPod only) names a local env file with `RUNPOD_API_KEY`, relative to the profiles file.
    `~` is expanded. UNC and device paths are refused with `local_file_only`, and files over 64 KiB with
    `credential_file_unreadable`; a file without the key gives `credential_missing`.
  - Without a credential file, a RunPod profile uses `RUNPOD_API_KEY` from the existing `.env`/environment (D10).
  - Keys are read only by `executor-profile-inspect` (the one profile) and by an approved `graph-work`. Planning and
    submission read public fields only.
  - Refusals point into the file (`/profiles/<index>/...`) and never echo a value.
- `graph-plan`'s `--request` file is a `GraphPlanRequest` body without `project_id` and `simulation`. Both are refused
  as `extra_field`: the argument names the project, and only `--real` makes a real plan.
- `graph-work` runs the 9.4 controller in the foreground:
  - It builds `ControllerStart(schema=START_SCHEMA, state_root, owner_id="cli.<pid>.<time_ns>", tracking, profiles,
    settings=StartSettings(tick_seconds))` and calls `controller_entry.serve` on a worker thread. `serve` pops the
    inherited credential variables, applies the MLflow scope once, takes the owner lease, writes `current.json` and
    drains; `GraphController` installs its redaction as `JobStore.error_filter`.
  - Without `--approve-execution-and-transfer`: `tracking = TrackingScope()` and no profiles. Simulation runs
    proceed; real runs wait (`executor profile is not configured in this controller`, no tracker).
  - With it: the scope from the environment, and every profile of `--executor-profiles` with its own key in
    `ProfileStart.secrets` (and so only in `ProfileSecrets`).
  - `--executor-profiles` without the approval flag is refused (exit 2) rather than ignored.
  - One controller per process: a process lock refuses a second `graph-work` in the same process (exit 2), and the
    owner lease refuses a second controller on the state directory (exit 3, `owner_held`).
  - **Corrected by the 9.6 review fixes below.** As committed here, Ctrl+C did not work. The main thread caught
    `KeyboardInterrupt` around `Thread.join`, and a real Ctrl+C lands inside the join, where CPython 3.12 marks the
    thread stopped while it still runs. One real Ctrl+C therefore ended the process mid-drain: exit 1, the final line
    lost, the owner lease held and no `stopped_at`. The intended behaviour (first Ctrl+C drains, second exits with
    130) is implemented and tested in the review fixes.
  - Stdout is JSON lines: `serve`'s ready line (or `{"ok": false, "code": "owner_held"|"settings_refused"}`), then
    `{"ok", "exit_code", "code", "message", "controller"}`, where `controller` is this owner's `current.json` (public
    IDs, revisions and hashes). The review fixes print this final line for every outcome, exit 2 included.
  - Exit codes are `serve`'s: 0 drained stop, 1 lost owner or internal error, 3 `owner_held` ("controller already
    running"), 4 settings refused (before any owner lease). The review fixes add 2 (refused before the start) and
    130 (a second Ctrl+C).
- `release-lock --confirm-execution-stopped` is unchanged and already works for graph jobs:
  `tests/test_graph_controller.py::test_ambiguous_launch_waits_for_the_operator` runs it through the CLI on a graph
  job, and it passed in the runs below.

Actual verification (main venv with `PYTHONPATH` on the worktree `src`; `--basetemp` under `%TEMP%`):
- RED first. All 19 tests written first failed: `cli_module()` reported `tuning_factory.cli.CliProfile (Task 9.6) is
  missing`. With those names stubbed in a throwaway plugin (outside the tree), the same 19 failed for the intended
  reason: `No such command 'project-list'` (and `graph-validate`, `executor-profile-inspect`, `graph-submit`,
  `graph-work`), exit 2, and the command set lacked the D10 names.
- GREEN: `tests/test_cli_graph.py` **23 passed**: the plan's 10 tests, plus the D11 parity table (6 cases), the
  synthetic-secret spy test, Ctrl+C (two moments) and a second Ctrl+C (seam-based, so they missed the real defect;
  replaced in the review fixes), exit 4 before the owner lease, an import check
  (no torch, peft, transformers, safetensors, huggingface_hub or keyring), and `--tracking-uri`. Four items were added
  after the first GREEN (19 passed) and were not RED first: exit 4, the import check, the "as soon as running" Ctrl+C
  moment and `--tracking-uri`. The last two were then checked against mutations (below).
- The spy test: a credential file key, an MLflow token in the env file, inspection, a real plan, submission and an
  approved `graph-work` against a fake `executor_for` and a fake `MLflowTracker` that both raise with the secret in
  their text.
  - The key reached the provider as a value, from the credential file; `RUNPOD_API_KEY` was never in `os.environ`.
  - The stored wait reason and the tracking event's `last_error` hold `[redacted]`.
  - Neither secret is in any argv, stdout, stderr or file under the state directory (`jobs.db` included).
  - `current.json`'s `tracking_settings_sha256` equals the real plan's pinned `public_settings_sha256` (D11 through
    the CLI).
- Mutations of `cli.py`, each restored and checked by SHA256. All 12 were killed:
  - profiles used without approval;
  - the MLflow scope applied without approval;
  - no `*_saved` flags in the view;
  - the first Ctrl+C not setting the stop event;
  - the second Ctrl+C ignored;
  - no process lock;
  - exit code always 0;
  - the environment key used instead of the credential file;
  - the request file choosing `simulation`;
  - every profile's key scoped;
  - `emit` exiting 0;
  - `--tracking-uri` ignored.
  - In the first round the first-Ctrl+C mutation hung its test (the run was killed by hand, so it did not count), and
    `--tracking-uri` survived. The Ctrl+C tests now stop a controller that ignores Ctrl+C through its stop request
    after 30 s and then fail, and the `--tracking-uri` test was added. Both mutations were then killed.
- `test_cli_graph`, `test_runner_cli`, `test_controller_entry` and `test_workflow_runs` (the plan's list): 23 + 10 +
  47 + 29 = **109**. With the other CLI modules (`test_connections`, `test_gpu_servers`, `test_graph_controller`,
  `test_graph_store`, `test_tracking_https`): **340 passed**. `tests/test_runner_cli.py` and the other existing tests
  are unchanged.
- Full suite: **1671 passed, 1 skipped** (880 s): the baseline 1648 plus the 23 new tests. The one skip is the
  real-GPU test.
- Ruff is clean. `scripts/check_secrets.py` reports 0 findings. `git diff --exit-code containers/ pyproject.toml
  uv.lock desktop/` is clean. Every changed file is LF only.
- CPU acceptance: exit 0, **29/29**, passed.
  - Receipt SHA256 `56a3c0f8d125c743359f1cfb1aa8b904f651d5e83163d923b476c7eec0a0b65f`.
  - `source_sha256 abf088b61071b975120778179699782d2a9aabba0cd4dbb7c00ccc87fdfdbed7`, equal to `source_hash()` of the
    tree.
  - `lock_sha256 5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.

Not verified:
- Real RunPod, SSH and MLflow from the CLI controller; only fakes were used.
- A real terminal Ctrl+C. The tests raised `KeyboardInterrupt` through the `_join` seam, before `Thread.join`, and the
  second Ctrl+C's `os._exit` was replaced by a stand-in. That is why they passed while a real Ctrl+C failed; see the
  9.6 review fixes below.
- The Linux CI run (only Windows was run here).
- Korean issue messages on a console with a legacy code page (fixed for the new commands in the review fixes).

Deviations:
- `graph-work --executor-profiles` without `--approve-execution-and-transfer` is refused (exit 2). The plan ignored
  the profiles.
- The request file may not contain `project_id` or `simulation`.
- `settings.auth_mode` is the controller scope's mode (`TrackingScope.from_environment`), not raw
  `current_auth_mode()`. They differ only for an OIDC URL without a client secret: the view says `oidc` with no saved
  secret, so `public_auth_mode` still equals `current_auth_mode()` (`none`), and planning reports
  `tracking_incomplete` exactly when the controller would refuse to start.
- `--tracking-uri` applies to the graph commands and to `graph-work`'s scope.
- `credential_file` is a string (resolved as above), not a `Path` field; a RunPod profile without one uses the
  environment key.
- A process lock (exit 2) in addition to the owner lease.
- `graph-work` prints JSON lines (the ready line, then a final line), not a single document.
- 13 tests beyond the plan's 10 (above).
- No CLI command creates projects, recipes, data or HF-root snapshots: D10 lists none, so those come from the app
  (or the workflow actions).

## Task 9.4 review fixes, 2026-10-21

Commit `fix: keep controller status readable after crashes and tighten restart reporting`, written on `6c4b877` and
rebased onto `7291742` (Task 10.2 changed only `desktop/` and this file; Task 9.6 added the CLI on `serve`) in
`D:\llm-tuning-factory-wt94b`. The independent review of `1a9ae29` was "Ready with one fix" with nothing Critical. It
reproduced the claims, the parity test and the lifeline (12 stop and 12 EOF exits, no `0xC0000005`). Changed:
`src/tuning_factory/controller_entry.py`, `src/tuning_factory/manifest.py` (the git child's environment),
`tests/test_controller_entry.py` and this file.

Required:
1. **A hot journal blocked `controller_status`, and so `controller_start`.**
   - `jobs.db` uses a rollback journal. After a controller is killed mid-write, `jobs.db-journal` stays hot, and the
     `?mode=ro` connection failed every poll with `attempt to write a readonly database`. Rust `controller_start`
     polls first, so the user could not start a controller.
   - Fix: the `is_file()` guard stays. The poll connects with `?mode=rw`, which opens an existing file and never
     creates one, issues SELECTs only and never migrates. SQLite then rolls the journal back.
   - Test: `test_controller_status_survives_a_hot_journal`. A writer is killed in an open transaction that has
     already spilled an uncommitted owner expiry into `jobs.db`. The poll succeeds, the owner is `absent` (rolled
     back, not read), the journal is gone, and a poll of a fresh directory creates nothing.

Recommended, all done:

2. **One bound per status read.** The three SELECTs share one deferred read transaction, with a single
   `STATUS_TIMEOUT_SECONDS = 5` busy bound. Once the first SELECT holds the shared lock, the others cannot wait.
   - A lock held longer answers `invalid`/`status_busy` with a fixed Korean text, for `controller_status` and
     `controller_stop` alike, instead of hanging.
   - The native `should_start` treats any non-`ok` status as an error, so no broker starts blind.
   - `_record_unowned` writes nothing when the database is busy.
   - Test: `test_controller_status_is_bounded_while_the_database_is_locked`: an `EXCLUSIVE` holder, a 0.5 s bound,
     an answer within 4 s.
3. **`restart_required.profiles` now also names a profile that was skipped at the start for its observation**
   (`observation_missing`, `_revision_differs` or `_invalid`) once an observation of the same revision builds it. It
   uses the start's own rule (`_observed_profile`). Test: `test_restart_required_names_profiles_a_restart_would_now_serve`.
   - Not detectable from a poll, which carries no `*_saved` flag or vault data:
     - A RunPod key saved for a `credential_missing` profile. Task 10 has what it needs to show it:
       `current.skipped_profiles` against `executor_profiles_get`'s `RUNPOD_API_KEY_saved`.
     - A rotated key. The old docstring said a rotation needs no restart; that was wrong. The running controller
       keeps its start key in `ProfileSecrets`, so a rotation needs a restart that polling cannot report. Task 10
       should say "restart after saving or rotating a key".
4. **Stale `broker`.** While the running owner's `current.json` is present, `broker` is `null`.
   - The review's condition was `broker.at < current.started_at`. It does not cover its own scenario: the broker of
     a fenced predecessor that lingered after a sleep writes `controller_failed` after the new start (`at` >
     `started_at`).
   - The new broker removes `broker.json` at its own spawn, so any failure seen while this owner runs describes
     another start.
   - With `stale` or `absent` it is shown as before. Test 13 covers both orders and both states.
5. **Leftover stop request.** At the start, a `stop-request.json` written before this owner lease was acquired, or
   naming another owner, is removed. That covers a killed run under the same fixed CLI owner ID. A request for this
   owner after the acquisition still stops it. Test: `test_a_leftover_stop_request_never_stops_a_new_controller`, for
   the same and for another owner ID.
6. **Owner lease held until its TTL when `outbox.backfill` raised inside `acquire_controller`.**
   - The failure path now takes the lease `acquire_controller` recorded (`controller._owner`), releases it, and
     writes `{ok: false, code: "controller_failed", error_type, owner_id, at}`.
   - A `recorded` flag replaces a stale `current.json` of the same owner ID instead of merging into it.
   - Test: `test_a_failed_outbox_backfill_releases_the_owner`.
7. **The MLflow token reached the `git rev-parse` child** in non-frozen runs: dev runs and 9.6's foreground
   controller.
   - `manifest.code_revision` now passes `env=child_environment()` and `stdin=DEVNULL`.
   - `child_environment()` drops every name in `manifest.CREDENTIAL_ENV`, which `controller_entry.CREDENTIAL_ENV` now
     reuses.
   - Test: `test_the_git_revision_child_inherits_no_credentials`, a subprocess with synthetic values that also runs
     the real git.
8. **CI margin.** The subprocess tests use a 6 s lease and a 1.5 s heartbeat (tick 0.2 s). Test 8 still waits at
   most 30 s for any one condition.
9. **Nits:**
   - The unreachable `invalid_fields` branch around `profile_revision` is gone (`ProfileStart` already holds the
     allowlist).
   - `_drained` is true only when `run_forever` released this owner lease. `_OwnedStore`, the controller's
     `GraphStore` subclass, records the release, so a drain fenced while a successor holds the owner row exits 1
     with `owner_lost`. Test: `test_a_drain_fenced_by_a_successor_is_not_a_clean_stop`.

Also from the record list: the popped list, and the git child's dropped list, now include the ambient credential
variables: `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_SHARED_CREDENTIALS_FILE`, `AWS_CONFIG_FILE`, the
web-identity, role and container-credential variables, `AZURE_STORAGE_*` keys, `AZURE_CLIENT_SECRET`,
`AZURE_CLIENT_CERTIFICATE_PATH`, `GOOGLE_APPLICATION_CREDENTIALS`, `HUGGINGFACE_HUB_TOKEN`,
`MLFLOW_TRACKING_INSECURE_TLS` and `MLFLOW_TRACKING_CLIENT_CERT_PATH`. `MLFLOW_TRACKING_SERVER_CERT_PATH` (a CA
bundle) is kept, because a corporate MLflow server may need it.

Records for Tasks 10 and 11 (no code):
- A fenced controller lingers until its in-flight dispatch threads return, because `ThreadPoolExecutor` joins its
  threads at interpreter exit. Its broker status is written only after that; item 4 keeps it out of a running
  owner's status.
- `restart_required.tracking` can be `null` (the TypeScript type is already nullable). An explicit mode with no
  credential (`tracking: true` with `current.tracking.effective_auth_mode` `none`) should be shown as "credential
  missing", not "restart".
- Ambient credentials that are files or profiles outside the environment (the shared AWS credentials file, the
  Azure CLI cache, gcloud's default credentials) are still readable by the controller process. Only the variables
  are popped.

Actual verification (main venv, `PYTHONPATH` on the worktree `src`, `--basetemp` under `%TEMP%`):
- RED first: the 7 new tests and 2 changed tests failed for the intended reasons:
  - `attempt to write a readonly database`;
  - no `STATUS_TIMEOUT_SECONDS`;
  - no restart for the observed profile;
  - the leftover stop request was not removed (the controller stopped at once);
  - the backfill failure left the owner lease unexpired;
  - the fenced drain exited 0;
  - `code_revision` passed no environment;
  - the ambient names were missing from `CREDENTIAL_ENV`;
  - test 13 saw the stale broker while running.
- GREEN: `tests/test_controller_entry.py` **54 passed** three times in a row (47 + 7). With `test_native_contract`,
  `test_desktop_projects`, `test_workflow_runs`, `test_graph_controller`, `test_config_registry`,
  `test_runtime_profiles`, `test_operation_schema` and `test_simulated_dispatch`: **175 passed**.
- Full suite: **1655 passed, 1 skipped** (the baseline at `6c4b877` was 1648 passed, 1 skipped; plus 7; the skip is
  the real-GPU test). After the rebase onto `0c38b3f` the full suite and the CPU acceptance gave the same numbers and
  the same receipt.
- On the final tree `7291742` (with 9.6):
  - `test_cli_graph`, `test_runner_cli`, `test_controller_entry` and `test_native_contract`: **94 passed**. 9.6's
    owner IDs are unique per run, and its stop requests come after the start, so the leftover rule keeps them.
  - Full suite: **1678 passed, 1 skipped** (9.6's 1671 plus 7).
  - CPU acceptance: exit 0, **29/29**. Receipt SHA256
    `aef4e29473f2ff9055f80bd8e29c446f33af6c240a6dd1759024d29dbe125e07`, `source_sha256
    2c1956d55a62b2a0eb8ec1631fe71e52a21c6782744bfecfd1284f11389f52e8`, equal to `source_hash()` of the tree.
- Ruff is clean. `check_secrets.py` reports 0 findings with the change staged. Every changed file is LF only.
  `git diff --exit-code desktop/ containers/ pyproject.toml uv.lock` is clean.
- Pre-rebase CPU acceptance on `6c4b877`/`0c38b3f` (superseded by the `aef4e294…` receipt above; `-I`, wt94b's
  script and receipt): exit 0, **29/29**.
  - Receipt SHA256 `e1d07227a2c426e6122db546b61e9ba671bfcc6d768706b0d7c75bb7808c58b8`.
  - `source_sha256 b0f24bdf6100ba09de7633217ee509f0c0beca4b6d9959dc72a94eac0fbec53f`, equal to `source_hash()` of the
    tree.
  - `lock_sha256 5883aae6…1437`.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.
- Re-review of `d227b7d`: ready to merge. Two notes it recorded:
  - **TLS behaviour change.** Graph controllers, both the desktop sidecar and the CLI `graph-work`, now pop
    `MLFLOW_TRACKING_INSECURE_TLS` and the mTLS `MLFLOW_TRACKING_CLIENT_CERT_PATH`, which v1 `work` honours.
    - A self-signed MLflow server needs `MLFLOW_TRACKING_SERVER_CERT_PATH`, which is kept, as are `REQUESTS_CA_BUNDLE`
      and `SSL_CERT_FILE`.
    - mTLS MLflow is not supported for graph runs.
  - **Status poll timing.** The 5 s bound measures about 1.37× on Windows, roughly 6.8 s, because of SQLite
    busy-handler sleep granularity. Task 10 spaces polls by measured call time (D10), so a slow poll delays the next
    one instead of piling up.

## Task 10.1 review fixes: typed drafts across deferred renders, guarded view hooks, 2026-10-21

Commit `fix: keep typed drafts across deferred renders and guard view hooks`, after 10.2 in the main tree. It lands the
independent review of `6c4b877` ("Ready with fixes": three Important, nothing Critical) and its recommendations.
Changed: `desktop/src/{main,workbench,issues,project-store,view-types,workflow-types}.ts`, the 10.2 views (message-only
errors), `desktop/tests/{navigation,workflow-core}.spec.ts`, `desktop/tests/workflow-mock.ts` and this file.

What changed:
- A poll patch during a press no longer loses typed text. A patch applies at once when the pressed control is outside
  its region; when it is inside, the typed drafts are read first and the render waits for the release.
  `ctx.render`/`notify`/`fail` read the drafts first whenever a press defers them (a task's pending render keeps
  precedence).
- A view's `capture` or `leave` that throws is logged and skipped (like `bind`), so boot, navigation and every legacy
  task keep working.
- After a dialog closes on a click, the rest of a double click or a repeated Enter at the same spot is swallowed for
  400 ms, so it never activates the control underneath or reopens the dialog through its opener.
- Recommended: errors show their message without "Error: "/"BridgeError: " (`errorText`); a code known to carry an
  English domain text is localized even when the text quotes a Korean value (generic refusal codes keep Korean
  messages); a patch copies typing only into the same single control (same tag and type) and takes
  `keepTyping: false`, and `view-types.ts` documents unique focus keys per region; `RunSteered.run` may be an
  unreadable run.
- Optional, taken: a task gives the focus back to the control that started it when the final render lost it;
  `restoreFocus(document, …)` searches `#app main` first; `store.generations()` snapshots the profile and settings
  generations before a plan request and `holdPlan(…, since)` uses them; `openWorkbench` waits for boot to reach its
  `capabilities` call; the profile-save notice says "실험 설정을 저장했습니다." (D19).

Actual verification:
- RED first: the four new `navigation.spec.ts` tests failed on the deferred patch, the boot that never reached
  `capabilities` (a throwing `capture`), the click that reached the page beneath and the ignored `keepTyping`; the two
  new `workflow-core.spec.ts` tests on the untranslated quoting text and the missing `generations`; test 10 on the
  "Error: " prefix in the render-guard banner (its expected text was updated). The `#app main`-first assertion was added
  after the code.
- Fifteen mutations (each fix and recommendation, including the capture before a deferred render, the leave guard,
  the task focus restore and the `since` basis) each made a test fail.
- From `desktop/`: `npx playwright test` **112 passed** (106 + 6 new); `npm run build` passes (before and after the
  rebases onto `7291742`, Task 9.6, and `d227b7d`, the 9.4 review fixes).
- From the root: `uv run pytest -q` **1648 passed, 1 skipped** before the rebases, **1671 passed, 1 skipped** on
  `7291742` and **1678 passed, 1 skipped** on `d227b7d` (their own tests; this commit changes no Python);
  `uv run ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; the protected-path `git diff --exit-code` is
  clean; every changed file is CR-free.

Not verified or still open:
- Real WebView2 pointer, focus and dialog timing (the 400 ms window, deferred renders): Task 11.

## Task 9.8 backend additions for the UI, with the 9.3 review fixes, 2026-10-21

Commit `feat: add operator release, profile inventory and planning inputs for the workbench`, written in
`D:\llm-tuning-factory-wt95` (branch `codex/task-9.8`) on `bf9a13b`, then rebased onto `6c4b877` (9.4 `1a9ae29` and
10.1), onto `0c38b3f` (10.2, desktop and docs only), onto `7291742` (9.6, the graph CLI), onto `d227b7d` (the 9.4
review fixes) and onto `6f05a08` (the 10.1 review fixes, desktop and docs only); only this file conflicted each time
(`workflow-types.ts` merged cleanly), every entry is kept, this one last. It implements the
[Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §2 "Root decisions (2026-10-21)", which
replace D11 and D20 and close planning problems 3–7 and 13 (§6 Q-A to Q-E, Q-K). It follows the Task 9 conventions
(strict request models, the issue envelope, registration through `desktop_workflows`, the Rust allowlist, exact
Python/Rust parity). Changed: `desktop_workflows.py`, `workflow_planning.py`, `workflow_runs.py`,
`graph_controller.py` (item 1 only), `desktop/src-tauri/src/main.rs`, the `WORKFLOW_ACTIONS` constant of
`desktop/src/workflow-types.ts` (see the deviations), the new `tests/test_workbench_backend.py`, the tests named below
and this file.

The 9.8 items:
1. **Node identifiers.** `graph_status` nodes (and so `graph_run_get` and every run answer) carry `job_id` next to
   the existing `attempt_id`; every 8.5 field stays (a test pins the exact key set).
2. **`graph_release_lock`** (`workflow_runs`; replaces D11's and D21's "CLI only", because the installed app ships
   no `factory` CLI). Request `{run_id, node_id, job_id, attempt_id, confirm_execution_stopped}`; the confirmation
   is a strict boolean and required. No secret scope.
   - It is allowed only while `graph_status` shows that node's current attempt **uncertain** (FAILED or CANCELED,
     launched, holding its lock) with `needs_operator` and no plausible late result.
   - Then it runs exactly the reads, checks and single store call of CLI `release-lock
     --confirm-execution-stopped` (`JobStore.get` status in FAILED/CANCELED/COMPLETED, `current_lease`, its attempt
     ID, `release_gpu(lease, execution_stopped=True)`), plus one read: the lease must still hold its lock.
   - Two copies of one ledger end byte-identical after the desktop release and after the CLI release, and the 8.5
     end-to-end test now runs both: the controller settles the node FAILED (`failed_before_submit`), blocks its
     child and never retries it (D4). A canceled uncertain attempt settles CANCELED, as after the CLI.
   - Fixed refusals: `invalid` `confirmation_required` (`false`; a non-boolean or a missing field is
     `invalid_value`), `run_unknown`, `run_unreadable`, `node_unknown`, `job_mismatch`; `conflict` `attempt_stale`,
     `awaiting_result`, `operator_not_needed`, `release_not_applicable` (needs the operator for another reason, or
     released already) and `lock_not_held` (the lock went between the status read and the release).
   - `ok` data: `{released: {run_id, node_id, job_id, attempt_id}, run, controller}`.
3. **`observations_list`** (`workflow_planning`, `{}`): each stored observation, sorted by profile ID:
   `{profile_id, revision, observed_at, age_seconds, provider, name, resource_id, gpu_type, gpu_count,
   total_hourly_cost_usd, cost_source, selected_gpu_uuids, profile_current, fresh_for_real_plan}`;
   `profile_current` is null when no such native profile exists, and `fresh_for_real_plan` means the current
   revision observed at most 3600 s ago. A record that does not verify is `{profile_id, error}`. A read: it creates
   nothing, and never returns a field value, key path or credential.
4. **`executor_profile_inventory`** (`workflow_planning`, `{profile_id}`): the legacy `server_inspect` probe (6A:
   the fixed identity read, then the restricted `nvidia-smi --query-gpu=uuid,name,memory.total,mig.mode.current`
   query; the test compares the command lists with `desktop_runtime.inspect_server`) for an SSH profile with or
   without a GPU selection. The selection is checked offline and reported, never required. It stores nothing.
   RunPod is `invalid`/`inventory_ssh_only`. No secret scope (SSH reads the local key file path).
   `ok` data: `{profile_id, revision, observed_at, server_id, resource_id, inventory: [{uuid, name, memory_mib}],
   selected_gpu_uuids}`.
5. **`recipe_get`** accepts `{revision_sha256}` instead of `{recipe_id[, revision]}` (exactly one;
   `invalid_value` at `/recipe_id`, `/revision_sha256` or `/revision` otherwise) through
   `_RevisionStore.get_by_revision`; an unknown hash is `unknown_definition` at `/revision_sha256`.
6. **`planning_inputs_get {project_id}` / `planning_inputs_save {project_id, project_revision, simulation,
   node_time_limits: [{node_id, timeout_minutes}], estimates: [PlanningEstimate]}`** (`workflow_planning`).
   - Stored atomically (`_replace`) in `state/workflows/planning-inputs/<sha256(project_id)>.json`.
   - The save is a compare-and-swap on the project's current revision (`conflict`/`revision_conflict` at
     `/project_revision`; the stored record stays).
   - Time limits are 1–10080 minutes (strict integers), name the draft graph's non-select nodes once each
     (`node_resource_unknown`/`node_resource_duplicate`); estimates reuse `graph_plan`'s `PlanningEstimate`
     (`estimate-<name>`, GPU type, count 1–8, positive price) and are unique by casefold plus `rstrip(".")`
     (`estimate_duplicate`). Strict models only: no secret can be stored.
   - Both answer `{project_id, current_project_revision, inputs: null | {project_revision, simulation,
     node_time_limits, estimates, saved_at}}`. Inputs saved for an earlier revision come back as they are, with their
     own `project_revision`; a damaged record is `invalid`/`planning_inputs_invalid`, and a save replaces it.
7. **`plan_summary`** nodes add `recipe` (train nodes: `{recipe_id, version, name, training_stage, training_method,
   revision_sha256}`, else null) and `datasets` (a train node's binding `{binding_id, benchmark_revision: null,
   dataset_id, version, sha256}` or an evaluate node's benchmark dataset with `benchmark_revision`, else `[]`).
8. **Legacy `recover` (Q-K).** A test runs the desktop "연결 끊긴 작업 확인" (legacy `recover` through
   `desktop.main`) and `JobStore.recover` against a launched graph attempt whose heartbeat is 0, 60 and 100 s old:
   nothing is recovered, the job stays RUNNING with its attempt and lock. Only an expired heartbeat (600 s) is
   recovered, into the controller's own FAILED-with-lock (uncertain) state.

The native allowlist: `main.rs` `WORKFLOW_ACTIONS` is `[&str; 38]` with the five 9.8 names last, in the Python
order. None is profile- or HF-scoped or vault-free. The Rust tests assert 38 unique names and that the five are
general requests (public settings and profiles, `secrets: {}`). `test_native_contract` asserts exact parity (38),
no scope and the owning modules.

The 9.3 review fixes (`workflow_runs.py`):
- **Minor 1.** Once the approvals pass (a simulation needs none), `graph_submit` first looks the request ID up
  read-only. The same request ID with the same plan hash returns that run, `{run_id, status (now), simulation,
  plan_sha256, controller}`, with no freshness check, no second run and no second receipt. Without both approvals, a
  retry goes through the freshness check as before; a new request ID after a change stays `plan_stale`.
- **Minor 2.** Four tests pin the fail-closed branches: a job holding only its `resource_leases` row counts as
  locked; an unreadable `plan` or `approval` makes `_pins` count the run as affected; a used dataset without a
  pinned snapshot is not exportable (a re-hashed stored plan without `datasets`); an http MLflow URI with a token is
  `conflict`/`plan_stale` at `/tracking`.
- **Minor 3.** `profile_in_use` skips jobs whose manifest has `lineage.simulation` true (as `affected_runs` skips
  simulations); a manifest without a readable lineage still counts (fail closed). The new test claims a simulated
  job pinned to the profile.
- Optional, both done: `tracking_failures` entries carry `attempt_id`, and `profile_usage` filters lock-holding or
  active graph jobs in SQL.

Deviations:
- **`desktop/src/workflow-types.ts` changed (the brief said not to touch `desktop/src/**`).** 10.1 (`6c4b877`), which
  landed while 9.8 was written, pins the typed bridge to `main.rs`: `workflow-core.spec.ts` "the typed bridge lists
  exactly the native workflow actions" requires the TypeScript `WORKFLOW_ACTIONS` to equal the Rust list (plus
  `NATIVE_PENDING`), and `api.ts` refuses any action outside it. After the rebase that test failed (33 against
  38). The only TypeScript change is the five 9.8 names appended to that constant and its count comment
  (`(33)` → `(38)`); no type, view or other file in `desktop/src` changed. Without it the branch's Playwright suite
  would be red and the UI could not call the new actions.
- 38 workflow actions, not 37: items 2, 3, 4 and 6 add five names (the release, the observation list, the inventory
  and the two planning-input actions).
- `graph_release_lock` adds two refusals to the listed ones: `release_not_applicable` (the node needs the operator
  but its attempt is not uncertain: outputs refused, a ledger check, or released already; CLI `release-lock` would
  free a COMPLETED attempt's lock there, the desktop does not) and `lock_not_held` (the lock went between the status
  read and the release). The extra `holds_lock` read before `release_gpu` is the only step the CLI does not take.
  `run_unreadable` covers an unreadable run.
- Refusal statuses: identity problems are `invalid` (`run_unknown`, `run_unreadable`, `node_unknown`,
  `job_mismatch`, `confirmation_required`); a state that moved on is a 409 `conflict` (`attempt_stale`,
  `awaiting_result`, `operator_not_needed`, `release_not_applicable`, `lock_not_held`). None is in the `CONFLICTS`
  text table: the handler returns them directly.
- `executor_profile_inventory` reuses `inspection_failed` and `remote_command_failed` from
  `executor_profile_inspect` (same handling in the UI). It probes without the saved selection, so a selected GPU that
  is gone never hides the listing (`server_inspect` would refuse it).
- `planning_inputs_*` store time limits as `timeout_minutes` (the UI multiplies by 60 for `graph_plan`); the runtime
  profile is not stored (D20 derives it from the recipe). The file name is the SHA256 of the project ID.
- `test_hf_targets` pinned `model_root_targets` as the last action; it now pins it right after `controller_stop`.
- The 9.3 PROGRESS deviation "freshness is checked before idempotency" no longer holds once the approvals pass
  (Minor 1).

Actual verification (main venv with `PYTHONPATH` on the wt95 `src`; `tuning_factory.__file__` resolved into wt95;
`CARGO_TARGET_DIR` on the main tree's target):
- RED, before any `src` edit: 25 Python cases failed for their intended reasons (no `job_id`; `unsupported workflow
  action` for the five new names, also from the two new desktop variants of the 8.5 end-to-end test; `recipe_get`'s
  `revision_sha256` refused as an extra field; no per-node `recipe`; 33 against 38 in the table and native contract
  tests; no `attempt_id` in `tracking_failures`; Minor 1 still `plan_stale`; Minor 3 still `profile_in_use`).
  `cargo test`: **46 passed, 2 failed** (33 against 38; `graph_release_lock` not allowed). The four Minor 2 tests
  passed at once: they pin existing fail-closed behaviour, and each failed under its mutation below.
- Mutations through a harness under `%TEMP%` (each restored after its run; the four touched sources were
  SHA256-checked afterwards): **31 mutants, 30 killed**. They covered the Minor 1 shortcut and its approval gate,
  the four Minor 2 branches, the Minor 3 skip, the SQL filter, `attempt_id`, every release check (confirmation,
  awaiting, needs-operator, uncertain, job, the CLI status check, the CLI attempt check, the lock re-read, the
  stale-token mapping, the store call, the unreadable run), `job_id`, the `recipe_get` exclusivity, the freshness of
  listed observations, the inventory's selection-free probe and RunPod refusal, the planning-input CAS, unknown and
  select nodes, casefolded estimates and the summary's recipe. The survivor is equivalent: the release's node-view
  attempt check and the CLI's lease attempt check both read `active_attempt_id`, so removing either alone changes
  nothing. Reviewing the release guards before the harness added the not-uncertain-node and ledger re-check tests
  and split `release_not_applicable` from `lock_not_held`; the first harness run left the CLI attempt-check mutant
  alive, so the re-check test gained a newer current attempt, which kills it.
- GREEN on `bf9a13b`: the seven files `test_workbench_backend`, `test_native_contract`, `test_desktop_projects`,
  `test_workflow_runs`, `test_workflow_planning`, `test_graph_controller` and `test_gpu_servers` gave **364 passed,
  1 skipped** (baseline 335 passed, 1 skipped). The full suite gave 1628 passed, 2 skipped and 1 failed:
  `test_hf_targets` pinned the last action (deviations); after that fix `test_hf_targets` and `test_workflow_runs`
  gave 116 passed. `cargo test` **48 passed**; Playwright **81 passed**; `npm run build` passes.
- Rebased onto `6c4b877` (9.4 and 10.1):
  - The seven files plus `test_hf_targets`: **445 passed** (the native contract's 9.4 parity test no longer skips).
  - The full suite: **1677 passed, 1 skipped** (10.1's 1648 + 29: 20 in `test_workbench_backend`, 7 in
    `test_workflow_runs`, 2 desktop variants in `test_graph_controller`).
  - `cargo test` **48 passed**. From `desktop/`: Playwright **91 passed** (10.1's count; its parity test failed
    before the `workflow-types.ts` change) and `npm run build` passes.
- Rebased onto `0c38b3f` (10.2; `src` and `tests` unchanged by it): the full suite **1677 passed, 1 skipped**,
  `cargo test` **48 passed**, Playwright **106 passed** (10.2's count) and `npm run build` passes. The CPU
  acceptance on that `src` gave receipt `8fa4f536…41fcd` (source `946e34ee…1384`).
- Rebased onto `7291742` (9.6): the seven files plus `test_cli_graph` **388 passed**, the full suite **1700 passed,
  1 skipped** (9.6's 1671 + 29), `cargo test` **48 passed**, Playwright **106 passed**, `npm run build` passes; CPU
  receipt `7bbe5aff…8640` (source `927cd31e…ce59`).
- On the final tree (rebased onto `d227b7d`, the 9.4 review fixes, then onto `6f05a08`, which changes no `src`,
  `tests` or Rust file, so the Python and Rust inputs are byte-identical to these runs):
  - The seven files plus `test_controller_entry`: **419 passed**. The full suite: **1707 passed, 1 skipped** (the
    9.4 review fixes' 1678 + 29). The known `test_jobs` flake did not occur in any run. On `6f05a08`,
    `test_native_contract`, `test_desktop_projects` and `test_workbench_backend` again gave **64 passed**.
  - `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release`: **48 passed**;
    `git diff --exit-code desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock` is clean.
  - From `desktop/` on `6f05a08`: Playwright **112 passed** (the 10.1 review fixes' count) and `npm run build`
    passes (106 passed on `d227b7d`). The main tree's Vite held port 1420 during
    these runs, and the stock config would have reused it, so the wt95 specs ran against wt95's own Vite on port
    1430 through a scratch config under `%TEMP%` (also for the 81 on `bf9a13b`).
  - Ruff (`src tests`) is clean. `check_secrets.py` reports 0 findings with the change staged. `git diff
    --exit-code` of `containers/`, `pyproject.toml` and `uv.lock` against the base is clean; `desktop/src/` differs
    only in the constant above. Every changed file is LF only (`i/lf w/lf`).
- CPU acceptance (`-I`, wt95's script and source) on the final tree exited 0 and all **29** checks passed. Receipt
  SHA256 `9592153854eff8769ce61737116f8a39366146e20176e878351f91802ccb690d`
  (`D:\llm-tuning-factory-wt95\.factory\model-ops-cpu-receipt.json`), source_sha256
  `fcf8861d34b3f408911cf2ba098fc260434a9fd18e40a54dbcb0da7c72663e55` (equal to `source_hash()` of the final `src`),
  lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max errors are unchanged (merge
  delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8). `model_downloaded`,
  `gpu_used`, `institution_data_used` and `production_promotable` are false. It supersedes the 9.4 review fixes'
  receipt (source `b0f24bdf…ec53f`).

Not verified or still open:
- No UI calls the new actions yet (10.4, 10.5, 10.6 and 10.7 do). The release dialog of the root decision (tick the
  confirmation and type the node label) is 10.7's.
- After a release, the node keeps the controller's last wait reason until the controller's next tick, so
  `needs_operator` can stay true with `uncertain` false; a second release answers `release_not_applicable`. The UI
  should offer the release only when `needs_operator && uncertain && !awaiting_result`.
- No controller ran outside the existing fakes, and no real SSH, RunPod, HF or MLflow was contacted: the inventory
  used `test_gpu_servers.FakeServer` behind the socket guard. No network, GPU, SSH, model download, MLflow write,
  provider call or institution data was used.

## Task 9.6 review fixes, 2026-10-21

Commit `fix: drain graph-work on a real Ctrl+C and keep CLI output encodable`, written on `d227b7d` (the 9.4 review
fixes) in the worktree `D:\llm-tuning-factory-wt96` and rebased onto `128f995` (10.1 review fixes and 9.8; no
conflict). It answers the independent review of `7291742`, which was "Ready with fixes" with one Critical. Changed:
`src/tuning_factory/cli.py` (the 9.6 part only), `tests/test_cli_graph.py` and this file.

Critical 1, a real Ctrl+C killed the controller instead of draining it:
- Cause: a CPython 3.12 bug in `Thread._wait_for_tstate_lock`. When `KeyboardInterrupt` lands inside `join()`, the
  thread is marked stopped while it still runs. The main thread waited in `worker.join(0.2)`, so a real Ctrl+C almost
  always landed there.
- The reviewer saw this in 6 of 6 probes. `_wait_for_controller` returned after the first press with exit 1. The final
  line went to the controller's null stdout. The process left mid-`run_forever` with the owner lease held and no
  `stopped_at`, and a restart got `owner_held`. The 130 path was unreachable.
- The 9.6 tests passed because they raised `KeyboardInterrupt` in the `_join` seam, before `Thread.join`.
- Fix:
  - `graph-work` installs its own SIGINT handler (`_CtrlC`) in the main thread before it takes the process lock and
    starts the controller thread, and restores the previous handler afterwards.
  - A press never raises. The first sets the stop event (`run_forever` drains, D14) and prints the note; the second
    calls `_exit_now(130)`.
  - The main thread waits on a `done` event that the thread sets after `serve` returned, so stdout is restored before
    the final line. It never calls `Thread.join` or `is_alive` while a press can arrive; it joins only after `done`.
  - This also closes Minor 2: a press right after `worker.start()` could release a lock the running worker held, and
    a press between `presses += 1` and `stop_event.set()` escaped. Outside the main thread no handler is installed;
    Python delivers SIGINT only to the main thread.
- Tests, which press through the real interrupt path rather than a seam:
  - `_thread.interrupt_main(SIGINT)` trips Python's SIGINT handling the way the C-level handler does for a console
    Ctrl+C, on Windows and POSIX alike. It is used on both.
  - `signal.raise_signal` is not used: on Windows it runs the C handler on the calling thread.
  - `test_a_real_ctrl_c_drains_then_releases_the_owner`, in process, pressed as soon as the controller runs and again
    after the run completed: exit 0, the `stopped` final line, the owner released, `current.json` `stopped_at`, the
    handler restored, nothing locked or active, and a restart runs at once.
  - `test_a_second_real_ctrl_c_exits_at_once_and_keeps_the_owner_until_its_ttl`, as a subprocess. The child holds
    the drain open for at most 20 s and presses twice. It exits 130 with only the ready line, the owner lease is
    still held, there is no `stopped_at`, and a restart gets exit 3 `owner_held`.
  - Both are bounded. A controller that ignores Ctrl+C is stopped through its stop request after 30 s, and the test
    then fails; the child process has a 30 s limit and is killed in `finally`.

Minors:
- 3, encoding of redirected output:
  - The new commands print the v1 `output` text (readable UTF-8 JSON) when stdout can encode it, and ASCII-escaped
    JSON otherwise. Before, `project-list` on cp949 crashed on é, an em dash or an emoji.
  - `graph-work`'s final line is ASCII JSON, like the ready line. Before, exit 4 on cp1252 crashed and gave exit 1.
  - The v1 `output` function and every v1 command are unchanged.
- 4, unknown keys echoed in issue pointers:
  - Profiles-file and plan-request-file refusals now point at the object holding an unknown key (`extra_field`), and
    a pointer stops at the first segment that is neither a field name nor a list index (a dict key). Values were never
    echoed.
  - The request file is validated with `GraphPlanRequest` in the CLI before the action, so this applies there too.
  - The workflow channel itself, `desktop_workflows._pointer` (9.1), still keeps the key for desktop requests. A fix
    for the app belongs there; it was not edited here, because 9.8 may be editing that file.
- 5, uniform `graph-work` output: every outcome now ends with the final line `{"ok", "exit_code", "code", "message",
  "controller"}`, plus `issues` for a refused profiles file. Exit 2 means refused before the start:
  - `approval_required`: `--executor-profiles` without the approval flag;
  - `controller_in_process`: the process lock;
  - `invalid_start`: a refused profiles file (it was exit 1 with a `WorkflowResult`), MLflow settings that form no
    scope, or invalid start settings.
  - Option-parsing errors (for example `--tick-seconds 0.01`) are still Click usage errors. `--help` documents the
    shapes and the exit codes 0, 1, 2, 3, 4 and 130.
- 6: the Ctrl+C note and `--help` say that after a second Ctrl+C a restart is refused (exit 3) until the 60 s owner
  TTL passes, and that a step left in flight may need `release-lock --confirm-execution-stopped`.
- 7: the `graph-work` help no longer has hard line breaks (`GRAPH_WORK_HELP`, joined paragraphs).

Actual verification (main venv with `PYTHONPATH` on the worktree `src`; `--basetemp` under `%TEMP%`):
- RED first, with the new tests against the unchanged `7291742` CLI: 10 failed, 15 passed.
  - Both real-Ctrl+C cases: exit 1, stdout holding only the ready line (`assert 1 == 0`).
  - The second press: `assert 1 == 130`.
  - Exit 4 on cp1252 and `project-list` on cp949: `UnicodeEncodeError`.
  - Pointers `/command` and `/profiles/0/RUNPOD_API_KEY` echoed the keys.
  - The exit-2 refusals printed no final line.
- GREEN: `tests/test_cli_graph.py` **25 passed**, three runs in a row (16.8 s, 17.8 s, 16.2 s).
- Mutations of the fix, each restored and checked by SHA256. All 8 were killed:
  - the first press not setting the stop event;
  - the second press ignored;
  - no handler installed;
  - no ASCII fallback;
  - a non-ASCII final line;
  - the unknown key kept;
  - a dict key kept;
  - an exit-2 refusal without a final line.
  - The unknown-key mutation first survived, because the truncation alone already hid keys that are not field
    names. A case was added for an unknown key spelled like a nested field (`profile_id` at the top level).
- `test_runner_cli`, `test_controller_entry` and `test_workflow_runs`: 10 + 54 + 29 = **93 passed**, unchanged.
- Full suite: **1680 passed, 1 skipped** (the baseline 1678 plus 2 tests; the 3 Ctrl+C items were replaced one for
  one). The one skip is the real-GPU test.
- Ruff is clean. `check_secrets.py` reports 0 findings with the change staged. Every changed file is LF only.
  `git diff --exit-code containers/ pyproject.toml uv.lock desktop/` is clean.
- CPU acceptance (`-I`, into wt96's `.factory`): exit 0, **29/29**.
  - Receipt SHA256 `4cce7785542d20a6032f20c0fafe201d5664175cb3ebc7fa982b42d478c71880`.
  - `source_sha256 6c48384efbe715c337e338544b2759d7142cf0228dad455cb401cc4e4660c068`, equal to `source_hash()` of the
    tree.
  - `lock_sha256 5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`.
- After the rebase onto `128f995` (9.8 changed `workflow_runs`, `workflow_planning` and `desktop_workflows`):
  - `tests/test_cli_graph.py` **25 passed** three times.
  - `test_runner_cli`, `test_controller_entry` and `test_workflow_runs`: 10 + 54 + 36 = **100 passed**.
  - Full suite: **1709 passed, 1 skipped** (the upstream tree plus the 2 new tests).
  - CPU acceptance: exit 0, **29/29**.
    - Receipt SHA256 `674d493a5d2e9becf64a4543921dc3b51b778eea73638562197275e24854588d`.
    - `source_sha256 5bb65fd9d56a68447d87b3d121267cdb636b26b0c2cc9d36215f3b343c690982`, equal to `source_hash()`.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

Not verified:
- A Ctrl+C typed into a real console, and Ctrl+Break. The tests trip Python's SIGINT handling with
  `_thread.interrupt_main`, which is the same Python-level path but not the console's `CTRL_C_EVENT`.
- The Linux CI run (only Windows was run here).
- On Windows a console Ctrl+C also reaches child processes in the same console (for example a `git` child of the
  manifest), as before.

## Task 9.8 review follow-ups, 2026-10-21

Commit `fix: tighten workbench planning inputs, freshness hints and release tests`, on `b040533` (the 9.6 review
fixes, on 9.8's `128f995`) in the worktree `D:\llm-tuning-factory-wt96`. The 9.8 review was "ready to merge" with
Minors only. Changed: `src/tuning_factory/workflow_planning.py`, `src/tuning_factory/workflow_runs.py`,
`tests/test_workbench_backend.py` and this file.

The fixes:
1. `planning_inputs_save` no longer crashes on a draft node whose `id` is a list or an object. `ProjectSpec.graph`
   is free JSON, and the set of planned IDs raised `TypeError: unhashable` (the sidecar answered
   `{"ok": false, "error": "TypeError"}`). Only string IDs are collected, so such nodes are nodes no time limit can
   name.
2. `observations_list`'s `fresh_for_real_plan` now says what `graph_plan` will decide.
   - New `pin_observation(profile_id, fields, observation)`: the key paths check, the revision is the observed one,
     and `public_profile` rebuilds from the current fields with the observed facts.
   - New `observation_fresh(age)`: at most an hour, and not from the future.
   - `_native_executor` and the list both use these two functions. Before, the list compared only the revision.
   - The probe: an SSH key path `~/keys/...` whose home directory changed after the observation. The list said
     fresh, while `graph_plan` said `observation_revision_differs`.
   - `profile_current` keeps its meaning (the revision only).
3. The `graph_submit` replay shortcut now heals a missing approval receipt. `_submitted` also returns the run's
   `created`, and the shortcut calls `_write_receipt`. `immutable_write` of identical bytes changes nothing, so a
   further retry writes no second receipt and leaves the state bytes unchanged (tested).
4. Test gap: a forged `attempt_id` on a RUNNING node gives `attempt_stale`, not `operator_not_needed` (identity is
   checked before state).
5. Nits:
   - The `graph_release_lock` docstring now says that a takeover makes the release fail as stale only when it
     rotates the token between `current_lease` and `release_gpu`. One before `current_lease` keeps the attempt ID,
     and the release goes ahead as the CLI's would.
   - The legacy recover test uses a 100 s heartbeat (it said 119 while this file said 100; this file's 9.8 entry now
     matches the test).
   - New test: a canceled uncertain attempt is released, then `classify_attempt('canceled')` settles the node and
     the job CANCELED, with disposition `canceled`.

Actual verification (main venv with `PYTHONPATH` on the worktree `src`; `--basetemp` under `%TEMP%`):
- RED first, with the new tests against 9.8's code: 3 failed, 21 passed.
  - `TypeError: unhashable type: 'list'` in `planning_inputs_save`.
  - `fresh_for_real_plan` was `True` while the real plan reported `observation_revision_differs` for the profile.
  - The receipt file was missing after the retry (`FileNotFoundError`).
- The forged-attempt and canceled-release tests passed at once: they pin behaviour that was already correct (test
  gaps).
- GREEN: `test_workbench_backend`, `test_workflow_planning` and `test_workflow_runs`: 24 + 84 + 36 = **144 passed**.
- Full suite: **1713 passed, 1 skipped** (the 9.6 review fixes' 1709 plus the 4 new tests). The one skip is the
  real-GPU test.
- Ruff is clean. `check_secrets.py` reports 0 findings with the change staged. Every changed file is LF only.
- CPU acceptance (`-I`, into wt96's `.factory`): exit 0, **29/29**.
  - Receipt SHA256 `f035bcc9e339da623f8a31adf67d9fc7adef0ff763ba9ed36c1d27cca39383be`.
  - `source_sha256 0849b47eddb852af7c333a84a5baa440c4bd661009a3e438ae8386723c6bd56c`, equal to `source_hash()`.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

Not changed: `controller_entry._observed_profile` (9.4), which `controller_status` and the controller start use,
keeps its own rule. It compares the rebuilt profile with the stored one (`observation_invalid`) rather than calling
`pin_observation`. On the home-directory probe the rebuild raises, so the controller skips the profile
(`invalid_fields`) as planning refuses it, but the two are not one function.

## Task 10.4 executor profiles and the controller bar, 2026-10-21

Commit `feat: manage executor profiles and the background controller from the workbench`, written in the main tree on
`128f995` (9.8) and fast-forwarded onto `42fbb0d` (the 9.6 and 9.8 review fixes, which change no desktop file). It
implements [Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.4" with D9, D10, D14 and
the root decisions (9.8's `executor_profile_inventory` and `observations_list`). Changed: new
`desktop/src/views/executor-profiles.{ts,css}`, `desktop/src/components/controller-bar.ts`,
`desktop/tests/executor-profiles.spec.ts`; `desktop/src/api.ts` (two wrappers, see deviations) and this file. No
Python, Rust, package or lock file changed.

What changed:
- 실행 프로필 (settings, order 20; `data-tour="executor-profiles"` once). Entering reads `executor_profiles_get`,
  `observations_list` and `controller_status` only. The list shows allowlisted public fields, the key flag or GPU
  count, and the last check (age, GPU × count, price, cost source, `fresh_for_real_plan`).
- Save and delete use the native commands (provider and ID fixed once saved; delete behind a dialog); the list is
  read again, `store.markProfilesChanged()` runs, `affected_runs` shows "이 변경 후 다시 계획해야 하는 실행: …";
  refusals are shown verbatim and the form keeps its public values. The RunPod key is a password field read once at
  save time, sent only when typed, never rendered, stored or logged, and empty after every save.
- SSH GPUs are checkboxes from `executor_profile_inventory`; "자원 확인" runs `executor_profile_inspect` (session
  result for 10.6, "1시간 이내"). Both run only after a read-only notice dialog.
- `#controller-bar`: 정지 / 동작 중 · 마지막 신호 N초 전 / 연결 끊김 / 정지 요청됨 / not installed (start and stop
  disabled); `broker` only while none runs; one restart banner with the D5 procedure for changed profiles, MLflow
  changes and a key saved for a `credential_missing` profile; a missing MLflow credential is not called a restart;
  `tracking: null` shows nothing. Start is click-only (`already_running`, the redirected-AppData text); stop shows the
  drain dialog. Polling per D10 (5 s, one call at a time, backoff incl. `status_busy` with the last status kept, never
  while busy, hidden or left; `not_available` stops it; patches only on change).

Deviations:
- `desktop/src/api.ts` gained `observationsList()`, `profileInventory(profile_id)` and their two result interfaces
  (`ObservationSummary`, `ProfileInventory`), declared there because `workflow-types.ts` stays unchanged. Additive
  only; nothing else in the file changed.
- Test 1 expects three entry calls (the plan lists two): `observations_list` is read-only and is how the root decision
  shows the last check. Test 3 picks GPUs from the inventory instead of typing UUID lines, so `ProfileDraft` has
  `gpus: string[]` instead of `gpuLines`. No selection is sent as `""` (the native store's value before a selection);
  an unchanged selection keeps its saved text, so a no-op save keeps the profile revision.
- A typed RunPod key is not kept after a refused save: it lives only in its input until the save reads it, and the
  task's re-render clears it (test 5 asserts the empty input), so it never enters module state (10.1 implementation
  note).
- Twelve tests, not ten: test 11 (`status_busy` backoff), test 12 (restart, credential, skipped-profile and broker
  notes). Test 5 also refuses a delete after a save that named runs to plan again.
- `controllerBarHtml(state, busy, host = "profiles")` and `attachControllerBar(ctx, {host, queue, profiles})` take
  the host view, because the bar's buttons route through `wf:<host>:controller-start|stop` (`controllerAction` for
  10.7). `ControllerBarState` adds optional `busyRead`, `profiles` and `settings`. The bar's rules live in
  `executor-profiles.css` (the only CSS file of 10.4); the eager view bundle loads them on every page.
- Playwright ran through a scratch config under `%TEMP%` with this tree's own Vite on port 1444
  (`reuseExistingServer: false`), so no other agent's server on 1420 could be reused.

Actual verification:
- RED first: all 12 new tests failed on the missing `실행 프로필` sidebar entry (`nav:profiles` not found).
- 21 mutations of the new code (key only when typed, reload after save, GPU selection, fixed provider, both notice
  dialogs, the busy submit guard, both `affected` resets, backoff and the kept status on `status_busy`, the
  `ROOT_UNUSABLE` text, the stop dialog, the credential-missing and skipped-key rules, broker hidden while running,
  start disabled when unavailable, `tracking: null`): 20 failed a test. The survivor is equivalent: removing the
  detach on leave still makes no call, because the poller's `active()` also checks the page.
- From `desktop/`: `npx playwright test` (scratch config) **124 passed** (112 existing, unchanged, plus 12 new; the
  new spec also passed three times in a row); `npm run build` passes.
- From the root: `uv run pytest -q` **1707 passed, 1 skipped** on `128f995` and **1713 passed, 1 skipped** on `42fbb0d`;
  `uv run ruff check src tests` passes; `scripts/check_secrets.py` reports 0 findings with the change staged;
  `git diff --exit-code src/ desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json
  desktop/package-lock.json` is clean; every changed file is CR-free.

Not verified or still open:
- Real WebView2 (dialogs, focus, timers, the native file picker for key paths), a real sidecar and controller, real
  Credential Manager: Task 11. No network, GPU, SSH, RunPod, MLflow, model download or institution data was used.
- The end-to-end effect of `markProfilesChanged()` on a held plan is 10.6's test 4(b). The runs page (10.7) mounts
  the same bar.
- A RunPod key rotated without any other change, and MLflow credentials re-saved without a public change, need a
  controller restart that polling cannot report; the bar and the key field say so.

## Task 10.2 review fixes: serialized workbench tasks, stable textarea drafts, 2026-10-21

Commit `fix: serialize workbench tasks and keep textarea drafts stable`, written in the worktree
`D:\llm-tuning-factory-wt102` (branch `codex/task-10.2-fix`, from `128f995`). It lands the independent review of
`0c38b3f` and `6f05a08` ("Ready with fixes": two Important, nothing Critical; it confirmed D17 byte identity for 41
stage/method combinations, the request payloads against the strict models and the recipe defaults). Changed:
`desktop/src/{main,workbench,issues,view-types}.ts`, `desktop/src/views/{projects,recipes}.ts`,
`desktop/tests/{projects,recipes,navigation,workflow-core}.spec.ts`; new `desktop/tests/recipe-defaults.json` and
`tests/test_desktop_recipe_defaults.py`; this file. No `src/` Python, Rust, package or lock file changed.

What changed:
- I1. Tasks may overlap, so busy now lasts until the last running task ends (its label shows the latest one). A view's
  `enter` waits for running tasks and is skipped if the user left the page meanwhile; messages the waited-for tasks
  left stay visible. A task's final render reads the drafts first when the user has moved to another page and puts
  the focus back on the control they were in; on its own page the task's result wins and focus returns to the
  starting control only there. The views' own focus moves are body-only and same-page; a project created or imported
  after the user moved on no longer pulls them to its page (a notice says where it is); the views do not read back
  forms locked while busy, so a reset draft (the created project's name) is not restored from the locked form.
- I2. Textareas render a leading newline before the value (the parser drops one), and a text that differs from the
  stored one only in line endings (CRLF from another writer) is kept as stored: no phantom "저장 안 된 변경", and the
  draft is re-read on reopening.
- M1. `localize` translates a text containing Korean only by a known English pattern inside it, never by its code
  (version_exists with hf_targets' Korean TARGETS_CHANGED keeps its own advice).
- M2. After a keyboard close, only a keyboard activation of the control the focus was returned to is swallowed;
  touch and pen use a 24 px radius.
- M3. The project list coerces its counts to numbers. M4. A patch waits while an IME composes in its region and
  applies after `compositionend`. M5. A test pins that a view render during a press after a task finished keeps the
  task's result. M6. `desktop/tests/recipe-defaults.json` (generated from `RecipeSpec`) pins the recipe defaults: the
  Python test compares it with the model, `recipes.spec.ts` with `recipeFromTemplate({})`. M7. The example choice is
  locked while busy; 저장 is disabled once the channel answered `not_available`; the recipe editor marks OFT as
  unsupported for PPO and refuses that combination locally with a Korean reason.

Actual verification:
- Against the worktree's own Vite on port 1440 (a scratch Playwright config under `%TEMP%`; the server answered 200
  for the worktree's `@fs` root and 403 for the main tree's), never the main tree's 1420.
- RED first, on `128f995`: busy cleared while a save was still held (and the list read did not wait); a late save
  render wiped typing on 데이터 (`my_dataset`); a created project pulled the user to its page; a refused create
  focused the projects page's 만들기; a leading-newline/CRLF purpose and description became dirty; the list rendered an
  unescaped count (`img` 1); 저장 stayed enabled after `not_available`; the example choice stayed enabled while busy;
  PPO with OFT reached `recipe_save`; a keyboard activation of another control after a keyboard close was swallowed; a
  second tap 14 px off went through; a patch replaced the composing input; `version_exists` with Korean text was
  translated; two overlapping tasks cleared busy when the second ended. M5 and the M6 parity test pass by design (no
  bug or drift today) and fail under their mutations.
- Twenty-one mutations (each fix above, including the busy counter, the `enter` wait, the message keeping, the
  locked-form skip, the same-page focus rule and `!renderDeferred` in `renderKeepingDrafts`) each made a test fail.
- From `desktop/`: `npx playwright test` **126 passed** on `128f995` (112 + 14 new) and **138 passed** after the
  rebase onto `4b4c194` (with 10.4's 12); `npm run build` passes on both.
- From the root (main venv, `PYTHONPATH` = the worktree's `src`): `pytest -q` **1709 passed, 1 skipped** on `128f995`
  (1707 + the 2 parity tests) and **1715 passed, 1 skipped** after the rebase; `ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; the protected-path `git diff --exit-code` is
  clean; every changed file is CR-free.

Not verified or still open:
- Real WebView2 timing (overlapping tasks, IME composition, touch): Task 11. Full renders during an IME composition
  (not patches) are not deferred.

## Task 10.3 data import and split, 2026-10-21

Commit `feat: map CSV and JSON columns and split datasets in the Windows workbench`, written in
`D:\llm-tuning-factory-wt95` (branch `codex/task-10.3`) on `128f995` (9.8). It was rebased onto `4b4c194` (the
9.6 review fixes, the 9.8 follow-ups and 10.4) and then onto `0a19c37` (the 10.2 review fixes). Only this file
conflicted each time; every entry is kept, this one last. It implements the
[Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.3" with D1–D5, D16 and D18.
Changed: new `desktop/src/views/data-import.{ts,css}` and `desktop/tests/data-import.spec.ts`; modified
`desktop/src-tauri/src/main.rs` (`pick_file` and one test), `desktop/src/views/projects.ts` (one link, see
deviations) and this file. No Python, package, lock or Cargo file changed.

What changed:
- The `data-import` page (child of 데이터; `nav:data-import[:<projectId>]`) runs seven numbered steps; entering it calls
  nothing.
  - 1 원본 파일 (`data-tour="data-import"`, once): "파일 찾기" (`pick_file` kind `import`) or a typed path, then
    "가져오기" → `dataset_import_source`. Refusals (`local_file_only`, `file_unreadable`, `size_limit`) show at the
    path. The same click runs the column-discovery preview (`fields: {}`).
  - 2 형식: format guessed from the extension; encoding (CP949 offered for CSV only and dropped on a switch to
    JSON/JSONL); CSV delimiter; data kind.
  - 3 열 연결: one select per canonical field of the kind (`CANONICAL_FIELDS`, required marked), filled from
    `source_fields`; same-named columns are mapped on discovery; KTO true/false label sets (a one-sided set is
    refused before any call); the first five source rows, escaped.
  - 4 미리보기: a status line (passed, `문제 N건`, or "형식이/열 연결이 바뀌어 다시 미리보아야 합니다"); issues up to
    50 ("외 N건") labelled "N행" (1-based, header excluded), "헤더" or "파일", in Korean with "원문 보기" (the codec
    error shows the CP949 hint); the five canonical samples, escaped. A discovery preview lists only file-level issues.
  - 5 등록: enabled only for a current preview with status `ok` and no issue; sends the previewed mapping and
    `preview_sha256` with `synthetic`/`export_allowed` as booleans (default false). `version_exists` and
    `practice_reserved` show under their field (the backend's Korean text for the second); `preview_changed` asks
    for a new preview. Success shows `ID · vN · SHA256 8자` and reloads the catalog.
  - 6 분할 (선택, after registration): whole percentages summing to 100 (`basisPoints`, else "합계가 100%가 되어야
    합니다." with no call), seed 42, an optional group column from the registered import's source columns, output
    IDs `<id>-train/-validation/-test` and version 1; a 0% role gets no output. `dataset_split_preview` shows
    "학습 n · 검증 n · 시험 n"; "분할 등록" → `dataset_split_commit {plan_sha256}` for a current preview only, then
    the catalog reloads.
  - 7 프로젝트에 연결 (project context only): `store.open` (the latest saved revision; a dirty draft is kept), then
    `store.edit` adds the split outputs by role, else the registered version as 학습, with `bindingIdFor` IDs; a
    version already bound is not added again. The page lists "저장 전 연결: …"; saving stays on the project page
    (no `project_save`).
- Edits to the mapping, labels and split inputs patch only the derived regions (status line, register button, split
  state), so typing, focus and Tab order are kept; format and kind changes re-render and keep the select focused.
  Every step is a task (controls disabled while busy) and also refuses a programmatic submit while busy.
- The shell rules of the 10.2 review fixes: `capture` skips controls locked while busy (a disabled fieldset), so
  leaving the page during a step cannot undo its result. A step's result takes the focus only while the user is still
  on the same page and project. No step navigates from inside its task.
- The 데이터 page shows a slot card "열 연결해 가져오기 · CSV·JSON·JSONL" after the legacy registration card; the legacy
  form, its anchors and button names are unchanged. The card holds one navigation button and no form control, and it
  runs no task beside the legacy forms (which are never locked). Every import step runs on the `data-import` page.
- Native (D18): `pick_file` uses a pure `picker_filter(kind)`: `import` → `csv`, `json`, `jsonl` ("가져올 데이터");
  `dataset` → `json`, `jsonl` (unchanged); every other kind, including `key`, is unfiltered (unchanged).

Deviations:
- `views/projects.ts` (10.2's file) gained one button, "CSV·JSON 가져오기" → `nav:data-import:<projectId>`, in the
  data-binding card, shown only when the `data-import` view exists. D1 says the page is reached "from a project" and
  test 7 stages data for "the project that opened the import", but no sub-task owned that link. Without it the
  project step cannot be reached in the UI.
- Test 7 opens the import from that link instead of an arbitrary `nav:data-import:<id>`. The project was created by
  a direct bridge call (another window), so the project page reads it; "프로젝트에 연결" then calls `project_get`
  again through `store.open`, as the plan's test expects.
- Thirteen Playwright tests plus the Rust test, not nine plus one. The extra four are:
  - test 7b, a registered version without a split is staged as 학습;
  - a KTO label test;
  - a pure test of `CANONICAL_FIELDS`, `mappingFrom` and `basisPoints`, imported in the page;
  - a test that leaving the page during a step keeps that step's result.

  Test 9 also checks that typing in `#dataset-form` and `#s3-form` survives a round trip through the slot card. The
  KTO and pure tests, and that test 9 check, were written after the code: they are characterization checks, not RED
  first. Targeted mutants (below) fail the KTO and pure tests.
- `ImportDraft` derives staleness from the previewed mapping (`previewSent`) instead of a `previewStale` flag, and
  carries the source fields, issues and registration state. Exports `emptyDraft`, `emptySplit` and `guessFormat` are
  extra. The split output version is editable ("분할 버전", default 1). Splitting a version registered earlier
  elsewhere is not offered: the split source is the version registered on this page.

Actual verification:
- RED first: before any `src` change, all eleven first-written Playwright tests failed. Ten failed on the missing
  slot button, page or project link; the pure test failed on the missing module. The Rust test failed to compile
  (`cannot find function picker_filter`).
- Mutants of `data-import.ts` and the project link, each run against the spec. Every one failed a test except two
  equivalent mutants. Removing both layers of the busy guard together fails test 8. Sending `mappingFrom(draft)`
  instead of the previewed mapping is equivalent, because registration requires them to be equal. The mutants were:
  stale mapping, stale format, discovery rows, the busy guard, the percentage sum, booleans, the reload before
  staging, the picker kind, cell escaping, CP949 for JSON, the zero-role output, duplicate staging, version focus,
  split staleness, the row label, the catalog reload, the project link, the one-sided KTO set, typing without derived
  refresh, dropped labels, and full re-render on edits.
- After the rebase onto `0a19c37`, the leaving-the-page test failed first: the navigation's capture read the locked
  split form back (`qa-train` instead of the new registration's `qa2-train`). Skipping locked controls fixed it.
- Python, run from the root with the main venv and `PYTHONPATH` set to wt95's `src`:
  - On `128f995`: **1706 passed, 1 skipped, 1 failed**. The failure was
    `test_graph_controller.py::test_ambiguous_launch_waits_for_the_operator[False-desktop]`: a `PermissionError`
    (WinError 5) renaming a file in its pytest temp directory while Playwright ran beside it. That file alone then gave
    **65 passed**, so 1707 passed.
  - On `4b4c194`: **1713 passed, 1 skipped**.
  - On the final tree (`0a19c37` plus this commit, which changes no Python): **1715 passed, 1 skipped**, run alone.
  - `tests/test_native_contract.py`: **7 passed** on each tree.
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release` (target dir of the main
  tree): **49 passed** (48 + 1) on each tree. `git diff --exit-code desktop/src-tauri/Cargo.toml
  desktop/src-tauri/Cargo.lock` is clean.
- Playwright from `desktop/`, against wt95's own Vite on port 1430. A scratch config under `%TEMP%` was used because
  the stock config would reuse the main tree's 1420.
  - `npx playwright test`: **124 passed** on `128f995` (112 + 12), **136 passed** on `4b4c194` (124 + 10.4's 12) and
    **151 passed** on the final tree (the 10.2 review fixes' 138 + 13).
  - The new spec passed three times in a row on the final tree.
  - `npm run build` passes on each tree.
- Ruff (`src tests`) passes. `check_secrets.py` reports 0 findings with the change staged. `git diff --exit-code`
  against the base is clean for `src/`, `containers/`, `pyproject.toml`, `uv.lock`, `desktop/package.json` and
  `desktop/package-lock.json`.
- Every changed file is LF only.

Not verified or still open:
- Real WebView2 and the real Windows file dialog filter (`rfd`), a real sidecar import of a CP949 CSV, and focus
  behaviour of native selects: Task 11. The 10.8 tutorial step for `data-import` is not added here.
- The page keeps one import draft for the session. Opening the import from another project shows the same draft, and
  "프로젝트에 연결" stages into the project named in the page argument and shown in the banner. Every pre-rebase
  mutant was run on `128f995`; only the leaving-the-page test was added after the rebase.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

## Task 10.4 review fixes: truthful profile freshness and controller status, 2026-10-21

Commit `fix: keep profile freshness and controller status truthful across edits and re-entry`, written in the worktree
`D:\llm-tuning-factory-wt104` (branch `codex/task-10.4-fix`) on `0a19c37` (the 10.2 review fixes, on 10.4's
`4b4c194`) and fast-forwarded onto `f6493a1` (10.3, which changes none of these files). The independent review of
`4b4c194` was "Ready with fixes" (two Important, nothing Critical); the 10.2 re-reviewer added two Minors. Changed:
`desktop/src/views/executor-profiles.ts`, `desktop/src/components/controller-bar.ts`,
`desktop/tests/executor-profiles.spec.ts` and this file.

Required:
- I1. After a save or delete the stored checks are read again (`observations_list`, through the page queue), this
  session's inspection of the profile is dropped (so `sessionInspections()` never hands 10.6 a pre-edit one), and a GPU
  list read with other connection details is dropped.
- I2. The bar's last read is cleared on attach and on detach: a page shown again says "상태 확인 중" (no signal age,
  stop disabled) until its own first read answers, and a read that ends after leaving is shown nowhere.

Recommended, all done:
- M1. Save and delete run through the page queue, so a status poll and the edit-check sidecar never run at once. The
  10.4 entry's "one call at a time" was not true for them before this fix.
- M2. A poll patches only `#controller-bar-body`, and only when the read changed apart from the heartbeat age; an
  age-only change updates the signal text in place. The one live region (`role="status"`, "백그라운드 컨트롤러: …")
  sits outside the patched markup and changes its text only on a transition. The pill now shows the state alone; the
  age is a separate non-live span (`#controller-bar-signal`).
- M3. For `oidc`, an empty token URL or client ID counts as an incomplete credential (auth.py needs the URL, the client
  ID and the secret), not as a restart.
- M4. The key field says a re-render clears a typed key; a save that leaves a RunPod profile without a saved key says
  it cannot be used yet.
- M5. A spec pins `ROOT_UNUSABLE` to `broker.rs` (and the mocked `PROFILE_IN_USE`/`EDIT_CHECK_FAILED` texts to
  `profiles.rs`); the dead `"ROOT_UNUSABLE"` branch is gone.
- M6. One `issueText` (exported by the bar) replaces both `describe`s; the form builds its inputs and the provider
  select itself instead of patching `field()`/`select()` output; `gib()` guards non-numbers; a 30 s timer re-renders
  the rows (`#profile-rows`, only when their text changes) so ages and "1시간 이내" stay true on an open page.
- 10.2 re-review Minors: focus after a save or delete moves only when a render lost it to `body` and the user is still
  on the page (`stillOn`/`refocus`); `captureForm` skips a form whose fieldset is disabled; the key picker moves the
  focus only from its own button.

Deviations: the bar's pill text no longer carries the signal age (M2), so test 7 checks the pill and
`#controller-bar-signal` separately (the plan's "동작 중 · 마지막 신호 N초 전" still reads that way on screen). Test 18 is a pin that passed on `4b4c194` (it failed when the bar's copy of the
text was changed).

Actual verification:
- RED first: the nine new behaviour tests failed for their intended reasons (one `observations_list` call instead of
  two; "동작 중 · 마지막 신호 2초 전" on re-entry; no persistent live region; no OIDC text; no key help line; no
  freshness re-render; the save ran beside a gated poll; focus taken from a control outside `#app`; a locked form read
  back as "잠긴 입력"), and test 7 failed on the old pill text.
- 17 mutations of the fixes: 15 failed a test. The two survivors each removed one of the two I2 resets (attach or
  detach), which cover each other; the mutation removing both fails test 14.
- From `desktop/`: `npx playwright test` (scratch config, this worktree's own Vite on port 1470,
  `reuseExistingServer: false`) **148 passed** on `0a19c37` (138 + 10 new) and **161 passed** on `f6493a1` (with 10.3's 13); the 22 profile tests also passed alone; `npm run build` passes.
- From the root (main venv, `PYTHONPATH` on this worktree's `src`): `pytest -q` **1715 passed, 1 skipped** on `0a19c37` and again on `f6493a1`; Ruff passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; the protected-path `git diff --exit-code` is
  clean; every changed file is LF only.

Not verified or still open:
- Real WebView2 screen-reader announcements of the live region, timers and focus: Task 11.
- A RunPod key rotated with no other change, and MLflow credentials re-saved, still need a restart polling cannot see.

## Task 10.7 graph runs (polling), 2026-10-21

Commit `feat: follow graph runs by polling with operator guidance`, written in the main tree on `4b4c194` (10.4),
fast-forwarded onto `0a19c37` (the 10.2 review fixes) before the implementation, then rebased onto `f6493a1` (10.3)
and `ca63dbe` (the 10.4 review fixes; only this file conflicted, both entries kept). It implements [Task 10
plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.7" with D10, D12, D15 and the root decision
that replaces D11 (the desktop operator release through 9.8's `graph_release_lock`). Changed: new
`desktop/src/views/graph-runs.{ts,css}`, `desktop/src/wait-reasons.ts`, `desktop/tests/graph-runs.spec.ts`;
`desktop/src/api.ts` (one wrapper, see deviations) and this file. No Python, Rust, package or lock file changed.

What changed:
- 그래프 실행 (워크스페이스, order 50; `data-tour="graph-runs"` once, on the list card) with 10.4's controller bar
  (`attachControllerBar(ctx, {host: "runs", queue})`, `controllerAction`). Entering reads `graph_runs_list` and
  `controller_status` only; `nav:runs:<run_id>` adds `graph_run_get` and, once per opened run, `project_get` at the
  plan's project revision (the merge relation of the selection texts; a failure leaves them generic).
- Polling per D10: detail every 3 s, list every 10 s, bar every 5 s, all through one page queue (one sidecar call at a
  time); next delay `max(base, 2 × last)`, backoff to 60 s; nothing while busy, hidden or left; showing the window
  again reads at once; the detail stops when the run is settled (final status, graph tracking
  `saved`/`failed`/`uncertain`, no node unfinished, holding an uncertain attempt or with tracking or registration
  `pending`/`retrying`); a region is patched only when its HTML changed, keeping focus and open "원문 보기".
- Detail: status, 모의/실제, plan hash, created, approved and reserved cost, graph tracking with its action; the
  budget texts ("승인 예산 소진: 예약 $R / 승인 $A — …", "가격을 알 수 없는 이전 시도 n개가 …"); a node table with
  label, kind, status, counted/max attempts, destination, the Korean wait reason (kind in `data-reason`, original in
  "원문 보기"), error, retry time, tracking and registration, output (artifact 8자 or metrics with the text-metric and
  format-metric notes), the selection text (no winner, 병합 모델 선택, 원본 최상 모델 유지, simulated) and the cache-seed
  note. `wait-reasons.ts` maps the controller's constant reasons (graph_controller.py:62-89, waiting, operator,
  replan, budget, error; unknown texts as received) plus the tracking actions and the `run_state` refusals. (Corrected
  by the review fixes below: the readiness reasons and the providers' refusals were not mapped at first.)
- Operator states: `awaiting_result` → "결과 대기 · <현지 시각>까지는 다시 실행하지 않습니다", no action.
  `needs_operator && uncertain && !awaiting_result` → "운영자 확인 필요", the reason, "실행 잠금 해제" and the CLI
  alternative (`factory … status --job-id` and `release-lock <job> <attempt> --confirm-execution-stopped`, IDs filled,
  with a note that the installed app ships no `factory`). Other `needs_operator` nodes (outputs refused, ledger check)
  say the desktop cannot resolve them and point to `factory status` and the operator. The release dialog explains the
  risk (same GPU, a discarded late result, FAILED and never retried), shows the job, attempt and CLI alternative, and
  enables "잠금 해제" only while "원격 서버/Pod에서 해당 실행이 멈춘 것을 확인했습니다" is ticked and the node label is
  typed exactly (trimmed, NFC); it sends the IDs it showed with `confirm_execution_stopped: true`. Afterwards the
  node says the release was recorded until the controller settles it; refusals are shown in Korean and the run is read
  again.
- Steering: 일시정지 / 다시 진행 (one focus key, so focus moves from one to the other) and 취소 behind a dialog
  ("진행 중인 단계에 취소를 요청합니다. …"; Esc sends nothing). Tracking failures list kind, node, status, tries and
  last error; an action text sits above "기록 다시 시도"; a `tracking_evidence_changed` warning stays shown after the
  re-arm; a re-arm or release follows the run again. Every dynamic text is escaped.

Deviations:
- `desktop/src/api.ts` gained `releaseLock()` with `LockRelease`/`LockReleased` (and three type imports), declared
  there because `workflow-types.ts` stays unchanged (9.8's wrapper was missing). The caller must pass
  `confirm_execution_stopped: true` explicitly. Additive only.
- §4 "10.7" lists the lock release as a non-goal and test 4 as "no button whose name contains 잠금 해제"; the root
  decision replaces that: test 4 now expects exactly one release button (the uncertain node), none for the awaiting
  node and none for the outputs-refused node. Thirteen tests instead of ten: 11 (release dialog gating, Esc and
  돌아가기 send nothing, payload, double activation), 12 (refusals `attempt_stale`, `release_not_applicable` refresh
  the run), 13 (Node: every wait-reason prefix and status table).
- Test 1 runs "three calls over 9 s" as three 3 s steps (the page clock settles the async tick after each step), moves
  the paused clock 1 ms for the visibility poke's zero delay, and checks the slow answer by measuring the hold on the
  page clock (next poll after exactly 2 × it). It also covers three unsettled finals (PAUSED with all steps done,
  graph tracking pending, node tracking retrying). Test 12 activates the second confirm by keyboard: the shell's
  400 ms guard swallows a click at the spot of the last dialog's button.
- D10's "no node pending/retrying/uncertain" is read as: no unfinished node, no node holding an uncertain attempt, no
  node tracking or registration `pending`/`retrying`; node tracking `uncertain`/`failed` waits for a re-arm like graph
  tracking. A finished pre-outbox run (graph tracking `not_started`) is never settled and is polled while shown.
- Beyond the plan: any refusal of a steer, re-arm or release reads the run again (D5 names `run_state`); an answer
  whose `run_id` is not the shown run is never displayed; the open run's detail sits above the list.
- Shell rules of the 10.2 review fixes are followed (no navigation inside a task, focus moves only from `body`); no
  limitation of the shell was hit. `controller-bar.ts` is used unchanged, with the 10.4 review fixes (status before the
  first read, age-only updates in place, the live region outside the patched body); no test depends on a cached status
  on entry, and test 9's no-patch check observes only the runs page's own regions.

Actual verification:
- Against this tree's own Vite on port 1450 through a scratch config under `%TEMP%` (`reuseExistingServer: false`).
- RED first: the 12 page tests failed waiting for `nav [data-action="nav:runs"]`, and the Node test on the missing
  `src/wait-reasons.ts`.
- 29 mutations of the new view: 23 failed a test in the first pass; a 24th (an answer for another run shown) fails
  since test 10 gained a check. The 5 survivors are equivalent: the release without `!awaiting_result` (the awaiting
  branch renders first, so no button exists), `settled` without the uncertain-node clause (an uncertain node is
  always unfinished in `graph_status`), the post-dialog label and checkbox re-checks (the button gate already holds),
  and a list poller left running on leave (its `active()` checks the attachment).
- From `desktop/`: `npx playwright test` **151 passed** on `0a19c37` (138 existing, unchanged, plus 13 new), **164
  passed** on `f6493a1` (151 + 13) and **174 passed** on `ca63dbe` (161 + 13); the new spec passed `--repeat-each=3`
  (39 of 39) on the first and last; `npm run build` passes on each.
- From the root: `uv run pytest -q` **1715 passed, 1 skipped** on `0a19c37`, `f6493a1` and `ca63dbe` (no Python
  changed since the 10.2 review fixes); `uv run ruff check src tests` passes; `scripts/check_secrets.py` reports 0
  findings with the change staged; `git diff --exit-code src/
  desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json desktop/package-lock.json` is clean;
  every changed file is CR-free.

Not verified or still open:
- Real WebView2 (dialogs, focus, IME typing of a Korean label, `visibilitychange`, timers), a real sidecar, controller
  and ledger: Task 11. No network, GPU, SSH, RunPod, MLflow, model download or institution data was used.
- 10.6 navigates here after a submit (`ctx.navigate("runs", runId)`, outside its task); 10.8 adds the `graph-runs`
  tutorial step. Wait reasons are matched by English prefixes: a backend wording change shows the original text.

## Task 10.3 review fixes: kept splits, bounded previews, 2026-10-21

Commit `fix: keep committed splits on re-registration and bound import previews`, written in
`D:\llm-tuning-factory-wt95` (branch `codex/task-10.3-fix`) on `f6493a1`, then rebased onto `2b98a4c` (the 10.4 review
fixes and 10.7; only this file conflicted, every entry is kept, this one last). It lands the independent review of
`f6493a1` ("Ready with fixes": one Important, nothing Critical) and its recommendations. Changed:
`desktop/src/views/data-import.ts`, `desktop/tests/data-import.spec.ts` and this file. No other file changed (no
Rust, Python, package or lock file).

What changed:
- Important: a second 등록 no longer throws the committed split away.
  - Before, 등록 stayed enabled after a registration, and the identical request is an idempotent `ok` on the
    server. Every `ok` reset the split, so 프로젝트에 연결 then bound the unsplit version as 학습.
  - Now 등록 is disabled with "이미 등록했습니다 · 다른 ID나 버전을 입력하면 새 버전으로 등록합니다." while the shown
    preview, ID and version are the registered ones. ID and version update the button while typing.
  - An `ok` resets the split only when it names another version than the one registered.
- 프로젝트에 연결 says "이미 연결된 데이터 버전은 다시 추가하지 않았습니다." and focuses that note when nothing was
  added, also after the project was saved. The note used to render only inside the unsaved-bindings block.
- 파일 찾기 reads the page again after the dialog answers. The dialog has no parent window, so what was typed or
  ticked meanwhile is no longer reverted.
- Previews are bounded on the client.
  - File-level issues are listed before row issues, so a `size_limit` that the server appends last is never cut off
    by the 50-item limit.
  - Sample tables show at most 30 columns ("열 N개 중 앞 30개만 표시합니다.") and 5 rows.
  - The column and group-column choices list at most 500 columns, plus the chosen one, with a note.
- Split issues show under their fields and focus the first one, as the register step does.
  - Server pointers under `/plan` place `practice_reserved` at an output ID.
  - A commit `version_exists` is shown and focused at 분할 버전.
  - Client checks use the same placement.
- The registered version shows its flags (e.g. "합성 데이터 · GPU 전송 미허용"). The split card and the committed
  split say that the split outputs inherit them (`dataset_splits.commit_split`).
- New client checks with Korean reasons, before any call:
  - a KTO value on both sides, or twice on one side;
  - duplicate split output IDs;
  - a split output with the source's ID and version.
- `preview_changed` is said once in the register card: by the button's reason, not again as an issue.
- Paths and column names are isolated for bidirectional text: `<bdi>` in markup, isolate marks in `<option>` text.
- `project-store`'s `canonical()` is not exported, so `stable()` stays.

Actual verification:
- RED first, before any `src` edit: the nine new or extended tests failed for the reasons above.
  - The KTO test sent overlapping labels.
  - 등록 stayed enabled after a commit.
  - There was no `#import-link-note`.
  - The checkboxes ticked during the held picker were unchecked.
  - The column choices numbered 601.
  - There was no `#import-out-train-issues`.
  - There were no flags.
  - Duplicate output IDs were sent.
  - There was no `<bdi>`.
- Test 3 now expects the header issue before the row issue: the order changed on purpose.
- The discovery pin (test 1: "열 확인" after the discovery, and still so after mapping columns) passes on the old code.
  It fails with the reviewer's mutant, which deletes the `"discovery"` branch.
- Mutants, each run against the spec:
  - The guard that resets the split only for another version survived at first. It is unreachable through the UI
    once the same request is blocked (a real server refuses the same version for another preview). The test
    "an answer naming the registered version again keeps its split" then pinned it and fails against that mutant.
  - The other 18 mutants each failed a test. They covered:
    - the registered block and live ID/version;
    - the note placement and the picker capture;
    - issue order, column cap, choice cap and sample cap;
    - split issue placement and focus;
    - flags;
    - duplicate and source-equal split IDs;
    - overlapping and duplicate labels;
    - the single `preview_changed` message and `<bdi>`.
- Playwright from `desktop/`, against wt95's own Vite on port 1431 (scratch config with `reuseExistingServer: false`
  under `%TEMP%`):
  - `npx playwright test` **160 passed** on `f6493a1` (151 + 9 new) and **183 passed** after the rebase onto
    `2b98a4c` (that tree's 174 + 9 new);
  - the spec (22 tests) passed three times in a row;
  - `npm run build` passes.
- From the root (main venv, `PYTHONPATH` = wt95's `src`): `pytest -q` **1715 passed, 1 skipped** on `f6493a1` and again
  after the rebase (no Python change here or upstream);
  Ruff (`src tests`) passes; `check_secrets.py` reports 0 findings with the change staged; `git diff --exit-code`
  of `src/`, `containers/`, `pyproject.toml`, `uv.lock`, `desktop/package.json`, `desktop/package-lock.json` and
  `desktop/src-tauri/` against the base is clean. Cargo was not rerun (no Rust change).
- Every changed file is LF only.

Still open:
- The split source is only a version registered on this page. A version registered elsewhere cannot be split
  anywhere in the UI yet. That includes ShareGPT data, which only the legacy JSON form registers.
- Real WebView2 bidirectional rendering and the real `rfd` dialog: Task 11.

## Task 10.5 graph editor, 2026-10-21

Commit `feat: edit training graphs with cards, preview and keyboard connections`, written in the worktree
`D:\llm-tuning-factory-wt102` (branch `codex/task-10.5`) on `0a19c37`. It implements
[Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.5" with D4–D7 and D13. Changed:
new `desktop/src/graph-model.ts`, `desktop/src/views/graph-editor.{ts,css}`,
`desktop/src/components/{model-roots,benchmark-form}.ts`, `desktop/tests/{graph-model,graph-editor}.spec.ts`,
`tests/test_desktop_ui_contract.py` and this file. No `src/` Python, Rust, package or lock file changed.

What changed:
- `graph-model.ts` (pure, types only): draft read/serialize (`{}` ↔ empty graph; unknown node keys such as `overrides`
  and `depends_on` kept; missing fields omitted, never `""`), generated IDs (`train-1`, `eval-1`, `select-1`,
  `merge-1`, `model-1`), serial and parallel adds, removal with its references, `connect`, the four starting shapes,
  `layout` (columns by longest path, each edge once, dependencies as `order`), `previewSvg` (escaped, one
  `g[data-node]` per card), `waitingOnSharedProfile`, local reasons (D7: cycle, port type, OFT parent and recipe, a
  method change that needs a materialize step, PPO routes, merge sources, count and exact decimal weights, different
  benchmarks under one selection, an evaluator without metrics, train-role data, missing fields), `metricChoices`
  with the text and format notes, and `EVALUATOR_METRICS`, pinned to `evaluation.metric_schema` by
  `tests/test_desktop_ui_contract.py`.
- 학습 그래프 page (`graph`, a child of 프로젝트, reached by the project page's "학습 그래프";
  `data-tour="graph-editor"` once, also in its empty state). Entering reads `project_get` (a dirty draft is kept),
  `recipes_list`, `recipe_get {revision_sha256}` for pinned older revisions only, `model_roots_list`,
  `benchmarks_list` and `executor_profiles_get`. Cards and the SVG preview come from the same draft. The card list is
  one tab stop (roving tabindex; ArrowUp/ArrowDown/Home/End move, Enter selects). "다음 단계 추가" opens a kind choice
  with reasons; also "나란히 추가", "새 학습 단계" and "단계 삭제". The add actions disable at 64 cards with "단계는 최대
  64개입니다"; after an add the focus is in the new card's label.
- The card form per kind follows §4:
  - train: recipe ("고정된 이전 버전 · <8자>" with "현재 버전으로 바꾸기"), a root or parent input with the derived
    initialization, train-role data only, a PPO reward, profiles and session estimates;
  - evaluate: model and benchmark with "새 평가 기준";
  - select: evaluations, metric, direction, optional threshold, partial success, minimum ≥ 2, merge tolerance and the
    help text;
  - merge: models with weights in % (exactly 100) and the real-only note; materialize: its input.
  Unusable sources are disabled in the selects and listed under "연결할 수 없는 단계". Applying a recipe changes only
  `recipe_revision` (and a root input's initialization when the method family changes).
- "저장" saves the draft (CAS), then validates the saved revision. Issues map to cards (`nodeIssues` with the saved
  graph's indexes) and fields (`issuesAt`); the rest go to a summary, with "프로젝트에서 데이터 연결 확인" for
  `/datasets`. A `revision_conflict` keeps the draft with the D5 choices and validates nothing.
- 기반 모델 panel (D13): indexed roots with their snapshot and module-structure state. "모델 검색"/"버전 확인" (legacy
  `model_search`/`model_inspect`, metadata only, inline note) pin a 40-hex revision. "설정·토크나이저 받기" and "모듈 구조
  확인" each run after their own notice dialog; a native refusal is shown verbatim. Train cards on a root without module
  evidence, and cards with `base_targets_missing`/`simulated_execution_needs_targets`, link to "모듈 구조 확인".
- Inline benchmark: a sibling `#benchmark-form` (held-out bindings only, rows from the catalog, `row:0…`, the root's
  tokenizer fingerprint and template, greedy causal-LM, bf16/fp16). `benchmark_unsupported` shows the worker's text
  under "원문 보기"; the new revision is selected on the card.

Deviations:
- Planning inputs are not edited here: §4 gives them to 10.6's plan panel. The profile choice lists native profiles
  and the store's session estimates (`draft.planning.estimates`), which stay empty until 10.6 loads and edits them
  through 9.8's `planning_inputs_get`/`planning_inputs_save`; 10.5 calls neither. Removing a card leaves its time
  limit in stored planning inputs, so 10.6 must drop unknown node IDs before `planning_inputs_save`.
- Exports beyond the §4 interfaces:
  - `graph-model.ts`: `graphToJson`, `serialKinds`, `findNode`, `refsOf`, `labelOf`, `outputRef`, `isRef`,
    `rootInput`, `initializationFor`, `weightsValid`, `percentText`, `weightFromPercent`, `selectionEvaluators`,
    `upstreamRoot`, `sameRoot`, `REASONS`, `NOTES`, `KIND_LABELS`, `METRIC_LABELS`.
  - `model-roots.ts` keeps its state in a module-level `rootsState` (with the pin form's drafts) and also exports
    `loadRoots`, `searchModels`, `pickModel`, `inspectModel` and `captureRootsForm`.
  - `benchmark-form.ts` also exports `newBenchmarkDraft`, `benchmarkProblems`, `benchmarkFormHtml` and
    `readBenchmarkForm`.
- `metricChoices` with no usable metric returns its metrics all disabled with the reason; an evaluator without metrics
  returns one placeholder `{metric: ""}` carrying it, and with `pipeline` among mixed kinds the no-metrics reason
  wins. `equalWeights` uses whole percents ([0.34, 0.33, 0.33]), as in the interface's example.
- Card-form edits reach the draft on `input`/`change` as they happen. A change that leaves the graph as it is never
  reaches the store, so it drops no plan. The view's `capture` reads only the pin and benchmark forms.
- A card is a wrapper (its button plus a status area), because a button cannot hold "원문 보기" details. Those summaries
  and the in-card "모듈 구조 확인" are `tabindex="-1"` so the list stays one tab stop; the card form repeats them,
  focusable.
- `recipe_get {revision_sha256}` goes through `api.workflow`: no new `api.ts` wrapper, because `api.ts` is not a 10.5
  file.
- Tests:
  - `<option disabled>` is asserted with `toHaveJSProperty("disabled", …)`: Playwright 1.64's `toBeDisabled`
    reported a disabled option as enabled.
  - Test 15 confirms dialogs with the keyboard. The shell swallows a pointer click at the same spot within 400 ms of
    the previous dialog's.
  - Legacy `model_search`/`model_inspect` (test 15) and `recipe_get {revision_sha256}` (test 12) are answered by a
    per-test invoke wrapper, because the shared mock (10.1, not edited) has no option for them.
  - Test 8 also pins the entering reads; test 19 also covers the four starting shapes.

Actual verification:
- RED first:
  - `graph-model.spec.ts` failed to load (`Cannot find module …/src/graph-model.ts`);
  - all 12 `graph-editor.spec.ts` tests failed waiting for the project page's "학습 그래프" button (no `graph` view);
  - `test_desktop_ui_contract.py` failed with `FileNotFoundError` for `desktop/src/graph-model.ts`.
- 26 mutations of the new code each failed a test. They covered the cycle, port, materialize and merge-limit
  reasons, mixed evaluators, `{}` serialization, the 64 guard, waiting ancestry, the equal-weight remainder, SVG
  escaping, roving tabindex, arrow keys, focus after an add, the in-card tab stop, the live label patch, validation
  after a save, no validation on a conflict, field issue mapping, a recipe change keeping the binding, the limit
  disabling adds, both notice dialogs, sample IDs, the train-role refusal in the benchmark form, waiting on cards and
  disabled metrics. The targets-dialog mutant survived the first run, so test 15 gained an Escape check.
- Playwright ran against this worktree's own Vite (a scratch config under `%TEMP%`, port 1460,
  `reuseExistingServer: false`; mutation runs used its own server on 1461). `npx playwright test` **157 passed** (138
  existing, unchanged, plus 19 new); `npm run build` passes.
- From the root (main venv, `PYTHONPATH` = the worktree's `src`): `pytest -q` **1716 passed, 1 skipped** (1715 + the parity test; the skip is the real-GPU test); `ruff check src
  tests` passes; `scripts/check_secrets.py` reports 0 findings with the change staged; `git diff --exit-code src/
  desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json desktop/package-lock.json` is clean;
  every changed file is CR-free.
- Rebased onto `ca63dbe` (10.3 and the 10.4 review fixes), `2b98a4c` (10.7) and `6c8780f` (the 10.3 review fixes);
  only this file conflicted each time, every entry kept, this one last:
  - on `ca63dbe`: `npx playwright test` **180 passed** (161 + the 19 new), `pytest -q` **1716 passed, 1 skipped**;
  - on `2b98a4c`: **193 passed** (174 + 19), `pytest -q` **1716 passed, 1 skipped**;
  - on `6c8780f`: **202 passed** (183 + 19), `npm run build` passes, `pytest -q` **1716 passed, 1 skipped**, Ruff
    passes, `check_secrets.py` 0 findings, and the protected-path diff against `6c8780f` is clean.

Not verified or still open:
- Real WebView2 (focus, dialogs, `<select>` keyboard changes, wide previews), a real sidecar, HF metadata, snapshots
  and header reads: Task 11. No network, GPU, SSH, model download, MLflow, provider call or institution data was used.
- Planning, approval and submission are 10.6: it mounts its panel on this page and passes plan resources to
  `waitingOnSharedProfile`. Recipe overrides and `depends_on` are kept but not editable here.

## Task 10.7 review fixes: readiness wait reasons, released and pre-outbox runs, 2026-10-21

Commit `fix: localize readiness wait reasons and settle released and pre-outbox runs`, written in the main tree on
`6c8780f` (the 10.3 review fixes) and rebased onto `d731f7f` (10.5) with the Task 11 plan commit; only this file
conflicted and both entries were kept. It answers the independent review of `2b98a4c` ("Ready with fixes": one Important,
no safety defect; the release gate held under every probe). Changed: `desktop/src/wait-reasons.ts`,
`desktop/src/views/graph-runs.{ts,css}`, `desktop/tests/graph-runs.spec.ts` and this file. No Python, Rust, package
or lock file changed.

What changed:
- I1. Every string that can become a node wait reason, found by reading `graph_controller.py`, `graph_store.py`,
  `controller_entry.py`, `executor_profiles.py` (and `desktop_runtime.py`/`runtime_profiles.py`, whose refusals
  `_provider_reason` passes through), is mapped:
  - The readiness reasons, after the specific "waiting for …" rules: "waiting for <parent>" → "앞 단계 ‘<label>’이(가)
    끝나기를 기다리는 중입니다" and "waiting for tracking of <parent>" (with `_prepare`'s "(tracking failed|uncertain:
    …)" suffix → the 기록 다시 시도 guidance, or the changed-evidence text), both kind `waiting` and named by the parent's
    label (`localizeWaitReason(reason, labelOf)`).
  - The providers' refusals: Pod/SSH server differs and destination mismatch as `replan`; profile missing from the
    controller, no provider for real runs, no live RunPod price, unreadable Pod facts, Pod not running, no SSH
    mapping, no network volume, no verified fingerprint, the model-operation runtime, a runtime mismatch, simulated
    destination refusals and the controller's destination timeout as `error`, in Korean.
  - Composite reasons: the 8.1 backfill's unpriced notice in front of a later reason, and the budget text with the
    unpriced suffix, show both parts; a local selection's `_describe(exc)` gets a Korean lead. Korean texts
    (`desktop_runtime.MISSING_KEY`) stay as received.
- M1. An attempt already released (`needs_operator`, not `uncertain`, an operator-confirmation or overdue reason) shows
  "잠금이 이미 해제되었습니다 · 컨트롤러가 다음 확인 때 …", plus "컨트롤러를 시작하세요" when the run's controller is
  not running, whoever released it. The session-only memory of releases is gone; outputs-refused and ledger-integrity
  reasons keep "데스크톱에서 해결할 수 없습니다".
- M2. The reason's focus and open keys are scoped (`reason:table:<id>`, `reason:operator:<id>`).
- M3. A final run with graph tracking `not_started` and no tracking event at all is settled (it ended before the Task 8
  outbox; nothing will change) and says so; one with any event is still followed.
- M4. While the release dialog is open, a poll that shows another job or attempt, or a node no longer releasable,
  disables "잠금 해제" for good and says why; a new dialog names the current attempt.
- M5. A blank label is shown and typed as the node ID everywhere (table, operator list, dialog, selection and tracking
  texts). M6. The release texts say a canceled run's attempt settles as canceled; `tracking_failures.last_error` is
  localized with "원문 보기"; re-arm warnings are dropped when another run is opened.

Actual verification:
- Against this tree's own Vite on port 1450 through a scratch config under `%TEMP%` (`reuseExistingServer: false`).
- RED first (7 of 15): test 2 found no `[data-reason="waiting"]` for "waiting for train-1"; test 4's released node said
  "데스크톱에서 해결할 수 없습니다"; test 7's last error stayed English; test 11's dialog lacked "취소로 기록"; test 13
  got kind `error` for "waiting for train-1"; the new test 14 found no pre-outbox note; the new test 15 showed a blank
  label as is.
- 18 mutations of the fixes (each readiness and tracking rule and its guidance, the unpriced composite and suffix, the
  Pod replan kind, the exception lead, the labels passed to the reasons, the released branch and both directions of
  its controller hint, the scoped keys, both pre-outbox conditions, the dialog's poll re-check and its stale gate, the
  blank-label name and the localized last error): every one failed a test.
- From `desktop/`: `npx playwright test` **185 passed** on `6c8780f` (183 + 2 new; tests 2, 4, 7, 11 and 13 were
  extended) and **204 passed** after the rebase (10.5's 202 + 2); the spec passed `--repeat-each=3` (45 of 45) on both;
  `npm run build` passes on both.
- From the root: `uv run pytest -q` **1715 passed, 1 skipped** on `6c8780f` and **1716 passed, 1 skipped** after the
  rebase (10.5's UI contract test); `uv run ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; `git diff
  --exit-code src/ desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json desktop/package-lock.json`
  is clean; every changed file is CR-free.

Not verified or still open:
- Wait reasons are still matched by English prefixes; a provider text outside the files read above (a deeper SSH or
  RunPod client error) shows as received with kind `error`. Real WebView2 and a real controller: Task 11.

## Task 10.6 plan and approval, 2026-10-21

Commit `feat: plan graphs and approve execution and data transfer separately`, written in the worktree
`D:\llm-tuning-factory-wt102` (branch `codex/task-10.6`) on `d731f7f`. It implements
[Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md) §4 "10.6" with D5, D8, D9 and the root
decision that replaces D20. Changed: new `desktop/src/components/{plan-panel.ts,plan-panel.css,plan-dialog.ts}` and
`desktop/tests/graph-plan.spec.ts`; `desktop/src/views/graph-editor.ts` (mounts the panel); `desktop/src/api.ts`
(typed `planning_inputs_get`/`planning_inputs_save` wrappers only); this file. No Python, Rust, package or lock file.

What changed:
- The "계획과 승인" panel sits below the graph editor.
  - Inputs: the plan kind (모의 실행 계획 by default, or 실제 실행 계획), each non-select card's maximum run time
    (60 minutes by default, 1–10080), and the estimates for dry runs. Estimates are entered in a sibling form with
    local checks that mirror the backend's patterns; there is no default price.
  - Inputs are read with `planning_inputs_get` when the page is entered, before `project_get`, so 10.5's reads after it
    are unchanged.
  - Stored inputs never replace this session's unsaved changes.
  - "계획 확인" saves changed inputs with `planning_inputs_save`, for the saved revision and only for the saved graph's
    non-select cards (a removed card's limit is never sent). It then sends `graph_plan`: every non-select node gets its
    timeout, runtime "ppo" for PPO recipes and "primary" otherwise. A dry run sends all estimates; a real plan sends only
    the estimates a card uses.
- Blockers: an unsaved or dirty draft ("먼저 저장하세요"), a saved graph without steps, a time limit out of range, and a
  real plan without an MLflow address.
- Issues go to their sections: profiles, estimate rows (by the request's index), the MLflow line, time-limit rows,
  data, and the editor's cards and fields (`/graph/...`). Warnings never block. `observation_revision_differs` is shown
  at its profile with the fixed text; `simulated_execution_needs_targets` is shown on its card with "모듈 구조 확인".
- A plan is held only when its project, revision and kind equal the request. Its basis is the saved graph's recipe
  revisions plus the generations from before the request. Any edit, input change (also a typed invalid value), profile
  save, recipe revision or settings save replaces the plan card with "변경되어 다시 계획해야 합니다: …".
- The approval dialog (D8) shows:
  - the plan hash, the steps and their recipes;
  - the maximum cost with the note that it is not a billing cap, the duration and concurrent GPUs;
  - each destination (name, ID, GPU ×n, hourly total, cost source, steps), each dataset (ID, version, 8-character
    SHA256, 합성/사용자, 전송 허용/불가) and the MLflow destination;
  - "대기 후 순차 실행" for later steps on a shared single resource.

  A dry run registers without approvals. A real plan needs the two checkboxes, "실행 승인" and "데이터 전송 승인".
- Submitting:
  - `graph_submit` always carries the held plan's request ID (reused after a bridge error or a refusal);
  - `plan_stale` drops the plan, names 프로젝트, each profile and MLflow 설정, keeps the draft and offers "다시 계획";
  - other refusals are shown under the plan, which is kept;
  - on success the runs page opens after the task, with a notice to press "컨트롤러 시작". The controller is never
    started.

Deviations:
- `planBlockers` blocks only on what the session knows for certain. A missing or old session inspection is a
  non-blocking hint ("실행 프로필 자원을 1시간 이내에 확인하세요", with "실행 프로필 열기"), because a check stored by an
  earlier session may still be fresh; the backend decides.
- A real plan sends the estimates its cards use, not none, so `estimate_real` lands on that estimate instead of an
  unknown profile on the card.
- Signatures:
  - `planPanelHtml`'s view also takes `recipes` and `usable`;
  - `runPlan`/`approveAndSubmit` take a third `env` (recipes, profiles, the page queue);
  - extra exports: `planWaiting`, `planFeedback`, `planPanelBody`, `bindPlanPanel`, `planAction`,
    `readPlanningInputs`, `applyPlanningInputs`, `runtimeOf`.
- File list: `components/plan-panel.css` is new; `api.ts` gained only the planning-inputs wrappers (`graph_plan_get`
  already existed and is unused). Plan issues reach cards through `planFeedback`, merged by the editor; `draft.issues`
  and `draft.warnings` are left to validation.
- Inputs are saved by "계획 확인" only when they differ from what is stored. Only explicit limits are stored; defaults
  are applied at request time.
- Tests: retries after a closed dialog confirm with the keyboard (the shell swallows a pointer click at the same spot
  within 400 ms). Planning inputs and plans come from a per-test invoke wrapper; the shared mock is not edited.

Actual verification:
- RED first: all 12 initial `graph-plan.spec.ts` tests failed on the missing panel (radio, "계획 확인", "추정 자원
  추가") or on the missing `planning_inputs_get` call. Two tests added later were shown to fail first:
  - the answer-mismatch test, against a mutant without the check;
  - the in-place panel patch test, which failed because the typed limit was not registered (the real bug: a card-label
    patch had dropped the form listeners).
- 15 mutations were run; 14 were killed: the real confirm without both checks, an approval sent for a dry run, a new
  request ID per submit, every estimate in a real plan, removed cards saved, no PPO runtime, both parallel steps
  waiting, an invalid limit keeping the plan, stored inputs over session edits, `plan_stale` keeping the plan, inputs
  never saved, no MLflow blocker, recipe revisions missing from the basis, and plan issues missing from cards. The
  survivor is the drop when profiles or settings change while `graph_plan` is in flight. The UI cannot reach that
  (every save is busy-disabled), so it stays untested defence.
- Playwright ran against this worktree's own Vite (a scratch config under `%TEMP%`, port 1480,
  `reuseExistingServer: false`). `npx playwright test` **215 passed**: 202 existing, unchanged, plus 13 new.
  - One earlier full run had a single failure of the "refused submit" test. It did not recur in four later full runs,
    in 75 repeats of the submit tests or in 72 repeats of the spec.
- `npm run build` passes.
- From the root (main venv, `PYTHONPATH` = the worktree's `src`): `pytest -q` **1716 passed, 1 skipped**; `ruff check
  src tests` passes; `scripts/check_secrets.py` reports 0 findings with the change staged; the protected-path diff is
  clean; every changed file is CR-free.

Not verified or still open:
- Real WebView2 (dialog sizing, radio and number-input keyboard behaviour), a real sidecar with real 9.2/9.3/9.8
  answers, a real controller after a submit: Task 11. No network, GPU, provider, MLflow or institution data was used.

## Task 10.5 review fixes: canonical graph comparison, clean reused card IDs, 2026-10-21

Commit `fix: compare graph drafts canonically and keep reused card ids clean`, after 10.6 in the same worktree. It
follows the independent 10.5 review ("Ready with fixes": I1 required; M1, M2, M4, M8 recommended, plus nits).
Changed: `desktop/src/graph-model.ts`, `desktop/src/views/graph-editor.{ts,css}`, `desktop/src/components/model-roots.ts`,
`desktop/tests/{graph-model,graph-editor,graph-plan}.spec.ts` (new tests appended; existing test bodies unchanged, only
`graph-model.spec.ts`'s import list and the 10.6 plan fake's `sortKeys` option grew) and this file.

- I1: the graph editor's no-op guard compares canonically (`sameGraph`, sorted keys as `project-store` does), the
  threshold is updated in place, and `graphToJson`'s defaults check is canonical. A graph the backend returned with
  sorted keys therefore saves as `{}` when emptied, and an edit to the same values neither dirties the draft nor
  drops a held plan.
- M1: `nextId` skips reserved IDs. The editor reserves the saved revision's card IDs and the IDs with planning inputs,
  so a new card never inherits a removed card's check results or time limit. Starting shapes connect to the IDs they
  actually get.
- M2: `graphEdgeCount` counts connections as `graph_schema.graph_edges` does. Above 256 a `local_edge_limit` issue
  appears; at 256, unchosen evaluation and merge choices are disabled with "그래프 연결은 최대 256개입니다", and the
  toolbar says so.
- M4: removing the last card focuses the first starting shape. After a snapshot or module check the focus goes to the
  root's next action, else to the root (`tabindex="-1"`), only from `body` and on the same page.
- M7 and M8: `percentText` uses the 40-digit scale, so tiny weights round-trip. Tests cover a near-miss that a 1e-9
  float tolerance would accept, and `removeNode` filtering `depends_on`.
- Nits:
  - an emptied `depends_on` is omitted;
  - the "not available" state is read again on each entry;
  - the threshold input waits for a selection metric ("선택 지표를 고른 뒤 입력할 수 있습니다");
  - M5: the PPO continuation reason no longer suggests an unfollowable step.
- M6: no change. 10.6 derives "primary" for every non-train step and "ppo" only for PPO train steps, as the local
  PPO route reason assumes. M3 (reward-binding route reasons) is left open.

Actual verification:
- RED first:
  - `graph-model.spec.ts` failed to load (`does not provide an export named 'graphEdgeCount'`);
  - the 5 page tests failed on their assertions: the removed ID reused (`train-2` present), the evaluation that would
    be the 257th connection enabled, focus not on the first starting shape, the threshold enabled without a metric,
    and the plan dropped by the "0.50" threshold.
- `npx playwright test` **224 passed** (202 + 13 from 10.6 + 9 new); `npm run build` passes;
  `tests/test_desktop_ui_contract.py` passes.
- Both commits were rebased onto `d4d81ae` (the 10.7 review fixes and the Task 11 plan). Only this file conflicted, and
  every entry was kept. Receipts on the rebased tree:
  - `npx playwright test` **226 passed** in 13 files: 204 at the new base, 13 from 10.6 and 9 from these fixes;
  - `npm run build` passes;
  - `pytest -q` **1716 passed, 1 skipped**, and Ruff passes;
  - `check_secrets.py` reports 0 findings, and the protected-path diff against `d4d81ae` is clean.

## Task 10.8 deferred nits: latest page entry, one-line texts, full renders during an IME, 2026-10-21

Commit `fix: run only the latest page entry and keep imported text stable`, written in the main tree on `d4d81ae` (the
10.7 review fixes). It lands the deferred UI nits assigned to 10.8 (the [Task 11 plan](superpowers/plans/2026-10-21-task11-release-0-4-0.md)
§2 "Deferred nit details", items 2, 3, 4, 9 and 10) and root's null guard from the 10.7 re-review. Changed:
`desktop/src/{main,view-types,wait-reasons}.ts`, `desktop/src/views/{projects,recipes,data-import}.ts`,
`desktop/tests/{navigation,projects,recipes,data-import,graph-runs,workflow-core}.spec.ts`, two characters of the Task
11 plan and this file. No Python, Rust, package or lock file changed.

What changed:
- Navigation sequence number (item 2). Every `nav:` takes the next number. A visit's `enter` that waited for running
  tasks runs only while it is still the latest navigation and its page and argument are still shown. Projects →
  기록 비교 → projects → 레시피 → projects while a save is held now reads `projects_list` once (it was three times).
- One-line texts (item 3). An `<input>` drops CR and LF from its value. The project page now treats a name or task type
  that differs from the stored one only by line breaks as the stored text. So there is no phantom "저장 안 된 변경",
  the clean draft is re-read on reopening, and a save without an edit sends the stored text. The purpose (a textarea)
  keeps the 10.2 rule. Beyond the nit: the recipe name had the same defect and gets the same rule.
- Full renders during an IME composition (item 4).
  - A full render that carries no task result waits while an IME composes in a control of the page that still has
    the focus (`renderSoft`). That covers a view's `ctx.render`/`notify`/`fail`, the legacy 실행 관리 poll and the
    기록 비교 search filter. When the composition ends, the shell reads the drafts, renders and puts the focus and
    selection back on the same control. Patches that waited are covered by that render.
  - A composition outside `#app` (a dialog, the tour popover), a composing control that lost the focus, and one that a
    render already replaced hold nothing back.
  - Documented limit, tested: a task's own final render is never deferred. Its result may change the forms of its own
    page, and a capture during the composition would read the stale page back over it (the training form reads every
    `[data-config]` field on each `input`). It replaces the composing input as before and supersedes a waiting render.
    The workflow views lock their forms while busy, so this affects only the unlocked legacy forms of the task's page.
  - The 기록 비교 search box was replaced on every composing keystroke, which split Hangul syllables. It now filters
    when the syllable is committed.
- `isolatedText` (item 9) writes its two isolate marks as escapes: the same characters, pinned by the option text in
  `data-import.spec.ts`. The Task 11 plan's item 9 held the same literal characters where it meant the escapes; fixed.
  A Node test fails on any bidi control (U+202A–U+202E, U+2066–U+2069) under `desktop/src` and `desktop/tests`.
  - Cause found while fixing it: the agent edit tool on this machine turns a typed backslash-u escape into the literal
    character. Write such escapes through a script that builds the backslash.
- 10.7 re-review: `localizeWaitReason` threw a `TypeError` for the unpriced notice with nothing after its "; ", which
  would have broken every poll of that run's detail. It now shows the notice alone; a Node case pins it.
- Notes (item 10, 10.3 re-review):
  - The import mapping's 500-choice cap takes the first 500 source columns in alphabetical (code point) order, not
    file order: `dataset_import` returns `source_fields` sorted. "앞 500개" in the cap note means that order.
  - A flags-only change (합성 데이터, GPU 전송) to an ID and version already registered on the page is blocked as
    "이미 등록했습니다": the register key is the preview, ID and version. The server would refuse it anyway
    (`version_exists`), so a different flag needs a new version.

Actual verification:
- Against this tree's own Vite on port 1490 through a scratch config under `%TEMP%` (`reuseExistingServer: false`).
- RED first, before any `src` edit:
  - the latest-visit test read `projects_list` three times;
  - the poll-render and history-filter tests found the composing input replaced;
  - the project test re-read nothing on reopening (a dirty draft was kept);
  - the recipe test likewise;
  - the bidi scan found `views/data-import.ts:157` (and the new test line, until it was rewritten with escapes);
  - the wait-reason case threw "Cannot read properties of null (reading 'text')".
- The option-text pin and the limit test pass on the old code by design: they pin behaviour that must not change.
- 14 distinct mutations of the fixes: 11 fail a test against the final specs (the sequence check, both line-break
  rules, the null guard, the deferral itself, its focus and page conditions, the reset by a task's render, the draft
  read and focus return after the composition, the history guard).
  - Two of those survived at first and gained test steps: the focus condition and the reset by a task's render.
  - The three survivors were redundant code, now removed: an `isConnected` check (the focus check implies it), a
    history search read in `captureDrafts`, and a draft read at deferral time (the waiting render reads the drafts when
    it runs).
  - Not covered by a test: the legacy jobs poll's switch to `renderSoft` (it shares the tested helper).
- From `desktop/`: `npx playwright test` **211 passed** (the 204 existing plus 7 new; `graph-runs.spec.ts` test 13 and
  `data-import.spec.ts` "names keep their direction" gained assertions); `npm run build` passes.
- From the root: `uv run pytest -q` **1716 passed, 1 skipped** (no Python change); `uv run ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; `git diff --exit-code src/ desktop/src-tauri/
  containers/ pyproject.toml uv.lock desktop/package.json desktop/package-lock.json` is clean; every changed file is
  CR-free.

- Rebased onto `248aa37` (10.6 and the 10.5 review fixes; only this file conflicted, every entry kept): at this commit
  `npx playwright test` **233 passed** (10.6's 226 + 7) in a scratch worktree on port 1491.

Not verified or still open:
- A real Windows IME in WebView2: the tests dispatch `compositionstart`/`input`/`compositionend` themselves. Task 11.

## Task 10.8 tutorial and full regression / Task 10 complete (software), 2026-10-21

Commit `feat: build general-purpose project and training graph workbench` (the parent plan's Task 10 message), written in
the main tree on `f6bf66e` (the deferred nits above), then rebased with it onto `248aa37` (10.6 and the 10.5 review
fixes; only this file conflicted, every entry kept). It implements [Task 10 plan](superpowers/plans/2026-10-21-task10-workflow-workbench-ui.md)
§4 "10.8" with D16. Changed: `desktop/src/tutorial-steps.ts`, `desktop/src/main.ts` (the first-run card text in
`tutorialCard()` only), `desktop/tests/tutorial.spec.ts`, new `desktop/tests/workflow-regression.spec.ts` and this file.
No view, Python, Rust, package or lock file changed.

What changed:
- Tutorial (D16). Five explain-only steps sit between `jobs-history` and `finish`, with §4's IDs, anchors, titles and
  bodies: `project-start` (작업 공간), `executor-profiles` (실행 프로필), `data-import` (데이터 가져오기),
  `graph-editor` (학습 그래프) and `graph-runs` (그래프 실행). The tour has 16 steps.
  - None has `enter`, `done` or a call-to-action, so the tour adds no call of its own. Entering those pages runs only
    their read-only reads: `graph_runs_list`, `observations_list`, `executor_profiles_get` and `controller_status`. The
    graph page without a project reads nothing.
  - Each anchor already rendered exactly once, also in the empty states (10.2–10.7); no view changed.
  - The practice stays on the legacy data and training forms, and `welcome` → `connection-provider` stays adjacent.
  - Texts: `welcome` names 프로젝트 → 데이터 → 레시피 → 그래프 실행 and 단일 학습 and says 약 7분. `finish` lists both
    paths (프로젝트·그래프, 단일 학습) and keeps "연결 설정으로 이동". The first-run card says 약 7분 and names the
    project screens.
- `tutorial.spec.ts`, as §4 lists:
  - The boot mock answers the project screens' reads with empty valid results, `controller_status` with the absent
    controller and `executor_profiles_get` with no profiles.
  - `forbidden` gains a native list and a workflow allowlist (`expectNoSideEffects`). The existing walkthrough calls it
    after its unchanged assertions.
  - New test 1, "the tour explains the project screens without creating anything": the step order, each step's
    page, anchor (count 1), a popover that is not centered and no missing-anchor note; the texts; no mutating call;
    only `tuning-factory.tutorial.v1` in `localStorage` afterwards.
  - The other 24 tests keep their names and assertions; the walkthrough now iterates 16 steps.
- `workflow-regression.spec.ts`:
  - Test 2, "workflow pages escape data, label controls and never nest forms". Hostile payloads sit in project and
    recipe names, column names, a profile name, a node label, a wait reason, a tracking error and issue messages
    (import row, validation).
    - It visits projects, project, recipes, data-import with a preview, profiles (with the edit form), graph (after a
      save) and runs (with a run open).
    - On each page: one `h1`, no `img`, no `form form`, no `dialog`, `.sidebar-foot small` once, an accessible name on
      every visible `input`/`select`/`textarea`, empty `localStorage` and `sessionStorage`, and no page error.
  - Test 3 (the parent Task 9 RED flow at the UI level) boots with no catalog template and no saved setting:
    - a blank project from the overview;
    - 데이터 가져오기 from the project (CSV question/context/answer, preview, register);
    - 프로젝트에 연결, and a project-page save whose `project_save` carries the binding;
    - an SFT LoRA recipe;
    - on the graph page, a pinned base model and its approved snapshot, then the "단일 학습" starter with that recipe,
      root and binding.
    - It is two tests sharing that walk (see deviations):
      - "… reaches a saved and validated graph …" saves (one `graph_validate`).
      - "… reaches a dry-run plan …" goes on: an estimate added in 10.6's plan panel and chosen on the card, save,
        validate, 계획 확인 (dry run by default; `graph_plan` with 3600 s and the estimate), 모의 실행 등록 (one
        dialog, no checkbox; `graph_submit` with both approvals `false`), then the runs page shows the run and
        `controller_start` was never called.
    - Both assert the call order, that no example ID reached any request, no `form form` and no storage.

Deviations:
- `observations_list` is in the tutorial allowlist and mock. 9.8 added it after §4 was written; the profiles page reads
  it on entering, and it is read-only. `controller_status` is a native command, so it never appears as a workflow
  action; the native reads are asserted separately.
- Test 3 is two tests. 10.6 was still in progress when this was written, so the walk up to a validated graph is its own
  test; the plan test was first written against 10.6's commit `018f636` (cherry-picked into a scratch worktree) and
  skipped on this branch until the plan panel existed. 10.6 landed before the push; after the rebase the skip guard
  was removed and the plan test runs.
- The plan test answers `planning_inputs_get`/`_save` and `graph_plan` from a small in-page fake (as 10.6's spec does);
  the shared mock (10.1) is unchanged.

Actual verification:
- Against this tree's own Vite on port 1490 (and port 1491 for the scratch worktree), through scratch configs under
  `%TEMP%` with `reuseExistingServer: false`.
- RED first: the new tutorial test failed on the first-run card ("약 5분"), then, with the card text in place, on the
  welcome text (no 프로젝트). With the five steps removed it fails on the step order. Tests 2 and 3a passed on their
  first run: they are regression nets over 10.1–10.7, as §4 allows, and no view needed a fix. Test 3b passed against
  `018f636` on its first run.
- Mutations: an unescaped project name in the list, an unescaped column name, a binding select stripped of its label,
  a renamed step and a missing `executor-profiles` anchor each fail a test. Removing only the `aria-label` of a select
  inside its `<label>` survives, correctly.
- From `desktop/`, before the rebase (on `f6bf66e`): `npx playwright test` **214 passed, 1 skipped** (211 + 4 new; the
  skip was the plan test); `npm run build` passes; the changed specs passed `--repeat-each=3`. Combined with `018f636`
  in the scratch worktree: **228 passed**.
- After the rebase onto `248aa37`, without the skip guard: `npx playwright test` **237 passed** (10.6's 226 + the 7 of
  the commit above + these 4); `npm run build` passes.
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release` **49 passed** (Rust unchanged).
- From the root, before and again after the rebase: `uv run pytest -q` **1716 passed, 1 skipped** (no Python change);
  `uv run ruff check src tests` passes; `scripts/check_secrets.py` reports 0 findings with the change staged;
  `git diff --exit-code src/ desktop/src-tauri/ containers/ pyproject.toml uv.lock desktop/package.json
  desktop/package-lock.json` against the base is clean; every changed file is CR-free.
- Manual reading pass (§4). The legacy flows of the parent's step 4 are still exercised by the unchanged
  `workspace.spec.ts` and `connections.spec.ts`:
  - HF search and import: "HF model search imports …";
  - S3 list, head and import: "S3 browse paginates …", "S3 folder navigation …", "changed S3 object …";
  - single-job plan, start and queue: "plan is offline …", "busy GPU keeps a submitted job queued …", and the
    server and RunPod plan tests;
  - jobs: "jobs explain … resume", "progress graph …";
  - history compare: "shared history escapes data …", "loaded history search filters …".
  - The tutorial flows are `tutorial.spec.ts`. Neither boot nor a legacy page calls the workflow channel
    (`navigation.spec.ts` test 1 and test 3 here).

**Task 10 is complete in software.** 10.1–10.8 and their review fixes are on this branch: blank projects, data import
and splits, recipes, executor profiles and the controller bar, the graph editor, planning and separate approvals, graph
runs, the tutorial and the legacy flows, each covered by mocked Playwright specs (237 tests). No GPU, network, SSH,
RunPod, MLflow, model download or institution data was used.

Still unverified, for Task 11 (installed app):
- real WebView2: `<dialog>`, focus return, native `<select>` keys, timers and `visibilitychange`, the
  `import.meta.glob` bundle, bidirectional text;
- a real sidecar, controller, broker and Credential Manager;
- an IME in WebView2: the tests dispatch composition events themselves;
- screen readers (Narrator, NVDA): accessible names are checked only as Playwright computes them;
- the 16-step tour in WebView2 (Task 10 §5 item 7) and the rest of Task 10 §5.

## Task 10.6 review fixes: stale projects reload, no second run of a plan, 2026-10-21

Commit `fix: reload the latest project on stale plans and stop duplicate runs after restart`, in the worktree
`D:\llm-tuning-factory-wt102` on `7321c1b`. It follows the independent review of `7ba0341`/`c10d9f4`/`248aa37`
("Ready with fixes"; approval integrity held on every path). Changed: `desktop/src/components/{plan-panel,plan-dialog}.ts`,
`desktop/src/views/graph-editor.ts`, `desktop/src/workbench.ts` (`swallowRepeat`), `desktop/tests/{graph-plan,graph-editor}.spec.ts`
(new tests; the plan fake also answers `recipe_get` by revision and the default runs list is empty),
`desktop/tests/workflow-regression.spec.ts` (see the deviation) and this file.

- I1: a project saved elsewhere is no longer a dead end.
  - A submit's `plan_stale` at `/project`, or an inputs save's (or plan's) `revision_conflict`, marks the saved
    revision stale.
  - While it is, "계획 확인" and "다시 계획" are blocked with "최신 버전을 불러오세요". The banner (or, without one,
    the action row) offers the page's own "최신 버전 불러오기" (confirm, `store.reloadLatest`), which takes the focus.
  - After the reload, "다시 계획" plans the latest revision.
- M1: before the approval opens for a plan without a receipt in this session, `graph_runs_list` is read.
  - A run with the same `plan_sha256` (registered before a restart, or by another window) replaces the approve button
    with "이 계획으로 등록한 실행이 있습니다 · 실행 보기" (focused). Nothing is submitted.
  - A list that cannot be read (bridge error, refusal, `not_available`, malformed) opens no approval and says why.
  - Entries carry `plan_sha256` (`workflow_runs._run_summary`). An unreadable entry (`{run_id, error}`) cannot be
    matched and is passed over.
- M2: `isSummary` checks every string the panel and the dialog read, including each allowed resource's profile,
  resource, GPU and cost source, and each dataset's ID, version and hash. `profileName`/`profileText` coerce with
  `String()`. A malformed answer is refused with "엔진 응답 형식이 올바르지 않습니다." and the page stays.
- The in-flight basis:
  - a plan answered after a profile or settings change, or after its planning inputs changed (M4), is dropped;
  - the notice then says "계획을 만드는 동안 …", not "계획을 만들었습니다" (M5);
  - a refusal after the dialog because another task is running says so.
- `swallowRepeat` ignores events inside an open dialog. The closed one is removed, so such a dialog is a new one, and
  its confirm at the same spot within 400 ms is no longer eaten.
- M3: re-entering compares stored inputs with the session's only on the saved graph's non-select cards, so a limit kept
  for a removed card no longer drops a held plan.
- M6: at 256 connections, "다음 단계 추가" and "나란히 추가" are disabled; "새 학습 단계" (unconnected) stays.
- M7: a train card whose recipe revision this page could not read blocks planning. The blocker reads "레시피 정보를
  확인할 수 없어 실행 환경(primary·ppo)을 정할 수 없습니다: …" and the runtime column shows "확인 불가": a PPO
  recipe is never planned on "primary" by guess.

Deviation: `workflow-regression.spec.ts` (10.8) scripted `graph_runs_list` to hold the run before it was submitted and
pinned `graph_runs_list` after `graph_submit` in the first-call order. M1 must read the list before the approval, so its
mock now answers no runs first and the run second, and the pinned order is `graph_plan`, `graph_runs_list`,
`graph_submit`. Nothing else in that test changed.

Actual verification:
- RED first: the 9 new tests failed on their assertions.
  - "다시 계획" was still offered after `plan_stale` at `/project`.
  - "계획 확인" stayed enabled after the inputs conflict.
  - The approval opened for a plan whose run exists.
  - No malformed-answer error appeared (the page threw `profileId.startsWith is not a function`).
  - The in-flight notice said "계획을 만들었습니다".
  - The new dialog's same-spot confirm was swallowed under a paused clock.
  - A re-entry dropped the held plan.
  - Planning was enabled with an unreadable recipe.
  - The toolbar's add buttons were enabled at 256 connections.
- The regression test failed with the old code once its mock answered no runs first.
- Mutants were killed: the in-flight guard (the 10.6 survivor) and stale marking on an inputs conflict.
- Playwright ran against this worktree's own Vite (scratch config under `%TEMP%`, port 1481,
  `reuseExistingServer: false`). `npx playwright test` **246 passed** (237 + 9 new), including the bidirectional-control
  scan.
- `npm run build` passes.
- `pytest -q` **1716 passed, 1 skipped**; Ruff passes; `check_secrets.py` 0 findings; the protected-path diff is clean;
  every changed file is CR-free.

## Task 10.8 review fixes: drafts and compositions across legacy job renders, stale composing flags, 2026-10-21

Commit `fix: keep drafts and compositions across legacy job renders and stale composing flags`, written in the main tree
on `7321c1b` and moved onto `024ce14` (the 10.6 review fixes) before committing. It answers the independent review of
`46f9f0c` and `7321c1b` ("Ready with fixes": one Important). The review confirmed the 16-step keyboard tour, the plan
fake's requests against the real Pydantic models, a repository without bidi characters and the mocked journey from a
blank project to an operator release. Changed: `desktop/src/{main,tutorial-steps,view-types}.ts`,
`desktop/src/views/{projects,recipes,data-import}.ts`, `desktop/tests/{navigation,projects,recipes,data-import,
tutorial}.spec.ts`, new `tests/test_repository_text.py` and this file. No `src/` Python, Rust, package or lock file
changed; the files 10.6's fix owns (`plan-panel.ts`, `plan-dialog.ts`, `graph-editor.ts`, `workbench.ts`, `api.ts`) were
not touched.

What changed:
- I1 (Important). A legacy step that finishes long after its click now renders through `renderLate`: it reads the
  drafts first, waits for an IME composition like every render without a task result, and gives the focus (with its
  selection) back to the control it was on when the render dropped it.
  - It covers both branches of a job's `execute` answer, which can arrive hours later. Before, typing on the project
    page reverted, the focus fell to `body` and a composition broke.
  - It also covers the other late legacy renders found in `main.ts`: the compatibility answer after a training change
    or on entering 학습 설정 or after an example, an error left by a view's action or `enter`, and the 실행 관리 poll
    (it read the drafts before its call, so typing during the call was lost, and every poll dropped the focus).
  - `execute()` reads the drafts before it switches to 실행 관리, because the job's task may have run while the user
    typed on another page.
  - The tutorial's own renders are unchanged; they follow clicks in the tour.
- M1. A patch waits for a composition only while the composing control itself has the focus and lies in its region.
  A flag left by an input a render replaced (its composition never ends) no longer queues the polls of a focused region
  forever. A full render clears the patches that waited, so a later composition end cannot bring older data back.
  - The flag itself is not reset: every check requires the composing element to hold the focus, which a detached
    element never does, so a reset was unobservable (its mutant survived) and was left out.
- M2. 파일 찾기's answer is written into the path and format controls before the render, because a render that waits
  for a composition reads the page back first. `view-types.ts` states that rule for every view.
- M3. The `graph-runs` step now says the operator state can be released with ‘실행 잠금 해제’ only after confirming on
  the remote server or Pod that the run stopped (root's replacement of D11); the spec checks the sentence.
- M4. A line-break text edited and typed back (project name, task type and purpose; recipe name and description) takes
  the saved revision's text again, so no phantom "저장 안 된 변경" is left.
- M5. `tests/test_repository_text.py` scans every tracked text file for U+202A–U+202E, U+2066–U+2069, U+200E and U+200F.
  The code points are built from integers, and a second case proves the scan reports each one.

Actual verification:
- Against this tree's own Vite on port 1492 through a scratch config under `%TEMP%` (`reuseExistingServer: false`).
- RED first, before any `src` edit:
  - the held execute answer reverted the typed project name ("늦은 완료");
  - the submit's switch to 실행 관리 lost the typed 데이터 ID (`my_dataset`);
  - the queued patch came back after a later composition end (`.stale` present);
  - edit-then-revert re-read nothing (project name, purpose, recipe name);
  - the picked path stayed `C:\data\qa.csv` after the waiting render;
  - the `graph-runs` body lacked the new sentence.
  - The late compatibility and poll test was written after the code, then checked by mutation.
  - M5 passes on the clean repository; staged with a probe file holding U+200F, it failed and named
    `docs/zz-bidi-probe.md:1: U+200F` (the probe was removed).
- 14 distinct mutations of the fixes; 13 fail a test: both `execute` branches, the draft read before the switch, the
  patch going back to the raw flag, the composing-control focus check, the queue clearing in `render`, the path and
  format writes, the project, recipe-name and recipe-description saved-text rules, the compatibility answer and the
  poll. The flag reset survived and was dropped (above).
- From `desktop/`: `npx playwright test` **251 passed** (`024ce14`'s 246 plus 5 new; 5 tests were extended);
  `npm run build` passes.
  - One full run on `7321c1b` had a single boot timeout in `graph-editor.spec.ts` ("boot reached capabilities"). That
    spec then passed `--repeat-each=3` (48 of 48), and the next full run passed 242 of 242.
- From the root: `uv run pytest -q` **1718 passed, 1 skipped** (1716 + the 2 repository-text cases); `uv run ruff check src tests` passes;
  `scripts/check_secrets.py` reports 0 findings with the change staged; the protected-path diff against `024ce14` is
  clean; every changed file is CR-free and the new repository scan passes.

Not verified or still open:
- Real WebView2 timing of late answers, focus return and a real Windows IME: Task 11.

## Task 11.4 documentation and release-note drafts, 2026-10-21

Commit `docs: guide general-purpose training workflows and the 0.4.0 install`, written in the main tree on `d0415c0` and
rebased onto `5ecfd45` (11.2's seed script; no conflict). It follows
[Task 11 plan](superpowers/plans/2026-10-21-task11-release-0-4-0.md) §4 "11.4" with D4, D11, D17 and D20. Changed:
`README.md`, `docs/CONFIGURATION.md`, `docs/WINDOWS_APP.md`, `docs/OPERATIONS.md` (one pointer section), new
`docs/TRAINING_WORKFLOWS.md` and this file. No code or test changed.

What changed:
- `docs/TRAINING_WORKFLOWS.md` (Korean, first-time users): the whole path from a blank project through mapped import
  (encoding, mapping, row issues, split, binding), recipes, the base-model panel (weights never downloaded), the graph
  editor, planning and the two approvals, executor profiles (absolute key paths, GPU list, last check, one-hour rule),
  the background controller (no auto-start, drain can take hours, restart after changes), the runs page ("결과 대기"
  versus "운영자 확인 필요", the release dialog and the CLI alternative), MLflow tracking and re-arm, the policies, the
  CLI equivalents, the D20 limits and the verification scope.
- README and WINDOWS_APP: 0.4.0 and the `v0.4.0` link, the five-step workflow, the D4 update and uninstall procedure
  (stop, wait for 정지, close, install; the uninstaller's ‘애플리케이션 데이터 삭제하기’ warning), 0.3.0 settings carried
  over, no downgrade, unsigned/SmartScreen; the dev-branch banners and "not in 0.3.0" sentences are gone; the tutorial is
  16 steps and about 7 minutes; storage paths and the D20 limits; the 11.3 verifier options and smoke flags.
- CONFIGURATION (English): state layout, projects and recipes, import mappings and split plans, graph JSON, planning
  inputs and plans, benchmarks and selection, native and CLI executor profiles with their credential entries and files,
  the MLflow auth modes, the controller start, `CREDENTIAL_ENV`, the TLS change and `state/controller/` files.
- Drafts (git-ignored, `git check-ignore` confirms): `.factory/release-0.4.0.md` from the 11.7 template and
  `.factory/pr-0.4.0.md`. Their angle-bracket placeholders (counts, receipts, installer and bridge SHA256, bytes, commit
  B, acceptance receipt name, review summary, 11.6 gaps) are for root at 11.7.

Corrections found while checking the docs against the code:
- `factory status` takes `--job-id JOB_ID`; `status JOB_ID` is a Click usage error (checked with a temporary state
  directory). WINDOWS_APP now uses `--job-id`. **`docs/OPERATIONS.md` still shows `status JOB_ID` twice in its RunPod
  lock note**; this sub-task was limited to the pointer section, so root should fix those lines.
- The engine deduplicates `graph_submit` by request ID only (`graph_store.submit`); the desktop adds the plan-hash check.
  The docs say the app never registers a plan twice and that a CLI resubmission must reuse its `--request-id`.
- `forget_credentials` also deletes every executor-profile key, but its confirmation text names only the connection
  credentials; the docs warn about it.
- A dry-run plan that names a native profile needs a stored observation of any age (`observation_missing`); only
  estimates need none.

Doc accuracy (§4 step 5):
- The plan's quoted-label check over README, WINDOWS_APP and TRAINING_WORKFLOWS prints "all quoted labels exist" (150
  labels). Four composite UI texts (‘GPU 서버 · 주소’, the plan destination line, ‘N행’, ‘확인됨 · 인증 없음’) were moved to
  single quotes after checking their templates in `server-connection.ts`, `main.ts` and `data-import.ts`; two section
  references moved too. Hangul bold spans were also checked: every miss is prose emphasis or a heading.
- `factory --help` and every command's `--help` (including `graph-work`, `graph-plan`, `graph-submit`, `status`): every
  `factory` command and flag the docs name exists. Flags of uv, git, cargo, npm, mlflow and WebView2 are external.
  `verify_desktop_package.py --bridge/--check/--receipt` and `FACTORY_SMOKE_LIVE_HF`/`FACTORY_SMOKE_FLOW` come from 11.3
  and do not exist at `d0415c0`; they are documented as 11.3 specifies and need a re-check when 11.3 lands.
- Every relative link and anchor resolves to a tracked file.

Actual verification:
- `uv run pytest tests/test_repository_text.py -q` with the six files staged: **2 passed**.
- `uv run python scripts/check_secrets.py` with the six files staged: `Checked 1028 staged/historical file versions; 0 findings.`
- A byte scan of the five docs and both drafts finds no CR, BOM, control, format or bidirectional character and no
  backslash escape sequence; the scanner was shown to report a probe holding both. `git ls-files --eol` shows `i/lf w/lf`.
- No full pytest run: no test or code changed.

Not verified: the installer-facing statements (uninstaller checkbox text from the Tauri 2.12.2 Korean NSIS template,
SmartScreen wording, the 0.3.0 → 0.4.0 carry-over and the stopped-controller update path) are checked in 11.6, and the
release notes stay a draft until 11.7.

## Task 11.3a release machinery: version 0.4.0, the extended native smoke and the CI filters, 2026-10-21

Three commits in worktree `D:\llm-tuning-factory-wt113` (branch `codex/task-11.3`), written on `d0415c0` and rebased onto
`1d938d6` (11.2's seed script and the 11.4 docs; only this file conflicted, both entries kept), with the §4 messages
of the Task 11 plan: `ci: rebuild the desktop package when bundled examples or configs change`, `test: extend the
installed-app smoke to workflows, profiles and the background controller`, `chore: version the Windows workbench as
0.4.0`. They are ordered so that every commit passes its own tests. No `src/` file, Rust source, dependency or
`scripts/build_desktop.py` changed (F5 still covers every bundled asset).

What changed:
- D1 version. `0.4.0` in `desktop/package.json` and both `desktop/package-lock.json` fields (`npm version 0.4.0
  --no-git-tag-version --offline`), `desktop/src-tauri/tauri.conf.json:4` and `desktop/src-tauri/Cargo.toml:3`;
  `cargo update --offline --workspace` rewrote only the root `Cargo.lock` entry (`alloc-stdlib 0.3.0` untouched).
  `pyproject.toml` stays `0.1.0`. The footer and `workspace.spec.ts` already read `package.json`.
  README and WINDOWS_APP are 11.4's files under §3; 11.4 (`1d938d6`) already says 0.4.0 there.
- `tests/test_release_version.py` (new) pins the six desktop version fields, the native smoke reading `package.json`
  without a literal, `pyproject.toml` at `0.1.0`, and the D16 path filters.
- `desktop/tests/native-smoke.mjs` (11.3c sections 1-9).
  - Footer from `package.json`. The legacy actions are unchanged, except that the live Hugging Face block runs only
    with `FACTORY_SMOKE_LIVE_HF=1`; without it the output has `live_hf: false`, `modern_hf_model_registered: null` and
    `network_used: false`.
  - The legacy vault check, which ends with `forget_credentials`, runs only when no `*_saved` flag is set and
    `executor_profiles_get` returns no profile. Otherwise it reports `"skipped_existing_credentials"`.
  - Workflow channel checks: `projects_list`, `project_create`, the project listed, `recipes_list`. It stops before
    any profile or controller step unless the controller is `absent` and no graph run is active.
  - Profiles, controller and flow run only on a state without executor profiles; otherwise each reports
    `"skipped_existing_profiles"`.
    - Profile: one synthetic RunPod profile `smoke-<suffix>` with a key built at runtime. The key must be masked. A
      relative SSH key path and a pasted key are refused with their Korean texts, and neither draft is saved.
    - Controller: `controller_start`, then wait for `running` with this owner's `current.json`. The profile must be
      in `current.profiles` and not in `skipped_profiles`. A second start gives `already_running`.
    - D21: five `controller_status` round trips, timed in the page, with the median.
    - The stop dialog on 실행 프로필: focus on the dialog heading, Esc closes it and changes nothing, focus returns
      to the opener.
    - `controller_stop`, then wait for `absent`; the profile is deleted and no profile is left.
  - `FACTORY_SMOKE_FLOW=1` adds the A1-style chain on the seeded root: a blank project, a mapped CSV, an 80/10/10
    split, an SFT LoRA recipe with `cutoff_len` 64 (the seed's reviewed limit), a `text` benchmark, then train →
    evaluate. It plans a simulation, reads it back with `graph_plan_get`, submits it, starts the controller, waits
    until the run is COMPLETED, and stops the controller.
  - It visits 프로젝트, 실행 프로필, 그래프 실행 and the overview, with screenshots. It prints one JSON line with
    `gpu_started: false` and `dataset_transferred: false`.
  - A cleanup list stops a controller the smoke started and deletes its synthetic profiles, even after a failure.
- `.github/workflows/windows-desktop.yml`: `paths` adds `scripts/seed_acceptance_root.py`, `examples/**` and
  `configs/ai/**`. No new job.

Actual verification:
- RED first: `tests/test_release_version.py` failed 3 of 4 for the intended reasons (every field `0.3.0`, the
  `내부 검토 0.3.0` literal, the three missing filters); the `pyproject` rule passed as an existing invariant. Then
  4 of 4 passed.
- `git diff desktop/src-tauri/Cargo.lock` shows exactly one line (the root package); the `package-lock.json` diff is
  the two version fields.
- `node --check desktop/tests/native-smoke.mjs` passes.
- Scratch harness under `%TEMP%`, not committed and deleted afterwards. It ran the unmodified smoke over CDP port 9223
  against this tree's Vite frontend in Chromium. The Tauri bridge was forwarded to the worktree's real Python sidecar
  (`scripts/desktop_entry.py`) and a real `--controller` process. Credential Manager, the profile file and the broker
  start line were emulated in memory.
  - Fresh state: exit 0 in 22 s; vault check true; profile, controller and dialog checks true; status median about
    0.5 s (source sidecar, not the frozen one); no controller alive afterwards; vault and profiles empty.
  - `FACTORY_SMOKE_FLOW=1` on a state seeded by 11.2's real `seed_acceptance_root.py` (from `5ecfd45`): exit 0 in
    50 s; the simulated train → evaluate run COMPLETED through the real controller.
  - A state with a saved legacy key and a user profile: exit 0. `forget_credentials` and `controller_start` were
    never called, and the user's two keys and profile were untouched.
  - An injected failure after the controller started: exit 1, but the cleanup stopped the controller and deleted the
    synthetic profile.
  - The harness also caught two bugs before commit:
    - A second identical `graph_plan` returns another `plan_sha256`, because an estimate pins `observed_at = now`.
      With a fixed clock the hashes are equal; with a moved clock they differ. The smoke checks `graph_plan_get`
      instead.
    - The real seed refuses the default `cutoff_len` 1024.
- `uv run pytest -q` (main venv, `PYTHONPATH` = this worktree's `src`): **1722 passed, 1 skipped** (1718 + 4).
  `ruff check src tests` passes.
- `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release`: **49 passed**.
- From `desktop/`: `npm run build` passes; `npx playwright test` against this tree's own Vite on port 1510 (scratch
  config, `reuseExistingServer: false`): **251 passed**. No spec changed: the footer test reads `package.json`.
- `scripts/check_secrets.py`: 0 findings with the change staged. Every changed file is CR-free.

Not verified or still open:
- The smoke against the installed 0.4.0 app (real WebView2, real broker and Credential Manager, the frozen sidecar's
  round-trip median for D21) and its RED against 0.3.0: Task 11.6.
- 11.3b (the frozen verifier) follows in this worktree. Its §4 step "graph_plan twice with the same plan_sha256" cannot
  hold with a real clock (see above); it compares the stored plan instead.

## Task 11.3b frozen verifier: workflow channel, background controller, argument refusal, 2026-10-21

Commit `test: verify the frozen workflow channel and background controller in the package check` (the §4 message), in
worktree `D:\llm-tuning-factory-wt113` on `f78ad93` (11.3a, pushed), after 11.2's seed script `5ecfd45`. Changed:
`scripts/verify_desktop_package.py` and this file. No `src/`, desktop, dependency or `scripts/build_desktop.py` change.

The CLI for 11.6:

```text
uv run python scripts/verify_desktop_package.py [--bridge PATH] [--check NAME]... [--receipt PATH]
  --bridge   default dist/factory-bridge.exe; the installed app's factory-bridge.exe; a .py path (the source entry
             scripts/desktop_entry.py) runs with this interpreter ("bridge_mode": "source")
  --check    legacy | arguments | workflow | controller | wheel   (repeatable; default: all, in this order)
  --receipt  also write the printed JSON receipt to PATH (LF)
11.6 step 4:  uv run python scripts/verify_desktop_package.py --receipt .factory\frozen-040-receipt.json
11.6 step 10: uv run python scripts/verify_desktop_package.py --bridge "$acc\app\factory-bridge.exe" --check arguments --check workflow --check controller
```

What it checks:
- `legacy`: the previous checks, moved into `check_legacy` unchanged, with the same receipt keys. The wheel's
  `source_and_worker_match` now comes from `check_wheel`.
- `arguments`: `--bogus` and `--controller extra` must each exit 2 within 60 s, with empty stdout and stderr
  `unsupported sidecar arguments`. stdin is an open, empty pipe, so an engine that reads it times out.
- `workflow`: one engine process per request, using the app's envelope on a temporary state.
  - A blank project ("질의응답 실험 01", task type `qa`, no `legacy_origin`).
  - A 12-row CP949 CSV; the preview round-trips the Korean cells. Registered `synthetic`, not exportable.
  - An 80/10/10 split, committed and bound to the project. An SFT LoRA recipe with `cutoff_len` 64.
  - `seed()` of `scripts/seed_acceptance_root.py` into the same state, from this process. `model_roots_list` through
    the engine must show the root `snapshot_ready` and `targets_ready`.
  - A `text` benchmark on the validation split, then train → evaluate. `graph_validate` must pass, then a simulation
    `graph_plan` with `estimate-local`.
  - `graph_plan_get` must return the same plan. The stored plan, hash-checked on read, must pin
    `digest(containers/gpu-requirements.lock)` for every resource; in a frozen engine that lock comes from
    `sys._MEIPASS/assets`.
  - No run before submit; then `graph_submit` (simulation) must return `RUNNING`.
- `controller` (runs `workflow` first when that is not selected):
  - It starts `--controller` with a `factory-controller/1` line: fast settings, `tracking: {}` and one RunPod profile
    whose synthetic key is built at runtime. stdin stays open.
  - The ready line must name the owner and a pid. The submitted run must reach COMPLETED within 180 s.
  - `controller_status` must show this owner `running`, with the profile in `current.profiles` and not in
    `skipped_profiles`.
  - `controller_stop` must return `{requested, owner_id}`. The process must exit 0 within 60 s; `current.json` then has
    `code: stopped` and `stopped_at`, the status is `absent`, and the ready pid is gone.
  - A second start, then stdin closed (EOF): exit 0 and the pid gone.
  - A third start, plus a fourth while it runs: the fourth prints `{"ok": false, "code": "owner_held"}` and exits 3.
    Then the third is stopped.
  - The key must appear in no file under the state, in no controller output and in no one-request answer.
  - Pids are checked with `OpenProcess`/`GetExitCodeProcess`, never `os.kill(pid, 0)`, which terminates a process
    on Windows.
- `wheel`: unchanged; without a build it says to run `build_desktop.py`.
- Receipt: `bridge`, `bridge_mode`, `bridge_sha256`, `checks`, the legacy keys, and these:
  - `unsupported_args_exit_2`;
  - `workflow_channel`, `mapped_import_cp949`, `split_committed`, `immutable_plan`, `frozen_lock_path`,
    `workflow_run_submitted`;
  - `controller_ready`, `controller_run_completed`, `controller_profile_loaded`, `controller_stop_drained`,
    `controller_eof_drained`, `owner_held_exit_3`, `onefile_child_exited`, `engine_child_process`,
    `broker_secret_not_written`;
  - `source_and_worker_match` (wheel) and `gpu_started: false`.

Deviations from §4 (for root):
- `--bridge` also accepts the source entry (`.py`), so everything except `wheel` runs before 11.6 builds the sidecar.
  The 11.4 text in WINDOWS_APP lists the three options and not this source mode.
- "graph_plan twice with the same plan_sha256" became `graph_plan_get` plus the stored plan. The plan pins
  the estimate's `observed_at`, so the hash changes with the clock (11.3a above).
- Extra receipt keys: `bridge`, `bridge_mode`, `checks`, `workflow_run_submitted`, `controller_profile_loaded` and
  `engine_child_process`. The last is true when the engine pid differs from the started process: the onefile child
  in a frozen engine, or the venv launcher's child in source mode on Windows.

Actual verification:
- RED (§4) against `D:\llm-tuning-factory\dist\desktop-acceptance-030\factory-bridge.exe` (0.3.0, run read-only):
  - `--check arguments` failed: `['--bogus']: still running after 60 s; the engine waits for stdin`;
  - `--check workflow` failed: `('project_create', {'ok': False, 'error': 'unsupported desktop action'})`.
  - No bridge process was left. The killed bootloader's `%TEMP%\_MEI287882` was removed.
- GREEN in source mode: `--bridge scripts/desktop_entry.py --check legacy --check arguments --check workflow --check
  controller` passed three times in a row on the final file (about 23 s each; the `--receipt` file is one LF line),
  every key true, `gpu_started: false`, and `source_sha256`
  `0849b47e…d56c`. `--check controller` alone also passed. No controller process or `factory-package-*` folder was
  left.
- `--check wheel` without a build fails with the message above. A missing default bridge exits 2 with `no engine at …`.
- `ruff check src tests scripts/verify_desktop_package.py scripts/seed_acceptance_root.py` passes; the file is CR-free;
  `scripts/check_secrets.py` reports 0 findings with the change staged.
- No test references the verifier.

Not verified or still open:
- The frozen engine (PyInstaller onefile: `sys._MEIPASS` lock, EOF through the real bootloader, the child pid) and
  `wheel`: Task 11.6, after root builds the sidecar.

## Task 11.2 cross-task acceptance tests and the synthetic seeded root, 2026-10-21

Three commits from worktree `codex/task-11.2`, written on `d0415c0` and rebased onto `0f84972` (11.3, 11.4):
- `5ecfd45 test: seed a synthetic offline root for acceptance plans`, pushed early for 11.3b;
- `fix: refuse merges of adapters from different base roots at planning`;
- `test: accept blank projects, the mock diamond and the headless lifecycle through the production wiring`.

Changed:
- new `scripts/seed_acceptance_root.py`, `tests/test_seed_acceptance_root.py` and `tests/test_acceptance_workflows.py`;
- `tests/test_graph_readiness.py`: a `synthetic=` keyword on `build`, and its `registered` helper;
- `src/tuning_factory/graph_planner.py` and `tests/test_graph_planner.py`, for the fix root approved;
- this file.

No desktop, Rust, lock or package file changed.

Seed script (D15). `python scripts/seed_acceptance_root.py --state-dir <state>` prints one JSON line:
`{"modules": 14, "root": {...}}`.
- The root is `factory-acceptance/tiny-qwen2` at `8342efb8b4c672c4d36d25d557acbcb8e0b0f6be`, the SHA-1 of
  `factory-acceptance/tiny-qwen2@1`. Its config is `65797530…07ef` and its tokenizer `9f73ebc3…3f1b`.
- It writes, through the public functions:
  - the plan's CONFIG and the WordLevel tokenizer of `test_model_inputs`, as canonical bytes;
  - the root index, with the `ModelProfile` that `ModelHub.inspect` (the legacy `model_inspect` path) builds, answered
    from the local bytes by an in-process `MockTransport`;
  - the evidence for 14 linear modules, from a synthetic `model.safetensors` header through `header_tensors` and
    `target_evidence`.
- It downloads nothing and writes no weight file.
- It's idempotent: a rerun rewrites no byte. Other content in the snapshot or the index exits 1; argparse errors exit 2.
- `seed(state)` and `write_root(state, repo_id, revision, config)` are importable.
- The profile's `max_length` is the config's `max_position_embeddings` (64), so every recipe planned on this root
  needs `hyperparameters.cutoff_len: 64`. The default 1024 gets `plan_refused` "cutoff_len exceeds reviewed model
  limit 64". CONFIG is kept exactly as the plan wrote it.

Product fix (root-approved, a separate commit). A4's "two different roots" case planned `ok`: `graph_plan` accepted a
merge of adapters trained on two HF roots. Only the merge's compile (`check_merge_descriptors`) refused it, so a real
plan would have trained every branch on paid GPUs first.
- `_MergeLineage` resolves each merge input's effective base and adapter targets the way compilation does:
  - a new adapter's base is its policy base;
  - a continuation keeps its parent's base;
  - a merge keeps its inputs' common base;
  - a selection offers each candidate's base;
  - an artifact adapter has its descriptor's base;
  - a full-model or materialized parent is unknown statically, so compile stays authoritative there.
- `_definition_issues` reports `merge_bases_differ` (the `check_merge_descriptors` text) on the merge node, so
  `graph_validate` shows it after a save.
- `plan_graph`'s static routes also refuse a difference that only the artifact snapshots or the bases' module evidence
  reveal. Example: `lora_target: all` that expands to more than `q_proj,v_proj`.
- The UI does not refuse such a connection locally. `graph-model.ts` `connectionReason` refuses only materialize and
  non-plain-adapter sources for a merge, and `upstreamRoot`/`sameRoot` serve the module-structure hint and the root
  pickers. The validate-after-save issue now names the card.

Acceptance cases (D13, D14). They run through `desktop_workflows.dispatch_action`/`handle`,
`controller_entry.build_controller`, `serve` on a thread, the `SimulatedOperationDispatcher` and the local tracking
sink. A module-wide socket guard is on, and a real MLflow client is never constructed.
- A1, UTF-8 and CP949: a blank project and a new-purpose CSV.
  - CP949 read as UTF-8 is `invalid_source`, and the Korean cells round-trip.
  - The flow is synthetic data that is never exported, split 80/10/10, an SFT LoRA, the seeded root, a text benchmark,
    validate, then a simulation plan.
  - The same request gives the same `plan_sha256` and the same bytes; `graph_plan_get` returns it, and a later
    project edit never rewrites it.
  - No run and no `jobs.db`; no `legacy_origin`; no catalog template named; no bundled example opened.
- A2, two destinations and one: the mock diamond completes.
  - `select` is `no_winner` and each GPU node has one attempt.
  - The claims are two `simulation:` keys, or one key that the second branch waited for.
  - `graph_run_get` never showed a node on another destination than its pinned one.
- A3, restart, cancel and cancel-at-join, through `serve`.
  - Spies that call through time the stop and the cancels.
  - Restart: the first serve drains the claimed branch, and a new owner finishes. One attempt and one
    `<launch_id>.result.json` per node.
  - Cancel at the first branch's launch gate: that attempt never launches, and nothing launches after the cancel.
  - Cancel while the selection is claimed: the select is CANCELED with no receipt.
  - `controller_stop`, then `serve` returns 0.
- A4: the codes observed today.
  - Five models give `invalid_value` at `/graph/nodes/5/models`.
  - Weights [0.5, 0.6] and a duplicated model give `invalid_value` under `/graph/nodes/<i>`. The path ends in the union
    tag `/merge` (passed to 11.1); the tests check only the prefix.
  - A full-method parent gets `plan_refused` from `graph_plan` only; validate passes it.
  - An evaluation output gives `typed_port`; two roots give `merge_bases_differ`.
  - No run is created.
  - A simulated merge plans, then the run is FAILED: the merge node fails with "merge requires real completed
    artifacts", its children are BLOCKED, and only the two simulated adapters are frozen (F14, pinned).
- A5:
  - (a) The diamond's candidates are all excluded with reason `simulation`, and only local receipts exist under
    `state/tracking/<run_id>/`.
  - (b) A real run over synthetic data (`build(synthetic=True)`) uses the real `OperationAttemptRunner`, the fake GPU
    host and the fake MLflow client. Selection is `no_winner` (`synthetic`). There's no `model_registration` event and
    no `create_model_version`, and `registration_status` is `none`.
- A6: the headless lifecycle through the channel.
  - `controller_status` shows `running` with this owner's `current.json`.
  - A second `serve` returns 3 and writes nothing.
  - A simulated run completes, `controller_stop` gives `{requested: true}` and `serve` returns 0.
  - Afterwards status is `absent`, and `current.json` has `stopped_at` and `code: stopped`.

Actual verification:
- RED first:
  - the seed tests failed with "scripts/seed_acceptance_root.py (Task 11.2) is missing";
  - 5 of the 10 new planner cases failed: no `merge_bases_differ` for the four two-root lineages, and DID NOT RAISE
    for different targets. The same-root and run-time-base cases passed before the fix.
- The 11 acceptance cases passed at once. Eight mutations, loaded by a scratch pytest plugin under `%TEMP%` (no `src`
  edit), each fail at least one:
  - `select_best` keeps simulation candidates: A5;
  - registration enqueued for synthetic artifacts: A5;
  - the claimed join completes despite a cancel: A3 cancel-at-join;
  - `begin_launch` without the cancel gate: A3 cancel;
  - the merge weight check removed: A4;
  - `graph_plan` not content-addressed: A1 and 10 more;
  - the seed writing a fingerprint other than its files: seed test 1 and 9 more;
  - the new merge-base check removed: A4.
- The new tests ran three times: 14, 14 and 14 passed (32-35 s each).
- `test_graph_readiness`, `test_graph_controller`, `test_controller_entry`, `test_workflow_runs`, `test_workflow_planning`,
  `test_simulated_dispatch`, `test_hf_targets` and `test_graph_planner`: 436 passed.
- Full suite:
  - after the seed: 1720 passed, 1 failed, 1 skipped. The failure is the flake below.
  - after the fix: 1730 passed, 1 skipped.
  - final, with all three commits: **1741 passed, 1 skipped** (1718 + 3 seed + 9 planner + 11 acceptance).
- `ruff check src tests scripts/seed_acceptance_root.py` passes, and `scripts/check_secrets.py` reports 0 findings.
- CPU acceptance (`scripts/` and `src/` changed): 29/29 twice. The last receipt is source `82fc8570…7352`, receipt
  SHA256 `f49e4a72…a0c5`, with no GPU, no download and no institution data.

Not verified or still open:
- Flake (11.1 owns the test): `test_controller_entry::test_a_drain_fenced_by_a_successor_is_not_a_clean_stop` timed
  out once under full-suite load ("the first tick"). It passed 6 of 6 alone.
  - Likely cause: `acquire_owner` writes `acquired == heartbeat == now`. This PC's `time.time()` resolves to 15.625 ms
    (`GetSystemTimeAsFileTime`), so a first renew in the same clock tick never makes `heartbeat > acquired` before the
    60 s tick.
- The frozen sidecar (11.3) and the installed app (11.6) layers of D14 are not covered here.

## Task 11.1 deferred nits and flake hardening, 2026-10-21

Commit `fix: close deferred release nits and harden flaky tests`, in the worktree `D:\llm-tuning-factory-wt102` on
`d0415c0`. It implements the [Task 11 plan](superpowers/plans/2026-10-21-task11-release-0-4-0.md) §4 "11.1" with root's
item list and the 11.2 finding root added during the work. Changed: `src/tuning_factory/{cli,desktop_workflows,
controller_entry,model_artifacts}.py`, `desktop/src/{project-store,tutorial,main}.ts`,
`desktop/src/components/plan-panel.ts`, `desktop/{vite,playwright}.config.ts`, their tests
(`tests/test_{cli_graph,desktop_projects,controller_entry,model_artifacts,repository_text,workflow_runs}.py`,
`desktop/tests/{workflow-core,graph-plan,tutorial,connections}.spec.ts`), the stale pointer pins in
`tests/test_{workbench_backend,hf_targets,workflow_planning}.py` and this file.

Each item, reproduced first:
- `graph-work` Ctrl+C, N1 (re-entrancy): **fixed**. `_CtrlC` takes the press number from one atomic `next()` first,
  exits on the second press and only then, on the first, sets the stop event. `presses` is a read-only property.
  - RED: a new test runs the handler with a second press injected before every bytecode of the first (its own methods,
    `Event` and `Condition`, through `sys.monitoring` instruction events; `sys.settrace` delivers no opcode events on
    3.12). The stop event's lock reports a self-deadlock instead of hanging. On the old code, of 74 injection points,
    46 deadlocked inside `Event.set` and 4 lost a press (no exit). After the fix, all 78 points exit exactly once
    with 130.
- N2: **fixed**. The `DRAINING` write is wrapped in `contextlib.suppress(OSError)`. RED: with a stderr that raises
  `BrokenPipeError` for that note, `graph-work` ended with exit 1 and no final line; now it drains and prints the
  final line (exit 0). `GRAPH_WORK_HELP` no longer says "always": a second Ctrl+C (exit 130) and a usage error (exit
  2, the usage message on stderr) print no final line. The test pins the new text and that a usage error prints
  nothing on stdout. `_final_line`'s docstring says the same.
- Unknown keys in workflow issue paths: **fixed**. `issues_from_validation` points an `extra_field` at the object
  holding the key and adds `UNKNOWN_KEY_HIDDEN` (moved from `cli.py` to `desktop_workflows.py`). The CLI's
  `_file_issues` keeps its stricter truncation and no longer drops a segment or adds the note itself. RED:
  `project_create` with `zz-secret-name` gave path `/zz-secret-name`.
  - Deviation: 39 existing test cases pinned the old paths (`/command`, `/spec/<key>`, `/RUNPOD_API_KEY`,
    `/estimates/0/RUNPOD_API_KEY`, `/graph/nodes/0/foo`, `/a~1b~0c`). They now expect the parent object. The escape
    test moved to a dict key that is not an unknown key (`/mapping/fields/a~1b~0c/0`). The UI routes issues by
    prefix and shows its own text per code, so no view changed.
- Union tag as the last pointer segment (found by 11.2, added by root): **fixed**. Only a `missing` error keeps a
  trailing key that the request lacks. Any other trailing segment absent from the request is a union member or
  validator tag and is dropped. RED: "weights must sum to one" and "merge models must be distinct" pointed at
  `/graph/nodes/<i>/merge`; they now point at `/graph/nodes/<i>`. A missing `weights` stays `/graph/nodes/3/weights`,
  and a bad model reference stays under `/graph/nodes/4/models/0/…`.
- `controller_entry._observed_profile`: **fixed**. It pins with `workflow_planning.pin_observation`, the function a
  real `graph_plan` and `observations_list` use. RED (`test_the_controller_and_planning_skip_a_profile_for_the_same_reason`,
  9.8's `~` home probe): planning said `observation_revision_differs` while the controller said `invalid_fields`.
  - Which profiles are usable is unchanged. Two reason labels now follow planning:
    - a `~` key path whose home changed is `observation_revision_differs` (was `invalid_fields`);
    - a refused key path with an observation of another revision is `invalid_fields` (was
      `observation_revision_differs`).
  - Because the first reason is one `controller_status` re-decides, `restart_required` now names such a profile once
    `~` points at the observed key again. The test pins that too.
- Duplicate submit across windows: **fixed**. `project-store.ts` `requestIdFor(planSha256)` gives `plan-<sha256>`
  (fits `RequestId`), and `holdPlan` uses it. The per-session random ID map and `createProjectStore`'s ID-generator
  parameter are gone. The runs-list check before the approval stays. RED: `graph-plan.spec.ts` sent a random UUID,
  and two stores (two windows) held different IDs for one plan.
  - Backend pin (passes on the old code by design): two submits with `plan-<sha>` give one run, and after a cancel
    the same plan answers with that run.
  - **Policy: a plan is never registered twice, not after a cancel or a failure either. To run it again, change an
    input so planning makes a new plan.**
- `tests/test_repository_text.py`: **fixed**. Files are read as bytes; a NUL byte marks a binary; the rest decodes
  with `errors="replace"`. U+061C joins the forbidden set, and every code point is listed as an integer. RED: a
  mostly-text file with one invalid byte and U+202E was skipped, U+061C was not reported, and a NUL file was
  scanned. The repository still scans clean.
- Flake `connections.spec.ts:515`: **not reproduced**; synchronization hardened.
  - The old test passed 100 of 100 (one worker), then 200 of 200 with 8 workers.
  - `open()` waited only for the loading notice to be absent, which an unrendered page also satisfies. It now waits
    for the boot to reach `capabilities` (as `openWorkbench` does), then for the notice.
  - The test waits for the held plan before leaving 학습 설정 and for the GPU list before saving.
  - The hardened test passed 100 of 100 with 6 workers. Likely mechanism: the next item.
- Flake `graph-editor.spec.ts` boot timeout: **mechanism reproduced and fixed**. RED: a scratch spec touched
  `desktop/src/workbench.ts` (mtime only) while a page was open; Vite reloaded the page, so the mock was reset.
  - With `TUNING_FACTORY_E2E=1`, which `playwright.config.ts` now sets on its web server, `vite.config.ts` serves
    with `hmr: false` and `watch: null`, and the page stays. Without it (`npm run dev`), HMR and the watcher are
    unchanged.
  - A Node test pins both server option sets and the Playwright env.
  - **Run Playwright from a tree nobody edits**: a module not yet served is still read from disk when first
    requested.
- Flake `test_ambiguous_launch_waits_for_the_operator[False-desktop]` `PermissionError`: **not reproduced**; the
  rename was hardened.
  - The test's only renames are `ModelArtifactStore.freeze`'s rename of the finished `.freeze-*` directory
    (production code). A rename probe plugin under `%TEMP%` found them; pytest's own tmp cleanup catches `OSError`.
  - 50 separate runs of both `-desktop` cases, partly beside a full Playwright run: 0 failures.
  - Windows refuses a directory rename with WinError 5 or 32 while another process (an antivirus or indexer scanning
    the files just written) holds a handle inside it. `_rename_directory` now retries such a rename at most 10 times,
    50 ms apart, while the destination does not exist, then raises.
  - RED: a rename that raised WinError 5 (and 32) twice failed `freeze`. A plain `PermissionError` is raised at once;
    a lasting violation is raised after 10 tries.
- Flake `test_a_drain_fenced_by_a_successor_is_not_a_clean_stop` (30 s waiting for the first tick): **mechanism
  reproduced and fixed** (test only).
  - The wait read "heartbeat > acquired". The first tick renews the owner lease 6–50 ms after acquiring it, and
    Python 3.12's `time.time()` on Windows is `GetSystemTimeAsFileTime` (resolution 15.625 ms at the default timer
    rate). Equal timestamps leave the next heartbeat 200 s away.
  - RED: a scratch plugin coarsening the controller's clock to 500 ms reproduced "timed out after 30 s waiting for the
    first tick".
  - The test now observes the tick itself (a wrapped `GraphController.tick` sets an event). The bound is
    `LOADED_WAIT` = 120 s, and a controller that returns early fails at once with its result. It passes with the
    coarse clock.
  - 11.2 reached the same cause independently (its entry above).
  - No production logic relies on a strict `heartbeat > acquired`:
    - `acquired` is written and displayed only;
    - liveness (`running`/`stale`) compares `expires` with now;
    - `heartbeat_age_seconds` is clamped at 0;
    - `_clear_stale_stop_request` keeps a request with `requested_at >= acquired_at`, so a same-tick stop request is
      honoured.
- Tutorial `end()` (optional): **fixed**. After the end's compatibility check the tour renders through `renderLate`,
  and it re-focuses the launcher only if the focus is still on it or nowhere. RED: typing a project name while that
  answer was held lost the text ("" after the render). A mutant that always re-focuses the launcher fails the new
  test.
- `reward_binding` route reasons and select candidates in `graph-model.ts` (optional, 10.5 M3): **declined**. The
  planner's reward rules depend on resolved descriptors (semantic kind, the LoRA reward's exact effective base, select
  candidates). A local copy would be partial and could disable valid sources; the planner stays authoritative and
  refuses at validate and plan time.
- Already closed in 10.8 (checked in the code, not changed):
  - the navigation sequence number (`main.ts` `navigations`);
  - one-line name, task type and recipe name (`oneLine` in `projects.ts`, `recipes.ts`);
  - full renders during an IME (`renderSoft`);
  - escapes in `data-import.ts` `isolatedText` (no literal isolate bytes);
  - the 500-choice cap and flags-only blocking notes (10.8 entry).

Actual verification:
- Python, main venv with `PYTHONPATH` set to this worktree's `src`.
  - `pytest -q`: **1728 passed, 1 skipped** alone (6 min 11 s), and again **1728 passed, 1 skipped** beside a full
    Playwright run (8 min 10 s). That is 1718 plus 10 new tests.
  - `ruff check src tests` passes.
  - `scripts/check_secrets.py`: 0 findings.
- From `desktop/`, against this worktree's own Vite on port 1500 (scratch config under `%TEMP%`,
  `reuseExistingServer: false`, `TUNING_FACTORY_E2E=1` as the repository config sets it):
  - `npx playwright test` **254 passed**: 251 plus 3 new (the request ID and server-option Node tests, and the
    tutorial test). Before any change, the same setup gave 251.
  - `npm run build` passes.
- CPU acceptance (`src/tuning_factory` changed): `verify_model_ops_cpu.py` exit 0, **29/29**, `passed: true`,
  `source_sha256` `a388169e8115a691d17dc1215692e44efb09b9089157cb9df66162788683f746`.
- Every changed file is CR-free, and a byte scan finds no bidi control, U+061C or NUL in them.
- Scratch configs, probes and plugins under `%TEMP%` were deleted.

- Rebased onto `0f84972` (11.2's seed script, 11.3a/b and 11.4), then onto `4925049` (11.2's acceptance tests and
  its planner fix). Only this file conflicted each time, and every entry was kept.
  - On `0f84972`: `pytest -q` **1735 passed, 1 skipped**; `npx playwright test` **254 passed**; the CPU acceptance
    gave 29/29 with the same `source_sha256` (that base changed nothing under `src/`).
  - On `4925049`:
    - `pytest -q` **1755 passed, 1 skipped** (the base's tests plus these 10), beside a full Playwright run;
    - `npx playwright test` **254 passed**, and `npm run build` passes;
    - Ruff passes, and `check_secrets.py` reports 0 findings;
    - the CPU acceptance gives exit 0, **29/29**, `passed: true`, `source_sha256`
      `7fd0f911601f4d67ee0cbe7bf73a81e9b447e205aa2796c1dd988281815622dc` (`graph_planner.py` changed in the base).

Not verified or still open:
- No machine-level reproduction of the two Windows flakes (WinError 5, the 30 s first tick); each fix addresses the
  mechanism found.
- Hardening a flaky legacy test (`connections.spec.ts`) is the listed deviation of §1.
- For 11.4 (its files, not edited here), three sentences now lag this change:
  - `docs/CONFIGURATION.md` "issues point at the offending field": an unknown key points at the object holding it.
  - `docs/CONFIGURATION.md` on `graph_submit`: the desktop sends `plan-<plan_sha256>` as the request ID.
  - `docs/TRAINING_WORKFLOWS.md` §6 "다시 실행하려면 다시 계획하세요": the same inputs plan the same hash, which
    answers with the first run. Change an input to run again.

## Hosted CI red since 7291742 (Task 9.6), fixed, 2026-10-21

Commit `test: keep CLI help tests stable under hosted CI terminals`, in the worktree `D:\llm-tuning-factory-wt102` on
`01c8503`. Test only: `tests/test_cli_graph.py`, `tests/test_gpu_servers.py` and this file. Found by integration
review R5.

- **Red since 9.6.** The hosted workflow "CPU verification" (`.github/workflows/ci.yml`) failed on every push from
  `7291742` to `01c8503`: 26 consecutive red runs in `gh run list`, 39477435008 to 39534232202 (the review counted
  29). The last green run was 39475674047 on `0c38b3f`.
  - Ubuntu: `2 failed, 1748 passed, 5 skipped, 1 deselected`. The failures were
    `test_cli_graph.py::test_existing_cli_commands_are_unchanged` and
    `test_graph_work_refusals_before_the_start_print_a_final_line`, which both read `--help` through `flat()`.
  - Windows: cancelled by fail-fast at about 28 % of its pytest step, after the same two failures. The rest of the
    Windows suite has not run on hosted CI since `7291742`.
- **The earlier green claims were local only.** The suite counts in the entries from Task 9.6 to Task 11.1 above
  (up to "1755 passed, 1 skipped") are local Windows runs without `GITHUB_ACTIONS`. Hosted CI was red throughout.
- **Cause.** `typer.rich_utils` reads `GITHUB_ACTIONS`, `FORCE_COLOR` and `PY_COLORS` at import and then forces a
  terminal. Rich styles the help with ANSI escapes even under `CliRunner`, and `flat()` kept the escapes glued to
  the words, so no command name or help sentence matched. RED locally: with `GITHUB_ACTIONS=true CI=true`, both tests
  fail on Windows; without them, both pass.
- **Fix.**
  - `flat()` strips terminal escape sequences (CSI and OSC) before it joins the words. This holds whatever forces the
    terminal. A per-test `NO_COLOR` or `_TYPER_FORCE_DISABLE_TERMINAL` would not: Typer has read its variables at
    import, and `NO_COLOR` keeps bold.
  - New `test_help_reads_the_same_through_a_forced_terminal`: root `--help` and `graph-work --help`, rendered with
    `typer.rich_utils.FORCE_TERMINAL` off and on, give the same `flat()` words, and the forced render does carry
    escapes. RED: with the stripping disabled (a scratch plugin), it fails with no CI variable set.
  - `test_gpu_servers.py::test_doctor_rejects_pod_id_combined_with_a_server_profile` now uses the same `flat()`
    instead of its own SGR-only regex. It already passed on hosted CI, because its `TERM=dumb` keeps Rich plain.
  - Audit: before the fix, the full suite with `GITHUB_ACTIONS=true CI=true` failed only these two tests
    (`2 failed, 1753 passed, 1 deselected`).
- **The help tests no longer touch the checkout (review M2).** `graph-work --help` runs the root callback, which
  builds `Factory(Path("state"))` and loads `.env` relative to the working directory, so the test created or migrated
  the repository's `./state/jobs.db`. With `state/` moved aside, the old tests recreated it and the new ones do not.
  Both help tests now `monkeypatch.chdir` into a scratch directory. The other CLI tests already pass `--state-dir`.
- **Windows.** `ci.yml` and `windows-desktop.yml` show no other blocker. `.gitattributes` pins LF, and
  `windows-desktop.yml` runs only on pull requests and by hand.

Actual verification (main venv, `PYTHONPATH` set to this worktree's `src`, Windows):
- `pytest -q`: **1756 passed, 1 skipped**, the 1755 baseline plus the new test.
- `GITHUB_ACTIONS=true CI=true pytest -q`: **1756 passed, 1 skipped**.
- `GITHUB_ACTIONS=true CI=true pytest -m "not gpu" -q`, the hosted command: **1756 passed, 1 deselected**.
- `ruff check src tests` passes, and `scripts/check_secrets.py` reports 0 findings.

Not verified here: the hosted run of this commit. Its result is reported with the push, not in this entry.

---

# Current extension acceptance — 2026-10-15, Windows 0.3.0

Approved training-capability/S3 design implemented on codex/training-capabilities-s3. This section supersedes
the feature-limit statements in older dated entries; it does not expand their real GPU evidence.

Implemented PT/SFT/RM/PPO/DPO/KTO, DPO/IPO/ORPO/SimPO/KTO-pair losses, Full/Freeze/LoRA/QLoRA/OFT,
typed method options and shared UI/config/worker compatibility rules. Fifteen initial text model profiles
cover Qwen2.5/Llama/Mistral/Gemma2. HF search/latest-update sort and exact repository-ID inspection/import now
persist immutable model profiles with config hashes, revisions and architecture/template-bound capabilities.
Qwen3.5/3.6 and Gemma4 structures can be imported for the app's initial text-SFT scope; this conservative
app limit is not a claim that upstream forbids other stages. Unknown structures, custom remote code,
pre-quantized inference weights and multimodal datasets are rejected with reasons. Qwen3 PPO is disabled
because its selected template is absent in the separate legacy PPO runtime. Phi uses the actual `phi` template.
HF tokens are vault-held metadata credentials only; gated GPU weights still require separately prepared access/cache.
Actual immutable HF metadata and worker GPU/BF16/architecture facts are checked.
Stage-specific datasets/descriptors, full model shards, adapters, value heads, generation and native RM evaluation
are handled. Cross-stage metric deltas are refused. Large artifact transfer/hash checks use streaming file hashes.

S3 connection uses explicit vault-held credentials, SigV4, list pagination/exact-key HEAD, VersionId/ETag frozen GET,
32 MiB bounded local import, length/checksum/SHA256 validation, safe random paths and partial-file cleanup.
HEAD checksum fields survive the UI import. Native settings reject secret-bearing/presigned endpoints before any
vault or plaintext settings write. S3 import and subsequent GPU export approvals remain separate; no S3 keys go to GPU.

Verification: Python 3.12 locked sync succeeds; **214 passed, 1 skipped**, Ruff passes. The skip is the opt-in
GPU test. UI **23 passed**, TypeScript/Vite build passes. Full native Rust **4 tests passed** on version 0.3.0.
S3 tests include actual boto3 SigV4 requests with independently verified signing to a local synthetic S3 endpoint;
this is not an AWS/NCP/MinIO account acceptance. New regression failures were observed before fixes.

Pinned LF0.9.5's main dependency check and PPO's old TRL API conflict. PPO therefore uses an isolated pristine
LF0.9.3/TRL0.9.6/Transformers4.52.4/PEFT0.15.2/Accelerate1.7.0/Datasets3.6.0 environment, not disabled version checks.
Actual CPU imports/API signatures/PPOConfig construction passed using Torch2.7.1+cpu; no model downloaded.
PyArrow20 is pinned to avoid the NumPy2 requirement of PyArrow26 conflicting with legacy NumPy1.26.
Both Linux GPU dependency locks are fully hash-resolved. The CPU probe is separate from CUDA validation.
See [CPU receipt](acceptance/2026-10-15-ppo-cpu.json) and [feature guide](TRAINING_AND_STORAGE.md).

Explicit remaining scope: new combinations have **not** been run on an actual GPU; no real S3 account/object or
institution data was accessed. Historical 0.2.3 H100 evidence below is SFT/Qwen0.5 only. PPO resume, OFT resume,
Full/Freeze RM resume and OFT standalone prediction/reward evaluation are blocked by upstream limits.
PPO prompts currently require Alpaca; incompatible odd-length ShareGPT prompts are rejected before launch.
PPO reward models must be pre-provisioned with a complete inventory hash; LoRA base model and Full tokenizer
identity are checked. Multimodal data/unknown architectures, alternate optimizers/quantizers/plugins, unbounded streaming,
cross-PC scheduling, serving, clean second-PC install and signing are not accepted here.

Real public HF searches found Qwen3.5, Qwen3.6 and Gemma4. Qwen3.5-0.8B immutable metadata inspection passed.
In an isolated Transformers5.6.0 environment, actual AutoConfig loaded Qwen3.5-0.8B and Qwen3.6-27B as
Qwen3_5Config; Gemma4 config registration was checked. No weights were downloaded and no latest-model
GPU training was performed. See [config-only CPU receipt](acceptance/2026-10-15-modern-config-cpu.json).

Final frozen bridge passed all six offline stage plans, both dependency locks, every worker Python source byte,
and dynamic S3 SDK data loading. Frozen backend commit 36e479d0b5b4192b1552352b786474f2d91db07d,
source SHA256 b8cbf0d8c8eb4ff5aedd1fcfa854b512dd186dc44153ee4c3d7b0c6669ec8595. All 23 UI tests pass. Actual public Qwen revision/config metadata lookup
passes without downloading model weights.

Final unsigned Windows 0.3.0 NSIS installed successfully on the development Windows PC. Actual installed native
smoke verified catalog, synthetic local registration/preview, immutable dry-run, PPO prerequisite guard,
actual HF Qwen3.5 inspection/import/pinned dry-run, and Windows vault save/masked read/delete for RunPod,
all three S3 credential fields and HF_TOKEN. Prior public settings
were preserved and test credentials removed. No live S3/GPU work was performed.
Installer SHA256 b7be610ed6c10ec98b89f3b8a3fb2459435306d1d67283354707a94fd3f4e49b; 47043962 bytes.
See [final safe receipt](acceptance/2026-10-15-desktop-030.json). Installer prepared for private prerelease v0.3.0;
review PR #2 is separate from the original Windows PR #1. Latest hosted reruns are reported separately from local checks.

---

# Current Windows GPU acceptance — 2026-10-15, app 0.2.3

This section supersedes outstanding GPU statements in the dated historical entries below.
Installed Windows UI + real H100 + Qwen2.5-0.5B synthetic SFT passed LoRA, genuine NF4 4-bit QLoRA,
native generation, cancellation, checkpoint-10 → step-100 resume with preserved history, controller-loss
reconciliation into the original MLflow run, GPU-busy queue behavior, and complete artifact hash recovery.
Every output file uploaded to MLflow in the accepted audits was downloaded again and hash-verified.
See [report](GPU_ACCEPTANCE_2026-10-15.md) and [safe receipt](acceptance/2026-10-15-desktop-gpu.json).

Fixed missing C-compiler preflight, a misleading queue completion notice, and the quantizer alias:
LLaMA-Factory 0.9.5 requires `bnb`, not `bitsandbytes`. The old unquantized QLoRA run was rejected and
marked FAILED in shared MLflow. Final QLoRA has 168 actual CUDA Linear4bit layers, NF4 quantization,
4.0 storage bits per original linear weight and a finite CUDA forward pass. Train and prediction logs
both confirm actual 4-bit loading; the worker rejects a silent fallback.

Final Python source SHA256: c5b040822868981c064ebb183c9a741424ea03d096f11b45c9ae1c361a2228ee
(frozen source commit 7fca6a441dfa875c6d0f40b7ce979c2fdefe772e).
Earlier LoRA/resume/queue receipts use source 8f91704981fadb47694fbb0c8fc4a1683427bbbc4c70cade72936285083f9298;
they are not represented as rerun on the final source. Final-source genuine QLoRA and controller recovery passed.
Final UI source 1222d626ed44a4143b18f1bcc3d4ce22c21636cf displays package version 0.2.3.

Fresh locked Python 3.12 sync passes; pytest **78 passed, 1 skipped** (opt-in GPU pytest; paid manual evidence
is separate); Ruff passes. UI **10 passed**, TypeScript/Vite build and frozen-package/wheel integrity pass.
Rust allowlist test passed; Rust source unchanged during final quantizer/footer fixes, final NSIS build passed.
GitHub CPU run 38667226882 and Windows package run 38667226909 both succeeded for 1222d62.
Installer SHA256: 35c9995db4d720506a3814f9e142ebe94ef776e0de0aae447d486fd56a464af5.
Installed sidecar equals build bytes; installed UI footer reports 0.2.3. NSIS changes only the native bundle
marker UNK→NSS (three bytes). Test credentials were deleted and original public settings restored afterward.

Temporary owned H100 stopped to EXITED, then deleted (GET 404). Existing H200 stayed EXITED and the
existing network volume was preserved. Upper estimate 82.36 minutes / $5.48 GPU, storage excluded,
within approved 90 minutes / $5.99. No institution data was transferred.

Remaining scope: only SFT LoRA/QLoRA and a fixed model catalog; no complete automatic model/method
capability matrix, direct S3 dataset connector, large-data streaming, multi-PC scheduler, or serving.
Other catalog models, gated-model authentication, a clean second PC and signing remain unverified.
Training-adapter Model Registry registration and a standalone GPU image build were not accepted here.
Synthetic data establishes pipeline behavior, not business quality or numeric reproducibility.

---

# Progress and acceptance status — 2026-10-14

## Task 11.5 fixes: planning and workflow (synthetic data, plan dedupe, merges, selections), 2026-10-21

Branch `codex/rc-planning-fixes` from RC `01c8503`, worktree `D:\llm-tuning-factory-wt115`. Each item was test first:
the new test was run and failed for the stated reason before the fix. No GPU, network, CPU numerics or dataset
transfer.

- **R1 I-2 (synthetic data), root's option (b): fixed.** `ModelDescriptor.eligible_for_real_use` (neither synthetic
  nor simulated) now gates only selection (evaluation records carry the flag) and MLflow registration
  (`tracking_outbox`). The new `ModelDescriptor.usable_in_real_operations` (not simulated) gates every artifact a real
  operation takes as an input: `graph_planner.compile_node`'s `verified` ("simulation model is ineligible for real
  execution"), `validate_input` ("simulation artifact is ineligible for real use"), `check_merge_descriptors`
  ("merge requires real completed artifacts") and `operation_runner._derive` (control plane and worker). Simulated
  artifacts stay refused as real inputs at each of these layers. Taint was already airtight and is now pinned:
  training, continuation, merge and materialization outputs inherit `synthetic` from any input or dataset, freeze
  refuses a child that drops it, and an evaluation record is synthetic when its model or benchmark data is.
  - RED: R1's probe graphs on a real plan over synthetic, exportable data through the real `OperationAttemptRunner`
    and the fake GPU host failed `b`, `ea`, `ec` with "synthetic/simulation model is ineligible for real execution".
  - Tests: `tests/test_synthetic_chains.py` (train a -> evaluate e(a); b continues a on *real* data; m merges two
    synthetic adapters; s selects with the merged candidate: COMPLETED, one launch per node, every descendant
    synthetic, `no_winner`, no `model_registration` event and no model version; a real chain still registers;
    model-input checks; channel `graph_plan` (real) and `graph_submit` accept a chain over synthetic data);
    `test_graph_planner::test_real_compilation_refuses_only_simulated_input_models`; updated
    `test_model_artifacts`, `test_model_ops_contract`, `test_model_inputs` cases (simulation refused, synthetic
    accepted).
- **R2 I1 (backend plan-hash dedupe): fixed.** `GraphStore.submit(plan, request_id, approval, *, unique_plan=False)`.
  With `unique_plan=True`, inside the existing `BEGIN IMMEDIATE`, a plan that is already a run under another request
  ID (any status, cancelled included) raises `PlanSubmitted(run_id, status)` with the text `PLAN_SUBMITTED`
  ("plan already submitted as another run; to run it again, change an input and plan again"). Only the workflow
  `graph_submit` passes it; the CLI `graph-submit` goes through it. Answer: `status: "conflict"`, one issue
  `{"code": "plan_submitted", "path": "/plan_sha256", "message": PLAN_SUBMITTED, "node_id": null, "row_number":
  null}`, `data: {"run_id": <existing run>, "status": <its status>}`. `desktop_workflows.conflict()` takes an
  optional `data=`; `CONFLICTS` maps the text to `plan_submitted`. The same request ID and plan stay idempotent.
  The staleness check (`plan_stale`) and a real plan's approval and data checks still run first.
  - RED: two concurrent subprocess submits with different IDs made two runs.
  - Tests (`tests/test_workflow_runs.py`): IDs `a` then `b` (also after a cancel, and as `plan-<sha>`), a re-plan
    with a changed input succeeds, the engine default keeps request-ID semantics, the CLI through `CliRunner` with
    `--state-dir` in tmp, two concurrent subprocess submits give exactly one run. Three tests that relied on duplicate
    submits (`test_simulation_submit_skips_profile_freshness_but_not_the_project`,
    `test_pause_resume_cancel_map_refusals_to_conflicts`, `test_run_status_is_db_only_and_secret_free`) now use
    distinct plans (another timeout).
- **R2 M3: fixed.** `model_root_snapshot`'s `_download` opens through `hf_targets._open` (at most five redirects,
  each closed with its body unread, HTTPS on every hop, Authorization dropped across origins) and caps the body
  with `hf_targets._read`. Its hub's metadata `get` uses the same helpers with the 4 MiB limit; the client no longer
  follows redirects itself. `_open` takes the too-many-redirects issue code (`snapshot_failed` here). Tests
  (`httpx.MockTransport`, endless undeclared bodies): CDN redirect (no token there, 0 bytes pulled), redirect loop
  (6 requests, 0 bytes pulled), oversized revision info (refused after one chunk past 4 MiB).
- **R5 I2: fixed (test).** `test_workflow_planning::test_an_instruction_recipe_bound_to_other_data_is_refused_before_any_run`
  (preference and text data): `train_contract` on `/graph/nodes/0` through `graph_validate` and `graph_plan`
  (simulation and real), nothing stored. With `graph_planner.py:349-350` removed both cases fail (checked).
- **R5 I3 and root's decision on R5 M4: fixed.** `tests/test_merge_fallback.py` (controller world, ScriptedDispatcher):
  a regression keeps the best original (`merge_regression`), an improvement selects the merged model, an ineligible
  merged evaluation keeps the original, and the kept fail-closed rule: without `allow_partial` a failed merge or a
  failed merged evaluation blocks the selection ("partial selection is not allowed", run FAILED); with
  `allow_partial` the best original is chosen.
  - Defect found and fixed: a receipt that excluded a nonfinite score was compared with `==` after a JSON round trip
    (NaN never equals NaN), so the selection failed with "selection receipt mismatch". `benchmarks.same_receipt`
    compares canonical JSON; the store's two receipt checks and the compiler of a selection's child use it. Test:
    `test_a_nonfinite_score_never_breaks_the_selection_receipt`.
  - `partial_selection_not_allowed` next to a selected winner was inconsistent (the pairwise comparison's gating
    reason). An ineligible merged evaluation now gives the one reason `merged_not_eligible`; the evidence stays in
    `excluded`. Tests in `test_benchmarks.py`.
- **R5 M6: fixed (test).** `test_graph_planner::test_lora_sft_materialized_into_dpo_binds_policy_and_reference_to_the_materialized_model`.
- **R1 m2: fixed at `plan_graph`.** `_MergeLineage` compares the known inputs (`len(known) >= 2`) and a merge's
  candidates are its known inputs' shared ones (this reaches `validate_graph` too, `merge_bases_differ`); a
  materialized model fed into a merge, or a selection none of whose candidates is a plain adapter, is refused ("merge
  requires plain causal LoRA/QLoRA parents"; a selection that may pick an adapter still plans); the merged rank is a
  floor carried through nested merges (sum) and selections (smallest candidate); duplicates provable through
  selections are refused ("merge requires distinct actual artifacts", a bipartite matching over each input's possible
  models). Recorded: these refusals live in `plan_graph` (`graph_plan` shows `plan_refused`), not `validate_graph`,
  as the existing merge rank and parent-kind refusals do: a new issue code would need its Korean text in
  `desktop/src/issues.ts`, and artifact snapshots exist only at planning. A merge whose selections may pick the same
  model at run time is not provable and stays with compile.
- **R1 m3: fixed at `plan_graph`.** Refused: repeated evaluation refs, two evaluations that can only be the same
  model (also through a selection) ("selection candidates must be distinct models"), more than one merged candidate
  ("a selection takes at most one merged candidate"), and a selection whose only candidates are merged ("a selection
  needs an original candidate beside its merged one", it always records no winner). Recorded: not a schema
  validator, so plans and runs stored with such graphs stay readable; `SelectNode.evaluations` keeps no
  `max_length` (64 nodes bound it).
- **Re-review minors m1-m3: fixed.** m1: `graph_planner.NodeRefusal` (a `ValueError` with `node_id`) is raised by
  the selection-shape refusals and wraps every `_static_routes` refusal; `graph_plan` reports it as `{"code":
  "plan_refused", "path": "/graph/nodes/<index>", "node_id": <node>, "message": <unchanged text>}`, and a refusal of
  the whole plan keeps `path: ""`, `node_id: null` (`tests/test_plan_refusal_nodes.py`, through the channel). m2: the
  snapshot client sends `Accept-Encoding: identity` and refuses an encoded answer unread (`snapshot_failed`,
  `hf_targets.ENCODED_RESPONSE`; gzip MockTransport test). m3: a deterministic test holds submit `a` inside its
  `BEGIN IMMEDIATE` until submit `b` reaches its own, then `b` refuses with `a`'s run; with the check moved before
  the transaction it fails. Verified: the ten affected test files **396 passed**; `GITHUB_ACTIONS=true CI=true` on
  `test_plan_refusal_nodes`, `test_workflow_planning`, `test_workflow_runs` **141 passed**; ruff clean;
  `git diff --check` clean.

Verification (this worktree, Python 3.12, `uv sync --extra dev --extra tracking --locked`):
- `uv run pytest -q`: **1808 passed, 1 skipped** (755 s; the skip is the opt-in real-GPU test). An earlier full run
  on the first version of the R1 m2 change gave 1 failed, 1806 passed, 1 skipped: a merge of two selections whose
  candidates included a full-model artifact was refused although the selection could pick an adapter. The check was
  narrowed to provable cases (a selection with no adapter candidate) before the final run.
- `GITHUB_ACTIONS=true CI=true uv run pytest tests/test_workflow_runs.py tests/test_workflow_planning.py
  tests/test_synthetic_chains.py tests/test_hf_targets.py -q`: **215 passed**.
- `uv run ruff check src tests`: all checks passed. `uv run python scripts/check_secrets.py`: 1176 file versions,
  0 findings. `git diff --check 01c8503..HEAD`: clean; every changed file is LF (`git ls-files --eol`).
- Mutation check (R5 I2): with the two kind-check lines of `graph_planner._definition_issues` removed, both
  parametrizations of the new channel test fail; restored.
- Not run here: CPU numerics, GPU, network, hosted CI (root's).

GPU work still outstanding for this area: the L2b chain on synthetic data (train -> continue -> evaluate -> select ->
merge -> evaluate) has only run against the fake GPU host; its live run stays as planned in Task 11.

## Task 11.5 fixes: release scripts and docs, 2026-10-21

Worktree `D:\llm-tuning-factory-wt116`, branch `codex/rc-docs-fixes` on RC `01c8503`, from the R4 review (release
machinery and docs) plus the doc items of R1, R2 and R5 that root assigned. Commits: `b27a22d` (verifier), `72a953a`
(CI lint), `bd2924a` (native smoke), `131bff1` (docs) and this entry. No `src/`, `desktop/src/` or
`desktop/src-tauri/` change.

Scripts and CI:
- R4 M1: **fixed**. `verify_desktop_package.frozen_only(bridge)` now sets `frozen_engine`, `frozen_s3_sdk_loaded`,
  `frozen_lock_path` and `onefile_child_exited`: true for a frozen bridge, null in source mode. Receipt consumers:
  `windows-desktop.yml` runs the default (frozen) bridge and parses nothing, and the 11.6 steps read "every key true"
  from frozen receipts, whose values are unchanged.
- R4 M11: **fixed**. New `tests/test_verify_desktop_package.py`:
  - `test_only_a_frozen_bridge_reports_frozen_evidence` runs on every platform.
  - `test_the_verifier_passes_in_source_mode_offline_and_cleans_up` (`slow`) runs the verifier as a subprocess on
    `scripts/desktop_entry.py` with `--check legacy/arguments/workflow/controller`. TEMP, TMP, TMPDIR, HOME and
    USERPROFILE point into `tmp_path`, credential variables are dropped, and a loopback-only `sitecustomize` guard on
    PYTHONPATH records and refuses any other connect or name lookup in the verifier and every child. It pins the 17
    passed keys, the four null keys, `source_sha256`, `gpu_started: false`, the receipt file bytes, zero refusals, a
    guard loaded in at least 10 processes, and an empty TEMP afterwards.
  - The `slow` marker sits beside `gpu` in `pyproject.toml` and runs by default: `ci.yml`'s `-m "not gpu"` and
    `windows-desktop.yml`'s plain `pytest` both include it, and `-m "not slow"` deselects it. Off Windows it is
    skipped, because the verifier accepts the Windows sidecar and no offline Linux environment was available here to
    run it on ubuntu.
  - RED: `AttributeError: ... no attribute 'frozen_only'`, and the source receipt had the four keys true.
- R4 M2 (script): **fixed**. The controller-absent and no-active-runs check now runs before the first write. That
  write is section 2's `desktop_smoke` dataset, which comes before the `project_create` the report named. A refused
  smoke now writes nothing.
- R4 M3: **fixed**. Sections 6 and 7 start the real controller only on a state with no executor profiles, no saved
  credential and no `MLFLOW_TRACKING_URI`. The broker hands the controller the public URI even under
  `auth_mode: none`, which `!savedCredentials` alone would miss. Otherwise the synthetic profile is deleted at once,
  and controller and flow report `skipped_saved_settings`. `node --check` passes; the smoke was not run (11.6).
- R4 M4: **recorded, no change**. `forget_profile_secrets` deleting the keys of unserved entries (an unknown provider
  or a non-object value) is what "forget" means. Only the smoke's "no profiles" guard cannot see those entries, which
  is very rare.
- R4 M9: **fixed**. `ci.yml` and `windows-desktop.yml` run `uv run ruff check src tests scripts`, the old F401
  (`shlex`) in `scripts/update_approved_runtime.py` is gone, and the developer commands in README and WINDOWS_APP say
  the same. No existing test pinned the CI command lines; `test_both_workflows_lint_the_release_scripts` pins them
  now (RED with the two workflows reverted).

Docs (Korean for users, English for operators):
- R4 I1: **fixed by rewording**. README, WINDOWS_APP and TRAINING_WORKFLOWS §14 no longer claim installed-app checks
  in the past tense. Done: automated tests, the 29-check CPU acceptance and the verifier's source mode. Planned: the
  frozen engine and installed-app checks, as "설치 앱 검사(11.6) … 진행 기록에 기록합니다". Root turns them into past
  tense from the 11.6 receipt.
- R4 doc fixes 1–5 and R2 M2: **fixed**.
  1. CONFIGURATION: an unknown key points at the object that holds it, with "(the unknown key is not shown)".
  2. CONFIGURATION `graph_submit`: the desktop sends `request_id = plan-<plan_sha256>`.
  3. TRAINING_WORKFLOWS §6, plus CONFIGURATION and WINDOWS_APP: re-run guidance. Real plans and dry runs on native
     profiles only give the same hash for the same inputs, so change an input (re-running 자원 확인 is enough). Dry
     runs that use an estimate give a new plan on every 계획 확인.
  4. OPERATIONS: `status --job-id JOB_ID` (twice), and the stale "development branch … not in 0.3.0" note on SSH
     servers.
  5. WINDOWS_APP: the verifier's `.py` source mode, `bridge_mode` and the null frozen keys.
- R4 M2 (docs): **fixed**. WINDOWS_APP no longer says the smoke runs in a "별도 테스트 앱". It says the smoke writes to
  the real app state (projects that cannot be deleted, `desktop_smoke`, and with FLOW a seeded root, datasets, a
  recipe, a benchmark and a run). FLOW's prerequisites are listed: the seed command, no profiles, saved credentials or
  MLflow address, the controller absent and no active runs. The seed command uses PowerShell's
  `"$env:APPDATA\kr.kbiofoundry.tuningfactory\state"`, because a literal `%APPDATA%` does not expand in the
  PowerShell context of that section.
- R4 M6: **fixed**. TRAINING_WORKFLOWS §12 and OPERATIONS: `graph-work`'s exit 3 holds only with `--state-dir` at the
  app state. With the default `./state`, a second controller can dispatch to the same borrowed Pod, because GPU locks
  live per state.
- R4 M7: **fixed**. The installer is named `LLM.Tuning.Factory_0.4.0_x64-setup.exe`.
- R5 M3: **fixed**. TRAINING_WORKFLOWS (§11 and the §12 table), CONFIGURATION and OPERATIONS recommend
  `--request-id plan-<PLAN_SHA256>`.
- Documented to root's target for behaviour that other batches implement in parallel (root re-reviews against the
  final code):
  - Plan dedupe (`plan_submitted`, also after a cancel; the same request ID stays idempotent; desktop and CLI):
    TRAINING_WORKFLOWS §6 and §11, CONFIGURATION `graph_submit`, OPERATIONS. The old text disclosing the CLI
    double-registration hole is gone.
  - Synthetic lineage: TRAINING_WORKFLOWS registration step, §9 and §11, and CONFIGURATION merge rules plus a new
    "Synthetic and simulated lineage" paragraph. Merges now say "not simulated; any synthetic input makes the merged
    output synthetic".
  - Merge fallback (R5 M4): TRAINING_WORKFLOWS §5 and CONFIGURATION `SelectionSpec`.
  - One machine, one profile (R1 m11): TRAINING_WORKFLOWS §7, WINDOWS_APP and CONFIGURATION executor profiles.
    **Deviation from root's wording:** "the app cannot prevent both being used at once" holds only across state
    directories or PCs. Within one state, the D3 cross-provider quarantine (`jobs.py` `_resource_blocked`,
    `graph_controller._conflicts`) makes every RunPod attempt and every SSH attempt wait for each other. What differs
    is the plan, which counts the two profiles separately for 대기 후 순차 실행 and `max_concurrent_gpu_count`. The docs
    say this.
  - Task 11 plan L2b: with `synthetic: true`, the chain runs end to end, both selections end `no_winner`, and nothing
    is registered; registration stays an L3 check with real data.
- Not in this batch: R4 M5 and doc fix 6 (the release-notes warning, in the git-ignored draft), M8 (PR draft links) and
  M10 (`tests/test_cli_graph.py`, owned by the CI-fix agent).

Actual verification:
- `uv run pytest -q`: **1758 passed, 1 skipped** in 945 s, with the code at `bd2924a` and the docs uncommitted, beside
  other agents' suites. That is 1755 at the RC, plus the two verifier tests and the lint pin. The skip is the opt-in
  real-GPU test.
- `uv run pytest tests/test_verify_desktop_package.py`: 2 passed in about 28 s, several times. The earlier RED runs are
  described above.
- `uv run ruff check src tests scripts`: All checks passed. `uv lock --check --offline` passes after the
  `pyproject.toml` marker change.
- `uv run python scripts/check_secrets.py`: `Checked 1101 staged/historical file versions; 0 findings.`
- `node --check desktop/tests/native-smoke.mjs` passes. `git diff --check 01c8503..HEAD` is clean, and
  `git ls-files --eol` shows `i/lf w/lf` for every changed file.
- `uv run python scripts/verify_desktop_package.py --bridge scripts/desktop_entry.py --check legacy --check arguments
  --check workflow --check controller --receipt <scratch>`: exit 0 in 31 s.
  - `bridge_mode: source`, and the 17 check keys are true.
  - `frozen_engine`, `frozen_s3_sdk_loaded`, `frozen_lock_path` and `onefile_child_exited` are null.
  - `engine_child_process` is true (the venv launcher), and `gpu_started` is false.
  - `source_sha256` is `7fd0f911601f4d67ee0cbe7bf73a81e9b447e205aa2796c1dd988281815622dc`.
  - `source_commit` is `"uncommitted"`. This is not new: `code_revision` runs `git rev-parse` in the verifier's temp
    workdir.
  - No `factory-package-*` folder was left. Without `--check`, the same command also runs `wheel`, which fails without
    a build (`no engine wheel under build/desktop-assets/dist`).
- Docs: a scratch checker over README, the four guides, the Task 11 plan and the changed scripts and tests found no CR,
  BOM, control, format, bidi or invisible character, and no backslash-u escape. Every relative link and anchor in the
  changed docs resolves; the plan's pre-existing `…/blob/v0.4.0/` template links and its quoted escape text are
  unchanged. A planted probe (CR, U+200B, a broken link and a broken anchor) was reported. The new curly- or
  single-quoted labels (“자원 확인”, “계획 확인”, ‘최대 동시 GPU’, ‘대기 후 순차 실행’, “실행 보기”) exist in
  `desktop/src`.
- Re-review Minors (docs, `git log` for the SHA): the checkbox label ‘일부 평가만 성공해도 선택’, L2b shows no selection
  outcome with synthetic data, the smoke leftovers name the seed command and the LIVE_HF model, one placeholder `plan-<PLAN_SHA256>`;
  `test_repository_text` passes, `check_secrets.py` checks 1182 file versions with 0 findings.
- Planning re-review (docs, checked against `codex/rc-planning-fixes`: `benchmarks.select_best`/`finalize_with_merge`,
  `graph_store._readiness`, `graph_planner` refusals, `test_merge_fallback`, `test_synthetic_chains`): a failed merge or
  merged evaluation without `allow_partial` blocks the selection and fails the run (not "waits for the operator"), an
  ineligible merged evaluation keeps the original, the synthetic selection rules, the planning refusals for select and
  merge inputs, materialize in the synthetic list, and the `plan_submitted` shape; secret scan 1214 versions (all branches; it grows as they commit), 0 findings.

Not verified or still open:
- The verifier test on ubuntu (skipped there) and on hosted windows-latest; root sees hosted CI.
- The native smoke against an installed app (11.6).
- The plan-dedupe, synthetic-lineage and merge-fallback docs describe behaviour from parallel batches; they need root's
  re-check against the final code.

## Task 11.5 fixes: Windows UI and native profiles, 2026-10-21

Branch `codex/rc-ui-fixes` from RC `01c8503`, worktree `D:\llm-tuning-factory-wt113`. Findings of the R2, R3 and R5
reviews in the UI and the native profile store, plus the UI side of the backend's new plan-hash dedupe. Each test was
written first and seen failing for the reason named; the existing tests that changed are listed.

- R2 I2 `forget_credentials`: **fixed**. The confirmation and the S3 note name "모든 실행 프로필의 RunPod API 키", and the
  confirmation says that real runs pinned to those profiles stop at the next controller start until the key is saved
  again. `profile_credentials_forgotten: false` shows `profile_warning` as the error notice instead of "저장된 인증값을
  삭제했습니다". Test: `forgetting credentials names every profile key and shows a partial failure instead of success`
  (workflow-regression.spec.ts). No legacy spec asserted the old texts.
- R2 M1 profile store across processes: **fixed**.
  - Saves, deletes and the profile part of `forget_credentials` hold an exclusive `std::fs::File::lock` on
    `executor-profiles.lock` around their read-modify-write (no new crate). Store writes use a unique temporary name
    (process, counter, time) and `sync_all` before the rename.
  - The crash window is closed: a new profile is written to the file before its key reaches the vault, and taken out
    again if the vault refuses the key. A crash in between leaves a profile without a key, never an orphaned key. A
    rotated key of an existing profile keeps the old order (key, then file, key restored if the file write fails).
  - Tests: `concurrent_saves_from_separate_lock_handles_keep_every_profile` (8 threads × 4 new profiles, each save with
    its own lock handle; on the old code entries were lost), `a_new_profile_is_in_the_file_before_its_key_is_in_the_vault`.
    `a_failed_store_write_restores_the_previous_vault_state` now blocks the rename with a handle that does not share
    delete (a directory at the old fixed temporary name no longer blocks anything); it is `#[cfg(windows)]`, like every
    CI cargo run. The atomic-replace test checks that no `.tmp` file is left.
  - `forget_credentials` still creates nothing without a profile file; with one, it may add the lock file beside it.
- R2 M4 native key paths: **fixed**. `profiles.rs` refuses what `desktop_workflows.local_file` refuses (NUL; a `:`
  after the drive, so ADS and `nul:`; DOS device names in any component, `CONIN$`, `COM¹` included; surrounding
  whitespace, with Python's `isspace` set), plus a trailing dot (Windows drops it). `~` stays refused as before. The
  device names are Rust constants that `test_rust_key_path_device_names_match_local_file` (test_native_contract.py)
  compares with `_DEVICE_NAMES`. Test: `profile_key_paths_refuse_what_the_planner_refuses` mirrors the cases of
  `test_import_source_reads_local_files_only`. `KEY_PATH_RELATIVE` keeps its prefix (native-smoke.mjs matches it) and
  now names these cases.
- R3 M1: **fixed**. Row counts, role counts and sizes render only as finite numbers ("—" otherwise). Test: `counts from
  a malformed answer are never inserted as markup` (HOSTILE in `row_count`, `canonical_size`, `counts.train`).
- R3 M2: **fixed**.
  - User names inside sentences sit in `<bdi>` (`workbench.isolate`): the import project banner, the editor's toolbar
    notes and blocked/evaluation lists, the plan panel's steps and step issues, the approval dialog's project name and
    waiting steps, and the release dialog.
  - The release dialog shows the name to type without Unicode format characters (and says so) and compares after
    removing them on both sides (`workbench.typedForm`); a label of only invisible characters is named by its node ID.
  - Nit: a train card's recipe choices show the recipe ID beside the name.
  - Tests (U+200B, U+202E, U+202C built from code points): `a node label with invisible or bidi characters is isolated
    and can be typed as it is shown`, `step and project names in the plan and its dialog are isolated…`, `the project
    name in the import banner is isolated…`, `names in toolbar notes are isolated and recipes that share a name differ…`.
- R3 M3: **fixed**. Issue texts say "전체 모델 확정" (graph-model `KIND_LABELS`), and the runs page's `NODE_KIND` is
  `KIND_LABELS` ("자동 선택"). The "실행 위치" column shows the profile name (ID beside it) for profiles this PC has,
  read once per opened run with `executor_profiles_get` (local, never the sidecar; the list page reads nothing new).
  Tests: `the runs page names kinds as the editor does and shows profile names where they are known`, `issue codes
  without an English pattern have Korean texts, and the materialize card has one name`. Changed: two assertions of the
  old "모델 변환(materialize)" text (workflow-core.spec.ts, graph-editor.spec.ts).
- R3 M4: **fixed**. An unreadable run (`{run_id, error}`) stops detail polling; the banner says so and offers "다시
  확인" (one read; a run that became readable is followed again). Test: `an unreadable run is not polled again until it
  is checked by hand` (page.clock, 120 s without a read). Changed: the existing unreadable-run test reads the
  other-run answer through "다시 확인" instead of a 3 s poll.
- R3 M5: **fixed**. The run-exists state and notice add: change an input (최대 실행 시간 등) or inspect the profile again,
  then plan anew; the run's status is shown when known. Test: the existing `a plan that already has a run…` asserts it.
- R3 M6: **fixed**. `reuseExistingServer: false`, pinned beside `serverOptions` in workflow-core.spec.ts.
- R3 M7: **fixed**. The PPO recipe fixture of graph-plan.spec.ts uses `input_kind: "prompt"`.
- R5 M1: **fixed**. Korean texts for `merge_bases_differ`, `executor_profile_invalid`, `tracking_invalid`,
  `benchmark_invalid`, `sample_id_invalid`, `planning_inputs_invalid` and `graph_schema` (Korean backend messages under
  these codes stay as received; the original stays under "원문 보기").
- R5 M5: **fixed**. `workflow-types.ts` declares `GraphRunNode.job_id`, `PlanSummary.nodes[].recipe` (`PlanNodeRecipe`)
  and `.datasets` (`PlanNodeDataset[]`); the local `RunNode` and `PlanNodeReferences` are gone.
- Backend plan-hash dedupe (contract of the planning batch): **UI done**. `graph_submit` → `conflict`,
  `plan_submitted` at `/plan_sha256`, `data: {run_id, status}` shows the run-exists state with the M5 hint and "실행
  보기", and no approval again; without a run in `data` the localized refusal is shown under the plan. `issues.ts` has
  the Korean text; `workflow-types.ts` declares `PlanSubmitted`. Tests (mocked): `a submit the backend refuses as
  already registered shows that run and how to plan anew`, `a plan_submitted refusal without its run is shown as the
  backend's reason`. Not run against the backend change, which lands separately.
- Scoped re-review (root): **fixed**. M1: `forget_profile_secrets` takes the store lock best effort, so a lock file it
  cannot open or lock (a directory there, a share-none handle) no longer keeps every key; the keys are deleted and
  `LOCK_FAILED` is the reported problem (`profile_warning`). M2: the duplicated `PlanNodeRecipe`/`PlanNodeDataset`
  declarations in `workflow-types.ts` (a `git stash pop` while splitting commits re-inserted them; TypeScript merged
  them silently) are gone. Nit: a store write, under the lock, first removes `executor-profiles.*.tmp` left by a crash
  (best effort). Tests: `forgetting_profile_keys_does_not_wait_for_a_lock_it_cannot_take`,
  `a_store_write_removes_temporary_files_a_crash_left_behind`, `the channel's shapes declare each type once`, each
  failing first. Then `npm run build` passes, `npx playwright test` **266 passed**, `cargo test` **54 passed**,
  `pytest tests/test_native_contract.py tests/test_repository_text.py` **11 passed**, `git diff --check` clean.
- Planning batch re-review (root): **fixed**. The five graph_planner refusals sent as `plan_refused` (distinct
  selection candidates, one merged candidate, an original beside the merged one, distinct merge artifacts, plain
  LoRA/QLoRA merge parents) have Korean texts in the editor's terms, the original under "원문 보기". A general plan
  refusal with a `node_id` now also flags that card (`planFeedback`), and stays in "계획 확인 결과". The tutorial's
  data-register step no longer says the synthetic mark blocks real training ("연습 데이터는 연습 전용이고 GPU 전송이
  막혀 있어 …(합성 표시도 붙습니다)"); no test asserted the old text. Tests: `the planner's merge and selection refusals
  are shown in the editor's terms`, `a merge or selection refusal is shown in Korean in the panel and on its card`
  (mocked), and the tutorial's registered-sample test, each failing first. Then `npm run build` passes, `npx
  playwright test` **268 passed**, `pytest tests/test_repository_text.py` 3 passed, `git diff --check` clean.

Actual verification (this worktree, its own `.venv` from `uv sync --extra dev --extra tracking --locked`):
- `npm run build` (tsc + vite) passes.
- `npx playwright test`: **265 passed** (254 before, plus 11 new), own Vite on 1420 with
  `TUNING_FACTORY_E2E=1`.
- `cargo test --locked --offline --release` (src-tauri): **52 passed** (49 plus 3 new); the concurrency test passed 10
  of 10 repeated runs.
- `uv run pytest -q`: **1756 passed, 1 skipped** in 13 min 58 s (1755 plus the new device-name contract test).
  `uv run ruff check src tests`: passes.
- `uv run python scripts/check_secrets.py`: 1130 file versions, 0 findings. `git diff --check`: clean; LF only; no
  literal bidi or invisible character in the changed files (byte scan).
- Not verified: real WebView2 rendering of `<bdi>` and of the release dialog, and two real app windows saving profiles
  at once (Task 11.6 installed-app checks); the dedupe UI against the real backend change.

## Task 11.5 fixes: control plane (R1), 2026-10-21

Branch `codex/rc-control-fixes` in the worktree `D:\llm-tuning-factory-wt114`, on the RC `01c8503`. It fixes R1's
control-plane findings I-1, I-3, I-4, m1 and m5–m9 and part of m11, test first. Every new test was run against
the RC source first (the `src` changes stashed): 37 failed for the reported reason (lost cancel, terminal FAILED
instead of uncertain, the split price refusal, a second dispatch, an unredacted query, an accepted device name, a
raised cleanup error, an accepted 59 s TTL), and the regression guards passed. With the fixes they pass.
Changed: `src/tuning_factory/{attempt_runner,execution,gpu_servers,simulated_dispatch,graph_store,graph_controller,
jobs,runner,auth,desktop,tracking_outbox,model_artifacts,controller_entry}.py`, their tests
(`tests/test_{operation_runner,simulated_dispatch,graph_controller,graph_store,tracking_https,tracking_outbox,
model_artifacts,controller_entry}.py`) and this file.

- **I-1, a cancel lost through reconcile: fixed.**
  - `OperationAttemptRunner.reconcile` and `SimulatedOperationDispatcher.reconcile` map the collected result to
    CANCELED when the job has `cancel_requested` or the run is CANCELED, as `_launch` does. The flag is re-read
    after the collection. Nothing is frozen, no child is unblocked and nothing is registered.
  - `GraphStore._state('CANCELED')` and `JobStore.cancel` also flag a FAILED/CANCELED graph attempt that still
    holds its lock. Since the re-review (M3 below), `JobStore.cancel` also flags one whose node is unfinished (not
    yet classified, or in a retry backoff), and that node is settled CANCELED instead of retried. The final attempt
    of a finished node is unchanged.
  - `SSHExecutor.collect_operation_result(..., cancel=True)` writes the remote `cancel` marker while there is no
    result. It is best effort: a launch directory that was never created is ignored, and the next reconcile tries
    again. `_await_result` uses the same `_send_cancel`. `collect_operation_result` has one new keyword-only
    argument; `SSHExecutor` is its only implementation.
  - The test fake `ScriptedDispatcher.reconcile` mirrors the runner's new semantics.
  - Tests:
    - `test_operation_runner.py`: `test_a_cancel_wins_over_a_late_completion_collected_by_reconcile[run|job]`,
      `test_reconcile_sends_the_cancel_to_a_worker_that_may_still_run` (the worker then stops before training: 0
      training calls) and `test_a_job_cancel_leaves_a_final_attempt_unchanged`.
    - `test_simulated_dispatch.py`: `test_a_cancel_wins_over_a_simulated_result_collected_by_reconcile`, 8 cases
      (run/job cancel, while running/once uncertain, with or without a published result).
    - `test_graph_controller.py`: `test_a_cancel_wins_over_a_late_completion_through_reconcile`, 4 cases with the
      real runner and the fake GPU host (R1's probes: a cancel while `a` runs or once it is FAILED-uncertain, then a
      lost start response and a late COMPLETED). `a` ends CANCELED and `e` is never compiled or claimed. There is
      no `model_registration` event and no `node_outputs` row, and `a` trained once and launched once. The run ends
      CANCELED, or FAILED with `e` BLOCKED after a job-level cancel. `host_world` is factored out of
      `test_end_to_end_with_the_real_attempt_runner`.
    - `test_graph_store.py`: `test_a_cancel_flags_an_uncertain_attempt_that_keeps_its_lock[run|job]`.
- **I-3, a transient failure after COMPLETED was terminal: fixed.**
  - `SSHExecutor.run_operation` raises `ExecutionUncertain` for a non-`ValueError` collection error after the
    result was accepted. `finished` is unchanged.
  - `OperationAttemptRunner._launch` keeps the lock (uncertain) for an `OSError` or `sqlite3.OperationalError`
    after an accepted COMPLETED result. `reconcile` re-raises those errors from the freeze, so the controller notes
    them and the next reconcile downloads and freezes the same launch again. Both steps are idempotent:
    hash-matched files are skipped and the freeze is content-addressed.
  - Content errors (`ValueError`: tamper, foreign or invalid output) stay terminal.
  - Tests: `test_a_transient_download_failure_after_completion_stays_uncertain` (one `sftp.get` OSError, then
    COMPLETED with 1 launch and 1 training call), `test_a_lasting_sharing_violation_while_freezing_stays_uncertain`
    (WinError 32 eleven times: 10 tries in `_launch`, then the reconcile freeze succeeds on its second try; one
    artifact) and `test_a_content_error_after_completion_stays_terminal` (`output_invalid`).
- **I-4, the split price check: fixed.** `SSHExecutor` and `SSHServerFacts` gain
  `validate_budget(gpu_count, gpu_type, total_hourly_cost_usd)`. The runner passes the pinned total, which
  `observation_fits` also compares at claim. The v1 `validate_config` computes the same per-GPU product as before
  and calls it, so legacy behaviour is unchanged. `attempt_runner._PinnedResources` is gone.
  - Tests: `test_a_pinned_total_is_compared_with_the_live_total[0.21-3|0.11-5]` (one training call, COMPLETED) and
    `test_a_live_pod_above_the_pinned_total_or_another_gpu_is_still_refused[price|count|type]` (the price is
    `nextafter(0.21, 1)`).
- **m1: fixed.** `claim_ready` and the controller's `_queued` count `GraphStore.concurrent_attempts`: active
  attempts plus the run's finished attempts that still hold a lock. Tests:
  `test_an_uncertain_attempt_holds_a_concurrency_slot` (R1's probe: no second dispatch, the other destination is
  not even opened, then both complete) and `test_an_uncertain_attempt_counts_toward_max_concurrent`.
- **m5: fixed.** `auth.credentials_configured` counts `MLFLOW_TRACKING_PASSWORD`, which MLflow sends with the
  username. A username alone is not a credential.
  - `current_auth_mode` keeps bearer/OIDC only, so the D11 tracking hash and the controller's scope are unchanged.
    The controller already pops Basic credentials.
  - `DesktopService` with `auth_mode: none` now also drops inherited Basic credentials, so a desktop without
    authentication on a local HTTP server keeps working.
  - Tests: `test_http_with_basic_credentials_is_refused[both|password]`, `test_a_username_alone_is_not_a_credential`
    and `test_basic_credentials_never_reach_a_none_mode_controller`.
- **m6: fixed.** `tracking_outbox.redact` strips URL query strings, keeping the scheme, host and path. It also
  redacts quoted `access_token`/`refresh_token`/`id_token`/`client_secret`/`token`/`password` members (JSON or
  repr). Test: `test_redact_strips_url_queries_and_json_tokens`, with Azure SAS, GCS signed URL and JSON shapes
  whose fakes are built at runtime.
- **m7: fixed.** `JobStore.reset_for_resume` refuses a graph-owned job (`GRAPH_RESUME_REFUSED`). So does
  `Factory.resume`, before it reads the v1 manifest. `claim_ready` (and the controller's queue) claim only a
  READY node's job.
  - Test: `test_a_graph_job_is_never_requeued_outside_the_controller`.
  - `test_claim_reserves_budget_atomically_and_refuses_over_budget` requeued a graph job with `reset_for_resume`.
    It now pins the refusal and requeues by SQL, so the max_attempts rule is still tested.
- **m8: fixed.** `freeze`'s cleanup uses `shutil.rmtree(..., ignore_errors=True)`. Test:
  `test_a_valid_freeze_survives_a_failing_cleanup_of_its_temporary_directory`.
- **m9: fixed.** `model_artifacts.WINDOWS_DEVICE_NAME` is the one rule for artifact paths and downloads
  (`execution._DEVICE_NAME` is the same object). It uses the stricter list: COM0–9 and LPT0–9 with the
  superscript digits, CONIN$/CONOUT$, and spaces before an extension (`NUL .txt`). Tests:
  `test_the_stricter_windows_device_names_cannot_be_model_paths` (8 names) and
  `test_names_that_only_resemble_device_names_stay_valid` (5).
- **m11, part: fixed.** `StartSettings.tracking_claim_ttl_seconds` is at least `CONTROLLER_HTTP_TIMEOUT_SECONDS`
  (60 s). This is harmless: production uses 600 s, the broker never sends settings and the CLI sets only
  `tick_seconds`. Test: `test_the_tracking_claim_ttl_covers_one_bounded_create`.
- Recorded with a reason, no code change:
  - **m4.** The store's and controller's `max_estimated_cost_usd * (1 + 1e-9)` ceiling is deliberate defence
    against float summation order. Reservations can exceed the approved total by at most 1e-9 relative (a
    millionth of a cent on a $10 plan). The planner's plain float sum stays the strict bound.
  - **m10.** Counting a failure that provably happened before `remote-attempt.json` was written is a design choice
    (D4). Only the store's own D9 settlement (`release_uncontacted`/`settle_stale_launch`, after the token
    rotation) releases an attempt uncounted. A caught failure inside `_launch` before contact (for example
    unreadable dataset bytes) is the attempt's own deterministic failure and stays counted and terminal, never a
    silent retry.
  - **m11, MergeNode weights.** The weights must sum to exactly 1 in decimal, so three equal thirds cannot be
    written; use for example 0.33/0.33/0.34. The weights are part of the plan hash and of the merge receipt's
    normalized weights; a tolerance would change that contract and the planning schema (the planning batch's
    area).
  - **m11, RunPod/SSH exclusivity keys.** A RunPod profile and an SSH profile of the same physical host get
    different keys (6A: provider labels do not prove distinct hardware). They still never run at once: the D3
    cross-provider quarantine in `JobStore._resource_blocked` blocks every claim while another provider's lock is
    held. The difference only affects D1's load ranking and status. The docs batch documents it.
- Contracts other batches may see: `collect_operation_result` has a keyword-only `cancel` argument;
  `validate_budget` is new on `SSHExecutor` and `SSHServerFacts`; `GraphStore.concurrent_attempts` is new;
  `GRAPH_RESUME_REFUSED` is new in `jobs`. `GraphStore.submit`, `workflow_runs`, `graph_planner`, the eligibility
  checks, `workflow_planning`, `hf_targets` and `tests/test_cli_graph.py` are untouched.
- Verification (this worktree, Windows, Python 3.12, `uv sync --extra dev --extra tracking --locked`):
  - The new and changed tests on the RC source: 37 failed, 14 passed. With the fixes: 51 passed.
  - The 17 affected test files before committing: 773 passed (742 s).
  - Each of the six fix commits on its own `git archive` tree: `ruff check src tests` passed, `compileall` passed,
    and the commit's own tests passed (19, 5, 6, 4 and 6; the sixth commit is the tested HEAD).
  - Full `uv run pytest -q` at the last fix commit, run 1 (beside three other agents' suites): 1803 passed,
    1 skipped, 1 failed (762 s). The failure was
    `test_acceptance_workflows.py::test_the_mock_diamond_survives_a_controller_restart_and_honours_cancel[restart]`:
    the second `serve` exited 1 after "stale controller lease" warnings, consistent with its 6 s owner lease
    expiring under load. The test then passed 3 times alone (all 3 cases each time) and once in the CI-variable
    run below.
  - Full `uv run pytest -q`, run 2, with this section in place: 1804 passed, 1 skipped (651 s). The skip is the
    opt-in real-GPU test.
  - `uv run ruff check src tests`: All checks passed.
  - `uv run python scripts/check_secrets.py`: 1173 file versions, 0 findings.
  - `git diff --check 01c8503 HEAD`: clean. All 21 changed files are `i/lf w/lf`, and the diff has 0 CR and 0
    bidi/invisible characters.
  - `GITHUB_ACTIONS=true CI=true uv run pytest tests/test_graph_controller.py tests/test_acceptance_workflows.py
    tests/test_controller_entry.py`: 138 passed (235 s).
  - Not run: the CPU numerics acceptance (root runs it at the RC), and every GPU or network step.
- **Re-review follow-ups (root's scoped re-review: ready to integrate, no Critical or Important), test first, as
  commits on top of `d462433`.** The new tests were run on `d462433` first: 17 failed for the reported
  reason, and the old `redact` was timed for M4 (below). All pass with the fixes.
  - **M1, a cancel landing during the freeze: fixed.**
    - Before, the cancel check ran before the freeze and the result was recorded after it, so such a cancel was
      lost.
    - Now `JobStore.transition(..., 'COMPLETED')` and `JobStore.reconcile_result(..., 'COMPLETED')` write CANCELED
      for a graph attempt whose job or run was canceled, in the same transaction, and return the status written.
      No window is left.
    - The runner and the simulated dispatcher use the returned status: no outputs, no cache seed. The early check
      before the freeze stays and saves a needless freeze. The scripted test fake honours the returned status.
    - Tests: `test_a_cancel_that_lands_during_the_freeze_is_never_lost` (`_launch` and reconcile × run and job; the
      cancel fires inside `freeze_operation_output`) and `test_a_cancel_during_the_simulated_freeze_is_never_lost`
      (run, job).
  - **M2, the over-broad transient set and the status: fixed.**
    - (a) `execution.CONTENT_OS_ERRORS` are FileNotFoundError, NotADirectoryError, IsADirectoryError and
      FileExistsError. After the host finished, they are content errors and final at once, as before 11.5:
      `output_invalid` from the freeze, `failed_before_submit` from the collection. `attempt_runner.local_transient`
      (freeze) and `execution.transient_error` (collection) keep the other local errors uncertain. Test:
      `test_a_missing_file_after_completion_is_final_not_uncertain`, 3 cases: a missing `adapter_config.json` from
      `_launch` and from reconcile, and a missing `artifacts.json`.
    - (b) A freeze failure in reconcile raises `jobs.OutputsNotSaved`. The controller then notes the new
      `graph_controller.OUTPUTS_NOT_SAVED`, which is in `OPERATOR_PREFIXES`, instead of `RECONCILE_UNAVAILABLE`.
      Its exact text: "the result arrived but its outputs could not be saved on this PC; free disk space, check the
      state folder's permissions, an antivirus scan or the path length; it is retried automatically, or release
      the lock to give the result up: {error}".
    - (c) In `graph_status`, an uncertain attempt with an accepted result for its launch is no longer
      `awaiting_result`. So `needs_operator` is true and the desktop's `graph_release_lock` gate
      (`needs_operator && uncertain && !awaiting_result`) offers the release, which gives a terminal
      `output_invalid`. There is no new status field. Test: `test_outputs_that_cannot_be_saved_locally_name_the_local_fix`:
      with `fixed`, the next reconcile completes it (1 launch, 1 training call); with `released`, the desktop release
      gives `output_invalid`.
    - (d) `desktop/src/wait-reasons.ts` localizes the reason (kind `operator`), and the
      `desktop/tests/graph-runs.spec.ts` case list has it. No test enforces that every constant is localized; the spec
      lists cases. `node_modules` is not installed here, so Playwright was not run. Instead a Node 26 script (type
      stripping) imported `wait-reasons.ts` and checked the localization of the constant's formatted text: kind
      `operator`, Korean text, original kept. The spec's text equals the constant.
  - **M3, a job-level cancel during a retry backoff: fixed.** `JobStore.cancel` flags a FAILED/CANCELED graph job
    while its node is unfinished. `_settle_node` then cancels a node not yet classified, and `settle_canceled_jobs`
    settles a node in its backoff CANCELED with its subtree BLOCKED.
    - Test: `test_a_job_cancel_during_a_retry_backoff_is_honoured` (before classification, and in a 300 s backoff;
      `max_attempts=2`, ticks past the backoff). Result: `a` CANCELED, `b` BLOCKED, 1 launch, run FAILED.
    - The two I-1 guards that cancelled an unclassified "final" attempt now classify it first.
  - **M4, `redact` was quadratic: fixed.**
    - On `d462433`, 20/40/80 KB of `https://a` took 0.11/0.43/1.7 s.
    - Now the configured secret values are replaced on the whole text first. Then it works on at most
      `REDACT_WINDOW` = 65536 characters (or 4 × limit). The patterns are possessive, and URL queries are stripped
      per whitespace-free token with `str.find`.
    - Test: `test_redact_is_bounded_on_long_text_without_whitespace`: six 1 MB adversarial texts, each under 0.5 s
      for both limits, with the secrets removed.
  - **M5: fixed.** `claim_ready`'s `max_concurrent` gate applies to GPU claims only. Test:
    `test_a_local_selection_is_never_held_back_by_max_concurrent` (the selection completes while the other GPU claim
    waits).
  - **Nits: fixed.**
    - `jobs.GRAPH_RESUME_REFUSED` = "a graph-owned job is never resumed by hand; to run it again, change an input and
      plan again".
    - `auth.INSECURE_TRACKING` now names `MLFLOW_TRACKING_USERNAME`/`MLFLOW_TRACKING_PASSWORD` as values to clear.
  - **Flake hardening.**
    - The `Served` helper of `tests/test_acceptance_workflows.py` takes settings. Both Served tests pass
      `SERVED = {**FAST, "lease_ttl_seconds": 60}`; the heartbeat stays 1.5 s (2 × 1.5 ≤ 60 and 3 × 1.5 ≤ 600).
    - Its failure messages include `current.json`'s `code`.
    - Neither test relies on lease expiry:
      - both stop by their stop event or a stop request, and the owner is released on the drain;
      - the second controller of the headless test is refused with `owner_held` while the first runs.
  - Commits:
    - `875837f` fixes M1.
    - `6290eea` fixes M2.
    - `6174911` fixes M3.
    - `e13f310` fixes M4.
    - `7d30f4e` fixes M5.
    - `81a58d7` fixes the nits.
    - `f405e77` is the flake hardening.
    - Each was checked on its own `git archive` tree: ruff passed, compileall passed, and its own tests passed (23,
      11, 7, 2, 8 and 5).
  - Verification:
    - Full `uv run pytest -q` at `f405e77`: 1819 passed, 1 skipped (452 s; the skip is the opt-in real-GPU test).
    - `GITHUB_ACTIONS=true CI=true uv run pytest tests/test_graph_controller.py tests/test_acceptance_workflows.py
      tests/test_controller_entry.py`: 142 passed (164 s).
    - `uv run ruff check src tests`: All checks passed.
    - `scripts/check_secrets.py`: 1247 file versions, 0 findings.
    - `git diff --check d462433 HEAD`: clean, with LF only.

## Task 11.5 integration onto PR #4 and the last control-plane fixes, 2026-10-21

**What was on PR #4.**
- In another session the user merged the branch tip `539b3af` into `codex/rc-planning-fixes` (`d187081`), added
  `4877791` (correlated selection identities during planning), pushed that to `codex/general-training-workflows` and
  opened draft PR #4.
- Hosted CI on `4877791` was green: push run 39562591090, and on the PR runs 39562598427 (CPU verification) and
  39562598501 (Windows desktop pilot).
- An installer was built from that tree (sidecar SHA256 `cd8c5bed…`) and installed into
  `dist\desktop-acceptance-030`. It does not contain the docs, UI/native or control-plane batches; it is not the release
  candidate.

**Integration (root).**
- `codex/rc-final` starts at `4877791` and cherry-picks:
  - the docs batch (7 commits);
  - the UI/native batch (15);
  - the control-plane batch (19). These include `ce26a90`, `096be4d`, `22256f7` and `97b35e2`, which its implementer
    committed just before an API billing error stopped it.
- The planning batch was already on PR #4.
- Conflicts:
  - PROGRESS sections, all kept.
  - One hunk in `desktop/src/views/graph-runs.ts`: the UI batch's `UNREADABLE_NOTE`/`HIDDEN_CHARACTERS` and the control
    batch's `RELEASE_REASON`/`NOT_SAVED_*` are both kept. `tsc --noEmit` is clean.

**Review of the four unverified commits: With fixes.**
- Important: only `os.*` functions set `winerror`.
  - `open()` (paramiko's download, the freeze copy) raises a path too long or a gone network share as a bare
    `FileNotFoundError`, so a paid result was still made final.
  - Fixed test first (`06e6ccc`): on Windows such an error is transient when its path is MAX_PATH or longer, or when
    its folder's stat fails with WinError 53/67.
- Two Minors, also fixed test first:
  - `370fe9d`: a `'` no longer ends the token that the redact window cut goes through.
  - `9f3c142`: the released state of an unsaved result no longer says that no result arrived.

**Re-review of those three: Ready Yes.** The Minor and nits it found, all fixed test first:
- `eeb4c59`:
  - The length rule applies only while Windows enforces MAX_PATH. With long paths enabled, a missing file at 260+
    characters is content, as are a `\\?\` name and a `FileExistsError`. `RtlAreLongPathsEnabled` is read once; it
    returns true on this PC.
  - The judgement never raises inside the runner's handlers.
- `a14a8d9`: a URL query is stripped to its end even past a `'`.
- `1522ca1`: the release dialog of an unsaved result says that the result is given up, recorded as `output_invalid`.

**Verification on `1522ca1`** (the code; this entry is a docs-only commit on top):

| Check | Result |
|---|---|
| `uv run pytest -q` | 1930 passed, 1 skipped (632 s; the skip is the opt-in real-GPU test) |
| `uv run ruff check src tests scripts` | All checks passed |
| `scripts/check_secrets.py` | 1294 file versions, 0 findings |
| `git diff --check` | clean against `4877791` |
| `npm run build` | passes |
| `npx playwright test` | 269 passed (3.7 min, own Vite server, `TUNING_FACTORY_E2E=1`) |
| `cargo test --locked --offline --release` | 54 passed (Rust is unchanged since the UI batch) |
| CPU acceptance (`.factory\model-ops-cpu-venv`, `verify_model_ops_cpu.py`) | 29/29, `passed: true` |

- CPU acceptance hashes:
  - `source_sha256` `f084a32d…c202`, which equals `source_hash()` of this tree;
  - `lock_sha256` `5883aae6…1437` (unchanged);
  - receipt SHA256 `38285e3a…ff92`.
- Not done yet:
  - the fresh frozen build and `verify_desktop_package.py` on it;
  - hosted CI on the pushed tip;
  - the 11.6 installer acceptance.
- No GPU, paid resource, network data or real credential was used.

## Task 11.6 installer acceptance on this PC, 2026-10-22

Receipt: [`docs/acceptance/2026-10-22-desktop-040.json`](acceptance/2026-10-22-desktop-040.json).

**After `bc3cb39`, before the acceptance.**
- Hosted CI on `bc3cb39`: the Windows desktop pilot succeeded (39575435511), but the ubuntu CPU job failed.
  - Cause: `posixpath.abspath` prefixed a `\\?\` test name, so `0d5cc13` judges such a name before making it absolute.
- `91b7bdc` adds `scripts` to the lint command in AGENTS.md. The user approved this on 2026-10-21.

**User decisions for the acceptance.**
- The app data on this PC already held a real legacy RunPod key in Credential Manager, `FACTORY_OPERATOR`, an MLflow
  address and a tutorial registration. The user chose: back up, accept, restore, and never write or forget the key.
- Both app-data folders were snapshotted and hashed (302 files), then moved aside, so the acceptance ran on a fresh
  state.

**Defect found and fixed: `b4e359a`.**
- The installed app refused a Hangul project name ("unable to parse raw data as a unicode string").
- Cause: the Rust bridge writes raw UTF-8 JSON, while the one-request sidecar read stdin as text in the cp949 code page.
  Every earlier check sent ASCII-escaped JSON.
- Fix: the request line is now decoded from stdin's byte buffer as UTF-8.
- The verifier now sends raw UTF-8 like the bridge. Against the old sidecar it fails with the app's error, and against
  the fixed one it passes.
- A subprocess test with `PYTHONIOENCODING=cp949` reproduces the code page on any OS.
- The legacy single-job paths still read some JSON with the locale code page. Their content is ASCII today; this is a
  follow-up.

**Release candidate `b4e359a`.**

| Check | Result |
|---|---|
| `pytest` | 1931 passed, 1 skipped |
| CPU acceptance | 29/29; source `2d8b667c…`, which equals the frozen receipt |
| Frozen sidecar | SHA256 `98f996f9…5023`; verifier: all 23 checks true, `gpu_started: false` |
| Installer | `LLM.Tuning.Factory_0.4.0_x64-setup.exe`, 48,200,394 bytes, SHA256 `ac6f2e25…7c02`; the `.sha256` file is one LF line |
| Hosted CI | all success: CPU verification push 39703288416 and PR 39703295195, Windows desktop pilot 39703295204 |

The NSIS 3.11 toolchain was copied from the existing local Tauri cache after the GitHub download stalled.

**Installed-app results.**
- **Upgrade from 0.3.0.** The 0.3.0 installer matches F3. A silent install of 0.3.0, then 0.4.0, into
  `dist\desktop-acceptance-040\app` gives `DisplayVersion 0.4.0`.
  - The installed bridge equals the build, and the main binary differs only by the 3-byte bundle marker.
  - The 0.4.0 footer shows, and both public settings and the saved-key flag carried over.
  - Only public settings were saved in 0.3.0, because its `settings_save` writes only non-empty secrets.
- **Native smoke.** It passed with no network or GPU, and the Hangul project was created.
  - The vault sections skipped because the key exists.
- **Background controller and flow.** These ran from an acceptance copy of the smoke whose offline guard ignores only
  `RUNPOD_API_KEY_saved`. The broker never hands the legacy key to the controller (`broker.rs:507`), and `forget` stays
  guarded.
  - The controller started and the broker read the synthetic profile key.
  - A second start was refused, and the stop dialog handled focus correctly.
  - The controller stopped, and a simulated train → evaluate run completed through it.
- **Installed sidecar.** The verifier passed on it (arguments, workflow channel, controller).
- **D21 status round trip.** The median was 3483 ms idle and 7063 ms under load.
  - No base interval change in 0.4.0: D10 already waits max(base, 2 × last call) with one call in flight.
  - Follow-up: reduce the one-file sidecar start time. WINDOWS_APP now states the 3–4 s per action.
- **Job object and EOF.**
  - The shell and the app are not in a job.
  - The controller survived the window close for 60 s.
  - After the broker was killed, the sidecar drained in about 1 s: state `absent`, `code: stopped`.
- **Installer and controller.**
  - Stopped path: the reinstall exited 0 and left the sidecar unchanged.
  - A silent uninstall removed the files and the uninstall key and kept the app data and the credential entry.
  - The running-idle interactive installer path was not observed; it needs a person at the prompt.
- **UI checks.**
  - M1 blank Korean project: pass.
  - M7 legacy pages: pass.
  - M8 escaping: pass (screenshot).
  - M9 tutorial: all 16 steps and anchors found with the practice done. The invoke hook recorded no calls, so
    "no mutating call" rests on `tutorial.spec.ts`.
  - M5, M6 and M10: partial, as listed in the receipt.
  - Not run or not verified: M2 (native file picker), M4 (in the UI), M11 (IME and hidden-window polling) and M12
    (optional).
- **Restore.** The original app data was moved back. All 302 files are byte-identical to the snapshot with no extra
  files, and the credential count is 1 before and after.
- **Left in place.** The acceptance install (0.4.0 at `b4e359a`) stays for 11.8. The user's earlier copy in
  `dist\desktop-acceptance-030` was built from `4877791` and lacks the 11.5 fixes and `b4e359a`.

**Still outstanding.**
- L1–L3.
- The running-idle interactive installer path.
- IME, hidden-window polling and Narrator.
- A clean second PC, code signing and redirected AppData.

## SSH reachability check on a borrowed RunPod Pod, 2026-10-29

A read-only check the user asked for. There is no `.env` or `RUNPOD_API_KEY` environment variable. The desktop app
has a RunPod key in Credential Manager, but it was not read. The CLI check below used the 2026-10-14 connection
profile instead.

- **Key.** The supplied ed25519 key is in OpenSSH format with no passphrase. Its public half matches, and paramiko
  loads it.
- **Pod.** `yuft63rjggvh5z` (EU-RO-1, 1 × RTX PRO 4000 Blackwell, 24,467 MiB, idle) accepted the key over both the
  RunPod SSH proxy and direct TCP. Inside the container, `RUNPOD_POD_ID`, `RUNPOD_PUBLIC_IP` and `RUNPOD_TCP_PORT_22`
  match the direct-TCP address the user gave.
- **Host key.** The ED25519 host key offered on direct TCP matches the one read inside the container over the proxy.
- **App path.** paramiko with `RejectPolicy`, as the CLI's `RemoteExecutor` uses it, connected and ran commands once
  given that pinned host key. The user's `~/.ssh/known_hosts` has no entry for the Pod, so CLI `work` would refuse the
  host. The desktop app uses its `ssh_fingerprint` setting (`PinnedHostKey`) instead, and that setting is empty.
- **Desktop MLflow.** The saved desktop settings still point at `http://127.0.0.1:5999` (operator
  `acceptance-upgrade`), where nothing listens, and no MLflow secret is saved. Reproduced with `check_connections`:
  that URI raises `MlflowException` (connection refused). `https://mlflow.example.org` without credentials returns
  the SSO login page, which raises `MlflowException` (response not valid JSON). The check stops at MLflow before
  reading the Pod.
- **CLI doctor.** `factory --state-dir <temp> --connection-file .factory/connection.json doctor --pod-id
  yuft63rjggvh5z` passed. That is the gitignored 2026-10-14 profile: OIDC, client ID `mlflow`, the secret from its
  referenced credential file. MLflow authenticated read and model-registry read passed (`mlflow_auth: oidc`). The
  RunPod API read the Pod as RUNNING, 1 × RTX PRO 4000 Blackwell, $0.57/h, network volume `m1b4qscsnl`, with the
  SSH mapping available. No MLflow write test was run.
- **Desktop check, slow MLflow failure.** The user then saved the desktop settings (13:26:39): OIDC, URI
  `https://mlflow.example.org/`, the Pod ID, the key file and the fingerprint. The saved OIDC secret and RunPod key
  equal the CLI profile's files (compared in-process, never printed). Results, each with a temporary state root:
  - The installed 0.4.0 `factory-bridge.exe` passed the `connection` action with the saved settings in 7.0 s, and
    `DesktopService.dispatch` from source passed in 2.8 s.
  - With the pre-save settings (`http://127.0.0.1:5999`), the installed bridge failed after 269.2 s, returning only
    `{"ok": false, "error": "MlflowException"}`. MLflow retries a refused connection with backoff, and `desktop.main`
    shows only the class name of non-`ValueError` exceptions.
  - The button checks only saved settings, so the slow failure came from a check against the old address.
  - Product follow-ups, not fixed: give the connection check a short MLflow retry budget, and map refused, timeout
    and HTML-login responses to fixed Korean messages.
- **Runtime prerequisites (read-only SSH).**
  - Present: `cc` (GCC 13.3), `python3 -m pip`, outbound PyPI, HF and GitHub, and driver 595.91 (CUDA 13.2).
  - No `/workspace/factory*` folder exists (the desktop runtime root is `/workspace/factory-desktop`), so the runtime
    has never been prepared on this volume. `/workspace` holds the
    Pod owner's other project folders.
- **GPU architecture risk (unverified on this GPU).**
  - The GPU's compute capability is 12.0 (sm_120). `containers/gpu-requirements.lock` pins the PyPI `torch==2.7.1`
    with CUDA 12.6 libraries. Public reports say cu126 wheels ship no sm_120 kernels and Blackwell needs cu128.
  - `desktop_runtime.check` only asserts `torch.cuda.is_available()`, so preparation and the runtime check would
    likely pass while the first CUDA kernel fails.
  - The image's own torch 2.8.0+cu128 lists sm_120, but the factory runtime does not use it.
  - Earlier GPU evidence was on H200 (sm_90). Not tested here; needs a non-Blackwell Pod, a cu128 lock with an
    architecture check, or an approved throwaway probe.
- **Remote tools.** Python 3.12.3, rsync, tar and sha256sum are present, and `/workspace` is a RunPod network-volume
  mount.
- **Not done.** Nothing was installed, transferred or started, and the Pod was not stopped. MLflow was only read, and
  HF was not contacted.

**Still outstanding.**
- A desktop "연결 검사" click after the save, and the operator label, which is still `acceptance-upgrade`.
- The user's queued desktop job `76986bce…` (QUEUED, 0 attempts, `Qwen/Qwen2.5-0.5B-Instruct` SFT) declares 1 ×
  `NVIDIA RTX A4000` at $0.50/h. Against this Pod, "실행" would first stop at the unprepared runtime, then at the
  GPU-type and price checks.
- The known_hosts entry, for CLI use only.
- Runtime preparation.
- L1–L3.

## Windows workbench refinement — 0.2.1

User selected the light Linear/Vercel direction after requesting getdesign.md references and similar open-source
research. DESIGN.md records original product rules; docs/OPEN_SOURCE_REVIEW.md records upstream links, fit and limits.
Removed the marketing hero/numbered sidebar; added compact workspace navigation, actual recent-run summary,
bundled Pretendard font, local sample inspection, same-snapshot validation guard, fetched-history filters,
nested parameter differences, reuse-as-new-plan and actual native progress visualization.

Current source checks: **74 passed, 1 skipped**; Ruff passes; UI workflows **8 passed**; TypeScript/Vite and Rust
allowlist test pass. Actual previously recovered GPU trainer_log.jsonl parsed as step 12/12 with finite loss
0.4167558252811432. This is a read of existing evidence, not a new training run.
The added tests cover duplicate/sample bounds, invalid samples, partial/nonfinite trainer records, downsampling,
missing/nested parameter differences, validation leakage, keyboard navigation, search and progress empty states.

0.2.1 NSIS installer build completed (~42.83 MiB), installed with exit code 0, and the installed app passed:
local synthetic registration, source/lock-bearing offline plan, actual Windows vault save/masked read/delete,
Ctrl+K navigation and actual-file sample inspection through the UI. The frozen engine and worker wheel
have identical Python source bytes and the GPU dependency-lock hash matches.
Packaged source commit: 7bc7171b63ddab2752a4f930ecb67d2c3d902437.
Source SHA256: 3ff133ebb8d5e507b6cf512c8a2f6c3feefe849d50b787aebe5788906a40897a.
Bundled authenticated read-only checks again read 12 MLflow runs and provider EXITED.
No GPU was started or dataset transferred. Installer removal is tested on the development Windows host;
clean second-PC acceptance and code signing are not covered.
New desktop GPU bootstrap/training/cancellation/recovery acceptance and the other limits below remain outstanding.

## Windows desktop pilot — current work

Approved Windows-only design and implementation plan are in docs/plans/2026-10-14-windows-desktop*.
Rust/Tauri UI wraps the existing Python controller. Windows Credential Manager retains API/OIDC secrets;
the sidecar receives them only through stdin. UI profiles, datasets, queue and logs stay in per-user app data.
No paid infrastructure was started and no dataset was transferred during desktop development.

Current source checks: **68 passed, 1 skipped**; Ruff src/tests passes; TypeScript/Vite production build passes;
four Playwright UI workflows pass; Rust command-allowlist test passes. A preexisting 1-second subprocess
failure assertion was flaky under simultaneous compiler load; its nonzero-exit case now allows 10 seconds
while the actual timeout case retains its 1-second deadline.

First native build produced an unsigned Windows x64 NSIS installer (~41 MiB). Native WebView2 smoke checks
verified bundled catalog, local synthetic dataset registration, offline plan with dependency lock, and actual
Credential Manager save/masked read/delete using a synthetic value (then restored prior non-secret settings).
The bundled engine authenticated to the existing MLflow server and read 12 experiment-16 runs; RunPod read-only
status confirmed EXITED. Public Hugging Face revision lookup also returned a valid immutable 40-character commit.
These were reads; no MLflow runs were created by the probes.

Final source commit **251d63e0cf3d075e819010596ac104768d0a5145** was packaged and verified:
the frozen engine's source hash equals the local package and all worker-wheel Python sources match.
Source SHA256: 7a3e8dff7b1ff5bfbb356899f73465f962c71c2e96b5d45e9da360f76150f128.
The frozen plan carries the exact GPU dependency lock. Final catalog has three epoch-based task templates.
The final NSIS installer installed into a test folder with exit code 0; the installed native app passed
catalog, synthetic local registration, offline plan and real vault masked-read checks. No system Python
was used by the installed app. This remains a test on the development Windows PC, not a clean second PC.
Final bundled read-only probes again read 12 MLflow runs and provider EXITED. Hosted CPU verification
38534264783 passed on both Windows and Ubuntu. Desktop packaging CI is tracked separately.

**Outstanding paid verification:** the new desktop runtime bootstrap and complete UI-triggered GPU
training/generation/cancel/reconcile/resume flow have not been GPU-executed. Historical CLI H200 evidence below
must not be presented as desktop acceptance. QLoRA, gated-model authentication, a clean second-PC installation,
Windows signing, large-data streaming and multi-PC scheduling remain unverified or outside this release.
The UI's existing-Pod flow has no automatic start/stop/delete controls. Assigned Pods and operator shutdown
remain required. See docs/WINDOWS_APP.md for limits and exact setup.

## Latest live verification (supersedes the earlier baseline below)

Fresh full suite after cancellation hardening: **57 passed, 1 skipped**. Ruff src/tests passes; wheel build succeeds.
The skipped pytest is the opt-in GPU case; manual paid GPU evidence is separate.

Distribution uses one installation and local state per PC, with common MLflow records. Local .env loading
supports only known settings, preserves shell environment precedence and never copies secrets into manifests.
FACTORY_OPERATOR labels common runs. The Git history/staging secret check found no known credentials or
supported token/private-key patterns across 142 file versions before the distribution commit.
Published to the private https://github.com/ParkYaeseong/llm-tuning-factory repository; see README.md for setup.
GitHub Actions run 38520574497 passed on Windows and Ubuntu for source commit 2bfc561, including
locked installation, lint, history secret scan and CPU tests. A separate clean local clone also installed
successfully and completed the synthetic CLI example without accessing paid infrastructure.

Production MLflow authentication, registry reads, successful/failed diagnostic runs and artifact upload/download
checksums pass at https://mlflow.example.org. The legacy URL redirects here. Receipts are
state/live-integration/connection-check.json and state/production-acceptance/acceptance.json.
Three synthetic CPU AI workflows completed with distinct production runs. These are infrastructure checks.

Actual RunPod REST metadata, pinned SSH and H200 SFT were exercised using synthetic data only.
Earlier 20-minute and 10-minute sessions stopped with EXITED confirmed. The latest explicitly authorized
90-minute-maximum session is recorded in .factory/gpu-session.json and ended early with EXITED confirmed.
Existing runtimes were preserved; all remote changes are under /workspace/factory-acceptance.

Real failed attempts exposed an incompatible optional torchaudio version, missing prediction dependencies,
and FP16 non-finite gradients with all-zero adapter B weights. Those attempts are FAILED and are not accepted tuning.
The Linux lock now pins matching torch/torchaudio/torchvision 2.7.1/2.7.1/0.22.1 and prediction dependencies.
BF16 is the default; the worker rejects non-finite loss/gradient metrics and writes training-health.json.
The source-hash gate also correctly rejected a stale remote worker. One provider read temporarily omitted SSH mappings.

Registered validation generation and detached result reconciliation are now implemented and CPU-tested.
Actual healthy BF16, generation, complete collection and checkpoint resume are recorded below.
Controlled mid-training process cancellation, native checkpoint resume and controller restart recovery are now verified.
QLoRA, multi-AI GPU batch, numerical reproducibility and Model Registry
version publication remain unverified. Real task quality requires authorized representative validation data.

The GPU image has not been built. Automatic approval review rejected Rancher startup with 'blocked by policy';
no alternate startup was attempted. The approved Pod uses an isolated hash-locked venv.
Source is now shared in the private repository and CPU CI passes. Preserve local state/ and the remote persistent volume.

### Earlier session evidence (before the collection follow-up)

H200 worker job 260aa618906147cf9d4da5bc92e29ebd completed two BF16 LoRA steps and native generation
for one synthetic validation example. Observed result.json was COMPLETED (engine 0.9.5);
training-health.json checked five finite statistics at global step 2. Train loss was about 0.7071;
gradient norms were 6.089 and 9.449. The downloaded adapter contains 196,608 finite, nonzero LoRA B values.
Native prediction produced a fenced JSON answer, so strict JSON validity must not be described as a quality pass.

The approved stop interrupted the large artifact download after worker completion. The overall job and MLflow
run f0c5f94e8a04426c88eb8bbbd500115c are FAILED, not end-to-end accepted. The partial adapter/predictions
are local; complete remote evidence remains on the persistent volume and needs reconciliation after a separately
authorized restart. Local receipt: state/gpu-acceptance/verified-gpu-evidence.json. GPU checkpoint recovery
was not attempted because remaining approved time was insufficient. Pod q17zwk1tbndy45 is STOPPED;
provider desiredStatus EXITED was confirmed. No further paid session was started.

The transfer code now preserves remote result/hash evidence before large downloads and downloads top-level
files before nested checkpoint directories. A regression test reproduces interrupted collection and verifies
that remote completion evidence survives. This prevents loss of the completion marker on future interrupted
transfers; it does not retroactively certify missing artifacts from this session.

### Earlier collection and saved-checkpoint restore follow-up

The user authorized one additional bounded session of at most 20 minutes at the live $5.29/hour rate.
Only synthetic data was used. The independent watcher ran before start. Pod q17zwk1tbndy45 was stopped
after roughly 17 minutes of the bounded session, and provider desiredStatus EXITED was confirmed.
The requested additional 10-minute extension had not been approved and was not used.

The original job 260aa618906147cf9d4da5bc92e29ebd was reconciled without retraining. All **40** files in its
original remote hash inventory matched locally. The adapter uploaded to MLflow was downloaded and matched
the remote inventory hash. The original run f0c5f94e8a04426c88eb8bbbd500115c is now FINISHED.
Receipt: state/gpu-acceptance/full-recovery-receipt.json. Strict JSON validity is 0 for the single fenced
synthetic answer; this is an infrastructure acceptance, not qualified task quality.

Recovery job 4a7ee3739e8a4747b9c0d25b55f813be preserved checkpoint-10 with adapter, optimizer, scheduler,
RNG and trainer state. Actual LLaMA-Factory/Transformers logs confirm loading output/checkpoint-10,
resuming at global step 10, fast-forwarding the dataloader, then finishing global step 12. The checkpoint
history prefix was preserved, and training-health.json checked 25 finite statistics. All **55** files in
the final local job inventory verify. The resumed run 27163d2c46ce4bd28fa23badee294cc5 is FINISHED.
Receipt: state/gpu-recovery/recovery-receipt.json, also uploaded to both attempt runs.

The first control-plane cancellation arrived after its GPU worker finished all 12 steps. The control run
f5979946c27e479fbdbc5b97a97ea912 is KILLED, but worker_cancellation_observed=false is explicitly recorded.
This proves native saved-checkpoint restoration, **not an actual mid-training forced-interruption test**.
A GPU-local checkpoint observer and smaller resume transfer were prepared and unit-tested for that remaining
case, but were not GPU-executed in this session. No unapproved extension or additional Pod start was used.

Collection now skips already hash-verified files and atomically replaces corrupted/partial downloads.
New attempts exclude previous completion/cancellation markers and controller receipts from upload.
Regression tests cover interrupted collection, hash-based reuse/repair and stale-marker exclusion.

### Actual mid-training cancellation and recovery — latest evidence

The user explicitly allowed more than one hour after the short earlier limits. A new **90-minute maximum**
was selected at the live $5.29/hour rate, with an independent local deadline watcher and synthetic data only.
The session ended early after **25.3 minutes**, with stop and provider desiredStatus **EXITED** confirmed.
The watcher was rearmed when the original local controller process terminated. Host-local guards cannot
enforce provider billing limits during host/process downtime; they are not provider-side billing caps.

The first real cancellation exposed a race: the mutable cancel signal was hashed while a control writer
could truncate/change it. GPU cancellation succeeded, but collection correctly rejected the changed bytes.
That attempt's immutable artifacts were subsequently recovered and its run is KILLED; its failure is retained.
The fix excludes only the root cancel signal from immutable inventories, preserves the raw remote inventory,
and creates cancellation signals exclusively without overwriting an existing signal. Legacy inventories
containing that mutable signal remain recoverable while all model/data/checkpoint hashes are still checked.
Regression tests reproduce both the mutation and SFTP v3 existing-file errors without an errno.

The corrected real trial is job **1772b42a93d94bcdab0f6b9ea0e58c8c**:

- Initial GPU worker actually published `status=CANCELED`, before a final trainer state existed.
- Complete checkpoint-10 retained adapter, optimizer, scheduler, RNG and trainer state.
- After the local control process terminated, expired-heartbeat recovery retained the GPU lease;
  reconciliation recovered the original result without duplicate training and released the lease.
- Native LLaMA-Factory resumed with `output/checkpoint-10`, retained the saved history prefix,
  and finished at global step **12**. Training health checked **25** finite statistics.
- **34** immutable files in the final remote inventory match locally. All **51** files in the final
  control-plane inventory also verify. The MLflow adapter upload/download hash matches.
- The recovered adapter contains **196,608** finite, nonzero LoRA B values.
- Canceled run **4e09a31fca8442428f44a75fd816d044** is KILLED; resumed run
  **e41e7b766172475fbdc58e74919dbc7c** is FINISHED, both in experiment 16.

The receipt state/gpu-recovery/recovery-receipt.json and actual first-worker result were uploaded to both
MLflow runs. Receipt flags worker_cancellation_observed=true and controller_restart_recovery_verified=true
distinguish this result from the earlier late control-plane cancellation. It remains synthetic-only and is
not a production model-quality qualification. Abrupt hardware/Pod loss during checkpoint writing was not tested.

Git LF checkout rules now preserve exact source/lockfile bytes across Windows and Linux. Actual index checkouts
with core.autocrlf=true and false matched the control-plane source hash and all three dependency file hashes.

## Earlier CPU baseline (historical)

## Delivered implementation

- Strict AI configuration, conservative Qwen/Llama3 template catalog and pinned model/tokenizer revisions.
- Immutable dataset snapshots, data validation/export permission, content hashes and AI revision history.
- Resolved manifests containing source/commit, translated YAML hash and dependency lock hashes.
- TrainingBackend interface and LLaMA-Factory LoRA/4-bit QLoRA SFT adapter; real CLI subprocess delegation.
- Local mock execution and RunPod REST/SSH adapters, isolated remote attempts, artifact checksums and worker-side deadlines.
- Transactional SQLite queue, priority/FIFO, duplicate prevention, independent concurrent-job/GPU limits and exclusive Pod leases.
- Bounded transient retries, active cancellation, failed-attempt history, checkpoint validation and conservative restart recovery.
- MLflow tracking/artifact adapter and approval-pending model-candidate registration; separate local candidate metadata.
- JSON validity, classification accuracy/macro F1, extraction exact accuracy/micro F1 and metric deltas.
- CLI, three AI examples, dummy fixtures, pinned control/GPU dependency locks, GPU image recipe and CPU CI definition.
- Explicit paid-allocation approval, managed ownership, live price checks and persistent-volume-preserving stop/watchdog.
- Canonical existing-project reuse: unchanged ShareGPT JSONL ingestion/mapping, configurable Keycloak bearer refresh,
  and the pure partial-credit extraction helpers. Source paths/commit/hashes are recorded in EXISTING_ASSET_PROVENANCE.json.

## Actual verification

Final full suite: **36 passed, 1 skipped**. The skipped case is the opt-in real GPU integration test.
`uv run ruff check src tests` passes. `uv sync --python 3.12 --extra dev --extra tracking --locked` succeeds.
The control-plane dependency graph contains 59 installed packages. GPU dependencies were resolved into a Linux hash lock,
not installed. `uv build --wheel --out-dir dist` succeeds and produces
dist/llm_tuning_factory-0.1.0-py3-none-any.whl; this is not a GPU image build.

Coverage includes configuration rejection, tamper detection, invalid data, generated LoRA/QLoRA YAML,
real CPU subprocess exit/timeout/cancellation, checkpoint completeness, atomic parallel claims/submissions,
GPU budget vs concurrency, recovery retaining leases, retry exhaustion, simulation safety, candidate metadata,
official REST request shapes through HTTP mocks, managed-budget lifecycle, SFTP artifact transport, isolated remote attempts,
remote interruption uncertainty, worker integrity gates, evaluator metrics and real local MLflow logging/artifact retrieval.
Existing ShareGPT compatibility, mocked OIDC grant/token refresh and the reused KBF partial-credit scorer are tested.
Mock remote/adapter fixtures are test-only bytes, not trained models or GPU evidence.

Additional subprocess CLI acceptance used state/cli-acceptance-v2 with the real installed MLflow client and temporary local
file tracking backend (explicit MLFLOW_ALLOW_FILE_STORE=true). Verified:

- Default run produces a dry-run plan without training.
- Three AI configurations are submitted and complete as simulations with three distinct MLflow run IDs.
- Each result has simulation=true and quality_status=not_evaluated; only pipeline_completed is measured.
- A deliberate repeated submission preserves the same resolved manifest hash and has a new job/run identity.
- Comparing these repeated simulations reports pipeline_completed delta=0; this is not a model quality result.

The local acceptance receipt is state/cli-acceptance-v2/acceptance.json (ignored by Git). No external datasets were sent,
no Pod was created/changed, no paid GPU ran, and no operating service was reconfigured.

## Phase and scenario status

| Item | Status and evidence |
|---|---|
| Phase 1 | Implemented; CPU configuration/registry/manifest tests and CLI plans pass |
| Phase 2 | Adapter implemented and subprocess contract tested; actual LoRA/QLoRA training unverified |
| Phase 3 | RunPod REST/SSH/lifecycle implemented with mocks; real image/Pod training and recovery unverified |
| Phase 4 | Real local MLflow tested; remote server, real adapter Model Registry and task-generation evaluation pending |
| Phase 5 | Queue/retries/idempotency/concurrency tested on one Windows control-plane host |
| Phase 6 | Local tests, package and docs provided; GitHub-hosted CI and GPU fault tests not executed |
| Scenario A | Simulation + local MLflow pass; full RunPod → task evaluation → registry remains unverified |
| Scenario B | Three simulated AIs + independent real local MLflow runs pass; real GPU batch unverified |
| Scenario C | Failure/retry, CPU cancellation, mock checkpoints and conservative restart tests pass; actual GPU resume unverified |
| Scenario D | Same-input manifest/hash reproduction passes; numeric training reproducibility unverified |
| Scenario E | Approval/budget/GPU constraints tested with mocks; real billing behavior unverified |

**The full GPU acceptance criteria have not been met.** No real model performance is reported.

## Known limits and next work

1. Start Docker and build/test the GPU image. Its dependency lock is satisfiable but CUDA/runtime compatibility is unverified.
2. Connect an approved existing Pod and approved non-sensitive dataset; check the exact worker/source/lock hash,
   SSH mapping, network volume, price and GPU metadata. Run a tiny real SFT and verify complete local artifact recovery.
3. Connect the intended authenticated MLflow server; verify artifact credentials, remote model-version registration,
   failed-run persistence and compatibility tags with a real adapter.
4. Supply registered validation data and task evaluation criteria. The initial worker does SFT and collects native loss;
   automatic adapter generation of task predictions is not implemented. Task metrics without predictions stay unevaluated.
5. Exercise real forced interruption, checkpoint resume, cancellation, capacity shortage and managed Pod stop/watchdog.
   Automatic reconnection and artifact retrieval from a previously detached interrupted attempt are not implemented;
   currently reconcile its network-volume directory manually before releasing its GPU lease.
6. Supervise guard independently and configure provider billing alerts. Software deadlines do not cap provider billing
   during guard/control-plane downtime. Storage/egress/provisioning/idle overhead are outside estimates.
7. PostgreSQL, multi-host scheduling, multi-user API/RBAC, other training engines and model serving are intentionally absent.
   SQLite is the supported initial single-host metadata backend. Model approval and deployment remain separate human steps.

No known failing CPU checks at handoff. Preserve state/ and network volumes before any retention/cleanup decisions.
Implementation commits are in the local Git repository; nothing was pushed remotely.

## Codex finalization in the Claude worktree — 2026-10-21

Continued from `D:\llm-tuning-factory-wt115`, branch `codex/rc-planning-fixes`. Merged the companion hosted-CI
help-format test correction `539b3af` without rewriting the existing work (`d187081`). Fresh Python 3.12.12
locked sync and Ruff pass. The history/index secret scan checked 1,266 versions with zero findings.

Fresh worktree browser verification: **254 Playwright tests passed** (3.0 minutes); TypeScript/Vite production
build passed. Pre-fix CPU numerical acceptance passed **29/29** using the existing hash-locked CPU environment;
it will be rerun after the final source correction. No model was downloaded or GPU used.

The independent final fix review found one remaining planning issue: repeated uses of the same multi-candidate
selection, including a single-candidate selection alias, were treated as independent possible models. Runtime
duplicate guards still refuse them, but only after upstream work. A narrow test-first planner correction and
workflow-channel regressions passed: **230 tests** in 278.53s. Seven intended RED failures were observed before the fix; independent conditional choices remain accepted. LF-only normalization followed the covering run; Ruff and diff checks passed. Scoped re-review remains pending. Initial full-suite runs were deliberately interrupted before
source changes; they are not reported as passing. Fresh full-suite and frozen/installed acceptance remain pending.

The PC has a registered 0.3.0 acceptance install; no app/controller was running at preflight and the factory's
Credential Manager service had zero entry-name matches. Existing application data must be preserved. No actual
BOP/SSH/RunPod/MLflow/HF live check, paid resource, dataset transfer, installation, merge to master or release
publication has been performed in this finalization session.
