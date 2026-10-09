#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Execution List (Trade History) Tool (V5 API)
# ------------------------------------------------------------
# Retrieves recent execution records for the authenticated user.
# Supports an optional `symbol` filter and pagination via `cursor`.
# Writes a concise JSON summary to the `$LLM_OUTPUT` variable.
# ------------------------------------------------------------

set -euo pipefail

# ---- Load environment variables ---------------------------------------
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# Default values
API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
PROXY_URL="${PROXY_URL:-}"
RECV_WINDOW="${RECV_WINDOW:-5000}"

# ---- Helper: generate request signature -------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local query=$3
    local body=$4
    # Bybit signs: timestamp + HTTP method + endpoint + query string + request body
    local timestamp=$(date +%s%3N)   # milliseconds
    local string_to_sign="${timestamp}${method}${endpoint}${query}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Argument parsing -------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 [--symbol <SYMBOL>] [--cursor <CURSOR>]
Fetch recent execution records. Optional symbol filter and pagination.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --symbol)   SYMBOL="$2"; shift 2 ;;
        --cursor)   CURSOR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

# ---- Build query string -----------------------------------------------
QUERY=""
[[ -n "${SYMBOL:-}" ]] && QUERY="symbol=${SYMBOL}"
[[ -n "${CURSOR:-}" ]] && QUERY="${QUERY}&cursor=${CURSOR}"

# If we have a query string, it starts with '?' later; for signing we send it without leading '?'
SIGN_QUERY="${QUERY#?}"   # remove leading '?' if present

# ---- API details -------------------------------------------------------
ENDPOINT="/v5/execution/list"
METHOD="GET"

# ---- Sign and send request --------------------------------------------
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$SIGN_QUERY" "")
# Build curl command
CURL_CMD=(curl -s -S -X GET "https://api.bybit.com$ENDPOINT")
# Append query string to URL
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
    # Use proxychains4 if a proxy is defined
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

# Extract a brief summary – number of executions returned and the first execution ID (if any)
EXEC_COUNT=$(echo "$RESPONSE_JSON" | jq -r '.result | length')
FIRST_EXEC_ID=$(echo "$RESPONSE_JSON" | jq -r '.[0].execId // empty')

SUMMARY=$(jq -n \
    --arg count "$EXEC_COUNT" \
    --arg exec_id "$FIRST_EXEC_ID" \
    '{action:"execution_list", count:$count, first_exec_id:$exec_id, symbol:"'"${SYMBOL:-all}"'"}')

# Forward to LLM_OUTPUT if set
if [[ -n "${LLM_OUTPUT:-}" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

exit 0
