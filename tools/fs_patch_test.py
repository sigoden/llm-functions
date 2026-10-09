#!/usr/bin/env python3
# =============================================================================
# patch.py — CPatch File Weaver v1.4.0
# argc/aichat compatible · Secure Sandbox · ANSI Colored Diffs
#
# @describe Patch, diff, read, write, and text replacement tool with fuzzy LLM-resilient patching.
#
# @option --operation! <OP>               Operation to perform (read, diff, patch, replace, write)
# @option --file-path <PATH>              Primary target file path
# @option --patch-path <PATH>             Path to the patch file (for patch op)
# @option --target-path <PATH>            Secondary file to compare against (for diff op)
# @option --search-text <TEXT>            Text to search for (for replace op)
# @option --replacement <TEXT>            Replacement text (for replace op)
# @option --content <TEXT>                Content to write (for write op)
# @option --start-line <NUM>              Start line (for read op, 1-based)
# @option --end-line <NUM>                End line (for read op, 1-based)
# @option --encoding <ENC>                File encoding (default: utf-8)
# @option --max-size <NUM>                Max read/write size in bytes (default: 100MB)
# @option --max-backups <NUM>             Maximum backup count (default: 15)
# @flag   --append                        Append content instead of overwriting (for write op)
# @flag   --no-lines                      Hide line numbers in read output
# @flag   --use-regex                     Use regex for replacement matching
# @flag   --no-global                     Only replace first occurrence
# @flag   --case-insensitive              Case-insensitive text replacement
# @flag   --no-backup                     Disable backup creation
# @flag   --dry-run                       Preview changes and diffs without saving
# @flag   --verbose                       Enable verbose/debug logging
#
# @env LLM_OUTPUT=/dev/fd/1              Output path for LLM integration
# =============================================================================

from __future__ import annotations

import argparse
import difflib
import functools
import json
import logging
import os
import re
import shutil
import sys
import tempfile
import time
from pathlib import Path
from typing import Any, Callable, Dict, List, Tuple

# ═════════════════════════════════════════════════════════════════════════════
# CONFIGURATION & OPTIONS
# ════════════════════════════════════════════════════════════════════════════


class PatchOptions(dict):
    """Simple dict-subclass that supports attribute access with defaults."""

    DEFAULTS = {
        "operation": None,
        "file_path": None,
        "patch_path": None,
        "target_path": None,
        "search_text": None,
        "replacement": None,
        "content": None,
        "use_regex": False,
        "global_replace": True,
        "case_sensitive": True,
        "start_line": None,
        "end_line": None,
        "show_lines": True,
        "encoding": "utf-8",
        "max_size": 104857600,
        "max_backups": 15,
        "append": False,
        "no_backup": False,
        "dry_run": False,
        "verbose": False,
    }

    def __init__(self, **kwargs: Any):
        data = self.DEFAULTS.copy()
        data.update(kwargs)
        super().__init__(data)

    __getattr__ = dict.get
    __setattr__ = dict.__setitem__
    __delattr__ = dict.__delitem__


__all__ = ["diff", "patch", "read", "replace", "run", "write"]
__version__ = "1.4.0"

# ════════════════════════════════════════════════════════════════════════════
# COLORS & LOGGING
# ════════════════════════════════════════════════════════════════════════════

logger = logging.getLogger(__name__)

try:
    from colorama import Fore, Style
    from colorama import init as colorama_init

    colorama_init(autoreset=True)
except ImportError:

    class _DummyColor:
        def __getattr__(self, name: str) -> str:
            return ""

    Fore = _DummyColor()  # type: ignore
    Style = _DummyColor()  # type: ignore

RESET = "\033[0m"
RED = "\033[31m"
GREEN = "\033[32m"
CYAN = "\033[36m"
BOLD_CYAN = "\033[1;36m"
DIM = "\033[2m"
YELLOW = "\033[33m"


def _cprint(text: str, color: str = CYAN, style: str = "", file=sys.stdout) -> None:
    """Print with ANSI escape colors."""
    if file.isatty() or os.environ.get("TERM") or os.environ.get("COLORTERM"):
        print(f"{style}{color}{text}{RESET}", file=file)
    else:
        print(text, file=file)


def print_colored_diff(diff_text: str, file=sys.stderr) -> None:
    """Prints a unified diff string with syntax highlighting."""
    use_color = file.isatty() or os.environ.get("TERM") or os.environ.get("COLORTERM")
    for line in diff_text.splitlines(keepends=True):
        if not use_color:
            file.write(line)
            continue
        if line.startswith("+++") or line.startswith("---"):
            file.write(f"{BOLD_CYAN}{line}{RESET}")
        elif line.startswith("@@"):
            file.write(f"{CYAN}{line}{RESET}")
        elif line.startswith("+"):
            file.write(f"{GREEN}{line}{RESET}")
        elif line.startswith("-"):
            file.write(f"{RED}{line}{RESET}")
        elif line.startswith("\\"):
            file.write(f"{DIM}{line}{RESET}")
        else:
            file.write(line)
    file.flush()


def _format_size(size_bytes: int | float) -> str:
    """Format bytes into human-readable strings."""
    if size_bytes < 1024:
        return f"{int(size_bytes)} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        size_bytes /= 1024.0
        if size_bytes < 1024.0:
            return f"{size_bytes:.1f} {unit}"
    return f"{size_bytes:.1f} PB"


# ═════════════════════════════════════════════════════════════════════════════
# SANDBOX CORE
# ═════════════════════════════════════════════════════════════════════════════


class FileEditor:
    """Secure sandboxed file editor ensuring path resolution safety and atomic writes."""

    def __init__(self) -> None:
        self.home: Path = Path.home().resolve()
        self.temp: Path = Path(tempfile.gettempdir()).resolve()

    def _validate_path(self, file_path: str, allow_write: bool = True) -> Path | None:
        if not file_path or "\x00" in file_path:
            return None
        try:
            path = Path(file_path).expanduser().resolve(strict=False)
        except (ValueError, OSError):
            return None
        if allow_write:
            parent = path.parent
            if not parent.exists():
                try:
                    parent.mkdir(parents=True, exist_ok=True)
                except (OSError, PermissionError) as e:
                    logger.debug(f"Failed to create directory {parent}: {e}")
                    return None
        elif path.exists() and not os.access(path, os.R_OK):
            return None
        return path

    def _atomic_write(self, path: Path, content: str, encoding: str = "utf-8") -> None:
        if not isinstance(content, str):
            content = str(content)
        logger.debug(f"Atomically writing to {path}")
        fd, tmp_name = tempfile.mkstemp(dir=path.parent, prefix=".~tmp_")
        try:
            if path.exists():
                try:
                    shutil.copymode(path, tmp_name)
                except OSError:
                    os.chmod(tmp_name, 0o644)
            else:
                os.chmod(tmp_name, 0o644)
            with os.fdopen(fd, "w", encoding=encoding) as f:
                f.write(content)
                f.flush()
                os.fsync(f.fileno())
            os.replace(tmp_name, str(path))
        except Exception:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise

    def _make_backup(self, path: Path, max_backups: int) -> Path | None:
        if not path.exists():
            logger.debug(f"No backup created; file does not exist: {path}")
            return None
        ts = time.time_ns()
        backup = path.parent / f"{path.stem}{path.suffix}.{ts}.bak"
        shutil.copy2(path, backup)
        logger.debug(f"Created backup: {backup}")
        import glob

        pattern = f"{glob.escape(path.stem)}{glob.escape(path.suffix)}.*.bak"
        backups = sorted(path.parent.glob(pattern), key=lambda p: p.stat().st_mtime_ns)
        while len(backups) > max_backups:
            backups.pop(0).unlink(missing_ok=True)
        return backup

    def _read_content(self, file_path: str, encoding: str = "utf-8") -> Dict[str, Any]:
        path = self._validate_path(file_path, allow_write=False)
        if not path or not path.exists():
            return {
                "success": False,
                "error": f"File not found or invalid: {file_path}",
            }
        encodings_to_try = list(
            dict.fromkeys([encoding, "utf-8", "utf-8-sig", "latin-1", "cp1252"])
        )
        for enc in encodings_to_try:
            try:
                content = path.read_text(encoding=enc)
                return {
                    "success": True,
                    "content": content,
                    "path": path,
                    "encoding_used": enc,
                }
            except UnicodeDecodeError:
                continue
            except Exception as exc:
                return {"success": False, "error": str(exc)}
        try:
            content = path.read_text(encoding=encoding, errors="surrogateescape")
            return {
                "success": True,
                "content": content,
                "path": path,
                "encoding_used": f"{encoding} (surrogateescape)",
            }
        except Exception as exc:
            return {"success": False, "error": str(exc)}


_editor = FileEditor()


def _timed(fn: Callable[..., Dict[str, Any]]) -> Callable[..., Dict[str, Any]]:
    @functools.wraps(fn)
    def wrapper(*args: Any, **kwargs: Any) -> Dict[str, Any]:
        t0 = time.perf_counter()
        res = fn(*args, **kwargs)
        if not isinstance(res, dict):
            res = {"success": False, "error": "Operation returned non-dict"}
        res["duration_ms"] = round((time.perf_counter() - t0) * 1000, 3)
        return res

    return wrapper


# ════════════════════════════════════════════════════════════════════════════
# OPERATIONS
# ════════════════════════════════════════════════════════════════════════════


@_timed
def read(options: PatchOptions) -> Dict[str, Any]:
    """Read a file (or subset of lines) and output it with colored line numbers."""
    res = _editor._read_content(options.file_path, options.encoding)
    if not res["success"]:
        return res
    path: Path = res["path"]
    content: str = res["content"]
    file_size = path.stat().st_size
    if file_size > options.max_size:
        return {
            "success": False,
            "error": f"File too large: exceeds {options.max_size} bytes limit",
        }
    all_lines = content.splitlines(keepends=False)
    total_lines = len(all_lines)
    if total_lines == 0:
        selected_lines = []
        start_line = 0
        end_line = 0
    else:
        start_line = options.start_line if options.start_line is not None else 1
        end_line = options.end_line if options.end_line is not None else total_lines
        start_line = max(1, start_line)
        end_line = min(total_lines, max(start_line, end_line))
        if start_line > total_lines:
            return {
                "success": False,
                "error": f"start_line {start_line} is beyond file length ({total_lines})",
            }
        selected_lines = all_lines[start_line - 1 : end_line]
    if options.show_lines and selected_lines:
        _cprint(
            f"\n[READ] {path.name} (Lines {start_line}-{end_line}):",
            Fore.CYAN,
            file=sys.stderr,
        )
        pad = len(str(end_line))
        for i, line in enumerate(selected_lines, start=start_line):
            line_num_str = f"{i:>{pad}}"
            sys.stderr.write(f"{DIM}{line_num_str} | {RESET}{line}\n")
        sys.stderr.flush()
    result = {
        "success": True,
        "path": str(path),
        "content": "\n".join(selected_lines),
        "total_lines": total_lines,
        "start_line": start_line,
        "end_line": end_line,
        "size": file_size,
        "size_fmt": _format_size(file_size),
        "encoding": res.get("encoding_used"),
    }
    if options.show_lines:
        result["lines"] = selected_lines
    return result


def _apply_unified_patch(
    source_lines: List[str], patch_lines: List[str]
) -> Tuple[List[str], int, int]:
    hunks = []
    current_hunk = None
    for line in patch_lines:
        if line.startswith("---") or line.startswith("+++"):
            continue
        m = re.match(r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@", line)
        if m:
            if current_hunk:
                hunks.append(current_hunk)
            current_hunk = {
                "hint_line": max(0, int(m.group(1)) - 1),
                "expected_old": [],
                "new_lines": [],
            }
            continue
        if current_hunk is not None:
            if line.startswith(" "):
                current_hunk["expected_old"].append(line[1:])
                current_hunk["new_lines"].append(line[1:])
            elif line.startswith("-"):
                current_hunk["expected_old"].append(line[1:])
            elif line.startswith("+"):
                current_hunk["new_lines"].append(line[1:])
            elif line.startswith("\\ No newline"):
                if current_hunk["new_lines"] and current_hunk["new_lines"][-1].endswith(
                    "\n"
                ):
                    current_hunk["new_lines"][-1] = current_hunk["new_lines"][
                        -1
                    ].rstrip("\r\n")
                if current_hunk["expected_old"] and current_hunk["expected_old"][
                    -1
                ].endswith("\n"):
                    current_hunk["expected_old"][-1] = current_hunk["expected_old"][
                        -1
                    ].rstrip("\r\n")
    if current_hunk:
        hunks.append(current_hunk)
    output_lines = source_lines[:]
    applied = 0
    failed = 0
    offset = 0
    for i, hunk in enumerate(hunks):
        expected = hunk["expected_old"]
        new_lines = hunk["new_lines"]
        hint = max(0, hunk["hint_line"] + offset)
        expected_stripped = [exp_line.rstrip() for exp_line in expected]

        def check_match(idx: int) -> bool:
            if idx < 0 or idx + len(expected) > len(output_lines):
                return False
            for j, exp_strip in enumerate(expected_stripped):
                if output_lines[idx + j].rstrip() != exp_strip:
                    return False
            return True

        search_radius = max(1000, len(output_lines))
        found_idx = -1
        for d in range(search_radius):
            if check_match(hint + d):
                found_idx = hint + d
                break
            if d > 0 and check_match(hint - d):
                found_idx = hint - d
                break
        if found_idx != -1:
            output_lines[found_idx : found_idx + len(expected)] = new_lines
            offset += len(new_lines) - len(expected)
            applied += 1
        else:
            _cprint(
                f"Warning: Hunk #{i + 1} (around line {hunk['hint_line'] + 1}) failed to apply cleanly.",
                Fore.YELLOW,
                file=sys.stderr,
            )
            if expected:
                _cprint(
                    f"  Expected context start: {expected[0].strip()}",
                    Fore.DIM,
                    file=sys.stderr,
                )
            failed += 1
    return output_lines, applied, failed


@_timed
def diff(options: PatchOptions) -> Dict[str, Any]:
    """Generate colored unified diff between two files."""
    if not options.target_path:
        return {"success": False, "error": "target_path is required for diff"}
    res1 = _editor._read_content(options.file_path, options.encoding)
    if not res1["success"]:
        return res1
    res2 = _editor._read_content(options.target_path, options.encoding)
    if not res2["success"]:
        return res2
    lines1 = res1["content"].splitlines(keepends=True)
    lines2 = res2["content"].splitlines(keepends=True)
    diff_lines = list(
        difflib.unified_diff(
            lines1, lines2, fromfile=options.file_path, tofile=options.target_path, n=3
        )
    )
    diff_text = "\n".join(diff_lines)
    if diff_text:
        _cprint(
            f"\n[DIFF] {options.file_path} -> {options.target_path}",
            Fore.CYAN,
            file=sys.stderr,
        )
        print_colored_diff(diff_text, file=sys.stderr)
    else:
        _cprint("Files are identical.", Fore.YELLOW, file=sys.stderr)
    return {
        "success": True,
        "path": options.file_path,
        "target": options.target_path,
        "diff": diff_text,
        "changed": bool(diff_text),
    }


@_timed
def patch(options: PatchOptions) -> Dict[str, Any]:
    """Apply a unified diff patch to a file."""
    if not options.patch_path:
        return {"success": False, "error": "patch_path is required for patch"}
    res1 = _editor._read_content(options.file_path, options.encoding)
    if not res1["success"]:
        return res1
    res2 = _editor._read_content(options.patch_path, options.encoding)
    if not res2["success"]:
        return res2
    path: Path = res1["path"]
    source_lines = res1["content"].splitlines(keepends=True)
    patch_lines = res2["content"].splitlines(keepends=True)
    patched_lines, applied, failed = _apply_unified_patch(source_lines, patch_lines)
    new_content = "\n".join(patched_lines)
    diff_lines = list(
        difflib.unified_diff(
            source_lines, patched_lines, fromfile=str(path), tofile=str(path), n=3
        )
    )
    diff_text = "\n".join(diff_lines)
    if diff_text:
        _cprint(
            f"\n[DIFF] Resulting changes for {path.name}:", Fore.CYAN, file=sys.stderr
        )
        print_colored_diff(diff_text, file=sys.stderr)
    if options.dry_run:
        return {
            "success": True,
            "path": str(path),
            "mode": "dry-run",
            "hunks_applied": applied,
            "hunks_failed": failed,
            "diff": diff_text,
        }
    if failed > 0:
        return {
            "success": False,
            "error": f"{failed} hunk(s) failed to apply cleanly. Aborting.",
        }
    backup = None
    if not options.no_backup:
        backup = _editor._make_backup(path, options.max_backups)
    _editor._atomic_write(path, new_content, options.encoding)
    new_size = path.stat().st_size
    return {
        "success": True,
        "path": str(path),
        "hunks_applied": applied,
        "backup_path": str(backup) if backup else None,
        "size": new_size,
        "size_fmt": _format_size(new_size),
    }


@_timed
def replace(options: PatchOptions) -> Dict[str, Any]:
    """Replace text or regex in a file and show colored diff."""
    if not options.search_text:
        return {"success": False, "error": "search_text is required"}
    res = _editor._read_content(options.file_path, options.encoding)
    if not res["success"]:
        return res
    content: str = res["content"].replace("\r\n", "\n").replace("\r", "\n")
    path: Path = res["path"]
    search_text = str(options.search_text)
    replacement = str(options.replacement or "")
    try:
        flags = 0 if options.case_sensitive else re.IGNORECASE
        if options.use_regex:
            flags |= re.MULTILINE
            compiled = re.compile(search_text, flags)
        else:
            compiled = re.compile(re.escape(search_text), flags)
            replacement = replacement.replace("\\", r"\\")
        if options.global_replace:
            new_content, count = compiled.subn(replacement, content)
        else:
            new_content, count = compiled.subn(replacement, content, count=1)
            if new_content == content:
                count = 0
        source_lines = content.splitlines(keepends=True)
        patched_lines = new_content.splitlines(keepends=True)
        diff_lines = list(
            difflib.unified_diff(
                source_lines, patched_lines, fromfile=str(path), tofile=str(path), n=3
            )
        )
        diff_text = "\n".join(diff_lines)
        if diff_text:
            _cprint(
                f"\n[DIFF] Replacements in {path.name}:", Fore.CYAN, file=sys.stderr
            )
            print_colored_diff(diff_text, file=sys.stderr)
        if options.dry_run:
            return {
                "success": True,
                "path": str(path),
                "mode": "dry-run",
                "replacements": count,
                "diff": diff_text,
            }
        backup = None
        if count > 0:
            if not options.no_backup:
                backup = _editor._make_backup(path, options.max_backups)
            _editor._atomic_write(path, new_content, options.encoding)
        new_size = path.stat().st_size
        return {
            "success": True,
            "path": str(path),
            "replacements": count,
            "backup_path": str(backup) if backup else None,
            "size": new_size,
            "size_fmt": _format_size(new_size),
        }
    except re.error as exc:
        return {"success": False, "error": f"Invalid regex: {exc}"}
    except Exception as exc:
        return {"success": False, "error": str(exc)}


@_timed
def write(options: PatchOptions) -> Dict[str, Any]:
    """Write or append content to a file."""
    if options.content is None:
        return {"success": False, "error": "content is required for write operation"}
    path = _editor._validate_path(options.file_path, allow_write=True)
    if not path:
        return {
            "success": False,
            "error": f"Invalid or inaccessible path: {options.file_path}",
        }
    content = str(options.content)
    if options.append and path.exists():
        try:
            existing = path.read_text(
                encoding=options.encoding, errors="surrogateescape"
            )
            content = existing + content
        except Exception as exc:
            return {
                "success": False,
                "error": f"Failed to read existing file for append: {exc}",
            }
    backup = None
    if not options.no_backup:
        backup = _editor._make_backup(path, options.max_backups)
    if options.dry_run:
        return {
            "success": True,
            "path": str(path),
            "mode": "append" if options.append else "write",
            "size": len(content.encode(options.encoding, errors="replace")),
            "size_fmt": _format_size(
                len(content.encode(options.encoding, errors="replace"))
            ),
        }
    _editor._atomic_write(path, content, options.encoding)
    new_size = path.stat().st_size
    return {
        "success": True,
        "path": str(path),
        "action": "append" if options.append else "write",
        "backup_path": str(backup) if backup else None,
        "size": new_size,
        "size_fmt": _format_size(new_size),
    }


# ═════════════════════════════════════════════════════════════════════════════
# DISPATCH & CLI
# ═════════════════════════════════════════════════════════════════════════════

_ALL_OPERATIONS = frozenset({"read", "diff", "patch", "replace", "write"})


def run(**kwargs: Any) -> Dict[str, Any]:
    if "path" in kwargs and "file_path" not in kwargs:
        kwargs["file_path"] = kwargs.pop("path")
    options = PatchOptions(**kwargs)
    return _run(options)


def _run(options: PatchOptions) -> Dict[str, Any]:
    if options.operation not in _ALL_OPERATIONS:
        return {"success": False, "error": f"Unknown operation '{options.operation}'"}
    if not options.file_path:
        return {"success": False, "error": "file_path is required"}
    operation_dispatch = {
        "read": lambda: read(options),
        "diff": lambda: diff(options),
        "patch": lambda: patch(options),
        "replace": lambda: replace(options),
        "write": lambda: write(options),
    }
    try:
        return operation_dispatch[options.operation]()
    except Exception as exc:
        logger.exception("Dispatcher error")
        return {"success": False, "error": f"Dispatcher error: {exc}"}


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="patch.py",
        description=f"CPatch File Weaver v{__version__}",
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--operation",
        required=True,
        choices=sorted(_ALL_OPERATIONS),
        help="Operation to perform",
    )
    parser.add_argument("--file-path", required=True, help="Primary file path")
    parser.add_argument("--patch-path", help="Path to patch file")
    parser.add_argument("--target-path", help="Secondary comparison file")
    parser.add_argument("--search-text", "-s", help="Text to search for")
    parser.add_argument("--replacement", "-r", help="Replacement text")
    parser.add_argument("--content", "-c", help="Content to write (for write op)")
    parser.add_argument(
        "--start-line", type=int, help="Start line (1-based, inclusive)"
    )
    parser.add_argument("--end-line", type=int, help="End line (1-based, inclusive)")
    parser.add_argument(
        "--no-lines",
        dest="show_lines",
        action="store_false",
        default=True,
        help="Hide line numbers in read output",
    )
    parser.add_argument("--use-regex", action="store_true", dest="use_regex")
    parser.add_argument(
        "--no-global", dest="global_replace", action="store_false", default=True
    )
    parser.add_argument(
        "--case-insensitive", dest="case_sensitive", action="store_false", default=True
    )
    parser.add_argument("--encoding", default="utf-8")
    parser.add_argument(
        "--max-size", type=int, help="Max read/write size in bytes (default: 100MB)"
    )
    parser.add_argument(
        "--max-backups", type=int, default=15, help="Maximum backup count (default: 15)"
    )
    parser.add_argument(
        "--append",
        action="store_true",
        default=False,
        help="Append content instead of overwriting (for write op)",
    )
    parser.add_argument(
        "--no-backup",
        action="store_true",
        default=False,
        help="Disable backup creation",
    )
    parser.add_argument("--dry-run", action="store_true", default=False)
    parser.add_argument("--verbose", "-v", action="store_true")
    return parser


if __name__ == "__main__":
    _parser = _build_parser()
    cli = _parser.parse_args()
    if cli.verbose:
        logging.basicConfig(
            level=logging.DEBUG, format="%(levelname)s: %(message)s", stream=sys.stderr
        )
    options = PatchOptions(
        operation=cli.operation,
        file_path=cli.file_path,
        patch_path=cli.patch_path,
        target_path=cli.target_path,
        search_text=cli.search_text,
        replacement=cli.replacement,
        content=cli.content,
        start_line=cli.start_line,
        end_line=cli.end_line,
        show_lines=cli.show_lines,
        use_regex=cli.use_regex,
        global_replace=cli.global_replace,
        case_sensitive=cli.case_sensitive,
        encoding=cli.encoding,
        max_backups=cli.max_backups,
        append=cli.append,
        no_backup=cli.no_backup,
        dry_run=cli.dry_run,
        verbose=cli.verbose,
    )
    result = _run(options)
    print(json.dumps(result, indent=2, ensure_ascii=False))
    sys.exit(0 if result.get("success") else 1)

# ====================
# Test modification added at line 632
