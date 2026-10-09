#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Get the current time.

# @env LLM_OUTPUT=/dev/fd/1 The output path

main() {
    date >&1
}

eval "$(argc --argc-eval "$0" "$@")"
