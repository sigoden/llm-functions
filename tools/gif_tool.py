#!/usr/bin/env python3
# ==============================================================================
# gifsicle.py — Pyrmethus AIChat Tool Wrapper for Gifsicle v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Manipulate, optimize, merge, explode, and inspect GIF images using Gifsicle
#
# @meta require-tools gifsicle
#
# @option --input-files!               Input GIF file paths or frames (space or comma-separated)
# @option --output-file -o              Write output GIF to specified FILE
# @option --mode -m                     Execution mode: merge/batch/explode/info (default: merge)
# @option --optimize -O                 Optimization level (1, 2, or 3)
# @option --lossy                       Lossiness level to shrink size (e.g. 20, 80, 200)
# @option --delay -d                    Set frame delay in 1/100ths of a second
# @option --loopcount -l                Set loop extension (0 or forever, or N)
# @option --colors -k                   Reduce number of colors (1-256)
# @option --resize                      Resize output GIF to WxH (e.g. 320x240)
# @option --resize-width                Resize output GIF to width W (proportional height)
# @option --resize-height               Resize output GIF to height H (proportional width)
# @option --resize-fit                  Shrink output GIF to fit WxH, preserving aspect ratio
# @option --scale                       Scale output GIF by XFACTOR[xYFACTOR]
# @option --crop                        Crop image (e.g. X,Y+WxH or X,Y-X2,Y2)
# @option --rotate                      Rotate GIF (rotate-90, rotate-180, rotate-270)
# @option --transparent -t              Make specified color transparent (e.g. #FFFFFF)
# @option --disposal -D                 Set frame disposal method (none, asis, background, previous)
# @flag   --crop-transparency           Crop transparent borders off the image
# @flag   --flip-horizontal             Flip image horizontally
# @flag   --flip-vertical               Flip image vertically
# @flag   --unoptimize -U               Unoptimize input GIFs
# @flag   --careful                     Write larger GIFs that avoid bugs in other programs
# @flag   --quiet -w                    Suppress warning messages
# @flag   --verbose -V                  Enable verbose progress reporting
# @flag   --use-cache                   Enable result caching for identical operations
# @flag   --no-color                    Disable ANSI color output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM JSON integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import os
import pickle
import re
import shutil
import signal
import subprocess
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Optional, Union

__version__ = "2.2.0"
__all__ = [
    "ToolCache",
    "ToolError",
    "__version__",
    "execute_tool",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
    "run",
]

# ==============================================================================
# SECTION 1: Exit Codes & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_FILE_NOT_FOUND = 2
EXIT_TIMEOUT = 124
EXIT_PERMISSION_DENIED = 126
EXIT_INVALID_INPUT = 127
EXIT_INTERRUPTED = 130


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


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
        if isinstance(obj, timedelta):
            return obj.total_seconds()
        if isinstance(obj, bytes):
            return obj.decode("utf-8", errors="replace")
        if isinstance(obj, (set, frozenset)):
            return list(obj)
        return super().default(obj)


# ==============================================================================
# SECTION 2: Terminal Color Palette & UI Helpers
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
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stderr is attached to an interactive, non-dumb terminal."""
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in (
        "dumb",
        "",
    )


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    """Print pre-formatted ANSI text, stripping colors if stream is not a TTY or --no-color is set."""
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


def _format_bytes(size: Optional[int]) -> str:
    """Convert byte size to human-readable string format."""
    if size is None:
        return "N/A"
    size_float = float(size)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if abs(size_float) < 1024.0:
            return f"{size_float:.2f} {unit}"
        size_float /= 1024.0
    return f"{size_float:.2f} PB"


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly colorized UI box on stderr for terminal interactive users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [GIFSICLE TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode:{RESET}        {data.get('mode', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Inputs:{RESET}      {NEON_YELLOW}{len(data.get('input_files', []))} item(s){RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Output File:{RESET} {NEON_YELLOW}{data.get('output_file') or 'Stdout/In-Place'}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Output Size:{RESET} {NEON_GREEN}{_format_bytes(data.get('file_size_bytes'))}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}      {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}    {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}       {data['error']}")

    info_text = data.get("info_output")
    if info_text:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}GIF Information Preview:{RESET}")
        lines = info_text.strip().split("\n")
        for line in lines[:8]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{line}{RESET}")
        if len(lines) > 8:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(lines) - 8} more lines{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables (e.g., __cwd__, __os__)."""
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "gifsicle"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def _parse_input_files(inputs: Union[str, list[str]]) -> list[str]:
    """Parse string or list inputs into a clean list of file path strings."""
    if isinstance(inputs, list):
        parsed = []
        for item in inputs:
            parsed.extend(_parse_input_files(item))
        return parsed

    if isinstance(inputs, str):
        cleaned = inputs.strip()
        if not cleaned:
            return []
        if "," in cleaned and not os.path.exists(cleaned):
            return [f.strip() for f in cleaned.split(",") if f.strip()]
        if " " in cleaned and not os.path.exists(cleaned):
            return [f.strip() for f in cleaned.split() if f.strip()]
        return [cleaned]

    return []


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility with TTL support for expensive operations."""

    def __init__(self, cache_dir: Optional[Path] = None) -> None:
        if cache_dir:
            self.cache_dir = cache_dir
        elif "LLM_TOOL_CACHE_DIR" in os.environ:
            self.cache_dir = Path(os.environ["LLM_TOOL_CACHE_DIR"])
        else:
            self.cache_dir = Path.home() / ".cache" / "aichat_tools"

        try:
            self.cache_dir.mkdir(parents=True, exist_ok=True)
        except OSError:
            pass

    def _make_key(self, key_data: str) -> str:
        return hashlib.sha256(key_data.encode("utf-8")).hexdigest()

    def get(self, key_data: str, ttl_seconds: int = 3600) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            mtime = cache_file.stat().st_mtime
            if time.time() - mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_data: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


class GracefulShutdown:
    """Signal handler for graceful cancellation of subprocess operations."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        """Restore previous signal handlers."""
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================


def execute_tool(
    input_files: Union[str, list[str]],
    output_file: Optional[str] = None,
    mode: Optional[str] = "merge",
    optimize: Optional[Union[int, str]] = None,
    lossy: Optional[Union[int, str]] = None,
    delay: Optional[Union[int, str]] = None,
    loopcount: Optional[Union[int, str]] = None,
    colors: Optional[Union[int, str]] = None,
    resize: Optional[str] = None,
    resize_width: Optional[Union[int, str]] = None,
    resize_height: Optional[Union[int, str]] = None,
    resize_fit: Optional[str] = None,
    scale: Optional[str] = None,
    crop: Optional[str] = None,
    rotate: Optional[str] = None,
    transparent: Optional[str] = None,
    disposal: Optional[str] = None,
    crop_transparency: bool = False,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    unoptimize: bool = False,
    careful: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
) -> dict[str, Any]:
    """Execute Gifsicle operation and return structured execution dictionary."""
    start_time = time.monotonic()

    # Verify gifsicle installation
    gifsicle_bin = shutil.which("gifsicle")
    if not gifsicle_bin:
        return {
            "success": False,
            "error": "Gifsicle binary ('gifsicle') was not found in PATH. Please install gifsicle.",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    file_list = _parse_input_files(input_files)
    if not file_list:
        return {
            "success": False,
            "error": "No valid input files provided to gifsicle.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    exec_mode = (mode or "merge").lower()

    # Create Cache Key
    cache = ToolCache()
    cache_key = f"{exec_mode}:{output_file}:{file_list}:{optimize}:{lossy}:{delay}:{loopcount}:{colors}:{resize}:{crop}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    cmd = [gifsicle_bin]

    # Handle Mode Options
    if exec_mode == "merge":
        cmd.append("--merge")
    elif exec_mode == "batch":
        cmd.append("--batch")
    elif exec_mode == "explode":
        cmd.append("--explode")
    elif exec_mode == "info":
        cmd.append("--info")
    else:
        cmd.append("--merge")

    # Handle Output File
    if output_file and exec_mode not in ("batch", "info"):
        cmd.extend(["-o", output_file])

    # Handle Animation Options
    if delay is not None:
        cmd.extend(["--delay", str(delay)])
    if loopcount is not None:
        cmd.append(f"--loopcount={loopcount}")

    # Handle Whole-GIF Options
    if optimize is not None:
        cmd.append(f"--optimize={optimize}")
    if lossy is not None:
        cmd.append(f"--lossy={lossy}")
    if colors is not None:
        cmd.extend(["--colors", str(colors)])

    # Handle Resize & Scale Options
    if resize:
        cmd.extend(["--resize", str(resize)])
    if resize_width is not None:
        cmd.extend(["--resize-width", str(resize_width)])
    if resize_height is not None:
        cmd.extend(["--resize-height", str(resize_height)])
    if resize_fit:
        cmd.extend(["--resize-fit", str(resize_fit)])
    if scale:
        cmd.extend(["--scale", str(scale)])

    # Handle Image Crop/Flip/Rotate Options
    if crop:
        cmd.extend(["--crop", str(crop)])
    if crop_transparency:
        cmd.append("--crop-transparency")
    if flip_horizontal:
        cmd.append("--flip-horizontal")
    if flip_vertical:
        cmd.append("--flip-vertical")
    if rotate:
        clean_rot = str(rotate).lower().strip()
        if clean_rot in ("rotate-90", "90"):
            cmd.append("--rotate-90")
        elif clean_rot in ("rotate-180", "180"):
            cmd.append("--rotate-180")
        elif clean_rot in ("rotate-270", "270"):
            cmd.append("--rotate-270")
        else:
            cmd.append(f"--{clean_rot}")

    if transparent:
        cmd.extend(["--transparent", str(transparent)])
    if disposal:
        cmd.extend(["--disposal", str(disposal)])
    if unoptimize:
        cmd.append("--unoptimize")
    if careful:
        cmd.append("--careful")
    if quiet:
        cmd.append("--no-warnings")
    if verbose:
        cmd.append("--verbose")

    # Append input file paths / frame specifications
    cmd.extend(file_list)

    shutdown = GracefulShutdown()

    try:
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            check=False,
        )
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        if shutdown.interrupted:
            return {
                "success": False,
                "error": "Gifsicle operation was interrupted by user signal.",
                "exit_code": EXIT_INTERRUPTED,
                "duration_ms": duration_ms,
            }

        if proc.returncode != 0:
            err_msg = (
                proc.stderr.strip()
                or proc.stdout.strip()
                or f"Process exited with code {proc.returncode}"
            )
            return {
                "success": False,
                "mode": exec_mode,
                "input_files": file_list,
                "output_file": output_file,
                "error": f"Gifsicle execution failed: {err_msg}",
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
            }

        # Measure file size if output file was created or modified
        output_size: Optional[int] = None
        if output_file and Path(output_file).is_file():
            try:
                output_size = Path(output_file).stat().st_size
            except OSError:
                output_size = None

        result: dict[str, Any] = {
            "success": True,
            "mode": exec_mode,
            "input_files": file_list,
            "output_file": output_file,
            "file_size_bytes": output_size,
            "info_output": proc.stdout
            if exec_mode == "info" or not output_file
            else None,
            "cached": False,
            "context": get_execution_context(),
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except PermissionError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Permission denied executing gifsicle: {exc}",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Gifsicle tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    direct_targets = {"/dev/stdout", "/dev/fd/1", "-"}
    if out_path in direct_targets:
        sys.stdout.write(json_payload)
        sys.stdout.flush()
    else:
        try:
            Path(out_path).parent.mkdir(parents=True, exist_ok=True)
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError as err:
            sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
            sys.stdout.write(json_payload)
            sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================


def run(
    input_files: Union[str, list[str]],
    output_file: Optional[str] = None,
    mode: Optional[str] = "merge",
    optimize: Optional[Union[int, str]] = None,
    lossy: Optional[Union[int, str]] = None,
    delay: Optional[Union[int, str]] = None,
    loopcount: Optional[Union[int, str]] = None,
    colors: Optional[Union[int, str]] = None,
    resize: Optional[str] = None,
    resize_width: Optional[Union[int, str]] = None,
    resize_height: Optional[Union[int, str]] = None,
    resize_fit: Optional[str] = None,
    scale: Optional[str] = None,
    crop: Optional[str] = None,
    rotate: Optional[str] = None,
    transparent: Optional[str] = None,
    disposal: Optional[str] = None,
    crop_transparency: bool = False,
    flip_horizontal: bool = False,
    flip_vertical: bool = False,
    unoptimize: bool = False,
    careful: bool = False,
    quiet: bool = False,
    verbose: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
) -> None:
    """Manipulate, optimize, merge, explode, and inspect GIF images using Gifsicle.

    Args:
        input_files: Input GIF file paths or frame ranges (required)
        output_file: Write output to specified GIF FILE (-o)
        mode: Execution mode: merge, batch, explode, or info (default: merge)
        optimize: Optimization level (1, 2, or 3)
        lossy: Lossiness level to shrink size (e.g., 20, 80)
        delay: Set frame delay in 1/100ths of a second
        loopcount: Set loop extension (0/forever or N)
        colors: Reduce number of colors (1-256)
        resize: Resize GIF to WxH (e.g. 320x240)
        resize_width: Resize GIF to width W
        resize_height: Resize GIF to height H
        resize_fit: Shrink GIF to fit WxH preserving aspect ratio
        scale: Scale GIF by XFACTOR[xYFACTOR]
        crop: Crop image (e.g. X,Y+WxH)
        rotate: Rotate GIF (rotate-90, rotate-180, rotate-270)
        transparent: Make color transparent (e.g. #FFFFFF)
        disposal: Set frame disposal method
        crop_transparency: Crop transparent borders off image
        flip_horizontal: Flip image horizontally
        flip_vertical: Flip image vertically
        unoptimize: Unoptimize input GIFs
        careful: Write larger GIFs that avoid bugs in other programs
        quiet: Suppress warnings
        verbose: Enable detailed progress logging
        use_cache: Enable result caching
        no_color: Disable ANSI color output
    """
    res = execute_tool(
        input_files=input_files,
        output_file=output_file,
        mode=mode,
        optimize=optimize,
        lossy=lossy,
        delay=delay,
        loopcount=loopcount,
        colors=colors,
        resize=resize,
        resize_width=resize_width,
        resize_height=resize_height,
        resize_fit=resize_fit,
        scale=scale,
        crop=crop,
        rotate=rotate,
        transparent=transparent,
        disposal=disposal,
        crop_transparency=crop_transparency,
        flip_horizontal=flip_horizontal,
        flip_vertical=flip_vertical,
        unoptimize=unoptimize,
        careful=careful,
        quiet=quiet,
        verbose=verbose,
        use_cache=use_cache,
        no_color=no_color,
    )

    print_human_readable_ui(res, no_color=no_color)
    write_llm_output(res)


# ==============================================================================
# SECTION 8: CLI Argument Parser & Entry Dispatcher
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="gifsicle.py",
        description=f"AIChat Gifsicle Tool Wrapper v{__version__}",
    )
    parser.add_argument(
        "--input-files",
        "-i",
        required=True,
        nargs="+",
        help="Input GIF file paths or frame ranges (required)",
    )
    parser.add_argument("--output-file", "-o", help="Write output to FILE")
    parser.add_argument(
        "--mode",
        "-m",
        choices=["merge", "batch", "explode", "info"],
        default="merge",
        help="Execution mode (default: merge)",
    )
    parser.add_argument("--optimize", "-O", help="Optimization level (1-3)")
    parser.add_argument("--lossy", help="Lossiness level to shrink size (e.g. 20, 80)")
    parser.add_argument("--delay", "-d", help="Frame delay in 1/100ths of a second")
    parser.add_argument("--loopcount", "-l", help="Set loop extension (0 or N)")
    parser.add_argument("--colors", "-k", help="Reduce number of colors (1-256)")
    parser.add_argument("--resize", help="Resize output GIF to WxH")
    parser.add_argument("--resize-width", help="Resize output GIF to width W")
    parser.add_argument("--resize-height", help="Resize output GIF to height H")
    parser.add_argument(
        "--resize-fit", help="Shrink GIF to fit WxH preserving aspect ratio"
    )
    parser.add_argument("--scale", help="Scale output GIF by XFACTOR[xYFACTOR]")
    parser.add_argument("--crop", help="Crop image (X,Y+WxH or X,Y-X2,Y2)")
    parser.add_argument(
        "--rotate", help="Rotate image (rotate-90, rotate-180, rotate-270)"
    )
    parser.add_argument("--transparent", "-t", help="Make specified color transparent")
    parser.add_argument("--disposal", "-D", help="Set frame disposal method")
    parser.add_argument(
        "--crop-transparency", action="store_true", help="Crop transparent borders"
    )
    parser.add_argument(
        "--flip-horizontal", action="store_true", help="Flip image horizontally"
    )
    parser.add_argument(
        "--flip-vertical", action="store_true", help="Flip image vertically"
    )
    parser.add_argument(
        "--unoptimize", "-U", action="store_true", help="Unoptimize input GIFs"
    )
    parser.add_argument(
        "--careful", action="store_true", help="Avoid bugs in other GIF software"
    )
    parser.add_argument(
        "--quiet", "-w", action="store_true", help="Suppress warning messages"
    )
    parser.add_argument(
        "--verbose", "-V", action="store_true", help="Verbose progress logging"
    )
    parser.add_argument(
        "--use-cache", action="store_true", help="Enable result caching"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    return parser


if __name__ == "__main__":
    # Support for single JSON string parameter passed by AIChat tool callers
    if len(sys.argv) == 2 and (
        sys.argv[1].startswith("{") or sys.argv[1].startswith("[")
    ):
        try:
            raw_data = json.loads(sys.argv[1])
            normalized = {}
            if isinstance(raw_data, dict):
                normalized = {k.replace("-", "_"): v for k, v in raw_data.items()}

            res = execute_tool(
                input_files=normalized.get("input_files", []),
                output_file=normalized.get("output_file"),
                mode=normalized.get("mode", "merge"),
                optimize=normalized.get("optimize"),
                lossy=normalized.get("lossy"),
                delay=normalized.get("delay"),
                loopcount=normalized.get("loopcount"),
                colors=normalized.get("colors"),
                resize=normalized.get("resize"),
                resize_width=normalized.get("resize_width"),
                resize_height=normalized.get("resize_height"),
                resize_fit=normalized.get("resize_fit"),
                scale=normalized.get("scale"),
                crop=normalized.get("crop"),
                rotate=normalized.get("rotate"),
                transparent=normalized.get("transparent"),
                disposal=normalized.get("disposal"),
                crop_transparency=bool(normalized.get("crop_transparency", False)),
                flip_horizontal=bool(normalized.get("flip_horizontal", False)),
                flip_vertical=bool(normalized.get("flip_vertical", False)),
                unoptimize=bool(normalized.get("unoptimize", False)),
                careful=bool(normalized.get("careful", False)),
                quiet=bool(normalized.get("quiet", False)),
                verbose=bool(normalized.get("verbose", False)),
                use_cache=bool(normalized.get("use_cache", False)),
                no_color=bool(normalized.get("no_color", False)),
            )
        except Exception as err:
            res = {
                "success": False,
                "error": f"JSON argument parse error: {err}",
                "exit_code": EXIT_INVALID_INPUT,
                "duration_ms": 0.0,
            }
        print_human_readable_ui(res, no_color=res.get("no_color", False))
        write_llm_output(res)
        sys.exit(res.get("exit_code", EXIT_ERROR))

    # Standard CLI Parser execution
    parser = _build_parser()
    args = parser.parse_args()

    res = execute_tool(
        input_files=args.input_files,
        output_file=args.output_file,
        mode=args.mode,
        optimize=args.optimize,
        lossy=args.lossy,
        delay=args.delay,
        loopcount=args.loopcount,
        colors=args.colors,
        resize=args.resize,
        resize_width=args.resize_width,
        resize_height=args.resize_height,
        resize_fit=args.resize_fit,
        scale=args.scale,
        crop=args.crop,
        rotate=args.rotate,
        transparent=args.transparent,
        disposal=args.disposal,
        crop_transparency=args.crop_transparency,
        flip_horizontal=args.flip_horizontal,
        flip_vertical=args.flip_vertical,
        unoptimize=args.unoptimize,
        careful=args.careful,
        quiet=args.quiet,
        verbose=args.verbose,
        use_cache=args.use_cache,
        no_color=args.no_color,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
