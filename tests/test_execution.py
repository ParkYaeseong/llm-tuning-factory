import httpx
import pytest

from tuning_factory.config import load_config
from tuning_factory.execution import Policy, RunPodController


def test_cost_export_and_paid_approval():
    config = load_config("configs/ai/paper_extraction.yaml")
    policy = Policy()
    assert policy.check(config, {"export_allowed": False}, execute=False) == 0.5
    with pytest.raises(ValueError, match="export"):
        policy.check(config, {"export_allowed": False}, execute=True, approved_transfer=True)
    with pytest.raises(ValueError, match="approval"):
        policy.check(config, {"export_allowed": True}, execute=True)
    policy.check(config, {"export_allowed": True}, execute=True, approved_transfer=True)
    with pytest.raises(ValueError, match="cost"):
        Policy(max_cost_usd=0.1).check(config, {}, execute=False)


def test_mock_runpod_rest_and_ownership():
    requests = []

    def handler(request):
        requests.append(request)
        if request.method == "POST" and request.url.path == "/v1/pods":
            return httpx.Response(200, json={"id": "newpod", "desiredStatus": "RUNNING"})
        return httpx.Response(200, json={"id": "pod1", "desiredStatus": "RUNNING", "costPerHr": 0.5})

    client = httpx.Client(transport=httpx.MockTransport(handler))
    controller = RunPodController("test-secret", client=client)
    assert controller.status("pod1")["desiredStatus"] == "RUNNING"
    assert requests[0].headers["Authorization"] == "Bearer test-secret"
    with pytest.raises(ValueError, match="approval"):
        controller.create({}, approved=False)
    with pytest.raises(ValueError, match="volume"):
        controller.create({}, approved=True)
    pod = controller.create({"networkVolumeId": "v1"}, approved=True)
    assert pod["id"] == "newpod"
    with pytest.raises(ValueError, match="owned"):
        controller.delete("pod1", owned=False, persisted=True)
    with pytest.raises(ValueError, match="persist"):
        controller.delete("newpod", owned=True, persisted=False)
    controller.delete("newpod", owned=True, persisted=True)


def test_capacity_unavailable_is_retryable():
    from tuning_factory.execution import CapacityUnavailable
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(503)))
    with pytest.raises(CapacityUnavailable):
        RunPodController("test", client=client).status("pod1")
