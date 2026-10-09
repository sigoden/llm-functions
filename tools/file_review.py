#!/usr/bin/env python3
# ==============================================================================
# file_reviewer.py — Pyrmethus AIChat Tool Template v1.2.0 (Wizard Upgraded)
# argc/aichat compatible · Human-Readable Colorized Outputs
#
# @describe Audits files and codebases for syntax issues, security secrets, TODOs, whitespace, file health, and modern code improvement/refactoring opportunities.
#
# @option --target! <PATH>               Target file or directory path to review (required)
# @option --checks <CHECKS>              Comma-separated checks: syntax,secrets,todos,whitespace,size,upgrades (default: all)
# @option --max-file-size <BYTES>        Flag files exceeding size limit in bytes (default: 1048576)
# @flag   --fix                          Automatically trim trailing whitespace where applicable
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
from pathlib import Path
from typing import Any

__version__ = "1.2.0"

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
    """Return True if stdout is attached to an interactive terminal and NO_COLOR is not set."""
    if "NO_COLOR" in os.environ:
        return False
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
        f"{NEON_PURPLE}│{RESET} {NEON_PINK}⚡ [FILE & CODE UPGRADE REVIEWER]{RESET} {status_color}{BOLD}{status_symbol} {status_text}{RESET}"
    )
    _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Target:{RESET}         {data.get('target', 'N/A')}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Files Reviewed:{RESET} {NEON_YELLOW}{data.get('files_reviewed', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Issues Found:{RESET}   {NEON_RED}{data.get('total_issues', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Upgrades/Tips:{RESET}  {NEON_GREEN}{data.get('total_upgrades', 0):,}{RESET}"
    )
    _cprint(
        f"{NEON_PURPLE}│{RESET} {NEON_CYAN}Duration:{RESET}       {DIM}{data.get('duration_ms', 0)}ms{RESET}"
    )

    if not success and "error" in data:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {NEON_RED}Error:{RESET}          {data['error']}"
        )

    findings = data.get("findings", [])
    if findings:
        _cprint(f"{NEON_PURPLE}├{border}┤{RESET}")
        _cprint(
            f"{NEON_PURPLE}│{RESET} {BOLD}Audit & Upgrade Findings ({len(findings)}):{RESET}"
        )
        for issue in findings[:10]:
            sev = issue.get("severity", "INFO")
            if sev == "HIGH":
                sev_color = NEON_RED
            elif sev == "MEDIUM":
                sev_color = NEON_YELLOW
            elif sev == "UPGRADE":
                sev_color = NEON_GREEN
            else:
                sev_color = NEON_CYAN

            rel_file = issue.get("file", "")
            try:
                rel_path = str(Path(rel_file).relative_to(Path.cwd()))
            except ValueError:
                rel_path = rel_file

            if len(rel_path) > 28:
                rel_path = "..." + rel_path[-25:]

            _cprint(
                f"{NEON_PURPLE}│{RESET}   {sev_color}[{sev:<7}]{RESET} {rel_path}:{issue.get('line', 0)} — {issue.get('message')}"
            )
        if len(findings) > 10:
            _cprint(
                f"{NEON_PURPLE}│{RESET}   {DIM}... and {len(findings) - 10} more findings{RESET}"
            )

    _cprint(f"{NEON_PURPLE}╰{border}╯{RESET}")


# ==============================================================================
# SECTION 2: Core Logic Implementation & Compiled Patterns
# ==============================================================================

SECRET_PATTERNS = [
    (
        re.compile(
            r"(?i)(api[_-]?key|secret[_-]?key|auth[_-]?token)\s*[:=]\s*['\"]([a-zA-Z0-9_\-]{16,})['\"]"
        ),
        "Potential API Secret Key",
    ),
    (re.compile(r"-----BEGIN (RSA|OPENSSH|PRIVATE) KEY-----"), "Private RSA/SSH Key"),
    (re.compile(r"AKIA[0-9A-Z]{16}"), "AWS Access Key ID"),
    (
        re.compile(r"(ghp_[a-zA-Z0-9]{36}|github_pat_[a-zA-Z0-9]{22}_[a-zA-Z0-9]{59})"),
        "GitHub Access Token",
    ),
    (
        re.compile(
            r"https://hooks\.slack\.com/services/T[a-zA-Z0-9_]+/B[a-zA-Z0-9_]+/[a-zA-Z0-9_]+"
        ),
        "Slack Webhook URL",
    ),
    (
        re.compile(r"(sk-[a-zA-Z0-9]{32,}|sk-ant-api03-[a-zA-Z0-9_\-]{80,})"),
        "OpenAI/Anthropic Secret Key",
    ),
    (
        re.compile(
            r"eyJ[a-zA-Z0-9_\-]{10,}\.eyJ[a-zA-Z0-9_\-]{10,}\.[a-zA-Z0-9_\-]{10,}"
        ),
        "JSON Web Token (JWT)",
    ),
]

# Pre-compiled Code Analysis Patterns
RE_PYTHONIC_LEN = re.compile(r"\bif\s+len\([^)]+\)\s*(==\s*0|>=\s*1|>0)\b")
RE_LEGACY_STR = re.compile(r"(['\"].*%[sdfr].*['\"]\s*%\s*\(|\.format\()")
RE_JS_VAR = re.compile(r"\bvar\s+[a-zA-Z0-9_$]+\s*=")
RE_TODO_TAG = re.compile(r"\b(TODO|FIXME|HACK|XXX)\b\s*[:|-]?\s*(.*)", re.IGNORECASE)


class ASTUpgradeAnalyzer(ast.NodeVisitor):
    """AST Node Visitor for detecting Python code modernization & upgrade opportunities."""

    def __init__(self, file_path: str):
        self.file_path = file_path
        self.findings: list[dict[str, Any]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        # 1. Mutable Default Arguments Check
        all_defaults = list(node.args.defaults) + [
            d for d in node.args.kw_defaults if d is not None
        ]
        for default in all_defaults:
            if isinstance(default, (ast.List, ast.Dict, ast.Set)):
                self.findings.append(
                    {
                        "file": self.file_path,
                        "line": node.lineno,
                        "type": "MUTABLE_DEFAULT_ARGUMENT",
                        "severity": "MEDIUM",
                        "message": f"Function '{node.name}' uses mutable default argument. Consider 'None' default.",
                    }
                )

        # 2. Missing Docstring Check for Public Functions
        if not node.name.startswith("_") and not ast.get_docstring(node):
            self.findings.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "MISSING_DOCSTRING",
                    "severity": "UPGRADE",
                    "message": f"Public function '{node.name}' is missing a docstring.",
                }
            )

        # 3. Missing Return Type Annotation Check
        if not node.name.startswith("_") and node.returns is None:
            self.findings.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "MISSING_TYPE_ANNOTATION",
                    "severity": "UPGRADE",
                    "message": f"Public function '{node.name}' is missing a return type annotation.",
                }
            )

        self.generic_visit(node)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        # 4. Bare Except Clause Check
        if node.type is None:
            self.findings.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "BARE_EXCEPT_CLAUSE",
                    "severity": "MEDIUM",
                    "message": "Bare 'except:' caught. Catch explicit exceptions or 'except Exception:' instead.",
                }
            )
        self.generic_visit(node)

    def visit_Compare(self, node: ast.Compare) -> None:
        # 5. type(x) == Y or type(x) is Y instead of isinstance(x, Y)
        if len(node.ops) == 1 and isinstance(node.ops[0], (ast.Eq, ast.Is)):
            if (
                isinstance(node.left, ast.Call)
                and isinstance(node.left.func, ast.Name)
                and node.left.func.id == "type"
            ):
                self.findings.append(
                    {
                        "file": self.file_path,
                        "line": node.lineno,
                        "type": "PREFER_ISINSTANCE",
                        "severity": "UPGRADE",
                        "message": "Use 'isinstance(x, Type)' instead of comparing 'type(x) == Type'.",
                    }
                )
        self.generic_visit(node)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        # 6. Wildcard Import Check
        for alias in node.names:
            if alias.name == "*":
                self.findings.append(
                    {
                        "file": self.file_path,
                        "line": node.lineno,
                        "type": "WILDCARD_IMPORT",
                        "severity": "MEDIUM",
                        "message": f"Wildcard import 'from {node.module} import *' detected. Import specific symbols.",
                    }
                )
        self.generic_visit(node)

    def visit_Call(self, node: ast.Call) -> None:
        # 7. Unsafe Dynamic Code Execution Check
        if isinstance(node.func, ast.Name) and node.func.id in ("eval", "exec"):
            self.findings.append(
                {
                    "file": self.file_path,
                    "line": node.lineno,
                    "type": "UNSAFE_DYNAMIC_EXECUTION",
                    "severity": "HIGH",
                    "message": f"Use of dynamic execution '{node.func.id}()' detected. High security risk.",
                }
            )
        self.generic_visit(node)


def _is_binary_file(file_path: Path) -> bool:
    """Fast check to determine if a file is binary using null-byte inspection."""
    try:
        with open(file_path, "rb") as f:
            chunk = f.read(1024)
            return b"\0" in chunk
    except OSError:
        return False


def _review_file(
    file_path: Path,
    enabled_checks: set[str],
    max_file_size: int,
    auto_fix: bool,
    verbose: bool = False,
) -> list[dict[str, Any]]:
    """Inspect a single file for enabled review and upgrade checks."""
    issues: list[dict[str, Any]] = []

    if verbose:
        sys.stderr.write(f"[DEBUG] Reviewing file: {file_path}\n")

    try:
        size = file_path.stat().st_size
        if "size" in enabled_checks and size > max_file_size:
            issues.append(
                {
                    "file": str(file_path),
                    "line": 1,
                    "type": "FILE_SIZE",
                    "severity": "MEDIUM",
                    "message": f"File size ({size:,} bytes) exceeds limit ({max_file_size:,} bytes)",
                }
            )

        if _is_binary_file(file_path):
            if verbose:
                sys.stderr.write(f"[DEBUG] Skipping binary file: {file_path}\n")
            return issues

        with open(file_path, encoding="utf-8", errors="replace") as f:
            lines = f.readlines()

        # ----------------------------------------------------------------------
        # Check 1: Syntax & AST Analysis
        # ----------------------------------------------------------------------
        if "syntax" in enabled_checks:
            content = "".join(lines)
            if file_path.suffix == ".py":
                try:
                    tree = ast.parse(content, filename=str(file_path))
                    if "upgrades" in enabled_checks:
                        analyzer = ASTUpgradeAnalyzer(str(file_path))
                        analyzer.visit(tree)
                        issues.extend(analyzer.findings)
                except SyntaxError as syn_err:
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": syn_err.lineno or 1,
                            "type": "SYNTAX_ERROR",
                            "severity": "HIGH",
                            "message": f"Python SyntaxError: {syn_err.msg}",
                        }
                    )
            elif file_path.suffix == ".json":
                try:
                    json.loads(content)
                except json.JSONDecodeError as json_err:
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": json_err.lineno,
                            "type": "SYNTAX_ERROR",
                            "severity": "HIGH",
                            "message": f"JSON SyntaxError: {json_err.msg}",
                        }
                    )

        # ----------------------------------------------------------------------
        # Checks 2-5: Single-Pass Line Auditing
        # ----------------------------------------------------------------------
        check_secrets = "secrets" in enabled_checks
        check_todos = "todos" in enabled_checks
        check_upgrades = "upgrades" in enabled_checks
        check_whitespace = "whitespace" in enabled_checks

        is_py = file_path.suffix == ".py"
        is_js_ts = file_path.suffix in (".js", ".ts", ".jsx", ".tsx")

        modified = False
        new_lines: list[str] = []

        for idx, line in enumerate(lines, 1):
            if check_secrets:
                for regex, desc in SECRET_PATTERNS:
                    if regex.search(line):
                        issues.append(
                            {
                                "file": str(file_path),
                                "line": idx,
                                "type": "HARDCODED_SECRET",
                                "severity": "HIGH",
                                "message": desc,
                            }
                        )

            if check_todos:
                match = RE_TODO_TAG.search(line)
                if match:
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": idx,
                            "type": "TODO_FOUND",
                            "severity": "LOW",
                            "message": f"{match.group(1).upper()}: {match.group(2).strip()[:40]}",
                        }
                    )

            if check_upgrades:
                if RE_PYTHONIC_LEN.search(line):
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": idx,
                            "type": "PYTHONIC_CONTAINER_CHECK",
                            "severity": "UPGRADE",
                            "message": "Simplify 'if len(x) == 0' to 'if not x' or 'if x'.",
                        }
                    )

                if is_py and RE_LEGACY_STR.search(line):
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": idx,
                            "type": "LEGACY_STRING_FORMATTING",
                            "severity": "UPGRADE",
                            "message": "Upgrade legacy % or .format() to modern Python f-strings.",
                        }
                    )

                if is_js_ts and RE_JS_VAR.search(line):
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": idx,
                            "type": "JS_VAR_TO_LET_CONST",
                            "severity": "UPGRADE",
                            "message": "Upgrade legacy 'var' declaration to 'let' or 'const'.",
                        }
                    )

            if check_whitespace:
                stripped_eol = line.rstrip("\r\n")
                if stripped_eol and stripped_eol[-1:] in (" ", "\t"):
                    issues.append(
                        {
                            "file": str(file_path),
                            "line": idx,
                            "type": "TRAILING_WHITESPACE",
                            "severity": "LOW",
                            "message": "Trailing whitespace detected",
                        }
                    )
                    if auto_fix:
                        eol = line[len(stripped_eol) :]
                        new_lines.append(stripped_eol.rstrip(" \t") + eol)
                        modified = True
                    else:
                        new_lines.append(line)
                else:
                    new_lines.append(line)
            else:
                new_lines.append(line)

        # ----------------------------------------------------------------------
        # Whitespace Auto-Fix Atomic File Save
        # ----------------------------------------------------------------------
        if auto_fix and modified and check_whitespace:
            tmp_path = file_path.with_name(f".{file_path.name}.tmp")
            try:
                with open(tmp_path, "w", encoding="utf-8") as f:
                    f.writelines(new_lines)
                os.replace(tmp_path, file_path)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass

    except Exception as err:
        issues.append(
            {
                "file": str(file_path),
                "line": 1,
                "type": "READ_ERROR",
                "severity": "HIGH",
                "message": f"Unable to review file: {err}",
            }
        )

    return issues


def execute_tool(
    target: str,
    checks: str | None = "all",
    max_file_size: int | str | None = 1048576,
    fix: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> dict[str, Any]:
    """
    Core execution logic for reviewing files and directories for defects and upgrade suggestions.
    """
    start_time = time.perf_counter()
    target_path = Path(target).expanduser().resolve()

    if not target_path.exists():
        return {
            "success": False,
            "error": f"Target path does not exist: {target}",
            "exit_code": 1,
        }

    all_checks = {"syntax", "secrets", "todos", "whitespace", "size", "upgrades"}
    checks_str = str(checks) if checks is not None else "all"
    if checks_str and checks_str.lower() != "all":
        enabled_checks = {c.strip().lower() for c in checks_str.split(",")}
    else:
        enabled_checks = all_checks

    try:
        max_size = int(max_file_size) if max_file_size is not None else 1048576
    except (ValueError, TypeError):
        max_size = 1048576

    files_to_review: list[Path] = []
    ignored_dirs = {
        ".git",
        "node_modules",
        "__pycache__",
        "venv",
        ".venv",
        ".mypy_cache",
        ".pytest_cache",
    }

    if target_path.is_file():
        files_to_review.append(target_path)
    else:
        for root, dirs, files in os.walk(target_path):
            dirs[:] = [
                d for d in dirs if d not in ignored_dirs and not d.startswith(".")
            ]
            for f in files:
                if not f.startswith(".") or f in (
                    ".env",
                    ".env.local",
                    ".env.production",
                ):
                    files_to_review.append(Path(root) / f)

    if verbose:
        sys.stderr.write(
            f"[DEBUG] Found {len(files_to_review)} candidate file(s) to review.\n"
        )

    all_findings: list[dict[str, Any]] = []
    for file_p in files_to_review:
        file_issues = _review_file(file_p, enabled_checks, max_size, fix, verbose)
        all_findings.extend(file_issues)

    total_upgrades = sum(
        1 for item in all_findings if item.get("severity") == "UPGRADE"
    )
    total_defects = len(all_findings) - total_upgrades

    duration_ms = round((time.perf_counter() - start_time) * 1000, 2)

    return {
        "success": True,
        "target": str(target_path),
        "files_reviewed": len(files_to_review),
        "total_issues": total_defects,
        "total_upgrades": total_upgrades,
        "findings": all_findings,
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
        try:
            if hasattr(sys.stdout, "buffer"):
                sys.stdout.buffer.write(json_payload.encode("utf-8"))
                sys.stdout.buffer.flush()
            else:
                sys.stdout.write(json_payload)
                sys.stdout.flush()
        except UnicodeEncodeError:
            sys.stdout.write(json.dumps(data, indent=2, ensure_ascii=True) + "\n")
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
    checks: str | None = "all",
    max_file_size: int | str | None = 1048576,
    fix: bool = False,
    no_color: bool = False,
    verbose: bool = False,
) -> None:
    """
    AIChat Programmatic Entrypoint.
    Parameter names match option/flag slugs (with underscores).
    """
    result = execute_tool(
        target=target,
        checks=checks,
        max_file_size=max_file_size,
        fix=fix,
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
        prog="file_reviewer.py",
        description=f"AIChat File Review & Code Upgrade Auditor v{__version__}",
    )
    parser.add_argument(
        "--target",
        "-t",
        required=True,
        metavar="PATH",
        help="Target file or directory path to review (required)",
    )
    parser.add_argument(
        "--checks",
        default="all",
        help="Comma-separated checks: syntax,secrets,todos,whitespace,size,upgrades (default: all)",
    )
    parser.add_argument(
        "--max-file-size",
        type=int,
        default=1048576,
        dest="max_file_size",
        help="Flag files exceeding size limit in bytes (default: 1048576)",
    )
    parser.add_argument(
        "--fix",
        action="store_true",
        default=False,
        help="Automatically trim trailing whitespace where applicable",
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
        checks=args.checks,
        max_file_size=args.max_file_size,
        fix=args.fix,
        no_color=args.no_color,
        verbose=args.verbose,
    )

    print_human_readable_ui(res, no_color=args.no_color)
    write_llm_output(res)
    sys.exit(res.get("exit_code", 0))
