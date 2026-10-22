// Recipes (10.2): reusable training options kept apart from data, models, GPUs and approvals. List, create, edit,
// clone and "예제에서 만들기". The fields are the training form's own (components/recipe-fields.ts, D17) in recipe mode.
// Saving is explicit (D4); a stale save keeps the draft and offers the D5 choices. Every dynamic text is escaped.
import "./recipes.css";
import { recipes as recipeApi } from "../api";
import { inputKindFor, methodSelect, readFields, sourceField, sourceSelect, stageSelect, trainingOptions,
  type RecipeFieldContext } from "../components/recipe-fields";
import { issueList, issuesAt, localize } from "../issues";
import { button, errorText, escapeHtml, openDialog, restoreFocus, select, statusPill } from "../workbench";
import type { View, ViewContext } from "../view-types";
import type { RecipeRevision, RecipeSpec, RecipeSummary, Sha256, Stage, WorkflowIssue, WorkflowResult } from "../workflow-types";

type Obj = Record<string, any>;
type Stages = RecipeFieldContext["stages"];
interface RecipeDraft { saved: RecipeRevision | null; spec: RecipeSpec; issues: WorkflowIssue[]; conflict: WorkflowIssue[] | null }

const NOT_AVAILABLE = "이 기능은 아직 설치되지 않았습니다.";
// Defaults of config.Hyperparameters, FinetuningOptions, StageOptions (without the reward binding) and EvaluationProfile
// (without its dataset), so every field of the form shows a value that reads back unchanged.
const HYPERPARAMETERS = { precision: "bf16", learning_rate: 0.0001, num_train_epochs: 1, per_device_train_batch_size: 1,
  gradient_accumulation_steps: 4, cutoff_len: 1024, lora_rank: 8, lora_target: "all", seed: 42, max_steps: -1, save_steps: 50,
  save_total_limit: 2 };
const FINETUNING = { lora_alpha: null, lora_dropout: 0, use_rslora: false, use_dora: false, loraplus_lr_ratio: null,
  loraplus_lr_embedding: 0.000001, pissa_init: false, pissa_iter: 16, pissa_convert: false, freeze_trainable_layers: 2,
  freeze_trainable_modules: "all", freeze_extra_modules: null, oft_rank: 0, oft_block_size: 32, oft_target: "all", module_dropout: 0 };
const STAGE_OPTIONS = { pref_loss: "sigmoid", pref_beta: 0.1, pref_ftx: 0, pref_bco_weight: 0, dpo_label_smoothing: 0,
  kto_chosen_weight: 1, kto_rejected_weight: 1, simpo_gamma: 0.5, ppo_buffer_size: 1, ppo_epochs: 4, ppo_score_norm: false,
  ppo_target: 6, ppo_whiten_rewards: false };
const EVALUATION = { kind: "pipeline", thresholds: {}, max_samples: 32, max_new_tokens: 256 };
const REWARD_FIELDS = ["reward_model", "reward_model_type", "reward_model_sha256"];
const METHOD_LABELS: Record<string, string> = { lora: "LoRA", qlora: "QLoRA", full: "Full", freeze: "Freeze", oft: "OFT" };
const KIND_LABELS: Record<string, string> = { text: "텍스트", instruction: "지시·대화", preference: "선호 쌍", prompt: "프롬프트", kto: "선호 라벨" };

const without = (value: unknown, keys: string[]) =>
  Object.fromEntries(Object.entries((value ?? {}) as Obj).filter(([key]) => !keys.includes(key)));
const clone = <T>(value: T): T => JSON.parse(JSON.stringify(value ?? null));

/**
 * A recipe from an example (or a legacy-shaped config): stage, method, quantization, hyperparameters, fine-tuning and
 * stage options without the reward binding, and evaluation defaults without the dataset. Never the AI/workflow IDs,
 * data, base model, revisions, template or resources. Missing options take the engine defaults.
 */
export function recipeFromTemplate(config: Record<string, any>, name: string, recipeId: string, stages: Stages): RecipeSpec {
  const stage = (config.training_stage ?? "sft") as Stage;
  return {
    schema_version: 1, recipe_id: recipeId, version: "1", name, description: "",
    training_backend: "llamafactory", capability_version: "lf-text-v1",
    training_stage: stage, training_method: config.training_method ?? "lora", input_kind: inputKindFor(stage, stages),
    quantization_bit: config.quantization_bit ?? 4,
    hyperparameters: { ...HYPERPARAMETERS, ...clone(config.hyperparameters ?? {}) },
    finetuning_options: { ...FINETUNING, ...clone(config.finetuning_options ?? {}) },
    stage_options: without({ ...STAGE_OPTIONS, ...clone(config.stage_options ?? {}) }, REWARD_FIELDS),
    evaluation_defaults: without({ ...EVALUATION, ...clone(config.evaluation_profile ?? {}) }, ["dataset_id", "dataset_version"]),
  };
}

// -- module state --
const drafts = new Map<string, RecipeDraft>();          // in-memory drafts per recipe (kept when another opens)
let openId: string | null = null;
let list: RecipeSummary[] | null = null;
let listIssues: WorkflowIssue[] = [];
let unavailable = false;
let example = "0";

const canonical = (value: unknown): string => Array.isArray(value) ? `[${value.map(canonical).join(",")}]`
  : value && typeof value === "object" ? `{${Object.keys(value).sort().map(key => JSON.stringify(key) + ":" + canonical((value as Obj)[key])).join(",")}}`
  : JSON.stringify(value) ?? "null";
const dirty = (draft: RecipeDraft) => !draft.saved || canonical(draft.spec) !== canonical(draft.saved.recipe);
const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => localize(issue).text).join("\n") || "요청을 처리하지 못했습니다.";
const newRecipeId = () => crypto.randomUUID().replace(/-/g, "");
const stagesOf = (ctx: ViewContext): Stages => ctx.catalog.training_stages ?? [];
const stageLabel = (ctx: ViewContext, stage: string) => stagesOf(ctx).find(item => item.id === stage)?.label ?? stage.toUpperCase();
const focusEditor = () => document.getElementById("recipe-editor-title")?.focus();
/** A text as a textarea's value holds it: the HTML parser and `.value` turn CRLF and CR into LF. */
const lf = (value: unknown) => String(value ?? "").replace(/\r\n?/g, "\n");
/** A text as a one-line `<input>` holds it: its value sanitization removes every CR and LF. */
const oneLine = (value: unknown) => String(value ?? "").replace(/[\r\n]/g, "");
/** Whether the user is still on the page an action started from (its follow-up may only then move the focus). */
const stillOn = (ctx: ViewContext) => { const page = ctx.page, arg = ctx.arg; return () => ctx.page === page && ctx.arg === arg; };
/** Puts the focus on `key` only when a render lost it (to body) and only on the same page: never across pages. */
function refocus(still: () => boolean, key: string) {
  if (still() && (!document.activeElement || document.activeElement === document.body)) restoreFocus(document, key);
}
const METHODS = ["lora", "qlora", "full", "freeze", "oft"];
const PPO_WITHOUT_OFT = "PPO 학습에서는 OFT를 쓸 수 없습니다. PPO 전용 학습 엔진(LLaMA-Factory 0.9.3)에 OFT가 없으니 LoRA·QLoRA·Full·Freeze 중에서 고르세요.";
const summaryOf = (revision: RecipeRevision): RecipeSummary => ({ recipe_id: revision.recipe.recipe_id, version: revision.recipe.version,
  name: revision.recipe.name, description: revision.recipe.description, training_stage: revision.recipe.training_stage,
  training_method: revision.recipe.training_method, input_kind: revision.recipe.input_kind, revision_sha256: revision.revision_sha256 });
function remember(revision: RecipeRevision) {
  drafts.set(revision.recipe.recipe_id, { saved: clone(revision), spec: clone(revision.recipe), issues: [], conflict: null });
  if (!list) return;
  const summary = summaryOf(revision);
  const index = list.findIndex(item => item.recipe_id === summary.recipe_id);
  list = index < 0 ? [...list, summary].sort((a, b) => a.recipe_id.localeCompare(b.recipe_id)) : list.map((item, at) => at === index ? summary : item);
}

// -- rendering --
function recipeList(ctx: ViewContext) {
  if (list === null && !listIssues.length) return '<div class="empty">레시피 목록을 불러오는 중입니다.</div>';
  if (listIssues.length) return `<div class="wf-banner bad">${unavailable ? NOT_AVAILABLE : "레시피 목록을 불러오지 못했습니다."}</div>${unavailable ? "" : issueList(listIssues)}`;
  if (!list!.length) return '<div class="empty">아직 저장한 레시피가 없습니다. 새 레시피를 만들거나 예제에서 시작하세요.</div>';
  return `<div class="table-scroll"><table><thead><tr><th>레시피</th><th>학습 단계 · 방법</th><th>입력 형식</th><th>저장된 버전</th><th></th></tr></thead><tbody>${list!.map(recipe => `<tr${recipe.recipe_id === openId ? ' class="current"' : ""}>
    <td><b>${escapeHtml(recipe.name)}</b>${recipe.description ? `<small>${escapeHtml(recipe.description)}</small>` : ""}</td>
    <td>${escapeHtml(stageLabel(ctx, recipe.training_stage))} · ${escapeHtml(METHOD_LABELS[recipe.training_method] ?? recipe.training_method)}</td>
    <td>${escapeHtml(KIND_LABELS[recipe.input_kind] ?? recipe.input_kind)}</td><td class="mono">${escapeHtml(recipe.revision_sha256.slice(0, 8))}</td>
    <td class="recipe-row-actions"><button type="button" class="quiet" data-action="${escapeHtml(`wf:recipes:open:${recipe.recipe_id}`)}" aria-label="${escapeHtml(recipe.name)} 열기">열기</button><button type="button" class="quiet" data-action="${escapeHtml(`wf:recipes:clone:${recipe.recipe_id}`)}" aria-label="${escapeHtml(recipe.name)} 복제"${unavailable ? " disabled" : ""}>복제</button></td></tr>`).join("")}</tbody></table></div>`;
}

function exampleCard(ctx: ViewContext) {
  const templates = ctx.catalog.templates ?? [];
  return `<section class="card recipe-example" aria-labelledby="recipe-example-title"><div class="section-title"><h3 id="recipe-example-title">예제에서 만들기</h3></div>
    <p>예제의 학습 단계·방법·옵션만 가져와 새 레시피 초안을 만듭니다. 데이터, 기반 모델, GPU·Pod와 승인은 가져오지 않습니다.</p>
    ${templates.length ? `${select("예제", "recipe_example", example, templates.map((template, index) => [String(index), template.name])).replace("<select ", ctx.busy ? "<select disabled " : "<select ")}<div class="actions">${button("예제에서 만들기", "wf:recipes:from-example", "", unavailable)}</div>` : '<div class="empty">불러올 예제가 없습니다.</div>'}</section>`;
}

function editor(ctx: ViewContext, recipeId: string, draft: RecipeDraft) {
  const placed = new Set<WorkflowIssue>();
  const decorate = (path: string, html: string) => {
    const found = issuesAt(draft.issues, "/spec/" + path.split(".").join("/"));
    if (!found.length) return `<div class="recipe-field">${html}</div>`;
    found.forEach(issue => placed.add(issue));
    const id = `recipe-issue-${path.replace(/[^a-z0-9_]/gi, "-")}`;
    return `<div class="recipe-field invalid">${html.replace(/<(input|select|textarea) /, `<$1 aria-invalid="true" aria-describedby="${id}" `)}<div id="${id}">${issueList(found)}</div></div>`;
  };
  const fields: RecipeFieldContext = { attr: "data-recipe", value: path => path.split(".").reduce<any>((node, part) => node?.[part], draft.spec),
    stages: stagesOf(ctx), recipeMode: true, decorate,
    capabilities: draft.spec.training_stage === "ppo" ? { methods: METHODS.filter(method => method !== "oft") } : null };
  const f = (label: string, path: string, type = "text", help = "") => sourceField(fields, label, path, type, help);
  const choice = (label: string, path: string, options: [string, string][]) => sourceSelect(fields, label, path, options);
  const description = decorate("description", `<label class="field"><span>설명</span><textarea id="recipe-description" aria-label="설명" data-recipe="description" rows="2" maxlength="2000">\n${escapeHtml(draft.spec.description)}</textarea></label>`);
  const body = `<div class="grid">${f("레시피 이름", "name").replace("<input ", '<input id="recipe-name" maxlength="200" aria-required="true" ')}${description}</div>
    <h4>학습 방식</h4><div class="grid">${stageSelect(fields)}${methodSelect(fields)}
    ${f("학습 횟수 (epoch)", "hyperparameters.num_train_epochs", "number")}${f("학습률", "hyperparameters.learning_rate", "number")}
    ${f("배치 크기", "hyperparameters.per_device_train_batch_size", "number")}${f("최대 토큰 길이", "hyperparameters.cutoff_len", "number")}
    ${choice("정밀도", "hyperparameters.precision", [["bf16", "BF16 · 권장"], ["fp16", "FP16"]])}${f("최대 step (-1은 epoch 기준)", "hyperparameters.max_steps", "number")}
    ${f("기울기 누적", "hyperparameters.gradient_accumulation_steps", "number")}${f("무작위 시드", "hyperparameters.seed", "number")}
    ${f("체크포인트 저장 간격 (step)", "hyperparameters.save_steps", "number")}${f("체크포인트 보관 수", "hyperparameters.save_total_limit", "number")}</div>
    ${trainingOptions(fields)}
    <section class="card"><h3>평가 기본값</h3><div class="grid">${choice("평가 방식", "evaluation_defaults.kind", [["pipeline", "학습 완료 확인"], ["json", "JSON 형식"], ["classification", "분류"], ["extraction", "정보 추출"], ["kbf_extraction", "KBF 추출"], ["reward", "보상 점수 · RM"]])}
    ${f("검증 최대 건수", "evaluation_defaults.max_samples", "number")}${f("답변 최대 토큰", "evaluation_defaults.max_new_tokens", "number")}</div></section>`;
  const rest = draft.issues.filter(issue => !placed.has(issue));
  const revision = draft.saved?.revision_sha256;
  const state = !draft.saved ? statusPill("neutral", "아직 저장하지 않음") : dirty(draft) ? statusPill("warn", "저장 안 된 변경") : statusPill("neutral", `저장된 버전 ${revision!.slice(0, 8)}`);
  const conflict = draft.conflict ? `<div class="wf-banner warn recipe-banner" role="status"><div>${draft.conflict.some(issue => issue.code === "revision_conflict")
    ? escapeHtml(localize(draft.conflict.find(issue => issue.code === "revision_conflict")!).text) : issueList(draft.conflict)}<small>내 초안은 그대로 남아 있습니다.</small></div>
    ${draft.conflict.some(issue => issue.code === "revision_conflict") ? `<div class="actions">${button("최신 버전 불러오기", "wf:recipes:reload")}${button("내 변경으로 덮어쓰기", "wf:recipes:overwrite")}</div>` : ""}</div>` : "";
  return `<form id="recipe-form" class="card recipe-editor" data-recipe-id="${escapeHtml(recipeId)}" novalidate aria-labelledby="recipe-editor-title">
    <div class="section-title"><h3 id="recipe-editor-title" tabindex="-1">${draft.saved ? escapeHtml(draft.saved.recipe.name) : "새 레시피"}</h3>${state}</div>
    ${conflict}${rest.length ? `<div class="recipe-summary" role="status">${issueList(rest, { heading: "저장하지 못한 이유" })}</div>` : ""}
    <fieldset class="recipe-fieldset"${ctx.busy ? " disabled" : ""}>${body}</fieldset>
    <p class="muted recipe-note">레시피에는 데이터, 기반 모델, GPU·Pod, 승인 정보가 들어가지 않습니다. 학습 그래프의 단계에서 데이터와 실행 위치를 따로 연결합니다.</p>
    <div class="actions"><button type="submit" class="primary" data-focus-key="recipe-save"${unavailable ? " disabled" : ""}>저장</button>${draft.saved ? button("복제", `wf:recipes:clone:${recipeId}`, "", unavailable) : ""}${button("닫기", "wf:recipes:close", "quiet")}</div></form>`;
}

// -- actions --
async function loadList(ctx: ViewContext, open: string | null) {
  await ctx.task("레시피 목록 불러오는 중", async () => {
    const result = await recipeApi.list();
    if (result.status === "ok" && result.data) { list = result.data.recipes; listIssues = []; }
    else {
      if (notAvailable(result)) unavailable = true;
      listIssues = result.issues.length ? result.issues : [{ code: "refused", path: "", message: "레시피 목록을 불러오지 못했습니다." }];
    }
    if (open) await read(open);
  });
}

/** Opens a recipe: a draft with unsaved changes is kept as it is, otherwise the current revision is read. */
async function read(recipeId: string) {
  const kept = drafts.get(recipeId);
  if (kept && dirty(kept)) { openId = recipeId; return; }
  const result = await recipeApi.get(recipeId);
  if (result.status !== "ok" || !result.data) {
    if (notAvailable(result)) unavailable = true;
    throw new Error(describe(result.issues));
  }
  remember(result.data);
  openId = recipeId;
}

function startDraft(ctx: ViewContext, config: Record<string, any>, name: string) {
  const spec = recipeFromTemplate(config, name, newRecipeId(), stagesOf(ctx));
  drafts.set(spec.recipe_id, { saved: null, spec, issues: [], conflict: null });
  openId = spec.recipe_id;
  ctx.render(); focusEditor();
}

async function cloneRecipe(ctx: ViewContext, recipeId: string) {
  const saved = drafts.get(recipeId)?.saved;
  const listed = list?.find(item => item.recipe_id === recipeId);
  const revision = saved?.revision_sha256 ?? listed?.revision_sha256;
  const base = saved?.recipe.name ?? listed?.name;
  if (!revision || base === undefined) return;
  const name = `${base.slice(0, 196)} 복사본`;
  const still = stillOn(ctx);
  let cloned = false;
  await ctx.task("레시피 복제 중", async () => {
    const result = await recipeApi.clone({ recipe_id: recipeId, revision, name });
    if (result.status !== "ok" || !result.data) {
      if (notAvailable(result)) unavailable = true;
      throw new Error(describe(result.issues));
    }
    remember(result.data);
    openId = result.data.recipe.recipe_id; cloned = true;
    ctx.notify("레시피를 복제했습니다. 원본은 바뀌지 않습니다.");
  });
  if (cloned && still()) focusEditor();
}

/** Saves `spec` against `expected`; a replaced revision invalidates held plans that pinned it (D8). */
async function saveAgainst(ctx: ViewContext, draft: RecipeDraft, expected: Sha256 | null) {
  const sent = clone(draft.spec);
  const result = await recipeApi.save(sent, expected);
  draft.issues = []; draft.conflict = null;
  if (result.status === "ok" && result.data) {
    const previous = draft.saved?.revision_sha256 ?? null;
    remember(result.data);
    for (const replaced of new Set([previous, expected])) if (replaced && replaced !== result.data.revision_sha256) ctx.store.markRecipeChanged(replaced);
    ctx.notify("레시피를 저장했습니다.");
    return;
  }
  if (notAvailable(result)) unavailable = true;
  if (result.status === "conflict") draft.conflict = result.issues;
  else draft.issues = result.issues;
}

async function saveRecipe(ctx: ViewContext) {
  captureForm(ctx);
  const draft = openId ? drafts.get(openId) : undefined;
  if (!draft) return;
  if (!draft.spec.name.trim()) {
    draft.issues = [{ code: "required", path: "/spec/name", message: "레시피 이름을 입력하세요." }];
    ctx.render(); document.getElementById("recipe-name")?.focus(); return;
  }
  if (draft.spec.training_stage === "ppo" && draft.spec.training_method === "oft") {
    draft.issues = [{ code: "invalid_value", path: "/spec/training_method", message: PPO_WITHOUT_OFT }];
    ctx.render(); document.querySelector<HTMLElement>('#recipe-form [data-recipe="training_method"]')?.focus(); return;
  }
  const still = stillOn(ctx);
  await ctx.task("레시피 저장 중", () => saveAgainst(ctx, draft, draft.saved?.revision_sha256 ?? null));
  // The shell gives the focus back to the control that started the save; this only covers a control that is gone.
  refocus(still, "recipe-save");
}

async function reloadRecipe(ctx: ViewContext) {
  const draft = openId ? drafts.get(openId) : undefined;
  if (!openId || !draft) return;
  const recipeId = openId;
  const answer = await openDialog({ title: "최신 버전 불러오기",
    bodyHtml: "<p>다른 곳에서 저장한 최신 버전으로 바꿉니다. 저장하지 않은 내 변경은 사라집니다.</p>",
    actions: [{ id: "back", label: "돌아가기" }, { id: "reload", label: "불러오기", primary: true }] });
  if (answer?.action !== "reload") return;
  const still = stillOn(ctx);
  await ctx.task("최신 버전 불러오는 중", async () => {
    const previous = draft.saved?.revision_sha256;
    const result = await recipeApi.get(recipeId);
    if (result.status !== "ok" || !result.data) throw new Error(describe(result.issues));
    remember(result.data);
    if (previous && previous !== result.data.revision_sha256) ctx.store.markRecipeChanged(previous);
    ctx.notify("최신 버전을 불러왔습니다.");
  });
  refocus(still, "recipe-save");
}

async function overwriteRecipe(ctx: ViewContext) {
  const draft = openId ? drafts.get(openId) : undefined;
  if (!openId || !draft) return;
  const recipeId = openId;
  const answer = await openDialog({ title: "내 변경으로 덮어쓰기",
    bodyHtml: "<p>다른 곳에서 먼저 저장한 변경을 지금 내 초안으로 덮어씁니다. 그 변경은 최신 버전에서 빠지고 이전 버전 기록으로만 남습니다.</p>",
    actions: [{ id: "back", label: "돌아가기" }, { id: "overwrite", label: "덮어쓰기", primary: true }] });
  if (answer?.action !== "overwrite") return;
  const still = stillOn(ctx);
  await ctx.task("내 변경으로 저장 중", async () => {
    const latest = await recipeApi.get(recipeId);
    if (latest.status !== "ok" || !latest.data) throw new Error(describe(latest.issues));
    await saveAgainst(ctx, draft, latest.data.revision_sha256);
  });
  refocus(still, "recipe-save");
}

/** Reads the open editor (and the example choice) into the module state; the stage decides the input kind. Controls
 * locked while busy were not edited and are not read back (the running task's result wins). */
function captureForm(ctx: ViewContext) {
  const chosen = document.querySelector<HTMLSelectElement>('[data-key="recipe_example"]');
  if (chosen && !chosen.disabled) example = chosen.value;
  const form = document.querySelector<HTMLFormElement>("#recipe-form");
  const draft = form?.dataset.recipeId ? drafts.get(form.dataset.recipeId) : undefined;
  if (!form || !draft || form.querySelector("fieldset:disabled")) return;
  const { description, name } = draft.spec;
  readFields(form, "data-recipe", draft.spec);
  // A description that differs only in line endings (CRLF from another writer) is the draft's or, after an edit typed
  // back, the saved one; so is a name that differs only in line breaks, which its one-line input cannot hold.
  const saved = draft.saved?.recipe;
  if (lf(draft.spec.description) === lf(description)) draft.spec.description = description;
  else if (saved && lf(draft.spec.description) === lf(saved.description)) draft.spec.description = saved.description;
  if (oneLine(draft.spec.name) === oneLine(name)) draft.spec.name = name;
  else if (saved && oneLine(draft.spec.name) === oneLine(saved.name)) draft.spec.name = saved.name;
  draft.spec.input_kind = inputKindFor(draft.spec.training_stage, stagesOf(ctx));
}

export const view: View = {
  id: "recipes", title: "레시피", description: "학습 단계·방법·옵션을 데이터와 GPU에서 떼어 저장하고 다시 씁니다.",
  nav: { group: "workspace", label: "레시피", icon: "recipes", order: 40 },
  render(ctx) {
    const draft = openId ? drafts.get(openId) : undefined;
    return `<div class="recipes-top"><section class="card recipe-list" aria-labelledby="recipe-list-title"><div class="section-title"><h3 id="recipe-list-title">저장한 레시피 ${list ? `<span class="count">${list.length}</span>` : ""}</h3><div class="actions">${button("새 레시피", "wf:recipes:new", "primary", unavailable)}</div></div>${recipeList(ctx)}</section>
      ${exampleCard(ctx)}</div>${openId && draft ? editor(ctx, openId, draft) : ""}`;
  },
  bind(ctx) {
    if (ctx.page !== "recipes") return;
    document.querySelector<HTMLFormElement>("#recipe-form")?.addEventListener("submit", event => {
      event.preventDefault();
      if (!ctx.busy) void saveRecipe(ctx).catch(error => ctx.fail(errorText(error)));
    });
    // A stage, method or loss choice changes which option fields exist: re-render and keep the focus on the choice.
    for (const path of ["training_stage", "training_method", "stage_options.pref_loss"])
      document.querySelector<HTMLSelectElement>(`#recipe-form [data-recipe="${path}"]`)?.addEventListener("change", () => {
        captureForm(ctx); ctx.render();
        document.querySelector<HTMLSelectElement>(`#recipe-form [data-recipe="${path}"]`)?.focus();
      });
  },
  capture: captureForm,
  async enter(ctx) {
    const still = stillOn(ctx);
    await loadList(ctx, ctx.arg);
    if (ctx.arg && openId === ctx.arg && still()) focusEditor();
  },
  async action(ctx, verb, arg) {
    if (verb === "new") startDraft(ctx, {}, "");
    else if (verb === "from-example") {
      const template = (ctx.catalog.templates ?? [])[Number(example)];
      if (template) startDraft(ctx, template.config, template.name);
    }
    else if (verb === "open" && arg) {
      const still = stillOn(ctx);
      await ctx.task("레시피 불러오는 중", () => read(arg));
      if (openId === arg && still()) focusEditor();
    }
    else if (verb === "clone" && arg) await cloneRecipe(ctx, arg);
    else if (verb === "save") await saveRecipe(ctx);
    else if (verb === "reload") await reloadRecipe(ctx);
    else if (verb === "overwrite") await overwriteRecipe(ctx);
    else if (verb === "close") { openId = null; ctx.render(); document.querySelector<HTMLElement>('[data-action="wf:recipes:new"]')?.focus(); }
  },
};
