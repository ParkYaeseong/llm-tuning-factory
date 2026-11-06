import { t as tr, msg, html as ui, staticHtml, localized } from "./i18n";
// Execution preflight: GPU compatibility findings from the sidecar, folded into the single run approval.
import { escapeHtml as esc, formatDate } from "./workbench";

type Obj = Record<string, any>;

export const RUNTIME_CLEANUP_NOTICE = () => tr("준비 전에 같은 작업 폴더에서 이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다")
  + tr("(실행 중인 작업의 환경, 모델·데이터 캐시, 작업 기록은 지우지 않습니다).");
export const RUNTIME_PREPARATION_NOTICE = () => tr("Pod에 이 앱 버전의 학습 환경이 없어 학습 전에 먼저 설치합니다. ")
  + tr("/workspace/factory-desktop 아래에 수 GB를 내려받으며 최대 30분 걸릴 수 있고, 그동안에도 GPU 요금이 발생합니다. ")
  + tr("빌린 Pod라면 Pod 주인의 동의를 받은 뒤 진행하세요. ") + RUNTIME_CLEANUP_NOTICE();
export const SERVER_RUNTIME_REQUIRED = () => tr("GPU 서버에 이 앱 버전의 학습 환경이 없습니다. ")
  + tr("연결 설정에서 GPU 목록을 조회한 뒤 '학습 환경 준비'를 먼저 누르세요.");
export const GPU_INCOMPATIBLE = "이 GPU에서 앱 학습 환경의 CUDA 연산이 실행되지 않습니다. 지원하는 GPU 세대인지 확인하세요.";
export const GPU_UNAVAILABLE = "GPU를 사용할 수 없거나 사용 중입니다. 다른 작업이 GPU를 쓰고 있는지, Pod·서버의 GPU 상태를 확인하세요.";

export const UNKNOWN_RUNTIME_STATE = "학습 환경 상태를 확인하지 못했습니다. 연결 설정에서 '환경 확인'을 다시 누르세요.";

const items = (findings: Obj[]) => `<ul>${findings.map(item => `<li>${esc(item.message)}</li>`).join("")}</ul>`;

/** The plan card's GPU compatibility section; empty when the sidecar sent none. */
export function gpuCompatibilityView(report: Obj | undefined): string {
  if (!report) return "";
  const findings: Obj[] = report.findings ?? [];
  const blocks = findings.filter(item => item.level === "block");
  const warns = findings.filter(item => item.level !== "block");
  return staticHtml(`<section class="compatibility" data-testid="plan-compatibility"><h4>GPU 호환성 점검</h4>`)
    + (report.observed_at ? ui`<p class="muted">저장된 GPU 정보 기준 · ${esc(formatDate(report.observed_at))}</p>` : "")
    + (blocks.length ? ui`<p class="warning-text">실행 시 거부될 항목</p>${items(blocks)}` : "")
    + (warns.length ? ui`<p>주의 항목</p>${items(warns)}` : "")
    + (findings.length ? "" : staticHtml("<p>저장된 GPU 정보 기준으로 문제가 없습니다.</p>"))
    + "</section>";
}

/** runtime_check before a run: an error to show, or what to add to the approval and whether to prepare. */
export function preflightOutcome(report: Obj | null | undefined, server: boolean):
    { error: string } | { prepare: boolean; extra: string } {
  const findings: Obj[] = report?.findings ?? [];
  const blocks = findings.filter(item => item.level === "block");
  if (blocks.length) return { error: [tr("실행할 수 없습니다."), ...blocks.map(item => item.message)].join("\n") };
  const state = report?.ready === false ? report.runtime_state : "ready";
  if (state === "incompatible") return { error: tr(GPU_INCOMPATIBLE) };
  if (state === "unavailable") return { error: tr(GPU_UNAVAILABLE) };
  if (!["ready", "missing", "mismatch"].includes(state)) return { error: tr(UNKNOWN_RUNTIME_STATE) };
  const missing = state === "missing" || state === "mismatch";
  if (missing && server) return { error: SERVER_RUNTIME_REQUIRED() };
  const warns = findings.filter(item => item.level !== "block").map(item => "· " + item.message);
  return { prepare: missing, extra: (missing ? "\n\n" + RUNTIME_PREPARATION_NOTICE() : "")
    + (warns.length ? tr("\n\n주의 항목\n") + warns.join("\n") : "") };
}

/** The cleanup lines after a preparation: how many old runtimes it removed and the free space it gained (removed
 * venvs share hard-linked files with uv-cache, so the volume's free-space delta, not their size; under 0.1 GB the
 * count alone), a short note when some or all could not be removed or the cleanup was skipped, and one when its
 * time budget left the rest for the next preparation. */
export function cleanupSummary(report: Obj | null | undefined): string {
  const cleanup = report?.cleanup;
  if (!cleanup) return "";
  const removed = (cleanup.removed ?? []).length;
  const freed = (Math.max(0, Number(cleanup.free_after) - Number(cleanup.free_before)) || 0) / 1024 ** 3;
  const unaffected = tr("(학습 환경 준비에는 영향이 없습니다).");
  const lines: string[] = [];
  if (removed) lines.push(msg`이전 앱 버전의 학습 환경 ${removed}개를 `
    + (freed >= 0.1 ? msg`지워 ${freed.toFixed(1)}GB를 확보했습니다.` : tr("지웠습니다.")));
  if (removed && (cleanup.cleanup_error || cleanup.errors?.length)) lines.push(tr("일부 이전 학습 환경은 지우지 못했습니다") + unaffected);
  else if (cleanup.errors?.length) lines.push(tr("이전 학습 환경을 지우지 못했습니다") + unaffected);   // every removal failed
  else if (cleanup.cleanup_error) lines.push(tr("이전 학습 환경 정리는 건너뛰었습니다") + unaffected);
  if (cleanup.incomplete) lines.push(tr("남은 이전 학습 환경은 다음 학습 환경 준비 때 정리합니다."));
  return lines.map(line => "\n" + line).join("");
}

/** The notice after 환경 확인 or 학습 환경 준비, with the observed GPU when there is one. */
export function runtimeNotice(report: Obj, server: boolean): string {
  const gpu = report?.observation?.gpus?.[0];
  const facts = gpu ? `\nGPU: ${gpu.name} · compute capability ${gpu.capability} · `
    + msg`${(gpu.memory_mib / 1024).toFixed(1)}GiB · 드라이버 ${gpu.driver}` : "";
  const target = server ? tr("GPU 서버") : "Pod";
  if (report?.ready === false && report.runtime_state === "incompatible") return tr(GPU_INCOMPATIBLE) + facts;
  if (report?.ready === false && report.runtime_state === "unavailable") return tr(GPU_UNAVAILABLE) + facts;
  if (report?.ready === false && server) return tr("선택한 GPU 서버에 이 앱 버전의 학습 환경이 아직 없습니다. ")
    + tr("연결 설정에서 GPU 목록을 조회한 뒤 '학습 환경 준비'를 누르세요.") + facts;
  if (report?.ready === false) return msg`선택한 ${target}에 이 앱 버전의 학습 환경이 아직 없습니다. `
    + tr("'학습 환경 준비'를 누르거나 학습 실행 승인에서 함께 준비하세요.") + facts;
  return msg`선택한 ${target}에서 앱과 일치하는 학습 환경과 CUDA를 확인했습니다.` + facts;
}
