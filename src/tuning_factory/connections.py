"""Non-secret connection profiles referring to existing credential files."""
import json
import os
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlsplit

from pydantic import field_validator, model_validator

from .config import StrictModel
from .gpu_servers import SSHServerSettings

ENV_SETTINGS = frozenset({
    "RUNPOD_API_KEY", "MLFLOW_TRACKING_URI", "MLFLOW_TRACKING_TOKEN",
    "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
    "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR",
})


def load_environment(path=Path(".env")):
    """Read local settings without executing shell syntax or overriding process settings."""
    path = Path(path)
    if path.is_file():
        for key, value in read_env_file(path).items():
            if key in ENV_SETTINGS and value:
                os.environ.setdefault(key, value)


def read_env_file(path):
    result = {}
    for line in Path(path).read_text(encoding="utf-8-sig").splitlines():
        line = line.strip().removeprefix("export ")
        if not line or line.startswith("#"):
            continue
        match = re.fullmatch(r"([A-Za-z_][A-Za-z0-9_]*)\s*[:=]\s*(.*)", line)
        if match:
            result[match[1]] = match[2].strip().strip("\"'")
    return result


class ConnectionProfile(StrictModel):
    provider: Literal["existing_runpod", "ssh_server"] = "existing_runpod"
    ssh_server: SSHServerSettings | None = None
    tracking_uri: str
    artifact_location: str | None = None
    oidc_token_url: str | None = None
    oidc_client_id: str = "mlflow"
    oidc_secret_file: Path | None = None
    oidc_secret_key: str = "Client_Secret"
    runpod_env_file: Path | None = None
    pod_id: str | None = None
    ssh_key_file: Path | None = None

    @field_validator("provider", mode="before")
    @classmethod
    def legacy_provider(cls, value):
        return value or "existing_runpod"

    @model_validator(mode="after")
    def secure_auth(self):
        if self.provider == "ssh_server" and self.ssh_server is None:
            raise ValueError("SSH server settings required for selected provider")
        if self.oidc_token_url and urlsplit(self.tracking_uri).scheme != "https":
            raise ValueError("OIDC-authenticated tracking requires HTTPS")
        return self


def load_connection(path):
    path = Path(path).resolve()
    profile = ConnectionProfile.model_validate(json.loads(path.read_text(encoding="utf-8")))
    if profile.ssh_server and not profile.ssh_server.ssh_key_file.is_absolute():
        server = profile.ssh_server.model_copy(update={"ssh_key_file": (path.parent / profile.ssh_server.ssh_key_file).resolve()})
        profile = profile.model_copy(update={"ssh_server": server})
    os.environ["MLFLOW_TRACKING_URI"] = profile.tracking_uri
    if profile.artifact_location:
        os.environ["FACTORY_MLFLOW_ARTIFACT_LOCATION"] = profile.artifact_location
    if profile.oidc_token_url:
        os.environ["MLFLOW_OIDC_TOKEN_URL"] = profile.oidc_token_url
        os.environ["MLFLOW_OIDC_CLIENT_ID"] = profile.oidc_client_id
        if not profile.oidc_secret_file:
            raise ValueError("OIDC secret file reference required")
        values = read_env_file(path.parent / profile.oidc_secret_file)
        secret = values.get(profile.oidc_secret_key)
        if not secret:
            raise ValueError("OIDC secret key missing from referenced credential file")
        os.environ["MLFLOW_OIDC_CLIENT_SECRET"] = secret
    if profile.runpod_env_file and profile.provider == "existing_runpod":
        values = read_env_file(path.parent / profile.runpod_env_file)
        if not values.get("RUNPOD_API_KEY"):
            raise ValueError("RunPod key missing from referenced credential file")
        os.environ["RUNPOD_API_KEY"] = values["RUNPOD_API_KEY"]
    return profile


def load_server_settings(path):
    """Read one local direct-SSH configuration, with no credentials or network reads."""
    path = Path(path).resolve()
    settings = SSHServerSettings.model_validate_json(path.read_text(encoding="utf-8-sig"))
    key = settings.ssh_key_file
    if not key.is_absolute():
        settings = settings.model_copy(update={"ssh_key_file": (path.parent / key).resolve()})
    return settings
