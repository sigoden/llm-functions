#!/usr/bin/env bash
set -e

# @describe Get the current time.

main() {
    date
}

eval "$(argc --argc-eval "$0" "$@")"

