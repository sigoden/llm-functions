#!/usr/bin/env bash
set -e

# @describe Execute the shell command.
# @option --command! The command to execute.

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
    "$ROOT_DIR/utils/guard_operation.sh"
    eval "$argc_command" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
