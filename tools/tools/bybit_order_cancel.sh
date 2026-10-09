#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Order Cancellation Tool (V5 API)
# ------------------------------------------------------------
# Cancels an existing order (or batch cancels multiple orders) 
# using the Bybit V5 REST API.  Reads credentials from a `.env` 
# file, supports an optional proxy via `proxychains4`, and writes 
# a JSON summary to the `$LLM_OUTPUT` variable.
# ------------------------------------------------------------

set -euo pipefail

# ---- Load environment variables ------------------------------------
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# Default values
API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
PROXY_URL="${PROXY_URL:-}"
RECV_WINDOW="${RECV_WINDOW:-5000}"

# ---- Helper: generate request signature ---------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3
    local timestamp=$(date +%s%3N)   # milliseconds
    local string_to_sign="${timestamp}${method}${endpoint}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Usage ---------------------------------------------------------
usage() {
    cat <<EOF
Usage: $0 --order-id <ORDER_ID> [--order-ids <COMMA_SEPARATED_LIST>]

Cancel a single order or multiple orders (batch cancel).  Provide
either --order-id or --order-ids.  Optional proxy can be set via 
the PROXY_URL environment variable.
EOF
    exit 1
}

# ---- Parse arguments -----------------------------------------------
ORDER_ID=""
ORDER_IDS=""
while [[ $# -gt 0 ]]; do
    case "$1" in
        --order-id) ORDER_ID="$2"; shift 2 ;;
        --order-ids) ORDER_IDS="$2"; shift 2 ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

[[ -z "$ORDER_ID" ]] && [[ -z "$ORDER_IDS" ]] && usage

# ---- Build request body --------------------------------------------
if [[ -n "$ORDER_IDS" ]]; then
    # Batch cancel – Bybit expects a JSON array of order IDs
    BODY=$(jq -n --argjson ids "$(echo "$ORDER_IDS" | jq -R -s 'split(",")')"{orderIds:$ids})
else
    # Single cancel – only the orderId field is needed
    BODY=$(jq -n --arg id "$ORDER_ID" '{orderId:$id}')
fi

# ---- API details ---------------------------------------------------
ENDPOINT="/v5/order/cancel"
METHOD="POST"

# ---- Sign and send request -----------------------------------------
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$BODY")
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

# ---- Response handling ---------------------------------------------
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

# Extract result information
if [[ -n "$ORDER_IDS" ]]; then
    # Batch cancel response contains a list of results
    CANCELLED=$(echo "$RESPONSE_JSON" | jq -r '.result | map(.orderId // empty) | .[]')
else
    # Single cancel response contains orderId and status
    ORDER_ID_CANCELLED=$(echo "$RESPONSE_JSON" | jq -r '.result.orderId // empty')
    STATUS=$(echo "$RESPONSE_JSON" | jq -r '.result.status // empty')
    CANCELLED="$ORDER_ID_CANCELLED"
    STATUS_JSON=$(echo "$RESPONSE_JSON" | jq -c '{orderId:$ORDER_ID_CANCELLED, status:$STATUS}')
fi

# Build summary payload
if [[ -n "$ORDER_IDS" ]]; then
    SUMMARY=$(jq -n \
        --argjson cancelled "$CANCELLED" \
        '{action:"batch_cancel", cancelled_order_ids:$cancelled}')
else
    SUMMARY=$(jq -n \
        --arg order_id "$CANCELLED" \
        --arg status "$STATUS" \
        '{action:"cancel", orderId:$order_id, status:$status}')
fi

# Forward to LLM_OUTPUT if set
if [[ -n "${LLM_OUTPUT:-}" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

exit 0
