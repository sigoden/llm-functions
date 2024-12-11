#!/usr/bin/env bash
set -e

# @describe Write the full file contents to a file at the specified path.

# @option --path! The path of the file to write to
# @option --contents! The full contents to write to the file

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
    if [[ -f "$argc_path" ]]; then
        printf "%s" "$argc_contents" | git diff --no-index "$argc_path" - || true
        "$ROOT_DIR/utils/guard_operation.sh" "Apply changes?"
    else
        "$ROOT_DIR/utils/guard_path.sh" "$argc_path" "Write '$argc_path'?"
        mkdir -p "$(dirname "$argc_path")"
    fi
    printf "%s" "$argc_contents" > "$argc_path"
    echo "The contents written to: $argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
