#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Order Amendment Tool (V5 API)
# ------------------------------------------------------------
# Amends an existing order using the Bybit V5 REST API.
# Reads credentials from a `.env` file, supports an optional proxy,
# and writes a JSON summary to `$LLM_OUTPUT`.
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

# ---- Helper: request signature ---------------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3
    local timestamp=$(date +%s%3N)   # ms
    local string_to_sign="${timestamp}${method}${endpoint}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Usage ------------------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 --order-id <ID> [--price <NEW_PRICE>] [--qty <NEW_QTY>] [--time-in-force <NEW_TIF>]
EOF
    exit 1
}

# ---- Parse arguments --------------------------------------------------
while [[ $# -gt 0 ]]; do
    case "$1" in
        --order-id) ORDER_ID="$2"; shift 2 ;;
        --price)    NEW_PRICE="$2"; shift 2 ;;
        --qty)      NEW_QTY="$2"; shift 2 ;;
        --time-in-force) NEW_TIF="$2"; shift 2 ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

[[ -z "${ORDER_ID:-}" ]] && echo "Missing --order-id" ; usage

# ---- Build request body -----------------------------------------------
# Bybit expects the whole order payload again, but only the fields you wish to change need to be sent.
# We'll construct a minimal JSON containing the fields we may modify.
if [[ -n "${NEW_PRICE:-}" ]] || [[ -n "${NEW_QTY:-}" ]] || [[ -n "${NEW_TIF:-}" ]]; then
    # Build JSON with only the provided fields
    AMEND_JSON=$(jq -n \
        --arg orderId "$ORDER_ID" \
        $( [[ -n "${NEW_PRICE:-}" ]] && echo "--arg price \"$NEW_PRICE\"" ) \
        $( [[ -n "${NEW_QTY:-}" ]] && echo "--arg qty \"$NEW_QTY\"" ) \
        $( [[ -n "${NEW_TIF:-}" ]] && echo "--arg timeInForce \"$NEW_TIF\"" ) \
        '{orderId:$orderId, price:($price // empty), qty:($qty // empty), timeInForce:($timeInForce // empty)}')
else
    # No changes supplied – just exit with a helpful message
    echo "No amendment parameters provided."
    exit 1
fi

# ---- API details -------------------------------------------------------
ENDPOINT="/v5/order/amend"
METHOD="POST"

# ---- Sign and send request --------------------------------------------
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$AMEND_JSON")
CURL_CMD=(curl -s -S -X POST "https://api.bybit.com$ENDPOINT")
CURL_CMD+=(-H "Content-Type: application/json")
CURL_CMD+=(-H "X-BAPI-API-KEY: $API_KEY")
CURL_CMD+=(-H "X-BAPI-SIGN: $SIGNATURE")
CURL_CMD+=(-H "X-BAPI-TIMESTAMP: $(date +%s%3N)")
CURL_CMD+=(-H "X-BAPI-RECV-WINDOW: $RECV_WINDOW")
if [[ -n "$PROXY_URL" ]]; then
    # Use proxychains4 if a proxy is defined
    PROXYCMD="proxychains4 ${CURL_CMD[*]}"
    RESPONSE=$($PROXYCMD)
else
    RESPONSE=$( "${CURL_CMD[@]}" )
fi

# ---- Response handling ------------------------------------------------
# Validate JSON
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

# Extract relevant fields
ORDER_ID_AMENDED=$(echo "$RESPONSE_JSON" | jq -r '.result.orderId // empty')
STATUS=$(echo "$RESPONSE_JSON" | jq -r '.result.status // empty')

# Build summary JSON
SUMMARY=$(jq -n \
    --arg orderId "$ORDER_ID_AMENDED" \
    --arg status "$STATUS" \
    '{orderId:$orderId, status:$status}')

# Forward to LLM_OUTPUT if set
if [[ -n "${LLM_OUTPUT:-}" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

exit 0
