import json
import time

import pytest

from tuning_factory.execution import Policy
from tuning_factory.managed import ManagedPods


class FakeController:
    def __init__(self):
        self.pods = {}
        self.stopped = []

    def create(self, payload, approved=False):
        assert approved
        self.pods["managed1"] = {"id": "managed1", "networkVolumeId": "volume1",
                                 "desiredStatus": "RUNNING", "costPerHr": 0.5}
        return self.pods["managed1"]

    def status(self, pod_id):
        return self.pods[pod_id]

    def stop(self, pod_id, owned=False, persisted=False):
        assert owned and persisted
        self.stopped.append(pod_id)
        self.pods[pod_id]["desiredStatus"] = "EXITED"


def test_managed_creation_budget_watchdog_and_borrowed_guard(tmp_path):
    controller = FakeController()
    manager = ManagedPods(tmp_path, controller, Policy())
    payload = {"gpuCount": 1, "networkVolumeId": "volume1", "imageName": "example@sha256:" + "a" * 64}
    with pytest.raises(ValueError, match="approval"):
        manager.create(payload, 0.5, 60, approved=False)
    with pytest.raises(ValueError, match="cost"):
        manager.create(payload, 100, 3600, approved=True)
    manager.create(payload, 0.5, 60, approved=True)
    record_path = tmp_path / "managed-pods" / "managed1.json"
    record = json.loads(record_path.read_text())
    record["deadline"] = time.time() - 1
    record_path.write_text(json.dumps(record))
    assert manager.guard_once() == ["managed1"]
    assert controller.stopped == ["managed1"]
    with pytest.raises(ValueError, match="owned"):
        manager.stop("borrowed")
