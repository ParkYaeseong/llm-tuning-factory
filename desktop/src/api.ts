// The typed bridge of the workflow channel: the only module that calls `invoke` for Task 10 (D3).
// It never logs request data; native `Err` strings (Korean) become BridgeErrors shown verbatim.
import { invoke } from "@tauri-apps/api/core";
import { WORKFLOW_ACTIONS, type BenchmarkRevision, type BenchmarkSpec, type ControllerStatus, type ControllerStopResult,
  type ControllerView, type GraphRun, type GraphValidation, type ImportMapping, type ImportPreview, type ImportRegistered, type ImportSource,
  type LegacyImport, type ModelRoot, type Observation, type PlanningEstimate, type PlanRequest, type PlanSummary, type ProjectRevision,
  type ProjectSpec, type ProjectSummary, type PublicProfile, type RecipeRevision, type RecipeSpec, type RecipeSummary,
  type RootSnapshot, type RootTargets, type RunDetail, type RunList, type RunSteered, type Sha256, type SplitCommitted,
  type SplitPlan, type SplitPreview, type PlanSubmitted, type SubmitResult, type TrackingRearmed, type UnreadableRun, type WorkflowAction,
  type WorkflowResult } from "./workflow-types";

export class BridgeError extends Error {
  constructor(message: string) { super(message); this.name = "BridgeError"; }
}
export const MALFORMED = "엔진 응답 형식이 올바르지 않습니다.";
const STATUSES = new Set(["ok", "invalid", "conflict"]);
const isObject = (value: unknown): value is Record<string, unknown> =>
  typeof value === "object" && value !== null && !Array.isArray(value);
const validIssue = (value: unknown) => isObject(value) && typeof value.code === "string" && typeof value.path === "string"
  && typeof value.message === "string" && (value.node_id == null || typeof value.node_id === "string")
  && (value.row_number == null || typeof value.row_number === "number");

/** The value itself when it is a WorkflowResult envelope; otherwise BridgeError(MALFORMED). */
export function parseWorkflowResult<T>(value: unknown): WorkflowResult<T> {
  if (!isObject(value) || typeof value.status !== "string" || !STATUSES.has(value.status) || !Array.isArray(value.issues)
      || !value.issues.every(validIssue) || !("data" in value) || value.data === undefined)
    throw new BridgeError(MALFORMED);
  return value as unknown as WorkflowResult<T>;
}

const toBridgeError = (reason: unknown): never => { throw new BridgeError(String(reason)); };

export async function workflow<T>(action: WorkflowAction, data: object = {}): Promise<WorkflowResult<T>> {
  if (!(WORKFLOW_ACTIONS as readonly string[]).includes(action)) throw new BridgeError("허용하지 않은 작업");
  return parseWorkflowResult<T>(await invoke("workflow_action", { action, data }).catch(toBridgeError));
}

export const projects = {
  list: () => workflow<{ projects: ProjectSummary[] }>("projects_list"),
  create: (r: { request_id: string; name: string; purpose: string; task_type: string }) => workflow<ProjectRevision>("project_create", r),
  get: (project_id: string, revision?: Sha256) => workflow<ProjectRevision>("project_get", revision ? { project_id, revision } : { project_id }),
  save: (spec: ProjectSpec, expected_revision: Sha256) => workflow<ProjectRevision>("project_save", { spec, expected_revision }),
  importLegacy: (request_id: string, ai_id: string) => workflow<LegacyImport>("project_import_legacy", { request_id, ai_id }),
};
export const recipes = {
  list: () => workflow<{ recipes: RecipeSummary[] }>("recipes_list"),
  get: (recipe_id: string, revision?: Sha256) => workflow<RecipeRevision>("recipe_get", revision ? { recipe_id, revision } : { recipe_id }),
  save: (spec: RecipeSpec, expected_revision: Sha256 | null) => workflow<RecipeRevision>("recipe_save", { spec, expected_revision }),
  clone: (r: { recipe_id: string; revision?: Sha256; name?: string; new_recipe_id?: string; version?: string }) => workflow<RecipeRevision>("recipe_clone", r),
};
export const imports = {
  source: (path: string) => workflow<ImportSource>("dataset_import_source", { path }),
  /** `invalid` still carries the preview (samples, source fields) when rows fail. */
  preview: (source_id: Sha256, mapping: ImportMapping) => workflow<ImportPreview>("dataset_import_preview", { source_id, mapping }),
  register: (r: { source_id: Sha256; mapping: ImportMapping; preview_sha256: Sha256; dataset_id: string; version: string; synthetic: boolean; export_allowed: boolean }) =>
    workflow<ImportRegistered>("dataset_import_register", r),
  splitPreview: (plan: SplitPlan) => workflow<SplitPreview>("dataset_split_preview", { plan }),
  splitCommit: (plan_sha256: Sha256) => workflow<SplitCommitted>("dataset_split_commit", { plan_sha256 }),
};
export const planning = {
  inspect: (profile_id: string) => workflow<Observation>("executor_profile_inspect", { profile_id }),
  rootSnapshot: (repo_id: string, revision: string) => workflow<RootSnapshot>("model_root_snapshot", { repo_id, revision, download_approved: true }),
  rootTargets: (repo_id: string, revision: string) => workflow<RootTargets>("model_root_targets", { repo_id, revision, download_approved: true }),
  roots: () => workflow<{ roots: ModelRoot[] }>("model_roots_list"),
  benchmarkSave: (spec: BenchmarkSpec) => workflow<BenchmarkRevision>("benchmark_save", { spec }),
  benchmarks: () => workflow<{ benchmarks: BenchmarkRevision[] }>("benchmarks_list"),
  validate: (project_id: string, revision: Sha256) => workflow<GraphValidation>("graph_validate", { project_id, revision }),
  plan: (request: PlanRequest) => workflow<PlanSummary>("graph_plan", request),
  planGet: (plan_sha256: Sha256) => workflow<PlanSummary>("graph_plan_get", { plan_sha256 }),
};
// 9.8 read-only actions used by the executor profiles screen (10.4). Their shapes are declared here because
// workflow-types.ts stays as it is for that sub-task (workflow_planning.observations_list / executor_profile_inventory).
export interface ObservationSummary {
  profile_id: string; revision: Sha256; observed_at: string; age_seconds: number; provider: "existing_runpod" | "ssh_server";
  name: string; resource_id: string; gpu_type: string; gpu_count: number; total_hourly_cost_usd: number; cost_source: string;
  selected_gpu_uuids: string[]; profile_current: boolean | null; fresh_for_real_plan: boolean;
}
export interface ProfileInventory {
  profile_id: string; revision: Sha256; observed_at: string; server_id: Sha256; resource_id: string;
  inventory: { uuid: string; name: string; memory_mib: number }[]; selected_gpu_uuids: string[];
}
/** Each profile's last stored observation; a record that does not verify is `{profile_id, error}`. DB/file only. */
export const observationsList = () => workflow<{ observations: (ObservationSummary | { profile_id: string; error: string })[] }>("observations_list");
/** The GPU listing of a saved SSH profile (contacts the server read-only; stores nothing). */
export const profileInventory = (profile_id: string) => workflow<ProfileInventory>("executor_profile_inventory", { profile_id });
// 9.8 planning inputs, used by the plan panel (10.6). Their shapes are declared here because workflow-types.ts stays as
// it is for that sub-task (workflow_planning.planning_inputs_get / planning_inputs_save).
export interface NodeTimeLimit { node_id: string; timeout_minutes: number }      // 1–10080 whole minutes
export interface StoredPlanningInputs {
  project_revision: Sha256; simulation: boolean; node_time_limits: NodeTimeLimit[]; estimates: PlanningEstimate[]; saved_at: string;
}
/** Both actions answer the project's current revision beside its stored inputs (null before the first save); inputs
 * saved for an earlier revision come back as they are, with their own `project_revision`. */
export interface PlanningInputsView { project_id: string; current_project_revision: Sha256; inputs: StoredPlanningInputs | null }
export interface PlanningInputsSave {
  project_id: string; project_revision: Sha256; simulation: boolean; node_time_limits: NodeTimeLimit[]; estimates: PlanningEstimate[];
}
export const planningInputs = {
  /** A read: the stored inputs of a project. */
  get: (project_id: string) => workflow<PlanningInputsView>("planning_inputs_get", { project_id }),
  /** Replaces them, only for the project's current revision (`revision_conflict` otherwise); never a secret. */
  save: (request: PlanningInputsSave) => workflow<PlanningInputsView>("planning_inputs_save", request),
};
export const runs = {
  /** `ok`: the run (`SubmitResult`); `conflict` `plan_submitted`: the run already registered for the plan (`PlanSubmitted`). */
  submit: (r: { plan_sha256: Sha256; request_id: string; approve_execution: boolean; approve_data_transfer: boolean }) =>
    workflow<SubmitResult | PlanSubmitted>("graph_submit", r),
  list: () => workflow<RunList>("graph_runs_list"),
  get: (run_id: string) => workflow<RunDetail>("graph_run_get", { run_id }),
  pause: (run_id: string) => workflow<RunSteered>("graph_pause", { run_id }),
  resume: (run_id: string) => workflow<RunSteered>("graph_resume", { run_id }),
  cancel: (run_id: string) => workflow<RunSteered>("graph_cancel", { run_id }),
  rearm: (event_id: Sha256) => workflow<TrackingRearmed>("tracking_rearm", { event_id }),
};
// 9.8 graph_release_lock, used by the graph runs page (10.7). Its shapes are declared here because workflow-types.ts
// stays as it is for that sub-task (workflow_runs.graph_release_lock).
export interface LockRelease { run_id: string; node_id: string; job_id: string; attempt_id: string }
export interface LockReleased { released: LockRelease; run: GraphRun | UnreadableRun; controller: ControllerView }
/** The desktop operator release of one uncertain attempt the operator confirmed stopped on the server or Pod (the CLI's
 * `release-lock --confirm-execution-stopped`). The caller passes the confirmation explicitly; the backend refuses
 * anything but the node's current attempt while it needs the operator and no late result is plausible. */
export const releaseLock = (request: LockRelease & { confirm_execution_stopped: true }) =>
  workflow<LockReleased>("graph_release_lock", request);

// Native commands (Tauri converts the camelCase argument keys to snake_case parameters).
export const executorProfiles = () => invoke<{ profiles: PublicProfile[] }>("executor_profiles_get").catch(toBridgeError);
export const saveExecutorProfile = (profileId: string, values: Record<string, string>, copyLegacyCredential = false) =>
  invoke<{ profile: PublicProfile; affected_runs: string[] }>("executor_profile_save", { profileId, values, copyLegacyCredential }).catch(toBridgeError);
export const deleteExecutorProfile = (profileId: string) =>
  invoke<{ profile_id: string; affected_runs: string[] }>("executor_profile_delete", { profileId }).catch(toBridgeError);
export const startController = () => invoke<{ started: boolean; reason?: "already_running" }>("controller_start").catch(toBridgeError);
export const controllerStatus = async () =>
  parseWorkflowResult<ControllerStatus>(await invoke("controller_status").catch(toBridgeError));
export const stopController = async () =>
  parseWorkflowResult<ControllerStopResult>(await invoke("controller_stop").catch(toBridgeError));
export const pickFile = (kind: "dataset" | "key" | "import") => invoke<string | null>("pick_file", { kind }).catch(toBridgeError);
/** A client request ID for retry-safe creation and submission (matches desktop_workflows.RequestId). */
export const newRequestId = () => crypto.randomUUID();
