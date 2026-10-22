"""Task 11.5 (R4 M1, M11): ``scripts/verify_desktop_package.py`` in source mode, end to end.

The verifier runs as a subprocess on ``scripts/desktop_entry.py`` with this interpreter ("source" mode): every check
except wheel (legacy, arguments, workflow, controller). TEMP, TMP, TMPDIR, HOME and USERPROFILE point into
``tmp_path`` and credential variables are dropped, so every state is temporary. A ``sitecustomize`` guard on
PYTHONPATH, inherited by the verifier and every engine and controller child it starts, records and refuses any
non-loopback connection or name lookup. A source-mode receipt reports no frozen evidence (null), while a frozen
bridge still reports it as true.

The run takes about 40 s (slow marker). The verifier accepts the Windows desktop sidecar and has only been run on
Windows, so elsewhere it is skipped. No GPU, SSH, network, model download, MLflow write or provider call.
"""
import importlib.util
import json
import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from tuning_factory.manifest import source_hash

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "scripts" / "verify_desktop_package.py"
ENTRY = ROOT / "scripts" / "desktop_entry.py"
SRC = ROOT / "src"
CHECKS = ("legacy", "arguments", "workflow", "controller")   # every check except wheel, which needs a build
RUN_TIMEOUT = 900   # seconds: the whole verifier run, generous for a loaded machine (about 40 s when idle)
# Evidence that only a frozen executable can give (R4 M1): null in source mode, true for a frozen bridge.
FROZEN_ONLY = ("frozen_engine", "frozen_s3_sdk_loaded", "frozen_lock_path", "onefile_child_exited")
# Every passed check of legacy, arguments, workflow and controller.
PASSED = ("offline_dry_run", "locked_dependencies", "all_stage_plans", "tutorial_sample_persisted",
          "unsupported_args_exit_2", "workflow_channel", "mapped_import_cp949", "split_committed", "immutable_plan",
          "workflow_run_submitted", "controller_ready", "controller_run_completed", "controller_profile_loaded",
          "controller_stop_drained", "controller_eof_drained", "owner_held_exit_3", "broker_secret_not_written")
# Receipt values that are not a passed check.
DESCRIPTIVE = {"bridge", "bridge_mode", "bridge_sha256", "checks", "source_commit", "source_sha256",
               "engine_child_process", "gpu_started"}
CREDENTIAL_PREFIXES = ("RUNPOD_", "MLFLOW_", "FACTORY_", "S3_", "AWS_", "AZURE_", "HF_", "HUGGING")
GUARD = textwrap.dedent('''\
    """verify_desktop_package test guard (sitecustomize): loopback only; anything else is recorded and refused."""
    import ipaddress
    import os
    import socket

    _LOG = os.environ.get("FACTORY_VERIFY_NETWORK_LOG")
    _connect, _connect_ex, _getaddrinfo = socket.socket.connect, socket.socket.connect_ex, socket.getaddrinfo
    if _LOG:
        with open(_LOG + ".loaded", "a", encoding="utf-8") as stream:
            stream.write(f"{os.getpid()}\\n")


    def _loopback(host):
        if host is None or host in ("", "localhost"):
            return True
        try:
            return ipaddress.ip_address(str(host).split("%")[0]).is_loopback
        except ValueError:
            return False


    def _refuse(what, host):
        if _LOG:
            with open(_LOG, "a", encoding="utf-8") as stream:
                stream.write(f"{os.getpid()} {what} {host!r}\\n")
        raise OSError(f"network disabled in the verifier test: {what} {host!r}")


    def connect(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            _refuse("connect", address[0])
        return _connect(self, address)


    def connect_ex(self, address):
        if self.family in (socket.AF_INET, socket.AF_INET6) and not _loopback(address[0]):
            _refuse("connect_ex", address[0])
        return _connect_ex(self, address)


    def getaddrinfo(host, *args, **kwargs):
        if not _loopback(host.decode() if isinstance(host, bytes) else host):
            _refuse("getaddrinfo", host)
        return _getaddrinfo(host, *args, **kwargs)


    socket.socket.connect = connect
    socket.socket.connect_ex = connect_ex
    socket.getaddrinfo = getaddrinfo
''')


def verifier():
    """The verifier as a module (its import reads only this tree)."""
    specification = importlib.util.spec_from_file_location("verify_desktop_package", SCRIPT)
    module = importlib.util.module_from_spec(specification)
    specification.loader.exec_module(module)
    return module


def test_only_a_frozen_bridge_reports_frozen_evidence(tmp_path):
    module = verifier()
    assert module.frozen_only(module.Bridge(tmp_path / "factory-bridge.exe")) is True
    assert module.frozen_only(module.Bridge(ENTRY)) is None


@pytest.mark.slow
@pytest.mark.skipif(os.name != "nt", reason="verify_desktop_package.py accepts the Windows desktop sidecar and has "
                                              "only been run on Windows")
def test_the_verifier_passes_in_source_mode_offline_and_cleans_up(tmp_path):
    guard, temp, home = tmp_path / "guard", tmp_path / "t", tmp_path / "home"
    for folder in (guard, temp, home):
        folder.mkdir()
    (guard / "sitecustomize.py").write_text(GUARD, encoding="utf-8")
    log, receipt_path = tmp_path / "network.log", tmp_path / "receipt.json"
    env = {name: value for name, value in os.environ.items()
           if not name.upper().startswith(CREDENTIAL_PREFIXES) and name.upper() != "PYTHONPATH"}
    env.update(PYTHONPATH=os.pathsep.join([str(guard), str(SRC)]), FACTORY_VERIFY_NETWORK_LOG=str(log),
               TEMP=str(temp), TMP=str(temp), TMPDIR=str(temp), HOME=str(home), USERPROFILE=str(home),
               PYTHONUTF8="1")
    checks = [argument for name in CHECKS for argument in ("--check", name)]
    run = subprocess.run([sys.executable, str(SCRIPT), "--bridge", str(ENTRY), *checks, "--receipt", str(receipt_path)],
                         cwd=tmp_path, env=env, capture_output=True, text=True, encoding="utf-8",
                         timeout=RUN_TIMEOUT)
    assert run.returncode == 0, (run.stdout[-2000:], run.stderr[-4000:])
    receipt = json.loads(run.stdout.strip().splitlines()[-1])
    assert receipt_path.read_bytes() == (json.dumps(receipt) + "\n").encode("utf-8")

    assert receipt["bridge_mode"] == "source" and Path(receipt["bridge"]) == ENTRY.resolve()
    assert receipt["checks"] == list(CHECKS)
    assert receipt["source_sha256"] == source_hash() and receipt["gpu_started"] is False
    assert {name: receipt[name] for name in FROZEN_ONLY} == dict.fromkeys(FROZEN_ONLY)
    passed = {name: value for name, value in receipt.items() if name not in DESCRIPTIVE and name not in FROZEN_ONLY}
    assert passed == dict.fromkeys(PASSED, True), passed
    assert isinstance(receipt["engine_child_process"], bool)

    assert not log.exists(), log.read_text(encoding="utf-8")
    loaded = (tmp_path / "network.log.loaded").read_text(encoding="utf-8").split()
    assert len(loaded) >= 10, loaded   # the guard ran in the verifier and in the engine and controller children
    assert list(temp.iterdir()) == [], list(temp.iterdir())   # the verifier removed its temporary folder
