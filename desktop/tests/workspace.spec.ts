import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ config }) => {
    const calls: any[] = [];
    const runs = [
      { id: "a", ai_id: "paper", status: "FINISHED", operator: "<img src=x onerror=alert(1)>",
        metrics: { accuracy: .5 }, config: {}, validation_sha256: "v1", simulation: false },
      { id: "b", ai_id: "paper", status: "FINISHED", operator: "tester",
        metrics: { accuracy: .8 }, config: {}, validation_sha256: "v2", simulation: false },
    ];
    (window as any).__testCalls = calls;
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args });
      if (command === "settings_get") return {};
      if (command === "settings_save") return { FACTORY_OPERATOR: args.values.FACTORY_OPERATOR, RUNPOD_API_KEY_saved: true };
      if (command === "pick_file") return "C:\\data\\dummy.json";
      if (command === "factory_action") {
        const { action, data } = args;
        if (action === "catalog") return { templates: [{ name: "paper_extraction", config }], profiles: [],
          datasets: [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true }] };
        if (action === "jobs") return [];
        if (action === "plan") return { dry_run: true, manifest: { config: data.config, dataset: { dataset_id: "dummy", rows: 2 } }, estimated_cost_usd: .5 };
        if (action === "history") return data.experiment_id ? { runs, next_page_token: null } : { experiments: [{ id: "16", name: "test" }] };
        if (action === "compare") return { comparable: false, reason: "동일 업무·검증 데이터·평가 방식의 실제 실행만 지표를 비교합니다.", deltas: {} };
        if (action === "submit") return { id: "c".repeat(32) };
        return {};
      }
    } };
  }, { config });
  await page.goto("/");
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
});
test("connection wizard masks secrets and persists via native command", async ({ page }) => {
  await page.getByRole("button", { name: "02연결 설정" }).click();
  await page.getByLabel("RunPod API 키").fill("synthetic-secret");
  await page.getByLabel("기록에 표시할 이름").fill("검토자");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect(page.getByLabel("RunPod API 키")).toHaveValue("");
  await expect(page.getByText("보안 저장됨 · 변경할 때만 입력")).toBeVisible();
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});
test("plan is offline and execution requires separate explicit confirmation", async ({ page }) => {
  await page.getByRole("button", { name: "04학습 설정" }).click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "execute"))).toBe(false);
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});
test("changed settings invalidate an already displayed execution plan", async ({ page }) => {
  await page.getByRole("button", { name: "04학습 설정" }).click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).fill("3");
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).blur();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("alert")).toContainText("실행 계획을 다시 확인");
});
test("shared history escapes data and refuses invalid metric deltas", async ({ page }) => {
  await page.getByRole("button", { name: "06기록 비교" }).click();
  await page.getByRole("button", { name: "기록 불러오기", exact: true }).click();
  await expect(page.getByText("<img src=x onerror=alert(1)>", { exact: true })).toBeVisible();
  await page.locator("[data-run=a]").check(); await page.locator("[data-run=b]").check();
  await page.getByRole("button", { name: "선택한 두 실행 비교" }).click();
  await expect(page.getByText("동일 업무·검증 데이터·평가 방식의 실제 실행만 지표를 비교합니다.", { exact: true })).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  await page.screenshot({ path: "test-results/history.png", fullPage: true });
});
