#!/usr/bin/env python3
"""End-to-end smoke test for photo_tool.py v2.1.0."""

import subprocess
import sys
import tempfile
from pathlib import Path

TOOL = "tools/photo_tool.py"
PYTHON = sys.executable

results = []


def run(label, cmd, expect_code=0):
    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    ok = result.returncode == expect_code
    results.append(
        {
            "test": label,
            "cmd": " ".join(cmd[1:]),
            "exit": result.returncode,
            "ok": ok,
        }
    )
    print(f"  {'✓' if ok else '✗'} {label} (exit={result.returncode})")
    if not ok:
        print(f"    stdout: {result.stdout[:300]}")
        print(f"    stderr: {result.stderr[:300]}")
    return result


print("Pyrmethus Camera Snap v2.1.0 — E2E smoke test\n")

# 1. --help
run("CLI: --help works", [PYTHON, TOOL, "--help"], expect_code=0)

# 2. --list-cameras
run("CLI: --list-cameras", [PYTHON, TOOL, "--list-cameras"], expect_code=0)

# 3. --selftest
run("CLI: --selftest", [PYTHON, TOOL, "--selftest"], expect_code=0)

# 4. --dry-run with various options
with tempfile.TemporaryDirectory() as td:
    cmd = [
        PYTHON,
        TOOL,
        "--dry-run",
        "--save-dir",
        td,
        "--no-log",
        "--no-thumbnail",
        "--filename",
        "smoke.jpg",
        "--resize",
        "300x300",
        "--filter",
        "sepia",
        "--annotate",
        "test {ts}",
        "--album",
        "smoke",
    ]
    run("CLI: --dry-run with filter+resize+annotate", cmd, expect_code=0)
    photo = Path(td) / "smoke" / "smoke.jpg"
    print(
        f"  Photo exists: {photo.exists()} (size={photo.stat().st_size if photo.exists() else 0})"
    )

# 5. --burst mode
with tempfile.TemporaryDirectory() as td:
    cmd = [
        PYTHON,
        TOOL,
        "--dry-run",
        "--save-dir",
        td,
        "--no-log",
        "--no-thumbnail",
        "--burst",
        "3",
        "--burst-delay",
        "50",
    ]
    run("CLI: --burst 3", cmd, expect_code=0)
    photos = sorted(Path(td).rglob("*.jpg"))
    print(f"  Burst photos: {len(photos)}")

# 6. Invalid filter rejected
run(
    "CLI: --filter invalid rejected",
    [PYTHON, TOOL, "--dry-run", "--no-log", "--no-thumbnail", "--filter", "bogus"],
    expect_code=2,
)

# 7. --show-log --export-log json
run(
    "CLI: --show-log --export-log json",
    [PYTHON, TOOL, "--show-log", "--export-log", "json"],
    expect_code=0,
)

# 8. --show-log --export-log csv
run(
    "CLI: --show-log --export-log csv",
    [PYTHON, TOOL, "--show-log", "--export-log", "csv"],
    expect_code=0,
)

# 9. --show-log --export-log md
run(
    "CLI: --show-log --export-log md",
    [PYTHON, TOOL, "--show-log", "--export-log", "md"],
    expect_code=0,
)

# 10. New filter choices (vignette/emboss)
with tempfile.TemporaryDirectory() as td:
    cmd = [
        PYTHON,
        TOOL,
        "--dry-run",
        "--save-dir",
        td,
        "--no-log",
        "--no-thumbnail",
        "--filter",
        "vignette",
    ]
    run("CLI: --filter vignette", cmd, expect_code=0)
    cmd = [
        PYTHON,
        TOOL,
        "--dry-run",
        "--save-dir",
        td,
        "--no-log",
        "--no-thumbnail",
        "--filter",
        "emboss",
    ]
    run("CLI: --filter emboss", cmd, expect_code=0)

# Summary
total = len(results)
passed = sum(1 for r in results if r["ok"])
print(f"\n{passed}/{total} tests passed")
sys.exit(0 if passed == total else 1)
