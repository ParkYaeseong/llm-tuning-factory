import hashlib
import json
import sqlite3
from concurrent.futures import ThreadPoolExecutor

import pytest
from pydantic import ValidationError

from tuning_factory.projects import DatasetBinding, DatasetRef, ProjectSpec, ProjectStore


def test_empty_project_has_no_paper_identity(tmp_path):
    store = ProjectStore(tmp_path)
    created = store.create("request-1", "새로운 목적", "자유 텍스트 학습", "text")
    assert created.spec.name == "새로운 목적"
    assert created.spec.project_id.isascii()
    assert created.spec.datasets == ()
    assert created.spec.graph == {}
    assert created.spec.legacy_origin is None
    assert "paper" not in json.dumps(created.spec.model_dump())
    assert store.get(created.spec.project_id) == created
    assert store.list() == [created]


def test_revision_conflict_never_overwrites(tmp_path):
    store = ProjectStore(tmp_path)
    first = store.create("request-1", "First", "", "")
    changed = first.spec.model_copy(update={"name": "Second"})
    second = store.save(changed, expected_revision=first.revision_sha256)
    with pytest.raises(ValueError, match="revision conflict"):
        store.save(first.spec.model_copy(update={"name": "Stale"}), first.revision_sha256)
    assert store.get(first.spec.project_id) == second
    assert store.get(first.spec.project_id, first.revision_sha256) == first
    assert second.revision_sha256 != first.revision_sha256


def test_request_id_is_idempotent(tmp_path):
    store = ProjectStore(tmp_path)
    first = store.create("same", "동일", "", "")
    store.save(first.spec.model_copy(update={"name": "edited"}), first.revision_sha256)
    assert ProjectStore(tmp_path).create("same", "동일", "", "") == first
    assert len(store.list()) == 1
    with pytest.raises(ValueError, match="request"):
        store.create("same", "different", "", "")


def test_competing_save_has_one_winner(tmp_path):
    first = ProjectStore(tmp_path).create("request", "Original", "", "")

    def save(name):
        try:
            return ProjectStore(tmp_path).save(
                first.spec.model_copy(update={"name": name}), first.revision_sha256
            )
        except ValueError as exc:
            assert "revision conflict" in str(exc)
            return None

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(save, ["A", "B"]))
    assert sum(result is not None for result in results) == 1
    assert ProjectStore(tmp_path).get(first.spec.project_id) in results


def test_competing_create_request_has_one_project(tmp_path):
    ProjectStore(tmp_path)

    def create(_):
        return ProjectStore(tmp_path).create("same-request", "Shared", "", "")

    with ThreadPoolExecutor(max_workers=2) as workers:
        results = list(workers.map(create, range(2)))
    assert results[0] == results[1]
    assert ProjectStore(tmp_path).list() == [results[0]]


def test_revision_is_content_addressed_and_corruption_is_rejected(tmp_path):
    store = ProjectStore(tmp_path)
    created = store.create("request", "해시", "", "")
    body = json.dumps(created.spec.model_dump(mode="json"), sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False).encode()
    assert created.revision_sha256 == hashlib.sha256(body).hexdigest()
    assert store.save(created.spec, created.revision_sha256) == created
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE definition_revisions SET body=? WHERE namespace='projects'", (b'{}',))
    with pytest.raises(ValueError, match="hash mismatch"):
        store.get(created.spec.project_id)


def test_draft_graph_preserved_without_execution_validation(tmp_path):
    store = ProjectStore(tmp_path)
    first = store.create("request", "Draft", "", "")
    draft = {"nodes": [{"id": "unfinished", "recipe": None}], "edges": [["missing", "unfinished"]]}
    saved = store.save(first.spec.model_copy(update={"graph": draft}), first.revision_sha256)
    draft["nodes"].clear()
    assert store.get(first.spec.project_id).spec.graph["nodes"] == [{"id": "unfinished", "recipe": None}]
    assert saved.spec.graph["edges"] == [["missing", "unfinished"]]


@pytest.mark.parametrize("graph", [{"value": float("nan")}, {"object": object()}, {1: "key"}, []])
def test_graph_requires_json_object(graph):
    with pytest.raises((ValueError, ValidationError)):
        ProjectSpec(project_id="project", name="Draft", graph=graph)


def test_dataset_references_and_internal_ids_are_strict():
    ref = DatasetRef(dataset_id="training", version="1.0", sha256="a" * 64)
    binding = DatasetBinding(binding_id="train", role="train", dataset=ref)
    assert binding.dataset == ref
    for invalid in ["../outside", "..", "", "한글"]:
        with pytest.raises(ValidationError):
            ProjectSpec(project_id=invalid, name="Display")
        with pytest.raises(ValidationError):
            DatasetRef(dataset_id="dataset", version=invalid, sha256="a" * 64)
    with pytest.raises(ValidationError):
        DatasetBinding(binding_id="train", role="other", dataset=ref)
    with pytest.raises(ValidationError):
        DatasetRef(dataset_id="dataset", version="1", sha256="bad")
    with pytest.raises(ValidationError, match="duplicate"):
        ProjectSpec(project_id="project", name="Duplicate", datasets=(binding, binding))


def test_save_revalidates_model_copy_and_get_rejects_bad_hash(tmp_path):
    store = ProjectStore(tmp_path)
    created = store.create("request", "Draft", "", "")
    with pytest.raises(ValidationError):
        store.save(created.spec.model_copy(update={"project_id": "../escape"}), None)
    with pytest.raises(ValueError):
        store.get(created.spec.project_id, "../escape")
