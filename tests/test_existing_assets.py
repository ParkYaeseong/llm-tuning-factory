import json

import httpx
import pytest

from tuning_factory.auth import OIDCTokenRefresher
from tuning_factory.config import load_config
from tuning_factory.evaluation import CommonEvaluator
from tuning_factory.registry import Registry
from tuning_factory.training import LlamaFactoryBackend


def test_existing_sharegpt_jsonl_contract_without_conversion(tmp_path):
    source = tmp_path / "sharegpt.jsonl"
    rows = [{"paper_id": f"dummy-{i}", "messages": [
        {"role": "system", "content": "Synthetic test only"},
        {"role": "user", "content": "Return fixture"},
        {"role": "assistant", "content": "fixture"}]} for i in range(2)]
    source.write_text("\n".join(json.dumps(row) for row in rows), encoding="utf-8")
    registry = Registry(tmp_path / "registry")
    record = registry.register_dataset("legacy", "1", source)
    assert record["format"] == "sharegpt"
    assert record["file_format"] == "jsonl"
    assert registry.dataset_path(record).read_bytes() == source.read_bytes()
    directory = tmp_path / "job"
    LlamaFactoryBackend().prepare(load_config("configs/ai/paper_extraction.yaml"), directory, source.read_bytes())
    info = json.loads((directory / "data" / "dataset_info.json").read_text())["factory_dataset"]
    assert info["file_name"] == "dataset.jsonl"
    assert info["formatting"] == "sharegpt"
    assert info["tags"]["assistant_tag"] == "assistant"
    assert (directory / "data" / "dataset.jsonl").read_bytes() == source.read_bytes()


def test_keycloak_token_refresh_contract(monkeypatch):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"access_token": "fixture-token", "expires_in": 300})

    refresher = OIDCTokenRefresher("https://sso.example/token", "mlflow", "fixture-secret",
                                 client=httpx.Client(transport=httpx.MockTransport(handler)))
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN", raising=False)
    refresher.refresh()
    import os
    assert os.environ["MLFLOW_TRACKING_TOKEN"] == "fixture-token"
    assert b"grant_type=client_credentials" in requests[0].content
    assert b"client_secret=fixture-secret" in requests[0].content
    monkeypatch.delenv("MLFLOW_TRACKING_TOKEN")


def test_reused_kbf_partial_credit_is_explicitly_distinct_from_json_validity():
    ev = CommonEvaluator()
    reference = '{"name":"Sample","catalog_id":"UO1"}'
    truncated = '{"name":"Sample","catalog_id":"UO1"'
    result = ev.evaluate([truncated], [reference], "kbf_extraction")
    assert result["mean_name_f1"] == 1
    assert result["mean_id_f1"] == 1
    assert result["json_validity"] == 0
    with pytest.raises(ValueError, match="ground truth"):
        ev.evaluate([truncated], None, "kbf_extraction")
