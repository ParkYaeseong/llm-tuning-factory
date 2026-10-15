"""Check staged files and reachable Git history without printing secret values.

Optional credential files are read only to compare their values with Git blobs.
This is a small release guard, not an exhaustive secret detector.
"""
import argparse
import re
import subprocess
from pathlib import Path

PATTERNS = {
    "private key": rb"-----BEGIN (?:RSA |EC |OPENSSH |DSA )?PRIVATE KEY-----",
    "GitHub token": rb"\bgh[pousr]_[A-Za-z0-9]{30,}\b|\bgithub_pat_[A-Za-z0-9_]{40,}\b",
    "Hugging Face token": rb"\bhf_[A-Za-z0-9]{24,}\b",
    "RunPod token": rb"\brpa_[A-Za-z0-9_]{24,}\b",
    "AWS access key": rb"\b(?:AKIA|ASIA)[A-Z0-9]{16}\b",
}


def git(*args):
    return subprocess.check_output(["git", *args])


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--credential-file", action="append", type=Path, default=[])
    args = parser.parse_args()
    patterns = {name: re.compile(value) for name, value in PATTERNS.items()}
    secrets = set()
    for path in [Path(".env"), *args.credential_file]:
        if not path.is_file():
            continue
        for line in path.read_text(encoding="utf-8-sig").splitlines():
            match = re.fullmatch(r"\s*(?:export )?(RUNPOD_API_KEY|MLFLOW_OIDC_CLIENT_SECRET|"
                                 r"MLFLOW_TRACKING_TOKEN|Client_Secret|S3_ACCESS_KEY_ID|S3_SECRET_ACCESS_KEY|"
                                 r"S3_SESSION_TOKEN|AWS_SECRET_ACCESS_KEY|AWS_SESSION_TOKEN)\s*[:=]\s*(.*?)\s*", line)
            if match:
                value = match[2].strip("\"'")
                if len(value) >= 8:
                    secrets.add(value.encode())

    objects = {}
    for line in git("rev-list", "--objects", "--all").decode().splitlines():
        sha, _, name = line.partition(" ")
        objects[sha] = name or "historical object"
    for line in git("ls-files", "--stage").decode().splitlines():
        metadata, name = line.split("\t", 1)
        objects[metadata.split()[1]] = name
    process = subprocess.run(["git", "cat-file", "--batch"],
                             input="".join(sha + "\n" for sha in objects).encode(),
                             capture_output=True, check=True)
    data, offset, findings, checked = process.stdout, 0, [], 0
    for sha, name in objects.items():
        end = data.index(b"\n", offset)
        header = data[offset:end].split()
        size = int(header[2])
        content = data[end + 1:end + 1 + size]
        offset = end + 2 + size
        if header[1] != b"blob":
            continue
        checked += 1
        for label, pattern in patterns.items():
            if pattern.search(content):
                findings.append((name, label))
        if any(secret in content for secret in secrets):
            findings.append((name, "known credential value"))
        filename = Path(name).name
        if (filename == ".env" or filename.startswith(".env.") or filename.endswith(".env")) \
                and not filename.endswith(".example"):
            findings.append((name, "credential file tracked"))
    for name, label in sorted(set(findings)):
        print(f"BLOCKED: {name}: {label}")
    print(f"Checked {checked} staged/historical file versions; {len(set(findings))} findings.")
    return bool(findings)


if __name__ == "__main__":
    raise SystemExit(main())
