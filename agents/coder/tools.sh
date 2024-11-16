#!/usr/bin/env bash
set -e

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# @env LLM_OUTPUT=/dev/stdout The output path

# @cmd Create a new file at the specified path with contents.
# @option --path! The path where the file should be created
# @option --contents! The contents of the file
fs_create() {
    "$ROOT_DIR/utils/guard_path.sh" "$argc_path" "Create '$argc_path'?"
    mkdir -p "$(dirname "$argc_path")"
    printf "%s" "$argc_contents" > "$argc_path"
    echo "File created: $argc_path" >> "$LLM_OUTPUT"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
