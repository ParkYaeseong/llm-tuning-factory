// The graph editor page (10.5, D4–D7, D13): cards and preview from one draft, click and keyboard serial/parallel adds
// and input connections, local and validation reasons, metric choices, recipe changes, save and validate, conflicts,
// HF root snapshot/targets approvals, inline benchmarks, shared-destination waiting, form/dialog nesting and the
// 64-card limit. Projects and recipes come from the stateful definitions fake; the other reads are scripted.
import { test, expect, type Page } from "@playwright/test";
import { calls, conflict, HOSTILE, invalid, issue, ok, openWorkbench, type BridgeOptions } from "./workflow-mock.ts";

const H = (c: string) => c.repeat(64);
const ROOT = { repo_id: "Qwen/Qwen2.5-0.5B-Instruct", revision: "a".repeat(40), config_sha256: H("c"), tokenizer_fingerprint: H("d") };
const ROOT_ENTRY = { root: ROOT, snapshot_ready: true, targets_ready: false,
  profile: { repo_id: ROOT.repo_id, revision: ROOT.revision, config_sha256: ROOT.config_sha256, architecture: "qwen2", template: "qwen" } };
const NEW_REPO = "example/Small-Llama", NEW_REVISION = "b".repeat(40);
const NEW_ROOT = { repo_id: NEW_REPO, revision: NEW_REVISION, config_sha256: H("f"), tokenizer_fingerprint: H("0") };
const NEW_ENTRY = { root: NEW_ROOT, snapshot_ready: true, targets_ready: false,
  profile: { repo_id: NEW_REPO, revision: NEW_REVISION, config_sha256: NEW_ROOT.config_sha256, architecture: HOSTILE, template: "llama3" } };
const DATASETS = [
  { binding_id: "train", role: "train", dataset: { dataset_id: "dummy", version: "1", sha256: "5".repeat(64) } },
  { binding_id: "validation", role: "validation", dataset: { dataset_id: "heldout", version: "1", sha256: "6".repeat(64) } }];
const CATALOG_DATASETS = [
  { dataset_id: "dummy", version: "1", rows: 2, export_allowed: true, synthetic: true, kind: "instruction", sha256: "5".repeat(64) },
  { dataset_id: "heldout", version: "1", rows: 20, export_allowed: true, synthetic: true, kind: "instruction", sha256: "6".repeat(64) }];
const PROFILES = [{ profile_id: "pod-a", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: "A100 Pod" } },
  { profile_id: "pod-b", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: `${HOSTILE} Pod` } }];
const benchmark = (revision: string, evaluator: string) => ({ revision_sha256: revision, spec: {
  dataset: DATASETS[1].dataset, evaluator_kind: evaluator, evaluator_version: "1", ordered_sample_ids: ["row:0", "row:1"],
  max_new_tokens: 64, cutoff_len: 512, seed: 1, generation_mode: "greedy", precision: "bf16", quantization_bit: null,
  tokenizer_fingerprint: ROOT.tokenizer_fingerprint, template: "qwen", semantic_model_kind: "causal_lm" } });
const TEXT_BENCHMARK = benchmark(H("7"), "text");
const PIPELINE_BENCHMARK = benchmark(H("e"), "pipeline");
const VALID = ok({ project_id: "validated", project_revision: H("1"), node_count: 1 });
const WAITING = "같은 실행 자원을 써서 순서대로 실행됩니다 (대기)";
const recipeSpec = (recipe_id: string, name: string, training_method: string, training_stage = "sft") => ({ schema_version: 1, recipe_id,
  version: "1", name, description: "", training_backend: "llamafactory", capability_version: "lf-text-v1", training_stage,
  training_method, input_kind: "instruction", quantization_bit: 4, hyperparameters: {}, finetuning_options: {}, stage_options: {},
  evaluation_defaults: {} });
const fromRoot = { policy: { base: ROOT, adapter: null }, initialization: "new_adapter" };
const ref = (node_id: string, output_name = "model") => ({ node_id, output_name });
const trainNode = (id: string, recipe_revision: string, extra: object = {}) => ({ id, kind: "train", label: id, recipe_revision,
  model_input: fromRoot, dataset_binding: "train", executor_profile_ids: ["pod-a"], ...extra });
const evalNode = (id: string, model: string, extra: object = {}) => ({ id, kind: "evaluate", label: id, model: ref(model),
  executor_profile_ids: ["pod-a"], ...extra });
const graphOf = (nodes: object[]) => ({ nodes, max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } });

const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const heading = (page: Page) => page.getByRole("heading", { level: 1 });
const card = (page: Page, id: string) => page.locator(`#graph-cards [data-card="${id}"]`);
const cardButton = (page: Page, id: string) => page.locator(`#graph-cards [data-node-card="${id}"]`);
const cards = (page: Page) => page.locator("#graph-cards [data-card]");
const previewNode = (page: Page, id: string) => page.locator(`#graph-preview svg g[data-node="${id}"]`);
const previewNodes = (page: Page) => page.locator("#graph-preview svg g[data-node]");
const nodeForm = (page: Page) => page.locator("#node-form");
const benchmarkForm = (page: Page) => page.locator("#benchmark-form");
const roots = (page: Page) => page.locator("#model-roots");
const saveButton = (page: Page) => page.locator("#graph-head").getByRole("button", { name: "저장", exact: true });
const bridge = (page: Page, action: string, data: object) =>
  page.evaluate(([action, data]) => (window as any).__TAURI_INTERNALS__.invoke("workflow_action", { action, data }), [action, data] as const);
const workflowActions = async (page: Page, from = 0) =>
  (await calls(page)).slice(from).filter(call => call.command === "workflow_action").map(call => call.args.action);

interface Seeded { projectId: string; revisions: Record<string, string> }
/** Saves recipes and one project with bindings and a graph through the definitions fake (as another window would). */
async function seed(page: Page, options: { name?: string; recipes?: ReturnType<typeof recipeSpec>[];
  graph?: (revisions: Record<string, string>) => object; datasets?: object[]; defaultProfile?: string | null }): Promise<Seeded> {
  const revisions: Record<string, string> = {};
  for (const spec of options.recipes ?? []) {
    const saved = await bridge(page, "recipe_save", { spec, expected_revision: null });
    revisions[spec.recipe_id] = saved.data.revision_sha256;
  }
  const name = options.name ?? "그래프 프로젝트";
  const created = await bridge(page, "project_create", { request_id: `seed-${name.length}-${Date.now()}`, name, purpose: "", task_type: "" });
  const spec = { ...created.data.project, datasets: options.datasets ?? DATASETS, graph: options.graph ? options.graph(revisions) : {},
    default_executor_profile_id: options.defaultProfile ?? null };
  const saved = await bridge(page, "project_save", { spec, expected_revision: created.data.revision_sha256 });
  expect(saved.status).toBe("ok");
  return { projectId: created.data.project.project_id, revisions };
}
/** Opens a project's graph page the way a user does: 프로젝트 → 열기 → 학습 그래프. */
async function openGraph(page: Page, name = "그래프 프로젝트") {
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  await page.locator(".project-list tbody tr", { hasText: name }).getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "학습 그래프", exact: true }).click();
  await expect(heading(page)).toHaveText("학습 그래프");
  await idle(page);
}
async function save(page: Page) {
  await saveButton(page).click();
  await idle(page);
}
const options = (extra: BridgeOptions = {}): BridgeOptions => ({ definitions: true, profiles: PROFILES, ...extra,
  catalog: { datasets: CATALOG_DATASETS, ...(extra.catalog ?? {}) },
  workflow: { model_roots_list: ok({ roots: [ROOT_ENTRY] }), benchmarks_list: ok({ benchmarks: [TEXT_BENCHMARK, PIPELINE_BENCHMARK] }),
    graph_validate: VALID, ...(extra.workflow ?? {}) } });
const LORA = recipeSpec("lora-sft", "기본 SFT", "lora");

test("cards and preview come from the same draft", async ({ page }) => {
  await openWorkbench(page, options());
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  const before = (await calls(page)).length;
  await openGraph(page);
  // Entering reads only: the project, recipes, base models, benchmarks and the native profile list.
  const entered = (await calls(page)).slice(before).map(call => call.command === "workflow_action" ? call.args.action : call.command);
  expect(entered.slice(entered.lastIndexOf("project_get"))).toEqual(["project_get", "recipes_list", "model_roots_list", "benchmarks_list", "executor_profiles_get"]);
  await expect(page.locator('#app [data-tour="graph-editor"]')).toHaveCount(1);
  await expect(cards(page)).toHaveCount(1);
  await expect(previewNodes(page)).toHaveCount(1);
  await expect(previewNode(page, "train-1")).toContainText("기준 학습");
  await expect(page.locator("#graph-preview svg")).toHaveAttribute("aria-label", "학습 그래프 미리보기: 단계 1개, 연결 0개");

  await cardButton(page, "train-1").click();
  await page.getByRole("button", { name: "나란히 추가", exact: true }).click();
  await expect(cards(page)).toHaveCount(2);
  await expect(previewNodes(page)).toHaveCount(2);
  const label = nodeForm(page).getByLabel("단계 이름");
  await expect(label).toBeFocused();
  await label.fill(`${HOSTILE} 비교`);
  await expect(card(page, "train-2")).toContainText(`${HOSTILE} 비교`);
  await expect(previewNode(page, "train-2")).toContainText(`${HOSTILE} 비교`);
  await expect(label).toBeFocused();
  const cardLabels = await page.locator("#graph-cards [data-node-card] .graph-card-label").allTextContents();
  expect(cardLabels).toEqual(["기준 학습", `${HOSTILE} 비교`]);
  expect(await page.locator("#graph-preview svg g[data-node] > title").allTextContents()).toEqual(cardLabels);
  await expect(page.locator("#graph-head")).toContainText("저장 안 된 변경");
  await expect(page.locator("img")).toHaveCount(0);
  // Nothing was saved or validated without "저장".
  expect(await workflowActions(page, before)).not.toContain("project_save");
});

test("serial and parallel cards are added and connected with the keyboard only", async ({ page }) => {
  await openWorkbench(page, options());
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"])]) });
  await openGraph(page);
  // The card list is one tab stop, right after the graph settings.
  await page.locator("#graph-backoff").focus();
  await page.keyboard.press("Tab");
  await expect(cardButton(page, "train-1")).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(cardButton(page, "train-1")).toHaveAttribute("aria-pressed", "true");
  await expect(cardButton(page, "train-1")).toBeFocused();
  // A parallel sibling.
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "다음 단계 추가", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.getByRole("button", { name: "나란히 추가", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(cards(page)).toHaveCount(2);
  const label = nodeForm(page).getByLabel("단계 이름");
  await expect(label).toBeFocused();
  await expect(label).toHaveValue("학습 2");
  // Back in the list (the selected card is its only tab stop), ArrowUp moves to the first card.
  for (let step = 0; step < 12 && !await cardButton(page, "train-2").evaluate(element => element === document.activeElement); step++)
    await page.keyboard.press("Shift+Tab");
  await expect(cardButton(page, "train-2")).toBeFocused();
  await expect(cardButton(page, "train-1")).toHaveAttribute("tabindex", "-1");
  await page.keyboard.press("ArrowUp");
  await expect(cardButton(page, "train-1")).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(cardButton(page, "train-2")).toBeFocused();
  await page.keyboard.press("ArrowUp");
  await page.keyboard.press("Enter");
  await expect(cardButton(page, "train-1")).toHaveAttribute("aria-pressed", "true");
  // A serial evaluation of the first card, its kind chosen with the keyboard.
  await page.keyboard.press("Tab");
  const next = page.getByRole("button", { name: "다음 단계 추가", exact: true });
  await expect(next).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(next).toHaveAttribute("aria-expanded", "true");
  await expect(page.locator("#graph-add-kinds").getByRole("button", { name: "학습", exact: true })).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(page.locator("#graph-add-kinds").getByRole("button", { name: "평가", exact: true })).toBeFocused();
  await page.keyboard.press("Enter");
  await expect(cards(page)).toHaveCount(3);
  await expect(label).toBeFocused();
  await expect(label).toHaveValue("평가 1");
  // Its model input (the first card) is switched to the sibling with the keyboard.
  const model = nodeForm(page).getByLabel("평가할 모델");
  await expect(model).toHaveValue("node:train-1");
  await page.keyboard.press("Tab");
  await expect(model).toBeFocused();
  await page.keyboard.press("ArrowDown");
  await expect(model).toHaveValue("node:train-2");
  await expect(model).toBeFocused();
  await expect(previewNodes(page)).toHaveCount(3);
  await expect(page.locator('#graph-preview [data-edge="train-2>eval-1"]')).toHaveCount(1);
  await save(page);
  const saved = (await calls(page, { action: "project_save" })).at(-1).args.data.spec.graph;
  expect(saved.nodes.map((node: any) => node.id)).toEqual(["train-1", "train-2", "eval-1"]);
  expect(saved.nodes[1].model_input).toEqual(saved.nodes[0].model_input);
  expect(saved.nodes[2]).toEqual({ id: "eval-1", kind: "evaluate", label: "평가 1", model: ref("train-2"),
    benchmark_revision: TEXT_BENCHMARK.revision_sha256, executor_profile_ids: ["pod-a"] });
});

test("unsupported connections show their reasons", async ({ page }) => {
  const validation = invalid([issue("typed_port", "input requires model output", "/graph/nodes/2", { node_id: "eval-1" }),
    issue("parent_transition", "parent transition requires explicit materialize", "/graph/nodes/4", { node_id: "train-2" })]);
  await openWorkbench(page, options({ workflow: { graph_validate: validation } }));
  const oft = recipeSpec("oft-sft", `${HOSTILE} OFT`, "oft"), full = recipeSpec("full-sft", "전체 SFT", "full");
  await seed(page, { recipes: [LORA, oft, full], graph: revisions => graphOf([
    trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }), trainNode("oft-1", revisions["oft-sft"], { label: "OFT 학습" }),
    evalNode("eval-1", "train-1", { label: "평가 1" }), evalNode("eval-2", "train-1", { label: "평가 2" }),
    trainNode("train-2", revisions["full-sft"], { label: "전체 학습", model_input: ref("train-1") })]) });
  await openGraph(page);
  await cardButton(page, "eval-1").click();
  const blocked = nodeForm(page).locator(".graph-blocked");
  await expect(blocked).toContainText("연결할 수 없는 단계");
  await expect(blocked).toContainText("평가 2 · 평가 결과는 모델 입력으로 쓸 수 없습니다");
  await expect(blocked).toContainText("OFT 학습 · OFT 결과는 다음 단계로 넘길 수 없습니다");
  await expect(blocked).toContainText("평가 1 · 연결하면 순환이 생깁니다");
  const model = nodeForm(page).getByLabel("평가할 모델");
  await expect(model.locator('option[value="node:eval-2"]')).toHaveJSProperty("disabled", true);
  await expect(model.locator('option[value="node:oft-1"]')).toHaveJSProperty("disabled", true);
  await expect(model.locator('option[value="node:train-1"]')).toHaveJSProperty("disabled", false);
  // What the draft alone proves is on the cards before any save.
  await expect(card(page, "train-2")).toContainText("학습 방법이 바뀌는 연결은 먼저 ‘전체 모델 확정’ 단계가 필요합니다");
  await expect(card(page, "oft-1")).toContainText("그래프에서는 OFT 학습을 쓸 수 없습니다");

  await save(page);
  expect(await calls(page, { action: "graph_validate" })).toHaveLength(1);
  const typed = card(page, "eval-1");
  await expect(typed).toContainText("이 입력에는 모델 출력이 필요합니다.");
  await expect(typed.locator("details")).toContainText("원문 보기");
  await expect(typed.locator("details .issue-original")).toHaveText("input requires model output");
  const transition = card(page, "train-2");
  await expect(transition).toContainText("이전 단계와 학습 방식이 달라 먼저 모델 변환(materialize) 단계를 넣어야 합니다.");
  await expect(transition.locator("details .issue-original")).toHaveText("parent transition requires explicit materialize");
  await expect(card(page, "train-1").locator(".issue-list")).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
});

test("selection cards explain metric choices and that text metrics are not semantic quality", async ({ page }) => {
  await openWorkbench(page, options());
  const selection = { metric: "exact_match", direction: "max", thresholds: [], allow_partial: false, min_success: 2, merge_regression_tolerance: 0 };
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), trainNode("train-2", revisions["lora-sft"]),
    evalNode("eval-1", "train-1", { benchmark_revision: TEXT_BENCHMARK.revision_sha256 }),
    evalNode("eval-2", "train-2", { benchmark_revision: TEXT_BENCHMARK.revision_sha256 }),
    { id: "select-1", kind: "select", label: "최종 선택", evaluations: [ref("eval-1", "evaluation"), ref("eval-2", "evaluation")], selection }]) });
  await openGraph(page);
  await cardButton(page, "select-1").click();
  const metric = nodeForm(page).getByLabel("선택 지표");
  await expect(metric).toHaveValue("exact_match");
  await expect(metric.locator('option[value="exact_match"]')).toHaveJSProperty("disabled", false);
  await expect(metric.locator('option[value="character_f1"]')).toHaveJSProperty("disabled", false);
  await expect(nodeForm(page)).toContainText("텍스트 정답 일치 지표 · 의미적 품질 점수가 아닙니다");
  await expect(nodeForm(page)).toContainText("자동 선택은 추천 결과 확정이며 배포하지 않습니다. 텍스트 정답 일치 지표는 의미적 품질을 보장하지 않습니다.");
  await expect(nodeForm(page).getByLabel("최소 성공 수")).toHaveValue("2");
  await expect(nodeForm(page).getByLabel("병합 회귀 허용치")).toHaveValue("0");
  // One input moves to a benchmark without metrics.
  await cardButton(page, "eval-2").click();
  await nodeForm(page).getByLabel("평가 기준").selectOption(PIPELINE_BENCHMARK.revision_sha256);
  await expect(nodeForm(page).getByLabel("평가 기준")).toBeFocused();
  await cardButton(page, "select-1").click();
  await expect(metric.locator('option[value="exact_match"]')).toHaveJSProperty("disabled", true);
  await expect(nodeForm(page)).toContainText("평가 지표가 없어 자동 선택에 쓸 수 없습니다");
  await expect(card(page, "select-1")).toContainText("서로 다른 평가 기준은 비교할 수 없습니다");
  await expect(page.locator("img")).toHaveCount(0);
});

test("applying a recipe keeps the card's data and profiles", async ({ page }) => {
  await openWorkbench(page, options());
  const named = recipeSpec("lora-sft", `${HOSTILE} 기본 SFT`, "lora");
  const other = recipeSpec("other-sft", "다른 SFT", "lora");
  const seeded = await seed(page, { recipes: [named, other], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"],
    { label: "기준 학습", overrides: {}, executor_profile_ids: ["pod-a", "pod-b"] })]) });
  const oldRevision = seeded.revisions["lora-sft"];
  // The recipe moves on after the card pinned it: the card keeps its pinned (older) revision.
  const moved = await bridge(page, "recipe_save", { spec: { ...named, description: "개정" }, expected_revision: oldRevision });
  const currentRevision = moved.data.revision_sha256;
  await page.evaluate(([oldRevision, spec]) => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      const answer = await original(command, args);
      if (command === "workflow_action" && args.action === "recipe_get" && args.data?.revision_sha256 === oldRevision)
        return { status: "ok", issues: [], data: { recipe: spec, revision_sha256: oldRevision } };
      return answer;
    };
  }, [oldRevision, named] as const);
  await openGraph(page);
  expect((await calls(page, { action: "recipe_get" })).map(call => call.args.data)).toEqual([{ revision_sha256: oldRevision }]);
  await cardButton(page, "train-1").click();
  const recipe = nodeForm(page).getByLabel("레시피");
  await expect(recipe).toHaveValue(oldRevision);
  await expect(recipe.locator("option:checked")).toContainText(`고정된 이전 버전 · ${oldRevision.slice(0, 8)}`);
  await expect(recipe.locator("option:checked")).toContainText(`${HOSTILE} 기본 SFT`);
  const stored = (await bridge(page, "project_get", { project_id: seeded.projectId })).data.project.graph.nodes[0];

  await nodeForm(page).getByRole("button", { name: "현재 버전으로 바꾸기" }).click();
  await expect(recipe).toHaveValue(currentRevision);
  await save(page);
  let node = (await calls(page, { action: "project_save" })).at(-1).args.data.spec.graph.nodes[0];
  expect(node).toEqual({ ...stored, recipe_revision: currentRevision });

  await recipe.selectOption(seeded.revisions["other-sft"]);
  await expect(recipe).toBeFocused();
  await save(page);
  node = (await calls(page, { action: "project_save" })).at(-1).args.data.spec.graph.nodes[0];
  expect(node).toEqual({ ...stored, recipe_revision: seeded.revisions["other-sft"] });
  expect(node.dataset_binding).toBe("train");
  expect(node.executor_profile_ids).toEqual(["pod-a", "pod-b"]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("saving validates the saved revision and maps issues to cards and fields", async ({ page }) => {
  const validation = invalid([issue("recipe_missing", "이 단계의 레시피 버전을 찾을 수 없습니다.", "/graph/nodes/0/recipe_revision", { node_id: "train-1" }),
    issue("dataset", "dataset snapshot is missing", "/datasets")]);
  await openWorkbench(page, options({ workflow: { graph_validate: validation } }));
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), evalNode("eval-1", "train-1")]) });
  await openGraph(page);
  await cardButton(page, "train-1").click();
  await nodeForm(page).getByLabel("단계 이름").fill(`${HOSTILE} 기준`);
  const from = (await calls(page)).length;
  await save(page);
  expect(await workflowActions(page, from)).toEqual(["project_save", "graph_validate"]);
  const current = await bridge(page, "project_get", { project_id: projectId });
  expect((await calls(page, { action: "graph_validate" })).at(-1).args.data).toEqual({ project_id: projectId, revision: current.data.revision_sha256 });
  expect(current.data.project.graph.nodes[0].label).toBe(`${HOSTILE} 기준`);
  const recipe = nodeForm(page).getByLabel("레시피");
  await expect(recipe).toHaveAttribute("aria-invalid", "true");
  await expect(nodeForm(page).locator(".graph-field:has(#node-recipe)")).toContainText("이 단계의 레시피 버전을 찾을 수 없습니다.");
  await expect(card(page, "train-1")).toContainText("이 단계의 레시피 버전을 찾을 수 없습니다.");
  await expect(card(page, "eval-1").locator(".issue-list")).toHaveCount(0);
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
  const link = page.getByRole("button", { name: "프로젝트에서 데이터 연결 확인" });
  await expect(page.locator(".graph-summary")).toContainText("데이터 스냅샷을 찾을 수 없습니다.");
  await expect(link).toHaveAttribute("data-action", `nav:project:${projectId}`);
  await link.click();
  await expect(heading(page)).toHaveText("그래프 프로젝트");
  await expect(page.locator("img")).toHaveCount(0);
});

test("a stale graph save keeps the draft", async ({ page }) => {
  await openWorkbench(page, options());
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"])]) });
  await openGraph(page);
  await cardButton(page, "train-1").click();
  await nodeForm(page).getByLabel("단계 이름").fill("내 변경");
  // Another window saves the project first.
  const current = await bridge(page, "project_get", { project_id: projectId });
  const other = await bridge(page, "project_save", { spec: { ...current.data.project, purpose: "다른 창" }, expected_revision: current.data.revision_sha256 });
  expect(other.status).toBe("ok");
  await save(page);
  const banner = page.locator(".wf-banner");
  await expect(banner).toContainText("다른 곳에서 먼저 저장한 최신 버전이 있습니다.");
  await expect(banner.getByRole("button", { name: "최신 버전 불러오기" })).toBeVisible();
  await expect(banner.getByRole("button", { name: "내 변경으로 덮어쓰기" })).toBeVisible();
  await expect(card(page, "train-1")).toContainText("내 변경");
  await expect(nodeForm(page).getByLabel("단계 이름")).toHaveValue("내 변경");
  await expect(page.locator("#graph-head")).toContainText("저장 안 된 변경");
  expect(await calls(page, { action: "graph_validate" })).toEqual([]);
  // Overwriting saves the draft against the latest revision, then validates it.
  await banner.getByRole("button", { name: "내 변경으로 덮어쓰기" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "덮어쓰기" }).click();
  await idle(page);
  const latest = await bridge(page, "project_get", { project_id: projectId });
  expect(latest.data.project.graph.nodes[0].label).toBe("내 변경");
  expect(await calls(page, { action: "graph_validate" })).toHaveLength(1);
});

test("base model snapshot and module inspection need separate approvals", async ({ page }) => {
  await openWorkbench(page, options({ workflow: {
    model_roots_list: [ok({ roots: [ROOT_ENTRY] }), ok({ roots: [ROOT_ENTRY, NEW_ENTRY] }), ok({ roots: [{ ...ROOT_ENTRY, targets_ready: true }, NEW_ENTRY] })],
    model_root_snapshot: ok({ root: NEW_ROOT, profile: NEW_ENTRY.profile, files: ["config.json", "tokenizer.json"] }),
    model_root_targets: [{ reject: "허용하지 않은 작업" }, ok({ root: ROOT, shards: ["model.safetensors"], module_count: 7, targets: ["q_proj"], evidence_sha256: H("3") })] } }));
  await page.evaluate(([repo, revision, hostile]) => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      const answer = await original(command, args);
      if (command === "factory_action" && args.action === "model_search") return { models: [{ id: repo, downloads: 12, pipeline_tag: hostile }] };
      if (command === "factory_action" && args.action === "model_inspect")
        return { repo_id: args.data.model, revision, architecture: "llama", supported: true, reasons: [], metadata_verified: true };
      return answer;
    };
  }, [NEW_REPO, NEW_REVISION, HOSTILE] as const);
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"])]) });
  await openGraph(page);
  const panel = roots(page);
  await expect(panel.locator(`[data-root="${ROOT.repo_id}@${ROOT.revision}"]`)).toContainText("설정·토크나이저 받음");
  await expect(panel.locator(`[data-root="${ROOT.repo_id}@${ROOT.revision}"]`)).toContainText("모듈 구조 확인 전");
  await expect(panel).toContainText("Hugging Face에서 메타데이터만 조회합니다");
  // The train card on a root without module evidence links to the inspection.
  await expect(card(page, "train-1")).toContainText("기반 모델의 모듈 구조를 아직 확인하지 않았습니다");
  await expect(card(page, "train-1").getByRole("button", { name: "모듈 구조 확인" })).toHaveCount(1);

  // Pin a new model: search, pick, check its version (metadata only).
  await panel.getByLabel("HF 모델 검색").fill("small llama");
  await panel.getByRole("button", { name: "모델 검색", exact: true }).click();
  await idle(page);
  expect((await calls(page, { action: "model_search" })).map(call => call.args.data)).toEqual([{ query: "small llama", sort: "downloads" }]);
  await panel.getByRole("button", { name: `${NEW_REPO} 고르기` }).click();
  await expect(panel.getByLabel("모델 ID")).toHaveValue(NEW_REPO);
  await panel.getByRole("button", { name: "버전 확인", exact: true }).click();
  await idle(page);
  expect((await calls(page, { action: "model_inspect" })).map(call => call.args.data)).toEqual([{ model: NEW_REPO }]);
  await expect(panel).toContainText(NEW_REVISION);
  const fetch = panel.getByRole("button", { name: "설정·토크나이저 받기", exact: true });
  await fetch.click();
  const dialog = page.getByRole("dialog", { name: "설정·토크나이저 받기" });
  await expect(dialog).toContainText("가중치는 받지 않습니다");
  await expect(dialog).toContainText("128 MiB");
  await expect(dialog).toContainText(NEW_REPO);
  await page.keyboard.press("Escape");
  await expect(dialog).toHaveCount(0);
  await expect(fetch).toBeFocused();
  expect(await calls(page, { action: "model_root_snapshot" })).toEqual([]);
  await fetch.click();
  // Confirmed by keyboard: the shell swallows a second pointer click at the same spot within 400 ms (a repeat).
  await page.getByRole("dialog").getByRole("button", { name: "받기", exact: true }).press("Enter");
  await idle(page);
  expect((await calls(page, { action: "model_root_snapshot" })).map(call => call.args.data)).toEqual([{ repo_id: NEW_REPO, revision: NEW_REVISION, download_approved: true }]);
  await expect(panel.locator(`[data-root="${NEW_REPO}@${NEW_REVISION}"]`)).toContainText("설정·토크나이저 받음");
  expect(await calls(page, { action: "model_root_targets" })).toEqual([]);

  // Module inspection is its own approval, after a snapshot.
  const inspect = panel.locator(`[data-root="${ROOT.repo_id}@${ROOT.revision}"]`).getByRole("button", { name: /모듈 구조 확인/ });
  await inspect.click();
  const targets = page.getByRole("dialog", { name: "모듈 구조 확인" });
  await expect(targets).toContainText("safetensors 헤더만");
  await expect(targets).toContainText("64 MiB");
  await page.keyboard.press("Escape");
  await expect(targets).toHaveCount(0);
  await expect(inspect).toBeFocused();
  expect(await calls(page, { action: "model_root_targets" })).toEqual([]);
  await inspect.click();
  await targets.getByRole("button", { name: "확인", exact: true }).press("Enter");
  await idle(page);
  // Today's native refusal is shown verbatim.
  await expect(page.locator(".notice.error")).toHaveText("허용하지 않은 작업");
  await inspect.click();
  await page.getByRole("dialog").getByRole("button", { name: "확인", exact: true }).press("Enter");
  await idle(page);
  expect((await calls(page, { action: "model_root_targets" })).map(call => call.args.data)).toEqual([
    { repo_id: ROOT.repo_id, revision: ROOT.revision, download_approved: true }, { repo_id: ROOT.repo_id, revision: ROOT.revision, download_approved: true }]);
  await expect(panel.locator(`[data-root="${ROOT.repo_id}@${ROOT.revision}"]`)).toContainText("모듈 구조 확인됨");
  await expect(card(page, "train-1")).not.toContainText("기반 모델의 모듈 구조를 아직 확인하지 않았습니다");
  await expect(page.locator("img")).toHaveCount(0);
});

test("a benchmark is created inline from a registered binding and a root", async ({ page }) => {
  const expected = { dataset: DATASETS[1].dataset, evaluator_kind: "text", evaluator_version: "1",
    ordered_sample_ids: Array.from({ length: 10 }, (_, index) => `row:${index}`), max_new_tokens: 256, cutoff_len: 1024, seed: 42,
    generation_mode: "greedy", precision: "bf16", quantization_bit: null, tokenizer_fingerprint: ROOT.tokenizer_fingerprint,
    template: "qwen", semantic_model_kind: "causal_lm" };
  const created = H("4");
  await openWorkbench(page, options({ workflow: { benchmarks_list: ok({ benchmarks: [] }),
    benchmark_save: [invalid([issue("benchmark_unsupported", "v2 evaluation supports greedy causal-LM generation benchmarks only", "/spec")]),
      ok({ revision_sha256: created, spec: expected })] } }));
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), evalNode("eval-1", "train-1", { label: `${HOSTILE} 평가` })]) });
  await openGraph(page);
  await cardButton(page, "eval-1").click();
  await nodeForm(page).getByRole("button", { name: "새 평가 기준" }).click();
  const form = benchmarkForm(page);
  await expect(form).toBeVisible();
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(form.getByLabel("평가 데이터")).toBeFocused();
  await expect(form.getByLabel("평가 데이터")).toHaveValue("validation");
  await expect(form.getByLabel("평가 데이터").locator('option[value="train"]')).toHaveJSProperty("disabled", true);
  await expect(form).toContainText("평가에는 검증·시험(validation·test) 데이터만 쓸 수 있습니다");
  await expect(form).toContainText("20행");
  await form.getByLabel("샘플 수").fill("10");
  await form.getByRole("button", { name: "평가 기준 저장" }).click();
  await idle(page);
  expect((await calls(page, { action: "benchmark_save" })).map(call => call.args.data)).toEqual([{ spec: expected }]);
  await expect(form).toContainText("이 벤치마크 설정은 평가 엔진에서 쓸 수 없습니다.");
  await expect(form.locator(".issue-original")).toHaveText("v2 evaluation supports greedy causal-LM generation benchmarks only");
  await form.getByRole("button", { name: "평가 기준 저장" }).click();
  await idle(page);
  expect(await calls(page, { action: "benchmark_save" })).toHaveLength(2);
  await expect(benchmarkForm(page)).toHaveCount(0);
  const chosen = nodeForm(page).getByLabel("평가 기준");
  await expect(chosen).toHaveValue(created);
  await expect(chosen).toBeFocused();
  await save(page);
  expect((await calls(page, { action: "project_save" })).at(-1).args.data.spec.graph.nodes[1].benchmark_revision).toBe(created);
  await expect(page.locator("img")).toHaveCount(0);
});

test("parallel cards on one execution profile are marked as waiting", async ({ page }) => {
  await openWorkbench(page, options());
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), trainNode("train-2", revisions["lora-sft"])]) });
  await openGraph(page);
  await expect(card(page, "train-1")).toContainText(WAITING);
  await expect(card(page, "train-2")).toContainText(WAITING);
  await expect(previewNode(page, "train-1")).toContainText(WAITING);
  await expect(previewNode(page, "train-2")).toContainText(WAITING);
  await cardButton(page, "train-2").click();
  const profiles = nodeForm(page).getByRole("group", { name: "실행 프로필" });
  await expect(profiles.getByLabel(`${HOSTILE} Pod`)).not.toBeChecked();
  await profiles.getByLabel("A100 Pod").uncheck();
  await profiles.getByLabel(`${HOSTILE} Pod`).check();
  await expect(page.locator("#app")).not.toContainText(WAITING);
  await expect(page.locator("#graph-preview")).not.toContainText(WAITING);
  await expect(page.locator("img")).toHaveCount(0);
});

test("the graph editor never nests forms or dialogs and stops at 64 cards", async ({ page }) => {
  await openWorkbench(page, options({ workflow: { model_roots_list: ok({ roots: [{ ...ROOT_ENTRY, snapshot_ready: false }] }) } }));
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), evalNode("eval-1", "train-1", { label: HOSTILE })]) });
  await seed(page, { name: "큰 그래프", recipes: [], graph: () => graphOf(Array.from({ length: 64 }, (_, index) => ({ id: `train-${index + 1}`, kind: "train", label: `학습 ${index + 1}` }))) });
  await openGraph(page);
  await cardButton(page, "eval-1").click();
  await nodeForm(page).getByRole("button", { name: "새 평가 기준" }).click();
  await expect(benchmarkForm(page)).toBeVisible();
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("#node-form, #benchmark-form")).toHaveCount(2);
  // A snapshot dialog is the only dialog: Ctrl+K opens nothing on top of it.
  await roots(page).getByRole("button", { name: `${ROOT.repo_id} 설정·토크나이저 받기` }).click();
  await expect(page.getByRole("dialog")).toHaveCount(1);
  await page.keyboard.press("Control+K");
  await expect(page.locator("dialog")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog")).toHaveCount(0);
  expect(await calls(page, { action: "model_root_snapshot" })).toEqual([]);

  await openGraph(page, "큰 그래프");
  await expect(cards(page)).toHaveCount(64);
  await cardButton(page, "train-1").click();
  for (const name of ["다음 단계 추가", "나란히 추가", "새 학습 단계"])
    await expect(page.locator("#graph-toolbar").getByRole("button", { name, exact: true })).toBeDisabled();
  await expect(page.locator("#graph-toolbar")).toContainText("단계는 최대 64개입니다");
  await expect(page.locator("#graph-toolbar").getByRole("button", { name: "단계 삭제", exact: true })).toBeEnabled();
  await expect(page.locator("img")).toHaveCount(0);
});

test("saving the graph is not submitted twice", async ({ page }) => {
  await openWorkbench(page, options({ holds: ["project_save"] }));
  const recipe = await bridge(page, "recipe_save", { spec: LORA, expected_revision: null });
  await bridge(page, "project_create", { request_id: "seed-19", name: "그래프 프로젝트", purpose: "", task_type: "" });
  await openGraph(page);
  // An empty graph offers the four starting shapes.
  const starters = page.locator(".graph-starters");
  for (const name of ["단일 학습", "직렬 추가 학습", "병렬 비교", "병렬 비교 후 병합"])
    await expect(starters.getByRole("button", { name, exact: true })).toBeVisible();
  await starters.getByRole("button", { name: "단일 학습", exact: true }).click();
  await expect(cards(page)).toHaveCount(1);
  await expect(nodeForm(page).getByLabel("단계 이름")).toBeFocused();
  await saveButton(page).dblclick();
  await expect.poll(() => page.evaluate(() => (window as any).__held("project_save"))).toBe(1);
  expect(await calls(page, { action: "project_save" })).toHaveLength(1);
  await expect(saveButton(page)).toBeDisabled();
  await page.evaluate(() => (window as any).__release("project_save"));
  await idle(page);
  expect(await calls(page, { action: "project_save" })).toHaveLength(1);
  expect(await calls(page, { action: "graph_validate" })).toHaveLength(1);
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
  const graph = (await calls(page, { action: "project_save" }))[0].args.data.spec.graph;
  // No binding or default profile exists yet: those fields stay absent (the first root and recipe are filled in).
  expect(graph).toEqual(graphOf([{ id: "train-1", kind: "train", label: "학습 1", recipe_revision: recipe.data.revision_sha256, model_input: fromRoot }]));
});

// -- 10.5 review fixes (M1, M2, M4, nits) --
test("a new card never takes the ID of a removed saved card or its check results", async ({ page }) => {
  const validation = invalid([issue("recipe_missing", "이 단계의 레시피 버전을 찾을 수 없습니다.", "/graph/nodes/1/recipe_revision", { node_id: "train-2" })]);
  await openWorkbench(page, options({ workflow: { graph_validate: validation } }));
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]), trainNode("train-2", revisions["lora-sft"])]) });
  await openGraph(page);
  await save(page);
  await expect(card(page, "train-2")).toContainText("이 단계의 레시피 버전을 찾을 수 없습니다.");
  await cardButton(page, "train-2").click();
  await page.locator("#graph-toolbar").getByRole("button", { name: "단계 삭제", exact: true }).click();
  await cardButton(page, "train-1").click();
  await page.getByRole("button", { name: "나란히 추가", exact: true }).click();
  await expect(cards(page)).toHaveCount(2);
  await expect(card(page, "train-2")).toHaveCount(0);
  await expect(card(page, "train-3")).not.toContainText("레시피 버전을 찾을 수 없습니다");
});

test("evaluation choices stop at 256 connections", async ({ page }) => {
  await openWorkbench(page, options());
  const evals = () => Array.from({ length: 16 }, (_, index) => evalNode(`eval-${index + 1}`, "train-1",
    { label: `평가 ${index + 1}`, benchmark_revision: TEXT_BENCHMARK.revision_sha256 }));
  const select = (index: number, count: number) => ({ id: `select-${index}`, kind: "select", label: `선택 ${index}`,
    evaluations: Array.from({ length: count }, (_, item) => ref(`eval-${item + 1}`, "evaluation")) });
  // 16 evaluation inputs + 14 × 16 + 15 selections + 1 dependency = 256 connections.
  await seed(page, { recipes: [LORA], graph: revisions => ({ ...graphOf([trainNode("train-1", revisions["lora-sft"]), ...evals(),
    ...Array.from({ length: 14 }, (_, index) => select(index + 1, 16)), select(15, 15)]), depends_on: [{ node_id: "select-1", depends_on: "train-1" }] }) });
  await openGraph(page);
  await cardButton(page, "select-15").click();
  const unchosen = nodeForm(page).locator('[data-node-evaluation="eval-16"]');
  await expect(unchosen).toBeDisabled();
  await expect(nodeForm(page).locator('label:has([data-node-evaluation="eval-16"])')).toContainText("그래프 연결은 최대 256개입니다");
  await expect(nodeForm(page).locator('[data-node-evaluation="eval-15"]')).toBeEnabled();
  await expect(page.locator("#graph-toolbar")).toContainText("그래프 연결은 최대 256개입니다");
});

test("focus stays on the page after the last card goes and after a model check", async ({ page }) => {
  await openWorkbench(page, options({ workflow: {
    model_roots_list: [ok({ roots: [{ ...ROOT_ENTRY, snapshot_ready: false }] }), ok({ roots: [ROOT_ENTRY] }), ok({ roots: [{ ...ROOT_ENTRY, targets_ready: true }] })],
    model_root_snapshot: ok({ root: ROOT, profile: ROOT_ENTRY.profile, files: ["config.json"] }),
    model_root_targets: ok({ root: ROOT, shards: ["model.safetensors"], module_count: 1, targets: ["q_proj"], evidence_sha256: H("e") }) } }));
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"])]) });
  await openGraph(page);
  await cardButton(page, "train-1").click();
  await page.locator("#graph-toolbar").getByRole("button", { name: "단계 삭제", exact: true }).click();
  await expect(page.locator(".graph-starters button").first()).toBeFocused();
  // After a snapshot the root's next step has the focus; after its module check, the root itself.
  await roots(page).getByRole("button", { name: `${ROOT.repo_id} 설정·토크나이저 받기` }).click();
  await page.getByRole("dialog").getByRole("button", { name: "받기", exact: true }).press("Enter");
  await idle(page);
  await expect(roots(page).getByRole("button", { name: `${ROOT.repo_id} 모듈 구조 확인` })).toBeFocused();
  await page.keyboard.press("Enter");
  await page.getByRole("dialog").getByRole("button", { name: "확인", exact: true }).press("Enter");
  await idle(page);
  await expect(roots(page).locator(`[data-root="${ROOT.repo_id}@${ROOT.revision}"]`)).toBeFocused();
});

test("a threshold waits for the selection metric", async ({ page }) => {
  await openWorkbench(page, options());
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"]),
    evalNode("eval-1", "train-1", { benchmark_revision: TEXT_BENCHMARK.revision_sha256 }),
    { id: "select-1", kind: "select", label: "선택", evaluations: [ref("eval-1", "evaluation")], selection: { direction: "max", thresholds: [] } }]) });
  await openGraph(page);
  await cardButton(page, "select-1").click();
  await expect(nodeForm(page).getByLabel("기준값 (선택)")).toBeDisabled();
  await expect(nodeForm(page)).toContainText("선택 지표를 고른 뒤 입력할 수 있습니다");
  await nodeForm(page).getByLabel("선택 지표").selectOption("exact_match");
  await expect(nodeForm(page).getByLabel("기준값 (선택)")).toBeEnabled();
});
