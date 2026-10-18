import importlib.metadata
import subprocess
import sys
from pathlib import Path

from .registry import canonical, digest


def code_revision():
    if hasattr(sys, "_MEIPASS"):
        return (Path(sys._MEIPASS) / "assets/commit.txt").read_text().strip()
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def source_hash():
    root = Path(__file__).parent
    return digest(canonical({p.relative_to(root).as_posix(): digest(p.read_bytes()) for p in sorted(root.rglob("*.py"))}))


def resolve(config, registry, simulation=True):
    from .config import GraphTrainingConfig
    if isinstance(config, GraphTrainingConfig):
        raise ValueError("graph training requires resolve_operation and verified model bindings")
    from .capabilities import STAGES
    from .runtime_profiles import engine_profile
    from .training import LlamaFactoryBackend
    dataset = registry.get_dataset(config.dataset_id, config.dataset_version)
    expected_kind = STAGES[config.training_stage][1]
    if dataset.get("kind", "instruction") != expected_kind:
        raise ValueError(f"dataset kind {dataset.get('kind', 'instruction')} is incompatible with {config.training_stage}; "
                         f"register {expected_kind} data")
    profile = engine_profile(config.training_stage)
    project_root = (Path(sys._MEIPASS) / "assets" if hasattr(sys, "_MEIPASS")
                    else Path(__file__).resolve().parents[2])
    locks = {name: digest((project_root / name).read_bytes()) for name in
             ("pyproject.toml", "uv.lock", profile["lock_file"]) if (project_root / name).is_file()}
    manifest = {"schema_version": 1, "config": config.model_dump(), "dataset": dataset,
                "ai_revision": registry.register_ai(config), "code_commit": code_revision(),
                "source_sha256": source_hash(), "simulation": simulation,
                "training_config_sha256": digest(canonical(LlamaFactoryBackend.translate(config))),
                "dependency_file_hashes": locks,
                "versions": {name: importlib.metadata.version(name)
                             for name in ("llm-tuning-factory", "pydantic", "PyYAML")},
                "training_engine": "llamafactory==" + profile["engine_version"],
                "runtime_profile": profile["profile"]}
    if config.evaluation_profile.dataset_id:
        manifest["validation_dataset"] = registry.get_dataset(config.evaluation_profile.dataset_id,
                                                               config.evaluation_profile.dataset_version)
        validation_kind = "preference" if config.evaluation_profile.kind == "reward" else "instruction"
        if manifest["validation_dataset"].get("kind", "instruction") != validation_kind:
            raise ValueError(f"validation dataset kind must be {validation_kind}")
    manifest["sha256"] = digest(canonical(manifest))
    return manifest


def verify_manifest(manifest):
    expected = manifest.get("sha256")
    if digest(canonical({k: v for k, v in manifest.items() if k != "sha256"})) != expected:
        raise ValueError("manifest hash mismatch")


def resolve_operation(operation, registry):
    """Hash a path-free v2 operation; preserve v1's body and hashing contract."""
    from pydantic import TypeAdapter

    from .graph_planner import snapshot_dataset, validate_sample_ids
    from .operations import EvaluateOperation, OperationSpec, TrainOperation

    value = operation.model_dump(mode="json") if hasattr(operation, "model_dump") else operation
    operation = TypeAdapter(OperationSpec).validate_python(value)
    datasets = []
    if isinstance(operation, TrainOperation):
        datasets.append(snapshot_dataset(registry, operation.dataset))
    elif isinstance(operation, EvaluateOperation):
        snapshot = snapshot_dataset(registry, operation.benchmark.dataset)
        validate_sample_ids(operation.benchmark, snapshot)
        datasets.append(snapshot)
    body = {"schema_version": 2, "operation": operation.model_dump(mode="json"),
            "datasets": [item.model_dump(mode="json") for item in datasets],
            "code_commit": code_revision(), "source_sha256": source_hash()}
    body["sha256"] = digest(canonical(body))
    return body
