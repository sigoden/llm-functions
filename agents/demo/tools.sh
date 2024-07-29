#!/usr/bin/env bash
set -e

# @cmd Get the ip info
get_ipinfo() {
    curl -fsSL https://httpbin.org/ip >> "$LLM_OUTPUT"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
