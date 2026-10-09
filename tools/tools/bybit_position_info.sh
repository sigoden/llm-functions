#!/usr/bin/env bash
set -e

# -------------------------------------------------------------------------
# Load environment variables from a .env file (if present)
# -------------------------------------------------------------------------
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
fi

# @describe Retrieve position information for a symbol (size, entry price,
#          mark price, unrealized PnL, liquidation price, etc.).
# @option --symbol!          Trading pair (e.g., BTCUSDT)
# @option --category=linear Market category (linear, inverse, spot)
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
    # ---- Validate required arguments -------------------------------------------------
    if [[ -z "$argc_symbol" || -z "$argc_category" ]]; then
        echo "Error: --symbol and --category are required." >&2
        exit 1
    fi

    local base_url="https://api.bybit.com"
    [[ "$BYBIT_TESTNET" == "true" ]] && base_url="https://api-testnet.bybit.com"
    local endpoint="/v5/position/list"

    # Query parameters
    local query="symbol=${argc_symbol}&category=${argc_category}&lang=en"

    # ---- Execute GET request via proxychains4 (if installed) -------------------------
    local curl_cmd=$(proxychains4 curl -s "${base_url}${endpoint}?${query}" \
        $( _sign_request GET "${endpoint}" "" ) \
        -G --data-urlencode "${query}")

    local resp=$($curl_cmd)

    # ---- Basic validation -------------------------------------------------------------
    local ret_code=$(echo "$resp" | jq -r '.retCode // -1' 2>/dev/null)
    if (( ret_code != 0 )); then
        local msg=$(echo "$resp" | jq -r '.retMsg // "Unknown error"' 2>/dev/null)
        echo "Error fetching position info: $msg (rc=$ret_code)" >&2
        exit 1
    fi

    # Bybit returns 'result' array; we expect at most one entry for a given symbol
    local positions=$(echo "$resp" | jq -r '.result[]')
    if [[ -z "$positions" || "$positions" == "null" ]]; then
        # No position found – still emit a neutral JSON so downstream can handle it
        local summary=$(jq -n '{symbol:$argc_symbol, category:$argc_category, status:"no_position"}')
        echo "$summary" >> "$LLM_OUTPUT"
        exit 0
    fi

    # Extract fields
    local size=$(echo "$positions" | jq -r '.size // 0')
    local entry_price=$(echo "$positions" | jq -r '.entry_price // 0')
    local mark_price=$(echo "$positions" | jq -r '.mark_price // 0')
    local unrealized_pnl=$(echo "$positions" | jq -r '.unrealized_PnL // 0')
    local liq_price=$(echo "$positions" | jq -r '.liquidation_price // 0')
    local leverage=$(echo "$positions" | jq -r '.leverage // 0')
    local margin_mode=$(echo "$positions" | jq -r '.margin_mode // "cross"')
    local position_idx=$(echo "$positions" | jq -r '.positionIdx // 0')

    # Determine side (long/short) based on size sign
    local side="neutral"
    if (( $(awk "BEGIN {print ($size>0)}") )); then
        side="long"
    elif (( $(awk "BEGIN {print ($size<0)}") )); then
        side="short"
    fi

    # Build JSON summary for LLM consumption
    local summary=$(jq -n \
        --arg symbol "$argc_symbol" \
        --arg category "$argc_category" \
        --argjson size "$size" \
        --argjson entry_price "$entry_price" \
        --argjson mark_price "$mark_price" \
        --argjson pnl "$unrealized_pnl" \
        --argjson liq_price "$liq_price" \
        --argjson lev "$leverage" \
        --arg side "$side" \
        --arg margin_mode "$margin_mode" \
        '{symbol:$symbol, category:$category, position_idx:$position_idx,
          side:$side, size:$size, entry_price:$entry_price,
          mark_price:$mark_price, unrealized_pnl:$pnl,
          liquidation_price:$liq_price, leverage:$lev,
          margin_mode:$margin_mode}')

    echo "$summary" >> "$LLM_OUTPUT"
}

# -------------------------------------------------------------------------
# Entry point – argc parsing
# -------------------------------------------------------------------------
eval "$(argc --argc-eval "$0" "$@")"
