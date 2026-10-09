#!/usr/bin/env python3
# ==============================================================================
# docker_intel.py — Pyrmethus AIChat Docker Management & Inspection Tool
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe Docker container inspector, status monitor, log tailer, and command runner.
#
# @meta require-tools aichat
#
# @option --target <CONTAINER>            Target container ID or name
# @option --action <ACTION>               Operation: list/logs/stats/inspect/exec (default: auto)
# @option --cmd <CMD>                     Command string to execute inside container
# @option --lines <NUM>                   Number of log lines to tail (default: 50)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --limit <NUM>                  Maximum containers to process (default: 100)
# @option --timeout <SEC>                Maximum execution timeout in seconds
# @flag   --use-cache                    Enable result caching for static inspection
# @flag   --clear-cache                  Clear cache directory and exit
# @flag   --schema                       Print JSON Tool Schema for LLM registration and exit
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# @env LLM_TOOL_MODE=summary             Default execution mode override
# @env LLM_TOOL_LIMIT=100                Default processing limit override
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
import shutil
import subprocess
import sys
import time
from datetime import datetime, timedelta
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.5.0"
__all__ = [
    "run",
    "execute_tool",
    "main",
    "validate_inputs",
    "build_cache_key",
    "invalidate_cache",
    "generate_tool_schema",
    "ToolCache",
    "ToolError",
    "GracefulShutdown",
    "get_agent_var",
    "get_builtin_var",
    "get_execution_context",
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

VALID_MODES = {"summary", "detailed"}
VALID_ACTIONS = {"auto", "list", "logs", "stats", "inspect", "exec"}


class ExecutionMode(str, Enum):
    SUMMARY = "summary"
    DETAILED = "detailed"


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
    action: str,
    mode: str,
    limit: Optional[int],
    lines: Optional[int] = None,
    timeout: Optional[float] = None,
) -> Optional[dict[str, Any]]:
    if action not in VALID_ACTIONS:
        return {
            "success": False,
            "error": f"Invalid action '{action}'. Allowed choices: {sorted(list(VALID_ACTIONS))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if mode not in VALID_MODES:
        return {
            "success": False,
            "error": f"Invalid mode '{mode}'. Allowed choices: {sorted(list(VALID_MODES))}",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if limit is not None and limit < 0:
        return {
            "success": False,
            "error": f"Invalid limit '{limit}'. Limit must be >= 0.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if lines is not None and lines < 0:
        return {
            "success": False,
            "error": f"Invalid lines count '{lines}'. Must be >= 0.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    if timeout is not None and timeout <= 0:
        return {
            "success": False,
            "error": f"Invalid timeout '{timeout}'. Timeout must be > 0 seconds.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }
    return None


class ToolJSONEncoder(json.JSONEncoder):
    """Resilient JSON encoder handling Path, Enum, datetime, timedelta, bytes, and sets."""

    def default(self, obj: Any) -> Any:
        try:
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
        except Exception:
            pass
        return repr(obj)


# ==============================================================================
# SECTION 2: Terminal Colors & UI Display Helpers
# ==============================================================================

NEON_CYAN    = "\033[38;5;51m"
NEON_GREEN   = "\033[38;5;46m"
NEON_RED     = "\033[38;5;196m"
NEON_YELLOW  = "\033[38;5;226m"
NEON_PURPLE  = "\033[38;5;129m"
NEON_PINK    = "\033[38;5;198m"
RESET        = "\033[0m"
BOLD         = "\033[1m"
DIM          = "\033[2m"

_ANSI_RE = re.compile(r"\x1b(?:[@-Z\\-_]|\[[0-?]*[ -/]*[@-~])|\033\[[0-9;?]*[a-zA-Z]")


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", text)


def _is_tty() -> bool:
    return sys.stderr.isatty() and os.environ.get("TERM", "").lower() not in ("dumb", "")


def _cprint(text: str, file: Any = None, no_color: bool = False, end: str = "\n") -> None:
    target = file or sys.stderr
    if no_color or not _is_tty():
        text = _strip_ansi(text)
    print(text, file=target, flush=True, end=end)


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
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [DOCKER INTEL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target Container:{RESET} {data.get('target', 'All')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}           {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count/Items:{RESET}     {NEON_YELLOW}{data.get('count', 0)}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}        {DIM}{data.get('duration_ms', 0)}ms{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}           {data['error']}")

    containers = data.get("containers")
    if containers:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Containers ({len(containers)}):{RESET}")
        for c in containers[:8]:
            name = c.get("name", "unknown")
            status = c.get("status", "unknown")
            image = c.get("image", "unknown")
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{name}{RESET} ({image}) - {NEON_YELLOW}{status}{RESET}")

    logs = data.get("logs")
    if logs:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Tail Logs:{RESET}")
        for line in str(logs).splitlines()[:6]:
            if len(line) > 64:
                line = line[:61] + "..."
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}{line}{RESET}")

    exec_out = data.get("exec_output")
    if exec_out:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Exec Output:{RESET}")
        for line in str(exec_out).splitlines()[:6]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_GREEN}{line}{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent Environment & Path Resolution Helpers
# ==============================================================================

def get_agent_var(name: str, default: str = "") -> str:
    return os.environ.get(f"LLM_AGENT_VAR_{name.upper()}", default)


def get_builtin_var(name: str) -> Optional[str]:
    return os.environ.get(f"LLM_AGENT_VAR_{name}")


def get_execution_context() -> dict[str, Any]:
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "docker_intel"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "root_dir": os.environ.get("LLM_ROOT_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
        "termux_prefix": termux_prefix,
        "is_termux": "com.termux" in termux_prefix or Path("/data/data/com.termux").exists(),
    }


def resolve_agent_path(target_str: str) -> Path:
    raw_path = Path(target_str).expanduser()
    if raw_path.is_absolute():
        return raw_path.resolve(strict=False)

    agent_cwd = get_builtin_var("__cwd__")
    if agent_cwd:
        return (Path(agent_cwd) / raw_path).resolve(strict=False)

    return raw_path.resolve(strict=False)


def env_default(env_name: str, fallback: Any = None) -> dict[str, Any]:
    val = os.getenv(env_name)
    if val is not None:
        return {"default": val}
    if fallback is not None:
        return {"default": fallback}
    return {}


# ==============================================================================
# SECTION 4: Cache Management, Signal Handling & Tool Schema
# ==============================================================================

def build_cache_key(
    target: Optional[str],
    action: str,
    lines_val: int,
) -> str:
    return "|".join([
        __version__,
        target or "all",
        action,
        str(lines_val),
    ])


class ToolCache:
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

    def _hash_key(self, key_str: str) -> str:
        return hashlib.sha256(key_str.encode("utf-8")).hexdigest()

    def get(self, key_str: str, ttl_seconds: int = 300) -> Optional[Any]:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
        if not cache_file.exists():
            return None
        try:
            if time.time() - cache_file.stat().st_mtime > ttl_seconds:
                cache_file.unlink(missing_ok=True)
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def set(self, key_str: str, value: Any) -> None:
        cache_file = self.cache_dir / f"{self._hash_key(key_str)}.cache"
        tmp_file = cache_file.with_suffix(".tmp")
        try:
            with open(tmp_file, "wb") as fp:
                pickle.dump(value, fp)
            tmp_file.replace(cache_file)
        except Exception:
            if tmp_file.exists():
                tmp_file.unlink(missing_ok=True)


def invalidate_cache(cache: Optional[ToolCache] = None, prefix: str = "") -> int:
    cache_obj = cache or ToolCache()
    removed = 0
    if not cache_obj.cache_dir.exists():
        return removed
    for file in cache_obj.cache_dir.glob("*.cache"):
        if not prefix or file.name.startswith(prefix):
            try:
                file.unlink(missing_ok=True)
                removed += 1
            except OSError:
                pass
    return removed


class GracefulShutdown:
    def __init__(self) -> None:
        self.interrupted = False
        self._old_sigint = None
        self._old_sigterm = None

    def __enter__(self) -> GracefulShutdown:
        self.interrupted = False
        try:
            self._old_sigint = signal.signal(signal.SIGINT, self._handle_signal)
            self._old_sigterm = signal.signal(signal.SIGTERM, self._handle_signal)
        except ValueError:
            pass
        return self

    def __exit__(self, exc_type: Any, exc_val: Any, exc_tb: Any) -> None:
        self.restore()

    def _handle_signal(self, signum: int, frame: Any) -> None:
        self.interrupted = True

    def restore(self) -> None:
        if getattr(self, "_old_sigint", None) is not None:
            try:
                signal.signal(signal.SIGINT, self._old_sigint)
            except ValueError:
                pass
        if getattr(self, "_old_sigterm", None) is not None:
            try:
                signal.signal(signal.SIGTERM, self._old_sigterm)
            except ValueError:
                pass

    def should_stop(self) -> bool:
        return getattr(self, "interrupted", False)


def generate_tool_schema() -> dict[str, Any]:
    return {
        "name": "docker_intel",
        "description": "Inspect Docker containers, list running stacks, tail logs, fetch resource stats, or execute commands inside containers.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target Docker container name or ID"
                },
                "action": {
                    "type": "string",
                    "enum": ["auto", "list", "logs", "stats", "inspect", "exec"],
                    "description": "Action mode (default: auto detect list or logs)"
                },
                "cmd": {
                    "type": "string",
                    "description": "Command string to execute when action is 'exec'"
                },
                "lines": {
                    "type": "integer",
                    "description": "Number of log lines to tail (default: 50)"
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Execution mode (default: summary)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum containers to list (default: 100)"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for static inspection"
                }
            }
        }
    }


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================

def execute_tool(
    target: Optional[str] = None,
    action: str = "auto",
    cmd: Optional[str] = None,
    lines: Optional[int] = None,
    mode: str = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    bad_inputs = validate_inputs(action, mode, limit, lines, timeout)
    if bad_inputs:
        return bad_inputs

    # Ensure docker CLI is installed
    docker_bin = shutil.which("docker")
    if not docker_bin:
        return {
            "success": False,
            "error": "Docker CLI binary 'docker' is not installed or available in PATH.",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    limit_val = limit if limit is not None else 100
    lines_val = lines if lines is not None else 50
    req_timeout = timeout if timeout is not None else 20.0

    actual_action = action
    if actual_action == "auto":
        if target:
            actual_action = "logs"
        else:
            actual_action = "list"

    cache = ToolCache()
    cache_key = build_cache_key(target, actual_action, lines_val)

    if use_cache and actual_action in {"list", "inspect"}:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    containers_list: list[dict[str, Any]] = []
    logs_output: Optional[str] = None
    stats_data: Optional[Any] = None
    inspect_data: Optional[Any] = None
    exec_output: Optional[str] = None

    try:
        with GracefulShutdown() as shutdown:
            # ------------------------------------------------------------------
            # ACTION 1: LIST CONTAINERS
            # ------------------------------------------------------------------
            if actual_action == "list":
                fmt_str = '{"id":"{{.ID}}","name":"{{.Names}}","image":"{{.Image}}","status":"{{.Status}}","ports":"{{.Ports}}"}'
                res = subprocess.run(
                    [docker_bin, "ps", "-a", "--format", fmt_str],
                    capture_output=True,
                    text=True,
                    timeout=req_timeout,
                )
                if res.returncode != 0:
                    raise ToolError(f"Docker command failed: {res.stderr.strip()}")

                for line in res.stdout.splitlines():
                    if shutdown.should_stop():
                        raise ToolError("Interrupted by signal", EXIT_INTERRUPTED)
                    if not line.strip():
                        continue
                    try:
                        c_info = json.loads(line)
                        containers_list.append(c_info)
                    except json.JSONDecodeError:
                        pass
                    if len(containers_list) >= limit_val:
                        break

            # ------------------------------------------------------------------
            # ACTION 2: TAIL LOGS
            # ------------------------------------------------------------------
            elif actual_action == "logs":
                if not target:
                    raise ToolError("Container target (--target) is required for logs action.")

                res = subprocess.run(
                    [docker_bin, "logs", "--tail", str(lines_val), target],
                    capture_output=True,
                    text=True,
                    timeout=req_timeout,
                )
                logs_output = (res.stdout + res.stderr).strip()

            # ------------------------------------------------------------------
            # ACTION 3: CONTAINER STATS
            # ------------------------------------------------------------------
            elif actual_action == "stats":
                cmd_args = [docker_bin, "stats", "--no-stream", "--format", '{"id":"{{.ID}}","name":"{{.Name}}","cpu":"{{.CPUPerc}}","mem":"{{.MemUsage}}","mem_perc":"{{.MemPerc}}","net_io":"{{.NetIO}}"}']
                if target:
                    cmd_args.append(target)

                res = subprocess.run(
                    cmd_args,
                    capture_output=True,
                    text=True,
                    timeout=req_timeout,
                )
                if res.returncode == 0:
                    parsed_stats = []
                    for line in res.stdout.splitlines():
                        try:
                            parsed_stats.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass
                    stats_data = parsed_stats

            # ------------------------------------------------------------------
            # ACTION 4: INSPECT
            # ------------------------------------------------------------------
            elif actual_action == "inspect":
                if not target:
                    raise ToolError("Container target (--target) is required for inspect action.")

                res = subprocess.run(
                    [docker_bin, "inspect", target],
                    capture_output=True,
                    text=True,
                    timeout=req_timeout,
                )
                if res.returncode == 0:
                    try:
                        inspect_data = json.loads(res.stdout)
                    except json.JSONDecodeError:
                        inspect_data = res.stdout.strip()

            # ------------------------------------------------------------------
            # ACTION 5: EXEC COMMAND
            # ------------------------------------------------------------------
            elif actual_action == "exec":
                if not target:
                    raise ToolError("Container target (--target) is required for exec action.")
                if not cmd:
                    raise ToolError("Command string (--cmd) is required for exec action.")

                res = subprocess.run(
                    [docker_bin, "exec", target, "sh", "-c", cmd],
                    capture_output=True,
                    text=True,
                    timeout=req_timeout,
                )
                exec_output = (res.stdout + res.stderr).strip()

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "target": target or "all",
            "action": actual_action,
            "count": len(containers_list) if actual_action == "list" else 1,
            "containers": containers_list if actual_action == "list" else None,
            "logs": logs_output,
            "stats": stats_data,
            "inspect": inspect_data,
            "exec_output": exec_output,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache and actual_action in {"list", "inspect"}:
            cache.set(cache_key, result)

        return result

    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except subprocess.TimeoutExpired:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Docker command timed out after {req_timeout}s",
            "exit_code": EXIT_TIMEOUT,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Docker tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }


# ==============================================================================
# SECTION 6: Output Routing
# ==============================================================================

def write_llm_output(data: dict[str, Any]) -> None:
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    payload = json.dumps(data, ensure_ascii=False, cls=ToolJSONEncoder)

    if out_path in {"/dev/stdout", "/dev/fd/1", "-"}:
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()
        return

    try:
        Path(out_path).parent.mkdir(parents=True, exist_ok=True)
        with open(out_path, "a", encoding="utf-8") as fp:
            fp.write(payload + "\n")
    except OSError as err:
        sys.stderr.write(f"Failed writing to LLM_OUTPUT '{out_path}': {err}\n")
        sys.stdout.write(payload + "\n")
        sys.stdout.flush()


# ==============================================================================
# SECTION 7: Function Entry Point for AIChat
# ==============================================================================

def run(
    target: Optional[str] = None,
    action: Literal["auto", "list", "logs", "stats", "inspect", "exec"] = "auto",
    cmd: Optional[str] = None,
    lines: Optional[int] = None,
    mode: Literal["summary", "detailed"] = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        target=target,
        action=action,
        cmd=cmd,
        lines=lines,
        mode=mode,
        limit=limit,
        timeout=timeout,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 8: CLI Parser & Main Entrypoint
# ==============================================================================

def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="docker_intel.py",
        description=f"AIChat Docker Management Tool v{__version__}",
    )
    parser.add_argument(
        "--target", "-t",
        metavar="CONTAINER",
        help="Target container name or ID",
    )
    parser.add_argument(
        "--action", "-a",
        choices=["auto", "list", "logs", "stats", "inspect", "exec"],
        default="auto",
        help="Operation action (default: auto)",
    )
    parser.add_argument(
        "--cmd", "-c",
        help="Command string to execute inside container when action is 'exec'",
    )
    parser.add_argument(
        "--lines", "-l",
        type=int,
        default=50,
        help="Number of log lines to tail (default: 50)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        **env_default("LLM_TOOL_MODE", "summary"),
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        **env_default("LLM_TOOL_LIMIT", 100),
        help="Maximum containers to process (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        dest="use_cache",
        help="Enable result caching for static operations",
    )
    parser.add_argument(
        "--clear-cache",
        action="store_true",
        dest="clear_cache",
        help="Clear tool cache directory and exit",
    )
    parser.add_argument(
        "--schema",
        action="store_true",
        help="Print JSON Tool Schema for LLM registration and exit",
    )
    parser.add_argument(
        "--no-color",
        action="store_true",
        dest="no_color",
        help="Disable ANSI color output",
    )
    parser.add_argument(
        "--verbose", "-v",
        action="store_true",
        help="Enable detailed debug logging",
    )
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
        _cprint(f"{NEON_GREEN}Cleared {removed} cache file(s).{RESET}", no_color=args.no_color)
        return EXIT_SUCCESS

    res = execute_tool(
        target=args.target,
        action=args.action,
        cmd=args.cmd,
        lines=args.lines,
        mode=args.mode,
        limit=args.limit,
        timeout=args.timeout,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
