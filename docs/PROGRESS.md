# Progress and acceptance status — 2026-10-14

## Delivered implementation

- Strict AI configuration, conservative Qwen model/template catalog and pinned model/tokenizer revisions.
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

## Actual verification

Final full suite: **33 passed, 1 skipped**. The skipped case is the opt-in real GPU integration test.
`uv run ruff check src tests` passes. `uv sync --python 3.12 --extra dev --extra tracking --locked` succeeds.
The control-plane dependency graph contains 59 installed packages. GPU dependencies were resolved into a Linux hash lock,
not installed. `uv build --wheel --out-dir dist` succeeds and produces
dist/llm_tuning_factory-0.1.0-py3-none-any.whl; this is not a GPU image build.

Coverage includes configuration rejection, tamper detection, invalid data, generated LoRA/QLoRA YAML,
real CPU subprocess exit/timeout/cancellation, checkpoint completeness, atomic parallel claims/submissions,
GPU budget vs concurrency, recovery retaining leases, retry exhaustion, simulation safety, candidate metadata,
official REST request shapes through HTTP mocks, managed-budget lifecycle, SFTP artifact transport, isolated remote attempts,
remote interruption uncertainty, worker integrity gates, evaluator metrics and real local MLflow logging/artifact retrieval.
Mock remote/adapter fixtures are test-only bytes, not trained models or GPU evidence.

Additional subprocess CLI acceptance used state/cli-acceptance with the real installed MLflow client and temporary local
file tracking backend (explicit MLFLOW_ALLOW_FILE_STORE=true). Verified:

- Default run produces a dry-run plan without training.
- Three AI configurations are submitted and complete as simulations with three distinct MLflow run IDs.
- Each result has simulation=true and quality_status=not_evaluated; only pipeline_completed is measured.
- A deliberate repeated submission preserves the same resolved manifest hash and has a new job/run identity.
- Comparing these repeated simulations reports pipeline_completed delta=0; this is not a model quality result.

The local acceptance receipt is state/cli-acceptance/acceptance.json (ignored by Git). No external datasets were sent,
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
