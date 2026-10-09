#!/usr/bin/env bash
# Bybit Funding Rate History Tool
# Usage: ./bybit_funding_rate.sh [--symbol BTCUSDT] [--category PERPETUAL|OPTION]
#        [--start 1609459200] [--end 1704067200]

set -euo pipefail

# Load .env if present
if [[ -f .env ]]; then export $(grep -v '^#' .env | xargs); fi

API_KEY="${API_KEY:-}"
API_SECRET="${API_SECRET:-}"
SYMBOL="${SYMBOL:-}"
CATEGORY="${CATEGORY:-PERPETUAL}"
START="${START:-}"
END="${END:-}"
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
ENDPOINT="/fundingRate/history"
PARAMS="category=${CATEGORY}&symbol=${SYMBOL}"
[[ -n "$START" ]] && PARAMS="${PARAMS}&startTime=${START}"
[[ -n "$END" ]]   && PARAMS="${PARAMS}&endTime=${END}"

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

# Extract funding history
RESULT=$(echo "$RESPONSE" | jq -c '.result[] | {fundingRate: .fp, fundingTime: .t}')
# Append a concise JSON summary to the global output variable
LLM_OUTPUT+=$(printf '%s' \
  "{\"tool\":\"bybit_funding_rate\",\"symbol\":\"$SYMBOL\",\"category\":\"$CATEGORY\",\"funding_history\":$RESULT}")
