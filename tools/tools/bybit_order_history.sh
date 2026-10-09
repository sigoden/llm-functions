#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Order History Retrieval Tool (V5 API)
# ------------------------------------------------------------
# Fetches the list of recent orders for the authenticated account.
# Supports optional pagination via `cursor` and writes a concise
# JSON summary to the `$LLM_OUTPUT` variable.
# ------------------------------------------------------------

set -euo pipefail

# ---- Load environment ------------------------------------------------
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# Default parameters
API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
PROXY_URL="${PROXY_URL:-}"
RECV_WINDOW="${RECV_WINDOW:-5000}"
CATEGORY="${CATEGORY:-spot}"   # can be spot, linear, inverse, option

# ---- Helper: generate signature ---------------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3
    local timestamp=$(date +%s%3N)   # milliseconds
    local string_to_sign="${timestamp}${method}${endpoint}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Argument parsing -------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 [--category <spot|linear|inverse|option>] [--cursor <cursor>]
Fetch order history. Optional cursor for pagination.
EOF
    exit 1
}

while [[ $# -gt 0 ]]; do
    case "$1" in
        --category) CATEGORY="$2"; shift 2 ;;
        --cursor)   CURSOR="$2"; shift 2 ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

# ---- Build request body (empty for order history) --------------------
BODY=""

# ---- API details ----------------------------------------------------
ENDPOINT="/v5/order/history"
METHOD="GET"

# ---- Sign and send request -------------------------------------------
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$BODY")
CURL_CMD=(curl -s -S -X GET "https://api.bybit.com$ENDPOINT?category=${CATEGORY}")
CURL_CMD+=(-H "X-BAPI-API-KEY: $API_KEY")
CURL_CMD+=(-H "X-BAPI-SIGN: $SIGNATURE")
CURL_CMD+=(-H "X-BAPI-TIMESTAMP: $(date +%s%3N)")
CURL_CMD+=(-H "X-BAPI-RECV-WINDOW: $RECV_WINDOW")
if [[ -n "$PROXY_URL" ]]; then
    PROXYCMD="proxychains4 ${CURL_CMD[*]}"
    RESPONSE=$($PROXYCMD)
else
    RESPONSE=$( "${CURL_CMD[@]}" )
fi

# ---- Response handling -----------------------------------------------
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

# Extract a short summary – count of orders and first order ID (if any)
ORDER_COUNT=$(echo "$RESPONSE_JSON" | jq -r '.result | length')
FIRST_ORDER_ID=$(echo "$RESPONSE_JSON" | jq -r '.[0].orderId // empty')

SUMMARY=$(jq -n \
    --arg count "$ORDER_COUNT" \
    --arg first_id "$FIRST_ORDER_ID" \
    '{action:"order_history", count:$count, first_order_id:$first_id, category:"'"$CATEGORY"'"}')

# Forward to LLM_OUTPUT if set
if [[ -n "${LLM_OUTPUT:-}" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

exit 0
