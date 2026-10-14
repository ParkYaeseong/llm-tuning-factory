"""Bounded desktop views of local datasets, actual trainer logs and parameter changes."""
import json
import math
from pathlib import Path

from .registry import canonical, digest, parse_dataset

PREVIEW_BYTES = 32 * 1024 * 1024


def inspect_dataset(path):
    path = Path(path)
    with path.open("rb") as handle:
        raw = handle.read(PREVIEW_BYTES + 1)
    if len(raw) > PREVIEW_BYTES:
        raise ValueError("미리보기는 32 MiB 이하 파일을 지원합니다. 큰 데이터는 버전을 나누어 검증하세요.")
    rows, formatting, file_format = parse_dataset(raw)
    seen = {digest(canonical(row)) for row in rows}

    def preview(value):
        if isinstance(value, str):
            return value[:2000] + ("…" if len(value) > 2000 else "")
        if isinstance(value, list):
            return [preview(x) for x in value[:8]]
        if isinstance(value, dict):
            return {key: preview(x) for key, x in list(value.items())[:12]}
        return value

    return {"rows": len(rows), "format": formatting, "file_format": file_format, "bytes": len(raw),
            "sha256": digest(raw), "duplicate_rows": len(rows) - len(seen),
            "samples": [preview(row) for row in rows[:3]], "sample_content_truncated": True}


def parse_progress(text):
    points = {}
    latest = {}
    for line in text.splitlines():
        try:
            record = json.loads(line)
        except (ValueError, TypeError):
            continue
        if not isinstance(record, dict):
            continue
        step = record.get("current_steps")
        if not isinstance(step, int) or isinstance(step, bool) or step < 0:
            continue
        latest["current_step"] = step
        total = record.get("total_steps")
        if isinstance(total, int) and not isinstance(total, bool) and total >= step and total > 0:
            latest["total_steps"] = total
        loss = record.get("loss")
        if isinstance(loss, (int, float)) and not isinstance(loss, bool) and math.isfinite(loss):
            points[step] = {"step": step, "loss": loss}
            latest["loss"] = loss
        for key in ("elapsed_time", "remaining_time"):
            if isinstance(record.get(key), str):
                latest[key] = record[key][:40]
    if not latest:
        return {"available": False, "points": [], "note": "아직 실제 학습 지표가 기록되지 않았습니다."}
    values = sorted(points.values(), key=lambda x: x["step"])
    if len(values) > 200:
        values = [values[round(i * (len(values) - 1) / 199)] for i in range(200)]
    total = latest.get("total_steps")
    return {"available": True, **latest, "points": values,
            "percent": min(100, round(latest["current_step"] / total * 100, 1)) if total else None,
            "source": "llamafactory-native-trainer-log"}


def parameter_changes(base, candidate):
    def flatten(value, prefix=""):
        result = {}
        if isinstance(value, dict):
            for key, child in value.items():
                name = prefix + "." + key if prefix else key
                if isinstance(child, dict) and child:
                    result.update(flatten(child, name))
                else:
                    result[name] = child
        return result
    left, right = flatten(base), flatten(candidate)
    return [{"parameter": key, "base": left.get(key), "candidate": right.get(key),
             "base_missing": key not in left, "candidate_missing": key not in right}
            for key in sorted(left.keys() | right.keys())
            if key not in left or key not in right or canonical(left[key]) != canonical(right[key])]
