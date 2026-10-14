import json
import os
import time
from pathlib import Path
from typing import Annotated

import typer

from .config import load_config
from .evaluation import CommonEvaluator
from .execution import Policy, RunPodController, SSHExecutor
from .managed import ManagedPods
from .manifest import resolve
from .runner import Factory

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False,
                  help="Common LLM tuning control plane. Real execution requires explicit approval flags.")


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.callback()
def main(ctx: typer.Context, state_dir: Path = Path("state"), policy_file: Path | None = None,
         tracking_uri: str | None = None, connection_file: Path | None = None):
    connection = None
    if connection_file:
        from .connections import load_connection
        connection = load_connection(connection_file)
    policy = Policy.model_validate_json(policy_file.read_text()) if policy_file else Policy()
    tracker = None
    uri = tracking_uri or os.environ.get("MLFLOW_TRACKING_URI")
    if uri:
        from .tracking import LazyMLflowTracker
        tracker = LazyMLflowTracker(uri)
    ctx.obj = Factory(state_dir, policy=policy, tracker=tracker)
    ctx.obj.connection = connection


@app.command()
def doctor(ctx: typer.Context, pod_id: str | None = None, write_test: bool = False):
    from .diagnostics import check_connections
    connection = ctx.obj.connection
    output(check_connections(ctx.obj.root, pod_id or (connection.pod_id if connection else None), write_test))


@app.command("dataset-register")
def dataset_register(ctx: typer.Context, dataset_id: str, version: str, source: Path,
                     export_allowed: bool = False, synthetic: bool = False):
    output(ctx.obj.registry.register_dataset(dataset_id, version, source, export_allowed, synthetic))


@app.command()
def validate(config: Path):
    output({"valid": True, "config": load_config(config).model_dump()})


@app.command()
def plan(ctx: typer.Context, config: Path, real: bool = False):
    manifest = resolve(load_config(config), ctx.obj.registry, simulation=not real)
    estimate = ctx.obj.policy.check(load_config(config), manifest["dataset"])
    output({"manifest": manifest, "sha256": manifest["sha256"], "estimated_cost_usd": estimate,
            "dry_run": True, "note": "Estimate excludes storage, transfer, provisioning and idle time."})


@app.command()
def run(ctx: typer.Context, config: Path, mock: bool = False, submit: bool = False,
        real: bool = False, request_key: str | None = None, priority: int = 0):
    if mock and real:
        raise typer.BadParameter("--mock and --real are mutually exclusive")
    if not (mock or submit):
        plan(ctx, config, real)
        return
    job = ctx.obj.submit(load_config(config), request_key, priority, simulation=not real)
    if mock and job["status"] == "QUEUED":
        job = ctx.obj.work(target_job_id=job["id"])
    output(job)


@app.command()
def batch(ctx: typer.Context, configs: Annotated[list[Path], typer.Argument()], submit: bool = False,
          real: bool = False):
    if submit:
        output([ctx.obj.submit(load_config(path), simulation=not real) for path in configs])
    else:
        output({"dry_run": True, "manifests": [resolve(load_config(p), ctx.obj.registry, not real) for p in configs]})


@app.command()
def work(ctx: typer.Context, drain: bool = False, pod_id: str | None = None,
         host: str | None = None, port: int = 22, username: str = "root", key_file: Path | None = None,
         approve_execution_and_transfer: bool = False):
    factory = ctx.obj
    factory.store.recover()
    while True:
        executor = None
        if pod_id:
            if not approve_execution_and_transfer:
                raise typer.BadParameter("--approve-execution-and-transfer is required for real jobs")
            if not host or not key_file:
                raise typer.BadParameter("--host and --key-file required; verify SSH host key separately")
            controller = RunPodController(os.environ["RUNPOD_API_KEY"])
            pod = controller.status(pod_id)
            if pod.get("desiredStatus") != "RUNNING":
                output({"waiting": True, "reason": "Pod not running; start separately with explicit approval"})
                return
            # Refuse to silently exceed the declared price or resource budget.
            queued = [j for j in factory.store.list() if j["status"] == "QUEUED" and not j["manifest"]["simulation"]]
            if not queued:
                output({"idle": True})
                return
            selected = sorted(queued, key=lambda j: (-j["priority"], j["created"]))[0]
            configured = selected["manifest"]["config"]["resource_profile"]
            if "costPerHr" not in pod or float(pod["costPerHr"]) > configured["hourly_cost_usd"] * configured["gpu_count"]:
                raise typer.BadParameter("Pod live price unavailable or exceeds configured total hourly price")
            if pod.get("gpu", {}).get("count") != configured["gpu_count"]:
                raise typer.BadParameter("Pod GPU count differs from resolved resource profile")
            if configured["gpu_type"] not in {pod.get("gpu", {}).get("id"), pod.get("gpu", {}).get("displayName")}:
                raise typer.BadParameter("Pod GPU type differs from resolved resource profile")
            if pod.get("publicIp") != host or int(pod.get("portMappings", {}).get("22", 0)) != port:
                raise typer.BadParameter("SSH host/port must match this Pod's public TCP SSH mapping")
            if not pod.get("networkVolume", {}).get("id") or pod.get("volumeMountPath") != "/workspace":
                raise typer.BadParameter("verified network volume mounted at /workspace required")
            executor = SSHExecutor(host, port, username, key_file, pod=pod)
        try:
            result = factory.work(executor=executor, gpu=pod_id or "mock-cpu",
                                  approved_transfer=approve_execution_and_transfer, select_mode=True)
        finally:
            # Borrowed Pods have no ownership record and are never changed here.
            remaining = any(j["status"] == "QUEUED" and not j["manifest"]["simulation"] for j in factory.store.list())
            if (pod_id and (not drain or not remaining) and
                    (factory.root / "managed-pods" / f"{pod_id}.json").is_file()):
                ManagedPods(factory.root, controller, factory.policy).stop(pod_id)
        output(result or {"idle": True})
        if not drain or result is None or result["status"] == "QUEUED":
            break


@app.command()
def status(ctx: typer.Context, job_id: str | None = None):
    output(ctx.obj.store.get(job_id) if job_id else ctx.obj.store.list())


@app.command()
def logs(ctx: typer.Context, job_id: str):
    directory = ctx.obj.directory(job_id)
    for name in ("train.log", "error.txt", "tracking-error.txt"):
        if (directory / name).exists():
            typer.echo((directory / name).read_text(encoding="utf-8", errors="replace"))


@app.command()
def cancel(ctx: typer.Context, job_id: str):
    ctx.obj.store.cancel(job_id)
    output(ctx.obj.store.get(job_id))


@app.command()
def resume(ctx: typer.Context, job_id: str, checkpoint: Path | None = None):
    ctx.obj.resume(job_id, checkpoint)
    output(ctx.obj.store.get(job_id))


@app.command()
def recover(ctx: typer.Context, stale_after_seconds: int = 120):
    output({"recovered": ctx.obj.store.recover(stale_after_seconds)})


@app.command()
def reconcile(ctx: typer.Context, job_id: str, pod_id: str, host: str, key_file: Path, port: int = 22):
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    pod = controller.status(pod_id)
    if pod.get("desiredStatus") != "RUNNING" or pod.get("publicIp") != host or int(pod.get("portMappings", {}).get("22", 0)) != port:
        raise typer.BadParameter("running Pod and matching direct SSH endpoint required for result retrieval")
    executor = SSHExecutor(host, port, "root", key_file, pod=pod)
    output(ctx.obj.reconcile(job_id, executor))


@app.command("release-lock")
def release_lock(ctx: typer.Context, job_id: str, confirm_execution_stopped: bool = False):
    if not confirm_execution_stopped:
        raise typer.BadParameter("verify remote process is stopped, then supply --confirm-execution-stopped")
    if ctx.obj.store.get(job_id)["status"] not in {"FAILED", "CANCELED", "COMPLETED"}:
        raise typer.BadParameter("cannot release an active job's lock")
    ctx.obj.store.release_gpu(job_id)
    output({"released": job_id})


@app.command()
def compare(ctx: typer.Context, job_id: str, baseline_id: str):
    def read(identifier):
        return json.loads((ctx.obj.directory(identifier) / "evaluation.json").read_text())
    current, baseline = read(job_id), read(baseline_id)
    a, b = ctx.obj.store.get(job_id), ctx.obj.store.get(baseline_id)
    if a["manifest"]["config"]["evaluation_profile"] != b["manifest"]["config"]["evaluation_profile"]:
        raise typer.BadParameter("evaluation profiles differ")
    output({"simulation": current["simulation"] or baseline["simulation"],
            "deltas": CommonEvaluator().compare_with_baseline(current["metrics"], baseline["metrics"])})


@app.command()
def evaluate(predictions: Path, kind: str = "classification"):
    data = json.loads(predictions.read_text())
    output({"source": "caller-supplied predictions and references",
            "metrics": CommonEvaluator().evaluate(data["predictions"], data.get("references"), kind)})


@app.command("pod-create")
def pod_create(ctx: typer.Context, payload: Path, approve_paid_creation: bool = False,
               hourly_quote_usd: float = 0, timeout_seconds: int = 3600):
    data = json.loads(payload.read_text())
    if not approve_paid_creation:
        output({"dry_run": True, "request": data, "requires": "--approve-paid-creation"})
        return
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    manager = ManagedPods(ctx.obj.root, controller, ctx.obj.policy)
    output(manager.create(data, hourly_quote_usd, timeout_seconds, approved=True))


@app.command("pod-start")
def pod_start(ctx: typer.Context, pod_id: str, approve_paid_start: bool = False):
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    manager = ManagedPods(ctx.obj.root, controller, ctx.obj.policy)
    record = manager.record(pod_id)
    if time.time() >= record["deadline"]:
        raise typer.BadParameter("allocation deadline expired; create a new approved allocation")
    output(controller.start(pod_id, approved=approve_paid_start))


@app.command("pod-stop")
def pod_stop(ctx: typer.Context, pod_id: str):
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    ManagedPods(ctx.obj.root, controller, ctx.obj.policy).stop(pod_id)
    output({"stopped": pod_id, "network_volume_preserved": True})


@app.command()
def guard(ctx: typer.Context, watch: bool = False, interval_seconds: int = 15):
    if not 1 <= interval_seconds <= 60:
        raise typer.BadParameter("interval must be 1..60 seconds")
    manager = ManagedPods(ctx.obj.root, RunPodController(os.environ["RUNPOD_API_KEY"]), ctx.obj.policy)
    while True:
        output({"stopped": manager.guard_once()})
        if not watch:
            return
        time.sleep(interval_seconds)


if __name__ == "__main__":
    app()
