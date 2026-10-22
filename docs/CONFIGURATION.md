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

These settings choose where real single-job (v1) training runs. They never appear in the AI YAML or in v1 manifests; the
AI YAML only declares the resource profile (GPU type/count and the per-GPU price). They ship in the Windows 0.4.0
installer. Training graphs use [executor profiles](#executor-profiles) instead, which are stored separately. Secrets never
belong here: use the desktop credential store or the local `.env`.

### Desktop settings (public settings.json)

| Key | Value |
| --- | --- |
| `provider` | `existing_runpod` (default; also assumed when absent in older settings) or `ssh_server`. Any other value is refused when saving. |
| `server_ssh_host` | Host name or IP address of the GPU server. |
| `server_ssh_port` | SSH port as a string, default `22`. |
| `server_ssh_username` | SSH account. Root is not required. |
| `server_ssh_key_file` | Local path of the private key file. A pasted `PRIVATE KEY` text is refused. The key stays where it is. |
| `server_ssh_fingerprint` | Host key fingerprint `SHA256:...` supplied by the administrator. Observed keys are never trusted automatically. |
| `server_remote_root` | Absolute work folder on the server, for example `/home/USER/factory`. Letters, digits, `_ . / -` only, no trailing slash, no `.` or `..` parts, writable. Neither the path nor any parent such as `/home` may be a symlink; enter the resolved real path. |
| `server_total_hourly_cost_usd` | Whole-server hourly cost typed by the operator; `0` is allowed for internal servers. It is a user setting, not a cloud price. |
| `server_selected_gpu_uuids` | A JSON string holding an array of at most 8 distinct GPU UUIDs, for example `["GPU-00000000-0000-0000-0000-000000000000"]`. |
| `auth_mode` | MLflow authentication: `oidc`, `token` or `none`. An empty value is read as `oidc`; other values are refused. |

The `server_` keys are separate from the Pod's `ssh_key_file` and `ssh_fingerprint`, so switching provider does not
lose the other connection. With `auth_mode` set to `none` the OIDC address, client ID, client secret and token are not
passed to the Python engine (the saved values are kept for a later switch back). Authenticated MLflow
(a token, or both the OIDC token URL and the OIDC secret; the URL alone does not count) requires an HTTPS tracking URI, in the CLI
and the desktop app alike (`work` and `run --mock` check before claiming), and an `http` artifact location, including a run's server-assigned one, is refused too. Even `file:` and `sqlite:` tracking URIs are refused while credentials are set,
so clear them for a local store. `none` allows HTTP such as `http://127.0.0.1:5000`. Native settings accept up to 8 selected UUIDs, but the
workbench refuses to save more than one (one GPU per job); an older larger saved selection is flagged and blocks actions. `.ppk` key paths are refused (OpenSSH format, no passphrase).

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

- `ssh_port` defaults to 22 and must be an integer. `selected_gpu_uuids` may be empty for `server-inspect` and `doctor --server-profile` (inspection-only probe);
  `server-runtime` (with or without `--prepare`), `plan --real`, submission and `work` need at least one observed UUID. The default
  policy allows one GPU per job.
- The key must be an OpenSSH-format private key without a passphrase; no agent is used.
- A relative `ssh_key_file` is resolved next to the JSON file. The file contains a path only, never key content.
- `total_hourly_cost_usd` is required (>= 0) and applies to the whole server.
- A job's resource profile in the AI YAML must have the selected GPU count and the exact GPU name, and its per-GPU price times the count must be at least `total_hourly_cost_usd`.
  Use `server-inspect` to read the exact name and UUIDs.

### Destination hash and receipt

Public destination settings (address, port, user, host fingerprint, remote work folder, selected GPU UUIDs, a hash of the
resolved key path and the operator cost) form a destination hash. The plan carries it (it is not displayed), and submission refuses a job
if the settings changed after the plan. On submission the app freezes an approval receipt from one read-only observation
of the server: server identity, transport settings, selected GPUs and cost. The receipt is stored next to the job, not in
the v1 manifest, and holds no credentials. Execution, logs, progress and result collection use that receipt (cancel is a local flag and does not consult it):
a different server, key path, work folder or GPU selection is refused instead of being linked to the old job. Changes to
the server's other, unselected GPUs or operator cost do not block recovery of an existing attempt.
Changing a destination setting invalidates earlier previews, plans and approvals.

## Workflow projects, data and graphs (Windows 0.4.0)

The general-purpose workflow (user guide: [TRAINING_WORKFLOWS.md](TRAINING_WORKFLOWS.md)) keeps its definitions in the
state directory: `%APPDATA%\kr.kbiofoundry.tuningfactory\state` for the desktop app, `--state-dir` (default `state`) for
the CLI. Every hash below is the SHA-256 of canonical JSON (sorted keys, no whitespace, UTF-8, no NaN; `registry.canonical`).
Requests are strict: unknown keys are refused. Issues carry an RFC 6901 pointer and never echo values: an invalid value
points at its field, and an unknown key (`extra_field`) points at the object that holds it, with the note "(the unknown
key is not shown)", so a mistyped or secret-looking key name never appears in the issue.
The desktop sends each request through one channel (`desktop_workflows.py`: envelope, action table, issue codes).

| Path under the state directory | Content | Owner |
|---|---|---|
| `workflows/workflow-revisions.sqlite3` | project and recipe revisions with their current pointers | `projects.py`, `recipes.py` |
| `workflows/roots/<owner>/<name>/<revision>/` | config and tokenizer snapshot of a Hugging Face root | `workflow_planning.py` |
| `workflows/root-index/`, `workflows/root-targets/` | root index and module (target) evidence | `workflow_planning.py`, `hf_targets.py` |
| `workflows/benchmarks/<revision>.json` | benchmark revisions | `workflow_planning.py`, `benchmarks.py` |
| `workflows/plans/<plan_sha256>.json` | immutable graph plans | `workflow_planning.py`, `graph_planner.py` |
| `workflows/tracking/<public_settings_sha256>.json` | public MLflow settings pinned by real plans | `workflow_planning.py`, `tracking.py` |
| `workflows/planning-inputs/<sha256(project_id)>.json` | saved planning inputs (replaced on save) | `workflow_planning.py` |
| `workflows/observations/<profile key>.json` | the stored observation of an executor profile | `workflow_planning.py` |
| `workflows/submissions/<run_id>.json` | immutable public submission receipt | `workflow_runs.py` |
| `registry/` | dataset versions, import sources and previews, split previews and commits | `registry.py`, `dataset_import.py`, `dataset_splits.py` |
| `controller/` | background controller status files (below) | `controller_entry.py`, `desktop/src-tauri/src/broker.rs` |
| `jobs.db` | single-job and graph ledger, leases, tracking outbox | `jobs.py`, `graph_store.py`, `tracking_outbox.py` |

### Projects and recipes

`ProjectSpec` (`projects.py`): `schema_version` 1, `project_id`, `name` (1–200), `purpose`, `task_type`, `datasets`,
`default_executor_profile_id`, `graph` and `legacy_origin`. A dataset binding is `{binding_id, role, dataset: {dataset_id,
version, sha256}}` with `role` `train`, `validation` or `test`; binding IDs are unique. `graph` is the free JSON draft; the
strict graph contract below applies when validating or planning. `project_create` takes `request_id`, `name`, `purpose`
(at most 4000 characters) and `task_type` (at most 200), assigns a random hex `project_id`, and is idempotent per request
ID (`request_reused` when the same ID arrives with other values).

`RecipeSpec` (`recipes.py`): `recipe_id`, `version`, `name` (1–200), `description`, `training_backend` (`llamafactory`),
`capability_version` (`lf-text-v1`), `training_stage` (pt, sft, rm, ppo, dpo, kto), `training_method` (lora, qlora, full,
freeze, oft), `input_kind` (fixed by the stage: pt text, sft instruction, rm and dpo preference, ppo prompt, kto kto),
`quantization_bit` (4 or 8), `hyperparameters`, `finetuning_options`, `stage_options` (without the reward-model fields)
and `evaluation_defaults` (without a dataset). A recipe holds no data, base model, resources or approvals. Graphs refuse
OFT train nodes.

Every save stores an immutable body under `revision_sha256 = sha256(canonical(spec))`, and every read re-hashes it. Saves
are compare-and-swap: `expected_revision` must equal the current revision (`null` creates a recipe; a project save always
names one); otherwise the result is `conflict`/`revision_conflict` and nothing changes. `recipe_clone` copies a revision to
a new recipe ID. There is no delete action for projects or recipes.

### Mapped import and split plans

`ImportMapping` (`dataset_import.py`): `source_format` (csv, json, jsonl), `encoding` (utf-8, utf-8-sig, cp949; cp949
only for CSV), `csv_delimiter` (one character, default `,`), `kind` (instruction, text, preference, kto, prompt),
`formatting` (alpaca or sharegpt), `fields` (canonical name to a field path of 1–16 parts), `message_tags` and
`boolean_labels` (`{"true": [...], "false": [...]}`, non-empty and disjoint; required for KTO string labels). Canonical
names: instruction, input, output, text, chosen, rejected, kto_tag, system, tools, messages, conversations. The desktop
page maps top-level columns with Alpaca formatting; ShareGPT files are registered through the legacy JSON form.

Sources and canonical bodies are limited to 32 MiB each, and the source path must be a local file (`local_file_only` for
UNC and device paths; the check reads the string only, so links or junctions to a share and mapped drive letters are not
detected). `preview_sha256` hashes the whole preview (source and canonical hashes, row count, samples, issues, mapping).
Registration takes `source_id`, `mapping`, `preview_sha256`, `dataset_id`, `version`, `synthetic` and `export_allowed`
(strict booleans, default false); it recomputes the preview and refuses a different one (`preview_changed`) or any issue
(`import_issues`). A registered version is immutable (`version_exists`), so a flag change needs a new version. The
provenance records the source hash, the mapping, the preview hash and the source row ordinals.

`SplitPlan` (`dataset_splits.py`): `dataset`, `seed` (int64), `train_basis_points`, `validation_basis_points` and
`test_basis_points` (0–10000 each, sum 10000), an optional `group_field`, and `outputs` (role to `{dataset_id, version}`
for exactly the roles with a non-zero share, distinct from each other and from the source). Rows with the same input
fingerprint, and with the same group value when `group_field` is set, form one component that stays in one role;
components are ordered by `sha256(canonical({seed, component_sha256}))`. The preview's `plan_sha256` hashes the full
manifest (assignments, counts, outputs). A commit recomputes it byte for byte, registers every output and copies
`synthetic` and `export_allowed` from the source.

### Graph JSON

`GraphSpec` (`graph_schema.py`): `nodes` (1–64), `depends_on` (order-only edges `{node_id, depends_on}`),
`max_concurrent` (1–64, default 1), `max_total_cost_usd` (> 0, default 100) and `retry_policy` (`max_attempts` 1–5,
default 1; `backoff_seconds` 0–3600, default 0). At most 256 edges, counting input references and `depends_on` entries,
and no cycle. Every node has `id` and `label` (1–200). An input reference is `{node_id, output_name}`: evaluate nodes
provide `evaluation`, every other kind provides `model`; select nodes take `evaluation` inputs and the others `model`.

| `kind` | Fields |
|---|---|
| `train` | `recipe_revision`, `overrides` (whole groups), `model_input` (an HF root with `initialization` `new_adapter`, `continue_adapter` or `full_weights`, or a node output), `dataset_binding` (a `train` binding), `reward_binding` (PPO only), `executor_profile_ids` (at least one) |
| `evaluate` | `model`, `benchmark_revision`, `executor_profile_ids` |
| `select` | `evaluations` (at least one), `selection`; `graph_plan` (`plan_refused`, not `graph_validate`) also requires distinct models (also through nested selections), at most one evaluation of a merge node, and at least one other candidate beside it |
| `merge` | `models` (2–4, distinct, also through selections), `weights` (one per model, each > 0, exact sum 1), `executor_profile_ids` |
| `materialize` | `model`, `executor_profile_ids` |

Merge rules (`graph_planner.py`): each merge input must be a plain causal LoRA/QLoRA adapter: such a train node (not rm
or ppo, no DoRA, rsLoRA or PiSSA), another merge, an existing adapter artifact, or a select node at least one of whose
candidates is one. A materialize output or full model is refused at planning (`merge requires plain causal LoRA/QLoRA
parents`). Ranks sum to at most 256: planning counts a nested merge by the sum of its inputs and a select input by its
smallest candidate, and compile checks the actual ranks. Merge and materialize use the primary runtime. The inputs must
be completed artifacts that are not simulated (any synthetic input makes the merged output synthetic), with the same
effective base, config, tokenizer, target modules and tensor shapes (`check_merge_descriptors`, `require_real=True`), so
a dry run stops at a merge node. Continuing, merging or materializing adapters trained on an HF root needs that root's
module evidence (`base_targets_missing`). The editor requires a `materialize` node between connected train nodes whose
training method family changes.

Synthetic and simulated lineage: a result is synthetic when any of its inputs was synthetic data (`synthetic: true`) or
a synthetic result; an evaluation on a synthetic benchmark (its data is synthetic) is synthetic too. Synthetic results
may feed later real nodes (continued training, evaluation, merge, materialize), and everything derived from them stays
synthetic: it is never promoted and never registered in MLflow, and a selection never chooses it. A synthetic
candidate counts as unsuccessful evidence: without `allow_partial`, one synthetic original ends the selection
`no_winner` (`partial_selection_not_allowed`); with it, only non-synthetic originals can win, and only when at least
`min_success` of them qualify; a synthetic merged candidate keeps the best original (`merged_not_eligible`).
Simulated results never feed a real run.

### Planning inputs and graph plans

`planning_inputs_save` stores, per project, `{schema: "planning-inputs/1", project_id, project_revision, simulation,
node_time_limits, estimates, saved_at}`, with `project_revision` as the compare-and-swap value. `node_time_limits` holds at
most 64 `{node_id, timeout_minutes}` entries (1–10080 minutes) for non-select nodes of the draft. An estimate is
`{profile_id, gpu_type, gpu_count, total_hourly_cost_usd}`: `profile_id` is `estimate-` plus 1–56 of `[A-Za-z0-9_.-]`,
`gpu_count` 1–8 and the whole-resource hourly price above 0 and at most 1000 USD; at most 16 estimates. Estimates are
usable in dry runs only (`estimate_real`) and may not reuse a native profile ID (`estimate_collision`).

`GraphPlanRequest` (`workflow_planning.py`): `project_id`, optional `project_revision` (default: current), `simulation`
(default true), `node_resources` (one `{node_id, timeout_seconds, runtime_profile}` per non-select node; 60–604800 s;
`ppo` exactly for PPO recipes, otherwise `primary`) and `estimates`. Executors come from the request's profiles and MLflow
from its public settings. A real plan additionally needs, for every profile, an observation of the same profile revision
taken at most 3600 s earlier (`observation_stale`, `observation_revision_differs`), a saved RunPod key for RunPod profiles,
the MLflow tracking URI (HTTPS when authenticated, complete OIDC values), and datasets registered with `export_allowed`
(practice data refused). A node's maximum cost is the highest `hourly price × timeout / 3600` among its allowed resources,
times `max_attempts`; the total must not exceed `max_total_cost_usd`. The plan is stored immutably as
`plans/<plan_sha256>.json`, where `plan_sha256` hashes the plan without that field; every read re-verifies it.

`graph_submit` takes `plan_sha256`, `request_id`, `approve_execution` and `approve_data_transfer`; a real plan needs both
approvals. It re-checks the project revision (every plan) and, for a real plan, each pinned profile's current revision and
the MLflow destination hash (`plan_stale`). It is idempotent by `request_id`: the same request returns the same run, and
the same ID with another plan is `request_reused`. A plan runs at most once: submitting a plan that already has a run
under another request ID is `conflict`/`plan_submitted` at `/plan_sha256` with `data: {run_id, status}` of the existing
run, also after that run was cancelled; `plan_stale`, missing approvals and data refusals are answered first.
This holds for the desktop and the CLI alike. The desktop sends `request_id = plan-<PLAN_SHA256>` (and checks
`graph_runs_list` for the plan hash before it opens the approval); pass `--request-id plan-<PLAN_SHA256>` to
`graph-submit` too, so the CLI and the desktop treat each other's submission as the same request. The receipt holds IDs
and approval flags only. Submission never starts the controller.

To run a graph again, plan again so that the plan hash changes. Real plans, and dry runs whose nodes use native profiles
only, take their resource facts from the stored observations, so the same inputs give the same `plan_sha256` and the
desktop shows the existing run: change an input (a new `executor_profile_inspect` of a used profile is enough). A dry run
with an estimate pins `observed_at = now` for it, so every `graph_plan` gives a new plan.

### Benchmarks and selection

`BenchmarkSpec` (`benchmarks.py`): `dataset`, `evaluator_kind`, `evaluator_version`, `ordered_sample_ids` (`row:<ordinal>`,
unique), `max_new_tokens`, `cutoff_len`, `seed`, `generation_mode`, `precision`, `quantization_bit` (4, 8 or null),
`tokenizer_fingerprint`, `template` and `semantic_model_kind`. `benchmark_save` accepts greedy causal-LM benchmarks in bf16
or fp16. The revision is `sha256(canonical({spec, evaluator_source_sha256}))`, so a change of the evaluator code gives a
new revision. The data must be a `validation` or `test` binding, and the benchmark's tokenizer fingerprint must equal the
root's.

| `evaluator_kind` | Metrics (higher is better) |
|---|---|
| `text` | `exact_match`, `character_f1` |
| `classification` | `accuracy`, `macro_f1` |
| `extraction` | `accuracy`, `f1` |
| `json` | `json_validity` |
| `kbf_extraction` | `mean_name_f1`, `mean_id_f1`, `json_validity` |
| `pipeline` | none (cannot drive a selection) |

`SelectionSpec`: `metric`, `direction` (`max` or `min`), `thresholds` (`{metric, direction, value}`), `allow_partial`
(default false), `min_success` (at least 2, default 2) and `merge_regression_tolerance` (at least 0, default 0). All
candidates use one benchmark on a `validation` binding; test evaluations never influence a selection. Synthetic and
simulated candidates and missing or non-finite metrics are unsuccessful evidence (gated by `allow_partial` and
`min_success`, see the lineage rules above), failed thresholds are ordinary exclusions, and the result is `selected`,
`waiting` or `no_winner`. After a merge, the best original is kept when the merged model regresses by more than the
tolerance, or when the merged evaluation is not eligible (`merged_not_eligible`; its exclusion stays in `excluded`).
Without `allow_partial`, a failed merge or merged evaluation blocks the final selection (`BLOCKED`, "partial
selection is not allowed"), and nothing is selected even when the originals were evaluated. With `allow_partial`, the
best original is selected when at least `min_success` originals succeeded; otherwise the result is `no_winner` with
`insufficient_partial_success`. In both cases the run still ends `FAILED`, because a node failed. Text-agreement
metrics are not semantic quality scores, and `json_validity` is a format metric.

## Executor profiles

### Desktop (native) profiles and Credential Manager entries

The app keeps the public fields in `<app data>/executor-profiles.json` (`{"schema_version": 1, "profiles": {id: fields}}`,
at most 32 profiles, written atomically; `desktop/src-tauri/src/profiles.rs`). A profile ID matches
`[A-Za-z0-9_-][A-Za-z0-9_.-]{0,63}`, does not start with `estimate-`, is not a DOS device name, and is unique ignoring case
and trailing dots. The provider and the ID are fixed once saved.

| `provider` | Fields |
|---|---|
| `existing_runpod` | `provider`, `name`, `pod_id`, `ssh_fingerprint`, `ssh_key_file` |
| `ssh_server` | `provider`, `name`, `server_ssh_host`, `server_ssh_port`, `server_ssh_username`, `server_ssh_fingerprint`, `server_remote_root`, `server_ssh_key_file`, `server_total_hourly_cost_usd`, `server_selected_gpu_uuids` |

Values are strings of at most 2048 bytes. Key-file fields must be local absolute paths (relative, UNC and device paths are
refused, because the transport hash resolves the path); key content, pasted keys and `.ppk` files are refused in every
field. `server_selected_gpu_uuids` is a JSON array of at most 8 distinct GPU UUIDs, and `server_total_hourly_cost_usd` a
non-negative decimal.

A RunPod profile's API key lives only in Windows Credential Manager, service `kr.kbiofoundry.tuningfactory`, entry
`executor-profile/<profile_id>/RUNPOD_API_KEY`; views carry only `RUNPOD_API_KEY_saved`. Saving with an empty key keeps
the stored one, and a save may copy the legacy connection's RunPod key instead. Deleting a profile deletes its entry, and
`forget_credentials` (the connection screen's credential delete) deletes the connection credentials and every profile
entry. A save or delete is refused while a job pinned to the profile is running or unsettled (`profile_in_use`; fails
closed when the engine cannot check).

`executor_profile_inspect` (read-only: a RunPod status read or the fixed SSH probe) stores the observed facts in
`observations/<profile key>.json`. A real plan accepts an observation only while its profile revision,
`sha256(canonical({schema: "executor-profile/1", profile_id, fields}))`, equals the current one and it is at most an hour
old (`pin_observation`, `observation_fresh`).

Destinations are told apart by their exclusivity key: `existing_runpod:<pod_id>` for a RunPod profile and
`ssh_server:<server_id>` (the hashed `/etc/machine-id`) for an SSH profile, where SSH profiles that share a server ID or a
GPU UUID count as one server. A RunPod profile and an SSH profile that point at the same physical host therefore count as
different destinations: the plan's waits and `max_concurrent_gpu_count` treat them separately. Within one state
directory the cross-provider quarantine still makes every RunPod attempt and every SSH attempt wait for each other, but
another state directory or PC is not protected. Register one physical machine under one profile only.

### CLI profiles file and credential files

The CLI takes executor profiles only from `--executor-profiles FILE` (`executor-profile-inspect`, `graph-plan`,
`graph-submit`, `graph-work`); there is no default file. The file is UTF-8 JSON of at most 1 MiB without duplicate keys,
with at most 32 profiles and the same field allowlist as the app (values are strings). Placeholders:

```json
{
  "schema_version": 1,
  "profiles": [
    {"profile_id": "pod-a", "credential_file": "pod-a.env",
     "fields": {"provider": "existing_runpod", "name": "Pod A", "pod_id": "POD_ID",
                "ssh_fingerprint": "SHA256:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
                "ssh_key_file": "C:/Users/USER/.ssh/id_ed25519"}},
    {"profile_id": "lab-server",
     "fields": {"provider": "ssh_server", "name": "Lab server", "server_ssh_host": "gpu.example.org",
                "server_ssh_port": "22", "server_ssh_username": "alice",
                "server_ssh_fingerprint": "SHA256:ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopq",
                "server_remote_root": "/home/alice/factory", "server_ssh_key_file": "C:/Users/USER/.ssh/id_ed25519",
                "server_total_hourly_cost_usd": "0",
                "server_selected_gpu_uuids": "[\"GPU-00000000-0000-0000-0000-000000000000\"]"}}
  ]
}
```

- A credential name or key content in `fields` is refused (`credential_field`: reference a credential file instead).
- `credential_file` is for RunPod profiles only: a local env file, relative to the profiles file or absolute (`~` is
  expanded), at most 64 KiB, holding `RUNPOD_API_KEY=<key>`. UNC and device paths give `local_file_only`, an unreadable
  file `credential_file_unreadable`, a file without the key `credential_missing`. Without a credential file, a RunPod
  profile uses `RUNPOD_API_KEY` from `.env` or the environment.
- Keys are read only by `executor-profile-inspect` (that one profile) and by `graph-work --approve-execution-and-transfer`.
  `graph-plan` and `graph-submit` see public fields and `RUNPOD_API_KEY_saved` only. `graph-work --executor-profiles`
  without the approval flag is refused (exit 2).
- MLflow credentials for the graph commands come from the usual `.env`, `--connection-file` and process environment;
  `--tracking-uri` overrides the URI. Use the same profiles file and MLflow environment for `graph-plan --real` and
  `graph-submit`, or the submission is `plan_stale`. Hugging Face tokens cannot be given to the CLI graph commands; HF root
  snapshots and module checks are desktop actions.

## MLflow authentication modes

The desktop setting `auth_mode` is `oidc` (default; an empty value reads as `oidc`), `token` or `none`. The CLI and the
controller derive the effective mode from the values set: `oidc` when `MLFLOW_OIDC_TOKEN_URL` and
`MLFLOW_OIDC_CLIENT_SECRET` are both set, otherwise `token` when `MLFLOW_TRACKING_TOKEN` is set, otherwise `none`
(`auth.current_auth_mode`). With
credentials configured, the tracking URI and every artifact location must be HTTPS, and local `file:`/`sqlite:` stores are
refused; `none` passes no token or OIDC value to the engine and allows HTTP, so use it only on a trusted network. Details
and the local server example are in [OPERATIONS.md](OPERATIONS.md). A real graph plan pins
`public_settings_sha256 = sha256(canonical({tracking_uri, experiment: "llm-tuning-factory", artifact_location, auth_mode}))`;
submission refuses other settings, and the controller records the hash of its own public view in `current.json`.

## Background controller

One controller runs per state directory: an owner lease refuses a second one (`owner_held`, exit 3). The desktop app starts
it only when the user presses 컨트롤러 시작: the main binary runs detached as a broker (`--controller`), reads `settings.json`,
`executor-profiles.json` and Credential Manager, and starts the bundled engine `factory-bridge.exe --controller` with no
added environment and one JSON start line on stdin (`factory-controller/1`: state root, owner ID, the public MLflow fields
and the credentials of the chosen auth mode, the profiles with their own RunPod keys, timing settings). The pipe stays open
as a lifeline; EOF or a stop request drains the controller (there is no kill-now). The state root must be a local absolute
path, so a redirected (UNC) AppData cannot start the controller. `factory graph-work` runs the same controller in the
foreground; its exit codes are 0 (drained), 1 (owner lost or error), 2 (refused before the start), 3 (`owner_held`), 4
(MLflow settings refused) and 130 (a second Ctrl+C).

### Environment and `CREDENTIAL_ENV`

Before applying its single MLflow scope, the controller removes every name in `manifest.CREDENTIAL_ENV` (reused as
`controller_entry.CREDENTIAL_ENV`) from its environment:

- `RUNPOD_API_KEY`;
- `MLFLOW_TRACKING_URI`, `MLFLOW_TRACKING_TOKEN`, `MLFLOW_TRACKING_USERNAME`, `MLFLOW_TRACKING_PASSWORD`, the three
  `MLFLOW_OIDC_*` values, `FACTORY_MLFLOW_ARTIFACT_LOCATION`, `FACTORY_OPERATOR`;
- `S3_*` and `AWS_*` keys and session tokens, `AWS_PROFILE`, `AWS_DEFAULT_PROFILE`, `AWS_SHARED_CREDENTIALS_FILE`,
  `AWS_CONFIG_FILE`, the web-identity, role and container-credential variables;
- `AZURE_STORAGE_CONNECTION_STRING`, `AZURE_STORAGE_ACCESS_KEY`, `AZURE_STORAGE_SAS_TOKEN`, `AZURE_CLIENT_SECRET`,
  `AZURE_CLIENT_CERTIFICATE_PATH`, `GOOGLE_APPLICATION_CREDENTIALS`;
- `HF_TOKEN`, `HUGGING_FACE_HUB_TOKEN`, `HUGGINGFACE_HUB_TOKEN`;
- `MLFLOW_TRACKING_INSECURE_TLS` and `MLFLOW_TRACKING_CLIENT_CERT_PATH`.

It then sets only the values of its own scope, filtered by the auth mode (`none` drops the token and OIDC values, `token`
drops the OIDC values, `oidc` drops the token), and enforces the HTTPS, artifact-location and complete-OIDC rules. A refusal
leaves no credential variable set, writes `settings_refused` and exits 4. Child processes such as `git rev-parse` get an
environment without these names. RunPod keys exist only per profile in memory; S3 and HF credentials never reach the
controller.

Known limits: credential *files* outside the environment (the shared AWS credentials file, the Azure CLI cache, gcloud
default credentials) stay readable by the controller process. **TLS behaviour change:** v1 `work` honours
`MLFLOW_TRACKING_INSECURE_TLS` and the mTLS client certificate, but graph controllers (desktop and `graph-work`) do not.
For an MLflow server with a private CA set `MLFLOW_TRACKING_SERVER_CERT_PATH`, which is kept, as are `REQUESTS_CA_BUNDLE`
and `SSL_CERT_FILE`. mTLS MLflow is not supported for graph runs.

### Status files (`state/controller/`)

| File | Content |
|---|---|
| `current.json` | While running: `ok`, `owner_id`, `pid`, `started_at`, `profiles` (ID to revision), `skipped_profiles` (ID to `invalid_fields`, `credential_missing`, `observation_missing`, `observation_revision_differs` or `observation_invalid`), `tracking` (`view_sha256`, `auth_mode`, `effective_auth_mode`) and `tracking_settings_sha256`. After the stop also `stopped_at` and `code` (`stopped` or `owner_lost`). A refusal or failure before the owner lease writes `{ok: false, code: "settings_refused" or "controller_failed", ...}` when no live controller owns the directory. |
| `broker.json` | The desktop broker's last start failure `{at, code, error}` with a fixed Korean text; removed when an engine starts. |
| `stop-request.json` | `{owner_id, requested_at}`, written by `controller_stop`; removed after the stop, and a leftover one is removed at the next start. |

The files hold IDs, revisions, hashes, timestamps and fixed codes only. `controller_status` reads `jobs.db` read-only and
reports `restart_required`: `profiles` lists IDs whose revision changed since the start, `tracking` is true when the public
MLflow view or an explicit auth mode changed. A RunPod key or MLflow credential re-saved without a public change is not
detected, so restart the controller after saving credentials.

### Graph destination hashes

A real graph plan pins, per destination, the profile revision and a transport hash. For RunPod it is
`sha256(canonical({provider: "existing_runpod", pod_id, ssh_fingerprint, key_reference_sha256}))`, where the key reference
is the hash of the resolved key path (`executor_profiles.py`); SSH profiles use the server profile's transport hash
(`gpu_servers.py`). The approval grant pins each destination profile's hash, so editing a pinned profile after planning
makes the run wait with a re-plan reason or the submission `plan_stale`.
