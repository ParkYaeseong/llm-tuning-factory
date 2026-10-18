import json

import pytest

from tuning_factory.dataset_import import ImportMapping, ImportService
from tuning_factory.dataset_splits import (
    SplitPlan,
    assert_no_input_overlap,
    commit_split,
    input_fingerprint,
    preview_split,
)
from tuning_factory.projects import DatasetRef
from tuning_factory.registry import Registry


def registered(tmp_path, rows):
    path = tmp_path / "rows.json"
    path.write_text(json.dumps(rows), encoding="utf-8")
    registry = Registry(tmp_path / "registry")
    record = registry.register_dataset("source", "1", path)
    return registry, DatasetRef(**{k: record[k] for k in ("dataset_id", "version", "sha256")})


def plan(dataset, **updates):
    return SplitPlan(dataset=dataset, seed=42, train_basis_points=5000,
                     validation_basis_points=2500, test_basis_points=2500,
                     outputs={"train": {"dataset_id": "train", "version": "1"},
                              "validation": {"dataset_id": "validation", "version": "1"},
                              "test": {"dataset_id": "test", "version": "1"}}, **updates)


def test_fingerprints_normalize_inputs_and_ignore_answers_across_kinds():
    a = {"instruction": "cafe\u0301\r\n hello  world", "input": "ctx", "output": "one"}
    b = {"instruction": "café hello world", "input": "ctx", "chosen": "two", "rejected": "three"}
    assert input_fingerprint(a, "instruction") == input_fingerprint(b, "preference")
    assert input_fingerprint(a, "instruction") != input_fingerprint({**a, "input": "different"}, "instruction")
    history = [{"role": "user", "content": "q"}, {"role": "assistant", "content": "previous"},
               {"role": "user", "content": "next"}]
    assert input_fingerprint({"messages": history + [{"role": "assistant", "content": "a"}]}, "instruction") == input_fingerprint({"messages": history}, "preference")
    assert input_fingerprint({"text": " a \n b "}, "text") == input_fingerprint({"text": "a b"}, "text")


def test_transitive_input_or_group_components_never_cross_and_ordinals_keep_order(tmp_path):
    rows = [{"instruction": "q1", "output": "a", "group": "A"},
            {"instruction": "q2", "output": "a", "group": "A"},
            {"instruction": "q2", "output": "b", "group": "B"},
            {"instruction": "q3", "output": "a", "group": "B"},
            {"instruction": "q4", "output": "a", "group": "C"},
            {"instruction": "q5", "output": "a", "group": "D"}]
    registry, ref = registered(tmp_path, rows)
    spec = plan(ref, group_field=("group",))
    preview = preview_split(registry, spec)
    assert preview_split(registry, spec) == preview
    assignments = preview["assignments"]
    assert [row["ordinal"] for row in assignments] == list(range(6))
    assert len({row["split"] for row in assignments[:4]}) == 1
    committed = commit_split(registry, preview["plan_sha256"])
    assert commit_split(registry, preview["plan_sha256"]) == committed
    for role, output in committed["outputs"].items():
        record = registry.get_dataset(output["dataset_id"], output["version"])
        actual = json.loads(registry.dataset_path(record).read_bytes())
        assert actual == [row for row, allocation in zip(rows, assignments) if allocation["split"] == role]
    assert_no_input_overlap([DatasetRef(**committed["outputs"]["train"])],
                            [DatasetRef(**committed["outputs"]["test"])], registry=registry)


def test_group_field_resolves_original_mapped_source_records(tmp_path):
    source_path = tmp_path / "raw.json"
    source_path.write_text(json.dumps([{"q": str(i), "a": "answer", "meta": {"group": i // 2}}
                                      for i in range(6)]))
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(source_path)
    mapping = ImportMapping(source_format="json", fields={"instruction": ("q",), "output": ("a",)})
    preview = service.inspect(source["source_id"], mapping)
    result = service.register(source["source_id"], mapping, preview["preview_sha256"], "mapped", "1", False, False)
    registry = Registry(service.root)
    split = preview_split(registry, plan(result["dataset"], group_field=("meta", "group")))
    assigned = split["assignments"]
    assert all(assigned[i]["split"] == assigned[i + 1]["split"] for i in (0, 2, 4))
    committed = commit_split(registry, split["plan_sha256"])
    for role, ref in committed["outputs"].items():
        origins = registry.dataset_origins(registry.get_dataset(ref["dataset_id"], ref["version"]))
        assert origins == [row for row, assignment in zip(json.loads(source_path.read_text()), assigned)
                           if assignment["split"] == role]


def test_impossible_nonempty_splits_and_invalid_plans_fail(tmp_path):
    registry, ref = registered(tmp_path, [{"instruction": "same", "output": str(i)} for i in range(3)])
    with pytest.raises(ValueError, match="nonempty"):
        preview_split(registry, plan(ref))
    base = plan(ref).model_dump()
    for updates in ({"seed": 2**63}, {"seed": True}, {"train_basis_points": 5001},
                    {"group_field": "record.eval()"}, {"outputs": {"train": {"dataset_id": "x", "version": "1"}}}):
        with pytest.raises(ValueError):
            SplitPlan.model_validate({**base, **updates})


def test_ref_hash_and_existing_output_cannot_be_overwritten(tmp_path):
    registry, ref = registered(tmp_path, [{"instruction": str(i), "output": "a"} for i in range(4)])
    with pytest.raises(ValueError, match="hash"):
        preview_split(registry, plan(ref.model_copy(update={"sha256": "0" * 64})))
    preview = preview_split(registry, plan(ref))
    path = tmp_path / "collision"
    path.write_text('[{"instruction":"other","output":"answer"}]')
    registry.register_dataset("train", "1", path)
    before = registry.dataset_path(registry.get_dataset("train", "1")).read_bytes()
    with pytest.raises(ValueError, match="immutable"):
        commit_split(registry, preview["plan_sha256"])
    assert registry.dataset_path(registry.get_dataset("train", "1")).read_bytes() == before


def test_overlap_guard_checks_all_inputs_and_ref_hashes(tmp_path):
    registry, ref = registered(tmp_path, [{"instruction": "q", "output": "a"}])
    with pytest.raises(ValueError, match="overlap"):
        assert_no_input_overlap([ref], [ref], registry=registry)
    with pytest.raises(ValueError, match="hash"):
        assert_no_input_overlap([ref], [ref.model_copy(update={"sha256": "0" * 64})], registry=registry)


def test_split_commit_recovers_after_partial_outputs(tmp_path, monkeypatch):
    registry, ref = registered(tmp_path, [{"instruction": str(i), "output": "a"} for i in range(8)])
    preview = preview_split(registry, plan(ref))
    original = Registry.register_dataset
    count = 0

    def interrupted(self, *args, **kwargs):
        nonlocal count
        record = original(self, *args, **kwargs)
        count += 1
        if count == 1:
            raise OSError("partial commit")
        return record

    monkeypatch.setattr(Registry, "register_dataset", interrupted)
    with pytest.raises(OSError):
        commit_split(registry, preview["plan_sha256"])
    result = commit_split(registry, preview["plan_sha256"])
    assert set(result["outputs"]) == {"train", "validation", "test"}


def test_registered_overlap_check_cannot_be_bypassed_by_inline_rows(tmp_path):
    registry, ref = registered(tmp_path, [{"instruction": "q", "output": "a"}])
    forged = {**ref.model_dump(), "rows": [{"instruction": "unrelated", "output": "b"}]}
    with pytest.raises(ValueError):
        assert_no_input_overlap([ref], [forged], registry=registry)
    assert_no_input_overlap([{"rows": [{"instruction": "a", "output": "x"}]}],
                            [{"rows": [{"instruction": "b", "output": "x"}]}])


def test_split_preview_tampering_and_missing_groups_fail_before_commit(tmp_path):
    registry, ref = registered(tmp_path, [{"instruction": str(i), "output": "a"} for i in range(4)])
    with pytest.raises(ValueError, match="group field"):
        preview_split(registry, plan(ref, group_field=("missing",)))
    preview = preview_split(registry, plan(ref))
    path = registry.root / "split_previews" / f"{preview['plan_sha256']}.json"
    preview["assignments"][0]["split"] = "tampered"
    path.write_text(json.dumps(preview))
    with pytest.raises(ValueError, match="preview hash"):
        commit_split(registry, preview["plan_sha256"])
    assert not (registry.root / "datasets/train").exists()


def legacy_mixed_rows(formatting, actual_key, metadata_key):
    def conversation(prompt, key):
        if key == "messages":
            return [{"role": "user", "content": prompt}, {"role": "assistant", "content": "answer"}]
        return [{"from": "human", "value": prompt}, {"from": "gpt", "value": "answer"}]

    def row(prompt):
        return ({"instruction": prompt, "output": "answer"} if formatting == "alpaca"
                else {actual_key: conversation(prompt, actual_key)})

    rows = [row("shared prompt"), row("shared prompt"), row("independent one"), row("independent two")]
    rows[1][metadata_key] = conversation("unused metadata prompt", metadata_key)
    return rows


@pytest.mark.parametrize("formatting,actual_key,metadata_key", [
    ("alpaca", None, "messages"),
    ("alpaca", None, "conversations"),
    ("sharegpt", "conversations", "messages"),
    ("sharegpt", "messages", "conversations"),
])
def test_registered_overlap_uses_dataset_format_despite_unused_later_metadata(
        tmp_path, formatting, actual_key, metadata_key):
    rows = legacy_mixed_rows(formatting, actual_key, metadata_key)
    # Only the later row overlaps; its extra conversation-shaped field is unused.
    rows[0] = ({"instruction": "different", "output": "answer"} if formatting == "alpaca"
               else {actual_key: rows[2][actual_key]})
    registry, train = registered(tmp_path, rows)
    heldout_path = tmp_path / "heldout.json"
    heldout_path.write_text(json.dumps([legacy_mixed_rows(formatting, actual_key, metadata_key)[0]]))
    heldout_record = registry.register_dataset("heldout", "1", heldout_path)
    heldout = DatasetRef(**{key: heldout_record[key] for key in ("dataset_id", "version", "sha256")})
    before_metadata = (registry.root / "datasets/source/1.json").read_bytes()
    before_blob = registry.dataset_path(registry.get_dataset("source", "1")).read_bytes()
    with pytest.raises(ValueError, match="overlap"):
        assert_no_input_overlap([train], [heldout], registry=registry)
    with pytest.raises(ValueError, match="overlap"):
        assert_no_input_overlap([{"rows": rows}], [{"rows": json.loads(heldout_path.read_text())}])
    assert (registry.root / "datasets/source/1.json").read_bytes() == before_metadata
    assert registry.dataset_path(registry.get_dataset("source", "1")).read_bytes() == before_blob


@pytest.mark.parametrize("formatting,actual_key,metadata_key", [
    ("alpaca", None, "messages"),
    ("alpaca", None, "conversations"),
    ("sharegpt", "conversations", "messages"),
    ("sharegpt", "messages", "conversations"),
])
def test_split_groups_actual_prompts_despite_unused_later_metadata(tmp_path, formatting, actual_key, metadata_key):
    rows = legacy_mixed_rows(formatting, actual_key, metadata_key)
    registry, ref = registered(tmp_path, rows)
    preview = preview_split(registry, plan(ref))
    first, later = preview["assignments"][:2]
    assert first["input_sha256"] == later["input_sha256"]
    assert first["component_sha256"] == later["component_sha256"]
    assert first["split"] == later["split"]
    committed = commit_split(registry, preview["plan_sha256"])
    output = committed["outputs"][first["split"]]
    record = registry.get_dataset(output["dataset_id"], output["version"])
    assert json.loads(registry.dataset_path(record).read_bytes()) == rows[:2]


@pytest.mark.parametrize("formatting,actual_key,metadata_key", [
    ("alpaca", None, "messages"),
    ("alpaca", None, "conversations"),
    ("sharegpt", "conversations", "messages"),
])
def test_split_rejects_output_that_would_select_unused_metadata_as_input(tmp_path, formatting, actual_key, metadata_key):
    rows = legacy_mixed_rows(formatting, actual_key, metadata_key)
    rows[0] = rows.pop()  # Three distinct prompts force one row into each split.
    registry, ref = registered(tmp_path, rows)
    with pytest.raises(ValueError, match="dataset input format"):
        preview_split(registry, plan(ref))
    assert not (registry.root / "split_previews").exists()
    assert not (registry.root / "datasets/train").exists()
