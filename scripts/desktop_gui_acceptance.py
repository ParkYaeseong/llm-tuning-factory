"""Drive the installed desktop app end to end through WebView2's DevTools port (no browser download).

Launches the installed app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>, connects with
Playwright's chromium.connect_over_cdp and walks the real screens: read-only pages → 연결 검사 → 환경 확인 → synthetic
data registration → training settings (model, SFT LoRA, GPU info) → 실행 계획 (GPU compatibility card, estimated
cost) → 학습 시작 approval (runtime preparation notice) → job status until it finishes → results and logs.
Screenshots (password and credential inputs masked) and a JSON-lines step log go to --state-dir. It never reads
credentials and redacts credential-shaped text from everything it records.

Without --confirm-paid-run it only walks the read-only pages (the same as --dry-run). With it, it uses the app's real
state, connection and MLflow settings and starts paid GPU work on the saved Pod: run it only when authorized. It never
starts, stops or deletes Pods, changes only the data and training fields the steps need, refuses to press 학습 시작
when the plan's estimated cost exceeds --max-cost, and closes only the app process it launched (it leaves the app
open if it stops while its job still runs, since the app must not be closed while the GPU is in use).
--selftest-ui runs the same steps against the Vite dev server with a mocked bridge (no app, GPU or network).

Playwright 1.64.0 is the PyPI release matching desktop's Node Playwright 1.64.0 (CDP needs no browser download; the
self-test reuses the Chromium that `npm test` installed). Usage:
  uv run --with playwright==1.64.0 python scripts/desktop_gui_acceptance.py --dry-run
  uv run --with playwright==1.64.0 python scripts/desktop_gui_acceptance.py --confirm-paid-run --max-cost 2
  uv run --with playwright==1.64.0 python scripts/desktop_gui_acceptance.py --selftest-ui
"""
import argparse
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
APP_NAME = "llm-tuning-factory-desktop.exe"
APP = Path(os.environ.get("LOCALAPPDATA", "")) / "LLM Tuning Factory" / APP_NAME
WEBVIEW2_ARGUMENTS = "WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS"
PLAYWRIGHT_PIN = "playwright==1.64.0"
LOG_NAME = "gui-acceptance.jsonl"
# Sidebar pages (main.ts `pages`): `nav [data-action="nav:<id>"]` and the page's h1.
PAGES = {"overview": "작업 공간", "connections": "연결 설정", "datasets": "데이터", "training": "학습 설정",
         "jobs": "실행 관리", "history": "기록 비교"}
# main.ts `statusName`: the job badge text and an ASCII name for screenshot files.
STATUS_SLUGS = {"대기": "queued", "준비": "pending", "전송 준비": "provisioning", "학습 중": "running",
                "평가 중": "evaluating", "완료": "completed", "실패": "failed", "취소": "canceled"}
FINISHED = {"완료", "실패", "취소"}
# gpu-preflight.ts RUNTIME_PREPARATION_NOTICE and RUNTIME_CLEANUP_NOTICE; main.ts execute() result notices.
PREPARE_NOTICE = "학습 환경이 없어 학습 전에 먼저 설치합니다"
CLEANUP_NOTICE = "이전 앱 버전의 학습 환경"
WAITING_NOTICE = "작업은 대기 중입니다"
EXECUTE_RESULTS = ("학습 처리가 끝났습니다", "새로 실행된 작업이 없습니다", WAITING_NOTICE)
# Masked in every screenshot: password inputs (every credential field of the connection page) and, in case one is
# ever shown as text, the credential and key-file fields by their data-key.
MASK_SELECTORS = ('input[type="password"]', '[data-key*="API_KEY"]', '[data-key*="SECRET"]', '[data-key*="TOKEN"]',
                  '[data-key*="ACCESS_KEY"]', '[data-key="ssh_key_file"]', '[data-key="server_ssh_key_file"]')
# Mirrors scripts/check_secrets.py plus generic assignments; the key header is built in pieces because that scan
# walks git history.
_DASHES = "-" * 5
SECRETS = [re.compile(pattern) for pattern in (
    _DASHES + r"BEGIN [A-Z ]*PRIVATE KEY" + _DASHES + r"(?:.*?" + _DASHES + r"END [A-Z ]*PRIVATE KEY" + _DASHES + ")?",
    r"\brpa_[A-Za-z0-9_]{8,}", r"\bhf_[A-Za-z0-9]{16,}", r"\bgh[pousr]_[A-Za-z0-9]{20,}",
    r"\bgithub_pat_[A-Za-z0-9_]{20,}", r"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b", r"(?i)\bbearer\s+[^\s\"']+")]
ASSIGNED_SECRET = re.compile(r"(?i)(\w*(?:api[_-]?key|secret|token|password))([\"']?\s*[=:]\s*[\"']?)([^\s\"',}]+)")
GPU_FACTS = re.compile(r"GPU: (?P<gpu>.+?) · compute capability (?P<capability>\S+) · (?P<memory_gib>[0-9.]+)GiB"
                       r" · 드라이버 (?P<driver>\S+)")
RUNTIME_STATES = (("학습 환경과 CUDA를 확인했습니다", "ready"), ("학습 환경이 아직 없습니다", "missing"),
                  ("CUDA 연산이 실행되지 않습니다", "incompatible"), ("GPU를 사용할 수 없거나", "unavailable"))
CLEANUP_REMOVED = re.compile(r"이전 앱 버전의 학습 환경 (\d+)개를 (?:지워 ([0-9.]+)GB를 확보했습니다|지웠습니다)")
TRAIN_LOSS = re.compile(r"train_loss['\"]?\s*[:=]\s*([-+]?(?:[0-9]+\.?[0-9]*|\.[0-9]+)(?:[eE][-+]?[0-9]+)?)")
USD = re.compile(r"\$\s*([0-9][0-9,]*(?:\.[0-9]+)?)")
SUBJECTS = ("enzyme", "plasmid", "buffer", "strain", "primer", "medium", "sample", "protein")


class StepFailed(RuntimeError):
    """A step that did not reach its expected state; the message names the step."""

    def __init__(self, step, message):
        super().__init__(f"{step}: {message}")
        self.step = step


class SetupError(RuntimeError):
    """The run could not start (no app, app already running, port taken, Playwright missing)."""


def redact(text):
    text = str(text)
    for pattern in SECRETS:
        text = pattern.sub("[REDACTED]", text)
    return ASSIGNED_SECRET.sub(lambda match: match[1] + match[2] + "[REDACTED]", text)


def scrub(value):
    """`value` with every string redacted (dict keys too), for the step log and the summary."""
    if isinstance(value, dict):
        return {redact(key): scrub(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [scrub(item) for item in value]
    return redact(value) if isinstance(value, str) else value


def default_state_dir(now):
    return Path(tempfile.gettempdir()) / "gpu_accept" / f"gui-{now:%Y%m%d-%H%M%S}"


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", "--state", dest="state_dir", type=Path, default=None,
                        help="screenshots and step log (default: %%TEMP%%\\gpu_accept\\gui-<timestamp>)")
    parser.add_argument("--dataset", type=Path, default=None,
                        help="instruction JSON to register (default: a deterministic 32-row synthetic file written "
                             "to the state dir)")
    parser.add_argument("--dataset-id", default="gui_accept_synth")
    parser.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    parser.add_argument("--stage", default="sft")
    parser.add_argument("--method", default="lora")
    parser.add_argument("--port", type=int, default=9223, help="WebView2 DevTools port")
    parser.add_argument("--app", type=Path, default=APP, help="installed desktop executable")
    parser.add_argument("--timeout", type=int, default=2700, help="seconds to wait for the job to finish (45 min)")
    parser.add_argument("--run-seconds", type=int, default=3600, help="the plan's 최대 실행 시간 (초)")
    parser.add_argument("--max-cost", type=float, default=2.0,
                        help="USD: the plan's 최대 예상 GPU 비용 and the most this script lets the plan estimate "
                             "before it presses 학습 시작")
    parser.add_argument("--poll-seconds", type=float, default=15.0, help="job status polling interval")
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument("--dry-run", action="store_true", help="read-only pages only (the default)")
    mode.add_argument("--confirm-paid-run", action="store_true",
                      help="run 연결 검사, 환경 확인, data registration, the plan and 학습 시작 (paid GPU work)")
    parser.add_argument("--selftest-ui", action="store_true",
                        help="run the steps against the Vite dev server with a mocked bridge (full flow unless "
                             "--dry-run)")
    parser.add_argument("--ui-url", default=None, help="--selftest-ui: an already running dev server")
    parser.add_argument("--run-queued", action="store_true",
                        help="press 대기 작업 실행 once if the started job waits for the GPU")
    parser.add_argument("--keep-app-open", action="store_true", help="leave the launched app running at the end")
    args = parser.parse_args(argv)
    if args.max_cost <= 0 or args.timeout <= 0 or args.run_seconds <= 0 or args.poll_seconds <= 0:
        parser.error("--max-cost, --timeout, --run-seconds and --poll-seconds must be positive")
    if not 1024 <= args.port <= 65535:
        parser.error("--port must be between 1024 and 65535")
    if args.ui_url and not args.selftest_ui:
        parser.error("--ui-url needs --selftest-ui")
    if args.state_dir is None:
        args.state_dir = default_state_dir(datetime.now())
    return args


def paid_mode(args):
    """Whether the run goes past the read-only pages: the mocked self-test always may, the real app only when asked."""
    return not args.dry_run if args.selftest_ui else args.confirm_paid_run


def app_environment(base, port):
    """`base` plus the DevTools port in WebView2's extra browser arguments (other arguments kept, an old port replaced)."""
    env = dict(base)
    kept = [arg for arg in env.get(WEBVIEW2_ARGUMENTS, "").split() if not arg.startswith("--remote-debugging-port")]
    env[WEBVIEW2_ARGUMENTS] = " ".join([*kept, f"--remote-debugging-port={port}"])
    return env


def app_running(tasklist_output):
    return APP_NAME.lower() in tasklist_output.lower()


def synthetic_rows(count=32):
    """Deterministic, unique fictional instruction records (`{"instruction", "output"}`)."""
    rows = []
    for index in range(count):
        subject, record = SUBJECTS[index % len(SUBJECTS)], f"SYN-{index + 1:03d}"
        kind = index % 4
        if kind == 0:
            row = {"instruction": f"Return the {subject} identifier of fictional record {record} as JSON.",
                   "output": json.dumps({"record": record, "subject": subject})}
        elif kind == 1:
            row = {"instruction": f"Classify the fictional step of record {record}: inspect the {subject}.",
                   "output": "inspection"}
        elif kind == 2:
            row = {"instruction": f"Summarize the fictional note of record {record}.",
                   "output": f"The {subject} of record {record} was checked and logged."}
        else:
            row = {"instruction": f"Extract the volume of record {record} as JSON: {index + 1} mL of {subject}.",
                   "output": json.dumps({"record": record, "volume_ml": index + 1})}
        rows.append(row)
    return rows


def synthetic_dataset_bytes(count=32):
    return (json.dumps(synthetic_rows(count), ensure_ascii=False, indent=1) + "\n").encode("utf-8")


def parse_usd(text):
    match = USD.search(text or "")
    return float(match[1].replace(",", "")) if match else None


def cost_guard(text, limit):
    """(allowed, cost, reason) for the plan's estimated cost text (`$0.59`, `$1,234.5`, `$—`)."""
    cost = parse_usd(text)
    if cost is None:
        return False, None, f"could not read the plan's estimated cost from {text!r}; not starting"
    if cost > limit:
        return False, cost, f"the plan estimates ${cost:g}, above --max-cost ${limit:g}; not starting"
    return True, cost, ""


def runtime_facts(text):
    """Runtime state and observed GPU from the 환경 확인 notice (gpu-preflight.ts runtimeNotice)."""
    text = text or ""
    facts = {"runtime_state": next((name for marker, name in RUNTIME_STATES if marker in text), "unknown")}
    match = GPU_FACTS.search(text)
    if match:
        facts.update(gpu=match["gpu"], capability=match["capability"], memory_gib=float(match["memory_gib"]),
                     driver=match["driver"])
    return facts


def cleanup_facts(text):
    """What the run approval's runtime preparation pruned (gpu-preflight.ts cleanupSummary)."""
    text = text or ""
    match = CLEANUP_REMOVED.search(text)
    return {"removed": int(match[1]) if match else 0,
            "freed_gb": float(match[2]) if match and match[2] else None,
            "not_removed": "지우지 못했습니다" in text, "skipped": "정리는 건너뛰었습니다" in text,
            "incomplete": "다음 학습 환경 준비 때 정리합니다" in text}


def parse_train_loss(text):
    found = TRAIN_LOSS.findall(text or "")
    return float(found[-1]) if found else None


def slug(text):
    return re.sub(r"[^a-z0-9]+", "-", text.lower()).strip("-") or "step"


def now_iso():
    return datetime.now().astimezone().isoformat(timespec="seconds")


class StepLog:
    """Numbered screenshots and one redacted JSON line per step in `<directory>/gui-acceptance.jsonl`."""

    def __init__(self, directory, echo=print):
        self.directory = Path(directory)
        self.path = self.directory / LOG_NAME
        self.echo = echo
        self.count = 0
        self.entries = []
        self.outcome = {}

    def screenshot_path(self, step):
        self.count += 1
        return self.directory / f"{self.count:02d}-{slug(step)}.png"

    def write(self, step, ok, screenshot=None, **facts):
        entry = scrub({"step": step, "ok": bool(ok), "at": now_iso(),
                       **({"screenshot": Path(screenshot).name} if screenshot else {}), **facts})
        line = json.dumps(entry, ensure_ascii=False, default=str)
        self.directory.mkdir(parents=True, exist_ok=True)
        with self.path.open("a", encoding="utf-8") as handle:
            handle.write(line + "\n")
        self.echo(line)
        self.entries.append(entry)
        return entry


# Playwright steps. Selectors come from desktop/src/main.ts, gpu-preflight.ts and server-connection.ts.
NEW_JOB_JS = """before => {
  const ids = [...document.querySelectorAll('[data-tour="jobs-list"] tbody tr small.mono')].map(e => e.innerText.trim());
  return ids.find(id => !before.includes(id)) || false;
}"""
STATUS_JS = """([id, last, waiting]) => {
  const row = [...document.querySelectorAll('[data-tour="jobs-list"] tbody tr')]
    .find(tr => tr.querySelector('small.mono')?.innerText.trim() === id);
  const status = row?.querySelector('.badge')?.innerText.trim() || null;
  const error = document.querySelector('.notice.error')?.innerText.trim() || '';
  const notice = [...document.querySelectorAll('.notice:not(.busy):not(.error)')].map(e => e.innerText.trim()).join('\\n');
  if (error || (status && status !== last) || (waiting && notice.includes(waiting))) return {status, error, notice};
  return false;
}"""
ROWS_JS = "rows => rows.map(row => [...row.querySelectorAll('td')].map(cell => cell.innerText.trim()))"
PAIRS_JS = ("items => Object.fromEntries(items.map(item => [item.querySelector('dt')?.innerText.trim() ?? '', "
            "item.querySelector('dd')?.innerText.trim() ?? '']))")
FINDINGS_JS = """el => {
  const out = {}; let label = null;
  for (const child of el.children) {
    if (child.tagName === 'P') label = child.innerText.trim();
    else if (child.tagName === 'UL' && label) out[label] = [...child.querySelectorAll('li')].map(li => li.innerText.trim());
  }
  return out;
}"""


class Walk:
    """The page, the step log and what the steps learned (runtime state, job IDs) for one run."""

    def __init__(self, page, log, args):
        self.page, self.log, self.args = page, log, args
        self.runtime_state = None
        self.jobs_before = None
        self.job_id = None

    def shot(self, step):
        path = self.log.screenshot_path(step)
        masks = [self.page.locator(selector) for selector in MASK_SELECTORS]
        try:
            # A modal dialog sits in the viewport; a full-page shot would place it somewhere down the page.
            full = not self.page.locator("dialog[open]").count()
            self.page.screenshot(path=str(path), full_page=full, mask=masks, animations="disabled")
        except Exception as error:   # a closed or unresponsive page must not hide the step's own result
            try:
                self.page.screenshot(path=str(path), mask=masks)
            except Exception:
                return None, f"{type(error).__name__}: {str(error).splitlines()[0] if str(error) else ''}"
        return path, None

    def record(self, step, ok=True, message="", **facts):
        path, error = self.shot(step)
        if error:
            facts["screenshot_error"] = error
        self.log.write(step, ok, screenshot=path, **({"message": message} if message else {}), **facts)
        if not ok:
            raise StepFailed(step, redact(message or "failed"))

    def fail(self, step, message, **facts):
        self.record(step, False, message, **facts)

    def texts(self, selector, scope=None):
        return [text.strip() for text in (scope or self.page).locator(selector).all_inner_texts()]

    def text(self, selector, scope=None):
        return "\n".join(self.texts(selector, scope))

    def messages(self):
        return self.text(".notice:not(.busy):not(.error)"), self.text(".notice.error")

    def settle(self, timeout_ms):
        """Waits until no task runs (main.ts shows `.notice.busy` from the click on) and returns (notice, error)."""
        self.page.locator(".notice.busy").first.wait_for(state="hidden", timeout=timeout_ms)
        return self.messages()

    def card(self, title):
        return self.page.locator("section.card").filter(
            has=self.page.get_by_role("heading", name=title, exact=True))

    def heading(self, title):
        return self.page.get_by_role("heading", level=1, name=title, exact=True)

    def nav(self, view):
        self.page.locator(f'nav [data-action="nav:{view}"]').click()
        self.heading(PAGES[view]).wait_for()

    def job_ids(self):
        return self.texts('[data-tour="jobs-list"] tbody tr small.mono')

    def wait_compat(self, timeout_ms=120_000):
        """(valid, text) of the training form's compatibility section once no check is pending."""
        self.page.locator("#training-form .compatibility.pending").first.wait_for(state="hidden", timeout=timeout_ms)
        section = self.page.locator('#training-form section[aria-label="설정 호환성"]')
        section.wait_for(timeout=timeout_ms)
        return "ready" in (section.get_attribute("class") or "").split(), section.inner_text().strip()

    def option_values(self, select):
        return select.locator("option").evaluate_all("options => options.map(o => [o.value, o.disabled, o.text])")


def step_ready(w, browser=None):
    w.page.locator("h1").first.wait_for(timeout=120_000)
    w.settle(180_000)   # 작업 공간 불러오는 중
    w.record("ready", True, url=w.page.url, browser=browser, version=w.text(".sidebar-foot small"))


def step_pages(w):
    """Read-only walk of the sidebar pages; remembers the existing job IDs."""
    page = w.page
    for view in PAGES:
        w.nav(view)
        facts = {}
        if view == "connections":
            facts["provider"] = page.locator('#settings-form [data-key="provider"]').input_value()
            pod = page.locator('#settings-form [data-key="pod_id"]')
            if pod.count():
                facts["pod_id"] = pod.input_value()
        elif view == "datasets":
            facts["registered_versions"] = w.card("등록한 버전").locator("tbody tr").count()
        elif view == "training":
            page.locator("#training-form").wait_for(timeout=120_000)
            models = [value for value, _, _ in w.option_values(page.locator('select[data-config="base_model"]'))]
            facts.update(models=len(models), model=w.args.model, model_listed=w.args.model in models)
        elif view == "jobs":
            w.jobs_before = w.job_ids()
            facts["jobs"] = len(w.jobs_before)
        w.record(f"page-{view}", True, **facts)


def step_connection_check(w):
    w.nav("connections")
    w.page.locator('[data-action="check"]').click()
    notice, error = w.settle(180_000)
    result = w.card("연결 검사 결과").locator("dl > div").evaluate_all(PAIRS_JS) if not error else {}
    if error:
        w.fail("connection-check", error, notice=notice)
    pod = result.get("Pod 상태")
    if pod is not None and pod != "RUNNING":
        w.fail("connection-check", f"the saved Pod is {pod}; start it in RunPod first (this script never starts "
               "Pods)", notice=notice, result=result)
    w.record("connection-check", True, notice=notice, result=result)


def step_runtime_check(w):
    w.nav("connections")
    w.page.locator('[data-action="runtime_check"]').click()
    notice, error = w.settle(960_000)   # read-only SSH check, at most 15 minutes
    facts = runtime_facts(notice or error)
    w.runtime_state = facts["runtime_state"]
    if error:
        w.fail("runtime-check", error, **facts)
    w.record("runtime-check", True, notice=notice, **facts)


def step_dataset(w, dataset, version):
    page, args = w.page, w.args
    w.nav("datasets")
    form = page.locator("#dataset-form")

    def fill():
        form.locator('[data-key="dataset_id"]').fill(args.dataset_id)
        form.locator('[data-key="version"]').fill(version)
        form.locator('[data-key="kind"]').select_option("instruction")
        form.locator('[data-key="path"]').fill(str(dataset))   # the native file dialog cannot be automated
        form.locator('input[name="synthetic"]').check()
        form.locator('input[name="export_allowed"]').check()

    fill()
    form.locator('[data-action="dataset-preview"]').click()
    notice, error = w.settle(120_000)
    if error:
        w.fail("dataset-preview", error)
    preview = w.card("데이터 미리보기").locator(".badge").first.inner_text() if not error else ""
    w.record("dataset-preview", True, preview=preview)
    if form.locator('[data-key="path"]').input_value() != str(dataset):
        fill()   # the preview's render keeps the draft; refill if it ever does not
    form.locator('button[type="submit"]').click()   # 검사하고 등록
    notice, error = w.settle(120_000)
    if error or "데이터 버전을 등록했습니다" not in notice:
        w.fail("dataset", error or f"no registration notice: {notice!r}")
    rows = w.card("등록한 버전").locator("tbody tr").evaluate_all(ROWS_JS)
    row = next((cells for cells in rows if cells[:2] == [args.dataset_id, version]), None)
    facts = {"notice": notice, "dataset": f"{args.dataset_id}:{version}", "registered": row}
    if row is None or "합성" not in row or "허용" not in row:
        w.fail("dataset", "the registered version is missing or not marked 합성 · GPU 전송 허용", **facts)
    w.log.outcome["dataset"] = facts["dataset"]
    w.record("dataset", True, **facts)


def step_training(w, version):
    page, args = w.page, w.args
    w.nav("training")
    form = page.locator("#training-form")
    form.wait_for(timeout=120_000)
    template = form.locator('select[data-key="template"]')
    names = [text for _, _, text in w.option_values(template)]
    if "paper_extraction" in names:   # the example settings, so earlier saved settings do not leak into the run
        template.select_option(label="paper_extraction")
    else:
        template.select_option("t:0")
    w.wait_compat()
    # GPU 정보 → 학습 설정에 적용 (GPU type, count and the Pod's current hourly price).
    w.nav("connections")
    page.locator('[data-action="apply-pod"]').click()
    notice, error = w.settle(180_000)
    if error:
        w.fail("gpu-info", error)
    w.record("gpu-info", True, notice=notice)
    w.nav("training")
    form.wait_for(timeout=120_000)
    for path, value in (("resource_profile.timeout_seconds", args.run_seconds),
                        ("resource_profile.max_cost_usd", args.max_cost)):
        field = form.locator(f'input[data-config="{path}"]')
        field.fill(f"{value:g}" if isinstance(value, float) else str(value))
        field.press("Tab")
    choices = (('select[data-key="train_dataset"]', f"{args.dataset_id}:{version}"),
               ('select[data-config="base_model"]', args.model))
    for selector, value in choices:
        select = form.locator(selector)
        options = {option: disabled for option, disabled, _ in w.option_values(select)}
        if value not in options:
            w.fail("training", f"{value} is not offered in {selector}")
        select.select_option(value)
    form.locator('[data-action="model_revision"]').click()   # 선택한 모델 버전 확인·고정
    notice, error = w.settle(120_000)
    if error or "고정했습니다" not in notice:
        w.fail("training", error or f"model revision not pinned: {notice!r}")
    for selector, value in (('select[data-config="training_stage"]', args.stage),
                            ('select[data-config="training_method"]', args.method)):
        select = form.locator(selector)
        options = {option: disabled for option, disabled, _ in w.option_values(select)}
        if options.get(value, True):
            w.fail("training", f"{value} is missing or disabled in {selector}")
        select.select_option(value)
    valid, compatibility = w.wait_compat()
    values = {path: form.locator(f'[data-config="{path}"]').input_value() for path in (
        "base_model", "model_revision", "training_stage", "training_method", "resource_profile.gpu_type",
        "resource_profile.gpu_count", "resource_profile.hourly_cost_usd", "resource_profile.timeout_seconds",
        "resource_profile.max_cost_usd")}
    facts = {"settings": values, "dataset": f"{args.dataset_id}:{version}", "compatibility": compatibility}
    if not valid or not form.locator('button[type="submit"]').is_enabled():
        w.fail("training", "설정 호환성 check did not pass", **facts)
    w.record("training", True, **facts)


def step_plan(w):
    page, args = w.page, w.args
    page.locator('#training-form button[type="submit"]').click()   # 실행 계획 확인
    notice, error = w.settle(180_000)
    if error:
        w.fail("plan", error)
    plan = page.locator("section.plan")
    plan.get_by_role("heading", name="실행 전 확인", exact=True).wait_for()
    stat = plan.locator(".stats article")
    cost_text = stat.filter(has_text="예상 GPU 비용").locator("b").inner_text()
    compat = plan.locator('[data-testid="plan-compatibility"]')
    findings = compat.evaluate(FINDINGS_JS) if compat.count() else {}
    allowed, cost, reason = cost_guard(cost_text, args.max_cost)
    facts = {"cost_text": cost_text, "estimated_cost_usd": cost, "max_cost_usd": args.max_cost,
             "data": stat.filter(has_text="학습 데이터").locator("b").inner_text(),
             "compatibility": compat.inner_text().strip() if compat.count() else None, "findings": findings,
             "warnings": findings.get("주의 항목", []), "plan_issues": w.texts(":scope > p.warning-text", plan),
             "destination": w.text(":scope > p.muted", plan)}
    w.log.outcome["estimated_cost_usd"] = cost
    if "실행 시 거부될 항목" in findings:
        w.fail("plan", "the GPU compatibility card lists 실행 시 거부될 항목", **facts)
    if facts["plan_issues"]:
        w.fail("plan", "the plan cannot start: " + " / ".join(facts["plan_issues"]), **facts)
    if not allowed:
        w.fail("plan", reason, **facts)
    w.record("plan", True, **facts)


def step_approval(w, step="approval", trigger='section.plan [data-action="start"]', expect_prepare=True):
    """Presses the run button, checks the approval dialog (runtime preparation and cleanup notice) and approves."""
    page = w.page
    page.locator(trigger).click()
    dialog = page.locator("dialog[open]")
    dialog.or_(page.locator(".notice.error")).first.wait_for(timeout=960_000)   # runtime_check runs first
    if not dialog.count():
        w.fail(step, w.messages()[1])
    body = dialog.locator("p.confirmation").inner_text()
    facts = {"title": dialog.locator("h2").inner_text(), "body": body, "prepare_notice": PREPARE_NOTICE in body,
             "cleanup_notice": CLEANUP_NOTICE in body, "runtime_state_at_check": w.runtime_state}
    problem = ("환경 확인 reported no runtime but the approval lacks the runtime preparation notice"
               if expect_prepare and w.runtime_state == "missing" and not facts["prepare_notice"] else
               "the runtime preparation notice lacks the cleanup sentence"
               if facts["prepare_notice"] and not facts["cleanup_notice"] else "")
    if problem:
        path, error = w.shot(step)   # the dialog as it was shown, then 돌아가기: nothing is approved
        w.log.write(step, False, screenshot=path, message=problem, **facts,
                    **({"screenshot_error": error} if error else {}))
        dialog.locator("[data-no]").click()
        raise StepFailed(step, problem)
    w.log.outcome["prepare_notice"] = facts["prepare_notice"]
    w.record(step, True, **facts)
    dialog.locator("[data-yes]").click()   # 내용 확인 · 진행


def step_job(w):
    """Waits for the new job and follows its status badge until it finishes; returns the final status."""
    page, args = w.page, w.args
    w.heading(PAGES["jobs"]).or_(page.locator(".notice.error")).first.wait_for(timeout=300_000)
    if not w.heading(PAGES["jobs"]).count():
        w.fail("submitted", w.messages()[1])
    handle = page.wait_for_function(NEW_JOB_JS, arg=w.jobs_before or [], timeout=120_000)
    w.job_id = w.log.outcome["job_id"] = handle.json_value()
    w.record("submitted", True, job_id=w.job_id, notice=w.messages()[0])
    started, last, transitions, queued_run = time.monotonic(), None, [], False
    while True:
        remaining = args.timeout - (time.monotonic() - started)
        if remaining <= 0:
            w.fail("job", f"the job did not finish within {args.timeout} s; it keeps running on the GPU (see 실행 관리;"
                   " this script never cancels it)", job_id=w.job_id, transitions=transitions)
        try:
            handle = page.wait_for_function(STATUS_JS, arg=[w.job_id, last, WAITING_NOTICE],
                                            polling=max(100, int(args.poll_seconds * 1000)), timeout=remaining * 1000)
        except Exception as error:   # Playwright's TimeoutError: the loop reports the job timeout
            if "Timeout" not in type(error).__name__:
                raise
            continue
        seen = handle.json_value()
        status = seen["status"]
        if status and status != last:
            last = status
            transitions.append({"status": status, "at": now_iso(), "elapsed_s": round(time.monotonic() - started)})
            w.log.outcome["status"] = status
            w.record(f"job-{STATUS_SLUGS.get(status, 'status')}", True, job_id=w.job_id, status=status)
        if status in FINISHED:
            w.record("job", True, job_id=w.job_id, status=status, error=seen["error"], transitions=transitions)
            return status
        if seen["error"]:
            w.fail("job", seen["error"] + " (the remote job may still run: use 재연결 · 결과 회수 in 실행 관리)",
                   job_id=w.job_id, status=status, transitions=transitions)
        if WAITING_NOTICE in seen["notice"]:
            if queued_run or not args.run_queued:
                w.fail("job", "the job waits for the GPU (작업은 대기 중입니다); " + (
                    "it still waits after 대기 작업 실행" if queued_run else
                    "press 대기 작업 실행 when the GPU is free, or rerun with --run-queued"), job_id=w.job_id)
            queued_run = True
            step_approval(w, "approval-queued", '[data-action="execute"]', expect_prepare=False)


def step_results(w):
    """The execute result notice (with the cleanup summary), the job's progress, train log and evaluation."""
    page = w.page
    result = page.locator(".notice:not(.busy):not(.error)").filter(
        has_text=re.compile("|".join(map(re.escape, EXECUTE_RESULTS))))
    try:
        result.or_(page.locator(".notice.error")).first.wait_for(timeout=300_000)
    except Exception as error:   # recorded below without the notice
        if "Timeout" not in type(error).__name__:
            raise
    notice, error = w.messages()
    cleanup = cleanup_facts(notice)
    w.log.outcome["cleanup"] = cleanup
    w.record("execute-result", True, notice=notice, error=error, cleanup=cleanup)
    page.locator(f'[data-action="job:{w.job_id}"]').click()   # loads the stored progress
    w.settle(120_000)
    summary = page.locator(".progress-summary > div")
    progress = dict(zip(summary.locator("small").all_inner_texts(), summary.locator("b").all_inner_texts()))
    progress_loss = parse_train_loss("train_loss: " + progress.get("최근 train loss", "").replace(",", ""))
    w.record("results", True, progress=progress or w.text(".chart-empty b"))
    page.locator('[data-action="logs"]').click()   # 학습 로그
    w.settle(120_000)
    log = page.locator("pre.log").inner_text()
    train_loss = parse_train_loss(log)
    w.log.outcome["train_loss"] = train_loss if train_loss is not None else progress_loss
    w.record("logs", True, train_loss=train_loss, recent_train_loss=progress_loss, log_tail=log[-2000:])
    page.locator('[data-action="evaluation"]').click()   # 평가 결과
    w.settle(120_000)
    w.record("evaluation", True, evaluation=page.locator("pre.log").inner_text()[:2000])


def guarded(w, name, step, *args, **kwargs):
    """Runs a step; a Playwright timeout or error becomes a failure of that step with a screenshot."""
    try:
        return step(w, *args, **kwargs)
    except StepFailed:
        raise
    except Exception as error:
        detail = str(error).strip().splitlines()[0] if str(error).strip() else ""
        w.fail(name, f"{type(error).__name__}: {detail}")


def walk(w, paid, dataset, version, browser=None):
    guarded(w, "ready", step_ready, browser)
    guarded(w, "pages", step_pages)
    if not paid:
        w.log.write("done", True, mode="read-only pages")
        return {"passed": True}
    guarded(w, "connection-check", step_connection_check)
    guarded(w, "runtime-check", step_runtime_check)
    guarded(w, "dataset", step_dataset, dataset, version)
    guarded(w, "training", step_training, version)
    guarded(w, "plan", step_plan)
    guarded(w, "approval", step_approval)
    status = guarded(w, "job", step_job)
    guarded(w, "results", step_results)
    w.record("verdict", status == "완료", "" if status == "완료" else f"the job ended as {status}",
             job_id=w.job_id, status=status, train_loss=w.log.outcome.get("train_loss"))
    return {"passed": True}


# Launching the installed app and the self-test server.
def devtools_version(port, timeout=2):
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{port}/json/version", timeout=timeout) as response:
            return json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError, urllib.error.URLError):
        return None


def wait_http(probe, seconds, process, what):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        found = probe()
        if found:
            return found
        if process is not None and process.poll() is not None:
            raise SetupError(f"{what} exited with code {process.returncode} before it answered")
        time.sleep(0.5)
    raise SetupError(f"{what} did not answer within {seconds} s")


def tasklist():
    try:
        return subprocess.run(["tasklist", "/FI", f"IMAGENAME eq {APP_NAME}", "/FO", "CSV", "/NH"],
                              capture_output=True, text=True, timeout=30).stdout
    except (OSError, subprocess.SubprocessError):
        return ""


def close_app(process, grace=20):
    """Closes the process this script started (WM_CLOSE, then terminate); never any other process."""
    if process.poll() is not None:
        return
    try:
        subprocess.run(["taskkill", "/PID", str(process.pid)], capture_output=True, timeout=30)
        process.wait(grace)
    except (OSError, subprocess.SubprocessError):
        process.terminate()
        try:
            process.wait(10)
        except subprocess.TimeoutExpired:
            process.kill()


def app_page(browser, seconds):
    deadline = time.monotonic() + seconds
    while time.monotonic() < deadline:
        for context in browser.contexts:
            for page in context.pages:
                if re.search(r"tauri|localhost", page.url):
                    return page
        time.sleep(0.5)
    raise SetupError("no app page (tauri.localhost) appeared over DevTools")


def import_playwright():
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as error:
        raise SetupError(f"Playwright is not installed: run with `uv run --with {PLAYWRIGHT_PIN} python "
                         "scripts/desktop_gui_acceptance.py ...`") from error
    return sync_playwright


def prepare_dataset(args, now):
    if args.dataset is not None:
        if not args.dataset.is_file():
            raise SetupError(f"dataset file not found: {args.dataset}")
        return args.dataset.resolve(), f"gui-{now:%Y%m%d%H%M%S}"
    path = args.state_dir / "synthetic-instruction-32.json"
    path.write_bytes(synthetic_dataset_bytes())
    return path.resolve(), f"gui-{now:%Y%m%d%H%M%S}"


def run_installed(args, log, now):
    paid = paid_mode(args)
    if not args.app.is_file():
        raise SetupError(f"installed app not found: {args.app} (use --app)")
    if app_running(tasklist()):
        raise SetupError("close the running app first: WebView2 reuses its browser process and ignores the port")
    if devtools_version(args.port):
        raise SetupError(f"port {args.port} already serves DevTools; choose another --port")
    sync_playwright = import_playwright()
    dataset, version = prepare_dataset(args, now) if paid else (None, None)
    log.write("launch", True, app=str(args.app), port=args.port, paid=paid, dataset=str(dataset) if dataset else None)
    process = subprocess.Popen([str(args.app)], env=app_environment(os.environ, args.port), cwd=str(args.app.parent))
    try:
        info = wait_http(lambda: devtools_version(args.port), 60, process, "the app's DevTools port")
        with sync_playwright() as playwright:
            browser = playwright.chromium.connect_over_cdp(f"http://127.0.0.1:{args.port}")
            try:
                page = app_page(browser, 60)
                page.set_default_timeout(60_000)
                return walk(Walk(page, log, args), paid, dataset, version, browser=info.get("Browser"))
            finally:
                browser.close()   # disconnects; the app stays up until close_app
    finally:
        # The app asks not to be closed while the GPU is in use: a job still running keeps it open too.
        running = log.outcome.get("job_id") and log.outcome.get("status") not in FINISHED
        if args.keep_app_open or running:
            log.write("app-left-open", True, pid=process.pid, reason="--keep-app-open" if args.keep_app_open else
                      "the job is still running; close the app yourself once it finishes (see 실행 관리)")
        else:
            close_app(process)


def free_port():
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        return probe.getsockname()[1]


def reachable(url):
    try:
        with urllib.request.urlopen(url, timeout=2) as response:
            return response.status == 200
    except (OSError, urllib.error.URLError):
        return False


def start_dev_server(port, log_path):
    """The desktop Vite dev server as the Playwright suite runs it (TUNING_FACTORY_E2E: no HMR, no watcher)."""
    vite, node = ROOT / "desktop" / "node_modules" / "vite" / "bin" / "vite.js", shutil.which("node")
    if not vite.is_file() or node is None:
        raise SetupError("the self-test needs Node and `npm ci` in desktop/")
    output = log_path.open("wb")
    try:
        return subprocess.Popen([node, str(vite), "--host", "127.0.0.1", "--port", str(port), "--strictPort"],
                                cwd=str(ROOT / "desktop"), env={**os.environ, "TUNING_FACTORY_E2E": "1"},
                                stdout=output, stderr=subprocess.STDOUT)
    finally:
        output.close()


def selftest_bridge():
    """The mocked Tauri bridge of the self-test (window.__TAURI_INTERNALS__, as desktop/tests do), with a RunPod
    connection, a missing runtime, the catalog's model and a job that completes after its execute call."""
    config = json.loads((ROOT / "desktop" / "tests" / "config.json").read_text(encoding="utf-8"))
    return SELFTEST_BRIDGE.replace("__CONFIG__", json.dumps(config))


SELFTEST_BRIDGE = r"""(() => {
  const config = __CONFIG__;
  const settings = { provider: "existing_runpod", pod_id: "selftestpod01", RUNPOD_API_KEY_saved: true,
    FACTORY_OPERATOR: "selftest", MLFLOW_TRACKING_URI: "https://mlflow.selftest.invalid", auth_mode: "none" };
  const gpu = { name: "NVIDIA A40", capability: "8.6", memory_mib: 46068, driver: "550.127.05" };
  const warn = { level: "warn", message: "self-test warning finding" };
  const models = [{ id: "Qwen/Qwen2.5-0.5B-Instruct", template: "qwen", architecture: "qwen2", gpu_verified: true },
    { id: "Qwen/Qwen3.5-0.8B", template: "qwen3_5_nothink", architecture: "qwen3_5", gpu_verified: false }];
  const stages = [{ id: "pt", label: "PT", dataset_kind: "text" }, { id: "sft", label: "SFT", dataset_kind: "instruction" },
    { id: "dpo", label: "DPO", dataset_kind: "preference" }, { id: "kto", label: "KTO", dataset_kind: "kto" }];
  let datasets = [{ dataset_id: "dummy", version: "1", rows: 3, synthetic: true, export_allowed: true }];
  let jobs = [{ id: "0".repeat(31) + "1", attempts: 1, fixed: "COMPLETED", updated: 1,
    manifest: { config, simulation: false } }];
  let executed = null;
  const status = job => {
    if (job.fixed) return job.fixed;
    if (!executed) return "QUEUED";
    const seconds = (Date.now() - executed) / 1000;
    return seconds < 3 ? "PROVISIONING" : seconds < 8 ? "RUNNING" : seconds < 11 ? "EVALUATING" : "COMPLETED";
  };
  const factory = async (action, data) => {
    if (action === "catalog") return { models, training_stages: stages, templates: [{ name: "paper_extraction", config }],
      profiles: [], datasets };
    if (action === "jobs") return jobs.map(job => ({ ...job, status: status(job) }));
    if (action === "capabilities") {
      const errors = data.config.model_revision ? [] : ["model revision required"];
      return { valid: !errors.length, errors, warnings: [], supported_stages: ["pt", "sft", "dpo", "kto"],
        methods: ["lora", "qlora", "full", "freeze", "oft"], quantization_bits: [4, 8], dataset_kind: "instruction" };
    }
    if (action === "connection") return { tracking_uri: settings.MLFLOW_TRACKING_URI, mlflow_read: true,
      mlflow_auth: "none", runpod: { status: "RUNNING" }, gpu_training_verified: false };
    if (action === "runtime_check") return { ready: false, runtime_state: "missing", observation: { gpus: [gpu] },
      findings: data.config || data.job_id ? [warn] : [] };
    if (action === "pod") return { gpu: { id: "NVIDIA A40", displayName: "A40", count: 1 }, costPerHr: 0.59 };
    if (action === "dataset_preview") return { rows: 32, format: "alpaca", duplicate_rows: 0,
      samples: [{ instruction: "self-test", output: "ok" }] };
    if (action === "dataset") {
      const record = { dataset_id: data.dataset_id, version: data.version, rows: 32, synthetic: data.synthetic,
        export_allowed: data.export_allowed };
      datasets = [...datasets, record];
      return record;
    }
    if (action === "model_revision") return { model_revision: "a".repeat(40), tokenizer_revision: "a".repeat(40),
      template: "qwen3_5_nothink", architecture: "qwen3_5", max_length: 32768, recommended_precision: "bf16",
      hidden_layers: 24, metadata_verified: true };
    if (action === "plan") {
      const r = data.config.resource_profile;
      return { dry_run: true, manifest: { config: data.config, dataset: { dataset_id: data.config.dataset_id, rows: 32 } },
        estimated_cost_usd: r.timeout_seconds / 3600 * r.hourly_cost_usd * r.gpu_count, note: "self-test plan",
        compatibility: { observed_at: Date.now() / 1000, findings: [warn] } };
    }
    if (action === "submit") {
      const job = { id: "5e1f7e57" + "0".repeat(24), attempts: 0, updated: Date.now() / 1000,
        manifest: { config: data.config, simulation: false } };
      jobs = [job, ...jobs];
      return { ...job, status: "QUEUED" };
    }
    if (action === "execute") {
      executed = Date.now();
      await new Promise(resolve => setTimeout(resolve, 12000));
      return { status: "COMPLETED", runtime_cleanup: { removed: ["0.5.0-0123456789abcdef"],
        free_before: 10 * 1024 ** 3, free_after: 12.5 * 1024 ** 3 } };
    }
    if (action === "job_progress") return { available: true, points: [{ step: 1, loss: 2.5 }, { step: 8, loss: 1.234 }],
      current_step: 8, total_steps: 8, loss: 1.234, elapsed_time: "0:00:12", remaining_time: "0:00:00", percent: 100 };
    if (action === "logs") return data.name === "evaluation.json" ? { text: '{"passed": true}' }
      : { text: "{'loss': 2.5, 'step': 1}\n{'train_loss': 1.234, 'train_runtime': 12.0}\n" };
    return {};
  };
  window.__TAURI_INTERNALS__ = { invoke: async (command, args) => {
    if (command === "settings_get") return settings;
    if (command === "factory_action") return factory(args.action, args.data || {});
    return null;
  } };
})();"""


def run_selftest(args, log, now):
    sync_playwright = import_playwright()
    paid = paid_mode(args)
    dataset, version = prepare_dataset(args, now) if paid else (None, None)
    args.poll_seconds = min(args.poll_seconds, 1.0)
    server, url = None, args.ui_url
    if not url:
        port = free_port()
        server = start_dev_server(port, args.state_dir / "vite.log")
        url = f"http://127.0.0.1:{port}"
    log.write("launch", True, selftest=True, url=url, paid=paid)
    try:
        wait_http(lambda: reachable(url), 60, server, "the Vite dev server")
        with sync_playwright() as playwright:
            try:
                browser = playwright.chromium.launch()
            except Exception:   # no bundled Chromium for this Playwright: use the system Edge
                browser = playwright.chromium.launch(channel="msedge")
            try:
                context = browser.new_context(viewport={"width": 1320, "height": 900})
                context.add_init_script(selftest_bridge())
                page = context.new_page()
                page.set_default_timeout(30_000)
                page.goto(url)
                return walk(Walk(page, log, args), paid, dataset, version, browser=browser.version)
            finally:
                browser.close()
    finally:
        if server is not None:
            server.terminate()
            try:
                server.wait(10)
            except subprocess.TimeoutExpired:
                server.kill()


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(errors="backslashreplace")
    args = parse_args(argv)
    now = datetime.now()
    args.state_dir.mkdir(parents=True, exist_ok=True)
    log = StepLog(args.state_dir)
    mode = ("selftest-ui " if args.selftest_ui else "") + ("paid" if paid_mode(args) else "read-only")
    summary = {"mode": mode, "state_dir": str(args.state_dir), "started": now_iso(), "playwright": PLAYWRIGHT_PIN}
    code = 1
    try:
        summary.update(run_selftest(args, log, now) if args.selftest_ui else run_installed(args, log, now))
        code = 0 if summary.get("passed") else 1
    except StepFailed as failure:
        summary.update(passed=False, failed_step=failure.step, message=str(failure))
    except SetupError as error:
        log.write("setup", False, message=str(error))
        summary.update(passed=False, failed_step="setup", message=str(error))
    finally:
        summary.update(log.outcome, exit_code=code, finished=now_iso())
        (args.state_dir / "summary.json").write_text(
            json.dumps(scrub(summary), ensure_ascii=False, indent=2, default=str) + "\n", encoding="utf-8")
        print(f"GUI acceptance {'passed' if code == 0 else 'FAILED'} ({mode}): {args.state_dir}")
    return code


if __name__ == "__main__":
    sys.exit(main())
