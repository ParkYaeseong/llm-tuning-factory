import { test, expect } from "@playwright/test";
import { calls, openWorkbench } from "./workflow-mock.ts";
import english from "../src/locales/en.json" with { type: "json" };

test("English catalog preserves all interpolation slots and has no untranslated entries", () => {
  expect(Object.keys(english).length).toBeGreaterThan(1600);
  for (const [source, translated] of Object.entries(english)) {
    expect(translated.trim(), source).not.toBe("");
    expect(translated, source).not.toMatch(/[가-힣]/);
    expect((translated.match(/\{\d+\}/g) ?? []).sort(), source).toEqual((source.match(/\{\d+\}/g) ?? []).sort());
  }
});

test("ENG/KOR switches immediately, persists, and preserves project text and drafts", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  const name = page.locator("#project-create-name");
  await name.fill("설정");
  await page.locator("#project-create-purpose").fill("논문 원문은 번역하지 않기");
  const before = (await calls(page)).length;
  await page.getByRole("combobox", { name: "Language / 언어" }).selectOption("en");
  await expect(page.locator("html")).toHaveAttribute("lang", "en");
  await expect(page.locator('nav [data-action="nav:projects"]')).toHaveText("Projects");
  await expect(page.getByRole("heading", { name: "Create a blank project", exact: true })).toBeVisible();
  await expect(name).toHaveValue("설정");
  await expect(page.locator("#project-create-purpose")).toHaveValue("논문 원문은 번역하지 않기");
  expect((await calls(page)).length).toBe(before);
  await page.locator("#project-create-form").getByRole("button", { name: "Create", exact: true }).click();
  await expect(page.locator(".page-heading h1")).toHaveText("설정");
  await expect(page.getByLabel("Project name", { exact: true })).toHaveValue("설정");
  await page.reload();
  await expect(page.locator('nav [data-action="nav:projects"]')).toHaveText("Projects");
  await expect(page.getByRole("combobox", { name: "Language / 언어" })).toHaveValue("en");
  await page.getByRole("combobox", { name: "Language / 언어" }).selectOption("ko");
  await expect(page.locator("html")).toHaveAttribute("lang", "ko");
  await expect(page.locator('nav [data-action="nav:projects"]')).toHaveText("프로젝트");
});

test("language switching keeps credential drafts in memory and stores only the language", async ({ page }) => {
  await openWorkbench(page);
  await page.locator('nav [data-action="nav:connections"]').click();
  const token = page.locator('#settings-form [data-key="RUNPOD_API_KEY"]');
  await token.fill("local-only-draft");
  await page.getByRole("combobox", { name: "Language / 언어" }).selectOption("en");
  await expect(token).toHaveValue("local-only-draft");
  expect(await page.evaluate(() => Object.fromEntries(Object.entries(localStorage)))).toEqual({ "factory.language": "en" });
  expect(await calls(page, { command: "settings_save" })).toEqual([]);
});

test("language switching retains the reviewed plan without submitting or rebuilding it", async ({ page }) => {
  await openWorkbench(page);
  await page.locator('nav [data-action="nav:training"]').click();
  await page.locator('[data-tour="training-plan"]').click();
  const plan = page.locator('[data-tour="plan-summary"]');
  await expect(plan).toBeVisible();
  const before = await calls(page, { action: "plan" });
  const manifest = await plan.locator("pre").textContent();
  await page.getByRole("combobox", { name: "Language / 언어" }).selectOption("en");
  await expect(plan).toBeVisible();
  expect(await plan.locator("pre").textContent()).toBe(manifest);
  expect(await calls(page, { action: "plan" })).toEqual(before);
  expect(await calls(page, { action: "submit" })).toEqual([]);
  expect(await calls(page, { action: "execute" })).toEqual([]);
});

test("English shell, connection form, tutorial and command palette use English", async ({ page }) => {
  await openWorkbench(page);
  await page.getByRole("combobox", { name: "Language / 언어" }).selectOption("en");
  for (const id of ["overview", "projects", "datasets", "recipes", "runs", "history", "training", "jobs", "connections", "profiles"]) {
    const link = page.locator(`nav [data-action="nav:${id}"]`);
    if (!await link.count()) continue;
    await link.click();
    await expect(page.locator(".notice.busy")).toHaveCount(0);
    // The diagnostic keeps the server's original wording in parentheses after its English explanation.
    const text = await page.locator("#app").innerText();
    expect(text.replace("Language / 언어", "").replace("(이 기능은 아직 설치되지 않았습니다.)", "")).not.toMatch(/[가-힣]/);
  }
  await page.locator('[data-action="search"]').click();
  await page.getByRole("textbox", { name: "Search pages" }).fill("Projects");
  await expect(page.locator(".command-options")).toContainText("Projects");
  await page.keyboard.press("Escape");
  await page.locator('aside [data-action="tutorial"]').click();
  await expect(page.locator("#tour-title")).toContainText("Welcome");
  expect(await page.locator(".tour-popover").innerText()).not.toMatch(/[가-힣]/);
});
