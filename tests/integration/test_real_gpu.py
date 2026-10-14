"""Opt-in paid integration. Requires prior operator authorization, never runs by default."""
import os
import subprocess

import pytest


@pytest.mark.gpu
@pytest.mark.skipif(os.getenv("FACTORY_APPROVED_GPU_TEST") != "yes", reason="no explicit real GPU authorization")
def test_authorized_existing_pod():
    required = ["FACTORY_POD_ID", "FACTORY_SSH_HOST", "FACTORY_SSH_KEY", "RUNPOD_API_KEY"]
    if not all(os.getenv(key) for key in required):
        pytest.fail("authorized GPU test missing required infrastructure environment variables")
    # Operator must first register export-approved data and submit a --real job in an isolated state dir.
    state = os.environ["FACTORY_GPU_TEST_STATE"]
    result = subprocess.run([
        "factory", "--state-dir", state, "work", "--pod-id", os.environ["FACTORY_POD_ID"],
        "--host", os.environ["FACTORY_SSH_HOST"], "--port", os.getenv("FACTORY_SSH_PORT", "22"),
        "--key-file", os.environ["FACTORY_SSH_KEY"], "--approve-execution-and-transfer",
    ], capture_output=True, text=True, timeout=15000, check=False)
    assert result.returncode == 0, result.stderr
    import json
    job = json.loads(result.stdout)
    assert job.get("status") == "COMPLETED", job
    assert job["manifest"]["simulation"] is False
