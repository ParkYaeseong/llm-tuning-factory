import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
const packageVersion = JSON.parse(readFileSync(new URL("../package.json", import.meta.url), "utf8")).version;
test.beforeEach(async ({ page }) => {
  await page.addInitScript(({ config }) => {
    const calls: any[] = [];
    const runs = [
      { id: "a", ai_id: "paper", status: "FINISHED", operator: "<img src=x onerror=alert(1)>",
        metrics: { accuracy: .5 }, config: {...config, ai_id: "paper"}, validation_sha256: "v1", simulation: false },
      { id: "b", ai_id: "paper", status: "FINISHED", operator: "tester",
        metrics: { accuracy: .8 }, config: {...config, ai_id: "paper"}, validation_sha256: "v2", simulation: false },
    ];
    (window as any).__testCalls = calls;
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args });
      if (command === "settings_get") return {};
      if (command === "settings_save") return { ...Object.fromEntries(Object.entries(args.values).filter(([key]) => !/SECRET|TOKEN|ACCESS_KEY|API_KEY/.test(key))), ...(args.values.RUNPOD_API_KEY ? { RUNPOD_API_KEY_saved: true } : {}), ...(args.values.S3_ACCESS_KEY_ID ? { S3_ACCESS_KEY_ID_saved: true } : {}), ...(args.values.S3_SECRET_ACCESS_KEY ? { S3_SECRET_ACCESS_KEY_saved: true } : {}), ...(args.values.S3_SESSION_TOKEN ? { S3_SESSION_TOKEN_saved: true } : {}) };
      if (command === "pick_file") return "C:\\data\\dummy.json";
      if (command === "factory_action") {
        const { action, data } = args;
        if (action === "capabilities") {
          const stage = data.config.training_stage || "sft";
          const method = data.config.training_method;
          const invalid = stage === "ppo" && (!data.config.stage_options?.reward_model || !data.config.stage_options?.reward_model_sha256);
          return { valid: !invalid, errors: invalid ? ["PPO에는 보상 모델과 목록 해시가 필요합니다."] : [], warnings: ["합성 예제 외의 GPU 검증이 필요합니다."], supported_stages: ["pt", "sft", "rm", "ppo", "dpo", "kto"], methods: ["full", "freeze", "lora", "qlora", "oft"], quantization_bits: [4, 8], model: { id: data.config.base_model, template: "qwen", gpu_verified: false } };
        }
        if (action === "s3_list") return { objects: data.continuation_token ? [{ key: "second.jsonl", size: 42, etag: '"v2"' }] : [{ key: "training.jsonl", size: 512, etag: '"v1"', last_modified: "2026-10-15T00:00:00Z" }], common_prefixes: ["folder/"], next_token: data.continuation_token ? null : "page-2" };
        if (action === "s3_head") return { bucket: data.bucket, key: data.key, size: 512, etag: '"v1"', version_id: "immutable-v1", checksum_sha256: "synthetic-base64-sha256", checksum_type: "FULL_OBJECT", free_bytes: 102400, import_max_bytes: 1048576 };
        if (action === "s3_import") return { dataset_id: data.dataset_id, version: data.version, rows: 2, sha256: "a".repeat(64) };
        if (action === "catalog") return { models: [{ id: "Qwen/Qwen2.5-0.5B-Instruct", template: "qwen", architecture: "qwen2", gpu_verified: true }, { id: "example/ServerCatalogModel", template: "llama3", architecture: "llama", gpu_verified: false }], training_stages: [
          { id: "pt", label: "PT · 사전 학습", dataset_kind: "text" }, { id: "sft", label: "SFT · 지도 학습", dataset_kind: "instruction" }, { id: "rm", label: "RM · 보상 학습", dataset_kind: "preference" }, { id: "ppo", label: "PPO · 정책 최적화", dataset_kind: "prompt", available: false, reason: "고정 엔진 의존성 미지원" }, { id: "dpo", label: "DPO · 선호 학습", dataset_kind: "preference" }, { id: "kto", label: "KTO · 선호 라벨 학습", dataset_kind: "kto" }], templates: [{ name: "paper_extraction", config }], profiles: [],
          datasets: [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true }] };
        if (action === "jobs") return [];
        if (action === "dataset_preview") return { rows: 6, format: "alpaca", duplicate_rows: 2, samples: [{ instruction: "<sample>", output: "answer" }], bytes: 512 };
        if (action === "plan") return { dry_run: true, manifest: { config: data.config, dataset: { dataset_id: "dummy", rows: 2 } }, estimated_cost_usd: .5 };
        if (action === "history") return data.experiment_id ? { runs, next_page_token: null } : { experiments: [{ id: "16", name: "test" }] };
        if (action === "compare") return { comparable: false, reason: "동일 업무·검증 데이터·평가 방식의 실제 실행만 지표를 비교합니다.", deltas: {}, parameter_changes: [{ parameter: "hyperparameters.lora_rank", base: 8, candidate: 16 }] };
        if (action === "submit") return { id: "c".repeat(32) };
        return {};
      }
    } };
  }, { config });
  await page.goto("/");
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
});

test('HF model search imports an immutable modern profile and disables unsupported stages', async ({ page }) => {
  await page.evaluate(() => {
    const native = (window as any).__TAURI_INTERNALS__;
    const original = native.invoke;
    let registered: any = null;
    const profile = { repo_id: 'Qwen/Qwen3.5-0.8B', revision: 'c'.repeat(40), config_sha256: 'd'.repeat(64),
      architecture: 'qwen3_5', template: 'qwen3_5_nothink', max_length: 32768, context_length: 262144,
      hidden_layers: 24, recommended_precision: 'bfloat16', gated: false };
    native.invoke = async (command: string, args: any) => {
      if (command === 'factory_action') {
        if (args.action === 'model_search') return { models: [{ id: profile.repo_id, downloads: 100, pipeline_tag: 'image-text-to-text' }] };
        if (args.action === 'model_inspect') return { repo_id: profile.repo_id, revision: profile.revision,
          architecture: profile.architecture, supported: true, reasons: [], profile, supported_stages: ['sft'] };
        if (args.action === 'model_import') { registered = { id: profile.repo_id, template: profile.template,
          registration: profile, gpu_verified: false }; return { model: registered, profile }; }
        if (args.action === 'catalog') { const result = await original(command, args); if (registered) result.models.push(registered); return result; }
        if (args.action === 'capabilities' && args.data.config.model_profile) return { valid: true, errors: [], warnings: ['GPU validation pending'],
          supported_stages: ['sft'], methods: ['lora', 'qlora', 'full', 'freeze'], dataset_kind: 'instruction', engine_version: '0.9.5' };
      }
      return original(command, args);
    };
  });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel('HF 모델 검색').fill('Qwen3.5');
  await page.locator('[data-action="hub-search"]').click();
  await page.locator('[data-action="hub-inspect:0"]').click();
  await page.locator('[data-action="hub-import"]').click();
  await expect(page.getByLabel('기반 모델', {exact:true})).toHaveValue('Qwen/Qwen3.5-0.8B');
  await expect(page.getByLabel('학습 단계', {exact:true}).locator('option[value="ppo"]')).toHaveJSProperty('disabled', true);
  await page.locator('#training-form').getByRole('button', { name: '실행 계획 확인', exact:true }).click();
  const data = await page.evaluate(() => (window as any).__testCalls.filter((x:any) => x.args?.action === 'plan').at(-1).args.data.config);
  expect(data.model_profile.repo_id).toBe('Qwen/Qwen3.5-0.8B');
  expect(data.model_revision).toBe('c'.repeat(40));
  expect(data.architecture).toBeUndefined();
});
test("connection wizard masks secrets and persists via native command", async ({ page }) => {
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByLabel("RunPod API 키").fill("synthetic-secret");
  await page.getByLabel("기록에 표시할 이름").fill("검토자");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect(page.getByLabel("RunPod API 키")).toHaveValue("");
  await expect(page.getByText("보안 저장됨 · 변경할 때만 입력")).toBeVisible();
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});

test("workbench displays the distributed package version", async ({ page }) => {
  await expect(page.locator(".sidebar-foot small")).toHaveText(`Windows · 내부 검토 ${packageVersion}`);
});
test("plan is offline and execution requires separate explicit confirmation", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "execute"))).toBe(false);
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("busy GPU keeps a submitted job queued without announcing completion", async ({ page }) => {
  await page.evaluate(config => {
    const bridge = (window as any).__TAURI_INTERNALS__;
    const original = bridge.invoke;
    let submitted = false;
    bridge.invoke = async (command: string, args: any) => {
      if (command === "factory_action") {
        if (args.action === "submit") submitted = true;
        if (args.action === "execute") return null;
        if (args.action === "jobs" && submitted) return [{ id: "c".repeat(32), status: "QUEUED",
          attempts: 0, manifest: { config, simulation: false } }];
      }
      return original(command, args);
    };
  }, config);
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("작업은 대기 중입니다");
  await expect(page.getByText("학습 처리가 끝났습니다.", { exact: false })).toHaveCount(0);
  await expect(page.locator(".badge").first()).toHaveText("대기");
});
test("changed settings invalidate an already displayed execution plan", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).fill("3");
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).blur();
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});
test("shared history escapes data and refuses invalid metric deltas", async ({ page }) => {
  await page.locator('nav [data-action="nav:history"]').click();
  await page.getByRole("button", { name: "기록 불러오기", exact: true }).click();
  await expect(page.getByText("<img src=x onerror=alert(1)>", { exact: true })).toBeVisible();
  await page.locator("[data-run=a]").check(); await page.locator("[data-run=b]").check();
  await page.getByRole("button", { name: "선택한 두 실행 비교" }).click();
  await expect(page.getByText("동일 업무·검증 데이터·평가 방식의 실제 실행만 지표를 비교합니다.", { exact: true })).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  await page.screenshot({ path: "test-results/history.png", fullPage: true });
});

test("command search supports keyboard navigation without executing a job", async ({ page }) => {
  await page.screenshot({ path: "test-results/workspace-redesign.png", fullPage: true });
  await page.keyboard.press("Control+k");
  const dialog = page.getByRole("dialog");
  await expect(dialog).toBeVisible();
  await dialog.getByRole("textbox").fill("학습");
  await dialog.getByRole("button", { name: "학습 설정" }).click();
  await expect(page.getByRole("heading", { name: "학습 설정", exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("dataset inspection shows real samples before registration", async ({ page }) => {
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.getByRole("button", { name: "파일 찾기", exact: true }).click();
  await page.getByRole("button", { name: "샘플 미리보기", exact: true }).click();
  await expect(page.getByText("중복 레코드 2건", { exact: false })).toBeVisible();
  await expect(page.getByText("<sample>", { exact: false })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "dataset"))).toBe(false);
  await page.screenshot({ path: "test-results/dataset-redesign.png", fullPage: true });
});

test("loaded history search filters and comparison highlights changed settings", async ({ page }) => {
  await page.locator('nav [data-action="nav:history"]').click();
  await page.getByRole("button", { name: "기록 불러오기", exact: true }).click();
  await page.getByPlaceholder("업무 AI, 실행자, 실행 ID 검색").fill("tester");
  await expect(page.locator("[data-run]")).toHaveCount(1);
  await page.getByPlaceholder("업무 AI, 실행자, 실행 ID 검색").fill("");
  await page.locator("[data-run=a]").check(); await page.locator("[data-run=b]").check();
  await page.getByRole("button", { name: "선택한 두 실행 비교" }).click();
  await expect(page.getByText("hyperparameters.lora_rank", { exact: true })).toBeVisible();
  await page.screenshot({ path: "test-results/comparison-redesign.png", fullPage: true });
  await page.getByRole("button", { name: "이 설정으로 새 실험", exact: true }).last().click();
  await expect(page.getByRole("heading", { name: "학습 설정", exact: true })).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("progress graph only appears after real progress payload is requested", async ({ page }) => {
  await page.evaluate(({ config }) => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action === "jobs") return [
        {id: "a".repeat(32), status: "COMPLETED", attempts: 1, updated: 1791960000,
         manifest: {config, simulation: false}},
      ];
      if (command === "factory_action" && args.action === "job_progress") return args.data.remote
        ? {available: true, current_step: 12, total_steps: 12, percent: 100, loss: .41,
          points: [{step: 1, loss: 2.1}, {step: 12, loss: .41}]}
        : {available: false, points: []};
      return original(command, args);
    };
  }, {config});
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByRole("button", { name: "선택", exact: true }).click();
  await expect(page.getByRole("img", {name: "실제 train loss 추이"})).toHaveCount(0);
  await page.getByRole("button", { name: "원격 진행 지표", exact: true }).click();
  await expect(page.getByRole("img", {name: "실제 train loss 추이"})).toBeVisible();
});


test("training stages and methods use catalog and expose typed options", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await expect(page.getByLabel("기반 모델", { exact: true }).locator("option")).toHaveCount(2);
  await page.getByLabel("학습 단계", { exact: true }).selectOption("dpo");
  await expect(page.getByLabel("선호 학습 loss", { exact: true })).toBeVisible();
  await page.getByLabel("선호 학습 loss", { exact: true }).selectOption("simpo");
  await expect(page.getByLabel("SimPO gamma", { exact: true })).toBeVisible();
  await page.getByLabel("학습 방법", { exact: true }).selectOption("freeze");
  await expect(page.getByLabel("학습할 레이어 수", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  const data = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "plan").args.data.config);
  expect(data.training_stage).toBe("dpo"); expect(data.training_method).toBe("freeze");
  expect(data.stage_options.pref_loss).toBe("simpo");
  await page.getByLabel("학습률", { exact: true }).fill("0.0002");
  await page.getByLabel("학습률", { exact: true }).blur();
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("invalid model stage method combination blocks planning with a reason", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel("학습 단계", { exact: true }).selectOption("ppo");
  await expect(page.getByLabel("보상 모델 원격 경로", { exact: true })).toBeVisible();
  await expect(page.getByText("PPO에는 보상 모델과 목록 해시가 필요합니다.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "실행 계획 확인", exact: true })).toBeDisabled();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "plan"))).toBe(false);
});

test("S3 secrets are masked and saved separately from public connection fields", async ({ page }) => {
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByLabel("S3 Access Key ID", { exact: true }).fill("synthetic-access");
  await page.getByLabel("S3 Secret Access Key", { exact: true }).fill("synthetic-secret");
  await page.getByLabel("S3 Session Token", { exact: true }).fill("synthetic-token");
  await expect(page.getByLabel("S3 Secret Access Key", { exact: true })).toHaveAttribute("type", "password");
  await page.getByRole("button", { name: "S3 연결 정보 저장", exact: true }).click();
  await expect(page.getByLabel("S3 Access Key ID", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("S3 Secret Access Key", { exact: true })).toHaveValue("");
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});

test("S3 browse paginates and exact key import requires a separate confirmation", async ({ page }) => {
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.getByLabel("S3 버킷", { exact: true }).fill("synthetic-bucket");
  await page.getByRole("button", { name: "S3 목록 조회", exact: true }).click();
  await expect(page.getByText("training.jsonl", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "다음 S3 목록", exact: true }).click();
  await expect(page.getByText("second.jsonl", { exact: true })).toBeVisible();
  await page.getByLabel("S3 객체 key · 직접 입력 가능", { exact: true }).fill("restricted/training.jsonl");
  await page.getByRole("button", { name: "선택한 객체 확인", exact: true }).click();
  await expect(page.getByText("immutable-v1", { exact: false })).toBeVisible();
  await page.getByRole("button", { name: "S3에서 PC로 가져오기", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("RunPod");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "s3_import"))).toBe(false);
  await page.getByRole("button", { name: "S3에서 PC로 가져오기", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  const payload = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "s3_import").args.data);
  expect(payload.expected).toEqual({ bucket: "synthetic-bucket", key: "restricted/training.jsonl", etag: '\"v1\"', version_id: "immutable-v1", size: 512, checksum_sha256: "synthetic-base64-sha256", checksum_type: "FULL_OBJECT" });
  expect(payload.key).toBe("restricted/training.jsonl"); expect(payload.approved).toBe(true);
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});


test("S3 folder navigation requests the selected prefix", async ({ page }) => {
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.getByLabel("S3 버킷", { exact: true }).fill("synthetic-bucket");
  await page.getByRole("button", { name: "S3 목록 조회", exact: true }).click();
  await page.getByRole("button", { name: "폴더 열기", exact: true }).click();
  await expect(page.getByLabel("S3 Prefix · 폴더", { exact: true })).toHaveValue("folder/");
  const request = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "s3_list").args.data);
  expect(request.prefix).toBe("folder/");
});

test("model changes use catalog metadata and invalidate pinned revisions", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel("기반 모델", { exact: true }).selectOption("example/ServerCatalogModel");
  await page.getByText("고급 설정", { exact: true }).click();
  await expect(page.getByLabel("모델 템플릿 · 모델에서 자동 적용", { exact: true })).toHaveValue("llama3");
  await expect(page.getByLabel("모델 revision · 40자리 commit", { exact: true })).toHaveValue("");
  await expect(page.getByLabel("토크나이저 revision · 모델과 일치", { exact: true })).toHaveValue("");
});

test("typed blank options stay null and quantization bits stay numeric", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel("학습 방법", { exact: true }).selectOption("freeze");
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  let data = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "plan").args.data.config);
  expect(data.finetuning_options.freeze_extra_modules).toBeNull();
  await page.getByLabel("학습 방법", { exact: true }).selectOption("qlora");
  await page.getByLabel("양자화 비트", { exact: true }).selectOption("8");
  await page.getByText("rsLoRA 사용", { exact: true }).click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  data = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "plan").args.data.config);
  expect(data.quantization_bit).toBe(8); expect(data.finetuning_options.lora_alpha).toBeNull();
  expect(data.finetuning_options.use_rslora).toBe(true);
});

test("PPO runtime preparation explicitly confirms and carries selected stage", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel("학습 단계", { exact: true }).selectOption("ppo");
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await expect(page.getByRole("dialog")).toBeVisible();
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "runtime_prepare"))).toBe(false);
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  const request = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "runtime_prepare").args.data);
  expect(request).toEqual({ approved: true, training_stage: "ppo" });
});

test("RunPod runtime preparation names the cleanup and reports what it freed or skipped", async ({ page }) => {
  // Freed space is the free-space delta of the volume: removed venvs share hard-linked files with uv-cache.
  const report = { ready: true, runtime_state: "ready", pod_id: "borrowed",
    cleanup: { removed: ["b".repeat(16), "c".repeat(16) + "-ppo"], kept_in_use: ["d".repeat(16)], errors: [],
      free_before: 20 * 1024 ** 3, free_after: 23.5 * 1024 ** 3 } };
  await overrideActions(page, { runtime_prepare: report });
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다");
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("이전 앱 버전의 학습 환경 2개를 지워 3.5GB를 확보했습니다.");
  await expect(page.locator(".notice")).not.toContainText("지우지 못했습니다");
  // Some removed, one failed and the time budget ran out: what was removed still shows, with short notes.
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: ["b".repeat(16)], kept_in_use: [],
    errors: ["c".repeat(16)], incomplete: true, free_before: 20 * 1024 ** 3, free_after: 21 * 1024 ** 3 } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("이전 앱 버전의 학습 환경 1개를 지워 1.0GB를 확보했습니다.");
  await expect(page.locator(".notice")).toContainText("일부 이전 학습 환경은 지우지 못했습니다(학습 환경 준비에는 영향이 없습니다).");
  await expect(page.locator(".notice")).toContainText("남은 이전 학습 환경은 다음 학습 환경 준비 때 정리합니다.");
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: ["b".repeat(16)], kept_in_use: [],
    cleanup_error: "ValueError: unreadable manifest", free_before: 20 * 1024 ** 3, free_after: 19 * 1024 ** 3 } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  // Less than 0.1 GB freed (here the volume even lost space): the count alone, without a GB figure.
  await expect(page.locator(".notice")).toContainText("이전 앱 버전의 학습 환경 1개를 지웠습니다.");
  await expect(page.locator(".notice")).not.toContainText("GB를 확보");
  await expect(page.locator(".notice")).toContainText("일부 이전 학습 환경은 지우지 못했습니다");
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: ["b".repeat(16)], kept_in_use: [],
    errors: [], free_before: 20 * 1024 ** 3, free_after: 20.05 * 1024 ** 3 } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).not.toContainText("지우지 못했습니다");   // this preparation's notice
  await expect(page.locator(".notice")).toContainText("이전 앱 버전의 학습 환경 1개를 지웠습니다.");
  await expect(page.locator(".notice")).not.toContainText("GB를 확보");
  // Nothing removed because every removal failed: not a skipped cleanup.
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: [], kept_in_use: [],
    errors: ["b".repeat(16)], free_before: 20 * 1024 ** 3, free_after: 20 * 1024 ** 3 } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("이전 학습 환경을 지우지 못했습니다(학습 환경 준비에는 영향이 없습니다).");
  await expect(page.locator(".notice")).not.toContainText("건너뛰었습니다");
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: [], kept_in_use: [], cleanup_error: "cleanup report missing" } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("이전 학습 환경 정리는 건너뛰었습니다(학습 환경 준비에는 영향이 없습니다).");
  await expect(page.locator(".notice")).not.toContainText("지우지 못했습니다");
  await overrideActions(page, { runtime_prepare: { ...report, cleanup: { removed: [], kept_in_use: [] } } });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("학습 환경과 CUDA를 확인했습니다.");
  await expect(page.locator(".notice")).not.toContainText("이전");
});

test("changed S3 object clears its inspected version and access errors prevent import", async ({ page }) => {
  await page.evaluate(() => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action === "s3_head" && args.data.key === "denied.jsonl") throw new Error("S3 AccessDenied: 읽기 권한이 없습니다.");
      return original(command, args);
    };
  });
  await page.locator('nav [data-action="nav:datasets"]').click();
  await page.getByLabel("S3 버킷", { exact: true }).fill("synthetic-bucket");
  await page.getByLabel("S3 객체 key · 직접 입력 가능", { exact: true }).fill("training.jsonl");
  await page.getByRole("button", { name: "선택한 객체 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "S3에서 PC로 가져오기", exact: true })).toBeEnabled();
  await page.getByLabel("S3 객체 key · 직접 입력 가능", { exact: true }).fill("denied.jsonl");
  await expect(page.getByRole("button", { name: "S3에서 PC로 가져오기", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "선택한 객체 확인", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("읽기 권한이 없습니다");
  await expect(page.getByRole("button", { name: "S3에서 PC로 가져오기", exact: true })).toBeDisabled();
});


test("BCO weight belongs to DPO while KTO exposes chosen and rejected weights", async ({ page }) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByLabel("학습 단계", { exact: true }).selectOption("dpo");
  await expect(page.getByLabel("BCO 가중치", { exact: true })).toBeVisible();
  await page.getByLabel("학습 단계", { exact: true }).selectOption("kto");
  await expect(page.getByLabel("BCO 가중치", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Chosen 가중치", { exact: true })).toBeVisible();
  await expect(page.getByLabel("Rejected 가중치", { exact: true })).toBeVisible();
});


test("jobs explain and disable unsupported checkpoint resume using capabilities", async ({ page }) => {
  await page.evaluate(config => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action === "jobs") return [{ id: "e".repeat(32), status: "FAILED", attempts: 1, manifest: { config: { ...config, training_stage: "ppo" }, simulation: false } }];
      if (command === "factory_action" && args.action === "capabilities" && args.data.config.training_stage === "ppo") return { valid: true, errors: [], warnings: [], methods: ["lora"], resume_supported: false };
      return original(command, args);
    };
  }, config);
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByRole("button", { name: "선택", exact: true }).click();
  await expect(page.getByRole("button", { name: "체크포인트에서 재개", exact: true })).toBeDisabled();
  await expect(page.getByText("이 학습 단계·방식은 체크포인트 재개를 지원하지 않습니다. 회수 가능한 결과는 재연결로 확인하세요.", { exact: true })).toBeVisible();
});


test("model revision metadata stays outside strict training config and clears on model change", async ({ page }) => {
  await page.evaluate(() => {
    const original = (window as any).__TAURI_INTERNALS__.invoke;
    (window as any).__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action === "model_revision") return { model_revision: "b".repeat(40), tokenizer_revision: "b".repeat(40), template: "qwen", architecture: "qwen2", max_length: 32768, recommended_precision: "bf16", hidden_layers: 24, metadata_verified: true };
      return original(command, args);
    };
  });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "선택한 모델 버전 확인·고정", exact: true }).click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  const data = await page.evaluate(() => (window as any).__testCalls.findLast((c: any) => c.args?.action === "plan").args.data.config);
  expect(data.model_revision).toBe("b".repeat(40)); expect(data.template).toBe("qwen");
  for (const key of ["architecture", "max_length", "recommended_precision", "hidden_layers", "metadata_verified"]) expect(data).not.toHaveProperty(key);
  await expect(page.getByText("고정 버전 메타데이터 · 확인됨", { exact: true })).toBeVisible();
  await expect(page.getByText("모델 최대 길이 32,768 토큰 · 현재 설정을 자동으로 변경하지 않습니다.", { exact: true })).toBeVisible();
  await expect(page.getByLabel("최대 토큰 길이", { exact: true })).toHaveValue("1024");
  await page.getByLabel("기반 모델", { exact: true }).selectOption("example/ServerCatalogModel");
  await expect(page.getByText("고정 버전 메타데이터 · 확인됨", { exact: true })).toHaveCount(0);
});

async function overrideActions(page: import("@playwright/test").Page, answers: Record<string, unknown>) {
  await page.evaluate(answers => {
    const bridge = (window as any).__TAURI_INTERNALS__;
    const original = bridge.invoke;
    (window as any).__overridden = [];
    bridge.invoke = async (command: string, args: any) => {
      if (command === "factory_action" && args.action in answers) {
        (window as any).__overridden.push(args);
        const answer = answers[args.action];
        if (answer && (answer as any).__reject) throw new Error((answer as any).__reject);
        if (answer && (answer as any).__hold) await new Promise<void>(resolve => { ((window as any).__releases ||= []).push(resolve); });
        return args.action === "plan" ? { ...(await original(command, args)), ...(answer as object) } : answer;
      }
      return original(command, args);
    };
  }, answers);
}

test("plan lists escaped GPU compatibility findings from the stored observation", async ({ page }) => {
  await overrideActions(page, { plan: { compatibility: { observed_at: 1793249000, findings: [
    { level: "block", code: "GPU_TYPE_MISMATCH", message: "학습 설정의 GPU 종류(<b>A4000</b>)가 다릅니다." },
    { level: "warn", code: "MODEL_UNVERIFIED", message: "이 모델은 아직 GPU에서 검증되지 않았습니다." }] } } });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  const section = page.getByTestId("plan-compatibility");
  await expect(section).toContainText("실행 시 거부될 항목");
  await expect(section).toContainText("학습 설정의 GPU 종류(<b>A4000</b>)가 다릅니다.");
  await expect(section).toContainText("주의 항목");
  await expect(section.locator("b")).toHaveCount(0);
});

test("blocking preflight findings stop the run before approval or submit", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: true, runtime_state: "ready", findings: [
    { level: "block", code: "GPU_ARCH_UNSUPPORTED", message: "이 GPU는 지원하지 않는 세대입니다." }] } });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("alert")).toContainText("이 GPU는 지원하지 않는 세대입니다.");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await page.evaluate(() => (window as any).__testCalls.some((c: any) => c.args?.action === "submit"))).toBe(false);
});

test("a missing RunPod runtime is folded into the single run approval", async ({ page }) => {
  await overrideActions(page, {
    runtime_check: { ready: false, runtime_state: "missing", findings: [
      { level: "warn", code: "MEMORY_TIGHT", message: "예상 메모리가 GPU 메모리에 빠듯합니다." }] },
    execute: { __hold: true } });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("학습 환경이 없어 학습 전에 먼저 설치합니다");
  await expect(dialog).toContainText("이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다");
  await expect(dialog).toContainText("예상 메모리가 GPU 메모리에 빠듯합니다.");
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.getByText("Pod에 학습 환경을 준비한 뒤 학습을 시작합니다. 최대 30분 걸릴 수 있으며, 쓰지 않는 이전 앱 버전 학습 환경을 정리합니다.", { exact: true })).toBeVisible();
  await expect.poll(() => page.evaluate(() => (window as any).__overridden
    .find((args: any) => args.action === "execute")?.data?.prepare_runtime)).toBe(true);
  const check = await page.evaluate(() => (window as any).__overridden.find((args: any) => args.action === "runtime_check"));
  expect(check.data.config.base_model).toBeTruthy();
});

const startRun = async (page: import("@playwright/test").Page) => {
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
};
const calledActions = (page: import("@playwright/test").Page) => page.evaluate(() => [
  ...(window as any).__testCalls.map((c: any) => c.args?.action), ...(window as any).__overridden.map((a: any) => a.action)]);

test("a preparation in the run approval reports what it pruned with the result", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: false, runtime_state: "missing", findings: [] },
    execute: { id: "c".repeat(32), status: "COMPLETED", runtime_cleanup: { removed: ["b".repeat(16)], kept_in_use: [],
      errors: [], free_before: 10 * 1024 ** 3, free_after: 12 * 1024 ** 3 } } });
  await startRun(page);
  await expect(page.getByRole("dialog")).toContainText("이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다");
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("학습 처리가 끝났습니다.");
  await expect(page.locator(".notice")).toContainText("이전 앱 버전의 학습 환경 1개를 지워 2.0GB를 확보했습니다.");
});

test("a ready environment is approved without preparing the runtime", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: true, runtime_state: "ready", findings: [] }, execute: null });
  await startRun(page);
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__overridden
    .find((args: any) => args.action === "execute")?.data?.prepare_runtime)).toBe(false);
});

test("an unavailable GPU stops the run before approval or submit", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: false, runtime_state: "unavailable", findings: [] } });
  await startRun(page);
  await expect(page.getByRole("alert")).toContainText("GPU를 사용할 수 없거나 사용 중입니다");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("submit");
});

test("an unknown runtime state stops the run before approval or submit", async ({ page }) => {
  await overrideActions(page, { runtime_check: { ready: false, runtime_state: "strange", findings: [] } });
  await startRun(page);
  await expect(page.getByRole("alert")).toContainText("학습 환경 상태를 확인하지 못했습니다");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("submit");
});

test("a failing runtime check stops the run before approval or submit", async ({ page }) => {
  await overrideActions(page, { runtime_check: { __reject: "연결 실패 테스트" } });
  await startRun(page);
  await expect(page.getByRole("alert")).toContainText("연결 실패 테스트");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("submit");
});

test("a GPU server with a missing runtime is told to prepare it from the connection settings", async ({ page }) => {
  await page.addInitScript(() => {
    const bridge = (window as any).__TAURI_INTERNALS__, original = bridge.invoke;
    bridge.invoke = async (command: string, args: any) => command === "settings_get" ? {
      provider: "ssh_server", server_ssh_host: "gpu.example.org", server_ssh_port: "2222", server_ssh_username: "researcher",
      server_ssh_fingerprint: "SHA256:" + "A".repeat(43), server_remote_root: "/home/researcher/factory",
      server_selected_gpu_uuids: JSON.stringify(["GPU-11111111-2222-3333-4444-555555555555"]) } : original(command, args);
  });
  await page.goto("/");
  await overrideActions(page, { plan: { destination_sha256: "a".repeat(64) },
    runtime_check: { ready: false, runtime_state: "missing", findings: [] }, execute: null });
  await startRun(page);
  await expect(page.getByRole("alert")).toContainText("연결 설정에서 GPU 목록을 조회한 뒤");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("execute");
  expect(await calledActions(page)).not.toContain("submit");
});

test("a double click on start runs one preflight and opens one approval", async ({ page }) => {
  await overrideActions(page, { runtime_check: { __hold: true, ready: true, runtime_state: "ready", findings: [] }, execute: null });
  await page.locator('nav [data-action="nav:training"]').click();
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).evaluate(button => { (button as HTMLButtonElement).click(); (button as HTMLButtonElement).click(); });
  await expect.poll(() => page.evaluate(() => (window as any).__releases?.length ?? 0)).toBeGreaterThan(0);
  await page.evaluate(() => (window as any).__releases.forEach((release: () => void) => release()));
  await expect(page.getByRole("dialog")).toHaveCount(1);
  expect((await calledActions(page)).filter(action => action === "runtime_check")).toHaveLength(1);
});

test("a plan changed during the preflight is not submitted", async ({ page }) => {
  await overrideActions(page, { runtime_check: { __hold: true, ready: true, runtime_state: "ready", findings: [] } });
  await startRun(page);
  await expect.poll(() => page.evaluate(() => (window as any).__releases?.length ?? 0)).toBe(1);
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).fill("3");
  await page.getByLabel("학습 횟수 (epoch)", { exact: true }).blur();
  await page.evaluate(() => (window as any).__releases.forEach((release: () => void) => release()));
  await expect(page.getByRole("alert")).toContainText("변경된 설정으로 실행 계획을 다시 확인하세요.");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("submit");
});

test("a Pod changed during the preflight is not submitted", async ({ page }) => {
  await overrideActions(page, { runtime_check: { __hold: true, ready: true, runtime_state: "ready", findings: [] } });
  await startRun(page);
  await expect.poll(() => page.evaluate(() => (window as any).__releases?.length ?? 0)).toBe(1);
  await page.locator('nav [data-action="nav:connections"]').click();
  await page.getByLabel("Pod ID", { exact: true }).fill("another-pod");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await page.evaluate(() => (window as any).__releases.forEach((release: () => void) => release()));
  await expect(page.getByRole("alert")).toContainText("연결된 Pod가 바뀌었습니다. 실행 전 확인을 다시 진행하세요.");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("submit");
});

test("a queued job runs only if it is still the selected job after the preflight", async ({ page }) => {
  const queued = (id: string) => ({ id: id.repeat(32), status: "QUEUED", attempts: 0, manifest: { config, simulation: false } });
  await overrideActions(page, { jobs: [queued("a"), queued("b")], execute: null,
    runtime_check: { __hold: true, ready: true, runtime_state: "ready", findings: [] } });
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByRole("button", { name: "선택", exact: true }).first().click();
  await page.getByRole("button", { name: "대기 작업 실행", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__releases?.length ?? 0)).toBe(1);
  await page.getByRole("button", { name: "선택", exact: true }).nth(1).click();
  await page.evaluate(() => (window as any).__releases.forEach((release: () => void) => release()));
  await expect(page.getByRole("alert")).toContainText("선택한 작업이 바뀌었습니다");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("execute");
});

test("a queued job whose destination stops matching during the preflight is not executed", async ({ page }) => {
  const queued = (route: unknown) => ({ id: "a".repeat(32), status: "QUEUED", attempts: 0, manifest: { config, simulation: false }, server_destination: route });
  await overrideActions(page, { jobs: [queued(null)], execute: null,
    runtime_check: { __hold: true, ready: true, runtime_state: "ready", findings: [] } });
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByRole("button", { name: "선택", exact: true }).first().click();
  await page.getByRole("button", { name: "대기 작업 실행", exact: true }).click();
  await expect.poll(() => page.evaluate(() => (window as any).__releases?.length ?? 0)).toBe(1);
  // The job now carries a GPU server route that the saved RunPod connection does not match.
  await overrideActions(page, { jobs: [queued({ ssh_host: "gpu.example.org", ssh_port: 22, ssh_username: "researcher", remote_root: "/srv/factory", selected_gpu_uuids: ["GPU-1"] })] });
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.evaluate(() => (window as any).__releases.forEach((release: () => void) => release()));
  await expect(page.getByRole("alert")).toContainText("GPU 서버용으로 승인되었습니다");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calledActions(page)).not.toContain("execute");
});
