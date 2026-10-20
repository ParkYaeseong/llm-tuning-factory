// The graph editor's pure draft model (10.5, D6, D7): serial/parallel cards, starting shapes, layout and preview,
// connection reasons, metric choices and the strict serialized shape. Node tests (no page).
import { test, expect } from "@playwright/test";
import { addNode, addParallel, addSerial, connect, emptyGraph, equalWeights, EVALUATOR_METRICS, graphToJson, inputOptions,
  layout, localIssues, MAX_NODES, metricChoices, nextId, previewSvg, readGraph, serialKinds, starter, waitingOnSharedProfile,
  type DraftContext, type GraphJson, type NodeJson } from "../src/graph-model.ts";
import type { BenchmarkRevision, ModelRoot, RecipeSummary } from "../src/workflow-types.ts";

const HOSTILE = "<img src=x onerror=alert(1)>";
const H = (c: string) => c.repeat(64);
const ROOT = { repo_id: "Qwen/Qwen2.5-0.5B-Instruct", revision: "a".repeat(40), config_sha256: H("c"), tokenizer_fingerprint: H("d") };
const recipe = (name: string, method: RecipeSummary["training_method"], revision: string, stage: RecipeSummary["training_stage"] = "sft"): RecipeSummary =>
  ({ recipe_id: name, version: "1", name, description: "", training_stage: stage, training_method: method, input_kind: "instruction", revision_sha256: revision });
const LORA = recipe("lora-sft", "lora", H("1"));
const FULL = recipe("full-sft", "full", H("2"));
const OFT = recipe("oft-sft", "oft", H("3"));
const QLORA = recipe("qlora-sft", "qlora", H("4"));
const PPO = recipe("ppo", "full", H("5"), "ppo");
const root: ModelRoot = { root: ROOT, snapshot_ready: true, targets_ready: false,
  profile: { repo_id: ROOT.repo_id, revision: ROOT.revision, config_sha256: ROOT.config_sha256, architecture: "qwen2", template: "qwen" } };
const benchmark = (revision: string, evaluator: string): BenchmarkRevision => ({ revision_sha256: revision, spec: {
  dataset: { dataset_id: "val", version: "1", sha256: H("9") }, evaluator_kind: evaluator, evaluator_version: "1",
  ordered_sample_ids: ["row:0"], max_new_tokens: 64, cutoff_len: 512, seed: 1, generation_mode: "greedy", precision: "bf16",
  quantization_bit: null, tokenizer_fingerprint: ROOT.tokenizer_fingerprint, template: "qwen", semantic_model_kind: "causal_lm" } });
const TEXT = benchmark(H("b"), "text");
const PIPELINE = benchmark(H("e"), "pipeline");
const ctx: DraftContext = {
  recipes: new Map([LORA, FULL, OFT, QLORA, PPO].map(item => [item.revision_sha256, item])),
  bindings: [{ binding_id: "train", role: "train", dataset: { dataset_id: "tr", version: "1", sha256: H("8") } },
    { binding_id: "validation", role: "validation", dataset: { dataset_id: "val", version: "1", sha256: H("9") } }],
  roots: [root], benchmarks: new Map([[TEXT.revision_sha256, TEXT], [PIPELINE.revision_sha256, PIPELINE]]), defaultProfiles: ["pod-a"],
};
const node = (graph: GraphJson, id: string) => graph.nodes.find(item => item.id === id) as any;
const kinds = (graph: GraphJson) => graph.nodes.map(item => `${item.id}:${item.kind}`);
const ref = (node_id: string, output_name: "model" | "evaluation" = "model") => ({ node_id, output_name });
const train = (id: string, revision: string, model_input: unknown = { policy: { base: ROOT, adapter: null }, initialization: "new_adapter" }): NodeJson =>
  ({ id, kind: "train", label: id, recipe_revision: revision, model_input, dataset_binding: "train", executor_profile_ids: ["pod-a"] } as NodeJson);
/** Every string value anywhere in a JSON value (to prove no empty string is written). */
const strings = (value: unknown): string[] => typeof value === "string" ? [value] : Array.isArray(value) ? value.flatMap(strings)
  : value && typeof value === "object" ? Object.values(value).flatMap(strings) : [];
/** A decimal number string as an exact integer of 10^-12 units. */
const units = (value: number) => { const [whole, fraction = ""] = String(value).split("."); return BigInt(whole + fraction.padEnd(12, "0")); };

test("a serial card is fed by the selected card's output", () => {
  const single = starter("single", ctx);
  const { graph: two, id } = addSerial(single, "train-1", "train", ctx);
  expect(id).toBe("train-2");
  expect(node(two, "train-2").model_input).toEqual(ref("train-1"));
  // A train child continues its parent's recipe, data and destinations.
  expect(node(two, "train-2")).toMatchObject({ recipe_revision: LORA.revision_sha256, dataset_binding: "train", executor_profile_ids: ["pod-a"] });
  const { graph: evaluated, id: evaluation } = addSerial(two, "train-2", "evaluate", ctx);
  expect(evaluation).toBe("eval-1");
  expect(node(evaluated, "eval-1").model).toEqual(ref("train-2"));
  const { graph: selected, id: selection } = addSerial(evaluated, "eval-1", "select", ctx);
  expect(selection).toBe("select-1");
  expect(node(selected, "select-1").evaluations).toEqual([ref("eval-1", "evaluation")]);
  const { graph: materialized, id: model } = addSerial(two, "train-1", "materialize", ctx);
  expect(model).toBe("model-1");
  expect(node(materialized, "model-1").model).toEqual(ref("train-1"));
  const { graph: merged } = addSerial(two, "train-1", "merge", ctx);
  expect(node(merged, "merge-1").models).toEqual([ref("train-1")]);
  // An evaluation feeds only a selection; a model output never feeds one.
  expect(() => addSerial(selected, "eval-1", "train", ctx)).toThrow("평가 결과는 모델 입력으로 쓸 수 없습니다");
  expect(() => addSerial(two, "train-1", "select", ctx)).toThrow("자동 선택에는 평가 결과만 연결할 수 있습니다");
  const choices = serialKinds(selected, "eval-1", ctx);
  expect(choices.filter(choice => choice.allowed).map(choice => choice.kind)).toEqual(["select"]);
  expect(choices.find(choice => choice.kind === "evaluate")?.reason).toBe("평가 결과는 모델 입력으로 쓸 수 없습니다");
  // The input graph is never mutated.
  expect(kinds(single)).toEqual(["train-1:train"]);
});

test("a parallel card takes the same input as its sibling", () => {
  const single = starter("single", ctx);
  const { graph: rootPair, id } = addParallel(single, "train-1", ctx);
  expect(id).toBe("train-2");
  expect(node(rootPair, "train-2").model_input).toEqual({ policy: { base: ROOT, adapter: null }, initialization: "new_adapter" });
  expect(node(rootPair, "train-2").model_input).toEqual(node(rootPair, "train-1").model_input);
  expect(node(rootPair, "train-2").model_input).not.toBe(node(rootPair, "train-1").model_input);
  const { graph: chain } = addSerial(rootPair, "train-1", "train", ctx);
  const { graph: siblings, id: sibling } = addParallel(chain, "train-3", ctx);
  expect(sibling).toBe("train-4");
  expect(node(siblings, "train-4").model_input).toEqual(ref("train-1"));
  const ids = siblings.nodes.map(item => item.id);
  expect(new Set(ids).size).toBe(ids.length);
  // Unrelated cards on one single destination wait for each other; a parent and its child do not.
  expect([...waitingOnSharedProfile(rootPair)].sort()).toEqual(["train-1", "train-2"]);
  expect([...waitingOnSharedProfile(addSerial(single, "train-1", "train", ctx).graph)]).toEqual([]);
  expect(waitingOnSharedProfile({ ...rootPair, nodes: rootPair.nodes.map(item => item.id === "train-2" ? { ...item, executor_profile_ids: ["pod-b"] } as NodeJson : item) }).size).toBe(0);
  expect(waitingOnSharedProfile({ ...rootPair, nodes: rootPair.nodes.map(item => item.id === "train-2" ? { ...item, executor_profile_ids: ["pod-a", "pod-b"] } as NodeJson : item) }).size).toBe(0);
  // 10.6 passes the plan's resources instead of the draft's profiles.
  expect([...waitingOnSharedProfile(rootPair, new Map([["train-1", ["r1"]], ["train-2", ["r1", "r2"]]]))]).toEqual([]);
});

test("starting shapes build single, serial, parallel and parallel-merge drafts", () => {
  const single = starter("single", ctx);
  expect(kinds(single)).toEqual(["train-1:train"]);
  expect(node(single, "train-1")).toEqual({ id: "train-1", kind: "train", label: "학습 1", recipe_revision: LORA.revision_sha256,
    model_input: { policy: { base: ROOT, adapter: null }, initialization: "new_adapter" }, dataset_binding: "train", executor_profile_ids: ["pod-a"] });
  expect(single).toMatchObject({ max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } });

  const serial = starter("serial", ctx);
  expect(kinds(serial)).toEqual(["train-1:train", "train-2:train"]);
  expect(node(serial, "train-2").model_input).toEqual(ref("train-1"));

  const parallel = starter("parallel", ctx);
  expect(kinds(parallel)).toEqual(["train-1:train", "train-2:train", "eval-1:evaluate", "eval-2:evaluate", "select-1:select"]);
  expect(node(parallel, "train-2").model_input).toEqual(node(parallel, "train-1").model_input);
  expect(node(parallel, "eval-1")).toMatchObject({ model: ref("train-1"), benchmark_revision: TEXT.revision_sha256 });
  expect(node(parallel, "eval-2")).toMatchObject({ model: ref("train-2"), benchmark_revision: TEXT.revision_sha256 });
  expect(node(parallel, "select-1").evaluations).toEqual([ref("eval-1", "evaluation"), ref("eval-2", "evaluation")]);
  expect(node(parallel, "select-1").selection).toEqual({ metric: "exact_match", direction: "max", thresholds: [], allow_partial: false,
    min_success: 2, merge_regression_tolerance: 0 });

  const merged = starter("parallel_merge", ctx);
  expect(kinds(merged)).toEqual(["train-1:train", "train-2:train", "eval-1:evaluate", "eval-2:evaluate", "merge-1:merge",
    "eval-3:evaluate", "select-1:select"]);
  expect(node(merged, "merge-1")).toMatchObject({ models: [ref("train-1"), ref("train-2")], weights: equalWeights(2), executor_profile_ids: ["pod-a"] });
  expect(node(merged, "eval-3").model).toEqual(ref("merge-1"));
  expect(node(merged, "select-1").evaluations).toEqual([ref("eval-1", "evaluation"), ref("eval-2", "evaluation"), ref("eval-3", "evaluation")]);
  // A full-weights recipe starts from the full model.
  const full = starter("single", { ...ctx, recipes: new Map([[FULL.revision_sha256, FULL]]) });
  expect(node(full, "train-1").model_input.initialization).toBe("full_weights");
  // Without roots, recipes, data, benchmarks or profiles the fields stay absent, never empty strings.
  const bare = starter("parallel", { recipes: new Map(), bindings: [], roots: [], benchmarks: new Map(), defaultProfiles: [] });
  expect(Object.keys(node(bare, "train-1")).sort()).toEqual(["id", "kind", "label"]);
  expect(Object.keys(node(bare, "eval-1")).sort()).toEqual(["id", "kind", "label", "model"]);
  expect(strings(graphToJson(bare)).filter(value => value === "")).toEqual([]);
});

test("layout places every node after its inputs and draws every edge once", () => {
  const base = starter("parallel_merge", ctx);
  const graph: GraphJson = { ...base, depends_on: [{ node_id: "merge-1", depends_on: "eval-1" }, { node_id: "eval-1", depends_on: "train-1" }],
    nodes: base.nodes.map(item => item.id === "select-1" ? { ...item, evaluations: [...(item as any).evaluations, ref("eval-1", "evaluation")] } as NodeJson
      : item.id === "train-2" ? { ...item, label: `${HOSTILE} 비교` } : item) };
  const result = layout(graph);
  expect(result.columns).toEqual([["train-1", "train-2"], ["eval-1", "eval-2"], ["merge-1"], ["eval-3"], ["select-1"]]);
  const column = (id: string) => result.columns.findIndex(ids => ids.includes(id));
  for (const edge of result.edges) expect(column(edge.from)).toBeLessThan(column(edge.to));
  const pairs = result.edges.map(edge => `${edge.from}>${edge.to}:${edge.kind}`);
  expect(new Set(pairs).size).toBe(pairs.length);
  expect(pairs.sort()).toEqual(["eval-1>merge-1:order", "eval-1>select-1:data", "eval-2>select-1:data", "eval-3>select-1:data",
    "merge-1>eval-3:data", "train-1>eval-1:data", "train-1>merge-1:data", "train-2>eval-2:data", "train-2>merge-1:data"]);
  // A reference to a missing card and a cycle still lay out every node once.
  const broken: GraphJson = { ...emptyGraph(), nodes: [{ id: "a", kind: "evaluate", label: "a", model: ref("b") } as NodeJson,
    { id: "b", kind: "materialize", label: "b", model: ref("a") } as NodeJson, { id: "c", kind: "evaluate", label: "c", model: ref("gone") } as NodeJson] };
  expect(layout(broken).columns.flat().sort()).toEqual(["a", "b", "c"]);

  const svg = previewSvg(graph, result, { selected: "merge-1", waiting: new Set(["train-1"]), issues: new Set(["eval-2"]) });
  expect(svg).toContain('role="img"');
  expect(svg).toContain('aria-label="학습 그래프 미리보기: 단계 7개, 연결 9개"');
  expect(svg.match(/<g [^>]*data-node="/g)).toHaveLength(7);
  expect(svg).not.toContain("<img");
  expect(svg).toContain("&lt;img src=x onerror=alert(1)&gt; 비교");
  expect(svg).toContain("같은 실행 자원을 써서 순서대로 실행됩니다 (대기)");
  expect(svg).toMatch(/data-node="merge-1"[^>]*class="[^"]*selected/);
  expect(svg).toMatch(/data-node="eval-2"[^>]*class="[^"]*issue/);
  expect(svg).toMatch(/data-edge="eval-1>merge-1"[^>]*class="[^"]*order/);
});

test("connection options explain why a source cannot be used", () => {
  const graph: GraphJson = { ...emptyGraph(), nodes: [
    train("train-1", LORA.revision_sha256), train("oft-1", OFT.revision_sha256),
    { id: "eval-1", kind: "evaluate", label: "평가 1", model: ref("train-1"), executor_profile_ids: ["pod-a"] } as NodeJson,
    { id: "eval-2", kind: "evaluate", label: "평가 2", model: ref("train-1"), executor_profile_ids: ["pod-a"] } as NodeJson,
    train("train-2", FULL.revision_sha256, ref("train-1")), train("train-3", LORA.revision_sha256, ref("train-1")),
    train("ppo-1", PPO.revision_sha256, ref("train-1")), train("train-4", QLORA.revision_sha256), train("train-5", LORA.revision_sha256),
    { id: "merge-1", kind: "merge", label: "병합", models: [ref("train-1"), ref("train-3"), ref("train-4"), ref("train-5")],
      weights: [0.25, 0.25, 0.25, 0.25], executor_profile_ids: ["pod-a"] } as NodeJson,
  ] };
  const reasons = (nodeId: string, port: Parameters<typeof inputOptions>[2]) =>
    Object.fromEntries(inputOptions(graph, nodeId, port, ctx).map(option => [option.value.node_id, option.allowed ? "ok" : option.reason]));
  const evaluated = reasons("eval-1", "model");
  expect(evaluated["eval-2"]).toBe("평가 결과는 모델 입력으로 쓸 수 없습니다");
  expect(evaluated["eval-1"]).toBe("연결하면 순환이 생깁니다");
  expect(evaluated["oft-1"]).toBe("OFT 결과는 다음 단계로 넘길 수 없습니다");
  expect(evaluated["train-1"]).toBe("ok");
  expect(evaluated["ppo-1"]).toBe("PPO 학습과 다른 학습 단계는 결과를 서로 넘길 수 없습니다");
  // The node itself and its descendants would close a cycle.
  const rooted = reasons("train-1", "model_input");
  expect(rooted["train-1"]).toBe("연결하면 순환이 생깁니다");
  expect(rooted["train-3"]).toBe("연결하면 순환이 생깁니다");
  expect(rooted["merge-1"]).toBe("연결하면 순환이 생깁니다");
  expect(rooted["train-5"]).toBe("ok");
  // QLoRA → LoRA changes the method too.
  expect(rooted["train-4"]).toBe("학습 방법이 바뀌는 연결은 먼저 ‘전체 모델 확정’ 단계가 필요합니다");
  // A Full child of a LoRA parent needs a materialize step first.
  expect(reasons("train-2", "model_input")["train-1"]).toBe("학습 방법이 바뀌는 연결은 먼저 ‘전체 모델 확정’ 단계가 필요합니다");
  expect(reasons("train-3", "model_input")["train-1"]).toBe("ok");
  // A merge refuses a fifth model and a full-weights source.
  const merging = reasons("merge-1", "models");
  expect(merging["train-1"]).toBe("ok");
  expect(merging["oft-1"]).toBe("병합은 최대 4개 모델까지입니다");
  const three = connect(graph, "merge-1", "models", [ref("train-1"), ref("train-3"), ref("train-4")]);
  expect((three.nodes.find(item => item.id === "merge-1") as any).weights).toEqual(equalWeights(3));
  const smaller = Object.fromEntries(inputOptions(three, "merge-1", "models", ctx).map(option => [option.value.node_id, option.allowed ? "ok" : option.reason]));
  expect(smaller["train-2"]).toBe("병합에는 LoRA·QLoRA로 학습한 단계만 쓸 수 있습니다");
  expect(smaller["train-5"]).toBe("ok");
  // A selection takes evaluations only.
  expect(reasons("merge-1", "models")["eval-1"]).toBe("평가 결과는 모델 입력으로 쓸 수 없습니다");
  const selecting: GraphJson = { ...graph, nodes: [...graph.nodes, { id: "select-1", kind: "select", label: "선택", evaluations: [ref("eval-1", "evaluation")] } as NodeJson] };
  const selectable = Object.fromEntries(inputOptions(selecting, "select-1", "evaluations", ctx).map(option => [option.value.node_id, option.allowed ? "ok" : option.reason]));
  expect(selectable["eval-2"]).toBe("ok");
  expect(selectable["train-1"]).toBe("자동 선택에는 평가 결과만 연결할 수 있습니다");
  // The same reasons, raised on the cards of a draft that already holds such connections.
  const wired = connect(graph, "eval-1", "model", ref("eval-2"));
  const issues = localIssues(connect(wired, "train-3", "model_input", ref("oft-1")), ctx);
  expect(issues).toContainEqual(expect.objectContaining({ node_id: "eval-1", message: "평가 결과는 모델 입력으로 쓸 수 없습니다" }));
  expect(issues).toContainEqual(expect.objectContaining({ node_id: "train-3", message: "OFT 결과는 다음 단계로 넘길 수 없습니다" }));
  expect(issues).toContainEqual(expect.objectContaining({ node_id: "train-2", message: "학습 방법이 바뀌는 연결은 먼저 ‘전체 모델 확정’ 단계가 필요합니다" }));
  expect(issues).toContainEqual(expect.objectContaining({ node_id: "oft-1", message: "그래프에서는 OFT 학습을 쓸 수 없습니다" }));
  expect(issues.every(item => item.code.startsWith("local_") && typeof item.node_id === "string")).toBe(true);
  const weights = localIssues(connect(graph, "merge-1", "models", [ref("train-1")]), ctx);
  expect(weights).toContainEqual(expect.objectContaining({ node_id: "merge-1", code: "local_merge_count" }));
  const uneven: GraphJson = { ...graph, nodes: graph.nodes.map(item => item.id === "merge-1" ? { ...item, weights: [0.3, 0.3, 0.3, 0.3] } as NodeJson : item) };
  expect(localIssues(uneven, ctx)).toContainEqual(expect.objectContaining({ node_id: "merge-1", code: "local_merge_weights" }));
});

test("metric choices follow the evaluator and explain unsuitable objectives", () => {
  const text = metricChoices(["text"]);
  expect(text.map(choice => [choice.metric, choice.allowed])).toEqual([["exact_match", true], ["character_f1", true]]);
  expect(text.every(choice => choice.note === "텍스트 정답 일치 지표 · 의미적 품질 점수가 아닙니다")).toBe(true);
  const pipeline = metricChoices(["pipeline"]);
  expect(pipeline.filter(choice => choice.allowed)).toEqual([]);
  expect(pipeline.length).toBeGreaterThan(0);
  expect(pipeline.every(choice => choice.reason === "평가 지표가 없어 자동 선택에 쓸 수 없습니다")).toBe(true);
  const json = metricChoices(["json"]);
  expect(json.map(choice => [choice.metric, choice.allowed, choice.note])).toEqual([["json_validity", true, "형식 지표 · 품질 지표가 아닙니다"]]);
  const mixed = metricChoices(["text", "classification"]);
  expect(mixed.filter(choice => choice.allowed)).toEqual([]);
  expect(mixed.length).toBeGreaterThan(0);
  expect(mixed.every(choice => choice.reason === "서로 다른 평가 기준은 비교할 수 없습니다")).toBe(true);
  expect(metricChoices(["text", "pipeline"]).every(choice => !choice.allowed && choice.reason === "평가 지표가 없어 자동 선택에 쓸 수 없습니다")).toBe(true);
  expect(metricChoices(["kbf_extraction", "kbf_extraction"]).map(choice => choice.metric)).toEqual([...EVALUATOR_METRICS.kbf_extraction]);
  expect(metricChoices([]).some(choice => choice.allowed)).toBe(false);
  // On the cards: different benchmarks under one selection, and a selection over an evaluator without metrics.
  const evaluate = (id: string, revision: string) => ({ id, kind: "evaluate", label: id, model: ref("train-1"), benchmark_revision: revision, executor_profile_ids: ["pod-a"] } as NodeJson);
  const selection = { metric: "exact_match", direction: "max", thresholds: [], allow_partial: false, min_success: 2, merge_regression_tolerance: 0 };
  const graph: GraphJson = { ...emptyGraph(), nodes: [train("train-1", LORA.revision_sha256), evaluate("eval-1", TEXT.revision_sha256),
    evaluate("eval-2", PIPELINE.revision_sha256),
    { id: "select-1", kind: "select", label: "선택", evaluations: [ref("eval-1", "evaluation"), ref("eval-2", "evaluation")], selection } as NodeJson] };
  const issues = localIssues(graph, ctx).filter(item => item.node_id === "select-1").map(item => item.message);
  expect(issues).toContain("서로 다른 평가 기준은 비교할 수 없습니다");
  expect(issues).toContain("평가 지표가 없어 자동 선택에 쓸 수 없습니다");
});

test("drafts serialize to the strict graph shape", () => {
  expect(readGraph({})).toEqual(emptyGraph());
  expect(emptyGraph()).toEqual({ nodes: [], max_concurrent: 1, max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } });
  expect(graphToJson(emptyGraph())).toEqual({});
  const bare: DraftContext = { recipes: new Map(), bindings: [], roots: [], benchmarks: new Map(), defaultProfiles: [] };
  const { graph: evaluation } = addNode(emptyGraph(), "evaluate", bare);
  expect(graphToJson(evaluation)).toEqual({ nodes: [{ id: "eval-1", kind: "evaluate", label: "평가 1" }], max_concurrent: 1,
    max_total_cost_usd: 100, retry_policy: { max_attempts: 1, backoff_seconds: 0 } });
  // Unknown node keys (recipe overrides) survive reading, editing and writing.
  const stored = { nodes: [{ id: "train-1", kind: "train", label: "기준", recipe_revision: LORA.revision_sha256, dataset_binding: "train",
    executor_profile_ids: ["pod-a"], overrides: { hyperparameters: { learning_rate: 0.0002 } }, model_input: ref("x") }],
    depends_on: [{ node_id: "train-1", depends_on: "x" }], max_concurrent: 2, max_total_cost_usd: 30, retry_policy: { max_attempts: 2, backoff_seconds: 60 } };
  expect(graphToJson(readGraph(structuredClone(stored)))).toEqual(stored);
  const edited = graphToJson(connect(readGraph(structuredClone(stored)), "train-1", "model_input", null));
  expect((edited.nodes as any)[0].overrides).toEqual(stored.nodes[0].overrides);
  expect("model_input" in (edited.nodes as any)[0]).toBe(false);
  expect(nextId(emptyGraph(), "train")).toBe("train-1");
  expect(["evaluate", "select", "merge", "materialize"].map(kind => nextId(emptyGraph(), kind as any))).toEqual(["eval-1", "select-1", "merge-1", "model-1"]);
  expect(nextId(readGraph(structuredClone(stored)), "train")).toBe("train-2");
  // At most 64 cards.
  let full = emptyGraph();
  for (let index = 0; index < MAX_NODES; index++) full = addNode(full, "train", ctx).graph;
  expect(full.nodes).toHaveLength(64);
  expect(() => addNode(full, "train", ctx)).toThrow("단계는 최대 64개입니다");
  expect(() => addSerial(full, "train-1", "evaluate", ctx)).toThrow("단계는 최대 64개입니다");
  expect(() => addParallel(full, "train-1", ctx)).toThrow("단계는 최대 64개입니다");
  // Equal merge weights are exact decimals that sum to one.
  expect(equalWeights(2)).toEqual([0.5, 0.5]);
  expect(equalWeights(3)).toEqual([0.34, 0.33, 0.33]);
  expect(equalWeights(4)).toEqual([0.25, 0.25, 0.25, 0.25]);
  for (const count of [2, 3, 4] as const) expect(equalWeights(count).reduce((sum, weight) => sum + units(weight), 0n)).toBe(10n ** 12n);
});
