#!/usr/bin/env bash
set -e

# @describe Run git commands with consistent LLM integration.
# @arg args+ The arguments to pass to the git command.

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
  "$ROOT_DIR/utils/guard_operation.sh"
  git "${argc_args[@]}" >>"$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
