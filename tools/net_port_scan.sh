#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Scan common open ports on a host.
# @option --host! The host to scan.

main() {
    for port in 22 80 443 3000 8080 27017; do
        if (echo > /dev/tcp/"$argc_host"/"$port") >/dev/null 2>&1; then
            message="$port is open"
            if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
                echo -e "\033[32m$message\033[0m" # Green for open
            else
                echo "$message"
            fi
        else
            message="$port is closed"
            if [[ "$LLM_OUTPUT_COLOR" == "1" ]]; then
                echo -e "\033[31m$message\033[0m" # Red for closed
            else
                echo "$message"
            fi
        fi
    done
}

eval "$(argc --argc-eval "$0" "$@")"
