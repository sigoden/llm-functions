#!/data/data/com.termux/files/usr/bin/env bash
set -euo pipefail

# @describe Send an SMS message using the Termux SMS API
# @option --number! -n The recipient phone number
# @option --message! -m The SMS message body
# @env LLM_OUTPUT=/dev/stdout The output path.

main() {
    # Check if the message body is provided as an argument or via stdin
    local message="${argc_message:-}"

    # If not provided via flag, read from stdin if available
    if [[ -z "$message" && ! -t 0 ]]; then
        message=$(cat)
    fi

    if [[ -z "$message" ]]; then
        echo "ERROR: Message body is required (use --message or pipe content)." >&2
        exit 1
    fi

    # Execute termux-sms-send
    if ! termux-sms-send -n "$argc_number" "$message"; then
        echo "{\"status\": \"error\", \"msg\": \"Failed to send SMS to $argc_number\"}"
        exit 1
    fi

    echo "{\"status\": \"success\", \"msg\": \"SMS sent to $argc_number\"}"
}


eval "$(argc --argc-eval "$0" "$@")"
