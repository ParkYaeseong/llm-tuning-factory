"""Release gate for the CUDA 12.8 GPU locks: no install and no GPU.

Reads the CUDA architectures compiled into the pinned torch wheel and checks that bitsandbytes ships a CUDA 12.8
library. Wheel members are read with HTTP range requests, so only the zip directory and one library are fetched.
Usage: uv run python scripts/inspect_gpu_wheels.py [--lock containers/gpu-requirements.lock]
"""
import argparse
import io
import json
import re
import sys
import zipfile
from pathlib import Path

import httpx

from tuning_factory.gpu_compat import capability_supported, version_tuple

TORCH_INDEX = "https://download.pytorch.org/whl/cu128/torch/"
TORCH_WHEEL = "torch-2.7.1+cu128-cp312-cp312-manylinux_2_28_x86_64.whl"
TORCH_LIBRARY = "torch/lib/libtorch_python.so"   # holds the flag list torch.cuda.get_arch_list() splits
BITSANDBYTES_JSON = "https://pypi.org/pypi/bitsandbytes/0.48.1/json"
REQUIRED = ("8.0", "8.6", "8.9", "9.0", "10.0", "12.0")   # Ampere, Ada, Hopper, Blackwell (B200, RTX PRO)
ARCH_FLAGS = re.compile(rb"(?:sm|compute)_\d+[a-z]?(?: (?:sm|compute)_\d+[a-z]?)+")


def parse_arch_flags(data):
    """The longest space-separated sm_/compute_ list in a binary, as {"sass": [...], "ptx": [...]} versions."""
    matches = ARCH_FLAGS.findall(data)
    if not matches:
        raise ValueError("compiled CUDA architecture list not found")
    sass, ptx = set(), set()
    for token in max(matches, key=len).decode("ascii").split():
        kind, digits = re.fullmatch(r"(sm|compute)_(\d+)[a-z]?", token).groups()
        (sass if kind == "sm" else ptx).add(f"{int(digits[:-1])}.{digits[-1]}")
    return {"sass": sorted(sass, key=version_tuple), "ptx": sorted(ptx, key=version_tuple)}


def unsupported(flags):
    return [code for code in REQUIRED if not capability_supported(code, flags["sass"], flags["ptx"])]


def has_cuda128_library(names):
    return any(re.search(r"(^|/)libbitsandbytes_cuda128[^/]*\.so$", name) for name in names)


class HTTPRangeFile(io.RawIOBase):
    """A read-only seekable view of one URL for zipfile; refuses servers without byte ranges."""

    def __init__(self, client, url):
        self.client, self.url, self.position = client, url, 0
        head = client.head(url, follow_redirects=True)
        head.raise_for_status()
        if head.headers.get("accept-ranges") != "bytes":
            raise ValueError("server does not support range requests: " + url)
        self.size = int(head.headers["content-length"])

    def readable(self):
        return True

    def seekable(self):
        return True

    def tell(self):
        return self.position

    def seek(self, offset, whence=io.SEEK_SET):
        base = {io.SEEK_SET: 0, io.SEEK_CUR: self.position, io.SEEK_END: self.size}[whence]
        self.position = max(0, base + offset)
        return self.position

    def readinto(self, buffer):
        if self.position >= self.size:
            return 0
        end = min(self.size, self.position + len(buffer)) - 1
        response = self.client.get(self.url, headers={"Range": f"bytes={self.position}-{end}"},
                                   follow_redirects=True)
        if response.status_code != 206:
            raise ValueError("range request refused: " + self.url)
        data = response.content
        buffer[:len(data)] = data
        self.position += len(data)
        return len(data)


def remote_zip(client, url):
    return zipfile.ZipFile(io.BufferedReader(HTTPRangeFile(client, url), buffer_size=8 << 20))


def torch_wheel_url(client, lock_text):
    page = client.get(TORCH_INDEX, follow_redirects=True)
    page.raise_for_status()
    name = re.escape(TORCH_WHEEL.replace("+", "%2B"))
    match = re.search(r'href="([^"#]*' + name + r')#sha256=([0-9a-f]{64})"', page.text)
    if not match:
        raise ValueError("pinned torch wheel is not listed on the PyTorch cu128 index")
    if match.group(2) not in lock_text:
        raise ValueError("PyTorch index hash differs from the lock")
    return str(httpx.URL(TORCH_INDEX).join(match.group(1)))


def bitsandbytes_wheel_url(client):
    response = client.get(BITSANDBYTES_JSON, follow_redirects=True)
    response.raise_for_status()
    for item in response.json()["urls"]:
        if item["filename"].endswith(".whl") and "manylinux" in item["filename"] and "x86_64" in item["filename"]:
            return item["url"]
    raise ValueError("bitsandbytes 0.48.1 Linux x86_64 wheel not found")


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--lock", default="containers/gpu-requirements.lock")
    args = parser.parse_args(argv)
    lock_text = Path(args.lock).read_text(encoding="utf-8")
    with httpx.Client(timeout=60) as client:
        url = torch_wheel_url(client, lock_text)
        with remote_zip(client, url) as wheel:
            flags = parse_arch_flags(wheel.read(TORCH_LIBRARY))
        with remote_zip(client, bitsandbytes_wheel_url(client)) as wheel:
            cuda128 = has_cuda128_library(wheel.namelist())
    missing = unsupported(flags)
    print(json.dumps({"torch_wheel": url, **flags, "unsupported_required": missing,
                      "bitsandbytes_cuda128": cuda128}, indent=2))
    return 0 if not missing and cuda128 else 1


if __name__ == "__main__":
    sys.exit(main())
