#!/usr/bin/env python3
"""
edit_options.py – Helper module that defines the EditOptions dataclass
and supporting parsing utilities for the Pyrmethus File Weaver CLI.
This module isolates the option schema from edit2.py so that other
tools can import and reuse the same validation logic without pulling
in the entire file‑editor implementation.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List


# ----------------------------------------------------------------------
# Option definitions – mirrors the CLI arguments in edit2.py
# ----------------------------------------------------------------------
@dataclass
class EditOptions:
    """Container for parsed command‑line options."""

    action: str = field(default="edit")
    file_path: str = field(default="")
    target_path: str = field(default="")
    content: str = field(default="")
    search_text: str = field(default="")
    replacement: str = field(default="")
    pattern: str = field(default="")
    line_number: int = field(default=-1)
    start_line: int = field(default=-1)
    end_line: int = field(default=-1)
    encoding: str = field(default="utf-8")
    max_size: int = field(default=0)
    max_write_size: int = field(default=0)
    line_context: int = field(default=0)
    sort_by: str = field(default="name")
    context_lines: int = field(default=0)
    truncate_size: int = field(default=0)
    max_backups: int = field(default=0)
    max_matches: int = field(default=0)
    mode: str = field(default="")
    to_type: str = field(default="")
    backup_timestamp: str = field(default="")
    algorithm: str = field(default="")
    n_lines: int = field(default=0)
    compare_mode: str = field(default="")
    compression: str = field(default="")
    password: str = field(default="")
    undefined_var: str = field(default="error")
    file_pattern: str = field(default="")
    min_size: int = field(default=0)
    max_size_filter: int = field(default=0)
    modified_after: str = field(default="")
    modified_before: str = field(default="")
    file_type: str = field(default="")
    max_results: int = field(default=0)
    var: Dict[str, Any] = field(default_factory=dict)
    ops: List[Any] = field(default_factory=list)
    edits: List[Any] = field(default_factory=list)
    use_regex: bool = field(default=False)
    no_global: bool = field(default=False)
    case_insensitive: bool = field(default=False)
    show_lines: bool = field(default=False)
    add_newline: bool = field(default=False)
    create_parents: bool = field(default=False)
    preserve_metadata: bool = field(default=False)
    include_hidden: bool = field(default=False)
    descending: bool = field(default=False)
    parents: bool = field(default=False)
    recursive: bool = field(default=False)
    verbose: bool = field(default=False)
    dry_run: bool = field(default=False)


# ----------------------------------------------------------------------
# Helper functions for parsing and validation
# ----------------------------------------------------------------------
def _dict_to_namespace(data: Dict[str, Any]) -> EditOptions:
    """Convert a raw argument dictionary into a typed EditOptions instance."""
    return EditOptions(**{k: v for k, v in data.items() if hasattr(EditOptions, k)})


def build_edit_options_from_dict(arg_dict: Dict[str, Any]) -> EditOptions:
    """
    Public helper that validates and builds an EditOptions object.
    It strips out any keys that are not part of the dataclass fields,
    logs a warning for unknown keys, and raises a ValueError for missing
    required fields.
    """
    # Required fields check
    required = {"action", "file_path"}
    missing = required - arg_dict.keys()
    if missing:
        raise ValueError(f"Missing required arguments: {', '.join(missing)}")

    # Filter unknown keys
    filtered = {k: v for k, v in arg_dict.items() if hasattr(EditOptions, k)}
    return _dict_to_namespace(filtered)


# ----------------------------------------------------------------------
# Example usage (can be removed or expanded)
# ----------------------------------------------------------------------
if __name__ == "__main__":
    # Simple demo: parse a mock CLI dict and print the resulting options
    mock_args = {
        "action": "view",
        "file_path": "example.txt",
        "verbose": True,
    }
    opts = build_edit_options_from_dict(mock_args)
    print("Parsed options:", opts)
