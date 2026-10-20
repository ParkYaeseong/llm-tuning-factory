"""Task 9.5: the contract between the native broker (Rust) and the Python sidecar.

The Rust sources are read as text, so this runs on every CI platform without cargo. The schema values fixed by the
Task 9 plan (§4 "9.4" and "9.5") are pinned here too, so neither side can drift alone while the 9.4 controller entry
is not written yet. Once ``tuning_factory.controller_entry`` exists, the parity test checks it directly.
"""
import importlib
import io
import json
import re
import string
import sys
from pathlib import Path

import pytest

from tuning_factory import desktop
from tuning_factory import desktop_workflows as wf
from tuning_factory.graph_store import OWNER_ID

ROOT = Path(__file__).resolve().parents[1]
NATIVE = ROOT / "desktop" / "src-tauri" / "src"

# Fixed by the plan (§2 D2, §4 "9.4" ControllerStart/TrackingScope, §4 "9.5" broker.rs).
START_SCHEMA = "factory-controller/1"
BROKER_SCHEMA = "factory-broker/1"
CONTROLLER_ARG = "--controller"
TRACKING_FIELDS = ("MLFLOW_TRACKING_URI", "FACTORY_MLFLOW_ARTIFACT_LOCATION", "FACTORY_OPERATOR", "auth_mode",
                   "MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
                   "MLFLOW_TRACKING_TOKEN")
TRACKING_SECRETS = ("MLFLOW_OIDC_CLIENT_SECRET", "MLFLOW_TRACKING_TOKEN")
START_KEYS = {"schema", "state_root", "owner_id", "tracking", "profiles"}
PROFILE_START_KEYS = {"profile_id", "fields", "secrets"}


def source(name: str) -> str:
    path = NATIVE / name
    if not path.is_file():
        pytest.fail(f"desktop/src-tauri/src/{name} is missing")
    return path.read_text(encoding="utf-8")


def str_array(text: str, name: str) -> tuple[str, ...]:
    match = re.search(rf"\bconst {name}: \[&str; (\d+)\] = \[(.*?)\];", text, re.S)
    if match is None:
        pytest.fail(f"the Rust constant {name} is missing")
    values = tuple(re.findall(r'"([^"\\]*)"', match[2]))
    assert len(values) == int(match[1]), f"{name} declares {match[1]} names but lists {len(values)}"
    return values


def str_const(text: str, name: str) -> str:
    match = re.search(rf'\bconst {name}: &str = "([^"\\]*)";', text)
    if match is None:
        pytest.fail(f"the Rust constant {name} is missing")
    return match[1]


def usize_const(text: str, name: str) -> int:
    match = re.search(rf"\bconst {name}: usize = ([0-9_]+);", text)
    if match is None:
        pytest.fail(f"the Rust constant {name} is missing")
    return int(match[1].replace("_", ""))


def controller_sample() -> dict:
    """The ``factory-controller/1`` line a Rust test pins byte-for-byte against ``controller_message``."""
    match = re.search(r'const CONTROLLER_MESSAGE_SAMPLE: &str = r#"(.*?)"#;', source("broker.rs"), re.S)
    if match is None:
        pytest.fail("broker.rs pins no controller message sample")
    return json.loads(match[1])


def legacy_actions() -> tuple[str, ...]:
    native = source("main.rs")
    allowed = native[native.index("fn allowed("):]
    return tuple(re.findall(r'"([a-z0-9_]+)"', allowed[:allowed.index("\n}\n")]))


def test_rust_workflow_allowlist_matches_python():
    main = source("main.rs")
    actions = str_array(main, "WORKFLOW_ACTIONS")
    assert actions == wf.WORKFLOW_ACTIONS
    assert len(set(actions)) == 32
    assert not set(actions) & set(legacy_actions())
    profile_scoped = str_array(main, "PROFILE_SCOPED_ACTIONS")
    hf_scoped = str_array(main, "HF_SCOPED_ACTIONS")
    profile_secrets = str_array(source("profiles.rs"), "PROFILE_SECRETS")
    assert profile_secrets == ("RUNPOD_API_KEY",)
    native_scopes = {**{name: frozenset(profile_secrets) for name in profile_scoped},
                     **{name: frozenset({"HF_TOKEN"}) for name in hf_scoped}}
    assert native_scopes == dict(wf.SECRET_SCOPES)
    # The profile edit check of D5 is a workflow action served by 9.3's module.
    edit_check = str_const(source("profiles.rs"), "EDIT_CHECK_ACTION")
    assert wf.ACTION_MODULES[edit_check] == "tuning_factory.workflow_runs"
    assert edit_check not in native_scopes


def test_rust_profile_fields_and_secret_names_match_python():
    profiles = source("profiles.rs")
    main = source("main.rs")
    assert str_array(profiles, "RUNPOD_PROFILE_FIELDS") == wf.RUNPOD_PROFILE_FIELDS
    assert str_array(profiles, "SERVER_PROFILE_FIELDS") == wf.SERVER_PROFILE_FIELDS
    assert str_array(main, "SECRETS") == wf.SECRET_FIELDS
    assert set(str_array(profiles, "PROFILE_SECRETS")) <= set(wf.SECRET_FIELDS)
    assert str_const(profiles, "SERVICE") == "kr.kbiofoundry.tuningfactory"
    assert usize_const(profiles, "MAX_PROFILES") == wf.WorkflowEnvelope.model_fields["profiles"].metadata[0].max_length
    id_limits = [item.max_length for item in wf.NativeProfile.model_fields["profile_id"].metadata
                 if getattr(item, "max_length", None)]
    assert usize_const(profiles, "MAX_PROFILE_ID") == min(id_limits) == 64
    # The per-profile vault entry name of D6.
    assert re.search(r'format!\("executor-profile/\{id\}/\{name\}"\)', profiles)
    # The pasted-key markers and the key-file fields they apply to are the channel's.
    assert str_array(main, "KEY_MARKERS") == wf.KEY_MARKERS
    assert str_array(main, "KEY_FILE_FIELDS") == wf.KEY_FILE_FIELDS == ("ssh_key_file", "server_ssh_key_file")


def desktop_main(monkeypatch, capsys, request) -> dict:
    """One request through ``desktop.main`` exactly as the native side sends it (one stdin line, one stdout line)."""
    monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps(request) + "\n"))
    desktop.main()
    return json.loads(capsys.readouterr().out.strip().splitlines()[-1])


def workflow_request(tmp_path, profiles, settings=None) -> dict:
    return {"channel": "workflow", "root": str(tmp_path / "state"), "settings": {} if settings is None else settings,
            "profiles": profiles, "secrets": {}, "action": "projects_list", "data": {}}


def test_profile_names_with_key_words_are_not_pasted_keys(tmp_path, monkeypatch, capsys):
    """9.1 review M4: markers are pasted keys only in ``KEY_FILE_FIELDS``; a display name may say anything.

    Natively, key content is refused in every profile field at save time (stricter, so the channel never sees a
    marker outside the key-file fields); names with ordinary KEY words are saved and must reach the channel.
    """
    fields = {"provider": "existing_runpod", "name": "API KEY rotation lab", "pod_id": "pod1",
              "ssh_fingerprint": "SHA256:" + "A" * 43, "ssh_key_file": "C:\\keys\\id_ed25519"}
    marker_words = "lab " + "PRIVATE" + " KEY rotation"           # built at runtime, like every marker below
    for name in ("API KEY rotation lab", "Private key server", "KEY-1 pod", marker_words):
        profiles = [{"profile_id": "gpu-a", "fields": {**fields, "name": name}, "RUNPOD_API_KEY_saved": True}]
        answer = desktop_main(monkeypatch, capsys, workflow_request(tmp_path, profiles))
        assert answer == {"ok": True, "result": {"status": "ok", "issues": [], "data": {"projects": []}}}, name
    # Key content in a key-file field (either provider) never reaches a handler.
    header = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
    server = {"provider": "ssh_server", "name": "lab", "server_ssh_key_file": header + "\nAAAA"}
    for pasted in ({**fields, "ssh_key_file": header + "\nAAAA"}, server):
        answer = desktop_main(monkeypatch, capsys, workflow_request(tmp_path, [{"profile_id": "p", "fields": pasted}]))
        assert answer == {"ok": False, "error": wf.CREDENTIALS_REFUSED}
        assert header not in json.dumps(answer)


def test_profile_ids_are_one_identity_on_both_sides(tmp_path, monkeypatch, capsys):
    """9.1 review M9: Windows file names and Credential Manager entries ignore case and trailing dots.

    Rust's ``profile_id_key`` (collisions refused at save, duplicates never served) must equal the channel's
    ``casefold().rstrip(".")`` on every valid profile ID, so the channel never sees two IDs for one entry.
    """
    body = re.search(r"pub fn profile_id_key\(id: &str\) -> String \{ (.*?) \}", source("profiles.rs"))
    assert body and body[1] == "id.trim_end_matches('.').to_ascii_lowercase()"
    alphabet = string.ascii_letters + string.digits + "_-."
    samples = [*alphabet, "GPU-A", "gpu-a.", "Gpu-A..", "gpu-a.1", "A_b-C.d.", "x" * 63 + "."]
    for profile_id in samples:
        assert profile_id.rstrip(".").lower() == profile_id.casefold().rstrip("."), profile_id
    fields = {"provider": "existing_runpod", "name": "Pod", "pod_id": "pod1"}
    same = [{"profile_id": "GPU-A", "fields": fields}, {"profile_id": "gpu-a.", "fields": fields}]
    assert desktop_main(monkeypatch, capsys, workflow_request(tmp_path, same)) == \
        {"ok": False, "error": wf.INVALID_ENVELOPE}
    distinct = [{"profile_id": "gpu-a", "fields": fields}, {"profile_id": "gpu-a.1", "fields": fields}]
    assert desktop_main(monkeypatch, capsys, workflow_request(tmp_path, distinct))["ok"] is True


def test_workflow_settings_are_always_an_object(tmp_path, monkeypatch, capsys):
    """The channel answers a non-object ``settings`` with a fixed envelope error; Rust always sends an object."""
    for settings in ([], "x", None, 3):
        request = {**workflow_request(tmp_path, []), "settings": settings}
        assert desktop_main(monkeypatch, capsys, request) == {"ok": False, "error": wf.INVALID_ENVELOPE + " (settings)"}
    main = source("main.rs")
    builder = main[main.index("fn build_workflow_request("):]
    assert "Value::Object(settings)" in builder[:builder.index("\n}\n")]


def test_rust_controller_message_follows_the_plan_schema():
    broker = source("broker.rs")
    assert str_const(broker, "START_SCHEMA") == START_SCHEMA
    assert str_const(broker, "BROKER_SCHEMA") == BROKER_SCHEMA
    assert str_const(broker, "CONTROLLER_ARG") == CONTROLLER_ARG
    assert str_array(broker, "CONTROLLER_TRACKING_FIELDS") == TRACKING_FIELDS
    sample = controller_sample()
    assert set(sample) == START_KEYS          # no "settings": the broker never sends StartSettings
    assert sample["schema"] == START_SCHEMA
    assert OWNER_ID.fullmatch(sample["owner_id"])
    assert set(sample["tracking"]) <= set(TRACKING_FIELDS)
    assert all(isinstance(value, str) for value in sample["tracking"].values())
    assert sample["profiles"], "the sample must cover profiles"
    for profile in sample["profiles"]:
        assert set(profile) == PROFILE_START_KEYS
        assert set(profile["secrets"]) <= {"RUNPOD_API_KEY"}
        # The public part is exactly what the workflow channel accepts as a native profile.
        wf.NativeProfile.model_validate({"profile_id": profile["profile_id"], "fields": profile["fields"]})
    # The broker's owner ID format, at its longest, is a valid graph_store owner ID.
    template = re.search(r'format!\("(desktop\.\{pid\}\.\{nanos\})"\)', broker)
    assert template, "owner_id must use the plan's desktop.{pid}.{nanos} format"
    longest = template[1].format(pid=2**32 - 1, nanos=2**128 - 1)
    assert OWNER_ID.fullmatch(longest) and OWNER_ID.fullmatch(template[1].format(pid=0, nanos=0))


def test_rust_controller_message_matches_python(tmp_path):
    name = "tuning_factory.controller_entry"
    try:
        entry = importlib.import_module(name)
    except ModuleNotFoundError as exc:
        if exc.name != name:
            raise
        pytest.skip("9.4's controller entry is not written yet; the plan schema test pins the message meanwhile")
    broker = source("broker.rs")
    assert str_const(broker, "START_SCHEMA") == entry.START_SCHEMA
    assert set(str_array(broker, "CONTROLLER_TRACKING_FIELDS")) == set(entry.TrackingScope.model_fields)
    sample = {**controller_sample(), "state_root": str(tmp_path / "state")}
    start = entry.ControllerStart.model_validate(sample)
    secrets = [sample["tracking"].get(name, "") for name in TRACKING_SECRETS]
    secrets += [value for profile in sample["profiles"] for value in profile["secrets"].values()]
    for value in filter(None, secrets):
        assert value not in repr(start) and value not in str(start)
