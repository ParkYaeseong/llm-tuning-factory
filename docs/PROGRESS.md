# General-purpose workflows — implementation in progress, 2026-10-19

User approved the [design](superpowers/specs/2026-10-17-general-training-workflows-design.md) and
[implementation plan](superpowers/plans/2026-10-17-general-training-workflows.md), including task implementers
and independent reviews. Work continues on codex/general-training-workflows; the released Windows 0.3.0
installer remains unchanged until new acceptance.

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
  After Task 8.5, the current receipt is `d796f3ed…7f1d` (29/29 checks, source `0888d176…ceab`; see "Task 8.5
  graph controller and end-to-end recovery / Task 8 complete (software)").

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
    restored afterwards. v1 trackers never set them. The mlflow client reads both per request, so the worst case of
    one create is about 3 × 60 s plus backoff, far below the 600 s claim.
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

Commit `feat: add the strict desktop workflow channel for projects, recipes and data`, written on `f75e628` in the
worktree `codex/task-9.1` while 8.5 was still uncommitted (9.1 touches none of 8.5's files). It implements
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
- `FORBIDDEN_FIELD` is applied with `fullmatch` to each field name and each of its snake/camel-case words
  (`command_like_field`). A substring search would flag `RecipeSpec.description` ("script") and 9.3's
  `approve_execution` ("exec"). Registration also refuses such models, and models that are not frozen `StrictModel`s
  with `extra="forbid"`.
- Credential refusal is broader than the seven names: their environment spellings (AWS, `HUGGING_FACE_HUB_TOKEN`,
  `MLFLOW_TRACKING_PASSWORD`; case-insensitive), a non-boolean `<secret>_saved` flag and pasted private-key markers
  in settings or profile fields. After model validation, a credential-named key inside free-form JSON (the
  project's graph draft) is refused as `credential_field`, so it is never stored.
- Envelope errors raise a fixed `invalid workflow request (<envelope fields>)`, never pydantic's text (it echoes
  inputs). The root must be absolute; profile IDs are ≤64 characters and unique.
- `issues_from_validation(..., data=None)` follows the request data to drop pydantic's union-member tags (for example
  `constrained-str`) from pointers, and escapes `~`/`/` (RFC 6901).
- `CONFLICTS` adds `immutable version already exists` → `version_exists`. Re-registering a mapped import's ID and
  version with other bytes fails in `immutable_write` of the provenance pointer, not with the registry's text.
  `INVALID_CODES` adds `LOCAL_FILE_ONLY` → `local_file_only`.
- `local_file` also refuses DOS device names (`CON`, `NUL.csv`, `COM1`, `CONOUT$`), mixed `\/` UNC prefixes and NUL
  bytes. New codes and Korean texts: `file_unreadable`, `source_unknown`, `dataset_unknown`, `preview_unknown`,
  `legacy_profile_missing`/`legacy_profile_invalid`, warning `legacy_dataset_missing`, `credential_field`.
- `dispatch_action` scrubs scoped secret values from any result (defence in depth for 9.2's two scoped actions).
- Reads on an empty state create no definitions database.
- `project_import_legacy`: project name = `ai_id`, purpose empty, task type = `workflow_id`; train binding plus a
  validation binding from the evaluation profile. A dataset that is not registered becomes the warning
  `legacy_dataset_missing` and no binding. A retry returns the current project; a changed profile under the same
  request ID is `request_reused`.
- `projects_list[].datasets` is the binding list. Splitting the practice dataset itself is allowed: its outputs
  inherit `export_allowed: false`, so a real run still refuses them.

Actual verification (worktree `D:\llm-tuning-factory-wt91`, main venv with `PYTHONPATH` on the worktree `src`;
`tuning_factory.__file__` resolved into the worktree):
- RED, before any `src` edit: 32 of the 33 new cases failed. 30 failed on the missing module (`pytest.fail`, not
  collection). Two failed because `desktop.main` had no workflow branch (legacy answered `unsupported desktop
  action`). The legacy-envelope regression guard passed. The device-name check was added later, test-first: RED
  (`<tmp>\CON` reached `open()`), then GREEN.
- Mutations through a pytest plugin in `%TEMP%` (no `src` edit): UNC allowed, scope check removed, credential check
  removed, reserved guard removed, no secret scrub, pointers without data, no free-JSON credential scan, extra fields
  relabelled, foreign-module registration allowed, every refusal classified `invalid`, random legacy recipe IDs. Each
  one fails at least one test.
- GREEN: the plan's command (`test_desktop_projects`, `test_desktop`, `test_desktop_tutorial`, `test_projects`,
  `test_recipes`, `test_dataset_import`, `test_dataset_splits`, `test_legacy_migration`) gave **153 passed**.
  Every desktop test (`tests/test_desktop*.py`, seven files) gave **145 passed**. The full suite gave **1316 passed,
  1 skipped** (1283 + 33), on the final tree and on a run before the last two test-first edits. The known
  `test_jobs.py::test_recovery_does_not_release_unknown_remote_execution` flake did not occur.
- Ruff (`src tests`) is clean. `check_secrets.py` reports 0 findings over 761 file versions (with this change
  staged). `git diff --exit-code containers/ pyproject.toml uv.lock` is clean.
  Every changed file is CR-free. A subprocess test shows the channel imports no torch, peft, transformers,
  safetensors, huggingface_hub or keyring.
- CPU acceptance (`-I`, the worktree's script and source) exited 0, and all **29** checks passed. Receipt SHA256
  `dd2dc480b45a5d34cf1d6d05652e203301f417950371fb91251f608817a6ec22`, source_sha256
  `3915741efe02876d5889b304626e36841b00a64591eedac3289c0ea7b3f232e0` (equal to `source_hash()` of the committed
  `src`), lock_sha256 `5883aae629da2fd83aa4bf78c291593e6e14ebda7d2dd20e465d435984a91437`. Max errors are
  unchanged (merge delta 7.78e-9, materialize delta 9.17e-9, parent→child 6.42e-9, logits 3.35e-8/2.98e-8).
  `model_downloaded`, `gpu_used`, `institution_data_used` and `production_promotable` are false. It supersedes
  8.4's `2afbd0b8…88f8`; 8.5's commit changes the source hash again, so root reconciles the receipt pointer.

Not verified or still open:
- No Rust, Playwright or UI run: 9.1 changes no TypeScript or Rust. The native side (9.5) does not send workflow
  requests yet, so the envelope is exercised only from Python.
- `local_file` checks strings only. A local symlink or junction to a share, or a mapped network drive letter, is not
  detected.
- No network, GPU, SSH, model download, MLflow, provider call or institution data was used.

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
checksums pass at https://mlflow.kbiofoundry.kr. The legacy URL redirects here. Receipts are
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
