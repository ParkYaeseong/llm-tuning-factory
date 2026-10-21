// Opt-in: connect only to a test instance launched with loopback WebView2 debugging.
// Offline and safe by default (Task 11 plan 11.3c). Opt-in flags:
//   FACTORY_SMOKE_LIVE_HF=1  also run the live Hugging Face metadata check (network)
//   FACTORY_SMOKE_FLOW=1     also run a simulated graph through the background controller; needs the seeded root
//                            (scripts/seed_acceptance_root.py --state-dir <app state>) and a state without executor
//                            profiles, saved credentials or an MLflow address
// It refuses before its first write unless the controller is absent and no graph run is active. It writes into the
// app's real state (projects, which cannot be deleted, the desktop_smoke dataset and the flow's records). It never
// calls forget_credentials on a state with saved credentials or executor profiles, starts the real controller only
// on a state without executor profiles, saved credentials or an MLflow address, creates only synthetic profiles
// (smoke-<suffix>) and deletes them, and stops every controller it started.
import { chromium } from "@playwright/test";
import { mkdirSync, readFileSync, writeFileSync } from "node:fs";
import { resolve } from "node:path";

const { version } = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8"));
const LIVE_HF = process.env.FACTORY_SMOKE_LIVE_HF === "1";
const FLOW = process.env.FACTORY_SMOKE_FLOW === "1";
const SEEDED_ROOT = "factory-acceptance/tiny-qwen2";
const SEEDED_MAX_LENGTH = 64;   // scripts/seed_acceptance_root.py MAX_LENGTH
// profiles.rs KEY_PATH_RELATIVE and main.rs PASTED_KEY_REFUSED (distinctive parts).
const KEY_PATH_RELATIVE = "SSH 키 파일은 이 PC의 전체 경로";
const PASTED_KEY_REFUSED = "키 내용이 아니라 키 파일 경로를 입력하세요";
const sleep = ms => new Promise(done => setTimeout(done, ms));

const browser = await chromium.connectOverCDP("http://127.0.0.1:9223");
const cleanup = [];   // run in reverse order before disconnecting, also after a failure
try {
  const page = browser.contexts()[0].pages().find(p => /tauri|localhost/.test(p.url()));
  if (!page) throw new Error("Native application WebView not found");
  page.setDefaultTimeout(60_000);
  // One native command; a rejected command (a native Err string) is returned, never thrown.
  const attempt = (command, args = {}) => page.evaluate(async ([command, args]) => {
    try { return { ok: true, value: await window.__TAURI_INTERNALS__.invoke(command, args) }; }
    catch (error) { return { ok: false, error: typeof error === "string" ? error : String(error?.message ?? error) }; }
  }, [command, args]);
  const invoke = async (command, args = {}) => {
    const answer = await attempt(command, args);
    if (!answer.ok) throw new Error(`${command}: ${answer.error}`);
    return answer.value;
  };
  const workflow = async (action, data = {}) => {
    const result = await invoke("workflow_action", { action, data });
    if (result?.status !== "ok") throw new Error(`${action}: ${JSON.stringify(result?.issues ?? result)}`);
    return result.data;
  };
  const controllerStatus = async () => {
    const result = await invoke("controller_status");
    if (result?.status !== "ok") throw new Error(`controller_status: ${JSON.stringify(result?.issues ?? result)}`);
    return result.data;
  };
  // While polling, a transient answer (status_busy: the job database is locked) is read again.
  const polledStatus = async () => {
    const result = await invoke("controller_status");
    return result?.status === "ok" ? result.data : null;
  };
  const waitFor = async (what, check, timeoutMs) => {
    const deadline = Date.now() + timeoutMs;
    for (;;) {
      const value = await check();
      if (value) return value;
      if (Date.now() > deadline) throw new Error(`Timed out waiting for ${what}`);
      await sleep(1000);
    }
  };
  const stopController = async () => {
    const stop = await invoke("controller_stop");
    if (stop?.status !== "ok") throw new Error(`controller_stop: ${JSON.stringify(stop?.issues ?? stop)}`);
    await waitFor("the controller to stop (absent)", async () => (await polledStatus())?.controller.state === "absent", 120_000);
    return stop.data;
  };
  const startController = async () => {
    const start = await invoke("controller_start");
    if (start.started !== true) throw new Error(`controller_start did not start: ${JSON.stringify(start)}`);
    cleanup.push(stopController);
    return waitFor("the controller to run", async () => {
      const status = await polledStatus();
      return status?.controller.state === "running" && status.current?.ok === true
        && status.current.owner_id === status.controller.owner_id ? status : null;
    }, 60_000);
  };
  const stopStartedController = async () => {
    const stop = await stopController();
    const index = cleanup.lastIndexOf(stopController);
    if (index >= 0) cleanup.splice(index, 1);
    return stop;
  };
  const visit = async (id, heading, screenshot) => {
    await page.locator(`nav [data-action="nav:${id}"]`).click();
    await page.getByRole("heading", { name: heading, exact: true, level: 1 }).waitFor();
    if (screenshot) await page.screenshot({ path: `test-results/${screenshot}`, fullPage: true });
  };

  // 1. Footer.
  await page.waitForFunction(() => !!window.__TAURI_INTERNALS__);
  await page.getByRole("heading", { name: "작업 공간", exact: true }).waitFor();
  await page.waitForFunction(() => !document.body.textContent.includes("작업 공간 불러오는 중"));
  await page.getByText(`Windows · 내부 검토 ${version}`, { exact: true }).waitFor();
  // Before the first write (the desktop_smoke dataset below): the background controller must be absent and no graph
  // run active, so a refused smoke leaves the state as it found it.
  const idle = await controllerStatus();
  if (idle.controller.state !== "absent")
    throw new Error(`The background controller must be absent before the smoke (state: ${idle.controller.state}); stop it in the app first`);
  if (idle.runs?.active !== 0) throw new Error(`Active graph runs present (${idle.runs?.active}); use a state without active runs`);

  // 2. Legacy one-request actions (unchanged; the Hugging Face block only with FACTORY_SMOKE_LIVE_HF=1).
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
  let modern = null;
  if (LIVE_HF) {
    modern = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke('factory_action', {
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
  }
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

  // 3. Legacy vault check, only on a state without saved credentials and without executor profiles:
  //    forget_credentials also deletes every executor-profile key (Task 9.5).
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByLabel("RunPod API 키").waitFor();
  const initial = await page.evaluate(() => window.__TAURI_INTERNALS__.invoke("settings_get"));
  const initialProfiles = (await invoke("executor_profiles_get")).profiles;
  if (!Array.isArray(initialProfiles)) throw new Error("executor_profiles_get returned no profile list");
  const savedCredentials = Object.keys(initial).some(key => key.endsWith("_saved") && initial[key]);
  let vaultVerified = "skipped_existing_credentials";
  if (!savedCredentials && initialProfiles.length === 0) {
    vaultVerified = false;
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

  // 4. Workflow channel (the controller was checked absent and no graph run active before the first write).
  const projects = await workflow("projects_list");
  if (!Array.isArray(projects.projects)) throw new Error("projects_list returned no project list");
  const projectName = `스모크 ${suffix}`;
  const created = await workflow("project_create", { request_id: `smoke-${suffix}`, name: projectName });
  if (created.project?.name !== projectName || !created.revision_sha256) throw new Error("project_create failed");
  if (!(await workflow("projects_list")).projects.some(p => p.project_id === created.project.project_id))
    throw new Error("The created project is not listed");
  const recipes = await workflow("recipes_list");
  if (!Array.isArray(recipes.recipes)) throw new Error("recipes_list returned no recipe list");

  // 5.-7. Only on a state without executor profiles: the profile, the controller and the flow use synthetic data only.
  //    The real controller (6, 7) gets the saved MLflow settings and could send pending tracking events, so it starts
  //    only when no credential is saved and no MLflow address is set; network_used: false then holds.
  const synthetic = initialProfiles.length === 0;
  const offline = synthetic && !savedCredentials && !String(initial.MLFLOW_TRACKING_URI ?? "").trim();
  const skipped = "skipped_existing_profiles";
  const results = { profiles: skipped, controller: skipped, flow: FLOW ? skipped : false };
  const profileId = `smoke-${suffix}`;
  const deleteSyntheticProfile = async () => {
    const deleted = await invoke("executor_profile_delete", { profileId });
    if (deleted.profile_id !== profileId) throw new Error("Profile delete failed");
    if ((await invoke("executor_profiles_get")).profiles.length !== 0) throw new Error("A synthetic profile was left");
    cleanup.splice(0, cleanup.length);   // nothing left to undo: no controller runs and every profile is gone
  };
  if (synthetic) {
    // 5. Profiles: one synthetic RunPod profile with a synthetic key; two drafts refused before anything is written.
    const syntheticKey = "synthetic-" + "runpod-" + "k".repeat(12) + "-" + suffix;
    cleanup.push(async () => { await invoke("executor_profile_delete", { profileId }); });
    const saved = await invoke("executor_profile_save", { profileId, values: {
      provider: "existing_runpod", name: `스모크 ${suffix}`, pod_id: `smoke-pod-${suffix}`,
      ssh_fingerprint: "SHA256:" + "A".repeat(43), ssh_key_file: resolve(`test-results/smoke-key-${suffix}`),
      RUNPOD_API_KEY: syntheticKey } });
    if (saved.profile?.profile_id !== profileId || saved.profile.RUNPOD_API_KEY_saved !== true) throw new Error("Profile save failed");
    const listed = await invoke("executor_profiles_get");
    const mine = listed.profiles.find(p => p.profile_id === profileId);
    if (mine?.RUNPOD_API_KEY_saved !== true) throw new Error("Profile key not saved");
    if (JSON.stringify(listed).includes(syntheticKey) || JSON.stringify(saved).includes(syntheticKey)) throw new Error("Profile key leaked");
    const relative = await attempt("executor_profile_save", { profileId: `smoke-ssh-${suffix}`, values: {
      provider: "ssh_server", name: "스모크 GPU 서버", server_ssh_key_file: "keys/id_ed25519" } });
    if (relative.ok) cleanup.push(async () => { await invoke("executor_profile_delete", { profileId: `smoke-ssh-${suffix}` }); });
    if (relative.ok || !relative.error.includes(KEY_PATH_RELATIVE)) throw new Error(`Relative key path not refused: ${relative.error}`);
    const pasted = await attempt("executor_profile_save", { profileId: `smoke-paste-${suffix}`, values: {
      provider: "existing_runpod", name: "스모크 키",
      ssh_key_file: "-----BEGIN " + "OPENSSH PRIVATE KEY-----\nsynthetic\n-----END " + "OPENSSH PRIVATE KEY-----" } });
    if (pasted.ok) cleanup.push(async () => { await invoke("executor_profile_delete", { profileId: `smoke-paste-${suffix}` }); });
    if (pasted.ok || !pasted.error.includes(PASTED_KEY_REFUSED)) throw new Error(`Pasted key not refused: ${pasted.error}`);
    if ((await invoke("executor_profiles_get")).profiles.map(p => p.profile_id).join() !== profileId)
      throw new Error("A refused draft was saved");
    results.profiles = { profile_saved: true, profile_key_masked: true, relative_key_refused: true, pasted_key_refused: true };
    if (!offline) {
      await deleteSyntheticProfile();
      results.controller = "skipped_saved_settings";
      if (FLOW) results.flow = "skipped_saved_settings";
    }
  }
  if (offline) {
    // 6. Controller: the real broker reads the key from Credential Manager in the background; D21 round trips; the
    //    stop dialog's focus while it runs; stop and drain; the synthetic profile is deleted.
    const running = await startController();
    const current = running.current;
    if (!Object.hasOwn(current.profiles ?? {}, profileId) || Object.hasOwn(current.skipped_profiles ?? {}, profileId))
      throw new Error(`The broker did not hand the profile key to the controller: ${JSON.stringify(current.skipped_profiles)}`);
    const again = await invoke("controller_start");
    if (again.started !== false || again.reason !== "already_running") throw new Error(`Second start not refused: ${JSON.stringify(again)}`);
    const samples = [];
    for (let i = 0; i < 5; i++) samples.push(await page.evaluate(async () => {
      const started = performance.now();
      await window.__TAURI_INTERNALS__.invoke("controller_status");
      return Math.round(performance.now() - started);
    }));
    const median = [...samples].sort((a, b) => a - b)[2];
    await visit("profiles", "실행 프로필");
    const stopSelector = '#controller-bar [data-action="wf:profiles:controller-stop"]';
    await page.waitForFunction(selector => document.querySelector(selector)?.disabled === false, stopSelector);
    await page.screenshot({ path: "test-results/native-controller-running.png", fullPage: true });
    await page.locator(stopSelector).click();
    const dialog = page.getByRole("dialog", { name: "컨트롤러 정지" });
    await dialog.waitFor();
    const headingFocused = await page.evaluate(() => {
      const active = document.activeElement;
      return active?.tagName === "H2" && !!active.closest("dialog[open]");
    });
    await page.keyboard.press("Escape");
    await dialog.waitFor({ state: "detached" });
    const focusReturned = await page.evaluate(() => document.activeElement?.getAttribute("data-action") === "wf:profiles:controller-stop");
    if (!headingFocused || !focusReturned) throw new Error(`Stop dialog focus: heading ${headingFocused}, returned ${focusReturned}`);
    const afterEsc = await controllerStatus();
    if (afterEsc.controller.state !== "running" || afterEsc.stop_requested) throw new Error("Esc on the stop dialog changed the controller");
    const stop = await stopStartedController();
    if (stop.requested !== true) throw new Error(`controller_stop did not request a stop: ${JSON.stringify(stop)}`);
    await deleteSyntheticProfile();   // the controller stopped
    results.controller = { started: true, broker_read_profile_key: true, second_start_refused: true,
      status_roundtrip_ms: samples, status_roundtrip_ms_median: median, stop_dialog_focus: true, stopped: true,
      profile_deleted: true };

    // 7. Flow (FACTORY_SMOKE_FLOW=1): blank project, mapped CSV, split, recipe, benchmark, train -> evaluate, a
    //    simulation plan submitted and completed by the background controller.
    if (FLOW) {
      const roots = await workflow("model_roots_list");
      const root = roots.roots.find(item => item.root.repo_id === SEEDED_ROOT && item.snapshot_ready && item.targets_ready);
      if (!root) throw new Error(`FACTORY_SMOKE_FLOW needs ${SEEDED_ROOT}: run scripts/seed_acceptance_root.py --state-dir <app state> first`);
      const id = `smoke-flow-${suffix}`;
      const csv = resolve(`test-results/${id}.csv`);
      writeFileSync(csv, "question,context,answer\n" + Array.from({ length: 12 }, (_, i) =>
        `질문 ${i + 1},문맥 ${i + 1},답 ${i + 1}\n`).join(""));
      const project = await workflow("project_create", { request_id: id, name: `스모크 흐름 ${suffix}`,
        purpose: "문맥으로 답하기", task_type: "qa" });
      const imported = await workflow("dataset_import_source", { path: csv });
      const mapping = { source_format: "csv", encoding: "utf-8",
        fields: { instruction: ["question"], input: ["context"], output: ["answer"] } };
      const shown = await workflow("dataset_import_preview", { source_id: imported.source_id, mapping });
      const registered = await workflow("dataset_import_register", { source_id: imported.source_id, mapping,
        preview_sha256: shown.preview_sha256, dataset_id: id, version: "1", synthetic: true, export_allowed: false });
      const split = await workflow("dataset_split_preview", { plan: { dataset: registered.dataset, seed: 42,
        train_basis_points: 8000, validation_basis_points: 1000, test_basis_points: 1000,
        outputs: Object.fromEntries(["train", "validation", "test"].map(role => [role, { dataset_id: `${id}-${role}`, version: "1" }])) } });
      const committed = await workflow("dataset_split_commit", { plan_sha256: split.plan_sha256 });
      // The seeded root's reviewed limit is 64 tokens (seed_acceptance_root.MAX_LENGTH); the recipe default is 1024.
      const recipe = await workflow("recipe_save", { spec: { recipe_id: id, name: "스모크 SFT LoRA",
        hyperparameters: { cutoff_len: SEEDED_MAX_LENGTH } }, expected_revision: null });
      const validation = committed.outputs.validation;
      const benchmark = await workflow("benchmark_save", { spec: { dataset: validation, evaluator_kind: "text",
        evaluator_version: "1", ordered_sample_ids: Array.from({ length: split.counts.validation }, (_, i) => `row:${i}`),
        max_new_tokens: 16, cutoff_len: SEEDED_MAX_LENGTH, seed: 42, precision: "bf16", quantization_bit: null,
        tokenizer_fingerprint: root.root.tokenizer_fingerprint, template: root.profile.template, semantic_model_kind: "causal_lm" } });
      const estimate = "estimate-local";
      const graph = { nodes: [
        { kind: "train", id: "train", label: "학습", recipe_revision: recipe.revision_sha256,
          model_input: { policy: { base: root.root }, initialization: "new_adapter" }, dataset_binding: "train",
          executor_profile_ids: [estimate] },
        { kind: "evaluate", id: "evaluate", label: "평가", model: { node_id: "train", output_name: "model" },
          benchmark_revision: benchmark.revision_sha256, executor_profile_ids: [estimate] }] };
      const datasets = ["train", "validation", "test"].map(role => ({ binding_id: role, role, dataset: committed.outputs[role] }));
      await workflow("project_save", { spec: { ...project.project, datasets, graph }, expected_revision: project.revision_sha256 });
      await workflow("graph_validate", { project_id: project.project.project_id });
      const request = { project_id: project.project.project_id, simulation: true,
        node_resources: [{ node_id: "train", timeout_seconds: 1800 }, { node_id: "evaluate", timeout_seconds: 600 }],
        estimates: [{ profile_id: estimate, gpu_type: "A100", gpu_count: 1, total_hourly_cost_usd: 1 }] };
      // The plan pins the estimate's observation time, so two real-clock plans differ: check the stored plan instead.
      const planned = await workflow("graph_plan", request);
      if ((await workflow("graph_plan_get", { plan_sha256: planned.plan_sha256 })).plan_sha256 !== planned.plan_sha256)
        throw new Error("graph_plan_get does not return the stored plan");
      const submitted = await workflow("graph_submit", { plan_sha256: planned.plan_sha256, request_id: id });
      if (submitted.simulation !== true) throw new Error("The flow plan is not a simulation");
      await startController();
      const finished = await waitFor("the simulated run to finish", async () => {
        const run = (await workflow("graph_run_get", { run_id: submitted.run_id })).run;
        return ["COMPLETED", "FAILED", "CANCELED"].includes(run.status) ? run : null;
      }, 600_000);
      if (finished.status !== "COMPLETED") throw new Error(`The simulated run ended ${finished.status}: ${JSON.stringify(finished)}`);
      await stopStartedController();
      results.flow = { run_id: submitted.run_id, plan_sha256: planned.plan_sha256, run_completed: true, simulation: true,
        controller_stopped: true };
    }
  }

  // 8. Workflow pages.
  await visit("projects", "프로젝트", "native-projects.png");
  await visit("profiles", "실행 프로필", "native-profiles.png");
  await visit("runs", "그래프 실행", "native-graph-runs.png");
  await visit("overview", "작업 공간", "native-overview.png");

  // 9. One JSON line.
  console.log(JSON.stringify({ native_ui: true, app_version: version, bundled_catalog: catalog.templates.length,
    dataset_registered_locally: true, dry_run: plan.dry_run, worker_lock_present: true, native_vault_masked: vaultVerified,
    native_s3_vault_masked: vaultVerified, training_stage_catalog: catalog.training_stages.length, ppo_guard: true,
    live_hf: LIVE_HF, modern_hf_model_registered: modern?.repo_id ?? null, modern_hf_revision: modern?.revision ?? null,
    native_hf_vault_masked: vaultVerified, native_dataset_preview: true, command_navigation: true,
    workflow_channel: true, workflow_project_created: true, controller_absent_before: true,
    executor_profiles: results.profiles, background_controller: results.controller,
    status_roundtrip_ms_median: results.controller.status_roundtrip_ms_median ?? null, flow: results.flow,
    workflow_pages: ["projects", "profiles", "runs", "overview"],
    gpu_started: false, dataset_transferred: false, network_used: LIVE_HF }));
} catch (error) {
  const page = browser.contexts()[0]?.pages().find(p => /tauri|localhost/.test(p.url()));
  if (page) console.error(JSON.stringify({ native_error_view: await page.locator('main').innerText().catch(() => null) }));
  throw error;
} finally {
  for (const undo of cleanup.reverse()) {
    try { await undo(); }
    catch (error) { console.error(JSON.stringify({ native_cleanup_error: String(error?.message ?? error) })); }
  }
  await browser.close();
}
