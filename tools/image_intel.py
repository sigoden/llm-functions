#!/usr/bin/env python3
# ==============================================================================
# img_intel_tool.py — Pyrmethus AIChat Tool: Image Processing & Vision Prep v2.7.0
# argc/aichat compatible · Human-Readable Box UI · EXIF Stripping & Downsampling
#
# @describe Inspects image EXIF metadata, resizes/downsamples images for LLM vision tokens, and strips GPS tags.
#
# @option --target! <PATH>                Target image path (required)
# @option --resize <DIM>                  Max bounding dimensions WxH (e.g. 1024x1024)
# @option --output <PATH>                 Destination file path for modified image
# @flag   --strip-exif                    Remove all EXIF metadata and GPS location tags
# @flag   --metadata-only                 Only output metadata without modifying the image
# @flag   --no-color                      Disable ANSI color output
# @flag   --verbose                       Enable detailed debug logging
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, Optional

try:
    from PIL import Image, ImageOps
    from PIL.ExifTags import TAGS
except ImportError:
    print(
        "\033[31mError: Missing dependency 'Pillow'. Please run: pip install Pillow\033[0m",
        file=sys.stderr,
    )
    sys.exit(127)

__version__ = "2.7.0"

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, datetime):
            return obj.isoformat()
        return super().default(obj)


NEON_CYAN = "\033[38;5;51m"
NEON_GREEN = "\033[38;5;46m"
NEON_RED = "\033[38;5;196m"
NEON_YELLOW = "\033[38;5;226m"
NEON_PURPLE = "\033[38;5;129m"
NEON_PINK = "\033[38;5;198m"
RESET = "\033[0m"
BOLD = "\033[1m"
DIM = "\033[2m"

_ANSI_RE = re.compile(r"\033\[[0-9;]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _human_bytes(num: int) -> str:
    for unit in ["B", "KB", "MB", "GB"]:
        if abs(num) < 1024.0:
            return f"{num:.1f}{unit}"
        num /= 1024.0
    return f"{num:.1f}TB"


def _extract_exif_dict(img: Image.Image) -> Dict[str, Any]:
    exif_data: Dict[str, Any] = {}
    try:
        raw_exif = img._getexif()
        if raw_exif:
            for tag, val in raw_exif.items():
                tag_name = TAGS.get(tag, str(tag))
                if isinstance(val, (str, int, float)):
                    exif_data[tag_name] = val
                elif isinstance(val, bytes):
                    exif_data[tag_name] = val.decode("utf-8", errors="replace")[:50]
    except Exception:
        pass
    return exif_data


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [IMAGE INTEL & VISION OPTIMIZER v{__version__}]{RESET} "
        f"{status_color}{BOLD}{status_symbol} {'SUCCESS' if success else 'FAILED'}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}      {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Format:{RESET}      {NEON_YELLOW}{data.get('format', 'N/A')}{RESET} ({data.get('mode')})"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Dimensions:{RESET}  {NEON_GREEN}{data.get('width')}x{data.get('height')}{RESET} px"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}File Size:{RESET}   {data.get('file_size_fmt')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if data.get("saved_to"):
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_GREEN}Saved Output:{RESET} {data['saved_to']}"
        )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


def execute_tool(
    target: str,
    resize: Optional[str] = None,
    output: Optional[str] = None,
    strip_exif: bool = False,
    metadata_only: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target image file does not exist: {target}",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    try:
        with Image.open(target_path) as img:
            img_format = img.format or "PNG"
            img_mode = img.mode
            orig_w, orig_h = img.size
            exif_meta = _extract_exif_dict(img)

            if metadata_only:
                return {
                    "success": True,
                    "target": str(target_path),
                    "format": img_format,
                    "mode": img_mode,
                    "width": orig_w,
                    "height": orig_h,
                    "file_size_bytes": target_path.stat().st_size,
                    "file_size_fmt": _human_bytes(target_path.stat().st_size),
                    "exif_metadata": exif_meta,
                    "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                    "exit_code": EXIT_SUCCESS,
                }

            # Process Image Modifications
            modified = False
            proc_img = img.copy()

            if resize:
                try:
                    w_s, h_s = resize.lower().split("x", 1)
                    max_w, max_h = int(w_s), int(h_s)
                    proc_img.thumbnail((max_w, max_h), Image.Resampling.LANCZOS)
                    modified = True
                except Exception as exc:
                    if verbose:
                        _cprint(f"{DIM}// Resize error: {exc}{RESET}")

            out_file = Path(output).expanduser().resolve() if output else None
            saved_path_str: Optional[str] = None

            if modified or strip_exif or out_file:
                target_out = out_file or target_path
                target_out.parent.mkdir(parents=True, exist_ok=True)

                save_kwargs: Dict[str, Any] = {}
                if not strip_exif and "exif" in img.info:
                    save_kwargs["exif"] = img.info["exif"]

                if proc_img.mode in ("RGBA", "P") and img_format.upper() in (
                    "JPEG",
                    "JPG",
                ):
                    proc_img = proc_img.convert("RGB")

                proc_img.save(target_out, format=img_format, **save_kwargs)
                saved_path_str = str(target_out)

            new_w, new_h = proc_img.size

            return {
                "success": True,
                "target": str(target_path),
                "format": img_format,
                "mode": img_mode,
                "width": new_w,
                "height": new_h,
                "original_dimensions": f"{orig_w}x{orig_h}",
                "file_size_fmt": _human_bytes(
                    Path(saved_path_str or target_path).stat().st_size
                ),
                "exif_stripped": strip_exif,
                "saved_to": saved_path_str,
                "exif_metadata": {} if strip_exif else exif_meta,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
                "exit_code": EXIT_SUCCESS,
            }

    except Exception as exc:
        return {
            "success": False,
            "error": f"Image processing failed: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
        }


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )
    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)


def run(
    target: str,
    resize: Optional[str] = None,
    output: Optional[str] = None,
    strip_exif: bool = False,
    metadata_only: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    res = execute_tool(
        target=target,
        resize=resize,
        output=output,
        strip_exif=strip_exif,
        metadata_only=metadata_only,
        no_color=no_color,
        verbose=verbose,
    )
    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Pyrmethus Image Intelligence Tool")
    parser.add_argument("--target", "-t", required=True, dest="target")
    parser.add_argument("--resize", dest="resize")
    parser.add_argument("--output", "-o", dest="output")
    parser.add_argument("--strip-exif", action="store_true", dest="strip_exif")
    parser.add_argument("--metadata-only", action="store_true", dest="metadata_only")
    parser.add_argument("--no-color", action="store_true", dest="no_color")
    parser.add_argument("--verbose", "-v", action="store_true", dest="verbose")

    args = parser.parse_args()
    res = execute_tool(
        target=args.target,
        resize=args.resize,
        output=args.output,
        strip_exif=args.strip_exif,
        metadata_only=args.metadata_only,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
