"""Repository text hygiene: no invisible bidirectional controls in any tracked text file (Task 10.8 review M5).

Embedding, override and isolate controls and the left-to-right, right-to-left and Arabic letter marks are invisible in
an editor, trigger GitHub's bidirectional-text warning and the Trojan-Source linters, and can make code read
differently from how it runs. Code that needs one writes an escape. The forbidden code points are built from integers,
so this file holds none.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
# U+202A-U+202E embeddings and overrides, U+2066-U+2069 isolates, U+200E/U+200F and U+061C the directional marks.
FORBIDDEN = frozenset(chr(code) for code in (0x202A, 0x202B, 0x202C, 0x202D, 0x202E, 0x2066, 0x2067, 0x2068, 0x2069,
                                             0x200E, 0x200F, 0x061C))


def tracked_files() -> list[Path]:
    git = shutil.which("git")
    if git is None or not (ROOT / ".git").exists():
        pytest.skip("needs a git checkout to list the tracked files")
    listed = subprocess.run([git, "ls-files", "-z"], cwd=ROOT, check=True, capture_output=True).stdout
    return [ROOT / name for name in listed.decode("utf-8").split("\0") if name]


def findings(paths: list[Path]) -> list[str]:
    found = []
    for path in paths:
        try:
            data = path.read_bytes()
        except (FileNotFoundError, IsADirectoryError, PermissionError):
            continue  # files deleted in the working tree
        if bytes([0]) in data:
            continue  # binary
        # An invalid byte becomes U+FFFD, so the rest of a mostly-text file is still scanned (10.8 re-review).
        text = data.decode("utf-8", errors="replace")
        for number, line in enumerate(text.split("\n"), start=1):
            codes = sorted({f"U+{ord(char):04X}" for char in line if char in FORBIDDEN})
            if codes:
                name = path.relative_to(ROOT).as_posix() if path.is_relative_to(ROOT) else path.name
                found.append(f"{name}:{number}: {', '.join(codes)}")
    return found


def test_no_tracked_text_file_holds_a_bidirectional_control() -> None:
    files = tracked_files()
    assert len(files) > 100  # the listing worked
    assert findings(files) == []


def test_the_scan_reports_each_forbidden_code_point(tmp_path: Path) -> None:
    sample = tmp_path / "sample.ts"
    sample.write_text("const ok = 1;\n" + "".join(f"x{char}" for char in sorted(FORBIDDEN)) + "\n", encoding="utf-8")
    (tmp_path / "binary.bin").write_bytes(bytes(range(256)))
    report = findings([sample, tmp_path / "binary.bin"])
    assert len(report) == 1
    assert report[0].endswith(", ".join(f"U+{ord(char):04X}" for char in sorted(FORBIDDEN)))
    assert len(FORBIDDEN) == 12


def test_a_mostly_text_file_is_scanned_and_a_nul_byte_marks_a_binary(tmp_path: Path) -> None:
    """10.8 re-review: one invalid UTF-8 byte must not hide a control in an otherwise text file (it decodes with
    replacement), while a file holding a NUL byte is binary and skipped. The Arabic letter mark is forbidden too."""
    override, arabic_mark = chr(0x202E), chr(0x061C)
    mixed = tmp_path / "mixed.txt"
    mixed.write_bytes(b"plain\n" + bytes([0xFF]) + ("x" + override).encode("utf-8") + b"\n")
    marked = tmp_path / "marked.md"
    marked.write_bytes(("a" + arabic_mark + "b\n").encode("utf-8"))
    binary = tmp_path / "blob.dat"
    binary.write_bytes(bytes([0]) + override.encode("utf-8"))
    assert findings([mixed, marked, binary]) == ["mixed.txt:2: U+202E", "marked.md:1: U+061C"]
    assert arabic_mark in FORBIDDEN
