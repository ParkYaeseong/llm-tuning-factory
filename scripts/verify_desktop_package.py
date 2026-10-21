"""Offline acceptance of the frozen engine (the desktop sidecar) and the matching worker wheel (Task 11 plan 11.3b).

usage: verify_desktop_package.py [--bridge PATH] [--check NAME]... [--receipt PATH]
  --bridge   the engine to check: default dist/factory-bridge.exe; also the installed app's factory-bridge.exe.
             A .py path (scripts/desktop_entry.py) runs the source entry with this interpreter ("source" mode), so
             every check except wheel can run before a build; the receipt then says bridge_mode "source".
  --check    legacy | arguments | workflow | controller | wheel   (repeatable; default: all, in this order).
             controller needs the run that workflow submits, so it runs workflow first when that is not selected.
  --receipt  also write the printed JSON receipt to PATH (e.g. .factory/frozen-040-receipt.json)

Nothing here contacts a network destination, starts a GPU or reads a real credential: every state is a temporary
directory, the workflow plan is a simulation on the synthetic seeded root (scripts/seed_acceptance_root.py), and the
controller's one RunPod profile carries a synthetic key built at runtime that must never be written anywhere.
"""
import argparse
import contextlib
import importlib.util
import json
import os
import queue
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from pathlib import Path

from tuning_factory.desktop import TUTORIAL_REFUSAL
from tuning_factory.manifest import source_hash
from tuning_factory.registry import digest
from tuning_factory.workflow_planning import stored_plan

ROOT = Path(__file__).resolve().parents[1]
CHECKS = ("legacy", "arguments", "workflow", "controller", "wheel")
UNSUPPORTED_ARGUMENTS = "unsupported sidecar arguments"   # = scripts/desktop_entry.py
TIMEOUT = 60                # seconds: an argument refusal, a controller line, a drained exit
REQUEST_TIMEOUT = 120       # seconds: one request to a fresh (onefile) engine
RUN_TIMEOUT = 180           # seconds: the simulated run under the controller
FAST = {"lease_ttl_seconds": 6, "tick_seconds": 0.2, "lease_heartbeat_seconds": 1.5}
ROLES = ("train", "validation", "test")
PROFILE_ID = "verify-pod"


class Bridge:
    """The engine under test: a frozen executable, or the source entry run by this interpreter."""

    def __init__(self, path: Path):
        self.path = Path(path).resolve()
        self.source = self.path.suffix == ".py"
        self.outputs: list[str] = []   # every one-request stdout and stderr, for the secret check

    def command(self, *args: str) -> list[str]:
        return [sys.executable, str(self.path), *args] if self.source else [str(self.path), *args]

    def request(self, envelope: dict, cwd: Path, timeout: float = REQUEST_TIMEOUT) -> dict:
        run = subprocess.run(self.command(), input=json.dumps(envelope), text=True, encoding="utf-8",
                             capture_output=True, timeout=timeout, cwd=cwd)
        self.outputs.append(run.stdout + run.stderr)
        return json.loads(run.stdout)


# -- legacy one-request actions (unchanged checks) -------------------------------------------------

def check_legacy(bridge: Bridge, workdir: Path) -> dict:
    temporary = str(workdir)

    def request(action, data=None, settings=None, expected_error=None):
        value = {"root": temporary + "/state", "action": action, "settings": settings or {}, "data": data or {}}
        result = bridge.request(value, workdir, timeout=TIMEOUT)
        if expected_error:
            assert not result["ok"] and expected_error in result["error"], result
            if expected_error == TUTORIAL_REFUSAL:
                assert result["error"] == TUTORIAL_REFUSAL, result
            return result
        if not result["ok"]:
            raise RuntimeError(result.get("error"))
        return result["result"]

    catalog = request("catalog")
    assert len(catalog["templates"]) == 3
    config = next(x["config"] for x in catalog["templates"] if x["name"] == "paper_extraction")
    assert config["hyperparameters"]["max_steps"] == -1
    path = Path(temporary) / "dummy.json"
    path.write_text('[{"instruction":"synthetic question","output":"synthetic answer"}]')
    request("dataset", {"dataset_id": "dummy", "version": "1", "path": str(path), "synthetic": True})
    plan = request("plan", {"config": config})
    assert plan["dry_run"] and not request("jobs")
    assert plan["manifest"]["source_sha256"] == source_hash()
    assert plan["manifest"]["dependency_file_hashes"]["containers/gpu-requirements.lock"] == digest(
        (ROOT / "containers/gpu-requirements.lock").read_bytes())
    assert {stage["id"] for stage in catalog["training_stages"]} == {"pt", "sft", "rm", "ppo", "dpo", "kto"}
    for stage, kind, row in [
        ("pt", "text", {"text": "synthetic document"}),
        ("rm", "preference", {"instruction": "q", "chosen": "yes", "rejected": "no"}),
        ("dpo", "preference", {"instruction": "q", "chosen": "yes", "rejected": "no"}),
        ("kto", "kto", {"instruction": "q", "output": "a", "kto_tag": True}),
        ("ppo", "prompt", {"instruction": "q"}),
    ]:
        candidate = json.loads(json.dumps(config))
        candidate.update(training_stage=stage, dataset_id=stage)
        if stage == "ppo":
            candidate["stage_options"].update(reward_model="/workspace/prepared-reward", reward_model_sha256="0" * 64)
        path.write_text(json.dumps([row]))
        request("dataset", {"dataset_id": stage, "version": "1", "path": str(path), "kind": kind, "synthetic": True})
        resolved = request("plan", {"config": candidate})
        assert resolved["dry_run"] and resolved["manifest"]["config"]["training_stage"] == stage
        if stage == "ppo":
            assert resolved["manifest"]["training_engine"] == "llamafactory==0.9.3"
            assert resolved["manifest"]["dependency_file_hashes"]["containers/gpu-ppo-requirements.lock"] == digest(
                (ROOT / "containers/gpu-ppo-requirements.lock").read_bytes())
    # The invalid bucket is rejected locally after boto's dynamic S3 service data loads.
    # No endpoint request, user credential or external dataset is involved.
    request("s3_list", {"bucket": "invalid/bucket"}, settings={
        "S3_ACCESS_KEY_ID": "frozen-offline-access", "S3_SECRET_ACCESS_KEY": "frozen-offline-secret",
        "S3_ENDPOINT_URL": "https://s3.invalid", "S3_REGION": "us-east-1",
    }, expected_error="exact S3 bucket")
    # The onefile asset folder is temporary; the returned practice copy must outlive this process.
    tutorial = request("tutorial_sample")
    bundled = (ROOT / "examples/tutorial_sample.json").read_bytes()
    assert tutorial["sha256"] == digest(bundled) and tutorial["rows"] == 8
    assert Path(tutorial["path"]).read_bytes() == bundled
    request("dataset", {"dataset_id": "tutorial_sample", "version": "1", "path": tutorial["path"],
                        "synthetic": True, "export_allowed": True}, expected_error=TUTORIAL_REFUSAL)
    registered = request("dataset", {"dataset_id": "tutorial_sample", "version": "1", "path": tutorial["path"],
                                     "synthetic": True})
    assert registered["export_allowed"] is False and registered["synthetic"] is True and registered["rows"] == 8
    return {"frozen_engine": True, "offline_dry_run": True, "locked_dependencies": True, "all_stage_plans": True,
            "frozen_s3_sdk_loaded": True, "tutorial_sample_persisted": True,
            "source_commit": plan["manifest"]["code_commit"], "source_sha256": source_hash()}


# -- argument refusal ------------------------------------------------------------------------------

def check_arguments(bridge: Bridge, workdir: Path) -> dict:
    """Any argv other than none or exactly --controller: exit 2 and the fixed stderr line, without reading stdin
    (the pipe stays open and empty, so an engine that reads it never exits)."""
    for args in (["--bogus"], ["--controller", "extra"]):
        process = subprocess.Popen(bridge.command(*args), stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                   stderr=subprocess.PIPE, cwd=workdir)
        try:
            code = process.wait(timeout=TIMEOUT)
        except subprocess.TimeoutExpired:
            process.kill()
            process.wait()
            raise AssertionError(f"{args}: still running after {TIMEOUT} s; the engine waits for stdin") from None
        finally:
            with contextlib.suppress(OSError):
                process.stdin.close()
        stdout, stderr = process.stdout.read(), process.stderr.read()
        process.stdout.close()
        process.stderr.close()
        assert code == 2 and stdout == b"" and UNSUPPORTED_ARGUMENTS in stderr.decode("utf-8", "replace"), (
            args, code, stdout, stderr)
    return {"unsupported_args_exit_2": True}


# -- workflow channel ------------------------------------------------------------------------------

def _seed_module():
    spec = importlib.util.spec_from_file_location("seed_acceptance_root", ROOT / "scripts/seed_acceptance_root.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def workflow_caller(bridge: Bridge, workdir: Path, state: Path):
    """One workflow request per engine process, exactly the envelope the app sends; returns the ``ok`` data."""
    def call(action: str, data: dict | None = None, *, status: str = "ok"):
        response = bridge.request({"channel": "workflow", "root": str(state), "settings": {}, "profiles": [],
                                   "secrets": {}, "action": action, "data": data or {}}, workdir)
        assert response.get("ok") is True, (action, response)
        result = response["result"]
        assert result.get("status") == status, (action, result)
        return result["data"]
    return call


def check_workflow(bridge: Bridge, workdir: Path) -> tuple[dict, dict]:
    """A blank project, a CP949 CSV mapped and split, a recipe, the seeded root, a benchmark, train -> evaluate, a
    simulation plan stored by its hash with the bundled GPU lock pinned, and the submitted run."""
    seed = _seed_module()
    state = workdir / "workflow-state"
    call = workflow_caller(bridge, workdir, state)
    created = call("project_create", {"request_id": "verify-a1", "name": "질의응답 실험 01",
                                      "purpose": "문맥으로 답하기", "task_type": "qa"})
    assert created["project"].get("legacy_origin") is None, created
    inbox = workdir / "inbox"
    inbox.mkdir(exist_ok=True)
    csv = inbox / "qa-cp949.csv"
    csv.write_bytes(("question,context,answer\n"
                     + "".join(f"질문 {i},문맥 {i},답 {i}\n" for i in range(1, 13))).encode("cp949"))
    source = call("dataset_import_source", {"path": str(csv)})
    mapping = {"source_format": "csv", "encoding": "cp949",
               "fields": {"instruction": ["question"], "input": ["context"], "output": ["answer"]}}
    shown = call("dataset_import_preview", {"source_id": source["source_id"], "mapping": mapping})
    first = shown["samples"][0]
    assert shown["row_count"] == 12 and (first.get("instruction"), first.get("input"), first.get("output")) == (
        "질문 1", "문맥 1", "답 1"), shown
    registered = call("dataset_import_register", {
        "source_id": source["source_id"], "mapping": mapping, "preview_sha256": shown["preview_sha256"],
        "dataset_id": "verify-qa", "version": "1", "synthetic": True, "export_allowed": False})
    split = call("dataset_split_preview", {"plan": {
        "dataset": registered["dataset"], "seed": 42, "train_basis_points": 8000, "validation_basis_points": 1000,
        "test_basis_points": 1000, "outputs": {role: {"dataset_id": f"verify-qa-{role}", "version": "1"}
                                               for role in ROLES}}})
    committed = call("dataset_split_commit", {"plan_sha256": split["plan_sha256"]})
    assert committed["plan_sha256"] == split["plan_sha256"] and set(committed["outputs"]) == set(ROLES), committed
    bound = call("project_save", {"spec": {**created["project"], "datasets": [
        {"binding_id": role, "role": role, "dataset": committed["outputs"][role]} for role in ROLES]},
        "expected_revision": created["revision_sha256"]})
    recipe = call("recipe_save", {"spec": {"recipe_id": "verify-sft-lora", "name": "SFT LoRA",
                                           "hyperparameters": {"cutoff_len": seed.MAX_LENGTH}},
                                  "expected_revision": None})
    seeded = seed.seed(state)   # in this process, from the source tree: never bundled in the installer
    (root,) = [item for item in call("model_roots_list")["roots"] if item["root"] == seeded["root"]]
    assert root["snapshot_ready"] and root["targets_ready"], root
    benchmark = call("benchmark_save", {"spec": {
        "dataset": committed["outputs"]["validation"], "evaluator_kind": "text", "evaluator_version": "1",
        "ordered_sample_ids": [f"row:{i}" for i in range(split["counts"]["validation"])], "max_new_tokens": 16,
        "cutoff_len": seed.MAX_LENGTH, "seed": 42, "precision": "bf16", "quantization_bit": None,
        "tokenizer_fingerprint": seeded["root"]["tokenizer_fingerprint"], "template": root["profile"]["template"],
        "semantic_model_kind": "causal_lm"}})
    estimate = "estimate-local"
    graph = {"nodes": [
        {"kind": "train", "id": "train", "label": "학습", "recipe_revision": recipe["revision_sha256"],
         "model_input": {"policy": {"base": seeded["root"]}, "initialization": "new_adapter"},
         "dataset_binding": "train", "executor_profile_ids": [estimate]},
        {"kind": "evaluate", "id": "evaluate", "label": "평가", "model": {"node_id": "train", "output_name": "model"},
         "benchmark_revision": benchmark["revision_sha256"], "executor_profile_ids": [estimate]}]}
    project_id = created["project"]["project_id"]
    call("project_save", {"spec": {**bound["project"], "graph": graph}, "expected_revision": bound["revision_sha256"]})
    call("graph_validate", {"project_id": project_id})
    planned = call("graph_plan", {
        "project_id": project_id, "simulation": True,
        "node_resources": [{"node_id": "train", "timeout_seconds": 1800}, {"node_id": "evaluate", "timeout_seconds": 600}],
        "estimates": [{"profile_id": estimate, "gpu_type": "A100", "gpu_count": 1, "total_hourly_cost_usd": 1}]})
    sha = planned["plan_sha256"]
    # A plan pins its estimate's observation time, so a second real-clock graph_plan is another plan: the plan is
    # immutable by its hash instead (graph_plan_get and the stored file, hash-verified on read).
    fetched = call("graph_plan_get", {"plan_sha256": sha})
    assert fetched["plan_sha256"] == sha and fetched["nodes"] == planned["nodes"] and planned["simulation"] is True
    plan = stored_plan(state, sha)
    lock = digest((ROOT / "containers/gpu-requirements.lock").read_bytes())
    pinned = {resource.runtime.dependency_sha256 for entry in plan.nodes for resource in entry.allowed_resources}
    assert pinned == {lock}, (pinned, lock)   # the engine read the GPU lock it bundles (frozen: sys._MEIPASS)
    assert call("graph_runs_list")["runs"] == []
    submitted = call("graph_submit", {"plan_sha256": sha, "request_id": "verify-submit"})
    assert submitted["simulation"] is True and submitted["status"] == "RUNNING", submitted
    return ({"workflow_channel": True, "mapped_import_cp949": True, "split_committed": True, "immutable_plan": True,
             "frozen_lock_path": True, "workflow_run_submitted": True},
            {"state": state, "run_id": submitted["run_id"], "call": call})


# -- background controller -------------------------------------------------------------------------

def alive(pid: int) -> bool:
    """Whether a process runs (never os.kill(pid, 0) on Windows: that terminates the process)."""
    if os.name == "nt":
        import ctypes
        kernel = ctypes.windll.kernel32
        handle = kernel.OpenProcess(0x1000, False, pid)   # PROCESS_QUERY_LIMITED_INFORMATION
        if not handle:
            return False
        try:
            code = ctypes.c_ulong()
            return bool(kernel.GetExitCodeProcess(handle, ctypes.byref(code))) and code.value == 259   # STILL_ACTIVE
        finally:
            kernel.CloseHandle(handle)
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    return True


def gone(pid: int, timeout: float = 15) -> bool:
    deadline = time.monotonic() + timeout
    while alive(pid):
        if time.monotonic() > deadline:
            return False
        time.sleep(0.2)
    return True


class Controller:
    """``bridge --controller`` with its start line written and stdin kept open (the broker's lifeline)."""

    def __init__(self, bridge: Bridge, workdir: Path, line: dict):
        self.process = subprocess.Popen(bridge.command("--controller"), stdin=subprocess.PIPE,
                                        stdout=subprocess.PIPE, stderr=subprocess.PIPE, cwd=workdir)
        self.lines: queue.Queue = queue.Queue()
        self.stdout, self.stderr = bytearray(), bytearray()
        self.readers = [threading.Thread(target=self._read_stdout, daemon=True),
                        threading.Thread(target=self._read_stderr, daemon=True)]
        for reader in self.readers:
            reader.start()
        self.process.stdin.write(json.dumps(line).encode("utf-8") + b"\n")
        self.process.stdin.flush()

    def _read_stdout(self):
        for raw in self.process.stdout:
            self.stdout += raw
            self.lines.put(raw)
        self.lines.put(None)

    def _read_stderr(self):
        for raw in self.process.stderr:
            self.stderr += raw

    def first_line(self) -> dict:
        try:
            raw = self.lines.get(timeout=TIMEOUT)
        except queue.Empty:
            raise AssertionError(f"no controller line within {TIMEOUT} s") from None
        assert raw is not None, ("the controller exited without a line", bytes(self.stderr)[-2000:])
        return json.loads(raw)

    def close_stdin(self):
        with contextlib.suppress(OSError):
            self.process.stdin.close()

    def wait(self) -> int:
        code = self.process.wait(timeout=TIMEOUT)
        for reader in self.readers:
            reader.join(5)
        return code

    def finish(self):
        """Cleanup: EOF (a drain), then a kill if it does not exit."""
        if self.process.poll() is None:
            self.close_stdin()
            try:
                self.process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                self.process.kill()
                self.process.wait()

    def output(self) -> bytes:
        return bytes(self.stdout) + bytes(self.stderr)


def check_controller(bridge: Bridge, workdir: Path, flow: dict) -> dict:
    """Start, run to completion, status with the profile, stop request (drained), EOF through the onefile
    bootloader (drained), owner held (exit 3); the synthetic key is written nowhere."""
    state, call = flow["state"], flow["call"]
    key = "synthetic-" + "runpod-" + "k" * 12 + "-" + str(os.getpid())
    profile = {"profile_id": PROFILE_ID, "secrets": {"RUNPOD_API_KEY": key},
               "fields": {"provider": "existing_runpod", "name": "Verify Pod", "pod_id": "verify-pod-1",
                          "ssh_fingerprint": "SHA256:" + "A" * 43, "ssh_key_file": str(workdir / "keys" / "verify")}}
    started: list[Controller] = []

    def launch() -> tuple[Controller, dict, str]:
        owner = f"verify.{os.getpid()}.{time.time_ns()}"
        controller = Controller(bridge, workdir, {"schema": "factory-controller/1", "state_root": str(state),
                                                  "owner_id": owner, "tracking": {}, "profiles": [profile],
                                                  "settings": FAST})
        started.append(controller)
        return controller, controller.first_line(), owner

    def stop_requested(controller: Controller, owner: str) -> int:
        stop = call("controller_stop")
        assert stop == {"requested": True, "owner_id": owner}, stop
        return controller.wait()

    try:
        first, ready, owner = launch()
        assert ready.get("ok") is True and ready.get("owner_id") == owner and isinstance(ready.get("pid"), int), ready
        deadline = time.monotonic() + RUN_TIMEOUT
        while True:
            run = call("graph_run_get", {"run_id": flow["run_id"]})["run"]
            if run["status"] in {"COMPLETED", "FAILED", "CANCELED"}:
                break
            assert time.monotonic() < deadline, f"the simulated run is still {run['status']} after {RUN_TIMEOUT} s"
            time.sleep(1)
        assert run["status"] == "COMPLETED", run
        status = call("controller_status")
        current = status["current"]
        assert status["controller"]["state"] == "running" and current["owner_id"] == owner, status
        assert PROFILE_ID in current["profiles"] and PROFILE_ID not in current["skipped_profiles"], current
        assert stop_requested(first, owner) == 0, first.output()[-2000:]
        stopped = json.loads((state / "controller" / "current.json").read_bytes())
        assert stopped["owner_id"] == owner and stopped["code"] == "stopped" and "stopped_at" in stopped, stopped
        assert call("controller_status")["controller"]["state"] == "absent"
        pids = [(first.process.pid, ready["pid"])]
        assert gone(ready["pid"]), "the stopped controller's engine process is still running"

        second, ready, owner = launch()
        assert ready.get("ok") is True and ready.get("owner_id") == owner, ready
        second.close_stdin()   # EOF through the onefile bootloader: drain and stop
        assert second.wait() == 0, second.output()[-2000:]
        assert gone(ready["pid"]), "the controller's engine process outlived EOF"
        pids.append((second.process.pid, ready["pid"]))

        third, ready, owner = launch()
        assert ready.get("ok") is True and ready.get("owner_id") == owner, ready
        fourth, refused, _ = launch()
        assert refused == {"ok": False, "code": "owner_held"}, refused
        assert fourth.wait() == 3, fourth.output()[-2000:]
        assert stop_requested(third, owner) == 0, third.output()[-2000:]
        assert gone(ready["pid"])
        pids.append((third.process.pid, ready["pid"]))
    finally:
        for controller in started:
            controller.finish()
    secret = key.encode("utf-8")
    written = [path for path in state.rglob("*") if path.is_file() and secret in path.read_bytes()]
    assert not written, [str(path.relative_to(state)) for path in written]
    assert not any(secret in controller.output() for controller in started), "the key reached controller output"
    assert not any(key in text for text in bridge.outputs), "the key reached a one-request answer"
    return {"controller_ready": True, "controller_run_completed": True, "controller_profile_loaded": True,
            "controller_stop_drained": True, "controller_eof_drained": True, "owner_held_exit_3": True,
            # The engine ran as a child of the started process (the onefile bootloader; in source mode on Windows,
            # the venv launcher), so stop and EOF were checked through that parent.
            "onefile_child_exited": True, "engine_child_process": all(parent != child for parent, child in pids),
            "broker_secret_not_written": True}


# -- worker wheel ----------------------------------------------------------------------------------

def check_wheel() -> dict:
    wheel = next((ROOT / "build/desktop-assets/dist").glob("*.whl"), None)
    assert wheel is not None, "no engine wheel under build/desktop-assets/dist: run scripts/build_desktop.py first"
    with zipfile.ZipFile(wheel) as archive:
        for source in (ROOT / "src/tuning_factory").glob("*.py"):
            assert archive.read("tuning_factory/" + source.name) == source.read_bytes(), source.name
    return {"source_and_worker_match": True}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="verify_desktop_package.py",
                                     description="Offline acceptance of the frozen engine and the worker wheel.")
    parser.add_argument("--bridge", type=Path, default=ROOT / "dist/factory-bridge.exe", metavar="PATH",
                        help="engine to check (default dist/factory-bridge.exe; a .py path runs the source entry)")
    parser.add_argument("--check", action="append", choices=CHECKS, metavar="NAME",
                        help="legacy, arguments, workflow, controller or wheel (repeatable; default: all, in this order)")
    parser.add_argument("--receipt", type=Path, metavar="PATH", help="also write the printed JSON receipt to this file")
    args = parser.parse_args(argv)
    bridge = Bridge(args.bridge)
    if not bridge.path.is_file():
        parser.error(f"no engine at {bridge.path}")
    selected = [name for name in CHECKS if name in (args.check or CHECKS)]
    receipt = {"bridge": str(bridge.path), "bridge_mode": "source" if bridge.source else "frozen",
               "bridge_sha256": digest(bridge.path.read_bytes()), "checks": selected}
    with tempfile.TemporaryDirectory(prefix="factory-package-", ignore_cleanup_errors=True) as temporary:
        workdir, flow = Path(temporary), None
        for name in selected:
            try:
                if name == "legacy":
                    receipt.update(check_legacy(bridge, workdir))
                elif name == "arguments":
                    receipt.update(check_arguments(bridge, workdir))
                elif name in {"workflow", "controller"} and flow is None:
                    part, flow = check_workflow(bridge, workdir)
                    receipt.update(part)
                if name == "controller":
                    receipt.update(check_controller(bridge, workdir, flow))
                elif name == "wheel":
                    receipt.update(check_wheel())
            except Exception:
                print(json.dumps({"failed_check": name}), file=sys.stderr)
                raise
    receipt["gpu_started"] = False
    text = json.dumps(receipt)
    print(text)
    if args.receipt:
        args.receipt.parent.mkdir(parents=True, exist_ok=True)
        args.receipt.write_bytes((text + "\n").encode("utf-8"))   # LF on every platform
    return 0


if __name__ == "__main__":
    sys.exit(main())
