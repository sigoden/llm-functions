#!/usr/bin/env bash
set -e

# @env LLM_OUTPUT=/dev/stdout The output path
# @env SKOGAI=/home/skogix/skogai SkogAI root dir

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd)}"

# @cmd Show the library
show_library() {
  local library="${SKOGAI}/docs/"
  tree -C "$library/archives" "$library/official" --noreport >> "$LLM_OUTPUT"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
