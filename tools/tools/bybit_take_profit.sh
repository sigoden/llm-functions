#!/usr/bin/env bash
set -e

# -------------------------------------------------------------------------
# Load environment variables from a .env file (if present)
# -------------------------------------------------------------------------
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
fi

# @describe Place a Take‑Profit (conditional) order on Bybit.
# @option --symbol!          Trading pair (e.g., BTCUSDT, ETHUSDT)
# @option --side=buy|sell    Order side – opposite of your current position
# @option --qty!             Quantity (contracts)
# @option --stop_px!         Take‑profit trigger price (the price at which the order is activated)
# @option --category=linear Market category (linear, inverse, spot)
# @option --client_oid!      Client‑provided order ID (unique)
# @option --leverage!        Leverage to use (if applicable)
# @env BYBIT_TESTNET=false   Switch to test‑net when true
# @env LLM_OUTPUT=/dev/stdout Destination for LLM context

# -------------------------------------------------------------------------
# Helper – sign request (HMAC‑SHA256) for Bybit V5 REST API.
# -------------------------------------------------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3

    # NOTE: In production load API_KEY and API_SECRET securely.
    local api_key="${BYBIT_API_KEY:-YOUR_API_KEY}"
    local api_secret="${BYBIT_API_SECRET:-YOUR_API_SECRET}"

    local timestamp=$(date +%s000)   # ms precision
    local prehash="${timestamp}${body}"
    local signature=$(echo -n "${prehash}" | openssl dgst -sha256 -hmac "${api_secret}" -binary | hexdump -v -e '%02x' | tr -d '\n')

    echo "-H \"X-API-KEY: ${api_key}\" \
          -H \"X-API-SIGN: ${signature}\" \
          -H \"X-API-TIMESTAMP: ${timestamp}\" \
          -H \"Content-Type: application/json\""
}

# -------------------------------------------------------------------------
# Main logic
# -------------------------------------------------------------------------
main() {
    # Validate required arguments
    if [[ -z "$argc_symbol" || -z "$argc_side" || -z "$argc_qty" || -z "$argc_stop_px" ]]; then
        echo "Error: --symbol, --side, --qty, and --stop_px are required." >&2
        exit 1
    fi

    # Build request payload for a TakeProfit order
    local payload=$(jq -n \
        --arg symbol "$argc_symbol" \
        --arg side "$argc_side" \
        --arg qty "$argc_qty" \
        --arg category "$argc_category" \
        --arg client_oid "$argc_client_oid" \
        --argjson stop_px "$argc_stop_px" \
        --argjson leverage "$argc_leverage" \
        '{symbol:$symbol, side:$side, qty:$qty, category:$category,
          order_type:"TakeProfit", stop_px:$stop_px,
          client_oid:$client_oid, leverage:$leverage}')

    # API endpoint
    local base_url="https://api.bybit.com"
    [[ "$BYBIT_TESTNET" == "true" ]] && base_url="https://api-testnet.bybit.com"
    local endpoint="/v5/order/create"

    # Execute POST request via proxychains4 (if installed)
    local curl_cmd=$(proxychains4 curl -s -X POST "${base_url}${endpoint}" \
        $( _sign_request POST "${endpoint}" "${payload}" ) \
        -d "${payload}")

    local resp=$($curl_cmd)

    # Basic validation
    local rc=$(echo "$resp" | jq -r '.retCode // -1' 2>/dev/null)
    if (( rc != 0 )); then
        local msg=$(echo "$resp" | jq -r '.retMsg // "Unknown error"' 2>/dev/null)
        echo "Error placing TakeProfit order: $msg (rc=$rc)" >&2
        exit 1
    fi

    # Append concise JSON summary for LLM consumption
    local summary=$(jq -n \
        --arg sym "$argc_symbol" \
        --arg side "$argc_side" \
        --arg qty "$argc_qty" \
        --arg stop_px "$argc_stop_px" \
        '{symbol:$sym, side:$side, qty:$qty, order_type:"TakeProfit", stop_px:$stop_px, status:"placed"}')
    echo "$summary" >> "$LLM_OUTPUT"
}

# -------------------------------------------------------------------------
# Entry point – argc parsing
# -------------------------------------------------------------------------
eval "$(argc --argc-eval "$0" "$@")"
