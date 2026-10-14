# Live connection configuration

The legacy https://mlflow.k-biofoundrycopilot.duckdns.org address redirects to the canonical
https://mlflow.kbiofoundry.kr. Use the canonical host for API calls so bearer authentication is not lost on a cross-host redirect.
Keycloak's existing realm/token endpoint remains valid.

The local ignored `.factory/connection.json` refers to the existing credential files without copying values:

```json
{
  "tracking_uri": "https://mlflow.kbiofoundry.kr",
  "artifact_location": "mlflow-artifacts:/llm-tuning-factory",
  "oidc_token_url": "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/token",
  "oidc_client_id": "mlflow",
  "oidc_secret_file": "D:/workflow/.env",
  "runpod_env_file": "D:/workflow/rp.env",
  "pod_id": "q17zwk1tbndy45",
  "ssh_key_file": "C:/Users/user/.ssh/id_ed25519"
}
```

```powershell
uv run factory --connection-file .factory/connection.json doctor
uv run factory --connection-file .factory/connection.json doctor --write-test
```

The first command reads authenticated service metadata and Pod status. The second creates clearly marked infrastructure
diagnostic runs, uploads/downloads a small non-sensitive artifact, and verifies successful and failed terminal states.
No GPU starts and no training data is transferred by doctor. Validation, status and dry-run commands use lazy tracking
initialization and do not create remote experiments.

Existing experiments default to direct S3 artifact locations. The client originally failed with missing boto3 on that route.
The authenticated server artifact proxy has been verified instead, with a new experiment configured to use
`mlflow-artifacts:/llm-tuning-factory`. Existing experiments/server storage settings were not changed and object-storage
keys were not copied to the control plane. MLflow terminal URL printing is suppressed to preserve JSON CLI output and
avoid Windows console encoding errors.

Live diagnostic receipt: state/live-integration/connection-check.json. Three synthetic CPU workflow runs have also been
recorded through the real server; receipt: state/production-acceptance/acceptance.json. These are not model quality results.

## Generation from registered validation data

Set evaluation_profile.dataset_id and dataset_version together, plus a task kind, max_samples (1..1000) and
max_new_tokens (1..2048). Register the validation corpus using the same immutable registry as training data.
Its content hash is resolved into the manifest and checked again before remote transfer and on the GPU worker.
Both corpora require explicit export authorization for a real remote job.

After training, the backend delegates generation to the official LLaMA-Factory SFT prediction path with do_predict,
predict_with_generate and the newly trained adapter. It saves the native generated_predictions.jsonl and normalized
output/predictions.json, bound to validation_sha256. Remaining job time and cancellation apply to prediction as well.
The control plane only scores predictions whose validation hash matches the resolved corpus. No validation corpus means
task quality stays unevaluated. Mark dummy corpora with `dataset-register ... --synthetic`; synthetic results are explicitly
synthetic_only and do not enter ordinary production model candidates.

## Detached result recovery

Each remote attempt records its exact isolated remote path and Pod identity locally before launch. Once a stale job is
marked FAILED by recover, reconnect using the same Pod and strict SSH identity:

```powershell
uv run factory --connection-file .factory/connection.json reconcile JOB_ID --pod-id POD_ID --host PUBLIC_IP --port SSH_PORT --key-file PATH_TO_KEY
```

An unfinished worker retains its failed/reconciliation-needed state and GPU lease. A completed worker's manifest and
artifact hashes are checked before accepting the result; evaluation and tracking are finalized without retraining.
The lease is released transactionally with the reconciled terminal status. This requires SSH access to the persistent
volume; stopping a Pod preserves data but its SSH endpoint is unavailable until an authorized start.

## Approved real GPU session

The user explicitly approved starting the stopped kbf-fix H200 for at most 20 minutes, transferring only synthetic examples,
and stopping it after this test. This overrides the usual no-automatic-stop rule only for this explicitly authorized session.
Current API price was $5.29/hour (roughly $1.77 GPU for 20 minutes; storage is separate).
The session controller persists approval/deadline metadata, starts an independent local watchdog before the provider start,
and confirms EXITED on stop. It does not take ownership of any other user's running Pod.

The isolated runtime is /workspace/factory-acceptance/runtime; existing virtual environments are not modified.
After the first session stopped with EXITED confirmed, the user explicitly approved an additional 10 minutes
for BF16, native generation and recovery checks. The independent deadline guard also covers that session.
Actual dependency/FP16 failures are retained as failed runs. Matching Torch audio/vision versions and
prediction dependencies are pinned; BF16 and finite-gradient health checks replace the failed FP16 default.
See PROGRESS.md for the final collected results; code implementation alone is not GPU acceptance.
The public SSH key changed on restart. A first-use fingerprint was explicitly pinned for the API-reported Pod endpoint;
it was not independently verified by the provider. Subsequent connections enforce that pin and do not use AutoAddPolicy.
The pin/session files are local and ignored. The guard reduces risk but cannot stop billing during host/network failure;
provider-side billing controls remain separate.

The local Docker/Rancher startup command was rejected by automatic approval review with 'blocked by policy'. It was not
retried through another route. The GPU image build remains pending; the approved existing Pod uses a separate hash-locked
venv for the real worker acceptance test instead. Its actual package versions are recorded in runtime.json.

## Complete collection and native checkpoint restore

The separately authorized 20-minute follow-up stopped early with EXITED confirmed. All 40 original remote
artifact hashes match locally, and an MLflow adapter upload/download round trip matches. No retraining was
needed: run f0c5f94e8a04426c88eb8bbbd500115c is FINISHED after reconciliation.
Native checkpoint-10 restoration reached global step 12 with preserved training history; resumed run
27163d2c46ce4bd28fa23badee294cc5 is FINISHED. Receipt files are listed in PROGRESS.md.
The initial cancellation was late: GPU computation had already finished. This distinction is tagged in
MLflow and is not accepted as mid-training cancellation. The GPU-local observer is prepared for a later trial.

The later explicitly authorized 90-minute-maximum session completed in 25.3 minutes with EXITED confirmed.
Actual worker cancellation, expired-heartbeat reconciliation after controller termination, native checkpoint-10
to step 12 resume and artifact recovery all pass. Resumed run e41e7b766172475fbdc58e74919dbc7c is FINISHED;
its preceding run 4e09a31fca8442428f44a75fd816d044 is KILLED. The actual worker result and receipt are attached
to both runs. All 34 final remote immutable hashes and 51 final local inventory entries verify, including
the MLflow adapter round trip. No actual institutional dataset was used.
