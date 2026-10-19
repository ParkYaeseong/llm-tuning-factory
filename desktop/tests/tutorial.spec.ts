import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { tutorialSteps } from "../src/tutorial-steps.ts";

const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
const forbidden = ["submit", "execute", "resume", "runtime_check", "runtime_prepare", "connection", "pod",
  "s3_list", "s3_head", "s3_import", "server_inspect", "server_facts", "runtime_reservations", "runtime_release"];
type Boot = { registered?: boolean; withoutDummy?: boolean; seen?: boolean };

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
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args });
      if (command === "settings_get") return {};
      if (command !== "factory_action") return {};
      const { action, data } = args;
      if (action === "catalog") return { templates: [{ name: "paper_extraction", config }], profiles: [], datasets: structuredClone(datasets),
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
});

test("ending the tutorial restores the previous training draft", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByLabel("업무 AI ID", { exact: true }).fill("my_draft");
  // Committing the edit first: its change event re-renders the page, which would swallow a click already pressed.
  await page.getByLabel("업무 AI ID", { exact: true }).blur();
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
