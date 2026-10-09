#!/usr/bin/env python3
# ==============================================================================
# proxy_utils.py — Pyrmethus AIChat Proxy Utility Tool v2.2.1-ASCENDED
# argc/aichat compatible · Tor & SOCKS5 Routing · Probing & Env Sync
#
# @describe Utility tool for detecting proxy availability, probing Tor/SOCKS5 ports,
#           and setting HTTP/HTTPS/SOCKS proxy environment configurations.
#
# @meta require-tools python3
#
# @option --action <ENUM>                Action to perform: check, get, sync (default: check)
# @option --proxy-url <TEXT>             Custom proxy URL to test
# @option --timeout <NUM>                Connection test timeout in seconds (default: 3.0)
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import enum
import json
import logging
import os
import re
import socket
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Dict, Optional, Tuple
from urllib.parse import urlparse

__version__ = "2.2.1-ASCENDED"
__all__ = [
    "__version__",
    "execute_proxy_tool",
    "get_proxies",
    "get_proxy_settings",
    "get_socks_port",
    "is_proxy_available",
    "run",
    "set_proxy_environment",
]

# ==============================================================================
# SECTION 1: Exit Codes & JSON Serializer
# ==============================================================================

EXIT_SUCCESS = 0
EXIT_ERROR = 1
EXIT_INVALID_INPUT = 2


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Decimal, Path, Enum, datetime, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, enum.Enum):
            return obj.value
        if isinstance(obj, (datetime, timedelta)):
            return obj.isoformat() if isinstance(obj, datetime) else obj.total_seconds()
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

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-9;]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a colorized box UI for terminal users to stderr."""
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [PROXY UTILITY ENGINE v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}         {NEON_YELLOW}{data.get('action', 'check')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Available:{RESET}      {NEON_GREEN if data.get('available') else NEON_RED}{data.get('available', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}SOCKS Port:{RESET}     {NEON_YELLOW}{data.get('socks_port', 1080)}{RESET}"
    )

    proxies = data.get("proxies", {})
    if proxies:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Active Proxies:{RESET}")
        for k, v in proxies.items():
            if v:
                _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {k:<6}: {v}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: CORE PROXY LOGIC & PUBLIC API
# ==============================================================================


def get_proxy_settings() -> Dict[str, Optional[str]]:
    """Return a dictionary of raw proxy URLs from standard environment variables."""
    return {
        "http": os.getenv("HTTP_PROXY") or os.getenv("http_proxy"),
        "https": os.getenv("HTTPS_PROXY") or os.getenv("https_proxy"),
        "all": os.getenv("ALL_PROXY") or os.getenv("all_proxy"),
        "socks": os.getenv("SOCKS_PROXY") or os.getenv("socks_proxy"),
    }


def get_socks_port() -> int:
    """
    Return the active SOCKS proxy port.
    Probes standard local Tor/SOCKS ports (9050, 1080, 9052) if no env var is set.
    """
    env_port = os.getenv("PROXY_PORT") or os.getenv("SOCKS_PORT")
    if env_port and env_port.isdigit():
        return int(env_port)

    # Probe localhost ports for an active Tor/SOCKS daemon
    for test_port in (9050, 1080, 9052):
        try:
            with socket.create_connection(("127.0.0.1", test_port), timeout=0.3):
                return test_port
        except OSError:
            continue

    return 1080  # Default Termux/Orbot fallback port


def get_proxies() -> Dict[str, str]:
    """
    Return a formatted dictionary for `requests`: `{'http': '...', 'https': '...'}`.
    Auto-detects SOCKS_PROXY, ALL_PROXY, HTTP_PROXY, HTTPS_PROXY, or local Tor daemons.
    """
    proxies: Dict[str, str] = {}

    socks = os.getenv("SOCKS_PROXY") or os.getenv("socks_proxy")
    all_p = os.getenv("ALL_PROXY") or os.getenv("all_proxy")
    http_p = os.getenv("HTTP_PROXY") or os.getenv("http_proxy")
    https_p = os.getenv("HTTPS_PROXY") or os.getenv("https_proxy")

    if socks:
        socks_url = socks if "://" in socks else f"socks5h://{socks}"
        proxies["http"] = socks_url
        proxies["https"] = socks_url
    elif all_p:
        all_url = all_p if "://" in all_p else f"socks5h://{all_p}"
        proxies["http"] = all_url
        proxies["https"] = all_url
    else:
        if http_p:
            proxies["http"] = http_p if "://" in http_p else f"http://{http_p}"
        if https_p:
            proxies["https"] = https_p if "://" in https_p else f"http://{https_p}"

    # Fallback to local Tor SOCKS proxy automatically to bypass Geo-blocks
    if not proxies and os.getenv("PROXY_ENABLED", "true").lower() in (
        "true",
        "1",
        "yes",
        "auto",
    ):
        tor_port = get_socks_port()
        tor_url = f"socks5h://127.0.0.1:{tor_port}"
        proxies["http"] = tor_url
        proxies["https"] = tor_url

    return proxies


def set_proxy_environment() -> None:
    """
    Populate and synchronize HTTP_PROXY/HTTPS_PROXY/ALL_PROXY environment variables
    for third-party libraries (e.g. `requests`, `urllib`).
    """
    proxies = get_proxies()
    if proxies.get("http"):
        os.environ["HTTP_PROXY"] = proxies["http"]
        os.environ["http_proxy"] = proxies["http"]
    if proxies.get("https"):
        os.environ["HTTPS_PROXY"] = proxies["https"]
        os.environ["https_proxy"] = proxies["https"]

    all_p = os.getenv("ALL_PROXY") or os.getenv("all_proxy")
    if all_p:
        os.environ["ALL_PROXY"] = all_p
        os.environ["all_proxy"] = all_p


def _parse_authority(proxy_url: str) -> Optional[Tuple[str, int]]:
    """Safely extract host and port from a proxy URL without throwing exceptions."""
    if not proxy_url:
        return None
    url = proxy_url if "://" in proxy_url else f"http://{proxy_url}"
    try:
        parsed = urlparse(url)
        host = parsed.hostname
        if not host:
            return None
        if parsed.port:
            port = parsed.port
        elif parsed.scheme in ("socks5", "socks5h", "socks4"):
            port = 9050 if "9050" in proxy_url else 1080
        elif parsed.scheme == "https":
            port = 443
        else:
            port = 80
        return host, port
    except Exception:
        return None


def is_proxy_available(
    timeout: float = 3.0, custom_proxy_url: Optional[str] = None
) -> bool:
    """Check if at least one configured or passed proxy is reachable via TCP connection."""
    target_urls = (
        [custom_proxy_url] if custom_proxy_url else list(get_proxy_settings().values())
    )

    if not any(target_urls):
        target_urls = list(get_proxies().values())

    if not any(target_urls):
        return False

    for proxy_url in target_urls:
        if not proxy_url:
            continue
        auth = _parse_authority(proxy_url)
        if not auth:
            continue
        host, port = auth
        try:
            with socket.create_connection((host, port), timeout=timeout):
                return True
        except OSError:
            continue

    return False


# ==============================================================================
# SECTION 4: OUTPUT ROUTING & EXECUTOR
# ==============================================================================


def execute_proxy_tool(
    action: str = "check",
    proxy_url: Optional[str] = None,
    timeout: float = 3.0,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Execute proxy utilities and return structured status metadata."""
    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(
            f"Running proxy tool | Action: {action} | Custom Proxy: {proxy_url}"
        )

    set_proxy_environment()
    available = is_proxy_available(timeout=timeout, custom_proxy_url=proxy_url)
    active_proxies = get_proxies()
    socks_port = get_socks_port()

    return {
        "success": True,
        "action": action,
        "available": available,
        "socks_port": socks_port,
        "proxies": active_proxies,
        "raw_settings": get_proxy_settings(),
    }


def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    json_payload = (
        json.dumps(data, indent=2, ensure_ascii=False, cls=ToolJSONEncoder) + "\n"
    )

    if out_path in ("/dev/stdout", "/dev/fd/1", "-"):
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
# SECTION 5: PROGRAMMATIC ENTRY POINT
# ==============================================================================


def run(
    action: str = "check",
    proxy_url: Optional[str] = None,
    timeout: float = 3.0,
    no_color: bool = False,
    verbose: bool = False,
) -> Dict[str, Any]:
    """Programmatic entry point for AIChat and Python scripts."""
    result = execute_proxy_tool(
        action=action,
        proxy_url=proxy_url,
        timeout=timeout,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)
    return result


# ==============================================================================
# SECTION 6: CLI ARGUMENT PARSER
# ==============================================================================


def _coerce(val: str) -> Any:
    if val == "":
        return None
    low = val.lower()
    if low in ("true", "yes", "1"):
        return True
    if low in ("false", "no", "0"):
        return False
    try:
        return int(val)
    except ValueError:
        pass
    try:
        return float(val)
    except ValueError:
        pass
    return val


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="proxy_utils.py", description=f"Proxy Utility Tool v{__version__}"
    )
    parser.add_argument(
        "--action",
        default="check",
        choices=["check", "get", "sync"],
        help="Action to perform",
    )
    parser.add_argument("--proxy-url", help="Custom proxy URL to test")
    parser.add_argument(
        "--timeout", type=float, default=3.0, help="Connection test timeout in seconds"
    )
    parser.add_argument(
        "--no-color", action="store_true", help="Disable ANSI color output"
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="Enable verbose debug logging"
    )
    return parser


if __name__ == "__main__":
    if any(k.startswith("argc_") for k in os.environ):
        kwargs = {}
        for k, v in os.environ.items():
            if k.startswith("argc_"):
                kwargs[k[5:].replace("-", "_")] = _coerce(v)
        res = run(**kwargs)
        sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)

    args = _build_parser().parse_args()
    res = run(
        action=args.action,
        proxy_url=args.proxy_url,
        timeout=args.timeout,
        no_color=args.no_color,
        verbose=args.verbose,
    )
    sys.exit(EXIT_SUCCESS if res.get("success") else EXIT_ERROR)
