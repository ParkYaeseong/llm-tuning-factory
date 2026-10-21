// Cross-screen regression of the Task 10 workbench (10.8): every workflow page escapes its data, labels its controls and
// never nests forms; a blank project reaches a dry-run plan through the screens without any example. Every call is
// mocked (projects and recipes by the stateful definitions fake, the rest scripted); nothing reaches a network.
import { test, expect, type Page } from "@playwright/test";
import { calls, HOSTILE, invalid, issue, ok, openWorkbench, type BridgeOptions } from "./workflow-mock.ts";

const H = (c: string, n = 64) => c.repeat(n);
const ROOT = { repo_id: "Qwen/Qwen2.5-0.5B-Instruct", revision: H("a", 40), config_sha256: H("c"), tokenizer_fingerprint: H("d") };
const ROOT_ENTRY = { root: ROOT, snapshot_ready: true, targets_ready: true,
  profile: { repo_id: ROOT.repo_id, revision: ROOT.revision, config_sha256: ROOT.config_sha256, architecture: "qwen2", template: "qwen" } };
const SOURCE = H("1");
const absent = { state: "absent", owner_id: null, heartbeat_age_seconds: null };
const controllerStatus = ok({ controller: absent, current: null, broker: null, stop_requested: false,
  restart_required: { profiles: [], tracking: null }, runs: { active: 0 } });

const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const heading = (page: Page) => page.getByRole("heading", { level: 1 });
const bridge = (page: Page, action: string, data: object) =>
  page.evaluate(([action, data]) => (window as any).__TAURI_INTERNALS__.invoke("workflow_action", { action, data }), [action, data] as const);
const nav = (page: Page, id: string) => page.locator(`nav [data-action="nav:${id}"]`).click();

/** A CSV mapping as data-import.ts builds it. */
const mapping = (fields: object = {}) => ({ schema_version: 1, transform_version: "text-mapping-v1", source_format: "csv", encoding: "utf-8",
  csv_delimiter: ",", kind: "instruction", formatting: "alpaca", fields, message_tags: {}, boolean_labels: {} });
const previewData = (columns: string[], rows: object[], extra: object = {}) => ({ source_id: SOURCE, source_sha256: SOURCE, source_size: 512,
  canonical_sha256: null, canonical_size: null, row_count: rows.length, samples: [], issues: [], source_samples: rows, source_fields: columns,
  mapping: mapping(), preview_sha256: H("2"), ...extra });
/** The first preview maps no column: every row fails, and the columns and rows arrive with it (F20). */
const discovery = (columns: string[], rows: object[]) => invalid(rows.map((_, index) => issue("invalid_row", "'instruction'", "/mapping",
  { row_number: index + 1 })), previewData(columns, rows, { issues: rows.map((_, index) => ({ ordinal: index, code: "invalid_row", message: "'instruction'" })) }));

/** What every workflow page must hold: one heading, escaped data, labelled visible controls, no nested form, the shell's
 * footer as it is, and no storage write. */
async function checkPage(page: Page, title: string) {
  await idle(page);
  await expect(heading(page)).toHaveText(title);
  await expect(page.locator("h1")).toHaveCount(1);
  await expect(page.locator("img")).toHaveCount(0);
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("dialog")).toHaveCount(0);
  await expect(page.locator(".sidebar-foot small")).toHaveCount(1);
  const controls = page.locator("#app :is(input, select, textarea):visible");
  const count = await controls.count();
  for (let index = 0; index < count; index++) {
    const control = controls.nth(index);
    const described = await control.evaluate(element => element.outerHTML.slice(0, 160));
    await expect(control, `${title}: ${described}`).toHaveAccessibleName(/\S/);
  }
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
  expect(await page.evaluate(() => sessionStorage.length)).toBe(0);
}

test("workflow pages escape data, label controls and never nest forms", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const runId = H("9", 32);
  const node = (id: string, extra: object = {}) => ({ node_id: id, label: HOSTILE, kind: "train", status: "PENDING", job_id: null,
    job_status: null, attempt_id: null, attempts: 0, counted_attempts: 0, max_attempts: 2, resource_id: null,
    destination_profile_id: "pod-a", uncertain: false, awaiting_result: false, result_deadline: null, needs_operator: false,
    wait_reason: null, error: null, retry_at: null, tracking_status: "not_started", registration_status: "none", tracking_action: null,
    output: null, selection: null, cache_seed: null, ...extra });
  const run = { run_id: runId, status: "RUNNING", simulation: true, plan_sha256: H("c"), approved_cost_usd: 0, reserved_cost_usd: 0,
    budget_exhausted: false, unpriced_attempts: 0, created: 1792540800,
    tracking: { events: { PENDING: 1 }, graph: "pending", action: null }, nodes: [node("train-1", { wait_reason: `${HOSTILE} 대기` })] };
  const columns = ["answer", "context", HOSTILE];
  const rows = [1, 2].map(n => ({ answer: `답 ${n}`, context: `맥락 ${n}`, [HOSTILE]: `${HOSTILE} 질문 ${n}` }));
  await openWorkbench(page, {
    definitions: true,
    profiles: [{ profile_id: "pod-a", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: `${HOSTILE} Pod` } }],
    native: { controller_status: controllerStatus },
    workflow: {
      dataset_import_source: ok({ source_id: SOURCE, source_sha256: SOURCE, size: 512 }),
      // The mapped preview still fails one row, with a hostile issue message.
      dataset_import_preview: [discovery(columns, rows), invalid([issue("invalid_row", `${HOSTILE} 행 오류`, "/mapping", { row_number: 2 })],
        previewData(columns, rows, { canonical_sha256: H("3"), canonical_size: 90, mapping: mapping({ instruction: [HOSTILE], input: ["context"], output: ["answer"] }),
          samples: [{ instruction: `${HOSTILE} 질문 1`, input: "맥락 1", output: "답 1" }], issues: [{ ordinal: 1, code: "invalid_row", message: `${HOSTILE} 행 오류` }] }))],
      model_roots_list: ok({ roots: [ROOT_ENTRY] }), benchmarks_list: ok({ benchmarks: [] }), observations_list: ok({ observations: [] }),
      graph_validate: invalid([issue("train_contract", `${HOSTILE} 검사 문제`, "/graph/nodes/0", { node_id: "train-1" })]),
      graph_runs_list: ok({ runs: [{ run_id: runId, status: "RUNNING", simulation: true, plan_sha256: H("c"), created: 1792540800,
        nodes_by_status: { PENDING: 1 } }], controller: absent }),
      graph_run_get: ok({ run, controller: absent, plan: null, tracking_failures: [{ event_id: H("e"), kind: "node", node_id: "train-1",
        status: "FAILED", tries: 2, last_error: `${HOSTILE} 기록 오류`, action: null }] }),
    },
  });
  // Another window saved a recipe and a project with hostile names, a binding and a card with a hostile label.
  const recipe = await bridge(page, "recipe_save", { expected_revision: null, spec: { schema_version: 1, recipe_id: "hostile-recipe", version: "1",
    name: `${HOSTILE} 레시피`, description: HOSTILE, training_backend: "llamafactory", capability_version: "lf-text-v1", training_stage: "sft",
    training_method: "lora", input_kind: "instruction", quantization_bit: 4, hyperparameters: {}, finetuning_options: {}, stage_options: {},
    evaluation_defaults: {} } });
  const created = await bridge(page, "project_create", { request_id: "regression-1", name: `${HOSTILE} 프로젝트`, purpose: HOSTILE, task_type: HOSTILE });
  const graph = { nodes: [{ id: "train-1", kind: "train", label: HOSTILE, recipe_revision: recipe.data.revision_sha256,
    model_input: { policy: { base: ROOT, adapter: null }, initialization: "new_adapter" }, dataset_binding: "train", executor_profile_ids: ["pod-a"] }],
  max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } };
  expect((await bridge(page, "project_save", { expected_revision: created.data.revision_sha256, spec: { ...created.data.project, graph,
    datasets: [{ binding_id: "train", role: "train", dataset: { dataset_id: "dummy", version: "1", sha256: H("5") } }] } })).status).toBe("ok");

  await nav(page, "projects");
  await checkPage(page, "프로젝트");
  await expect(page.locator(".project-list")).toContainText(`${HOSTILE} 프로젝트`);
  await page.locator(".project-list tbody tr").getByRole("button", { name: /열기/ }).click();
  await checkPage(page, `${HOSTILE} 프로젝트`);
  await expect(page.locator("#project-form").getByLabel("목적")).toHaveValue(HOSTILE);

  await nav(page, "recipes");
  await idle(page);
  await page.locator(".recipe-list tbody tr").getByRole("button", { name: /열기/ }).click();
  await checkPage(page, "레시피");
  await expect(page.locator("#recipe-form").getByLabel("레시피 이름")).toHaveValue(`${HOSTILE} 레시피`);

  await nav(page, "projects");
  await idle(page);
  await page.locator(".project-list tbody tr").getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await idle(page);
  const source = page.locator('#app [data-tour="data-import"]');
  await source.getByLabel("파일 경로", { exact: true }).fill(`C:\\data\\${HOSTILE}.csv`);
  await source.getByRole("button", { name: "가져오기", exact: true }).click();
  await idle(page);
  for (const [label, column] of [["지시문", HOSTILE], ["입력", "context"], ["정답", "answer"]])
    await page.locator("#import-mapping").getByLabel(label, { exact: true }).selectOption(column);
  await page.getByRole("button", { name: "미리보기", exact: true }).click();
  await checkPage(page, "데이터 가져오기");
  await expect(page.locator("#import-preview")).toContainText(`${HOSTILE} 행 오류`);
  await expect(page.locator("#import-preview")).toContainText(`${HOSTILE} 질문 1`);

  await nav(page, "profiles");
  await idle(page);
  await expect(page.locator('#app [data-tour="executor-profiles"]')).toContainText(`${HOSTILE} Pod`);
  await page.getByRole("button", { name: `${HOSTILE} Pod 편집` }).click();
  await checkPage(page, "실행 프로필");

  await nav(page, "projects");
  await idle(page);
  await page.locator(".project-list tbody tr").getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "학습 그래프", exact: true }).click();
  await idle(page);
  await page.locator('#graph-cards [data-node-card="train-1"]').click();
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await checkPage(page, "학습 그래프");
  await expect(page.locator("#graph-preview svg g[data-node]")).toContainText(HOSTILE);
  await expect(page.locator('#graph-cards [data-card="train-1"]')).toContainText(`${HOSTILE} 검사 문제`);

  await nav(page, "runs");
  await idle(page);
  await page.locator(`#app [data-tour="graph-runs"] tr[data-run-id="${runId}"]`).getByRole("button", { name: "열기" }).click();
  await idle(page);
  await expect(page.locator("#run-detail")).toContainText(`${HOSTILE} 대기`);
  await expect(page.locator("#run-detail")).toContainText(`${HOSTILE} 기록 오류`);
  await checkPage(page, "그래프 실행");
  expect(errors).toEqual([]);
});

// -- a blank project through the screens without examples (§4 10.8 test 3; the parent Task 9 RED flow at the UI level) --

const QA = [1, 2, 3].map(n => ({ question: `질문 ${n}`, context: `맥락 ${n}`, answer: `답 ${n}` }));
const QA_COLUMNS = ["answer", "context", "question"];
const QA_MAPPING = { instruction: ["question"], input: ["context"], output: ["answer"] };
const DATASET = { dataset_id: "seq-qa", version: "1", sha256: H("4") };
const PINNED_REVISION = H("b", 40);
const PINNED = { repo_id: "Qwen/Qwen2.5-0.5B-Instruct", revision: PINNED_REVISION, config_sha256: H("c"), tokenizer_fingerprint: H("d") };
const PINNED_ENTRY = { root: PINNED, snapshot_ready: true, targets_ready: false,
  profile: { repo_id: PINNED.repo_id, revision: PINNED_REVISION, config_sha256: PINNED.config_sha256, architecture: "qwen2", template: "qwen" } };
const PROJECT = { name: "서열 설명 실험", purpose: "논문 없이 서열 설명을 만든다" };
const TRAIN_NODE = { id: "train-1", kind: "train", label: "학습 1", recipe_revision: expect.stringMatching(/^[0-9a-f]{64}$/),
  model_input: { policy: { base: PINNED, adapter: null }, initialization: "new_adapter" }, dataset_binding: "train" };

/**
 * Boots with no example at all (no catalog template, no saved setting) and walks the screens: a blank project from the
 * overview, 데이터 가져오기 from the project (CSV question/context/answer, preview, register, 프로젝트에 연결, save on the
 * project page), an SFT LoRA recipe, and the graph page with a pinned and snapshotted base model and the "단일 학습"
 * starter (unsaved). Returns the project ID.
 */
async function blankProjectToGraph(page: Page, workflow: BridgeOptions["workflow"] = {}, plan: object | null = null): Promise<string> {
  await openWorkbench(page, {
    definitions: true, catalog: { templates: [], profiles: [] },
    native: { controller_status: controllerStatus },
    workflow: {
      dataset_import_source: ok({ source_id: SOURCE, source_sha256: SOURCE, size: 512 }),
      dataset_import_preview: [discovery(QA_COLUMNS, QA), ok(previewData(QA_COLUMNS, QA, { canonical_sha256: H("3"), canonical_size: 90,
        mapping: mapping(QA_MAPPING), samples: QA.map(row => ({ instruction: row.question, input: row.context, output: row.answer })) }))],
      dataset_import_register: ok({ dataset: DATASET, preview_sha256: H("2") }),
      model_roots_list: [ok({ roots: [] }), ok({ roots: [PINNED_ENTRY] })],
      model_root_snapshot: ok({ root: PINNED, profile: PINNED_ENTRY.profile, files: ["config.json", "tokenizer.json"] }),
      benchmarks_list: ok({ benchmarks: [] }), observations_list: ok({ observations: [] }),
      graph_validate: ok({ project_id: "validated", project_revision: H("1"), node_count: 1 }),
      ...workflow,
    },
  });
  // HF metadata (legacy model_search/model_inspect) answers from the page, as the graph editor spec does: no network.
  await page.evaluate(([repo, revision]) => {
    const native = (window as any).__TAURI_INTERNALS__, original = native.invoke;
    native.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action === "model_search") return { models: [{ id: repo, downloads: 1, pipeline_tag: "text-generation" }] };
      if (command === "factory_action" && args.action === "model_inspect")
        return { repo_id: args.data.model, revision, architecture: "qwen2", supported: true, reasons: [], metadata_verified: true };
      return original(command, args);
    };
  }, [PINNED.repo_id, PINNED_REVISION] as const);
  if (plan) await installPlanFake(page, plan);
  // Nothing of the workflow channel ran at boot.
  expect((await calls(page)).filter(call => call.command === "workflow_action")).toEqual([]);

  // 1. A blank project from the overview: a free name and purpose.
  const start = page.locator('#app [data-tour="project-start"]');
  await start.getByLabel("프로젝트 이름").fill(PROJECT.name);
  await start.getByLabel("목적").fill(PROJECT.purpose);
  await start.getByRole("button", { name: "만들기", exact: true }).click();
  await expect(heading(page)).toHaveText(PROJECT.name);
  await idle(page);

  // 2. 데이터 가져오기 from the project, then 프로젝트에 연결 and an explicit save on the project page.
  await page.locator("#project-form").getByRole("button", { name: "CSV·JSON 가져오기", exact: true }).click();
  await expect(heading(page)).toHaveText("데이터 가져오기");
  const source = page.locator('#app [data-tour="data-import"]');
  await source.getByLabel("파일 경로", { exact: true }).fill(String.raw`C:\data\seq-qa.csv`);
  await source.getByRole("button", { name: "가져오기", exact: true }).click();
  await idle(page);
  for (const [label, column] of [["지시문", "question"], ["입력", "context"], ["정답", "answer"]])
    await page.locator("#import-mapping").getByLabel(label, { exact: true }).selectOption(column);
  await page.getByRole("button", { name: "미리보기", exact: true }).click();
  await idle(page);
  const register = page.locator("#import-register-form");
  await register.getByLabel("데이터 ID", { exact: true }).fill(DATASET.dataset_id);
  await register.getByLabel("버전", { exact: true }).fill(DATASET.version);
  await register.getByRole("button", { name: "등록", exact: true }).click();
  await idle(page);
  const link = page.locator("#import-link");
  await link.getByRole("button", { name: "프로젝트에 연결", exact: true }).click();
  await idle(page);
  await link.getByRole("button", { name: "프로젝트에서 저장하기", exact: true }).click();
  await idle(page);
  await expect(heading(page)).toHaveText(PROJECT.name);
  await page.locator("#project-form").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  const projectSaves = await calls(page, { action: "project_save" });
  expect(projectSaves).toHaveLength(1);
  expect(projectSaves[0].args.data.spec).toMatchObject({ ...PROJECT, datasets: [{ binding_id: "train", role: "train", dataset: DATASET }], graph: {} });

  // 3. An SFT LoRA recipe.
  await nav(page, "recipes");
  await idle(page);
  await page.getByRole("button", { name: "새 레시피", exact: true }).click();
  const recipeForm = page.locator("#recipe-form");
  await recipeForm.getByLabel("레시피 이름").fill("SFT LoRA");
  await recipeForm.getByLabel("학습 단계", { exact: true }).selectOption("sft");
  await recipeForm.getByLabel("학습 방법", { exact: true }).selectOption("lora");
  await recipeForm.getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  const [recipeSave] = await calls(page, { action: "recipe_save" });
  expect(recipeSave.args.data.spec).toMatchObject({ name: "SFT LoRA", training_stage: "sft", training_method: "lora", input_kind: "instruction" });

  // 4. The graph page: pin a base model (metadata only), snapshot it after its own approval, then "단일 학습".
  await nav(page, "projects");
  await idle(page);
  await page.locator(".project-list tbody tr", { hasText: PROJECT.name }).getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "학습 그래프", exact: true }).click();
  await expect(heading(page)).toHaveText("학습 그래프");
  await idle(page);
  const roots = page.locator("#model-roots");
  await roots.getByLabel("HF 모델 검색").fill("Qwen2.5 Instruct");
  await roots.getByRole("button", { name: "모델 검색", exact: true }).click();
  await idle(page);
  await roots.getByRole("button", { name: `${PINNED.repo_id} 고르기` }).click();
  await roots.getByRole("button", { name: "버전 확인", exact: true }).click();
  await idle(page);
  await roots.getByRole("button", { name: "설정·토크나이저 받기", exact: true }).click();
  // Confirmed by keyboard: the shell swallows a second pointer click at the same spot within 400 ms.
  await page.getByRole("dialog", { name: "설정·토크나이저 받기" }).getByRole("button", { name: "받기", exact: true }).press("Enter");
  await idle(page);
  expect((await calls(page, { action: "model_root_snapshot" })).map(call => call.args.data))
    .toEqual([{ repo_id: PINNED.repo_id, revision: PINNED_REVISION, download_approved: true }]);
  await page.locator(".graph-starters").getByRole("button", { name: "단일 학습", exact: true }).click();
  await expect(page.locator("#graph-cards [data-card]")).toHaveCount(1);
  return projectSaves[0].args.data.spec.project_id;
}
/** Planning inputs (9.8) kept per project against its saved revision, and a dry plan answering the request's project,
 * revision and mode (10.6 never holds a plan that differs from its request). Recorded by the shared mock first. */
async function installPlanFake(page: Page, plan: object) {
  await page.evaluate(plan => {
    const native = (window as any).__TAURI_INTERNALS__, original = native.invoke;
    const revisions: Record<string, string> = {}, inputs: Record<string, unknown> = {};
    const result = (data: unknown) => ({ status: "ok", issues: [], data });
    native.invoke = async (command: string, args: any) => {
      const answer = await original(command, args);
      if (command !== "workflow_action") return answer;
      const { action, data } = args;
      if ((action === "project_create" || action === "project_save" || action === "project_get") && answer?.status === "ok")
        revisions[answer.data.project.project_id] = answer.data.revision_sha256;
      if (action === "planning_inputs_save") inputs[data.project_id] = { project_revision: data.project_revision, simulation: data.simulation,
        node_time_limits: data.node_time_limits, estimates: data.estimates, saved_at: "2026-10-21T09:00:00+00:00" };
      if (action === "planning_inputs_get" || action === "planning_inputs_save")
        return result({ project_id: data.project_id, current_project_revision: revisions[data.project_id], inputs: inputs[data.project_id] ?? null });
      if (action === "graph_plan")
        return result({ ...structuredClone(plan), project_id: data.project_id, project_revision: data.project_revision, simulation: data.simulation });
      return answer;
    };
  }, plan);
}
/** The workflow actions in their first-call order are `expected`, and no example went into any request. */
async function expectFlow(page: Page, expected: string[]) {
  const order = (await calls(page)).filter(call => call.command === "workflow_action").map(call => call.args.action);
  const firsts = expected.map(action => order.indexOf(action));
  expect(firsts.every(position => position >= 0), `${expected} in ${order}`).toBe(true);
  expect([...firsts].sort((a, b) => a - b)).toEqual(firsts);
  const requests = JSON.stringify((await calls(page)).map(call => call.args ?? null));
  for (const example of ["paper_extraction", "paper_processing"]) expect(requests).not.toContain(example);
  await expect(page.locator("img")).toHaveCount(0);
  await expect(page.locator("form form")).toHaveCount(0);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
}
const FLOW = ["project_create", "dataset_import_source", "dataset_import_preview", "dataset_import_register", "project_save", "recipe_save",
  "model_roots_list", "model_root_snapshot", "graph_validate"];

test("a blank project reaches a saved and validated graph through the screens without examples", async ({ page }) => {
  await blankProjectToGraph(page);
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  expect((await calls(page, { action: "project_save" })).at(-1)!.args.data.spec.graph.nodes).toEqual([TRAIN_NODE]);
  expect(await calls(page, { action: "graph_validate" })).toHaveLength(1);
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
  await expectFlow(page, FLOW);
});

test("a blank project reaches a dry-run plan through the screens without examples", async ({ page }) => {
  const runId = H("7", 32);
  const ESTIMATE = { profile_id: "estimate-a100", gpu_type: "NVIDIA A100", gpu_count: 1, total_hourly_cost_usd: 1.5 };
  const run = { run_id: runId, status: "RUNNING", simulation: true, plan_sha256: H("f"), approved_cost_usd: 0, reserved_cost_usd: 0,
    budget_exhausted: false, unpriced_attempts: 0, created: 1792540800, tracking: { events: {}, graph: "not_started", action: null },
    nodes: [{ node_id: "train-1", label: "학습 1", kind: "train", status: "PENDING", job_id: null, job_status: null, attempt_id: null,
      attempts: 0, counted_attempts: 0, max_attempts: 1, resource_id: null, destination_profile_id: null, uncertain: false,
      awaiting_result: false, result_deadline: null, needs_operator: false, wait_reason: null, error: null, retry_at: null,
      tracking_status: "not_started", registration_status: "none", tracking_action: null, output: null, selection: null, cache_seed: null }] };
  const DRY_PLAN = { plan_sha256: H("f"), simulation: true, project_id: "", project_revision: "", max_estimated_cost_usd: 1.5, max_duration_seconds: 3600,
    max_concurrent_gpu_count: 1, retry_policy: { max_attempts: 1, backoff_seconds: 0 }, tracking: null,
    datasets: [{ ...DATASET, synthetic: false, export_allowed: false }],
    nodes: [{ node_id: "train-1", kind: "train", label: "학습 1", max_estimated_cost_usd: 1.5, max_duration_seconds: 3600, recipe: null, datasets: [],
      allowed: [{ profile_id: ESTIMATE.profile_id, revision: H("a"), resource_id: `estimate:${ESTIMATE.profile_id}`, gpu_type: ESTIMATE.gpu_type,
        gpu_count: 1, total_hourly_cost_usd: 1.5, cost_source: "estimate" }] }] };
  const projectId = await blankProjectToGraph(page, {
    graph_submit: ok({ run_id: runId, status: "RUNNING", simulation: true, plan_sha256: H("f"), controller: absent }),
    // The approval first looks for a run of the same plan (none yet); the runs page then lists the new one.
    graph_runs_list: [ok({ runs: [], controller: absent }), ok({ runs: [{ run_id: runId, status: "RUNNING", simulation: true, plan_sha256: H("f"),
      created: 1792540800, nodes_by_status: { PENDING: 1 } }], controller: absent })],
    graph_run_get: ok({ run, controller: absent, plan: null, tracking_failures: [] }),
  }, DRY_PLAN);
  // 5. An estimate for the dry run, chosen on the card, saved with the graph.
  const panel = page.locator("#plan-panel");
  await panel.getByRole("button", { name: "추정 자원 추가", exact: true }).click();
  const estimate = page.locator("#plan-estimate-form");
  await estimate.getByLabel("추정 자원 이름").fill("a100");
  await estimate.getByLabel("GPU 종류").fill(ESTIMATE.gpu_type);
  await estimate.getByLabel("GPU 수").fill("1");
  await estimate.getByLabel("시간당 총 요금 (USD)").fill("1.5");
  await estimate.getByRole("button", { name: "추가", exact: true }).click();
  await page.locator('#graph-cards [data-node-card="train-1"]').click();
  await page.locator("#node-form").getByRole("group", { name: "실행 프로필" }).getByLabel("계획용 추정 · NVIDIA A100 × 1 · estimate-a100").check();
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  expect((await calls(page, { action: "project_save" })).at(-1)!.args.data.spec.graph.nodes)
    .toEqual([{ ...TRAIN_NODE, executor_profile_ids: [ESTIMATE.profile_id] }]);
  expect(await calls(page, { action: "graph_validate" })).toHaveLength(1);

  // 6. 계획 확인 as a dry run (the default), then 모의 실행 등록 without any approval checkbox.
  await expect(panel.getByRole("radio", { name: /모의 실행 계획/ })).toBeChecked();
  await panel.getByRole("button", { name: "계획 확인", exact: true }).click();
  await idle(page);
  const revision = (await bridge(page, "project_get", { project_id: projectId })).data.revision_sha256;
  expect((await calls(page, { action: "graph_plan" })).map(call => call.args.data)).toEqual([{ project_id: projectId, project_revision: revision,
    simulation: true, node_resources: [{ node_id: "train-1", timeout_seconds: 3600, runtime_profile: "primary" }], estimates: [ESTIMATE] }]);
  await panel.getByRole("button", { name: "계획 승인·실행…", exact: true }).click();
  const dialog = page.getByRole("dialog", { name: "모의 실행 등록" });
  await expect(dialog).toBeVisible();
  await expect(page.locator("dialog")).toHaveCount(1);
  await expect(page.locator("form form, dialog dialog")).toHaveCount(0);
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await dialog.getByRole("button", { name: "모의 실행 등록", exact: true }).click();

  // 7. The runs page shows the registered run; the controller is not started on its own.
  await expect(heading(page)).toHaveText("그래프 실행");
  await idle(page);
  expect((await calls(page, { action: "graph_submit" })).map(call => call.args.data)).toEqual([{ plan_sha256: H("f"),
    request_id: expect.stringMatching(/^[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}$/), approve_execution: false, approve_data_transfer: false }]);
  await expect(page.locator(`#app [data-tour="graph-runs"] tr[data-run-id="${runId}"]`)).toHaveCount(1);
  await expect(page.locator("#run-detail")).toContainText("학습 1");
  expect(await calls(page, { command: "controller_start" })).toEqual([]);
  await expectFlow(page, [...FLOW, "graph_plan", "graph_runs_list", "graph_submit"]);
});
