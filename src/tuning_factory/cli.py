import contextlib
import itertools
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path
from types import MappingProxyType
from typing import Annotated, Literal

import typer
from pydantic import Field, ValidationError, model_validator

from .config import StrictModel, load_config
from .evaluation import CommonEvaluator
from .execution import Policy, RunPodController, SSHExecutor
from .managed import ManagedPods
from .manifest import resolve
from .projects import LocalId
from .runner import Factory

app = typer.Typer(no_args_is_help=True, pretty_exceptions_enable=False,
                  help="Common LLM tuning control plane. Real execution requires explicit approval flags.")


def output(value):
    typer.echo(json.dumps(value, ensure_ascii=False, indent=2))


@app.callback()
def main(ctx: typer.Context, state_dir: Path = Path("state"), policy_file: Path | None = None,
         tracking_uri: str | None = None, connection_file: Path | None = None, env_file: Path = Path(".env")):
    from .connections import load_environment
    if env_file != Path(".env") and not env_file.is_file():
        raise typer.BadParameter("Specified environment file does not exist", param_hint="--env-file")
    load_environment(env_file)
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
def doctor(ctx: typer.Context, pod_id: str | None = None, write_test: bool = False,
           server_profile: Path | None = None):
    from .diagnostics import check_connections
    connection = ctx.obj.connection
    server = server_connection(ctx, server_profile)
    if server is not None and pod_id:
        raise typer.BadParameter("--pod-id cannot be combined with an SSH server profile; choose one provider",
                                 param_hint="--pod-id")
    output(check_connections(ctx.obj.root, None if server else pod_id or (connection.pod_id if connection else None),
                             write_test, server=server))


def server_connection(ctx, path=None):
    from .connections import load_server_settings
    if path is not None:
        settings = load_server_settings(path)
    elif ctx.obj.connection and ctx.obj.connection.provider == "ssh_server":
        settings = ctx.obj.connection.ssh_server
    else:
        return None
    return {"provider": "ssh_server", **settings.model_dump(mode="json")}


@app.command("server-inspect")
def server_inspect(profile: Path):
    from .connections import load_server_settings
    from .desktop_runtime import inspect_server
    output(inspect_server({"provider": "ssh_server", **load_server_settings(profile).model_dump(mode="json")})
           .model_dump(mode="json"))


@app.command("server-runtime")
def server_runtime(ctx: typer.Context, profile: Path, prepare: bool = False,
                   approve_preparation: bool = False, training_stage: str = "sft"):
    from .desktop_runtime import inspect_server, runtime
    if prepare and not approve_preparation:
        raise typer.BadParameter("--approve-preparation required for private runtime installation")
    settings = server_connection(ctx, profile)
    inspection = inspect_server(settings) if prepare else None
    output(runtime(settings, ctx.obj.root, prepare=prepare, training_stage=training_stage, inspection=inspection))


@app.command("server-runtime-status")
def server_runtime_status(ctx: typer.Context):
    from .desktop_runtime import runtime_reservations
    output(runtime_reservations(ctx.obj.root))


@app.command("server-runtime-release")
def server_runtime_release(ctx: typer.Context, profile: Path, reservation_id: str,
                           confirm_preparation_stopped: bool = False):
    from .desktop_runtime import release_runtime_reservation
    output(release_runtime_reservation(server_connection(ctx, profile), ctx.obj.root, reservation_id,
                                       confirm_preparation_stopped=confirm_preparation_stopped))


@app.command("dataset-register")
def dataset_register(ctx: typer.Context, dataset_id: str, version: str, source: Path,
                     export_allowed: bool = False, synthetic: bool = False, kind: str = "instruction"):
    output(ctx.obj.registry.register_dataset(dataset_id, version, source, export_allowed, synthetic, kind=kind))


@app.command()
def validate(config: Path):
    output({"valid": True, "config": load_config(config).model_dump()})


@app.command()
def plan(ctx: typer.Context, config: Path, real: bool = False, server_profile: Path | None = None):
    manifest = resolve(load_config(config), ctx.obj.registry, simulation=not real)
    estimate = ctx.obj.policy.check(load_config(config), manifest["dataset"])
    result = {"manifest": manifest, "sha256": manifest["sha256"], "estimated_cost_usd": estimate,
              "dry_run": True, "note": "Estimate excludes storage, transfer, provisioning and idle time."}
    server = server_connection(ctx, server_profile)
    if server is not None and real:
        from .desktop_runtime import plan_server_destination
        result["server_receipt"] = plan_server_destination(server, manifest).model_dump(mode="json")
    output(result)


@app.command()
def run(ctx: typer.Context, config: Path, mock: bool = False, submit: bool = False,
        real: bool = False, request_key: str | None = None, priority: int = 0,
        server_profile: Path | None = None):
    if mock and real:
        raise typer.BadParameter("--mock and --real are mutually exclusive")
    if not (mock or submit):
        plan(ctx, config, real, server_profile)
        return
    receipt = None
    server = server_connection(ctx, server_profile)
    if server is not None and real:
        from .desktop_runtime import plan_server_destination
        receipt = plan_server_destination(server, resolve(load_config(config), ctx.obj.registry, simulation=False))
    if mock:
        require_secure_tracker(ctx.obj)
    job = ctx.obj.submit(load_config(config), request_key, priority, simulation=not real, server_receipt=receipt)
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


def require_secure_tracker(factory):
    """Refuse an insecure tracking setup before any job is claimed, so no attempt is consumed."""
    from .auth import require_complete_oidc, require_secure_artifacts, require_secure_tracking
    uri = getattr(factory.tracker, "uri", None)
    require_secure_tracking(uri)
    if uri:
        require_complete_oidc()
    require_secure_artifacts(os.environ.get("FACTORY_MLFLOW_ARTIFACT_LOCATION"))


@app.command()
def work(ctx: typer.Context, drain: bool = False, pod_id: str | None = None,
         host: str | None = None, port: int = 22, username: str = "root", key_file: Path | None = None,
         approve_execution_and_transfer: bool = False, server_profile: Path | None = None):
    factory = ctx.obj
    require_secure_tracker(factory)
    factory.store.recover()
    server = server_connection(ctx, server_profile)
    if server is not None:
        if pod_id:
            raise typer.BadParameter("choose one execution provider")
        server_work(factory, server, drain, approve_execution_and_transfer)
        return
    while True:
        executor = None
        selected = None
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
            queued = runpod_queue(factory.store)
            if not queued:
                output({"idle": True})
                return
            def fits(job):
                profile = job["manifest"]["config"]["resource_profile"]
                return ("costPerHr" in pod
                        and float(pod["costPerHr"]) <= profile["hourly_cost_usd"] * profile["gpu_count"]
                        and pod.get("gpu", {}).get("count") == profile["gpu_count"]
                        and profile["gpu_type"] in {pod.get("gpu", {}).get("id"), pod.get("gpu", {}).get("displayName")}
                        and profile["gpu_count"] <= factory.policy.max_gpus)
            selected = next((job for job in sorted(queued, key=lambda j: (-j["priority"], j["created"])) if fits(job)), None)
            if selected is None:
                output({"waiting": True, "reason": "no queued job fits the observed Pod and GPU budget"})
                return
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
            result = factory.work(executor=executor, gpu=("existing_runpod:" + pod_id) if pod_id else "mock-cpu",
                                  approved_transfer=approve_execution_and_transfer, select_mode=True,
                                  target_job_id=selected["id"] if selected else None)
        finally:
            # Borrowed Pods have no ownership record and are never changed here.
            remaining = bool(runpod_queue(factory.store))
            if (pod_id and (not drain or not remaining) and
                    (factory.root / "managed-pods" / f"{pod_id}.json").is_file()):
                ManagedPods(factory.root, controller, factory.policy).stop(pod_id)
        output(result or {"idle": True})
        if not drain or result is None or result["status"] == "QUEUED":
            break


def runpod_queue(store):
    """Real v1 jobs a RunPod worker may select: never one frozen to an SSH server destination."""
    bound = store.server_bound_job_ids()
    return [job for job in store.list() if job["status"] == "QUEUED" and not job["graph_run_id"]
            and not job["manifest"]["simulation"] and job["id"] not in bound]


def server_work(factory, settings, drain, approved):
    from .config import AIConfig
    from .desktop_runtime import connection, executor_for
    from .gpu_servers import ServerApprovalReceipt, server_settings
    if not approved:
        raise typer.BadParameter("--approve-execution-and-transfer is required for real jobs")
    transport = server_settings(settings).transport
    while True:
        queued = sorted((job for job in factory.store.list() if job["status"] == "QUEUED"
                         and not job["graph_run_id"] and not job["manifest"]["simulation"]),
                        key=lambda job: (-job["priority"], job["created"]))
        if not queued:
            output({"idle": True})
            return
        # Read-only hardware fit precedes selecting the stage-specific private venv.
        with connection(settings) as (facts, _):
            selected = None
            for job in queued:
                receipt = factory.store.server_receipt(job["id"])
                # Only unbound jobs or jobs frozen to this exact destination; others never block.
                if receipt is not None and not receipt.matches_destination(job["manifest"]["sha256"],
                                                                           transport, facts):
                    continue
                config = AIConfig.model_validate(job["manifest"]["config"])
                try:
                    facts.validate_config(config)
                except ValueError:
                    continue
                if config.resource_profile.gpu_count <= factory.policy.max_gpus:
                    selected = job
                    break
            if selected is None:
                output({"waiting": True, "reason": "no queued job fits this server destination, "
                                                   "observed GPU selection and budget"})
                return
        receipt = factory.store.server_receipt(selected['id'])
        stage = selected['manifest']['config']['training_stage']
        with executor_for(settings, factory.root, training_stage=stage, server_receipt=receipt) as executor:
            if receipt is None:
                receipt = ServerApprovalReceipt.create(selected['manifest']['sha256'], executor.server_profile,
                                                       executor.server_facts)
                factory.store.bind_server_receipt(selected['id'], receipt)
            result = factory.work(executor=executor, gpu=executor.server_facts.exclusivity_key,
                                  approved_transfer=True, select_mode=True, target_job_id=selected['id'])
        output(result or {"idle": True})
        if not drain or result is None or result["status"] == "QUEUED":
            return


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
def reconcile(ctx: typer.Context, job_id: str,
              pod_id: Annotated[str | None, typer.Argument()] = None,
              host: Annotated[str | None, typer.Argument()] = None,
              key_file: Annotated[Path | None, typer.Argument()] = None, port: int = 22,
              server_profile: Path | None = None):
    server = server_connection(ctx, server_profile)
    if server is not None:
        from .desktop_runtime import executor_for
        receipt = ctx.obj.store.server_receipt(job_id)
        if receipt is None:
            raise typer.BadParameter("job has no original SSH server destination receipt")
        # Recovery binds the original server, transport and attempt record, not current inventory.
        with executor_for(server, ctx.obj.root, preflight=False, server_receipt=receipt,
                          recovery=True) as executor:
            output(ctx.obj.reconcile(job_id, executor))
        return
    if not pod_id or not host or not key_file:
        raise typer.BadParameter("Pod ID, host and key file required for RunPod reconciliation")
    controller = RunPodController(os.environ["RUNPOD_API_KEY"])
    pod = controller.status(pod_id)
    if pod.get("desiredStatus") != "RUNNING" or pod.get("publicIp") != host or int(pod.get("portMappings", {}).get("22", 0)) != port:
        raise typer.BadParameter("running Pod and matching direct SSH endpoint required for result retrieval")
    executor = SSHExecutor(host, port, "root", key_file, pod=pod)
    output(ctx.obj.reconcile(job_id, executor))


@app.command("release-lock")
def release_lock(ctx: typer.Context, job_id: str, attempt_id: str, confirm_execution_stopped: bool = False):
    if not confirm_execution_stopped:
        raise typer.BadParameter("verify remote process is stopped, then supply --confirm-execution-stopped")
    if ctx.obj.store.get(job_id)["status"] not in {"FAILED", "CANCELED", "COMPLETED"}:
        raise typer.BadParameter("cannot release an active job's lock")
    lease = ctx.obj.store.current_lease(job_id)
    if lease.attempt_id != attempt_id:
        raise typer.BadParameter("attempt ID is stale; confirm termination of the current attempt")
    ctx.obj.store.release_gpu(lease, execution_stopped=True)
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


# -- Task 9.6: project and graph commands (Task 9 plan §4 "9.6"; D9, D10, D14) ----------------------------------
#
# Each command runs one desktop workflow action through ``desktop_workflows.dispatch_action``: the same action and the
# same strict request model as the Windows app, printed as its ``WorkflowResult`` (exit 0 for ``ok``, 1 otherwise).
# ``graph-work`` runs the Task 9.4 controller (``controller_entry.serve``) in the foreground. Credentials come only
# from files that ``--executor-profiles`` references or from the existing ``.env``/environment, never from an option
# value, and never reach argv, output, files or the ledger.

PROFILES_MAX_BYTES = REQUEST_MAX_BYTES = 1024 * 1024
CREDENTIAL_FILE_MAX_BYTES = 64 * 1024
EXIT_INVALID_START = 2   # = controller_entry.EXIT_INVALID_START: refused before the controller starts
EXIT_INTERRUPTED = 130   # a second Ctrl+C: the conventional SIGINT exit status
ONE_CONTROLLER = "a graph controller already runs in this process"
PROFILES_REQUIRE_APPROVAL = "--approve-execution-and-transfer is required to use executor profiles for real jobs"
PROFILES_REFUSED = "the executor profiles file was refused; see issues"
DRAINING = ("Stop requested: no new step starts; steps in flight finish, then the controller releases its owner "
            "lease. Press Ctrl+C again to exit at once: the owner lease then stays held until its 60 s TTL passes "
            "(a restart is refused with exit 3 until then), and a step that was in flight is reconciled by the next "
            "controller or may need release-lock --confirm-execution-stopped.")
PROFILES_FILE_INVALID = "the executor profiles file must be a readable JSON object of at most 1 MiB"
REQUEST_FILE_INVALID = "the plan request file must be a readable JSON object of at most 1 MiB"
CREDENTIAL_FILE_UNREADABLE = "the credential file of this executor profile cannot be read"
CREDENTIAL_KEY_MISSING = "RunPod key missing from referenced credential file"   # connections.load_connection's text
CREDENTIAL_FILE_RUNPOD_ONLY = "only RunPod profiles take a credential file; SSH profiles use their key file"
CREDENTIALS_IN_PROFILES = "; reference a credential file instead"
PROJECT_FROM_ARGUMENT = "the project comes from the PROJECT_ID argument"
REAL_FROM_FLAG = "plans are dry runs unless --real is given"
START_INVALID = "the controller start settings are invalid (state directory or tick interval)"
TRACKING_INVALID = "the MLflow settings of this environment are invalid"
STOPPED = "the controller drained and released its owner lease"
OWNER_LOST = "the controller lost its owner lease (after a sleep or a takeover); start it again"
CONTROLLER_FAILED = "the controller stopped with an error; see state/controller/current.json"
# One controller per process, whatever its state directory: MLflow auth is process-wide (Task 9.4 note 1, D7).
_CONTROLLER = threading.Lock()


class CliRefused(Exception):
    """An input file refused before any action, printed as an ``invalid`` result (exit 1). Its issues never carry an
    input value."""

    def __init__(self, *issues):
        super().__init__("; ".join(item.message for item in issues))
        self.issues = issues


def _issue(code: str, message: str, path: str = ""):
    from .desktop_workflows import issue
    return issue(code, message, path)


def _messages(exc: ValidationError) -> str:
    """Pydantic's messages only: they never echo an input value."""
    return "; ".join(dict.fromkeys(error["msg"].removeprefix("Value error, ") for error in exc.errors()))


def _native_profile(profile_id: str, fields, saved: bool = False):
    """``NativeProfile`` of public fields (the per-provider allowlist). Credential names, and pasted private keys in
    key-file fields, are refused first, by name, so the message says where credentials belong."""
    from .desktop_workflows import NativeProfile, _refuse_credentials
    try:
        _refuse_credentials(dict(fields))
    except ValueError as exc:
        raise ValueError(str(exc) + CREDENTIALS_IN_PROFILES) from None
    try:
        return NativeProfile.model_validate({"profile_id": profile_id, "fields": dict(fields),
                                             "RUNPOD_API_KEY_saved": saved})
    except ValidationError as exc:
        raise ValueError(_messages(exc)) from None


class CliProfile(StrictModel):
    """One executor profile of an ``--executor-profiles`` file: the desktop's public profile fields
    (``NativeProfile``'s allowlist) and, for a RunPod profile, an optional reference to a local env file holding
    ``RUNPOD_API_KEY``, relative to the profiles file. Never a credential value. Without a credential file a RunPod
    profile uses ``RUNPOD_API_KEY`` from the existing ``.env``/environment (D10)."""
    profile_id: Annotated[LocalId, Field(max_length=64)]
    fields: dict[str, str]
    credential_file: Annotated[str, Field(min_length=1, max_length=1024)] | None = None

    @model_validator(mode="after")
    def native_fields(self):
        _native_profile(self.profile_id, self.fields)
        if self.credential_file is not None and self.fields.get("provider") != "existing_runpod":
            raise ValueError(CREDENTIAL_FILE_RUNPOD_ONLY)
        return self


class CliProfiles(StrictModel):
    """An ``--executor-profiles`` file: ``{"schema_version": 1, "profiles": [...]}``, at most 32 profiles whose IDs
    differ under ``casefold().rstrip(".")`` (as the desktop's profile store)."""
    schema_version: Literal[1] = 1
    profiles: tuple[CliProfile, ...] = Field(default=(), max_length=32)

    @model_validator(mode="after")
    def unique_profiles(self):
        if len({item.profile_id.casefold().rstrip(".") for item in self.profiles}) != len(self.profiles):
            raise ValueError("duplicate executor profile ID")
        return self


def _unique_keys(pairs):
    data = {}
    for key, value in pairs:
        if key in data:
            raise ValueError("duplicate JSON key")
        data[key] = value
    return data


def _json_object(path: Path, limit: int, code: str, message: str) -> dict:
    """A JSON object of at most ``limit`` bytes (UTF-8, optional BOM, no duplicate key); else one fixed issue."""
    try:
        with open(path, "rb") as stream:
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise ValueError(message)
        data = json.loads(raw.decode("utf-8-sig"), object_pairs_hook=_unique_keys)
    except (OSError, ValueError, RecursionError):
        raise CliRefused(_issue(code, message)) from None
    if not isinstance(data, dict):
        raise CliRefused(_issue(code, message))
    return data


def _file_issues(exc: ValidationError, data: dict, model) -> tuple:
    """Validation issues of a file the user wrote (profiles or plan request), with pointers that never echo a key
    the user chose: an unknown key (``extra_field``) points at the object holding it, and a pointer stops at the
    first segment that is neither a field name of ``model`` nor a list index (a dict key). Messages are pydantic's
    and never echo a value. ``issues_from_validation`` already points an unknown key at its object and adds
    ``UNKNOWN_KEY_HIDDEN``; this keeps the stricter truncation."""
    from .desktop_workflows import (
        CREDENTIALS_REFUSED,
        _pointer_part,
        issues_from_validation,
        model_field_names,
    )
    names = model_field_names(model)
    found = []
    for item in issues_from_validation(exc, data=data):
        parts = [part.replace("~1", "/").replace("~0", "~") for part in item.path.split("/")[1:]] if item.path else []
        kept = []
        for part in parts:
            if part not in names and not (part.isdigit() and len(part) <= 6):
                break
            kept.append(part)
        message = item.message.removeprefix("Value error, ")
        code = "credential_field" if CREDENTIALS_REFUSED in message else item.code
        found.append(item.model_copy(update={"code": code, "message": message,
                                             "path": "".join("/" + _pointer_part(part) for part in kept)}))
    return tuple(found)


def load_profiles(path: Path) -> tuple[Path, CliProfiles]:
    """``(absolute path, profiles)`` of an ``--executor-profiles`` file; refusals point into the file."""
    source = Path(os.path.abspath(path))
    data = _json_object(source, PROFILES_MAX_BYTES, "profiles_file_invalid", PROFILES_FILE_INVALID)
    try:
        return source, CliProfiles.model_validate(data)
    except ValidationError as exc:
        raise CliRefused(*_file_issues(exc, data, CliProfiles)) from None


def _profile_key(source: Path, index: int, item: CliProfile) -> str:
    """The RunPod key of one profile: ``RUNPOD_API_KEY`` from its credential file (a local file, relative to the
    profiles file), else from the existing ``.env``/environment. Read only when a command needs it; never printed."""
    from .connections import read_env_file
    from .desktop_workflows import LOCAL_FILE_ONLY, local_file
    if item.credential_file is None:
        return os.environ.get("RUNPOD_API_KEY", "")
    pointer = f"/profiles/{index}/credential_file"
    try:
        path = local_file(os.path.abspath(os.path.join(source.parent, os.path.expanduser(item.credential_file))))
    except (ValueError, RuntimeError):
        raise CliRefused(_issue("local_file_only", LOCAL_FILE_ONLY, pointer)) from None
    try:
        if path.stat().st_size > CREDENTIAL_FILE_MAX_BYTES:
            raise ValueError(CREDENTIAL_FILE_UNREADABLE)
        key = read_env_file(path).get("RUNPOD_API_KEY", "")
    except (OSError, ValueError):
        raise CliRefused(_issue("credential_file_unreadable", CREDENTIAL_FILE_UNREADABLE, pointer)) from None
    if not key:
        raise CliRefused(_issue("credential_missing", CREDENTIAL_KEY_MISSING, pointer))
    return key


def _tracking_scope(ctx: typer.Context):
    """This process's one MLflow scope (``TrackingScope.from_environment``: ``.env``, ``--connection-file`` and the
    environment), with ``--tracking-uri`` applied as the v1 commands apply it. Plans and ``graph-work`` use the same
    scope, so a real plan pins the destination its controller reports (D11)."""
    from .controller_entry import TrackingScope
    try:
        scope = TrackingScope.from_environment()
        uri = getattr(ctx.obj.tracker, "uri", None) or ""
        if uri != scope.MLFLOW_TRACKING_URI:
            scope = TrackingScope.model_validate(
                {**{name: getattr(scope, name) for name in TrackingScope.model_fields}, "MLFLOW_TRACKING_URI": uri})
    except ValidationError:
        raise typer.BadParameter(TRACKING_INVALID) from None
    return scope


def tracking_view(scope) -> dict[str, str | bool]:
    """The desktop's public settings view of a ``TrackingScope``, as main.rs sends it: public strings and ``*_saved``
    flags, never a credential value. ``public_auth_mode(view)`` is the auth mode the controller applies with the same
    scope, and planning checks it with the controller's HTTPS and OIDC rules (D11, D13)."""
    view: dict[str, str | bool] = {"MLFLOW_TRACKING_URI": scope.MLFLOW_TRACKING_URI,
                                   "FACTORY_MLFLOW_ARTIFACT_LOCATION": scope.FACTORY_MLFLOW_ARTIFACT_LOCATION,
                                   "auth_mode": scope.auth_mode}
    if scope.auth_mode == "token":
        view["MLFLOW_TRACKING_TOKEN_saved"] = bool(scope.MLFLOW_TRACKING_TOKEN.get_secret_value())
    elif scope.auth_mode in ("", "oidc"):
        view.update({"MLFLOW_OIDC_TOKEN_URL": scope.MLFLOW_OIDC_TOKEN_URL,
                     "MLFLOW_OIDC_CLIENT_ID": scope.MLFLOW_OIDC_CLIENT_ID,
                     "MLFLOW_OIDC_CLIENT_SECRET_saved": bool(scope.MLFLOW_OIDC_CLIENT_SECRET.get_secret_value())})
    return view


def cli_context(ctx: typer.Context, profiles_file: Path | None, *, scope_secret: str | None = None):
    """The ``WorkflowContext`` of one command: this state directory, the public tracking view (``tracking_view``),
    the profiles file's public fields, and only the RunPod key of ``scope_secret`` (the profile that
    ``executor-profile-inspect`` observes)."""
    from .desktop_workflows import WorkflowContext
    profiles, secrets = {}, {}
    if profiles_file is not None:
        source, loaded = load_profiles(profiles_file)
        for index, item in enumerate(loaded.profiles):
            runpod = item.fields.get("provider") == "existing_runpod"
            saved = runpod and (item.credential_file is not None or bool(os.environ.get("RUNPOD_API_KEY")))
            profiles[item.profile_id] = _native_profile(item.profile_id, item.fields, saved)
            if runpod and item.profile_id == scope_secret:
                key = _profile_key(source, index, item)
                if key:
                    secrets["RUNPOD_API_KEY"] = key
    return WorkflowContext(state_dir=ctx.obj.root, settings=MappingProxyType(tracking_view(_tracking_scope(ctx))),
                           profiles=MappingProxyType(profiles), secrets=MappingProxyType(secrets), clock=time.time)


def _encodable_json(value) -> str:
    """The v1 ``output`` text (readable UTF-8 JSON) when stdout can encode it; otherwise the same JSON with ASCII
    escapes, so output redirected in a legacy code page (cp949, cp1252) never fails on a name."""
    text = json.dumps(value, ensure_ascii=False, indent=2)
    try:
        text.encode(getattr(sys.stdout, "encoding", None) or "utf-8")
    except (UnicodeError, LookupError):
        text = json.dumps(value, ensure_ascii=True, indent=2)
    return text


def emit(result) -> None:
    """A ``WorkflowResult`` as JSON on stdout (the v1 ``output`` format, ASCII-escaped only where stdout cannot
    encode it); exit 0 for ``ok``, 1 otherwise."""
    typer.echo(_encodable_json(result.model_dump(mode="json")))
    if result.status != "ok":
        raise typer.Exit(1)


def _refused(refusal: CliRefused) -> None:
    from .desktop_workflows import invalid
    emit(invalid(*refusal.issues))


def _workflow(ctx: typer.Context, action: str, data: dict, *, profiles_file: Path | None = None,
              scope_secret: str | None = None) -> None:
    from .desktop_workflows import dispatch_action
    try:
        context = cli_context(ctx, profiles_file, scope_secret=scope_secret)
    except CliRefused as refusal:
        _refused(refusal)
        return
    try:
        result = dispatch_action(context, action, data)
    except ValueError as exc:   # an envelope refusal: a fixed text, never an input value
        raise typer.BadParameter(str(exc)) from None
    emit(result)


@app.command("project-list")
def project_list(ctx: typer.Context):
    """List the workflow projects of this state directory."""
    _workflow(ctx, "projects_list", {})


@app.command("executor-profile-inspect")
def executor_profile_inspect(ctx: typer.Context, profile_id: str,
                             executor_profiles: Annotated[Path, typer.Option()]):
    """Observe one executor profile read-only (RunPod status, or the SSH probe) and store the public facts."""
    _workflow(ctx, "executor_profile_inspect", {"profile_id": profile_id}, profiles_file=executor_profiles,
              scope_secret=profile_id)


@app.command("graph-validate")
def graph_validate(ctx: typer.Context, project_id: str, revision: str | None = None):
    """Check a project's graph offline (definitions, recipes, data, models)."""
    data = {"project_id": project_id}
    if revision is not None:
        data["revision"] = revision
    _workflow(ctx, "graph_validate", data)


def _plan_request(path: Path, project_id: str, real: bool) -> dict:
    """The ``graph_plan`` request: the file's body, the PROJECT_ID argument and ``--real``. It is validated here first,
    so a refusal points into the file without echoing an unknown key (``_file_issues``)."""
    from .workflow_planning import GraphPlanRequest
    data = _json_object(Path(os.path.abspath(path)), REQUEST_MAX_BYTES, "request_file_invalid", REQUEST_FILE_INVALID)
    found = [_issue("extra_field", PROJECT_FROM_ARGUMENT, "/project_id")] if "project_id" in data else []
    if "simulation" in data:
        found.append(_issue("extra_field", REAL_FROM_FLAG, "/simulation"))
    if found:
        raise CliRefused(*found)
    request = {**data, "project_id": project_id, "simulation": not real}
    try:
        GraphPlanRequest.model_validate(request)
    except ValidationError as exc:
        raise CliRefused(*_file_issues(exc, request, GraphPlanRequest)) from None
    return request


@app.command("graph-plan")
def graph_plan(ctx: typer.Context, project_id: str, request: Annotated[Path, typer.Option()],
               executor_profiles: Path | None = None, real: bool = False):
    """Plan a project's graph offline and store the plan. A dry run unless --real."""
    try:
        body = _plan_request(request, project_id, real)
    except CliRefused as refusal:
        _refused(refusal)
        return
    _workflow(ctx, "graph_plan", body, profiles_file=executor_profiles)


@app.command("graph-submit")
def graph_submit(ctx: typer.Context, plan_sha256: str, request_id: Annotated[str, typer.Option()],
                 approve_execution: bool = False, approve_data_transfer: bool = False,
                 executor_profiles: Path | None = None):
    """Submit a stored plan. A real plan needs --approve-execution and --approve-data-transfer."""
    _workflow(ctx, "graph_submit", {"plan_sha256": plan_sha256, "request_id": request_id,
                                    "approve_execution": approve_execution,
                                    "approve_data_transfer": approve_data_transfer},
              profiles_file=executor_profiles)


@app.command("graph-status")
def graph_status_command(ctx: typer.Context, run_id: Annotated[str | None, typer.Argument()] = None):
    """Show every graph run, or one run's nodes, wait reasons and tracking (from the ledger only)."""
    if run_id is None:
        _workflow(ctx, "graph_runs_list", {})
    else:
        _workflow(ctx, "graph_run_get", {"run_id": run_id})


@app.command("graph-pause")
def graph_pause(ctx: typer.Context, run_id: str):
    """Pause a running graph run: no new step starts."""
    _workflow(ctx, "graph_pause", {"run_id": run_id})


@app.command("graph-resume")
def graph_resume(ctx: typer.Context, run_id: str):
    """Resume a paused graph run."""
    _workflow(ctx, "graph_resume", {"run_id": run_id})


@app.command("graph-cancel")
def graph_cancel(ctx: typer.Context, run_id: str):
    """Cancel a graph run."""
    _workflow(ctx, "graph_cancel", {"run_id": run_id})


@app.command("graph-rearm-tracking")
def graph_rearm_tracking(ctx: typer.Context, event_id: str):
    """Re-arm a failed or uncertain tracking event."""
    _workflow(ctx, "tracking_rearm", {"event_id": event_id})


def _profile_starts(profiles_file: Path | None) -> tuple:
    """``ProfileStart`` of every profile in the file, each RunPod profile with its own key (``ProfileSecrets``
    only; D7)."""
    from .controller_entry import ProfileStart
    if profiles_file is None:
        return ()
    source, loaded = load_profiles(profiles_file)
    starts = []
    for index, item in enumerate(loaded.profiles):
        key = _profile_key(source, index, item) if item.fields.get("provider") == "existing_runpod" else ""
        try:
            starts.append(ProfileStart(profile_id=item.profile_id, fields=dict(item.fields),
                                       secrets={"RUNPOD_API_KEY": key} if key else {}))
        except ValidationError:
            raise CliRefused(_issue("invalid_value", "executor profile fields are not the native allowlist",
                                    f"/profiles/{index}")) from None
    return tuple(starts)


def _exit_now(code: int) -> None:
    """The second Ctrl+C: leave at once, without draining. The owner lease stays held until its TTL passes (a restart
    gets exit 3 until then), and the next controller reconciles what was in flight without relaunching it (Task 8);
    a step left uncertain may need ``release-lock --confirm-execution-stopped``."""
    for stream in (sys.stdout, sys.stderr):
        with contextlib.suppress(Exception):
            stream.flush()
    os._exit(code)


class _CtrlC:
    """The SIGINT handler of ``graph-work``, installed in the main thread for the controller's lifetime.

    A Ctrl+C never raises ``KeyboardInterrupt`` here. Raising it inside ``Thread.join`` would hit the CPython 3.12 bug
    in ``Thread._wait_for_tstate_lock``: the thread is marked stopped while it still runs (9.6 review Critical 1). The
    first press sets the stop event (``run_forever`` drains, D14); the second exits at once (``_exit_now(130)``).
    Outside the main thread nothing is installed: Python delivers SIGINT to the main thread only.

    A second press can run this handler re-entrantly, between any two bytecodes of the first (9.6 re-review N1). So
    the press number comes first, from one atomic ``next`` (a ``+= 1`` could lose a press), and only the first press
    touches the stop event: its ``Condition`` lock is not reentrant, and a nested ``set`` while the first press holds
    it would deadlock the main thread."""

    def __init__(self, stop_event: threading.Event):
        self.stop_event, self.previous, self.installed = stop_event, None, False
        self._numbers, self._latest = itertools.count(1), 0

    @property
    def presses(self) -> int:
        """How many presses arrived (read by the main loop for its DRAINING note)."""
        return self._latest

    def __call__(self, signum, frame) -> None:
        press = next(self._numbers)
        self._latest = max(self._latest, press)
        if press > 1:
            _exit_now(EXIT_INTERRUPTED)
            return   # only a stubbed exit returns
        self.stop_event.set()

    def __enter__(self):
        try:
            self.previous = signal.signal(signal.SIGINT, self)
            self.installed = True
        except ValueError:   # not the main thread
            pass
        return self

    def __exit__(self, *exc) -> None:
        if self.installed:
            signal.signal(signal.SIGINT, self.previous if self.previous is not None else signal.default_int_handler)


def _final_line(exit_code: int, code: str, message: str, controller=None, issues=None) -> None:
    """``graph-work``'s last stdout line, for every outcome except a second Ctrl+C (``os._exit(130)``) and a Click usage
    error: ``{"ok", "exit_code", "code", "message", "controller"}`` (``controller`` is this owner's ``current.json``
    or null), plus ``issues`` for a refused profiles file. ASCII JSON, like ``serve``'s ready line, so redirected
    output in any code page can print it. Then exits with ``exit_code``."""
    body = {"ok": exit_code == 0, "exit_code": exit_code, "code": code, "message": message, "controller": controller}
    if issues is not None:
        body["issues"] = [item.model_dump(mode="json") for item in issues]
    typer.echo(json.dumps(body, ensure_ascii=True))
    if exit_code:
        raise typer.Exit(exit_code)


# Paragraphs without hard line breaks: rich help keeps every newline after the first paragraph.
GRAPH_WORK_HELP = "\n\n".join((
    "Run the graph controller in the foreground until Ctrl+C or --max-ticks.",
    "Simulation runs proceed. Real runs need --executor-profiles and --approve-execution-and-transfer; without "
    "them they wait.",
    "The first Ctrl+C drains: no new step starts, steps in flight finish, then the owner lease is released. A "
    "second Ctrl+C exits at once with 130: the owner lease stays held until its 60 s TTL passes (a restart gets "
    "exit 3 until then), and a step left in flight may need release-lock --confirm-execution-stopped.",
    'Output is JSON lines: the controller\'s ready line {"ok": true, "owner_id", "pid"} or {"ok": false, "code": '
    '"owner_held" or "settings_refused"}, then a final line {"ok", "exit_code", "code", "message", "controller"}, '
    'with "issues" for a refused profiles file. A second Ctrl+C (exit 130) and a usage error (exit 2, the usage '
    'message on stderr) print no final line.',
    "Exit codes: 0 drained stop; 1 lost owner lease or controller error; 2 refused before the start; 3 another "
    "controller owns the state directory; 4 MLflow settings refused; 130 a second Ctrl+C.",
))


@app.command("graph-work", help=GRAPH_WORK_HELP)
def graph_work(ctx: typer.Context, executor_profiles: Path | None = None,
               approve_execution_and_transfer: bool = False,
               max_ticks: Annotated[int | None, typer.Option(min=1)] = None,
               tick_seconds: Annotated[float, typer.Option(min=0.05, max=60.0)] = 5.0):
    """``GRAPH_WORK_HELP``: the Task 9.4 controller in the foreground (``controller_entry.serve`` on a worker
    thread), with ``_CtrlC`` for Ctrl+C and ``_final_line`` for every outcome."""
    from .controller_entry import (
        EXIT_INTERNAL,
        EXIT_OK,
        EXIT_OWNER_HELD,
        EXIT_SETTINGS_REFUSED,
        SETTINGS_REFUSED,
        START_SCHEMA,
        ControllerStart,
        StartSettings,
        TrackingScope,
        read_current,
        serve,
    )
    from .graph_store import OWNER_HELD
    if executor_profiles is not None and not approve_execution_and_transfer:
        _final_line(EXIT_INVALID_START, "approval_required", PROFILES_REQUIRE_APPROVAL)
    try:
        profiles = _profile_starts(executor_profiles) if approve_execution_and_transfer else ()
    except CliRefused as refusal:
        _final_line(EXIT_INVALID_START, "invalid_start", PROFILES_REFUSED, issues=refusal.issues)
    try:
        tracking = _tracking_scope(ctx) if approve_execution_and_transfer else TrackingScope()
    except typer.BadParameter:
        _final_line(EXIT_INVALID_START, "invalid_start", TRACKING_INVALID)
    owner_id = f"cli.{os.getpid()}.{time.time_ns()}"
    try:
        start = ControllerStart(schema=START_SCHEMA, state_root=str(ctx.obj.root), owner_id=owner_id,
                                tracking=tracking, profiles=profiles,
                                settings=StartSettings(tick_seconds=float(tick_seconds)))
    except ValidationError:
        _final_line(EXIT_INVALID_START, "invalid_start", START_INVALID)
    stop_event, done, box = threading.Event(), threading.Event(), {}

    def run():
        try:
            box["code"] = serve(start, stop_event=stop_event, max_ticks=max_ticks)
        except BaseException:
            box["code"] = EXIT_INTERNAL
        finally:
            _CONTROLLER.release()
            done.set()   # serve has returned: its quiet stdout is restored

    # The handler is in place before the lock and the thread, so no Ctrl+C can leave either half-done.
    with _CtrlC(stop_event) as ctrl_c:
        if not _CONTROLLER.acquire(blocking=False):
            _final_line(EXIT_INVALID_START, "controller_in_process", ONE_CONTROLLER)
        worker = threading.Thread(target=run, name="cli-graph-controller", daemon=True)
        try:
            worker.start()
        except BaseException:
            _CONTROLLER.release()
            raise
        announced = False
        while True:   # an Event wait, never Thread.join or is_alive, while a Ctrl+C may arrive
            finished = done.wait(0.2)
            if ctrl_c.presses and not announced:
                announced = True
                with contextlib.suppress(OSError):   # a broken stderr pipe loses the note, never the drain (N2)
                    typer.echo(DRAINING, err=True)
            if finished:
                break
        worker.join()
        code = box.get("code", EXIT_INTERNAL)
        current = read_current(ctx.obj.root)
        mine = current if isinstance(current, dict) and current.get("owner_id") == owner_id else None
        recorded = (mine or {}).get("code")
        if code == EXIT_OWNER_HELD:
            name, message = "owner_held", OWNER_HELD
        elif code == EXIT_SETTINGS_REFUSED:
            name, message = "settings_refused", (mine or {}).get("error") or SETTINGS_REFUSED
        elif code == EXIT_OK:
            name, message = recorded or "stopped", STOPPED
        else:
            name = recorded if recorded in ("owner_lost", "controller_failed") else "controller_failed"
            message = OWNER_LOST if name == "owner_lost" else CONTROLLER_FAILED
        _final_line(code, name, message, mine)


if __name__ == "__main__":
    app()
