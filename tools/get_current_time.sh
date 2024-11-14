#!/usr/bin/env bash
set -e

# @describe Get the current time.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    date >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
