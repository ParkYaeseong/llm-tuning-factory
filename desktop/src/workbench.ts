type Obj = Record<string, any>;
export const escapeHtml = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const paths: Record<string, string> = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  training: '<path d="M9 3h6m-5 0v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4A1.5 1.5 0 0 0 20 19L14 9V3M7 14h10"/>',
  datasets: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  jobs: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
  history: '<path d="M3 3v18h18M7 15l4-5 4 3 5-8"/>',
  connections: '<path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8m6 9v4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  file: '<path d="M14 2H5v20h14V7zM14 2v6h5M8 13h8M8 17h5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  folder: '<path d="M3 6V4h6l2 3h10v13H3z"/>',
  tutorial: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8z"/>',
  projects: '<path d="M3 7V5h7l2 2h9v12H3z"/><path d="M8 13h8M8 16h5"/>',
  recipes: '<path d="M6 3h12v18H6z"/><path d="M9 8h6M9 12h6M9 16h3"/>',
  runs: '<circle cx="6" cy="6" r="2.5"/><circle cx="6" cy="18" r="2.5"/><circle cx="18" cy="12" r="2.5"/><path d="M8.5 6H12v12H8.5M12 12h3.5"/>',
  profiles: '<rect x="3" y="4" width="18" height="7" rx="1.5"/><rect x="3" y="13" width="18" height="7" rx="1.5"/><path d="M7 7.5h.01M7 16.5h.01"/>',
};
export function icon(name: string, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
}
/** A labelled input. `label` and `help` are fixed markup (not escaped); the value is escaped. */
export function field(label: string, key: string, value: unknown = "", type = "text", help = "") {
  return `<label class="field"><span>${label}</span><input aria-label="${escapeHtml(label)}" data-key="${key}" type="${type}" value="${escapeHtml(value)}" ${type === "password" ? 'autocomplete="new-password"' : ""}>${help ? `<small>${help}</small>` : ""}</label>`;
}
/** A labelled select. `label` is fixed markup; option IDs and titles are escaped. */
export function select(label: string, key: string, value: string, options: [string, string][]) {
  return `<label class="field"><span>${label}</span><select aria-label="${escapeHtml(label)}" data-key="${key}">${options.map(([id, title]) => `<option value="${escapeHtml(id)}" ${id === String(value ?? "") ? "selected" : ""}>${escapeHtml(title)}</option>`).join("")}</select></label>`;
}
export const button = (label: string, action: string, cls = "", disabled = false) =>
  `<button type="button" class="${cls}" data-action="${escapeHtml(action)}"${disabled ? " disabled" : ""}>${label}</button>`;
export function formatDate(seconds: unknown) {
  return typeof seconds === "number" ? new Date(seconds * 1000).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}
export function filterRuns(runs: Obj[], query: string, status: string) {
  const needle = query.toLocaleLowerCase().trim();
  return runs.filter(run => (!status || run.status === status) &&
    [run.ai_id, run.operator, run.id, run.config?.base_model].some(value =>
      String(value || "").toLocaleLowerCase().includes(needle)));
}
export function progressView(progress: Obj | null) {
  if (!progress?.available) return `<div class="chart-empty">${icon("history", 24)}<b>실제 학습 지표를 기다리고 있습니다</b><span>원격 진행 상태를 불러오거나 완료된 작업의 기록을 확인하세요.</span></div>`;
  const points = progress.points || [];
  const number = (value: unknown) => typeof value === "number" ? value.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : "—";
  let chart = '<div class="chart-empty small">Loss가 두 번 이상 기록되면 추이를 표시합니다.</div>';
  if (points.length >= 2) {
    const losses = points.map((p: Obj) => p.loss);
    const low = Math.min(...losses), high = Math.max(...losses);
    const first = points[0].step, last = points.at(-1).step;
    const coordinates = points.map((p: Obj) => `${36 + (p.step - first) / Math.max(1, last - first) * 574},${24 + (high - p.loss) / (high - low || 1) * 98}`).join(" ");
    chart = `<svg class="loss-chart" viewBox="0 0 640 158" role="img" aria-label="실제 train loss 추이"><path d="M36 24H610M36 73H610M36 122H610" class="chart-grid"/><text x="0" y="28">${number(high)}</text><text x="0" y="126">${number(low)}</text><text x="36" y="148">step ${first}</text><text x="555" y="148">${last}</text><polyline points="${coordinates}" fill="none" stroke="currentColor" stroke-width="2"/></svg>`;
  }
  return `<div class="progress-summary"><div><small>진행 단계</small><b>${number(progress.current_step)} <span>/ ${number(progress.total_steps)}</span></b></div><div><small>최근 train loss</small><b>${number(progress.loss)}</b></div><div><small>경과 시간</small><b>${escapeHtml(progress.elapsed_time || "—")}</b></div><div><small>남은 시간 · 엔진 추정</small><b>${escapeHtml(progress.remaining_time || "—")}</b></div></div>
  ${progress.percent !== null && progress.percent !== undefined ? `<progress max="100" value="${Number(progress.percent)}" aria-label="학습 진행률"></progress>` : ""}
  ${chart}<div class="chart-caption">LLaMA-Factory 원본 학습 로그 · 최대 200개 지점 · 업무 품질 점수가 아닙니다</div>`;
}

/** Graph-owned jobs (D15): a `graph_run_id`, or a manifest without the v1 `config`. Legacy lists never show them. */
export const isGraphJob = (job: Obj) => Boolean(job?.graph_run_id) || !job?.manifest?.config;

/** What identifies a focusable control across re-renders: `data-focus-key`, else `data-action`, else `id`. */
export function focusKey(element: Element | null): string | null {
  if (!(element instanceof HTMLElement)) return null;
  return element.dataset.focusKey ?? element.dataset.action ?? (element.id || null);
}

/** The controls under `root` that a focus key names: by `data-focus-key`, else `data-action`, else `id`. */
export function focusMatches(root: ParentNode, key: string): HTMLElement[] {
  const quoted = CSS.escape(key);
  for (const selector of [`[data-focus-key="${quoted}"]`, `[data-action="${quoted}"]`, `#${quoted}`]) {
    const found = [...root.querySelectorAll<HTMLElement>(selector)];
    if (found.length) return found;
  }
  return [];
}

/** Focuses the element under `root` that `focusKey` named before a re-render (no scrolling); for the whole document
 * the page content (`#app main`) is searched first, so the sidebar never wins over the page. */
export function restoreFocus(root: ParentNode, key: string | null): void {
  if (!key) return;
  const scopes = root === document ? [document.querySelector("#app main"), document] : [root];
  for (const scope of scopes) {
    const target = scope ? focusMatches(scope, key)[0] : undefined;
    if (target) { target.focus({ preventScroll: true }); return; }
  }
}

/** The text of an error for the user: its message, never an "Error: " or "BridgeError: " prefix. */
export const errorText = (error: unknown) => error instanceof Error ? error.message || error.name : String(error);

/** After a dialog closes on a click, its repetition within 400 ms is swallowed so it never activates what was underneath:
 * for a pointer close, a pointer click at the same spot (8 px for a mouse, 24 px for touch and pen); for a keyboard close
 * (`detail` 0), a keyboard activation of the control the focus was returned to — other keyboard activations pass. */
function swallowRepeat(origin: MouseEvent, returnedTo: Element | null) {
  const until = performance.now() + 400;
  const types = ["mousedown", "click", "dblclick"];
  const keyboard = origin.detail === 0;
  const pointerType = (origin as PointerEvent).pointerType;
  const radius = pointerType === "touch" || pointerType === "pen" ? 24 : 8;
  const swallow = (event: Event) => {
    const mouse = event as MouseEvent;
    if (performance.now() > until) { stop(); return; }
    const repeat = keyboard
      ? mouse.detail === 0 && returnedTo !== null && event.target instanceof Node && returnedTo.contains(event.target)
      : mouse.detail !== 0 && Math.abs(mouse.clientX - origin.clientX) <= radius && Math.abs(mouse.clientY - origin.clientY) <= radius;
    if (repeat) { event.preventDefault(); event.stopImmediatePropagation(); }
  };
  const stop = () => types.forEach(type => window.removeEventListener(type, swallow, true));
  types.forEach(type => window.addEventListener(type, swallow, true));
  setTimeout(stop, 450);
}

/** A state pill (not a `.badge`): semantic colour for real states only. */
export function statusPill(kind: "neutral" | "good" | "warn" | "bad", text: string): string {
  return `<span class="status-pill ${kind}">${escapeHtml(text)}</span>`;
}

export interface DialogSpec {
  title: string; bodyHtml: string;                               // caller escapes dynamic parts
  checks?: { id: string; label: string }[];
  actions: { id: string; label: string; primary?: boolean; requires?: string[] }[];
}
let dialogs = 0;

/**
 * A modal `<dialog>` appended to `body`. Resolves null (and opens nothing) when a dialog is already open, and on Esc.
 * The heading takes focus; an action that requires checks stays disabled until they are all checked; the first
 * activation disables every action; on close, focus returns to the control that was focused before opening, and the
 * rest of a double click or a repeated Enter at the same spot is swallowed for 400 ms (`swallowRepeat`).
 */
export function openDialog(spec: DialogSpec): Promise<{ action: string; checked: string[] } | null> {
  if (document.querySelector("dialog[open]")) return Promise.resolve(null);
  const opener = document.activeElement instanceof HTMLElement && document.activeElement !== document.body ? document.activeElement : null;
  const openerKey = focusKey(opener);
  const titleId = `wf-dialog-title-${++dialogs}`;
  const dialog = document.createElement("dialog");
  dialog.className = "wf-dialog";
  dialog.setAttribute("aria-labelledby", titleId);
  dialog.innerHTML = `<h2 id="${titleId}" tabindex="-1">${escapeHtml(spec.title)}</h2><div class="wf-dialog-body">${spec.bodyHtml}</div>
    ${(spec.checks ?? []).map(check => `<label class="check"><input type="checkbox" data-dialog-check="${escapeHtml(check.id)}">${escapeHtml(check.label)}</label>`).join("")}
    <div class="actions">${spec.actions.map(item => `<button type="button" class="${item.primary ? "primary" : ""}" data-dialog-action="${escapeHtml(item.id)}">${escapeHtml(item.label)}</button>`).join("")}</div>`;
  document.body.append(dialog);
  const checks = [...dialog.querySelectorAll<HTMLInputElement>("[data-dialog-check]")];
  const buttons = [...dialog.querySelectorAll<HTMLButtonElement>("[data-dialog-action]")];
  const checked = () => checks.filter(input => input.checked).map(input => input.dataset.dialogCheck!);
  const update = () => buttons.forEach((button, index) => {
    const requires = spec.actions[index].requires ?? [];
    button.disabled = !requires.every(id => checked().includes(id));
  });
  update();
  dialog.showModal();
  dialog.querySelector<HTMLElement>(`#${titleId}`)!.focus();
  return new Promise(resolve => {
    let settled = false;
    const finish = (result: { action: string; checked: string[] } | null) => {
      if (settled) return;
      settled = true;
      buttons.forEach(button => button.disabled = true);
      dialog.close(); dialog.remove();
      if (opener?.isConnected) opener.focus({ preventScroll: true });
      else restoreFocus(document, openerKey);
      resolve(result);
    };
    checks.forEach(input => input.addEventListener("change", update));
    buttons.forEach((button, index) => button.addEventListener("click", event => {
      if (button.disabled) return;
      finish({ action: spec.actions[index].id, checked: checked() });
      swallowRepeat(event, document.activeElement);   // finish() has returned the focus to the opener
    }));
    dialog.addEventListener("cancel", event => { event.preventDefault(); finish(null); });
  });
}
