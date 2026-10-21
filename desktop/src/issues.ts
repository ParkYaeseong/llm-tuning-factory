// Issue routing (field path, card, row, plan section) and Korean texts for known codes and English domain texts
// (D12). Pure. The original text is always kept and shown under "원문 보기".
import { KIND_LABELS } from "./graph-model";
import type { WorkflowIssue } from "./workflow-types";
import { escapeHtml } from "./workbench";

export type PlanSection = "project" | "profile" | "estimate" | "tracking" | "node_resources" | "datasets" | "approval" | "graph" | "other";
export interface Localized { text: string; original: string | null }

/** Issues at an RFC 6901 pointer or below it (`/spec/name` matches `/spec/name/0`, not `/spec/names`). */
export function issuesAt(issues: WorkflowIssue[], pointerPrefix: string): WorkflowIssue[] {
  return issues.filter(issue => issue.path === pointerPrefix || issue.path.startsWith(pointerPrefix + "/"));
}

const NODE_PATH = /^\/graph\/nodes\/(\d+)(?:\/|$)/;

/** Issues of one graph card: by `node_id`, or by `/graph/nodes/<i>[/…]` where `indexToId[i]` is the card. */
export function nodeIssues(issues: WorkflowIssue[], nodeId: string, indexToId: string[]): WorkflowIssue[] {
  return issues.filter(issue => {
    if (issue.node_id) return issue.node_id === nodeId;
    const match = NODE_PATH.exec(issue.path);
    return match !== null && indexToId[Number(match[1])] === nodeId;
  });
}

/** Import issues with their 1-based source row (CSV header excluded); null for file-level issues. */
export function rowIssues(issues: WorkflowIssue[]): { row: number | null; issue: WorkflowIssue }[] {
  return issues.map(issue => ({ row: typeof issue.row_number === "number" ? issue.row_number : null, issue }));
}

const unescapePointer = (part: string) => part.replace(/~1/g, "/").replace(/~0/g, "~");

/** Where a planning or submit issue belongs on the plan panel. */
export function planSection(issue: WorkflowIssue): { section: PlanSection; key?: string } {
  const [first = "", second] = issue.path.split("/").slice(1);
  if (["project", "project_id", "project_revision"].includes(first)) return { section: "project" };
  if (first === "profiles" && second !== undefined) return { section: "profile", key: unescapePointer(second) };
  if (first === "estimates" && second !== undefined) return { section: "estimate", key: unescapePointer(second) };
  if (first === "tracking") return { section: "tracking" };
  if (first === "node_resources") return issue.node_id ? { section: "node_resources", key: issue.node_id } : { section: "node_resources" };
  if (first === "datasets") return { section: "datasets" };
  if (first === "approve_execution" || first === "approve_data_transfer") return { section: "approval" };
  if (first === "graph") return issue.node_id ? { section: "graph", key: issue.node_id } : { section: "graph" };
  return { section: "other" };
}

const ENCODING = "문자 인코딩이 맞지 않습니다. Windows에서 만든 CSV라면 CP949로 바꿔 다시 미리보세요.";
/** The graph editor's card names (graph-model KIND_LABELS), so the advice names cards the add menu has. */
const MATERIALIZE = KIND_LABELS.materialize, SELECT = KIND_LABELS.select, MERGE = KIND_LABELS.merge;
/** English domain texts of Tasks 1–8 (exact prefixes or fixed fragments) → Korean. Checked before CODE_TEXTS. */
const MESSAGE_TEXTS: [RegExp, string][] = [
  [/codec can't decode/, ENCODING],
  [/^input requires model output/, "이 입력에는 모델 출력이 필요합니다."],
  [/^input requires evaluation output/, "이 입력에는 평가 출력이 필요합니다."],
  [/^graph contains a cycle/, "그래프에 순환 연결이 있습니다."],
  [/^dependency refers to a missing node/, "연결된 단계를 찾을 수 없습니다."],
  [/^node IDs must be unique/, "단계 ID가 중복되었습니다."],
  [/^graph exceeds 256 edges/, "그래프 연결이 256개를 넘습니다."],
  [/^parent transition requires explicit materialize/, `이전 단계와 학습 방식이 달라 먼저 ${MATERIALIZE} 단계를 넣어야 합니다.`],
  [/^SFT to preference training requires explicit materialize/, `SFT 결과로 선호 학습을 하려면 먼저 ${MATERIALIZE} 단계를 넣어야 합니다.`],
  [/^OFT artifact handoff\/evaluation\/merge\/materialize is unsupported/, `OFT 결과는 다음 단계·평가·병합·${MATERIALIZE}에 쓸 수 없습니다.`],
  [/^OFT training is unsupported in graphs/, "그래프에서는 OFT 학습을 쓸 수 없습니다."],
  [/^unsupported primary\/legacy PPO artifact route/, "PPO와 다른 학습 단계 사이에는 결과를 넘길 수 없습니다."],
  [/^PPO adapter continuation is unsupported/, `PPO는 어댑터를 이어서 학습할 수 없습니다. 이전 단계 다음에 먼저 ${MATERIALIZE} 단계를 넣으세요.`],
  [/^PPO requires a typed reward binding/, "PPO에는 보상 모델 연결이 필요하고, 다른 단계는 보상 모델을 쓸 수 없습니다."],
  [/^training requires a train dataset binding/, "학습 단계에는 학습 역할의 데이터를 연결해야 합니다."],
  [/^training dataset kind differs from recipe input kind/, "학습 데이터 종류가 레시피의 입력 형식과 다릅니다."],
  [/^dataset ID is unsupported by the training adapter/, "이 데이터 ID는 학습 엔진에서 쓸 수 없습니다."],
  [/^the same dataset cannot be relabeled between roles/, "같은 데이터를 서로 다른 역할(학습·검증·시험)로 연결할 수 없습니다."],
  [/^training and held-out inputs overlap/, "학습 데이터와 검증·시험 데이터의 입력이 겹칩니다."],
  [/^dataset snapshot is missing/, "데이터 스냅샷을 찾을 수 없습니다."],
  [/^input kind does not match training stage/, "입력 형식이 학습 단계와 맞지 않습니다."],
  [/^dataset exceeds 32 MiB limit|^canonical dataset exceeds 32 MiB limit/, "데이터가 32 MiB 한도를 넘습니다."],
  [/^duplicate_header: /, "CSV 헤더에 같은 이름의 열이 있습니다."],
  [/^CSV row has a different number of columns/, "열 개수가 헤더와 다른 CSV 행이 있습니다."],
  [/^invalid CSV headers/, "CSV 헤더가 비어 있거나 올바르지 않습니다."],
  [/^JSON source must contain an array/, "JSON 파일은 레코드 배열이어야 합니다."],
  [/^source must contain nonempty records/, "원본에 레코드가 없거나 객체가 아닌 레코드가 있습니다."],
  [/^CP949 is supported only for CSV/, "CP949 인코딩은 CSV에서만 쓸 수 있습니다."],
  [/^duplicate dataset binding ID/, "데이터 연결 ID가 중복되었습니다."],
  // graph_planner's merge and selection refusals (11.5), sent as graph_plan's `plan_refused`, with the node when known.
  [/^selection candidates must be distinct models/, `${SELECT} 후보는 서로 다른 모델이어야 합니다(앞 ${SELECT}을 거쳐도 같은 모델이면 안 됨).`],
  [/^a selection takes at most one merged candidate/, `${SELECT}에는 ${MERGE} 모델 평가를 하나까지만 넣을 수 있습니다.`],
  [/^a selection needs an original candidate beside its merged one/, `${MERGE} 모델 평가 옆에 원래 모델 평가가 하나 이상 있어야 합니다.`],
  [/^merge requires distinct actual artifacts/, `같은 모델을 두 번 ${MERGE}할 수 없습니다(${SELECT}을 거쳐도 같음).`],
  [/^merge requires plain causal LoRA\/QLoRA parents/, `${MERGE}에는 LoRA·QLoRA 어댑터만 넣을 수 있습니다(${MATERIALIZE} 결과는 안 됨).`],
];
/** Known codes → Korean, used when the message is English and no message text above matched. */
const CODE_TEXTS: Record<string, string> = {
  revision_conflict: "다른 곳에서 먼저 저장한 최신 버전이 있습니다. 최신 버전을 불러오거나 내 변경으로 덮어쓰세요.",
  request_reused: "같은 요청 ID가 다른 내용으로 이미 쓰였습니다. 다시 시도하세요.",
  preview_changed: "미리본 뒤 원본이나 설정이 바뀌었습니다. 다시 미리보세요.",
  version_exists: "이미 등록된 버전입니다. 다른 버전을 입력하세요.",
  run_state: "지금 실행 상태에서는 이 요청을 처리할 수 없습니다.",
  tracking_state: "실패했거나 불확실한 기록만 다시 시도할 수 있습니다.",
  size_limit: "데이터가 32 MiB 한도를 넘습니다.",
  import_issues: "미리보기에 문제가 있는 행이 있어 등록할 수 없습니다.",
  unknown_definition: "저장된 정의를 찾을 수 없습니다.",
  run_unknown: "실행을 찾을 수 없습니다.",
  tracking_event_unknown: "기록 이벤트를 찾을 수 없습니다.",
  extra_field: "허용하지 않는 항목이 요청에 있습니다.",
  invalid_value: "입력값이 올바르지 않습니다.",
  refused: "요청을 처리할 수 없습니다.",
  invalid_source: "원본 파일을 읽을 수 없습니다. 형식·구분자·인코딩을 확인하세요.",
  duplicate_header: "CSV 헤더에 같은 이름의 열이 있습니다.",
  invalid_row: "이 행을 연결한 열로 학습 형식에 맞게 바꿀 수 없습니다.",
  invalid_dataset: "바꾼 데이터가 학습 형식 검사를 통과하지 못했습니다.",
  typed_port: "입력과 이전 단계 출력의 종류가 맞지 않습니다.",
  cycle: "그래프에 순환 연결이 있습니다.",
  missing_node: "연결된 단계를 찾을 수 없습니다.",
  duplicate_node: "단계 ID가 중복되었습니다.",
  edge_limit: "그래프 연결이 256개를 넘습니다.",
  dataset_role: "같은 데이터를 서로 다른 역할(학습·검증·시험)로 연결할 수 없습니다.",
  input_overlap: "학습 데이터와 검증·시험 데이터의 입력이 겹칩니다.",
  dataset: "데이터 스냅샷을 확인할 수 없습니다.",
  train_contract: "학습 단계의 설정이 레시피·데이터와 맞지 않습니다.",
  parent_transition: "이전 단계의 결과를 이 단계에 이어서 쓸 수 없습니다.",
  plan_refused: "실행 계획을 만들 수 없습니다.",
  plan_invalid: "실행 계획을 저장할 수 없습니다.",
  benchmark_unsupported: "이 벤치마크 설정은 평가 엔진에서 쓸 수 없습니다.",
  targets_failed: "모델 모듈 구조를 확인하지 못했습니다.",
  snapshot_failed: "모델 설정·토크나이저 파일을 받지 못했습니다.",
  inspection_failed: "자원을 확인하지 못했습니다.",
  // 11.5 (R5 M1): codes whose message is an exception text, and fd9c283's merge check. Korean messages stay as received.
  merge_bases_differ: "병합할 모델들의 기반 모델·토크나이저·대상 모듈이 서로 다릅니다. 같은 기반 모델에서 같은 대상 모듈로 학습한 결과만 병합할 수 있습니다.",
  executor_profile_invalid: "실행 프로필 설정이 올바르지 않습니다. 실행 프로필 화면에서 값을 확인하고 다시 저장하세요.",
  tracking_invalid: "MLflow 연결 설정이 올바르지 않습니다. 연결 설정에서 서버 주소와 인증 방식을 확인하세요.",
  benchmark_invalid: "벤치마크를 저장할 수 없습니다. 평가 데이터와 생성 설정을 확인하세요.",
  sample_id_invalid: "벤치마크 샘플 ID가 평가 데이터와 맞지 않습니다. 샘플을 다시 고르세요.",
  planning_inputs_invalid: "저장된 계획 입력을 읽을 수 없습니다. 최대 실행 시간과 추정 자원을 다시 정한 뒤 ‘계획 확인’을 누르세요.",
  graph_schema: "저장된 그래프 형식이 올바르지 않습니다. 단계의 필수 항목과 연결을 확인한 뒤 다시 저장하세요.",
  // The backend's plan-hash dedupe (11.5): the same plan was registered under another request ID, also when cancelled.
  plan_submitted: "같은 계획이 이미 다른 실행으로 등록되어 있습니다(취소한 실행 포함). 다시 실행하려면 입력(최대 실행 시간 등)을 바꾸거나 실행 프로필에서 자원을 다시 확인한 뒤 ‘계획 확인’으로 새 계획을 만드세요.",
};
const HANGUL = /[가-힣]/;

/** Korean text of an issue; `original` is the received message when the text differs from it. */
export function localize(issue: WorkflowIssue): Localized {
  const message = issue.message ?? "";
  // A text with Korean (Task 9's own texts, or an English text quoting a Korean name) is translated only by a known
  // English pattern inside it, never by its code: a Korean message under a code whose usual text is English (e.g.
  // version_exists with hf_targets' TARGETS_CHANGED) is specific advice that a generic code text would replace.
  const pattern = MESSAGE_TEXTS.find(([expression]) => expression.test(message))?.[1];
  const known = HANGUL.test(message) ? pattern : pattern ?? CODE_TEXTS[issue.code];
  return known && known !== message ? { text: known, original: message } : { text: message, original: null };
}

/** Escaped `<ul class="issue-list">`: each item in Korean with its original under "원문 보기"; "외 N건" past `limit`. */
export function issueList(issues: WorkflowIssue[], options: { heading?: string; limit?: number } = {}): string {
  if (!issues.length) return "";
  const limit = Math.max(0, options.limit ?? 50);
  const items = issues.slice(0, limit).map(issue => {
    const { text, original } = localize(issue);
    const source = original === null ? "" : `<details><summary>원문 보기</summary><span class="issue-original">${escapeHtml(original)}</span></details>`;
    return `<li data-code="${escapeHtml(issue.code)}"><span>${escapeHtml(text)}</span>${source}</li>`;
  });
  if (issues.length > limit) items.push(`<li class="issue-more">외 ${issues.length - limit}건</li>`);
  const heading = options.heading ? `<p class="issue-heading">${escapeHtml(options.heading)}</p>` : "";
  return `${heading}<ul class="issue-list"${options.heading ? ` aria-label="${escapeHtml(options.heading)}"` : ""}>${items.join("")}</ul>`;
}
