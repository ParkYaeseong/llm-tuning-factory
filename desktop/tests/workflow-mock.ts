// Shared Tauri bridge mock for the Task 10 specs (owned by 10.1; later specs pass options, they do not edit it).
import { expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";

export const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
/** A hostile value for escaping checks; every spec puts it in at least one dynamic field. */
export const HOSTILE = "<img src=x onerror=alert(1)>";
export const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";

export const ok = <T>(data: T, warnings?: unknown[]) =>
  ({ status: "ok", issues: [], data: warnings ? { ...(data as object), warnings } : data });
export const invalid = (issues: unknown[], data: unknown = null) => ({ status: "invalid", issues, data });
export const conflict = (issues: unknown[]) => ({ status: "conflict", issues, data: null });
export const issue = (code: string, message: string, path = "", extra: object = {}) => ({ code, path, message, ...extra });

export interface BridgeOptions {
  settings?: Record<string, unknown>; catalog?: Record<string, unknown>; jobs?: unknown[];
  profiles?: unknown[];                                   // executor_profiles_get
  workflow?: Record<string, unknown | unknown[]>;         // scripted WorkflowResult per action (array = sequence, last repeats);
                                                          // { reject: "text" } makes invoke reject (a native Err string)
  native?: Record<string, unknown>;                       // other native results; { reject: "text" } rejects
  definitions?: boolean;                                  // stateful projects/recipes fake with 9.1 CAS and request-ID semantics
  holds?: string[];                                       // workflow action or native command names held until window.__release(name)
  clock?: boolean;                                        // page.clock.install() before goto
}

/**
 * Installs the mocked bridge, opens the app and waits until the boot task has finished.
 * Unscripted answers model a sidecar where no workflow module answers: workflow actions and the controller
 * status/stop commands answer `invalid`/`not_available`, and the commands that depend on them reject with the native
 * fail-closed texts (profile save/delete: EDIT_CHECK_FAILED; controller_start: STATUS_UNKNOWN). Specs script the rest.
 */
export async function openWorkbench(page: Page, options: BridgeOptions = {}): Promise<void> {
  if (options.clock) await page.clock.install();
  await page.addInitScript(installBridge, { options, config });
  await page.goto("/");
  // A positive boot signal: the boot task reached its last call (capabilities), and then its notice cleared.
  await expect.poll(() => page.evaluate(() => (window as any).__testCalls.some((call: any) =>
    call.command === "factory_action" && call.args?.action === "capabilities")), { message: "boot reached capabilities", timeout: 10_000 }).toBe(true);
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
}

/** Recorded bridge calls, optionally only one native command and/or one workflow or legacy action. */
export const calls = (page: Page, filter: { command?: string; action?: string } = {}): Promise<any[]> =>
  page.evaluate(filter => (window as any).__testCalls.filter((call: any) =>
    (!filter.command || call.command === filter.command) && (!filter.action || call.args?.action === filter.action)), filter);

// Runs in the page (serialized by addInitScript): it may not use anything defined outside its body.
function installBridge({ options, config }: { options: BridgeOptions; config: Record<string, any> }) {
  const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
  const SECRETS = ["RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN", "S3_ACCESS_KEY_ID",
    "S3_SECRET_ACCESS_KEY", "S3_SESSION_TOKEN", "HF_TOKEN"];
  const resultOk = (data: unknown) => ({ status: "ok", issues: [], data });
  const resultIssue = (status: string, code: string, message: string, path = "") =>
    ({ status, issues: [{ code, path, message }], data: null });
  const calls: any[] = [];
  (window as any).__testCalls = calls;
  let settings: Record<string, any> = { ...(options.settings ?? {}) };
  const positions: Record<string, number> = {};
  const scripted = (table: Record<string, unknown> | undefined, key: string, name: string) => {
    const entry = table?.[name];
    if (entry === undefined) return { found: false, value: undefined as unknown };
    if (!Array.isArray(entry)) return { found: true, value: entry };
    const position = positions[key + name] ?? 0;
    positions[key + name] = position + 1;
    return { found: true, value: entry[Math.min(position, entry.length - 1)] };
  };
  const answer = (value: unknown) => {
    if (value && typeof value === "object" && "reject" in (value as object)) throw (value as { reject: string }).reject;
    return structuredClone(value);
  };

  const holds = new Set(options.holds ?? []);
  const waiting: Record<string, (() => void)[]> = {};
  (window as any).__release = (name: string) => {
    holds.delete(name);
    for (const resolve of waiting[name] ?? []) resolve();
    delete waiting[name];
  };
  (window as any).__held = (name: string) => (waiting[name] ?? []).length;
  const hold = async (name: string) => {
    if (holds.has(name)) await new Promise<void>(resolve => (waiting[name] ??= []).push(resolve));
  };

  // -- stateful 9.1 definitions: content-addressed revisions, CAS saves, idempotent project creation --
  const canonical = (value: any): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
    : value && typeof value === "object"
      ? `{${Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",")}}`
      : JSON.stringify(value);
  const digest = async (value: unknown) => [...new Uint8Array(await crypto.subtle.digest("SHA-256",
    new TextEncoder().encode(canonical(value))))].map(byte => byte.toString(16).padStart(2, "0")).join("");
  const newId = () => crypto.randomUUID().replace(/-/g, "");
  type Entity = { current: string; revisions: Map<string, any> };
  const stores: Record<string, Map<string, Entity>> = { projects: new Map(), recipes: new Map() };
  const creations = new Map<string, { request: string; project_id: string; revision: string }>();
  const unknown = (path: string, revision = false) =>
    resultIssue("invalid", "unknown_definition", revision ? "unknown definition revision" : "unknown definition", path);
  const conflictAt = (path: string) => resultIssue("conflict", "revision_conflict", "definition revision conflict", path);
  async function store(kind: string, id: string, spec: any, expected: string | null) {
    const entity = stores[kind].get(id);
    if ((entity?.current ?? null) !== expected) return null;
    const revision = await digest(spec);
    const next = entity ?? { current: revision, revisions: new Map() };
    next.revisions.set(revision, structuredClone(spec));
    next.current = revision;
    stores[kind].set(id, next);
    return revision;
  }
  const read = (kind: string, id: string, revision?: string | null) => {
    const entity = stores[kind].get(id);
    const chosen = revision ?? entity?.current;
    return entity && chosen && entity.revisions.has(chosen) ? { spec: structuredClone(entity.revisions.get(chosen)), revision: chosen } : null;
  };
  const project = (found: { spec: any; revision: string }) => ({ project: found.spec, revision_sha256: found.revision });
  const recipe = (found: { spec: any; revision: string }) => ({ recipe: found.spec, revision_sha256: found.revision });
  async function definition(action: string, data: any): Promise<unknown> {
    if (action === "projects_list") return resultOk({ projects: [...stores.projects.keys()].sort().map(id => {
      const { spec, revision } = read("projects", id)!;
      return { project_id: id, name: spec.name, purpose: spec.purpose, task_type: spec.task_type, revision_sha256: revision,
        legacy_origin: spec.legacy_origin, datasets: spec.datasets,
        graph_nodes: Array.isArray(spec.graph?.nodes) ? spec.graph.nodes.length : 0 };
    }) });
    if (action === "project_create") {
      if (!data.name) return resultIssue("invalid", "invalid_value", "String should have at least 1 character", "/name");
      const request = canonical({ name: data.name, purpose: data.purpose ?? "", task_type: data.task_type ?? "" });
      const earlier = creations.get(data.request_id);
      if (earlier) return earlier.request === request ? resultOk(project(read("projects", earlier.project_id, earlier.revision)!))
        : resultIssue("conflict", "request_reused", "request ID reused with different project details", "/request_id");
      const spec = { schema_version: 1, project_id: newId(), name: data.name, purpose: data.purpose ?? "",
        task_type: data.task_type ?? "", datasets: [], default_executor_profile_id: null, graph: {}, legacy_origin: null };
      const revision = (await store("projects", spec.project_id, spec, null))!;
      creations.set(data.request_id, { request, project_id: spec.project_id, revision });
      return resultOk(project({ spec, revision }));
    }
    if (action === "project_get") {
      if (!stores.projects.has(data.project_id)) return unknown("/project_id");
      const found = read("projects", data.project_id, data.revision);
      return found ? resultOk(project(found)) : unknown("/revision", true);
    }
    if (action === "project_save") {
      if (!stores.projects.has(data.spec?.project_id)) return unknown("/spec/project_id");
      const revision = await store("projects", data.spec.project_id, data.spec, data.expected_revision);
      return revision ? resultOk(project({ spec: structuredClone(data.spec), revision })) : conflictAt("/expected_revision");
    }
    if (action === "recipes_list") return resultOk({ recipes: [...stores.recipes.keys()].sort().map(id => {
      const { spec, revision } = read("recipes", id)!;
      return { recipe_id: id, version: spec.version, name: spec.name, description: spec.description,
        training_stage: spec.training_stage, training_method: spec.training_method, input_kind: spec.input_kind,
        revision_sha256: revision };
    }) });
    if (action === "recipe_get") {
      if (!stores.recipes.has(data.recipe_id)) return unknown("/recipe_id");
      const found = read("recipes", data.recipe_id, data.revision);
      return found ? resultOk(recipe(found)) : unknown("/revision", true);
    }
    if (action === "recipe_save") {
      const revision = await store("recipes", data.spec.recipe_id, data.spec, data.expected_revision);
      return revision ? resultOk(recipe({ spec: structuredClone(data.spec), revision })) : conflictAt("/expected_revision");
    }
    if (action === "recipe_clone") {
      if (!stores.recipes.has(data.recipe_id)) return unknown("/recipe_id");
      const found = read("recipes", data.recipe_id, data.revision);
      if (!found) return unknown("/revision", true);
      const spec = { ...found.spec, recipe_id: data.new_recipe_id ?? newId(), name: data.name ?? found.spec.name,
        version: data.version ?? "1" };
      const revision = await store("recipes", spec.recipe_id, spec, null);
      return revision ? resultOk(recipe({ spec, revision })) : conflictAt("/new_recipe_id");
    }
    return undefined;
  }

  async function workflow(action: string, data: any) {
    const script = scripted(options.workflow, "workflow:", action);
    if (script.found) return answer(script.value);
    if (options.definitions) {
      const result = await definition(action, data ?? {});
      if (result !== undefined) return result;
    }
    return resultIssue("invalid", "not_available", NOT_AVAILABLE);
  }

  const catalog = {
    models: [{ id: "Qwen/Qwen2.5-0.5B-Instruct", template: "qwen", architecture: "qwen2", gpu_verified: true },
      { id: "example/ServerCatalogModel", template: "llama3", architecture: "llama", gpu_verified: false }],
    training_stages: [{ id: "pt", label: "PT · 사전 학습", dataset_kind: "text" }, { id: "sft", label: "SFT · 지도 학습", dataset_kind: "instruction" },
      { id: "rm", label: "RM · 보상 학습", dataset_kind: "preference" }, { id: "ppo", label: "PPO · 정책 최적화", dataset_kind: "prompt" },
      { id: "dpo", label: "DPO · 선호 학습", dataset_kind: "preference" }, { id: "kto", label: "KTO · 선호 라벨 학습", dataset_kind: "kto" }],
    templates: [{ name: "paper_extraction", config }], profiles: [],
    datasets: [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true, synthetic: true, kind: "instruction",
      sha256: "5".repeat(64) }],
    ...(options.catalog ?? {}),
  };
  function legacy(action: string, data: any): unknown {
    if (action === "catalog") return catalog;
    if (action === "jobs") return options.jobs ?? [];
    if (action === "capabilities") {
      const stage = data.config?.training_stage || "sft";
      const blocked = stage === "ppo" && (!data.config.stage_options?.reward_model || !data.config.stage_options?.reward_model_sha256);
      return { valid: !blocked, errors: blocked ? ["PPO에는 보상 모델과 목록 해시가 필요합니다."] : [], warnings: [],
        supported_stages: ["pt", "sft", "rm", "ppo", "dpo", "kto"], methods: ["full", "freeze", "lora", "qlora", "oft"],
        quantization_bits: [4, 8], model: { id: data.config?.base_model, template: "qwen", gpu_verified: false } };
    }
    if (action === "dataset_preview") return { rows: 6, format: "alpaca", duplicate_rows: 0, samples: [{ instruction: "<sample>", output: "answer" }], bytes: 512 };
    if (action === "plan") return { dry_run: true, manifest: { config: data.config, dataset: { dataset_id: data.config.dataset_id, rows: 2 } },
      estimated_cost_usd: .5, note: "실행 버튼 전에는 GPU 작업을 시작하지 않습니다." };
    if (action === "history") return data.experiment_id ? { runs: [], next_page_token: null } : { experiments: [] };
    return {};
  }

  (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
    calls.push({ command, args: args === undefined ? undefined : structuredClone(args) });
    if (command === "workflow_action") { await hold(args.action); return workflow(args.action, args.data); }
    await hold(command);
    const script = scripted(options.native, "native:", command);
    if (script.found) return answer(script.value);
    if (command === "controller_status" || command === "controller_stop") return workflow(command, {});
    if (command === "factory_action") return legacy(args.action, args.data ?? {});
    if (command === "settings_get") return { ...settings };
    if (command === "settings_save") {
      for (const [key, value] of Object.entries(args.values as Record<string, unknown>)) {
        if (!SECRETS.includes(key)) settings[key] = value;
        else if (value) settings[key + "_saved"] = true;
      }
      return { ...settings };
    }
    if (command === "forget_credentials") {
      settings = Object.fromEntries(Object.entries(settings).filter(([key]) => !key.endsWith("_saved")));
      return { profile_credentials_forgotten: true };
    }
    if (command === "pick_file") return "C:\\data\\dummy.json";
    if (command === "executor_profiles_get") return { profiles: structuredClone(options.profiles ?? []) };
    if (command === "executor_profile_save" || command === "executor_profile_delete")
      throw `실행 프로필 사용 여부를 확인할 수 없어 변경하지 않았습니다. (${NOT_AVAILABLE})`;
    if (command === "controller_start") throw `컨트롤러 상태를 확인할 수 없습니다. (${NOT_AVAILABLE})`;
    return null;
  } };
}
