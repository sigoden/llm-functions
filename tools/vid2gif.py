#!/usr/bin/env python3
# ==============================================================================
# vid2gif.py — Pyrmethus Video-to-GIF Orchestrator v1.0.0
# One-shot workflow: lux_download → gen_thumbs → gif_tool
# Chain-friendly subprocess wrapper that returns a structured JSON status.
#
# @describe Orchestrate video-to-GIF conversion via lux → ffmpeg → gifsicle pipeline.
#
# @option --url! <URL>                  Source video URL (HTTP/HTTPS, supported by lux).
# @option --output-dir <DIR>            Directory where the GIF and intermediate frames are written (default: ~/vid2gif).
# @option --gif-name <NAME>             Output GIF filename (default: derived from URL).
# @option --max-frames <N>              Maximum frames to extract from the video (default: 30).
# @option --interval <SEC>              Seconds between captured frames (default: 1.0).
# @option --width <PX>                  Frame width in pixels (default: 480).
# @option --format <FMT>                Intermediate frame format: png, jpg, webp (default: png).
# @option --delay <CS>                  Frame delay in 1/100ths of a second (default: 8).
# @option --loopcount <N>               GIF loop count (0 = forever, default: 0).
# @option --optimize <1-3>              Gifsicle optimization level (1-3; optional).
# @option --lossy <N>                   Gifsicle lossiness (1-200; optional).
# @option --colors <N>                  Reduce palette to N colors (2-256; optional).
# @option --start <HH:MM:SS|SEC>        Start time for frame extraction (default: 00:00:00).
# @option --end <HH:MM:SS|SEC>          End time for frame extraction (default: full video).
# @option --keep-frames                 Keep intermediate frames after GIF creation (default: delete).
# @flag   --audio-only                  Download audio-only track (passed to lux).
# @flag   --use-aria2                   Use aria2 for faster downloads (passed to lux).
# @flag   --no-color                    Disable ANSI color output.
# @flag   --verbose                     Enable detailed debug logging.
# @flag   --dry-run                     Print all planned steps but do not execute.
#
# @env LLM_OUTPUT=/dev/stdout           Output path for LLM JSON integration.
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import signal
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Optional
from urllib.parse import urlparse

__version__ = "1.0.0"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2
EXIT_FILE_NOT_FOUND = 3
EXIT_PERMISSION_DENIED = 4
EXIT_INTERRUPTED = 5
EXIT_DOWNLOAD_FAILED = 10
EXIT_THUMB_FAILED = 11
EXIT_GIF_FAILED = 12

# ---------------------------------------------------------------------------
# ANSI / Output Helpers
# ---------------------------------------------------------------------------

NEON_CYAN = "\033[38;5;51m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
NEON_GREEN = "\033[38;5;46m"
NEON_YELLOW = "\033[38;5;226m"
NEON_RED = "\033[38;5;196m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(text: str, no_color: bool = False, end: str = "\n") -> None:
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, flush=True, end=end)


def _validate_sandbox(path: Path) -> bool:
    """Allow writes anywhere in $HOME, /tmp, or Termux prefix."""
    allowed_roots: list[Path] = [
        Path.home().resolve(),
        Path("/tmp").resolve(),
        Path(tempfile.gettempdir()).resolve(),
    ]
    prefix = os.environ.get("PREFIX")
    if prefix:
        allowed_roots.append(Path(prefix).resolve())
        allowed_roots.append((Path(prefix) / "tmp").resolve())
    tmpdir = os.environ.get("TMPDIR")
    if tmpdir:
        allowed_roots.append(Path(tmpdir).resolve())
    llm_root = os.environ.get("LLM_ROOT_DIR")
    if llm_root:
        allowed_roots.append(Path(llm_root).resolve())
    if Path("/data/data/com.termux").exists():
        allowed_roots.append(Path("/data/data/com.termux").resolve())
    try:
        resolved = path.resolve()
        s = str(resolved)
        return any(s.startswith(str(root)) for root in allowed_roots)
    except OSError:
        return False


def _check_binary(name: str) -> Optional[str]:
    """Return full path of binary if installed, else None."""
    return shutil.which(name)


def _slugify(text: str, max_len: int = 80) -> str:
    """Make a filesystem-safe identifier from a URL or arbitrary string."""
    text = re.sub(r"[^a-zA-Z0-9._-]+", "_", text or "")
    text = text.strip("._-")
    return text[:max_len] or "output"


def _run_subprocess(
    cmd: list[str],
    timeout: Optional[float] = None,
    verbose: bool = False,
) -> tuple[int, str, str]:
    """Run a subprocess and capture stdout/stderr. Returns (rc, stdout, stderr)."""
    if verbose:
        _cprint(f"{DIM}[EXEC] {' '.join(cmd)}{RESET}", no_color=False)
    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        return proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as e:
        return 124, e.stdout or "", (e.stderr or "") + f"\n[Timeout after {timeout}s]"
    except FileNotFoundError as e:
        return 127, "", str(e)


# ---------------------------------------------------------------------------
# Step 1: Video download via lux (with pornhub-dl and yt-dlp fallbacks)
# ---------------------------------------------------------------------------


def _run_lux_download(
    url: str,
    output_dir: Path,
    audio_only: bool,
    use_aria2: bool,
    timeout: int,
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """
    Invoke lux to download the video, with fallbacks to pornhub-dl (for Pornhub URLs)
    and yt-dlp. Returns a dict with:
        success (bool)
        video_path (str) – absolute path to the downloaded file
        all_candidates (list[str]) – every file created in output_dir
        exit_code_rc (int) – return code of the last attempt that succeeded
        stdout / stderr (str) – captured output of the last attempt
        attempts (int) – how many times we tried
        fallback_used (str, optional) – which fallback succeeded, if any
    On failure, 'success' is False and 'error' contains a human‑readable message.
    """
    # Helper to run a download attempt and locate the output file.
    def _run_attempt(
        cmd: list[str],
        attempt_name: str,
        attempt_no: int,
    ) -> dict[str, Any]:
        if verbose:
            _cprint(f"{DIM}[{attempt_name} attempt {attempt_no}] {' '.join(cmd)}{RESET}", no_color=no_color)
            _cprint(f"{DIM}[{attempt_name} cwd] {output_dir}{RESET}", no_color=no_color)

        rc, stdout, stderr = _run_subprocess(cmd, timeout=timeout, verbose=verbose)

        if verbose:
            if stdout:
                _cprint(f"{DIM}[{attempt_name} stdout] {stdout.strip()[:500]}{RESET}", no_color=no_color)
            if stderr:
                _cprint(f"{DIM}[{attempt_name} stderr] {stderr.strip()[:500]}{RESET}", no_color=no_color)

        # Try to extract an expected filename from output (lux/yt-dlp/pornhub-dl often print it)
        expected_name = None
        for line in (stdout + stderr).splitlines():
            m = re.search(r"\[info\\]\s+([^\s]+\.(?:mp4|mkv|webm|mov|flv))", line, re.IGNORECASE)
            if m:
                expected_name = m.group(1)
                break

        # Build list of candidate files (ignore any .gif we might have created earlier)
        candidates = [
            p for p in output_dir.iterdir()
            if p.is_file() and not p.name.lower().endswith(".gif")
        ]

        if expected_name:
            expected_path = output_dir / expected_name
            if expected_path.is_file() and expected_path.stat().st_size > 0:
                return {
                    "success": True,
                    "video_path": str(expected_path.resolve()),
                    "all_candidates": [str(p.resolve()) for p in candidates],
                    "exit_code_rc": rc,
                    "stdout": stdout,
                    "stderr": stderr,
                }
            if verbose:
                _cprint(
                    f"{NEON_RED}{attempt_name} reported success but expected file "
                    f"'{expected_name}' is missing or zero‑size.{RESET}",
                    no_color=no_color,
                )
        else:
            # No explicit filename – fall back to the newest file.
            if candidates:
                candidates.sort(key=lambda p: p.stat().st_mtime, reverse=True)
                newest = candidates[0]
                if newest.stat().st_size > 0:
                    return {
                        "success": True,
                        "video_path": str(newest.resolve()),
                        "all_candidates": [str(p.resolve()) for p in candidates],
                        "exit_code_rc": rc,
                        "stdout": stdout,
                        "stderr": stderr,
                    }
                if verbose:
                    _cprint(
                        f"{NEON_RED}{attempt_name} produced a file but it is zero‑size: {newest.name}{RESET}",
                        no_color=no_color,
                    )
            else:
                if verbose:
                    _cprint(f"{NEON_RED}{attempt_name} produced no output files at all.{RESET}", no_color=no_color)

        # Attempt failed.
        return {
            "success": False,
            "error": (
                f"{attempt_name} attempt {attempt_no} failed (rc={rc}). "
                f"Expected file: {expected_name or '<any>'}. "
                f"Stderr: {stderr.strip()[:200] if stderr else '(none)'}"
            ),
            "exit_code_rc": rc,
            "stdout": stdout,
            "stderr": stderr,
        }

    # -----------------------------------------------------------------
    # 1) Try lux first
    # -----------------------------------------------------------------
    lux_available = _check_binary("lux") is not None
    lux_result = None
    if lux_available:
        # Retry loop for lux (max 3 attempts)
        max_attempts = 3
        backoff_base = 2
        for attempt in range(1, max_attempts + 1):
            cmd = ["lux", "-o", str(output_dir), url]
            if audio_only:
                cmd.append("--audio-only")
            if use_aria2:
                cmd.append("--aria2")
            lux_result = _run_attempt(cmd, "lux", attempt)
            if lux_result.get("success"):
                break
            if attempt < max_attempts:
                wait_time = backoff_base ** (attempt - 1)
                if verbose:
                    _cprint(
                        f"{NEON_YELLOW}lux attempt {attempt} failed; retrying in {wait_time}s…{RESET}",
                        no_color=no_color,
                    )
                time.sleep(wait_time)

    # -----------------------------------------------------------------
    # 2) If lux failed and URL looks like Pornhub, try pornhub-dl
    # -----------------------------------------------------------------
    pornhub_result = None
    if not (lux_result and lux_result.get("success")):
        if "pornhub.com" in url.lower() and _check_binary("pornhub-dl"):
            max_attempts = 3
            backoff_base = 2
            for attempt in range(1, max_attempts + 1):
                cmd = ["pornhub-dl", "-o", str(output_dir), url]
                if audio_only:
                    # pornhub-dl doesn't have an explicit audio‑only flag; we fallback to yt-dlp later
                    pass
                if use_aria2:
                    # pornhub-dl doesn't expose aria2; we ignore for now
                    pass
                pornhub_result = _run_attempt(cmd, "pornhub-dl", attempt)
                if pornhub_result.get("success"):
                    break
                if attempt < max_attempts:
                    wait_time = backoff_base ** (attempt - 1)
                    if verbose:
                        _cprint(
                            f"{NEON_YELLOW}pornhub-dl attempt {attempt} failed; retrying in {wait_time}s…{RESET}",
                            no_color=no_color,
                        )
                    time.sleep(wait_time)

    # -----------------------------------------------------------------
    # 3) Fallback to yt-dlp if still unsuccessful
    # -----------------------------------------------------------------
    ytdlp_result = None
    if not ((lux_result and lux_result.get("success")) or (pornhub_result and pornhub_result.get("success"))):
        if _check_binary("yt-dlp"):
            max_attempts = 3
            backoff_base = 2
            for attempt in range(1, max_attempts + 1):
                cmd = [
                    "yt-dlp",
                    "-o",
                    str(output_dir / "%(title)s.%(ext)s"),
                    url,
                ]
                if audio_only:
                    cmd.extend(["-x", "--audio-format", "best"])
                if use_aria2:
                    cmd.extend(["--downloader", "aria2c"])
                ytdlp_result = _run_attempt(cmd, "yt-dlp", attempt)
                if ytdlp_result.get("success"):
                    break
                if attempt < max_attempts:
                    wait_time = backoff_base ** (attempt - 1)
                    if verbose:
                        _cprint(
                            f"{NEON_YELLOW}yt-dlp attempt {attempt} failed; retrying in {wait_time}s…{RESET}",
                            no_color=no_color,
                        )
                    time.sleep(wait_time)

    # -----------------------------------------------------------------
    # Determine which attempt succeeded and return appropriate dict
    # -----------------------------------------------------------------
    successful = None
    used_fallback = None
    if lux_result and lux_result.get("success"):
        successful = lux_result
    elif pornhub_result and pornhub_result.get("success"):
        successful = pornhub_result
        used_fallback = "pornhub-dl"
    elif ytdlp_result and ytdlp_result.get("success"):
        successful = ytdlp_result
        used_fallback = "yt-dlp"

    if successful:
        # Attach fallback info if we used one
        if used_fallback:
            successful["fallback_used"] = used_fallback
        return successful

    # -----------------------------------------------------------------
    # All attempts failed – return the most informative error
    # -----------------------------------------------------------------
    # Prefer lux error if we tried it, else pornhub-dl, else yt-dlp
    error_source = None
    if lux_result:
        error_source = lux_result
    elif pornhub_result:
        error_source = pornhub_result
    elif ytdlp_result:
        error_source = ytdlp_result
    else:
        # No downloader available at all
        return {
            "success": False,
            "stage": "download",
            "error": "No supported downloader (lux, pornhub-dl, yt-dlp) found in PATH.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    return {
        "success": False,
        "stage": "download",
        "error": error_source.get("error", "Unknown download error"),
        "exit_code": error_source.get("exit_code_rc", EXIT_DOWNLOAD_FAILED),
        "step_result": error_source,  # keep full debug info
    }


# ---------------------------------------------------------------------------
# Step 2: Frame extraction via gen_thumbs (or ffmpeg directly)
# ---------------------------------------------------------------------------


def _run_gen_thumbs(
    video_path: str,
    frames_dir: Path,
    max_frames: int,
    interval: float,
    width: int,
    fmt: str,
    start: str,
    end: str,
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """Call gen_thumbs.py to extract frames. Falls back to ffmpeg directly if absent."""
    gen_thumbs_path = Path(__file__).resolve().parent / "gen_thumbs.py"
    if not gen_thumbs_path.is_file():
        return _run_ffmpeg_thumbs(
            video_path,
            frames_dir,
            max_frames,
            interval,
            width,
            fmt,
            start,
            end,
            verbose,
            no_color,
        )

    cmd = [
        sys.executable,
        str(gen_thumbs_path),
        "--input",
        video_path,
        "--output_dir",
        str(frames_dir),
        "--max_frames",
        str(max_frames),
        "--interval",
        str(interval),
        "--width",
        str(width),
        "--format",
        fmt,
        "--start",
        start,
        "--end",
        end,
    ]
    if verbose:
        cmd.append("--verbose")

    rc, stdout, stderr = _run_subprocess(cmd, timeout=180, verbose=verbose)

    if verbose:
        if stdout:
            _cprint(
                f"{DIM}[gen_thumbs stdout] {stdout.strip()[:500]}{RESET}",
                no_color=no_color,
            )
        if stderr:
            _cprint(
                f"{DIM}[gen_thumbs stderr] {stderr.strip()[:500]}{RESET}",
                no_color=no_color,
            )

    frames = sorted(
        p
        for p in frames_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    )
    if not frames:
        return {
            "success": False,
            "error": f"gen_thumbs produced no frames. stderr: {stderr.strip()[:300]}",
            "exit_code": EXIT_THUMB_FAILED,
            "stdout": stdout,
            "stderr": stderr,
        }

    return {
        "success": True,
        "frames": [str(p.resolve()) for p in frames],
        "frame_count": len(frames),
        "frames_dir": str(frames_dir),
    }


def _run_ffmpeg_thumbs(
    video_path: str,
    frames_dir: Path,
    max_frames: int,
    interval: float,
    width: int,
    fmt: str,
    start: str,
    end: str,
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """Direct ffmpeg fallback when gen_thumbs.py is unavailable."""
    if not _check_binary("ffmpeg"):
        return {
            "success": False,
            "error": "ffmpeg not found in PATH and gen_thumbs.py is missing.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    fmt_ext = "jpg" if fmt == "jpeg" else fmt
    pattern = str(frames_dir / f"frame_%04d.{fmt_ext}")

    # fps=1/interval means one frame every `interval` seconds
    fps_value = 1.0 / max(interval, 0.1)
    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        "-ss",
        start,
        "-i",
        video_path,
        "-vf",
        f"fps={fps_value},scale={width}:-1",
        "-frames:v",
        str(max_frames),
        pattern,
    ]
    if end and end != "00:00:00":
        # Insert -to before -i
        cmd = [
            "ffmpeg",
            "-y",
            "-loglevel",
            "error",
            "-ss",
            start,
        ]
        if end and end != "00:00:00":
            cmd.extend(["-to", end])
        cmd.extend(
            [
                "-i",
                video_path,
                "-vf",
                f"fps={fps_value},scale={width}:-1",
                "-frames:v",
                str(max_frames),
                pattern,
            ]
        )

    rc, stdout, stderr = _run_subprocess(cmd, timeout=180, verbose=verbose)

    if verbose and stderr:
        _cprint(f"{DIM}[ffmpeg] {stderr.strip()[:500]}{RESET}", no_color=no_color)

    frames = sorted(
        p
        for p in frames_dir.rglob("*")
        if p.is_file() and p.suffix.lower() in (".png", ".jpg", ".jpeg", ".webp")
    )
    if not frames:
        return {
            "success": False,
            "error": f"ffmpeg produced no frames. stderr: {stderr.strip()[:300]}",
            "exit_code": EXIT_THUMB_FAILED,
        }

    return {
        "success": True,
        "frames": [str(p.resolve()) for p in frames],
        "frame_count": len(frames),
        "frames_dir": str(frames_dir),
    }


# ---------------------------------------------------------------------------
# Step 3: GIF creation via ffmpeg (preferred) → gifsicle optimize (optional)
# ---------------------------------------------------------------------------


def _build_gif_direct(
    video_path: str,
    output_gif: Path,
    max_frames: int,
    interval: float,
    width: int,
    delay: int,
    loopcount: int,
    colors: Optional[int],
    start: str,
    end: str,
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """Build animated GIF directly from video using ffmpeg palettegen + paletteuse (highest quality)."""
    if not _check_binary("ffmpeg"):
        return {
            "success": False,
            "error": "ffmpeg not found in PATH.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    # Convert delay (1/100s) to ffmpeg fps for output filter
    # delay=10 → 10/100=0.1s per frame → 10 fps
    fps_value = 100.0 / max(delay, 1)

    scale_filter = f"scale={width}:-1:flags=lanczos"
    if colors and 2 <= colors <= 256:
        # Limit palette size
        palette_filters = f"{scale_filter},split[a][b];[a]palettegen=max_colors={colors}[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"
    else:
        palette_filters = f"{scale_filter},split[a][b];[a]palettegen[p];[b][p]paletteuse=dither=bayer:bayer_scale=5"

    cmd = ["ffmpeg", "-y", "-loglevel", "error"]
    if start and start != "00:00:00":
        cmd.extend(["-ss", start])
    cmd.extend(["-i", video_path])
    if end and end != "00:00:00":
        cmd.extend(["-to", end])
    cmd.extend(
        [
            "-vf",
            palette_filters,
            "-frames:v",
            str(max_frames),
            "-loop",
            str(loopcount),
            str(output_gif),
        ]
    )

    rc, stdout, stderr = _run_subprocess(cmd, timeout=300, verbose=verbose)

    if verbose and stderr:
        _cprint(
            f"{DIM}[ffmpeg-gif stderr] {stderr.strip()[:500]}{RESET}",
            no_color=no_color,
        )

    if not output_gif.is_file() or rc != 0:
        return {
            "success": False,
            "error": f"ffmpeg GIF creation failed (rc={rc}): {stderr.strip()[:300]}",
            "exit_code": EXIT_GIF_FAILED,
            "stdout": stdout,
            "stderr": stderr,
        }

    size = output_gif.stat().st_size
    return {
        "success": True,
        "gif_path": str(output_gif.resolve()),
        "file_size_bytes": size,
    }


def _run_gif_make(
    frames: list[str],
    output_gif: Path,
    delay: int,
    loopcount: int,
    optimize: Optional[int],
    lossy: Optional[int],
    colors: Optional[int],
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """Build animated GIF from PNG frames using ffmpeg concat + palettegen/paletteuse, then optional gifsicle optimization."""
    if not _check_binary("gifsicle"):
        return {
            "success": False,
            "error": "gifsicle not found in PATH.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    if not _check_binary("ffmpeg"):
        return {
            "success": False,
            "error": "ffmpeg not found in PATH.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    # Merge PNG frames into a single multi-frame GIF using ffmpeg concat
    # with per-frame palettegen+paletteuse (best quality, no temp files).
    n = len(frames)
    inputs: list[str] = []
    for frame in frames:
        inputs.extend(["-i", frame])
    filters: list[str] = []
    for i in range(n):
        filters.append(f"[{i}:v]palettegen[p{i}]")
        filters.append(f"[{i}:v][p{i}]paletteuse[v{i}]")
    filters.append("".join(f"[v{i}]" for i in range(n)) + f"concat=n={n}:v=1[v]")

    cmd = [
        "ffmpeg",
        "-y",
        "-loglevel",
        "error",
        *inputs,
        "-filter_complex",
        ";".join(filters),
        "-map",
        "[v]",
        "-loop",
        str(loopcount),
        str(output_gif),
    ]

    if verbose:
        _cprint(
            f"{DIM}[ffmpeg-merge] {n} frames via concat+paletteuse{RESET}",
            no_color=no_color,
        )

    proc = subprocess.run(cmd, capture_output=True, timeout=300)
    if verbose and proc.stderr:
        _cprint(
            f"{DIM}[ffmpeg-merge stderr] {proc.stderr.decode('utf-8', errors='replace')[:300]}{RESET}",
            no_color=no_color,
        )

    if not output_gif.is_file() or proc.returncode != 0:
        err = proc.stderr.decode("utf-8", errors="replace") if proc.stderr else ""
        return {
            "success": False,
            "error": f"ffmpeg PNG→GIF merge failed (rc={proc.returncode}): {err.strip()[:300]}",
            "exit_code": EXIT_GIF_FAILED,
        }

    size = output_gif.stat().st_size

    # Optional post-processing with gifsicle (optimize, lossy, colors)
    if _check_binary("gifsicle") and (
        optimize is not None or lossy is not None or colors is not None
    ):
        post_cmd = ["gifsicle"]
        if optimize is not None:
            post_cmd.append(f"--optimize={optimize}")
        if lossy is not None:
            post_cmd.append(f"--lossy={lossy}")
        if colors is not None:
            post_cmd.extend(["--colors", str(colors)])
        post_cmd.extend(["-o", str(output_gif), str(output_gif)])
        post_proc = subprocess.run(post_cmd, capture_output=True, timeout=120)
        if post_proc.returncode == 0 and output_gif.is_file():
            size = output_gif.stat().st_size

    # Re-apply delay/loopcount with gifsicle if needed (only when gifsicle exists)
    if _check_binary("gifsicle") and delay > 0:
        try:
            subprocess.run(
                [
                    "gifsicle",
                    "--delay",
                    str(delay),
                    "--loopcount",
                    str(loopcount),
                    "-o",
                    str(output_gif),
                    str(output_gif),
                ],
                capture_output=True,
                timeout=60,
            )
        except Exception:
            pass

    return {
        "success": True,
        "gif_path": str(output_gif.resolve()),
        "file_size_bytes": size,
        "frame_count": n,
    }


def _run_gif_tool(
    frames: list[str],
    output_gif: Path,
    delay: int,
    loopcount: int,
    optimize: Optional[int],
    lossy: Optional[int],
    colors: Optional[int],
    verbose: bool,
    no_color: bool,
) -> dict[str, Any]:
    """Build animated GIF using gif_tool.py (PNG support depends on its implementation)."""
    gif_tool_path = Path(__file__).resolve().parent / "gif_tool.py"
    if not gif_tool_path.is_file():
        return {
            "success": False,
            "error": "gif_tool.py not found in tools directory.",
            "exit_code": EXIT_FILE_NOT_FOUND,
        }

    cmd = [
        sys.executable,
        str(gif_tool_path),
        "--input-files",
        *frames,
        "--output-file",
        str(output_gif),
        "--mode",
        "merge",
        "--delay",
        str(delay),
        "--loopcount",
        str(loopcount),
    ]
    if optimize is not None:
        cmd.extend(["--optimize", str(optimize)])
    if lossy is not None:
        cmd.extend(["--lossy", str(lossy)])
    if colors is not None:
        cmd.extend(["--colors", str(colors)])

    rc, stdout, stderr = _run_subprocess(cmd, timeout=180, verbose=verbose)

    if verbose:
        if stdout:
            _cprint(
                f"{DIM}[gif_tool stdout] {stdout.strip()[:500]}{RESET}",
                no_color=no_color,
            )
        if stderr:
            _cprint(
                f"{DIM}[gif_tool stderr] {stderr.strip()[:500]}{RESET}",
                no_color=no_color,
            )

    if not output_gif.is_file() or rc != 0:
        return {
            "success": False,
            "error": f"gif_tool failed (rc={rc}): {(stderr or stdout).strip()[:300]}",
            "exit_code": EXIT_GIF_FAILED,
            "stdout": stdout,
            "stderr": stderr,
        }

    size = output_gif.stat().st_size
    return {
        "success": True,
        "gif_path": str(output_gif.resolve()),
        "file_size_bytes": size,
    }


# ---------------------------------------------------------------------------
# Main orchestration
# ---------------------------------------------------------------------------


def execute_tool(
    url: str,
    output_dir: Optional[str] = None,
    gif_name: Optional[str] = None,
    max_frames: int = 30,
    interval: float = 1.0,
    width: int = 480,
    format: str = "png",
    delay: int = 8,
    loopcount: int = 0,
    optimize: Optional[int] = None,
    lossy: Optional[int] = None,
    colors: Optional[int] = None,
    start: str = "00:00:00",
    end: str = "00:00:00",
    keep_frames: bool = False,
    audio_only: bool = False,
    use_aria2: bool = False,
    timeout: int = 300,
    verbose: bool = False,
    no_color: bool = False,
    dry_run: bool = False,
    gif_method: str = "ffmpeg",
) -> dict[str, Any]:
    """Orchestrate the video → frames → GIF pipeline and return a structured result.

    Args:
        gif_method: How to build the final GIF.
          - "ffmpeg" (default): single-pass ffmpeg palettegen+paletteuse, highest quality.
          - "frames": extract PNG frames, then PNG→GIF→merge via gifsicle (for inspection).
          - "tool": delegate to gif_tool.py (uses existing gifsicle wrapper).
    """
    if gif_method not in ("ffmpeg", "frames", "tool"):
        return {
            "success": False,
            "error": f"Unknown gif_method '{gif_method}'. Use 'ffmpeg', 'frames', or 'tool'.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    start_time = time.monotonic()

    if not url or not url.strip():
        return {
            "success": False,
            "error": "URL cannot be empty.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https"):
        return {
            "success": False,
            "error": f"Unsupported URL scheme '{parsed.scheme}'. Use http:// or https://",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    # Resolve output directory
    if output_dir:
        out_dir = Path(output_dir).expanduser().resolve()
    else:
        out_dir = Path.home() / "vid2gif"

    if not _validate_sandbox(out_dir):
        out_dir = Path.home() / "vid2gif"

    out_dir.mkdir(parents=True, exist_ok=True)
    frames_dir = out_dir / "frames"
    frames_dir.mkdir(parents=True, exist_ok=True)

    # Resolve GIF filename
    if not gif_name:
        base = _slugify(Path(parsed.path).stem or "video")
        gif_name = f"{base}.gif"
    if not gif_name.lower().endswith(".gif"):
        gif_name += ".gif"
    output_gif = out_dir / gif_name

    fmt = format.lower().strip()
    if fmt == "jpeg":
        fmt = "jpg"
    if fmt not in ("png", "jpg", "webp"):
        return {
            "success": False,
            "error": f"Unsupported frame format '{format}'. Use png, jpg, or webp.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    if verbose:
        _cprint(f"{NEON_CYAN}[vid2gif] URL: {url}{RESET}", no_color=no_color)
        _cprint(f"{NEON_CYAN}[vid2gif] Output dir: {out_dir}{RESET}", no_color=no_color)
        _cprint(f"{NEON_CYAN}[vid2gif] GIF: {output_gif}{RESET}", no_color=no_color)

    plan = {
        "url": url,
        "output_dir": str(out_dir),
        "gif_path": str(output_gif),
        "frames_dir": str(frames_dir),
        "max_frames": max_frames,
        "interval": interval,
        "width": width,
        "format": fmt,
        "delay": delay,
        "loopcount": loopcount,
        "optimize": optimize,
        "lossy": lossy,
        "colors": colors,
        "start": start,
        "end": end,
        "keep_frames": keep_frames,
        "audio_only": audio_only,
        "use_aria2": use_aria2,
        "gif_method": gif_method,
    }

    if dry_run:
        return {
            "success": True,
            "dry_run": True,
            "plan": plan,
            "exit_code": EXIT_SUCCESS,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }

    # Step 1: download
    if verbose:
        _cprint(
            f"\n{NEON_PINK}━━ Step 1/3: Downloading video via lux (with fallbacks) ━━{RESET}",
            no_color=no_color,
        )

    dl = _run_lux_download(
        url=url,
        output_dir=out_dir,
        audio_only=audio_only,
        use_aria2=use_aria2,
        timeout=timeout,
        verbose=verbose,
        no_color=no_color,
    )
    if not dl.get("success"):
        return {
            "success": False,
            "stage": "download",
            "error": dl.get("error"),
            "step_result": dl,
            "plan": plan,
            "exit_code": dl.get("exit_code", EXIT_DOWNLOAD_FAILED),
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }

    video_path = dl["video_path"]
    if verbose:
        _cprint(f"{NEON_GREEN}✔ Downloaded: {video_path}{RESET}", no_color=no_color)

    # Fast path: build GIF directly from video using ffmpeg palettegen (highest quality)
    if gif_method == "ffmpeg":
        if verbose:
            _cprint(
                f"\n{NEON_PINK}━━ Step 2/2: Building GIF directly (ffmpeg palettegen) ━━{RESET}",
                no_color=no_color,
            )

        gf = _build_gif_direct(
            video_path=video_path,
            output_gif=output_gif,
            max_frames=max_frames,
            interval=interval,
            width=width,
            delay=delay,
            loopcount=loopcount,
            colors=colors,
            start=start,
            end=end,
            verbose=verbose,
            no_color=no_color,
        )
        if not gf.get("success"):
            return {
                "success": False,
                "stage": "gif",
                "error": gf.get("error"),
                "video_path": video_path,
                "step_result": gf,
                "plan": plan,
                "exit_code": gf.get("exit_code", EXIT_GIF_FAILED),
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            }

        if verbose:
            _cprint(
                f"{NEON_GREEN}✔ GIF created: {gf['gif_path']} ({gf['file_size_bytes']} bytes){RESET}",
                no_color=no_color,
            )

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": True,
            "url": url,
            "video_path": video_path,
            "gif_path": gf["gif_path"],
            "file_size_bytes": gf["file_size_bytes"],
            "frame_count": max_frames,
            "frames_kept": False,
            "frames_dir": None,
            "gif_method": gif_method,
            "plan": plan,
            "context": {
                "tool": "vid2gif",
                "version": __version__,
                "is_termux": "com.termux" in os.environ.get("PREFIX", "")
                or Path("/data/data/com.termux").exists(),
            },
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

    # Step 2: extract frames (only for "frames" / "tool" methods)
    if verbose:
        _cprint(
            f"\n{NEON_PINK}━━ Step 2/3: Extracting frames ━━{RESET}",
            no_color=no_color,
        )

    # Clean old frames to avoid mixing with previous runs
    for old in frames_dir.iterdir():
        try:
            old.unlink()
        except OSError:
            pass

    th = _run_gen_thumbs(
        video_path=video_path,
        frames_dir=frames_dir,
        max_frames=max_frames,
        interval=interval,
        width=width,
        fmt=fmt,
        start=start,
        end=end,
        verbose=verbose,
        no_color=no_color,
    )
    if not th.get("success"):
        return {
            "success": False,
            "stage": "frames",
            "error": th.get("error"),
            "video_path": video_path,
            "step_result": th,
            "plan": plan,
            "exit_code": th.get("exit_code", EXIT_THUMB_FAILED),
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }

    frame_count = th["frame_count"]
    if verbose:
        _cprint(
            f"{NEON_GREEN}✔ Extracted {frame_count} frames → {frames_dir}{RESET}",
            no_color=no_color,
        )

    # Step 3: build GIF
    if verbose:
        _cprint(f"\n{NEON_PINK}━━ Step 3/3: Building GIF ━━{RESET}", no_color=no_color)

    if gif_method == "tool":
        gf = _run_gif_tool(
            frames=th["frames"],
            output_gif=output_gif,
            delay=delay,
            loopcount=loopcount,
            optimize=optimize,
            lossy=lossy,
            colors=colors,
            verbose=verbose,
            no_color=no_color,
        )
    else:  # "frames"
        gf = _run_gif_make(
            frames=th["frames"],
            output_gif=output_gif,
            delay=delay,
            loopcount=loopcount,
            optimize=optimize,
            lossy=lossy,
            colors=colors,
            verbose=verbose,
            no_color=no_color,
        )
    if not gf.get("success"):
        return {
            "success": False,
            "stage": "gif",
            "error": gf.get("error"),
            "video_path": video_path,
            "frames_dir": str(frames_dir),
            "frame_count": frame_count,
            "step_result": gf,
            "plan": plan,
            "exit_code": gf.get("exit_code", EXIT_GIF_FAILED),
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }

    if verbose:
        _cprint(
            f"{NEON_GREEN}✔ GIF created: {gf['gif_path']} ({gf['file_size_bytes']} bytes){RESET}",
            no_color=no_color,
        )

    # Cleanup
    if not keep_frames:
        for old in frames_dir.iterdir():
            try:
                old.unlink()
            except OSError:
                pass
        try:
            frames_dir.rmdir()
        except OSError:
            pass

    # Keep the source video by default (so user can re‑run with different GIF params)
    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    return {
        "success": True,
        "url": url,
        "video_path": video_path,
        "gif_path": gf["gif_path"],
        "file_size_bytes": gf["file_size_bytes"],
        "frame_count": frame_count,
        "frames_kept": keep_frames,
        "frames_dir": str(frames_dir) if keep_frames else None,
        "plan": plan,
        "context": {
            "tool": "vid2gif",
            "version": __version__,
            "is_termux": "com.termux" in os.environ.get("PREFIX", "")
            or Path("/data/data/com.termux").exists(),
        },
        "duration_ms": duration_ms,
        "exit_code": EXIT_SUCCESS,
    }


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return
    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"
    box_w = 68
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [VIDEO→GIF ORCHESTRATOR v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")

    if data.get("dry_run"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_YELLOW}DRY RUN — no execution performed{RESET}"
        )
        plan = data.get("plan", {})
        for k, v in plan.items():
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}{k}:{RESET} {v}")
    else:
        if data.get("url"):
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}URL:{RESET}        {data['url']}"
            )
        if data.get("video_path"):
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Video:{RESET}      {data['video_path']}"
            )
        if data.get("gif_path"):
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}GIF:{RESET}        {NEON_GREEN}{data['gif_path']}{RESET}"
            )
        if data.get("file_size_bytes") is not None:
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Size:{RESET}       {data['file_size_bytes']} bytes"
            )
        if data.get("frame_count"):
            _cprint(
                f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Frames:{RESET}     {NEON_YELLOW}{data['frame_count']}{RESET}"
            )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}   {DIM}{data.get('duration_ms', 0)}ms{RESET}"
        )

    if not success and data.get("error"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}      {data['error']}")
    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"
    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(payload)
        sys.stdout.flush()
        return
    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as f:
            f.write(payload)
    except OSError as err:
        sys.stderr.write(f"[vid2gif] LLM_OUTPUT write failed: {err}\n")
        sys.stdout.write(payload)
        sys.stdout.flush()


def run(
    url: str,
    output_dir: Optional[str] = None,
    gif_name: Optional[str] = None,
    max_frames: int = 30,
    interval: float = 1.0,
    width: int = 480,
    format: str = "png",
    delay: int = 8,
    loopcount: int = 0,
    optimize: Optional[int] = None,
    lossy: Optional[int] = None,
    colors: Optional[int] = None,
    start: str = "00:00:00",
    end: str = "00:00:00",
    keep_frames: bool = False,
    audio_only: bool = False,
    use_aria2: bool = False,
    timeout: int = 300,
    verbose: bool = False,
    no_color: bool = False,
    dry_run: bool = False,
    gif_method: str = "ffmpeg",
) -> None:
    """End‑to‑end video‑to‑GIF orchestrator."""
    res = execute_tool(
        url=url,
        output_dir=output_dir,
        gif_name=gif_name,
        max_frames=max_frames,
        interval=interval,
        width=width,
        format=format,
        delay=delay,
        loopcount=loopcount,
        optimize=optimize,
        lossy=lossy,
        colors=colors,
        start=start,
        end=end,
        keep_frames=keep_frames,
        audio_only=audio_only,
        use_aria2=use_aria2,
        timeout=timeout,
        verbose=verbose,
        no_color=no_color,
        dry_run=dry_run,
        gif_method=gif_method,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vid2gif.py",
        description=f"AIChat Video-to-GIF Orchestrator v{__version__}",
    )
    parser.add_argument(
        "--url", "-u", required=True, help="Source video URL (http/https)"
    )
    parser.add_argument(
        "--output-dir",
        help="Directory for GIF + intermediate files (default: ~/vid2gif)",
    )
    parser.add_argument(
        "--gif-name", help="Output GIF filename (default: derived from URL)"
    )
    parser.add_argument(
        "--max-frames", type=int, default=30, help="Max frames to extract (default: 30)"
    )
    parser.add_argument(
        "--interval",
        type=float,
        default=1.0,
        help="Seconds between frames (default: 1.0)",
    )
    parser.add_argument(
        "--width", type=int, default=480, help="Frame width in pixels (default: 480)"
    )
    parser.add_argument(
        "--format",
        default="png",
        choices=["png", "jpg", "webp"],
        help="Intermediate frame format (default: png)",
    )
    parser.add_argument(
        "--delay",
        type=int,
        default=8,
        help="Frame delay in 1/100ths of a second (default: 8)",
    )
    parser.add_argument(
        "--loopcount",
        type=int,
        default=0,
        help="GIF loop count (0=forever, default: 0)",
    )
    parser.add_argument(
        "--optimize",
        type=int,
        choices=[1, 2, 3],
        help="Gifsicle optimization level (1-3)",
    )
    parser.add_argument("--lossy", type=int, help="Gifsicle lossiness (1-200)")
    parser.add_argument("--colors", type=int, help="Reduce palette to N colors (2-256)")
    parser.add_argument(
        "--start", default="00:00:00", help="Start time HH:MM:SS or seconds"
    )
    parser.add_argument(
        "--end",
        default="00:00:00",
        help="End time HH:MM:SS or seconds (00:00:00 = full video)",
    )
    parser.add_argument(
        "--keep-frames",
        action="store_true",
        help="Keep extracted frames after GIF creation",
    )
    parser.add_argument(
        "--audio-only", action="store_true", help="Download audio-only track"
    )
    parser.add_argument(
        "--use-aria2", action="store_true", help="Use aria2 for faster downloads"
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=300,
        help="Total operation timeout in seconds (default: 300)",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable debug logging"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    parser.add_argument(
        "--dry-run", action="store_true", help="Print plan but do not execute"
    )
    parser.add_argument(
        "--gif-method",
        dest="gif_method",
        choices=["ffmpeg", "frames", "tool"],
        default="ffmpeg",
        help="GIF build method: ffmpeg (palettegen, default), frames (extract PNGs first), or tool (gif_tool.py)",
    )
    return parser


def _setup_signal_handlers() -> None:
    def _handle(signum: int, _frame: Any) -> None:
        sys.stderr.write(f"\n[vid2gif] Interrupted (signal {signum}).\n")
        sys.exit(EXIT_INTERRUPTED)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)


if __name__ == "__main__":
    _setup_signal_handlers()
    args = _build_parser().parse_args()
    res = execute_tool(
        url=args.url,
        output_dir=args.output_dir,
        gif_name=args.gif_name,
        max_frames=args.max_frames,
        interval=args.interval,
        width=args.width,
        format=args.format,
        delay=args.delay,
        loopcount=args.loopcount,
        optimize=args.optimize,
        lossy=args.lossy,
        colors=args.colors,
        start=args.start,
        end=args.end,
        keep_frames=args.keep_frames,
        audio_only=args.audio_only,
        use_aria2=args.use_aria2,
        timeout=args.timeout,
        verbose=args.verbose,
        no_color=args.no_color,
        dry_run=args.dry_run,
        gif_method=args.gif_method,
    )
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))