import { invoke } from "@tauri-apps/api/core";
import { version } from "../package.json";
import { button, errorText, field, filterRuns, focusKey, focusMatches, formatDate, icon, isGraphJob, progressView, restoreFocus, select } from "./workbench";
import { methodSelect, sourceField, sourceSelect, stageSelect, trainingOptions, type FieldSource, type RecipeFieldContext } from "./components/recipe-fields";
import { planning as planningApi, projects as projectApi } from "./api";
import { NAV_GROUPS, navModel, type NavItem } from "./nav-model";
import { createProjectStore } from "./project-store";
import { viewById, views as workflowViews } from "./view-registry";
import type { Catalog, View, ViewContext } from "./view-types";
import { createTutorial, rememberTutorial, tutorialSeen } from "./tutorial";
import { TUTORIAL_DATASET } from "./tutorial-steps";
import { connectionResult, destinationChange, GPU_POLICY_MESSAGE, inventoryTable, isServer, jobDestination, jobDestinationIssue,
  MAX_GPUS_PER_JOB, prepareApproval, prepareIssue, puttyKeyPath, releaseApproval, reservationMatches, reservationTable, resourcesFromFacts,
  sameSelection, selectedGpus, selectionPolicyIssue, serverAddress, serverBridge, serverConfigured, settingsRoute, shortIdentifier,
  transferApproval } from "./server-connection";
import "./style.css";

type Obj = Record<string, any>;
const root = document.querySelector<HTMLDivElement>("#app")!;
const state: Obj = {
  page: "overview", arg: null, settings: {}, catalog: { templates: [], profiles: [], datasets: [] },
  config: null, jobs: [], graphJobs: 0, plan: null, logs: "", experiments: [], runs: [], nextToken: null,
  selected: [], experiment: "16", busy: "", notice: "", error: "", active: "", comparison: null,
  preview: null, datasetDraft: {}, historyQuery: "", historyStatus: "", progress: null,
  jobCapabilities: null, modelMetadata: null, connectionReport: null, runtimeReport: null, settingsDraft: null, s3SettingsDraft: null,
  capabilities: null, capabilityPending: false, s3Draft: { bucket: "", prefix: "", key: "", version_id: "", dataset_id: "s3_dataset", version: "1", kind: "instruction" }, s3Listing: null, s3Object: null,
  hubDraft: {query: "Qwen3.5", model: "", sort: "downloads"}, hubModels: [], hubInspection: null,
  serverInspection: null, gpuDraft: null, reservations: null,
};
const pages: Record<string, string> = {
  overview: "작업 공간", connections: "연결 설정", datasets: "데이터", training: "학습 설정",
  jobs: "실행 관리", history: "기록 비교",
};
// Project drafts of the workflow views (D4); a successful connection save invalidates held plans (D8).
const store = createProjectStore({
  get: (projectId, revision) => projectApi.get(projectId, revision),
  save: (spec, expected) => projectApi.save(spec, expected),
  validate: (projectId, revision) => planningApi.validate(projectId, revision),
});
// The only legacy actions a workflow view may call (read-only catalog and HF metadata, D3, D13).
const VIEW_LEGACY_ACTIONS = new Set(["catalog", "model_search", "model_inspect"]);
const ctx: ViewContext = {
  get page() { return state.page; }, get arg() { return state.arg; }, get settings() { return state.settings; },
  get catalog() { return state.catalog as Catalog; }, get busy() { return Boolean(state.busy); }, store,
  hasPage: id => Object.hasOwn(pages, id) || viewById(id) !== undefined,
  navigate: (page, arg) => action(arg === undefined ? `nav:${page}` : `nav:${page}:${arg}`),
  render: () => renderKeepingDrafts(),
  patch: (regionId, html, options) => patch(regionId, html, options),
  task: (label, work) => task(label, work),
  notify(message) { state.notice = message; if (!state.busy) renderKeepingDrafts(); },
  fail(message) { state.error = message; if (!state.busy) renderKeepingDrafts(); },
  async legacy<T>(name: string, data: object = {}) {
    if (!VIEW_LEGACY_ACTIONS.has(name)) throw new Error("허용하지 않은 작업");
    return await api(name, data as Obj) as T;
  },
  reloadCatalog: () => catalog(),
};
const defaults: Obj = {
  MLFLOW_TRACKING_URI: "https://mlflow.kbiofoundry.kr",
  FACTORY_MLFLOW_ARTIFACT_LOCATION: "mlflow-artifacts:/llm-tuning-factory",
  MLFLOW_OIDC_TOKEN_URL: "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/token",
  MLFLOW_OIDC_CLIENT_ID: "mlflow", auth_mode: "oidc", provider: "existing_runpod", server_ssh_port: "22",
};
const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const num = (v: unknown) => typeof v === "number" ? v.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "—";
const statusName: Obj = { QUEUED: "대기", PENDING: "준비", PROVISIONING: "전송 준비", RUNNING: "학습 중",
  EVALUATING: "평가 중", COMPLETED: "완료", FINISHED: "완료", FAILED: "실패", CANCELED: "취소", KILLED: "취소" };
function badge(status: string) { return `<span class="badge ${/COMPLETED|FINISHED/.test(status) ? "green" : /FAILED/.test(status) ? "red" : ""}">${esc(statusName[status] || status)}</span>`; }
const descriptions: Obj = {
  overview: "최근 실행과 다음 작업을 한곳에서 확인하세요.",
  connections: "학습에 사용할 GPU와 공용 기록 저장소를 연결합니다.",
  datasets: "데이터를 먼저 살펴보고, 검증한 버전을 학습에 사용하세요.",
  training: "목적에 맞는 데이터, 모델, 학습 조건을 설정합니다.",
  jobs: "실행 상태부터 실제 학습 지표, 체크포인트까지 확인합니다.",
  history: "같은 기준으로 실험을 살펴보고 다음 학습을 결정하세요.",
};
const taskNames: Obj = { paper_extraction: "논문 정보 추출", labnote_generation: "실험노트 생성", workflow_classification: "업무 분류" };
async function api(action: string, data: Obj = {}) {
  return invoke<any>("factory_action", { action, data });
}
const gpuServer = serverBridge(api);
// Tasks may overlap (navigation stays available and a view's `enter` starts its own read), so busy lasts until the last
// running task ends; `idle()` lets a view's `enter` wait for the running tasks before it starts its own (I1).
const running: { label: string }[] = [];
// A view's `enter` that waited for running tasks keeps the notice or error they left (a save's result) visible.
let keepMessages = false;
// Every navigation gets the next number: a visit's `enter` that waited for running tasks runs only while it is still the
// latest navigation (visiting a page again meanwhile would otherwise run each visit's `enter` once the tasks end).
let navigations = 0;
let idleWaiters: (() => void)[] = [];
const idle = () => running.length ? new Promise<void>(resolve => idleWaiters.push(resolve)) : Promise.resolve();
async function task(label: string, operation: () => Promise<void>) {
  // The control that started the task (e.g. the opener a confirm dialog returned focus to) gets it back afterwards,
  // but only on the page the task started from.
  const opener = focusKey(document.activeElement);
  const page = state.page, arg = state.arg;
  captureDrafts();
  const token = { label };
  running.push(token);
  if (!keepMessages) { state.error = ""; state.notice = ""; }
  state.busy = label; render();
  try { await operation(); }
  catch (error) { state.error = errorText(error); }
  finally {
    running.splice(running.indexOf(token), 1);
    state.busy = running.at(-1)?.label ?? "";
    // The user moved on: what they typed on this page since is newer than state, and keeps its focus. (On the task's
    // own page its result wins; the workflow views and the connection form lock their inputs while busy.)
    const samePage = state.page === page && state.arg === arg;
    if (!samePage) captureDrafts();
    const keep = samePage ? null : focusSpot();
    render();
    if (!document.activeElement || document.activeElement === document.body) {
      if (samePage) restoreFocus(document, opener); else keep?.();
    }
    if (!running.length) { const waiters = idleWaiters; idleWaiters = []; waiters.forEach(resolve => resolve()); }
  }
}
/** Where the focus is, so a full render can put it back on the same control with its text selection (the control's
 * focus key, else its draft attribute within its form). */
function focusSpot(): () => void {
  const active = document.activeElement;
  if (!(active instanceof HTMLElement) || !root.contains(active)) return () => {};
  const key = focusKey(active);
  const attribute = ["data-key", "data-config", "data-recipe", "name"].find(name => active.hasAttribute(name));
  const form = active.closest("form[id]")?.id;
  const selector = key || !attribute ? null
    : `${form ? `#${CSS.escape(form)} ` : ""}[${attribute}="${CSS.escape(active.getAttribute(attribute)!)}"]`;
  const text = active instanceof HTMLTextAreaElement || active instanceof HTMLInputElement;
  const selection = text ? (() => { try { return [active.selectionStart, active.selectionEnd]; } catch { return null; } })() : null;
  return () => {
    if (key) restoreFocus(document, key);
    else if (selector) document.querySelector<HTMLElement>(selector)?.focus({ preventScroll: true });
    const next = document.activeElement;
    if (selection && (next instanceof HTMLTextAreaElement || next instanceof HTMLInputElement))
      try { next.setSelectionRange(selection[0], selection[1]); } catch { /* inputs without a text selection */ }
  };
}
function captureDrafts() {
  flushDeferredRender();
  const hub = document.querySelector<HTMLFormElement>("#hub-form");
  if (hub) state.hubDraft = settingsFromForm(hub);
  const hf = document.querySelector<HTMLFormElement>("#hf-settings-form");
  if (hf) state.hfDraft = settingsFromForm(hf);
  const settings = document.querySelector<HTMLFormElement>("#settings-form");
  // Merge so drafts of fields hidden by the provider or MLflow auth choice survive switching back.
  if (settings) state.settingsDraft = { ...state.settingsDraft, ...settingsFromForm(settings) };
  const gpus = document.querySelectorAll<HTMLInputElement>("[data-gpu]");
  if (gpus.length) state.gpuDraft = [...gpus].filter(el => el.checked).map(el => el.dataset.gpu!);
  const s3Settings = document.querySelector<HTMLFormElement>("#s3-settings-form");
  if (s3Settings) state.s3SettingsDraft = settingsFromForm(s3Settings);
  const s3 = document.querySelector<HTMLFormElement>("#s3-form");
  if (s3) state.s3Draft = { ...settingsFromForm(s3), synthetic: (s3.elements.namedItem("synthetic") as HTMLInputElement).checked, export_allowed: (s3.elements.namedItem("export_allowed") as HTMLInputElement).checked };
  const data = document.querySelector<HTMLFormElement>("#dataset-form");
  if (data) state.datasetDraft = { ...settingsFromForm(data),
    synthetic: (data.elements.namedItem("synthetic") as HTMLInputElement).checked,
    export_allowed: (data.elements.namedItem("export_allowed") as HTMLInputElement).checked };
  const checkpoint = document.querySelector<HTMLInputElement>("#checkpoint");
  if (checkpoint) state.checkpoint = checkpoint.value;
  // A view whose capture throws must not take boot or the legacy tasks down (the render guard's rule for hooks).
  for (const view of workflowViews) {
    try { view.capture?.(ctx); } catch (error) { console.error(error); }
  }
}
function commandSearch() {
  if (document.querySelector("dialog[open]")) return;
  captureDrafts(); captureConfig();
  const dialog = document.createElement("dialog"); dialog.className = "command-dialog";
  dialog.setAttribute("aria-label", "바로 가기");
  dialog.innerHTML = `<label>${icon("search", 18)}<input aria-label="페이지 검색" placeholder="어디로 이동할까요?" autofocus></label><div class="command-options"></div><div class="command-hint">Enter로 선택 · Esc로 닫기</div>`;
  const input = dialog.querySelector("input")!;
  const options = dialog.querySelector(".command-options")!;
  const update = () => {
    // The sidebar pages only (D1), in sidebar order.
    options.innerHTML = navModel(workflowViews).filter(item => item.label.includes(input.value.trim())).map(item =>
      `<button data-command="${esc(item.id)}">${icon(item.icon)}<span>${esc(item.label)}</span>${icon("arrow", 13)}</button>`).join("");
    options.querySelectorAll<HTMLButtonElement>("[data-command]").forEach(el => el.addEventListener("click", () => {
      const id = el.dataset.command!; dialog.close(); dialog.remove(); void action(id === "tutorial" ? "tutorial" : "nav:" + id);
    }));
  };
  input.addEventListener("input", update);
  input.addEventListener("keydown", e => {
    if (e.key === "Enter") { e.preventDefault(); options.querySelector<HTMLButtonElement>("button")?.click(); }
    if (e.key === "ArrowDown") { e.preventDefault(); options.querySelector<HTMLButtonElement>("button")?.focus(); }
  });
  dialog.addEventListener("close", () => dialog.remove()); document.body.append(dialog); update(); dialog.showModal();
}
function captureConfig() {
  flushDeferredRender();
  if (!state.config) return;
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-config]").forEach(el => {
    const parts = el.dataset.config!.split(".");
    let target = state.config;
    for (const part of parts.slice(0, -1)) target = target[part] ??= {};
    target[parts.at(-1)!] = el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked : el.value === "" && el.dataset.optional ? null : el.dataset.number ? Number(el.value) : el.value;
  });
}
// The training form's fields come from the shared recipe fields (D17), reading the single-job config.
const configSource: FieldSource = { attr: "data-config", value(path) { let v = state.config; for (const p of path.split(".")) v = v?.[p]; return v; } };
const trainingFields = (): RecipeFieldContext => ({ ...configSource, stages: state.catalog.training_stages || [], capabilities: state.capabilities, recipeMode: false });
const configField = (label: string, path: string, type = "text", help = "") => sourceField(configSource, label, path, type, help);
const configSelect = (label: string, path: string, options: [string, string][]) => sourceSelect(configSource, label, path, options);
function modelMetadataView() {
  const metadata = state.modelMetadata;
  const c = state.config;
  if (!metadata || metadata.model !== c.base_model || metadata.model_revision !== c.model_revision || metadata.tokenizer_revision !== c.tokenizer_revision) return "";
  return `<div class="model-metadata compatibility"><h3>고정 버전 메타데이터 · ${metadata.metadata_verified ? "확인됨" : "확인 필요"}</h3><p>아키텍처 ${esc(metadata.architecture || "확인 필요")} · 레이어 ${num(metadata.hidden_layers)} · 권장 정밀도 ${esc(metadata.recommended_precision || "확인 필요")}</p>${typeof metadata.max_length === "number" ? `<p>모델 최대 길이 ${num(metadata.max_length)} 토큰 · 현재 설정을 자동으로 변경하지 않습니다.</p>` : ""}<small>설정은 직접 조정하고 호환성 검사 결과를 확인하세요.</small></div>`;
}
function modelHubView() {
  const d = state.hubDraft, inspected = state.hubInspection;
  return `<section class="card"><div class="section-title"><h3>Hugging Face 모델 추가</h3><span class="badge">검색 · 버전 고정</span></div><p>최신 모델을 검색하거나 저장소 ID로 추가합니다. 여기서는 메타데이터만 확인하며 가중치는 학습 승인 후 GPU에서 받습니다.</p>
  <form id="hub-form"><div class="grid">${field("HF 모델 검색", "query", d.query)}${select("검색 정렬", "sort", d.sort, [["downloads", "다운로드 순"], ["lastModified", "최신 업데이트 순"]])}${field("직접 입력할 모델 ID", "model", d.model, "text", "예: Qwen/Qwen3.5-0.8B")}</div><div class="actions">${button("모델 검색", "hub-search")}${button("입력한 ID 확인", "hub-inspect")}</div></form>
  ${state.hubModels.length ? `<div class="table-scroll"><table><thead><tr><th>모델</th><th>종류</th><th>다운로드</th><th>추가</th></tr></thead><tbody>${state.hubModels.map((model: Obj, index: number) => `<tr><td>${esc(model.id)}<small>${esc(model.last_modified || "")}</small></td><td>${esc(model.pipeline_tag || "확인 필요")}</td><td>${num(model.downloads)}</td><td>${button("확인", "hub-inspect:" + index)}</td></tr>`).join("")}</tbody></table></div>` : ""}
  ${inspected ? `<div class="compatibility ${inspected.supported ? "" : "error"}"><h3>모델 확인 · ${esc(inspected.repo_id)}</h3><p>구조 ${esc(inspected.architecture || "확인 불가")} · 템플릿 ${esc(inspected.template || "확인 필요")}</p><small class="mono">${esc(inspected.revision)}</small><p>${inspected.supported ? "현재 엔진에서 설정 가능한 구조입니다. 실제 GPU 검증은 별도이며 텍스트 데이터만 지원합니다." : esc((inspected.reasons || []).join(" · "))}</p><button type="button" data-action="hub-import" class="primary" ${inspected.supported ? "" : "disabled"}>이 모델 추가·선택</button></div>` : ""}</section>`;
}
function normalizeConfig() {
  if (!state.config) return;
  state.config.training_stage ??= "sft";
  state.config.model_profile ??= null;
  state.config.quantization_bit ??= 4;
  state.config.finetuning_options = { lora_alpha: null, lora_dropout: 0, use_rslora: false, use_dora: false, loraplus_lr_ratio: null, loraplus_lr_embedding: 0.000001, pissa_init: false, pissa_iter: 16, pissa_convert: false, freeze_trainable_layers: 2, freeze_trainable_modules: "all", freeze_extra_modules: null, oft_rank: 0, oft_block_size: 32, oft_target: "all", module_dropout: 0, ...state.config.finetuning_options };
  state.config.stage_options = { pref_loss: "sigmoid", pref_beta: 0.1, pref_ftx: 0, pref_bco_weight: 0, dpo_label_smoothing: 0, kto_chosen_weight: 1, kto_rejected_weight: 1, simpo_gamma: 0.5, ppo_buffer_size: 1, ppo_epochs: 4, ppo_score_norm: false, ppo_target: 6, ppo_whiten_rewards: false, reward_model: null, reward_model_type: "lora", reward_model_sha256: null, ...state.config.stage_options };
}
let capabilityRequest = 0;
async function checkCapabilities() {
  normalizeConfig();
  const request = ++capabilityRequest;
  const config = structuredClone(state.config);
  state.capabilityPending = true;
  try {
    const result = await api("capabilities", { config });
    if (request === capabilityRequest && JSON.stringify(state.config) === JSON.stringify(config)) state.capabilities = result;
    return result;
  } finally { if (request === capabilityRequest) state.capabilityPending = false; }
}
function invalidatePlan() {
  state.plan = null; state.capabilities = null;
  document.querySelector(".plan")?.remove();
  document.querySelector<HTMLButtonElement>('#training-form button[type="submit"]')?.setAttribute("disabled", "");
}
async function configChanged(rerender = false, captured = false) {
  if (!captured) captureConfig(); invalidatePlan();
  if (rerender) render();
  try { await checkCapabilities(); } catch (error) { state.error = errorText(error); }
  if (state.page === "training") render();
}
function compatibilityView() {
  const result = state.capabilities;
  if (state.capabilityPending || !result) return '<div class="compatibility pending" aria-live="polite">모델·단계·데이터 호환성을 확인하고 있습니다.</div>';
  return `<section class="compatibility ${result.valid ? "ready" : "blocked"}" aria-label="설정 호환성"><div class="section-title"><h3>${result.valid ? "설정 호환성 확인됨" : "설정 수정이 필요합니다"}</h3><span class="badge ${result.valid ? "green" : "red"}">${result.valid ? "실행 계획 확인 가능" : "실행 불가"}</span></div>
    ${(result.errors || []).map((message: string) => `<p class="error-text">${esc(message)}</p>`).join("")}
    ${(result.warnings || []).map((message: string) => `<p>${esc(message)}</p>`).join("")}
    ${result.model?.verification_scope ? `<p>GPU 검증 이력: ${esc(result.model.verification_scope)}</p>` : ""}<small>필요한 데이터 종류: ${esc(result.dataset_kind || "학습 단계에 따른 데이터")} · 학습 엔진 ${esc(result.engine_version || "확인 필요")} · ${result.resume_supported === false ? "이 조합은 체크포인트 재개를 지원하지 않습니다." : "재개 시 완전한 체크포인트가 필요합니다."}<br>설정 호환성은 실제 GPU 학습 성공이나 모든 모델의 검증을 보장하지 않습니다.</small></section>`;
}
const datasetKinds: [string, string][] = [["instruction", "지시·대화 · SFT"], ["text", "텍스트 · PT"], ["preference", "선호 쌍 · RM/DPO"], ["kto", "선호 라벨 · KTO"], ["prompt", "프롬프트 · PPO"]];
function dataSize(value: unknown) {
  if (typeof value !== "number") return "—";
  if (value < 1024) return `${num(value)} B`;
  if (value < 1024 ** 2) return `${num(value / 1024)} KiB`;
  return `${num(value / 1024 ** 2)} MiB`;
}
function s3Connections() {
  const s = { ...state.settings, ...(state.s3SettingsDraft || {}) };
  return `<form id="s3-settings-form"><section class="card"><div class="section-title"><h3>S3 데이터 저장소</h3><span class="badge">S3 → 내 PC</span></div><p>AWS S3 또는 S3 호환 저장소를 연결합니다. 가져온 데이터는 PC에 불변 사본으로 등록되며 S3 키는 GPU에 전달하지 않습니다.</p><div class="grid">
    ${field("S3 Endpoint URL", "S3_ENDPOINT_URL", s.S3_ENDPOINT_URL, "url", "AWS 기본 endpoint는 비워 두세요. S3 호환 저장소는 경로·서명·인증값 없는 HTTPS 기본 주소만 입력하세요.")}
    ${field("S3 Region", "S3_REGION", s.S3_REGION || "us-east-1")}
    ${select("S3 주소 방식", "S3_ADDRESSING_STYLE", s.S3_ADDRESSING_STYLE || "auto", [["auto", "자동"], ["virtual", "Virtual hosted"], ["path", "Path style · MinIO 등"]])}
    ${field("S3 Access Key ID", "S3_ACCESS_KEY_ID", s.S3_ACCESS_KEY_ID || "", "password", s.S3_ACCESS_KEY_ID_saved ? "보안 저장됨 · 변경할 때만 입력" : "읽기 권한의 키를 권장합니다.")}
    ${field("S3 Secret Access Key", "S3_SECRET_ACCESS_KEY", s.S3_SECRET_ACCESS_KEY || "", "password", s.S3_SECRET_ACCESS_KEY_saved ? "보안 저장됨" : "Windows 자격 증명 저장소에 보관합니다.")}
    ${field("S3 Session Token", "S3_SESSION_TOKEN", s.S3_SESSION_TOKEN || "", "password", s.S3_SESSION_TOKEN_saved ? "보안 저장됨" : "임시 자격 증명을 사용할 때 입력하세요.")}
    </div><div class="actions"><button class="primary" type="submit">S3 연결 정보 저장</button></div><p class="muted">저장된 인증값 삭제는 RunPod·MLflow·S3의 인증값을 함께 삭제합니다. 목록 권한이 없어도 데이터 화면에서 정확한 객체 key로 확인할 수 있습니다.</p></section></form>`;
}
function s3Datasets() {
  const d = state.s3Draft;
  const object = state.s3Object;
  const listing = state.s3Listing;
  return `<section class="card"><div class="section-title"><h3>S3에서 데이터 가져오기</h3><span class="badge">JSON · JSONL</span></div><p>버킷과 key를 지정해 S3에서 PC로 사본을 가져옵니다. 파일 한도는 32 MiB입니다. 현재 전체 파일 가져오기를 지원하며 무제한 스트리밍은 지원하지 않습니다.</p>
  <form id="s3-form"><div class="grid">${field("S3 버킷", "bucket", d.bucket)}${field("S3 Prefix · 폴더", "prefix", d.prefix)}
  ${field("S3 객체 key · 직접 입력 가능", "key", d.key, "text", "목록 권한이 없으면 key를 직접 입력하고 객체 확인을 누르세요.")}${field("S3 Version ID · 선택", "version_id", d.version_id)}
  </div><div class="actions">${button("S3 목록 조회", "s3-list")}${button("선택한 객체 확인", "s3-head")}</div>
  ${listing ? `<div class="s3-list table-scroll"><table><thead><tr><th>객체·폴더</th><th>크기</th><th>수정 시각</th><th></th></tr></thead><tbody>
  ${(listing.common_prefixes || []).map((prefix: any, i: number) => `<tr><td>${esc(typeof prefix === "string" ? prefix : prefix.prefix)}</td><td>폴더</td><td>—</td><td>${button("폴더 열기", "s3-folder:" + i, "quiet")}</td></tr>`).join("")}
  ${(listing.objects || []).map((item: Obj, i: number) => `<tr><td class="object-key">${esc(item.key)}</td><td>${dataSize(item.size)}</td><td>${item.last_modified ? esc(formatDate(Date.parse(item.last_modified) / 1000)) : "—"}</td><td>${button("객체 선택", "s3-object:" + i, "quiet")}</td></tr>`).join("")}
  </tbody></table>${!(listing.objects || []).length && !(listing.common_prefixes || []).length ? '<div class="empty">이 prefix에서 조회된 객체가 없습니다.</div>' : ""}</div>${listing.next_token ? `<div class="actions">${button("다음 S3 목록", "s3-next")}</div>` : ""}` : ""}
  ${object ? `<div class="s3-object"><h3>가져올 객체</h3><dl class="object-details"><div><dt>객체</dt><dd>${esc(object.bucket || d.bucket)}/${esc(object.key || d.key)}</dd></div><div><dt>크기</dt><dd>${dataSize(object.size)}</dd></div><div><dt>Version ID</dt><dd>${esc(object.version_id || "버전 없음 · ETag 조건부 읽기")}</dd></div><div><dt>ETag · 내용 해시와 구분</dt><dd>${esc(object.etag)}</dd></div>${object.import_max_bytes ? `<div><dt>파일 가져오기 한도</dt><dd>${dataSize(object.import_max_bytes)}</dd></div>` : ""}${object.free_bytes ? `<div><dt>PC 사용 가능 공간</dt><dd>${dataSize(object.free_bytes)}</dd></div>` : ""}</dl><p class="muted">확인한 버전 또는 ETag가 바뀌면 가져오기를 중단합니다. 실제 SHA256과 데이터 형식 검사를 통과한 사본만 등록합니다.</p></div>` : ""}
  <div class="grid s3-registration">${field("S3 등록 데이터 ID", "dataset_id", d.dataset_id)}${field("S3 등록 버전", "version", d.version)}${select("S3 데이터 종류", "kind", d.kind || "instruction", datasetKinds)}</div>
  <label class="check"><input name="synthetic" type="checkbox" ${d.synthetic ? "checked" : ""}> 가져올 파일은 더미·합성 데이터입니다</label>
  <label class="check"><input name="export_allowed" type="checkbox" ${d.export_allowed ? "checked" : ""}> 등록한 사본을 선택한 GPU로 전송할 수 있습니다</label>
  <div class="actions"><button type="button" data-action="s3-import" class="primary" ${!object ? "disabled" : ""}>S3에서 PC로 가져오기</button></div><p class="muted">가져오기 동의와 RunPod 학습 전송 동의는 별도입니다. 여기서는 GPU를 사용하지 않습니다.</p></form></section>`;
}
async function approve(title: string, body: string): Promise<boolean> {
  const dialog = document.createElement("dialog");
  dialog.innerHTML = `<h2>${esc(title)}</h2><p class="confirmation">${esc(body)}</p><div class="actions"><button data-no>돌아가기</button><button data-yes class="primary">내용 확인 · 진행</button></div>`;
  document.body.append(dialog); dialog.showModal();
  return new Promise(resolve => {
    const done = (answer: boolean) => { dialog.close(); dialog.remove(); resolve(answer); };
    dialog.querySelector("[data-no]")!.addEventListener("click", () => done(false));
    dialog.querySelector("[data-yes]")!.addEventListener("click", () => done(true));
    dialog.addEventListener("cancel", e => { e.preventDefault(); done(false); });
  });
}
function tutorialCard() {
  if (tutorial.active() || tutorialSeen()) return "";
  return `<section class="tutorial-card" aria-label="튜토리얼 안내"><div><h3>처음 사용하시나요?</h3><p>약 7분 튜토리얼로 연결 → 데이터 → 학습 설정 → 실행 계획 순서를 실습하고, 프로젝트·데이터 가져오기·학습 그래프·그래프 실행 화면을 둘러보세요. GPU를 사용하거나 데이터를 전송하지 않습니다.</p></div><div class="actions">${button("다음에 하기", "tutorial-dismiss")}${button("튜토리얼 시작", "tutorial", "primary")}</div></section>`;
}
function overview() {
  const running = state.jobs.filter((j: Obj) => ["RUNNING", "PROVISIONING", "EVALUATING"].includes(j.status)).length;
  const complete = state.jobs.filter((j: Obj) => j.status === "COMPLETED").length;
  const saved = state.settings, server = isServer(saved);
  const steps = [
    { page: "connections", label: "연결 정보 설정", text: server && selectionPolicyIssue(saved) ? GPU_POLICY_MESSAGE : "RunPod 또는 GPU 서버와 MLflow 연결을 확인하세요.", done: server ? serverConfigured(saved) : Boolean(saved.RUNPOD_API_KEY_saved && saved.pod_id) },
    { page: "datasets", label: "데이터 버전 등록", text: "학습·검증 데이터를 준비하세요.", done: state.catalog.datasets.some((d: Obj) => d.dataset_id !== TUTORIAL_DATASET.id) },
    { page: "training", label: "첫 학습 준비", text: "업무와 모델을 선택하고 계획을 확인하세요.", done: state.jobs.length > 0 },
  ];
  return `${tutorialCard()}<div class="summary-strip"><div><span class="status-dot blue"></span>진행 중 <b>${running}</b></div><div>완료한 실행 <b>${complete}</b></div><div>데이터 버전 <b>${state.catalog.datasets.length}</b></div><div>저장한 설정 <b>${state.catalog.profiles.length}</b></div></div>
  <div class="workspace-columns"><div>${slotPanels("overviewPanel")}<section class="panel"><div class="panel-heading"><h3>최근 실행 <span class="count">${state.jobs.length}</span></h3>${button("전체 보기 " + icon("arrow", 13), "nav:jobs", "quiet")}</div>
  ${state.jobs.length ? `<div class="table-scroll"><table><thead><tr><th>실험</th><th>상태</th><th>데이터</th><th>업데이트</th></tr></thead><tbody>${state.jobs.slice(0, 7).map((j: Obj) => `<tr><td><button class="row-link" data-action="job:${esc(j.id)}">${esc(j.manifest.config.ai_id)}</button><small class="mono">${esc(j.manifest.config.base_model.split("/").at(-1))}</small></td><td>${badge(j.status)}</td><td>${esc(j.manifest.config.dataset_id)}<small>v${esc(j.manifest.config.dataset_version)}</small></td><td>${formatDate(j.updated)}</td></tr>`).join("")}</tbody></table></div>` :
  `<div class="empty-workspace"><div class="empty-symbol">${icon("training", 28)}</div><h3>첫 번째 실험을 시작해 보세요</h3><p>데이터와 모델을 고르면 실행 전에<br>설정과 예상 비용을 확인할 수 있습니다.</p>${button(icon("plus") + " 새 학습 설정", "nav:training", "primary")}<small>계획 확인만으로 GPU가 실행되지는 않습니다.</small></div>`}${graphJobsNote()}</section>
  <section class="panel task-library"><div class="panel-heading"><h3>예제 시작점 · 선택</h3><span class="subtle">설정을 불러와 자유롭게 수정하세요</span></div>
  ${state.catalog.templates.map((t: Obj, i: number) => `<button class="template-row" data-action="template:${i}"><span class="template-icon">${icon(i === 0 ? "file" : i === 1 ? "folder" : "overview", 19)}</span><span><b>${esc(taskNames[t.name] || t.name)}</b><small>${esc(t.config.workflow_id)}</small></span><span class="model-tag">LoRA</span>${icon("chevron", 14)}</button>`).join("")}</section></div>
  <div class="workspace-rail"><section class="panel" data-tour="overview-setup"><div class="panel-heading"><h3>시작하기</h3><span class="count">${steps.filter(s => s.done).length} / 3</span></div><div class="setup-list">${steps.map(s => `<button data-action="nav:${s.page}" class="setup-row"><span class="setup-check ${s.done ? "done" : ""}">${s.done ? icon("check", 12) : ""}</span><span><b>${s.label}</b><small>${s.text}</small></span>${icon("chevron", 12)}</button>`).join("")}</div></section>
  <section class="panel"><div class="panel-heading"><h3>연결 정보</h3>${button("설정", "nav:connections", "quiet")}</div><dl class="connection-list"><div><dt>${icon("connections")} ${server ? "GPU 서버" : "RunPod"}</dt><dd>${server ? (saved.server_ssh_host ? esc(serverAddress(saved, false)) : "미설정") : saved.pod_id ? esc(saved.pod_id) : "미설정"}</dd></div><div><dt>${icon("history")} MLflow</dt><dd>${state.settings.MLFLOW_TRACKING_URI ? "주소 저장됨" : "미설정"}</dd></div></dl><p class="panel-note">저장 상태입니다. 실제 연결은 연결 설정에서 검사하세요.</p></section>
  <p class="rail-note">내 PC에서 작업을 관리하고<br>배정받은 RunPod 또는 GPU 서버에서 학습합니다.<br>실행 기록은 공용 MLflow에 모입니다.</p></div></div>`;
}
function connections() {
  const s = { ...defaults, ...state.settings, ...(state.settingsDraft || {}) };
  const server = isServer(s), auth = s.auth_mode || "oidc";
  // Remote checks use saved settings; an unsaved provider switch would target the other destination.
  const pending = server !== isServer(state.settings);
  const operator = field("기록에 표시할 이름", "FACTORY_OPERATOR", s.FACTORY_OPERATOR);
  // Native settings refuse a submitted .ppk path, so a stored one would block every save of this form.
  const keyHelp = (path: unknown) => "OpenSSH 형식·암호 없는 개인키 파일 경로를 선택합니다. 키 내용은 입력하지 마세요." + (puttyKeyPath(path)
    ? '<br><span class="warning-text">PuTTY .ppk 키는 지원하지 않습니다. OpenSSH 형식(암호 없는 키)으로 변환한 키 파일로 바꿔야 연결 정보를 저장할 수 있습니다.</span>' : "");
  const runpod = `<section class="card" data-tour="connection-fields"><div class="section-title"><h3>RunPod 연결</h3><span class="badge">기존 Pod 사용</span></div><div class="grid">
  ${field("RunPod API 키", "RUNPOD_API_KEY", s.RUNPOD_API_KEY || "", "password", s.RUNPOD_API_KEY_saved ? "보안 저장됨 · 변경할 때만 입력" : "API 키는 Windows 자격 증명 저장소에 보관합니다.")}
  ${field("Pod ID", "pod_id", s.pod_id)}
  ${field("SSH 개인키 파일", "ssh_key_file", s.ssh_key_file, "text", keyHelp(s.ssh_key_file))}
  <div class="field"><span>키 파일 선택</span>${button("파일 찾기", "pick:key")}</div>
  ${field("SSH 서버 지문", "ssh_fingerprint", s.ssh_fingerprint, "text", "신뢰할 수 있는 경로로 확인한 SHA256:… 지문을 입력하세요.")}
  ${operator}
  </div><p class="muted">Pod는 RunPod에서 직접 시작합니다. 앱은 기존 Pod를 자동 시작·종료·삭제하지 않습니다.</p></section>`;
  const sshServer = `<section class="card" data-tour="connection-fields"><div class="section-title"><h3>GPU 서버 연결</h3><span class="badge">SSH 직접 실행</span></div><div class="grid">
  ${field("서버 주소", "server_ssh_host", s.server_ssh_host, "text", "예: gpu.example.org 또는 IP 주소")}
  ${field("SSH 포트", "server_ssh_port", s.server_ssh_port || "22")}
  ${field("접속 계정", "server_ssh_username", s.server_ssh_username, "text", "root 계정이 아니어도 됩니다.")}
  ${field("SSH 개인키 파일", "server_ssh_key_file", s.server_ssh_key_file, "text", keyHelp(s.server_ssh_key_file))}
  <div class="field"><span>키 파일 선택</span>${button("파일 찾기", "pick:server-key")}</div>
  ${field("SSH 서버 지문", "server_ssh_fingerprint", s.server_ssh_fingerprint, "text", "관리자가 신뢰할 수 있는 경로로 전달한 SHA256:… 값만 입력하세요.")}
  ${field("작업 폴더 · 절대 경로", "server_remote_root", s.server_remote_root, "text", "예: /home/USERNAME/factory (영문·숫자·_ . / - 만 사용). 앱의 학습 환경·캐시·작업 파일이 이 폴더 아래에 만들어지며 서버의 Python은 변경하지 않습니다.")}
  ${field("시간당 비용 (USD) · 서버 전체", "server_total_hourly_cost_usd", s.server_total_hourly_cost_usd, "text", "내부 서버는 0을 입력하세요. 클라우드 요금 조회 결과가 아니라 직접 입력한 값입니다.")}
  ${operator}
  </div><p class="muted">RunPod API 키와 Pod ID는 필요하지 않습니다. 앱은 서버를 켜거나 끄지 않으며 서버의 시스템 패키지를 변경하지 않습니다.</p></section>`;
  return `<form id="settings-form"><section class="card" data-tour="connection-provider"><div class="section-title"><h3>GPU 연결 대상</h3><span class="badge">배정받은 GPU 사용</span></div><p>앱은 Pod나 서버를 만들거나 시작·종료하지 않습니다. 이미 사용 권한을 받은 GPU에서만 학습합니다.</p><div class="grid">
  ${select("연결 대상", "provider", server ? "ssh_server" : "existing_runpod", [["existing_runpod", "RunPod · 배정받은 Pod"], ["ssh_server", "GPU 서버 · SSH 직접 실행 (BOP 등)"]])}
  </div></section>
  ${server ? sshServer : runpod}
  <section class="card"><h3>공용 MLflow 연결</h3><div class="grid">
  ${field("MLflow 서버 주소", "MLFLOW_TRACKING_URI", s.MLFLOW_TRACKING_URI)}
  ${field("산출물 저장 위치", "FACTORY_MLFLOW_ARTIFACT_LOCATION", s.FACTORY_MLFLOW_ARTIFACT_LOCATION)}
  ${select("인증 방식", "auth_mode", auth, [["oidc", "기관 인증 (OIDC)"], ["token", "발급받은 토큰"], ["none", "인증 없음 · 로컬·내부 서버"]])}
  ${auth === "oidc" ? `${field("인증 서버 주소", "MLFLOW_OIDC_TOKEN_URL", s.MLFLOW_OIDC_TOKEN_URL)}
  ${field("클라이언트 ID", "MLFLOW_OIDC_CLIENT_ID", s.MLFLOW_OIDC_CLIENT_ID)}
  ${field("클라이언트 인증값", "MLFLOW_OIDC_CLIENT_SECRET", s.MLFLOW_OIDC_CLIENT_SECRET || "", "password", s.MLFLOW_OIDC_CLIENT_SECRET_saved ? "보안 저장됨" : "관리자에게 별도로 전달받으세요.")}` : ""}
  ${auth === "token" ? field("별도 발급 토큰", "MLFLOW_TRACKING_TOKEN", s.MLFLOW_TRACKING_TOKEN || "", "password", s.MLFLOW_TRACKING_TOKEN_saved ? "보안 저장됨" : "관리자에게 발급받은 토큰을 입력하세요.") : ""}
  </div>${auth === "none" ? '<p class="warning-text">인증 없음은 MLflow 서버에 접근할 수 있는 누구나 실행 기록을 볼 수 있습니다. 신뢰할 수 있는 로컬·내부망에서만 사용하세요. 저장된 인증값은 지우지 않으며 사용하지도 않습니다.</p>' : ""}
  <div class="actions"><button class="primary" type="submit">연결 정보 저장</button>${button("연결 검사", "check", "", pending)}${button("저장된 인증값 삭제", "forget", "danger")}</div></section></form>
  ${runtimeCard(server, pending)}
  ${s3Connections()}
  <form id="hf-settings-form"><section class="card"><h3>Hugging Face 접근</h3><div class="grid">${field("HF 읽기 토큰", "HF_TOKEN", state.hfDraft?.HF_TOKEN || "", "password", s.HF_TOKEN_saved ? "보안 저장됨 · 변경할 때만 입력" : "공개 모델 검색에는 필요하지 않습니다. 비공개·접근 제한 메타데이터 조회용입니다.")}</div><div class="actions"><button type="submit" class="primary">HF 인증값 저장</button></div><p class="muted">Windows 자격 증명 저장소에 보관합니다. GPU에는 전달하지 않으므로 제한 모델은 GPU의 사전 접근 권한과 캐시가 필요합니다.</p></section></form>
  ${connectionResult(state.connectionReport)}`;
}
function runtimeCard(server: boolean, pending: boolean) {
  const runtimeButton = (label: string, name: string, cls = "", disabled = false) => button(label, name, cls, pending || disabled);
  const note = pending ? '<p class="warning-text">바꾼 연결 대상을 저장한 뒤 사용할 수 있습니다.</p>' : "";
  if (!server) return `<section class="card" data-tour="connection-runtime"><h3>GPU 학습 환경</h3>
  <p>선택한 학습 단계에 맞는 학습 엔진을 Pod의 별도 폴더에 설치합니다. PPO는 별도 환경을 사용합니다. 첫 준비는 다운로드 용량에 따라 시간이 걸립니다.</p>${note}
  <div class="actions">${runtimeButton("환경 확인", "runtime_check")}${runtimeButton("학습 환경 준비", "runtime_prepare", "primary")}${runtimeButton("GPU 정보 → 학습 설정에 적용", "apply-pod")}</div></section>`;
  const saved = selectedGpus(state.settings), inspection = state.serverInspection;
  // A saved selection over the app policy blocks every action that would use it until a valid one is saved.
  const policy = selectionPolicyIssue(state.settings);
  const actions = [runtimeButton("GPU 목록 조회", "server-inspect"), runtimeButton("GPU 선택 저장", "server-select", "", !inspection),
    runtimeButton("환경 확인", "runtime_check", "", !!policy), runtimeButton("학습 환경 준비", "runtime_prepare", "primary", !!policy),
    runtimeButton("GPU 정보 → 학습 설정에 적용", "apply-pod", "", !!policy), runtimeButton("준비 예약 확인", "server-reservations")];
  return `<section class="card" data-tour="connection-runtime"><h3>GPU 학습 환경</h3>
  <p>저장된 GPU 서버의 작업 폴더 아래 별도 환경에 학습 엔진을 설치합니다. 서버의 기본 Python과 시스템 패키지는 변경하지 않습니다. 학습에 사용할 GPU를 조회해 선택하세요. 첫 준비는 다운로드 용량에 따라 시간이 걸립니다.</p>${note}
  ${policy ? `<p class="warning-text">${esc(policy)}</p>` : ""}
  <p class="muted">저장된 선택 GPU: ${saved.length ? saved.map(shortIdentifier).join(", ") : "없음 · GPU 목록을 조회해 선택하세요"}</p>
  ${inspection ? inventoryTable(inspection, state.gpuDraft || saved, saved) : ""}
  <div class="actions">${actions.join("")}</div>
  ${state.reservations ? reservationTable(state.reservations, state.settings, pending) : ""}</section>`;
}
function datasets() {
  return `<section class="card"><h3>새 데이터 버전 등록</h3><p>학습 단계에 맞는 JSON·JSONL을 등록합니다. 지시·대화, 텍스트, 선호 쌍, 선호 라벨, 프롬프트를 검사합니다. 등록 시 로컬 사본과 해시를 보존하며, 같은 버전은 덮어쓰지 않습니다.</p>
  <form id="dataset-form" data-tour="dataset-form"><div class="grid">${field("데이터 ID", "dataset_id", state.datasetDraft.dataset_id || "my_dataset")}${field("버전", "version", state.datasetDraft.version || "1")}
  ${select("데이터 종류", "kind", state.datasetDraft.kind || "instruction", datasetKinds)}${field("파일 경로", "path", state.datasetDraft.path || "")}<div class="field"><span>데이터 선택</span>${button(icon("folder") + " 파일 찾기", "pick:dataset")}</div></div>
  <label class="check"><input name="synthetic" type="checkbox" ${state.datasetDraft.synthetic ? "checked" : ""}> 더미·합성 데이터입니다</label>
  <label class="check"><input name="export_allowed" type="checkbox" ${state.datasetDraft.export_allowed ? "checked" : ""}> 이 데이터를 선택한 GPU로 전송해도 됩니다</label>
  <p class="muted">여기서는 PC에만 등록합니다. 실제 전송은 학습 실행 시 별도로 확인합니다.</p><div class="actions">${button("샘플 미리보기", "dataset-preview")}<button class="primary" type="submit" data-tour="dataset-submit">검사하고 등록</button></div></form></section>
  ${slotPanels("datasetsPanel")}
  ${s3Datasets()}
  ${state.preview ? `<section class="card"><div class="section-title"><h3>데이터 미리보기</h3><span class="badge">${esc(state.preview.format)} · ${num(state.preview.rows)}건</span></div>
  <p class="${state.preview.duplicate_rows ? "warning-text" : "muted"}">중복 레코드 ${num(state.preview.duplicate_rows)}건 · 앞 3건 미리보기 · 긴 내용은 일부만 표시합니다.</p><div class="sample-list">${state.preview.samples.map((sample: Obj, i: number) => `<article><small>SAMPLE ${String(i + 1).padStart(2, "0")}</small><pre>${esc(JSON.stringify(sample, null, 2))}</pre></article>`).join("")}</div></section>` : ""}
  <section class="card"><h3>등록한 버전</h3>${state.catalog.datasets.length ? `<table><thead><tr><th>데이터</th><th>버전</th><th>건수</th><th>구분</th><th>GPU 전송</th></tr></thead><tbody>${state.catalog.datasets.map((d: Obj) => `<tr><td>${esc(d.dataset_id)}</td><td>${esc(d.version)}</td><td>${num(d.rows)}</td><td>${d.synthetic ? "합성" : "사용자 데이터"}</td><td>${d.export_allowed ? "허용" : "미허용"}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">아직 등록한 데이터가 없습니다.</div>'}</section>`;
}
function training() {
  if (!state.config) return '<section class="card">학습 설정을 불러오는 중입니다.</section>';
  normalizeConfig();
  const c = state.config;
  const datasetOptions: [string, string][] = [["", "선택해 주세요"], ...state.catalog.datasets.map((d: Obj) => [d.dataset_id + ":" + d.version, d.dataset_id + " · v" + d.version])];
  return `${modelHubView()}<form id="training-form"><section class="card"><div class="section-title"><h3>실험 설정</h3>${button("설정 저장", "save-profile")}</div><div class="grid">
  ${select("예제·저장한 설정 불러오기", "template", "", [["", "현재 설정"], ...state.catalog.templates.map((t: Obj, i: number) => ["t:" + i, t.name]), ...state.catalog.profiles.map((p: Obj, i: number) => ["p:" + i, "저장됨 · " + p.ai_id])])}
  ${configField("업무 AI ID", "ai_id")}${configField("업무 흐름 ID", "workflow_id")}
  ${select("학습 데이터", "train_dataset", c.dataset_id + ":" + c.dataset_version, datasetOptions).replace('<label class="field">', '<label class="field" data-tour="train-dataset">')}
  ${select("검증 데이터", "validation_dataset", c.evaluation_profile.dataset_id ? c.evaluation_profile.dataset_id + ":" + c.evaluation_profile.dataset_version : "", [["", "없음 · 학습 완료만 확인"], ...datasetOptions.slice(1)])}
  ${configSelect("평가 방식", "evaluation_profile.kind", [["pipeline", "학습 완료 확인"], ["json", "JSON 형식"], ["classification", "분류"], ["extraction", "정보 추출"], ["kbf_extraction", "KBF 추출"], ["reward", "보상 점수 · RM"]])}
  </div></section><section class="card"><h3>모델과 학습</h3><div class="grid">
  ${configSelect("기반 모델", "base_model", (state.catalog.models || []).map((model: Obj) => [model.id, model.id + (model.gpu_verified ? " · GPU 검증 이력" : "")]))}
  ${stageSelect(trainingFields())}
  ${methodSelect(trainingFields())}
  ${configField("학습 횟수 (epoch)", "hyperparameters.num_train_epochs", "number")}
  ${configField("학습률", "hyperparameters.learning_rate", "number")}
  ${configField("배치 크기", "hyperparameters.per_device_train_batch_size", "number")}
  ${configField("최대 토큰 길이", "hyperparameters.cutoff_len", "number")}
  </div><div class="actions">${button("선택한 모델 버전 확인·고정", "model_revision")}</div>${modelMetadataView()}<p class="muted">모델을 변경하면 버전 확인을 눌러 주세요. 최초에는 공개 Qwen 모델을 권장합니다. Llama 모델은 이용 승인과 GPU의 사전 다운로드가 필요합니다.</p><details><summary>고급 설정</summary><div class="grid">
  ${configField("모델 revision · 40자리 commit", "model_revision")}
  ${configField("토크나이저 revision · 모델과 일치", "tokenizer_revision")}
  ${configField("모델 템플릿 · 모델에서 자동 적용", "template")}
  ${configSelect("정밀도", "hyperparameters.precision", [["bf16", "BF16 · 권장"], ["fp16", "FP16"]])}
  ${configField("최대 step (-1은 epoch 기준)", "hyperparameters.max_steps", "number")}
  ${configField("기울기 누적", "hyperparameters.gradient_accumulation_steps", "number")}
    ${configField("무작위 시드", "hyperparameters.seed", "number")}
    ${configField("체크포인트 저장 간격 (step)", "hyperparameters.save_steps", "number")}
  ${configField("체크포인트 보관 수", "hyperparameters.save_total_limit", "number")}
  ${configField("검증 최대 건수", "evaluation_profile.max_samples", "number")}
  ${configField("답변 최대 토큰", "evaluation_profile.max_new_tokens", "number")}
  </div></details></section>${trainingOptions(trainingFields())}<section class="card"><h3>GPU와 실행 한도</h3><div class="grid">
  ${configField("GPU 종류 · 연결 설정에서 가져오기", "resource_profile.gpu_type")}
  ${configField("GPU 수", "resource_profile.gpu_count", "number")}
  ${configField("GPU 한 개당 시간 요금 (USD)", "resource_profile.hourly_cost_usd", "number")}
  ${configField("최대 실행 시간 (초)", "resource_profile.timeout_seconds", "number")}
  ${configField("최대 예상 GPU 비용 (USD)", "resource_profile.max_cost_usd", "number")}
  </div><p class="muted">현재 앱 정책은 동시 GPU 1개, 최대 4시간·예상 비용 $5입니다. 기존 Pod의 실제 청구를 차단하는 한도는 아닙니다.</p>
  ${compatibilityView()}<div class="actions"><button class="primary" type="submit" data-tour="training-plan" ${!state.capabilities?.valid || state.capabilityPending ? "disabled" : ""}>실행 계획 확인</button>${button("설정 저장", "save-profile")}</div></section></form>
  ${state.plan ? `<section class="card plan" data-tour="plan-summary"><h3>실행 전 확인</h3><div class="stats"><article><small>업무 AI</small><b>${esc(state.plan.manifest.config.ai_id)}</b></article><article><small>학습 데이터</small><b>${esc(state.plan.manifest.dataset.dataset_id)} · ${num(state.plan.manifest.dataset.rows)}건</b></article><article><small>최대 시간 기준 예상 GPU 비용</small><b>$${num(state.plan.estimated_cost_usd)}</b></article></div>
  <p>${esc(state.plan.note)}</p><p class="muted">${isServer(state.settings) ? `GPU 서버: ${esc(serverAddress(state.settings))} · 선택 GPU ${selectedGpus(state.settings).length}개` : `Pod: ${esc(state.settings.pod_id || "미설정")}`} · GPU: ${esc(state.plan.manifest.config.resource_profile.gpu_type)}</p>
  ${planIssues().map(issue => `<p class="warning-text">${esc(issue)}</p>`).join("")}
  ${button("데이터 전송에 동의하고 학습 시작", "start", "primary", planIssues().length > 0)}
  <details><summary>확정된 설정 보기</summary><pre>${esc(JSON.stringify(state.plan.manifest.config, null, 2))}</pre></details></section>` : ""}`;
}
/** Why a displayed server plan cannot start: no destination hash for the saved settings (the offline plan's
 * issue) or a saved GPU selection over the app policy, which the submitted facts would not match. */
function planIssues(): string[] {
  if (!state.plan || !isServer(state.settings)) return [];
  const destination = state.plan.destination_sha256 ? null : state.plan.destination_issue || "GPU 서버 연결 설정을 확인하고 실행 계획을 다시 확인하세요.";
  return [destination, selectionPolicyIssue(state.settings)].filter((issue): issue is string => Boolean(issue));
}
function jobs() {
  const active = state.jobs.find((j: Obj) => j.id === state.active);
  const resumeSupported = state.jobCapabilities && state.jobCapabilities.resume_supported !== false;
  return `<section class="card" data-tour="jobs-list"><div class="section-title"><h3>이 PC의 작업</h3><div class="actions">${button("새로고침", "refresh")}${button("연결 끊긴 작업 확인", "recover")}</div></div>
  <p class="muted">학습은 원격에서 계속됩니다. PC를 다시 열면 이전 작업을 선택해 결과 회수를 시도하세요. GPU가 사용 중이면 종료하지 마세요.</p>${graphJobsNote()}
  ${state.jobs.length ? `<table><thead><tr><th>업무 AI / 작업</th><th>상태</th><th>시도</th><th>종류</th><th>실행 위치</th><th></th></tr></thead><tbody>${state.jobs.map((j: Obj) => `<tr><td><b>${esc(j.manifest.config.ai_id)}</b><small class="mono">${esc(j.id)}</small></td><td>${badge(j.status)}</td><td>${j.attempts}</td><td>${j.manifest.simulation ? "모의" : "실제"}</td><td>${esc(jobDestination(j))}</td><td>${button("선택", "job:" + j.id)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">실행 계획을 확인한 뒤 학습을 시작해 보세요.</div>'}</section>
  ${active ? `<section class="card"><div class="section-title"><h3>${esc(active.manifest.config.ai_id)} ${badge(active.status)}</h3><div class="actions">${button("저장된 진행 지표", "progress-local")}${button("원격 진행 지표", "progress-remote")}</div></div>${progressView(state.progress)}${active.error ? `<p class="error">${esc(active.error)}</p>` : ""}
  <div class="actions">${button("학습 로그", "logs")}${button("원격 실시간 로그", "live_logs")}${button("평가 결과", "evaluation")}${button("재연결 · 결과 회수", "reconcile")}${button("대기 작업 실행", "execute")}${button("취소 요청", "cancel", "danger")}</div>
  <div class="actions"><label>재개할 체크포인트 <input id="checkpoint" value="${esc(state.checkpoint || "checkpoint-10")}" aria-label="재개할 체크포인트"></label>${button("체크포인트에서 재개", "resume").replace("<button ", `<button ${!resumeSupported ? "disabled" : ""} `)}</div>${state.jobCapabilities?.resume_supported === false ? '<p class="warning-text">이 학습 단계·방식은 체크포인트 재개를 지원하지 않습니다. 회수 가능한 결과는 재연결로 확인하세요.</p>' : !state.jobCapabilities ? '<p class="muted">작업을 선택하면 재개 지원 여부를 확인합니다.</p>' : ""}<pre class="log">${esc(state.logs || "로그를 선택하세요.")}</pre></section>` : ""}`;
}
function history() {
  const options: [string, string][] = state.experiments.map((e: Obj) => [e.id, e.name + " · " + e.id]);
  const visible = filterRuns(state.runs, state.historyQuery, state.historyStatus);
  return `<section class="card"><div class="section-title"><h3>공용 MLflow 기록</h3>${button("실험 목록 불러오기", "experiments")}</div><div class="actions">${options.length ? select("실험", "experiment", state.experiment, options) : field("실험 ID", "experiment", state.experiment)}${button("기록 불러오기", "history")}</div>
  <p class="muted">두 실행을 선택해 설정과 지표를 비교합니다. 지표 차이는 같은 업무·검증 데이터·평가 방식에서만 계산합니다. 합성 데이터 결과는 업무 품질 검증과 구분하세요.</p>
  ${state.runs.length ? `<div class="filter-bar"><label class="search-input">${icon("search")}<input id="history-search" aria-label="불러온 기록 검색" placeholder="업무 AI, 실행자, 실행 ID 검색" value="${esc(state.historyQuery)}"></label>
  <select id="history-status" aria-label="실행 상태 필터">${[["", "모든 상태"], ["FINISHED", "완료"], ["RUNNING", "실행 중"], ["FAILED", "실패"], ["KILLED", "취소"]].map(([id, text]) => `<option value="${id}" ${state.historyStatus === id ? "selected" : ""}>${text}</option>`).join("")}</select><span class="subtle">불러온 ${state.runs.length}개 중 ${visible.length}개</span></div>
  <div class="table-scroll"><table><thead><tr><th>선택</th><th>업무 AI / 모델</th><th>실행자</th><th>상태</th><th>시작 시각</th><th>종류</th><th></th></tr></thead><tbody>${visible.map((r: Obj) => `<tr><td><input type="checkbox" data-run="${esc(r.id)}" aria-label="${esc(r.id)} 비교 선택" ${state.selected.includes(r.id) ? "checked" : ""}></td><td><b>${esc(r.ai_id)}</b><small class="mono">${esc(r.config?.base_model?.split("/").at(-1) || r.id.slice(0, 12))}</small></td><td class="operator-cell">${esc(r.operator)}</td><td>${badge(r.status)}</td><td>${r.start_time ? esc(new Date(r.start_time).toLocaleString("ko-KR", {month: "short", day: "numeric", hour: "2-digit", minute: "2-digit"})) : "—"}</td><td>${r.simulation ? "모의" : r.synthetic ? "합성 데이터" : "실제 데이터"}</td><td>${button("설정 불러오기", "reuse:" + r.id, "quiet")}</td></tr>`).join("")}</tbody></table></div>${!visible.length ? '<div class="empty">검색 조건에 맞는 기록이 없습니다. 불러온 페이지 안에서 검색합니다.</div>' : ""}
  <div class="comparison-tray"><span><b>${state.selected.length}</b> / 2 실행 선택</span>${button("선택한 두 실행 비교", "compare", state.selected.length === 2 ? "primary" : "")}${state.nextToken ? button("다음 기록 더 보기", "next-history") : ""}</div>` : '<div class="empty">서버에서 실행 기록을 불러오세요.</div>'}</section>
  ${state.comparison ? comparison() : ""}`;
}
function comparison() {
  const [a, b] = state.selected.map((id: string) => state.runs.find((r: Obj) => r.id === id));
  if (!a || !b) return "";
  const keys = [...new Set([...Object.keys(a.metrics), ...Object.keys(b.metrics)])];
  return `<section class="card"><h3>실행 비교</h3><p>${esc(state.comparison.reason || "같은 검증 기준입니다. 차이는 두 번째 실행 − 첫 번째 실행입니다.")}</p>
  <table><thead><tr><th>지표</th><th>${esc(a.id.slice(0, 8))}</th><th>${esc(b.id.slice(0, 8))}</th><th>차이</th></tr></thead><tbody>${keys.map(k => `<tr><td>${esc(k)}</td><td>${num(a.metrics[k])}</td><td>${num(b.metrics[k])}</td><td>${num(state.comparison.deltas[k])}</td></tr>`).join("")}</tbody></table>
  <div class="diff-heading"><h3>변경된 설정</h3><span class="count">${state.comparison.parameter_changes?.length || 0}</span></div>
  ${state.comparison.parameter_changes?.length ? `<table class="parameter-diff"><thead><tr><th>설정</th><th>첫 번째</th><th>두 번째</th></tr></thead><tbody>${state.comparison.parameter_changes.map((p: Obj) => `<tr><td class="mono">${esc(p.parameter)}</td><td class="diff-before">${esc(p.base_missing ? "없음" : JSON.stringify(p.base))}</td><td class="diff-after">${esc(p.candidate_missing ? "없음" : JSON.stringify(p.candidate))}</td></tr>`).join("")}</tbody></table>` : '<p class="muted">기록된 설정이 동일합니다.</p>'}
  <div class="grid"><div><details><summary>첫 번째 실행의 전체 설정</summary><pre>${esc(JSON.stringify(a.config, null, 2))}</pre></details>${button("이 설정으로 새 실험", "reuse:" + a.id)}</div><div><details><summary>두 번째 실행의 전체 설정</summary><pre>${esc(JSON.stringify(b.config, null, 2))}</pre></details>${button("이 설정으로 새 실험", "reuse:" + b.id)}</div></div></section>`;
}
// Pressing a control moves focus out of an edited field, whose change handler re-renders. Replacing the pressed
// control before pointerup would make the browser drop the click, so renders wait until the press ends.
const pressable = "button, summary, label.check, input[type=checkbox], input[type=radio]";
let pressed = false, presses = 0, armed = 0, renderDeferred = false;
let pressedControl: Element | null = null;
// While an IME composes (Hangul), replacing the composing input would corrupt the text: patches of its region wait, and
// so does a full render that carries no task result (`renderSoft`) while the composing control is in the page.
let composing = false;
let composingTarget: EventTarget | null = null;
let composeDeferred = false;
const composedPatches = new Map<string, { html: string; options: { keepTyping?: boolean } }>();
window.addEventListener("compositionstart", event => { composing = true; composingTarget = event.target; }, true);
window.addEventListener("compositionend", () => {
  composing = false; composingTarget = null;
  setTimeout(() => {
    if (composing) return;
    const waiting = [...composedPatches];
    composedPatches.clear();
    // A full render that waited for the composition covers every patch that waited with it.
    if (composeDeferred) { renderAfterComposition(); return; }
    for (const [regionId, { html, options }] of waiting) patch(regionId, html, options);
  }, 0);
}, true);
/** Whether an IME composes in a control of the page that a full render would replace. The control must still have the
 * focus, so one that a task's render already replaced (its composition never ended) holds no render back. */
function composingInPage() {
  return composing && composingTarget instanceof Element && document.activeElement === composingTarget && root.contains(composingTarget);
}
/** A full render that carries no task result (a poll, a view's own render or notice, the history filter). While an IME
 * composes in the page it waits until the composition ends. A task's render never waits (its result may change the
 * forms of its page, which a capture during the composition would read back stale) and supersedes a waiting one. */
function renderSoft() {
  if (!pressed && composingInPage()) { composeDeferred = true; return; }
  render();
}
/** The render that waited for a composition: the composed text is read into the drafts first, and the focus returns to
 * the same control with its selection. */
function renderAfterComposition() {
  if (!composeDeferred) return;
  captureDrafts();
  const keep = focusSpot();
  render();
  if (!document.activeElement || document.activeElement === document.body) keep();
}
/** Before form values are read back into state: a deferred render means the page still shows values that a
 * finished task has since replaced. During the press itself the page is current and stays as it is. */
function flushDeferredRender() { if (renderDeferred && !pressed) render(); }
function releasePress() {
  if (!pressed) return;
  pressed = false; pressedControl = null;
  // Runs after the click this release produces, unless that click has rendered already.
  if (renderDeferred) setTimeout(() => { if (renderDeferred) render(); }, 0);
}
window.addEventListener("pointerdown", e => {
  const control = (e.target as Element | null)?.closest?.(pressable);
  if (control) { pressed = true; presses++; pressedControl = control; }
}, true);
/** A render a view asks for (often after a poll). If a press defers it, what was typed so far is read first, so the
 * deferred render cannot rebuild the page from older drafts; a task's render already pending keeps precedence. */
function renderKeepingDrafts() {
  if (pressed && !renderDeferred) captureDrafts();
  renderSoft();
}
for (const type of ["pointerup", "pointercancel"]) window.addEventListener(type, releasePress, true);
// A release the page never saw (outside the window, on a control disabled meanwhile) must not leave it stale.
window.addEventListener("pointermove", e => { if (!e.buttons) releasePress(); }, true);
window.addEventListener("blur", releasePress);
function render() {
  if (pressed) {
    renderDeferred = true;
    // One fallback per press, so a later press whose release is also lost cannot leave the page stale either.
    if (armed !== presses) { const press = armed = presses; setTimeout(() => { if (pressed && presses === press) releasePress(); }, 1000); }
    return;
  }
  renderDeferred = false; composeDeferred = false;
  const legacyViews: Obj = { overview, connections, datasets, training, jobs, history };
  const view = viewById(state.page);
  const title = view ? view.title : pages[state.page];
  const parent = view?.parent ? viewById(view.parent)?.title ?? pages[view.parent] : null;
  const primary = state.page !== "overview" ? "" : viewById("projects")
    ? button(icon("plus") + " 새 프로젝트", "nav:projects", "primary") : button(icon("plus") + " 새 학습", "nav:training", "primary");
  root.innerHTML = `<aside><div class="brand"><span class="brand-mark"><svg width="22" height="24" viewBox="0 0 22 24" fill="none"><path d="M3 5h16M3 12h11M3 19h6" stroke="currentColor" stroke-width="3.2" stroke-linecap="square"/><circle cx="18" cy="19" r="2" fill="currentColor"/></svg></span><div>Tuning Factory<small>개인 워크스페이스</small></div>${icon("chevron", 12)}</div>
  <button class="command-trigger" data-action="search">${icon("search")}<span>바로 가기</span><kbd>Ctrl K</kbd></button>${sidebarNav(view?.parent ?? state.page)}
  <div class="sidebar-foot"><span class="avatar">${esc((state.settings.FACTORY_OPERATOR || "U").slice(0, 1).toUpperCase())}</span><div><b>${esc(state.settings.FACTORY_OPERATOR || "사용자 미설정")}</b><small>Windows · 내부 검토 ${esc(version)}</small></div></div></aside>
  <main><div class="appbar"><span class="breadcrumb">내 워크스페이스 <span>/</span> ${parent ? `${esc(parent)} <span>/</span> ` : ""}<b>${esc(title)}</b></span><span class="local-context">${icon("folder", 13)} 내 PC에서 작업 관리</span></div><div class="content"><header class="page-heading"><div><h1>${esc(title)}</h1><p>${esc(view ? view.description : descriptions[state.page])}</p></div>${primary}</header>
  <div aria-live="polite">${state.busy ? `<div class="notice busy"><span class="spinner"></span>${esc(state.busy)}<small>다른 화면에서도 작업 상태를 확인할 수 있습니다.</small></div>` : ""}${state.notice ? `<div class="notice">${esc(state.notice)}</div>` : ""}${state.error ? `<div class="notice error" role="alert">${esc(state.error)}</div>` : ""}</div>
  ${view ? guarded(() => view.render(ctx)) : legacyViews[state.page]()}</div></main>`;
  bind();
  for (const shown of shownViews(view)) {
    try { shown.bind?.(ctx); } catch (error) { console.error(error); }
  }
  tutorial.afterRender();
}
/** A workflow view that throws never takes the shell or the legacy pages down with it (as graph jobs did, F8). */
function guarded(work: () => string) {
  try { return work(); }
  catch (error) {
    console.error(error);
    return `<div class="wf-banner bad">이 영역을 표시하지 못했습니다. ${esc(errorText(error))}</div>`;
  }
}
/** The sidebar of D1: three groups from the nav model; a child page (such as one project) highlights its parent. */
function sidebarNav(highlighted: string) {
  const running = state.jobs.some((j: Obj) => j.status === "RUNNING");
  const item = ({ id, label, icon: name }: NavItem) => id === "tutorial"
    ? `<button aria-label="${esc(label)}" data-action="tutorial">${icon(name)}<span>${esc(label)}</span></button>`
    : `<button aria-label="${esc(label)}" data-action="nav:${esc(id)}" class="${highlighted === id ? "selected" : ""}">${icon(name)}<span>${esc(label)}</span>${id === "jobs" && running ? '<i class="activity-marker"></i>' : ""}</button>`;
  const items = navModel(workflowViews);
  return NAV_GROUPS.map(group => `<div class="nav-label${group.id === "workspace" ? "" : ` group-label ${group.id}-label`}" id="nav-group-${group.id}">${group.label}</div>`
    + `<nav aria-labelledby="nav-group-${group.id}">${items.filter(entry => entry.group === group.id).map(item).join("")}</nav>`).join("");
}
const byNavOrder = (a: View, b: View) => (a.nav?.order ?? 1000) - (b.nav?.order ?? 1000) || a.id.localeCompare(b.id);
/** Workflow panels on the overview (first in the left column) and on the datasets page (after the legacy form). */
function slotPanels(slot: "overviewPanel" | "datasetsPanel") {
  return [...workflowViews].sort(byNavOrder).map(view => view[slot] ? guarded(() => view[slot]!(ctx)) : "").join("");
}
/** The current view and every view whose slot panel this page shows: each binds its own elements after a render. */
function shownViews(current: View | undefined) {
  const shown = current ? [current] : [];
  for (const view of workflowViews)
    if ((state.page === "overview" && view.overviewPanel) || (state.page === "datasets" && view.datasetsPanel))
      if (!shown.includes(view)) shown.push(view);
  return shown;
}
/** D15: legacy lists show single jobs only; graph steps are counted and pointed to the graph runs page. */
function graphJobsNote() {
  if (!state.graphJobs) return "";
  const link = viewById("runs") ? " " + button("그래프 실행 열기", "nav:runs", "quiet") : "";
  return `<p class="graph-jobs-note">그래프 실행 단계 ${state.graphJobs}개는 ‘그래프 실행’에서 확인하세요.${link}</p>`;
}
/** Replaces one region of the current page (a poll result) without a full render: its actions are bound again and the
 * focused control survives, with what was typed in it when the same single control (same tag and type) comes back
 * under its key (`keepTyping: false` for a patch that is not a poll result). A press on a control inside the region
 * defers it like a render (the typed drafts are read first); a press elsewhere is unaffected by the patch. */
function patch(regionId: string, html: string, options: { keepTyping?: boolean } = {}) {
  if (renderDeferred) { render(); return; }
  const region = document.getElementById(regionId);
  if (!region || !root.contains(region)) return;
  if (pressed && pressedControl && region.contains(pressedControl)) { renderKeepingDrafts(); return; }
  if (composing && region.contains(document.activeElement)) { composedPatches.set(regionId, { html, options }); return; }
  const active = document.activeElement;
  const inside = active instanceof HTMLElement && region.contains(active);
  const key = inside ? focusKey(active) : null;
  const typed = options.keepTyping !== false && inside && (active instanceof HTMLTextAreaElement || (active instanceof HTMLInputElement
    && !["checkbox", "radio", "file", "button", "submit", "reset"].includes(active.type)))
    ? { tag: active.tagName, type: active.type, value: active.value, start: active.selectionStart, end: active.selectionEnd } : null;
  region.innerHTML = html;
  bindActions(region);
  if (key) {
    const matches = focusMatches(region, key);
    matches[0]?.focus({ preventScroll: true });
    const next = matches.length === 1 ? matches[0] : null;
    if (typed && (next instanceof HTMLTextAreaElement || next instanceof HTMLInputElement) && next.tagName === typed.tag && next.type === typed.type) {
      next.value = typed.value;
      try { next.setSelectionRange(typed.start, typed.end); } catch { /* inputs without a text selection */ }
    }
  }
  tutorial.afterRender();
}
async function refresh() {
  const all = await api("jobs");
  const list: Obj[] = Array.isArray(all) ? all : [];
  state.jobs = list.filter(job => !isGraphJob(job));
  state.graphJobs = list.length - state.jobs.length;
}
let catalogLoaded = false;
async function catalog() { state.catalog = await api("catalog"); catalogLoaded = true; }
async function saveSettings(values: Obj) {
  const before = state.settings;
  state.settings = await invoke("settings_save", { values });
  store.markSettingsChanged();
  const change = destinationChange(before, state.settings);
  if (change === "none") return change;
  // A plan or runtime report made for the previous destination or GPU selection must not be reused.
  state.runtimeReport = null; state.gpuDraft = null; invalidatePlan();
  // The observed inventory stays valid for a new selection on the same server; other destination data does not.
  if (change === "destination") { state.serverInspection = null; state.reservations = null; state.connectionReport = null; }
  return change;
}
async function readHistory(append = false) {
  const result = await api("history", { experiment_id: state.experiment, page_token: append ? state.nextToken : null });
  state.runs = append ? [...state.runs, ...result.runs] : result.runs;
  state.nextToken = result.next_page_token; state.comparison = null;
  if (!append) state.selected = [];
}
function settingsFromForm(form: HTMLFormElement) {
  const values: Obj = {};
  form.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-key]").forEach(el => values[el.dataset.key!] = el.value.trim());
  return values;
}
async function execute(job: Obj) {
  state.active = job.id; state.jobCapabilities = null; state.page = "jobs"; await refresh(); render();
  // Deliberately do not block navigation: a separate bridge process owns this attempt.
  api("execute", { job_id: job.id, approved: true }).then(async result => {
    await refresh();
    const current = state.jobs.find((j: Obj) => j.id === job.id);
    const waiting = "GPU를 사용할 수 없어 작업은 대기 중입니다. 다른 작업이 끝나면 ‘대기 작업 실행’을 눌러 주세요."
      // A GPU server job can also wait on a lock another job or an unfinished job of another destination left behind.
      + ((current ?? job).server_destination ? " 다른 작업이나 다른 연결 대상(RunPod 등)의 끝나지 않은 작업 잠금이 남아 있을 수 있습니다. 실행 관리에서 확인하세요." : "");
    state.notice = result ? "학습 처리가 끝났습니다. 상태와 평가 결과를 확인하세요." :
      ["PENDING", "QUEUED", "RETRYING"].includes(current?.status) ? waiting : "새로 실행된 작업이 없습니다. 현재 작업 상태를 확인하세요.";
    render();
  }).catch(async error => { state.error = errorText(error); await refresh(); render(); });
}
/** Every action button under `scope` runs its action; while busy, all but navigation (and every submit) are disabled. */
function bindActions(scope: ParentNode) {
  if (state.busy) scope.querySelectorAll<HTMLButtonElement>("button[data-action]:not([data-action^='nav:']), button[type='submit']").forEach(el => el.disabled = true);
  scope.querySelectorAll<HTMLElement>("[data-action]").forEach(el => el.addEventListener("click", () => void action(el.dataset.action!)));
}
function bind() {
  document.querySelector<HTMLFormElement>("#hub-form")?.addEventListener("submit", e => { e.preventDefault(); void action("hub-search"); });
  document.querySelector<HTMLInputElement>('#hub-form [data-key="model"]')?.addEventListener("input", () => {
    state.hubInspection = null; document.querySelector<HTMLButtonElement>('[data-action="hub-import"]')?.setAttribute("disabled", "");
  });
  document.querySelector<HTMLFormElement>("#hf-settings-form")?.addEventListener("submit", e => {
    e.preventDefault(); const values = settingsFromForm(e.currentTarget as HTMLFormElement);
    void task("HF 인증값 저장 중", async () => { state.settings = await invoke("settings_save", {values}); state.hfDraft = null; state.notice = "HF 인증값을 보안 저장했습니다."; });
  });
  bindActions(document);
  // A task's final re-render would drop typing, so connection inputs stay locked until it finishes.
  if (state.busy) document.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(":is(#settings-form, #s3-settings-form, #hf-settings-form) "
    + ":is(input, select, textarea), [data-gpu]").forEach(el => el.disabled = true);
  document.querySelector<HTMLFormElement>("#settings-form")?.addEventListener("submit", e => {
    e.preventDefault(); const values = settingsFromForm(e.currentTarget as HTMLFormElement);
    void task("연결 정보 저장 중", async () => { await saveSettings(values); state.settingsDraft = null; state.connectionReport = null; state.notice = "연결 정보를 저장했습니다."; });
  });
  document.querySelectorAll<HTMLSelectElement>('#settings-form [data-key="provider"], #settings-form [data-key="auth_mode"]').forEach(el => el.addEventListener("change", () => {
    captureDrafts(); render();
    document.querySelector<HTMLSelectElement>(`#settings-form [data-key="${el.dataset.key}"]`)?.focus();
  }));
  document.querySelector<HTMLFormElement>("#s3-settings-form")?.addEventListener("submit", e => {
    e.preventDefault(); const values = settingsFromForm(e.currentTarget as HTMLFormElement);
    void task("S3 연결 정보 저장 중", async () => { state.settings = await invoke("settings_save", { values }); state.s3SettingsDraft = null; state.s3Listing = null; state.s3Object = null; state.notice = "S3 연결 정보를 저장했습니다. 데이터 화면에서 버킷을 조회하세요."; });
  });
  document.querySelector<HTMLFormElement>("#s3-form")?.addEventListener("submit", e => e.preventDefault());
  document.querySelectorAll<HTMLInputElement>('#s3-form [data-key="bucket"], #s3-form [data-key="key"], #s3-form [data-key="version_id"]').forEach(el => el.addEventListener("input", () => {
    state.s3Object = null;
    document.querySelector<HTMLButtonElement>('[data-action="s3-import"]')?.setAttribute("disabled", "");
    document.querySelector(".s3-object")?.remove();
  }));
  document.querySelector<HTMLFormElement>("#dataset-form")?.addEventListener("submit", e => {
    e.preventDefault(); const form = e.currentTarget as HTMLFormElement; const data = settingsFromForm(form);
    data.synthetic = (form.elements.namedItem("synthetic") as HTMLInputElement).checked;
    data.export_allowed = (form.elements.namedItem("export_allowed") as HTMLInputElement).checked;
    void task("데이터 검사·등록 중", async () => { await api("dataset", data); await catalog(); state.notice = "데이터 버전을 등록했습니다."; });
  });
  document.querySelector<HTMLFormElement>("#training-form")?.addEventListener("submit", e => {
    e.preventDefault(); captureConfig(); void task("실행 계획 검사 중", async () => { const result = await checkCapabilities(); if (!result.valid) throw new Error((result.errors || ["설정 호환성을 확인해 주세요."]).join("\n")); state.plan = await api("plan", { config: state.config }); });
  });
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-config]").forEach(el => {
    el.addEventListener("input", () => { captureConfig(); invalidatePlan(); });
    el.addEventListener("change", () => {
      captureConfig();
      if (["base_model", "model_revision", "tokenizer_revision", "template"].includes(el.dataset.config || "")) state.modelMetadata = null;
      if (el.dataset.config === "base_model") {
        state.config.model_revision = ""; state.config.tokenizer_revision = "";
        const model = state.catalog.models.find((item: Obj) => item.id === state.config.base_model);
        state.config.template = model?.template || "";
        state.config.model_profile = model?.registration ? structuredClone(model.registration) : null;
        if (model?.registration) state.config.model_revision = state.config.tokenizer_revision = model.registration.revision;
      }
      if (el.dataset.config === "training_stage") {
        if (state.config.evaluation_profile.dataset_id) {
          if (state.config.training_stage === "rm") state.config.evaluation_profile.kind = "reward";
          else if (state.config.evaluation_profile.kind === "reward") state.config.evaluation_profile.kind = "json";
        }
      }
      void configChanged(el instanceof HTMLSelectElement || (el instanceof HTMLInputElement && el.type === "checkbox"), true);
    });
  });
  document.querySelector<HTMLSelectElement>('[data-key="template"]')?.addEventListener("change", e => {
    const [kind, index] = (e.target as HTMLSelectElement).value.split(":");
    if (index) state.config = structuredClone(kind === "t" ? state.catalog.templates[Number(index)].config : state.catalog.profiles[Number(index)]);
    normalizeConfig(); state.plan = null; void configChanged(true, true);
  });
  for (const key of ["train_dataset", "validation_dataset"]) {
    document.querySelector<HTMLSelectElement>(`[data-key="${key}"]`)?.addEventListener("change", e => {
      captureConfig(); const [id, version] = (e.target as HTMLSelectElement).value.split(":");
      if (key === "train_dataset") { state.config.dataset_id = id; state.config.dataset_version = version || ""; }
      else { state.config.evaluation_profile.dataset_id = id || null; state.config.evaluation_profile.dataset_version = version || null;
        if (!id) state.config.evaluation_profile.kind = "pipeline";
        else if (state.config.training_stage === "rm") state.config.evaluation_profile.kind = "reward";
        else if (state.config.evaluation_profile.kind === "pipeline") state.config.evaluation_profile.kind = "json"; }
      state.plan = null; void configChanged(true, true);
    });
  }
  document.querySelector<HTMLInputElement | HTMLSelectElement>('[data-key="experiment"]')?.addEventListener("change", e => state.experiment = (e.target as HTMLInputElement).value);
  document.querySelector<HTMLInputElement>("#history-search")?.addEventListener("input", e => {
    const input = e.target as HTMLInputElement; const cursor = input.selectionStart;
    state.historyQuery = input.value;
    // While an IME composes in the search box, the filter applies once the composition ends (renderSoft waits).
    if (composingInPage()) { renderSoft(); return; }
    render();
    const next = document.querySelector<HTMLInputElement>("#history-search")!; next.focus({ preventScroll: true }); next.setSelectionRange(cursor, cursor);
  });
  document.querySelector<HTMLSelectElement>("#history-status")?.addEventListener("change", e => {
    state.historyStatus = (e.target as HTMLSelectElement).value; render();
  });
  document.querySelectorAll<HTMLInputElement>("[data-run]").forEach(el => el.addEventListener("change", () => {
    if (el.checked && state.selected.length >= 2) { el.checked = false; return; }
    state.selected = el.checked ? [...state.selected, el.dataset.run!] : state.selected.filter((id: string) => id !== el.dataset.run);
    state.comparison = null; render();
  }));
}
async function prepareServer() {
  captureDrafts();
  if (state.gpuDraft && !sameSelection(state.gpuDraft, selectedGpus(state.settings))) { state.error = "GPU 선택을 먼저 저장하세요."; render(); return; }
  const stage = state.config?.training_stage || "sft";
  let inspection = null as Obj | null;
  await task("GPU 서버 조회 중", async () => {
    const result = await gpuServer.serverInspect();
    state.serverInspection = result; state.gpuDraft = null;
    const issue = prepareIssue(result);
    if (issue) throw new Error(issue);
    inspection = result;
  });
  if (!inspection) return;
  const approved = inspection;
  if (!await approve("GPU 서버 학습 환경 준비", prepareApproval(approved, stage))) return;
  await task("GPU 환경 준비 중 · 첫 설치는 수 분 이상 걸릴 수 있습니다", async () => {
    try { state.runtimeReport = await gpuServer.runtimePrepare(approved, stage); }
    catch (error) {
      // An interrupted preparation can keep an uncertain reservation; show it beside the error.
      state.reservations = await gpuServer.runtimeReservations().catch(() => state.reservations);
      throw error;
    }
    state.notice = "GPU 서버 작업 폴더에 학습 환경을 준비하고 CUDA를 확인했습니다.";
  });
}
async function releaseReservation(reservation: Obj | undefined) {
  if (!reservation || !reservationMatches(reservation, state.settings)) return;
  if (!await approve("환경 준비 예약 해제", releaseApproval(reservation))) return;
  await task("준비 예약 해제 중", async () => {
    await gpuServer.runtimeRelease(reservation.reservation_id);
    state.reservations = await gpuServer.runtimeReservations(); state.notice = "환경 준비 예약을 해제했습니다.";
  });
}
async function action(name: string) {
  if (name.startsWith("hub-inspect:")) {
    captureConfig(); captureDrafts();
    state.hubDraft.model = state.hubModels[Number(name.split(":")[1])].id;
    render(); await action("hub-inspect"); return;
  }
  if (name === "search") { commandSearch(); return; }
  if (name === "tutorial") {
    if (state.busy || !state.config) {
      state.notice = !state.busy && catalogLoaded ? "예제 학습 설정을 찾을 수 없어 튜토리얼을 시작할 수 없습니다."
        : "작업 공간을 불러오거나 진행 중인 작업이 끝난 뒤 튜토리얼을 시작하세요.";
      render(); return;
    }
    captureConfig(); captureDrafts(); await tutorial.start(); return;
  }
  if (name === "tutorial-dismiss") { rememberTutorial("dismissed"); render(); return; }
  if (name.startsWith("wf:")) {
    const [, viewId, verb, ...rest] = name.split(":");
    const target = viewById(viewId);
    if (!target?.action || !verb) return;
    captureConfig(); captureDrafts();
    try { await target.action(ctx, verb, rest.length ? rest.join(":") : null); }
    catch (error) { state.error = errorText(error); render(); }
    return;
  }
  if (name.startsWith("nav:")) {
    const [, page, ...rest] = name.split(":");
    if (!Object.hasOwn(pages, page) && !viewById(page)) return;
    captureConfig(); captureDrafts();
    try { viewById(state.page)?.leave?.(ctx); } catch (error) { console.error(error); }
    const visit = ++navigations;
    state.page = page; state.arg = rest.length ? rest.join(":") : null;
    render();
    const entered = viewById(page);
    if (entered?.enter) {
      // A view's reads start after the running tasks (a save still in flight), and only if this visit is still the
      // latest navigation and its page is still shown.
      const arg = state.arg, waited = running.length > 0;
      await idle();
      if (visit === navigations && state.page === page && state.arg === arg) {
        keepMessages = waited;
        try { await entered.enter(ctx); } catch (error) { state.error = errorText(error); render(); }
        finally { keepMessages = false; }
      }
    }
    if (state.page === "training" && !state.capabilities && !state.capabilityPending) { await checkCapabilities(); render(); }
    return;
  }
  if (name.startsWith("template:")) {
    state.config = structuredClone(state.catalog.templates[Number(name.split(":")[1])].config);
    normalizeConfig(); state.plan = null; state.capabilities = null; state.page = "training"; render(); await checkCapabilities(); render(); return;
  }
  if (name.startsWith("pick:")) {
    const dataset = name.endsWith("dataset");
    const path = await invoke<string | null>("pick_file", { kind: dataset ? "dataset" : "key" });
    flushDeferredRender();
    const input = document.querySelector<HTMLInputElement>(`[data-key="${dataset ? "path" : name === "pick:server-key" ? "server_ssh_key_file" : "ssh_key_file"}"]`);
    if (path && input) input.value = path;
    captureDrafts();
    if (path && dataset) { state.preview = null; render(); }
    return;
  }
  if (name.startsWith("job:")) {
    state.active = name.split(":")[1]; state.page = "jobs"; state.logs = ""; state.progress = null; state.liveProgress = false; state.jobCapabilities = null;
    await task("학습 기록 불러오는 중", async () => {
      const job = state.jobs.find((item: Obj) => item.id === state.active);
      const [progress, capabilities] = await Promise.all([api("job_progress", { job_id: state.active }), api("capabilities", { config: job.manifest.config })]);
      state.progress = progress; state.jobCapabilities = capabilities;
    }); return;
  }
  if (name.startsWith("reuse:")) {
    await task("실행 설정 불러오는 중", async () => {
      const config = state.runs.find((run: Obj) => run.id === name.slice(6))?.config;
      if (!config?.ai_id || !config.hyperparameters || !config.resource_profile || !config.evaluation_profile) throw new Error("이 기록에는 재사용할 전체 설정이 없습니다.");
      state.config = structuredClone(config); normalizeConfig(); state.page = "training"; state.plan = null; await checkCapabilities();
      state.notice = "설정을 불러왔습니다. 이 PC에 등록된 데이터 버전을 선택하고 실행 계획을 확인하세요.";
    }); return;
  }
  if (name.startsWith("s3-folder:")) {
    captureDrafts(); const prefix = state.s3Listing.common_prefixes[Number(name.split(":")[1])];
    state.s3Draft.prefix = typeof prefix === "string" ? prefix : prefix.prefix; state.s3Listing = null; render();
    await action("s3-list"); return;
  }
  if (name.startsWith("s3-object:")) {
    captureDrafts(); const object = state.s3Listing.objects[Number(name.split(":")[1])];
    state.s3Draft.key = object.key; state.s3Draft.version_id = ""; state.s3Object = null; render();
    await action("s3-head"); return;
  }
  if (name === "s3-import") {
    captureDrafts();
    if (!state.s3Object) { state.error = "가져올 객체를 먼저 확인하세요."; render(); return; }
    if (!await approve("S3에서 PC로 데이터 가져오기", `${state.s3Draft.bucket}/${state.s3Draft.key} (${dataSize(state.s3Object.size)})를 이 PC에 다운로드하고 검사한 사본으로 등록합니다. S3 다운로드 요금이 발생할 수 있습니다. RunPod로는 전송하지 않습니다. 학습 시작 시 별도 전송 동의가 필요합니다.`)) return;
  }
  if (name.startsWith("release:")) { await releaseReservation(state.reservations?.[Number(name.split(":")[1])]); return; }
  const policy = isServer(state.settings) && ["runtime_check", "runtime_prepare", "apply-pod"].includes(name) && selectionPolicyIssue(state.settings);
  if (policy) { state.error = policy; render(); return; }
  if (name === "runtime_prepare" && isServer(state.settings)) { await prepareServer(); return; }
  if (name === "runtime_prepare" && !await approve("GPU 학습 환경 준비", "저장된 연결의 현재 실행 중인 Pod에 앱 전용 학습 환경을 설치합니다. 의존성과 모델 실행 도구를 다운로드하며 GPU 요금은 계속 발생합니다. 사용자 데이터는 전송하지 않습니다.")) return;
  if (name === "resume" && state.jobCapabilities?.resume_supported === false) { state.error = "이 학습 단계·방식은 체크포인트 재개를 지원하지 않습니다."; render(); return; }
  if (["start", "execute", "resume"].includes(name)) {
    captureConfig();
    if (name === "start" && !state.plan) { state.error = "변경된 설정으로 실행 계획을 다시 확인하세요."; render(); return; }
    if (name === "start" && planIssues().length) { state.error = planIssues().join("\n"); render(); return; }
    // Execute and resume run where the job was approved; resume resets the job before the sidecar could refuse.
    const job = name === "start" ? null : state.jobs.find((j: Obj) => j.id === state.active);
    const issue = job ? jobDestinationIssue(job, state.settings) : null;
    if (issue) { state.error = issue; render(); return; }
    if (!await approve("실제 GPU 학습 실행", transferApproval(job ? job.server_destination ?? null : settingsRoute(state.settings)))) return;
  }
  const checkpoint = document.querySelector<HTMLInputElement>("#checkpoint")?.value;
  captureConfig();
  await task(name === "runtime_prepare" ? "GPU 환경 준비 중 · 첫 설치는 수 분 이상 걸릴 수 있습니다" : "처리 중", async () => {
    if (name === "hub-search") { state.hubInspection = null; state.hubModels = (await api("model_search", state.hubDraft)).models; }
    else if (name === "hub-inspect") { state.hubInspection = null; state.hubInspection = await api("model_inspect", {model: state.hubDraft.model}); }
    else if (name === "hub-import") {
      const inspected = state.hubInspection;
      if (!inspected?.supported || inspected.repo_id !== state.hubDraft.model) throw new Error("먼저 현재 모델 ID를 확인하세요.");
      const imported = await api("model_import", {model: inspected.repo_id, revision: inspected.revision, config_sha256: inspected.profile.config_sha256});
      await catalog(); const profile = imported.profile;
      state.config.base_model = profile.repo_id; state.config.template = profile.template;
      state.config.model_revision = state.config.tokenizer_revision = profile.revision;
      state.config.model_profile = profile; state.modelMetadata = null; state.plan = null;
      state.hubModels = []; state.hubInspection = null;
      await checkCapabilities(); state.notice = "Hugging Face 모델을 추가했습니다. 실제 GPU 검증은 아직 수행하지 않았습니다.";
    }
    else if (name === "check") {
      const report = state.connectionReport = await api("connection");
      state.notice = !report.ssh_server ? "저장된 연결 정보로 MLflow와 Pod 조회를 확인했습니다." : report.tracking_uri ?
        "저장된 연결 정보로 MLflow와 GPU 서버 조회를 확인했습니다. 실제 학습은 아직 확인하지 않았습니다." : "GPU 서버 조회를 확인했습니다. MLflow 주소가 없어 기록 저장소는 확인하지 않았습니다.";
    }
    else if (name === "forget") {
      if (await approve("저장된 인증값 삭제", "Windows 자격 증명 저장소에 저장한 이 앱의 RunPod·MLflow·S3·Hugging Face 인증값을 삭제합니다. 진행 중인 작업에는 적용되지 않습니다.")) {
        await invoke("forget_credentials"); state.settings = await invoke("settings_get"); store.markSettingsChanged(); state.settingsDraft = null; state.s3SettingsDraft = null; state.hfDraft = null; state.connectionReport = null; state.notice = "저장된 인증값을 삭제했습니다.";
      }
    }
    else if (name === "runtime_check" || name === "runtime_prepare") { state.runtimeReport = await api(name, { approved: name === "runtime_prepare", training_stage: state.config?.training_stage || "sft" }); state.notice = `선택한 ${isServer(state.settings) ? "GPU 서버" : "Pod"}에서 앱과 일치하는 학습 환경과 CUDA를 확인했습니다.`; }
    else if (name === "server-inspect") { state.serverInspection = null; state.gpuDraft = null; state.serverInspection = await gpuServer.serverInspect(); }
    else if (name === "server-select") {
      const selected: string[] = state.gpuDraft ?? selectedGpus(state.settings);
      if (selected.length > MAX_GPUS_PER_JOB) throw new Error(GPU_POLICY_MESSAGE);
      const change = await saveSettings({ server_selected_gpu_uuids: JSON.stringify(selected) });
      state.notice = change === "none" ? "GPU 선택이 바뀌지 않았습니다." : selected.length
        ? `학습에 사용할 GPU ${selected.length}개를 저장했습니다. 실행 계획을 다시 확인하세요.` : "GPU 선택을 비웠습니다. 학습 전에 GPU를 선택하세요.";
    }
    else if (name === "server-reservations") { state.reservations = await gpuServer.runtimeReservations(); }
    else if (name === "dataset-preview") {
      if (!state.datasetDraft.path) throw new Error("먼저 데이터 파일을 선택하세요.");
      state.preview = await api("dataset_preview", { path: state.datasetDraft.path, kind: state.datasetDraft.kind || "instruction" });
    }
    else if (["s3-list", "s3-next", "s3-head", "s3-import"].includes(name)) {
      const d = state.s3Draft;
      if (!d.bucket) throw new Error("S3 버킷을 입력하세요.");
      if (["s3-list", "s3-next"].includes(name)) state.s3Listing = await api("s3_list", { bucket: d.bucket, prefix: d.prefix || "", continuation_token: name === "s3-next" ? state.s3Listing?.next_token : null });
      else if (name === "s3-head") {
        if (!d.key) throw new Error("S3 객체 key를 입력하세요.");
        state.s3Object = null;
        state.s3Object = await api("s3_head", { bucket: d.bucket, key: d.key, version_id: d.version_id || null });
      } else {
        const object = state.s3Object;
        if (!object) throw new Error("가져올 객체를 먼저 확인하세요.");
        const result = await api("s3_import", { bucket: d.bucket, key: d.key, version_id: object.version_id || d.version_id || null, expected: { bucket: object.bucket, key: object.key, etag: object.etag, version_id: object.version_id || null, size: object.size, checksum_sha256: object.checksum_sha256, checksum_type: object.checksum_type }, dataset_id: d.dataset_id, version: d.version, kind: d.kind || "instruction", synthetic: Boolean(d.synthetic), export_allowed: Boolean(d.export_allowed), approved: true });
        await catalog(); state.notice = `S3 데이터를 PC에 등록했습니다. ${result.dataset_id} · v${result.version} · SHA256 ${result.sha256 || "확인됨"}`; state.s3Object = null;
      }
    }
    else if (name === "progress-local" || name === "progress-remote") {
      state.progress = await api("job_progress", { job_id: state.active, remote: name === "progress-remote" });
      state.liveProgress = name === "progress-remote";
    }
    else if (name === "apply-pod" && isServer(state.settings)) {
      Object.assign(state.config.resource_profile, resourcesFromFacts(await gpuServer.serverFacts()));
      state.plan = null; await checkCapabilities(); state.notice = "선택한 GPU 서버의 GPU 종류·수와 직접 입력한 시간당 비용을 학습 설정에 적용했습니다.";
    }
    else if (name === "apply-pod") {
      const pod = await api("pod");
      state.config.resource_profile.gpu_type = pod.gpu.id || pod.gpu.displayName;
      state.config.resource_profile.gpu_count = pod.gpu.count;
      state.config.resource_profile.hourly_cost_usd = Number(pod.costPerHr) / pod.gpu.count;
      state.plan = null; await checkCapabilities(); state.notice = "실제 GPU 종류·수·현재 요금을 학습 설정에 적용했습니다.";
    }
    else if (name === "model_revision") {
      const model = state.config.base_model;
      const resolved = await api("model_revision", { model });
      if (state.config.base_model !== model) throw new Error("조회 중 모델이 변경되었습니다. 현재 모델의 버전을 다시 확인하세요.");
      for (const field of ["model_revision", "tokenizer_revision", "template"]) state.config[field] = resolved[field];
      state.config.model_profile = resolved.model_profile || null;
      state.modelMetadata = { ...resolved, model };
      state.plan = null; await checkCapabilities(); state.notice = "선택한 모델과 토크나이저 버전을 고정했습니다.";
    }
    else if (name === "save-profile") { await api("save_profile", { config: state.config }); await catalog(); state.notice = "실험 설정을 저장했습니다."; }
    else if (name === "start") {
      // The server destination approved in the dialog is the one the offline plan hashed; submit refuses any other.
      const destination = isServer(state.settings) ? { destination_sha256: state.plan.destination_sha256 } : {};
      const job = await api("submit", { config: state.plan.manifest.config, approved: true, request_key: crypto.randomUUID(), ...destination }); await execute(job); state.plan = null;
    }
    else if (name === "execute") { await execute(state.jobs.find((j: Obj) => j.id === state.active)); }
    else if (name === "refresh") { await refresh(); }
    else if (name === "recover") { await api("recover"); await refresh(); state.notice = "오래된 작업 상태를 확인했습니다. 실패한 원격 작업은 결과 회수를 시도하세요."; }
    else if (["logs", "live_logs", "evaluation"].includes(name)) { const result = await api(name === "live_logs" ? name : "logs", { job_id: state.active, name: name === "evaluation" ? "evaluation.json" : "train.log" }); state.logs = result.text || result.note || "아직 로그가 없습니다."; }
    else if (name === "cancel") { const result = await api("cancel", { job_id: state.active }); state.notice = result.note; await refresh(); }
    else if (name === "reconcile") { await api("recover"); await api("reconcile", { job_id: state.active }); await refresh(); state.notice = "회수 가능한 원격 결과를 확인했습니다."; }
    else if (name === "resume") { const job = await api("resume", { job_id: state.active, checkpoint, approved: true }); await execute(job); }
    else if (name === "experiments") { const result = await api("history"); state.experiments = result.experiments; }
    else if (name === "history" || name === "next-history") await readHistory(name === "next-history");
    else if (name === "compare") {
      if (state.selected.length !== 2) throw new Error("비교할 실행을 두 개 선택하세요.");
      const [base, candidate] = state.selected.map((id: string) => state.runs.find((r: Obj) => r.id === id));
      state.comparison = await api("compare", { base, candidate });
    }
  });
}
const tutorial = createTutorial({ state, navigate: page => action("nav:" + page), render, api, checkCapabilities, normalizeConfig });
render();
void task("작업 공간 불러오는 중", async () => {
  state.settings = await invoke("settings_get"); await catalog(); await refresh();
  state.config = structuredClone(state.catalog.profiles[0] || state.catalog.templates.find((t: Obj) => t.name === "paper_extraction")?.config);
  normalizeConfig(); await checkCapabilities();
});
window.addEventListener("keydown", e => { if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "k") { e.preventDefault(); commandSearch(); } });
let polling = false;
setInterval(() => {
  if (state.page !== "jobs" || state.busy || polling) return;
  polling = true; captureDrafts();
  refresh().then(async () => {
    if (state.liveProgress && state.active) state.progress = await api("job_progress", { job_id: state.active, remote: true });
    renderSoft();
  }).catch(() => { state.liveProgress = false; }).finally(() => polling = false);
}, 10000);
