#!/usr/bin/env bash
set -e

# @describe Estimate file space usage (du). Identifies large directories.
# @option --path! The directory to analyze (default: current directory).
# @option --depth <INT> Display only a total for each directory to this depth (default: 1).
# @flag --human-readable Print sizes in human readable format (e.g., 1K 234M 2G).

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    local target="${argc_path:-.}"
    local depth="${argc_depth:-1}"
    local flags="--max-depth=$depth"

    if [[ -n "$argc_human_readable" ]]; then
        flags="$flags -h"
    fi

    # Sort by size (numeric sort) to show biggest items at the bottom
    du $flags "$target" | sort -h >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
