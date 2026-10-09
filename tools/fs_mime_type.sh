#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Identify the MIME type of a file.
# @arg file! The file to identify.

main() {
    if [[ ! -f "$argc_file" ]]; then
        echo "Error: File '$argc_file' not found." >&2
        exit 1
    fi
    file --mime-type -b "$argc_file" > "${LLM_OUTPUT:-/dev/stdout}"
}

eval "$(argc --argc-eval "$0" "$@")"
