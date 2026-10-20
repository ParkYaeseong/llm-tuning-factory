// Project drafts in memory (D4): the saved revision, the edited spec, dirty state, issues, conflicts (D5), planning
// inputs and the held plan with the basis it is valid for (D8). Pure: the bridge is injected, so Node tests use a fake.
import type { BindingRole, DatasetBinding, PlanSummary, PlanningEstimate, ProjectRevision, ProjectSpec, Sha256,
  WorkflowIssue, WorkflowResult } from "./workflow-types";

export interface ProjectStoreBridge {
  get(projectId: string, revision?: Sha256): Promise<WorkflowResult<ProjectRevision>>;
  save(spec: ProjectSpec, expected: Sha256): Promise<WorkflowResult<ProjectRevision>>;
  validate(projectId: string, revision: Sha256): Promise<WorkflowResult<unknown>>;
}
export interface PlanningInputs {
  simulation: boolean;                                           // default true (D8)
  nodeResources: Record<string, { timeout_seconds: number; runtime_profile: "primary" | "ppo" }>;
  estimates: PlanningEstimate[];
}
export interface PlanBasis { projectRevision: Sha256; recipeRevisions: Sha256[]; profilesGeneration: number; settingsGeneration: number }
export interface HeldPlan { summary: PlanSummary; basis: PlanBasis; requestId: string }
export interface ProjectDraft {
  projectId: string; saved: ProjectRevision | null; spec: ProjectSpec; dirty: boolean;
  issues: WorkflowIssue[]; warnings: WorkflowIssue[]; conflict: WorkflowIssue[] | null;
  planning: PlanningInputs; plan: HeldPlan | null; planDropped: string | null;   // Korean reason shown once
}
export interface ProjectStore {
  draft(projectId: string): ProjectDraft | undefined;
  adopt(revision: ProjectRevision): ProjectDraft;               // after create or legacy import
  open(projectId: string): Promise<WorkflowResult<ProjectRevision> | null>;     // null: a dirty draft is kept
  edit(projectId: string, change: (spec: ProjectSpec) => void, reason?: string): void; // dirty, drops the plan
  save(projectId: string): Promise<WorkflowResult<ProjectRevision>>;            // CAS; conflict keeps the draft
  validate(projectId: string): Promise<WorkflowResult<unknown>>;                // the saved revision only
  reloadLatest(projectId: string): Promise<WorkflowResult<ProjectRevision>>;    // discards the draft
  overwriteWithDraft(projectId: string): Promise<WorkflowResult<ProjectRevision>>;
  setPlanning(projectId: string, change: (inputs: PlanningInputs) => void): void; // drops the plan
  holdPlan(projectId: string, summary: PlanSummary, recipeRevisions: Sha256[]): HeldPlan;
  dropPlan(projectId: string, reason: string): void;
  planUsable(projectId: string): boolean;                        // basis matches and the draft is clean
  markProfilesChanged(): void; markSettingsChanged(): void; markRecipeChanged(previousRevision: Sha256): void;
}

export const PLAN_DROPPED = {
  edit: "프로젝트 초안이 바뀌었습니다. 계획을 다시 확인하세요.",
  planning: "계획 입력이 바뀌었습니다. 계획을 다시 확인하세요.",
  revision: "프로젝트가 다른 버전으로 바뀌었습니다. 계획을 다시 확인하세요.",
  profiles: "실행 프로필이 바뀌었습니다. 계획을 다시 확인하세요.",
  settings: "연결 설정이 바뀌었습니다. 계획을 다시 확인하세요.",
  recipe: "계획에 쓴 레시피가 바뀌었습니다. 계획을 다시 확인하세요.",
} as const;

/** Key-order independent JSON, so a draft edited back to its saved values is clean again. */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical((value as Record<string, unknown>)[key])).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
const sameSpec = (a: ProjectSpec, b: ProjectSpec | undefined) => b !== undefined && canonical(a) === canonical(b);

export function createProjectStore(bridge: ProjectStoreBridge, newRequestId: () => string = () => crypto.randomUUID()): ProjectStore {
  const drafts = new Map<string, ProjectDraft>();
  // Planning inputs that no backend stores stay per project for the session, across reloads of its draft.
  const planningInputs = new Map<string, PlanningInputs>();
  // One request ID per plan hash, reused when the same plan is approved again after an error (D8).
  const requestIds = new Map<Sha256, string>();
  let profilesGeneration = 0, settingsGeneration = 0;

  const required = (projectId: string) => {
    const found = drafts.get(projectId);
    if (!found) throw new Error(`no draft for project ${projectId}`);
    return found;
  };
  const savedRevision = (draft: ProjectDraft) => {
    if (!draft.saved) throw new Error(`project ${draft.projectId} has no saved revision`);
    return draft.saved.revision_sha256;
  };
  function drop(draft: ProjectDraft, reason: string) {
    if (!draft.plan) return;
    draft.plan = null; draft.planDropped = reason;
  }
  function planningFor(projectId: string) {
    let inputs = planningInputs.get(projectId);
    if (!inputs) { inputs = { simulation: true, nodeResources: {}, estimates: [] }; planningInputs.set(projectId, inputs); }
    return inputs;
  }
  /** The saved revision becomes `revision`; the draft follows it unless it changed since `sent` was saved. */
  function settle(draft: ProjectDraft, revision: ProjectRevision, sent?: ProjectSpec) {
    draft.saved = structuredClone(revision);
    if (!sent || sameSpec(draft.spec, sent)) draft.spec = structuredClone(revision.project);
    draft.dirty = !sameSpec(draft.spec, draft.saved.project);
    draft.conflict = null; draft.issues = []; draft.warnings = [];
    if (draft.plan && draft.plan.basis.projectRevision !== revision.revision_sha256) drop(draft, PLAN_DROPPED.revision);
  }
  async function saveAgainst(draft: ProjectDraft, expected: Sha256) {
    const sent = structuredClone(draft.spec);
    const result = await bridge.save(sent, expected);
    if (result.status === "ok" && result.data) settle(draft, result.data, sent);
    else if (result.status === "conflict") draft.conflict = result.issues;
    else draft.issues = result.issues;
    return result;
  }

  const store: ProjectStore = {
    draft: projectId => drafts.get(projectId),
    adopt(revision) {
      const projectId = revision.project.project_id;
      let draft = drafts.get(projectId);
      if (!draft) {
        draft = { projectId, saved: null, spec: structuredClone(revision.project), dirty: false, issues: [], warnings: [],
          conflict: null, planning: planningFor(projectId), plan: null, planDropped: null };
        drafts.set(projectId, draft);
      }
      settle(draft, revision);
      return draft;
    },
    async open(projectId) {
      if (drafts.get(projectId)?.dirty) return null;
      const result = await bridge.get(projectId);
      if (result.status === "ok" && result.data && !drafts.get(projectId)?.dirty) store.adopt(result.data);
      return result;
    },
    edit(projectId, change, reason) {
      const draft = required(projectId);
      change(draft.spec);
      draft.dirty = !sameSpec(draft.spec, draft.saved?.project);
      drop(draft, reason ?? PLAN_DROPPED.edit);
    },
    save(projectId) {
      const draft = required(projectId);
      return saveAgainst(draft, savedRevision(draft));
    },
    async validate(projectId) {
      const draft = required(projectId);
      const result = await bridge.validate(projectId, savedRevision(draft));
      draft.issues = result.status === "ok" ? [] : result.issues;
      return result;
    },
    async reloadLatest(projectId) {
      const draft = required(projectId);
      const result = await bridge.get(projectId);
      if (result.status === "ok" && result.data) settle(draft, result.data);
      return result;
    },
    async overwriteWithDraft(projectId) {
      const draft = required(projectId);
      const latest = await bridge.get(projectId);
      if (latest.status !== "ok" || !latest.data) return latest;
      return saveAgainst(draft, latest.data.revision_sha256);
    },
    setPlanning(projectId, change) {
      const draft = required(projectId);
      change(draft.planning);
      drop(draft, PLAN_DROPPED.planning);
    },
    holdPlan(projectId, summary, recipeRevisions) {
      const draft = required(projectId);
      let requestId = requestIds.get(summary.plan_sha256);
      if (!requestId) { requestId = newRequestId(); requestIds.set(summary.plan_sha256, requestId); }
      draft.plan = { summary: structuredClone(summary), requestId, basis: { projectRevision: summary.project_revision,
        recipeRevisions: [...recipeRevisions], profilesGeneration, settingsGeneration } };
      draft.planDropped = null;
      return draft.plan;
    },
    dropPlan(projectId, reason) { drop(required(projectId), reason); },
    planUsable(projectId) {
      const draft = drafts.get(projectId);
      const basis = draft?.plan?.basis;
      return Boolean(draft && basis && !draft.dirty && draft.saved?.revision_sha256 === basis.projectRevision
        && basis.profilesGeneration === profilesGeneration && basis.settingsGeneration === settingsGeneration);
    },
    markProfilesChanged() {
      profilesGeneration++;
      for (const draft of drafts.values()) drop(draft, PLAN_DROPPED.profiles);
    },
    markSettingsChanged() {
      settingsGeneration++;
      for (const draft of drafts.values()) drop(draft, PLAN_DROPPED.settings);
    },
    markRecipeChanged(previousRevision) {
      for (const draft of drafts.values())
        if (draft.plan?.basis.recipeRevisions.includes(previousRevision)) drop(draft, PLAN_DROPPED.recipe);
    },
  };
  return store;
}

/** "train", "validation", "test" for the first binding of a role, then "-2", "-3", … (10.2 and 10.3 share it). */
export function bindingIdFor(role: BindingRole, existing: DatasetBinding[]): string {
  const taken = new Set(existing.map(binding => binding.binding_id));
  if (!taken.has(role)) return role;
  let suffix = 2;
  while (taken.has(`${role}-${suffix}`)) suffix++;
  return `${role}-${suffix}`;
}
