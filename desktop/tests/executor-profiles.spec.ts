import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { calls, HOSTILE, invalid, issue, NOT_AVAILABLE, ok, openWorkbench } from "./workflow-mock.ts";

// Synthetic values only, built at runtime (the secret scan walks every commit).
const KEY = "synthetic-" + "runpod-" + "k".repeat(12);
const KEY_FILE = "C:\\Users\\tester\\.ssh\\id_ed25519";
const GPU = (digit: string) => `GPU-${digit.repeat(8)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(4)}-${digit.repeat(12)}`;
const [GPU1, GPU2, GPU3] = [GPU("1"), GPU("2"), GPU("3")];
const SERVER_FIELDS = ["provider", "name", "server_ssh_host", "server_ssh_port", "server_ssh_username", "server_ssh_fingerprint",
  "server_remote_root", "server_ssh_key_file", "server_total_hourly_cost_usd", "server_selected_gpu_uuids"];
// Native and backend texts (profiles.rs, broker.rs, workflow_planning.py, controller_entry.py) and the D9 texts.
const EDIT_CHECK_FAILED = `실행 프로필 사용 여부를 확인할 수 없어 변경하지 않았습니다. (${NOT_AVAILABLE})`;
const PROFILE_IN_USE = "이 실행 프로필로 실행 중이거나 결과 확인이 끝나지 않은 작업이 있어 수정·삭제할 수 없습니다.";
const CREDENTIAL_MISSING = "이 실행 프로필에 저장된 RunPod API 키가 없습니다. 실행 프로필에서 키를 저장하세요.";
const ROOT_UNUSABLE = "앱 저장 폴더 경로를 백그라운드 실행기에 전달할 수 없습니다.";
const STATUS_BUSY = "컨트롤러 상태를 지금 읽을 수 없습니다. 작업 기록이 사용 중입니다. 잠시 후 다시 확인하세요.";
const REDIRECTED_APPDATA = "앱 저장 폴더가 네트워크 경로(리디렉션된 AppData)에 있으면 백그라운드 컨트롤러를 시작할 수 없습니다. 단일 학습 기능은 계속 사용할 수 있습니다.";
const RESTART_PROCEDURE = "일시정지 → 진행 중 단계 완료 대기(또는 컨트롤러 정지) → 설정 저장 → 컨트롤러 시작";

const runpod = { profile_id: "pod-a", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: "A100 Pod",
  pod_id: "pod-123", ssh_fingerprint: "SHA256:abc", ssh_key_file: KEY_FILE } };
const server = (selection = "") => ({ profile_id: "lab", RUNPOD_API_KEY_saved: false, fields: { provider: "ssh_server",
  name: `${HOSTILE} 서버`, server_ssh_host: "gpu.example.org", server_ssh_port: "2222", server_ssh_username: "lab",
  server_ssh_fingerprint: "SHA256:def", server_remote_root: "/home/lab/factory", server_ssh_key_file: KEY_FILE,
  server_total_hourly_cost_usd: "0", server_selected_gpu_uuids: selection } });
/** A `controller_status` answer (9.4 shape); no `*_saved` flag exists in it. */
const controller = (state: "running" | "stale" | "absent", extra: object = {}) => ok({
  controller: { owner_id: state === "absent" ? null : "owner-1", state, heartbeat_age_seconds: state === "absent" ? null : state === "running" ? 2 : 140 },
  current: null, broker: null, stop_requested: false, restart_required: { profiles: [], tracking: false }, runs: { active: 0 }, ...extra });
const noObservations = ok({ observations: [] });

const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
const list = (page: Page) => page.locator('#app [data-tour="executor-profiles"]');
const rows = (page: Page) => list(page).locator("tbody tr");
const form = (page: Page) => page.locator("#profile-form");
const bar = (page: Page) => page.locator("#controller-bar");
const pill = (page: Page) => bar(page).locator(".status-pill").first();
/** The signal age beside the pill (not a live region: it changes on every poll). */
const signal = (page: Page) => bar(page).locator("#controller-bar-signal");
const release = (page: Page, name: string) => page.evaluate(name => (window as any).__release(name), name);
/** Holds every later `command` call until `window.__openGate()` (the mock's `holds` cannot start later). */
const gate = (page: Page, command: string) => page.evaluate(command => {
  const bridge = (window as any).__TAURI_INTERNALS__, inner = bridge.invoke;
  let open!: () => void;
  const opened = new Promise<void>(resolve => { open = resolve; });
  (window as any).__openGate = () => open();
  bridge.invoke = async (name: string, args: unknown) => { if (name === command) await opened; return inner(name, args); };
}, command);
const saveButton = (page: Page) => form(page).getByRole("button", { name: "저장", exact: true });
const statusCalls = async (page: Page) => (await calls(page, { command: "controller_status" })).length;
/** Whether the synthetic key appears in the page markup or text (only a boolean leaves the page). */
const keyShown = (page: Page) => page.evaluate(key => document.documentElement.outerHTML.includes(key) || document.body.innerText.includes(key), KEY);
/** Stops the page clock after boot, so polls run only when a test advances it. */
const pauseClock = async (page: Page) => page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));

async function openProfiles(page: Page) {
  await page.locator('nav [data-action="nav:profiles"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("실행 프로필");
  await idle(page);
}
async function edit(page: Page, index: number) {
  await rows(page).nth(index).getByRole("button", { name: /편집/ }).click();
  await expect(form(page)).toBeVisible();
}

test("profiles list public fields and never echo a saved key", async ({ page }) => {
  const logs: string[] = [];
  page.on("console", message => logs.push(message.text()));
  // Even a native answer that carried a key (it never does) is never echoed: only allowlisted fields are shown.
  const leaky = { ...runpod, secrets: { RUNPOD_API_KEY: KEY } };
  const observations = ok({ observations: [
    { profile_id: "pod-a", revision: "a".repeat(64), observed_at: "2026-10-21T08:00:00+00:00", age_seconds: 600, provider: "existing_runpod",
      name: "A100 Pod", resource_id: "existing_runpod:pod-123", gpu_type: "NVIDIA A100 80GB PCIe", gpu_count: 1, total_hourly_cost_usd: 1.89,
      cost_source: "runpod_api", selected_gpu_uuids: [], profile_current: true, fresh_for_real_plan: true },
    { profile_id: "lab", error: "저장된 자원 확인 기록을 검증할 수 없습니다." }] });
  await openWorkbench(page, { profiles: [leaky, server()], workflow: { controller_status: controller("absent"), observations_list: observations } });
  expect(await calls(page, { command: "executor_profiles_get" })).toHaveLength(0);
  const before = (await calls(page)).length;
  await openProfiles(page);
  // Entering runs read-only calls only: the native list, the stored observations and the controller status.
  const entered = (await calls(page)).slice(before).map(call => call.command === "workflow_action" ? `workflow:${call.args.action}` : call.command);
  expect([...new Set(entered)].sort()).toEqual(["controller_status", "executor_profiles_get", "workflow:observations_list"]);
  await expect(list(page)).toHaveCount(1);
  await expect(rows(page)).toHaveCount(2);
  for (const text of ["A100 Pod", "pod-a", "RunPod", "Pod pod-123", "API 키 보안 저장됨", "10분 전 확인", "NVIDIA A100 80GB PCIe × 1", "실제 실행 계획에 사용 가능"])
    await expect(rows(page).nth(0)).toContainText(text);
  for (const text of [`${HOSTILE} 서버`, "GPU 서버", "lab@gpu.example.org:2222", "GPU 선택 없음", "확인 기록을 읽을 수 없습니다"])
    await expect(rows(page).nth(1)).toContainText(text);
  await edit(page, 0);
  const key = form(page).getByLabel("RunPod API 키");
  await expect(key).toHaveAttribute("type", "password");
  await expect(key).toHaveValue("");
  await expect(form(page)).toContainText("보안 저장됨 · 변경할 때만 입력");
  await expect(form(page).getByLabel("Pod ID")).toHaveValue("pod-123");
  await expect(form(page).getByLabel("SSH 개인키 파일")).toHaveValue(KEY_FILE);
  expect(await keyShown(page)).toBe(false);
  expect(logs.some(line => line.includes(KEY))).toBe(false);
  await expect(page.locator("img")).toHaveCount(0);
});

test("a RunPod profile saves through the native command and reloads", async ({ page }) => {
  const saved = { profile_id: "pod-a", RUNPOD_API_KEY_saved: true, fields: { provider: "existing_runpod", name: "새 Pod", pod_id: "pod-777",
    ssh_fingerprint: "SHA256:xyz", ssh_key_file: KEY_FILE } };
  await openWorkbench(page, { workflow: { controller_status: controller("absent"), observations_list: noObservations },
    native: { executor_profiles_get: [{ profiles: [] }, { profiles: [saved] }],
      executor_profile_save: [{ profile: saved, affected_runs: [`run-${HOSTILE}`] }, { profile: saved, affected_runs: [] }] } });
  await openProfiles(page);
  await expect(list(page)).toContainText("아직 저장한 실행 프로필이 없습니다");
  await list(page).getByRole("button", { name: "새 실행 프로필" }).click();
  const f = form(page);
  await expect(f.getByRole("heading", { name: "새 실행 프로필" })).toBeFocused();
  await expect(f.getByLabel("연결 대상")).toHaveValue("existing_runpod");
  await f.getByLabel("프로필 ID").fill("pod-a");
  await f.getByLabel("이름", { exact: true }).fill("새 Pod");
  await f.getByLabel("Pod ID").fill("pod-777");
  await f.getByLabel("SSH 서버 지문").fill("SHA256:xyz");
  await f.getByLabel("SSH 개인키 파일").fill(KEY_FILE);
  await f.getByLabel("RunPod API 키").fill(KEY);
  await saveButton(page).click();
  await idle(page);
  const saves = await calls(page, { command: "executor_profile_save" });
  expect(saves).toHaveLength(1);
  const { values: { RUNPOD_API_KEY, ...values }, ...rest } = saves[0].args;
  expect(RUNPOD_API_KEY === KEY).toBe(true);
  expect(rest).toEqual({ profileId: "pod-a", copyLegacyCredential: false });
  expect(values).toEqual({ provider: "existing_runpod", name: "새 Pod", pod_id: "pod-777", ssh_fingerprint: "SHA256:xyz", ssh_key_file: KEY_FILE });
  // The list is read again from the native store; the key input is empty and the key is nowhere in the page.
  expect(await calls(page, { command: "executor_profiles_get" })).toHaveLength(2);
  await expect(page.getByText("실행 프로필을 저장했습니다.")).toBeVisible();
  await expect(f.getByLabel("RunPod API 키")).toHaveValue("");
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("pod-777");
  await expect(page.locator(".profile-affected")).toContainText("이 변경 후 다시 계획해야 하는 실행");
  await expect(page.locator(".profile-affected")).toContainText(`run-${HOSTILE}`);
  await expect(f.getByLabel("연결 대상")).toBeDisabled();
  await expect(f.getByLabel("프로필 ID")).toBeDisabled();
  expect(await keyShown(page)).toBe(false);
  // Editing without typing a key sends no RUNPOD_API_KEY (the saved one stays).
  await f.getByLabel("이름", { exact: true }).fill("새 Pod 2");
  await saveButton(page).click();
  await idle(page);
  const second = (await calls(page, { command: "executor_profile_save" }))[1].args;
  expect(Object.keys(second.values).sort()).toEqual(["name", "pod_id", "provider", "ssh_fingerprint", "ssh_key_file"]);
  expect(second).toMatchObject({ profileId: "pod-a", copyLegacyCredential: false, values: { name: "새 Pod 2" } });
  await expect(page.locator(".profile-affected")).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
});

test("an SSH profile picks GPUs from the read-only inventory and keeps its provider", async ({ page }) => {
  const inventory = ok({ profile_id: "lab", revision: "a".repeat(64), observed_at: "2026-10-21T08:00:00+00:00", server_id: "b".repeat(64),
    resource_id: "ssh_server:" + "b".repeat(64), selected_gpu_uuids: [], inventory: [
      { uuid: GPU1, name: "NVIDIA H100 80GB HBM3", memory_mib: 81559 }, { uuid: GPU2, name: HOSTILE, memory_mib: 81559 },
      { uuid: GPU3, name: "NVIDIA H100 80GB HBM3", memory_mib: 81559 }] });
  const selected = server(JSON.stringify([GPU1, GPU2]));
  await openWorkbench(page, { workflow: { controller_status: controller("absent"), observations_list: noObservations, executor_profile_inventory: inventory },
    native: { executor_profiles_get: [{ profiles: [server()] }, { profiles: [selected] }], executor_profile_save: { profile: selected, affected_runs: [] } } });
  await openProfiles(page);
  // A new SSH profile cannot list GPUs before it is saved; the provider choice re-renders its fields.
  await list(page).getByRole("button", { name: "새 실행 프로필" }).click();
  await form(page).getByLabel("연결 대상").selectOption("ssh_server");
  await expect(form(page).getByLabel("연결 대상")).toBeFocused();
  await expect(form(page).getByLabel("서버 주소")).toBeVisible();
  await expect(form(page).getByLabel("RunPod API 키")).toHaveCount(0);
  await expect(form(page).getByRole("button", { name: "GPU 목록 확인" })).toBeDisabled();
  await form(page).getByRole("button", { name: "닫기" }).click();
  // The saved profile lists its GPUs only after the read-only notice.
  await edit(page, 0);
  const f = form(page);
  await expect(f.getByLabel("연결 대상")).toHaveValue("ssh_server");
  await expect(f.getByLabel("연결 대상")).toBeDisabled();
  await f.getByRole("button", { name: "GPU 목록 확인" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("읽기만");
  await dialog.getByRole("button", { name: "돌아가기" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, { action: "executor_profile_inventory" })).toHaveLength(0);
  await f.getByRole("button", { name: "GPU 목록 확인" }).click();
  await page.getByRole("dialog").getByRole("button", { name: "읽기 전용으로 확인" }).click();
  await idle(page);
  expect((await calls(page, { action: "executor_profile_inventory" })).map(call => call.args.data)).toEqual([{ profile_id: "lab" }]);
  const boxes = f.locator("[data-profile-gpu]");
  await expect(boxes).toHaveCount(3);
  await expect(f.locator(".profile-gpus")).toContainText(HOSTILE);
  await boxes.nth(0).check();
  await boxes.nth(1).check();
  await saveButton(page).click();
  await idle(page);
  const [save] = await calls(page, { command: "executor_profile_save" });
  expect(save.args.profileId).toBe("lab");
  expect(Object.keys(save.args.values).sort()).toEqual([...SERVER_FIELDS].sort());
  expect(save.args.values).toMatchObject({ provider: "ssh_server", server_ssh_host: "gpu.example.org", server_ssh_port: "2222",
    server_selected_gpu_uuids: JSON.stringify([GPU1, GPU2]) });
  // Reopening for edit keeps the provider fixed and the saved selection checked.
  await form(page).getByRole("button", { name: "닫기" }).click();
  await expect(rows(page).first()).toContainText("선택 GPU 2개");
  await edit(page, 0);
  await expect(form(page).getByLabel("연결 대상")).toBeDisabled();
  await expect(form(page).locator("[data-profile-gpu]:checked")).toHaveCount(2);
  await expect(page.locator("img")).toHaveCount(0);
});

test("profile save and delete fail closed until the edit check is available", async ({ page }) => {
  // The mock's unscripted save and delete reject with the native fail-closed text (EDIT_CHECK_FAILED).
  await openWorkbench(page, { profiles: [runpod], workflow: { controller_status: controller("absent"), observations_list: noObservations } });
  await openProfiles(page);
  await list(page).getByRole("button", { name: "새 실행 프로필" }).click();
  const f = form(page);
  await f.getByLabel("프로필 ID").fill("pod-b");
  await f.getByLabel("이름", { exact: true }).fill("두 번째 Pod");
  await f.getByLabel("Pod ID").fill("pod-456");
  await saveButton(page).click();
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(EDIT_CHECK_FAILED);
  await expect(f.getByLabel("프로필 ID")).toHaveValue("pod-b");
  await expect(f.getByLabel("이름", { exact: true })).toHaveValue("두 번째 Pod");
  await expect(f.getByLabel("Pod ID")).toHaveValue("pod-456");
  await expect(rows(page)).toHaveCount(1);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(1);
  expect(await calls(page, { command: "executor_profiles_get" })).toHaveLength(1);
  await expect(page.getByText("다시 계획해야 하는 실행")).toHaveCount(0);
  // Delete behaves the same, after its own confirm dialog.
  await f.getByRole("button", { name: "닫기" }).click();
  await edit(page, 0);
  await f.getByLabel("이름", { exact: true }).fill("바꾼 이름");
  await f.getByRole("button", { name: "삭제", exact: true }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("pod-a");
  await dialog.getByRole("button", { name: "돌아가기" }).click();
  expect(await calls(page, { command: "executor_profile_delete" })).toHaveLength(0);
  await f.getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "삭제", exact: true }).click();
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(EDIT_CHECK_FAILED);
  expect((await calls(page, { command: "executor_profile_delete" })).map(call => call.args)).toEqual([{ profileId: "pod-a" }]);
  await expect(f.getByLabel("이름", { exact: true })).toHaveValue("바꾼 이름");
  await expect(rows(page)).toHaveCount(1);
  await expect(rows(page).first()).toContainText("A100 Pod");
  expect(await calls(page, { command: "executor_profiles_get" })).toHaveLength(1);
});

test("a profile in use is refused with the backend reason", async ({ page }) => {
  const saved = { profile: runpod, affected_runs: ["run-1"] };
  await openWorkbench(page, { profiles: [runpod], native: { executor_profile_save: [saved, saved, { reject: PROFILE_IN_USE }],
    executor_profile_delete: { reject: PROFILE_IN_USE } }, workflow: { controller_status: controller("absent"), observations_list: noObservations } });
  await openProfiles(page);
  await edit(page, 0);
  const f = form(page);
  // A refused delete after a save that named runs to plan again no longer shows them.
  await saveButton(page).click();
  await idle(page);
  await expect(page.locator(".profile-affected")).toContainText("run-1");
  await f.getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "삭제", exact: true }).click();
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(PROFILE_IN_USE);
  await expect(page.getByText("다시 계획해야 하는 실행")).toHaveCount(0);
  await expect(rows(page)).toHaveCount(1);
  // The same for a refused save.
  await saveButton(page).click();
  await idle(page);
  await expect(page.locator(".profile-affected")).toContainText("run-1");
  await f.getByLabel("이름", { exact: true }).fill("수정 시도");
  await f.getByLabel("RunPod API 키").fill(KEY);
  await saveButton(page).click();
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(PROFILE_IN_USE);
  await expect(f.getByLabel("이름", { exact: true })).toHaveValue("수정 시도");
  await expect(f.getByLabel("Pod ID")).toHaveValue("pod-123");
  // The typed key lives only in its input until the save reads it; after a refusal it is typed again.
  await expect(f.getByLabel("RunPod API 키")).toHaveValue("");
  await expect(page.getByText("다시 계획해야 하는 실행")).toHaveCount(0);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(3);
  expect(await calls(page, { command: "executor_profile_delete" })).toHaveLength(1);
  expect(await keyShown(page)).toBe(false);
});

test("resource inspection is explicit and read-only", async ({ page }) => {
  const observed = ok({ profile: { provider: "existing_runpod", profile_id: "pod-a", name: "A100 Pod" }, revision: "c".repeat(64),
    facts: { pod_id: "pod-123", gpu_type: "NVIDIA H100 80GB HBM3", gpu_count: 2, total_hourly_cost_usd: 5.98, observed_at: "2026-10-21T08:00:00+00:00" },
    observed_at: "2026-10-21T08:00:00+00:00" });
  await openWorkbench(page, { profiles: [runpod, server()], workflow: { controller_status: controller("absent"), observations_list: noObservations,
    executor_profile_inspect: [observed, invalid([issue("credential_missing", CREDENTIAL_MISSING, "/profile_id")])] } });
  await openProfiles(page);
  expect(await calls(page, { action: "executor_profile_inspect" })).toHaveLength(0);
  // An SSH profile needs a saved GPU selection before the launch probe can observe it.
  await expect(rows(page).nth(1).getByRole("button", { name: /자원 확인/ })).toBeDisabled();
  const row = rows(page).nth(0);
  await row.getByRole("button", { name: /자원 확인/ }).click();
  await expect(page.getByRole("dialog")).toContainText("RunPod API로 Pod 상태를 읽기만 합니다");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(row.getByRole("button", { name: /자원 확인/ })).toBeFocused();
  expect(await calls(page, { action: "executor_profile_inspect" })).toHaveLength(0);
  await row.getByRole("button", { name: /자원 확인/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "읽기 전용으로 확인" }).click();
  await idle(page);
  expect((await calls(page, { action: "executor_profile_inspect" })).map(call => call.args.data)).toEqual([{ profile_id: "pod-a" }]);
  for (const text of ["NVIDIA H100 80GB HBM3 × 2", "시간당 $5.98", "RunPod API 요금", "방금 확인", "1시간 이내"])
    await expect(row).toContainText(text);
  // By keyboard this time (a click at the same spot within 400 ms is swallowed by openDialog's double-click guard).
  await row.getByRole("button", { name: /자원 확인/ }).press("Enter");
  await page.getByRole("dialog").getByRole("button", { name: "읽기 전용으로 확인" }).press("Enter");
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(CREDENTIAL_MISSING);
  expect(await calls(page, { action: "executor_profile_inspect" })).toHaveLength(2);
});

test("controller status shows running, stale and stopped without saved-credential flags", async ({ page }) => {
  await openWorkbench(page, { clock: true, profiles: [runpod], workflow: { observations_list: noObservations, controller_status: [controller("absent"),
    controller("running", { restart_required: { profiles: ["pod-a"], tracking: null } }), controller("stale")] } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(pill(page)).toHaveText("정지");
  await expect(bar(page).locator('[role="status"]')).toHaveCount(1);
  expect(await statusCalls(page)).toBe(1);
  await page.clock.runFor(5000);
  await expect(pill(page)).toHaveText("동작 중");
  await expect(signal(page)).toHaveText("마지막 신호 2초 전");
  await expect(bar(page)).toContainText("컨트롤러 시작 후 바뀐 실행 프로필: pod-a");
  await expect(bar(page)).toContainText(RESTART_PROCEDURE);
  await expect(bar(page)).not.toContainText("MLflow 설정이 바뀌었습니다");
  await expect(bar(page)).not.toContainText("인증값이 저장되어 있지 않습니다");
  await page.clock.runFor(5000);
  await expect(pill(page)).toHaveText("연결 끊김");
  await expect(signal(page)).toHaveText("신호 2분 전 · PC가 절전·종료되었다면 다시 시작하세요");
  await expect(bar(page)).not.toContainText("pod-a");
  expect(await statusCalls(page)).toBe(3);
  // Leaving the page stops the poller.
  await page.locator('nav [data-action="nav:overview"]').click();
  await page.clock.runFor(30000);
  expect(await statusCalls(page)).toBe(3);
});

test("starting the controller is explicit and explains redirected AppData", async ({ page }) => {
  await openWorkbench(page, { workflow: { controller_status: controller("absent"), observations_list: noObservations },
    native: { controller_start: [{ started: true }, { started: false, reason: "already_running" }, { reject: ROOT_UNUSABLE }] } });
  await openProfiles(page);
  expect(await calls(page, { command: "controller_start" })).toHaveLength(0);
  const start = bar(page).getByRole("button", { name: "컨트롤러 시작" });
  await expect(bar(page).getByRole("button", { name: "컨트롤러 정지" })).toBeDisabled();
  const before = await statusCalls(page);
  await start.click();
  await idle(page);
  expect(await calls(page, { command: "controller_start" })).toHaveLength(1);
  await expect(page.getByText("컨트롤러를 시작했습니다. 상태가 ‘동작 중’이 되는지 확인하세요.")).toBeVisible();
  expect(await statusCalls(page)).toBeGreaterThan(before);             // an immediate status read
  await start.click();
  await idle(page);
  await expect(page.getByText("컨트롤러가 이미 동작 중입니다.")).toBeVisible();
  await start.click();
  await idle(page);
  await expect(page.getByRole("alert")).toContainText(ROOT_UNUSABLE);
  await expect(page.getByRole("alert")).toContainText(REDIRECTED_APPDATA);
  expect(await calls(page, { command: "controller_start" })).toHaveLength(3);
  // Without the controller module (not_available) start and stop stay disabled.
  const other = await page.context().newPage();
  await openWorkbench(other);
  await openProfiles(other);
  await expect(pill(other)).toHaveText("컨트롤러 기능이 아직 설치되지 않았습니다");
  await expect(bar(other).getByRole("button", { name: "컨트롤러 시작" })).toBeDisabled();
  await expect(bar(other).getByRole("button", { name: "컨트롤러 정지" })).toBeDisabled();
  expect(await calls(other, { command: "controller_start" })).toHaveLength(0);
});

test("stopping explains that a drain can take hours", async ({ page }) => {
  await openWorkbench(page, { clock: true, workflow: { observations_list: noObservations, controller_stop: ok({ requested: true, owner_id: "owner-1" }),
    controller_status: [controller("running"), controller("running", { stop_requested: true })] } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(bar(page).getByRole("button", { name: "컨트롤러 시작" })).toBeDisabled();
  const stop = bar(page).getByRole("button", { name: "컨트롤러 정지" });
  await stop.click();
  const dialog = page.getByRole("dialog");
  await expect(dialog).toContainText("몇 시간");
  await expect(dialog).toContainText("앱을 닫아도 컨트롤러는 계속 동작합니다.");
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(stop).toBeFocused();
  expect(await calls(page, { command: "controller_stop" })).toHaveLength(0);
  await stop.click();
  await page.getByRole("dialog").getByRole("button", { name: "정지 요청" }).click();
  await idle(page);
  expect(await calls(page, { command: "controller_stop" })).toHaveLength(1);
  await expect(page.getByText("정지를 요청했습니다.", { exact: false })).toBeVisible();
  await expect(pill(page)).toHaveText("정지 요청됨 · 진행 중 단계가 끝나면 정지합니다");
  await expect(stop).toBeDisabled();
});

test("profile forms are not nested and saving is not submitted twice", async ({ page }) => {
  await openWorkbench(page, { profiles: [runpod], holds: ["executor_profile_save"] });
  await openProfiles(page);
  await expect(page.locator("form form")).toHaveCount(0);
  await edit(page, 0);
  await expect(page.locator("form form")).toHaveCount(0);
  const save = saveButton(page);
  await save.dblclick();
  await expect(save).toBeDisabled();
  await form(page).getByLabel("Pod ID").press("Enter");
  await form(page).evaluate(element => (element as HTMLFormElement).requestSubmit());
  expect(await page.evaluate(() => (window as any).__held("executor_profile_save"))).toBe(1);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(1);
  await page.evaluate(() => (window as any).__release("executor_profile_save"));
  await idle(page);
  await expect(page.getByRole("alert")).toHaveText(EDIT_CHECK_FAILED);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(1);
  await expect(save).toBeFocused();
  // Enter in a field saves through the same form, once, and keeps the focus there.
  await form(page).getByLabel("Pod ID").press("Enter");
  await idle(page);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(2);
  await expect(form(page).getByLabel("Pod ID")).toBeFocused();
  await expect(page.locator("form form")).toHaveCount(0);
  await expect(page.locator("dialog")).toHaveCount(0);
});

test("a busy status read is transient and polling backs off", async ({ page }) => {
  const busy = invalid([issue("status_busy", STATUS_BUSY)]);
  await openWorkbench(page, { clock: true, workflow: { observations_list: noObservations,
    controller_status: [controller("running"), busy, busy, controller("running", { runs: { active: 2 } })] } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(pill(page)).toContainText("동작 중");
  await page.clock.runFor(5000);
  await expect(bar(page)).toContainText(STATUS_BUSY);
  await expect(pill(page)).toContainText("동작 중");                     // the last known state stays
  await expect(page.getByRole("alert")).toHaveCount(0);
  expect(await statusCalls(page)).toBe(2);
  await page.clock.runFor(5000);                                        // after a failure: 2 × 5 s
  expect(await statusCalls(page)).toBe(2);
  await page.clock.runFor(5000);
  await expect.poll(() => statusCalls(page)).toBe(3);
  await page.clock.runFor(10000);                                       // after two failures: 4 × 5 s
  expect(await statusCalls(page)).toBe(3);
  await page.clock.runFor(10000);
  await expect.poll(() => statusCalls(page)).toBe(4);
  await expect(bar(page)).not.toContainText(STATUS_BUSY);
  await expect(bar(page)).toContainText("진행 중인 그래프 실행 2개");
});

test("the bar names what a restart changes and what needs a saved credential first", async ({ page }) => {
  const running = controller("running", {
    broker: { at: 1792540800, code: "spawn_failed", error: "이전 시작 실패 기록" },          // hidden while a controller runs
    current: { ok: true, owner_id: "owner-1", profiles: { "pod-a": "a".repeat(64), lab: "b".repeat(64) },
      skipped_profiles: { "pod-a": "credential_missing", lab: "observation_missing" },
      tracking: { view_sha256: "d".repeat(64), auth_mode: "token", effective_auth_mode: "none" } },
    restart_required: { profiles: [], tracking: true }, runs: { active: 1 } });
  const failed = controller("absent", { broker: { at: 1792540800, code: "spawn_failed", error: `${HOSTILE} 백그라운드 실행기를 시작하지 못했습니다.` } });
  await openWorkbench(page, { clock: true, settings: { auth_mode: "token" }, profiles: [runpod, server()],
    workflow: { observations_list: noObservations, controller_status: [running, failed] } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(pill(page)).toContainText("동작 중");
  // A RunPod key saved after the start (pod-a was skipped for it) needs a restart that polling cannot report.
  await expect(bar(page)).toContainText("키 저장 후 컨트롤러를 다시 시작하세요: pod-a");
  await expect(bar(page)).toContainText("lab (자원 확인 기록 없음)");
  // An explicit MLflow auth mode without its credential reads as a missing credential, not as a restart.
  await expect(bar(page)).toContainText("MLflow 인증값이 저장되어 있지 않습니다");
  await expect(bar(page)).not.toContainText("MLflow 설정이 바뀌었습니다");
  await expect(bar(page)).toContainText("진행 중인 그래프 실행 1개");
  await expect(bar(page)).not.toContainText("이전 시작 실패 기록");
  await page.clock.runFor(5000);
  await expect(pill(page)).toHaveText("정지");
  await expect(bar(page)).toContainText(`${HOSTILE} 백그라운드 실행기를 시작하지 못했습니다.`);
  await expect(bar(page)).not.toContainText("키 저장 후");
  await expect(page.locator("img")).toHaveCount(0);
});

// -- review fixes (10.4 review of 4b4c194) --
const observation = (extra: object = {}) => ({ profile_id: "pod-a", revision: "a".repeat(64), observed_at: "2026-10-21T08:00:00+00:00",
  age_seconds: 120, provider: "existing_runpod", name: "A100 Pod", resource_id: "existing_runpod:pod-123", gpu_type: "NVIDIA A100 80GB PCIe",
  gpu_count: 1, total_hourly_cost_usd: 1.89, cost_source: "runpod_api", selected_gpu_uuids: [], profile_current: true, fresh_for_real_plan: true, ...extra });

test("saving or deleting reads the last checks again and drops the session inspection", async ({ page }) => {
  const moved = { ...runpod, fields: { ...runpod.fields, pod_id: "pod-999" } };
  const observed = ok({ profile: { provider: "existing_runpod", profile_id: "pod-a", name: "A100 Pod" }, revision: "a".repeat(64),
    facts: { pod_id: "pod-123", gpu_type: "NVIDIA A100 80GB PCIe", gpu_count: 1, total_hourly_cost_usd: 1.89, observed_at: "2026-10-21T08:00:00+00:00" },
    observed_at: "2026-10-21T08:00:00+00:00" });
  await openWorkbench(page, { workflow: { controller_status: controller("absent"), executor_profile_inspect: observed,
    observations_list: [ok({ observations: [observation()] }), ok({ observations: [observation({ profile_current: false, fresh_for_real_plan: false })] })] },
    native: { executor_profiles_get: [{ profiles: [runpod] }, { profiles: [moved] }, { profiles: [] }],
      executor_profile_save: { profile: moved, affected_runs: [] }, executor_profile_delete: { profile_id: "pod-a", affected_runs: [] } } });
  await openProfiles(page);
  const row = rows(page).first();
  await expect(row).toContainText("실제 실행 계획에 사용 가능");
  await row.getByRole("button", { name: /자원 확인/ }).click();
  await page.getByRole("dialog").getByRole("button", { name: "읽기 전용으로 확인" }).click();
  await idle(page);
  await expect(row).toContainText("방금 확인");
  await edit(page, 0);
  await form(page).getByLabel("Pod ID").fill("pod-999");
  await saveButton(page).click();
  await idle(page);
  // The stored checks are read again (the backend decides what still holds) and this session's inspection is gone.
  expect(await calls(page, { action: "observations_list" })).toHaveLength(2);
  await expect(row).toContainText("확인 후 프로필이 바뀌어 다시 확인이 필요합니다");
  await expect(row).not.toContainText("실제 실행 계획에 사용 가능");
  await expect(row).not.toContainText("방금 확인");
  await form(page).getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "삭제", exact: true }).press("Enter");
  await idle(page);
  expect(await calls(page, { action: "observations_list" })).toHaveLength(3);
  await expect(rows(page)).toHaveCount(0);
});

test("returning to the page never shows the previous controller status as current", async ({ page }) => {
  await openWorkbench(page, { clock: true, workflow: { observations_list: noObservations, controller_status: [controller("running"), controller("stale")] } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(pill(page)).toContainText("동작 중");
  await page.locator('nav [data-action="nav:overview"]').click();
  await gate(page, "controller_status");
  await page.locator('nav [data-action="nav:profiles"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("실행 프로필");
  await expect(page.locator(".notice.busy")).toHaveCount(1);
  // Until this visit's own read answers, the bar claims nothing.
  await expect(pill(page)).toHaveText("상태 확인 중");
  await expect(bar(page)).not.toContainText("동작 중");
  await expect(bar(page)).not.toContainText("마지막 신호");
  await page.evaluate(() => (window as any).__openGate());
  await idle(page);
  await expect(pill(page)).toHaveText("연결 끊김");
});

test("polls patch the bar only when its state changes and announce a transition once", async ({ page }) => {
  const running = (age: number) => controller("running", { controller: { owner_id: "owner-1", state: "running", heartbeat_age_seconds: age } });
  await openWorkbench(page, { clock: true, workflow: { observations_list: noObservations, controller_status: [running(2), running(7), controller("stale")] } });
  await pauseClock(page);
  await openProfiles(page);
  const live = bar(page).locator('[role="status"]');
  await expect(live).toHaveCount(1);
  await expect(live).toHaveText("백그라운드 컨트롤러: 동작 중");
  const marked = '#controller-bar .status-pill, #controller-bar [role="status"]';
  await page.evaluate(selector => document.querySelectorAll(selector).forEach(element => { (element as any).__kept = true; }), marked);
  await page.clock.runFor(5000);
  await expect(signal(page)).toHaveText("마지막 신호 7초 전");
  // Only the age changed: the pill and the live region are the very same nodes, so nothing is announced again.
  expect(await page.evaluate(selector => [...document.querySelectorAll(selector)].map(element => (element as any).__kept === true), marked)).toEqual([true, true]);
  await page.clock.runFor(5000);
  await expect(pill(page)).toHaveText("연결 끊김");
  await expect(live).toHaveText("백그라운드 컨트롤러: 연결 끊김");
  expect(await live.evaluate(element => (element as any).__kept === true)).toBe(true);   // the region persists; its text changed once
});

test("an OIDC mode without its token URL is a missing credential, not a restart", async ({ page }) => {
  const running = controller("running", { restart_required: { profiles: [], tracking: true },
    current: { ok: true, owner_id: "owner-1", profiles: {}, skipped_profiles: {}, tracking: { view_sha256: "d".repeat(64), auth_mode: "oidc", effective_auth_mode: "none" } } });
  const options = { workflow: { observations_list: noObservations, controller_status: running } };
  await openWorkbench(page, { ...options, settings: { auth_mode: "oidc", MLFLOW_OIDC_CLIENT_ID: "mlflow", MLFLOW_OIDC_CLIENT_SECRET_saved: true } });
  await openProfiles(page);
  await expect(bar(page)).toContainText("MLflow 인증 서버 주소(OIDC)가 저장되어 있지 않습니다");
  await expect(bar(page)).not.toContainText("MLflow 설정이 바뀌었습니다");
  // With the token URL and the secret saved, the same answer is a restart.
  const other = await page.context().newPage();
  await openWorkbench(other, { ...options, settings: { auth_mode: "oidc", MLFLOW_OIDC_TOKEN_URL: "https://sso.example.org/token",
    MLFLOW_OIDC_CLIENT_ID: "mlflow", MLFLOW_OIDC_CLIENT_SECRET_saved: true } });
  await openProfiles(other);
  await expect(bar(other)).toContainText("MLflow 설정이 바뀌었습니다");
  await expect(bar(other)).toContainText(RESTART_PROCEDURE);
  await expect(bar(other)).not.toContainText("저장되어 있지 않습니다");
});

test("a RunPod profile saved without a key says it cannot be used yet", async ({ page }) => {
  const keyless = { profile_id: "pod-c", RUNPOD_API_KEY_saved: false, fields: { provider: "existing_runpod", name: "키 없는 Pod",
    pod_id: "pod-c1", ssh_fingerprint: "", ssh_key_file: "" } };
  await openWorkbench(page, { workflow: { controller_status: controller("absent"), observations_list: noObservations },
    native: { executor_profiles_get: [{ profiles: [] }, { profiles: [keyless] }], executor_profile_save: { profile: keyless, affected_runs: [] } } });
  await openProfiles(page);
  await list(page).getByRole("button", { name: "새 실행 프로필" }).click();
  const f = form(page);
  await expect(f).toContainText("화면이 다시 그려지면 입력 중인 키는 지워집니다");
  await f.getByLabel("프로필 ID").fill("pod-c");
  await f.getByLabel("이름", { exact: true }).fill("키 없는 Pod");
  await f.getByLabel("Pod ID").fill("pod-c1");
  await saveButton(page).click();
  await idle(page);
  await expect(page.locator(".notice").filter({ hasText: "실행 프로필을 저장했습니다." })).toContainText("RunPod API 키가 저장되어 있지 않아");
});

// A pin, passing on 4b4c194: the texts this screen recognizes or mocks are the native ones.
test("the native texts this screen relies on match broker.rs and profiles.rs", () => {
  const source = (path: string) => readFileSync(new URL(path, import.meta.url), "utf8");
  const rust = (file: string, name: string) => new RegExp(`const ${name}: &str = "([^"]*)";`).exec(source(`../src-tauri/src/${file}`))?.[1];
  expect(rust("broker.rs", "ROOT_UNUSABLE")).toBe(ROOT_UNUSABLE);
  expect(/const ROOT_UNUSABLE = "([^"]*)";/.exec(source("../src/components/controller-bar.ts"))?.[1]).toBe(ROOT_UNUSABLE);
  expect(rust("profiles.rs", "PROFILE_IN_USE")).toBe(PROFILE_IN_USE);
  expect(`${rust("profiles.rs", "EDIT_CHECK_FAILED")} (${NOT_AVAILABLE})`).toBe(EDIT_CHECK_FAILED);
});

test("the last check stops reading as usable once its hour passes on an open page", async ({ page }) => {
  await openWorkbench(page, { clock: true, profiles: [runpod], workflow: { controller_status: controller("absent"),
    observations_list: ok({ observations: [observation({ age_seconds: 3590 })] }) } });
  await pauseClock(page);
  await openProfiles(page);
  await expect(rows(page).first()).toContainText("실제 실행 계획에 사용 가능");
  await page.clock.runFor(30000);
  await expect(rows(page).first()).toContainText("1시간이 지나 실제 실행 계획 전에 다시 확인이 필요합니다");
  await expect(rows(page).first()).toContainText("1시간 전 확인");
  expect(await calls(page, { action: "observations_list" })).toHaveLength(1);      // a re-render, not a read
});

test("saving waits for a status read in flight, so one sidecar runs at a time", async ({ page }) => {
  await openWorkbench(page, { clock: true, profiles: [runpod], workflow: { controller_status: controller("absent"), observations_list: noObservations },
    native: { executor_profile_save: { profile: runpod, affected_runs: [] } } });
  await pauseClock(page);
  await openProfiles(page);
  await gate(page, "controller_status");
  await page.clock.runFor(5000);                                    // the poll starts and waits at the gate
  await edit(page, 0);
  await saveButton(page).click();
  await expect(page.locator(".notice.busy")).toHaveCount(1);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(0);
  await page.evaluate(() => (window as any).__openGate());
  await idle(page);
  expect(await calls(page, { command: "executor_profile_save" })).toHaveLength(1);
});

test("a finished save or delete never takes the focus from a control outside the page", async ({ page }) => {
  await openWorkbench(page, { profiles: [runpod], holds: ["executor_profile_save", "executor_profile_delete"],
    workflow: { controller_status: controller("absent"), observations_list: noObservations },
    native: { executor_profile_save: { profile: runpod, affected_runs: [] }, executor_profile_delete: { profile_id: "pod-a", affected_runs: [] } } });
  // A control outside #app that survives renders, as the tutorial's popover does.
  await page.evaluate(() => { const outside = document.createElement("button"); outside.id = "outside"; outside.textContent = "바깥"; document.body.append(outside); });
  await openProfiles(page);
  await edit(page, 0);
  await saveButton(page).click();
  await page.locator("#outside").focus();
  await release(page, "executor_profile_save");
  await idle(page);
  await expect(page.getByText("실행 프로필을 저장했습니다.")).toBeVisible();
  await expect(page.locator("#outside")).toBeFocused();
  await form(page).getByRole("button", { name: "삭제", exact: true }).click();
  await page.getByRole("dialog").getByRole("button", { name: "삭제", exact: true }).click();
  await page.locator("#outside").focus();
  await release(page, "executor_profile_delete");
  await idle(page);
  await expect(page.getByText("실행 프로필을 삭제했습니다.")).toBeVisible();
  await expect(page.locator("#outside")).toBeFocused();
});

test("a form locked by a running save is not read back", async ({ page }) => {
  // The unscripted save is refused (fail closed), so nothing replaces the draft afterwards.
  await openWorkbench(page, { profiles: [runpod], holds: ["executor_profile_save"], workflow: { controller_status: controller("absent"), observations_list: noObservations } });
  await openProfiles(page);
  await edit(page, 0);
  await saveButton(page).click();
  await expect(form(page).getByLabel("Pod ID")).toBeDisabled();                  // its fieldset is disabled while busy
  // No typing reaches a locked input; a value put there anyway must not become the draft when navigation reads drafts.
  await form(page).getByLabel("Pod ID").evaluate(input => { (input as HTMLInputElement).value = "잠긴 입력"; });
  await page.locator('nav [data-action="nav:overview"]').click();
  await release(page, "executor_profile_save");
  await idle(page);
  await page.locator('nav [data-action="nav:profiles"]').click();
  await idle(page);
  await expect(form(page).getByLabel("Pod ID")).toHaveValue("pod-123");
});
