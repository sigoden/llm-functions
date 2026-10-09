#!/usr/bin/env python3
import argparse
import os
import sys
from pathlib import Path
from typing import List, Tuple


def human_readable_size(num_bytes: int) -> str:
    """Convert bytes to human readable format."""
    if num_bytes < 1024:
        return f"{num_bytes} B"
    for unit in ["KB", "MB", "GB", "TB"]:
        num_bytes /= 1024.0
        if num_bytes < 1024.0:
            return f"{num_bytes:.2f} {unit}"
    return f"{num_bytes:.2f} PB"


def gather_files(root_path: str) -> List[Tuple[Path, int, str]]:
    """Walk root_path and return list of (file_path, size_bytes, mtime)"""
    entries = []
    for dirpath, dirnames, filenames in os.walk(root_path):
        for filename in filenames:
            file_path = Path(dirpath) / filename
            try:
                stat = file_path.stat()
                entries.append((file_path, stat.st_size, stat.st_mtime))
            except (OSError, PermissionError):
                continue
    return entries


def main():
    parser = argparse.ArgumentParser(description="Workspace analysis tool")
    parser.add_argument("--path", type=str, default=".", help="Path to analyze")
    parser.add_argument(
        "--sort", type=str, choices=["name", "size", "date", "type"], default="name"
    )
    parser.add_argument(
        "--max-results", type=int, default=50, help="Maximum results to show"
    )
    parser.add_argument(
        "--human-readable",
        action="store_true",
        help="Show sizes in human readable format (K/M/G)",
    )
    parser.add_argument(
        "--show-summary", action="store_true", help="Show workspace summary"
    )
    args = parser.parse_args()

    root = Path(args.path).expanduser().resolve()
    if not root.exists():
        print(f"Error: path '{root}' does not exist.", file=sys.stderr)
        sys.exit(1)

    files = gather_files(str(root))

    # Determine sorting key
    if args.sort == "name":
        files.sort(key=lambda x: x[0].name.lower())
    elif args.sort == "size":
        files.sort(key=lambda x: x[1])
    elif args.sort == "date":
        files.sort(key=lambda x: x[2])
    elif args.sort == "type":
        files.sort(key=lambda x: x[0].suffix.lower())

    # Apply max-results limit
    files = files[: args.max_results]

    # Output each file
    for file_path, size, mtime in files:
        size_display = human_readable_size(size) if args.human_readable else str(size)
        mtime_str = os.path.getmtime(file_path)
        print(f"{size_display:>10}  {mtime_str}  {file_path}")

    # Summary
    if args.show_summary:
        total_bytes = sum(f[1] for f in files)
        print(
            f"\nSummary: {len(files)} items, total size {human_readable_size(total_bytes)}"
        )


if __name__ == "__main__":
    main()
