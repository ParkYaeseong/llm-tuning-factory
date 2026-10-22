// Inline benchmark creation on the graph page (10.5): a sibling `<form id="benchmark-form">` next to the card form,
// never inside it. A benchmark pins a registered held-out binding's data version, the ordered sample IDs `row:0…`,
// the evaluator and the chosen base model's tokenizer fingerprint and template; generation is greedy causal-LM in
// bf16/fp16 (what the pinned worker can enforce). `benchmark_save` answers with the worker's exact refusal text.
import { issueList } from "../issues";
import { EVALUATOR_METRICS, EVALUATOR_VERSION, type DraftContext } from "../graph-model";
import { escapeHtml } from "../workbench";
import type { BenchmarkSpec, BindingRole, WorkflowIssue } from "../workflow-types";
import { rootKey } from "./model-roots";

export interface BenchmarkDraft { binding: string; evaluator: keyof typeof EVALUATOR_METRICS; sampleCount: number;
  maxNewTokens: number; cutoffLen: number; seed: number; precision: "bf16" | "fp16"; quantization: 4 | 8 | null; rootKey: string }

export const EVALUATOR_LABELS: Record<keyof typeof EVALUATOR_METRICS, string> = { text: "텍스트 정답 비교", classification: "분류",
  extraction: "정보 추출", json: "JSON 형식", kbf_extraction: "KBF 추출", pipeline: "학습 완료 확인 (지표 없음)" };
const ROLE_LABELS: Record<BindingRole, string> = { train: "학습", validation: "검증", test: "시험" };
export const HELD_OUT_ONLY = "평가에는 검증·시험(validation·test) 데이터만 쓸 수 있습니다";

/** The rows of each bound data version, from the catalog's registry records (null: unknown). */
export type RowCounts = Map<string, number | null>;

/** A new draft: the first validation binding (else test), text answers, the first root, engine-like defaults. */
export function newBenchmarkDraft(ctx: DraftContext, rows: RowCounts): BenchmarkDraft {
  const binding = ctx.bindings.find(item => item.role === "validation") ?? ctx.bindings.find(item => item.role === "test");
  const available = binding ? rows.get(binding.binding_id) ?? null : null;
  return { binding: binding?.binding_id ?? "", evaluator: "text", sampleCount: available ? Math.min(available, 32) : 10,
    maxNewTokens: 256, cutoffLen: 1024, seed: 42, precision: "bf16", quantization: null,
    rootKey: ctx.roots[0] ? rootKey(ctx.roots[0].root) : "" };
}

/** Why the draft cannot be saved yet (local checks only; the backend decides). */
export function benchmarkProblems(draft: BenchmarkDraft, ctx: DraftContext, rows: RowCounts): string[] {
  const problems: string[] = [];
  const binding = ctx.bindings.find(item => item.binding_id === draft.binding);
  if (!binding) problems.push("평가 데이터를 고르세요.");
  else if (binding.role === "train") problems.push(HELD_OUT_ONLY);
  const available = binding ? rows.get(binding.binding_id) ?? null : null;
  if (!Number.isInteger(draft.sampleCount) || draft.sampleCount < 1 || (available !== null && draft.sampleCount > available))
    problems.push(available !== null ? `샘플 수는 1–${available} 사이의 정수여야 합니다.` : "샘플 수는 1 이상의 정수여야 합니다.");
  if (!Number.isInteger(draft.maxNewTokens) || draft.maxNewTokens < 1) problems.push("답변 최대 토큰은 1 이상의 정수여야 합니다.");
  if (!Number.isInteger(draft.cutoffLen) || draft.cutoffLen < 1) problems.push("최대 길이는 1 이상의 정수여야 합니다.");
  if (!Number.isInteger(draft.seed)) problems.push("시드는 정수여야 합니다.");
  if (!ctx.roots.some(entry => rootKey(entry.root) === draft.rootKey)) problems.push("토크나이저를 쓸 기반 모델을 고르세요. 기반 모델은 ‘기반 모델’에서 먼저 받습니다.");
  return problems;
}

/** The `benchmark_save` spec of a draft: `row:0`…`row:n-1` of the binding's data version, the root's tokenizer. */
export function benchmarkSpec(draft: BenchmarkDraft, ctx: DraftContext): BenchmarkSpec {
  const binding = ctx.bindings.find(item => item.binding_id === draft.binding);
  const root = ctx.roots.find(entry => rootKey(entry.root) === draft.rootKey);
  if (!binding || !root) throw new Error("평가 데이터와 기반 모델을 고르세요.");
  return { dataset: { ...binding.dataset }, evaluator_kind: draft.evaluator, evaluator_version: EVALUATOR_VERSION,
    ordered_sample_ids: Array.from({ length: draft.sampleCount }, (_, index) => `row:${index}`),
    max_new_tokens: draft.maxNewTokens, cutoff_len: draft.cutoffLen, seed: draft.seed, generation_mode: "greedy",
    precision: draft.precision, quantization_bit: draft.quantization, tokenizer_fingerprint: root.root.tokenizer_fingerprint,
    template: String(root.profile?.template ?? ""), semantic_model_kind: "causal_lm" };
}

const option = (value: string, label: string, chosen: string, extra = "") =>
  `<option value="${escapeHtml(value)}"${value === chosen ? " selected" : ""}${extra}>${escapeHtml(label)}</option>`;
const number = (id: string, key: string, label: string, value: number, min: number, help = "") =>
  `<label class="field"><span>${label}</span><input id="${id}" type="number" step="1" min="${min}" data-bench="${key}" aria-label="${label}" value="${escapeHtml(Number.isFinite(value) ? value : "")}">${help ? `<small>${help}</small>` : ""}</label>`;

export function benchmarkFormHtml(draft: BenchmarkDraft, ctx: DraftContext, options: { busy: boolean; issues: WorkflowIssue[]; problems: string[]; rows: RowCounts; nodeLabel: string }): string {
  const bindings = ctx.bindings.map(binding => option(binding.binding_id,
    `${binding.binding_id} · ${ROLE_LABELS[binding.role] ?? binding.role} · ${binding.dataset.dataset_id} v${binding.dataset.version}${binding.role === "train" ? " · 평가에 쓸 수 없음" : ""}`,
    draft.binding, binding.role === "train" ? " disabled" : "")).join("");
  const rows = options.rows.get(draft.binding);
  const evaluators = (Object.keys(EVALUATOR_LABELS) as (keyof typeof EVALUATOR_METRICS)[]).map(kind => option(kind, EVALUATOR_LABELS[kind], draft.evaluator)).join("");
  const roots = ctx.roots.map(entry => option(rootKey(entry.root), `${entry.root.repo_id} · ${entry.root.revision.slice(0, 8)} · 템플릿 ${entry.profile?.template ?? ""}`, draft.rootKey)).join("");
  const metrics = EVALUATOR_METRICS[draft.evaluator] ?? [];
  return `<form id="benchmark-form" class="card graph-benchmark-form" novalidate aria-labelledby="benchmark-form-title">
    <div class="section-title"><h3 id="benchmark-form-title">새 평가 기준</h3></div>
    <p class="muted">‘${escapeHtml(options.nodeLabel)}’ 단계에 쓸 평가 기준을 저장합니다. 탐욕(greedy) 생성 · causal LM · 평가 엔진 버전 ${EVALUATOR_VERSION}.</p>
    <fieldset class="graph-fieldset"${options.busy ? " disabled" : ""}>
    <div class="graph-field"><label class="field"><span>평가 데이터</span><select id="bench-binding" data-bench="binding" aria-label="평가 데이터"><option value="">평가 데이터 선택</option>${bindings}</select></label>
      <small class="muted">${HELD_OUT_ONLY}${typeof rows === "number" ? ` · 데이터 ${rows}행` : ""}</small></div>
    <div class="graph-field"><label class="field"><span>평가 방식</span><select id="bench-evaluator" data-bench="evaluator" aria-label="평가 방식">${evaluators}</select></label>
      <small class="muted">${metrics.length ? `지표: ${escapeHtml(metrics.join(", "))}` : "지표가 없어 자동 선택에 쓸 수 없습니다."}</small></div>
    ${number("bench-samples", "sampleCount", "샘플 수", draft.sampleCount, 1, "앞에서부터 row:0, row:1 … 순서로 고정합니다.")}
    ${number("bench-max-new", "maxNewTokens", "답변 최대 토큰", draft.maxNewTokens, 1)}
    ${number("bench-cutoff", "cutoffLen", "최대 길이 (토큰)", draft.cutoffLen, 1)}
    ${number("bench-seed", "seed", "시드", draft.seed, 0)}
    <label class="field"><span>정밀도</span><select id="bench-precision" data-bench="precision" aria-label="정밀도">${option("bf16", "BF16", draft.precision)}${option("fp16", "FP16", draft.precision)}</select></label>
    <label class="field"><span>양자화</span><select id="bench-quant" data-bench="quantization" aria-label="양자화">${option("", "없음", String(draft.quantization ?? ""))}${option("4", "4비트", String(draft.quantization ?? ""))}${option("8", "8비트", String(draft.quantization ?? ""))}</select></label>
    <div class="graph-field"><label class="field"><span>기반 모델 (토크나이저)</span><select id="bench-root" data-bench="rootKey" aria-label="기반 모델 (토크나이저)"><option value="">기반 모델 선택</option>${roots}</select></label>
      ${ctx.roots.length ? "" : '<small class="muted">‘기반 모델’에서 설정·토크나이저를 먼저 받으세요.</small>'}</div>
    </fieldset>
    ${options.problems.length ? `<ul class="graph-reasons" role="status">${options.problems.map(problem => `<li>${escapeHtml(problem)}</li>`).join("")}</ul>` : ""}
    ${issueList(options.issues, { heading: "저장하지 못한 이유" })}
    <div class="actions"><button type="submit" class="primary" data-focus-key="benchmark-save">평가 기준 저장</button><button type="button" class="quiet" data-action="wf:graph:benchmark-close">닫기</button></div></form>`;
}

/** The draft as the form shows it (the caller skips a locked form). */
export function readBenchmarkForm(form: HTMLFormElement, previous: BenchmarkDraft): BenchmarkDraft {
  const value = (key: string) => form.querySelector<HTMLInputElement | HTMLSelectElement>(`[data-bench="${key}"]`)?.value;
  const integer = (key: string, fallback: number) => { const text = value(key); return text === undefined ? fallback : text.trim() === "" ? NaN : Number(text); };
  const evaluator = value("evaluator");
  const quantization = value("quantization");
  return { binding: value("binding") ?? previous.binding,
    evaluator: evaluator && Object.hasOwn(EVALUATOR_METRICS, evaluator) ? evaluator as BenchmarkDraft["evaluator"] : previous.evaluator,
    sampleCount: integer("sampleCount", previous.sampleCount), maxNewTokens: integer("maxNewTokens", previous.maxNewTokens),
    cutoffLen: integer("cutoffLen", previous.cutoffLen), seed: integer("seed", previous.seed),
    precision: value("precision") === "fp16" ? "fp16" : value("precision") === "bf16" ? "bf16" : previous.precision,
    quantization: quantization === undefined ? previous.quantization : quantization === "4" ? 4 : quantization === "8" ? 8 : null,
    rootKey: value("rootKey") ?? previous.rootKey };
}
