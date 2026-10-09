#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Ping a host to check connectivity.
# @option --host! The host to ping.
# @option --count=4 Number of pings.

main() {
    if ! command -v ping >/dev/null 2>&1; then
        echo "Error: 'ping' command not found." >&2
        exit 1
    fi
    ping -c "${argc_count}" "${argc_host}" > "${LLM_OUTPUT:-/dev/stdout}"
}

eval "$(argc --argc-eval "$0" "$@")"
