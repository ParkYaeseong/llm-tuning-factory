import { test, expect, type Page } from "@playwright/test";
import { calls, conflict, HOSTILE, issue, ok, openWorkbench } from "./workflow-mock.ts";

// Graph runs (10.7): list and detail polled per D10, Korean wait reasons with their originals, operator states and the
// desktop operator release of the root decision (9.8's graph_release_lock), steering and tracking re-arm.
const hex = (c: string, n = 32) => c.repeat(n);
const [RUN, RUN2] = [hex("a"), hex("b")];
const [J_A, A_A, J_B, A_B, J_C, A_C] = ["1", "2", "3", "4", "5", "6"].map(c => hex(c));
const [E1, E2] = [hex("e", 64), hex("f", 64)];
const artifact = (c: string) => hex(c, 64);

// Backend texts (graph_controller.py, graph_store.py, tracking_outbox.py, workflow_runs.py) and the UI texts.
const OPERATOR_CONFIRMATION = "operator confirmation required: release-lock --confirm-execution-stopped";
const AWAITING_RESULT = "waiting for the result of the same launch (its time limit has not passed)";
const OUTPUTS_REFUSED = "the completed attempt's outputs could not be recorded; an operator must inspect it: ValueError: inventory differs";
const BUDGET = "approved budget exhausted: 12 of 10 USD already reserved (historical attempts reserved by the Task 8 migration count too); re-plan with a larger budget";
const UNPRICED = "a pre-Task-8 attempt could not be priced; it reserves the run's whole approved budget";
const EVIDENCE_CHANGED_ACTION = "local evidence changed after it was recorded; re-arming cannot repair it. Restore the attempt's original evidence files, or re-run the node in a new graph run";
const ATTEMPT_STALE = "이 단계의 현재 시도가 아닙니다. 실행 상태를 새로 고친 뒤 현재 시도가 멈췄는지 다시 확인하세요.";
const RELEASE_NOT_APPLICABLE = "이 단계는 잠금 해제로 해결하는 상태가 아닙니다. 이미 해제했다면 컨트롤러가 다음 확인 때 이 단계를 실패로 기록하고 다시 실행하지 않습니다. 그 밖에는 표시된 이유를 확인하세요.";
const RESTART_PROCEDURE = "일시정지 → 진행 중 단계 완료 대기(또는 컨트롤러 정지) → 설정 저장 → 컨트롤러 시작";
const DESTINATION_WAIT = "같은 실행 자원의 다른 단계가 끝나기를 기다리는 중입니다";
const STOP_CONFIRMED = "원격 서버/Pod에서 해당 실행이 멈춘 것을 확인했습니다";
const CANCEL_NOTICE = "진행 중인 단계에 취소를 요청합니다. 원격 종료가 확인될 때까지 자원을 해제하지 않습니다.";
const NO_WINNER = "모든 후보가 기준을 통과하지 못해 최상 모델을 정하지 않았습니다";
const RESULT_OVERDUE = "no result after the launch's time limit; if its remote execution stopped, " + OPERATOR_CONFIRMATION;
const LEDGER = "ledger integrity check failed; no node was failed, and the run waits until an operator restores or removes the record";
const TRACKER_MISSING = "MLflow tracking is not configured for this controller; the real graph run waits for it";
const SIMULATED = "모의 결과는 실제 품질이 아닙니다";
const TEXT_METRIC_NOTE = "텍스트 정답 일치 지표 · 의미적 품질 점수가 아닙니다";
const FORMAT_METRIC_NOTE = "형식 지표 · 품질 지표가 아닙니다";

/** A `graph_run_get` node (graph_controller._node_status with 9.8's job_id). */
const node = (id: string, extra: object = {}) => ({
  node_id: id, label: id, kind: "train", status: "PENDING", job_id: null, job_status: null, attempt_id: null, attempts: 0,
  counted_attempts: 0, max_attempts: 2, resource_id: null, destination_profile_id: "pod-a", uncertain: false,
  awaiting_result: false, result_deadline: null, needs_operator: false, wait_reason: null, error: null, retry_at: null,
  tracking_status: "not_started", registration_status: "none", tracking_action: null, output: null, selection: null,
  cache_seed: null, ...extra });
const model = (c: string) => ({ artifact_id: artifact(c), inventory_sha256: artifact("9") });
const run = (extra: object = {}, nodes: object[] = [node("train-1", { status: "RUNNING" })]) => ({
  run_id: RUN, status: "RUNNING", simulation: false, plan_sha256: artifact("c"), approved_cost_usd: 10, reserved_cost_usd: 2.5,
  budget_exhausted: false, unpriced_attempts: 0, created: 1792540800,
  tracking: { events: { PENDING: 1, ACKNOWLEDGED: 0, FAILED: 0, UNCERTAIN: 0 }, graph: "pending", action: null }, nodes, ...extra });
const settledRun = () => run({ status: "COMPLETED", tracking: { events: { ACKNOWLEDGED: 3 }, graph: "saved", action: null } },
  [node("train-1", { status: "COMPLETED", tracking_status: "saved", registration_status: "registered", output: model("d") })]);
const controllerView = (state: "running" | "stale" | "absent" = "running") =>
  ({ owner_id: state === "absent" ? null : "owner-1", state, heartbeat_age_seconds: state === "absent" ? null : 1 });
const detail = (r: object, extra: object = {}) => ok({ run: r, controller: controllerView(), plan: null, tracking_failures: [], ...extra });
const entry = (r: any, counts: object = { RUNNING: 1 }) =>
  ({ run_id: r.run_id, status: r.status, simulation: r.simulation, plan_sha256: r.plan_sha256, created: r.created, nodes_by_status: counts });
const listOf = (...runs: object[]) => ok({ runs, controller: controllerView() });
/** A `controller_status` answer (9.4 shape). */
const controllerStatus = (state: "running" | "stale" | "absent", extra: object = {}) => ok({
  controller: controllerView(state), current: null, broker: null, stop_requested: false,
  restart_required: { profiles: [], tracking: false }, runs: { active: 1 }, ...extra });

const idle = (page: Page) => expect(page.locator(".notice.busy")).toHaveCount(0);
/** Stops the page clock after boot, so polls run only when a test advances it. */
const pauseClock = async (page: Page) => page.clock.pauseAt(await page.evaluate(() => Date.now() + 1000));
const runList = (page: Page) => page.locator('#app [data-tour="graph-runs"]');
const listRow = (page: Page, runId: string) => runList(page).locator(`tr[data-run-id="${runId}"]`);
const detailCard = (page: Page) => page.locator("#run-detail");
const runState = (page: Page) => detailCard(page).getByRole("status");
const nodeRow = (page: Page, nodeId: string) => detailCard(page).locator(`tr[data-node-id="${nodeId}"]`);
const nodeStatus = (page: Page, nodeId: string) => nodeRow(page, nodeId).locator(".run-node-status .status-pill").first();
const operatorItem = (page: Page, nodeId: string) => detailCard(page).locator(`[data-operator-node="${nodeId}"]`);
const failure = (page: Page, eventId: string) => detailCard(page).locator(`[data-event-id="${eventId}"]`);
const gets = async (page: Page) => (await calls(page, { action: "graph_run_get" })).length;
const dataOf = async (page: Page, action: string) => (await calls(page, { action })).map(call => call.args.data);

async function openRuns(page: Page) {
  await page.locator('nav [data-action="nav:runs"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("그래프 실행");
  await idle(page);
}
async function openRun(page: Page, runId: string) {
  await openRuns(page);
  await listRow(page, runId).getByRole("button", { name: "열기" }).click();
  await expect(detailCard(page)).toBeVisible();
  await idle(page);
}
/** Counts every bridge call the page starts and the most in flight at once; `__holdGet` holds graph_run_get answers. */
async function watchCalls(page: Page) {
  await page.evaluate(() => {
    const w = window as any;
    const original = w.__TAURI_INTERNALS__.invoke;
    Object.assign(w, { __started: 0, __inflight: 0, __maxInflight: 0, __holdGet: false, __holding: false });
    w.__TAURI_INTERNALS__.invoke = async (command: string, args: any) => {
      w.__started++; w.__inflight++; w.__maxInflight = Math.max(w.__maxInflight, w.__inflight);
      try {
        if (w.__holdGet && command === "workflow_action" && args?.action === "graph_run_get") {
          w.__holding = true; w.__holdAt = Date.now();
          await new Promise(resolve => { w.__releaseGet = resolve; });
          w.__holding = false;
        }
        return await original(command, args);
      } finally { w.__inflight--; }
    };
  });
}
async function setVisibility(page: Page, state: "hidden" | "visible") {
  await page.evaluate(state => {
    Object.defineProperty(document, "visibilityState", { configurable: true, get: () => state });
    document.dispatchEvent(new Event("visibilitychange"));
  }, state);
}

test("run detail polls only while visible and stops when settled", async ({ page }) => {
  const running = detail(run());
  // Not settled yet (D10): a paused run whose steps are done, a final run whose graph tracking is pending, and a final
  // run with graph tracking saved while a node's tracking still retries.
  const done = (status: string, graph: string, nodeTracking: string) => detail(run({ status, tracking: { events: {}, graph, action: null } },
    [node("train-1", { status: "COMPLETED", tracking_status: nodeTracking, output: model("d") })]));
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: [running, running, running, running, done("PAUSED", "saved", "saved"), done("COMPLETED", "pending", "saved"),
      done("COMPLETED", "saved", "retrying"), detail(settledRun()), running] } });
  await watchCalls(page);
  await pauseClock(page);
  await openRun(page, RUN);
  expect(await gets(page)).toBe(1);
  for (const expected of [2, 3, 4, 5, 6, 7]) {                          // every 3 s while shown: three polls over 9 s, then on
    await page.clock.runFor(3000);
    await expect.poll(() => gets(page)).toBe(expected);
  }
  await expect(runState(page)).toContainText("완료");
  await expect(nodeRow(page, "train-1").locator(".run-node-tracking")).toContainText("기록 재시도");
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(8);                          // COMPLETED, graph tracking saved, nothing pending
  await expect(nodeRow(page, "train-1").locator(".run-node-tracking")).toContainText("기록됨");
  await page.clock.runFor(30000);
  expect(await gets(page)).toBe(8);
  // Leaving the page stops every poller of the page (detail, list and controller bar).
  await openRun(page, RUN);
  expect(await gets(page)).toBe(9);
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(10);
  await page.locator('nav [data-action="nav:overview"]').click();
  const left = { lists: (await calls(page, { action: "graph_runs_list" })).length, status: (await calls(page, { command: "controller_status" })).length };
  await page.clock.runFor(30000);
  expect(await gets(page)).toBe(10);
  expect((await calls(page, { action: "graph_runs_list" })).length).toBe(left.lists);
  expect((await calls(page, { command: "controller_status" })).length).toBe(left.status);
  // A hidden window pauses polling; showing it again reads at once.
  await openRun(page, RUN);
  expect(await gets(page)).toBe(11);
  await setVisibility(page, "hidden");
  const hidden = await page.evaluate(() => (window as any).__started);
  await page.clock.runFor(12000);
  expect(await page.evaluate(() => (window as any).__started)).toBe(hidden);
  await setVisibility(page, "visible");
  await page.clock.runFor(1);                                           // the poke's zero delay, on the paused page clock
  await expect.poll(() => gets(page)).toBe(12);
  // A slow answer (7 s) is never overlapped: nothing else starts meanwhile, and the next poll waits 2 × 7 s.
  await page.clock.runFor(3000);                                        // lets the other pollers settle after the poke
  const base = await gets(page);
  await page.evaluate(() => { (window as any).__holdGet = true; });
  await page.clock.runFor(3000);
  await expect.poll(() => page.evaluate(() => (window as any).__holding)).toBe(true);
  const started = await page.evaluate(() => (window as any).__started);
  await page.clock.runFor(7000);
  expect(await page.evaluate(() => (window as any).__started)).toBe(started);
  const held = await page.evaluate(() => { const w = window as any; w.__holdGet = false; w.__releaseGet(); return Date.now() - w.__holdAt; });
  expect(held).toBeGreaterThanOrEqual(7000);
  await expect.poll(() => gets(page)).toBe(base + 1);
  await page.clock.runFor(2 * held - 1000);                             // well past the 3 s base
  expect(await gets(page)).toBe(base + 1);
  await page.clock.runFor(1000);
  await expect.poll(() => gets(page)).toBe(base + 2);
  expect(await page.evaluate(() => (window as any).__maxInflight)).toBe(1);
});

test("node states, wait reasons and tracking are shown in Korean with originals kept", async ({ page }) => {
  const nodes = [
    node("train-1", { label: "기본 학습", status: "RUNNING", job_id: J_A, attempt_id: A_A, attempts: 1, counted_attempts: 1,
      resource_id: "existing_runpod:pod-a", tracking_status: "pending" }),
    node("eval-1", { label: "평가", kind: "evaluate", wait_reason: "waiting for destination existing_runpod:pod-a" }),
    node("train-2", { status: "COMPLETED", tracking_status: "saved", registration_status: "registered", output: model("d") }),
    node("train-3", { status: "FAILED", error: "dispatch failed: timeout", tracking_status: "failed" }),
    node("train-4", { status: "BLOCKED" }),
    node("train-5", { status: "CANCELED", tracking_status: "uncertain" }),
    node("train-6", { status: "READY", tracking_status: "retrying" }),
    node("eval-2", { label: "다음 평가", kind: "evaluate", wait_reason: "waiting for train-1" }),
    node("eval-3", { label: "기록 뒤 평가", kind: "evaluate",
      wait_reason: "waiting for tracking of train-2 (tracking failed: re-arm the tracking event once MLflow is reachable)" }),
  ];
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: detail(run({}, nodes)) } });
  await openRun(page, RUN);
  // The most common reason: a PENDING step behind an unfinished parent, named by the parent's label.
  const behind = nodeRow(page, "eval-2").locator('[data-reason="waiting"]');
  await expect(behind).toContainText("앞 단계 ‘기본 학습’이(가) 끝나기를 기다리는 중입니다");
  await behind.getByText("원문 보기").click();
  await expect(behind).toContainText("waiting for train-1");
  const tracked = nodeRow(page, "eval-3").locator('[data-reason="waiting"]');
  await expect(tracked).toContainText("앞 단계 ‘train-2’의 MLflow 기록");
  await expect(tracked).toContainText("기록 다시 시도");
  await expect(nodeRow(page, "eval-3").locator('[data-reason="error"]')).toHaveCount(0);
  const waiting = nodeRow(page, "eval-1");
  await expect(waiting).toContainText(DESTINATION_WAIT);
  await expect(waiting.getByText("waiting for destination existing_runpod:pod-a")).toBeHidden();
  await waiting.getByText("원문 보기").click();
  await expect(waiting.getByText("waiting for destination existing_runpod:pod-a")).toBeVisible();
  const expected: Record<string, [string, string[]]> = {
    "train-1": ["실행 중", ["기록 대기"]], "eval-1": ["대기", ["기록 전"]], "train-2": ["완료", ["기록됨", "등록됨"]],
    "train-3": ["실패", ["기록 실패"]], "train-4": ["앞 단계 실패로 중단", []], "train-5": ["취소", ["기록 확인 필요"]],
    "train-6": ["준비됨", ["기록 재시도"]] };
  for (const [id, [status, tracking]] of Object.entries(expected)) {
    await expect(nodeStatus(page, id)).toHaveText(status);
    for (const text of tracking) await expect(nodeRow(page, id).locator(".run-node-tracking")).toContainText(text);
  }
  await expect(nodeRow(page, "train-1")).toContainText("기본 학습");
  await expect(nodeRow(page, "train-1")).toContainText("학습");
  await expect(nodeRow(page, "eval-1")).toContainText("평가");
  await expect(nodeRow(page, "train-1")).toContainText("1/2");
  await expect(nodeRow(page, "train-1")).toContainText("pod-a");
  await expect(nodeRow(page, "train-3")).toContainText("dispatch failed: timeout");
  for (const raw of ["RUNNING", "PENDING", "BLOCKED", "not_started"]) await expect(detailCard(page).locator("tbody")).not.toContainText(raw);
  await expect(runState(page)).toContainText("실행 중");
  await expect(runState(page)).toContainText("실제 실행");
  await expect(detailCard(page).locator(".run-meta")).toContainText("기록 대기");
  await expect(detailCard(page).locator(".run-meta")).toContainText("$10.00");
  await expect(detailCard(page).locator(".run-meta")).toContainText("$2.50");
});

test("a single destination keeps the other branch waiting", async ({ page }) => {
  const nodes = [
    node("train-a", { label: "가지 A", status: "RUNNING", job_id: J_A, attempt_id: A_A, attempts: 1, counted_attempts: 1, resource_id: "existing_runpod:pod-a" }),
    node("train-b", { label: "가지 B", status: "PENDING", wait_reason: "waiting for destination existing_runpod:pod-a" }),
  ];
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run(), { PENDING: 1, RUNNING: 1 })),
    controller_status: controllerStatus("running"), graph_run_get: detail(run({}, nodes)) } });
  await openRuns(page);
  await expect(listRow(page, RUN)).toContainText("실행 중 1");
  await expect(listRow(page, RUN)).toContainText("대기 1");
  await listRow(page, RUN).getByRole("button", { name: "열기" }).click();
  await idle(page);
  await expect(nodeStatus(page, "train-a")).toHaveText("실행 중");
  await expect(nodeRow(page, "train-a")).toContainText("pod-a");
  await expect(nodeStatus(page, "train-b")).toHaveText("대기");
  await expect(nodeRow(page, "train-b").locator('[data-reason="waiting"]')).toContainText(DESTINATION_WAIT);
  await expect(nodeRow(page, "train-b")).not.toContainText("운영자 확인 필요");
});

test("awaiting a late result is different from needing the operator", async ({ page }) => {
  const deadline = 1792544400;
  const nodes = [
    node("train-a", { label: "결과 대기 단계", status: "RUNNING", job_id: J_A, attempt_id: A_A, attempts: 1, counted_attempts: 1,
      job_status: "FAILED", uncertain: true, awaiting_result: true, result_deadline: deadline, wait_reason: AWAITING_RESULT }),
    node("train-b", { label: "확인 필요 단계", status: "RUNNING", job_id: J_B, attempt_id: A_B, attempts: 1, counted_attempts: 1,
      job_status: "FAILED", uncertain: true, needs_operator: true, wait_reason: OPERATOR_CONFIRMATION }),
    node("train-c", { label: "결과 기록 실패 단계", status: "RUNNING", job_id: J_C, attempt_id: A_C, attempts: 1, counted_attempts: 1,
      job_status: "COMPLETED", needs_operator: true, wait_reason: OUTPUTS_REFUSED }),
    // Released already (by another session, the CLI, or before a lost answer): the lock is gone until the next tick.
    node("train-d", { label: "해제된 단계", status: "RUNNING", job_id: hex("7"), attempt_id: hex("8"), attempts: 1, counted_attempts: 1,
      job_status: "FAILED", needs_operator: true, wait_reason: RESULT_OVERDUE }),
    node("train-e", { label: "기록 검사 실패 단계", status: "RUNNING", job_id: hex("9"), attempt_id: hex("0"), attempts: 1,
      counted_attempts: 1, job_status: "COMPLETED", needs_operator: true, wait_reason: LEDGER }),
  ];
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: detail(run({}, nodes), { controller: controllerView("absent") }) } });
  await openRun(page, RUN);
  const local = await page.evaluate(seconds => new Date(seconds * 1000).toLocaleString("ko-KR",
    { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }), deadline);
  const awaiting = operatorItem(page, "train-a");
  await expect(awaiting).toContainText("결과 대기");
  await expect(awaiting).toContainText(`${local}까지는 다시 실행하지 않습니다`);
  await expect(awaiting).not.toContainText("release-lock");
  await expect(awaiting).not.toContainText("운영자 확인 필요");
  await expect(awaiting.getByRole("button")).toHaveCount(0);
  await expect(nodeRow(page, "train-a")).toContainText("결과 대기");
  const operator = operatorItem(page, "train-b");
  await expect(operator).toContainText("운영자 확인 필요");
  for (const text of ["factory status", "release-lock", "--confirm-execution-stopped", J_B, A_B, "%APPDATA%\\kr.kbiofoundry.tuningfactory\\state"])
    await expect(operator).toContainText(text);
  await expect(operator.getByRole("button", { name: "실행 잠금 해제" })).toHaveCount(1);
  await expect(nodeRow(page, "train-b")).toContainText("운영자 확인 필요");
  // needs_operator without an uncertain attempt (outputs refused, ledger check): no lock release settles it.
  const stuck = operatorItem(page, "train-c");
  await expect(stuck).toContainText("운영자 확인 필요");
  await expect(stuck).toContainText("데스크톱에서 해결할 수 없습니다");
  await expect(stuck).toContainText("factory status");
  await expect(stuck.getByRole("button")).toHaveCount(0);
  const released = operatorItem(page, "train-d");
  await expect(released).toContainText("잠금이 이미 해제되었습니다");
  await expect(released).toContainText("컨트롤러를 시작");
  await expect(released).not.toContainText("데스크톱에서 해결할 수 없습니다");
  await expect(released.getByRole("button")).toHaveCount(0);
  await expect(operatorItem(page, "train-e")).toContainText("데스크톱에서 해결할 수 없습니다");
  // Only the uncertain attempt that no late result can settle offers the release.
  await expect(detailCard(page).getByRole("button", { name: /잠금 해제/ })).toHaveCount(1);
  // A focus or open key names one control in the region (a reason shown in the table and in the operator list).
  for (const attribute of ["data-focus-key", "data-open-key"]) {
    const keys = await detailCard(page).locator(`[${attribute}]`).evaluateAll((items, name) => items.map(item => item.getAttribute(name)), attribute);
    expect(keys.length, attribute).toBeGreaterThan(3);
    expect(new Set(keys).size, attribute).toBe(keys.length);
  }
  expect(await calls(page, { action: "graph_release_lock" })).toHaveLength(0);
});

test("budget exhaustion and unpriced attempts explain the re-plan", async ({ page }) => {
  const nodes = [node("train-1", { wait_reason: BUDGET }), node("train-2", { wait_reason: UNPRICED })];
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: detail(run({ budget_exhausted: true, unpriced_attempts: 1, reserved_cost_usd: 12, approved_cost_usd: 10 }, nodes)) } });
  await openRun(page, RUN);
  await expect(detailCard(page)).toContainText("승인 예산 소진: 예약 $12.00 / 승인 $10.00 — 더 큰 예산으로 다시 계획하세요");
  await expect(detailCard(page)).toContainText("가격을 알 수 없는 이전 시도 1개가 승인 예산 전체를 예약했습니다");
  await expect(nodeRow(page, "train-1").locator('[data-reason="budget"]')).toContainText("승인 예산");
  await expect(nodeRow(page, "train-2").locator('[data-reason="budget"]')).toContainText("가격을 알 수 없는 이전 시도");
  await nodeRow(page, "train-1").getByText("원문 보기").click();
  await expect(nodeRow(page, "train-1")).toContainText("approved budget exhausted");
});

test("pause, resume and cancel steer the run and refresh on conflicts", async ({ page }) => {
  const canceled = run({ status: "CANCELED" }, [node("train-1", { status: "CANCELED" })]);
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: [detail(run()), detail(canceled)], graph_pause: ok({ run: run({ status: "PAUSED" }), controller: controllerView() }),
    graph_resume: conflict([issue("run_state", "canceled graph cannot resume", "/run_id")]) } });
  await openRun(page, RUN);
  // Cancel asks first; Esc sends nothing and gives the focus back.
  const cancel = detailCard(page).getByRole("button", { name: "취소", exact: true });
  await cancel.click();
  await expect(page.getByRole("dialog")).toContainText(CANCEL_NOTICE);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(cancel).toBeFocused();
  expect(await calls(page, { action: "graph_cancel" })).toHaveLength(0);
  await detailCard(page).getByRole("button", { name: "일시정지" }).click();
  await idle(page);
  expect(await dataOf(page, "graph_pause")).toEqual([{ run_id: RUN }]);
  await expect(runState(page)).toContainText("일시정지");
  const resume = detailCard(page).getByRole("button", { name: "다시 진행" });
  await expect(resume).toBeFocused();
  const before = await gets(page);
  await resume.click();
  await idle(page);
  expect(await dataOf(page, "graph_resume")).toEqual([{ run_id: RUN }]);
  await expect(page.getByRole("alert")).toContainText("취소한 실행은 다시 진행할 수 없습니다");
  expect(await gets(page)).toBe(before + 1);                            // a run_state conflict reads the run again
  await expect(runState(page)).toContainText("취소");
  await expect(detailCard(page).getByRole("button", { name: /일시정지|다시 진행|^취소$/ })).toHaveCount(0);
  // Confirming the cancel sends it once and shows the requested state.
  const other = await page.context().newPage();
  const requested = run({ status: "CANCELED" }, [node("train-1", { status: "RUNNING", job_status: "RUNNING", job_id: J_A, attempt_id: A_A })]);
  await openWorkbench(other, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: detail(run()), graph_cancel: ok({ run: requested, controller: controllerView() }) } });
  await openRun(other, RUN);
  await detailCard(other).getByRole("button", { name: "취소", exact: true }).click();
  await other.getByRole("dialog").getByRole("button", { name: "취소 요청" }).click();
  await idle(other);
  expect(await dataOf(other, "graph_cancel")).toEqual([{ run_id: RUN }]);
  await expect(runState(other)).toContainText("취소");
  await expect(other.getByText("취소를 요청했습니다", { exact: false })).toBeVisible();
});

test("failed tracking can be re-armed and changed evidence is explained", async ({ page }) => {
  const failures = [
    { event_id: E1, kind: "attempt_evidence", node_id: "train-1", attempt_id: A_A, status: "FAILED", tries: 3,
      last_error: "MLflow request failed: HTTP 503", action: null },
    { event_id: E2, kind: "attempt_run", node_id: "train-1", attempt_id: A_A, status: "UNCERTAIN", tries: 1,
      last_error: "local evidence changed after enqueue: node-outputs.json", action: EVIDENCE_CHANGED_ACTION }];
  const tracked = (graph: string) => run({ status: "COMPLETED", tracking: { events: { ACKNOWLEDGED: 2, FAILED: 1, UNCERTAIN: 1 }, graph,
    action: graph === "failed" ? EVIDENCE_CHANGED_ACTION : null } }, [node("train-1", { label: "기본 학습", status: "COMPLETED",
    tracking_status: graph, tracking_action: graph === "failed" ? EVIDENCE_CHANGED_ACTION : null, output: model("d") })]);
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(tracked("failed"))), controller_status: controllerStatus("running"),
    graph_run_get: [detail(tracked("failed"), { tracking_failures: failures }), detail(tracked("pending"), { tracking_failures: [failures[1]] }),
      detail(tracked("pending"))],
    tracking_rearm: [ok({ event_id: E1, run_id: RUN, node_id: "train-1", kind: "attempt_evidence", status: "PENDING" }),
      ok({ event_id: E2, run_id: RUN, node_id: "train-1", kind: "attempt_run", status: "PENDING" },
        [issue("tracking_evidence_changed", EVIDENCE_CHANGED_ACTION, "/event_id")])] } });
  await pauseClock(page);
  await openRun(page, RUN);
  // Failed graph tracking settles the run: it is not polled until something is re-armed.
  await page.clock.runFor(9000);
  expect(await gets(page)).toBe(1);
  await expect(detailCard(page).locator(".run-meta")).toContainText("기록 실패");
  await expect(detailCard(page).locator(".run-meta")).toContainText("다시 시도해도 고칠 수 없습니다");
  const first = failure(page, E1);
  for (const text of ["시도 결과 파일", "기본 학습", "기록 실패", "3회", "MLflow request failed: HTTP 503"]) await expect(first).toContainText(text);
  await expect(first.locator(".run-tracking-action")).toHaveCount(0);
  const second = failure(page, E2);
  await expect(second).toContainText("기록 확인 필요");
  await expect(second.locator(".run-tracking-error")).toContainText("로컬 증거 파일이 바뀌었습니다");
  await second.locator(".run-tracking-error").getByText("원문 보기").click();
  await expect(second.locator(".run-tracking-error")).toContainText("local evidence changed after enqueue: node-outputs.json");
  const action = second.locator(".run-tracking-action");
  await expect(action).toContainText("다시 시도해도 고칠 수 없습니다");
  const [actionBox, buttonBox] = [await action.boundingBox(), await second.getByRole("button", { name: "기록 다시 시도" }).boundingBox()];
  expect(actionBox!.y).toBeLessThan(buttonBox!.y);                     // the action text sits above its button
  await first.getByRole("button", { name: "기록 다시 시도" }).click();
  await idle(page);
  expect(await dataOf(page, "tracking_rearm")).toEqual([{ event_id: E1 }]);
  expect(await gets(page)).toBe(2);                                     // the run is read again at once
  await expect(failure(page, E1)).toHaveCount(0);
  await failure(page, E2).getByRole("button", { name: "기록 다시 시도" }).click();
  await idle(page);
  expect(await dataOf(page, "tracking_rearm")).toEqual([{ event_id: E1 }, { event_id: E2 }]);
  const warning = detailCard(page).locator(".run-rearm-warning");
  await expect(warning).toContainText("다시 시도해도 고칠 수 없습니다");
  await warning.getByText("원문 보기").click();
  await expect(warning).toContainText("re-arming cannot repair it");
  // Pending tracking is followed again.
  const after = await gets(page);
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(after + 1);
  await expect(detailCard(page).locator(".run-rearm-warning")).toContainText("다시 시도해도 고칠 수 없습니다");
});

test("selection results show the winner, no winner and a kept original", async ({ page }) => {
  // train-a, train-b → merge-m; each model evaluated; select-1 keeps an original, select-2 picks the merge.
  const ref = (id: string, output = "model") => ({ node_id: id, output_name: output });
  const graph = { nodes: [
    { id: "train-a", kind: "train", label: "원본 A" }, { id: "train-b", kind: "train", label: "원본 B" },
    { id: "merge-m", kind: "merge", label: "병합 M", models: [ref("train-a"), ref("train-b")], weights: [0.5, 0.5] },
    { id: "eval-a", kind: "evaluate", label: "평가 A", model: ref("train-a") },
    { id: "eval-b", kind: "evaluate", label: "평가 B", model: ref("train-b") },
    { id: "eval-m", kind: "evaluate", label: "평가 M", model: ref("merge-m") },
    { id: "select-1", kind: "select", label: "선택 1", evaluations: [ref("eval-a", "evaluation"), ref("eval-b", "evaluation"), ref("eval-m", "evaluation")] },
    { id: "select-2", kind: "select", label: "선택 2", evaluations: [ref("eval-a", "evaluation"), ref("eval-m", "evaluation")] },
  ], depends_on: [] };
  const done = { status: "COMPLETED", tracking_status: "saved" };
  const metrics = (values: object) => ({ evaluation_status: "completed", metrics: values });
  const nodes = (simulation: boolean) => [
    node("train-a", { ...done, label: "원본 A", output: model("a") }), node("train-b", { ...done, label: "원본 B", output: model("b") }),
    node("merge-m", { ...done, kind: "merge", label: "병합 M", output: model("m") }),
    node("eval-a", { ...done, kind: "evaluate", label: "평가 A", output: metrics({ exact_match: 0.5, character_f1: 0.75 }) }),
    node("eval-b", { ...done, kind: "evaluate", label: "평가 B", output: metrics({ json_validity: 1 }) }),
    node("eval-m", { ...done, kind: "evaluate", label: "평가 M", output: metrics({ accuracy: 0.4 }) }),
    node("select-1", { ...done, kind: "select", label: "선택 1", output: simulation ? null : model("a"), selection: simulation ? "no_winner" : artifact("a") }),
    node("select-2", { ...done, kind: "select", label: "선택 2", output: model("m"), selection: artifact("m") }),
  ];
  const plan = { plan_sha256: artifact("c"), simulation: false, project_id: "proj-1", project_revision: artifact("7"), nodes: [], datasets: [] };
  const real = run({ status: "COMPLETED", tracking: { events: {}, graph: "saved", action: null } }, nodes(false));
  const simulated = { ...run({ status: "COMPLETED", simulation: true, tracking: { events: {}, graph: "saved", action: null } }, nodes(true)), run_id: RUN2 };
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(real), entry(simulated)), controller_status: controllerStatus("running"),
    graph_run_get: [detail(real, { plan }), detail(simulated, { plan: { ...plan, simulation: true } }), detail(real, { plan })],
    project_get: ok({ project: { schema_version: 1, project_id: "proj-1", name: "프로젝트", purpose: "", task_type: "", datasets: [],
      default_executor_profile_id: null, graph, legacy_origin: null }, revision_sha256: artifact("7") }) } });
  await pauseClock(page);
  await openRun(page, RUN);
  expect(await dataOf(page, "project_get")).toEqual([{ project_id: "proj-1", revision: artifact("7") }]);
  await expect(nodeRow(page, "select-1")).toContainText("원본 최상 모델 유지");
  await expect(nodeRow(page, "select-1")).toContainText("원본 A");
  await expect(nodeRow(page, "select-2")).toContainText("병합 모델 선택");
  await expect(nodeRow(page, "select-2")).toContainText("병합 M");
  await expect(nodeRow(page, "eval-a")).toContainText("exact_match");
  await expect(nodeRow(page, "eval-a")).toContainText(TEXT_METRIC_NOTE);
  await expect(nodeRow(page, "eval-b")).toContainText(FORMAT_METRIC_NOTE);
  await expect(nodeRow(page, "eval-m")).not.toContainText(TEXT_METRIC_NOTE);
  await expect(nodeRow(page, "train-a")).toContainText(artifact("a").slice(0, 8));
  await expect(detailCard(page)).not.toContainText(SIMULATED);
  await openRun(page, RUN2);
  await expect(nodeRow(page, "select-1")).toContainText(NO_WINNER);
  await expect(nodeRow(page, "select-1")).toContainText(SIMULATED);
  await expect(nodeRow(page, "eval-a")).toContainText(SIMULATED);
  await expect(runState(page)).toContainText("모의 실행");
  // The planned graph (a pinned project revision) is read once per opened run.
  expect(await dataOf(page, "project_get")).toHaveLength(2);
  await openRun(page, RUN);
  expect(await dataOf(page, "project_get")).toHaveLength(2);
  await expect(nodeRow(page, "select-1")).toContainText("원본 최상 모델 유지");
});

test("run data is escaped and polls keep keyboard focus", async ({ page }) => {
  const nodes = (counted: number) => [node("train-1", { label: HOSTILE, status: "RUNNING", job_id: J_A, attempt_id: A_A, attempts: counted,
    counted_attempts: counted, max_attempts: 3, error: HOSTILE, wait_reason: `${HOSTILE} unknown reason`, destination_profile_id: HOSTILE })];
  const failures = [{ event_id: E1, kind: "node_run", node_id: "train-1", attempt_id: null, status: "FAILED", tries: 1, last_error: HOSTILE, action: HOSTILE }];
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(run(), { [HOSTILE]: 1 })), controller_status: controllerStatus("running"),
    graph_run_get: [1, 2, 3].map(counted => detail(run({ tracking: { events: {}, graph: "pending", action: HOSTILE } }, nodes(counted)),
      { tracking_failures: failures })) } });
  await pauseClock(page);
  await openRuns(page);
  await expect(listRow(page, RUN)).toContainText(HOSTILE);
  await listRow(page, RUN).getByRole("button", { name: "열기" }).click();
  await idle(page);
  await expect(nodeRow(page, "train-1")).toContainText(HOSTILE);
  await expect(failure(page, E1)).toContainText(HOSTILE);
  await expect(page.locator("img")).toHaveCount(0);
  const pause = detailCard(page).getByRole("button", { name: "일시정지" });
  await pause.focus();
  for (const counted of [2, 3]) {
    await page.clock.runFor(3000);
    await expect(nodeRow(page, "train-1")).toContainText(`${counted}/3`);
    await expect(pause).toBeFocused();
  }
  // An unchanged answer (the last one repeats) leaves the page as it is: no patch at all (D10).
  await page.evaluate(() => {
    const w = window as any;
    w.__mutations = 0;
    new MutationObserver(records => { w.__mutations += records.length; }).observe(document.querySelector(".runs-layout")!, { childList: true, subtree: true, characterData: true });
  });
  const before = await gets(page);
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(before + 1);
  expect(await page.evaluate(() => (window as any).__mutations)).toBe(0);
  await expect(pause).toBeFocused();
  await expect(page.locator("img")).toHaveCount(0);
});

test("an unreadable run and a missing controller are shown without breaking the page", async ({ page }) => {
  const errors: string[] = [];
  page.on("pageerror", error => errors.push(error.message));
  await openWorkbench(page, { clock: true, workflow: {
    graph_runs_list: listOf({ run_id: RUN2, error: `unreadable graph run: ValueError: ${HOSTILE}` }, entry(run())),
    graph_run_get: [ok({ run: { run_id: RUN, error: "unreadable graph run: JSONDecodeError: Expecting value" }, controller: controllerView("absent"),
      plan: null, tracking_failures: [] }), detail({ ...run(), run_id: RUN2 })],
    controller_status: [controllerStatus("absent"), controllerStatus("running", { restart_required: { profiles: ["pod-a"], tracking: null } })] } });
  expect(await calls(page, { command: "workflow_action" })).toEqual([]);   // nothing at boot
  await pauseClock(page);
  const before = (await calls(page)).length;
  await openRuns(page);
  const entered = (await calls(page)).slice(before).map(call => call.command === "workflow_action" ? `workflow:${call.args.action}` : call.command);
  expect([...new Set(entered)].sort()).toEqual(["controller_status", "workflow:graph_runs_list"]);
  await expect(runList(page)).toHaveCount(1);
  const unreadable = listRow(page, RUN2);
  await expect(unreadable).toContainText("읽을 수 없는 실행");
  await expect(unreadable.getByRole("button", { name: "열기" })).toHaveCount(0);
  await unreadable.getByText("원문 보기").click();
  await expect(unreadable).toContainText(HOSTILE);
  await expect(listRow(page, RUN).getByRole("button", { name: "열기" })).toBeVisible();
  const bar = page.locator("#controller-bar");
  await expect(bar.locator(".status-pill").first()).toHaveText("정지");
  await expect(bar.getByRole("button", { name: "컨트롤러 시작" })).toBeEnabled();
  expect(await calls(page, { command: "controller_start" })).toHaveLength(0);
  await page.clock.runFor(5000);
  await expect(bar).toContainText("컨트롤러 시작 후 바뀐 실행 프로필: pod-a");
  await expect(bar).toContainText(RESTART_PROCEDURE);
  // The list is read every 10 s while shown.
  const lists = (await calls(page, { action: "graph_runs_list" })).length;
  await page.clock.runFor(5000);
  await expect.poll(async () => (await calls(page, { action: "graph_runs_list" })).length).toBe(lists + 1);
  // An unreadable detail is named as such, without steering actions.
  await listRow(page, RUN).getByRole("button", { name: "열기" }).click();
  await idle(page);
  await expect(detailCard(page)).toContainText("읽을 수 없는 실행");
  await expect(detailCard(page).getByRole("button", { name: /일시정지|다시 진행|^취소$/ })).toHaveCount(0);
  // An answer about another run is never shown as this one: the last state stays with a read error.
  await page.clock.runFor(3000);
  await expect(detailCard(page)).toContainText("엔진 응답 형식이 올바르지 않습니다.");
  await expect(detailCard(page)).toContainText("읽을 수 없는 실행");
  await expect(detailCard(page).getByRole("button", { name: /일시정지|다시 진행|^취소$/ })).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("the operator release needs the stop confirmation and the typed node label", async ({ page }) => {
  const label = `${HOSTILE} 학습 B`;
  const stuck = (released: boolean) => node("train-b", { label, status: "RUNNING", job_id: J_B, attempt_id: A_B, attempts: 1, counted_attempts: 1,
    job_status: "FAILED", uncertain: !released, needs_operator: true, wait_reason: OPERATOR_CONFIRMATION });
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: [detail(run({}, [stuck(false)])), detail(run({}, [stuck(true)]))],
    graph_release_lock: ok({ released: { run_id: RUN, node_id: "train-b", job_id: J_B, attempt_id: A_B }, run: run({}, [stuck(true)]),
      controller: controllerView() }) } });
  await openRun(page, RUN);
  const opener = operatorItem(page, "train-b").getByRole("button", { name: "실행 잠금 해제" });
  await opener.click();
  const dialog = page.getByRole("dialog");
  for (const text of ["같은 GPU", "다시 실행하지 않습니다", "취소로 기록", label, J_B, A_B, "release-lock", "--confirm-execution-stopped"])
    await expect(dialog).toContainText(text);
  await expect(page.locator("img")).toHaveCount(0);
  const confirm = dialog.getByRole("button", { name: "잠금 해제", exact: true });
  const stopped = dialog.getByRole("checkbox", { name: STOP_CONFIRMED });
  const typed = dialog.getByRole("textbox");
  await expect(confirm).toBeDisabled();
  await page.keyboard.press("Escape");                                   // Esc sends nothing
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await expect(opener).toBeFocused();
  await opener.click();
  await stopped.check();
  await expect(confirm).toBeDisabled();                                  // the label is not typed yet
  await typed.fill("학습 B");
  await expect(confirm).toBeDisabled();
  await typed.fill(label);
  await expect(confirm).toBeEnabled();
  await stopped.uncheck();
  await expect(confirm).toBeDisabled();                                  // both are needed
  await stopped.check();
  await expect(confirm).toBeEnabled();
  await dialog.getByRole("button", { name: "돌아가기" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(await calls(page, { action: "graph_release_lock" })).toHaveLength(0);
  // A new dialog starts empty; confirming sends the IDs shown, once.
  await opener.click();
  await expect(stopped).not.toBeChecked();
  await expect(typed).toHaveValue("");
  await stopped.check();
  await typed.fill(label);
  await confirm.dblclick();
  await idle(page);
  expect(await dataOf(page, "graph_release_lock")).toEqual([{ run_id: RUN, node_id: "train-b", job_id: J_B, attempt_id: A_B,
    confirm_execution_stopped: true }]);
  await expect(page.getByText("잠금을 해제했습니다", { exact: false })).toBeVisible();
  const item = operatorItem(page, "train-b");
  await expect(item).toContainText("잠금이 이미 해제되었습니다");
  await expect(item).not.toContainText("컨트롤러를 시작");
  await expect(item.getByRole("button")).toHaveCount(0);
  await expect(page.locator("img")).toHaveCount(0);
});

test("a refused release is explained and refreshes the run", async ({ page }) => {
  const stuck = node("train-b", { label: "학습 B", status: "RUNNING", job_id: J_B, attempt_id: A_B, attempts: 1, counted_attempts: 1,
    job_status: "FAILED", uncertain: true, needs_operator: true, wait_reason: OPERATOR_CONFIRMATION });
  await openWorkbench(page, { workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: detail(run({}, [stuck])),
    graph_release_lock: [conflict([issue("attempt_stale", ATTEMPT_STALE, "/attempt_id", { node_id: "train-b" })]),
      conflict([issue("release_not_applicable", RELEASE_NOT_APPLICABLE, "/node_id", { node_id: "train-b" })])] } });
  await openRun(page, RUN);
  for (const text of [ATTEMPT_STALE, RELEASE_NOT_APPLICABLE]) {
    const before = await gets(page);
    await operatorItem(page, "train-b").getByRole("button", { name: "실행 잠금 해제" }).click();
    const dialog = page.getByRole("dialog");
    await dialog.getByRole("checkbox", { name: STOP_CONFIRMED }).check();
    await dialog.getByRole("textbox").fill("학습 B");
    // By keyboard: a second dialog's button sits where the last one was clicked, and the shell swallows a repeated
    // click at the same spot within 400 ms (faster than any person, not than this test).
    await dialog.getByRole("button", { name: "잠금 해제", exact: true }).press("Enter");
    await idle(page);
    await expect(page.getByRole("alert")).toContainText(text);
    expect(await gets(page)).toBe(before + 1);
  }
  expect(await calls(page, { action: "graph_release_lock" })).toHaveLength(2);
});

test("wait reasons cover the controller's texts and every status has a Korean name", async () => {
  const reasons = await import("../src/wait-reasons.ts");
  const cases: [string, string, string][] = [
    ["waiting for destination existing_runpod:pod-a", "waiting", DESTINATION_WAIT],
    ["waiting: the run is at its max_concurrent limit", "waiting", "최대 동시 실행 수에 도달해 기다리는 중입니다"],
    ["waiting: every dispatch thread of this controller is busy", "waiting", "기다리는 중입니다"],
    ["waiting for the retry backoff (attempt 2 of 3)", "waiting", "2/3"],
    ["waiting for a cross-provider hardware lock (D3 quarantine): another provider holds hardware", "waiting", "다른 연결 대상(RunPod 등)의 잠금이 풀리기를 기다립니다"],
    [AWAITING_RESULT, "waiting", "같은 실행의 결과를 기다립니다"],
    ["no result after the launch's time limit; if its remote execution stopped, " + OPERATOR_CONFIRMATION, "operator", "시간 제한"],
    [OPERATOR_CONFIRMATION, "operator", "운영자 확인"],
    [OUTPUTS_REFUSED, "operator", "결과를 기록하지 못했습니다"],
    ["ledger integrity check failed; no node was failed, and the run waits until an operator restores or removes the record", "operator", "무결성"],
    ["live destination facts differ from the approved plan (GPU or a higher price); re-plan before running", "replan", "다시 계획하세요"],
    ["executor profile changed after approval; re-plan before running", "replan", "실행 프로필이 바뀌었습니다"],
    ["executor transport settings changed after approval; re-plan before running", "replan", "연결 설정이 바뀌었습니다"],
    ["the opened destination is not this job's pinned profile; re-plan before running", "replan", "다시 계획하세요"],
    [BUDGET, "budget", "승인 예산 소진: 예약 $12 / 승인 $10"],
    [UNPRICED, "budget", "가격을 알 수 없는 이전 시도"],
    ["destination unavailable: ConnectionError: refused", "error", "실행 자원에 연결할 수 없습니다"],
    ["dispatch failed: RuntimeError: boom", "error", "보내지 못했습니다"],
    ["settling the attempt failed; retrying next tick: OSError", "error", "정리하지 못했습니다"],
    ["reconcile could not reach the destination: timeout", "error", "확인하지 못했습니다"],
    // graph_store.readiness and graph_controller._prepare: the most common reasons.
    ["waiting for train-1", "waiting", "앞 단계 ‘train-1’이(가) 끝나기를 기다리는 중입니다"],
    ["waiting for tracking of train-1", "waiting", "앞 단계 ‘train-1’의 MLflow 기록이 저장되기를 기다리는 중입니다"],
    ["waiting for tracking of train-1 (tracking uncertain: re-arm the tracking event once MLflow is reachable)", "waiting", "기록 다시 시도"],
    [`waiting for tracking of train-1 (tracking failed: ${EVIDENCE_CHANGED_ACTION})`, "waiting", "다시 시도해도 고칠 수 없습니다"],
    ["returned unlaunched after takeover: the graph run is not running", "waiting", "되돌렸습니다"],
    // graph_controller._provider_reason: the providers' fixed refusals (executor_profiles.py, desktop_runtime.py).
    ["observed Pod differs from the approved Pod", "replan", "확인한 Pod가 승인한 Pod와 다릅니다"],
    ["observed SSH server differs from the approved server", "replan", "확인한 SSH 서버가 승인한 서버와 다릅니다"],
    ["executor/resource destination mismatch", "replan", "다시 계획하세요"],
    ["executor profile is not configured in this controller", "error", "컨트롤러에 없습니다"],
    ["no executor provider is configured for real graph runs", "error", "실제 실행에 쓸 실행 프로필"],
    ["positive finite live RunPod API price required", "error", "가격"],
    ["live RunPod Pod lacks its GPU type ID and count", "error", "Pod 정보"],
    ["live RunPod Pod metadata required", "error", "Pod 정보"],
    ["Pod must already be running; start it separately", "error", "Pod를 먼저 시작하세요"],
    ["Pod public TCP SSH mapping required", "error", "SSH 포트"],
    ["persistent network volume at /workspace required", "error", "네트워크 볼륨"],
    ["independently verified SSH SHA256 fingerprint required", "error", "SSH 지문"],
    ["model operation requires the primary runtime with pinned PEFT 0.18.1", "error", "PEFT 0.18.1"],
    ["GPU runtime source, lock, engine or CUDA preflight mismatch; prepare runtime first", "error", "런타임"],
    ["simulated destinations serve simulation plans only", "error", "모의 실행"],
    ["destination unavailable: TimeoutError: the destination did not open within the controller's time limit; it is retried later",
      "error", "시간 제한 안에 열리지 않았습니다"],
    // Composite reasons: the 8.1 backfill keeps its notice in front; the budget names it at the end.
    [`${UNPRICED}; waiting for destination existing_runpod:pod-a`, "budget", DESTINATION_WAIT],
    [`${BUDGET}; ${UNPRICED}`, "budget", "가격을 알 수 없는 이전 시도"],
    [`${LEDGER}: ValueError: binding differs`, "operator", "무결성"],
    // A local selection's own failure (graph_controller._local_selections: `_describe(exc)`).
    ["ValueError: selection receipt differs", "error", "처리하지 못했습니다"],
  ];
  for (const [original, kind, text] of cases) {
    const localized = reasons.localizeWaitReason(original)!;
    expect(localized, original).toMatchObject({ kind, original });
    expect(localized.text, original).toContain(text);
    expect(localized.text, original).not.toBe(original);
  }
  expect(reasons.localizeWaitReason("waiting for train-1", id => ({ "train-1": "기본 학습" } as Record<string, string>)[id]).text)
    .toBe("앞 단계 ‘기본 학습’이(가) 끝나기를 기다리는 중입니다");
  expect(reasons.localizeWaitReason(`${UNPRICED}; waiting for destination existing_runpod:pod-a`)!.text).toContain("가격을 알 수 없는 이전 시도");
  // The unpriced notice with nothing after its separator (10.7 re-review) shows the notice alone instead of throwing.
  expect(reasons.localizeWaitReason(`${UNPRICED}; `)).toEqual({ text: "가격을 알 수 없는 이전 시도가 승인 예산 전체를 예약했습니다",
    original: `${UNPRICED}; `, kind: "budget" });
  // Korean texts (desktop_runtime.MISSING_KEY) are shown as received.
  const missingKey = "SSH 개인키 파일을 찾을 수 없습니다. 연결 화면에서 키 파일을 다시 선택하세요.";
  expect(reasons.localizeWaitReason(missingKey)).toEqual({ text: missingKey, original: missingKey, kind: "error" });
  for (const [text, korean] of [[TRACKER_MISSING, "MLflow 기록 설정"], ["MLflow outcome is uncertain; searching before any new create: OSError", "불확실"],
    ["local evidence changed after enqueue: node-outputs.json", "로컬 증거 파일이 바뀌었습니다"]]) {
    const localized = reasons.localizeRunText(text)!;
    expect(localized.text, text).toContain(korean);
    expect(localized.original, text).toBe(text);
  }
  expect(reasons.localizeWaitReason(null)).toBeNull();
  expect(reasons.localizeWaitReason("")).toBeNull();
  expect(reasons.localizeWaitReason("something new")).toEqual({ text: "something new", original: "something new", kind: "error" });
  expect(Object.keys(reasons.NODE_STATUS).sort()).toEqual(["BLOCKED", "CANCELED", "COMPLETED", "FAILED", "PENDING", "READY", "RUNNING"]);
  expect(Object.keys(reasons.RUN_STATUS).sort()).toEqual(["CANCELED", "COMPLETED", "FAILED", "PAUSED", "RUNNING"]);
  for (const key of ["not_started", "pending", "retrying", "saved", "uncertain", "failed", "registered", "none"])
    expect(reasons.TRACKING_STATUS[key], key).toMatch(/[가-힣]/);
});

test("a finished run from before the tracking outbox is not polled", async ({ page }) => {
  const finished = (pending: number) => run({ status: "COMPLETED", tracking: { events: { PENDING: pending, ACKNOWLEDGED: 0, FAILED: 0, UNCERTAIN: 0 },
    graph: "not_started", action: null } }, [node("train-1", { status: "COMPLETED", output: model("d") })]);
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(finished(0))), controller_status: controllerStatus("running"),
    graph_run_get: [detail(finished(0)), detail(finished(1))] } });
  await pauseClock(page);
  await openRun(page, RUN);
  // Final, no graph tracking and no tracking event at all: nothing will change, so nothing is polled.
  await expect(detailCard(page).locator(".run-meta")).toContainText("기록 대기열이 생기기 전에 끝난 실행");
  await page.clock.runFor(9000);
  expect(await gets(page)).toBe(1);
  // A tracking event still pending keeps the run followed.
  await openRun(page, RUN);
  expect(await gets(page)).toBe(2);
  await expect(detailCard(page).locator(".run-meta")).not.toContainText("기록 대기열이 생기기 전에 끝난 실행");
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(3);
});

test("the release dialog stops when the node changes while it is open", async ({ page }) => {
  // A blank label is shown, and typed, as the node ID.
  const stuck = (attempt: string) => node("train-b", { label: "   ", status: "RUNNING", job_id: J_B, attempt_id: attempt, attempts: 1,
    counted_attempts: 1, job_status: "FAILED", uncertain: true, needs_operator: true, wait_reason: OPERATOR_CONFIRMATION });
  await openWorkbench(page, { clock: true, workflow: { graph_runs_list: listOf(entry(run())), controller_status: controllerStatus("running"),
    graph_run_get: [detail(run({}, [stuck(A_B)])), detail(run({}, [stuck(A_C)]))] } });
  await pauseClock(page);
  await openRun(page, RUN);
  await expect(nodeRow(page, "train-b").locator(".run-node-label b")).toHaveText("train-b");
  await expect(operatorItem(page, "train-b").locator(".run-operator-head b")).toHaveText("train-b");
  await operatorItem(page, "train-b").getByRole("button", { name: "실행 잠금 해제" }).click();
  const dialog = page.getByRole("dialog");
  await expect(dialog.locator(".run-release-target b")).toHaveText("train-b");
  const confirm = dialog.getByRole("button", { name: "잠금 해제", exact: true });
  const stopped = dialog.getByRole("checkbox", { name: STOP_CONFIRMED });
  await stopped.check();
  await dialog.getByRole("textbox").fill("train-b");
  await expect(confirm).toBeEnabled();
  // A poll shows another attempt: the shown attempt is no longer the one to release.
  await page.clock.runFor(3000);
  await expect.poll(() => gets(page)).toBe(2);
  await expect(dialog).toContainText("실행 상태가 바뀌었습니다");
  await expect(confirm).toBeDisabled();
  await stopped.uncheck();
  await stopped.check();
  await expect(confirm).toBeDisabled();
  await page.keyboard.press("Escape");
  expect(await calls(page, { action: "graph_release_lock" })).toHaveLength(0);
  // A new dialog names the current attempt.
  await operatorItem(page, "train-b").getByRole("button", { name: "실행 잠금 해제" }).click();
  await expect(page.getByRole("dialog")).toContainText(A_C);
  await expect(page.getByRole("dialog")).not.toContainText("실행 상태가 바뀌었습니다");
});
