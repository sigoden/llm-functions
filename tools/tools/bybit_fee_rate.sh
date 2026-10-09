#!/usr/bin/env bash
# Bybit Fee Rate Lookup Tool
# Usage: ./bybit_fee_rate.sh [--category PERPETUAL|OPTION] [--symbol BTCUSDT]

set -euo pipefail

# Load .env if present
if [[ -f .env ]]; then export $(grep -v '^#' .env | xargs); fi

API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
CATEGORY="${CATEGORY:-PERPETUAL}"
SYMBOL="${SYMBOL:-}"
PROXY_URL="${PROXY_URL:-}"

if [[ -z "$API_KEY" || -z "$API_SECRET" ]]; then
  echo "Missing API credentials" >&2; exit 1
fi

# ---- Helper: sign request -------------------------------------------------
_sign_request() {
  local endpoint=$1 method=${2:-GET} params=${3:-}
  local ts=$(date +%s000)
  local query=""
  [[ -n "$params" ]] && query="?${params}"
  local body="${method}${endpoint}${ts}${query}"
  local sig=$(echo -n "$body" | openssl dgst -sha256 -hex -hmac "$API_SECRET" | cut -d' ' -f2)
  echo "${ts}${query}&signature=${sig}"
}

# ---- Build request ---------------------------------------------------------
BASE_URL="https://api.bybit.com/v5"
ENDPOINT="/feeRate"
PARAMS="category=${CATEGORY}"
[[ -n "$SYMBOL" ]] && PARAMS="${PARAMS}&symbol=${SYMBOL}"

QUERY=$(_sign_request "$ENDPOINT" GET "$PARAMS")

# Build curl command (add proxy if configured)
CURL_CMD=(curl -sS -X GET "${BASE_URL}${ENDPOINT}" -G $PARAMS \
  -H "X-API-KEY: $API_KEY" -H "X-API-SIGN: $QUERY")
if [[ -n "$PROXY_URL" ]]; then
  CURL_CMD=("proxychains4" "${CURL_CMD[@]}")
fi

# Execute request
RESPONSE=$( "${CURL_CMD[@]}" )
RET_CODE=$(echo "$RESPONSE" | jq -r '.retCode')
if [[ "$RET_CODE" -ne 0 ]]; then
  echo "Error $RET_CODE: $(echo "$RESPONSE" | jq -r '.retMsg')" >&2
  exit 1
fi

# Extract fee rate
FEE_RATE=$(echo "$RESPONSE" | jq -c '.result')
# Append a concise JSON summary to the global output variable
LLM_OUTPUT+=$(printf '%s' \
  "{\"tool\":\"bybit_fee_rate\",\"category\":\"$CATEGORY\",\"symbol\":\"$SYMBOL\",\"fee_rate\":$FEE_RATE}")
