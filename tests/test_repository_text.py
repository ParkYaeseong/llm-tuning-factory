"""Repository text hygiene: no invisible bidirectional controls in any tracked text file (Task 10.8 review M5).

Embedding, override and isolate controls and the left-to-right/right-to-left marks are invisible in an editor, trigger
GitHub's bidirectional-text warning and the Trojan-Source linters, and can make code read differently from how it runs.
Code that needs one writes an escape. The forbidden code points are built from integers, so this file holds none.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
FORBIDDEN = frozenset(chr(code) for code in (*range(0x202A, 0x202F), *range(0x2066, 0x206A), 0x200E, 0x200F))


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
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, FileNotFoundError, IsADirectoryError):
            continue  # binary files, and files deleted in the working tree
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
    assert len(FORBIDDEN) == 11
