"""Worker protocol v2: the files that fence one launch directory.

``command.json`` is a ``WorkerRequest`` whose SHA256 is the worker's argv. ``start.json`` is a
``StartMarker`` created once with O_CREAT|O_EXCL: only its creator writes anything else in the launch
directory, so a launch directory starts at most one execution. ``result.json`` is an ``OperationResult``,
published atomically after ``artifacts.json``. The marker and the result carry the same ``launch_id``.

Module import touches only the standard library, pydantic and local contracts.
"""
from __future__ import annotations

import hashlib
import os
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .config import StrictModel
from .model_artifacts import check_link_free, fsync_directory
from .projects import Sha256
from .registry import canonical

Hex32 = Annotated[str, Field(pattern=r"^[0-9a-f]{32}$")]
OperationKind = Literal["train", "evaluate", "merge", "materialize"]
OPERATION_KINDS = ("train", "evaluate", "merge", "materialize")   # the worker's dispatch allowlist
COMMAND, START_MARKER, RESULT = "command.json", "start.json", "result.json"
COMMAND_LIMIT = 64 * 1024
_EVIDENCE_NAME = re.compile(r"^[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*$")


class WorkerRequest(StrictModel):
    """``command.json``: bytes are ``canonical(model_dump(mode="json"))``; their SHA256 is the worker argv."""
    schema_version: Literal[2] = 2
    attempt_id: Hex32
    launch_id: Hex32
    manifest_sha256: Sha256
    operation_sha256: Sha256                    # digest(canonical(manifest["operation"]))
    timeout_seconds: int = Field(strict=True, ge=1, le=604800)


class StartMarker(StrictModel):
    """``start.json``. Identities are the request's claims; None when the request failed verification."""
    schema_version: Literal[2] = 2
    launch_id: Hex32
    command_sha256: Sha256
    attempt_id: Hex32 | None = None
    manifest_sha256: Sha256 | None = None
    operation_sha256: Sha256 | None = None
    pid: int = Field(strict=True, ge=0)
    started_at: str = Field(min_length=1)


class OperationResult(StrictModel):
    """``result.json``; ``launch_id`` is the launch directory name and identities are only verified ones."""
    schema_version: Literal[2] = 2
    status: Literal["COMPLETED", "FAILED", "CANCELED"]
    launch_id: Hex32
    attempt_id: Hex32 | None = None
    manifest_sha256: Sha256 | None = None
    operation_sha256: Sha256 | None = None
    kind: OperationKind | None = None
    engine_version: str | None = None
    error: str | None = None
    evidence: dict[str, Sha256] = Field(default_factory=dict)   # launch-relative file -> SHA256 of its bytes

    @field_validator("evidence")
    @classmethod
    def relative_evidence(cls, value):
        if any(not _EVIDENCE_NAME.match(name) or ".." in name.split("/") for name in value):
            raise ValueError("evidence names are launch-relative POSIX paths")
        return value

    @model_validator(mode="after")
    def completed_identity(self):
        if self.status == "COMPLETED" and (
                None in (self.attempt_id, self.manifest_sha256, self.operation_sha256, self.kind)
                or self.error is not None):
            raise ValueError("a completed result names its attempt, manifest, operation and kind and carries no "
                             "error")
        return self


def read_worker_request(directory: Path, command_sha256: str, launch_id: str) -> WorkerRequest:
    """Read ``command.json``; its bytes must hash to the argv, be canonical and name this launch."""
    path = Path(directory) / COMMAND
    check_link_free(path)
    with path.open("rb") as stream:
        raw = stream.read(COMMAND_LIMIT + 1)
    if len(raw) > COMMAND_LIMIT or hashlib.sha256(raw).hexdigest() != command_sha256:
        raise ValueError("worker request hash mismatch")
    request = WorkerRequest.model_validate_json(raw)
    if canonical(request.model_dump(mode="json")) != raw:
        raise ValueError("worker request is not canonical")
    if request.launch_id != launch_id:
        raise ValueError("worker request launch identity differs from launch directory")
    return request


def create_start_marker(directory: Path, marker: StartMarker) -> bool:
    """Create ``start.json`` exclusively and durably; False (nothing written) if any marker already exists."""
    marker = StartMarker.model_validate(marker.model_dump(mode="json"))
    flags = os.O_CREAT | os.O_EXCL | os.O_WRONLY | getattr(os, "O_BINARY", 0) | getattr(os, "O_NOFOLLOW", 0)
    try:
        descriptor = os.open(Path(directory) / START_MARKER, flags, 0o644)
    except FileExistsError:
        return False
    with os.fdopen(descriptor, "wb") as stream:
        stream.write(canonical(marker.model_dump(mode="json")))
        stream.flush()
        os.fsync(stream.fileno())
    fsync_directory(directory)
    return True


def write_operation_result(directory: Path, result: OperationResult) -> None:
    """Publish ``result.json`` atomically (tmp -> replace) and durably; it is the completion marker."""
    data = canonical(OperationResult.model_validate(result.model_dump(mode="json")).model_dump(mode="json"))
    temporary = Path(directory) / "result.tmp"
    with temporary.open("wb") as stream:
        stream.write(data)
        stream.flush()
        os.fsync(stream.fileno())
    temporary.replace(Path(directory) / RESULT)
    fsync_directory(directory)
