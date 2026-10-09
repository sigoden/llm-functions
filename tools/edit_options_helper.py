#!/usr/bin/env python3
# =============================================================================
# Edit Options Helper – definitions for the Pyrmethus File Weaver edit tool
# =============================================================================
# This module provides the EditOptions dataclass and associated helper functions
# used by edit_file.py (edit2.py) to validate and parse command‑line arguments.
# It mirrors the original EditOptions definition but is isolated for easier
# import and reuse by other scripts or utilities.
# =============================================================================

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Union


# ---------------------------------------------------------------------------
# Helper: convert a raw dict (e.g. from argparse) into a typed EditOptions
# ---------------------------------------------------------------------------
def _dict_to_namespace(raw: Dict[str, Any]) -> Dict[str, Any]:
    """
    Normalise raw argument dict values:
      * booleans from strings like "true"/"false"/"1"/"0"
      * integers from strings when possible
      * lists from JSON strings or comma‑separated strings
    """

    def _coerce(value: Union[str, int, bool, None]) -> Any:
        if value is None:
            return None
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return int(value) if value.is_integer() else value
        if isinstance(value, str):
            low = value.lower()
            if low in {"true", "1", "yes", "y"}:
                return True
            if low in {"false", "0", "no", "n"}:
                return False
            if low == "none":
                return None
            # Try numeric conversion
            try:
                return int(value)
            except ValueError:
                try:
                    return float(value)
                except ValueError:
                    return value
        return value

    coerced: Dict[str, Any] = {}
    for k, v in raw.items():
        if isinstance(v, dict):
            # Nested structures (e.g. JSON arrays) need special handling
            if k in {"ops", "edits"} and isinstance(v, str):
                try:
                    parsed = json.loads(v)
                    if isinstance(parsed, (list, dict)):
                        coerced[k] = parsed
                        continue
                except json.JSONDecodeError:
                    pass
            coerced[k] = _dict_to_namespace(v)
        else:
            coerced[k] = _coerce(v)
    return coerced


# ---------------------------------------------------------------------------
# EditOptions – mirrors the argument schema used by the CLI in edit_file.py
# ---------------------------------------------------------------------------
@dataclass
class EditOptions:
    """
    Dataclass representing a parsed set of edit‑tool options.

    All fields correspond to a command‑line flag (see edit_file.py's argparse
    definition).  The class is deliberately lightweight – it does **not**
    implement validation logic; validation is performed by the dispatcher
    before the operation is executed.
    """

    operation: Optional[str] = None  # required
    file_path: Optional[str] = None  # primary path
    target_path: Optional[str] = None  # secondary path
    content: Optional[Union[str, int, float]] = None  # content to write/append
    search_text: Optional[str] = None  # pattern to search for
    replacement: Optional[str] = None  # replacement text
    pattern: Optional[str] = None  # alias for search_text
    use_regex: bool = False  # --regex flag
    global_replace: bool = True  # --no-global negates this
    case_sensitive: bool = True  # --case-insensitive negates this
    encoding: str = "utf-8"  # --encoding
    line_context: int = 0  # --context
    include_hidden: bool = False  # --include-hidden
    sort_by: str = "name"  # --sort
    descending: bool = False  # --descending
    show_lines: bool = True  # --no-lines negates this
    add_newline: bool = False  # --add-newline
    context_lines: int = 3  # --diff-context
    truncate_size: int = 0  # --truncate-size
    max_backups: int = 15  # --max-backups
    max_matches: int = 1000  # --max-matches
    mode: Optional[str] = None  # permission/compare mode
    to_type: Optional[str] = None  # --to-type (lf/crlf)
    backup_timestamp: Optional[str] = None  # --backup-timestamp
    recursive: bool = False  # --recursive
    verbose: bool = False  # --verbose
    algorithm: str = "sha256"  # --algorithm (hash)
    n_lines: int = 10  # --n-lines
    compare_mode: str = "bytes"  # --compare-mode
    compression: str = "deflate"  # --compression
    password: Optional[str] = None  # --password
    variables: List[str] = field(default_factory=list)  # --var (repeatable)
    undefined_var: str = "error"  # --undefined-var
    file_pattern: str = "*"  # --file-pattern
    min_size: Optional[int] = None  # --min-size
    max_size_filter: Optional[int] = None  # --max-size-filter
    modified_after: Optional[float] = None  # --modified-after
    modified_before: Optional[float] = None  # --modified-before
    file_type: str = "any"  # --file-type
    max_results: int = 1000  # --max-results
    edits: Optional[List[Dict[str, Any]]] = field(default_factory=list)  # --edits
    continue_on_error: bool = False  # --continue-on-error
    dry_run: bool = False  # --dry-run
    ops: Optional[List[Dict[str, Any]]] = None  # --ops (batch mode)
    line_number: Optional[int] = None  # --line
    start_line: Optional[int] = None  # --start-line
    end_line: Optional[int] = None  # --end-line
    # Additional convenience fields that are derived from the raw CLI args
    path_alias_resolved: bool = False  # internal: did we map --path → file_path?

    # -----------------------------------------------------------------------
    # Helper: convert this instance back to a plain dict suitable for JSON I/O
    # -----------------------------------------------------------------------
    def to_dict(self) -> Dict[str, Any]:
        """Return a JSON‑serialisable representation of the options."""
        return {k: v for k, v in self.__dict__.items() if v is not None}

    # -----------------------------------------------------------------------
    # Helper: validate that required fields are present
    # -----------------------------------------------------------------------
    def validate(self) -> Dict[str, str]:
        """Check for mandatory fields and report missing ones."""
        missing = []
        if not self.operation:
            missing.append("operation")
        if not self.file_path:
            missing.append("file_path")
        if missing:
            return {
                "success": False,
                "error": f"Missing required option(s): {', '.join(missing)}",
            }
        return {"success": True}


# ---------------------------------------------------------------------------
# Convenience: build an EditOptions instance from a flat dict (e.g. argparse.Namespace)
# ---------------------------------------------------------------------------
def build_edit_options_from_dict(data: Dict[str, Any]) -> EditOptions:
    """
    Create an EditOptions instance from a dictionary that mimics the parsed
    argparse namespace.  The function performs minimal coercion and then hands
    the result to the EditOptions dataclass constructor.
    """
    # Convert boolean string flags that may have been stored as ints/strings
    bool_flags = {
        "use_regex",
        "global_replace",
        "case_sensitive",
        "show_lines",
        "add_newline",
        "recursive",
        "verbose",
        "continue_on_error",
        "dry_run",
    }
    for flag in bool_flags:
        val = data.get(flag)
        if isinstance(val, str):
            data[flag] = val.lower() in {"1", "true", "yes", "y"}

    # Handle list‑like arguments that arrive as JSON strings or comma‑separated
    for key in ("variables", "ops", "edits"):
        raw = data.get(key)
        if isinstance(raw, str):
            try:
                parsed = json.loads(raw)
                if isinstance(parsed, list):
                    data[key] = parsed
            except json.JSONDecodeError:
                # fallback: split on commas
                data[key] = [x.strip() for x in raw.split(",") if x.strip()]

    # Resolve the ``path`` alias to ``file_path`` if present
    if "path" in data and "file_path" not in data:
        data["file_path"] = data.pop("path")
        data["path_alias_resolved"] = True

    return EditOptions(**data)


# ---------------------------------------------------------------------------
# Example usage (can be removed or kept for documentation)
# ---------------------------------------------------------------------------
if __name__ == "__main__":
    # Simple demo: print the dataclass signature
    import pprint

    pprint.pprint(EditOptions.__dataclass_fields__)
