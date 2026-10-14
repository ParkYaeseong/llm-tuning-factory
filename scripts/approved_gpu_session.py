"""Explicitly approved, bounded start/stop session for one existing KBF test Pod.

No credentials are copied. A separate local process enforces the approved deadline.
"""
import argparse
import json
import os
import subprocess
import sys
import time
from pathlib import Path

from tuning_factory.connections import load_connection
from tuning_factory.execution import RunPodController


def save(path, data):
    temporary = path.with_suffix(".tmp")
    temporary.write_text(json.dumps(data, indent=2), encoding="utf-8")
    temporary.replace(path)


def stop_session(path):
    data = json.loads(path.read_text())
    if data.get("authorization") != "explicit-user-approved-start-test-stop" or data.get("original_status") != "EXITED":
        raise ValueError("no authorized test-session lifecycle record")
    load_connection(data["connection_file"])
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    pod_id = controller.identifier(data["pod_id"])
    status = controller.status(pod_id)["desiredStatus"]
    if status != "EXITED":
        controller.request("POST", f"/pods/{pod_id}/stop")
    for _ in range(12):
        status = controller.status(pod_id)["desiredStatus"]
        if status == "EXITED":
            data.update(state="STOPPED", stopped_at=time.time(), final_status=status)
            save(path, data)
            return data
        time.sleep(5)
    raise RuntimeError("stop requested but EXITED not yet confirmed")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("action", choices=["start", "watch", "stop"])
    parser.add_argument("--session", type=Path, default=Path(".factory/gpu-session.json"))
    parser.add_argument("--connection", type=Path, default=Path(".factory/connection.json"))
    parser.add_argument("--approve-paid-start-and-stop", action="store_true")
    args = parser.parse_args()
    session = args.session.resolve()
    if args.action == "start":
        if not args.approve_paid_start_and_stop:
            raise ValueError("explicit approval flag required")
        profile = load_connection(args.connection)
        controller = RunPodController(os.environ["RUNPOD_API_KEY"])
        pod = controller.status(profile.pod_id)
        if pod["desiredStatus"] != "EXITED" or float(pod["costPerHr"]) > 5.29:
            raise ValueError("Pod state/price differs from approved stopped $5.29/hour allocation")
        data = {"authorization": "explicit-user-approved-start-test-stop", "pod_id": profile.pod_id,
                "connection_file": str(args.connection.resolve()), "original_status": "EXITED",
                "deadline": time.time() + 1200, "hourly_cost_usd": float(pod["costPerHr"]), "state": "STARTING"}
        session.parent.mkdir(parents=True, exist_ok=True)
        save(session, data)
        flags = subprocess.CREATE_NO_WINDOW | subprocess.DETACHED_PROCESS if os.name == "nt" else 0
        with (session.parent / "gpu-watchdog.log").open("ab") as log:
            watcher = subprocess.Popen([sys.executable, __file__, "watch", "--session", str(session)],
                                       stdout=log, stderr=log, creationflags=flags,
                                       start_new_session=os.name != "nt", cwd=str(Path(__file__).resolve().parents[1]))
        data["watchdog_pid"] = watcher.pid
        save(session, data)
        # Unknown provider outcomes are not automatically retried.
        controller.start(profile.pod_id, approved=True)
        data["state"] = "START_REQUESTED"
        save(session, data)
        print(json.dumps(data, indent=2))
    elif args.action == "watch":
        while True:
            data = json.loads(session.read_text())
            if data.get("state") == "STOPPED":
                return
            if time.time() >= data["deadline"]:
                stop_session(session)
                return
            time.sleep(5)
    else:
        print(json.dumps(stop_session(session), indent=2))


if __name__ == "__main__":
    main()
