import { test, expect, type Page } from "@playwright/test";
import { calls, conflict, HOSTILE, invalid, issue, ok, openWorkbench } from "./workflow-mock.ts";

const SOURCE = "a".repeat(64);
const DATASET_SHA = "d".repeat(64);
const PLAN = "e".repeat(64);
const PRACTICE_REFUSAL = "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다.";
const ENCODING_HINT = "문자 인코딩이 맞지 않습니다. Windows에서 만든 CSV라면 CP949로 바꿔 다시 미리보세요.";
const QA_MAPPING = { instruction: ["question"], input: ["context"], output: ["answer"] };

const heading = (page: Page) => page.getByRole("heading", { level: 1 });
const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const sourceCard = (page: Page) => page.locator('#app [data-tour="data-import"]');
const registerButton = (page: Page) => page.locator("#import-register-form").getByRole("button", { name: "등록", exact: true });
const previewButton = (page: Page) => page.getByRole("button", { name: "미리보기", exact: true });
const workflowCalls = async (page: Page, action: string) => (await calls(page, { action })).map(call => call.args.data);

function mapping(over: Record<string, unknown> = {}) {
  return { schema_version: 1, transform_version: "text-mapping-v1", source_format: "csv", encoding: "utf-8", csv_delimiter: ",",
    kind: "instruction", formatting: "alpaca", fields: {}, message_tags: {}, boolean_labels: {}, ...over };
}
const QA_ROWS = [1, 2, 3, 4, 5].map(n => ({ question: n === 1 ? `${HOSTILE} 질문 ${n}` : `질문 ${n}`, context: `맥락 ${n}`, answer: `답 ${n}` }));
function previewData(over: Record<string, unknown> = {}) {
  return { source_id: SOURCE, source_sha256: SOURCE, source_size: 2048, canonical_sha256: null, canonical_size: null, row_count: 10,
    samples: [], issues: [], source_samples: QA_ROWS, source_fields: ["answer", "context", "question"], mapping: mapping(),
    preview_sha256: "1".repeat(64), ...over };
}
/** The first preview maps no column: every row fails, but the source columns and rows arrive (F20). */
function discovery(over: Record<string, unknown> = {}) {
  const rows = Array.from({ length: 10 }, (_, index) => issue("invalid_row", "'instruction'", "/mapping", { row_number: index + 1 }));
  return invalid(rows, previewData({ issues: rows.map((_, index) => ({ ordinal: index, code: "invalid_row", message: "'instruction'" })), ...over }));
}
function mappedPreview(sha: string, over: Record<string, unknown> = {}) {
  return ok(previewData({ canonical_sha256: "c".repeat(64), canonical_size: 900, preview_sha256: sha,
    samples: QA_ROWS.map(row => ({ instruction: row.question, input: row.context, output: row.answer })),
    mapping: mapping({ fields: QA_MAPPING }), ...over }));
}
const sourceOk = ok({ source_id: SOURCE, source_sha256: SOURCE, size: 2048 });
const registered = (datasetId = "qa", version = "1", sha = DATASET_SHA) =>
  ok({ dataset: { dataset_id: datasetId, version, sha256: sha }, preview_sha256: "2".repeat(64) });
const splitOutputs = (id = "qa") => ({ train: { dataset_id: `${id}-train`, version: "1", sha256: "7".repeat(64) },
  validation: { dataset_id: `${id}-validation`, version: "1", sha256: "8".repeat(64) },
  test: { dataset_id: `${id}-test`, version: "1", sha256: "9".repeat(64) } });

/** From the 데이터 page's slot panel to the import page. */
async function openImport(page: Page) {
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.getByRole("button", { name: "열 연결해 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
}
/** Types a path and imports it (the source snapshot, then the column discovery preview). */
async function importFile(page: Page, path = "C:\\data\\qa.csv") {
  await sourceCard(page).getByLabel("파일 경로", { exact: true }).fill(path);
  await sourceCard(page).getByRole("button", { name: "가져오기", exact: true }).click();
  await idle(page);
}
async function mapColumns(page: Page, columns: Record<string, string>) {
  for (const [label, column] of Object.entries(columns))
    await page.locator("#import-mapping").getByLabel(label, { exact: true }).selectOption(column);
}
async function preview(page: Page) {
  await previewButton(page).click();
  await idle(page);
}
/** Import, map question/context/answer and preview until registration is possible. */
async function readyToRegister(page: Page) {
  await openImport(page);
  await importFile(page);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  await expect(registerButton(page)).toBeEnabled();
}
async function register(page: Page, datasetId = "qa", version = "1") {
  const form = page.locator("#import-register-form");
  await form.getByLabel("데이터 ID", { exact: true }).fill(datasetId);
  await form.getByLabel("버전", { exact: true }).fill(version);
  await registerButton(page).click();
  await idle(page);
}

test("CSV question, context and answer map to instruction, input and output", async ({ page }) => {
  await openWorkbench(page, { native: { pick_file: "C:\\data\\qa.csv" },
    workflow: { dataset_import_source: sourceOk, dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))] } });
  await openImport(page);
  const card = sourceCard(page);
  await expect(card).toHaveCount(1);
  await card.getByRole("button", { name: "파일 찾기" }).click();
  await expect(card.getByLabel("파일 경로", { exact: true })).toHaveValue("C:\\data\\qa.csv");
  expect((await calls(page, { command: "pick_file" })).map(call => call.args)).toEqual([{ kind: "import" }]);
  await card.getByRole("button", { name: "가져오기", exact: true }).click();
  await idle(page);
  expect(await workflowCalls(page, "dataset_import_source")).toEqual([{ path: "C:\\data\\qa.csv" }]);
  // Column discovery: the first preview maps no column (its failing rows are not shown as problems).
  expect(await workflowCalls(page, "dataset_import_preview")).toEqual([{ source_id: SOURCE, mapping: mapping() }]);
  await expect(page.getByLabel("원본 형식", { exact: true })).toHaveValue("csv");
  await expect(page.locator("#import-preview .import-issues li")).toHaveCount(0);
  const state = page.locator("#import-preview-state");
  await expect(state).toContainText("열 확인");
  await expect(state).toContainText("열을 연결한 뒤 미리보기를 누르세요.");
  await expect(page.locator("#import-mapping")).toContainText("열 3개");
  // The source rows are shown escaped beside the column choices.
  await expect(page.locator("#import-mapping table.import-samples tbody tr")).toHaveCount(5);
  await expect(page.locator("#import-mapping table.import-samples")).toContainText(`${HOSTILE} 질문 1`);
  await expect(registerButton(page)).toBeDisabled();

  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  // Mapping columns after the discovery is the expected next step, not a change that makes a preview stale.
  await expect(state).toContainText("열 확인");
  await expect(state).not.toContainText("다시 미리보아야");
  await preview(page);
  const previews = await workflowCalls(page, "dataset_import_preview");
  expect(previews).toHaveLength(2);
  expect(previews[1]).toEqual({ source_id: SOURCE, mapping: mapping({ fields: QA_MAPPING }) });
  expect(previews[1].mapping).toMatchObject({ source_format: "csv", encoding: "utf-8", kind: "instruction" });
  // The five canonical samples, escaped.
  const samples = page.locator("#import-preview table.import-samples");
  await expect(samples.locator("tbody tr")).toHaveCount(5);
  await expect(samples.locator("thead")).toContainText("지시문");
  await expect(samples.locator("tbody tr").first()).toContainText(`${HOSTILE} 질문 1`);
  await expect(samples.locator("tbody tr").first()).toContainText("맥락 1");
  await expect(page.locator("#import-preview")).toContainText("행 10개");
  await expect(registerButton(page)).toBeEnabled();
  await expect(page.locator("img")).toHaveCount(0);
});

test("a wrong encoding is re-selected and previewed again", async ({ page }) => {
  const codec = "'utf-8' codec can't decode byte 0xb1 in position 12: invalid start byte";
  const unreadable = invalid([issue("invalid_source", codec, "/mapping")], previewData({ row_count: 0, source_samples: [],
    source_fields: [], issues: [{ ordinal: null, code: "invalid_source", message: codec }] }));
  const cp949 = (over: Record<string, unknown> = {}) => mapping({ encoding: "cp949", ...over });
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk, dataset_import_preview: [unreadable,
    discovery({ mapping: cp949() }), mappedPreview("3".repeat(64), { mapping: cp949({ fields: QA_MAPPING }) })] } });
  await openImport(page);
  await importFile(page);
  const result = page.locator("#import-preview");
  await expect(result).toContainText(ENCODING_HINT);
  await result.getByText("원문 보기").click();
  await expect(result).toContainText(codec);
  await expect(registerButton(page)).toBeDisabled();
  // CP949 is offered for CSV only.
  const encoding = page.getByLabel("문자 인코딩", { exact: true });
  await page.getByLabel("원본 형식", { exact: true }).selectOption("json");
  await expect(encoding.locator('option[value="cp949"]')).toHaveCount(0);
  await page.getByLabel("원본 형식", { exact: true }).selectOption("csv");
  await expect(page.getByLabel("원본 형식", { exact: true })).toBeFocused();
  await encoding.selectOption("cp949");
  await expect(encoding).toBeFocused();
  await expect(page.locator("#import-preview")).toContainText("형식이 바뀌어 다시 미리보아야 합니다");
  await preview(page);
  const previews = await workflowCalls(page, "dataset_import_preview");
  expect(previews).toHaveLength(2);
  expect(previews[1]).toEqual({ source_id: SOURCE, mapping: cp949() });
  await expect(result).not.toContainText(ENCODING_HINT);
  await expect(registerButton(page)).toBeDisabled();
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await expect(registerButton(page)).toBeDisabled();
  await preview(page);
  expect((await workflowCalls(page, "dataset_import_preview"))[2]).toEqual({ source_id: SOURCE, mapping: cp949({ fields: QA_MAPPING }) });
  await expect(registerButton(page)).toBeEnabled();
});

test("row errors show their source row numbers and block registration", async ({ page }) => {
  const hostileColumn = `${HOSTILE}열`;
  const rows = invalid([issue("invalid_row", "'answer'", "/mapping", { row_number: 3 }),
    issue("duplicate_header", "duplicate_header: duplicate CSV headers", "/mapping")],
  previewData({ canonical_sha256: null, source_fields: ["answer", "context", hostileColumn, "question"],
    issues: [{ ordinal: 2, code: "invalid_row", message: "'answer'" }, { ordinal: null, code: "duplicate_header", message: "duplicate_header: duplicate CSV headers" }],
    samples: QA_ROWS.slice(0, 2).map(row => ({ instruction: row.question, output: row.answer })), mapping: mapping({ fields: QA_MAPPING }) }));
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery({ source_fields: ["answer", "context", hostileColumn, "question"] }), rows] } });
  await openImport(page);
  await importFile(page);
  // A hostile column name is offered as text.
  await expect(page.locator("#import-mapping").getByLabel("입력", { exact: true }).locator("option", { hasText: hostileColumn })).toHaveCount(1);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  const items = page.locator("#import-preview .import-issues li");
  await expect(items).toHaveCount(2);
  // File-level issues are listed before row issues.
  await expect(items.nth(0).locator(".import-issue-where")).toHaveText("헤더");
  await expect(items.nth(0)).toContainText("CSV 헤더에 같은 이름의 열이 있습니다.");
  await expect(items.nth(1).locator(".import-issue-where")).toHaveText("3행");
  await expect(items.nth(1)).toContainText("이 행을 연결한 열로 학습 형식에 맞게 바꿀 수 없습니다.");
  await items.nth(1).getByText("원문 보기").click();
  await expect(items.nth(1)).toContainText("'answer'");
  await expect(page.locator("#import-preview")).toContainText("문제 2건");
  await expect(registerButton(page)).toBeDisabled();
  await page.locator("#import-register-form").getByLabel("데이터 ID", { exact: true }).fill("qa");
  await page.locator("#import-register-form").evaluate(form => (form as HTMLFormElement).requestSubmit());
  await idle(page);
  expect(await calls(page, { action: "dataset_import_register" })).toEqual([]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("changing the mapping invalidates the shown preview", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64)),
      mappedPreview("3".repeat(64), { mapping: mapping({ fields: { instruction: ["question"], input: ["answer"], output: ["answer"] } }) })],
    dataset_import_register: registered() } });
  await readyToRegister(page);
  const note = "열 연결이 바뀌어 다시 미리보아야 합니다";
  await expect(page.locator("#import-preview")).not.toContainText(note);
  await mapColumns(page, { 입력: "answer" });
  await expect(page.locator("#import-mapping").getByLabel("입력", { exact: true })).toBeFocused();
  await expect(page.locator("#import-preview")).toContainText(note);
  await expect(registerButton(page)).toBeDisabled();
  // Choosing the previewed column again makes the shown preview current again.
  await mapColumns(page, { 입력: "context" });
  await expect(page.locator("#import-preview")).not.toContainText(note);
  await expect(registerButton(page)).toBeEnabled();
  // The data kind is part of the mapping too.
  await page.getByLabel("데이터 종류", { exact: true }).selectOption("prompt");
  await expect(page.locator("#import-preview")).toContainText(note);
  await page.getByLabel("데이터 종류", { exact: true }).selectOption("instruction");
  await mapColumns(page, { 입력: "answer" });
  await expect(registerButton(page)).toBeDisabled();
  // A register attempt with a stale preview is refused before any call.
  await page.locator("#import-register-form").evaluate(form => (form as HTMLFormElement).requestSubmit());
  expect(await calls(page, { action: "dataset_import_register" })).toEqual([]);
  await preview(page);
  await expect(page.locator("#import-preview")).not.toContainText(note);
  await register(page);
  const sent = await workflowCalls(page, "dataset_import_register");
  expect(sent).toEqual([{ source_id: SOURCE, preview_sha256: "3".repeat(64), dataset_id: "qa", version: "1", synthetic: false,
    export_allowed: false, mapping: mapping({ fields: { instruction: ["question"], input: ["answer"], output: ["answer"] } }) }]);
});

test("registration keeps versions immutable and refuses the practice ID", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))],
    dataset_import_register: [conflict([issue("version_exists", "immutable version already exists: qa 1", "/version")]),
      invalid([issue("practice_reserved", PRACTICE_REFUSAL, "/dataset_id")]), registered("qa", "2")] } });
  await readyToRegister(page);
  const form = page.locator("#import-register-form");
  const catalogsBefore = (await calls(page, { action: "catalog" })).length;
  await register(page, "qa", "1");
  await expect(form).toContainText("이미 등록된 버전입니다. 다른 버전을 입력하세요.");
  await expect(form.getByLabel("버전", { exact: true })).toBeFocused();
  await expect(form.getByLabel("데이터 ID", { exact: true })).toHaveValue("qa");
  await register(page, "tutorial_sample", "2");
  await expect(form).toContainText(PRACTICE_REFUSAL);
  await expect(form.getByLabel("데이터 ID", { exact: true })).toBeFocused();
  await expect(form).not.toContainText("이미 등록된 버전입니다.");
  await form.getByLabel("더미·합성 데이터입니다").check();
  await register(page, "qa", "2");
  const sent = await workflowCalls(page, "dataset_import_register");
  expect(sent.map(request => [request.dataset_id, request.version, request.synthetic, request.export_allowed])).toEqual([
    ["qa", "1", false, false], ["tutorial_sample", "2", false, false], ["qa", "2", true, false]]);
  for (const request of sent) {
    expect(typeof request.synthetic).toBe("boolean");
    expect(typeof request.export_allowed).toBe("boolean");
    expect(request).toMatchObject({ source_id: SOURCE, preview_sha256: "2".repeat(64), mapping: mapping({ fields: QA_MAPPING }) });
  }
  await expect(page.locator("#import-registered")).toContainText(`qa · v2 · ${DATASET_SHA.slice(0, 8)}`);
  await expect(page.locator("#import-registered")).toBeFocused();
  // The legacy pages list the new version.
  expect((await calls(page, { action: "catalog" })).length).toBe(catalogsBefore + 1);
});

test("split preview shows counts per role before commit", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered(),
    dataset_split_preview: [ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
      ok({ plan_sha256: "f".repeat(64), counts: { train: 9, validation: 1 }, outputs: { train: splitOutputs().train, validation: splitOutputs().validation } })],
    dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  await readyToRegister(page);
  await expect(page.locator("#import-split")).toHaveCount(0);
  await register(page);
  const split = page.locator("#import-split");
  await expect(split.getByLabel("시드", { exact: true })).toHaveValue("42");
  await expect(split.getByLabel("학습 데이터 ID", { exact: true })).toHaveValue("qa-train");
  await split.getByLabel("묶음 열", { exact: true }).selectOption("question");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  expect(await workflowCalls(page, "dataset_split_preview")).toEqual([{ plan: { dataset: { dataset_id: "qa", version: "1", sha256: DATASET_SHA },
    seed: 42, train_basis_points: 8000, validation_basis_points: 1000, test_basis_points: 1000, group_field: ["question"],
    outputs: { train: { dataset_id: "qa-train", version: "1" }, validation: { dataset_id: "qa-validation", version: "1" },
      test: { dataset_id: "qa-test", version: "1" } } } }]);
  await expect(split).toContainText("학습 8 · 검증 1 · 시험 1");
  const catalogsBefore = (await calls(page, { action: "catalog" })).length;
  await split.getByRole("button", { name: "분할 등록", exact: true }).click();
  await idle(page);
  expect(await workflowCalls(page, "dataset_split_commit")).toEqual([{ plan_sha256: PLAN }]);
  await expect(page.locator("#import-split-committed")).toContainText(`qa-validation · v1 · ${"8".repeat(8)}`);
  expect((await calls(page, { action: "catalog" })).length).toBe(catalogsBefore + 1);

  // Percentages must be whole numbers summing to 100: refused on the client, no call.
  await split.getByLabel("학습 비율(%)", { exact: true }).fill("70");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await expect(split).toContainText("합계가 100%가 되어야 합니다.");
  await expect(split.getByLabel("학습 비율(%)", { exact: true })).toBeFocused();
  expect(await calls(page, { action: "dataset_split_preview" })).toHaveLength(1);
  // A role at 0% gets no output.
  await split.getByLabel("학습 비율(%)", { exact: true }).fill("90");
  await split.getByLabel("시험 비율(%)", { exact: true }).fill("0");
  await split.getByLabel("묶음 열", { exact: true }).selectOption("");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  const second = (await workflowCalls(page, "dataset_split_preview"))[1].plan;
  expect(second).toMatchObject({ train_basis_points: 9000, validation_basis_points: 1000, test_basis_points: 0, group_field: null });
  expect(Object.keys(second.outputs)).toEqual(["train", "validation"]);
  await expect(split).toContainText("학습 9 · 검증 1 · 시험 0");
  // A changed plan needs a new preview before it can be committed.
  await split.getByLabel("시드", { exact: true }).fill("7");
  await split.getByLabel("시드", { exact: true }).press("Tab");
  await expect(split.getByRole("button", { name: "분할 등록", exact: true })).toBeDisabled();
  await expect(split).toContainText("분할 조건이 바뀌어 다시 미리보아야 합니다");
});

test("registered data is staged for the project that opened the import", async ({ page }) => {
  await openWorkbench(page, { definitions: true, workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered(),
    dataset_split_preview: ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
    dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  // The project was created elsewhere (another window), so this window has no draft of it yet.
  const created = await page.evaluate(name => (window as any).__TAURI_INTERNALS__.invoke("workflow_action",
    { action: "project_create", data: { request_id: "req-1", name, purpose: "", task_type: "" } }), `${HOSTILE} 서열 요약`);
  const projectId = created.data.project.project_id;
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  await page.locator(".project-list tbody tr").getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
  await expect(page.locator('nav [data-action="nav:datasets"]')).toHaveClass("selected");
  await expect(page.locator("#data-import-page .wf-banner")).toContainText(`${HOSTILE} 서열 요약`);
  await expect(page.locator("#import-link")).toContainText("등록하거나 분할을 등록하면 연결할 수 있습니다.");
  await importFile(page);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  await register(page);
  await expect(page.locator("#import-link")).toContainText("학습 · qa · v1");
  const split = page.locator("#import-split");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  await split.getByRole("button", { name: "분할 등록", exact: true }).click();
  await idle(page);
  const readsBefore = (await calls(page, { action: "project_get" })).length;
  const link = page.locator("#import-link");
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  // The latest saved revision is read before staging (a dirty draft would be kept as it is).
  expect((await workflowCalls(page, "project_get")).slice(readsBefore)).toEqual([{ project_id: projectId }]);
  await expect(link.locator("#import-staged")).toBeFocused();
  await expect(link).toContainText("저장 전 연결: train, validation, test");
  for (const [role, output] of Object.entries(splitOutputs()))
    await expect(link.locator(`tr[data-binding="${role}"]`)).toContainText(`${output.dataset_id} · v1 · ${output.sha256.slice(0, 8)}`);
  expect(await calls(page, { action: "project_save" })).toEqual([]);
  // A second click stages nothing twice.
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  await expect(link).toContainText("저장 전 연결: train, validation, test");
  await expect(link).toContainText("이미 연결된 데이터 버전은 다시 추가하지 않았습니다.");
  // The project page shows the staged bindings as an unsaved change; saving stays the user's choice.
  await link.getByRole("button", { name: "프로젝트에서 저장하기", exact: true }).click();
  await idle(page);
  await expect(heading(page)).toHaveText(`${HOSTILE} 서열 요약`);
  await expect(page.locator("#project-form .status-pill")).toHaveText("저장 안 된 변경");
  await expect(page.locator(".binding-table tbody tr")).toHaveCount(3);
  await expect(page.locator(".binding-table")).toContainText("qa-validation · v1");
  expect(await calls(page, { action: "project_save" })).toEqual([]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("a registered version without a split is staged as training data", async ({ page }) => {
  await openWorkbench(page, { definitions: true, workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered() } });
  await page.locator('#app [data-tour="project-start"]').getByLabel("프로젝트 이름").fill("학습만 연결");
  await page.locator('#app [data-tour="project-start"]').getByRole("button", { name: "만들기", exact: true }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await readyToRegisterFromHere(page);
  await register(page);
  const link = page.locator("#import-link");
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  await expect(link).toContainText("저장 전 연결: train");
  await expect(link.locator('tr[data-binding="train"]')).toContainText(`qa · v1 · ${DATASET_SHA.slice(0, 8)}`);
  expect(await calls(page, { action: "project_save" })).toEqual([]);
});
async function readyToRegisterFromHere(page: Page) {
  await expect(heading(page)).toHaveText("데이터 가져오기");
  await importFile(page);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  await expect(registerButton(page)).toBeEnabled();
}

test("import steps are not submitted twice while busy", async ({ page }) => {
  await openWorkbench(page, { holds: ["dataset_import_preview", "dataset_import_register", "dataset_split_preview", "dataset_split_commit"],
    workflow: { dataset_import_source: sourceOk, dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))],
      dataset_import_register: registered(),
      dataset_split_preview: ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
      dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  await openImport(page);
  const held = (name: string) => page.evaluate(name => (window as any).__held(name), name);
  const release = (name: string) => page.evaluate(name => (window as any).__release(name), name);
  const card = sourceCard(page);
  await card.getByLabel("파일 경로", { exact: true }).fill("C:\\data\\qa.csv");
  const importButton = card.getByRole("button", { name: "가져오기", exact: true });
  await importButton.dblclick();
  await expect.poll(() => held("dataset_import_preview")).toBe(1);
  await expect(importButton).toBeDisabled();
  await expect(card.getByRole("button", { name: "파일 찾기" })).toBeDisabled();
  await page.locator("#import-source-form").evaluate(form => (form as HTMLFormElement).requestSubmit());
  expect(await calls(page, { action: "dataset_import_source" })).toHaveLength(1);
  expect(await held("dataset_import_preview")).toBe(1);
  await release("dataset_import_preview");
  await idle(page);
  expect(await calls(page, { action: "dataset_import_preview" })).toHaveLength(1);

  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  expect(await calls(page, { action: "dataset_import_preview" })).toHaveLength(2);

  const form = page.locator("#import-register-form");
  await form.getByLabel("데이터 ID", { exact: true }).fill("qa");
  await registerButton(page).dblclick();
  await expect.poll(() => held("dataset_import_register")).toBe(1);
  await expect(registerButton(page)).toBeDisabled();
  await form.evaluate(element => (element as HTMLFormElement).requestSubmit());
  expect(await calls(page, { action: "dataset_import_register" })).toHaveLength(1);
  await release("dataset_import_register");
  await idle(page);
  expect(await calls(page, { action: "dataset_import_register" })).toHaveLength(1);

  const split = page.locator("#import-split");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).dblclick();
  await expect.poll(() => held("dataset_split_preview")).toBe(1);
  await page.locator("#import-split-form").evaluate(element => (element as HTMLFormElement).requestSubmit());
  await release("dataset_split_preview");
  await idle(page);
  expect(await calls(page, { action: "dataset_split_preview" })).toHaveLength(1);
  await split.getByRole("button", { name: "분할 등록", exact: true }).dblclick();
  await expect.poll(() => held("dataset_split_commit")).toBe(1);
  await expect(split.getByRole("button", { name: "분할 등록", exact: true })).toBeDisabled();
  await release("dataset_split_commit");
  await idle(page);
  expect(await calls(page, { action: "dataset_split_commit" })).toHaveLength(1);
});

test("the datasets page links to the mapped import without changing the legacy form", async ({ page }) => {
  await openWorkbench(page);
  await page.locator('nav [data-action="nav:datasets"]').click();
  const slot = page.locator("section.card:has(#dataset-form) ~ .import-slot");
  await expect(slot).toHaveCount(1);
  await expect(slot.getByRole("heading")).toHaveText("열 연결해 가져오기 · CSV·JSON·JSONL");
  await expect(page.locator("#dataset-form")).toHaveCount(1);
  await expect(page.locator('#app [data-tour="dataset-form"]')).toHaveCount(1);
  await expect(page.locator('#app [data-tour="dataset-submit"]')).toHaveCount(1);
  for (const name of ["파일 찾기", "샘플 미리보기", "검사하고 등록"])
    await expect(page.getByRole("button", { name, exact: true })).toHaveCount(1);
  await expect(page.locator('#app [data-tour="data-import"]')).toHaveCount(0);
  // The slot runs no task beside the legacy forms (which are never locked): it holds one navigation button and no form
  // control, so nothing it does can re-render over typing in #dataset-form or #s3-form. Typing survives the round trip.
  await expect(slot.locator("input, select, textarea, form")).toHaveCount(0);
  expect(await slot.locator("button").evaluateAll(buttons => buttons.map(button => button.getAttribute("data-action")))).toEqual(["nav:data-import"]);
  const legacy = page.locator("#dataset-form");
  await legacy.getByLabel("데이터 ID", { exact: true }).fill(`입력 중 ${HOSTILE}`);
  await legacy.getByLabel("파일 경로", { exact: true }).fill("C:\\data\\typed.jsonl");
  await page.locator("#s3-form").getByLabel("S3 버킷", { exact: true }).fill("typed-bucket");
  await slot.getByRole("button", { name: "열 연결해 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
  await page.locator('nav [data-action="nav:datasets"]').click();
  await expect(legacy.getByLabel("데이터 ID", { exact: true })).toHaveValue(`입력 중 ${HOSTILE}`);
  await expect(legacy.getByLabel("파일 경로", { exact: true })).toHaveValue("C:\\data\\typed.jsonl");
  await expect(page.locator("#s3-form").getByLabel("S3 버킷", { exact: true })).toHaveValue("typed-bucket");
  await slot.getByRole("button", { name: "열 연결해 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
  await expect(page.locator(".breadcrumb")).toHaveText("내 워크스페이스 / 데이터 / 데이터 가져오기");
  await expect(page.locator('nav [data-action="nav:datasets"]')).toHaveClass("selected");
  await expect(page.locator('#app [data-tour="data-import"]')).toHaveCount(1);
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("#data-import-page")).toContainText("ShareGPT");
  // Opening the page runs nothing; only explicit steps call the workflow channel.
  expect(await calls(page, { command: "workflow_action" })).toEqual([]);
  expect(await calls(page, { command: "pick_file" })).toEqual([]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("percentages and mappings are built only from the shown choices", async ({ page }) => {
  await openWorkbench(page);
  const result = await page.evaluate(async () => {
    const module = await import("/src/views/data-import.ts" as string);
    const draft = { ...module.emptyDraft(), format: "csv", encoding: "cp949", delimiter: ";", kind: "kto",
      columns: { instruction: "q", output: "a", kto_tag: "label", input: "", system: "s", chosen: "c" },
      trueLabels: " 좋음, 1 ,", falseLabels: "나쁨,0" };
    return {
      fields: module.CANONICAL_FIELDS.kto,
      mapping: module.mappingFrom(draft),
      json: module.mappingFrom({ ...draft, format: "json", kind: "text", columns: { text: "body" } }),
      even: module.basisPoints({ train: 80, validation: 10, test: 10 }),
      zero: module.basisPoints({ train: 100, validation: 0, test: 0 }),
      short: module.basisPoints({ train: 70, validation: 10, test: 10 }),
      fraction: module.basisPoints({ train: 79.5, validation: 10.5, test: 10 }),
      negative: module.basisPoints({ train: 110, validation: -10, test: 0 }),
    };
  });
  expect(result.fields).toEqual({ required: ["instruction", "output", "kto_tag"], optional: ["input"] });
  expect(result.mapping).toEqual(mapping({ encoding: "cp949", csv_delimiter: ";", kind: "kto",
    fields: { instruction: ["q"], output: ["a"], kto_tag: ["label"] }, boolean_labels: { true: ["좋음", "1"], false: ["나쁨", "0"] } }));
  // CP949 and the delimiter apply to CSV only.
  expect(result.json).toEqual(mapping({ source_format: "json", kind: "text", fields: { text: ["body"] } }));
  expect(result.even).toEqual({ train: 8000, validation: 1000, test: 1000 });
  expect(result.zero).toEqual({ train: 10000, validation: 0, test: 0 });
  expect([result.short, result.fraction, result.negative]).toEqual([null, null, null]);
});

test("KTO labels are sent as exact label sets and a one-sided set is refused first", async ({ page }) => {
  const kto = (over: Record<string, unknown> = {}) => mapping({ kind: "kto", ...over });
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery({ source_fields: ["answer", "label", "question"] }),
      ok(previewData({ source_fields: ["answer", "label", "question"], canonical_sha256: "c".repeat(64), preview_sha256: "4".repeat(64),
        samples: [{ instruction: "질문 1", output: "답 1", kto_tag: true }],
        mapping: kto({ fields: { instruction: ["question"], output: ["answer"], kto_tag: ["label"] }, boolean_labels: { true: ["좋음", "1"], false: ["나쁨"] } }) }))] } });
  await openImport(page);
  await page.getByLabel("데이터 종류", { exact: true }).selectOption("kto");
  await expect(page.getByLabel("데이터 종류", { exact: true })).toBeFocused();
  await importFile(page);
  expect((await workflowCalls(page, "dataset_import_preview"))[0].mapping).toEqual(kto());
  await mapColumns(page, { 지시문: "question", 정답: "answer", "선호 라벨": "label" });
  await expect(page.locator("#import-mapping").getByLabel("시스템 프롬프트", { exact: true })).toHaveCount(0);
  await page.getByLabel("좋은 응답으로 볼 값", { exact: true }).fill("좋음, 1");
  await previewButton(page).click();
  await expect(page.locator("#import-mapping")).toContainText("좋은 응답과 나쁜 응답으로 볼 값을 모두 입력하거나 둘 다 비워 두세요.");
  await expect(page.getByLabel("나쁜 응답으로 볼 값", { exact: true })).toBeFocused();
  expect(await calls(page, { action: "dataset_import_preview" })).toHaveLength(1);
  // A value on both sides, or twice on one side, is refused with its reason before any call.
  await page.getByLabel("나쁜 응답으로 볼 값", { exact: true }).fill("나쁨, 좋음");
  await previewButton(page).click();
  await expect(page.locator("#import-mapping")).toContainText("좋은 응답과 나쁜 응답에 같은 값이 있습니다: 좋음");
  await expect(page.getByLabel("나쁜 응답으로 볼 값", { exact: true })).toBeFocused();
  await page.getByLabel("나쁜 응답으로 볼 값", { exact: true }).fill("나쁨, 나쁨");
  await previewButton(page).click();
  await expect(page.locator("#import-mapping")).toContainText("같은 값을 두 번 입력했습니다: 나쁨");
  expect(await calls(page, { action: "dataset_import_preview" })).toHaveLength(1);
  await page.getByLabel("나쁜 응답으로 볼 값", { exact: true }).fill("나쁨");
  await preview(page);
  expect((await workflowCalls(page, "dataset_import_preview"))[1].mapping).toEqual(kto({ fields: { instruction: ["question"],
    output: ["answer"], kto_tag: ["label"] }, boolean_labels: { true: ["좋음", "1"], false: ["나쁨"] } }));
  await expect(page.locator("#import-mapping")).not.toContainText("좋은 응답과 나쁜 응답으로 볼 값을 모두 입력하거나");
  await expect(registerButton(page)).toBeEnabled();
  // Editing a label afterwards makes the preview stale while typing, without leaving the field.
  const falseLabels = page.getByLabel("나쁜 응답으로 볼 값", { exact: true });
  await falseLabels.click();
  await page.keyboard.type(", 0");
  await expect(registerButton(page)).toBeDisabled();
  await expect(page.locator("#import-preview")).toContainText("열 연결이 바뀌어 다시 미리보아야 합니다");
  await expect(falseLabels).toBeFocused();
  await expect(falseLabels).toHaveValue("나쁨, 0");
});

test("leaving the page while a step runs keeps that step's result", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))],
    dataset_import_register: [registered("qa"), registered("qa2", "1", "6".repeat(64))] } });
  await readyToRegister(page);
  await register(page, "qa");
  await expect(page.locator("#import-split").getByLabel("학습 데이터 ID", { exact: true })).toHaveValue("qa-train");
  // Hold the catalog reload that follows the next registration (its own result is already in place by then).
  await page.evaluate(() => {
    const internals = (window as any).__TAURI_INTERNALS__;
    const original = internals.invoke;
    let release = () => {};
    const held = new Promise<void>(resolve => { release = resolve; });
    (window as any).__releaseCatalog = () => release();
    internals.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args?.action === "catalog") { (window as any).__catalogWaiting = true; await held; }
      return original(command, args);
    };
  });
  await page.locator("#import-register-form").getByLabel("데이터 ID", { exact: true }).fill("qa2");
  await registerButton(page).click();
  await expect.poll(() => page.evaluate(() => (window as any).__catalogWaiting === true)).toBe(true);
  // Navigating reads every view's drafts; the locked split form still shows the previous defaults.
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.evaluate(() => (window as any).__releaseCatalog());
  await idle(page);
  await page.getByRole("button", { name: "열 연결해 가져오기", exact: true }).click();
  await expect(page.locator("#import-registered")).toContainText("qa2 · v1 · 66666666");
  await expect(page.locator("#import-split").getByLabel("학습 데이터 ID", { exact: true })).toHaveValue("qa2-train");
  await expect(page.locator("#import-split").getByLabel("시험 데이터 ID", { exact: true })).toHaveValue("qa2-test");
});

/** Creates a project from the overview panel and opens its import page through the project page's link. */
async function importForNewProject(page: Page, name: string) {
  await page.locator('#app [data-tour="project-start"]').getByLabel("프로젝트 이름").fill(name);
  await page.locator('#app [data-tour="project-start"]').getByRole("button", { name: "만들기", exact: true }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
}
async function splitAndCommit(page: Page) {
  const split = page.locator("#import-split");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  await split.getByRole("button", { name: "분할 등록", exact: true }).click();
  await idle(page);
}

test("registering again keeps the committed split and the same version is not offered twice", async ({ page }) => {
  await openWorkbench(page, { definitions: true, workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))],
    dataset_import_register: [registered("qa", "1"), registered("qa", "2", "5".repeat(64))],
    dataset_split_preview: ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
    dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  await importForNewProject(page, "다시 등록");
  await importFile(page);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  await register(page, "qa", "1");
  await splitAndCommit(page);
  const link = page.locator("#import-link");
  await expect(link).toContainText(`학습 · qa-train · v1 · ${"7".repeat(8)}`);
  // The registered preview, ID and version are not offered again; a programmatic submit sends nothing either.
  await expect(registerButton(page)).toBeDisabled();
  await expect(page.locator("#import-register-actions")).toContainText("이미 등록했습니다");
  await page.locator("#import-register-form").evaluate(form => (form as HTMLFormElement).requestSubmit());
  await idle(page);
  expect(await calls(page, { action: "dataset_import_register" })).toHaveLength(1);
  await expect(page.locator("#import-split-committed")).toContainText("qa-train · v1");
  await expect(link).toContainText(`학습 · qa-train · v1 · ${"7".repeat(8)}`);
  await expect(link).not.toContainText("학습 · qa · v1");
  // Another version is a new registration: typing it enables 등록 at once, and its split starts over.
  await page.locator("#import-register-form").getByLabel("버전", { exact: true }).fill("2");
  await expect(registerButton(page)).toBeEnabled();
  await registerButton(page).click();
  await idle(page);
  await expect(page.locator("#import-registered")).toContainText(`qa · v2 · ${"5".repeat(8)}`);
  await expect(page.locator("#import-split-committed")).toHaveCount(0);
  await expect(link).toContainText(`학습 · qa · v2 · ${"5".repeat(8)}`);
});

test("an answer naming the registered version again keeps its split", async ({ page }) => {
  const other = { instruction: ["question"], input: ["answer"], output: ["answer"] };
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64)), mappedPreview("3".repeat(64), { mapping: mapping({ fields: other }) })],
    dataset_import_register: registered("qa", "1"),
    dataset_split_preview: ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
    dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  await readyToRegister(page);
  await register(page, "qa", "1");
  await splitAndCommit(page);
  // Another preview may be registered (the server decides); an answer that names the same version keeps the split.
  await mapColumns(page, { 입력: "answer" });
  await preview(page);
  await expect(registerButton(page)).toBeEnabled();
  await registerButton(page).click();
  await idle(page);
  expect(await calls(page, { action: "dataset_import_register" })).toHaveLength(2);
  await expect(page.locator("#import-split-committed")).toContainText("qa-train · v1");
  await expect(page.locator("#import-split").getByLabel("학습 데이터 ID", { exact: true })).toHaveValue("qa-train");
});

test("linking again after the project was saved says nothing new was added", async ({ page }) => {
  await openWorkbench(page, { definitions: true, workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered() } });
  await importForNewProject(page, "저장 후 다시 연결");
  await importFile(page);
  await mapColumns(page, { 지시문: "question", 입력: "context", 정답: "answer" });
  await preview(page);
  await register(page);
  const link = page.locator("#import-link");
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  await expect(link).toContainText("저장 전 연결: train");
  await link.getByRole("button", { name: "프로젝트에서 저장하기", exact: true }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  expect(await calls(page, { action: "project_save" })).toHaveLength(1);
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
  await expect(link.locator("#import-staged")).toHaveCount(0);
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  const note = link.locator("#import-link-note");
  await expect(note).toHaveText("이미 연결된 데이터 버전은 다시 추가하지 않았습니다.");
  await expect(note).toBeFocused();
  await expect(link.locator("#import-staged")).toHaveCount(0);
  expect(await calls(page, { action: "project_save" })).toHaveLength(1);
});

test("what is typed while the file dialog is open is kept", async ({ page }) => {
  await openWorkbench(page, { holds: ["pick_file"], native: { pick_file: "C:\\data\\other.csv" },
    workflow: { dataset_import_source: sourceOk, dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))] } });
  await readyToRegister(page);
  await sourceCard(page).getByRole("button", { name: "파일 찾기" }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__held("pick_file"))).toBe(1);
  // The dialog has no parent window, so the page stays usable meanwhile.
  const form = page.locator("#import-register-form");
  await form.getByLabel("더미·합성 데이터입니다").check();
  await form.getByLabel("이 데이터를 선택한 GPU로 전송해도 됩니다").check();
  await page.evaluate(() => (window as any).__release("pick_file"));
  await expect(sourceCard(page).getByLabel("파일 경로", { exact: true })).toHaveValue("C:\\data\\other.csv");
  await expect(form.getByLabel("더미·합성 데이터입니다")).toBeChecked();
  await expect(form.getByLabel("이 데이터를 선택한 GPU로 전송해도 됩니다")).toBeChecked();
});

test("previews stay bounded: file problems first, capped columns, choices and samples", async ({ page }) => {
  const many = Array.from({ length: 600 }, (_, index) => `col${String(index).padStart(3, "0")}`);
  const row = (n: number) => Object.fromEntries(many.map(field => [field, `${field}-${n}`]));
  const rows = Array.from({ length: 60 }, (_, index) => issue("invalid_row", "'col001'", "/mapping", { row_number: index + 1 }));
  const sizeLimit = issue("size_limit", "canonical dataset exceeds 32 MiB limit", "/mapping");
  const eight = [1, 2, 3, 4, 5, 6, 7, 8];
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk, dataset_import_preview: [
    discovery({ source_fields: many, source_samples: eight.map(row) }),
    invalid([...rows, sizeLimit], previewData({ source_fields: many, source_samples: eight.map(row),
      samples: eight.map(n => ({ instruction: `질문 ${n}`, output: `답 ${n}` })), mapping: mapping({ fields: { instruction: ["col000"], output: ["col001"] } }) }))] } });
  await openImport(page);
  await importFile(page);
  const mappingCard = page.locator("#import-mapping");
  await expect(mappingCard.getByLabel("지시문", { exact: true }).locator("option")).toHaveCount(501);
  await expect(mappingCard).toContainText("열이 600개라 앞 500개만 고를 수 있습니다.");
  await expect(mappingCard.locator("table.import-samples thead th")).toHaveCount(31);
  await expect(mappingCard).toContainText("열 600개 중 앞 30개만 표시합니다.");
  await expect(mappingCard.locator("table.import-samples tbody tr")).toHaveCount(5);
  await mapColumns(page, { 지시문: "col000", 정답: "col001" });
  await preview(page);
  const items = page.locator("#import-preview .import-issues li");
  await expect(items.first()).toHaveAttribute("data-code", "size_limit");
  await expect(items.first().locator(".import-issue-where")).toHaveText("파일");
  await expect(items.first()).toContainText("데이터가 32 MiB 한도를 넘습니다.");
  await expect(items.nth(1).locator(".import-issue-where")).toHaveText("1행");
  await expect(items.last()).toHaveText("외 11건");
  await expect(page.locator("#import-preview table.import-samples tbody tr")).toHaveCount(5);
});

test("split refusals point at their fields", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered(),
    dataset_split_preview: [invalid([issue("practice_reserved", PRACTICE_REFUSAL, "/plan/outputs/train/dataset_id")]),
      ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() })],
    dataset_split_commit: conflict([issue("version_exists", "immutable dataset version already registered without this provenance", "/plan_sha256")]) } });
  await readyToRegister(page);
  await register(page);
  const split = page.locator("#import-split");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  await expect(page.locator("#import-out-train-issues")).toContainText(PRACTICE_REFUSAL);
  await expect(split.getByLabel("학습 데이터 ID", { exact: true })).toBeFocused();
  await expect(split.getByLabel("학습 데이터 ID", { exact: true })).toHaveAttribute("aria-invalid", "true");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await idle(page);
  await expect(page.locator("#import-out-train-issues")).toHaveCount(0);
  await split.getByRole("button", { name: "분할 등록", exact: true }).click();
  await idle(page);
  await expect(page.locator("#import-split-version-issues")).toContainText("이미 등록된 버전입니다. 다른 버전을 입력하세요.");
  await expect(split.getByLabel("분할 버전", { exact: true })).toBeFocused();
});

test("registered and split versions show their data flags", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered(),
    dataset_split_preview: ok({ plan_sha256: PLAN, counts: { train: 8, validation: 1, test: 1 }, outputs: splitOutputs() }),
    dataset_split_commit: ok({ plan_sha256: PLAN, outputs: splitOutputs() }) } });
  await readyToRegister(page);
  await page.locator("#import-register-form").getByLabel("더미·합성 데이터입니다").check();
  await register(page);
  await expect(page.locator("#import-registered")).toContainText("합성 데이터 · GPU 전송 미허용");
  await expect(page.locator("#import-split")).toContainText("분할 결과도 합성 데이터 · GPU 전송 미허용");
  await splitAndCommit(page);
  await expect(page.locator("#import-split-committed")).toContainText("분할 결과도 합성 데이터 · GPU 전송 미허용");
});

test("split output IDs must differ from each other and from the source", async ({ page }) => {
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery(), mappedPreview("2".repeat(64))], dataset_import_register: registered() } });
  await readyToRegister(page);
  await register(page);
  const split = page.locator("#import-split");
  await split.getByLabel("검증 데이터 ID", { exact: true }).fill("qa-train");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await expect(split).toContainText("학습·검증·시험 데이터 ID는 서로 달라야 합니다.");
  await expect(split.getByLabel("검증 데이터 ID", { exact: true })).toBeFocused();
  await split.getByLabel("검증 데이터 ID", { exact: true }).fill("qa-validation");
  await split.getByLabel("학습 데이터 ID", { exact: true }).fill("qa");
  await split.getByRole("button", { name: "분할 미리보기", exact: true }).click();
  await expect(split).toContainText("등록한 원본(qa · v1)과 같은 ID·버전은 쓸 수 없습니다.");
  await expect(split.getByLabel("학습 데이터 ID", { exact: true })).toBeFocused();
  expect(await calls(page, { action: "dataset_split_preview" })).toEqual([]);
});

test("a changed preview is reported once and names keep their direction", async ({ page }) => {
  const hebrew = "\u05E9\u05D0\u05DC\u05D4";                     // a right-to-left column and file name
  const path = `C:\\data\\${hebrew}.csv`;
  await openWorkbench(page, { workflow: { dataset_import_source: sourceOk,
    dataset_import_preview: [discovery({ source_fields: ["answer", "context", hebrew] }), mappedPreview("2".repeat(64))],
    dataset_import_register: conflict([issue("preview_changed", "import preview changed; inspect the mapping again", "/preview_sha256")]) } });
  await openImport(page);
  await importFile(page, path);
  await expect(page.locator(".import-source-facts bdi")).toHaveText(path);
  await expect(page.locator("#import-mapping table.import-samples thead th bdi")).toHaveCount(3);
  // <option> text cannot hold <bdi>: first-strong isolate marks wrap the name (escapes in the source, same characters).
  await expect(page.locator("#import-mapping").getByLabel("지시문", { exact: true }).locator("option", { hasText: hebrew }))
    .toHaveJSProperty("text", `\u2068${hebrew}\u2069`);
  await mapColumns(page, { 지시문: hebrew, 입력: "context", 정답: "answer" });
  await preview(page);
  await register(page, "qa", "1");
  const changed = "미리본 뒤 원본이나 설정이 바뀌었습니다. 다시 미리보세요.";
  const card = await page.locator("#import-register").textContent();
  expect(card!.split(changed).length - 1).toBe(1);
  await expect(page.locator("#import-preview-state")).toContainText(changed);
  await expect(registerButton(page)).toBeDisabled();
});
