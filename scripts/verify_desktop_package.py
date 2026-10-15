"""Offline acceptance of the frozen engine and matching worker wheel."""
import json
import subprocess
import tempfile
import zipfile
from pathlib import Path

from tuning_factory.manifest import source_hash
from tuning_factory.registry import digest

root = Path(__file__).resolve().parents[1]
with tempfile.TemporaryDirectory(prefix="factory-package-") as temporary:
    def request(action, data=None, settings=None, expected_error=None):
        value = {"root": temporary + "/state", "action": action, "settings": settings or {}, "data": data or {}}
        run = subprocess.run([str(root / "dist/factory-bridge.exe")], input=json.dumps(value),
                             text=True, encoding="utf-8", capture_output=True, timeout=60, cwd=temporary)
        result = json.loads(run.stdout)
        if expected_error:
            assert not result["ok"] and expected_error in result["error"], result
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
        (root / "containers/gpu-requirements.lock").read_bytes())
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
                (root / "containers/gpu-ppo-requirements.lock").read_bytes())
    # The invalid bucket is rejected locally after boto's dynamic S3 service data loads.
    # No endpoint request, user credential or external dataset is involved.
    request("s3_list", {"bucket": "invalid/bucket"}, settings={
        "S3_ACCESS_KEY_ID": "frozen-offline-access", "S3_SECRET_ACCESS_KEY": "frozen-offline-secret",
        "S3_ENDPOINT_URL": "https://s3.invalid", "S3_REGION": "us-east-1",
    }, expected_error="exact S3 bucket")
    wheel = next((root / "build/desktop-assets/dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for source in (root / "src/tuning_factory").glob("*.py"):
            assert archive.read("tuning_factory/" + source.name) == source.read_bytes(), source.name
    print(json.dumps({"frozen_engine": True, "offline_dry_run": True, "source_and_worker_match": True,
                      "locked_dependencies": True, "all_stage_plans": True, "frozen_s3_sdk_loaded": True,
                      "source_commit": plan["manifest"]["code_commit"],
                      "source_sha256": source_hash(), "gpu_started": False}))
