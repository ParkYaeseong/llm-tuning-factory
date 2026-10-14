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
    def request(action, data=None):
        value = {"root": temporary + "/state", "action": action, "settings": {}, "data": data or {}}
        run = subprocess.run([str(root / "dist/factory-bridge.exe")], input=json.dumps(value),
                             text=True, encoding="utf-8", capture_output=True, timeout=60, cwd=temporary)
        result = json.loads(run.stdout)
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
    wheel = next((root / "build/desktop-assets/dist").glob("*.whl"))
    with zipfile.ZipFile(wheel) as archive:
        for source in (root / "src/tuning_factory").glob("*.py"):
            assert archive.read("tuning_factory/" + source.name) == source.read_bytes(), source.name
    print(json.dumps({"frozen_engine": True, "offline_dry_run": True, "source_and_worker_match": True,
                      "locked_dependencies": True, "source_commit": plan["manifest"]["code_commit"],
                      "source_sha256": source_hash(), "gpu_started": False}))
