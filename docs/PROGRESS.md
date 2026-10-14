# Progress and acceptance status — 2026-10-14

## Latest live verification (supersedes the earlier baseline below)

Fresh full suite after recovery hardening: **52 passed, 1 skipped**. Ruff src/tests passes; wheel build succeeds.
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
The first authorized 20-minute session stopped with EXITED confirmed before its deadline. A separately
authorized additional 10-minute session is recorded in .factory/gpu-session.json; no further extension is authorized.
Existing runtimes were preserved; all remote changes are under /workspace/factory-acceptance.

Real failed attempts exposed an incompatible optional torchaudio version, missing prediction dependencies,
and FP16 non-finite gradients with all-zero adapter B weights. Those attempts are FAILED and are not accepted tuning.
The Linux lock now pins matching torch/torchaudio/torchvision 2.7.1/2.7.1/0.22.1 and prediction dependencies.
BF16 is the default; the worker rejects non-finite loss/gradient metrics and writes training-health.json.
The source-hash gate also correctly rejected a stale remote worker. One provider read temporarily omitted SSH mappings.

Registered validation generation and detached result reconciliation are now implemented and CPU-tested.
Actual healthy BF16, generation, complete collection and checkpoint resume are recorded below.
Mid-training forced interruption, QLoRA, multi-AI GPU batch, numerical reproducibility and Model Registry
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

### Collection and native checkpoint restore follow-up

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
