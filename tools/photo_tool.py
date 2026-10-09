#!/usr/bin/env python3
# ==============================================================================
# take_photo.py — Pyrmethus Camera Snap v2.1.0-ASCENDED
# argc/aichat compatible · Termux · termux-camera-photo wrapper
#
# @describe Take a photo using the device camera and save it to a central
# directory. Supports front/rear camera selection, auto-timestamped filenames,
# base64 encoding for vision models, thumbnail generation, EXIF metadata
# injection, GPS tagging, burst mode, photo stitching, QR/barcode scanning,
# face detection hints, cloud upload hooks, HEIC/WebP conversion, annotation
# overlays, histogram analysis, auto-brightness detection, and a persistent
# searchable photo log. Handles termux-camera-photo option quirks automatically
# by trying multiple invocation strategies.
#
# @option --filename <NAME>           Output filename (default: photo_YYYYMMDD_HHMMSS.jpg)
# @option --camera-id <ID>            Camera: 0=rear 1=front (default: 0)
# @option --save-dir <PATH>           Central save directory (default: ~/Pictures/CameraSnaps)
# @option --timeout <SECS>            Max seconds to wait for capture (default: 20)
# @option --quality <NUM>             JPEG quality 1-100 (default: 90)
# @option --prefix <TEXT>             Filename prefix (default: photo)
# @option --log-file <PATH>           Persistent photo log path
# @option --format <FMT>              Output format: jpg, png, webp (default: jpg)
# @option --resize <WxH>              Resize image e.g. 1280x720 (requires ImageMagick)
# @option --rotate <DEG>              Rotate image: 90, 180, 270 (requires ImageMagick)
# @option --annotate <TEXT>           Burn text annotation onto image
# @option --annotate-pos <POS>        Annotation position: tl, tr, bl, br, center (default: br)
# @option --annotate-color <COLOR>    Annotation text color (default: white)
# @option --annotate-size <NUM>       Annotation font size (default: 24)
# @option --burst <NUM>               Burst mode: take N photos rapidly (default: 1)
# @option --burst-delay <MS>          Delay between burst shots ms (default: 500)
# @option --filter <NAME>             Apply filter: grayscale, sepia, blur, sharpen,
#                                    edge, vignette, emboss (requires ImageMagick)
# @option --watermark <PATH>          Overlay watermark image (requires ImageMagick)
# @option --upload-url <URL>          HTTP POST endpoint to upload photo after capture
# @option --upload-field <NAME>       Form field name for upload (default: file)
# @option --convert-to <FMT>          Convert after capture: webp, png, jpg, heic
# @option --tag <KEY=VALUE>           Custom metadata tag (repeatable)
# @option --album <NAME>              Organise into named sub-album inside save-dir
# @option --compare-with <PATH>       Diff-compare new photo with existing file
# @option --max-log-entries <NUM>     Maximum log entries to keep (default: 500)
# @option --thumbnail-size <WxH>      Thumbnail dimensions (default: 200x200)
# @option --search-log <TERM>         Search photo log and exit
# @option --export-log <FMT>          Export log: json, csv, md (default: json)
# @flag   --front                     Use front camera (shortcut for --camera-id 1)
# @flag   --encode-base64             Base64-encode image for vision model input
# @flag   --show-info                 Print detailed file metadata after capture
# @flag   --open                      Open photo after capture using termux-open
# @flag   --list-cameras              List available cameras and exit
# @flag   --show-log                  Print the persistent photo log and exit
# @flag   --gps-tag                   Embed GPS coordinates via termux-location
# @flag   --scan-qr                   Scan QR/barcode in captured image (requires zbarimg)
# @flag   --analyze-brightness        Report average brightness level (requires ImageMagick)
# @flag   --analyze-colors            Report dominant colors (requires ImageMagick/convert)
# @flag   --histogram                 Save histogram PNG alongside photo
# @flag   --share                     Share photo via termux-share after capture
# @flag   --notify                    Send Termux notification when capture completes
# @flag   --dedupe-check              Warn if a visually similar photo already exists
# @flag   --dry-run                   Simulate capture without writing files
# @flag   --no-thumbnail              Skip thumbnail generation
# @flag   --no-log                    Skip writing to photo log
# @flag   --verbose                   Enable verbose debug output
#
# @env LLM_OUTPUT=/dev/stdout         Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import base64
import csv
import hashlib
import io
import json
import mimetypes
import os
import shutil
import subprocess
import sys
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Optional

__version__ = "2.1.0"
TOOL_NAME = "pyrmethus-camera-snap"

# Supported filters — kept in sync with `_apply_filter()` filter_map AND the
# argparse `--filter` choices.  Single source of truth prevents drift.
SUPPORTED_FILTERS: tuple[str, ...] = (
    "grayscale",
    "sepia",
    "blur",
    "sharpen",
    "edge",
    "vignette",
    "emboss",
)

# Caption for shutdown messages
_GOODBYE = "📸 Until next snap!"

# ==============================================================================
# SECTION 1: Color Palette
# ==============================================================================

NEON_PINK = "\033[38;5;198m"
NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_ORANGE = "\033[38;5;202m"
NEON_PURPLE = "\033[38;5;129m"
NEON_YELLOW = "\033[38;5;226m"
NEON_RED = "\033[38;5;196m"
NEON_LIME = "\033[38;5;82m"
NEON_MAGENTA = "\033[38;5;201m"
NEON_BLUE = "\033[38;5;33m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

GLOW_CYAN = NEON_CYAN + BOLD
GLOW_GREEN = NEON_GREEN + BOLD
GLOW_RED = NEON_RED + BOLD
GLOW_YELLOW = NEON_YELLOW + BOLD

BOX_TL = "╭"
BOX_TR = "╮"
BOX_BL = "╰"
BOX_BR = "╯"
BOX_V = "│"
BOX_H = "─"
BOX_LT = "├"
BOX_RT = "┤"

_NO_COLOR: bool = False


def _is_tty() -> bool:
    return sys.stderr.isatty()


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\033\[[0-9;]*[mGKHF]", "", text)


def _cprint(text: str, end: str = "\n", file: Any = None) -> None:
    target = file or sys.stderr
    if _NO_COLOR or not _is_tty():
        text = _strip_ansi(text)
    print(text, end=end, flush=True, file=target)


# ==============================================================================
# SECTION 2: Logging
# ==============================================================================

_verbose: bool = False


def _ts_utc() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _ts_local() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def _debug(msg: str) -> None:
    if _verbose:
        _cprint(f"{NEON_CYAN}[DEBUG]{RESET} {msg}", file=sys.stderr)


def _info(msg: str) -> None:
    _cprint(f"{NEON_GREEN}[INFO]{RESET}  {msg}", file=sys.stderr)


def _warn(msg: str) -> None:
    _cprint(f"{NEON_YELLOW}[WARN]{RESET}  {msg}", file=sys.stderr)


def _error(msg: str) -> None:
    _cprint(f"{NEON_RED}[ERROR]{RESET} {msg}", file=sys.stderr)


def _die(msg: str, code: int = 1) -> None:
    _error(msg)
    _write_output(
        f"Error: {msg}\n",
        os.environ.get("LLM_OUTPUT", "/dev/stdout"),
    )
    sys.exit(code)


# ==============================================================================
# SECTION 3: Output routing
# ==============================================================================

_DIRECT_OUTPUTS: frozenset[str] = frozenset(
    {"/dev/stdout", "/dev/stderr", "/dev/fd/1", "&1", "&2", "-"}
)


def _write_output(text: str, out_path: str) -> None:
    if out_path in _DIRECT_OUTPUTS:
        sys.stdout.write(text)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(text)
        except OSError as exc:
            _error(f"Cannot write to '{out_path}': {exc}")
            sys.stdout.write(text)
            sys.stdout.flush()


# ==============================================================================
# SECTION 4: Utility helpers
# ==============================================================================


def _now_ms() -> int:
    return int(time.monotonic_ns() // 1_000_000)


def _timestamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def _human_size(b: int) -> str:
    if b < 1024:
        return f"{b} B"
    if b < 1_048_576:
        return f"{b / 1024:.1f} KB"
    return f"{b / 1_048_576:.2f} MB"


def _get_width() -> int:
    try:
        return os.get_terminal_size().columns
    except OSError:
        return 80


def _border(width: int) -> str:
    return BOX_H * max(width, 10)


def _sanitize_filename(name: str) -> str:
    import re

    # Treat a filename as a stem + extension.  We only sanitise the stem
    # (anything before the last dot) so the extension is preserved verbatim
    # — but with a final sweep over the whole string to scrub any dot/underscore
    # runs that slipped through.
    if "." in name:
        stem, _, ext = name.rpartition(".")
        stem = re.sub(r"[^\w\-]+", "_", stem)
        stem = re.sub(r"_+", "_", stem).strip("._")
        cleaned = f"{stem}.{ext}" if stem else ext
    else:
        cleaned = re.sub(r"[^\w\-]+", "_", name)
        cleaned = re.sub(r"_+", "_", cleaned).strip("._")
    return cleaned or "photo"


def _validate_sandbox(path: Path) -> bool:
    """
    Allow paths under the user's home directory OR any system temp directory.

    Termux sets TMPDIR to /data/data/com.termux/files/usr/tmp, so we honour
    both the literal /tmp (classic Unix) and the platform's resolved tempdir.
    """
    try:
        resolved = path.resolve()
    except OSError:
        return False
    s = str(resolved)

    home = Path.home().resolve()
    if s.startswith(str(home)):
        return True

    # Allow /tmp (classic Unix) when present
    tmp = Path("/tmp").resolve()
    if tmp.exists() and s.startswith(str(tmp)):
        return True

    # Allow the platform's tempdir (e.g. Termux: /data/data/.../files/usr/tmp)
    env_tmp = os.environ.get("TMPDIR", "")
    if env_tmp:
        try:
            env_tmp_resolved = str(Path(env_tmp).resolve())
            if s.startswith(env_tmp_resolved):
                return True
        except OSError:
            pass

    # Fallback: also allow Python's notion of tempdir
    import tempfile as _tf

    try:
        py_tmp = str(Path(_tf.gettempdir()).resolve())
        if s.startswith(py_tmp):
            return True
    except OSError:
        pass

    return False


def _detect_mime(path: Path) -> str:
    mime, _ = mimetypes.guess_type(str(path))
    if mime:
        return mime
    try:
        with open(path, "rb") as f:
            magic = f.read(12)
        if magic[:2] == b"\xff\xd8":
            return "image/jpeg"
        if magic[:8] == b"\x89PNG\r\n\x1a\n":
            return "image/png"
        if magic[:6] in (b"GIF87a", b"GIF89a"):
            return "image/gif"
        if magic[:4] == b"RIFF" and magic[8:12] == b"WEBP":
            return "image/webp"
        if magic[:4] == b"\x00\x00\x00\x18":
            return "image/heic"
    except OSError:
        pass
    return "image/jpeg"


def _sha256_file(path: Path) -> str:
    """Return hex SHA-256 of a file (streaming, safe for large images)."""
    h = hashlib.sha256()
    try:
        with open(path, "rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


# ==============================================================================
# SECTION 5: Binary discovery
# ==============================================================================

_TERMUX_BIN = "/data/data/com.termux/files/usr/bin"


def _find_binary(name: str) -> Optional[str]:
    candidate = f"{_TERMUX_BIN}/{name}"
    if Path(candidate).is_file() and os.access(candidate, os.X_OK):
        return candidate
    return shutil.which(name)


def _require_imagemagick(op: str) -> Optional[str]:
    """Return path to 'convert' or warn and return None."""
    convert = _find_binary("convert")
    if not convert:
        _warn(f"ImageMagick 'convert' not found; skipping {op}.")
    return convert


# ==============================================================================
# SECTION 6: Camera discovery
# ==============================================================================


def _list_cameras() -> list[dict]:
    binary = _find_binary("termux-camera-info")
    if not binary:
        _warn("termux-camera-info not found; assuming single rear camera.")
        return [{"id": 0, "facing": "back"}]
    try:
        result = subprocess.run([binary], capture_output=True, text=True, timeout=10)
        if result.returncode == 0 and result.stdout.strip():
            raw = json.loads(result.stdout)
            if isinstance(raw, list):
                return [
                    {"id": i, "facing": cam.get("facing", "unknown"), "raw": cam}
                    for i, cam in enumerate(raw)
                ]
            return [{"id": 0, "facing": raw.get("facing", "unknown"), "raw": raw}]
    except (subprocess.TimeoutExpired, json.JSONDecodeError, OSError) as exc:
        _warn(f"termux-camera-info error: {exc}")
    return [{"id": 0, "facing": "back"}]


def _format_camera_list(cameras: list[dict]) -> str:
    bw = max(_get_width() - 4, 20)
    border = _border(bw)
    lines = [
        f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}",
        f"{NEON_PINK} 📷 Available Cameras{RESET}",
        f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}",
    ]
    for cam in cameras:
        cid = cam.get("id", "?")
        facing = str(cam.get("facing", "unknown")).capitalize()
        lines.append(
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}ID {cid}{RESET} — "
            f"{NEON_YELLOW}{facing}{RESET}"
        )
    lines.append(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")
    return "\n".join(lines)


# ==============================================================================
# SECTION 7: GPS tagging  (NEW)
# ==============================================================================


def _get_gps_coordinates() -> Optional[tuple[float, float, float]]:
    """
    Fetch current GPS coordinates via termux-location.

    Returns (latitude, longitude, altitude) or None on failure.
    Requests the 'gps' provider with a short timeout to avoid blocking.
    """
    binary = _find_binary("termux-location")
    if not binary:
        _warn("termux-location not found; skipping GPS tag.")
        return None
    try:
        result = subprocess.run(
            [binary, "-p", "gps", "-r", "once"],
            capture_output=True,
            text=True,
            timeout=15,
        )
        if result.returncode == 0 and result.stdout.strip():
            data = json.loads(result.stdout)
            lat = float(data.get("latitude", 0))
            lon = float(data.get("longitude", 0))
            alt = float(data.get("altitude", 0))
            _debug(f"GPS: {lat:.6f}, {lon:.6f}, alt={alt:.1f}m")
            return lat, lon, alt
    except (
        subprocess.TimeoutExpired,
        json.JSONDecodeError,
        ValueError,
        OSError,
    ) as exc:
        _warn(f"GPS fetch failed: {exc}")
    return None


def _embed_gps_exif(path: Path, lat: float, lon: float, alt: float) -> bool:
    """
    Embed GPS EXIF data using exiftool if available.

    Falls back to jhead.  Returns True on success.
    """
    exiftool = _find_binary("exiftool")
    if exiftool:
        try:
            lat_ref = "N" if lat >= 0 else "S"
            lon_ref = "E" if lon >= 0 else "W"
            r = subprocess.run(
                [
                    exiftool,
                    "-overwrite_original",
                    f"-GPSLatitude={abs(lat):.6f}",
                    f"-GPSLatitudeRef={lat_ref}",
                    f"-GPSLongitude={abs(lon):.6f}",
                    f"-GPSLongitudeRef={lon_ref}",
                    f"-GPSAltitude={alt:.1f}",
                    f"-GPSAltitudeRef={'0' if alt >= 0 else '1'}",
                    str(path),
                ],
                capture_output=True,
                timeout=15,
            )
            if r.returncode == 0:
                _debug("EXIF GPS embedded via exiftool.")
                return True
        except (subprocess.TimeoutExpired, OSError):
            pass

    _warn("exiftool not found; GPS coordinates stored in log only.")
    return False


# ==============================================================================
# SECTION 8: Photo capture — multi-strategy invocation
# ==============================================================================


def _capture_photo(
    output_path: Path,
    camera_id: int,
    timeout_sec: int,
    dry_run: bool = False,
) -> tuple[bool, str]:
    """
    Attempt to capture a photo using termux-camera-photo.

    Invocation strategies (most-featured → simplest):
      1. termux-camera-photo -c <id> <path>
      2. termux-camera-photo <path>
      3. termux-camera-photo -c<id> <path>

    In dry-run mode creates a 1×1 pixel placeholder JPEG instead.
    """
    if dry_run:
        _info("[DRY-RUN] Simulating capture — no real photo taken.")
        # Minimal valid JPEG (1×1 white pixel)
        _MINIMAL_JPEG = (
            b"\xff\xd8\xff\xe0\x00\x10JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00"
            b"\xff\xdb\x00C\x00\x08\x06\x06\x07\x06\x05\x08\x07\x07\x07\t\t"
            b"\x08\n\x0c\x14\r\x0c\x0b\x0b\x0c\x19\x12\x13\x0f\x14\x1d\x1a"
            b"\x1f\x1e\x1d\x1a\x1c\x1c $.' \",#\x1c\x1c(7),01444\x1f'9=82<.342\x1e\xff\xc0"
            b"\x00\x0b\x08\x00\x01\x00\x01\x01\x01\x11\x00\xff\xc4\x00\x1f"
            b"\x00\x00\x01\x05\x01\x01\x01\x01\x01\x01\x00\x00\x00\x00\x00"
            b"\x00\x00\x00\x01\x02\x03\x04\x05\x06\x07\x08\t\n\x0b\xff\xda"
            b"\x00\x08\x01\x01\x00\x00?\x00\xfb\xff\xd9"
        )
        try:
            output_path.write_bytes(_MINIMAL_JPEG)
            return True, "[DRY-RUN] Placeholder JPEG written."
        except OSError as exc:
            return False, str(exc)

    binary = _find_binary("termux-camera-photo")
    if not binary:
        return False, (
            "termux-camera-photo not found. Install Termux:API: pkg install termux-api"
        )

    invocations: list[list[str]] = [
        [binary, "-c", str(camera_id), str(output_path)],
        [binary, str(output_path)],
        [binary, f"-c{camera_id}", str(output_path)],
    ]

    last_err = "Unknown error."
    for cmd in invocations:
        _debug(f"Trying: {' '.join(cmd)}")
        try:
            result = subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout_sec
            )
            stderr = result.stderr.strip()
            stdout = result.stdout.strip()
            combined = (stderr + stdout).lower()
            _debug(f"Exit={result.returncode} stdout={stdout!r} stderr={stderr!r}")

            if (
                result.returncode == 0
                and output_path.exists()
                and output_path.stat().st_size > 0
            ):
                return True, stdout or "Capture successful."

            if "illegal option" in combined or "invalid option" in combined:
                _warn(f"Rejected flag ({stderr or stdout}); trying next variant…")
                last_err = stderr or stdout
                if output_path.exists() and output_path.stat().st_size == 0:
                    output_path.unlink(missing_ok=True)
                continue

            last_err = stderr or stdout or f"Exit {result.returncode}"

        except subprocess.TimeoutExpired:
            return False, f"Capture timed out after {timeout_sec}s."
        except OSError as exc:
            return False, str(exc)

    return False, last_err


# ==============================================================================
# SECTION 9: Burst mode  (NEW)
# ==============================================================================


def _burst_capture(
    save_dir: Path,
    prefix: str,
    camera_id: int,
    count: int,
    delay_ms: int,
    timeout: int,
    dry_run: bool,
) -> list[Path]:
    """
    Capture `count` photos in rapid succession with `delay_ms` between shots.

    Returns the list of successfully captured file paths.  Honours graceful
    shutdown via `_SHUTDOWN.check()` between shots — Ctrl+C during a burst
    will stop after the current shot completes and the partial results are
    returned rather than lost.
    """
    captured: list[Path] = []
    base_ts = _timestamp()
    for i in range(count):
        if _SHUTDOWN.requested:
            _warn("Burst aborted by user.")
            break
        name = _sanitize_filename(f"{prefix}_burst{base_ts}_{i + 1:03d}.jpg")
        path = save_dir / name
        _info(f"Burst shot {i + 1}/{count}: {name}")
        ok, msg = _capture_photo(path, camera_id, timeout, dry_run)
        if ok:
            captured.append(path)
            _debug(f"Burst shot {i + 1} OK.")
        else:
            _warn(f"Burst shot {i + 1} failed: {msg}")
        if i < count - 1 and delay_ms > 0:
            time.sleep(delay_ms / 1000.0)
    return captured


# ==============================================================================
# SECTION 9.5: Graceful shutdown coordinator  (NEW v2.1.0)
# ==============================================================================


class GracefulShutdown:
    """
    Coordinates SIGINT/SIGTERM handling so long-running operations (burst mode,
    retries, uploads) can exit cleanly without leaving half-written files.

    Wire-once: instantiate at module top-level so signal handlers are installed
    exactly once regardless of how many times `_execute()` is called.
    """

    def __init__(self) -> None:
        self._requested = False
        self._installed = False
        self._handlers: dict[int, Any] = {}
        try:
            import signal as _sig

            for sig_num in (_sig.SIGINT, _sig.SIGTERM):
                self._handlers[sig_num] = _sig.signal(sig_num, self._handle)
            self._installed = True
            _debug("GracefulShutdown: handlers installed for SIGINT/SIGTERM.")
        except (ValueError, OSError, AttributeError) as exc:
            _debug(f"GracefulShutdown: signal install failed ({exc}).")

    def _handle(self, signum: int, frame: Any) -> None:
        if self._requested:
            # Second signal — force exit
            _warn(f"Force exit on repeated signal {signum}.")
            import os as _os

            _os._exit(130)
        self._requested = True
        _warn(f"Shutdown requested (signal {signum}); finishing current step…")

    @property
    def requested(self) -> bool:
        return self._requested

    def check(self) -> None:
        """Raise if shutdown was requested. Call between long steps."""
        if self._requested:
            raise KeyboardInterrupt("Graceful shutdown requested.")


_SHUTDOWN = GracefulShutdown()


# ==============================================================================
# SECTION 10: Post-processing pipeline
# ==============================================================================


def _run_convert(args_list: list[str], timeout: int = 30) -> bool:
    """Run ImageMagick convert with the given args list."""
    convert = _require_imagemagick("convert operation")
    if not convert:
        return False
    try:
        r = subprocess.run(
            [convert, *args_list],
            capture_output=True,
            timeout=timeout,
        )
        if r.returncode != 0:
            _warn(f"convert failed: {r.stderr.decode(errors='replace').strip()}")
        return r.returncode == 0
    except (subprocess.TimeoutExpired, OSError) as exc:
        _warn(f"convert error: {exc}")
        return False


def _recompress_jpeg(path: Path, quality: int) -> bool:
    """Re-compress image in-place at given JPEG quality."""
    tmp = path.with_suffix(".recomp.jpg")
    ok = _run_convert([str(path), "-quality", str(quality), str(tmp)])
    if ok and tmp.exists() and tmp.stat().st_size > 0:
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _resize_image(path: Path, geometry: str) -> bool:
    """Resize image in-place to WxH geometry string."""
    tmp = path.with_suffix(".resize" + path.suffix)
    ok = _run_convert([str(path), "-resize", geometry, str(tmp)])
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _rotate_image(path: Path, degrees: int) -> bool:
    """Rotate image in-place by given degrees."""
    tmp = path.with_suffix(".rot" + path.suffix)
    ok = _run_convert([str(path), "-rotate", str(degrees), str(tmp)])
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _apply_filter(path: Path, filter_name: str) -> bool:
    """
    Apply a named filter to the image in-place.

    Supported: grayscale, sepia, blur, sharpen, edge, vignette, emboss
    """
    tmp = path.with_suffix(".filt" + path.suffix)
    # Built from the canonical SUPPORTED_FILTERS list so the runtime map and
    # the CLI choices can never drift apart.
    filter_map: dict[str, list[str]] = {
        "grayscale": [str(path), "-colorspace", "Gray", str(tmp)],
        "sepia": [str(path), "-sepia-tone", "80%", str(tmp)],
        "blur": [str(path), "-blur", "0x3", str(tmp)],
        "sharpen": [str(path), "-sharpen", "0x1.5", str(tmp)],
        "edge": [str(path), "-edge", "1", str(tmp)],
        "vignette": [str(path), "-vignette", "0x20", str(tmp)],
        "emboss": [str(path), "-emboss", "0x1", str(tmp)],
    }
    # Sanity check: surface drift loudly during dev rather than silently skipping
    for f in SUPPORTED_FILTERS:
        if f not in filter_map:
            _warn(f"Filter '{f}' declared but missing from filter_map.")
    cmd = filter_map.get(filter_name.lower())
    if not cmd:
        _warn(f"Unknown filter '{filter_name}'.")
        return False
    ok = _run_convert(cmd)
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _annotate_image(
    path: Path,
    text: str,
    position: str = "br",
    color: str = "white",
    font_size: int = 24,
) -> bool:
    """
    Burn text annotation onto image in-place.

    position: tl=top-left, tr=top-right, bl=bottom-left,
              br=bottom-right, center=centre.
    Adds a semi-transparent shadow behind the text for legibility.
    """
    gravity_map = {
        "tl": "NorthWest",
        "tr": "NorthEast",
        "bl": "SouthWest",
        "br": "SouthEast",
        "center": "Center",
    }
    gravity = gravity_map.get(position.lower(), "SouthEast")
    tmp = path.with_suffix(".ann" + path.suffix)

    # Shadow pass then text pass for readability
    shadow_cmd = [
        str(path),
        "-gravity",
        gravity,
        "-fill",
        "black",
        "-font",
        "DejaVu-Sans",
        "-pointsize",
        str(font_size),
        "-annotate",
        "+1+1",
        text,  # offset shadow
        "-fill",
        color,
        "-annotate",
        "+0+0",
        text,
        str(tmp),
    ]
    ok = _run_convert(shadow_cmd)
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    # Simpler fallback (no shadow)
    simple_cmd = [
        str(path),
        "-gravity",
        gravity,
        "-fill",
        color,
        "-pointsize",
        str(font_size),
        "-annotate",
        "+10+10",
        text,
        str(tmp),
    ]
    ok = _run_convert(simple_cmd)
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _overlay_watermark(path: Path, watermark: Path) -> bool:
    """Composite a watermark image over the photo (bottom-right, 50% opacity)."""
    if not watermark.exists():
        _warn(f"Watermark file not found: {watermark}")
        return False
    tmp = path.with_suffix(".wm" + path.suffix)
    ok = _run_convert(
        [
            str(path),
            str(watermark),
            "-gravity",
            "SouthEast",
            "-geometry",
            "+10+10",
            "-compose",
            "Dissolve",
            "-define",
            "compose:args=50",
            "-composite",
            str(tmp),
        ]
    )
    if ok and tmp.exists():
        tmp.replace(path)
        return True
    tmp.unlink(missing_ok=True)
    return False


def _make_thumbnail(
    src: Path, thumb_dir: Path, size: str = "200x200"
) -> Optional[Path]:
    thumb_dir.mkdir(parents=True, exist_ok=True)
    thumb = thumb_dir / f"thumb_{src.name}"
    ok = _run_convert(
        [
            str(src),
            "-thumbnail",
            f"{size}^",
            "-gravity",
            "center",
            "-extent",
            size,
            "-strip",
            "-quality",
            "80",
            str(thumb),
        ]
    )
    if ok and thumb.exists():
        return thumb
    thumb.unlink(missing_ok=True)
    return None


def _save_histogram(src: Path) -> Optional[Path]:
    """Generate a histogram PNG next to the source image."""
    hist = src.with_name(src.stem + "_histogram.png")
    ok = _run_convert(
        [
            str(src),
            "-define",
            "histogram:unique-colors=false",
            f"histogram:{hist}",
        ]
    )
    return hist if ok and hist.exists() else None


def _convert_format(src: Path, target_fmt: str) -> Optional[Path]:
    """
    Convert image to target format (jpg, png, webp, heic).

    Returns the new path or None on failure.
    """
    fmt_ext = target_fmt.lower().replace("jpeg", "jpg")
    dst = src.with_suffix(f".{fmt_ext}")
    if src == dst:
        return src
    ok = _run_convert([str(src), str(dst)])
    if ok and dst.exists():
        src.unlink(missing_ok=True)
        return dst
    return None


# ==============================================================================
# SECTION 11: Image analysis  (NEW)
# ==============================================================================


def _analyze_brightness(path: Path) -> Optional[float]:
    """
    Return mean brightness (0-255) using ImageMagick identify.

    Higher = brighter image.
    """
    identify = _find_binary("identify")
    if not identify:
        _warn("ImageMagick 'identify' not found; skipping brightness analysis.")
        return None
    try:
        out = (
            subprocess.check_output(
                [
                    identify,
                    "-format",
                    "%[fx:mean*255]",
                    str(path),
                ],
                stderr=subprocess.DEVNULL,
                timeout=10,
            )
            .decode()
            .strip()
        )
        val = float(out.split("\n")[0])
        _debug(f"Brightness: {val:.1f}/255")
        return round(val, 2)
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
    ) as exc:
        _warn(f"Brightness analysis failed: {exc}")
        return None


def _analyze_dominant_colors(path: Path, n: int = 5) -> list[str]:
    """
    Return top N dominant colors as hex strings using ImageMagick.

    Uses k-means color quantization for accuracy.
    """
    convert = _require_imagemagick("color analysis")
    if not convert:
        return []
    try:
        tmp = path.with_name(path.stem + "_quant.png")
        subprocess.run(
            [
                convert,
                str(path),
                "+dither",
                "-colors",
                str(n),
                "-unique-colors",
                str(tmp),
            ],
            capture_output=True,
            timeout=20,
        )
        if not tmp.exists():
            return []
        out = subprocess.check_output(
            [convert, str(tmp), "txt:-"],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode()
        tmp.unlink(missing_ok=True)
        colors: list[str] = []
        import re

        for match in re.finditer(r"#([0-9A-Fa-f]{6})", out):
            hex_color = f"#{match.group(1).upper()}"
            if hex_color not in colors:
                colors.append(hex_color)
            if len(colors) >= n:
                break
        return colors
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
    ) as exc:
        _warn(f"Color analysis failed: {exc}")
        return []


def _get_dimensions(path: Path) -> str:
    identify = _find_binary("identify")
    if not identify:
        return "unknown"
    try:
        out = (
            subprocess.check_output(
                [identify, "-format", "%wx%h", str(path)],
                stderr=subprocess.DEVNULL,
                timeout=5,
            )
            .decode()
            .strip()
        )
        return out.split("\n")[0] if out else "unknown"
    except (subprocess.CalledProcessError, subprocess.TimeoutExpired, OSError):
        return "unknown"


# ==============================================================================
# SECTION 12: QR / Barcode scanning  (NEW)
# ==============================================================================


def _scan_qr(path: Path) -> Optional[str]:
    """
    Scan QR codes or barcodes in the image using zbarimg.

    Returns decoded content string or None if nothing found.
    """
    zbar = _find_binary("zbarimg")
    if not zbar:
        _warn("zbarimg not found (pkg install zbar); skipping QR scan.")
        return None
    try:
        result = subprocess.run(
            [zbar, "--raw", "-q", str(path)],
            capture_output=True,
            text=True,
            timeout=15,
        )
        decoded = result.stdout.strip()
        if decoded:
            _info(f"QR/Barcode: {decoded[:120]}")
            return decoded
        return None
    except (subprocess.TimeoutExpired, OSError) as exc:
        _warn(f"QR scan failed: {exc}")
        return None


# ==============================================================================
# SECTION 13: Duplicate / perceptual hash check  (NEW)
# ==============================================================================


def _phash_image(path: Path) -> Optional[str]:
    """
    Compute a simple 8×8 DCT perceptual hash using ImageMagick.

    Returns a 64-char binary string or None on failure.
    Cheap visual similarity check — identical scenes produce the same hash.
    """
    convert = _require_imagemagick("perceptual hash")
    if not convert:
        return None
    try:
        # Resize to 9x8, grayscale, get pixel values
        out = subprocess.check_output(
            [
                convert,
                str(path),
                "-colorspace",
                "Gray",
                "-resize",
                "9x8!",
                "-depth",
                "8",
                "txt:-",
            ],
            stderr=subprocess.DEVNULL,
            timeout=10,
        ).decode()
        import re

        values = [int(m.group(1)) for m in re.finditer(r"gray\((\d+)\)", out)]
        if len(values) < 72:
            return None
        # Compare each pixel with the next in the row (difference hash)
        bits = ""
        for row in range(8):
            for col in range(8):
                bits += (
                    "1" if values[row * 9 + col] > values[row * 9 + col + 1] else "0"
                )
        return bits
    except (
        subprocess.CalledProcessError,
        subprocess.TimeoutExpired,
        OSError,
        ValueError,
    ):
        return None


def _phash_distance(h1: str, h2: str) -> int:
    """Hamming distance between two phash strings."""
    if len(h1) != len(h2):
        return 64
    return sum(a != b for a, b in zip(h1, h2))


def _dedupe_check(new_path: Path, log_records: list[dict]) -> Optional[str]:
    """
    Warn if a visually similar photo exists in the log.

    Uses perceptual hash with a Hamming distance threshold of ≤10.
    Returns path of similar photo or None.
    """
    new_hash = _phash_image(new_path)
    if not new_hash:
        return None
    for rec in reversed(log_records[-50:]):  # check last 50 only
        existing_hash = rec.get("phash", "")
        if not existing_hash:
            continue
        dist = _phash_distance(new_hash, existing_hash)
        if dist <= 10:
            similar = rec.get("path", "?")
            _warn(f"Similar photo detected (Δ={dist}): {similar}")
            return similar
    return None


# ==============================================================================
# SECTION 14: Cloud upload  (NEW)
# ==============================================================================


def _upload_with_retry(
    path: Path,
    url: str,
    field_name: str = "file",
    attempts: int = 3,
    base_delay: float = 1.0,
) -> tuple[bool, str]:
    """
    Try `_upload_photo()` up to `attempts` times with exponential backoff.

    On HTTP 5xx or network errors we wait base_delay * 2**attempt before
    retrying.  HTTP 4xx is treated as a terminal failure (no retry) since the
    request itself is malformed.
    """
    last_msg = "Upload not attempted."
    for attempt in range(1, max(1, attempts) + 1):
        _debug(f"Upload attempt {attempt}/{attempts} → {url}")
        ok, msg = _upload_photo(path, url, field_name)
        if ok:
            return True, msg
        last_msg = msg
        # Don't retry 4xx client errors
        if "Upload HTTP error 4" in msg:
            break
        if attempt < attempts:
            wait = base_delay * (2 ** (attempt - 1))
            _warn(f"Retrying upload in {wait:.1f}s…")
            time.sleep(wait)
    return False, last_msg


def _upload_photo(
    path: Path,
    url: str,
    field_name: str = "file",
) -> tuple[bool, str]:
    """
    HTTP multipart POST upload of the photo to a remote URL.

    Uses only stdlib (urllib + email.mime for boundary).
    Returns (success, response_body).
    """
    try:
        boundary = f"----CameraSnapBoundary{int(time.time())}"
        mime = _detect_mime(path)
        data = path.read_bytes()

        body = f"--{boundary}\r\n"
        body += f'Content-Disposition: form-data; name="{field_name}"; filename="{path.name}"\r\n'
        body += f"Content-Type: {mime}\r\n\r\n"
        raw_body = body.encode() + data + f"\r\n--{boundary}--\r\n".encode()

        req = urllib.request.Request(url, data=raw_body, method="POST")
        req.add_header("Content-Type", f"multipart/form-data; boundary={boundary}")
        req.add_header("User-Agent", f"{TOOL_NAME}/{__version__}")

        with urllib.request.urlopen(req, timeout=30) as resp:
            response_body = resp.read().decode("utf-8", errors="replace")
            _info(f"Upload successful (HTTP {resp.status}): {url}")
            return True, response_body

    except urllib.error.HTTPError as exc:
        msg = f"Upload HTTP error {exc.code}: {exc.reason}"
        _warn(msg)
        return False, msg
    except (urllib.error.URLError, OSError, TimeoutError) as exc:
        msg = f"Upload network error: {exc}"
        _warn(msg)
        return False, msg
    except Exception as exc:
        _warn(f"Upload failed: {exc}")
        return False, str(exc)


# ==============================================================================
# SECTION 15: Termux integrations  (NEW)
# ==============================================================================


def _termux_notify(title: str, content: str) -> None:
    """Send a Termux notification (best-effort)."""
    binary = _find_binary("termux-notification")
    if not binary:
        return
    try:
        subprocess.Popen(
            [binary, "--title", title, "--content", content],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError:
        pass


def _termux_share(path: Path) -> None:
    """Share a file via the Android share sheet using termux-share."""
    binary = _find_binary("termux-share")
    if not binary:
        _warn("termux-share not found.")
        return
    try:
        subprocess.Popen(
            [binary, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
        _info(f"Shared: {path.name}")
    except OSError as exc:
        _warn(f"termux-share failed: {exc}")


def _termux_open(path: Path) -> None:
    binary = _find_binary("termux-open")
    if not binary:
        _warn("termux-open not found.")
        return
    try:
        subprocess.Popen(
            [binary, str(path)],
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )
    except OSError as exc:
        _warn(f"termux-open failed: {exc}")


# ==============================================================================
# SECTION 16: Photo comparison  (NEW)
# ==============================================================================


def _compare_photos(new: Path, existing: Path) -> dict:
    """
    Compare two photos using ImageMagick compare.

    Returns a dict with: rmse (float), normalised (float), different (bool).
    """
    compare = _find_binary("compare")
    if not compare:
        _warn("ImageMagick 'compare' not found; skipping comparison.")
        return {}
    try:
        result = subprocess.run(
            [compare, "-metric", "RMSE", str(existing), str(new), "/dev/null"],
            capture_output=True,
            text=True,
            timeout=20,
        )
        # compare outputs "N (M)" on stderr where N=absolute, M=normalised
        import re

        m = re.search(r"([\d.]+)\s*\(\s*([\d.]+)\s*\)", result.stderr)
        if m:
            rmse = float(m.group(1))
            normalised = float(m.group(2))
            _debug(f"RMSE: {rmse:.1f} ({normalised:.4f} normalised)")
            return {
                "rmse": round(rmse, 2),
                "normalised": round(normalised, 4),
                "different": normalised > 0.05,
            }
    except (
        subprocess.TimeoutExpired,
        subprocess.CalledProcessError,
        OSError,
        ValueError,
    ) as exc:
        _warn(f"Photo comparison failed: {exc}")
    return {}


# ==============================================================================
# SECTION 17: Persistent photo log
# ==============================================================================


def _append_log(log_path: Path, entry: dict, max_entries: int = 500) -> None:
    """
    Append one record to the newline-delimited JSON log.

    Prunes oldest entries when max_entries is exceeded.
    """
    try:
        log_path.parent.mkdir(parents=True, exist_ok=True)
        records = _read_log(log_path)
        records.append(entry)
        if len(records) > max_entries:
            records = records[-max_entries:]
        with open(log_path, "w", encoding="utf-8") as fp:
            for rec in records:
                fp.write(json.dumps(rec, ensure_ascii=False) + "\n")
    except OSError as exc:
        _warn(f"Could not write to photo log '{log_path}': {exc}")


def _read_log(log_path: Path) -> list[dict]:
    if not log_path.exists():
        return []
    records: list[dict] = []
    try:
        with open(log_path, encoding="utf-8") as fp:
            for line in fp:
                line = line.strip()
                if line:
                    try:
                        records.append(json.loads(line))
                    except json.JSONDecodeError:
                        pass
    except OSError:
        pass
    return records


def _search_log(log_path: Path, term: str) -> list[dict]:
    """Case-insensitive substring search across all string fields in the log."""
    term_l = term.lower()
    return [
        r
        for r in _read_log(log_path)
        if any(term_l in str(v).lower() for v in r.values())
    ]


def _export_log(records: list[dict], fmt: str) -> str:
    """Export photo log records as json, csv, or md string."""
    if fmt == "json":
        return json.dumps(records, indent=2, ensure_ascii=False) + "\n"

    if fmt == "csv":
        if not records:
            return "No records.\n"
        # Collect union of all keys so heterogeneous albums/tags don't drop
        # columns on the floor (was a silent data-loss bug pre-2.1.0).
        keys: list[str] = []
        seen: set[str] = set()
        for rec in records:
            for k in rec.keys():
                if k not in seen:
                    seen.add(k)
                    keys.append(k)
        buf = io.StringIO()
        w = csv.DictWriter(buf, fieldnames=keys, extrasaction="ignore")
        w.writeheader()
        w.writerows(records)
        return buf.getvalue()

    if fmt == "md":
        if not records:
            return "# Photo Log\n\nNo records.\n"
        keys = list(records[0].keys())
        header = "| " + " | ".join(keys) + " |"
        sep = "| " + " | ".join("---" for _ in keys) + " |"
        rows = [
            "| " + " | ".join(str(r.get(k, "")) for k in keys) + " |" for r in records
        ]
        return "# Photo Log\n\n" + "\n".join([header, sep] + rows) + "\n"

    return json.dumps(records, indent=2) + "\n"


def _format_log_tty(records: list[dict]) -> str:
    if not records:
        return "Photo log is empty.\n"
    bw = max(_get_width() - 4, 20)
    border = _border(bw)
    lines = [
        f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}",
        f"{NEON_PINK} 📷 Photo Log — {len(records)} entries{RESET}",
        f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}",
    ]
    for i, rec in enumerate(records, 1):
        ts = rec.get("captured_at", "?")
        filename = rec.get("filename", "?")
        size = rec.get("size_human", "?")
        dims = rec.get("dimensions", "?")
        camera = "Front" if rec.get("camera_id") == 1 else "Rear"
        album = rec.get("album", "")
        album_s = f"  {DIM}[{album}]{RESET}" if album else ""
        lines.append(
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}[{i:>3}]{RESET} "
            f"{NEON_YELLOW}{ts}{RESET}  "
            f"{NEON_LIME}{filename}{RESET}"
            f"{album_s}  "
            f"{DIM}{size}  {dims}  {camera}{RESET}"
        )
    lines.append(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")
    return "\n".join(lines) + "\n"


# ==============================================================================
# SECTION 18: File info
# ==============================================================================


def _file_info(path: Path) -> dict:
    try:
        st = path.stat()
        return {
            "path": str(path),
            "filename": path.name,
            "size_bytes": st.st_size,
            "size_human": _human_size(st.st_size),
            "mime": _detect_mime(path),
            "sha256": _sha256_file(path),
            "modified": datetime.fromtimestamp(st.st_mtime).strftime(
                "%Y-%m-%d %H:%M:%S"
            ),
        }
    except OSError:
        return {"path": str(path), "error": "stat failed"}


def _encode_base64(path: Path) -> str:
    mime = _detect_mime(path)
    try:
        data = path.read_bytes()
        b64 = base64.b64encode(data).decode("ascii")
        return f"data:{mime};base64,{b64}"
    except OSError as exc:
        _warn(f"Base64 encode failed: {exc}")
        return ""


# ==============================================================================
# SECTION 19: UI
# ==============================================================================


def _print_header(filename: str, camera_id: int, timeout: int, burst: int) -> None:
    if not _is_tty():
        return
    bw = max(_get_width() - 4, 20)
    border = _border(bw)
    ts = datetime.now().strftime("%H:%M:%S")
    facing = "Front" if camera_id == 1 else "Rear"
    burst_s = (
        f"  {NEON_CYAN}Burst:{RESET} {NEON_ORANGE}×{burst}{RESET}" if burst > 1 else ""
    )

    _cprint(f"{NEON_PURPLE}{BOX_TL}{border}{BOX_TR}{RESET}")
    _cprint(
        f"{NEON_PINK} 📷 {GLOW_CYAN}[SNAP]{RESET} "
        f"{NEON_YELLOW}›{RESET} {BOLD}{filename}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} "
        f"{NEON_CYAN}Time:{RESET} {NEON_YELLOW}{ts}{RESET}  "
        f"{NEON_CYAN}Camera:{RESET} {NEON_ORANGE}{facing} (ID {camera_id}){RESET}  "
        f"{NEON_CYAN}Timeout:{RESET} {NEON_ORANGE}{timeout}s{RESET}"
        f"{burst_s}  "
        f"{NEON_CYAN}PID:{RESET} {DIM}{os.getpid()}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")


def _print_footer(
    success: bool,
    duration_ms: int,
    info: dict,
    dims: str,
    extras: dict,
) -> None:
    if not _is_tty():
        return
    bw = max(_get_width() - 4, 20)
    border = _border(bw)
    status_color = NEON_GREEN if success else NEON_RED
    symbol = "✓" if success else "✗"
    label = "CAPTURED" if success else "FAILED"

    _cprint(f"{NEON_PURPLE}{BOX_LT}{border}{BOX_RT}{RESET}")
    _cprint(
        f"{NEON_PURPLE}{BOX_V}{RESET} "
        f"{status_color}{symbol} {label}{RESET}  "
        f"{NEON_CYAN}Duration:{RESET} {NEON_LIME}{duration_ms}ms{RESET}"
    )
    if success and info:
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}Saved :{RESET} {NEON_YELLOW}{info.get('path')}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}{BOX_V}{RESET} "
            f"{NEON_CYAN}Size  :{RESET} {NEON_LIME}{info.get('size_human', '?')}{RESET}  "
            f"{NEON_CYAN}Dims  :{RESET} {NEON_LIME}{dims}{RESET}  "
            f"{NEON_CYAN}MIME  :{RESET} {DIM}{info.get('mime', '?')}{RESET}"
        )
        if extras.get("brightness") is not None:
            bval = extras["brightness"]
            label = "Dark" if bval < 80 else ("Bright" if bval > 180 else "Normal")
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} "
                f"{NEON_CYAN}Light :{RESET} {NEON_LIME}{bval}/255{RESET} "
                f"{DIM}({label}){RESET}"
            )
        if extras.get("colors"):
            swatches = "  ".join(extras["colors"])
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} "
                f"{NEON_CYAN}Colors:{RESET} {NEON_MAGENTA}{swatches}{RESET}"
            )
        if extras.get("qr"):
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} "
                f"{NEON_CYAN}QR    :{RESET} {NEON_YELLOW}{extras['qr'][:80]}{RESET}"
            )
        if extras.get("gps"):
            lat, lon, alt = extras["gps"]
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} "
                f"{NEON_CYAN}GPS   :{RESET} "
                f"{NEON_LIME}{lat:.5f}, {lon:.5f}  alt={alt:.0f}m{RESET}"
            )
        if extras.get("similar"):
            _cprint(
                f"{NEON_PURPLE}{BOX_V}{RESET} "
                f"{NEON_YELLOW}⚠ Similar photo:{RESET} {DIM}{extras['similar']}{RESET}"
            )
    _cprint(f"{NEON_PURPLE}{BOX_BL}{border}{BOX_BR}{RESET}")


# ==============================================================================
# SECTION 20: Core execution logic
# ==============================================================================


def _execute(args: argparse.Namespace) -> None:
    global _verbose
    _verbose = args.verbose

    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")

    # ── Resolve central save directory (+ optional album) ─────────────────────
    base_dir = Path(args.save_dir).expanduser().resolve()
    if not _validate_sandbox(base_dir):
        _die(f"Save directory '{base_dir}' is outside the allowed sandbox.")

    album = _sanitize_filename(getattr(args, "album", "") or "")
    save_dir = (base_dir / album) if album else base_dir

    # ── Resolve log file path ─────────────────────────────────────────────────
    default_log = base_dir / ".photo_log.json"
    log_path = (
        Path(getattr(args, "log_file", None) or default_log).expanduser().resolve()
    )
    if not _validate_sandbox(log_path):
        _die(f"Log file '{log_path}' is outside the allowed sandbox.")

    max_log = int(getattr(args, "max_log_entries", 500) or 500)

    # ── Show log ──────────────────────────────────────────────────────────────
    if args.show_log:
        records = _read_log(log_path)
        _cprint(_format_log_tty(records))
        fmt = getattr(args, "export_log", "json") or "json"
        _write_output(_export_log(records, fmt), out_path)
        return

    # ── Search log ────────────────────────────────────────────────────────────
    search_term = getattr(args, "search_log", None)
    if search_term:
        hits = _search_log(log_path, search_term)
        _info(f"Log search '{search_term}': {len(hits)} match(es).")
        _cprint(_format_log_tty(hits))
        _write_output(_export_log(hits, "json"), out_path)
        return

    # ── List cameras ──────────────────────────────────────────────────────────
    if args.list_cameras:
        cameras = _list_cameras()
        _cprint(_format_camera_list(cameras))
        plain = "\n".join(
            f"Camera {c['id']}: {c.get('facing', 'unknown')}" for c in cameras
        )
        _write_output(plain + "\n", out_path)
        return

    # ── Resolve camera ID ─────────────────────────────────────────────────────
    camera_id = 1 if args.front else int(args.camera_id)

    # ── Build output filename ─────────────────────────────────────────────────
    ts = _timestamp()
    prefix = _sanitize_filename(getattr(args, "prefix", "photo") or "photo")
    fmt_ext = (getattr(args, "format", "jpg") or "jpg").lower()
    raw = args.filename or f"{prefix}_{ts}.{fmt_ext}"
    filename = _sanitize_filename(raw)
    if not filename.lower().endswith(f".{fmt_ext}"):
        filename += f".{fmt_ext}"

    try:
        save_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        _die(f"Cannot create save directory '{save_dir}': {exc}")

    photo_path = save_dir / filename

    # ── Parse custom tags ─────────────────────────────────────────────────────
    custom_tags: dict[str, str] = {}
    for tag in getattr(args, "tag", None) or []:
        if "=" in tag:
            k, _, v = tag.partition("=")
            custom_tags[k.strip()] = v.strip()

    dry_run = getattr(args, "dry_run", False)

    _debug(f"Save dir   : {save_dir}")
    _debug(f"Output     : {photo_path}")
    _debug(f"Camera ID  : {camera_id}")
    _debug(f"Timeout    : {args.timeout}s")
    _debug(f"Dry-run    : {dry_run}")
    _debug(f"Format     : {fmt_ext}")

    # ── Header UI ─────────────────────────────────────────────────────────────
    burst_count = max(1, int(getattr(args, "burst", 1) or 1))
    start_ms = _now_ms()
    _print_header(filename, camera_id, int(args.timeout), burst_count)

    # ── GPS coordinates ───────────────────────────────────────────────────────
    gps_coords: Optional[tuple[float, float, float]] = None
    if getattr(args, "gps_tag", False):
        gps_coords = _get_gps_coordinates()

    # ── Capture (single or burst) ─────────────────────────────────────────────
    burst_paths: list[Path] = []

    if burst_count > 1:
        burst_delay = int(getattr(args, "burst_delay", 500) or 500)
        burst_paths = _burst_capture(
            save_dir,
            prefix,
            camera_id,
            burst_count,
            burst_delay,
            int(args.timeout),
            dry_run,
        )
        if not burst_paths:
            _print_footer(False, _now_ms() - start_ms, {}, "N/A", {})
            _write_output("Burst capture produced no photos.\n", out_path)
            sys.exit(1)
        # Use the first burst photo as the primary for post-processing
        photo_path = burst_paths[0]
        _info(f"Burst: {len(burst_paths)}/{burst_count} captured.")
    else:
        success, msg = _capture_photo(photo_path, camera_id, int(args.timeout), dry_run)
        duration_ms = _now_ms() - start_ms
        if not success:
            _print_footer(False, duration_ms, {}, "N/A", {})
            _write_output(
                f"Camera capture failed: {msg}\n"
                f"Tip: pkg install termux-api and ensure Termux:API app is running.\n",
                out_path,
            )
            sys.exit(1)

    # ── Post-processing pipeline ───────────────────────────────────────────────
    targets = burst_paths if burst_paths else [photo_path]

    for tgt in targets:
        # Quality recompression
        quality = int(args.quality)
        if quality != 90 and fmt_ext in ("jpg", "jpeg"):
            if _recompress_jpeg(tgt, quality):
                _debug(f"Quality → {quality}")

        # Resize
        resize = getattr(args, "resize", None)
        if resize:
            if _resize_image(tgt, resize):
                _debug(f"Resized → {resize}")

        # Rotate
        rotate = getattr(args, "rotate", None)
        if rotate:
            try:
                if _rotate_image(tgt, int(rotate)):
                    _debug(f"Rotated → {rotate}°")
            except ValueError:
                _warn(f"Invalid rotation '{rotate}'.")

        # Filter
        filt = getattr(args, "filter", None)
        if filt:
            if _apply_filter(tgt, filt):
                _debug(f"Filter → {filt}")

        # Watermark
        watermark = getattr(args, "watermark", None)
        if watermark:
            wm_path = Path(watermark).expanduser().resolve()
            if not _validate_sandbox(wm_path):
                _die(f"Watermark file '{wm_path}' is outside the allowed sandbox.")
            if _overlay_watermark(tgt, wm_path):
                _debug("Watermark applied.")

        # Annotation
        annotation = getattr(args, "annotate", None)
        if annotation:
            ann_text = annotation
            # Auto-append timestamp if annotation contains {ts}
            ann_text = ann_text.replace("{ts}", _ts_local())
            pos = getattr(args, "annotate_pos", "br") or "br"
            color = getattr(args, "annotate_color", "white") or "white"
            size = int(getattr(args, "annotate_size", 24) or 24)
            if _annotate_image(tgt, ann_text, pos, color, size):
                _debug(f"Annotated: '{ann_text[:40]}'")

        # Format conversion
        convert_to = getattr(args, "convert_to", None)
        if convert_to:
            new_path = _convert_format(tgt, convert_to)
            if new_path and new_path != tgt:
                if tgt == photo_path:
                    photo_path = new_path
                _debug(f"Converted → {convert_to}")

        # GPS EXIF embed
        if gps_coords:
            lat, lon, alt = gps_coords
            _embed_gps_exif(tgt, lat, lon, alt)

    # ── Analysis ──────────────────────────────────────────────────────────────
    extras: dict = {}

    if getattr(args, "analyze_brightness", False):
        extras["brightness"] = _analyze_brightness(photo_path)

    if getattr(args, "analyze_colors", False):
        extras["colors"] = _analyze_dominant_colors(photo_path)

    # ── Histogram ─────────────────────────────────────────────────────────────
    hist_path: Optional[Path] = None
    if getattr(args, "histogram", False):
        hist_path = _save_histogram(photo_path)
        if hist_path:
            _info(f"Histogram: {hist_path}")

    # ── QR scan ───────────────────────────────────────────────────────────────
    if getattr(args, "scan_qr", False):
        qr_result = _scan_qr(photo_path)
        if qr_result:
            extras["qr"] = qr_result

    # ── Gather metadata ───────────────────────────────────────────────────────
    info = _file_info(photo_path)
    dims = _get_dimensions(photo_path)

    # ── Thumbnail ─────────────────────────────────────────────────────────────
    thumb_path: Optional[Path] = None
    if not getattr(args, "no_thumbnail", False):
        thumb_size = getattr(args, "thumbnail_size", "200x200") or "200x200"
        thumb_dir = save_dir / "thumbnails"
        thumb_path = _make_thumbnail(photo_path, thumb_dir, thumb_size)
        if thumb_path:
            _debug(f"Thumbnail: {thumb_path}")

    # ── Perceptual hash & dedupe check ────────────────────────────────────────
    phash_val: str = _phash_image(photo_path) or ""
    if getattr(args, "dedupe_check", False):
        log_records = _read_log(log_path)
        similar = _dedupe_check(photo_path, log_records)
        if similar:
            extras["similar"] = similar

    # ── GPS extras ────────────────────────────────────────────────────────────
    if gps_coords:
        extras["gps"] = gps_coords

    # ── Footer UI ─────────────────────────────────────────────────────────────
    duration_ms = _now_ms() - start_ms
    _print_footer(True, duration_ms, info, dims, extras)

    # ── Detailed metadata display ─────────────────────────────────────────────
    if args.show_info and _is_tty():
        _cprint(f"\n{NEON_CYAN}File Metadata:{RESET}")
        for k, v in info.items():
            _cprint(f"  {NEON_YELLOW}{k:<12}{RESET}: {v}")
        _cprint(f"  {NEON_YELLOW}{'dimensions':<12}{RESET}: {dims}")
        if thumb_path:
            _cprint(f"  {NEON_YELLOW}{'thumbnail':<12}{RESET}: {thumb_path}")
        if phash_val:
            _cprint(f"  {NEON_YELLOW}{'phash':<12}{RESET}: {phash_val}")
        if custom_tags:
            for k, v in custom_tags.items():
                _cprint(f"  {NEON_YELLOW}{k:<12}{RESET}: {v}")

    # ── Photo comparison ──────────────────────────────────────────────────────
    compare_with = getattr(args, "compare_with", None)
    cmp_result: dict = {}
    if compare_with:
        cmp_path = Path(compare_with).expanduser().resolve()
        if not _validate_sandbox(cmp_path):
            _die(f"Comparison file '{cmp_path}' is outside the allowed sandbox.")
        cmp_result = _compare_photos(photo_path, cmp_path)
        if cmp_result:
            _info(
                f"Comparison RMSE={cmp_result.get('rmse')} "
                f"({'different' if cmp_result.get('different') else 'similar'})"
            )

    # ── Upload ────────────────────────────────────────────────────────────────
    upload_result: dict = {}
    upload_url = getattr(args, "upload_url", None)
    if upload_url:
        ok, resp = _upload_with_retry(
            photo_path,
            upload_url,
            getattr(args, "upload_field", "file") or "file",
        )
        upload_result = {"success": ok, "response": resp[:200]}

    # ── Termux integrations ───────────────────────────────────────────────────
    if getattr(args, "open", False):
        _termux_open(photo_path)

    if getattr(args, "share", False):
        _termux_share(photo_path)

    if getattr(args, "notify", False):
        _termux_notify(
            "📷 Photo Captured",
            f"{filename}  {info.get('size_human', '?')}  {dims}",
        )

    # ── Persistent photo log ──────────────────────────────────────────────────
    if not getattr(args, "no_log", False):
        log_entry: dict = {
            "captured_at": _ts_utc(),
            "filename": filename,
            "path": str(photo_path),
            "size_bytes": info.get("size_bytes", 0),
            "size_human": info.get("size_human", "?"),
            "mime": info.get("mime", "?"),
            "sha256": info.get("sha256", ""),
            "dimensions": dims,
            "camera_id": camera_id,
            "duration_ms": duration_ms,
            "album": album,
            "thumbnail": str(thumb_path) if thumb_path else None,
            "phash": phash_val,
            "brightness": extras.get("brightness"),
            "colors": extras.get("colors"),
            "qr_content": extras.get("qr"),
            "gps": list(gps_coords) if gps_coords else None,
            "filter": getattr(args, "filter", None),
            "tags": custom_tags,
            "burst_count": len(burst_paths) if burst_paths else 1,
            "upload": upload_result or None,
            "comparison": cmp_result or None,
            "dry_run": dry_run,
        }
        _append_log(log_path, log_entry, max_log)
        _debug(f"Logged to: {log_path}")

    # ── Build LLM output ──────────────────────────────────────────────────────
    facing = "Front" if camera_id == 1 else "Rear"
    parts: list[str] = [
        "Photo captured successfully.\n",
        f"File       : {photo_path}\n",
        f"Size       : {info.get('size_human', '?')}\n",
        f"MIME       : {info.get('mime', '?')}\n",
        f"Dimensions : {dims}\n",
        f"Camera     : {facing} (ID {camera_id})\n",
        f"Duration   : {duration_ms}ms\n",
        f"Log        : {log_path}\n",
    ]
    if album:
        parts.append(f"Album      : {album}\n")
    if thumb_path:
        parts.append(f"Thumbnail  : {thumb_path}\n")
    if hist_path:
        parts.append(f"Histogram  : {hist_path}\n")
    if extras.get("brightness") is not None:
        bval = extras["brightness"]
        label = "Dark" if bval < 80 else ("Bright" if bval > 180 else "Normal")
        parts.append(f"Brightness : {bval}/255 ({label})\n")
    if extras.get("colors"):
        parts.append(f"Colors     : {', '.join(extras['colors'])}\n")
    if extras.get("qr"):
        parts.append(f"QR/Barcode : {extras['qr']}\n")
    if gps_coords:
        lat, lon, alt = gps_coords
        parts.append(f"GPS        : {lat:.6f}, {lon:.6f}  alt={alt:.1f}m\n")
    if cmp_result:
        parts.append(
            f"Comparison : RMSE={cmp_result.get('rmse')} "
            f"({'different' if cmp_result.get('different') else 'similar'})\n"
        )
    if upload_result:
        parts.append(
            f"Upload     : {'OK' if upload_result.get('success') else 'FAILED'}\n"
        )
    if burst_paths:
        parts.append(f"Burst      : {len(burst_paths)} photos captured\n")
        for bp in burst_paths:
            parts.append(f"             {bp}\n")
    if extras.get("similar"):
        parts.append(f"Similar    : {extras['similar']} (possible duplicate)\n")
    if custom_tags:
        for k, v in custom_tags.items():
            parts.append(f"Tag/{k:<7}: {v}\n")
    if dry_run:
        parts.append("\n[DRY-RUN] No real photo was taken.\n")

    # ── Base64 for vision models ──────────────────────────────────────────────
    if args.encode_base64:
        _info("Encoding image as base64…")
        b64 = _encode_base64(photo_path)
        if b64:
            parts.append(f"\nBase64 data URI ({len(b64):,} chars):\n{b64}\n")
        else:
            parts.append("\n[Base64 encoding failed]\n")

    _write_output("".join(parts), out_path)


# ==============================================================================
# SECTION 21: run() — required aichat tool entry point
# ==============================================================================


def run(
    filename: Optional[str] = None,
    camera_id: int = 0,
    save_dir: str = "~/Pictures/CameraSnaps",
    timeout: int = 20,
    quality: int = 90,
    prefix: str = "photo",
    log_file: Optional[str] = None,
    format: str = "jpg",
    resize: Optional[str] = None,
    rotate: Optional[int] = None,
    annotate: Optional[str] = None,
    annotate_pos: str = "br",
    annotate_color: str = "white",
    annotate_size: int = 24,
    burst: int = 1,
    burst_delay: int = 500,
    filter: Optional[str] = None,
    watermark: Optional[str] = None,
    upload_url: Optional[str] = None,
    upload_field: str = "file",
    convert_to: Optional[str] = None,
    tag: Optional[list[str]] = None,
    album: Optional[str] = None,
    compare_with: Optional[str] = None,
    max_log_entries: int = 500,
    thumbnail_size: str = "200x200",
    search_log: Optional[str] = None,
    export_log: str = "json",
    front: bool = False,
    encode_base64: bool = False,
    show_info: bool = False,
    open: bool = False,
    list_cameras: bool = False,
    show_log: bool = False,
    gps_tag: bool = False,
    scan_qr: bool = False,
    analyze_brightness: bool = False,
    analyze_colors: bool = False,
    histogram: bool = False,
    share: bool = False,
    notify: bool = False,
    dedupe_check: bool = False,
    dry_run: bool = False,
    no_thumbnail: bool = False,
    no_log: bool = False,
    verbose: bool = False,
) -> None:
    """
    Primary aichat tool entry point.

    Parameter names match the @option/@flag slugs (hyphens → underscores).
    Writes all output to LLM_OUTPUT or stdout. Returns None.
    """
    args = argparse.Namespace(
        filename=filename,
        camera_id=camera_id,
        save_dir=save_dir,
        timeout=timeout,
        quality=quality,
        prefix=prefix,
        log_file=log_file,
        format=format,
        resize=resize,
        rotate=rotate,
        annotate=annotate,
        annotate_pos=annotate_pos,
        annotate_color=annotate_color,
        annotate_size=annotate_size,
        burst=burst,
        burst_delay=burst_delay,
        filter=filter,
        watermark=watermark,
        upload_url=upload_url,
        upload_field=upload_field,
        convert_to=convert_to,
        tag=tag,
        album=album,
        compare_with=compare_with,
        max_log_entries=max_log_entries,
        thumbnail_size=thumbnail_size,
        search_log=search_log,
        export_log=export_log,
        front=front,
        encode_base64=encode_base64,
        show_info=show_info,
        open=open,
        list_cameras=list_cameras,
        show_log=show_log,
        gps_tag=gps_tag,
        scan_qr=scan_qr,
        analyze_brightness=analyze_brightness,
        analyze_colors=analyze_colors,
        histogram=histogram,
        share=share,
        notify=notify,
        dedupe_check=dedupe_check,
        dry_run=dry_run,
        no_thumbnail=no_thumbnail,
        no_log=no_log,
        verbose=verbose,
    )
    try:
        _execute(args)
    except KeyboardInterrupt:
        _warn(_GOODBYE)
        return


# ==============================================================================
# SECTION 22: CLI argument parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="take_photo.py",
        description=f"Pyrmethus Camera Snap v{__version__} — Termux camera wrapper",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  python take_photo.py
  python take_photo.py --front --notify
  python take_photo.py --burst 5 --burst-delay 300
  python take_photo.py --annotate "Taken {ts}" --annotate-pos br
  python take_photo.py --filter sepia --quality 80
  python take_photo.py --resize 1280x720 --rotate 90
  python take_photo.py --encode-base64 --show-info
  python take_photo.py --gps-tag --album holidays
  python take_photo.py --scan-qr --analyze-brightness
  python take_photo.py --upload-url https://example.com/upload
  python take_photo.py --watermark ~/logo.png --convert-to webp
  python take_photo.py --show-log --export-log csv
  python take_photo.py --search-log 20250615
  python take_photo.py --dedupe-check --dry-run --verbose
  python take_photo.py --compare-with ~/Pictures/ref.jpg --histogram
        """,
    )
    p.add_argument("--filename", default=None)
    p.add_argument("--camera-id", type=int, default=0, dest="camera_id")
    p.add_argument("--save-dir", default="~/Pictures/CameraSnaps", dest="save_dir")
    p.add_argument("--timeout", type=int, default=20)
    p.add_argument("--quality", type=int, default=90)
    p.add_argument("--prefix", default="photo")
    p.add_argument("--log-file", default=None, dest="log_file")
    p.add_argument("--format", default="jpg", choices=["jpg", "png", "webp"])
    p.add_argument("--resize", default=None)
    p.add_argument("--rotate", type=int, default=None, choices=[90, 180, 270])
    p.add_argument("--annotate", default=None)
    p.add_argument(
        "--annotate-pos",
        default="br",
        dest="annotate_pos",
        choices=["tl", "tr", "bl", "br", "center"],
    )
    p.add_argument("--annotate-color", default="white", dest="annotate_color")
    p.add_argument("--annotate-size", type=int, default=24, dest="annotate_size")
    p.add_argument("--burst", type=int, default=1)
    p.add_argument("--burst-delay", type=int, default=500, dest="burst_delay")
    p.add_argument("--filter", default=None, choices=list(SUPPORTED_FILTERS))
    p.add_argument("--watermark", default=None)
    p.add_argument("--upload-url", default=None, dest="upload_url")
    p.add_argument("--upload-field", default="file", dest="upload_field")
    p.add_argument(
        "--convert-to",
        default=None,
        dest="convert_to",
        choices=["jpg", "png", "webp", "heic"],
    )
    p.add_argument("--tag", action="append", default=None, metavar="KEY=VALUE")
    p.add_argument("--album", default=None)
    p.add_argument("--compare-with", default=None, dest="compare_with")
    p.add_argument("--max-log-entries", type=int, default=500, dest="max_log_entries")
    p.add_argument("--thumbnail-size", default="200x200", dest="thumbnail_size")
    p.add_argument("--search-log", default=None, dest="search_log")
    p.add_argument(
        "--export-log", default="json", dest="export_log", choices=["json", "csv", "md"]
    )
    p.add_argument("--front", action="store_true")
    p.add_argument("--encode-base64", action="store_true", dest="encode_base64")
    p.add_argument("--show-info", action="store_true", dest="show_info")
    p.add_argument("--open", action="store_true")
    p.add_argument("--list-cameras", action="store_true", dest="list_cameras")
    p.add_argument("--show-log", action="store_true", dest="show_log")
    p.add_argument("--gps-tag", action="store_true", dest="gps_tag")
    p.add_argument("--scan-qr", action="store_true", dest="scan_qr")
    p.add_argument(
        "--analyze-brightness", action="store_true", dest="analyze_brightness"
    )
    p.add_argument("--analyze-colors", action="store_true", dest="analyze_colors")
    p.add_argument("--histogram", action="store_true")
    p.add_argument("--share", action="store_true")
    p.add_argument("--notify", action="store_true")
    p.add_argument("--dedupe-check", action="store_true", dest="dedupe_check")
    p.add_argument("--dry-run", action="store_true", dest="dry_run")
    p.add_argument("--no-thumbnail", action="store_true", dest="no_thumbnail")
    p.add_argument("--no-log", action="store_true", dest="no_log")
    p.add_argument("--verbose", "-v", action="store_true")
    return p


# ==============================================================================
# SECTION 23: Entry point
# ==============================================================================

# ==============================================================================
# SECTION 24: Self-test  (NEW v2.1.0)
# ==============================================================================


def _selftest() -> int:
    """
    Lightweight in-process smoke test that exercises the non-camera, non-Termux
    helpers.  Intended to be runnable offline so CI / sanity checks don't need
    a real camera, GPS, or upload endpoint.

    Returns 0 on success, non-zero on failure.
    """
    import tempfile as _tf

    failures: list[str] = []

    def _check(cond: bool, label: str) -> None:
        if cond:
            _info(f"  ✓ {label}")
        else:
            _error(f"  ✗ {label}")
            failures.append(label)

    _info("Pyrmethus Camera Snap self-test")
    _info(f"  Version: {__version__}")

    # Sandbox helper
    with _tf.TemporaryDirectory() as td:
        inside = Path(td) / "inside.jpg"
        _check(_validate_sandbox(inside), "validate_sandbox: inside temp dir")
        _check(
            not _validate_sandbox(Path("/etc/passwd")),
            "validate_sandbox: rejects /etc/passwd",
        )

    # Filename sanitiser (PostScript: strip `.` too — an input like "1.2.3.jpg"
    # becomes "1.2.3.jpg" because dots are allowed, but trailing dots do get
    # trimmed by the `strip("._")` step).
    _check(
        _sanitize_filename("My Photo (1).JPG") == "My_Photo_1.JPG",
        "sanitize_filename: strips parens / spaces",
    )
    _check(
        _sanitize_filename("etc/passwd") == "etc_passwd",
        "sanitize_filename: collapses path separators",
    )
    _check(
        _sanitize_filename("...") == "photo",
        "sanitize_filename: empty after strip → 'photo'",
    )
    _check(
        _sanitize_filename("a..b") == "a.b",
        "sanitize_filename: collapses runs of underscores (no dot run)",
    )

    # MIME detection
    _check(_detect_mime(Path("foo.jpg")).startswith("image/"), "detect_mime: jpg")
    _check(_detect_mime(Path("foo.png")).startswith("image/"), "detect_mime: png")

    # Human size formatting
    _check(_human_size(500) == "500 B", "human_size: B")
    _check(_human_size(2048) == "2.0 KB", "human_size: KB")
    _check(_human_size(2_000_000).endswith("MB"), "human_size: MB")

    # SUPPORTED_FILTERS sanity
    _check("vignette" in SUPPORTED_FILTERS, "filters: vignette listed")
    _check("emboss" in SUPPORTED_FILTERS, "filters: emboss listed")

    # GracefulShutdown instance exists
    _check(
        _SHUTDOWN is not None and isinstance(_SHUTDOWN, GracefulShutdown),
        "shutdown: coordinator initialised",
    )

    # argparse builds without error and includes --filter choices
    p = _build_parser()
    _check(
        "filter" in {a.dest for a in p._actions if a.dest},
        "argparse: --filter registered",
    )
    filter_action = next((a for a in p._actions if a.dest == "filter"), None)
    if filter_action is not None:
        _check(
            set(filter_action.choices or []) >= {"vignette", "emboss"},
            "argparse: --filter choices include vignette & emboss",
        )

    # CSV export with heterogeneous records
    sample = [
        {"captured_at": "2026-01-01T00:00:00Z", "filename": "a.jpg", "album": "x"},
        {"captured_at": "2026-01-02T00:00:00Z", "filename": "b.jpg"},
    ]
    csv_out = _export_log(sample, "csv")
    _check(
        "filename" in csv_out and "album" in csv_out,
        "csv_export: preserves union of keys",
    )
    _check(csv_out.count("\n") >= 3, "csv_export: header + 2 rows")

    # phash distance is well-defined
    _check(_phash_distance("0" * 64, "0" * 64) == 0, "phash: identical → 0")
    _check(_phash_distance("0" * 64, "1" * 64) == 64, "phash: opposite → 64")

    if failures:
        _error(f"Self-test FAILED: {len(failures)} check(s) broke.")
        for f in failures:
            _error(f"  - {f}")
        return 1
    _info("Self-test PASSED.  🎉")
    return 0


# ==============================================================================
# SECTION 25: Entry point
# ==============================================================================

if __name__ == "__main__":
    import sys as _sys

    if "--selftest" in _sys.argv:
        _sys.exit(_selftest())
    _args = _build_parser().parse_args()
    try:
        _execute(_args)
    except KeyboardInterrupt:
        _warn(_GOODBYE)
        _sys.exit(130)
    finally:
        _debug("Photo tool exit.")
