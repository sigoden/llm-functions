#!/usr/bin/env python3
# ==============================================================================
# todo_reviewer.py — Pyrmethus AIChat Tool Template v1.1.0
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Audits files for existing TODOs and analyzes code logic to generate detailed, actionable TODO task items and TODO.md roadmaps.
#
# @option --target! <PATH>               Target file or directory path to review (required)
# @option --todo-file <PATH>             Destination path for generated Markdown TODO report (default: TODO.md)
# @option --min-priority <PRIORITY>      Minimum priority filter: high, medium, low (default: low)
# @flag   --write-md                     Write/update structured TODO.md file on disk
# @flag   --include-generated            Auto-detect code smells and generate actionable TODO tasks alongside existing comments
# @flag   --no-color                     Disable ANSI color output
# @flag   --verbose                      Enable detailed debug log output
#
# @env LLM_OUTPUT=/dev/stdout            Output path for LLM integration
# ==============================================================================

from __future__ import annotations

import argparse
import ast
import json
import os
import re
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [TODO REVIEW & GENERATOR TOOL]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}         {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Files Scanned:{RESET}  {NEON_YELLOW}{data.get('files_scanned', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}High Priority:{RESET}  {NEON_RED}{data.get('high_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Med Priority:{RESET}   {NEON_YELLOW}{data.get('medium_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Low Priority:{RESET}   {NEON_GREEN}{data.get('low_count', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Markdown File:{RESET}  {data.get('written_file', 'Not Written')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}       {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}          {data['error']}"
        )

    tasks = data.get("tasks", [])
    if tasks:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Actionable TODO Tasks Preview ({len(tasks)}):{RESET}"
        )
        for task in tasks[:8]:
            pri = task.get("priority", "LOW")
            pri_color = (
                NEON_RED
                if pri == "HIGH"
                else (NEON_YELLOW if pri == "MEDIUM" else NEON_GREEN)
            )
            rel_file = task.get("file", "")
            if len(rel_file) > 22:
                rel_file = "..." + rel_file[-19:]
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {pri_color}[{pri:<6}]{RESET} {rel_file}:{task.get('line', 0)} — {task.get('title')}"
            )
        if len(tasks) > 8:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(tasks) - 8} more tasks{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation
# ==============================================================================

INLINE_COMMENT_REGEX = re.compile(
    r"\b(TODO|FIXME|BUG|HACK|XXX|OPTIMIZE|NOTE)\b\s*[:|-]?\s*(.*)", re.IGNORECASE
)

HARDCODED_URL_REGEX = re.compile(r"https?://[a-zA-Z0-9.\-_]+(?::\d+)?/[^\s'\"]+")


class CodeSmellTodoAnalyzer(ast.NodeVisitor):
    """AST visitor to auto-generate TODO tasks from code logic inspection."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.tasks: list[dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # 1. Empty or Stubbed Function Definition
        if len(node.body) == 1 and isinstance(node.body[0], (ast.Pass, ast.Expr)):
            stmt = node.body[0]
            is_stub = isinstance(stmt, ast.Pass) or (
                isinstance(stmt, ast.Expr)
                and isinstance(stmt.value, ast.Constant)
                and stmt.value.value == ...
            )
            if is_stub:
                self.tasks.append(
                    {
                        "file": self.file_path,
                        "line": node.lineno,
                        "type": "STUB_FUNCTION",
                        "priority": "HIGH",
                        "title": f"Implement stubbed function '{node.name}'",
                        "details": f"Function '{node.name}' contains only a pass/ellipsis stub. Needs complete implementation logic.",
                        "recommendation": f"Add functional implementation and tests for '{node.name}'.",
                    }
                )

        # 2. Long Function Check (> 40 lines)
        func_len = (node.end_lineno or node.lineno) - node.lineno
        if func_len > 40:
            self.tasks.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "COMPLEX_FUNCTION",
                    "priority": "MEDIUM",
                    "title": f"Refactor long function '{node.name}' ({func_len} lines)",
                    "details": f"Function '{node.name}' spans {func_len} lines. High complexity increases bug risk.",
                    "recommendation": f"Break down '{node.name}' into smaller, modular helper functions.",
                }
            )

        # 3. Missing Docstring in Public Functions
        if not node.name.startswith("_") and not ast.get_docstring(node):
            self.tasks.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "MISSING_DOCS",
                    "priority": "LOW",
                    "title": f"Add docstring documentation for public function '{node.name}'",
                    "details": f"Function '{node.name}' is exported or public but missing documentation.",
                    "recommendation": f"Add args, returns, and description docstring to '{node.name}'.",
                }
            )

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # 4. Bare except or pass in exception handler
        if node.type is None:
            self.tasks.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "UNHANDLED_EXCEPTION",
                    "priority": "HIGH",
                    "title": "Fix dangerous bare except block",
                    "details": "Bare 'except:' suppresses system interrupts and unexpected errors without logging.",
                    "recommendation": "Catch explicit Exception types and add proper error logging/handling.",
                }
            )
        elif len(node.body) == 1 and isinstance(node.body[0], ast.Pass):
            self.tasks.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "SILENT_EXCEPTION",
                    "priority": "HIGH",
                    "title": "Fix silent exception suppression (except pass)",
                    "details": "Exception block silently ignores caught errors using 'pass'.",
                    "recommendation": "Log caught exceptions or handle fallback recovery logic.",
                }
            )

        self.generic_visit(node)


def _scan_file_for_todos(
    file_path: Path, include_generated: bool
) -> list[dict[str, Any]]:
    """Scan file lines for inline comments and analyze code for task creation."""
    tasks: list[dict[str, Any]] = []

    try:
        with open(file_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        content = "".join(lines)

        # 1. Inline Comment Review (TODO, FIXME, BUG, HACK, etc.)
        for idx, line in enumerate(lines, 1):
            match = INLINE_COMMENT_REGEX.search(line)
            if match:
                tag = match.group(1).upper()
                text = match.group(2).strip() or "No additional comment provided."

                # Assign Priority based on comment tag
                priority = (
                    "HIGH"
                    if tag in ("BUG", "FIXME")
                    else ("MEDIUM" if tag in ("TODO", "HACK") else "LOW")
                )

                tasks.append(
                    {
                        "file": str(file_path),
                        "line": idx,
                        "type": f"INLINE_{tag}",
                        "priority": priority,
                        "title": f"[{tag}] {text[:50]}",
                        "details": text,
                        "recommendation": f"Address inline {tag} marker at line {idx}.",
                    }
                )

            # Hardcoded Endpoint / URL Check
            if include_generated and HARDCODED_URL_REGEX.search(line):
                url_match = HARDCODED_URL_REGEX.search(line)
                url_str = url_match.group(0) if url_match else "URL"
                tasks.append(
                    {
                        "file": str(file_path),
                        "line": idx,
                        "type": "HARDCODED_CONFIG",
                        "priority": "LOW",
                        "title": f"Extract hardcoded URL endpoint '{url_str[:30]}...'",
                        "details": f"Hardcoded URL found in source code: {url_str}",
                        "recommendation": "Extract endpoint URL into environment variables or config file.",
                    }
                )

        # 2. Code Smell Auto-Generation via AST (for Python)
        if include_generated and file_path.suffix == ".py":
            try:
                tree = ast.parse(content, filename=str(file_path))
                analyzer = CodeSmellTodoAnalyzer(str(file_path))
                analyzer.visit(tree)
                tasks.extend(analyzer.tasks)
            except SyntaxError:
                pass

    except Exception as err:
        tasks.append(
            {
                "file": str(file_path),
                "line": 1,
                "type": "FILE_READ_ERROR",
                "priority": "HIGH",
                "title": f"Fix unreadable file '{file_path.name}'",
                "details": f"Error reading file during review: {err}",
                "recommendation": "Verify file permissions and text encoding.",
            }
        )

    return tasks


def _generate_markdown_report(
    tasks: list[dict[str, Any]], target_path: Path, out_file: Path
) -> None:
    """Write structured Markdown TODO roadmap to output file."""
    high_tasks = [t for t in tasks if t.get("priority") == "HIGH"]
    med_tasks = [t for t in tasks if t.get("priority") == "MEDIUM"]
    low_tasks = [t for t in tasks if t.get("priority") == "LOW"]

    lines = [
        "# 📋 Project TODO & Implementation Roadmap",
        f"*Generated on {datetime.now().strftime('%Y-%m-%d %H:%M')} for `{target_path.name}`*",
        "",
        "## Summary Overview",
        f"- **Total Tasks**: {len(tasks):,}",
        f"- **🔴 High Priority**: {len(high_tasks):,}",
        f"- **🟡 Medium Priority**: {len(med_tasks):,}",
        f"- **🟢 Low Priority**: {len(low_tasks):,}",
        "",
    ]

    def _render_task_section(
        section_title: str, task_list: list[dict[str, Any]], icon: str
    ):
        lines.append(f"## {icon} {section_title} ({len(task_list)})")
        if not task_list:
            lines.append("*No items in this category.*\n")
            return

        for idx, task in enumerate(task_list, 1):
            rel_file = task.get("file", "unknown")
            line = task.get("line", 1)
            title = task.get("title", "Task")
            details = task.get("details", "")
            rec = task.get("recommendation", "")
            task_type = task.get("type", "GENERAL")

            lines.append(f"- [ ] **`{rel_file}:{line}`** — **{title}**")
            lines.append(f"  - **Type**: `{task_type}`")
            lines.append(f"  - **Details**: {details}")
            if rec:
                lines.append(f"  - **Action**: {rec}")
            lines.append("")

    _render_task_section("High Priority Tasks", high_tasks, "🔴")
    _render_task_section("Medium Priority Tasks", med_tasks, "🟡")
    _render_task_section("Low Priority Tasks", low_tasks, "🟢")

    out_file.parent.mkdir(parents=True, exist_ok=True)
    out_file.write_text("\n".join(lines), encoding="utf-8")


def execute_tool(
    target: str,
    todo_file: str = "TODO.md",
    min_priority: str = "low",
    write_md: bool = False,
    include_generated: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for scanning files and generating TODO task lists/roadmaps.
    """
    start_time = time.perf_counter()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target path does not exist: {target}",
            "exit_code": 1,
        }

    ignored_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        "venv",
        ".venv",
        "dist",
        "build",
    }
    files_to_scan: list[Path] = []

    if target_path.is_file():
        files_to_scan.append(target_path)
    else:
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [
                d for d in dirs if d not in ignored_dirs and not d.startswith(".")
            ]
            for f in files:
                files_to_scan.append(Path(root) / f)

    all_tasks: list[dict[str, Any]] = []
    for file_p in files_to_scan:
        file_tasks = _scan_file_for_todos(file_p, include_generated=include_generated)
        all_tasks.extend(file_tasks)

    # Priority filtering
    pri_weight = {"HIGH": 30, "MEDIUM": 20, "LOW": 10}
    min_pri_str = min_priority.upper()
    min_weight = pri_weight.get(min_pri_str, 0)

    filtered_tasks = [
        t
        for t in all_tasks
        if pri_weight.get(t.get("priority", "LOW"), 0) >= min_weight
    ]

    # Count tasks by priority
    high_count = sum(1 for t in filtered_tasks if t.get("priority") == "HIGH")
    medium_count = sum(1 for t in filtered_tasks if t.get("priority") == "MEDIUM")
    low_count = sum(1 for t in filtered_tasks if t.get("priority") == "LOW")

    written_dest = None
    if write_md:
        out_md_path = Path(todo_file).expanduser().resolve()
        _generate_markdown_report(filtered_tasks, target_path, out_md_path)
        written_dest = str(out_md_path)

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "success": True,
        "target": str(target_path),
        "files_scanned": len(files_to_scan),
        "total_tasks": len(filtered_tasks),
        "high_count": high_count,
        "medium_count": medium_count,
        "low_count": low_count,
        "written_file": written_dest,
        "tasks": filtered_tasks,
        "duration_ms": duration_ms,
        "exit_code": 0,
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
    target: str,
    todo_file: str = "TODO.md",
    min_priority: str = "low",
    write_md: bool = False,
    include_generated: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        target=target,
        todo_file=todo_file,
        min_priority=min_priority,
        write_md=write_md,
        include_generated=include_generated,
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
        prog="todo_reviewer.py",
        description=f"AIChat TODO Review & Task Generator Tool v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        required=True,
        metavar="PATH",
        help="Target file or directory path to review (required)",
    )
    parser.add_argument(
        "--todo-file",
        default="TODO.md",
        dest="todo_file",
        metavar="PATH",
        help="Destination path for generated Markdown TODO report (default: TODO.md)",
    )
    parser.add_argument(
        "--min-priority",
        choices=["high", "medium", "low"],
        default="low",
        dest="min_priority",
        help="Minimum priority filter: high, medium, low (default: low)",
    )
    parser.add_argument(
        "--write-md",
        action="store_true",
        default=False,
        dest="write_md",
        help="Write/update structured TODO.md file on disk",
    )
    parser.add_argument(
        "--include-generated",
        action="store_true",
        default=False,
        dest="include_generated",
        help="Auto-detect code smells and generate actionable TODO tasks alongside existing comments",
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
        target=args.target,
        todo_file=args.todo_file,
        min_priority=args.min_priority,
        write_md=args.write_md,
        include_generated=args.include_generated,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
