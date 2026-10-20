"""Explicit MLflow client: tracking, artifact storage and candidate registry are separate.

Task 8.3 adds the keyed, search-first methods the tracking outbox uses (``find_keyed_runs``/``create_keyed_run``,
``find_keyed_model_versions``/``create_keyed_model_version``), the per-run artifact-root check for created and
adopted runs, and ``tracking_settings_sha256`` (D11). ``register_candidate`` (v1) searches by a stable key that
includes the attempt before it creates a version (D10); everything else in v1 keeps its behaviour.
"""
import json
import math
import os
import re
import time
from collections.abc import Mapping
from pathlib import Path
from urllib.parse import urlsplit

from .registry import canonical, digest

DEFAULT_EXPERIMENT = "llm-tuning-factory"
TRACKING_UNCERTAIN = "MLflow outcome is uncertain; searching before any new create"
TRACKING_SETTINGS = ("tracking_uri", "experiment", "artifact_location", "auth_mode")
AUTH_MODES = ("oidc", "token", "none")
TRACKING_KEY_TAG = "factory.tracking_key"
REGISTRATION_KEY_TAG = "factory.registration_key"
TERMINAL_STATUSES = ("FINISHED", "FAILED", "KILLED")
_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,249}")   # never a quote: keys go into filter strings


def _key(key):
    if not isinstance(key, str) or not _KEY.fullmatch(key):
        raise ValueError("invalid MLflow tracking key")
    return key


def _quotable(value, what):
    if not isinstance(value, str) or not value or "'" in value or "\\" in value:
        raise ValueError(f"invalid MLflow {what}")
    return value


def current_auth_mode():
    """The MLflow auth scope of this process, never a credential: ``oidc``, ``token`` or ``none``."""
    from .auth import credentials_configured
    if not credentials_configured():
        return "none"
    oidc = os.environ.get("MLFLOW_OIDC_TOKEN_URL") and os.environ.get("MLFLOW_OIDC_CLIENT_SECRET")
    return "oidc" if oidc else "token"


def _public(public):
    if not isinstance(public, Mapping) or set(public) != set(TRACKING_SETTINGS):
        raise ValueError("tracking settings are exactly tracking_uri, experiment, artifact_location and auth_mode")
    uri, experiment, location, mode = (public[key] for key in TRACKING_SETTINGS)
    if not isinstance(uri, str) or not uri:
        raise ValueError("tracking URI required")
    if not isinstance(experiment, str) or not experiment:
        raise ValueError("MLflow experiment name required")
    if location is not None and (not isinstance(location, str) or not location):
        raise ValueError("artifact location must be a non-empty string or None")
    if mode not in AUTH_MODES:
        raise ValueError("auth_mode must be oidc, token or none")
    for value in (uri, location):
        if value and (urlsplit(value).username is not None or urlsplit(value).password is not None):
            raise ValueError("tracking settings must not embed credentials")
    return {key: public[key] for key in TRACKING_SETTINGS}


def public_tracking_settings(uri, experiment=DEFAULT_EXPERIMENT, artifact_location=None, auth_mode=None):
    """What identifies an MLflow destination without any secret (D11)."""
    return _public({"tracking_uri": uri, "experiment": experiment, "artifact_location": artifact_location,
                    "auth_mode": current_auth_mode() if auth_mode is None else auth_mode})


def tracking_settings_sha256(public: Mapping) -> str:
    """D11: ``TrackingDestination.public_settings_sha256``. Task 9 must build the pinned hash with this function.

    It covers exactly the tracking URI, the experiment, the artifact location and the auth mode, as ``canonical``
    JSON. Credentials are refused, never hashed.
    """
    return digest(canonical(_public(public)))


def _artifact_uri(info):
    value = getattr(info, "artifact_uri", None)
    return value if isinstance(value, str) else None


def _string_tags(tags):
    return {str(key): str(value) for key, value in dict(tags).items()}


class MLflowTracker:
    def __init__(self, uri, experiment=DEFAULT_EXPERIMENT, client=None, artifact_location=None):
        # MLflow's terminal URL output breaks machine-readable JSON and legacy Windows consoles.
        os.environ["MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"] = "true"
        from .auth import configure_oidc, require_secure_artifacts, require_secure_tracking
        require_secure_tracking(uri)
        artifact_location = artifact_location or os.environ.get("FACTORY_MLFLOW_ARTIFACT_LOCATION")
        require_secure_artifacts(artifact_location)
        self.uri, self.experiment, self.artifact_location = uri, experiment, artifact_location
        self.auth_mode = current_auth_mode()
        self.auth_refresher = configure_oidc()
        if client is None:
            from mlflow import MlflowClient
            client = MlflowClient(tracking_uri=uri)
        self.client = client
        found = client.get_experiment_by_name(experiment)
        if found is not None:
            require_secure_artifacts(getattr(found, "artifact_location", None))
        self.experiment_id = found.experiment_id if found else client.create_experiment(experiment, artifact_location=artifact_location)

    def start(self, job_id, manifest):
        tags = {"factory.job_id": job_id, "factory.simulation": str(manifest["simulation"]).lower(),
                "factory.ai_id": manifest["config"]["ai_id"],
                "factory.workflow_id": manifest["config"]["workflow_id"]}
        if os.environ.get("FACTORY_OPERATOR"):
            tags["factory.operator"] = os.environ["FACTORY_OPERATOR"]
        run_info = self.client.create_run(self.experiment_id, tags=tags).info
        run_id = run_info.run_id
        from .auth import require_secure_artifacts
        artifact_uri = getattr(run_info, "artifact_uri", None)
        try:
            # A server-assigned default artifact root is only known per run; refuse before any upload.
            require_secure_artifacts(artifact_uri if isinstance(artifact_uri, str) else None)
        except ValueError:
            self.client.set_terminated(run_id, status="FAILED")
            raise
        try:
            for key, value in manifest.items():
                self.client.log_param(run_id, key, json.dumps(value, sort_keys=True)[:5900])
        except Exception:
            self.client.set_terminated(run_id, status="FAILED")
            raise
        return run_id

    def finish(self, run_id, metrics, directory, status, error=None):
        try:
            self._persist(run_id, metrics, directory, error)
        except Exception:
            self.client.set_tag(run_id, "factory.persistence_error", "Artifact or metric persistence failed")
            self.client.set_terminated(run_id, status="FAILED")
            raise
        self.client.set_terminated(run_id, status={"COMPLETED": "FINISHED", "CANCELED": "KILLED"}.get(status, "FAILED"))

    def _persist(self, run_id, metrics, directory, error):
        from mlflow.entities import Metric
        if metrics:
            self.client.log_batch(run_id, metrics=[Metric(k, float(v), int(time.time() * 1000), 0)
                                                  for k, v in metrics.items()])
        # Upload individual evidence files; never recursively upload the tracking store itself.
        directory = Path(directory)
        for name in ("manifest.json", "train.yaml", "train.log", "artifacts.json", "evaluation.json", "result.json", "error.txt", "runtime.json", "execution.json"):
            if (directory / name).is_file():
                self.client.log_artifact(run_id, str(directory / name), artifact_path="evidence")
        if (directory / "output").is_dir():
            self.client.log_artifacts(run_id, str(directory / "output"), artifact_path="output")
        if error:
            self.client.set_tag(run_id, "factory.error", error[:2000])

    def register_candidate(self, run_id, manifest, qualified, *, attempt_id=None):
        """v1 registration, search-first (D10): a version keyed by the manifest and the attempt is adopted after a
        lost receipt instead of being created twice. A failing search raises, so nothing is created blindly."""
        if manifest["simulation"]:
            raise ValueError("simulation cannot register a trained model candidate")
        name = "factory-" + manifest["config"]["ai_id"]
        self.ensure_registered_model(name)
        key = _key("v1:" + manifest["sha256"] + ":" + (attempt_id or "run-" + run_id))
        found = self.find_keyed_model_versions(name, run_id, key)
        if found:
            return found[0]
        tags = {"base_model": manifest["config"]["base_model"],
                "model_revision": manifest["config"]["model_revision"],
                "tokenizer_revision": manifest["config"]["tokenizer_revision"],
                "engine": manifest["training_engine"], "manifest_sha256": manifest["sha256"],
                "quality_qualified": str(qualified).lower(), "approval": "pending", REGISTRATION_KEY_TAG: key}
        return self.client.create_model_version(name, source=f"runs:/{run_id}/output", run_id=run_id, tags=tags)

    # -- Task 8.3: keyed, search-first operations for the tracking outbox -----------------------------

    def public_settings(self) -> dict:
        """tracking_uri, experiment, artifact_location and auth_mode; never a secret."""
        return public_tracking_settings(self.uri, self.experiment, self.artifact_location, self.auth_mode)

    @staticmethod
    def _pages(search):
        """Every page of a paginated search. A token that does not advance is an error, never a short answer."""
        items, token, seen = [], None, set()
        while True:
            page = search(token)
            items.extend(page)
            token = getattr(page, "token", None)
            if not token:
                return items
            if token in seen:
                raise RuntimeError("MLflow search pagination did not advance")
            seen.add(token)

    def find_keyed_runs(self, key) -> list[dict]:
        """Active runs of this experiment tagged with the stable key, earliest first: run_id, start_time and
        artifact_uri. The tag is checked client-side as well."""
        key = _key(key)
        runs = self._pages(lambda token: self.client.search_runs(
            [self.experiment_id], filter_string=f"tags.`{TRACKING_KEY_TAG}` = '{key}'", max_results=1000,
            page_token=token))
        found = {run.info.run_id: {"run_id": run.info.run_id, "start_time": getattr(run.info, "start_time", None),
                                   "artifact_uri": _artifact_uri(run.info)}
                 for run in runs if (run.data.tags or {}).get(TRACKING_KEY_TAG) == key}
        return sorted(found.values(), key=lambda item: (item["start_time"] is None, item["start_time"] or 0,
                                                        item["run_id"]))

    def create_keyed_run(self, key, *, parent_run_id, run_name, tags) -> dict:
        """Create a run tagged with the stable key and its parent. As in ``start``, a server-assigned insecure
        artifact root terminates the run FAILED and refuses before any upload."""
        tags = _string_tags(tags) | {TRACKING_KEY_TAG: _key(key)}
        if parent_run_id:
            tags["mlflow.parentRunId"] = parent_run_id
        if os.environ.get("FACTORY_OPERATOR"):
            tags["factory.operator"] = os.environ["FACTORY_OPERATOR"]
        info = self.client.create_run(self.experiment_id, tags=tags, run_name=run_name).info
        identity = {"run_id": info.run_id, "start_time": getattr(info, "start_time", None),
                    "artifact_uri": _artifact_uri(info)}
        self._refuse_insecure_root(info.run_id, identity["artifact_uri"])
        return identity

    def _refuse_insecure_root(self, run_id, artifact_uri):
        from .auth import require_secure_artifacts
        try:
            require_secure_artifacts(artifact_uri)
        except ValueError:
            self.client.set_terminated(run_id, status="FAILED")
            raise

    def check_run_artifacts(self, run_id) -> None:
        """``require_secure_artifacts`` on an existing (adopted) run's root, before any upload into it."""
        self._refuse_insecure_root(run_id, _artifact_uri(self.client.get_run(run_id).info))

    def log_lineage(self, run_id, files: Mapping[str, Path], *, params, metrics, artifact_path="lineage",
                    timestamp_ms=0):
        """Upload staged evidence files under their own names, then identical params and step-0 metric points.
        ``timestamp_ms`` comes from the event, so a retry repeats the same points."""
        from mlflow.entities import Metric, Param
        for name, path in sorted(files.items()):
            if Path(path).name != name:
                raise ValueError("a lineage file must be staged under its own name")
            self.client.log_artifact(run_id, str(path), artifact_path=artifact_path)
        metric_items = [Metric(key, float(value), int(timestamp_ms), 0) for key, value in sorted(metrics.items())
                        if isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)]
        param_items = [Param(str(key), str(value)[:5900]) for key, value in sorted(params.items())]
        if metric_items or param_items:
            self.client.log_batch(run_id, metrics=metric_items, params=param_items)

    def terminate(self, run_id, status) -> None:
        if status not in TERMINAL_STATUSES:
            raise ValueError("MLflow terminal status must be FINISHED, FAILED or KILLED")
        self.client.set_terminated(run_id, status=status)

    def upload_model(self, run_id, directory) -> None:
        self.client.log_artifacts(run_id, str(directory), artifact_path="model")

    def ensure_registered_model(self, name) -> None:
        """The registered model's name is its stable key: a create that raced or lost its response is adopted."""
        name = _quotable(name, "registered model name")
        if self.client.search_registered_models(filter_string=f"name = '{name}'"):
            return
        try:
            self.client.create_registered_model(name)
        except Exception:
            if not self.client.search_registered_models(filter_string=f"name = '{name}'"):
                raise

    def find_keyed_model_versions(self, name, run_id, key) -> list[dict]:
        """Versions of ``name`` created from ``run_id`` whose registration key tag equals ``key``, lowest first.

        The server filter uses only ``name`` and ``run_id``, which older servers understand (plan F11). The tag is
        checked client-side over every page."""
        name, run_id, key = _quotable(name, "registered model name"), _quotable(run_id, "run ID"), _key(key)
        versions = self._pages(lambda token: self.client.search_model_versions(
            filter_string=f"name = '{name}' and run_id = '{run_id}'", page_token=token))
        found = {str(item.version): {"name": item.name, "version": str(item.version)} for item in versions
                 if item.name == name and (item.tags or {}).get(REGISTRATION_KEY_TAG) == key}
        return sorted(found.values(), key=lambda item: int(item["version"]))

    def create_keyed_model_version(self, name, *, run_id, key, tags) -> dict:
        tags = _string_tags(tags) | {REGISTRATION_KEY_TAG: _key(key)}
        version = self.client.create_model_version(name, source=f"runs:/{run_id}/model", run_id=run_id, tags=tags)
        return {"name": version.name, "version": str(version.version)}


class LazyMLflowTracker:
    """Validation/status/dry-run do not create remote experiments or authenticate."""
    def __init__(self, uri):
        self.uri = uri
        self._tracker = None

    def public_settings(self):
        """Known without constructing the client, so a mismatched destination never contacts MLflow."""
        if self._tracker is not None:
            return self._tracker.public_settings()
        return public_tracking_settings(self.uri, DEFAULT_EXPERIMENT,
                                        os.environ.get("FACTORY_MLFLOW_ARTIFACT_LOCATION") or None)

    def __getattr__(self, name):
        if self._tracker is None:
            self._tracker = MLflowTracker(self.uri)
        return getattr(self._tracker, name)
