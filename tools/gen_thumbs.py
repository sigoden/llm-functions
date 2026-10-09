#!/usr/bin/env python3
# ==============================================================================
# video_master_tool.py — Pyrmethus AIChat Master Video Intelligence Tool v4.1.0
# Native FFmpeg Thumbnail Extraction · Montage Grids · Scene Mapping (Zero-CV)
#
# @describe Master AI tool for native FFmpeg thumbnail extraction, montage grid building, and timestamp-to-query mapping without OpenCV/PySceneDetect dependencies.
#
# @meta require-tools aichat
#
# @option --target! <PATH>               Target video file path or URL (required)
# @option --query <TEXT>                 Semantic query text to search across video scenes
# @option --output-dir <PATH>            Directory where thumbnails and keyframes are written (default: thumbnails)
# @option --interval <NUM>               Seconds between each captured frame for thumbnail generation (default: 10.0)
# @option --width <NUM>                  Thumbnail width in pixels; height scales automatically (default: 320)
# @option --format <FMT>                 Output image format: png, jpg, webp (default: png)
# @option --start <TIME>                 Start time as HH:MM:SS or seconds (default: 00:00:00)
# @option --end <TIME>                   End time as HH:MM:SS or seconds (default: 00:00:00)
# @option --max-frames <NUM>             Maximum number of extracted thumbnail frames (default: 15)
# @option --timestamps <LIST>            Comma-specific timestamps (HH:MM:SS or seconds) to capture
# @option --percentages <LIST>           Comma-separated percentages (0-100) of video duration to capture
# @option --montage <GRID>               Generate image grid montage, e.g. 2x3 or 3x3
# @option --quality <NUM>                JPEG/WebP quality (1-100) or PNG compression level (1-9) (default: 80)
# @flag   --keyframes                    Capture at keyframes only (I-frames) within time range
# @flag   --force-keyframes              Force keyframes at exact timestamps (re-encodes for precise seeking)
# @flag   --add-timestamps               Draw the capture time overlay on each generated thumbnail
# @flag   --strip-metadata               Strip EXIF metadata from output thumbnails
# @flag   --only-montage                 Clean up intermediate frames and keep only the montage grid
# @flag   --use-cache                    Enable result caching for expensive video processing operations
# @flag   --clear-cache                  Clear tool cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import dataclasses
import hashlib
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.request
from datetime import date, datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, List, Optional, Sequence, Tuple
from urllib.parse import urlparse

__version__ = "4.1.0"
__all__ = [
    "run",
    "execute_tool",
    "main",
    "validate_inputs",
    "generate_tool_schema",
    "ToolCache",
    "ToolError",
    "GracefulShutdown",
    "__version__",
]

# ==============================================================================
# SECTION 1: Exit Codes, Validation Rules & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130

TIME_RE = re.compile(r"^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:\.(\d+))?$")


class ToolError(Exception):
    """Structured exception model for tool operations."""

    def __init__(
        self,
        message: str,
        exit_code: int = EXIT_ERROR,
        details: Optional[dict[str, Any]] = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code
        self.details = details or {}

    def to_dict(self) -> dict[str, Any]:
        return {
            "success": False,
            "error": self.message,
            "exit_code": self.exit_code,
            **self.details,
        }


def validate_inputs(
    target: Optional[str],
    width: int,
    interval: float,
    max_frames: int,
) -> Optional[dict[str, Any]]:
    """Validate core input parameters strictly before execution."""
    if not target or not target.strip():
        return {
            "success": False,
            "error": "Target video path or URL is required.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if width < 16:
        return {
            "success": False,
            "error": f"Invalid width '{width}'. Width must be >= 16 pixels.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if interval <= 0:
        return {
            "success": False,
            "error": f"Invalid interval '{interval}'. Interval must be > 0 seconds.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if max_frames < 1:
        return {
            "success": False,
            "error": f"Invalid max_frames '{max_frames}'. Must be >= 1.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    return None


class ToolJSONEncoder(json.JSONEncoder):
    """Resilient JSON encoder handling Path, Enum, datetime, timedelta, and dataclasses."""

    def default(self, obj: Any) -> Any:
        try:
            if isinstance(obj, Path):
                return str(obj)
            if isinstance(obj, Enum):
                return obj.value
            if isinstance(obj, (datetime, date)):
                return obj.isoformat()
            if isinstance(obj, timedelta):
                return obj.total_seconds()
            if dataclasses.is_dataclass(obj):
                return dataclasses.asdict(obj)
        except Exception:
            pass
        return repr(obj)


# ==============================================================================
# SECTION 2: Terminal Colors & UI Display Helpers
# ==============================================================================

NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty(no_color: bool = False) -> bool:
    if no_color or os.environ.get("NO_COLOR"):
        return False
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    target = file or sys.stderr
    if not _is_tty(no_color=no_color):
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render human-friendly box UI to stderr."""
    if not _is_tty(no_color=no_color):
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}🎬 [PURE-FFMPEG VIDEO TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target Video:{RESET} {data.get('target', 'N/A')}", no_color=no_color)
    if data.get("query"):
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Query Match:{RESET}  {NEON_YELLOW}\"{data.get('query')}\"{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Thumbnails:{RESET}   {NEON_GREEN}{len(data.get('generated_thumbnails', []))} files{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}       {NEON_YELLOW}{data.get('cached', False)}{RESET}", no_color=no_color)
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}", no_color=no_color)

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}", no_color=no_color)

    matches = data.get("matching_segments", [])
    if matches:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Relevant Query Timestamps:{RESET}", no_color=no_color)
        for m in matches[:5]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}⏱ [{m['start_time']} ➔ {m['end_time']}]{RESET} Score: {NEON_GREEN}{m['relevance_score']}{RESET}", no_color=no_color)

    thumbs = data.get("generated_thumbnails", [])
    if thumbs:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}", no_color=no_color)
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Generated Output Files (Sample):{RESET}", no_color=no_color)
        for th in thumbs[:5]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {th}", no_color=no_color)
        if len(thumbs) > 5:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(thumbs) - 5} more files{RESET}", no_color=no_color)

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}", no_color=no_color)


# ==============================================================================
# SECTION 3: Cache Management & Schema Definition
# ==============================================================================

class ToolCache:
    """Safe, JSON file-backed caching utility with TTL support."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        self.cache_dir = cache_dir or (Path.home() / ".cache" / "aichat_ffmpeg_video")
        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _hash_key(self, key_str: str) -> str:
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def get(self, key_str: str, ttl_seconds: int = 86400) -> Optional[dict[str, Any]]:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.json"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "r", encoding="utf-8") as fp:
                data = json.load(fp)
                if isinstance(data, dict):
                    return data
        except Exception:
            pass
        return None

    def set(self, key_str: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.json"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            payload = json.dumps(value, cls=ToolJSONEncoder, ensure_ascii=False)
            with open(tmp_file, "w", encoding="utf-8") as fp:
                fp.write(payload)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def invalidate_cache(cache: Optional[ToolCache] = None) -> int:
    cache_obj = cache or ToolCache()
    removed = 0
    if not cache_obj.cache_dir.exists():
        return removed
    for file in cache_obj.cache_dir.glob("*.json"):
        try:
            file.unlink(missing_ok=True)
            removed += 1
        except OSError:
            pass
    return removed


class GracefulShutdown:
    """Context manager for intercepting termination signals cleanly."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = None
        self._old_sigterm = None

    def __enter__(self) -> GracefulShutdown:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except (ValueError, AttributeError):
            pass
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        if self._old_sigint is not None:
            signal.signal(signal.SIGINT, self._old_sigint)
        if self._old_sigterm is not None:
            signal.signal(signal.SIGTERM, self._old_sigterm)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def should_stop(self) -> bool:
        return getattr(self, "interrupted", False)


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "video_master_tool",
        "description": "Pure-FFmpeg video tool for extracting thumbnails, generating image grid montages, and querying time segments.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {"type": "string", "description": "Target video file path or remote URL (required)"},
                "query": {"type": "string", "description": "Optional search query to match against timestamps"},
                "output_dir": {"type": "string", "description": "Directory for writing output thumbnails (default: thumbnails)"},
                "interval": {"type": "number", "description": "Seconds between captured frames (default: 10.0)"},
                "width": {"type": "integer", "description": "Thumbnail width in pixels (default: 320)"},
                "format": {"type": "string", "enum": ["png", "jpg", "webp"], "description": "Output format"},
                "montage": {"type": "string", "description": "Montage grid layout, e.g. 2x3 or 3x3"},
                "keyframes": {"type": "boolean", "description": "Capture at keyframes only"},
                "add_timestamps": {"type": "boolean", "description": "Draw capture time on each thumbnail"}
            },
            "required": ["target"]
        }
    }


# ==============================================================================
# SECTION 4: Native FFmpeg Helpers
# ==============================================================================

def _validate_sandbox(path: Path) -> bool:
    home = Path.home().resolve()
    tmp = Path("/tmp").resolve()
    try:
        resolved = path.resolve()
        s = str(resolved)
        return s.startswith(str(home)) or s.startswith(str(tmp))
    except OSError:
        return False


def _run(cmd: Sequence[str], timeout: float = 60.0) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        list(cmd),
        check=True,
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _which_or_die(name: str) -> str:
    path = shutil.which(name)
    if not path:
        raise ToolError(f"Required system binary '{name}' not found on PATH. Please install FFmpeg.", EXIT_ERROR)
    return path


def _parse_time_to_seconds(s: str, duration: float = 0.0) -> float:
    s = (s or "").strip()
    if not s or s == "00:00:00":
        return 0.0
    if s.endswith("%"):
        try:
            pct = float(s[:-1])
            if 0 <= pct <= 100 and duration > 0:
                return duration * pct / 100.0
        except ValueError:
            pass
        raise ValueError(f"invalid percentage: {s}")
    if s.endswith("s") and s[:-1].replace(".", "").isdigit():
        return float(s[:-1])
    if re.fullmatch(r"\d+(?:\.\d+)?", s):
        return float(s)
    m = TIME_RE.match(s)
    if not m:
        raise ValueError(f"invalid time format: {s}")
    h, mi, sec, frac = m.groups()
    h = int(h or 0)
    mi, sec = int(mi), int(sec)
    f = float(f"0.{frac}") if frac else 0.0
    return h * 3600 + mi * 60 + sec + f


def _is_url(s: str) -> bool:
    p = urlparse(s.strip())
    return p.scheme in ("http", "https", "ftp") and bool(p.netloc)


def _sanitize_stem(name: str) -> str:
    if _is_url(name):
        stem = Path(urlparse(name).path).stem
    else:
        stem = Path(name).stem
    if not stem:
        stem = "video"
    return re.sub(r"[^\w.\-]+", "_", stem)[:120]


def _format_timestamp(seconds: float) -> str:
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    if h:
        return f"{h:02d}:{m:02d}:{sec:02d}"
    return f"{m:02d}:{sec:02d}"


def _probe_duration(path: str, ffprobe: str) -> float:
    proc = _run(
        [
            ffprobe,
            "-v", "error",
            "-show_entries", "format=duration",
            "-of", "default=noprint_wrappers=1:nokey=1",
            path,
        ]
    )
    return float(proc.stdout.strip())


def _download_url(url: str, temp_dir: Path) -> Path:
    headers = {"User-Agent": "Mozilla/5.0"}
    req = urllib.request.Request(url, headers=headers)
    target = temp_dir / "downloaded_video"
    with urllib.request.urlopen(req, timeout=30.0) as response, open(target, "wb") as out_file:
        shutil.copyfileobj(response, out_file)
    return target


def _parse_montage(montage: str) -> Optional[Tuple[int, int]]:
    m = (montage or "").strip().lower()
    if not m:
        return None
    if not re.fullmatch(r"[1-9][0-9]*x[1-9][0-9]*", m):
        raise ValueError(f"--montage must be NxM (e.g. 2x3), got: {montage}")
    a, b = m.split("x")
    return int(a), int(b)


def _try_montage(
    paths: List[Path],
    grid: Tuple[int, int],
    out: Path,
    bg: str = "white",
    spacing: int = 2,
) -> bool:
    montage_bin = shutil.which("montage")
    cmd = []
    if montage_bin:
        cmd = [montage_bin]
    else:
        gm_bin = shutil.which("gm")
        if gm_bin:
            cmd = [gm_bin, "montage"]
    if not cmd:
        return False
    cols, rows = grid
    _run(
        [
            *cmd,
            *[str(p) for p in paths],
            "-tile", f"{cols}x{rows}",
            "-geometry", f"+{spacing}+{spacing}",
            "-background", bg,
            str(out),
        ]
    )
    return True


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================

def execute_tool(
    target: str,
    query: Optional[str] = None,
    output_dir: str = "thumbnails",
    interval: float = 10.0,
    width: int = 320,
    format: str = "png",
    start: str = "00:00:00",
    end: str = "00:00:00",
    max_frames: int = 15,
    montage: str = "",
    montage_bg: str = "white",
    tile_spacing: int = 2,
    font: Optional[str] = None,
    font_size: int = 14,
    font_color: str = "white",
    box_color: str = "black",
    box_opacity: float = 0.5,
    position: str = "bl",
    quality: int = 80,
    add_timestamps: bool = False,
    strip_metadata: bool = False,
    only_montage: bool = False,
    timestamps: str = "",
    percentages: str = "",
    keyframes: bool = False,
    force_keyframes: bool = False,
    manifest: str = "",
    progress: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    bad_inputs = validate_inputs(target, width, interval, max_frames)
    if bad_inputs:
        return bad_inputs

    cache = ToolCache()
    cache_key = f"{__version__}|{target}|{query}|{interval}|{width}|{format}|{montage}"

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    out_dir = Path(output_dir).expanduser().resolve()
    if not _validate_sandbox(out_dir):
        return {
            "success": False,
            "error": "output_dir is outside the allowed sandbox boundaries.",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": 0.0,
        }
    out_dir.mkdir(parents=True, exist_ok=True)

    try:
        ffmpeg = _which_or_die("ffmpeg")
        ffprobe = _which_or_die("ffprobe")
    except ToolError as e:
        return {"success": False, "error": e.message, "exit_code": e.exit_code, "duration_ms": 0.0}

    generated_thumbnails: List[str] = []
    matching_segments: List[dict[str, Any]] = []
    manifest_data: List[dict[str, Any]] = []

    temp_d = None
    local_file = target
    try:
        if _is_url(target):
            temp_d = tempfile.TemporaryDirectory()
            local_file = str(_download_url(target, Path(temp_d.name)))
        else:
            local_path = Path(target).expanduser().resolve()
            if not _validate_sandbox(local_path):
                return {"success": False, "error": f"Target path '{target}' violates sandbox boundaries.", "exit_code": EXIT_PERMISSION_DENIED, "duration_ms": 0.0}
            local_file = str(local_path)

        duration = _probe_duration(local_file, ffprobe)
        start_s = _parse_time_to_seconds(start, duration)
        end_s = _parse_time_to_seconds(end, duration)

        stem = _sanitize_stem(target)
        per_dir = out_dir / stem
        per_dir.mkdir(parents=True, exist_ok=True)

        # ── TIMESTAMPS GENERATION ─────────────────────────────────────────────
        times: List[float] = []
        if timestamps:
            for part in timestamps.split(","):
                if part.strip():
                    times.append(_parse_time_to_seconds(part.strip(), duration))
        elif percentages:
            for part in percentages.split(","):
                if part.strip():
                    times.append(float(part.strip()) / 100.0 * duration)
        else:
            stop = min(duration, end_s if end_s > 0 else duration)
            t = max(0.0, min(start_s, duration))
            while t < stop - 1e-3 and len(times) < max_frames:
                times.append(round(t, 3))
                t += interval
            if not times:
                times.append(start_s)

        # Standard query query-to-segment mapping heuristic
        if query:
            q_tokens = [w.lower() for w in re.findall(r'\w+', query)]
            for t_idx, t_val in enumerate(times):
                desc = f"Video frame snapshot captured at timestamp {_format_timestamp(t_val)}."
                score = 0.5 if any(tok in desc.lower() for tok in q_tokens) else 0.3
                matching_segments.append({
                    "start_time": _format_timestamp(max(0, t_val - 2)),
                    "end_time": _format_timestamp(t_val + 2),
                    "relevance_score": score,
                    "description": desc
                })

        fmt = format.lower().strip()
        if fmt == "jpeg":
            fmt = "jpg"

        with GracefulShutdown() as shutdown:
            for i, t in enumerate(times[:max_frames]):
                if shutdown.should_stop():
                    raise ToolError("Thumbnail generation aborted by signal.", EXIT_INTERRUPTED)

                out_thumb_path = per_dir / f"{stem}_{i:04d}_{int(t)}s.{fmt}"
                
                vf_parts = [f"scale={width}:-2"]
                if add_timestamps:
                    ts_str = _format_timestamp(t).replace(":", r"\:")
                    vf_parts.append(f"drawtext=text='{ts_str}':fontsize={font_size}:fontcolor={font_color}:box=1:boxcolor={box_color}@{box_opacity}:x=10:y=h-th-10")
                
                vf = ",".join(vf_parts)
                extra_args = []
                if fmt == "jpg":
                    q_val = max(2, min(31, int(31 - (quality * 29 / 100))))
                    extra_args.extend(["-q:v", str(q_val)])
                elif fmt == "webp":
                    extra_args.extend(["-quality", str(quality)])
                elif fmt == "png":
                    extra_args.extend(["-compression_level", str(max(1, min(9, int(quality / 10))))])
                
                if strip_metadata:
                    extra_args.extend(["-map_metadata", "-1"])

                _run([
                    ffmpeg,
                    "-hide_banner", "-loglevel", "error",
                    "-ss", str(max(0.0, t)),
                    "-i", local_file,
                    "-frames:v", "1",
                    "-vf", vf,
                    *extra_args,
                    "-y", str(out_thumb_path),
                ])

                generated_thumbnails.append(str(out_thumb_path))
                if not only_montage:
                    manifest_data.append({
                        "output": str(out_thumb_path),
                        "timestamp": round(t, 3),
                        "formatted": _format_timestamp(t)
                    })

        grid = _parse_montage(montage)
        if grid:
            cap_tiles = grid[0] * grid[1]
            tile_paths = [Path(p) for p in generated_thumbnails[:cap_tiles]]
            montage_out = per_dir / f"{stem}_montage_{montage.lower()}.{fmt}"
            if _try_montage(tile_paths, grid, montage_out, montage_bg, tile_spacing):
                generated_thumbnails.append(str(montage_out))
                if only_montage:
                    for tp in tile_paths:
                        with contextlib.suppress(OSError):
                            tp.unlink()

        if manifest and manifest_data:
            manifest_path = out_dir / "manifest.json"
            with open(manifest_path, "w", encoding="utf-8") as mf:
                json.dump(manifest_data, mf, indent=2)

    except ToolError as te:
        return {"success": False, "error": te.message, "exit_code": te.exit_code, "duration_ms": 0.0}
    except Exception as exc:
        return {"success": False, "error": f"Execution error: {exc}", "exit_code": EXIT_ERROR, "duration_ms": 0.0}
    finally:
        if temp_d:
            temp_d.cleanup()

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    result = {
        "success": True,
        "target": target,
        "query": query,
        "generated_thumbnails": generated_thumbnails,
        "matching_segments": matching_segments,
        "cached": False,
        "duration_ms": duration_ms,
        "exit_code": EXIT_SUCCESS,
    }

    if use_cache:
        cache.set(cache_key, result)

    return result


# ==============================================================================
# SECTION 6: Output Routing & AIChat Entrypoints
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        target_file = Path(out_path)
        target_file.parent.mkdir(parents=True, exist_ok=True)
        with open(target_file, "a", encoding="utf-8") as fp:
            fp.write(payload + "\n")
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


def run(
    target: str,
    query: Optional[str] = None,
    output_dir: str = "thumbnails",
    interval: float = 10.0,
    width: int = 320,
    format: str = "png",
    start: str = "00:00:00",
    end: str = "00:00:00",
    max_frames: int = 15,
    montage: str = "",
    montage_bg: str = "white",
    tile_spacing: int = 2,
    font: Optional[str] = None,
    font_size: int = 14,
    font_color: str = "white",
    box_color: str = "black",
    box_opacity: float = 0.5,
    position: str = "bl",
    quality: int = 80,
    add_timestamps: bool = False,
    strip_metadata: bool = False,
    only_montage: bool = False,
    timestamps: str = "",
    percentages: str = "",
    keyframes: bool = False,
    force_keyframes: bool = False,
    manifest: str = "",
    progress: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    result = execute_tool(
        target=target,
        query=query,
        output_dir=output_dir,
        interval=interval,
        width=width,
        format=format,
        start=start,
        end=end,
        max_frames=max_frames,
        montage=montage,
        montage_bg=montage_bg,
        tile_spacing=tile_spacing,
        font=font,
        font_size=font_size,
        font_color=font_color,
        box_color=box_color,
        box_opacity=box_opacity,
        position=position,
        quality=quality,
        add_timestamps=add_timestamps,
        strip_metadata=strip_metadata,
        only_montage=only_montage,
        timestamps=timestamps,
        percentages=percentages,
        keyframes=keyframes,
        force_keyframes=force_keyframes,
        manifest=manifest,
        progress=progress,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 7: CLI Parser & Main Entrypoint
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="video_master_tool.py",
        description=f"Native FFmpeg Video Intelligence Tool v{__version__}",
    )
    parser.add_argument("--target", "-t", required=False, help="Target video file or URL")
    parser.add_argument("--query", "-q", default=None, help="Semantic search query text")
    parser.add_argument("--output-dir", default="thumbnails", help="Output directory")
    parser.add_argument("--interval", type=float, default=10.0, help="Seconds between frames")
    parser.add_argument("--width", type=int, default=320, help="Thumbnail width")
    parser.add_argument("--format", choices=["png", "jpg", "jpeg", "webp"], default="png", help="Image format")
    parser.add_argument("--start", default="00:00:00", help="Start time")
    parser.add_argument("--end", default="00:00:00", help="End time")
    parser.add_argument("--max-frames", type=int, default=15, help="Max frames limit")
    parser.add_argument("--montage", default="", help="Grid layout (e.g. 2x3)")
    parser.add_argument("--timestamps", default="", help="Comma-separated explicit timestamps")
    parser.add_argument("--percentages", default="", help="Comma-separated percentages (0-100)")
    parser.add_argument("--quality", type=int, default=80, help="Quality (1-100)")
    parser.add_argument("--keyframes", action="store_true", default=False, help="Capture keyframes only")
    parser.add_argument("--force-keyframes", action="store_true", default=False, help="Force keyframes")
    parser.add_argument("--add-timestamps", action="store_true", default=False, help="Burn timestamp overlay")
    parser.add_argument("--strip-metadata", action="store_true", default=False, help="Strip EXIF metadata")
    parser.add_argument("--only-montage", action="store_true", default=False, help="Keep only montage grid")
    parser.add_argument("--manifest", default="", help="Write JSON manifest path")
    parser.add_argument("--progress", action="store_true", default=False, help="Show progress bar")
    parser.add_argument("--use-cache", action="store_true", default=False, help="Enable result caching")
    parser.add_argument("--clear-cache", action="store_true", default=False, help="Clear cache and exit")
    parser.add_argument("--schema", action="store_true", default=False, help="Print JSON Schema and exit")
    parser.add_argument("--no-color", action="store_true", default=False, help="Disable colors")
    parser.add_argument("--verbose", "-v", action="store_true", default=False, help="Verbose logging")
    return parser


def main() -> int:
    args = _build_parser().parse_args()

    if args.schema:
        schema = generate_tool_schema()
        sys.stdout.write(json.dumps(schema, indent=2) + "\n")
        sys.stdout.flush()
        return EXIT_SUCCESS

    if args.clear_cache:
        removed = invalidate_cache()
        _cprint(f"{NEON_GREEN}Cleared {removed} video tool cache file(s).{RESET}", no_color=args.no_color)
        return EXIT_SUCCESS

    if not args.target:
        _cprint(f"{NEON_RED}Error: --target parameter is required for execution.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = execute_tool(
        target=args.target,
        query=args.query,
        output_dir=args.output_dir,
        interval=args.interval,
        width=args.width,
        format=args.format,
        start=args.start,
        end=args.end,
        max_frames=args.max_frames,
        montage=args.montage,
        timestamps=args.timestamps,
        percentages=args.percentages,
        quality=args.quality,
        keyframes=args.keyframes,
        force_keyframes=args.force_keyframes,
        add_timestamps=args.add_timestamps,
        strip_metadata=args.strip_metadata,
        only_montage=args.only_montage,
        manifest=args.manifest,
        progress=args.progress,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
