import json

import pytest

from tuning_factory.dataset_import import ImportMapping, ImportService
from tuning_factory.registry import Registry


def mapping(source_format="json", **updates):
    return ImportMapping(source_format=source_format,
                         fields={"instruction": ("question",), "input": ("context",),
                                 "output": ("answer",)}, **updates)


@pytest.mark.parametrize("source_format,raw", [
    ("csv", b"question,context,answer\nq,c,a\n"),
    ("json", b'[{"question":"q","context":"c","answer":"a"}]'),
    ("jsonl", b'{"question":"q","context":"c","answer":"a"}\n'),
])
def test_formats_produce_identical_canonical_bytes_and_recoverable_origins(tmp_path, source_format, raw):
    path = tmp_path / "source"
    path.write_bytes(raw)
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    path.write_bytes(b"changed after snapshot")
    preview = service.inspect(source["source_id"], mapping(source_format))
    assert preview["issues"] == []
    assert preview["samples"] == [{"instruction": "q", "input": "c", "output": "a"}]
    assert preview["source_samples"] == [{"question": "q", "context": "c", "answer": "a"}]
    assert preview["source_fields"] == ["answer", "context", "question"]
    result = service.register(source["source_id"], mapping(source_format), preview["preview_sha256"],
                              "mapped", "1", False, False)
    registry = Registry(service.root)
    record = registry.get_dataset("mapped", "1")
    assert registry.dataset_path(record).read_bytes() == b'[{"input":"c","instruction":"q","output":"a"}]'
    assert result["canonical_sha256"] == record["sha256"]
    assert registry.dataset_origins(record)[0] == {"question": "q", "context": "c", "answer": "a"}
    assert service.register(source["source_id"], mapping(source_format), preview["preview_sha256"],
                            "mapped", "1", False, False) == result


def test_csv_cp949_requires_explicit_encoding(tmp_path):
    path = tmp_path / "data.csv"
    path.write_bytes("question,context,answer\n질문,문맥,답\n".encode("cp949"))
    service = ImportService(tmp_path)
    source = service.snapshot_local(path)
    assert service.inspect(source["source_id"], mapping("csv"))["issues"]
    assert service.inspect(source["source_id"], mapping("csv", encoding="cp949"))["issues"] == []
    with pytest.raises(ValueError):
        mapping("json", encoding="cp949")


@pytest.mark.parametrize("raw,code", [
    (b"question,question,context,answer\nq,q,c,a\n", "duplicate_header"),
    (b"question,context,answer\nq,c,\n", "invalid_row"),
])
def test_csv_bad_rows_are_structured_and_cannot_register(tmp_path, raw, code):
    path = tmp_path / "source"
    path.write_bytes(raw)
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping("csv"))
    assert any(issue["code"] == code for issue in preview["issues"])
    with pytest.raises(ValueError, match="issues"):
        service.register(source["source_id"], mapping("csv"), preview["preview_sha256"], "bad", "1", False, False)
    assert not (service.root / "datasets").exists()


def test_paths_are_bounded_literal_tuples_and_labels_are_exact_disjoint(tmp_path):
    with pytest.raises(ValueError):
        ImportMapping(source_format="json", fields={"instruction": "__import__('os').system('bad')"})
    with pytest.raises(ValueError):
        ImportMapping(source_format="json", fields={"instruction": tuple("x" for _ in range(17))})
    with pytest.raises(ValueError):
        ImportMapping(source_format="json", kind="kto", boolean_labels={"true": ("yes",), "false": ("yes",)})
    path = tmp_path / "kto.json"
    path.write_text('[{"q":"q","a":"a","label":"YES"}]')
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    spec = ImportMapping(source_format="json", kind="kto",
                         fields={"instruction": ("q",), "output": ("a",), "kto_tag": ("label",)},
                         boolean_labels={"true": ("yes",), "false": ("no",)})
    assert service.inspect(source["source_id"], spec)["issues"][0]["ordinal"] == 0
    path.write_text('[{"q":"q","a":"a","label":"yes"}]')
    source = service.snapshot_local(path)
    assert service.inspect(source["source_id"], spec)["samples"][0]["kto_tag"] is True


def test_preview_binds_mapping_and_source_and_canonical_size_is_limited(tmp_path):
    path = tmp_path / "data"
    path.write_text('[{"question":"q","context":"c","answer":"a"}]')
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping())
    changed = ImportMapping(source_format="json", fields={"instruction": ("question",), "output": ("context",)})
    with pytest.raises(ValueError, match="preview"):
        service.register(source["source_id"], changed, preview["preview_sha256"], "changed", "1", False, False)
    path.write_bytes(b"x" * (32 * 1024 * 1024 + 1))
    with pytest.raises(ValueError, match="32 MiB"):
        service.snapshot_local(path)
    # One source field repeated in two output columns expands beyond the limit.
    path.write_text(json.dumps([{"x": "x" * (17 * 1024 * 1024)}]))
    source = service.snapshot_local(path)
    large = ImportMapping(source_format="json", fields={"instruction": ("x",), "output": ("x",)})
    assert service.inspect(source["source_id"], large)["issues"][0]["code"] == "size_limit"


def test_registration_recovers_after_partial_write_without_changing_legacy_bytes(tmp_path, monkeypatch):
    path = tmp_path / "data"
    path.write_bytes(b'[{"question":"q","context":"c","answer":"a"}]')
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping())
    original = Registry.register_dataset
    calls = []

    def interrupted(self, *args, **kwargs):
        result = original(self, *args, **kwargs)
        calls.append(result)
        if len(calls) == 1:
            raise OSError("interrupted after dataset write")
        return result

    monkeypatch.setattr(Registry, "register_dataset", interrupted)
    with pytest.raises(OSError):
        service.register(source["source_id"], mapping(), preview["preview_sha256"], "mapped", "1", False, False)
    result = service.register(source["source_id"], mapping(), preview["preview_sha256"], "mapped", "1", False, False)
    assert result["dataset"]["dataset_id"] == "mapped"
    metadata = (service.root / "datasets/mapped/1.json").read_bytes()
    assert b"provenance" not in metadata
    legacy = tmp_path / "legacy"
    legacy.write_bytes(b'[{"instruction":"old","output":"answer"}]')
    registry = Registry(service.root)
    old = registry.register_dataset("legacy", "1", legacy)
    before = (registry.root / "datasets/legacy/1.json").read_bytes()
    assert registry.dataset_origins(old) == [{"instruction": "old", "output": "answer"}]
    assert (registry.root / "datasets/legacy/1.json").read_bytes() == before


def test_import_preview_and_registration_have_no_remote_or_job_side_effects(tmp_path, monkeypatch):
    import socket

    from tuning_factory.jobs import JobStore

    def forbidden(*args, **kwargs):
        pytest.fail("local import attempted external or job action")

    monkeypatch.setattr(socket, "create_connection", forbidden)
    monkeypatch.setattr(JobStore, "__init__", forbidden)
    path = tmp_path / "data"
    path.write_text('[{"question":"q","context":"c","answer":"a"}]')
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping())
    assert service.register(source["source_id"], mapping(), preview["preview_sha256"], "local", "1", False, False)["issues"] == []


def test_sharegpt_mapping_normalizes_custom_tags_and_nested_paths(tmp_path):
    path = tmp_path / "source"
    path.write_text(json.dumps([{"data": {"turns": [{"speaker": "human", "text": "q"},
                                                        {"speaker": "bot", "text": "a"}]}}]))
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    spec = ImportMapping(source_format="json", formatting="sharegpt", fields={"messages": ("data", "turns")},
                         message_tags={"role_tag": "speaker", "content_tag": "text", "user_tag": "human", "assistant_tag": "bot"})
    preview = service.inspect(source["source_id"], spec)
    assert preview["issues"] == []
    assert preview["samples"] == [{"messages": [{"role": "user", "content": "q"},
                                               {"role": "assistant", "content": "a"}]}]


def test_mapping_rejects_conflicting_conversation_fields_and_roles():
    with pytest.raises(ValueError):
        ImportMapping(source_format="json", formatting="sharegpt",
                      fields={"messages": ("a",), "conversations": ("b",)})
    # The custom user role collides with the legacy conversation default assistant.
    with pytest.raises(ValueError, match="distinct"):
        ImportMapping(source_format="json", formatting="sharegpt", fields={"conversations": ("turns",)},
                      message_tags={"user_tag": "gpt"})


def test_snapshot_and_provenance_tampering_are_detected(tmp_path):
    path = tmp_path / "source"
    path.write_text('[{"question":"q","context":"c","answer":"a"}]')
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping())
    result = service.register(source["source_id"], mapping(), preview["preview_sha256"], "mapped", "1", False, False)
    registry = Registry(service.root)
    pointer = json.loads((registry.root / "dataset_provenance/mapped/1.json").read_bytes())
    (registry.root / "provenance_blobs" / f"{pointer['sha256']}.json").write_bytes(b"{}")
    with pytest.raises(ValueError, match="provenance hash"):
        registry.dataset_origins(result["dataset"])
    (registry.root / "imports/sources" / f"{source['source_id']}.bin").write_bytes(b"{}")
    with pytest.raises(ValueError, match="snapshot hash"):
        service.inspect(source["source_id"], mapping())


def test_csv_text_fields_can_use_the_file_size_budget(tmp_path):
    path = tmp_path / "data.csv"
    path.write_text("question,context,answer\n" + "q" * 140000 + ",c,a\n")
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    preview = service.inspect(source["source_id"], mapping("csv"))
    assert preview["issues"] == []
    assert len(preview["samples"][0]["instruction"]) == 140000


@pytest.mark.parametrize("raw", [b'[{"question":NaN}]', b'[{"question":"\\ud800"}]'])
def test_nonfinite_and_invalid_unicode_sources_return_structured_issues(tmp_path, raw):
    path = tmp_path / "source"
    path.write_bytes(raw)
    service = ImportService(tmp_path / "registry")
    source = service.snapshot_local(path)
    assert service.inspect(source["source_id"], mapping())["issues"]
