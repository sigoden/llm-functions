#!/usr/bin/env bash
set -e

# -------------------------------------------------------------------------
# Load environment variables from a .env file (if present)
# -------------------------------------------------------------------------
if [[ -f .env ]]; then
    export $(grep -v '^#' .env | xargs)
fi

# @describe Retrieve recent kline (candlestick) data for a symbol and interval,
#          compute price‑change %, green/red‑bar counts, average volume,
#          high‑low range % and classify momentum, candle‑bias and volatility.
# @option --symbol!          Trading pair (e.g., BTCUSDT)
# @option --interval!        Kline interval (1m,5m,1h,4h,1d, etc.)
# @option --limit=100        Number of klines to fetch (default 100)
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
    if [[ -z "$argc_symbol" || -z "$argc_interval" ]]; then
        echo "Error: --symbol and --interval are required." >&2
        exit 1
    fi

    local limit="${argc_limit:-100}"
    local base_url="https://api.bybit.com"
    [[ "$BYBIT_TESTNET" == "true" ]] && base_url="https://api-testnet.bybit.com"
    local endpoint="/v5/kline/list"

    # ---- Build query string -----------------------------------------------------------
    local query="symbol=${argc_symbol}&category=${argc_category}&interval=${argc_interval}&limit=${limit}"
    local url="${base_url}${endpoint}?${query}"

    # ---- Execute GET request via proxychains4 (if installed) -------------------------
    local curl_cmd=$(proxychains4 curl -s "${url}" \
        $( _sign_request GET "${endpoint}" "" ) \
        -G --data-urlencode "${query}")

    local resp=$($curl_cmd)

    # ---- Basic validation -------------------------------------------------------------
    local ret_code=$(echo "$resp" | jq -r '.retCode // -1' 2>/dev/null)
    if (( ret_code != 0 )); then
        local msg=$(echo "$resp" | jq -r '.retMsg // "Unknown error"' 2>/dev/null)
        echo "Error fetching klines: $msg (rc=$ret_code)" >&2
        exit 1
    fi

    # ---- Parse klines -----------------------------------------------------------------
    # Bybit returns an array 'result' where each element has:
    #   open, high, low, close, volume, turnover, turnover_value, contract_code,
    #   time, interval, settle_funding_rate, mark_price, ic_mark_price, ic_mark_price_24h
    local klines=$(echo "$resp" | jq -r '.result[]')

    # Arrays to accumulate statistics
    local changes=()
    local green=0
    local red=0
    local total_vol=0
    local sum_range=0

    while IFS= read -r line; do
        # Extract fields
        local open=$(echo "$line" | jq -r '.open // 0')
        local close=$(echo "$line" | jq -r '.close // 0')
        local volume=$(echo "$line" | jq -r '.volume // 0')
        local high=$(echo "$line" | jq -r '.high // 0')
        local low=$(echo "$line" | jq -r '.low // 0')

        # price change %
        local change=$(awk "BEGIN {printf \"%.6f\", (${close}-${open})/${open}*100}")
        changes+=("$change")

        # count green vs red
        if (( $(awk "BEGIN {print ($close>=$open)}") )); then
            ((green++))
        else
            ((red++))
        fi

        # accumulate volume
        total_vol=$(awk "BEGIN {print ${total_vol}+${volume}}")

        # high‑low range %
        local range=$(awk "BEGIN {printf \"%.6f\", (${high}-${low})/${open}*100}")
        sum_range=$(awk "BEGIN {print ${sum_range}+${range}}")
    done <<< "$klines"

    # ---- Compute aggregates ------------------------------------------------------------
    local avg_change=$(awk "BEGIN {sum=0; for(v in ${changes[@]}) sum+=v; print sum/length(${changes[@]})}")
    local green_pct=$(awk "BEGIN {printf \"%.2f\", ${green}/${limit}*100}")
    local red_pct=$(awk "BEGIN {printf \"%.2f\", ${red}/${limit}*100}")
    local avg_vol=$(awk "BEGIN {printf \"%.2f\", ${total_vol}/${limit}}")
    local avg_range=$(awk "BEGIN {printf \"%.2f\", ${sum_range}/${limit}}")

    # ---- Classify momentum, candle‑bias and volatility ---------------------------------
    local momentum="neutral"
    if (( $(awk "BEGIN {print (${avg_change}>0)}") )); then
        momentum="up"
    else
        momentum="down"
    fi

    local candle_bias="neutral"
    if (( $(awk "BEGIN {print (${green_pct}>50)}") )); then
        candle_bias="bullish"
    else
        candle_bias="bearish"
    fi

    local volatility="low"
    if (( $(awk "BEGIN {print (${avg_range}>0.02)}") )); then
        if (( $(awk "BEGIN {print (${avg_range}>0.05)}") )); then
            volatility="high"
        else
            volatility="medium"
        fi
    fi

    # ---- Build JSON summary for LLM consumption ---------------------------------------
    local summary=$(jq -n \
        --arg symbol "$argc_symbol" \
        --arg interval "$argc_interval" \
        --argjson avg_change "$avg_change" \
        --argjson green_pct "$green_pct" \
        --argjson red_pct "$red_pct" \
        --argjson avg_vol "$avg_vol" \
        --argjson avg_range "$avg_range" \
        --arg momentum "$momentum" \
        --arg candle_bias "$candle_bias" \
        --arg volatility "$volatility" \
        '{symbol:$symbol, interval:$interval, limit:$limit,
          avg_price_change_pct:$avg_change,
          green_bar_pct:$green_pct, red_bar_pct:$red_pct,
          avg_volume:$avg_vol, avg_high_low_range_pct:$avg_range,
          momentum:$momentum, candle_bias:$candle_bias, volatility:$volatility}')

    echo "$summary" >> "$LLM_OUTPUT"
}

# -------------------------------------------------------------------------
# Entry point – argc parsing
# -------------------------------------------------------------------------
eval "$(argc --argc-eval "$0" "$@")"
