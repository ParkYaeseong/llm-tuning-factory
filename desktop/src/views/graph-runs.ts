// Graph runs (10.7; D10, D11 as replaced by the root decision, D12): the runs list and one run's detail with the
// controller bar of 10.4 on top. Only read-only, DB-only actions are polled (graph_runs_list, graph_run_get,
// controller_status), never at boot, one call at a time through the page's queue (D10). Pause, resume, cancel, tracking
// re-arm and the desktop operator release (9.8's graph_release_lock) happen only on a click; the release sits behind a
// dialog that explains the risk and needs the stop confirmation and the typed node label. Every dynamic text (labels,
// wait reasons, errors, IDs) is escaped: run data is untrusted.
import "./graph-runs.css";
import { MALFORMED, projects as projectApi, releaseLock, runs as runApi } from "../api";
import { attachControllerBar, controllerAction, controllerBarHtml, controllerBarState } from "../components/controller-bar";
import { localize } from "../issues";
import { createPoller, createQueue, type Poller } from "../poller";
import type { View, ViewContext } from "../view-types";
import { button, errorText, escapeHtml, formatDate, openDialog, statusPill } from "../workbench";
import { EVALUATION_STATUS, EVENT_STATUS, localizeRunText, localizeWaitReason, NODE_KIND, NODE_STATUS, RUN_STATUS, TRACKING_KIND,
  TRACKING_STATUS } from "../wait-reasons";
import type { ControllerView, GraphRun, GraphRunNode, RunDetail, RunListEntry, TrackingFailure, UnreadableRun, WorkflowIssue,
  WorkflowResult } from "../workflow-types";

/** 9.8 adds `job_id` to every node (workflow-types.ts stays as it is for this sub-task). */
type RunNode = GraphRunNode & { job_id?: string | null };
type Obj = Record<string, any>;
type Pill = "neutral" | "good" | "warn" | "bad";

const DETAIL_MS = 3000;                                  // D10: graph_run_get every 3 s while one unsettled run is shown
const LIST_MS = 10000;                                   // D10: graph_runs_list every 10 s while the page is shown
const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const STATE_DIR = "%APPDATA%\\kr.kbiofoundry.tuningfactory\\state";
export const NO_WINNER = "모든 후보가 기준을 통과하지 못해 최상 모델을 정하지 않았습니다";
export const SIMULATED = "모의 결과는 실제 품질이 아닙니다";
export const STOP_CONFIRMED = "원격 서버/Pod에서 해당 실행이 멈춘 것을 확인했습니다";
const CANCEL_NOTICE = "진행 중인 단계에 취소를 요청합니다. 원격 종료가 확인될 때까지 자원을 해제하지 않습니다.";
const RELEASE_RISK = "원격 서버나 Pod에서 이 실행이 아직 돌고 있는데 잠금을 해제하면, 같은 GPU에 다른 단계가 배정되어 두 작업이 함께 돌거나 늦게 도착한 결과가 버려질 수 있습니다.";
const RELEASE_EFFECT = "해제하면 컨트롤러가 다음 확인 때 이 시도를 정리합니다. 결과가 없으면 이 단계는 실패로(취소를 요청한 실행의 시도는 취소로 기록) 기록되고 다시 실행하지 않습니다. 결과가 필요하면 다시 계획해 새로 실행하세요.";
const RELEASE_CHECK = "RunPod 콘솔이나 서버의 프로세스 목록에서 아래 작업·시도의 실행이 멈춘 것을 직접 확인한 뒤에만 해제하세요.";
const RELEASED = "잠금이 이미 해제되었습니다 · 컨트롤러가 다음 확인 때 이 시도를 정리합니다. 결과가 없으면 실패로(취소를 요청한 실행이면 취소로) 기록하고 다시 실행하지 않습니다.";
const RELEASED_START = "컨트롤러가 동작 중이 아니어서 아직 정리되지 않습니다. 위에서 컨트롤러를 시작하세요.";
const RELEASE_STALE = "실행 상태가 바뀌었습니다. 이 시도는 더 이상 해제할 대상이 아닙니다. 대화 상자를 닫고 다시 확인하세요.";
const PRE_OUTBOX = "기록 대기열이 생기기 전에 끝난 실행이라 MLflow 기록 상태가 없습니다.";
/** The reasons graph_status gives an uncertain attempt (OPERATOR_CONFIRMATION, RESULT_OVERDUE): with `needs_operator`
 * still true but `uncertain` false, the lock was released (by this app, another session or the CLI) and the
 * controller has not settled the node yet (9.8: until its next tick). */
const RELEASE_REASON = /^(?:operator confirmation required|no result after the launch's time limit)/;
const CLI_NOTE = "설치형 앱에는 factory 명령이 들어 있지 않습니다. 소스로 설치한 운영자 환경에서 같은 상태 폴더를 지정해 실행하세요.";
const TEXT_METRICS = new Set(["exact_match", "character_f1"]);
const FORMAT_METRICS = new Set(["json_validity"]);
const TEXT_METRIC_NOTE = "텍스트 정답 일치 지표 · 의미적 품질 점수가 아닙니다";
const FORMAT_METRIC_NOTE = "형식 지표 · 품질 지표가 아닙니다";
const RUN_PILL: Record<string, Pill> = { RUNNING: "neutral", PAUSED: "warn", CANCELED: "neutral", COMPLETED: "good", FAILED: "bad" };
const NODE_PILL: Record<string, Pill> = { PENDING: "neutral", READY: "neutral", RUNNING: "neutral", COMPLETED: "good", FAILED: "bad", BLOCKED: "bad", CANCELED: "neutral" };
const FINAL_RUN = new Set(["COMPLETED", "FAILED", "CANCELED"]);
const TRACKING_DONE = new Set(["saved", "failed", "uncertain"]);
const TRACKING_MOVING = new Set(["pending", "retrying"]);
const UNFINISHED_NODE = new Set(["PENDING", "READY", "RUNNING"]);

// -- module state (session only; the backend is the truth) --
interface ListState { runs: (RunListEntry | UnreadableRun)[] | null; error: string | null; unavailable: boolean }
interface DetailState { runId: string; data: RunDetail | null; error: string | null; unavailable: boolean }
interface Attached { token: number; ctx: ViewContext; bar: ReturnType<typeof attachControllerBar>; list: Poller; detail: Poller; onVisible(): void }
let listState: ListState = { runs: null, error: null, unavailable: false };
let detailState: DetailState | null = null;
const graphs = new Map<string, { nodes?: unknown[] } | null>();   // run ID → its planned graph (null: generic, read once)
const rearmWarnings = new Map<string, { runId: string; warnings: WorkflowIssue[] }>();
const lastHtml: Record<string, string> = {};
const queue = createQueue();                                       // one sidecar call at a time on this page (D10)
let attached: Attached | null = null;
let session = 0;
/** While the release dialog is open: re-checks the shown node after every detail read (M4). */
let releaseWatch: (() => void) | null = null;

const isRecord = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const isUnreadable = (value: unknown): value is UnreadableRun => isRecord(value) && typeof value.error === "string" && !Array.isArray(value.nodes);
const isRun = (value: unknown): value is GraphRun => isRecord(value) && typeof value.run_id === "string" && typeof value.status === "string"
  && Array.isArray(value.nodes) && value.nodes.every(node => isRecord(node) && typeof node.node_id === "string");
const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const short = (value: unknown, length = 8) => String(value ?? "").slice(0, length);
const usd = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "—";
const number = (value: unknown) => typeof value === "number" ? value.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : String(value ?? "—");
/** An issue's Korean text: the run texts of this page first (steering refusals), then the shared table (issues.ts). */
function localizeIssue(issue: WorkflowIssue): { text: string; original: string | null } {
  const run = localizeRunText(issue.message);
  return run?.original ? run : localize(issue);
}
const describe = (issues: WorkflowIssue[]) => issues.map(issue => {
  const { text, original } = localizeIssue(issue);
  return original ? `${text} (${original})` : text;
}).join("\n") || "요청을 처리하지 못했습니다.";
/** "원문 보기" for an English text shown in Korean; its key keeps it open (and focused) across poll updates. */
const originalDetails = (text: string, key: string) =>
  `<details class="run-original" data-open-key="${escapeHtml(key)}"><summary data-focus-key="${escapeHtml(key)}">원문 보기</summary><span class="mono">${escapeHtml(text)}</span></details>`;
function localizedText(text: string | null, key: string): string {
  const localized = localizeRunText(text);
  if (!localized) return "";
  return `<span>${escapeHtml(localized.text)}</span>${localized.original ? originalDetails(localized.original, key) : ""}`;
}
/** How a node is named on this page: its label, or its ID when the label is blank. */
const nameOf = (node: Pick<GraphRunNode, "label" | "node_id">) => String(node.label ?? "").trim() || node.node_id;
const labelsOf = (run: GraphRun) => (nodeId: string) => { const node = run.nodes.find(item => item.node_id === nodeId); return node ? nameOf(node) : null; };
const keyedButton = (label: string, action: string, key: string, cls = "") =>
  `<button type="button" class="${cls}" data-action="${escapeHtml(action)}" data-focus-key="${escapeHtml(key)}">${escapeHtml(label)}</button>`;

/** D10 settlement: a final run status, graph tracking `saved`/`failed`/`uncertain`, and no node still unfinished,
 * holding an uncertain attempt (a late result or a release may still change it) or with tracking or registration
 * still `pending`/`retrying`. Tracking that failed or became uncertain waits for a re-arm, which follows the run again. */
export function settled(run: GraphRun): boolean {
  return FINAL_RUN.has(run.status) && (TRACKING_DONE.has(run.tracking?.graph) || preOutbox(run)) && run.nodes.every(node =>
    !UNFINISHED_NODE.has(node.status) && !node.uncertain && !TRACKING_MOVING.has(node.tracking_status)
    && !TRACKING_MOVING.has(node.registration_status));
}

/** A final run without graph tracking and without any tracking event: it ended before the Task 8 outbox existed, so
 * nothing will be tracked and the run is settled (the controller does not manage it either). */
export function preOutbox(run: GraphRun): boolean {
  const events = run.tracking?.events;
  return FINAL_RUN.has(run.status) && run.tracking?.graph === "not_started" && isRecord(events)
    && Object.values(events).every(count => count === 0);
}

/** The CLI alternative of the desktop release (escaped text): find the job, then release its attempt. */
export function operatorInstruction(runId: string, node: GraphRunNode): string {
  const job = (node as RunNode).job_id || "<JOB_ID>";
  const attempt = node.attempt_id || "<ATTEMPT_ID>";
  return escapeHtml([
    `1. 원격 서버나 Pod에서 이 단계의 실행(작업 ${job}, 시도 ${attempt})이 멈춘 것을 직접 확인합니다.`,
    `2. factory status로 실행 ${runId}의 작업 상태를 확인합니다:`,
    `   factory --state-dir "${STATE_DIR}" status --job-id ${job}`,
    "3. 멈춘 것을 확인했다면 잠금을 해제합니다:",
    `   factory --state-dir "${STATE_DIR}" release-lock ${job} ${attempt} --confirm-execution-stopped`,
  ].join("\n"));
}

/** The merge nodes whose outputs the select node's evaluations evaluate, read structurally from the planned graph
 * (10.7 does not import graph-model.ts); null when the graph or the select node is unknown. */
function evaluatedMerges(graph: { nodes?: unknown[] } | null, selectId: string): string[] | null {
  const nodes = Array.isArray(graph?.nodes) ? graph.nodes.filter(isRecord) : null;
  if (!nodes) return null;
  const byId = new Map(nodes.map(node => [String(node.id), node]));
  const select = byId.get(selectId);
  if (select?.kind !== "select" || !Array.isArray(select.evaluations)) return null;
  const merges: string[] = [];
  for (const ref of select.evaluations) {
    const evaluation = isRecord(ref) ? byId.get(String(ref.node_id)) : undefined;
    const input = isRecord(evaluation?.model) ? evaluation.model : null;
    const candidate = input && typeof input.node_id === "string" ? byId.get(input.node_id) : undefined;
    if (candidate?.kind === "merge" && !merges.includes(String(candidate.id))) merges.push(String(candidate.id));
  }
  return merges;
}

/** What a selection decided (plain text): no winner, the merge, a kept original, or a generic winner when the planned
 * graph is unknown (§6: derived by comparing the winner with the evaluated merge outputs). Simulated runs say so. */
export function selectionText(run: GraphRun, node: GraphRunNode, graph: { nodes?: unknown[] } | null): string {
  if (node.selection == null) return "";
  const artifactOf = (id: string) => {
    const output: unknown = run.nodes.find(item => item.node_id === id)?.output;
    return isRecord(output) && typeof output.artifact_id === "string" ? output.artifact_id : null;
  };
  let text: string;
  if (node.selection === "no_winner") text = NO_WINNER;
  else {
    const winner = run.nodes.find(item => item.kind !== "select" && artifactOf(item.node_id) === node.selection);
    const name = winner ? `‘${nameOf(winner)}’` : `모델 ${short(node.selection)}`;
    const merges = evaluatedMerges(graph, node.node_id);
    if (merges === null) text = `선택된 모델: ${name}`;
    else if (!merges.length) text = `최상 모델: ${name}`;
    else if (merges.some(id => artifactOf(id) === node.selection)) text = `병합 모델 선택: ${name}`;
    else if (merges.every(id => artifactOf(id) !== null)) text = `원본 최상 모델 유지: ${name}`;
    else text = `선택된 모델: ${name}`;
  }
  return run.simulation ? `${text} · ${SIMULATED}` : text;
}

/** The desktop release applies only to an uncertain attempt that needs the operator and no late result can settle
 * (9.8: `needs_operator && uncertain && !awaiting_result`), with the IDs the backend checks. */
const releasable = (node: RunNode) => node.needs_operator && node.uncertain && !node.awaiting_result && Boolean(node.job_id) && Boolean(node.attempt_id);

// -- rendering --
function listInner(): string {
  const runs = listState.runs;
  const count = runs ? `<span class="count">${runs.length}개</span>` : "";
  const head = `<div class="section-title"><h3 id="run-list-title">실행 목록</h3>${count}</div>`;
  if (listState.unavailable) return `${head}<p class="muted">${NOT_AVAILABLE}</p>`;
  const error = listState.error ? `<div class="wf-banner bad run-read-error"><div><p>실행 목록을 읽지 못했습니다. 자동으로 다시 확인합니다.</p><small>${escapeHtml(listState.error)}</small></div></div>` : "";
  if (!runs) return `${head}${error}${error ? "" : '<p class="muted">실행 목록을 불러오는 중입니다.</p>'}`;
  if (!runs.length) return `${head}${error}<div class="empty">아직 그래프 실행이 없습니다. 프로젝트의 그래프 화면에서 계획을 승인하면 여기에 표시됩니다.</div>`;
  const current = detailState?.runId ?? null;
  const rows = [...runs].reverse().map(entry => {
    const id = String(entry.run_id ?? "");
    const mark = id === current ? ' class="current" aria-current="true"' : "";
    if (isUnreadable(entry)) {
      const localized = localizeRunText(entry.error)!;
      return `<tr data-run-id="${escapeHtml(id)}"${mark}><td><b>읽을 수 없는 실행</b><small class="mono">${escapeHtml(short(id))}</small></td>
        <td colspan="4"><span>${escapeHtml(localized.text)}</span>${originalDetails(entry.error, `list-error:${id}`)}</td></tr>`;
    }
    const counts = isRecord(entry.nodes_by_status) ? Object.entries(entry.nodes_by_status)
      .map(([status, n]) => `${NODE_STATUS[status as keyof typeof NODE_STATUS] ?? status} ${number(n)}`).join(" · ") : "";
    return `<tr data-run-id="${escapeHtml(id)}"${mark}><td><b class="mono">${escapeHtml(short(id))}</b><small>${entry.simulation ? "모의 실행" : "실제 실행"} · 계획 <span class="mono">${escapeHtml(short(entry.plan_sha256))}</span></small></td>
      <td>${statusPill(RUN_PILL[entry.status] ?? "neutral", RUN_STATUS[entry.status] ?? String(entry.status))}</td>
      <td class="run-counts">${escapeHtml(counts) || "—"}</td><td>${escapeHtml(formatDate(entry.created))}</td>
      <td class="run-row-actions">${button("열기", `nav:runs:${id}`)}</td></tr>`;
  }).join("");
  return `${head}${error}<div class="table-scroll"><table><thead><tr><th>실행</th><th>상태</th><th>단계</th><th>만든 시각</th><th><span class="sr-only">동작</span></th></tr></thead><tbody>${rows}</tbody></table></div>`;
}

function detailHeading(runId: string, state = ""): string {
  return `<h3 id="run-detail-title" tabindex="-1">실행 <span class="mono">${escapeHtml(short(runId))}</span></h3>${state}`;
}

function steerButtons(run: GraphRun): string {
  if (run.status === "RUNNING") return keyedButton("일시정지", "wf:runs:pause", "run-steer") + keyedButton("취소", "wf:runs:cancel", "run-cancel", "danger");
  if (run.status === "PAUSED") return keyedButton("다시 진행", "wf:runs:resume", "run-steer") + keyedButton("취소", "wf:runs:cancel", "run-cancel", "danger");
  return "";
}

function reasonHtml(run: GraphRun, node: GraphRunNode, scope: "table" | "operator"): string {
  const localized = localizeWaitReason(node.wait_reason, labelsOf(run));
  if (!localized) return "";
  const original = localized.text !== localized.original ? originalDetails(localized.original, `reason:${scope}:${node.node_id}`) : "";
  return `<div class="run-reason ${localized.kind}" data-reason="${localized.kind}"><span>${escapeHtml(localized.text)}</span>${original}</div>`;
}

function progressHtml(run: GraphRun, node: RunNode): string {
  const parts: string[] = [];
  if (node.awaiting_result) parts.push(statusPill("neutral", "결과 대기"));
  else if (node.needs_operator) parts.push(statusPill("warn", "운영자 확인 필요"));
  parts.push(reasonHtml(run, node, "table"));
  if (node.error) parts.push(`<p class="run-node-error">오류: <span class="mono">${escapeHtml(node.error)}</span></p>`);
  if (typeof node.retry_at === "number") parts.push(`<small>다시 시도: ${escapeHtml(formatDate(node.retry_at))}</small>`);
  return parts.filter(Boolean).join("") || "—";
}

function trackingCell(node: GraphRunNode): string {
  const parts = [`<span>${escapeHtml(TRACKING_STATUS[node.tracking_status] ?? node.tracking_status)}</span>`];
  if (node.registration_status && node.registration_status !== "none")
    parts.push(`<small>모델 등록: ${escapeHtml(TRACKING_STATUS[node.registration_status] ?? node.registration_status)}</small>`);
  if (node.tracking_action) parts.push(`<div class="run-node-action">${localizedText(node.tracking_action, `action:${node.node_id}`)}</div>`);
  return parts.join("");
}

function outputHtml(run: GraphRun, node: GraphRunNode): string {
  const parts: string[] = [];
  const output = node.output as Obj | null;
  if (isRecord(output) && typeof output.artifact_id === "string")
    parts.push(`<span>모델 <span class="mono" title="${escapeHtml(output.artifact_id)}">${escapeHtml(short(output.artifact_id))}</span></span>`);
  else if (isRecord(output) && "evaluation_status" in output) {
    const metrics = isRecord(output.metrics) ? Object.entries(output.metrics) : [];
    const status = EVALUATION_STATUS[String(output.evaluation_status)] ?? String(output.evaluation_status ?? "");
    parts.push(`<span>평가 ${escapeHtml(status)}</span>`);
    if (metrics.length) parts.push(`<ul class="run-metrics">${metrics.map(([name, value]) => `<li><span class="mono">${escapeHtml(name)}</span> ${escapeHtml(number(value))}</li>`).join("")}</ul>`);
    if (metrics.some(([name]) => TEXT_METRICS.has(name))) parts.push(`<small class="run-metric-note">${TEXT_METRIC_NOTE}</small>`);
    if (metrics.some(([name]) => FORMAT_METRICS.has(name))) parts.push(`<small class="run-metric-note">${FORMAT_METRIC_NOTE}</small>`);
    if (run.simulation) parts.push(`<small class="run-metric-note">${SIMULATED}</small>`);
  }
  const selection = selectionText(run, node, graphs.get(run.run_id) ?? null);
  if (selection) parts.push(`<p class="run-selection">${escapeHtml(selection)}</p>`);
  if (node.cache_seed === "missing" || node.cache_seed === "failed")
    parts.push(`<small>${node.cache_seed === "failed" ? "모델 캐시를 올리지 못했습니다" : "모델 캐시 기록이 없습니다"} · 다음 실행 시 다시 올립니다</small>`);
  return parts.join("") || "—";
}

function nodeRows(run: GraphRun): string {
  return (run.nodes as RunNode[]).map(node => {
    const attempts = `${number(node.counted_attempts)}/${number(node.max_attempts)}`
      + (node.attempts !== node.counted_attempts ? `<small>전체 시도 ${escapeHtml(number(node.attempts))}회</small>` : "");
    const destination = node.destination_profile_id || node.resource_id
      ? `${node.destination_profile_id ? `<b>${escapeHtml(node.destination_profile_id)}</b>` : ""}${node.resource_id ? `<small class="mono">${escapeHtml(node.resource_id)}</small>` : ""}` : "—";
    return `<tr data-node-id="${escapeHtml(node.node_id)}"><td class="run-node-label"><b>${escapeHtml(nameOf(node))}</b><small>${escapeHtml(NODE_KIND[node.kind] ?? node.kind)} · <span class="mono">${escapeHtml(node.node_id)}</span></small></td>
      <td class="run-node-status">${statusPill(NODE_PILL[node.status] ?? "neutral", NODE_STATUS[node.status] ?? String(node.status))}</td>
      <td class="run-node-attempts">${attempts}</td><td class="run-node-destination">${destination}</td>
      <td class="run-node-progress">${progressHtml(run, node)}</td><td class="run-node-tracking">${trackingCell(node)}</td>
      <td class="run-node-output">${outputHtml(run, node)}</td></tr>`;
  }).join("");
}

function operatorItem(run: GraphRun, node: RunNode, controller: ControllerView | null): string {
  const head = (pill: string) => `<div class="run-operator-head"><b>${escapeHtml(nameOf(node))}</b>${pill}</div>`;
  const attrs = `class="run-operator-item" data-operator-node="${escapeHtml(node.node_id)}"`;
  if (node.awaiting_result) {
    const until = typeof node.result_deadline === "number" ? `${formatDate(node.result_deadline)}까지는` : "시간 제한이 지나기 전에는";
    return `<article ${attrs}>${head(statusPill("neutral", "결과 대기"))}
      <p>결과 대기 · ${escapeHtml(until)} 다시 실행하지 않습니다. 같은 실행의 결과가 늦게 도착할 수 있어 실행 자원 잠금을 그대로 둡니다.</p></article>`;
  }
  const reason = reasonHtml(run, node, "operator");
  if (!node.uncertain && RELEASE_REASON.test(node.wait_reason ?? ""))
    return `<article ${attrs}>${head(statusPill("neutral", "잠금 해제됨"))}<p>${escapeHtml(RELEASED)}</p>${controller?.state === "running" ? "" : `<p>${escapeHtml(RELEASED_START)}</p>`}</article>`;
  const cli = `<details class="run-cli-details" data-open-key="cli:${escapeHtml(node.node_id)}"><summary data-focus-key="cli:${escapeHtml(node.node_id)}">명령줄로 해제하기(대안)</summary><p class="muted">${CLI_NOTE}</p><pre class="run-cli">${operatorInstruction(run.run_id, node)}</pre></details>`;
  if (releasable(node)) {
    return `<article ${attrs}>${head(statusPill("warn", "운영자 확인 필요"))}${reason}
      <p>원격 실행이 멈췄는지 알 수 없어 이 단계의 실행 자원 잠금을 유지하고 있습니다. 원격 서버나 Pod에서 실행이 멈춘 것을 확인한 뒤에만 잠금을 해제하세요.</p>
      <div class="actions">${keyedButton("실행 잠금 해제", `wf:runs:release:${node.node_id}`, `release:${node.node_id}`)}</div>${cli}</article>`;
  }
  const job = node.job_id || "<JOB_ID>";
  return `<article ${attrs}>${head(statusPill("warn", "운영자 확인 필요"))}${reason}
    <p>이 단계는 데스크톱에서 해결할 수 없습니다. 잠금 해제로 풀리는 상태가 아니므로(결과 기록 실패나 작업 기록 검사 실패 등) 운영자가 서버의 기록을 확인하고 복구해야 합니다.</p>
    <pre class="run-cli">${escapeHtml(`factory status로 작업 상태를 확인합니다:\nfactory --state-dir "${STATE_DIR}" status --job-id ${job}`)}</pre></article>`;
}

function operatorSection(run: GraphRun, controller: ControllerView | null): string {
  const nodes = (run.nodes as RunNode[]).filter(node => node.awaiting_result || node.needs_operator);
  if (!nodes.length) return "";
  return `<section class="run-operator" aria-labelledby="run-operator-title"><h4 id="run-operator-title">확인이 필요한 단계</h4>${nodes.map(node => operatorItem(run, node, controller)).join("")}</section>`;
}

function trackingSection(run: GraphRun, failures: TrackingFailure[]): string {
  const warnings = [...rearmWarnings].filter(([, note]) => note.runId === run.run_id);
  if (!failures.length && !warnings.length) return "";
  const labelOf = (nodeId: string | null) => nodeId ? labelsOf(run)(nodeId) ?? nodeId : "그래프 전체";
  const items = failures.map(item => `<li class="run-tracking-failure" data-event-id="${escapeHtml(item.event_id)}">
    <div class="run-tracking-head"><b>${escapeHtml(TRACKING_KIND[item.kind] ?? item.kind)}</b><span>${escapeHtml(labelOf(item.node_id))}</span>${statusPill(item.status === "FAILED" ? "bad" : "warn", EVENT_STATUS[item.status] ?? String(item.status))}<span class="muted">${escapeHtml(number(item.tries))}회 시도</span></div>
    ${item.last_error ? `<div class="run-tracking-error">마지막 오류: ${localizedText(item.last_error, `failure-error:${item.event_id}`)}</div>` : ""}
    ${item.action ? `<div class="wf-banner warn run-tracking-action"><div>${localizedText(item.action, `failure-action:${item.event_id}`)}</div></div>` : ""}
    <div class="actions">${button("기록 다시 시도", `wf:runs:rearm:${item.event_id}`)}</div></li>`).join("");
  const notes = warnings.flatMap(([eventId, note]) => note.warnings.map((warning, index) => {
    const { text, original } = localizeIssue(warning);
    return `<div class="wf-banner warn run-rearm-warning"><div><p>다시 시도를 예약했지만 확인이 필요합니다: ${escapeHtml(text)}</p>${original ? originalDetails(original, `rearm:${eventId}:${index}`) : ""}</div></div>`;
  })).join("");
  return `<section class="run-tracking" aria-labelledby="run-tracking-title"><h4 id="run-tracking-title">MLflow 기록 문제</h4>${notes}${items ? `<ul class="run-tracking-list">${items}</ul>` : ""}</section>`;
}

function detailInner(): string {
  const state = detailState;
  if (!state) return "";
  const back = `<div class="actions run-back">${button("목록으로", "nav:runs", "quiet")}</div>`;
  if (state.unavailable) return `${detailHeading(state.runId)}<p class="muted">${NOT_AVAILABLE}</p>${back}`;
  const readError = state.error ? `<div class="wf-banner bad run-read-error"><div><p>${state.data ? "실행 상태를 새로 읽지 못했습니다. 아래는 마지막으로 읽은 상태이며 자동으로 다시 확인합니다." : "실행 정보를 읽지 못했습니다. 자동으로 다시 확인합니다."}</p><small>${escapeHtml(state.error)}</small></div></div>` : "";
  if (!state.data) return `${detailHeading(state.runId)}${readError || '<p class="muted">실행 정보를 불러오는 중입니다.</p>'}${back}`;
  const run = state.data.run;
  if (!isRun(run)) {
    const error = isUnreadable(run) ? run.error : MALFORMED;
    return `${detailHeading(state.runId)}${readError}<div class="wf-banner bad"><div><p><b>읽을 수 없는 실행</b> · ${escapeHtml(localizeRunText(error)!.text)}</p>${localizeRunText(error)!.original ? originalDetails(error, "detail-error") : ""}</div></div>${back}`;
  }
  const status = `<div class="run-state" role="status">${statusPill(RUN_PILL[run.status] ?? "neutral", RUN_STATUS[run.status] ?? String(run.status))}${statusPill("neutral", run.simulation ? "모의 실행" : "실제 실행")}</div>`;
  const banners: string[] = [];
  if (run.budget_exhausted)
    banners.push(`<div class="wf-banner warn"><div><p>${escapeHtml(`승인 예산 소진: 예약 ${usd(run.reserved_cost_usd)} / 승인 ${usd(run.approved_cost_usd)} — 더 큰 예산으로 다시 계획하세요`)}</p></div></div>`);
  if (run.unpriced_attempts > 0)
    banners.push(`<div class="wf-banner warn"><div><p>${escapeHtml(`가격을 알 수 없는 이전 시도 ${number(run.unpriced_attempts)}개가 승인 예산 전체를 예약했습니다`)}</p></div></div>`);
  if (run.simulation) banners.push(`<div class="wf-banner run-note"><div><p>모의 실행 · 원격 GPU와 MLflow를 쓰지 않습니다. ${SIMULATED}.</p></div></div>`);
  const tracking = run.tracking ?? { graph: "not_started", action: null, events: {} };
  const meta = `<dl class="run-meta">
    <div><dt>계획</dt><dd class="mono" title="${escapeHtml(run.plan_sha256)}">${escapeHtml(short(run.plan_sha256, 12))}</dd></div>
    <div><dt>만든 시각</dt><dd>${escapeHtml(formatDate(run.created))}</dd></div>
    <div><dt>승인 예산</dt><dd>${escapeHtml(usd(run.approved_cost_usd))}</dd></div>
    <div><dt>예약된 비용</dt><dd>${escapeHtml(usd(run.reserved_cost_usd))}</dd></div>
    <div><dt>MLflow 기록</dt><dd>${escapeHtml(TRACKING_STATUS[tracking.graph] ?? String(tracking.graph))}${preOutbox(run) ? `<div class="run-graph-note">${escapeHtml(PRE_OUTBOX)}</div>` : ""}${tracking.action ? `<div class="run-graph-action">${localizedText(tracking.action, "graph-action")}</div>` : ""}</dd></div></dl>`;
  const table = `<div class="table-scroll"><table class="run-nodes"><thead><tr><th>단계</th><th>상태</th><th>시도</th><th>실행 위치</th><th>진행 정보</th><th>기록</th><th>결과</th></tr></thead><tbody>${nodeRows(run)}</tbody></table></div>`;
  return `<div class="run-head"><div>${detailHeading(run.run_id, status)}</div><div class="actions">${steerButtons(run)}${button("목록으로", "nav:runs", "quiet")}</div></div>
    ${readError}${banners.join("")}${meta}${operatorSection(run, isRecord(state.data.controller) ? state.data.controller : null)}${table}${trackingSection(run, Array.isArray(state.data.tracking_failures) ? state.data.tracking_failures : [])}`;
}

/** Patches a region only when its content changed (D10), keeping the open "원문 보기" and CLI details open. */
function refreshRegion(ctx: ViewContext, regionId: "run-list" | "run-detail") {
  const html = regionId === "run-list" ? listInner() : detailInner();
  if (html === lastHtml[regionId]) return;
  lastHtml[regionId] = html;
  const region = document.getElementById(regionId);
  const open = region ? [...region.querySelectorAll<HTMLDetailsElement>("details[open][data-open-key]")].map(item => item.dataset.openKey!) : [];
  ctx.patch(regionId, html);
  const after = document.getElementById(regionId);
  for (const key of open) after?.querySelector<HTMLDetailsElement>(`details[data-open-key="${CSS.escape(key)}"]`)?.setAttribute("open", "");
}

// -- reads (through the page's queue; a result for a page left meanwhile is dropped) --
async function readList(token: number): Promise<"ok" | "failed" | "unavailable"> {
  try {
    const result = await queue.run(() => runApi.list());
    if (token !== session) return "ok";
    if (result.status === "ok" && isRecord(result.data) && Array.isArray(result.data.runs)) {
      listState = { runs: result.data.runs.filter(isRecord) as (RunListEntry | UnreadableRun)[], error: null, unavailable: false };
      return "ok";
    }
    if (notAvailable(result)) { listState = { runs: null, error: null, unavailable: true }; return "unavailable"; }
    listState = { ...listState, error: result.status === "ok" ? MALFORMED : describe(result.issues) };
  } catch (error) { if (token === session) listState = { ...listState, error: errorText(error) }; }
  return "failed";
}

async function readDetail(token: number, runId: string): Promise<"ok" | "failed" | "unavailable"> {
  try {
    const result = await queue.run(() => runApi.get(runId));
    if (token !== session || detailState?.runId !== runId) return "ok";
    const data = result.data as Obj | null;
    // An answer about another run is never shown as this one.
    if (result.status === "ok" && isRecord(data) && (isRun(data.run) || isUnreadable(data.run)) && data.run.run_id === runId) {
      detailState = { runId, data: data as RunDetail, error: null, unavailable: false };
      return "ok";
    }
    if (notAvailable(result)) { detailState = { runId, data: null, error: null, unavailable: true }; return "unavailable"; }
    detailState = { ...detailState, error: result.status === "ok" ? MALFORMED : describe(result.issues) };
  } catch (error) { if (token === session && detailState?.runId === runId) detailState = { ...detailState, error: errorText(error) }; }
  return "failed";
}

/** Once per opened run: the planned graph (`project_get` at the plan's project revision) for the merge relation of the
 * selection texts. A failure leaves those texts generic. */
async function ensureGraph(token: number, runId: string) {
  const plan = detailState?.runId === runId ? detailState.data?.plan : null;
  if (graphs.has(runId) || !isRecord(plan) || typeof plan.project_id !== "string" || typeof plan.project_revision !== "string") return;
  graphs.set(runId, null);
  try {
    const result = await queue.run(() => projectApi.get(plan.project_id, plan.project_revision));
    if (token === session && result.status === "ok" && isRecord(result.data?.project?.graph)) graphs.set(runId, result.data!.project.graph as { nodes?: unknown[] });
  } catch { /* generic selection texts */ }
}

const shownRun = (): GraphRun | null => isRun(detailState?.data?.run) ? detailState!.data!.run as GraphRun : null;
const shownSettled = () => { const run = shownRun(); return run !== null && settled(run); };

/** Shows a steering or release answer `{run, controller}` at once. */
function applyRun(runId: string, data: { run: GraphRun | UnreadableRun; controller: ControllerView }) {
  if (detailState?.runId !== runId || !detailState.data || !(isRun(data.run) || isUnreadable(data.run)) || data.run.run_id !== runId) return;
  detailState = { ...detailState, data: { ...detailState.data, run: data.run, controller: data.controller ?? detailState.data.controller }, error: null };
}

// -- polling (D10) --
function detach() {
  if (!attached) return;
  attached.bar.detach(); attached.list.stop(); attached.detail.stop();
  document.removeEventListener("visibilitychange", attached.onVisible);
  attached = null;
  session++;
}

function attach(ctx: ViewContext): Attached {
  detach();
  const token = ++session;
  const shown = () => attached?.token === token && ctx.page === "runs" && document.visibilityState === "visible" && !ctx.busy;
  const handle: Attached = {
    token, ctx, bar: attachControllerBar(ctx, { host: "runs", queue }),
    list: createPoller({ baseMs: LIST_MS, active: shown, async tick() {
      const outcome = await readList(token);
      if (attached?.token !== token) return "stop";
      refreshRegion(ctx, "run-list");
      if (outcome === "unavailable") return "stop";
      if (outcome === "failed") throw new Error("graph runs not read");
      return "continue";
    } }),
    detail: createPoller({ baseMs: DETAIL_MS, active: () => shown() && detailState !== null && ctx.arg === detailState.runId, async tick() {
      const runId = detailState?.runId;
      if (!runId) return "stop";
      const outcome = await readDetail(token, runId);
      if (attached?.token !== token || ctx.arg !== runId) return "stop";
      await ensureGraph(token, runId);
      refreshRegion(ctx, "run-detail");
      releaseWatch?.();
      if (outcome === "unavailable") return "stop";
      if (outcome === "failed") throw new Error("graph run not read");
      return shownSettled() ? "stop" : "continue";
    } }),
    onVisible() { if (document.visibilityState === "visible") { handle.list.poke(); handle.detail.poke(); } },
  };
  document.addEventListener("visibilitychange", handle.onVisible);
  attached = handle;
  return handle;
}

/** After a click changed the run: follow it again until it settles (a re-arm or a release can unsettle it). */
function follow() {
  if (attached && detailState && !detailState.unavailable && !shownSettled()) attached.detail.start();
}

function focusTitle() {
  if (!document.activeElement || document.activeElement === document.body) document.getElementById("run-detail-title")?.focus();
}

// -- actions (every mutation follows a click) --
const STEER = {
  pause: { label: "실행 일시정지 요청 중", call: runApi.pause, done: "일시정지했습니다. 이미 실행 중인 단계는 끝날 때까지 계속되고 새 단계는 시작하지 않습니다." },
  resume: { label: "실행 다시 진행 요청 중", call: runApi.resume, done: "다시 진행합니다. 컨트롤러가 다음 확인 때 대기 중인 단계를 배정합니다." },
  cancel: { label: "실행 취소 요청 중", call: runApi.cancel, done: "취소를 요청했습니다. 진행 중인 단계는 원격 종료가 확인된 뒤에 자원을 해제합니다." },
} as const;

async function steer(ctx: ViewContext, verb: keyof typeof STEER, runId: string) {
  const { label, call, done } = STEER[verb];
  await ctx.task(label, async () => {
    const result = await queue.run(() => call(runId));
    if (result.status === "ok" && isRecord(result.data) && (isRun(result.data.run) || isUnreadable(result.data.run))) {
      applyRun(runId, result.data);
      ctx.notify(done);
      return;
    }
    // D5: a run_state conflict (or any refusal) reads the run again, so the page shows what the backend has.
    if (!notAvailable(result)) await readDetail(session, runId);
    throw new Error(describe(result.issues));
  });
  follow();
}

async function cancelRun(ctx: ViewContext, runId: string) {
  const answer = await openDialog({ title: "실행 취소",
    bodyHtml: `<p>${escapeHtml(CANCEL_NOTICE)}</p><p>취소한 실행은 다시 진행할 수 없습니다.</p>`,
    actions: [{ id: "back", label: "돌아가기" }, { id: "cancel", label: "취소 요청", primary: true }] });
  if (answer?.action === "cancel") await steer(ctx, "cancel", runId);
}

async function rearm(ctx: ViewContext, eventId: string, runId: string) {
  await ctx.task("기록 다시 시도 예약 중", async () => {
    const result = await queue.run(() => runApi.rearm(eventId));
    if (result.status !== "ok" || !isRecord(result.data)) {
      if (!notAvailable(result)) await readDetail(session, runId);
      throw new Error(describe(result.issues));
    }
    const warnings = Array.isArray(result.data.warnings) ? result.data.warnings : [];
    if (warnings.length) rearmWarnings.set(eventId, { runId, warnings });
    ctx.notify(warnings.length ? "기록을 다시 시도하도록 예약했습니다. 아래 경고를 확인하세요."
      : "기록을 다시 시도하도록 예약했습니다. 컨트롤러가 다음 확인 때 다시 보냅니다.");
    await readDetail(session, runId);
  });
  follow();
}

const normalized = (text: string) => text.trim().normalize("NFC");

/** The desktop operator release (root decision, 9.8): a dialog that explains the risk, shows the CLI alternative and
 * enables its button only while "멈춘 것을 확인했습니다" is ticked and the node label is typed exactly. It sends the IDs
 * the dialog showed; the backend refuses anything but that node's current attempt in that state. */
async function releaseNode(ctx: ViewContext, nodeId: string) {
  const run = shownRun();
  const node = run?.nodes.find(item => item.node_id === nodeId) as RunNode | undefined;
  if (!run || !node || !releasable(node) || document.querySelector("dialog[open]")) return;
  const expected = normalized(nameOf(node));
  const request = { run_id: run.run_id, node_id: node.node_id, job_id: node.job_id!, attempt_id: node.attempt_id! };
  const pending = openDialog({ title: "실행 잠금 해제",
    bodyHtml: `<p class="run-release-target"><b>${escapeHtml(nameOf(node))}</b> · 작업 <span class="mono">${escapeHtml(request.job_id)}</span> · 시도 <span class="mono">${escapeHtml(request.attempt_id)}</span></p>
      <p class="run-release-risk">${escapeHtml(RELEASE_RISK)}</p><p>${escapeHtml(RELEASE_EFFECT)}</p><p>${escapeHtml(RELEASE_CHECK)}</p>
      <details class="run-cli-details"><summary>명령줄로 해제하기(대안)</summary><p class="muted">${CLI_NOTE}</p><pre class="run-cli">${operatorInstruction(run.run_id, node)}</pre></details>
      <label class="field run-release-label"><span>확인을 위해 단계 이름 ‘${escapeHtml(expected)}’을(를) 그대로 입력하세요</span><input id="run-release-label" type="text" autocomplete="off" spellcheck="false"></label>
      <p id="run-release-stale" class="run-release-stale"></p>`,
    checks: [{ id: "stopped", label: STOP_CONFIRMED }],
    actions: [{ id: "back", label: "돌아가기" }, { id: "release", label: "잠금 해제", primary: true, requires: ["stopped"] }] });
  // openDialog gates the button on the check; this gate adds the typed label (its listeners run after openDialog's) and
  // closes for good once a poll shows that the node, its job or its attempt changed (M4).
  let typed = "", stale = false;
  const input = document.getElementById("run-release-label") as HTMLInputElement | null;
  const dialog = input?.closest("dialog");
  if (input && dialog) {
    const check = dialog.querySelector<HTMLInputElement>('[data-dialog-check="stopped"]');
    const confirm = dialog.querySelector<HTMLButtonElement>('[data-dialog-action="release"]');
    const note = dialog.querySelector<HTMLElement>("#run-release-stale");
    const gate = () => {
      typed = input.value;
      if (confirm) confirm.disabled = stale || !(check?.checked && normalized(typed) === expected);
    };
    for (const type of ["input", "change", "compositionend"]) input.addEventListener(type, gate);
    check?.addEventListener("change", gate);
    gate();
    releaseWatch = () => {
      const now = shownRun()?.nodes.find(item => item.node_id === request.node_id) as RunNode | undefined;
      if (stale || (now && releasable(now) && now.job_id === request.job_id && now.attempt_id === request.attempt_id)) return;
      stale = true;
      if (note) note.textContent = RELEASE_STALE;
      gate();
    };
  }
  const answer = await pending;
  releaseWatch = null;
  if (stale || answer?.action !== "release" || !answer.checked.includes("stopped") || normalized(typed) !== expected) return;
  await ctx.task("실행 잠금 해제 중", async () => {
    const result = await queue.run(() => releaseLock({ ...request, confirm_execution_stopped: true }));
    if (result.status === "ok" && isRecord(result.data) && (isRun(result.data.run) || isUnreadable(result.data.run))) {
      applyRun(request.run_id, result.data);
      ctx.notify("잠금을 해제했습니다. 컨트롤러가 다음 확인 때 이 시도를 정리하고(실패 또는 취소로 기록) 다시 실행하지 않습니다.");
      return;
    }
    // A stale attempt, a late result or a released lock: read the run again and say why (Korean backend texts).
    if (!notAvailable(result)) await readDetail(session, request.run_id);
    throw new Error(describe(result.issues));
  });
  follow();
  focusTitle();
}

export const view: View = {
  id: "runs", title: "그래프 실행", description: "승인한 그래프 실행의 진행 상황을 확인하고 일시정지·취소·기록 재시도를 요청합니다.",
  nav: { group: "workspace", label: "그래프 실행", icon: "runs", order: 50 },
  render(ctx) {
    lastHtml["run-list"] = listInner();
    lastHtml["run-detail"] = detailInner();
    const detail = detailState ? `<section id="run-detail" class="card run-detail" aria-labelledby="run-detail-title">${lastHtml["run-detail"]}</section>` : "";
    return `${controllerBarHtml(controllerBarState(), ctx.busy, "runs")}<div class="runs-layout">${detail}
      <section id="run-list" class="card run-list" data-tour="graph-runs" aria-labelledby="run-list-title">${lastHtml["run-list"]}</section></div>`;
  },
  async enter(ctx) {
    const runId = ctx.arg;
    // Re-arm warnings stay with their run: opening another run drops those of the others.
    if (runId) for (const [eventId, note] of rearmWarnings) if (note.runId !== runId) rearmWarnings.delete(eventId);
    if (!runId) detailState = null;
    else if (detailState?.runId !== runId) detailState = { runId, data: null, error: null, unavailable: false };
    const handle = attach(ctx);
    // Read-only only (D3): the runs list, the shown run (and its planned graph once) and the controller status.
    await ctx.task(runId ? "그래프 실행 불러오는 중" : "그래프 실행 목록 불러오는 중", async () => {
      await readList(handle.token);
      if (runId) { await readDetail(handle.token, runId); await ensureGraph(handle.token, runId); }
      await handle.bar.refresh();
    });
    if (attached !== handle || ctx.page !== "runs" || ctx.arg !== runId) return;
    handle.bar.poller.start();
    if (!listState.unavailable) handle.list.start();
    if (runId) { follow(); focusTitle(); }
  },
  leave() { detach(); },
  async action(ctx, verb, arg) {
    if (await controllerAction(ctx, verb)) return;
    const runId = shownRun()?.run_id;
    if ((verb === "pause" || verb === "resume") && runId) await steer(ctx, verb, runId);
    else if (verb === "cancel" && runId) await cancelRun(ctx, runId);
    else if (verb === "release" && arg) await releaseNode(ctx, arg);
    else if (verb === "rearm" && arg && runId) await rearm(ctx, arg, runId);
  },
};
