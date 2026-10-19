# Configuration, data and evaluation contracts

## AI YAML

Start with the three files in configs/ai/. All use the same non-sensitive dummy dataset and pipeline evaluator;
their names illustrate workflow separation and do not claim task-trained models. The Qwen revision was queried
from the public Hugging Face model metadata during implementation:
https://huggingface.co/Qwen/Qwen2.5-0.5B-Instruct/tree/7ae557604adf67be50417f59c2c2f167def9a775

Required identity fields: ai_id, workflow_id, dataset_id, dataset_version, base_model, model_revision,
tokenizer_revision. Other fields have explicit validated defaults. Unknown keys fail validation.

| Field | Current contract |
|---|---|
| ai_id / dataset_id | Letters, digits, underscore or hyphen |
| dataset_version | Immutable registry version; letters, digits, dots, underscore or hyphen |
| base_model | Qwen/Qwen2.5-{0.5B,1.5B,7B}-Instruct or meta-llama/Llama-{3.1-8B,3.2-3B}-Instruct allowlist |
| model_revision | Exact 40-character lowercase Hugging Face commit SHA |
| tokenizer_revision | Must equal model_revision for the initial adapter |
| template | qwen for Qwen2.5, llama3 for supported Llama3 models |
| training_backend | llamafactory |
| training_method | lora or qlora (4-bit bitsandbytes) |
| artifact_destination | control-plane; actual files are retained under the chosen state directory |

The allowlist prevents silently accepting untested model/template combinations. It is a validation boundary,
not a claim that all listed models have been GPU-tested. Extend it with an appropriate integration test when needed.
Training options are SFT only; remote model code is disabled. Arbitrary backend YAML overrides are intentionally absent.
Different tokenizer commits are rejected because the initial LLaMA-Factory integration uses a shared model_revision.

Hyperparameters: learning_rate, num_train_epochs, per_device_train_batch_size, gradient_accumulation_steps,
cutoff_len, lora_rank, lora_target, seed, max_steps and save_steps. See Pydantic field bounds in config.py.
Example max_steps=2 is a smoke setting, not a useful training prescription. fp16 is enabled by the initial translator.

Resource profile: gpu_count, gpu_type (RunPod GPU ID or exact displayName), timeout_seconds,
hourly_cost_usd **per GPU**, max_cost_usd. Estimate = GPU count × hourly price × timeout / 3600.
Managed Pod creation uses a **whole-Pod** hourly quote; do not multiply that quote twice.
Actual Pod price, count and type are checked before dataset transfer.

Policy is JSON, supplied before the command with `--policy-file configs/policy.json`.
It controls max_gpus, max_concurrent, max_timeout_seconds, max_cost_usd and max_attempts.
Costs are estimates and a software guard, not a provider-enforced spend cap.

## Dataset registry

Input is UTF-8 JSON or JSONL in Alpaca instruction format or the existing project's ShareGPT messages format.
Alpaca rows require nonempty string instruction and output; input is an optional string. ShareGPT rows require messages
with system/user/assistant roles and nonempty content, a user prompt and a final assistant answer. The existing
ops/dataset_info.json role/content mapping is preserved. The platform snapshots original bytes and does not create
real datasets or annotations. All records in a dataset must use one compatible format.

```json
[{"instruction":"Your task", "input":"Provided source", "output":"Provided reference answer"}]
```

`dataset-register ID VERSION PATH` snapshots bytes under a SHA-256 key, records row count/format and export authorization.
A version cannot be replaced, including its export permission. Register a new version after the data owner approves
external use, using `--export-allowed`. Approval is a recorded operator assertion, not an automated classification of data.
Every use rechecks the dataset hash. Dataset blobs are not uploaded to MLflow; only their identity/hash and job evidence are.
AI config revisions are stored by content hash under registry/ai/AI_ID/ for history.

## Evaluation

`evaluation_profile.kind`: pipeline, json, classification, extraction or kbf_extraction.
`thresholds` is an optional dictionary of **minimum** metric values. A missing metric fails its threshold.
Training completion and quality qualification are recorded separately; low quality retains the successful experiment.

For evaluator use, supply a JSON object with predictions and references arrays. JSON validation requires only predictions.
Classification uses exact string labels, accuracy and macro F1 over the union of predicted/reference labels.
Extraction uses JSON objects; each (field, canonical JSON value) is an exact item, with micro F1 and whole-object accuracy.
JSON validity is the valid JSON fraction and does not measure semantic correctness.

`kbf_extraction` reuses the canonical existing eval_extraction_pc.py name/catalog-ID regex and per-record set-F1 semantics.
It accepts generation/reference strings and reports mean_name_f1, mean_id_f1 and strict json_validity. A truncated JSON
string may score partial credit while failing JSON validity. These are heuristic extraction metrics, not general semantic
correctness, and are not interchangeable with the common dictionary-extraction micro F1.

`factory evaluate FILE --kind KIND` evaluates caller-supplied results and prints metrics; it does not attach them to a run
or claim they came from a model. For automatic orchestration evaluation, a future generation evaluator should write
`output/predictions.json` from the actual trained adapter and the registered validation corpus. The initial SFT worker
does not perform this generation step. Without this file, task quality is explicitly not_evaluated.
Never use examples/evaluation.json as evidence of model quality.

## Execution destination settings

These settings choose where real training runs. They never appear in the AI YAML or in v1 manifests; the AI YAML only
declares the resource profile (GPU type/count and the per-GPU price). This part describes the development branch and
is not in the released 0.3.0 installer. Secrets never belong here: use the desktop credential store or the local `.env`.

### Desktop settings (public settings.json)

| Key | Value |
| --- | --- |
| `provider` | `existing_runpod` (default; also assumed when absent in older settings) or `ssh_server`. Any other value is refused when saving. |
| `server_ssh_host` | Host name or IP address of the GPU server. |
| `server_ssh_port` | SSH port as a string, default `22`. |
| `server_ssh_username` | SSH account. Root is not required. |
| `server_ssh_key_file` | Local path of the private key file. A pasted `PRIVATE KEY` text is refused. The key stays where it is. |
| `server_ssh_fingerprint` | Host key fingerprint `SHA256:...` supplied by the administrator. Observed keys are never trusted automatically. |
| `server_remote_root` | Absolute work folder on the server, for example `/home/USER/factory`. Letters, digits, `_ . / -` only, no trailing slash, no `.` or `..` parts, no symlinks, writable. |
| `server_total_hourly_cost_usd` | Whole-server hourly cost typed by the operator; `0` is allowed for internal servers. It is a user setting, not a cloud price. |
| `server_selected_gpu_uuids` | A JSON string holding an array of at most 8 distinct GPU UUIDs, for example `["GPU-00000000-0000-0000-0000-000000000000"]`. |
| `auth_mode` | MLflow authentication: `oidc`, `token` or `none`. An empty value is read as `oidc`; other values are refused. |

The `server_` keys are separate from the Pod's `ssh_key_file` and `ssh_fingerprint`, so switching provider does not
lose the other connection. With `auth_mode` set to `none` the OIDC address, client ID, client secret and token are not
passed to the Python engine (the saved values are kept for a later switch back). Authenticated MLflow
(token or OIDC secret) requires an HTTPS tracking URI in the desktop app (the CLI requires HTTPS for OIDC token URLs and connection profiles, but does not refuse a bare token over HTTP; use HTTPS anyway); `none` also allows HTTP such as `http://127.0.0.1:5000`.

### CLI server profile (JSON)

The CLI reads one local JSON file for `--server-profile` and for the `server-inspect`, `server-runtime` and
`server-runtime-release` commands. Unknown fields are rejected. Use placeholder values like these:

```json
{
  "ssh_host": "gpu.example.org",
  "ssh_port": 22,
  "ssh_username": "alice",
  "ssh_key_file": "keys/id_ed25519",
  "ssh_fingerprint": "SHA256:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
  "remote_root": "/home/alice/factory",
  "total_hourly_cost_usd": 0,
  "selected_gpu_uuids": ["GPU-00000000-0000-0000-0000-000000000000"]
}
```

- `ssh_port` defaults to 22 and must be an integer. `selected_gpu_uuids` may be empty for `server-inspect`, but
  preparation, `plan --real`, submission and `work` need at least one observed UUID.
- A relative `ssh_key_file` is resolved next to the JSON file. The file contains a path only, never key content.
- `total_hourly_cost_usd` is required (>= 0) and applies to the whole server.
- A job's resource profile in the AI YAML must still match the selected GPU count and GPU name.
  Use `server-inspect` to read the exact name and UUIDs.

### Destination hash and receipt

Public destination settings (address, port, user, host fingerprint, remote work folder, selected GPU UUIDs, a hash of the
resolved key path and the operator cost) form a destination hash. The desktop shows it in the plan, and submission refuses a job
if the settings changed after the plan. On submission the app freezes an approval receipt from one read-only observation
of the server: server identity, transport settings, selected GPUs and cost. The receipt is stored next to the job, not in
the v1 manifest, and holds no credentials. Execution, logs, progress, cancel and result collection use that receipt:
a different server, key path, work folder or GPU selection is refused instead of being linked to the old job. Changes to
the server's other, unselected GPUs or operator cost do not block recovery of an existing attempt.
Changing a destination setting invalidates earlier previews, plans and approvals.
