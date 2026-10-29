"""Narrow JSON sidecar API for the Windows application; never a shell or HTTP server."""
import contextlib
import json
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path

import yaml

from .capabilities import compatibility, model_catalog, training_stages
from .config import AIConfig
from .connections import ENV_SETTINGS
from .desktop_inspection import inspect_dataset, parameter_changes, parse_progress
from .execution import ExecutionUncertain, Policy, RunPodController
from .gpu_servers import ServerApprovalReceipt, SSHServerInspection, server_settings
from .manifest import resolve, source_hash
from .model_hub import ModelHub, register_model, registered_models
from .registry import canonical, digest, parse_dataset, safe_name
from .runner import Factory
from .runtime_profiles import engine_profile

PROVIDERS = ("existing_runpod", "ssh_server")
# Withheld for unauthenticated MLflow servers, even if a caller still sends them.
MLFLOW_AUTH_SETTINGS = ("MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
                        "MLFLOW_TRACKING_TOKEN")
BASIC_AUTH_ENV = ("MLFLOW_TRACKING_USERNAME", "MLFLOW_TRACKING_PASSWORD")
# jobs.server_destination only: the public route without key path, key hash or host pin. server_inspect,
# connection and runtime_reservations return full transports (with key_reference_sha256 and the pin)
# because the approved inspection must round-trip into runtime_prepare.
PUBLIC_SERVER_DESTINATION = {"ssh_host", "ssh_port", "ssh_username", "remote_root", "selected_gpu_uuids"}
DESTINATION_CHANGED = "연결 설정이 실행 계획 이후 바뀌었습니다. 실행 계획을 다시 확인하세요."
UNBOUND_SERVER_JOB = ("GPU 서버용 승인 기록이 없는 작업입니다. "
                      "GPU 서버 연결에서 실행 계획을 다시 확인하고 제출하세요.")
SERVER_BOUND_JOB = "이 작업은 GPU 서버용으로 승인되었습니다. 연결 대상을 그 GPU 서버로 바꾼 뒤 실행하세요."
UNREADABLE_RECEIPT = "GPU 서버 승인 기록을 읽을 수 없습니다. 작업을 다시 계획·제출하세요."
CROSS_PROVIDER_LOCK = ("다른 연결 대상(RunPod 등)의 끝나지 않은 작업 잠금이 남아 있어 GPU 서버를 사용할 수 없습니다. "
                       "실행 관리에서 해당 작업을 재연결·결과 회수하거나, 원래 프로세스가 끝난 것을 확인한 뒤 "
                       "CLI release-lock으로 해제하세요.")
PRACTICE_SUBMIT_REFUSAL = "연습용 데이터(tutorial_sample)로는 실제 학습을 시작할 수 없습니다."
SERVER_RESERVED = ("GPU 서버가 작업이나 다른 환경 준비에 예약되어 있습니다. "
                   "작업을 재조정하거나 준비 예약을 해제한 뒤 다시 준비하세요.")
SERVER_PREPARE_IN_EXECUTION = ("GPU 서버는 실행 승인에서 학습 환경을 함께 준비하지 않습니다. 연결 설정에서 GPU 목록을 "
                               "조회한 뒤 '학습 환경 준비'를 먼저 누르세요.")
STALE_JOB = ("앱이 업데이트되어 이 작업은 실행할 수 없습니다. 같은 설정으로 실행 계획을 다시 확인해 새로 제출하세요. "
             "기존 작업은 '취소 요청'으로 정리할 수 있습니다.")
POD_RESERVED_FOR_PREPARATION = ("다른 작업이 이 Pod를 사용 중이라 학습 환경을 준비할 수 없습니다. 실행 관리에서 그 작업을 "
                                "재연결·결과 회수해 끝낸 뒤 다시 시도하세요.")


def assets_root():
    if hasattr(sys, "_MEIPASS"):
        return Path(sys._MEIPASS) / "assets"
    return Path(__file__).resolve().parents[2]


TUTORIAL_DATASET_ID = "tutorial_sample"
TUTORIAL_REFUSAL = "tutorial_sample은 앱에 포함된 연습용 합성 데이터만 GPU 전송 없이 등록할 수 있습니다."


def tutorial_sample_path():
    return assets_root() / "examples" / "tutorial_sample.json"


def tutorial_copy_matches(copy, raw):
    try:
        return copy.is_file() and copy.read_bytes() == raw
    except OSError:  # Still locked by the concurrent writer.
        return False


def reserved_tutorial_id(data):
    # Windows folders ignore case and trailing dots, so those spelling variants share the reserved name.
    return str(data.get("dataset_id", "")).casefold().rstrip(".") == TUTORIAL_DATASET_ID


def check_tutorial_registration(data):
    """The practice ID only ever names the bundled synthetic sample, never transferable data."""
    path = Path(str(data.get("path", "")))
    if (data.get("dataset_id") != TUTORIAL_DATASET_ID or data.get("version") != "1"
            or data.get("kind", "instruction") != "instruction" or data.get("synthetic") is not True
            or data.get("export_allowed") is True or not path.is_file()
            or digest(path.read_bytes()) != digest(tutorial_sample_path().read_bytes())):
        raise ValueError(TUTORIAL_REFUSAL)


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
        if self.settings.get("auth_mode") == "none":
            # Defense in depth: native settings also stop sending these for unauthenticated servers.
            self.settings = {key: value for key, value in self.settings.items() if key not in MLFLOW_AUTH_SETTINGS}
            # Inherited HTTP Basic credentials are never sent either (as the graph controller pops them), so they
            # cannot make a local HTTP server refused now that they count as credentials (11.5 R1 m5).
            for key in BASIC_AUTH_ENV:
                os.environ.pop(key, None)
        for key in ENV_SETTINGS:
            os.environ.pop(key, None)
            if self.settings.get(key):
                os.environ[key] = str(self.settings[key])
        from .auth import require_secure_artifacts, require_secure_tracking
        from .tracking import LazyMLflowTracker
        uri = self.settings.get("MLFLOW_TRACKING_URI")
        require_secure_tracking(uri)
        require_secure_artifacts(self.settings.get("FACTORY_MLFLOW_ARTIFACT_LOCATION"))
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

    def is_server(self):
        """Validated provider choice; a missing or empty provider is a legacy RunPod setting."""
        provider = self.settings.get("provider") or "existing_runpod"
        if provider not in PROVIDERS:
            raise ValueError("unsupported execution provider")
        return provider == "ssh_server"

    def require_tracking_ready(self):
        """Fail before any remote work when the configured MLflow OIDC values are incomplete."""
        if self.settings.get("MLFLOW_TRACKING_URI"):
            from .auth import require_complete_oidc
            require_complete_oidc()

    def require_server(self):
        if not self.is_server():
            raise ValueError("GPU 서버 연결을 선택한 경우에만 사용할 수 있습니다.")

    def compatibility(self, config, observation, stage):
        """gpu_compat findings for one job config; catalog sizes feed the memory estimate."""
        from .gpu_compat import evaluate
        from .runtime_profiles import runtime_capabilities
        profile = engine_profile(config.get("training_stage", "sft"))["profile"]
        model = next((item for item in model_catalog() if item["id"] == config.get("base_model")), None)
        return evaluate(config, observation, runtime_capabilities(profile), model, stage=stage)

    def plan_compatibility(self, config):
        """Offline: the plan judges the last stored GPU observation of the saved destination."""
        from .desktop_runtime import observation_key
        from .gpu_compat import load_observation
        try:
            key = observation_key(self.settings)
        except (ValueError, TypeError, RuntimeError, OSError):   # incomplete connection settings: nothing was observed yet
            key = None
        observation = load_observation(self.root, key) if key else None
        try:
            findings = self.compatibility(config, observation, "plan")
        except (TypeError, ValueError, KeyError):   # a stored file with wrong value types counts as missing
            observation = None
            findings = self.compatibility(config, None, "plan")
        return {"observed_at": (observation or {}).get("observed_at"), "findings": findings}

    def job_manifest(self, job_id):
        """The stored manifest, refusing a job submitted by another app version before any contact."""
        manifest = self.factory.store.get(job_id)["manifest"]
        if "source_sha256" in manifest and manifest["source_sha256"] != source_hash():
            raise ValueError(STALE_JOB)
        return manifest

    def preflight_config(self, data):
        """The job config (job_id) or plan config a runtime check should judge, validated before any contact."""
        if data.get("job_id"):
            self.job_directory(data["job_id"])
            return self.job_manifest(data["job_id"])["config"]
        if data.get("config"):
            return AIConfig.model_validate(data["config"]).model_dump(mode="json")
        return None

    def add_findings(self, report, config):
        if config is not None and report.get("observation") is not None:
            report["findings"] = self.compatibility(config, report["observation"], "execute")

    def execution_gate(self, config):
        from .gpu_compat import refusal

        def gate(observation):
            message = refusal(self.compatibility(config, observation, "execute"))
            if message:
                raise ValueError(message)
        return gate

    def server_destination(self):
        """Offline (destination_sha256, issue) of the saved server settings. Never opens SSH."""
        try:
            config = server_settings(self.settings)
            planned = config.destination_sha256
        except (ValueError, TypeError, RuntimeError, OSError):
            # Validation details can echo local paths; the connection screen shows the fields.
            return None, "GPU 서버 연결 설정이 비어 있거나 올바르지 않습니다. 연결 화면에서 확인하세요."
        if not config.selected_gpu_uuids:
            return None, "학습에 사용할 GPU를 연결 화면에서 선택하세요."
        return planned, None

    def job_receipt(self, job_id, server):
        """The job's frozen server destination, refusing a job routed to the other provider.
        An unreadable stored receipt fails closed with a fixed message, never validation text."""
        try:
            receipt = self.factory.store.server_receipt(job_id)
        except ValueError:
            raise ValueError(UNREADABLE_RECEIPT) from None
        if server and receipt is None:
            raise ValueError(UNBOUND_SERVER_JOB)
        if not server and receipt is not None:
            raise ValueError(SERVER_BOUND_JOB)  # Before any RunPod API call or SSH connection.
        return receipt

    def server_destinations(self, jobs):
        """Public route per listed job from one bulk read: the route, None (no receipt) or an invalid marker."""
        stored = self.factory.store.raw_server_receipts(job["id"] for job in jobs)
        result = {}
        for job_id, raw in stored.items():
            try:
                transport = ServerApprovalReceipt.model_validate_json(raw).profile.transport
            except ValueError:
                result[job_id] = {"invalid": True}  # Still server-bound; never shown as RunPod (null).
            else:
                result[job_id] = transport.model_dump(mode="json", include=PUBLIC_SERVER_DESTINATION)
        return result

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
        if action == "tutorial_sample":
            # The frozen onefile asset folder disappears after this process; later requests read the state copy.
            raw = tutorial_sample_path().read_bytes()
            copy = self.root / "tutorial" / "tutorial_sample.json"
            if not copy.is_file() or copy.read_bytes() != raw:
                copy.parent.mkdir(parents=True, exist_ok=True)
                temporary = None
                try:
                    with tempfile.NamedTemporaryFile(dir=copy.parent, suffix=".tmp", delete=False) as handle:
                        temporary = Path(handle.name)
                        handle.write(raw)
                    os.replace(temporary, copy)
                except PermissionError:
                    # A concurrent process may hold the target; success only if it already holds the bundle.
                    for _ in range(5):
                        if tutorial_copy_matches(copy, raw):
                            break
                        time.sleep(0.1)
                    else:
                        raise
                finally:
                    if temporary is not None:
                        temporary.unlink(missing_ok=True)
            return {"dataset_id": TUTORIAL_DATASET_ID, "version": "1", "kind": "instruction",
                    "path": str(copy), "sha256": digest(raw), "rows": len(parse_dataset(raw, "instruction")[0])}
        if action == "dataset_preview":
            return inspect_dataset(data["path"], data.get("kind", "instruction"))
        if action == "dataset":
            source = Path(data["path"])
            if reserved_tutorial_id(data):
                check_tutorial_registration(data)
                source = tutorial_sample_path()  # Read the verified bundle, not a path that could be swapped.
            return self.factory.registry.register_dataset(
                data["dataset_id"], data["version"], source,
                export_allowed=data.get("export_allowed") is True, synthetic=data.get("synthetic") is True,
                kind=data.get("kind", "instruction"))
        if action == "s3_list":
            return self.storage().list_objects(data["bucket"], data.get("prefix", ""),
                                               data.get("continuation_token"))
        if action == "s3_head":
            return {**self.storage().head(data["bucket"], data["key"], data.get("version_id")),
                    "import_max_bytes": 32 * 1024 * 1024, "free_bytes": shutil.disk_usage(self.root).free}
        if action == "s3_import":
            if reserved_tutorial_id(data):
                raise ValueError(TUTORIAL_REFUSAL)
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
            server = self.is_server()
            config = AIConfig.model_validate(data["config"])
            manifest = self.manifest(config)
            estimate = self.factory.policy.check(config, manifest["dataset"])
            result = {"dry_run": True, "manifest": manifest, "estimated_cost_usd": estimate,
                      "note": "저장·전송·대기 비용 별도. 실행 버튼 전에는 GPU 작업을 시작하지 않습니다."}
            if server:
                # Offline only (no SSH or provider API); submit refuses a different destination.
                result["destination_sha256"], result["destination_issue"] = self.server_destination()
            result["compatibility"] = self.plan_compatibility(manifest["config"])
            return result
        if action == "submit":
            self.require_approval(data)
            self.require_tracking_ready()
            server = self.is_server()
            if server:
                planned, issue = self.server_destination()
                if planned is None:
                    raise ValueError(issue)  # The same issue plan reports for the current settings.
                if data.get("destination_sha256") != planned:
                    raise ValueError(DESTINATION_CHANGED)
            config = AIConfig.model_validate(data["config"])
            if any(reserved_tutorial_id({"dataset_id": identifier}) for identifier in
                   (config.dataset_id, config.evaluation_profile.dataset_id or "")):
                raise ValueError(PRACTICE_SUBMIT_REFUSAL)  # Before any registry read or SSH connection.
            manifest = self.manifest(config)
            receipt = None
            if server:
                from .desktop_runtime import plan_server_destination
                # Offline refusals first; the price check needs the observed server facts below.
                for dataset in filter(None, (manifest["dataset"], manifest.get("validation_dataset"))):
                    self.factory.policy.check(config, dataset)
                    if not dataset.get("export_allowed"):
                        raise ValueError("dataset export has not been authorized in registry")
                # Read-only observation; the receipt freezes the approved destination for this job.
                receipt = plan_server_destination(self.settings, manifest)
            facts = receipt.facts if receipt else None
            self.factory.policy.check(config, manifest["dataset"], execute=True, approved_transfer=True,
                                      server_facts=facts)
            if manifest.get("validation_dataset"):
                self.factory.policy.check(config, manifest["validation_dataset"], execute=True,
                                          approved_transfer=True, server_facts=facts)
            return self.factory.submit(config, request_key=data.get("request_key"), simulation=False,
                                       server_receipt=receipt)
        if action == "execute":
            self.require_approval(data)
            self.require_tracking_ready()
            from .desktop_runtime import executor_for
            server = self.is_server()
            prepare = data.get("prepare_runtime") is True
            if prepare and server:
                raise ValueError(SERVER_PREPARE_IN_EXECUTION)
            job_id = data["job_id"]
            self.job_directory(job_id)
            config = self.job_manifest(job_id)["config"]
            stage = config.get("training_stage", "sft")
            if prepare and self.factory.store.gpu_locked("existing_runpod:" + self.settings.get("pod_id", "")):
                raise ValueError(POD_RESERVED_FOR_PREPARATION)
            gate = self.execution_gate(config)
            receipt = self.job_receipt(job_id, server)
            if server:
                with executor_for(self.settings, self.root, training_stage=stage,
                                  server_receipt=receipt, gate=gate) as executor:
                    result = self.factory.work(executor=executor, gpu=executor.server_facts.exclusivity_key,
                                               approved_transfer=True, select_mode=True, target_job_id=job_id)
                    if result is None and self.factory.store.foreign_provider_lock(
                            executor.server_facts.exclusivity_key):
                        raise ValueError(CROSS_PROVIDER_LOCK)
                    return result
            with executor_for(self.settings, self.root, training_stage=stage, gate=gate,
                              prepare_runtime=prepare) as executor:
                return self.factory.work(executor=executor, gpu="existing_runpod:" + self.settings["pod_id"],
                                         approved_transfer=True, select_mode=True, target_job_id=job_id)
        if action == "jobs":
            jobs = self.factory.store.list()[-200:][::-1]
            destinations = self.server_destinations(jobs)
            for job in jobs:
                job["server_destination"] = destinations.get(job["id"])
            return jobs
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
            server = self.is_server()
            self.job_directory(data["job_id"])
            receipt = self.job_receipt(data["job_id"], server)
            if server:
                # Recovery binds the original server, transport and attempt record, not current inventory.
                with executor_for(self.settings, self.root, preflight=False, server_receipt=receipt,
                                  recovery=True) as executor:
                    return self.factory.reconcile(data["job_id"], executor)
            with executor_for(self.settings, self.root, preflight=False) as executor:
                return self.factory.reconcile(data["job_id"], executor)
        if action == "resume":
            self.require_approval(data)
            directory = self.job_directory(data["job_id"])
            checkpoint = safe_name(data["checkpoint"])
            # A job from another app version is refused before resume could reset it to QUEUED.
            self.job_manifest(data["job_id"])
            # Destination checks precede resume, which moves the job back to QUEUED.
            server = self.is_server()
            receipt = self.job_receipt(data["job_id"], server)
            if server:
                from .desktop_runtime import validate_server_receipt
                validate_server_receipt(self.settings, receipt)
            self.factory.resume(data["job_id"], directory / "output" / checkpoint)
            return self.factory.store.get(data["job_id"])
        if action == "connection":
            from .diagnostics import check_connections
            if self.is_server():
                return check_connections(self.root, server=self.settings)
            return check_connections(self.root, self.settings.get("pod_id"))
        if action == "pod":
            if self.is_server():
                raise ValueError("GPU 서버 연결에는 Pod가 없습니다. 선택한 GPU 정보는 server_facts로 확인하세요.")
            return RunPodController(self.settings["RUNPOD_API_KEY"]).status(self.settings["pod_id"])
        if action == "server_inspect":
            self.require_server()
            from .desktop_runtime import inspect_server
            return inspect_server(self.settings).model_dump(mode="json")
        if action == "server_facts":
            self.require_server()
            from .desktop_runtime import connection
            with connection(self.settings) as (facts, _):
                return facts.model_dump(mode="json")
        if action in {"runtime_check", "runtime_prepare"}:
            from .desktop_runtime import runtime
            server = self.is_server()
            if action == "runtime_prepare" and data.get("approved") is not True:
                raise ValueError("explicit runtime preparation approval required")
            if action == "runtime_prepare" and server:
                if not data.get("inspection"):
                    raise ValueError("환경 준비 승인 화면에서 확인한 GPU 서버 조회 결과(inspection)가 필요합니다. "
                                     "GPU 서버를 다시 조회하세요.")
                inspection = SSHServerInspection.model_validate(data["inspection"])
                # Same alias rules as claims: this machine ID or any of its GPU UUIDs, plus the v1
                # cross-provider and unknown-lock policy. reserve_preparation re-checks with fresh facts.
                if self.factory.store.resource_blocked(inspection):
                    raise ValueError(CROSS_PROVIDER_LOCK if self.factory.store.foreign_provider_lock(
                        inspection.exclusivity_key) else SERVER_RESERVED)
                return runtime(self.settings, self.root, prepare=True,
                               training_stage=data.get("training_stage", "sft"), inspection=inspection)
            if action == "runtime_prepare" and self.factory.store.gpu_locked("existing_runpod:" + self.settings.get("pod_id", "")):
                raise ValueError(POD_RESERVED_FOR_PREPARATION)
            config = self.preflight_config(data) if action == "runtime_check" else None
            stage = (config or {}).get("training_stage") or data.get("training_stage", "sft")
            report = runtime(self.settings, self.root, prepare=action == "runtime_prepare", training_stage=stage)
            if action == "runtime_check":
                self.add_findings(report, config)
            return report
        if action == "runtime_reservations":
            from .desktop_runtime import runtime_reservations
            return runtime_reservations(self.root)
        if action == "runtime_release":
            self.require_server()
            if data.get("approved") is not True or data.get("confirm_preparation_stopped") is not True:
                raise ValueError("환경 준비 예약 해제에는 승인과 원래 GPU 서버의 준비 명령 종료 확인이 모두 필요합니다.")
            reservation_id = data.get("reservation_id")
            if not isinstance(reservation_id, str) or not re.fullmatch(r"[0-9a-f]{32}", reservation_id):
                raise ValueError("invalid preparation reservation ID")
            from .desktop_runtime import release_runtime_reservation
            return release_runtime_reservation(self.settings, self.root, reservation_id,
                                               confirm_preparation_stopped=True)
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

        from .auth import configure_oidc, require_secure_tracking
        require_secure_tracking(self.settings["MLFLOW_TRACKING_URI"])
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


def _request_line() -> str:
    """The request line as UTF-8. The native bridge writes raw UTF-8 JSON, while a text stdin on Korean Windows
    decodes with the cp949 code page and garbles Hangul (11.6). A stdin without a byte buffer is read as text."""
    buffer = getattr(sys.stdin, "buffer", None)
    return buffer.readline().decode("utf-8") if buffer is not None else sys.stdin.readline()


def main():
    # One request per process: authentication environment is never shared across profiles.
    response = None
    try:
        request = json.loads(_request_line())
        if not isinstance(request, dict):
            raise ValueError("invalid request")
        with contextlib.redirect_stdout(sys.stderr):
            if request.get("channel") == "workflow":
                # The Task 9 workflow channel: strict models, public settings and scoped secrets only. It never
                # builds DesktopService and never exports settings to the process environment.
                from .desktop_workflows import handle
                response = {"ok": True, "result": handle(request)}
            else:
                service = DesktopService(request["root"], request.get("settings"))
                response = {"ok": True, "result": service.dispatch(request["action"], request.get("data", {}))}
    except Exception as exc:
        # Avoid HTTP/SSH exception details that can contain credentials or request headers. Uncertain
        # execution/preparation and remote timeouts carry fixed operator guidance (e.g. a retained reservation).
        message = str(exc) if isinstance(exc, (ValueError, KeyError, FileNotFoundError, ExecutionUncertain,
                                               TimeoutError)) else type(exc).__name__
        sent = locals().get("request")
        settings = request.get("settings", {}) if isinstance(sent, dict) else {}
        if isinstance(sent, dict) and sent.get("channel") == "workflow" and not isinstance(settings, dict):
            settings = {}   # the workflow envelope error is answered; the legacy path is unchanged
        values = list(settings.values())
        if isinstance(sent, dict) and isinstance(sent.get("secrets"), dict):
            values.extend(sent["secrets"].values())   # workflow channel: the action's scoped secrets
        if isinstance(sent, dict) and isinstance(sent.get("profiles"), list):
            values.extend(value for profile in sent["profiles"] if isinstance(profile, dict)
                          and isinstance(profile.get("fields"), dict) for value in profile["fields"].values())
        for value in values:
            if isinstance(value, str) and len(value) >= 8:
                # OS errors quote paths with repr(), doubling Windows backslashes.
                for form in (repr(value)[1:-1], value):
                    message = message.replace(form, "[redacted]")
        response = {"ok": False, "error": message[:2000]}
    sys.stdout.write(json.dumps(response, ensure_ascii=True) + "\n")
    sys.stdout.flush()


if __name__ == "__main__":
    main()
