import { t as tr, msg, html as ui, staticHtml, localized } from "../i18n";
// The training-option fields shared by the single-job training form (`data-config`) and the recipe editor
// (`data-recipe`) (D17). With `data-config` the markup is exactly the training form's; recipe mode omits the PPO
// reward-model fields, which a recipe never holds (RecipeSpec has none). Labels are fixed strings; values are escaped.
import { escapeHtml, field, select } from "../workbench";
import type { InputKind, Stage } from "../workflow-types";

export interface FieldSource { attr: "data-config" | "data-recipe"; value(path: string): unknown }
export interface RecipeFieldContext extends FieldSource {
  stages: { id: string; label: string; dataset_kind: string }[];
  capabilities: { supported_stages?: string[]; methods?: string[]; quantization_bits?: number[] } | null;
  recipeMode: boolean;                                           // omit PPO reward fields
  /** Recipe mode only: wraps a rendered field (issues under it). Absent for the training form, whose DOM stays as is. */
  decorate?(path: string, html: string): string;
}

const decorated = (source: FieldSource, path: string, html: string) => (source as RecipeFieldContext).decorate?.(path, html) ?? html;

const plainField = (source: FieldSource, label: string, path: string, type: string, help: string) =>
  field(label, path, source.value(path), type, help).replace("data-key=", `${source.attr}=`)
    .replace("<input ", `<input ${type === "number" ? 'data-number="true" step="any" ' : ""}`);
export function sourceField(source: FieldSource, label: string, path: string, type = "text", help = "") {
  return decorated(source, path, plainField(source, label, path, type, help));
}
/** An empty value reads back as null. */
export function optionalSourceField(source: FieldSource, label: string, path: string, type = "text", help = "") {
  return decorated(source, path, plainField(source, label, path, type, help).replace("<input ", '<input data-optional="true" '));
}
export function sourceSelect(source: FieldSource, label: string, path: string, options: [string, string][], flags = "") {
  const html = select(label, path, source.value(path) as string, options).replace("data-key=", `${source.attr}=`);
  return decorated(source, path, flags ? html.replace("<select ", `<select ${flags} `) : html);
}
export function sourceCheck(source: FieldSource, label: string, path: string) {
  return decorated(source, path, `<label class="check"><input type="checkbox" ${source.attr}="${path}" ${source.value(path) ? "checked" : ""}>${label}</label>`);
}

export function stageSelect(ctx: RecipeFieldContext): string {
  const supported = ctx.capabilities?.supported_stages;
  const current = ctx.value("training_stage");
  return decorated(ctx, "training_stage", ui`<label class="field"><span>학습 단계</span><select aria-label="학습 단계" ${ctx.attr}="training_stage">${ctx.stages.map(stage => `<option value="${escapeHtml(stage.id)}" ${current === stage.id ? "selected" : ""} ${supported && !supported.includes(stage.id) ? "disabled" : ""}>${escapeHtml(tr(stage.label))}${supported && !supported.includes(stage.id) ? tr(" · 이 모델 미지원") : ""}</option>`).join("")}</select></label>`);
}

export function methodSelect(ctx: RecipeFieldContext): string {
  const methods = [["lora", "LoRA"], ["qlora", tr("QLoRA · 양자화 LoRA")], ["full", tr("Full · 전체 갱신")], ["freeze", tr("Freeze · 일부 레이어")], ["oft", "OFT"]];
  const supported = ctx.capabilities?.methods;
  const current = ctx.value("training_method");
  return decorated(ctx, "training_method", ui`<label class="field"><span>학습 방법</span><select aria-label="학습 방법" ${ctx.attr}="training_method">${methods.map(([id, name]) => `<option value="${id}" ${current === id ? "selected" : ""} ${supported && !supported.includes(id) ? "disabled" : ""}>${name}${supported && !supported.includes(id) ? tr(" · 현재 단계 미지원") : ""}</option>`).join("")}</select></label>`);
}

/** The stage card and the method card of the training form (markup identical to the training page's). */
export function trainingOptions(ctx: RecipeFieldContext): string {
  const f = (label: string, path: string, type = "text", help = "") => sourceField(ctx, label, path, type, help);
  const optional = (label: string, path: string, type = "text", help = "") => optionalSourceField(ctx, label, path, type, help);
  const choice = (label: string, path: string, options: [string, string][], flags = "") => sourceSelect(ctx, label, path, options, flags);
  const check = (label: string, path: string) => sourceCheck(ctx, label, path);
  const stage = ctx.value("training_stage");
  const method = ctx.value("training_method");
  let fields = "";
  if (stage === "dpo" || stage === "kto") {
    if (stage === "dpo") fields += choice(tr("선호 학습 loss"), "stage_options.pref_loss", [["sigmoid", "DPO · sigmoid"], ["hinge", "Hinge"], ["ipo", "IPO"], ["kto_pair", "KTO pair"], ["orpo", "ORPO"], ["simpo", "SimPO"]]);
    fields += f(tr("선호 학습 beta"), "stage_options.pref_beta", "number");
    if (stage === "dpo") fields += f(tr("SFT 보조 loss 비중"), "stage_options.pref_ftx", "number") + f("DPO label smoothing", "stage_options.dpo_label_smoothing", "number") + f(tr("BCO 가중치"), "stage_options.pref_bco_weight", "number");
    if (stage === "dpo" && ctx.value("stage_options.pref_loss") === "simpo") fields += f("SimPO gamma", "stage_options.simpo_gamma", "number");
    if (stage === "kto") fields += f(tr("Chosen 가중치"), "stage_options.kto_chosen_weight", "number") + f(tr("Rejected 가중치"), "stage_options.kto_rejected_weight", "number");
  }
  if (stage === "ppo") {
    if (!ctx.recipeMode) fields += optional(tr("보상 모델 원격 경로"), "stage_options.reward_model", "text", tr("GPU에 관리자가 미리 준비한 보상 모델의 절대 경로입니다.")) + choice(tr("보상 모델 종류"), "stage_options.reward_model_type", [["lora", tr("LoRA 어댑터")], ["full", tr("전체 모델")]]) + optional(tr("보상 모델 SHA256"), "stage_options.reward_model_sha256", "text", tr("관리자가 제공한 파일 목록 {상대경로: SHA256}의 정규 JSON SHA256을 입력하세요."));
    fields += f("PPO buffer size", "stage_options.ppo_buffer_size", "number") + f("PPO epoch", "stage_options.ppo_epochs", "number") + f("PPO KL target", "stage_options.ppo_target", "number");
  }
  let methods = "";
  if (method === "lora" || method === "qlora") {
    methods += f("LoRA rank", "hyperparameters.lora_rank", "number") + choice(tr("LoRA 대상"), "hyperparameters.lora_target", [["all", tr("전체")], ["q_proj,v_proj", "q_proj · v_proj"]]) + optional(tr("LoRA alpha · 비우면 자동"), "finetuning_options.lora_alpha", "number") + f("LoRA dropout", "finetuning_options.lora_dropout", "number") + optional(tr("LoRA+ 학습률 비율 · 선택"), "finetuning_options.loraplus_lr_ratio", "number") + f(tr("LoRA+ embedding 학습률"), "finetuning_options.loraplus_lr_embedding", "number") + f(tr("PiSSA 초기화 반복 수"), "finetuning_options.pissa_iter", "number");
    if (method === "qlora") methods += choice(tr("양자화 비트"), "quantization_bit", (ctx.capabilities?.quantization_bits || [4, 8]).map((bit: number) => [String(bit), `${bit}-bit · bitsandbytes`]), 'data-number="true"');
  }
  if (method === "freeze") methods += f(tr("학습할 레이어 수"), "finetuning_options.freeze_trainable_layers", "number") + choice(tr("학습할 모듈"), "finetuning_options.freeze_trainable_modules", [["all", tr("전체")], ["self_attn", "Attention"], ["mlp", "MLP"], ["self_attn,mlp", "Attention + MLP"]]) + choice(tr("추가 학습 모듈 · 선택"), "finetuning_options.freeze_extra_modules", [["", tr("추가 없음")], ["embed_tokens", "Embedding"], ["lm_head", "LM head"], ["norm", "Normalization"], ["embed_tokens,lm_head", "Embedding + LM head"]], 'data-optional="true"');
  if (method === "oft") methods += f("OFT rank", "finetuning_options.oft_rank", "number") + f("OFT block size", "finetuning_options.oft_block_size", "number") + choice(tr("OFT 대상"), "finetuning_options.oft_target", [["all", tr("전체")], ["q_proj,v_proj", "q_proj · v_proj"]]) + f("OFT module dropout", "finetuning_options.module_dropout", "number");
  return `${fields ? ui`<section class="card"><h3>학습 단계별 설정</h3><div class="grid">${fields}</div>${stage === "ppo" ? check("PPO score normalization", "stage_options.ppo_score_norm") + check("PPO reward whitening", "stage_options.ppo_whiten_rewards") : ""}</section>` : ""}${methods ? ui`<section class="card"><h3>파라미터 갱신 설정</h3><div class="grid">${methods}</div>${method === "lora" || method === "qlora" ? check(tr("rsLoRA 사용"), "finetuning_options.use_rslora") + check(tr("DoRA 사용"), "finetuning_options.use_dora") + check(tr("PiSSA 초기화"), "finetuning_options.pissa_init") + check(tr("PiSSA 변환 저장"), "finetuning_options.pissa_convert") : ""}</section>` : ""}`;
}

/** Reads every `[attr]` control under `root` into `target` by its dotted path (the training form's capture rules):
 * checkboxes as booleans, empty optional controls as null, `data-number` controls as numbers, others as text. */
export function readFields(root: ParentNode, attr: FieldSource["attr"], target: Record<string, any>): void {
  root.querySelectorAll<HTMLInputElement | HTMLSelectElement | HTMLTextAreaElement>(`[${attr}]`).forEach(el => {
    const parts = el.getAttribute(attr)!.split(".");
    let node = target;
    for (const part of parts.slice(0, -1)) node = node[part] ??= {};
    node[parts.at(-1)!] = el instanceof HTMLInputElement && el.type === "checkbox" ? el.checked
      : el.value === "" && el.dataset.optional ? null : el.dataset.number ? Number(el.value) : el.value;
  });
}

/** RecipeSpec.input_kind must equal the stage's data kind (capabilities.STAGES); the catalog's value when it is known. */
const STAGE_KINDS: Record<Stage, InputKind> = { pt: "text", sft: "instruction", rm: "preference", ppo: "prompt", dpo: "preference", kto: "kto" };
const INPUT_KINDS: readonly string[] = ["text", "instruction", "preference", "prompt", "kto"];
export function inputKindFor(stage: Stage, stages: RecipeFieldContext["stages"]): InputKind {
  const listed = stages.find(item => item.id === stage)?.dataset_kind;
  return listed && INPUT_KINDS.includes(listed) ? listed as InputKind : STAGE_KINDS[stage];
}
