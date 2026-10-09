#!/usr/bin/env python3
# ==============================================================================
# sqlite_intel.py — Pyrmethus AIChat SQLite Database Tool
# argc/aichat compatible · Colorized UI · Safe Caching · Agent CWD Resolution
#
# @describe SQLite database inspector, schema explorer, query runner, and table exporter.
#
# @meta require-tools aichat
#
# @option --target! <PATH>               Target SQLite database file path (required)
# @option --query <SQL>                  SQL query to execute (SELECT/PRAGMA recommended)
# @option --table <NAME>                 Inspect specific table schema and rows
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --limit <NUM>                  Maximum rows to return (default: 100)
# @option --timeout <SEC>                Maximum execution timeout in seconds
# @option --export <FORMAT>              Export format: json/csv (default: json)
# @flag   --list-tables                  List all tables in the database
# @flag   --use-cache                    Enable result caching for read queries
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
import csv
import hashlib
import io
import json
import logging
import os
import pickle
import re
import signal
import sqlite3
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
VALID_EXPORTS = {"json", "csv"}


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
    target: Optional[str],
    mode: str,
    limit: Optional[int],
    timeout: Optional[float] = None,
    export_format: str = "json",
) -> Optional[dict[str, Any]]:
    """Validate input parameters strictly before processing."""
    if not target or not target.strip():
        return {
            "success": False,
            "error": "Target SQLite database file path is required.",
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
    if export_format not in VALID_EXPORTS:
        return {
            "success": False,
            "error": f"Invalid export format '{export_format}'. Allowed: {sorted(list(VALID_EXPORTS))}",
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
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [SQLITE INTEL v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}")
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target DB:{RESET} {data.get('target', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}    {data.get('action', 'N/A')}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Rows:{RESET}      {NEON_YELLOW}{data.get('count', 0)}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}    {NEON_YELLOW}{data.get('cached', False)}{RESET}")
    _cprint(f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}  {DIM}{data.get('duration_ms', 0)}ms{RESET}")

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}     {data['error']}")

    tables = data.get("tables")
    if tables:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Database Tables ({len(tables)}):{RESET}")
        for tbl in tables[:15]:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {tbl}")
        if len(tables) > 15:
            _cprint(f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(tables) - 15} more tables{RESET}")

    columns = data.get("columns")
    if columns:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Columns ({len(columns)}):{RESET}")
        cols_str = ", ".join([f"{c['name']} ({c['type']})" for c in columns[:8]])
        _cprint(f"{NEON_PURPLE}│{RESET}   {cols_str}")

    rows = data.get("rows")
    if rows:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Sample Data ({len(rows)} rows):{RESET}")
        for idx, row in enumerate(rows[:5]):
            row_preview = str(row)
            if len(row_preview) > 60:
                row_preview = row_preview[:57] + "..."
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_YELLOW}[{idx}]{RESET} {row_preview}")

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
        "tool_name": os.environ.get("LLM_TOOL_NAME", "sqlite_intel"),
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
    target_path: Path,
    mtime: float,
    query: Optional[str],
    table: Optional[str],
    list_tables: bool,
    limit_val: int,
) -> str:
    return "|".join([
        __version__,
        str(target_path),
        str(mtime),
        query or "",
        table or "",
        str(list_tables),
        str(limit_val),
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

    def get(self, key_str: str, ttl_seconds: int = 3600) -> Optional[Any]:
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
        "name": "sqlite_intel",
        "description": "Inspect SQLite databases, list tables, view column schemas, run SQL queries, and export data.",
        "parameters": {
            "type": "object",
            "properties": {
                "target": {
                    "type": "string",
                    "description": "Target SQLite database file path"
                },
                "query": {
                    "type": "string",
                    "description": "SQL query to execute (SELECT/PRAGMA recommended)"
                },
                "table": {
                    "type": "string",
                    "description": "Inspect specific table schema and preview top rows"
                },
                "list_tables": {
                    "type": "boolean",
                    "description": "List all tables inside the SQLite database"
                },
                "mode": {
                    "type": "string",
                    "enum": ["summary", "detailed"],
                    "description": "Execution mode (default: summary)"
                },
                "limit": {
                    "type": "integer",
                    "description": "Maximum number of rows to return (default: 100)"
                },
                "export": {
                    "type": "string",
                    "enum": ["json", "csv"],
                    "description": "Output export format (default: json)"
                },
                "use_cache": {
                    "type": "boolean",
                    "description": "Enable result caching for read-only operations"
                }
            },
            "required": ["target"]
        }
    }


# ==============================================================================
# SECTION 5: Core Execution Engine
# ==============================================================================

def execute_tool(
    target: str,
    query: Optional[str] = None,
    table: Optional[str] = None,
    list_tables: bool = False,
    mode: str = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    export_format: str = "json",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    start_time = time.monotonic()

    bad_inputs = validate_inputs(target, mode, limit, timeout, export_format)
    if bad_inputs:
        return bad_inputs

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Connecting to SQLite DB: {target}")

    limit_val = limit if limit is not None else 100
    target_path = resolve_agent_path(target)

    if not target_path.exists():
        return {
            "success": False,
            "error": f"SQLite DB file does not exist: {target} (Resolved: {target_path})",
            "exit_code": EXIT_FILE_NOT_FOUND,
            "duration_ms": 0.0,
        }

    mtime = target_path.stat().st_mtime
    cache = ToolCache()
    cache_key = build_cache_key(target_path, mtime, query, table, list_tables, limit_val)

    if use_cache:
        cached_result = cache.get(cache_key)
        if cached_result is not None:
            cached_result["cached"] = True
            return cached_result

    db_uri = f"file:{target_path}?mode=ro"
    
    try:
        with GracefulShutdown() as shutdown:
            # Try connecting read-only first
            try:
                conn = sqlite3.connect(db_uri, uri=True, timeout=timeout or 10.0)
            except sqlite3.OperationalError:
                conn = sqlite3.connect(str(target_path), timeout=timeout or 10.0)

            conn.row_factory = sqlite3.Row
            cursor = conn.cursor()

            action = "schema_summary"
            tables_list: list[str] = []
            columns_info: list[dict[str, Any]] = []
            rows_data: list[dict[str, Any]] = []
            row_count = 0
            formatted_export: Optional[str] = None

            if list_tables or (not query and not table):
                action = "list_tables"
                cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' ORDER BY name;")
                tables_list = [r["name"] for r in cursor.fetchall()]

            if table:
                action = f"inspect_table:{table}"
                cursor.execute(f"PRAGMA table_info('{table}')")
                col_rows = cursor.fetchall()
                columns_info = [
                    {
                        "cid": r["cid"],
                        "name": r["name"],
                        "type": r["type"],
                        "notnull": bool(r["notnull"]),
                        "default_value": r["dflt_value"],
                        "pk": bool(r["pk"]),
                    }
                    for r in col_rows
                ]

                cursor.execute(f"SELECT COUNT(*) as cnt FROM '{table}'")
                total_table_rows = cursor.fetchone()["cnt"]

                cursor.execute(f"SELECT * FROM '{table}' LIMIT ?", (limit_val,))
                fetched = cursor.fetchall()
                rows_data = [dict(r) for r in fetched]
                row_count = total_table_rows

            elif query:
                action = "execute_query"
                cursor.execute(query)
                if cursor.description:
                    fetched = cursor.fetchmany(limit_val)
                    rows_data = [dict(r) for r in fetched]
                    row_count = len(rows_data)
                    columns_info = [{"name": col[0]} for col in cursor.description]
                else:
                    conn.commit()
                    row_count = cursor.rowcount

            conn.close()

            if shutdown.should_stop():
                raise ToolError("Execution interrupted by signal.", EXIT_INTERRUPTED)

            if export_format == "csv" and rows_data:
                output_buf = io.StringIO()
                writer = csv.DictWriter(output_buf, fieldnames=rows_data[0].keys())
                writer.writeheader()
                writer.writerows(rows_data)
                formatted_export = output_buf.getvalue()

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)

        result: dict[str, Any] = {
            "success": True,
            "target": str(target_path),
            "action": action,
            "mode": mode,
            "count": row_count,
            "tables": tables_list if tables_list else None,
            "columns": columns_info if columns_info else None,
            "rows": rows_data if mode == "detailed" or query or table else rows_data[:10],
            "csv_export": formatted_export,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache and not query:
            cache.set(cache_key, result)

        return result

    except sqlite3.Error as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"SQLite error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    except ToolError as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": exc.message,
            "exit_code": exc.exit_code,
            "duration_ms": duration_ms,
        }
    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Unexpected execution error: {exc}",
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
    target: str,
    query: Optional[str] = None,
    table: Optional[str] = None,
    list_tables: bool = False,
    mode: Literal["summary", "detailed"] = "summary",
    limit: Optional[int] = None,
    timeout: Optional[float] = None,
    export_format: Literal["json", "csv"] = "json",
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """AIChat Tool entrypoint function."""
    result = execute_tool(
        target=target,
        query=query,
        table=table,
        list_tables=list_tables,
        mode=mode,
        limit=limit,
        timeout=timeout,
        export_format=export_format,
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
        prog="sqlite_intel.py",
        description=f"AIChat SQLite Database Tool v{__version__}",
    )
    parser.add_argument(
        "--target", "-t",
        required=False,
        metavar="PATH",
        help="Target SQLite database file path",
    )
    parser.add_argument(
        "--query", "-q",
        help="SQL query to execute",
    )
    parser.add_argument(
        "--table",
        help="Inspect specific table schema and preview rows",
    )
    parser.add_argument(
        "--list-tables",
        action="store_true",
        dest="list_tables",
        help="List all tables in database",
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
        help="Maximum rows to return (default: 100)",
    )
    parser.add_argument(
        "--timeout",
        type=float,
        help="Maximum execution timeout in seconds",
    )
    parser.add_argument(
        "--export",
        choices=["json", "csv"],
        default="json",
        dest="export_format",
        help="Export format (default: json)",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        dest="use_cache",
        help="Enable result caching for read operations",
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

    if not args.target:
        _cprint(f"{NEON_RED}Error: --target parameter is required for execution.{RESET}", no_color=args.no_color)
        return EXIT_INVALID_INPUT

    res = execute_tool(
        target=args.target,
        query=args.query,
        table=args.table,
        list_tables=args.list_tables,
        mode=args.mode,
        limit=args.limit,
        timeout=args.timeout,
        export_format=args.export_format,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    return res.get("exit_code", EXIT_SUCCESS)


if __name__ == "__main__":
    sys.exit(main())
