"""Content-addressed model cache on a GPU host, for the SSH transport of v2 operations (Task 7.4).

An entry ``<cache>/<artifact_id>/<inventory_sha256>`` (``files/``, ``descriptor.json``, ``inventory.json``)
is reused only after the worker's fixed-argv ``--verify-model-cache`` check exits 0. A missing entry is
filled in a fresh incoming slot ``<cache>/.incoming/<launch_id>/<n>`` (the first ``n`` that an exclusive
``mkdir`` creates, so a failed copy never blocks a retry), verified there, then renamed into place with
``mv -T``: real directories, never a link, never over a non-empty entry. Nothing here deletes or replaces a
cache entry or an incoming copy; an operator inspects and removes them.

Every remote command is a fixed argv quoted with ``shlex.join``. ``execution.SSHExecutor`` supplies the
injected SSH client, its SFTP session, directory creation and the remote Python; this module never
imports it. Module import touches only the standard library and local lightweight contracts.
"""
from __future__ import annotations

import json
import re
import shlex
import stat
import time
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from .model_artifacts import ARTIFACT_LAYOUT, ModelArtifactRef, check_link_free
from .worker_protocol import CACHE_ENTRY_INVALID

CACHE_COMMAND_TIMEOUT = 3600   # hashing a multi-GB entry; the check is read-only and launches nothing
CORRUPT_CACHE = "remote model cache entry failed verification; remove it after inspection"
INCOMING_SLOTS = 100
_ARTIFACT_PATH = re.compile(r"[A-Za-z0-9_][A-Za-z0-9_.-]*(?:/[A-Za-z0-9_][A-Za-z0-9_.-]*)*")
_SHA256 = re.compile(r"[0-9a-f]{64}")


def remote_exists(sftp, path: str) -> bool:
    try:
        sftp.lstat(path)
    except OSError as exc:
        if exc.errno == 2:
            return False
        raise
    return True


def remote_regular_size(sftp, path: str) -> int | None:
    """The size of a regular remote file (``lstat``: a link is not followed); None if missing or not regular."""
    try:
        info = sftp.lstat(path)
    except OSError as exc:
        if exc.errno == 2:
            return None
        raise
    return info.st_size if stat.S_ISREG(info.st_mode) else None


@dataclass(frozen=True)
class CachedTransfer:
    """One staged artifact that the executor caches on the GPU host before a v2 launch (7.5 builds it).

    ``local_location`` is the control-plane entry ``<store>/<artifact_id>/<inventory_sha256>`` holding
    ``files/``, ``descriptor.json`` and ``inventory.json``.
    """
    ref: ModelArtifactRef
    local_location: Path

    def checked(self) -> CachedTransfer:
        """Layout and every inventory entry are valid; runs before any remote change."""
        ref = ModelArtifactRef.model_validate(self.ref)
        location = Path(self.local_location).absolute()
        check_link_free(location)
        if ((location.parent.name, location.name) != (ref.artifact_id, ref.inventory_sha256) or not location.is_dir()
                or {item.name for item in location.iterdir()} != ARTIFACT_LAYOUT):
            raise ValueError("transfer is not a control-plane model cache entry for its artifact")
        transfer = CachedTransfer(ref=ref, local_location=location)
        transfer.entries()
        return transfer

    def entries(self) -> tuple[tuple[str, int], ...]:
        """``(path, size)`` of every inventory file; each path is safe to name in a fixed argv on the host."""
        inventory = json.loads((Path(self.local_location) / "inventory.json").read_bytes())
        if not isinstance(inventory, list) or not inventory:
            raise ValueError("model artifact inventory cannot be published")
        entries = []
        for item in inventory:
            if (not isinstance(item, dict) or not isinstance(item.get("path"), str)
                    or not _ARTIFACT_PATH.fullmatch(item["path"]) or type(item.get("size")) is not int or item["size"] < 0
                    or not isinstance(item.get("sha256"), str) or not _SHA256.fullmatch(item["sha256"])):
                raise ValueError("model artifact inventory entry cannot be published safely")
            entries.append((item["path"], item["size"]))
        return tuple(entries)


def run_fixed(client, argv, timeout=CACHE_COMMAND_TIMEOUT) -> tuple[int, str]:
    """Run one fixed argv (``shlex.join``, never a free-form command); return its status and stderr[:200]."""
    _, stdout, stderr = client.exec_command(shlex.join(argv), timeout=timeout)
    channel = stdout.channel
    deadline = time.monotonic() + timeout
    while not channel.exit_status_ready():
        if time.monotonic() >= deadline:
            channel.close()
            raise TimeoutError("remote model cache command timed out; inspect the remote GPU host")
        time.sleep(1)
    status = channel.recv_exit_status()
    return status, stderr.read().decode("utf-8", errors="replace")[:200].strip()


def verified(client, remote_python, root, ref) -> bool:
    """The worker's check of ``<root>/<artifact_id>/<inventory_sha256>``: True verified, False corrupt.
    Any other exit status (missing runtime, unreadable entry) is not evidence about the entry and raises."""
    status, detail = run_fixed(client, [remote_python, "-m", "tuning_factory.worker", "--verify-model-cache", root,
                                        ref.artifact_id, ref.inventory_sha256])
    if status == 0:
        return True
    if status == CACHE_ENTRY_INVALID:
        return False
    raise RuntimeError(f"remote model cache check could not run (exit {status}): {detail}")


def ensure_cached(executor, sftp, transfer: CachedTransfer, key: str, source_remote_dir: str | None = None) -> None:
    """Reuse a verified entry or publish one through a fresh incoming slot of launch ``key``.

    With ``source_remote_dir`` (a finished launch's remote output) weight payload files whose remote size
    equals the frozen size are copied on the host with ``cp -P``. Metadata, configs and tokenizer files are
    always uploaded from the control-plane entry, because the frozen artifact may take them from the
    effective base while the engine re-saved its own bytes into the output.
    """
    from .operation_runner import WEIGHT_SUFFIXES  # lazy: execution stays light to import
    transfer = transfer.checked()
    entries = transfer.entries()
    ref, cache, client = transfer.ref, executor.model_cache_root, executor.client
    final = f"{cache}/{ref.artifact_id}/{ref.inventory_sha256}"
    executor.mkdir(sftp, cache)   # every component an ordinary directory: the cache never sits under a link
    if remote_exists(sftp, final):
        if not verified(client, executor.remote_python, cache, ref):
            raise ValueError(CORRUPT_CACHE)   # fail closed; never deleted or replaced
        return
    slot = _incoming_slot(executor, sftp, f"{cache}/.incoming/{key}")
    entry = f"{slot}/{ref.artifact_id}/{ref.inventory_sha256}"
    executor.mkdir(sftp, entry + "/files")
    local = transfer.local_location
    for name in ("descriptor.json", "inventory.json"):
        sftp.put(str(local / name), f"{entry}/{name}")
    for relative, size in entries:
        target = f"{entry}/files/{relative}"
        if "/" in relative:
            executor.mkdir(sftp, target.rsplit("/", 1)[0])
        origin = f"{source_remote_dir}/{relative}" if source_remote_dir is not None else None
        if (origin is not None and PurePosixPath(relative).suffix in WEIGHT_SUFFIXES
                and remote_regular_size(sftp, origin) == size):
            status, detail = run_fixed(client, ["cp", "-P", "--", origin, target])
            if status != 0:
                raise RuntimeError(f"remote model cache copy failed (exit {status}): {detail}")
        else:
            sftp.put(str(local / "files" / relative), target)
    if not verified(client, executor.remote_python, slot, ref):
        raise ValueError("uploaded model cache entry failed remote verification; the incoming copy is kept for "
                         "inspection")
    executor.mkdir(sftp, f"{cache}/{ref.artifact_id}")
    status, detail = run_fixed(client, ["mv", "-T", "--", entry, final], 60)
    if status != 0:
        # A concurrent publisher may have won: its entry must verify. Ours is left for manual cleanup.
        if not remote_exists(sftp, final):
            raise RuntimeError(f"remote model cache publish failed (exit {status}): {detail}; the incoming copy is "
                               "kept for inspection")
        if not verified(client, executor.remote_python, cache, ref):
            raise ValueError(CORRUPT_CACHE)


def _incoming_slot(executor, sftp, staging: str) -> str:
    """The first ``<staging>/<n>`` an exclusive mkdir creates; earlier (failed) slots are left untouched."""
    executor.mkdir(sftp, staging)
    for number in range(INCOMING_SLOTS):
        slot = f"{staging}/{number}"
        try:
            sftp.mkdir(slot)
        except OSError:
            if remote_exists(sftp, slot):
                continue
            raise
        return slot
    raise ValueError("no free incoming model cache slot for this launch; inspect and remove old incoming copies")
