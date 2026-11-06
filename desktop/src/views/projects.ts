import { t as tr, msg, html as ui, staticHtml, localized } from "../i18n";
// Projects (10.2): a blank project from a free name and purpose (overview panel), the project list, legacy import, and
// one project's page with its purpose, data bindings and default executor profile. Saving is explicit (D4); a stale
// save keeps the draft and offers the D5 choices. Every dynamic text is escaped.
import "./projects.css";
import { executorProfiles, newRequestId, projects as projectApi } from "../api";
import { issueList, issuesAt, localize } from "../issues";
import { bindingIdFor } from "../project-store";
import { button, errorText, escapeHtml, field, openDialog, restoreFocus, select, statusPill } from "../workbench";
import type { View, ViewContext } from "../view-types";
import type { BindingRole, DatasetBinding, ProjectSpec, ProjectSummary, PublicProfile, RecipeSpec, Sha256, WorkflowIssue,
  WorkflowResult } from "../workflow-types";

type Obj = Record<string, any>;
interface CreateDraft { name: string; purpose: string; task_type: string }
interface ImportNote { origin: string; recipe: RecipeSpec; recipeRevision: Sha256; warnings: WorkflowIssue[] }

const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const PRACTICE = "연습 데이터 · 실제 실행 불가";
const ROLES: [BindingRole, string][] = localized(() => ([["train", "학습"], ["validation", "검증"], ["test", "시험"]]));
const ROLE_LABEL: Record<BindingRole, string> = localized(() => ({ train: "학습", validation: "검증", test: "시험" }));
const HASH = /^[0-9a-f]{64}$/;
const PROJECT_FIELDS: [keyof CreateDraft, string][] = [["name", "/spec/name"], ["purpose", "/spec/purpose"], ["task_type", "/spec/task_type"]];

// -- module state (drafts of the project itself live in ctx.store) --
let shell: ViewContext | null = null;                  // the shell's live context, for the project page's title
let createDraft: CreateDraft = { name: "", purpose: "", task_type: "" };
let createRequest: { id: string; key: string } | null = null;   // reused until the name, purpose or task type changes
let createIssues: WorkflowIssue[] = [];
let list: ProjectSummary[] | null = null;
let listIssues: WorkflowIssue[] = [];
let importIssues: WorkflowIssue[] = [];
const legacyRequests = new Map<string, string>();       // ai_id → request ID, kept for a retry after a bridge error
let unavailable = false;                                 // the workflow channel answered not_available
const fresh = new Set<string>();                         // adopted just now: entering its page needs no re-read
const loadIssues = new Map<string, WorkflowIssue[]>();
const importNotes = new Map<string, ImportNote>();
let profiles: PublicProfile[] | null = null;
let profilesError: string | null = null;
let bindingDraft: { dataset: string; role: BindingRole } = { dataset: "", role: "train" };
let bindingNote: { kind: "ok" | "error"; text: string } | null = null;

const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => localize(issue).text).join("\n") || tr("요청을 처리하지 못했습니다.");
const localIssue = (path: string, message: string): WorkflowIssue => ({ code: "required", path, message });
/** The reserved practice ID (Windows ignores case and trailing dots, as desktop.reserved_tutorial_id does). */
const practice = (datasetId: unknown) => String(datasetId ?? "").toLowerCase().replace(/\.+$/, "") === "tutorial_sample";
const short = (hash: unknown) => escapeHtml(String(hash ?? "").slice(0, 8));
const graphUses = (spec: ProjectSpec, bindingId: string) => {
  const nodes = (spec.graph as Obj)?.nodes;
  return Array.isArray(nodes) ? nodes.filter((node: Obj) => node?.dataset_binding === bindingId).length : 0;
};
const keyOf = (draft: CreateDraft) => JSON.stringify([draft.name, draft.purpose, draft.task_type]);
const trimmed = (draft: CreateDraft): CreateDraft => ({ name: draft.name.trim(), purpose: draft.purpose.trim(), task_type: draft.task_type.trim() });
const focusById = (id: string) => document.getElementById(id)?.focus({ preventScroll: false });
/** A text as a textarea's value holds it: the HTML parser and `.value` turn CRLF and CR into LF. */
const lf = (value: unknown) => String(value ?? "").replace(/\r\n?/g, "\n");
/** A text as a one-line `<input>` holds it: its value sanitization removes every CR and LF. */
const oneLine = (value: unknown) => String(value ?? "").replace(/[\r\n]/g, "");
/** Whether the user is still on the page an action started from (its follow-up may only then move the focus). */
const stillOn = (ctx: ViewContext) => { const page = ctx.page, arg = ctx.arg; return () => ctx.page === page && ctx.arg === arg; };
/** Puts the focus on `key` only when a render lost it (to body) and only on the same page: never across pages. */
function refocus(still: () => boolean, key: string) {
  if (still() && (!document.activeElement || document.activeElement === document.body)) restoreFocus(document, key);
}
/** A form whose controls are locked while busy was not edited; reading it back could undo the running task's result. */
const locked = (form: Element) => form.querySelector("fieldset:disabled") !== null;

/** A text input with an ID (focus survives re-renders), a length limit and the issues at its pointer below it. */
function textInput(label: string, key: string, value: unknown, id: string, options: { max: number; help?: string; required?: boolean; issues?: WorkflowIssue[] }) {
  const issues = options.issues ?? [];
  const attributes = `id="${id}" maxlength="${options.max}"${options.required ? ' aria-required="true"' : ""}${issues.length ? ` aria-invalid="true" aria-describedby="${id}-issues"` : ""}`;
  const html = field(label, key, value, "text", options.help ? escapeHtml(options.help) : "").replace("<input ", `<input ${attributes} `);
  return issues.length ? `<div class="project-field">${html}<div id="${id}-issues">${issueList(issues)}</div></div>` : html;
}
function textArea(label: string, key: string, value: unknown, id: string, options: { max: number; help?: string; issues?: WorkflowIssue[] }) {
  const issues = options.issues ?? [];
  return `<label class="field project-wide"><span>${label}</span><textarea id="${id}" aria-label="${escapeHtml(label)}" data-key="${key}" rows="3" maxlength="${options.max}"${issues.length ? ` aria-invalid="true" aria-describedby="${id}-issues"` : ""}>\n${escapeHtml(value)}</textarea>${options.help ? `<small>${escapeHtml(options.help)}</small>` : ""}</label>${issues.length ? `<div id="${id}-issues" class="project-wide">${issueList(issues)}</div>` : ""}`;
}
const readKey = (form: Element, key: string) => form.querySelector<HTMLInputElement | HTMLTextAreaElement | HTMLSelectElement>(`[data-key="${key}"]`)?.value ?? "";

// -- the blank project form (overview panel and the projects page) --
function createForm(ctx: ViewContext) {
  const routed = new Set(["/name", "/purpose", "/task_type"]);
  const others = createIssues.filter(issue => !routed.has(issue.path));
  return ui`<form id="project-create-form" class="project-create" novalidate>
    <fieldset class="project-fieldset"${ctx.busy ? " disabled" : ""}><div class="grid">
    ${textInput(tr("프로젝트 이름"), "name", createDraft.name, "project-create-name", { max: 200, required: true, help: tr("필수 · 자유롭게 정하세요."), issues: issuesAt(createIssues, "/name") })}
    ${textInput(tr("작업 유형"), "task_type", createDraft.task_type, "project-create-task-type", { max: 200, help: tr("선택 · 예: 요약, 분류, 정보 추출"), issues: issuesAt(createIssues, "/task_type") })}
    ${textArea(tr("목적"), "purpose", createDraft.purpose, "project-create-purpose", { max: 4000, help: tr("선택 · 이 프로젝트로 확인하려는 것을 적어 두세요."), issues: issuesAt(createIssues, "/purpose") })}
    </div>${issueList(others)}
    <div class="actions"><button type="submit" class="primary" data-focus-key="project-create"${unavailable ? " disabled" : ""}>만들기</button></div></fieldset>
    ${unavailable ? `<p class="muted">${tr(NOT_AVAILABLE)}</p>` : ""}</form>`;
}

async function createProject(ctx: ViewContext) {
  captureForms(ctx);
  const still = stillOn(ctx);
  const values = trimmed(createDraft);
  if (!values.name) {
    createIssues = [localIssue("/name", tr("프로젝트 이름을 입력하세요."))];
    ctx.render(); focusById("project-create-name"); return;
  }
  const key = keyOf(values);
  if (createRequest?.key !== key) createRequest = { id: newRequestId(), key };
  const requestId = createRequest.id;
  let created: string | null = null;
  await ctx.task(tr("프로젝트 만드는 중"), async () => {
    const result = await projectApi.create({ request_id: requestId, ...values });
    createIssues = [];
    if (result.status === "ok" && result.data) {
      const draft = ctx.store.adopt(result.data);
      createDraft = { name: "", purpose: "", task_type: "" }; createRequest = null;
      // A user who moved on meanwhile stays where they are.
      if (still()) { created = draft.projectId; fresh.add(created); }
      else ctx.notify(msg`프로젝트를 만들었습니다: ‘${values.name}’. 프로젝트 목록에서 열 수 있습니다.`);
      return;
    }
    if (notAvailable(result)) unavailable = true;
    createIssues = result.issues;
    if (result.status === "conflict") createRequest = null;   // request_reused: the next attempt is a new request
  });
  if (created) await openCreated(ctx, created);
  else refocus(still, "project-create");
}

async function openCreated(ctx: ViewContext, projectId: string) {
  await ctx.navigate("project", projectId);
  focusById("project-name");
}

// -- the projects page --
function projectList() {
  if (list === null && !listIssues.length) return staticHtml('<div class="empty">프로젝트 목록을 불러오는 중입니다.</div>');
  if (listIssues.length) return `<div class="wf-banner bad">${unavailable ? tr(NOT_AVAILABLE) : tr("프로젝트 목록을 불러오지 못했습니다.")}</div>${unavailable ? "" : issueList(listIssues)}`;
  if (!list!.length) return staticHtml('<div class="empty">아직 만든 프로젝트가 없습니다. 이름과 목적만 정하면 바로 시작할 수 있습니다.</div>');
  return ui`<div class="table-scroll"><table><thead><tr><th>프로젝트</th><th>작업 유형</th><th>데이터</th><th>그래프 단계</th><th></th></tr></thead><tbody>${list!.map(project => ui`<tr>
    <td><b>${escapeHtml(project.name)}</b>${project.purpose ? `<small>${escapeHtml(project.purpose)}</small>` : ""}${project.legacy_origin ? ui`<small>기존 설정에서 가져옴 · ${escapeHtml(project.legacy_origin)}</small>` : ""}</td>
    <td>${escapeHtml(project.task_type || "—")}</td><td>${Array.isArray(project.datasets) ? project.datasets.length : 0}</td><td>${Number(project.graph_nodes) || 0}</td>
    <td><button type="button" class="quiet" data-action="${escapeHtml(`nav:project:${project.project_id}`)}" aria-label="${escapeHtml(project.name)} 열기">열기</button></td></tr>`).join("")}</tbody></table></div>`;
}

function legacyImport(ctx: ViewContext) {
  const saved = ctx.catalog.profiles ?? [];
  const rows = saved.map((profile: Obj) => ui`<tr><td><b>${escapeHtml(profile.ai_id)}</b><small>${escapeHtml(profile.workflow_id ?? "")}</small></td>
    <td>${escapeHtml(String(profile.training_stage ?? "sft").toUpperCase())} · ${escapeHtml(profile.training_method ?? "lora")}</td>
    <td><button type="button" data-action="${escapeHtml(`wf:projects:import:${profile.ai_id}`)}" aria-label="${escapeHtml(profile.ai_id)} 가져오기"${unavailable ? " disabled" : ""}>가져오기</button></td></tr>`).join("");
  return ui`<section class="card legacy-import" aria-labelledby="legacy-import-title"><div class="section-title"><h3 id="legacy-import-title">기존 학습 설정 가져오기</h3></div>
    <p>단일 학습에서 저장한 설정을 프로젝트 초안과 레시피로 옮깁니다. 기존 설정 파일은 읽기만 하고 바꾸지 않습니다.</p>
    ${issueList(importIssues)}
    ${rows ? ui`<div class="table-scroll"><table><thead><tr><th>저장한 설정</th><th>학습 단계 · 방법</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : staticHtml('<div class="empty">저장한 단일 학습 설정이 없습니다.</div>')}</section>`;
}

async function importLegacy(ctx: ViewContext, aiId: string) {
  const still = stillOn(ctx);
  const requestId = legacyRequests.get(aiId) ?? newRequestId();
  legacyRequests.set(aiId, requestId);
  let created: string | null = null;
  await ctx.task(tr("기존 설정 가져오는 중"), async () => {
    const result = await projectApi.importLegacy(requestId, aiId);
    legacyRequests.delete(aiId);                         // answered: a later import is a new request
    importIssues = [];
    if (result.status !== "ok" || !result.data) {
      if (notAvailable(result)) unavailable = true;
      importIssues = result.issues; return;
    }
    const data = result.data;
    const draft = ctx.store.adopt({ project: data.project, revision_sha256: data.revision_sha256 });
    importNotes.set(draft.projectId, { origin: aiId, recipe: data.recipe, recipeRevision: data.recipe_revision_sha256, warnings: data.warnings ?? [] });
    if (still()) { created = draft.projectId; fresh.add(created); }
    else ctx.notify(msg`기존 설정 ‘${aiId}’을(를) 프로젝트로 가져왔습니다. 프로젝트 목록에서 열 수 있습니다.`);
  });
  if (created) await openCreated(ctx, created);
}

async function loadList(ctx: ViewContext) {
  await ctx.task(tr("프로젝트 목록 불러오는 중"), async () => {
    const result = await projectApi.list();
    if (result.status === "ok" && result.data) { list = result.data.projects; listIssues = []; return; }
    if (notAvailable(result)) unavailable = true;
    listIssues = result.issues.length ? result.issues : [localIssue("", tr("프로젝트 목록을 불러오지 못했습니다."))];
  });
}

// -- one project --
function conflictBanner(issues: WorkflowIssue[] | null) {
  if (!issues) return "";
  const revision = issues.some(issue => issue.code === "revision_conflict");
  return ui`<div class="wf-banner warn project-banner" role="status"><div>${revision ? escapeHtml(localize(issues.find(issue => issue.code === "revision_conflict")!).text) : issueList(issues)}
    <small>내 초안은 그대로 남아 있습니다.</small></div>${revision ? `<div class="actions">${button(tr("최신 버전 불러오기"), "wf:project:reload")}${button(tr("내 변경으로 덮어쓰기"), "wf:project:overwrite")}</div>` : ""}</div>`;
}

function importNote(projectId: string) {
  const note = importNotes.get(projectId);
  if (!note) return "";
  return ui`<section class="wf-banner import-note" role="status"><div><b>단일 학습 설정 ‘${escapeHtml(note.origin)}’에서 가져왔습니다.</b>
    <p>레시피 ‘${escapeHtml(note.recipe.name)}’ · 버전 <span class="mono">${short(note.recipeRevision)}</span>를 함께 만들었습니다. 데이터·GPU·승인 정보는 레시피에 들어가지 않습니다.</p>
    ${note.warnings.length ? issueList(note.warnings) : ""}</div>${button(tr("레시피 열기"), `nav:recipes:${note.recipe.recipe_id}`, "quiet")}</section>`;
}

function profileSelect(spec: ProjectSpec) {
  const current = spec.default_executor_profile_id ?? "";
  const options: [string, string][] = [["", tr("지정 안 함")], ...(profiles ?? []).map(profile =>
    [profile.profile_id, profile.fields?.provider ? `${profile.profile_id} · ${profile.fields.provider}` : profile.profile_id] as [string, string])];
  if (current && !options.some(([id]) => id === current)) options.push([current, msg`${current} · 목록에 없음`]);
  const html = select(tr("기본 실행 프로필"), "default_executor_profile_id", current, options).replace("<select ", '<select id="project-default-profile" ');
  return `<div class="project-field">${html}<small class="muted">${profilesError ? msg`실행 프로필 목록을 불러오지 못했습니다. ${escapeHtml(profilesError)}` : tr("선택 · 그래프 단계가 따로 고르지 않으면 이 실행 위치를 제안합니다.")}</small></div>`;
}

function datasetOptions(ctx: ViewContext) {
  const records: Obj[] = ctx.catalog.datasets ?? [];
  const options = records.map(record => {
    const value = `${record.dataset_id}:${record.version}`;
    const missing = !HASH.test(String(record.sha256 ?? ""));
    const label = `${record.dataset_id} · v${record.version}${practice(record.dataset_id) ? ` · ${tr(PRACTICE)}` : ""}${missing ? tr(" · 해시 기록 없음") : ""}`;
    return `<option value="${escapeHtml(value)}"${bindingDraft.dataset === value ? " selected" : ""}${missing ? " disabled" : ""}>${escapeHtml(label)}</option>`;
  }).join("");
  return ui`<label class="field"><span>등록한 데이터 버전</span><select id="binding-dataset" aria-label="등록한 데이터 버전" data-key="binding_dataset"><option value="">데이터 버전 선택</option>${options}</select></label>`;
}

function bindings(ctx: ViewContext, spec: ProjectSpec, issues: WorkflowIssue[]) {
  const rows = spec.datasets.map(binding => {
    const uses = graphUses(spec, binding.binding_id);
    const action = uses ? ui`<small class="muted">그래프 단계 ${uses}개가 쓰는 중이라 해제할 수 없습니다.</small>`
      : ui`<button type="button" class="quiet" data-action="${escapeHtml(`wf:project:unbind:${binding.binding_id}`)}" aria-label="${escapeHtml(binding.binding_id)} 연결 해제">연결 해제</button>`;
    return `<tr><td class="mono">${escapeHtml(binding.binding_id)}</td><td>${escapeHtml(ROLE_LABEL[binding.role] ?? binding.role)}</td>
      <td>${escapeHtml(binding.dataset.dataset_id)} · v${escapeHtml(binding.dataset.version)}${practice(binding.dataset.dataset_id) ? `<small>${tr(PRACTICE)}</small>` : ""}</td>
      <td class="mono">${short(binding.dataset.sha256)}</td><td>${action}</td></tr>`;
  }).join("");
  return ui`<section class="card" aria-labelledby="binding-title"><div class="section-title"><h3 id="binding-title">데이터 연결 <span class="count">${spec.datasets.length}</span></h3></div>
    <p>등록한 데이터 버전을 학습·검증·시험 역할로 연결합니다. 연결 ID는 학습 그래프의 단계가 데이터를 가리킬 때 씁니다.</p>
    ${rows ? ui`<div class="table-scroll"><table class="binding-table"><thead><tr><th>연결 ID</th><th>역할</th><th>데이터 버전</th><th>SHA256</th><th></th></tr></thead><tbody>${rows}</tbody></table></div>` : staticHtml('<div class="empty">아직 연결한 데이터가 없습니다. 데이터는 나중에 연결해도 됩니다.</div>')}
    ${issueList(issues)}
    <fieldset class="project-fieldset binding-add"${ctx.busy ? " disabled" : ""}><div class="grid">${datasetOptions(ctx)}
    ${select(tr("데이터 역할"), "binding_role", bindingDraft.role, ROLES).replace("<select ", '<select id="binding-role" ')}</div>
    <div class="actions">${button(tr("데이터 연결 추가"), "wf:project:bind")}${ctx.hasPage("data-import") && ctx.arg ? button(tr("CSV·JSON 가져오기"), `nav:data-import:${ctx.arg}`, "quiet") : ""}</div></fieldset>
    <p class="binding-note${bindingNote?.kind === "error" ? " error-text" : ""}" role="status" aria-live="polite">${bindingNote ? escapeHtml(bindingNote.text) : ""}</p></section>`;
}

function projectPage(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (!projectId) return ui`<section class="card"><div class="empty">프로젝트 목록에서 프로젝트를 고르세요.</div>${button(tr("프로젝트 목록"), "nav:projects")}</section>`;
  const draft = ctx.store.draft(projectId);
  const failed = loadIssues.get(projectId);
  if (!draft) return `<section class="card">${failed ? `<div class="wf-banner bad">${unavailable ? tr(NOT_AVAILABLE) : tr("프로젝트를 불러오지 못했습니다.")}</div>${unavailable ? "" : issueList(failed)}<div class="actions">${button(tr("다시 불러오기"), "wf:project:reopen")}${button(tr("프로젝트 목록"), "nav:projects", "quiet")}</div>` : staticHtml('<div class="empty">프로젝트를 불러오는 중입니다.</div>')}</section>`;
  const spec = draft.spec;
  const issues = draft.issues;
  const placed = (pointer: string) => issuesAt(issues, pointer);
  const routed = ["/spec/name", "/spec/purpose", "/spec/task_type", "/spec/default_executor_profile_id", "/spec/datasets"];
  const others = issues.filter(issue => !routed.some(pointer => issue.path === pointer || issue.path.startsWith(pointer + "/")));
  const state = draft.dirty ? statusPill("warn", tr("저장 안 된 변경")) : draft.saved ? statusPill("neutral", msg`저장된 버전 ${String(draft.saved.revision_sha256).slice(0, 8)}`) : "";
  const links = [ctx.hasPage("graph") ? button(tr("학습 그래프"), `nav:graph:${projectId}`) : "", ctx.hasPage("runs") ? button(tr("그래프 실행"), "nav:runs") : ""].join("");
  return ui`${conflictBanner(draft.conflict)}${importNote(projectId)}
  ${failed ? ui`<div class="wf-banner bad">최신 버전을 다시 읽지 못해 이 PC의 초안을 보여 줍니다.</div>${issueList(failed)}` : ""}
  <form id="project-form" class="project-form" data-project-id="${escapeHtml(projectId)}" novalidate aria-labelledby="project-form-title">
  <section class="card"><div class="section-title"><h3 id="project-form-title">목적과 기본 정보</h3>${state}</div>
    ${spec.legacy_origin ? ui`<p class="muted">기존 설정에서 가져옴 · ${escapeHtml(spec.legacy_origin)}</p>` : ""}
    <fieldset class="project-fieldset"${ctx.busy ? " disabled" : ""}><div class="grid">
    ${textInput(tr("프로젝트 이름"), "name", spec.name, "project-name", { max: 200, required: true, issues: placed("/spec/name") })}
    ${textInput(tr("작업 유형"), "task_type", spec.task_type, "project-task-type", { max: 200, help: tr("선택 · 예: 요약, 분류, 정보 추출"), issues: placed("/spec/task_type") })}
    ${textArea(tr("목적"), "purpose", spec.purpose, "project-purpose", { max: 4000, help: tr("선택 · 이 프로젝트로 확인하려는 것"), issues: placed("/spec/purpose") })}
    ${profileSelect(spec)}${issueList(placed("/spec/default_executor_profile_id"))}
    </div></fieldset></section>
  ${bindings(ctx, spec, placed("/spec/datasets"))}
  ${links ? ui`<section class="card"><h3>다음 단계</h3><div class="actions">${links}</div></section>` : ""}
  ${others.length ? `<div class="project-summary" role="status">${issueList(others, { heading: tr("저장하지 못한 이유") })}</div>` : ""}
  <div class="actions project-actions"><button type="submit" class="primary" data-focus-key="project-save"${unavailable ? " disabled" : ""}>저장</button>${button(tr("프로젝트 목록"), "nav:projects", "quiet")}<span class="muted">${unavailable ? tr(NOT_AVAILABLE) : tr("저장해야 다른 화면과 실행 계획에 반영됩니다.")}</span></div>
  </form>`;
}

async function enterProject(ctx: ViewContext) {
  const projectId = ctx.arg;
  bindingNote = null;
  if (!projectId) return;
  loadIssues.delete(projectId);
  await ctx.task(tr("프로젝트 불러오는 중"), async () => {
    const adoptedNow = fresh.delete(projectId) && ctx.store.draft(projectId) !== undefined;   // from create or import
    if (!adoptedNow) {
      const result = await ctx.store.open(projectId);  // null: a dirty draft is kept as it is
      if (result && result.status !== "ok") {
        if (notAvailable(result)) unavailable = true;
        loadIssues.set(projectId, result.issues);
      }
    }
    try { profiles = (await executorProfiles()).profiles; profilesError = null; }
    catch (error) { profilesError = errorText(error); }
  });
}

/** Saves through `work` and keeps keyboard focus on the control that started it (the save button by default). */
async function saving(ctx: ViewContext, label: string, work: (projectId: string) => Promise<WorkflowResult<unknown>>) {
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  const still = stillOn(ctx);
  await ctx.task(label, async () => {
    const result = await work(projectId);
    if (result.status === "ok") { ctx.notify(tr("프로젝트를 저장했습니다.")); return; }
    if (notAvailable(result)) unavailable = true;
    // The store keeps a conflict on the draft; an invalid answer (also a failed read before overwriting) shows its issues.
    if (result.status === "invalid") draft.issues = result.issues;
  });
  // The shell gives the focus back to the control that started the save; this only covers a control that is gone.
  refocus(still, "project-save");
}

async function saveProject(ctx: ViewContext) {
  captureForms(ctx);
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  if (!draft.spec.name.trim()) {
    draft.issues = [localIssue("/spec/name", tr("프로젝트 이름을 입력하세요."))];
    ctx.render(); focusById("project-name"); return;
  }
  await saving(ctx, tr("프로젝트 저장 중"), id => ctx.store.save(id));
}

async function reloadProject(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (!projectId || !ctx.store.draft(projectId)) return;
  const answer = await openDialog({ title: tr("최신 버전 불러오기"),
    bodyHtml: staticHtml("<p>다른 곳에서 저장한 최신 버전으로 바꿉니다. 저장하지 않은 내 변경은 사라집니다.</p>"),
    actions: [{ id: "back", label: tr("돌아가기") }, { id: "reload", label: tr("불러오기"), primary: true }] });
  if (answer?.action !== "reload") return;
  const still = stillOn(ctx);
  await ctx.task(tr("최신 버전 불러오는 중"), async () => {
    const result = await ctx.store.reloadLatest(projectId);
    if (result.status !== "ok") throw new Error(describe(result.issues));
    ctx.notify(tr("최신 버전을 불러왔습니다."));
  });
  refocus(still, "project-save");
}

async function overwriteProject(ctx: ViewContext) {
  const projectId = ctx.arg;
  if (!projectId || !ctx.store.draft(projectId)) return;
  const answer = await openDialog({ title: tr("내 변경으로 덮어쓰기"),
    bodyHtml: staticHtml("<p>다른 곳에서 먼저 저장한 변경을 지금 내 초안으로 덮어씁니다. 그 변경은 최신 버전에서 빠지고 이전 버전 기록으로만 남습니다.</p>"),
    actions: [{ id: "back", label: tr("돌아가기") }, { id: "overwrite", label: tr("덮어쓰기"), primary: true }] });
  if (answer?.action !== "overwrite") return;
  await saving(ctx, tr("내 변경으로 저장 중"), id => ctx.store.overwriteWithDraft(id));
}

function addBinding(ctx: ViewContext) {
  captureForms(ctx);
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  const record = (ctx.catalog.datasets ?? []).find((item: Obj) => `${item.dataset_id}:${item.version}` === bindingDraft.dataset);
  const still = stillOn(ctx);
  const refuse = (text: string) => { bindingNote = { kind: "error", text }; ctx.render(); refocus(still, "wf:project:bind"); };
  if (!record) return refuse(tr("연결할 데이터 버전을 고르세요."));
  if (!HASH.test(String(record.sha256 ?? ""))) return refuse(tr("이 데이터 버전에는 SHA256 기록이 없어 연결할 수 없습니다. 데이터를 다시 등록하세요."));
  if (draft.spec.datasets.some(binding => binding.dataset.dataset_id === record.dataset_id && binding.dataset.version === record.version))
    return refuse(tr("이미 연결된 데이터 버전입니다."));
  const role = bindingDraft.role;
  const binding: DatasetBinding = { binding_id: bindingIdFor(role, draft.spec.datasets), role,
    dataset: { dataset_id: String(record.dataset_id), version: String(record.version), sha256: String(record.sha256) } };
  ctx.store.edit(projectId, spec => { spec.datasets = [...spec.datasets, binding]; });
  bindingNote = { kind: "ok", text: msg`${ROLE_LABEL[role]} 데이터로 ${binding.dataset.dataset_id} · v${binding.dataset.version}을(를) 연결했습니다 (연결 ID ${binding.binding_id}). 저장해야 반영됩니다.` };
  bindingDraft = { ...bindingDraft, dataset: "" };
  ctx.render(); refocus(still, "wf:project:bind");
}

function removeBinding(ctx: ViewContext, bindingId: string) {
  captureForms(ctx);
  const projectId = ctx.arg;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (!projectId || !draft) return;
  const uses = graphUses(draft.spec, bindingId);
  if (uses) bindingNote = { kind: "error", text: msg`그래프 단계 ${uses}개가 연결 ID ${bindingId}를 쓰고 있어 해제할 수 없습니다.` };
  else {
    ctx.store.edit(projectId, spec => { spec.datasets = spec.datasets.filter(binding => binding.binding_id !== bindingId); });
    bindingNote = { kind: "ok", text: msg`연결 ID ${bindingId}를 해제했습니다. 저장해야 반영됩니다.` };
  }
  ctx.render(); refocus(() => true, "wf:project:bind");
}

/** Reads the visible project forms into the drafts (the shell calls it before every task and navigation). */
function captureForms(ctx: ViewContext) {
  const create = document.querySelector<HTMLFormElement>("#project-create-form");
  if (create && !locked(create)) {
    const next = { name: readKey(create, "name"), purpose: readKey(create, "purpose"), task_type: readKey(create, "task_type") };
    if (keyOf(next) !== keyOf(createDraft)) createDraft = next;
  }
  const form = document.querySelector<HTMLFormElement>("#project-form");
  const projectId = form?.dataset.projectId;
  const draft = projectId ? ctx.store.draft(projectId) : undefined;
  if (form && projectId && draft && !locked(form)) {
    // A text that differs only in what its control cannot hold (CRLF in the purpose's textarea; any line break in the
    // one-line name and task type inputs, from another writer) is the draft's or, after an edit typed back, the saved one.
    const text = (key: "name" | "purpose" | "task_type") => {
      const shown = readKey(form, key);
      const same = (stored: unknown) => key === "purpose" ? lf(shown) === lf(stored) : oneLine(shown) === oneLine(stored);
      const saved = draft.saved?.project[key];
      return same(draft.spec[key]) ? draft.spec[key] : saved !== undefined && same(saved) ? saved : shown;
    };
    const values = { name: text("name"), purpose: text("purpose"), task_type: text("task_type"),
      default_executor_profile_id: readKey(form, "default_executor_profile_id") || null };
    if ((Object.keys(values) as (keyof typeof values)[]).some(key => draft.spec[key] !== values[key]))
      ctx.store.edit(projectId, spec => Object.assign(spec, values));
    const dataset = form.querySelector<HTMLSelectElement>("#binding-dataset");
    const role = form.querySelector<HTMLSelectElement>("#binding-role");
    if (dataset && role) bindingDraft = { dataset: dataset.value, role: role.value as BindingRole };
  }
}

function bindForms(ctx: ViewContext) {
  shell = ctx;
  document.querySelector<HTMLFormElement>("#project-create-form")?.addEventListener("submit", event => {
    event.preventDefault();
    if (!ctx.busy) void createProject(ctx).catch(error => ctx.fail(errorText(error)));
  });
  document.querySelector<HTMLFormElement>("#project-form")?.addEventListener("submit", event => {
    event.preventDefault();
    if (!ctx.busy) void saveProject(ctx).catch(error => ctx.fail(errorText(error)));
  });
}

const projectsView: View = localized(() => ({
  id: "projects", title: "프로젝트", description: "이름과 목적에서 시작해 데이터, 레시피와 학습 그래프를 한곳에 묶습니다.",
  nav: { group: "workspace", label: "프로젝트", icon: "projects", order: 20 },
  render(ctx) {
    shell = ctx;
    return ui`<div class="projects-page"><section class="card project-list" aria-labelledby="project-list-title"><div class="section-title"><h3 id="project-list-title">프로젝트 목록 ${list ? `<span class="count">${list.length}</span>` : ""}</h3>${button(tr("목록 다시 불러오기"), "wf:projects:reload", "quiet")}</div>${projectList()}</section>
      <div class="projects-side"><section class="card" aria-labelledby="project-new-title"><div class="section-title"><h3 id="project-new-title">새 프로젝트</h3></div><p>예제 없이 이름과 목적만으로 시작합니다. 데이터와 학습 그래프는 프로젝트에서 이어서 정합니다.</p>${createForm(ctx)}</section>
      ${legacyImport(ctx)}</div></div>`;
  },
  overviewPanel(ctx) {
    shell = ctx;
    return ui`<section class="panel project-start" data-tour="project-start" aria-labelledby="project-start-title"><div class="panel-heading"><h3 id="project-start-title">빈 프로젝트 만들기</h3>${button(tr("프로젝트 목록"), "nav:projects", "quiet")}</div>
      <div class="project-start-body"><p class="muted">예제 없이 이름과 목적만으로 시작합니다. 데이터·레시피·학습 그래프는 프로젝트에서 이어서 정합니다.</p>${createForm(ctx)}</div></section>`;
  },
  bind: bindForms,
  capture: captureForms,
  async enter(ctx) { shell = ctx; importIssues = []; await loadList(ctx); },
  async action(ctx, verb, arg) {
    if (verb === "create") await createProject(ctx);
    else if (verb === "reload") await loadList(ctx);
    else if (verb === "import" && arg) await importLegacy(ctx, arg);
  },
}));

const projectView: View = localized(() => ({
  id: "project", parent: "projects", description: "목적, 데이터 연결과 기본 실행 프로필을 정하고 저장합니다.",
  get title() {
    const projectId = shell?.page === "project" ? shell.arg : null;
    const draft = projectId ? shell?.store.draft(projectId) : undefined;
    return draft?.saved?.project.name || draft?.spec.name || tr("프로젝트");
  },
  render(ctx) { shell = ctx; return projectPage(ctx); },
  bind: bindForms,
  async enter(ctx) { shell = ctx; await enterProject(ctx); },
  async action(ctx, verb, arg) {
    if (verb === "save") await saveProject(ctx);
    else if (verb === "reload") await reloadProject(ctx);
    else if (verb === "overwrite") await overwriteProject(ctx);
    else if (verb === "bind") addBinding(ctx);
    else if (verb === "unbind" && arg) removeBinding(ctx, arg);
    else if (verb === "reopen") await enterProject(ctx);
  },
}));

export const views: View[] = [projectsView, projectView];
