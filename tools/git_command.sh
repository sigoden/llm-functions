#!/usr/bin/env bash
set -e

# @describe Run the git command with the specified arguments.
# @option --path The path to the Git repository (default: current directory).
# @option --arguments+ An array of arguments to pass to the git command.

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
  cd "${argc_path:-.}"
  printf "Path is : %s\n" "$(pwd)"
  "$ROOT_DIR/utils/guard_operation.sh"
  git "${argc_arguments[@]}" >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
