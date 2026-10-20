"""Streamed, standard-library NaN/inf scan of safetensors files.

Used before any heavy import so that non-finite adapter inputs are refused without loading torch.
"""
from __future__ import annotations

import math
from pathlib import Path

from .model_artifacts import DTYPE_BYTES, JSON_LIMIT, _json, _open_regular, _tensor_header

# dtype: (width, high byte, high exponent mask, low byte, low exponent mask). A value is NaN/inf
# exactly when every exponent bit is set; little-endian byte order as stored by safetensors.
_EXPONENT_LAYOUT = {
    "F16": (2, 1, 0x7C, 0, 0x00),
    "BF16": (2, 1, 0x7F, 0, 0x80),
    "F32": (4, 3, 0x7F, 2, 0x80),
    "F64": (8, 7, 0x7F, 6, 0xF0),
}
_INTEGER_DTYPES = frozenset({"BOOL", "U8", "I8", "I16", "U16", "I32", "U32", "I64", "U64"})
_SCAN_CHUNK = 8 * 1024 * 1024  # bytes; a multiple of every float width
_CANDIDATES = {mask: bytes(1 if byte & mask == mask else 0 for byte in range(256))
               for mask in {layout[2] for layout in _EXPONENT_LAYOUT.values()}}


def scan_finite_safetensors(path: Path) -> int:
    """Stream a safetensors file and refuse NaN/inf; return the number of float elements scanned.

    Integer tensors are finite by construction and are skipped; FP8 and unknown dtypes are refused.
    """
    path = Path(path)
    validated = {tensor.name: tensor for tensor in _tensor_header(path, path.name)}
    if any(tensor.dtype not in _EXPONENT_LAYOUT and tensor.dtype not in _INTEGER_DTYPES
           for tensor in validated.values()):
        raise ValueError("unsupported tensor dtype for finite scan")
    scanned = 0
    with _open_regular(path) as stream:
        length = int.from_bytes(stream.read(8), "little")
        if not 1 <= length <= JSON_LIMIT:   # bound the re-read header like the validated one
            raise ValueError("invalid safetensors header length")
        header = _json(stream.read(length))
        try:   # the validated geometry must still describe the bytes opened here
            entries = sorted((header[name]["data_offsets"][0], header[name]["data_offsets"][1], name)
                             for name in validated)
            if set(header) - {"__metadata__"} != set(validated) or any(
                    (header[name]["dtype"], tuple(header[name]["shape"]))
                    != (validated[name].dtype, validated[name].shape) for name in validated) or any(
                    not 0 <= start <= end or end - start
                    != math.prod(validated[name].shape) * DTYPE_BYTES[validated[name].dtype]
                    for start, end, name in entries):
                raise ValueError
        except (KeyError, TypeError, IndexError, ValueError):
            raise ValueError("safetensors header changed while scanning") from None
        for start, end, name in entries:
            dtype = validated[name].dtype
            if dtype in _INTEGER_DTYPES:
                continue
            width, high, high_mask, low, low_mask = _EXPONENT_LAYOUT[dtype]
            table = _CANDIDATES[high_mask]
            stream.seek(8 + length + start)
            remaining = end - start
            while remaining:
                chunk = stream.read(min(_SCAN_CHUNK, remaining))
                if not chunk or len(chunk) % width:
                    raise ValueError("truncated safetensors payload")
                marks = chunk[high::width].translate(table)
                position = marks.find(1)
                while position != -1:
                    if not low_mask or chunk[position * width + low] & low_mask == low_mask:
                        raise ValueError(f"non-finite tensor values in {path.name}: {name}")
                    position = marks.find(1, position + 1)
                remaining -= len(chunk)
            scanned += math.prod(validated[name].shape)
    return scanned
