"""Public pinned destinations and read-only observations; no credential lookup."""
from datetime import datetime

from pydantic import Field, field_validator

from .config import ResourceProfile, StrictModel
from .gpu_servers import SSHServerFacts, SSHServerProfile, validate_server_binding
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
