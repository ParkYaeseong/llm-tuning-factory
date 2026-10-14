"""Immutable registry revisions, with hashes checked at every use."""
import hashlib
import json
import re
from pathlib import Path


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", value):
        raise ValueError("invalid registry identifier")
    return value


def immutable_write(path: Path, data: bytes):
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open("xb") as f:
            f.write(data)
    except FileExistsError:
        if path.read_bytes() != data:
            raise ValueError(f"immutable version already exists: {path.name}") from None


class Registry:
    def __init__(self, root: Path):
        self.root = Path(root).resolve()

    def register_dataset(self, dataset_id, version, source, export_allowed=False):
        dataset_id, version = safe_name(dataset_id), safe_name(version)
        raw = Path(source).read_bytes()
        rows = json.loads(raw)
        if not isinstance(rows, list) or not rows:
            raise ValueError("dataset must be a nonempty Alpaca JSON array")
        for row in rows:
            if (not isinstance(row, dict) or not isinstance(row.get("instruction"), str)
                    or not row["instruction"].strip() or not isinstance(row.get("output"), str)
                    or not row["output"].strip() or not isinstance(row.get("input", ""), str)):
                raise ValueError("each row requires nonempty instruction/output and optional string input")
        sha = digest(raw)
        record = {"dataset_id": dataset_id, "version": version, "sha256": sha,
                  "rows": len(rows), "export_allowed": bool(export_allowed), "format": "alpaca"}
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
