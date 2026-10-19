"""Public direct-SSH destinations and fixed, read-only Linux/NVIDIA observations.

Credential paths belong only to local settings. Public profiles and receipts never
contain keys, key paths, arbitrary commands, or provider-shaped stand-ins.
"""
import csv
import io
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .config import ResourceProfile, StrictModel
from .projects import LocalId, Sha256
from .registry import canonical, digest

GPUUUID = Annotated[str, Field(pattern=r"^GPU-[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}$")]


def safe_remote_root(value):
    if (not re.fullmatch(r"/[A-Za-z0-9_./-]+", value) or value.endswith("/")
            or any(part in {"", ".", ".."} for part in value.split("/")[1:])):
        raise ValueError("remote_root must be a safe absolute Linux directory below /")
    return value


class _SSHAddress(StrictModel):
    ssh_host: str = Field(pattern=r"^[A-Za-z0-9][A-Za-z0-9.:-]{0,252}$")
    ssh_port: int = Field(default=22, strict=True, ge=1, le=65535)
    ssh_username: str = Field(pattern=r"^[a-z_][a-z0-9_.-]{0,63}$")
    ssh_fingerprint: str = Field(pattern=r"^SHA256:[A-Za-z0-9+/]{43}$")
    remote_root: str
    selected_gpu_uuids: tuple[GPUUUID, ...] = Field(default=(), max_length=8)

    @field_validator("remote_root")
    @classmethod
    def safe_root(cls, value):
        return safe_remote_root(value)

    @field_validator("selected_gpu_uuids")
    @classmethod
    def unique_gpus(cls, value):
        if len(set(value)) != len(value):
            raise ValueError("selected GPU UUIDs must be unique")
        return value

class SSHTransport(_SSHAddress):
    key_reference_sha256: Sha256

    @property
    def sha256(self):
        return digest(canonical(self.model_dump(mode="json")))


class SSHServerSettings(_SSHAddress):
    """Local/native fields. Keys are resolved only by SSH; never copied to plans."""
    ssh_key_file: Path
    total_hourly_cost_usd: float = Field(ge=0)

    @field_validator("ssh_key_file", mode="before")
    @classmethod
    def local_key_reference(cls, value):
        text = str(value)
        if not text.strip() or text in {".", ".."} or any(ord(char) < 32 or ord(char) == 127 for char in text):
            raise ValueError("nonempty local SSH key file path without control characters required")
        return value

    @property
    def transport(self):
        body = self.model_dump(include=set(_SSHAddress.model_fields))
        body["key_reference_sha256"] = digest(str(Path(self.ssh_key_file).expanduser().resolve()).encode())
        return SSHTransport.model_validate(body)

    def profile(self, facts, *, profile_id="ssh-server", revision="1", name="SSH GPU server",
                credential_scope="local"):
        profile = SSHServerProfile(profile_id=profile_id, revision=revision, name=name,
                                   credential_scope=credential_scope, server_id=facts.server_id,
                                   transport=self.transport, total_hourly_cost_usd=self.total_hourly_cost_usd)
        validate_server_binding(profile, facts)
        return profile


def server_settings(settings):
    """Extract the exact native fields from a larger desktop settings dictionary."""
    if settings.get("provider", "existing_runpod") != "ssh_server":
        raise ValueError("SSH server provider required")
    # Native settings are strings. Separate keys retain the inactive Pod's pin/key.
    native = any(key.startswith("server_") for key in settings)
    prefix = "server_" if native else ""
    values = {key: settings[prefix + key] for key in SSHServerSettings.model_fields if prefix + key in settings}
    if isinstance(values.get("ssh_port"), str):
        if not re.fullmatch(r"[0-9]+", values["ssh_port"]):
            raise ValueError("SSH port must be an integer")
        values["ssh_port"] = int(values["ssh_port"])
    if isinstance(values.get("selected_gpu_uuids"), str):
        values["selected_gpu_uuids"] = json.loads(values["selected_gpu_uuids"])
    if isinstance(values.get("total_hourly_cost_usd"), str) and not values["total_hourly_cost_usd"].strip():
        raise ValueError("explicit operator hourly cost required; enter 0 for internal cost")
    return SSHServerSettings.model_validate(values)


class GPUDevice(StrictModel):
    uuid: GPUUUID
    name: str = Field(min_length=1, max_length=200)
    memory_mib: int = Field(strict=True, ge=1)


class SSHServerProfile(StrictModel):
    profile_id: LocalId
    revision: LocalId
    name: str = Field(min_length=1, max_length=200)
    provider: Literal["ssh_server"] = "ssh_server"
    credential_scope: LocalId
    server_id: Sha256
    transport: SSHTransport
    total_hourly_cost_usd: float = Field(ge=0)

    @model_validator(mode="after")
    def selected(self):
        if not self.transport.selected_gpu_uuids:
            raise ValueError("select at least one observed GPU UUID before planning or execution")
        return self

    @property
    def transport_settings_sha256(self):
        return self.transport.sha256

    @property
    def exclusivity_key(self):
        return "ssh_server:" + self.server_id

    @property
    def approval_identity(self):
        return {"resource_id": self.exclusivity_key}


class SSHServerInspection(StrictModel):
    provider: Literal["ssh_server"] = "ssh_server"
    server_id: Sha256
    transport: SSHTransport
    inventory: tuple[GPUDevice, ...] = Field(min_length=1, max_length=256)
    total_hourly_cost_usd: float = Field(ge=0)
    cost_source: Literal["operator"] = "operator"
    observed_at: datetime

    @field_validator("inventory")
    @classmethod
    def stable_inventory(cls, value):
        if len({gpu.uuid for gpu in value}) != len(value):
            raise ValueError("GPU UUIDs must be unique")
        return tuple(sorted(value, key=lambda gpu: gpu.uuid))

    @property
    def selected_gpu_uuids(self):
        return self.transport.selected_gpu_uuids

    @property
    def destination_sha256(self):
        return digest(canonical(dict(transport=self.transport.model_dump(mode="json"),
                                     total_hourly_cost_usd=self.total_hourly_cost_usd)))

    def validate_settings(self, settings):
        if self.transport != settings.transport or self.total_hourly_cost_usd != settings.total_hourly_cost_usd:
            raise ValueError("inspection destination differs from current settings")


class SSHServerFacts(StrictModel):
    provider: Literal["ssh_server"] = "ssh_server"
    server_id: Sha256
    transport_settings_sha256: Sha256
    inventory: tuple[GPUDevice, ...] = Field(min_length=1, max_length=256)
    selected_gpu_uuids: tuple[GPUUUID, ...] = Field(min_length=1, max_length=8)
    gpu_type: str = Field(min_length=1)
    gpu_count: int = Field(strict=True, ge=1, le=8)
    total_hourly_cost_usd: float = Field(ge=0)
    cost_source: Literal["operator"] = "operator"
    observed_at: datetime

    @field_validator("inventory")
    @classmethod
    def stable_inventory(cls, value):
        return tuple(sorted(value, key=lambda gpu: gpu.uuid))

    @field_validator("observed_at")
    @classmethod
    def timezone_required(cls, value):
        if value.utcoffset() is None:
            raise ValueError("resource observations require an explicit timezone")
        return value

    @model_validator(mode="after")
    def selected_inventory(self):
        inventory = {gpu.uuid: gpu for gpu in self.inventory}
        if len(inventory) != len(self.inventory) or len(set(self.selected_gpu_uuids)) != len(self.selected_gpu_uuids):
            raise ValueError("GPU UUIDs must be unique")
        if any(uuid not in inventory for uuid in self.selected_gpu_uuids):
            raise ValueError("selected GPU UUID is absent from observed inventory")
        if len(self.selected_gpu_uuids) != self.gpu_count:
            raise ValueError("selected GPU count differs from observation")
        if any(inventory[uuid].name != self.gpu_type for uuid in self.selected_gpu_uuids):
            raise ValueError("selected GPU types must match requested GPU type")
        return self

    @property
    def exclusivity_key(self):
        return "ssh_server:" + self.server_id

    def cost(self, seconds):
        if type(seconds) is not int or seconds < 1:
            raise ValueError("positive integer duration required")
        return self.total_hourly_cost_usd * seconds / 3600

    def legacy_resources(self, seconds):
        return ResourceProfile(gpu_count=self.gpu_count, gpu_type=self.gpu_type, timeout_seconds=seconds,
                               hourly_cost_usd=self.total_hourly_cost_usd / self.gpu_count,
                               max_cost_usd=max(self.cost(seconds), 1e-12))

    def validate_config(self, config):
        resource = config.resource_profile
        if resource.gpu_count != self.gpu_count:
            raise ValueError("claimed job GPU count differs from observed SSH server selection")
        if resource.gpu_type != self.gpu_type:
            raise ValueError("claimed job GPU type differs from observed SSH server selection")
        if self.total_hourly_cost_usd > resource.hourly_cost_usd * resource.gpu_count:
            raise ValueError("claimed job price exceeds approved resource budget")


def validate_server_binding(profile, facts):
    profile = SSHServerProfile.model_validate(profile.model_dump())
    facts = SSHServerFacts.model_validate(facts.model_dump())
    if (profile.server_id != facts.server_id or profile.transport.sha256 != facts.transport_settings_sha256
            or profile.transport.selected_gpu_uuids != facts.selected_gpu_uuids
            or profile.total_hourly_cost_usd != facts.total_hourly_cost_usd):
        raise ValueError("SSH server profile differs from verified destination observation")


# This source is fixed. The sole user-derived argument is a separately quoted,
# validated path. Inspection never creates files, installs tools, or loads data.
_LINUX_PROBE = """import json,os,platform,shutil,sys
from pathlib import Path
p=Path(sys.argv[1]); ancestors=(p,*p.parents)
safe=all(not x.is_symlink() and (not x.exists() or x.is_dir()) for x in ancestors)
existing=next(x for x in ancestors if x.exists())
print(json.dumps(dict(system=platform.system(),machine_id=Path('/etc/machine-id').read_text().strip(),
 python=list(sys.version_info[:3]),tools={x:bool(shutil.which(x)) for x in ('flock','cc')},
 workspace_safe=safe,workspace_writable=os.access(existing,os.W_OK|os.X_OK))))
"""


def probe_server(client, settings: SSHServerSettings, run_command, *, inspection_only=False):
    """Read Linux identity, tool/workspace facts and full NVIDIA UUID inventory."""
    settings = SSHServerSettings.model_validate(settings.model_dump())
    system = json.loads(run_command(client, ["python3", "-c", _LINUX_PROBE, settings.remote_root]))
    if system.get("system") != "Linux" or not re.fullmatch(r"[0-9a-fA-F]{32}", system.get("machine_id", "")):
        raise ValueError("supported Linux server with stable machine identity required")
    if not system.get("workspace_safe") or not system.get("workspace_writable"):
        raise ValueError("remote workspace must be writable and free of symlinks")
    if not all(system.get("tools", {}).get(name) for name in ("flock", "cc")):
        raise ValueError("server requires flock and a C compiler; ask its administrator")
    if tuple(system.get("python", ())[:2]) < (3, 8):
        raise ValueError("working Python 3.8 or newer required for private bootstrap")
    output = run_command(client, ["nvidia-smi", "--query-gpu=uuid,name,memory.total,mig.mode.current",
                                  "--format=csv,noheader,nounits"])
    devices = []
    for row in csv.reader(io.StringIO(output), skipinitialspace=True):
        if len(row) != 4 or row[3].strip() not in {"Disabled", "[N/A]", "N/A", "Not Supported"}:
            raise ValueError("unsupported NVIDIA MIG/partition or GPU inventory response")
        devices.append(GPUDevice(uuid=row[0].strip(), name=row[1].strip(), memory_mib=int(row[2].strip())))
    inventory = {gpu.uuid: gpu for gpu in devices}
    if len(inventory) != len(devices):
        raise ValueError("duplicate GPU UUID in observed inventory")
    if any(uuid not in inventory for uuid in settings.selected_gpu_uuids):
        raise ValueError("selected GPU UUID is absent from observed inventory")
    server_id = digest(system["machine_id"].lower().encode())
    if inspection_only:
        return SSHServerInspection(server_id=server_id, transport=settings.transport, inventory=tuple(devices),
                                   total_hourly_cost_usd=settings.total_hourly_cost_usd,
                                   observed_at=datetime.now(timezone.utc))
    if not settings.selected_gpu_uuids:
        raise ValueError("select at least one observed GPU UUID before preparing or executing")
    return SSHServerFacts(server_id=server_id,
                          transport_settings_sha256=settings.transport.sha256, inventory=tuple(devices),
                          selected_gpu_uuids=settings.selected_gpu_uuids,
                          gpu_count=len(settings.selected_gpu_uuids),
                          gpu_type=inventory[settings.selected_gpu_uuids[0]].name,
                          total_hourly_cost_usd=settings.total_hourly_cost_usd, observed_at=datetime.now(timezone.utc))


class ServerApprovalReceipt(StrictModel):
    """External v1 approval envelope; v1 manifest bytes remain unchanged."""
    manifest_sha256: Sha256
    profile: SSHServerProfile
    facts: SSHServerFacts
    sha256: Sha256

    @model_validator(mode="after")
    def valid_receipt(self):
        validate_server_binding(self.profile, self.facts)
        if digest(canonical(self.model_dump(mode="json", exclude={"sha256"}))) != self.sha256:
            raise ValueError("invalid server approval receipt digest")
        return self

    @classmethod
    def create(cls, manifest_sha256, profile, facts):
        body = dict(manifest_sha256=manifest_sha256, profile=profile.model_dump(mode="json"),
                    facts=facts.model_dump(mode="json"))
        return cls(**body, sha256=digest(canonical(body)))

    def validate_destination(self, manifest_sha256, transport, facts):
        type(self).model_validate(self.model_dump())
        if manifest_sha256 != self.manifest_sha256:
            raise ValueError("server receipt manifest differs from approved job")
        if (self.profile.transport != transport or self.profile.server_id != facts.server_id
                or self.facts.selected_gpu_uuids != facts.selected_gpu_uuids
                or self.facts.gpu_type != facts.gpu_type or self.facts.gpu_count != facts.gpu_count
                or self.facts.inventory != facts.inventory
                or self.facts.total_hourly_cost_usd != facts.total_hourly_cost_usd
                or transport.sha256 != facts.transport_settings_sha256):
            raise ValueError("server receipt destination differs from approved job")
