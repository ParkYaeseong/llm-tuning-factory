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
