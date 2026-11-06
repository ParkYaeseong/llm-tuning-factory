import { t as tr, msg, html as ui, staticHtml, localized } from "../i18n";
// The "기반 모델" panel of the graph page (10.5, D13): indexed HF roots from `model_roots_list`, pinning a model with the
// legacy metadata search/inspection (a 40-hex revision), and two separate downloads behind their own notice dialogs:
// "설정·토크나이저 받기" (`model_root_snapshot`) and "모듈 구조 확인" (`model_root_targets`, after a snapshot). Nothing
// runs without a click; a native refusal is shown verbatim. Every dynamic text is escaped.
import { planning } from "../api";
import { localize } from "../issues";
import type { ViewContext } from "../view-types";
import { escapeHtml, openDialog, statusPill } from "../workbench";
import type { HFRootRef, ModelRoot, WorkflowIssue, WorkflowResult } from "../workflow-types";

export interface PinnedModel { repo_id: string; revision: string }
export interface InspectedModel { repo_id: string; revision: string; architecture: string; supported: boolean; reasons: string[] }
export interface RootsState {
  roots: ModelRoot[]; pinned: PinnedModel | null; error: string | null;
  /** The pin form's drafts and results (legacy `model_search`/`model_inspect`, metadata only). */
  query: string; model: string; results: { id: string; downloads: number | null; tag: string }[] | null; inspected: InspectedModel | null;
  unavailable: boolean; loaded: boolean;
}
export const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const HEX40 = /^[0-9a-f]{40}$/;
const SNAPSHOT_NOTE = "가중치는 받지 않습니다 · 파일당 64 MiB, 합계 128 MiB 이하 · 저장된 HF 토큰을 씁니다.";
const TARGETS_NOTE = "safetensors 헤더만 읽고 가중치는 받지 않습니다 · 합계 64 MiB 이하 · 저장된 HF 토큰을 씁니다.";

/** The panel's state for this app session (the graph page owns its rendering). */
export const rootsState: RootsState = { roots: [], pinned: null, error: null, query: "", model: "", results: null, inspected: null,
  unavailable: false, loaded: false };

/** The identity a root is shown and addressed by in the page (`data-root`). */
export const rootKey = (root: Pick<HFRootRef, "repo_id" | "revision">) => `${root.repo_id}@${root.revision}`;
export const findRoot = (root: HFRootRef | null | undefined) => root
  ? rootsState.roots.find(entry => entry.root.repo_id === root.repo_id && entry.root.revision === root.revision
    && entry.root.config_sha256 === root.config_sha256 && entry.root.tokenizer_fingerprint === root.tokenizer_fingerprint) : undefined;
const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => {
  const { text, original } = localize(issue);
  return original ? `${text} (${original})` : text;
}).join("\n") || tr("요청을 처리하지 못했습니다.");
const short = (revision: string) => escapeHtml(revision.slice(0, 8));
const disabled = (flag: boolean) => flag ? " disabled" : "";

export function modelRootsPanel(state: RootsState, busy: boolean): string {
  const list = state.roots.map((entry, index) => {
    const name = escapeHtml(entry.root.repo_id);
    const actions = [
      !entry.snapshot_ready ? ui`<button type="button" data-action="wf:graph:root-snapshot:${index}" aria-label="${name} 설정·토크나이저 받기"${disabled(busy || state.unavailable)}>설정·토크나이저 받기</button>` : "",
      entry.snapshot_ready && !entry.targets_ready ? ui`<button type="button" data-action="wf:graph:root-targets:${index}" aria-label="${name} 모듈 구조 확인"${disabled(busy || state.unavailable)}>모듈 구조 확인</button>` : "",
    ].join("");
    return ui`<li class="root-item" data-root="${escapeHtml(rootKey(entry.root))}" tabindex="-1"><div><b>${name}</b> <span class="mono">${short(entry.root.revision)}</span>
      <small>${escapeHtml(entry.profile?.architecture ?? "")} · 템플릿 ${escapeHtml(entry.profile?.template ?? "")}</small></div>
      <div class="root-states">${entry.snapshot_ready ? statusPill("good", tr("설정·토크나이저 받음")) : statusPill("warn", tr("설정·토크나이저 없음"))}
      ${entry.targets_ready ? statusPill("good", tr("모듈 구조 확인됨")) : statusPill("neutral", tr("모듈 구조 확인 전"))}</div>
      ${actions ? `<div class="actions">${actions}</div>` : ""}</li>`;
  }).join("");
  const empty = !state.loaded ? staticHtml('<p class="muted">기반 모델 목록을 불러오는 중입니다.</p>')
    : state.unavailable ? `<p class="muted">${tr(NOT_AVAILABLE)}</p>` : staticHtml('<p class="muted">아직 받은 기반 모델이 없습니다. 아래에서 모델을 고르고 설정·토크나이저를 받으세요.</p>');
  const results = state.results === null ? "" : state.results.length ? ui`<ul class="root-results" aria-label="검색 결과">${state.results.map((model, index) =>
    ui`<li><span><b>${escapeHtml(model.id)}</b><small>${escapeHtml(model.tag)}${model.downloads !== null ? msg` · 다운로드 ${model.downloads.toLocaleString("ko-KR")}` : ""}</small></span>
      <button type="button" class="quiet" data-action="wf:graph:root-pick:${index}" aria-label="${escapeHtml(model.id)} 고르기">고르기</button></li>`).join("")}</ul>`
    : staticHtml('<p class="muted">검색 결과가 없습니다.</p>');
  const inspected = state.inspected;
  const pinned = inspected ? ui`<div class="root-inspected${inspected.supported ? "" : " blocked"}" role="status"><b>${escapeHtml(inspected.repo_id)}</b>
      <span class="mono">${escapeHtml(inspected.revision)}</span><small>구조 ${escapeHtml(inspected.architecture || tr("확인 불가"))} · ${inspected.supported ? tr("학습 기반 모델로 쓸 수 있는 구조입니다.") : escapeHtml(inspected.reasons.join(" · ") || tr("학습 기반 모델로 지원하지 않습니다."))}</small>
      <div class="actions"><button type="button" data-action="wf:graph:root-snapshot"${disabled(busy || state.unavailable || !inspected.supported || !HEX40.test(inspected.revision))}>설정·토크나이저 받기</button></div></div>` : "";
  return ui`<section class="card graph-roots" id="model-roots" aria-labelledby="model-roots-title">
    <div class="section-title"><h3 id="model-roots-title">기반 모델 <span class="count">${state.roots.length}</span></h3></div>
    ${state.error ? `<p class="error-text root-error" role="status">${escapeHtml(state.error)}</p>` : ""}
    ${list ? `<ul class="root-list">${list}</ul>` : empty}
    <form id="root-pin-form" class="root-pin" novalidate aria-label="기반 모델 고르기"><fieldset class="graph-fieldset"${busy ? " disabled" : ""}>
      <p class="muted">Hugging Face에서 메타데이터만 조회합니다 · 가중치는 받지 않습니다.</p>
      <label class="field"><span>HF 모델 검색</span><input id="root-query" aria-label="HF 모델 검색" maxlength="120" value="${escapeHtml(state.query)}" placeholder="예: Qwen2.5 Instruct"></label>
      <div class="actions"><button type="button" data-action="wf:graph:root-search">모델 검색</button></div>
      ${results}
      <label class="field"><span>모델 ID</span><input id="root-model" aria-label="모델 ID" maxlength="193" value="${escapeHtml(state.model)}" placeholder="예: Qwen/Qwen2.5-0.5B-Instruct"></label>
      <div class="actions"><button type="button" data-action="wf:graph:root-inspect">버전 확인</button></div>
      ${pinned}</fieldset></form></section>`;
}

/** Reads the pin form's drafts (skipped while it is locked: the running task's result wins). */
export function captureRootsForm(): void {
  const form = document.querySelector<HTMLFormElement>("#root-pin-form");
  if (!form || form.querySelector("fieldset:disabled")) return;
  rootsState.query = form.querySelector<HTMLInputElement>("#root-query")?.value ?? rootsState.query;
  const model = form.querySelector<HTMLInputElement>("#root-model")?.value ?? rootsState.model;
  if (model.trim() !== rootsState.model.trim()) { rootsState.inspected = null; rootsState.pinned = null; }
  rootsState.model = model;
}

/** `model_roots_list` (read-only). */
export async function loadRoots(): Promise<void> {
  try {
    const result = await planning.roots();
    rootsState.loaded = true;
    if (result.status === "ok" && result.data && Array.isArray(result.data.roots)) { rootsState.roots = result.data.roots; rootsState.unavailable = false; return; }
    if (notAvailable(result)) { rootsState.unavailable = true; rootsState.roots = []; return; }
    rootsState.error = describe(result.issues);
  } catch (error) {
    rootsState.loaded = true;
    rootsState.error = error instanceof Error ? error.message : String(error);
  }
}

/** Legacy HF metadata search (no download). */
export async function searchModels(ctx: ViewContext): Promise<void> {
  const query = rootsState.query.trim();
  if (query.length < 2) { rootsState.error = tr("검색어를 두 글자 이상 입력하세요."); ctx.render(); return; }
  await ctx.task(tr("Hugging Face 모델 검색 중"), async () => {
    rootsState.error = null; rootsState.results = null;
    const found = await ctx.legacy<{ models?: { id?: unknown; downloads?: unknown; pipeline_tag?: unknown }[] }>("model_search", { query, sort: "downloads" });
    rootsState.results = (Array.isArray(found?.models) ? found.models : []).filter(model => typeof model?.id === "string").slice(0, 20)
      .map(model => ({ id: String(model.id), downloads: typeof model.downloads === "number" ? model.downloads : null, tag: String(model.pipeline_tag ?? tr("확인 필요")) }));
  });
}
export function pickModel(ctx: ViewContext, index: number): void {
  const picked = rootsState.results?.[index];
  if (!picked) return;
  rootsState.model = picked.id; rootsState.inspected = null; rootsState.pinned = null;
  ctx.render();
  document.querySelector<HTMLElement>("#root-model")?.focus();
}
/** Legacy HF metadata inspection: pins the immutable 40-hex revision the inspection returns. */
export async function inspectModel(ctx: ViewContext): Promise<void> {
  const model = rootsState.model.trim();
  if (!model) { rootsState.error = tr("모델 ID를 입력하세요."); ctx.render(); return; }
  await ctx.task(tr("모델 버전 확인 중"), async () => {
    rootsState.error = null; rootsState.inspected = null; rootsState.pinned = null;
    const found = await ctx.legacy<Record<string, unknown>>("model_inspect", { model });
    const revision = typeof found?.revision === "string" ? found.revision : "";
    rootsState.inspected = { repo_id: typeof found?.repo_id === "string" ? found.repo_id : model, revision,
      architecture: typeof found?.architecture === "string" ? found.architecture : "",
      supported: found?.supported === true && HEX40.test(revision),
      reasons: Array.isArray(found?.reasons) ? (found.reasons as unknown[]).map(String) : [] };
    if (rootsState.inspected.supported) rootsState.pinned = { repo_id: rootsState.inspected.repo_id, revision };
  });
}

/** "설정·토크나이저 받기": its notice dialog first; then `model_root_snapshot` and a fresh root list. */
export async function snapshotRoot(ctx: ViewContext, repoId: string, revision: string): Promise<void> {
  const answer = await openDialog({ title: tr("설정·토크나이저 받기"),
    bodyHtml: ui`<p><b>${escapeHtml(repoId)}</b> · 버전 <span class="mono">${escapeHtml(revision)}</span></p>
      <p>이 버전의 설정(config)과 토크나이저 파일만 이 PC에 받습니다. 학습 그래프의 기반 모델과 평가 기준의 토크나이저로 씁니다.</p><p>${tr(SNAPSHOT_NOTE)}</p>`,
    actions: [{ id: "back", label: tr("돌아가기") }, { id: "fetch", label: tr("받기"), primary: true }] });
  if (answer?.action !== "fetch") return;
  const back = focusRootLater(ctx, repoId, revision);
  let done = false;
  await ctx.task(tr("설정·토크나이저 받는 중"), async () => {
    rootsState.error = null;
    const result = await planning.rootSnapshot(repoId, revision);
    if (result.status !== "ok") {
      if (notAvailable(result)) rootsState.unavailable = true;
      throw new Error(describe(result.issues));
    }
    await loadRoots();
    rootsState.inspected = null; rootsState.pinned = null; rootsState.results = null; rootsState.model = "";
    ctx.notify(msg`${repoId}의 설정·토크나이저를 받았습니다. 가중치는 받지 않았습니다.`);
    done = true;
  });
  if (done) back();
}

/** After a check the button that started it is gone: the focus goes to the root's next action, else to the root
 * itself, only when the render left it on `body` and the user is still on the same page. */
function focusRootLater(ctx: ViewContext, repoId: string, revision: string): () => void {
  const page = ctx.page, arg = ctx.arg;
  return () => {
    if (ctx.page !== page || ctx.arg !== arg || (document.activeElement && document.activeElement !== document.body)) return;
    const item = document.querySelector<HTMLElement>(`#model-roots [data-root="${CSS.escape(rootKey({ repo_id: repoId, revision }))}"]`);
    (item?.querySelector<HTMLButtonElement>("button:not(:disabled)") ?? item)?.focus();
  };
}

/** "모듈 구조 확인": its own notice dialog; then `model_root_targets` (header-only) and a fresh root list. */
export async function inspectTargets(ctx: ViewContext, root: HFRootRef): Promise<void> {
  const answer = await openDialog({ title: tr("모듈 구조 확인"),
    bodyHtml: ui`<p><b>${escapeHtml(root.repo_id)}</b> · 버전 <span class="mono">${escapeHtml(root.revision)}</span></p>
      <p>${tr(TARGETS_NOTE)}</p><p>확인한 모듈 구조는 이 PC에 기록되어 LoRA 대상 모듈(lora_target: all)과 Full·Freeze 학습의 계획·모의 실행에 쓰입니다.</p>`,
    actions: [{ id: "back", label: tr("돌아가기") }, { id: "inspect", label: tr("확인"), primary: true }] });
  if (answer?.action !== "inspect") return;
  const back = focusRootLater(ctx, root.repo_id, root.revision);
  let done = false;
  await ctx.task(tr("모듈 구조 확인 중"), async () => {
    rootsState.error = null;
    const result = await planning.rootTargets(root.repo_id, root.revision);
    if (result.status !== "ok") {
      if (notAvailable(result)) rootsState.unavailable = true;
      throw new Error(describe(result.issues));
    }
    await loadRoots();
    ctx.notify(msg`${root.repo_id}의 모듈 구조를 확인했습니다. 가중치는 받지 않았습니다.`);
    done = true;
  });
  if (done) back();
}
