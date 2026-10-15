"""Narrow JSON sidecar API for the Windows application; never a shell or HTTP server."""
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
from pathlib import Path
from urllib.parse import urlsplit

import yaml

from .capabilities import compatibility, model_catalog, training_stages
from .config import AIConfig
from .connections import ENV_SETTINGS
from .desktop_inspection import inspect_dataset, parameter_changes, parse_progress
from .execution import Policy, RunPodController
from .manifest import resolve
from .model_hub import ModelHub, register_model, registered_models
from .registry import canonical, safe_name
from .runner import Factory


def assets_root():
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"
    return Path(__file__).resolve().parents[2]


def compare_runs(base, candidate):
    comparable = bool(base.get("ai_id")) and bool(base.get("validation_sha256")) and (
        base.get("evaluation_kind") not in {None, "pipeline"}
    ) and base.get("status") == candidate.get("status") == "FINISHED" and all(
        base.get(key) == candidate.get(key)
        for key in ("ai_id", "validation_sha256", "simulation", "evaluation_kind")
    ) and not base.get("simulation") and (
        base.get("config", {}).get("training_stage", "sft") ==
        candidate.get("config", {}).get("training_stage", "sft")
    )
    return {"comparable": comparable,
            "reason": "" if comparable else "동일 업무·검증 데이터·평가 방식의 실제 실행만 지표를 비교합니다.",
            "parameter_changes": parameter_changes(base.get("config", {}), candidate.get("config", {})),
            "deltas": {key: candidate["metrics"][key] - value
                       for key, value in base.get("metrics", {}).items()
                       if key in candidate.get("metrics", {})} if comparable else {}}


class DesktopService:
    def __init__(self, root, settings=None):
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self.settings = settings or {}
        uri = self.settings.get("MLFLOW_TRACKING_URI")
        if uri and any(self.settings.get(k) for k in ("MLFLOW_TRACKING_TOKEN", "MLFLOW_OIDC_CLIENT_SECRET")):
            if urlsplit(uri).scheme != "https":
                raise ValueError("authenticated MLflow requires HTTPS")
        for key in ENV_SETTINGS:
            os.environ.pop(key, None)
            if self.settings.get(key):
                os.environ[key] = str(self.settings[key])
        from .tracking import LazyMLflowTracker
        uri = self.settings.get("MLFLOW_TRACKING_URI")
        self.factory = Factory(self.root, policy=Policy(),
                               tracker=LazyMLflowTracker(uri) if uri else None)

    def job_directory(self, job_id):
        if not re.fullmatch(r"[0-9a-f]{32}", job_id):
            raise ValueError("invalid job ID")
        return self.factory.directory(job_id)

    def manifest(self, config):
        manifest = resolve(config, self.factory.registry, simulation=False)
        validation = manifest.get("validation_dataset")
        if validation and validation["sha256"] == manifest["dataset"]["sha256"]:
            raise ValueError("training and validation snapshots are identical; 별도 검증 데이터를 선택하세요.")
        return manifest

    def dispatch(self, action, data):
        if action == "catalog":
            templates = []
            for name in ("paper_extraction", "labnote_generation", "workflow_classification"):
                path = assets_root() / "configs/ai" / (name + ".yaml")
                config = AIConfig.model_validate(yaml.safe_load(path.read_text(encoding="utf-8"))).model_dump()
                config["hyperparameters"]["max_steps"] = -1
                templates.append({"name": name, "config": config})
            profiles = [json.loads(p.read_bytes()) for p in sorted((self.root / "profiles").glob("*.json"))]
            datasets = [json.loads(p.read_bytes()) for p in sorted(
                (self.factory.registry.root / "datasets").glob("*/*.json"))]
            models = {model["id"]: model for model in model_catalog()}
            models.update({model["id"]: model for model in registered_models(self.root)})
            return {"templates": templates, "profiles": profiles, "datasets": datasets,
                    "models": list(models.values()), "training_stages": training_stages()}
        if action == "model_search":
            with self.hub() as hub:
                return hub.search(data["query"], data.get("sort", "downloads"))
        if action in {"model_inspect", "model_import"}:
            with self.hub() as hub:
                result = hub.inspect(data["model"], data.get("revision"))
            if action == "model_inspect":
                return result
            if not result["supported"]:
                raise ValueError("; ".join(result["reasons"]))
            if (data.get("config_sha256") != result["profile"]["config_sha256"] or
                    data.get("revision") != result["revision"]):
                raise ValueError("model metadata changed since inspection; inspect it again")
            return register_model(self.root, result["profile"])
        if action == "capabilities":
            result = compatibility(data["config"])
            try:
                config = AIConfig.model_validate(data["config"])
            except ValueError as exc:
                result = {**result, "valid": False,
                          "errors": result["errors"] or [str(exc)]}
            else:
                try:
                    dataset = self.factory.registry.get_dataset(config.dataset_id, config.dataset_version)
                except FileNotFoundError:
                    result["warnings"].append("학습 전에 이 PC에 데이터 버전을 등록하세요.")
                else:
                    if dataset.get("kind", "instruction") != result["dataset_kind"]:
                        result["errors"].append(f"dataset kind must be {result['dataset_kind']} for {config.training_stage}")
                        result["valid"] = False
            return result
        if action == "model_revision":
            import httpx
            model = data["model"]
            custom = next((m for m in registered_models(self.root) if m["id"] == model), None)
            if custom:
                with self.hub() as hub:
                    result = hub.inspect(model)
                if not result["supported"]:
                    raise ValueError("; ".join(result["reasons"]))
                register_model(self.root, result["profile"])
                return {**result, "model_profile": result["profile"]}
            profile = next((m for m in model_catalog() if m["id"] == model), None)
            if not profile:
                raise ValueError("supported model required")
            response = httpx.get("https://huggingface.co/api/models/" + model + "/revision/main", timeout=20)
            response.raise_for_status()
            revision = response.json().get("sha", "")
            if not re.fullmatch("[0-9a-f]{40}", revision):
                raise ValueError("model revision lookup did not return an immutable commit")
            response = httpx.get(f"https://huggingface.co/{model}/resolve/{revision}/config.json", timeout=20,
                                 follow_redirects=True)
            response.raise_for_status()
            metadata = response.json()
            if metadata.get("model_type") != profile["architecture"]:
                raise ValueError("model architecture differs from the reviewed compatibility profile")
            return {"model_revision": revision, "tokenizer_revision": revision,
                    "template": profile["template"], "architecture": profile["architecture"],
                    "max_length": min(profile["max_length"], metadata.get("max_position_embeddings") or profile["max_length"]),
                    "recommended_precision": metadata.get("torch_dtype", metadata.get("dtype")),
                    "hidden_layers": metadata.get("num_hidden_layers"), "metadata_verified": True}
        if action == "save_profile":
            config = AIConfig.model_validate(data["config"])
            directory = self.root / "profiles"
            directory.mkdir(exist_ok=True)
            (directory / (safe_name(config.ai_id) + ".json")).write_bytes(canonical(config.model_dump()))
            return {"saved": True}
        if action == "dataset_preview":
            return inspect_dataset(data["path"], data.get("kind", "instruction"))
        if action == "dataset":
            return self.factory.registry.register_dataset(
                data["dataset_id"], data["version"], Path(data["path"]),
                export_allowed=data.get("export_allowed") is True, synthetic=data.get("synthetic") is True,
                kind=data.get("kind", "instruction"))
        if action == "s3_list":
            return self.storage().list_objects(data["bucket"], data.get("prefix", ""),
                                               data.get("continuation_token"))
        if action == "s3_head":
            return {**self.storage().head(data["bucket"], data["key"], data.get("version_id")),
                    "import_max_bytes": 32 * 1024 * 1024, "free_bytes": shutil.disk_usage(self.root).free}
        if action == "s3_import":
            if data.get("approved") is not True:
                raise ValueError("explicit S3 download approval required")
            safe_name(data["dataset_id"])
            safe_name(data["version"])
            if int((data.get("expected") or {}).get("size", 0)) > shutil.disk_usage(self.root).free:
                raise ValueError("insufficient local disk space for S3 import")
            temporary_root = self.root / "s3-imports"
            temporary_root.mkdir(exist_ok=True)
            with tempfile.TemporaryDirectory(dir=temporary_root) as directory:
                result = self.storage().download(data["bucket"], data["key"], directory,
                                                 data.get("expected") or {}, maximum_bytes=32 * 1024 * 1024)
                return self.factory.registry.register_dataset(
                    data["dataset_id"], data["version"], result["path"],
                    export_allowed=data.get("export_allowed") is True, synthetic=data.get("synthetic") is True,
                    kind=data.get("kind", "instruction"), source_metadata=result["source"])
        if action == "plan":
            config = AIConfig.model_validate(data["config"])
            manifest = self.manifest(config)
            estimate = self.factory.policy.check(config, manifest["dataset"])
            return {"dry_run": True, "manifest": manifest, "estimated_cost_usd": estimate,
                    "note": "저장·전송·대기 비용 별도. 실행 버튼 전에는 GPU 작업을 시작하지 않습니다."}
        if action == "submit":
            self.require_approval(data)
            config = AIConfig.model_validate(data["config"])
            manifest = self.manifest(config)
            self.factory.policy.check(config, manifest["dataset"], execute=True, approved_transfer=True)
            if manifest.get("validation_dataset"):
                self.factory.policy.check(config, manifest["validation_dataset"], execute=True, approved_transfer=True)
            return self.factory.submit(config, request_key=data.get("request_key"), simulation=False)
        if action == "execute":
            self.require_approval(data)
            from .desktop_runtime import executor_for
            job_id = data["job_id"]
            self.job_directory(job_id)
            stage = self.factory.store.get(job_id)["manifest"]["config"].get("training_stage", "sft")
            with executor_for(self.settings, self.root, training_stage=stage) as executor:
                return self.factory.work(executor=executor, gpu=self.settings["pod_id"],
                                         approved_transfer=True, select_mode=True, target_job_id=job_id)
        if action == "jobs":
            return self.factory.store.list()[-200:][::-1]
        if action == "logs":
            directory = self.job_directory(data["job_id"])
            name = data.get("name", "train.log")
            if name not in {"train.log", "worker.log", "error.txt", "evaluation.json", "remote-attempt.json",
                            "runtime.json", "output/generated_predictions.jsonl"}:
                raise ValueError("invalid log name")
            path = directory / name
            if not path.is_file():
                return {"text": "", "note": "파일이 아직 회수되지 않았습니다."}
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 64000))
                return {"text": handle.read(64000).decode("utf-8", errors="replace")}
        if action == "cancel":
            self.job_directory(data["job_id"])
            self.factory.store.cancel(data["job_id"])
            return {"requested": True, "note": "취소 요청입니다. 원격 프로세스 종료 확인과는 다릅니다."}
        if action == "recover":
            return {"recovered": self.factory.store.recover()}
        if action == "reconcile":
            from .desktop_runtime import executor_for
            self.job_directory(data["job_id"])
            with executor_for(self.settings, self.root, preflight=False) as executor:
                return self.factory.reconcile(data["job_id"], executor)
        if action == "resume":
            self.require_approval(data)
            directory = self.job_directory(data["job_id"])
            checkpoint = safe_name(data["checkpoint"])
            self.factory.resume(data["job_id"], directory / "output" / checkpoint)
            return self.factory.store.get(data["job_id"])
        if action == "connection":
            from .diagnostics import check_connections
            return check_connections(self.root, self.settings.get("pod_id"))
        if action == "pod":
            return RunPodController(self.settings["RUNPOD_API_KEY"]).status(self.settings["pod_id"])
        if action in {"runtime_check", "runtime_prepare"}:
            from .desktop_runtime import runtime
            if action == "runtime_prepare" and data.get("approved") is not True:
                raise ValueError("explicit runtime preparation approval required")
            if action == "runtime_prepare" and self.factory.store.gpu_locked(self.settings.get("pod_id", "")):
                raise ValueError("Pod is reserved by a job; reconcile and finish it before preparing runtime")
            return runtime(self.settings, self.root, prepare=action == "runtime_prepare",
                           training_stage=data.get("training_stage", "sft"))
        if action == "live_logs":
            from .desktop_runtime import live_logs
            return live_logs(self.settings, self.root, self.job_directory(data["job_id"]))
        if action == "job_progress":
            directory = self.job_directory(data["job_id"])
            if data.get("remote") is True:
                from .desktop_runtime import read_progress
                return read_progress(self.settings, directory)
            path = directory / "output/trainer_log.jsonl"
            if not path.is_file():
                return parse_progress("")
            with path.open("rb") as handle:
                handle.seek(max(0, path.stat().st_size - 2 * 1024 * 1024))
                return parse_progress(handle.read().decode("utf-8", errors="replace"))
        if action == "history":
            return self.history(data)
        if action == "compare":
            return compare_runs(data["base"], data["candidate"])
        raise ValueError("unsupported desktop action")

    @staticmethod
    def require_approval(data):
        if data.get("approved") is not True:
            raise ValueError("explicit execution and dataset-transfer approval required")

    def storage(self):
        from .storage import S3Storage
        return S3Storage(self.settings)

    def hub(self):
        return ModelHub(token=self.settings.get("HF_TOKEN"))

    def history(self, data):
        from mlflow import MlflowClient

        from .auth import configure_oidc
        configure_oidc()
        client = MlflowClient(tracking_uri=self.settings["MLFLOW_TRACKING_URI"])
        if not data.get("experiment_id"):
            experiments = client.search_experiments(max_results=100)
            return {"experiments": [{"id": x.experiment_id, "name": x.name} for x in experiments]}
        experiment = str(data["experiment_id"])
        if not experiment.isdigit():
            raise ValueError("invalid experiment ID")
        runs = client.search_runs([experiment], max_results=50, order_by=["start_time DESC"],
                                  page_token=data.get("page_token"))
        result = []
        for run in runs:
            def parameter(name, default):
                try:
                    return json.loads(run.data.params.get(name, "null")) or default
                except (ValueError, TypeError):
                    return default
            config = parameter("config", {})
            validation = parameter("validation_dataset", {})
            result.append({"id": run.info.run_id, "status": run.info.status, "start_time": run.info.start_time,
                           "ai_id": run.data.tags.get("factory.ai_id", ""),
                           "operator": run.data.tags.get("factory.operator", ""),
                           "simulation": run.data.tags.get("factory.simulation", "true") != "false",
                           "synthetic": parameter("dataset", {}).get("synthetic", False),
                           "validation_sha256": validation.get("sha256"),
                           "evaluation_kind": config.get("evaluation_profile", {}).get("kind"),
                           "training_stage": config.get("training_stage", "sft"),
                           "metrics": run.data.metrics, "config": config,
                           "url": self.settings["MLFLOW_TRACKING_URI"].rstrip("/") +
                                  f"/#/experiments/{experiment}/runs/{run.info.run_id}"})
        return {"runs": result, "next_page_token": getattr(runs, "token", None)}


def main():
    # One request per process: authentication environment is never shared across profiles.
    response = None
    try:
        request = json.loads(sys.stdin.readline())
        if not isinstance(request, dict):
            raise ValueError("invalid request")
        with contextlib.redirect_stdout(sys.stderr):
            service = DesktopService(request["root"], request.get("settings"))
            response = {"ok": True, "result": service.dispatch(request["action"], request.get("data", {}))}
    except Exception as exc:
        # Avoid HTTP/SSH exception details that can contain credentials or request headers.
        message = str(exc) if isinstance(exc, (ValueError, KeyError, FileNotFoundError)) else type(exc).__name__
        for value in (request.get("settings", {}) if isinstance(locals().get("request"), dict) else {}).values():
            if isinstance(value, str) and len(value) >= 8:
                message = message.replace(value, "[redacted]")
        response = {"ok": False, "error": message[:2000]}
    sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
