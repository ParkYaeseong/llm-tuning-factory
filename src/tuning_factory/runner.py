"""One common pipeline for all AIs. Durable evidence stays on the control plane."""
import json
import threading
import time
from pathlib import Path

from .config import AIConfig
from .evaluation import CommonEvaluator
from .execution import CapacityUnavailable, ExecutionUncertain, LocalExecutor, Policy
from .jobs import JobStore
from .manifest import resolve, source_hash, verify_manifest
from .registry import Registry, canonical, file_digest, immutable_write
from .training import CanceledError, LlamaFactoryBackend, MockTrainingBackend


class Factory:
    def __init__(self, root="state", policy=None, tracker=None):
        self.root = Path(root).resolve()
        self.registry = Registry(self.root / "registry")
        self.store = JobStore(self.root / "jobs.db")
        self.policy = policy or Policy()
        self.tracker = tracker

    def directory(self, job_id):
        # Resolve only persisted IDs, never arbitrary caller paths.
        self.store.get(job_id)
        return self.root / "jobs" / job_id

    def submit(self, config, request_key=None, priority=0, simulation=True):
        manifest = resolve(config, self.registry, simulation=simulation)
        self.policy.check(config, manifest["dataset"])
        job = self.store.submit(manifest, request_key or manifest["sha256"], priority)
        directory = self.directory(job["id"])
        immutable_write(directory / "manifest.json", canonical(manifest))
        if job["status"] == "PENDING":
            self.store.transition(job["id"], "QUEUED")
        return self.store.get(job["id"])

    def resume(self, job_id, checkpoint=None):
        job = self.store.get(job_id)
        directory = self.directory(job_id)
        verify_manifest(job["manifest"])
        from .capabilities import compatibility
        if not compatibility(job["manifest"]["config"])["resume_supported"]:
            raise ValueError("checkpoint resume is unsupported for this training stage/method")
        if checkpoint:
            path = Path(checkpoint).resolve()
            if not path.is_relative_to(directory / "output") or not path.is_dir():
                raise ValueError("checkpoint must be inside original job output")
            hashes = self.verify_artifacts(job_id)
            checkpoint_files = [p for p in path.rglob("*") if p.is_file()]
            if not checkpoint_files or any(p.relative_to(directory).as_posix() not in hashes for p in checkpoint_files):
                raise ValueError("checkpoint provenance missing from preserved artifact inventory")
            (directory / "resume.json").write_bytes(canonical({"checkpoint": path.relative_to(directory).as_posix()}))
        self.store.reset_for_resume(job_id)

    def work(self, executor=None, backend=None, gpu="mock-cpu", approved_transfer=False, select_mode=False, target_job_id=None):
        executor = executor or LocalExecutor()
        job = self.store.claim(gpu, self.policy.max_concurrent, self.policy.max_gpus,
                               simulation=not executor.remote if select_mode else None, job_id=target_job_id)
        if job is None:
            return None
        job_id = job["id"]
        directory = self.directory(job_id)
        manifest = job["manifest"]
        backend = backend or (MockTrainingBackend() if manifest["simulation"] else LlamaFactoryBackend())
        status, error, metrics, uncertain = "FAILED", None, {}, False
        stop_heartbeat = threading.Event()

        def heartbeat():
            while not stop_heartbeat.wait(10):
                self.store.heartbeat(job_id)

        thread = threading.Thread(target=heartbeat, daemon=True)
        thread.start()
        run_id = None
        try:
            verify_manifest(manifest)
            if manifest["source_sha256"] != source_hash():
                raise ValueError("source hash differs from immutable manifest; submit a new job")
            config = AIConfig.model_validate(manifest["config"])
            if manifest["simulation"] and (executor.remote or not isinstance(backend, MockTrainingBackend)):
                raise ValueError("simulation manifest cannot execute real training")
            if not manifest["simulation"] and isinstance(backend, MockTrainingBackend):
                raise ValueError("real manifest cannot be executed by mock backend")
            if not manifest["simulation"] and not executor.remote:
                raise ValueError("real training requires the approved RunPod executor")
            dataset = self.registry.get_dataset(config.dataset_id, config.dataset_version)
            self.policy.check(config, dataset, execute=executor.remote, approved_transfer=approved_transfer)
            executor.validate_config(config)
            if dataset != manifest["dataset"]:
                raise ValueError("dataset registration differs from manifest")
            if self.tracker:
                run_id = self.tracker.start(job_id, manifest)
                self.store.set_run_id(job_id, run_id)
            backend.prepare(config, directory, self.registry.dataset_path(dataset).read_bytes())
            validation = manifest.get("validation_dataset")
            if validation:
                actual = self.registry.get_dataset(validation["dataset_id"], validation["version"])
                if actual != validation:
                    raise ValueError("validation data differs from immutable manifest")
                self.policy.check(config, actual, execute=executor.remote, approved_transfer=approved_transfer)
                validation_kind = "preference" if config.evaluation_profile.kind == "reward" else "instruction"
                backend.prepare_validation(directory, self.registry.dataset_path(actual).read_bytes(), kind=validation_kind)
            if getattr(executor, "pod", None):
                pod = executor.pod
                (directory / "execution.json").write_bytes(canonical({
                    "pod_id": gpu, "gpu": pod["gpu"], "image": pod.get("image"),
                    "cost_per_hour_usd": float(pod["costPerHr"]),
                    "network_volume": pod["networkVolume"]["id"]}))
            # Remove markers from an earlier attempt, without touching its output/checkpoints.
            for name in ("result.json", "cancel", "error.txt", "tracking-error.txt"):
                (directory / name).unlink(missing_ok=True)
            checkpoint = None
            if (directory / "resume.json").exists():
                checkpoint = directory / json.loads((directory / "resume.json").read_text())["checkpoint"]
            self.store.transition(job_id, "RUNNING")
            executor.run(backend, directory, config.resource_profile.timeout_seconds,
                         lambda: bool(self.store.get(job_id)["cancel_requested"]), checkpoint)
            if self.store.get(job_id)["cancel_requested"]:
                raise CanceledError("cancellation requested")
            self.store.transition(job_id, "EVALUATING")
            metrics, quality = self.evaluate(directory, config, manifest["simulation"],
                                             (manifest.get("validation_dataset") or {}).get("sha256"))
            if dataset.get("synthetic") or (manifest.get("validation_dataset") or {}).get("synthetic"):
                quality["quality_status"] = "synthetic_only"
                quality["note"] = "Synthetic data verifies pipeline mechanics only; not actual AI quality."
            (directory / "evaluation.json").write_bytes(canonical(quality))
            (directory / "artifacts.json").write_bytes(canonical(backend.collect_artifacts(directory)))
            if not manifest["simulation"] and not manifest["dataset"].get("synthetic"):
                candidate = {"job_id": job_id, "manifest_sha256": manifest["sha256"],
                             "config": manifest["config"], "run_id": run_id, "approval": "pending",
                             "quality_status": quality["quality_status"],
                             "artifacts": json.loads((directory / "artifacts.json").read_text())}
                immutable_write(self.root / "candidates" / f"{job_id}-{job['attempts']}.json", canonical(candidate))
            status = "COMPLETED"
        except CanceledError as exc:
            status, error = "CANCELED", str(exc)
        except CapacityUnavailable as exc:
            error = str(exc)
            if job["attempts"] < self.policy.max_attempts:
                status = "RETRYING"
        except ExecutionUncertain as exc:
            error, uncertain = str(exc), True
        except Exception as exc:
            error = f"{type(exc).__name__}: {exc}"
        finally:
            stop_heartbeat.set()
            thread.join(timeout=2)
            if error:
                (directory / "error.txt").write_text(error, encoding="utf-8")
            (directory / "artifacts.json").write_bytes(canonical(backend.collect_artifacts(directory)))
            if self.tracker and run_id:
                try:
                    self.tracker.finish(run_id, metrics, directory, status, error)
                    if status == "COMPLETED" and not manifest["simulation"] and not manifest["dataset"].get("synthetic"):
                        quality = json.loads((directory / "evaluation.json").read_text())
                        self.tracker.register_candidate(run_id, manifest, quality["quality_status"] == "qualified")
                except Exception as exc:
                    (directory / "tracking-error.txt").write_text(str(exc), encoding="utf-8")
                    status, error = "FAILED", "MLflow persistence failed; training artifacts retained; see tracking-error.txt"
            # Preserve attempt evidence before any retry/resume overwrites live logs.
            archive = directory / "attempts" / str(job["attempts"])
            archive.mkdir(parents=True, exist_ok=True)
            for name in ("train.log", "train.yaml", "error.txt", "evaluation.json", "tracking-error.txt", "runtime.json", "execution.json"):
                if (directory / name).is_file():
                    (archive / name).write_bytes((directory / name).read_bytes())
            (archive / "outcome.json").write_bytes(canonical({"status": status, "error": error,
                                                             "run_id": run_id, "timestamp": time.time()}))
            try:
                self.store.transition(job_id, status, error)
                if status == "RETRYING":
                    self.store.transition(job_id, "QUEUED", error)
            finally:
                if not uncertain:
                    self.store.release_gpu(job_id)
                backend.cleanup(directory)
        return self.store.get(job_id)

    @staticmethod
    def evaluate(directory, config, simulation, validation_sha=None):
        metrics = {"pipeline_completed": 1.0}
        quality_status = "not_evaluated"
        evaluator = CommonEvaluator()
        profile = config.evaluation_profile
        predictions = directory / "output" / "predictions.json"
        reward_metrics = directory / "output" / "reward_metrics.json"
        if not simulation and profile.kind == "reward" and reward_metrics.is_file() and validation_sha:
            data = json.loads(reward_metrics.read_text())
            if data.get("validation_sha256") != validation_sha:
                raise ValueError("reward metrics do not match registered validation data")
            metrics.update({key: value for key, value in data["metrics"].items()
                            if key in {"eval_accuracy", "eval_loss"}})
            quality_status = "measured"
            if profile.thresholds:
                quality_status = "qualified" if all(key in metrics and metrics[key] >= value
                                                    for key, value in profile.thresholds.items()) else "below_threshold"
        if not simulation and predictions.is_file() and profile.kind != "pipeline" and validation_sha:
            data = json.loads(predictions.read_text())
            if data.get("validation_sha256") != validation_sha:
                raise ValueError("predictions do not match registered validation data")
            metrics.update(evaluator.evaluate(data["predictions"], data.get("references"), profile.kind))
            quality_status = "measured"
            if profile.thresholds:
                quality_status = "qualified" if all(k in metrics and metrics[k] >= v
                                                    for k, v in profile.thresholds.items()) else "below_threshold"
        if not simulation:
            results = directory / "output" / "all_results.json"
            if results.is_file():
                metrics.update({k: v for k, v in json.loads(results.read_text()).items()
                                if isinstance(v, (int, float)) and k in {"train_loss", "eval_loss", "train_runtime"}})
        evaluator.summarize_metrics(metrics)
        return metrics, {"simulation": simulation, "metrics": metrics, "quality_status": quality_status,
                         "training_stage": config.training_stage,
                         "training_success": True, "approval": "pending",
                         "note": "No task quality measured without actual model predictions and supplied criteria."}

    def verify_artifacts(self, job_id):
        directory = self.directory(job_id)
        hashes = json.loads((directory / "artifacts.json").read_text())
        for name, expected in hashes.items():
            path = (directory / name).resolve()
            if not path.is_relative_to(directory) or file_digest(path) != expected:
                raise ValueError("artifact hash mismatch")
        return hashes

    def reconcile(self, job_id, executor):
        job = self.store.get(job_id)
        if job["status"] != "FAILED":
            raise ValueError("recover stale active jobs before reconciling; only FAILED jobs are eligible")
        manifest = job["manifest"]
        verify_manifest(manifest)
        directory = self.directory(job_id)
        result = executor.collect_result(directory)
        if result is None:
            return job
        status = result.get("status")
        if status not in {"COMPLETED", "FAILED", "CANCELED"}:
            raise ValueError("unknown remote completion status")
        metrics, error = {}, result.get("error")
        if status == "COMPLETED":
            config = AIConfig.model_validate(manifest["config"])
            metrics, quality = self.evaluate(directory, config, manifest["simulation"],
                                             (manifest.get("validation_dataset") or {}).get("sha256"))
            if manifest["dataset"].get("synthetic") or (manifest.get("validation_dataset") or {}).get("synthetic"):
                quality["quality_status"] = "synthetic_only"
            (directory / "evaluation.json").write_bytes(canonical(quality))
        (directory / "artifacts.json").write_bytes(canonical(LlamaFactoryBackend().collect_artifacts(directory)))
        if self.tracker and job["run_id"]:
            self.tracker.finish(job["run_id"], metrics, directory, status, error)
            if status == "COMPLETED" and not manifest["simulation"] and not manifest["dataset"].get("synthetic"):
                self.tracker.register_candidate(job["run_id"], manifest, quality["quality_status"] == "qualified")
        self.store.reconcile_result(job_id, status, error)
        (directory / "reconciliation.json").write_bytes(canonical({"status": status, "run_id": job["run_id"], "at": time.time()}))
        return self.store.get(job_id)
