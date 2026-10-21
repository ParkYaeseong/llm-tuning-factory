// TypeScript shapes of the workflow channel (Task 9) as Task 10 uses them. Pure: no imports.
// Sources: desktop_workflows.py, workflow_planning.py, workflow_runs.py, hf_targets.py, controller_entry.py,
// graph_controller.graph_status, profiles.rs and broker.rs.

export const WORKFLOW_ACTIONS = [
  "projects_list", "project_create", "project_get", "project_save", "project_import_legacy",
  "recipes_list", "recipe_get", "recipe_save", "recipe_clone",
  "dataset_import_source", "dataset_import_preview", "dataset_import_register",
  "dataset_split_preview", "dataset_split_commit",
  "executor_profile_inspect", "model_root_snapshot", "model_roots_list", "benchmark_save", "benchmarks_list",
  "graph_validate", "graph_plan", "graph_plan_get",
  "graph_submit", "graph_runs_list", "graph_run_get", "graph_pause", "graph_resume", "graph_cancel",
  "tracking_rearm", "executor_profile_edit_check",
  "controller_status", "controller_stop",
  "model_root_targets",
  "graph_release_lock", "observations_list", "executor_profile_inventory", "planning_inputs_get",   // 9.8
  "planning_inputs_save",
] as const;                                                    // = desktop_workflows.WORKFLOW_ACTIONS (38)
export type WorkflowAction = (typeof WORKFLOW_ACTIONS)[number];
/** Actions Python serves but main.rs does not allow yet. Empty since bf9a13b added model_root_targets to main.rs
 * (9.7 native follow-up); a new Python-only action goes here until its native allowlist entry lands. */
export const NATIVE_PENDING: readonly WorkflowAction[] = [];
export type Sha256 = string;
export interface WorkflowIssue { code: string; path: string; message: string; node_id?: string | null; row_number?: number | null }
/** `data` may also be set on `invalid`/`conflict`: a blocked plan carries `{warnings}`, an import preview with row
 * issues carries the preview, `executor_profile_edit_check`'s `profile_in_use` carries `{jobs}`, and `graph_submit`'s
 * `plan_submitted` carries the run already registered for the plan (`PlanSubmitted`). */
export interface WorkflowResult<T> { status: "ok" | "invalid" | "conflict"; issues: WorkflowIssue[]; data: T | null }
export interface WithWarnings { warnings?: WorkflowIssue[] }

// -- 9.1 projects, recipes and data --
export interface DatasetRef { dataset_id: string; version: string; sha256: Sha256 }
export type BindingRole = "train" | "validation" | "test";
export interface DatasetBinding { binding_id: string; role: BindingRole; dataset: DatasetRef }
export interface ProjectSpec {
  schema_version: 1; project_id: string; name: string; purpose: string; task_type: string;
  datasets: DatasetBinding[]; default_executor_profile_id: string | null;
  graph: Record<string, unknown>;                              // GraphJson or {} (graph-model.ts, 10.5)
  legacy_origin: string | null;
}
export interface ProjectRevision { project: ProjectSpec; revision_sha256: Sha256 }
export interface ProjectSummary {
  project_id: string; name: string; purpose: string; task_type: string; revision_sha256: Sha256;
  legacy_origin: string | null; datasets: DatasetBinding[]; graph_nodes: number;
}
export type Stage = "pt" | "sft" | "rm" | "ppo" | "dpo" | "kto";
export type Method = "lora" | "qlora" | "full" | "freeze" | "oft";
export type InputKind = "text" | "instruction" | "preference" | "prompt" | "kto";
export interface RecipeSpec {
  schema_version: 1; recipe_id: string; version: string; name: string; description: string;
  training_backend: "llamafactory"; capability_version: "lf-text-v1";
  training_stage: Stage; training_method: Method; input_kind: InputKind; quantization_bit: 4 | 8;
  hyperparameters: Record<string, unknown>; finetuning_options: Record<string, unknown>;
  stage_options: Record<string, unknown>; evaluation_defaults: Record<string, unknown>;
}
export interface RecipeRevision { recipe: RecipeSpec; revision_sha256: Sha256 }
export interface RecipeSummary {
  recipe_id: string; version: string; name: string; description: string; training_stage: Stage;
  training_method: Method; input_kind: InputKind; revision_sha256: Sha256;
}
export interface LegacyImport extends ProjectRevision, WithWarnings {
  recipe: RecipeSpec; recipe_revision_sha256: Sha256; datasets: DatasetBinding[];
}
export type FieldPath = (string | number)[];
export interface ImportMapping {
  schema_version: 1; transform_version: "text-mapping-v1"; source_format: "csv" | "json" | "jsonl";
  encoding: "utf-8" | "utf-8-sig" | "cp949"; csv_delimiter: string; kind: InputKind;
  formatting: "alpaca" | "sharegpt"; fields: Record<string, FieldPath>;
  message_tags: Record<string, string>; boolean_labels: { true?: string[]; false?: string[] };
}
export interface ImportSource { source_id: Sha256; source_sha256: Sha256; size: number }
export interface ImportPreview {
  source_id: Sha256; source_sha256: Sha256; source_size: number; canonical_sha256: Sha256 | null;
  canonical_size: number | null; row_count: number; samples: Record<string, unknown>[];
  issues: { ordinal: number | null; code: string; message: string }[];
  source_samples: Record<string, unknown>[]; source_fields: string[]; mapping: ImportMapping; preview_sha256: Sha256;
}
export interface ImportRegistered { dataset: DatasetRef; preview_sha256: Sha256 }
export interface SplitPlan {
  dataset: DatasetRef; seed: number; train_basis_points: number; validation_basis_points: number;
  test_basis_points: number; group_field: FieldPath | null; outputs: Partial<Record<BindingRole, { dataset_id: string; version: string }>>;
}
export interface SplitPreview { plan_sha256: Sha256; counts: Partial<Record<BindingRole, number>>; outputs: Partial<Record<BindingRole, DatasetRef>> }
export interface SplitCommitted { plan_sha256: Sha256; outputs: SplitPreview["outputs"] }

// -- native executor profiles (profiles.rs) and 9.2 planning --
export interface PublicProfile { profile_id: string; fields: Record<string, string>; RUNPOD_API_KEY_saved?: boolean }
export interface ResourceFacts { gpu_type: string; gpu_count: number; total_hourly_cost_usd: number; observed_at: string; [key: string]: unknown }
export interface Observation { profile: Record<string, unknown>; facts: ResourceFacts; observed_at: string; revision: Sha256 }
export interface HFRootRef { repo_id: string; revision: string; config_sha256: Sha256; tokenizer_fingerprint: Sha256 }
export interface ModelProfile { repo_id: string; revision: string; config_sha256: Sha256; architecture: string; template: string; [key: string]: unknown }
export interface ModelRoot { root: HFRootRef; profile: ModelProfile; snapshot_ready: boolean; targets_ready: boolean }
export interface RootSnapshot { root: HFRootRef; profile: ModelProfile; files: string[] }
export interface RootTargets { root: HFRootRef; shards: string[]; module_count: number; targets: string[]; evidence_sha256: Sha256 }
export interface BenchmarkSpec {
  dataset: DatasetRef; evaluator_kind: string; evaluator_version: string; ordered_sample_ids: string[];
  max_new_tokens: number; cutoff_len: number; seed: number; generation_mode: "greedy";
  precision: "bf16" | "fp16"; quantization_bit: 4 | 8 | null; tokenizer_fingerprint: Sha256; template: string;
  semantic_model_kind: "causal_lm";
}
export interface BenchmarkRevision { revision_sha256: Sha256; spec: BenchmarkSpec }
export interface GraphValidation { project_id: string; project_revision: Sha256; node_count: number }
export interface NodeResourceChoice { node_id: string; timeout_seconds: number; runtime_profile: "primary" | "ppo" }   // 60–604800 s
export interface PlanningEstimate { profile_id: string; gpu_type: string; gpu_count: number; total_hourly_cost_usd: number }   // profile_id "estimate-<name>"
export interface PlanRequest {
  project_id: string; project_revision: Sha256; simulation: boolean;          // Python accepts a null revision; the UI always pins it
  node_resources: NodeResourceChoice[]; estimates: PlanningEstimate[];
}
export interface PlanResource {
  profile_id: string; revision: string; resource_id: string; gpu_type: string; gpu_count: number;
  total_hourly_cost_usd: number; cost_source: string;          // "runpod_api" | "estimate" | SSH cost source
}
/** A plan node's pinned recipe revision and its public identity (9.8 `_node_references`; null fields when the
 * revision's spec is not in the plan). Train nodes only. */
export interface PlanNodeRecipe {
  recipe_id: string | null; version: string | null; name: string | null; training_stage: string | null;
  training_method: string | null; revision_sha256: Sha256;
}
/** The data a plan node uses: a train node's bound dataset (`binding_id`) or an evaluate node's benchmark dataset
 * (`benchmark_revision`). */
export interface PlanNodeDataset extends DatasetRef { binding_id: string | null; benchmark_revision: Sha256 | null }
/** `graph_plan`/`graph_plan_get` `ok` data. A blocked `graph_plan` answers `invalid` with data `{warnings}` or null. */
export interface PlanSummary extends WithWarnings {
  plan_sha256: Sha256; simulation: boolean; project_id: string; project_revision: Sha256;
  max_estimated_cost_usd: number; max_duration_seconds: number; max_concurrent_gpu_count: number;
  retry_policy: { max_attempts: number; backoff_seconds: number };
  nodes: { node_id: string; kind: string; label: string; max_estimated_cost_usd: number; max_duration_seconds: number; allowed: PlanResource[];
    recipe: PlanNodeRecipe | null; datasets: PlanNodeDataset[] }[];
  datasets: (DatasetRef & { synthetic: boolean; export_allowed: boolean })[];
  tracking: { profile_id: string; public_settings_sha256: Sha256; settings: Record<string, string> | null } | null;
}

// -- the controller (graph_status, 9.4) and graph runs (9.3) --
export interface ControllerView { owner_id: string | null; state: "running" | "stale" | "absent"; heartbeat_age_seconds: number | null }
/** `controller_status` data (controller_entry.py, 9.4; checked against 1a9ae29). `controller` equals graph_status's
 * block; `restart_required` is filled only for the running owner. `broker` is broker.rs's `broker.json`
 * `{at, code, error}`, where `at` is epoch seconds (a number). */
export interface ControllerStatus {
  controller: ControllerView; current: Record<string, unknown> | null;
  broker: { at: number; code: string; error: string } | null; stop_requested: boolean;
  restart_required: { profiles: string[]; tracking: boolean | null }; runs: { active: number };
}
/** `controller_stop` data (controller_entry.py, 9.4). */
export interface ControllerStopResult { requested: boolean; owner_id?: string; reason?: "not_running" }
export type TrackingStatus = "not_started" | "pending" | "retrying" | "saved" | "uncertain" | "failed";
export interface GraphRunNode {
  node_id: string; label: string; kind: string; job_id: string | null;     // job_id: 9.8, the node's current job
  status: "PENDING" | "READY" | "RUNNING" | "COMPLETED" | "FAILED" | "BLOCKED" | "CANCELED";
  job_status: string | null; attempt_id: string | null; attempts: number; counted_attempts: number; max_attempts: number;
  resource_id: string | null; destination_profile_id: string | null; uncertain: boolean; awaiting_result: boolean;
  result_deadline: number | null; needs_operator: boolean; wait_reason: string | null; error: string | null;
  retry_at: number | null; tracking_status: TrackingStatus; registration_status: TrackingStatus | "none" | "registered";
  tracking_action: string | null;
  output: { artifact_id: Sha256; inventory_sha256: Sha256 } | { evaluation_status: string; metrics: Record<string, unknown> } | null;
  selection: Sha256 | "no_winner" | null; cache_seed: "published" | "failed" | "missing" | null;
}
export interface GraphRun {
  run_id: string; status: "RUNNING" | "PAUSED" | "CANCELED" | "COMPLETED" | "FAILED"; simulation: boolean;
  plan_sha256: Sha256; approved_cost_usd: number; reserved_cost_usd: number; budget_exhausted: boolean;
  unpriced_attempts: number; created: number;
  tracking: { events: Record<string, number>; graph: TrackingStatus; action: string | null }; nodes: GraphRunNode[];
}
export interface UnreadableRun { run_id: string; error: string }
export interface RunListEntry { run_id: string; status: GraphRun["status"]; simulation: boolean; plan_sha256: Sha256; created: number; nodes_by_status: Record<string, number> }
export interface TrackingFailure { event_id: Sha256; kind: string; node_id: string | null; status: "FAILED" | "UNCERTAIN"; tries: number; last_error: string | null; action: string | null }
export interface RunList { runs: (RunListEntry | UnreadableRun)[]; controller: ControllerView }
export interface RunDetail { run: GraphRun | UnreadableRun; controller: ControllerView; plan: PlanSummary | null; tracking_failures: TrackingFailure[] }
export interface RunSteered { run: GraphRun | UnreadableRun; controller: ControllerView }
export interface SubmitResult { run_id: string; status: string; simulation: boolean; plan_sha256: Sha256; controller: ControllerView }
/** `graph_submit`'s `conflict` data with the issue `plan_submitted` at `/plan_sha256` (11.5): the run already registered
 * for this plan under another request ID, whatever its status (a cancelled run too). */
export interface PlanSubmitted { run_id: string; status: GraphRun["status"] }
export interface TrackingRearmed extends WithWarnings { event_id: Sha256; run_id: string; node_id: string | null; kind: string; status: string }
