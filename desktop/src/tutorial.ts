import { escapeHtml as esc } from "./workbench";
import { tutorialSteps, type TutorialContext, type TutorialStep } from "./tutorial-steps";

type Obj = Record<string, any>;

export interface TutorialHost {
  state: Obj;
  navigate(page: string): Promise<void>;
  render(): void;
  api(action: string, data?: Obj): Promise<any>;
  checkCapabilities(): Promise<unknown>;
  normalizeConfig(): void;
}

const STORAGE_KEY = "tuning-factory.tutorial.v1";

export function tutorialSeen() {
  try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
}

export function rememberTutorial(outcome: "completed" | "dismissed") {
  // Without storage the first-run card simply appears again; the tour itself still works.
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ outcome, at: new Date().toISOString() })); } catch { return; }
}

export function createTutorial(host: TutorialHost, steps: TutorialStep[] = tutorialSteps) {
  let index = -1;
  let snapshot: Obj | null = null;
  let sample: Obj | null = null;
  let entered = new Set<string>();
  let focused = -1;
  let scrolled = -1;
  let layer: HTMLDivElement | null = null;

  const context: TutorialContext = {
    state: host.state,
    async loadSample() { sample ??= await host.api("tutorial_sample"); return sample!; },
    normalizeConfig: () => host.normalizeConfig(),
    checkCapabilities: () => host.checkCapabilities(),
    navigate: page => host.navigate(page),
    finish: () => end("completed"),
  };

  async function guarded(work: () => unknown) {
    try { await work(); } catch (error) { host.state.error = String(error); }
  }

  function target(step: TutorialStep) {
    if (host.state.page !== step.page) return null;
    const matches = document.querySelectorAll<HTMLElement>(`#app [data-tour="${step.anchor}"]`);
    return matches.length === 1 ? matches[0] : null;
  }

  async function go(next: number) {
    index = next;
    const step = steps[index];
    host.state.error = "";
    if (host.state.page !== step.page) await host.navigate(step.page);
    if (index !== next) return;
    if (step.enter && !entered.has(step.id)) {
      entered.add(step.id);
      await guarded(() => step.enter!(context));
    }
    host.render();
  }

  async function start() {
    if (index >= 0) return;
    const { config, plan, datasetDraft, preview } = host.state;
    snapshot = structuredClone({ config, plan, datasetDraft, preview });
    entered = new Set(); focused = -1; scrolled = -1;
    await go(0);
  }

  async function end(outcome: "completed" | "dismissed") {
    if (index < 0) return;
    rememberTutorial(outcome);
    index = -1;
    layer?.remove(); layer = null;
    if (snapshot) Object.assign(host.state, snapshot, { capabilities: null });
    snapshot = null;
    host.render();
    if (host.state.config) { await guarded(() => host.checkCapabilities()); host.render(); }
  }

  async function command(name: string) {
    if (index < 0) return;
    const step = steps[index];
    if (name === "stop") return end("dismissed");
    if (name === "back") return go(Math.max(0, index - 1));
    if (name === "goto") { focused = -1; return go(index); }
    if (name === "cta" && step.cta) { await guarded(() => step.cta!.run(context)); host.render(); return; }
    if (name === "next" || name === "skip") return index === steps.length - 1 ? end("completed") : go(index + 1);
  }

  function ensureLayer() {
    if (layer) return layer;
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
    if (!element) { ring.hidden = true; popover.style.top = popover.style.left = ""; return; }
    const box = element.getBoundingClientRect();
    const margin = 12, width = popover.offsetWidth, height = popover.offsetHeight;
    ring.hidden = false;
    Object.assign(ring.style, { top: `${box.top - 4}px`, left: `${box.left - 4}px`, width: `${box.width + 8}px`, height: `${box.height + 8}px` });
    let top = box.bottom + margin;
    let left = Math.min(Math.max(margin, box.left), window.innerWidth - width - margin);
    if (top + height > window.innerHeight) {
      top = box.top - margin - height;
      // A tall target leaves no room above or below; keep its left-aligned controls uncovered.
      if (top < 0) { top = window.innerHeight - height - margin; left = window.innerWidth - width - margin; }
    }
    popover.style.top = `${top}px`; popover.style.left = `${left}px`;
  }

  function afterRender() {
    if (index < 0) return;
    const step = steps[index];
    const element = target(step);
    const root = ensureLayer();
    const popover = root.querySelector<HTMLDivElement>(".tour-popover")!;
    const active = document.activeElement as HTMLElement | null;
    const refocus = active && popover.contains(active) ? (active.dataset.tourAction ?? "title") : undefined;
    const practice = step.kind === "practice";
    const complete = step.done ? step.done(host.state, sample) : true;
    const busy = Boolean(host.state.busy);
    const away = host.state.page !== step.page;
    const last = index === steps.length - 1;
    const status = busy ? ["", "처리 중입니다. 끝나면 계속 진행할 수 있습니다."]
      : away ? ["", "다른 화면을 보고 있습니다. ‘이 단계로 이동’을 누르세요."]
      : !element ? ["", "이 단계의 화면 요소를 찾지 못했습니다."]
      : practice && complete ? ["done", "완료했습니다. 다음으로 진행하세요."]
      : practice && host.state.error ? ["error", "위의 오류 안내를 확인한 뒤 다시 시도해 보세요."]
      : null;
    popover.classList.toggle("centered", !element);
    popover.innerHTML = `<div class="tour-meta"><span>${index + 1} / ${steps.length}</span><span class="badge">${practice ? "직접 해 보기" : "설명"}</span></div>
      <h2 id="tour-title" tabindex="-1">${esc(step.title)}</h2><p id="tour-body">${esc(step.body)}</p>
      ${status ? `<p class="tour-status ${status[0]}">${status[1]}</p>` : ""}
      <div class="tour-actions"><button type="button" class="quiet" data-tour-action="stop">그만하기</button><span></span>
      ${index > 0 ? '<button type="button" data-tour-action="back">이전</button>' : ""}
      ${away ? '<button type="button" data-tour-action="goto">이 단계로 이동</button>' : ""}
      ${step.cta && !away ? `<button type="button" data-tour-action="cta" ${busy ? "disabled" : ""}>${esc(step.cta.label)}</button>` : ""}
      ${practice && !complete ? `<button type="button" data-tour-action="skip" ${busy ? "disabled" : ""}>건너뛰기</button>` : ""}
      <button type="button" class="primary" data-tour-action="next" ${busy || (practice && !complete) ? "disabled" : ""}>${last ? "완료" : "다음"}</button></div>`;
    root.querySelector(".sr-only")!.textContent = `튜토리얼 ${index + 1}단계: ${step.title}`;
    if (focused !== index || refocus === "title") {
      focused = index;
      popover.querySelector<HTMLElement>("#tour-title")!.focus({ preventScroll: true });
    } else if (refocus) popover.querySelector<HTMLElement>(`[data-tour-action="${refocus}"]`)?.focus({ preventScroll: true });
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
