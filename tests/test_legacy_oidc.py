"""The default OIDC URL without a secret is not a credential: startup works and MLflow actions explain why."""
import pytest

from tuning_factory import desktop
from tuning_factory.auth import OIDC_CLIENT_MISSING

SETTINGS = {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000",
            "MLFLOW_OIDC_TOKEN_URL": "https://sso.example/token"}


def test_legacy_default_oidc_url_without_secret_keeps_the_app_usable(tmp_path, monkeypatch):
    for key in desktop.ENV_SETTINGS:
        monkeypatch.setenv(key, "x")  # Registers teardown for the service's env export.
        monkeypatch.delenv(key)
    monkeypatch.setattr("mlflow.MlflowClient", lambda *a, **kw: pytest.fail("client created"))
    app = desktop.DesktopService(tmp_path, SETTINGS)
    assert "templates" in app.dispatch("catalog", {})
    with pytest.raises(ValueError) as error:
        app.dispatch("history", {})
    assert str(error.value) == OIDC_CLIENT_MISSING
