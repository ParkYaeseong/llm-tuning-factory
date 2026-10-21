"""Write the synthetic acceptance root ``factory-acceptance/tiny-qwen2`` into a state directory (offline).

Acceptance only: never bundled in the installer. The root plans and runs simulated graphs; a real run on it fails at
the worker, because no such Hugging Face repository exists.
Usage: uv run python scripts/seed_acceptance_root.py --state-dir <state>   (prints one JSON line)

What it writes, through the public functions ``model_root_snapshot`` and ``model_root_targets`` use (Task 11 plan
D15, F15); nothing is downloaded and no weight file is written:

- ``<state>/workflows/roots/factory-acceptance/tiny-qwen2/<REVISION>/``: ``config.json`` (a 2-layer Qwen2 with hidden
  size 16) and a WordLevel tokenizer (``tokenizer.json``, ``tokenizer_config.json``), as canonical JSON bytes;
- the root index entry (``workflow_planning.save_root``) with the ``ModelProfile`` that the legacy ``model_inspect``
  path builds (``ModelHub.inspect``), here answered from the local bytes by an in-process transport;
- the module evidence (``workflow_planning.save_root_targets``) of a synthetic ``model.safetensors`` header for every
  Qwen2 linear, embedding, norm and ``lm_head`` tensor, validated by ``model_artifacts.header_tensors`` and reduced by
  ``hf_targets.target_evidence``, the rules of a header-only HTTP read.

The revision is the SHA-1 of ``factory-acceptance/tiny-qwen2@1``. Seeding is idempotent: a second run over the same
state rewrites nothing and prints the same line. A snapshot directory with other bytes is refused (exit 1).

Recipes planned on this root need ``hyperparameters.cutoff_len`` of 64 (``MAX_LENGTH``): the profile's reviewed limit
is the config's ``max_position_embeddings``, so the default 1024 is refused by ``graph_plan`` ("cutoff_len exceeds
reviewed model limit 64").
"""
from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import shutil
import sys
import tempfile
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
if str(REPO / "src") not in sys.path:
    sys.path.insert(0, str(REPO / "src"))

REPO_ID = "factory-acceptance/tiny-qwen2"
REVISION = hashlib.sha1(b"factory-acceptance/tiny-qwen2@1").hexdigest()
CONFIG = {"architectures": ["Qwen2ForCausalLM"], "model_type": "qwen2", "hidden_size": 16,
          "intermediate_size": 32, "num_hidden_layers": 2, "num_attention_heads": 2, "num_key_value_heads": 2,
          "vocab_size": 32, "max_position_embeddings": 64, "torch_dtype": "bfloat16", "tie_word_embeddings": False}
# The WordLevel vocabulary of tests/test_model_inputs.write_snapshot: one special token, so tokenizer comparisons
# have real ids to compare.
TOKENIZER = {
    "tokenizer.json": {"version": "1.0", "added_tokens": [{"id": 0, "content": "<unk>", "special": True}],
                       "model": {"type": "WordLevel", "unk_token": "<unk>", "vocab": {"<unk>": 0, "q": 1, "v": 2}}},
    "tokenizer_config.json": {"added_tokens_decoder": {"0": {"content": "<unk>", "special": True}}},
}
MAX_LENGTH = CONFIG["max_position_embeddings"]   # the largest cutoff_len a recipe on this root may use
WEIGHTS_FILE = "model.safetensors"   # the synthetic header's file name; the file itself is never written
DTYPE_WIDTH = {"BF16": 2}
SNAPSHOT_CONFLICT = "the acceptance root's snapshot directory exists with other content; remove it and seed again"
INDEX_CONFLICT = "the state directory already indexes this root with another profile; remove it and seed again"


def _canonical(data) -> bytes:
    from tuning_factory.registry import canonical
    return canonical(data)


def qwen2_shapes(config: dict) -> dict[str, tuple[int, ...]]:
    """Every tensor of a ``Qwen2ForCausalLM`` checkpoint of this config: embeddings, per-layer norms, the biased
    q/k/v and unbiased o projections, the MLP, the final norm and (untied) ``lm_head``."""
    hidden, inner, vocab = config["hidden_size"], config["intermediate_size"], config["vocab_size"]
    head = hidden // config["num_attention_heads"]
    query, key_value = config["num_attention_heads"] * head, config["num_key_value_heads"] * head
    shapes = {"model.embed_tokens.weight": (vocab, hidden)}
    for index in range(config["num_hidden_layers"]):
        prefix = f"model.layers.{index}."
        shapes.update({
            prefix + "input_layernorm.weight": (hidden,), prefix + "post_attention_layernorm.weight": (hidden,),
            prefix + "self_attn.q_proj.weight": (query, hidden), prefix + "self_attn.q_proj.bias": (query,),
            prefix + "self_attn.k_proj.weight": (key_value, hidden), prefix + "self_attn.k_proj.bias": (key_value,),
            prefix + "self_attn.v_proj.weight": (key_value, hidden), prefix + "self_attn.v_proj.bias": (key_value,),
            prefix + "self_attn.o_proj.weight": (hidden, query),
            prefix + "mlp.gate_proj.weight": (inner, hidden), prefix + "mlp.up_proj.weight": (inner, hidden),
            prefix + "mlp.down_proj.weight": (hidden, inner)})
    shapes["model.norm.weight"] = (hidden,)
    if not config.get("tie_word_embeddings", False):
        shapes["lm_head.weight"] = (vocab, hidden)
    return shapes


def safetensors_header(shapes: dict[str, tuple[int, ...]], dtype: str = "BF16") -> tuple[dict, int]:
    """A parsed safetensors header with exact, contiguous offsets (sorted by name), and its payload size."""
    header, offset = {"__metadata__": {"format": "pt"}}, 0
    for name in sorted(shapes):
        size = DTYPE_WIDTH[dtype] * math.prod(shapes[name])
        header[name] = {"dtype": dtype, "shape": list(shapes[name]), "data_offsets": [offset, offset + size]}
        offset += size
    return header, offset


def inspected_profile(repo_id: str, revision: str, config: bytes, tokenizer_config: bytes):
    """The ``ModelProfile`` of the legacy ``model_inspect`` path (``ModelHub.inspect``), answered from these local
    bytes by an in-process ``httpx.MockTransport``: nothing leaves the process."""
    import httpx

    from tuning_factory.model_hub import ModelHub, ModelProfile
    files = {f"/{repo_id}/resolve/{revision}/config.json": config,
             f"/{repo_id}/resolve/{revision}/tokenizer_config.json": tokenizer_config}

    def respond(request: httpx.Request) -> httpx.Response:
        if request.url.path == f"/api/models/{repo_id}/revision/{revision}":
            return httpx.Response(200, json={"sha": revision, "gated": False})
        if request.url.path in files:
            return httpx.Response(200, content=files[request.url.path])
        return httpx.Response(404)

    client = httpx.Client(transport=httpx.MockTransport(respond), trust_env=False, follow_redirects=False)
    try:
        inspected = ModelHub(client=client).inspect(repo_id, revision)
    finally:
        client.close()
    if not inspected.get("supported") or not inspected.get("profile"):
        raise ValueError("the acceptance root is not a supported model: " + "; ".join(inspected.get("reasons") or ()))
    return ModelProfile.model_validate(inspected["profile"])


def _publish_snapshot(state: Path, repo_id: str, revision: str, files: dict[str, bytes]):
    """Write the config/tokenizer snapshot into place (renamed complete, never partial) and return its root ref. An
    existing directory is kept only if it is exactly this root (``DirectoryRootSnapshots``)."""
    from tuning_factory.model_artifacts import HFRootRef, tokenizer_fingerprint
    from tuning_factory.registry import digest
    from tuning_factory.simulated_dispatch import DirectoryRootSnapshots
    from tuning_factory.workflow_planning import root_snapshots_dir
    roots = root_snapshots_dir(state)
    owner, name = repo_id.split("/")
    final = roots / owner / name / revision
    final.parent.mkdir(parents=True, exist_ok=True)
    temporary = Path(tempfile.mkdtemp(prefix=".seed-", dir=roots))
    try:
        for relative, data in sorted(files.items()):
            (temporary / relative).write_bytes(data)
        root = HFRootRef(repo_id=repo_id, revision=revision, config_sha256=digest(files["config.json"]),
                         tokenizer_fingerprint=tokenizer_fingerprint(temporary))
        if not final.exists():
            try:
                os.rename(temporary, final)
            except OSError:
                if not final.exists():
                    raise
        if DirectoryRootSnapshots(roots).snapshot(root) is None:
            raise ValueError(SNAPSHOT_CONFLICT)
    finally:
        shutil.rmtree(temporary, ignore_errors=True)
    return root


def write_root(state_dir: str | Path, repo_id: str, revision: str, config: dict,
               tokenizer: dict | None = None) -> dict:
    """Seed one synthetic HF root (the snapshot, its index entry and its module evidence) and return
    ``{"root": ..., "modules": n}``. ``seed`` is this for ``REPO_ID@REVISION``; acceptance tests also use it for a
    second root."""
    from tuning_factory.graph_planner import ModelContext
    from tuning_factory.hf_targets import target_evidence
    from tuning_factory.model_artifacts import header_tensors
    from tuning_factory.workflow_planning import save_root, save_root_targets
    state = Path(state_dir).absolute()
    files = {"config.json": _canonical(config),
             **{name: _canonical(body) for name, body in (TOKENIZER if tokenizer is None else tokenizer).items()}}
    root = _publish_snapshot(state, repo_id, revision, files)
    profile = inspected_profile(repo_id, revision, files["config.json"], files["tokenizer_config.json"])
    entry = ModelContext(root=root, profile=profile)
    if save_root(state, entry) != entry:   # the first entry of a root wins: it must be this one
        raise ValueError(INDEX_CONFLICT)
    header, payload = safetensors_header(qwen2_shapes(config))
    evidence = target_evidence(root, header_tensors(header, payload, WEIGHTS_FILE))
    save_root_targets(state, evidence)     # immutable: other evidence of this root is refused
    return {"root": root.model_dump(mode="json"), "modules": len(evidence.modules)}


def seed(state_dir: str | Path) -> dict:
    """Idempotent: the acceptance root ``REPO_ID@REVISION`` in ``state_dir``; ``{"root": ..., "modules": n}``."""
    return write_root(state_dir, REPO_ID, REVISION, CONFIG)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="seed_acceptance_root.py",
        description="Write the synthetic acceptance root factory-acceptance/tiny-qwen2 into a state directory "
                    "(offline; acceptance only).")
    parser.add_argument("--state-dir", required=True, type=Path, help="the factory state directory to seed")
    args = parser.parse_args(argv)
    try:
        result = seed(args.state_dir)
    except ValueError as exc:
        print(f"seed_acceptance_root: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    sys.exit(main())
