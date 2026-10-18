"""Public pinned destinations and read-only observations; no credential lookup."""
from datetime import datetime

from pydantic import Field, field_validator

from .config import ResourceProfile, StrictModel
from .projects import LocalId, Sha256


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


class ResourceFacts(StrictModel):
    pod_id: LocalId
    gpu_type: str = Field(min_length=1)
    gpu_count: int = Field(strict=True, ge=1, le=8)
    total_hourly_cost_usd: float = Field(ge=0)
    observed_at: datetime

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
