#!/usr/bin/env python3
# ==============================================================================
# lux_download.py — Pyrmethus AIChat Tool v1.2.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Download video or extract audio tracks using lux CLI with support for quality selection, cookies, playlists, subtitles, and aria2 acceleration.
#
# @option --url! <URL>                   The video or playlist URL to download (required)
# @option --output-path <PATH>           Destination directory for downloaded files
# @option --stream <STREAM_ID>           Select specific video stream/quality format (e.g., 1080p, 720p)
# @option --cookie <PATH_OR_TEXT>        Path to cookie file or raw cookie string for authenticated downloads
# @option --threads <NUM>                Number of download threads / concurrency limit
# @flag   --info                         Output video metadata and formats without downloading
# @flag   --audio-only                   Download only the best quality audio track
# @flag   --playlist                     Download full playlist if URL points to a playlist
# @flag   --caption                      Download video captions / subtitles if available
# @flag   --use-aria2                    Use aria2c downloader engine for faster multi-connection downloads
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from pathlib import Path
from typing import Any, Optional

__version__ = "1.2.0"

# ==============================================================================
# SECTION 1: Color Palette & Formatting Helpers
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

# Regex to strip ANSI escape codes
_ANSI_RE = re.compile(r"\033\[[0-9;]*[mGKHF]")


def _strip_ansi(text: str) -> str:
    """Remove all ANSI escape sequences from text."""
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    """Return True if stdout is attached to an interactive terminal."""
    return sys.stdout.isatty()


def _cprint(text: str, file: Any = None, no_color: bool = False) -> None:
    """Print pre-formatted ANSI text, stripping colors if stdout is not a TTY or --no-color is set."""
    target = file or sys.stdout
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly, colorized box UI for terminal users.
    Only executes if running in an interactive TTY.
    """
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [LUX DOWNLOADER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}URL:{RESET}          {data.get('url', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Output Path:{RESET}  {data.get('output_path') or 'Default (Current Directory)'}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Stream/Format:{RESET}{data.get('stream') or 'Best / Auto'}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Mode Flags:{RESET}   "
        f"Info Mode: {NEON_YELLOW}{data.get('info_mode', False)}{RESET} | "
        f"Audio Only: {NEON_YELLOW}{data.get('audio_only', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Features:{RESET}     "
        f"Playlist: {NEON_YELLOW}{data.get('playlist', False)}{RESET} | "
        f"Captions: {NEON_YELLOW}{data.get('caption', False)}{RESET} | "
        f"aria2c: {NEON_YELLOW}{data.get('use_aria2', False)}{RESET}"
    )

    if data.get("threads"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Threads:{RESET}      {NEON_YELLOW}{data.get('threads')}{RESET}"
        )
    if data.get("cookie_used"):
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cookie Status:{RESET}{NEON_GREEN}Active{RESET}"
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    stdout = data.get("stdout")
    if stdout:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Output Stream:{RESET}")
        lines = stdout.strip().splitlines()
        for line in lines[:15]:  # Cap output display
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}›{RESET} {line}")
        if len(lines) > 15:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(lines) - 15} more lines{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


def execute_tool(
    url: str,
    output_path: Optional[str] = None,
    stream: Optional[str] = None,
    cookie: Optional[str] = None,
    threads: Optional[int] = None,
    info: bool = False,
    audio_only: bool = False,
    playlist: bool = False,
    caption: bool = False,
    use_aria2: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic shared by run() and CLI parser.
    """
    start_time = time.perf_counter()

    # 1. Resolve lux executable
    lux_bin = shutil.which("lux")
    if not lux_bin:
        return {
            "success": False,
            "error": "lux video downloader binary is not installed or not found in system PATH.",
            "url": url,
            "exit_code": 1,
            "duration_ms": 0.0,
        }

    # 2. Build lux command line parameters
    cmd = [lux_bin]
    resolved_out_path: Optional[str] = None

    if output_path:
        out_dir = Path(output_path).expanduser().resolve()
        try:
            out_dir.mkdir(parents=True, exist_ok=True)
        except OSError as err:
            return {
                "success": False,
                "error": f"Failed to create output directory: {err}",
                "url": url,
                "exit_code": 1,
                "duration_ms": round((time.perf_counter() - start_time) * 1000, 2),
            }
        resolved_out_path = str(out_dir)
        cmd.extend(["-o", resolved_out_path])

    if stream:
        cmd.extend(["-s", stream])

    cookie_used = False
    if cookie:
        cookie_used = True
        cookie_path = Path(cookie).expanduser().resolve()
        if cookie_path.is_file():
            cmd.extend(["-c", str(cookie_path)])
        else:
            cmd.extend(["-c", cookie])

    if threads is not None and threads > 0:
        cmd.extend(["-n", str(threads)])

    if info:
        cmd.append("-i")

    if audio_only:
        cmd.append("-ao")

    if playlist:
        cmd.append("-p")

    if caption:
        cmd.append("--caption")

    aria2_active = False
    if use_aria2:
        if shutil.which("aria2c"):
            cmd.append("-a")
            aria2_active = True
        elif verbose:
            sys.stderr.write(
                "[WARNING] aria2c requested but not found in PATH. Defaulting to lux downloader.\n"
            )

    cmd.append(url)

    if verbose:
        sys.stderr.write(f"[DEBUG] Executing command: {' '.join(cmd)}\n")

    # 3. Process execution
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True)
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        success = proc.returncode == 0

        res: dict[str, Any] = {
            "success": success,
            "url": url,
            "output_path": resolved_out_path,
            "stream": stream,
            "cookie_used": cookie_used,
            "threads": threads,
            "info_mode": info,
            "audio_only": audio_only,
            "playlist": playlist,
            "caption": caption,
            "use_aria2": aria2_active,
            "stdout": proc.stdout.strip(),
            "stderr": proc.stderr.strip(),
            "exit_code": proc.returncode,
            "duration_ms": duration_ms,
        }

        if not success:
            res["error"] = (
                proc.stderr.strip() or f"lux process exited with code {proc.returncode}"
            )

        return res

    except Exception as exc:
        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Failed to execute lux process: {exc}",
            "url": url,
            "exit_code": 1,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")

    # Ensure JSON for LLM is clean (no ANSI color sequences)
    json_payload = json.dumps(data, indent=2, ensure_ascii=False) + "\n"

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
# SECTION 4: Function Entry Point for AIChat
# ==============================================================================


def run(
    url: str,
    output_path: Optional[str] = None,
    stream: Optional[str] = None,
    cookie: Optional[str] = None,
    threads: Optional[int] = None,
    info: bool = False,
    audio_only: bool = False,
    playlist: bool = False,
    caption: bool = False,
    use_aria2: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.

    Parameter names match option/flag slugs (converting hyphens to underscores).
    """
    result = execute_tool(
        url=url,
        output_path=output_path,
        stream=stream,
        cookie=cookie,
        threads=threads,
        info=info,
        audio_only=audio_only,
        playlist=playlist,
        caption=caption,
        use_aria2=use_aria2,
        no_color=no_color,
        verbose=verbose,
    )

    # 1. Render interactive colorized UI for terminal users
    print_human_readable_ui(result, no_color=no_color)

    # 2. Write structured JSON to LLM_OUTPUT
    write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="lux_download.py",
        description=f"AIChat Lux Downloader Tool v{__version__}",
    )
    parser.add_argument(
        "--url",
        "-u",
        required=True,
        metavar="URL",
        help="The video or playlist URL to download (required)",
    )
    parser.add_argument(
        "--output-path",
        "-o",
        dest="output_path",
        metavar="PATH",
        help="Destination directory path for downloaded files",
    )
    parser.add_argument(
        "--stream",
        "-s",
        metavar="STREAM_ID",
        help="Select specific video stream/quality format (e.g., 1080p, 720p)",
    )
    parser.add_argument(
        "--cookie",
        "-c",
        metavar="PATH_OR_TEXT",
        help="Path to cookie file or raw cookie string for authenticated downloads",
    )
    parser.add_argument(
        "--threads",
        "-n",
        type=int,
        default=None,
        metavar="NUM",
        help="Number of download threads / concurrency limit",
    )
    parser.add_argument(
        "--info",
        "-i",
        action="store_true",
        default=False,
        help="Output video metadata and formats without downloading",
    )
    parser.add_argument(
        "--audio-only",
        action="store_true",
        default=False,
        dest="audio_only",
        help="Download only the best quality audio track",
    )
    parser.add_argument(
        "--playlist",
        "-p",
        action="store_true",
        default=False,
        help="Download full playlist if URL points to a playlist",
    )
    parser.add_argument(
        "--caption",
        action="store_true",
        default=False,
        help="Download video captions / subtitles if available",
    )
    parser.add_argument(
        "--use-aria2",
        "-A",
        action="store_true",
        default=False,
        dest="use_aria2",
        help="Use aria2c downloader engine for faster multi-connection downloads",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        default=False,
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose",
        "-v",
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        url=args.url,
        output_path=args.output_path,
        stream=args.stream,
        cookie=args.cookie,
        threads=args.threads,
        info=args.info,
        audio_only=args.audio_only,
        playlist=args.playlist,
        caption=args.caption,
        use_aria2=args.use_aria2,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    # Output rendering
    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
