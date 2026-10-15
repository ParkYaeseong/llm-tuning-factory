// Opt-in: connect only to a test instance launched with loopback WebView2 debugging.
import { chromium } from "@playwright/test";
import { mkdirSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";
const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
try {
  const page = browser.contexts()[0].pages().find(p => /tauri|localhost/.test(p.url()));
  if (!page) throw new Error("Native application WebView not found");
  await page.waitForFunction(() => !!window.__TAURI_INTERNALS__);
  await page.getByRole("heading", { name: "작업 공간", exact: true }).waitFor();
  await page.waitForFunction(() => !document.body.textContent.includes("작업 공간 불러오는 중"));
  await page.getByText('Windows · 내부 검토 0.3.0', { exact: true }).waitFor();
  const catalog = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("factory_action", { action: "catalog", data: {} }));
  if (catalog.templates.length < 3) throw new Error("Bundled profiles missing");
  if (catalog.training_stages.length !== 6 || catalog.models.length < 15) throw new Error("Stage/model catalog missing");
  const config = catalog.templates.find(t => t.name === "paper_extraction").config;
  mkdirSync("test-results", { recursive: true });
  const source = resolve("test-results/native-dummy.json");
  writeFileSync(source, JSON.stringify([{ instruction: "synthetic question", output: "synthetic answer" }]));
  const suffix = Date.now().toString();
  config.dataset_id = "desktop_smoke"; config.dataset_version = suffix;
  await page.evaluate(({ source, suffix }) => window.__TAURI_INTERNALS__.invoke("factory_action", { action: "dataset",
    data: { path: source, dataset_id: "desktop_smoke", version: suffix, synthetic: true, export_allowed: false } }), { source, suffix });
  const plan = await page.evaluate(config => window.__TAURI_INTERNALS__.invoke("factory_action", { action: "plan", data: { config } }), config);
  if (!plan.dry_run || !plan.manifest.dependency_file_hashes["containers/gpu-requirements.lock"]) throw new Error("Packaged dry-run lacks worker lock");
  const blockedPpo = await page.evaluate(config => window.__TAURI_INTERNALS__.invoke("factory_action", {
    action: "capabilities", data: { config: { ...config, training_stage: "ppo" } }
  }), config);
  if (blockedPpo.valid || !blockedPpo.errors.some(x => x.includes("reward_model"))) throw new Error("PPO prerequisite guard missing");
  const modern = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('factory_action', {
    action: 'model_inspect', data: {model: 'Qwen/Qwen3.5-0.8B'}
  }));
  if (!modern.supported || modern.architecture !== 'qwen3_5') throw new Error('Modern HF inspection unavailable');
  const registered = await page.evaluate(modern => window.__TAURI_INTERNALS__.invoke('factory_action', {
    action: 'model_import', data: {model: modern.repo_id, revision: modern.revision, config_sha256: modern.profile.config_sha256}
  }), modern);
  const modernConfig = {...config, base_model: registered.profile.repo_id, template: registered.profile.template,
    model_revision: registered.profile.revision, tokenizer_revision: registered.profile.revision, model_profile: registered.profile};
  const modernPlan = await page.evaluate(config => window.__TAURI_INTERNALS__.invoke('factory_action', {
    action:'plan', data:{config}
  }), modernConfig);
  if (!modernPlan.dry_run || modernPlan.manifest.config.model_profile.repo_id !== modern.repo_id) throw new Error('Modern HF plan failed');
  const preview = await page.evaluate(source => window.__TAURI_INTERNALS__.invoke("factory_action",
    { action: "dataset_preview", data: { path: source } }), source);
  if (preview.rows !== 1 || preview.samples.length !== 1) throw new Error("Native dataset inspection failed");
  await page.keyboard.press("Control+k");
  await page.getByRole("dialog").getByRole("textbox").fill("데이터");
  await page.getByRole("dialog").getByRole("button", { name: "데이터", exact: true }).click();
  await page.getByLabel("파일 경로", {exact: true}).fill(source);
  await page.getByRole("button", {name: "샘플 미리보기", exact: true}).click();
  await page.getByRole("heading", {name: "데이터 미리보기", exact: true}).waitFor();
  await page.screenshot({path: "test-results/native-dataset.png", fullPage: true});
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByLabel("RunPod API 키").waitFor();
  const initial = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("settings_get"));
  let vaultVerified = false;
  if (!Object.keys(initial).some(key => key.endsWith("_saved") && initial[key])) {
    const fake = "desktop-synthetic-vault-" + suffix;
    try {
      await page.evaluate(({ fake, initial }) => window.__TAURI_INTERNALS__.invoke("settings_save",
        { values: { ...initial, RUNPOD_API_KEY: fake, S3_ACCESS_KEY_ID: fake,
          S3_SECRET_ACCESS_KEY: fake, S3_SESSION_TOKEN: fake, HF_TOKEN: fake } }), { fake, initial });
      const masked = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("settings_get"));
      if (!masked.RUNPOD_API_KEY_saved || masked.RUNPOD_API_KEY) throw new Error("Vault response leaked secret");
      for (const key of ["S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN"]) {
        if (!masked[key + "_saved"] || masked[key]) throw new Error("S3 vault value missing or leaked");
      }
      vaultVerified = true;
    } finally {
      await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("forget_credentials"));
      await page.evaluate(initial => window.__TAURI_INTERNALS__.invoke("settings_save", { values: initial }), initial);
    }
  }
  await page.screenshot({ path: "test-results/native-connections.png", fullPage: true });
  await page.locator('nav [data-action="nav:overview"]').click();
  await page.screenshot({ path: "test-results/native-workspace.png", fullPage: true });
  console.log(JSON.stringify({ native_ui: true, bundled_catalog: catalog.templates.length,
    dataset_registered_locally: true, dry_run: plan.dry_run, worker_lock_present: true, native_vault_masked: vaultVerified,
    native_s3_vault_masked: vaultVerified, training_stage_catalog: catalog.training_stages.length, ppo_guard: true,
    modern_hf_model_registered: modern.repo_id, modern_hf_revision: modern.revision, native_hf_vault_masked: vaultVerified,
    native_dataset_preview: true, command_navigation: true,
    gpu_started: false, dataset_transferred: false }));
} catch (error) {
  const page = browser.contexts()[0]?.pages().find(p => /tauri|localhost/.test(p.url()));
  if (page) console.error(JSON.stringify({ native_error_view: await page.locator('main').innerText() }));
  throw error;
} finally { await browser.close(); }
