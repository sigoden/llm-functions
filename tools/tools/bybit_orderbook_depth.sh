#!/usr/bin/env bash
set -e

# -------------------------------------------------------------------------
# Load environment variables from a .env file (if present)
# -------------------------------------------------------------------------
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
fi

# @describe Retrieve order‑book depth for a symbol, compute spread,
#          imbalance and classify liquidity zone.
# @option --symbol!          Trading pair (e.g., BTCUSDT)
# @option --category=linear Market category (linear, inverse, spot)
# @option --limit=20         Number of price levels to fetch (default 20)
# @option --proxy_host=      (optional) host for proxychains4
# @option --proxy_port=      (optional) port for proxychains4
# @env BYBIT_TESTNET=false   Switch to test‑net when true
# @env LLM_OUTPUT=/dev/stdout Destination for LLM context

# -------------------------------------------------------------------------
# Helper – sign request (HMAC‑SHA256) for Bybit V5 REST API.
# -------------------------------------------------------------------------
_sign_request() {
    local method=$1
    local endpoint=$2
    local body=$3

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
    if [[ -z "$argc_symbol" || -z "$argc_category" ]]; then
        echo "Error: --symbol and --category are required." >&2
        exit 1
    fi

    local limit="${argc_limit:-20}"
    local base_url="https://api.bybit.com"
    [[ "$BYBIT_TESTNET" == "true" ]] && base_url="https://api-testnet.bybit.com"
    local endpoint="/v5/orderBook/L2"

    # Build query string
    local query="symbol=${argc_symbol}&category=${argc_category}&limit=${limit}"
    local url="${base_url}${endpoint}?${query}"

    # Execute GET request via proxychains4 (if installed)
    local curl_cmd=$(proxychains4 curl -s "${url}" \
        $( _sign_request GET "${endpoint}" "" ) \
        -G --data-urlencode "${query}")

    local resp=$($curl_cmd)

    # Basic validation
    local ret_code=$(echo "$resp" | jq -r '.retCode // -1' 2>/dev/null)
    if (( ret_code != 0 )); then
        local msg=$(echo "$resp" | jq -r '.retMsg // "Unknown error"' 2>/dev/null)
        echo "Error fetching order book: $msg (rc=$ret_code)" >&2
        exit 1
    fi

    # Extract top levels (assume array 'result' contains bids and asks)
    local bids=$(echo "$resp" | jq -r '.result.bids // []')
    local asks=$(echo "$resp" | jq -r '.result.asks // []')

    # Defensive check – need at least one bid and one ask
    if [[ -z "$bids" || -z "$asks" ]]; then
        echo "Error: insufficient depth data." >&2
        exit 1
    fi

    # Compute best bid/ask and spread
    local best_bid_price=$(echo "$bids" | jq -r '.[0][0] // 0')
    local best_ask_price=$(echo "$asks" | jq -r '.[0][0] // 0')
    local spread=$(awk "BEGIN {printf \"%.8f\", ($best_ask_price-$best_bid_price)/$best_bid_price*100}")

    # Compute total quantity at best level and imbalance
    local total_bid_qty=$(echo "$bids" | jq '[.[] | .[1]] | add')
    local total_ask_qty=$(echo "$asks" | jq '[.[] | .[1]] | add')
    local imbalance=$(awk "BEGIN {printf \"%.2f\", (${total_bid_qty}-${total_ask_qty})/(${total_bid_qty}+${total_ask_qty})*100}")

    # Classify liquidity zone
    local liquidity_zone="thin"
    if (( $(awk "BEGIN {print ($spread < 0.05)}") )); then
        liquidity_zone="thin"
    elif (( $(awk "BEGIN {print ($spread < 0.15)}") )); then
        liquidity_zone="moderate"
    else
        liquidity_zone="thick"
    fi

    # Assemble JSON payload for LLM consumption
    local summary=$(jq -n \
        --arg symbol "$argc_symbol" \
        --arg category "$argc_category" \
        --argjson spread "$spread" \
        --argjson imbalance "$imbalance" \
        --arg liquidity "$liquidity_zone" \
        '{symbol:$symbol, category:$category, spread_pct:$spread, imbalance_pct:$imbalance, liquidity_zone:$liquidity}')

    echo "$summary" >> "$LLM_OUTPUT"
}

# -------------------------------------------------------------------------
# Entry point – argc parsing
# -------------------------------------------------------------------------
eval "$(argc --argc-eval "$0" "$@")"
