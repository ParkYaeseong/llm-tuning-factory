// Graph editor (10.5, D4–D7, D13): the open project's training graph as cards in topological columns plus an SVG
// preview, both from the same draft (`graph-model.layout`). Click and keyboard only: a card is a button (roving
// tabindex, ArrowUp/ArrowDown move, Enter selects); "다음 단계 추가" adds a serial child of the selected card and
// "나란히 추가" a parallel sibling; inputs are connected with selects whose unusable sources are disabled and listed with
// their reasons. Edits change the store's draft as they happen; saving is explicit and validates the saved revision.
// The base-model panel and the inline benchmark form are components. Every dynamic text is escaped.
import "./graph-editor.css";
import { executorProfiles, planning, recipes as recipeApi, workflow } from "../api";
import { benchmarkFormHtml, benchmarkProblems, benchmarkSpec, EVALUATOR_LABELS, newBenchmarkDraft, readBenchmarkForm,
  type BenchmarkDraft, type RowCounts } from "../components/benchmark-form";
import { captureRootsForm, findRoot, inspectModel, inspectTargets, loadRoots, modelRootsPanel, pickModel, rootsState,
  searchModels, snapshotRoot } from "../components/model-roots";
import { applyPlanningInputs, bindPlanPanel, capturePlanPanel, planAction, planFeedback, planPanelBody, planPanelHtml,
  readPlanningInputs, type PlanEnv, type PlanView } from "../components/plan-panel";
import { addNode, addParallel, addSerial, connect, findNode, graphEdgeCount, graphToJson, initializationFor, inputOptions, isRef,
  KIND_LABELS, labelOf, layout, localIssues, MAX_EDGES, MAX_NODES, metricChoices, NOTES, outputRef, percentText, previewSvg, readGraph,
  REASONS, removeNode, rootInput, sameGraph, sameRoot, selectionEvaluators, serialKinds, starter, upstreamRoot, waitingOnSharedProfile,
  weightFromPercent, weightsValid, type DraftContext, type GraphJson, type InputPort, type NodeJson, type NodeKind,
  type StarterShape } from "../graph-model";
import { issueList, issuesAt, localize, nodeIssues } from "../issues";
import { createQueue } from "../poller";
import type { ProjectDraft } from "../project-store";
import type { View, ViewContext } from "../view-types";
import { button, errorText, escapeHtml, focusKey, openDialog, restoreFocus, statusPill } from "../workbench";
import { sessionInspections } from "./executor-profiles";
import type { BenchmarkRevision, HFRootRef, PublicProfile, RecipeRevision, RecipeSummary, Sha256, WorkflowIssue,
  WorkflowResult } from "../workflow-types";

type Obj = Record<string, any>;
const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const TARGETS_HINT = "기반 모델의 모듈 구조를 아직 확인하지 않았습니다 · lora_target: all(기본값)이나 Full·Freeze 학습은 모의 실행이 이 단계에서 멈춥니다.";
const TARGETS_SHORT = "기반 모델의 모듈 구조를 아직 확인하지 않았습니다.";
const MERGE_SHORT = "실제로 완료된 결과만 병합 · 모의 실행은 이 단계에서 멈춥니다.";
const SNAPSHOT_HINT = "이 기반 모델의 설정·토크나이저를 아직 받지 않았습니다 · ‘기반 모델’에서 먼저 받으세요.";
const TARGET_CODES = new Set(["base_targets_missing", "simulated_execution_needs_targets"]);
const STARTERS: [StarterShape, string, string][] = [["single", "단일 학습", "학습 단계 하나"], ["serial", "직렬 추가 학습", "학습 결과로 이어서 학습"],
  ["parallel", "병렬 비교", "학습 두 개를 같은 평가 기준으로 비교"], ["parallel_merge", "병렬 비교 후 병합", "두 학습, 병합과 세 평가 중 자동 선택"]];
const METHOD_LABELS: Record<string, string> = { lora: "LoRA", qlora: "QLoRA", full: "Full", freeze: "Freeze", oft: "OFT" };
const INITIALIZATION_LABELS: Record<string, string> = { new_adapter: "새 어댑터 (new_adapter)", full_weights: "전체 가중치 (full_weights)",
  continue_adapter: "어댑터 이어 학습 (continue_adapter)" };
const ROLE_LABELS: Record<string, string> = { train: "학습", validation: "검증", test: "시험" };
/** The short name of a missing field, for the cards' "채울 항목". */
const TODO_NAMES: Record<string, string> = { [REASONS.label]: "이름", [REASONS.recipe]: "레시피", [REASONS.modelInput]: "입력 모델",
  [REASONS.dataset]: "학습 데이터", [REASONS.benchmark]: "평가 기준", [REASONS.evaluations]: "비교할 평가", [REASONS.metric]: "선택 지표",
  [REASONS.profiles]: "실행 프로필" };

// -- module state (the project drafts themselves live in ctx.store) --
const selection = new Map<string, string>();             // project → selected card
let addMenu = false;                                     // the "다음 단계 추가" kind choice is open
let toolbarNote: string | null = null;
let benchmarkFor: { projectId: string; nodeId: string } | null = null;
let benchmarkDraft: BenchmarkDraft | null = null;
let benchmarkIssues: WorkflowIssue[] = [];
let benchmarkTried = false;
let recipeList: RecipeSummary[] | null = null;
let recipeError: string | null = null;
const pinnedRecipes = new Map<Sha256, RecipeSummary | null>();   // older revisions a card pins (recipe_get by hash)
let benchmarkList: BenchmarkRevision[] | null = null;
let benchmarkError: string | null = null;
let profileList: PublicProfile[] | null = null;
let profileError: string | null = null;
const loadIssues = new Map<string, WorkflowIssue[]>();
const validated = new Map<string, Sha256>();              // project → the saved revision graph_validate accepted
let unavailable = false;
let weightTextInvalid = false;                            // a merge weight input holds text that is not a percent
const queue = createQueue();                              // one sidecar call at a time on this page (D10)

const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => {
  const { text, original } = localize(issue);
  return original ? `${text} (${original})` : text;
}).join("\n") || "요청을 처리하지 못했습니다.";
const short = (hash: unknown) => escapeHtml(String(hash ?? "").slice(0, 8));
const isObject = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
/** Whether the user is still on the page an action started from (its follow-up may only then move the focus). */
const stillOn = (ctx: ViewContext) => { const page = ctx.page, arg = ctx.arg; return () => ctx.page === page && ctx.arg === arg; };
/** Puts the focus on `key` only when a render lost it (to body) and only on the same page: never across pages. */
function refocus(still: () => boolean, key: string) {
  if (still() && (!document.activeElement || document.activeElement === document.body)) restoreFocus(document, key);
}
const focusLabel = () => document.getElementById("node-label")?.focus({ preventScroll: false });
const locked = (form: Element) => form.querySelector("fieldset:disabled") !== null;
/** Issue lists inside the card list keep the list one tab stop: their "원문 보기" is not in the tab order (the card form
 * repeats the same issues with focusable details). */
const quiet = (html: string) => html.replace(/<summary>/g, '<summary tabindex="-1">');
const summaryOf = (revision: RecipeRevision): RecipeSummary => ({ recipe_id: revision.recipe.recipe_id, version: revision.recipe.version,
  name: revision.recipe.name, description: revision.recipe.description, training_stage: revision.recipe.training_stage,
  training_method: revision.recipe.training_method, input_kind: revision.recipe.input_kind, revision_sha256: revision.revision_sha256 });
const recipeText = (recipe: RecipeSummary) =>
  `${recipe.name} · v${recipe.version} · ${String(recipe.training_stage).toUpperCase()} · ${METHOD_LABELS[recipe.training_method] ?? recipe.training_method}`;
const isRootInput = (value: unknown): value is { policy: { base: HFRootRef; adapter: null }; initialization: string } =>
  isObject(value) && isObject(value.policy) && isObject(value.policy.base) && typeof value.policy.base.repo_id === "string" && value.policy.adapter == null;

function draftOf(ctx: ViewContext): ProjectDraft | undefined {
  return ctx.page === "graph" && ctx.arg ? ctx.store.draft(ctx.arg) : undefined;
}
function draftContext(draft: ProjectDraft): DraftContext {
  const recipes = new Map<Sha256, RecipeSummary>();
  for (const recipe of recipeList ?? []) recipes.set(recipe.revision_sha256, recipe);
  for (const [revision, recipe] of pinnedRecipes) if (recipe && !recipes.has(revision)) recipes.set(revision, recipe);
  return { recipes, bindings: draft.spec.datasets, roots: rootsState.roots,
    benchmarks: new Map((benchmarkList ?? []).map(item => [item.revision_sha256, item])),
    defaultProfiles: draft.spec.default_executor_profile_id ? [draft.spec.default_executor_profile_id] : [],
    // A new card never takes a saved card's ID (its check results) or one with planning inputs (its time limit).
    reservedIds: [...new Set([...savedIds(draft), ...Object.keys(draft.planning.nodeResources)])] };
}
const graphOf = (draft: ProjectDraft) => readGraph(draft.spec.graph);
const savedIds = (draft: ProjectDraft) => readGraph(draft.saved?.project.graph ?? {}).nodes.map(node => String(node.id));
function selectedIn(projectId: string, graph: GraphJson): string | null {
  const id = selection.get(projectId);
  return id && findNode(graph, id) ? id : null;
}
/** Applies a graph change to the project's draft (dirty; a held plan is dropped by the store). A change that leaves the
 * graph as it is never reaches the store, so it drops no plan. */
function editGraph(ctx: ViewContext, projectId: string, change: (graph: GraphJson) => GraphJson) {
  const draft = ctx.store.draft(projectId);
  if (!draft) return;
  const changed = change(readGraph(draft.spec.graph));
  // Canonically: the backend returns the graph with sorted keys, and an edit may rebuild an object in another order.
  if (sameGraph(changed, readGraph(draft.spec.graph))) return;
  const next = graphToJson(changed);
  ctx.store.edit(projectId, spec => { spec.graph = next; });
}
/** Applies a change to one card of the draft. */
function editNode(ctx: ViewContext, projectId: string, nodeId: string, change: (node: Obj, graph: GraphJson) => void) {
  editGraph(ctx, projectId, graph => {
    const node = findNode(graph, nodeId) as Obj | undefined;
    if (node) change(node, graph);
    return graph;
  });
}
function rowCounts(ctx: ViewContext, draft: ProjectDraft): RowCounts {
  const records: Obj[] = ctx.catalog.datasets ?? [];
  return new Map(draft.spec.datasets.map(binding => {
    const record = records.find(item => item.dataset_id === binding.dataset.dataset_id && String(item.version) === binding.dataset.version);
    return [binding.binding_id, typeof record?.rows === "number" ? record.rows : null];
  }));
}

// -- what one render shows --
/** `issues`/`warnings`: the validation's and the store's, plus the plan panel's for cards and fields (10.6). */
interface Shown { projectId: string; draft: ProjectDraft; graph: GraphJson; dctx: DraftContext; plan: ReturnType<typeof layout>;
  local: WorkflowIssue[]; waiting: Set<string>; selected: string | null; saved: string[]; issues: WorkflowIssue[]; warnings: WorkflowIssue[] }
function shown(ctx: ViewContext): Shown | null {
  const draft = draftOf(ctx);
  if (!draft || !ctx.arg) return null;
  const graph = graphOf(draft);
  const dctx = draftContext(draft);
  const planned = planFeedback(draft);
  return { projectId: ctx.arg, draft, graph, dctx, plan: layout(graph), local: localIssues(graph, dctx), waiting: waitingOnSharedProfile(graph),
    selected: selectedIn(ctx.arg, graph), saved: savedIds(draft), issues: [...draft.issues, ...planned.issues],
    warnings: [...draft.warnings, ...planned.warnings] };
}
const cardIssues = (view: Shown, id: string) => nodeIssues(view.issues, id, view.saved);
const cardWarnings = (view: Shown, id: string) => nodeIssues(view.warnings, id, view.saved);
/** What the plan panel shows besides the draft (10.6). */
function planView(ctx: ViewContext, draft: ProjectDraft): PlanView {
  return { profiles: profileList ?? [], inspections: sessionInspections(), settings: ctx.settings, recipes: draftContext(draft).recipes,
    usable: ctx.store.planUsable(draft.projectId) };
}
const planEnv = (draft: ProjectDraft): PlanEnv => ({ recipes: draftContext(draft).recipes, profiles: profileList ?? [], queue });

/** The module-structure hint of a card: its train input is a root without evidence, or a check asked for evidence. */
function targetsHint(view: Shown, node: NodeJson): { text: string; short: string; root: HFRootRef | null } | null {
  const flagged = [...cardIssues(view, node.id), ...cardWarnings(view, node.id)].some(issue => TARGET_CODES.has(issue.code));
  const root = upstreamRoot(view.graph, node.id);
  const entry = findRoot(root);
  const inspectable = entry?.snapshot_ready && !entry.targets_ready ? root : null;
  if (flagged) return { text: "이 단계에는 기반 모델의 모듈 구조 정보가 필요합니다.", short: "모듈 구조 정보가 필요합니다.", root: inspectable };
  if (node.kind !== "train" || !isRootInput((node as Obj).model_input)) return null;
  if (!rootsState.loaded || rootsState.unavailable) return null;
  if (!entry) return { text: SNAPSHOT_HINT, short: SNAPSHOT_HINT, root: null };
  return entry.snapshot_ready && !entry.targets_ready ? { text: TARGETS_HINT, short: TARGETS_SHORT, root } : null;
}

// -- rendering --
function headHtml(ctx: ViewContext, view: Shown): string {
  const { draft, graph } = view;
  const revision = draft.saved?.revision_sha256;
  const state = draft.dirty ? statusPill("warn", "저장 안 된 변경")
    : revision && validated.get(view.projectId) === revision ? statusPill("good", "저장한 버전 검사 통과")
    : revision ? statusPill("neutral", `저장된 버전 ${String(revision).slice(0, 8)}`) : "";
  return `<div class="section-title"><h3>${escapeHtml(draft.spec.name)}</h3></div>${state}
    <p class="muted">단계 ${graph.nodes.length}개 · 최대 ${MAX_NODES}개${revision ? ` · 저장된 버전 <span class="mono">${short(revision)}</span>` : ""}</p>
    <div class="actions"><button type="button" class="primary" data-action="wf:graph:save" data-focus-key="graph-save"${unavailable ? " disabled" : ""}>저장</button>${button("프로젝트로 돌아가기", `nav:project:${view.projectId}`, "quiet")}</div>
    <p class="muted">${unavailable ? NOT_AVAILABLE : "저장하면 저장한 버전을 바로 검사합니다. 계획과 실행은 저장한 버전으로만 합니다."}</p>`;
}

function settingsHtml(ctx: ViewContext, view: Shown): string {
  const graph = view.graph;
  const value = (number: unknown) => typeof number === "number" && Number.isFinite(number) ? String(number) : "";
  const input = (id: string, key: string, label: string, current: unknown, attributes: string, placeholder: string) =>
    `<label class="field"><span>${label}</span><input id="${id}" type="number" data-setting="${key}" aria-label="${label}" ${attributes} value="${escapeHtml(value(current))}" placeholder="${placeholder}"></label>`;
  return `<form id="graph-settings-form" class="card graph-settings" novalidate aria-labelledby="graph-settings-title">
    <h3 id="graph-settings-title">실행 설정</h3><fieldset class="graph-fieldset"${ctx.busy ? " disabled" : ""}><div class="graph-settings-grid">
    ${input("graph-concurrent", "max_concurrent", "최대 동시 실행", graph.max_concurrent, 'min="1" max="64" step="1"', "기본 1")}
    ${input("graph-cost", "max_total_cost_usd", "최대 총비용 (USD)", graph.max_total_cost_usd, 'min="0" step="any"', "기본 100")}
    ${input("graph-attempts", "retry_policy.max_attempts", "재시도 횟수", graph.retry_policy?.max_attempts, 'min="1" max="5" step="1"', "기본 1")}
    ${input("graph-backoff", "retry_policy.backoff_seconds", "재시도 간격 (초)", graph.retry_policy?.backoff_seconds, 'min="0" max="3600" step="1"', "기본 0")}
    </div></fieldset><div id="graph-settings-note" class="graph-settings-note" role="status">${settingsNoteHtml(view)}</div></form>`;
}
function settingsNoteHtml(view: Shown): string {
  const problems = view.local.filter(issue => issue.code === "local_settings");
  return problems.length ? `<ul class="graph-reasons">${problems.map(issue => `<li>${escapeHtml(issue.message)}</li>`).join("")}</ul>`
    : '<small class="muted">최대 총비용은 재시도를 포함한 그래프 전체의 계획 상한입니다.</small>';
}

function startersHtml(ctx: ViewContext): string {
  return `<div class="graph-starters" role="group" aria-label="시작 모양"><p class="muted">아직 단계가 없습니다. 시작 모양을 고르면 카드가 만들어지고, 그다음 카드마다 레시피·데이터·실행 프로필을 고칠 수 있습니다.</p>
    <div class="graph-starter-list">${STARTERS.map(([shape, label, help]) => `<div class="graph-starter"><button type="button" data-action="wf:graph:start:${shape}"${ctx.busy ? " disabled" : ""}>${label}</button><small>${help}</small></div>`).join("")}</div></div>`;
}

function cardsHtml(ctx: ViewContext, view: Shown): string {
  const { graph, plan, local, waiting, selected } = view;
  if (!graph.nodes.length) return startersHtml(ctx);
  const order = plan.columns.flat();
  const tabStop = selected && order.includes(selected) ? selected : order[0];
  const card = (id: string) => {
    const node = findNode(graph, id)!;
    const mine = local.filter(issue => issue.node_id === id);
    const todo = [...new Set(mine.filter(issue => issue.code === "local_required").map(issue => TODO_NAMES[issue.message] ?? issue.message))];
    const reasons = [...new Set(mine.filter(issue => issue.code !== "local_required").map(issue => issue.message))];
    const checked = cardIssues(view, id), warned = cardWarnings(view, id);
    const hint = targetsHint(view, node);
    const status = [
      waiting.has(id) ? `<p class="graph-card-wait">${escapeHtml(NOTES.waiting)}</p>` : "",
      reasons.length ? `<ul class="graph-reasons">${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul>` : "",
      todo.length ? `<p class="graph-card-todo">채울 항목: ${escapeHtml(todo.join(", "))}</p>` : "",
      checked.length ? quiet(issueList(checked)) : "",
      warned.length ? quiet(issueList(warned, { heading: "계획 경고" })) : "",
      hint ? `<p class="graph-card-hint">${escapeHtml(hint.short)}${hint.root ? ` <button type="button" class="quiet" tabindex="-1" data-action="wf:graph:card-targets:${escapeHtml(id)}">모듈 구조 확인</button>` : ""}</p>` : "",
      node.kind === "merge" ? `<p class="graph-card-todo">${MERGE_SHORT}</p>` : "",
    ].join("");
    const flagged = reasons.length || checked.length;
    return `<div class="graph-card${id === selected ? " selected" : ""}${flagged ? " flagged" : ""}${waiting.has(id) ? " waiting" : ""}" data-card="${escapeHtml(id)}" role="listitem">
      <button type="button" class="graph-card-button" data-node-card="${escapeHtml(id)}" data-action="wf:graph:select:${escapeHtml(id)}" data-focus-key="card:${escapeHtml(id)}"
        tabindex="${id === tabStop ? "0" : "-1"}" aria-pressed="${id === selected}"${status ? ` aria-describedby="card-status-${escapeHtml(id)}"` : ""}>
        <span class="graph-card-label">${escapeHtml(labelOf(node))}</span><span class="graph-card-meta">${escapeHtml(KIND_LABELS[node.kind] ?? "알 수 없는 단계")} · ${escapeHtml(id)}</span></button>
      ${status ? `<div class="graph-card-status" id="card-status-${escapeHtml(id)}">${status}</div>` : ""}</div>`;
  };
  return `<div class="graph-columns" role="group" aria-label="학습 단계 카드 · 위아래 화살표로 이동, Enter로 선택">${plan.columns.map((column, index) =>
    `<div class="graph-column" role="list" aria-label="${index + 1}번째 열">${column.map(card).join("")}</div>`).join("")}</div>`;
}

function toolbarHtml(ctx: ViewContext, view: Shown): string {
  const { graph, selected, dctx } = view;
  if (!graph.nodes.length) return "";
  const full = graph.nodes.length >= MAX_NODES;
  const off = (flag: boolean) => flag ? " disabled" : "";
  const menu = addMenu && selected && !full ? `<div id="graph-add-kinds" class="graph-kinds" role="group" aria-label="추가할 단계 종류">
      ${serialKinds(graph, selected, dctx).map(choice => `<span class="graph-kind"><button type="button" data-action="wf:graph:add:${choice.kind}"${off(!choice.allowed)}>${escapeHtml(choice.label)}</button>${choice.reason ? `<small>${escapeHtml(choice.reason)}</small>` : ""}</span>`).join("")}
      <button type="button" class="quiet" data-action="wf:graph:add-menu-close">닫기</button></div>` : "";
  const note = (full ? `<p class="graph-limit">${REASONS.limit} · 단계를 삭제한 뒤 추가하세요.</p>`
    : !selected ? '<p class="muted">카드를 고르면 그 카드 다음 단계나 나란히 놓을 카드를 추가할 수 있습니다.</p>' : "")
    + (graphEdgeCount(graph) >= MAX_EDGES ? `<p class="graph-limit">${REASONS.edgeLimit} · 연결을 지운 뒤 추가하세요.</p>` : "");
  return `<div class="graph-toolbar"><button type="button" data-action="wf:graph:add-menu" aria-expanded="${Boolean(menu)}" aria-controls="graph-add-kinds"${off(!selected || full)}>다음 단계 추가</button>
    <button type="button" data-action="wf:graph:add-parallel"${off(!selected || full)}>나란히 추가</button>
    <button type="button" data-action="wf:graph:add-root"${off(full)}>새 학습 단계</button>
    <button type="button" data-action="wf:graph:remove"${off(!selected)}>단계 삭제</button></div>${menu}${note}
    <p class="muted graph-toolbar-note" role="status">${toolbarNote ? escapeHtml(toolbarNote) : ""}</p>`;
}

function previewHtml(view: Shown): string {
  if (!view.graph.nodes.length) return '<p class="muted">카드를 추가하면 같은 초안으로 그린 미리보기가 여기에 나타납니다.</p>';
  const flagged = new Set([...view.local.filter(issue => issue.code !== "local_required" && issue.node_id).map(issue => issue.node_id!),
    ...view.graph.nodes.filter(node => cardIssues(view, node.id).length).map(node => node.id)]);
  return `${previewSvg(view.graph, view.plan, { selected: view.selected ?? undefined, waiting: view.waiting, issues: flagged })}
    <p class="graph-preview-caption">카드와 같은 초안으로 그립니다 · 점선은 실행 순서만 정한 연결입니다 · 테두리 점선은 같은 실행 자원을 기다리는 단계입니다.</p>`;
}

/** Validation issues no card shows (graph settings, data bindings, the graph as a whole). */
function summaryHtml(view: Shown): string {
  const unplaced = view.draft.issues.filter(issue => !view.graph.nodes.some(node => cardIssues(view, node.id).includes(issue)));
  if (!unplaced.length) return "";
  const datasets = unplaced.some(issue => issue.path === "/datasets" || issue.path.startsWith("/datasets/"));
  return `<div class="graph-summary" role="status">${issueList(unplaced, { heading: "저장한 그래프 검사 결과" })}
    ${datasets ? `<div class="actions">${button("프로젝트에서 데이터 연결 확인", `nav:project:${view.projectId}`)}</div>` : ""}</div>`;
}

function conflictHtml(draft: ProjectDraft): string {
  if (!draft.conflict) return "";
  const revision = draft.conflict.find(issue => issue.code === "revision_conflict");
  return `<div class="wf-banner warn graph-banner" role="status"><div>${revision ? escapeHtml(localize(revision).text) : issueList(draft.conflict)}
    <small>내 초안은 그대로 남아 있습니다.</small></div>${revision ? `<div class="actions">${button("최신 버전 불러오기", "wf:graph:reload")}${button("내 변경으로 덮어쓰기", "wf:graph:overwrite")}</div>` : ""}</div>`;
}

// -- the card form --
interface Choice { value: string; label: string; disabled?: boolean }
function selectHtml(id: string, label: string, field: string, choices: Choice[], current: string, issues: WorkflowIssue[], help = "") {
  const invalid = issues.length ? ` aria-invalid="true" aria-describedby="${id}-issues"` : "";
  return `<div class="graph-field"><label class="field"><span>${label}</span><select id="${id}" data-node-field="${field}" aria-label="${label}"${invalid}>${choices.map(choice =>
    `<option value="${escapeHtml(choice.value)}"${choice.value === current ? " selected" : ""}${choice.disabled ? " disabled" : ""}>${escapeHtml(choice.label)}</option>`).join("")}</select></label>
    ${help}${issues.length ? `<div id="${id}-issues">${issueList(issues)}</div>` : ""}</div>`;
}
function numberHtml(id: string, label: string, field: string, value: unknown, attributes: string, issues: WorkflowIssue[], help = "") {
  const invalid = issues.length ? ` aria-invalid="true" aria-describedby="${id}-issues"` : "";
  const shownValue = typeof value === "number" && Number.isFinite(value) ? String(value) : "";
  return `<div class="graph-field"><label class="field"><span>${label}</span><input id="${id}" type="number" data-node-field="${field}" aria-label="${label}" ${attributes} value="${escapeHtml(shownValue)}"${invalid}></label>
    ${help}${issues.length ? `<div id="${id}-issues">${issueList(issues)}</div>` : ""}</div>`;
}
/** The node sources of one port as select choices: disabled with the reason when they cannot be used. */
function sourceChoices(view: Shown, nodeId: string, port: InputPort): Choice[] {
  return inputOptions(view.graph, nodeId, port, view.dctx).map(option => ({ value: `node:${option.value.node_id}`,
    label: option.allowed ? option.label : `${option.label} — ${option.reason}`, disabled: !option.allowed }));
}
function blockedHtml(view: Shown, nodeId: string, port: InputPort, onlyEvaluations = false): string {
  const blocked = inputOptions(view.graph, nodeId, port, view.dctx).filter(option => !option.allowed
    && (!onlyEvaluations || findNode(view.graph, option.value.node_id)?.kind === "evaluate"));
  if (!blocked.length) return "";
  return `<div class="graph-blocked"><p class="graph-blocked-title">연결할 수 없는 단계</p><ul>${blocked.map(option =>
    `<li>${escapeHtml(labelOf(findNode(view.graph, option.value.node_id)))} · ${escapeHtml(option.reason)}</li>`).join("")}</ul></div>`;
}
const refValue = (value: unknown) => isRef(value) ? `node:${value.node_id}` : value === undefined || value === null ? "" : "keep";

function profilesHtml(view: Shown, node: Obj, issues: WorkflowIssue[]): string {
  const chosen: string[] = Array.isArray(node.executor_profile_ids) ? node.executor_profile_ids.map(String) : [];
  const items = [...(profileList ?? []).map(profile => ({ id: profile.profile_id, label: `${profile.fields?.name || profile.profile_id} · ${profile.profile_id}` })),
    ...view.draft.planning.estimates.map(estimate => ({ id: estimate.profile_id, label: `계획용 추정 · ${estimate.gpu_type} × ${estimate.gpu_count} · ${estimate.profile_id}` }))];
  for (const id of chosen) if (!items.some(item => item.id === id)) items.push({ id, label: `${id} · 목록에 없음` });
  const boxes = items.map((item, index) => `<label class="check"><input type="checkbox" id="node-profile-${index}" data-focus-key="profile:${escapeHtml(item.id)}" data-node-profile="${escapeHtml(item.id)}"${chosen.includes(item.id) ? " checked" : ""}>${escapeHtml(item.label)}</label>`).join("");
  return `<fieldset class="graph-choices"${issues.length ? ' aria-invalid="true"' : ""}><legend>실행 프로필</legend>${boxes || '<p class="muted">저장한 실행 프로필이 없습니다. 설정 › 실행 프로필에서 만드세요.</p>'}
    ${profileError ? `<p class="error-text">실행 프로필 목록을 불러오지 못했습니다. ${escapeHtml(profileError)}</p>` : ""}
    <small class="muted">여러 개를 고르면 비어 있는 곳에서 실행합니다. 하나만 고른 단계끼리는 같은 자원을 기다립니다.</small>${issueList(issues)}</fieldset>`;
}

function trainFields(view: Shown, node: Obj, at: (field: string) => WorkflowIssue[]): string {
  const recipes = recipeList ?? [];
  const current: string = typeof node.recipe_revision === "string" ? node.recipe_revision : "";
  const pinned = current && !recipes.some(recipe => recipe.revision_sha256 === current) ? pinnedRecipes.get(current) : undefined;
  const choices: Choice[] = [{ value: "", label: "레시피 선택" }, ...recipes.map(recipe => ({ value: recipe.revision_sha256, label: recipeText(recipe) }))];
  let recipeHelp = "";
  if (current && !recipes.some(recipe => recipe.revision_sha256 === current)) {
    choices.push({ value: current, label: `고정된 이전 버전 · ${current.slice(0, 8)}${pinned ? ` · ${recipeText(pinned)}` : " · 레시피 정보를 찾을 수 없음"}` });
    const newer = pinned ? recipes.find(recipe => recipe.recipe_id === pinned.recipe_id) : undefined;
    recipeHelp = `<small class="muted">이 카드는 레시피의 이전 버전을 고정해 씁니다.</small>${newer ? `<div class="actions">${button("현재 버전으로 바꾸기", "wf:graph:recipe-current")}</div>` : ""}`;
  }
  if (recipeError) recipeHelp += `<small class="error-text">레시피 목록을 불러오지 못했습니다. ${escapeHtml(recipeError)}</small>`;
  const recipe = view.dctx.recipes.get(current);
  if (recipe) recipeHelp = `<small class="muted">${escapeHtml(String(recipe.training_stage).toUpperCase())} · ${escapeHtml(METHOD_LABELS[recipe.training_method] ?? recipe.training_method)} · 입력 ${escapeHtml(recipe.input_kind)}</small>${recipeHelp}`;
  const roots = rootsState.roots;
  const input = node.model_input;
  const inputChoices: Choice[] = [{ value: "", label: "입력 모델 선택" }, ...roots.map((entry, index) => ({ value: `root:${index}`,
    label: `기반 모델 · ${entry.root.repo_id} · ${entry.root.revision.slice(0, 8)}${entry.snapshot_ready ? "" : " · 설정·토크나이저 없음"}` })),
    ...sourceChoices(view, node.id, "model_input")];
  let inputValue = refValue(input);
  if (isRootInput(input)) {
    const index = roots.findIndex(entry => sameRoot(entry.root, input.policy.base));
    if (index >= 0) inputValue = `root:${index}`;
    else { inputValue = "keep"; inputChoices.push({ value: "keep", label: `기반 모델 · ${input.policy.base.repo_id} · ${String(input.policy.base.revision).slice(0, 8)} · 목록에 없음` }); }
  } else if (inputValue === "keep") inputChoices.push({ value: "keep", label: "이전 실행의 산출물 · 지원하지 않는 입력" });
  const inputHelp = isRootInput(input) ? `<small class="muted">초기화: ${escapeHtml(INITIALIZATION_LABELS[input.initialization] ?? input.initialization)} · 레시피의 학습 방법에서 정합니다.</small>`
    : isRef(input) ? '<small class="muted">이전 단계의 결과 모델에서 이어 학습합니다.</small>' : "";
  const bindings = view.draft.spec.datasets;
  const dataChoices: Choice[] = [{ value: "", label: "학습 데이터 선택" }, ...bindings.map(binding => ({ value: binding.binding_id,
    label: `${binding.binding_id} · ${ROLE_LABELS[binding.role] ?? binding.role} · ${binding.dataset.dataset_id} v${binding.dataset.version}`, disabled: binding.role !== "train" }))];
  if (node.dataset_binding && !bindings.some(binding => binding.binding_id === node.dataset_binding))
    dataChoices.push({ value: String(node.dataset_binding), label: `${node.dataset_binding} · 프로젝트에 없음` });
  const dataHelp = `<small class="muted">${bindings.some(binding => binding.role !== "train") ? REASONS.trainRole : "데이터는 프로젝트 화면에서 연결합니다."}</small>`;
  let reward = "";
  if (recipe?.training_stage === "ppo" || node.reward_binding != null) {
    const rewardChoices: Choice[] = [{ value: "", label: "보상 모델 선택" }, ...roots.map((entry, index) => ({ value: `root:${index}`,
      label: `기반 모델 · ${entry.root.repo_id} · ${entry.root.revision.slice(0, 8)}` })), ...sourceChoices(view, node.id, "reward_binding")];
    let rewardValue = refValue(node.reward_binding);
    if (isObject(node.reward_binding) && !isRef(node.reward_binding)) {
      const base = node.reward_binding.base;
      const index = node.reward_binding.adapter == null && isObject(base) ? roots.findIndex(entry => sameRoot(entry.root, base as HFRootRef)) : -1;
      if (index >= 0) rewardValue = `root:${index}`;
      else { rewardValue = "keep"; rewardChoices.push({ value: "keep", label: "저장된 보상 모델 · 목록에 없음" }); }
    }
    reward = selectHtml("node-reward", "보상 모델", "reward_binding", rewardChoices, rewardValue, at("reward_binding"),
      `<small class="muted">${recipe?.training_stage === "ppo" ? "PPO 학습에만 씁니다." : REASONS.rewardNotPpo}</small>`);
  }
  return `${selectHtml("node-recipe", "레시피", "recipe_revision", choices, current, at("recipe_revision"), recipeHelp)}
    ${selectHtml("node-model-input", "입력 모델", "model_input", inputChoices, inputValue, at("model_input"), inputHelp)}
    ${blockedHtml(view, node.id, "model_input")}
    ${selectHtml("node-dataset", "학습 데이터", "dataset_binding", dataChoices, String(node.dataset_binding ?? ""), at("dataset_binding"), dataHelp)}
    ${reward}${profilesHtml(view, node, at("executor_profile_ids"))}`;
}

function modelSelect(view: Shown, node: Obj, label: string, at: (field: string) => WorkflowIssue[]): string {
  const choices: Choice[] = [{ value: "", label: `${label} 선택` }, ...sourceChoices(view, node.id, "model")];
  const value = refValue(node.model);
  if (value === "keep") choices.push({ value: "keep", label: "이전 실행의 산출물 · 지원하지 않는 입력" });
  return `${selectHtml("node-model", label, "model", choices, value, at("model"))}${blockedHtml(view, node.id, "model")}`;
}

function evaluateFields(view: Shown, node: Obj, at: (field: string) => WorkflowIssue[]): string {
  const current: string = typeof node.benchmark_revision === "string" ? node.benchmark_revision : "";
  const list = benchmarkList ?? [];
  const choices: Choice[] = [{ value: "", label: "평가 기준 선택" }, ...list.map(item => ({ value: item.revision_sha256,
    label: `${EVALUATOR_LABELS[item.spec.evaluator_kind as keyof typeof EVALUATOR_LABELS] ?? item.spec.evaluator_kind} · ${item.spec.dataset.dataset_id} v${item.spec.dataset.version} · 샘플 ${item.spec.ordered_sample_ids.length} · ${item.revision_sha256.slice(0, 8)}` }))];
  if (current && !list.some(item => item.revision_sha256 === current)) choices.push({ value: current, label: `저장된 평가 기준 · ${current.slice(0, 8)} · 목록에 없음` });
  const help = `<div class="actions"><button type="button" data-action="wf:graph:benchmark-new" data-focus-key="benchmark-new">새 평가 기준</button></div>
    ${benchmarkError ? `<small class="error-text">평가 기준 목록을 불러오지 못했습니다. ${escapeHtml(benchmarkError)}</small>` : '<small class="muted">평가 기준은 검증·시험 데이터의 고정된 샘플과 기반 모델의 토크나이저로 저장합니다.</small>'}`;
  return `${modelSelect(view, node, "평가할 모델", at)}
    ${selectHtml("node-benchmark", "평가 기준", "benchmark_revision", choices, current, at("benchmark_revision"), help)}
    ${profilesHtml(view, node, at("executor_profile_ids"))}`;
}

function selectFields(view: Shown, node: Obj, at: (field: string) => WorkflowIssue[]): string {
  const chosen = new Set((Array.isArray(node.evaluations) ? node.evaluations : []).filter(isRef).map((ref: Obj) => ref.node_id));
  const options = inputOptions(view.graph, node.id, "evaluations", view.dctx).filter(option => findNode(view.graph, option.value.node_id)?.kind === "evaluate");
  const edgesFull = graphEdgeCount(view.graph) >= MAX_EDGES;     // another evaluation would add a connection
  const boxes = options.map((option, index) => {
    const id = option.value.node_id;
    const reason = option.reason ?? (edgesFull && !chosen.has(id) ? REASONS.edgeLimit : undefined);
    const off = Boolean(reason) && !chosen.has(id);
    return `<label class="check"><input type="checkbox" id="node-evaluation-${index}" data-focus-key="evaluation:${escapeHtml(id)}" data-node-evaluation="${escapeHtml(id)}"${chosen.has(id) ? " checked" : ""}${off ? " disabled" : ""}>${escapeHtml(labelOf(findNode(view.graph, id)))}${reason ? ` <small>${escapeHtml(reason)}</small>` : ""}</label>`;
  }).join("");
  const selection: Obj = isObject(node.selection) ? node.selection : {};
  const kinds = selectionEvaluators(view.graph, node as NodeJson, view.dctx);
  const choices = kinds === null ? null : metricChoices(kinds);
  const metric = typeof selection.metric === "string" ? selection.metric : "";
  const metricOptions: Choice[] = [{ value: "", label: "선택 지표 선택" }, ...(choices ?? []).filter(choice => choice.metric).map(choice => ({ value: choice.metric,
    label: choice.allowed ? choice.label : `${choice.label} — ${choice.reason}`, disabled: !choice.allowed }))];
  if (metric && !metricOptions.some(choice => choice.value === metric)) metricOptions.push({ value: metric, label: `${metric} · 이 평가 기준에 없는 지표` });
  const picked = choices?.find(choice => choice.metric === metric);
  const blockedReason = choices && !choices.some(choice => choice.allowed) ? choices[0].reason : null;
  const metricHelp = [picked?.note ? `<small class="graph-note">${escapeHtml(picked.note)}</small>` : "",
    blockedReason ? `<p class="graph-reason">${escapeHtml(blockedReason)}</p>` : "",
    kinds === null ? '<small class="muted">평가 기준 정보를 불러오지 못해 지표를 확인할 수 없습니다.</small>' : ""].join("");
  const thresholds: Obj[] = Array.isArray(selection.thresholds) ? selection.thresholds : [];
  const editable = thresholds.length === 0 || (thresholds.length === 1 && thresholds[0]?.metric === metric && thresholds[0]?.direction === (selection.direction ?? "max"));
  // A threshold belongs to the selection metric: without one it cannot be kept, so it waits for the metric.
  const threshold = editable
    ? numberHtml("node-threshold", "기준값 (선택)", "threshold", thresholds[0]?.value, metric ? 'step="any"' : 'step="any" disabled', at("selection/thresholds"),
      `<small class="muted">${!metric ? "선택 지표를 고른 뒤 입력할 수 있습니다." : (selection.direction ?? "max") === "min" ? "이 값보다 높은 후보는 제외합니다." : "이 값보다 낮은 후보는 제외합니다."}</small>`)
    : `<p class="muted">저장된 기준 ${thresholds.length}개는 그대로 둡니다.</p>`;
  return `<fieldset class="graph-choices"${at("evaluations").length ? ' aria-invalid="true"' : ""}><legend>비교할 평가</legend>${boxes || '<p class="muted">평가 단계를 먼저 추가하세요.</p>'}${issueList(at("evaluations"))}</fieldset>
    ${selectHtml("node-metric", "선택 지표", "selection.metric", metricOptions, metric, at("selection/metric"), metricHelp)}
    ${selectHtml("node-direction", "방향", "selection.direction", [...(selection.direction ? [] : [{ value: "", label: "방향 선택" }]),
      { value: "max", label: "높을수록 좋음 (max)" }, { value: "min", label: "낮을수록 좋음 (min)" }], String(selection.direction ?? ""), at("selection/direction"))}
    ${threshold}
    <div class="graph-field"><label class="check"><input type="checkbox" id="node-partial" data-node-field="selection.allow_partial"${selection.allow_partial ? " checked" : ""}>일부 평가만 성공해도 선택</label></div>
    ${numberHtml("node-min-success", "최소 성공 수", "selection.min_success", selection.min_success ?? 2, 'min="2" step="1"', at("selection/min_success"))}
    ${numberHtml("node-tolerance", "병합 회귀 허용치", "selection.merge_regression_tolerance", selection.merge_regression_tolerance ?? 0, 'min="0" step="any"', at("selection/merge_regression_tolerance"))}
    <p class="graph-note">${escapeHtml(NOTES.select)}</p>`;
}

function mergeSum(node: Obj): string {
  const count = Array.isArray(node.models) ? node.models.length : 0;
  if (weightTextInvalid) return "가중치는 0보다 큰 숫자(%)로 입력하세요. 숫자가 아닌 값은 반영하지 않았습니다.";
  if (!count) return "병합할 모델을 2–4개 고르세요.";
  const weights: unknown[] = Array.isArray(node.weights) ? node.weights : [];
  const total = weights.map(percentText).filter(Boolean).reduce((sum, text) => sum + Math.round(Number(text) * 1e6), 0) / 1e6;
  return weightsValid(node.weights, count) ? `가중치 합계 100% · 모델 ${count}개` : `가중치 합계 ${total}% · 정확히 100%여야 합니다`;
}
function mergeFields(view: Shown, node: Obj, at: (field: string) => WorkflowIssue[]): string {
  const models: unknown[] = Array.isArray(node.models) ? node.models : [];
  const weights: unknown[] = Array.isArray(node.weights) ? node.weights : [];
  const chosen = models.map(item => isRef(item) ? item.node_id : null);
  const options = inputOptions(view.graph, node.id, "models", view.dctx).filter(option => option.value.node_id !== node.id
    && findNode(view.graph, option.value.node_id)?.kind !== "evaluate");
  const edgesFull = graphEdgeCount(view.graph) >= MAX_EDGES;     // another model would add a connection
  const rows = options.map((option, index) => {
    const id = option.value.node_id;
    const position = chosen.indexOf(id);
    const label = labelOf(findNode(view.graph, id));
    if (option.allowed && edgesFull && position < 0) option = { ...option, allowed: false, reason: REASONS.edgeLimit };
    const off = !option.allowed && position < 0;
    return `<div class="graph-merge-row"><label class="check"><input type="checkbox" id="node-merge-${index}" data-focus-key="merge:${escapeHtml(id)}" data-node-merge="${escapeHtml(id)}"${position >= 0 ? " checked" : ""}${off ? " disabled" : ""}>${escapeHtml(label)}</label>
      ${position >= 0 ? `<label class="graph-weight"><span>가중치 (%)</span><input id="node-weight-${index}" type="text" inputmode="decimal" data-node-weight="${escapeHtml(id)}" aria-label="${escapeHtml(label)} 가중치 (%)" value="${escapeHtml(percentText(weights[position]))}"></label>` : ""}
      ${option.reason ? `<small>${escapeHtml(option.reason)}</small>` : ""}</div>`;
  }).join("");
  const external = chosen.filter(id => id === null).length;
  return `<fieldset class="graph-choices graph-merge"${at("models").length ? ' aria-invalid="true"' : ""}><legend>병합할 모델 (2–4개)</legend>${rows || '<p class="muted">학습 단계를 먼저 추가하세요.</p>'}
    ${external ? `<p class="muted">이전 실행의 산출물 ${external}개는 그대로 둡니다.</p>` : ""}
    <p id="merge-sum" class="graph-merge-sum" role="status">${escapeHtml(mergeSum(node))}</p>${issueList([...at("models"), ...at("weights")])}</fieldset>
    <p class="graph-note">${escapeHtml(NOTES.mergeRealOnly)}</p>${profilesHtml(view, node, at("executor_profile_ids"))}`;
}

function materializeFields(view: Shown, node: Obj, at: (field: string) => WorkflowIssue[]): string {
  return `${modelSelect(view, node, "확정할 모델", at)}
    <p class="graph-note">LoRA·QLoRA 어댑터를 기반 모델에 합쳐 전체 가중치 모델로 확정합니다. 학습 방법이 바뀌는 다음 학습 단계 앞에 넣으세요.</p>
    ${profilesHtml(view, node, at("executor_profile_ids"))}`;
}

const FORM_FIELDS: Record<NodeKind, string[]> = {
  train: ["label", "recipe_revision", "model_input", "dataset_binding", "reward_binding", "executor_profile_ids"],
  evaluate: ["label", "model", "benchmark_revision", "executor_profile_ids"],
  select: ["label", "evaluations", "selection/metric", "selection/direction", "selection/thresholds", "selection/min_success", "selection/merge_regression_tolerance"],
  merge: ["label", "models", "weights", "executor_profile_ids"], materialize: ["label", "model", "executor_profile_ids"],
};
function fieldIssuesFor(view: Shown, nodeId: string) {
  const index = view.saved.indexOf(nodeId);
  return (field: string) => index < 0 ? [] : issuesAt(view.issues, `/graph/nodes/${index}/${field}`);
}
/** The card's reasons, missing fields, check results not shown under a field, plan warnings and the targets hint. */
function nodeReasonsHtml(view: Shown, node: NodeJson): string {
  const at = fieldIssuesFor(view, node.id);
  const placed = new Set((FORM_FIELDS[node.kind] ?? []).flatMap(field => at(field)));
  const mine = view.local.filter(issue => issue.node_id === node.id);
  const reasons = [...new Set(mine.filter(issue => issue.code !== "local_required").map(issue => issue.message))];
  const todo = [...new Set(mine.filter(issue => issue.code === "local_required").map(issue => issue.message))];
  const checked = cardIssues(view, node.id).filter(issue => !placed.has(issue));
  const warned = cardWarnings(view, node.id);
  const hint = targetsHint(view, node);
  return [
    view.waiting.has(node.id) ? `<p class="graph-card-wait">${escapeHtml(NOTES.waiting)}</p>` : "",
    reasons.length ? `<div class="graph-reason-box"><p class="graph-blocked-title">확인할 점</p><ul class="graph-reasons">${reasons.map(reason => `<li>${escapeHtml(reason)}</li>`).join("")}</ul></div>` : "",
    todo.length ? `<div class="graph-todo-box"><p class="graph-blocked-title">채울 항목</p><ul>${todo.map(item => `<li>${escapeHtml(item)}</li>`).join("")}</ul></div>` : "",
    checked.length ? issueList(checked, { heading: "저장한 그래프 검사 결과" }) : "",
    warned.length ? issueList(warned, { heading: "계획 경고" }) : "",
    hint ? `<p class="graph-card-hint">${escapeHtml(hint.text)}${hint.root ? ` ${button("모듈 구조 확인", `wf:graph:card-targets:${node.id}`)}` : ""}</p>` : "",
  ].join("");
}

function nodeFormHtml(ctx: ViewContext, view: Shown): string {
  weightTextInvalid = false;
  const id = view.selected;
  if (!id) return view.graph.nodes.length ? '<section class="card graph-node-empty"><p class="muted">카드를 고르면 여기에서 이름, 입력, 데이터와 실행 프로필을 정합니다.</p></section>' : "";
  const node = findNode(view.graph, id)! as Obj;
  const at = fieldIssuesFor(view, id);
  const kind = node.kind as NodeKind;
  const body = kind === "train" ? trainFields(view, node, at) : kind === "evaluate" ? evaluateFields(view, node, at)
    : kind === "select" ? selectFields(view, node, at) : kind === "merge" ? mergeFields(view, node, at)
    : kind === "materialize" ? materializeFields(view, node, at) : '<p class="muted">이 단계 종류는 화면에서 고칠 수 없습니다. 저장하면 그대로 둡니다.</p>';
  const labelIssues = at("label");
  return `<form id="node-form" class="card graph-node-form" data-project-id="${escapeHtml(view.projectId)}" data-node-id="${escapeHtml(id)}" novalidate aria-labelledby="node-form-title">
    <div class="section-title"><h3 id="node-form-title" tabindex="-1">${escapeHtml(KIND_LABELS[kind] ?? "알 수 없는 단계")} · ${escapeHtml(id)}</h3></div>
    <fieldset class="graph-fieldset"${ctx.busy ? " disabled" : ""}>
    <div class="graph-field"><label class="field"><span>단계 이름</span><input id="node-label" data-node-field="label" aria-label="단계 이름" maxlength="200" value="${escapeHtml(typeof node.label === "string" ? node.label : "")}"${labelIssues.length ? ' aria-invalid="true" aria-describedby="node-label-issues"' : ""}></label>${labelIssues.length ? `<div id="node-label-issues">${issueList(labelIssues)}</div>` : ""}</div>
    ${body}</fieldset>
    <div id="node-reasons" class="graph-node-reasons">${nodeReasonsHtml(view, node as NodeJson)}</div></form>`;
}

function benchmarkHtml(ctx: ViewContext, view: Shown): string {
  if (!benchmarkFor || benchmarkFor.projectId !== view.projectId || !benchmarkDraft || !findNode(view.graph, benchmarkFor.nodeId)) return "";
  const rows = rowCounts(ctx, view.draft);
  return benchmarkFormHtml(benchmarkDraft, view.dctx, { busy: ctx.busy, issues: benchmarkIssues,
    problems: benchmarkTried ? benchmarkProblems(benchmarkDraft, view.dctx, rows) : [], rows, nodeLabel: labelOf(findNode(view.graph, benchmarkFor.nodeId)) });
}

function graphPage(ctx: ViewContext): string {
  const projectId = ctx.arg;
  const empty = (body: string) => `<div class="graph-page" data-tour="graph-editor"><section class="card graph-empty">${body}</section></div>`;
  if (!projectId) return empty(`<div class="empty">프로젝트를 먼저 여세요. 학습 그래프는 프로젝트마다 하나씩 있습니다.</div><div class="actions">${button("프로젝트 목록", "nav:projects")}</div>`);
  const view = shown(ctx);
  const failed = loadIssues.get(projectId);
  if (!view) return empty(failed ? `<div class="wf-banner bad">${unavailable ? NOT_AVAILABLE : "프로젝트를 불러오지 못했습니다."}</div>${unavailable ? "" : issueList(failed)}
      <div class="actions">${button("다시 불러오기", "wf:graph:reopen")}${button("프로젝트 목록", "nav:projects", "quiet")}</div>`
    : '<div class="empty">프로젝트를 먼저 여세요. 프로젝트를 불러오는 중입니다.</div>');
  return `<div class="graph-page" data-tour="graph-editor" data-project-id="${escapeHtml(projectId)}">${conflictHtml(view.draft)}
    ${failed ? `<div class="wf-banner bad">최신 버전을 다시 읽지 못해 이 PC의 초안을 보여 줍니다.</div>${issueList(failed)}` : ""}${summaryHtml(view)}
    <div class="graph-layout"><div class="graph-side"><section class="card graph-head" id="graph-head" aria-label="프로젝트와 저장">${headHtml(ctx, view)}</section>
      ${modelRootsPanel(rootsState, ctx.busy)}${settingsHtml(ctx, view)}</div>
    <section class="card graph-centre" aria-labelledby="graph-cards-title"><div class="section-title"><h3 id="graph-cards-title">단계 카드 <span class="count">${view.graph.nodes.length}</span></h3></div>
      <div id="graph-cards">${cardsHtml(ctx, view)}</div><div id="graph-toolbar">${toolbarHtml(ctx, view)}</div>
      <div id="graph-preview" class="graph-preview">${previewHtml(view)}</div></section>
    <div class="graph-detail">${nodeFormHtml(ctx, view)}${benchmarkHtml(ctx, view)}</div></div>
    ${planPanelHtml(view.draft, planView(ctx, view.draft), ctx.busy)}</div>`;
}

/** Re-renders what a typed value changes, without touching the control being typed in. */
function refreshDerived(ctx: ViewContext) {
  const view = shown(ctx);
  if (!view) return;
  const options = { keepTyping: false };
  ctx.patch("graph-head", headHtml(ctx, view), options);
  ctx.patch("graph-cards", cardsHtml(ctx, view), options);
  ctx.patch("graph-preview", previewHtml(view), options);
  const node = view.selected ? findNode(view.graph, view.selected) : undefined;
  if (node && document.getElementById("node-reasons")) ctx.patch("node-reasons", nodeReasonsHtml(view, node), options);
  if (node && document.getElementById("merge-sum")) ctx.patch("merge-sum", escapeHtml(mergeSum(node as Obj)), options);
  if (document.getElementById("graph-settings-note")) ctx.patch("graph-settings-note", settingsNoteHtml(view), options);
  // A draft edit drops a held plan and changes what the plan panel can do (10.6).
  if (document.getElementById("plan-panel")) {
    capturePlanPanel();
    ctx.patch("plan-panel", planPanelBody(view.draft, planView(ctx, view.draft), ctx.busy), options);
    bindPlan(ctx);                                         // the patch replaced its forms (the shell rebinds only actions)
  }
}
function bindPlan(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (projectId && document.getElementById("plan-panel"))
    bindPlanPanel(ctx, projectId, () => planView(ctx, ctx.store.draft(projectId)!), () => profileList ?? []);
}
/** A full render that keeps the focus on the control that changed (by its focus key). */
function rerender(ctx: ViewContext, key: string | null) {
  ctx.render();
  if (key) restoreFocus(document, key);
}

// -- card form edits (applied as they happen; nothing is left for capture) --
function onNodeInput(ctx: ViewContext, event: Event) {
  const target = event.target;
  const form = document.querySelector<HTMLFormElement>("#node-form");
  if (!(target instanceof HTMLInputElement) || !form || locked(form) || ctx.busy) return;
  const projectId = form.dataset.projectId!, nodeId = form.dataset.nodeId!;
  if (target.id === "node-label") {
    const label = target.value;
    editNode(ctx, projectId, nodeId, node => { if (label) node.label = label; else delete node.label; });
  } else if (target.dataset.nodeWeight !== undefined) {
    editNode(ctx, projectId, nodeId, node => {
      if (!Array.isArray(node.models)) return;
      const weights: unknown[] = Array.isArray(node.weights) && node.weights.length === node.models.length ? [...node.weights] : node.models.map(() => null);
      weightTextInvalid = false;
      form.querySelectorAll<HTMLInputElement>("[data-node-weight]").forEach(input => {
        const position = node.models.findIndex((item: unknown) => isRef(item) && item.node_id === input.dataset.nodeWeight);
        const weight = weightFromPercent(input.value);
        if (weight === null) weightTextInvalid = true;
        if (position >= 0 && weight !== null) weights[position] = weight;
      });
      if (weights.every(weight => typeof weight === "number")) node.weights = weights;
    });
  } else if (target.type === "number" && target.dataset.nodeField) {
    const field = target.dataset.nodeField, text = target.value.trim();
    const value = text === "" ? undefined : Number(text);
    editNode(ctx, projectId, nodeId, node => {
      if (node.kind !== "select") return;
      const selection: Obj = isObject(node.selection) ? node.selection : (node.selection = {});
      if (field === "threshold") {
        const kept = Array.isArray(selection.thresholds) && selection.thresholds.length === 1 && isObject(selection.thresholds[0]) ? selection.thresholds[0] : null;
        if (value === undefined || !Number.isFinite(value) || !selection.metric) selection.thresholds = [];
        else if (kept) Object.assign(kept, { metric: selection.metric, direction: selection.direction ?? "max", value });   // in place: its key order stays
        else selection.thresholds = [{ metric: selection.metric, direction: selection.direction ?? "max", value }];
      } else {
        const key = field.replace(/^selection\./, "");
        if (value === undefined || !Number.isFinite(value)) delete selection[key]; else selection[key] = value;
      }
    });
  } else return;
  refreshDerived(ctx);
}

/** A select or checkbox of the card form: one field of the card changes, then the form is rendered again (the
 * choices and reasons depend on it) with the focus kept on that control. */
function onNodeChange(ctx: ViewContext, event: Event) {
  const target = event.target;
  const form = document.querySelector<HTMLFormElement>("#node-form");
  if (!form || locked(form) || ctx.busy) return;
  const checkbox = target instanceof HTMLInputElement && target.type === "checkbox";
  if (!(target instanceof HTMLSelectElement) && !checkbox) return;
  const projectId = form.dataset.projectId!, nodeId = form.dataset.nodeId!;
  const draft = ctx.store.draft(projectId);
  if (!draft) return;
  const dctx = draftContext(draft);
  const element = target as HTMLInputElement | HTMLSelectElement;
  const field = element.dataset.nodeField;
  const rootAt = (value: string) => rootsState.roots[Number(value.slice(5))]?.root;
  const ports: [string | undefined, InputPort, "model" | "evaluation"][] = [[element.dataset.nodeEvaluation, "evaluations", "evaluation"],
    [element.dataset.nodeMerge, "models", "model"]];
  for (const [id, port, output] of ports) {
    if (!checkbox || id === undefined) continue;
    // A chosen evaluation or merge model is appended; a changed merge count resets equal weights (graph-model.connect).
    editGraph(ctx, projectId, graph => {
      const node = findNode(graph, nodeId) as Obj | undefined;
      const current: unknown[] = Array.isArray(node?.[port]) ? node![port] : [];
      const others = current.filter(item => !(isRef(item) && item.node_id === id));
      return connect(graph, nodeId, port, ((element as HTMLInputElement).checked ? [...others, outputRef(id, output)] : others) as never);
    });
    rerender(ctx, focusKey(element));
    return;
  }
  editNode(ctx, projectId, nodeId, node => {
    if (element instanceof HTMLSelectElement && field === "recipe_revision") {
      const before = dctx.recipes.get(node.recipe_revision)?.training_method;
      if (element.value) node.recipe_revision = element.value; else delete node.recipe_revision;
      const after = dctx.recipes.get(element.value)?.training_method;
      // Only the recipe changes; a root input follows the new method family's initialization (D6, 10.5).
      if (isRootInput(node.model_input) && initializationFor(before) !== initializationFor(after) && after)
        node.model_input = { ...node.model_input, initialization: initializationFor(after) };
    } else if (element instanceof HTMLSelectElement && (field === "model_input" || field === "model" || field === "reward_binding")) {
      const value = element.value;
      if (value === "keep") return;
      if (value === "") { delete node[field]; return; }
      if (value.startsWith("node:")) { node[field] = outputRef(value.slice(5)); return; }
      const root = rootAt(value);
      if (!root) return;
      if (field === "model_input") {
        if (!(isRootInput(node.model_input) && sameRoot(node.model_input.policy.base, root)))
          node.model_input = rootInput(root, dctx.recipes.get(node.recipe_revision)?.training_method);
      } else if (field === "reward_binding") node.reward_binding = { base: { ...root }, adapter: null };
    } else if (element instanceof HTMLSelectElement && (field === "dataset_binding" || field === "benchmark_revision")) {
      if (element.value) node[field] = element.value; else delete node[field];
    } else if (element instanceof HTMLSelectElement && field?.startsWith("selection.")) {
      const selection: Obj = isObject(node.selection) ? node.selection : (node.selection = {});
      const key = field.slice("selection.".length);
      if (element.value) selection[key] = element.value; else delete selection[key];
      // A single editable threshold follows the chosen metric and direction.
      if (Array.isArray(selection.thresholds) && selection.thresholds.length === 1 && (key === "metric" || key === "direction"))
        selection.thresholds = element.value ? [{ ...selection.thresholds[0], [key]: element.value }] : [];
    } else if (checkbox && field === "selection.allow_partial") {
      const selection: Obj = isObject(node.selection) ? node.selection : (node.selection = {});
      selection.allow_partial = (element as HTMLInputElement).checked;
    } else if (checkbox && element.dataset.nodeProfile !== undefined) {
      const id = element.dataset.nodeProfile;
      const current: string[] = Array.isArray(node.executor_profile_ids) ? node.executor_profile_ids : [];
      const next = (element as HTMLInputElement).checked ? [...current.filter(item => item !== id), id] : current.filter(item => item !== id);
      if (next.length) node.executor_profile_ids = next; else delete node.executor_profile_ids;
    }
  });
  rerender(ctx, focusKey(element));
}

// -- actions --
function addAndFocus(ctx: ViewContext, projectId: string, build: (graph: GraphJson, dctx: DraftContext) => { graph: GraphJson; id: string }) {
  const draft = ctx.store.draft(projectId);
  if (!draft) return;
  let created: { graph: GraphJson; id: string };
  try { created = build(graphOf(draft), draftContext(draft)); }
  catch (error) { toolbarNote = errorText(error); ctx.render(); return; }
  ctx.store.edit(projectId, spec => { spec.graph = graphToJson(created.graph); });
  selection.set(projectId, created.id);
  addMenu = false;
  toolbarNote = `‘${labelOf(findNode(created.graph, created.id))}’ 단계를 추가했습니다. 저장해야 반영됩니다.`;
  ctx.render();
  focusLabel();
}

function selectCard(ctx: ViewContext, nodeId: string) {
  const projectId = ctx.arg;
  if (!projectId) return;
  if (selection.get(projectId) !== nodeId) addMenu = false;
  if (benchmarkFor && benchmarkFor.nodeId !== nodeId) { benchmarkFor = null; benchmarkIssues = []; benchmarkTried = false; }
  selection.set(projectId, nodeId);
  toolbarNote = null;
  ctx.render();
  restoreFocus(document, `card:${nodeId}`);
}

function toggleMenu(ctx: ViewContext, open: boolean) {
  addMenu = open;
  ctx.render();
  if (open) document.querySelector<HTMLButtonElement>("#graph-add-kinds button:not(:disabled)")?.focus();
  else restoreFocus(document, "wf:graph:add-menu");
}

function removeCard(ctx: ViewContext) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  const graph = graphOf(draft);
  const id = selectedIn(projectId, graph);
  if (!id) return;
  const order = layout(graph).columns.flat();
  const next = order[order.indexOf(id) - 1] ?? order[order.indexOf(id) + 1] ?? null;
  const label = labelOf(findNode(graph, id));
  editGraph(ctx, projectId, current => removeNode(current, id));
  if (next) selection.set(projectId, next); else selection.delete(projectId);
  if (benchmarkFor?.nodeId === id) benchmarkFor = null;
  addMenu = false;
  toolbarNote = `‘${label}’ 단계와 이 단계로 들어오던 연결을 지웠습니다. 저장하기 전에는 저장된 그래프가 바뀌지 않습니다.`;
  ctx.render();
  // The last card gone: the empty graph has no toolbar, only its starting shapes.
  if (next) restoreFocus(document, `card:${next}`); else document.querySelector<HTMLButtonElement>(".graph-starters button")?.focus();
}

function useCurrentRecipe(ctx: ViewContext) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  const graph = graphOf(draft);
  const id = selectedIn(projectId, graph);
  const node = id ? findNode(graph, id) as Obj : undefined;
  const pinned = node?.recipe_revision ? pinnedRecipes.get(node.recipe_revision) : undefined;
  const current = pinned ? recipeList?.find(recipe => recipe.recipe_id === pinned.recipe_id) : undefined;
  if (!id || !current) return;
  editNode(ctx, projectId, id, item => {
    const before = initializationFor(pinned!.training_method), after = initializationFor(current.training_method);
    item.recipe_revision = current.revision_sha256;
    if (isRootInput(item.model_input) && before !== after) item.model_input = { ...item.model_input, initialization: after };
  });
  ctx.render();
  document.getElementById("node-recipe")?.focus();
}

async function readLists(ctx: ViewContext, draft: ProjectDraft | undefined) {
  try {
    const result = await queue.run(() => recipeApi.list());
    if (result.status === "ok" && result.data) { recipeList = result.data.recipes; recipeError = null; }
    else { recipeError = notAvailable(result) ? NOT_AVAILABLE : describe(result.issues); recipeList ??= []; }
  } catch (error) { recipeError = errorText(error); recipeList ??= []; }
  const pinned = new Set<string>();
  for (const node of draft ? graphOf(draft).nodes : []) {
    const revision = (node as Obj).recipe_revision;
    if (node.kind === "train" && typeof revision === "string" && /^[0-9a-f]{64}$/.test(revision)
        && !(recipeList ?? []).some(recipe => recipe.revision_sha256 === revision) && !pinnedRecipes.get(revision)) pinned.add(revision);
  }
  for (const revision of pinned) {
    try {
      // 9.8: a recipe revision by its hash alone, also after the recipe moved on.
      const result = await queue.run(() => workflow<RecipeRevision>("recipe_get", { revision_sha256: revision }));
      pinnedRecipes.set(revision, result.status === "ok" && result.data ? summaryOf(result.data) : null);
    } catch { pinnedRecipes.set(revision, null); }
  }
  await queue.run(() => loadRoots());
  try {
    const result = await queue.run(() => planning.benchmarks());
    if (result.status === "ok" && result.data) { benchmarkList = result.data.benchmarks; benchmarkError = null; }
    else { benchmarkError = notAvailable(result) ? NOT_AVAILABLE : describe(result.issues); benchmarkList ??= []; }
  } catch (error) { benchmarkError = errorText(error); benchmarkList ??= []; }
  try { profileList = (await queue.run(() => executorProfiles())).profiles; profileError = null; }
  catch (error) { profileError = errorText(error); }
}

async function enterGraph(ctx: ViewContext) {
  addMenu = false; toolbarNote = null; benchmarkFor = null; benchmarkIssues = []; benchmarkTried = false;
  const projectId = ctx.arg;
  if (!projectId) return;
  loadIssues.delete(projectId);
  unavailable = false;                                     // this project's own reads decide again
  await ctx.task("학습 그래프 불러오는 중", async () => {
    // The stored planning inputs first (read-only, 9.8), then the project and its lists as before (10.6).
    await queue.run(() => readPlanningInputs(projectId));
    const opened = await queue.run(() => ctx.store.open(projectId));   // null: a dirty draft is kept as it is
    if (opened && opened.status !== "ok") {
      if (notAvailable(opened)) unavailable = true;
      loadIssues.set(projectId, opened.issues);
    }
    applyPlanningInputs(ctx.store, projectId);
    await readLists(ctx, ctx.store.draft(projectId));
  });
}

async function saveGraph(ctx: ViewContext) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft || ctx.busy) return;
  const still = stillOn(ctx);
  await ctx.task("학습 그래프 저장 중", async () => {
    const result = await ctx.store.save(projectId);
    if (result.status === "conflict") return;      // the draft keeps its edits; the banner offers the D5 choices
    if (result.status !== "ok") { if (notAvailable(result)) unavailable = true; return; }
    await validate(ctx, projectId);
  });
  refocus(still, "graph-save");
}
/** `graph_validate` of the revision just saved (D4: validation reads the saved revision). */
async function validate(ctx: ViewContext, projectId: string) {
  const draft = ctx.store.draft(projectId)!;
  const revision = draft.saved?.revision_sha256;
  const result = await ctx.store.validate(projectId);
  if (result.status === "ok") {
    if (revision) validated.set(projectId, revision);
    ctx.notify("학습 그래프를 저장했습니다. 저장한 버전을 검사했고 문제가 없습니다.");
  } else if (notAvailable(result)) {
    draft.issues = [];
    ctx.notify(`학습 그래프를 저장했습니다. 그래프 검사는 하지 못했습니다. (${NOT_AVAILABLE})`);
  } else ctx.notify("학습 그래프를 저장했습니다. 저장한 버전의 검사에서 확인할 점이 나왔습니다.");
}

async function reloadGraph(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (!projectId || !ctx.store.draft(projectId)) return;
  const answer = await openDialog({ title: "최신 버전 불러오기",
    bodyHtml: "<p>다른 곳에서 저장한 최신 버전으로 바꿉니다. 저장하지 않은 내 그래프 변경은 사라집니다.</p>",
    actions: [{ id: "back", label: "돌아가기" }, { id: "reload", label: "불러오기", primary: true }] });
  if (answer?.action !== "reload") return;
  const still = stillOn(ctx);
  await ctx.task("최신 버전 불러오는 중", async () => {
    const result = await ctx.store.reloadLatest(projectId);
    if (result.status !== "ok") throw new Error(describe(result.issues));
    ctx.notify("최신 버전을 불러왔습니다.");
  });
  refocus(still, "graph-save");
}

async function overwriteGraph(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (!projectId || !ctx.store.draft(projectId)) return;
  const answer = await openDialog({ title: "내 변경으로 덮어쓰기",
    bodyHtml: "<p>다른 곳에서 먼저 저장한 변경을 지금 내 초안으로 덮어씁니다. 그 변경은 최신 버전에서 빠지고 이전 버전 기록으로만 남습니다.</p>",
    actions: [{ id: "back", label: "돌아가기" }, { id: "overwrite", label: "덮어쓰기", primary: true }] });
  if (answer?.action !== "overwrite") return;
  const still = stillOn(ctx);
  await ctx.task("내 변경으로 저장 중", async () => {
    const result = await ctx.store.overwriteWithDraft(projectId);
    if (result.status === "ok") await validate(ctx, projectId);
    else if (result.status === "invalid") ctx.store.draft(projectId)!.issues = result.issues;
  });
  refocus(still, "graph-save");
}

function openBenchmark(ctx: ViewContext) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  const nodeId = projectId ? selection.get(projectId) : undefined;
  if (!projectId || !draft || !nodeId) return;
  benchmarkFor = { projectId, nodeId };
  benchmarkDraft = newBenchmarkDraft(draftContext(draft), rowCounts(ctx, draft));
  benchmarkIssues = []; benchmarkTried = false;
  ctx.render();
  document.getElementById("bench-binding")?.focus();
}
function closeBenchmark(ctx: ViewContext) {
  benchmarkFor = null; benchmarkIssues = []; benchmarkTried = false;
  ctx.render();
  restoreFocus(document, "benchmark-new");
}
async function saveBenchmark(ctx: ViewContext) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft || !benchmarkFor || !benchmarkDraft || ctx.busy) return;
  captureBenchmark();
  const dctx = draftContext(draft);
  const rows = rowCounts(ctx, draft);
  benchmarkTried = true;
  if (benchmarkProblems(benchmarkDraft, dctx, rows).length) { benchmarkIssues = []; ctx.render(); restoreFocus(document, "benchmark-save"); return; }
  const spec = benchmarkSpec(benchmarkDraft, dctx);
  const target = benchmarkFor;
  const still = stillOn(ctx);
  let saved = false;
  await ctx.task("평가 기준 저장 중", async () => {
    benchmarkIssues = [];
    const result = await queue.run(() => planning.benchmarkSave(spec));
    if (result.status !== "ok" || !result.data) {
      benchmarkIssues = result.issues.length ? result.issues : [{ code: "refused", path: "/spec", message: "평가 기준을 저장하지 못했습니다." }];
      return;
    }
    const revision = result.data;
    benchmarkList = [...(benchmarkList ?? []).filter(item => item.revision_sha256 !== revision.revision_sha256), revision];
    benchmarkError = null;
    editNode(ctx, target.projectId, target.nodeId, node => { node.benchmark_revision = revision.revision_sha256; });
    benchmarkFor = null; benchmarkTried = false; saved = true;
    ctx.notify("평가 기준을 저장하고 이 평가 단계에 연결했습니다. 그래프는 아직 저장하지 않았습니다.");
  });
  if (saved && still()) document.getElementById("node-benchmark")?.focus();
}
function captureBenchmark() {
  const form = document.querySelector<HTMLFormElement>("#benchmark-form");
  if (form && benchmarkDraft && !locked(form)) benchmarkDraft = readBenchmarkForm(form, benchmarkDraft);
}

function onSettingsInput(ctx: ViewContext, event: Event) {
  const target = event.target;
  const form = document.querySelector<HTMLFormElement>("#graph-settings-form");
  const projectId = ctx.arg;
  if (!(target instanceof HTMLInputElement) || !target.dataset.setting || !form || locked(form) || !projectId || ctx.busy) return;
  const text = target.value.trim();
  const value = text === "" ? undefined : Number(text);
  const [group, key] = target.dataset.setting.split(".");
  editGraph(ctx, projectId, graph => {
    const next = graph as Obj;
    if (key) {
      const policy: Obj = isObject(next[group]) ? next[group] : (next[group] = {});
      if (value === undefined || !Number.isFinite(value)) delete policy[key]; else policy[key] = value;
      if (!Object.keys(policy).length) delete next[group];
    } else if (value === undefined || !Number.isFinite(value)) delete next[group];
    else next[group] = value;
    return next as GraphJson;
  });
  refreshDerived(ctx);
}

/** ArrowUp/ArrowDown (and Home/End) move the focus between the cards; the focused card becomes the tab stop. */
function onCardKey(event: KeyboardEvent) {
  if (!["ArrowUp", "ArrowDown", "Home", "End"].includes(event.key) || event.altKey || event.ctrlKey || event.metaKey) return;
  const current = (event.target as Element | null)?.closest?.("[data-node-card]");
  if (!(current instanceof HTMLButtonElement)) return;
  const cards = [...document.querySelectorAll<HTMLButtonElement>("#graph-cards [data-node-card]")];
  const index = cards.indexOf(current);
  const next = event.key === "ArrowUp" ? index - 1 : event.key === "ArrowDown" ? index + 1 : event.key === "Home" ? 0 : cards.length - 1;
  event.preventDefault();
  const target = cards[Math.max(0, Math.min(cards.length - 1, next))];
  if (!target || target === current) return;
  cards.forEach(card => { card.tabIndex = -1; });
  target.tabIndex = 0;
  target.focus();
}

function bindPage(ctx: ViewContext) {
  if (ctx.page !== "graph") return;
  document.getElementById("graph-cards")?.addEventListener("keydown", onCardKey);
  const form = document.querySelector<HTMLFormElement>("#node-form");
  form?.addEventListener("submit", event => event.preventDefault());
  form?.addEventListener("input", event => onNodeInput(ctx, event));
  form?.addEventListener("change", event => onNodeChange(ctx, event));
  const settings = document.querySelector<HTMLFormElement>("#graph-settings-form");
  settings?.addEventListener("submit", event => event.preventDefault());
  settings?.addEventListener("input", event => onSettingsInput(ctx, event));
  const bench = document.querySelector<HTMLFormElement>("#benchmark-form");
  bench?.addEventListener("submit", event => {
    event.preventDefault();
    if (!ctx.busy) void saveBenchmark(ctx).catch(error => ctx.fail(errorText(error)));
  });
  // The data version and evaluator change the rows and metrics shown under them.
  bench?.querySelectorAll<HTMLSelectElement>("#bench-binding, #bench-evaluator").forEach(element => element.addEventListener("change", () => {
    captureBenchmark(); rerender(ctx, element.id);
  }));
  const pin = document.querySelector<HTMLFormElement>("#root-pin-form");
  pin?.addEventListener("submit", event => event.preventDefault());
  pin?.querySelectorAll<HTMLInputElement>("#root-query, #root-model").forEach(input => input.addEventListener("keydown", event => {
    if (event.key !== "Enter" || ctx.busy) return;
    event.preventDefault();
    captureRootsForm();
    void (input.id === "root-query" ? searchModels(ctx) : inspectModel(ctx)).catch(error => ctx.fail(errorText(error)));
  }));
  document.getElementById("graph-add-kinds")?.addEventListener("keydown", event => {
    if (event.key === "Escape") { event.preventDefault(); toggleMenu(ctx, false); }
  });
  bindPlan(ctx);
}

async function graphAction(ctx: ViewContext, verb: string, arg: string | null) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  const selected = projectId && draft ? selectedIn(projectId, graphOf(draft)) : null;
  if (verb.startsWith("plan-")) { if (projectId && draft) await planAction(ctx, projectId, verb, arg, planEnv(draft)); return; }
  if (verb === "select" && arg) selectCard(ctx, arg);
  else if (verb === "add-menu") toggleMenu(ctx, !addMenu);
  else if (verb === "add-menu-close") toggleMenu(ctx, false);
  else if (verb === "add" && arg && projectId && selected) addAndFocus(ctx, projectId, (graph, dctx) => addSerial(graph, selected, arg as NodeKind, dctx));
  else if (verb === "add-parallel" && projectId && selected) addAndFocus(ctx, projectId, (graph, dctx) => addParallel(graph, selected, dctx));
  else if (verb === "add-root" && projectId) addAndFocus(ctx, projectId, (graph, dctx) => addNode(graph, "train", dctx));
  else if (verb === "start" && arg && projectId) addAndFocus(ctx, projectId, (graph, dctx) => {
    if (graph.nodes.length) throw new Error("시작 모양은 빈 그래프에서만 고를 수 있습니다.");
    const built = starter(arg as StarterShape, dctx);
    return { graph: built, id: built.nodes[0].id };
  });
  else if (verb === "remove") removeCard(ctx);
  else if (verb === "save") await saveGraph(ctx);
  else if (verb === "reload") await reloadGraph(ctx);
  else if (verb === "overwrite") await overwriteGraph(ctx);
  else if (verb === "recipe-current") useCurrentRecipe(ctx);
  else if (verb === "benchmark-new") openBenchmark(ctx);
  else if (verb === "benchmark-close") closeBenchmark(ctx);
  else if (verb === "root-search") await searchModels(ctx);
  else if (verb === "root-pick" && arg !== null) pickModel(ctx, Number(arg));
  else if (verb === "root-inspect") await inspectModel(ctx);
  else if (verb === "root-snapshot") {
    const listed = arg !== null ? rootsState.roots[Number(arg)]?.root : null;
    const target = listed ?? rootsState.pinned;
    if (target) await snapshotRoot(ctx, target.repo_id, target.revision);
  } else if (verb === "root-targets" && arg !== null) {
    const entry = rootsState.roots[Number(arg)];
    if (entry) await inspectTargets(ctx, entry.root);
  } else if (verb === "card-targets" && arg && draft) {
    const entry = findRoot(upstreamRoot(graphOf(draft), arg));
    if (entry?.snapshot_ready) await inspectTargets(ctx, entry.root);
  } else if (verb === "reopen") await enterGraph(ctx);
}

export const view: View = {
  id: "graph", parent: "projects", title: "학습 그래프",
  description: "카드로 학습·평가·자동 선택·병합 단계를 직렬·병렬로 잇고, 저장한 버전을 바로 검사합니다.",
  render: graphPage,
  bind: bindPage,
  capture(ctx) {
    if (ctx.page !== "graph") return;
    captureRootsForm();
    captureBenchmark();
    capturePlanPanel();
    // The card form, the graph settings and the planning inputs apply every edit as it happens (input/change).
  },
  async enter(ctx) { await enterGraph(ctx); },
  leave() { addMenu = false; toolbarNote = null; },
  action: graphAction,
};
