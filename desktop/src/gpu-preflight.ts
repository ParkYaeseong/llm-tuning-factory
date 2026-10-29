// Execution preflight: GPU compatibility findings from the sidecar, folded into the single run approval.
import { escapeHtml as esc, formatDate } from "./workbench";

type Obj = Record<string, any>;

export const RUNTIME_PREPARATION_NOTICE = "Pod에 이 앱 버전의 학습 환경이 없어 학습 전에 먼저 설치합니다. "
  + "/workspace/factory-desktop 아래에 수 GB를 내려받으며 최대 30분 걸릴 수 있고, 그동안에도 GPU 요금이 발생합니다. "
  + "빌린 Pod라면 Pod 주인의 동의를 받은 뒤 진행하세요.";
export const SERVER_RUNTIME_REQUIRED = "GPU 서버에 이 앱 버전의 학습 환경이 없습니다. "
  + "연결 설정에서 GPU 목록을 조회한 뒤 '학습 환경 준비'를 먼저 누르세요.";
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
  return `<section class="compatibility" data-testid="plan-compatibility"><h4>GPU 호환성 점검</h4>`
    + (report.observed_at ? `<p class="muted">저장된 GPU 정보 기준 · ${esc(formatDate(report.observed_at))}</p>` : "")
    + (blocks.length ? `<p class="warning-text">실행 시 거부될 항목</p>${items(blocks)}` : "")
    + (warns.length ? `<p>주의 항목</p>${items(warns)}` : "")
    + (findings.length ? "" : "<p>저장된 GPU 정보 기준으로 문제가 없습니다.</p>")
    + "</section>";
}

/** runtime_check before a run: an error to show, or what to add to the approval and whether to prepare. */
export function preflightOutcome(report: Obj | null | undefined, server: boolean):
    { error: string } | { prepare: boolean; extra: string } {
  const findings: Obj[] = report?.findings ?? [];
  const blocks = findings.filter(item => item.level === "block");
  if (blocks.length) return { error: ["실행할 수 없습니다.", ...blocks.map(item => item.message)].join("\n") };
  const state = report?.ready === false ? report.runtime_state : "ready";
  if (state === "incompatible") return { error: GPU_INCOMPATIBLE };
  if (state === "unavailable") return { error: GPU_UNAVAILABLE };
  if (!["ready", "missing", "mismatch"].includes(state)) return { error: UNKNOWN_RUNTIME_STATE };
  const missing = state === "missing" || state === "mismatch";
  if (missing && server) return { error: SERVER_RUNTIME_REQUIRED };
  const warns = findings.filter(item => item.level !== "block").map(item => "· " + item.message);
  return { prepare: missing, extra: (missing ? "\n\n" + RUNTIME_PREPARATION_NOTICE : "")
    + (warns.length ? "\n\n주의 항목\n" + warns.join("\n") : "") };
}

/** The notice after 환경 확인 or 학습 환경 준비, with the observed GPU when there is one. */
export function runtimeNotice(report: Obj, server: boolean): string {
  const gpu = report?.observation?.gpus?.[0];
  const facts = gpu ? `\nGPU: ${gpu.name} · compute capability ${gpu.capability} · `
    + `${(gpu.memory_mib / 1024).toFixed(1)}GiB · 드라이버 ${gpu.driver}` : "";
  const target = server ? "GPU 서버" : "Pod";
  if (report?.ready === false && report.runtime_state === "incompatible") return GPU_INCOMPATIBLE + facts;
  if (report?.ready === false && report.runtime_state === "unavailable") return GPU_UNAVAILABLE + facts;
  if (report?.ready === false && server) return "선택한 GPU 서버에 이 앱 버전의 학습 환경이 아직 없습니다. "
    + "연결 설정에서 GPU 목록을 조회한 뒤 '학습 환경 준비'를 누르세요." + facts;
  if (report?.ready === false) return `선택한 ${target}에 이 앱 버전의 학습 환경이 아직 없습니다. `
    + "'학습 환경 준비'를 누르거나 학습 실행 승인에서 함께 준비하세요." + facts;
  return `선택한 ${target}에서 앱과 일치하는 학습 환경과 CUDA를 확인했습니다.` + facts;
}
