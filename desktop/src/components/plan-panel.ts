// The plan panel of the graph page (10.6; D5, D8, D9 and the root decision on planning inputs). Planning inputs (the
// dry-run choice, each non-select card's maximum run time, estimates for dry runs) are kept by 9.8's
// planning_inputs_get/_save: read when the page is entered, saved by "계획 확인" when they changed, never naming a card
// the saved graph no longer has. "계획 확인" plans the saved revision (graph_plan); issues and warnings go to their
// sections (profiles, estimates, MLflow, time limits, data, cards). A held plan stays usable only while its basis holds
// (project-store.ts); its approval dialog (plan-dialog.ts) leads to graph_submit with the plan's one request ID. The
// panel never starts the controller (D9). Every dynamic text is escaped.
import "./plan-panel.css";
import { MALFORMED, planning, planningInputs, runs, type PlanningInputsSave, type StoredPlanningInputs } from "../api";
import { readGraph, waitingOnSharedProfile, type GraphJson, type NodeJson } from "../graph-model";
import { issueList, localize, planSection } from "../issues";
import { PLAN_DROPPED, type PlanningInputs, type ProjectDraft, type ProjectStore } from "../project-store";
import type { ViewContext } from "../view-types";
import type { InspectionSummary } from "../views/executor-profiles";
import { button, errorText, escapeHtml, focusKey, restoreFocus, statusPill } from "../workbench";
import type { ControllerView, PlanningEstimate, PlanRequest, PlanSummary, PublicProfile, RecipeSummary, Sha256, WorkflowIssue,
  WorkflowResult } from "../workflow-types";
import { COST_NOTE, durationText, kindLabel, openApproval, profileName, profileText, usd, WAITING_TEXT } from "./plan-dialog";

type Obj = Record<string, any>;
/** What the panel shows besides the draft. `inspections` = sessionInspections() of views/executor-profiles.ts (10.4);
 * `usable` = store.planUsable(project). */
export interface PlanView {
  profiles: readonly PublicProfile[]; inspections: Readonly<Record<string, InspectionSummary>>;
  settings: Readonly<Record<string, unknown>>; recipes: ReadonlyMap<Sha256, RecipeSummary>; usable: boolean;
}
/** What planning and submitting need from the page: its recipes (PPO runtime), profiles (names) and its queue (D10). */
export interface PlanEnv {
  recipes: ReadonlyMap<Sha256, RecipeSummary>; profiles: readonly PublicProfile[];
  queue: { run<T>(work: () => Promise<T>): Promise<T> };
}

export const DEFAULT_MINUTES = 60, MAX_MINUTES = 10080;
const MAX_ESTIMATES = 16;
const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const NOT_SAVED = "먼저 저장하세요 · 계획은 저장한 버전으로만 만듭니다.";
const NO_STEPS = "학습 단계를 먼저 추가하고 저장하세요.";
const NO_MLFLOW = "MLflow 서버 주소가 필요합니다 · 연결 설정에서 주소를 저장하세요.";
const BAD_LIMIT = "최대 실행 시간은 1–10080분 사이의 정수로 입력하세요";
const INSPECT_HINT = "실행 프로필 자원을 1시간 이내에 확인하세요";
const CHANGED_WHILE_PLANNING = "계획을 만드는 동안 실행 프로필이나 연결 설정이 바뀌었습니다. 계획을 다시 확인하세요.";
const MISMATCH = "엔진이 요청과 다른 프로젝트·버전·실행 종류의 계획을 보냈습니다. 다시 계획하세요.";
const PLAN_REFUSED = "계획을 만들 수 없습니다. 아래 확인할 점을 고친 뒤 다시 계획하세요.";
const INPUTS_REFUSED = "계획 입력을 저장하지 못해 계획하지 않았습니다. 아래 확인할 점을 보세요.";
const DROPPED_PREFIX = "변경되어 다시 계획해야 합니다: ";
/** Fixed texts of non-blocking warnings shown at their profile; the backend text stays under "원문 보기". */
const WARNING_TEXTS: Record<string, string> = {
  observation_revision_differs: "자원 확인 후 프로필이 바뀌었습니다 · 모의 계획은 확인 당시 자원으로 계산했습니다",
};

// -- module state (session only; the backend keeps the planning inputs and the plans) --
interface Feedback { revision: Sha256; issues: WorkflowIssue[]; warnings: WorkflowIssue[]; estimates: string[]; nodes: string[] }
const feedback = new Map<string, Feedback>();            // project → the last plan (or refused inputs save) answer
const submitFeedback = new Map<string, { plan: Sha256; issues: WorkflowIssue[] }>();
const submittedRuns = new Map<Sha256, string>();         // plan hash → the run registered from this app
const storedInputs = new Map<string, StoredPlanningInputs | null>();   // project → as last read or saved
const pendingApply = new Map<string, StoredPlanningInputs | null>();   // project → a read not yet applied to its draft
const changedInputs = new Set<string>();                 // projects whose inputs this session changed since then
const inputsNote = new Map<string, string>();            // project → why stored inputs could not be read or saved
const limitText = new Map<string, string>();             // project + node → typed text that is not a valid limit
interface EstimateDraft { projectId: string; name: string; gpuType: string; count: string; price: string; problems: string[] }
let estimateDraft: EstimateDraft | null = null;

const isObject = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const isIssue = (value: unknown): value is WorkflowIssue => isObject(value) && typeof value.code === "string"
  && typeof value.path === "string" && typeof value.message === "string";
const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => {
  const { text, original } = localize(issue);
  return original ? `${text} (${original})` : text;
}).join(" ") || "요청을 처리하지 못했습니다.";
const limitKey = (projectId: string, nodeId: string) => `${projectId}\u0000${nodeId}`;
const planned = (graph: GraphJson) => graph.nodes.filter(node => node.kind !== "select");
const labelText = (node: NodeJson) => typeof node.label === "string" && node.label.trim() ? node.label : node.id;
const profilesOf = (node: NodeJson): string[] => Array.isArray((node as Obj).executor_profile_ids) ? (node as Obj).executor_profile_ids.map(String) : [];
const savedGraph = (draft: ProjectDraft) => readGraph(draft.saved?.project.graph ?? {});
/** Whether the user is still on the page an action started from (its follow-up may only then move the focus). */
const stillOn = (ctx: ViewContext) => { const page = ctx.page, arg = ctx.arg; return () => ctx.page === page && ctx.arg === arg; };
const focusIfLost = (still: () => boolean, key: string) => {
  if (still() && (!document.activeElement || document.activeElement === document.body)) restoreFocus(document, key);
};
/** Key-order independent JSON (as project-store.ts compares drafts). */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}

// -- requests --
const secondsOf = (draft: ProjectDraft, nodeId: string) => draft.planning.nodeResources[nodeId]?.timeout_seconds ?? DEFAULT_MINUTES * 60;
/** PPO recipes run on the "ppo" runtime; every other step on "primary" (graph-model's PPO routes assume this). */
export function runtimeOf(node: NodeJson, recipes: ReadonlyMap<Sha256, RecipeSummary>): "primary" | "ppo" {
  const revision = (node as Obj).recipe_revision;
  return node.kind === "train" && typeof revision === "string" && recipes.get(revision)?.training_stage === "ppo" ? "ppo" : "primary";
}

/**
 * The graph_plan request of a saved draft: `node_resources` for every non-select node of `graph` (the saved graph:
 * its timeout from the inputs, default 60 minutes; runtime "ppo" for PPO recipes), pinned to the saved revision.
 * Estimates: all of them for a dry run; for a real plan only those a card uses, so the backend reports `estimate_real`
 * at that estimate instead of an unknown profile on the card.
 */
export function planRequest(draft: ProjectDraft, graph: GraphJson, recipes: ReadonlyMap<Sha256, RecipeSummary>): PlanRequest {
  if (!draft.saved) throw new Error(NOT_SAVED);
  const nodes = planned(graph);
  const used = new Set(nodes.flatMap(profilesOf));
  const simulation = draft.planning.simulation;
  return { project_id: draft.projectId, project_revision: draft.saved.revision_sha256, simulation,
    node_resources: nodes.map(node => ({ node_id: node.id, timeout_seconds: secondsOf(draft, node.id), runtime_profile: runtimeOf(node, recipes) })),
    estimates: draft.planning.estimates.filter(estimate => simulation || used.has(estimate.profile_id)).map(estimate => ({ ...estimate })) };
}

/** Cards of the draft whose typed maximum run time is not a whole number of minutes in 1–10080. */
function badLimits(draft: ProjectDraft): string[] {
  return planned(readGraph(draft.spec.graph)).filter(node => limitText.has(limitKey(draft.projectId, node.id))).map(labelText);
}

/**
 * Why "계획 확인" is disabled: an unsaved or dirty draft, a saved graph without steps, a time limit out of range, and for
 * a real plan an MLflow address missing from the saved settings. These are hints from what the session knows; the
 * backend decides everything else (a stale resource check is shown as a hint, not a blocker: a check stored by an
 * earlier session may still be fresh).
 */
export function planBlockers(draft: ProjectDraft, settings: Readonly<Record<string, unknown>>): string[] {
  const out: string[] = [];
  if (!draft.saved || draft.dirty) out.push(NOT_SAVED);
  else if (!planned(savedGraph(draft)).length) out.push(NO_STEPS);
  const bad = badLimits(draft);
  if (bad.length) out.push(`${BAD_LIMIT}: ${bad.join(", ")}`);
  if (!draft.planning.simulation && !String(settings.MLFLOW_TRACKING_URI ?? "").trim()) out.push(NO_MLFLOW);
  return out;
}

/** The steps that wait for another parallel step on the same single destination (by the plan's resource IDs; the
 * first in plan order runs, the later ones wait). */
export function planWaiting(summary: PlanSummary, graph: GraphJson): Set<string> {
  const single = new Map<string, string>();
  for (const node of Array.isArray(summary.nodes) ? summary.nodes : []) {
    const ids = [...new Set((Array.isArray(node.allowed) ? node.allowed : []).map(resource => String(resource.resource_id)))];
    if (ids.length === 1) single.set(node.node_id, ids[0]);
  }
  const order = [...single.keys()];
  const waiting = new Set<string>();
  for (const [index, id] of order.entries()) {
    const resource = single.get(id)!;
    if (order.slice(0, index).some(earlier => single.get(earlier) === resource
        && waitingOnSharedProfile(graph, new Map([[earlier, [resource]], [id, [resource]]])).has(id))) waiting.add(id);
  }
  return waiting;
}

/** Plan issues and warnings that belong on the editor's cards and fields (`/graph/...`), for the saved revision they
 * were planned from. */
export function planFeedback(draft: ProjectDraft): { issues: WorkflowIssue[]; warnings: WorkflowIssue[] } {
  const found = feedback.get(draft.projectId);
  if (!found || found.revision !== draft.saved?.revision_sha256) return { issues: [], warnings: [] };
  const onCards = (list: WorkflowIssue[]) => list.filter(issue => planSection(issue).section === "graph");
  return { issues: onCards(found.issues), warnings: onCards(found.warnings) };
}

// -- planning inputs (9.8) --
function validStored(value: unknown): StoredPlanningInputs | null | undefined {
  if (value === null) return null;
  if (!isObject(value) || typeof value.simulation !== "boolean" || !Array.isArray(value.node_time_limits) || !Array.isArray(value.estimates)) return undefined;
  const limits = value.node_time_limits.every(item => isObject(item) && typeof item.node_id === "string" && Number.isInteger(item.timeout_minutes));
  const estimates = value.estimates.every(item => isObject(item) && typeof item.profile_id === "string" && typeof item.gpu_type === "string"
    && Number.isInteger(item.gpu_count) && typeof item.total_hourly_cost_usd === "number");
  return limits && estimates ? value as StoredPlanningInputs : undefined;
}

/** `planning_inputs_get` (read-only), kept until `applyPlanningInputs` once the draft is open. */
export async function readPlanningInputs(projectId: string): Promise<void> {
  const failed = (why: string) => inputsNote.set(projectId, `저장된 계획 입력을 불러오지 못했습니다. ${why} · 이 화면의 값으로 계획합니다.`);
  try {
    const result = await planningInputs.get(projectId);
    const inputs = result.status === "ok" && isObject(result.data) && result.data.project_id === projectId ? validStored(result.data.inputs) : undefined;
    if (inputs === undefined) { failed(result.status === "ok" ? MALFORMED : notAvailable(result) ? NOT_AVAILABLE : describe(result.issues)); return; }
    storedInputs.set(projectId, inputs); pendingApply.set(projectId, inputs); inputsNote.delete(projectId);
  } catch (error) { failed(errorText(error)); }
}

const inputsKey = (inputs: PlanningInputs) => canonical({ simulation: inputs.simulation, estimates: inputs.estimates,
  limits: Object.fromEntries(Object.entries(inputs.nodeResources).map(([id, value]) => [id, value.timeout_seconds])) });

/** Puts the inputs just read into the open draft: never over this session's unsaved changes, only for cards the saved
 * graph has, and (through the store) dropping a held plan only when they differ. */
export function applyPlanningInputs(store: ProjectStore, projectId: string): void {
  const draft = store.draft(projectId);
  if (!draft || !pendingApply.has(projectId)) return;
  const record = pendingApply.get(projectId)!;
  pendingApply.delete(projectId);
  if (!record || changedInputs.has(projectId)) return;
  const known = new Set(planned(savedGraph(draft)).map(node => node.id));
  const next: PlanningInputs = { simulation: record.simulation, estimates: record.estimates.map(estimate => ({ ...estimate })),
    nodeResources: Object.fromEntries(record.node_time_limits.filter(item => known.has(item.node_id) && item.timeout_minutes >= 1 && item.timeout_minutes <= MAX_MINUTES)
      .map(item => [item.node_id, { timeout_seconds: item.timeout_minutes * 60, runtime_profile: "primary" as const }])) };
  if (inputsKey(draft.planning) === inputsKey(next)) return;
  const replace = (inputs: PlanningInputs) => { inputs.simulation = next.simulation; inputs.nodeResources = next.nodeResources; inputs.estimates = next.estimates; };
  if (draft.plan) store.setPlanning(projectId, replace); else replace(draft.planning);
  for (const key of [...limitText.keys()]) if (key.startsWith(`${projectId}\u0000`)) limitText.delete(key);
}

const saveKey = (value: { simulation: boolean; node_time_limits: { node_id: string; timeout_minutes: number }[]; estimates: PlanningEstimate[] }) =>
  canonical({ simulation: value.simulation, node_time_limits: value.node_time_limits, estimates: value.estimates });

/** Saves the inputs before planning when they differ from what is stored: only the saved graph's non-select cards,
 * for its current revision. `not_available` plans with the screen's values; other refusals stop the plan. */
async function persistInputs(projectId: string, draft: ProjectDraft, graph: GraphJson, env: PlanEnv): Promise<boolean> {
  const body: PlanningInputsSave = { project_id: projectId, project_revision: draft.saved!.revision_sha256, simulation: draft.planning.simulation,
    node_time_limits: planned(graph).filter(node => draft.planning.nodeResources[node.id])
      .map(node => ({ node_id: node.id, timeout_minutes: Math.round(draft.planning.nodeResources[node.id].timeout_seconds / 60) })),
    estimates: draft.planning.estimates.map(estimate => ({ ...estimate })) };
  const before = storedInputs.get(projectId) ?? { simulation: true, node_time_limits: [], estimates: [] };
  if (saveKey(before) === saveKey(body)) { changedInputs.delete(projectId); return true; }
  const result = await env.queue.run(() => planningInputs.save(body));
  if (result.status === "ok" && isObject(result.data)) {
    const saved = validStored(result.data.inputs);
    storedInputs.set(projectId, saved ?? null);
    changedInputs.delete(projectId); inputsNote.delete(projectId);
    return true;
  }
  if (notAvailable(result)) {
    inputsNote.set(projectId, `계획 입력을 이 PC에 저장하지 못했습니다 (${NOT_AVAILABLE}) · 이번 계획은 화면의 값으로 만듭니다.`);
    return true;
  }
  // A time limit's issue goes to its card's row; an estimate's to its row (same order as the body).
  const issues = result.issues.map(issue => {
    const match = /^\/node_time_limits\/(\d+)/.exec(issue.path);
    return match ? { ...issue, path: "/node_resources", node_id: body.node_time_limits[Number(match[1])]?.node_id ?? issue.node_id ?? null } : issue;
  });
  feedback.set(projectId, { revision: body.project_revision, issues, warnings: [], estimates: body.estimates.map(item => item.profile_id), nodes: [] });
  return false;
}

// -- input edits (applied as they happen; a change drops a held plan through the store) --
function setLimit(store: ProjectStore, projectId: string, nodeId: string, text: string): boolean {
  const draft = store.draft(projectId);
  if (!draft) return false;
  const trimmed = text.trim();
  const minutes = /^\d{1,6}$/.test(trimmed) ? Number(trimmed) : NaN;
  if (!(minutes >= 1 && minutes <= MAX_MINUTES)) {
    limitText.set(limitKey(projectId, nodeId), text);
    if (draft.plan) store.dropPlan(projectId, PLAN_DROPPED.planning);   // what the screen shows is not what was planned
    return false;
  }
  limitText.delete(limitKey(projectId, nodeId));
  if (secondsOf(draft, nodeId) === minutes * 60) return true;
  store.setPlanning(projectId, inputs => {
    inputs.nodeResources = { ...inputs.nodeResources, [nodeId]: { timeout_seconds: minutes * 60, runtime_profile: "primary" } };
  });
  changedInputs.add(projectId);
  return true;
}
function setMode(store: ProjectStore, projectId: string, simulation: boolean) {
  const draft = store.draft(projectId);
  if (!draft || draft.planning.simulation === simulation) return;
  store.setPlanning(projectId, inputs => { inputs.simulation = simulation; });
  changedInputs.add(projectId);
}

const ESTIMATE_NAME = /^[A-Za-z0-9_.-]{1,56}$/;
const GPU_TYPE = /^[A-Za-z0-9][A-Za-z0-9 ._()-]*$/;
const estimateKey = (profileId: string) => profileId.toLowerCase().replace(/\.+$/, "");
function estimateProblems(entry: EstimateDraft, draft: ProjectDraft, profiles: readonly PublicProfile[]): string[] {
  const out: string[] = [];
  const name = entry.name.trim(), gpuType = entry.gpuType.trim();
  const id = `estimate-${name}`;
  if (!ESTIMATE_NAME.test(name)) out.push("추정 자원 이름은 영문·숫자와 _ . - 로 56자 이내로 입력하세요.");
  else if (draft.planning.estimates.some(estimate => estimateKey(estimate.profile_id) === estimateKey(id))) out.push("같은 이름의 추정 자원이 이미 있습니다.");
  else if (profiles.some(profile => estimateKey(profile.profile_id) === estimateKey(id))) out.push("저장된 실행 프로필 ID와 같은 이름은 쓸 수 없습니다.");
  if (!gpuType || gpuType.length > 100 || !GPU_TYPE.test(gpuType)) out.push("GPU 종류는 영문·숫자로 시작하고 영문·숫자·공백·._()- 만 써서 100자 이내로 입력하세요.");
  const count = Number(entry.count.trim());
  if (!/^\d+$/.test(entry.count.trim()) || count < 1 || count > 8) out.push("GPU 수는 1–8 사이의 정수로 입력하세요.");
  const price = Number(entry.price.trim());
  if (!entry.price.trim() || !Number.isFinite(price) || price <= 0 || price > 1000) out.push("시간당 총 요금은 0보다 크고 1000 이하인 USD 금액으로 입력하세요.");
  if (draft.planning.estimates.length >= MAX_ESTIMATES) out.push(`추정 자원은 ${MAX_ESTIMATES}개까지 둘 수 있습니다.`);
  return out;
}

// -- rendering --
function issueBlock(issues: WorkflowIssue[]): string {
  return issues.length ? issueList(issues) : "";
}
function warningList(warnings: WorkflowIssue[]): string {
  if (!warnings.length) return "";
  return `<ul class="plan-warnings">${warnings.map(warning => {
    const text = WARNING_TEXTS[warning.code] ?? localize(warning).text;
    const original = text !== warning.message ? `<details><summary>원문 보기</summary><span class="issue-original">${escapeHtml(warning.message)}</span></details>` : "";
    return `<li data-code="${escapeHtml(warning.code)}"><span>${escapeHtml(text)}</span>${original}</li>`;
  }).join("")}</ul>`;
}
const inSection = (list: WorkflowIssue[], section: string, key?: string) =>
  list.filter(issue => { const place = planSection(issue); return place.section === section && (key === undefined || place.key === key); });
/** Issues of one estimate row: `/estimates/<i>` names the request's i-th estimate. */
const estimateIssues = (found: Feedback | undefined, list: "issues" | "warnings", profileId: string) =>
  found ? found[list].filter(issue => { const place = planSection(issue); return place.section === "estimate" && found.estimates[Number(place.key)] === profileId; }) : [];
/** Issues of one card's time limit: by `node_id`, or `/node_resources/<i>` naming the request's i-th node. */
function limitIssues(found: Feedback | undefined, nodeId: string): WorkflowIssue[] {
  if (!found) return [];
  return found.issues.filter(issue => {
    if (planSection(issue).section !== "node_resources") return false;
    if (issue.node_id) return issue.node_id === nodeId;
    const match = /^\/node_resources\/(\d+)/.exec(issue.path);
    return match !== null && found.nodes[Number(match[1])] === nodeId;
  });
}
const current = (draft: ProjectDraft) => {
  const found = feedback.get(draft.projectId);
  return found && found.revision === draft.saved?.revision_sha256 ? found : undefined;
};

function modeHtml(draft: ProjectDraft): string {
  const radio = (value: "simulation" | "real", label: string, help: string) => {
    const checked = (value === "simulation") === draft.planning.simulation;
    return `<label class="check plan-radio"><input type="radio" name="plan-mode" value="${value}" data-plan-mode="${value}" data-focus-key="plan-mode-${value}"${checked ? " checked" : ""}>${label}</label><small>${help}</small>`;
  };
  return `<fieldset class="plan-mode"><legend>계획 종류</legend>
    ${radio("simulation", "모의 실행 계획 · 기본", "GPU를 쓰지 않고 데이터를 보내지 않습니다. 결과는 실제 품질이 아닙니다.")}
    ${radio("real", "실제 실행 계획", "승인하면 실행 자원에서 GPU 요금이 발생하고 데이터를 보냅니다.")}</fieldset>`;
}

function limitsHtml(draft: ProjectDraft, view: PlanView): string {
  const graph = readGraph(draft.spec.graph);
  const nodes = planned(graph);
  const found = current(draft);
  if (!nodes.length) return '<p class="muted">학습 단계를 추가하면 단계별 최대 실행 시간을 정할 수 있습니다.</p>';
  const rows = nodes.map(node => {
    const typed = limitText.get(limitKey(draft.projectId, node.id));
    const value = typed ?? String(Math.round(secondsOf(draft, node.id) / 60));
    const issues = limitIssues(found, node.id);
    const invalid = typed !== undefined || issues.length > 0;
    const label = labelText(node);
    return `<tr data-plan-node="${escapeHtml(node.id)}"><th scope="row"><span>${escapeHtml(label)}</span><small>${escapeHtml(kindLabel(node.kind))} · ${escapeHtml(node.id)}</small></th>
      <td><input type="number" min="1" max="${MAX_MINUTES}" step="1" inputmode="numeric" data-plan-limit="${escapeHtml(node.id)}" data-focus-key="plan-limit:${escapeHtml(node.id)}" aria-label="${escapeHtml(label)} 최대 실행 시간 (분)" value="${escapeHtml(value)}"${invalid ? ' aria-invalid="true"' : ""}>${issueBlock(issues)}</td>
      <td class="mono">${runtimeOf(node, view.recipes)}</td></tr>`;
  }).join("");
  const selects = graph.nodes.length - nodes.length;
  return `<table class="plan-limits"><caption>단계별 최대 실행 시간 · 기본 ${DEFAULT_MINUTES}분, 1–${MAX_MINUTES}분</caption>
    <thead><tr><th scope="col">단계</th><th scope="col">최대 실행 시간 (분)</th><th scope="col">실행 환경</th></tr></thead><tbody>${rows}</tbody></table>
    ${selects ? '<p class="muted">자동 선택 단계는 GPU를 쓰지 않아 시간 제한이 없습니다.</p>' : ""}`;
}

function estimatesHtml(draft: ProjectDraft, busy: boolean): string {
  const found = current(draft);
  const items = draft.planning.estimates.map(estimate => `<li class="plan-estimate" data-plan-estimate="${escapeHtml(estimate.profile_id)}" data-focus-key="estimate:${escapeHtml(estimate.profile_id)}" tabindex="-1">
      <div><b class="mono">${escapeHtml(estimate.profile_id)}</b><small>${escapeHtml(`${estimate.gpu_type} ×${estimate.gpu_count}`)} · ${escapeHtml(usd(estimate.total_hourly_cost_usd))}/시간</small></div>
      <button type="button" class="quiet" data-action="wf:graph:plan-estimate-remove:${escapeHtml(estimate.profile_id)}" aria-label="${escapeHtml(estimate.profile_id)} 삭제"${busy ? " disabled" : ""}>삭제</button>
      ${issueBlock(estimateIssues(found, "issues", estimate.profile_id))}${warningList(estimateIssues(found, "warnings", estimate.profile_id))}</li>`).join("");
  const open = estimateDraft?.projectId === draft.projectId;
  return `<div class="plan-estimates" role="group" aria-labelledby="plan-estimates-title"><h4 id="plan-estimates-title">계획용 추정 자원</h4>
    <p class="muted">${draft.planning.simulation ? "모의 실행 계획에만 씁니다. 카드의 실행 프로필에서 고를 수 있습니다." : "실제 실행 계획에는 쓸 수 없습니다. 카드가 고른 추정 자원은 계획에서 거부됩니다."}</p>
    ${items ? `<ul class="plan-estimate-list">${items}</ul>` : '<p class="muted">추정 자원이 없습니다. 기본 요금은 없으니 견적이나 요금표의 시간당 총액을 입력하세요.</p>'}
    ${open ? "" : `<div class="actions"><button type="button" data-action="wf:graph:plan-estimate-new" data-focus-key="plan-estimate-new"${busy ? " disabled" : ""}>추정 자원 추가</button></div>`}</div>`;
}

function estimateFormHtml(draft: ProjectDraft, busy: boolean): string {
  if (estimateDraft?.projectId !== draft.projectId) return "";
  const entry = estimateDraft;
  const input = (key: string, label: string, value: string, attributes: string) =>
    `<label class="field"><span>${label}</span><input id="plan-estimate-${key}" data-estimate="${key}" aria-label="${label}" ${attributes} value="${escapeHtml(value)}"></label>`;
  return `<form id="plan-estimate-form" class="plan-estimate-form" novalidate aria-labelledby="plan-estimate-title">
    <h4 id="plan-estimate-title">추정 자원 추가</h4><fieldset class="plan-fieldset"${busy ? " disabled" : ""}><div class="plan-estimate-grid">
    <label class="field"><span>추정 자원 이름</span><span class="plan-prefixed"><span class="mono" aria-hidden="true">estimate-</span><input id="plan-estimate-name" data-estimate="name" aria-label="추정 자원 이름" maxlength="56" spellcheck="false" value="${escapeHtml(entry.name)}"></span><small>영문·숫자와 _ . - · 56자 이내</small></label>
    ${input("gpu_type", "GPU 종류", entry.gpuType, 'maxlength="100" spellcheck="false" placeholder="예: NVIDIA A100"')}
    ${input("count", "GPU 수", entry.count, 'type="number" min="1" max="8" step="1"')}
    ${input("price", "시간당 총 요금 (USD)", entry.price, 'type="number" min="0" max="1000" step="any"')}
    </div></fieldset>
    ${entry.problems.length ? `<ul class="issue-list" id="plan-estimate-problems">${entry.problems.map(problem => `<li>${escapeHtml(problem)}</li>`).join("")}</ul>` : ""}
    <p class="muted">GPU 전체의 시간당 총액입니다. 모의 실행의 비용 추정에만 쓰고 어디에도 연결하지 않습니다.</p>
    <div class="actions"><button type="submit" class="primary" data-focus-key="plan-estimate-add">추가</button><button type="button" data-action="wf:graph:plan-estimate-close" data-focus-key="plan-estimate-close">취소</button></div></form>`;
}

function inspectionText(inspection: InspectionSummary | undefined): { text: string; fresh: boolean } {
  if (!inspection) return { text: "이 세션에서 자원을 확인하지 않았습니다", fresh: false };
  const age = (Date.now() - Date.parse(inspection.observedAt)) / 1000;
  if (!Number.isFinite(age) || age < 0 || age > 3600) return { text: "자원을 확인한 지 1시간이 지났습니다", fresh: false };
  const facts = inspection.facts;
  return { text: `${Math.max(0, Math.floor(age / 60))}분 전 확인 · ${facts.gpu_type} ×${facts.gpu_count} · ${usd(facts.total_hourly_cost_usd)}/시간`, fresh: true };
}

function resourcesHtml(draft: ProjectDraft, view: PlanView): string {
  const found = current(draft);
  const estimates = new Set(draft.planning.estimates.map(estimate => estimate.profile_id));
  const ids = [...new Set([...planned(readGraph(draft.spec.graph)).flatMap(profilesOf).filter(id => !estimates.has(id)),
    ...[...(found?.issues ?? []), ...(found?.warnings ?? [])].flatMap(issue => { const place = planSection(issue); return place.section === "profile" && place.key ? [place.key] : []; })])];
  const real = !draft.planning.simulation;
  const stale: string[] = [];
  const items = ids.map(id => {
    const native = view.profiles.some(profile => profile.profile_id === id);
    const inspection = native ? inspectionText(view.inspections[id]) : null;
    if (real && native && inspection && !inspection.fresh) stale.push(profileText(id, view.profiles));
    const state = !native ? statusPill("bad", "목록에 없는 실행 프로필") : inspection!.fresh ? statusPill("good", "자원 확인함") : statusPill(real ? "warn" : "neutral", "확인 필요");
    return `<li class="plan-profile" data-plan-profile="${escapeHtml(id)}"><div><b>${escapeHtml(profileName(id, view.profiles))}</b> <small class="mono">${escapeHtml(id)}</small> ${state}</div>
      ${inspection ? `<small>${escapeHtml(inspection.text)}</small>` : ""}
      ${issueBlock(found ? inSection(found.issues, "profile", id) : [])}${warningList(found ? inSection(found.warnings, "profile", id) : [])}</li>`;
  }).join("");
  const hint = stale.length ? `<p class="plan-hint">${INSPECT_HINT}: ${escapeHtml(stale.join(", "))} · 저장된 확인 기록은 계획이 판단합니다.</p><div class="actions">${button("실행 프로필 열기", "nav:profiles")}</div>` : "";
  return `<div class="plan-resources" role="group" aria-labelledby="plan-resources-title"><h4 id="plan-resources-title">실행 자원</h4>
    ${items ? `<ul class="plan-profile-list">${items}</ul>` : '<p class="muted">카드에서 실행 프로필을 고르면 여기에 나타납니다.</p>'}${hint}</div>`;
}

function trackingHtml(draft: ProjectDraft, view: PlanView): string {
  const found = current(draft);
  const uri = String(view.settings.MLFLOW_TRACKING_URI ?? "").trim();
  const text = draft.planning.simulation ? "모의 실행 계획은 MLflow에 기록하지 않습니다."
    : uri ? `기록 위치: <span class="plan-uri">${escapeHtml(uri)}</span> · 연결 설정의 저장된 주소` : "MLflow 서버 주소가 없습니다. 연결 설정에서 저장하세요.";
  return `<div class="plan-tracking" data-plan-tracking><h4>MLflow 기록</h4><p>${text}</p>${issueBlock(found ? inSection(found.issues, "tracking") : [])}${warningList(found ? inSection(found.warnings, "tracking") : [])}</div>`;
}

function otherChecksHtml(draft: ProjectDraft): string {
  const found = current(draft);
  if (!found) return "";
  const datasets = inSection(found.issues, "datasets");
  const graph = readGraph(draft.saved?.project.graph ?? {});
  // Card issues the editor cannot place on a card (no node ID and no node index of the saved graph).
  const loose = inSection(found.issues, "graph").filter(issue => !issue.node_id && !(/^\/graph\/nodes\/(\d+)/.exec(issue.path) && graph.nodes[Number(/^\/graph\/nodes\/(\d+)/.exec(issue.path)![1])]));
  return `${datasets.length ? `<div class="plan-datasets" data-plan-datasets><h4>데이터</h4>${issueList(datasets)}<div class="actions">${button("데이터 연결 확인", `nav:project:${draft.projectId}`)}</div></div>` : ""}
    ${loose.length ? `<div class="plan-graph-issues"><h4>그래프</h4>${issueList(loose)}</div>` : ""}`;
}

function actionsHtml(draft: ProjectDraft, view: PlanView, busy: boolean): string {
  const blockers = planBlockers(draft, view.settings);
  const found = current(draft);
  const general = found ? [...inSection(found.issues, "project"), ...inSection(found.issues, "other"), ...inSection(found.issues, "approval")] : [];
  const held = Boolean(draft.plan && view.usable);
  const note = inputsNote.get(draft.projectId)
    ?? (changedInputs.has(draft.projectId) ? "바뀐 계획 입력은 ‘계획 확인’ 때 이 PC에 저장합니다." : storedInputs.get(draft.projectId) ? "계획 입력을 이 PC에 저장해 두었습니다." : "계획 입력은 ‘계획 확인’ 때 이 PC에 저장합니다.");
  return `<div class="plan-blockers" role="status">${blockers.length ? `<ul>${blockers.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : ""}</div>
    ${general.length ? issueList(general, { heading: "계획 확인 결과" }) : ""}
    <div class="actions"><button type="button"${held ? "" : ' class="primary"'} data-action="wf:graph:plan-run" data-focus-key="plan-run"${busy || blockers.length ? " disabled" : ""}>계획 확인</button></div>
    <p class="muted plan-note">${escapeHtml(note)}</p>`;
}

function droppedReason(draft: ProjectDraft, view: PlanView): string | null {
  if (draft.plan && view.usable) return null;
  if (draft.plan) return draft.dirty ? PLAN_DROPPED.edit : draft.saved?.revision_sha256 !== draft.plan.basis.projectRevision ? PLAN_DROPPED.revision : CHANGED_WHILE_PLANNING;
  return draft.planDropped;
}

function issuesWithSteps(issues: WorkflowIssue[], summary: PlanSummary): string {
  if (!issues.length) return "";
  const label = (nodeId: string | null | undefined) => summary.nodes.find(node => node.node_id === nodeId)?.label || nodeId || "";
  return `<ul class="issue-list" aria-label="실행 등록 결과">${issues.map(issue => {
    const { text, original } = localize(issue);
    const step = issue.node_id ? `<b>${escapeHtml(label(issue.node_id))}</b> · ` : "";
    return `<li data-code="${escapeHtml(issue.code)}"><span>${step}${escapeHtml(text)}</span>${original ? `<details><summary>원문 보기</summary><span class="issue-original">${escapeHtml(original)}</span></details>` : ""}</li>`;
  }).join("")}</ul>`;
}

function summaryHtml(draft: ProjectDraft, view: PlanView): string {
  const held = draft.plan!;
  const summary = held.summary;
  const waiting = planWaiting(summary, savedGraph(draft));
  const found = current(draft);
  const warnings = found?.warnings.length ?? 0;
  const submitted = submittedRuns.get(summary.plan_sha256);
  const refused = submitFeedback.get(draft.projectId);
  const steps = (Array.isArray(summary.nodes) ? summary.nodes : []).map(node => {
    const places = (node.allowed ?? []).map(resource => profileText(resource.profile_id, view.profiles));
    return `<li data-plan-step="${escapeHtml(node.node_id)}"><b>${escapeHtml(node.label || node.node_id)}</b> · ${escapeHtml(kindLabel(node.kind))} · ${places.length ? escapeHtml(places.join(", ")) : "GPU를 쓰지 않음"} · 최대 ${escapeHtml(usd(node.max_estimated_cost_usd))}${waiting.has(node.node_id) ? ` <span class="plan-wait">${WAITING_TEXT}</span>` : ""}</li>`;
  }).join("");
  return `<section class="plan-summary" aria-labelledby="plan-summary-title"><div class="plan-summary-head"><h4 id="plan-summary-title" tabindex="-1">${summary.simulation ? "모의 실행 계획" : "실제 실행 계획"}</h4>
      ${statusPill(summary.simulation ? "neutral" : "warn", summary.simulation ? "GPU·데이터 전송 없음" : "승인 필요 · 실행·데이터 전송")}</div>
    <dl class="plan-totals">
      <div><dt>최대 추정 비용</dt><dd>${escapeHtml(usd(summary.max_estimated_cost_usd))}<small>${COST_NOTE}</small></dd></div>
      <div><dt>최대 소요 시간</dt><dd>${escapeHtml(durationText(summary.max_duration_seconds))}</dd></div>
      <div><dt>최대 동시 GPU</dt><dd>${escapeHtml(String(summary.max_concurrent_gpu_count ?? "—"))}개</dd></div>
      <div><dt>계획 해시</dt><dd class="mono">${escapeHtml(String(summary.plan_sha256).slice(0, 12))}…</dd></div></dl>
    <ul class="plan-steps">${steps}</ul>
    ${warnings ? `<p class="plan-hint">계획 경고 ${warnings}건 · 실행을 막지 않습니다. 실행 자원과 카드에 표시했습니다.</p>` : ""}
    ${refused?.plan === summary.plan_sha256 ? issuesWithSteps(refused.issues, summary) : ""}
    ${submitted ? `<p class="plan-submitted">이 계획으로 실행을 등록했습니다 · 실행 <span class="mono">${escapeHtml(submitted.slice(0, 12))}</span></p><div class="actions">${button("실행 보기", `nav:runs:${submitted}`)}</div>`
      : `<div class="actions"><button type="button" class="primary" data-action="wf:graph:plan-approve" data-focus-key="plan-approve">계획 승인·실행…</button></div>
        <p class="muted">${summary.simulation ? "대화상자에서 내용을 확인한 뒤 모의 실행으로 등록합니다." : "대화상자에서 실행과 데이터 전송을 각각 승인해야 등록합니다."} 등록한 뒤에도 컨트롤러는 자동으로 시작하지 않습니다.</p>`}</section>`;
}

function resultHtml(draft: ProjectDraft, view: PlanView, busy: boolean): string {
  if (draft.plan && view.usable) return summaryHtml(draft, view);
  const reason = droppedReason(draft, view);
  if (!reason) return "";
  const blocked = busy || planBlockers(draft, view.settings).length > 0;
  return `<div class="wf-banner warn plan-dropped" role="status"><div><p>${escapeHtml(DROPPED_PREFIX + reason)}</p><small>승인하지 않은 계획은 실행되지 않습니다. 초안은 그대로 남아 있습니다.</small></div>
    <div class="actions"><button type="button" data-action="wf:graph:plan-run" data-focus-key="plan-rerun"${blocked ? " disabled" : ""}>다시 계획</button></div></div>`;
}

/** The panel's inside (what the editor patches when the draft changes). */
export function planPanelBody(draft: ProjectDraft, view: PlanView, busy: boolean): string {
  return `<div class="section-title"><h3 id="plan-panel-title">계획과 승인</h3></div>
    <p class="muted plan-lead">계획은 저장한 버전으로 만듭니다. 모의 실행이 기본이며, 실제 실행은 승인 대화상자에서 실행과 데이터 전송을 따로 승인해야 등록됩니다.</p>
    <div class="plan-grid"><div class="plan-column">
      <form id="plan-inputs-form" class="plan-inputs" novalidate aria-label="계획 입력"><fieldset class="plan-fieldset"${busy ? " disabled" : ""}>${modeHtml(draft)}${limitsHtml(draft, view)}</fieldset></form>
      ${estimatesHtml(draft, busy)}${estimateFormHtml(draft, busy)}</div>
    <div class="plan-column">${resourcesHtml(draft, view)}${trackingHtml(draft, view)}${otherChecksHtml(draft)}</div></div>
    <div id="plan-actions" class="plan-actions">${actionsHtml(draft, view, busy)}</div>
    <div id="plan-result" class="plan-result">${resultHtml(draft, view, busy)}</div>`;
}

/** The plan panel below the graph editor. */
export function planPanelHtml(draft: ProjectDraft, view: PlanView, busy: boolean): string {
  return `<section id="plan-panel" class="card plan-panel" aria-labelledby="plan-panel-title">${planPanelBody(draft, view, busy)}</section>`;
}

/** Re-renders the panel's buttons, blockers and plan card (not the control being typed in). */
function refreshParts(ctx: ViewContext, draft: ProjectDraft, view: PlanView) {
  if (document.getElementById("plan-actions")) ctx.patch("plan-actions", actionsHtml(draft, view, ctx.busy), { keepTyping: false });
  if (document.getElementById("plan-result")) ctx.patch("plan-result", resultHtml(draft, view, ctx.busy), { keepTyping: false });
}

/** Wires the inputs form (time limits on `input`, the plan kind on `change`) and the estimate form. */
export function bindPlanPanel(ctx: ViewContext, projectId: string, view: () => PlanView, profiles: () => readonly PublicProfile[]): void {
  const form = document.querySelector<HTMLFormElement>("#plan-inputs-form");
  form?.addEventListener("submit", event => event.preventDefault());
  form?.addEventListener("input", event => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.dataset.planLimit === undefined || ctx.busy || target.matches(":disabled")) return;
    const valid = setLimit(ctx.store, projectId, target.dataset.planLimit, target.value);
    if (valid && !limitIssues(feedback.get(projectId), target.dataset.planLimit).length) target.removeAttribute("aria-invalid");
    else target.setAttribute("aria-invalid", "true");
    const draft = ctx.store.draft(projectId);
    if (draft) refreshParts(ctx, draft, view());
  });
  form?.addEventListener("change", event => {
    const target = event.target;
    if (!(target instanceof HTMLInputElement) || target.type !== "radio" || !target.checked || ctx.busy) return;
    setMode(ctx.store, projectId, target.value === "simulation");
    const key = focusKey(target);
    ctx.render();
    restoreFocus(document, key);
  });
  const estimate = document.querySelector<HTMLFormElement>("#plan-estimate-form");
  estimate?.addEventListener("input", () => captureEstimate());
  estimate?.addEventListener("submit", event => {
    event.preventDefault();
    if (!ctx.busy) addEstimate(ctx, projectId, profiles());
  });
}

/** Reads the estimate form into its draft (skipped while the form is locked by a running task). */
function captureEstimate() {
  const form = document.querySelector<HTMLFormElement>("#plan-estimate-form");
  if (!form || !estimateDraft || form.querySelector("fieldset:disabled")) return;
  const value = (key: string) => form.querySelector<HTMLInputElement>(`[data-estimate="${key}"]`)?.value ?? "";
  estimateDraft = { ...estimateDraft, name: value("name"), gpuType: value("gpu_type"), count: value("count"), price: value("price") };
}
/** The view's capture: the estimate form (time limits and the plan kind apply as they happen). */
export function capturePlanPanel(): void { captureEstimate(); }

function addEstimate(ctx: ViewContext, projectId: string, profiles: readonly PublicProfile[]) {
  captureEstimate();
  const draft = ctx.store.draft(projectId);
  if (!draft || !estimateDraft || estimateDraft.projectId !== projectId) return;
  const problems = estimateProblems(estimateDraft, draft, profiles);
  if (problems.length) {
    estimateDraft = { ...estimateDraft, problems };
    ctx.render();
    document.getElementById("plan-estimate-name")?.focus();
    return;
  }
  const estimate: PlanningEstimate = { profile_id: `estimate-${estimateDraft.name.trim()}`, gpu_type: estimateDraft.gpuType.trim(),
    gpu_count: Number(estimateDraft.count.trim()), total_hourly_cost_usd: Number(estimateDraft.price.trim()) };
  ctx.store.setPlanning(projectId, inputs => { inputs.estimates = [...inputs.estimates, estimate]; });
  changedInputs.add(projectId);
  estimateDraft = null;
  ctx.render();
  restoreFocus(document, `estimate:${estimate.profile_id}`);
}

/** The panel's verbs (`wf:graph:plan-*`). */
export async function planAction(ctx: ViewContext, projectId: string, verb: string, arg: string | null, env: PlanEnv): Promise<void> {
  if (verb === "plan-run") await runPlan(ctx, projectId, env);
  else if (verb === "plan-approve") await approveAndSubmit(ctx, projectId, env);
  else if (verb === "plan-estimate-new") {
    estimateDraft = { projectId, name: "", gpuType: "", count: "1", price: "", problems: [] };
    ctx.render();
    document.getElementById("plan-estimate-name")?.focus();
  } else if (verb === "plan-estimate-close") {
    estimateDraft = null;
    ctx.render();
    restoreFocus(document, "plan-estimate-new");
  } else if (verb === "plan-estimate-remove" && arg) {
    const draft = ctx.store.draft(projectId);
    if (!draft?.planning.estimates.some(estimate => estimate.profile_id === arg)) return;
    ctx.store.setPlanning(projectId, inputs => { inputs.estimates = inputs.estimates.filter(estimate => estimate.profile_id !== arg); });
    changedInputs.add(projectId);
    ctx.render();
    restoreFocus(document, estimateDraft?.projectId === projectId ? "plan-estimate-name" : "plan-estimate-new");
  }
}

const HEX64 = /^[0-9a-f]{64}$/;
function isSummary(value: unknown): value is PlanSummary {
  return isObject(value) && typeof value.plan_sha256 === "string" && HEX64.test(value.plan_sha256) && typeof value.simulation === "boolean"
    && typeof value.project_id === "string" && typeof value.project_revision === "string" && Array.isArray(value.nodes)
    && value.nodes.every(node => isObject(node) && typeof node.node_id === "string" && Array.isArray(node.allowed))
    && Array.isArray(value.datasets) && typeof value.max_estimated_cost_usd === "number";
}

/**
 * "계획 확인": saves changed planning inputs, then graph_plan of the saved revision. `ok` holds the plan with the
 * recipe revisions of the saved graph and the profile and settings generations from before the request (a change
 * meanwhile makes it unusable at once); its warnings never block. `invalid`/`conflict` show the issues by section,
 * with a blocked plan's warnings. A new request replaces the plan shown before.
 */
export async function runPlan(ctx: ViewContext, projectId: string, env: PlanEnv): Promise<void> {
  const draft = ctx.store.draft(projectId);
  if (!draft?.saved || ctx.busy || planBlockers(draft, ctx.settings).length) return;
  const graph = savedGraph(draft);
  const request = planRequest(draft, graph, env.recipes);
  const recipeRevisions = [...new Set(graph.nodes.flatMap(node => node.kind === "train" && typeof (node as Obj).recipe_revision === "string"
    ? [(node as Obj).recipe_revision as string] : []))];
  const since = ctx.store.generations();
  const still = stillOn(ctx);
  const outcome = { held: false };
  await ctx.task(request.simulation ? "모의 실행 계획 확인 중" : "실제 실행 계획 확인 중", async () => {
    draft.plan = null; draft.planDropped = null;                 // a new plan replaces the one shown (no approval left)
    feedback.delete(projectId); submitFeedback.delete(projectId);
    if (!await persistInputs(projectId, draft, graph, env)) { ctx.notify(INPUTS_REFUSED); return; }
    const result = await env.queue.run(() => planning.plan(request));
    const data: unknown = result.data;
    const warnings = isObject(data) && Array.isArray(data.warnings) ? data.warnings.filter(isIssue) : [];
    const base = { revision: request.project_revision, estimates: request.estimates.map(item => item.profile_id),
      nodes: request.node_resources.map(item => item.node_id) };
    if (result.status === "ok") {
      if (!isSummary(data)) throw new Error(MALFORMED);
      if (data.project_id !== projectId || data.project_revision !== request.project_revision || data.simulation !== request.simulation)
        throw new Error(MISMATCH);
      ctx.store.holdPlan(projectId, data, recipeRevisions, since);
      if (ctx.store.planUsable(projectId)) outcome.held = true;
      else ctx.store.dropPlan(projectId, CHANGED_WHILE_PLANNING);
      feedback.set(projectId, { ...base, issues: [], warnings });
      ctx.notify(`${data.simulation ? "모의" : "실제"} 실행 계획을 만들었습니다. 내용을 확인한 뒤 ‘계획 승인·실행…’을 누르세요.`);
      return;
    }
    feedback.set(projectId, { ...base, issues: result.issues, warnings });
    ctx.notify(PLAN_REFUSED);
  });
  const active = document.activeElement;
  if (outcome.held && still() && (!active || active === document.body || active.closest("#plan-panel")))
    document.getElementById("plan-summary-title")?.focus();
}

const STALE_PARTS: Record<string, string> = { project: "프로젝트", tracking: "MLflow 설정" };
/** What changed since the plan (D5), named by path: 프로젝트, each profile, MLflow 설정. */
function staleReason(issues: WorkflowIssue[], profiles: readonly PublicProfile[]): string {
  const parts = issues.map(issue => {
    const [first = "", ...rest] = issue.path.split("/").slice(1);
    if (first === "profiles" && rest.length) return `실행 프로필 ${profileText(rest.join("/").replace(/~1/g, "/").replace(/~0/g, "~"), profiles)}`;
    return STALE_PARTS[first] ?? "계획에 쓴 설정";
  });
  return `계획 이후 바뀐 것 · ${[...new Set(parts)].join(", ")}. 다시 계획하세요.`;
}
const isControllerView = (value: unknown): value is ControllerView => isObject(value) && typeof value.state === "string";
function registeredText(controller: ControllerView | null): string {
  if (controller?.state === "running") return "실행을 등록했습니다. 동작 중인 백그라운드 컨트롤러가 진행합니다.";
  const why = controller?.state === "stale" ? "백그라운드 컨트롤러의 연결이 끊겨" : "백그라운드 컨트롤러가 동작하지 않아";
  return `실행을 등록했습니다. ${why} 아직 진행되지 않습니다. 앱은 컨트롤러를 자동으로 시작하지 않으니 ‘컨트롤러 시작’을 눌러 시작하세요.`;
}

/**
 * "계획 승인·실행…": the approval dialog of the held, usable plan, then graph_submit with the plan's request ID (reused
 * after a bridge error or a refusal). A real plan is sent only with both approvals from the dialog; a dry run with
 * none. `ok` opens the run on the runs page (after the task; the controller is never started here). `plan_stale`
 * drops the plan and names what changed; other refusals are shown with the plan kept.
 */
export async function approveAndSubmit(ctx: ViewContext, projectId: string, env: PlanEnv): Promise<void> {
  const draft = ctx.store.draft(projectId);
  const held = draft?.plan;
  if (!draft?.saved || !held || ctx.busy || !ctx.store.planUsable(projectId) || submittedRuns.has(held.summary.plan_sha256)) return;
  const choice = await openApproval(held.summary, [...env.profiles], planWaiting(held.summary, savedGraph(draft)), draft.spec.name);
  if (!choice) return;
  // The approval covers the plan the dialog showed, only while its basis still holds.
  if (ctx.store.draft(projectId)?.plan !== held || !ctx.store.planUsable(projectId) || ctx.busy) {
    ctx.fail("계획이 바뀌어 등록하지 않았습니다. 다시 계획하세요.");
    return;
  }
  const simulation = held.summary.simulation === true;
  if (!simulation && !(choice.approveExecution && choice.approveDataTransfer)) return;
  const request = { plan_sha256: held.summary.plan_sha256, request_id: held.requestId,
    approve_execution: !simulation && choice.approveExecution, approve_data_transfer: !simulation && choice.approveDataTransfer };
  const still = stillOn(ctx);
  const outcome: { run?: { id: string; controller: ControllerView | null }; stale?: boolean } = {};
  await ctx.task(simulation ? "모의 실행 등록 중" : "실행 등록 중", async () => {
    submitFeedback.delete(projectId);
    const result = await env.queue.run(() => runs.submit(request));
    if (result.status === "ok") {
      const data: unknown = result.data;
      if (!isObject(data) || typeof data.run_id !== "string" || !data.run_id) throw new Error(MALFORMED);
      submittedRuns.set(held.summary.plan_sha256, data.run_id);
      outcome.run = { id: data.run_id, controller: isControllerView(data.controller) ? data.controller : null };
      return;
    }
    const stale = result.status === "conflict" ? result.issues.filter(issue => issue.code === "plan_stale") : [];
    if (stale.length) {
      ctx.store.dropPlan(projectId, staleReason(stale, env.profiles));
      outcome.stale = true;
      ctx.notify("계획 이후 바뀐 것이 있어 실행을 등록하지 않았습니다. 초안은 그대로입니다. ‘다시 계획’을 누르세요.");
      return;
    }
    submitFeedback.set(projectId, { plan: held.summary.plan_sha256, issues: result.issues });
    ctx.notify("실행을 등록하지 못했습니다. 계획 아래의 확인할 점을 보세요.");
  });
  if (outcome.stale) { focusIfLost(still, "plan-rerun"); return; }
  const run = outcome.run;
  if (!run) return;
  const message = registeredText(run.controller);
  // Never from inside the task: the runs page's enter waits for running tasks (view-types.ts).
  if (ctx.hasPage("runs") && still()) {
    await ctx.navigate("runs", run.id);
    ctx.notify(message);
    if (ctx.page === "runs" && ctx.arg === run.id) focusIfLost(() => true, "run-detail-title");
  } else ctx.notify(`${message} 실행 ID: ${run.id}`);
}
