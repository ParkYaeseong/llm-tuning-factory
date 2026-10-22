// The background controller bar (10.4; D9, D10): the controller's state from `controller_status`, start and stop with
// their explanations, and what a restart would change. Shown on the executor profiles page (and on 10.7's runs page).
// It never starts, stops or restarts anything by itself: polling only reads, and every mutation follows a click.
import { controllerStatus, startController, stopController } from "../api";
import { localize } from "../issues";
import { createPoller, createQueue, type Poller } from "../poller";
import type { ViewContext } from "../view-types";
import { button, errorText, escapeHtml, formatDate, openDialog, statusPill } from "../workbench";
import type { ControllerStatus, PublicProfile, WorkflowIssue, WorkflowResult } from "../workflow-types";

export const RESTART_PROCEDURE = "일시정지 → 진행 중 단계 완료 대기(또는 컨트롤러 정지) → 설정 저장 → 컨트롤러 시작";
export const DRAIN_NOTICE = "진행 중인 단계가 끝날 때까지 기다린 뒤 정지합니다. 학습 중이면 몇 시간이 걸릴 수 있습니다. 앱을 닫아도 컨트롤러는 계속 동작합니다.";
export const REDIRECTED_APPDATA = "앱 저장 폴더가 네트워크 경로(리디렉션된 AppData)에 있으면 백그라운드 컨트롤러를 시작할 수 없습니다. 단일 학습 기능은 계속 사용할 수 있습니다.";
/** broker.rs `ROOT_UNUSABLE`: the app root cannot be handed to the broker (redirected AppData). */
const ROOT_UNUSABLE = "앱 저장 폴더 경로를 백그라운드 실행기에 전달할 수 없습니다.";
const NOT_AVAILABLE = "컨트롤러 기능이 아직 설치되지 않았습니다";
const STATUS_BUSY = "컨트롤러 상태를 지금 읽을 수 없습니다. 작업 기록이 사용 중입니다.";
const BASE_MS = 5000;                                    // D10: controller_status every 5 s while a page shows the bar
// controller_entry.provider_profiles: why the running controller left a profile out at its start.
const SKIP_REASONS: Record<string, string> = {
  credential_missing: "RunPod API 키 없음", observation_missing: "자원 확인 기록 없음",
  observation_revision_differs: "확인 후 프로필이 바뀜", observation_invalid: "확인 기록 오류", invalid_fields: "프로필 항목 오류",
};

export interface ControllerBarState {
  status: ControllerStatus | null; unavailable: boolean; error: string | null;
  /** `status_busy`: shown as a transient note while the last known status stays and the poller backs off. */
  busyRead?: string | null;
  /** The host page's native profiles (public fields and `*_saved` flags), when it has them: a RunPod key saved for a
   * profile the controller skipped as `credential_missing` is invisible to a poll, so the bar compares them. */
  profiles?: readonly PublicProfile[] | null;
  /** Public settings with their `*_saved` flags: an explicit MLflow auth mode whose credential was never saved reads
   * as a missing credential, not as a restart (9.4 review notes). */
  settings?: Readonly<Record<string, unknown>>;
}

type Read = { status: ControllerStatus | null; unavailable: boolean; error: string | null; busyRead: string | null };
type Queue = ReturnType<typeof createQueue>;
interface Handle {
  ctx: ViewContext; host: string; queue: Queue; profiles: () => readonly PublicProfile[] | null;
  poller: Poller; detached: boolean; read(): Promise<"ok" | "failed" | "unavailable">; detach(): void;
}

// The last status read of the attached bar. It is cleared on attach and on detach: a bar shown again claims nothing
// until its own first read answers (never a cached "동작 중" for a controller that may have stopped meanwhile).
const NONE: Read = { status: null, unavailable: false, error: null, busyRead: null };
let last: Read = NONE;
let active: Handle | null = null;

const isRecord = (value: unknown): value is Record<string, unknown> => typeof value === "object" && value !== null && !Array.isArray(value);
/** The Korean texts of workflow issues, each with its original when it differs (one line each). */
export function issueText(issues: WorkflowIssue[], fallback = "요청을 처리하지 못했습니다."): string {
  return issues.map(issue => {
    const { text, original } = localize(issue);
    return original ? `${text} (${original})` : text;
  }).join("\n") || fallback;
}
function since(seconds: unknown): string | null {
  if (typeof seconds !== "number" || !Number.isFinite(seconds) || seconds < 0) return null;
  if (seconds < 60) return `${Math.floor(seconds)}초`;
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분`;
  return `${Math.floor(seconds / 3600)}시간`;
}
const note = (kind: "" | "warn" | "bad", text: string | string[], detail = "") =>
  `<div class="wf-banner ${kind} controller-note"><div>${(Array.isArray(text) ? text : [text]).map(line => `<p>${escapeHtml(line)}</p>`).join("")}${detail ? `<small>${escapeHtml(detail)}</small>` : ""}</div></div>`;
const procedure = `다시 시작하는 순서: ${RESTART_PROCEDURE}`;

/** The state the bar shows now: the last read plus what the attached host page knows (profiles, settings). */
export function controllerBarState(): ControllerBarState {
  return { ...last, profiles: active?.profiles() ?? null, settings: active?.ctx.settings };
}

/** The state in words, without the signal age (the pill and the live region; neither changes on an age-only poll). */
function stateLabel(state: ControllerBarState): { kind: "neutral" | "good" | "warn" | "bad"; text: string } {
  if (state.unavailable) return { kind: "neutral", text: NOT_AVAILABLE };
  const status = state.status;
  if (!status) return state.error ? { kind: "bad", text: "상태를 확인할 수 없음" } : { kind: "neutral", text: "상태 확인 중" };
  if (status.controller.state === "running")
    return status.stop_requested ? { kind: "warn", text: "정지 요청됨 · 진행 중 단계가 끝나면 정지합니다" } : { kind: "good", text: "동작 중" };
  if (status.controller.state === "stale") return { kind: "warn", text: "연결 끊김" };
  return { kind: "neutral", text: "정지" };
}
/** The signal age beside the pill ("" when there is none); a poll updates it in place. */
function signalText(state: ControllerBarState): string {
  const status = state.unavailable ? null : state.status;
  if (!status) return "";
  const age = since(status.controller.heartbeat_age_seconds);
  if (status.controller.state === "running") return age ? `마지막 신호 ${age} 전` : "";
  if (status.controller.state === "stale") return `${age ? `신호 ${age} 전 · ` : ""}PC가 절전·종료되었다면 다시 시작하세요`;
  return "";
}
const liveText = (state: ControllerBarState) => `백그라운드 컨트롤러: ${stateLabel(state).text}`;
/** What a poll compares before patching: the read without its heartbeat age (that only updates the signal text). */
function signature(read: Read): string {
  const status = read.status && { ...read.status, controller: { ...read.status.controller, heartbeat_age_seconds: null } };
  return JSON.stringify({ ...read, status });
}

/** Why `tracking: true` is not a restart yet: the explicit auth mode's credential is not saved completely (the
 * controller applied "none"; auth.py needs the token, or the OIDC token URL, client ID and secret). Null otherwise. */
function trackingCredentialMissing(status: ControllerStatus, settings: Readonly<Record<string, unknown>> | undefined): string | null {
  const tracking = isRecord(status.current) ? status.current.tracking : null;
  if (!isRecord(tracking) || tracking.effective_auth_mode !== "none" || !settings) return null;
  const blank = (name: string) => !String(settings[name] ?? "").trim();
  const then = "연결 설정에서 저장한 뒤 컨트롤러를 다시 시작하세요.";
  if (settings.auth_mode === "token")
    return settings.MLFLOW_TRACKING_TOKEN_saved ? null : `MLflow 인증값이 저장되어 있지 않습니다. ${then}`;
  if (settings.auth_mode !== "oidc") return null;
  if (blank("MLFLOW_OIDC_TOKEN_URL")) return `MLflow 인증 서버 주소(OIDC)가 저장되어 있지 않습니다. ${then}`;
  if (blank("MLFLOW_OIDC_CLIENT_ID")) return `MLflow OIDC 클라이언트 ID가 저장되어 있지 않습니다. ${then}`;
  return settings.MLFLOW_OIDC_CLIENT_SECRET_saved ? null : `MLflow 인증값이 저장되어 있지 않습니다. ${then}`;
}

function notes(state: ControllerBarState): string[] {
  const out: string[] = [];
  if (state.busyRead) out.push(note("", state.busyRead, "자동으로 다시 확인합니다. 아래 표시는 마지막으로 읽은 상태입니다."));
  if (state.error) out.push(note("bad", state.error));
  const status = state.status;
  if (!status || state.unavailable) return out;
  const running = status.controller.state === "running";
  const current = isRecord(status.current) ? status.current : null;
  // broker.json only ever describes a start that failed; while a controller runs it describes another start.
  if (!running && status.broker)
    out.push(note("bad", `최근 컨트롤러 시작 실패 · ${formatDate(status.broker.at)}`, String(status.broker.error ?? "")));
  if (current?.ok === false) {
    const error = typeof current.error === "string" ? current.error : "";
    out.push(note("bad", current.code === "controller_failed"
      ? `컨트롤러가 오류로 멈췄습니다.${typeof current.error_type === "string" ? ` (${current.error_type})` : ""}`
      : error || `컨트롤러를 시작하지 못했습니다. (${String(current.code ?? "")})`));
  }
  // What a restart would change, in one banner with the D5 procedure once.
  const restart: string[] = [];
  const changed = Array.isArray(status.restart_required?.profiles) ? status.restart_required.profiles : [];
  if (changed.length) restart.push(`컨트롤러 시작 후 바뀐 실행 프로필: ${changed.join(", ")}`);
  const credentialMissing = status.restart_required?.tracking === true ? trackingCredentialMissing(status, state.settings) : null;
  if (status.restart_required?.tracking === true && !credentialMissing) restart.push("MLflow 설정이 바뀌었습니다");
  // What the running controller left out at its start (current.json of this owner only).
  const skipped = running && current?.ok === true && current.owner_id === status.controller.owner_id && isRecord(current.skipped_profiles)
    ? Object.entries(current.skipped_profiles).filter((entry): entry is [string, string] => typeof entry[1] === "string") : [];
  const keyed = skipped.filter(([id, reason]) => reason === "credential_missing"
    && (state.profiles ?? []).some(profile => profile.profile_id === id && profile.RUNPOD_API_KEY_saved));
  const others = skipped.filter(entry => !keyed.includes(entry));
  if (keyed.length) restart.push(`키 저장 후 컨트롤러를 다시 시작하세요: ${keyed.map(([id]) => id).join(", ")}`);
  if (restart.length) out.push(note("warn", restart, procedure));
  if (credentialMissing) out.push(note("warn", credentialMissing));
  if (others.length)
    out.push(note("", `시작할 때 쓰지 않은 실행 프로필: ${others.map(([id, reason]) => `${id} (${SKIP_REASONS[reason] ?? reason})`).join(", ")}`));
  if (!running && status.runs?.active > 0)
    out.push(note("warn", `진행 중인 그래프 실행 ${status.runs.active}개가 있지만 컨트롤러가 동작하지 않아 진행되지 않습니다. 컨트롤러를 시작하세요.`));
  return out;
}

/** The bar's body (what a poll patches into `#controller-bar-body`). Every dynamic text is escaped. */
export function controllerBarInner(state: ControllerBarState, busy: boolean, host = "profiles"): string {
  const status = state.unavailable ? null : state.status;
  const running = status?.controller.state === "running";
  const label = stateLabel(state), signal = signalText(state);
  const summary = running ? `진행 중인 그래프 실행 ${status!.runs?.active ?? 0}개` : "";
  const startDisabled = busy || state.unavailable || running;
  const stopDisabled = busy || state.unavailable || !running || Boolean(status?.stop_requested);
  const list = notes(state);
  return `<div class="controller-head"><h3 id="controller-bar-title">백그라운드 컨트롤러</h3>
    <div class="controller-state">${statusPill(label.kind, label.text)}${signal ? `<span id="controller-bar-signal" class="controller-signal">${escapeHtml(signal)}</span>` : ""}${summary ? `<span>${escapeHtml(summary)}</span>` : ""}</div>
    <div class="actions">${button("컨트롤러 시작", `wf:${host}:controller-start`, running ? "" : "primary", startDisabled)}${button("컨트롤러 정지", `wf:${host}:controller-stop`, "", stopDisabled)}</div></div>
    ${list.length ? `<div class="controller-notes">${list.join("")}</div>` : ""}
    <p class="muted controller-foot">앱은 컨트롤러를 자동으로 시작하지 않습니다. 실행 프로필의 RunPod 키나 MLflow 인증값을 저장하거나 바꾼 뒤에는 컨트롤러를 다시 시작해야 새 값을 씁니다. 앱을 업데이트하거나 제거하기 전에는 컨트롤러를 정지하세요.</p>`;
}

/** The whole bar region (`#controller-bar`, a Task 11 selector): the patched body and, outside it, the one live region
 * (`role="status"`), whose text changes only when the state does, so a poll that changes only the age announces nothing. */
export function controllerBarHtml(state: ControllerBarState, busy: boolean, host = "profiles"): string {
  return `<section id="controller-bar" class="controller-bar" aria-labelledby="controller-bar-title"><div id="controller-bar-body">${controllerBarInner(state, busy, host)}</div>
    <p id="controller-bar-live" class="sr-only" role="status">${escapeHtml(liveText(state))}</p></section>`;
}

function readOf(result: WorkflowResult<ControllerStatus>): [Read, "ok" | "failed" | "unavailable"] {
  if (result.status === "ok" && isRecord(result.data) && isRecord(result.data.controller))
    return [{ status: result.data, unavailable: false, error: null, busyRead: null }, "ok"];
  if (result.issues.some(issue => issue.code === "not_available")) return [{ ...NONE, unavailable: true }, "unavailable"];
  // `status_busy` is transient: the last status of this visit stays, marked as not current.
  const busy = result.issues.find(issue => issue.code === "status_busy");
  if (busy) return [{ ...last, error: null, busyRead: busy.message || STATUS_BUSY }, "failed"];
  // Never keep showing an unverified controller as running.
  return [{ ...NONE, error: issueText(result.issues, "컨트롤러 상태를 확인하지 못했습니다.") }, "failed"];
}

/**
 * Shows the bar on `host` (default: the current page) and polls `controller_status` there (D10): one call in flight
 * through the page's queue, every 5 s, adaptive spacing and backoff (a `status_busy` answer backs off too), no call
 * while the page is busy, hidden or left; `not_available` stops it. A poll patches the bar only when the state changed
 * (an age-only change updates the signal text in place) and sets the live region's text only on a transition.
 */
export function attachControllerBar(ctx: ViewContext, options: { host?: string; queue?: Queue; profiles?: () => readonly PublicProfile[] | null } = {}):
    { poller: Poller; refresh(): Promise<void>; detach(): void } {
  active?.detach();                                       // one bar polls at a time
  last = NONE;                                            // this visit's own first read decides what is shown
  const host = options.host ?? ctx.page;
  const onVisible = () => { if (document.visibilityState === "visible") handle.poller.poke(); };
  const handle: Handle = {
    ctx, host, queue: options.queue ?? createQueue(), profiles: options.profiles ?? (() => null), detached: false,
    detach() {
      handle.detached = true;
      handle.poller.stop();
      document.removeEventListener("visibilitychange", onVisible);
      if (active === handle) { active = null; last = NONE; }
    },
    async read() {
      let next: Read, outcome: "ok" | "failed" | "unavailable";
      try { [next, outcome] = readOf(await handle.queue.run(() => controllerStatus())); }
      catch (error) { [next, outcome] = [{ ...NONE, error: errorText(error) }, "failed"]; }
      if (!handle.detached) last = next;                  // a read that ends after leaving is shown nowhere
      return outcome;
    },
    poller: createPoller({
      baseMs: BASE_MS,
      active: () => !handle.detached && ctx.page === host && document.visibilityState === "visible" && !ctx.busy,
      async tick() {
        const before = signature(last);
        const outcome = await handle.read();
        if (handle.detached) return "stop";
        if (ctx.page === host) {
          const state = controllerBarState();
          if (signature(last) !== before) ctx.patch("controller-bar-body", controllerBarInner(state, ctx.busy, host));
          else {
            const signal = document.getElementById("controller-bar-signal");
            if (signal && signal.textContent !== signalText(state)) signal.textContent = signalText(state);
          }
          const live = document.getElementById("controller-bar-live");
          if (live && live.textContent !== liveText(state)) live.textContent = liveText(state);
        }
        if (outcome === "unavailable") return "stop";
        if (outcome === "failed") throw new Error("controller status not read");
        return "continue";
      },
    }),
  };
  document.addEventListener("visibilitychange", onVisible);
  active = handle;
  return { poller: handle.poller, async refresh() { await handle.read(); }, detach: () => handle.detach() };
}

/** "컨트롤러 시작" (D9: only on a click, never automatically): a started controller is followed by an immediate read. */
export async function startControllerFlow(ctx: ViewContext): Promise<void> {
  const handle = active;
  await ctx.task("컨트롤러 시작 중", async () => {
    let result: { started: boolean; reason?: string };
    try { result = await (handle?.queue ?? createQueue()).run(() => startController()); }
    catch (error) {
      const text = errorText(error);
      throw new Error(text.includes(ROOT_UNUSABLE) ? `${text}\n${REDIRECTED_APPDATA}` : text);
    }
    ctx.notify(result.started ? "컨트롤러를 시작했습니다. 상태가 ‘동작 중’이 되는지 확인하세요."
      : result.reason === "already_running" ? "컨트롤러가 이미 동작 중입니다." : "컨트롤러를 시작하지 않았습니다.");
    if (handle && !handle.detached) await handle.read();
  });
}

/** "컨트롤러 정지": a dialog explains the drain (D9); Esc or "돌아가기" sends nothing. */
export async function stopControllerFlow(ctx: ViewContext): Promise<void> {
  const handle = active;
  const answer = await openDialog({ title: "컨트롤러 정지",
    bodyHtml: `<p>${escapeHtml(DRAIN_NOTICE)}</p><p>정지 요청 뒤에도 진행 상황은 이 상태 표시줄에서 확인할 수 있습니다.</p>`,
    actions: [{ id: "back", label: "돌아가기" }, { id: "stop", label: "정지 요청", primary: true }] });
  if (answer?.action !== "stop") return;
  await ctx.task("컨트롤러 정지 요청 중", async () => {
    const result = await (handle?.queue ?? createQueue()).run(() => stopController());
    if (result.status !== "ok" || !result.data) {
      if (result.issues.some(issue => issue.code === "not_available") && handle && !handle.detached) last = { ...NONE, unavailable: true };
      throw new Error(issueText(result.issues));
    }
    ctx.notify(result.data.requested ? "정지를 요청했습니다. 진행 중인 단계가 끝나면 컨트롤러가 정지합니다."
      : "컨트롤러가 동작 중이 아니어서 정지할 것이 없습니다.");
    if (handle && !handle.detached) await handle.read();
  });
}

/** Runs the bar's own verbs for its host view (`wf:<host>:controller-start|controller-stop`); false for others. */
export async function controllerAction(ctx: ViewContext, verb: string): Promise<boolean> {
  if (verb === "controller-start") { await startControllerFlow(ctx); return true; }
  if (verb === "controller-stop") { await stopControllerFlow(ctx); return true; }
  return false;
}
