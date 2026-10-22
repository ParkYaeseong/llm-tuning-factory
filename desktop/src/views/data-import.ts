// Data import and split (10.3): a local CSV/JSON/JSONL file (picker kind "import", D18) → format and encoding →
// column mapping → preview with row issues → an immutable registered version → an optional reproducible split →
// bindings staged on the project that opened the page (`nav:data-import:<projectId>`), saved on the project page.
// Entering the page calls nothing; every request follows a click, and a new request waits until the last one answered.
// Every dynamic text (paths, column names, cell values, IDs, issue messages) is escaped.
import "./data-import.css";
import { imports, pickFile } from "../api";
import { issueList, issuesAt, localize, rowIssues } from "../issues";
import { bindingIdFor } from "../project-store";
import { button, errorText, escapeHtml, icon, isolate, statusPill } from "../workbench";
import type { View, ViewContext } from "../view-types";
import type { BindingRole, DatasetBinding, DatasetRef, FieldPath, ImportMapping, ImportPreview, ImportSource, InputKind,
  SplitCommitted, SplitPlan, SplitPreview, WorkflowIssue, WorkflowResult } from "../workflow-types";

/** The canonical fields the mapping offers per data kind (top-level source keys only in this screen). */
export const CANONICAL_FIELDS: Record<InputKind, { required: string[]; optional: string[] }> = {
  instruction: { required: ["instruction", "output"], optional: ["input", "system"] },
  text: { required: ["text"], optional: [] },
  preference: { required: ["instruction", "chosen", "rejected"], optional: ["input"] },
  kto: { required: ["instruction", "output", "kto_tag"], optional: ["input"] },
  prompt: { required: ["instruction"], optional: ["input"] },
};

export interface SplitDraft {
  percents: Record<BindingRole, number>; seed: number; groupField: string; outputs: Record<BindingRole, string>; version: string;
  preview: SplitPreview | null; sent: SplitPlan | null; issues: WorkflowIssue[]; committed: SplitCommitted | null;
}
export interface ImportDraft {
  path: string; source: ImportSource | null; sourcePath: string; sourceIssues: WorkflowIssue[];
  format: ImportMapping["source_format"]; encoding: ImportMapping["encoding"]; delimiter: string; kind: InputKind;
  columns: Record<string, string>;                               // canonical field → source column ("" = unused)
  trueLabels: string; falseLabels: string;                       // KTO, comma separated
  sourceFields: string[]; sourceSamples: Record<string, unknown>[];
  preview: ImportPreview | null; previewIssues: WorkflowIssue[]; previewStatus: WorkflowResult<unknown>["status"] | null;
  previewSent: ImportMapping | null;                             // the mapping of the shown preview, as sent
  previewChanged: boolean;                                       // registration answered preview_changed
  datasetId: string; datasetIdAuto: boolean; version: string; synthetic: boolean; exportAllowed: boolean;
  registerIssues: WorkflowIssue[]; registered: DatasetRef | null; registeredFields: string[];
  registeredKey: string | null;                                  // the preview, ID and version that were registered
  registeredFlags: { synthetic: boolean; export_allowed: boolean } | null;   // as registered (split outputs inherit them)
  split: SplitDraft;
}

const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
const ROLES: BindingRole[] = ["train", "validation", "test"];
const ROLE_LABEL: Record<BindingRole, string> = { train: "학습", validation: "검증", test: "시험" };
const FIELD_LABELS: Record<string, string> = { instruction: "지시문", input: "입력", output: "정답", system: "시스템 프롬프트",
  text: "텍스트", chosen: "선호 응답", rejected: "비선호 응답", kto_tag: "선호 라벨" };
const KINDS: [InputKind, string][] = [["instruction", "지시·응답 · SFT"], ["text", "텍스트 · PT"], ["preference", "선호 쌍 · RM/DPO"],
  ["kto", "선호 라벨 · KTO"], ["prompt", "프롬프트 · PPO"]];
const FORMATS: [ImportMapping["source_format"], string][] = [["csv", "CSV"], ["json", "JSON · 레코드 배열"], ["jsonl", "JSONL · 한 줄에 한 레코드"]];
const ENCODINGS: [ImportMapping["encoding"], string][] = [["utf-8", "UTF-8"], ["utf-8-sig", "UTF-8 (BOM)"], ["cp949", "CP949 · Windows 한글"]];
const DELIMITERS: [string, string, string][] = [["comma", ",", "쉼표 ,"], ["tab", "\t", "탭"], ["semicolon", ";", "세미콜론 ;"], ["pipe", "|", "세로선 |"]];
const LOCAL_ID = /^[A-Za-z0-9_-][A-Za-z0-9_.-]*$/;
const ID_RULE = "영문·숫자·_·-·.만 쓸 수 있고 .으로 시작할 수 없습니다.";
const STALE: Record<"format" | "mapping" | "changed", string> = {
  format: "형식이 바뀌어 다시 미리보아야 합니다.",
  mapping: "열 연결이 바뀌어 다시 미리보아야 합니다.",
  changed: "미리본 뒤 원본이나 설정이 바뀌었습니다. 다시 미리보세요.",
};
const SPLIT_STALE = "분할 조건이 바뀌어 다시 미리보아야 합니다.";
const REGISTERED = "이미 등록했습니다 · 다른 ID나 버전을 입력하면 새 버전으로 등록합니다.";
const ALREADY_BOUND = "이미 연결된 데이터 버전은 다시 추가하지 않았습니다.";
// Previews are bounded on the client too: a wide or long source must not make the page unusable.
const ISSUE_LIMIT = 50;
const CELL_LIMIT = 200;
const SAMPLE_LIMIT = 5;
const TABLE_COLUMNS = 30;
const COLUMN_CHOICES = 500;

export function emptySplit(datasetId = ""): SplitDraft {
  return { percents: { train: 80, validation: 10, test: 10 }, seed: 42, groupField: "", version: "1",
    outputs: { train: datasetId ? `${datasetId}-train` : "", validation: datasetId ? `${datasetId}-validation` : "",
      test: datasetId ? `${datasetId}-test` : "" },
    preview: null, sent: null, issues: [], committed: null };
}
export function emptyDraft(): ImportDraft {
  return { path: "", source: null, sourcePath: "", sourceIssues: [], format: "csv", encoding: "utf-8", delimiter: ",",
    kind: "instruction", columns: {}, trueLabels: "", falseLabels: "", sourceFields: [], sourceSamples: [], preview: null,
    previewIssues: [], previewStatus: null, previewSent: null, previewChanged: false, datasetId: "", datasetIdAuto: true,
    version: "1", synthetic: false, exportAllowed: false, registerIssues: [], registered: null, registeredFields: [],
    registeredKey: null, registeredFlags: null, split: emptySplit() };
}

const labels = (text: string) => text.split(",").map(label => label.trim()).filter(Boolean);

/** The request mapping: only the mapped columns of the current kind; CP949 and the delimiter apply to CSV only. */
export function mappingFrom(draft: ImportDraft): ImportMapping {
  const csv = draft.format === "csv";
  const { required, optional } = CANONICAL_FIELDS[draft.kind];
  const fields: Record<string, FieldPath> = {};
  for (const name of [...required, ...optional]) if (draft.columns[name]) fields[name] = [draft.columns[name]];
  const yes = labels(draft.trueLabels), no = labels(draft.falseLabels);
  return { schema_version: 1, transform_version: "text-mapping-v1", source_format: draft.format,
    encoding: !csv && draft.encoding === "cp949" ? "utf-8" : draft.encoding, csv_delimiter: csv ? draft.delimiter : ",",
    kind: draft.kind, formatting: "alpaca", fields, message_tags: {},
    boolean_labels: draft.kind === "kto" && (yes.length || no.length) ? { true: yes, false: no } : {} };
}

/** Whole percentages from 0 to 100 summing to 100, as basis points; null otherwise ("합계가 100%가 되어야 합니다."). */
export function basisPoints(percents: Record<BindingRole, number>): { train: number; validation: number; test: number } | null {
  const values = ROLES.map(role => percents[role]);
  if (!values.every(value => Number.isInteger(value) && value >= 0 && value <= 100)) return null;
  if (values.reduce((sum, value) => sum + value, 0) !== 100) return null;
  return { train: percents.train * 100, validation: percents.validation * 100, test: percents.test * 100 };
}

/** The format the extension names, if any. */
export function guessFormat(path: string): ImportMapping["source_format"] | null {
  const extension = /\.([A-Za-z0-9]+)$/.exec(path.trim())?.[1].toLowerCase();
  return extension === "csv" || extension === "json" || extension === "jsonl" ? extension : null;
}
/** A dataset ID suggestion from the file name (empty when nothing usable remains, e.g. a Korean name). */
function idFromPath(path: string) {
  const stem = (path.split(/[\\/]/).pop() ?? "").replace(/\.[^.]*$/, "");
  const id = stem.replace(/[^A-Za-z0-9_.-]+/g, "_").replace(/^\.+/, "").replace(/_+/g, "_").slice(0, 64);
  return /[A-Za-z0-9]/.test(id) ? id : "";
}
/** Key-order independent JSON for comparing what was sent with what the page shows now. */
function stable(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(stable).join(",")}]`;
  if (value && typeof value === "object")
    return `{${Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + stable((value as Record<string, unknown>)[key])).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}

// -- module state (kept while the user visits other pages) --
let draft: ImportDraft = emptyDraft();
let unavailable = false;                                 // the workflow channel answered not_available
let picking = false;
let labelIssues: WorkflowIssue[] = [];                   // a one-sided KTO label set, refused before any request
let link: { projectId: string | null; issues: WorkflowIssue[]; note: string | null } = { projectId: null, issues: [], note: null };

const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const clientIssue = (path: string, message: string): WorkflowIssue => ({ code: "client", path, message });
const short = (hash: unknown) => escapeHtml(String(hash ?? "").slice(0, 8));
const ref = (dataset: DatasetRef) => `${escapeHtml(dataset.dataset_id)} · v${escapeHtml(dataset.version)} · ${short(dataset.sha256)}`;
const disabledIf = (condition: boolean) => condition ? " disabled" : "";
const busyFieldset = (ctx: ViewContext) => `<fieldset class="import-fieldset"${disabledIf(ctx.busy)}>`;
function focusById(id: string) { document.getElementById(id)?.focus({ preventScroll: false }); }
/** The page a step started on: its result takes the focus only while the user is still there (never across pages). */
function here(ctx: ViewContext) {
  const page = ctx.page, arg = ctx.arg;
  return { still: () => ctx.page === page && ctx.arg === arg };
}
/** A count or size from the backend as text: only a finite number is shown (a malformed answer shows "—", never markup). */
const count = (value: unknown) => typeof value === "number" && Number.isFinite(value) ? String(value) : "—";
function size(bytes: unknown) {
  if (typeof bytes !== "number" || !Number.isFinite(bytes)) return "—";
  if (bytes < 1024) return `${bytes} B`;
  return bytes < 1024 * 1024 ? `${(bytes / 1024).toFixed(1)} KB` : `${(bytes / 1024 / 1024).toFixed(1)} MB`;
}
function cell(value: unknown) {
  const text = value === undefined ? "—" : typeof value === "string" ? value : JSON.stringify(value);
  return escapeHtml(text.length > CELL_LIMIT ? `${text.slice(0, CELL_LIMIT)}…` : text);
}
/** A source name (path, column) isolated from the surrounding text's direction: `<bdi>` in markup; first-strong
 * isolate marks in `<option>` text, which cannot hold markup. */
const isolated = isolate;
const isolatedText = (name: string) => `\u2068${name}\u2069`;
const flagsText = (flags: { synthetic: boolean; export_allowed: boolean }) =>
  `${flags.synthetic ? "합성 데이터" : "사용자 데이터"} · GPU 전송 ${flags.export_allowed ? "허용" : "미허용"}`;
/** Column choices: the first COLUMN_CHOICES source columns, plus the chosen one when it lies beyond them. */
function columnChoices(fields: string[], current: string): [string, string][] {
  const shown = fields.slice(0, COLUMN_CHOICES).map(field => [field, isolatedText(field)] as [string, string]);
  if (current && !shown.some(([field]) => field === current))
    shown.push([current, fields.includes(current) ? isolatedText(current) : `${isolatedText(current)} · 원본에서 찾지 못함`]);
  return shown;
}
const choicesNote = (fields: string[]) => fields.length > COLUMN_CHOICES
  ? `<p class="muted import-cap">열이 ${fields.length}개라 앞 ${COLUMN_CHOICES}개만 고를 수 있습니다. 필요한 열이 뒤에 있으면 열을 줄인 파일로 가져오세요.</p>` : "";
/** What the register request names: the shown preview, the ID and the version. */
const registerKey = () => stable({ preview_sha256: draft.preview?.preview_sha256 ?? null, dataset_id: draft.datasetId, version: draft.version });
function setFormat(format: ImportMapping["source_format"]) {
  draft.format = format;
  if (format !== "csv" && draft.encoding === "cp949") draft.encoding = "utf-8";
}

// -- preview state --
type PreviewState = "none" | "discovery" | "fresh" | "format" | "mapping" | "changed";
/** How the shown preview relates to the current choices. A preview without mapped columns only discovered them. */
function previewState(): PreviewState {
  const sent = draft.previewSent;
  if (!sent) return "none";
  if (draft.previewChanged) return "changed";
  const now = mappingFrom(draft);
  if (now.source_format !== sent.source_format || now.encoding !== sent.encoding || now.csv_delimiter !== sent.csv_delimiter) return "format";
  if (!Object.keys(sent.fields).length) return "discovery";
  return stable(now) === stable(sent) ? "fresh" : "mapping";
}
/** Why registration is not possible now, or null when the shown preview can be registered. */
function registerBlock(): string | null {
  if (!draft.source) return "원본 파일을 먼저 가져오세요.";
  const state = previewState();
  if (state === "none" || state === "discovery") return "열을 연결하고 문제 없는 미리보기를 확인해야 등록할 수 있습니다.";
  if (state !== "fresh") return STALE[state];
  if (draft.previewStatus !== "ok" || draft.previewIssues.length || !draft.preview) return "미리보기의 문제를 고친 뒤 다시 미리보세요.";
  // Registering the same preview, ID and version again would only answer the same version (idempotent on the server).
  if (draft.registered && draft.registeredKey === registerKey()) return REGISTERED;
  return null;
}
/** The shown preview mapped no column: it only discovered the columns (every row fails, so rows are not problems). */
const discovered = () => Boolean(draft.previewSent && !Object.keys(draft.previewSent.fields).length);
/** Issues the shown preview lists: a discovery lists only file-level ones. */
function shownPreviewIssues() {
  return discovered() ? draft.previewIssues.filter(issue => typeof issue.row_number !== "number") : draft.previewIssues;
}

// -- split state --
function buildPlan(): { plan: SplitPlan } | { issue: WorkflowIssue; focus: string } {
  const split = draft.split;
  const refuse = (path: string, message: string, focus: string) => ({ issue: clientIssue(path, message), focus });
  const points = basisPoints(split.percents);
  if (!points) return refuse("/plan/train_basis_points", "비율은 0~100의 정수로, 합계가 100%가 되어야 합니다.", "import-split-train");
  if (!Number.isSafeInteger(split.seed)) return refuse("/plan/seed", "시드는 정수로 입력하세요.", "import-seed");
  if (!LOCAL_ID.test(split.version)) return refuse("/plan/version", `분할 버전은 ${ID_RULE}`, "import-split-version");
  const outputs: SplitPlan["outputs"] = {};
  const source = draft.registered!;
  for (const role of ROLES) {
    if (!points[role]) continue;
    const id = split.outputs[role];
    if (!LOCAL_ID.test(id)) return refuse(`/plan/outputs/${role}/dataset_id`, `${ROLE_LABEL[role]} 데이터 ID는 ${ID_RULE}`, `import-out-${role}`);
    if (Object.values(outputs).some(output => output?.dataset_id === id))
      return refuse(`/plan/outputs/${role}/dataset_id`, "학습·검증·시험 데이터 ID는 서로 달라야 합니다.", `import-out-${role}`);
    if (id === source.dataset_id && split.version === source.version)
      return refuse(`/plan/outputs/${role}/dataset_id`, `등록한 원본(${source.dataset_id} · v${source.version})과 같은 ID·버전은 쓸 수 없습니다.`, `import-out-${role}`);
    outputs[role] = { dataset_id: id, version: split.version };
  }
  return { plan: { dataset: { ...source }, seed: split.seed, train_basis_points: points.train,
    validation_basis_points: points.validation, test_basis_points: points.test,
    group_field: split.groupField ? [split.groupField] : null, outputs } };
}
function splitFresh() {
  const built = draft.registered ? buildPlan() : null;
  return Boolean(built && "plan" in built && draft.split.sent && stable(built.plan) === stable(draft.split.sent));
}

// -- rendering --
// Controls marked `data-import-structure` (format, kind) change which controls exist: the page renders again.
// Controls marked `data-import-derived` change only what is derived from them (stale notes, enabled steps): those
// regions are patched, so typing, focus and Tab order in the edited control are never disturbed.
type Reaction = "structure" | "derived" | "";
const reacts = (reaction: Reaction) => reaction ? ` data-import-${reaction}` : "";
function selectControl(id: string, label: string, value: string, options: [string, string][], reaction: Reaction = "derived") {
  return `<label class="field"><span>${escapeHtml(label)}</span><select id="${id}" aria-label="${escapeHtml(label)}"${reacts(reaction)}>${options.map(([optionValue, title]) =>
    `<option value="${escapeHtml(optionValue)}"${optionValue === value ? " selected" : ""}>${escapeHtml(title)}</option>`).join("")}</select></label>`;
}
function textControl(id: string, label: string, value: string, options: { issues?: WorkflowIssue[]; help?: string; type?: string; derived?: boolean; extra?: string } = {}) {
  const issues = options.issues ?? [];
  const described = issues.length ? ` aria-invalid="true" aria-describedby="${id}-issues"` : "";
  return `<div class="import-field"><label class="field"><span>${escapeHtml(label)}</span><input id="${id}" type="${options.type ?? "text"}" aria-label="${escapeHtml(label)}" value="${escapeHtml(value)}"${described}${reacts(options.derived ? "derived" : "")}${options.extra ?? ""}>${options.help ? `<small>${escapeHtml(options.help)}</small>` : ""}</label>${issues.length ? `<div id="${id}-issues">${issueList(issues)}</div>` : ""}</div>`;
}
const numberText = (value: number) => Number.isFinite(value) ? String(value) : "";

/** File-level and row issues with their place: "3행" (1-based source row, header excluded), "헤더" or "파일". File-level
 * issues come first, so one the server appends after every row issue (`size_limit`) is never cut off by the limit. */
function previewIssueList(issues: WorkflowIssue[]) {
  if (!issues.length) return "";
  const placed = rowIssues(issues);
  const ordered = [...placed.filter(({ row }) => row === null), ...placed.filter(({ row }) => row !== null)];
  const items = ordered.slice(0, ISSUE_LIMIT).map(({ row, issue }) => {
    const { text, original } = localize(issue);
    const where = row !== null ? `${row}행` : issue.code === "duplicate_header" || /header/i.test(issue.message) ? "헤더" : "파일";
    const source = original === null ? "" : `<details><summary>원문 보기</summary><span class="issue-original">${escapeHtml(original)}</span></details>`;
    return `<li data-code="${escapeHtml(issue.code)}"><span class="import-issue-where">${escapeHtml(where)}</span><span>${escapeHtml(text)}</span>${source}</li>`;
  });
  if (issues.length > ISSUE_LIMIT) items.push(`<li class="issue-more">외 ${issues.length - ISSUE_LIMIT}건</li>`);
  return `<ul class="issue-list import-issues" aria-label="미리보기 문제">${items.join("")}</ul>`;
}

/** The first SAMPLE_LIMIT rows and TABLE_COLUMNS columns, with a note when columns were left out. */
function sampleTable(noun: string, unit: string, allColumns: [string, string][], allRows: Record<string, unknown>[]) {
  const rows = allRows.slice(0, SAMPLE_LIMIT), columns = allColumns.slice(0, TABLE_COLUMNS);
  if (!rows.length || !columns.length) return "";
  const title = `${noun} 앞 ${rows.length}${unit}`;
  const note = allColumns.length > columns.length ? `<p class="muted import-cap">열 ${allColumns.length}개 중 앞 ${columns.length}개만 표시합니다.</p>` : "";
  return `<div class="import-table"><p class="import-table-title">${escapeHtml(title)}</p>${note}<div class="table-scroll"><table class="import-samples" aria-label="${escapeHtml(title)}">
    <thead><tr><th>행</th>${columns.map(([, label]) => `<th>${isolated(label)}</th>`).join("")}</tr></thead>
    <tbody>${rows.map((row, index) => `<tr><td class="import-row">${index + 1}</td>${columns.map(([key]) => `<td>${cell(row[key])}</td>`).join("")}</tr>`).join("")}</tbody></table></div></div>`;
}

function projectBanner(ctx: ViewContext, projectId: string) {
  const project = ctx.store.draft(projectId);
  const name = project?.saved?.project.name || project?.spec.name;
  return `<div class="wf-banner import-project"><div>${name ? `프로젝트 <b>‘${isolate(name)}’</b>` : "선택한 프로젝트"}에 연결할 데이터를 가져옵니다. 등록이나 분할을 마친 뒤 마지막 단계에서 연결하세요.</div>${button("프로젝트로 돌아가기", `nav:project:${projectId}`, "quiet")}</div>`;
}

function sourceCard(ctx: ViewContext) {
  const pathIssues = issuesAt(draft.sourceIssues, "/path");
  const others = draft.sourceIssues.filter(issue => !pathIssues.includes(issue));
  const facts = draft.source ? `<p class="import-source-facts">가져온 원본 · <span class="mono">${isolated(draft.sourcePath)}</span> · SHA256 <span class="mono">${short(draft.source.source_sha256)}</span> · ${size(draft.source.size)}</p>` : "";
  return `<section class="card import-source" data-tour="data-import" aria-labelledby="import-source-title"><div class="section-title"><h3 id="import-source-title">1. 원본 파일</h3>${draft.source ? statusPill("good", "가져옴") : ""}</div>
    <p>이 PC의 CSV·JSON·JSONL 파일(32 MiB 이하)을 고르세요. 원본은 바뀌지 않는 사본으로 보관하고, 고른 열만 학습 형식으로 바꿉니다. 최상위 열만 연결할 수 있으며, ShareGPT 대화(messages·conversations) 파일은 데이터 화면의 ‘새 데이터 버전 등록’에서 JSON으로 등록하세요.</p>
    <form id="import-source-form" novalidate>${busyFieldset(ctx)}<div class="import-path-row">
      ${textControl("import-path", "파일 경로", draft.path, { issues: pathIssues, help: "이 PC의 절대 경로 · 네트워크 경로는 가져올 수 없습니다.", extra: ' placeholder="C:\\data\\qa.csv" spellcheck="false"' })}
      <div class="actions">${button(icon("folder") + " 파일 찾기", "wf:data-import:pick")}<button type="submit" class="primary" data-focus-key="import-source"${disabledIf(unavailable)}>가져오기</button></div>
    </div>${issueList(others)}</fieldset></form>${facts}</section>`;
}

function formatCard(ctx: ViewContext) {
  const encodings = ENCODINGS.filter(([value]) => value !== "cp949" || draft.format === "csv");
  const delimiter = DELIMITERS.find(([, char]) => char === draft.delimiter)?.[0] ?? "comma";
  return `<section class="card" aria-labelledby="import-format-title"><div class="section-title"><h3 id="import-format-title">2. 형식</h3></div>
    <p>형식은 확장자로 짐작합니다. 한글이 깨지거나 읽지 못하면 문자 인코딩을 바꿔 다시 미리보세요(CP949는 CSV에서만 쓸 수 있습니다).</p>
    ${busyFieldset(ctx)}<div class="grid">
    ${selectControl("import-format", "원본 형식", draft.format, FORMATS, "structure")}
    ${selectControl("import-encoding", "문자 인코딩", draft.encoding, encodings)}
    ${draft.format === "csv" ? selectControl("import-delimiter", "구분자", delimiter, DELIMITERS.map(([token, , title]) => [token, title])) : ""}
    ${selectControl("import-kind", "데이터 종류", draft.kind, KINDS, "structure")}
    </div></fieldset></section>`;
}

function mappingCard(ctx: ViewContext) {
  const { required, optional } = CANONICAL_FIELDS[draft.kind];
  const column = (name: string, needed: boolean) => {
    const current = draft.columns[name] ?? "";
    const options: [string, string][] = [["", needed ? "열 선택" : "연결 안 함"], ...columnChoices(draft.sourceFields, current)];
    const label = FIELD_LABELS[name] ?? name;
    return `<label class="field"><span>${escapeHtml(label)} <small${needed ? ' class="import-required"' : ""}>${needed ? "필수" : "선택"}</small></span><select id="import-col-${name}" aria-label="${escapeHtml(label)}" data-import-column="${name}" data-import-derived>${options.map(([value, title]) =>
      `<option value="${escapeHtml(value)}"${value === current ? " selected" : ""}>${escapeHtml(title)}</option>`).join("")}</select></label>`;
  };
  const kto = draft.kind === "kto" ? `<div class="grid import-labels">
    ${textControl("import-true-labels", "좋은 응답으로 볼 값", draft.trueLabels, { derived: true, help: "선호 라벨 열의 값 · 쉼표로 구분 (예: 1, 좋음)" })}
    ${textControl("import-false-labels", "나쁜 응답으로 볼 값", draft.falseLabels, { derived: true, help: "쉼표로 구분 (예: 0, 나쁨) · 참·거짓 값이면 둘 다 비워 두세요." })}</div>${issueList(labelIssues)}` : "";
  const found = draft.sourceFields.length;
  const intro = found ? `원본에서 열 ${found}개를 찾았습니다. 학습 형식의 항목마다 원본 열을 고르세요.`
    : "원본의 열을 읽지 못했습니다. 형식·구분자·문자 인코딩을 확인하고 다시 미리보세요.";
  return `<section class="card" id="import-mapping" aria-labelledby="import-mapping-title"><div class="section-title"><h3 id="import-mapping-title">3. 열 연결</h3>${found ? `<span class="count">열 ${found}개</span>` : ""}</div>
    <p>${escapeHtml(intro)}</p>${choicesNote(draft.sourceFields)}
    ${busyFieldset(ctx)}<div class="grid import-columns">${required.map(name => column(name, true)).join("")}${optional.map(name => column(name, false)).join("")}</div>${kto}</fieldset>
    ${sampleTable("원본", "행", draft.sourceFields.map(field => [field, field]), draft.sourceSamples)}</section>`;
}

/** The preview's state line (patched region `import-preview-state`): passed, problems, or why it must run again. */
function previewStatusHtml() {
  const state = previewState();
  const count = shownPreviewIssues().length;
  if (state === "none") return `<p class="muted">미리보기를 누르면 연결한 열로 바꾼 결과와 문제 행을 보여 줍니다.</p>`;
  if (state === "format" || state === "mapping" || state === "changed")
    return `<div class="import-status">${statusPill("warn", "다시 미리보기 필요")}<span class="import-stale">${escapeHtml(STALE[state])}</span></div>`;
  if (state === "discovery")
    return `<div class="import-status">${count ? statusPill("bad", `문제 ${count}건`) : statusPill("neutral", "열 확인")}<span>${count ? "원본을 읽지 못했습니다." : "열을 연결한 뒤 미리보기를 누르세요."}</span></div>`;
  return `<div class="import-status">${count || draft.previewStatus !== "ok" ? statusPill("bad", `문제 ${count}건`) : statusPill("good", "미리보기 통과")}</div>`;
}

function previewCard() {
  const preview = draft.preview;
  const shown = preview && !discovered() ? preview : null;
  const facts = shown ? `<p class="import-facts">행 ${count(shown.row_count)}개${shown.canonical_sha256 ? ` · 변환 결과 SHA256 <span class="mono">${short(shown.canonical_sha256)}</span> · ${size(shown.canonical_size ?? 0)}` : ""}</p>` : "";
  const fields = [...CANONICAL_FIELDS[draft.previewSent?.kind ?? draft.kind].required, ...CANONICAL_FIELDS[draft.previewSent?.kind ?? draft.kind].optional];
  const rows = shown ? shown.samples.slice(0, SAMPLE_LIMIT) : [];
  const keys = [...new Set([...fields.filter(name => rows.some(sample => name in sample)), ...rows.flatMap(sample => Object.keys(sample))])];
  const samples = shown ? sampleTable("변환 결과", "건", keys.map(key => [key, FIELD_LABELS[key] ?? key]), shown.samples) : "";
  return `<section class="card" id="import-preview" aria-labelledby="import-preview-title"><div class="section-title"><h3 id="import-preview-title">4. 미리보기</h3>
    <div class="actions">${button("미리보기", "wf:data-import:preview", "", unavailable)}</div></div>
    <div id="import-preview-state" role="status">${previewStatusHtml()}</div>${facts}${previewIssueList(shownPreviewIssues())}${samples}</section>`;
}

/** The register button and why it is disabled (patched region `import-register-actions`). */
function registerActionsHtml() {
  const block = registerBlock();
  return `<button type="submit" class="primary" data-focus-key="import-register"${disabledIf(Boolean(block) || unavailable)}>등록</button>${block ? `<span class="muted">${escapeHtml(block)}</span>` : ""}`;
}

function registerCard(ctx: ViewContext) {
  const idIssues = issuesAt(draft.registerIssues, "/dataset_id");
  const versionIssues = issuesAt(draft.registerIssues, "/version");
  const others = draft.registerIssues.filter(issue => !idIssues.includes(issue) && !versionIssues.includes(issue));
  return `<section class="card" id="import-register" aria-labelledby="import-register-title"><div class="section-title"><h3 id="import-register-title">5. 등록</h3>${draft.registered ? statusPill("good", "등록함") : ""}</div>
    <p>미리본 결과 그대로 데이터 버전을 등록합니다. 등록한 버전은 바뀌지 않으며, 같은 ID와 버전에 다른 내용을 등록할 수 없습니다.</p>
    <form id="import-register-form" novalidate>${busyFieldset(ctx)}<div class="grid">
      ${textControl("import-dataset-id", "데이터 ID", draft.datasetId, { issues: idIssues, help: "영문·숫자·_·-·.", derived: true })}
      ${textControl("import-version", "버전", draft.version, { issues: versionIssues, derived: true })}</div>
      <label class="check"><input id="import-synthetic" type="checkbox"${draft.synthetic ? " checked" : ""}> 더미·합성 데이터입니다</label>
      <label class="check"><input id="import-export" type="checkbox"${draft.exportAllowed ? " checked" : ""}> 이 데이터를 선택한 GPU로 전송해도 됩니다</label>
      ${issueList(others)}
      <div class="actions" id="import-register-actions">${registerActionsHtml()}</div>
    </fieldset></form>
    ${draft.registered ? `<p id="import-registered" class="import-result" tabindex="-1">등록했습니다 · <b>${ref(draft.registered)}</b>${draft.registeredFlags ? ` · ${flagsText(draft.registeredFlags)}` : ""}</p>` : ""}</section>`;
}

/** The split preview's counts and the commit button (patched region `import-split-state`). */
function splitStateHtml() {
  const split = draft.split;
  const fresh = splitFresh();
  const committedNow = Boolean(split.committed && split.preview && split.committed.plan_sha256 === split.preview.plan_sha256);
  const canCommit = Boolean(split.preview) && fresh && !committedNow && !unavailable;
  const preview = split.preview;
  const result = preview ? `${fresh ? "" : `<div class="import-status">${statusPill("warn", "다시 미리보기 필요")}<span class="import-stale">${SPLIT_STALE}</span></div>`}
    <p class="import-counts">${ROLES.map(role => `${ROLE_LABEL[role]} ${count(preview.counts[role] ?? 0)}`).join(" · ")}</p>
    <ul class="import-outputs">${ROLES.filter(role => preview.outputs[role]).map(role => `<li>${ROLE_LABEL[role]} · ${ref(preview.outputs[role]!)}</li>`).join("")}</ul>`
    : `<p class="muted">분할 미리보기에서 역할별 행 수를 확인한 뒤 등록합니다.</p>`;
  return `${result}<div class="actions">${button("분할 등록", "wf:data-import:split-commit", "primary", !canCommit)}${committedNow ? '<span class="muted">이 분할은 등록했습니다.</span>' : ""}</div>`;
}

/** The split field an issue belongs to: server pointers under `/plan`, the commit's version clash, client checks. */
function splitFieldFor(issue: WorkflowIssue): string | null {
  const under = (pointer: string) => issuesAt([issue], pointer).length > 0;
  if (issue.code === "version_exists" || under("/plan/version") || ROLES.some(role => under(`/plan/outputs/${role}/version`))) return "import-split-version";
  for (const role of ROLES) {
    if (under(`/plan/${role}_basis_points`)) return `import-split-${role}`;
    if (under(`/plan/outputs/${role}`)) return `import-out-${role}`;
  }
  return under("/plan/seed") ? "import-seed" : null;
}

function splitCard(ctx: ViewContext) {
  const split = draft.split;
  const registered = draft.registered!;
  const at = (field: string) => split.issues.filter(issue => splitFieldFor(issue) === field);
  const others = split.issues.filter(issue => splitFieldFor(issue) === null);
  const percent = (role: BindingRole) => textControl(`import-split-${role}`, `${ROLE_LABEL[role]} 비율(%)`, numberText(split.percents[role]),
    { type: "number", derived: true, issues: at(`import-split-${role}`), extra: ' min="0" max="100" step="1" inputmode="numeric"' });
  const groupOptions: [string, string][] = [["", "없음"], ...columnChoices(draft.registeredFields, split.groupField)];
  const flags = draft.registeredFlags ? flagsText(draft.registeredFlags) : null;
  const committed = split.committed ? `<div id="import-split-committed" class="import-result" tabindex="-1"><p>분할을 등록했습니다.${flags ? ` 분할 결과도 ${flags}입니다.` : ""}</p><ul class="import-outputs">${ROLES.filter(role => split.committed!.outputs[role]).map(role =>
    `<li>${ROLE_LABEL[role]} · <b>${ref(split.committed!.outputs[role]!)}</b></li>`).join("")}</ul></div>` : "";
  return `<section class="card" id="import-split" aria-labelledby="import-split-title"><div class="section-title"><h3 id="import-split-title">6. 분할 (선택)</h3>${split.committed ? statusPill("good", "분할 등록함") : ""}</div>
    <p>등록한 ‘${ref(registered)}’을 학습·검증·시험 데이터로 나눕니다. 같은 입력의 행(묶음 열을 고르면 같은 묶음 값의 행도)은 항상 같은 쪽에 들어가고, 같은 시드는 같은 결과를 냅니다. 0%인 역할은 만들지 않습니다.${flags ? ` 분할 결과도 ${flags}으로 등록됩니다.` : ""}</p>
    <form id="import-split-form" novalidate>${busyFieldset(ctx)}
      <div class="import-triple">${ROLES.map(percent).join("")}</div>
      <div class="grid import-split-grid">${textControl("import-seed", "시드", numberText(split.seed), { type: "number", derived: true, issues: at("import-seed"), extra: ' step="1" inputmode="numeric"' })}
      ${selectControl("import-group", "묶음 열", split.groupField, groupOptions)}</div>${choicesNote(draft.registeredFields)}
      <div class="import-triple import-split-grid">${ROLES.map(role => textControl(`import-out-${role}`, `${ROLE_LABEL[role]} 데이터 ID`, split.outputs[role], { derived: true, issues: at(`import-out-${role}`) })).join("")}</div>
      <div class="grid import-split-grid">${textControl("import-split-version", "분할 버전", split.version, { derived: true, issues: at("import-split-version") })}</div>
      ${issueList(others)}
      <div class="actions"><button type="submit" data-focus-key="import-split-preview"${disabledIf(unavailable)}>분할 미리보기</button></div>
    </fieldset></form>
    <div id="import-split-state" class="import-split-state">${splitStateHtml()}</div>${committed}</section>`;
}

/** What "프로젝트에 연결" binds: the committed split outputs by role, else the registered version as 학습. */
function pendingRefs(): { role: BindingRole; dataset: DatasetRef }[] {
  const committed = draft.split.committed;
  if (committed) return ROLES.filter(role => committed.outputs[role]).map(role => ({ role, dataset: committed.outputs[role]! }));
  return draft.registered ? [{ role: "train", dataset: draft.registered }] : [];
}
const sameBinding = (a: DatasetBinding, b: DatasetBinding) => stable(a) === stable(b);

function linkCard(ctx: ViewContext, projectId: string) {
  const refs = pendingRefs();
  const project = ctx.store.draft(projectId);
  const saved = project?.saved?.project.datasets ?? [];
  const unsaved = project ? project.spec.datasets.filter(binding => !saved.some(other => sameBinding(binding, other))) : [];
  const issues = link.projectId === projectId ? link.issues : [];
  const note = link.projectId === projectId ? link.note : null;
  const staged = unsaved.length ? `<div id="import-staged" class="import-staged" tabindex="-1"><p><b>저장 전 연결: ${unsaved.map(binding => escapeHtml(binding.binding_id)).join(", ")}</b></p>
    <div class="table-scroll"><table><thead><tr><th>연결 ID</th><th>역할</th><th>데이터 버전</th></tr></thead><tbody>${unsaved.map(binding =>
      `<tr data-binding="${escapeHtml(binding.binding_id)}"><td class="mono">${escapeHtml(binding.binding_id)}</td><td>${escapeHtml(ROLE_LABEL[binding.role] ?? binding.role)}</td><td>${ref(binding.dataset)}</td></tr>`).join("")}</tbody></table></div></div>` : "";
  return `<section class="card" id="import-link" aria-labelledby="import-link-title"><div class="section-title"><h3 id="import-link-title">7. 프로젝트에 연결</h3></div>
    <p>${refs.length ? `연결할 데이터: ${refs.map(item => `${ROLE_LABEL[item.role]} · ${ref(item.dataset)}`).join(", ")}` : "등록하거나 분할을 등록하면 연결할 수 있습니다."}</p>
    <p class="muted">연결은 프로젝트 초안에만 더해집니다. 프로젝트 화면에서 저장해야 반영됩니다.</p>
    ${issueList(issues)}
    <div class="actions">${button("프로젝트에 연결", "wf:data-import:link", "primary", !refs.length || unavailable)}${button("프로젝트에서 저장하기", `nav:project:${projectId}`, "quiet")}</div>
    ${note ? `<p id="import-link-note" class="muted import-link-note" tabindex="-1">${escapeHtml(note)}</p>` : ""}${staged}</section>`;
}

function render(ctx: ViewContext) {
  const projectId = ctx.arg;
  return `<div id="data-import-page" class="import-page">
    ${unavailable ? `<div class="wf-banner bad">${NOT_AVAILABLE}</div>` : ""}
    ${projectId ? projectBanner(ctx, projectId) : ""}
    ${sourceCard(ctx)}${formatCard(ctx)}
    ${draft.source ? mappingCard(ctx) + previewCard() + registerCard(ctx) : ""}
    ${draft.registered ? splitCard(ctx) : ""}
    ${projectId ? linkCard(ctx, projectId) : ""}</div>`;
}

// -- capture and binding --
/** Reads the page's controls into the draft (the shell calls it before every task and navigation). */
function capture() {
  if (!document.getElementById("data-import-page")) return;
  // A control locked while busy (inside a disabled fieldset) was not edited: reading it back could undo the running
  // step's result (the view contract), so it reads as absent.
  const usable = <T extends HTMLInputElement | HTMLSelectElement>(element: T | null) => element && !element.matches(":disabled") ? element : null;
  const value = (id: string) => usable(document.querySelector<HTMLInputElement | HTMLSelectElement>(`#${id}`))?.value;
  const checked = (id: string) => usable(document.querySelector<HTMLInputElement>(`#${id}`))?.checked;
  const number = (id: string, fallback: number) => {
    const text = value(id);
    return text === undefined ? fallback : text.trim() === "" ? Number.NaN : Number(text);
  };
  draft.path = value("import-path") ?? draft.path;
  const format = FORMATS.find(([id]) => id === value("import-format"))?.[0];
  if (format) draft.format = format;
  const encoding = ENCODINGS.find(([id]) => id === value("import-encoding"))?.[0];
  if (encoding) draft.encoding = encoding;
  const delimiter = DELIMITERS.find(([token]) => token === value("import-delimiter"))?.[1];
  if (delimiter) draft.delimiter = delimiter;
  const kind = KINDS.find(([id]) => id === value("import-kind"))?.[0];
  if (kind) draft.kind = kind;
  setFormat(draft.format);
  document.querySelectorAll<HTMLSelectElement>("#data-import-page [data-import-column]").forEach(select => {
    if (usable(select)) draft.columns[select.dataset.importColumn!] = select.value;
  });
  draft.trueLabels = value("import-true-labels") ?? draft.trueLabels;
  draft.falseLabels = value("import-false-labels") ?? draft.falseLabels;
  const datasetId = value("import-dataset-id");
  if (datasetId !== undefined && datasetId.trim() !== draft.datasetId) { draft.datasetId = datasetId.trim(); draft.datasetIdAuto = false; }
  draft.version = value("import-version")?.trim() ?? draft.version;
  draft.synthetic = checked("import-synthetic") ?? draft.synthetic;
  draft.exportAllowed = checked("import-export") ?? draft.exportAllowed;
  const split = draft.split;
  for (const role of ROLES) {
    split.percents[role] = number(`import-split-${role}`, split.percents[role]);
    split.outputs[role] = value(`import-out-${role}`)?.trim() ?? split.outputs[role];
  }
  split.seed = number("import-seed", split.seed);
  split.groupField = value("import-group") ?? split.groupField;
  split.version = value("import-split-version")?.trim() ?? split.version;
}

function bind(ctx: ViewContext) {
  if (!document.getElementById("data-import-page")) return;
  const guarded = (work: () => Promise<void>) => (event: Event) => {
    event.preventDefault();
    if (!ctx.busy) void work().catch(error => ctx.fail(errorText(error)));
  };
  document.querySelector("#import-source-form")?.addEventListener("submit", guarded(() => importSource(ctx)));
  document.querySelector("#import-register-form")?.addEventListener("submit", guarded(() => register(ctx)));
  document.querySelector("#import-split-form")?.addEventListener("submit", guarded(() => previewSplit(ctx)));
  // The format and the kind decide which controls exist: render again and keep the changed select in focus.
  document.querySelectorAll<HTMLSelectElement>("#data-import-page select[data-import-structure]").forEach(select =>
    select.addEventListener("change", () => {
      capture();
      ctx.render();
      document.getElementById(select.id)?.focus({ preventScroll: true });
    }));
  // Every other choice of the mapping or the split only patches what derives from it, at once and while typing.
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("#data-import-page [data-import-derived]").forEach(control =>
    control.addEventListener(control instanceof HTMLSelectElement ? "change" : "input", () => {
      capture();
      refreshDerived(ctx);
      if (control instanceof HTMLSelectElement && document.activeElement !== control) control.focus({ preventScroll: true });
    }));
}

/** Patches the regions derived from the choices (no control the user edits lives in them). */
function refreshDerived(ctx: ViewContext) {
  const regions: [string, () => string][] = [["import-preview-state", previewStatusHtml],
    ["import-register-actions", registerActionsHtml], ["import-split-state", splitStateHtml]];
  for (const [id, html] of regions) if (document.getElementById(id)) ctx.patch(id, html(), { keepTyping: false });
}

// -- steps --
async function pick(ctx: ViewContext) {
  if (ctx.busy || picking) return;
  capture();
  picking = true;
  try {
    const path = await pickFile("import");
    if (!path) return;
    // The file dialog has no parent window, so the page stayed usable: what was typed meanwhile is newer than the draft.
    capture();
    draft.path = path;
    const format = guessFormat(path);
    if (format) setFormat(format);
    draft.sourceIssues = [];
    // The render may wait for an IME composition and read the page back first: the page shows the pick already.
    const input = document.querySelector<HTMLInputElement>("#import-path"), select = document.querySelector<HTMLSelectElement>("#import-format");
    if (input) input.value = path;
    if (select) select.value = draft.format;
    ctx.render();
    document.querySelector<HTMLElement>('#data-import-page [data-focus-key="import-source"]')?.focus();
  } finally { picking = false; }
}

/** A new source resets everything derived from the previous one; the same source keeps the mapping and registration. */
function adoptSource(path: string, source: ImportSource) {
  if (draft.source?.source_id === source.source_id) {
    Object.assign(draft, { source, sourcePath: path, sourceIssues: [], previewChanged: false });
    return;
  }
  const format = guessFormat(path);
  if (format) setFormat(format);
  Object.assign(draft, { source, sourcePath: path, sourceIssues: [], columns: {}, sourceFields: [], sourceSamples: [],
    preview: null, previewIssues: [], previewStatus: null, previewSent: null, previewChanged: false, registerIssues: [],
    registered: null, registeredFields: [], registeredKey: null, registeredFlags: null, split: emptySplit() });
  if (draft.datasetIdAuto || !draft.datasetId) { draft.datasetId = idFromPath(path); draft.datasetIdAuto = true; }
}

/** Previews the current mapping. With no mapped column yet it discovers the columns and maps same-named ones. */
async function runPreview() {
  const source = draft.source;
  if (!source) return;
  const mapping = mappingFrom(draft);
  const result = await imports.preview(source.source_id, mapping);
  if (notAvailable(result)) unavailable = true;
  draft.previewSent = mapping; draft.previewChanged = false; draft.previewStatus = result.status;
  draft.previewIssues = result.issues; draft.preview = result.data ?? null;
  if (!result.data) return;
  draft.sourceFields = Array.isArray(result.data.source_fields) ? result.data.source_fields.map(String) : [];
  draft.sourceSamples = Array.isArray(result.data.source_samples) ? result.data.source_samples.slice(0, 5) : [];
  if (!Object.keys(mapping.fields).length) {
    const { required, optional } = CANONICAL_FIELDS[draft.kind];
    for (const name of [...required, ...optional]) if (!draft.columns[name] && draft.sourceFields.includes(name)) draft.columns[name] = name;
  }
}

async function importSource(ctx: ViewContext) {
  if (ctx.busy) return;
  capture();
  const path = draft.path.trim();
  if (!path) {
    draft.sourceIssues = [clientIssue("/path", "파일 경로를 입력하거나 ‘파일 찾기’를 누르세요.")];
    ctx.render(); focusById("import-path"); return;
  }
  let focus: string | null = null;
  const where = here(ctx);
  await ctx.task("원본 파일 가져오는 중", async () => {
    const result = await imports.source(path);
    if (result.status !== "ok" || !result.data) {
      if (notAvailable(result)) unavailable = true;
      draft.sourceIssues = result.issues; focus = "import-path"; return;
    }
    adoptSource(path, result.data);
    labelIssues = [];
    focus = "import-format";
    await runPreview();
    const first = CANONICAL_FIELDS[draft.kind].required[0];
    if (draft.sourceFields.length && first) focus = `import-col-${first}`;
  });
  if (focus && where.still()) focusById(focus);
}

/** Why the KTO label sets cannot be sent (the server's rule: both sides or neither, each value once, no value on both
 * sides), with the field to fix; null when they can. */
function labelRefusal(): { message: string; focus: string } | null {
  const yes = labels(draft.trueLabels), no = labels(draft.falseLabels);
  if (Boolean(yes.length) !== Boolean(no.length))
    return { message: "좋은 응답과 나쁜 응답으로 볼 값을 모두 입력하거나 둘 다 비워 두세요.", focus: yes.length ? "import-false-labels" : "import-true-labels" };
  for (const [values, focus] of [[yes, "import-true-labels"], [no, "import-false-labels"]] as const) {
    const twice = values.find((value, index) => values.indexOf(value) !== index);
    if (twice !== undefined) return { message: `같은 값을 두 번 입력했습니다: ${twice}`, focus };
  }
  const both = yes.filter(value => no.includes(value));
  return both.length ? { message: `좋은 응답과 나쁜 응답에 같은 값이 있습니다: ${both.join(", ")}`, focus: "import-false-labels" } : null;
}

async function preview(ctx: ViewContext) {
  if (ctx.busy || !draft.source) return;
  capture();
  const refused = draft.kind === "kto" ? labelRefusal() : null;
  if (refused) {
    labelIssues = [clientIssue("/mapping/boolean_labels", refused.message)];
    ctx.render(); focusById(refused.focus); return;
  }
  labelIssues = [];
  await ctx.task("미리보는 중", runPreview);
}

async function register(ctx: ViewContext) {
  if (ctx.busy) return;
  capture();
  // The button is disabled with its reason shown while the preview cannot be registered; a programmatic submit stops here.
  if (registerBlock()) { refreshDerived(ctx); return; }
  const local: [WorkflowIssue, string][] = [];
  if (!LOCAL_ID.test(draft.datasetId)) local.push([clientIssue("/dataset_id", `데이터 ID는 ${ID_RULE}`), "import-dataset-id"]);
  if (!LOCAL_ID.test(draft.version)) local.push([clientIssue("/version", `버전은 ${ID_RULE}`), "import-version"]);
  if (local.length) {
    draft.registerIssues = local.map(([issue]) => issue);
    ctx.render(); focusById(local[0][1]); return;
  }
  const source = draft.source!, sent = draft.previewSent!, shown = draft.preview!;
  const request = { source_id: source.source_id, mapping: sent, preview_sha256: shown.preview_sha256, dataset_id: draft.datasetId,
    version: draft.version, synthetic: draft.synthetic, export_allowed: draft.exportAllowed };
  let focus: string | null = null;
  const where = here(ctx);
  await ctx.task("데이터 버전 등록 중", async () => {
    const result = await imports.register(request);
    if (result.status === "ok" && result.data) {
      const dataset = { ...result.data.dataset };
      // The same version answered again (registration is idempotent) keeps its split, committed or not.
      if (!draft.registered || stable(draft.registered) !== stable(dataset)) {
        draft.registeredFields = [...draft.sourceFields];
        draft.split = emptySplit(dataset.dataset_id);
      }
      draft.registered = dataset; draft.registerIssues = [];
      draft.registeredKey = stable({ preview_sha256: request.preview_sha256, dataset_id: request.dataset_id, version: request.version });
      draft.registeredFlags = { synthetic: request.synthetic, export_allowed: request.export_allowed };
      focus = "import-registered";
      ctx.notify("데이터 버전을 등록했습니다.");
      await ctx.reloadCatalog();
      return;
    }
    if (notAvailable(result)) unavailable = true;
    // A changed preview is said once, by the preview's state line and the register button's reason.
    if (result.issues.some(issue => issue.code === "preview_changed")) draft.previewChanged = true;
    draft.registerIssues = result.issues.filter(issue => issue.code !== "preview_changed");
    focus = issuesAt(result.issues, "/version").length ? "import-version" : issuesAt(result.issues, "/dataset_id").length ? "import-dataset-id" : null;
  });
  if (focus && where.still()) focusById(focus);
}

async function previewSplit(ctx: ViewContext) {
  if (ctx.busy || !draft.registered) return;
  capture();
  const built = buildPlan();
  if ("issue" in built) {
    draft.split.issues = [built.issue];
    ctx.render(); focusById(built.focus); return;
  }
  const where = here(ctx);
  await ctx.task("분할 미리보는 중", async () => {
    const result = await imports.splitPreview(built.plan);
    if (notAvailable(result)) unavailable = true;
    const split = draft.split;
    split.sent = built.plan; split.issues = result.issues;
    split.preview = result.status === "ok" && result.data ? result.data : null;
  });
  focusSplitIssue(where);
}

/** After a refused split step, the first field an issue names takes the focus (as the register step does). */
function focusSplitIssue(where: { still(): boolean }) {
  const field = draft.split.issues.map(splitFieldFor).find(Boolean);
  if (field && where.still()) focusById(field);
}

async function commitSplit(ctx: ViewContext) {
  const split = draft.split;
  if (ctx.busy || !split.preview) return;
  capture();
  if (!splitFresh()) { split.issues = [clientIssue("/plan_sha256", SPLIT_STALE)]; ctx.render(); return; }
  const planSha = split.preview.plan_sha256;
  let focus: string | null = null;
  const where = here(ctx);
  await ctx.task("분할 등록 중", async () => {
    const result = await imports.splitCommit(planSha);
    if (result.status === "ok" && result.data) {
      split.committed = { plan_sha256: result.data.plan_sha256, outputs: result.data.outputs }; split.issues = [];
      focus = "import-split-committed";
      ctx.notify("분할한 데이터 버전을 등록했습니다.");
      await ctx.reloadCatalog();
      return;
    }
    if (notAvailable(result)) unavailable = true;
    split.issues = result.issues;
    if (result.issues.some(issue => issue.code === "preview_changed" || issue.code === "preview_unknown")) { split.preview = null; split.sent = null; }
  });
  if (focus && where.still()) focusById(focus);
  else focusSplitIssue(where);
}

/** Stages the registered or split versions as bindings of the project draft; the project page saves them (D4). */
async function stage(ctx: ViewContext) {
  const projectId = ctx.arg;
  const refs = pendingRefs();
  if (ctx.busy || !projectId || !refs.length) return;
  let staged: string | null = null;
  const where = here(ctx);
  await ctx.task("프로젝트에 연결하는 중", async () => {
    link = { projectId, issues: [], note: null };
    // The latest saved revision first, so the staged bindings land on it; a dirty draft is kept as it is (null).
    const loaded = await ctx.store.open(projectId);
    if (loaded && loaded.status !== "ok") {
      if (notAvailable(loaded)) unavailable = true;
      link.issues = loaded.issues.length ? loaded.issues : [clientIssue("", "프로젝트를 불러오지 못했습니다.")]; return;
    }
    const project = ctx.store.draft(projectId);
    if (!project) { link.issues = [clientIssue("", "프로젝트를 불러오지 못했습니다.")]; return; }
    const bound = (dataset: DatasetRef) => project.spec.datasets.some(binding =>
      binding.dataset.dataset_id === dataset.dataset_id && binding.dataset.version === dataset.version);
    const added: DatasetBinding[] = [];
    for (const item of refs.filter(item => !bound(item.dataset)))
      added.push({ binding_id: bindingIdFor(item.role, [...project.spec.datasets, ...added]), role: item.role, dataset: { ...item.dataset } });
    if (added.length) ctx.store.edit(projectId, spec => { spec.datasets = [...spec.datasets, ...added]; });
    if (added.length < refs.length) link.note = ALREADY_BOUND;
    staged = added.length > 0 ? "import-staged" : "import-link-note";
  });
  // The new bindings, or (when every version was bound already, even saved) the note saying so.
  if (staged && where.still()) focusById(staged);
}

export const view: View = {
  id: "data-import", parent: "datasets", title: "데이터 가져오기",
  description: "CSV·JSON·JSONL의 열을 학습 형식에 연결하고, 미리본 뒤 바뀌지 않는 버전으로 등록하고 나눕니다.",
  render,
  bind,
  capture,
  async enter(ctx) { if (link.projectId !== ctx.arg) link = { projectId: ctx.arg, issues: [], note: null }; },
  async action(ctx, verb) {
    if (verb === "pick") await pick(ctx);
    else if (verb === "preview") await preview(ctx);
    else if (verb === "split-commit") await commitSplit(ctx);
    else if (verb === "link") await stage(ctx);
  },
  datasetsPanel() {
    return `<section class="card import-slot" aria-labelledby="import-slot-title"><div class="section-title"><h3 id="import-slot-title">열 연결해 가져오기 · CSV·JSON·JSONL</h3></div>
      <p>Windows에서 만든 CSV나 열 이름이 다른 JSON·JSONL의 열을 학습 형식에 연결하고, 미리본 뒤 바뀌지 않는 버전으로 등록합니다. 학습·검증·시험 분할도 여기서 만듭니다.</p>
      <div class="actions">${button("열 연결해 가져오기", "nav:data-import")}</div></section>`;
  },
};
