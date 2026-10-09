#!/usr/bin/env bash
set -e

# -------------------------------------------------------------------------
# Load environment variables from a .env file (if present)
# -------------------------------------------------------------------------
if [[ -f .env ]]; then
    # Strip comments and empty lines, then export each KEY=VALUE pair
    export $(grep -v '^#' .env | xargs)
fi

# @describe Place a limit order on Bybit (instant order or conditional).
# @option --symbol!          Trading pair (e.g., BTCUSDT, ETHUSDT)
# @option --side=buy|sell    Order side
# @option --qty!             Order quantity (contracts)
# @option --price!           Limit price
# @option --category=linear Market category (linear, inverse, spot)
# @option --time_in_force=GTC   Time‑in‑force (GTC, IOC, FOK)
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
    # Validate required args
    if [[ -z "$argc_symbol" || -z "$argc_side" || -z "$argc_qty" || -z "$argc_price" ]]; then
        echo "Error: --symbol, --side, --qty, and --price are required." >&2
        exit 1
    fi

    # Build request payload
    local payload=$(jq -n \
        --arg symbol "$argc_symbol" \
        --arg side "$argc_side" \
        --arg qty "$argc_qty" \
        --arg price "$argc_price" \
        --arg category "$argc_category" \
        --arg time_in_force "$argc_time_in_force" \
        --arg client_oid "$argc_client_oid" \
        --argjson leverage "$argc_leverage" \
        '{symbol:$symbol, side:$side, qty:$qty, price:$price, category:$category,
          time_in_force:$time_in_force, client_oid:$client_oid, leverage:$leverage}')

    # API endpoint
    local base_url="https://api.bybit.com"
    [[ "$BYBIT_TESTNET" == "true" ]] && base_url="https://api-testnet.bybit.com"
    local endpoint="/v5/order/create"

    # Execute POST request via proxychains4 (if proxychains is installed)
    local curl_cmd=$(proxychains4 curl -s -X POST "${base_url}${endpoint}" \
        $( _sign_request POST "${endpoint}" "${payload}" ) \
        -d "${payload}")

    local resp=$($curl_cmd)

    # Simple validation – you can expand this with jq parsing as needed
    local rc=$(echo "$resp" | jq -r '.retCode // -1' 2>/dev/null)
    if (( rc != 0 )); then
        local msg=$(echo "$resp" | jq -r '.retMsg // "Unknown error"' 2>/dev/null)
        echo "Error placing order: $msg (rc=$rc)" >&2
        exit 1
    fi

    # Append a concise JSON summary for downstream LLM consumption
    local summary=$(jq -n \
        --arg sym "$argc_symbol" \
        --arg side "$argc_side" \
        --arg qty "$argc_qty" \
        --arg price "$argc_price" \
        --arg status "placed" \
        '{symbol:$sym, side:$side, qty:$qty, price:$price, status:$status}')
    echo "$summary" >> "$LLM_OUTPUT"
}

# -------------------------------------------------------------------------
# Entry point – argc parsing
# -------------------------------------------------------------------------
eval "$(argc --argc-eval "$0" "$@")"
