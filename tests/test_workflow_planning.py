"""Task 9.2: profiles, observations, HF-root snapshots, benchmarks, validation and offline dry plans."""
import importlib
import json
import os
import socket
import sqlite3
import subprocess
import sys
import textwrap
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import unquote

import httpx
import pytest
import test_desktop_projects as desk
from test_executor_profiles import LIVE_POD, PIN
from test_gpu_servers import UUID2

from tuning_factory.execution import RunPodController as REAL_RUNPOD_CONTROLLER
from tuning_factory.registry import Registry, canonical, digest

ROOT = Path(__file__).resolve().parents[1]
# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
SECRET = "synthetic-" + "runpod-" + "k" * 12
HF_SECRET = "synthetic-" + "hf-" + "t" * 12
MLFLOW_SECRET = "synthetic-" + "mlflow-" + "s" * 12
NOW = 1_800_000_000.0
REPO = "Qwen/Qwen2.5-0.5B-Instruct"
COMMIT = "c" * 40
CONFIG = canonical({"model_type": "qwen2", "max_position_embeddings": 32768, "num_hidden_layers": 24,
                    "torch_dtype": "bfloat16"})
TOKENIZER = {"tokenizer.json": canonical({"version": "1.0", "model": {"type": "BPE"}}),
             "tokenizer_config.json": canonical({"chat_template": "{{ messages }}", "model_max_length": 32768}),
             "special_tokens_map.json": canonical({"eos_token": "<|im_end|>"}),
             "chat_templates/tool_use.jinja": b"{{ tools }}"}
WEIGHTS = ("model.safetensors", "model-00001-of-00002.safetensors", "pytorch_model.bin", "model.gguf",
           "consolidated.pt")
TARGETED = {"hyperparameters": {"lora_target": "q_proj,v_proj"}}
ESTIMATE = {"profile_id": "estimate-a", "gpu_type": "A100", "gpu_count": 1, "total_hourly_cost_usd": 2.0}
TOKEN_SETTINGS = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "token",
                  "MLFLOW_TRACKING_TOKEN_saved": True}
ACTIONS_92 = desk.ACTIONS_92


def planning():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.workflow_planning")
    except ModuleNotFoundError as exc:
        if exc.name != "tuning_factory.workflow_planning":
            raise
        pytest.fail("the workflow planning module (Task 9.2) is missing")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Planning is tested with fakes only: no RunPod API, SSH, Hugging Face or MLflow connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("workflow planning tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


def run(state, action, data=None, *, settings=None, profiles=(), secrets=None, clock=NOW):
    """One workflow action with an injected clock, exactly as the channel dispatches it."""
    planning()
    wf = desk.workflows()
    native = {item["profile_id"]: wf.NativeProfile.model_validate(item) for item in profiles}
    context = wf.WorkflowContext(state_dir=Path(state), settings=dict(settings or {}), profiles=native,
                                 secrets=dict(secrets or {}), clock=lambda: clock)
    return wf.dispatch_action(context, action, {} if data is None else data).model_dump(mode="json")


def codes(result):
    return [issue["code"] for issue in result["issues"]]


def files(directory):
    directory = Path(directory)
    return sorted(path.relative_to(directory).as_posix() for path in directory.rglob("*")) if directory.exists() else []


def state_bytes(state):
    return b"".join(desk.snapshot(state).values())


# -- fakes ---------------------------------------------------------------------------------------

class FakeHub:
    """Hugging Face over httpx.MockTransport: revision info, config.json and tokenizer files; never a socket."""

    def __init__(self, repo=REPO, commit=COMMIT, files=None, siblings=None, second_config=None, streamed=False):
        self.repo, self.commit, self.streamed = repo, commit, streamed
        self.files = {"config.json": CONFIG, **TOKENIZER} if files is None else dict(files)
        self.siblings = (list(siblings) if siblings is not None
                         else [*sorted(self.files), *WEIGHTS, "README.md", "generation_config.json"])
        self.second_config = second_config
        self.requests, self.config_reads = [], 0

    def respond(self, request):
        self.requests.append(request)
        path = unquote(request.url.path)
        if path == f"/api/models/{self.repo}/revision/{self.commit}":
            return httpx.Response(200, json={"sha": self.commit, "gated": False,
                                             "siblings": [{"rfilename": name} for name in self.siblings]})
        prefix = f"/{self.repo}/resolve/{self.commit}/"
        if path.startswith(prefix):
            name = path[len(prefix):]
            if name == "config.json":
                self.config_reads += 1
                if self.second_config is not None and self.config_reads > 1:
                    return httpx.Response(200, content=self.second_config)
            if name in self.files:
                if self.streamed and name != "config.json":   # chunked: no Content-Length to refuse early
                    return httpx.Response(200, stream=httpx.ByteStream(self.files[name]))
                return httpx.Response(200, content=self.files[name])
        return httpx.Response(404)

    def install(self, monkeypatch):
        wp = planning()
        real = getattr(wp._hub, "real_hub", wp._hub)   # the module's own factory, even when installed twice

        def hub(token):
            return real(token, transport=httpx.MockTransport(self.respond))

        hub.real_hub = real
        monkeypatch.setattr(wp, "_hub", hub)
        return self

    @property
    def paths(self):
        return [unquote(request.url.path) for request in self.requests]


class FakeRunPod:
    """A RunPodController stand-in that serves only the read-only ``status`` GET."""

    def __init__(self, pod=None, error=None):
        self.pod = dict(LIVE_POD if pod is None else pod)
        self.error = error
        self.calls, self.keys = [], []

    def install(self, monkeypatch):
        from tuning_factory import execution
        fake = self

        class Controller:
            def __init__(self, api_key, client=None):
                fake.keys.append(api_key)

            def status(self, pod_id):
                fake.calls.append(("status", pod_id))
                if fake.error is not None:
                    raise fake.error
                return dict(fake.pod)

        monkeypatch.setattr(execution, "RunPodController", Controller)
        return self


def refuse_ssh(monkeypatch):
    from tuning_factory import desktop_runtime
    monkeypatch.setattr(desktop_runtime, "connection",
                        lambda *a, **kw: pytest.fail("RunPod inspection must not open SSH"))


# -- scenario builders ---------------------------------------------------------------------------

def register_data(state, tmp_path, dataset_id="mapped", *, export_allowed=True, rows=None):
    rows = rows or [(f"{dataset_id} 질문 {i}", f"문맥 {i}", f"답 {i}") for i in range(1, 9)]
    source_id = desk.import_source(state, tmp_path, desk.csv_bytes(rows), f"{dataset_id}.csv")
    shown = desk.preview(state, source_id)
    assert shown["status"] == "ok", shown
    result = desk.register(state, source_id, shown["data"]["preview_sha256"], dataset_id, "1",
                           export_allowed=export_allowed)
    assert result["status"] == "ok", result
    return result["data"]["dataset"]


def practice_dataset(state, tmp_path):
    """The reserved practice ID, registered the way only the legacy tutorial path may."""
    path = tmp_path / "practice.json"
    path.write_bytes(canonical([{"instruction": f"연습 {i}", "input": "", "output": f"답 {i}"} for i in range(4)]))
    record = Registry(state / "registry").register_dataset("tutorial_sample", "1", path, export_allowed=True,
                                                          synthetic=True)
    return {"dataset_id": "tutorial_sample", "version": "1", "sha256": record["sha256"]}


def save_recipe(state, recipe_id="sft-lora", **spec):
    result = desk.call(state, "recipe_save", {"spec": {"recipe_id": recipe_id, "name": "SFT LoRA", **spec},
                                              "expected_revision": None})
    assert result["status"] == "ok", result
    return result["data"]["revision_sha256"]


def snapshot_root(state, monkeypatch, **hub):
    fake = FakeHub(**hub).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": fake.repo, "revision": fake.commit,
                                                "download_approved": True})
    assert result["status"] == "ok", result
    return result["data"]["root"]


def from_root(base, initialization="new_adapter"):
    return {"policy": {"base": base}, "initialization": initialization}


def output(node_id):
    return {"node_id": node_id, "output_name": "model"}


def train(node_id, recipe, model_input, profiles=("estimate-a",), binding="train"):
    return {"kind": "train", "id": node_id, "label": f"{node_id} 학습", "recipe_revision": recipe,
            "model_input": model_input, "dataset_binding": binding, "executor_profile_ids": list(profiles)}


def binding(binding_id, role, dataset):
    return {"binding_id": binding_id, "role": role, "dataset": dataset}


def make_project(state, nodes, datasets, **graph):
    created = desk.call(state, "project_create", {"request_id": uuid.uuid4().hex, "name": "효소 기능 요약",
                                                  "purpose": "예제 없이 정한 새 목적"})
    assert created["status"] == "ok", created
    project = created["data"]["project"]
    spec = {**project, "datasets": list(datasets), "graph": {"nodes": list(nodes), **graph}}
    saved = desk.call(state, "project_save", {"spec": spec, "expected_revision": created["data"]["revision_sha256"]})
    assert saved["status"] == "ok", saved
    return project["project_id"], saved["data"]["revision_sha256"]


def scenario(state, tmp_path, monkeypatch, *, profiles=("estimate-a",), recipe_spec=None, export_allowed=True,
             dataset=None, build=None, **graph):
    dataset = dataset or register_data(state, tmp_path, export_allowed=export_allowed)
    recipe = save_recipe(state, **(TARGETED if recipe_spec is None else recipe_spec))
    root = snapshot_root(state, monkeypatch)
    nodes = build(recipe, root) if build else [train("train", recipe, from_root(root), profiles)]
    graph = {"retry_policy": {"max_attempts": 2}, **graph}
    project_id, revision = make_project(state, nodes, [binding("train", "train", dataset)], **graph)
    return SimpleNamespace(dataset=dataset, recipe=recipe, root=root, project_id=project_id, revision=revision)


def plan_request(project_id, *, simulation=True, resources=(("train", 1800),), estimates=(ESTIMATE,), **extra):
    return {"project_id": project_id, "simulation": simulation,
            "node_resources": [{"node_id": node, "timeout_seconds": seconds} for node, seconds in resources],
            "estimates": list(estimates), **extra}


def runpod_profile(tmp_path, profile_id="pod-a", **fields):
    return {"profile_id": profile_id, "RUNPOD_API_KEY_saved": True,
            "fields": {"provider": "existing_runpod", "name": "Pod A", "pod_id": "pod1", "ssh_fingerprint": PIN,
                       "ssh_key_file": str(tmp_path / "keys" / "pod-key"), **fields}}


def server_profile(profile_id="lab", **fields):
    return {"profile_id": profile_id,
            "fields": {"provider": "ssh_server", "name": "Lab server", "server_ssh_host": "gpu.example",
                       "server_ssh_port": "22", "server_ssh_username": "researcher", "server_ssh_fingerprint": PIN,
                       "server_remote_root": "/home/researcher/factory",
                       "server_ssh_key_file": str(ROOT / "keys" / "test-key"),
                       "server_total_hourly_cost_usd": "0", "server_selected_gpu_uuids": json.dumps([UUID2]),
                       **fields}}


def inspect(state, monkeypatch, profile, *, clock=NOW, pod=None, error=None):
    fake = FakeRunPod(pod, error).install(monkeypatch)
    result = run(state, "executor_profile_inspect", {"profile_id": profile["profile_id"]}, profiles=[profile],
                 secrets={"RUNPOD_API_KEY": SECRET}, clock=clock)
    return result, fake


def real_scenario(state, tmp_path, monkeypatch, **options):
    profile = runpod_profile(tmp_path)
    found = scenario(state, tmp_path, monkeypatch, profiles=("pod-a",), **options)
    inspected, _ = inspect(state, monkeypatch, profile)
    assert inspected["status"] == "ok", inspected
    found.profile = profile
    return found


def real_plan(state, found, *, settings=TOKEN_SETTINGS, clock=NOW + 60, profile=None, **request):
    request = {"simulation": False, "estimates": (), **request}
    return run(state, "graph_plan", plan_request(found.project_id, **request), settings=settings,
               profiles=[profile or found.profile], clock=clock)


# 1
def test_profile_revision_is_content_addressed_over_public_fields(tmp_path):
    wp = planning()
    wf = desk.workflows()
    fields = runpod_profile(tmp_path)["fields"]
    revision = wp.profile_revision("pod-a", fields)
    assert revision == wp.profile_revision("pod-a", dict(reversed(list(fields.items()))))
    assert revision == digest(canonical({"schema": "executor-profile/1", "profile_id": "pod-a", "fields": fields}))
    changed = {wp.profile_revision("pod-a", {**fields, name: value}) for name, value in (
        ("name", "Renamed"), ("pod_id", "pod2"), ("ssh_fingerprint", "SHA256:" + "B" * 43),
        ("ssh_key_file", str(tmp_path / "other-key")))}
    assert len(changed) == 4 and revision not in changed
    assert wp.profile_revision("pod-b", fields) != revision
    with pytest.raises(ValueError):
        wf.NativeProfile.model_validate({"profile_id": "pod-a", "fields": {**fields, "RUNPOD_API_KEY": SECRET}})
    with pytest.raises(ValueError) as refused:
        wp.profile_revision("pod-a", {**fields, "RUNPOD_API_KEY": SECRET})
    assert SECRET not in str(refused.value)
    assert wp.current_profile_revisions(wf.WorkflowContext(state_dir=tmp_path, profiles={
        "pod-a": wf.NativeProfile.model_validate(runpod_profile(tmp_path))})) == {"pod-a": revision}


# 2
def test_public_profiles_reuse_the_task8_destination_hashes(tmp_path):
    wp = planning()
    from test_gpu_servers import FakeServer

    from tuning_factory.executor_profiles import ExecutorProfile, runpod_transport_sha256
    from tuning_factory.gpu_servers import SSHServerProfile, server_settings
    fields = runpod_profile(tmp_path)["fields"]
    public = wp.public_profile("pod-a", fields)
    assert isinstance(public, ExecutorProfile)
    assert public.transport_settings_sha256 == runpod_transport_sha256(fields)
    assert (public.profile_id, public.revision, public.name, public.pod_id, public.credential_scope) == (
        "pod-a", wp.profile_revision("pod-a", fields), "Pod A", "pod1", "local")
    native = server_profile()["fields"]
    config = server_settings(native)
    facts = FakeServer(tmp_path / "remote").facts(config)
    server = wp.public_profile("lab", native, facts)
    assert isinstance(server, SSHServerProfile)
    assert server.transport == config.transport and server.server_id == facts.server_id
    assert server.revision == wp.profile_revision("lab", native) and server.credential_scope == "local"
    with pytest.raises(ValueError):
        wp.public_profile("lab", native)
    with pytest.raises(ValueError):
        wp.public_profile("lab", {**native, "server_remote_root": "/elsewhere"}, facts)


# 3
def test_runpod_inspection_is_read_only_and_stores_public_facts(tmp_path, monkeypatch):
    wp = planning()
    from tuning_factory.executor_profiles import runpod_facts
    state = tmp_path / "state"
    refuse_ssh(monkeypatch)
    profile = runpod_profile(tmp_path)
    result, fake = inspect(state, monkeypatch, profile)
    assert result["status"] == "ok", result
    assert fake.calls == [("status", "pod1")] and fake.keys == [SECRET]
    observed = datetime.fromtimestamp(NOW, tz=timezone.utc)
    facts = runpod_facts(LIVE_POD, observed_at=observed)
    assert facts.gpu_type == LIVE_POD["gpu"]["id"]
    revision = wp.profile_revision("pod-a", profile["fields"])
    assert result["data"] == {"profile": wp.public_profile("pod-a", profile["fields"]).model_dump(mode="json"),
                              "facts": facts.model_dump(mode="json"), "observed_at": observed.isoformat(),
                              "revision": revision}
    stored = wp.load_observation(state, "pod-a")
    assert stored["revision"] == revision and stored["facts"] == facts and stored["observed_at"] == observed
    assert stored["profile"] == wp.public_profile("pod-a", profile["fields"])
    raw = (state / "workflows" / "observations" / "pod-a.json").read_bytes()
    assert SECRET.encode() not in raw and profile["fields"]["ssh_key_file"].encode() not in raw
    assert "pod-key".encode() not in raw
    assert wp.load_observation(state, "other") is None
    before = desk.snapshot(state / "workflows" / "observations")
    alias, alias_fake = inspect(state, monkeypatch, {**profile, "profile_id": "POD-A."})
    desk.only_issue(alias, "invalid", "observation_conflict", "/profile_id")
    assert alias_fake.calls == []               # refused before any request
    assert desk.snapshot(state / "workflows" / "observations") == before
    fake = FakeRunPod().install(monkeypatch)
    missing = run(state, "executor_profile_inspect", {"profile_id": "pod-a"}, profiles=[profile])
    desk.only_issue(missing, "invalid", "credential_missing", "/profile_id")
    unknown = run(state, "executor_profile_inspect", {"profile_id": "pod-z"}, profiles=[profile],
                  secrets={"RUNPOD_API_KEY": SECRET})
    desk.only_issue(unknown, "invalid", "executor_profile_missing", "/profile_id")
    assert fake.calls == []


# 4
@pytest.mark.parametrize("price", [0, -1, "nan", "missing", True])
def test_runpod_inspection_refuses_a_missing_or_nonpositive_price(tmp_path, monkeypatch, price):
    wp = planning()
    state = tmp_path / "state"
    refuse_ssh(monkeypatch)
    pod = {key: value for key, value in LIVE_POD.items() if key != "costPerHr"}
    if price != "missing":
        pod["costPerHr"] = price
    result, fake = inspect(state, monkeypatch, runpod_profile(tmp_path), pod=pod)
    issue = desk.only_issue(result, "invalid", "inspection_failed")
    assert issue["message"] == "positive finite live RunPod API price required"
    assert fake.calls == [("status", "pod1")]
    assert wp.load_observation(state, "pod-a") is None
    assert not (state / "workflows" / "observations").exists() or files(state / "workflows" / "observations") == []


def test_runpod_inspection_reports_provider_failures_without_details(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    refuse_ssh(monkeypatch)
    profile = runpod_profile(tmp_path)
    result, _ = inspect(state, monkeypatch, profile, error=RuntimeError("RunPod said " + SECRET))
    issue = desk.only_issue(result, "invalid", "inspection_failed")
    assert issue["message"] == "RuntimeError"
    result, _ = inspect(state, monkeypatch, profile, pod={**LIVE_POD, "desiredStatus": "EXITED"})
    assert desk.only_issue(result, "invalid", "inspection_failed")["message"] == \
        "Pod must already be running; start it separately"
    result, fake = inspect(state, monkeypatch, runpod_profile(tmp_path, ssh_fingerprint="SHA256:short"))
    desk.only_issue(result, "invalid", "executor_profile_invalid", "/profile_id")
    assert fake.calls == []                     # invalid public fields are refused before any request
    result, _ = inspect(state, monkeypatch, profile, pod={**LIVE_POD, "id": "pod9"})
    desk.only_issue(result, "invalid", "inspection_failed")
    assert SECRET.encode() not in state_bytes(state)


# 5
def test_ssh_inspection_builds_the_profile_from_observed_facts(tmp_path, monkeypatch):
    wp = planning()
    from test_gpu_servers import FakeServer

    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_servers import server_settings
    state = tmp_path / "state"
    server = FakeServer(tmp_path / "remote").install(monkeypatch)
    monkeypatch.setattr(desktop_runtime, "check", lambda *a, **kw: pytest.fail("inspection runs no runtime probe"))
    profile = server_profile()
    result = run(state, "executor_profile_inspect", {"profile_id": "lab"}, profiles=[profile])
    assert result["status"] == "ok", result
    assert [argv[0] for argv in server.commands] == ["python3", "nvidia-smi"]
    stored = wp.load_observation(state, "lab")
    facts = server.facts(server_settings(profile["fields"]))
    assert stored["facts"].server_id == facts.server_id == stored["profile"].server_id
    assert stored["facts"].selected_gpu_uuids == (UUID2,)
    assert stored["revision"] == wp.profile_revision("lab", profile["fields"]) == result["data"]["revision"]
    assert stored["profile"] == wp.public_profile("lab", profile["fields"], stored["facts"])
    assert b"test-key" not in (state / "workflows" / "observations" / "lab.json").read_bytes()


# 6
def test_model_root_snapshot_downloads_only_config_and_tokenizer_files(tmp_path, monkeypatch):
    wp = planning()
    from tuning_factory.model_artifacts import HFRootRef, tokenizer_fingerprint
    from tuning_factory.simulated_dispatch import DirectoryRootSnapshots
    state = tmp_path / "state"
    request = {"repo_id": REPO, "revision": COMMIT, "download_approved": False}
    hub = FakeHub().install(monkeypatch)
    desk.only_issue(run(state, "model_root_snapshot", request, secrets={"HF_TOKEN": HF_SECRET}), "invalid",
                    "approval_required", "/download_approved")
    assert hub.requests == [] and not state.exists()
    result = run(state, "model_root_snapshot", {**request, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    assert result["status"] == "ok", result
    allowed = {f"/api/models/{REPO}/revision/{COMMIT}",
               *(f"/{REPO}/resolve/{COMMIT}/{name}" for name in ("config.json", *TOKENIZER))}
    assert set(hub.paths) <= allowed
    assert {f"/{REPO}/resolve/{COMMIT}/{name}" for name in TOKENIZER} <= set(hub.paths)
    assert not any(path.endswith(WEIGHTS) or "README" in path or "generation_config" in path for path in hub.paths)
    assert all(request.headers.get("authorization") == "Bearer " + HF_SECRET for request in hub.requests)
    root = HFRootRef.model_validate(result["data"]["root"])
    directory = wp.root_snapshots_dir(state) / "Qwen" / "Qwen2.5-0.5B-Instruct" / COMMIT
    assert sorted(files(directory)) == sorted(["chat_templates", "config.json", *TOKENIZER])
    assert (directory / "config.json").read_bytes() == CONFIG
    assert root.config_sha256 == digest(CONFIG) and root.tokenizer_fingerprint == tokenizer_fingerprint(directory)
    assert DirectoryRootSnapshots(wp.root_snapshots_dir(state)).snapshot(root) == directory
    assert result["data"]["profile"]["template"] == "qwen" and result["data"]["profile"]["revision"] == COMMIT
    [listed] = run(state, "model_roots_list")["data"]["roots"]
    assert listed["root"] == root.model_dump(mode="json") and listed["snapshot_ready"] is True
    assert listed["targets_ready"] is False
    assert HF_SECRET.encode() not in state_bytes(state) and HF_SECRET not in json.dumps(result)
    # The same snapshot again is idempotent; without a token no Authorization header is ever sent.
    again = FakeHub().install(monkeypatch)
    assert run(state, "model_root_snapshot", {**request, "download_approved": True}) == result
    assert again.requests and not any("authorization" in request.headers for request in again.requests)


@pytest.mark.parametrize("case", ["config_changed", "oversized", "oversized_streamed", "over_total",
                                  "weights_named_like_a_template", "unsafe_name", "incomplete_tokenizer",
                                  "unsupported"])
def test_model_root_snapshot_refusals_publish_nothing(tmp_path, monkeypatch, case):
    wp = planning()
    state = tmp_path / "state"
    options = {}
    expected = {"config_changed": "config_changed", "oversized": "size_limit", "oversized_streamed": "size_limit",
                "over_total": "size_limit", "weights_named_like_a_template": "snapshot_refused",
                "unsafe_name": "snapshot_refused", "incomplete_tokenizer": "tokenizer_incomplete",
                "unsupported": "model_unsupported"}[case]
    if case == "config_changed":
        options["second_config"] = canonical({"model_type": "qwen2", "num_hidden_layers": 2})
    elif case in {"oversized", "oversized_streamed"}:
        monkeypatch.setattr(wp, "MAX_ROOT_FILE_BYTES", 1024)
        options["files"] = {"config.json": CONFIG, **TOKENIZER, "tokenizer.json": b"x" * 4096}
        options["streamed"] = case == "oversized_streamed"
    elif case == "over_total":   # every file fits alone; together they exceed the total
        monkeypatch.setattr(wp, "MAX_ROOT_TOTAL_BYTES", len(CONFIG) + 600)
        options["files"] = {"config.json": CONFIG, **TOKENIZER, "tokenizer.json": b"x" * 500,
                            "tokenizer_config.json": b"{" + b" " * 300 + b"}"}
        options["streamed"] = True
    elif case == "weights_named_like_a_template":
        options["files"] = {"config.json": CONFIG, **TOKENIZER, "chat_templates/evil.safetensors": b"w"}
    elif case == "unsafe_name":
        options["files"] = {"config.json": CONFIG, **TOKENIZER, "chat_templates/../secret.jinja": b"w"}
    elif case == "incomplete_tokenizer":
        options["files"] = {"config.json": CONFIG, "special_tokens_map.json": b"{}"}
    else:
        options["files"] = {"config.json": canonical({"model_type": "unknown_next_architecture"}), **TOKENIZER}
    hub = FakeHub(**options).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True})
    desk.only_issue(result, "invalid", expected)
    roots = wp.root_snapshots_dir(state)
    assert files(roots) == [] and not (state / "workflows" / "root-index").exists()
    assert not any(path.endswith(WEIGHTS) or "evil" in path or "secret" in path for path in hub.paths)


# 7
def test_benchmark_store_is_content_addressed_by_fingerprint(tmp_path):
    wp = planning()
    from tuning_factory.benchmarks import BenchmarkSpec
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path, "heldout")
    spec = {"dataset": dataset, "evaluator_kind": "classification", "evaluator_version": "1",
            "ordered_sample_ids": ["row:0", "row:3"], "max_new_tokens": 32, "cutoff_len": 256, "seed": 7,
            "precision": "bf16", "quantization_bit": None, "tokenizer_fingerprint": "f" * 64, "template": "qwen",
            "semantic_model_kind": "causal_lm"}
    first = run(state, "benchmark_save", {"spec": spec})
    assert first["status"] == "ok", first
    assert run(state, "benchmark_save", {"spec": spec}) == first
    fingerprint = BenchmarkSpec.model_validate(spec).fingerprint()
    assert first["data"]["revision_sha256"] == fingerprint
    store = wp.BenchmarkStore(state / "workflows" / "benchmarks")
    assert [item.revision_sha256 for item in store.list()] == [fingerprint]
    assert store.get(fingerprint).spec == BenchmarkSpec.model_validate(spec)
    assert files(state / "workflows" / "benchmarks") == [f"{fingerprint}.json"]
    assert [item["revision_sha256"] for item in run(state, "benchmarks_list")["data"]["benchmarks"]] == [fingerprint]
    outside = run(state, "benchmark_save", {"spec": {**spec, "ordered_sample_ids": ["row:8"]}})
    desk.only_issue(outside, "invalid", "sample_id_invalid", "/spec/ordered_sample_ids")
    unknown = run(state, "benchmark_save", {"spec": {**spec, "dataset": {**dataset, "version": "9"}}})
    desk.only_issue(unknown, "invalid", "dataset_unknown", "/spec/dataset")
    path = state / "workflows" / "benchmarks" / f"{fingerprint}.json"
    path.write_bytes(path.read_bytes().replace(b'"seed":7', b'"seed":8'))
    with pytest.raises(ValueError):
        store.get(fingerprint)
    with pytest.raises(ValueError):
        store.get("0" * 64)
    # An idempotent rewrite over other bytes is this module's own refusal, never a generic version conflict.
    rewrite = run(state, "benchmark_save", {"spec": spec})
    issue = desk.only_issue(rewrite, "invalid", "benchmark_invalid", "/spec")
    assert issue["message"] == wp.BENCHMARK_STORED_DIFFERS


# 8
def test_graph_validate_maps_issues_to_paths_and_nodes(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path)
    heldout = register_data(state, tmp_path, "heldout")
    recipe = save_recipe(state, **TARGETED)
    root = snapshot_root(state, monkeypatch)
    datasets = [binding("train", "train", dataset), binding("val", "validation", heldout)]
    good, revision = make_project(state, [train("train", recipe, from_root(root))], datasets)
    result = run(state, "graph_validate", {"project_id": good})
    assert result == {"status": "ok", "issues": [], "data": {"project_id": good, "project_revision": revision,
                                                              "node_count": 1}}
    extra, _ = make_project(state, [{**train("train", recipe, from_root(root)), "foo": 1}], datasets)
    desk.only_issue(run(state, "graph_validate", {"project_id": extra}), "invalid", "extra_field",
                    "/graph/nodes/0")
    unknown, _ = make_project(state, [train("first", recipe, from_root(root)), train("train", "e" * 64,
                                                                                     from_root(root))], datasets)
    issue = desk.only_issue(run(state, "graph_validate", {"project_id": unknown}), "invalid", "recipe_missing",
                            "/graph/nodes/1/recipe_revision")
    assert issue["node_id"] == "train"
    cycle, _ = make_project(state, [train("a", recipe, from_root(root)), train("b", recipe, from_root(root))],
                            datasets, depends_on=[{"node_id": "a", "depends_on": "b"},
                                                  {"node_id": "b", "depends_on": "a"}])
    issue = desk.only_issue(run(state, "graph_validate", {"project_id": cycle}), "invalid", "cycle", "/graph")
    assert issue["node_id"] is None
    wrong, _ = make_project(state, [train("first", recipe, from_root(root)),
                                    train("train", recipe, from_root(root), binding="val")], datasets)
    issue = desk.only_issue(run(state, "graph_validate", {"project_id": wrong}), "invalid", "train_contract",
                            "/graph/nodes/1")
    assert issue["node_id"] == "train" and "train dataset binding" in issue["message"]
    empty, _ = make_project(state, [], [])
    desk.only_issue(run(state, "graph_validate", {"project_id": empty}), "invalid", "invalid_value", "/graph/nodes")
    other = dict(root, revision="d" * 40)
    missing_root, _ = make_project(state, [train("train", recipe, from_root(other))], datasets)
    issue = desk.only_issue(run(state, "graph_validate", {"project_id": missing_root}), "invalid",
                            "model_root_missing", "/graph/nodes/0")
    assert f"{REPO}@{'d' * 40}" in issue["message"] and issue["node_id"] == "train"
    desk.only_issue(run(state, "graph_validate", {"project_id": "absent"}), "invalid", "unknown_definition",
                    "/project_id")
    desk.only_issue(run(state, "graph_validate", {"project_id": good, "revision": "0" * 64}), "invalid",
                    "unknown_definition", "/revision")


# 9
def test_graph_plan_is_offline_and_mutates_no_provider(tmp_path, monkeypatch):
    planning()
    import paramiko

    from tuning_factory import desktop_runtime, execution, graph_store, jobs
    state = tmp_path / "state"
    found = real_scenario(state, tmp_path, monkeypatch)
    estimated, _ = make_project(state, [train("train", found.recipe, from_root(found.root))],
                                [binding("train", "train", found.dataset)])
    calls = []

    def spy(name):
        def record(*args, **kwargs):
            calls.append(name)
            raise AssertionError(f"{name} must not be reached from validation or planning")
        return record

    monkeypatch.setattr(execution, "RunPodController", REAL_RUNPOD_CONTROLLER)   # the inspection fake is gone
    for method in ("__init__", "request", "status", "create", "start", "stop", "delete"):
        monkeypatch.setattr(execution.RunPodController, method, spy(f"RunPodController.{method}"))
    monkeypatch.setattr(desktop_runtime, "connection", spy("desktop_runtime.connection"))
    monkeypatch.setattr(desktop_runtime, "executor_for", spy("desktop_runtime.executor_for"))
    monkeypatch.setattr(paramiko.SSHClient, "connect", spy("paramiko.connect"))
    monkeypatch.setattr(httpx.Client, "send", spy("httpx.send"))
    monkeypatch.setattr(graph_store.GraphStore, "submit", spy("GraphStore.submit"))
    monkeypatch.setattr(jobs.JobStore, "submit", spy("JobStore.submit"))
    monkeypatch.setattr(jobs.JobStore, "claim", spy("JobStore.claim"))
    environment = dict(os.environ)
    results = [run(state, "graph_validate", {"project_id": found.project_id}),
               run(state, "graph_plan", plan_request(estimated)),
               run(state, "graph_plan", plan_request(found.project_id, estimates=()), profiles=[found.profile],
                   clock=NOW + 7 * 24 * 3600),
               real_plan(state, found)]
    assert [result["status"] for result in results] == ["ok"] * 4, results
    assert calls == [] and dict(os.environ) == environment
    for result in results[1:]:
        assert (state / "workflows" / "plans" / f"{result['data']['plan_sha256']}.json").is_file()
        assert run(state, "graph_plan_get", {"plan_sha256": result["data"]["plan_sha256"]})["data"] == result["data"]
    database = state / "jobs.db"
    if database.exists():
        with sqlite3.connect(database) as db:
            tables = {row[0] for row in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for table in {"jobs", "graph_runs"} & tables:
                assert db.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0] == 0


# 10
def test_real_plan_pins_destination_and_tracking_hashes(tmp_path, monkeypatch):
    wp = planning()
    from tuning_factory.executor_profiles import runpod_transport_sha256
    from tuning_factory.graph_planner import ApprovalGrant
    from tuning_factory.tracking import DEFAULT_EXPERIMENT, public_tracking_settings, tracking_settings_sha256
    state = tmp_path / "state"
    found = real_scenario(state, tmp_path, monkeypatch)
    result = real_plan(state, found)
    assert result["status"] == "ok", result
    summary = result["data"]
    plan = wp.stored_plan(state, summary["plan_sha256"])
    assert plan.simulation is False and summary["simulation"] is False
    [executor] = plan.execution_choices.executors
    assert executor.transport_settings_sha256 == runpod_transport_sha256(found.profile["fields"])
    assert executor == wp.public_profile("pod-a", found.profile["fields"])
    public = public_tracking_settings("https://mlflow.invalid", DEFAULT_EXPERIMENT, None, auth_mode="token")
    expected = tracking_settings_sha256(public)
    destination = plan.execution_choices.tracking_destination
    assert (destination.profile_id, destination.revision, destination.public_settings_sha256) == (
        "mlflow", expected, expected)
    assert summary["tracking"] == {"profile_id": "mlflow", "public_settings_sha256": expected, "settings": public}
    ApprovalGrant.for_plan(plan).validate_plan(plan)
    assert summary["max_estimated_cost_usd"] == pytest.approx(2.0 * 1800 / 3600 * 2)
    [node] = summary["nodes"]
    assert node["allowed"] == [{"profile_id": "pod-a", "revision": executor.revision,
                                "resource_id": "existing_runpod:pod1", "gpu_type": "A100", "gpu_count": 1,
                                "total_hourly_cost_usd": 2.0, "cost_source": "runpod_api"}]
    assert summary["datasets"] == [{**found.dataset, "synthetic": False, "export_allowed": True}]
    text = json.dumps(summary)
    assert found.profile["fields"]["ssh_key_file"] not in text and "pod-key" not in text and SECRET not in text
    # The artifact location is part of the pinned hash exactly as the controller's tracker reports it.
    located = real_plan(state, found, settings={**TOKEN_SETTINGS,
                                                "FACTORY_MLFLOW_ARTIFACT_LOCATION": "s3://bucket/mlflow"})
    pinned = wp.stored_plan(state, located["data"]["plan_sha256"]).execution_choices.tracking_destination
    assert pinned.public_settings_sha256 == tracking_settings_sha256(public_tracking_settings(
        "https://mlflow.invalid", DEFAULT_EXPERIMENT, "s3://bucket/mlflow", auth_mode="token"))


# 11
@pytest.mark.parametrize("case", ["no_uri", "token_http", "token_http_artifacts", "oidc_incomplete", "stale",
                                  "fields_changed", "estimate", "practice", "not_exportable", "none_http"])
def test_real_plan_refusals(tmp_path, monkeypatch, case):
    wp = planning()
    from tuning_factory import auth, desktop
    state = tmp_path / "state"
    options = {}
    if case == "practice":
        options["dataset"] = practice_dataset(state, tmp_path)
    if case == "not_exportable":
        options["export_allowed"] = False
    found = real_scenario(state, tmp_path, monkeypatch, **options)
    request, settings, profile = {}, dict(TOKEN_SETTINGS), found.profile
    expected = {"no_uri": ("tracking_required", "/tracking", wp.TRACKING_REQUIRED),
                "token_http": ("tracking_insecure", "/tracking", auth.INSECURE_TRACKING),
                "token_http_artifacts": ("tracking_insecure", "/tracking", auth.INSECURE_TRACKING),
                "oidc_incomplete": ("tracking_incomplete", "/tracking", auth.OIDC_CLIENT_MISSING),
                "stale": ("observation_stale", "/profiles/pod-a", wp.OBSERVATION_STALE),
                "fields_changed": ("observation_revision_differs", "/profiles/pod-a", None),
                "estimate": ("estimate_real", "/estimates/0", None),
                "practice": ("practice_data", "/graph/nodes/0/dataset_binding", desktop.PRACTICE_SUBMIT_REFUSAL),
                "not_exportable": ("export_not_allowed", "/graph/nodes/0/dataset_binding", None),
                "none_http": None}[case]
    clock = NOW + 60
    if case == "no_uri":
        del settings["MLFLOW_TRACKING_URI"]
    elif case == "token_http":
        settings["MLFLOW_TRACKING_URI"] = "http://mlflow.invalid"
    elif case == "token_http_artifacts":
        settings["FACTORY_MLFLOW_ARTIFACT_LOCATION"] = "http://artifacts.invalid/mlflow"
    elif case == "oidc_incomplete":
        settings = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": "oidc",
                    "MLFLOW_OIDC_TOKEN_URL": "https://idp.invalid/token", "MLFLOW_OIDC_CLIENT_SECRET_saved": True}
    elif case == "stale":
        clock = NOW + wp.OBSERVATION_MAX_AGE_SECONDS + 1
    elif case == "fields_changed":
        profile = {**profile, "fields": {**profile["fields"], "name": "Renamed"}}
    elif case == "estimate":
        request["estimates"] = [ESTIMATE]
    elif case == "none_http":
        settings = {"MLFLOW_TRACKING_URI": "http://127.0.0.1:5000", "auth_mode": "none",
                    "MLFLOW_TRACKING_TOKEN_saved": True}
    result = real_plan(state, found, settings=settings, clock=clock, profile=profile, **request)
    if expected is None:
        assert result["status"] == "ok", result
        plan = wp.stored_plan(state, result["data"]["plan_sha256"])
        assert result["data"]["tracking"]["settings"]["auth_mode"] == "none"
        assert plan.execution_choices.tracking_destination is not None
        return
    code, path, message = expected
    issue = desk.only_issue(result, "invalid", code, path)
    if message is not None:
        assert issue["message"] == message
    assert files(state / "workflows" / "plans") == []
    if case == "stale":   # exactly the age limit is still fresh
        assert real_plan(state, found, clock=NOW + wp.OBSERVATION_MAX_AGE_SECONDS)["status"] == "ok"


# 12
def test_simulation_estimates_are_validated(tmp_path, monkeypatch):
    wp = planning()
    from tuning_factory.executor_profiles import ResourceFacts
    state = tmp_path / "state"
    found = scenario(state, tmp_path, monkeypatch)
    for estimate, code, path in (({**ESTIMATE, "profile_id": "a100"}, "invalid_value", "/estimates/0/profile_id"),
                                 ({**ESTIMATE, "total_hourly_cost_usd": 0}, "invalid_value",
                                  "/estimates/0/total_hourly_cost_usd"),
                                 ({**ESTIMATE, "total_hourly_cost_usd": -1}, "invalid_value",
                                  "/estimates/0/total_hourly_cost_usd")):
        desk.only_issue(run(state, "graph_plan", plan_request(found.project_id, estimates=[estimate])), "invalid",
                        code, path)
    native = {**runpod_profile(tmp_path, "estimate-a")}
    collision = run(state, "graph_plan", plan_request(found.project_id), profiles=[native])
    desk.only_issue(collision, "invalid", "estimate_collision", "/estimates/0/profile_id")
    duplicate = run(state, "graph_plan", plan_request(found.project_id, estimates=[ESTIMATE, ESTIMATE]))
    desk.only_issue(duplicate, "invalid", "estimate_duplicate", "/estimates/1/profile_id")
    result = run(state, "graph_plan", plan_request(found.project_id))
    assert result["status"] == "ok", result
    plan = wp.stored_plan(state, result["data"]["plan_sha256"])
    assert plan.simulation is True and plan.execution_choices.tracking_destination is None
    assert plan.resource_snapshots == (ResourceFacts(pod_id="estimate-a", gpu_type="A100", gpu_count=1,
                                                     total_hourly_cost_usd=2.0,
                                                     observed_at=datetime.fromtimestamp(NOW, tz=timezone.utc)),)
    [executor] = plan.execution_choices.executors
    assert (executor.profile_id, executor.pod_id, executor.transport_settings_sha256) == (
        "estimate-a", "estimate-a", None)
    assert result["data"]["nodes"][0]["allowed"][0]["cost_source"] == "estimate"
    missing = run(state, "graph_plan", plan_request(found.project_id, estimates=()))
    issue = desk.only_issue(missing, "invalid", "executor_profile_missing", "/graph/nodes/0/executor_profile_ids/0")
    assert issue["node_id"] == "train"
    resources = run(state, "graph_plan", plan_request(found.project_id, resources=()))
    assert desk.only_issue(resources, "invalid", "node_resource_missing", "/node_resources")["node_id"] == "train"
    unknown = run(state, "graph_plan", plan_request(found.project_id, resources=(("train", 1800), ("ghost", 600))))
    desk.only_issue(unknown, "invalid", "node_resource_unknown", "/node_resources/1/node_id")


def test_simulation_plans_accept_old_observations_and_report_changed_profiles(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    found = real_scenario(state, tmp_path, monkeypatch)
    old = run(state, "graph_plan", plan_request(found.project_id, estimates=()), profiles=[found.profile],
              clock=NOW + 30 * 24 * 3600)
    assert old["status"] == "ok" and "warnings" not in old["data"], old
    renamed = {**found.profile, "fields": {**found.profile["fields"], "name": "Renamed"}}
    changed = run(state, "graph_plan", plan_request(found.project_id, estimates=()), profiles=[renamed])
    assert changed["status"] == "ok", changed
    [warning] = changed["data"]["warnings"]
    assert (warning["code"], warning["path"]) == ("observation_revision_differs", "/profiles/pod-a")
    plan = wp.stored_plan(state, changed["data"]["plan_sha256"])
    assert plan.execution_choices.executors[0].revision == wp.profile_revision("pod-a", found.profile["fields"])
    missing = run(state, "graph_plan", plan_request(found.project_id, estimates=()),
                  profiles=[runpod_profile(tmp_path)], clock=NOW)
    assert missing["status"] == "ok"
    (state / "workflows" / "observations" / "pod-a.json").unlink()
    desk.only_issue(run(state, "graph_plan", plan_request(found.project_id, estimates=()), profiles=[found.profile]),
                    "invalid", "observation_missing", "/profiles/pod-a")


# 13
def test_nodes_needing_hf_root_target_evidence(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path)
    targeted = save_recipe(state, "targeted", **TARGETED)
    default = save_recipe(state, "default")
    root = snapshot_root(state, monkeypatch)
    datasets = [binding("train", "train", dataset)]
    merge = {"kind": "merge", "id": "merged", "label": "병합", "models": [output("a"), output("b")],
             "weights": [0.5, 0.5], "executor_profile_ids": ["estimate-a"]}
    materialize = {"kind": "materialize", "id": "full", "label": "확정", "model": output("a"),
                   "executor_profile_ids": ["estimate-a"]}
    cases = (([train("parent", targeted, from_root(root)), train("child", targeted, output("parent"))], "child", 1),
             ([train("a", targeted, from_root(root)), train("b", targeted, from_root(root)), merge], "merged", 2),
             ([train("a", targeted, from_root(root)), materialize], "full", 1))
    for nodes, blocked, index in cases:
        project_id, _ = make_project(state, nodes, datasets)
        resources = tuple((node["id"], 1800) for node in nodes)
        result = run(state, "graph_plan", plan_request(project_id, resources=resources))
        issue = desk.only_issue(result, "invalid", "base_targets_missing", f"/graph/nodes/{index}")
        assert issue["node_id"] == blocked and issue["message"] == wp.BASE_TARGETS_MISSING
        desk.only_issue(run(state, "graph_validate", {"project_id": project_id}), "invalid", "base_targets_missing")
    # A real continuation on an HF root is refused the same way.
    profile = runpod_profile(tmp_path)
    assert inspect(state, monkeypatch, profile)[0]["status"] == "ok"
    real_id, _ = make_project(state, [train("parent", targeted, from_root(root), ("pod-a",)),
                                      train("child", targeted, output("parent"), ("pod-a",))], datasets)
    real = run(state, "graph_plan", plan_request(real_id, simulation=False, estimates=(),
                                                 resources=(("parent", 1800), ("child", 1800))),
               settings=TOKEN_SETTINGS, profiles=[profile], clock=NOW + 60)
    assert desk.only_issue(real, "invalid", "base_targets_missing", "/graph/nodes/1")["node_id"] == "child"
    # Simulation of a default (lora_target: all) recipe on an HF root plans, with a warning.
    simple_id, _ = make_project(state, [train("train", default, from_root(root))], datasets)
    warned = run(state, "graph_plan", plan_request(simple_id))
    assert warned["status"] == "ok", warned
    [warning] = warned["data"]["warnings"]
    assert (warning["code"], warning["node_id"], warning["path"]) == (
        "simulated_execution_needs_targets", "train", "/graph/nodes/0")
    full_id, _ = make_project(state, [train("train", save_recipe(state, "full", training_method="full"),
                                            from_root(root, "full_weights"))], datasets)
    assert [item["code"] for item in run(state, "graph_plan", plan_request(full_id))["data"]["warnings"]] == [
        "simulated_execution_needs_targets"]
    real_simple, _ = make_project(state, [train("train", default, from_root(root), ("pod-a",))], datasets)
    plain = run(state, "graph_plan", plan_request(real_simple, simulation=False, estimates=()),
                settings=TOKEN_SETTINGS, profiles=[profile], clock=NOW + 60)
    assert plain["status"] == "ok" and "warnings" not in plain["data"], plain
    targeted_id, _ = make_project(state, [train("train", targeted, from_root(root))], datasets)
    clean = run(state, "graph_plan", plan_request(targeted_id))
    assert clean["status"] == "ok" and "warnings" not in clean["data"], clean


def test_hf_root_target_evidence_from_the_store_lifts_the_refusal(tmp_path, monkeypatch):
    """The seam 9.7 fills: evidence stored beside the root index reaches ExecutionChoices.base_targets."""
    wp = planning()
    from tuning_factory.graph_planner import ModelTargetEvidence, TargetModuleEvidence
    from tuning_factory.model_artifacts import HFRootRef
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path)
    targeted = save_recipe(state, "targeted", **TARGETED)
    default = save_recipe(state, "default")
    root = snapshot_root(state, monkeypatch)
    evidence = ModelTargetEvidence(base=HFRootRef.model_validate(root), modules=tuple(
        TargetModuleEvidence(name=f"model.layers.0.self_attn.{name}", input_dimension=16, output_dimension=16)
        for name in ("q_proj", "k_proj", "v_proj", "o_proj")))
    wp.save_root_targets(state, evidence)
    wp.save_root_targets(state, evidence)    # idempotent
    assert wp.load_root_targets(state, HFRootRef.model_validate(root)) == evidence
    assert wp.load_root_targets(state, HFRootRef.model_validate({**root, "revision": "d" * 40})) is None
    assert run(state, "model_roots_list")["data"]["roots"][0]["targets_ready"] is True
    project_id, _ = make_project(state, [train("parent", targeted, from_root(root)),
                                         train("child", targeted, output("parent")),
                                         train("other", default, from_root(root))],
                                 [binding("train", "train", dataset)])
    result = run(state, "graph_plan", plan_request(project_id, resources=(("parent", 600), ("child", 600),
                                                                          ("other", 600))))
    assert result["status"] == "ok" and "warnings" not in result["data"], result
    assert wp.stored_plan(state, result["data"]["plan_sha256"]).execution_choices.base_targets == (evidence,)
    other = ModelTargetEvidence(base=HFRootRef.model_validate({**root, "revision": "d" * 40}),
                                modules=evidence.modules)
    path = wp.root_targets_path(state, HFRootRef.model_validate(root))
    path.write_bytes(canonical(other.model_dump(mode="json")))
    refused = run(state, "graph_plan", plan_request(project_id, resources=(("parent", 600), ("child", 600),
                                                                           ("other", 600))))
    assert "root_targets_invalid" in codes(refused)


# 14
def test_external_artifact_inputs_are_reported_unsupported(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    found = scenario(state, tmp_path, monkeypatch)
    artifact = {"artifact_id": "1" * 64, "inventory_sha256": "2" * 64}
    nodes = [train("external", found.recipe, from_root(artifact)),
             train("continued", found.recipe, {"policy": {"base": found.root, "adapter": artifact},
                                               "initialization": "continue_adapter"}),
             train("inside", found.recipe, from_root(found.root)),
             {"kind": "merge", "id": "merged", "label": "병합", "models": [output("inside"), artifact],
              "weights": [0.5, 0.5], "executor_profile_ids": ["estimate-a"]},
             {"kind": "materialize", "id": "full", "label": "확정", "model": artifact,
              "executor_profile_ids": ["estimate-a"]}]
    project_id, _ = make_project(state, nodes, [binding("train", "train", found.dataset)])
    resources = tuple((node["id"], 600) for node in nodes)
    for result in (run(state, "graph_validate", {"project_id": project_id}),
                   run(state, "graph_plan", plan_request(project_id, resources=resources))):
        assert result["status"] == "invalid"
        external = [(issue["node_id"], issue["path"]) for issue in result["issues"]
                    if issue["code"] == "external_artifact_unsupported"]
        assert external == [("external", "/graph/nodes/0"), ("continued", "/graph/nodes/1"),
                            ("merged", "/graph/nodes/3"), ("full", "/graph/nodes/4")]


# 15
def test_recipe_lookup_by_revision_is_unique_and_verified(tmp_path):
    from tuning_factory.recipes import RecipeSpec, RecipeStore
    store = RecipeStore(tmp_path)
    first = store.save(RecipeSpec(recipe_id="a", name="A"))
    second = store.save(RecipeSpec(recipe_id="b", name="B"))
    edited = store.save(RecipeSpec(recipe_id="a", name="A2"), first.revision_sha256)
    if not hasattr(store, "get_by_revision"):
        pytest.fail("projects._RevisionStore.get_by_revision (Task 9.2) is missing")
    assert store.get_by_revision(first.revision_sha256) == first
    assert store.get_by_revision(second.revision_sha256) == second
    assert store.get_by_revision(edited.revision_sha256) == edited
    with pytest.raises(ValueError, match="unknown definition revision"):
        store.get_by_revision("0" * 64)
    with pytest.raises(ValueError, match="invalid revision hash"):
        store.get_by_revision("../" + "0" * 61)
    from tuning_factory.projects import ProjectStore
    assert ProjectStore(tmp_path).list() == []
    with pytest.raises(ValueError, match="unknown definition revision"):
        ProjectStore(tmp_path).get_by_revision(first.revision_sha256)   # namespaces never mix
    with sqlite3.connect(store.path) as db:
        db.execute("UPDATE definition_revisions SET body=? WHERE revision_sha256=?",
                   (canonical(RecipeSpec(recipe_id="a", name="Tampered").model_dump(mode="json")),
                    first.revision_sha256))
    with pytest.raises(ValueError, match="definition revision hash mismatch"):
        store.get_by_revision(first.revision_sha256)


# 16
def test_planning_reads_the_lock_from_the_frozen_bundle(tmp_path, monkeypatch):
    from tuning_factory import graph_planner
    source = {profile: digest((ROOT / lock).read_bytes()) for profile, lock in (
        ("primary", "containers/gpu-requirements.lock"), ("ppo", "containers/gpu-ppo-requirements.lock"))}
    assert graph_planner._runtime("primary").dependency_sha256 == source["primary"]
    bundle = tmp_path / "bundle"
    locks = bundle / "assets" / "containers"
    locks.mkdir(parents=True)
    (locks / "gpu-requirements.lock").write_bytes(b"frozen primary lock 1\n")
    (locks / "gpu-ppo-requirements.lock").write_bytes(b"frozen ppo lock\n")
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "_MEIPASS", str(bundle), raising=False)
    assert graph_planner._runtime("primary").dependency_sha256 == digest(b"frozen primary lock 1\n")
    assert graph_planner._runtime("ppo").dependency_sha256 == digest(b"frozen ppo lock\n")
    (locks / "gpu-requirements.lock").write_bytes(b"frozen primary lock 2\n")
    assert graph_planner._runtime("primary").dependency_sha256 == digest(b"frozen primary lock 2\n")
    monkeypatch.delattr(sys, "_MEIPASS")
    monkeypatch.delattr(sys, "frozen")
    assert graph_planner._runtime("primary").dependency_sha256 == source["primary"]
    assert graph_planner._runtime("ppo").dependency_sha256 == source["ppo"]


def current_auth_mode():
    """The controller's view, wherever it lives (8.5 moves it from ``tracking`` into ``auth``)."""
    from tuning_factory import auth, tracking
    return getattr(auth, "current_auth_mode", None) or tracking.current_auth_mode


MLFLOW_CREDENTIALS = ("MLFLOW_OIDC_TOKEN_URL", "MLFLOW_OIDC_CLIENT_ID", "MLFLOW_OIDC_CLIENT_SECRET",
                      "MLFLOW_TRACKING_TOKEN")


def filtered_environment(mode, url, token_saved, secret_saved):
    """What the controller sees: main.rs `filter_mlflow_auth` withholds per auth_mode (main.rs:56-66)."""
    values = {"MLFLOW_OIDC_TOKEN_URL": url, "MLFLOW_OIDC_CLIENT_ID": "client-id",
              "MLFLOW_OIDC_CLIENT_SECRET": MLFLOW_SECRET if secret_saved else "",
              "MLFLOW_TRACKING_TOKEN": MLFLOW_SECRET if token_saved else ""}
    withheld = {"token": MLFLOW_CREDENTIALS[:3], "none": MLFLOW_CREDENTIALS}.get(mode, MLFLOW_CREDENTIALS[3:])
    return {name: value for name, value in values.items() if name not in withheld and value}


# 17
@pytest.mark.parametrize("mode", ["", "oidc", "token", "none"])
def test_public_auth_mode_follows_saved_flags(monkeypatch, mode):
    wp = planning()
    expected = {("", "url", False, True): "oidc", ("oidc", "url", True, True): "oidc",
                ("oidc", "", False, True): "none", ("", "url", True, False): "none",
                ("token", "url", True, True): "token", ("token", "", False, True): "none",
                ("none", "url", True, True): "none"}
    for url in ("", "https://idp.invalid/token"):
        for token_saved in (False, True):
            for secret_saved in (False, True):
                public = {"MLFLOW_TRACKING_URI": "https://mlflow.invalid", "auth_mode": mode,
                          "MLFLOW_OIDC_TOKEN_URL": url, "MLFLOW_OIDC_CLIENT_ID": "client-id",
                          "MLFLOW_TRACKING_TOKEN_saved": token_saved, "MLFLOW_OIDC_CLIENT_SECRET_saved": secret_saved}
                for name in MLFLOW_CREDENTIALS:
                    monkeypatch.delenv(name, raising=False)
                for name, value in filtered_environment(mode, url, token_saved, secret_saved).items():
                    monkeypatch.setenv(name, value)
                assert wp.public_auth_mode(public) == current_auth_mode()(), (mode, url, token_saved, secret_saved)
                key = (mode, "url" if url else "", token_saved, secret_saved)
                if key in expected:
                    assert wp.public_auth_mode(public) == expected[key], key
    assert wp.public_auth_mode({}) == "none"
    assert wp.public_auth_mode({"auth_mode": "token", "MLFLOW_TRACKING_TOKEN_saved": "true"}) == "none"


# -- additional contracts -------------------------------------------------------------------------

def minimal_bodies():
    return {"executor_profile_inspect": {"profile_id": "pod-a"},
            "model_root_snapshot": {"repo_id": REPO, "revision": COMMIT, "download_approved": True},
            "model_roots_list": {}, "benchmarks_list": {},
            "benchmark_save": {"spec": {"dataset": {"dataset_id": "d", "version": "1", "sha256": "0" * 64},
                                        "evaluator_kind": "classification", "evaluator_version": "1",
                                        "ordered_sample_ids": ["row:0"], "max_new_tokens": 8, "cutoff_len": 64,
                                        "seed": 1, "precision": "bf16", "quantization_bit": None,
                                        "tokenizer_fingerprint": "f" * 64, "template": "qwen",
                                        "semantic_model_kind": "causal_lm"}},
            "graph_validate": {"project_id": "p"}, "graph_plan": {"project_id": "p"},
            "graph_plan_get": {"plan_sha256": "0" * 64}}


@pytest.mark.parametrize("action", ACTIONS_92)
def test_planning_requests_refuse_extra_fields(tmp_path, monkeypatch, action):
    planning()
    state = tmp_path / "state"
    hub = FakeHub().install(monkeypatch)
    fake = FakeRunPod().install(monkeypatch)
    secrets = {name: SECRET for name in desk.workflows().SECRET_SCOPES.get(action, ())}
    result = run(state, action, {**minimal_bodies()[action], "command": "calc.exe"}, secrets=secrets,
                 profiles=[runpod_profile(tmp_path)])
    issue = desk.only_issue(result, "invalid", "extra_field", "")
    assert issue["node_id"] is None and "calc.exe" not in json.dumps(result)
    assert hub.requests == [] and fake.calls == [] and not state.exists()


def test_stored_plans_are_verified_and_summaries_are_public(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    found = scenario(state, tmp_path, monkeypatch)
    result = run(state, "graph_plan", plan_request(found.project_id))
    assert result["status"] == "ok", result
    summary = result["data"]
    sha = summary["plan_sha256"]
    plan = wp.stored_plan(state, sha)
    assert plan.sha256 == sha and wp.plan_summary(plan) == summary
    assert set(summary) == {"plan_sha256", "simulation", "project_id", "project_revision", "max_estimated_cost_usd",
                            "max_duration_seconds", "max_concurrent_gpu_count", "retry_policy", "nodes",
                            "datasets", "tracking"}
    assert (summary["project_id"], summary["project_revision"], summary["simulation"]) == (
        found.project_id, found.revision, True)
    assert summary["retry_policy"] == {"max_attempts": 2, "backoff_seconds": 0}
    assert summary["max_estimated_cost_usd"] == pytest.approx(2.0) and summary["max_duration_seconds"] == 3600
    assert summary["tracking"] is None and summary["max_concurrent_gpu_count"] == 1
    assert summary["nodes"][0] | {"allowed": None} == {"node_id": "train", "kind": "train", "label": "train 학습",
                                                       "max_estimated_cost_usd": pytest.approx(2.0),
                                                       "max_duration_seconds": 3600, "allowed": None,
                                                       # 9.8: the node's recipe and data references
                                                       "recipe": {"recipe_id": "sft-lora", "version": "1",
                                                                  "name": "SFT LoRA", "training_stage": "sft",
                                                                  "training_method": "lora",
                                                                  "revision_sha256": found.recipe},
                                                       "datasets": [{"binding_id": "train", "benchmark_revision": None,
                                                                     **found.dataset}]}
    desk.only_issue(run(state, "graph_plan_get", {"plan_sha256": "0" * 64}), "invalid", "plan_unknown",
                    "/plan_sha256")
    with pytest.raises(ValueError) as refused:
        wp.stored_plan(state, "../" + sha[3:])
    assert str(refused.value) == wp.PLAN_UNKNOWN
    path = state / "workflows" / "plans" / f"{sha}.json"
    path.write_bytes(path.read_bytes().replace(b'"max_attempts":2', b'"max_attempts":1'))
    desk.only_issue(run(state, "graph_plan_get", {"plan_sha256": sha}), "invalid", "plan_invalid", "/plan_sha256")
    with pytest.raises(ValueError, match="plan hash mismatch"):
        wp.stored_plan(state, sha)
    # The same inputs and clock give the same plan hash: rewriting it over the tampered bytes is refused.
    rewrite = desk.only_issue(run(state, "graph_plan", plan_request(found.project_id)), "invalid", "plan_invalid")
    assert rewrite["message"] == wp.PLAN_STORED_DIFFERS


def test_synthetic_secrets_never_reach_planning_files_responses_or_environment(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    environment = dict(os.environ)
    refuse_ssh(monkeypatch)
    found = real_scenario(state, tmp_path, monkeypatch)
    responses = []
    leaky, fake = inspect(state, monkeypatch, found.profile, error=ValueError("bad key " + SECRET))
    responses.append(leaky)
    assert desk.only_issue(leaky, "invalid", "inspection_failed")["message"] == "bad key [redacted]"
    assert fake.keys == [SECRET]
    crashed, _ = inspect(state, monkeypatch, found.profile, error=ConnectionError("send " + SECRET))
    responses.append(crashed)
    assert desk.only_issue(crashed, "invalid", "inspection_failed")["message"] == "ConnectionError"
    hub = FakeHub(files={}).install(monkeypatch)
    denied = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": "e" * 40, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    responses.append(denied)
    assert denied["status"] == "invalid" and hub.requests
    assert all(request.headers["authorization"] == "Bearer " + HF_SECRET for request in hub.requests)
    settings = {**TOKEN_SETTINGS, "MLFLOW_OIDC_CLIENT_SECRET_saved": True, "RUNPOD_API_KEY_saved": True,
                "HF_TOKEN_saved": True}
    responses.append(real_plan(state, found, settings=settings))
    assert responses[-1]["status"] == "ok", responses[-1]
    text = json.dumps(responses)
    assert SECRET not in text and HF_SECRET not in text
    stored = state_bytes(state)
    assert SECRET.encode() not in stored and HF_SECRET.encode() not in stored
    assert dict(os.environ) == environment


def test_planning_module_imports_no_heavy_or_keyring_modules(tmp_path):
    code = textwrap.dedent("""
        import json, sys
        import tuning_factory.workflow_planning
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring", "paramiko",
                 "mlflow")
        print(json.dumps(sorted(name for name in heavy if name in sys.modules)))
    """)
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == []


def test_observation_store_is_public_bounded_and_atomic(tmp_path):
    wp = planning()
    from test_gpu_servers import FakeServer

    from tuning_factory.executor_profiles import runpod_facts
    from tuning_factory.gpu_servers import server_settings
    state = tmp_path / "state"
    observed = datetime.fromtimestamp(NOW, tz=timezone.utc)
    fields = runpod_profile(tmp_path)["fields"]
    public = wp.public_profile("pod-a", fields)
    facts = runpod_facts(LIVE_POD, observed_at=observed)
    wp.save_observation(state, public, facts, revision=public.revision, observed_at=observed)
    later = observed + timedelta(minutes=5)
    wp.save_observation(state, public, facts, revision=public.revision, observed_at=later)
    assert wp.load_observation(state, "pod-a")["observed_at"] == later
    assert files(state / "workflows" / "observations") == ["pod-a.json"]
    with pytest.raises(ValueError):
        wp.save_observation(state, public, facts.model_copy(update={"pod_id": "pod9"}), revision=public.revision,
                            observed_at=observed)
    with pytest.raises(ValueError):
        wp.save_observation(state, public, facts, revision="0" * 64, observed_at=observed)
    native = server_profile()["fields"]
    server_facts = FakeServer(tmp_path / "remote").facts(server_settings(native))
    with pytest.raises(ValueError):
        wp.save_observation(state, public, server_facts, revision=public.revision, observed_at=observed)
    for reserved in ("CON", "nul", "com1"):
        with pytest.raises(ValueError):
            wp.load_observation(state, reserved)
    # Profile IDs compare by casefold plus rstrip(".") (9.1 M9): such IDs share a file only to be refused.
    other = wp.public_profile("Pod-A.", fields)
    with pytest.raises(ValueError):
        wp.save_observation(state, other, facts, revision=other.revision, observed_at=observed)
    for alias in ("Pod-A.", "POD-A", "pod-a.."):
        with pytest.raises(ValueError):
            wp.load_observation(state, alias)
    assert files(state / "workflows" / "observations") == ["pod-a.json"]
    assert wp.load_observation(state, "pod-a")["profile"] == public
    path = state / "workflows" / "observations" / "pod-a.json"
    path.write_bytes(path.read_bytes().replace(b'"pod1"', b'"pod2"'))
    with pytest.raises(ValueError):
        wp.load_observation(state, "pod-a")


# -- 9.2 review follow-ups (Minor 1, 2, 3, 5, 6 and two nits) ---------------------------------------

def test_d15_walks_through_selections_merges_and_materialize(tmp_path, monkeypatch):
    """Minor 1: each walker branch decides whether a child needs HF-root evidence."""
    planning()
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path)
    heldout = register_data(state, tmp_path, "heldout")
    targeted = save_recipe(state, "targeted", **TARGETED)
    root = snapshot_root(state, monkeypatch)
    spec = {**minimal_bodies()["benchmark_save"]["spec"], "dataset": heldout,
            "tokenizer_fingerprint": root["tokenizer_fingerprint"]}
    saved = run(state, "benchmark_save", {"spec": spec})
    assert saved["status"] == "ok", saved
    datasets = [binding("train", "train", dataset), binding("held", "validation", heldout)]

    def evaluate(node_id, model):
        return {"kind": "evaluate", "id": node_id, "label": node_id, "model": output(model),
                "benchmark_revision": saved["data"]["revision_sha256"], "executor_profile_ids": ["estimate-a"]}

    adapters = [train("a", targeted, from_root(root)), train("b", targeted, from_root(root))]
    select = {"kind": "select", "id": "pick", "label": "선택",
              "selection": {"metric": "accuracy", "direction": "max"},
              "evaluations": [{"node_id": "ea", "output_name": "evaluation"},
                              {"node_id": "eb", "output_name": "evaluation"}]}
    merge = {"kind": "merge", "id": "merged", "label": "병합", "models": [output("a"), output("b")],
             "weights": [0.5, 0.5], "executor_profile_ids": ["estimate-a"]}
    materialize = {"kind": "materialize", "id": "full", "label": "확정", "model": output("a"),
                   "executor_profile_ids": ["estimate-a"]}
    cases = {   # continuation through a selection of HF-root adapters, from a merge output, after materialize
        "selection": ([*adapters, evaluate("ea", "a"), evaluate("eb", "b"), select,
                       train("child", targeted, output("pick"))], {"child"}),
        "merge": ([*adapters, merge, train("child", targeted, output("merged"))], {"merged", "child"}),
        "materialize": ([adapters[0], materialize, train("child", targeted, output("full"))], {"full"}),
    }
    for name, (nodes, blocked) in cases.items():
        project_id, _ = make_project(state, nodes, datasets)
        result = run(state, "graph_validate", {"project_id": project_id})
        found = {issue["node_id"] for issue in result["issues"] if issue["code"] == "base_targets_missing"}
        assert found == blocked, (name, result)


def test_real_plan_refuses_an_observation_from_the_future(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    found = real_scenario(state, tmp_path, monkeypatch)
    desk.only_issue(real_plan(state, found, clock=NOW - 1), "invalid", "observation_stale", "/profiles/pod-a")
    assert real_plan(state, found, clock=NOW)["status"] == "ok"


@pytest.mark.parametrize("repo", ["CON/model", "Qwen/model.", "aux.cfg/model", "Qwen/NUL.x"])
def test_model_root_snapshot_refuses_repository_names_windows_would_alter(tmp_path, monkeypatch, repo):
    wp = planning()
    state = tmp_path / "state"
    hub = FakeHub().install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": repo, "revision": COMMIT, "download_approved": True})
    issue = desk.only_issue(result, "invalid", "repository_unsupported", "/repo_id")
    assert issue["message"] == wp.REPOSITORY_UNSUPPORTED
    assert hub.requests == [] and not state.exists()


def test_model_root_snapshot_reuses_an_existing_directory_only_if_it_is_this_root(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    directory = wp.root_snapshots_dir(state) / "Qwen" / "Qwen2.5-0.5B-Instruct" / COMMIT
    directory.mkdir(parents=True)
    (directory / "config.json").write_bytes(canonical({"model_type": "qwen2", "num_hidden_layers": 2}))
    before = files(directory)
    FakeHub().install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True})
    desk.only_issue(result, "invalid", "root_snapshot_conflict", "/repo_id")
    assert files(directory) == before and not (state / "workflows" / "root-index").exists()
    assert [path for path in files(wp.root_snapshots_dir(state)) if path.startswith(".")] == []


def test_estimate_collision_compares_casefolded_profile_ids(tmp_path, monkeypatch):
    planning()
    state = tmp_path / "state"
    found = scenario(state, tmp_path, monkeypatch)
    for native_id in ("ESTIMATE-A", "Estimate-a."):
        collision = run(state, "graph_plan", plan_request(found.project_id),
                        profiles=[runpod_profile(tmp_path, native_id)])
        desk.only_issue(collision, "invalid", "estimate_collision", "/estimates/0/profile_id")


def test_model_root_snapshot_refuses_tokenizer_names_that_collide_by_case(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    hub = FakeHub(files={"config.json": CONFIG, **TOKENIZER, "chat_templates/Tool_Use.jinja": b"{{ other }}"})
    hub.install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True})
    desk.only_issue(result, "invalid", "snapshot_refused", "/repo_id")
    assert not any("chat_templates" in path for path in hub.paths)
    assert files(wp.root_snapshots_dir(state)) == []


@pytest.mark.parametrize("key", ["keys/pod-key", "pod-key", "./pod-key", "../keys/pod-key"])
def test_relative_key_paths_are_refused_before_hashing(tmp_path, monkeypatch, key):
    """Minor 2: a relative key path would hash against the sidecar's working directory, so the controller
    would refuse the pinned transport forever. ``~`` expands per user and stays allowed."""
    wp = planning()
    from test_gpu_servers import FakeServer

    from tuning_factory import desktop_runtime
    from tuning_factory.gpu_servers import server_settings
    state = tmp_path / "state"
    runpod = runpod_profile(tmp_path, ssh_key_file=key)
    with pytest.raises(ValueError) as refused:
        wp.public_profile("pod-a", runpod["fields"])
    assert str(refused.value) == wp.KEY_PATH_RELATIVE
    native = server_profile()["fields"]
    facts = FakeServer(tmp_path / "remote").facts(server_settings(native))
    with pytest.raises(ValueError) as refused:
        wp.public_profile("lab", {**native, "server_ssh_key_file": key}, facts)
    assert str(refused.value) == wp.KEY_PATH_RELATIVE
    assert wp.public_profile("pod-a", {**runpod["fields"], "ssh_key_file": "~/.ssh/pod-key"}).revision
    inspected, fake = inspect(state, monkeypatch, runpod)
    issue = desk.only_issue(inspected, "invalid", "executor_profile_invalid", "/profile_id")
    assert issue["message"] == wp.KEY_PATH_RELATIVE and fake.calls == []
    monkeypatch.setattr(desktop_runtime, "connection", lambda *a, **kw: pytest.fail("no SSH for a relative key"))
    result = run(state, "executor_profile_inspect", {"profile_id": "lab"},
                 profiles=[server_profile(server_ssh_key_file=key)])
    desk.only_issue(result, "invalid", "executor_profile_invalid", "/profile_id")
    # Planning with such a profile names the profile, not a stale or changed observation.
    found = scenario(state, tmp_path, monkeypatch, profiles=("pod-a",))
    planned = run(state, "graph_plan", plan_request(found.project_id, estimates=()), profiles=[runpod])
    assert ("executor_profile_invalid", "/profiles/pod-a") in [(item["code"], item["path"])
                                                             for item in planned["issues"]], planned


class KeyErrorHub(FakeHub):
    """A config fetch that fails with a KeyError (``config=None`` below)."""

    def respond(self, request):
        if unquote(request.url.path).endswith("/config.json"):
            raise KeyError("model_type")
        return super().respond(request)


@pytest.mark.parametrize("config, error", [
    (canonical({"model_type": "qwen2", "max_position_embeddings": "big"}), "TypeError"),
    (canonical([{"model_type": "qwen2"}]), "AttributeError"),
    (None, "KeyError")])
def test_snapshot_errors_stay_in_the_issue_envelope(tmp_path, monkeypatch, config, error):
    """Minor 3: unexpected metadata shapes are issues with the type name, never an escaping exception."""
    wp = planning()
    state = tmp_path / "state"
    if config is None:
        KeyErrorHub().install(monkeypatch)
    else:
        FakeHub(files={"config.json": config, **TOKENIZER}).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    assert desk.only_issue(result, "invalid", "snapshot_failed", "/repo_id")["message"] == error
    assert files(wp.root_snapshots_dir(state)) == [] and HF_SECRET not in json.dumps(result)


@pytest.mark.parametrize("key", ["\\\\server\\share\\pod-key", "//server/share/pod-key",
                                 "\\\\?\\C:\\keys\\pod-key", "\\\\.\\pipe\\pod-key"])
def test_network_and_device_key_paths_are_refused(tmp_path, monkeypatch, key):
    """9.7 review Minor 7: UNC and device key paths are network or device access (D12), refused like relative ones."""
    wp = planning()
    state = tmp_path / "state"
    runpod = runpod_profile(tmp_path, ssh_key_file=key)
    with pytest.raises(ValueError) as refused:
        wp.check_key_paths(runpod["fields"])
    assert str(refused.value) == wp.KEY_PATH_RELATIVE
    inspected, fake = inspect(state, monkeypatch, runpod)
    issue = desk.only_issue(inspected, "invalid", "executor_profile_invalid", "/profile_id")
    assert issue["message"] == wp.KEY_PATH_RELATIVE and fake.calls == []
    with pytest.raises(ValueError):
        wp.check_key_paths(server_profile(server_ssh_key_file=key)["fields"])


def test_an_unexpandable_home_in_a_key_path_is_an_issue(tmp_path, monkeypatch):
    """9.7 review Minor 7: ``~`` without a resolvable home raised RuntimeError out of ``executor_profile_inspect``."""
    import pathlib
    wp = planning()
    state = tmp_path / "state"
    runpod = runpod_profile(tmp_path, ssh_key_file="~/.ssh/pod-key")

    def no_home(self):
        raise RuntimeError("Could not determine home directory.")

    monkeypatch.setattr(pathlib.Path, "expanduser", no_home)
    with pytest.raises(ValueError) as refused:
        wp.public_profile("pod-a", runpod["fields"])
    assert str(refused.value) == wp.KEY_PATH_RELATIVE
    inspected, fake = inspect(state, monkeypatch, runpod)
    issue = desk.only_issue(inspected, "invalid", "executor_profile_invalid", "/profile_id")
    assert issue["message"] == wp.KEY_PATH_RELATIVE and fake.calls == []


@pytest.mark.parametrize("change", [{"semantic_model_kind": "reward_model"}, {"generation_mode": "sample"},
                                    {"precision": "fp32"}])
def test_benchmark_save_refuses_what_the_worker_cannot_run(tmp_path, change):
    """Minor 5: ``check_generation_benchmark`` at save time, with the worker's exact message."""
    planning()
    from tuning_factory.benchmarks import BenchmarkSpec
    from tuning_factory.training import check_generation_benchmark
    state = tmp_path / "state"
    dataset = register_data(state, tmp_path, "heldout")
    spec = {**minimal_bodies()["benchmark_save"]["spec"], "dataset": dataset, **change}
    with pytest.raises(ValueError) as worker:
        check_generation_benchmark(BenchmarkSpec.model_validate(spec))
    result = run(state, "benchmark_save", {"spec": spec})
    assert desk.only_issue(result, "invalid", "benchmark_unsupported", "/spec")["message"] == str(worker.value)
    assert files(state / "workflows" / "benchmarks") == []


def test_remote_command_output_never_reaches_inspection_issues(tmp_path, monkeypatch):
    """Minor 6: a failed remote probe is a fixed text and code, never the remote output."""
    wp = planning()
    from contextlib import contextmanager

    from tuning_factory import desktop_runtime
    state = tmp_path / "state"
    remote = "remote output " + "x" * 64 + " /home/researcher/private"

    @contextmanager
    def failing(*args, **kwargs):
        raise desktop_runtime.RemoteCommandFailed("GPU runtime command failed. " + remote)
        yield

    monkeypatch.setattr(desktop_runtime, "connection", failing)
    result = run(state, "executor_profile_inspect", {"profile_id": "lab"}, profiles=[server_profile()])
    issue = desk.only_issue(result, "invalid", "remote_command_failed")
    assert issue["message"] == wp.REMOTE_COMMAND_FAILED and "remote output" not in json.dumps(result)
    assert not (state / "workflows" / "observations").exists()


class RedirectingHub(FakeHub):
    """Redirects one tokenizer file to plain HTTP (an https to http downgrade)."""

    def respond(self, request):
        if unquote(request.url.path).endswith("/tokenizer.json") and request.url.host == "huggingface.co":
            self.requests.append(request)
            return httpx.Response(302, headers={"Location": "http://cdn.hf.example/tokenizer.json"}, content=b"x")
        return super().respond(request)


def test_model_root_snapshot_refuses_plain_http_redirects(tmp_path, monkeypatch):
    wp = planning()
    state = tmp_path / "state"
    hub = RedirectingHub().install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    assert desk.only_issue(result, "invalid", "insecure_redirect", "/repo_id")["message"] == wp.INSECURE_REDIRECT
    assert all(request.url.scheme == "https" for request in hub.requests)
    assert files(wp.root_snapshots_dir(state)) == [] and not (state / "workflows" / "root-index").exists()


@pytest.mark.parametrize("fails", [False, True])
def test_runpod_inspection_closes_its_http_client(tmp_path, monkeypatch, fails):
    planning()
    from tuning_factory import execution
    state = tmp_path / "state"
    closed = []

    class Client:
        def close(self):
            closed.append(True)

    class Controller:
        def __init__(self, api_key, client=None):
            self.client = Client()

        def status(self, pod_id):
            if fails:
                raise ConnectionError("unreachable")
            return dict(LIVE_POD)

    monkeypatch.setattr(execution, "RunPodController", Controller)
    refuse_ssh(monkeypatch)
    result = run(state, "executor_profile_inspect", {"profile_id": "pod-a"}, profiles=[runpod_profile(tmp_path)],
                 secrets={"RUNPOD_API_KEY": SECRET})
    assert result["status"] == ("invalid" if fails else "ok") and closed == [True]


# -- Task 11.5 (R2 M3): model_root_snapshot reads through hf_targets' bounded _open/_read -----------------------

MIB = 1024 * 1024
CDN = "https://cdn-lfs.hf.example"


class BoundedHub(FakeHub):
    """A tokenizer file that redirects to a CDN origin (or to itself, ``loop``) with an undeclared, endless redirect
    body, and optionally revision info whose undeclared body never ends (``huge_info``). ``pulled`` counts every
    body byte of those streams the client takes."""

    def __init__(self, *, loop=False, huge_info=False, **options):
        super().__init__(**options)
        self.loop, self.huge_info, self.pulled = loop, huge_info, 0

    def endless(self, total=64 * MIB):
        def chunks():
            while self.pulled < total:
                self.pulled += MIB
                yield bytes(MIB)
        return chunks()

    def respond(self, request):
        path = unquote(request.url.path)
        if request.url.host == "huggingface.co" and path == f"/api/models/{self.repo}/revision/{self.commit}" \
                and self.huge_info:
            self.requests.append(request)
            return httpx.Response(200, content=self.endless())
        if request.url.host == "huggingface.co" and path.endswith("/resolve/" + self.commit + "/tokenizer.json"):
            self.requests.append(request)
            location = str(request.url) if self.loop else CDN + path + "?signature=1"
            return httpx.Response(302, headers={"Location": location}, content=self.endless())
        return super().respond(request)


def test_model_root_snapshot_follows_redirects_without_reading_their_bodies(tmp_path, monkeypatch):
    """Every hop stays on HTTPS, the token never leaves huggingface.co, and a redirect body is never read."""
    wp = planning()
    state = tmp_path / "state"
    hub = BoundedHub().install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    assert result["status"] == "ok", result
    cdn = [item for item in hub.requests if item.url.host == "cdn-lfs.hf.example"]
    origin = [item for item in hub.requests if item.url.host == "huggingface.co"]
    assert [unquote(item.url.path) for item in cdn] == [f"/{REPO}/resolve/{COMMIT}/tokenizer.json"]
    assert not any("authorization" in item.headers for item in cdn)
    assert all(item.headers.get("authorization") == "Bearer " + HF_SECRET for item in origin)
    assert all(item.url.scheme == "https" for item in hub.requests)
    assert hub.pulled == 0
    directory = wp.root_snapshots_dir(state) / "Qwen" / "Qwen2.5-0.5B-Instruct" / COMMIT
    assert (directory / "tokenizer.json").read_bytes() == TOKENIZER["tokenizer.json"]


def test_model_root_snapshot_stops_a_redirect_loop_unread(tmp_path, monkeypatch):
    wp = planning()
    from tuning_factory import hf_targets as ht
    state = tmp_path / "state"
    hub = BoundedHub(loop=True).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True},
                 secrets={"HF_TOKEN": HF_SECRET})
    issue = desk.only_issue(result, "invalid", "snapshot_failed", "/repo_id")
    assert issue["message"] == ht.TOO_MANY_REDIRECTS
    loops = [item for item in hub.requests if unquote(item.url.path).endswith("/tokenizer.json")]
    assert len(loops) == ht.MAX_REDIRECTS + 1 and hub.pulled == 0
    assert files(wp.root_snapshots_dir(state)) == [] and not (state / "workflows" / "root-index").exists()
    assert HF_SECRET not in json.dumps(result)


def test_model_root_snapshot_caps_an_undeclared_metadata_body(tmp_path, monkeypatch):
    """Revision info is metadata: at most 4 MiB is read (one chunk more refuses), as ModelHub's limit says."""
    wp = planning()
    state = tmp_path / "state"
    hub = BoundedHub(huge_info=True).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True})
    issue = desk.only_issue(result, "invalid", "snapshot_failed", "/repo_id")
    assert issue["message"] == "Hugging Face metadata exceeds the inspection size limit"
    assert 4 * MIB < hub.pulled <= 5 * MIB
    assert files(wp.root_snapshots_dir(state)) == []


# -- Task 11.5 (R5 I2): a recipe's input kind against the bound dataset's kind, through the channel ----------------

def kind_dataset(state, tmp_path, kind):
    """A registered dataset of another kind than the SFT instruction recipe takes."""
    rows = {"preference": [{"instruction": f"선호 {i}", "chosen": f"좋은 답 {i}", "rejected": f"나쁜 답 {i}"}
                           for i in range(4)],
            "text": [{"text": f"사전학습 문서 {i}"} for i in range(4)]}[kind]
    path = tmp_path / f"{kind}.json"
    path.write_bytes(canonical(rows))
    record = Registry(state / "registry").register_dataset(f"{kind}-data", "1", path, export_allowed=True, kind=kind)
    return {"dataset_id": f"{kind}-data", "version": "1", "sha256": record["sha256"]}


@pytest.mark.parametrize("kind", ["preference", "text"])
def test_an_instruction_recipe_bound_to_other_data_is_refused_before_any_run(tmp_path, monkeypatch, kind):
    """``graph_planner._definition_issues``' kind check (``train_contract``) reaches the user through
    ``graph_validate`` and ``graph_plan`` (simulation and real), on the train node, before anything is stored."""
    planning()
    state = tmp_path / "state"
    found = real_scenario(state, tmp_path, monkeypatch)
    recipe = desk.call(state, "recipe_get", {"recipe_id": "sft-lora"})
    assert recipe["status"] == "ok" and recipe["data"]["recipe"]["training_stage"] == "sft", recipe
    assert recipe["data"]["recipe"]["input_kind"] == "instruction"
    dataset = kind_dataset(state, tmp_path, kind)
    project_id, _ = make_project(state, [train("train", found.recipe, from_root(found.root), ("pod-a",))],
                                 [binding("train", "train", dataset)])
    message = "training dataset kind differs from recipe input kind"
    for result in (run(state, "graph_validate", {"project_id": project_id}),
                   run(state, "graph_plan", plan_request(project_id, estimates=()), profiles=[found.profile]),
                   run(state, "graph_plan", plan_request(project_id, simulation=False, estimates=()),
                       settings=TOKEN_SETTINGS, profiles=[found.profile], clock=NOW + 60)):
        issue = desk.only_issue(result, "invalid", "train_contract", "/graph/nodes/0")
        assert issue["node_id"] == "train" and issue["message"] == message, result
    assert not (state / "jobs.db").exists() and files(state / "workflows" / "plans") == []


class EncodedHub(FakeHub):
    """Answers ``encoded`` (the revision info or one tokenizer file) gzip-encoded, whatever the request asked."""

    def __init__(self, encoded, **options):
        super().__init__(**options)
        self.encoded = encoded

    def respond(self, request):
        import gzip
        response = super().respond(request)
        path = unquote(request.url.path)
        wanted = (path == f"/api/models/{self.repo}/revision/{self.commit}" if self.encoded == "info"
                  else path.endswith("/resolve/" + self.commit + "/tokenizer.json"))
        if not wanted or response.status_code != 200:
            return response
        return httpx.Response(200, content=gzip.compress(response.read()), headers={"Content-Encoding": "gzip"})


@pytest.mark.parametrize("encoded", ["info", "file"])
def test_model_root_snapshot_asks_for_identity_and_refuses_an_encoded_answer(tmp_path, monkeypatch, encoded):
    """Re-review m2: the caps count the bytes on the wire, so the snapshot client sends ``Accept-Encoding: identity``
    and refuses a compressed answer unread (``hf_targets._identity``), metadata and files alike."""
    wp = planning()
    from tuning_factory import hf_targets as ht
    state = tmp_path / "state"
    hub = EncodedHub(encoded).install(monkeypatch)
    result = run(state, "model_root_snapshot", {"repo_id": REPO, "revision": COMMIT, "download_approved": True})
    issue = desk.only_issue(result, "invalid", "snapshot_failed", "/repo_id")
    assert issue["message"] == ht.ENCODED_RESPONSE
    assert hub.requests and all(item.headers.get("accept-encoding") == "identity" for item in hub.requests)
    assert files(wp.root_snapshots_dir(state)) == []
