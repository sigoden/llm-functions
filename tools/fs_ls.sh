#!/usr/bin/env bash
set -e

# @describe List all files and directories at the specified path.

# @option --path! The path of the directory to list

main() {
    ls -1 "$argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
