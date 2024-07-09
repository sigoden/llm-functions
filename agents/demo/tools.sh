#!/usr/bin/env bash
set -e

# @cmd Get the system info
get_sysinfo() {
    echo "OS: $(uname)"
    echo "Arch: $(arch)"
    echo "User: $USER"
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
