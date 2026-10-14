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
| base_model | Qwen/Qwen2.5-{0.5B,1.5B,7B}-Instruct allowlist |
| model_revision | Exact 40-character lowercase Hugging Face commit SHA |
| tokenizer_revision | Must equal model_revision for the initial adapter |
| template | qwen |
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

Input is a UTF-8 JSON array in Alpaca instruction format. Every row requires nonempty string instruction and output;
input is an optional string. The platform validates/copies data but does not create real datasets or annotations.

```json
[{"instruction":"Your task", "input":"Provided source", "output":"Provided reference answer"}]
```

`dataset-register ID VERSION PATH` snapshots bytes under a SHA-256 key, records row count/format and export authorization.
A version cannot be replaced, including its export permission. Register a new version after the data owner approves
external use, using `--export-allowed`. Approval is a recorded operator assertion, not an automated classification of data.
Every use rechecks the dataset hash. Dataset blobs are not uploaded to MLflow; only their identity/hash and job evidence are.
AI config revisions are stored by content hash under registry/ai/AI_ID/ for history.

## Evaluation

`evaluation_profile.kind`: pipeline, json, classification or extraction.
`thresholds` is an optional dictionary of **minimum** metric values. A missing metric fails its threshold.
Training completion and quality qualification are recorded separately; low quality retains the successful experiment.

For evaluator use, supply a JSON object with predictions and references arrays. JSON validation requires only predictions.
Classification uses exact string labels, accuracy and macro F1 over the union of predicted/reference labels.
Extraction uses JSON objects; each (field, canonical JSON value) is an exact item, with micro F1 and whole-object accuracy.
JSON validity is the valid JSON fraction and does not measure semantic correctness.

`factory evaluate FILE --kind KIND` evaluates caller-supplied results and prints metrics; it does not attach them to a run
or claim they came from a model. For automatic orchestration evaluation, a future generation evaluator should write
`output/predictions.json` from the actual trained adapter and the registered validation corpus. The initial SFT worker
does not perform this generation step. Without this file, task quality is explicitly not_evaluated.
Never use examples/evaluation.json as evidence of model quality.
