# Qwen3.5·Gemma 4 텍스트 학습 (2단계)과 1단계 후속 — 설계안

작성일: 2026-10-30 · 상태: 사용자가 프로젝트 전체를 승인했다(추가 승인 단계 없음). 이 문서는 질문이 아니라 결정을 적는다.

이 문서는 설계안이다. 아래 기능은 아직 구현되거나 GPU에서 검증되지 않았다.
브랜치 `claude/phase2-models`는 PR #5(`claude/gpu-compat-runtime`, 0.5.0) 위에 쌓는다. 결과는 데스크톱 앱 0.6.0이다.

## 1. 배경

[1단계 설계](2026-10-29-gpu-compat-runtime-design.md) §7은 2단계를 "Qwen3.5·Gemma 4 텍스트 학습의 GPU 검증과
그 과정에서 드러나는 문제 수정"으로 예고했다. 2026-10-30 조사에서 다음을 확인했다(하위 에이전트가 Hugging Face와
고정 엔진 소스를 읽어 검증한 사실이다).

### 1.1 모델 (Hugging Face, 모두 apache-2.0, `gated=false`, 로그인 없이 config를 읽을 수 있음)

| 계열 | 저장소 | `model_type` / 클래스 | 전체 매개변수(safetensors) | 텍스트 매개변수 | hidden · 층 | 비고 |
|---|---|---|---|---|---|---|
| Qwen3.5 | `Qwen/Qwen3.5-0.8B`(+`-Base`) | `qwen3_5` / `Qwen3_5ForConditionalGeneration` | 873,438,784 | 752,393,024 | 1024 · 24 | 임베딩 공유(tied) |
| | `Qwen/Qwen3.5-2B`(+`-Base`) | 〃 | 2,274,069,824 | 1,881,825,088 | 2048 · 24 | tied |
| | `Qwen/Qwen3.5-4B`(+`-Base`) | 〃 | 4,659,865,088 | 4,205,751,296 | 2560 · 32 | tied |
| | `Qwen/Qwen3.5-9B`(+`-Base`) | 〃 | 9,653,104,368 | 8,953,803,264 | 4096 · 32 | untied |
| | `Qwen/Qwen3.5-27B` | 〃 | 27,781,427,952 | 26,895,998,464 | 5120 · 64 | untied. 27B-Base는 확인되지 않아 넣지 않는다 |
| | `Qwen/Qwen3.5-35B-A3B`(+`-Base`) | `qwen3_5_moe` / `Qwen3_5MoeForConditionalGeneration` | 35,951,822,704 | 34,660,610,688 | 2048 · 40 | MoE 256 전문가, 토큰당 8 |
| Gemma 4 | `google/gemma-4-E2B-it`, `google/gemma-4-E2B` | `gemma4` / `Gemma4ForConditionalGeneration` | 5,123,178,051 | 4,647,449,891 | 1536 · 35 | 층별 입력 임베딩 256 |
| | `google/gemma-4-E4B-it`, `google/gemma-4-E4B` | 〃 | 7,996,156,490 | 7,518,069,034 | 2560 · 42 | 층별 입력 임베딩 |
| | `google/gemma-4-26B-A4B-it`, `google/gemma-4-26B-A4B` | 〃 | 25,805,936,206 | 25,233,141,790 | 2816 · 30 | MoE 128 전문가, top-8 |
| | `google/gemma-4-31B-it`, `google/gemma-4-31B` | 〃 | 31,273,088,876 | 30,697,345,340 | 5376 · 60 | |

- Qwen3.5: `text_config`(`qwen3_5_text`)와 `vision_config`가 있고 오디오는 없다. vocab 248320, 최대 위치 262144.
  최상위 config에는 `hidden_size`·`num_hidden_layers`·`vocab_size`가 없고 `text_config`에만 있다.
- Gemma 4: `text_config`(`gemma4_text`)와 비전 config가 있고, 오디오 config는 E2B·E4B에만 값이 있다. vocab 262144, 임베딩 공유.
  최대 위치는 E 모델 131072, 나머지 262144. `text_config.hidden_size_per_layer_input`은 E 모델 256, 26B-A4B·31B 0이다.
  `num_experts`는 26B-A4B만 128이고 나머지는 null이다.
- 텍스트 매개변수는 safetensors 헤더의 `model.language_model.*`+`lm_head.*` 합이다(2026-10-30, 하위 에이전트가 헤더만 읽어 측정).
  나머지는 비전(+투영), 오디오(E 모델 약 3억), Qwen3.5 `mtp.*`(0.8B 2,045만 ~ 35B 8.4억)다.
- 저장소 파일: Qwen3.5에는 `preprocessor_config.json`·`video_preprocessor_config.json`이 있고, Gemma 4에는
  `processor_config.json`이 있다. Qwen3.5 샤드 이름은 `model.safetensors-0000N-of-0000M.safetensors`로 표준과 다르다.
- 텐서 이름: Qwen3.5 언어 모델의 3차원 텐서는 `linear_attn.conv1d.weight`뿐이다. MoE 전문가는 두 계열 모두 3차원으로
  합쳐져 있다. Gemma 4 비전·오디오 선형층 가중치는 `.linear.weight`로 끝난다.

### 1.2 고정 엔진

주 학습 환경은 LLaMA-Factory 0.9.5(transformers ≥4.55, ≤5.6.0 요구), transformers 5.6.0, peft 0.18.1, trl ≤0.24다.
PPO 환경(transformers 4.52.4)에는 `qwen3_5`·`gemma4`가 없다.

- **transformers 5.6.0.** `AutoModelForCausalLM`은 `qwen3_5`를 텍스트 전용 `Qwen3_5ForCausalLM`으로 만든다.
  이때 `language_model` 접두사를 떼고 `model.visual.*`·`mtp.*`를 무시한다. image-text-to-text는
  `Qwen3_5ForConditionalGeneration`이다.
  - Qwen3.5 모듈: `model.visual`(patch_embed, pos_embed, blocks, merger)과 `model.language_model`.
  - 텍스트 층은 `linear_attn`(Gated DeltaNet: in_proj_qkv, in_proj_z, in_proj_b, in_proj_a, out_proj와 Conv1d)과
    네 번째 층마다 `self_attn`(q/k/v/o_proj)을 섞는다. mlp는 gate/up/down_proj다.
  - MoE 전문가는 3차원 Parameter라 LoRA 대상이 아니다. shared_expert의 선형층은 대상이다.
  - fla·causal-conv1d는 고정 파일에 없어 느린 torch 대체 경로를 쓴다.
- **Gemma 4.** `AutoModelForCausalLM`과 image-text-to-text가 모두 `Gemma4ForConditionalGeneration`이다.
  - 모듈: `model.vision_tower`, `model.audio_tower`, 투영 `model.embed_vision`·`model.embed_audio`, `model.language_model`.
  - 비전·오디오 선형층은 `Gemma4ClippableLinear(.linear)`로 감싸여 있다. 텍스트는 q/k/v/o, gate/up/down이다.
  - E 모델은 `per_layer_input_gate`·`per_layer_projection`·`per_layer_model_projection`과 큰 층별 임베딩을 더한다.
    MoE는 `router.proj`를 더한다.
- **LLaMA-Factory 0.9.5.**
  - 이 모델들을 `AutoModelForImageTextToText`로 불러온다(`model/loader.py:160`).
  - 템플릿(`data/template.py`): `qwen3_5`(추론), `qwen3_5_nothink`(일반), `qwen3_6`; `gemma4`(이미지·동영상)와
    `gemma4n`(이미지·동영상·오디오)는 둘 다 추론 템플릿이다(생각 단어 `<|channel>thought\n`/`<channel|>`, 시스템에 `<|think|>`).
  - `enable_thinking`의 기본값은 참이다(`hparams/data_args.py:124`). 답에 생각이 없으면 빈 생각 블록을 학습한다.
  - `constants.py`는 Qwen3.5를 `qwen3_5` 템플릿으로, Gemma 4는 -it만 등록한다(E2B·E4B → `gemma4n`, 26B-A4B·31B → `gemma4`).
  - `visual.py` 복합 모델 표: gemma4 투영 `model.embed_vision`/`model.embed_audio`, 비전 키 `vision_tower`/`audio_tower`;
    qwen3_5(_moe) 투영 `model.visual.merger`, 비전 키 `visual.pos_embed`/`patch_embed`/`blocks`.
  - 동결 기본값: 비전·투영 동결, 언어 모델 학습. `find_all_linear_modules`는 투영과 동결된 비전을 뺀다.
    어댑터는 전체 이름(`model.language_model.layers.N...`)을 기록한다.
  - 텍스트만 있는 배치: 대조기(collator)가 64×64 빈 이미지를 넣는다(gemma4n은 빈 오디오도).
    mm_plugin은 processor와 이미지·동영상 처리기를 요구한다. PT는 일반 LM 대조기를 쓴다. 내보내기는 processor를 저장한다.

### 1.3 앱 결함(감사 결과, 각 위치를 계획 작성 시 다시 확인함)

1. **processor 파일을 받지도 복사하지도 않는다.** `operation_runner.py`의 `HF_ALLOW_PATTERNS`(`model_artifacts.TOKENIZER_FILES`에서 옴)와
   `model_ops.py`·`operation_outputs.py`·`operation_dispatch.py`·`workflow_planning.py`·`simulated_dispatch.py`의 파일 목록에
   `preprocessor_config.json`·`video_preprocessor_config.json`·`processor_config.json`·`chat_template.json`이 없다
   (`chat_template.jinja`는 이미 `TOKENIZER_FILES`에 있다). LLaMA-Factory가 "Processor was not found"로 멈춘다.
   같은 허용 목록의 `model-*.safetensors`는 Qwen3.5 샤드 이름에 맞지 않아 v2 실행이 가중치를 받지 않는다.
2. **LoRA 대상 검사**(`target_inspection.py:71`)가 `AutoModelForCausalLM.from_config`를 쓴다. 하위 에이전트가 모델 작업 CPU 환경에서
   확인한 결과: Qwen3.5는 `'Qwen3_5Config' object has no attribute 'vocab_size'`로 실패한다. Gemma 4는 비전의
   `Gemma4ClippableLinear`(`in_features` 없음)에서 실패하고, 고쳐도 비전·오디오·투영 선형층을 넣는다.
3. **병합·확정**(`model_ops.py:465-468` `_load_fp32`)이 `AutoModelForCausalLM`을 쓴다. Qwen3.5는 텍스트 전용 클래스로 불러와
   비전 텐서가 빠지고(작은 모델에서 77개 중 56개), 어댑터 이름(`model.language_model.*`)이 맞지 않으며, 확정의 키 집합 검사
   (`model_ops.py:824-826`)가 실패한다. 실제 체크포인트의 `mtp.*`도 불러오지 않으므로 같은 검사가 실패한다.
4. 대상 집합 비교가 섞여 있다. 계획(`graph_planner.py:417-422`, `simulated_dispatch.py:425-430`)은 잎 이름을 쓰지만, 복합 모델
   어댑터는 전체 이름을 기록한다. 그래서 산출물 어댑터와 새 어댑터의 비교가 틀린다. `check_continuation_targets`(`model_ops.py:852-864`)는
   증거가 언어 모델만 담으면 전체 이름으로도 맞게 동작한다.
5. **설정 변환**(`training.py:121-187`, 예측 230-251·266-280, 보상 평가 304-322)이 `freeze_vision_tower`·
   `freeze_multi_modal_projector`·`freeze_language_model`·`enable_thinking`을 쓰지 않는다.
6. **freeze 선택지**(`config.py:44`)의 `self_attn`은 Qwen3.5 0번 층(`linear_attn`)에 없어 "Module self_attn is not found"로 실패한다.
   임베딩을 공유하는 모델의 `freeze_extra_modules=lm_head`도 실패한다.
7. **학습 단계**: `model_hub.py:19-20,26-27`이 이 구조에 SFT만 허용한다.
8. **메모리 추정이 허브 모델을 다루지 않는다.** `desktop.py:166`은 기본 목록만 찾고, `ModelProfile`(`model_hub.py:39-50`)은
   층 수만 저장하며, `estimate_memory`(`gpu_compat.py:65-89`)는 밀집 모델을 가정한다.
9. `operation_outputs.py:227-229`의 vocab 비교가 최상위 `vocab_size`(복합 모델은 없음)를 읽어 `None == None`으로 늘 통과한다.
10. `hf_targets.py:75`가 이 구조의 헤더 기반 대상 증거를 거부한다. 게이트를 풀어도 `target_evidence`가 3차원 conv1d·5차원 비전
    텐서와 `mtp.*`에서 거부한다. 그래서 이어 학습·병합·확정이 `workflow_planning.py:1187-1209`(D15)에서 거부된다.

모델 작업 시험 환경: 주 개발 환경에는 torch가 없다(`test_cpu_script_refuses_to_run_without_torch`가 이를 강제한다). 실제 수치
검증은 고정 CPU 환경 `.factory/model-ops-cpu-venv`(torch 2.7.1+cpu, transformers 5.6.0, peft 0.18.1)에서
`scripts/verify_model_ops_cpu.py`로 한다. 이 환경의 transformers는 `qwen3_5`·`qwen3_5_moe`·`gemma4`를 알고, 작은 무작위 config로
CPU forward가 된다(2026-10-30 확인).

### 1.4 1단계 후속(PROGRESS "남은 일")

- **F2 SSH keepalive.** 긴 probe(최대 900초)·결과 대기 중에 유휴 연결이 끊길 수 있다.
- **F3 GPU 서버 요금.** 호환성 판정이 SSH 서버 요금을 보지 않는다. 지금은 원격 접속 뒤 영어
  `claimed job price exceeds approved resource budget`(`desktop_runtime.plan_server_destination`)로만 거부된다.
- **F1 이전 학습 환경 정리.** 앱 소스가 바뀔 때마다 `runtime/<소스 해시 16자>`(5–12 GB)가 새로 생기고 지워지지 않는다
  (`ls9ua5ymse`에 4벌). 사용자 볼륨이 차는 원인이다.

## 2. 결정

| 항목 | 결정 |
|---|---|
| 출시 | 데스크톱 앱 0.6.0. Python 패키지 버전은 그대로 |
| 작업 순서 | 작고 독립적인 F2 → F3 → F1, 그다음 2단계 엔진 작업, 버전·문서, 빌드·전체 검증, GPU 인수, 검증 결과 반영, 화면 끝까지 시험, 최종 빌드·재설치·PR |
| 모델 목록 | 1.1의 저장소를 모두 넣는다(지시형과 Base, Qwen3.5-27B-Base 제외). `gpu_verified: false`, `verified_capabilities: []`로 시작하고 GPU 인수 결과로만 바꾼다 |
| 템플릿 | Qwen3.5 → `qwen3_5_nothink`. Gemma 4 E2B·E4B → `gemma4n`, 26B-A4B·31B → `gemma4`. Base 모델도 같은 계열 템플릿 |
| 복합 구조 동결 | `qwen3_5`·`qwen3_5_moe`·`gemma4` 학습 설정에 `freeze_vision_tower: true`, `freeze_multi_modal_projector: true`, `freeze_language_model: false`를 쓴다 |
| 추론 템플릿 | `qwen3_5`·`qwen3_6`·`gemma4`·`gemma4n`이면 학습·예측·평가 설정에 `enable_thinking: false`를 쓴다 |
| 기존 구조 | 설정 변환 결과가 바이트 단위로 같다. `tests/test_model_inputs.py`의 고정 다이제스트를 바꾸지 않는다 |
| 학습 단계 | 새 구조는 sft·dpo·kto·pt 허용. rm은 GPU 인수에서 값 헤드가 동작함을 보일 때까지 한국어로 거부. ppo는 한국어로 거부 |
| freeze | 구조에 없는 학습 모듈(`qwen3_5*`의 `self_attn`, `self_attn,mlp`)과 임베딩 공유 모델의 `lm_head` 추가 모듈은 한국어로 거부 |
| 메모리 | 가중치는 전체 매개변수(비전·오디오 포함), 학습 상태·옵티마이저는 텍스트 매개변수, 활성값은 `text_config` 크기로 추정. 허브 모델도 크기를 알면 추정한다 |
| 결함 수정 | 1.3의 1(샤드 이름 포함), 2, 3, 9를 고치고, 병합·확정·이어 학습에 필요한 만큼 4와 10을 고친다. 5–8은 위 결정으로 고친다 |
| 복합 LoRA 대상 규칙 | `model.language_model.` 아래 Linear만, 어댑터에는 전체 이름. 비교는 잎 이름 집합 |
| 병합 실패 시 | 복합 구조의 병합이 CPU 시험에서 올바름을 보이지 못하면, 복합 구조의 병합·확정·이어 학습을 한국어로 거부하고 PROGRESS에 기록한다 |
| GPU 인수 | 공장 소유 A40 Pod(CA-MTL-1, 네트워크 볼륨 `cys1ne5znp`, `ManagedPods`), 합성 데이터만, MLflow 끔 |
| 화면 끝까지 시험 | 설치된 앱을 CDP로 조작하는 스크립트. 사용자 Pod `amdva0lurw9rv1`을 시험 동안만 켜고 끝나면 멈춘다(사용자 요청) |

## 3. 범위

포함한다.

1. F2·F3·F1(4.1–4.3).
2. 모델 목록·허브 프로필·메모리 추정(4.4–4.5).
3. 학습 단계·방법 제한과 한국어 거부(4.6).
4. 설정 변환의 복합 구조 동결·생각 끄기(4.7).
5. processor 파일 수급·복사·해시(4.8).
6. LoRA 대상 검사·헤더 증거·병합·확정·이어 학습·vocab 검사(4.9–4.11).
7. 0.6.0과 문서(4.12), GPU 인수(5.2), 화면 끝까지 시험(5.3).

제외한다.

- 이미지·오디오·동영상 데이터 학습. 텍스트 데이터만 다룬다. 비전·오디오는 항상 동결한다.
- PPO(전용 환경에 이 구조가 없다), OFT(1단계 허브 정책 유지), 여러 GPU 분산 학습.
- Qwen3.5 MTP(`mtp.*`) 가중치 학습. transformers가 불러오지 않는다.
- fla·causal-conv1d 빠른 커널. 고정 파일을 바꾸지 않는다(느린 경로를 GPU 인수에서 측정만 한다).
- 기존 기본 목록 모델에 `tie_word_embeddings` 정보를 더하는 일. 새 항목에만 넣는다.
- `gemma3`(허브의 기존 복합 구조) 동결 키. SFT 전용·미검증 상태 그대로 둔다.

## 4. 구조

### 4.1 F2 SSH keepalive — `execution.py`

- `SSH_KEEPALIVE_SECONDS = 30`과 `keep_alive(client)`를 둔다. `client.get_transport()`가 있으면 `set_keepalive(30)`을 부른다.
- 연결 직후 세 곳에서 부른다: `desktop_runtime.connection`의 GPU 서버 연결과 RunPod 연결, `SSHExecutor.__init__`의 자체 연결.
- `scripts/`의 승인 세션 도구는 짧은 명령만 실행하므로 고치지 않는다.

### 4.2 F3 GPU 서버 요금 — `gpu_compat.evaluate(..., server=None)`

- `server`는 `{"hourly_cost_usd": float | None}`이다. GPU 서버일 때만 넘긴다.
- 서버 요금은 `observation is None` 조기 반환보다 **먼저** 판정한다. 그래서 GPU 정보가 없어도(제출 시) 판정된다.
- RunPod 규칙과 공유 함수를 쓴다.
  - 요금 > 작업의 GPU당 요금 × GPU 수 → `PRICE_OVER_BUDGET` 차단(기존 문구).
  - 비었거나 숫자가 아니거나 음수·무한대 → `PRICE_UNKNOWN` 차단. 서버 문구는 연결 설정에서 0 이상을 입력하라고 안내한다.
  - 0은 서버에서 허용한다(내부 서버). RunPod는 지금처럼 0 이하를 `PRICE_UNKNOWN`으로 거부한다.
- `gpu_servers.server_hourly_cost(settings)`는 저장된 설정에서 요금만 읽는 오프라인 함수다. 예외를 내지 않고 잘못된 값은 `None`으로 돌려준다
  (계획 화면의 대체 경로가 다시 예외를 내지 않도록).
- `DesktopService.compatibility`는 GPU 서버일 때 `server=`를 넘긴다. 계획 카드에 서버 요금 초과가 "실행 시 거부될 항목"으로 나온다.
- `submit`은 서버의 오프라인 거부(데이터 전송 허용 확인) 뒤, 서버 관측(SSH) 전에
  `refusal(compatibility(manifest["config"], None, "execute"))`로 한국어 거부를 낸다. 기존 `validate_budget`은 안전장치로 남긴다.

### 4.3 F1 이전 학습 환경 정리

**사용 표시.** 학습 프로세스가 쓰는 동안 그 학습 환경을 지우지 않도록 공유 잠금을 잡는다.
- `SSHExecutor(..., runtime_lock_file=None)`. 값이 있으면 v1·v2 실행 줄이
  `nohup flock -s <runtime>/in-use.lock env … python -m tuning_factory.worker …`가 된다. 값이 없으면 지금과 같다.
- `desktop_runtime.executor_for`는 두 실행 대상 모두 `runtime_lock_file=<runtime>/in-use.lock`을 넘긴다.
- `desktop_runtime.prerequisites()`는 C 컴파일러와 함께 `flock`도 요구하고, 없으면 영어 고정 문구로 알린다
  (RunPod 설치 스크립트의 `command -v flock` 무음 실패를 대신한다).

**정리.** 고정 Python 소스 `desktop_runtime.PRUNE_SOURCE`를 실행한다(`gpu_servers._LINUX_PROBE`와 같은 방식, 인자는 따로 인용한
검증된 원격 루트와 현재 16자리 소스 해시뿐).
- 별도 SSH 명령이 아니라 `install_runtime`의 설치 스크립트 안에서 실행한다. 이번 학습 환경의 `prepare.lock`(`flock -n 9`)을 잡은
  직후다. prepare 잠금은 그 스크립트가 도는 동안만 유지되기 때문이다.
- 스크립트 출력의 `TF_PRUNE` 줄은 `prune_report(output)`이 읽는다.
- 후보: `<root>/runtime/` 아래에서 이름이 `^[0-9a-f]{16}(-ppo)?$`이고 심볼릭 링크가 아닌 실제 디렉터리.
  현재 소스 해시의 `<h16>`·`<h16>-ppo`는 후보가 아니다.
- 다음을 모두 만족할 때만 지운다.
  - 후보의 `in-use.lock`과 `prepare.lock`에 비차단 배타 `flock`을 잡을 수 있다(쓰는 학습·준비가 없다).
    잡은 잠금은 이름 바꾸기가 끝날 때까지 쥔다.
  - `jobs/*/*` 아래에 그 학습 환경을 쓰는 살아 있는 시도가 없다. 살아 있는 시도는 `worker.log`가 있고 `result.json`이 없으며,
    `manifest.json`의 `source_sha256[:16]`이 후보 해시와 같고, 지금 < 시도 디렉터리 수정 시각 + 제한 시간 + 3600초인 시도다.
    제한 시간은 v1 `config.resource_profile.timeout_seconds`, v2 `operation.resources.timeout_seconds`다.
    잠금 표시가 없던 이전 앱 버전의 실행 중 작업도 이 검사로 보호한다.
  - 끝나지 않은 시도의 `manifest.json`을 읽을 수 없으면 **아무것도 지우지 않는다.**
- 지우는 방법: `runtime/.trash-<이름>-<pid>`로 이름을 바꾼 뒤 `shutil.rmtree`. 남은 `.trash-*`도 지운다.
- `uv-cache/`, `python/`, `hf-cache/`, `model-cache/`, `jobs/`는 절대 건드리지 않는다.
- 결과를 한 줄 `TF_PRUNE {"removed": [{"name", "bytes"}], "kept_in_use": [...], "free_before", "free_after"}`로 출력한다
  (`shutil.disk_usage`, POSIX에서 statvfs).
  실패는 준비를 멈추지 않는다(`cleanup_error`). 결과는 `preparation["cleanup"]`과 `runtime()` 보고의 `cleanup`에 넣는다.
- 승인·안내 문구(RunPod "학습 환경 준비", 실행 승인의 설치 안내, GPU 서버 `prepareApproval`, 준비 중 안내)에
  정리 사실을 적고, 준비가 끝나면 지운 개수·확보한 용량을 보여 준다.

### 4.4 모델 목록과 허브 프로필

**구조 정책은 `model_hub.ARCHITECTURES` 한 곳에 둔다.**
- `qwen3_5`·`qwen3_5_moe`·`gemma4`: 단계 `["pt", "sft", "dpo", "kto"]`, 방법 `["lora", "qlora", "full", "freeze"]`.
- `COMPOSITE_ARCHITECTURES = {"qwen3_5", "qwen3_5_moe", "gemma4"}`.
- `gemma4` 허용 템플릿은 `{"gemma4", "gemma4n"}`. 허브 조회는 `text_config.hidden_size_per_layer_input`이 0보다 크면(E 모델)
  `gemma4n`을 고른다.

**`ModelProfile`에 선택 필드를 더한다:** `parameters_total`, `hidden_size`, `moe`, `tie_word_embeddings`.
- 값이 `None`이면 직렬화에서 뺀다. 그래서 이미 저장된 프로필과 그래프 계획의 바이트·다이제스트가 그대로다.
- 허브 조회가 채운다.
  - `parameters_total`: 모델 API의 `safetensors.total`.
  - `hidden_size`·`hidden_layers`: `text_config`(없으면 최상위).
  - `moe`: `text_config.num_experts`가 양의 정수이면 참(Gemma 4는 MoE가 아니어도 키가 null로 있다).
  - `tie_word_embeddings`: 최상위 → `text_config`(불리언 값만) → 둘 다 없으면 Gemma 계열(`gemma*`)은 참(transformers
    기본값), 그 밖의 구조는 모름(`None`, 직렬화에서 빠진다). 모름이면 tied lm_head 거부(4.6)를 하지 않는다.
  - 값이 형식에 맞지 않으면(불리언·문자열·소수, 0 이하, 범위 밖, `safetensors`가 객체가 아님) 그 값만 모름으로 두고
    등록은 막지 않는다.
  - 텍스트 매개변수는 허브 조회로 얻지 못한다(가중치 헤더를 읽지 않는다). 그래서 허브 모델의 추정은 학습 상태에도 전체 매개변수를 쓴다.
- `imported_model()`은 이 값을 목록 항목 키(`parameters_total`, `parameters_b`, `hidden_size`, `num_layers`, `moe`,
  `tie_word_embeddings`)로 옮긴다.

**기본 목록(`capabilities.model_catalog()`)** 에 1.1의 19개 항목을 더한다.
- 같은 ID를 허브로도 등록했으면 화면 목록에서는 검토된 기본 목록 항목이 이긴다.
- 항목 키: `parameters_total`(정확한 값), `parameters_b`(= 전체/1e9, 소수 둘째 자리), `text_parameters`(safetensors 헤더에서 잰
  `model.language_model.*`+`lm_head.*` 합), `hidden_size`·`num_layers`(text_config), `max_length` 32768, `architecture`,
  `template`, `moe`, `tie_word_embeddings`, `gated: false`, `gpu_verified: false`, `verified_capabilities: []`,
  `verification_scope`, 그리고 `ARCHITECTURES`에서 온 `supported_stages`·`methods`.
- 기존 항목은 바꾸지 않는다.

### 4.5 메모리 추정

`estimate_memory(model, config)`:
- 전체 매개변수 `P = parameters_total`(없으면 `parameters_b × 1e9`). 가중치 `W = P × b`(비전·오디오 포함; QLoRA도 같은 계수).
- 학습 매개변수 기준 `Pt = text_parameters`(없으면 `P`). 학습 상태는 1단계 식에 `Pt`를 넣는다.
  - 전체 학습 `Pt × 14`, freeze는 학습 층 비율 × `Pt × 14`, LoRA·QLoRA·OFT는 `Pt × 0.01 × 16`.
- 활성값은 `text_config`의 `hidden_size`·`num_layers`로 계산한다(1단계 식).
- MoE는 `moe: true`로 표시만 한다. 모든 전문가 가중치가 GPU에 올라가므로 가중치는 전체 매개변수다.
- `DesktopService.compatibility`는 기본 목록에 없으면 설정의 `model_profile`로 만든 항목(`imported_model`)을 쓴다.
  허브 프로필이 기본 목록과 같은 저장소·같은 구조이면 기본 목록 항목(`gpu_verified`·`verified_capabilities` 포함)을
  쓴다(`capabilities.reviewed_model_entry`). 그 밖의 허브 항목도 기본 목록에 같은 저장소·구조가 있으면 없는 크기
  사실(텍스트 매개변수 등)을 거기서 채운다(`with_catalog_sizes`, 허브 값이 이긴다).
  크기 정보가 있으면 `MEMORY_UNKNOWN` 대신 추정한다. `MEMORY_UNKNOWN` 문구는 "모델 크기 정보가 없어"로 바꾼다.

### 4.6 학습 단계·방법 제한 — `capabilities.compatibility`

- 복합 구조에서 `rm`은 "Qwen3.5·Gemma 4의 보상 모델(RM) 학습은 아직 GPU에서 값 헤드를 검증하지 않아 막아 두었습니다."로,
  `ppo`는 "Qwen3.5·Gemma 4는 PPO 전용 학습 환경(transformers 4.52.4)이 지원하지 않습니다."로 거부한다.
- freeze 거부(한국어):
  - `architecture`가 `qwen3_5*`이고 `freeze_trainable_modules`가 `self_attn` 또는 `self_attn,mlp`.
  - 모델 항목이 `tie_word_embeddings: true`이고 `freeze_extra_modules`에 `lm_head`가 있음.
- rm 허용은 GPU 인수(5.2)의 RM 시험이 값 헤드 저장·평가를 보인 뒤 별도 커밋으로 `ARCHITECTURES`에 `rm`을 더하고 거부를 지운다.

### 4.7 설정 변환 — `training.py`

- `capabilities.model_entry(config)`가 설정의 모델 항목(허브 프로필 우선, 다음 기본 목록)을 돌려준다.
- `_legacy_plan`: 구조가 `COMPOSITE_ARCHITECTURES`이면 `freeze_vision_tower: true`, `freeze_multi_modal_projector: true`,
  `freeze_language_model: false`를 더한다.
- 템플릿이 `REASONING_TEMPLATES = {"qwen3_5", "qwen3_6", "gemma4", "gemma4n"}`이면 학습(`_legacy_plan`), 예측(`predict`,
  `prediction_plan`), 보상 평가(`evaluate_reward`) 설정에 `enable_thinking: false`를 더한다.
- 다른 구조·템플릿에는 키를 더하지 않는다. 고정 다이제스트 시험이 이를 지킨다.

### 4.8 processor 파일

`model_artifacts.PROCESSOR_FILES = {"preprocessor_config.json", "video_preprocessor_config.json", "processor_config.json",
"chat_template.json"}`와 `is_processor_file()`을 둔다.
- 내려받기 허용 목록과 기본 모델 스냅숏·기준 스냅숏·학습 결과·병합·확정 결과의 복사·허용 목록에 넣는다(위치는 계획 Task 6).
  산출물 목록 해시(`inventory`)에는 다른 파일처럼 들어간다.
- `TOKENIZER_FILES`에는 넣지 않는다. 그러면 `tokenizer_fingerprint`가 바뀌어 기존 루트·산출물 지문이 깨진다.
- 선택 파일이다. 있으면 받고 복사하며, 없어도 오류가 아니다. 텍스트 전용 모델의 결과는 바뀌지 않는다.
- 내려받기 허용 목록에 Qwen3.5 샤드 이름 `model.safetensors-*.safetensors`를 더한다.

### 4.9 LoRA 대상 검사 — `target_inspection.py`

- `COMPOSITE_MODEL_TYPES = {"qwen3_5", "qwen3_5_moe", "gemma4"}`, `LANGUAGE_MODEL_PREFIX = "model.language_model."`.
  모델 작업 런타임이 쓰는 모듈이므로 `model_hub`를 import하지 않는다.
- 규칙: 복합 모델의 LoRA 대상은 `model.language_model.` 아래 Linear다. LLaMA-Factory 0.9.5는 `lm_head`, 투영
  (`model.visual.merger`, `model.embed_vision`/`embed_audio`)과 동결된 비전·오디오 키를 빼고, `patch_target_modules`로 전체 이름을 쓴다.
  복합 모델의 최상위 모듈은 비전·오디오·투영·언어 모델·`lm_head`뿐이므로 두 규칙의 결과가 같다.
- `inspect_linear_modules`: `model_type`이 복합이면 meta 장치에서 `AutoModelForImageTextToText.from_config`로 만들고, 위 규칙의
  전체 이름을 증거로 낸다. 접두사 조건을 먼저 보므로 `Gemma4ClippableLinear`에 닿지 않는다.
- `verify_inspected_targets`: 텐서 목록이 복합이면(`model.language_model.` 이름이 있으면) 기대 집합을 같은 접두사로 줄인다.
- `resolved_lora_targets(lora_target, names)`: LLaMA-Factory가 어댑터에 쓰는 `target_modules`. 텍스트 모델은 잎 이름,
  복합 모델은 전체 이름이다.
- `target_leaves(targets)`: 대상 집합 비교에 쓰는 잎 이름 집합.

### 4.10 병합·확정·이어 학습 — `model_ops.py`, `graph_planner.py`, `hf_targets.py`

- `_load_fp32`: 복합 config면 `AutoModelForImageTextToText`로 불러온다. 비전·오디오와 어댑터 이름
  (`model.language_model.*`)이 맞고, 저장 결과가 LLaMA-Factory가 다시 불러올 수 있는 전체 복합 모델이 된다.
- 키 집합 검사(`_verify_materialized`): transformers 5.6.0이 불러오지 않는 원본 키(`UNLOADED_PREFIXES`: Qwen3.5의 `mtp.`)는
  기대 집합에서 뺀다. 그 밖의 차이는 지금처럼 거부한다.
- 대상 비교: `graph_planner._MergeLineage`는 산출물 어댑터의 `target_modules`를 `target_leaves`로 바꿔 새 어댑터의 예상(잎 이름)과
  비교한다. 텍스트 모델은 이미 잎 이름이라 결과가 같다. `check_continuation_targets`는 바꾸지 않는다.
- 모의 실행(`simulated_dispatch`)은 증거가 있으면 `resolved_lora_targets`로 어댑터 대상을 만든다.
- `hf_targets`: 복합 구조는 `model.language_model.*`·`lm_head.*` 텐서만 대상 증거로 쓰고, 비전·오디오·투영·`mtp.*`는 무시한다.
  언어 모델 안의 3차원 텐서는 `linear_attn.conv1d.weight`만 허용한다(대상이 아님). 전문가 텐서는 지금처럼 거부한다(MoE 제외).
- CPU 시험: `scripts/verify_model_ops_cpu.py`에 `check_composite` 단계를 더한다. 작은 무작위 `qwen3_5`(가짜 `mtp.fc.weight` 포함)와
  `gemma4` config로 대상 검사(meta·헤더 증거 일치), 전체 이름 대상의 LoRA 두 개 병합, 확정을 실행한다. 확정 결과는 키 집합(기본 − `mtp.*`),
  비전 텐서 보존, 로짓으로 확인한다. 기존 29개 검사에 6개가 더해진다.
  올바름을 보이지 못하면 2절의 대체 결정(한국어 거부)을 따른다.

### 4.11 vocab 검사 — `operation_outputs.py`

토크나이저와 config의 vocab 비교는 `text_config.vocab_size`를 먼저 읽고, 없으면 최상위 `vocab_size`를 읽는다.

### 4.12 버전과 문서

앱 0.6.0. `docs/OPERATIONS.md`(학습 환경 정리, flock, keepalive), `docs/WINDOWS_APP.md`(새 모델, 단계 제한, 서버 요금 판정,
학습 환경 정리), `docs/TRAINING_AND_STORAGE.md`(복합 구조 동결, 생각 끄기, 메모리 추정의 텍스트 매개변수)를 고친다.

## 5. 시험

### 5.1 CPU 자동 시험

- F2: `keep_alive`가 30초를 설정하고 전송 객체가 없으면 넘어간다. 가짜 SSH 연결이 keepalive를 기록한다.
- F3: 서버 요금 초과·0·빈 값·잘못된 값·한도와 같은 값. RunPod 0 거부 유지. 계획 카드와 제출(SSH 전) 한국어 거부.
- F1: 실행 줄의 `flock -s` 접두. `prerequisites`의 flock 요구. 임시 디렉터리 트리에서 `PRUNE_SOURCE`를 그대로 실행해
  지울 것·남길 것(현재 해시, 사용 중 잠금, 살아 있는 시도, 읽을 수 없는 매니페스트, 심볼릭 링크, 캐시 폴더)을 확인한다.
  `fcntl` 잠금 경우는 win32에서 건너뛴다. 화면 문구 시험.
- 목록·허브 프로필·직렬화 다이제스트 유지.
- 메모리 추정 고정 사례(배치 1, 길이 1024):
  - Qwen3.5-0.8B bf16 LoRA: 가중치 약 1.63GiB, 전체 약 3.3GiB.
  - Qwen3.5-27B bf16 LoRA: 가중치 약 51.7GiB라 A40 48GB에서 차단.
  - Gemma 4 31B 4비트 QLoRA: 가중치 약 16.0GiB, 전체 약 22.9GiB라 48GB에서 통과.
- 단계·freeze 한국어 거부, 변환 키(복합·추론 템플릿)와 기존 고정 다이제스트 유지, processor 파일 목록, vocab 검사.
- 대상 검사·병합·확정: 가짜 transformers로 클래스 선택을 시험하고(주 환경, torch 없음), 실제 수치는 모델 작업 CPU 환경의
  `scripts/verify_model_ops_cpu.py`(작은 무작위 `qwen3_5`·`gemma4`)로 확인한다.
- `uv run pytest`, `uv run ruff check src tests scripts`, `npm run build`, `npm test`, `cargo test --locked`.

### 5.2 GPU 인수 시험(유료, 사용자 승인됨)

- 공장 소유 A40 Pod, CA-MTL-1, 네트워크 볼륨 `cys1ne5znp`, `ManagedPods`로 만들고 끝나면 멈춘다(정책: GPU 1개, 실행당 $5, 4시간).
- 기존 드라이버 `%TEMP%\gpu_accept`(`provision.py`가 Pod를 만들고 SSH 지문을 두 경로로 확인; `run.py`가 MLflow를 끈 별도
  엔진 루트에서 브리지에 환경 확인 → 데이터 등록 → 계획 → 제출 → 실행 → 로그·메모리를 보내고 Pod를 멈춤)를 쓴다.
- 합성 데이터(32행)만 쓴다.
- 시험 목록:
  - `Qwen/Qwen3.5-0.8B`: SFT LoRA, QLoRA 4비트, DPO·KTO·PT 짧은 실행, full·freeze 짧은 실행, 예측·평가.
  - RM 시험(앱 밖에서 학습 환경의 `llamafactory-cli`로 직접 실행): rm 허용 여부를 정한다.
  - `google/gemma-4-E2B-it`: SFT LoRA.
  - `Qwen/Qwen3.5-9B`: SFT LoRA.
  - 0.8B 어댑터 병합(구현했을 때).
- 최대 메모리와 추정치를 비교한다. 검증된 모델만 별도 커밋으로 `verified_capabilities: ["8.6"]`과 `gpu_verified: true`를 반영한다.

### 5.3 화면 끝까지 시험

- `scripts/desktop_gui_acceptance.py`가 설치된 앱을 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>`로
  띄우고 Playwright `chromium.connect_over_cdp`로 조작한다.
  - 실행: `uv run --with playwright==1.64.0`(데스크톱 시험의 Node Playwright와 같은 버전; PyPI에 없으면 가장 가까운 1.x로 고정하고 기록).
    `uv.lock`은 바꾸지 않는다. 브라우저 내려받기는 필요 없다.
- 순서:
  1. 연결 검사
  2. 환경 확인
  3. 합성 데이터 등록(파일 경로 입력란; 기본 파일 대화상자는 쓰지 않음)
  4. 학습 설정 → 실행 계획(GPU 호환성 카드 확인)
  5. 학습 시작 승인 창의 학습 환경 준비 안내 확인 → 승인
  6. 완료 대기 → 결과·로그 확인
- 화면을 상태 폴더에 저장하고 비밀값을 출력하지 않는다.
- 사용자의 실제 앱 상태로 실행한다. 학습 기록은 앱 설정대로 사용자의 MLflow에 남는다(앱 승인 창이 알린다).
- 사용자 Pod `amdva0lurw9rv1`을 시험 동안만 켠다. SSH 지문이 바뀌었으면 두 경로로 확인한 뒤에만 설정에 반영하고,
  끝나면 Pod를 멈춘다(사용자 요청). 지우지는 않는다.

## 6. 위험

- **복합 구조 LoRA 이름 규칙.** LLaMA-Factory의 이름 규칙(부분 문자열 일치, 금지 키)을 앱이 재현한다.
  meta 장치 검사와 GPU 인수의 실제 어댑터 `target_modules`를 비교해 확인한다.
- **느린 linear attention.** fla·causal-conv1d가 없어 Qwen3.5 학습이 느리다. 9B 시간을 기록하고, 필요하면 후속으로 고정 파일에 넣는다.
- **빈 이미지 대조기.** 텍스트 전용 배치에 들어가는 빈 이미지·오디오 때문에 메모리·시간이 조금 늘 수 있다. GPU 인수에서 확인한다.
- **정리의 동시성.** 네트워크 볼륨을 여러 Pod가 함께 쓰면 `flock`이 Pod 사이에서 보장되지 않을 수 있다.
  살아 있는 시도 검사가 두 번째 안전장치다. 확신이 없으면 지우지 않는다.
- **메모리 추정.** 허브 모델은 텍스트 매개변수를 몰라 학습 상태를 크게 잡는다(안전한 쪽). Gemma E 모델의 층별 임베딩은 가중치에 포함된다.
- **화면 끝까지 시험.** 사용자 Pod를 켜면 지문·IP가 바뀔 수 있고, 같은 볼륨의 GPU가 없으면 켜지지 않는다. 그때는 기록하고 멈춘다.

## 7. 이후

- rm 허용(GPU 인수 결과에 따름), 이미지 데이터 학습, Qwen3.5-27B·35B-A3B·Gemma 4 26B/31B의 큰 GPU 검증,
  fla 커널, 여러 GPU 분산 학습.
