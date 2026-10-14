"""Explicitly owned Pod lifecycle. Persistent network volumes survive stop/delete."""
import json
import re
import sqlite3
import time
import uuid
from pathlib import Path

from .registry import canonical, immutable_write


class ManagedPods:
    def __init__(self, root, controller, policy):
        self.root = Path(root) / "managed-pods"
        self.controller = controller
        self.policy = policy

    def record(self, pod_id):
        pod_id = self.controller.identifier(pod_id) if hasattr(self.controller, "identifier") else pod_id
        if not re.fullmatch(r"[a-zA-Z0-9_-]+", pod_id):
            raise ValueError("invalid Pod ID")
        path = self.root / f"{pod_id}.json"
        if not path.is_file():
            raise ValueError("Pod is not factory-owned")
        return json.loads(path.read_text())

    def create(self, payload, hourly_quote, timeout_seconds, approved=False):
        self.root.mkdir(parents=True, exist_ok=True)
        # Serialize allocation decisions across concurrent control-plane processes.
        with sqlite3.connect(self.root / "allocation-lock.db", timeout=60) as db:
            db.execute("CREATE TABLE IF NOT EXISTS allocation_lock (id INTEGER)")
            db.execute("BEGIN IMMEDIATE")
            return self._create(payload, hourly_quote, timeout_seconds, approved)

    def _create(self, payload, hourly_quote, timeout_seconds, approved):
        if not approved:
            raise ValueError("explicit paid creation approval required")
        count = payload.get("gpuCount", 1)
        if not isinstance(count, int) or not 1 <= count <= self.policy.max_gpus:
            raise ValueError("GPU count exceeds policy")
        if not 1 <= timeout_seconds <= self.policy.max_timeout_seconds:
            raise ValueError("runtime exceeds policy")
        if hourly_quote <= 0 or hourly_quote * timeout_seconds / 3600 > self.policy.max_cost_usd:
            raise ValueError("estimated cost exceeds policy or price missing")
        if not payload.get("networkVolumeId"):
            raise ValueError("network volume required")
        if not re.search(r"@sha256:[a-f0-9]{64}$", payload.get("imageName", "")):
            raise ValueError("managed image must be pinned by registry digest")
        existing_gpus = 0
        known_requests = {json.loads(p.read_text())["request_id"] for p in self.root.glob("*.json")}
        if any(p.stem not in known_requests for p in (self.root / "requests").glob("*.json")):
            raise ValueError("previous creation outcome unknown; reconcile the factory request before another allocation")
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            pod = self.controller.status(record["pod_id"])
            if pod.get("desiredStatus") == "RUNNING":
                existing_gpus += record["gpu_count"]
        if existing_gpus + count > self.policy.max_gpus:
            raise ValueError("total owned GPU count exceeds policy")
        # A unique allocation request is persisted BEFORE the non-idempotent provider call.
        # Unknown network outcomes require manual reconciliation, never an automatic POST retry.
        request_id = uuid.uuid4().hex
        request = {"request_id": request_id, "deadline": time.time() + timeout_seconds,
                   "network_volume_id": payload["networkVolumeId"], "gpu_count": count,
                   "hourly_quote_usd": hourly_quote}
        immutable_write(self.root / "requests" / f"{request_id}.json", canonical(request))
        pod = self.controller.create(payload | {"name": "factory-" + request_id}, approved=True)
        record = request | {"pod_id": pod["id"], "ownership": "created-by-factory"}
        immutable_write(self.root / f"{pod['id']}.json", canonical(record))
        live = self.controller.status(pod["id"])
        if "costPerHr" not in live or float(live["costPerHr"]) > hourly_quote:
            self.stop(pod["id"])
            raise ValueError("live Pod cost missing or exceeds approved quote; Pod stopped")
        return record

    def stop(self, pod_id):
        record = self.record(pod_id)
        live = self.controller.status(pod_id)
        volume_id = live.get("networkVolume", {}).get("id") or live.get("networkVolumeId")
        if volume_id != record["network_volume_id"]:
            raise ValueError("persistent network volume could not be verified; reconcile manually")
        # Stop preserves the verified network volume, including unfinished checkpoints.
        self.controller.stop(pod_id, owned=True, persisted=True)

    def guard_once(self):
        stopped = []
        for path in self.root.glob("*.json"):
            record = json.loads(path.read_text())
            live = self.controller.status(record["pod_id"])
            if live.get("desiredStatus") != "RUNNING":
                continue
            if (time.time() >= record["deadline"] or "costPerHr" not in live
                    or float(live["costPerHr"]) > record["hourly_quote_usd"]):
                self.stop(record["pod_id"])
                stopped.append(record["pod_id"])
        return stopped
