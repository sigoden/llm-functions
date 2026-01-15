#!/usr/bin/env bash
set -e

# @describe List currently logged in users, recent successful logins, and failed login attempts.
# @flag --failed Show failed login attempts matching recent history (potential intrusion).
# @flag --verbose Show full detail of current sessions including source IP.

# @env LLM_OUTPUT=/dev/stdout The output path

main() {
    echo "=== Active Sessions ===" >> "$LLM_OUTPUT"
    # 'w' shows who is doing what right now
    if [[ -n "$argc_verbose" ]]; then
        w -i >> "$LLM_OUTPUT"
    else
        w -h >> "$LLM_OUTPUT"
    fi
    echo "" >> "$LLM_OUTPUT"

    echo "=== Last 5 Logins ===" >> "$LLM_OUTPUT"
    last -n 5 >> "$LLM_OUTPUT"

    if [[ -n "$argc_failed" ]]; then
        echo "" >> "$LLM_OUTPUT"
        echo "=== FAILED Login Attempts (last 10) ===" >> "$LLM_OUTPUT"
        # Reading user login failure log directly
        if command -v fail2ban-client &> /dev/null; then
             fail2ban-client status sshd >> "$LLM_OUTPUT"
        else
             lastb -n 10 >> "$LLM_OUTPUT" || echo "Need sudo to read detailed failure logs (btmp)." >> "$LLM_OUTPUT"
        fi
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
