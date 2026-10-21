"""The Windows release version (Task 11 plan D1) and the desktop CI path filters (D16)."""
import json
import re
import tomllib
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parents[1]
RELEASE = "0.4.0"


def read(path: str) -> str:
    return (ROOT / path).read_text(encoding="utf-8")


def desktop_versions() -> dict[str, str]:
    package, lock = json.loads(read("desktop/package.json")), json.loads(read("desktop/package-lock.json"))
    cargo = tomllib.loads(read("desktop/src-tauri/Cargo.toml"))
    cargo_lock = tomllib.loads(read("desktop/src-tauri/Cargo.lock"))
    (own,) = [item for item in cargo_lock["package"] if item["name"] == cargo["package"]["name"]]
    return {"package.json": package["version"], "package-lock.json": lock["version"],
            "package-lock.json packages['']": lock["packages"][""]["version"],
            "Cargo.toml": cargo["package"]["version"], "Cargo.lock": own["version"],
            "tauri.conf.json": json.loads(read("desktop/src-tauri/tauri.conf.json"))["version"]}


def test_every_desktop_version_field_is_the_release():
    versions = desktop_versions()
    assert versions == dict.fromkeys(versions, RELEASE)


def test_the_native_smoke_reads_the_version_instead_of_a_literal():
    smoke = read("desktop/tests/native-smoke.mjs")
    assert not re.search(r"내부 검토 \d+\.\d+\.\d+", smoke)
    assert "../package.json" in smoke


def test_the_engine_package_keeps_its_own_version():
    """pyproject.toml and uv.lock bytes are hashed into every manifest (manifest.py:56-64)."""
    assert tomllib.loads(read("pyproject.toml"))["project"]["version"] == "0.1.0"


def test_the_desktop_package_is_rebuilt_when_bundled_inputs_change():
    """build_desktop.py bundles configs/ai and the tutorial sample under examples/; 11.3b's verifier seeds a root."""
    workflow = yaml.safe_load(read(".github/workflows/windows-desktop.yml"))
    triggers = workflow.get("on", workflow.get(True))   # YAML 1.1 reads a bare `on` key as true
    paths = triggers["pull_request"]["paths"]
    assert {"examples/**", "configs/ai/**", "scripts/seed_acceptance_root.py"} <= set(paths)
    assert len(paths) == len(set(paths))
