// Executor profiles (10.4, D14): named GPU destinations kept by the native store (profiles.rs), apart from the legacy
// single connection. List, create, edit and delete go through the native commands, which validate every value and
// refuse an edit of a profile in use; their refusals are shown verbatim. Resource inspection and the SSH GPU inventory
// (9.8, instead of typed UUIDs) run only after a read-only notice; `observations_list` says when each profile was last
// checked. The controller bar (D9, D10) sits on top. A RunPod key exists only in its password input until a save reads
// it; it is never rendered, stored in this module, logged or shown.
import "./executor-profiles.css";
import { deleteExecutorProfile, executorProfiles, observationsList, pickFile, planning, profileInventory, saveExecutorProfile,
  type ObservationSummary, type ProfileInventory } from "../api";
import { attachControllerBar, controllerAction, controllerBarHtml, controllerBarState } from "../components/controller-bar";
import { localize } from "../issues";
import { createQueue } from "../poller";
import type { View, ViewContext } from "../view-types";
import { button, errorText, escapeHtml, field, focusKey, openDialog, restoreFocus, select, statusPill } from "../workbench";
import type { PublicProfile, ResourceFacts, Sha256, WorkflowIssue, WorkflowResult } from "../workflow-types";

/** = profiles.rs RUNPOD_PROFILE_FIELDS / SERVER_PROFILE_FIELDS: the only fields a save sends or a view shows. */
export const RUNPOD_FIELDS = ["provider", "name", "pod_id", "ssh_fingerprint", "ssh_key_file"] as const;
export const SERVER_FIELDS = ["provider", "name", "server_ssh_host", "server_ssh_port", "server_ssh_username",
  "server_ssh_fingerprint", "server_remote_root", "server_ssh_key_file", "server_total_hourly_cost_usd",
  "server_selected_gpu_uuids"] as const;
type Provider = "existing_runpod" | "ssh_server";
export interface ProfileDraft {
  profileId: string; isNew: boolean; provider: Provider;
  fields: Record<string, string>;
  /** Only ever set on the copy a save builds from the password input; the open draft keeps it empty. */
  apiKey: string;
  copyLegacy: boolean;
  /** Checked GPU UUIDs (root decision 2026-10-21: chosen from `executor_profile_inventory`, not typed lines). */
  gpus: string[];
}
/** A resource inspection of this app session (D14: session only; 10.6 reads it for its plan hints). */
export interface InspectionSummary { observedAt: string; facts: ResourceFacts; revision: Sha256 }

/** The public field values a save sends: the provider's allowlist only (trimmed), the GPU selection as a JSON array
 * string ("" for none, the saved text when the selection is unchanged), and `RUNPOD_API_KEY` only when one was typed
 * for a RunPod profile (an absent key keeps the saved one). */
export function profileValues(draft: ProfileDraft): Record<string, string> {
  const names: readonly string[] = draft.provider === "ssh_server" ? SERVER_FIELDS : RUNPOD_FIELDS;
  const values: Record<string, string> = {};
  for (const name of names) {
    if (name === "provider") values.provider = draft.provider;
    else if (name === "server_selected_gpu_uuids") values[name] = gpuSelection(draft.gpus, draft.fields[name] ?? "");
    else values[name] = String(draft.fields[name] ?? "").trim();
  }
  if (draft.provider === "existing_runpod" && draft.apiKey) values.RUNPOD_API_KEY = draft.apiKey;
  return values;
}
/** The UUIDs of a saved `server_selected_gpu_uuids` (a JSON array string, or "" before any selection). */
export function parseGpus(raw: string): string[] {
  try {
    const value = JSON.parse(raw);
    return Array.isArray(value) ? value.filter((item): item is string => typeof item === "string") : [];
  } catch { return []; }
}
function gpuSelection(selected: string[], stored: string): string {
  const chosen = [...new Set(selected.map(uuid => uuid.trim()).filter(Boolean))];
  const saved = parseGpus(stored);
  // An unchanged selection keeps its saved text, so a save that changes nothing keeps the profile revision.
  if (stored.trim() && saved.length === chosen.length && chosen.every(uuid => saved.includes(uuid))) return stored;
  return chosen.length ? JSON.stringify(chosen) : "";
}

// -- module state (session only) --
type StoredObservation = ObservationSummary | { profile_id: string; error: string };
let profiles: PublicProfile[] | null = null;
let listError: string | null = null;
let observations = new Map<string, StoredObservation>();
let observationsReadAt = 0;
let observationsNote: string | null = null;
let draft: ProfileDraft | null = null;
let affected: string[] | null = null;                  // runs to plan again after the last save or delete
const inspections = new Map<string, InspectionSummary & { receivedAt: number }>();
const inventories = new Map<string, ProfileInventory & { receivedAt: number }>();
let inspectUnavailable = false;
let inventoryUnavailable = false;
const queue = createQueue();                             // one sidecar call at a time on this page (D10)
let controller: ReturnType<typeof attachControllerBar> | null = null;

/** Session inspections by profile ID (read by 10.6 for "1시간 이내 확인 필요"). */
export function sessionInspections(): Readonly<Record<string, InspectionSummary>> {
  return Object.fromEntries([...inspections].map(([id, { observedAt, facts, revision }]) => [id, { observedAt, facts, revision }]));
}

const PROVIDERS: [Provider, string][] = [["existing_runpod", "RunPod · 배정받은 Pod"], ["ssh_server", "GPU 서버 · SSH 직접 실행"]];
const COST_SOURCES: Record<string, string> = { runpod_api: "RunPod API 요금", operator: "직접 입력한 서버 요금", estimate: "계획용 추정" };
const MAX_AGE_SECONDS = 3600;                            // a real plan accepts an observation at most this old (D13)
const providerOf = (profile: PublicProfile): Provider => profile.fields?.provider === "ssh_server" ? "ssh_server" : "existing_runpod";
const find = (profileId: string) => profiles?.find(profile => profile.profile_id === profileId);
const nameOf = (profile: PublicProfile | undefined, fallback: string) => profile?.fields?.name || fallback;
const notAvailable = (result: WorkflowResult<unknown>) => result.issues.some(issue => issue.code === "not_available");
const describe = (issues: WorkflowIssue[]) => issues.map(issue => {
  const { text, original } = localize(issue);
  return original ? `${text} (${original})` : text;
}).join("\n") || "요청을 처리하지 못했습니다.";
const usd = (value: unknown) => typeof value === "number" && Number.isFinite(value)
  ? `$${value.toLocaleString("en-US", { minimumFractionDigits: 2, maximumFractionDigits: 4 })}` : "—";
function ago(seconds: number): string {
  if (!Number.isFinite(seconds) || seconds < 60) return "방금";
  if (seconds < 3600) return `${Math.floor(seconds / 60)}분 전`;
  if (seconds < 86400) return `${Math.floor(seconds / 3600)}시간 전`;
  return `${Math.floor(seconds / 86400)}일 전`;
}
const gib = (mib: number) => `${Math.round(mib / 1024)} GiB`;
const focusTitle = () => document.getElementById("profile-form-title")?.focus();
const focusNew = () => document.querySelector<HTMLElement>('[data-action="wf:profiles:new"]')?.focus();

function draftFrom(profile: PublicProfile): ProfileDraft {
  const provider = providerOf(profile);
  const allowed: readonly string[] = provider === "ssh_server" ? SERVER_FIELDS : RUNPOD_FIELDS;
  const fields = Object.fromEntries(Object.entries(profile.fields ?? {})
    .filter(([name, value]) => allowed.includes(name) && typeof value === "string")) as Record<string, string>;
  return { profileId: profile.profile_id, isNew: false, provider, fields, apiKey: "", copyLegacy: false,
    gpus: parseGpus(fields.server_selected_gpu_uuids ?? "") };
}
const newDraft = (): ProfileDraft => ({ profileId: "", isNew: true, provider: "existing_runpod", fields: { server_ssh_port: "22" },
  apiKey: "", copyLegacy: false, gpus: [] });

// -- the list --
function location(profile: PublicProfile): string {
  const fields = profile.fields ?? {};
  if (providerOf(profile) === "ssh_server")
    return `${fields.server_ssh_username ? `${fields.server_ssh_username}@` : ""}${fields.server_ssh_host || "주소 미설정"}:${fields.server_ssh_port || "22"}`;
  return `Pod ${fields.pod_id || "미설정"}`;
}
function credentialCell(profile: PublicProfile): string {
  if (providerOf(profile) === "ssh_server") {
    const count = parseGpus(profile.fields?.server_selected_gpu_uuids ?? "").length;
    return count ? `선택 GPU ${count}개` : "GPU 선택 없음 · 편집에서 GPU 목록 확인";
  }
  return profile.RUNPOD_API_KEY_saved ? "API 키 보안 저장됨" : "API 키 없음";
}
function facts(gpuType: unknown, count: unknown, cost: unknown, source: string) {
  return `<b>${escapeHtml(`${String(gpuType ?? "GPU")} × ${String(count ?? "—")}`)}</b><small>시간당 ${escapeHtml(usd(cost))} · ${escapeHtml(COST_SOURCES[source] ?? source)}</small>`;
}
function checkCell(profile: PublicProfile): string {
  const id = profile.profile_id;
  const session = inspections.get(id);
  if (session) {
    const source = typeof session.facts.cost_source === "string" ? session.facts.cost_source : providerOf(profile) === "ssh_server" ? "operator" : "runpod_api";
    return `<span class="profile-when">${escapeHtml(ago((Date.now() - session.receivedAt) / 1000))} 확인</span>${facts(session.facts.gpu_type, session.facts.gpu_count, session.facts.total_hourly_cost_usd, source)}
      <small>실제 실행 계획에는 1시간 이내의 확인이 필요합니다.</small>`;
  }
  const stored = observations.get(id);
  if (!stored) return '<span class="muted">확인 기록 없음</span>';
  if ("error" in stored) return '<span class="warning-text">확인 기록을 읽을 수 없습니다 · 다시 확인하세요</span>';
  const age = stored.age_seconds + Math.max(0, Date.now() - observationsReadAt) / 1000;
  // `fresh_for_real_plan` is graph_plan's own decision (a pin for the current fields, at most an hour old).
  const usable = stored.fresh_for_real_plan && stored.profile_current !== false && age <= MAX_AGE_SECONDS;
  const freshness = stored.profile_current === false ? "확인 후 프로필이 바뀌어 다시 확인이 필요합니다"
    : usable ? "실제 실행 계획에 사용 가능 · 1시간 이내 확인"
    : age > MAX_AGE_SECONDS ? "1시간이 지나 실제 실행 계획 전에 다시 확인이 필요합니다"
    : "지금 프로필로는 실제 실행 계획에 쓸 수 없는 확인 기록입니다 · 다시 확인하세요";
  return `<span class="profile-when">${escapeHtml(ago(age))} 확인</span>${facts(stored.gpu_type, stored.gpu_count, stored.total_hourly_cost_usd, stored.cost_source)}
    <small class="${usable ? "" : "warning-text"}">${escapeHtml(freshness)}</small>`;
}
function profileRow(profile: PublicProfile): string {
  const id = profile.profile_id;
  const name = nameOf(profile, id);
  const ssh = providerOf(profile) === "ssh_server";
  // The SSH launch probe needs a saved GPU selection before it can observe (F22).
  const inspectable = !inspectUnavailable && (!ssh || parseGpus(profile.fields?.server_selected_gpu_uuids ?? "").length > 0);
  return `<tr${draft && !draft.isNew && draft.profileId === id ? ' class="current"' : ""}>
    <td><b>${escapeHtml(name)}</b><small class="mono">${escapeHtml(id)}</small></td>
    <td>${ssh ? "GPU 서버" : "RunPod"}</td><td class="profile-location">${escapeHtml(location(profile))}</td>
    <td>${escapeHtml(credentialCell(profile))}</td><td class="profile-check">${checkCell(profile)}</td>
    <td class="profile-row-actions"><button type="button" class="quiet" data-action="${escapeHtml(`wf:profiles:edit:${id}`)}" aria-label="${escapeHtml(name)} 편집">편집</button><button type="button" data-action="${escapeHtml(`wf:profiles:inspect:${id}`)}" aria-label="${escapeHtml(name)} 자원 확인"${inspectable ? "" : " disabled"}>자원 확인</button></td></tr>`;
}
function listCard(): string {
  let body: string;
  if (profiles === null) body = listError ? `<div class="wf-banner bad">실행 프로필을 불러오지 못했습니다. ${escapeHtml(listError)}</div>`
    : '<div class="empty">실행 프로필을 불러오는 중입니다.</div>';
  else if (!profiles.length) body = '<div class="empty">아직 저장한 실행 프로필이 없습니다. 배정받은 RunPod Pod나 GPU 서버를 이름 붙여 저장하세요.</div>';
  else body = `<div class="table-scroll"><table><thead><tr><th>실행 프로필</th><th>연결 대상</th><th>위치</th><th>GPU · 키</th><th>마지막 자원 확인</th><th></th></tr></thead><tbody>${profiles.map(profileRow).join("")}</tbody></table></div>`;
  const stale = profiles !== null && listError ? `<div class="wf-banner bad">목록을 다시 읽지 못했습니다. ${escapeHtml(listError)}</div>` : "";
  const replan = affected?.length ? `<div class="wf-banner warn profile-affected" role="status">이 변경 후 다시 계획해야 하는 실행: ${affected.map(escapeHtml).join(", ")}</div>` : "";
  return `<section class="card profile-list" data-tour="executor-profiles" aria-labelledby="profile-list-title">
    <div class="section-title"><h3 id="profile-list-title">실행 프로필 ${profiles ? `<span class="count">${profiles.length}</span>` : ""}</h3><div class="actions">${button("새 실행 프로필", "wf:profiles:new", "primary")}</div></div>
    <p>그래프 단계를 실행할 GPU 위치를 이름 붙여 저장합니다. 연결 설정의 단일 학습 연결과 따로 보관하며, 앱은 Pod나 서버를 시작·종료하지 않습니다.</p>
    ${replan}${stale}${body}${observationsNote ? `<p class="muted profile-observations-note">${escapeHtml(observationsNote)}</p>` : ""}</section>`;
}

// -- the form (create or edit one profile) --
function gpuSection(d: ProfileDraft): string {
  const inventory = inventories.get(d.profileId);
  const listed = d.isNew ? [] : inventory?.inventory ?? [];
  const extra = d.gpus.filter(uuid => !listed.some(gpu => gpu.uuid === uuid));
  const box = (uuid: string, label: string, hint: string) => `<label class="check profile-gpu"><input type="checkbox" data-profile-gpu="${escapeHtml(uuid)}" data-focus-key="${escapeHtml(`gpu-${uuid}`)}"${d.gpus.includes(uuid) ? " checked" : ""}><span><b>${escapeHtml(label)}</b> <span class="mono">${escapeHtml(uuid)}</span>${hint ? `<small>${escapeHtml(hint)}</small>` : ""}</span></label>`;
  const boxes = [...listed.map(gpu => box(gpu.uuid, `${gpu.name} · ${gib(gpu.memory_mib)}`, "")),
    ...extra.map(uuid => box(uuid, "저장된 선택", inventory ? "이번 목록에 없는 GPU입니다. 선택을 해제하고 저장하세요." : "GPU 목록을 확인하면 이름과 메모리를 보여 줍니다."))];
  const stamp = inventory && !d.isNew ? `<p class="muted">${escapeHtml(ago((Date.now() - inventory.receivedAt) / 1000))} 확인한 목록 · GPU ${listed.length}개 · 저장된 연결 정보 기준</p>` : "";
  return `<fieldset class="profile-gpus"><legend>사용할 GPU · 최대 8개</legend>
    <p class="muted">"GPU 목록 확인"은 저장된 연결 정보로 서버에 접속해 목록만 읽습니다. 사용할 GPU를 고른 뒤 저장하세요. 자원 확인과 실행 계획에는 저장된 선택이 필요합니다.</p>
    ${boxes.length ? `<div class="profile-gpu-list">${boxes.join("")}</div>` : '<p class="muted">아직 확인한 GPU 목록이 없습니다.</p>'}${stamp}
    <div class="actions">${button("GPU 목록 확인", "wf:profiles:inventory", "", d.isNew || inventoryUnavailable)}</div>
    ${d.isNew ? '<p class="muted">먼저 연결 정보를 저장한 뒤 GPU 목록을 확인하세요.</p>' : ""}</fieldset>`;
}
function profileForm(ctx: ViewContext, d: ProfileDraft): string {
  const profile = d.isNew ? undefined : find(d.profileId);
  const ssh = d.provider === "ssh_server";
  const input = (label: string, key: string, help = "", max = 2048) =>
    field(label, key, d.fields[key] ?? "", "text", escapeHtml(help)).replace("<input ", `<input id="profile-field-${key}" maxlength="${max}" spellcheck="false" `);
  const keyFile = (key: string) => `${input("SSH 개인키 파일", key, "이 PC의 전체 경로(예: C:\\Users\\이름\\.ssh\\id_ed25519)로 입력하세요. 키 내용은 입력하지 마세요.")}
    <div class="field"><span>키 파일 선택</span>${button("파일 찾기", "wf:profiles:pick-key")}</div>`;
  const idField = `<label class="field"><span>프로필 ID</span><input id="profile-id" aria-label="프로필 ID" maxlength="64" autocomplete="off" spellcheck="false" value="${escapeHtml(d.profileId)}"${d.isNew ? "" : " disabled"}><small>${d.isNew ? "영문·숫자·_·-·. 64자 이내 · 그래프 단계와 실행 계획이 이 ID로 가리킵니다." : "저장한 뒤에는 바꿀 수 없습니다."}</small></label>`;
  const provider = select("연결 대상", "provider", d.provider, PROVIDERS).replace("<select ", `<select id="profile-provider"${d.isNew ? "" : " disabled"} `);
  const secretHelp = profile?.RUNPOD_API_KEY_saved ? "보안 저장됨 · 변경할 때만 입력" : "이 프로필 전용으로 Windows 자격 증명 저장소에 보관합니다.";
  const secret = `<label class="field"><span>RunPod API 키</span><input id="profile-runpod-key" aria-label="RunPod API 키" data-profile-secret type="password" autocomplete="new-password" spellcheck="false" maxlength="2048" value=""><small>${secretHelp}<br>키를 저장하거나 바꾼 뒤에는 컨트롤러를 다시 시작해야 새 키를 씁니다.</small></label>`;
  const copy = ctx.settings.RUNPOD_API_KEY_saved
    ? `<label class="check"><input type="checkbox" id="profile-copy-legacy" data-profile-copy${d.copyLegacy ? " checked" : ""}> 연결 설정에 저장된 RunPod 키를 이 프로필에도 저장합니다 (키를 입력하지 않을 때만)</label>` : "";
  const runpod = `${input("Pod ID", "pod_id")}${input("SSH 서버 지문", "ssh_fingerprint", "신뢰할 수 있는 경로로 확인한 SHA256:… 지문을 입력하세요.")}${keyFile("ssh_key_file")}${secret}`;
  const server = `${input("서버 주소", "server_ssh_host", "예: gpu.example.org 또는 IP 주소")}${input("SSH 포트", "server_ssh_port")}
    ${input("접속 계정", "server_ssh_username", "root 계정이 아니어도 됩니다.")}${input("SSH 서버 지문", "server_ssh_fingerprint", "관리자가 신뢰할 수 있는 경로로 전달한 SHA256:… 값만 입력하세요.")}
    ${input("작업 폴더 · 절대 경로", "server_remote_root", "예: /home/USERNAME/factory")}${keyFile("server_ssh_key_file")}
    ${input("시간당 비용 (USD) · 서버 전체", "server_total_hourly_cost_usd", "내부 서버는 0을 입력하세요. 요금 조회 결과가 아니라 직접 입력한 값입니다.")}`;
  const state = d.isNew ? statusPill("neutral", "아직 저장하지 않음") : statusPill("neutral", `저장됨 · ${d.profileId}`);
  return `<form id="profile-form" class="card profile-form" novalidate aria-labelledby="profile-form-title">
    <div class="section-title"><h3 id="profile-form-title" tabindex="-1">${d.isNew ? "새 실행 프로필" : escapeHtml(nameOf(profile, d.profileId))}</h3>${state}</div>
    <fieldset class="profile-fieldset"${ctx.busy ? " disabled" : ""}><div class="grid">${idField}${provider}${input("이름", "name", "목록과 실행 계획에 표시할 이름 · 200자 이내", 200)}${ssh ? server : runpod}</div>
    ${ssh ? gpuSection(d) : copy}</fieldset>
    <p class="muted profile-note">${ssh ? "앱은 서버를 켜거나 끄지 않으며 서버의 시스템 패키지를 바꾸지 않습니다." : "앱은 기존 Pod를 자동으로 시작·종료·삭제하지 않습니다."} 키 파일은 경로만 저장합니다. 앱은 값을 검사해 맞지 않거나 사용 중인 실행 프로필의 변경을 거부합니다.</p>
    <div class="actions"><button type="submit" class="primary" data-focus-key="profile-save">저장</button>${d.isNew ? "" : button("삭제", "wf:profiles:delete", "danger")}${button("닫기", "wf:profiles:close", "quiet")}</div></form>`;
}

/** Reads the open form into the draft (never the RunPod key: it stays in its password input). */
function captureForm() {
  const form = document.querySelector<HTMLFormElement>("#profile-form");
  const d = draft;
  if (!form || !d) return;
  if (d.isNew) {
    const id = form.querySelector<HTMLInputElement>("#profile-id");
    if (id) d.profileId = id.value;
    const provider = form.querySelector<HTMLSelectElement>("#profile-provider");
    if (provider) d.provider = provider.value === "ssh_server" ? "ssh_server" : "existing_runpod";
  }
  form.querySelectorAll<HTMLInputElement>("input[data-key]").forEach(input => { d.fields[input.dataset.key!] = input.value; });
  if (form.querySelector(".profile-gpus"))
    d.gpus = [...form.querySelectorAll<HTMLInputElement>("[data-profile-gpu]")].filter(box => box.checked).map(box => box.dataset.profileGpu!);
  const copy = form.querySelector<HTMLInputElement>("[data-profile-copy]");
  if (copy) d.copyLegacy = copy.checked;
}

// -- reads --
async function reloadProfiles() {
  try {
    const result = await executorProfiles();
    profiles = Array.isArray(result?.profiles) ? result.profiles : [];
    listError = null;
  } catch (error) { listError = errorText(error); }
}
async function reloadObservations() {
  try {
    const result = await queue.run(() => observationsList());
    if (result.status === "ok" && result.data && Array.isArray(result.data.observations)) {
      observations = new Map(result.data.observations.map(entry => [entry.profile_id, entry]));
      observationsReadAt = Date.now(); observationsNote = null;
    } else observationsNote = `자원 확인 기록을 불러오지 못했습니다. ${describe(result.issues)}`;
  } catch (error) { observationsNote = `자원 확인 기록을 불러오지 못했습니다. ${errorText(error)}`; }
}

// -- actions --
function openDraft(ctx: ViewContext, next: ProfileDraft) {
  affected = null;
  draft = next;
  ctx.render(); focusTitle();
}

async function saveProfile(ctx: ViewContext) {
  captureForm();
  const d = draft;
  if (!d) return;
  // The key is read once, here, and leaves the page with this call; the input is emptied at once.
  const keyInput = document.querySelector<HTMLInputElement>("#profile-form [data-profile-secret]");
  const apiKey = d.provider === "existing_runpod" ? (keyInput?.value ?? "").trim() : "";
  if (keyInput) keyInput.value = "";
  const profileId = d.profileId.trim();
  const copy = d.provider === "existing_runpod" && d.copyLegacy;
  const values = profileValues({ ...d, apiKey });
  const focus = focusKey(document.activeElement);
  affected = null;
  await ctx.task("실행 프로필 저장 중", async () => {
    const result = await saveExecutorProfile(profileId, values, copy);
    ctx.store.markProfilesChanged();
    affected = Array.isArray(result?.affected_runs) && result.affected_runs.length ? result.affected_runs.map(String) : null;
    // A running controller keeps the key it started with (9.4 review): a new key needs a restart polling cannot see.
    const restart = Boolean(apiKey || copy) && controllerBarState().status?.controller.state === "running";
    d.profileId = profileId; d.isNew = false; d.copyLegacy = false;
    await reloadProfiles();
    const reloaded = find(profileId) ?? result?.profile;
    if (draft === d && reloaded) draft = draftFrom(reloaded);
    ctx.notify(restart ? "실행 프로필을 저장했습니다. 동작 중인 컨트롤러는 시작할 때의 키를 계속 씁니다. 새 키를 쓰려면 컨트롤러를 다시 시작하세요."
      : "실행 프로필을 저장했습니다.");
  });
  restoreFocus(document, focus);
  if (!document.activeElement || document.activeElement === document.body) restoreFocus(document, "profile-save");
}

async function deleteProfile(ctx: ViewContext) {
  captureForm();
  const d = draft;
  if (!d || d.isNew) return;
  const profileId = d.profileId;
  const name = nameOf(find(profileId), profileId);
  const answer = await openDialog({ title: "실행 프로필 삭제",
    bodyHtml: `<p>${escapeHtml(name)} (${escapeHtml(profileId)}) 실행 프로필과 이 프로필에 보안 저장된 RunPod API 키를 삭제합니다. Pod나 서버는 바뀌지 않습니다.</p><p>사용 중인 실행이 있으면 앱이 삭제를 거부합니다. 이 프로필을 쓰던 실행 계획은 다시 계획해야 합니다.</p>`,
    actions: [{ id: "back", label: "돌아가기" }, { id: "delete", label: "삭제", primary: true }] });
  if (answer?.action !== "delete") return;
  let deleted = false;
  affected = null;
  await ctx.task("실행 프로필 삭제 중", async () => {
    const result = await deleteExecutorProfile(profileId);
    ctx.store.markProfilesChanged();
    affected = Array.isArray(result?.affected_runs) && result.affected_runs.length ? result.affected_runs.map(String) : null;
    inspections.delete(profileId); inventories.delete(profileId);
    if (draft === d) draft = null;
    deleted = true;
    await reloadProfiles();
    ctx.notify("실행 프로필을 삭제했습니다.");
  });
  if (deleted) focusNew();
}

async function inspectProfile(ctx: ViewContext, profileId: string) {
  const profile = find(profileId);
  if (!profile) return;
  const name = nameOf(profile, profileId);
  const target = providerOf(profile) === "ssh_server"
    ? "<p>저장된 SSH 연결 정보로 서버에 접속해 선택한 GPU와 실행 환경을 읽기만 합니다. 서버의 파일·패키지를 바꾸지 않고 데이터를 보내지 않습니다.</p>"
    : "<p>RunPod API로 Pod 상태를 읽기만 합니다. Pod를 시작·중지·삭제하지 않고 데이터를 보내지 않습니다. 이 프로필에 보안 저장된 RunPod API 키를 씁니다.</p>";
  const answer = await openDialog({ title: `자원 확인 · ${name}`,
    bodyHtml: `${target}<p>확인한 GPU와 요금은 실행 계획에 쓰도록 이 PC에 기록됩니다. 실제 실행 계획에는 1시간 이내의 확인이 필요합니다.</p>`,
    actions: [{ id: "back", label: "돌아가기" }, { id: "inspect", label: "읽기 전용으로 확인", primary: true }] });
  if (answer?.action !== "inspect") return;
  await ctx.task("자원 확인 중", async () => {
    const result = await queue.run(() => planning.inspect(profileId));
    if (result.status === "ok" && result.data?.facts) {
      inspections.set(profileId, { observedAt: result.data.observed_at, facts: result.data.facts, revision: result.data.revision, receivedAt: Date.now() });
      ctx.notify(`${name} 자원을 확인했습니다.`);
      return;
    }
    if (notAvailable(result)) inspectUnavailable = true;
    throw new Error(describe(result.issues));
  });
}

async function listGpus(ctx: ViewContext) {
  captureForm();
  const d = draft;
  if (!d || d.isNew || d.provider !== "ssh_server") return;
  const profileId = d.profileId;
  const answer = await openDialog({ title: "GPU 목록 확인",
    bodyHtml: "<p>저장된 SSH 연결 정보로 서버에 접속해 GPU 목록(nvidia-smi 조회)을 읽기만 합니다. 서버의 파일·패키지를 바꾸지 않고 아무것도 저장하지 않습니다.</p><p>폼에서 아직 저장하지 않은 연결 정보 변경은 쓰지 않습니다.</p>",
    actions: [{ id: "back", label: "돌아가기" }, { id: "list", label: "읽기 전용으로 확인", primary: true }] });
  if (answer?.action !== "list") return;
  await ctx.task("GPU 목록 확인 중", async () => {
    const result = await queue.run(() => profileInventory(profileId));
    if (result.status === "ok" && result.data && Array.isArray(result.data.inventory)) {
      inventories.set(profileId, { ...result.data, receivedAt: Date.now() });
      ctx.notify("GPU 목록을 불러왔습니다. 사용할 GPU를 선택한 뒤 저장하세요.");
      return;
    }
    if (notAvailable(result)) inventoryUnavailable = true;
    throw new Error(describe(result.issues));
  });
}

/** The native file picker for the key path: the chosen path goes into the input directly (no re-render). */
async function pickKey(ctx: ViewContext) {
  const d = draft;
  if (!d) return;
  const name = d.provider === "ssh_server" ? "server_ssh_key_file" : "ssh_key_file";
  let path: string | null;
  try { path = await pickFile("key"); }
  catch (error) { ctx.fail(errorText(error)); return; }
  if (!path || draft !== d) return;
  d.fields[name] = path;
  const input = document.querySelector<HTMLInputElement>(`#profile-form [data-key="${name}"]`);
  if (input) { input.value = path; input.focus(); }
}

export const view: View = {
  id: "profiles", title: "실행 프로필", description: "그래프 단계를 실행할 GPU 위치(RunPod Pod·GPU 서버)와 백그라운드 컨트롤러를 관리합니다.",
  nav: { group: "settings", label: "실행 프로필", icon: "profiles", order: 20 },
  render(ctx) {
    return `${controllerBarHtml(controllerBarState(), ctx.busy, "profiles")}<div class="profiles-layout">${listCard()}${draft ? profileForm(ctx, draft) : ""}</div>`;
  },
  bind(ctx) {
    if (ctx.page !== "profiles") return;
    document.querySelector<HTMLFormElement>("#profile-form")?.addEventListener("submit", event => {
      event.preventDefault();
      if (!ctx.busy) void saveProfile(ctx).catch(error => ctx.fail(errorText(error)));
    });
    // The provider of a new profile decides which fields exist: re-render and keep the focus on the choice.
    document.querySelector<HTMLSelectElement>("#profile-provider")?.addEventListener("change", () => {
      captureForm(); ctx.render();
      document.querySelector<HTMLSelectElement>("#profile-provider")?.focus();
    });
  },
  capture: () => captureForm(),
  async enter(ctx) {
    controller?.detach();
    const attached = controller = attachControllerBar(ctx, { host: "profiles", queue, profiles: () => profiles });
    // Read-only only (D3): the native list, the stored observations and the controller status.
    await ctx.task("실행 프로필 불러오는 중", async () => {
      await reloadProfiles();
      await reloadObservations();
      await attached.refresh();
    });
    if (controller === attached && ctx.page === "profiles") attached.poller.start();
  },
  leave() { controller?.detach(); controller = null; },
  async action(ctx, verb, arg) {
    if (await controllerAction(ctx, verb)) return;
    if (verb === "new") openDraft(ctx, newDraft());
    else if (verb === "edit" && arg) {
      const profile = find(arg);
      if (profile) openDraft(ctx, draft && !draft.isNew && draft.profileId === arg ? draft : draftFrom(profile));
    }
    else if (verb === "close") { draft = null; ctx.render(); focusNew(); }
    else if (verb === "save") await saveProfile(ctx);
    else if (verb === "delete") await deleteProfile(ctx);
    else if (verb === "inspect" && arg) await inspectProfile(ctx, arg);
    else if (verb === "inventory") await listGpus(ctx);
    else if (verb === "pick-key") await pickKey(ctx);
  },
};
