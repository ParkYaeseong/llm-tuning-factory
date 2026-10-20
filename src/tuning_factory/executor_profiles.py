"""Public pinned destinations, read-only observations and the graph controller's executor providers.

Profiles, facts and approvals are public: no credential lookup happens here. Task 8.4 adds the providers that turn an
approved, pinned destination into a live observation and an executor:

- ``ProfileSecrets`` holds one profile's local settings in memory only; its repr redacts them and nothing here ever
  writes, logs or raises them.
- ``SettingsExecutorProvider`` refuses a changed profile or transport before any connection, then observes the
  canonical Pod (or SSH facts) and requires it to be the approved one. ``desktop_runtime`` is imported only inside its
  methods, so importing this module never imports paramiko.
- ``SimulatedExecutorProvider`` serves simulation plans from their own pinned observations and contacts nothing.
"""
import math
import os
import re
import time
from collections.abc import Callable, Iterable, Mapping
from contextlib import AbstractContextManager, contextmanager
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from types import MappingProxyType
from typing import Protocol

from pydantic import Field, TypeAdapter, field_validator

from .config import ResourceProfile, StrictModel
from .gpu_servers import (
    ServerApprovalReceipt,
    SSHServerFacts,
    SSHServerProfile,
    server_settings,
    validate_server_binding,
)
from .projects import LocalId, Sha256
from .registry import canonical, digest


class ExecutorProfile(StrictModel):
    profile_id: LocalId
    revision: LocalId
    name: str = Field(min_length=1, max_length=200)
    provider: str = Field(default="existing_runpod", pattern="^existing_runpod$")
    pod_id: LocalId
    credential_scope: LocalId
    transport_settings_sha256: Sha256 | None = None

    @property
    def exclusivity_key(self):
        return f"{self.provider}:{self.pod_id}"

    @property
    def approval_identity(self):
        return {"pod_id": self.pod_id}


class ResourceFacts(StrictModel):
    pod_id: LocalId
    gpu_type: str = Field(min_length=1)
    gpu_count: int = Field(strict=True, ge=1, le=8)
    total_hourly_cost_usd: float = Field(ge=0)
    observed_at: datetime

    @property
    def exclusivity_key(self):
        return "existing_runpod:" + self.pod_id

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.tzinfo is None or value.utcoffset() is None:
            raise ValueError("resource observations require an explicit timezone")
        return value

    def cost(self, seconds: int):
        if type(seconds) is not int or seconds < 1:
            raise ValueError("positive integer duration required")
        return self.total_hourly_cost_usd * seconds / 3600

    def legacy_resources(self, seconds: int):
        return ResourceProfile(gpu_count=self.gpu_count, gpu_type=self.gpu_type,
                               timeout_seconds=seconds,
                               hourly_cost_usd=self.total_hourly_cost_usd / self.gpu_count,
                               max_cost_usd=max(self.cost(seconds), 1e-12))


PublicExecutorProfile = ExecutorProfile | SSHServerProfile
ResourceObservation = ResourceFacts | SSHServerFacts


def approval_destination(profile):
    return {"profile_id": profile.profile_id, "revision": profile.revision,
            "profile_sha256": digest(canonical(profile.model_dump(mode="json"))), **profile.approval_identity}


def observation_key(value):
    """Match a pinned public route to its observation, without guessing providers."""
    return value.exclusivity_key, (value.transport_settings_sha256 if isinstance(
        value, (SSHServerProfile, SSHServerFacts)) else None)


def validate_resource_binding(profile, facts):
    if isinstance(profile, SSHServerProfile) and isinstance(facts, SSHServerFacts):
        validate_server_binding(profile, facts)
    elif not (isinstance(profile, ExecutorProfile) and isinstance(facts, ResourceFacts)
              and profile.exclusivity_key == facts.exclusivity_key):
        raise ValueError("executor/resource destination mismatch")


def observation_fits(actual, pinned):
    if type(actual) is not type(pinned) or observation_key(actual) != observation_key(pinned):
        return False
    if (actual.gpu_type != pinned.gpu_type or actual.gpu_count != pinned.gpu_count
            or actual.total_hourly_cost_usd > pinned.total_hourly_cost_usd):
        return False
    return not isinstance(actual, SSHServerFacts) or (actual.selected_gpu_uuids == pinned.selected_gpu_uuids
                                                     and actual.inventory == pinned.inventory)


# -- Task 8.4: live observations, destination hashes and executor providers ----------------------

PROFILE_CHANGED = "executor profile changed after approval; re-plan before running"
PROFILE_MISSING = "executor profile is not configured in this controller"
TRANSPORT_CHANGED = "executor transport settings changed after approval; re-plan before running"
POD_DIFFERS = "observed Pod differs from the approved Pod"
SERVER_DIFFERS = "observed SSH server differs from the approved server"
RUNPOD_PRICE_REQUIRED = "positive finite live RunPod API price required"   # SSHExecutor.validate_config's text
_POD_ID = re.compile(r"[A-Za-z0-9_-][A-Za-z0-9_.-]*")   # projects.LocalId
# Setting names whose values are credentials (8.5 review Minor 7): the controller redacts their values from errors.
_SECRET_SETTING = re.compile(r"(?i)(api_?key|token|secret|password|passphrase|credential)")
_FINGERPRINT = re.compile(r"SHA256:[A-Za-z0-9+/]{43}")
_OBSERVATION = TypeAdapter(ResourceObservation)


def runpod_facts(pod: Mapping, *, observed_at: datetime) -> ResourceFacts:
    """The canonical observation of a live, already running Pod (``RunPodController.status`` shape; D13).

    ``gpu_type`` is RunPod's GPU type ID (``gpu.id``); planning-time facts must come from this same function.
    ``costPerHr`` is the Pod's total price and must be positive and finite. Every refusal is a fixed text.
    """
    if not isinstance(pod, Mapping):
        raise ValueError("live RunPod Pod metadata required")
    pod_id = pod.get("id")
    if not isinstance(pod_id, str) or not _POD_ID.fullmatch(pod_id):
        raise ValueError("live RunPod Pod ID required")
    gpu = pod.get("gpu") if isinstance(pod.get("gpu"), Mapping) else {}
    gpu_type, count = gpu.get("id"), gpu.get("count")
    if not isinstance(gpu_type, str) or not gpu_type.strip() or type(count) is not int or not 1 <= count <= 8:
        raise ValueError("live RunPod Pod lacks its GPU type ID and count")
    price = pod.get("costPerHr")
    try:
        if isinstance(price, bool):
            raise TypeError
        price = float(price)
    except (TypeError, ValueError):
        raise ValueError(RUNPOD_PRICE_REQUIRED) from None
    if not math.isfinite(price) or price <= 0:
        raise ValueError(RUNPOD_PRICE_REQUIRED)
    if not isinstance(observed_at, datetime) or observed_at.utcoffset() is None:
        raise ValueError("resource observations require an explicit timezone")
    return ResourceFacts(pod_id=pod_id, gpu_type=gpu_type, gpu_count=count, total_hourly_cost_usd=price,
                         observed_at=observed_at)


def runpod_transport_sha256(settings: Mapping) -> str:
    """D11: the public identity of an existing-Pod route, mirroring ``SSHTransport``.

    It covers the provider, the Pod ID, the pinned SSH host fingerprint and a hash of the resolved key-file path, never
    the key, the API key or any other setting. Task 9 must reuse it for ``ExecutorProfile.transport_settings_sha256``.
    """
    if (settings.get("provider") or "existing_runpod") != "existing_runpod":
        raise ValueError("RunPod transport settings require the existing_runpod provider")
    pod_id, fingerprint, key = settings.get("pod_id"), settings.get("ssh_fingerprint"), settings.get("ssh_key_file")
    if not isinstance(pod_id, str) or not _POD_ID.fullmatch(pod_id):
        raise ValueError("RunPod transport settings require a valid Pod ID")
    if not isinstance(fingerprint, str) or not _FINGERPRINT.fullmatch(fingerprint):
        raise ValueError("independently verified SSH SHA256 fingerprint required")
    if not isinstance(key, (str, os.PathLike)) or not str(key).strip() or any(
            ord(char) < 32 or ord(char) == 127 for char in str(key)):
        raise ValueError("nonempty local SSH key file path without control characters required")
    return digest(canonical({"provider": "existing_runpod", "pod_id": pod_id, "ssh_fingerprint": fingerprint,
                             "key_reference_sha256": digest(str(Path(key).expanduser().resolve()).encode())}))


@dataclass(frozen=True)
class ResolvedDestination:
    profile: PublicExecutorProfile        # the approved, pinned public profile
    observation: ResourceObservation      # the live (or simulated) canonical observation
    executor: object | None               # launch executor inside its connection() context; None in simulation


@dataclass(frozen=True, repr=False, eq=False)
class ProfileSecrets:
    """One profile's local settings (API key, key-file path, ...), in memory only. Never persisted, logged or shown:
    the repr names only the public profile, and the settings are a read-only copy."""
    public: PublicExecutorProfile
    settings: Mapping[str, object]

    def __post_init__(self):
        if not isinstance(self.public, (ExecutorProfile, SSHServerProfile)):
            raise TypeError("profile secrets require a public executor profile")
        if not isinstance(self.settings, Mapping) or any(not isinstance(key, str) for key in self.settings):
            raise TypeError("profile settings must be a mapping with string keys")
        object.__setattr__(self, "settings", MappingProxyType(dict(self.settings)))

    def __repr__(self):
        return f"ProfileSecrets(public={self.public.profile_id}@{self.public.revision}, settings=<redacted>)"

    __str__ = __repr__


class ExecutorProvider(Protocol):
    """How the graph controller reaches an approved destination: ``resolve`` to launch, ``recover`` to reconcile."""

    def resolve(self, profile_id: str, approved_revision: str, *, pinned: PublicExecutorProfile,
                runtime_profile: str | None = None, model_ops: bool = False
                ) -> AbstractContextManager[ResolvedDestination]: ...

    def recover(self, profile_id: str, approved_revision: str, *, pinned: PublicExecutorProfile,
                pinned_facts: ResourceObservation, manifest_sha256: str, runtime_profile: str | None = None
                ) -> AbstractContextManager[object]: ...


def _check_pinned(profile_id, approved_revision, pinned):
    if (not isinstance(pinned, (ExecutorProfile, SSHServerProfile)) or pinned.profile_id != profile_id
            or pinned.revision != approved_revision):
        raise ValueError(PROFILE_CHANGED)


def _manifest_sha256(value):
    if not isinstance(value, str) or not re.fullmatch(r"[0-9a-f]{64}", value):
        raise ValueError("recovery requires the attempt's manifest SHA256")
    return value


class SettingsExecutorProvider:
    """Real destinations from an in-memory profile map (Task 9 reads the vault and supplies it).

    ``resolve`` checks, before any connection, that the profile exists (``PROFILE_MISSING``), that it is still the
    approved revision and body (``PROFILE_CHANGED``) and that its transport still hashes to the pinned one
    (``TRANSPORT_CHANGED``). It then opens ``desktop_runtime.executor_for`` and requires the observed Pod
    (``POD_DIFFERS``, positive live price) or SSH server (``SERVER_DIFFERS``) to be the pinned one. Price and
    inventory against the pinned facts stay with ``claim_ready``'s ``observation_fits`` and the runner. Borrowed Pods and
    servers are never started, stopped or deleted.
    """

    def __init__(self, profiles: Mapping[str, ProfileSecrets], state_root: Path, *,
                 clock: Callable[[], float] = time.time):
        checked = {}
        for profile_id, secrets in dict(profiles).items():
            if not isinstance(secrets, ProfileSecrets):
                raise TypeError("the executor provider requires ProfileSecrets values")
            if profile_id != secrets.public.profile_id:
                raise ValueError("profile map key differs from its public profile ID")
            checked[profile_id] = secrets
        self._profiles = MappingProxyType(checked)
        self.state_root = Path(state_root)
        self.clock = clock

    def __repr__(self):
        return f"SettingsExecutorProvider(profiles={sorted(self._profiles)!r})"

    def redact(self, text: str) -> str:
        """``text`` without any credential value this provider holds (8.5 review Minor 7). The values stay in memory;
        only their absence from ``text`` is observable."""
        text = str(text)
        for secrets in self._profiles.values():
            for name, value in secrets.settings.items():
                if isinstance(value, str) and len(value) >= 6 and _SECRET_SETTING.search(name):
                    text = text.replace(value, "[redacted]")
        return text

    def status(self) -> dict:
        """Public facts only, for the controller's status: never a setting."""
        return {"provider": "settings", "profiles": [
            {"profile_id": item.public.profile_id, "revision": item.public.revision,
             "provider": item.public.provider, "resource_id": item.public.exclusivity_key}
            for _, item in sorted(self._profiles.items())]}

    def _approved(self, profile_id, approved_revision, pinned) -> ProfileSecrets:
        secrets = self._profiles.get(profile_id)
        if secrets is None:
            raise ValueError(PROFILE_MISSING)
        if (not isinstance(pinned, (ExecutorProfile, SSHServerProfile)) or secrets.public.revision != approved_revision
                or approval_destination(secrets.public) != approval_destination(pinned)):
            raise ValueError(PROFILE_CHANGED)
        settings = secrets.settings
        if isinstance(pinned, ExecutorProfile):
            try:
                actual = runpod_transport_sha256(settings)
            except ValueError:
                actual = None
            if pinned.transport_settings_sha256 is None or actual != pinned.transport_settings_sha256:
                raise ValueError(TRANSPORT_CHANGED)
        else:
            try:
                config = server_settings(settings)
            except (TypeError, ValueError):
                raise ValueError(TRANSPORT_CHANGED) from None   # never echo setting values
            if config.transport != pinned.transport or config.total_hourly_cost_usd != pinned.total_hourly_cost_usd:
                raise ValueError(TRANSPORT_CHANGED)
        return secrets

    def _now(self) -> datetime:
        return datetime.fromtimestamp(self.clock(), tz=timezone.utc)

    @contextmanager
    def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
        secrets = self._approved(profile_id, approved_revision, pinned)
        from . import desktop_runtime
        ssh = isinstance(pinned, SSHServerProfile)
        identity = {name: getattr(pinned, name) for name in ("profile_id", "revision", "name", "credential_scope")}
        with desktop_runtime.executor_for(dict(secrets.settings), self.state_root, runtime_profile=runtime_profile,
                                          model_ops=model_ops, profile_identity=identity if ssh else None) as executor:
            if ssh:
                observation = _OBSERVATION.validate_python(executor.server_facts.model_dump())
                if observation_key(observation) != observation_key(pinned):
                    raise ValueError(SERVER_DIFFERS)
                if executor.server_profile != pinned:
                    raise ValueError(PROFILE_CHANGED)
            else:
                pod = getattr(executor, "pod", None) or {}
                if pod.get("id") != pinned.pod_id:
                    raise ValueError(POD_DIFFERS)
                observation = runpod_facts(pod, observed_at=self._now())
            # The runner compares it with the attempt's pinned profile (8.5 review Important 1): an alias of the same
            # Pod or server opened for another job never runs this one.
            executor.destination_profile = pinned
            yield ResolvedDestination(profile=pinned, observation=observation, executor=executor)

    @contextmanager
    def recover(self, profile_id, approved_revision, *, pinned, pinned_facts, manifest_sha256, runtime_profile=None):
        """A recovery executor for an attempt that may have reached its host. SSH: a recovery-only executor from a
        ``ServerApprovalReceipt`` built in memory (never stored in ``job_destinations``), tolerant of unrelated GPU
        inventory drift. RunPod: ``executor_for(preflight=False)`` on the pinned Pod, as v1 reconcile does."""
        secrets = self._approved(profile_id, approved_revision, pinned)
        facts = _OBSERVATION.validate_python(pinned_facts.model_dump())
        validate_resource_binding(pinned, facts)
        manifest_sha256 = _manifest_sha256(manifest_sha256)
        from . import desktop_runtime
        settings = dict(secrets.settings)
        if isinstance(pinned, SSHServerProfile):
            receipt = ServerApprovalReceipt.create(manifest_sha256, pinned, facts)
            with desktop_runtime.executor_for(settings, self.state_root, recovery=True, server_receipt=receipt,
                                              runtime_profile=runtime_profile) as executor:
                yield executor
            return
        with desktop_runtime.executor_for(settings, self.state_root, preflight=False,
                                          runtime_profile=runtime_profile) as executor:
            if (getattr(executor, "pod", None) or {}).get("id") != pinned.pod_id:
                raise ValueError(POD_DIFFERS)
            yield executor


class SimulatedExecutorProvider:
    """Destinations of simulation plans: the plan's own pinned observations, stamped with the injected clock.

    It contacts nothing, holds no settings and never imports ``desktop_runtime``. Its executor is always None (the
    simulated dispatcher takes none); claims of simulation plans hold ``simulation:`` locks, never the real hardware.
    """

    def __init__(self, observations: Iterable[ResourceObservation] = (), *,
                 clock: Callable[[], float] = time.time):
        pinned = {}
        for item in observations:
            item = _OBSERVATION.validate_python(item.model_dump())
            if pinned.setdefault(observation_key(item), item) != item:
                raise ValueError("conflicting simulated observations of one destination")
        self._observations = MappingProxyType(pinned)
        self.clock = clock

    @classmethod
    def for_plan(cls, plan, *, clock: Callable[[], float] = time.time):
        if plan.simulation is not True:
            raise ValueError("simulated destinations serve simulation plans only")
        return cls(plan.resource_snapshots, clock=clock)

    def status(self) -> dict:
        return {"provider": "simulated", "destinations": sorted(key for key, _ in self._observations)}

    @staticmethod
    def redact(text: str) -> str:
        return str(text)   # it holds no settings

    def _observation(self, profile_id, approved_revision, pinned):
        _check_pinned(profile_id, approved_revision, pinned)
        observation = self._observations.get(observation_key(pinned))
        if observation is None:
            raise ValueError(PROFILE_MISSING)
        return observation

    @contextmanager
    def resolve(self, profile_id, approved_revision, *, pinned, runtime_profile=None, model_ops=False):
        observation = self._observation(profile_id, approved_revision, pinned)
        observed = datetime.fromtimestamp(self.clock(), tz=timezone.utc)
        yield ResolvedDestination(profile=pinned, observation=observation.model_copy(update={"observed_at": observed}),
                                  executor=None)

    @contextmanager
    def recover(self, profile_id, approved_revision, *, pinned, pinned_facts, manifest_sha256, runtime_profile=None):
        self._observation(profile_id, approved_revision, pinned)
        _manifest_sha256(manifest_sha256)
        yield None
