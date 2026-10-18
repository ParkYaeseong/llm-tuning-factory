"""Local, immutable source snapshots and explicit text dataset mappings."""

import csv
import io
import json
import re
from pathlib import Path
from typing import Annotated, Literal

from pydantic import Field, field_validator, model_validator

from .config import StrictModel
from .registry import Registry, canonical, digest, immutable_write, parse_dataset

MAX_DATASET_BYTES = 32 * 1024 * 1024
PathPart = Annotated[str, Field(min_length=1, max_length=128)] | Annotated[int, Field(strict=True, ge=0, le=1000000)]
FieldPath = Annotated[tuple[PathPart, ...], Field(min_length=1, max_length=16)]
DatasetKind = Literal["instruction", "text", "preference", "kto", "prompt"]


def checked_path(value):
    """Paths are literal key/index sequences, never Python/JSONPath expressions."""
    if not isinstance(value, (tuple, list)):
        raise ValueError("field paths must be bounded tuples of literal keys/indexes")
    if any(type(part) not in (str, int) for part in value):
        raise ValueError("field paths require literal string keys or nonnegative indexes")
    return value


def resolve_path(record, path):
    value = record
    for part in path:
        if isinstance(value, dict) and type(part) is str:
            value = value[part]
        elif isinstance(value, list) and type(part) is int:
            value = value[part]
        else:
            raise ValueError("field path does not match the source record")
    return value


class ImportMapping(StrictModel):
    schema_version: Literal[1] = 1
    transform_version: Literal["text-mapping-v1"] = "text-mapping-v1"
    source_format: Literal["csv", "json", "jsonl"]
    encoding: Literal["utf-8", "utf-8-sig", "cp949"] = "utf-8"
    csv_delimiter: str = Field(default=",", min_length=1, max_length=1)
    kind: DatasetKind = "instruction"
    formatting: Literal["alpaca", "sharegpt"] = "alpaca"
    fields: dict[str, FieldPath] = Field(default_factory=dict)
    message_tags: dict[str, str] = Field(default_factory=dict)
    boolean_labels: dict[Literal["true", "false"], tuple[str, ...]] = Field(default_factory=dict)

    @field_validator("fields", mode="before")
    @classmethod
    def paths_are_literal(cls, value):
        if not isinstance(value, dict):
            raise ValueError("fields must be an object")
        for path in value.values():
            checked_path(path)
        return value

    @model_validator(mode="after")
    def valid_mapping(self):
        if self.encoding == "cp949" and self.source_format != "csv":
            raise ValueError("CP949 is supported only for CSV")
        if self.csv_delimiter in "\r\n\x00\"":
            raise ValueError("invalid CSV delimiter")
        allowed = {"instruction", "input", "output", "text", "chosen", "rejected", "kto_tag",
                   "system", "tools", "messages", "conversations"}
        if set(self.fields) - allowed:
            raise ValueError("unsupported canonical field")
        if {"messages", "conversations"} <= set(self.fields):
            raise ValueError("map only one conversation field")
        tags = {"role_tag", "content_tag", "user_tag", "assistant_tag", "system_tag"}
        if set(self.message_tags) - tags or any(not tag for tag in self.message_tags.values()):
            raise ValueError("invalid message tags")
        legacy = "conversations" in self.fields
        role_tags = [self.message_tags.get(key, default) for key, default in
                     (("user_tag", "human" if legacy else "user"),
                      ("assistant_tag", "gpt" if legacy else "assistant"), ("system_tag", "system"))]
        if len(set(role_tags)) != 3:
            raise ValueError("message role tags must be distinct")
        if self.boolean_labels:
            yes, no = self.boolean_labels.get("true", ()), self.boolean_labels.get("false", ())
            if (not yes or not no or set(yes) & set(no) or len(set(yes)) != len(yes)
                    or len(set(no)) != len(no) or any(not label for label in (*yes, *no))):
                raise ValueError("boolean labels must be nonempty disjoint exact label sets")
        return self


def read_bounded(path):
    with Path(path).open("rb") as stream:
        raw = stream.read(MAX_DATASET_BYTES + 1)
    if len(raw) > MAX_DATASET_BYTES:
        raise ValueError("dataset exceeds 32 MiB limit")
    return raw


def _source_rows(raw, mapping):
    text = raw.decode(mapping.encoding).removeprefix("\ufeff")
    if mapping.source_format == "csv":
        csv.field_size_limit(max(csv.field_size_limit(), MAX_DATASET_BYTES))
        reader = csv.reader(io.StringIO(text, newline=""), delimiter=mapping.csv_delimiter, strict=True)
        headers = next(reader, [])
        if len(set(headers)) != len(headers):
            raise ValueError("duplicate_header: duplicate CSV headers")
        if not headers or any(not header.strip() for header in headers):
            raise ValueError("invalid CSV headers")
        rows = []
        for values in reader:
            if len(values) != len(headers):
                raise ValueError("CSV row has a different number of columns")
            rows.append(dict(zip(headers, values)))
    elif mapping.source_format == "json":
        rows = json.loads(text)
        if not isinstance(rows, list):
            raise ValueError("JSON source must contain an array")
    else:
        rows = [json.loads(line) for line in text.splitlines() if line.strip()]
    if not rows or any(not isinstance(row, dict) for row in rows):
        raise ValueError("source must contain nonempty records")
    # Python's JSON decoder otherwise accepts NaN and escaped lone surrogates.
    # Origins and preview samples must themselves be valid finite UTF-8 JSON.
    canonical(rows)
    return rows


def _mapped_row(source, mapping):
    row = {key: resolve_path(source, path) for key, path in mapping.fields.items()}
    if mapping.formatting == "sharegpt":
        message_key = "messages" if "messages" in row else "conversations"
        raw_messages = row.pop(message_key, None)
        if not isinstance(raw_messages, list):
            raise ValueError("mapped conversation must be a list")
        defaults = ({"role_tag": "from", "content_tag": "value", "user_tag": "human", "assistant_tag": "gpt"}
                    if message_key == "conversations" else {})
        tags = {"role_tag": "role", "content_tag": "content", "user_tag": "user",
                "assistant_tag": "assistant", "system_tag": "system", **defaults, **mapping.message_tags}
        if len({tags["user_tag"], tags["assistant_tag"], tags["system_tag"]}) != 3:
            raise ValueError("message role tags must be distinct")
        roles = {tags["user_tag"]: "user", tags["assistant_tag"]: "assistant", tags["system_tag"]: "system"}

        def message(value):
            if not isinstance(value, dict):
                raise ValueError("mapped message must be an object")
            return {"role": roles[value[tags["role_tag"]]], "content": value[tags["content_tag"]]}

        row["messages"] = [message(value) for value in raw_messages]
        if mapping.kind == "preference":
            for key in ("chosen", "rejected"):
                row[key] = message(row[key])
    elif "messages" in row or "conversations" in row:
        raise ValueError("conversation fields require sharegpt formatting")
    if mapping.kind == "kto" and type(row.get("kto_tag")) is not bool:
        value = row.get("kto_tag")
        if not isinstance(value, str) or not mapping.boolean_labels:
            raise ValueError("KTO labels require booleans or explicit exact labels")
        if value in mapping.boolean_labels["true"]:
            row["kto_tag"] = True
        elif value in mapping.boolean_labels["false"]:
            row["kto_tag"] = False
        else:
            raise ValueError("ambiguous or unknown KTO boolean label")
    parse_dataset(canonical([row]), mapping.kind)
    return row


class ImportService:
    def __init__(self, root):
        self.root = Path(root).resolve()

    def snapshot_local(self, path):
        raw = read_bounded(path)
        source_id = digest(raw)
        immutable_write(self.root / "imports" / "sources" / f"{source_id}.bin", raw)
        return {"source_id": source_id, "source_sha256": source_id, "size": len(raw)}

    def _source(self, source_id):
        if not isinstance(source_id, str) or not re.fullmatch("[0-9a-f]{64}", source_id):
            raise ValueError("invalid source ID")
        raw = read_bounded(self.root / "imports" / "sources" / f"{source_id}.bin")
        if digest(raw) != source_id:
            raise ValueError("source snapshot hash mismatch")
        return raw

    def _inspect(self, source_id, mapping):
        mapping = ImportMapping.model_validate(mapping.model_dump() if isinstance(mapping, ImportMapping) else mapping)
        raw = self._source(source_id)
        issues, rows, original_rows = [], [], []
        try:
            original_rows = _source_rows(raw, mapping)
        except (ValueError, UnicodeError, csv.Error) as exc:
            code = "duplicate_header" if str(exc).startswith("duplicate_header:") else "invalid_source"
            issues.append({"ordinal": None, "code": code, "message": str(exc)})
        for ordinal, source in enumerate(original_rows):
            try:
                rows.append(_mapped_row(source, mapping))
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                issues.append({"ordinal": ordinal, "code": "invalid_row", "message": str(exc)})
        body = None
        if rows:
            try:
                body = canonical(rows)
                if len(body) > MAX_DATASET_BYTES:
                    issues.append({"ordinal": None, "code": "size_limit", "message": "canonical dataset exceeds 32 MiB limit"})
                    body = None
                else:
                    parse_dataset(body, mapping.kind)
            except (ValueError, UnicodeError) as exc:
                issues.append({"ordinal": None, "code": "invalid_dataset", "message": str(exc)})
                body = None
        result = {"source_id": source_id, "source_sha256": digest(raw), "source_size": len(raw),
                  "canonical_sha256": digest(body) if body is not None else None,
                  "canonical_size": len(body) if body is not None else None,
                  "row_count": len(original_rows), "samples": rows[:5], "issues": issues,
                  "source_samples": original_rows[:5],
                  "source_fields": sorted({key for row in original_rows for key in row}),
                  "mapping": mapping.model_dump(mode="json")}
        result["preview_sha256"] = digest(canonical(result))
        return result, body, original_rows, mapping

    def inspect(self, source_id, mapping):
        result, body, _, _ = self._inspect(source_id, mapping)
        if body is not None:
            immutable_write(self.root / "imports" / "canonical" / f"{digest(body)}.json", body)
        immutable_write(self.root / "imports" / "previews" / f"{result['preview_sha256']}.json", canonical(result))
        return result

    def register(self, source_id, mapping, preview_sha256, dataset_id, version, synthetic=False, export_allowed=False):
        result, body, originals, mapping = self._inspect(source_id, mapping)
        if result["preview_sha256"] != preview_sha256:
            raise ValueError("import preview changed; inspect the mapping again")
        preview_path = self.root / "imports" / "previews" / f"{preview_sha256}.json"
        if not preview_path.exists() or preview_path.read_bytes() != canonical(result):
            raise ValueError("unknown or changed import preview")
        if result["issues"] or body is None:
            raise ValueError("import has validation issues")
        path = self.root / "imports" / "canonical" / f"{digest(body)}.json"
        immutable_write(path, body)
        registry = Registry(self.root)
        provenance = {"schema_version": 1, "kind": "mapped_import", "source_id": source_id,
                      "source_sha256": result["source_sha256"], "mapping": mapping.model_dump(mode="json"),
                      "preview_sha256": preview_sha256, "source_records": originals,
                      "source_ordinals": list(range(len(originals)))}
        record = registry.register_derived_dataset(dataset_id, version, path, provenance,
                                                   synthetic=synthetic, export_allowed=export_allowed, kind=mapping.kind)
        return {**result, "dataset": {key: record[key] for key in ("dataset_id", "version", "sha256")}}
