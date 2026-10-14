"""Live integration probes. Only explicit --write-test creates diagnostic MLflow runs."""
import json
import os
import uuid
from pathlib import Path

from .execution import RunPodController
from .registry import canonical, digest


def check_connections(root, pod_id=None, write_test=False):
    from mlflow import MlflowClient

    from .auth import configure_oidc
    from .tracking import MLflowTracker
    uri = os.environ.get("MLFLOW_TRACKING_URI")
    if not uri:
        raise ValueError("MLFLOW_TRACKING_URI or connection profile required")
    configure_oidc()
    client = MlflowClient(tracking_uri=uri)
    client.search_experiments(max_results=1)
    registry_readable = True
    try:
        client.search_registered_models(max_results=1)
    except Exception:
        registry_readable = False
    report = {"tracking_uri": uri, "mlflow_authenticated_read": True,
              "model_registry_read": registry_readable, "gpu_training_verified": False}
    if pod_id:
        controller = RunPodController(os.environ["RUNPOD_API_KEY"])
        pod = controller.status(pod_id)
        report["runpod"] = {"authenticated_read": True, "pod_id": pod_id, "name": pod.get("name"),
                            "status": pod.get("desiredStatus"), "gpu": pod.get("gpu"),
                            "hourly_cost_usd": float(pod["costPerHr"]),
                            "network_volume_id": (pod.get("networkVolume") or {}).get("id"),
                            "ssh_available": bool(pod.get("publicIp") and pod.get("portMappings", {}).get("22"))}
    if write_test:
        directory = Path(root) / "diagnostics" / uuid.uuid4().hex
        directory.mkdir(parents=True)
        manifest = {"simulation": True, "config": {"ai_id": "connection_check", "workflow_id": "diagnostics"},
                    "note": "Infrastructure connectivity test only. No model training or quality measurement."}
        (directory / "manifest.json").write_bytes(canonical(manifest))
        tracker = MLflowTracker(uri, experiment="llm-tuning-factory-connection-check-proxy", client=client)
        run_id = tracker.start("connection-" + directory.name, manifest)
        tracker.finish(run_id, {"connection_check_passed": 1}, directory, "COMPLETED")
        downloaded = client.download_artifacts(run_id, "evidence/manifest.json", str(directory))
        if digest(Path(downloaded).read_bytes()) != digest(canonical(manifest)):
            raise ValueError("remote diagnostic artifact download hash mismatch")
        if client.get_run(run_id).info.status != "FINISHED":
            raise ValueError("remote diagnostic run status mismatch")
        failure_id = tracker.start("failure-check-" + directory.name, manifest)
        tracker.finish(failure_id, {}, directory, "FAILED", "Intentional diagnostic failure; no training executed")
        if client.get_run(failure_id).info.status != "FAILED":
            raise ValueError("remote failed-run status mismatch")
        report["mlflow_write_test"] = {"run_id": run_id, "failure_run_id": failure_id,
                                        "artifact_upload_download_verified": True,
                                        "url": f"{uri}/#/experiments/{tracker.experiment_id}/runs/{run_id}"}
    receipt = Path(root) / "connection-check.json"
    receipt.parent.mkdir(parents=True, exist_ok=True)
    receipt.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return report
