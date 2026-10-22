import { destinationChange } from "./server-connection";
import { escapeHtml as esc } from "./workbench";
import { tutorialSteps, type TutorialContext, type TutorialStep } from "./tutorial-steps";

type Obj = Record<string, any>;
type Outcome = "completed" | "dismissed";

export interface TutorialHost {
  state: Obj;
  navigate(page: string): Promise<void>;
  render(): void;
  /** main.ts renderLate: for a render that may come long after the click (drafts read first, focus kept). */
  renderLate(): void;
  api(action: string, data?: Obj): Promise<any>;
  checkCapabilities(): Promise<unknown>;
  normalizeConfig(): void;
}

const STORAGE_KEY = "tuning-factory.tutorial.v1";

export function tutorialSeen() {
  try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
}

export function rememberTutorial(outcome: Outcome) {
  // Without storage the first-run card simply appears again; the tour itself still works.
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ outcome, at: new Date().toISOString() })); } catch { return; }
}

export function createTutorial(host: TutorialHost, steps: TutorialStep[] = tutorialSteps) {
  let index = -1;
  let snapshot: Obj | null = null;
  let startSettings: Obj = {};
  let sample: Obj | null = null;
  let entered = new Set<string>();
  let focused = -1;
  let scrolled = -1;
  let announced = -1;
  let markup = "";
  let layer: HTMLDivElement | null = null;
  // Every start, restart and end begins a new run; work begun in an earlier run must not change the current one.
  let run = 0;
  let ctaRun = -1;
  let pendingEnd: Outcome | null = null;
  // Applied by the next actual render: the host may defer a render, which would replace a button focused earlier.
  let launcherFocus = false;

  const ctaPending = () => ctaRun === run;
  const waiting = () => Boolean(host.state.busy) || ctaPending();

  const context: TutorialContext = {
    state: host.state,
    async loadSample() { sample ??= await host.api("tutorial_sample"); return sample!; },
    normalizeConfig: () => host.normalizeConfig(),
    checkCapabilities: () => host.checkCapabilities(),
    navigate: page => host.navigate(page),
    // Called by the last step's own call-to-action, whose pending state must not defer the end it asks for.
    finish: () => end("completed", true),
  };

  async function guarded(work: () => unknown) {
    try { await work(); } catch (error) { host.state.error = String(error); }
  }

  function target(step: TutorialStep) {
    if (host.state.page !== step.page) return null;
    const matches = document.querySelectorAll<HTMLElement>(`#app [data-tour="${step.anchor}"]`);
    return matches.length === 1 ? matches[0] : null;
  }

  /** The removed popover took focus with it; hand it to the sidebar button that reopens the tutorial. */
  function focusLauncher() {
    if (document.activeElement && document.activeElement !== document.body) return;
    document.querySelector<HTMLElement>('#app aside [data-action="tutorial"]')?.focus();
  }
  function onLauncherOrNowhere() {
    const active = document.activeElement;
    return !active || active === document.body || active.matches('#app aside [data-action="tutorial"]');
  }

  async function go(next: number) {
    const current = run;
    index = next;
    const step = steps[index];
    host.state.error = "";
    if (host.state.page !== step.page) await host.navigate(step.page);
    if (current !== run || index !== next) return;
    if (step.enter && !entered.has(step.id)) {
      entered.add(step.id);
      await guarded(() => step.enter!(context));
      if (current !== run) return;
    }
    host.render();
  }

  async function start() {
    // A restart keeps the draft saved by the first start, so ending still returns to the user's own work.
    if (index < 0) {
      const { config, plan, datasetDraft, preview } = host.state;
      snapshot = structuredClone({ config, plan, datasetDraft, preview });
      startSettings = structuredClone(host.state.settings ?? {});
    }
    run++; pendingEnd = null; launcherFocus = false;
    entered = new Set(); focused = -1; scrolled = -1; announced = -1;
    await go(0);
  }

  async function end(outcome: Outcome, fromCta = false) {
    if (index < 0) return;
    // A request still in flight would land after the restore and bring tutorial values back.
    if (host.state.busy || (ctaPending() && !fromCta)) { pendingEnd ??= outcome; host.render(); return; }
    pendingEnd = null;
    rememberTutorial(outcome);
    index = -1; run++;
    layer?.remove(); layer = null;
    if (snapshot) {
      // Saving another destination or GPU selection during the tour invalidated the plan, as it does outside it;
      // bringing it back would offer a start the sidecar refuses for its old destination.
      if (destinationChange(startSettings, host.state.settings ?? {}) !== "none") snapshot.plan = null;
      Object.assign(host.state, snapshot, { capabilities: null });
    }
    snapshot = null;
    launcherFocus = true; host.render();
    if (host.state.config) {
      await guarded(() => host.checkCapabilities());
      // The answer may come late (Task 11.1): what was typed meanwhile is kept and a focus the user moved stays.
      if (index < 0 && onLauncherOrNowhere()) launcherFocus = true;
      host.renderLate();
    }
  }

  async function runCta(step: TutorialStep) {
    const current = run;
    ctaRun = current; host.render();
    try {
      const patch = await step.cta!.run(context);
      if (patch && current === run) Object.assign(host.state, patch);
    } catch (error) { if (current === run) host.state.error = String(error); }
    finally { if (ctaRun === current) ctaRun = -1; }
    if (index < 0) launcherFocus = true;
    host.render();
  }

  async function command(name: string) {
    if (index < 0 || pendingEnd) return;
    const step = steps[index];
    if (name === "stop") return end("dismissed");
    if (name === "back") return go(Math.max(0, index - 1));
    if (name === "goto") { focused = -1; scrolled = -1; return go(index); }
    if (waiting()) return;
    if (name === "cta" && step.cta) return runCta(step);
    if (name === "next" || name === "skip") return index === steps.length - 1 ? end("completed") : go(index + 1);
  }

  function ensureLayer() {
    if (layer) return layer;
    markup = ""; announced = -1;
    layer = document.createElement("div");
    layer.id = "tutorial-layer";
    layer.innerHTML = '<div class="tour-ring" hidden></div><div class="tour-popover" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-body"></div><div class="sr-only" aria-live="polite"></div>';
    layer.addEventListener("click", event => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-tour-action]");
      if (button && !button.disabled) void command(button.dataset.tourAction!);
    });
    document.body.append(layer);
    return layer;
  }

  function position() {
    if (index < 0 || !layer) return;
    const ring = layer.querySelector<HTMLDivElement>(".tour-ring")!;
    const popover = layer.querySelector<HTMLDivElement>(".tour-popover")!;
    const element = target(steps[index]);
    const margin = 12, width = popover.offsetWidth, height = popover.offsetHeight;
    let top: number, left: number;
    if (element) {
      const box = element.getBoundingClientRect();
      ring.hidden = false;
      Object.assign(ring.style, { top: `${box.top - 4}px`, left: `${box.left - 4}px`, width: `${box.width + 8}px`, height: `${box.height + 8}px` });
      top = box.bottom + margin;
      left = box.left;
      if (top + height > window.innerHeight) {
        top = box.top - margin - height;
        // A tall target leaves no room above or below; keep its left-aligned controls uncovered.
        if (top < 0) { top = window.innerHeight - height - margin; left = window.innerWidth - width - margin; }
      }
    } else {
      ring.hidden = true;
      // Center over the content beside the sidebar, whose width depends on the window width.
      const content = document.querySelector("#app main")?.getBoundingClientRect();
      left = content ? content.left + (content.width - width) / 2 : (window.innerWidth - width) / 2;
      top = (window.innerHeight - height) / 2;
    }
    // Stay inside the window even while the target is scrolled out of view.
    popover.style.top = `${Math.max(margin, Math.min(top, window.innerHeight - height - margin))}px`;
    popover.style.left = `${Math.max(margin, Math.min(left, window.innerWidth - width - margin))}px`;
  }

  function afterRender() {
    if (index < 0) {
      if (launcherFocus) { launcherFocus = false; focusLauncher(); }
      return;
    }
    const step = steps[index];
    const busy = waiting();
    if (pendingEnd && !busy) {
      const current = run, outcome = pendingEnd;
      queueMicrotask(() => { if (current === run && pendingEnd) void end(outcome); });
    }
    const element = target(step);
    const root = ensureLayer();
    const popover = root.querySelector<HTMLDivElement>(".tour-popover")!;
    const active = document.activeElement as HTMLElement | null;
    const refocus = active && popover.contains(active) ? (active.dataset.tourAction ?? "title") : undefined;
    const practice = step.kind === "practice";
    const complete = step.done ? step.done(host.state, sample) : true;
    const ending = pendingEnd !== null;
    const locked = busy || ending;
    const away = host.state.page !== step.page;
    // Coming back to the step's page scrolls its target into view again.
    if (away) scrolled = -1;
    const last = index === steps.length - 1;
    const status = ending ? ["", "진행 중인 작업이 끝나면 튜토리얼을 종료합니다."]
      : busy ? ["", "처리 중입니다. 끝나면 계속 진행할 수 있습니다."]
      : away ? ["", "다른 화면을 보고 있습니다. ‘이 단계로 이동’을 누르세요."]
      : !element ? ["", "이 단계의 화면 요소를 찾지 못했습니다."]
      : practice && complete ? ["done", "완료했습니다. 다음으로 진행하세요."]
      : practice && host.state.error ? ["error", "위의 오류 안내를 확인한 뒤 다시 시도해 보세요."]
      : null;
    const html = `<div class="tour-meta"><span>${index + 1} / ${steps.length}</span><span class="badge">${practice ? "직접 해 보기" : "설명"}</span></div>
      <h2 id="tour-title" tabindex="-1">${esc(step.title)}</h2><p id="tour-body">${esc(step.body)}</p>
      ${status ? `<p class="tour-status ${status[0]}">${status[1]}</p>` : ""}
      <div class="tour-actions"><button type="button" class="quiet" data-tour-action="stop" ${ending ? "disabled" : ""}>그만하기</button><span></span>
      ${index > 0 ? `<button type="button" data-tour-action="back" ${ending ? "disabled" : ""}>이전</button>` : ""}
      ${away ? `<button type="button" data-tour-action="goto" ${ending ? "disabled" : ""}>이 단계로 이동</button>` : ""}
      ${step.cta && !away ? `<button type="button" data-tour-action="cta" ${locked ? "disabled" : ""}>${esc(step.cta.label)}</button>` : ""}
      ${practice && !complete ? `<button type="button" data-tour-action="skip" ${locked ? "disabled" : ""}>건너뛰기</button>` : ""}
      <button type="button" class="primary" data-tour-action="next" ${locked || (practice && !complete) ? "disabled" : ""}>${last ? "완료" : "다음"}</button></div>`;
    popover.classList.toggle("centered", !element);
    // App renders (such as the jobs poll) leave an unchanged popover alone, so a click on it is not lost.
    const rewrite = html !== markup;
    if (rewrite) { popover.innerHTML = html; markup = html; }
    if (announced !== index) {
      announced = index;
      root.querySelector(".sr-only")!.textContent = `튜토리얼 ${index + 1}단계: ${step.title}`;
    }
    const title = popover.querySelector<HTMLElement>("#tour-title")!;
    if (focused !== index) {
      focused = index;
      title.focus({ preventScroll: true });
    } else if (rewrite && refocus) {
      const again = refocus === "title" ? null : popover.querySelector<HTMLButtonElement>(`[data-tour-action="${refocus}"]`);
      (again && !again.disabled ? again : title).focus({ preventScroll: true });
    }
    if (element && scrolled !== index) {
      scrolled = index;
      element.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
    position();
  }

  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, true);
  window.addEventListener("keydown", event => {
    if (event.key === "Escape" && index >= 0 && !document.querySelector("dialog[open]")) {
      event.preventDefault(); void end("dismissed");
    }
  });

  return { start, end, afterRender, active: () => index >= 0 };
}
