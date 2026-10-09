#!/data/data/com.termux/files/usr/bin/env bash
# =============================================================================
# crypto-price.sh – fetch the current USD price of a cryptocurrency from Bybit
#
# @describe Fetch the current USD price of a cryptocurrency from Bybit.
# @arg coin! The cryptocurrency name/symbol (e.g. bitcoin, BTC, ETH)
#
# Usage:
#   ./crypto-price.sh <coin>
#
#   <coin> can be any of the common symbols (bitcoin, ethereum, dogecoin, …)
#   The lookup is case‑insensitive and supports both full names and common
#   abbreviations (BTC, ETH, DOGE, etc.).
#
#   The script queries Bybit's public endpoint:
#       https://api.bybit.com/v5/market/tickers?category=spot&symbol=<SYMBOL>USDT
#
#   It prints a nicely‑formatted line like:
#       💰 Bitcoin Price: $43250.12
#
#   Requires: curl, python3 (for JSON parsing fallback) or jq (optional)
# =============================================================================

set -euo pipefail   # Fail fast on errors, undefined vars, and pipe failures

# ---------------------------------------------------------------------------
# Helper: print a friendly error message and exit with a non‑zero status
# ---------------------------------------------------------------------------
die() {
    printf 'Error: %s\n' "$*" >&2
    exit 1
}

# ---------------------------------------------------------------------------
# Helper: map a user‑friendly coin name to the Bybit trading‑pair suffix
# ---------------------------------------------------------------------------
coin_to_pair() {
    local name="${1,,}"   # lowercase for case‑insensitive matching

    case "$name" in
        bitcoin|btc)   echo "BTC"  ;;
        ethereum|eth)  echo "ETH"  ;;
        dogecoin|doge) echo "DOGE" ;;
        cardano|ada)   echo "ADA"  ;;
        solana|sol)    echo "SOL"  ;;
        ripple|xrp)    echo "XRP"  ;;
        litecoin|ltc)  echo "LTC"  ;;
        bitcoin-cash|bch) echo "BCH" ;;
        *) die "Unsupported coin: $1. Supported coins are: bitcoin, ethereum, dogecoin, cardano, solana, ripple, litecoin, bitcoin-cash." ;;
    esac
}

# ---------------------------------------------------------------------------
# Helper: fetch price from Bybit and extract the last traded price
# ---------------------------------------------------------------------------
fetch_price() {
    local symbol="$1"   # e.g. "BTC"
    local url="https://api.bybit.com/v5/market/tickers?category=spot&symbol=${symbol}USDT"

    # Use curl with a short timeout and silent mode
    local json
    json=$(curl -fsS --max-time 10 "$url") || die "Failed to reach Bybit API"

    # Try jq first (if installed); otherwise fall back to a tiny Python parser
    local price
    if command -v jq >/dev/null 2>&1; then
        price=$(jq -r '.result.list[0].lastPrice // empty' <<<"$json") || die "Failed to parse JSON with jq"
    else
        price=$(python3 -c "import sys, json; data=json.load(sys.stdin); print(data.get('result','').get('list',[{}])[0].get('lastPrice',''))" <<<"$json") || die "Failed to parse JSON with Python"
    fi

    [[ -n "$price" ]] || die "No price found for symbol $symbol"
    printf '%s' "$price"
}

# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------
main() {
    # -----------------------------------------------------------------------
    # Argument handling
    # -----------------------------------------------------------------------
    if [[ $# -ne 1 ]]; then
        cat <<EOF >&2
Usage: $0 <coin>

Fetch the current USD price of a cryptocurrency from Bybit.

Examples:
  $0 bitcoin
  $0 ethereum
  $0 dogecoin
EOF
        exit 1
    fi

    local raw_coin="$1"
    local pair_symbol
    pair_symbol=$(coin_to_pair "$raw_coin") || exit 1   # exits on error via die()

    # -----------------------------------------------------------------------
    # Fetch and display the price
    # -----------------------------------------------------------------------
    local price
    price=$(fetch_price "$pair_symbol")

    # Capitalise the first letter of each word for pretty output
    local pretty_coin
    pretty_coin=$(echo "$raw_coin" | awk '{for(i=1;i<=NF;i++) $i=tolower(substr($i,1,1)) toupper(substr($i,2)); printf "%s ", $i} END{print}' | sed 's/ $//')

    printf '💰 %s Price: $%s\n' "$pretty_coin" "$price"
}

# ---------------------------------------------------------------------------
# Run the script
# ---------------------------------------------------------------------------
main "$@"
