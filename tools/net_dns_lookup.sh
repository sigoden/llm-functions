#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Perform a DNS lookup.
# @option --host! The host to lookup.

main() {
    if ! command -v nslookup >/dev/null 2>&1; then
        echo "Error: 'nslookup' command not found." >&2
        exit 1
    fi
    nslookup "${argc_host}" > "${LLM_OUTPUT:-/dev/stdout}"
}

eval "$(argc --argc-eval "$0" "$@")"
