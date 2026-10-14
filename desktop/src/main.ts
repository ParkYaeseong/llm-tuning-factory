import { invoke } from "@tauri-apps/api/core";
import "./style.css";

type Obj = Record<string, any>;
const root = document.querySelector<HTMLDivElement>("#app")!;
const state: Obj = {
  page: "overview", settings: {}, catalog: { templates: [], profiles: [], datasets: [] },
  config: null, jobs: [], plan: null, logs: "", experiments: [], runs: [], nextToken: null,
  selected: [], experiment: "16", busy: "", notice: "", error: "", active: "", comparison: null,
};
const pages: Record<string, string> = {
  overview: "작업 공간", connections: "연결 설정", datasets: "데이터", training: "학습 설정",
  jobs: "실행 관리", history: "기록 비교",
};
const defaults: Obj = {
  MLFLOW_TRACKING_URI: "https://mlflow.kbiofoundry.kr",
  FACTORY_MLFLOW_ARTIFACT_LOCATION: "mlflow-artifacts:/llm-tuning-factory",
  MLFLOW_OIDC_TOKEN_URL: "https://sso.k-biofoundrycopilot.duckdns.org/realms/kbf/protocol/openid-connect/token",
  MLFLOW_OIDC_CLIENT_ID: "mlflow", auth_mode: "oidc",
};
const esc = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const num = (v: unknown) => typeof v === "number" ? v.toLocaleString("ko-KR", { maximumFractionDigits: 5 }) : "—";
const statusName: Obj = { QUEUED: "대기", PENDING: "준비", PROVISIONING: "전송 준비", RUNNING: "학습 중",
  EVALUATING: "평가 중", COMPLETED: "완료", FINISHED: "완료", FAILED: "실패", CANCELED: "취소", KILLED: "취소" };
function badge(status: string) { return `<span class="badge ${/COMPLETED|FINISHED/.test(status) ? "green" : /FAILED/.test(status) ? "red" : ""}">${esc(statusName[status] || status)}</span>`; }
const button = (label: string, action: string, cls = "") => `<button type="button" class="${cls}" data-action="${action}">${label}</button>`;
function field(label: string, key: string, value: unknown = "", type = "text", help = "") {
  return `<label class="field"><span>${label}</span><input data-key="${key}" type="${type}" value="${esc(value)}" ${type === "password" ? 'autocomplete="new-password"' : ""}>${help ? `<small>${help}</small>` : ""}</label>`;
}
function select(label: string, key: string, value: string, options: [string, string][]) {
  return `<label class="field"><span>${label}</span><select data-key="${key}">${options.map(([id, title]) => `<option value="${esc(id)}" ${id === value ? "selected" : ""}>${esc(title)}</option>`).join("")}</select></label>`;
}
async function api(action: string, data: Obj = {}) {
  return invoke<any>("factory_action", { action, data });
}
async function task(label: string, operation: () => Promise<void>) {
  state.error = ""; state.notice = ""; state.busy = label; render();
  try { await operation(); }
  catch (error) { state.error = String(error); }
  finally { state.busy = ""; render(); }
}
function captureConfig() {
  if (!state.config) return;
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-config]").forEach(el => {
    const parts = el.dataset.config!.split(".");
    let target = state.config;
    for (const part of parts.slice(0, -1)) target = target[part];
    target[parts.at(-1)!] = el.dataset.number ? Number(el.value) : el.value;
  });
}
function configField(label: string, path: string, type = "text", help = "") {
  let v = state.config; for (const p of path.split(".")) v = v[p];
  return field(label, path, v, type, help).replace('data-key=', 'data-config=')
    .replace("<input ", `<input ${type === "number" ? 'data-number="true" step="any" ' : ""}`);
}
function configSelect(label: string, path: string, options: [string, string][]) {
  let v = state.config; for (const p of path.split(".")) v = v[p];
  return select(label, path, v, options).replace("data-key=", "data-config=");
}
async function approve(title: string, body: string): Promise<boolean> {
  const dialog = document.createElement("dialog");
  dialog.innerHTML = `<h2>${esc(title)}</h2><p class="confirmation">${esc(body)}</p><div class="actions"><button data-no>돌아가기</button><button data-yes class="primary">내용 확인 · 진행</button></div>`;
  document.body.append(dialog); dialog.showModal();
  return new Promise(resolve => {
    const done = (answer: boolean) => { dialog.close(); dialog.remove(); resolve(answer); };
    dialog.querySelector("[data-no]")!.addEventListener("click", () => done(false));
    dialog.querySelector("[data-yes]")!.addEventListener("click", () => done(true));
    dialog.addEventListener("cancel", e => { e.preventDefault(); done(false); });
  });
}
function overview() {
  return `<section class="hero"><div><span class="eyebrow">YOUR TRAINING WORKSPACE</span><h2>업무에 맞는 AI를,<br>같은 기준으로 학습하세요.</h2><p>데이터와 설정은 내 PC에서 관리하고,<br>GPU 학습은 RunPod에서, 실행 기록은 MLflow에서 함께 확인합니다.</p>${button("학습 준비하기 →", "nav:training", "primary")}</div><div class="pipeline"><div><b>01</b><span>내 PC<small>데이터 · 설정 · 작업 관리</small></span></div><i>↓</i><div><b>02</b><span>RunPod GPU<small>LLaMA-Factory 학습 · 평가</small></span></div><i>↓</i><div><b>03</b><span>공용 MLflow<small>실행 기록 · 결과 비교</small></span></div></div></section>
  <div class="stats"><article><small>등록한 데이터 버전</small><strong>${state.catalog.datasets.length}</strong></article><article><small>저장한 업무 AI</small><strong>${state.catalog.profiles.length}</strong></article><article><small>이 PC의 최근 작업</small><strong>${state.jobs.length}</strong></article></div>
  <section class="card"><h3>처음 사용하는 순서</h3><div class="steps"><div><b>1. 연결 설정</b><p>RunPod · MLflow 인증과 SSH 키를 등록하고 GPU 환경을 준비합니다.</p></div><div><b>2. 데이터와 학습 설정</b><p>데이터를 등록하고 업무별 모델과 검증 기준을 선택합니다.</p></div><div><b>3. 계획 확인 후 실행</b><p>GPU와 예상 비용, 전송할 데이터를 확인한 뒤 학습을 시작합니다.</p></div></div></section>
  <p class="muted">개인별 Pod를 사용하세요. 여러 PC 사이의 GPU 배정은 공유되지 않습니다. 실제 실행 전까지 GPU 작업을 시작하지 않습니다.</p>`;
}
function connections() {
  const s = { ...defaults, ...state.settings };
  return `<form id="settings-form"><section class="card"><div class="section-title"><h3>RunPod 연결</h3><span class="badge">기존 Pod 사용</span></div><div class="grid">
  ${field("RunPod API 키", "RUNPOD_API_KEY", "", "password", s.RUNPOD_API_KEY_saved ? "보안 저장됨 · 변경할 때만 입력" : "API 키는 Windows 자격 증명 저장소에 보관합니다.")}
  ${field("Pod ID", "pod_id", s.pod_id)}
  ${field("SSH 개인키 파일", "ssh_key_file", s.ssh_key_file, "text", "개인키 내용을 입력하지 않고 파일 경로를 선택합니다.")}
  <div class="field"><span>키 파일 선택</span>${button("파일 찾기", "pick:key")}</div>
  ${field("SSH 서버 지문", "ssh_fingerprint", s.ssh_fingerprint, "text", "신뢰할 수 있는 경로로 확인한 SHA256:… 지문을 입력하세요.")}
  ${field("기록에 표시할 이름", "FACTORY_OPERATOR", s.FACTORY_OPERATOR)}
  </div><p class="muted">Pod는 RunPod에서 직접 시작합니다. 앱은 기존 Pod를 자동 시작·종료·삭제하지 않습니다.</p></section>
  <section class="card"><h3>공용 MLflow 연결</h3><div class="grid">
  ${field("서버 주소", "MLFLOW_TRACKING_URI", s.MLFLOW_TRACKING_URI)}
  ${field("산출물 저장 위치", "FACTORY_MLFLOW_ARTIFACT_LOCATION", s.FACTORY_MLFLOW_ARTIFACT_LOCATION)}
  ${select("인증 방식", "auth_mode", s.auth_mode, [["oidc", "기관 인증 (OIDC)"], ["token", "발급받은 토큰"]])}
  ${field("인증 서버 주소", "MLFLOW_OIDC_TOKEN_URL", s.MLFLOW_OIDC_TOKEN_URL)}
  ${field("클라이언트 ID", "MLFLOW_OIDC_CLIENT_ID", s.MLFLOW_OIDC_CLIENT_ID)}
  ${field("클라이언트 인증값", "MLFLOW_OIDC_CLIENT_SECRET", "", "password", s.MLFLOW_OIDC_CLIENT_SECRET_saved ? "보안 저장됨" : "관리자에게 별도로 전달받으세요.")}
  ${field("별도 발급 토큰", "MLFLOW_TRACKING_TOKEN", "", "password", s.MLFLOW_TRACKING_TOKEN_saved ? "보안 저장됨" : "토큰 인증 방식을 선택한 경우에만 입력")}
  </div><div class="actions"><button class="primary" type="submit">연결 정보 저장</button>${button("연결 검사", "check")}${button("저장된 인증값 삭제", "forget", "danger")}</div></section></form>
  <section class="card"><h3>GPU 학습 환경</h3><p>선택한 Pod의 별도 폴더에 이 앱과 일치하는 학습 엔진을 설치합니다. 첫 준비는 다운로드 용량에 따라 시간이 걸립니다.</p>
  <div class="actions">${button("환경 확인", "runtime_check")}${button("학습 환경 준비", "runtime_prepare", "primary")}${button("GPU 정보 → 학습 설정에 적용", "apply-pod")}</div></section>`;
}
function datasets() {
  return `<section class="card"><h3>새 데이터 버전 등록</h3><p>Alpaca 또는 ShareGPT 형식의 JSON·JSONL을 등록합니다. 등록 시 로컬 사본과 해시를 보존하며, 같은 버전은 덮어쓰지 않습니다.</p>
  <form id="dataset-form"><div class="grid">${field("데이터 ID", "dataset_id", "my_dataset")}${field("버전", "version", "1")}
  ${field("파일 경로", "path", "")}<div class="field"><span>데이터 선택</span>${button("파일 찾기", "pick:dataset")}</div></div>
  <label class="check"><input name="synthetic" type="checkbox"> 더미·합성 데이터입니다</label>
  <label class="check"><input name="export_allowed" type="checkbox"> 이 데이터를 선택한 GPU로 전송해도 됩니다</label>
  <p class="muted">여기서는 PC에만 등록합니다. 실제 전송은 학습 실행 시 별도로 확인합니다.</p><button class="primary" type="submit">검사하고 등록</button></form></section>
  <section class="card"><h3>등록한 버전</h3>${state.catalog.datasets.length ? `<table><thead><tr><th>데이터</th><th>버전</th><th>건수</th><th>구분</th><th>GPU 전송</th></tr></thead><tbody>${state.catalog.datasets.map((d: Obj) => `<tr><td>${esc(d.dataset_id)}</td><td>${esc(d.version)}</td><td>${num(d.rows)}</td><td>${d.synthetic ? "합성" : "사용자 데이터"}</td><td>${d.export_allowed ? "허용" : "미허용"}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">아직 등록한 데이터가 없습니다.</div>'}</section>`;
}
function training() {
  if (!state.config) return '<section class="card">학습 설정을 불러오는 중입니다.</section>';
  const c = state.config;
  const datasetOptions: [string, string][] = [["", "선택해 주세요"], ...state.catalog.datasets.map((d: Obj) => [d.dataset_id + ":" + d.version, d.dataset_id + " · v" + d.version])];
  return `<form id="training-form"><section class="card"><div class="section-title"><h3>업무 AI 설정</h3>${button("설정 저장", "save-profile")}</div><div class="grid">
  ${select("예제·저장한 설정 불러오기", "template", "", [["", "현재 설정"], ...state.catalog.templates.map((t: Obj, i: number) => ["t:" + i, t.name]), ...state.catalog.profiles.map((p: Obj, i: number) => ["p:" + i, "저장됨 · " + p.ai_id])])}
  ${configField("업무 AI ID", "ai_id")}${configField("업무 흐름 ID", "workflow_id")}
  ${select("학습 데이터", "train_dataset", c.dataset_id + ":" + c.dataset_version, datasetOptions)}
  ${select("검증 데이터", "validation_dataset", c.evaluation_profile.dataset_id ? c.evaluation_profile.dataset_id + ":" + c.evaluation_profile.dataset_version : "", [["", "없음 · 학습 완료만 확인"], ...datasetOptions.slice(1)])}
  ${configSelect("평가 방식", "evaluation_profile.kind", [["pipeline", "학습 완료 확인"], ["json", "JSON 형식"], ["classification", "분류"], ["extraction", "정보 추출"], ["kbf_extraction", "KBF 추출"]])}
  </div></section><section class="card"><h3>모델과 학습</h3><div class="grid">
  ${configSelect("기반 모델", "base_model", ["Qwen/Qwen2.5-0.5B-Instruct", "Qwen/Qwen2.5-1.5B-Instruct", "Qwen/Qwen2.5-7B-Instruct", "meta-llama/Llama-3.1-8B-Instruct", "meta-llama/Llama-3.2-3B-Instruct"].map(x => [x, x]))}
  ${configSelect("학습 방법", "training_method", [["lora", "LoRA"], ["qlora", "QLoRA · 실제 GPU 검증 대기"]])}
  ${configField("학습 횟수 (epoch)", "hyperparameters.num_train_epochs", "number")}
  ${configField("학습률", "hyperparameters.learning_rate", "number")}
  ${configField("배치 크기", "hyperparameters.per_device_train_batch_size", "number")}
  ${configField("최대 토큰 길이", "hyperparameters.cutoff_len", "number")}
  </div><div class="actions">${button("선택한 모델 버전 확인·고정", "model_revision")}</div><p class="muted">모델을 변경하면 버전 확인을 눌러 주세요. 최초에는 공개 Qwen 모델을 권장합니다. Llama 모델은 이용 승인과 GPU의 사전 다운로드가 필요합니다.</p><details><summary>고급 설정</summary><div class="grid">
  ${configField("모델 revision · 40자리 commit", "model_revision")}
  ${configField("토크나이저 revision · 모델과 일치", "tokenizer_revision")}
  ${configField("모델 템플릿 (qwen / llama3)", "template")}
  ${configSelect("정밀도", "hyperparameters.precision", [["bf16", "BF16 · 권장"], ["fp16", "FP16"]])}
  ${configField("최대 step (-1은 epoch 기준)", "hyperparameters.max_steps", "number")}
  ${configField("기울기 누적", "hyperparameters.gradient_accumulation_steps", "number")}
  ${configField("LoRA rank", "hyperparameters.lora_rank", "number")}
  ${configField("무작위 시드", "hyperparameters.seed", "number")}
  ${configSelect("LoRA 대상", "hyperparameters.lora_target", [["all", "전체"], ["q_proj,v_proj", "q_proj · v_proj"]])}
  ${configField("체크포인트 저장 간격 (step)", "hyperparameters.save_steps", "number")}
  ${configField("체크포인트 보관 수", "hyperparameters.save_total_limit", "number")}
  ${configField("검증 최대 건수", "evaluation_profile.max_samples", "number")}
  ${configField("답변 최대 토큰", "evaluation_profile.max_new_tokens", "number")}
  </div></details></section><section class="card"><h3>GPU와 실행 한도</h3><div class="grid">
  ${configField("GPU 종류 · 연결 설정에서 가져오기", "resource_profile.gpu_type")}
  ${configField("GPU 수", "resource_profile.gpu_count", "number")}
  ${configField("GPU 한 개당 시간 요금 (USD)", "resource_profile.hourly_cost_usd", "number")}
  ${configField("최대 실행 시간 (초)", "resource_profile.timeout_seconds", "number")}
  ${configField("최대 예상 GPU 비용 (USD)", "resource_profile.max_cost_usd", "number")}
  </div><p class="muted">현재 앱 정책은 동시 GPU 1개, 최대 4시간·예상 비용 $5입니다. 기존 Pod의 실제 청구를 차단하는 한도는 아닙니다.</p>
  <div class="actions"><button class="primary" type="submit">실행 계획 확인</button>${button("설정 저장", "save-profile")}</div></section></form>
  ${state.plan ? `<section class="card plan"><h3>실행 전 확인</h3><div class="stats"><article><small>업무 AI</small><b>${esc(state.plan.manifest.config.ai_id)}</b></article><article><small>학습 데이터</small><b>${esc(state.plan.manifest.dataset.dataset_id)} · ${num(state.plan.manifest.dataset.rows)}건</b></article><article><small>최대 시간 기준 예상 GPU 비용</small><b>$${num(state.plan.estimated_cost_usd)}</b></article></div>
  <p>${esc(state.plan.note)}</p><p class="muted">Pod: ${esc(state.settings.pod_id || "미설정")} · GPU: ${esc(state.plan.manifest.config.resource_profile.gpu_type)}</p>
  ${button("데이터 전송에 동의하고 학습 시작", "start", "primary")}
  <details><summary>확정된 설정 보기</summary><pre>${esc(JSON.stringify(state.plan.manifest.config, null, 2))}</pre></details></section>` : ""}`;
}
function jobs() {
  const active = state.jobs.find((j: Obj) => j.id === state.active);
  return `<section class="card"><div class="section-title"><h3>이 PC의 작업</h3><div class="actions">${button("새로고침", "refresh")}${button("연결 끊긴 작업 확인", "recover")}</div></div>
  <p class="muted">학습은 원격에서 계속됩니다. PC를 다시 열면 이전 작업을 선택해 결과 회수를 시도하세요. GPU가 사용 중이면 종료하지 마세요.</p>
  ${state.jobs.length ? `<table><thead><tr><th>업무 AI / 작업</th><th>상태</th><th>시도</th><th>종류</th><th></th></tr></thead><tbody>${state.jobs.map((j: Obj) => `<tr><td><b>${esc(j.manifest.config.ai_id)}</b><small class="mono">${esc(j.id)}</small></td><td>${badge(j.status)}</td><td>${j.attempts}</td><td>${j.manifest.simulation ? "모의" : "실제"}</td><td>${button("선택", "job:" + j.id)}</td></tr>`).join("")}</tbody></table>` : '<div class="empty">실행 계획을 확인한 뒤 학습을 시작해 보세요.</div>'}</section>
  ${active ? `<section class="card"><h3>${esc(active.manifest.config.ai_id)} ${badge(active.status)}</h3>${active.error ? `<p class="error">${esc(active.error)}</p>` : ""}
  <div class="actions">${button("학습 로그", "logs")}${button("원격 실시간 로그", "live_logs")}${button("평가 결과", "evaluation")}${button("재연결 · 결과 회수", "reconcile")}${button("대기 작업 실행", "execute")}${button("취소 요청", "cancel", "danger")}</div>
  <div class="actions"><label>재개할 체크포인트 <input id="checkpoint" value="checkpoint-10" aria-label="재개할 체크포인트"></label>${button("체크포인트에서 재개", "resume")}</div><pre class="log">${esc(state.logs || "로그를 선택하세요.")}</pre></section>` : ""}`;
}
function history() {
  const options: [string, string][] = state.experiments.map((e: Obj) => [e.id, e.name + " · " + e.id]);
  return `<section class="card"><div class="section-title"><h3>공용 MLflow 기록</h3>${button("실험 목록 불러오기", "experiments")}</div><div class="actions">${options.length ? select("실험", "experiment", state.experiment, options) : field("실험 ID", "experiment", state.experiment)}${button("기록 불러오기", "history")}</div>
  <p class="muted">두 실행을 선택해 설정과 지표를 비교합니다. 지표 차이는 같은 업무·검증 데이터·평가 방식에서만 계산합니다. 합성 데이터 결과는 업무 품질 검증과 구분하세요.</p>
  ${state.runs.length ? `<table><thead><tr><th>선택</th><th>업무 AI</th><th>실행자</th><th>상태</th><th>시작 시각</th><th>종류</th></tr></thead><tbody>${state.runs.map((r: Obj) => `<tr><td><input type="checkbox" data-run="${esc(r.id)}" aria-label="${esc(r.id)} 비교 선택" ${state.selected.includes(r.id) ? "checked" : ""}></td><td><b>${esc(r.ai_id)}</b><small class="mono">${esc(r.id.slice(0, 12))}</small></td><td>${esc(r.operator)}</td><td>${badge(r.status)}</td><td>${r.start_time ? esc(new Date(r.start_time).toLocaleString("ko-KR")) : "—"}</td><td>${r.simulation ? "모의" : r.synthetic ? "합성 데이터" : "실제 데이터"}</td></tr>`).join("")}</tbody></table><div class="actions">${button("선택한 두 실행 비교", "compare")}${state.nextToken ? button("다음 기록 더 보기", "next-history") : ""}</div>` : '<div class="empty">서버에서 실행 기록을 불러오세요.</div>'}</section>
  ${state.comparison ? comparison() : ""}`;
}
function comparison() {
  const [a, b] = state.selected.map((id: string) => state.runs.find((r: Obj) => r.id === id));
  if (!a || !b) return "";
  const keys = [...new Set([...Object.keys(a.metrics), ...Object.keys(b.metrics)])];
  return `<section class="card"><h3>실행 비교</h3><p>${esc(state.comparison.reason || "같은 검증 기준입니다. 차이는 두 번째 실행 − 첫 번째 실행입니다.")}</p>
  <table><thead><tr><th>지표</th><th>${esc(a.id.slice(0, 8))}</th><th>${esc(b.id.slice(0, 8))}</th><th>차이</th></tr></thead><tbody>${keys.map(k => `<tr><td>${esc(k)}</td><td>${num(a.metrics[k])}</td><td>${num(b.metrics[k])}</td><td>${num(state.comparison.deltas[k])}</td></tr>`).join("")}</tbody></table>
  <div class="grid"><details><summary>첫 번째 실행 설정</summary><pre>${esc(JSON.stringify(a.config, null, 2))}</pre></details><details><summary>두 번째 실행 설정</summary><pre>${esc(JSON.stringify(b.config, null, 2))}</pre></details></div></section>`;
}
function render() {
  const views: Obj = { overview, connections, datasets, training, jobs, history };
  root.innerHTML = `<aside><div class="brand"><span class="brand-icon">F</span><div>TUNING FACTORY<small>LLM WORKSPACE</small></div></div><nav>${Object.entries(pages).map(([id, name], i) => `<button data-action="nav:${id}" class="${state.page === id ? "selected" : ""}"><span>0${i + 1}</span>${name}</button>`).join("")}</nav><div class="sidebar-foot"><span class="dot"></span> Windows · 내부 검토 버전<small>학습은 RunPod에서 실행됩니다.</small></div></aside>
  <main><header><div><span class="eyebrow">LLM TUNING FACTORY / ${esc(pages[state.page])}</span><h1>${esc(pages[state.page])}</h1></div><span class="operator">${esc(state.settings.FACTORY_OPERATOR || "사용자 미설정")}</span></header>
  <div aria-live="polite">${state.busy ? `<div class="notice busy"><span class="spinner"></span>${esc(state.busy)}<small>다른 화면에서도 작업 상태를 확인할 수 있습니다.</small></div>` : ""}${state.notice ? `<div class="notice">${esc(state.notice)}</div>` : ""}${state.error ? `<div class="notice error" role="alert">${esc(state.error)}</div>` : ""}</div>
  ${views[state.page]()}</main>`;
  bind();
}
async function refresh() { state.jobs = await api("jobs"); }
async function catalog() { state.catalog = await api("catalog"); }
async function readHistory(append = false) {
  const result = await api("history", { experiment_id: state.experiment, page_token: append ? state.nextToken : null });
  state.runs = append ? [...state.runs, ...result.runs] : result.runs;
  state.nextToken = result.next_page_token; state.comparison = null;
  if (!append) state.selected = [];
}
function settingsFromForm(form: HTMLFormElement) {
  const values: Obj = {};
  form.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-key]").forEach(el => values[el.dataset.key!] = el.value.trim());
  return values;
}
async function execute(job: Obj) {
  state.active = job.id; state.page = "jobs"; await refresh(); render();
  // Deliberately do not block navigation: a separate bridge process owns this attempt.
  api("execute", { job_id: job.id, approved: true }).then(async () => {
    await refresh(); state.notice = "학습 처리가 끝났습니다. 상태와 평가 결과를 확인하세요."; render();
  }).catch(async error => { state.error = String(error); await refresh(); render(); });
}
function bind() {
  if (state.busy) document.querySelectorAll<HTMLButtonElement>("button[data-action]:not([data-action^='nav:']), button[type='submit']").forEach(el => el.disabled = true);
  document.querySelectorAll<HTMLButtonElement>("[data-action]").forEach(el => el.addEventListener("click", () => void action(el.dataset.action!)));
  document.querySelector<HTMLFormElement>("#settings-form")?.addEventListener("submit", e => {
    e.preventDefault(); const values = settingsFromForm(e.currentTarget as HTMLFormElement);
    void task("연결 정보 저장 중", async () => { state.settings = await invoke("settings_save", { values }); state.notice = "연결 정보를 저장했습니다."; });
  });
  document.querySelector<HTMLFormElement>("#dataset-form")?.addEventListener("submit", e => {
    e.preventDefault(); const form = e.currentTarget as HTMLFormElement; const data = settingsFromForm(form);
    data.synthetic = (form.elements.namedItem("synthetic") as HTMLInputElement).checked;
    data.export_allowed = (form.elements.namedItem("export_allowed") as HTMLInputElement).checked;
    void task("데이터 검사·등록 중", async () => { await api("dataset", data); await catalog(); state.notice = "데이터 버전을 등록했습니다."; });
  });
  document.querySelector<HTMLFormElement>("#training-form")?.addEventListener("submit", e => {
    e.preventDefault(); captureConfig(); void task("실행 계획 검사 중", async () => { state.plan = await api("plan", { config: state.config }); });
  });
  document.querySelectorAll<HTMLInputElement | HTMLSelectElement>("[data-config]").forEach(el => el.addEventListener("change", () => { captureConfig(); state.plan = null; }));
  document.querySelector<HTMLSelectElement>('[data-config="base_model"]')?.addEventListener("change", () => {
    state.config.model_revision = ""; state.config.tokenizer_revision = "";
    state.config.template = state.config.base_model.startsWith("Qwen/") ? "qwen" : "llama3";
    render();
  });
  document.querySelector<HTMLSelectElement>('[data-key="template"]')?.addEventListener("change", e => {
    const [kind, index] = (e.target as HTMLSelectElement).value.split(":");
    if (index) state.config = structuredClone(kind === "t" ? state.catalog.templates[Number(index)].config : state.catalog.profiles[Number(index)]);
    state.plan = null; render();
  });
  for (const key of ["train_dataset", "validation_dataset"]) {
    document.querySelector<HTMLSelectElement>(`[data-key="${key}"]`)?.addEventListener("change", e => {
      captureConfig(); const [id, version] = (e.target as HTMLSelectElement).value.split(":");
      if (key === "train_dataset") { state.config.dataset_id = id; state.config.dataset_version = version || ""; }
      else { state.config.evaluation_profile.dataset_id = id || null; state.config.evaluation_profile.dataset_version = version || null;
        if (!id) state.config.evaluation_profile.kind = "pipeline";
        else if (state.config.evaluation_profile.kind === "pipeline") state.config.evaluation_profile.kind = "json"; }
      state.plan = null; render();
    });
  }
  document.querySelector<HTMLInputElement | HTMLSelectElement>('[data-key="experiment"]')?.addEventListener("change", e => state.experiment = (e.target as HTMLInputElement).value);
  document.querySelectorAll<HTMLInputElement>("[data-run]").forEach(el => el.addEventListener("change", () => {
    if (el.checked && state.selected.length >= 2) { el.checked = false; return; }
    state.selected = el.checked ? [...state.selected, el.dataset.run!] : state.selected.filter((id: string) => id !== el.dataset.run);
    state.comparison = null;
  }));
}
async function action(name: string) {
  if (name.startsWith("nav:")) { captureConfig(); state.page = name.split(":")[1]; render(); return; }
  if (name.startsWith("pick:")) {
    const dataset = name.endsWith("dataset");
    const path = await invoke<string | null>("pick_file", { kind: dataset ? "dataset" : "key" });
    const input = document.querySelector<HTMLInputElement>(`[data-key="${dataset ? "path" : "ssh_key_file"}"]`);
    if (path && input) input.value = path;
    return;
  }
  if (name.startsWith("job:")) { state.active = name.split(":")[1]; state.logs = ""; render(); return; }
  if (name === "runtime_prepare" && !await approve("GPU 학습 환경 준비", "저장된 연결의 현재 실행 중인 Pod에 앱 전용 학습 환경을 설치합니다. 의존성과 모델 실행 도구를 다운로드하며 GPU 요금은 계속 발생합니다. 사용자 데이터는 전송하지 않습니다.")) return;
  if (["start", "execute", "resume"].includes(name)) {
    captureConfig();
    if (name === "start" && !state.plan) { state.error = "변경된 설정으로 실행 계획을 다시 확인하세요."; render(); return; }
    if (!await approve("실제 GPU 학습 실행", "선택한 작업의 학습·검증 데이터를 RunPod로 전송하고 실제 학습을 실행합니다. 실행 기록과 산출물은 공용 MLflow에 저장됩니다. 기존 Pod는 작업이 끝나도 자동 종료되지 않습니다.")) return;
  }
  const checkpoint = document.querySelector<HTMLInputElement>("#checkpoint")?.value;
  captureConfig();
  await task(name === "runtime_prepare" ? "GPU 환경 준비 중 · 첫 설치는 수 분 이상 걸릴 수 있습니다" : "처리 중", async () => {
    if (name === "check") { state.notice = JSON.stringify(await api("connection"), null, 2); }
    else if (name === "forget") {
      if (await approve("저장된 인증값 삭제", "Windows 자격 증명 저장소에 저장한 이 앱의 API 키와 MLflow 인증값을 삭제합니다. 진행 중인 작업에는 적용되지 않습니다.")) {
        await invoke("forget_credentials"); state.settings = await invoke("settings_get"); state.notice = "저장된 인증값을 삭제했습니다.";
      }
    }
    else if (name === "runtime_check" || name === "runtime_prepare") { state.notice = JSON.stringify(await api(name, { approved: name === "runtime_prepare" }), null, 2); }
    else if (name === "apply-pod") {
      const pod = await api("pod");
      state.config.resource_profile.gpu_type = pod.gpu.id || pod.gpu.displayName;
      state.config.resource_profile.gpu_count = pod.gpu.count;
      state.config.resource_profile.hourly_cost_usd = Number(pod.costPerHr) / pod.gpu.count;
      state.plan = null; state.notice = "실제 GPU 종류·수·현재 요금을 학습 설정에 적용했습니다.";
    }
    else if (name === "model_revision") {
      Object.assign(state.config, await api("model_revision", { model: state.config.base_model }));
      state.plan = null; state.notice = "선택한 모델과 토크나이저 버전을 고정했습니다.";
    }
    else if (name === "save-profile") { await api("save_profile", { config: state.config }); await catalog(); state.notice = "업무 AI 설정을 저장했습니다."; }
    else if (name === "start") { const job = await api("submit", { config: state.plan.manifest.config, approved: true, request_key: crypto.randomUUID() }); await execute(job); state.plan = null; }
    else if (name === "execute") { await execute(state.jobs.find((j: Obj) => j.id === state.active)); }
    else if (name === "refresh") { await refresh(); }
    else if (name === "recover") { await api("recover"); await refresh(); state.notice = "오래된 작업 상태를 확인했습니다. 실패한 원격 작업은 결과 회수를 시도하세요."; }
    else if (["logs", "live_logs", "evaluation"].includes(name)) { const result = await api(name === "live_logs" ? name : "logs", { job_id: state.active, name: name === "evaluation" ? "evaluation.json" : "train.log" }); state.logs = result.text || result.note || "아직 로그가 없습니다."; }
    else if (name === "cancel") { const result = await api("cancel", { job_id: state.active }); state.notice = result.note; await refresh(); }
    else if (name === "reconcile") { await api("recover"); await api("reconcile", { job_id: state.active }); await refresh(); state.notice = "회수 가능한 원격 결과를 확인했습니다."; }
    else if (name === "resume") { const job = await api("resume", { job_id: state.active, checkpoint, approved: true }); await execute(job); }
    else if (name === "experiments") { const result = await api("history"); state.experiments = result.experiments; }
    else if (name === "history" || name === "next-history") await readHistory(name === "next-history");
    else if (name === "compare") {
      if (state.selected.length !== 2) throw new Error("비교할 실행을 두 개 선택하세요.");
      const [base, candidate] = state.selected.map((id: string) => state.runs.find((r: Obj) => r.id === id));
      state.comparison = await api("compare", { base, candidate });
    }
  });
}
render();
void task("작업 공간 불러오는 중", async () => {
  state.settings = await invoke("settings_get"); await catalog(); await refresh();
  state.config = structuredClone(state.catalog.profiles[0] || state.catalog.templates.find((t: Obj) => t.name === "paper_extraction")?.config);
});
setInterval(() => { if (state.page === "jobs" && !state.busy) refresh().then(render).catch(() => {}); }, 10000);
