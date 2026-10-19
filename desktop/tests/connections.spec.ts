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

async function open(page: Page, settings: Record<string, unknown> = {}) {
  await page.addInitScript(({ config, settings, inspection, secrets }) => {
    const calls: any[] = [];
    const saved = sessionStorage.getItem("mock-settings");
    const stored: Record<string, any> = saved ? JSON.parse(saved) : { ...settings };
    const mock: any = (window as any).__mock = { inspection, facts: null, connection: {}, jobs: [], reservations: [],
      prepareError: "", gate: null, destination: { destination_sha256: "d".repeat(64), destination_issue: null } };
    (window as any).__testCalls = calls;
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args: args === undefined ? undefined : structuredClone(args) });
      if (command === "settings_get") return { ...stored };
      if (command === "settings_save") {
        // Native contract: credentials go to the vault and only *_saved flags come back. Public MLflow
        // fields (OIDC URL, client ID) stay in the public view for every auth mode.
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
      if (action === "runtime_prepare") { if (mock.gate) await mock.gate; if (mock.prepareError) throw mock.prepareError; return { prepared: true }; }
      if (action === "runtime_reservations") return structuredClone(mock.reservations);
      if (action === "history") return data.experiment_id ? { runs: structuredClone(mock.runs || []), next_page_token: null } : { experiments: [] };
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
  await open(page, { pod_id: "assigned-pod", RUNPOD_API_KEY_saved: true });
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
  await expect(page.getByRole("button", { name: "연결 검사", exact: true })).toBeDisabled();
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
  await expect(page.getByRole("button", { name: "연결 검사", exact: true })).toBeEnabled();
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
  await page.getByLabel(`${GPU_A} 학습에 사용`, { exact: true }).uncheck();
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).check();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  expect((await saves(page)).at(-1)).toEqual({ server_selected_gpu_uuids: JSON.stringify([GPU_B]) });
  await expect(page.locator(".notice")).toContainText("GPU 1개");
  // A new selection on the same server keeps the inventory instead of forcing another SSH inspection.
  await expect(runtime.locator("tbody tr")).toHaveCount(2);
  await expect(page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true })).toBeChecked();
  expect(await calls(page, "server_inspect")).toHaveLength(1);

  // Multi-GPU price rounding is covered by the module test; the app policy allows one GPU per job.
  await page.evaluate(f => (window as any).__mock.facts = f, facts(1, 0.11));
  await page.getByRole("button", { name: "GPU 정보 → 학습 설정에 적용", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("학습 설정에 적용했습니다");
  await trainingPage(page);
  await expect(page.getByLabel("GPU 종류 · 연결 설정에서 가져오기", { exact: true })).toHaveValue("NVIDIA A100-SXM4-80GB");
  await expect(page.getByLabel("GPU 수", { exact: true })).toHaveValue("1");
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  let resource = (await calls(page, "plan")).at(-1).config.resource_profile;
  expect(resource).toMatchObject({ gpu_type: "NVIDIA A100-SXM4-80GB", gpu_count: 1, hourly_cost_usd: 0.11 });

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
  await page.getByLabel(`${GPU_A} 학습에 사용`, { exact: true }).uncheck();
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

test("releasing an uncertain or stuck reservation requires confirming the remote preparation stopped", async ({ page }) => {
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
  const release = page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true });
  await expect(release).toHaveCount(2);
  await runtime.screenshot({ path: "test-results/server-reservations.png" });
  await release.first().click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("준비 프로세스가 종료된 것을 직접 확인했습니다");
  await expect(dialog).toContainText("researcher@gpu.example.org");
  await expect(dialog).not.toContainText("다른 앱 창");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "runtime_release")).toEqual([]);
  await release.first().click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(runtime.locator("tbody tr")).toHaveCount(1);
  expect(await calls(page, "runtime_release")).toEqual([{ approved: true, confirm_preparation_stopped: true, reservation_id: "a".repeat(32) }]);

  // A PREPARING reservation outlives a killed app or sidecar; releasing it warns that preparation may still run.
  await expect(release).toBeEnabled();
  await release.click();
  await expect(dialog).toContainText("이 앱이나 다른 앱 창에서 환경 준비가 아직 실행 중일 수 있습니다");
  await expect(dialog).toContainText("다른 앱 창을 모두 닫고");
  await expect(dialog).toContainText("프로세스 목록");
  // A killed window can leave its sidecar running with the preparation lease.
  await expect(dialog).toContainText("작업 관리자에서 factory-bridge.exe가 실행 중이 아닌지 확인하세요.");
  await expect(dialog).toContainText("준비 프로세스가 종료된 것을 직접 확인했습니다");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "runtime_release")).toHaveLength(1);
  await release.click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(runtime.locator("tbody tr")).toHaveCount(0);
  expect((await calls(page, "runtime_release")).at(-1)).toEqual({ approved: true, confirm_preparation_stopped: true, reservation_id: "b".repeat(32) });
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

test("a saved selection over the GPU policy leaves the setup step open with the policy hint", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: JSON.stringify([GPU_A, GPU_B]) });
  const step = page.locator(".setup-row", { hasText: "연결 정보 설정" });
  await expect(step.locator(".setup-check.done")).toHaveCount(0);
  await expect(step).toContainText("현재 앱 정책은 작업당 GPU 1개입니다. GPU를 하나만 선택하세요.");
});

test("an unauthenticated round trip keeps the saved OIDC address and client ID", async ({ page }) => {
  await open(page, { auth_mode: "oidc", MLFLOW_OIDC_TOKEN_URL: "https://sso.custom.example/token", MLFLOW_OIDC_CLIENT_ID: "custom-client",
    MLFLOW_OIDC_CLIENT_SECRET_saved: true });
  await connectionsPage(page);
  await expect(page.getByLabel("인증 서버 주소", { exact: true })).toHaveValue("https://sso.custom.example/token");
  await page.getByLabel("인증 방식", { exact: true }).selectOption("none");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await page.reload();
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
  await connectionsPage(page);
  await page.getByLabel("인증 방식", { exact: true }).selectOption("oidc");
  await expect(page.getByLabel("인증 서버 주소", { exact: true })).toHaveValue("https://sso.custom.example/token");
  await expect(page.getByLabel("클라이언트 ID", { exact: true })).toHaveValue("custom-client");
  await expect(page.getByText("보안 저장됨", { exact: true })).toBeVisible();
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  expect((await saves(page)).at(-1)).toMatchObject({ auth_mode: "oidc", MLFLOW_OIDC_TOKEN_URL: "https://sso.custom.example/token",
    MLFLOW_OIDC_CLIENT_ID: "custom-client", MLFLOW_OIDC_CLIENT_SECRET: "" });
});

const route = { ssh_host: "gpu.example.org", ssh_port: 2222, ssh_username: "researcher", remote_root: "/home/researcher/factory", selected_gpu_uuids: [GPU_A] };
const SERVER_BOUND_JOB = "이 작업은 GPU 서버용으로 승인되었습니다. 연결 대상을 그 GPU 서버로 바꾼 뒤 실행하세요.";
const UNBOUND_SERVER_JOB = "GPU 서버용 승인 기록이 없는 작업입니다. GPU 서버 연결에서 실행 계획을 다시 확인하고 제출하세요.";
const UNREADABLE_RECEIPT = "GPU 서버 승인 기록을 읽을 수 없습니다. 작업을 다시 계획·제출하세요.";
async function selectJob(page: Page, destination: unknown) {
  await page.evaluate(({ config, destination }) => (window as any).__mock.jobs = [{ id: "4".repeat(32), status: "QUEUED", attempts: 0,
    manifest: { config, simulation: false }, server_destination: destination }], { config, destination });
  await page.locator('nav [data-action="nav:jobs"]').click();
  await page.getByRole("button", { name: "새로고침", exact: true }).click();
  await page.getByRole("button", { name: "선택", exact: true }).click();
  await expect(page.getByRole("button", { name: "체크포인트에서 재개", exact: true })).toBeEnabled();
}
async function expectRefused(page: Page, message: string) {
  for (const name of ["대기 작업 실행", "체크포인트에서 재개"]) {
    await page.getByRole("button", { name, exact: true }).click();
    await expect(page.getByRole("alert")).toHaveText(message);
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  expect(await calls(page, "execute")).toEqual([]);
  expect(await calls(page, "resume")).toEqual([]);
}

test("RunPod connection refuses server-bound or unreadable jobs before approval", async ({ page }) => {
  await open(page, { pod_id: "assigned-pod" });
  await selectJob(page, route);
  await expectRefused(page, SERVER_BOUND_JOB);
  await selectJob(page, { invalid: true });
  await expectRefused(page, UNREADABLE_RECEIPT);
  await selectJob(page, null);
  await page.getByRole("button", { name: "대기 작업 실행", exact: true }).click();
  await expect(page.getByRole("dialog")).toContainText("RunPod로 전송하고 실제 학습을 실행합니다.");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "execute")).toEqual([]);
});

test("server connection approves only jobs bound to the saved server and names the job destination", async ({ page }) => {
  await open(page, server);
  await selectJob(page, null);
  await expectRefused(page, UNBOUND_SERVER_JOB);
  await selectJob(page, { invalid: true });
  await expectRefused(page, UNREADABLE_RECEIPT);
  await selectJob(page, { ...route, ssh_host: "other.example.org" });
  await expectRefused(page, SERVER_BOUND_JOB);
  await selectJob(page, route);
  await page.getByRole("button", { name: "체크포인트에서 재개", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("선택한 GPU 서버(researcher@gpu.example.org)로 학습·검증 데이터를 전송");
  await expect(dialog).toContainText("/home/researcher/factory");
  await page.getByRole("button", { name: "돌아가기", exact: true }).click();
  expect(await calls(page, "resume")).toEqual([]);
});

test("saving an unchanged GPU selection keeps the plan and says nothing changed", async ({ page }) => {
  await open(page, server);
  await trainingPage(page);
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await connectionsPage(page);
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  await expect(page.locator(".notice")).toHaveText("GPU 선택이 바뀌지 않았습니다.");
  await trainingPage(page);
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toBeEnabled();
});

test("environment preparation refuses unsaved GPU checkboxes and saved GPUs missing from the server", async ({ page }) => {
  await open(page, server);
  await connectionsPage(page);
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).check();
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await expect(page.getByRole("alert")).toHaveText("GPU 선택을 먼저 저장하세요.");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, "server_inspect")).toHaveLength(1);
  await expect(page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true })).toBeChecked();

  const GPU_GONE = "GPU-77777777-2222-3333-4444-555555555555";
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).uncheck();
  await page.evaluate(gone => (window as any).__mock.inspection.transport.selected_gpu_uuids.push(gone), GPU_GONE);
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText("이번 조회에 없는 GPU");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, "runtime_prepare")).toEqual([]);
});

test("reservations made with other server settings cannot be released here", async ({ page }) => {
  await open(page, server);
  await page.evaluate(() => {
    const transport = { ...(window as any).__mock.inspection.transport, ssh_host: "other.example.org" };
    (window as any).__mock.reservations = [{ reservation_id: "a".repeat(32), resource_id: "ssh_server:" + "9".repeat(64), status: "UNCERTAIN",
      created: 1791960000, profile: { transport }, facts: {} }];
  });
  await connectionsPage(page);
  await page.getByRole("button", { name: "준비 예약 확인", exact: true }).click();
  await expect(page.getByRole("button", { name: "원격 준비 종료 확인 · 예약 해제", exact: true })).toBeDisabled();
  await expect(page.getByText("다른 서버 설정으로 만든 예약입니다. 그 서버 설정으로 바꾼 뒤 해제하세요.", { exact: true })).toBeVisible();
  expect(await calls(page, "runtime_release")).toEqual([]);
});

test("connection controls are locked while a server preparation runs", async ({ page }) => {
  await open(page, server);
  await page.evaluate(() => { const mock = (window as any).__mock; mock.gate = new Promise(resolve => mock.open = resolve); });
  await connectionsPage(page);
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await page.getByRole("button", { name: "학습 환경 준비", exact: true }).click();
  await page.getByRole("button", { name: "내용 확인 · 진행", exact: true }).click();
  await expect(page.locator(".notice.busy")).toBeVisible();
  for (const label of ["서버 주소", "연결 대상", "인증 방식", "S3 Region", "HF 읽기 토큰", `${GPU_A} 학습에 사용`])
    await expect(page.getByLabel(label, { exact: true }), label).toBeDisabled();
  await page.evaluate(() => (window as any).__mock.open());
  await expect(page.locator(".notice.busy")).toHaveCount(0);
  for (const label of ["서버 주소", "연결 대상", "S3 Region", `${GPU_A} 학습에 사용`])
    await expect(page.getByLabel(label, { exact: true }), label).toBeEnabled();
});

test("a destination change clears the inspection, reservations and connection report", async ({ page }) => {
  await open(page, server);
  await page.evaluate(inspection => {
    const mock = (window as any).__mock;
    mock.connection = { ssh_server: inspection, gpu_training_verified: false };
    mock.reservations = [{ reservation_id: "b".repeat(32), resource_id: "ssh_server:" + "f".repeat(64), status: "PREPARING", created: 1791960100,
      profile: { transport: inspection.transport }, facts: {} }];
  }, inspection);
  await connectionsPage(page);
  const runtime = page.locator('[data-tour="connection-runtime"]');
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await page.getByRole("button", { name: "준비 예약 확인", exact: true }).click();
  await page.getByRole("button", { name: "연결 검사", exact: true }).click();
  await expect(runtime.locator("table")).toHaveCount(2);
  await expect(page.getByRole("heading", { name: "연결 검사 결과" })).toBeVisible();
  await page.getByLabel("작업 폴더 · 절대 경로", { exact: true }).fill("/home/researcher/factory-2");
  await page.getByRole("button", { name: "연결 정보 저장", exact: true }).click();
  await expect(page.locator(".notice")).toContainText("연결 정보를 저장했습니다.");
  await expect(runtime.locator("table")).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "환경 준비 예약" })).toHaveCount(0);
  await expect(page.getByRole("heading", { name: "연결 검사 결과" })).toHaveCount(0);
});

test("the server connection module refuses invalid prices and escapes rendered values", async ({ page }) => {
  await open(page);
  await page.addScriptTag({ type: "module", content: 'import * as m from "/src/server-connection.ts"; window.__serverConnection = m;' });
  await page.waitForFunction(() => (window as any).__serverConnection);
  const result = await page.evaluate(() => {
    const m = (window as any).__serverConnection;
    const price = (total: number, count: number) => { try { return m.perGpuHourlyCost(total, count); } catch { return "refused"; } };
    const name = "<script>alert(1)</script>";
    const transport = { ssh_host: "<b>h</b>", ssh_port: 22, ssh_username: "u", selected_gpu_uuids: [] };
    const inspection = { transport, observed_at: "2026-10-19T00:00:00Z", total_hourly_cost_usd: 0,
      inventory: [{ uuid: '"><img src=x>', name, memory_mib: 1024 }] };
    return {
      refused: [[-1, 1], [NaN, 1], [Infinity, 1], [1, 0], [1, 1.5], [1, -2], [1, NaN]].map(([t, c]) => price(t, c)),
      accepted: [price(0, 1), price(0.11, 5) * 5 >= 0.11, price(4, 2)],
      resources: m.resourcesFromFacts({ gpu_type: "A100", gpu_count: 3, total_hourly_cost_usd: 0.21 }),
      html: m.inventoryTable(inspection, [], []) + m.connectionResult({ ssh_server: inspection, mlflow_read: true, mlflow_auth: name })
        + m.reservationTable([{ reservation_id: name, status: "UNCERTAIN", created: 1, profile: { transport } }], {}, false),
    };
  });
  expect(result.refused).toEqual(Array(7).fill("refused"));
  expect(result.accepted).toEqual([0, true, 2]);
  expect(result.resources.gpu_type).toBe("A100"); expect(result.resources.gpu_count).toBe(3);
  expect(result.resources.hourly_cost_usd * 3).toBeGreaterThanOrEqual(0.21);
  expect(result.html).not.toContain("<script>"); expect(result.html).not.toContain("<img"); expect(result.html).not.toContain("<b>");
  expect(result.html).toContain("&lt;script&gt;");
});

test("server-supplied run IDs cannot inject markup through shared action buttons", async ({ page }) => {
  const evil = 'run" data-injected="1"><img src=x>';
  await open(page);
  await page.evaluate(({ config, evil }) => (window as any).__mock.runs = [{ id: evil, ai_id: "paper", status: "FINISHED", operator: "tester",
    metrics: {}, config: { ...config, ai_id: "paper" }, simulation: false }], { config, evil });
  await page.locator('nav [data-action="nav:history"]').click();
  await page.getByRole("button", { name: "기록 불러오기", exact: true }).click();
  await expect(page.getByRole("button", { name: "설정 불러오기", exact: true })).toBeVisible();
  await expect(page.locator("[data-injected]")).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
  // dataset decodes the escaped attribute, so the dispatcher still receives the original ID.
  expect(await page.getByRole("button", { name: "설정 불러오기", exact: true }).evaluate(el => (el as HTMLElement).dataset.action)).toBe("reuse:" + evil);
  await page.getByRole("button", { name: "설정 불러오기", exact: true }).click();
  await expect(page.getByRole("heading", { name: "학습 설정", exact: true })).toBeVisible();
  await expect(page.locator(".notice")).toContainText("설정을 불러왔습니다.");
});

test("SSH ports compare the way the sidecar parses them", async ({ page }) => {
  await open(page);
  await page.addScriptTag({ type: "module", content: 'import * as m from "/src/server-connection.ts"; window.__serverConnection = m;' });
  await page.waitForFunction(() => (window as any).__serverConnection);
  const result = await page.evaluate(() => {
    const m = (window as any).__serverConnection;
    const transport = { ssh_host: "h", ssh_port: 22, ssh_username: "u", ssh_fingerprint: "SHA256:x", remote_root: "/r", selected_gpu_uuids: [] };
    const job = { server_destination: { ssh_host: "h", ssh_port: 22, ssh_username: "u", remote_root: "/r", selected_gpu_uuids: [] } };
    return [null, "22", "022", "", "22.0", "0x16", " 22", "abc"].map(port => {
      const settings: Record<string, unknown> = { provider: "ssh_server", server_ssh_host: "h", server_ssh_username: "u",
        server_ssh_fingerprint: "SHA256:x", server_remote_root: "/r", server_selected_gpu_uuids: "[]" };
      if (port !== null) settings.server_ssh_port = port;
      return [port, m.reservationMatches({ profile: { transport } }, settings), m.jobDestinationIssue(job, settings) === null, String(m.sshPort(port ?? undefined))];
    });
  });
  // Python: an absent port is 22, a digit string becomes an integer ("022" is 22) and anything else is refused.
  expect(result).toEqual([[null, true, true, "22"], ["22", true, true, "22"], ["022", true, true, "22"], ["", false, false, "NaN"],
    ["22.0", false, false, "NaN"], ["0x16", false, false, "NaN"], [" 22", false, false, "NaN"], ["abc", false, false, "NaN"]]);
});

const GPU_POLICY = "현재 앱 정책은 작업당 GPU 1개입니다. GPU를 하나만 선택하세요.";
test("the app policy allows one GPU per job in the server selection", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: JSON.stringify([GPU_A, GPU_B]) });
  await connectionsPage(page);
  const runtime = page.locator('[data-tour="connection-runtime"]');
  // A saved selection over the policy blocks the actions that would use it until it is fixed.
  await expect(runtime.getByText(GPU_POLICY, { exact: false })).toBeVisible();
  for (const name of ["GPU 정보 → 학습 설정에 적용", "학습 환경 준비", "환경 확인"])
    await expect(page.getByRole("button", { name, exact: true }), name).toBeDisabled();
  await page.getByRole("button", { name: "GPU 목록 조회", exact: true }).click();
  await expect(runtime.getByText("작업당 GPU 1개", { exact: false }).first()).toBeVisible();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  await expect(page.getByRole("alert")).toContainText(GPU_POLICY);
  expect(await saves(page)).toEqual([]);
  await page.getByLabel(`${GPU_B} 학습에 사용`, { exact: true }).uncheck();
  await page.getByRole("button", { name: "GPU 선택 저장", exact: true }).click();
  expect((await saves(page)).at(-1)).toEqual({ server_selected_gpu_uuids: JSON.stringify([GPU_A]) });
  await expect(runtime.getByText(GPU_POLICY, { exact: false })).toHaveCount(0);
  for (const name of ["GPU 정보 → 학습 설정에 적용", "학습 환경 준비", "환경 확인"])
    await expect(page.getByRole("button", { name, exact: true }), name).toBeEnabled();
  expect(await calls(page, "server_facts")).toEqual([]);
  expect(await calls(page, "runtime_check")).toEqual([]);
});

test("both key-file fields ask for an OpenSSH key without a passphrase", async ({ page }) => {
  await open(page);
  await connectionsPage(page);
  const hint = "OpenSSH 형식·암호 없는 개인키 파일 경로";
  await expect(page.locator('[data-tour="connection-fields"]')).toContainText(hint);
  await page.getByLabel("연결 대상", { exact: true }).selectOption("ssh_server");
  await expect(page.locator('[data-tour="connection-fields"]')).toContainText(hint);
});

test("a stored PuTTY key path is flagged under its field so saves are not refused without explanation", async ({ page }) => {
  const warning = "OpenSSH 형식(암호 없는 키)으로 변환한 키 파일로 바꿔야 연결 정보를 저장할 수 있습니다";
  await open(page, { ...server, ssh_key_file: "C:\\keys\\pod.PPK.", server_ssh_key_file: "C:\\keys\\gpu.ppk" });
  await connectionsPage(page);
  const fields = page.locator('[data-tour="connection-fields"]');
  await expect(fields).toContainText(warning);
  await page.getByLabel("SSH 개인키 파일", { exact: true }).fill("C:\\keys\\id_ed25519");
  await page.getByLabel("연결 대상", { exact: true }).selectOption("existing_runpod");
  await expect(fields).toContainText(warning);
  await page.getByLabel("SSH 개인키 파일", { exact: true }).fill("C:\\keys\\pod_ed25519");
  await page.getByLabel("연결 대상", { exact: true }).selectOption("ssh_server");
  await expect(fields).not.toContainText(warning);
});

test("an over-limit GPU selection blocks starting a planned job and says why", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: JSON.stringify([GPU_A, GPU_B]) });
  await trainingPage(page);
  await page.getByRole("button", { name: "실행 계획 확인", exact: true }).click();
  await expect(page.locator(".plan")).toContainText(GPU_POLICY);
  await expect(page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" })).toBeDisabled();
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).evaluate(el => el.removeAttribute("disabled"));
  await page.getByRole("button", { name: "데이터 전송에 동의하고 학습 시작" }).click();
  await expect(page.getByRole("alert")).toContainText(GPU_POLICY);
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, "submit")).toEqual([]);
});

test("the dispatcher refuses guarded runtime actions even when a disabled button is re-enabled", async ({ page }) => {
  await open(page, { ...server, server_selected_gpu_uuids: JSON.stringify([GPU_A, GPU_B]) });
  await connectionsPage(page);
  for (const name of ["GPU 정보 → 학습 설정에 적용", "학습 환경 준비", "환경 확인"]) {
    const control = page.getByRole("button", { name, exact: true });
    await expect(control).toBeDisabled();
    await control.evaluate(el => el.removeAttribute("disabled"));
    await control.click();
    await expect(page.getByRole("alert"), name).toContainText(GPU_POLICY);
    await expect(page.getByRole("dialog")).toHaveCount(0);
  }
  for (const action of ["server_facts", "server_inspect", "runtime_check", "runtime_prepare"]) expect(await calls(page, action), action).toEqual([]);
});
