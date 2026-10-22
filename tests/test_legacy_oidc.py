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


def test_incomplete_oidc_fails_fast_before_submit_and_execute(tmp_path, monkeypatch):
    for key in desktop.ENV_SETTINGS:
        monkeypatch.setenv(key, "x")
        monkeypatch.delenv(key)
    app = desktop.DesktopService(tmp_path, SETTINGS)
    for action in ("submit", "execute"):
        with pytest.raises(ValueError) as error:
            app.dispatch(action, {"approved": True, "config": {}, "job_id": "0" * 32})
        assert str(error.value) == OIDC_CLIENT_MISSING


def test_oidc_message_is_provider_neutral():
    assert OIDC_CLIENT_MISSING == ("OIDC 인증을 선택했지만 클라이언트 인증값이 없습니다. "
                                   "인증값을 입력하거나 토큰·OIDC 값을 비우세요(앱에서는 ‘인증 없음’ 선택).")
