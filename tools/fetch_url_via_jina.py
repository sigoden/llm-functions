#!/usr/bin/env python3
# ==============================================================================
# fetch_url.py — Pyrmethus AIChat Jina AI Reader Tool v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Extract content from a URL using Jina AI Reader API.
#
# @meta require-tools aichat
#
# @option --url! <URL>                   Target URL to scrape (required)
# @option --timeout <NUM>                Request timeout in seconds (default: 30)
# @option --format <FORMAT>              Output format: text, json, markdown (default: text)
# @option --target-selector <SELECTOR>   CSS selector to target specific element
# @option --remove-selector <SELECTOR>   CSS selector to exclude/remove element
# @option --wait-for-selector <SELECTOR> CSS selector to wait for before returning
# @flag   --no-cache                     Bypass Jina AI cache
# @flag   --use-cache                    Enable local result caching
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env JINA_API_KEY                      Optional Jina API key for higher rate limits
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import pickle
import re
import signal
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional, Union

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


class FormatMode(str, Enum):
    TEXT = "text"
    JSON = "json"
    MARKDOWN = "markdown"


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


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """Render a human-friendly colorized box UI to stderr."""
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [JINA AI READER v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}URL:{RESET}      {data.get('url', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Format:{RESET}   {data.get('format', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Status:{RESET}   {NEON_YELLOW}{data.get('status_code', 'N/A')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}   {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET} {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}    {data['error']}")

    content = data.get("content")
    if content:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Content Preview:{RESET}")
        preview_str = str(content).replace("\r", "").replace("\n", " ")
        if len(preview_str) > 250:
            preview_str = preview_str[:250] + "..."
        _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{preview_str}{RESET}")

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
    """Extract complete execution context."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


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
    """Signal handler for graceful cancellation."""

    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
        self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        signal.signal(signal.SIGINT, self._old_sigint)
        signal.signal(signal.SIGTERM, self._old_sigterm)

    def should_stop(self) -> bool:
        return self.interrupted


# ==============================================================================
# SECTION 5: Core Logic Implementation
# ==============================================================================


def execute_tool(
    url: str,
    timeout: int = 30,
    format: str = "text",
    target_selector: Optional[str] = None,
    remove_selector: Optional[str] = None,
    wait_for_selector: Optional[str] = None,
    no_cache: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic fetching page content through Jina AI Reader API.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Starting fetch for URL: {url}")

    # Normalize input URL
    normalized_url = url.strip()
    if not re.match(r"^https?://", normalized_url, re.IGNORECASE):
        normalized_url = f"https://{normalized_url}"

    jina_endpoint = f"https://r.jina.ai/{normalized_url}"

    # Cache lookup
    cache = ToolCache()
    cache_key = f"{normalized_url}:{format}:{target_selector}:{remove_selector}:{wait_for_selector}:{no_cache}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            if verbose:
                logging.debug("Cache hit! Returning cached response.")
            cached_result["cached"] = True
            return cached_result

    # Construct headers
    accept_header = "text/plain"
    if format == "json":
        accept_header = "application/json"
    elif format == "markdown":
        accept_header = "text/markdown"

    headers = {
        "Accept": accept_header,
        "User-Agent": "fetch_url_script/2.2",
    }

    api_key = os.environ.get("JINA_API_KEY")
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"

    if target_selector:
        headers["X-Target-Selector"] = target_selector
    if remove_selector:
        headers["X-Remove-Selector"] = remove_selector
    if wait_for_selector:
        headers["X-Wait-For-Selector"] = wait_for_selector
    if no_cache:
        headers["X-No-Cache"] = "true"

    req = urllib.request.Request(jina_endpoint, headers=headers, method="GET")
    shutdown = GracefulShutdown()

    try:
        if shutdown.should_stop():
            return {
                "success": False,
                "error": "Operation interrupted before request dispatch.",
                "exit_code": EXIT_INTERRUPTED,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            }

        with urllib.request.urlopen(req, timeout=timeout) as response:
            status_code = response.getcode()
            body = response.read().decode("utf-8", errors="replace")

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        # Parse JSON payload if requested
        content_output: Union[str, dict[str, Any]] = body
        if format == "json":
            try:
                content_output = json.loads(body)
            except json.JSONDecodeError:
                pass

        result: dict[str, Any] = {
            "success": True,
            "url": normalized_url,
            "jina_endpoint": jina_endpoint,
            "format": format,
            "status_code": status_code,
            "content": content_output,
            "cached": False,
            "context": get_execution_context(),
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache:
            cache.set(cache_key, result)

        return result

    except urllib.error.HTTPError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        err_body = exc.read().decode("utf-8", errors="replace") if exc.fp else str(exc)
        return {
            "success": False,
            "url": normalized_url,
            "format": format,
            "status_code": exc.code,
            "error": f"HTTP {exc.code}: {exc.reason}",
            "content": err_body,
            "duration_ms": duration_ms,
            "exit_code": EXIT_ERROR,
        }
    except (urllib.error.URLError, TimeoutError) as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "url": normalized_url,
            "format": format,
            "error": f"Network error or timeout: {exc}",
            "duration_ms": duration_ms,
            "exit_code": EXIT_TIMEOUT,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "url": normalized_url,
            "format": format,
            "error": f"Execution error: {exc}",
            "duration_ms": duration_ms,
            "exit_code": EXIT_ERROR,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write output to LLM_OUTPUT destination safely."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")

    # If format is text or markdown and request succeeded, pass raw content to stdout directly if plain format requested
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
    url: str,
    timeout: int = 30,
    format: Literal["text", "json", "markdown"] = "text",
    target_selector: Optional[str] = None,
    remove_selector: Optional[str] = None,
    wait_for_selector: Optional[str] = None,
    no_cache: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute Jina AI Reader tool with specified parameters.

    Args:
        url: Target URL to scrape (required)
        timeout: Request timeout in seconds (default: 30)
        format: Output format: text, json, markdown (default: text)
        target_selector: Optional CSS selector to target specific element
        remove_selector: Optional CSS selector to exclude/remove element
        wait_for_selector: Optional CSS selector to wait for before returning
        no_cache: Bypass Jina AI cache
        use_cache: Enable local result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug logging
    """
    result = execute_tool(
        url=url,
        timeout=timeout,
        format=format,
        target_selector=target_selector,
        remove_selector=remove_selector,
        wait_for_selector=wait_for_selector,
        no_cache=no_cache,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="fetch_url.py",
        description=f"AIChat Jina AI Reader Web Scraper Tool v{__version__}",
    )
    parser.add_argument(
        "--url",
        "-u",
        required=True,
        metavar="URL",
        help="Target URL to scrape (required)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=30,
        metavar="NUM",
        help="Request timeout in seconds (default: 30)",
    )
    parser.add_argument(
        "--format",
        choices=["text", "json", "markdown"],
        default="text",
        help="Output format (default: text)",
    )
    parser.add_argument(
        "--target-selector",
        dest="target_selector",
        metavar="SELECTOR",
        help="CSS selector to target specific element",
    )
    parser.add_argument(
        "--remove-selector",
        dest="remove_selector",
        metavar="SELECTOR",
        help="CSS selector to exclude/remove element",
    )
    parser.add_argument(
        "--wait-for-selector",
        dest="wait_for_selector",
        metavar="SELECTOR",
        help="CSS selector to wait for before returning",
    )
    parser.add_argument(
        "--no-cache",
        action="store_true",
        default=False,
        dest="no_cache",
        help="Bypass Jina AI cache",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable local result caching",
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
        timeout=args.timeout,
        format=args.format,
        target_selector=args.target_selector,
        remove_selector=args.remove_selector,
        wait_for_selector=args.wait_for_selector,
        no_cache=args.no_cache,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
