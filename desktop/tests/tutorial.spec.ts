import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { tutorialSteps } from "../src/tutorial-steps.ts";

const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
const forbidden = ["submit", "execute", "resume", "runtime_check", "runtime_prepare", "connection", "pod",
  "s3_list", "s3_head", "s3_import", "server_inspect", "server_facts", "runtime_reservations", "runtime_release",
  "save_profile", "model_import", "cancel", "recover", "reconcile"];
// Task 10 (D16): no native command that changes or picks anything, and only the read-only actions the project screens
// make on entering (observations_list is 9.8's read of the profiles page, added after the plan's list).
const forbiddenNative = ["executor_profile_save", "executor_profile_delete", "controller_start", "controller_stop", "pick_file"];
const tourReads = ["projects_list", "recipes_list", "graph_runs_list", "model_roots_list", "benchmarks_list", "controller_status",
  "observations_list"];
/** The legacy, native and workflow side-effect checks of a whole tour. */
function expectNoSideEffects(calls: any[]) {
  expect(calls.filter((c: any) => c.command === "factory_action" && forbidden.includes(c.args.action))).toEqual([]);
  expect(calls.map((c: any) => c.command)).not.toContain("settings_save");
  expect(calls.map((c: any) => c.command)).not.toContain("forget_credentials");
  expect(calls.filter((c: any) => forbiddenNative.includes(c.command))).toEqual([]);
  expect(calls.filter((c: any) => c.command === "workflow_action" && !tourReads.includes(c.args.action))).toEqual([]);
}
type Boot = { registered?: boolean; withoutDummy?: boolean; seen?: boolean; noTemplates?: boolean };

async function boot(page: Page, options: Boot = {}) {
  await page.addInitScript(({ config, options }) => {
    if (options.seen && !localStorage.getItem("tuning-factory.tutorial.v1"))
      localStorage.setItem("tuning-factory.tutorial.v1", JSON.stringify({ outcome: "completed" }));
    const calls: any[] = [];
    const sample = { dataset_id: "tutorial_sample", version: "1", kind: "instruction", rows: 8, sha256: "e".repeat(64),
      path: "C:\\Users\\tester\\AppData\\state\\tutorial\\tutorial_sample.json" };
    const datasets: any[] = options.withoutDummy ? [] : [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true, synthetic: true }];
    if (options.registered) datasets.push({ dataset_id: "tutorial_sample", version: "1", rows: 8, export_allowed: false, synthetic: true });
    (window as any).__testCalls = calls;
    const ok = (data: unknown) => ({ status: "ok", issues: [], data });
    const absent = { state: "absent", owner_id: null, heartbeat_age_seconds: null };
    // The project screens' reads answer empty valid results (D16); any other workflow action is not installed.
    const reads: Record<string, unknown> = { projects_list: ok({ projects: [] }), recipes_list: ok({ recipes: [] }),
      graph_runs_list: ok({ runs: [], controller: absent }), model_roots_list: ok({ roots: [] }), benchmarks_list: ok({ benchmarks: [] }),
      observations_list: ok({ observations: [] }) };
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args });
      if (command === "settings_get") return {};
      if (command === "settings_save") return { ...args.values };
      if (command === "workflow_action") return structuredClone(reads[args.action]
        ?? { status: "invalid", issues: [{ code: "not_available", path: "", message: "이 기능은 아직 설치되지 않았습니다." }], data: null });
      if (command === "controller_status") return ok({ controller: absent, current: null, broker: null, stop_requested: false,
        restart_required: { profiles: [], tracking: null }, runs: { active: 0 } });
      if (command === "executor_profiles_get") return { profiles: [] };
      if (command !== "factory_action") return {};
      const { action, data } = args;
      if (action === "catalog") return { templates: options.noTemplates ? [] : [{ name: "paper_extraction", config }], profiles: [], datasets: structuredClone(datasets),
        models: [{ id: config.base_model, template: "qwen", architecture: "qwen2", gpu_verified: true }],
        training_stages: [{ id: "sft", label: "SFT · 지도 학습", dataset_kind: "instruction" }] };
      if (action === "jobs") return [];
      if (action === "capabilities") return { valid: true, errors: [], warnings: [], supported_stages: ["sft"],
        methods: ["lora", "qlora"], dataset_kind: "instruction", engine_version: "llamafactory==0.9.5" };
      if (action === "tutorial_sample") return sample;
      if (action === "dataset_preview") return { rows: data.path === sample.path ? 8 : 1, format: "alpaca", duplicate_rows: 0,
        samples: [{ instruction: "가상 시료", output: "TUT-001" }], bytes: 900 };
      if (action === "dataset") {
        if (data.dataset_id === "tutorial_sample" && (data.export_allowed || !data.synthetic))
          throw "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다.";
        if (!datasets.some(d => d.dataset_id === data.dataset_id && d.version === data.version))
          datasets.push({ dataset_id: data.dataset_id, version: data.version, rows: 8, export_allowed: false, synthetic: true });
        return datasets.at(-1);
      }
      if (action === "plan") return { dry_run: true, estimated_cost_usd: .5, note: "실행 버튼 전에는 GPU 작업을 시작하지 않습니다.",
        manifest: { config: data.config, dataset: { dataset_id: data.config.dataset_id, rows: 8 } } };
      return {};
    } };
  }, { config, options });
  await page.goto("/");
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
}

const popover = (page: Page) => page.locator(".tour-popover");
const tourButton = (page: Page, name: string) => popover(page).getByRole("button", { name, exact: true });

async function expectStep(page: Page, id: string) {
  await expect(popover(page).locator("#tour-title")).toHaveText(tutorialSteps.find(s => s.id === id)!.title);
}

/** Holds the next request for `action` until the returned release function is called, optionally answering `result`. */
async function holdAction(page: Page, action: string, result?: unknown) {
  await page.evaluate(({ action, result }) => {
    const native = (window as any).__TAURI_INTERNALS__;
    const original = native.invoke;
    native.invoke = async (command: string, args: any) => {
      if (command !== "factory_action" || args.action !== action) return original(command, args);
      await new Promise(resolve => (window as any).__release = resolve);
      return result === undefined ? original(command, args) : result;
    };
  }, { action, result });
  return () => page.evaluate(() => (window as any).__release());
}

async function advanceTo(page: Page, id: string) {
  const goal = tutorialSteps.findIndex(s => s.id === id);
  const title = await popover(page).locator("#tour-title").textContent();
  for (let i = tutorialSteps.findIndex(s => s.title === title); i < goal; i++) {
    const skip = tourButton(page, "건너뛰기");
    await ((await skip.count()) ? skip : tourButton(page, "다음")).click();
    await expectStep(page, tutorialSteps[i + 1].id);
  }
}

test("first-run card starts the tutorial and dismissal is remembered", async ({ page }) => {
  await boot(page);
  const card = page.getByRole("region", { name: "튜토리얼 안내" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "다음에 하기" }).click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await expect(page.locator("#tour-title")).toBeFocused();
});

test("hands-on tutorial reaches a dry-run plan with every step anchored and no side effects", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  for (const [index, step] of tutorialSteps.entries()) {
    await expectStep(page, step.id);
    await expect(popover(page)).toContainText(`${index + 1} / ${tutorialSteps.length}`);
    if (step.id === "data-preview") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await tourButton(page, "연습 샘플 채우기").click();
      await expect(page.getByLabel("데이터 ID", { exact: true })).toHaveValue("tutorial_sample");
      await expect(page.locator('#dataset-form input[name="synthetic"]')).toBeChecked();
      await expect(page.locator('#dataset-form input[name="export_allowed"]')).not.toBeChecked();
      await page.getByRole("button", { name: "샘플 미리보기" }).click();
    }
    if (step.id === "data-register") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByRole("button", { name: "검사하고 등록" }).click();
    }
    if (step.id === "training-dataset") {
      await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("paper_extraction");
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByLabel("학습 데이터", { exact: true }).selectOption("tutorial_sample:1");
    }
    if (step.id === "training-plan") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByRole("button", { name: "실행 계획 확인" }).click();
    }
    await expect(page.locator(`#app [data-tour="${step.anchor}"]`)).toHaveCount(1);
    await expect(popover(page)).not.toHaveClass(/centered/);
    if (step.kind === "practice") await expect(popover(page)).toContainText("완료했습니다");
    await tourButton(page, index === tutorialSteps.length - 1 ? "완료" : "다음").click();
  }
  await expect(popover(page)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).__testCalls);
  expect(calls.filter((c: any) => c.command === "factory_action" && forbidden.includes(c.args.action))).toEqual([]);
  expect(calls.map((c: any) => c.command)).not.toContain("settings_save");
  expect(calls.map((c: any) => c.command)).not.toContain("forget_credentials");
  expectNoSideEffects(calls);
});

test("the tour explains the project screens without creating anything", async ({ page }) => {
  await boot(page);
  const card = page.getByRole("region", { name: "튜토리얼 안내" });
  await expect(card).toContainText("약 7분");
  await expect(card).toContainText("프로젝트");
  await card.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  // The welcome names the project flow and the single-job group; the practice still starts at the connection.
  for (const text of ["프로젝트", "데이터", "레시피", "그래프 실행", "단일 학습"]) await expect(popover(page)).toContainText(text);
  await advanceTo(page, "project-start");
  const titles: Record<string, string> = { overview: "작업 공간", profiles: "실행 프로필", "data-import": "데이터 가져오기",
    graph: "학습 그래프", runs: "그래프 실행" };
  const explained = ["project-start", "executor-profiles", "data-import", "graph-editor", "graph-runs"];
  // Right after the practice's last explanation and right before the end.
  expect(tutorialSteps.map(s => s.id).slice(tutorialSteps.findIndex(s => s.id === "jobs-history"))).toEqual(["jobs-history", ...explained, "finish"]);
  for (const id of explained) {
    const step = tutorialSteps.find(s => s.id === id)!;
    await expectStep(page, id);
    expect([step.kind, step.anchor, step.cta, step.enter, step.done]).toEqual(["explain", id, undefined, undefined, undefined]);
    await expect(page.getByRole("heading", { level: 1 })).toHaveText(titles[step.page]);
    await expect(page.locator(".notice.busy")).toHaveCount(0);
    await expect(page.locator(`#app [data-tour="${step.anchor}"]`)).toHaveCount(1);
    await expect(popover(page)).not.toHaveClass(/centered/);
    await expect(popover(page)).not.toContainText("화면 요소를 찾지 못했습니다");
    await expect(popover(page).locator(".tour-meta .badge")).toHaveText("설명");
    await tourButton(page, "다음").click();
  }
  await expectStep(page, "finish");
  for (const text of ["프로젝트", "그래프", "단일 학습"]) await expect(popover(page)).toContainText(text);
  await expect(tourButton(page, "연결 설정으로 이동")).toBeVisible();
  await tourButton(page, "완료").click();
  await expect(popover(page)).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).__testCalls);
  expectNoSideEffects(calls);
  // The project screens were read (never written) while they were explained.
  const reads = new Set(calls.filter((c: any) => c.command === "workflow_action").map((c: any) => c.args.action));
  expect([...reads].sort()).toEqual(["graph_runs_list", "observations_list"]);
  expect(calls.map((c: any) => c.command)).toEqual(expect.arrayContaining(["executor_profiles_get", "controller_status"]));
  expect(await page.evaluate(() => [Object.keys(localStorage), sessionStorage.length])).toEqual([["tuning-factory.tutorial.v1"], 0]);
  await expect(page.locator("img")).toHaveCount(0);
});

test("ending the tutorial restores the previous training draft", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByLabel("업무 AI ID", { exact: true }).fill("my_draft");
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "training-dataset");
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("paper_extraction");
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("my_draft");
  await page.getByRole("button", { name: "작업 공간", exact: true }).click();
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
});

test("practice steps can be skipped and a missing anchor centers the explanation", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-preview");
  await expect(tourButton(page, "다음")).toBeDisabled();
  await expect(tourButton(page, "건너뛰기")).toBeEnabled();
  await advanceTo(page, "plan-reading");
  await expect(popover(page)).toHaveClass(/centered/);
  await expect(popover(page)).toContainText("이 단계의 화면 요소를 찾지 못했습니다");
  await expect(tourButton(page, "다음")).toBeEnabled();
  await page.getByRole("button", { name: "실행 관리", exact: true }).click();
  await expect(popover(page)).toContainText("다른 화면을 보고 있습니다");
  await tourButton(page, "이 단계로 이동").click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("학습 설정");
});

test("an already registered practice sample completes the registration step", async ({ page }) => {
  await boot(page, { registered: true });
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-register");
  await expect(tourButton(page, "다음")).toBeEnabled();
  await expect(tourButton(page, "건너뛰기")).toHaveCount(0);
  await expect(popover(page)).toContainText("완료했습니다");
});

test("Ctrl+K starts the tutorial and each step moves focus to its title", async ({ page }) => {
  await boot(page, { seen: true });
  await page.keyboard.press("Control+K");
  await page.getByLabel("페이지 검색").fill("튜토리얼");
  await page.keyboard.press("Enter");
  await expectStep(page, "welcome");
  await expect(page.locator("#tour-title")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(tourButton(page, "그만하기")).toBeFocused();
  await tourButton(page, "다음").click();
  await expectStep(page, "connection-provider");
  await expect(page.locator("#tour-title")).toBeFocused();
  await expect(page.getByRole("dialog", { name: "GPU 연결 방식 고르기" })).toBeVisible();
});

test("one click navigates right after a training field edit re-renders the workbench", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByLabel("업무 AI ID", { exact: true }).fill("edited_draft");
  await page.getByRole("button", { name: "데이터", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("데이터");
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("edited_draft");
});

test("a press that never ends normally still lets the deferred render happen", async ({ page }) => {
  await boot(page, { seen: true });
  for (const ending of ["pointercancel", "no release", "second press"]) {
    await page.getByRole("button", { name: "학습 설정", exact: true }).click();
    await page.getByLabel("업무 AI ID", { exact: true }).fill(`draft ${ending}`);
    await page.locator("h1").evaluate(el => el.setAttribute("data-before-press", ""));
    await page.getByRole("button", { name: "데이터", exact: true }).hover();
    await page.mouse.down();
    if (ending === "pointercancel") {
      await page.evaluate(() => window.dispatchEvent(new PointerEvent("pointercancel")));
      await expect(page.locator("h1[data-before-press]")).toHaveCount(0, { timeout: 500 });
    } else if (ending === "second press") {
      // The first release schedules its render, but another press starts first and its release is never seen.
      await page.evaluate(() => {
        window.dispatchEvent(new PointerEvent("pointercancel"));
        document.querySelector('#app [data-action="nav:datasets"]')!.dispatchEvent(new PointerEvent("pointerdown", { bubbles: true }));
      });
      await expect(page.locator("h1[data-before-press]")).toHaveCount(0);
    } else await expect(page.locator("h1[data-before-press]")).toHaveCount(0);
    await page.mouse.up();
  }
});

test("a click released after a task finished during the press keeps the task's result", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  const pinned = "f".repeat(40);
  const release = await holdAction(page, "model_revision", { model_revision: pinned, tokenizer_revision: pinned, template: "qwen", model_profile: null });
  await page.getByRole("button", { name: "선택한 모델 버전 확인·고정" }).click();
  await expect(page.locator(".notice.busy")).toBeVisible();
  await page.getByRole("button", { name: "데이터", exact: true }).hover();
  await page.mouse.down();
  await release();
  // The pinning task has finished once its follow-up compatibility check went out with the new revision.
  await expect.poll(() => page.evaluate(pinned => (window as any).__testCalls.some((c: any) =>
    c.args?.action === "capabilities" && c.args.data.config.model_revision === pinned), pinned)).toBe(true);
  await page.mouse.up();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("데이터");
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByLabel("모델 revision · 40자리 commit", { exact: true })).toHaveValue(pinned);
});

test("ending the tutorial during a press focuses the tutorial button of the next render", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-preview");
  await page.locator("#dataset-form label.check").first().hover();
  await page.mouse.down();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.locator("h1").evaluate(el => el.setAttribute("data-before-release", ""));
  await page.evaluate(() => window.dispatchEvent(new PointerEvent("pointercancel")));
  // Only the render deferred until the release replaces the sidebar; focus must be on its new button.
  await expect(page.locator("h1[data-before-release]")).toHaveCount(0);
  await expect(page.getByRole("button", { name: "튜토리얼", exact: true })).toBeFocused();
  await page.mouse.up();
});

test("without an example setting the tutorial explains that it cannot start", async ({ page }) => {
  await boot(page, { seen: true, noTemplates: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expect(page.getByText("예제 학습 설정을 찾을 수 없어 튜토리얼을 시작할 수 없습니다.")).toBeVisible();
  await expect(popover(page)).toHaveCount(0);
});

test("ending while a plan request is pending waits for it and then restores the draft", async ({ page }) => {
  await boot(page, { registered: true });
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "training-dataset");
  await page.getByLabel("학습 데이터", { exact: true }).selectOption("tutorial_sample:1");
  await tourButton(page, "다음").click();
  await expectStep(page, "training-plan");
  const release = await holdAction(page, "plan");
  await page.getByRole("button", { name: "실행 계획 확인" }).click();
  await expect(popover(page)).toContainText("처리 중입니다");
  await page.keyboard.press("Escape");
  await expect(popover(page)).toContainText("진행 중인 작업이 끝나면 튜토리얼을 종료합니다.");
  await release();
  await expect(popover(page)).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("학습 설정");
  await expect(page.getByLabel("학습 데이터", { exact: true })).toHaveValue("dummy:1");
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toHaveCount(0);
});

test("ending while the sample request is pending keeps the dataset draft from before the tour", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "데이터", exact: true }).click();
  await page.getByLabel("데이터 ID", { exact: true }).fill("my_data");
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-preview");
  await expect(page.getByLabel("데이터 ID", { exact: true })).toHaveValue("my_data");
  const release = await holdAction(page, "tutorial_sample");
  await tourButton(page, "연습 샘플 채우기").click();
  await expect(tourButton(page, "연습 샘플 채우기")).toBeDisabled();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toContainText("진행 중인 작업이 끝나면 튜토리얼을 종료합니다.");
  await release();
  await expect(popover(page)).toHaveCount(0);
  await expect(page.getByLabel("데이터 ID", { exact: true })).toHaveValue("my_data");
});

test("returning to an anchored step scrolls to it and keeps the popover on screen", async ({ page }) => {
  // Instant scrolling: an unfinished smooth scroll from the first visit would otherwise mask the defect.
  await page.emulateMedia({ reducedMotion: "reduce" });
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "training-plan");
  await page.getByRole("button", { name: "실행 관리", exact: true }).click();
  await expect(popover(page)).toContainText("다른 화면을 보고 있습니다");
  await tourButton(page, "이 단계로 이동").click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("학습 설정");
  await expect(page.locator('#app [data-tour="training-plan"]')).toBeInViewport();
  await expect(popover(page)).toBeInViewport({ ratio: 1 });
  await expect(popover(page).locator("#tour-title")).toBeInViewport();
});

test("a centered explanation sits over the content area beside the narrow sidebar", async ({ page }) => {
  await page.setViewportSize({ width: 990, height: 800 });
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "plan-reading");
  await expect(popover(page)).toHaveClass(/centered/);
  await expect.poll(async () => {
    const [box, area] = [await popover(page).boundingBox(), await page.locator("#app main").boundingBox()];
    return Math.abs(box!.x + box!.width / 2 - (area!.x + area!.width / 2));
  }).toBeLessThan(2);
  await expect(popover(page)).toBeInViewport({ ratio: 1 });
});

test("ending the tutorial returns focus to the tutorial button", async ({ page }) => {
  await boot(page, { seen: true });
  const launcher = page.getByRole("button", { name: "튜토리얼", exact: true });
  await launcher.click();
  await expectStep(page, "welcome");
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await expect(launcher).toBeFocused();
  await page.keyboard.press("Enter");
  await expectStep(page, "welcome");
  await tourButton(page, "그만하기").click();
  await expect(popover(page)).toHaveCount(0);
  await expect(launcher).toBeFocused();
});

test("the last step's call-to-action ends the tour on the connection page", async ({ page }) => {
  await boot(page, { seen: true });
  const launcher = page.getByRole("button", { name: "튜토리얼", exact: true });
  await launcher.click();
  await expectStep(page, "welcome");
  await advanceTo(page, "finish");
  await tourButton(page, "연결 설정으로 이동").click();
  await expect(popover(page)).toHaveCount(0);
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("연결 설정");
  await expect(launcher).toBeFocused();
});

test("app re-renders keep the popover in place without re-announcing the step", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await tourButton(page, "다음").click();
  await expectStep(page, "connection-provider");
  await page.evaluate(() => {
    const layer = document.querySelector("#tutorial-layer")!;
    layer.querySelector<HTMLElement>("#tour-title")!.dataset.kept = "yes";
    (window as any).__announcements = 0;
    new MutationObserver(records => (window as any).__announcements += records.length)
      .observe(layer.querySelector(".sr-only")!, { childList: true, characterData: true, subtree: true });
  });
  await page.getByLabel("연결 대상", { exact: true }).selectOption("ssh_server");
  await expect(page.getByLabel("서버 주소", { exact: true })).toBeVisible();
  await expect(popover(page).locator('#tour-title[data-kept="yes"]')).toHaveCount(1);
  expect(await page.evaluate(() => (window as any).__announcements)).toBe(0);
});

test("starting the tutorial while work is in progress explains why it did not start", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "데이터", exact: true }).click();
  await page.getByLabel("파일 경로", { exact: true }).fill("C:\\data\\held.json");
  const release = await holdAction(page, "dataset_preview");
  await page.getByRole("button", { name: "샘플 미리보기" }).click();
  await expect(page.locator(".notice.busy")).toBeVisible();
  await page.keyboard.press("Control+K");
  await page.getByLabel("페이지 검색").fill("튜토리얼");
  await page.keyboard.press("Enter");
  await expect(page.getByText("작업 공간을 불러오거나 진행 중인 작업이 끝난 뒤 튜토리얼을 시작하세요.")).toBeVisible();
  await expect(popover(page)).toHaveCount(0);
  await release();
  await expect(page.locator(".notice.busy")).toHaveCount(0);
});

test("the tutorial button restarts an active tour and the end still restores the original draft", async ({ page }) => {
  await boot(page, { registered: true });
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByLabel("업무 AI ID", { exact: true }).fill("my_draft");
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "training-plan");
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("paper_extraction");
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await expect(popover(page)).toContainText(`1 / ${tutorialSteps.length}`);
  await expect(page.locator("#tour-title")).toBeFocused();
  await advanceTo(page, "data-register");
  await expect(tourButton(page, "다음")).toBeEnabled();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("my_draft");
});

test("restarting ignores a sample request from the previous run", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-preview");
  const release = await holdAction(page, "tutorial_sample");
  await tourButton(page, "연습 샘플 채우기").click();
  await expect(tourButton(page, "연습 샘플 채우기")).toBeDisabled();
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await release();
  await advanceTo(page, "data-preview");
  await expect(page.getByLabel("데이터 ID", { exact: true })).toHaveValue("my_dataset");
  await expect(tourButton(page, "연습 샘플 채우기")).toBeEnabled();
  await expect(tourButton(page, "다음")).toBeDisabled();
});

test("ending the tutorial drops a plan made for a connection saved differently during the tour", async ({ page }) => {
  await boot(page, { seen: true });
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByRole("button", { name: "실행 계획 확인" }).click();
  const planCard = page.getByRole("heading", { name: "실행 전 확인" });
  await expect(planCard).toBeVisible();
  // An unchanged connection keeps the plan from before the tour.
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(planCard).toBeVisible();
  // Saving another destination during the tour invalidates it for good.
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await tourButton(page, "다음").click();
  await expectStep(page, "connection-provider");
  await page.getByLabel("연결 대상", { exact: true }).selectOption("ssh_server");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect(page.getByText("연결 정보를 저장했습니다.", { exact: true })).toBeVisible();
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("학습 설정");
  await expect(planCard).toHaveCount(0);
});

test("a registered real dataset completes the data checklist item", async ({ page }) => {
  await boot(page, { registered: true, seen: true });
  const row = page.locator(".setup-row", { hasText: "데이터 버전 등록" });
  await expect(row.locator(".setup-check")).toHaveClass(/done/);
});

test("the practice sample alone does not complete the data checklist item", async ({ page }) => {
  await boot(page, { registered: true, withoutDummy: true, seen: true });
  const row = page.locator(".setup-row", { hasText: "데이터 버전 등록" });
  await expect(row.locator(".setup-check")).not.toHaveClass(/done/);
});
