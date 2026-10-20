"""Task 9.5: the contract between the native broker (Rust) and the Python sidecar.

The Rust sources are read as text, so this runs on every CI platform without cargo. The schema values fixed by the
Task 9 plan (§4 "9.4" and "9.5") are pinned here too, so neither side can drift alone while the 9.4 controller entry
is not written yet. Once ``tuning_factory.controller_entry`` exists, the parity test checks it directly.
"""
import importlib
import json
import re
from pathlib import Path

import pytest

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
    # The pasted-key markers refused natively are the ones the channel refuses.
    assert str_array(main, "KEY_MARKERS") == wf.KEY_MARKERS


def test_profile_names_with_key_words_are_not_pasted_keys(tmp_path):
    """9.1 review M4: markers are pasted keys in the key-file fields; a name with KEY words is ordinary data.

    Natively, key content is refused in every profile field at save time (stricter); the channel must still accept
    names that merely contain words such as KEY.
    """
    assert str_array(source("main.rs"), "KEY_FILE_FIELDS") == ("ssh_key_file", "server_ssh_key_file")
    fields = {"provider": "existing_runpod", "name": "API KEY rotation lab", "pod_id": "pod1",
              "ssh_fingerprint": "SHA256:" + "A" * 43, "ssh_key_file": "C:\\keys\\id_ed25519"}

    def request(profile_fields):
        return {"channel": "workflow", "root": str(tmp_path / "state"), "settings": {}, "secrets": {},
                "profiles": [{"profile_id": "gpu-a", "fields": profile_fields, "RUNPOD_API_KEY_saved": True}],
                "action": "projects_list", "data": {}}

    for name in ("API KEY rotation lab", "Private key server", "KEY-1 pod"):
        assert wf.handle(request({**fields, "name": name}))["status"] == "ok"
    # Key content in a key-file field never reaches a handler (built at runtime for the history-walking scan).
    header = "-----BEGIN OPENSSH " + "PRIVATE KEY-----"
    with pytest.raises(ValueError, match=wf.CREDENTIALS_REFUSED):
        wf.handle(request({**fields, "ssh_key_file": header + "\nAAAA"}))


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
