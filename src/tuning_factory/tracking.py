"""Explicit MLflow client: tracking, artifact storage and candidate registry are separate."""
import json
import os
import time
from pathlib import Path


class MLflowTracker:
    def __init__(self, uri, experiment="llm-tuning-factory", client=None, artifact_location=None):
        # MLflow's terminal URL output breaks machine-readable JSON and legacy Windows consoles.
        os.environ["MLFLOW_SUPPRESS_PRINTING_URL_TO_STDOUT"] = "true"
        from .auth import configure_oidc, require_secure_artifacts, require_secure_tracking
        require_secure_tracking(uri)
        artifact_location = artifact_location or os.environ.get("FACTORY_MLFLOW_ARTIFACT_LOCATION")
        require_secure_artifacts(artifact_location)
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

    def register_candidate(self, run_id, manifest, qualified):
        if manifest["simulation"]:
            raise ValueError("simulation cannot register a trained model candidate")
        name = "factory-" + manifest["config"]["ai_id"]
        if not self.client.search_registered_models(filter_string=f"name = '{name}'"):
            self.client.create_registered_model(name)
        tags = {"base_model": manifest["config"]["base_model"],
                "model_revision": manifest["config"]["model_revision"],
                "tokenizer_revision": manifest["config"]["tokenizer_revision"],
                "engine": manifest["training_engine"], "manifest_sha256": manifest["sha256"],
                "quality_qualified": str(qualified).lower(), "approval": "pending"}
        return self.client.create_model_version(name, source=f"runs:/{run_id}/output", run_id=run_id, tags=tags)


class LazyMLflowTracker:
    """Validation/status/dry-run do not create remote experiments or authenticate."""
    def __init__(self, uri):
        self.uri = uri
        self._tracker = None

    def __getattr__(self, name):
        if self._tracker is None:
            self._tracker = MLflowTracker(self.uri)
        return getattr(self._tracker, name)
