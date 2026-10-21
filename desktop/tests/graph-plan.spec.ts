// Plan and approval (10.6; D5, D8, D9 and the root decision on planning inputs): planning inputs kept by 9.8's
// planning_inputs_get/_save, "계획 확인" (graph_plan) with issues and warnings by section, plan validity, the approval
// dialog with separate approvals, graph_submit with a reused request ID, plan_stale and the run page after a submit.
// Projects and recipes come from the stateful definitions fake; planning inputs and plans from a per-test fake below.
import { test, expect, type Page } from "@playwright/test";
import { calls, conflict, HOSTILE, invalid, issue, ok, openWorkbench, type BridgeOptions } from "./workflow-mock.ts";

const H = (c: string) => c.repeat(64);
const ROOT = { repo_id: "Qwen/Qwen2.5-0.5B-Instruct", revision: "a".repeat(40), config_sha256: H("c"), tokenizer_fingerprint: H("d") };
const ROOT_ENTRY = { root: ROOT, snapshot_ready: true, targets_ready: false,
  profile: { repo_id: ROOT.repo_id, revision: ROOT.revision, config_sha256: ROOT.config_sha256, architecture: "qwen2", template: "qwen" } };
const DATASETS = [
  { binding_id: "train", role: "train", dataset: { dataset_id: "dummy", version: "1", sha256: H("5") } },
  { binding_id: "train-2", role: "train", dataset: { dataset_id: "extra", version: "1", sha256: H("8") } },
  { binding_id: "validation", role: "validation", dataset: { dataset_id: "heldout", version: "1", sha256: H("6") } }];
const CATALOG_DATASETS = DATASETS.map(binding => ({ ...binding.dataset, rows: 20, export_allowed: true, synthetic: true, kind: "instruction" }));
const PROFILES = [{ profile_id: "pod-a", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: "A100 Pod" } },
  { profile_id: "pod-b", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: `${HOSTILE} Pod` } }];
const ESTIMATE = { profile_id: "estimate-a100", gpu_type: "NVIDIA A100", gpu_count: 1, total_hourly_cost_usd: 1.5 };
const MLFLOW = "https://mlflow.kbiofoundry.kr";
const VALID = ok({ project_id: "validated", project_revision: H("1"), node_count: 1 });
const RUN = "a".repeat(32);

// Backend texts (workflow_planning.py, workflow_runs.py, desktop.py, auth.py) and the UI texts of the plan.
const OBSERVATION_STALE = "실제 실행 계획에는 1시간 이내에 확인한 자원 정보가 필요합니다. 실행 프로필에서 자원을 다시 확인하세요.";
const OBSERVATION_REVISION_DIFFERS = "자원을 확인한 뒤 실행 프로필이 바뀌었습니다. 실행 프로필에서 자원을 다시 확인하세요.";
const ESTIMATE_REAL = "계획용 추정 자원은 모의 실행 계획에만 쓸 수 있습니다.";
const NODE_RESOURCE_MISSING = "이 단계의 최대 실행 시간을 정하세요.";
const INSECURE_TRACKING = "인증값을 사용하는 MLflow 서버 주소는 HTTPS여야 합니다. 인증 없는 로컬·내부 HTTP 서버는 토큰과 OIDC 값을 비우거나 앱에서 ‘인증 없음’을 선택하세요.";
const BASE_TARGETS_MISSING = "이 단계는 기반 모델의 모듈 구조 정보가 필요하지만 아직 지원하지 않습니다(이어 학습·병합·전체 모델 확정). 기반 모델을 바꾸거나 이 단계를 빼고 다시 계획하세요.";
const SIMULATED_NEEDS_TARGETS = "모의 실행에는 기반 모델의 모듈 구조 정보가 필요합니다(lora_target: all 또는 전체·freeze 학습). 계획은 만들 수 있지만 모의 실행은 이 단계에서 멈춥니다. 대상 모듈을 직접 고르면 모의 실행할 수 있습니다.";
const PRACTICE_REFUSAL = "연습용 데이터(tutorial_sample)로는 실제 학습을 시작할 수 없습니다.";
const EXPORT_NOT_ALLOWED = "이 PC 밖으로 보낼 수 있다고 표시하지 않은 데이터는 실제 실행에 쓸 수 없습니다.";
const PLAN_STALE = "실행 계획 이후 프로젝트·실행 프로필·MLflow 설정이 바뀌었습니다. 다시 계획하세요.";
const COST_NOTE = "재시도를 포함한 최대 추정치입니다. 제공자 청구 상한이 아니며 저장·대기 비용은 별도입니다.";
const WARNING_CHANGED = "자원 확인 후 프로필이 바뀌었습니다 · 모의 계획은 확인 당시 자원으로 계산했습니다";
const DROPPED = "변경되어 다시 계획해야 합니다";
const WAIT = "대기 후 순차 실행";

const recipeSpec = (recipe_id: string, name: string, training_method: string, training_stage = "sft") => ({ schema_version: 1, recipe_id,
  version: "1", name, description: "", training_backend: "llamafactory", capability_version: "lf-text-v1", training_stage,
  training_method, input_kind: "instruction", quantization_bit: 4, hyperparameters: {}, finetuning_options: {}, stage_options: {},
  evaluation_defaults: {} });
const LORA = recipeSpec("lora-sft", "기본 SFT", "lora");
const PPO = recipeSpec("ppo-rl", "보상 학습", "lora", "ppo");
const fromRoot = { policy: { base: ROOT, adapter: null }, initialization: "new_adapter" };
const ref = (node_id: string, output_name = "model") => ({ node_id, output_name });
const trainNode = (id: string, recipe_revision: string, extra: object = {}) => ({ id, kind: "train", label: id, recipe_revision,
  model_input: fromRoot, dataset_binding: "train", executor_profile_ids: ["pod-a"], ...extra });
const evalNode = (id: string, model: string, extra: object = {}) => ({ id, kind: "evaluate", label: id, model: ref(model),
  executor_profile_ids: ["pod-a"], ...extra });
const graphOf = (nodes: object[]) => ({ nodes, max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } });

// Plan summaries (workflow_planning.plan_summary with 9.8's node references); the fake fills in the request's
// project, revision and mode.
const resource = (profile_id: string, extra: object = {}) => ({ profile_id, revision: H("a"), resource_id: `runpod:${profile_id}`,
  gpu_type: "NVIDIA A100", gpu_count: 1, total_hourly_cost_usd: 1.89, cost_source: "runpod_api", ...extra });
const ESTIMATED = resource("estimate-a100", { resource_id: "estimate:estimate-a100", total_hourly_cost_usd: 1.5, cost_source: "estimate" });
const planNode = (node_id: string, kind: string, label: string, allowed: object[], extra: object = {}) => ({ node_id, kind, label,
  max_estimated_cost_usd: 3.78, max_duration_seconds: 3600, allowed, recipe: null, datasets: [], ...extra });
const summary = (nodes: object[], extra: object = {}) => ({ plan_sha256: H("f"), simulation: true, project_id: "", project_revision: "",
  max_estimated_cost_usd: 7.56, max_duration_seconds: 7200, max_concurrent_gpu_count: 1, retry_policy: { max_attempts: 2, backoff_seconds: 0 },
  nodes, datasets: [{ dataset_id: "dummy", version: "1", sha256: H("5"), synthetic: true, export_allowed: true }], tracking: null, ...extra });
const ONE_STEP = ok(summary([planNode("train-1", "train", "기준 학습", [resource("pod-a")])]));
const TRACKING = { profile_id: "mlflow", public_settings_sha256: H("b"),
  settings: { tracking_uri: MLFLOW, experiment: "llm-tuning-factory", artifact_location: null, auth_mode: "oidc" } };
const controllerView = (state: "running" | "stale" | "absent") =>
  ({ owner_id: state === "absent" ? null : "owner-1", state, heartbeat_age_seconds: state === "absent" ? null : 1 });
const submitted = (simulation: boolean, state: "running" | "absent" = "running") =>
  ok({ run_id: RUN, status: "RUNNING", simulation, plan_sha256: H("f"), controller: controllerView(state) });
const stored = (extra: object = {}) => ({ project_revision: H("0"), simulation: true, node_time_limits: [], estimates: [],
  saved_at: "2026-10-20T09:00:00+00:00", ...extra });

const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const heading = (page: Page) => page.getByRole("heading", { level: 1 });
const card = (page: Page, id: string) => page.locator(`#graph-cards [data-card="${id}"]`);
const cardButton = (page: Page, id: string) => page.locator(`#graph-cards [data-node-card="${id}"]`);
const nodeForm = (page: Page) => page.locator("#node-form");
const panel = (page: Page) => page.locator("#plan-panel");
const planButton = (page: Page) => panel(page).getByRole("button", { name: "계획 확인", exact: true });
const approveButton = (page: Page) => panel(page).getByRole("button", { name: "계획 승인·실행…", exact: true });
const limit = (page: Page, id: string) => panel(page).locator(`tr[data-plan-node="${id}"] input`);
const mode = (page: Page, name: "모의 실행 계획" | "실제 실행 계획") => panel(page).getByRole("radio", { name: new RegExp(name) });
const dropped = (page: Page) => panel(page).locator(".plan-dropped");
const bridge = (page: Page, action: string, data: object) =>
  page.evaluate(([action, data]) => (window as any).__TAURI_INTERNALS__.invoke("workflow_action", { action, data }), [action, data] as const);
const dataOf = async (page: Page, action: string) => (await calls(page, { action })).map(call => call.args.data);

/**
 * Planning inputs (9.8) and plans answered in the page. Inputs are kept per project with planning_inputs_save's CAS
 * on the project revision and its node check (non-select nodes of the saved graph); `__storeInputs` writes them as
 * another window would. graph_plan answers come from `plans` in order (the last repeats); an `ok` takes the
 * request's project, revision and mode. Every call reaches the shared mock first, so it is recorded.
 */
async function installPlanFake(page: Page, options: { stored?: object | null; plans?: object[]; sortKeys?: boolean } = {}) {
  await page.evaluate(({ initial, plans, sortKeys }) => {
    const w = window as any;
    const original = w.__TAURI_INTERNALS__.invoke;
    const sorted = (value: any): any => Array.isArray(value) ? value.map(sorted) : value && typeof value === "object"
      ? Object.fromEntries(Object.keys(value).sort().map(key => [key, sorted(value[key])])) : value;
    const projects: Record<string, any> = {};
    const recipes: Record<string, any> = {};
    const records: Record<string, any> = {};
    let position = 0;
    w.__storeInputs = (projectId: string, record: unknown) => { records[projectId] = record; };
    w.__setPlans = (list: any[]) => { plans = list; position = 0; };
    const result = (status: string, data: unknown, issues: unknown[] = []) => ({ status, issues, data });
    w.__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      const answer = await original(command, args);
      if (command !== "workflow_action") return answer;
      const { action, data } = args;
      // `sortKeys`: projects come back as the backend stores them (projects._json_object: sorted keys).
      if (sortKeys && ["project_create", "project_save", "project_get"].includes(action) && answer?.status === "ok") answer.data.project = sorted(answer.data.project);
      if ((action === "project_create" || action === "project_save") && answer?.status === "ok") projects[answer.data.project.project_id] = answer.data;
      // 9.8's recipe_get by revision hash alone (the definitions fake needs the recipe ID), from the revisions saved.
      if (action === "recipe_save" && answer?.status === "ok") recipes[answer.data.revision_sha256] = answer.data;
      if (action === "recipe_get" && typeof data.revision_sha256 === "string" && !data.recipe_id)
        return recipes[data.revision_sha256] ? result("ok", structuredClone(recipes[data.revision_sha256]))
          : result("invalid", null, [{ code: "unknown_definition", path: "/revision_sha256", message: "unknown definition revision" }]);
      if (action === "planning_inputs_get" || action === "planning_inputs_save") {
        const project = projects[data.project_id];
        if (!project) return result("invalid", null, [{ code: "unknown_definition", path: "/project_id", message: "unknown definition" }]);
        const view = () => result("ok", { project_id: data.project_id, current_project_revision: project.revision_sha256,
          inputs: structuredClone(data.project_id in records ? records[data.project_id] : initial) });
        if (action === "planning_inputs_get") return view();
        if (data.project_revision !== project.revision_sha256)
          return result("conflict", null, [{ code: "revision_conflict", path: "/project_revision", message: "계획 입력을 저장하기 전에 프로젝트가 바뀌었습니다. 최신 프로젝트를 불러온 뒤 다시 저장하세요." }]);
        const nodes: any[] = Array.isArray(project.project.graph?.nodes) ? project.project.graph.nodes : [];
        const planned = new Set(nodes.filter(node => node.kind !== "select").map(node => node.id));
        const unknown = data.node_time_limits.flatMap((item: any, index: number) => planned.has(item.node_id) ? []
          : [{ code: "node_resource_unknown", path: `/node_time_limits/${index}/node_id`, message: "그래프에 없는 단계나 선택 단계의 실행 시간입니다." }]);
        if (unknown.length) return result("invalid", null, unknown);
        records[data.project_id] = { project_revision: data.project_revision, simulation: data.simulation,
          node_time_limits: data.node_time_limits, estimates: data.estimates, saved_at: "2026-10-21T09:00:00+00:00" };
        return view();
      }
      if (action === "graph_plan" && plans.length) {
        const next = structuredClone(plans[Math.min(position++, plans.length - 1)]);
        // `as_sent` keeps the template's own project, revision and mode (an answer that differs from the request).
        if (next.status === "ok" && !next.data.as_sent) Object.assign(next.data, { project_id: data.project_id, project_revision: data.project_revision, simulation: data.simulation });
        if (next.status === "ok") delete next.data.as_sent;
        return next;
      }
      return answer;
    };
  }, { initial: options.stored ?? null, plans: options.plans ?? [], sortKeys: options.sortKeys ?? false });
}

/** Saves recipes and one project with bindings and a graph through the definitions fake (as another window would). */
async function seed(page: Page, options: { recipes?: ReturnType<typeof recipeSpec>[]; graph: (revisions: Record<string, string>) => object; datasets?: object[] }) {
  const revisions: Record<string, string> = {};
  for (const spec of options.recipes ?? []) revisions[spec.recipe_id] = (await bridge(page, "recipe_save", { spec, expected_revision: null })).data.revision_sha256;
  const created = await bridge(page, "project_create", { request_id: `seed-${Date.now()}`, name: "계획 프로젝트", purpose: "", task_type: "" });
  const spec = { ...created.data.project, datasets: options.datasets ?? DATASETS, graph: options.graph(revisions) };
  const saved = await bridge(page, "project_save", { spec, expected_revision: created.data.revision_sha256 });
  expect(saved.status).toBe("ok");
  return { projectId: created.data.project.project_id as string, revisions };
}
async function currentRevision(page: Page, projectId: string): Promise<string> {
  return (await bridge(page, "project_get", { project_id: projectId })).data.revision_sha256;
}
/** Opens the project's graph page the way a user does: 프로젝트 → 열기 → 학습 그래프. */
async function openGraph(page: Page) {
  await page.locator('nav [data-action="nav:projects"]').click();
  await idle(page);
  await page.locator(".project-list tbody tr", { hasText: "계획 프로젝트" }).getByRole("button", { name: /열기/ }).click();
  await idle(page);
  await page.locator("#project-form").getByRole("button", { name: "학습 그래프", exact: true }).click();
  await expect(heading(page)).toHaveText("학습 그래프");
  await idle(page);
}
async function plan(page: Page) {
  await planButton(page).click();
  await idle(page);
}
/** No run exists yet unless a test says otherwise (the approval first looks for a run of the same plan). */
const NO_RUNS = ok({ runs: [], controller: { owner_id: null, state: "absent", heartbeat_age_seconds: null } });
const options = (extra: BridgeOptions = {}): BridgeOptions => ({ definitions: true, profiles: PROFILES, ...extra,
  catalog: { datasets: CATALOG_DATASETS, ...(extra.catalog ?? {}) },
  workflow: { model_roots_list: ok({ roots: [ROOT_ENTRY] }), benchmarks_list: ok({ benchmarks: [] }), graph_validate: VALID, graph_runs_list: NO_RUNS,
    ...(extra.workflow ?? {}) } });

test("a dry run is the default plan and needs no transfer approval", async ({ page }) => {
  await openWorkbench(page, options({ workflow: { graph_submit: submitted(true) } }));
  await installPlanFake(page, { stored: stored({ estimates: [ESTIMATE] }),
    plans: [ok(summary([planNode("train-1", "train", `${HOSTILE} 학습`, [ESTIMATED])]))] });
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([
    trainNode("train-1", revisions["lora-sft"], { label: `${HOSTILE} 학습`, executor_profile_ids: ["estimate-a100"] })]) });
  await openGraph(page);
  await expect(mode(page, "모의 실행 계획")).toBeChecked();
  await expect(limit(page, "train-1")).toHaveValue("60");
  await expect(panel(page).locator('[data-plan-estimate="estimate-a100"]')).toContainText("NVIDIA A100");
  await plan(page);
  expect(await dataOf(page, "graph_plan")).toEqual([{ project_id: projectId, project_revision: await currentRevision(page, projectId),
    simulation: true, node_resources: [{ node_id: "train-1", timeout_seconds: 3600, runtime_profile: "primary" }], estimates: [ESTIMATE] }]);
  await expect(panel(page).locator(".plan-summary")).toContainText("모의 실행 계획");
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "모의 실행 등록" });
  await expect(dialog).toContainText("모의 실행");
  await expect(dialog).toContainText(`${HOSTILE} 학습`);
  await expect(dialog.getByRole("checkbox")).toHaveCount(0);
  await dialog.getByRole("button", { name: "모의 실행 등록", exact: true }).click();
  await expect(heading(page)).toHaveText("그래프 실행");
  // The request ID is derived from the plan hash (Task 11.1), so every window and restart sends the same one.
  expect(await dataOf(page, "graph_submit")).toEqual([{ plan_sha256: H("f"), request_id: `plan-${H("f")}`,
    approve_execution: false, approve_data_transfer: false }]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("a real plan shows destinations, data and maximum cost before two separate approvals", async ({ page }) => {
  const real = summary([
    planNode("train-1", "train", "기준 학습", [resource("pod-a")], { recipe: { recipe_id: "lora-sft", version: "1", name: "기본 SFT",
      training_stage: "sft", training_method: "lora", revision_sha256: H("9") },
      datasets: [{ binding_id: "train", benchmark_revision: null, dataset_id: "dummy", version: "1", sha256: H("5") }] }),
    planNode("eval-1", "evaluate", `${HOSTILE} 평가`, [resource("pod-a")])], {
    datasets: [{ dataset_id: "dummy", version: "1", sha256: H("5"), synthetic: true, export_allowed: true },
      { dataset_id: "heldout", version: "2", sha256: H("6"), synthetic: false, export_allowed: true }], tracking: TRACKING });
  await openWorkbench(page, options({ settings: { MLFLOW_TRACKING_URI: MLFLOW },
    workflow: { graph_submit: [{ reject: "엔진 응답이 끊겼습니다." }, submitted(false)] } }));
  await installPlanFake(page, { plans: [ok(real)] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }),
    evalNode("eval-1", "train-1", { label: `${HOSTILE} 평가` })]) });
  await openGraph(page);
  await mode(page, "실제 실행 계획").check();
  await expect(mode(page, "실제 실행 계획")).toBeFocused();
  await plan(page);
  const [request] = await dataOf(page, "graph_plan");
  expect(request).toMatchObject({ simulation: false, estimates: [], node_resources: [
    { node_id: "train-1", timeout_seconds: 3600, runtime_profile: "primary" }, { node_id: "eval-1", timeout_seconds: 3600, runtime_profile: "primary" }] });
  await expect(panel(page).locator(".plan-summary")).toContainText("실제 실행 계획");
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "실행 승인" });
  for (const text of ["A100 Pod", "pod-a", "NVIDIA A100 ×1", "$1.89", "RunPod API", "dummy", "heldout", "v2", "55555555", "66666666",
    "합성", "사용자", "전송 허용", MLFLOW, "$7.56", COST_NOTE, "2시간", H("f"), `${HOSTILE} 평가`, "기본 SFT"])
    await expect(dialog).toContainText(text);
  await expect(dialog).not.toContainText(H("5"));
  await expect(dialog.locator('[data-plan-total="gpus"]')).toContainText("1");
  const confirm = dialog.getByRole("button", { name: "승인하고 실행 등록", exact: true });
  const execution = dialog.getByRole("checkbox", { name: /^실행 승인/ });
  const transfer = dialog.getByRole("checkbox", { name: /^데이터 전송 승인/ });
  await expect(confirm).toBeDisabled();
  await execution.check();
  await expect(confirm).toBeDisabled();
  await execution.uncheck();
  await transfer.check();
  await expect(confirm).toBeDisabled();
  await execution.check();
  await expect(confirm).toBeEnabled();
  await confirm.click();
  await idle(page);
  // A bridge error keeps the plan and its request ID; the retry is the same request.
  await expect(page.locator(".notice.error")).toContainText("엔진 응답이 끊겼습니다.");
  await expect(heading(page)).toHaveText("학습 그래프");
  await approveButton(page).click();
  await dialog.getByRole("checkbox", { name: /^실행 승인/ }).check();
  await dialog.getByRole("checkbox", { name: /^데이터 전송 승인/ }).check();
  // The keyboard: the shell swallows a pointer click at the previous dialog's spot within 400 ms.
  await dialog.getByRole("button", { name: "승인하고 실행 등록", exact: true }).press("Enter");
  await expect(heading(page)).toHaveText("그래프 실행");
  const sent = await dataOf(page, "graph_submit");
  expect(sent).toHaveLength(2);
  expect(sent[0]).toEqual({ plan_sha256: H("f"), request_id: expect.any(String), approve_execution: true, approve_data_transfer: true });
  expect(sent[1]).toEqual(sent[0]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("planning issues point to profiles, estimates, MLflow and cards", async ({ page }) => {
  const blocked = invalid([
    issue("observation_stale", OBSERVATION_STALE, "/profiles/pod-a"),
    issue("estimate_real", ESTIMATE_REAL, "/estimates/0"),
    issue("tracking_insecure", INSECURE_TRACKING, "/tracking"),
    issue("node_resource_missing", NODE_RESOURCE_MISSING, "/node_resources", { node_id: "eval-1" }),
    issue("base_targets_missing", BASE_TARGETS_MISSING, "/graph/nodes/0", { node_id: "train-1" })],
    { warnings: [issue("observation_revision_differs", OBSERVATION_REVISION_DIFFERS, "/profiles/pod-b")] });
  const warned = ok(summary([planNode("train-1", "train", "기준 학습", [resource("pod-a")]), planNode("eval-1", "evaluate", "평가", [ESTIMATED])]), [
    issue("observation_revision_differs", OBSERVATION_REVISION_DIFFERS, "/profiles/pod-a"),
    issue("simulated_execution_needs_targets", SIMULATED_NEEDS_TARGETS, "/graph/nodes/0", { node_id: "train-1" })]);
  await openWorkbench(page, options({ settings: { MLFLOW_TRACKING_URI: "http://mlflow.internal" } }));
  await installPlanFake(page, { stored: stored({ estimates: [ESTIMATE, { ...ESTIMATE, profile_id: "estimate-spare" }] }), plans: [blocked, warned] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }),
    evalNode("eval-1", "train-1", { label: "평가", executor_profile_ids: ["estimate-a100"] })]) });
  await openGraph(page);
  await mode(page, "실제 실행 계획").check();
  await plan(page);
  // A real plan sends only the estimate a card uses, so its refusal lands on that estimate.
  expect((await dataOf(page, "graph_plan"))[0].estimates).toEqual([ESTIMATE]);
  await expect(panel(page).locator('[data-plan-profile="pod-a"]')).toContainText(OBSERVATION_STALE);
  await expect(panel(page).locator('[data-plan-estimate="estimate-a100"]')).toContainText(ESTIMATE_REAL);
  await expect(panel(page).locator('[data-plan-estimate="estimate-spare"]')).not.toContainText(ESTIMATE_REAL);
  await expect(panel(page).locator("[data-plan-tracking]")).toContainText(INSECURE_TRACKING);
  await expect(panel(page).locator('tr[data-plan-node="eval-1"]')).toContainText(NODE_RESOURCE_MISSING);
  await expect(panel(page).locator('tr[data-plan-node="train-1"]')).not.toContainText(NODE_RESOURCE_MISSING);
  await expect(card(page, "train-1")).toContainText(BASE_TARGETS_MISSING);
  await expect(card(page, "eval-1")).not.toContainText(NODE_RESOURCE_MISSING);
  // The blocked plan's warning is shown too, at its profile (a profile no card uses, with an escaped name).
  await expect(panel(page).locator('[data-plan-profile="pod-b"]')).toContainText(WARNING_CHANGED);
  await expect(panel(page).locator('[data-plan-profile="pod-b"]')).toContainText(`${HOSTILE} Pod`);
  await expect(approveButton(page)).toHaveCount(0);

  // A dry run with both warnings is held: warnings never block.
  await mode(page, "모의 실행 계획").check();
  await plan(page);
  await expect(panel(page).locator('[data-plan-profile="pod-a"]')).toContainText(WARNING_CHANGED);
  await expect(panel(page).locator('[data-plan-profile="pod-a"]')).not.toContainText(OBSERVATION_STALE);
  await expect(card(page, "train-1")).toContainText(SIMULATED_NEEDS_TARGETS);
  await expect(card(page, "train-1")).not.toContainText(BASE_TARGETS_MISSING);
  await expect(card(page, "train-1").getByRole("button", { name: "모듈 구조 확인" })).toHaveCount(1);
  await expect(approveButton(page)).toBeEnabled();
  await expect(page.locator("img")).toHaveCount(0);
});

test("a changed draft, profile, recipe, input or setting blocks the shown plan", async ({ page }) => {
  await openWorkbench(page, options({ native: { executor_profile_save: { profile: PROFILES[0], affected_runs: [] } },
    workflow: { observations_list: ok({ observations: [] }) } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  const held = async () => {
    await plan(page);
    await expect(approveButton(page)).toBeEnabled();
    await expect(dropped(page)).toHaveCount(0);
  };
  const blocked = async (reason: string) => {
    await expect(approveButton(page)).toHaveCount(0);
    await expect(dropped(page)).toContainText(DROPPED);
    await expect(dropped(page)).toContainText(reason);
  };

  // (a) a card label edit
  await held();
  await cardButton(page, "train-1").click();
  const label = nodeForm(page).getByLabel("단계 이름");
  await label.fill("고친 이름");
  await blocked("프로젝트 초안이 바뀌었습니다");
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("먼저 저장하세요");
  await label.fill("기준 학습");                         // back to the saved draft
  await expect(planButton(page)).toBeEnabled();

  // (f) a planning input: a value out of range already drops the plan, then a valid one is planned again
  await held();
  await limit(page, "train-1").fill("0");
  await blocked("계획 입력이 바뀌었습니다");
  await expect(planButton(page)).toBeDisabled();
  expect(await calls(page, { action: "graph_plan" })).toHaveLength(2);
  await limit(page, "train-1").fill("90");
  await expect(limit(page, "train-1")).toBeFocused();
  await held();
  await limit(page, "train-1").fill("95");
  await blocked("계획 입력이 바뀌었습니다");

  // (b) a profile save on the profiles page
  await held();
  await page.locator('nav [data-action="nav:profiles"]').click();
  await idle(page);
  await page.locator('#app [data-tour="executor-profiles"] tbody tr').first().getByRole("button", { name: /편집/ }).click();
  await page.locator("#profile-form").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  await openGraph(page);
  await blocked("실행 프로필이 바뀌었습니다");

  // (c) a new revision of the recipe the plan used, saved on the recipes page
  await held();
  await page.locator('nav [data-action="nav:recipes"]').click();
  await idle(page);
  await page.getByRole("button", { name: "기본 SFT 열기" }).click();
  await idle(page);
  await page.locator("#recipe-form").getByLabel("설명").fill("다른 설명");
  await page.locator("#recipe-form").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  await openGraph(page);
  await blocked("계획에 쓴 레시피가 바뀌었습니다");

  // (d) a changed data binding
  await held();
  await cardButton(page, "train-1").click();
  await nodeForm(page).getByLabel("학습 데이터").selectOption("train-2");
  await blocked("프로젝트 초안이 바뀌었습니다");
  await nodeForm(page).getByLabel("학습 데이터").selectOption("train");

  // (e) saving the connection settings
  await held();
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await idle(page);
  await openGraph(page);
  await blocked("연결 설정이 바뀌었습니다");
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
});

test("a stale plan at submit keeps the draft and asks to plan again", async ({ page }) => {
  const stale = conflict([issue("plan_stale", PLAN_STALE, "/profiles/pod-a"), issue("plan_stale", PLAN_STALE, "/tracking")]);
  await openWorkbench(page, options({ settings: { MLFLOW_TRACKING_URI: MLFLOW }, workflow: { graph_submit: stale } }));
  await installPlanFake(page, { plans: [ok(summary([planNode("train-1", "train", "기준 학습", [resource("pod-a")])], { tracking: TRACKING }))] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await cardButton(page, "train-1").click();
  await mode(page, "실제 실행 계획").check();
  await plan(page);
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "실행 승인" });
  await dialog.getByRole("checkbox", { name: /^실행 승인/ }).check();
  await dialog.getByRole("checkbox", { name: /^데이터 전송 승인/ }).check();
  await dialog.getByRole("button", { name: "승인하고 실행 등록", exact: true }).click();
  await idle(page);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(heading(page)).toHaveText("학습 그래프");
  await expect(dropped(page)).toContainText(DROPPED);
  await expect(dropped(page)).toContainText("pod-a");
  await expect(dropped(page)).toContainText("MLflow 설정");
  await expect(approveButton(page)).toHaveCount(0);
  // The draft is kept as it was.
  await expect(nodeForm(page).getByLabel("단계 이름")).toHaveValue("기준 학습");
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
  await dropped(page).getByRole("button", { name: "다시 계획", exact: true }).click();
  await idle(page);
  expect(await calls(page, { action: "graph_plan" })).toHaveLength(2);
  await expect(approveButton(page)).toBeEnabled();
  expect(await calls(page, { action: "graph_submit" })).toHaveLength(1);
});

test("parallel steps on one destination are shown as waiting in the plan", async ({ page }) => {
  const shared = ok(summary([planNode("train-1", "train", "학습 A", [resource("pod-a")]), planNode("train-2", "train", "학습 B", [resource("pod-a")])], { plan_sha256: H("1") }));
  const distinct = ok(summary([planNode("train-1", "train", "학습 A", [resource("pod-a")]), planNode("train-2", "train", "학습 B", [resource("pod-b")])], { plan_sha256: H("2") }));
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [shared, distinct] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "학습 A" }),
    trainNode("train-2", revisions["lora-sft"], { label: "학습 B" })]) });
  await openGraph(page);
  await plan(page);
  const steps = panel(page).locator(".plan-summary");
  await expect(steps.locator('[data-plan-step="train-2"]')).toContainText(WAIT);
  await expect(steps.locator('[data-plan-step="train-1"]')).not.toContainText(WAIT);
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "모의 실행 등록" });
  await expect(dialog.locator('[data-plan-step="train-2"]')).toContainText(WAIT);
  await expect(dialog.locator('[data-plan-step="train-1"]')).not.toContainText(WAIT);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  // Distinct destinations: nothing waits.
  await plan(page);
  await expect(panel(page).locator(".plan-summary")).toBeVisible();
  await expect(panel(page).locator(".plan-summary")).not.toContainText(WAIT);
  await approveButton(page).click();
  await expect(dialog).toBeVisible();
  await expect(dialog).not.toContainText(WAIT);
  await page.keyboard.press("Escape");
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
});

test("the approval dialog keeps focus, closes on Escape and never submits twice", async ({ page }) => {
  await openWorkbench(page, options({ holds: ["graph_submit"], workflow: { graph_submit: submitted(true) } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  await approveButton(page).click();
  const dialog = page.getByRole("dialog", { name: "모의 실행 등록" });
  await expect(dialog.getByRole("heading", { name: "모의 실행 등록" })).toBeFocused();
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(approveButton(page)).toBeFocused();
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
  await approveButton(page).click();
  await dialog.getByRole("button", { name: "모의 실행 등록", exact: true }).dblclick();
  await expect.poll(() => page.evaluate(() => (window as any).__held("graph_submit"))).toBe(1);
  expect(await calls(page, { action: "graph_submit" })).toHaveLength(1);
  await expect(approveButton(page)).toBeDisabled();
  await page.evaluate(() => (window as any).__release("graph_submit"));
  await expect(heading(page)).toHaveText("그래프 실행");
  expect(await calls(page, { action: "graph_submit" })).toHaveLength(1);
});

test("after submit the run opens and starting the controller stays explicit", async ({ page }) => {
  const node = { node_id: "train-1", label: "기준 학습", kind: "train", status: "PENDING", job_id: null, job_status: null, attempt_id: null,
    attempts: 0, counted_attempts: 0, max_attempts: 2, resource_id: null, destination_profile_id: "pod-a", uncertain: false, awaiting_result: false,
    result_deadline: null, needs_operator: false, wait_reason: null, error: null, retry_at: null, tracking_status: "not_started",
    registration_status: "none", tracking_action: null, output: null, selection: null, cache_seed: null };
  const run = { run_id: RUN, status: "RUNNING", simulation: true, plan_sha256: H("f"), approved_cost_usd: 7.56, reserved_cost_usd: 0,
    budget_exhausted: false, unpriced_attempts: 0, created: 1792540800,
    tracking: { events: {}, graph: "not_started", action: null }, nodes: [node] };
  // The list before the approval has no run of this plan; the runs page then lists the new one.
  await openWorkbench(page, options({ workflow: { graph_submit: submitted(true, "absent"),
    graph_runs_list: [NO_RUNS, ok({ runs: [{ run_id: RUN, status: "RUNNING", simulation: true, plan_sha256: H("f"), created: 1792540800, nodes_by_status: { PENDING: 1 } }],
      controller: controllerView("absent") })],
    graph_run_get: ok({ run, controller: controllerView("absent"), plan: null, tracking_failures: [] }),
    controller_status: ok({ controller: controllerView("absent"), current: null, broker: null, stop_requested: false,
      restart_required: { profiles: [], tracking: false }, runs: { active: 1 } }) } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  await approveButton(page).click();
  await page.getByRole("dialog").getByRole("button", { name: "모의 실행 등록", exact: true }).click();
  await expect(heading(page)).toHaveText("그래프 실행");
  await idle(page);
  await expect(page.locator("#run-detail")).toBeVisible();
  expect(await dataOf(page, "graph_run_get")).toContainEqual({ run_id: RUN });
  const start = page.locator("#controller-bar").getByRole("button", { name: "컨트롤러 시작", exact: true });
  await expect(start).toBeEnabled();
  const notice = page.locator(".notice").filter({ hasText: "실행을 등록했습니다." });
  await expect(notice).toContainText("컨트롤러 시작");
  expect(await calls(page, { command: "controller_start" })).toEqual([]);
});

test("practice data plans only as a dry run", async ({ page }) => {
  const practice = invalid([issue("practice_data", PRACTICE_REFUSAL, "/graph/nodes/0/dataset_binding", { node_id: "train-1" })]);
  const tutorial = [{ binding_id: "train", role: "train", dataset: { dataset_id: "tutorial_sample", version: "1", sha256: H("7") } }];
  await openWorkbench(page, options({ settings: { MLFLOW_TRACKING_URI: MLFLOW },
    catalog: { datasets: [{ dataset_id: "tutorial_sample", version: "1", sha256: H("7"), rows: 6, export_allowed: false, synthetic: true, kind: "instruction" }] } }));
  await installPlanFake(page, { plans: [practice, ONE_STEP] });
  await seed(page, { datasets: tutorial, recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "연습 학습" })]) });
  await openGraph(page);
  await mode(page, "실제 실행 계획").check();
  await plan(page);
  await expect(approveButton(page)).toHaveCount(0);
  await expect(card(page, "train-1")).toContainText(PRACTICE_REFUSAL);
  await cardButton(page, "train-1").click();
  const data = nodeForm(page).locator(".graph-field:has(#node-dataset)");
  await expect(data).toContainText(PRACTICE_REFUSAL);
  await expect(nodeForm(page).locator("#node-dataset")).toHaveAttribute("aria-invalid", "true");
  await mode(page, "모의 실행 계획").check();
  await plan(page);
  await expect(approveButton(page)).toBeEnabled();
  expect((await dataOf(page, "graph_plan")).map(request => request.simulation)).toEqual([false, true]);
  await expect(data).not.toContainText(PRACTICE_REFUSAL);
});

test("planning inputs are restored, saved with the plan and never name removed cards", async ({ page }) => {
  await openWorkbench(page, options());
  await installPlanFake(page, { stored: stored({ node_time_limits: [{ node_id: "train-1", timeout_minutes: 120 }, { node_id: "gone-1", timeout_minutes: 30 }] }),
    plans: [ONE_STEP] });
  const { projectId } = await seed(page, { recipes: [LORA, PPO], graph: revisions => graphOf([
    trainNode("train-1", revisions["lora-sft"], { label: `${HOSTILE} 기준` }), evalNode("eval-1", "train-1", { label: "평가" }),
    trainNode("ppo-1", revisions["ppo-rl"], { label: "보상 학습", model_input: ref("train-1") }),
    { id: "select-1", kind: "select", label: "선택", evaluations: [ref("eval-1", "evaluation")] }]) });
  const before = (await calls(page)).length;
  await openGraph(page);
  // Entering reads the stored inputs (before the project, so 10.5's reads after project_get stay as they were).
  const entered = (await calls(page)).slice(before).filter(call => call.command === "workflow_action").map(call => call.args.action);
  expect(entered.filter(action => action === "planning_inputs_get")).toHaveLength(1);
  expect(entered.lastIndexOf("planning_inputs_get")).toBeLessThan(entered.lastIndexOf("project_get"));
  await expect(limit(page, "train-1")).toHaveValue("120");
  await expect(limit(page, "eval-1")).toHaveValue("60");
  await expect(panel(page).locator('tr[data-plan-node="ppo-1"]')).toContainText("ppo");
  await expect(panel(page).locator('tr[data-plan-node="select-1"]')).toHaveCount(0);
  await expect(panel(page).locator('tr[data-plan-node="train-1"]')).toContainText(`${HOSTILE} 기준`);
  await limit(page, "eval-1").fill("90");
  await plan(page);
  const revision = await currentRevision(page, projectId);
  expect(await dataOf(page, "planning_inputs_save")).toEqual([{ project_id: projectId, project_revision: revision, simulation: true,
    node_time_limits: [{ node_id: "train-1", timeout_minutes: 120 }, { node_id: "eval-1", timeout_minutes: 90 }], estimates: [] }]);
  expect((await dataOf(page, "graph_plan"))[0].node_resources).toEqual([{ node_id: "train-1", timeout_seconds: 7200, runtime_profile: "primary" },
    { node_id: "eval-1", timeout_seconds: 5400, runtime_profile: "primary" }, { node_id: "ppo-1", timeout_seconds: 3600, runtime_profile: "ppo" }]);
  const order = (await calls(page)).filter(call => call.command === "workflow_action").map(call => call.args.action);
  expect(order.lastIndexOf("planning_inputs_save")).toBeLessThan(order.lastIndexOf("graph_plan"));

  // A removed card's time limit is never sent: remove eval-1, save the graph and plan again.
  await cardButton(page, "eval-1").click();
  await page.locator("#graph-toolbar").getByRole("button", { name: "단계 삭제", exact: true }).click();
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  await expect(panel(page).locator('tr[data-plan-node="eval-1"]')).toHaveCount(0);
  await limit(page, "train-1").fill("150");
  await plan(page);
  const saves = await dataOf(page, "planning_inputs_save");
  expect(saves).toHaveLength(2);
  expect(saves[1]).toMatchObject({ project_revision: await currentRevision(page, projectId), node_time_limits: [{ node_id: "train-1", timeout_minutes: 150 }] });

  // Inputs another window saved are shown when the page is entered again (this session changed nothing since).
  await page.evaluate(([projectId, record]) => (window as any).__storeInputs(projectId, record),
    [projectId, stored({ simulation: false, node_time_limits: [{ node_id: "train-1", timeout_minutes: 45 }] })] as const);
  await page.locator('nav [data-action="nav:projects"]').click();
  await openGraph(page);
  await expect(limit(page, "train-1")).toHaveValue("45");
  await expect(mode(page, "실제 실행 계획")).toBeChecked();
  // A change of this session that no plan has saved yet is never replaced by the stored inputs.
  await limit(page, "train-1").fill("50");
  await page.locator('nav [data-action="nav:projects"]').click();
  await openGraph(page);
  await expect(limit(page, "train-1")).toHaveValue("50");
  expect(await calls(page, { action: "planning_inputs_save" })).toHaveLength(2);
  await expect(page.locator("img")).toHaveCount(0);
});

test("estimates are added beside the inputs, offered to cards, and bad inputs block planning", async ({ page }) => {
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await panel(page).getByRole("button", { name: "추정 자원 추가", exact: true }).click();
  const form = page.locator("#plan-estimate-form");
  await expect(form.getByLabel("추정 자원 이름")).toBeFocused();
  await expect(page.locator("form form")).toHaveCount(0);
  await form.getByLabel("추정 자원 이름").fill("a 100!");
  await form.getByLabel("GPU 종류").fill("NVIDIA A100");
  await form.getByLabel("GPU 수").fill("1");
  await form.getByLabel("시간당 총 요금 (USD)").fill("1.5");
  await form.getByRole("button", { name: "추가", exact: true }).click();
  await expect(form).toContainText("영문·숫자");
  await form.getByLabel("추정 자원 이름").fill("a100");
  await form.getByRole("button", { name: "추가", exact: true }).click();
  await expect(form).toHaveCount(0);
  await expect(panel(page).locator('[data-plan-estimate="estimate-a100"]')).toBeFocused();
  // The card can use it; the draft is then unsaved and planning waits for a save.
  await cardButton(page, "train-1").click();
  await nodeForm(page).getByRole("group", { name: "실행 프로필" }).getByLabel("계획용 추정 · NVIDIA A100 × 1 · estimate-a100").check();
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("먼저 저장하세요");
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  await expect(planButton(page)).toBeEnabled();
  // A label typed on the card updates the panel in place, and its inputs keep working without a full render.
  await nodeForm(page).getByLabel("단계 이름").fill(`${HOSTILE} 2`);
  await expect(panel(page).locator('tr[data-plan-node="train-1"]')).toContainText(`${HOSTILE} 2`);
  // An out-of-range time limit blocks planning.
  await limit(page, "train-1").fill("0");
  await expect(panel(page).locator(".plan-blockers")).toContainText("1–10080분");
  await nodeForm(page).getByLabel("단계 이름").fill("기준 학습");
  await expect(limit(page, "train-1")).toHaveValue("0");
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("1–10080분");
  await limit(page, "train-1").fill("10080");
  await expect(planButton(page)).toBeEnabled();
  // A real plan without an MLflow address is blocked.
  await mode(page, "실제 실행 계획").check();
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("MLflow 서버 주소가 필요합니다");
  await mode(page, "모의 실행 계획").check();
  await plan(page);
  expect((await dataOf(page, "planning_inputs_save")).at(-1)).toMatchObject({ simulation: true,
    node_time_limits: [{ node_id: "train-1", timeout_minutes: 10080 }], estimates: [ESTIMATE] });
  expect((await dataOf(page, "graph_plan")).at(-1)).toMatchObject({ estimates: [ESTIMATE],
    node_resources: [{ node_id: "train-1", timeout_seconds: 604800, runtime_profile: "primary" }] });
  await expect(page.locator("img")).toHaveCount(0);
});

test("a plan answer that differs from the request is never held", async ({ page }) => {
  await openWorkbench(page, options({ workflow: { graph_submit: submitted(false) } }));
  await installPlanFake(page);
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  const revision = await currentRevision(page, projectId);
  // A dry-run request answered with a real plan of the same project and revision.
  await page.evaluate(answer => (window as any).__setPlans([answer]),
    ok(summary([planNode("train-1", "train", "기준 학습", [resource("pod-a")])], { as_sent: true, project_id: projectId, project_revision: revision, simulation: false })));
  await openGraph(page);
  await plan(page);
  expect((await dataOf(page, "graph_plan"))[0].simulation).toBe(true);
  await expect(page.locator(".notice.error")).toContainText("요청과 다른 프로젝트·버전·실행 종류의 계획");
  await expect(panel(page).locator(".plan-summary")).toHaveCount(0);
  await expect(approveButton(page)).toHaveCount(0);
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
});

test("a refused submit shows the backend reasons and keeps the plan", async ({ page }) => {
  const refused = invalid([issue("export_not_allowed", EXPORT_NOT_ALLOWED, "/plan_sha256", { node_id: "train-1" })]);
  await openWorkbench(page, options({ settings: { MLFLOW_TRACKING_URI: MLFLOW }, workflow: { graph_submit: [refused, submitted(false)] } }));
  await installPlanFake(page, { plans: [ok(summary([planNode("train-1", "train", "기준 학습", [resource("pod-a")])], { tracking: TRACKING }))] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await mode(page, "실제 실행 계획").check();
  await plan(page);
  const approve = async () => {
    await approveButton(page).click();
    const dialog = page.getByRole("dialog", { name: "실행 승인" });
    await dialog.getByRole("checkbox", { name: /^실행 승인/ }).check();
    await dialog.getByRole("checkbox", { name: /^데이터 전송 승인/ }).check();
    // The keyboard: the shell swallows a pointer click at the previous dialog's spot within 400 ms.
    await dialog.getByRole("button", { name: "승인하고 실행 등록", exact: true }).press("Enter");
    await idle(page);
  };
  await approve();
  await expect(heading(page)).toHaveText("학습 그래프");
  await expect(panel(page).locator(".plan-summary")).toContainText(EXPORT_NOT_ALLOWED);
  await expect(panel(page).locator(".plan-summary")).toContainText("기준 학습");
  await expect(approveButton(page)).toBeEnabled();
  await approve();
  await expect(heading(page)).toHaveText("그래프 실행");
  const sent = await dataOf(page, "graph_submit");
  expect(sent.map(request => request.request_id)).toEqual([sent[0].request_id, sent[0].request_id]);
});

// -- 10.5 review fix I1: a no-op edit never reaches the store, also when the backend returns sorted keys --
test("edits that leave the saved graph as it is keep the plan, with the backend's sorted keys", async ({ page }) => {
  const selection = { metric: "exact_match", direction: "max", thresholds: [{ metric: "exact_match", direction: "max", value: 0.5 }],
    allow_partial: false, min_success: 2, merge_regression_tolerance: 0 };
  const benchmark = { revision_sha256: H("7"), spec: { dataset: DATASETS[2].dataset, evaluator_kind: "text", evaluator_version: "1",
    ordered_sample_ids: ["row:0", "row:1"], max_new_tokens: 64, cutoff_len: 512, seed: 1, generation_mode: "greedy", precision: "bf16",
    quantization_bit: null, tokenizer_fingerprint: ROOT.tokenizer_fingerprint, template: "qwen", semantic_model_kind: "causal_lm" } };
  await openWorkbench(page, options({ workflow: { benchmarks_list: ok({ benchmarks: [benchmark] }) } }));
  await installPlanFake(page, { sortKeys: true, plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }),
    evalNode("eval-1", "train-1", { label: "평가", benchmark_revision: benchmark.revision_sha256 }),
    { id: "select-1", kind: "select", label: "선택", evaluations: [ref("eval-1", "evaluation")], selection }]) });
  await openGraph(page);
  await plan(page);
  await expect(approveButton(page)).toBeEnabled();
  await cardButton(page, "select-1").click();
  // The same threshold typed differently, the same cost and the same label.
  await nodeForm(page).getByLabel("기준값 (선택)").fill("0.50");
  await page.locator("#graph-cost").fill("100");
  await nodeForm(page).getByLabel("단계 이름").fill("선택");
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
  await expect(approveButton(page)).toBeEnabled();
  await expect(dropped(page)).toHaveCount(0);
  // A real change still drops it.
  await nodeForm(page).getByLabel("기준값 (선택)").fill("0.6");
  await expect(dropped(page)).toContainText("프로젝트 초안이 바뀌었습니다");
  await nodeForm(page).getByLabel("기준값 (선택)").fill("0.5");
  await expect(page.locator("#graph-head")).not.toContainText("저장 안 된 변경");
});

// -- 10.6 review fixes --
test("a plan made stale by another save of the project offers the latest version, then plans it", async ({ page }) => {
  await openWorkbench(page, options({ workflow: { graph_submit: conflict([issue("plan_stale", PLAN_STALE, "/project")]) } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  const planned = await currentRevision(page, projectId);
  // Another window saves the project; the submit of the plan is then stale at /project.
  const current = await bridge(page, "project_get", { project_id: projectId });
  expect((await bridge(page, "project_save", { spec: { ...current.data.project, purpose: "다른 창" }, expected_revision: planned })).status).toBe("ok");
  await approveButton(page).click();
  await page.getByRole("dialog", { name: "모의 실행 등록" }).getByRole("button", { name: "모의 실행 등록", exact: true }).click();
  await idle(page);
  await expect(dropped(page)).toContainText("프로젝트");
  // Planning the same (old) revision again cannot help: the latest version is offered instead.
  await expect(dropped(page).getByRole("button", { name: "다시 계획", exact: true })).toHaveCount(0);
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("최신 버전을 불러오세요");
  const reload = dropped(page).getByRole("button", { name: "최신 버전 불러오기", exact: true });
  await expect(reload).toBeFocused();
  await reload.click();
  await page.getByRole("dialog", { name: "최신 버전 불러오기" }).getByRole("button", { name: "불러오기", exact: true }).click();
  await idle(page);
  await expect(planButton(page)).toBeEnabled();
  await dropped(page).getByRole("button", { name: "다시 계획", exact: true }).click();
  await idle(page);
  const requests = await dataOf(page, "graph_plan");
  expect(requests.map(request => request.project_revision)).toEqual([planned, await currentRevision(page, projectId)]);
  await expect(approveButton(page)).toBeEnabled();
});

test("planning inputs refused for a project saved elsewhere offer the latest version", async ({ page }) => {
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [ONE_STEP] });
  const { projectId } = await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await limit(page, "train-1").fill("90");
  const current = await bridge(page, "project_get", { project_id: projectId });
  await bridge(page, "project_save", { spec: { ...current.data.project, purpose: "다른 창" }, expected_revision: current.data.revision_sha256 });
  await plan(page);
  expect(await calls(page, { action: "graph_plan" })).toEqual([]);
  await expect(panel(page).locator("#plan-actions")).toContainText("최신 프로젝트를 불러온 뒤 다시 저장하세요");
  await expect(planButton(page)).toBeDisabled();
  await panel(page).locator("#plan-actions").getByRole("button", { name: "최신 버전 불러오기", exact: true }).click();
  await page.getByRole("dialog", { name: "최신 버전 불러오기" }).getByRole("button", { name: "불러오기", exact: true }).click();
  await idle(page);
  await expect(limit(page, "train-1")).toHaveValue("90");
  await plan(page);
  const latest = await currentRevision(page, projectId);
  expect((await dataOf(page, "planning_inputs_save")).at(-1)).toMatchObject({ project_revision: latest, node_time_limits: [{ node_id: "train-1", timeout_minutes: 90 }] });
  expect((await dataOf(page, "graph_plan")).map(request => request.project_revision)).toEqual([latest]);
  await expect(approveButton(page)).toBeEnabled();
});

test("a plan that already has a run is never approved again, also after a restart", async ({ page }) => {
  const existing = ok({ runs: [{ run_id: "b".repeat(32), status: "RUNNING", simulation: true, plan_sha256: H("f"), created: 1792540800,
    nodes_by_status: { PENDING: 1 } }, { run_id: "c".repeat(32), error: "unreadable" }], controller: controllerView("absent") });
  await openWorkbench(page, options({ workflow: { graph_runs_list: [{ reject: "실행 기록을 읽을 수 없습니다." }, existing], graph_submit: submitted(true) } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  // The runs cannot be read: no approval opens (a run of this plan might exist).
  await approveButton(page).click();
  await idle(page);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(page.locator(".notice.error")).toContainText("기존 실행을 확인하지 못해");
  await expect(page.locator(".notice.error")).toContainText("실행 기록을 읽을 수 없습니다.");
  // A run of this plan exists (registered before a restart, whose request ID is gone): it is shown instead.
  await approveButton(page).click();
  await idle(page);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  const result = panel(page).locator(".plan-summary");
  await expect(result).toContainText("이 계획으로 등록한 실행이 있습니다");
  await expect(approveButton(page)).toHaveCount(0);
  const open = result.getByRole("button", { name: "실행 보기", exact: true });
  await expect(open).toBeFocused();
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
  expect(await calls(page, { action: "graph_runs_list" })).toHaveLength(2);
  await open.click();
  await expect(heading(page)).toHaveText("그래프 실행");
  expect(await calls(page, { action: "graph_submit" })).toEqual([]);
});

test("a malformed plan answer is refused without breaking the page", async ({ page }) => {
  const broken = ok(summary([planNode("train-1", "train", "기준 학습", [{ ...resource("pod-a"), profile_id: 5 }])]));
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [broken, ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  await expect(page.locator(".notice.error")).toContainText("엔진 응답 형식이 올바르지 않습니다.");
  await expect(approveButton(page)).toHaveCount(0);
  await plan(page);
  await expect(approveButton(page)).toBeEnabled();
});

test("a settings save while the plan is being made drops that plan", async ({ page }) => {
  await openWorkbench(page, options({ holds: ["graph_plan"] }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await planButton(page).click();
  await expect.poll(() => page.evaluate(() => (window as any).__held("graph_plan"))).toBe(1);
  // Meanwhile the connection settings are saved (the busy-disabled button re-enabled by script).
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.evaluate(() => { document.querySelector<HTMLButtonElement>('#settings-form button[type="submit"]')!.disabled = false; });
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect.poll(async () => (await calls(page, { command: "settings_save" })).length).toBe(1);
  await page.evaluate(() => (window as any).__release("graph_plan"));
  await idle(page);
  await expect(page.locator(".notice:not(.busy):not(.error)")).toContainText("계획을 만드는 동안");
  await expect(page.locator(".notice:not(.busy):not(.error)")).not.toContainText("계획을 만들었습니다");
  await openGraph(page);
  await expect(dropped(page)).toContainText("계획을 만드는 동안");
  await expect(approveButton(page)).toHaveCount(0);
});

test("a confirm right after another dialog closed at the same spot is not swallowed", async ({ page }) => {
  const refused = invalid([issue("export_not_allowed", EXPORT_NOT_ALLOWED, "/plan_sha256", { node_id: "train-1" })]);
  await openWorkbench(page, options({ clock: true, workflow: { graph_submit: [refused, submitted(true)] } }));
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" })]) });
  await openGraph(page);
  await plan(page);
  // The clock stands still from here: every later click is "within 400 ms" of the first dialog's close.
  await page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
  const dialog = page.getByRole("dialog", { name: "모의 실행 등록" });
  await approveButton(page).click();
  await dialog.getByRole("button", { name: "모의 실행 등록", exact: true }).click();
  await idle(page);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(panel(page).locator(".plan-summary")).toContainText(EXPORT_NOT_ALLOWED);
  // The new dialog's confirm sits where the closed one's was: a click inside it is meant for it.
  await approveButton(page).click();
  await dialog.getByRole("button", { name: "모의 실행 등록", exact: true }).click();
  await expect(heading(page)).toHaveText("그래프 실행");
  expect(await calls(page, { action: "graph_submit" })).toHaveLength(2);
});

test("re-entering with the stored inputs of the saved graph keeps a held plan", async ({ page }) => {
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }),
    evalNode("eval-1", "train-1", { label: "평가" })]) });
  await openGraph(page);
  await limit(page, "eval-1").fill("90");
  await plan(page);
  // eval-1 goes: the saved graph no longer has it, and the next plan saves the inputs without it.
  await cardButton(page, "eval-1").click();
  await page.locator("#graph-toolbar").getByRole("button", { name: "단계 삭제", exact: true }).click();
  await page.locator("#graph-head").getByRole("button", { name: "저장", exact: true }).click();
  await idle(page);
  await plan(page);
  expect((await dataOf(page, "planning_inputs_save")).at(-1).node_time_limits).toEqual([]);
  await expect(approveButton(page)).toBeEnabled();
  await page.locator('nav [data-action="nav:projects"]').click();
  await openGraph(page);
  await expect(approveButton(page)).toBeEnabled();
  await expect(dropped(page)).toHaveCount(0);
});

test("a card whose recipe cannot be read blocks planning with its reason", async ({ page }) => {
  await openWorkbench(page, options());
  await installPlanFake(page, { plans: [ONE_STEP] });
  await seed(page, { recipes: [LORA], graph: revisions => graphOf([trainNode("train-1", revisions["lora-sft"], { label: "기준 학습" }),
    trainNode("train-2", H("9"), { label: `${HOSTILE} 레시피` })]) });
  await openGraph(page);
  await expect(planButton(page)).toBeDisabled();
  await expect(panel(page).locator(".plan-blockers")).toContainText("실행 환경(primary·ppo)");
  await expect(panel(page).locator(".plan-blockers")).toContainText(`${HOSTILE} 레시피`);
  await expect(panel(page).locator('tr[data-plan-node="train-2"]')).toContainText("확인 불가");
  await expect(panel(page).locator('tr[data-plan-node="train-1"]')).toContainText("primary");
  await expect(page.locator("img")).toHaveCount(0);
});
