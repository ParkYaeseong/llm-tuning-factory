// Pure modules of the workflow workbench, tested in Node (no page): bridge parsing, issues, drafts, polling, navigation.
import { test, expect } from "@playwright/test";
import { readFileSync } from "node:fs";
import { NATIVE_PENDING, WORKFLOW_ACTIONS, type PlanSummary, type ProjectRevision, type WorkflowIssue } from "../src/workflow-types.ts";
import { BridgeError, MALFORMED, parseWorkflowResult } from "../src/api.ts";
import { issueList, issuesAt, localize, nodeIssues, planSection, rowIssues } from "../src/issues.ts";
import { bindingIdFor, createProjectStore, type ProjectStoreBridge } from "../src/project-store.ts";
import { createPoller, createQueue } from "../src/poller.ts";
import { NAV_GROUPS, navModel } from "../src/nav-model.ts";
import type { View } from "../src/view-types.ts";

const HOSTILE = "<img src=x onerror=alert(1)>";
const hash = (character: string) => character.repeat(64);
const issue = (code: string, message: string, path = "", extra: Partial<WorkflowIssue> = {}): WorkflowIssue => ({ code, path, message, ...extra });

test("the typed bridge lists exactly the native workflow actions", () => {
  const source = readFileSync(new URL("../src-tauri/src/main.rs", import.meta.url), "utf8");
  const declaration = /const WORKFLOW_ACTIONS: \[&str; (\d+)\] = \[([\s\S]*?)\];/.exec(source);
  expect(declaration).not.toBeNull();
  const rust = [...declaration![2].matchAll(/"([a-z_]+)"/g)].map(match => match[1]);
  expect(rust).toHaveLength(Number(declaration![1]));
  expect(new Set(WORKFLOW_ACTIONS).size).toBe(WORKFLOW_ACTIONS.length);
  expect(new Set(WORKFLOW_ACTIONS)).toEqual(new Set([...rust, ...NATIVE_PENDING]));
  // A Python-only action listed in NATIVE_PENDING fails here once main.rs allows it, until it is removed there.
  for (const pending of NATIVE_PENDING) expect(rust).not.toContain(pending);
});

test("workflow results are validated before use", () => {
  const malformed: unknown[] = [{}, null, "ok", [], { status: "done", issues: [], data: null }, { status: "ok", data: null },
    { status: "invalid", issues: "x", data: null }, { status: "ok", issues: [] },
    { status: "invalid", issues: [{ code: 1, path: "", message: "x" }], data: null }];
  for (const value of malformed) {
    let caught: unknown = null;
    try { parseWorkflowResult(value); } catch (error) { caught = error; }
    expect(caught, JSON.stringify(value)).toBeInstanceOf(BridgeError);
    expect((caught as Error).message).toBe(MALFORMED);
  }
  for (const value of [{ status: "ok", issues: [], data: { projects: [] } },
    { status: "invalid", issues: [issue("not_available", "이 기능은 아직 설치되지 않았습니다.")], data: null },
    { status: "conflict", issues: [issue("profile_in_use", "사용 중", "/profile_id")], data: { jobs: [] } }])
    expect(parseWorkflowResult(value)).toBe(value);
});

test("issues map to fields, cards, rows and plan sections", () => {
  const name = issue("invalid_value", "x", "/spec/name"), child = issue("invalid_value", "x", "/spec/name/0");
  const names = issue("invalid_value", "x", "/spec/names");
  expect(issuesAt([name, child, names], "/spec/name")).toEqual([name, child]);
  const byId = issue("train_contract", "x", "", { node_id: "train-2" });
  const byPath = issue("invalid_value", "x", "/graph/nodes/1/recipe_revision");
  const other = issue("invalid_value", "x", "/graph/nodes/0/label");
  expect(nodeIssues([byId, byPath, other], "train-2", ["train-1", "train-2"])).toEqual([byId, byPath]);
  expect(nodeIssues([byId, byPath, other], "train-1", ["train-1", "train-2"])).toEqual([other]);
  const row = issue("invalid_row", "'question'", "/mapping", { row_number: 3 }), header = issue("duplicate_header", "x", "/mapping");
  expect(rowIssues([row, header])).toEqual([{ row: 3, issue: row }, { row: null, issue: header }]);
  expect(planSection(issue("observation_stale", "x", "/profiles/a~1b"))).toEqual({ section: "profile", key: "a/b" });
  expect(planSection(issue("observation_stale", "x", "/profiles/a~0b~1c"))).toEqual({ section: "profile", key: "a~b/c" });
  expect(planSection(issue("estimate_real", "x", "/estimates/0"))).toEqual({ section: "estimate", key: "0" });
  expect(planSection(issue("tracking_insecure", "x", "/tracking"))).toEqual({ section: "tracking" });
  expect(planSection(issue("node_resource_missing", "x", "/node_resources", { node_id: "eval-1" })))
    .toEqual({ section: "node_resources", key: "eval-1" });
  expect(planSection(issue("approval_required", "x", "/approve_execution"))).toEqual({ section: "approval" });
  expect(planSection(issue("approval_required", "x", "/approve_data_transfer"))).toEqual({ section: "approval" });
  expect(planSection(issue("plan_stale", "x", "/project"))).toEqual({ section: "project" });
  expect(planSection(issue("dataset", "x", "/datasets"))).toEqual({ section: "datasets" });
  expect(planSection(issue("train_contract", "x", "/graph/nodes/0/dataset_binding", { node_id: "train-1" })))
    .toEqual({ section: "graph", key: "train-1" });
  expect(planSection(issue("plan_refused", "x"))).toEqual({ section: "other" });
});

test("known codes and English domain texts are shown in Korean with the original kept", () => {
  const conflict = localize(issue("revision_conflict", "definition revision conflict", "/expected_revision"));
  expect(conflict.text).toContain("최신 버전");
  expect(conflict.original).toBe("definition revision conflict");
  expect(localize(issue("typed_port", "input requires model output", "", { node_id: "eval-1" })))
    .toEqual({ text: "이 입력에는 모델 출력이 필요합니다.", original: "input requires model output" });
  const transition = localize(issue("parent_transition", "parent transition requires explicit materialize"));
  expect(transition.text).toContain("materialize");
  expect(transition.text).toMatch(/[가-힣]/);
  expect(transition.original).toBe("parent transition requires explicit materialize");
  const codec = "'utf-8' codec can't decode byte 0xb1 in position 0: invalid start byte";
  expect(localize(issue("invalid_source", codec, "/mapping")))
    .toEqual({ text: "문자 인코딩이 맞지 않습니다. Windows에서 만든 CSV라면 CP949로 바꿔 다시 미리보세요.", original: codec });
  // Korean (Task 9) texts and unknown codes are shown as received.
  expect(localize(issue("not_available", "이 기능은 아직 설치되지 않았습니다."))).toEqual({ text: "이 기능은 아직 설치되지 않았습니다.", original: null });
  expect(localize(issue("brand_new_code", "Something new happened"))).toEqual({ text: "Something new happened", original: null });

  const html = issueList([issue("brand_new_code", HOSTILE), issue("typed_port", "input requires model output")], { heading: `제목 ${HOSTILE}` });
  expect(html).not.toContain("<img");
  expect(html).toContain("&lt;img src=x onerror=alert(1)&gt;");
  expect(html).toContain('<ul class="issue-list"');
  expect(html).toContain("원문 보기");
  expect(html.match(/원문 보기/g)).toHaveLength(1);
  const many = issueList(Array.from({ length: 53 }, (_, index) => issue("brand_new_code", `문제 ${index}`)));
  expect(many.match(/<li/g)).toHaveLength(51);
  expect(many).toContain("외 3건");
  expect(many).not.toContain("문제 50");
  expect(issueList([])).toBe("");
});

function fakeBridge(initial: ProjectRevision) {
  const calls: { action: string; args: unknown[] }[] = [];
  let latest = initial;
  let next = 1;
  const bridge: ProjectStoreBridge = {
    async get(projectId, revision) {
      calls.push({ action: "get", args: [projectId, revision] });
      return { status: "ok", issues: [], data: structuredClone(latest) };
    },
    async save(spec, expected) {
      calls.push({ action: "save", args: [structuredClone(spec), expected] });
      if (expected !== latest.revision_sha256)
        return { status: "conflict", issues: [issue("revision_conflict", "definition revision conflict", "/expected_revision")], data: null };
      latest = { project: structuredClone(spec), revision_sha256: hash(String(++next)) };
      return { status: "ok", issues: [], data: structuredClone(latest) };
    },
    async validate(projectId, revision) {
      calls.push({ action: "validate", args: [projectId, revision] });
      return { status: "ok", issues: [], data: { project_id: projectId, project_revision: revision, node_count: 0 } };
    },
  };
  return { bridge, calls, elsewhere(change: (spec: ProjectRevision["project"]) => void) {
    const spec = structuredClone(latest.project); change(spec);
    latest = { project: spec, revision_sha256: hash("e") };
  } };
}

const summary = (revision: string): PlanSummary => ({
  plan_sha256: hash("p"), simulation: true, project_id: "p1", project_revision: revision, max_estimated_cost_usd: 1,
  max_duration_seconds: 3600, max_concurrent_gpu_count: 1, retry_policy: { max_attempts: 1, backoff_seconds: 0 },
  nodes: [], datasets: [], tracking: null });

test("a project draft tracks revision, dirty state and plan validity", async () => {
  const first: ProjectRevision = { revision_sha256: hash("1"), project: { schema_version: 1, project_id: "p1", name: "첫 프로젝트",
    purpose: "", task_type: "", datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: null } };
  const fake = fakeBridge(first);
  const store = createProjectStore(fake.bridge);
  const draft = store.adopt(first);
  expect(draft.dirty).toBe(false);
  expect(draft.planning.simulation).toBe(true);
  expect(store.draft("p1")).toBe(draft);

  const held = store.holdPlan("p1", summary(first.revision_sha256), [hash("r")]);
  expect(store.planUsable("p1")).toBe(true);
  expect(store.holdPlan("p1", summary(first.revision_sha256), [hash("r")]).requestId).toBe(held.requestId);   // retry-safe
  store.edit("p1", spec => { spec.purpose = "서열 설명"; });
  expect(store.draft("p1")!.dirty).toBe(true);
  expect(store.draft("p1")!.plan).toBeNull();
  expect(store.draft("p1")!.planDropped).toMatch(/[가-힣]/);
  expect(store.planUsable("p1")).toBe(false);
  // Reverting the edit by hand makes the draft clean again.
  store.edit("p1", spec => { spec.purpose = ""; });
  expect(store.draft("p1")!.dirty).toBe(false);
  store.edit("p1", spec => { spec.purpose = "서열 설명"; });

  const saved = await store.save("p1");
  expect(saved.status).toBe("ok");
  expect(fake.calls.at(-1)).toEqual({ action: "save", args: [{ ...first.project, purpose: "서열 설명" }, first.revision_sha256] });
  expect(store.draft("p1")!.dirty).toBe(false);
  expect(store.draft("p1")!.saved!.revision_sha256).toBe(hash("2"));

  await store.validate("p1");
  expect(fake.calls.at(-1)).toEqual({ action: "validate", args: ["p1", hash("2")] });

  // Another writer saves first: the conflict keeps the draft.
  fake.elsewhere(spec => { spec.name = "다른 이름"; });
  store.edit("p1", spec => { spec.task_type = "요약"; });
  const stale = await store.save("p1");
  expect(stale.status).toBe("conflict");
  expect(store.draft("p1")!.conflict).toEqual(stale.issues);
  expect(store.draft("p1")!.dirty).toBe(true);
  expect(store.draft("p1")!.spec.task_type).toBe("요약");
  expect(store.draft("p1")!.spec.name).toBe("첫 프로젝트");

  // Overwrite: fetch the latest revision, then save the draft against it.
  const overwritten = await store.overwriteWithDraft("p1");
  expect(overwritten.status).toBe("ok");
  expect(fake.calls.slice(-2).map(call => call.action)).toEqual(["get", "save"]);
  expect(fake.calls.at(-1)!.args[1]).toBe(hash("e"));
  expect(store.draft("p1")!.conflict).toBeNull();
  expect(store.draft("p1")!.dirty).toBe(false);
  expect(store.draft("p1")!.spec.name).toBe("첫 프로젝트");

  // Reload: the latest revision replaces the draft.
  fake.elsewhere(spec => { spec.name = "최신"; });
  store.edit("p1", spec => { spec.purpose = "버릴 변경"; });
  expect(await store.open("p1")).toBeNull();                       // a dirty draft is kept
  await store.reloadLatest("p1");
  expect(store.draft("p1")!.spec.name).toBe("최신");
  expect(store.draft("p1")!.spec.purpose).toBe("서열 설명");
  expect(store.draft("p1")!.dirty).toBe(false);

  // Each changed basis makes a held plan unusable; an unpinned recipe change keeps it.
  const revision = store.draft("p1")!.saved!.revision_sha256;
  for (const change of [() => store.markProfilesChanged(), () => store.markSettingsChanged(),
    () => store.markRecipeChanged(hash("r")), () => store.setPlanning("p1", inputs => { inputs.simulation = false; })]) {
    store.holdPlan("p1", summary(revision), [hash("r")]);
    store.markRecipeChanged(hash("u"));
    expect(store.planUsable("p1")).toBe(true);
    change();
    expect(store.planUsable("p1")).toBe(false);
    expect(store.draft("p1")!.planDropped).toMatch(/[가-힣]/);
  }
  expect(store.draft("p1")!.planning.simulation).toBe(false);
  // A plan of another revision is never usable.
  store.holdPlan("p1", summary(hash("9")), []);
  expect(store.planUsable("p1")).toBe(false);

  expect(bindingIdFor("train", [])).toBe("train");
  const bound = (binding_id: string) => ({ binding_id, role: "train" as const, dataset: { dataset_id: "d", version: "1", sha256: hash("d") } });
  expect(bindingIdFor("train", [bound("train")])).toBe("train-2");
  expect(bindingIdFor("train", [bound("train"), bound("train-2")])).toBe("train-3");
  expect(bindingIdFor("validation", [bound("train")])).toBe("validation");
});

test("texts containing Korean are translated only by known English patterns, never by their code", () => {
  // A known English fragment inside a text that quotes a Korean file name is still translated.
  const codec = issue("invalid_source", "'utf-8' codec can't decode byte 0xb0 in position 2 of '질문.csv'");
  expect(localize(codec)).toEqual({ text: "문자 인코딩이 맞지 않습니다. Windows에서 만든 CSV라면 CP949로 바꿔 다시 미리보세요.", original: codec.message });
  // Otherwise a text with Korean is shown as received, even under a code whose usual text is English: a Korean
  // message under such a code (version_exists with hf_targets' TARGETS_CHANGED) is specific advice of its own.
  const targets = issue("version_exists", "이 고정 모델 버전에 이미 저장된 모듈 구조 정보와 읽은 헤더가 다릅니다.", "/repo_id");
  expect(localize(targets)).toEqual({ text: targets.message, original: null });
  const row = issue("invalid_row", "missing required field '질문'", "/mapping", { row_number: 3 });
  expect(localize(row)).toEqual({ text: row.message, original: null });
  const korean = issue("legacy_dataset_missing", "기존 프로필의 데이터 버전을 프로젝트에 연결하지 못했습니다.");
  expect(localize(korean)).toEqual({ text: korean.message, original: null });
  const refused = issue("refused", "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다.");
  expect(localize(refused)).toEqual({ text: refused.message, original: null });
  // An English text under a known code still gets the code's Korean text.
  const english = issue("version_exists", "immutable version already exists", "/version");
  expect(localize(english)).toEqual({ text: "이미 등록된 버전입니다. 다른 버전을 입력하세요.", original: english.message });
});

test("a held plan keeps the generations seen before the plan request", async () => {
  const first: ProjectRevision = { revision_sha256: hash("1"), project: { schema_version: 1, project_id: "p1", name: "계획",
    purpose: "", task_type: "", datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: null } };
  const store = createProjectStore(fakeBridge(first).bridge);
  store.adopt(first);
  // A profile and the connection settings are saved while the plan request is running.
  const seen = store.generations();
  store.markProfilesChanged();
  store.holdPlan("p1", summary(first.revision_sha256), [], seen);
  expect(store.planUsable("p1")).toBe(false);
  const settings = store.generations();
  store.markSettingsChanged();
  store.holdPlan("p1", summary(first.revision_sha256), [], settings);
  expect(store.planUsable("p1")).toBe(false);
  // Planned with the current basis, the plan is usable (and without a snapshot the current basis applies).
  store.holdPlan("p1", summary(first.revision_sha256), [], store.generations());
  expect(store.planUsable("p1")).toBe(true);
  store.holdPlan("p1", summary(first.revision_sha256), []);
  expect(store.planUsable("p1")).toBe(true);
});

function fakeTimers() {
  const pending: { fn: () => void; at: number; handle: number }[] = [];
  const delays: number[] = [];
  let now = 0, handles = 0;
  return {
    delays, pending,
    timers: {
      set(fn: () => void, ms: number) { delays.push(ms); const handle = ++handles; pending.push({ fn, at: now + ms, handle }); return handle; },
      clear(handle: unknown) { const index = pending.findIndex(item => item.handle === handle); if (index >= 0) pending.splice(index, 1); },
      now: () => now,
    },
    elapse(ms: number) { now += ms; },
    async fire() {
      pending.sort((a, b) => a.at - b.at);
      const next = pending.shift()!;
      now = Math.max(now, next.at);
      next.fn();
      for (let i = 0; i < 10; i++) await Promise.resolve();
    },
  };
}

test("the poller never overlaps, backs off on errors and stops when inactive", async () => {
  const clock = fakeTimers();
  const outcomes: ("continue" | "stop" | "fail")[] = ["continue", "fail", "fail", "fail", "continue", "stop"];
  let ticks = 0, active = true, running = 0, overlapped = false;
  const poller = createPoller({ baseMs: 3000, timers: clock.timers, active: () => active, async tick() {
    running++; overlapped ||= running > 1; ticks++;
    const outcome = outcomes.shift()!;
    if (ticks === 1) clock.elapse(4000);                        // a slow call: 4 s
    running--;
    if (outcome === "fail") throw new Error("bridge failed");
    return outcome;
  } });
  poller.start();
  expect(poller.running).toBe(true);
  expect(clock.delays).toEqual([3000]);
  await clock.fire();
  expect(clock.delays.at(-1)).toBe(8000);                        // max(base, 2 × 4 s)
  for (const expected of [6000, 12000, 24000]) { await clock.fire(); expect(clock.delays.at(-1)).toBe(expected); }
  expect(poller.failures).toBe(3);
  active = false;
  await clock.fire();
  expect(ticks).toBe(4);                                          // inactive: no tick, re-check after base
  expect(clock.delays.at(-1)).toBe(3000);
  active = true;
  await clock.fire();
  expect(poller.failures).toBe(0);
  expect(clock.delays.at(-1)).toBe(3000);                        // success resets
  poller.poke();                                                  // an immediate tick
  expect(clock.delays.at(-1)).toBe(0);
  await clock.fire();
  expect(poller.running).toBe(false);                            // "stop"
  expect(clock.pending).toHaveLength(0);
  expect(overlapped).toBe(false);

  // A stop while a tick is in flight schedules nothing afterwards.
  let release: (value: "continue") => void = () => undefined;
  const second = fakeTimers();
  const held = createPoller({ baseMs: 3000, timers: second.timers, active: () => true,
    tick: () => new Promise<"continue">(resolve => { release = resolve; }) });
  held.start();
  await second.fire();
  held.poke();                                                    // no overlap with the call in flight
  held.stop();
  release("continue");
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(second.pending).toHaveLength(0);

  const queue = createQueue();
  const order: string[] = [];
  let finishFirst: () => void = () => undefined;
  const first = queue.run(() => new Promise<string>(resolve => { order.push("first start"); finishFirst = () => { order.push("first end"); resolve("a"); }; }));
  const next = queue.run(async () => { order.push("second start"); return "b"; });
  for (let i = 0; i < 10; i++) await Promise.resolve();
  expect(order).toEqual(["first start"]);
  finishFirst();
  expect(await first).toBe("a");
  expect(await next).toBe("b");
  expect(order).toEqual(["first start", "first end", "second start"]);
  // A failed call does not block the queue.
  await expect(queue.run(async () => { throw new Error("x"); })).rejects.toThrow("x");
  expect(await queue.run(async () => "after")).toBe("after");
});

test("the navigation model places workflow views beside the legacy pages", () => {
  const view = (id: string, group: "workspace" | "settings", label: string, order: number): View =>
    ({ id, title: label, description: "", nav: { group, label, icon: id, order }, render: () => "" });
  const views = [view("profiles", "settings", "실행 프로필", 20), view("runs", "workspace", "그래프 실행", 50),
    view("recipes", "workspace", "레시피", 40), view("projects", "workspace", "프로젝트", 20),
    { id: "project", title: "프로젝트", description: "", parent: "projects", render: () => "" }];
  expect(NAV_GROUPS.map(group => group.label)).toEqual(["워크스페이스", "단일 학습", "설정"]);
  const items = navModel(views);
  expect(NAV_GROUPS.map(group => items.filter(item => item.group === group.id).map(item => item.label))).toEqual([
    ["작업 공간", "프로젝트", "데이터", "레시피", "그래프 실행", "기록 비교"],
    ["학습 설정", "실행 관리"],
    ["연결 설정", "실행 프로필", "튜토리얼"]]);
  expect(items.map(item => item.id)).not.toContain("project");
  const legacy = navModel([]);
  expect(legacy.map(item => [item.group, item.id])).toEqual([["workspace", "overview"], ["workspace", "datasets"],
    ["workspace", "history"], ["single", "training"], ["single", "jobs"], ["settings", "connections"], ["settings", "tutorial"]]);
});
