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
                       "인증값을 입력하거나 ‘인증 없음’을 선택하세요.")


def configure_oidc():
    token_url = os.environ.get("MLFLOW_OIDC_TOKEN_URL")
    if not token_url:
        return None
    client_id = os.environ.get("MLFLOW_OIDC_CLIENT_ID")
    client_secret = os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")
    if not client_id or not client_secret:
        raise ValueError(OIDC_CLIENT_MISSING)
    refresher = OIDCTokenRefresher(token_url, client_id, client_secret)
    refresher.start()
    return refresher
