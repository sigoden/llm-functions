#!/usr/bin/env python3
# ==============================================================================
# wget2.py — Pyrmethus AIChat Tool Wrapper for GNU Wget2 v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Termux Aware
#
# @describe Download files or websites using GNU Wget2
#
# @meta require-tools wget2
#
# @option --url!                        The URL to download (required)
# @option --output-file -O              File to save the output
# @option --user-agent                  Custom User-Agent header string
# @option --limit-rate                  Limit bandwidth usage (e.g., 100k, 1M)
# @option --tries -t                    Number of retries (default: 20)
# @option --waitretry                   Wait specified seconds between retries
# @flag   --quiet -q                    Quiet mode (suppress non-error output)
# @flag   --verbose -v                  Verbose mode (detailed download log)
# @flag   --mirror -m                   Mirror a website recursively
# @flag   --no-check-certificate        Don't validate the server's TLS certificate
# @flag   --use-cache                   Enable result caching for download metadata
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
from typing import Any, Optional
from urllib.parse import urlparse

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
    """Render a human-friendly, colorized box UI for terminal users on stderr."""
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [GNU WGET2 TOOL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}URL:{RESET}        {data.get('url', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Output File:{RESET}{NEON_YELLOW}{data.get('downloaded_file') or data.get('output_file') or 'Default'}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}File Size:{RESET}  {NEON_GREEN}{_format_bytes(data.get('file_size_bytes'))}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}     {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}   {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}      {data['error']}")

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
        "tool_name": os.environ.get("LLM_TOOL_NAME", "wget2"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix
        or Path("/data/data/com.termux").exists(),
    }


def _find_ca_certs() -> Optional[str]:
    """Detect platform TLS certificate bundles including Termux, Linux, and Android paths."""
    prefix = os.environ.get("PREFIX", "")
    candidates = [
        Path(prefix) / "etc" / "tls" / "cert.pem" if prefix else None,
        Path("/data/data/com.termux/files/usr/etc/tls/cert.pem"),
        Path("/etc/ssl/certs/ca-certificates.crt"),
        Path("/etc/pki/tls/certs/ca-bundle.crt"),
    ]
    for cand in candidates:
        if cand and cand.is_file():
            return str(cand)
    return None


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
    url: str,
    output_file: Optional[str] = None,
    user_agent: Optional[str] = None,
    limit_rate: Optional[str] = None,
    tries: Optional[str] = None,
    waitretry: Optional[str] = None,
    quiet: bool = False,
    verbose: bool = False,
    mirror: bool = False,
    no_check_certificate: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
) -> dict[str, Any]:
    """Execute GNU Wget2 download and process results."""
    start_time = time.monotonic()

    if not url or not isinstance(url, str):
        return {
            "success": False,
            "error": "Missing or invalid required parameter 'url'",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    # Verify wget2 installation
    wget2_bin = shutil.which("wget2")
    if not wget2_bin:
        return {
            "success": False,
            "error": "GNU Wget2 binary ('wget2') was not found in PATH. Please install wget2.",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    cache = ToolCache()
    cache_key = f"{url}:{output_file}:{mirror}:{no_check_certificate}"
    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    wget2_args = [wget2_bin]

    ca_cert_path = _find_ca_certs()
    if ca_cert_path and not no_check_certificate:
        wget2_args.extend(["--ca-certificate", ca_cert_path])

    wget2_args.append("--no-cookies")

    if no_check_certificate:
        wget2_args.append("--no-check-certificate")
    if output_file:
        wget2_args.extend(["-O", output_file])
    if user_agent:
        wget2_args.extend(["--user-agent", user_agent])
    if quiet:
        wget2_args.append("-q")
    if verbose:
        wget2_args.append("-v")
    if mirror:
        wget2_args.append("-m")
    if limit_rate:
        wget2_args.extend(["--limit-rate", limit_rate])
    if tries:
        wget2_args.extend(["-t", str(tries)])
    if waitretry:
        wget2_args.extend(["--waitretry", str(waitretry)])

    wget2_args.append(url)

    shutdown = GracefulShutdown()

    try:
        proc = subprocess.run(wget2_args)
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        if shutdown.interrupted:
            return {
                "success": False,
                "error": "Download operation was interrupted by user signal.",
                "exit_code": EXIT_INTERRUPTED,
                "duration_ms": duration_ms,
            }

        if proc.returncode != 0:
            return {
                "success": False,
                "url": url,
                "output_file": output_file,
                "error": f"wget2 process exited with non-zero exit code ({proc.returncode})",
                "exit_code": proc.returncode,
                "duration_ms": duration_ms,
            }

        # Resolve output destination and verify downloaded file size
        downloaded_path: Optional[Path] = None
        if output_file:
            downloaded_path = Path(output_file).expanduser().resolve()
        else:
            url_path_name = Path(urlparse(url).path).name
            if url_path_name:
                cand = Path.cwd() / url_path_name
                if cand.exists():
                    downloaded_path = cand.resolve()

        file_size: Optional[int] = None
        if downloaded_path and downloaded_path.is_file():
            try:
                file_size = downloaded_path.stat().st_size
            except OSError:
                file_size = None

        result: dict[str, Any] = {
            "success": True,
            "url": url,
            "output_file": output_file,
            "downloaded_file": str(downloaded_path) if downloaded_path else None,
            "file_size_bytes": file_size,
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
            "error": f"Permission denied executing download: {exc}",
            "exit_code": EXIT_PERMISSION_DENIED,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Wget2 execution error for {url}: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 6: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write JSON output to LLM_OUTPUT destination safely."""
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
    url: str,
    output_file: Optional[str] = None,
    user_agent: Optional[str] = None,
    limit_rate: Optional[str] = None,
    tries: Optional[str] = None,
    waitretry: Optional[str] = None,
    quiet: bool = False,
    verbose: bool = False,
    mirror: bool = False,
    no_check_certificate: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
) -> None:
    """Download files or websites using GNU Wget2.

    Args:
        url: The URL to download
        output_file: File to save the output (-O)
        user_agent: Custom User-Agent header
        limit_rate: Limit bandwidth usage (e.g., 100k, 1M)
        tries: Number of retries (default: 20)
        waitretry: Wait specified seconds between retries
        quiet: Quiet mode (suppress output)
        verbose: Verbose mode
        mirror: Mirror a website recursively
        no_check_certificate: Skip TLS certificate validation
        use_cache: Enable result caching
        no_color: Disable ANSI color output
    """
    res = execute_tool(
        url=url,
        output_file=output_file,
        user_agent=user_agent,
        limit_rate=limit_rate,
        tries=tries,
        waitretry=waitretry,
        quiet=quiet,
        verbose=verbose,
        mirror=mirror,
        no_check_certificate=no_check_certificate,
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
        prog="wget2.py",
        description=f"AIChat GNU Wget2 Tool Wrapper v{__version__}",
    )
    parser.add_argument("--url", required=True, help="The URL to download")
    parser.add_argument("--output-file", "-O", help="File to save the output")
    parser.add_argument("--user-agent", help="Custom User-Agent header")
    parser.add_argument("--limit-rate", help="Limit bandwidth usage (e.g., 100k, 1M)")
    parser.add_argument("--tries", "-t", help="Number of retries")
    parser.add_argument("--waitretry", help="Wait specified seconds between retries")
    parser.add_argument("--quiet", "-q", action="store_true", help="Quiet mode")
    parser.add_argument("--verbose", "-v", action="store_true", help="Verbose mode")
    parser.add_argument(
        "--mirror", "-m", action="store_true", help="Mirror website recursively"
    )
    parser.add_argument(
        "--no-check-certificate",
        action="store_true",
        help="Don't validate server certificate",
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
                url=normalized.get("url", ""),
                output_file=normalized.get("output_file"),
                user_agent=normalized.get("user_agent"),
                limit_rate=normalized.get("limit_rate"),
                tries=str(normalized.get("tries"))
                if normalized.get("tries") is not None
                else None,
                waitretry=str(normalized.get("waitretry"))
                if normalized.get("waitretry") is not None
                else None,
                quiet=bool(normalized.get("quiet", False)),
                verbose=bool(normalized.get("verbose", False)),
                mirror=bool(normalized.get("mirror", False)),
                no_check_certificate=bool(
                    normalized.get("no_check_certificate", False)
                ),
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
        url=args.url,
        output_file=args.output_file,
        user_agent=args.user_agent,
        limit_rate=args.limit_rate,
        tries=args.tries,
        waitretry=args.waitretry,
        quiet=args.quiet,
        verbose=args.verbose,
        mirror=args.mirror,
        no_check_certificate=args.no_check_certificate,
        use_cache=args.use_cache,
        no_color=args.no_color,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
