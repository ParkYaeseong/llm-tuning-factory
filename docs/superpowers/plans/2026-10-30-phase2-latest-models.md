# Qwen3.5·Gemma 4 텍스트 학습 (2단계)과 1단계 후속 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 데스크톱 앱 0.6.0. 1단계 후속(F2 SSH keepalive, F3 GPU 서버 요금 판정, F1 이전 학습 환경 정리)을 먼저 끝내고,
Qwen3.5·Gemma 4(비전·오디오 겸용 구조)를 텍스트 데이터로 SFT·DPO·KTO·PT 학습·평가·병합할 수 있게 한다.
A40 GPU 인수 시험과 설치된 앱 화면으로 처음부터 끝까지 하는 시험으로 확인한다.

**Architecture:**
- 구조 정책(허용 단계·방법·템플릿·복합 구조 집합)은 `model_hub.ARCHITECTURES`·`COMPOSITE_ARCHITECTURES` 한 곳에 둔다.
- 모델 크기 사실(`parameters_total`, `text_parameters`, `hidden_size`, `num_layers`, `moe`, `tie_word_embeddings`)은
  기본 목록과 허브 프로필이 같은 키로 내고, `gpu_compat.estimate_memory`가 읽는다.
- 설정 변환(`training.py`)은 복합 구조에만 동결 키를, 추론 템플릿에만 `enable_thinking: false`를 더한다.
- 복합 구조의 LoRA 대상은 "`model.language_model.` 아래 Linear"라는 한 규칙으로 정한다(LLaMA-Factory 0.9.5의
  `find_all_linear_modules` + `patch_target_modules`와 같은 결과). meta 검사(`target_inspection`), 헤더 증거(`hf_targets`),
  검증(`verify_inspected_targets`), 병합·확정(`model_ops`)이 이 규칙을 공유한다.
- 인프라 어댑터(`execution.py`, `desktop_runtime.py`, `gpu_servers.py`)는 keepalive·사용 잠금·정리만 맡고,
  학습 어댑터(`training.py`, `model_ops.py`)와 섞지 않는다.

**Tech Stack:** Python 3.12, uv, paramiko, httpx, pydantic, pytest; 모델 작업 CPU 환경(`.factory/model-ops-cpu-venv`:
torch 2.7.1+cpu, transformers 5.6.0, peft 0.18.1); TypeScript/Vite, Playwright(Node 1.64.0 시험, Python `connect_over_cdp`
화면 시험); Tauri 2/NSIS.

**Spec:** [설계](../specs/2026-10-30-phase2-latest-models-design.md). 사용자가 프로젝트 전체를 승인했다(추가 승인 단계 없음).

**Branch:** `claude/phase2-models` (PR #5 `claude/gpu-compat-runtime` 위에 쌓음).

**Order:** F2 → F3 → F1(Task 1–5), 2단계 엔진(Task 6–14), 버전·문서(15–16), 빌드·전체 검증(17), GPU 인수(18), 검증 반영(19),
화면 끝까지 시험(20–21), 최종(22). 설정 변환(Task 11)은 목록 항목과 `model_entry`(Task 8–9)를 쓰므로 그 뒤에 둔다.

---

## Global Constraints

- Python 3.12, `uv sync --extra dev --extra tracking --locked`. 각 Task 끝에 해당 시험을, 완료 보고 전에 `uv run pytest`와
  `uv run ruff check src tests scripts`를 실행한다. 화면을 바꾼 Task는 `desktop`에서 `npm test`와 `npm run build`도 실행한다.
- 학습 어댑터와 인프라 어댑터를 분리한다. 매니페스트·설정·문서에 자격 증명을 넣지 않는다. 기본은 dry-run이다.
- 유료 자원은 Task 18(GPU 인수: 공장 소유 Pod를 `ManagedPods`로, 합성 데이터만, MLflow 끔)과
  Task 21(사용자 Pod `amdva0lurw9rv1`을 시험 동안만 켜고 끝나면 멈춤, 사용자 요청)에서만 쓴다.
  사용자 Pod를 자동으로 멈추거나 지우는 코드를 넣지 않는다. Task 21의 정지는 사용자가 요청한 일회성 운영 조작이다.
- `tests/test_model_inputs.py`의 `LEGACY_MATRIX_SHA256`·`EXAMPLE_SHA256`은 바꾸지 않는다. 새 YAML 키는 새 구조·템플릿에만 쓴다.
- Edit 도구로 `\u` 이스케이프를 입력하지 않는다(문자 그대로 들어간다). 한글·기호는 그대로 쓰고, 필요한 특수 문자는 `chr()`로 만든다.
- 시험·스크립트에 PEM 키 머리글을 문자 그대로 쓰지 않는다(비밀 검사가 git 이력을 본다). `"-" * 5 + "BEGIN ..."`처럼 조립한다.
- `src/**/*.py`는 LF 줄바꿈. 사용자에게 보이는 거부·안내는 한국어, 기존 시험이 맞추는 내부 영문 문구는 유지한다.
- 실제 검증 결과와 남은 GPU 작업은 `docs/PROGRESS.md`에 기록한다. 통과하지 않은 결과를 통과로 쓰지 않는다.

## File Structure

| 파일 | 책임 |
|---|---|
| `src/tuning_factory/execution.py` | `SSH_KEEPALIVE_SECONDS`, `keep_alive`, `SSHExecutor(runtime_lock_file=)`, `_launch_line` |
| `src/tuning_factory/desktop_runtime.py` | 연결 keepalive, `prerequisites`의 flock, `PRUNE_SOURCE`·`prune_report`, 준비 스크립트 정리, 보고의 `cleanup` |
| `src/tuning_factory/gpu_servers.py` | `server_hourly_cost(settings)` |
| `src/tuning_factory/gpu_compat.py` | `_price_findings`, `evaluate(..., server=)`, 크기 사실 기반 `estimate_memory` |
| `src/tuning_factory/desktop.py` | 서버 요금 전달, 제출 전 한국어 거부, `model_entry` 사용, 기본 목록 우선 |
| `src/tuning_factory/model_hub.py` | `COMPOSITE_ARCHITECTURES`, 단계 정책, `gemma4n`, `ModelProfile` 크기 사실, `imported_model` |
| `src/tuning_factory/capabilities.py` | 19개 모델 항목, `model_entry`, 단계·freeze 한국어 거부 |
| `src/tuning_factory/training.py` | 복합 동결 키, `REASONING_TEMPLATES`·`enable_thinking` |
| `src/tuning_factory/model_artifacts.py` | `PROCESSOR_FILES`, `is_processor_file` |
| `operation_runner.py`, `operation_dispatch.py`, `operation_outputs.py`, `workflow_planning.py`, `simulated_dispatch.py`, `model_ops.py` | processor 파일 수급·복사, Qwen3.5 샤드 이름, vocab, 복합 대상·병합 |
| `src/tuning_factory/target_inspection.py` | `COMPOSITE_MODEL_TYPES`, `LANGUAGE_MODEL_PREFIX`, `resolved_lora_targets`, `target_leaves`, `unloaded_prefixes`, 복합 meta 검사 |
| `src/tuning_factory/hf_targets.py`, `graph_planner.py` | 복합 헤더 증거, 대상 집합 비교 |
| `scripts/verify_model_ops_cpu.py` | 작은 `qwen3_5`·`gemma4` 병합·확정·대상 검사 |
| `scripts/desktop_gui_acceptance.py` (생성) | 설치된 앱 CDP 조작 |
| `desktop/src/gpu-preflight.ts`, `server-connection.ts`, `main.ts` | 정리 안내·결과 |
| 시험: `tests/test_execution.py`, `test_gpu_servers.py`, `test_controller_entry.py`, `test_remote_worker.py`, `test_gpu_compat.py`, `test_desktop_servers.py`, `test_desktop_runtime.py`, `test_model_hub.py`, `test_training_capabilities.py`, `test_desktop.py`, `test_hf_targets.py`, `test_model_ops_contract.py`, `test_operation_runner.py`, `test_workflow_planning.py`, `test_desktop_gui_acceptance.py`(생성), `desktop/tests/workspace.spec.ts`, `connections.spec.ts` | |
| 버전 파일 5개, `tests/test_release_version.py`, `docs/OPERATIONS.md`, `docs/WINDOWS_APP.md`, `docs/TRAINING_AND_STORAGE.md`, `docs/PROGRESS.md` | 0.6.0과 문서 |

---

### Task 1: F2 — SSH keepalive

**Files:**
- Modify: `src/tuning_factory/execution.py` (상단 상수·함수, `SSHExecutor.__init__` 310-311행 연결 직후)
- Modify: `src/tuning_factory/desktop_runtime.py:128-130`(GPU 서버 연결), `:151-153`(RunPod 연결)
- Test: `tests/test_execution.py`; 가짜 클라이언트 `tests/test_gpu_servers.py:117`, `:233`, `:955`(FakeServer),
  `tests/test_controller_entry.py:733`, `tests/test_remote_worker.py:1592`
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_execution.py` 끝에 추가
```python
def test_keep_alive_sets_a_30_second_ssh_keepalive():
    from types import SimpleNamespace

    from tuning_factory.execution import SSH_KEEPALIVE_SECONDS, keep_alive
    calls = []
    keep_alive(SimpleNamespace(get_transport=lambda: SimpleNamespace(set_keepalive=calls.append)))
    keep_alive(SimpleNamespace(get_transport=lambda: None))   # no transport: nothing to configure
    assert SSH_KEEPALIVE_SECONDS == 30 and calls == [30]
```

가짜 클라이언트 다섯 곳에 `get_transport`를 더한다(`SimpleNamespace` import가 없으면 더한다).
- `test_gpu_servers.py:117`(`test_ssh_connection_uses_nonroot_and_never_runpod`): 함수 안에 `keepalives = []`,
  `def get_transport(self): return SimpleNamespace(set_keepalive=keepalives.append)`, 끝에 `assert keepalives == [30]`.
- `test_controller_entry.py:733` `class SSH`: 같은 방식, 시험 끝에 `assert keepalives == [30]`.
- `test_gpu_servers.py:233`, FakeServer의 `Client`(955), `test_remote_worker.py:1592` `OwnClient`:
  `def get_transport(self): return SimpleNamespace(set_keepalive=lambda seconds: None)`.
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_execution.py::test_keep_alive_sets_a_30_second_ssh_keepalive tests/test_gpu_servers.py::test_ssh_connection_uses_nonroot_and_never_runpod -v` — Expected: FAIL (`ImportError: cannot import name 'SSH_KEEPALIVE_SECONDS'`, `assert [] == [30]`)
- [ ] **Step 3: 구현** — `execution.py` import 바로 아래
```python
SSH_KEEPALIVE_SECONDS = 30   # long silent probes (up to 900 s) and result polls must survive idle NAT/proxy timeouts

def keep_alive(client):
    """Ask paramiko to send SSH keepalives on a connected client; a client without a transport is left alone."""
    transport = client.get_transport()
    if transport is not None:
        transport.set_keepalive(SSH_KEEPALIVE_SECONDS)
```

- `SSHExecutor.__init__`: `self.client.connect(...)` 다음 줄(`if client is None:` 안)에 `keep_alive(self.client)`.
- `desktop_runtime.py`: `.execution`에서 `keep_alive`를 import하고, 두 `client.connect(...)` 바로 다음에 `keep_alive(client)`.
- `scripts/`의 승인 세션 도구는 바꾸지 않는다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_execution.py tests/test_gpu_servers.py tests/test_controller_entry.py tests/test_remote_worker.py tests/test_desktop_runtime.py tests/test_desktop_servers.py tests/test_workbench_backend.py -q` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/execution.py src/tuning_factory/desktop_runtime.py tests/test_execution.py tests/test_gpu_servers.py tests/test_controller_entry.py tests/test_remote_worker.py
git commit -m "fix: send SSH keepalives every 30 seconds on app connections"
```

---

### Task 2: F3 — GPU 서버 요금을 호환성 판정에 넣기

**Files:**
- Modify: `src/tuning_factory/gpu_compat.py:104`(문구), `:111-125`(`evaluate`), `:179-188`(RunPod 요금)
- Modify: `src/tuning_factory/gpu_servers.py` (`server_settings` 뒤에 `server_hourly_cost`)
- Modify: `src/tuning_factory/desktop.py:161-167`(`compatibility`), `:422-430`(`submit`의 서버 오프라인 거부)
- Test: `tests/test_gpu_compat.py`, `tests/test_gpu_servers.py`, `tests/test_desktop_servers.py`
- [ ] **Step 1: 실패하는 시험 작성**
`tests/test_gpu_compat.py` 끝(기존 `job`, `observed`, `CAPS`, `QWEN05` 재사용):
```python
@pytest.mark.parametrize("price,code", [(0.6, None), (0, None), (0.61, "PRICE_OVER_BUDGET"), (None, "PRICE_UNKNOWN"),
                                        (float("nan"), "PRICE_UNKNOWN"), (-1, "PRICE_UNKNOWN"), (True, "PRICE_UNKNOWN")])
def test_a_server_price_is_judged_like_a_pod_price_but_zero_is_allowed(price, code):
    for observation in (observed(pod=None), None):   # judged before the missing-observation return
        findings = gpu_compat.evaluate(job(), observation, CAPS, QWEN05, stage="execute",
                                       server={"hourly_cost_usd": price})
        assert {item["code"] for item in findings if item["level"] == "block"} == ({code} if code else set())

def test_an_unknown_server_price_points_to_the_connection_settings():
    findings = gpu_compat.evaluate(job(), None, CAPS, QWEN05, stage="plan", server={"hourly_cost_usd": None})
    assert gpu_compat.SERVER_PRICE_UNKNOWN in gpu_compat.refusal(findings)
```

`tests/test_gpu_servers.py` 끝:
```python
@pytest.mark.parametrize("saved,price", [
    ({"server_total_hourly_cost_usd": "1.5"}, 1.5), ({"server_total_hourly_cost_usd": " 0 "}, 0.0),
    ({"server_total_hourly_cost_usd": ""}, None), ({"server_total_hourly_cost_usd": "abc"}, None),
    ({"server_total_hourly_cost_usd": "-1"}, None), ({"server_total_hourly_cost_usd": "nan"}, None),
    ({"server_ssh_host": "gpu.example"}, None), ({"total_hourly_cost_usd": 2}, 2.0),
    ({"total_hourly_cost_usd": True}, None)])
def test_server_hourly_cost_reads_only_the_saved_price_offline(saved, price):
    from tuning_factory.gpu_servers import server_hourly_cost
    assert server_hourly_cost({"provider": "ssh_server", **saved}) == {"hourly_cost_usd": price}
```

`tests/test_desktop_servers.py`(`test_submit_runs_offline_refusals_before_observing_the_server` 뒤):
```python
def test_a_server_price_over_the_job_budget_is_refused_in_korean_before_ssh(tmp_path, monkeypatch):
    offline(monkeypatch)
    app = desktop.DesktopService(tmp_path, native(server_total_hourly_cost_usd="1.5"))
    config = zero_cost_config(app)
    findings = app.dispatch("plan", {"config": config})["compatibility"]["findings"]
    assert ("block", "PRICE_OVER_BUDGET") in {(item["level"], item["code"]) for item in findings}
    planned, _ = app.server_destination()
    with pytest.raises(ValueError, match=r"^실행할 수 없습니다\. 현재 시간당 요금 \$1\.500"):
        app.dispatch("submit", {"approved": True, "config": config, "destination_sha256": planned})
    assert app.dispatch("jobs", {}) == []
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_gpu_compat.py tests/test_gpu_servers.py tests/test_desktop_servers.py -k "server_price or server_hourly_cost" -v` — Expected: FAIL (`TypeError: evaluate() got an unexpected keyword argument 'server'`, `ImportError`)
- [ ] **Step 3: 구현**
`gpu_compat.py` — `PRICE_UNKNOWN` 아래 문구와 공유 함수, `evaluate` 변경:
```python
SERVER_PRICE_UNKNOWN = ("GPU 서버의 시간당 비용이 비어 있거나 올바르지 않습니다. 연결 설정에서 0 이상의 숫자를 입력하세요"
                        "(내부 서버는 0).")

def _price_findings(price, resource, *, allow_zero, unknown):
    """PRICE_UNKNOWN or PRICE_OVER_BUDGET for one destination's hourly price against the job's per-GPU budget × GPUs.
    A server may cost 0 (internal hardware); a RunPod price of 0 means the API gave no price."""
    if (isinstance(price, bool) or not isinstance(price, (int, float)) or not math.isfinite(price) or price < 0
            or (price == 0 and not allow_zero)):
        return [_finding("block", "PRICE_UNKNOWN", unknown)]
    limit = float(resource.get("hourly_cost_usd", 0)) * int(resource.get("gpu_count", 1))
    if price > limit:
        return [_finding("block", "PRICE_OVER_BUDGET",
                         f"현재 시간당 요금 ${price:.3f}가 학습 설정의 한도 ${limit:.3f}를 넘습니다.")]
    return []
```

- 시그니처: `def evaluate(config, observation, capabilities, model, *, stage, now=None, server=None):`.
  독스트링에 "``server`` is ``{"hourly_cost_usd": float | None}`` for an SSH server destination (judged even without an
  observation)"을 더한다.
- `if observation is None:` 바로 앞에:
  ```python
  if server is not None:
      findings += _price_findings(server.get("hourly_cost_usd"), config.get("resource_profile") or {},
                                  allow_zero=True, unknown=SERVER_PRICE_UNKNOWN)
  ```
- 179-188행 RunPod 블록을 다음으로 바꾼다(서버 관측은 `pod`가 `None`이라 중복되지 않는다):
  ```python
  if pod is not None:
      findings += _price_findings(pod.get("hourly_cost_usd"), resource, allow_zero=False, unknown=PRICE_UNKNOWN)
  ```

`gpu_servers.py` — `server_settings` 다음:
```python
def server_hourly_cost(settings):
    """Offline {"hourly_cost_usd": float | None} of the saved server price; never raises (None = unknown).

    Only the price field is read, so an unrelated invalid field (host, key, pin) is not reported as a price problem."""
    native = any(key.startswith("server_") for key in settings)
    value = settings.get("server_total_hourly_cost_usd" if native else "total_hourly_cost_usd")
    if isinstance(value, str):
        value = value.strip() or None
    try:
        price = None if value is None or isinstance(value, bool) else float(value)
    except (TypeError, ValueError):
        price = None
    return {"hourly_cost_usd": price if price is not None and math.isfinite(price) and price >= 0 else None}
```
(`math`가 import되지 않았으면 더한다.)

`desktop.py`:
- `compatibility`: `from .gpu_servers import server_hourly_cost`. `evaluate(...)` 직전에
  ```python
  try:
      server = server_hourly_cost(self.settings) if self.is_server() else None
  except ValueError:   # an unsupported provider is reported by the connection screen, not by this judgement
      server = None
  ```
  `evaluate(..., stage=stage, server=server)`로 넘긴다.
- `submit`의 `if server:` 블록에서 데이터 전송 허용 검사 `for` 반복 다음, `plan_server_destination` 호출 앞에:
  ```python
  # Price and other offline blocks in Korean before the read-only server observation opens SSH.
  message = refusal(self.compatibility(manifest["config"], None, "execute"))
  if message:
      raise ValueError(message)
  ```
  `from .gpu_compat import refusal`을 더하고, 424행 주석 "the price check needs the observed server facts below"는
  "the observed server facts are checked again below"로 고친다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_gpu_compat.py tests/test_gpu_servers.py tests/test_desktop_servers.py tests/test_desktop.py tests/test_desktop_runtime.py -q` — Expected: PASS. `test_plan_is_offline_and_returns_the_saved_destination_hash`·`test_plan_reports_incomplete_server_settings_without_raising`는
계획이 예외 없이 끝나야 한다(이제 PRICE 차단 항목이 생길 뿐이다). 다른 시험이 계획의 판정 목록을 정확히 비교해 실패하면 그 기대값에 새 PRICE 항목을 더한다.
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/gpu_compat.py src/tuning_factory/gpu_servers.py src/tuning_factory/desktop.py tests/test_gpu_compat.py tests/test_gpu_servers.py tests/test_desktop_servers.py
git commit -m "feat: judge GPU server prices in the compatibility check and refuse them in Korean before SSH"
```

---

### Task 3: F1-a — 학습 환경 사용 표시(`flock -s`)와 flock 사전 조건

**Files:**
- Modify: `src/tuning_factory/execution.py:279-321`(`runtime_lock_file`), `:466-475`(v1), `:650-656`(v2)
- Modify: `src/tuning_factory/desktop_runtime.py:186-191`(`prerequisites`), `:526-531`·`:551-555`(`executor_for`)
- Test: `tests/test_remote_worker.py:95`, `:885-890`, `:918-940`, `:1156-1173`; `tests/test_desktop_runtime.py`
- [ ] **Step 1: 실패하는 시험 작성**
`tests/test_remote_worker.py`:
- 95행 `directory = self.sftp.path(args[4])` → `directory = self.sftp.path(args[args.index("tuning_factory.worker") + 1])`.
- `v2_transport`(918-940)의 `SSHExecutor(...)`에 `runtime_lock_file=base + "/runtime/in-use.lock"`를 넘긴다.
- `GPUHost.launch` 888-890행:
  ```python
  assert args[:3] == ["nohup", "flock", "-s"] and args[3].endswith("/runtime/in-use.lock") and args[4] == "env"
  assert args[-6] == ">>" and args[-4:] == ["2>&1", "<", "/dev/null", "&"]
  for item in args[5:index - 2]:
  ```
- 1163행: `assert args[:5] == ["nohup", "flock", "-s", t.base + "/runtime/in-use.lock", "env"] and dict(item.split("=", 1) for item in args[5:index - 2]) == {`(나머지 그대로).
- 파일 끝에 추가:
```python
def test_launch_lines_hold_the_runtime_in_use_lock_shared():
    plain = SSHExecutor("", 22, "root", "", client=SimpleNamespace())
    held = SSHExecutor("", 22, "root", "", client=SimpleNamespace(), runtime_lock_file="/w/runtime/abc/in-use.lock")
    command = ["env", "A=1", "/w/venv/bin/python", "-m", "tuning_factory.worker", "/w/jobs/j/a"]
    assert plain._launch_line(command, ">", "/w/jobs/j/a/worker.log") == (
        "nohup env A=1 /w/venv/bin/python -m tuning_factory.worker /w/jobs/j/a"
        " > /w/jobs/j/a/worker.log 2>&1 < /dev/null &")
    assert held._launch_line(command, ">>", "/w/jobs/j/a/worker.log") == (
        "nohup flock -s /w/runtime/abc/in-use.lock env A=1 /w/venv/bin/python -m tuning_factory.worker /w/jobs/j/a"
        " >> /w/jobs/j/a/worker.log 2>&1 < /dev/null &")
```

`tests/test_desktop_runtime.py`:
- `test_executor_for_gates_first_and_prepares_only_when_approved`(573)의 두 번째 `with` 안:
  `assert executor.pod == POD and executor.runtime_lock_file == runtime.runtime_path("sft") + "/in-use.lock"`.
- 새 시험:
```python
def test_prerequisites_require_flock_and_a_c_compiler(monkeypatch):
    from tuning_factory import desktop_runtime as runtime
    scripts = []
    monkeypatch.setattr(runtime, "command", lambda _, argv, **kw: scripts.append(argv) or "")
    runtime.prerequisites(None)
    assert len(scripts) == 1 and scripts[0][:2] == ["sh", "-c"]
    assert "command -v flock" in scripts[0][2] and "flock (util-linux) required" in scripts[0][2]
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_remote_worker.py tests/test_desktop_runtime.py -k "launch or prerequisites_require or gates_first" -v` — Expected: FAIL (`TypeError: ... unexpected keyword argument 'runtime_lock_file'`)
- [ ] **Step 3: 구현**
`execution.py`:
- `__init__`에 키워드 `runtime_lock_file=None`을 더하고 `self.runtime_lock_file = runtime_lock_file`.
- 메서드 추가:
  ```python
  def _launch_line(self, command, redirect, log):
      """The detached worker launch. With a runtime lock the worker holds it shared for its whole life, so
      desktop_runtime.PRUNE_SOURCE never removes a runtime that a running attempt uses."""
      prefix = ["nohup", "flock", "-s", self.runtime_lock_file] if self.runtime_lock_file else ["nohup"]
      return shlex.join([*prefix, *command]) + f" {redirect} " + shlex.quote(log) + " 2>&1 < /dev/null &"
  ```
- v1 473행: `launch = self._launch_line(command, ">", remote + "/worker.log")`.
- v2 653-654행: `launch_line = self._launch_line(command, ">>", remote + "/worker.log")`.

`desktop_runtime.py`:
- `prerequisites`의 `sh -c` 문자열 맨 앞에 다음을 붙인다(인자 수·시간 제한은 그대로):
  `'command -v flock >/dev/null || { printf "%s\\n" "flock (util-linux) required to mark runtimes in use; ask its administrator to install util-linux." >&2; exit 1; }; '`
- `executor_for`의 두 `SSHExecutor(...)`에 `runtime_lock_file=root + "/in-use.lock"`.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_remote_worker.py tests/test_desktop_runtime.py tests/test_gpu_servers.py tests/test_desktop_servers.py tests/test_workbench_backend.py tests/test_executor_profiles.py -q` — Expected: PASS. 다른 가짜가 실행 줄을 고정 위치로 읽어 실패하면 `args.index("tuning_factory.worker") + 1`로 바꾼다.
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/execution.py src/tuning_factory/desktop_runtime.py tests/test_remote_worker.py tests/test_desktop_runtime.py
git commit -m "feat: hold a shared runtime lock while workers run and require flock"
```

---

### Task 4: F1-b — 이전 학습 환경 정리

**Files:**
- Modify: `src/tuning_factory/desktop_runtime.py` (`install_runtime` 앞에 `PRUNE_SOURCE`·`prune_report`, 324-347행 스크립트,
  350-397행 `runtime()` 보고)
- Test: `tests/test_desktop_runtime.py` (`:503-523` 기대값 수정과 새 시험)
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop_runtime.py` 끝에 추가
```python
CURRENT, OLD, OLDER = "a" * 16, "b" * 16, "c" * 16

def prune_tree(root):
    """A remote root: current runtimes, two old ones, a non-runtime folder, a leftover trash folder and the caches."""
    for name in (CURRENT, CURRENT + "-ppo", OLD, OLDER + "-ppo", "notahash", ".trash-dead-1"):
        (root / "runtime" / name / "venv").mkdir(parents=True)
    (root / "runtime" / OLD / "venv" / "lib").write_bytes(b"x" * 10)
    for name in ("uv-cache", "python", "hf-cache", "model-cache", "jobs"):
        (root / name).mkdir()
    return root

def attempt(root, source, *, manifest=None, finished=False, age=0):
    directory = root / "jobs" / "j" / source[:4]
    directory.mkdir(parents=True)
    (directory / "worker.log").write_text("started")
    if finished:
        (directory / "result.json").write_text("{}")
    (directory / "manifest.json").write_text(manifest if manifest is not None else json.dumps(
        {"source_sha256": source * 4, "config": {"resource_profile": {"timeout_seconds": 60}}}))
    os.utime(directory, (time.time() - age, time.time() - age))

def prune(root):
    import subprocess

    from tuning_factory import desktop_runtime as runtime
    done = subprocess.run([sys.executable, "-I", "-c", runtime.PRUNE_SOURCE, str(root), CURRENT],
                          capture_output=True, text=True, timeout=120, check=True)
    return runtime.prune_report(done.stdout)

def test_prune_removes_only_old_idle_runtimes(tmp_path):
    root = prune_tree(tmp_path)
    attempt(root, OLD, finished=True)
    report = prune(root)
    assert sorted(item["name"] for item in report["removed"]) == [OLD, OLDER + "-ppo"]
    assert next(item["bytes"] for item in report["removed"] if item["name"] == OLD) == 10
    assert sorted(path.name for path in (root / "runtime").iterdir()) == sorted([CURRENT, CURRENT + "-ppo", "notahash"])
    assert all((root / name).is_dir() for name in ("uv-cache", "python", "hf-cache", "model-cache", "jobs"))
    assert "cleanup_error" not in report and report["free_before"] > 0 and report["free_after"] > 0

def test_prune_keeps_the_runtime_of_a_live_attempt_and_forgets_expired_ones(tmp_path):
    root = prune_tree(tmp_path)
    attempt(root, OLD)                                    # unfinished, within its deadline
    attempt(root, OLDER, age=60 + 3600 + 5, manifest=json.dumps(
        {"source_sha256": OLDER * 4, "operation": {"resources": {"timeout_seconds": 60}}}))   # v2, expired
    report = prune(root)
    assert report["kept_in_use"] == [OLD] and [item["name"] for item in report["removed"]] == [OLDER + "-ppo"]

def test_prune_deletes_nothing_when_an_unfinished_manifest_is_unreadable(tmp_path):
    root = prune_tree(tmp_path)
    attempt(root, OLD, manifest="{")
    report = prune(root)
    assert report["removed"] == [] and "unreadable" in report["cleanup_error"]
    assert (root / "runtime" / OLD).is_dir() and (root / "runtime" / (OLDER + "-ppo")).is_dir()

@pytest.mark.skipif(sys.platform == "win32", reason="fcntl locks and symlinks are exercised on Linux only")
def test_prune_keeps_locked_runtimes_and_never_follows_symlinks(tmp_path):
    import fcntl
    root = prune_tree(tmp_path)
    (root / "runtime" / ("d" * 16)).symlink_to(root / "hf-cache", target_is_directory=True)
    with open(root / "runtime" / OLD / "in-use.lock", "a") as held:
        fcntl.flock(held, fcntl.LOCK_SH)                  # a running worker's `flock -s`
        report = prune(root)
    assert report["kept_in_use"] == [OLD] and [item["name"] for item in report["removed"]] == [OLDER + "-ppo"]
    assert (root / "runtime" / ("d" * 16)).is_symlink() and (root / "hf-cache").is_dir()

def test_preparation_prunes_under_its_prepare_lock_and_keeps_the_report(tmp_path, monkeypatch):
    import shlex
    runtime, client = install_fixture(tmp_path, monkeypatch)
    cleanup = {"removed": [{"name": OLD, "bytes": 10}], "kept_in_use": [], "free_before": 1, "free_after": 11}
    scripts = []
    monkeypatch.setattr(runtime, "command",
                        lambda _, argv, **kw: scripts.append(argv[2]) or "pip ok\nTF_PRUNE " + json.dumps(cleanup))
    preparation = {"started": False, "stopped": False}
    runtime.install_runtime(client, "sft", runtime.REMOTE, POD, preparation)
    script = scripts[0]
    assert script.index("TF_PREPARE_BUSY") < script.index(shlex.quote(runtime.PRUNE_SOURCE)) < script.index(" venv ")
    assert f"{shlex.quote(runtime.REMOTE)} {runtime.source_hash()[:16]} || true" in script
    assert preparation == {"started": True, "stopped": True, "cleanup": cleanup}

def test_a_prepared_runtime_reports_its_cleanup(tmp_path, monkeypatch):
    events = []
    runtime = runpod_executor_fixture(monkeypatch, ["ready", "ready"], events)
    cleanup = {"removed": [], "kept_in_use": [OLD], "free_before": 1, "free_after": 1}
    monkeypatch.setattr(runtime, "install_runtime",
                        lambda client, stage, root, pod, preparation: preparation.update(cleanup=cleanup))
    assert runtime.runtime({"pod_id": "borrowed"}, tmp_path, prepare=True)["cleanup"] == cleanup
    assert "cleanup" not in runtime.runtime({"pod_id": "borrowed"}, tmp_path)   # a check never prunes
```
(파일 상단에 `os`, `time`, `json`, `sys` import가 없으면 더한다. `source_hash`는 `desktop_runtime`이 이미 import한다.)

`test_install_runtime_uses_the_cu128_backend`(503-523)의 준비 부분(assets·`FakeSSH`·`assets_root` 바꾸기)을
`install_fixture(tmp_path, monkeypatch) -> (runtime, client)`로 꺼내 두 시험이 함께 쓰고, 그 시험의 521행 기대값을 바꾼다:
```python
    assert preparation == {"started": True, "stopped": True, "cleanup": {
        "removed": [], "kept_in_use": [], "cleanup_error": "cleanup report missing"}}
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_desktop_runtime.py -k "prune or cleanup or cu128" -v` — Expected: FAIL (`AttributeError: ... no attribute 'PRUNE_SOURCE'`)
- [ ] **Step 3: 구현** — `desktop_runtime.py`, `install_runtime` 앞
```python
# Fixed source run by the preparation script under this runtime's prepare.lock. The only arguments are the validated
# remote root and the current 16-hex source hash, each separately quoted. It removes old app-version runtimes that no
# worker holds (`flock -s in-use.lock`), that no preparation holds and that no live attempt references; it never
# touches uv-cache/, python/, hf-cache/, model-cache/ or jobs/, and with any doubt it removes nothing.
PRUNE_SOURCE = r'''import json,os,re,shutil,sys,time
from pathlib import Path
try:
    import fcntl
except ImportError:
    fcntl=None
root,current=Path(sys.argv[1]),sys.argv[2]
runtime,jobs=root/'runtime',root/'jobs'
report=dict(removed=[],kept_in_use=[],free_before=None,free_after=None)
def size(path):
    total=0
    for base,dirs,files in os.walk(path):
        for name in files:
            try:
                total+=os.lstat(os.path.join(base,name)).st_size
            except OSError:
                pass
    return total
def live():
    found=set()
    for attempt in jobs.glob('*/*'):
        if attempt.is_symlink() or not attempt.is_dir() or not (attempt/'worker.log').exists() or (attempt/'result.json').exists():
            continue
        try:
            manifest=json.loads((attempt/'manifest.json').read_text())
            timeout=(manifest.get('config') or {}).get('resource_profile',{}).get('timeout_seconds') or (manifest.get('operation') or {}).get('resources',{}).get('timeout_seconds')
            if time.time()<attempt.stat().st_mtime+int(timeout)+3600:
                found.add(str(manifest['source_sha256'])[:16])
        except Exception:
            raise ValueError('unreadable manifest of an unfinished attempt; nothing removed') from None
    return found
def hold(path):
    if fcntl is None:
        return []
    handles=[]
    try:
        for name in ('in-use.lock','prepare.lock'):
            handles.append(open(path/name,'a'))
            fcntl.flock(handles[-1],fcntl.LOCK_EX|fcntl.LOCK_NB)
        return handles
    except OSError:
        for handle in handles:
            handle.close()
        return None
try:
    if not re.fullmatch('[0-9a-f]{16}',current):
        raise ValueError('invalid current runtime')
    report['free_before']=shutil.disk_usage(root).free
    referenced=live()
    for path in sorted(runtime.iterdir()) if runtime.is_dir() else ():
        name=path.name
        if path.is_symlink() or not path.is_dir():
            continue
        if name.startswith('.trash-'):
            shutil.rmtree(path,ignore_errors=True)
            continue
        if not re.fullmatch('[0-9a-f]{16}(-ppo)?',name) or name[:16]==current:
            continue
        handles=None if name[:16] in referenced else hold(path)
        if handles is None:
            report['kept_in_use'].append(name)
            continue
        try:
            removed=dict(name=name,bytes=size(path))
            trash=runtime/('.trash-%s-%d'%(name,os.getpid()))
            path.rename(trash)
        finally:
            for handle in handles:
                handle.close()
        shutil.rmtree(trash)
        report['removed'].append(removed)
except Exception as error:
    report['cleanup_error']=(type(error).__name__+': '+str(error))[:300]
try:
    report['free_after']=shutil.disk_usage(root).free
except OSError:
    pass
print('TF_PRUNE '+json.dumps(report,sort_keys=True))
'''

def prune_report(output):
    """The TF_PRUNE report of a preparation; a missing or unreadable report is a cleanup error, never fatal."""
    for line in reversed(output.splitlines()):
        if line.startswith("TF_PRUNE "):
            try:
                report = json.loads(line[len("TF_PRUNE "):])
            except ValueError:
                break
            if isinstance(report, dict):
                return report
            break
    return {"removed": [], "kept_in_use": [], "cleanup_error": "cleanup report missing"}
```

`fcntl`이 없으면(시험용 Windows) 잠금 확인만 건너뛰고 파일을 열지 않는다(열린 파일이 있으면 Windows가 폴더 이름 바꾸기를 막는다).
원격은 항상 Linux다. `hold`는 이름 바꾸기가 끝날 때까지 배타 잠금을 쥐어, 그 사이 시작하는 `flock -s`가 지워지는 경로를 쓰지 못하게 한다.

`install_runtime` 스크립트(327행 `flock -n 9 ...` 줄 바로 다음)에 한 줄을 더한다:
```python
        f"python3 -c {shlex.quote(PRUNE_SOURCE)} {shlex.quote(remote_root)} {source_hash()[:16]} || true\n"
```
341행을 `output = command(client, ["sh", "-c", script], timeout=1800)`로 바꾸고, 347행 `preparation["stopped"] = True` 다음에
`preparation["cleanup"] = prune_report(output)`.

`runtime()`: `if prepare:` 블록에서 `install_runtime(...)` 다음에 `cleanup = preparation.get("cleanup")`를 두고,
`report = {...}` 다음에 `if prepare: report["cleanup"] = cleanup`.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_desktop_runtime.py tests/test_gpu_servers.py tests/test_desktop_servers.py -q` — Expected: PASS (win32에서는 잠금·심볼릭 링크 시험 1개 건너뜀)
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/desktop_runtime.py tests/test_desktop_runtime.py
git commit -m "feat: prune unused old-version runtimes while preparing a runtime"
```

---

### Task 5: F1-c — 정리 안내와 결과 표시

**Files:**
- Modify: `desktop/src/gpu-preflight.ts:6-8`, `desktop/src/server-connection.ts:139-145`, `desktop/src/main.ts:718`, `:853`, `:961`
  (RunPod 준비 완료 안내가 나오는 곳도: `runtimeNotice(...)`를 쓰는 `runtime_prepare` 처리)
- Test: `desktop/tests/workspace.spec.ts:430-442`, `desktop/tests/connections.spec.ts:313-327`
- [ ] **Step 1: 실패하는 시험 작성**
  - `workspace.spec.ts` "a missing RunPod runtime is folded into the single run approval"(430):
    대화상자에 `toContainText("이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다")`를 더하고, 442행 정확한 문구를
    `"Pod에 학습 환경을 준비한 뒤 학습을 시작합니다. 최대 30분 걸릴 수 있으며, 쓰지 않는 이전 앱 버전 학습 환경을 정리합니다."`로 바꾼다.
  - `connections.spec.ts` "server runtime preparation inspects first, cancels cleanly and sends the exact inspection"(313):
    승인 대화상자에 같은 정리 문장을 확인한다. 그 시험의 가짜 `runtime_prepare` 응답에
    `cleanup: { removed: [{ name: "b".repeat(16), bytes: 5 * 1024 ** 3 }], kept_in_use: [] }`를 더하고, 완료 안내가
    `"이전 앱 버전의 학습 환경 1개를 지워 5.0GB를 확보했습니다."`를 포함하는지 확인한다.
- [ ] **Step 2: 실패 확인** — Run: `cd desktop && npx playwright test tests/workspace.spec.ts tests/connections.spec.ts -g "runtime" && cd ..` — Expected: FAIL (문구 없음)
- [ ] **Step 3: 구현** — `gpu-preflight.ts`
```ts
export const RUNTIME_CLEANUP_NOTICE = "준비 전에 같은 작업 폴더에서 이전 앱 버전의 학습 환경 중 쓰지 않는 것을 지웁니다"
  + "(실행 중인 작업의 환경, 모델·데이터 캐시, 작업 기록은 지우지 않습니다).";

/** The cleanup line after a preparation: removed old runtimes and the space they freed, or nothing. */
export function cleanupSummary(report: Obj | null | undefined): string {
  const cleanup = report?.cleanup;
  if (!cleanup) return "";
  if (cleanup.cleanup_error) return "\n이전 학습 환경 정리는 건너뛰었습니다(학습 환경 준비에는 영향이 없습니다).";
  const removed: Obj[] = cleanup.removed ?? [];
  if (!removed.length) return "";
  const bytes = removed.reduce((sum, item) => sum + (Number(item.bytes) || 0), 0);
  return `\n이전 앱 버전의 학습 환경 ${removed.length}개를 지워 ${(bytes / 1024 ** 3).toFixed(1)}GB를 확보했습니다.`;
}
```
- `RUNTIME_PREPARATION_NOTICE` 끝에 `" " + RUNTIME_CLEANUP_NOTICE`를 붙인다(상수 선언 순서를 맞춘다).
  `server-connection.ts`·`main.ts`는 `RUNTIME_CLEANUP_NOTICE`·`cleanupSummary`를 `./gpu-preflight`에서 import한다.
- `server-connection.ts` `prepareApproval`: 둘째 줄 끝 `"...변경하지 않습니다.\n"`을 `"...변경하지 않습니다. " + RUNTIME_CLEANUP_NOTICE + "\n"`로.
- `main.ts:961` RunPod "GPU 학습 환경 준비" 본문 끝에 `" " + RUNTIME_CLEANUP_NOTICE`.
- `main.ts:718` 안내를 Step 1의 새 문장으로.
- `main.ts:853` 서버 준비 완료 안내 끝과 RunPod `runtime_prepare` 완료 안내 끝에 `+ cleanupSummary(state.runtimeReport)`.
- [ ] **Step 4: 통과 확인** — Run: `cd desktop && npm test && npm run build && cd ..` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add desktop/src/gpu-preflight.ts desktop/src/server-connection.ts desktop/src/main.ts desktop/tests/workspace.spec.ts desktop/tests/connections.spec.ts
git commit -m "feat: tell users that runtime preparation prunes old runtimes and show what it freed"
```

---

### Task 6: processor 파일과 Qwen3.5 샤드 이름

`chat_template.jinja`는 이미 `TOKENIZER_FILES`에 있다. 빠진 것은 processor 파일 넷과 Qwen3.5의 비표준 샤드 이름
(`model.safetensors-00001-of-00002.safetensors`; `HF_ALLOW_PATTERNS`의 `model-*.safetensors`에 맞지 않아 v2 실행에서 가중치를
받지 않는다)이다. processor 파일을 `TOKENIZER_FILES`에 넣으면 `tokenizer_fingerprint`가 바뀌므로 별도 목록으로 둔다.

**Files:**
- Modify: `src/tuning_factory/model_artifacts.py:37-40`(목록), `:327-329`(판정 함수 옆)
- Modify: `operation_runner.py:74-75`, `model_ops.py:652-660`, `operation_dispatch.py:177-188`,
  `operation_outputs.py:173-175`·`:216-218`·`:259-263`, `workflow_planning.py:906`, `simulated_dispatch.py:280-282`
- Test: `tests/test_model_inputs.py`, `tests/test_model_ops_contract.py`, `tests/test_workflow_planning.py`, `tests/test_operation_runner.py`
- [ ] **Step 1: 실패하는 시험 작성**
`tests/test_model_inputs.py`(`test_hf_root_resolves_to_a_link_free_verified_local_directory` 뒤):
```python
def test_hf_download_includes_processor_files_and_qwen35_shard_names():
    from fnmatch import fnmatch

    from tuning_factory.model_artifacts import PROCESSOR_FILES
    from tuning_factory.operation_runner import HF_ALLOW_PATTERNS
    for name in (*PROCESSOR_FILES, "chat_template.jinja", "model.safetensors-00001-of-00002.safetensors",
                 "model-00001-of-00002.safetensors"):
        assert any(fnmatch(name, pattern) for pattern in HF_ALLOW_PATTERNS), name
    assert not any(fnmatch(name, pattern) for pattern in HF_ALLOW_PATTERNS
                   for name in ("pytorch_model.bin", "model.py", "consolidated.safetensors"))
```

`tests/test_model_ops_contract.py`:
```python
def test_snapshot_copy_carries_processor_files_without_changing_the_tokenizer_fingerprint(tmp_path):
    from tuning_factory import model_ops
    from tuning_factory.model_artifacts import tokenizer_fingerprint
    source, target = tmp_path / "base", tmp_path / "out"
    source.mkdir()
    target.mkdir()
    for name in ("config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json",
                 "video_preprocessor_config.json", "processor_config.json", "model.safetensors"):
        (source / name).write_text("{}")
    before = tokenizer_fingerprint(source)
    model_ops._copy_snapshot_files(source, target, config=False)
    assert sorted(path.name for path in target.iterdir()) == [
        "preprocessor_config.json", "processor_config.json", "tokenizer.json", "tokenizer_config.json",
        "video_preprocessor_config.json"]
    (source / "processor_config.json").unlink()
    assert tokenizer_fingerprint(source) == before
```

`tests/test_workflow_planning.py`(`test_model_root_snapshot_downloads_only_config_and_tokenizer_files` 뒤):
```python
def test_root_snapshot_selects_processor_files_beside_the_tokenizer():
    from tuning_factory.workflow_planning import _selected_files
    names = ["config.json", "tokenizer.json", "tokenizer_config.json", "preprocessor_config.json",
             "video_preprocessor_config.json", "processor_config.json", "chat_template.jinja", "README.md",
             "model.safetensors-00001-of-00001.safetensors", "model.safetensors.index.json"]
    assert _selected_files({"siblings": [{"rfilename": name} for name in names]}) == (
        "chat_template.jinja", "config.json", "preprocessor_config.json", "processor_config.json",
        "tokenizer.json", "tokenizer_config.json", "video_preprocessor_config.json")
```

`tests/test_operation_runner.py`: `operation_dispatch._write_base_snapshot(directory, base)`를 `base`에 config·tokenizer·
`preprocessor_config.json`·`model.safetensors`를 둔 임시 폴더로 호출하고, 돌려준 이름이
`["base-snapshot/config.json", "base-snapshot/preprocessor_config.json", "base-snapshot/tokenizer.json", "base-snapshot/tokenizer_config.json"]`
(정렬)인지 확인하는 시험을 추가한다(`BASE_SNAPSHOT` 상수를 import해 경로를 만든다).
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_model_inputs.py tests/test_model_ops_contract.py tests/test_workflow_planning.py tests/test_operation_runner.py -k "processor or shard_names" -v` — Expected: FAIL (`ImportError: cannot import name 'PROCESSOR_FILES'`)
- [ ] **Step 3: 구현**
`model_artifacts.py` — `TOKENIZER_FILES` 아래:
```python
# Processor files of composite (vision/audio + text) models. LLaMA-Factory's multimodal plugin loads them even for
# text-only data. They travel beside the tokenizer but are not part of tokenizer_fingerprint, so existing roots and
# artifacts keep their fingerprints.
PROCESSOR_FILES = frozenset({"preprocessor_config.json", "video_preprocessor_config.json", "processor_config.json",
                             "chat_template.json"})
```
`is_tokenizer_file` 옆:
```python
def is_processor_file(relative: str) -> bool:
    """A processor file of the base snapshot: copied byte-for-byte into derived models, never fingerprinted."""
    return relative in PROCESSOR_FILES
```

각 목록에 같은 판정을 더한다(조건식 안에 `or is_processor_file(...)`; import는 각 모듈의 `model_artifacts` import에 더한다):
- `operation_runner.py:74-75`:
  ```python
  HF_ALLOW_PATTERNS = ("config.json", "generation_config.json", *sorted(TOKENIZER_FILES), *sorted(PROCESSOR_FILES),
                       "chat_templates/*", "model.safetensors", "model.safetensors.index.json", "model-*.safetensors",
                       "model.safetensors-*.safetensors")   # Qwen3.5 names its shards model.safetensors-0000N-of-0000M
  ```
- `model_ops._copy_snapshot_files`: `if is_tokenizer_file(relative) or is_processor_file(relative) or config and relative == "config.json":`
- `operation_dispatch._write_base_snapshot`: `relative == "config.json" or is_tokenizer_file(relative) or is_processor_file(relative)`
- `operation_outputs._base_snapshot`(173-175): 허용 조건 `name == "config.json" or is_tokenizer_file(name) or is_processor_file(name)`
- `operation_outputs._train_output`(216-218): `if is_tokenizer_file(relative) or is_processor_file(relative) or adapter and relative == "config.json":`
- `operation_outputs._model_op_output`(259-263): `... or is_tokenizer_file(name) or is_processor_file(name) or name == "config.json"`
- `workflow_planning._selected_files`(906): `if name != "config.json" and not is_tokenizer_file(name) and not is_processor_file(name): continue`
  (독스트링 "config.json plus every tokenizer and processor file the revision lists")
- `simulated_dispatch._snapshot_files`: `relative == "config.json" or is_tokenizer_file(relative) or is_processor_file(relative)`
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_model_inputs.py tests/test_model_ops_contract.py tests/test_workflow_planning.py tests/test_operation_runner.py tests/test_simulated_dispatch.py tests/test_synthetic_chains.py -q` — Expected: PASS (`test_model_root_snapshot_downloads_only_config_and_tokenizer_files`의 고정 파일 목록은 processor 파일이 없는 고정물이라 그대로 통과한다)
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/model_artifacts.py src/tuning_factory/operation_runner.py src/tuning_factory/model_ops.py src/tuning_factory/operation_dispatch.py src/tuning_factory/operation_outputs.py src/tuning_factory/workflow_planning.py src/tuning_factory/simulated_dispatch.py tests/test_model_inputs.py tests/test_model_ops_contract.py tests/test_workflow_planning.py tests/test_operation_runner.py
git commit -m "fix: fetch and carry processor files and Qwen3.5 shard names for composite models"
```

---

### Task 7: 구조 정책과 허브 프로필의 크기 사실

**Files:**
- Modify: `src/tuning_factory/model_hub.py:12-30`(정책), `:39-64`(`ModelProfile`), `:67-74`(`imported_model`), `:163-183`(`inspect`)
- Test: `tests/test_model_hub.py`
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_model_hub.py`
`hub()`에 `info=None` 인자를 더하고 API 응답을 `{'sha': REVISION, 'gated': False, **(info or {})}`로 바꾼다.
`test_gemma4_is_discoverable_with_text_training_scope`의 기대값을 `['pt', 'sft', 'dpo', 'kto']`로 바꾼다. 추가:
```python
@pytest.mark.parametrize('per_layer,experts,template,moe', [(256, None, 'gemma4n', False), (0, 128, 'gemma4', True)])
def test_hub_profile_records_sizes_and_the_gemma4_template(per_layer, experts, template, moe):
    service, _ = hub('gemma4', {'tie_word_embeddings': True, 'text_config': {
        'max_position_embeddings': 131072, 'num_hidden_layers': 35, 'hidden_size': 1536,
        'hidden_size_per_layer_input': per_layer, 'num_experts': experts, 'dtype': 'bfloat16'}},
        info={'safetensors': {'total': 5123178051}})
    profile = service.inspect('google/gemma-4-E2B-it')['profile']
    assert profile['template'] == template
    assert (profile['parameters_total'], profile['hidden_size'], profile['hidden_layers'], profile['moe'],
            profile['tie_word_embeddings']) == (5123178051, 1536, 35, moe, True)
    model = imported_model(profile)
    assert (model['parameters_total'], model['hidden_size'], model['num_layers'], model['parameters_b']) == (
        5123178051, 1536, 35, 5.12)
    assert model['supported_stages'] == ['pt', 'sft', 'dpo', 'kto'] and 'oft' not in model['methods']

def test_profiles_without_size_facts_keep_their_exact_bytes():
    old = {'repo_id': 'Qwen/Qwen3.5-0.8B', 'revision': REVISION, 'config_sha256': 'c' * 64,
           'architecture': 'qwen3_5', 'template': 'qwen3_5_nothink', 'max_length': 32768,
           'context_length': 262144, 'hidden_layers': 24, 'recommended_precision': 'bfloat16', 'gated': False}
    profile = ModelProfile.model_validate(old)
    assert profile.model_dump() == old and profile.model_dump_json() == json.dumps(old, separators=(',', ':'))
    assert 'parameters_total' not in imported_model(profile)
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_model_hub.py -v` — Expected: FAIL (`KeyError: 'parameters_total'`, 단계 목록 불일치)
- [ ] **Step 3: 구현** — `model_hub.py`
```python
# Vision(/audio) + text models trained on text only: LLaMA-Factory loads them with AutoModelForImageTextToText and the
# app keeps their vision/audio towers and projectors frozen (training.py, target_inspection.py).
COMPOSITE_ARCHITECTURES = frozenset({"qwen3_5", "qwen3_5_moe", "gemma4"})
# Reward modeling waits for GPU proof of the value head; the PPO runtime (transformers 4.52.4) lacks these models.
COMPOSITE_STAGES = [x for x in ALL_STAGES if x not in {"rm", "ppo"}]
COMPOSITE_METHODS = ["lora", "qlora", "full", "freeze"]
```
`ARCHITECTURES`의 `qwen3_5`·`qwen3_5_moe`·`gemma4` 항목을 `(템플릿, COMPOSITE_STAGES, COMPOSITE_METHODS)`로 바꾼다
(`gemma3`·`gemma4_text`는 그대로).

`ModelProfile`: `gated` 뒤에 필드를 더하고 직렬화에서 빈 값을 뺀다(`model_serializer`를 pydantic에서 import):
```python
    parameters_total: int | None = Field(default=None, ge=1)   # safetensors total: vision/audio included
    hidden_size: int | None = Field(default=None, ge=1, le=1_000_000)
    moe: bool | None = None
    tie_word_embeddings: bool | None = None

    @model_serializer(mode="wrap")
    def _omit_absent_size_facts(self, handler):
        # Profiles registered before these facts keep their exact bytes and digests (model-profiles/<digest>.json,
        # graph plans): absent facts are omitted instead of dumped as null.
        data = handler(self)
        for key in ("parameters_total", "hidden_size", "moe", "tie_word_embeddings"):
            if data.get(key) is None:
                data.pop(key, None)
        return data
```
`reviewed_architecture`에 `if self.architecture == "gemma4": templates.add("gemma4n")`.

`imported_model`: 반환 dict에 크기 사실을 더한다.
```python
    sizes = {}
    if profile.parameters_total and profile.hidden_size and profile.hidden_layers:
        sizes = {"parameters_total": profile.parameters_total, "parameters_b": round(profile.parameters_total / 1e9, 2),
                 "hidden_size": profile.hidden_size, "num_layers": profile.hidden_layers}
    sizes.update({key: value for key in ("moe", "tie_word_embeddings")
                  if (value := getattr(profile, key)) is not None})
    return {..., **sizes}
```

`ModelHub.inspect`: `text = ...` 다음에 사실을 읽고 프로필에 넣는다.
```python
        total = (info.get("safetensors") or {}).get("total")
        facts = {"parameters_total": total if isinstance(total, int) and not isinstance(total, bool) and total > 0
                 else None,
                 "hidden_size": text.get("hidden_size"), "moe": bool(text.get("num_experts")),
                 "tie_word_embeddings": bool(metadata.get("tie_word_embeddings",
                                                          text.get("tie_word_embeddings", True)))}
```
- `template` 결정에 `if architecture == "gemma4" and (text.get("hidden_size_per_layer_input") or 0) > 0: template = "gemma4n"`
  (Gemma 4 E2B·E4B는 256, 26B-A4B·31B는 0).
- `ModelProfile(..., **facts)`. `facts`는 응답의 `result`에도 넣는다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_model_hub.py tests/test_model_inputs.py tests/test_graph_planner.py tests/test_desktop.py -q` — Expected: PASS (`test_model_inputs`의 고정 다이제스트 포함)
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/model_hub.py tests/test_model_hub.py
git commit -m "feat: record hub model sizes and open text stages for Qwen3.5 and Gemma 4"
```

---

### Task 8: 모델 목록 — Qwen3.5·Gemma 4 19개 항목

값은 2026-10-30 각 저장소의 API(`safetensors.total`), `config.json`(`text_config`), safetensors 헤더 합(`model.language_model.*` +
`lm_head.*` = 텍스트 매개변수)에서 읽었다. Qwen3.5-27B-Base는 확인되지 않아 넣지 않는다.

**Files:**
- Modify: `src/tuning_factory/capabilities.py:18-49`, `src/tuning_factory/desktop.py:263-264`(목록 병합 순서), `:317-327`(`model_revision`)
- Test: `tests/test_training_capabilities.py`, `tests/test_model_hub.py:105-107`
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_training_capabilities.py` 끝
```python
def test_catalog_lists_qwen35_and_gemma4_with_sizes_and_text_scope():
    from tuning_factory.capabilities import model_catalog
    models = {model["id"]: model for model in model_catalog()}
    composite = [m for m in models.values() if m["architecture"] in {"qwen3_5", "qwen3_5_moe", "gemma4"}]
    assert len(composite) == 19 and "Qwen/Qwen3.5-27B-Base" not in models
    assert all(not m["gated"] and not m["gpu_verified"] and m["verified_capabilities"] == []
               and 0 < m["text_parameters"] < m["parameters_total"] and m["max_length"] == 32768
               and m["supported_stages"] == ["pt", "sft", "dpo", "kto"] and "oft" not in m["methods"]
               for m in composite)
    small = models["Qwen/Qwen3.5-0.8B"]
    assert (small["template"], small["parameters_total"], small["text_parameters"], small["hidden_size"],
            small["num_layers"], small["tie_word_embeddings"], small["moe"]) == (
        "qwen3_5_nothink", 873_438_784, 752_393_024, 1024, 24, True, False)
    assert models["Qwen/Qwen3.5-35B-A3B-Base"]["architecture"] == "qwen3_5_moe" and models["Qwen/Qwen3.5-35B-A3B"]["moe"]
    assert not models["Qwen/Qwen3.5-9B"]["tie_word_embeddings"]
    assert {models["google/gemma-4-" + n]["template"] for n in ("E2B", "E2B-it", "E4B", "E4B-it")} == {"gemma4n"}
    assert {models["google/gemma-4-" + n]["template"] for n in ("26B-A4B", "26B-A4B-it", "31B", "31B-it")} == {"gemma4"}
    assert models["google/gemma-4-26B-A4B-it"]["moe"] and models["google/gemma-4-31B"]["parameters_b"] == 31.27
```

`tests/test_model_hub.py:106-107`(`test_import_registration_persists_only_checked_metadata`): 기본 목록 항목이 허브 등록보다 우선한다.
```python
    entry = next(item for item in loaded if item['id'] == 'Qwen/Qwen3.5-0.8B')
    assert entry.get('source') != 'huggingface' and entry['template'] == 'qwen3_5_nothink'
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_training_capabilities.py tests/test_model_hub.py -k "qwen35_and_gemma4 or registration" -v` — Expected: FAIL (`assert 0 == 19`)
- [ ] **Step 3: 구현** — `capabilities.py`
`from .model_hub import ARCHITECTURES, ModelProfile, imported_model`. 모듈 상단(STAGES 아래):
```python
# Qwen3.5 and Gemma 4 (vision/audio + text, trained on text only). Per row: repositories, architecture, template,
# safetensors total (vision/audio included), language model + lm_head parameters (from the safetensors headers),
# text_config hidden_size and num_hidden_layers, tied embeddings, MoE. Read 2026-10-30.
COMPOSITE_MODELS = [
    (("Qwen/Qwen3.5-0.8B", "Qwen/Qwen3.5-0.8B-Base"), "qwen3_5", "qwen3_5_nothink",
     873_438_784, 752_393_024, 1024, 24, True, False),
    (("Qwen/Qwen3.5-2B", "Qwen/Qwen3.5-2B-Base"), "qwen3_5", "qwen3_5_nothink",
     2_274_069_824, 1_881_825_088, 2048, 24, True, False),
    (("Qwen/Qwen3.5-4B", "Qwen/Qwen3.5-4B-Base"), "qwen3_5", "qwen3_5_nothink",
     4_659_865_088, 4_205_751_296, 2560, 32, True, False),
    (("Qwen/Qwen3.5-9B", "Qwen/Qwen3.5-9B-Base"), "qwen3_5", "qwen3_5_nothink",
     9_653_104_368, 8_953_803_264, 4096, 32, False, False),
    (("Qwen/Qwen3.5-27B",), "qwen3_5", "qwen3_5_nothink", 27_781_427_952, 26_895_998_464, 5120, 64, False, False),
    (("Qwen/Qwen3.5-35B-A3B", "Qwen/Qwen3.5-35B-A3B-Base"), "qwen3_5_moe", "qwen3_5_nothink",
     35_951_822_704, 34_660_610_688, 2048, 40, False, True),
    (("google/gemma-4-E2B-it", "google/gemma-4-E2B"), "gemma4", "gemma4n",
     5_123_178_051, 4_647_449_891, 1536, 35, True, False),
    (("google/gemma-4-E4B-it", "google/gemma-4-E4B"), "gemma4", "gemma4n",
     7_996_156_490, 7_518_069_034, 2560, 42, True, False),
    (("google/gemma-4-26B-A4B-it", "google/gemma-4-26B-A4B"), "gemma4", "gemma4",
     25_805_936_206, 25_233_141_790, 2816, 30, True, True),
    (("google/gemma-4-31B-it", "google/gemma-4-31B"), "gemma4", "gemma4",
     31_273_088_876, 30_697_345_340, 5376, 60, True, False),
]
```
`model_catalog()`의 `return models` 앞:
```python
    for ids, architecture, template, total, text, hidden, layers, tied, moe in COMPOSITE_MODELS:
        _, stages, methods = ARCHITECTURES[architecture]
        for model_id in ids:
            models.append({"id": model_id, "template": template, "architecture": architecture, "max_length": 32768,
                           "parameters_b": round(total / 1e9, 2), "parameters_total": total, "text_parameters": text,
                           "hidden_size": hidden, "num_layers": layers, "tie_word_embeddings": tied, "moe": moe,
                           "gated": False, "gpu_verified": False, "verified_capabilities": [],
                           "verification_scope": "text data only; vision/audio frozen; GPU verification pending",
                           "supported_stages": list(stages), "methods": list(methods)})
```

`desktop.py`:
- 263-264행 순서를 바꾼다: `models = {model["id"]: model for model in registered_models(self.root)}` 다음
  `models.update({model["id"]: model for model in model_catalog()})` (검토된 목록 항목이 같은 ID의 허브 등록보다 우선).
- `model_revision`의 기본 목록 경로(317-327행): `text = metadata.get("text_config") or metadata`를 두고
  `max_position_embeddings`·`num_hidden_layers`를 `text`에서 읽는다(복합 config는 최상위에 없다).
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_training_capabilities.py tests/test_model_hub.py tests/test_desktop.py tests/test_desktop_recipe_defaults.py tests/test_model_inputs.py -q` — Expected: PASS. 화면 기본값 시험(`desktop/tests/recipe-defaults.json`)이 목록 길이를 고정하면 함께 갱신한다.
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/capabilities.py src/tuning_factory/desktop.py tests/test_training_capabilities.py tests/test_model_hub.py
git commit -m "feat: list Qwen3.5 and Gemma 4 with their total and text parameter counts"
```

---

### Task 9: 메모리 추정 — 전체·텍스트 매개변수와 허브 모델

**Files:**
- Modify: `src/tuning_factory/gpu_compat.py:65-89`, `:96`; `src/tuning_factory/capabilities.py`(`model_entry`); `src/tuning_factory/desktop.py:161-167`
- Test: `tests/test_gpu_compat.py`, `tests/test_desktop.py`
- [ ] **Step 1: 실패하는 시험 작성**
`tests/test_gpu_compat.py`(기존 `training()` 재사용):
```python
QWEN35_08 = {"id": "Qwen/Qwen3.5-0.8B", "parameters_b": 0.87, "parameters_total": 873_438_784,
             "text_parameters": 752_393_024, "hidden_size": 1024, "num_layers": 24, "gpu_verified": False}
QWEN35_27 = {"id": "Qwen/Qwen3.5-27B", "parameters_total": 27_781_427_952, "text_parameters": 26_895_998_464,
             "hidden_size": 5120, "num_layers": 64}
GEMMA4_31 = {"id": "google/gemma-4-31B-it", "parameters_total": 31_273_088_876, "text_parameters": 30_697_345_340,
             "hidden_size": 5376, "num_layers": 60}

def test_composite_weights_count_every_tensor_and_training_state_only_the_text_model():
    small = gpu_compat.estimate_memory(QWEN35_08, training())
    assert 1.62 < small["weights_gib"] < 1.63 and 3.2 < small["total_gib"] < 3.4
    assert gpu_compat.estimate_memory({**QWEN35_08, "text_parameters": None}, training())["total_gib"] > small["total_gib"]
    assert 12.9 < gpu_compat.estimate_memory(QWEN35_08, training("full"))["total_gib"] < 13.2
    assert gpu_compat.estimate_memory(QWEN35_27, training())["weights_gib"] > 51.5
    qlora = gpu_compat.estimate_memory(GEMMA4_31, training("qlora"))
    assert 15.9 < qlora["weights_gib"] < 16.1 and 22.5 < qlora["total_gib"] < 23.5

def test_a_27b_bf16_lora_is_refused_on_a_48gb_gpu():
    a40 = {**BLACKWELL, "name": "NVIDIA A40", "capability": "8.6", "memory_mib": 46068}
    config = job(gpu_type="NVIDIA A40")
    findings = gpu_compat.evaluate(config, observed(gpus=(a40,), pod=None), CAPS, QWEN35_27, stage="execute")
    assert ("block", "MEMORY_WEIGHTS_EXCEED") in codes(findings)
```

`tests/test_desktop.py`(L217 근처):
```python
def test_compatibility_estimates_memory_for_a_registered_hub_model(tmp_path):
    from tuning_factory.desktop import DesktopService
    from tuning_factory.gpu_compat import make_observation
    from tuning_factory.model_hub import ModelProfile
    profile = ModelProfile(repo_id="google/gemma-4-31B-it", revision="a" * 40, config_sha256="c" * 64,
                           architecture="gemma4", template="gemma4", hidden_layers=60, hidden_size=5376,
                           parameters_total=31_273_088_876).model_dump()
    config = {**A4000_JOB, "base_model": profile["repo_id"], "model_profile": profile,
              "resource_profile": {**A4000_JOB["resource_profile"], "gpu_type": BLACKWELL["name"]}}
    findings = DesktopService(tmp_path).compatibility(config, make_observation("runpod-x", [BLACKWELL]), "execute")
    found = {(item["level"], item["code"]) for item in findings}
    assert ("block", "MEMORY_WEIGHTS_EXCEED") in found and ("warn", "MEMORY_UNKNOWN") not in found
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_gpu_compat.py tests/test_desktop.py -k "composite or 27b or registered_hub" -v` — Expected: FAIL (`estimate_memory`가 `parameters_b` 없는 항목에 `None`을 돌려줌, 허브 모델은 `MEMORY_UNKNOWN`)
- [ ] **Step 3: 구현**
`gpu_compat.estimate_memory`:
```python
    if not model or not (model.get("parameters_total") or model.get("parameters_b")) or not all(
            model.get(key) for key in ("hidden_size", "num_layers")):
        return None
    parameters = float(model.get("parameters_total") or float(model["parameters_b"]) * 1e9)   # every tensor on the GPU
    trainable = float(model.get("text_parameters") or parameters)   # vision/audio stay frozen; unknown: all of them
```
학습 상태 세 줄(`full`, `freeze`, 어댑터)의 `parameters`를 `trainable`로 바꾼다. 가중치·활성값 식은 그대로다.
독스트링에 "weights: every tensor (vision/audio and Gemma per-layer embeddings included); training state: the text model"을 더한다.
`MEMORY_UNKNOWN = "모델 크기 정보가 없어 메모리 요구량을 추정하지 못했습니다."`

`capabilities.py`:
```python
def model_entry(config):
    """The model entry of a (partial) config: its registered HF profile first, else the reviewed catalog; or None."""
    if hasattr(config, "model_dump"):
        config = config.model_dump()
    if config.get("model_profile"):
        try:
            return imported_model(ModelProfile.model_validate(config["model_profile"]))
        except ValueError:
            return None
    return next((m for m in model_catalog() if m["id"] == config.get("base_model")), None)
```
`desktop.compatibility` 166행을 `model = model_entry(config)`로 바꾼다(import 추가).
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_gpu_compat.py tests/test_desktop.py tests/test_desktop_servers.py -q` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/gpu_compat.py src/tuning_factory/capabilities.py src/tuning_factory/desktop.py tests/test_gpu_compat.py tests/test_desktop.py
git commit -m "feat: estimate composite and hub model memory from total and text parameters"
```

---

### Task 10: 학습 단계·freeze 한국어 거부

**Files:**
- Modify: `src/tuning_factory/capabilities.py:91-92`, `:120-124`
- Test: `tests/test_training_capabilities.py`
- [ ] **Step 1: 실패하는 시험 작성**
```python
QWEN35 = {"base_model": "Qwen/Qwen3.5-0.8B", "template": "qwen3_5_nothink"}

@pytest.mark.parametrize("change,message", [
    ({"training_stage": "rm"}, "값 헤드"), ({"training_stage": "ppo"}, "PPO 전용 학습 환경"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_trainable_modules": "self_attn"}}, "linear_attn"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_trainable_modules": "self_attn,mlp"}}, "linear_attn"),
    ({"training_method": "freeze", "finetuning_options": {"freeze_extra_modules": "embed_tokens,lm_head"}}, "lm_head"),
    ({"training_method": "oft"}, "oft is unavailable")])
def test_composite_models_refuse_unverified_stages_and_missing_modules(change, message):
    from tuning_factory.capabilities import compatibility
    result = compatibility({**QWEN35, **change})
    assert not result["valid"] and any(message in error for error in result["errors"])

@pytest.mark.parametrize("stage", ["pt", "sft", "dpo", "kto"])
def test_composite_models_allow_text_stages_and_existing_freeze_modules(stage):
    from tuning_factory.capabilities import compatibility
    assert compatibility({**QWEN35, "training_stage": stage})["valid"]
    assert compatibility({**QWEN35, "training_method": "freeze", "finetuning_options": {
        "freeze_trainable_modules": "mlp", "freeze_extra_modules": "embed_tokens"}})["valid"]
    assert compatibility({"base_model": "Qwen/Qwen3.5-9B", "template": "qwen3_5_nothink", "training_method": "freeze",
                          "finetuning_options": {"freeze_extra_modules": "lm_head"}})["valid"]   # untied
    assert compatibility({"base_model": "google/gemma-4-E2B-it", "template": "gemma4n", "training_method": "freeze",
                          "finetuning_options": {"freeze_trainable_modules": "self_attn"}})["valid"]
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_training_capabilities.py -k composite_models -v` — Expected: FAIL (영문 일반 거부만 있음, freeze 거부 없음)
- [ ] **Step 3: 구현** — `capabilities.py`
```python
COMPOSITE_STAGE_REFUSALS = {
    "rm": ("Qwen3.5·Gemma 4의 보상 모델(RM) 학습은 아직 GPU에서 값 헤드를 검증하지 않아 막아 두었습니다. "
           "SFT·DPO·KTO·PT를 쓰세요."),
    "ppo": "Qwen3.5·Gemma 4는 PPO 전용 학습 환경(transformers 4.52.4)이 지원하지 않습니다.",
}
FREEZE_SELF_ATTN = ("Qwen3.5는 층마다 linear_attn과 self_attn이 섞여 있어 freeze 학습 모듈로 'self_attn'을 고를 수 없습니다. "
                    "'all' 또는 'mlp'를 고르세요.")
FREEZE_TIED_LM_HEAD = ("이 모델은 입력 임베딩과 lm_head를 공유해 lm_head를 따로 학습할 수 없습니다. 추가 학습 모듈에서 "
                       "lm_head를 빼세요(embed_tokens를 고르면 공유된 가중치가 함께 학습됩니다).")
```
- 121-122행:
  ```python
  if stage not in model.get("supported_stages", STAGES):
      errors.append(COMPOSITE_STAGE_REFUSALS[stage]
                    if model["architecture"] in COMPOSITE_ARCHITECTURES and stage in COMPOSITE_STAGE_REFUSALS
                    else f"{stage} is unavailable for this architecture/runtime profile; choose a supported stage")
  ```
- 그 아래 `if model:` 블록 안:
  ```python
  if method == "freeze":
      modules = str(options.get("freeze_trainable_modules", "all")).split(",")
      if model["architecture"] in {"qwen3_5", "qwen3_5_moe"} and "self_attn" in modules:
          errors.append(FREEZE_SELF_ATTN)       # layer 0 is linear_attn: LLaMA-Factory finds no self_attn there
      if model.get("tie_word_embeddings") and "lm_head" in str(options.get("freeze_extra_modules") or "").split(","):
          errors.append(FREEZE_TIED_LM_HEAD)    # a tied lm_head is not a separate parameter
  ```
- `from .model_hub import ARCHITECTURES, COMPOSITE_ARCHITECTURES, ModelProfile, imported_model`.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_training_capabilities.py tests/test_model_hub.py tests/test_config_registry.py tests/test_recipes.py -q` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/capabilities.py tests/test_training_capabilities.py
git commit -m "feat: refuse unverified stages and missing freeze modules for Qwen3.5 and Gemma 4 in Korean"
```

---

### Task 11: 설정 변환 — 복합 구조 동결과 생각 끄기

**Files:**
- Modify: `src/tuning_factory/training.py:15`, `:152-187`(`_legacy_plan`), `:230-251`(`predict`), `:265-280`(`prediction_plan`), `:304-322`(`evaluate_reward`)
- Test: `tests/test_training_capabilities.py`; 고정 다이제스트 `tests/test_model_inputs.py:303-319`(변경 금지)
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_training_capabilities.py`(`configuration()` 재사용)
```python
FROZEN = ("freeze_vision_tower", "freeze_multi_modal_projector", "freeze_language_model")

def test_composite_training_freezes_vision_and_reasoning_templates_turn_thinking_off():
    qwen = LlamaFactoryBackend.translate(configuration(base_model="Qwen/Qwen3.5-0.8B", template="qwen3_5_nothink"))
    assert tuple(qwen[key] for key in FROZEN) == (True, True, False) and "enable_thinking" not in qwen
    gemma = LlamaFactoryBackend.translate(configuration(base_model="google/gemma-4-E2B-it", template="gemma4n"))
    assert gemma["enable_thinking"] is False and gemma["freeze_vision_tower"] is True
    legacy = LlamaFactoryBackend.translate(load_config("configs/ai/paper_extraction.yaml"))
    assert not {*FROZEN, "enable_thinking"} & set(legacy)

def test_prediction_plans_turn_thinking_off_only_for_reasoning_templates():
    def plan(template):
        benchmark = SimpleNamespace(semantic_model_kind="causal_lm", generation_mode="greedy", precision="bf16",
                                    template=template, max_new_tokens=8, cutoff_len=64, seed=1, quantization_bit=None)
        return LlamaFactoryBackend.prediction_plan(benchmark, model_path="/m", adapter_path=None, max_samples=1)
    assert all(plan(name)["enable_thinking"] is False for name in ("qwen3_5", "qwen3_6", "gemma4", "gemma4n"))
    assert "enable_thinking" not in plan("qwen") and "enable_thinking" not in plan("qwen3_5_nothink")

def test_native_prediction_of_a_reasoning_template_turns_thinking_off(tmp_path):
    config = configuration(base_model="google/gemma-4-E2B-it", template="gemma4n")
    plans = []

    class Backend(LlamaFactoryBackend):
        def run_config(self, directory, timeout, canceled, filename, log_name):
            plans.append(yaml.safe_load((directory / filename).read_text()))
            (directory / "prediction").mkdir()
            (directory / "prediction/generated_predictions.jsonl").write_text('{"predict":"a","label":"a"}\n')
    backend = Backend()
    backend.prepare(config, tmp_path, b'[{"instruction":"a","output":"b"}]')
    backend.predict(config, tmp_path, "abc", 1)
    assert plans[-1]["enable_thinking"] is False and "freeze_vision_tower" not in plans[-1]
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_training_capabilities.py -k "thinking or freezes_vision" -v` — Expected: FAIL (`KeyError: 'freeze_vision_tower'`)
- [ ] **Step 3: 구현** — `training.py`
`from .capabilities import STAGES, compatibility, model_entry`, `from .model_hub import COMPOSITE_ARCHITECTURES`. 모듈 상수:
```python
# LLaMA-Factory 0.9.5 reasoning templates default to enable_thinking=True, which trains an empty thought block on
# answers that have none; the app trains and evaluates plain text answers.
REASONING_TEMPLATES = frozenset({"qwen3_5", "qwen3_6", "gemma4", "gemma4n"})

def _plain_answers(plan, template):
    if template in REASONING_TEMPLATES:
        plan["enable_thinking"] = False
    return plan
```
- `_legacy_plan`의 `return plan` 앞:
  ```python
  model = model_entry(config)
  if model is not None and model["architecture"] in COMPOSITE_ARCHITECTURES:
      # Text data only: LLaMA-Factory's composite defaults, written out so every plan states them.
      plan.update(freeze_vision_tower=True, freeze_multi_modal_projector=True, freeze_language_model=False)
  return _plain_answers(plan, config.template)
  ```
- `predict`: `(directory / "predict.yaml").write_text(...)` 직전에 `_plain_answers(plan, config.template)`.
- `prediction_plan`: `return _plain_answers(plan, benchmark.template)`.
- `evaluate_reward`: yaml 쓰기 직전에 `_plain_answers(plan, config.template)`.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_training_capabilities.py tests/test_model_inputs.py tests/test_training.py tests/test_prediction.py tests/test_graph_planner.py -q` — Expected: PASS. 특히 `test_translate_legacy_output_is_byte_identical`·`test_v1_manifest_training_config_hash_unchanged`가 그대로 통과해야 한다.
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/training.py tests/test_training_capabilities.py
git commit -m "feat: freeze composite vision towers and turn reasoning templates' thinking off for text data"
```

---

### Task 12: vocab 검사 — `text_config`

**Files:**
- Modify: `src/tuning_factory/operation_outputs.py:227-229`
- Test: `tests/test_operation_runner.py`
- [ ] **Step 1: 실패하는 시험 작성**
```python
def test_vocabulary_size_is_read_from_text_config_for_composite_configs():
    from tuning_factory.operation_outputs import _vocab_size
    assert _vocab_size({"text_config": {"vocab_size": 248320}}) == 248320
    assert _vocab_size({"vocab_size": 151936}) == 151936
    assert _vocab_size({"text_config": {"vocab_size": 1}, "vocab_size": 2}) == 1
    assert _vocab_size({"text_config": {}}) is None
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_operation_runner.py -k vocabulary_size -v` — Expected: FAIL (`ImportError`)
- [ ] **Step 3: 구현**
```python
def _vocab_size(config):
    """A model config's vocabulary size; composite (vision/audio + text) configs keep it only in text_config."""
    text = config.get("text_config")
    if isinstance(text, Mapping) and "vocab_size" in text:
        return text["vocab_size"]
    return config.get("vocab_size")
```
228행: `if not isinstance(trained, Mapping) or _vocab_size(trained) != _vocab_size(original):`
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_operation_runner.py tests/test_model_ops_contract.py -q` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/operation_outputs.py tests/test_operation_runner.py
git commit -m "fix: compare composite model vocabularies through text_config"
```

---

### Task 13: 복합 구조 대상 규칙(torch 없이) — 헤더 증거·검증·계획

규칙: 복합 모델의 LoRA 대상은 `model.language_model.` 아래의 Linear뿐이다. LLaMA-Factory는 비전·오디오(동결)와
투영(`model.visual.merger`, `model.embed_vision`/`embed_audio`)을 빼고, 어댑터에 **전체 이름**을 기록한다
(`patch_target_modules`). Qwen3.5의 `mtp.*`는 transformers가 불러오지 않는다. 텍스트 모델은 지금처럼 잎 이름이다.

**Files:**
- Modify: `src/tuning_factory/target_inspection.py`(상수·순수 함수, `verify_inspected_targets:41-44`)
- Modify: `src/tuning_factory/hf_targets.py:75`, `:263-277`; `src/tuning_factory/graph_planner.py:433-434`, `:461-462`;
  `src/tuning_factory/simulated_dispatch.py:418-431`
- Test: `tests/test_hf_targets.py`, `tests/test_model_ops_contract.py`
- [ ] **Step 1: 실패하는 시험 작성**
`tests/test_hf_targets.py` 끝:
```python
def composite_tensors(extra=()):
    from tuning_factory.model_artifacts import TensorMetadata
    shapes = {"model.language_model.embed_tokens.weight": (64, 32),
              "model.language_model.layers.0.linear_attn.in_proj_qkv.weight": (96, 32),
              "model.language_model.layers.0.linear_attn.conv1d.weight": (96, 1, 4),
              "model.language_model.layers.0.linear_attn.out_proj.weight": (32, 32),
              "model.language_model.layers.0.input_layernorm.weight": (32,),
              "model.language_model.layers.3.self_attn.q_proj.weight": (64, 32),
              "model.language_model.norm.weight": (32,),
              "model.visual.blocks.0.attn.qkv.weight": (48, 16),
              "model.visual.patch_embed.proj.weight": (16, 3, 2, 16, 16),
              "model.visual.merger.linear_fc1.weight": (64, 64), "mtp.fc.weight": (32, 64), **dict(extra)}
    return tuple(TensorMetadata(file="model.safetensors", name=name, shape=shape, dtype="BF16")
                 for name, shape in shapes.items())

def test_composite_header_evidence_keeps_only_language_model_linears():
    ht = targets()
    from tuning_factory.graph_planner import ModelTargetEvidence
    from tuning_factory.model_artifacts import HFRootRef, ModelArtifactRef, ModelDescriptor
    from tuning_factory.target_inspection import verify_inspected_targets
    root = HFRootRef(repo_id="test/tiny-qwen3_5", revision="a" * 40, config_sha256="c" * 64,
                     tokenizer_fingerprint="d" * 64)
    evidence = ht.target_evidence(root, composite_tensors())
    assert [module.name for module in evidence.modules] == [
        "model.language_model.layers.0.linear_attn.in_proj_qkv", "model.language_model.layers.0.linear_attn.out_proj",
        "model.language_model.layers.3.self_attn.q_proj"]
    descriptor = ModelDescriptor(producer_job_id="i", producer_attempt_id="i", producer_manifest_sha256="b" * 64,
                                 storage_kind="full_model", semantic_kind="causal_lm", has_value_head=False,
                                 root=root, effective_base=root, config_sha256=root.config_sha256,
                                 tokenizer_fingerprint=root.tokenizer_fingerprint, tensors=composite_tensors())
    ref = ModelArtifactRef(artifact_id=digest(canonical(descriptor.model_dump(mode="json"))), inventory_sha256="0" * 64)
    moved = ModelTargetEvidence(base=ref, modules=evidence.modules)
    assert verify_inspected_targets(moved, descriptor, ref).modules == evidence.modules
    for extra in ({"model.language_model.layers.1.mlp.experts.gate_up_proj": (4, 64, 32)},
                  {"model.language_model.layers.1.self_attn.k_proj.weight": (4, 32, 2)}):
        with pytest.raises(ht._Refused):
            ht.target_evidence(root, composite_tensors(extra))
```
(`digest`·`canonical`이 import되어 있지 않으면 `tuning_factory.registry`에서 import한다.) 같은 파일의
`test_architectures_without_plain_linear_weights_are_refused_not_guessed`는 바꾸지 않는다(텍스트 구조의 `conv_kernel`·`gemma3`는 계속 거부).

`tests/test_model_ops_contract.py` 끝:
```python
def test_resolved_lora_targets_follow_llamafactory_for_text_and_composite_models():
    from tuning_factory.target_inspection import resolved_lora_targets, target_leaves, unloaded_prefixes
    text = ["model.layers.0.self_attn.q_proj", "model.layers.0.self_attn.v_proj", "model.layers.0.mlp.up_proj"]
    assert resolved_lora_targets("all", text) == ("q_proj", "up_proj", "v_proj")
    assert resolved_lora_targets("q_proj,v_proj", text) == ("q_proj", "v_proj")
    composite = ["model.language_model.layers.0.linear_attn.in_proj_qkv",
                 "model.language_model.layers.3.self_attn.q_proj", "model.language_model.layers.3.self_attn.v_proj"]
    assert resolved_lora_targets("q_proj,v_proj", composite) == tuple(composite[1:])
    assert resolved_lora_targets("all", composite) == tuple(sorted(composite))
    assert target_leaves(composite) == ("in_proj_qkv", "q_proj", "v_proj") == target_leaves(("in_proj_qkv", "q_proj", "v_proj"))
    assert unloaded_prefixes({"model_type": "qwen3_5"}) == ("mtp.",) and unloaded_prefixes({"model_type": "qwen2"}) == ()
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_hf_targets.py tests/test_model_ops_contract.py -k "composite or resolved_lora" -v` — Expected: FAIL (`architecture_unsupported` 거부, `ImportError`)
- [ ] **Step 3: 구현**
`target_inspection.py` — `_not_a_target` 위:
```python
# Composite (vision/audio + text) model types the app trains on text only. LLaMA-Factory 0.9.5 loads them with
# AutoModelForImageTextToText, keeps the vision/audio towers and projectors frozen and records the adapted modules by
# their full names; every one of them lies under the language model.
COMPOSITE_MODEL_TYPES = frozenset({"qwen3_5", "qwen3_5_moe", "gemma4"})
LANGUAGE_MODEL_PREFIX = "model.language_model."
# Checkpoint tensors transformers 5.6.0 never loads for these model types (Qwen3.5's multi-token-prediction head).
UNLOADED_PREFIXES = {"qwen3_5": ("mtp.",), "qwen3_5_moe": ("mtp.",)}

def unloaded_prefixes(config) -> tuple:
    return UNLOADED_PREFIXES.get(config.get("model_type"), ())

def is_composite_inventory(names) -> bool:
    return any(name.startswith(LANGUAGE_MODEL_PREFIX) for name in names)

def target_leaves(targets) -> tuple:
    """The leaf names of adapter targets: how planned and recorded target sets are compared."""
    return tuple(sorted({target.rsplit(".", 1)[-1] for target in targets}))

def resolved_lora_targets(lora_target: str, module_names) -> tuple:
    """The ``target_modules`` LLaMA-Factory 0.9.5 writes for ``lora_target`` over a base's inspected Linear modules:
    leaf names for text models, full language-model module names for composite models (``patch_target_modules``)."""
    names = tuple(module_names)
    wanted = set(target_leaves(names)) if lora_target == "all" else set(lora_target.split(","))
    if not is_composite_inventory(names):
        return tuple(sorted(wanted))
    return tuple(sorted(name for name in names if name.rsplit(".", 1)[-1] in wanted))
```
`verify_inspected_targets` 41-42행 `expected` 다음에:
```python
    if is_composite_inventory(weights):   # vision/audio towers, projectors and mtp.* are never adapted
        expected = {name for name in expected if name.startswith(LANGUAGE_MODEL_PREFIX)}
```

`hf_targets.py`:
- `HEADER_TARGET_ARCHITECTURES`에 `"qwen3_5"`, `"gemma4"`를 더하고 주석에 "composite models: only their language model (vision,
  audio, projectors and mtp.* are frozen or unloaded); hybrid linear attention keeps its 3-D conv1d kernel, which is never a
  target. Their MoE variants stay refused (fused 3-D experts)."를 적는다. `qwen3_5_moe`는 넣지 않는다.
- `target_evidence` 반복을 다음으로 바꾼다(`from .target_inspection import LANGUAGE_MODEL_PREFIX, _not_a_target, is_composite_inventory`):
  ```python
  composite = is_composite_inventory(tensor.name for tensor in tensors)
  for tensor in tensors:
      if composite and not tensor.name.startswith((LANGUAGE_MODEL_PREFIX, "lm_head.")):
          continue
      kernel = composite and len(tensor.shape) == 3 and tensor.name.endswith(".linear_attn.conv1d.weight")
      if (not tensor.name.startswith(CAUSAL_LM_PREFIXES) or (len(tensor.shape) >= 3 and not kernel)
              or "experts" in tensor.name.split(".") or tensor.name.endswith(QUANTIZED_SUFFIXES)):
          raise unsupported
  ```
  (아래 모듈 추가 조건은 그대로.)

`graph_planner.py` — `from .target_inspection import target_leaves`. 434행 `tuple(sorted(descriptor.adapter.target_modules))`와
461-462행 같은 식을 `target_leaves(descriptor.adapter.target_modules)`로 바꾼다(텍스트 모델은 이미 잎 이름이라 결과가 같다).

`simulated_dispatch._adapter_modules`: 새 어댑터 경로를 다음으로 바꾼다.
```python
        elif evidence is not None:   # LLaMA-Factory: leaf names, or full language-model names for composite models
            targets = resolved_lora_targets(config.hyperparameters.lora_target,
                                            [item.name for item in evidence.modules])
        elif config.hyperparameters.lora_target == "all":
            raise ValueError(TARGETS_REQUIRED.format(kind=operation.kind))
        else:
            targets = tuple(sorted(config.hyperparameters.lora_target.split(",")))
```
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_hf_targets.py tests/test_model_ops_contract.py tests/test_graph_planner.py tests/test_simulated_dispatch.py tests/test_synthetic_chains.py tests/test_merge_fallback.py tests/test_workflow_planning.py -q` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add src/tuning_factory/target_inspection.py src/tuning_factory/hf_targets.py src/tuning_factory/graph_planner.py src/tuning_factory/simulated_dispatch.py tests/test_hf_targets.py tests/test_model_ops_contract.py
git commit -m "feat: derive composite LoRA targets from the language model only"
```

---

### Task 14: 대상 검사·병합·확정 런타임과 CPU 수치 검증

**Files:**
- Modify: `src/tuning_factory/target_inspection.py:59-82`, `src/tuning_factory/model_ops.py:465-468`, `:823-826`
- Modify: `scripts/verify_model_ops_cpu.py` (`load`, `descriptor`, `make_adapter`, 새 단계 `check_composite`, `run`)
- Test: `tests/test_model_ops_contract.py`; CPU 수치 검증(`.factory/model-ops-cpu-venv`)
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_model_ops_contract.py`
```python
class FakeLinear:
    def __init__(self, inputs, outputs):
        self.in_features, self.out_features = inputs, outputs

def test_composite_inspection_uses_the_image_text_class_and_keeps_the_language_model(tmp_path, monkeypatch):
    from contextlib import nullcontext
    from types import SimpleNamespace as NS

    from tuning_factory import runtime_profiles, target_inspection
    modules = [("model.visual.blocks.0.attn.qkv", FakeLinear(16, 48)),
               ("model.visual.merger.linear_fc1", FakeLinear(64, 64)),
               ("model.language_model.layers.0.linear_attn.in_proj_qkv", FakeLinear(32, 96)),
               ("model.language_model.layers.3.self_attn.q_proj", FakeLinear(32, 64)), ("lm_head", FakeLinear(32, 64))]
    model = NS(named_modules=lambda: iter(modules))
    transformers = NS(AutoConfig=NS(from_pretrained=lambda *a, **k: NS(model_type="qwen3_5")),
                      AutoModelForCausalLM=NS(from_config=lambda config: pytest.fail("text-only class")),
                      AutoModelForImageTextToText=NS(from_config=lambda config: model))
    monkeypatch.setattr(runtime_profiles, "model_ops_runtime",
                        lambda: (NS(device=lambda name: nullcontext()), None, None, transformers))
    assert [item.name for item in target_inspection.inspect_linear_modules(tmp_path)] == [
        "model.language_model.layers.0.linear_attn.in_proj_qkv", "model.language_model.layers.3.self_attn.q_proj"]

def test_composite_models_load_through_the_image_text_class(tmp_path):
    from types import SimpleNamespace as NS

    from tuning_factory import model_ops
    (tmp_path / "config.json").write_text('{"model_type": "gemma4"}')
    loaded = NS(to=lambda device: NS(eval=lambda: "composite"))
    transformers = NS(AutoModelForCausalLM=NS(from_pretrained=lambda *a, **k: pytest.fail("text-only class")),
                      AutoModelForImageTextToText=NS(from_pretrained=lambda *a, **k: loaded))
    assert model_ops._load_fp32(NS(float32="f32"), transformers, tmp_path, "cpu") == "composite"
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_model_ops_contract.py -k "image_text_class" -v` — Expected: FAIL (`text-only class`)
- [ ] **Step 3: 구현**
`target_inspection.inspect_linear_modules`:
```python
    composite = config.model_type in COMPOSITE_MODEL_TYPES
    loader = transformers.AutoModelForImageTextToText if composite else transformers.AutoModelForCausalLM
    with torch.device("meta"):
        model = loader.from_config(config)
```
`modules` 조건에 `and (not composite or name.startswith(LANGUAGE_MODEL_PREFIX))`를 더한다(Gemma 4 비전의
`Gemma4ClippableLinear`는 `in_features`가 없으므로 이 조건을 `"Linear" in ...` 검사보다 먼저 평가한다).
독스트링에 "composite models: AutoModelForImageTextToText, language-model modules only (LLaMA-Factory's rule)"을 더한다.

`model_ops.py` — `from .target_inspection import COMPOSITE_MODEL_TYPES, unloaded_prefixes`(기존 import 줄에 더함):
```python
def _load_fp32(torch, transformers, path: Path, device):
    composite = _read_json(path / "config.json").get("model_type") in COMPOSITE_MODEL_TYPES
    loader = transformers.AutoModelForImageTextToText if composite else transformers.AutoModelForCausalLM
    model = loader.from_pretrained(str(path), dtype=torch.float32, local_files_only=True, use_safetensors=True)
    return model.to(device).eval()
```
`_verify_materialized` 824행:
```python
    skipped = unloaded_prefixes(_read_json(base_path / "config.json"))   # never loaded, so never saved
    base = {name: tensor for name, tensor in _load_tensors(base_path, adapter=False).items()
            if not name.startswith(skipped)}
    result = _load_tensors(payload, adapter=False)
```

`scripts/verify_model_ops_cpu.py`:
- 상수(QWEN2 아래). 2026-10-30에 같은 모양(vocab 64)으로 CPU forward를 확인했다. vocab 32는 작은 토크나이저와 맞춘다.
  ```python
  QWEN3_5 = dict(text_config=dict(num_hidden_layers=4, hidden_size=32, intermediate_size=64, num_attention_heads=2,
                                  num_key_value_heads=1, head_dim=16, vocab_size=32, linear_num_key_heads=2,
                                  linear_num_value_heads=2, linear_key_head_dim=8, linear_value_head_dim=8),
                 vision_config=dict(depth=1, hidden_size=16, intermediate_size=32, num_heads=2, out_hidden_size=32))
  GEMMA4 = dict(text_config=dict(num_hidden_layers=2, hidden_size=32, intermediate_size=64, num_attention_heads=2,
                                 num_key_value_heads=1, head_dim=16, vocab_size=32),
                vision_config=dict(num_hidden_layers=1, hidden_size=16, intermediate_size=32, num_attention_heads=2,
                                   num_key_value_heads=2, head_dim=8), audio_config=None)
  ```
- `load(path)`: `config.json`의 `model_type`이 `target_inspection.COMPOSITE_MODEL_TYPES`이면 `AutoModelForImageTextToText`.
- `descriptor(..., root=None)`: `root = root or self.root`를 `root=`·`config`·`tokenizer_fingerprint`에 쓴다.
- `make_adapter(..., target_modules=TARGETS, root=None)`: `LoraConfig(target_modules=list(target_modules), ...)`,
  `write_adapter_config(..., (root or self.root).repo_id)`, `descriptor(..., root=root)`.
- 새 단계 `check_composite`(`run()`의 단계 목록 끝, `check_execute_operation` 뒤). 두 구조(`"qwen3_5"`, `"gemma4"`)마다:
  1. `base = self.work / f"composite-{kind}"`에 `self.base_dir`의 토크나이저 파일을 복사하고, `torch.manual_seed(0)`로
     `AutoModelForImageTextToText.from_config(<Config>(**QWEN3_5 | GEMMA4), dtype=torch.float32)`를 저장한다.
     `qwen3_5`는 `model.safetensors`에 `mtp.fc.weight`(32×64) 하나를 더해 다시 쓴다(`load_file`/`save_file(..., metadata={"format": "pt"})`).
  2. `HFRootRef(repo_id=f"factory-synthetic/tiny-{kind}", revision=sha1(...), config_sha256=..., tokenizer_fingerprint=...)`,
     `LocalModel`, `store.freeze(base, descriptor(..., root=root))`.
  3. 검사 `composite_{kind}_targets`: `inspect_target_modules(base, root)`의 모든 이름이 `model.language_model.`로 시작하고,
     저장 결과에서 `verify_inspected_targets`가 받아들이며, `hf_targets.target_evidence(root, self.headers(base)).modules`와 같다.
  4. 대상 = 증거 모듈 중 잎 이름이 `q_proj`·`v_proj`인 **전체 이름**(LLaMA-Factory가 쓰는 형태,
     `resolved_lora_targets("q_proj,v_proj", names)`). 이 대상으로 어댑터 둘(r2/α4, r3/α9)을 만든다.
  5. 검사 `composite_{kind}_merge`: `ops.merge_adapters((a1, a2), (0.25, 0.75), out, base=local, require_real=False, probe_ids=PROBE)`가
     성공하고, `lora_delta` 모듈 집합이 대상 집합과 같다.
  6. 검사 `composite_{kind}_materialize`: `ops.materialize_model(ops.StagedLoad(base=local, adapter=a1), out2, probe_ids=PROBE)`가 성공하고,
     출력 텐서 이름 = 기본 텐서 이름 − `mtp.*`, `model.visual.*`/`model.vision_tower.*` 텐서가 기본과 `torch.equal`,
     `config.json`의 `architectures`가 복합 클래스이며, `load(out2)` 로짓이 기본 + 델타를 직접 더한 모델과 허용 오차 안에서 같다.
     출력은 `store.freeze`로 동결된다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_model_ops_contract.py tests/test_operation_runner.py tests/test_model_inputs.py -q` — Expected: PASS
Run: `.factory/model-ops-cpu-venv/Scripts/python.exe -I scripts/verify_model_ops_cpu.py --receipt dist/model-ops-cpu-0.6.0.json`
Expected: 종료 코드 0, 모든 검사 `passed: true`(기존 29개 + 새 6개 = 35개), `stopped_after: null`. 결과를 PROGRESS에 적는다.
- [ ] **Step 5: 대체 결정(검증 실패 시에만)**
복합 검사를 고칠 수 없으면 Task 13의 `HEADER_TARGET_ARCHITECTURES`에서 `qwen3_5`·`gemma4`를 빼고, `workflow_planning._target_checks`에
복합 구조 기반의 병합·확정·이어 학습 노드를 한국어 `issue("composite_model_ops_unsupported", "Qwen3.5·Gemma 4 모델의 병합·확정·이어 학습은 아직 지원하지 않습니다. 학습한 어댑터는 그대로 평가·사용할 수 있습니다.", ...)`로
거부하는 시험·구현을 더한다. 실패한 검사 이름과 오류를 PROGRESS에 기록한다.
- [ ] **Step 6: Commit**
```bash
git add src/tuning_factory/target_inspection.py src/tuning_factory/model_ops.py scripts/verify_model_ops_cpu.py tests/test_model_ops_contract.py
git commit -m "feat: inspect, merge and materialize composite models through their image-text class"
```

---

### Task 15: 버전 0.6.0

**Files:**
- Modify: `desktop/package.json:3`, `desktop/package-lock.json:3,9`, `desktop/src-tauri/Cargo.toml:3`, `desktop/src-tauri/Cargo.lock:2031`, `desktop/src-tauri/tauri.conf.json:4`
- Test: `tests/test_release_version.py:10`
- [ ] **Step 1:** `RELEASE = "0.6.0"`
- [ ] **Step 2:** Run: `uv run pytest tests/test_release_version.py -v` — Expected: FAIL (`test_every_desktop_version_field_is_the_release`)
- [ ] **Step 3:** `cd desktop && npm version 0.6.0 --no-git-tag-version && cd ..`; `Cargo.toml` 3행·`Cargo.lock` 2031행·`tauri.conf.json` 4행을 `0.6.0`으로.
- [ ] **Step 4:** Run: `uv run pytest tests/test_release_version.py -v && cargo metadata --manifest-path desktop/src-tauri/Cargo.toml --locked --format-version 1 --no-deps > /dev/null` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add tests/test_release_version.py desktop/package.json desktop/package-lock.json desktop/src-tauri/Cargo.toml desktop/src-tauri/Cargo.lock desktop/src-tauri/tauri.conf.json
git commit -m "chore: version the desktop app 0.6.0"
```

---

### Task 16: 운영 문서

**Files:**
- Modify: `docs/OPERATIONS.md`(flock 전제 180행 근처, 원격 배치 192-194행), `docs/WINDOWS_APP.md`("실행 전 GPU 점검 (0.5.0)" 절 뒤), `docs/TRAINING_AND_STORAGE.md`(끝)
- [ ] **Step 1: `docs/OPERATIONS.md`** — 원격 배치 설명에 `model-cache/`를 더하고 다음 단락을 넣는다:
  "Workers run under `flock -s <runtime>/in-use.lock`, so a runtime in use is never removed. Preparing a runtime first prunes
  old app-version runtimes below `runtime/` that no worker or preparation holds and that no unfinished attempt references
  (deadline: attempt mtime + timeout + 1 h); `uv-cache/`, `python/`, `hf-cache/`, `model-cache/` and `jobs/` are never touched.
  The `TF_PRUNE` report is returned as `cleanup`. App SSH connections send keepalives every 30 s."
- [ ] **Step 2: `docs/WINDOWS_APP.md`** — 새 절 "## Qwen3.5·Gemma 4와 0.6.0 변경":
  - 목록에 Qwen3.5(0.8B·2B·4B·9B·27B·35B-A3B, Base 포함)와 Gemma 4(E2B·E4B·26B-A4B·31B, -it와 기본)가 있다. 텍스트 데이터만 학습하며
    비전·오디오는 항상 동결된다.
  - 단계: SFT·DPO·KTO·PT. RM은 GPU 검증 후(결과는 Task 19), PPO는 지원하지 않는다. freeze의 'Attention'은 Qwen3.5에서 고를 수 없고,
    임베딩을 공유하는 모델에서는 'LM head'를 따로 학습할 수 없다.
  - 메모리 추정: 가중치는 비전·오디오를 포함한 전체 매개변수, 학습 상태는 텍스트 매개변수로 계산한다. Hugging Face에서 등록한 모델도
    크기 정보가 있으면 추정한다.
  - GPU 서버의 시간당 비용이 작업 한도를 넘거나 비어 있으면 계획에서 알리고 제출 전에 거부한다(0은 내부 서버로 허용).
  - 학습 환경 준비가 같은 작업 폴더의 쓰지 않는 이전 앱 버전 학습 환경을 지운다. 지운 개수와 확보한 용량을 보여 준다.
- [ ] **Step 3: `docs/TRAINING_AND_STORAGE.md`** — "## 비전·오디오 겸용 모델(Qwen3.5·Gemma 4)" 절: 동결 키 세 개, 추론 템플릿의
  `enable_thinking: false`, processor 파일(`preprocessor_config.json` 등)이 기본 모델과 함께 복사되고 토크나이저 지문에는 들어가지 않는 점,
  LoRA 대상이 `model.language_model.` 아래 모듈의 전체 이름이라는 점, 병합·확정이 이미지-텍스트 클래스로 불러와 비전 가중치를
  그대로 보존하고 Qwen3.5 `mtp.*`는 결과에 없다는 점.
- [ ] **Step 4: Commit**
```bash
git add docs/OPERATIONS.md docs/WINDOWS_APP.md docs/TRAINING_AND_STORAGE.md
git commit -m "docs: describe Qwen3.5 and Gemma 4 text training, server prices and runtime cleanup"
```

---

### Task 17: 빌드와 전체 검증 (CPU)

**Files:** `docs/PROGRESS.md` (빌드 산출물은 커밋하지 않는다)
- [ ] **Step 1: 전체 검증**
```bash
uv run pytest
uv run ruff check src tests scripts
cd desktop && npm run build && npm test && cargo test --manifest-path src-tauri/Cargo.toml --locked && cd ..
.factory/model-ops-cpu-venv/Scripts/python.exe -I scripts/verify_model_ops_cpu.py --receipt dist/model-ops-cpu-0.6.0.json
```
Expected: 모두 통과. 실패하면 기록하고 고친 뒤 다시 실행한다.
- [ ] **Step 2: 엔진·설치 파일 빌드**
```bash
uv run python scripts/build_desktop.py
uv run python scripts/verify_desktop_package.py --receipt dist/verify-0.6.0.json
cd desktop && npm ci && npm run tauri -- build --ci && cd ..
```
Expected: 영수증에 오류 없음, `bridge_mode: frozen`. `dist/factory-bridge.exe`(Task 18 드라이버가 쓴다)와
`desktop/src-tauri/target/release/bundle/nsis/LLM Tuning Factory_0.6.0_x64-setup.exe`가 생긴다.
- [ ] **Step 3: `docs/PROGRESS.md`** — 맨 위에 새 절 "# Qwen3.5·Gemma 4 텍스트 학습 (2단계)과 1단계 후속 — 0.6.0":
  설계·계획 링크, 브랜치, Task 1–16 요약, 위 명령의 실제 결과(통과 수, 시간), CPU 수치 검증 결과(검사 수), 설치 파일 크기·SHA-256·
  빌드 커밋. 이전 "남은 일"의 keepalive·서버 요금·런타임 정리 항목은 "0.6.0에서 처리"로 고친다.
- [ ] **Step 4: Commit**
```bash
git add docs/PROGRESS.md
git commit -m "docs: record the 0.6.0 CPU verification and installer build"
```

---

### Task 18: GPU 인수 시험 (유료 — 사용자 승인됨)

공장 소유 Pod만 만들고 끝나면 멈춘다. 데이터는 합성 데이터만, MLflow는 끈다. 사용자 Pod·볼륨은 건드리지 않는다
(같은 볼륨 `cys1ne5znp`를 쓰지만 사용자 Pod `amdva0lurw9rv1`은 EXITED 상태로 둔다). 드라이버는 저장소 밖 `%TEMP%\gpu_accept`에 있다.
- `provision.py LABEL GPU_TYPE_ID DATA_CENTER HOURLY_QUOTE [TIMEOUT]`
  - `ManagedPods(...).create(..., approved=True)`로 Pod를 만든다(SECURE, GPU 1, 다이제스트 고정 이미지, 네트워크 볼륨 `/workspace`).
  - 기록 `state/gpu-acceptance-20261030/provision-<label>.json`에 `volume_id`가 있으면 볼륨을 새로 만들지 않는다.
  - RUNNING을 기다리고 SSH 지문을 공인 IP(`ssh-keyscan`)와 RunPod 프록시(`ssh-keygen -lf`) 두 경로로 확인한다(`fingerprint_verified`).
- `run.py LABEL`
  - 앱 설정을 복사해 MLflow 설정을 지운 별도 엔진 루트를 만든다.
  - `dist\factory-bridge.exe`에 한 줄 JSON으로 다음을 보낸다: 환경 확인 → 데이터 등록 → 계획 → 제출 → 실행(`prepare_runtime`) → 작업·로그.
  - `nvidia-smi -l 5`로 메모리를 기록하고 추정치와 비교한 뒤 Pod를 멈춘다.
- [ ] **Step 1: 범위 기록** — PROGRESS 새 절에 승인 범위(A40 1대, CA-MTL-1, 볼륨 `cys1ne5znp`, 최대 4시간·실행당 정책 $5,
  합성 데이터, MLflow 끔, 시험 목록)를 먼저 적고 커밋한다(`docs: record the phase 2 GPU acceptance scope`).
- [ ] **Step 2: 드라이버 확장(저장소 밖)** — `run.py`를 `run_phase2.py`로 복사해 고친다.
  - 인자 `LABEL CASE`. 사례마다 Pod를 멈추지 않는다. 마지막 `stop` 사례만 `ManagedPods(...).stop(pod_id)`를 부른다.
  - 사례 표: 모델, 템플릿, 단계, 방법, 데이터 종류, `max_steps`, 평가 여부.
  - 합성 데이터는 32행을 결정적으로 만든다. 형식: instruction `{"instruction","output"}`, preference `{"instruction","chosen","rejected"}`,
    kto `{"instruction","output","kto_tag": bool}`, text `{"text"}`(`tests/test_dataset_kinds.py`가 받아들이는 형식).
  - 평가 사례는 `evaluation_profile = {kind: "json", dataset_id, dataset_version}`를 둔다.
- [ ] **Step 3: Pod 준비** — `provision-phase2-a40.json`에 `{"volume_id": "cys1ne5znp"}`를 먼저 쓰고
  `uv run python %TEMP%\gpu_accept\provision.py phase2-a40 "NVIDIA A40" CA-MTL-1 0.59 14400`.
  `fingerprint_verified: true`여야 한다. A40이 없으면 30분 동안 세 번까지 다시 시도하고, 그래도 없으면 기록하고 멈춘다(지역을 바꾸지 않는다).
- [ ] **Step 4: 시험 실행** — `uv run python %TEMP%\gpu_accept\run_phase2.py phase2-a40 <CASE>`를 다음 순서로 실행한다.
  각 사례의 상태·시간·train_loss·최대 메모리(MiB)·추정치(GiB)를 기록한다.

  | CASE | 모델 | 단계·방법 | 비고 |
  |---|---|---|---|
  | q08-sft-lora | `Qwen/Qwen3.5-0.8B` | sft·lora, 평가 포함 | 첫 실행: 실행 승인 안에서 환경 준비. `cleanup` 결과 기록 |
  | q08-sft-qlora | 〃 | sft·qlora 4비트 | `Quantizing model to 4 bit` 확인 |
  | q08-dpo / q08-kto / q08-pt | 〃 | dpo·kto·pt lora, `max_steps` 5 | 짧은 실행 |
  | q08-full / q08-freeze | 〃 | sft full / freeze(`mlp`), `max_steps` 5 | 짧은 실행 |
  | gemma-e2b-sft | `google/gemma-4-E2B-it` | sft·lora | `gemma4n` 템플릿, 빈 오디오 대조기 |
  | q9-sft | `Qwen/Qwen3.5-9B` | sft·lora | 느린 linear attention 시간 기록 |
- [ ] **Step 5: RM 시험(앱 밖, rm 허용 여부 결정)** — Pod에 SSH로 접속해 앱이 설치한 학습 환경의
  `<runtime>/venv/bin/llamafactory-cli train rm.yaml`을 실행한다.
  - `rm.yaml`은 `_legacy_plan`과 같은 형식이다: `stage: rm`, `finetuning_type: lora`, `template: qwen3_5_nothink`,
    합성 preference 데이터, 동결 키 세 개, `max_steps: 5`.
  - 출력에 `value_head.safetensors`가 있어야 한다.
  - 이어서 `evaluate_reward`와 같은 형식(`do_eval`)으로 실행해 `eval_accuracy`·`eval_loss`가 유한한지 확인한다.
  - 둘 다 되면 "RM 통과"로 기록한다.
- [ ] **Step 6: 대상·병합 확인(Task 14가 통과했을 때)**
  - `q08-sft-lora`의 산출물 `adapter_config.json`의 `target_modules`를 확인한다. Pod에서
    `inspect_linear_modules(<hf 스냅숏 경로>)`로 얻은 이름에 대한 `resolved_lora_targets("all", names)`와 같아야 한다
    (런타임 venv의 python으로 실행).
  - 같은 어댑터를 Pod에서 확정한다. 업로드한 드라이버가 `model_ops.materialize_model`을 부르고, 기본·어댑터 기술자는
    `scripts/verify_model_ops_cpu.py`의 `Acceptance.descriptor` 방식으로 만든다.
  - 확정 결과를 LLaMA-Factory 예측(`finetuning_type: full`, 4개)으로 불러 `generated_predictions.jsonl`이 비어 있지 않은지 확인한다.
- [ ] **Step 7: 정리** — `run_phase2.py phase2-a40 stop`. Pod가 EXITED인지 RunPod API로 확인한다. 볼륨은 남긴다.
- [ ] **Step 8: 기록** — PROGRESS에 사례별 결과 표, RM 판정, 대상·병합 확인, 메모리 실측과 추정 비교, 정리(`cleanup`) 결과,
  비용(시간 × $0.59), 실패와 수정을 적는다. 시험 중 코드 결함을 고쳤으면 고친 커밋마다 시험을 먼저 쓴다.
  Commit: `docs: record the phase 2 A40 acceptance results`.

---

### Task 19: GPU 검증 결과 반영

**Files:**
- Modify: `src/tuning_factory/capabilities.py`(`model_catalog`), (RM 통과 시) `src/tuning_factory/model_hub.py`(`COMPOSITE_STAGES`)·`capabilities.py`(`COMPOSITE_STAGE_REFUSALS`)
- Test: `tests/test_training_capabilities.py`, `tests/test_model_hub.py`
- [ ] **Step 1: 실패하는 시험 작성** — Task 18에서 통과한 모델만 적는다(예: 셋 다 통과한 경우).
```python
def test_a40_verified_composite_models_are_marked():
    from tuning_factory.capabilities import model_catalog
    models = {model["id"]: model for model in model_catalog()}
    for name in ("Qwen/Qwen3.5-0.8B", "Qwen/Qwen3.5-9B", "google/gemma-4-E2B-it"):
        assert models[name]["gpu_verified"] is True and models[name]["verified_capabilities"] == ["8.6"]
    assert models["Qwen/Qwen3.5-0.8B-Base"]["gpu_verified"] is False
```
RM이 통과했으면 Task 10 시험의 `({"training_stage": "rm"}, "값 헤드")` 사례를 지우고, 허용 시험의 단계 목록과
`test_model_hub.py`·Task 8 시험의 `supported_stages`를 `["pt", "sft", "rm", "dpo", "kto"]`로 바꾼다.
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_training_capabilities.py tests/test_model_hub.py -q` — Expected: FAIL
- [ ] **Step 3: 구현**
  - `capabilities.py`에 다음 dict를 두고, 복합 항목을 만들 때 `model_id in VERIFIED_COMPOSITE`이면
    `gpu_verified: True`, `verified_capabilities: ["8.6"]`, `verification_scope`에 그 문구를 넣는다.
    ```python
    VERIFIED_COMPOSITE = {"Qwen/Qwen3.5-0.8B": "0.6.0 SFT LoRA/QLoRA, DPO/KTO/PT, full/freeze synthetic A40", ...}
    ```
  - RM이 통과했으면 `COMPOSITE_STAGES = [x for x in ALL_STAGES if x != "ppo"]`로 바꾸고 `COMPOSITE_STAGE_REFUSALS`에서 `"rm"`을 지운다.
    통과하지 못했으면 바꾸지 않고, 실패 원인을 PROGRESS에 적는다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest -q && uv run ruff check src tests scripts` — Expected: PASS
- [ ] **Step 5: Commit**(RM 변경은 별도 커밋)
```bash
git add src/tuning_factory/capabilities.py tests/test_training_capabilities.py
git commit -m "feat: record A40 verification of Qwen3.5 and Gemma 4"
git add src/tuning_factory/model_hub.py src/tuning_factory/capabilities.py tests/test_training_capabilities.py tests/test_model_hub.py
git commit -m "feat: allow reward modeling for Qwen3.5 and Gemma 4 after GPU verification"
```

---

### Task 20: 화면 끝까지 시험 스크립트

설치된 앱을 DevTools 포트로 띄워 화면을 직접 조작한다. 선택자는 `desktop/src/main.ts`와 기존 Playwright 시험의 것이다.
- 탐색: `nav [data-action="nav:<id>"]`, 제목 `h1`.
- 버튼: "연결 검사", "환경 확인", "GPU 정보 → 학습 설정에 적용", `#training-form`의 "실행 계획 확인",
  "데이터 전송에 동의하고 학습 시작".
- 계획 카드: `[data-testid=plan-compatibility]`.
- 승인 창: `<dialog>`의 "내용 확인 · 진행".
- 데이터: `#dataset-form`의 "파일 경로" 입력란. 기본 파일 대화상자는 자동화할 수 없으므로 쓰지 않는다.

**Files:**
- Create: `scripts/desktop_gui_acceptance.py`, `tests/test_desktop_gui_acceptance.py`
- [ ] **Step 1: 실패하는 시험 작성** — `tests/test_desktop_gui_acceptance.py`
```python
import importlib.util
import sys
from pathlib import Path

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "desktop_gui_acceptance.py"

def load():
    spec = importlib.util.spec_from_file_location("desktop_gui_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module

def test_the_driver_imports_without_playwright_and_redacts_credentials():
    gui = load()
    assert "playwright" not in sys.modules
    key, pem = "rpa_" + "A1" * 10, "-" * 5 + "BEGIN OPENSSH PRIVATE KEY" + "-" * 5
    cleaned = gui.redact(f"key {key} then {pem} and Bearer abc.def")
    assert key not in cleaned and pem not in cleaned and "abc.def" not in cleaned
    assert gui.redact("학습 환경을 준비했습니다") == "학습 환경을 준비했습니다"
    assert gui.parse_args(["--state-dir", "s", "--dataset", "d.json"]).model == "Qwen/Qwen3.5-0.8B"
```
- [ ] **Step 2: 실패 확인** — Run: `uv run pytest tests/test_desktop_gui_acceptance.py -v` — Expected: FAIL (`FileNotFoundError`)
- [ ] **Step 3: 구현** — `scripts/desktop_gui_acceptance.py`
```python
"""Drive the installed desktop app end to end through WebView2's DevTools port (no browser download).

Launches the installed app with WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>, connects with
Playwright's chromium.connect_over_cdp and walks: 연결 검사 → 환경 확인 → synthetic data registration → training settings
→ 실행 계획 (GPU compatibility card) → 학습 시작 approval (runtime preparation notice) → completion → logs.
Screenshots and a JSON-lines report go to --state-dir. It never reads credentials and redacts credential-shaped text.
It uses the app's real state, connection and MLflow settings and starts paid GPU work: run it only when authorized.
Usage: uv run --with playwright==1.64.0 python scripts/desktop_gui_acceptance.py --state-dir DIR --dataset FILE
"""
import argparse
import json
import os
import re
import subprocess
import time
import urllib.request
from datetime import datetime
from pathlib import Path

APP = Path(os.environ.get("LOCALAPPDATA", "")) / "LLM Tuning Factory" / "llm-tuning-factory-desktop.exe"
FINISHED = {"완료", "실패", "취소"}
# Mirrors scripts/check_secrets.py; the key header is built in pieces because that scan walks git history.
SECRETS = [re.compile(pattern) for pattern in (r"rpa_[A-Za-z0-9_]{8,}", r"hf_[A-Za-z0-9]{16,}", r"(?i)bearer\s+\S+",
                                               "-" * 5 + r"BEGIN [A-Z ]*PRIVATE KEY" + "-" * 5)]

def redact(text):
    for pattern in SECRETS:
        text = pattern.sub("[REDACTED]", text)
    return text

def parse_args(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--state-dir", type=Path, required=True)
    parser.add_argument("--dataset", type=Path, required=True, help="a synthetic instruction JSON file")
    parser.add_argument("--model", default="Qwen/Qwen3.5-0.8B")
    parser.add_argument("--port", type=int, default=9223)
    parser.add_argument("--app", type=Path, default=APP)
    parser.add_argument("--timeout", type=int, default=7200, help="seconds to wait for the training job")
    return parser.parse_args(argv)
```
`Walk(page, state_dir)` 도우미:
- `record(step, ok, **facts)`: 전체 화면 사진 `<state>/<NN>-<step>.png`, `redact`한 JSON 한 줄을 `gui-acceptance.jsonl`과
  표준 출력에 남기고, `ok`가 거짓이면 `SystemExit`.
- `nav(view, heading)`: `nav [data-action="nav:<view>"]`를 누르고 그 `h1` 제목을 기다린다.
- `settle(timeout_ms)`: 0.5초 뒤 `.notice.busy`가 사라질 때까지 기다리고, `.notice`(바쁨·오류 아님)와
  `.notice.error[role=alert]`의 글을 돌려준다.

`walk(w, args, version)`는 다음 순서로 실행하고 단계마다 `w.record(...)`를 부른다.
1. 시작 대기: 제목 "작업 공간", "작업 공간 불러오는 중" 사라짐.
2. 연결 설정
   1. "연결 검사" → `settle(120_000)`. 오류가 없어야 하고 "연결 검사 결과" 절의 글을 기록한다.
   2. "환경 확인" → `settle(960_000)`(읽기 전용 점검은 최대 15분). 오류가 없어야 한다.
3. 데이터: `#dataset-form`에 다음을 넣고 "검사하고 등록"을 누른다. 안내에 "데이터 버전을 등록했습니다"가 있어야 한다.
   - "데이터 ID" `gui_accept_synth`, "버전" `version`(`gui-<YYYYMMDDHHMM>`).
   - "데이터 종류" `instruction`, "파일 경로" `args.dataset.resolve()`.
   - `input[name=synthetic]`·`input[name=export_allowed]` 체크.
4. 학습 설정에서 `#training-form`의 "예제·저장한 설정 불러오기"를 `t:0`(paper_extraction)으로 고른다.
   연결 설정으로 가서 "GPU 정보 → 학습 설정에 적용"을 누르고(작업 GPU 종류·요금을 Pod에 맞춤) 학습 설정으로 돌아온다.
   - "학습 데이터" `gui_accept_synth:<version>`, "기반 모델" `args.model`, "학습 단계" `sft`, "학습 방법" `lora`.
   - "최대 실행 시간 (초)" `3600`, "최대 예상 GPU 비용 (USD)" `2`.
5. "실행 계획 확인" → 제목 "실행 전 확인"을 기다린다. `plan-compatibility` 글에 "실행 시 거부될 항목"이 없어야 한다(주의 항목은 기록).
6. 승인 창
   1. "데이터 전송에 동의하고 학습 시작"을 누르고 `dialog`를 기다린다(최대 960초). 오류 안내가 먼저 나오면 실패로 기록한다.
   2. 본문을 기록한다(`prepare`: "학습 환경이 없어 학습 전에 먼저 설치합니다" 포함 여부, `cleanup_notice`: "이전 앱 버전의 학습 환경" 포함 여부).
   3. "내용 확인 · 진행"을 누른다.
7. 실행 관리
   - 제목 "실행 관리"를 기다리고 15초마다 활성 작업 카드(`h3` 안의 `.badge`; 구현 시 `main.ts:461-470`에서 확인)의 상태를 읽는다.
   - `FINISHED`가 될 때까지 기다린다(최대 `args.timeout`). 작업 ID(활성 카드)·상태를 기록한다. `완료`여야 통과다.
8. 결과
   - "학습 로그"를 누르고 `pre.log` 끝 2000자를 기록한다(redact).
   - "평가 결과" 버튼이 있으면 누르고 결과를 기록한다.

`main(argv=None)`:
- 이미 실행 중인 앱(`tasklist`에 `llm-tuning-factory-desktop.exe`)이 있으면 "close the running app first: WebView2 reuses its
  browser process and ignores the port"로 멈춘다.
- `state_dir`를 만들고 `env`에 `WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS=--remote-debugging-port=<port>`를 더해 `subprocess.Popen([app], env=env)`로 띄운다.
- `http://127.0.0.1:<port>/json/version`이 응답할 때까지 60초 기다린다.
- `from playwright.sync_api import sync_playwright`(함수 안에서만 import)로 `connect_over_cdp`하고, URL이 `tauri|localhost`인 페이지를
  찾아 기본 시간 제한 60초로 `walk`를 실행한다.
- `finally`에서 연결을 닫고 띄운 앱을 `terminate()`한다. 반환은 0(모든 단계 통과)이다.
- [ ] **Step 4: 통과 확인** — Run: `uv run pytest tests/test_desktop_gui_acceptance.py -v && uv run ruff check scripts tests` — Expected: PASS
- [ ] **Step 5: Commit**
```bash
git add scripts/desktop_gui_acceptance.py tests/test_desktop_gui_acceptance.py
git commit -m "test: add an installed-app GUI acceptance driver over WebView2 DevTools"
```

---

### Task 21: 화면 끝까지 시험 실행 (사용자 Pod, 사용자 요청)
- [ ] **Step 1: 범위 기록** — PROGRESS에 범위를 먼저 적고 커밋한다(`docs: record the GUI acceptance scope`).
  - 사용자 Pod `amdva0lurw9rv1`(A40, CA-MTL-1, $0.59/h)을 시험 동안만 켜고 끝나면 멈춘다. 지우지 않는다.
  - 앱 설정 그대로 MLflow에 합성 실행 1건이 기록된다. 데이터는 32행 합성 데이터다.
  - 앱의 이전 대기 작업 `76986bce`는 건드리지 않는다.
- [ ] **Step 2: 설치** — Task 19까지 반영한 HEAD에서 Task 17 Step 2를 다시 실행한다. 앱을 닫고 그 설치 파일로 같은 위치에 덮어 설치한다.
  `"desktop\src-tauri\target\release\bundle\nsis\LLM Tuning Factory_0.6.0_x64-setup.exe" /S /D=%LOCALAPPDATA%\LLM Tuning Factory`
  (NSIS는 이전 폴더를 기억하므로 `/D`를 반드시 준다). 설치된 `factory-bridge.exe`의 버전·커밋을 기록한다.
- [ ] **Step 3: Pod 켜기** — 저장소 밖 `%TEMP%\gpu_accept\user_pod_power.py start amdva0lurw9rv1`을 만든다.
  - `vault_only.vault("RUNPOD_API_KEY")`로 키를 읽는다(출력하지 않는다).
  - RunPod REST `POST /v1/pods/{id}/start`를 부르고, `desiredStatus` RUNNING·공인 IP·22번 포트가 될 때까지 기다린다.
  - GPU가 없어 켜지지 않으면 기록하고 멈춘다. 다른 Pod를 만들지 않는다.
- [ ] **Step 4: 지문 확인** — `provision.py`의 두 경로(`fingerprint_direct`, `fingerprint_proxy`)로 SSH 지문을 읽는다.
  - 둘이 같고 앱 설정과 다르면, 앱을 닫은 채 `%APPDATA%\kr.kbiofoundry.tuningfactory\settings.json`을 백업하고 `ssh_fingerprint`만 바꾼다.
  - 둘이 다르면 Pod를 멈추고 중단한다.
- [ ] **Step 5: 시험** —
  `uv run --with playwright==1.64.0 python scripts/desktop_gui_acceptance.py --state-dir state/gui-acceptance-<날짜> --dataset <run.py가 쓰는 32행 합성 파일>`.
  - PyPI에 `playwright==1.64.0`이 없으면 Node 1.64.0과 가장 가까운 1.x 버전을 고정해 쓰고, 그 버전을 스크립트 독스트링과 PROGRESS에 적는다.
  - 0.6.0은 소스 해시가 바뀌었으므로 승인 창에 학습 환경 설치·정리 안내가 나와야 한다.
- [ ] **Step 6: Pod 멈추기** — `user_pod_power.py stop amdva0lurw9rv1`(REST stop, 사용자 요청). API로 EXITED를 확인한다. 성공·실패와 관계없이 실행한다.
- [ ] **Step 7: 기록** — PROGRESS에 단계별 결과, 화면 사진 폴더, 작업 ID·상태·시간, 정리(`cleanup`)로 지운 학습 환경과 확보 용량,
  Pod 사용 시간·비용, 발견한 결함을 적는다. 결함을 고쳤으면 시험을 먼저 쓰고 고친 뒤 Step 2–6을 다시 한다.
  Commit: `docs: record the installed-app GUI acceptance on the user's A40 Pod`.

---

### Task 22: 최종 빌드·재설치·기록·PR
- [ ] **Step 1:** Task 21 설치 이후 `src/`·`desktop/` 변경이 있으면 Task 17 Step 1–2를 다시 실행하고 Task 21 Step 2와 같이 재설치한다.
  없으면 Task 21의 설치가 최종이다.
- [ ] **Step 2:** `uv run pytest`, `uv run ruff check src tests scripts`, `npm run build`, `npm test`, `cargo test --locked`를
  마지막으로 실행하고 결과를 PROGRESS에 적는다(설치 파일 크기·SHA-256·빌드 커밋, `dist/verify-0.6.0.json`).
  "남은 일"에 남은 항목을 적는다: 26B-A4B·31B·27B·35B-A3B의 큰 GPU 검증, RM(통과하지 못했다면), fla 커널, 볼륨 `ls9ua5ymse` 수동 삭제.
- [ ] **Step 3: Commit·push**
```bash
git add docs/PROGRESS.md
git commit -m "docs: record the final 0.6.0 verification and installer"
git push -u origin claude/phase2-models
gh pr create --base claude/gpu-compat-runtime --title "Phase 2: Qwen3.5 and Gemma 4 text training, follow-ups (0.6.0)" --body-file <본문 파일>
```
PR 본문: 요약(F2·F3·F1, 2단계), 검증(CPU·GPU 인수·화면 시험 결과), 남은 일, 마지막 줄
`🤖 Generated with [Claude Code](https://claude.com/claude-code)`.
- [ ] **Step 4:** `gh run list --branch claude/phase2-models --limit 5`로 호스트 CI를 확인한다. 작업이 시작되지 않았으면 결제 제한
  주석부터 확인한다(2026-10-30 지출 한도로 작업이 시작되지 않은 적이 있다).
