"""Task 9.7: header-only HF-root target evidence from safetensors headers over HTTP range requests.

Fakes only: Hugging Face is an ``httpx.MockTransport`` serving synthetic safetensors files (zero payloads), and the
socket guard refuses every connection. No GPU, SSH, model download or MLflow write.
"""
import importlib
import json
import logging
import math
import os
import re
import socket
import struct
import subprocess
import sys
import textwrap
from urllib.parse import unquote, urlsplit

import httpx
import pytest
import test_desktop_projects as desk
import test_workflow_planning as wpt

from tuning_factory.registry import canonical, digest

# Built at runtime: the history-walking secret scan must never see a credential-shaped literal.
HF_SECRET = "synthetic-" + "hf-" + "t" * 12
RUNPOD_SECRET = "synthetic-" + "runpod-" + "k" * 12
REPO, COMMIT = wpt.REPO, wpt.COMMIT
INDEX = "model.safetensors.index.json"
SINGLE = "model.safetensors"
SHARDS = ("model-00001-of-00002.safetensors", "model-00002-of-00002.safetensors")
CDN = "https://cdn-lfs.hf.example"
MIB = 1024 * 1024
WIDTH = {"BF16": 2, "F16": 2, "F32": 4, "I8": 1, "I32": 4, "U8": 1}
HIDDEN, KV, INTER, VOCAB = 8, 4, 16, 32
LINEAR = {"self_attn.q_proj": (HIDDEN, HIDDEN), "self_attn.k_proj": (KV, HIDDEN), "self_attn.v_proj": (KV, HIDDEN),
          "self_attn.o_proj": (HIDDEN, HIDDEN), "mlp.gate_proj": (INTER, HIDDEN), "mlp.up_proj": (INTER, HIDDEN),
          "mlp.down_proj": (HIDDEN, INTER)}


def layer(index):
    prefix = f"model.layers.{index}."
    return {**{f"{prefix}{name}.weight": shape for name, shape in LINEAR.items()},
            f"{prefix}self_attn.q_proj.bias": (HIDDEN,), f"{prefix}input_layernorm.weight": (HIDDEN,),
            f"{prefix}post_attention_layernorm.weight": (HIDDEN,), f"{prefix}self_attn.q_norm.weight": (2, 4)}


FIRST = {"model.embed_tokens.weight": (VOCAB, HIDDEN), **layer(0)}
SECOND = {**layer(1), "model.norm.weight": (HIDDEN,), "lm_head.weight": (VOCAB, HIDDEN)}
# Written by hand (not computed by the rule under test): the 2D *.weight tensors outside embeddings, norms and
# heads, as {module: (out, in)}.
EXPECTED = {f"model.layers.{index}.{name}": shape for index in (0, 1) for name, shape in LINEAR.items()}
LEAVES = ["down_proj", "gate_proj", "k_proj", "o_proj", "q_proj", "up_proj", "v_proj"]


def targets():
    # A missing implementation is a test failure, rather than a collection error.
    try:
        return importlib.import_module("tuning_factory.hf_targets")
    except ModuleNotFoundError as exc:
        if exc.name != "tuning_factory.hf_targets":
            raise
        pytest.fail("the HF-root target inspector (Task 9.7) is missing")


@pytest.fixture(autouse=True)
def no_network(monkeypatch):
    """Only MockTransport: no Hugging Face, CDN, RunPod, SSH or MLflow connection."""
    def refuse(*args, **kwargs):
        raise AssertionError("HF target inspection tests must not open a network connection")
    monkeypatch.setattr(socket.socket, "connect", refuse)


# -- synthetic checkpoints -----------------------------------------------------------------------

def raw_file(header: bytes, payload: int) -> bytes:
    return struct.pack("<Q", len(header)) + header + bytes(payload)


def safetensors(shapes, *, dtypes=None) -> bytes:
    """A safetensors file with a zero payload and exact, contiguous offsets."""
    header, offset = {"__metadata__": {"format": "pt"}}, 0
    for name, shape in shapes.items():
        dtype = (dtypes or {}).get(name, "BF16")
        size = WIDTH[dtype] * math.prod(shape)
        header[name] = {"dtype": dtype, "shape": list(shape), "data_offsets": [offset, offset + size]}
        offset += size
    return raw_file(json.dumps(header).encode(), offset)


def index_of(shards) -> bytes:
    return json.dumps({"metadata": {"total_size": 0},
                       "weight_map": {name: shard for shard, shapes in shards.items() for name in shapes}}).encode()


def checkpoint(first=FIRST, second=SECOND, *, dtypes=None) -> dict:
    shards = dict(zip(SHARDS, (first, second), strict=True))
    files = {shard: safetensors(shapes, dtypes=dtypes) for shard, shapes in shards.items()}
    files[INDEX] = index_of(shards)
    return files


def header_length(data: bytes) -> int:
    return struct.unpack("<Q", data[:8])[0]


class FakeShards:
    """HF resolve URLs of one pinned revision over httpx.MockTransport: the shard index in full, safetensors files
    only as byte ranges. ``endless`` bodies count every byte the client pulls."""

    def __init__(self, files=None, *, ignore_range=False, redirect=None, hook=None):
        self.files = checkpoint() if files is None else dict(files)
        self.ignore_range, self.redirect, self.hook = ignore_range, redirect, hook
        self.requests, self.pulled = [], 0

    def endless(self, chunk):
        def chunks():
            while self.pulled < 4096 * MIB:
                self.pulled += chunk
                yield bytes(chunk)
        return chunks()

    def name(self, request):
        path = unquote(request.url.path)
        prefix = f"/{REPO}/resolve/{COMMIT}/"
        if request.url.host == "huggingface.co" and path.startswith(prefix):
            return path[len(prefix):]
        if self.redirect and request.url.host == urlsplit(self.redirect).hostname:
            return path[1:]
        return None

    def respond(self, request):
        self.requests.append(request)
        name = self.name(request)
        if name is None:
            return httpx.Response(404)
        if self.redirect and request.url.host == "huggingface.co" and name.endswith(".safetensors"):
            # No Content-Length and an endless body: a redirect is followed without reading its body at all.
            return httpx.Response(302, headers={"Location": f"{self.redirect}/{name}?signature=1"},
                                  content=self.endless(MIB))
        data = self.files.get(name)
        if data is None:
            return httpx.Response(404)
        wanted = re.fullmatch(r"bytes=(\d+)-(\d+)", request.headers.get("range", ""))
        if self.hook is not None:
            custom = self.hook(self, request, name, data, wanted)
            if custom is not None:
                return custom
        if not name.endswith(".safetensors"):
            return httpx.Response(200, content=data)
        if wanted is None or self.ignore_range:
            return httpx.Response(200, content=self.endless(MIB))   # the whole file: weights
        start, end = int(wanted[1]), min(int(wanted[2]), len(data) - 1)
        return httpx.Response(206, content=data[start:end + 1],
                              headers={"Content-Range": f"bytes {start}-{end}/{len(data)}"})

    def install(self, monkeypatch):
        ht = targets()
        real = getattr(ht._client, "real_client", ht._client)   # the module's own factory, even when installed twice

        def client(token):
            return real(token, transport=httpx.MockTransport(self.respond))

        client.real_client = real
        monkeypatch.setattr(ht, "_client", client)
        return self

    @property
    def calls(self):
        """``(file name, Range header)`` of every request, in order."""
        return [(self.name(request), request.headers.get("range")) for request in self.requests]


def request(**changes):
    return {"repo_id": REPO, "revision": COMMIT, "download_approved": True, **changes}


def inspect(state, data=None, *, secrets=None):
    targets()
    return wpt.run(state, "model_root_targets", request() if data is None else data, secrets=secrets)


def stored(state):
    return wpt.files(state / "workflows" / "root-targets")


def evidence_of(state, root):
    from tuning_factory.model_artifacts import HFRootRef
    return wpt.planning().load_root_targets(state, HFRootRef.model_validate(root))


def dimensions(evidence):
    return {module.name: (module.output_dimension, module.input_dimension) for module in evidence.modules}


def ranges(data):
    length = header_length(data)
    return ["bytes=0-7", f"bytes=8-{8 + length - 1}"]


# -- the action and its table entry ----------------------------------------------------------------

def test_model_root_targets_is_a_closed_table_action_scoped_to_hf_token(tmp_path):
    targets()
    wf = desk.workflows()
    # 9.7's action follows the 9.4 pair; Task 9.8 appends its own five actions after it.
    assert wf.WORKFLOW_ACTIONS[wf.WORKFLOW_ACTIONS.index("controller_stop") + 1] == "model_root_targets"
    assert wf.ACTION_MODULES["model_root_targets"] == "tuning_factory.hf_targets"
    assert wf.SECRET_SCOPES["model_root_targets"] == frozenset({"HF_TOKEN"})
    model = wf.registered_requests()["model_root_targets"]
    assert set(model.model_fields) == {"repo_id", "revision", "download_approved"}
    context = wf.WorkflowContext(state_dir=tmp_path / "state", secrets={"RUNPOD_API_KEY": RUNPOD_SECRET})
    with pytest.raises(ValueError, match=wf.SCOPE_REFUSED):
        wf.dispatch_action(context, "model_root_targets", request())


@pytest.mark.parametrize("case", ["unapproved", "approval_not_strict", "branch_revision", "short_revision",
                                  "device_owner", "trailing_dot", "not_indexed", "ambiguous_root", "extra_field"])
def test_refusals_before_any_request(tmp_path, monkeypatch, case):
    ht = targets()
    state = tmp_path / "state"
    if case != "not_indexed":
        root = wpt.snapshot_root(state, monkeypatch)
    if case == "ambiguous_root":   # a second index entry for the same repo@revision: refused, never chosen
        from tuning_factory.graph_planner import ModelContext
        [entry] = wpt.planning().model_roots(state)
        forged = entry.root.model_copy(update={"tokenizer_fingerprint": "e" * 64})
        wpt.planning().save_root(state, ModelContext(root=forged, profile=entry.profile))
        assert root == entry.root.model_dump(mode="json") and len(wpt.planning().model_roots(state)) == 2
    server = FakeShards().install(monkeypatch)
    data, expected = {
        "unapproved": (request(download_approved=False), ("approval_required", "/download_approved")),
        "approval_not_strict": (request(download_approved="true"), ("invalid_value", "/download_approved")),
        "branch_revision": (request(revision="main"), ("invalid_value", "/revision")),
        "short_revision": (request(revision="c" * 39), ("invalid_value", "/revision")),
        "device_owner": (request(repo_id="CON/model"), ("repository_unsupported", "/repo_id")),
        "trailing_dot": (request(repo_id="Qwen/model."), ("repository_unsupported", "/repo_id")),
        "not_indexed": (request(), ("model_root_missing", "/repo_id")),
        "ambiguous_root": (request(), ("model_root_invalid", "/repo_id")),
        "extra_field": (request(command="calc.exe"), ("extra_field", "")),
    }[case]
    result = inspect(state, data, secrets={"HF_TOKEN": HF_SECRET})
    issue = desk.only_issue(result, "invalid", *expected)
    if case == "unapproved":
        assert issue["message"] == ht.TARGETS_APPROVAL_REQUIRED
    if case == "not_indexed":
        assert f"{REPO}@{COMMIT}" in issue["message"]
    assert server.requests == [] and stored(state) == []
    assert "calc.exe" not in json.dumps(result) and HF_SECRET not in json.dumps(result)


# -- reading headers ---------------------------------------------------------------------------------

def test_sharded_root_reads_two_ranges_per_shard_and_stores_evidence(tmp_path, monkeypatch):
    ht = targets()
    wp = wpt.planning()
    from tuning_factory.model_artifacts import HFRootRef
    assert (ht.MAX_HEADER_BYTES, ht.INDEX_FILE, ht.SINGLE_FILE) == (16 * MIB, INDEX, SINGLE)
    state = tmp_path / "state"
    root = wpt.snapshot_root(state, monkeypatch)
    server = FakeShards().install(monkeypatch)
    result = inspect(state, secrets={"HF_TOKEN": HF_SECRET})
    assert result["status"] == "ok", result
    first, second = (server.files[shard] for shard in SHARDS)
    assert server.calls == [(INDEX, None), *((SHARDS[0], value) for value in ranges(first)),
                            *((SHARDS[1], value) for value in ranges(second))]
    assert all(item.headers.get("authorization") == "Bearer " + HF_SECRET for item in server.requests)
    assert all(item.method == "GET" for item in server.requests)
    evidence = evidence_of(state, root)
    assert evidence.base == HFRootRef.model_validate(root)
    assert dimensions(evidence) == EXPECTED
    assert [module.name for module in evidence.modules] == sorted(EXPECTED)
    assert result["data"] == {"root": root, "shards": list(SHARDS), "module_count": len(EXPECTED),
                              "targets": LEAVES, "evidence_sha256": digest(canonical(evidence.model_dump(mode="json")))}
    path = wp.root_targets_path(state, evidence.base)
    assert stored(state) == [path.name]
    [listed] = wpt.run(state, "model_roots_list")["data"]["roots"]
    assert listed["targets_ready"] is True and listed["snapshot_ready"] is True
    assert HF_SECRET.encode() not in wpt.state_bytes(state) and HF_SECRET not in json.dumps(result)
    # The same pinned root again: identical evidence is idempotent; without a token no Authorization is sent.
    # The index lists the second shard first this time: shards are read and reported in sorted order.
    before = path.read_bytes()
    files = dict(server.files)
    weight_map = json.loads(files[INDEX])["weight_map"]
    files[INDEX] = json.dumps({"weight_map": dict(reversed(weight_map.items()))}).encode()
    assert next(iter(json.loads(files[INDEX])["weight_map"].values())) == SHARDS[1]
    again = FakeShards(files).install(monkeypatch)
    assert inspect(state) == result
    assert path.read_bytes() == before and again.calls == server.calls
    assert not any("authorization" in item.headers for item in again.requests)


def test_single_file_root_without_an_index(tmp_path, monkeypatch):
    targets()
    state = tmp_path / "state"
    root = wpt.snapshot_root(state, monkeypatch)
    data = safetensors({**FIRST, **SECOND})
    server = FakeShards({SINGLE: data}).install(monkeypatch)
    result = inspect(state)
    assert result["status"] == "ok", result
    assert server.calls == [(INDEX, None), *((SINGLE, value) for value in ranges(data))]
    assert result["data"]["shards"] == [SINGLE]
    assert dimensions(evidence_of(state, root)) == EXPECTED


def test_evidence_is_exactly_what_verify_inspected_targets_accepts(tmp_path, monkeypatch):
    """The header rule is the full-model artifact rule: the same tensors as a verified full-model inventory pass
    ``verify_inspected_targets`` with the evidence modules unchanged."""
    targets()
    from tuning_factory.graph_planner import ModelTargetEvidence
    from tuning_factory.model_artifacts import HFRootRef, ModelArtifactRef, ModelDescriptor, TensorMetadata
    from tuning_factory.target_inspection import verify_inspected_targets
    state = tmp_path / "state"
    root = HFRootRef.model_validate(wpt.snapshot_root(state, monkeypatch))
    FakeShards().install(monkeypatch)
    assert inspect(state)["status"] == "ok"
    evidence = evidence_of(state, root.model_dump(mode="json"))
    tensors = tuple(TensorMetadata(file=shard, name=name, shape=shape, dtype="BF16")
                    for shard, shapes in zip(SHARDS, (FIRST, SECOND), strict=True) for name, shape in shapes.items())
    descriptor = ModelDescriptor(producer_job_id="inventory", producer_attempt_id="inventory",
                                 producer_manifest_sha256="b" * 64, storage_kind="full_model",
                                 semantic_kind="causal_lm", has_value_head=False, root=root, effective_base=root,
                                 config_sha256=root.config_sha256, tokenizer_fingerprint=root.tokenizer_fingerprint,
                                 tensors=tensors)
    ref = ModelArtifactRef(artifact_id=digest(canonical(descriptor.model_dump(mode="json"))), inventory_sha256="0" * 64)
    moved = ModelTargetEvidence(base=ref, modules=evidence.modules)
    assert verify_inspected_targets(moved, descriptor, ref).modules == evidence.modules


def test_a_server_that_ignores_range_is_refused_without_streaming_weights(tmp_path, monkeypatch):
    ht = targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    server = FakeShards(ignore_range=True).install(monkeypatch)
    result = inspect(state)
    issue = desk.only_issue(result, "invalid", "range_unsupported", "/repo_id")
    assert issue["message"] == ht.RANGE_UNSUPPORTED
    # The 200 body (1 MiB chunks of an endless file) was never read: not even one chunk.
    assert server.pulled <= 64 * 1024
    assert server.calls == [(INDEX, None), (SHARDS[0], "bytes=0-7")] and stored(state) == []


def _violation(case):
    def hook(server, request, name, data, wanted):
        total = len(data)
        if case == "gated":
            return httpx.Response(401, content=b"access to this model is restricted")
        if not name.endswith(".safetensors") or wanted is None:
            return None
        first = wanted[1] == "0"
        content_range = {"Content-Range": f"bytes 0-7/{total}"}
        if case == "no_content_range" and first:
            return httpx.Response(206, content=data[:8])
        if case == "other_range" and first:
            return httpx.Response(206, content=data[:1024], headers={"Content-Range": f"bytes 0-1023/{total}"})
        if case == "shifted_range" and first:     # the right length, but another range
            return httpx.Response(206, content=data[8:16], headers={"Content-Range": f"bytes 8-15/{total}"})
        if case == "unknown_total" and first:
            return httpx.Response(206, content=data[:8], headers={"Content-Range": "bytes 0-7/*"})
        if case == "short_body" and first:
            return httpx.Response(206, content=data[:4], headers=content_range)
        if case == "long_body" and first:
            return httpx.Response(206, content=server.endless(4096), headers=content_range)
        if case == "gzip" and first:      # streamed, so httpx does not decode it while building the response
            return httpx.Response(206, content=iter([data[:8]]), headers={**content_range, "Content-Encoding": "gzip"})
        if case == "second_ignores_range" and not first:
            return httpx.Response(200, content=server.endless(MIB))
        if case == "total_changes" and not first:
            start, end = int(wanted[1]), int(wanted[2])
            return httpx.Response(206, content=data[start:end + 1],
                                  headers={"Content-Range": f"bytes {start}-{end}/{total + 1}"})
        if case == "missing_shard" and name == SHARDS[1]:
            return httpx.Response(404)
        return None
    return hook


@pytest.mark.parametrize("case, code", [
    ("no_content_range", "range_unsupported"), ("other_range", "range_unsupported"),
    ("shifted_range", "range_unsupported"),
    ("unknown_total", "range_unsupported"), ("short_body", "range_unsupported"), ("long_body", "range_unsupported"),
    ("gzip", "range_unsupported"), ("second_ignores_range", "range_unsupported"), ("total_changes", "header_invalid"),
    ("missing_shard", "targets_failed"), ("gated", "targets_failed"), ("oversized_header", "size_limit"),
    ("header_beyond_file", "header_invalid"), ("zero_length", "header_invalid")])
def test_range_protocol_violations_are_refused(tmp_path, monkeypatch, case, code):
    targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    files = checkpoint()
    first = files[SHARDS[0]]
    prefix = {"oversized_header": 16 * MIB + 1, "header_beyond_file": len(first), "zero_length": 0}.get(case)
    if prefix is not None:
        files[SHARDS[0]] = struct.pack("<Q", prefix) + first[8:]
    server = FakeShards(files, hook=_violation(case)).install(monkeypatch)
    result = inspect(state, secrets={"HF_TOKEN": HF_SECRET})
    issue = desk.only_issue(result, "invalid", code, "/repo_id")
    assert stored(state) == [] and HF_SECRET not in json.dumps(result)
    shard_calls = [value for name, value in server.calls if name == SHARDS[0]]
    if case in {"oversized_header", "header_beyond_file", "zero_length"}:
        assert shard_calls == ["bytes=0-7"]            # never the second range
    if case == "long_body":
        assert server.pulled <= 8 * 1024               # the cap plus at most one chunk
    if case == "second_ignores_range":
        assert server.pulled <= 64 * 1024
    if case == "gated":
        assert "401" in issue["message"] and server.calls == [(INDEX, None)]


TENSOR = {"dtype": "BF16", "shape": [2, 2], "data_offsets": [0, 8]}


def _malformed(case):
    def entry(**changes):
        return {**TENSOR, **changes}

    def text(value):
        return json.dumps(value).encode()

    return {
        "duplicate_key": (b'{"a.weight": ' + text(TENSOR) + b', "a.weight": ' + text(TENSOR) + b"}", 8),
        "gap": (text({"a.weight": entry(), "b.weight": entry(data_offsets=[16, 24])}), 24),
        "overlap": (text({"a.weight": entry(), "b.weight": entry(data_offsets=[4, 12])}), 12),
        "unknown_dtype": (text({"a.weight": entry(dtype="Q4")}), 8),
        "bad_metadata": (text({"__metadata__": {"format": 1}, "a.weight": entry()}), 8),
        "extra_key": (text({"a.weight": entry(scale=1)}), 8),
        "unsafe_name": (text({"a/../b.weight": entry()}), 8),
        "short_payload": (text({"a.weight": entry()}), 4),
        "long_payload": (text({"a.weight": entry()}), 12),
        "nonfinite": (b'{"a.weight": {"dtype": "BF16", "shape": [NaN, 2], "data_offsets": [0, 8]}}', 8),
        "not_an_object": (b"[]", 8),
        "empty": (b"{}", 0),
    }[case]


@pytest.mark.parametrize("case", ["duplicate_key", "gap", "overlap", "unknown_dtype", "bad_metadata", "extra_key",
                                  "unsafe_name", "short_payload", "long_payload", "nonfinite", "not_an_object",
                                  "empty"])
def test_header_parsing_matches_the_local_safetensors_rules(tmp_path, monkeypatch, case):
    """Every header a local ``tensor_header`` refuses is refused over HTTP too (one rule set)."""
    targets()
    from tuning_factory.model_artifacts import tensor_header
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    data = raw_file(*_malformed(case))
    local = tmp_path / "local" / SINGLE
    local.parent.mkdir()
    local.write_bytes(data)
    with pytest.raises(ValueError):
        tensor_header(local, SINGLE)
    FakeShards({SINGLE: data}).install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", "header_invalid", "/repo_id")
    assert stored(state) == []


# -- what is refused, not guessed -------------------------------------------------------------------

@pytest.mark.parametrize("case", ["moe_profile", "multimodal_profile", "fused_experts_3d", "expert_weight_3d",
                                  "per_expert_2d", "conv_kernel", "int8_weight", "gptq_qweight", "prefix_less",
                                  "classifier_score", "value_head", "empty_targets", "zero_dimension"])
def test_architectures_without_plain_linear_weights_are_refused_not_guessed(tmp_path, monkeypatch, case):
    ht = targets()
    state = tmp_path / "state"
    model_type = {"moe_profile": "qwen3_moe", "multimodal_profile": "gemma3"}.get(case, "qwen2")
    config = canonical({"model_type": model_type, "max_position_embeddings": 32768, "num_hidden_layers": 2,
                        "torch_dtype": "bfloat16"})
    root = wpt.snapshot_root(state, monkeypatch, files={"config.json": config, **wpt.TOKENIZER})
    extra = {"fused_experts_3d": {"model.layers.1.mlp.experts.gate_up_proj": (4, HIDDEN, 2 * INTER)},
             "expert_weight_3d": {"model.layers.1.mlp.experts.down_proj.weight": (4, INTER, HIDDEN)},
             "per_expert_2d": {"model.layers.1.mlp.experts.0.gate_proj.weight": (INTER, HIDDEN)},
             "conv_kernel": {"model.layers.1.linear_attn.conv1d.weight": (INTER, 1, 4)},
             "gptq_qweight": {"model.layers.1.self_attn.q_proj.qweight": (1, HIDDEN)},
             # A sequence classifier's head and a value head are not modules of AutoModelForCausalLM.
             "classifier_score": {"score.weight": (2, HIDDEN)},
             "value_head": {"v_head.summary.weight": (1, HIDDEN)}}.get(case, {})
    dtypes = {"int8_weight": {"model.layers.0.self_attn.q_proj.weight": "I8"},
              "gptq_qweight": {"model.layers.1.self_attn.q_proj.qweight": "I32"}}.get(case)
    first, second = FIRST, {**SECOND, **extra}
    if case == "prefix_less":     # saved from the bare XModel (embedding models): the runtime adds ``model.``
        first = {name.removeprefix("model."): shape for name, shape in FIRST.items()}
        second = {name.removeprefix("model."): shape for name, shape in SECOND.items() if name != "lm_head.weight"}
    elif case == "empty_targets":  # valid safetensors, but no 2D target weight at all
        first = {"model.embed_tokens.weight": (VOCAB, HIDDEN)}
        second = {"model.norm.weight": (HIDDEN,), "lm_head.weight": (VOCAB, HIDDEN)}
    elif case == "zero_dimension":
        first = {**FIRST, "model.layers.0.self_attn.q_proj.weight": (0, HIDDEN)}
    server = FakeShards(checkpoint(first, second, dtypes=dtypes)).install(monkeypatch)
    result = inspect(state)
    issue = desk.only_issue(result, "invalid", "architecture_unsupported", "/repo_id")
    assert issue["message"] == ht.ARCHITECTURE_UNSUPPORTED
    assert stored(state) == [] and evidence_of(state, root) is None
    if case.endswith("_profile"):
        assert server.requests == []                 # refused from the indexed profile, before any request
    else:
        assert any(value == "bytes=0-7" for _, value in server.calls)


def test_different_evidence_for_the_same_pinned_root_is_refused(tmp_path, monkeypatch):
    ht = targets()
    wp = wpt.planning()
    from tuning_factory.model_artifacts import HFRootRef
    state = tmp_path / "state"
    root = wpt.snapshot_root(state, monkeypatch)
    FakeShards().install(monkeypatch)
    assert inspect(state)["status"] == "ok"
    path = wp.root_targets_path(state, HFRootRef.model_validate(root))
    before = path.read_bytes()
    changed = checkpoint(first={**FIRST, "model.layers.0.self_attn.q_proj.weight": (KV, HIDDEN)})
    FakeShards(changed).install(monkeypatch)
    issue = desk.only_issue(inspect(state), "conflict", "version_exists", "/repo_id")
    assert issue["message"] == ht.TARGETS_CHANGED
    assert path.read_bytes() == before
    path.write_bytes(b"{}")
    server = FakeShards().install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", "root_targets_invalid", "/repo_id")
    assert path.read_bytes() == b"{}" and server.requests == []      # refused before any request


def _index_case(case, files):
    weight_map = json.loads(files[INDEX])["weight_map"]
    if case == "unsafe_shard":
        weight_map["model.norm.weight"] = "../evil.safetensors"
    elif case == "not_safetensors":
        weight_map["model.norm.weight"] = "pytorch_model-00002-of-00002.bin"
    elif case == "missing_tensor":
        weight_map["model.layers.9.mlp.up_proj.weight"] = SHARDS[1]
    elif case == "unlisted_tensor":
        del weight_map["model.norm.weight"]
    elif case == "wrong_shard":
        weight_map["model.norm.weight"] = SHARDS[0]
    elif case == "empty_map":
        weight_map.clear()
    files[INDEX] = json.dumps({"weight_map": weight_map}).encode()
    if case == "duplicate_key":
        files[INDEX] = b'{"weight_map": {}, "weight_map": {}}'
    elif case == "not_json":
        files[INDEX] = b"not json"
    elif case == "not_a_map":
        files[INDEX] = b'{"weight_map": ["model.norm.weight"]}'
    elif case in {"no_safetensors", "only_bin"}:
        files.clear()
        if case == "only_bin":
            files["pytorch_model.bin"] = b"weights"
    return files


@pytest.mark.parametrize("case, code", [
    ("unsafe_shard", "index_invalid"), ("not_safetensors", "index_invalid"), ("missing_tensor", "index_invalid"),
    ("unlisted_tensor", "index_invalid"), ("wrong_shard", "index_invalid"), ("empty_map", "index_invalid"),
    ("duplicate_key", "index_invalid"), ("not_json", "index_invalid"), ("not_a_map", "index_invalid"),
    ("too_many_shards", "index_invalid"), ("oversized_index", "size_limit"),
    ("no_safetensors", "weights_format_unsupported"), ("only_bin", "weights_format_unsupported")])
def test_index_inconsistencies_are_refused(tmp_path, monkeypatch, case, code):
    ht = targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    if case == "too_many_shards":
        monkeypatch.setattr(ht, "MAX_SHARDS", 1)
    if case == "oversized_index":
        monkeypatch.setattr(ht, "MAX_INDEX_BYTES", 64)
    server = FakeShards(_index_case(case, checkpoint())).install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", code, "/repo_id")
    assert stored(state) == []
    names = [name for name, _ in server.calls]
    assert not any(name and (".bin" in name or "evil" in name) for name in names)
    if case in {"unsafe_shard", "not_safetensors", "empty_map", "duplicate_key", "not_json", "not_a_map",
                "too_many_shards", "oversized_index"}:
        assert names == [INDEX]                      # refused before any range request
    if case in {"no_safetensors", "only_bin"}:
        assert server.calls == [(INDEX, None), (SINGLE, "bytes=0-7")]


def test_the_total_cap_bounds_every_read(tmp_path, monkeypatch):
    ht = targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    files = checkpoint()
    needed = len(files[INDEX]) + sum(8 + header_length(files[shard]) for shard in SHARDS)
    monkeypatch.setattr(ht, "MAX_TARGET_TOTAL_BYTES", needed - 1)
    server = FakeShards(files).install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", "size_limit", "/repo_id")
    # The second shard's header would pass the total: its range is never requested.
    assert server.calls == [(INDEX, None), *((SHARDS[0], value) for value in ranges(files[SHARDS[0]])),
                            (SHARDS[1], "bytes=0-7")]
    assert stored(state) == []
    monkeypatch.setattr(ht, "MAX_TARGET_TOTAL_BYTES", needed)
    FakeShards(files).install(monkeypatch)
    assert inspect(state)["status"] == "ok"


# -- redirects and secrets ---------------------------------------------------------------------------

def test_authorization_is_dropped_on_cross_host_redirects(tmp_path, monkeypatch):
    targets()
    state = tmp_path / "state"
    root = wpt.snapshot_root(state, monkeypatch)
    server = FakeShards(redirect=CDN).install(monkeypatch)
    result = inspect(state, secrets={"HF_TOKEN": HF_SECRET})
    assert result["status"] == "ok", result
    hub = [item for item in server.requests if item.url.host == "huggingface.co"]
    cdn = [item for item in server.requests if item.url.host == urlsplit(CDN).hostname]
    assert len(hub) == 5 and len(cdn) == 4
    assert all(item.headers.get("authorization") == "Bearer " + HF_SECRET for item in hub)
    assert not any("authorization" in item.headers for item in cdn)
    assert [item.headers.get("range") for item in cdn] == [
        *ranges(server.files[SHARDS[0]]), *ranges(server.files[SHARDS[1]])]
    assert dimensions(evidence_of(state, root)) == EXPECTED
    # The redirects declared no Content-Length and streamed endless bodies: not one byte of them was read.
    assert server.pulled == 0


@pytest.mark.parametrize("case, code", [("plain_http", "insecure_redirect"), ("redirect_loop", "targets_failed")])
def test_unsafe_redirects_are_refused(tmp_path, monkeypatch, case, code):
    ht = targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)

    def loop(server, request, name, data, wanted):
        return httpx.Response(302, headers={"Location": str(request.url)}, content=server.endless(MIB))

    options = {"redirect": "http://cdn-lfs.hf.example"} if case == "plain_http" else {"hook": loop}
    server = FakeShards(**options).install(monkeypatch)
    issue = desk.only_issue(inspect(state, secrets={"HF_TOKEN": HF_SECRET}), "invalid", code, "/repo_id")
    assert all(item.url.scheme == "https" for item in server.requests)   # never sent over plain HTTP
    assert not any(item.url.host == urlsplit(CDN).hostname for item in server.requests)
    assert server.pulled == 0 and stored(state) == []
    if case == "redirect_loop":
        assert issue["message"] == ht.TOO_MANY_REDIRECTS
        assert len(server.requests) == ht.MAX_REDIRECTS + 1            # the first request and five redirects


def test_a_declared_oversized_index_is_refused_before_reading(tmp_path, monkeypatch):
    ht = targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)

    def declared(server, request, name, data, wanted):
        if name == INDEX:
            return httpx.Response(200, content=server.endless(MIB),
                                  headers={"Content-Length": str(ht.MAX_INDEX_BYTES + 1)})
        return None

    server = FakeShards(hook=declared).install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", "size_limit", "/repo_id")
    assert server.pulled == 0 and server.calls == [(INDEX, None)] and stored(state) == []


@pytest.mark.parametrize("where, code", [("header", "header_invalid"), ("index", "index_invalid")])
def test_deeply_nested_json_is_refused_not_raised(tmp_path, monkeypatch, where, code):
    """200,000 ``[`` make ``json.loads`` raise RecursionError: an invalid header or index, over HTTP and locally."""
    targets()
    from tuning_factory.model_artifacts import read_json, tensor_header
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)
    nested = b"[" * 200_000
    local = tmp_path / "local"
    local.mkdir()
    if where == "header":
        files = {SINGLE: raw_file(nested, 0)}
        (local / SINGLE).write_bytes(files[SINGLE])
        with pytest.raises(ValueError, match="invalid artifact JSON metadata"):
            tensor_header(local / SINGLE, SINGLE)
    else:
        files = {**checkpoint(), INDEX: nested}
        (local / INDEX).write_bytes(nested)
        with pytest.raises(ValueError, match="invalid artifact JSON metadata"):
            read_json(local / INDEX)
    FakeShards(files).install(monkeypatch)
    desk.only_issue(inspect(state), "invalid", code, "/repo_id")
    assert stored(state) == []


def test_hf_token_never_reaches_files_responses_exceptions_or_logs(tmp_path, monkeypatch, caplog):
    ht = targets()
    caplog.set_level(logging.DEBUG)
    state = tmp_path / "state"
    environment = dict(os.environ)
    wpt.snapshot_root(state, monkeypatch)
    responses = []

    def echo(server, request, name, data, wanted):
        token = request.headers.get("authorization", "")
        return httpx.Response(403, content=b"denied for " + token.encode(), headers={"X-Echo": token})

    FakeShards(hook=echo).install(monkeypatch)
    responses.append(inspect(state, secrets={"HF_TOKEN": HF_SECRET}))
    assert desk.only_issue(responses[-1], "invalid", "targets_failed")["message"] == (
        "Hugging Face file request failed (HTTP 403); check model access")

    def crash(server, request, name, data, wanted):
        raise httpx.ConnectError("connect failed with " + request.headers.get("authorization", ""), request=request)

    FakeShards(hook=crash).install(monkeypatch)
    responses.append(inspect(state, secrets={"HF_TOKEN": HF_SECRET}))
    assert desk.only_issue(responses[-1], "invalid", "targets_failed")["message"] == ht.HF_CONNECTION

    def leak_in_header(server, request, name, data, wanted):
        if name.endswith(".safetensors"):
            raise ValueError("bad header from " + request.headers.get("authorization", ""))
        return None

    FakeShards(hook=leak_in_header).install(monkeypatch)
    responses.append(inspect(state, secrets={"HF_TOKEN": HF_SECRET}))
    # An unexpected error contributes its type name only, never its text.
    assert desk.only_issue(responses[-1], "invalid", "targets_failed")["message"] == "ValueError"
    FakeShards().install(monkeypatch)
    responses.append(inspect(state, secrets={"HF_TOKEN": HF_SECRET}))
    assert responses[-1]["status"] == "ok"
    text = json.dumps(responses)
    assert HF_SECRET not in text
    assert HF_SECRET.encode() not in wpt.state_bytes(state)
    assert HF_SECRET not in caplog.text
    assert dict(os.environ) == environment


@pytest.mark.parametrize("error", [TypeError, AttributeError, KeyError])
def test_unexpected_errors_stay_in_the_issue_envelope(tmp_path, monkeypatch, error):
    """9.2 review Minor 3, applied here too: an unexpected error is an issue with its type name only."""
    targets()
    state = tmp_path / "state"
    wpt.snapshot_root(state, monkeypatch)

    def fail(server, request, name, data, wanted):
        raise error("unexpected " + request.headers.get("authorization", ""))

    FakeShards(hook=fail).install(monkeypatch)
    result = inspect(state, secrets={"HF_TOKEN": HF_SECRET})
    assert desk.only_issue(result, "invalid", "targets_failed", "/repo_id")["message"] == error.__name__
    assert stored(state) == []


def test_inspector_imports_no_heavy_or_keyring_modules(tmp_path):
    targets()
    code = textwrap.dedent("""
        import json, sys
        import tuning_factory.hf_targets
        heavy = ("torch", "peft", "transformers", "safetensors", "huggingface_hub", "keyring", "paramiko",
                 "mlflow")
        print(json.dumps(sorted(name for name in heavy if name in sys.modules)))
    """)
    process = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True, timeout=120, cwd=tmp_path)
    assert process.returncode == 0, process.stderr
    assert json.loads(process.stdout) == []


# -- end to end: snapshot, targets, simulation plan, simulated train ----------------------------------

def test_snapshot_then_targets_lift_d15_and_run_a_simulated_lora_all_train(tmp_path, monkeypatch):
    targets()
    wp = wpt.planning()
    from tuning_factory.executor_profiles import SimulatedExecutorProvider
    from tuning_factory.graph_planner import ApprovalGrant
    from tuning_factory.graph_store import GraphStore, LedgerBindingResolver
    from tuning_factory.model_artifacts import HFRootRef
    from tuning_factory.operations import OperationResources
    from tuning_factory.simulated_dispatch import DirectoryRootSnapshots, SimulatedOperationDispatcher
    state = tmp_path / "state"
    dataset = wpt.register_data(state, tmp_path)
    default = wpt.save_recipe(state, "default")            # lora_target defaults to "all"
    root = wpt.snapshot_root(state, monkeypatch)
    datasets = [wpt.binding("train", "train", dataset)]
    project_id, _ = wpt.make_project(state, [wpt.train("train", default, wpt.from_root(root))], datasets)
    before = wpt.run(state, "graph_plan", wpt.plan_request(project_id))
    assert [item["code"] for item in before["data"]["warnings"]] == ["simulated_execution_needs_targets"]
    merge = {"kind": "merge", "id": "merged", "label": "병합", "models": [wpt.output("a"), wpt.output("b")],
             "weights": [0.5, 0.5], "executor_profile_ids": ["estimate-a"]}
    materialize = {"kind": "materialize", "id": "full", "label": "확정", "model": wpt.output("merged"),
                   "executor_profile_ids": ["estimate-a"]}
    nodes = [wpt.train("a", default, wpt.from_root(root)), wpt.train("b", default, wpt.from_root(root)),
             wpt.train("child", default, wpt.output("a")), merge, materialize]
    diamond_id, _ = wpt.make_project(state, nodes, datasets)
    diamond = wpt.plan_request(diamond_id, resources=tuple((node["id"], 600) for node in nodes))
    assert "base_targets_missing" in wpt.codes(wpt.run(state, "graph_plan", diamond))

    FakeShards().install(monkeypatch)
    assert inspect(state)["status"] == "ok"
    evidence = evidence_of(state, root)
    lifted = wpt.run(state, "graph_plan", diamond)
    assert lifted["status"] == "ok" and "warnings" not in lifted["data"], lifted
    planned = wpt.run(state, "graph_plan", wpt.plan_request(project_id))
    assert planned["status"] == "ok" and "warnings" not in planned["data"], planned
    plan = wp.stored_plan(state, planned["data"]["plan_sha256"])
    assert plan.simulation is True and plan.execution_choices.base_targets == (evidence,)

    store = GraphStore(state / "jobs.db")
    approval = ApprovalGrant.for_plan(plan)
    run = store.submit(plan, "targets-e2e", approval)
    store.binding_resolver = LedgerBindingResolver(store)
    controller = store.acquire_controller(run["id"], ttl_seconds=3600)
    [job_id] = store.prepare_ready(controller, node_ids=["train"])
    executor = OperationResources.model_validate(store.jobs.get(job_id)["manifest"]["operation"]["resources"]).executor
    provider = SimulatedExecutorProvider.for_plan(plan)
    with provider.resolve(executor.profile_id, executor.revision, pinned=executor) as resolved:
        claimed = store.claim_ready(resolved.observation, controller, job_id=job_id, executor=executor)
    dispatcher = SimulatedOperationDispatcher(store, root=state,
                                              roots=DirectoryRootSnapshots(wp.root_snapshots_dir(state)))
    outcome = dispatcher.run_claimed(claimed, None, approval=approval)
    assert outcome.status == "COMPLETED", outcome.error
    descriptor = store.artifacts.verify(outcome.outputs.model.model)
    assert descriptor.simulation and descriptor.effective_base == HFRootRef.model_validate(root)
    assert sorted(descriptor.adapter.target_modules) == LEAVES
    shapes = {tensor.name: tensor.shape for tensor in descriptor.tensors}
    modules = {name.removeprefix("base_model.model.").split(".lora_")[0] for name in shapes}
    assert modules == set(EXPECTED)
    for module, (out, inp) in EXPECTED.items():
        assert shapes[f"base_model.model.{module}.lora_A.weight"][1] == inp
        assert shapes[f"base_model.model.{module}.lora_B.weight"][0] == out
