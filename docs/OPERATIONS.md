# Installation and operations

## Local control plane

Use Python 3.12 and `uv sync --python 3.12 --extra dev --extra tracking --locked` from the project root.
MLflow is optional: omit --extra tracking when unused. Keep all runner processes on one host and use one state directory.
The CLI reads supported settings from .env in the current working directory, without overriding existing
process environment variables. Use --env-file PATH for another local file. Never commit a populated file.
The distributed-PC setup and first installation steps are in README.md. Each PC has its own queue/leases;
use separate Pods or one designated operator per Pod. MLflow does not coordinate cross-PC GPU ownership.

```powershell
uv run factory --state-dir state/demo dataset-register dummy 1 examples/dummy.json
uv run factory --state-dir state/demo batch configs/ai/paper_extraction.yaml configs/ai/labnote_generation.yaml configs/ai/workflow_classification.yaml --submit
uv run factory --state-dir state/demo work --drain
uv run factory --state-dir state/demo status
uv run factory --state-dir state/demo logs JOB_ID
uv run factory --state-dir state/demo compare JOB_ID BASELINE_JOB_ID
```

Global options precede the command. For concurrent execution, launch multiple `work` processes with a shared state directory
and different Pods; increase both max_concurrent and max_gpus deliberately. A Pod is exclusively reserved for a job.

## MLflow

Reuse your existing authenticated tracking server by setting MLFLOW_TRACKING_URI and, when applicable,
MLFLOW_TRACKING_TOKEN (or the authentication variables required by your server). An explicit --tracking-uri overrides the URI.
The tracking adapter uses the official MlflowClient interfaces:
https://mlflow.org/docs/latest/api_reference/python_api/mlflow.client.html

For an isolated local test only, the legacy filesystem store works with MLFLOW_ALLOW_FILE_STORE=true and a file:/// URI.
Tests use a temporary directory and the installed real MLflow client. Shared deployments should use a SQL tracking backend,
authenticated network access and a separately backed-up artifact store. Install the full MLflow server distribution in its
own environment if needed; the control plane only installs mlflow-skinny. Do not expose an unauthenticated tracking server
to the public internet. The platform does not change or start an existing tracking server.

The existing canonical project documents a Keycloak-protected server. Non-secret endpoint settings are in
configs/integrations/kbf.env.example. Its existing scripts/mlflow_util.py refresh contract is reused by auth.py:
set MLFLOW_OIDC_TOKEN_URL, MLFLOW_OIDC_CLIENT_ID and MLFLOW_OIDC_CLIENT_SECRET in the process environment/secret manager.
The client obtains an HTTPS client-credentials token before creating the tracking client and refreshes it in a daemon
thread before expiry. MLflow reads MLFLOW_TRACKING_TOKEN per request. Authentication failures are not silently ignored;
token/client-secret values are never printed. Files outside the local .env are loaded only by explicit reference.
Authenticated production tracking and artifact round trips have now been verified; see LIVE_INTEGRATION.md.

Each attempt creates a run and logs the resolved manifest, training YAML, metrics, logs and output/checkpoints.
Job metadata stores the current run ID; attempts/N/outcome.json preserves older IDs. Production candidates are registered
with approval=pending and compatibility tags. Model registration is separate from deployment and human approval.

## GPU image

First start your Docker daemon. No image has been built in the current environment.

```powershell
docker build -f containers/Dockerfile -t YOUR_REGISTRY/llm-tuning-factory:0.1.0 .
```

The recipe uses a digest-pinned Python 3.12 base and containers/gpu-requirements.lock with hashes, including
LLaMA-Factory 0.9.5, PyTorch 2.7.1 and bitsandbytes 0.48.1. CUDA user-space libraries come through PyTorch's wheel dependencies;
the host needs a compatible NVIDIA driver and container runtime. No image compatibility/GPU memory claim has been verified.
Build/publish only after reviewing the resolved image, then use its registry digest for managed Pod creation.
Install no separate training engine on the control plane.

The image starts key-only SSH. Supply PUBLIC_KEY or mount /run/secrets/authorized_keys. No password login is enabled.
Attach a RunPod Network Volume at /workspace. Cache, checkpoints and attempt directories reside there.
Verify the SSH host fingerprint via a trusted channel before adding it to known_hosts; automatic trust is disabled.
Direct TCP SSH is supported; the RunPod SSH proxy is not supported in this initial implementation.

Regenerate the Linux dependency lock deliberately with:

```powershell
uv pip compile pyproject.toml containers/gpu-requirements.in --python-version 3.12 --python-platform x86_64-manylinux_2_28 --generate-hashes --output-file containers/gpu-requirements.lock --no-header
```

The isolated existing-Pod venv has been GPU-tested with BF16 LoRA/native generation. The container image itself
has not been built or tested. See PROGRESS.md for the interrupted complete-artifact collection and remaining checks.

## Approved existing Pod — first real test

Before running this section: get data-owner approval for external transfer, confirm GPU billing authorization,
publish the image containing the same source hash, and attach/verify the persistent network volume.
These commands are instructions; they were not run against a real Pod during implementation.

1. Edit a copy of the example AI YAML for your supplied dataset/version and the Pod's exact GPU type/count and per-GPU price.
2. Register approved data with a **new version** and --export-allowed.
3. Set RUNPOD_API_KEY in the shell. Confirm the Pod is running and record publicIp and portMappings["22"].
4. Submit --real, then explicitly authorize execution and transfer:

```powershell
uv run factory dataset-register approved_data 1 PATH_TO_APPROVED_JSON --export-allowed
uv run factory plan PATH_TO_AI_YAML --real
uv run factory run PATH_TO_AI_YAML --real --submit
uv run factory work --pod-id POD_ID --host PUBLIC_IP --port SSH_PORT --key-file PATH_TO_PRIVATE_KEY --approve-execution-and-transfer
```

The API response's price, GPU and endpoint must match the configuration. Transfer is blocked for unapproved data or
simulation manifests. Existing borrowed Pods are left running: their owner remains responsible for billing and shutdown.
Do not use an unrelated SSH host even if you control its key; the live provider mapping is checked before transfer.

## Managed paid Pod

Prepare a JSON request matching the official REST API:
https://docs.runpod.io/api-reference/pods/POST/pods
Required by this platform: networkVolumeId, digest-pinned imageName, gpuCount and your desired gpuTypeIds/SSH ports.
For example, copy/edit a local JSON with cloudType=SECURE, computeType=GPU, ports=["22/tcp"], volumeMountPath=/workspace,
an existing Network Volume in the requested data center, and PUBLIC_KEY in env. Never commit runtime secrets.

```powershell
uv run factory pod-create PATH_TO_POD_REQUEST_JSON
uv run factory pod-create PATH_TO_POD_REQUEST_JSON --hourly-quote-usd TOTAL_POD_HOURLY_PRICE --timeout-seconds 3600 --approve-paid-creation
uv run factory guard --watch
```

The first command is dry-run. The second is an explicit paid action. Run the guard as a supervised, independently
monitored process on the control-plane host before provisioning or immediately afterward. In another shell, use
`work --pod-id ...` as above. Managed Pods are stopped when the invocation finishes (or the drained real queue is empty),
with the verified network volume retained. `pod-start --approve-paid-start` supports owned allocations whose deadline
has not expired. `pod-stop` checks ownership and persistent volume. There is no destructive cleanup CLI.

The guard checks the allocation deadline and live total Pod price every 15 seconds. It also guards idle managed Pods.
This is not a hard provider spend cap. If the control plane/guard/network is down, billing may continue; use provider
billing alerts and an independently operated supervisor. Storage and egress charges remain after compute stops.
Managed allocation decisions are serialized locally. A creation request with unknown outcome blocks further allocations:
find its unique factory-REQUEST_ID name in RunPod and reconcile state/managed-pods/requests before retrying.
Never retry an uncertain POST blindly, and never delete the request evidence to bypass reconciliation.

## Failure, cancellation and restart

```powershell
uv run factory cancel JOB_ID
uv run factory recover
uv run factory logs JOB_ID
```

The live worker checks cancellation and kills the training subprocess group. CLI cancel requests cancellation for an active
job; it is not proof that disconnected remote work stopped. Heartbeats expire after 120 seconds by default.
Recovery marks stale jobs failed **without** releasing their GPU leases. Inspect the Pod/process and stop or otherwise
confirm the old attempt ended. Preserve/retrieve the checkpoint directory from /workspace/factory/JOB_ID/ATTEMPT_ID.
Then reconcile the local artifact inventory and use:

```powershell
uv run factory release-lock JOB_ID --confirm-execution-stopped
uv run factory resume JOB_ID --checkpoint FULL_PATH_TO_ORIGINAL_JOB_CHECKPOINT
uv run factory work
```

For real jobs use `work --pod-id ... --approve-execution-and-transfer` again. `resume` without checkpoint is an explicit
retry from initialization; use it only if the output directory is suitable for a fresh engine run. Changed source/config
requires a new job. Automatic retries apply only to transient capacity/rate-limit failures and are bounded by max_attempts.
The runner does not retry arbitrary training failures. Re-run `work` after capacity is restored; it does not busy-wait.

After a controller interruption, automated reconnection/download of a previously detached attempt is not implemented.
Use the retained network-volume files for manual recovery, preserving the worker's hash inventory; do not release a GPU
lease while an old worker could still be active. No existing user Pod or unrelated data should be removed during recovery.

## Optional real GPU integration test

Set FACTORY_APPROVED_GPU_TEST=yes only after explicit approval. Also set FACTORY_GPU_TEST_STATE (an isolated prepared
state directory with one queued real job), FACTORY_POD_ID, FACTORY_SSH_HOST, FACTORY_SSH_KEY, FACTORY_SSH_PORT and
RUNPOD_API_KEY. Run `uv run pytest tests/integration/test_real_gpu.py -m gpu -v`.
Without the opt-in variable the test is skipped; a skipped test does not count as GPU acceptance.
