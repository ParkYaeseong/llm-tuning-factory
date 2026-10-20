import { test, expect, type Page } from "@playwright/test";
import { calls, config, HOSTILE, invalid, issue, openWorkbench } from "./workflow-mock.ts";

// Every [data-config] control of the training form as "path|control|number flag|optional flag|visible label".
const trainingFields = (page: Page) => page.locator("#training-form [data-config]").evaluateAll(elements => elements.map(element => {
  const control = element as HTMLInputElement | HTMLSelectElement;
  const type = control instanceof HTMLSelectElement ? "select" : control.type;
  const label = type === "checkbox" ? control.closest("label")?.textContent : control.closest("label")?.querySelector("span")?.textContent;
  return [control.dataset.config, type, control.dataset.number ? "number" : "", control.dataset.optional ? "optional" : "", label?.trim()].join("|");
}));

const FORM_HEAD = [
  "ai_id|text|||업무 AI ID", "workflow_id|text|||업무 흐름 ID", "evaluation_profile.kind|select|||평가 방식",
  "base_model|select|||기반 모델", "training_stage|select|||학습 단계", "training_method|select|||학습 방법",
  "hyperparameters.num_train_epochs|number|number||학습 횟수 (epoch)", "hyperparameters.learning_rate|number|number||학습률",
  "hyperparameters.per_device_train_batch_size|number|number||배치 크기", "hyperparameters.cutoff_len|number|number||최대 토큰 길이",
  "model_revision|text|||모델 revision · 40자리 commit", "tokenizer_revision|text|||토크나이저 revision · 모델과 일치",
  "template|text|||모델 템플릿 · 모델에서 자동 적용", "hyperparameters.precision|select|||정밀도",
  "hyperparameters.max_steps|number|number||최대 step (-1은 epoch 기준)", "hyperparameters.gradient_accumulation_steps|number|number||기울기 누적",
  "hyperparameters.seed|number|number||무작위 시드", "hyperparameters.save_steps|number|number||체크포인트 저장 간격 (step)",
  "hyperparameters.save_total_limit|number|number||체크포인트 보관 수", "evaluation_profile.max_samples|number|number||검증 최대 건수",
  "evaluation_profile.max_new_tokens|number|number||답변 최대 토큰",
];
const FORM_RESOURCES = [
  "resource_profile.gpu_type|text|||GPU 종류 · 연결 설정에서 가져오기", "resource_profile.gpu_count|number|number||GPU 수",
  "resource_profile.hourly_cost_usd|number|number||GPU 한 개당 시간 요금 (USD)", "resource_profile.timeout_seconds|number|number||최대 실행 시간 (초)",
  "resource_profile.max_cost_usd|number|number||최대 예상 GPU 비용 (USD)",
];
const LORA = [
  "hyperparameters.lora_rank|number|number||LoRA rank", "hyperparameters.lora_target|select|||LoRA 대상",
  "finetuning_options.lora_alpha|number|number|optional|LoRA alpha · 비우면 자동", "finetuning_options.lora_dropout|number|number||LoRA dropout",
  "finetuning_options.loraplus_lr_ratio|number|number|optional|LoRA+ 학습률 비율 · 선택",
  "finetuning_options.loraplus_lr_embedding|number|number||LoRA+ embedding 학습률", "finetuning_options.pissa_iter|number|number||PiSSA 초기화 반복 수",
];
const LORA_CHECKS = [
  "finetuning_options.use_rslora|checkbox|||rsLoRA 사용", "finetuning_options.use_dora|checkbox|||DoRA 사용",
  "finetuning_options.pissa_init|checkbox|||PiSSA 초기화", "finetuning_options.pissa_convert|checkbox|||PiSSA 변환 저장",
];
const TRAINING_FORM: [string, string, string, string[]][] = [
  ["sft", "lora", "", [...LORA, ...LORA_CHECKS]],
  ["dpo", "freeze", "simpo", [
    "stage_options.pref_loss|select|||선호 학습 loss", "stage_options.pref_beta|number|number||선호 학습 beta",
    "stage_options.pref_ftx|number|number||SFT 보조 loss 비중", "stage_options.dpo_label_smoothing|number|number||DPO label smoothing",
    "stage_options.pref_bco_weight|number|number||BCO 가중치", "stage_options.simpo_gamma|number|number||SimPO gamma",
    "finetuning_options.freeze_trainable_layers|number|number||학습할 레이어 수", "finetuning_options.freeze_trainable_modules|select|||학습할 모듈",
    "finetuning_options.freeze_extra_modules|select||optional|추가 학습 모듈 · 선택"]],
  ["kto", "qlora", "", [
    "stage_options.pref_beta|number|number||선호 학습 beta", "stage_options.kto_chosen_weight|number|number||Chosen 가중치",
    "stage_options.kto_rejected_weight|number|number||Rejected 가중치", ...LORA, "quantization_bit|select|number||양자화 비트", ...LORA_CHECKS]],
  ["ppo", "oft", "", [
    "stage_options.reward_model|text||optional|보상 모델 원격 경로", "stage_options.reward_model_type|select|||보상 모델 종류",
    "stage_options.reward_model_sha256|text||optional|보상 모델 SHA256", "stage_options.ppo_buffer_size|number|number||PPO buffer size",
    "stage_options.ppo_epochs|number|number||PPO epoch", "stage_options.ppo_target|number|number||PPO KL target",
    "stage_options.ppo_score_norm|checkbox|||PPO score normalization", "stage_options.ppo_whiten_rewards|checkbox|||PPO reward whitening",
    "finetuning_options.oft_rank|number|number||OFT rank", "finetuning_options.oft_block_size|number|number||OFT block size",
    "finetuning_options.oft_target|select|||OFT 대상", "finetuning_options.module_dropout|number|number||OFT module dropout"]],
];

// Characterization test (D17): written first and passing on the code before the recipe-field extraction; the
// extraction must keep the legacy training form's controls, their order, flags and labels exactly as they were.
test("training form keeps its fields after the recipe field extraction", async ({ page }) => {
  await openWorkbench(page);
  await page.locator('nav [data-action="nav:training"]').click();
  for (const [stage, method, prefLoss, options] of TRAINING_FORM) {
    await page.getByLabel("학습 단계", { exact: true }).selectOption(stage);
    await page.getByLabel("학습 방법", { exact: true }).selectOption(method);
    if (prefLoss) await page.getByLabel("선호 학습 loss", { exact: true }).selectOption(prefLoss);
    await expect.poll(() => trainingFields(page), { message: `${stage}/${method}` }).toEqual([...FORM_HEAD, ...options, ...FORM_RESOURCES]);
  }
  await expect(page.locator("img")).toHaveCount(0);
});

// Keys a recipe must never carry: data, model, Pod/resources, approvals, credentials or PPO reward bindings.
const FORBIDDEN_KEYS = ["dataset_id", "dataset_version", "pod_id", "resource_profile", "approved", "RUNPOD_API_KEY",
  "reward_model", "reward_model_type", "reward_model_sha256", "ai_id", "workflow_id", "base_model"];
function forbiddenKeys(value: unknown, path = ""): string[] {
  if (Array.isArray(value)) return value.flatMap((item, index) => forbiddenKeys(item, `${path}/${index}`));
  if (!value || typeof value !== "object") return [];
  return Object.entries(value).flatMap(([key, item]) =>
    [...(FORBIDDEN_KEYS.includes(key) ? [`${path}/${key}`] : []), ...forbiddenKeys(item, `${path}/${key}`)]);
}
const HASH = /^[0-9a-f]{64}$/;
const recipeForm = (page: Page) => page.locator("#recipe-form");
// A direct bridge call, as another window of the app would make it (the stateful definitions fake answers it).
const bridge = (page: Page, action: string, data: object) =>
  page.evaluate(([action, data]) => (window as any).__TAURI_INTERNALS__.invoke("workflow_action", { action, data }), [action, data] as const);

async function openRecipes(page: Page) {
  await page.locator('nav [data-action="nav:recipes"]').click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("레시피");
  await expect(page.locator(".notice.busy")).toHaveCount(0);
}
async function newRecipe(page: Page, name: string) {
  await page.getByRole("button", { name: "새 레시피", exact: true }).click();
  const form = recipeForm(page);
  await expect(form.getByRole("heading", { name: "새 레시피" })).toBeFocused();
  await form.getByLabel("레시피 이름").fill(name);
  return form;
}
async function saveRecipe(page: Page) {
  await recipeForm(page).getByRole("button", { name: "저장", exact: true }).click();
  await expect(page.locator(".notice.busy")).toHaveCount(0);
}

test("a recipe stores stage, method and options without data, Pod or approval", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await openRecipes(page);
  expect(await calls(page, { action: "recipes_list" })).toHaveLength(1);
  await expect(page.locator(".recipe-list")).toContainText("아직 저장한 레시피가 없습니다");
  const form = await newRecipe(page, `${HOSTILE} DPO 비교`);
  // Recipe mode shares the training fields but never offers the PPO reward-model binding (D17).
  await form.getByLabel("학습 단계", { exact: true }).selectOption("ppo");
  await expect(form.getByLabel("PPO buffer size")).toBeVisible();
  await expect(form.getByLabel("학습 단계", { exact: true })).toBeFocused();
  await expect(form.locator('[data-recipe^="stage_options.reward_model"]')).toHaveCount(0);
  await expect(form.locator("[data-config]")).toHaveCount(0);
  await form.getByLabel("학습 단계", { exact: true }).selectOption("dpo");
  await form.getByLabel("학습 방법", { exact: true }).selectOption("lora");
  await form.getByLabel("선호 학습 loss", { exact: true }).selectOption("simpo");
  await expect(form.getByLabel("SimPO gamma")).toBeVisible();
  await saveRecipe(page);
  const saves = await calls(page, { action: "recipe_save" });
  expect(saves).toHaveLength(1);
  expect(Object.keys(saves[0].args.data).sort()).toEqual(["expected_revision", "spec"]);
  expect(saves[0].args.data.expected_revision).toBeNull();
  const spec = saves[0].args.data.spec;
  expect(spec).toMatchObject({ schema_version: 1, name: `${HOSTILE} DPO 비교`, training_stage: "dpo", training_method: "lora",
    input_kind: "preference", training_backend: "llamafactory", capability_version: "lf-text-v1", stage_options: { pref_loss: "simpo" } });
  expect(spec.recipe_id).toMatch(/^[a-zA-Z0-9_-][a-zA-Z0-9_.-]*$/);
  expect(forbiddenKeys(saves[0].args.data)).toEqual([]);
  await expect(page.getByText("레시피를 저장했습니다.")).toBeVisible();
  const row = page.locator(".recipe-list tbody tr");
  await expect(row).toHaveCount(1);
  await expect(row).toContainText(`${HOSTILE} DPO 비교`);
  await expect(row).toContainText("DPO");
  await expect(form.locator("#recipe-editor-title")).toHaveText(`${HOSTILE} DPO 비교`);
  await expect(page.locator("img")).toHaveCount(0);
});

test("cloning copies only the recipe and leaves the original unchanged", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await openRecipes(page);
  const form = await newRecipe(page, "원본 레시피");
  await form.getByLabel("학습률", { exact: true }).fill("0.0002");
  await saveRecipe(page);
  const [first] = await calls(page, { action: "recipe_save" });
  const originalId = first.args.data.spec.recipe_id;
  const original = await bridge(page, "recipe_get", { recipe_id: originalId });

  await page.locator(".recipe-list tbody tr", { hasText: "원본 레시피" }).getByRole("button", { name: /복제/ }).click();
  await expect(page.locator(".notice.busy")).toHaveCount(0);
  const clones = await calls(page, { action: "recipe_clone" });
  expect(clones.map(call => call.args.data)).toEqual([{ recipe_id: originalId, revision: original.data.revision_sha256, name: "원본 레시피 복사본" }]);
  await expect(form.getByLabel("레시피 이름")).toHaveValue("원본 레시피 복사본");
  await expect(form.getByLabel("학습률", { exact: true })).toHaveValue("0.0002");
  await expect(page.locator(".recipe-list tbody tr")).toHaveCount(2);

  await form.getByLabel("학습률", { exact: true }).fill("0.0005");
  await saveRecipe(page);
  const saves = await calls(page, { action: "recipe_save" });
  const listed = await bridge(page, "recipes_list", {});
  const cloneId = listed.data.recipes.map((recipe: any) => recipe.recipe_id).find((id: string) => id !== originalId);
  expect(saves.at(-1).args.data.spec).toMatchObject({ recipe_id: cloneId, name: "원본 레시피 복사본", hyperparameters: { learning_rate: 0.0005 } });
  // The mock's CAS accepted it, so this is the clone's own revision, not the original's.
  expect(saves.at(-1).args.data.expected_revision).toMatch(HASH);
  expect(saves.at(-1).args.data.expected_revision).not.toBe(original.data.revision_sha256);
  expect(forbiddenKeys(saves.at(-1).args.data)).toEqual([]);
  await expect(page.locator(".wf-banner")).toHaveCount(0);

  await page.locator(".recipe-list tbody tr").filter({ hasNotText: "복사본" }).getByRole("button", { name: /열기/ }).click();
  await expect(page.locator(".notice.busy")).toHaveCount(0);
  expect((await calls(page, { action: "recipe_get" })).filter(call => call.args.data.recipe_id === originalId).length).toBeGreaterThan(1);
  await expect(form.getByLabel("레시피 이름")).toHaveValue("원본 레시피");
  await expect(form.getByLabel("학습률", { exact: true })).toHaveValue("0.0002");
  const after = await bridge(page, "recipe_get", { recipe_id: originalId });
  expect(after.data).toEqual(original.data);
});

test("an example becomes a recipe without its dataset or resources", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await openRecipes(page);
  await page.getByLabel("예제", { exact: true }).selectOption({ label: "paper_extraction" });
  await page.getByRole("button", { name: "예제에서 만들기", exact: true }).click();
  const form = recipeForm(page);
  await expect(form.getByRole("heading", { name: "새 레시피" })).toBeFocused();
  await expect(form.getByLabel("레시피 이름")).toHaveValue("paper_extraction");
  await saveRecipe(page);
  const [save] = await calls(page, { action: "recipe_save" });
  const spec = save.args.data.spec;
  const catalog = await page.evaluate(() => (window as any).__TAURI_INTERNALS__.invoke("factory_action", { action: "catalog", data: {} }));
  const expected = await page.evaluate(async ({ config, name, id, stages }) =>
    (await import("/src/views/recipes.ts" as string)).recipeFromTemplate(config, name, id, stages),
  { config, name: spec.name, id: spec.recipe_id, stages: catalog.training_stages });
  expect(spec).toEqual(expected);
  // The example's own training options arrive unchanged; nothing else of the example does.
  expect(spec.hyperparameters).toEqual(config.hyperparameters);
  expect(spec).toMatchObject({ training_stage: "sft", training_method: "lora", input_kind: "instruction", quantization_bit: 4,
    evaluation_defaults: { kind: "pipeline", thresholds: {}, max_samples: 32, max_new_tokens: 256 } });
  expect(forbiddenKeys(spec)).toEqual([]);
  expect(spec).not.toHaveProperty("template");
  expect(spec).not.toHaveProperty("model_revision");
  expect(spec).not.toHaveProperty("evaluation_profile");
  expect(await calls(page, { action: "project_create" })).toEqual([]);
});

test("recipe validation issues point at their fields", async ({ page }) => {
  await openWorkbench(page, { definitions: true, workflow: { recipe_save: invalid([
    issue("invalid_value", `Input should be greater than 0 ${HOSTILE}`, "/spec/stage_options/pref_beta"),
    issue("invalid_value", "input kind does not match training stage", "/spec")]) } });
  await openRecipes(page);
  const form = await newRecipe(page, "검사 레시피");
  await form.getByLabel("학습 단계", { exact: true }).selectOption("dpo");
  await form.getByLabel("선호 학습 beta").fill("0");
  await saveRecipe(page);
  const beta = form.locator(".recipe-field", { has: page.getByLabel("선호 학습 beta") });
  await expect(beta.locator(".issue-list")).toContainText("입력값이 올바르지 않습니다.");
  await beta.getByText("원문 보기").click();
  await expect(beta.locator(".issue-original")).toHaveText(`Input should be greater than 0 ${HOSTILE}`);
  await expect(form.getByLabel("선호 학습 beta")).toHaveAttribute("aria-invalid", "true");
  const summary = form.locator(".recipe-summary");
  await expect(summary).toContainText("입력 형식이 학습 단계와 맞지 않습니다.");
  await expect(summary).not.toContainText("입력값이 올바르지 않습니다.");
  await summary.getByText("원문 보기").click();
  await expect(summary.locator(".issue-original")).toHaveText("input kind does not match training stage");
  // The draft is kept as typed.
  await expect(form.getByLabel("선호 학습 beta")).toHaveValue("0");
  await expect(form.getByLabel("레시피 이름")).toHaveValue("검사 레시피");
  await expect(page.locator("img")).toHaveCount(0);
});

test("a recipe edit that conflicts keeps the draft", async ({ page }) => {
  await openWorkbench(page, { definitions: true });
  await openRecipes(page);
  const form = await newRecipe(page, "공유 레시피");
  await form.getByLabel("학습률", { exact: true }).fill("0.0002");
  await saveRecipe(page);
  // Another window saves a newer revision of the same recipe.
  const [{ args }] = await calls(page, { action: "recipe_save" });
  const current = await bridge(page, "recipe_get", { recipe_id: args.data.spec.recipe_id });
  const newer = { ...current.data.recipe, description: "다른 창에서 저장", hyperparameters: { ...current.data.recipe.hyperparameters, learning_rate: 0.0009 } };
  expect((await bridge(page, "recipe_save", { spec: newer, expected_revision: current.data.revision_sha256 })).status).toBe("ok");

  await form.getByLabel("학습률", { exact: true }).fill("0.0003");
  await saveRecipe(page);
  const banner = page.locator(".wf-banner");
  await expect(banner).toContainText("다른 곳에서 먼저 저장한 최신 버전이 있습니다.");
  await expect(form.getByLabel("학습률", { exact: true })).toHaveValue("0.0003");
  await expect(form.getByLabel("레시피 이름")).toHaveValue("공유 레시피");
  const reads = (await calls(page, { action: "recipe_get" })).length;
  await banner.getByRole("button", { name: "최신 버전 불러오기" }).click();
  const dialog = page.getByRole("dialog", { name: "최신 버전 불러오기" });
  await expect(dialog).toBeVisible();
  await dialog.getByRole("button", { name: "돌아가기" }).click();
  await expect(form.getByLabel("학습률", { exact: true })).toHaveValue("0.0003");
  expect(await calls(page, { action: "recipe_get" })).toHaveLength(reads);
  await banner.getByRole("button", { name: "최신 버전 불러오기" }).click();
  await dialog.getByRole("button", { name: "불러오기", exact: true }).click();
  await expect(page.locator(".notice.busy")).toHaveCount(0);
  await expect(form.getByLabel("학습률", { exact: true })).toHaveValue("0.0009");
  await expect(form.getByLabel("설명")).toHaveValue("다른 창에서 저장");
  await expect(page.locator(".wf-banner")).toHaveCount(0);
  expect(await calls(page, { action: "recipe_get" })).toHaveLength(reads + 1);
});
