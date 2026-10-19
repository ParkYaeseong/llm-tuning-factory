import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));

// The native SECRETS allowlist: these go to Windows Credential Manager, never to public settings.
const SECRETS = ["RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN", "S3_ACCESS_KEY_ID", "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN"];
const FINGERPRINT = "SHA256:" + "A".repeat(43);
const GPU_A = "GPU-11111111-2222-3333-4444-555555555555";
const GPU_B = "GPU-66666666-7777-8888-9999-000000000000";
const server = {
  provider: "ssh_server", server_ssh_host: "gpu.example.org", server_ssh_port: "2222", server_ssh_username: "researcher",
  server_ssh_fingerprint: FINGERPRINT, server_remote_root: "/home/researcher/factory", server_ssh_key_file: "C:\\keys\\id_ed25519",
  server_total_hourly_cost_usd: "0.11", server_selected_gpu_uuids: JSON.stringify([GPU_A]),
};
const inspection = {
  provider: "ssh_server", server_id: "f".repeat(64),
  transport: { ssh_host: "gpu.example.org", ssh_port: 2222, ssh_username: "researcher", ssh_fingerprint: FINGERPRINT,
    remote_root: "/home/researcher/factory", selected_gpu_uuids: [GPU_A], key_reference_sha256: "e".repeat(64) },
  inventory: [{ uuid: GPU_A, name: "NVIDIA A100-SXM4-80GB", memory_mib: 81920 },
    { uuid: GPU_B, name: "<img src=x onerror=alert(1)>", memory_mib: 24576 }],
  total_hourly_cost_usd: 0.11, cost_source: "operator", observed_at: "2026-10-19T01:02:03+00:00",
};
const facts = (count: number, total: number) => {
  const selected = Array.from({ length: count }, (_, i) => `GPU-${String(i).padStart(8, "0")}-2222-3333-4444-555555555555`);
  return { provider: "ssh_server", server_id: "f".repeat(64), transport_settings_sha256: "a".repeat(64),
    inventory: selected.map(uuid => ({ uuid, name: "NVIDIA A100-SXM4-80GB", memory_mib: 81920 })), selected_gpu_uuids: selected,
    gpu_type: "NVIDIA A100-SXM4-80GB", gpu_count: count, total_hourly_cost_usd: total, cost_source: "operator",
    observed_at: "2026-10-19T01:02:03+00:00" };
};

async function open(page: Page, settings: Record<string, string> = {}) {
  await page.addInitScript(({ config, settings, inspection, secrets }) => {
    const calls: any[] = [];
    const saved = sessionStorage.getItem("mock-settings");
    const stored: Record<string, any> = saved ? JSON.parse(saved) : { ...settings };
    const mock: any = (window as any).__mock = { inspection, facts: null, connection: {}, jobs: [], reservations: [],
      prepareError: "", destination: { destination_sha256: "d".repeat(64), destination_issue: null } };
    (window as any).__testCalls = calls;
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args: args === undefined ? undefined : structuredClone(args) });
      if (command === "settings_get") return { ...stored };
      if (command === "settings_save") {
        // Native contract: credentials go to the vault and only *_saved flags come back.
        for (const [key, value] of Object.entries(args.values)) {
          if (secrets.includes(key)) { if (value) stored[key + "_saved"] = true; }
          else stored[key] = value;
        }
        sessionStorage.setItem("mock-settings", JSON.stringify(stored));
        return { ...stored };
      }
      if (command === "pick_file") return "C:\\keys\\picked_ed25519";
      if (command !== "factory_action") return null;
      const { action, data } = args;
      if (action === "catalog") return { models: [{ id: "Qwen/Qwen2.5-0.5B-Instruct", template: "qwen", gpu_verified: true }],
        training_stages: [{ id: "sft", label: "SFT · 지도 학습", dataset_kind: "instruction" }, { id: "ppo", label: "PPO · 정책 최적화", dataset_kind: "prompt" }],
        templates: [{ name: "paper_extraction", config }], profiles: [], datasets: [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true }] };
      if (action === "jobs") return structuredClone(mock.jobs);
      if (action === "capabilities") return { valid: true, errors: [], warnings: [], supported_stages: ["sft", "ppo"], methods: ["lora", "qlora"], quantization_bits: [4, 8] };
      if (action === "server_inspect") return structuredClone(mock.inspection);
      if (action === "server_facts") return structuredClone(mock.facts);
      if (action === "connection") return structuredClone(mock.connection);
      if (action === "plan") return { dry_run: true, manifest: { config: data.config, dataset: { dataset_id: "dummy", rows: 2 } }, estimated_cost_usd: .5,
        note: "계획만 확인했습니다.", ...(stored.provider === "ssh_server" ? mock.destination : {}) };
      if (action === "submit") return { id: "c".repeat(32), status: "QUEUED", attempts: 0, manifest: { config: data.config, simulation: false } };
      if (action === "execute") return null;
      if (action === "runtime_prepare") { if (mock.prepareError) throw mock.prepareError; return { prepared: true }; }
      if (action === "runtime_reservations") return structuredClone(mock.reservations);
      if (action === "runtime_release") { mock.reservations = mock.reservations.filter((r: any) => r.reservation_id !== data.reservation_id); return { released: data.reservation_id }; }
      return {};
    } };
  }, { config, settings, inspection, secrets: SECRETS });
  await page.goto("/");
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
}
const calls = (page: Page, action: string) => page.evaluate(name => (window as any).__testCalls
  .filter((c: any) => c.command === "factory_action" && c.args.action === name).map((c: any) => c.args.data), action);
const saves = (page: Page) => page.evaluate(() => (window as any).__testCalls.filter((c: any) => c.command === "settings_save").map((c: any) => c.args.values));
const connectionsPage = (page: Page) => page.locator('nav [data-action="nav:connections"]').click();
const trainingPage = (page: Page) => page.locator('nav [data-action="nav:training"]').click();

test("provider switch shows server fields and save/reopen persists them without secrets", async ({ page }) => {
  await open(page, { pod_id: "assigned-pod", RUNPOD_API_KEY_saved: "true" });
  await connectionsPage(page);
  await expect(page.getByLabel("RunPod API 키", { exact: true })).toBeVisible();
  await expect(page.getByLabel("서버 주소", { exact: true })).toHaveCount(0);
  await page.getByLabel("연결 대상", { exact: true }).selectOption("ssh_server");
  await expect(page.getByLabel("연결 대상", { exact: true })).toBeFocused();
  await expect(page.getByLabel("RunPod API 키", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("Pod ID", { exact: true })).toHaveCount(0);
  await expect(page.getByLabel("기록에 표시할 이름", { exact: true })).toBeVisible();
  await expect(page.getByLabel("SSH 포트", { exact: true })).toHaveValue("22");
  // Runtime actions use saved settings, so they wait for the provider switch to be saved.
  await expect(page.getByRole("button", { name: "GPU 목록 조회", exact: true })).toBeDisabled();
  await expect(page.getByText("바꾼 연결 대상을 저장한 뒤 사용할 수 있습니다.", { exact: true })).toBeVisible();
  await page.getByLabel("서버 주소", { exact: true }).fill("gpu.example.org");
  await page.getByLabel("SSH 포트", { exact: true }).fill("2222");
  await page.getByLabel("접속 계정", { exact: true }).fill("researcher");
  await page.getByRole("button", { name: "파일 찾기", exact: true }).click();
  await expect(page.getByLabel("SSH 개인키 파일", { exact: true })).toHaveValue("C:\\keys\\picked_ed25519");
  await page.getByLabel("SSH 서버 지문", { exact: true }).fill(FINGERPRINT);
  await page.getByLabel("작업 폴더 · 절대 경로", { exact: true }).fill("/home/researcher/factory");
  await page.getByLabel("시간당 비용 (USD) · 서버 전체", { exact: true }).fill("0");
  await page.getByLabel("기록에 표시할 이름", { exact: true }).fill("검토자");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("연결 정보를 저장했습니다.");
  await expect(page.getByRole("button", { name: "GPU 목록 조회", exact: true })).toBeEnabled();
  const values = (await saves(page)).at(-1);
  expect(values).toMatchObject({ provider: "ssh_server", server_ssh_host: "gpu.example.org", server_ssh_port: "2222",
    server_ssh_username: "researcher", server_ssh_key_file: "C:\\keys\\picked_ed25519", server_ssh_fingerprint: FINGERPRINT,
    server_remote_root: "/home/researcher/factory", server_total_hourly_cost_usd: "0", FACTORY_OPERATOR: "검토자" });
  expect(values).not.toHaveProperty("RUNPOD_API_KEY");
  expect(values).not.toHaveProperty("pod_id");
  for (const key of SECRETS) if (key in values) expect(values[key], key).toBe("");
  const picks = await page.evaluate(() => (window as any).__testCalls.filter((c: any) => c.command === "pick_file").map((c: any) => c.args));
  expect(picks).toEqual([{ kind: "key" }]);

  await page.reload();
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
  await connectionsPage(page);
  await expect(page.getByLabel("연결 대상", { exact: true })).toHaveValue("ssh_server");
  await expect(page.getByLabel("서버 주소", { exact: true })).toHaveValue("gpu.example.org");
  await expect(page.getByLabel("SSH 개인키 파일", { exact: true })).toHaveValue("C:\\keys\\picked_ed25519");
  await expect(page.getByLabel("시간당 비용 (USD) · 서버 전체", { exact: true })).toHaveValue("0");
  await expect(page.getByLabel("RunPod API 키", { exact: true })).toHaveCount(0);
  await page.getByLabel("연결 대상", { exact: true }).selectOption("existing_runpod");
  await expect(page.getByLabel("Pod ID", { exact: true })).toHaveValue("assigned-pod");
  expect(await page.evaluate(() => localStorage.length)).toBe(0);
});

test("unauthenticated MLflow hides OIDC and token fields, persists, and reports the auth method", async ({ page }) => {
  await open(page, { pod_id: "assigned-pod" });
  await connectionsPage(page);
  await expect(page.getByLabel("클라이언트 인증값", { exact: true })).toBeVisible();
  await expect(page.getByLabel("별도 발급 토큰", { exact: true })).toHaveCount(0);
  await page.getByLabel("인증 방식", { exact: true }).selectOption("token");
  await expect(page.getByLabel("별도 발급 토큰", { exact: true })).toBeVisible();
  await expect(page.getByLabel("인증 서버 주소", { exact: true })).toHaveCount(0);
  await page.getByLabel("인증 방식", { exact: true }).selectOption("none");
  for (const label of ["인증 서버 주소", "클라이언트 ID", "클라이언트 인증값", "별도 발급 토큰"])
    await expect(page.getByLabel(label, { exact: true })).toHaveCount(0);
  await expect(page.getByText("신뢰할 수 있는 로컬·내부망에서만 사용하세요", { exact: false })).toBeVisible();
  await page.getByLabel("MLflow 서버 주소", { exact: true }).fill("http://127.0.0.1:5000");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  const values = (await saves(page)).at(-1);
  expect(values.auth_mode).toBe("none");
  for (const key of ["MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN"]) expect(values).not.toHaveProperty(key);
  await page.reload();
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
  await connectionsPage(page);
  await expect(page.getByLabel("인증 방식", { exact: true })).toHaveValue("none");
  await expect(page.getByLabel("클라이언트 인증값", { exact: true })).toHaveCount(0);
  await page.evaluate(() => (window as any).__mock.connection = { tracking_uri: "http://127.0.0.1:5000", mlflow_read: true, mlflow_auth: "none",
    mlflow_authenticated_read: false, model_registry_read: true, gpu_training_verified: false, runpod: { status: "RUNNING" } });
  await page.getByRole("button", { name: "연결 검사", exact: true }).click();
  const result = page.locator("section", { has: page.getByRole("heading", { name: "연결 검사 결과" }) });
  await expect(result.locator("div", { has: page.getByText("MLflow 조회", { exact: true }) }).locator("dd")).toHaveText("확인됨 · 인증 없음");
  await expect(result.locator("div", { has: page.getByText("Pod 상태", { exact: true }) }).locator("dd")).toHaveText("RUNNING");
  // Older sidecar responses only carried mlflow_authenticated_read.
  await page.evaluate(() => (window as any).__mock.connection = { tracking_uri: "https://tracking.example", mlflow_authenticated_read: true, runpod: { status: "EXITED" } });
  await page.getByRole("button", { name: "연결 검사", exact: true }).click();
  await expect(result.locator("div", { has: page.getByText("MLflow 조회", { exact: true }) }).locator("dd")).toHaveText("확인됨");
});

test("server connection check shows the observed server instead of a Pod and never claims training works", async ({ page }) => {
  await open(page, server);
  await page.evaluate(inspection => (window as any).__mock.connection = { tracking_uri: "https://tracking.example", mlflow_read: true,
    mlflow_auth: "oidc", mlflow_authenticated_read: true, model_registry_read: true, gpu_training_verified: false, ssh_server: inspection }, inspection);
  await connectionsPage(page);
  await page.getByRole("button", { name: "연결 검사", exact: true }).click();
  const result = page.locator("section", { has: page.getByRole("heading", { name: "연결 검사 결과" }) });
  await expect(result.locator("div", { has: page.getByText("MLflow 조회", { exact: true }) }).locator("dd")).toHaveText("확인됨 · 기관 인증(OIDC)");
  await expect(result).toContainText("researcher@gpu.example.org:2222");
  await expect(result).toContainText("GPU 2개");
  await expect(result).toContainText("NVIDIA A100-SXM4-80GB");
  await expect(result).not.toContainText("Pod 상태");
  await expect(result).toContainText("아직 확인하지 않음");
  await expect(page.locator("img")).toHaveCount(0);
  await page.evaluate(inspection => (window as any).__mock.connection = { ssh_server: inspection, gpu_training_verified: false }, inspection);
  await page.getByRole("button", { name: "연결 검사", exact: true }).click();
  await expect(result.locator("div", { has: page.getByText("MLflow 조회", { exact: true }) }).locator("dd")).not.toContainText("확인됨");
  expect(await calls(page, "pod")).toEqual([]);
});

test("GPU inventory selection saves a JSON string and server facts fill exact training resources", async ({ page }) => {
  await open(page, server);
  await connectionsPage(page);
  await expect(page.getByRole("button", { name: "GPU 선택 저장", exact: true })).toBeDisabled();
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  const runtime = page.locator('[data-tour="connection-runtime"]');
  await expect(runtime.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByLabel(`${GPU_A} 학습에 사용`, { exact: true })).toBeChecked();
  await expect(page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true })).not.toBeChecked();
  await expect(runtime.locator(`[title="${GPU_A}"]`).first()).toBeVisible();
  await expect(runtime).toContainText("80 GiB");
  await expect(runtime.getByText("<img src=x onerror=alert(1)>", { exact: true })).toBeVisible();
  await expect(page.locator("img")).toHaveCount(0);
  await page.screenshot({ path: "test-results/server-connection.png", fullPage: true });
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).check();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  expect((await saves(page)).at(-1)).toEqual({ server_selected_gpu_uuids: JSON.stringify([GPU_A, GPU_B]) });
  await expect(page.locator(".notice")).toContainText("GPU 2개");

  // 0.11 / 5 rounds below 0.11 when multiplied back; the per-GPU price must never undercut the server total.
  await page.evaluate(f => (window as any).__mock.facts = f, facts(5, 0.11));
  await page.getByRole("button", { name: "GPU 정보 → 학습 설정에 적용", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("학습 설정에 적용했습니다");
  await trainingPage(page);
  await expect(page.getByLabel("GPU 종류 · 연결 설정에서 가져오기", { exact: true })).toHaveValue("NVIDIA A100-SXM4-80GB");
  await expect(page.getByLabel("GPU 수", { exact: true })).toHaveValue("5");
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  let resource = (await calls(page, "plan")).at(-1).config.resource_profile;
  expect(resource.gpu_type).toBe("NVIDIA A100-SXM4-80GB"); expect(resource.gpu_count).toBe(5);
  expect(resource.hourly_cost_usd * 5).toBeGreaterThanOrEqual(0.11);
  expect(Math.abs(resource.hourly_cost_usd - 0.11 / 5)).toBeLessThan(1e-15);

  await page.evaluate(f => (window as any).__mock.facts = f, facts(1, 0));
  await connectionsPage(page);
  await page.getByRole("button", { name: "GPU 정보 → 학습 설정에 적용", exact: true }).click();
  await trainingPage(page);
  await expect(page.getByLabel("GPU 한 개당 시간 요금 (USD)", { exact: true })).toHaveValue("0");
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  resource = (await calls(page, "plan")).at(-1).config.resource_profile;
  expect(resource).toMatchObject({ gpu_count: 1, hourly_cost_usd: 0 });
  expect(await calls(page, "pod")).toEqual([]);
});

test("server plan shows its destination, refuses a missing hash and submits the planned hash", async ({ page }) => {
  await open(page, server);
  await page.evaluate(() => (window as any).__mock.destination = { destination_sha256: null, destination_issue: "학습에 사용할 GPU를 연결 화면에서 선택하세요." });
  await trainingPage(page);
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.getByText("학습에 사용할 GPU를 연결 화면에서 선택하세요.", { exact: true })).toBeVisible();
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toBeDisabled();

  await page.evaluate(() => (window as any).__mock.destination = { destination_sha256: "d".repeat(64), destination_issue: null });
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.locator(".plan")).toContainText("GPU 서버: researcher@gpu.example.org:2222 · 선택 GPU 1개");
  await expect(page.locator(".plan")).not.toContainText("Pod:");
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("선택한 GPU 서버(researcher@gpu.example.org)로 학습·검증 데이터를 전송하고 실제 학습을 실행합니다.");
  await expect(dialog).toContainText("앱은 서버를 켜거나 끄지 않습니다.");
  await expect(dialog).not.toContainText("RunPod");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "submit")).toEqual([]);
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.getByRole("heading", { name: "실행 관리", exact: true })).toBeVisible();
  const submitted = (await calls(page, "submit")).at(-1);
  expect(submitted).toMatchObject({ approved: true, destination_sha256: "d".repeat(64) });
  expect(typeof submitted.request_key).toBe("string");
});

test("changing server settings after planning removes the plan", async ({ page }) => {
  await open(page, server);
  await trainingPage(page);
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toBeEnabled();
  await connectionsPage(page);
  await page.getByLabel("기록에 표시할 이름", { exact: true }).fill("검토자");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await trainingPage(page);
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toBeEnabled();
  await connectionsPage(page);
  await page.getByLabel("SSH 포트", { exact: true }).fill("22");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await trainingPage(page);
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toHaveCount(0);

  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toBeVisible();
  await connectionsPage(page);
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).check();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  await trainingPage(page);
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toHaveCount(0);

  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await connectionsPage(page);
  await page.getByLabel("연결 대상", { exact: true }).selectOption("existing_runpod");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await trainingPage(page);
  await expect(page.getByRole("heading", { name: "실행 전 확인" })).toHaveCount(0);
  expect(await calls(page, "submit")).toEqual([]);
});

test("RunPod plan keeps the Pod destination text and submit shape", async ({ page }) => {
  await open(page, { pod_id: "assigned-pod" });
  await trainingPage(page);
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.locator(".plan")).toContainText("Pod: assigned-pod");
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("dialog")).toContainText("RunPod로 전송하고 실제 학습을 실행합니다.");
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.getByRole("heading", { name: "실행 관리", exact: true })).toBeVisible();
  expect((await calls(page, "submit")).at(-1)).not.toHaveProperty("destination_sha256");
});

test("server runtime preparation inspects first, cancels cleanly and sends the exact inspection", async ({ page }) => {
  await open(page, server);
  await connectionsPage(page);
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("researcher@gpu.example.org");
  await expect(dialog).toContainText("/home/researcher/factory");
  await expect(dialog).toContainText(GPU_A);
  await expect(dialog).toContainText("NVIDIA A100-SXM4-80GB");
  expect(await calls(page, "server_inspect")).toHaveLength(1);
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "runtime_prepare")).toEqual([]);
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("학습 환경");
  expect((await calls(page, "runtime_prepare")).at(-1)).toEqual({ approved: true, inspection, training_stage: "sft" });

  await page.evaluate(() => {
    const mock = (window as any).__mock;
    mock.prepareError = "runtime preparation uncertain; reservation " + "a".repeat(32) + " retained until original-server termination is confirmed";
    mock.reservations = [{ reservation_id: "a".repeat(32), resource_id: "ssh_server:" + "f".repeat(64), status: "UNCERTAIN", created: 1791960000,
      profile: { transport: mock.inspection.transport }, facts: {} }];
  });
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("runtime preparation uncertain");
  await expect(page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true })).toBeVisible();
  expect(await calls(page, "runtime_reservations")).toHaveLength(1);
});

test("server runtime preparation refuses an empty GPU selection before approval", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: "[]" });
  await page.evaluate(() => (window as any).__mock.inspection.transport.selected_gpu_uuids = []);
  await connectionsPage(page);
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("GPU를");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, "runtime_prepare")).toEqual([]);
});

test("releasing an uncertain reservation requires confirming the remote preparation stopped", async ({ page }) => {
  await open(page, server);
  await page.evaluate(() => {
    const mock = (window as any).__mock;
    mock.reservations = [
      { reservation_id: "a".repeat(32), resource_id: "ssh_server:" + "f".repeat(64), status: "UNCERTAIN", created: 1791960000, profile: { transport: mock.inspection.transport }, facts: {} },
      { reservation_id: "b".repeat(32), resource_id: "ssh_server:" + "9".repeat(64), status: "PREPARING", created: 1791960100, profile: { transport: mock.inspection.transport }, facts: {} }];
  });
  await connectionsPage(page);
  await page.getByRole("button", { name: "준비 예약 확인", exact: true }).click();
  const runtime = page.locator('[data-tour="connection-runtime"]');
  await expect(runtime.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true })).toHaveCount(1);
  await runtime.screenshot({ path: "test-results/server-reservations.png" });
  await page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("준비 프로세스가 종료된 것을 직접 확인했습니다");
  await expect(page.getByRole("dialog")).toContainText("researcher@gpu.example.org");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "runtime_release")).toEqual([]);
  await page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(runtime.locator("tbody tr")).toHaveCount(1);
  expect(await calls(page, "runtime_release")).toEqual([{ approved: true, confirm_preparation_stopped: true, reservation_id: "a".repeat(32) }]);
});

test("tutorial anchors exist exactly once for both providers without remote calls", async ({ page }) => {
  await open(page, { pod_id: "assigned-pod" });
  const startup = (await page.evaluate(() => (window as any).__testCalls.filter((c: any) => c.command === "factory_action").map((c: any) => c.args.action))).sort();
  for (const name of startup) expect(["catalog", "jobs", "capabilities"]).toContain(name);
  await connectionsPage(page);
  for (const provider of ["existing_runpod", "ssh_server", "existing_runpod"]) {
    await page.getByLabel("연결 대상", { exact: true }).selectOption(provider);
    for (const anchor of ["connection-provider", "connection-fields", "connection-runtime"])
      await expect(page.locator(`[data-tour="${anchor}"]`), `${anchor} for ${provider}`).toHaveCount(1);
    await expect(page.locator('[data-tour="connection-provider"]').getByLabel("연결 대상", { exact: true })).toBeVisible();
    await expect(page.locator('[data-tour="connection-fields"]').getByLabel(provider === "ssh_server" ? "서버 주소" : "Pod ID", { exact: true })).toBeVisible();
  }
  for (const target of ["overview", "connections", "training", "connections", "jobs", "connections"])
    await page.locator(`nav [data-action="nav:${target}"]`).click();
  await page.getByLabel("인증 방식", { exact: true }).selectOption("none");
  const after = await page.evaluate(() => (window as any).__testCalls.filter((c: any) => c.command === "factory_action").map((c: any) => c.args.action));
  for (const name of after) expect(["catalog", "jobs", "capabilities"]).toContain(name);
});

test("saved server settings drive the overview and job destination labels", async ({ page }) => {
  await open(page, server);
  await expect(page.locator(".setup-row", { hasText: "연결 정보 설정" })).toContainText("RunPod 또는 GPU 서버와 MLflow 연결을 확인하세요.");
  await expect(page.locator(".setup-row", { hasText: "연결 정보 설정" }).locator(".setup-check.done")).toHaveCount(1);
  await expect(page.locator(".connection-list")).toContainText("GPU 서버");
  await expect(page.locator(".connection-list")).toContainText("researcher@gpu.example.org");
  await expect(page.locator(".rail-note")).toContainText("배정받은 RunPod 또는 GPU 서버에서 학습합니다");
  await page.evaluate(config => (window as any).__mock.jobs = [
    { id: "1".repeat(32), status: "COMPLETED", attempts: 1, manifest: { config, simulation: false },
      server_destination: { ssh_host: "gpu.example.org", ssh_port: 2222, ssh_username: "researcher", remote_root: "/home/researcher/factory", selected_gpu_uuids: [] } },
    { id: "2".repeat(32), status: "FAILED", attempts: 1, manifest: { config, simulation: false }, server_destination: { invalid: true } },
    { id: "3".repeat(32), status: "QUEUED", attempts: 0, manifest: { config, simulation: false }, server_destination: null }], config);
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  const rows = page.locator("tbody tr");
  await expect(rows.nth(0)).toContainText("GPU 서버 · gpu.example.org");
  await expect(rows.nth(1)).toContainText("승인 기록 손상");
  await expect(rows.nth(2)).toContainText("RunPod");
});

test("incomplete server settings leave the setup step open", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: "[]" });
  await expect(page.locator(".setup-row", { hasText: "연결 정보 설정" }).locator(".setup-check.done")).toHaveCount(0);
});
