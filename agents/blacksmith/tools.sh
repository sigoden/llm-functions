#!/usr/bin/env bash
set -e

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# @cmd Append content to a file.
# @option --path! The path to the file.
# @option --contents! The contents to append.
fs_append() {
  "$ROOT_DIR/utils/guard_path.sh" "$argc_path" "Append to '$argc_path'?"
  printf "%s" "$argc_contents" >> "$argc_path"
  echo "Appended to: $argc_path" >> "$LLM_OUTPUT"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
