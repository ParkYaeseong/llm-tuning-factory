// The training graph draft (10.5, D6, D7): pure operations on the project's draft `graph` JSON. Cards and the SVG
// preview both come from `layout` of the same draft; connection reasons and metric choices are what the draft alone
// proves (the backend's `graph_validate`/`graph_plan` stay authoritative). Pure: imports only types.
import type { BenchmarkRevision, DatasetBinding, HFRootRef, Method, ModelRoot, RecipeSummary, Sha256, WorkflowIssue } from "./workflow-types";

export type NodeKind = "train" | "evaluate" | "select" | "merge" | "materialize";
export interface NodeOutputRef { node_id: string; output_name: "model" | "evaluation" }
export interface ArtifactRefJson { artifact_id: Sha256; inventory_sha256: Sha256 }
export interface ModelLoadRefJson { base: HFRootRef | ArtifactRefJson; adapter: ArtifactRefJson | null }
export interface ModelInputJson {
  policy: ModelLoadRefJson; initialization: "new_adapter" | "continue_adapter" | "full_weights";
  frozen_reference?: ModelLoadRefJson | null; reward_model?: ModelLoadRefJson | null;
}
interface NodeBase { id: string; label: string }
export interface TrainNodeJson extends NodeBase { kind: "train"; recipe_revision?: Sha256; overrides?: Record<string, unknown>;
  model_input?: ModelInputJson | NodeOutputRef; dataset_binding?: string; reward_binding?: NodeOutputRef | ModelLoadRefJson | null; executor_profile_ids?: string[] }
export interface EvaluateNodeJson extends NodeBase { kind: "evaluate"; model?: NodeOutputRef; benchmark_revision?: Sha256; executor_profile_ids?: string[] }
export interface SelectNodeJson extends NodeBase { kind: "select"; evaluations?: NodeOutputRef[]; selection?: SelectionJson }
export interface MergeNodeJson extends NodeBase { kind: "merge"; models?: NodeOutputRef[]; weights?: number[]; executor_profile_ids?: string[] }
export interface MaterializeNodeJson extends NodeBase { kind: "materialize"; model?: NodeOutputRef; executor_profile_ids?: string[] }
export type NodeJson = TrainNodeJson | EvaluateNodeJson | SelectNodeJson | MergeNodeJson | MaterializeNodeJson;
export interface SelectionJson { metric: string; direction: "max" | "min"; thresholds: { metric: string; direction: "max" | "min"; value: number }[];
  allow_partial: boolean; min_success: number; merge_regression_tolerance: number }
export interface GraphJson { nodes: NodeJson[]; depends_on?: { node_id: string; depends_on: string }[]; max_concurrent?: number;
  max_total_cost_usd?: number; retry_policy?: { max_attempts: number; backoff_seconds: number } }

export const MAX_NODES = 64, MAX_EDGES = 256;          // graph_schema: edges = dependencies ∪ input references
export const EVALUATOR_VERSION = "1";
// Pinned by tests/test_desktop_ui_contract.py (= evaluation.metric_schema): the object literal below, up to " as const;",
// must stay valid JSON (quoted keys, no trailing comma, no comments inside).
export const EVALUATOR_METRICS = {
  "text": ["exact_match", "character_f1"], "classification": ["accuracy", "macro_f1"], "extraction": ["accuracy", "f1"],
  "json": ["json_validity"], "kbf_extraction": ["mean_name_f1", "mean_id_f1", "json_validity"], "pipeline": []
} as const;
export type EvaluatorKind = keyof typeof EVALUATOR_METRICS;

export interface DraftContext {
  recipes: Map<Sha256, RecipeSummary>; bindings: DatasetBinding[]; roots: ModelRoot[];
  benchmarks: Map<Sha256, BenchmarkRevision>; defaultProfiles: string[];
  /** IDs a new card must not take although the draft lacks them: the saved revision's cards (their check results and
   * planning inputs are keyed by ID). */
  reservedIds?: readonly string[];
}
export type InputPort = "model_input" | "model" | "evaluations" | "models" | "reward_binding";
export interface InputOption { value: NodeOutputRef; label: string; allowed: boolean; reason?: string }
export interface Layout { columns: string[][]; edges: { from: string; to: string; kind: "data" | "order" }[] }
export type StarterShape = "single" | "serial" | "parallel" | "parallel_merge";
export interface MetricChoice { metric: string; label: string; note?: string; allowed: boolean; reason?: string }

/** Fixed Korean texts: local reasons (D7) and notes. */
export const REASONS = {
  limit: "단계는 최대 64개입니다",
  cycle: "연결하면 순환이 생깁니다",
  cycleExists: "그래프에 순환 연결이 있습니다",
  missingNode: "연결된 단계를 찾을 수 없습니다",
  duplicateId: "단계 ID가 중복되었습니다",
  unknownKind: "이 단계 종류는 화면에서 추가하거나 고칠 수 없습니다",
  evaluationAsModel: "평가 결과는 모델 입력으로 쓸 수 없습니다",
  notEvaluation: "자동 선택에는 평가 결과만 연결할 수 있습니다",
  oftParent: "OFT 결과는 다음 단계로 넘길 수 없습니다",
  oftRecipe: "그래프에서는 OFT 학습을 쓸 수 없습니다",
  materialize: "학습 방법이 바뀌는 연결은 먼저 ‘전체 모델 확정’ 단계가 필요합니다",
  ppoRoute: "PPO 학습과 다른 학습 단계는 결과를 서로 넘길 수 없습니다",
  ppoContinuation: "PPO는 이전 단계의 어댑터를 이어서 학습할 수 없습니다",
  ppoReward: "PPO 학습에는 보상 모델 연결이 필요합니다",
  rewardNotPpo: "보상 모델은 PPO 학습에서만 연결할 수 있습니다",
  mergeLimit: "병합은 최대 4개 모델까지입니다",
  mergeSource: "병합에는 LoRA·QLoRA로 학습한 단계만 쓸 수 있습니다",
  mergeCount: "병합에는 서로 다른 모델 2–4개가 필요합니다",
  mergeWeights: "병합 가중치는 모두 0보다 크고 합이 정확히 100%여야 합니다",
  materializeSource: "전체 모델 확정에는 LoRA·QLoRA 학습 결과만 쓸 수 있습니다",
  noMetrics: "평가 지표가 없어 자동 선택에 쓸 수 없습니다",
  mixedEvaluators: "서로 다른 평가 기준은 비교할 수 없습니다",
  noEvaluations: "평가 결과를 먼저 연결하세요",
  unknownMetric: "이 평가 기준에 없는 지표입니다",
  minSuccess: "최소 성공 수는 2 이상의 정수여야 합니다",
  tolerance: "병합 회귀 허용치는 0 이상이어야 합니다",
  trainRole: "학습 단계에는 학습용(train) 데이터만 연결할 수 있습니다",
  bindingMissing: "연결한 데이터를 프로젝트에서 찾을 수 없습니다",
  label: "단계 이름을 입력하세요",
  recipe: "레시피를 고르세요",
  modelInput: "입력 모델을 고르세요",
  model: "입력 모델을 고르세요",
  dataset: "학습 데이터를 고르세요",
  benchmark: "평가 기준을 고르세요",
  evaluations: "비교할 평가를 고르세요",
  metric: "선택 지표를 고르세요",
  profiles: "실행 프로필을 하나 이상 고르세요",
  settingsConcurrent: "최대 동시 실행은 1–64 사이의 정수여야 합니다",
  settingsCost: "최대 총비용은 0보다 커야 합니다",
  settingsAttempts: "재시도 횟수는 1–5 사이의 정수여야 합니다",
  settingsBackoff: "재시도 간격은 0–3600초 사이의 정수여야 합니다",
  edgeLimit: "그래프 연결은 최대 256개입니다",
} as const;
export const NOTES = {
  text: "텍스트 정답 일치 지표 · 의미적 품질 점수가 아닙니다",
  format: "형식 지표 · 품질 지표가 아닙니다",
  select: "자동 선택은 추천 결과 확정이며 배포하지 않습니다. 텍스트 정답 일치 지표는 의미적 품질을 보장하지 않습니다.",
  mergeRealOnly: "병합은 실제로 완료된 LoRA·QLoRA 결과만 합칩니다(가중치 합 100%, 합친 rank 256 이하). 모의 실행 계획에서는 모의 결과를 병합할 수 없어 이 단계에서 멈춥니다.",
  waiting: "같은 실행 자원을 써서 순서대로 실행됩니다 (대기)",
} as const;
export const KIND_LABELS: Record<NodeKind, string> = { train: "학습", evaluate: "평가", select: "자동 선택", merge: "병합", materialize: "전체 모델 확정" };
export const KINDS: readonly NodeKind[] = ["train", "evaluate", "select", "merge", "materialize"];
const ID_PREFIX: Record<NodeKind, string> = { train: "train", evaluate: "eval", select: "select", merge: "merge", materialize: "model" };
export const METRIC_LABELS: Record<string, string> = { exact_match: "정확 일치 (exact_match)", character_f1: "문자 F1 (character_f1)",
  accuracy: "정확도 (accuracy)", macro_f1: "매크로 F1 (macro_f1)", f1: "F1 (f1)", json_validity: "JSON 형식 유효성 (json_validity)",
  mean_name_f1: "이름 평균 F1 (mean_name_f1)", mean_id_f1: "ID 평균 F1 (mean_id_f1)" };
const METRIC_NOTES: Record<string, string> = { exact_match: NOTES.text, character_f1: NOTES.text, json_validity: NOTES.format };

type Obj = Record<string, any>;
const isObject = (value: unknown): value is Obj => typeof value === "object" && value !== null && !Array.isArray(value);
const clone = <T>(value: T): T => structuredClone(value);
export const isRef = (value: unknown): value is NodeOutputRef => isObject(value) && typeof value.node_id === "string"
  && (value.output_name === "model" || value.output_name === "evaluation") && Object.keys(value).length === 2;
export const outputRef = (node_id: string, output_name: "model" | "evaluation" = "model"): NodeOutputRef => ({ node_id, output_name });
export const findNode = (graph: GraphJson, id: string): NodeJson | undefined => graph.nodes.find(node => node.id === id);
export const labelOf = (node: NodeJson | undefined) => node ? (typeof node.label === "string" && node.label.trim() ? node.label : "(이름 없음)") : "(없는 단계)";
const kindOf = (node: NodeJson | undefined): NodeKind | null => node && KINDS.includes(node.kind) ? node.kind : null;

/** The node's references to other cards, with the port and the output each port requires. */
export function refsOf(node: NodeJson): { port: InputPort; ref: NodeOutputRef; required: "model" | "evaluation" }[] {
  const n = node as Obj;
  const found: { port: InputPort; ref: NodeOutputRef; required: "model" | "evaluation" }[] = [];
  const single = (port: InputPort) => { if (isRef(n[port])) found.push({ port, ref: n[port], required: "model" }); };
  const many = (port: InputPort, required: "model" | "evaluation") => {
    if (Array.isArray(n[port])) for (const item of n[port]) if (isRef(item)) found.push({ port, ref: item, required });
  };
  if (node.kind === "train") { single("model_input"); single("reward_binding"); }
  else if (node.kind === "evaluate" || node.kind === "materialize") single("model");
  else if (node.kind === "select") many("evaluations", "evaluation");
  else if (node.kind === "merge") many("models", "model");
  return found;
}

export function emptyGraph(): GraphJson {
  return { nodes: [], max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } };
}

/** The project's draft graph: `{}` (or anything but an object) is an empty graph; every other key is kept as it is. */
export function readGraph(value: Record<string, unknown>): GraphJson {
  if (!isObject(value) || !Object.keys(value).length) return emptyGraph();
  const graph = clone(value) as Obj;
  graph.nodes = Array.isArray(graph.nodes) ? graph.nodes.filter(isObject) : [];
  return graph as GraphJson;
}

/** Key-order independent JSON (the backend stores the graph with sorted keys; project-store compares the same way). */
function canonical(value: unknown): string {
  if (Array.isArray(value)) return `[${value.map(canonical).join(",")}]`;
  if (isObject(value)) return `{${Object.keys(value).sort().filter(key => value[key] !== undefined)
    .map(key => JSON.stringify(key) + ":" + canonical(value[key])).join(",")}}`;
  return JSON.stringify(value) ?? "null";
}
/** Whether two drafts serialize to the same stored graph, whatever their key order. */
export const sameGraph = (a: GraphJson, b: GraphJson) => canonical(graphToJson(a)) === canonical(graphToJson(b));

const DEFAULT_SETTINGS = canonical(emptyGraph());
/** The JSON the project stores: an empty graph with default settings is `{}`; undefined values are dropped. */
export function graphToJson(graph: GraphJson): Record<string, unknown> {
  const json = JSON.parse(JSON.stringify(graph)) as Obj;   // drops undefined values
  if (!json.nodes?.length && !Object.keys(json).some(key => !["nodes", "max_concurrent", "max_total_cost_usd", "retry_policy"].includes(key))
      && canonical({ nodes: [], max_concurrent: json.max_concurrent, max_total_cost_usd: json.max_total_cost_usd, retry_policy: json.retry_policy }) === DEFAULT_SETTINGS)
    return {};
  return json;
}

/** The next free `<prefix>-<n>`: after every number the draft or `reserved` (the saved revision's IDs) uses. */
export function nextId(graph: GraphJson, kind: NodeKind, reserved: Iterable<string> = []): string {
  const prefix = ID_PREFIX[kind];
  const taken = new Set([...graph.nodes.map(node => String(node.id)), ...reserved]);
  let number = 1 + Math.max(0, ...[...taken].map(id => new RegExp(`^${prefix}-(\\d+)$`).exec(id)).filter(Boolean).map(match => Number(match![1])));
  while (taken.has(`${prefix}-${number}`)) number++;
  return `${prefix}-${number}`;
}

const defaultLabel = (kind: NodeKind, id: string) => `${KIND_LABELS[kind]} ${/-(\d+)$/.exec(id)?.[1] ?? ""}`.trim();
export const initializationFor = (method: Method | undefined): ModelInputJson["initialization"] =>
  method === "full" || method === "freeze" ? "full_weights" : "new_adapter";
/** A root input: the HF root as the policy base, initialization from the recipe's method family. */
export const rootInput = (root: HFRootRef, method: Method | undefined): ModelInputJson =>
  ({ policy: { base: clone(root), adapter: null }, initialization: initializationFor(method) });
const recipeOf = (node: NodeJson | undefined, ctx: DraftContext) =>
  node?.kind === "train" && typeof node.recipe_revision === "string" ? ctx.recipes.get(node.recipe_revision) : undefined;
const firstOf = <K, V>(map: Map<K, V>) => map.values().next().value as V | undefined;
function selectionFor(kinds: string[]): Partial<SelectionJson> {
  const metric = metricChoices(kinds).find(choice => choice.allowed)?.metric;
  return { ...(metric ? { metric } : {}), direction: "max", thresholds: [], allow_partial: false, min_success: 2, merge_regression_tolerance: 0 };
}
const benchmarkKind = (node: NodeJson | undefined, ctx: DraftContext) =>
  node?.kind === "evaluate" && node.benchmark_revision ? ctx.benchmarks.get(node.benchmark_revision)?.spec.evaluator_kind : undefined;

/** A new card with the context's defaults: the first recipe, root, train binding and benchmark; the default profiles. */
function freshNode(graph: GraphJson, kind: NodeKind, ctx: DraftContext): NodeJson {
  const id = nextId(graph, kind, ctx.reservedIds);
  const node: Obj = { id, kind, label: defaultLabel(kind, id) };
  if (kind === "train") {
    const recipe = firstOf(ctx.recipes);
    if (recipe) node.recipe_revision = recipe.revision_sha256;
    if (ctx.roots[0]) node.model_input = rootInput(ctx.roots[0].root, recipe?.training_method);
    const binding = ctx.bindings.find(item => item.role === "train");
    if (binding) node.dataset_binding = binding.binding_id;
  }
  if (kind === "evaluate") { const benchmark = firstOf(ctx.benchmarks); if (benchmark) node.benchmark_revision = benchmark.revision_sha256; }
  if (kind === "select") node.selection = selectionFor([]);
  if (kind !== "select" && ctx.defaultProfiles.length) node.executor_profile_ids = [...ctx.defaultProfiles];
  return node as NodeJson;
}
function guard(graph: GraphJson) { if (graph.nodes.length >= MAX_NODES) throw new Error(REASONS.limit); }
const withNode = (graph: GraphJson, node: NodeJson): GraphJson => ({ ...clone(graph), nodes: [...clone(graph.nodes), node] });

export function addNode(graph: GraphJson, kind: NodeKind, ctx: DraftContext): { graph: GraphJson; id: string } {
  guard(graph);
  const node = freshNode(graph, kind, ctx);
  return { graph: withNode(graph, node), id: node.id };
}

/** A child of `fromId` without the connection check (starting shapes may hold reasons the cards then show). */
function serialChild(graph: GraphJson, fromId: string, kind: NodeKind, ctx: DraftContext): NodeJson {
  const parent = findNode(graph, fromId);
  if (!parent) throw new Error(REASONS.missingNode);
  const node = freshNode(graph, kind, ctx) as Obj;
  if (kind === "train") {
    node.model_input = outputRef(fromId);
    if (parent.kind === "train") {
      if (parent.recipe_revision) node.recipe_revision = parent.recipe_revision;
      if (parent.dataset_binding) node.dataset_binding = parent.dataset_binding;
    }
  } else if (kind === "evaluate" || kind === "materialize") node.model = outputRef(fromId);
  else if (kind === "select") { node.evaluations = [outputRef(fromId, "evaluation")]; node.selection = selectionFor([benchmarkKind(parent, ctx)].filter(Boolean) as string[]); }
  else if (kind === "merge") { node.models = [outputRef(fromId)]; node.weights = [1]; }
  const profiles = (parent as Obj).executor_profile_ids;
  if (kind !== "select" && Array.isArray(profiles) && profiles.length) node.executor_profile_ids = [...profiles];
  return node as NodeJson;
}
const serialPort = (kind: NodeKind): InputPort => kind === "train" ? "model_input" : kind === "select" ? "evaluations" : kind === "merge" ? "models" : "model";

/** Which kinds may follow `fromId` as a serial card, with the reason for the others. */
export function serialKinds(graph: GraphJson, fromId: string, ctx: DraftContext): { kind: NodeKind; label: string; allowed: boolean; reason?: string }[] {
  return KINDS.map(kind => {
    let reason: string | undefined;
    if (graph.nodes.length >= MAX_NODES) reason = REASONS.limit;
    else {
      const child = serialChild(graph, fromId, kind, ctx);
      const tentative = withNode(graph, child);
      reason = inputOptions(tentative, child.id, serialPort(kind), ctx).find(option => option.value.node_id === fromId)?.reason;
    }
    return { kind, label: KIND_LABELS[kind], allowed: !reason, ...(reason ? { reason } : {}) };
  });
}

export function addSerial(graph: GraphJson, fromId: string, kind: NodeKind, ctx: DraftContext): { graph: GraphJson; id: string } {
  guard(graph);
  const reason = serialKinds(graph, fromId, ctx).find(choice => choice.kind === kind)?.reason;
  if (reason) throw new Error(reason);
  const node = serialChild(graph, fromId, kind, ctx);
  return { graph: withNode(graph, node), id: node.id };
}

const SIBLING_FIELDS: Record<NodeKind, string[]> = {
  train: ["recipe_revision", "model_input", "dataset_binding", "reward_binding", "executor_profile_ids"],
  evaluate: ["model", "benchmark_revision", "executor_profile_ids"], select: ["evaluations", "selection"],
  merge: ["models", "weights", "executor_profile_ids"], materialize: ["model", "executor_profile_ids"],
};
/** A sibling of the same kind with the same inputs and choices (a root train copies its model input); never the
 * sibling's hidden keys (recipe overrides), which the card does not show. */
export function addParallel(graph: GraphJson, siblingId: string, ctx: DraftContext): { graph: GraphJson; id: string } {
  guard(graph);
  const sibling = findNode(graph, siblingId);
  const kind = kindOf(sibling);
  if (!sibling) throw new Error(REASONS.missingNode);
  if (!kind) throw new Error(REASONS.unknownKind);
  const id = nextId(graph, kind, ctx.reservedIds);
  const node: Obj = { id, kind, label: defaultLabel(kind, id) };
  for (const field of SIBLING_FIELDS[kind]) if ((sibling as Obj)[field] !== undefined) node[field] = clone((sibling as Obj)[field]);
  return { graph: withNode(graph, node as NodeJson), id };
}

/** Removes a card and every reference to it (inputs, merge models with their weights, explicit dependencies). */
export function removeNode(graph: GraphJson, id: string): GraphJson {
  const next = clone(graph);
  next.nodes = next.nodes.filter(node => node.id !== id);
  for (const node of next.nodes as Obj[]) {
    for (const port of ["model_input", "model", "reward_binding"]) if (isRef(node[port]) && node[port].node_id === id) delete node[port];
    if (Array.isArray(node.evaluations)) {
      node.evaluations = node.evaluations.filter((item: unknown) => !(isRef(item) && item.node_id === id));
      if (!node.evaluations.length) delete node.evaluations;
    }
    if (Array.isArray(node.models)) {
      const keep = node.models.map((item: unknown) => !(isRef(item) && item.node_id === id));
      if (Array.isArray(node.weights) && node.weights.length === node.models.length) node.weights = node.weights.filter((_: unknown, index: number) => keep[index]);
      node.models = node.models.filter((_: unknown, index: number) => keep[index]);
      if (!node.models.length) { delete node.models; delete node.weights; }
    }
  }
  if (Array.isArray(next.depends_on)) {
    next.depends_on = next.depends_on.filter(edge => edge.node_id !== id && edge.depends_on !== id);
    if (!next.depends_on.length) delete next.depends_on;     // no empty list left behind
  }
  return next;
}

/** Sets one input port (null or an empty list removes it). A changed merge model count resets equal weights. */
export function connect(graph: GraphJson, nodeId: string, port: InputPort, value: NodeOutputRef | NodeOutputRef[] | ModelInputJson | ModelLoadRefJson | null): GraphJson {
  const next = clone(graph);
  const node = findNode(next, nodeId) as Obj | undefined;
  if (!node) return next;
  if (value === null || (Array.isArray(value) && !value.length)) {
    delete node[port];
    if (port === "models") delete node.weights;
    return next;
  }
  if (port === "models" && Array.isArray(value)) {
    const before = Array.isArray(node.models) ? node.models.length : 0;
    node.models = clone(value);
    if (value.length !== before || !Array.isArray(node.weights) || node.weights.length !== value.length)
      node.weights = value.length === 1 ? [1] : value.length >= 2 && value.length <= 4 ? equalWeights(value.length as 2 | 3 | 4) : node.weights;
    return next;
  }
  node[port] = clone(value);
  return next;
}

// -- graph relations --
/** The graph's connections as graph_schema.graph_edges counts them: explicit dependencies and input references, each
 * (from, to) pair once. */
export function graphEdgeCount(graph: GraphJson): number {
  const edges = new Set<string>();
  for (const node of graph.nodes) for (const { ref } of refsOf(node)) edges.add(JSON.stringify([ref.node_id, String(node.id)]));
  for (const edge of graph.depends_on ?? []) if (isObject(edge)) edges.add(JSON.stringify([String(edge.depends_on), String(edge.node_id)]));
  return edges.size;
}
function childrenMap(graph: GraphJson) {
  const children = new Map<string, Set<string>>();
  const add = (from: string, to: string) => { if (!children.has(from)) children.set(from, new Set()); children.get(from)!.add(to); };
  for (const node of graph.nodes) for (const { ref } of refsOf(node)) add(ref.node_id, node.id);
  for (const edge of graph.depends_on ?? []) if (isObject(edge)) add(String(edge.depends_on), String(edge.node_id));
  return children;
}
function reach(start: string, next: Map<string, Set<string>>): Set<string> {
  const seen = new Set<string>();
  const stack = [...(next.get(start) ?? [])];
  while (stack.length) {
    const id = stack.pop()!;
    if (seen.has(id)) continue;
    seen.add(id);
    stack.push(...(next.get(id) ?? []));
  }
  return seen;
}
const descendants = (graph: GraphJson, id: string) => reach(id, childrenMap(graph));
function ancestorsMap(graph: GraphJson) {
  const parents = new Map<string, Set<string>>();
  for (const [from, tos] of childrenMap(graph)) for (const to of tos) { if (!parents.has(to)) parents.set(to, new Set()); parents.get(to)!.add(from); }
  return parents;
}

/** Whether a recipe change from `parent` to `child` needs an explicit materialize step (graph_planner._requires_materialization). */
function needsMaterialize(parent: RecipeSummary, child: RecipeSummary) {
  return ["lora", "qlora", "oft"].includes(parent.training_method)
    && ((parent.training_stage === "sft" && ["dpo", "kto"].includes(child.training_stage)) || parent.training_method !== child.training_method);
}
const plainAdapter = (recipe: RecipeSummary | undefined) => recipe !== undefined && ["lora", "qlora"].includes(recipe.training_method)
  && !["rm", "ppo"].includes(recipe.training_stage);

/** Why `source`'s output cannot feed `target`'s `port` (null: it can). Cycle and port type first, then the routes. */
function connectionReason(graph: GraphJson, target: NodeJson, port: InputPort, source: NodeJson, ctx: DraftContext,
  options: { closesCycle: boolean; current?: string[] }): string | null {
  if (options.closesCycle) return REASONS.cycle;
  const required = port === "evaluations" ? "evaluation" : "model";
  if (required === "model" && source.kind === "evaluate") return REASONS.evaluationAsModel;
  if (required === "evaluation" && source.kind !== "evaluate") return REASONS.notEvaluation;
  if (required === "evaluation") return null;
  if (port === "models" && options.current && !options.current.includes(source.id) && options.current.length >= 4) return REASONS.mergeLimit;
  if (port === "reward_binding") return null;
  const parentRecipe = recipeOf(source, ctx);
  const childRecipe = recipeOf(target, ctx);
  if (parentRecipe?.training_method === "oft") return REASONS.oftParent;
  const parentPpo = parentRecipe?.training_stage === "ppo";
  const childPpo = childRecipe?.training_stage === "ppo";
  if (source.kind === "train" && parentRecipe && (target.kind !== "train" ? parentPpo : childRecipe && parentPpo !== childPpo)) return REASONS.ppoRoute;
  if (target.kind === "train" && childPpo && (source.kind === "merge" || source.kind === "materialize")) return REASONS.ppoRoute;
  if (target.kind === "merge" && (source.kind === "materialize" || (source.kind === "train" && parentRecipe && !plainAdapter(parentRecipe))))
    return REASONS.mergeSource;
  if (target.kind === "materialize" && (source.kind === "materialize" || (source.kind === "train" && parentRecipe
      && (!["lora", "qlora"].includes(parentRecipe.training_method) || parentRecipe.training_stage === "rm"))))
    return REASONS.materializeSource;
  if (target.kind === "train" && port === "model_input" && parentRecipe && childRecipe) {
    if (needsMaterialize(parentRecipe, childRecipe)) return REASONS.materialize;
    if (childPpo && ["lora", "qlora"].includes(parentRecipe.training_method)) return REASONS.ppoContinuation;
  }
  return null;
}

/** Every card as a source for `nodeId`'s `port`: allowed, or disabled with the local reason. */
export function inputOptions(graph: GraphJson, nodeId: string, port: InputPort, ctx: DraftContext): InputOption[] {
  const target = findNode(graph, nodeId);
  if (!target) return [];
  const blocked = descendants(graph, nodeId);
  blocked.add(nodeId);
  const output = port === "evaluations" ? "evaluation" : "model";
  const current = port === "models" && Array.isArray((target as Obj).models)
    ? ((target as Obj).models as unknown[]).filter(isRef).map(item => item.node_id) : undefined;
  return graph.nodes.map(source => {
    const reason = connectionReason(graph, target, port, source, ctx, { closesCycle: blocked.has(source.id), current });
    return { value: outputRef(source.id, output), label: `${labelOf(source)} · ${KIND_LABELS[source.kind] ?? source.kind}`, allowed: !reason,
      ...(reason ? { reason } : {}) };
  });
}

// -- decimal weights --
/** An exact integer of 10^-digits units for a JS number's shortest decimal text (null when not exact at that scale). */
function scaled(value: number, digits = 40): bigint | null {
  if (!Number.isFinite(value)) return null;
  const [mantissa, exponentText = "0"] = String(value).toLowerCase().split("e");
  const negative = mantissa.startsWith("-");
  const [whole, fraction = ""] = mantissa.replace("-", "").split(".");
  const shift = digits + Number(exponentText) - fraction.length;
  if (shift < 0) return null;
  const result = BigInt(whole + fraction) * 10n ** BigInt(shift);
  return negative ? -result : result;
}
/** Whether positive weights sum to exactly one as decimals (MergeNode: `sum(Fraction(str(w))) == 1`). */
export function weightsValid(weights: unknown, count: number): boolean {
  if (!Array.isArray(weights) || weights.length !== count || !weights.every(weight => typeof weight === "number" && Number.isFinite(weight) && weight > 0)) return false;
  let total = 0n;
  for (const weight of weights) { const units = scaled(weight); if (units === null) return false; total += units; }
  return total === 10n ** 40n;
}
/** Whole-percent equal weights; the first takes the remainder ([0.34, 0.33, 0.33]). */
export function equalWeights(count: 2 | 3 | 4): number[] {
  const share = Math.floor(100 / count);
  return Array.from({ length: count }, (_, index) => (share + (index === 0 ? 100 - share * count : 0)) / 100);
}
/** A weight as the percent text the form shows (0.3333 → "33.33"). */
export function percentText(weight: unknown): string {
  if (typeof weight !== "number" || !Number.isFinite(weight)) return "";
  const units = scaled(weight, 40);                        // the scale weightsValid uses: tiny weights round-trip
  if (units === null) return String(weight * 100);
  const text = (units * 100n).toString().padStart(41, "0");
  const whole = text.slice(0, -40).replace(/^0+(?=\d)/, ""), fraction = text.slice(-40).replace(/0+$/, "");
  return fraction ? `${whole}.${fraction}` : whole;
}
/** A percent text as an exact decimal weight (null when it is not a decimal number). */
export function weightFromPercent(text: string): number | null {
  const trimmed = text.trim();
  if (!/^\d+(\.\d+)?$/.test(trimmed)) return null;
  const [whole, fraction = ""] = trimmed.split(".");
  const digits = (whole + fraction).replace(/^0+(?=\d)/, "");
  const exponent = fraction.length + 2;
  const padded = digits.padStart(exponent + 1, "0");
  return Number(`${padded.slice(0, -exponent)}.${padded.slice(-exponent)}`);
}

// -- metrics (D7) --
export function metricChoices(evaluatorKinds: string[]): MetricChoice[] {
  const kinds = [...new Set(evaluatorKinds)];
  const table = EVALUATOR_METRICS as Record<string, readonly string[]>;
  let reason: string | undefined;
  if (!kinds.length) reason = REASONS.noEvaluations;
  else if (kinds.some(kind => !Object.hasOwn(table, kind) || !table[kind].length)) reason = REASONS.noMetrics;
  else if (kinds.length > 1) reason = REASONS.mixedEvaluators;
  const metrics = [...new Set(kinds.flatMap(kind => Object.hasOwn(table, kind) ? [...table[kind]] : []))];
  const choices = metrics.map(metric => ({ metric, label: METRIC_LABELS[metric] ?? metric, ...(METRIC_NOTES[metric] ? { note: METRIC_NOTES[metric] } : {}),
    allowed: !reason, ...(reason ? { reason } : {}) }));
  if (choices.length) return choices;
  return [{ metric: "", label: reason === REASONS.noEvaluations ? "평가를 먼저 연결하세요" : "고를 수 있는 지표 없음", allowed: false, reason: reason ?? REASONS.noMetrics }];
}
/** The evaluator kinds of a selection's evaluations whose benchmarks are known (null when one is unknown). */
export function selectionEvaluators(graph: GraphJson, node: NodeJson, ctx: DraftContext): string[] | null {
  if (node.kind !== "select") return [];
  const kinds: string[] = [];
  for (const { ref } of refsOf(node)) {
    const kind = benchmarkKind(findNode(graph, ref.node_id), ctx);
    if (!kind) return null;
    kinds.push(kind);
  }
  return kinds;
}

// -- local issues (D7) --
const local = (code: string, message: string, path: string, node_id?: string): WorkflowIssue =>
  ({ code, message, path, ...(node_id !== undefined ? { node_id } : {}) });
const isInteger = (value: unknown, low: number, high: number) => typeof value === "number" && Number.isInteger(value) && value >= low && value <= high;

/** What the draft alone proves wrong or missing, with `local_*` codes, the card's `node_id` and its pointer. */
export function localIssues(graph: GraphJson, ctx: DraftContext): WorkflowIssue[] {
  const issues: WorkflowIssue[] = [];
  const at = (index: number, field?: string) => `/graph/nodes/${index}${field ? `/${field}` : ""}`;
  if (graph.nodes.length > MAX_NODES) issues.push(local("local_limit", REASONS.limit, "/graph/nodes"));
  if (graphEdgeCount(graph) > MAX_EDGES || (graph.depends_on?.length ?? 0) > MAX_EDGES) issues.push(local("local_edge_limit", REASONS.edgeLimit, "/graph"));
  if (graph.max_concurrent !== undefined && !isInteger(graph.max_concurrent, 1, 64)) issues.push(local("local_settings", REASONS.settingsConcurrent, "/graph/max_concurrent"));
  if (graph.max_total_cost_usd !== undefined && !(typeof graph.max_total_cost_usd === "number" && graph.max_total_cost_usd > 0))
    issues.push(local("local_settings", REASONS.settingsCost, "/graph/max_total_cost_usd"));
  if (graph.retry_policy !== undefined) {
    if (!isInteger(graph.retry_policy?.max_attempts, 1, 5)) issues.push(local("local_settings", REASONS.settingsAttempts, "/graph/retry_policy/max_attempts"));
    if (!isInteger(graph.retry_policy?.backoff_seconds, 0, 3600)) issues.push(local("local_settings", REASONS.settingsBackoff, "/graph/retry_policy/backoff_seconds"));
  }
  const seen = new Set<string>();
  const parents = ancestorsMap(graph);
  const inCycle = new Set(graph.nodes.map(node => node.id).filter(id => reach(id, parents).has(id)));
  graph.nodes.forEach((node, index) => {
    const id = node.id;
    const n = node as Obj;
    const add = (code: string, message: string, field?: string) => issues.push(local(code, message, at(index, field), id));
    if (seen.has(id)) add("local_duplicate", REASONS.duplicateId, "id");
    seen.add(id);
    if (!(typeof n.label === "string" && n.label.trim())) add("local_required", REASONS.label, "label");
    if (inCycle.has(id)) add("local_cycle", REASONS.cycleExists);
    for (const { port, ref } of refsOf(node)) {
      const source = findNode(graph, ref.node_id);
      if (!source) { add("local_missing_node", REASONS.missingNode, port); continue; }
      const reason = connectionReason(graph, node, port, source, ctx, { closesCycle: false });
      if (reason) add("local_connection", reason, port);
    }
    const profiles = () => { if (!(Array.isArray(n.executor_profile_ids) && n.executor_profile_ids.length)) add("local_required", REASONS.profiles, "executor_profile_ids"); };
    if (node.kind === "train") {
      const recipe = recipeOf(node, ctx);
      if (!n.recipe_revision) add("local_required", REASONS.recipe, "recipe_revision");
      if (recipe?.training_method === "oft") add("local_oft_recipe", REASONS.oftRecipe, "recipe_revision");
      if (n.model_input === undefined) add("local_required", REASONS.modelInput, "model_input");
      if (!n.dataset_binding) add("local_required", REASONS.dataset, "dataset_binding");
      else {
        const binding = ctx.bindings.find(item => item.binding_id === n.dataset_binding);
        if (!binding) add("local_dataset", REASONS.bindingMissing, "dataset_binding");
        else if (binding.role !== "train") add("local_dataset", REASONS.trainRole, "dataset_binding");
      }
      const reward = n.reward_binding ?? (isObject(n.model_input) ? n.model_input.reward_model : null);
      if (recipe?.training_stage === "ppo" && !reward) add("local_ppo_reward", REASONS.ppoReward, "reward_binding");
      if (recipe && recipe.training_stage !== "ppo" && reward) add("local_ppo_reward", REASONS.rewardNotPpo, "reward_binding");
      profiles();
    } else if (node.kind === "evaluate") {
      if (n.model === undefined) add("local_required", REASONS.model, "model");
      if (!n.benchmark_revision) add("local_required", REASONS.benchmark, "benchmark_revision");
      profiles();
    } else if (node.kind === "select") {
      const evaluations = refsOf(node);
      if (!evaluations.length) add("local_required", REASONS.evaluations, "evaluations");
      const benchmarks = new Set(evaluations.map(({ ref }) => findNode(graph, ref.node_id)).filter(item => item?.kind === "evaluate")
        .map(item => (item as EvaluateNodeJson).benchmark_revision).filter(Boolean));
      if (benchmarks.size > 1) add("local_benchmarks_differ", REASONS.mixedEvaluators, "evaluations");
      const selection = isObject(n.selection) ? n.selection : {};
      if (!selection.metric) add("local_required", REASONS.metric, "selection/metric");
      else {
        const kinds = selectionEvaluators(graph, node, ctx);
        if (kinds && kinds.length) {
          const choice = metricChoices(kinds).find(item => item.metric === selection.metric);
          if (!choice?.allowed) add("local_metric", choice?.reason ?? REASONS.unknownMetric, "selection/metric");
        }
      }
      if (selection.min_success !== undefined && !(Number.isInteger(selection.min_success) && selection.min_success >= 2))
        add("local_selection", REASONS.minSuccess, "selection/min_success");
      if (selection.merge_regression_tolerance !== undefined && !(typeof selection.merge_regression_tolerance === "number" && selection.merge_regression_tolerance >= 0))
        add("local_selection", REASONS.tolerance, "selection/merge_regression_tolerance");
    } else if (node.kind === "merge") {
      const models = refsOf(node).map(({ ref }) => ref.node_id);
      const count = Array.isArray(n.models) ? n.models.length : 0;
      if (count < 2 || count > 4 || new Set(models).size !== models.length) add("local_merge_count", REASONS.mergeCount, "models");
      if (count && !weightsValid(n.weights, count)) add("local_merge_weights", REASONS.mergeWeights, "weights");
      profiles();
    } else if (node.kind === "materialize") {
      if (n.model === undefined) add("local_required", REASONS.model, "model");
      profiles();
    }
  });
  return issues;
}

// -- layout and preview (D6) --
/** Columns by the longest path from a root (graph order within a column); every data reference and explicit
 * dependency between existing cards once (a dependency that repeats a data reference is drawn as data). */
export function layout(graph: GraphJson): Layout {
  const ids = graph.nodes.map(node => node.id);
  const known = new Set(ids);
  const edges: Layout["edges"] = [];
  const keys = new Set<string>();
  const add = (from: string, to: string, kind: "data" | "order") => {
    const key = `${from}>${to}`;
    if (from === to || !known.has(from) || !known.has(to) || keys.has(key)) return;
    keys.add(key);
    edges.push({ from, to, kind });
  };
  for (const node of graph.nodes) for (const { ref } of refsOf(node)) add(ref.node_id, node.id, "data");
  for (const edge of graph.depends_on ?? []) if (isObject(edge)) add(String(edge.depends_on), String(edge.node_id), "order");
  const incoming = new Map(ids.map(id => [id, edges.filter(edge => edge.to === id).map(edge => edge.from)]));
  const depth = new Map<string, number>();
  let progress = true;
  while (progress) {
    progress = false;
    for (const id of ids) {
      if (depth.has(id)) continue;
      const parents = incoming.get(id)!;
      if (parents.every(parent => depth.has(parent))) { depth.set(id, Math.max(-1, ...parents.map(parent => depth.get(parent)!)) + 1); progress = true; }
    }
  }
  for (const id of ids) if (!depth.has(id)) {   // inside a cycle: after its placed parents
    depth.set(id, Math.max(-1, ...incoming.get(id)!.filter(parent => depth.has(parent)).map(parent => depth.get(parent)!)) + 1);
  }
  const columns: string[][] = [];
  for (const id of ids) {
    if (columns.flat().includes(id)) continue;   // a duplicate ID is laid out once
    (columns[depth.get(id)!] ??= []).push(id);
  }
  return { columns: columns.filter(Boolean), edges };
}

const escape = (value: unknown) => String(value ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const attribute = (value: unknown) => String(value ?? "").replace(/[&<"']/g, c => ({ "&": "&amp;", "<": "&lt;", '"': "&quot;", "'": "&#39;" }[c]!));
const truncate = (text: string, length: number) => [...text].length > length ? [...text].slice(0, length - 1).join("") + "…" : text;

/** The SVG preview: one `<g data-node>` per card (title = its label), edges with `data-edge="from>to"`. */
export function previewSvg(graph: GraphJson, plan: Layout, marks: { selected?: string; waiting: Set<string>; issues: Set<string> }): string {
  const width = 176, height = 58, gapX = 52, gapY = 16, pad = 12;
  const at = new Map<string, { x: number; y: number }>();
  plan.columns.forEach((column, columnIndex) => column.forEach((id, row) =>
    at.set(id, { x: pad + columnIndex * (width + gapX), y: pad + row * (height + gapY) })));
  const rows = Math.max(1, ...plan.columns.map(column => column.length));
  const totalWidth = pad * 2 + Math.max(1, plan.columns.length) * width + Math.max(0, plan.columns.length - 1) * gapX;
  const totalHeight = pad * 2 + rows * height + (rows - 1) * gapY;
  const paths = plan.edges.map(edge => {
    const from = at.get(edge.from)!, to = at.get(edge.to)!;
    const x1 = from.x + width, y1 = from.y + height / 2, x2 = to.x, y2 = to.y + height / 2, middle = (x1 + x2) / 2;
    return `<path data-edge="${attribute(`${edge.from}>${edge.to}`)}" class="graph-edge ${edge.kind}" d="M${x1} ${y1} C${middle} ${y1}, ${middle} ${y2}, ${x2 - 4} ${y2}" marker-end="url(#graph-arrow)"/>`;
  }).join("");
  const nodes = graph.nodes.filter((node, index) => at.has(node.id) && graph.nodes.findIndex(item => item.id === node.id) === index).map(node => {
    const { x, y } = at.get(node.id)!;
    const classes = ["graph-node", `kind-${kindOf(node) ?? "unknown"}`, marks.selected === node.id ? "selected" : "",
      marks.issues.has(node.id) ? "issue" : "", marks.waiting.has(node.id) ? "waiting" : ""].filter(Boolean).join(" ");
    const kind = KIND_LABELS[node.kind] ?? "알 수 없는 단계";
    const waiting = marks.waiting.has(node.id);
    return `<g data-node="${attribute(node.id)}" class="${classes}" transform="translate(${x} ${y})"><title>${escape(labelOf(node))}</title>`
      + `<desc>${escape(`${kind} · ${node.id}${waiting ? ` · ${NOTES.waiting}` : ""}${marks.issues.has(node.id) ? " · 확인 필요" : ""}`)}</desc>`
      + `<rect width="${width}" height="${height}" rx="7"/><text class="graph-node-label" x="12" y="24">${escape(truncate(labelOf(node), 15))}</text>`
      + `<text class="graph-node-meta" x="12" y="44">${escape(truncate(`${kind} · ${node.id}`, 20))}</text>`
      + (waiting ? `<text class="graph-node-wait" x="${width - 12}" y="44" text-anchor="end">대기</text>` : "") + "</g>";
  }).join("");
  return `<svg class="graph-preview-svg" xmlns="http://www.w3.org/2000/svg" role="img" aria-label="학습 그래프 미리보기: 단계 ${graph.nodes.length}개, 연결 ${plan.edges.length}개" `
    + `width="${totalWidth}" height="${totalHeight}" viewBox="0 0 ${totalWidth} ${totalHeight}"><defs><marker id="graph-arrow" viewBox="0 0 8 8" refX="7" refY="4" markerWidth="7" markerHeight="7" orient="auto"><path d="M0 0 8 4 0 8z"/></marker></defs>`
    + `<g class="graph-edges">${paths}</g>${nodes}</svg>`;
}

/** Cards with exactly one destination that equals the single destination of another card that is neither its
 * ancestor nor its descendant: they cannot run at the same time (draft: executor_profile_ids; 10.6: plan resources). */
export function waitingOnSharedProfile(graph: GraphJson, allowed?: Map<string, string[]>): Set<string> {
  const single = new Map<string, string>();
  for (const node of graph.nodes) {
    if (node.kind === "select") continue;
    const destinations = allowed ? allowed.get(node.id) : (node as Obj).executor_profile_ids;
    if (Array.isArray(destinations) && new Set(destinations).size === 1) single.set(node.id, String(destinations[0]));
  }
  const parents = ancestorsMap(graph);
  const ancestors = new Map([...single.keys()].map(id => [id, reach(id, parents)]));
  const waiting = new Set<string>();
  const ids = [...single.keys()];
  for (const [index, a] of ids.entries()) for (const b of ids.slice(index + 1)) {
    if (single.get(a) !== single.get(b) || ancestors.get(a)!.has(b) || ancestors.get(b)!.has(a)) continue;
    waiting.add(a); waiting.add(b);
  }
  return waiting;
}

/** The four starting shapes of an empty graph (design §4). Reasons they may hold show on their cards. */
export function starter(shape: StarterShape, ctx: DraftContext): GraphJson {
  const first = addNode(emptyGraph(), "train", ctx);
  let graph = first.graph;
  // Each card connects to the IDs the shape actually got (reserved IDs move them on).
  const child = (fromId: string, kind: NodeKind) => { const node = serialChild(graph, fromId, kind, ctx); graph = withNode(graph, node); return node.id; };
  if (shape === "single") return graph;
  if (shape === "serial") { child(first.id, "train"); return graph; }
  const sibling = addParallel(graph, first.id, ctx);
  graph = sibling.graph;
  const evaluations = [outputRef(child(first.id, "evaluate"), "evaluation"), outputRef(child(sibling.id, "evaluate"), "evaluation")];
  if (shape === "parallel_merge") {
    const merge = child(first.id, "merge");
    graph = connect(graph, merge, "models", [outputRef(first.id), outputRef(sibling.id)]);
    evaluations.push(outputRef(child(merge, "evaluate"), "evaluation"));
  }
  const select = child(evaluations[0].node_id, "select");
  return connect(graph, select, "evaluations", evaluations);
}

/** The HF root a card loads directly, else the first one upstream (for the module-structure link). */
export function upstreamRoot(graph: GraphJson, nodeId: string): HFRootRef | null {
  const seen = new Set<string>();
  const visit = (id: string): HFRootRef | null => {
    if (seen.has(id)) return null;
    seen.add(id);
    const node = findNode(graph, id) as Obj | undefined;
    if (!node) return null;
    const base = isObject(node.model_input) && isObject(node.model_input.policy) ? node.model_input.policy.base : null;
    if (isObject(base) && typeof base.repo_id === "string" && typeof base.revision === "string") return base as HFRootRef;
    for (const { ref } of refsOf(node as NodeJson)) { const found = visit(ref.node_id); if (found) return found; }
    return null;
  };
  return visit(nodeId);
}
export const sameRoot = (a: HFRootRef | null | undefined, b: HFRootRef | null | undefined) => Boolean(a && b)
  && a!.repo_id === b!.repo_id && a!.revision === b!.revision && a!.config_sha256 === b!.config_sha256 && a!.tokenizer_fingerprint === b!.tokenizer_fingerprint;
