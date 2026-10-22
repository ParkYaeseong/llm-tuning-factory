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

## Training graphs and the background controller (Windows 0.4.0)

- Graph CLI operation (`project-list`, `executor-profile-inspect`, `graph-validate`, `graph-plan`, `graph-submit`,
  `graph-status`, `graph-pause`, `graph-resume`, `graph-cancel`, `graph-rearm-tracking`, `graph-work`) and the operator
  lock release for graph runs: [TRAINING_WORKFLOWS.md](TRAINING_WORKFLOWS.md) (Korean), sections 9 and 12.
- Starting, stopping (drain) and restarting the desktop background controller, and stopping it before an update or
  uninstall: [WINDOWS_APP.md](WINDOWS_APP.md).
- File formats, the CLI profiles file, credential files and the controller environment: [CONFIGURATION.md](CONFIGURATION.md).
- An uncertain graph attempt is released with the same `release-lock JOB_ID ATTEMPT_ID --confirm-execution-stopped` as a
  single job, only after confirming on the Pod or server that the attempt stopped; `status --job-id JOB_ID` shows the job.
- Point every graph command that shares borrowed resources with the app at the app's state with `--state-dir`. GPU locks
  live per state directory: `graph-work` refuses to start beside the app's controller (exit 3) only with that state; with
  the default `./state` it starts a second controller that can dispatch to the same borrowed Pod or server.
- Submit with `graph-submit PLAN_SHA256 --request-id plan-<PLAN_SHA256>`, the request ID the desktop uses: `plan-`
  followed by the same plan hash, typed without the angle brackets. A plan runs at most once: another request ID for an
  already submitted plan is refused (`plan_submitted`, naming the existing run).

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

### Unauthenticated local or internal MLflow server

A local or internal-network server without authentication is supported. In the desktop app choose the authentication
method "인증 없음 · 로컬·내부 서버" and enter the server address. In the CLI leave `MLFLOW_TRACKING_TOKEN` empty and
clear the three `MLFLOW_OIDC_*` values (`.env.example` pre-fills the OIDC token URL and client ID; the secret is empty). The connection check
then reports `mlflow_auth: none`, shown in the desktop as "확인됨 · 인증 없음"; `mlflow_authenticated_read` is true only
when real credentials were used.

The project environment installs only `mlflow-skinny` (client), which cannot run a server. Run the full `mlflow` package
separately, for example in a throwaway tool environment or its own venv, at the version this project pins (3.17.1 in `uv.lock`):

```powershell
uvx --from mlflow==3.17.1 mlflow server --host 127.0.0.1 --port 5000
# .env: MLFLOW_TRACKING_URI=http://127.0.0.1:5000
```

- One rule applies to the CLI and the desktop app alike: credentials count as configured when `MLFLOW_TRACKING_TOKEN` is set, or when both
  `MLFLOW_OIDC_TOKEN_URL` and `MLFLOW_OIDC_CLIENT_SECRET` are set; then the tracking URI must be `https`. The OIDC URL alone
  (the desktop pre-fills it) does not count: the app starts, and only MLflow actions fail with the "OIDC 인증을 선택했지만
  …" message below. The check runs in the tracker, `doctor`, the desktop history view and service start; `work` and
  `run --mock` check the tracking URI and any configured artifact location before claiming. An `http` artifact location is
  refused whether configured, taken from the existing experiment, or assigned by the server to a run. Without
  credentials, plain HTTP is allowed. With credentials set, even local stores are refused: so a `file:` or `sqlite:` tracking URI is also refused while they are set; clear
  them when using a local store.
- If an OIDC address is set but the client ID or secret is missing, MLflow actions stop with
  "OIDC 인증을 선택했지만 클라이언트 인증값이 없습니다. 인증값을 입력하거나 토큰·OIDC 값을 비우세요(앱에서는 ‘인증 없음’ 선택)." Fill the
  credentials or clear the OIDC values. The CLI checks this before claiming (`work`, `run --mock`), and the desktop checks it
  before submit and execute. In the CLI, `.env.example` pre-fills the OIDC token URL and client ID, so clear them for a no-auth server.
- The default `FACTORY_MLFLOW_ARTIFACT_LOCATION=mlflow-artifacts:/llm-tuning-factory` stores artifacts through the
  tracking server's artifact proxy. MLflow 2 and later `mlflow server` normally serves that proxy by default, but confirm
  with `mlflow server --help` for your installed version (`--serve-artifacts` defaults to on in 3.17.1). The
  `mlflow-artifacts:` location needs an http(s) tracking URI. With a proxy-less server, uploads of logs and models fail;
  set an artifact location the server supports.
- Anyone who can reach an unauthenticated server can read its records and artifacts. Bind it to `127.0.0.1` or use it
  only on a trusted internal network, and never expose it to the public internet. This project did not verify a real
  unauthenticated MLflow server yet; only mocked connections were tested.

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
has not been built or tested. Complete artifact collection and native checkpoint restoration have since
been verified, including controlled mid-training cancellation and subsequent native resume. Abrupt Pod loss
during checkpoint writing is not covered by that test. See PROGRESS.md for exact evidence.

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
During acceptance, the user may explicitly authorize starting and stopping a named existing test Pod for a bounded
session. That exception applies only to that Pod/session; it grants no ownership of other borrowed Pods.
Do not use an unrelated SSH host even if you control its key; the live provider mapping is checked before transfer.

## Existing Linux GPU server over SSH

For an institution server (for example a BOP-style Linux host) the app connects with SSH and runs the same training
worker directly on the server, in the same way as the existing Pod path. It ships in the Windows 0.4.0 installer (the
0.3.0 installer did not have it). It was tested only with fake SSH responses; no real SSH server or GPU was used.
Nothing below is authorization to connect, transfer data or spend: each step needs the owner's approval.

### What the app never does

It never creates, starts, stops or deletes a server, and never touches Docker or Slurm. It does not use sudo or apt, does not
change the system Python, and does not trust a host key it merely observed. Private keys stay on your PC. No credentials are
written to manifests or profiles. Data transfer and execution share `--approve-execution-and-transfer`; environment preparation uses its own `--approve-preparation`.

### Prerequisites

- Linux, a readable `/etc/machine-id`, and NVIDIA GPUs visible to `nvidia-smi` (MIG partitions are rejected; one MIG-enabled GPU makes the whole inspection fail).
- `python3` (3.8 or newer) with `python3 -m pip`, `flock` and a C compiler (`cc`). Missing tools are reported, never installed.
- An absolute work folder, for example `/home/USER/factory`, that is writable. The probe rejects the folder if the path or any parent (such as `/home`) is a symlink; enter the
  resolved real path. Only ASCII letters, digits and `_ . / -` are allowed in the path.
- SSH key authentication with an OpenSSH-format private key without a passphrase (no agent, no PuTTY `.ppk`). Create the key
  pair yourself and ask the administrator to register the public key; never ask for a private key.
- The server's `SHA256:` host key fingerprint from the administrator by a trusted route. Ask for the ED25519 host key
  fingerprint (paramiko prefers ED25519). Root is not required.
- Preparation always needs PyPI, or a pip/uv index mirror configured on the server: it runs `pip install ... uv==0.11.23` and
  `uv pip sync`. Python 3.12 is downloaded only if it is missing. Only the Hugging Face model download can be avoided, by
  pre-filling `hf-cache/` under the work folder.
- An operator-entered whole-server hourly cost (0 is allowed for internal servers; it is not a cloud price).

Under the work folder the app keeps a private runtime for each app source version (`runtime/`), job files (`jobs/`), the model
cache (`hf-cache/`), downloaded Python builds (`python/`) and a uv cache (`uv-cache/`). pip runs with no cache. The selected GPU UUIDs are bound with
`CUDA_VISIBLE_DEVICES`. The first preparation downloads dependencies and can take a long time.

### Concurrency and locks

One factory job runs per server, and the default policy allows one GPU per job (`Policy.max_gpus=1`; the desktop app
fixes this at one, so select exactly one GPU there). Registering the same server under another address or profile does not
bypass the lock (identity is derived from the machine ID and shared GPU UUIDs).

Cross-provider quarantine: because a RunPod Pod and an SSH server cannot be proven distinct, any held RunPod job lock
blocks SSH claims and SSH preparation, and any SSH job lock or SSH preparation reservation blocks RunPod claims and RunPod
preparation. RunPod preparation records no reservation, so it does not block SSH. Locks live in the local state directory:
another PC, or a different `--state-dir`, is not blocked, so coordinate with other users of the server.

### CLI flow

Create a server profile JSON (fields in [CONFIGURATION.md](CONFIGURATION.md)), then:

```powershell
uv run factory server-inspect server.json
uv run factory server-runtime server.json
uv run factory server-runtime server.json --prepare --approve-preparation --training-stage sft
uv run factory dataset-register approved_data 1 PATH_TO_APPROVED_JSON --export-allowed
uv run factory plan PATH_TO_AI_YAML --real --server-profile server.json
uv run factory run PATH_TO_AI_YAML --real --submit --server-profile server.json
uv run factory work --server-profile server.json --approve-execution-and-transfer
uv run factory reconcile JOB_ID --server-profile server.json
uv run factory doctor --server-profile server.json
```

1. `server-inspect` is read-only. It prints the observed GPU UUIDs, names and memory; copy the UUIDs you may use into
   `selected_gpu_uuids`.
2. `server-runtime` without `--prepare` checks the private runtime; like `--prepare`, it needs at least one selected GPU UUID
   in the profile. `--prepare` installs it and requires
   `--approve-preparation`. `--training-stage` selects the stage environment (default `sft`).
3. `plan --real --server-profile` makes one read-only observation and prints the destination receipt. The AI YAML's GPU type and
   count must equal the selected GPUs, and its per-GPU price times the count must be at least the declared whole-server cost. `run ... --submit --real --server-profile`
   freezes that receipt for the job. Without `--submit` the command only prints the plan.
4. Preparation uses `--approve-preparation`; execution and data transfer share `--approve-execution-and-transfer`.
   `work --server-profile` fails without it. With it, the worker transfers the approved
   data to the server and runs the job there. Only unbound jobs and jobs whose receipt matches the current
   destination are chosen; others wait without blocking it. `--drain` keeps working until the queue is empty.
   `--pod-id` cannot be combined with a server profile.
5. `reconcile JOB_ID --server-profile` re-attaches to an existing attempt to collect results, logs and progress. It uses
   the job's original receipt and refuses a different server, key, work folder or GPU selection, but tolerates drift in
   unrelated GPUs and operator cost. It never launches work. `doctor --server-profile` runs the read-only connection
   check; it cannot be combined with `--pod-id`. `--connection-file` is a global option placed before the command
   (`uv run factory --connection-file FILE doctor`); a connection profile with `provider: ssh_server` replaces
   `--server-profile` for `doctor`, `plan`, `run`, `work` and `reconcile` only. `server-inspect`, `server-runtime` and
   `server-runtime-release` always take the profile file as an argument.

### Uncertain preparation reservations

A preparation that loses its connection leaves an UNCERTAIN reservation whose remote end is unknown, and a preparation
whose app was killed can stay PREPARING. Both keep blocking the same server (and RunPod claims and preparation) until released. First confirm on the server that the preparation process has ended, then:

```powershell
uv run factory server-runtime-status
uv run factory server-runtime-release server.json RESERVATION_ID --confirm-preparation-stopped
```

`server-runtime-status` lists reservations in the local state directory. Release refuses without
`--confirm-preparation-stopped` and only for a reservation made with the same server settings. In the desktop app this is
the "준비 예약 확인" button and "원격 준비 종료 확인 · 예약 해제" with an approval dialog. Do not release while the
preparation may still run.

### Limits

Cost is the operator-entered value, not a measurement; there is no observation of other users' load on the server, and the
app does not stop the remote worker automatically beyond the job's own time limit. Real BOP/SSH, GPU and installer
acceptance remain outstanding (see PROGRESS.md).

### Upgrade note: a leftover RunPod lock blocks GPU servers

Because of the cross-provider quarantine, an unfinished or uncertain RunPod job lock (for example one left by 0.3.0) blocks all
GPU server work and preparation. The desktop shows "다른 연결 대상(RunPod 등)의 끝나지 않은 작업 잠금이 남아 있어 GPU 서버를 사용할 수 없습니다. …".
Remedy: first try 재연결 · 결과 회수 in 실행 관리. If the original process and Pod are confirmed stopped and reconcile cannot
succeed, release the lock with the CLI against the desktop state folder. `release-lock` refuses unless the job is FAILED,
CANCELED or COMPLETED and ATTEMPT_ID is the job's current attempt:

```powershell
uv run factory --state-dir "$env:APPDATA\kr.kbiofoundry.tuningfactory\state" status --job-id JOB_ID
uv run factory --state-dir "$env:APPDATA\kr.kbiofoundry.tuningfactory\state" release-lock JOB_ID ATTEMPT_ID --confirm-execution-stopped
```

JOB_ID is shown under the task name in the desktop jobs table. The ATTEMPT_ID is not shown there; read the `active_attempt_id`
field in the `status --job-id JOB_ID` output. The state folder is the app data folder (`kr.kbiofoundry.tuningfactory`) plus `state`.

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
uv run factory release-lock JOB_ID ATTEMPT_ID --confirm-execution-stopped
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
