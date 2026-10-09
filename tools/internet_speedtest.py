#!/usr/bin/env python3
# ==============================================================================
# net_speed_tool.py — Pyrmethus AIChat Tool: Network Speed Analyzer
# argc/aichat compatible · Human-Readable Colorized Outputs · Real-time Metrics
#
# @describe Measures internet connection performance including download, upload, and latency.
#
# @meta require-tools aichat
#
# @option --server <ID>                 Specific Speedtest.net server ID (optional)
# @option --unit <UNIT>                 Output unit: Mbps or Kbps (default: Mbps)
# @flag   --verbose                     Enable detailed debug log output
# @flag   --no-color                    Disable ANSI color output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
import time
from pathlib import Path
from typing import Any, Literal, Optional

# Attempt to import speedtest-cli
try:
    import speedtest
except ImportError:
    print(
        "\033[31mError: 'speedtest-cli' library not found. Please run: pip install speedtest-cli\033[0m"
    )
    sys.exit(127)

# ==============================================================================
# SECTION 1: Constants & Exception Models
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_TIMEOUT = 124
EXIT_INVALID_INPUT = 127


class ToolError(Exception):
    def __init__(self, message: str, exit_code: int = EXIT_ERROR):
        super().__init__(message)
        self.message = message
        self.exit_code = exit_code


class ToolJSONEncoder(json.JSONEncoder):
    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
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


def _cprint(
    text: str, file: Any = None, no_color: bool = False, end: str = "\n"
) -> None:
    target = file or sys.stderr
    if no_color:
        import re

        text = re.sub(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])", "", text)
    print(text, file=target, flush=True, end=end)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    if not sys.stderr.isatty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"

    box_w = 64
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [NETWORK SPEED TEST]{RESET} {status_color}{BOLD}{status_symbol} {status_color}{'SUCCESS' if success else 'FAILED'}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")

    if success:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Client IP:{RESET}   {data.get('client_ip')}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Server:{RESET}     {data.get('server_name')} ({data.get('server_location')})"
        )
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Metrics:{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}↓ Download:{RESET} {NEON_YELLOW}{data.get('download')}{data.get('unit')}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}↑ Upload:{RESET}   {NEON_YELLOW}{data.get('upload')}{data.get('unit')}{RESET}"
        )
        _cprint(
            f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}○ Latency:{RESET}  {NEON_YELLOW}{data.get('ping')} ms{RESET}"
        )
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms')}ms{RESET}"
        )
    else:
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data.get('error')}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_execution_context() -> dict[str, Any]:
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "net_speed_tool"),
        "cwd": os.getcwd(),
        "os": sys.platform,
    }


# ==============================================================================
# SECTION 4: Core Logic Implementation
# ==============================================================================


def execute_speed_test(
    server_id: Optional[str] = None,
    unit: str = "Mbps",
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG)
        logging.debug("Initializing Speedtest engine...")

    try:
        st = speedtest.Speedtest()

        if verbose:
            _cprint(
                f"{NEON_CYAN}Searching for optimal server...{RESET}",
                end="",
                no_color=False,
            )

        if server_id:
            st.get_servers([server_id])
        else:
            st.get_best_server()

        if verbose:
            _cprint(f" Done. {RESET}", end="", no_color=False)

        if verbose:
            _cprint(
                f"{NEON_CYAN}Testing download speed...{RESET}", end="", no_color=False
            )
        down_bps = st.download()
        if verbose:
            _cprint(f" Done. {RESET}", end="", no_color=False)

        if verbose:
            _cprint(
                f"{NEON_CYAN}Testing upload speed...{RESET}", end="", no_color=False
            )
        up_bps = st.upload()
        if verbose:
            _cprint(f" Done. {RESET}", end="", no_color=False)

        # Unit Conversion
        # speedtest-cli returns bits per second.
        # Mbps = bps / 1,000,000
        # Kbps = bps / 1,000
        divisor = 1_000_000 if unit == "Mbps" else 1_000

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        return {
            "success": True,
            "client_ip": st.results.client,
            "server_name": st.results.server["sponsor"],
            "server_location": f"{st.results.server['name']}, {st.results.server['country']}",
            "download": round(down_bps / divisor, 2),
            "upload": round(up_bps / divisor, 2),
            "ping": st.results.ping,
            "unit": unit,
            "duration_ms": duration_ms,
            "context": get_execution_context(),
            "exit_code": EXIT_SUCCESS,
        }

    except Exception as exc:
        return {
            "success": False,
            "error": str(exc),
            "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            "exit_code": EXIT_ERROR,
        }


# ==============================================================================
# SECTION 5: Output Routing
# ==============================================================================


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
            with open(out_path, "a", encoding="utf-8") as fp:
                fp.write(json_payload)
        except OSError:
            sys.stdout.write(json_payload)


# ==============================================================================
# SECTION 6: Entry Points
# ==============================================================================


def run(
    server: Optional[str] = None,
    unit: Literal["Mbps", "Kbps"] = "Mbps",
    verbose: bool = False,
    no_color: bool = False,
) -> None:
    """
    Perform an internet speed test.

    Args:
        server: Optional Speedtest.net server ID.
        unit: Result unit (Mbps or Kbps).
        verbose: Show progress logs.
        no_color: Disable ANSI colors.
    """
    result = execute_speed_test(server_id=server, unit=unit, verbose=verbose)
    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="AIChat Network Speed Tool")
    parser.add_argument("--server", help="Server ID")
    parser.add_argument(
        "--unit", choices=["Mbps", "Kbps"], default="Mbps", help="Output unit"
    )
    parser.add_argument("--verbose", action="store_true", help="Enable debug logs")
    parser.add_argument("--no-color", action="store_true", help="Disable color")

    args = parser.parse_args()
    run(
        server=args.server, unit=args.unit, verbose=args.verbose, no_color=args.no_color
    )
