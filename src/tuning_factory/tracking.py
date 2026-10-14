"""Explicit MLflow client: tracking, artifact storage and candidate registry are separate."""
import json
import time
from pathlib import Path


class MLflowTracker:
    def __init__(self, uri, experiment="llm-tuning-factory", client=None):
        from .auth import configure_oidc
        self.auth_refresher = configure_oidc()
        if client is None:
            from mlflow import MlflowClient
            client = MlflowClient(tracking_uri=uri)
        self.client = client
        found = client.get_experiment_by_name(experiment)
        self.experiment_id = found.experiment_id if found else client.create_experiment(experiment)

    def start(self, job_id, manifest):
        tags = {"factory.job_id": job_id, "factory.simulation": str(manifest["simulation"]).lower(),
                "factory.ai_id": manifest["config"]["ai_id"],
                "factory.workflow_id": manifest["config"]["workflow_id"]}
        run_id = self.client.create_run(self.experiment_id, tags=tags).info.run_id
        try:
            for key, value in manifest.items():
                self.client.log_param(run_id, key, json.dumps(value, sort_keys=True)[:5900])
        except Exception:
            self.client.set_terminated(run_id, status="FAILED")
            raise
        return run_id

    def finish(self, run_id, metrics, directory, status, error=None):
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
        self.client.set_terminated(run_id, status={"COMPLETED": "FINISHED", "CANCELED": "KILLED"}.get(status, "FAILED"))

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
