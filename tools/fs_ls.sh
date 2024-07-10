#!/usr/bin/env bash
set -e

# @describe List all files and directories at the specified path.

# @env FS_BASE_DIR=. The base dir
# @option --path! The path of the directory to list

main() {
    path="$FS_BASE_DIR/$argc_path"
    ls -1 "$path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
