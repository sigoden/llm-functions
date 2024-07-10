#!/usr/bin/env bash
set -e

# @cmd Get the system info
get_sysinfo() {
    cat <<EOF >> "$LLM_OUTPUT"
OS: $(uname)
Arch: $(arch)
EOF
}

# See more details at https://github.com/sigoden/argc
eval "$(argc --argc-eval "$0" "$@")"
