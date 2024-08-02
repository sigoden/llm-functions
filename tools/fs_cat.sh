#!/usr/bin/env bash
set -e

# @describe Read the contents of a file at the specified path.
# Use this when you need to examine the contents of an existing file.

# @option --path! The path of the file to read

main() {
    cat "$argc_path" >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
