"""Immutable registry revisions, with hashes checked at every use."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", value):
        raise ValueError("invalid registry identifier")
    return value


def parse_dataset(raw: bytes):
    text = raw.decode("utf-8-sig")
    try:
        parsed = json.loads(text)
        rows, file_format = (parsed, "json") if isinstance(parsed, list) else ([parsed], "jsonl")
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        file_format = "jsonl"
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("dataset must contain nonempty JSON/JSONL records")
    formatting = "sharegpt" if "messages" in rows[0] else "alpaca"
    for row in rows:
        if formatting == "sharegpt":
            messages = row.get("messages")
            if not isinstance(messages, list) or len(messages) < 2:
                raise ValueError("ShareGPT records require a messages array with prompt and answer")
            if any(not isinstance(m, dict) or m.get("role") not in {"system", "user", "assistant"}
                   or not isinstance(m.get("content"), str) or not m["content"].strip() for m in messages):
                raise ValueError("ShareGPT messages require system/user/assistant roles and nonempty string content")
            if messages[-1]["role"] != "assistant" or not any(m["role"] == "user" for m in messages):
                raise ValueError("ShareGPT training records require a user prompt and final assistant answer")
        elif (not isinstance(row.get("instruction"), str) or not row["instruction"].strip()
              or not isinstance(row.get("output"), str) or not row["output"].strip()
              or not isinstance(row.get("input", ""), str)):
            raise ValueError("each Alpaca row requires nonempty instruction/output and optional string input")
    return rows, formatting, file_format


def immutable_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as f:
            temporary = Path(f.name)
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        try:
            os.link(temporary, path)
        except FileExistsError:
            if path.read_bytes() != data:
                raise ValueError(f"immutable version already exists: {path.name}") from None
    finally:
        if temporary:
            temporary.unlink(missing_ok=True)


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def register_dataset(self, dataset_id, version, source, export_allowed=False):
        dataset_id, version = safe_name(dataset_id), safe_name(version)
        raw = Path(source).read_bytes()
        rows, formatting, file_format = parse_dataset(raw)
        sha = digest(raw)
        record = {"dataset_id": dataset_id, "version": version, "sha256": sha,
                  "rows": len(rows), "export_allowed": bool(export_allowed), "format": formatting,
                  "file_format": file_format}
        metadata = self.root / "datasets" / dataset_id / f"{version}.json"
        if metadata.exists() and metadata.read_bytes() != canonical(record):
            raise ValueError("immutable dataset version already registered")
        immutable_write(self.root / "blobs" / f"{sha}.json", raw)
        immutable_write(metadata, canonical(record))
        return record

    def get_dataset(self, dataset_id, version):
        record = json.loads((self.root / "datasets" / safe_name(dataset_id) / f"{safe_name(version)}.json").read_bytes())
        if digest(self.dataset_path(record).read_bytes()) != record["sha256"]:
            raise ValueError("dataset hash mismatch")
        return record

    def dataset_path(self, record):
        sha = record["sha256"]
        if not re.fullmatch("[0-9a-f]{64}", sha):
            raise ValueError("invalid dataset hash")
        return self.root / "blobs" / f"{sha}.json"

    def register_ai(self, config):
        body = canonical(config.model_dump())
        revision = digest(body)
        immutable_write(self.root / "ai" / safe_name(config.ai_id) / f"{revision}.json", body)
        return revision
