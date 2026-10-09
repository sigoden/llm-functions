#!/usr/bin/env python3
# @describe CPatch File Weaver: Atomic file manipulation for Termux.
# @operation read|diff|patch|replace|edit|write
# @option --file-path! <PATH>
# @option --target-path <PATH>
# @flag --dry-run
# @flag --aichat-mode

import argparse
import difflib
import json
import os
import sys
import tempfile
from pathlib import Path
from typing import Any, Dict

__version__ = "4.2.0"


# --- ANSI PALETTE ---
class C:
    RED = "\033[31m"
    GRN = "\033[32m"
    YLW = "\033[33m"
    CYN = "\033[36m"
    MAG = "\033[35m"
    RST = "\033[0m"
    DIM = "\033[2m"
    BOLD_CYN = "\033[1;36m"


def print_colored_diff(diff_text: str, file=sys.stderr):
    """Render unified diff hunks with color, only if TTY is detected."""
    if not file.isatty():
        file.write(diff_text)
        return
    for line in diff_text.splitlines(keepends=True):
        if line.startswith(("+++", "---")):
            file.write(f"{C.BOLD_CYN}{line}{C.RST}")
        elif line.startswith("@@"):
            file.write(f"{C.CYN}{line}{C.RST}")
        elif line.startswith("+"):
            file.write(f"{C.GRN}{line}{C.RST}")
        elif line.startswith("-"):
            file.write(f"{C.RED}{line}{C.RST}")
        else:
            file.write(line)
    file.flush()


# --- CORE LOGIC ---
class FileWeaver:
    def __init__(self):
        self.home = Path("/data/data/com.termux/files/home").resolve()
        self.temp = Path(tempfile.gettempdir()).resolve()

    def is_safe(self, p: Path) -> bool:
        resolved = p.resolve()
        return (
            resolved.is_relative_to(self.home) or resolved.is_relative_to(self.temp)
        ) and ".." not in p.parts

    def atomic_write(self, path: Path, content: str):
        tmp = tempfile.NamedTemporaryFile(
            "w", dir=path.parent, delete=False, encoding="utf-8"
        )
        tmp.write(content)
        tmp.flush()
        os.fsync(tmp.fileno())
        tmp.close()
        os.replace(tmp.name, path)


def run(operation: str, file_path: str, **kwargs: Any) -> Dict[str, Any]:
    weaver = FileWeaver()
    target = Path(file_path).expanduser().resolve()

    if not weaver.is_safe(target):
        return {"success": False, "error": "Access Denied"}

    try:
        if operation == "read":
            content = target.read_text(errors="replace")
            return {"success": True, "content": content}

        elif operation == "diff":
            target_path = Path(kwargs.get("target_path", "")).expanduser().resolve()
            if not weaver.is_safe(target_path):
                return {"success": False, "error": "Unsafe target"}

            c1 = target.read_text(errors="replace").splitlines(keepends=True)
            c2 = target_path.read_text(errors="replace").splitlines(keepends=True)
            diff_text = "".join(
                difflib.unified_diff(
                    c1, c2, fromfile=str(target), tofile=str(target_path)
                )
            )

            if diff_text:
                print_colored_diff(diff_text)
            return {"success": True, "diff": diff_text, "changed": bool(diff_text)}

        return {"success": False, "error": "Operation not implemented"}
    except Exception as e:
        return {"success": False, "error": str(e)}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("operation", choices=["read", "diff", "write"])
    parser.add_argument("file_path")
    parser.add_argument("--target-path")

    args = parser.parse_args()
    result = run(**vars(args))

    dest = os.environ.get("LLM_OUTPUT", "/dev/stdout")
    with open(dest, "w", encoding="utf-8") as f:
        json.dump(result, f, indent=2)


if __name__ == "__main__":
    main()
