import { test, expect, type Page } from "@playwright/test";
import { calls, config, HOSTILE, invalid, issue, NOT_AVAILABLE, ok, openWorkbench } from "./workflow-mock.ts";

const REQUEST_ID = /^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/;
const LEGACY_DATASET_MISSING = "기존 프로필의 데이터 버전을 프로젝트에 연결하지 못했습니다. 데이터를 등록한 뒤 프로젝트에서 직접 연결하세요.";
const heading = (page: Page) => page.getByRole("heading", { level: 1 });
const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const projectForm = (page: Page) => page.locator("#project-form");
// A direct bridge call, as another window of the app would make it (the stateful definitions fake answers it).
const bridge = (page: Page, action: string, data: object) =>
  page.evaluate(([action, data]) => (window as any).__TAURI_INTERNALS__.invoke("workflow_action", { action, data }), [action, data] as const);
const workflowActions = async (page: Page, from = 0) =>
  (await calls(page)).slice(from).filter(call => call.command === "workflow_action").map(call => call.args.action);

/** Creates a project from the overview panel and waits for its page. */
async function createProject(page: Page, name: string, purpose = "") {
  const start = page.locator('#app [data-tour="project-start"]');
  await start.getByLabel("프로젝트 이름").fill(name);
  if (purpose) await start.getByLabel("목적").fill(purpose);
  await start.getByRole("button", { name: "만들기", exact: true }).click();
  await expect(heading(page)).toHaveText(name);
  await idle(page);
}
async function saveProject(page: Page) {
  await projectForm(page).getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
}

test("the first screen offers a blank project with a free name and purpose", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  const start = page.locator('#app [data-tour="project-start"]');
  await expect(start).toHaveCount(1);
  await expect(start.getByRole("heading", { name: "빈 프로젝트 만들기" })).toBeVisible();
  await expect(page.locator(".page-heading").getByRole("button", { name: "새 프로젝트" })).toHaveAttribute("data-action", "nav:projects");
  // Nothing of the workflow channel runs before the user asks for it.
  expect(await workflowActions(page)).toEqual([]);
  await createProject(page, `${HOSTILE} 단백질 서열 요약`, "논문 없이 서열 설명 생성");
  const created = await calls(page, { action: "project_create" });
  expect(created.map(call => call.args.data)).toEqual([{ request_id: expect.stringMatching(REQUEST_ID),
    name: `${HOSTILE} 단백질 서열 요약`, purpose: "논문 없이 서열 설명 생성", task_type: "" }]);
  await expect(page.locator(".breadcrumb")).toHaveText(`내 워크스페이스 / 프로젝트 / ${HOSTILE} 단백질 서열 요약`);
  await expect(page.locator('nav [data-action="nav:projects"]')).toHaveClass("selected");
  const form = projectForm(page);
  await expect(form.getByLabel("프로젝트 이름")).toHaveValue(`${HOSTILE} 단백질 서열 요약`);
  await expect(form.getByLabel("목적")).toHaveValue("논문 없이 서열 설명 생성");
  // The created project takes focus.
  await expect(form.getByLabel("프로젝트 이름")).toBeFocused();
  await expect(page.locator("img")).toHaveCount(0);
});

test("a blank project saves and reopens without any example", async ({ page }) => {
  await openWorkbench(page, { definitions: true, catalog: { templates: [] } });
  await createProject(page, "빈 프로젝트");
  expect(await calls(page, { action: "project_get" })).toEqual([]);
  const form = projectForm(page);
  await form.getByLabel("목적").fill("서열 설명");
  await form.getByLabel("작업 유형").fill("요약");
  await saveProject(page);
  await expect(page.getByText("프로젝트를 저장했습니다.")).toBeVisible();
  const saves = await calls(page, { action: "project_save" });
  expect(saves).toHaveLength(1);
  const { spec, expected_revision } = saves[0].args.data;
  expect(spec).toEqual({ schema_version: 1, project_id: expect.any(String), name: "빈 프로젝트", purpose: "서열 설명",
    task_type: "요약", datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: null });
  // The expected revision is the one project_create returned (the blank creation content).
  const createdRevision = await bridge(page, "project_get", { project_id: spec.project_id, revision: expected_revision });
  expect(createdRevision.data.project).toMatchObject({ purpose: "", task_type: "" });

  await page.locator('nav [data-action="nav:history"]').click();
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  const row = page.locator(".project-list tbody tr", { hasText: "빈 프로젝트" });
  await expect(row).toContainText("서열 설명");
  await expect(row).toContainText("요약");
  const reads = (await calls(page, { action: "project_get" })).length;
  await row.getByRole("button", { name: /열기/ }).click();
  await idle(page);
  expect((await calls(page, { action: "project_get" })).slice(reads).map(call => call.args.data)).toEqual([{ project_id: spec.project_id }]);
  await expect(heading(page)).toHaveText("빈 프로젝트");
  await expect(form.getByLabel("목적")).toHaveValue("서열 설명");
  await expect(form.getByLabel("작업 유형")).toHaveValue("요약");
  // No recipe, example or training-page detour was needed.
  expect([...await calls(page, { action: "recipe_save" }), ...await calls(page, { action: "recipe_clone" })]).toEqual([]);
  expect(await calls(page, { action: "capabilities" })).toHaveLength(1);
});

test("examples stay optional starting points", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await expect(page.getByRole("heading", { name: "예제 시작점 · 선택", exact: true })).toBeVisible();
  await expect(page.locator('#app [data-tour="project-start"]')).toBeVisible();
  await page.locator('[data-action="template:0"]').click();
  await expect(heading(page)).toHaveText("학습 설정");
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue(config.ai_id);
  await expect(page.getByLabel("업무 흐름 ID", { exact: true })).toHaveValue(config.workflow_id);
  expect(await calls(page, { command: "workflow_action" })).toEqual([]);
});

test("a stale project save keeps the draft and offers reload or overwrite", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await createProject(page, "공동 프로젝트");
  const form = projectForm(page);
  await form.getByLabel("목적").fill("내 목적");
  // Another window saves first.
  const listed = await bridge(page, "projects_list", {});
  const { project_id, revision_sha256 } = listed.data.projects[0];
  const current = await bridge(page, "project_get", { project_id });
  const other = await bridge(page, "project_save", { spec: { ...current.data.project, purpose: "다른 창 목적", task_type: "분류" },
    expected_revision: revision_sha256 });
  expect(other.status).toBe("ok");

  await saveProject(page);
  const banner = page.locator(".wf-banner");
  await expect(banner).toContainText("다른 곳에서 먼저 저장한 최신 버전이 있습니다.");
  await expect(banner.getByRole("button", { name: "최신 버전 불러오기" })).toBeVisible();
  await expect(form.getByLabel("목적")).toHaveValue("내 목적");
  await expect(form.getByLabel("프로젝트 이름")).toHaveValue("공동 프로젝트");
  const before = (await calls(page)).length;
  await banner.getByRole("button", { name: "내 변경으로 덮어쓰기" }).click();
  const dialog = page.getByRole("dialog", { name: "내 변경으로 덮어쓰기" });
  await expect(dialog).toContainText("다른 곳에서 먼저 저장한 변경");
  await dialog.getByRole("button", { name: "덮어쓰기", exact: true }).click();
  await idle(page);
  expect(await workflowActions(page, before)).toEqual(["project_get", "project_save"]);
  const last = (await calls(page, { action: "project_save" })).at(-1).args.data;
  expect(last.expected_revision).toBe(other.data.revision_sha256);
  expect(last.spec).toMatchObject({ project_id, purpose: "내 목적", name: "공동 프로젝트" });
  await expect(page.locator(".wf-banner")).toHaveCount(0);
  await expect(page.getByText("프로젝트를 저장했습니다.")).toBeVisible();
  await expect(form.getByLabel("목적")).toHaveValue("내 목적");
});

test("data bindings choose registered versions and refuse duplicate IDs", async ({ page }) => {
  const datasets = [
    { dataset_id: "dummy", version: "1", rows: 2, export_allowed: true, synthetic: true, kind: "instruction", sha256: "5".repeat(64) },
    { dataset_id: "extra", version: "2", rows: 4, export_allowed: false, synthetic: false, kind: "instruction", sha256: "6".repeat(64) },
    { dataset_id: "tutorial_sample", version: "1", rows: 6, export_allowed: false, synthetic: true, kind: "instruction", sha256: "7".repeat(64) },
  ];
  await openWorkbench(page, { definitions: true, catalog: { datasets } });
  await createProject(page, `${HOSTILE} 데이터 연결`);
  const dataset = page.getByLabel("등록한 데이터 버전");
  const role = page.getByLabel("데이터 역할");
  const add = page.getByRole("button", { name: "데이터 연결 추가", exact: true });
  await expect(dataset.locator("option", { hasText: "tutorial_sample · v1" })).toContainText("연습 데이터 · 실제 실행 불가");
  await dataset.selectOption("dummy:1");
  await role.selectOption("train");
  await add.click();
  const rows = page.locator(".binding-table tbody tr");
  await expect(rows).toHaveCount(1);
  await expect(rows.first()).toContainText("train");
  await expect(rows.first()).toContainText("학습");
  await expect(rows.first()).toContainText("dummy · v1");
  await dataset.selectOption("extra:2");
  await role.selectOption("train");
  await add.click();
  await expect(rows).toHaveCount(2);
  await expect(rows.nth(1)).toContainText("train-2");
  // The same registered version is never bound twice.
  await dataset.selectOption("dummy:1");
  await role.selectOption("validation");
  await add.click();
  await expect(page.locator(".binding-note")).toContainText("이미 연결된 데이터 버전입니다.");
  await expect(rows).toHaveCount(2);
  await saveProject(page);
  const [save] = await calls(page, { action: "project_save" });
  expect(save.args.data.spec.datasets).toEqual([
    { binding_id: "train", role: "train", dataset: { dataset_id: "dummy", version: "1", sha256: "5".repeat(64) } },
    { binding_id: "train-2", role: "train", dataset: { dataset_id: "extra", version: "2", sha256: "6".repeat(64) } },
  ]);
  // A binding no graph card uses can be removed again.
  await rows.nth(1).getByRole("button", { name: /연결 해제/ }).click();
  await expect(rows).toHaveCount(1);
  await expect(page.locator("img")).toHaveCount(0);
});

test("a legacy setting imports as a draft project and recipe", async ({ page }) => {
  const project = { schema_version: 1, project_id: "legacy-project-1", name: "paper_extraction", purpose: "", task_type: "paper_processing",
    datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: "paper_extraction" };
  const recipe = { schema_version: 1, recipe_id: "legacy-0123456789abcdef01234567", version: "1", name: "paper_extraction", description: "",
    training_backend: "llamafactory", capability_version: "lf-text-v1", training_stage: "sft", training_method: "lora", input_kind: "instruction",
    quantization_bit: 4, hyperparameters: config.hyperparameters, finetuning_options: {}, stage_options: {}, evaluation_defaults: {} };
  const revision = "a".repeat(64);
  await openWorkbench(page, {
    catalog: { profiles: [{ ...config, ai_id: "paper_extraction" }, { ...config, ai_id: HOSTILE }] },
    workflow: {
      projects_list: ok({ projects: [] }),
      project_import_legacy: ok({ project, revision_sha256: revision, recipe, recipe_revision_sha256: "b".repeat(64), datasets: [] },
        [issue("legacy_dataset_missing", LEGACY_DATASET_MISSING, "/ai_id")]),
      project_get: ok({ project, revision_sha256: revision }),
    },
  });
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  const legacy = page.locator(".legacy-import");
  await expect(legacy).toContainText(HOSTILE);
  await legacy.locator("tr", { hasText: "paper_extraction" }).getByRole("button", { name: /가져오기/ }).click();
  await idle(page);
  const imports = await calls(page, { action: "project_import_legacy" });
  expect(imports.map(call => call.args.data)).toEqual([{ request_id: expect.stringMatching(REQUEST_ID), ai_id: "paper_extraction" }]);
  await expect(heading(page)).toHaveText("paper_extraction");
  const note = page.locator(".import-note");
  await expect(note).toContainText(LEGACY_DATASET_MISSING);
  await expect(note).toContainText("bbbbbbbb");
  await expect(projectForm(page).getByLabel("작업 유형")).toHaveValue("paper_processing");
  await expect(page.getByText("기존 설정에서 가져옴 · paper_extraction")).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
});

test("project creation is not submitted twice while busy", async ({ page }) => {
  await openWorkbench(page, { definitions: true, holds: ["project_create"] });
  const start = page.locator('#app [data-tour="project-start"]');
  await start.getByLabel("프로젝트 이름").fill("한 번만 만들기");
  const create = start.getByRole("button", { name: "만들기", exact: true });
  await create.dblclick();
  await expect(create).toBeDisabled();
  await start.getByLabel("프로젝트 이름").press("Enter");
  await page.locator("#project-create-form").evaluate(form => (form as HTMLFormElement).requestSubmit());
  expect(await page.evaluate(() => (window as any).__held("project_create"))).toBe(1);
  expect(await calls(page, { action: "project_create" })).toHaveLength(1);
  await page.evaluate(() => (window as any).__release("project_create"));
  await expect(heading(page)).toHaveText("한 번만 만들기");
  expect(await calls(page, { action: "project_create" })).toHaveLength(1);
});

test("a retried project creation reuses its request ID until the details change", async ({ page }) => {
  const created = { project: { schema_version: 1, project_id: "retry-project", name: "다시 정한 이름", purpose: "", task_type: "", datasets: [],
    default_executor_profile_id: null, graph: {}, legacy_origin: null }, revision_sha256: "c".repeat(64) };
  const lost = { reject: "엔진 응답을 받지 못했습니다." };
  await openWorkbench(page, { workflow: { project_create: [lost, lost, ok(created)] } });
  const start = page.locator('#app [data-tour="project-start"]');
  const create = start.getByRole("button", { name: "만들기", exact: true });
  await start.getByLabel("프로젝트 이름").fill("재시도 프로젝트");
  await create.click();
  await expect(page.getByRole("alert")).toContainText("엔진 응답을 받지 못했습니다.");
  await expect(start.getByLabel("프로젝트 이름")).toHaveValue("재시도 프로젝트");
  await expect(create).toBeFocused();
  await create.click();
  await idle(page);
  await start.getByLabel("프로젝트 이름").fill("다시 정한 이름");
  await create.click();
  await expect(heading(page)).toHaveText("다시 정한 이름");
  const ids = (await calls(page, { action: "project_create" })).map(call => call.args.data.request_id);
  expect(ids).toHaveLength(3);
  expect(ids[1]).toBe(ids[0]);
  expect(ids[2]).not.toBe(ids[0]);
  expect(ids[2]).toMatch(REQUEST_ID);
});

test("project forms are not nested and keep focus after saving", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await expect(page.locator("form form")).toHaveCount(0);
  await createProject(page, "초점 프로젝트");
  await expect(page.locator("form form")).toHaveCount(0);
  const form = projectForm(page);
  await form.getByLabel("목적").fill("초점 유지");
  const save = form.getByRole("button", { name: "저장", exact: true });
  await save.click();
  await idle(page);
  await expect(page.getByText("프로젝트를 저장했습니다.")).toBeVisible();
  await expect(save).toBeFocused();
  // Enter in a text field saves through the same form, once.
  await form.getByLabel("작업 유형").fill("요약");
  await form.getByLabel("작업 유형").press("Enter");
  await idle(page);
  expect(await calls(page, { action: "project_save" })).toHaveLength(2);
  await expect(form.getByLabel("작업 유형")).toBeFocused();
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("#project-create-form")).toHaveCount(1);
  await page.locator('nav [data-action="nav:recipes"]').click();
  await idle(page);
  await page.getByRole("button", { name: "새 레시피", exact: true }).click();
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("dialog")).toHaveCount(0);
});

// -- review fixes: overlapping tasks, late renders, textarea drafts (Task 10.2 review I1, I2, M3, M7) --

const release = (page: Page, name: string) => page.evaluate(name => (window as any).__release(name), name);

test("a save still running keeps the workbench busy and the next page waits for it", async ({ page }) => {
  await openWorkbench(page, { definitions: true, holds: ["project_save"] });
  await createProject(page, "느린 저장");
  await projectForm(page).getByLabel("목적").fill("저장 중인 목적");
  await projectForm(page).getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.locator(".notice.busy")).toContainText("프로젝트 저장 중");
  await page.locator('nav [data-action="nav:projects"]').click();
  await expect(heading(page)).toHaveText("프로젝트");
  // The list read waits for the save, and nothing re-enables while the save is in flight.
  await expect(page.locator(".notice.busy")).toContainText("프로젝트 저장 중");
  await expect(page.getByRole("button", { name: "만들기", exact: true })).toBeDisabled();
  await expect(page.locator("#project-create-name")).toBeDisabled();
  expect(await calls(page, { action: "projects_list" })).toHaveLength(0);
  await release(page, "project_save");
  await idle(page);
  expect(await calls(page, { action: "projects_list" })).toHaveLength(1);
  await expect(page.locator(".project-list tbody tr", { hasText: "느린 저장" })).toContainText("저장 중인 목적");
  await expect(page.getByRole("button", { name: "만들기", exact: true })).toBeEnabled();
});

test("a task finishing after the user moved on keeps their typing, focus and page", async ({ page }) => {
  await openWorkbench(page, { definitions: true, holds: ["project_save"] });
  await createProject(page, "멀리 간 사용자");
  await projectForm(page).getByLabel("목적").fill("저장할 목적");
  await projectForm(page).getByRole("button", { name: "저장", exact: true }).click();
  // On another page, the user types into a field that the finishing save must not wipe or blur.
  await page.locator('nav [data-action="nav:datasets"]').click();
  const datasetId = page.getByLabel("데이터 ID", { exact: true });
  await datasetId.fill("새 데이터");
  await release(page, "project_save");
  await idle(page);
  await expect(heading(page)).toHaveText("데이터");
  await expect(datasetId).toHaveValue("새 데이터");
  await expect(datasetId).toBeFocused();
  expect((await calls(page, { action: "project_save" })).at(-1).args.data.spec.purpose).toBe("저장할 목적");
});

test("a project created after the user moved on does not pull them to its page", async ({ page }) => {
  await openWorkbench(page, { definitions: true, holds: ["project_create"] });
  const start = page.locator('#app [data-tour="project-start"]');
  await start.getByLabel("프로젝트 이름").fill("나중에 만든 프로젝트");
  await start.getByRole("button", { name: "만들기", exact: true }).click();
  // The projects page shows its own (locked) create form with the name being created.
  await page.locator('nav [data-action="nav:projects"]').click();
  await expect(page.locator("#project-create-name")).toHaveValue("나중에 만든 프로젝트");
  await release(page, "project_create");
  await idle(page);
  await expect(heading(page)).toHaveText("프로젝트");
  await expect(page.getByText("프로젝트를 만들었습니다: ‘나중에 만든 프로젝트’.", { exact: false })).toBeVisible();
  await expect(page.locator(".project-list tbody tr", { hasText: "나중에 만든 프로젝트" })).toHaveCount(1);
  // The created project's name is not read back into the form, where it could be submitted again.
  await expect(page.locator("#project-create-name")).toHaveValue("");
  expect(await calls(page, { action: "project_create" })).toHaveLength(1);
});

test("a finished task never moves the focus onto another page's control", async ({ page }) => {
  await openWorkbench(page, { holds: ["project_create"], workflow: { projects_list: ok({ projects: [] }),
    project_create: invalid([issue("invalid_value", "String should have at most 200 characters", "/name")]) } });
  const start = page.locator('#app [data-tour="project-start"]');
  await start.getByLabel("프로젝트 이름").fill("거절될 이름");
  await start.getByRole("button", { name: "만들기", exact: true }).click();
  // The projects page has its own "만들기" (the same focus key): the refused create must not focus it.
  await page.locator('nav [data-action="nav:projects"]').click();
  await expect(heading(page)).toHaveText("프로젝트");
  await release(page, "project_create");
  await idle(page);
  await expect(page.locator("#project-create-form").getByRole("button", { name: "만들기", exact: true })).not.toBeFocused();
  await expect(page.locator("#project-create-form")).toContainText("입력값이 올바르지 않습니다.");
});

test("a purpose with a leading newline or Windows line breaks stays clean", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await createProject(page, "줄바꿈 목적");
  // Another writer saves a purpose that starts with a newline and uses CRLF line breaks.
  const listed = await bridge(page, "projects_list", {});
  const { project_id, revision_sha256 } = listed.data.projects[0];
  const current = await bridge(page, "project_get", { project_id });
  const purpose = "\n첫 줄은 비움\r\n둘째 줄";
  expect((await bridge(page, "project_save", { spec: { ...current.data.project, purpose }, expected_revision: revision_sha256 })).status).toBe("ok");
  const open = async () => {
    await page.locator('nav [data-action="nav:history"]').click();
    await page.locator('nav [data-action="nav:projects"]').click();
    await idle(page);
    await page.locator(".project-list tbody tr", { hasText: "줄바꿈 목적" }).getByRole("button", { name: /열기/ }).click();
    await idle(page);
  };
  await open();
  const form = projectForm(page);
  await expect(form.getByLabel("목적")).toHaveValue("\n첫 줄은 비움\n둘째 줄");
  await expect(form.locator(".status-pill")).toContainText("저장된 버전");
  // Leaving and coming back re-reads it: a clean draft is never kept as if it had unsaved changes.
  const reads = (await calls(page, { action: "project_get" })).length;
  await open();
  expect(await calls(page, { action: "project_get" })).toHaveLength(reads + 1);
  await expect(form.locator(".status-pill")).toContainText("저장된 버전");
  // Saving without an edit sends the purpose exactly as stored.
  await saveProject(page);
  expect((await calls(page, { action: "project_save" })).at(-1).args.data.spec.purpose).toBe(purpose);
});

// -- deferred nit recorded for 10.8 (10.2 re-review): one-line inputs strip line breaks --

test("a name or task type with a line break from another writer stays clean", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await createProject(page, "줄바꿈 이름");
  // Another program saves a name and a task type with line breaks, which an <input> cannot show.
  const listed = await bridge(page, "projects_list", {});
  const { project_id, revision_sha256 } = listed.data.projects[0];
  const current = await bridge(page, "project_get", { project_id });
  const name = "줄바꿈\r\n이름", taskType = "요약\n분류\r";
  expect((await bridge(page, "project_save", { spec: { ...current.data.project, name, task_type: taskType },
    expected_revision: revision_sha256 })).status).toBe("ok");
  const open = async () => {
    await page.locator('nav [data-action="nav:history"]').click();
    await page.locator('nav [data-action="nav:projects"]').click();
    await idle(page);
    await page.locator(".project-list tbody tr", { hasText: "줄바꿈" }).getByRole("button", { name: /열기/ }).click();
    await idle(page);
  };
  await open();
  const form = projectForm(page);
  await expect(form.getByLabel("프로젝트 이름")).toHaveValue("줄바꿈이름");
  await expect(form.getByLabel("작업 유형")).toHaveValue("요약분류");
  await expect(form.locator(".status-pill")).toContainText("저장된 버전");
  // Leaving reads the form back: the draft stays clean, so coming back re-reads it instead of keeping a phantom change.
  const reads = (await calls(page, { action: "project_get" })).length;
  await open();
  expect(await calls(page, { action: "project_get" })).toHaveLength(reads + 1);
  await expect(form.locator(".status-pill")).toContainText("저장된 버전");
  // Saving without an edit sends both texts exactly as stored; an edit replaces them as typed.
  await saveProject(page);
  expect((await calls(page, { action: "project_save" })).at(-1).args.data.spec).toMatchObject({ name, task_type: taskType });
  await form.getByLabel("작업 유형").fill("요약 분류");
  await saveProject(page);
  expect((await calls(page, { action: "project_save" })).at(-1).args.data.spec).toMatchObject({ name, task_type: "요약 분류" });
});

test("the project list escapes every value and saving stops once the channel is unavailable", async ({ page }) => {
  const created = { project: { schema_version: 1, project_id: "unavailable-project", name: "설치 전 프로젝트", purpose: "", task_type: "",
    datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: null }, revision_sha256: "d".repeat(64) };
  await openWorkbench(page, { workflow: {
    projects_list: ok({ projects: [{ project_id: "p-1", name: "값 확인", purpose: "", task_type: "", revision_sha256: "e".repeat(64),
      legacy_origin: null, datasets: [], graph_nodes: HOSTILE }] }),
    project_create: ok(created) } });
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  await expect(page.locator(".project-list tbody tr")).toHaveCount(1);
  await expect(page.locator("img")).toHaveCount(0);
  // project_save is not installed (the mock's default answer): 저장 is disabled with the fixed reason.
  const start = page.locator("#project-create-form");
  await start.getByLabel("프로젝트 이름").fill("설치 전 프로젝트");
  await start.getByRole("button", { name: "만들기", exact: true }).click();
  await expect(heading(page)).toHaveText("설치 전 프로젝트");
  await saveProject(page);
  await expect(projectForm(page).getByRole("button", { name: "저장", exact: true })).toBeDisabled();
  await expect(projectForm(page)).toContainText(NOT_AVAILABLE);
});
