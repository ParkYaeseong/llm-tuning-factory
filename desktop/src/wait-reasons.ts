// Korean texts for the graph controller's English wait reasons, statuses and tracking texts (D12). Pure: no imports.
// The controller's texts carry no codes, so they are matched by their fixed prefixes (graph_controller.py:62-89,
// executor_profiles.py:122-124, graph_store.py:85-86, tracking_outbox.py:62-70, graph_store._state). A wording change
// in the backend falls back to the original text, never to a wrong one; the original is always kept for "원문 보기".

export type ReasonKind = "waiting" | "operator" | "replan" | "budget" | "error";
export interface LocalizedReason { text: string; original: string; kind: ReasonKind }
export interface LocalizedText { text: string; original: string | null }

type Rule = [RegExp, ReasonKind, (match: RegExpExecArray) => string];
const REASONS: Rule[] = [
  [/^waiting for destination (\S+)/, "waiting", match => `같은 실행 자원의 다른 단계가 끝나기를 기다리는 중입니다 (대상: ${match[1]})`],
  [/^waiting: the run is at its max_concurrent limit/, "waiting", () => "최대 동시 실행 수에 도달해 기다리는 중입니다"],
  [/^waiting: every dispatch thread/, "waiting", () => "컨트롤러의 실행 슬롯이 모두 사용 중이라 기다리는 중입니다"],
  [/^waiting for the retry backoff(?: \(attempt (\d+) of (\d+)\))?/, "waiting",
    match => match[1] ? `다시 시도 간격을 기다리는 중입니다 (시도 ${match[1]}/${match[2]})` : "다시 시도 간격을 기다리는 중입니다"],
  [/^waiting for a cross-provider hardware lock/, "waiting", () => "다른 연결 대상(RunPod 등)의 잠금이 풀리기를 기다립니다"],
  [/^waiting for the result of the same launch/, "waiting", () => "같은 실행의 결과를 기다립니다. 시간 제한이 지나기 전에는 다시 실행하지 않습니다"],
  [/^returned unlaunched after takeover/, "waiting", () => "컨트롤러가 바뀌어 실행하지 않고 되돌렸습니다. 실행이 다시 진행되면 배정합니다"],
  [/^no result after the launch's time limit/, "operator",
    () => "시간 제한이 지나도 결과가 없습니다. 원격 실행이 멈춘 것을 확인한 뒤 잠금을 해제해야 합니다"],
  [/^operator confirmation required/, "operator", () => "원격 실행이 멈췄는지 운영자 확인이 필요합니다"],
  [/^the completed attempt's outputs could not be recorded/, "operator", () => "완료된 시도의 결과를 기록하지 못했습니다. 운영자가 확인해야 합니다"],
  [/^ledger integrity check failed/, "operator", () => "작업 기록 무결성 검사에 실패했습니다. 운영자가 기록을 복구하거나 지울 때까지 기다립니다"],
  [/^live destination facts differ/, "replan", () => "실행 자원의 실제 정보(GPU 또는 더 높은 가격)가 승인한 계획과 다릅니다. 다시 계획하세요"],
  [/^executor profile changed after approval/, "replan", () => "승인한 뒤 실행 프로필이 바뀌었습니다. 다시 계획하세요"],
  [/^executor transport settings changed after approval/, "replan", () => "승인한 뒤 실행 프로필의 연결 설정이 바뀌었습니다. 다시 계획하세요"],
  [/^the opened destination is not this job's pinned profile/, "replan", () => "연결된 실행 자원이 이 단계에 지정된 실행 프로필이 아닙니다. 다시 계획하세요"],
  [/^approved budget exhausted(?:: ([\d.e+-]+) of ([\d.e+-]+) USD)?/, "budget", match => match[1]
    ? `승인 예산 소진: 예약 $${match[1]} / 승인 $${match[2]} — 더 큰 예산으로 다시 계획하세요`
    : "승인 예산을 모두 예약했습니다. 더 큰 예산으로 다시 계획하세요"],
  [/^a pre-Task-8 attempt could not be priced/, "budget", () => "가격을 알 수 없는 이전 시도가 승인 예산 전체를 예약했습니다"],
  [/^destination unavailable/, "error", () => "실행 자원에 연결할 수 없습니다"],
  [/^dispatch failed/, "error", () => "단계를 실행 자원에 보내지 못했습니다"],
  [/^settling the attempt failed/, "error", () => "시도 결과를 정리하지 못했습니다. 다음 확인 때 다시 시도합니다"],
  [/^reconcile could not reach/, "error", () => "실행 자원의 상태를 확인하지 못했습니다"],
];

/** The Korean text and kind of a controller wait reason; an unknown reason is shown as received (kind `error`). */
export function localizeWaitReason(reason: string | null): LocalizedReason | null {
  if (!reason) return null;
  for (const [pattern, kind, text] of REASONS) {
    const match = pattern.exec(reason);
    if (match) return { text: text(match), original: reason, kind };
  }
  return { text: reason, original: reason, kind: "error" };
}

const TEXTS: [RegExp, string][] = [
  // tracking_outbox.py: the actions and graph-level errors of the tracking outbox.
  [/^local evidence changed after it was recorded/, "기록한 뒤 로컬 증거 파일이 바뀌어 다시 시도해도 고칠 수 없습니다. 시도의 원래 증거 파일을 되돌리거나, 새 그래프 실행에서 이 단계를 다시 실행하세요."],
  [/^tracking destination differs from the approved plan/, "MLflow 기록 대상이 승인한 계획과 다릅니다. 승인한 MLflow 설정으로 컨트롤러를 다시 시작하세요."],
  [/^MLflow tracking is not configured for this controller/, "이 컨트롤러에 MLflow 기록 설정이 없어 실제 실행의 기록이 기다리는 중입니다."],
  [/^the real graph run has no pinned tracking destination/, "이 실제 실행에는 지정된 MLflow 기록 대상이 없습니다."],
  [/^the graph run's stored plan is unreadable/, "저장된 실행 계획을 읽을 수 없어 기록을 건너뜁니다."],
  // graph_store._state: the run_state refusals of pause, resume and cancel.
  [/^only a running graph can pause/, "실행 중인 그래프만 일시정지할 수 있습니다."],
  [/^only a paused graph can resume/, "일시정지한 그래프만 다시 진행할 수 있습니다."],
  [/^canceled graph cannot resume/, "취소한 실행은 다시 진행할 수 없습니다."],
  [/^a finished graph cannot be canceled/, "이미 끝난 실행은 취소할 수 없습니다."],
  [/^unknown graph run/, "실행을 찾을 수 없습니다."],
  [/^unreadable graph run/, "실행 기록을 읽을 수 없습니다."],
];

/** Korean text for a tracking action, a steering refusal or a run error; unknown texts are shown as received. */
export function localizeRunText(text: string | null): LocalizedText | null {
  if (!text) return null;
  const known = TEXTS.find(([pattern]) => pattern.test(text))?.[1];
  return known ? { text: known, original: text } : { text, original: null };
}

export const NODE_STATUS: Record<"PENDING" | "READY" | "RUNNING" | "COMPLETED" | "FAILED" | "BLOCKED" | "CANCELED", string> = {
  PENDING: "대기", READY: "준비됨", RUNNING: "실행 중", COMPLETED: "완료", FAILED: "실패", BLOCKED: "앞 단계 실패로 중단", CANCELED: "취소",
};
export const RUN_STATUS: Record<"RUNNING" | "PAUSED" | "CANCELED" | "COMPLETED" | "FAILED", string> = {
  RUNNING: "실행 중", PAUSED: "일시정지", CANCELED: "취소", COMPLETED: "완료", FAILED: "실패",
};
export const TRACKING_STATUS: Record<string, string> = {
  not_started: "기록 전", pending: "기록 대기", retrying: "기록 재시도", saved: "기록됨", uncertain: "기록 확인 필요",
  failed: "기록 실패", registered: "등록됨", none: "없음",
};
/** Tracking event kinds (tracking_outbox.EventKind) and their event statuses. */
export const TRACKING_KIND: Record<string, string> = {
  graph_run: "그래프 기록", graph_summary: "그래프 요약 기록", node_run: "단계 기록", attempt_run: "시도 기록",
  attempt_evidence: "시도 결과 파일", model_registration: "모델 등록",
};
export const EVENT_STATUS: Record<string, string> = { FAILED: "기록 실패", UNCERTAIN: "기록 확인 필요", PENDING: "기록 대기", ACKNOWLEDGED: "기록됨" };
export const NODE_KIND: Record<string, string> = { train: "학습", evaluate: "평가", select: "선택", merge: "병합", materialize: "모델 변환" };
/** benchmarks.EvaluationRecord.status */
export const EVALUATION_STATUS: Record<string, string> = {
  pending: "대기", running: "실행 중", completed: "완료", failed: "실패", cancelled: "취소", skipped: "건너뜀", unevaluated: "평가하지 않음",
};
