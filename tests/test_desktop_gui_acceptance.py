"""``scripts/desktop_gui_acceptance.py`` without launching anything: arguments, the WebView2 environment, the
synthetic dataset, the step log, the cost guard, parsing of the app's notices and the screenshot masks.

The script imports Playwright only inside its run functions, so these tests pass in the torch- and Playwright-free
development environment.
"""
import importlib.util
import json
import re
import sys
from datetime import datetime
from pathlib import Path

import pytest

from tuning_factory.registry import parse_dataset

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "desktop_gui_acceptance.py"


def load():
    spec = importlib.util.spec_from_file_location("desktop_gui_acceptance", SCRIPT)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def gui():
    return load()


def test_the_driver_imports_without_playwright_and_redacts_credentials():
    gui = load()
    assert "playwright" not in sys.modules
    key, pem = "rpa_" + "A1" * 10, "-" * 5 + "BEGIN OPENSSH PRIVATE KEY" + "-" * 5
    cleaned = gui.redact(f"key {key} then {pem} and Bearer abc.def")
    assert key not in cleaned and pem not in cleaned and "abc.def" not in cleaned
    assert gui.redact("학습 환경을 준비했습니다") == "학습 환경을 준비했습니다"
    assert gui.parse_args(["--state-dir", "s", "--dataset", "d.json"]).model == "Qwen/Qwen3.5-0.8B"


def test_redaction_covers_assignments_and_keeps_ordinary_fields(gui):
    hf, aws = "hf_" + "b2" * 12, "AKIA" + "C3D4" * 4
    text = f'HF_TOKEN={hf} {aws} "MLFLOW_OIDC_CLIENT_SECRET": "s3cr3t-value" password: hunter2x'
    cleaned = gui.redact(text)
    for secret in (hf, aws, "s3cr3t-value", "hunter2x"):
        assert secret not in cleaned
    assert "HF_TOKEN=" in cleaned and "password: [REDACTED]" in cleaned
    ordinary = "tokenizer_revision: abc max_new_tokens: 256 train_loss: 1.25"
    assert gui.redact(ordinary) == ordinary
    assert gui.scrub({"notice": f"key {hf}", "rows": [f"Bearer {hf}"], "n": 3}) == {
        "notice": "key [REDACTED]", "rows": ["[REDACTED]"], "n": 3}


def test_arguments_default_to_a_read_only_walk_with_the_planned_limits(gui):
    args = gui.parse_args([])
    assert (args.port, args.timeout, args.max_cost, args.run_seconds, args.poll_seconds) == (9223, 2700, 2.0, 3600, 15)
    assert (args.model, args.stage, args.method, args.dataset_id) == ("Qwen/Qwen3.5-0.8B", "sft", "lora",
                                                                       "gui_accept_synth")
    assert args.app.name == "llm-tuning-factory-desktop.exe" and args.app.parent.name == "LLM Tuning Factory"
    assert args.dataset is None and not args.selftest_ui
    assert args.state_dir.parent.name == "gpu_accept" and re.fullmatch(r"gui-\d{8}-\d{6}", args.state_dir.name)
    assert gui.paid_mode(args) is False
    assert gui.paid_mode(gui.parse_args(["--dry-run"])) is False
    assert gui.paid_mode(gui.parse_args(["--confirm-paid-run"])) is True
    assert gui.paid_mode(gui.parse_args(["--selftest-ui"])) is True        # mocked bridge: no paid work
    assert gui.paid_mode(gui.parse_args(["--selftest-ui", "--dry-run"])) is False
    custom = gui.parse_args(["--state", "out", "--port", "9333", "--max-cost", "1.5", "--timeout", "60"])
    assert (custom.state_dir, custom.port, custom.max_cost, custom.timeout) == (Path("out"), 9333, 1.5, 60)


@pytest.mark.parametrize("argv", [["--dry-run", "--confirm-paid-run"], ["--max-cost", "0"], ["--timeout", "-1"],
                                  ["--port", "80"], ["--ui-url", "http://127.0.0.1:1"]])
def test_conflicting_or_invalid_arguments_are_refused(gui, argv, capsys):
    with pytest.raises(SystemExit) as exited:
        gui.parse_args(argv)
    assert exited.value.code == 2
    capsys.readouterr()


def test_default_state_dir_lives_under_temp_gpu_accept(gui, tmp_path, monkeypatch):
    monkeypatch.setattr("tempfile.tempdir", str(tmp_path))
    assert gui.default_state_dir(datetime(2026, 10, 30, 9, 5, 7)) == tmp_path / "gpu_accept" / "gui-20261030-090507"


def test_the_app_environment_adds_the_devtools_port_without_touching_the_base(gui):
    base = {"PATH": "x", gui.WEBVIEW2_ARGUMENTS: "--disable-gpu --remote-debugging-port=1111"}
    env = gui.app_environment(base, 9223)
    assert env[gui.WEBVIEW2_ARGUMENTS] == "--disable-gpu --remote-debugging-port=9223"
    assert env["PATH"] == "x" and base[gui.WEBVIEW2_ARGUMENTS].endswith("=1111")
    assert gui.app_environment({}, 9300) == {"WEBVIEW2_ADDITIONAL_BROWSER_ARGUMENTS": "--remote-debugging-port=9300"}


def test_a_running_app_is_detected_from_tasklist(gui):
    assert gui.app_running('"llm-tuning-factory-desktop.exe","4242","Console","1","180,000 K"\r\n')
    assert not gui.app_running("INFO: No tasks are running which match the specified criteria.\r\n")


def test_the_synthetic_dataset_is_deterministic_unique_and_registrable(gui):
    first, second = gui.synthetic_dataset_bytes(), gui.synthetic_dataset_bytes()
    assert first == second and first.endswith(b"\n")
    rows = json.loads(first)
    assert len(rows) == 32 and all(set(row) == {"instruction", "output"} for row in rows)
    assert len({row["instruction"] for row in rows}) == 32
    assert all(row["instruction"].strip() and row["output"].strip() for row in rows)
    parsed, formatting, file_format = parse_dataset(first, "instruction")
    assert (len(parsed), formatting, file_format) == (32, "alpaca", "json")


def test_a_given_dataset_is_used_as_is_and_the_default_is_written_to_the_state_dir(gui, tmp_path):
    now = datetime(2026, 10, 30, 12, 0, 1)
    args = gui.parse_args(["--state-dir", str(tmp_path)])
    path, version = gui.prepare_dataset(args, now)
    assert path == (tmp_path / "synthetic-instruction-32.json").resolve() and version == "gui-20261030120001"
    assert path.read_bytes() == gui.synthetic_dataset_bytes()
    own = tmp_path / "own.json"
    own.write_text("[]", encoding="utf-8")
    assert gui.prepare_dataset(gui.parse_args(["--state-dir", str(tmp_path), "--dataset", str(own)]), now)[0] == own
    with pytest.raises(gui.SetupError, match="dataset file not found"):
        gui.prepare_dataset(gui.parse_args(["--state-dir", str(tmp_path), "--dataset", str(tmp_path / "x")]), now)


def test_the_step_log_numbers_screenshots_and_writes_redacted_json_lines(gui, tmp_path):
    echoed = []
    log = gui.StepLog(tmp_path / "state", echo=echoed.append)
    first = log.screenshot_path("page-connections")
    second = log.screenshot_path("job-학습 중")
    assert (first.name, second.name) == ("01-page-connections.png", "02-job.png")
    secret = "rpa_" + "Z9" * 12
    log.write("connection-check", True, screenshot=first, notice=f"saved {secret}", result={"Pod 상태": "RUNNING"})
    log.write("plan", False, message="above --max-cost")
    lines = (tmp_path / "state" / gui.LOG_NAME).read_text(encoding="utf-8").splitlines()
    entries = [json.loads(line) for line in lines]
    assert [entry["step"] for entry in entries] == ["connection-check", "plan"]
    assert entries[0]["ok"] is True and entries[0]["screenshot"] == "01-page-connections.png"
    assert entries[0]["notice"] == "saved [REDACTED]" and entries[0]["result"] == {"Pod 상태": "RUNNING"}
    assert entries[1]["ok"] is False and entries[1]["message"] == "above --max-cost" and "screenshot" not in entries[1]
    assert echoed == lines and secret not in "".join(lines) and log.entries == entries
    assert datetime.fromisoformat(entries[0]["at"]).tzinfo is not None


@pytest.mark.parametrize(("text", "cost"), [("$0.59", 0.59), ("$1,234.5", 1234.5), ("$ 2", 2.0), ("$—", None),
                                            ("", None), ("예상 $0.00001", 0.00001)])
def test_the_plan_cost_text_is_parsed_like_the_screen_formats_it(gui, text, cost):
    assert gui.parse_usd(text) == cost


def test_the_cost_guard_refuses_unknown_or_excessive_estimates(gui):
    assert gui.cost_guard("$0.59", 2.0) == (True, 0.59, "")
    assert gui.cost_guard("$2", 2.0) == (True, 2.0, "")
    allowed, cost, reason = gui.cost_guard("$2.01", 2.0)
    assert not allowed and cost == 2.01 and "above --max-cost $2" in reason
    allowed, cost, reason = gui.cost_guard("$—", 2.0)
    assert not allowed and cost is None and "could not read" in reason


def test_the_runtime_notice_yields_the_state_and_observed_gpu(gui):
    missing = ("선택한 Pod에 이 앱 버전의 학습 환경이 아직 없습니다. '학습 환경 준비'를 누르거나 학습 실행 승인에서 함께 "
               "준비하세요.\nGPU: NVIDIA A40 · compute capability 8.6 · 45.0GiB · 드라이버 550.127.05")
    assert gui.runtime_facts(missing) == {"runtime_state": "missing", "gpu": "NVIDIA A40", "capability": "8.6",
                                          "memory_gib": 45.0, "driver": "550.127.05"}
    ready = "선택한 Pod에서 앱과 일치하는 학습 환경과 CUDA를 확인했습니다."
    assert gui.runtime_facts(ready) == {"runtime_state": "ready"}
    assert gui.runtime_facts("이 GPU에서 앱 학습 환경의 CUDA 연산이 실행되지 않습니다.")["runtime_state"] == "incompatible"
    assert gui.runtime_facts("GPU를 사용할 수 없거나 사용 중입니다.")["runtime_state"] == "unavailable"
    assert gui.runtime_facts("")["runtime_state"] == "unknown"


def test_the_execute_notice_yields_the_cleanup_result(gui):
    notice = ("학습 처리가 끝났습니다. 상태와 평가 결과를 확인하세요.\n이전 앱 버전의 학습 환경 2개를 지워 3.5GB를 "
              "확보했습니다.\n남은 이전 학습 환경은 다음 학습 환경 준비 때 정리합니다.")
    assert gui.cleanup_facts(notice) == {"removed": 2, "freed_gb": 3.5, "not_removed": False, "skipped": False,
                                         "incomplete": True}
    small = "이전 앱 버전의 학습 환경 1개를 지웠습니다.\n일부 이전 학습 환경은 지우지 못했습니다(학습 환경 준비에는 영향이 없습니다)."
    assert gui.cleanup_facts(small) == {"removed": 1, "freed_gb": None, "not_removed": True, "skipped": False,
                                        "incomplete": False}
    assert gui.cleanup_facts("학습 처리가 끝났습니다.")["removed"] == 0


def test_train_loss_is_read_from_trainer_output(gui):
    log = "{'loss': 2.5, 'step': 1}\n***** train metrics *****\n  train_loss = 1.75\n{'train_loss': 1.2345, 'x': 1}\n"
    assert gui.parse_train_loss(log) == 1.2345
    assert gui.parse_train_loss('"train_loss": 3e-1') == 0.3
    assert gui.parse_train_loss("로그를 선택하세요.") is None


def test_status_names_cover_the_app_badges_and_their_terminal_states(gui):
    main = (ROOT / "desktop" / "src" / "main.ts").read_text(encoding="utf-8")
    names = set(re.findall(r'[A-Z]+: "([^"]+)"', main.split("const statusName", 1)[1].split(";", 1)[0]))
    assert names == set(gui.STATUS_SLUGS)
    assert gui.FINISHED == {"완료", "실패", "취소"} <= names


def test_screenshots_mask_every_credential_field_of_the_screen(gui):
    assert 'input[type="password"]' in gui.MASK_SELECTORS
    sources = "".join(path.read_text(encoding="utf-8") for path in (ROOT / "desktop" / "src").glob("*.ts"))
    password_keys = set(re.findall(
        r'field\((?:tr\()?"[^"]*"\)?, "([A-Za-z0-9_]+)",[^\n]*?"password"', sources
    ))
    assert {"RUNPOD_API_KEY", "MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN", "S3_SECRET_ACCESS_KEY",
            "HF_TOKEN"} <= password_keys

    def masked(key):
        for selector in gui.MASK_SELECTORS:
            match = re.fullmatch(r'\[data-key(\*|\$)?="([^"]+)"\]', selector)
            if match and {"*": match[2] in key, "$": key.endswith(match[2]), None: key == match[2]}[match[1]]:
                return True
        return False

    assert all(masked(key) for key in password_keys | {"ssh_key_file", "server_ssh_key_file"})


def test_the_selftest_bridge_mocks_tauri_with_the_desktop_test_config(gui):
    script = gui.selftest_bridge()
    assert "__CONFIG__" not in script and "window.__TAURI_INTERNALS__" in script
    config = json.loads((ROOT / "desktop" / "tests" / "config.json").read_text(encoding="utf-8"))
    assert json.dumps(config) in script and '"Qwen/Qwen3.5-0.8B"' in script
    assert gui.PREPARE_NOTICE in (ROOT / "desktop" / "src" / "gpu-preflight.ts").read_text(encoding="utf-8")
