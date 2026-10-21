"""Client-credentials refresh adapted from the existing kbl-llm-repo MLflow helper.

Reuses its bearer-token refresh contract without copying secrets or hardcoded /workspace paths.
"""
import os
import threading
import warnings
from urllib.parse import urlsplit

import httpx


class OIDCTokenRefresher:
    def __init__(self, token_url, client_id, client_secret, client=None):
        if urlsplit(token_url).scheme != "https":
            raise ValueError("OIDC client credentials require HTTPS")
        self.token_url = token_url
        self.client_id = client_id
        self.client_secret = client_secret
        self.client = client or httpx.Client(timeout=20, follow_redirects=False)
        self.stop = threading.Event()

    def refresh(self):
        response = self.client.post(self.token_url, data={"grant_type": "client_credentials",
                                    "client_id": self.client_id, "client_secret": self.client_secret})
        if response.is_error or response.is_redirect:
            raise RuntimeError(f"MLflow OIDC token request failed: HTTP {response.status_code}")
        data = response.json()
        token = data.get("access_token")
        if not isinstance(token, str) or not token:
            raise ValueError("OIDC response has no access token")
        os.environ["MLFLOW_TRACKING_TOKEN"] = token
        return max(1, min(240, int(data.get("expires_in", 300)) // 2))

    def start(self):
        interval = self.refresh()

        def loop():
            delay = interval
            while not self.stop.wait(delay):
                try:
                    delay = self.refresh()
                except Exception:
                    # Never put token responses or client secrets in logs.
                    warnings.warn("MLflow OIDC refresh failed; retrying in 15 seconds", stacklevel=1)
                    delay = 15

        threading.Thread(target=loop, daemon=True).start()


OIDC_CLIENT_MISSING = ("OIDC 인증을 선택했지만 클라이언트 인증값이 없습니다. "
                       "인증값을 입력하거나 토큰·OIDC 값을 비우세요(앱에서는 ‘인증 없음’ 선택).")


def require_complete_oidc():
    """An OIDC token URL without client credentials fails here, before any claim or remote work."""
    if os.environ.get("MLFLOW_OIDC_TOKEN_URL") and not (
            os.environ.get("MLFLOW_OIDC_CLIENT_ID") and os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")):
        raise ValueError(OIDC_CLIENT_MISSING)


INSECURE_TRACKING = ("인증값을 사용하는 MLflow 서버 주소는 HTTPS여야 합니다. 인증 없는 로컬·내부 HTTP 서버는 "
                     "토큰과 OIDC 값을 비우거나 앱에서 ‘인증 없음’을 선택하세요.")


def _bearer_configured():
    # A bare OIDC URL is only a default; configure_oidc refuses before any request without a secret.
    return bool(os.environ.get("MLFLOW_TRACKING_TOKEN")
                or (os.environ.get("MLFLOW_OIDC_TOKEN_URL") and os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")))


def credentials_configured():
    """Any credential MLflow would send: a bearer token, OIDC client credentials, or an HTTP Basic password
    (``MLFLOW_TRACKING_USERNAME``/``MLFLOW_TRACKING_PASSWORD``; 11.5 R1 m5). A username alone is not secret."""
    return _bearer_configured() or bool(os.environ.get("MLFLOW_TRACKING_PASSWORD"))


def current_auth_mode():
    """The MLflow auth scope of this process, never a credential: ``oidc``, ``token`` or ``none``. Bearer and OIDC
    credentials only: Basic credentials have no mode of their own (the graph controller pops them, and the desktop
    drops them for 'no authentication'), so they never change ``tracking_settings_sha256`` (D11), which ``tracking``
    re-exports this for."""
    if not _bearer_configured():
        return "none"
    oidc = os.environ.get("MLFLOW_OIDC_TOKEN_URL") and os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")
    return "oidc" if oidc else "token"


def require_secure_tracking(uri):
    """Bearer tokens and OIDC secrets never travel over plain HTTP; unauthenticated servers are unaffected."""
    if uri and credentials_configured() and urlsplit(str(uri)).scheme != "https":
        raise ValueError(INSECURE_TRACKING)


def require_secure_artifacts(uri):
    """MLflow's HTTP artifact repository sends the tracking token; mlflow-artifacts:, s3:, file: do not."""
    if uri and credentials_configured() and urlsplit(str(uri)).scheme == "http":
        raise ValueError(INSECURE_TRACKING)


def configure_oidc():
    token_url = os.environ.get("MLFLOW_OIDC_TOKEN_URL")
    if not token_url:
        return None
    require_complete_oidc()
    client_id = os.environ.get("MLFLOW_OIDC_CLIENT_ID")
    client_secret = os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")
    refresher = OIDCTokenRefresher(token_url, client_id, client_secret)
    refresher.start()
    return refresher
