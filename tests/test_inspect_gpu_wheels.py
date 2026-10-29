import importlib.util
import io
import zipfile
from pathlib import Path

import pytest

SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "inspect_gpu_wheels.py"
SPEC = importlib.util.spec_from_file_location("inspect_gpu_wheels", SCRIPT)
gate = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(gate)


def archive(member, data):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w", zipfile.ZIP_DEFLATED) as wheel:
        wheel.writestr(member, data)
    buffer.seek(0)
    return zipfile.ZipFile(buffer)


def test_the_longest_compiled_flag_list_becomes_versions():
    data = b"\x00sm_80\x00sm_75 sm_80 sm_86 sm_90 sm_100 sm_120 compute_120\x00tail"
    with archive(gate.TORCH_LIBRARY, data) as wheel:
        flags = gate.parse_arch_flags(wheel.read(gate.TORCH_LIBRARY))
    assert flags == {"sass": ["7.5", "8.0", "8.6", "9.0", "10.0", "12.0"], "ptx": ["12.0"]}
    assert gate.unsupported(flags) == []


def test_a_build_without_blackwell_code_is_reported():
    flags = gate.parse_arch_flags(b"sm_50 sm_60 sm_70 sm_75 sm_80 sm_86 sm_90")
    assert gate.unsupported(flags) == ["10.0", "12.0"]


def test_a_missing_flag_list_is_an_error():
    with pytest.raises(ValueError, match="not found"):
        gate.parse_arch_flags(b"sm_80 only once")


def test_bitsandbytes_cuda128_library_is_recognised():
    assert gate.has_cuda128_library(["bitsandbytes/libbitsandbytes_cuda126.so",
                                     "bitsandbytes/libbitsandbytes_cuda128.so"])
    assert not gate.has_cuda128_library(["bitsandbytes/libbitsandbytes_cuda126.so"])
