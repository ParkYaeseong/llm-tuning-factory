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
profile. Scoped re-review of these fixes is pending.

Native settings contract for Task 6B: `provider: "ssh_server"` with string values under `server_`-prefixed keys
(`server_ssh_host`, `server_ssh_port`, `server_ssh_username`, `server_ssh_fingerprint`, `server_ssh_key_file`,
`server_remote_root`, `server_selected_gpu_uuids` as a JSON-encoded array, `server_total_hourly_cost_usd`), so the
inactive Pod's `ssh_key_file`/`ssh_fingerprint` stay separate. An explicit operator hourly cost is required; 0 is
allowed. Server identity is the SHA-256 of the lowercased /etc/machine-id; lock aliasing also treats any observed
GPU UUID overlap as the same server, and an SSH lock without an observation blocks. The cross-provider lock policy
is deliberate: while any SSH job or preparation reservation exists, RunPod claims/preparation are blocked and vice
versa, matching the v1 default of one concurrent job (`Policy.max_concurrent=1`). Distinct-resource concurrency is
Task 8.

Deferred: the key identity is a hash of the resolved local key-file path, so moving the key breaks recovery of old
jobs and replacing it at the same path goes undetected (consider the public-key fingerprint later); the server
worker binds an unbound real job to its server before claiming, approved by `--approve-execution-and-transfer`;
graph ResourceFacts still allows a 0 RunPod price (Task 8); `JobStore.resource_blocked` is unused.

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
Actual verification: **711 passed, 1 skipped** in 64.32s (59 new cases); Ruff passes. Pending: Rust native settings and
action allowlist, TypeScript UI and docs (6B-2/6B-3). No real SSH server, GPU or MLflow server was used.

Task 6B-2 (Windows native settings and connection screen): native settings save `provider` and the eight public
`server_*` strings, refuse an unknown provider or `auth_mode` and a selection that is not a JSON array of at most 8
strings, and allow `server_inspect`/`server_facts`/`runtime_reservations`/`runtime_release`. A pure MLflow filter
withholds the OIDC URL, client ID and secret (and, for `none`, the token) from the sidecar; vault values stay saved.
The connection screen adds a provider card, GPU server fields (no RunPod key or Pod ID), GPU inventory selection
saved as a JSON string, server facts applied with a per-GPU price that never multiplies back below the server total
(a one-ulp upward correction where float division undercuts it, about 3% of cent-valued cases), inspect → approve →
`runtime_prepare` with the unmodified inspection, a reservation list whose uncertain entries are released only after
an approval stating the remote preparation stopped (refreshed automatically after a prepare error), MLflow
“인증 없음” with a trusted-network note, auth-method connection results and an unverified-training row, provider-specific
overview, plan and approval texts, a disabled start for a null destination hash, `destination_sha256` on submit and
a job destination column. Saving a changed provider, `server_*` field or selection clears the plan, inspection,
reservations and reports. The three `data-tour` anchors render once for both providers, and no remote action runs
on page load, navigation or provider/auth switching. The MLflow URI label is now “MLflow 서버 주소” so field names
stay unique. Actual verification: Playwright **36 passed** (13 new in `desktop/tests/connections.spec.ts`;
`workspace.spec.ts` unchanged), `npm run build` (tsc + Vite) passes, Rust **8 passed** (4 new), Python **711 passed,
1 skipped**, Ruff passes. Unverified: docs (6B-3), installer acceptance (Task 11), the real Tauri/WebView2 shell and
Credential Manager (the UI is tested against a mocked bridge) and any real SSH server, GPU or MLflow server.
Known carry-over: as for `token`, `settings_get` under `none` omits the saved OIDC URL and client ID, so switching back
to OIDC shows the defaults until re-entered.

Remaining Tasks 6A/6B and 7–11: general SSH GPU servers, model-operation CPU acceptance,
persistent multi-Pod controller/tracking repair, secure desktop API/broker, Windows UI/installer and Git sharing.
These foundations are not end-to-end workflow, Windows 0.4.0 or live GPU acceptance. Actual serial handoff,
multi-Pod concurrency and merge/reevaluation on GPU require separate concrete resource/data authorization.

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
