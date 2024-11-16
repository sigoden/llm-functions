#!/usr/bin/env bash
set -e

# @describe Remove the file or directory at the specified path.

# @option --path! The path of the file or directory to remove

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    if [[ -f "$argc_path" ]]; then
        root_dir="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"
        "$root_dir/utils/guard_path.sh" "$argc_path" "Remove '$argc_path'?"
        rm -rf "$argc_path"
    fi
    echo "Path removed: $argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
