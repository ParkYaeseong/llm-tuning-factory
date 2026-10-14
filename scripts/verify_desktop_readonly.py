"""Explicit local operator check; reads an existing credential-reference profile, never starts GPUs."""
import argparse
import json
import os
import subprocess
from pathlib import Path

from tuning_factory.connections import ENV_SETTINGS, load_connection

parser = argparse.ArgumentParser()
parser.add_argument("--connection-file", type=Path, required=True)
args = parser.parse_args()
profile = load_connection(args.connection_file)
settings = {key: os.environ[key] for key in ENV_SETTINGS if key in os.environ}
settings["pod_id"] = profile.pod_id
root = Path(__file__).resolve().parents[1]
for action, data in [("history", {"experiment_id": "16"}), ("pod", {})]:
    request = {"root": str(root / "state/desktop-readonly"), "action": action, "data": data, "settings": settings}
    completed = subprocess.run([str(root / "dist/factory-bridge.exe")], input=json.dumps(request),
                               capture_output=True, text=True, encoding="utf-8", timeout=120)
    response = json.loads(completed.stdout)
    if not response["ok"]:
        raise RuntimeError(f"{action}: {response.get('error')}")
    value = response["result"]
    print(json.dumps({"action": action, "ok": True,
                      "run_count": len(value.get("runs", [])) if action == "history" else None,
                      "pod_status": value.get("desiredStatus") if action == "pod" else None}))
