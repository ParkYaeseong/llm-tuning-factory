"""Task 9.1: the strict desktop workflow channel for projects, recipes and mapped data."""
import importlib
import io
import json
import os
import re
import socket
import sqlite3
import subprocess
import sys
import textwrap
import typing
from pathlib import Path

import pytest
import yaml
from pydantic import BaseModel

from tuning_factory import desktop
from tuning_factory.config import AIConfig, StrictModel
from tuning_factory.registry import Registry, canonical, digest

ROOT = Path(__file__).resolve().parents[1]
# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
SECRET = "synthetic-" + "runpod-" + "k" * 12
HF_SECRET = "synthetic-" + "hf-" + "t" * 12

ACTIONS_91 = ("projects_list", "project_create", "project_get", "project_save", "project_import_legacy",
              "recipes_list", "recipe_get", "recipe_save", "recipe_clone",
              "dataset_import_source", "dataset_import_preview", "dataset_import_register",
              "dataset_split_preview", "dataset_split_commit")
ACTIONS_92 = ("executor_profile_inspect", "model_root_snapshot", "model_roots_list", "benchmark_save",
              "benchmarks_list", "graph_validate", "graph_plan", "graph_plan_get")
ACTIONS_93 = ("graph_submit", "graph_runs_list", "graph_run_get", "graph_pause", "graph_resume", "graph_cancel",
              "tracking_rearm", "executor_profile_edit_check")
ACTIONS_94 = ("controller_status", "controller_stop")
# Copied from desktop/src-tauri/src/main.rs `allowed()` (plan F6 says 35; the list has 34 names).
LEGACY_ACTIONS = ("catalog", "save_profile", "dataset", "plan", "submit", "execute", "jobs", "logs", "cancel",
                  "recover", "reconcile", "resume", "connection", "pod", "runtime_check", "runtime_prepare",
                  "live_logs", "history", "compare", "model_revision", "dataset_preview", "job_progress",
                  "capabilities", "s3_list", "s3_head", "s3_import", "model_search", "model_inspect",
                  "model_import", "server_inspect", "server_facts", "runtime_reservations", "runtime_release",
                  "tutorial_sample")
MAPPING = {"source_format": "csv", "fields": {"instruction": ["question"], "input": ["context"],
                                              "output": ["answer"]}}
ROWS = [(f"질문 {i}", f"문맥 {i}", f"답 {i}") for i in range(1, 9)]


def workflows():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.desktop_workflows")
    except ModuleNotFoundError as exc:
        if exc.name != "tuning_factory.desktop_workflows":
            raise
        pytest.fail("the desktop workflow channel is missing")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Projects, recipes and data are local: no provider, SSH, HF or other connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("desktop workflow tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


@pytest.fixture
def isolated_environment(monkeypatch):
    """The legacy DesktopService exports its settings to the process environment; restore it afterwards."""
    from tuning_factory.connections import ENV_SETTINGS
    for key in ENV_SETTINGS:
        monkeypatch.setenv(key, "x")  # Registers teardown even for keys that start absent.
        monkeypatch.delenv(key)


def envelope(state, action, data=None, **fields):
    return {"channel": "workflow", "root": str(state), "settings": {}, "action": action,
            "data": {} if data is None else data, **fields}


def call(state, action, data=None, **fields):
    return workflows().handle(envelope(state, action, data, **fields))


def sidecar(monkeypatch, capsys, request):
    """One request through desktop.main over stdin/stdout, exactly as the native bridge sends it."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    desktop.main()
    captured = capsys.readouterr()
    lines = captured.out.splitlines()
    assert len(lines) == 1, captured.out
    return json.loads(lines[0]), captured


def only_issue(result, status, code, path=None):
    assert result["status"] == status, result
    assert [issue["code"] for issue in result["issues"]] == [code], result
    if path is not None:
        assert result["issues"][0]["path"] == path, result
    return result["issues"][0]


def snapshot(directory):
    directory = Path(directory)
    if not directory.exists():
        return {}
    return {path.relative_to(directory).as_posix(): path.read_bytes()
            for path in sorted(directory.rglob("*")) if path.is_file()}


def csv_bytes(rows=ROWS, encoding="utf-8"):
    return ("question,context,answer\n" + "".join(f"{q},{c},{a}\n" for q, c, a in rows)).encode(encoding)


def write(tmp_path, name, raw):
    inbox = tmp_path / "inbox"
    inbox.mkdir(exist_ok=True)
    path = inbox / name
    path.write_bytes(raw)
    return path


def import_source(state, tmp_path, raw=None, name="source.csv"):
    result = call(state, "dataset_import_source", {"path": str(write(tmp_path, name, raw or csv_bytes()))})
    assert result["status"] == "ok", result
    return result["data"]["source_id"]


def preview(state, source_id, mapping=MAPPING):
    return call(state, "dataset_import_preview", {"source_id": source_id, "mapping": mapping})


def register(state, source_id, preview_sha256, dataset_id="mapped", version="1", mapping=MAPPING, **flags):
    return call(state, "dataset_import_register", {"source_id": source_id, "mapping": mapping,
                                                   "preview_sha256": preview_sha256, "dataset_id": dataset_id,
                                                   "version": version, **flags})


def imported(state, tmp_path, dataset_id="mapped", version="1", raw=None, name="source.csv"):
    source_id = import_source(state, tmp_path, raw, name)
    shown = preview(state, source_id)
    assert shown["status"] == "ok", shown
    result = register(state, source_id, shown["data"]["preview_sha256"], dataset_id, version)
    assert result["status"] == "ok", result
    return result["data"]["dataset"]


def split_plan(dataset, outputs=None, seed=42):
    return {"dataset": dataset, "seed": seed, "train_basis_points": 5000, "validation_basis_points": 2500,
            "test_basis_points": 2500,
            "outputs": outputs or {"train": {"dataset_id": "split-train", "version": "1"},
                                   "validation": {"dataset_id": "split-validation", "version": "1"},
                                   "test": {"dataset_id": "split-test", "version": "1"}}}


def legacy_config(ai_id="paper_extraction", **updates):
    data = yaml.safe_load((ROOT / "configs/ai/paper_extraction.yaml").read_text(encoding="utf-8"))
    data.update(ai_id=ai_id, **updates)
    return AIConfig.model_validate(data)


def write_legacy_profile(state, config):
    # The exact bytes the legacy `save_profile` action writes.
    directory = Path(state) / "profiles"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{config.ai_id}.json").write_bytes(canonical(config.model_dump()))


def minimal_bodies(tmp_path):
    dataset = {"dataset_id": "mapped", "version": "1", "sha256": "0" * 64}
    return {
        "projects_list": {},
        "project_create": {"request_id": "create-1", "name": "새 프로젝트"},
        "project_get": {"project_id": "p1"},
        "project_save": {"spec": {"project_id": "p1", "name": "새 프로젝트"}, "expected_revision": "0" * 64},
        "project_import_legacy": {"request_id": "legacy-1", "ai_id": "paper_extraction"},
        "recipes_list": {},
        "recipe_get": {"recipe_id": "r1"},
        "recipe_save": {"spec": {"recipe_id": "r1", "name": "레시피"}, "expected_revision": None},
        "recipe_clone": {"recipe_id": "r1"},
        "dataset_import_source": {"path": str(tmp_path / "inbox" / "source.csv")},
        "dataset_import_preview": {"source_id": "0" * 64, "mapping": MAPPING},
        "dataset_import_register": {"source_id": "0" * 64, "mapping": MAPPING, "preview_sha256": "1" * 64,
                                    "dataset_id": "mapped", "version": "1"},
        "dataset_split_preview": {"plan": split_plan(dataset)},
        "dataset_split_commit": {"plan_sha256": "2" * 64},
    }


def model_classes(annotation, depth=0):
    if depth > 20:
        return
    if isinstance(annotation, type) and issubclass(annotation, BaseModel):
        yield annotation
        return
    for argument in typing.get_args(annotation):
        yield from model_classes(argument, depth + 1)


def field_names(model, seen=None):
    seen = set() if seen is None else seen
    if model in seen:
        return set()
    seen.add(model)
    names = set()
    for name, info in model.model_fields.items():
        names.add(name)
        if isinstance(info.alias, str):
            names.add(info.alias)
        for nested in model_classes(info.annotation):
            names |= field_names(nested, seen)
    return names


def command_like(name):
    pattern = workflows().FORBIDDEN_FIELD
    parts = [name, *re.split(r"[^A-Za-z0-9]+|(?<=[a-z0-9])(?=[A-Z])", name)]
    return any(part and pattern.fullmatch(part) for part in parts)


# 1
def test_workflow_action_table_is_closed_and_partitioned():
    wf = workflows()
    expected = ACTIONS_91 + ACTIONS_92 + ACTIONS_93 + ACTIONS_94
    assert wf.WORKFLOW_ACTIONS == expected
    assert len(set(wf.WORKFLOW_ACTIONS)) == 32
    assert set(wf.ACTION_MODULES) == set(wf.WORKFLOW_ACTIONS)
    owners = {"tuning_factory.desktop_workflows": ACTIONS_91, "tuning_factory.workflow_planning": ACTIONS_92,
              "tuning_factory.workflow_runs": ACTIONS_93, "tuning_factory.controller_entry": ACTIONS_94}
    assert {name: module for module, names in owners.items() for name in names} == dict(wf.ACTION_MODULES)
    native = (ROOT / "desktop/src-tauri/src/main.rs").read_text(encoding="utf-8")
    allowed = native[native.index("fn allowed("):]
    allowed = allowed[:allowed.index("\n}\n")]
    assert tuple(re.findall(r'"([a-z0-9_]+)"', allowed)) == LEGACY_ACTIONS
    assert not set(wf.WORKFLOW_ACTIONS) & set(LEGACY_ACTIONS)
    assert dict(wf.SECRET_SCOPES) == {"executor_profile_inspect": frozenset({"RUNPOD_API_KEY"}),
                                      "model_root_snapshot": frozenset({"HF_TOKEN"})}
    assert set(wf.SECRET_SCOPES) <= set(wf.WORKFLOW_ACTIONS)
    assert set().union(*wf.SECRET_SCOPES.values()) <= set(wf.SECRET_FIELDS)
    secrets = re.search(r"const SECRETS: \[&str; 7\] = \[(.*?)\];", native, re.S)[1]
    assert wf.SECRET_FIELDS == tuple(re.findall(r'"([A-Z0-9_]+)"', secrets))
    with pytest.raises(TypeError):
        wf.ACTION_MODULES["shell"] = "tuning_factory.desktop_workflows"


# 2
@pytest.mark.parametrize("action", ACTIONS_91)
def test_every_workflow_request_refuses_extra_fields(tmp_path, action):
    state = tmp_path / "state"
    assert call(state, "project_create", {"request_id": "seed", "name": "기존"})["status"] == "ok"
    import_source(state, tmp_path)
    before = snapshot(state)
    result = call(state, action, {**minimal_bodies(tmp_path)[action], "command": "calc.exe"})
    issue = only_issue(result, "invalid", "extra_field", "/command")
    assert "calc.exe" not in json.dumps(result)
    assert issue["row_number"] is None and issue["node_id"] is None
    assert snapshot(state) == before


# 3
def test_workflow_envelope_refuses_credentials_and_unknown_actions(tmp_path, monkeypatch, capsys,
                                                                   isolated_environment):
    wf = workflows()
    state = tmp_path / "state"
    for name in wf.SECRET_FIELDS:
        response, captured = sidecar(monkeypatch, capsys, envelope(state, "projects_list",
                                                                   settings={name: SECRET}))
        assert response == {"ok": False, "error": wf.CREDENTIALS_REFUSED}
        assert SECRET not in captured.out + captured.err
    for action, secrets in (("projects_list", {"RUNPOD_API_KEY": SECRET}),
                            ("project_create", {"HF_TOKEN": HF_SECRET}),
                            ("executor_profile_inspect", {"HF_TOKEN": HF_SECRET}),
                            ("model_root_snapshot", {"RUNPOD_API_KEY": SECRET})):
        response, captured = sidecar(monkeypatch, capsys, envelope(state, action, secrets=secrets))
        assert response == {"ok": False, "error": wf.SCOPE_REFUSED}
        assert SECRET not in captured.out + captured.err and HF_SECRET not in captured.out + captured.err
    for action in ("shell", "plan", "jobs", "tutorial_sample"):
        response, _ = sidecar(monkeypatch, capsys, envelope(state, action))
        assert response == {"ok": False, "error": wf.UNSUPPORTED_ACTION}
    for action in ("projects_list", "graph_plan", "controller_status"):
        response, _ = sidecar(monkeypatch, capsys, {"root": str(state), "settings": {}, "action": action,
                                                    "data": {}})
        assert response == {"ok": False, "error": "unsupported desktop action"}
    assert SECRET not in json.dumps({key: value.decode("latin-1") for key, value in snapshot(state).items()})


# 4
def test_request_models_have_no_command_like_fields():
    wf = workflows()
    for module in sorted(set(wf.ACTION_MODULES.values())):
        try:
            importlib.import_module(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise
    requests = wf.registered_requests()
    assert set(ACTIONS_91) <= set(requests)
    assert set(requests) <= set(wf.WORKFLOW_ACTIONS)
    assert command_like("run_command") and command_like("shellCmd") and command_like("argv")
    assert not command_like("description") and not command_like("approve_execution")
    for action, model in requests.items():
        assert issubclass(model, StrictModel), action
        assert model.model_config.get("extra") == "forbid" and model.model_config.get("frozen") is True, action
        names = field_names(model)
        assert not sorted(name for name in names if command_like(name)), action
    assert {"spec", "hyperparameters", "lora_rank", "mapping", "outputs"} <= set().union(
        *(field_names(model) for model in requests.values()))


# 5
def test_project_lifecycle_conflicts_and_idempotency(tmp_path):
    state = tmp_path / "state"
    assert call(state, "projects_list") == {"status": "ok", "issues": [], "data": {"projects": []}}
    assert not state.exists()
    body = {"request_id": "create-1", "name": "단백질 서열 분류", "purpose": "예제 없이 새로 정한 목적",
            "task_type": "classification"}
    first = call(state, "project_create", body)
    assert first["status"] == "ok" and first["issues"] == []
    assert call(state, "project_create", body) == first
    project, revision = first["data"]["project"], first["data"]["revision_sha256"]
    assert project["graph"] == {} and project["datasets"] == [] and project["legacy_origin"] is None
    assert project["default_executor_profile_id"] is None
    only_issue(call(state, "project_create", {**body, "name": "다른 이름"}), "conflict", "request_reused",
               "/request_id")

    saved = call(state, "project_save", {"spec": {**project, "purpose": "바뀐 목적"}, "expected_revision": revision})
    assert saved["status"] == "ok"
    current = saved["data"]["revision_sha256"]
    assert current != revision and saved["data"]["project"]["purpose"] == "바뀐 목적"
    stale = call(state, "project_save", {"spec": {**project, "purpose": "늦은 수정"}, "expected_revision": revision})
    issue = only_issue(stale, "conflict", "revision_conflict", "/expected_revision")
    assert issue["message"] == "definition revision conflict"
    got = call(state, "project_get", {"project_id": project["project_id"]})
    assert got["data"]["revision_sha256"] == current and got["data"]["project"]["purpose"] == "바뀐 목적"
    old = call(state, "project_get", {"project_id": project["project_id"], "revision": revision})
    assert old["data"] == {"project": project, "revision_sha256": revision}
    only_issue(call(state, "project_get", {"project_id": "missing"}), "invalid", "unknown_definition",
               "/project_id")
    only_issue(call(state, "project_get", {"project_id": project["project_id"], "revision": "0" * 64}),
               "invalid", "unknown_definition", "/revision")
    only_issue(call(state, "project_save", {"spec": {**project, "project_id": "missing"},
                                            "expected_revision": revision}),
               "invalid", "unknown_definition", "/spec/project_id")
    only_issue(call(state, "project_save", {"spec": project}), "invalid", "invalid_value", "/expected_revision")

    # A blank draft (no data, no model, graph={}) saves, and so does a partial graph draft.
    blank = call(state, "project_save", {"spec": {**project, "graph": {}}, "expected_revision": current})
    assert blank["status"] == "ok"
    draft = call(state, "project_save", {"spec": {**project, "graph": {"nodes": [{"node_id": "train"}]}},
                                         "expected_revision": blank["data"]["revision_sha256"]})
    assert draft["status"] == "ok"
    listed = call(state, "projects_list")["data"]["projects"]
    assert listed == [{"project_id": project["project_id"], "name": "단백질 서열 분류", "purpose": "예제 없이 새로 정한 목적",
                       "task_type": "classification", "revision_sha256": draft["data"]["revision_sha256"],
                       "legacy_origin": None, "datasets": [], "graph_nodes": 1}]


# 6
def test_recipe_save_clone_and_strict_spec(tmp_path):
    state = tmp_path / "state"
    spec = {"recipe_id": "sft-lora", "name": "기본 SFT LoRA"}
    created = call(state, "recipe_save", {"spec": spec, "expected_revision": None})
    assert created["status"] == "ok"
    original = created["data"]
    assert original["recipe"]["training_stage"] == "sft" and original["recipe"]["input_kind"] == "instruction"
    only_issue(call(state, "recipe_save", {"spec": spec, "expected_revision": None}), "conflict",
               "revision_conflict", "/expected_revision")
    only_issue(call(state, "recipe_save", {"spec": spec}), "invalid", "invalid_value", "/expected_revision")

    clone = call(state, "recipe_clone", {"recipe_id": "sft-lora", "new_recipe_id": "sft-lora-copy", "name": "복사본"})
    assert clone["status"] == "ok" and clone["data"]["recipe"]["recipe_id"] == "sft-lora-copy"
    edited_spec = {**clone["data"]["recipe"], "description": "랭크를 올린 사본",
                   "hyperparameters": {**clone["data"]["recipe"]["hyperparameters"], "lora_rank": 16}}
    edited = call(state, "recipe_save", {"spec": edited_spec,
                                         "expected_revision": clone["data"]["revision_sha256"]})
    assert edited["status"] == "ok"
    assert call(state, "recipe_get", {"recipe_id": "sft-lora"})["data"] == original
    assert call(state, "recipe_get", {"recipe_id": "sft-lora", "revision": original["revision_sha256"]})["data"] \
        == original
    only_issue(call(state, "recipe_clone", {"recipe_id": "sft-lora", "new_recipe_id": "sft-lora-copy"}),
               "conflict", "revision_conflict", "/new_recipe_id")
    only_issue(call(state, "recipe_get", {"recipe_id": "missing"}), "invalid", "unknown_definition", "/recipe_id")

    for name in ("reward_model", "dataset_id", "pod_id", "RUNPOD_API_KEY", "command"):
        result = call(state, "recipe_save", {"spec": {**spec, "recipe_id": "strict", name: SECRET},
                                             "expected_revision": None})
        only_issue(result, "invalid", "extra_field", f"/spec/{name}")
        assert SECRET not in json.dumps(result)
    mismatch = call(state, "recipe_save", {"spec": {"recipe_id": "dpo", "name": "DPO", "training_stage": "dpo"},
                                           "expected_revision": None})
    only_issue(mismatch, "invalid", "invalid_value", "/spec")
    listed = call(state, "recipes_list")["data"]["recipes"]
    assert [recipe["recipe_id"] for recipe in listed] == ["sft-lora", "sft-lora-copy"]
    assert set(listed[0]) == {"recipe_id", "version", "name", "description", "training_stage", "training_method",
                              "input_kind", "revision_sha256"}
    assert listed[1]["revision_sha256"] == edited["data"]["revision_sha256"]
    assert SECRET.encode() not in b"".join(snapshot(state).values())


# 7
def test_project_import_legacy_is_idempotent_and_preserves_legacy_bytes(tmp_path, isolated_environment):
    state = tmp_path / "state"
    app = desktop.DesktopService(state)
    source = write(tmp_path, "legacy.json", json.dumps([{"instruction": "질문", "output": "답"}]).encode())
    record = app.dispatch("dataset", {"dataset_id": "dummy", "version": "1", "path": str(source)})
    config = legacy_config()
    assert app.dispatch("save_profile", {"config": config.model_dump()}) == {"saved": True}
    legacy = {key: value for key, value in snapshot(state).items() if key.startswith(("profiles/", "registry/"))}
    assert "profiles/paper_extraction.json" in legacy

    first = call(state, "project_import_legacy", {"request_id": "legacy-1", "ai_id": "paper_extraction"})
    assert first["status"] == "ok", first
    data = first["data"]
    assert data["project"]["legacy_origin"] == "paper_extraction"
    assert data["project"]["name"] == "paper_extraction" and data["project"]["graph"] == {}
    assert data["recipe"]["recipe_id"] == "legacy-" + digest(b"legacy-1")[:24]
    assert data["recipe"]["name"] == "paper_extraction" and data["recipe"]["training_method"] == "lora"
    binding = {"binding_id": "train", "role": "train",
               "dataset": {"dataset_id": "dummy", "version": "1", "sha256": record["sha256"]}}
    assert data["datasets"] == [binding] and data["project"]["datasets"] == [binding]
    assert call(state, "project_import_legacy", {"request_id": "legacy-1", "ai_id": "paper_extraction"}) == first
    got = call(state, "project_get", {"project_id": data["project"]["project_id"]})
    assert got["data"]["revision_sha256"] == data["revision_sha256"]
    assert call(state, "recipe_get", {"recipe_id": data["recipe"]["recipe_id"]})["data"]["revision_sha256"] \
        == data["recipe_revision_sha256"]

    other = legacy_config("labnote_draft")
    write_legacy_profile(state, other)
    only_issue(call(state, "project_import_legacy", {"request_id": "legacy-1", "ai_id": "labnote_draft"}),
               "conflict", "request_reused", "/request_id")
    orphan = legacy_config("orphan_profile", dataset_id="never_registered")
    write_legacy_profile(state, orphan)
    unbound = call(state, "project_import_legacy", {"request_id": "legacy-2", "ai_id": "orphan_profile"})
    assert unbound["status"] == "ok" and unbound["data"]["datasets"] == []
    assert [warning["code"] for warning in unbound["data"]["warnings"]] == ["legacy_dataset_missing"]
    only_issue(call(state, "project_import_legacy", {"request_id": "legacy-3", "ai_id": "missing_profile"}),
               "invalid", "legacy_profile_missing", "/ai_id")
    after = snapshot(state)
    assert {key: after[key] for key in legacy} == legacy
    assert not any(key.startswith("registry/") and key not in legacy for key in after)


# 8
def test_mapped_preview_reports_row_numbers(tmp_path):
    state = tmp_path / "state"
    clean = preview(state, import_source(state, tmp_path))
    assert clean["status"] == "ok" and clean["issues"] == []
    assert clean["data"]["samples"][0] == {"instruction": "질문 1", "input": "문맥 1", "output": "답 1"}
    assert clean["data"]["row_count"] == 8 and re.fullmatch("[0-9a-f]{64}", clean["data"]["preview_sha256"])

    lines = [{"question": "q1", "context": "c", "answer": "a"}, {"question": "q2", "context": "c", "answer": "a"},
             {"question": "q3", "context": "c"}, {"question": "q4", "context": "c", "answer": "a"}]
    raw = "\n\n".join(json.dumps(line) for line in lines).encode()
    jsonl = preview(state, import_source(state, tmp_path, raw, "rows.jsonl"),
                    {**MAPPING, "source_format": "jsonl"})
    issue = only_issue(jsonl, "invalid", "invalid_row", "/mapping")
    assert issue["row_number"] == 3
    assert jsonl["data"]["preview_sha256"] and jsonl["data"]["row_count"] == 4

    duplicate = preview(state, import_source(state, tmp_path, b"question,question,context,answer\nq,q,c,a\n",
                                             "duplicate.csv"))
    issue = only_issue(duplicate, "invalid", "duplicate_header", "/mapping")
    assert issue["row_number"] is None

    cp949 = import_source(state, tmp_path, csv_bytes(encoding="cp949"), "cp949.csv")
    assert preview(state, cp949)["status"] == "invalid"
    korean = preview(state, cp949, {**MAPPING, "encoding": "cp949"})
    assert korean["status"] == "ok" and korean["data"]["samples"][0]["instruction"] == "질문 1"
    only_issue(preview(state, "0" * 64), "invalid", "source_unknown", "/source_id")


# 9
def test_import_register_conflicts_and_reserved_ids(tmp_path):
    state = tmp_path / "state"
    source_id = import_source(state, tmp_path)
    shown = preview(state, source_id)["data"]
    only_issue(register(state, source_id, "f" * 64), "conflict", "preview_changed", "/preview_sha256")
    bad_source = import_source(state, tmp_path, b"question,context,answer\nq,c,\n", "bad.csv")
    bad = preview(state, bad_source)
    assert bad["status"] == "invalid"
    only_issue(register(state, bad_source, bad["data"]["preview_sha256"], "bad"), "invalid", "import_issues")
    for reserved in ("tutorial_sample", "TUTORIAL_SAMPLE.", "Tutorial_Sample.."):
        issue = only_issue(register(state, source_id, shown["preview_sha256"], reserved, synthetic=True),
                           "invalid", "practice_reserved", "/dataset_id")
        assert issue["message"] == desktop.TUTORIAL_REFUSAL
    assert not (state / "registry" / "datasets").exists()

    first = register(state, source_id, shown["preview_sha256"])
    assert first["status"] == "ok"
    record = Registry(state / "registry").get_dataset("mapped", "1")
    assert first["data"] == {"dataset": {"dataset_id": "mapped", "version": "1", "sha256": record["sha256"]},
                             "preview_sha256": shown["preview_sha256"]}
    assert record["export_allowed"] is False and record["synthetic"] is False
    assert register(state, source_id, shown["preview_sha256"]) == first
    other_id = import_source(state, tmp_path, csv_bytes(ROWS[:4]), "other.csv")
    other = preview(state, other_id)["data"]
    only_issue(register(state, other_id, other["preview_sha256"]), "conflict", "version_exists", "/version")
    assert Registry(state / "registry").get_dataset("mapped", "1") == record
    strict = register(state, source_id, shown["preview_sha256"], "flags", export_allowed="yes")
    only_issue(strict, "invalid", "invalid_value", "/export_allowed")


# 10
def test_split_preview_commit_conflicts_and_reserved_outputs(tmp_path):
    state = tmp_path / "state"
    dataset = imported(state, tmp_path)
    shown = call(state, "dataset_split_preview", {"plan": split_plan(dataset)})
    assert shown["status"] == "ok", shown
    assert set(shown["data"]) == {"plan_sha256", "counts", "outputs"}
    assert sum(shown["data"]["counts"].values()) == 8
    committed = call(state, "dataset_split_commit", {"plan_sha256": shown["data"]["plan_sha256"]})
    assert committed["status"] == "ok"
    assert committed["data"] == {"plan_sha256": shown["data"]["plan_sha256"], "outputs": shown["data"]["outputs"]}
    registry = Registry(state / "registry")
    for role, output in committed["data"]["outputs"].items():
        assert registry.get_dataset(output["dataset_id"], "1")["sha256"] == output["sha256"], role
    assert call(state, "dataset_split_commit", {"plan_sha256": shown["data"]["plan_sha256"]}) == committed

    other = call(state, "dataset_split_preview", {"plan": split_plan(dataset, seed=7, outputs={
        "train": {"dataset_id": "seven-train", "version": "1"},
        "validation": {"dataset_id": "seven-validation", "version": "1"},
        "test": {"dataset_id": "seven-test", "version": "1"}})})
    path = state / "registry" / "split_previews" / f"{other['data']['plan_sha256']}.json"
    manifest = json.loads(path.read_bytes())
    manifest["counts"]["train"] += 1
    path.write_bytes(canonical(manifest))
    only_issue(call(state, "dataset_split_commit", {"plan_sha256": other["data"]["plan_sha256"]}),
               "conflict", "preview_changed", "/plan_sha256")
    assert not (state / "registry" / "datasets" / "seven-train").exists()
    only_issue(call(state, "dataset_split_commit", {"plan_sha256": "3" * 64}), "invalid", "preview_unknown",
               "/plan_sha256")

    before = snapshot(state)
    for reserved in ("tutorial_sample", "Tutorial_Sample.."):
        outputs = {"train": {"dataset_id": "safe-train", "version": "1"},
                   "validation": {"dataset_id": reserved, "version": "1"},
                   "test": {"dataset_id": "safe-test", "version": "1"}}
        issue = only_issue(call(state, "dataset_split_preview", {"plan": split_plan(dataset, outputs)}),
                           "invalid", "practice_reserved", "/plan/outputs/validation/dataset_id")
        assert issue["message"] == desktop.TUTORIAL_REFUSAL
    assert snapshot(state) == before
    only_issue(call(state, "dataset_split_preview", {"plan": split_plan({**dataset, "dataset_id": "absent"})}),
               "invalid", "dataset_unknown", "/plan/dataset")


# 11
def test_import_source_reads_local_files_only(tmp_path):
    wf = workflows()
    state = tmp_path / "state"
    devices = [str(tmp_path / name) for name in ("CON", "nul.csv", "Com1.txt", "LPT9", "conout$", "AUX .csv")]
    for path in (r"\\server\share\a.csv", "//server/a.csv", r"\\?\C:\a.csv", r"\\.\pipe\x", "\\/server/a.csv",
                 "relative/a.csv", "a.csv", "", *devices):
        result = call(state, "dataset_import_source", {"path": path})
        if path == "":
            only_issue(result, "invalid", "invalid_value", "/path")
            continue
        issue = only_issue(result, "invalid", "local_file_only", "/path")
        assert issue["message"] == wf.LOCAL_FILE_ONLY
        with pytest.raises(ValueError, match=re.escape(wf.LOCAL_FILE_ONLY)):
            wf.local_file(path)
    assert not state.exists()
    large = tmp_path / "inbox" / "large.csv"
    large.parent.mkdir()
    with large.open("wb") as stream:
        stream.truncate(32 * 1024 * 1024 + 1)
    only_issue(call(state, "dataset_import_source", {"path": str(large)}), "invalid", "size_limit", "/path")
    only_issue(call(state, "dataset_import_source", {"path": str(tmp_path / "inbox" / "absent.csv")}),
               "invalid", "file_unreadable", "/path")
    raw = csv_bytes()
    local = write(tmp_path, "local.csv", raw)
    assert wf.local_file(str(local)) == local
    for name in ("console.csv", "com10.csv", "null.json", "my con.csv"):
        assert wf.local_file(str(tmp_path / name)) == tmp_path / name
    result = call(state, "dataset_import_source", {"path": str(local)})
    assert result == {"status": "ok", "issues": [],
                      "data": {"source_id": digest(raw), "source_sha256": digest(raw), "size": len(raw)}}
    assert (state / "registry" / "imports" / "sources" / f"{digest(raw)}.bin").read_bytes() == raw


def full_flow(state, tmp_path):
    """Every 9.1 action, each succeeding, through `handle` only."""
    results = {"projects_list": call(state, "projects_list")}
    created = call(state, "project_create", {"request_id": "flow-1", "name": "흐름 프로젝트"})
    results["project_create"] = created
    project = created["data"]["project"]
    results["project_get"] = call(state, "project_get", {"project_id": project["project_id"]})
    results["recipes_list"] = call(state, "recipes_list")
    results["recipe_save"] = call(state, "recipe_save", {"spec": {"recipe_id": "flow", "name": "흐름"},
                                                         "expected_revision": None})
    results["recipe_get"] = call(state, "recipe_get", {"recipe_id": "flow"})
    results["recipe_clone"] = call(state, "recipe_clone", {"recipe_id": "flow", "new_recipe_id": "flow-copy"})
    path = write(tmp_path, "flow.csv", csv_bytes())
    results["dataset_import_source"] = source = call(state, "dataset_import_source", {"path": str(path)})
    results["dataset_import_preview"] = shown = preview(state, source["data"]["source_id"])
    results["dataset_import_register"] = registered = register(state, source["data"]["source_id"],
                                                               shown["data"]["preview_sha256"], "flow-data")
    dataset = registered["data"]["dataset"]
    results["dataset_split_preview"] = split = call(state, "dataset_split_preview", {"plan": split_plan(dataset)})
    results["dataset_split_commit"] = call(state, "dataset_split_commit",
                                           {"plan_sha256": split["data"]["plan_sha256"]})
    results["project_save"] = call(state, "project_save", {
        "spec": {**project, "datasets": [{"binding_id": "train", "role": "train", "dataset": dataset}]},
        "expected_revision": created["data"]["revision_sha256"]})
    config = legacy_config(dataset_id="flow-data")
    write_legacy_profile(state, config)
    results["project_import_legacy"] = call(state, "project_import_legacy",
                                            {"request_id": "flow-legacy", "ai_id": config.ai_id})
    assert {action: result["status"] for action, result in results.items()} == dict.fromkeys(ACTIONS_91, "ok")
    return results


# 12
def test_project_recipe_and_data_actions_mutate_no_provider_or_job(tmp_path, monkeypatch):
    import httpx
    import paramiko

    from tuning_factory import desktop_runtime, execution, jobs
    calls = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be reached from projects, recipes or data")
        return record

    monkeypatch.setattr(execution.RunPodController, "__init__", spy("RunPodController"))
    monkeypatch.setattr(desktop_runtime, "connection", spy("desktop_runtime.connection"))
    monkeypatch.setattr(paramiko.SSHClient, "connect", spy("paramiko.connect"))
    monkeypatch.setattr(httpx.Client, "send", spy("httpx.send"))
    monkeypatch.setattr(jobs.JobStore, "submit", spy("JobStore.submit"))
    monkeypatch.setattr(jobs.JobStore, "claim", spy("JobStore.claim"))
    monkeypatch.setattr(desktop.DesktopService, "__init__", spy("DesktopService"))
    environment = dict(os.environ)
    state = tmp_path / "state"
    full_flow(state, tmp_path)
    assert calls == []
    assert dict(os.environ) == environment
    database = state / "jobs.db"
    if database.exists():
        with sqlite3.connect(database) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in {"jobs", "graph_runs"} & tables:
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# 13
def test_legacy_requests_keep_their_envelope(tmp_path, monkeypatch, capsys, isolated_environment):
    state = tmp_path / "state"
    response, _ = sidecar(monkeypatch, capsys, {"root": str(state), "settings": {}, "action": "jobs", "data": {}})
    assert response == {"ok": True, "result": []}
    sample = (ROOT / "examples" / "tutorial_sample.json").read_bytes()
    response, captured = sidecar(monkeypatch, capsys, {"root": str(state), "settings": {},
                                                       "action": "tutorial_sample", "data": {}})
    copy = state.resolve() / "tutorial" / "tutorial_sample.json"
    assert response == {"ok": True, "result": {"dataset_id": "tutorial_sample", "version": "1",
                                               "kind": "instruction", "path": str(copy),
                                               "sha256": digest(sample), "rows": 8}}
    assert captured.out == json.dumps(response, ensure_ascii=True) + "\n"
    response, _ = sidecar(monkeypatch, capsys, {"root": str(state), "settings": {}, "action": "dataset",
                                                "data": {"dataset_id": "tutorial_sample", "version": "1",
                                                         "path": str(copy), "synthetic": True}})
    assert response["ok"] is True and response["result"]["dataset_id"] == "tutorial_sample"


def test_workflow_responses_use_the_issue_envelope(tmp_path, monkeypatch, capsys):
    state = tmp_path / "state"
    response, captured = sidecar(monkeypatch, capsys, envelope(state, "project_create",
                                                               {"request_id": "r", "name": "이름"}))
    assert response["ok"] is True and set(response) == {"ok", "result"}
    assert set(response["result"]) == {"status", "issues", "data"} and response["result"]["status"] == "ok"
    assert captured.out == json.dumps(response, ensure_ascii=True) + "\n"
    response, _ = sidecar(monkeypatch, capsys, envelope(state, "project_get", {"project_id": "absent"}))
    assert response == {"ok": True, "result": {"status": "invalid", "data": None, "issues": [
        {"code": "unknown_definition", "path": "/project_id", "message": "unknown definition", "node_id": None,
         "row_number": None}]}}
    for broken in ({"channel": "workflow", "action": "projects_list"},
                   {**envelope(state, "projects_list"), "root": "relative/state"},
                   {**envelope(state, "projects_list"), "surprise": SECRET},
                   {**envelope(state, "projects_list"), "secrets": [SECRET]},
                   {**envelope(state, "projects_list"), "profiles": [{"profile_id": "p", "fields": {
                       "provider": "existing_runpod", "server_ssh_host": "gpu.invalid"}}]}):
        response, captured = sidecar(monkeypatch, capsys, broken)
        assert response["ok"] is False and response["error"].startswith(workflows().INVALID_ENVELOPE)
        assert SECRET not in captured.out + captured.err


def test_unimplemented_actions_are_refused_as_not_available(tmp_path, monkeypatch):
    wf = workflows()
    state = tmp_path / "state"
    real_import = importlib.import_module
    for module in sorted(set(wf.ACTION_MODULES.values())):
        # Register every installed later sub-task now, into the real table, not into the patched copy below.
        try:
            real_import(module)
        except ModuleNotFoundError as exc:
            if exc.name != module:
                raise

    def missing_planning(name, *args):
        if name == "tuning_factory.workflow_planning":
            raise ModuleNotFoundError(f"No module named {name!r}", name=name)
        return real_import(name, *args)

    monkeypatch.setattr(wf, "_HANDLERS", {k: v for k, v in wf._HANDLERS.items() if k not in ACTIONS_92})
    monkeypatch.setattr(wf.importlib, "import_module", missing_planning)
    for action in ACTIONS_92:
        secrets = {name: HF_SECRET for name in wf.SECRET_SCOPES.get(action, ())}
        result = call(state, action, {"anything": 1}, secrets=secrets)
        issue = only_issue(result, "invalid", "not_available", "")
        assert issue["message"] == wf.NOT_AVAILABLE
        assert HF_SECRET not in json.dumps(result)
    for action in ACTIONS_92 + ACTIONS_93 + ACTIONS_94:
        result = call(state, action)
        assert result["status"] in {"ok", "invalid", "conflict"}, action
        if action not in wf._HANDLERS:
            only_issue(result, "invalid", "not_available")

    def broken_dependency(name, *args):
        if name == "tuning_factory.workflow_planning":
            raise ModuleNotFoundError("No module named 'torch'", name="torch")
        return real_import(name, *args)

    monkeypatch.setattr(wf.importlib, "import_module", broken_dependency)
    with pytest.raises(ModuleNotFoundError):
        call(state, "graph_plan")


def test_synthetic_secrets_never_reach_responses_files_or_environment(tmp_path, monkeypatch, capsys):
    wf = workflows()
    state = tmp_path / "state"
    environment = dict(os.environ)
    constructed = []
    monkeypatch.setattr(desktop.DesktopService, "__init__", lambda *args, **kwargs: constructed.append(1))
    outputs = []

    def send(request):
        response, captured = sidecar(monkeypatch, capsys, request)
        outputs.append(captured.out + captured.err)
        return response

    assert send(envelope(state, "projects_list", settings={"RUNPOD_API_KEY_saved": SECRET}))["error"] \
        == wf.CREDENTIALS_REFUSED
    assert send(envelope(state, "projects_list", settings={"aws_secret_access_key": SECRET}))["error"] \
        == wf.CREDENTIALS_REFUSED
    pasted = "-----BEGIN " + "OPENSSH " + "PRIVATE KEY-----\n" + SECRET
    assert send(envelope(state, "projects_list", settings={"ssh_key_file": pasted}))["error"] \
        == wf.CREDENTIALS_REFUSED
    profile = {"profile_id": "pod-a", "fields": {"provider": "existing_runpod", "RUNPOD_API_KEY": SECRET}}
    assert send(envelope(state, "projects_list", profiles=[profile]))["error"] == wf.CREDENTIALS_REFUSED
    created = send(envelope(state, "project_create", {"request_id": "s", "name": "비밀 없음"},
                            settings={"MLFLOW_TRACKING_URI": "https://mlflow.invalid",
                                      "MLFLOW_TRACKING_TOKEN_saved": True, "RUNPOD_API_KEY_saved": True},
                            profiles=[{"profile_id": "pod-a", "RUNPOD_API_KEY_saved": True,
                                       "fields": {"provider": "existing_runpod", "name": "A", "pod_id": "pod1"}}]))
    project = created["result"]["data"]["project"]
    graph = send(envelope(state, "project_save", {"spec": {**project, "graph": {"nodes": [
        {"node_id": "a", "settings": {"HF_TOKEN": SECRET}}]}}, "expected_revision": created["result"]["data"][
        "revision_sha256"]}))
    issue = only_issue(graph["result"], "invalid", "credential_field", "/spec/graph/nodes/0/settings/HF_TOKEN")
    assert issue["message"] == wf.CREDENTIALS_REFUSED

    class Probe(StrictModel):
        profile_id: str

    def leaky(context, request):
        raise ValueError("provider said " + context.secrets["RUNPOD_API_KEY"])

    monkeypatch.setitem(wf._HANDLERS, "executor_profile_inspect", wf._Registered(Probe, leaky))
    leaked = send(envelope(state, "executor_profile_inspect", {"profile_id": "pod-a"},
                           secrets={"RUNPOD_API_KEY": SECRET}))
    only_issue(leaked["result"], "invalid", "refused")
    assert "[redacted]" in leaked["result"]["issues"][0]["message"]

    def crash(context, request):
        raise TimeoutError("timed out talking with " + context.secrets["RUNPOD_API_KEY"])

    monkeypatch.setitem(wf._HANDLERS, "executor_profile_inspect", wf._Registered(Probe, crash))
    crashed = send(envelope(state, "executor_profile_inspect", {"profile_id": "pod-a"},
                            secrets={"RUNPOD_API_KEY": SECRET}))
    assert crashed["ok"] is False and "[redacted]" in crashed["error"]
    assert not any(SECRET in output for output in outputs)
    assert SECRET.encode() not in b"".join(snapshot(state).values())
    assert constructed == [] and dict(os.environ) == environment
    context = wf.WorkflowContext(state_dir=state, settings={}, profiles={}, secrets={"RUNPOD_API_KEY": SECRET})
    assert SECRET not in repr(context) and SECRET not in str(context)


def test_registration_is_closed_to_unknown_names_foreign_modules_and_command_fields():
    wf = workflows()
    before = dict(wf._HANDLERS)

    def handler(context, request):
        return wf.ok()

    with pytest.raises(ValueError):
        wf.workflow_action("shell", wf.Empty)
    for name in ("projects_list", "graph_plan", "controller_status"):
        with pytest.raises(ValueError):
            wf.workflow_action(name, wf.Empty)(handler)

    class Sneaky(StrictModel):
        run_command: str = ""

    class Nested(StrictModel):
        inner: tuple[Sneaky, ...] = ()

    class Lenient(BaseModel):
        value: str = ""

    for model in (Sneaky, Nested, Lenient):
        with pytest.raises(TypeError):
            wf.workflow_action("graph_plan", model)
    assert wf._HANDLERS == before


def test_validation_issues_use_escaped_json_pointers_without_union_tags(tmp_path):
    state = tmp_path / "state"
    only_issue(call(state, "projects_list", {"a/b~c": 1}), "invalid", "extra_field", "/a~1b~0c")
    result = call(state, "dataset_import_preview", {"source_id": "0" * 64, "mapping": {
        "source_format": "csv", "fields": {"instruction": [-1]}}})
    assert result["status"] == "invalid"
    # pydantic reports ('mapping', 'fields', 'instruction', 0, 'constrained-str') etc.: the member tags go.
    assert [issue["path"] for issue in result["issues"]] == ["/mapping/fields/instruction/0",
                                                             "/mapping/fields/instruction/0",
                                                             "/mapping/fields/instruction"]
    assert all(issue["code"] == "invalid_value" for issue in result["issues"])
    missing = call(state, "project_create", {"request_id": "r"})
    only_issue(missing, "invalid", "invalid_value", "/name")


def test_native_profiles_are_allowlisted_per_provider(tmp_path):
    wf = workflows()
    runpod = {"provider": "existing_runpod", "name": "A", "pod_id": "pod1", "ssh_fingerprint": "SHA256:x",
              "ssh_key_file": r"C:\keys\id_ed25519"}
    server = {"provider": "ssh_server", "name": "B", "server_ssh_host": "gpu.invalid", "server_ssh_port": "22",
              "server_selected_gpu_uuids": "[]"}
    profiles = wf.WorkflowEnvelope.model_validate(envelope(tmp_path, "projects_list", profiles=[
        {"profile_id": "pod-a", "fields": runpod, "RUNPOD_API_KEY_saved": True},
        {"profile_id": "server-b", "fields": server}])).profiles
    assert [profile.profile_id for profile in profiles] == ["pod-a", "server-b"]
    for fields in ({**runpod, "server_ssh_host": "x"}, {**server, "pod_id": "p"}, {**runpod, "provider": "aws"},
                   {**runpod, "RUNPOD_API_KEY": SECRET}, {**runpod, "name": "n" * 2049}, {"name": "no provider"}):
        with pytest.raises(ValueError):
            wf.NativeProfile.model_validate({"profile_id": "p", "fields": fields})
    with pytest.raises(ValueError):
        wf.WorkflowEnvelope.model_validate(envelope(tmp_path, "projects_list", profiles=[
            {"profile_id": "same", "fields": runpod}, {"profile_id": "same", "fields": server}]))
    assert wf.RUNPOD_PROFILE_FIELDS == ("provider", "name", "pod_id", "ssh_fingerprint", "ssh_key_file")
    assert wf.SERVER_PROFILE_FIELDS == ("provider", "name", "server_ssh_host", "server_ssh_port",
                                        "server_ssh_username", "server_ssh_fingerprint", "server_remote_root",
                                        "server_ssh_key_file", "server_total_hourly_cost_usd",
                                        "server_selected_gpu_uuids")


def test_workflow_channel_imports_no_heavy_or_keyring_modules(tmp_path):
    code = textwrap.dedent("""
        import io, json, sys
        request = {"channel": "workflow", "root": sys.argv[1], "settings": {}, "action": "projects_list",
                   "data": {}}
        sys.stdin = io.StringIO(json.dumps(request) + "\\n")
        from tuning_factory import desktop
        desktop.main()
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring")
        print(json.dumps(sorted(name for name in heavy if name in sys.modules)))
    """)
    process = subprocess.run([sys.executable, "-c", code, str(tmp_path / "state")], capture_output=True,
                             text=True, timeout=120, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    response, heavy = process.stdout.splitlines()
    assert json.loads(response) == {"ok": True, "result": {"status": "ok", "issues": [], "data": {"projects": []}}}
    assert json.loads(heavy) == []
