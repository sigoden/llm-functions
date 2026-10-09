#!/usr/bin/env python3
"""Summarize media files with ffprobe — llm-functions tool."""

from __future__ import annotations

import argparse
import json
import shutil
import subprocess
import sys
from typing import Any, Dict, List, Sequence


def _run(cmd: Sequence[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(list(cmd), check=True, capture_output=True, text=True)


def _ffprobe_json(path: str, ffprobe: str) -> Dict[str, Any]:
    proc = _run(
        [
            ffprobe,
            "-v",
            "quiet",
            "-print_format",
            "json",
            "-show_format",
            "-show_streams",
            path,
        ]
    )
    return json.loads(proc.stdout)


def _human_summary(data: Dict[str, Any], path: str) -> str:
    fmt = data.get("format") or {}
    streams: List[Dict[str, Any]] = data.get("streams") or []
    lines = [f"file: {path}"]
    if fmt.get("format_long_name") or fmt.get("format_name"):
        lines.append(
            f"container: {fmt.get('format_long_name') or fmt.get('format_name')}"
        )
    dur = fmt.get("duration")
    if dur is not None:
        try:
            d = float(dur)
            lines.append(f"duration_sec: {d:.3f}")
            lines.append(
                f"duration_hms: {int(d // 3600):02d}:{int((d % 3600) // 60):02d}:{int(d % 60):02d}"
            )
        except ValueError:
            lines.append(f"duration: {dur}")
    if fmt.get("bit_rate"):
        lines.append(f"bitrate_bps: {fmt.get('bit_rate')}")
    if fmt.get("size"):
        lines.append(f"size_bytes: {fmt.get('size')}")

    for i, st in enumerate(streams):
        codec_type = st.get("codec_type", "?")
        lines.append(f"stream[{i}] type={codec_type} codec={st.get('codec_name', '?')}")
        if codec_type == "video":
            w, h = st.get("width"), st.get("height")
            if w and h:
                lines.append(f"  resolution: {w}x{h}")
            if st.get("r_frame_rate"):
                lines.append(f"  frame_rate: {st.get('r_frame_rate')}")
        if codec_type == "audio":
            if st.get("sample_rate"):
                lines.append(f"  sample_rate: {st.get('sample_rate')}")
            if st.get("channels"):
                lines.append(f"  channels: {st.get('channels')}")
    return "\n".join(lines)


def run(input: str) -> str:
    """Summarize a video or audio file (local path or URL) using ffprobe.

    Args:
        input: Path or URL to one media file.
    """
    path = (input or "").strip()
    if not path:
        return "ERROR: input is empty"
    ffprobe = shutil.which("ffprobe")
    if not ffprobe:
        return "ERROR: ffprobe not found on PATH"
    try:
        data = _ffprobe_json(path, ffprobe)
        return _human_summary(data, path)
    except subprocess.CalledProcessError as e:
        return f"ERROR: ffprobe failed: {(e.stderr or e.stdout or str(e)).strip()}"
    except json.JSONDecodeError as e:
        return f"ERROR: invalid ffprobe JSON: {e}"


def _cli() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--input", required=True)
    args = p.parse_args()
    out = run(args.input)
    print(out)
    return 0 if not out.startswith("ERROR:") else 1


if __name__ == "__main__":
    sys.exit(_cli())
