"""Credentials never reach a plain-HTTP MLflow server from any client path; no network is touched."""
import pytest

from tuning_factory import desktop, diagnostics
from tuning_factory.auth import require_secure_tracking
from tuning_factory.tracking import MLflowTracker

CREDENTIALS = ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_SECRET")


class FakeClient:
    def get_experiment_by_name(self, name):
        return None

    def create_experiment(self, name, artifact_location=None):
        return "1"


@pytest.fixture(autouse=True)
def forbid_network(monkeypatch):
    for key in CREDENTIALS + ("MLFLOW_OIDC_CLIENT_ID", "MLFLOW_TRACKING_URI"):
        monkeypatch.delenv(key, raising=False)
    created = []
    monkeypatch.setattr("mlflow.MlflowClient", lambda *a, **kw: created.append(kw) or pytest.fail("client created"))
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: pytest.fail("OIDC configured"))
    return created


@pytest.mark.parametrize("env", [
    {"MLFLOW_TRACKING_TOKEN": "t"},
    {"MLFLOW_OIDC_TOKEN_URL": "https://idp.example/token", "MLFLOW_OIDC_CLIENT_ID": "id"},
    {"MLFLOW_OIDC_CLIENT_SECRET": "s"},
])
def test_http_with_credentials_is_refused_before_any_client(monkeypatch, env):
    for key, value in env.items():
        monkeypatch.setenv(key, value)
    uri = "http://mlflow.example"
    with pytest.raises(ValueError, match="HTTPS"):
        MLflowTracker(uri, client=FakeClient())
    with pytest.raises(ValueError, match="HTTPS"):
        MLflowTracker(uri)
    monkeypatch.setenv("MLFLOW_TRACKING_URI", uri)
    with pytest.raises(ValueError, match="HTTPS"):
        diagnostics.check_connections("unused")
    with pytest.raises(ValueError, match="HTTPS"):
        desktop.DesktopService.history(type("S", (), {"settings": {"MLFLOW_TRACKING_URI": uri}})(), {})


def test_http_without_credentials_and_file_store_are_allowed(monkeypatch):
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    require_secure_tracking("http://mlflow.internal:5000")
    require_secure_tracking("file:///tmp/mlruns")
    MLflowTracker("http://mlflow.internal:5000", client=FakeClient())


def test_https_with_token_is_allowed(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "t")
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    require_secure_tracking("https://mlflow.example")
    MLflowTracker("https://mlflow.example", client=FakeClient())
