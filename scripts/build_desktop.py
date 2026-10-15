"""Build a self-contained Windows sidecar. No credentials, state or data are bundled."""
import importlib.metadata
import shutil
import subprocess
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def run(*args):
    subprocess.run(args, cwd=ROOT, check=True)


def main():
    assets = ROOT / "build/desktop-assets"
    assets.mkdir(parents=True, exist_ok=True)
    for name in ("pyproject.toml", "uv.lock", "containers/gpu-requirements.lock", "containers/gpu-ppo-requirements.lock"):
        target = assets / name
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(ROOT / name, target)
    shutil.copytree(ROOT / "configs/ai", assets / "configs/ai", dirs_exist_ok=True)
    wheel_dir = assets / "dist"
    wheel_dir.mkdir(exist_ok=True)
    for old in wheel_dir.glob("*.whl"):
        old.unlink()
    run("uv", "build", "--wheel", "--out-dir", str(wheel_dir))
    commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=ROOT).decode().strip()
    (assets / "commit.txt").write_text(commit, encoding="utf-8")
    metadata = []
    for distribution in importlib.metadata.distributions():
        if distribution.metadata.get("Name"):
            metadata.extend(["--copy-metadata", distribution.metadata["Name"]])
    # Package sources are data too: source_hash() is byte-for-byte identical on both hosts.
    run("uv", "run", "--with", "pyinstaller==6.20.0", "pyinstaller", "--noconfirm", "--clean",
        "--onefile", "--console", "--name", "factory-bridge",
        "--paths", str(ROOT / "src"),
        "--add-data", str(assets) + ";assets",
        "--add-data", str(ROOT / "src/tuning_factory") + ";tuning_factory",
        "--collect-submodules", "tuning_factory", "--collect-all", "mlflow",
        "--collect-all", "boto3", "--collect-submodules", "botocore",
        "--additional-hooks-dir", str(ROOT / "scripts/pyinstaller-hooks"),
        *metadata, str(ROOT / "scripts/desktop_entry.py"))
    target = ROOT / "desktop/src-tauri/binaries/factory-bridge-x86_64-pc-windows-msvc.exe"
    target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(ROOT / "dist/factory-bridge.exe", target)
    print("Bundled sidecar:", target)


if __name__ == "__main__":
    main()
