#!/usr/bin/env python3
# ==============================================================================
# memory_analytics.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Generates usage analytics, access patterns, growth trends, and optimization recommendations for persistent memory stores.
#
# @option --analytics-type <TYPE>        Type of analysis: summary, patterns, trends, recommendations (default: summary)
# @option --days <DAYS>                  Number of past days to include in analysis (default: 7)
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
import sys
import time
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Optional

__version__ = "1.1.0"

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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [MEMORY ANALYTICS & INSIGHTS]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Analytics Type:{RESET} {NEON_YELLOW}{data.get('analytics_type', 'summary')}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Period (Days):{RESET}  {data.get('period_days', 7)}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Total Memories:{RESET} {NEON_GREEN}{data.get('total_memories', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}       {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}          {data['error']}"
        )

    by_type = data.get("by_type", {})
    if by_type:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(f"{NEON_PURPLE}│{RESET} {BOLD}Distribution by Memory Type:{RESET}")
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

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================


def _parse_timestamp(ts_str: Optional[str]) -> Optional[datetime]:
    """Parse ISO timestamp with fallback handling for timezone offsets."""
    if not ts_str:
        return None
    try:
        clean_ts = ts_str[:-1] + "+00:00" if ts_str.endswith("Z") else ts_str
        dt = datetime.fromisoformat(clean_ts)
        return dt.replace(tzinfo=timezone.utc) if dt.tzinfo is None else dt
    except ValueError:
        return None


def generate_summary(memory_dir: Path, days: int) -> dict[str, Any]:
    """Generate overall memory usage statistics."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)

    total_count = 0
    by_type: defaultdict[str, int] = defaultdict(int)
    by_session: defaultdict[str, int] = defaultdict(int)
    top_tags: Counter[str] = Counter()
    activity_by_day: defaultdict[str, int] = defaultdict(int)

    for jsonl_file in memory_dir.glob("*.jsonl"):
        m_type = jsonl_file.stem
        try:
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
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
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    return {
        "period_days": days,
        "total_memories": total_count,
        "by_type": dict(by_type),
        "by_session": dict(by_session),
        "top_tags": dict(top_tags.most_common(10)),
        "activity_by_day": dict(sorted(activity_by_day.items())),
    }


def analyze_patterns(memory_dir: Path, days: int) -> dict[str, Any]:
    """Analyze tag co-occurrences and session usage patterns."""
    co_occurrence: defaultdict[str, Counter[str]] = defaultdict(Counter)
    session_types: defaultdict[str, Counter[str]] = defaultdict(Counter)

    for jsonl_file in memory_dir.glob("*.jsonl"):
        try:
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        tags = [t for t in entry.get("tags", []) if t]
                        session = entry.get("session", "default")
                        m_type = entry.get("type", jsonl_file.stem)

                        session_types[session][m_type] += 1

                        for i, tag in enumerate(tags):
                            for other in tags[i + 1 :]:
                                co_occurrence[tag][other] += 1
                                co_occurrence[other][tag] += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    formatted_co = {k: dict(v.most_common(5)) for k, v in co_occurrence.items()}
    formatted_sessions = {k: dict(v) for k, v in session_types.items()}

    return {
        "period_days": days,
        "tag_co_occurrence": formatted_co,
        "session_type_distribution": formatted_sessions,
    }


def analyze_trends(memory_dir: Path, days: int) -> dict[str, Any]:
    """Analyze volume trends per memory type across time."""
    cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
    daily_type_volume: defaultdict[str, defaultdict[str, int]] = defaultdict(
        lambda: defaultdict(int)
    )

    for jsonl_file in memory_dir.glob("*.jsonl"):
        m_type = jsonl_file.stem
        try:
            with open(jsonl_file, encoding="utf-8") as f:
                for line in f:
                    if not line.strip():
                        continue
                    try:
                        entry = json.loads(line)
                        dt = _parse_timestamp(entry.get("timestamp"))
                        if dt and dt >= cutoff_date:
                            day_key = dt.strftime("%Y-%m-%d")
                            daily_type_volume[day_key][entry.get("type", m_type)] += 1
                    except json.JSONDecodeError:
                        continue
        except OSError:
            continue

    sorted_trends = {
        day: dict(types) for day, types in sorted(daily_type_volume.items())
    }

    return {"period_days": days, "daily_volume_by_type": sorted_trends}


def analyze_recommendations(memory_dir: Path, days: int) -> dict[str, Any]:
    """Generate dynamic maintenance and optimization recommendations."""
    summary = generate_summary(memory_dir, days)
    total_memories = summary.get("total_memories", 0)
    top_tags = summary.get("top_tags", {})
    by_type = summary.get("by_type", {})

    recs: list[str] = []

    if total_memories > 300:
        recs.append(
            f"High memory density detected ({total_memories:,} entries in {days} days). Run 'memory_manager.py --action cleanup --days 30' to prune old context."
        )

    if not top_tags:
        recs.append(
            "No memory tags found. Add '--tags tag1,tag2' when storing memories to enable categorized searching."
        )

    if by_type.get("conversation", 0) > 200:
        recs.append(
            "Conversation log exceeds 200 items. Export or summarize active context into 'knowledge' memories for faster retrieval."
        )

    if not recs:
        recs.append(
            "Memory health is optimal. No maintenance actions required at this time."
        )

    return {
        "period_days": days,
        "total_memories": total_memories,
        "recommendations": recs,
    }


def execute_tool(
    analytics_type: str = "summary",
    days: int = 7,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core memory analytics execution logic.
    """
    start_time = time.perf_counter()
    root_dir = Path(os.environ.get("LLM_ROOT_DIR", os.getcwd())).resolve()
    memory_dir = root_dir / "memory"

    if not memory_dir.exists():
        return {
            "success": False,
            "error": f"Memory directory not found: {memory_dir}",
            "exit_code": 1,
        }

    a_type = analytics_type.lower().strip()
    days_val = max(1, int(days))

    try:
        if a_type == "summary":
            result = generate_summary(memory_dir, days_val)
        elif a_type == "patterns":
            result = analyze_patterns(memory_dir, days_val)
        elif a_type == "trends":
            result = analyze_trends(memory_dir, days_val)
        elif a_type == "recommendations":
            result = analyze_recommendations(memory_dir, days_val)
        else:
            return {
                "success": False,
                "error": f"Unknown analytics_type: '{analytics_type}' (choose summary, patterns, trends, recommendations)",
                "exit_code": 1,
            }

        duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

        output_payload = {
            "success": True,
            "analytics_type": a_type,
            "duration_ms": duration_ms,
            "exit_code": 0,
            **result,
        }

        return output_payload

    except Exception as exc:
        return {
            "success": False,
            "error": f"Memory analytics failed: {exc}",
            "exit_code": 1,
        }


# ==============================================================================
# SECTION 3: Output Routing (LLM vs Human Terminal)
# ==============================================================================


def write_llm_output(data: dict[str, Any]) -> None:
    """Format and write clean JSON output to LLM_OUTPUT destination."""
    out_path = os.environ.get("LLM_OUTPUT", "/dev/stdout")
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
    analytics_type: str = "summary",
    days: int = 7,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        analytics_type=analytics_type,
        days=days,
        no_color=no_color,
        verbose=verbose,
    )

    print_human_readable_ui(result, no_color=no_color)
    write_llm_output(result)


# ==============================================================================
# SECTION 5: CLI Argument Parser
# ==============================================================================


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="memory_analytics.py",
        description=f"AIChat Memory Analytics Tool v{__version__}",
    )
    parser.add_argument(
        "--analytics-type",
        "-a",
        choices=["summary", "patterns", "trends", "recommendations"],
        default="summary",
        dest="analytics_type",
        help="Type of analytics (default: summary)",
    )
    parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=7,
        help="Number of past days to analyze (default: 7)",
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
        analytics_type=args.analytics_type,
        days=args.days,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
