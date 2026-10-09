#!/usr/bin/env bash
# Bybit Position Liquidation Lookup
# Usage: ./bybit_position_liquidation.sh [--category PERPETUAL|OPTION] [--symbol BTCUSDT]

set -euo pipefail

# Load .env if present
if [[ -f .env ]]; then export $(grep -v '^#' .env | xargs); fi

API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
CATEGORY="${CATEGORY:-PERPETUAL}"
SYMBOL="${SYMBOL:-}"
PROXY_URL="${PROXY_URL:-}"

if [[ -z "$API_KEY" || -z "$API_SECRET" ]]; then
  echo "Error: API credentials not found in environment." >&2
  exit 1
fi

# ---- Helper: sign request -------------------------------------------------
_sign_request() {
  local endpoint=$1 method=${2:-GET} params=${3:-}
  local ts=$(date +%s000)
  local query=""
  if [[ -n "$params" ]]; then query="?${params}"; fi
  local body="${method}${endpoint}${ts}${query}"
  local sig=$(echo -n "$body" | openssl dgst -sha256 -hex -hmac "$API_SECRET" | cut -d' ' -f2)
  echo "${ts}${query}&signature=${sig}"
}

# ---- Build request ---------------------------------------------------------
BASE_URL="https://api.bybit.com/v5"
ENDPOINT="/position/liquidation"

PARAMS=""
[[ -n "$SYMBOL" ]] && PARAMS="symbol=${SYMBOL}"
[[ -n "$CATEGORY" ]] && PARAMS="${PARAMS}&category=${CATEGORY}"

QUERY=$(_sign_request "$ENDPOINT" GET "$PARAMS")

# Build curl command (add proxy if configured)
CURL_CMD=(curl -sS -X GET "${BASE_URL}${ENDPOINT}" -G "$PARAMS" \
  -H "X-API-KEY: $API_KEY" -H "X-API-SIGN: $QUERY")
if [[ -n "$PROXY_URL" ]]; then
  CURL_CMD=("proxychains4" "${CURL_CMD[@]}")
fi

# Execute request
RESPONSE=$( "${CURL_CMD[@]}" )
RET_CODE=$(echo "$RESPONSE" | jq -r '.retCode')

if [[ "$RET_CODE" -ne 0 ]]; then
  ERROR_MSG=$(echo "$RESPONSE" | jq -r '.retMsg // "Unknown error"')
  echo "Error $RET_CODE: $ERROR_MSG" >&2
  exit 1
fi

# Extract liquidation info
LIQUIDATIONS=$(echo "$RESPONSE" | jq -c '.result[] | {
  time: .t,
  symbol: .s,
  positionId: .pi,
  liquidationPrice: .lp,
  liqOrderId: .lo
}')

# Append a concise JSON summary to the global output variable
LLM_OUTPUT+=$(printf '%s' \
  "{\"tool\":\"bybit_position_liquidation\",\"category\":\"$CATEGORY\",\"symbol\":\"$SYMBOL\",\"liquidations\":$LIQUIDATIONS}")
