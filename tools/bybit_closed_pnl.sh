#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# bybit_closed_pnl.sh — Bybit Closed PnL Retrieval Tool (V5 API)
#
# @describe Query closed profit-and-loss records for the authenticated user.
# @option --category <linear|inverse|option|spot> Category (default: linear)
# @option --symbol <SYMBOL> Filter by trading symbol
# @option --start-time <ms> Filter by start time (milliseconds)
# @option --end-time <ms> Filter by end time (milliseconds)
# @option --limit <1-100> Limit (default: 50)
# @option --cursor <CURSOR> Pagination cursor
# @env LLM_OUTPUT=/proc/self/fd/1 Output path
# ==============================================================================

set -uo pipefail

# SYNTHWAVE RETRO-NEON PALETTE
CYBER_MAGENTA=$'\033[38;5;201m'
CYBER_LIME=$'\033[38;5;82m'
CYBER_YELLOW=$'\033[38;5;226m'
CYBER_CYAN=$'\033[38;5;51m'
RESET=$'\033[0m'
BORDER_NEON="${CYBER_MAGENTA}"
BORDER_TL='╔' BORDER_TR='╗' BORDER_BL='╚' BORDER_BR='╝' BORDER_V='║'

# Helper functions for reporting
get_width() { tput cols 2>/dev/null || echo 80; }

print_header() {
    local title=" PNL REPORT | $(date '+%Y-%m-%d %H:%M:%S') "
    printf "${BORDER_NEON}${BORDER_TL}${CYBER_YELLOW}%s${BORDER_TR}${RESET}
" "$title"
}

print_footer() {
    printf "${BORDER_NEON}${BORDER_BL}%*s${BORDER_BR}${RESET}
" "$(($(get_width)-2))" ""
}

# ---- API Helper -----------------------------------------------------
_sign_request() {
    local timestamp=$(date +%s%3N)
    local string_to_sign="${timestamp}${1}${2}${3}${4}"
    echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //'
}

main() {
    # Default values from argc
    local CATEGORY="${argc_category:-linear}"
    local SYMBOL="${argc_symbol:-}"
    local LIMIT="${argc_limit:-50}"
    local START_TIME="${argc_start_time:-}"
    local END_TIME="${argc_end_time:-}"
    local CURSOR="${argc_cursor:-}"

    # Build query
    local QUERY="category=${CATEGORY}"
    [[ -n "$SYMBOL" ]] && QUERY="${QUERY}&symbol=${SYMBOL}"
    [[ -n "$START_TIME" ]] && QUERY="${QUERY}&startTime=${START_TIME}"
    [[ -n "$END_TIME" ]] && QUERY="${QUERY}&endTime=${END_TIME}"
    QUERY="${QUERY}&limit=${LIMIT}"
    [[ -n "$CURSOR" ]] && QUERY="${QUERY}&cursor=${CURSOR}"

    local ENDPOINT="/v5/position/closed-pnl"
    local SIGNATURE=$(_sign_request "GET" "$ENDPOINT" "$QUERY" "")

    # Request - One-line curl to avoid shell syntax errors
    local RESPONSE
    RESPONSE=$(curl -s -S -X GET "https://api.bybit.com${ENDPOINT}?${QUERY}" -H "X-BAPI-API-KEY: ${API_KEY:-}" -H "X-BAPI-SIGN: $SIGNATURE" -H "X-BAPI-TIMESTAMP: $(date +%s%3N)" -H "X-BAPI-RECV-WINDOW: 5000")

    # Output formatting
    local width=$(get_width)
    local formatted_output
    if echo "$RESPONSE" | jq . >/dev/null 2>&1; then
        formatted_output=$(echo "$RESPONSE" | jq -C '.result')
    else
        formatted_output="$RESPONSE"
    fi

    # Build full content
    local content
    content=$(
        print_header
        echo "$formatted_output" | while IFS= read -r line; do
            printf "${BORDER_NEON}${BORDER_V}${RESET} %s
" "$line"
        done
        print_footer
    )

    # 1. Print to stdout
    echo "$content"

    # 2. Append to LLM_OUTPUT if set and is a regular file
    if [[ -n "${LLM_OUTPUT:-}" && -f "$LLM_OUTPUT" && "$LLM_OUTPUT" != "/proc/self/fd/1" && "$LLM_OUTPUT" != "/dev/stdout" ]]; then
        echo "$content" >> "$LLM_OUTPUT"
    fi
}

eval "$(argc --argc-eval "$0" "$@")"
