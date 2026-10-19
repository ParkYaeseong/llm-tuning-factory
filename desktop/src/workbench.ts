type Obj = Record<string, any>;
export const escapeHtml = (v: unknown) => String(v ?? "").replace(/[&<>"']/g, c =>
  ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;" }[c]!));
const paths: Record<string, string> = {
  overview: '<rect x="3" y="3" width="7" height="7" rx="1"/><rect x="14" y="3" width="7" height="7" rx="1"/><rect x="3" y="14" width="7" height="7" rx="1"/><rect x="14" y="14" width="7" height="7" rx="1"/>',
  training: '<path d="M9 3h6m-5 0v6L4 19a1.5 1.5 0 0 0 1.3 2h13.4A1.5 1.5 0 0 0 20 19L14 9V3M7 14h10"/>',
  datasets: '<ellipse cx="12" cy="5" rx="8" ry="3"/><path d="M4 5v14c0 4 16 4 16 0V5M4 12c0 4 16 4 16 0"/>',
  jobs: '<circle cx="12" cy="12" r="9"/><path d="m10 8 6 4-6 4z"/>',
  history: '<path d="M3 3v18h18M7 15l4-5 4 3 5-8"/>',
  connections: '<path d="M8 3v5m8-5v5M6 8h12v3a6 6 0 0 1-12 0V8m6 9v4"/>',
  search: '<circle cx="10.5" cy="10.5" r="6.5"/><path d="m16 16 5 5"/>',
  plus: '<path d="M12 5v14M5 12h14"/>',
  arrow: '<path d="M5 12h14m-5-5 5 5-5 5"/>',
  check: '<path d="m5 12 4 4L19 6"/>',
  file: '<path d="M14 2H5v20h14V7zM14 2v6h5M8 13h8M8 17h5"/>',
  chevron: '<path d="m9 5 7 7-7 7"/>',
  folder: '<path d="M3 6V4h6l2 3h10v13H3z"/>',
  tutorial: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8z"/>',
};
export function icon(name: string, size = 16) {
  return `<svg width="${size}" height="${size}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.65" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${paths[name] || paths.file}</svg>`;
}
export const button = (label: string, action: string, cls = "", disabled = false) =>
  `<button type="button" class="${cls}" data-action="${escapeHtml(action)}"${disabled ? " disabled" : ""}>${label}</button>`;
export function formatDate(seconds: unknown) {
  return typeof seconds === "number" ? new Date(seconds * 1000).toLocaleString("ko-KR", { month: "short", day: "numeric", hour: "2-digit", minute: "2-digit" }) : "—";
}
export function filterRuns(runs: Obj[], query: string, status: string) {
  const needle = query.toLocaleLowerCase().trim();
  return runs.filter(run => (!status || run.status === status) &&
    [run.ai_id, run.operator, run.id, run.config?.base_model].some(value =>
      String(value || "").toLocaleLowerCase().includes(needle)));
}
export function progressView(progress: Obj | null) {
  if (!progress?.available) return `<div class="chart-empty">${icon("history", 24)}<b>실제 학습 지표를 기다리고 있습니다</b><span>원격 진행 상태를 불러오거나 완료된 작업의 기록을 확인하세요.</span></div>`;
  const points = progress.points || [];
  const number = (value: unknown) => typeof value === "number" ? value.toLocaleString("ko-KR", { maximumFractionDigits: 4 }) : "—";
  let chart = '<div class="chart-empty small">Loss가 두 번 이상 기록되면 추이를 표시합니다.</div>';
  if (points.length >= 2) {
    const losses = points.map((p: Obj) => p.loss);
    const low = Math.min(...losses), high = Math.max(...losses);
    const first = points[0].step, last = points.at(-1).step;
    const coordinates = points.map((p: Obj) => `${36 + (p.step - first) / Math.max(1, last - first) * 574},${24 + (high - p.loss) / (high - low || 1) * 98}`).join(" ");
    chart = `<svg class="loss-chart" viewBox="0 0 640 158" role="img" aria-label="실제 train loss 추이"><path d="M36 24H610M36 73H610M36 122H610" class="chart-grid"/><text x="0" y="28">${number(high)}</text><text x="0" y="126">${number(low)}</text><text x="36" y="148">step ${first}</text><text x="555" y="148">${last}</text><polyline points="${coordinates}" fill="none" stroke="currentColor" stroke-width="2"/></svg>`;
  }
  return `<div class="progress-summary"><div><small>진행 단계</small><b>${number(progress.current_step)} <span>/ ${number(progress.total_steps)}</span></b></div><div><small>최근 train loss</small><b>${number(progress.loss)}</b></div><div><small>경과 시간</small><b>${escapeHtml(progress.elapsed_time || "—")}</b></div><div><small>남은 시간 · 엔진 추정</small><b>${escapeHtml(progress.remaining_time || "—")}</b></div></div>
  ${progress.percent !== null && progress.percent !== undefined ? `<progress max="100" value="${Number(progress.percent)}" aria-label="학습 진행률"></progress>` : ""}
  ${chart}<div class="chart-caption">LLaMA-Factory 원본 학습 로그 · 최대 200개 지점 · 업무 품질 점수가 아닙니다</div>`;
}
