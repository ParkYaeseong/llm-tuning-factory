"""Graph runs on the desktop workflow channel (Task 9.3): submission, status and steering.

- ``graph_submit`` (D9) loads the stored plan by its hash and re-checks its freshness: the project revision always;
  for a real plan also every pinned profile's current native revision and the MLflow destination hash (any change
  is a 409 ``plan_stale``). A simulation plan pins its profiles as observed and never contacts them (it routes to
  ``SimulatedExecutorProvider`` and the local tracking sink), so only its project is re-checked. A real plan needs
  both ``approve_execution`` and ``approve_data_transfer``, and never runs practice or non-exportable data. Then
  ``ApprovalGrant.for_plan(plan)`` goes to ``GraphStore.submit``, which is idempotent by request ID. Once the
  approvals pass, a retry of a submitted request (same request ID, same plan) is that run, read-only, before any
  freshness check (9.3 review Minor 1). Starting the controller is not an approval, and this module never starts it.
- ``graph_runs_list`` and ``graph_run_get`` are DB-only reads of ``graph_controller.graph_status`` (no provider,
  SSH, HF or MLflow), plus the stored plan's public summary and the re-armable tracking events.
- ``graph_pause``, ``graph_resume`` and ``graph_cancel`` call ``GraphStore`` directly; its fixed refusals become
  ``conflict``/``run_state``. ``tracking_rearm`` calls ``TrackingOutbox.rearm``.
- ``executor_profile_edit_check`` (D5, 8.4 review Minor 5) is what the native profile save and delete ask first: a
  profile that a real graph job holding a lock, or still active, is pinned to may not change.
- ``graph_release_lock`` (Task 9.8; the Task 10 root decision that replaces D21's CLI-only rule, because the
  installed app ships no ``factory`` CLI) is the desktop operator release. It takes only the current attempt of a
  node that needs the operator (uncertain, no late result plausible) with ``confirm_execution_stopped: true``, then
  runs exactly the reads, checks and single store call of CLI ``release-lock --confirm-execution-stopped``. The
  controller then settles the node FAILED (CANCELED for a canceled attempt) and never retries it (D4).

Reads never create the ledger. Domain texts of Tasks 1-8 pass through unchanged (English); this module's own texts
are Korean (F25e).
"""

import json
from pathlib import Path
from typing import Annotated

from pydantic import Field

from .config import StrictModel
from .desktop_workflows import (
    Empty,
    RequestId,
    WorkflowContext,
    WorkflowIssue,
    WorkflowResult,
    _pointer_part,
    conflict,
    invalid,
    issue,
    ok,
    refusal,
    workflow_action,
)
from .graph_controller import _display, graph_status
from .graph_planner import ApprovalGrant, GraphPlan
from .graph_schema import EvaluateNode, TrainNode
from .graph_store import GraphStore, PlanSubmitted
from .jobs import ACTIVE
from .projects import LocalId, Sha256
from .registry import canonical, immutable_write
from .tracking_outbox import EVIDENCE_CHANGED, EVIDENCE_CHANGED_ACTION, TrackingOutbox
from .workflow_planning import (
    EXPORT_NOT_ALLOWED,
    PLAN_UNKNOWN,
    plan_summary,
    profile_revision,
    public_auth_mode,
    stored_plan,
    tracking_destination,
)

PLAN_STALE = "실행 계획 이후 프로젝트·실행 프로필·MLflow 설정이 바뀌었습니다. 다시 계획하세요."
APPROVAL_REQUIRED = "실제 실행에는 실행 승인과 데이터 전송 승인이 모두 필요합니다."
PROFILE_IN_USE = ("이 실행 프로필로 실행 중이거나 결과 확인이 끝나지 않은 작업이 있어 수정·삭제할 수 없습니다. "
                  "작업이 끝나거나 재조정된 뒤 다시 시도하세요.")
LEDGER = "jobs.db"
UNKNOWN_RUN = "unknown graph run"                   # GraphStore's text, mapped to run_unknown
UNKNOWN_EVENT = "unknown tracking event"            # TrackingOutbox's text, mapped to tracking_event_unknown
APPROVALS = ("approve_execution", "approve_data_transfer")
# graph_status's controller block when nothing ever owned this state directory (a read never creates jobs.db).
NO_CONTROLLER = {"owner_id": None, "state": "absent", "heartbeat_age_seconds": None}
_UNREADABLE = object()

# graph_release_lock (9.8): its fixed refusals, Korean (F25e).
RELEASE_CONFIRMATION_REQUIRED = ("원격 서버나 Pod에서 이 실행이 멈춘 것을 직접 확인했다는 표시"
                                 "(confirm_execution_stopped: true)가 있어야 잠금을 해제합니다.")
RUN_UNREADABLE = "실행 기록을 읽을 수 없어 잠금을 해제하지 않았습니다."
NODE_UNKNOWN = "이 실행에 없는 단계입니다."
JOB_MISMATCH = "이 단계의 작업이 아닙니다. 실행 상태를 새로 고친 뒤 다시 시도하세요."
ATTEMPT_STALE = "이 단계의 현재 시도가 아닙니다. 실행 상태를 새로 고친 뒤 현재 시도가 멈췄는지 다시 확인하세요."
AWAITING_RESULT_REFUSED = ("같은 실행의 결과를 아직 기다리는 중이라 잠금을 해제할 수 없습니다. "
                           "시간 제한이 지나도 결과가 없으면 다시 확인하세요.")
OPERATOR_NOT_NEEDED = "이 단계는 운영자 확인이 필요한 상태가 아니어서 잠금을 해제하지 않았습니다."
RELEASE_NOT_APPLICABLE = ("이 단계는 잠금 해제로 해결하는 상태가 아닙니다. 이미 해제했다면 컨트롤러가 다음 확인 때 이 단계를 "
                          "실패로 기록하고 다시 실행하지 않습니다. 그 밖에는 표시된 이유를 확인하세요.")
LOCK_NOT_HELD = "확인하는 사이 이 시도의 잠금이 이미 풀렸습니다. 실행 상태를 새로 고치세요."
# cli.release_lock's checks, verbatim: a final job status, then JobStore.release_gpu's refusal of an active one.
RELEASABLE_STATUSES = frozenset({"FAILED", "CANCELED", "COMPLETED"})
STILL_ACTIVE = "current attempt termination confirmation required"
ID32 = r"^[0-9a-f]{32}$"     # graph run, job and attempt IDs (uuid4 hex)


class GraphSubmit(StrictModel):
    plan_sha256: Sha256
    request_id: RequestId
    approve_execution: bool = Field(default=False, strict=True)
    approve_data_transfer: bool = Field(default=False, strict=True)


class RunRef(StrictModel):
    run_id: str = Field(pattern=r"^[0-9a-f]{32}$")


class EventRef(StrictModel):
    event_id: Sha256


class ProfileEditCheck(StrictModel):
    profile_id: Annotated[LocalId, Field(max_length=64)]      # NativeProfile's limit


class ReleaseLock(StrictModel):
    """The node, its job and the attempt the operator confirmed stopped on the server or Pod."""
    run_id: str = Field(pattern=ID32)
    node_id: Annotated[LocalId, Field(max_length=128)]
    job_id: str = Field(pattern=ID32)
    attempt_id: str = Field(pattern=ID32)
    confirm_execution_stopped: bool = Field(strict=True)


# -- helpers -----------------------------------------------------------------------------------------

def submissions_dir(state_dir: Path) -> Path:
    return Path(state_dir) / "workflows" / "submissions"


def _ledger(context: WorkflowContext, *, create: bool = False) -> GraphStore | None:
    """The graph ledger of this state directory; None for a read before anything was ever submitted."""
    path = Path(context.state_dir) / LEDGER
    if not create and not path.is_file():
        return None
    return GraphStore(path, clock=context.clock)


def _profile_key(profile_id: str) -> str:
    """How profile IDs compare (9.1 M9): casefold plus ``rstrip(".")``, as Windows file names and vault entries."""
    return profile_id.casefold().rstrip(".")


def _pinned_profiles(plan: GraphPlan) -> dict:
    """The executor profiles the plan's approval pins: every node's allowed destinations."""
    return {resource.executor.profile_id: resource.executor for entry in plan.nodes
            for resource in entry.allowed_resources}


def staleness(context: WorkflowContext, plan: GraphPlan) -> tuple[WorkflowIssue, ...]:
    """``plan_stale`` issues for what changed since the plan: the project revision (``/project``); for a real plan
    also each pinned profile whose current native revision differs or that no longer exists
    (``/profiles/<id>``), and the MLflow destination hash (``/tracking``). Simulation plans skip the profile and
    tracking checks: they never contact a provider or MLflow (9.2 review Minor 4)."""
    found = []
    current = None
    if context.definitions_exist:   # a read never creates the definitions database
        try:
            current = context.projects().get(plan.project_revision.spec.project_id).revision_sha256
        except ValueError:
            current = None
    if current != plan.project_revision.revision_sha256:
        found.append(issue("plan_stale", PLAN_STALE, "/project"))
    if plan.simulation:
        return tuple(found)
    for profile_id, pinned in sorted(_pinned_profiles(plan).items()):
        native = context.profiles.get(profile_id)
        try:
            revision = None if native is None else profile_revision(profile_id, native.fields)
        except ValueError:
            revision = None
        if revision is None or revision != pinned.revision:
            found.append(issue("plan_stale", PLAN_STALE, "/profiles/" + _pointer_part(profile_id)))
    pinned = plan.execution_choices.tracking_destination
    try:
        destination = tracking_destination(context.settings, auth_mode=public_auth_mode(context.settings))
    except ValueError:
        destination = None
    if pinned is None or destination != pinned:
        found.append(issue("plan_stale", PLAN_STALE, "/tracking"))
    return tuple(found)


def _data_issues(plan: GraphPlan) -> list[WorkflowIssue]:
    """Real plans, defence in depth for plans stored by older code: data used by any node must be neither the
    practice dataset nor non-exportable (by the plan's pinned snapshot; a missing snapshot is not exportable)."""
    from .desktop import PRACTICE_SUBMIT_REFUSAL, reserved_tutorial_id
    choices = plan.execution_choices
    bindings = {item.binding_id: item.dataset for item in plan.project_revision.spec.datasets}
    benchmarks = {item.revision_sha256: item.spec.dataset for item in choices.benchmarks}
    snapshots = {(item.dataset.dataset_id, item.dataset.version, item.dataset.sha256): item
                 for item in choices.datasets}
    found = []
    for entry in plan.nodes:
        node = entry.node
        if isinstance(node, TrainNode):
            used = [bindings.get(node.dataset_binding)]
        elif isinstance(node, EvaluateNode):
            used = [benchmarks.get(node.benchmark_revision)]
        else:
            continue
        for ref in used:
            if ref is not None and reserved_tutorial_id({"dataset_id": ref.dataset_id}):
                found.append(issue("practice_data", PRACTICE_SUBMIT_REFUSAL, "/plan_sha256", node_id=node.id))
                continue
            snapshot = None if ref is None else snapshots.get((ref.dataset_id, ref.version, ref.sha256))
            if snapshot is None or not snapshot.export_allowed:
                found.append(issue("export_not_allowed", EXPORT_NOT_ALLOWED, "/plan_sha256", node_id=node.id))
    return found


def _write_receipt(context: WorkflowContext, run: dict, plan: GraphPlan, request: GraphSubmit) -> None:
    """``state/workflows/submissions/<run_id>.json``: who approved what, immutable. Public values only."""
    body = {"run_id": run["id"], "plan_sha256": plan.sha256, "request_id": request.request_id,
            "simulation": plan.simulation, "approve_execution": request.approve_execution,
            "approve_data_transfer": request.approve_data_transfer, "created": run["created"]}
    try:
        immutable_write(submissions_dir(context.state_dir) / f"{run['id']}.json", canonical(body))
    except ValueError:
        pass   # a retry of the same request (other simulation flags): the first receipt stands


def _tracking_failures(store: GraphStore, run_id: str) -> list[dict]:
    """The run's FAILED or UNCERTAIN tracking events, which ``tracking_rearm`` takes by ``event_id``. The error is
    shown as ``graph_status`` shows wait reasons (credentials redacted, remote paths removed). Changed evidence gets
    its distinct action: a re-arm re-reads the same bytes (8.3 review Minor 2)."""
    with store.jobs.connection() as db:
        rows = db.execute("SELECT event_id,kind,node_id,attempt_id,status,tries,last_error FROM tracking_events "
                          "WHERE run_id=? AND status IN ('FAILED','UNCERTAIN') ORDER BY rowid", (run_id,)).fetchall()
    return [{"event_id": row["event_id"], "kind": row["kind"], "node_id": row["node_id"],
             "attempt_id": row["attempt_id"], "status": row["status"],
             "tries": row["tries"], "last_error": _display(row["last_error"]) if row["last_error"] else None,
             "action": EVIDENCE_CHANGED_ACTION if row["last_error"] and EVIDENCE_CHANGED in row["last_error"] else None}
            for row in rows]


def _run_summary(entry: dict) -> dict:
    if "error" in entry:   # graph_status keeps one unreadable run from hiding the others
        return {"run_id": entry["run_id"], "error": entry["error"]}
    counts = {}
    for node in entry["nodes"]:
        counts[node["status"]] = counts.get(node["status"], 0) + 1
    return {"run_id": entry["run_id"], "status": entry["status"], "simulation": entry["simulation"],
            "plan_sha256": entry["plan_sha256"], "created": entry["created"],
            "nodes_by_status": dict(sorted(counts.items()))}


def _job_profile(manifest: str):
    """The executor profile a graph job is pinned to; None for a local selection or a simulated operation, which
    never uses the native profile (9.3 review Minor 3, as ``_pins``); ``_UNREADABLE`` otherwise, a manifest without
    a readable lineage included."""
    try:
        operation = json.loads(manifest)["operation"]
        if operation.get("kind") == "select" or operation["lineage"]["simulation"] is True:
            return None
        profile_id = operation["resources"]["executor"]["profile_id"]
    except (TypeError, KeyError, ValueError, AttributeError):
        return _UNREADABLE
    return profile_id if isinstance(profile_id, str) else _UNREADABLE


def _pins(run, key: str) -> bool:
    """Whether a running or paused real run's approval pins the profile. A simulation never uses the native
    profile (its destinations are the plan's own observations), so it never needs a re-plan; an unreadable run
    might pin it."""
    try:
        if json.loads(run["plan"]).get("simulation") is True:
            return False
        return any(_profile_key(item["profile_id"]) == key
                   for item in json.loads(run["approval"])["destination_profile_revisions"])
    except (TypeError, KeyError, ValueError, AttributeError):
        return True


def profile_usage(store: GraphStore, profile_id: str) -> dict:
    """``{"jobs": [...], "affected_runs": [...]}`` of one profile (public; IDs and statuses only).

    ``jobs``: real graph jobs pinned to the profile that hold a lock (``gpu_locks`` or ``resource_leases``) or are
    PROVISIONING/RUNNING/EVALUATING; a job whose pinned profile cannot be read counts too (fail closed). A simulated
    job never uses the native profile, so it never counts (9.3 review Minor 3).
    ``affected_runs``: RUNNING or PAUSED real runs whose approval pins the profile; they need a re-plan after an edit.
    IDs compare by casefold plus ``rstrip(".")``.
    """
    key = _profile_key(profile_id)
    active = tuple(sorted(ACTIVE))
    with store.jobs.connection() as db:
        rows = db.execute(   # only lock-holding or active graph jobs leave the database (9.3 review, optional)
            "SELECT * FROM (SELECT j.id,j.status,j.manifest,j.graph_run_id,j.graph_node_id,j.created,"
            "(EXISTS (SELECT 1 FROM gpu_locks g WHERE g.job_id=j.id) OR "
            "EXISTS (SELECT 1 FROM resource_leases r WHERE r.job_id=j.id)) AS locked "
            "FROM jobs j WHERE j.graph_run_id IS NOT NULL) "
            f"WHERE locked OR status IN ({','.join('?' * len(active))}) ORDER BY created,id", active).fetchall()
        runs = db.execute("SELECT id,plan,approval FROM graph_runs WHERE status IN ('RUNNING','PAUSED') "
                          "ORDER BY created,id").fetchall()
    jobs = []
    for row in rows:
        locked = bool(row["locked"])
        pinned = _job_profile(row["manifest"])
        if pinned is _UNREADABLE or (pinned is not None and _profile_key(pinned) == key):
            jobs.append({"run_id": row["graph_run_id"], "node_id": row["graph_node_id"], "job_id": row["id"],
                         "status": row["status"], "locked": locked})
    return {"jobs": jobs, "affected_runs": [run["id"] for run in runs if _pins(run, key)]}


# -- graph_submit ------------------------------------------------------------------------------------

def _submitted(context: WorkflowContext, request_id: str, plan: GraphPlan) -> tuple[dict, dict] | None:
    """``(graph_submit's answer, {id, created})`` for a run this request ID already made from this plan, read from
    the ledger; else None.

    9.3 review Minor 1: such a run was fresh and approved when it was made, so a retry (a lost response, a double
    click) must find it, not ``plan_stale``, after the project, a pinned profile or the tracking settings changed."""
    store = _ledger(context)
    if store is None:
        return None
    with store.jobs.connection() as db:
        row = db.execute("SELECT id,status,plan,created FROM graph_runs WHERE request_id=?",
                         (request_id,)).fetchone()
    try:
        same = row is not None and json.loads(row["plan"])["sha256"] == plan.sha256
    except (TypeError, KeyError, ValueError):
        same = False   # unreadable: the normal path decides (GraphStore.submit refuses a different plan)
    if not same:
        return None
    answer = {"run_id": row["id"], "status": row["status"], "simulation": plan.simulation,
              "plan_sha256": plan.sha256, "controller": graph_status(store, row["id"], now=context.clock())["controller"]}
    return answer, {"id": row["id"], "created": row["created"]}


@workflow_action("graph_submit", GraphSubmit)
def graph_submit(context: WorkflowContext, request: GraphSubmit) -> WorkflowResult:
    """The stored plan, fresh, with both approvals for a real plan, as an idempotent graph run (D9)."""
    try:
        plan = stored_plan(context.state_dir, request.plan_sha256)
    except ValueError as exc:
        return invalid(issue("plan_unknown" if str(exc) == PLAN_UNKNOWN else "plan_invalid", str(exc),
                             "/plan_sha256"))
    missing = [] if plan.simulation else [issue("approval_required", APPROVAL_REQUIRED, "/" + name)
                                          for name in APPROVALS if getattr(request, name) is not True]
    if not missing:
        existing = _submitted(context, request.request_id, plan)
        if existing is not None:
            answer, run = existing
            # A crash after GraphStore.submit left no receipt: the retry writes it (identical bytes: a no-op).
            _write_receipt(context, run, plan, request)
            return ok(answer)
    stale = staleness(context, plan)
    if stale:
        return conflict(*stale)
    if not plan.simulation:
        refused = missing + _data_issues(plan)
        if refused:
            return invalid(*refused)
    approval = ApprovalGrant.for_plan(plan)
    store = _ledger(context, create=True)
    try:
        run = store.submit(plan, request.request_id, approval, unique_plan=True)
    except PlanSubmitted as exc:   # one plan is one run, whatever the request ID (Task 11.5, R2 I1)
        return conflict(issue("plan_submitted", str(exc), "/plan_sha256"),
                        data={"run_id": exc.run_id, "status": exc.status})
    except ValueError as exc:
        return refusal(exc, path="/request_id")
    _write_receipt(context, run, plan, request)
    return ok({"run_id": run["id"], "status": run["status"], "simulation": plan.simulation,
               "plan_sha256": plan.sha256, "controller": graph_status(store, run["id"], now=context.clock())["controller"]})


# -- status --------------------------------------------------------------------------------------------

@workflow_action("graph_runs_list", Empty)
def graph_runs_list(context: WorkflowContext, request: Empty) -> WorkflowResult:
    store = _ledger(context)
    if store is None:
        return ok({"runs": [], "controller": dict(NO_CONTROLLER)})
    status = graph_status(store, now=context.clock())
    return ok({"runs": [_run_summary(entry) for entry in status["runs"]], "controller": status["controller"]})


@workflow_action("graph_run_get", RunRef)
def graph_run_get(context: WorkflowContext, request: RunRef) -> WorkflowResult:
    """``graph_status`` of one run (per node status, wait reason, ``needs_operator``, tracking), the controller, the
    plan's public summary (null for an unreadable run) and the re-armable tracking events. DB-only."""
    store = _ledger(context)
    if store is None:
        return invalid(issue("run_unknown", UNKNOWN_RUN, "/run_id"))
    try:
        status = graph_status(store, request.run_id, now=context.clock())
    except ValueError as exc:
        return refusal(exc, path="/run_id")
    try:
        summary = plan_summary(store.get_run(request.run_id)["plan"], context.state_dir)
    except ValueError:
        summary = None
    return ok({"run": status["runs"][0], "controller": status["controller"], "plan": summary,
               "tracking_failures": _tracking_failures(store, request.run_id)})


# -- steering ------------------------------------------------------------------------------------------

def _steer(context: WorkflowContext, run_id: str, change) -> WorkflowResult:
    store = _ledger(context)
    if store is None:
        return invalid(issue("run_unknown", UNKNOWN_RUN, "/run_id"))
    try:
        change(store, run_id)
    except ValueError as exc:   # the store's fixed refusals: run_state conflicts, run_unknown
        return refusal(exc, path="/run_id")
    status = graph_status(store, run_id, now=context.clock())
    return ok({"run": status["runs"][0], "controller": status["controller"]})


@workflow_action("graph_pause", RunRef)
def graph_pause(context: WorkflowContext, request: RunRef) -> WorkflowResult:
    return _steer(context, request.run_id, GraphStore.pause)


@workflow_action("graph_resume", RunRef)
def graph_resume(context: WorkflowContext, request: RunRef) -> WorkflowResult:
    return _steer(context, request.run_id, GraphStore.resume)


@workflow_action("graph_cancel", RunRef)
def graph_cancel(context: WorkflowContext, request: RunRef) -> WorkflowResult:
    return _steer(context, request.run_id, GraphStore.cancel)


@workflow_action("tracking_rearm", EventRef)
def tracking_rearm(context: WorkflowContext, request: EventRef) -> WorkflowResult:
    """FAILED or UNCERTAIN -> PENDING. Changed evidence may be re-armed (after its files were restored), with the
    action text that says a re-arm alone cannot repair it."""
    store = _ledger(context)
    if store is None:
        return invalid(issue("tracking_event_unknown", UNKNOWN_EVENT, "/event_id"))
    try:
        TrackingOutbox(store, clock=context.clock).rearm(request.event_id)
    except ValueError as exc:
        return refusal(exc, path="/event_id")
    with store.jobs.connection() as db:
        row = db.execute("SELECT event_id,run_id,node_id,kind,status,last_error FROM tracking_events WHERE event_id=?",
                         (request.event_id,)).fetchone()
    warnings = ([issue("tracking_evidence_changed", EVIDENCE_CHANGED_ACTION, "/event_id")]
                if row["last_error"] and EVIDENCE_CHANGED in row["last_error"] else [])
    return ok({"event_id": row["event_id"], "run_id": row["run_id"], "node_id": row["node_id"], "kind": row["kind"],
               "status": row["status"]}, warnings=warnings)


# -- graph_release_lock (9.8) ----------------------------------------------------------------------

def _release_refusal(node: dict, request: ReleaseLock) -> WorkflowResult | None:
    """Why ``graph_status`` says this node's attempt may not be released, or None. Identity first (the node's job,
    then its current attempt), then state: a late result still plausible, no operator needed, nothing held."""
    if node["job_id"] != request.job_id:
        return invalid(issue("job_mismatch", JOB_MISMATCH, "/job_id", node_id=request.node_id))
    if node["attempt_id"] != request.attempt_id:
        return conflict(issue("attempt_stale", ATTEMPT_STALE, "/attempt_id", node_id=request.node_id))
    if node["awaiting_result"]:
        return conflict(issue("awaiting_result", AWAITING_RESULT_REFUSED, "/node_id", node_id=request.node_id))
    if not node["needs_operator"]:
        return conflict(issue("operator_not_needed", OPERATOR_NOT_NEEDED, "/node_id", node_id=request.node_id))
    if not node["uncertain"]:   # outputs refused, a ledger check, or released already: no lock release settles it
        return conflict(issue("release_not_applicable", RELEASE_NOT_APPLICABLE, "/node_id", node_id=request.node_id))
    return None


@workflow_action("graph_release_lock", ReleaseLock)
def graph_release_lock(context: WorkflowContext, request: ReleaseLock) -> WorkflowResult:
    """The desktop operator release of one uncertain attempt whose remote execution the operator confirmed stopped.

    Allowed only while ``graph_status`` shows the node's current attempt uncertain (FAILED or CANCELED, launched,
    holding its lock) with ``needs_operator`` and no plausible late result. Then exactly CLI ``release-lock
    --confirm-execution-stopped``: a final job status, the current lease, its attempt ID, one
    ``JobStore.release_gpu(lease, execution_stopped=True)``. The only extra step is a read: the lease must still
    hold its lock, so nothing is ever "released" twice. A takeover makes the release fail as stale only when it
    rotates the attempt token between ``current_lease`` and ``release_gpu``; one before ``current_lease`` keeps the
    attempt ID, so the release goes ahead on the new token, as the CLI's would (9.8 review). The controller settles
    the node at its next tick exactly as after the CLI release: without a result FAILED (``failed_before_submit``,
    counted) and never retried (D4); a canceled attempt CANCELED."""
    if request.confirm_execution_stopped is not True:
        return invalid(issue("confirmation_required", RELEASE_CONFIRMATION_REQUIRED, "/confirm_execution_stopped"))
    store = _ledger(context)
    if store is None:
        return invalid(issue("run_unknown", UNKNOWN_RUN, "/run_id"))
    try:
        run = graph_status(store, request.run_id, now=context.clock())["runs"][0]
    except ValueError as exc:
        return refusal(exc, path="/run_id")
    if "error" in run:
        return invalid(issue("run_unreadable", RUN_UNREADABLE, "/run_id"))
    node = next((item for item in run["nodes"] if item["node_id"] == request.node_id), None)
    if node is None:
        return invalid(issue("node_unknown", NODE_UNKNOWN, "/node_id"))
    refused = _release_refusal(node, request)
    if refused is not None:
        return refused
    jobs = store.jobs
    try:   # cli.release_lock from here, with the same store calls in the same order
        if jobs.get(request.job_id)["status"] not in RELEASABLE_STATUSES:
            return conflict(issue("operator_not_needed", OPERATOR_NOT_NEEDED, "/node_id", node_id=request.node_id))
        lease = jobs.current_lease(request.job_id)
        if lease.attempt_id != request.attempt_id:
            return conflict(issue("attempt_stale", ATTEMPT_STALE, "/attempt_id", node_id=request.node_id))
        if not jobs.holds_lock(lease):
            return conflict(issue("lock_not_held", LOCK_NOT_HELD, "/node_id", node_id=request.node_id))
        jobs.release_gpu(lease, execution_stopped=True)
    except ValueError as exc:   # a token rotated by a takeover meanwhile, or the job became active again
        code, message, path = (("operator_not_needed", OPERATOR_NOT_NEEDED, "/node_id") if str(exc) == STILL_ACTIVE
                               else ("attempt_stale", ATTEMPT_STALE, "/attempt_id"))
        return conflict(issue(code, message, path, node_id=request.node_id))
    status = graph_status(store, request.run_id, now=context.clock())
    return ok({"released": {"run_id": request.run_id, "node_id": request.node_id, "job_id": request.job_id,
                            "attempt_id": request.attempt_id},
               "run": status["runs"][0], "controller": status["controller"]})


# -- executor_profile_edit_check -------------------------------------------------------------------

@workflow_action("executor_profile_edit_check", ProfileEditCheck)
def executor_profile_edit_check(context: WorkflowContext, request: ProfileEditCheck) -> WorkflowResult:
    """The native save and delete ask first (D5): ``conflict``/``profile_in_use`` with the pinned jobs, else ``ok``
    with ``affected_runs``. A running controller keeps its old in-memory profile and the provider refuses a changed
    revision (``PROFILE_CHANGED``), so the check-then-save race is harmless."""
    store = _ledger(context)
    if store is None:
        return ok({"affected_runs": []})
    usage = profile_usage(store, request.profile_id)
    if usage["jobs"]:
        return WorkflowResult(status="conflict", issues=(issue("profile_in_use", PROFILE_IN_USE, "/profile_id"),),
                              data={"jobs": usage["jobs"]})
    return ok({"affected_runs": usage["affected_runs"]})
