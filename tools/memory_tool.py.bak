#!/usr/bin/env python3
# ==============================================================================
# memory_tool.py — Pyrmethus AIChat Tool Master Template v2.2.0-ASCENDED
# argc/aichat compatible · Human-Readable Colorized Outputs · Native Caching
#
# @describe Advanced multi-tiered persistent memory toolkit with hybrid scoring, recall tracking, core/archival memory hierarchies, upserts, consolidation, and telemetry.
#
# @meta require-tools aichat
#
# @option --action! <ACTION>             Action: store, retrieve, update, delete, search, list, clear, export, import, cleanup, consolidate, analytics (required)
# @option --key <KEY>                    Memory key or unique identifier
# @option --value <VALUE>                Value to store, search query, or import file path
# @option --type <TYPE>                  Memory type: core, working, conversation, preference, context, knowledge, archival, all (default: context)
# @option --session <SESSION>            Session identifier
# @option --tags <TAGS>                  Comma-separated tags for categorization
# @option --importance <NUM>             Importance/priority rating 1-10 (default: 5)
# @option --ttl <SECONDS>                Time-to-live in seconds for auto-expiration
# @option --metadata <JSON>              JSON string of extra metadata attributes
# @option --analytics-type <TYPE>        Analytics mode: summary, patterns, trends, recommendations, health (default: summary)
# @option --days <DAYS>                  Days window for analytics or cleanup retention (default: 30)
# @option --limit <NUM>                  Maximum items to process/return (default: 100)
# @flag   --upsert                       Update existing entry if key exists on store (default: True)
# @flag   --use-cache                    Enable result caching for search/analytics
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
import uuid
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from enum import Enum
from pathlib import Path
from typing import Any, Optional

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


class ActionType(str, Enum):
    STORE = "store"
    RETRIEVE = "retrieve"
    UPDATE = "update"
    DELETE = "delete"
    SEARCH = "search"
    LIST = "list"
    CLEAR = "clear"
    EXPORT = "export"
    IMPORT = "import"
    CLEANUP = "cleanup"
    CONSOLIDATE = "consolidate"
    ANALYTICS = "analytics"


class MemoryType(str, Enum):
    CORE = "core"
    WORKING = "working"
    CONVERSATION = "conversation"
    PREFERENCE = "preference"
    CONTEXT = "context"
    KNOWLEDGE = "knowledge"
    ARCHIVAL = "archival"
    ALL = "all"


class AnalyticsType(str, Enum):
    SUMMARY = "summary"
    PATTERNS = "patterns"
    TRENDS = "trends"
    RECOMMENDATIONS = "recommendations"
    HEALTH = "health"


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


def print_progress(
    current: int, total: int, message: str = "", no_color: bool = False
) -> None:
    """Render a visual progress bar for long-running batch operations."""
    if not _is_tty() or no_color:
        return
    percent = (current / total) * 100.0 if total > 0 else 100.0
    bar_width = 30
    filled = int(bar_width * percent / 100.0)
    bar = "█" * filled + "░" * (bar_width - filled)

    _cprint(
        f"\r{NEON_CYAN}Progress:{RESET} [{NEON_GREEN}{bar}{RESET}] {percent:.1f}% {message}",
        end="",
        no_color=no_color,
    )
    if current >= total:
        _cprint("", no_color=no_color)


def print_human_readable_ui(data: dict[str, Any], no_color: bool = False) -> None:
    """
    Render a human-friendly colorized box UI for terminal users to stderr.
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [PERSISTENT MEMORY TOOLKIT v{__version__}]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Action:{RESET}       {NEON_YELLOW}{data.get('action', 'N/A')}{RESET}"
    )

    if "analytics_type" in data:
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Analytics:{RESET}    {NEON_YELLOW}{data.get('analytics_type')}{RESET}"
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Memory Tier:{RESET}  {data.get('type', 'all')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Count/Items:{RESET}  {NEON_GREEN}{data.get('count', data.get('total_memories', 0)):,}{RESET}"
    )

    if "health_score" in data:
        hs = data.get("health_score", 0)
        hs_color = NEON_GREEN if hs >= 80 else (NEON_YELLOW if hs >= 50 else NEON_RED)
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Health Score:{RESET} {hs_color}{BOLD}{hs}%{RESET}"
        )

    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Cached:{RESET}       {NEON_YELLOW}{data.get('cached', False)}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}     {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}        {data['error']}")

    by_type = data.get("by_type", {})
    if by_type:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Distribution by Memory Tier:{RESET}")
        for t_name, count in by_type.items():
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {t_name:<15}: {NEON_YELLOW}{count:,}{RESET} entries"
            )

    top_tags = data.get("top_tags", {})
    if top_tags:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Top Tags Used:{RESET}")
        for tag, count in list(top_tags.items())[:5]:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} #{tag:<14}: {NEON_GREEN}{count:,}{RESET} occurrences"
            )

    recs = data.get("recommendations", [])
    if recs:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Optimization Recommendations:{RESET}")
        for rec in recs:
            _cprint(f"{NEON_PURPLE}│{RESET}   {NEON_YELLOW}💡{RESET} {rec}")

    results = data.get("results", [])
    if results:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Memory Entries ({len(results)} shown):{RESET}"
        )
        for item in results[:5]:
            k = item.get("key", item.get("id", "N/A"))
            v_raw = str(item.get("value", ""))
            v = (v_raw[:40] + "...") if len(v_raw) > 40 else v_raw
            score_str = f" [{item['_score']:.2f}]" if "_score" in item else ""
            priority_str = f" [p:{item.get('importance', 5)}]"
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {NEON_CYAN}›{RESET} {BOLD}{k}{RESET}{score_str}{priority_str}: {DIM}{v}{RESET}"
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
    """Extract complete execution context from the LLM environment."""
    termux_prefix = os.environ.get("PREFIX", "")
    return {
        "tool_name": os.environ.get("LLM_TOOL_NAME", "memory_tool"),
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
    """Caching utility with TTL support for expensive search & analytics operations."""

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
    """Signal handler for cancellation of batch operations."""

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
# SECTION 5: Memory Storage & Analytics Utilities
# ==============================================================================


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp with robust timezone handling."""
    if not ts_str:
        return None
    try:
        clean_ts = ts_str[:-1] + "+00:00" if ts_str.endswith("Z") else ts_str
        dt = datetime.fromisoformat(clean_ts)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def _get_memory_files(memory_dir: Path, target_type: str) -> list[Path]:
    """Return matching .jsonl store paths based on type filter."""
    if target_type == "all":
        return sorted(list(memory_dir.glob("*.jsonl")))
    f_path = memory_dir / f"{target_type}.jsonl"
    return [f_path] if f_path.exists() else []


def _read_jsonl(file_path: Path) -> list[dict[str, Any]]:
    """Safely parse lines from a JSONL file."""
    entries: list[dict[str, Any]] = []
    if not file_path.exists():
        return entries
    try:
        with open(file_path, encoding="utf-8") as f:
            for line in f:
                line_str = line.strip()
                if not line_str:
                    continue
                try:
                    entries.append(json.loads(line_str))
                except json.JSONDecodeError:
                    continue
    except OSError:
        pass
    return entries


def _write_jsonl_atomic(file_path: Path, entries: list[dict[str, Any]]) -> None:
    """Atomically write entries to a JSONL file using a temp file."""
    file_path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = file_path.with_suffix(".tmp")
    with open(tmp_path, "w", encoding="utf-8") as f:
        for entry in entries:
            f.write(json.dumps(entry, ensure_ascii=False, cls=ToolJSONEncoder) + "\n")
    tmp_path.replace(file_path)


def _is_expired(entry: dict[str, Any], now_dt: datetime) -> bool:
    """Check if a memory entry is expired based on TTL or explicit expires_at."""
    expires_at = entry.get("expires_at")
    if expires_at:
        exp_dt = _parse_timestamp(expires_at)
        if exp_dt and exp_dt <= now_dt:
            return True

    ttl = entry.get("ttl")
    if ttl and isinstance(ttl, (int, float)) and ttl > 0:
        created_dt = _parse_timestamp(entry.get("timestamp") or entry.get("created_at"))
        if created_dt and (now_dt - created_dt).total_seconds() > ttl:
            return True

    return False


def _calculate_hybrid_score(
    entry: dict[str, Any],
    query_tokens: list[str],
    tag_list: list[str],
    now_dt: datetime,
) -> float:
    """
    Calculate a hybrid relevance score combining:
    1. Keyword term frequency and field weighting (Key > Tags > Value)
    2. Tag overlap
    3. Importance multiplier (1-10)
    4. Recency decay curve (half-life over 30 days)
    5. Access frequency boost (recall score)
    """
    score = 0.0
    if not query_tokens and not tag_list:
        return 1.0

    key_text = str(entry.get("key", "")).lower()
    val_text = str(entry.get("value", "")).lower()
    tags_lower = [str(t).lower() for t in entry.get("tags", [])]

    # Term Frequency and Field Weighting
    if query_tokens:
        for token in query_tokens:
            if token in key_text:
                score += 3.5  # Exact or sub-string key match
            if token in val_text:
                tf = val_text.count(token)
                score += min(2.5, 0.5 * tf)
            if any(token in t for t in tags_lower):
                score += 2.0

    # Tag Overlap Matching
    if tag_list:
        for target_tag in tag_list:
            t_low = target_tag.lower()
            if t_low in tags_lower:
                score += 3.0
            elif any(t_low in t for t in tags_lower):
                score += 1.0

    if score <= 0.0:
        return 0.0

    # Importance Rating Multiplier (1-10 scale -> 0.6x to 1.5x)
    importance = float(entry.get("importance", 5))
    importance_mult = 0.5 + (max(1.0, min(10.0, importance)) / 10.0)
    score *= importance_mult

    # Recency Decay Factor (smooth curve with 30-day scale)
    dt = _parse_timestamp(entry.get("timestamp") or entry.get("created_at"))
    if dt:
        age_days = max(0.0, (now_dt - dt).total_seconds() / 86400.0)
        recency_factor = 1.0 / (1.0 + (age_days / 30.0))
        score *= 0.7 + 0.3 * recency_factor

    # Access Frequency / Recall Boost
    access_count = int(entry.get("access_count", 0))
    if access_count > 0:
        score *= 1.0 + min(0.5, 0.05 * access_count)

    return round(score, 4)


# ==============================================================================
# SECTION 6: Analytics & Health Engines
# ==============================================================================


def generate_summary(memory_dir: Path, days: int) -> dict[str, Any]:
    """Generate memory usage statistics."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    total_count = 0
    by_type: defaultdict[str, int] = defaultdict(int)
    by_session: defaultdict[str, int] = defaultdict(int)
    top_tags: Counter[str] = Counter()
    activity_by_day: defaultdict[str, int] = defaultdict(int)

    for jsonl_file in memory_dir.glob("*.jsonl"):
        m_type = jsonl_file.stem
        for entry in _read_jsonl(jsonl_file):
            dt = _parse_timestamp(entry.get("timestamp"))
            if dt and dt >= cutoff_date:
                total_count += 1
                by_type[entry.get("type", m_type)] += 1
                by_session[entry.get("session", "default")] += 1

                for tag in entry.get("tags", []):
                    if tag:
                        top_tags[str(tag).strip()] += 1

                day_key = dt.strftime("%Y-%m-%d")
                activity_by_day[day_key] += 1

    return {
        "period_days": days,
        "total_memories": total_count,
        "by_type": dict(by_type),
        "by_session": dict(by_session),
        "top_tags": dict(top_tags.most_common(10)),
        "activity_by_day": dict(sorted(activity_by_day.items())),
    }


def analyze_patterns(memory_dir: Path, days: int) -> dict[str, Any]:
    """Analyze tag co-occurrences and session distribution."""
    co_occurrence: defaultdict[str, Counter[str]] = defaultdict(Counter)
    session_types: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for jsonl_file in memory_dir.glob("*.jsonl"):
        m_type = jsonl_file.stem
        for entry in _read_jsonl(jsonl_file):
            tags = [str(t).strip() for t in entry.get("tags", []) if t]
            session = entry.get("session", "default")
            entry_type = entry.get("type", m_type)

            session_types[session][entry_type] += 1

            for i, tag in enumerate(tags):
                for other in tags[i + 1 :]:
                    co_occurrence[tag][other] += 1
                    co_occurrence[other][tag] += 1

    formatted_co = {k: dict(v.most_common(5)) for k, v in co_occurrence.items()}
    formatted_sessions = {k: dict(v) for k, v in session_types.items()}

    return {
        "period_days": days,
        "tag_co_occurrence": formatted_co,
        "session_type_distribution": formatted_sessions,
    }


def analyze_trends(memory_dir: Path, days: int) -> dict[str, Any]:
    """Analyze volume trends per memory tier across time."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    daily_type_volume: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for jsonl_file in memory_dir.glob("*.jsonl"):
        m_type = jsonl_file.stem
        for entry in _read_jsonl(jsonl_file):
            dt = _parse_timestamp(entry.get("timestamp"))
            if dt and dt >= cutoff_date:
                day_key = dt.strftime("%Y-%m-%d")
                daily_type_volume[day_key][entry.get("type", m_type)] += 1

    sorted_trends = {
        day: dict(types) for day, types in sorted(daily_type_volume.items())
    }

    return {
        "period_days": days,
        "daily_volume_by_type": sorted_trends,
    }


def analyze_health(memory_dir: Path, days: int) -> dict[str, Any]:
    """Analyze memory store health, duplicate ratio, tag coverage, and expired ratio."""
    now_dt = datetime.now(timezone.utc)
    all_entries = []
    keys_seen = set()
    duplicate_count = 0
    untagged_count = 0
    expired_count = 0

    for jsonl_file in memory_dir.glob("*.jsonl"):
        for entry in _read_jsonl(jsonl_file):
            all_entries.append(entry)
            key = entry.get("key")
            if key:
                if key in keys_seen:
                    duplicate_count += 1
                else:
                    keys_seen.add(key)

            if not entry.get("tags"):
                untagged_count += 1

            if _is_expired(entry, now_dt):
                expired_count += 1

    total = len(all_entries)
    if total == 0:
        return {
            "health_score": 100,
            "total_memories": 0,
            "duplicate_count": 0,
            "untagged_count": 0,
            "expired_count": 0,
            "recommendations": ["Memory store is empty."],
        }

    dup_pct = (duplicate_count / total) * 100.0
    untagged_pct = (untagged_count / total) * 100.0
    expired_pct = (expired_count / total) * 100.0

    # Calculate health score (0-100)
    deductions = (dup_pct * 0.4) + (untagged_pct * 0.3) + (expired_pct * 0.3)
    health_score = max(0, min(100, int(100 - deductions)))

    recs: list[str] = []
    if duplicate_count > 0:
        recs.append(
            f"Found {duplicate_count} duplicate keys. Run '--action consolidate' to deduplicate."
        )
    if untagged_pct > 30.0:
        recs.append(
            f"{untagged_pct:.1f}% of memories lack tags. Add tags when storing memories for improved hybrid search precision."
        )
    if expired_count > 0:
        recs.append(
            f"Found {expired_count} expired entries. Run '--action cleanup' to purge them."
        )
    if not recs:
        recs.append("Memory health is optimal.")

    return {
        "health_score": health_score,
        "total_memories": total,
        "duplicate_count": duplicate_count,
        "untagged_count": untagged_count,
        "expired_count": expired_count,
        "duplicate_percentage": round(dup_pct, 2),
        "untagged_percentage": round(untagged_pct, 2),
        "expired_percentage": round(expired_pct, 2),
        "recommendations": recs,
    }


def analyze_recommendations(memory_dir: Path, days: int) -> dict[str, Any]:
    """Generate dynamic optimization recommendations."""
    summary = generate_summary(memory_dir, days)
    health = analyze_health(memory_dir, days)

    total_memories = summary.get("total_memories", 0)
    top_tags = summary.get("top_tags", {})
    by_type = summary.get("by_type", {})

    recs: list[str] = list(health.get("recommendations", []))

    if total_memories > 500:
        recs.append(
            f"High context density detected ({total_memories:,} entries in {days} days). Run '--action cleanup --days 30' to prune old context."
        )

    if by_type.get("conversation", 0) > 200:
        recs.append(
            "Conversation logs exceed 200 items. Summarize active context into 'knowledge' or 'core' memory tiers."
        )

    return {
        "period_days": days,
        "total_memories": total_memories,
        "health_score": health.get("health_score", 100),
        "recommendations": recs,
    }


# ==============================================================================
# SECTION 7: Core Execution Routine
# ==============================================================================


def execute_tool(
    action: str,
    key: Optional[str] = None,
    value: Optional[str] = None,
    type: str = "context",
    session: Optional[str] = None,
    tags: Optional[str] = None,
    importance: int = 5,
    ttl: Optional[int] = None,
    metadata: Optional[str] = None,
    analytics_type: str = "summary",
    days: int = 30,
    limit: Optional[int] = 100,
    upsert: bool = True,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core memory management & analytics logic shared by CLI and programmatic API.
    """
    start_time = time.monotonic()

    if verbose:
        logging.basicConfig(level=logging.DEBUG, format="[DEBUG] %(message)s")
        logging.debug(f"Executing memory action '{action}' on tier '{type}'")

    action_clean = action.lower().strip()
    type_clean = (type or "context").lower().strip()
    analytics_clean = (analytics_type or "summary").lower().strip()
    limit_val = limit if (limit is not None and limit >= 0) else 100
    importance_val = max(1, min(10, int(importance)))
    tag_list = [t.strip() for t in tags.split(",") if t.strip()] if tags else []

    # Parse metadata JSON string
    parsed_metadata: dict[str, Any] = {}
    if metadata:
        try:
            parsed_metadata = (
                json.loads(metadata) if isinstance(metadata, str) else dict(metadata)
            )
        except Exception as exc:
            return {
                "success": False,
                "error": f"Invalid metadata JSON string: {exc}",
                "exit_code": EXIT_INVALID_INPUT,
            }

    root_dir = Path(os.environ.get("LLM_ROOT_DIR", os.getcwd())).resolve()
    memory_dir = root_dir / "memory"
    memory_dir.mkdir(parents=True, exist_ok=True)

    cache = ToolCache()
    cache_key = f"mem2:{action_clean}:{type_clean}:{key}:{value}:{session}:{tags}:{importance_val}:{analytics_clean}:{days}:{limit_val}"

    if use_cache and action_clean in ("search", "analytics", "list"):
        cached_val = cache.get(cache_key)
        if cached_val is not None:
            cached_val["cached"] = True
            return cached_val

    shutdown = GracefulShutdown()
    now_dt = datetime.now(timezone.utc)
    now_iso = now_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    results: list[dict[str, Any]] = []

    try:
        # ----------------------------------------------------------------------
        # ACTION 1: STORE
        # ----------------------------------------------------------------------
        if action_clean == "store":
            if not key and not value:
                return {
                    "success": False,
                    "error": "Store requires --key or --value",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            store_type = "context" if type_clean == "all" else type_clean
            memory_file = memory_dir / f"{store_type}.jsonl"

            key_clean = key.strip() if key else f"mem_{uuid.uuid4().hex[:8]}"
            value_clean = value.strip() if value else key_clean

            # Calculate explicit expires_at if ttl is provided
            expires_at = None
            if ttl and isinstance(ttl, int) and ttl > 0:
                expires_at = (now_dt + timedelta(seconds=ttl)).strftime(
                    "%Y-%m-%dT%H:%M:%SZ"
                )

            entry = {
                "id": f"id_{uuid.uuid4().hex[:12]}",
                "key": key_clean,
                "value": value_clean,
                "type": store_type,
                "session": session.strip() if session else "default",
                "tags": tag_list,
                "importance": importance_val,
                "access_count": 0,
                "created_at": now_iso,
                "timestamp": now_iso,
                "last_accessed_at": now_iso,
                "ttl": ttl,
                "expires_at": expires_at,
                "metadata": parsed_metadata,
            }

            existing_entries = _read_jsonl(memory_file)
            updated = False

            if upsert:
                for idx, existing in enumerate(existing_entries):
                    if existing.get("key") == key_clean:
                        # Preserve existing ID, creation date, and access count
                        entry["id"] = existing.get("id", entry["id"])
                        entry["created_at"] = existing.get(
                            "created_at", existing.get("timestamp", now_iso)
                        )
                        entry["access_count"] = existing.get("access_count", 0)
                        existing_entries[idx] = entry
                        updated = True
                        break

            if not updated:
                existing_entries.append(entry)

            _write_jsonl_atomic(memory_file, existing_entries)
            results.append(entry)

        # ----------------------------------------------------------------------
        # ACTION 2: RETRIEVE
        # ----------------------------------------------------------------------
        elif action_clean == "retrieve":
            if not key:
                return {
                    "success": False,
                    "error": "Retrieve requires --key",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            files = _get_memory_files(memory_dir, type_clean)
            for f_path in files:
                entries = _read_jsonl(f_path)
                file_modified = False

                for idx, item in enumerate(entries):
                    if item.get("key") == key.strip() or item.get("id") == key.strip():
                        if session and item.get("session") != session.strip():
                            continue

                        if _is_expired(item, now_dt):
                            continue

                        # Update recall telemetry
                        item["access_count"] = int(item.get("access_count", 0)) + 1
                        item["last_accessed_at"] = now_iso
                        entries[idx] = item
                        file_modified = True
                        results.append(item)

                if file_modified:
                    _write_jsonl_atomic(f_path, entries)

        # ----------------------------------------------------------------------
        # ACTION 3: UPDATE
        # ----------------------------------------------------------------------
        elif action_clean == "update":
            if not key:
                return {
                    "success": False,
                    "error": "Update requires --key",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            files = _get_memory_files(memory_dir, type_clean)
            updated_count = 0

            for f_path in files:
                entries = _read_jsonl(f_path)
                file_modified = False

                for idx, item in enumerate(entries):
                    if item.get("key") == key.strip() or item.get("id") == key.strip():
                        if value:
                            item["value"] = value.strip()
                        if tag_list:
                            item["tags"] = tag_list
                        if importance:
                            item["importance"] = importance_val
                        if parsed_metadata:
                            existing_meta = item.get("metadata", {})
                            existing_meta.update(parsed_metadata)
                            item["metadata"] = existing_meta
                        if ttl:
                            item["ttl"] = ttl
                            item["expires_at"] = (
                                now_dt + timedelta(seconds=ttl)
                            ).strftime("%Y-%m-%dT%H:%M:%SZ")

                        item["timestamp"] = now_iso
                        entries[idx] = item
                        file_modified = True
                        updated_count += 1
                        results.append(item)

                if file_modified:
                    _write_jsonl_atomic(f_path, entries)

        # ----------------------------------------------------------------------
        # ACTION 4: DELETE
        # ----------------------------------------------------------------------
        elif action_clean == "delete":
            if not key:
                return {
                    "success": False,
                    "error": "Delete requires --key",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            files = _get_memory_files(memory_dir, type_clean)
            for f_path in files:
                entries = _read_jsonl(f_path)
                retained = []
                for item in entries:
                    if item.get("key") == key.strip() or item.get("id") == key.strip():
                        results.append(item)
                    else:
                        retained.append(item)

                if len(retained) < len(entries):
                    _write_jsonl_atomic(f_path, retained)

        # ----------------------------------------------------------------------
        # ACTION 5: SEARCH (Hybrid Scoring)
        # ----------------------------------------------------------------------
        elif action_clean == "search":
            query = (value or key or "").lower().strip()
            query_tokens = [q for q in re.split(r"\W+", query) if q]

            files = _get_memory_files(memory_dir, type_clean)
            scored_items: list[tuple[float, dict[str, Any]]] = []

            for f_path in files:
                for item in _read_jsonl(f_path):
                    if shutdown.should_stop():
                        return {
                            "success": False,
                            "error": "Interrupted by signal",
                            "exit_code": EXIT_INTERRUPTED,
                        }

                    if session and item.get("session") != session.strip():
                        continue

                    if _is_expired(item, now_dt):
                        continue

                    rel_score = _calculate_hybrid_score(
                        item, query_tokens, tag_list, now_dt
                    )
                    if rel_score > 0.0:
                        item_copy = dict(item)
                        item_copy["_score"] = rel_score
                        scored_items.append((rel_score, item_copy))

            scored_items.sort(key=lambda x: x[0], reverse=True)
            results = [item for _, item in scored_items[:limit_val]]

        # ----------------------------------------------------------------------
        # ACTION 6: LIST
        # ----------------------------------------------------------------------
        elif action_clean == "list":
            files = _get_memory_files(memory_dir, type_clean)
            for f_path in files:
                for item in _read_jsonl(f_path):
                    if session and item.get("session") != session.strip():
                        continue
                    if _is_expired(item, now_dt):
                        continue
                    if tag_list:
                        item_tags = [str(t).lower() for t in item.get("tags", [])]
                        if not any(t.lower() in item_tags for t in tag_list):
                            continue

                    results.append(item)
                    if len(results) >= limit_val:
                        break
                if len(results) >= limit_val:
                    break

        # ----------------------------------------------------------------------
        # ACTION 7: CLEAR
        # ----------------------------------------------------------------------
        elif action_clean == "clear":
            files = _get_memory_files(memory_dir, type_clean)
            for f_path in files:
                f_path.write_text("", encoding="utf-8")

        # ----------------------------------------------------------------------
        # ACTION 8: EXPORT
        # ----------------------------------------------------------------------
        elif action_clean == "export":
            files = _get_memory_files(memory_dir, type_clean)
            all_entries = []
            for f_path in files:
                all_entries.extend(_read_jsonl(f_path))

            export_filename = (
                f"{type_clean}_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.json"
            )
            export_path = memory_dir / export_filename
            export_path.write_text(
                json.dumps(
                    all_entries, indent=2, ensure_ascii=False, cls=ToolJSONEncoder
                ),
                encoding="utf-8",
            )
            results.append(
                {"export_path": str(export_path), "exported_count": len(all_entries)}
            )

        # ----------------------------------------------------------------------
        # ACTION 9: IMPORT
        # ----------------------------------------------------------------------
        elif action_clean == "import":
            if not value:
                return {
                    "success": False,
                    "error": "Missing import path in --value",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            import_path = Path(value).expanduser().resolve()
            if not import_path.exists():
                return {
                    "success": False,
                    "error": f"Import file not found: {import_path}",
                    "exit_code": EXIT_FILE_NOT_FOUND,
                }

            raw_text = import_path.read_text(encoding="utf-8")
            imported_entries: list[dict[str, Any]] = []

            try:
                data = json.loads(raw_text)
                if isinstance(data, list):
                    imported_entries = data
                elif isinstance(data, dict):
                    imported_entries = [data]
            except json.JSONDecodeError:
                for line in raw_text.splitlines():
                    if line.strip():
                        try:
                            imported_entries.append(json.loads(line))
                        except json.JSONDecodeError:
                            pass

            grouped: defaultdict[str, list[dict[str, Any]]] = defaultdict(list)
            for entry in imported_entries:
                e_type = entry.get("type", "context")
                if "id" not in entry:
                    entry["id"] = f"id_{uuid.uuid4().hex[:12]}"
                grouped[e_type].append(entry)

            for g_type, entries in grouped.items():
                target_file = memory_dir / f"{g_type}.jsonl"
                existing = _read_jsonl(target_file)
                existing.extend(entries)
                _write_jsonl_atomic(target_file, existing)

            results = imported_entries

        # ----------------------------------------------------------------------
        # ACTION 10: CLEANUP
        # ----------------------------------------------------------------------
        elif action_clean == "cleanup":
            retention_days = max(1, int(days))
            cutoff = now_dt - timedelta(days=retention_days)

            for jsonl_file in memory_dir.glob("*.jsonl"):
                retained_local = []
                for item in _read_jsonl(jsonl_file):
                    if _is_expired(item, now_dt):
                        continue
                    dt = _parse_timestamp(
                        item.get("timestamp") or item.get("created_at")
                    )
                    if dt and dt < cutoff:
                        continue
                    retained_local.append(item)

                _write_jsonl_atomic(jsonl_file, retained_local)
                results.extend(retained_local)

        # ----------------------------------------------------------------------
        # ACTION 11: CONSOLIDATE
        # ----------------------------------------------------------------------
        elif action_clean == "consolidate":
            consolidated_total = 0
            for jsonl_file in memory_dir.glob("*.jsonl"):
                entries = _read_jsonl(jsonl_file)
                key_map: dict[str, dict[str, Any]] = {}

                for item in entries:
                    if _is_expired(item, now_dt):
                        continue
                    k = item.get("key") or item.get("id")
                    if not k:
                        continue

                    if k in key_map:
                        # Keep the item with higher importance or more recent timestamp
                        existing_item = key_map[k]
                        e_imp = existing_item.get("importance", 5)
                        i_imp = item.get("importance", 5)
                        if i_imp > e_imp or (
                            i_imp == e_imp
                            and item.get("timestamp", "")
                            > existing_item.get("timestamp", "")
                        ):
                            key_map[k] = item
                    else:
                        key_map[k] = item

                consolidated_list = list(key_map.values())
                consolidated_total += len(entries) - len(consolidated_list)
                _write_jsonl_atomic(jsonl_file, consolidated_list)
                results.extend(consolidated_list)

            results = [
                {
                    "consolidated_pruned_count": consolidated_total,
                    "retained_count": len(results),
                }
            ]

        # ----------------------------------------------------------------------
        # ACTION 12: ANALYTICS
        # ----------------------------------------------------------------------
        elif action_clean == "analytics":
            if analytics_clean == "summary":
                analytics_res = generate_summary(memory_dir, days)
            elif analytics_clean == "patterns":
                analytics_res = analyze_patterns(memory_dir, days)
            elif analytics_clean == "trends":
                analytics_res = analyze_trends(memory_dir, days)
            elif analytics_clean == "health":
                analytics_res = analyze_health(memory_dir, days)
            elif analytics_clean == "recommendations":
                analytics_res = analyze_recommendations(memory_dir, days)
            else:
                return {
                    "success": False,
                    "error": f"Invalid analytics_type: '{analytics_type}'",
                    "exit_code": EXIT_INVALID_INPUT,
                }

            duration_ms = round((time.monotonic() - start_time) * 1000, 2)
            final_res = {
                "success": True,
                "action": action_clean,
                "analytics_type": analytics_clean,
                "type": type_clean,
                "cached": False,
                "duration_ms": duration_ms,
                "exit_code": EXIT_SUCCESS,
                **analytics_res,
            }
            if use_cache:
                cache.set(cache_key, final_res)
            return final_res

        else:
            return {
                "success": False,
                "error": f"Unknown action: '{action}'",
                "exit_code": EXIT_INVALID_INPUT,
            }

        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        final_res = {
            "success": True,
            "action": action_clean,
            "type": type_clean,
            "count": len(results),
            "results": results,
            "context": get_execution_context(),
            "cached": False,
            "duration_ms": duration_ms,
            "exit_code": EXIT_SUCCESS,
        }

        if use_cache and action_clean in ("search", "list"):
            cache.set(cache_key, final_res)

        return final_res

    except Exception as exc:
        duration_ms = round((time.monotonic() - start_time) * 1000, 2)
        return {
            "success": False,
            "error": f"Memory tool execution error: {exc}",
            "exit_code": EXIT_ERROR,
            "duration_ms": duration_ms,
        }
    finally:
        shutdown.restore()


# ==============================================================================
# SECTION 8: Output Routing (LLM vs Human Terminal)
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
# SECTION 9: Function Entry Point for AIChat
# ==============================================================================


def run(
    action: str,
    key: Optional[str] = None,
    value: Optional[str] = None,
    type: str = "context",
    session: Optional[str] = None,
    tags: Optional[str] = None,
    importance: int = 5,
    ttl: Optional[int] = None,
    metadata: Optional[str] = None,
    analytics_type: str = "summary",
    days: int = 30,
    limit: Optional[int] = 100,
    upsert: bool = True,
    use_cache: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """Execute memory management or analytics tool with specified options.

    Args:
        action: Action to perform (store, retrieve, update, delete, search, list, clear, export, import, cleanup, consolidate, analytics)
        key: Memory key or unique identifier
        value: Value to store, search query, or import file path
        type: Memory tier (core, working, conversation, preference, context, knowledge, archival, all)
        session: Session identifier filter
        tags: Comma-separated categorization tags
        importance: Importance/priority rating 1-10 (default: 5)
        ttl: Time-to-live in seconds for auto-expiration
        metadata: JSON string of extra metadata attributes
        analytics_type: Analytics mode (summary, patterns, trends, recommendations, health)
        days: Days window for analytics or cleanup retention
        limit: Maximum items to return/process
        upsert: Update existing entry if key matches on store
        use_cache: Enable result caching
        no_color: Disable ANSI color output
        verbose: Enable detailed debug logging
    """
    result = execute_tool(
        action=action,
        key=key,
        value=value,
        type=type,
        session=session,
        tags=tags,
        importance=importance,
        ttl=ttl,
        metadata=metadata,
        analytics_type=analytics_type,
        days=days,
        limit=limit,
        upsert=upsert,
        use_cache=use_cache,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 10: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_tool.py",
        description=f"AIChat Advanced Multi-Tiered Persistent Memory Tool v{__version__}",
    )
    parser.add_argument(
        "--action",
        "-a",
        required=True,
        choices=[
            "store",
            "retrieve",
            "update",
            "delete",
            "search",
            "list",
            "clear",
            "export",
            "import",
            "cleanup",
            "consolidate",
            "analytics",
        ],
        help="Action to perform (required)",
    )
    parser.add_argument(
        "--key",
        "-k",
        type=str,
        default=None,
        help="Memory key or identifier",
    )
    parser.add_argument(
        "--value",
        "-v",
        type=str,
        default=None,
        help="Value to store, search query, or import file path",
    )
    parser.add_argument(
        "--type",
        "-t",
        default="context",
        choices=[
            "core",
            "working",
            "conversation",
            "preference",
            "context",
            "knowledge",
            "archival",
            "all",
        ],
        help="Memory tier (default: context)",
    )
    parser.add_argument(
        "--session",
        "-s",
        type=str,
        default=None,
        help="Session identifier",
    )
    parser.add_argument(
        "--tags",
        type=str,
        default=None,
        help="Comma-separated tags for categorization",
    )
    parser.add_argument(
        "--importance",
        type=int,
        default=5,
        help="Importance/priority rating 1-10 (default: 5)",
    )
    parser.add_argument(
        "--ttl",
        type=int,
        default=None,
        help="Time-to-live in seconds for auto-expiration",
    )
    parser.add_argument(
        "--metadata",
        type=str,
        default=None,
        help="JSON string of extra metadata attributes",
    )
    parser.add_argument(
        "--analytics-type",
        choices=["summary", "patterns", "trends", "recommendations", "health"],
        default="summary",
        dest="analytics_type",
        help="Analytics mode (default: summary)",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=30,
        help="Days window for analytics or cleanup retention (default: 30)",
    )
    parser.add_argument(
        "--limit",
        type=int,
        default=100,
        help="Maximum items to return/process",
    )
    parser.add_argument(
        "--no-upsert",
        action="store_false",
        dest="upsert",
        default=True,
        help="Disable upsert mode during store action",
    )
    parser.add_argument(
        "--use-cache",
        action="store_true",
        default=False,
        dest="use_cache",
        help="Enable result caching",
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
        action="store_true",
        default=False,
        help="Enable detailed debug logging",
    )
    return parser


if __name__ == "__main__":
    args = _build_parser().parse_args()
    res = execute_tool(
        action=args.action,
        key=args.key,
        value=args.value,
        type=args.type,
        session=args.session,
        tags=args.tags,
        importance=args.importance,
        ttl=args.ttl,
        metadata=args.metadata,
        analytics_type=args.analytics_type,
        days=args.days,
        limit=args.limit,
        upsert=args.upsert,
        use_cache=args.use_cache,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", EXIT_SUCCESS))
