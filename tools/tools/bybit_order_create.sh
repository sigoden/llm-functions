#!/usr/bin/env bash
# ------------------------------------------------------------
# Bybit Order Creation Tool (V5 API)
# ------------------------------------------------------------
# This script places a new order using the Bybit V5 REST API.
# It reads credentials from a `.env` file in the same directory,
# supports an optional proxy via `proxychains4`, and outputs a
# JSON summary to the `$LLM_OUTPUT` variable.
# ------------------------------------------------------------

set -euo pipefail

# ---- Configuration ------------------------------------------------
# Load environment variables (API_KEY, API_SECRET, PROXY_URL, etc.)
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# Default values
CATEGORY="${CATEGORY:-spot}"
PROXY="${PROXY_URL:-}"
RECV_WINDOW="${RECV_WINDOW:-5000}"

# ---- Helper Functions ---------------------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3
    local timestamp=$(date +%s%3N)   # milliseconds
    local string_to_sign="${timestamp}${method}${endpoint}${body}"
    local sign=$(echo -n "$string_to_sign" | openssl dgst -sha256 -hex | sed 's/^.* //')
    echo "$sign"
}

# ---- Argument Parsing ---------------------------------------------
usage() {
    cat <<EOF
Usage: $0 --category <spot|linear|inverse|option> \\
          --symbol <SYMBOL> \\
          --side <Buy|Sell> \\
          --order-type <Limit|Market> \\
          --qty <QUANTITY> [--price <PRICE>] [--time-in-force <GoodTillCancel|ImmediateOrCancel>]
EOF
    exit 1
}

# Parse long options
TEMP=$(getopt -o '' -l category:,symbol:,side:,order-type:,qty:,price:,time-in-force: -n "$0" -- "$@")
if [[ $? -ne 0 ]]; then usage; fi
eval set -- "$TEMP"
while true; do
    case "$1" in
        --category)      CATEGORY="$2"; shift 2 ;;
        --symbol)        SYMBOL="$2"; shift 2 ;;
        --side)          SIDE="$2"; shift 2 ;;
        --order-type)    ORDER_TYPE="$2"; shift 2 ;;
        --qty)           QUANTITY="$2"; shift 2 ;;
        --price)         PRICE="$2"; shift 2 ;;
        --time-in-force) TIME_IN_FORCE="$2"; shift 2 ;;
        --) shift; break ;;
        *) echo "Unknown option: $1" ; usage ;;
    esac
done

# Validate required fields
[[ -z "${CATEGORY:-}" ]] && echo "Missing --category" ; usage
[[ -z "${SYMBOL:-}" ]] && echo "Missing --symbol" ; usage
[[ -z "${SIDE:-}" ]] && echo "Missing --side" ; usage
[[ -z "${ORDER_TYPE:-}" ]] && echo "Missing --order-type" ; usage
[[ -z "${QUANTITY:-}" ]] && echo "Missing --qty" ; usage

# Build request body
if [[ "$ORDER_TYPE" == "Limit" ]]; then
    if [[ -z "${PRICE:-}" ]]; then
        echo "Price is required for Limit orders"
        usage
    fi
    READABLE_BODY=$(jq -n \
        --arg category "$CATEGORY" \
        --arg symbol "$SYMBOL" \
        --arg side "$SIDE" \
        --arg order_type "$ORDER_TYPE" \
        --arg qty "$QUANTITY" \
        --arg price "$PRICE" \
        --arg time_in_force "${TIME_IN_FORCE:-GoodTillCancel}" \
        '{category:$category, symbol:$symbol, side:$side, type:$order_type, qty:$qty, price:$price, timeInForce:$time_in_force}')
else
    READABLE_BODY=$(jq -n \
        --arg category "$CATEGORY" \
        --arg symbol "$SYMBOL" \
        --arg side "$SIDE" \
        --arg qty "$QUANTITY" \
        '{category:$category, symbol:$symbol, side:$side, type:$ORDER_TYPE, qty:$qty}')
fi

# API endpoint
ENDPOINT="/v5/order/create"
METHOD="POST"

# Sign request
SIGNATURE=$(_sign_request "$METHOD" "$ENDPOINT" "$READABLE_BODY")

# Prepare curl command
CURL_CMD=(curl -s -S -X POST "https://api.bybit.com$ENDPOINT")
CURL_CMD+=(-H "Content-Type: application/json")
CURL_CMD+=(-H "X-BAPI-API-KEY: $API_KEY")
CURL_CMD+=(-H "X-BAPI-SIGN: $SIGNATURE")
CURL_CMD+=(-H "X-BAPI-TIMESTAMP: $(date +%s%3N)")
CURL_CMD+=(-H "X-BAPI-RECV-WINDOW: $RECV_WINDOW")
if [[ -n "$PROXY" ]]; then
    # If a proxy URL is defined, use proxychains4
    PROXYCHAINS="proxychains4 ${CURL_CMD[*]}"
    OUTPUT=$($PROXYCHAINS)
else
    OUTPUT=$( "${CURL_CMD[@]}" )
fi

# ---- Response Handling --------------------------------------------
# Extract JSON payload (or error)
RESPONSE_JSON=$(echo "$OUTPUT" | jq -e . 2>/dev/null || true)

if [[ -z "$RESPONSE_JSON" ]]; then
    echo "Error: Empty or invalid response from Bybit API"
    echo "Raw response: $OUTPUT"
    exit 1
fi

# Check for API error code
RET_CODE=$(echo "$RESPONSE_JSON" | jq -r '.retCode // 0')
if [[ "$RET_CODE" -ne 0 ]]; then
    RET_MSG=$(echo "$RESPONSE_JSON" | jq -r '.retMsg // "Unknown error"')
    echo "Bybit API error $RET_CODE: $RET_MSG"
    exit 1
fi

# Extract fields we care about
ORDER_ID=$(echo "$RESPONSE_JSON" | jq -r '.result.orderId // empty')
ORDER_STATUS=$(echo "$RESPONSE_JSON" | jq -r '.result.status // empty')
ORDER_INFO=$(echo "$RESPONSE_JSON" | jq -r '.result | {orderId, status, symbol, side, type, qty, price, avgPrice, cumQty, cumCost, createdTime, updatedTime}')

# Build summary
SUMMARY=$(jq -n \
    --argjson order "$ORDER_INFO" \
    --arg order_id "$ORDER_ID" \
    --arg status "$ORDER_STATUS" \
    '{orderId:$order_id, status:$status, details:$order}')

# Output to LLM_OUTPUT for downstream consumption
LLM_OUTPUT="${LLM_OUTPUT:-}"
if [[ -n "$LLM_OUTPUT" ]]; then
    printf '%s\n' "$SUMMARY" >> "$LLM_OUTPUT"
fi

# Exit successfully
exit 0
