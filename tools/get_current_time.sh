#!/usr/bin/env bash
set -e

# @describe Get the current time.

main() {
    date >> "$LLM_OUTPUT"
}

eval "$(argc --argc-eval "$0" "$@")"
