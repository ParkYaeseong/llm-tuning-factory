"""Reproducible local splits that keep connected input/group components together."""

import json
import re
import unicodedata
from typing import Annotated

from pydantic import Field, field_validator, model_validator

from .config import StrictModel
from .dataset_import import FieldPath, checked_path, resolve_path
from .projects import DatasetRef, LocalId
from .registry import DATASET_KINDS, canonical, digest, immutable_write, parse_dataset

ROLES = ("train", "validation", "test")
BasisPoints = Annotated[int, Field(strict=True, ge=0, le=10000)]


class SplitOutput(StrictModel):
    dataset_id: LocalId
    version: LocalId


class SplitPlan(StrictModel):
    dataset: DatasetRef
    seed: Annotated[int, Field(strict=True, ge=-(2**63), le=2**63 - 1)]
    train_basis_points: BasisPoints
    validation_basis_points: BasisPoints
    test_basis_points: BasisPoints
    group_field: FieldPath | None = None
    outputs: dict[str, SplitOutput]

    @field_validator("group_field", mode="before")
    @classmethod
    def literal_group(cls, value):
        return checked_path(value) if value is not None else None

    @model_validator(mode="after")
    def valid_plan(self):
        weights = {role: getattr(self, f"{role}_basis_points") for role in ROLES}
        if sum(weights.values()) != 10000:
            raise ValueError("split basis points must sum to 10000")
        if set(self.outputs) != {role for role, weight in weights.items() if weight}:
            raise ValueError("outputs must match the requested nonempty splits")
        identities = [(output.dataset_id, output.version) for output in self.outputs.values()]
        if len(set(identities)) != len(identities) or (self.dataset.dataset_id, self.dataset.version) in identities:
            raise ValueError("split outputs must be distinct new dataset versions")
        return self


def _normalize(value):
    if isinstance(value, str):
        return " ".join(unicodedata.normalize("NFC", value).split())
    if isinstance(value, dict):
        return {key: _normalize(item) for key, item in value.items()}
    if isinstance(value, list):
        return [_normalize(item) for item in value]
    return value


def input_fingerprint(row, kind, *, formatting=None, message_key=None):
    """text-input-v1 input hash; dataset callers must pin the parsed input schema.

    Without schema keywords this pure single-record helper infers that record's
    format. Split/overlap boundaries always pass the dataset-wide parser choice.
    """
    if kind not in DATASET_KINDS or not isinstance(row, dict):
        raise ValueError("unsupported input fingerprint record")
    if formatting is None:
        formatting = "sharegpt" if kind != "text" and ("messages" in row or "conversations" in row) else "alpaca"
    if formatting not in {"alpaca", "sharegpt"}:
        raise ValueError("unsupported input fingerprint formatting")
    if message_key is not None and (formatting != "sharegpt" or message_key not in {"messages", "conversations"}):
        raise ValueError("invalid input fingerprint message key")
    if kind == "text":
        value = {"text": row.get("text", "")}
    elif formatting == "sharegpt":
        message_key = message_key or ("messages" if "messages" in row else "conversations")
        modern = message_key == "messages"
        messages = row[message_key]
        role_key, content_key = ("role", "content") if modern else ("from", "value")
        roles = {"human": "user", "gpt": "assistant"} if not modern else {}
        history = [{"role": roles.get(message[role_key], message[role_key]), "content": message[content_key]}
                   for message in messages]
        if kind in {"instruction", "kto"} and history and history[-1]["role"] == "assistant":
            history = history[:-1]
        value = {"messages": history, "system": row.get("system", ""), "tools": row.get("tools", "")}
    else:
        value = {key: row.get(key, "") for key in ("instruction", "input", "system", "tools")}
    return digest(canonical({"version": "text-input-v1", "input": _normalize(value)}))


def _input_schema(rows, formatting):
    # parse_dataset and dataset_descriptor choose this key once from the first
    # record, even when a later record contains another conversation-like field.
    return {"formatting": formatting,
            "message_key": ("messages" if "messages" in rows[0] else "conversations")
            if formatting == "sharegpt" else None}


def _dataset(registry, ref):
    ref = DatasetRef.model_validate(ref.model_dump() if isinstance(ref, DatasetRef) else ref)
    record = registry.get_dataset(ref.dataset_id, ref.version)
    if record["sha256"] != ref.sha256:
        raise ValueError("dataset reference hash mismatch")
    rows, formatting, _ = parse_dataset(registry.dataset_path(record).read_bytes(), record.get("kind", "instruction"))
    return record, rows, _input_schema(rows, formatting)


def assert_no_input_overlap(train_refs, heldout_refs, *, registry=None):
    """Reject known input overlap. Registered evidence requires an explicit registry.

    For pure in-memory checks callers may pass {'kind': ..., 'rows': [...]} objects;
    those objects are not registered provenance or authorization evidence.
    """
    def fingerprints(refs):
        found = set()
        for ref in refs:
            if (registry is None and isinstance(ref, dict) and "rows" in ref
                    and set(ref) <= {"kind", "rows"} and isinstance(ref["rows"], list)):
                kind, rows = ref.get("kind", "instruction"), ref["rows"]
                _, formatting, _ = parse_dataset(canonical(rows), kind)
                schema = _input_schema(rows, formatting)
            else:
                if registry is None:
                    raise ValueError("registry required for dataset references")
                record, rows, schema = _dataset(registry, ref)
                kind = record.get("kind", "instruction")
            found.update(input_fingerprint(row, kind, **schema) for row in rows)
        return found

    train = fingerprints(train_refs)
    heldout = fingerprints(heldout_refs)
    if train & heldout:
        raise ValueError("training and held-out inputs overlap")


def _preview(registry, plan):
    plan = SplitPlan.model_validate(plan.model_dump() if isinstance(plan, SplitPlan) else plan)
    record, rows, schema = _dataset(registry, plan.dataset)
    origins = registry.dataset_origins(record)
    parent_provenance = registry.dataset_provenance(record)
    source_ordinals = parent_provenance["source_ordinals"] if parent_provenance else list(range(len(rows)))
    parents = list(range(len(rows)))

    def root(index):
        while parents[index] != index:
            parents[index] = parents[parents[index]]
            index = parents[index]
        return index

    seen_inputs, seen_groups = {}, {}
    fingerprints = [input_fingerprint(row, record.get("kind", "instruction"), **schema) for row in rows]
    for ordinal, fingerprint in enumerate(fingerprints):
        keys = [(seen_inputs, fingerprint)]
        if plan.group_field is not None:
            try:
                group = resolve_path(origins[ordinal], plan.group_field)
                group_key = digest(canonical(group))
            except (KeyError, IndexError, ValueError, TypeError) as exc:
                raise ValueError(f"invalid group field at ordinal {ordinal}") from exc
            keys.append((seen_groups, group_key))
        for seen, key in keys:
            if key in seen:
                parents[root(ordinal)] = root(seen[key])
            else:
                seen[key] = ordinal
    components = {}
    for ordinal in range(len(rows)):
        components.setdefault(root(ordinal), []).append(ordinal)
    roles = [role for role in ROLES if role in plan.outputs]
    if len(components) < len(roles):
        raise ValueError("not enough independent components for requested nonempty splits")
    ranked = []
    for ordinals in components.values():
        component = digest(canonical([{"ordinal": ordinal, "input_sha256": fingerprints[ordinal],
                                     "row_sha256": digest(canonical(rows[ordinal]))} for ordinal in ordinals]))
        ranked.append((digest(canonical({"seed": plan.seed, "component_sha256": component})), component, ordinals))
    ranked.sort()
    weights = {role: getattr(plan, f"{role}_basis_points") for role in roles}
    counts, assigned, component_hashes = dict.fromkeys(roles, 0), {}, {}
    # Reserve the last independent components for still-empty requested roles.
    for index, (_, component_hash, ordinals) in enumerate(ranked):
        empty = [role for role in roles if counts[role] == 0]
        available = empty if len(ranked) - index == len(empty) else roles
        role = max(available, key=lambda role: (len(rows) * weights[role] - counts[role] * 10000, -ROLES.index(role)))
        counts[role] += len(ordinals)
        for ordinal in ordinals:
            assigned[ordinal], component_hashes[ordinal] = role, component_hash
    assignments = [{"ordinal": ordinal, "source_ordinal": source_ordinals[ordinal],
                    "row_sha256": digest(canonical(row)), "source_row_sha256": digest(canonical(origins[ordinal])),
                    "input_sha256": fingerprints[ordinal], "component_sha256": component_hashes[ordinal],
                    "split": assigned[ordinal]} for ordinal, row in enumerate(rows)]
    bodies, output_refs = {}, {}
    for role in roles:
        body = canonical([row for ordinal, row in enumerate(rows) if assigned[ordinal] == role])
        output_rows, output_formatting, _ = parse_dataset(body, record.get("kind", "instruction"))
        if _input_schema(output_rows, output_formatting) != schema:
            raise ValueError("split output changes the dataset input format; remove ambiguous unused metadata")
        bodies[role] = body
        output_refs[role] = {**plan.outputs[role].model_dump(), "sha256": digest(body)}
    manifest = {"schema_version": 1, "fingerprint_version": "text-input-v1", "plan": plan.model_dump(mode="json"),
                "source_provenance_sha256": digest(canonical(parent_provenance)),
                "assignments": assignments, "counts": counts, "outputs": output_refs}
    manifest["plan_sha256"] = digest(canonical(manifest))
    return manifest, bodies, record, origins, parent_provenance


def preview_split(registry, plan):
    manifest, _, _, _, _ = _preview(registry, plan)
    immutable_write(registry.root / "split_previews" / f"{manifest['plan_sha256']}.json", canonical(manifest))
    return manifest


def commit_split(registry, plan_sha256):
    if not isinstance(plan_sha256, str) or not re.fullmatch("[0-9a-f]{64}", plan_sha256):
        raise ValueError("invalid split plan hash")
    raw = (registry.root / "split_previews" / f"{plan_sha256}.json").read_bytes()
    saved = json.loads(raw)
    manifest, bodies, record, origins, parent_provenance = _preview(registry, saved["plan"])
    if manifest["plan_sha256"] != plan_sha256 or canonical(manifest) != raw:
        raise ValueError("split preview hash mismatch or dataset origins changed")
    for role, ref in manifest["outputs"].items():
        body = bodies[role]
        path = registry.root / "split_canonical" / f"{digest(body)}.json"
        immutable_write(path, body)
        allocations = [allocation for allocation in manifest["assignments"] if allocation["split"] == role]
        provenance = {"schema_version": 1, "kind": "split", "parent": manifest["plan"]["dataset"],
                      "parent_provenance_sha256": manifest["source_provenance_sha256"],
                      "plan_sha256": plan_sha256, "split": role,
                      "source_records": [origins[allocation["ordinal"]] for allocation in allocations],
                      "source_ordinals": [allocation["source_ordinal"] for allocation in allocations]}
        # Retain the original mapping/source identity through repeated splits.
        if parent_provenance:
            for key in ("source_id", "source_sha256", "mapping"):
                if key in parent_provenance:
                    provenance[key] = parent_provenance[key]
        registry.register_derived_dataset(ref["dataset_id"], ref["version"], path, provenance,
                                           kind=record.get("kind", "instruction"),
                                           synthetic=record["synthetic"], export_allowed=record["export_allowed"])
    immutable_write(registry.root / "split_commits" / f"{plan_sha256}.json", canonical(manifest))
    return manifest
