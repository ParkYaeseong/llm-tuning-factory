"""Immutable registry revisions, with hashes checked at every use."""
import hashlib
import json
import os
import re
import tempfile
from pathlib import Path
from urllib.parse import urlsplit


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def file_digest(path) -> str:
    with Path(path).open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def canonical(data) -> bytes:
    return json.dumps(data, sort_keys=True, ensure_ascii=False, separators=(",", ":"), allow_nan=False).encode()


def safe_name(value: str) -> str:
    if not re.fullmatch(r"[a-zA-Z0-9_-][a-zA-Z0-9_.-]*", value):
        raise ValueError("invalid registry identifier")
    return value


DATASET_KINDS = {"instruction", "text", "preference", "kto", "prompt"}


def parse_dataset(raw: bytes, kind=None):
    kind = kind or "instruction"
    if kind not in DATASET_KINDS:
        raise ValueError("unsupported dataset kind")
    text = raw.decode("utf-8-sig")
    try:
        parsed = json.loads(text)
        rows, file_format = (parsed, "json") if isinstance(parsed, list) else ([parsed], "jsonl")
    except json.JSONDecodeError:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
        file_format = "jsonl"
    if not rows or not all(isinstance(row, dict) for row in rows):
        raise ValueError("dataset must contain nonempty JSON/JSONL records")
    formatting = ("sharegpt" if kind != "text" and
                  any(key in rows[0] for key in ("messages", "conversations")) else "alpaca")
    if kind == "prompt" and formatting == "sharegpt":
        raise ValueError("PPO prompts currently require Alpaca instruction records; ShareGPT prompts are unsupported")
    for optional in ("input", "system", "tools"):
        present = [optional in row for row in rows]
        if any(present) and not all(present):
            raise ValueError(f"optional column {optional} must be present consistently in every record")
        if any(present) and any(not isinstance(row[optional], str) for row in rows):
            raise ValueError(f"optional column {optional} must contain strings")
    message_key = "messages" if "messages" in rows[0] else "conversations"
    role_key, content_key = ("role", "content") if message_key == "messages" else ("from", "value")
    user, assistant, system = ("user", "assistant", "system") if message_key == "messages" else ("human", "gpt", "system")

    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())

    def valid_message(message, expected_role=None):
        return (isinstance(message, dict) and message.get(role_key) in {system, user, assistant}
                and nonempty(message.get(content_key))
                and (expected_role is None or message.get(role_key) == expected_role))

    for row in rows:
        if any(row.get(column) for column in ("images", "videos", "audios")):
            raise ValueError("media datasets are unsupported; this application currently trains on text records only")
        if kind == "text":
            if not nonempty(row.get("text")):
                raise ValueError("pretraining records require nonempty text")
            continue
        if formatting == "sharegpt":
            messages = row.get(message_key)
            if not isinstance(messages, list) or not messages or any(not valid_message(m) for m in messages):
                raise ValueError("ShareGPT messages require valid roles and nonempty string content")
            if not any(m[role_key] == user for m in messages):
                raise ValueError("ShareGPT records require a user prompt")
            conversation = messages[1:] if messages[0][role_key] == system else messages
            if any(message[role_key] != (user if index % 2 == 0 else assistant)
                   for index, message in enumerate(conversation)):
                raise ValueError("ShareGPT messages must alternate user and assistant after an optional initial system")
            if kind in {"instruction", "kto"} and messages[-1][role_key] != assistant:
                raise ValueError("ShareGPT training records require a final assistant answer")
            if kind in {"preference", "prompt"} and messages[-1][role_key] != user:
                raise ValueError("ShareGPT preference/prompt records must end with a user prompt")
        elif (not nonempty(row.get("instruction")) or not isinstance(row.get("input", ""), str)
              or (kind in {"instruction", "kto"} and not nonempty(row.get("output")))):
            raise ValueError("Alpaca records require instruction and stage-appropriate output")
        if kind == "preference":
            chosen, rejected = row.get("chosen"), row.get("rejected")
            valid_pair = (valid_message(chosen, assistant) and valid_message(rejected, assistant)
                          if formatting == "sharegpt" else nonempty(chosen) and nonempty(rejected))
            if not valid_pair or chosen == rejected:
                raise ValueError("preference records require distinct chosen and rejected responses")
        if kind == "kto" and type(row.get("kto_tag")) is not bool:
            raise ValueError("KTO records require a boolean kto_tag")
    return rows, formatting, file_format


def dataset_descriptor(raw, kind, filename):
    rows, formatting, _ = parse_dataset(raw, kind)
    descriptor = {"file_name": filename, "formatting": formatting}
    if kind == "text":
        descriptor["columns"] = {"prompt": "text"}
    elif formatting == "sharegpt":
        messages = "messages" if "messages" in rows[0] else "conversations"
        canonical_roles = messages == "messages"
        descriptor["columns"] = {"messages": messages}
        descriptor["tags"] = {"role_tag": "role" if canonical_roles else "from",
                              "content_tag": "content" if canonical_roles else "value",
                              "user_tag": "user" if canonical_roles else "human",
                              "assistant_tag": "assistant" if canonical_roles else "gpt", "system_tag": "system"}
    else:
        descriptor["columns"] = {"prompt": "instruction"}
        if any("input" in row for row in rows):
            descriptor["columns"]["query"] = "input"
        if kind in {"instruction", "kto"}:
            descriptor["columns"]["response"] = "output"
    for optional in ("system", "tools"):
        if any(optional in row for row in rows):
            descriptor["columns"][optional] = optional
    if kind == "preference":
        descriptor["ranking"] = True
        descriptor["columns"].update(chosen="chosen", rejected="rejected")
    if kind == "kto":
        descriptor["columns"]["kto_tag"] = "kto_tag"
    return descriptor


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

    def register_dataset(self, dataset_id, version, source, export_allowed=False, synthetic=False,
                         kind="instruction", source_metadata=None):
        dataset_id, version = safe_name(dataset_id), safe_name(version)
        raw = Path(source).read_bytes()
        rows, formatting, file_format = parse_dataset(raw, kind)
        sha = digest(raw)
        record = {"dataset_id": dataset_id, "version": version, "sha256": sha,
                  "rows": len(rows), "export_allowed": bool(export_allowed), "format": formatting,
                  "file_format": file_format, "synthetic": bool(synthetic)}
        # Keep historical instruction registrations byte-compatible.
        if kind != "instruction":
            record["kind"] = kind
        if source_metadata is not None:
            allowed = {"kind", "bucket", "key", "version_id", "etag", "size", "sha256", "endpoint_url", "region"}
            if (not isinstance(source_metadata, dict) or set(source_metadata) - allowed
                    or source_metadata.get("kind") != "s3"):
                raise ValueError("invalid dataset source metadata")
            endpoint = urlsplit(source_metadata.get("endpoint_url", ""))
            if endpoint.username or endpoint.password or endpoint.query or endpoint.fragment:
                raise ValueError("unsafe dataset source endpoint")
            if source_metadata.get("sha256", sha) != sha or source_metadata.get("size", len(raw)) != len(raw):
                raise ValueError("dataset source integrity mismatch")
            record["source"] = source_metadata
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
