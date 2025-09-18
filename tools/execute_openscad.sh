#!/usr/bin/env bash
set -e

# @describe Execute the openscad file.
# @option --path! Path of the file to execute.

# @meta require-tools openscad

# @env LLM_OUTPUT=/dev/stdout The output path

ROOT_DIR="${LLM_ROOT_DIR:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)}"

main() {
    openscad "$argc_path" -o /tmp/aichat_openscad.echo || true
    cat /tmp/aichat_openscad.echo >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
