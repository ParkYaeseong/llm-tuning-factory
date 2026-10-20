import { test, expect } from "@playwright/test";
import { calls, config, HOSTILE, openWorkbench } from "./workflow-mock.ts";

const WORKFLOW_COMMANDS = ["workflow_action", "executor_profiles_get", "executor_profile_save", "executor_profile_delete",
  "controller_status", "controller_start", "controller_stop"];

test("legacy navigation, labels and anchors stay while no workflow call runs at boot", async ({ page }) => {
  await openWorkbench(page, { settings: { FACTORY_OPERATOR: HOSTILE } });
  const sidebar = page.locator("aside");
  for (const name of ["작업 공간", "데이터", "학습 설정", "실행 관리", "기록 비교", "연결 설정", "튜토리얼"])
    await expect(sidebar.getByRole("button", { name, exact: true })).toHaveCount(1);
  // D1: the single-job pages get their own group; the group labels are plain text, not buttons or headings.
  await expect(sidebar.locator(".nav-label")).toHaveText(["워크스페이스", "단일 학습", "설정"]);
  expect(await sidebar.locator("nav button").evaluateAll(buttons => buttons.map(button => button.getAttribute("aria-label"))))
    .toEqual(["작업 공간", "데이터", "기록 비교", "학습 설정", "실행 관리", "연결 설정", "튜토리얼"]);
  await expect(page.locator(".sidebar-foot small")).toHaveCount(1);
  await expect(page.locator(".sidebar-foot b")).toHaveText(HOSTILE);
  // D19 wording on the fixed headings; the header keeps "새 학습" while no project view exists.
  await expect(page.getByRole("heading", { name: "예제 시작점 · 선택", exact: true })).toBeVisible();
  await expect(page.locator(".page-heading").getByRole("button", { name: "새 학습" })).toHaveCount(1);
  await expect(page.locator('#app [data-tour="overview-setup"]')).toHaveCount(1);
  for (const [nav, anchors] of [["datasets", ["dataset-form", "dataset-submit"]], ["training", ["train-dataset", "training-plan"]],
    ["jobs", ["jobs-list"]], ["history", []], ["connections", ["connection-provider", "connection-fields", "connection-runtime"]]] as const) {
    await page.locator(`nav [data-action="nav:${nav}"]`).click();
    for (const anchor of anchors) await expect(page.locator(`#app [data-tour="${anchor}"]`)).toHaveCount(1);
    if (nav === "training") {
      await expect(page.getByRole("heading", { name: "실험 설정", exact: true })).toBeVisible();
      await expect(page.locator(".page-heading p")).toHaveText("목적에 맞는 데이터, 모델, 학습 조건을 설정합니다.");
    }
  }
  // Neither boot nor any legacy page calls the workflow channel, the profile store or the controller.
  expect((await calls(page)).filter(call => WORKFLOW_COMMANDS.includes(call.command))).toEqual([]);
  expect(await page.evaluate(() => localStorage.length + sessionStorage.length)).toBe(0);
  await expect(page.locator("img")).toHaveCount(0);
});

test("legacy pages ignore graph-owned jobs without breaking", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  const single = { id: "a".repeat(32), status: "COMPLETED", attempts: 1, updated: 1791960000, graph_run_id: null,
    manifest: { config: { ...config, ai_id: HOSTILE }, simulation: false } };
  // A graph-owned job: v2 manifest without `config`, as the legacy `jobs` action returns it (F8).
  const graphJob = { id: "b".repeat(32), status: "RUNNING", attempts: 1, updated: 1791960000, graph_run_id: "c".repeat(32),
    graph_node_id: "train-1", manifest: { schema_version: 2, operation: { kind: "train", node_id: "train-1" } } };
  await openWorkbench(page, { jobs: [single, graphJob] });
  const recent = page.locator(".panel", { has: page.getByRole("heading", { name: /최근 실행/ }) });
  await expect(recent.locator("tbody tr")).toHaveCount(1);
  await expect(recent.locator("tbody")).toContainText(HOSTILE);
  await expect(recent).toContainText("그래프 실행 단계 1개는 ‘그래프 실행’에서 확인하세요.");
  // The running graph step neither counts as a running single job nor lights the jobs marker.
  await expect(page.locator(".summary-strip")).toContainText("진행 중 0");
  await expect(page.locator(".activity-marker")).toHaveCount(0);
  await page.locator('nav [data-action="nav:jobs"]').click();
  const list = page.locator('[data-tour="jobs-list"]');
  await expect(list.locator("tbody tr")).toHaveCount(1);
  await expect(list).toContainText("그래프 실행 단계 1개는 ‘그래프 실행’에서 확인하세요.");
  // Legacy job actions are never offered for the graph job.
  await expect(page.locator(`[data-action="job:${graphJob.id}"]`)).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
  expect(errors).toEqual([]);
});

// A stand-in for the view registry, served in place of /src/view-registry.ts: the shell is exercised through the same
// contract later sub-tasks use (no test hook exists in the app). Dynamic text goes through the shared escaping helpers.
const FAKE_REGISTRY = `
import { escapeHtml, openDialog, statusPill } from "/src/workbench.ts";
const log = window.__viewLog = [];
let ticks = 0, current = null;
const region = () => '<p>' + statusPill("good", "갱신 " + ticks) + '</p><button type="button" data-action="wf:projects:tick:7"'
  + ' data-focus-key="tick">지금 갱신</button><input id="fake-note" aria-label="메모">';
const projects = {
  id: "projects", title: "프로젝트", description: "목적과 데이터를 묶습니다.",
  nav: { group: "workspace", label: "프로젝트", icon: "projects", order: 20 },
  render: () => '<section class="card"><div id="fake-region">' + region() + '</div><div class="actions">'
    + '<button type="button" data-action="wf:projects:open:p-1">열기</button>'
    + '<button type="button" data-action="wf:projects:dialog">승인 확인</button>'
    + '<button type="button" data-action="wf:projects:slow">오래 걸리는 작업</button></div></section>',
  async enter(ctx) { current = ctx; log.push("enter:" + ctx.page + ":" + ctx.arg); },
  leave(ctx) { log.push("leave:" + ctx.page); },
  bind() { window.__bound = (window.__bound ?? 0) + 1; },
  async action(ctx, verb, arg) {
    log.push("action:" + verb + ":" + arg);
    if (verb === "tick") { ticks++; ctx.patch("fake-region", region()); }
    if (verb === "open") await ctx.navigate("project", arg);
    if (verb === "slow") await ctx.task("오래 걸리는 작업 중", () => new Promise(resolve => { window.__finish = resolve; }));
    if (verb === "dialog") log.push("dialog:" + JSON.stringify(await openDialog({ title: "실행 승인 <img src=x onerror=alert(1)>",
      bodyHtml: "<p>" + escapeHtml("<img src=x onerror=alert(1)> 본문") + "</p>",
      checks: [{ id: "execution", label: "실행 승인" }, { id: "transfer", label: "데이터 전송 승인" }],
      actions: [{ id: "back", label: "돌아가기" }, { id: "go", label: "승인하고 실행 등록", primary: true, requires: ["execution", "transfer"] }] })));
  },
  overviewPanel: () => '<section class="panel" data-fake-slot="overview"><div class="panel-heading"><h3>빈 프로젝트 만들기</h3></div></section>',
  datasetsPanel: () => '<section class="card" data-fake-slot="datasets"><h3>열 연결해 가져오기</h3></section>',
};
const project = { id: "project", title: "프로젝트 하나", description: "한 프로젝트", parent: "projects",
  render: ctx => '<p class="fake-arg">' + escapeHtml(ctx.arg) + '</p>' };
const broken = { id: "broken", title: "고장 난 화면", description: "", render: () => "",
  overviewPanel: () => { throw new Error("panel failed <img src=x onerror=alert(1)>"); },
  bind: () => { throw new Error("bind failed"); } };
window.__patch = () => { ticks++; current.patch("fake-region", region()); };
window.__dialog = openDialog;
export const views = [broken, projects, project];
export const viewById = id => views.find(view => view.id === id);
`;

test("workflow views plug into the shell: routing, slots, patches that keep focus, busy state and dialogs", async ({ page }) => {
  await page.route(/\/src\/view-registry\.ts(\?.*)?$/, route => route.fulfill({ contentType: "application/javascript", body: FAKE_REGISTRY }));
  await openWorkbench(page);
  const log = () => page.evaluate(() => (window as any).__viewLog as string[]);
  expect(await page.locator("aside nav button").evaluateAll(buttons => buttons.map(button => button.getAttribute("aria-label"))))
    .toEqual(["작업 공간", "프로젝트", "데이터", "기록 비교", "학습 설정", "실행 관리", "연결 설정", "튜토리얼"]);
  // D1: with a projects view the overview's primary action starts a project, and its panel leads the left column.
  await expect(page.locator(".page-heading").getByRole("button", { name: "새 프로젝트" })).toHaveAttribute("data-action", "nav:projects");
  await expect(page.locator(".workspace-columns > div").first().locator("> :first-child")).toHaveAttribute("data-fake-slot", "overview");
  // A view that throws while rendering or binding never takes the legacy overview down (as graph jobs did, F8).
  await expect(page.locator(".wf-banner.bad")).toContainText("이 영역을 표시하지 못했습니다. Error: panel failed <img src=x onerror=alert(1)>");
  await expect(page.locator('#app [data-tour="overview-setup"]')).toHaveCount(1);
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__bound)).toBeGreaterThan(0);
  await page.locator('nav [data-action="nav:projects"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("프로젝트");
  expect(await log()).toEqual(["enter:projects:null"]);

  // A patch keeps the focused control, and what was typed in a replaced input.
  const tick = page.getByRole("button", { name: "지금 갱신" });
  await tick.focus();
  await page.keyboard.press("Enter");
  await expect(page.locator("#fake-region .status-pill")).toHaveText("갱신 1");
  await expect(tick).toBeFocused();
  await page.getByLabel("메모").fill("입력 중인 메모");
  await page.evaluate(() => (window as any).__patch());
  await expect(page.locator("#fake-region .status-pill")).toHaveText("갱신 2");
  await expect(page.getByLabel("메모")).toBeFocused();
  await expect(page.getByLabel("메모")).toHaveValue("입력 중인 메모");

  // While a task runs, view actions are disabled (also in a region patched meanwhile); navigation stays available.
  await page.getByRole("button", { name: "오래 걸리는 작업" }).click();
  await expect(page.locator(".notice.busy")).toContainText("오래 걸리는 작업 중");
  await page.evaluate(() => (window as any).__patch());
  await expect(page.locator("#fake-region .status-pill")).toHaveText("갱신 3");
  await expect(tick).toBeDisabled();
  await expect(page.getByRole("button", { name: "열기" })).toBeDisabled();
  await expect(page.locator('nav [data-action="nav:datasets"]')).toBeEnabled();
  await page.evaluate(() => (window as any).__finish());
  await expect(page.locator(".notice.busy")).toHaveCount(0);
  await expect(tick).toBeEnabled();

  // nav:<page>:<arg> leaves the current view; a child page shows and highlights its parent.
  await page.getByRole("button", { name: "열기" }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("프로젝트 하나");
  await expect(page.locator(".fake-arg")).toHaveText("p-1");
  await expect(page.locator(".breadcrumb")).toHaveText("내 워크스페이스 / 프로젝트 / 프로젝트 하나");
  await expect(page.locator('nav [data-action="nav:projects"]')).toHaveClass("selected");
  expect((await log()).slice(-2)).toEqual(["action:open:p-1", "leave:projects"]);
  await page.locator('nav [data-action="nav:datasets"]').click();
  await expect(page.locator('section.card:has(#dataset-form) + [data-fake-slot="datasets"]')).toHaveCount(1);
  await expect(page.locator("#dataset-form")).toHaveCount(1);

  // Dialogs: heading focus, checks gate the action, no second dialog, Esc returns focus, one result per opening.
  await page.locator('nav [data-action="nav:projects"]').click();
  const opener = page.getByRole("button", { name: "승인 확인" });
  await opener.click();
  const dialog = page.getByRole("dialog", { name: "실행 승인 <img src=x onerror=alert(1)>" });
  await expect(dialog).toBeVisible();
  await expect(dialog.getByRole("heading")).toBeFocused();
  await expect(dialog).toContainText("<img src=x onerror=alert(1)> 본문");
  const go = dialog.getByRole("button", { name: "승인하고 실행 등록" });
  await expect(go).toBeDisabled();
  await dialog.getByLabel("실행 승인").check();
  await expect(go).toBeDisabled();
  await dialog.getByLabel("데이터 전송 승인").check();
  await expect(go).toBeEnabled();
  expect(await page.evaluate(() => (window as any).__dialog({ title: "두 번째", bodyHtml: "", actions: [{ id: "x", label: "x" }] }))).toBeNull();
  await expect(page.locator("dialog")).toHaveCount(1);
  await page.keyboard.press("Escape");
  await expect(page.locator("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
  await page.keyboard.press("Enter");
  await dialog.getByLabel("실행 승인").check();
  await dialog.getByRole("button", { name: "돌아가기" }).click();
  await expect(page.locator("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
  // A double activation of the confirm button resolves once (the second click lands on the page below).
  await opener.click();
  await dialog.getByLabel("실행 승인").check();
  await dialog.getByLabel("데이터 전송 승인").check();
  await go.dblclick();
  await expect(page.locator("dialog")).toHaveCount(0);
  expect((await log()).filter(entry => entry.startsWith("dialog:"))).toEqual(["dialog:null",
    'dialog:{"action":"back","checked":["execution"]}', 'dialog:{"action":"go","checked":["execution","transfer"]}']);
  await expect(page.locator("img")).toHaveCount(0);
  expect((await calls(page)).filter(call => WORKFLOW_COMMANDS.includes(call.command))).toEqual([]);
});
