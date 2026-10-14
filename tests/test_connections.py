import json

import pytest

from tuning_factory.connections import ConnectionProfile, load_connection, read_env_file
from tuning_factory.execution import RunPodController


def test_secret_references_support_existing_colon_and_export_formats(tmp_path, monkeypatch):
    secret_file = tmp_path / "credentials.env"
    secret_file.write_text("Client_Secret: 'unit-secret'\nexport RUNPOD_API_KEY=unit-api\n")
    assert read_env_file(secret_file)["Client_Secret"] == "unit-secret"
    profile = tmp_path / "connection.json"
    profile.write_text(json.dumps({"tracking_uri": "https://tracking.example", "oidc_token_url": "https://sso.example/token",
                                   "oidc_client_id": "mlflow", "oidc_secret_file": str(secret_file),
                                   "runpod_env_file": str(secret_file)}))
    for key in ("MLFLOW_TRACKING_URI", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                "MLFLOW_OIDC_CLIENT_SECRET", "RUNPOD_API_KEY"):
        monkeypatch.setenv(key, "")
    connection = load_connection(profile)
    import os
    assert os.environ["MLFLOW_OIDC_CLIENT_SECRET"] == "unit-secret"
    assert os.environ["RUNPOD_API_KEY"] == "unit-api"
    assert "unit-secret" not in connection.model_dump_json()
    for key in ("MLFLOW_TRACKING_URI", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID",
                "MLFLOW_OIDC_CLIENT_SECRET", "RUNPOD_API_KEY"):
        monkeypatch.delenv(key, raising=False)
    with pytest.raises(ValueError):
        ConnectionProfile(tracking_uri="http://public.example", oidc_token_url="https://sso.example/token")


def test_live_runpod_machine_shape_normalization():
    normalized = RunPodController.normalize({"id": "test", "gpuCount": 1,
        "machine": {"gpuTypeId": "NVIDIA H200", "gpuDisplayName": "H200"}, "networkVolume": {"id": "v"}})
    assert normalized["gpu"] == {"id": "NVIDIA H200", "displayName": "H200", "count": 1}
