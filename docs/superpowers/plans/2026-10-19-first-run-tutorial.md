# First-run Hands-on Tutorial Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 처음 사용자가 Windows 앱 안에서 연결 → 데이터 → 학습 설정 → dry-run 실행 계획을 직접 눌러 보며 익히는 11단계 실습형 투어를 추가한다.

**Architecture:** 화면을 막지 않는 말풍선 투어 엔진(`tutorial.ts`)과 단계 정의(`tutorial-steps.ts`)를 분리하고, 기존 `render()` 끝에서 위치를 다시 계산한다. 단계는 `data-tour` 표시만 가리킨다. 백엔드는 내장 합성 샘플을 영속 상태 폴더에 복사하는 `tutorial_sample` 동작과 `tutorial_sample` 예약 ID 보호만 추가한다. GPU·SSH·데이터 전송 경로는 호출하지 않는다.

**Tech Stack:** TypeScript/Vite, Playwright(mock Tauri invoke), Python 3.12 desktop sidecar, Rust/Tauri allowlist.

**Spec:** [설계](../specs/2026-10-19-first-run-tutorial-design.md). 사용자 설계 승인 2026-10-19 “진행해”, 설계 문서 승인 “승인”.

**순서:** [일반 학습 워크플로 계획](2026-10-17-general-training-workflows.md)의 Task 6A 독립 검토와 Task 6B를 마친 뒤 이 계획을 실행하고, 이후 Task 7–11로 돌아간다.

---

## Global Constraints

- `uv sync --extra dev --extra tracking --locked`, 완료 전 `uv run pytest`, `uv run ruff check src tests`.
- 투어는 `submit`, `execute`, `resume`, `runtime_check`, `runtime_prepare`, `connection`, `pod`, `s3_*`, `settings_save`, `forget_credentials`를 호출하지 않는다.
- 연습 데이터는 `synthetic=True`, `export_allowed=False`로만 등록된다. 실제 GPU·SSH 서버·기관 데이터는 쓰지 않는다.
- 기존 `desktop/tests/workspace.spec.ts`는 수정하지 않는다. 이 파일은 비밀값 흐름 뒤 `localStorage.length === 0`을 확인하며, 튜토리얼은 닫기/완료 때만 자기 키를 쓴다.
- DESIGN.md: 강조색 #5367c8 하나, 반경 8px, 기본 13px, 장식 그라데이션 없음.

## File Structure

| 파일 | 책임 |
|---|---|
| `examples/tutorial_sample.json` (생성) | 가상 연구 업무 한국어 Alpaca 합성 예제 8건 |
| `src/tuning_factory/desktop.py` (수정) | `tutorial_sample` 동작, 예약 ID 보호 |
| `tests/test_desktop_tutorial.py` (생성) | 백엔드 동작·보호 규칙 시험 |
| `desktop/src-tauri/src/main.rs` (수정) | allowlist에 `tutorial_sample` 추가 |
| `scripts/build_desktop.py`, `scripts/verify_desktop_package.py` (수정) | 샘플 자산 포함과 설치본 확인 |
| `desktop/src/tutorial-steps.ts` (생성) | 11단계 정의(데이터만, DOM 없음) |
| `desktop/src/tutorial.ts` (생성) | 투어 엔진: 상태·말풍선·강조·키보드·완료 기록 |
| `desktop/src/workbench.ts` (수정) | `tutorial` 아이콘 |
| `desktop/src/main.ts` (수정) | 엔진 연결, 안내 카드, 사이드바/Ctrl+K, `data-tour` 표시, 체크리스트 판정 |
| `desktop/src/style.css` (수정) | 투어 스타일 한 줄 추가 |
| `desktop/tests/tutorial.spec.ts` (생성) | 투어 Playwright 시험 |
| `docs/WINDOWS_APP.md`, `docs/PROGRESS.md` (수정) | 사용 안내와 실제 검증 기록 |

---

### Task 0: 선행 조건 확인

**Files:** 없음 (확인만)

- [ ] **Step 1: Task 6A 검토와 Task 6B 완료 확인**

Run: `git log --oneline -15`
Expected: Task 6B 커밋 `feat: configure SSH GPU servers in Windows workbench`가 있고, `docs/PROGRESS.md`에 Task 6A 독립 검토 통과가 기록되어 있다.

- [ ] **Step 2: Task 6B가 만든 연결 화면 표시 확인**

Run: `grep -c 'data-tour="connection-provider"\|data-tour="connection-fields"\|data-tour="connection-runtime"' desktop/src/main.ts`
Expected: `3`. 하나라도 없으면 이 계획을 시작하지 말고 Task 6B의 표시 요구사항을 먼저 완료한다.

---

### Task 1: 내장 연습 샘플과 예약 ID 보호

**Files:**
- Create: `examples/tutorial_sample.json`
- Modify: `src/tuning_factory/desktop.py`
- Test: `tests/test_desktop_tutorial.py`

- [ ] **Step 1: 실패 시험 작성**

`tests/test_desktop_tutorial.py`:

```python
import json
from pathlib import Path

import pytest

from tuning_factory import desktop
from tuning_factory.registry import digest

SAMPLE = Path(__file__).resolve().parents[1] / "examples/tutorial_sample.json"


def register(app, path, **overrides):
    data = {"dataset_id": "tutorial_sample", "version": "1", "kind": "instruction", "path": str(path),
            "synthetic": True, "export_allowed": False, **overrides}
    return app.dispatch("dataset", data)


def test_bundled_sample_is_fictional_korean_alpaca():
    rows = json.loads(SAMPLE.read_text(encoding="utf-8"))
    assert len(rows) == 8
    assert all(set(row) == {"instruction", "input", "output"} for row in rows)
    assert all("가상" in row["instruction"] and row["input"].startswith("연습용") for row in rows)


def test_tutorial_sample_copies_the_bundle_into_persistent_state(tmp_path):
    result = desktop.DesktopService(tmp_path).dispatch("tutorial_sample", {})
    copy = tmp_path / "tutorial" / "tutorial_sample.json"
    assert result == {"dataset_id": "tutorial_sample", "version": "1", "kind": "instruction",
                      "path": str(copy), "sha256": digest(SAMPLE.read_bytes()), "rows": 8}
    assert copy.read_bytes() == SAMPLE.read_bytes()


def test_tutorial_sample_registers_repeatably_without_gpu_export(tmp_path):
    app = desktop.DesktopService(tmp_path)
    path = app.dispatch("tutorial_sample", {})["path"]
    first = register(app, path)
    assert register(app, path) == first
    assert first["synthetic"] is True and first["export_allowed"] is False and first["rows"] == 8


@pytest.mark.parametrize("overrides", [
    {"export_allowed": True}, {"synthetic": False}, {"version": "2"}, {"kind": "text"},
    {"dataset_id": "Tutorial_Sample"},
])
def test_reserved_tutorial_id_rejects_changed_registration(tmp_path, overrides):
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(desktop.DesktopService(tmp_path), SAMPLE, **overrides)


def test_reserved_tutorial_id_rejects_other_content(tmp_path):
    other = tmp_path / "real.json"
    other.write_text(json.dumps([{"instruction": "q", "output": "a"}]), encoding="utf-8")
    with pytest.raises(ValueError, match="tutorial_sample"):
        register(desktop.DesktopService(tmp_path / "state"), other)


def test_reserved_tutorial_id_cannot_be_imported_from_s3(tmp_path):
    app = desktop.DesktopService(tmp_path)
    with pytest.raises(ValueError, match="tutorial_sample"):
        app.dispatch("s3_import", {"dataset_id": "TUTORIAL_SAMPLE", "version": "1", "approved": True,
                                   "bucket": "bucket", "key": "key"})


def test_tutorial_plan_is_dry_run_and_submission_is_refused(tmp_path):
    app = desktop.DesktopService(tmp_path)
    register(app, app.dispatch("tutorial_sample", {})["path"])
    templates = app.dispatch("catalog", {})["templates"]
    config = next(x["config"] for x in templates if x["name"] == "paper_extraction")
    config.update(dataset_id="tutorial_sample", dataset_version="1")
    plan = app.dispatch("plan", {"config": config})
    assert plan["dry_run"] is True and plan["manifest"]["dataset"]["rows"] == 8
    with pytest.raises(ValueError, match="export has not been authorized"):
        app.dispatch("submit", {"config": config, "approved": True})
    assert app.dispatch("jobs", {}) == []
```

- [ ] **Step 2: 실패 확인**

Run: `uv run pytest tests/test_desktop_tutorial.py -q`
Expected: FAIL — 샘플 파일 없음(`FileNotFoundError`)과 `unsupported desktop action`.

- [ ] **Step 3: 샘플 파일 작성**

`examples/tutorial_sample.json` (UTF-8, BOM 없음, LF):

```json
[
  {"instruction": "다음 가상의 실험 기록에서 시료 ID를 JSON으로 추출하세요.", "input": "연습용 기록: 가상 시료 TUT-001을 37도에서 2시간 배양했다.", "output": "{\"sample_id\": \"TUT-001\"}"},
  {"instruction": "다음 가상의 실험 기록에서 시료 ID와 배양 온도를 JSON으로 추출하세요.", "input": "연습용 기록: 가상 시료 TUT-002를 30도에서 하룻밤 배양했다.", "output": "{\"sample_id\": \"TUT-002\", \"temperature_c\": 30}"},
  {"instruction": "다음 가상의 장비 기록에서 장비 이름을 JSON으로 추출하세요.", "input": "연습용 기록: 가상 원심분리기 DEMO-C1으로 5분간 분리했다.", "output": "{\"instrument\": \"DEMO-C1\"}"},
  {"instruction": "다음 가상의 실험노트를 한 문장으로 요약하세요.", "input": "연습용 노트: 가상 시료 세 개를 준비했고 그중 하나의 색이 변했다. 나머지 두 개는 변화가 없었다.", "output": "가상 시료 세 개 중 하나만 색이 변했다."},
  {"instruction": "다음 가상의 실험노트를 한 문장으로 요약하세요.", "input": "연습용 노트: 가상 배지를 새로 만들고 pH를 두 번 확인했다. 두 번 모두 기준 범위 안이었다.", "output": "새로 만든 가상 배지의 pH가 두 번 모두 기준 범위 안이었다."},
  {"instruction": "다음 가상의 작업 기록을 한 문장으로 요약하세요.", "input": "연습용 기록: 가상 시약 재고를 점검했고 한 품목이 부족해 주문 요청을 남겼다.", "output": "가상 시약 재고 점검 중 부족한 한 품목의 주문을 요청했다."},
  {"instruction": "다음 가상의 작업을 '실험', '분석', '행정' 중 하나로 분류하세요.", "input": "연습용 작업: 가상 측정값을 표로 정리하고 평균을 계산했다.", "output": "분석"},
  {"instruction": "다음 가상의 작업을 '실험', '분석', '행정' 중 하나로 분류하세요.", "input": "연습용 작업: 가상 장비 사용 예약 신청서를 제출했다.", "output": "행정"}
]
```

- [ ] **Step 4: 백엔드 구현**

`src/tuning_factory/desktop.py` import 줄을 바꾼다:

```python
from .registry import canonical, digest, parse_dataset, safe_name
```

`assets_root()` 아래에 추가한다:

```python
TUTORIAL_DATASET_ID = "tutorial_sample"
TUTORIAL_REFUSAL = "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다."


def tutorial_sample_path():
    return assets_root() / "examples" / "tutorial_sample.json"


def reserved_tutorial_id(data):
    # Windows registry folders are case-insensitive, so spelling variants share the reserved name.
    return str(data.get("dataset_id", "")).casefold() == TUTORIAL_DATASET_ID


def check_tutorial_registration(data):
    """The practice ID only ever names the bundled synthetic sample, never transferable data."""
    path = Path(str(data.get("path", "")))
    if (data.get("dataset_id") != TUTORIAL_DATASET_ID or data.get("version") != "1"
            or data.get("kind", "instruction") != "instruction" or data.get("synthetic") is not True
            or data.get("export_allowed") is True or not path.is_file()
            or digest(path.read_bytes()) != digest(tutorial_sample_path().read_bytes())):
        raise ValueError(TUTORIAL_REFUSAL)
```

`dispatch()`의 `if action == "dataset_preview":` 바로 앞에 추가한다:

```python
        if action == "tutorial_sample":
            # The frozen onefile asset folder disappears after this process; later requests read the state copy.
            raw = tutorial_sample_path().read_bytes()
            copy = self.root / "tutorial" / "tutorial_sample.json"
            if not copy.is_file() or copy.read_bytes() != raw:
                copy.parent.mkdir(parents=True, exist_ok=True)
                temporary = copy.with_suffix(".tmp")
                temporary.write_bytes(raw)
                os.replace(temporary, copy)
            return {"dataset_id": TUTORIAL_DATASET_ID, "version": "1", "kind": "instruction",
                    "path": str(copy), "sha256": digest(raw), "rows": len(parse_dataset(raw, "instruction")[0])}
```

`if action == "dataset":` 블록을 다음으로 바꾼다:

```python
        if action == "dataset":
            if reserved_tutorial_id(data):
                check_tutorial_registration(data)
            return self.factory.registry.register_dataset(
                data["dataset_id"], data["version"], Path(data["path"]),
                export_allowed=data.get("export_allowed") is True, synthetic=data.get("synthetic") is True,
                kind=data.get("kind", "instruction"))
```

`if action == "s3_import":` 블록 첫 줄(승인 검사 앞)에 추가한다:

```python
            if reserved_tutorial_id(data):
                raise ValueError(TUTORIAL_REFUSAL)
```

- [ ] **Step 5: 통과 확인**

Run: `uv run pytest tests/test_desktop_tutorial.py tests/test_desktop.py tests/test_desktop_extensions.py -q`
Expected: 모두 PASS. `tutorial_sample` 결과의 `rows`가 8이 아니면 샘플 JSON을 고치고 `parse_dataset`은 바꾸지 않는다.

- [ ] **Step 6: Ruff와 커밋**

```bash
uv run ruff check src tests
git add examples/tutorial_sample.json src/tuning_factory/desktop.py tests/test_desktop_tutorial.py
git commit -m "feat: provide a protected synthetic sample for the desktop tutorial"
```

---

### Task 2: 네이티브 허용 목록과 설치 자산

**Files:**
- Modify: `desktop/src-tauri/src/main.rs` (`allowed()`, `only_named_operations_are_available` 시험)
- Modify: `scripts/build_desktop.py`
- Modify: `scripts/verify_desktop_package.py`

- [ ] **Step 1: 실패 시험 작성**

`main.rs`의 `only_named_operations_are_available` 시험에서 `assert!(allowed("s3_import"));` 다음 줄에 추가한다:

```rust
        assert!(allowed("tutorial_sample"));
```

- [ ] **Step 2: 실패 확인**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release only_named_operations_are_available`
Expected: FAIL — `assertion failed: allowed("tutorial_sample")`.

- [ ] **Step 3: 허용 목록 구현**

`allowed()`의 마지막 줄을 바꾼다:

```rust
        "model_search" | "model_inspect" | "model_import" | "tutorial_sample")
```

- [ ] **Step 4: 통과 확인**

Run: `cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release`
Expected: 모든 Rust 시험 PASS.

- [ ] **Step 5: 설치 자산 복사**

`scripts/build_desktop.py`의 `shutil.copytree(ROOT / "configs/ai", ...)` 다음 줄에 추가한다:

```python
    (assets / "examples").mkdir(exist_ok=True)
    shutil.copyfile(ROOT / "examples/tutorial_sample.json", assets / "examples/tutorial_sample.json")
```

- [ ] **Step 6: 설치본 확인 추가**

`scripts/verify_desktop_package.py`의 `wheel = next(...)` 줄 바로 앞에 추가한다:

```python
    # The onefile asset folder is temporary; the returned practice copy must outlive this process.
    tutorial = request("tutorial_sample")
    bundled = (root / "examples/tutorial_sample.json").read_bytes()
    assert tutorial["sha256"] == digest(bundled) and tutorial["rows"] == 8
    assert Path(tutorial["path"]).read_bytes() == bundled
    request("dataset", {"dataset_id": "tutorial_sample", "version": "1", "path": tutorial["path"],
                        "synthetic": True, "export_allowed": True}, expected_error="tutorial_sample")
    request("dataset", {"dataset_id": "tutorial_sample", "version": "1", "path": tutorial["path"], "synthetic": True})
```

같은 파일의 마지막 `print(json.dumps({...}))` 딕셔너리에 `"tutorial_sample_persisted": True,`를 `"frozen_s3_sdk_loaded": True,` 다음에 추가한다.

- [ ] **Step 7: 정적 확인과 커밋**

Run: `uv run ruff check scripts/build_desktop.py scripts/verify_desktop_package.py`
Expected: `All checks passed!`. 설치본 빌드와 `verify_desktop_package.py` 실행은 Task 11 설치 인수에서 한다.

```bash
git add desktop/src-tauri/src/main.rs scripts/build_desktop.py scripts/verify_desktop_package.py
git commit -m "feat: bundle and allow the desktop tutorial sample"
```

---

### Task 3: 투어 엔진·단계·화면 연결

**Files:**
- Create: `desktop/src/tutorial-steps.ts`, `desktop/src/tutorial.ts`, `desktop/tests/tutorial.spec.ts`
- Modify: `desktop/src/main.ts`, `desktop/src/workbench.ts`, `desktop/src/style.css`

- [ ] **Step 1: 실패 시험 작성**

`desktop/tests/tutorial.spec.ts`:

```ts
import { test, expect, type Page } from "@playwright/test";
import { readFileSync } from "node:fs";
import { tutorialSteps } from "../src/tutorial-steps.ts";

const config = JSON.parse(readFileSync(new URL("./config.json", import.meta.url), "utf8"));
const forbidden = ["submit", "execute", "resume", "runtime_check", "runtime_prepare", "connection", "pod",
  "s3_list", "s3_head", "s3_import"];
type Boot = { registered?: boolean; withoutDummy?: boolean; seen?: boolean };

async function boot(page: Page, options: Boot = {}) {
  await page.addInitScript(({ config, options }) => {
    if (options.seen && !localStorage.getItem("tuning-factory.tutorial.v1"))
      localStorage.setItem("tuning-factory.tutorial.v1", JSON.stringify({ outcome: "completed" }));
    const calls: any[] = [];
    const sample = { dataset_id: "tutorial_sample", version: "1", kind: "instruction", rows: 8, sha256: "e".repeat(64),
      path: "C:\\Users\\tester\\AppData\\state\\tutorial\\tutorial_sample.json" };
    const datasets: any[] = options.withoutDummy ? [] : [{ dataset_id: "dummy", version: "1", rows: 2, export_allowed: true, synthetic: true }];
    if (options.registered) datasets.push({ dataset_id: "tutorial_sample", version: "1", rows: 8, export_allowed: false, synthetic: true });
    (window as any).__testCalls = calls;
    (window as any).__TAURI_INTERNALS__ = { invoke: async (command: string, args: any) => {
      calls.push({ command, args });
      if (command === "settings_get") return {};
      if (command !== "factory_action") return {};
      const { action, data } = args;
      if (action === "catalog") return { templates: [{ name: "paper_extraction", config }], profiles: [], datasets: structuredClone(datasets),
        models: [{ id: config.base_model, template: "qwen", architecture: "qwen2", gpu_verified: true }],
        training_stages: [{ id: "sft", label: "SFT · 지도 학습", dataset_kind: "instruction" }] };
      if (action === "jobs") return [];
      if (action === "capabilities") return { valid: true, errors: [], warnings: [], supported_stages: ["sft"],
        methods: ["lora", "qlora"], dataset_kind: "instruction", engine_version: "llamafactory==0.9.5" };
      if (action === "tutorial_sample") return sample;
      if (action === "dataset_preview") return { rows: data.path === sample.path ? 8 : 1, format: "alpaca", duplicate_rows: 0,
        samples: [{ instruction: "가상 시료", output: "TUT-001" }], bytes: 900 };
      if (action === "dataset") {
        if (data.dataset_id === "tutorial_sample" && (data.export_allowed || !data.synthetic))
          throw "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다.";
        if (!datasets.some(d => d.dataset_id === data.dataset_id && d.version === data.version))
          datasets.push({ dataset_id: data.dataset_id, version: data.version, rows: 8, export_allowed: false, synthetic: true });
        return datasets.at(-1);
      }
      if (action === "plan") return { dry_run: true, estimated_cost_usd: .5, note: "실행 버튼 전에는 GPU 작업을 시작하지 않습니다.",
        manifest: { config: data.config, dataset: { dataset_id: data.config.dataset_id, rows: 8 } } };
      return {};
    } };
  }, { config, options });
  await page.goto("/");
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
}

const popover = (page: Page) => page.locator(".tour-popover");
const tourButton = (page: Page, name: string) => popover(page).getByRole("button", { name, exact: true });

async function expectStep(page: Page, id: string) {
  await expect(popover(page).locator("#tour-title")).toHaveText(tutorialSteps.find(s => s.id === id)!.title);
}

async function advanceTo(page: Page, id: string) {
  const goal = tutorialSteps.findIndex(s => s.id === id);
  const title = await popover(page).locator("#tour-title").textContent();
  for (let i = tutorialSteps.findIndex(s => s.title === title); i < goal; i++) {
    const skip = tourButton(page, "건너뛰기");
    await ((await skip.count()) ? skip : tourButton(page, "다음")).click();
    await expectStep(page, tutorialSteps[i + 1].id);
  }
}

test("first-run card starts the tutorial and dismissal is remembered", async ({ page }) => {
  await boot(page);
  const card = page.getByRole("region", { name: "튜토리얼 안내" });
  await expect(card).toBeVisible();
  await card.getByRole("button", { name: "다음에 하기" }).click();
  await expect(card).toHaveCount(0);
  await page.reload();
  await expect(page.getByText("작업 공간 불러오는 중")).toHaveCount(0);
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await expect(page.locator("#tour-title")).toBeFocused();
});

test("hands-on tutorial reaches a dry-run plan with every step anchored and no side effects", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  for (const [index, step] of tutorialSteps.entries()) {
    await expectStep(page, step.id);
    await expect(popover(page)).toContainText(`${index + 1} / ${tutorialSteps.length}`);
    if (step.id === "data-preview") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await tourButton(page, "연습 샘플 채우기").click();
      await expect(page.getByLabel("데이터 ID", { exact: true })).toHaveValue("tutorial_sample");
      await expect(page.locator('#dataset-form input[name="synthetic"]')).toBeChecked();
      await expect(page.locator('#dataset-form input[name="export_allowed"]')).not.toBeChecked();
      await page.getByRole("button", { name: "샘플 미리보기" }).click();
    }
    if (step.id === "data-register") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByRole("button", { name: "검사하고 등록" }).click();
    }
    if (step.id === "training-dataset") {
      await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("paper_extraction");
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByLabel("학습 데이터", { exact: true }).selectOption("tutorial_sample:1");
    }
    if (step.id === "training-plan") {
      await expect(tourButton(page, "다음")).toBeDisabled();
      await page.getByRole("button", { name: "실행 계획 확인" }).click();
    }
    await expect(page.locator(`#app [data-tour="${step.anchor}"]`)).toHaveCount(1);
    await expect(popover(page)).not.toHaveClass(/centered/);
    if (step.kind === "practice") await expect(popover(page)).toContainText("완료했습니다");
    await tourButton(page, index === tutorialSteps.length - 1 ? "완료" : "다음").click();
  }
  await expect(popover(page)).toHaveCount(0);
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
  const calls = await page.evaluate(() => (window as any).__testCalls);
  expect(calls.filter((c: any) => c.command === "factory_action" && forbidden.includes(c.args.action))).toEqual([]);
  expect(calls.map((c: any) => c.command)).not.toContain("settings_save");
  expect(calls.map((c: any) => c.command)).not.toContain("forget_credentials");
});

test("ending the tutorial restores the previous training draft", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await page.getByLabel("업무 AI ID", { exact: true }).fill("my_draft");
  await page.getByRole("button", { name: "튜토리얼", exact: true }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "training-dataset");
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("paper_extraction");
  await page.keyboard.press("Escape");
  await expect(popover(page)).toHaveCount(0);
  await page.getByRole("button", { name: "학습 설정", exact: true }).click();
  await expect(page.getByLabel("업무 AI ID", { exact: true })).toHaveValue("my_draft");
  await page.getByRole("button", { name: "작업 공간", exact: true }).click();
  await expect(page.getByRole("region", { name: "튜토리얼 안내" })).toHaveCount(0);
});

test("practice steps can be skipped and a missing anchor centers the explanation", async ({ page }) => {
  await boot(page);
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-preview");
  await expect(tourButton(page, "다음")).toBeDisabled();
  await expect(tourButton(page, "건너뛰기")).toBeEnabled();
  await advanceTo(page, "plan-reading");
  await expect(popover(page)).toHaveClass(/centered/);
  await expect(popover(page)).toContainText("이 단계의 화면 요소를 찾지 못했습니다");
  await expect(tourButton(page, "다음")).toBeEnabled();
  await page.getByRole("button", { name: "실행 관리", exact: true }).click();
  await expect(popover(page)).toContainText("다른 화면을 보고 있습니다");
  await tourButton(page, "이 단계로 이동").click();
  await expect(page.getByRole("heading", { level: 1 })).toHaveText("학습 설정");
});

test("an already registered practice sample completes the registration step", async ({ page }) => {
  await boot(page, { registered: true });
  await page.getByRole("button", { name: "튜토리얼 시작" }).click();
  await expectStep(page, "welcome");
  await advanceTo(page, "data-register");
  await expect(tourButton(page, "다음")).toBeEnabled();
  await expect(tourButton(page, "건너뛰기")).toHaveCount(0);
  await expect(popover(page)).toContainText("완료했습니다");
});

test("Ctrl+K starts the tutorial and each step moves focus to its title", async ({ page }) => {
  await boot(page, { seen: true });
  await page.keyboard.press("Control+K");
  await page.getByLabel("페이지 검색").fill("튜토리얼");
  await page.keyboard.press("Enter");
  await expectStep(page, "welcome");
  await expect(page.locator("#tour-title")).toBeFocused();
  await page.keyboard.press("Tab");
  await expect(tourButton(page, "그만하기")).toBeFocused();
  await tourButton(page, "다음").click();
  await expectStep(page, "connection-provider");
  await expect(page.locator("#tour-title")).toBeFocused();
  await expect(page.getByRole("dialog", { name: "GPU 연결 방식 고르기" })).toBeVisible();
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd desktop && npx playwright test tests/tutorial.spec.ts`
Expected: FAIL — `../src/tutorial-steps.ts`를 찾을 수 없음.

- [ ] **Step 3: 단계 정의 작성**

`desktop/src/tutorial-steps.ts`:

```ts
type Obj = Record<string, any>;

export const TUTORIAL_DATASET = { id: "tutorial_sample", version: "1" } as const;

export interface TutorialContext {
  state: Obj;
  loadSample(): Promise<Obj>;
  normalizeConfig(): void;
  checkCapabilities(): Promise<unknown>;
  navigate(page: string): Promise<void>;
  finish(): Promise<void>;
}

export interface TutorialStep {
  id: string;
  page: string;
  /** Value of a data-tour attribute rendered on `page`; never an arbitrary selector. */
  anchor: string;
  title: string;
  body: string;
  kind: "explain" | "practice";
  done?(state: Obj, sample: Obj | null): boolean;
  enter?(context: TutorialContext): Promise<void>;
  cta?: { label: string; run(context: TutorialContext): Promise<void> };
}

const practiceData = (id: unknown, version: unknown) => id === TUTORIAL_DATASET.id && version === TUTORIAL_DATASET.version;

export const tutorialSteps: TutorialStep[] = [
  { id: "welcome", page: "overview", anchor: "overview-setup", kind: "explain", title: "작업 공간 둘러보기",
    body: "왼쪽 메뉴에서 데이터 → 학습 설정 → 실행 관리 → 기록 비교 순서로 작업합니다. 오른쪽 ‘시작하기’ 목록은 아직 남은 준비 단계를 보여 줍니다.\n이 튜토리얼은 약 5분 걸리며 GPU를 쓰거나 데이터를 전송하지 않습니다." },
  { id: "connection-provider", page: "connections", anchor: "connection-provider", kind: "explain", title: "GPU 연결 방식 고르기",
    body: "RunPod는 관리자가 배정한 Pod를 RunPod API로 확인한 뒤 SSH로 학습합니다. GPU 서버는 BOP 같은 기관 Linux·NVIDIA 서버에 SSH로 직접 접속해 학습합니다.\n배정받은 자원에 맞는 방식을 고르세요. 앱은 Pod나 서버를 켜거나 끄지 않습니다." },
  { id: "connection-fields", page: "connections", anchor: "connection-fields", kind: "explain", title: "연결에 필요한 정보",
    body: "RunPod는 API 키와 Pod ID, GPU 서버는 주소·포트·계정, SSH 개인키 파일, 작업 폴더, 시간당 비용(내부 서버는 0 가능)이 필요합니다.\n두 방식 모두 SSH 서버 지문은 관리자에게 신뢰할 수 있는 경로로 받은 값만 입력하세요. 인증값은 Windows 자격 증명 저장소에 보관됩니다. 지금 입력하지 않아도 튜토리얼은 계속됩니다." },
  { id: "connection-runtime", page: "connections", anchor: "connection-runtime", kind: "explain", title: "학습 환경 확인과 준비",
    body: "‘환경 확인’은 GPU와 학습 엔진 상태를 조회만 합니다. ‘학습 환경 준비’는 확인 창을 거친 뒤 원격의 앱 전용 폴더에 학습 엔진을 설치하며, 그동안 GPU 요금이 발생할 수 있습니다.\nGPU 서버에서는 조회한 목록에서 사용할 GPU를 골라 고정합니다." },
  { id: "data-preview", page: "datasets", anchor: "dataset-form", kind: "practice", title: "연습 데이터 미리보기",
    body: "‘연습 샘플 채우기’를 누르면 앱에 포함된 합성 예제 8건이 양식에 채워집니다. 이어서 양식의 ‘샘플 미리보기’를 눌러 형식과 건수를 확인하세요.\n미리보기는 이 PC에서만 실행됩니다.",
    cta: { label: "연습 샘플 채우기", async run(context) {
      const sample = await context.loadSample();
      context.state.datasetDraft = { dataset_id: sample.dataset_id, version: sample.version, kind: sample.kind,
        path: sample.path, synthetic: true, export_allowed: false };
      context.state.preview = null;
    } },
    done: (state, sample) => Boolean(sample && state.preview?.rows === sample.rows && state.datasetDraft?.path === sample.path) },
  { id: "data-register", page: "datasets", anchor: "dataset-submit", kind: "practice", title: "연습 데이터 등록",
    body: "‘검사하고 등록’을 눌러 연습 데이터를 이 PC에 등록하세요. 등록한 버전은 바뀌지 않는 사본으로 보관됩니다.\n연습 데이터는 합성 표시가 붙고 GPU 전송이 막혀 있어 실제 학습에는 쓸 수 없습니다. 이미 등록했다면 바로 다음으로 넘어갈 수 있습니다.",
    done: state => (state.catalog?.datasets || []).some((d: Obj) => practiceData(d.dataset_id, d.version)) },
  { id: "training-dataset", page: "training", anchor: "train-dataset", kind: "practice", title: "학습 데이터 선택",
    body: "예제 설정 ‘paper_extraction’을 불러왔습니다. ‘학습 데이터’에서 tutorial_sample · v1을 선택하세요.\n학습 단계의 SFT는 질문·답변 예시로 배우는 지도 학습입니다. 학습 방법의 LoRA는 모델 전체 대신 작은 추가 가중치만 학습해 GPU 메모리를 아끼고, QLoRA는 모델을 4비트로 불러와 메모리를 더 줄입니다.",
    async enter(context) {
      const templates: Obj[] = context.state.catalog?.templates || [];
      const template = templates.find(t => t.name === "paper_extraction") || templates[0];
      if (!template) throw new Error("예제 학습 설정을 찾지 못했습니다.");
      context.state.config = structuredClone(template.config);
      context.normalizeConfig();
      context.state.plan = null; context.state.capabilities = null;
      await context.checkCapabilities();
    },
    done: state => practiceData(state.config?.dataset_id, state.config?.dataset_version) },
  { id: "training-plan", page: "training", anchor: "training-plan", kind: "practice", title: "실행 계획 확인",
    body: "‘실행 계획 확인’을 누르세요. 설정 호환성을 검사한 뒤 확정된 설정과 예상 비용을 계산합니다.\n이 단계는 GPU를 시작하거나 데이터를 보내지 않습니다.",
    done: state => practiceData(state.plan?.manifest?.config?.dataset_id, state.plan?.manifest?.config?.dataset_version) },
  { id: "plan-reading", page: "training", anchor: "plan-summary", kind: "explain", title: "실행 계획 읽는 법",
    body: "실행 전 확인에는 업무 AI, 학습 데이터 건수, 최대 실행 시간 기준 예상 GPU 비용이 나옵니다.\n‘데이터 전송에 동의하고 학습 시작’은 데이터를 GPU로 보내 실제 학습을 시작하는 버튼이며, 누르면 확인 창이 한 번 더 나옵니다. 연습 데이터는 GPU 전송이 허용되지 않아 실제로 실행되지 않습니다." },
  { id: "jobs-history", page: "jobs", anchor: "jobs-list", kind: "explain", title: "실행 관리와 기록 비교",
    body: "실제 학습을 시작하면 이곳에서 상태, 학습 로그, 실제 train loss 진행과 평가 결과를 봅니다. 앱을 다시 열었을 때는 ‘연결 끊긴 작업 확인’과 ‘재연결 · 결과 회수’로 결과를 가져옵니다.\n왼쪽 메뉴의 ‘기록 비교’에서는 공용 MLflow에 저장된 두 실행의 설정과 지표를 나란히 비교합니다." },
  { id: "finish", page: "overview", anchor: "overview-setup", kind: "explain", title: "튜토리얼 완료",
    body: "실제 학습은 ① 연결 설정에서 배정받은 RunPod 또는 GPU 서버 연결 ② 실제 데이터 등록과 GPU 전송 허용 선택 ③ 학습 설정과 실행 계획 확인 순서로 진행합니다.\n등록한 tutorial_sample은 연습용으로 남으며 실제 학습에는 쓸 수 없습니다. 튜토리얼은 왼쪽 메뉴의 ‘튜토리얼’에서 다시 볼 수 있습니다.",
    cta: { label: "연결 설정으로 이동", async run(context) { await context.finish(); await context.navigate("connections"); } } },
];
```

- [ ] **Step 4: 엔진 작성**

`desktop/src/tutorial.ts`:

```ts
import { escapeHtml as esc } from "./workbench";
import { tutorialSteps, type TutorialContext, type TutorialStep } from "./tutorial-steps";

type Obj = Record<string, any>;

export interface TutorialHost {
  state: Obj;
  navigate(page: string): Promise<void>;
  render(): void;
  api(action: string, data?: Obj): Promise<any>;
  checkCapabilities(): Promise<unknown>;
  normalizeConfig(): void;
}

const STORAGE_KEY = "tuning-factory.tutorial.v1";

export function tutorialSeen() {
  try { return localStorage.getItem(STORAGE_KEY) !== null; } catch { return false; }
}

export function rememberTutorial(outcome: "completed" | "dismissed") {
  // Without storage the first-run card simply appears again; the tour itself still works.
  try { localStorage.setItem(STORAGE_KEY, JSON.stringify({ outcome, at: new Date().toISOString() })); } catch { return; }
}

export function createTutorial(host: TutorialHost, steps: TutorialStep[] = tutorialSteps) {
  let index = -1;
  let snapshot: Obj | null = null;
  let sample: Obj | null = null;
  let entered = new Set<string>();
  let focused = -1;
  let scrolled = -1;
  let layer: HTMLDivElement | null = null;

  const context: TutorialContext = {
    state: host.state,
    async loadSample() { sample ??= await host.api("tutorial_sample"); return sample!; },
    normalizeConfig: () => host.normalizeConfig(),
    checkCapabilities: () => host.checkCapabilities(),
    navigate: page => host.navigate(page),
    finish: () => end("completed"),
  };

  async function guarded(work: () => unknown) {
    try { await work(); } catch (error) { host.state.error = String(error); }
  }

  function target(step: TutorialStep) {
    if (host.state.page !== step.page) return null;
    const matches = document.querySelectorAll<HTMLElement>(`#app [data-tour="${step.anchor}"]`);
    return matches.length === 1 ? matches[0] : null;
  }

  async function go(next: number) {
    index = next;
    const step = steps[index];
    host.state.error = "";
    if (host.state.page !== step.page) await host.navigate(step.page);
    if (index !== next) return;
    if (step.enter && !entered.has(step.id)) {
      entered.add(step.id);
      await guarded(() => step.enter!(context));
    }
    host.render();
  }

  async function start() {
    if (index >= 0) return;
    const { config, plan, datasetDraft, preview } = host.state;
    snapshot = structuredClone({ config, plan, datasetDraft, preview });
    entered = new Set(); focused = -1; scrolled = -1;
    await go(0);
  }

  async function end(outcome: "completed" | "dismissed") {
    if (index < 0) return;
    rememberTutorial(outcome);
    index = -1;
    layer?.remove(); layer = null;
    if (snapshot) Object.assign(host.state, snapshot, { capabilities: null });
    snapshot = null;
    host.render();
    if (host.state.config) { await guarded(() => host.checkCapabilities()); host.render(); }
  }

  async function command(name: string) {
    if (index < 0) return;
    const step = steps[index];
    if (name === "stop") return end("dismissed");
    if (name === "back") return go(Math.max(0, index - 1));
    if (name === "goto") { focused = -1; return go(index); }
    if (name === "cta" && step.cta) { await guarded(() => step.cta!.run(context)); host.render(); return; }
    if (name === "next" || name === "skip") return index === steps.length - 1 ? end("completed") : go(index + 1);
  }

  function ensureLayer() {
    if (layer) return layer;
    layer = document.createElement("div");
    layer.id = "tutorial-layer";
    layer.innerHTML = '<div class="tour-ring" hidden></div><div class="tour-popover" role="dialog" aria-modal="false" aria-labelledby="tour-title" aria-describedby="tour-body"></div><div class="sr-only" aria-live="polite"></div>';
    layer.addEventListener("click", event => {
      const button = (event.target as HTMLElement).closest<HTMLButtonElement>("[data-tour-action]");
      if (button && !button.disabled) void command(button.dataset.tourAction!);
    });
    document.body.append(layer);
    return layer;
  }

  function position() {
    if (index < 0 || !layer) return;
    const ring = layer.querySelector<HTMLDivElement>(".tour-ring")!;
    const popover = layer.querySelector<HTMLDivElement>(".tour-popover")!;
    const element = target(steps[index]);
    if (!element) { ring.hidden = true; popover.style.top = popover.style.left = ""; return; }
    const box = element.getBoundingClientRect();
    const margin = 12, width = popover.offsetWidth, height = popover.offsetHeight;
    ring.hidden = false;
    Object.assign(ring.style, { top: `${box.top - 4}px`, left: `${box.left - 4}px`, width: `${box.width + 8}px`, height: `${box.height + 8}px` });
    let top = box.bottom + margin;
    let left = Math.min(Math.max(margin, box.left), window.innerWidth - width - margin);
    if (top + height > window.innerHeight) {
      top = box.top - margin - height;
      // A tall target leaves no room above or below; keep its left-aligned controls uncovered.
      if (top < 0) { top = window.innerHeight - height - margin; left = window.innerWidth - width - margin; }
    }
    popover.style.top = `${top}px`; popover.style.left = `${left}px`;
  }

  function afterRender() {
    if (index < 0) return;
    const step = steps[index];
    const element = target(step);
    const root = ensureLayer();
    const popover = root.querySelector<HTMLDivElement>(".tour-popover")!;
    const active = document.activeElement as HTMLElement | null;
    const refocus = active && popover.contains(active) ? (active.dataset.tourAction ?? "title") : undefined;
    const practice = step.kind === "practice";
    const complete = step.done ? step.done(host.state, sample) : true;
    const busy = Boolean(host.state.busy);
    const away = host.state.page !== step.page;
    const last = index === steps.length - 1;
    const status = busy ? ["", "처리 중입니다. 끝나면 계속 진행할 수 있습니다."]
      : away ? ["", "다른 화면을 보고 있습니다. ‘이 단계로 이동’을 누르세요."]
      : !element ? ["", "이 단계의 화면 요소를 찾지 못했습니다."]
      : practice && complete ? ["done", "완료했습니다. 다음으로 진행하세요."]
      : practice && host.state.error ? ["error", "위의 오류 안내를 확인한 뒤 다시 시도해 보세요."]
      : null;
    popover.classList.toggle("centered", !element);
    popover.innerHTML = `<div class="tour-meta"><span>${index + 1} / ${steps.length}</span><span class="badge">${practice ? "직접 해 보기" : "설명"}</span></div>
      <h2 id="tour-title" tabindex="-1">${esc(step.title)}</h2><p id="tour-body">${esc(step.body)}</p>
      ${status ? `<p class="tour-status ${status[0]}">${status[1]}</p>` : ""}
      <div class="tour-actions"><button type="button" class="quiet" data-tour-action="stop">그만하기</button><span></span>
      ${index > 0 ? '<button type="button" data-tour-action="back">이전</button>' : ""}
      ${away ? '<button type="button" data-tour-action="goto">이 단계로 이동</button>' : ""}
      ${step.cta && !away ? `<button type="button" data-tour-action="cta" ${busy ? "disabled" : ""}>${esc(step.cta.label)}</button>` : ""}
      ${practice && !complete ? `<button type="button" data-tour-action="skip" ${busy ? "disabled" : ""}>건너뛰기</button>` : ""}
      <button type="button" class="primary" data-tour-action="next" ${busy || (practice && !complete) ? "disabled" : ""}>${last ? "완료" : "다음"}</button></div>`;
    root.querySelector(".sr-only")!.textContent = `튜토리얼 ${index + 1}단계: ${step.title}`;
    if (focused !== index || refocus === "title") {
      focused = index;
      popover.querySelector<HTMLElement>("#tour-title")!.focus({ preventScroll: true });
    } else if (refocus) popover.querySelector<HTMLElement>(`[data-tour-action="${refocus}"]`)?.focus({ preventScroll: true });
    if (element && scrolled !== index) {
      scrolled = index;
      element.scrollIntoView({ block: "center", behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth" });
    }
    position();
  }

  window.addEventListener("resize", position);
  window.addEventListener("scroll", position, true);
  window.addEventListener("keydown", event => {
    if (event.key === "Escape" && index >= 0 && !document.querySelector("dialog[open]")) {
      event.preventDefault(); void end("dismissed");
    }
  });

  return { start, end, afterRender, active: () => index >= 0 };
}
```

- [ ] **Step 5: 아이콘 추가**

`desktop/src/workbench.ts`의 `paths`에서 `folder:` 줄 다음에 추가한다:

```ts
  tutorial: '<circle cx="12" cy="12" r="9"/><path d="m15.5 8.5-2.2 4.8-4.8 2.2 2.2-4.8z"/>',
```

- [ ] **Step 6: `main.ts` 연결**

1. 3행 import 다음에 추가한다:

```ts
import { createTutorial, rememberTutorial, tutorialSeen } from "./tutorial";
```

2. `commandSearch()`의 `options.innerHTML = Object.entries(pages)...` 두 줄과 click 처리 줄을 다음으로 바꾼다:

```ts
    options.innerHTML = [...Object.entries(pages), ["tutorial", "튜토리얼"] as [string, string]].filter(([, name]) => name.includes(input.value.trim())).map(([id, name]) =>
      `<button data-command="${id}">${icon(id)}<span>${name}</span>${icon("arrow", 13)}</button>`).join("");
    options.querySelectorAll<HTMLButtonElement>("[data-command]").forEach(el => el.addEventListener("click", () => {
      const id = el.dataset.command!; dialog.close(); dialog.remove(); void action(id === "tutorial" ? "tutorial" : "nav:" + id);
    }));
```

3. `function overview()` 바로 앞에 추가한다:

```ts
function tutorialCard() {
  if (tutorial.active() || tutorialSeen()) return "";
  return `<section class="tutorial-card" aria-label="튜토리얼 안내"><div><h3>처음 사용하시나요?</h3><p>약 5분 실습 튜토리얼로 연결 → 데이터 → 학습 설정 → 실행 계획 순서를 익혀 보세요. GPU를 사용하거나 데이터를 전송하지 않습니다.</p></div><div class="actions">${button("다음에 하기", "tutorial-dismiss")}${button("튜토리얼 시작", "tutorial", "primary")}</div></section>`;
}
```

4. `overview()`에서 `` return `<div class="summary-strip">``를 `` return `${tutorialCard()}<div class="summary-strip">``로, `<div class="workspace-rail"><section class="panel">`를 `<div class="workspace-rail"><section class="panel" data-tour="overview-setup">`로 바꾼다.

5. `datasets()`에서 `<form id="dataset-form">`를 `<form id="dataset-form" data-tour="dataset-form">`로, `<button class="primary" type="submit">검사하고 등록</button>`를 `<button class="primary" type="submit" data-tour="dataset-submit">검사하고 등록</button>`로 바꾼다.

6. `training()`에서 다음 세 곳을 바꾼다:

```ts
  ${select("학습 데이터", "train_dataset", c.dataset_id + ":" + c.dataset_version, datasetOptions).replace('<label class="field">', '<label class="field" data-tour="train-dataset">')}
```

`<button class="primary" type="submit" ${!state.capabilities?.valid`를 `<button class="primary" type="submit" data-tour="training-plan" ${!state.capabilities?.valid`로, `<section class="card plan">`를 `<section class="card plan" data-tour="plan-summary">`로 바꾼다.

7. `jobs()`의 `` return `<section class="card"><div class="section-title"><h3>이 PC의 작업</h3>``를 `` return `<section class="card" data-tour="jobs-list"><div class="section-title"><h3>이 PC의 작업</h3>``로 바꾼다.

8. `render()`의 설정 nav에서 `<span>연결 설정</span></button></nav>`를 다음으로 바꾼다:

```ts
<span>연결 설정</span></button><button aria-label="튜토리얼" data-action="tutorial">${icon("tutorial")}<span>튜토리얼</span></button></nav>
```

같은 함수 끝의 `bind();` 다음 줄에 `tutorial.afterRender();`를 추가한다.

9. `action()`의 `if (name === "search") { commandSearch(); return; }` 다음에 추가한다:

```ts
  if (name === "tutorial") { if (state.busy || !state.config) return; captureConfig(); captureDrafts(); await tutorial.start(); return; }
  if (name === "tutorial-dismiss") { rememberTutorial("dismissed"); render(); return; }
```

10. 파일 아래쪽 최초 `render();` 호출 바로 앞에 추가한다:

```ts
const tutorial = createTutorial({ state, navigate: page => action("nav:" + page), render, api, checkCapabilities, normalizeConfig });
```

- [ ] **Step 7: 스타일 추가**

`desktop/src/style.css` 끝에 한 줄을 추가한다:

```css
.tutorial-card{display:flex;align-items:center;justify-content:space-between;gap:24px;border:1px solid #dfe4f6;background:#f6f7fd;border-radius:8px;padding:18px 20px;margin-bottom:24px}.tutorial-card h3{margin:0 0 6px;font-size:14px}.tutorial-card p{margin:0;color:var(--muted);font-size:12px;line-height:1.7}.tutorial-card .actions{margin:0;flex-shrink:0}.tour-ring{position:fixed;z-index:30;border:2px solid var(--accent);border-radius:8px;pointer-events:none;transition:all .18s ease}.tour-popover{position:fixed;z-index:31;width:340px;background:#fff;border:1px solid var(--line);border-radius:8px;padding:18px 20px;box-shadow:0 8px 28px #171e3b1f;transition:top .18s ease,left .18s ease}.tour-popover.centered{top:50%;left:calc(50% + 108px);transform:translate(-50%,-50%)}.tour-meta{display:flex;justify-content:space-between;align-items:center;font-size:11px;color:var(--muted);margin-bottom:10px}.tour-popover h2{font-size:14px;margin:0 0 8px;outline:none}.tour-popover p{font-size:12px;line-height:1.7;margin:0 0 10px;color:#3c414c;white-space:pre-line}.tour-popover p.tour-status{font-size:11px;color:var(--muted)}.tour-popover p.tour-status.done{color:#2f7a4f}.tour-popover p.tour-status.error{color:#b4413c}.tour-actions{display:flex;gap:6px;align-items:center;margin-top:14px}.tour-actions>span{flex:1}.sr-only{position:absolute;width:1px;height:1px;overflow:hidden;clip:rect(0 0 0 0);white-space:nowrap}@media (prefers-reduced-motion:reduce){.tour-ring,.tour-popover{transition:none}}
```

- [ ] **Step 8: 투어 시험 통과 확인**

Run: `cd desktop && npx playwright test tests/tutorial.spec.ts`
Expected: 6 passed. 말풍선이 클릭 대상 버튼을 가린다는 Playwright 오류가 나면 `position()`의 배치 규칙만 고치고 시험 기대값은 바꾸지 않는다.

- [ ] **Step 9: 전체 UI 회귀와 빌드**

Run: `cd desktop && npx playwright test && npm run build`
Expected: 기존 `workspace.spec.ts`를 포함해 모두 PASS, `tsc --noEmit`과 Vite build 성공.

- [ ] **Step 10: 커밋**

```bash
git add desktop/src/tutorial-steps.ts desktop/src/tutorial.ts desktop/src/workbench.ts desktop/src/main.ts desktop/src/style.css desktop/tests/tutorial.spec.ts
git commit -m "feat: add a hands-on first-run tutorial to the Windows workbench"
```

---

### Task 4: ‘시작하기’ 데이터 등록 판정에서 연습 데이터 제외

**Files:**
- Modify: `desktop/src/main.ts` (`overview()`의 `steps`)
- Test: `desktop/tests/tutorial.spec.ts`

- [ ] **Step 1: 실패 시험 추가**

`desktop/tests/tutorial.spec.ts` 끝에 추가한다:

```ts
test("the practice sample alone does not complete the data checklist item", async ({ page }) => {
  await boot(page, { registered: true, withoutDummy: true, seen: true });
  const row = page.locator(".setup-row", { hasText: "데이터 버전 등록" });
  await expect(row.locator(".setup-check")).not.toHaveClass(/done/);
});
```

- [ ] **Step 2: 실패 확인**

Run: `cd desktop && npx playwright test tests/tutorial.spec.ts -g "data checklist"`
Expected: FAIL — `.setup-check`에 `done` class가 있음.

- [ ] **Step 3: 구현**

`main.ts`의 tutorial import 줄 다음에 추가한다:

```ts
import { TUTORIAL_DATASET } from "./tutorial-steps";
```

`overview()`의 데이터 단계 `done: state.catalog.datasets.length > 0`를 다음으로 바꾼다:

```ts
done: state.catalog.datasets.some((d: Obj) => d.dataset_id !== TUTORIAL_DATASET.id)
```

- [ ] **Step 4: 통과 확인**

Run: `cd desktop && npx playwright test tests/tutorial.spec.ts && npm run build`
Expected: 7 passed, build 성공.

- [ ] **Step 5: 커밋**

```bash
git add desktop/src/main.ts desktop/tests/tutorial.spec.ts
git commit -m "fix: keep the practice sample out of the setup checklist"
```

---

### Task 5: 문서·전체 검증·독립 검토

**Files:**
- Modify: `docs/WINDOWS_APP.md`, `docs/PROGRESS.md`

- [ ] **Step 1: 사용 안내 추가**

`docs/WINDOWS_APP.md`의 `## 처음 사용` 제목 바로 아래, 번호 목록 앞에 추가한다:

```markdown
처음이라면 작업 공간의 **튜토리얼 시작**(또는 왼쪽 메뉴의 **튜토리얼**, Ctrl+K)을 눌러 약 5분 실습 안내를 따라 하세요.
앱에 포함된 합성 예제 `tutorial_sample`을 미리보기·등록하고 실행 계획(dry-run)까지 직접 확인합니다.
연결 정보가 없어도 진행할 수 있으며 GPU 사용·데이터 전송·비용이 없습니다. Esc로 언제든 끝낼 수 있고
투어 전에 작성하던 학습 설정은 끝난 뒤 되돌려 놓습니다. 등록한 연습 데이터는 합성 표시와 GPU 전송 차단이 적용되어
실제 학습에는 쓸 수 없습니다.
```

- [ ] **Step 2: 전체 검증 실행**

```bash
uv sync --extra dev --extra tracking --locked
uv run pytest
uv run ruff check src tests
uv run python scripts/check_secrets.py
cd desktop && npx playwright test && npm run build && cd ..
cargo test --manifest-path desktop/src-tauri/Cargo.toml --locked --offline --release
```

Expected: pytest 전체 통과(기존 opt-in GPU 시험 1개 skip), Ruff 통과, 비밀정보 검사 통과, Playwright 전체 통과, build 성공, Rust 시험 통과. 실패가 있으면 superpowers:systematic-debugging으로 원인을 찾아 고친 뒤 이 단계를 처음부터 다시 실행한다.

- [ ] **Step 3: 진행 기록**

`docs/PROGRESS.md` 맨 위 섹션의 Task 6A/6B 문단 다음에 Step 2 출력의 실제 숫자로 다음 내용을 기록한다:
첫 실행 실습형 튜토리얼(11단계, 화면을 막지 않는 말풍선) 구현; `tutorial_sample` 영속 사본과 예약 ID 보호;
pytest 통과/skip 수와 소요 시간, Ruff, Playwright 통과 수, TypeScript/Vite build, Rust 시험 수;
투어 중 제출·실행·환경 준비·연결 검사·설정 저장 호출 0을 mock 시험으로 확인;
미검증: 설치본 빌드·`verify_desktop_package.py`·실제 WebView2 화면 배치(Task 11), 실제 GPU·SSH 서버 없음.

- [ ] **Step 4: 독립 검토**

superpowers:requesting-code-review로 Task 1–4 커밋 전체를 검토받는다. 검토 초점:
예약 ID 우회(대소문자·경로·S3), onefile 임시 경로, 투어 중 비가역 동작 호출, 상태 복원 누락, `data-tour` 계약, 이스케이프, 포커스.
지적을 superpowers:receiving-code-review로 검증해 반영하고 관련 시험과 Step 2를 다시 실행한다.

- [ ] **Step 5: 커밋**

```bash
git add docs/WINDOWS_APP.md docs/PROGRESS.md
git commit -m "docs: guide first-time users through the desktop tutorial"
```

---

## Self-review

- Spec 대응: 사용자 흐름 표 → Task 3 Step 3; 시작/종료·복원 → Task 3 Step 4·6; 영속 사본·예약 ID → Task 1;
  allowlist·자산·설치본 확인 → Task 2; 오류·안전·접근성 → Task 3 엔진과 시험; 체크리스트 제외 → Task 4;
  문서·PROGRESS·검증 → Task 5. 연결 단계 표시는 Task 6B 요구사항이며 Task 0이 확인한다.
- 이름 일치: `tutorialSteps`, `TUTORIAL_DATASET`, `TutorialContext`, `TutorialStep`, `createTutorial`, `rememberTutorial`,
  `tutorialSeen`, `afterRender`, `active`, `tutorial_sample`, `TUTORIAL_REFUSAL`, `reserved_tutorial_id`, `check_tutorial_registration`.
- 실행 시 실제 측정값으로 채우는 곳은 Task 5 Step 3의 검증 숫자뿐이다.
