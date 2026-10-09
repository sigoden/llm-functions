#!/usr/bin/env python3
# ==============================================================================
# github_search.py — Pyrmethus AIChat GitHub Search Tool v2.0.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe GitHub Repository Search tool for AIChat using GitHub REST API.
#
# @meta require-tools aichat
#
# @option --query! <QUERY>               GitHub search query (e.g., "aichat", "topic:rag") (required)
# @option --language <LANG>              Filter by primary programming language (e.g. python, rust, go)
# @option --topic <TOPIC>                Filter by repository topic tag
# @option --min-stars <NUM>              Minimum stargazers count filter
# @option --user <USER_OR_ORG>           Filter by specific GitHub user or organization
# @option --sort <SORT>                  Sort field: stars, forks, help-wanted-issues, updated (default: stars)
# @option --order <ORDER>                Sort order: desc, asc (default: desc)
# @option --limit <NUM>                  Maximum items to process (default: 10, max: 100)
# @option --token <TOKEN>                GitHub access token (defaults to GITHUB_TOKEN environment variable)
# @option --mode <MODE>                  Execution mode: summary/detailed (default: summary)
# @option --ttl <SECONDS>                Cache TTL in seconds (default: 3600)
# @option --timeout <SECONDS>            Network HTTP request timeout in seconds (default: 15)
# @flag   --include-forks                Include repository forks in search results
# @flag   --include-archived             Include archived repositories in search results
# @flag   --use-cache                    Enable result caching for API operations
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
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
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Any, Literal, Optional

__version__ = "2.0.0"
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


class ToolJSONEncoder(json.JSONEncoder):
    """Custom JSON encoder handling Path, Enum, datetime, bytes, and sets safely."""

    def default(self, obj: Any) -> Any:
        if isinstance(obj, Path):
            return str(obj)
        if isinstance(obj, Enum):
            return obj.value
        if isinstance(obj, datetime):
            return obj.isoformat()
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
    """Render a human-friendly colorized ASCII box UI for interactive terminal users."""
    if not _is_tty() or no_color:
        return

    success = data.get("success", False)
    status_color = NEON_GREEN if success else NEON_RED
    status_symbol = "✓" if success else "✗"
    status_text = "SUCCESS" if success else "FAILED"
    if data.get("stale"):
        status_text = "FALLBACK (STALE CACHE)"
        status_color = NEON_YELLOW

    box_w = 72
    border = "─" * box_w

    _cprint(f"{NEON_PURPLE}╭{border}╮{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [GITHUB REPO SEARCH v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Synthesized Query:{RESET} {data.get('synthesized_query', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Sort/Order:{RESET}        {data.get('sort', 'stars')} ({data.get('order', 'desc')})"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Matches Found:{RESET}     {NEON_YELLOW}{data.get('total_count', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Items Displayed:{RESET}   {NEON_YELLOW}{data.get('count', 0)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached / Stale:{RESET}    {data.get('cached', False)} / {data.get('stale', False)}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}          {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    # Display Rate Limit Information if available
    rate_info = data.get("rate_limit")
    if rate_info and isinstance(rate_info, dict):
        rem = rate_info.get("remaining", "N/A")
        lim = rate_info.get("limit", "N/A")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}API Rate Limit:{RESET}    {NEON_GREEN}{rem}{RESET}/{lim} remaining"
        )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET} {data['error']}")

    items = data.get("items", [])
    if items:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Repositories ({len(items)}):{RESET}")
        for idx, repo in enumerate(items, 1):
            _cprint(f"{NEON_PURPLE}│{RESET}")
            stars_fmt = f"{NEON_YELLOW}⭐ {repo.get('stars', 0):,}{RESET}"
            forks_fmt = f"{DIM}🍴 {repo.get('forks', 0):,}{RESET}"
            _cprint(
                f"{NEON_PURPLE}│{RESET} {BOLD}{idx}. {NEON_GREEN}{repo.get('full_name')}{RESET} ({stars_fmt} | {forks_fmt})"
            )

            if repo.get("language"):
                _cprint(
                    f"{NEON_PURPLE}│{RESET}    {NEON_CYAN}Lang:{RESET} {repo.get('language')}"
                )

            if repo.get("topics"):
                topics_str = ", ".join(repo.get("topics", [])[:5])
                _cprint(
                    f"{NEON_PURPLE}│{RESET}    {NEON_PURPLE}Tags:{RESET} {DIM}{topics_str}{RESET}"
                )

            if repo.get("description"):
                desc = repo.get("description", "")
                desc_truncated = desc[:85] + "..." if len(desc) > 85 else desc
                _cprint(f"{NEON_PURPLE}│{RESET}    {DIM}{desc_truncated}{RESET}")

            _cprint(f"{NEON_PURPLE}│{RESET}    {NEON_PINK}🔗 {repo.get('url')}{RESET}")

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 3: Agent & Environment Helpers
# ==============================================================================


def get_agent_var(name: str, default: str = "") -> str:
    """Access agent user-defined variables (LLM_AGENT_VAR_<NAME>)."""
    env_name = f"LLM_AGENT_VAR_{name.upper()}"
    return os.environ.get(env_name, default)


def get_builtin_var(name: str) -> Optional[str]:
    """Access agent built-in environment variables."""
    env_name = f"LLM_AGENT_VAR_{name}"
    return os.environ.get(env_name)


def get_execution_context() -> dict[str, Any]:
    """Extract complete execution context from environment."""
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "github_search"),
        "cache_dir": os.environ.get("LLM_TOOL_CACHE_DIR"),
        "output_path": os.environ.get("LLM_OUTPUT"),
        "cwd": get_builtin_var("__cwd__") or os.getcwd(),
    }


# ==============================================================================
# SECTION 4: Native Caching & Signal Handlers
# ==============================================================================


class ToolCache:
    """Caching utility supporting TTL and stale fallback."""

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
                return None
            with open(cache_file, "rb") as fp:
                return pickle.load(fp)
        except Exception:
            return None

    def get_stale(self, key_data: str) -> Optional[Any]:
        """Fetch cached data even if expired (used as fallback during failures)."""
        cache_file = self.cache_dir / f"{self._make_key(key_data)}.cache"
        if not cache_file.exists():
            return None
        try:
            with open(cache_file, "rb") as fp:
                data = pickle.load(fp)
                if isinstance(data, dict):
                    data["stale"] = True
                return data
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


# ==============================================================================
# SECTION 5: Core Logic & Query Synthesizer
# ==============================================================================


def _synthesize_query(
    query: str,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    min_stars: Optional[int] = None,
    user: Optional[str] = None,
    include_forks: bool = False,
    include_archived: bool = False,
) -> str:
    """Build structured GitHub search query syntax automatically."""
    parts = [query.strip()] if query and query.strip() else []

    if language and "language:" not in query:
        parts.append(f"language:{language.strip()}")
    if topic and "topic:" not in query:
        parts.append(f"topic:{topic.strip()}")
    if min_stars is not None and "stars:" not in query:
        parts.append(f"stars:>={min_stars}")
    if user and not any(kw in query for kw in ("user:", "org:", "owner:")):
        parts.append(f"user:{user.strip()}")
    if include_forks and "fork:" not in query:
        parts.append("fork:true")
    if not include_archived and "archived:" not in query:
        parts.append("archived:false")

    return " ".join(parts)


def execute_tool(
    query: str,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    min_stars: Optional[int] = None,
    user: Optional[str] = None,
    sort: str = "stars",
    order: str = "desc",
    limit: Optional[int] = None,
    token: Optional[str] = None,
    mode: str = "summary",
    ttl: int = 3600,
    timeout: int = 15,
    include_forks: bool = False,
    include_archived: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """Execute GitHub API repository search with query synthesis, retry, and caching."""
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing search with raw query: '{query}'")

    if not query or not query.strip():
        return {
            "success": False,
            "error": "Query parameter cannot be empty.",
            "exit_code": EXIT_INVALID_INPUT,
            "duration_ms": 0.0,
        }

    synthesized_q = _synthesize_query(
        query=query,
        language=language,
        topic=topic,
        min_stars=min_stars,
        user=user,
        include_forks=include_forks,
        include_archived=include_archived,
    )

    limit_val = min(max(limit or 10, 1), 100)
    auth_token = (
        token or os.environ.get("GITHUB_TOKEN") or get_agent_var("GITHUB_TOKEN")
    )

    cache = ToolCache()
    cache_key = f"gh_search_v2:{synthesized_q}:{sort}:{order}:{limit_val}:{mode}"

    if use_cache:
        cached_result = cache.get(cache_key, ttl_seconds=ttl)
        if cached_result is not None:
            if verbose:
                logging.debug("Cache hit! Serving fresh cached GitHub response.")
            cached_result["cached"] = True
            cached_result["stale"] = False
            return cached_result

    params = {
        "q": synthesized_q,
        "sort": sort,
        "order": order,
        "per_page": str(limit_val),
    }
    url = f"https://api.github.com/search/repositories?{urllib.parse.urlencode(params)}"

    headers = {
        "User-Agent": "AIChat-GitHub-Search-Tool/2.0",
        "Accept": "application/vnd.github.v3+json",
    }
    if auth_token:
        headers["Authorization"] = f"Bearer {auth_token}"

    req = urllib.request.Request(url, headers=headers)
    shutdown = GracefulShutdown()

    max_retries = 2
    last_exception: Optional[Exception] = None
    rate_limit_info: dict[str, Any] = {}

    for attempt in range(max_retries + 1):
        if shutdown.interrupted:
            return {
                "success": False,
                "error": "Operation interrupted by user signal.",
                "exit_code": EXIT_INTERRUPTED,
                "duration_ms": round((time.monotonic() - start_time) * 1000, 2),
            }

        try:
            if verbose and attempt > 0:
                logging.debug(
                    f"Retrying HTTP request (Attempt {attempt + 1}/{max_retries + 1})..."
                )

            with urllib.request.urlopen(req, timeout=timeout) as response:
                resp_bytes = response.read()
                raw_headers = response.headers

                # Extract GitHub Rate Limit Headers
                if "x-ratelimit-remaining" in raw_headers:
                    rate_limit_info = {
                        "limit": raw_headers.get("x-ratelimit-limit"),
                        "remaining": raw_headers.get("x-ratelimit-remaining"),
                        "reset_timestamp": raw_headers.get("x-ratelimit-reset"),
                    }

                raw_data = json.loads(resp_bytes.decode("utf-8"))
                total_count = raw_data.get("total_count", 0)
                items_raw = raw_data.get("items", [])

                processed_items: list[dict[str, Any]] = []
                for repo in items_raw:
                    item_dict: dict[str, Any] = {
                        "name": repo.get("name"),
                        "full_name": repo.get("full_name"),
                        "owner": repo.get("owner", {}).get("login"),
                        "url": repo.get("html_url"),
                        "description": repo.get("description"),
                        "stars": repo.get("stargazers_count", 0),
                        "forks": repo.get("forks_count", 0),
                        "language": repo.get("language"),
                        "updated_at": repo.get("updated_at"),
                        "topics": repo.get("topics", []),
                    }

                    if mode == "detailed":
                        item_dict.update(
                            {
                                "license": repo.get("license", {}).get("name")
                                if repo.get("license")
                                else None,
                                "default_branch": repo.get("default_branch"),
                                "open_issues": repo.get("open_issues_count", 0),
                                "archived": repo.get("archived", False),
                                "pushed_at": repo.get("pushed_at"),
                            }
                        )

                    processed_items.append(item_dict)

                duration_ms = round((time.monotonic() - start_time) * 1000, 2)

                result: dict[str, Any] = {
                    "success": True,
                    "query": query,
                    "synthesized_query": synthesized_q,
                    "sort": sort,
                    "order": order,
                    "mode": mode,
                    "total_count": total_count,
                    "count": len(processed_items),
                    "items": processed_items,
                    "rate_limit": rate_limit_info,
                    "cached": False,
                    "stale": False,
                    "duration_ms": duration_ms,
                    "exit_code": EXIT_SUCCESS,
                }

                if use_cache:
                    cache.set(cache_key, result)

                return result

        except urllib.error.HTTPError as err:
            last_exception = err
            if err.code == 403:  # Rate Limit or Forbidden
                break
            elif err.code in (500, 502, 503, 504) and attempt < max_retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            else:
                break
        except (urllib.error.URLError, TimeoutError) as err:
            last_exception = err
            if attempt < max_retries:
                time.sleep(1.0 * (attempt + 1))
                continue
            break
        finally:
            shutdown.restore()

    # --- Fallback to Stale Cache on Failure ---
    if use_cache:
        stale_data = cache.get_stale(cache_key)
        if stale_data:
            stale_data["duration_ms"] = round((time.monotonic() - start_time) * 1000, 2)
            stale_data["warning"] = (
                f"Network or API request failed ({last_exception}). Serving stale fallback cache."
            )
            return stale_data

    duration_ms = round((time.monotonic() - start_time) * 1000, 2)
    err_msg = str(last_exception) if last_exception else "Unknown error occurred."
    if (
        isinstance(last_exception, urllib.error.HTTPError)
        and last_exception.code == 403
    ):
        err_msg += (
            " (GitHub Rate Limit Exceeded. Provide a --token or set GITHUB_TOKEN)"
        )

    return {
        "success": False,
        "error": f"GitHub API Request Failed: {err_msg}",
        "synthesized_query": synthesized_q,
        "exit_code": EXIT_ERROR,
        "duration_ms": duration_ms,
    }


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
    query: str,
    language: Optional[str] = None,
    topic: Optional[str] = None,
    min_stars: Optional[int] = None,
    user: Optional[str] = None,
    sort: str = "stars",
    order: str = "desc",
    limit: Optional[int] = None,
    token: Optional[str] = None,
    mode: Literal["summary", "detailed"] = "summary",
    ttl: int = 3600,
    timeout: int = 15,
    include_forks: bool = False,
    include_archived: bool = False,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Search GitHub repositories with specified parameters.

    Args:
        query: GitHub search query string (e.g. "aichat", "topic:rag")
        language: Filter by primary programming language
        topic: Filter by repository topic tag
        min_stars: Filter by minimum stargazers count
        user: Filter by specific GitHub user or organization
        sort: Sort field: stars, forks, help-wanted-issues, updated (default: stars)
        order: Sort order: desc or asc (default: desc)
        limit: Maximum results to return (default: 10, max: 100)
        token: Optional GitHub Personal Access Token
        mode: Execution mode: summary or detailed (default: summary)
        ttl: Cache TTL in seconds (default: 3600)
        timeout: HTTP timeout in seconds (default: 15)
        include_forks: Include repository forks
        include_archived: Include archived repositories
        use_cache: Enable API result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug logging
    """
    result = execute_tool(
        query=query,
        language=language,
        topic=topic,
        min_stars=min_stars,
        user=user,
        sort=sort,
        order=order,
        limit=limit,
        token=token,
        mode=mode,
        ttl=ttl,
        timeout=timeout,
        include_forks=include_forks,
        include_archived=include_archived,
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
        prog="github_search.py",
        description=f"AIChat GitHub Repository Search Tool v{__version__}",
    )
    parser.add_argument(
        "--query",
        "-q",
        required=True,
        metavar="QUERY",
        help="Search query string (e.g., 'aichat', 'llm')",
    )
    parser.add_argument(
        "--language",
        metavar="LANG",
        help="Filter by language (e.g. python, rust, go)",
    )
    parser.add_argument(
        "--topic",
        metavar="TOPIC",
        help="Filter by GitHub repository topic tag",
    )
    parser.add_argument(
        "--min-stars",
        type=int,
        metavar="NUM",
        dest="min_stars",
        help="Minimum stargazers count filter",
    )
    parser.add_argument(
        "--user",
        metavar="USER_OR_ORG",
        help="Filter by specific GitHub user or organization",
    )
    parser.add_argument(
        "--sort",
        choices=["stars", "forks", "help-wanted-issues", "updated"],
        default="stars",
        help="Sort field (default: stars)",
    )
    parser.add_argument(
        "--order",
        choices=["desc", "asc"],
        default="desc",
        help="Sort order (default: desc)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=10,
        help="Maximum items to process (default: 10, max: 100)",
    )
    parser.add_argument(
        "--token",
        metavar="TOKEN",
        help="GitHub personal access token (optional)",
    )
    parser.add_argument(
        "--mode",
        choices=["summary", "detailed"],
        default="summary",
        help="Execution mode (default: summary)",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=3600,
        help="Cache TTL in seconds (default: 3600)",
    )
    parser.add_argument(
        "--timeout",
        type=int,
        default=15,
        help="Network request timeout in seconds (default: 15)",
    )
    parser.add_argument(
        "--include-forks",
        action="store_true",
        default=False,
        dest="include_forks",
        help="Include repository forks in results",
    )
    parser.add_argument(
        "--include-archived",
        action="store_true",
        default=False,
        dest="include_archived",
        help="Include archived repositories in results",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable API result caching",
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
        help="Enable debug logging output",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        query=args.query,
        language=args.language,
        topic=args.topic,
        min_stars=args.min_stars,
        user=args.user,
        sort=args.sort,
        order=args.order,
        limit=args.limit,
        token=args.token,
        mode=args.mode,
        ttl=args.ttl,
        timeout=args.timeout,
        include_forks=args.include_forks,
        include_archived=args.include_archived,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
