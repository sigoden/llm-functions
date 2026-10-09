#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Closed PnL Retrieval Tool (V5 API)
# ------------------------------------------------------------
# Queries closed profit-and-loss records for the authenticated user.
# Supports filtering by category, symbol, time range and pagination.
# Writes a concise JSON summary to the `$LLM_OUTPUT` variable.
# ------------------------------------------------------------

set -euo pipefail

# ---- Load environment ------------------------------------------------
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# Default values
API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
PROXY_URL="${PROXY_URL:-}"
RECV_WINDOW="${RECV_WINDOW:-5000}"
CATEGORY="${CATEGORY:-linear}"   # can be linear, inverse, option, etc.
SYMBOL="${SYMBOL:-}"
START_TIME="${START_TIME:-}"
END_TIME="${END_TIME:-}"
LIMIT="${LIMIT:-50}"
CURSOR="${CURSOR:-}"

# ---- Helper: generate request signature -------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local query=$3
    local body=$4
    # Bybit signs: timestamp + HTTP method + endpoint + query string + body
    local timestamp=$(date +%s%3N)   # milliseconds
    local string_to_sign="${timestamp}${method}${endpoint}${query}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Argument parsing -------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 [--category <linear|inverse|option|spot>] [--symbol <SYMBOL>]
        [--start-time <ms>] [--end-time <ms>] [--limit <1-100>] [--cursor <CURSOR>]
Fetch closed PnL records. Optional filters and pagination.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --category)   CATEGORY="$2"; shift 2 ;;
        --symbol)     SYMBOL="$2"; shift 2 ;;
        --start-time) START_TIME="$2"; shift 2 ;;
        --end-time)   END_TIME="$2"; shift 2 ;;
        --limit)      LIMIT="$2"; shift 2 ;;
        --cursor)     CURSOR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

# ---- Build query string -----------------------------------------------
QUERY="category=${CATEGORY}"
[[ -n "$SYMBOL" ]] && QUERY="${QUERY}&symbol=${SYMBOL}"
[[ -n "$START_TIME" ]] && QUERY="${QUERY}&startTime=${START_TIME}"
[[ -n "$END_TIME" ]] && QUERY="${QUERY}&endTime=${END_TIME}"
[[ -n "$LIMIT" ]] && QUERY="${QUERY}&limit=${LIMIT}"
[[ -n "$CURSOR" ]] && QUERY="${QUERY}&cursor=${CURSOR}"

# Remove leading '&' if present for signing
SIGN_QUERY="${QUERY#&}"

# ---- API details -------------------------------------------------------
ENDPOINT="/v5/position/closed-pnl"
METHOD="GET"

# ---- Sign and send request --------------------------------------------
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$SIGN_QUERY" "")
# Build curl command
CURL_CMD=(curl -s -S -X GET "https://api.bybit.com$ENDPOINT")
if [[ -n "$QUERY" ]]; then
    CURL_CMD+=("?${QUERY}")
fi
CURL_CMD+=(
    -H "X-BAPI-API-KEY: $API_KEY"
    -H "X-BAPI-SIGN: $SIGNATURE"
    -H "X-BAPI-TIMESTAMP: $(date +%s%3N)"
    -H "X-BAPI-RECV-WINDOW: $RECV_WINDOW"
)
if [[ -n "$PROXY_URL" ]]; then
    PROXYCMD="proxychains4 ${CURL_CMD[*]}"
    RESPONSE=$($PROXYCMD)
else
    RESPONSE=$( "${CURL_CMD[@]}" )
fi

# ---- Response handling -------------------------------------------------
if ! RESPONSE_JSON=$(echo "$RESPONSE" | jq -e . 2>/dev/null); then
    echo "Invalid JSON response"
    echo "Raw: $RESPONSE"
    exit 1
fi

RET_CODE=$(echo "$RESPONSE_JSON" | jq -r '.retCode // 0')
if [[ "$RET_CODE" -ne 0 ]]; then
    RET_MSG=$(echo "$RESPONSE_JSON" | jq -r '.retMsg // "Unknown error"')
    echo "Bybit API error $RET_CODE: $RET_MSG"
    exit 1
fi

# Extract a brief summary – number of closed records and the first record's PnL if present
RECORDS=$(echo "$RESPONSE_JSON" | jq -r '.result | length')
FIRST_PNL=$(echo "$RESPONSE_JSON" | jq -r '.[0].closedPnl // empty')

SUMMARY=$(jq -n \
    --arg count "$RECORDS" \
    --arg pnl "$FIRST_PNL" \
    '{action:"closed_pnl", record_count:$count, first_closed_pnl:$pnl, category:"'"$CATEGORY"'"}')

# Forward to LLM_OUTPUT if set
if [[ -n "${LLM_OUTPUT:-}" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

exit 0
