import importlib.metadata
import subprocess
from pathlib import Path

from .registry import canonical, digest


def code_revision():
    result = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True)
    return result.stdout.strip() if result.returncode == 0 else "uncommitted"


def source_hash():
    root = Path(__file__).parent
    return digest(canonical({str(p.relative_to(root)): digest(p.read_bytes()) for p in sorted(root.rglob("*.py"))}))


def resolve(config, registry, simulation=True):
    dataset = registry.get_dataset(config.dataset_id, config.dataset_version)
    manifest = {"schema_version": 1, "config": config.model_dump(), "dataset": dataset,
                "ai_revision": registry.register_ai(config), "code_commit": code_revision(),
                "source_sha256": source_hash(), "simulation": simulation,
                "versions": {name: importlib.metadata.version(name)
                             for name in ("llm-tuning-factory", "pydantic", "PyYAML")},
                "training_engine": "llamafactory==0.9.5"}
    manifest["sha256"] = digest(canonical(manifest))
    return manifest


def verify_manifest(manifest):
    expected = manifest.get("sha256")
    if digest(canonical({k: v for k, v in manifest.items() if k != "sha256"})) != expected:
        raise ValueError("manifest hash mismatch")
