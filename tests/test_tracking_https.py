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


class FoundExperiment:
    def __init__(self, artifact_location):
        self.experiment_id = "7"
        self.artifact_location = artifact_location


class ExistingClient(FakeClient):
    def __init__(self, artifact_location):
        self.artifact_location = artifact_location

    def get_experiment_by_name(self, name):
        return FoundExperiment(self.artifact_location)


def test_http_artifact_roots_never_receive_the_token(monkeypatch):
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "t")
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    uri = "https://mlflow.example"
    monkeypatch.setenv("FACTORY_MLFLOW_ARTIFACT_LOCATION", "http://artifacts.example/root")
    with pytest.raises(ValueError, match="HTTPS"):
        MLflowTracker(uri, client=FakeClient())
    monkeypatch.delenv("FACTORY_MLFLOW_ARTIFACT_LOCATION")
    with pytest.raises(ValueError, match="HTTPS"):
        MLflowTracker(uri, client=ExistingClient("http://artifacts.example/root"))
    for location in ("mlflow-artifacts:/7", "https://artifacts.example/root", "s3://bucket/root", "file:///data"):
        MLflowTracker(uri, client=ExistingClient(location))
    monkeypatch.setenv("FACTORY_MLFLOW_ARTIFACT_LOCATION", "mlflow-artifacts:/7")
    MLflowTracker(uri, client=FakeClient())


def test_http_artifact_root_is_allowed_without_credentials(monkeypatch):
    monkeypatch.setattr("tuning_factory.auth.configure_oidc", lambda: None)
    MLflowTracker("http://mlflow.internal", client=ExistingClient("http://artifacts.internal/root"))


def test_desktop_uses_the_shared_guard_for_oidc_values(tmp_path):
    with pytest.raises(ValueError, match="HTTPS"):
        desktop.DesktopService(tmp_path, {"MLFLOW_TRACKING_URI": "http://example.org",
                                         "MLFLOW_OIDC_TOKEN_URL": "https://idp.example/token"})


def test_cli_work_refuses_insecure_tracking_before_claiming(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from tuning_factory.cli import app
    from tuning_factory.config import load_config
    from tuning_factory.runner import Factory

    factory = Factory(tmp_path / "state")
    factory.registry.register_dataset("dummy", "1", "examples/dummy.json", export_allowed=False)
    job = factory.submit(load_config("configs/ai/paper_extraction.yaml"))
    monkeypatch.setenv("MLFLOW_TRACKING_TOKEN", "t")
    result = CliRunner().invoke(app, ["--state-dir", str(tmp_path / "state"), "--tracking-uri",
                                      "http://mlflow.example", "work"])
    assert result.exit_code != 0
    stored = Factory(tmp_path / "state").store.get(job["id"])
    assert stored["status"] == "QUEUED" and stored["attempts"] == 0
