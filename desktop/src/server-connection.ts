// SSH GPU server ("ssh_server") connection contract for the Windows workbench: native settings helpers,
// typed sidecar calls, approval texts and escaped renderers. Screen state stays with the caller, so later
// profile and graph screens can reuse this module without importing main.ts.
import { button, escapeHtml as esc, formatDate } from "./workbench";

type Obj = Record<string, any>;
/** The screen's factory_action bridge. */
export type Bridge = (action: string, data?: Obj) => Promise<any>;

// The sidecar's fixed refusals, shown before an approval dialog when the screen can already tell.
export const SERVER_BOUND_JOB = "이 작업은 GPU 서버용으로 승인되었습니다. 연결 대상을 그 GPU 서버로 바꾼 뒤 실행하세요.";
export const UNBOUND_SERVER_JOB = "GPU 서버용 승인 기록이 없는 작업입니다. GPU 서버 연결에서 실행 계획을 다시 확인하고 제출하세요.";
export const UNREADABLE_RECEIPT = "GPU 서버 승인 기록을 읽을 수 없습니다. 작업을 다시 계획·제출하세요.";
const OTHER_SETTINGS_RESERVATION = "다른 서버 설정으로 만든 예약입니다. 그 서버 설정으로 바꾼 뒤 해제하세요.";
// The desktop sidecar uses the default execution policy (one GPU per job); the plan refuses more.
// Native settings still accept up to 8 UUIDs for a later multi-GPU policy. Keep the message in step.
export const MAX_GPUS_PER_JOB = 1;
export const GPU_POLICY_MESSAGE = `현재 앱 정책은 작업당 GPU ${MAX_GPUS_PER_JOB}개입니다. `
  + `GPU를 ${MAX_GPUS_PER_JOB === 1 ? "하나만" : `${MAX_GPUS_PER_JOB}개 이하로`} 선택하세요.`;
/** A PuTTY key path; Windows drops trailing dots and spaces, so "key.ppk." is one too (native refuses saving it). */
export const puttyKeyPath = (path: unknown) => /\.ppk[.\s]*$/i.test(String(path ?? ""));

/* Native settings: string values; the GPU selection is a JSON-encoded UUID array. */

const ROUTE_KEYS = ["server_ssh_host", "server_ssh_port", "server_ssh_username", "server_ssh_fingerprint",
  "server_remote_root", "server_ssh_key_file", "server_total_hourly_cost_usd"];
export const isServer = (settings: Obj) => settings.provider === "ssh_server";
export function selectedGpus(settings: Obj): string[] {
  try {
    const value = JSON.parse(settings.server_selected_gpu_uuids || "[]");
    return Array.isArray(value) ? value.filter(item => typeof item === "string") : [];
  } catch { return []; }
}
export const sameSelection = (a: string[], b: string[]) => JSON.stringify([...a].sort()) === JSON.stringify([...b].sort());
export const sshAddress = (user: unknown, host: unknown, port?: unknown) =>
  `${user || "?"}@${host || "?"}${port === undefined ? "" : ":" + (port || 22)}`;
export const serverAddress = (settings: Obj, port = true) =>
  sshAddress(settings.server_ssh_username, settings.server_ssh_host, port ? settings.server_ssh_port : undefined);
export const serverConfigured = (settings: Obj) => ["server_ssh_host", "server_ssh_username", "server_ssh_fingerprint",
  "server_ssh_key_file", "server_remote_root", "server_total_hourly_cost_usd"].every(key => String(settings[key] ?? "").trim())
  && selectedGpus(settings).length > 0 && !selectionPolicyIssue(settings);
/** "destination" when the provider or a server field changed, "selection" when only the GPU selection did. */
export function destinationChange(before: Obj, after: Obj): "none" | "selection" | "destination" {
  const route = (settings: Obj) => JSON.stringify([isServer(settings), ...ROUTE_KEYS.map(key => settings[key] ?? "")]);
  if (route(before) !== route(after)) return "destination";
  return (before.server_selected_gpu_uuids ?? "") === (after.server_selected_gpu_uuids ?? "") ? "none" : "selection";
}
/** The SSH port as the sidecar parses it: absent is 22, a digit string is an integer ("022" is 22), anything else NaN. */
export function sshPort(value: unknown) {
  if (value === undefined || value === null) return 22;
  if (typeof value === "number") return Number.isInteger(value) ? value : NaN;
  return typeof value === "string" && /^[0-9]+$/.test(value) ? Number(value) : NaN;
}
/** The saved server shaped like jobs[].server_destination; null for RunPod. */
export function settingsRoute(settings: Obj): Obj | null {
  if (!isServer(settings)) return null;
  return { ssh_host: settings.server_ssh_host, ssh_port: sshPort(settings.server_ssh_port), ssh_username: settings.server_ssh_username,
    remote_root: settings.server_remote_root, selected_gpu_uuids: selectedGpus(settings) };
}
// NaN serializes as null and never equals an observed integer port.
const routeKey = (route: Obj) => JSON.stringify([route.ssh_host, sshPort(route.ssh_port), route.ssh_username, route.remote_root, route.selected_gpu_uuids]);
/** The sidecar's refusal for running this job with the saved connection, or null when they agree. */
export function jobDestinationIssue(job: Obj, settings: Obj) {
  const route = job.server_destination, saved = settingsRoute(settings);
  if (route?.invalid) return UNREADABLE_RECEIPT;
  if (!saved) return route ? SERVER_BOUND_JOB : null;
  if (!route) return UNBOUND_SERVER_JOB;
  return routeKey(route) === routeKey(saved) ? null : SERVER_BOUND_JOB;
}
/** Release rebuilds the reservation profile from saved settings, so only matching reservations can be released. */
export function reservationMatches(reservation: Obj, settings: Obj) {
  const t = reservation.profile?.transport || {};
  const key = (...values: unknown[]) => JSON.stringify(values);
  return isServer(settings) && key(t.ssh_host, sshPort(t.ssh_port), t.ssh_username, t.ssh_fingerprint, t.remote_root, t.selected_gpu_uuids)
    === key(settings.server_ssh_host, sshPort(settings.server_ssh_port), settings.server_ssh_username,
      settings.server_ssh_fingerprint, settings.server_remote_root, selectedGpus(settings));
}
/** Why the saved GPU selection cannot be used under the app policy, or null. */
export function selectionPolicyIssue(settings: Obj) {
  const count = selectedGpus(settings).length;
  return count > MAX_GPUS_PER_JOB ? `${GPU_POLICY_MESSAGE} (저장된 선택 GPU ${count}개)` : null;
}
/** Why a fresh inspection cannot be approved for runtime preparation, or null. */
export function prepareIssue(inspection: Obj) {
  const selected: string[] = inspection.transport.selected_gpu_uuids;
  if (!selected.length) return "학습에 사용할 GPU를 조회·선택하고 저장한 뒤 환경을 준비하세요.";
  if (selected.some(uuid => !inspection.inventory.some((gpu: Obj) => gpu.uuid === uuid)))
    return "저장된 선택 중 이번 조회에 없는 GPU가 있습니다. GPU 목록을 확인하고 선택을 다시 저장하세요.";
  return null;
}

/* Sidecar calls. Approval-gated calls always carry their approval flags; callers show the dialog first. */

export function serverBridge(api: Bridge) {
  return {
    serverInspect: (): Promise<Obj> => api("server_inspect"),
    serverFacts: (): Promise<Obj> => api("server_facts"),
    /** `inspection` must be the unmodified server_inspect result shown in the approval dialog. */
    runtimePrepare: (inspection: Obj, stage: string): Promise<Obj> =>
      api("runtime_prepare", { approved: true, inspection, training_stage: stage }),
    runtimeReservations: (): Promise<Obj[]> => api("runtime_reservations"),
    runtimeRelease: (reservationId: string): Promise<Obj> =>
      api("runtime_release", { approved: true, confirm_preparation_stopped: true, reservation_id: reservationId }),
  };
}

/* Training resources from server facts. */

/** Per-GPU price whose product with the count never falls below the total, which the backend refuses.
 * When the binary division lands below, the price moves up to the next representable value. */
export function perGpuHourlyCost(total: number, count: number) {
  if (!Number.isFinite(total) || total < 0 || !Number.isInteger(count) || count < 1)
    throw new Error("GPU 서버의 시간당 비용 또는 GPU 수가 올바르지 않습니다.");
  let price = total / count;
  const bits = new DataView(new ArrayBuffer(8));
  while (price * count < total) { bits.setFloat64(0, price); bits.setBigUint64(0, bits.getBigUint64(0) + 1n); price = bits.getFloat64(0); }
  return price;
}
export function resourcesFromFacts(facts: Obj) {
  return { gpu_type: facts.gpu_type, gpu_count: facts.gpu_count,
    hourly_cost_usd: perGpuHourlyCost(Number(facts.total_hourly_cost_usd), facts.gpu_count) };
}

/* Approval texts (plain text; the dialog escapes them). */

export const RUNPOD_TRANSFER_APPROVAL = "선택한 작업의 학습·검증 데이터를 RunPod로 전송하고 실제 학습을 실행합니다. "
  + "실행 기록과 산출물은 공용 MLflow에 저장됩니다. 기존 Pod는 작업이 끝나도 자동 종료되지 않습니다.";
/** `route` is the job's server_destination or settingsRoute(); null means RunPod. */
export function transferApproval(route: Obj | null) {
  if (!route) return RUNPOD_TRANSFER_APPROVAL;
  return `선택한 GPU 서버(${sshAddress(route.ssh_username, route.ssh_host)})로 학습·검증 데이터를 전송하고 실제 학습을 실행합니다. `
    + `작업 파일은 서버 작업 폴더(${route.remote_root}) 아래에 만들어지고 실행 기록과 산출물은 공용 MLflow에 저장됩니다. `
    + "앱은 서버를 켜거나 끄지 않습니다.";
}
export function prepareApproval(inspection: Obj, stage: string) {
  const t = inspection.transport, names = new Map(inspection.inventory.map((gpu: Obj) => [gpu.uuid, gpu.name]));
  const gpus = t.selected_gpu_uuids.map((uuid: string) => `${names.get(uuid) || "조회되지 않은 GPU"} (${uuid})`).join(", ");
  return `${sshAddress(t.ssh_username, t.ssh_host, t.ssh_port)}의 작업 폴더 ${t.remote_root} 아래에 ${stage} 학습용 앱 전용 환경을 설치합니다. `
    + "의존성·Python·학습 도구는 이 폴더로 다운로드하며 서버의 기본 Python과 시스템 패키지는 변경하지 않습니다.\n"
    + `선택 GPU: ${gpus}\n사용자 데이터는 전송하지 않습니다. 앱은 서버를 켜거나 끄지 않습니다.`;
}
export function releaseApproval(reservation: Obj) {
  const t = reservation.profile?.transport || {}, server = sshAddress(t.ssh_username, t.ssh_host, t.ssh_port);
  // A PREPARING reservation outlives a killed app or sidecar, but it may also belong to a preparation still running.
  const preparing = reservation.status !== "PREPARING" ? "" : "이 예약은 아직 ‘준비 중’으로 기록되어 있습니다. "
    + "이 앱이나 다른 앱 창에서 환경 준비가 아직 실행 중일 수 있습니다. 다른 앱 창을 모두 닫고 "
    + `${server}의 프로세스 목록에서 준비 명령이 실행 중이지 않음을 확인한 경우에만 진행하세요.\n`
    // A killed window can leave its sidecar running with the preparation lease.
    + "작업 관리자에서 factory-bridge.exe가 실행 중이 아닌지 확인하세요.\n";
  return preparing + `원래 GPU 서버(${server})에서 학습 환경 준비 프로세스가 종료된 것을 직접 확인했습니다.\n`
    + `예약 ${reservation.reservation_id}을 해제하면 이 서버에서 다시 준비하거나 학습할 수 있습니다. `
    + "준비가 아직 실행 중이면 해제하지 마세요. 같은 서버에서 작업이 겹칠 수 있습니다.";
}

/* Renderers: explicit arguments, every dynamic value escaped. */

const amount = (value: unknown) => typeof value === "number" ? value.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "—";
const gibibytes = (mib: unknown) => typeof mib === "number" ? (mib / 1024).toLocaleString("ko-KR", { maximumFractionDigits: 1 }) + " GiB" : "—";
/** A shortened GPU UUID or reservation ID with the full value in its title. */
export function shortIdentifier(value: string) {
  return `<span class="mono" title="${esc(value)}">${esc(value.length > 16 ? value.slice(0, 12) + "…" : value)}</span>`;
}
export function gpuSummary(inventory: Obj[]) {
  const names = new Map<string, number>();
  for (const gpu of inventory) names.set(gpu.name, (names.get(gpu.name) || 0) + 1);
  return `GPU ${inventory.length}개 · ${[...names].map(([name, count]) => count > 1 ? `${name} ×${count}` : name).join(", ")}`;
}
/** Plain-text label for the jobs table. */
export function jobDestination(job: Obj) {
  const destination = job.server_destination;
  if (destination?.invalid) return "승인 기록 손상";
  return destination ? "GPU 서버 · " + destination.ssh_host : "RunPod";
}
const authNames: Obj = { none: "인증 없음", oidc: "기관 인증(OIDC)", token: "발급 토큰" };
/** MLflow line of a connection report; older reports only carried mlflow_authenticated_read. */
export function mlflowStatus(report: Obj) {
  const read = report.mlflow_read ?? report.mlflow_authenticated_read;
  if (read === undefined) return "확인하지 않음 · 서버 주소 없음";
  if (read !== true) return "확인 필요";
  return authNames[report.mlflow_auth] ? "확인됨 · " + authNames[report.mlflow_auth] : "확인됨";
}
export function connectionResult(report: Obj | null) {
  if (!report) return "";
  const t = report.ssh_server?.transport;
  const rows = [["MLflow 조회", mlflowStatus(report)],
    t ? ["GPU 서버", `${sshAddress(t.ssh_username, t.ssh_host, t.ssh_port)} · ${gpuSummary(report.ssh_server.inventory || [])}`]
      : ["Pod 상태", report.runpod?.status || "조회하지 않음"],
    ["실제 GPU 학습", report.gpu_training_verified === true ? "확인됨" : "아직 확인하지 않음 · 조회만 확인"]];
  const row = ([name, value]: string[]) => `<div><dt>${esc(name)}</dt><dd title="${esc(value)}">${esc(value)}</dd></div>`;
  return `<section class="card"><h3>연결 검사 결과</h3><dl class="connection-list">${rows.map(row).join("")}</dl></section>`;
}
/** Observed GPUs with a checkbox per GPU; `checked` is the draft or saved selection. */
export function inventoryTable(inspection: Obj, checked: string[], saved: string[]) {
  const t = inspection.transport, inventory: Obj[] = inspection.inventory;
  const summary = [sshAddress(t.ssh_username, t.ssh_host, t.ssh_port), gpuSummary(inventory),
    "조회 " + formatDate(Date.parse(inspection.observed_at) / 1000), `시간당 $${amount(inspection.total_hourly_cost_usd)} (직접 입력한 비용)`];
  const missing = saved.filter(uuid => !inventory.some(gpu => gpu.uuid === uuid)).length;
  const row = (gpu: Obj) => `<tr><td><input type="checkbox" data-gpu="${esc(gpu.uuid)}" aria-label="${esc(gpu.uuid)} 학습에 사용"`
    + `${checked.includes(gpu.uuid) ? " checked" : ""}></td><td>${shortIdentifier(gpu.uuid)}</td><td>${esc(gpu.name)}</td><td>${esc(gibibytes(gpu.memory_mib))}</td></tr>`;
  return `<p class="muted">${esc(summary.join(" · "))}</p>`
    + `<p class="muted">현재 앱 정책은 작업당 GPU ${MAX_GPUS_PER_JOB}개입니다. 학습에 사용할 GPU를 골라 저장하세요.</p>`
    + (missing ? `<p class="warning-text">저장된 선택 중 ${missing}개 GPU가 이번 조회에 없습니다. 선택을 다시 저장하세요.</p>` : "")
    + '<div class="table-scroll"><table><thead><tr><th>사용</th><th>GPU UUID</th><th>이름</th><th>메모리</th></tr></thead>'
    + `<tbody>${inventory.map(row).join("")}</tbody></table></div>`;
}
/** Preparation reservations with a release button (data-action "release:<index>"). PREPARING ones are releasable
 * too: a killed app or sidecar leaves them behind, and the approval text warns that one may still be running. */
export function reservationTable(reservations: Obj[], settings: Obj, disabled: boolean) {
  if (!reservations.length) return '<p class="muted">진행 중이거나 확인이 필요한 환경 준비 예약이 없습니다.</p>';
  const release = (item: Obj, index: number) => {
    if (!["PREPARING", "UNCERTAIN"].includes(item.status)) return "";
    const releasable = reservationMatches(item, settings);
    return button("원격 준비 종료 확인 · 예약 해제", "release:" + index, "danger", disabled || !releasable)
      + (releasable ? "" : `<small>${OTHER_SETTINGS_RESERVATION}</small>`);
  };
  const row = (item: Obj, index: number) => {
    const t = item.profile?.transport || {};
    return `<tr><td>${shortIdentifier(String(item.reservation_id))}</td><td>${esc(sshAddress(t.ssh_username, t.ssh_host))}</td>`
      + `<td><span class="badge">${item.status === "UNCERTAIN" ? "종료 확인 필요" : "준비 중"}</span></td>`
      + `<td>${esc(formatDate(item.created))}</td><td>${release(item, index)}</td></tr>`;
  };
  return '<h3 class="subheading">환경 준비 예약</h3><p class="muted">‘종료 확인 필요’는 준비 명령이 끝났는지 앱이 확인하지 못한 예약입니다. '
    + "‘준비 중’이 계속 남아 있으면 앱이나 준비 과정이 중단되었을 수 있습니다. "
    + "원래 서버에서 준비 프로세스가 끝났는지 직접 확인한 뒤 해제하세요.</p>"
    + '<div class="table-scroll"><table><thead><tr><th>예약</th><th>서버</th><th>상태</th><th>시작 시각</th><th></th></tr></thead>'
    + `<tbody>${reservations.map(row).join("")}</tbody></table></div>`;
}
