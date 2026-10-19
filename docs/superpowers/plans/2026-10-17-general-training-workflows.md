# General-purpose Training Workflows Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Windows에서 목적·입력 데이터를 자유롭게 바꾸고 재사용 레시피의 직렬·병렬 학습, 공통 평가, 호환 어댑터 통합과 최상 결과 선택을 자동 실행한다.

**Architecture:** 기존 단일 작업 실행기를 유지하고 프로젝트/레시피, 형식화된 모델 산출물, 그래프와 SQLite 컨트롤러를 추가한다. 그래프 정의와 준비된 노드의 실제 실행 명세를 따로 고정하며, 한 Pod에는 한 작업만 배정한다. Windows native broker가 보안 저장소를 읽어 백그라운드 Python 컨트롤러에 메모리 pipe로 전달한다.

**Tech Stack:** Python 3.12, uv locked, Pydantic, SQLite, 기존 RunPod SSH/MLflow, Rust/Tauri, TypeScript/Vite/Playwright. GPU 주 환경 LF0.9.5/PEFT0.18.1/Transformers5.6.0, PPO 전용 환경은 기존 잠금을 유지한다.

**Spec:** [승인된 설계](../specs/2026-10-17-general-training-workflows-design.md). 사용자 설계 승인: 2026-10-17 “진행해”. 구현 계획/실행 방식 승인: 2026-10-17 “계획 확인: 기능별 담당 구현·독립 검토로 진행”. 상태: 구현 진행.

**2026-10-19 범위 추가:** 사용자 요청과 “SSH 접속 후 서버에서 직접 실행” 선택에 따라
[일반 SSH GPU 서버 확장](../specs/2026-10-19-ssh-gpu-servers-extension.md)을 포함한다.
진행 중인 Task 6의 검토를 마친 뒤 Task 6A → 6B → 7–11 순으로 실행한다.
이 추가 범위가 아래 RunPod 전용 문구보다 우선하며 기존 기본 dry-run/데이터 승인/비밀정보 경계는 유지한다.

**2026-10-19 튜토리얼 추가:** 사용자 요청에 따라 [첫 실행 실습형 튜토리얼 설계](../specs/2026-10-19-first-run-tutorial-design.md)와
[별도 구현 계획](2026-10-19-first-run-tutorial.md)을 추가한다. 실행 순서는 Task 6A 독립 검토 → 6B → 튜토리얼 Task 0–5 → 7–11이다.

## Global Constraints

- Python 3.12와 `uv sync --extra dev --extra tracking --locked`; 완료 전 `uv run pytest`, `uv run ruff check src tests`.
- 기존 단일 학습 API와 Windows 0.3.0 설정은 계속 읽는다. 학습 엔진과 인프라 어댑터를 분리한다.
- 기본은 dry-run. 명세에 인증값을 넣지 않는다. 유료 자원과 데이터 전송은 명시적 승인 없이 실행하지 않는다.
- 기존 배정 Pod 또는 기존 Linux SSH GPU 서버를 사용하며 자원을 생성·시작·정지·삭제하지 않는다.
  실제 동시 실행은 서로 다른 배타 자원에서만 한다. 같은 서버/Pod의 별칭은 같은 잠금을 공유한다.
- 그래프 최대 64개 노드·256개 간선. 기본 동시 실행 수 1. 파일당 32 MiB. UTF-8/UTF-8 BOM, CSV만 CP949 선택.
- 병합은 서로 다른 완료 실제 LoRA/QLoRA 2~4개, 양의 유한 가중치 합 1, `cat`, 합산 rank ≤256.
- 병합 후 기본 허용 퇴보 0. 동점은 산출물 ID 사전순. 부분 성공은 명시 선택하고 성공 후보 최소 2개.
- 기존 HF/데이터/manifest 바이트와 MLflow ID/태그는 보존한다. 과거 기록에 새 선택 자격을 임의 부여하지 않는다.
- 실제 검증과 남은 GPU 작업은 docs/PROGRESS.md에 기록한다. 이번 구현 승인은 GPU/외부 데이터 사용 승인이 아니다.

## Review Focus

1. 같거나 그룹으로 연결된 입력이 다른 분할로 새는 경우: 연결 성분 분할, 변경된 미리보기 거부 — Task 2.
2. 같은 Pod의 다른 프로필 이름, 저장 후 수정된 프로필: 물리 Pod별 배타성과 승인한 revision 고정 — Tasks 5, 8.
3. 부모가 이미 확정된 모델인데 원본 HF를 다시 기반으로 쓰는 경우: 실효 부모 보존과 완전한 토크나이저 — Tasks 3, 7.
4. 시작 응답 유실·오래된 컨트롤러·취소/join 경합: 재학습 없이 같은 시도 확인, 오래된 변경 거절 — Tasks 6, 8.
5. 모델 등록 성공 후 로컬 영수증 유실: MLflow 태그로 채택하거나 불확실 상태 유지, 학습/등록 무조건 반복 금지 — Task 8.

## File and dependency map

| 책임 | 파일 | 선행 task |
|---|---|---|
| 프로젝트·레시피 버전 | `projects.py`, `recipes.py` | 없음 |
| 입력 매핑·분할 | `dataset_import.py`, `dataset_splits.py` | 1 (DatasetRef) |
| 모델 identity·snapshot | `model_artifacts.py` | 없음 |
| 비교 benchmark·선택 | `benchmarks.py` | 3 |
| 연산·그래프 정의·컴파일·계획 | `operations.py`, `graph_schema.py`, `graph_planner.py` | 1–4 |
| 실행 시도·원자적 노드 저장 | `jobs.py`, `graph_store.py` | 3, 5 |
| GPU 모델 연산·전송·worker | `model_ops.py`, `operation_runner.py`, 기존 worker/engine/executor | 3–6 |
| 실행 자원·컨트롤러·저장 재시도 | `executor_profiles.py`, `graph_controller.py`, `tracking_outbox.py` | 5–7 |
| native/API/CLI | `desktop_workflows.py`, `desktop.py`, Rust bridge, `cli.py` | 1–8 |
| Windows 화면 | `desktop/src/views/*`, `api.ts`, `project-store.ts` | 9 |
| 설치·문서·인수 | `scripts/*`, `.github/workflows/*`, docs | 전체 |

새 Python 파일은 `src/tuning_factory/`에 둔다. 각 task는 실패 시험→구현→해당 시험/기존 회귀 검사→커밋으로 끝낸다. Task 1/3은 독립 작업 가능하며, Task 1 완료 후 Task 2, Task 3 완료 후 Task 4를 병행할 수 있다. 공용 파일 수정은 root가 순서대로 통합하고 이후에도 선행 인터페이스가 확정된 작업만 병행한다.

### Task 1: 빈 프로젝트와 재사용 가능한 학습 레시피

**Files:** Create `projects.py`, `recipes.py`, `tests/test_projects.py`, `tests/test_recipes.py`, `tests/test_legacy_migration.py`; 기존 `config.py`의 typed option 클래스를 재사용한다.

**Interfaces:** `Revision[T](spec, revision_sha256)`; `ProjectSpec(schema_version=1, project_id, name, purpose, task_type, datasets, default_executor_profile_id, graph, legacy_origin)`는 빈 graph/미완료 draft를 허용한다. `DatasetRef(dataset_id, version, sha256)`, `DatasetBinding(binding_id, role=train|validation|test, dataset)`을 공용 정의한다. `RecipeSpec`은 ID/버전/이름/설명, backend/capability version, stage/method/input kind, quantization과 typed options를 갖고 데이터·목적지·승인·reward 경로를 갖지 않는다.

**Produces:** `ProjectStore(root).create(request_id, name, purpose, task_type)`, `.save(spec, expected_revision)`, `.get(id, revision=None)`, `.list()`; `RecipeStore`의 `.save/.get/.list/.clone`; `recipe_from_config(config)`와 `recipe_training_options(recipe)`는 기존 설정을 불변 recipe로 복사하고 원본을 수정하지 않는다. graph draft는 JSON 구조만 보존하고 완전성 검사는 Task 5에 위임한다.

- [ ] **1. 실패 시험 작성:** `test_empty_project_has_no_paper_identity`, `test_revision_conflict_never_overwrites`, `test_request_id_is_idempotent`; recipe clone 독립성, stage/input 불일치, reward 경로/데이터/Pod/credential/임의 명령 필드 거절; legacy profile/blob의 전후 bytes 동일.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_projects.py tests/test_recipes.py tests/test_legacy_migration.py -q` → 새 인터페이스 부재로 실패.
- [ ] **3. 구현:** content-addressed revision과 current pointer, 저장 시 expected revision 비교를 하나의 저장소 잠금 안에서 처리한다. Unicode 표시 이름과 ASCII 내부 ID를 분리한다. recipe의 PPO reward는 Task 5의 별도 모델 입력으로만 연결한다.
- [ ] **4. 검증:** 위 시험 + `tests/test_config_registry.py tests/test_desktop.py` 통과. 빈 프로젝트 저장이 모델/데이터가 없다는 이유로 실패하지 않아야 한다.
- [ ] **5. 커밋:** `feat: add general projects and versioned training recipes`.

### Task 2: 다른 입력 형식의 열 매핑과 누수 없는 데이터 분할

**Files:** Create `dataset_import.py`, `dataset_splits.py`, `tests/test_dataset_import.py`, `tests/test_dataset_splits.py`; Modify `registry.py`는 새 import provenance를 별도 필드/파일로 추가하며 기존 등록 경로를 바꾸지 않는다.

**Interfaces:** `ImportMapping(schema_version=1, transform_version='text-mapping-v1', source_format, encoding, csv_delimiter, kind, formatting, fields, message_tags, boolean_labels)`; `fields` 값은 표현식이 아닌 bounded tuple 경로다. `ImportService(root).snapshot_local(path)`, `.inspect(source_id, mapping)`, `.register(source_id, mapping, preview_sha256, dataset_id, version, synthetic, export_allowed)` 반환은 source/canonical 해시·samples·구조화된 row issues다. S3 snapshot은 기존 conditional GET과 별도 승인 경로를 사용한다.

**Produces:** `SplitPlan(dataset, seed, train_basis_points, validation_basis_points, test_basis_points, group_field, outputs)`, `preview_split(registry, plan)`, `commit_split(registry, plan_sha256)`; 합계 10,000 basis points, seed는 signed 64-bit. `input_fingerprint(row, kind)`와 `assert_no_input_overlap(train_refs, heldout_refs)`를 Task 5가 사용한다. split manifest에 원본 ordinal/행 해시/실제 할당과 새 DatasetRef를 기록한다.

- [ ] **1. 실패 시험 작성:** CSV/JSON/JSONL 질문·문맥·답이 같은 canonical bytes가 됨; CP949 명시 선택; 중복 CSV 헤더, 애매한 boolean, 빈 필수 값, 악성 경로 표현식, 32 MiB 초과, 변환 후 preview 변경을 거절. 같은 입력 OR 같은 group의 전이 연결 성분이 분할을 넘지 않음, 모든 ordinal 한 번만 할당, 요청한 nonempty split 불가능 시 오류.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_dataset_import.py tests/test_dataset_splits.py -q` → 새 기능 부재로 실패.
- [ ] **3. 구현:** 원본과 변환 바이트를 로컬에 별도로 불변 보존하고 canonical output은 기존 parser로 검사한다. source와 canonical 파일 각각 32 MiB 한도를 검사한다. KTO 문자열 매핑은 disjoint exact label set만 허용한다. `text-input-v1`은 NFC/줄바꿈/연속 공백을 정규화한 입력(정답/chosen/rejected/kto_tag 제외, 대화는 최종 정답 이전 history)을 canonical hash한다. group_field는 import 원본 record, legacy 등록이면 기존 record를 기준으로 해석한다. 같은 입력 OR group의 연결 성분을 seed+component 해시로 결정적으로 배치하고 각 출력에서 원본 순서를 유지한다. 등록과 split commit의 부분 실패는 재호출로 복구하고 기존 버전 덮어쓰기를 거절한다.
- [ ] **4. 검증:** 새 시험 + `tests/test_dataset_kinds.py tests/test_storage.py tests/test_desktop_inspection.py` 통과. 미리보기/등록만으로 jobs 제출·외부 전송이 발생하지 않는 spy 검사 포함.
- [ ] **5. 커밋:** `feat: import mapped datasets and reproducible grouped splits`.

### Task 3: 불변 모델 산출물과 모델 입력 계약

**Files:** Create `model_artifacts.py`, `tests/test_model_artifacts.py`; 기존 `manifest.py`/`resolve()`는 이 작업에서 변경하지 않는다.

**Interfaces:** `HFRootRef(repo_id, revision, config_sha256, tokenizer_fingerprint)`, `ModelArtifactRef(artifact_id, inventory_sha256)`에는 경로가 없다. `ModelDescriptor`는 생성 job/attempt/manifest, storage_kind(adapter|full_model), semantic_kind(causal_lm|reward_model), has_value_head, 원본 HF, 정확한 실효 base ref, tokenizer/config fingerprint, adapter 실효 설정, tensor inventory, synthetic/simulation 표식을 가진다. `ModelLoadRef(base: HFRootRef|ModelArtifactRef, adapter: ModelArtifactRef|None)`; `ModelInput(policy, initialization=new_adapter|continue_adapter|full_weights, frozen_reference=None, reward_model=None)`.

**Produces:** `ModelArtifactStore(root).freeze(directory, descriptor)`, `.get(ref)`, `.verify(ref)`, `.path(ref)`; copy-to-temp→streaming inventory→검사→atomic publish. artifact_id는 자신의 ID를 제외한 canonical descriptor의 SHA256, inventory_sha256은 정렬된 파일 목록의 SHA256이다. 입력 base는 완전한 모델이어야 하며 adapter의 effective_base가 그 base와 같아야 한다. 공통 연산 union은 benchmark가 정의된 뒤 Task 5에서 만든다.

- [ ] **1. 실패 시험 작성:** output 수정/재개 후에도 frozen parent bytes 유지; 심볼릭 링크/path escape/누락 shard/hash 변조/unknown fields 거절; effective parent와 root HF가 다를 때 구분; descriptor에 credential/임의 모델 경로 금지; adapter/full 저장 형태와 생성/보상 의미 구분.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_model_artifacts.py -q`.
- [ ] **3. 구현:** 모델 metadata와 tensor 이름/shape/dtype 검사 계약을 고정한다. 무거운 tensor 패키지는 lazy import하며 desktop core dependency에 넣지 않는다. adapter 출력의 base 포인터는 config.base_model이 아니라 실제 ModelInput.policy.base다. 유한 tensor 전수 검사는 Task 7 연산 환경에서 수행한다.
- [ ] **4. 검증:** 새 시험 + `tests/test_streaming_artifacts.py tests/test_config_registry.py tests/test_training.py` 통과. 큰 파일 hash가 read_bytes에 의존하지 않는 회귀 검사 유지.
- [ ] **5. 커밋:** `feat: track immutable effective model artifacts and inputs`.

### Task 4: 공통 benchmark와 최상 결과 선택

**Files:** Create `benchmarks.py`, `tests/test_benchmarks.py`; Modify `evaluation.py`에 명시적인 `text` 정답 평가와 metric schema를 추가하고 legacy 비교 API는 유지한다.

**Interfaces:** `BenchmarkSpec(dataset:DatasetRef, evaluator_kind, evaluator_version, ordered_sample_ids, max_new_tokens, cutoff_len, seed, generation_mode='greedy', precision, quantization_bit, tokenizer_fingerprint, template, semantic_model_kind)`; `.fingerprint()`은 전체 spec+평가기 source hash를 고정한다. `SelectionSpec(metric, direction=max|min, thresholds, allow_partial=False, min_success=2, merge_regression_tolerance=0)`; threshold 각각은 metric/direction/value다. `EvaluationRecord(model, benchmark_sha256, metrics, status, synthetic, simulation, producer_attempt_id)`.

**Produces:** `select_best(records, selection) -> SelectionResult(winner|None, eligible, excluded, reasons)`와 `finalize_with_merge(best_original, merged_record, selection)`; 같은 benchmark만 비교한다. `text` metric은 NFC+공백 정규화 exact match와 Unicode 문자(공백 제외) multiset F1로 고정하고 이를 텍스트 정답 일치 지표라고 표시한다. 의미적 품질 점수라고 부르지 않는다. 저장 형식(adapter/full)은 비교 fingerprint에 넣지 않는다.

- [ ] **1. 실패 시험 작성:** max/min 반대 승자, 동일 점수 artifact-ID 동점, sample/seed/precision 차이 비교 거절, NaN/Inf/결측/모의·합성/미평가 제외, 모두 탈락 winner=None, 요청한 전체 후보 대기, 명시적 partial 최소 2개, 병합 후 퇴보 시 원본 승자 유지. 한국어 text 정규화/문자 F1의 고정 입력과 예상값 검사.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_benchmarks.py -q`.
- [ ] **3. 구현:** 평가를 통과한 증거만 선택하고 점수와 제외 이유를 불변 selection receipt로 남긴다. pipeline completion/JSON validity를 자동 기본 주 지표로 고르지 않는다. held-out test 결과는 selection record와 따로 보관한다.
- [ ] **4. 검증:** 새 시험 + `tests/test_evaluation_tracking.py tests/test_prediction.py tests/test_desktop.py` 통과.
- [ ] **5. 커밋:** `feat: compare models with immutable benchmarks and explicit objectives`.

### Task 5: 학습 그래프 검증·컴파일·전체 비용 계획

**Files:** Create `operations.py`, `graph_schema.py`, `graph_planner.py`, `executor_profiles.py`, `tests/test_operation_schema.py`, `tests/test_graph_schema.py`, `tests/test_graph_planner.py`, `tests/test_executor_profiles.py`; Modify `manifest.py`에 v2 생성기를 추가한다.

**Interfaces:** `NodeOutputRef(node_id, output_name)`; `TrainNode(recipe_revision, overrides, model_input, dataset_binding, reward_binding, executor_profile_ids)`, `EvaluateNode(model, benchmark_revision, executor_profile_ids)`, `SelectNode(evaluations, selection)`, `MergeNode(models, weights, executor_profile_ids)`, `MaterializeNode(model, executor_profile_ids)`; 모든 node는 id/label을 가진다. GraphSpec은 이 union의 nodes, explicit depends_on, max_concurrent, max_total_cost_usd와 bounded retry policy다. `ExecutorProfile(profile_id, revision, name, provider='existing_runpod', pod_id, credential_scope)`와 `ResourceFacts(pod_id, gpu_type, gpu_count, total_hourly_cost_usd, observed_at)`는 public data다.

**Produces:** `validate_graph(project, recipes, registry) -> Issue[]`; `plan_graph(project_revision, execution_choices, resource_snapshots, simulation=True) -> GraphPlan`; `compile_node(plan, node_id, verified_bindings) -> OperationSpec|LocalSelectSpec`. GraphPlan에는 definition/project/recipe revisions, 허용 목적지/프로필 revision, 모든 데이터·benchmark 해시, 노드별 입력 계약/시간/최대 예상 비용과 plan SHA가 들어간다. `ApprovalGrant(plan_sha256, destination_profile_revisions)`는 제출 시 별도 생성한다.

`OperationSpec`는 resolved `TrainOperation(config:AIConfig, model_input)`, `EvaluateOperation(model, benchmark:BenchmarkSpec, resources)`, `MergeOperation(models, weights, resources)`, `MaterializeOperation(model, resources)`의 discriminator union이다. `resolve_operation(operation, registry)`는 v2 manifest를 만들며 실제 경로는 worker에서만 해석한다. v1 `resolve()`의 기본 hash/바이트 계약을 유지한다. 레시피 PPO 옵션에 저장되지 않는 reward 값은 검증된 typed binding으로만 실행 시 공급한다.

- [ ] **1. 실패 시험 작성:** cycle/missing/duplicate/typed-port 오류, 65개 노드·257간선, validation/test를 train으로 연결, 별도 파일의 같은 입력 누수, unsupported stage/method/부모 전환/OFT route, 평가 없는 select 거절. profile 수정 후 기존 승인 retarget 금지, Pod alias 배타성 키 동일, Pod 총 요금을 GPU 수로 다시 곱하지 않음, 변환/평가/병합/재시도 비용 포함. 새 operation extra field 거절과 v1 manifest 바이트 보존.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_operation_schema.py tests/test_graph_schema.py tests/test_graph_planner.py tests/test_executor_profiles.py -q`.
- [ ] **3. 구현:** 초안 저장과 실행 가능한 계획 검사를 분리한다. 부모 결과가 아직 없는 자식은 조건부 입력 계약으로 계획하고 ready 시 실제 산출물 ref로 새 operation manifest를 고정한다. runtime profile은 각 operation에 고정한다. 실제 입력으로 조건을 충족하지 못하면 자식 시작 전에 오류를 남기고 중단한다. 기존 단일 config의 per-GPU 가격 필드가 필요할 때만 Pod 총 요금을 GPU 수로 나누어 내려 기존 Policy와 그래프 총액이 일치하도록 검사한다. 지원되지 않는 primary↔legacy PPO artifact route는 사전에 거절한다.
- [ ] **4. 검증:** 새 시험 + `tests/test_training_capabilities.py tests/test_runtime_profiles.py tests/test_execution.py` 통과. plan 기본 경로에 provider mutation/데이터 전송 호출이 0인지 검사한다.
- [ ] **5. 커밋:** `feat: plan typed training graphs and approved resource budgets`.

### Task 6: 실행 시도 fencing과 그래프의 원자적 저장

**Files:** Create `graph_store.py`, `tests/test_graph_store.py`; Modify `jobs.py`, `runner.py`, `cli.py`, `tests/test_jobs.py`, `tests/test_reconciliation.py`와 기존 실행 fixture.

**Interfaces:** `AttemptLease(job_id, attempt_id, lease_token, resource_id)`; `JobStore.claim()`은 이를 포함한 claimed job을 반환한다. `heartbeat/transition/set_run_id/release_gpu/reconcile_result`의 active-attempt 변경은 lease를 받는다. 별도 immutable `launch_id`는 remote 요청에 사용하며 lease owner 교체 때 바뀌지 않는다. `Factory.run_claimed(claimed, executor, backend, approval)`를 추출하고 기존 `Factory.work()`는 claim→run_claimed wrapper로 유지한다.

**Produces:** 같은 jobs.db의 graph_runs/nodes/edges/job_attempts/node_outputs/controller_leases/resource_leases/tracking_outbox tables와 additive migration. `GraphStore.submit(plan, request_id, approval)`, `.get_run(id)`, `.prepare_ready(controller_lease)`, `.claim_ready(resource_facts, controller_lease)`, `.record_outputs(attempt_lease, outputs)`, `.pause/.resume/.cancel`. UNIQUE(run,node)와 canonical manifest를 jobs 연결과 같은 트랜잭션에 기록한다. `materialize_job_evidence(job_id)`는 DB에서 파일 내보내기를 재실행할 수 있다.

- [ ] **1. 실패 시험 작성:** 경쟁 submit/claim에서 노드 job 하나; DB commit 직후 파일 쓰기 전 crash 복구; graph-owned job을 legacy worker가 claim 못함; 오래된 heartbeat/완료/release를 거절; 재개한 시도의 lock을 옛 시도가 해제 못함; 큰 고우선순위 작업 뒤 작은 fitting job 실행; migration이 기존 기록/불확실 lock을 보존.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_graph_store.py tests/test_jobs.py tests/test_reconciliation.py -q`에서 새 assertions 실패 확인.
- [ ] **3. 구현:** active 변이는 attempt_id+lease_token CAS를 요구한다. controller 교체 시 execution launch identity는 보존하고 actor lease만 재발급한다. claim 전에 자원 적합 후보를 고른다. legacy active 작업은 불확실 상태/기존 remote 경로를 보존하고 자동 재시작하지 않는다. CLI release-lock 우회도 현재 시도와 종료 확인을 요구하도록 변경한다.
- [ ] **4. 검증:** 새 시험 + 기존 `tests/test_jobs.py tests/test_runner_cli.py tests/test_reconciliation.py tests/test_remote_worker.py` 전부 통과. graph controller가 claim 후 work()로 재claim하지 않는 시험 포함.
- [ ] **5. 커밋:** `feat: persist graph jobs atomically with fenced execution attempts`.

### Task 6A: 일반 Linux SSH GPU 서버와 제공자 중립 자원 계약

**Files:** Create `gpu_servers.py`, `tests/test_gpu_servers.py`; Modify `executor_profiles.py`,
`operations.py`, `graph_planner.py`, `graph_store.py`, `jobs.py`, `execution.py`, `connections.py`,
`desktop_runtime.py`, `cli.py`, `diagnostics.py` 및 해당 범위 시험. 기존 승인된 Task 6을 기반으로 한다.

**Requirements:** [SSH 서버 확장 명세](../specs/2026-10-19-ssh-gpu-servers-extension.md).
일반 서버용 typed profile/observation을 추가하고 기존 RunPod 공개 바이트/해석은 보존한다.
공통 자원 identity/가격/관측/배타 키를 사용하며 일반 서버를 가짜 Pod dictionary로 표현하지 않는다.
GPU 선택과 작업 경로·목적지 설정은 계획/승인/복구 identity에 포함한다.
RunPod는 실제 API 가격을 검사하고, 일반 서버는 운영자가 명시한 0 이상 유한 비용을 사용한다.

- [ ] **1. RED:** RunPod 키 없이 SSH 서버 검사/준비/실행 어댑터 구성; 비-root 계정/사용자 지정 경로;
  실제 GPU UUID 선택과 개수/종류 불일치; 잘못된 지문/경로/원격 명령 입력 차단;
  같은 물리 서버 별칭의 잠금과 알려지지 않은 기존 잠금 보존; 내부 서버 비용 0 허용;
  목적지/경로/GPU 변경 후 원래 시도 로그·복구 거부; RunPod API 미호출을 시험한다.
- [ ] **2. 구현:** 고정 SSH probe로 Linux/NVIDIA/작업 환경을 확인하고 선택 GPU를 실행 환경에 바인딩한다.
  기본 Python을 바꾸지 않는 버전별 private venv와 기존 worker 검증을 재사용한다.
  원격 부트스트랩은 명시적 prepare 동작에서만 한다. CLI에는 일반 SSH 서버 연결/검사/실행 경로를 제공한다.
  GraphPlan/Operation/GraphStore는 제공자 이름을 추측하지 않고 검증된 공통 자원 계약을 사용한다.
- [ ] **3. 검증:** 새 GPU 서버 시험과 기존 graph/jobs/runner/remote/runtime/connections/CLI 회귀 시험,
  root의 전체 pytest/Ruff 및 독립 검토. 실제 SSH/BOP 접속·GPU 실행은 수행하지 않는다.
- [ ] **4. 보고/커밋:** 소비할 typed API, native/UI 설정 계약, 관측 근거/비용 출처,
  잠금/복구 보장과 미검증을 기록한다. `feat: support existing Linux GPU servers over SSH`.

### Task 6B: Windows 일반 GPU 서버 연결 화면과 설정 보존

**Files:** Modify `desktop.py`, `desktop/src-tauri/src/main.rs`, `desktop/src/main.ts`, `workbench.ts`,
필요한 `style.css`, Python desktop 시험, `desktop/tests/workspace.spec.ts`,
`README.md`, `docs/CONFIGURATION.md`, `docs/WINDOWS_APP.md`.

**Requirements:** Task 6A의 검토된 계약을 사용한다. 연결 대상 RunPod/일반 SSH 서버를 선택하고
일반 서버 주소·포트·계정·키 파일·호스트 지문·작업 경로·비용을 저장한다.
서버의 GPU 목록을 조회하고 UUID를 선택한다. 빈 제공자 필드는 과거 RunPod 설정으로 읽는다.
RunPod 키/Pod ID는 일반 서버에서 요구하지 않는다. 연결 검사, 환경 확인/명시적 준비,
학습 승인, 로그/진행률/재조정은 같은 고정된 목적지 정보를 사용한다.
튜토리얼 연결 단계용으로 연결 대상(RunPod/GPU 서버) 선택 필드 묶음에 `data-tour="connection-provider"`,
선택한 제공자의 연결 필드 묶음에 `data-tour="connection-fields"`, GPU 학습 환경(환경 확인/준비·GPU 선택) 카드에
`data-tour="connection-runtime"`을 각각 정확히 하나씩 렌더링한다.

- [ ] **1. RED:** 일반 서버 필드 표시와 save/reopen; 키/인증값 masking 유지;
  일반 서버 GPU 조회/선택/0 비용 실행 계획; 프로필/선택 GPU 변경 시 기존 계획 차단;
  환경 준비·실행 확인 취소 시 부작용 없음; RunPod/HF/S3/history 기존 흐름 보존.
- [ ] **2. 구현:** native 설정 allowlist와 엄격한 typed backend 동작을 확장한다.
  자유 입력과 에러를 HTML escape하고 native form/keyboard/busy 상태를 보존한다.
  홈/확인 화면의 연결 정보도 선택 제공자에 맞게 표시한다. 전역 환경에 SSH 비밀값을 추가하지 않는다.
  앞으로 Task 9/10의 다중 프로필/그래프 화면에서도 이 계약을 그대로 재사용하도록 모듈 경계를 둔다.
- [ ] **3. 검증:** 해당 Python/native 설정 시험, 전체 Playwright 및 TypeScript/Vite build,
  root 전체 pytest/Ruff와 독립 검토. 실제 기관 서버/자격 증명/데이터는 사용하지 않는다.
- [ ] **4. 문서/커밋:** SSH 직접 실행 설정 예시와 관측/비용/검증 한계를 설명한다.
  실제 설치본 수용은 Task 11에 포함한다. `feat: configure SSH GPU servers in Windows workbench`.

### Task 7: 실제 부모 모델 전달·평가·병합·전체 모델 확정 연산

**Files:** Create `model_ops.py`, `operation_runner.py`, `tests/test_model_inputs.py`, `tests/test_model_ops_contract.py`, `scripts/verify_model_ops_cpu.py`, `containers/model-ops-cpu-requirements.in/.lock`; Modify `training.py`, `worker.py`, `execution.py`, `desktop_runtime.py`, `runtime_profiles.py`, `tests/test_remote_worker.py`.

**Interfaces:** `WorkerRequest(schema_version=2, attempt_id, launch_id, manifest_sha256, operation_sha256, timeout_seconds)`; hash-검증 command.json과 operation manifest를 allowlist로 dispatch한다. `stage_model_inputs(operation, approved_cache, staging_root) -> ResolvedModelInputs`; `LlamaFactoryBackend.translate(config, model_input=None)`의 legacy 호출 결과는 동일하다. 새 logical YAML은 policy_base/policy_adapter/reference_base/reference_adapter/reward_base/reward_adapter role placeholder만 가진다. `bind_training_plan(template, verified_bindings)`는 승인된 cache 아래 경로로 허용 slot만 치환하고 train.bound.yaml과 binding/실행 YAML hash를 증거로 남긴다. LF0.9.5에는 tokenizer_name_or_path가 없으므로 각 local base/reference 폴더에 동일한 토크나이저 snapshot을 포함한다.

**Produces:** `execute_operation(operation, directory, timeout, canceled) -> OperationResult`; train/evaluate는 기존 엔진, `merge_adapters(inputs, weights, output)`와 `materialize_model(input, output)`는 pinned PEFT를 사용한다. LocalSelect는 worker에 보내지 않는다. cache의 전체 기반 모델을 attempt 디렉터리에 복사하여 SSH uploader가 다시 전송하지 않도록 경로 binding과 파일 staging을 분리한다. remote cache는 content hash 검증 후만 재사용하고 새 UUID를 매번 만들지 않고 저장된 attempt_id 경로를 사용한다. remote start marker와 결과가 같은 launch_id를 가지며 같은 시작을 두 번 수행하지 않는다.

- [ ] **1. 실패 시험 작성:** parent HF 대신 effective SFT snapshot 유지; local tokenizer 누락 거절; continuation의 실효 alpha(None→2*rank)/dropout/target/rank/variant/양자화 mismatch 거절; DPO/KTO frozen reference가 확정 부모를 가리킴; PPO reward typed binding 정합성; manifest/command/결과 attempt mismatch; 시작 응답 유실 후 두 번째 학습 호출 0. merge input 중복·base/tokenizer/target 불일치·rank 초과·비정상 tensor 거절.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_model_inputs.py tests/test_model_ops_contract.py tests/test_remote_worker.py -q`와 CPU 검산의 최소 실패를 확인한다. numerical acceptance는 importorskip로 넘어가지 않는다.
- [ ] **3. 구현:** continuation만 부모 adapter를 trainable로 로드하며 새 optimizer를 시작한다. 방법 전환은 명시적 materialize 노드를 요구한다. 새 adapter/full/freeze는 그 effective parent를 기반으로 한다. runtime_preflight는 config.base_model이 아닌 실제 bound 모델/토크나이저를 검사한다. 참조가 필요한 DPO/KTO는 `ref_model`의 검증된 frozen local parent와 명시한 reference 양자화 설정을 사용하며 필요 VRAM을 계획한다. ORPO/SimPO는 reference-free로 유지한다. `cat`은 normalize한 양의 weights로 FP32 연산하고 저장 후 델타 검산·reload를 수행한다. 전체 모델은 exact effective unquantized base에 `safe_merge=True`로 병합하고 reload logits를 비교한다.
- [ ] **4. 검증:** 표준 suite 외에 별도 Python 3.12 CPU 환경에 torch2.7.1+cpu/PEFT0.18.1/Transformers5.6.0/safetensors0.8.0 및 필요한 작은 core deps를 hash-lock한다. `scripts/verify_model_ops_cpu.py`가 WordLevel tokenizer와 Qwen2 설정(layer=2, hidden=16, intermediate=32, heads=2, KV heads=2, vocab=32, context=64)의 모델을 로컬 생성한다. rank 2/3, alpha 4/9, 가중치 0.25/0.75와 0이 아닌 A/B 행렬로 output rank=5와 FP32 delta(atol=1e-6, rtol=1e-5), reload logits(atol=1e-5, rtol=1e-4)를 검사한다. 부모→자식 업데이트 보존, frozen reference 불변, tokenizer 유지와 NaN 차단도 검사한다. 모델 다운로드·GPU·기관 데이터 없이 JSON receipt를 남긴다. 다른 저장 dtype은 별도 수치 검증을 통과한 versioned tolerance policy만 허용한다. primary/PPO GPU lock은 무관한 변경 없이 유지한다.
- [ ] **5. 커밋:** `feat: execute verified model handoff and compatible adapter operations`.

### Task 8: 여러 배정 Pod의 지속 컨트롤러와 MLflow 저장 복구

**Files:** Create `graph_controller.py`, `tracking_outbox.py`, `tests/test_graph_controller.py`, `tests/test_tracking_outbox.py`; Modify `executor_profiles.py`, `tracking.py`, `runner.py`, `graph_store.py`와 관련 회귀 tests.

**Interfaces:** `GraphController(store, executor_provider, operation_dispatcher, tracker, clock).acquire_controller(owner_id)`, `.tick(controller_lease)`, `.run_forever(stop_event)`, `.status()`; 시간/자원/실행기는 주입한다. `ExecutorProvider.resolve(profile_id, approved_revision)`는 비밀정보를 메모리에서만 해석하고 관측한 canonical Pod ID와 계획을 대조한다. `TrackingOutbox.enqueue(event, transaction)`, `.claim_pending(lease)`, `.record_receipt(event_id, remote_identity)`, `.retry(event_id, error)`.

**Produces:** 부모 완료/산출물 검증/필요 증거 저장 후 준비 노드 자동 dispatch; CPU select와 GPU operation 분리; pause/cancel/reconcile; 총 예산 예약과 bounded retry. execution outcome과 tracking_status를 별도 저장하고 원격 종료가 확인되면 GPU lease를 해제하되 증거 저장 전 후속 의존 노드는 막는다. MLflow graph/node/attempt hierarchy와 전체 lineage artifact를 기록한다.

- [ ] **1. 실패 시험 작성:** two-resource diamond 동시 진행/one-Pod 자동 순차; 같은 Pod aliases 배타성; 부모 실패와 partial join; pause 후 새 dispatch 0; cancel이 join보다 우선; duplicate controller·controller 재시작·늦은 결과·ambiguous launch에서 재학습 0. upload 실패→재시작→재저장 중 training call 1회. remote model registration 후 receipt crash는 stable tag 검색으로 기존 version 채택, 조회도 불확실하면 자동 추가 create 금지.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_graph_controller.py tests/test_tracking_outbox.py -q`.
- [ ] **3. 구현:** controller lease와 attempt lease를 각 변경에서 확인한다. 같은 불확실 launch를 reconcile하고 timeout만으로 새 시도를 만들지 않는다. 학습 성공 후 저장 오류는 GPU 실행 queue로 되돌리지 않는다. 등록 key는 artifact+producer attempt이며 remote side effect의 exactly-once를 가정하지 않는다. profile/가격/목적지 변경은 기존 plan으로 실행하지 않는다.
- [ ] **4. 검증:** 새 시험 + `tests/test_evaluation_tracking.py tests/test_execution.py tests/test_reconciliation.py tests/test_runner_cli.py` 통과. 실제 서버 write 없이 fake provider/tracker로 deadline·retry·crash 지점을 deterministic하게 검사한다.
- [ ] **5. 커밋:** `feat: automate graph scheduling and repair tracking without retraining`.

### Task 9: Windows 보안 경계와 프로젝트·그래프 API/CLI 연결

**Files:** Create `desktop_workflows.py`, `tests/test_desktop_projects.py`, `tests/test_controller_entry.py`; Modify `desktop.py`, `cli.py`, `scripts/desktop_entry.py`, `desktop/src-tauri/src/main.rs`.

**Interfaces:** 새 desktop actions는 `projects_list/project_create/project_get/project_save/project_import_legacy`, `recipes_list/recipe_get/recipe_save/recipe_clone`, `dataset_import_source/dataset_import_preview/dataset_import_register`, `dataset_split_preview/dataset_split_commit`, `executor_profile_inspect`, `graph_validate/graph_plan/graph_submit/graph_run_get/graph_pause/graph_resume/graph_cancel`. strict 요청 모델로 요청 전체를 검사한다. revision/plan 변경은 409 상당 issue로 반환한다. Issue 필드는 code/path/message/node_id?/row_number?다.

**Produces:** native `executor_profiles_get/executor_profile_save`, `controller_start/controller_status` commands와 headless `--controller` broker. broker는 vault+승인한 profile revision을 읽고 sidecar `--controller`에 시작 stdin pipe로 주입한다. secrets는 DB/파일/env/argv/log에 남기지 않고 Python에는 새 keyring 의존성을 넣지 않는다. 실행 중 설정 변경은 다음 controller 시작에 적용하며 UI에 pause/재접속 절차를 표시한다. GUI 종료와 독립 실행, 상태는 DB heartbeat로 확인한다. CLI에는 `project-list`, `graph-plan`, `graph-submit`, `graph-status`, `graph-pause/resume/cancel`, `graph-work`를 추가하고 real submission/execution에는 plan 해시 및 별도 실행·전송 승인을 요구한다.

- [ ] **1. 실패 시험 작성:** 예제 없는 project→mapped data→recipe→dry plan; extra request fields/임의 command 거절; save/preview/plan의 provider mutation 0; native secrets 미노출/프로필별 보존; controller argv/stdout/stderr/DB에 synthetic secret 미포함; GUI proxy 종료 후 controller heartbeat 지속; 재시작은 같은 run을 채택하고 이미 running attempt를 재시작하지 않음.
- [ ] **2. 실패 확인:** `uv run pytest tests/test_desktop_projects.py tests/test_controller_entry.py -q`; 새 Rust tests도 실패 확인.
- [ ] **3. 구현:** 기존 desktop actions는 응답 계약을 보존한다. main의 controller mode만 별도 분기하고 legacy one-request 모드는 그대로 둔다. controller를 hidden process로 실행하며 임의 user-supplied executable/command를 허용하지 않는다. 여러 연결의 인증 환경을 global os.environ으로 서로 덮어쓰지 않고 broker의 단일 공용 auth scope를 사용한다.
- [ ] **4. 검증:** 새 tests + 기존 desktop/runtime/CLI tests, `cargo test --manifest-path src-tauri/Cargo.toml --locked --offline --release` 통과. subprocess 단위로 시작/정상 종료·중복 broker·깨진 입력을 검사하며 GPU/외부 데이터에 접근하지 않는다.
- [ ] **5. 커밋:** `feat: expose secure desktop workflow automation and controller lifecycle`.

### Task 10: 목적·데이터·레시피·그래프 중심 Windows 화면

**Files:** Create `desktop/src/api.ts`, `project-store.ts`, `views/projects.ts`, `views/recipes.ts`, `views/data-import.ts`, `views/graph-editor.ts`, `views/executor-profiles.ts`, `views/graph-runs.ts`, `components/recipe-fields.ts`; Modify `main.ts`, `style.css`, `workbench.ts`; tests는 해당 이름의 `desktop/tests/*.spec.ts`에 둔다.

**Tutorial:** 화면을 바꿀 때 `desktop/src/tutorial-steps.ts`의 단계 내용과 `data-tour` 표시를 함께 갱신하고 `desktop/tests/tutorial.spec.ts`를 통과시킨다.

**Interfaces:** view는 typed bridge wrappers만 호출하며 실행 스케줄을 소유하지 않는다. `ProjectDraft`는 저장 revision/dirty/완전성 issues를 갖고 변경 시 plan/preview 승인을 무효화한다. 기존 stage/method 필드는 recipe-fields로 점진적으로 추출한다. legacy 단일 학습/기록은 계속 접근 가능하다.

- [ ] **1. 실패 시험 작성:** 첫 화면 빈 프로젝트와 자유 이름/목적, save/reopen, optional examples; recipe clone/apply에 data/Pod/approval 미복사; CSV question/context/answer 매핑·인코딩 재선택·행 오류·분할 counts; 클릭과 keyboard로 직렬/병렬 card 추가/입력 연결; 지원 불가 연결과 metric 이유 표시; 하나뿐인 Pod는 대기로 표시; 변경된 profile/recipe/input은 이전 plan 실행 차단.
- [ ] **2. 실패 확인:** `npm test -- tests/projects.spec.ts tests/data-import.spec.ts tests/graph-editor.spec.ts tests/executor-profiles.spec.ts` → 새 화면/동작 assertions 실패.
- [ ] **3. 구현:** 현재 밝고 절제된 UI를 유지하고 업무 고정 문구를 프로젝트/실험으로 바꾼다. graph preview와 cards는 같은 draft에서 생성한다. 시작은 전체 plan의 목적지/데이터/최대 추정을 보여준 후 승인한다. run 화면은 controller/노드/추적 저장 상태·선택 근거·원본 유지 여부를 보여주며 polling만 한다. 일반 text metric은 의미적 품질 보장이 아님을 해당 선택 도움말에 설명한다.
- [ ] **4. 검증:** 전체 `npm test`, `npm run build` 통과. 기존 HF/S3/단일 job/history 흐름도 회귀 확인한다. modal/form 중첩, HTML escape, 키보드 focus, busy 상태의 중복 submit을 포함한다.
- [ ] **5. 커밋:** `feat: build general-purpose project and training graph workbench`.

### Task 11: 전체 인수·Windows 설치·문서와 공유

**Files:** Modify `scripts/verify_desktop_package.py`, `desktop/tests/native-smoke.mjs`, `scripts/build_desktop.py`, `.github/workflows/windows-desktop.yml`, `README.md`, `docs/CONFIGURATION.md`, `docs/WINDOWS_APP.md`, `docs/PROGRESS.md`; Create `docs/TRAINING_WORKFLOWS.md`, 안전한 acceptance receipts. 새 UI 기능이 반영된 앱 버전은 0.4.0으로 package/lock/Cargo/Tauri에 일치시킨다.

**Interfaces:** frozen 검증은 모든 worker Python bytes/두 GPU locks, 새 데이터 매핑/불변 graph plan, 그래프 controller entrypoint를 확인한다. 실제 CPU 모델 연산 검산은 별도 hash-lock 환경에서 필수 실행하고 receipt를 남긴다. numerical fixture는 production 승격 대상이 아니다.

- [ ] **1. 인수 시험 확장:** 예제 없이 새 목적/CSV의 계획 생성, mock diamond 재시작/취소, 잘못된 병합 계획 거절, synthetic 결과의 최상 품질/모델 등록 금지, headless lifecycle을 먼저 assertion으로 추가한다.
- [ ] **2. 전체 검사:** Python 3.12 locked sync → `uv run pytest` → `uv run ruff check src tests`; 별도 CPU 연산 script; 전체 UI/TypeScript/Vite/Rust; frozen-package 검증을 실행한다. 코드 변경이 생긴 범위의 실패만 고쳐 다시 검사한다.
- [ ] **3. 설치 인수:** 실제 개발 Windows의 전용 검사 폴더에 새 NSIS를 설치해 sidecar hash, 새 project/data/plan, vault masking, controller 동작·종료·재접속을 확인한다. 튜토리얼 실습 흐름(연습 데이터 미리보기·등록·dry-run 계획)과 `verify_desktop_package.py`의 연습 사본 확인도 수행한다. 실제 기관 데이터/유료 GPU를 쓰지 않는다. 깨끗한 두 번째 PC와 코드 서명은 별도 미검증으로 남긴다.
- [ ] **4. 기록·검토·공유:** 변경 전체 독립 review 후 필요한 수정과 관련 검사를 완료한다. PROGRESS에 실제 통과/미검증을 날짜별로 적고 소스/설치 파일 SHA256을 남긴다. credential scan 후 사용자 승인된 GitHub 공유 범위로 기능 브랜치를 push하고 PR을 첨부한다. 배포 시 검증한 exe/checksum과 정확한 commit을 연결하고 실제 업로드 digest를 확인한다.
- [ ] **5. 별도 live GPU 인수 준비:** 위 설치본/계획을 확정한 다음 더미 데이터, 복수 배정 Pod, 제한 시간·총 예상 비용·전송 목적지를 구체적으로 제시한다. 명시적 승인 전에는 자원을 시작/생성하거나 데이터를 전송하지 않는다. 승인 후만 실제 직렬·병렬·병합/재평가·회수까지 검사하고 빌린 Pod의 자동 lifecycle을 금지한다.

## Self-review and handoff

Root 자가검토 완료: spec 각 항목을 Tasks 1–11에 대조하고 schema/함수 이름, 5개 Review Focus의 시험, 기존 버전·자격 증명 경계를 확인했다. benchmark가 없는 상태에서 operation type을 먼저 import하는 순환/선행 의존성을 피하도록 task 경계를 정리했다. placeholder가 없으며 모델의 저장 형태와 의미적 종류를 구분한다.

사용자가 **기능별 담당 에이전트 구현 + 각 작업의 독립 검토**를 선택했다. root가 공용 인터페이스·통합·최종 검증을 맡는다. 병행 가능 범위는 위 dependency map을 따르며 선행 계약을 지킨다.
