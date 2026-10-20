// The approval dialog of a held plan (10.6; D8, Task 9 D9). A dry run needs no approval ("모의 실행 등록"); a real plan
// needs two separate checkboxes, execution and data transfer, before "승인하고 실행 등록" enables. The body shows what
// is approved: the plan hash, the steps, the maximum estimated cost (with retries; not a provider billing cap), the
// maximum duration and concurrent GPUs, every destination, every dataset, the MLflow destination and the steps that
// wait on a shared single destination. Every dynamic text is escaped (plan data is untrusted).
import { KIND_LABELS, type NodeKind } from "../graph-model";
import { escapeHtml, openDialog, statusPill } from "../workbench";
import type { PlanSummary, PublicProfile, Sha256 } from "../workflow-types";

export interface ApprovalChoice { approveExecution: boolean; approveDataTransfer: boolean }
export const EXECUTION_APPROVAL = "위 실행 자원에서 학습·평가·병합을 실행하고, 최대 추정 비용까지 GPU 요금이 발생하는 것을 승인합니다.";
export const TRANSFER_APPROVAL = "위 데이터를 위 실행 자원으로 전송하는 것을 승인합니다.";
export const COST_NOTE = "재시도를 포함한 최대 추정치입니다. 제공자 청구 상한이 아니며 저장·대기 비용은 별도입니다.";
export const WAITING_TEXT = "대기 후 순차 실행";
const SIMULATION_NOTE = "모의 실행 · GPU를 쓰지 않고 데이터를 보내지 않습니다. 모의 결과는 실제 품질이 아닙니다.";
const REAL_NOTE = "실제 실행 · 승인하면 컨트롤러가 아래 실행 자원에서 단계를 실행하고 데이터를 보냅니다. 실행과 데이터 전송을 각각 승인해야 등록합니다.";
const COST_SOURCES: Record<string, string> = { runpod_api: "RunPod API", estimate: "계획용 추정", operator: "직접 입력한 서버 요금" };
const AUTH_MODES: Record<string, string> = { oidc: "OIDC", token: "토큰", none: "인증 없음" };

/** 9.8 adds each node's recipe and data references to the summary (workflow-types.ts stays as it is for this sub-task). */
export interface PlanNodeReferences {
  recipe?: { recipe_id: string | null; version: string | null; name: string | null; training_stage: string | null;
    training_method: string | null; revision_sha256: Sha256 } | null;
  datasets?: { binding_id: string | null; benchmark_revision: Sha256 | null; dataset_id: string; version: string; sha256: Sha256 }[];
}
export type PlanNode = PlanSummary["nodes"][number] & PlanNodeReferences;

const finite = (value: unknown): value is number => typeof value === "number" && Number.isFinite(value);
/** "$1.89": two to four decimals. */
export const usd = (value: unknown) => finite(value)
  ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "—";
/** "2시간", "1시간 30분", "5분" (whole minutes, rounded up). */
export function durationText(seconds: unknown): string {
  if (!finite(seconds) || seconds < 0) return "—";
  const minutes = Math.ceil(seconds / 60);
  if (minutes < 60) return `${minutes}분`;
  const hours = Math.floor(minutes / 60), rest = minutes % 60;
  return rest ? `${hours}시간 ${rest}분` : `${hours}시간`;
}
export const kindLabel = (kind: string) => KIND_LABELS[kind as NodeKind] ?? kind;
export const costSource = (source: string) => COST_SOURCES[source] ?? source;
export const gpuText = (gpuType: unknown, gpuCount: unknown) => `${String(gpuType ?? "")} ×${String(gpuCount ?? "")}`;
/** A destination's name: the native profile's name, "계획용 추정" for an estimate, else the ID. */
export function profileName(profileId: string, profiles: readonly PublicProfile[]): string {
  const native = profiles.find(profile => profile.profile_id === profileId);
  if (native) return native.fields?.name || profileId;
  return profileId.startsWith("estimate-") ? "계획용 추정" : profileId;
}
/** "A100 Pod (pod-a)", or the ID alone when it is its own name. */
export function profileText(profileId: string, profiles: readonly PublicProfile[]): string {
  const name = profileName(profileId, profiles);
  return name === profileId ? profileId : `${name} (${profileId})`;
}

/** Each destination of the plan once, with the steps allowed on it (in plan order). */
function destinations(summary: PlanSummary) {
  const found = new Map<string, { resource: PlanSummary["nodes"][number]["allowed"][number]; steps: string[] }>();
  for (const node of summary.nodes)
    for (const resource of Array.isArray(node.allowed) ? node.allowed : []) {
      const entry = found.get(resource.profile_id) ?? { resource, steps: [] };
      entry.steps.push(node.label || node.node_id);
      found.set(resource.profile_id, entry);
    }
  return [...found.values()];
}

function trackingHtml(summary: PlanSummary): string {
  if (!summary.tracking) return summary.simulation ? "모의 실행은 MLflow에 기록하지 않습니다." : "MLflow 기록 위치가 계획에 없습니다.";
  const settings = summary.tracking.settings;
  if (!settings) return `MLflow 공개 설정을 읽을 수 없습니다 · 설정 해시 <span class="mono">${escapeHtml(String(summary.tracking.public_settings_sha256).slice(0, 8))}</span>`;
  const auth = AUTH_MODES[settings.auth_mode] ?? settings.auth_mode ?? "";
  return `<span class="plan-uri">${escapeHtml(settings.tracking_uri)}</span>${settings.experiment ? ` · 실험 ${escapeHtml(settings.experiment)}` : ""}${settings.artifact_location ? ` · 산출물 ${escapeHtml(settings.artifact_location)}` : ""}${auth ? ` · 인증 ${escapeHtml(auth)}` : ""}`;
}

/** The dialog body: what the approval covers, all escaped. `waiting` names the steps that wait on a shared single
 * destination (`planWaiting` in plan-panel.ts). */
export function approvalBodyHtml(summary: PlanSummary, profiles: PublicProfile[], waiting: Set<string>, projectName: string): string {
  const nodes = (Array.isArray(summary.nodes) ? summary.nodes : []) as PlanNode[];
  const datasets = Array.isArray(summary.datasets) ? summary.datasets : [];
  const steps = nodes.map(node => {
    const places = (node.allowed ?? []).map(resource => profileText(resource.profile_id, profiles));
    const recipe = node.recipe?.name ? ` · ${node.recipe.name}${node.recipe.version ? ` v${node.recipe.version}` : ""}` : "";
    return `<tr data-plan-step="${escapeHtml(node.node_id)}"><td><b>${escapeHtml(node.label || node.node_id)}</b><small>${escapeHtml(kindLabel(node.kind))} · ${escapeHtml(node.node_id)}${escapeHtml(recipe)}</small>${waiting.has(node.node_id) ? `<span class="plan-wait">${WAITING_TEXT}</span>` : ""}</td>
      <td>${places.length ? escapeHtml(places.join(", ")) : "GPU를 쓰지 않음"}</td><td>${escapeHtml(usd(node.max_estimated_cost_usd))}</td><td>${escapeHtml(durationText(node.max_duration_seconds))}</td></tr>`;
  }).join("");
  const places = destinations(summary).map(({ resource, steps }) => `<tr data-plan-destination="${escapeHtml(resource.profile_id)}">
      <td><b>${escapeHtml(profileName(resource.profile_id, profiles))}</b><small class="mono">${escapeHtml(resource.profile_id)}</small></td>
      <td>${escapeHtml(gpuText(resource.gpu_type, resource.gpu_count))}</td><td>${escapeHtml(usd(resource.total_hourly_cost_usd))}/시간</td>
      <td>${escapeHtml(costSource(resource.cost_source))}</td><td>${escapeHtml(steps.join(", "))}</td></tr>`).join("");
  const data = datasets.map(item => `<tr data-plan-dataset="${escapeHtml(item.dataset_id)}"><td>${escapeHtml(item.dataset_id)}</td><td>v${escapeHtml(item.version)}</td>
      <td class="mono">${escapeHtml(String(item.sha256).slice(0, 8))}</td><td>${item.synthetic ? "합성" : "사용자"}</td><td>${item.export_allowed ? "전송 허용" : "전송 불가"}</td></tr>`).join("");
  const waitingSteps = nodes.filter(node => waiting.has(node.node_id));
  const attempts = summary.retry_policy?.max_attempts;
  return `<div class="plan-approval">
    <p class="plan-approval-head">${statusPill(summary.simulation ? "neutral" : "warn", summary.simulation ? "모의 실행" : "실제 실행")} <b>${escapeHtml(projectName)}</b></p>
    <p>${summary.simulation ? SIMULATION_NOTE : REAL_NOTE}</p>
    <dl class="plan-totals">
      <div data-plan-total="cost"><dt>최대 추정 비용</dt><dd>${escapeHtml(usd(summary.max_estimated_cost_usd))}<small>${COST_NOTE}</small></dd></div>
      <div data-plan-total="duration"><dt>최대 소요 시간</dt><dd>${escapeHtml(durationText(summary.max_duration_seconds))}</dd></div>
      <div data-plan-total="gpus"><dt>최대 동시 GPU</dt><dd>${escapeHtml(String(summary.max_concurrent_gpu_count ?? "—"))}개</dd></div>
      <div data-plan-total="attempts"><dt>단계별 최대 시도</dt><dd>${finite(attempts) ? `${attempts}회 (재시도 포함)` : "—"}</dd></div>
    </dl>
    <p class="plan-hash">계획 해시 <span class="mono">${escapeHtml(summary.plan_sha256)}</span></p>
    <h3>단계 ${nodes.length}개</h3>
    <div class="plan-table-wrap"><table class="plan-table"><thead><tr><th scope="col">단계</th><th scope="col">실행 위치</th><th scope="col">최대 비용</th><th scope="col">최대 시간</th></tr></thead><tbody>${steps}</tbody></table></div>
    <h3>실행 위치</h3>
    ${places ? `<div class="plan-table-wrap"><table class="plan-table"><thead><tr><th scope="col">실행 프로필</th><th scope="col">GPU</th><th scope="col">시간당 총 요금</th><th scope="col">요금 출처</th><th scope="col">쓰는 단계</th></tr></thead><tbody>${places}</tbody></table></div>` : '<p class="muted">GPU를 쓰는 단계가 없습니다.</p>'}
    <h3>데이터</h3>
    ${data ? `<div class="plan-table-wrap"><table class="plan-table"><thead><tr><th scope="col">데이터 ID</th><th scope="col">버전</th><th scope="col">SHA256</th><th scope="col">종류</th><th scope="col">PC 밖 전송</th></tr></thead><tbody>${data}</tbody></table></div>` : '<p class="muted">쓰는 데이터가 없습니다.</p>'}
    <h3>MLflow 기록</h3><p data-plan-total="tracking">${trackingHtml(summary)}</p>
    ${waitingSteps.length ? `<h3>같은 실행 위치를 기다리는 단계</h3><ul class="plan-waiting">${waitingSteps.map(node => `<li>${escapeHtml(node.label || node.node_id)} · ${WAITING_TEXT}</li>`).join("")}</ul>` : ""}
  </div>`;
}

/**
 * Opens the approval (10.1 `openDialog`: appended to `body`, the heading focused, Esc cancels, the first activation
 * disables every action, focus returns to the opener). Resolves null on Esc or "돌아가기". A dry run resolves both
 * approvals false; a real plan resolves only with both boxes ticked (the confirm button requires both).
 */
export async function openApproval(summary: PlanSummary, profiles: PublicProfile[], waiting: Set<string>, projectName: string): Promise<ApprovalChoice | null> {
  const real = summary.simulation !== true;
  const answer = await openDialog({
    title: real ? "실행 승인" : "모의 실행 등록",
    bodyHtml: approvalBodyHtml(summary, profiles, waiting, projectName),
    checks: real ? [{ id: "execution", label: `실행 승인 · ${EXECUTION_APPROVAL}` }, { id: "transfer", label: `데이터 전송 승인 · ${TRANSFER_APPROVAL}` }] : [],
    actions: [{ id: "back", label: "돌아가기" }, real
      ? { id: "submit", label: "승인하고 실행 등록", primary: true, requires: ["execution", "transfer"] }
      : { id: "submit", label: "모의 실행 등록", primary: true }],
  });
  if (answer?.action !== "submit") return null;
  if (!real) return { approveExecution: false, approveDataTransfer: false };
  const approveExecution = answer.checked.includes("execution"), approveDataTransfer = answer.checked.includes("transfer");
  return approveExecution && approveDataTransfer ? { approveExecution, approveDataTransfer } : null;
}
