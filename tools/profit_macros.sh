#!/data/data/com.termux/files/usr/bin/env bash
# ==============================================================================
# profit_macros.sh – Ready‑to‑run macro shortcuts for micro_profit.py
# @describe Ready‑to‑run macro shortcuts for micro_profit.py
# ==============================================================================
# Each function wraps a common micro‑profit configuration.
# To use, source this file or call the functions directly, e.g.:
#     source profit_macros.sh && macro_basic_scalp
# ==============================================================================

# ------------------------------------------------------------------------------
# 🛠️ System Check Helper
# ------------------------------------------------------------------------------
check_environment() {
  if ! command -v python3 &>/dev/null; then
    echo "ERROR: python3 is not installed or not in your PATH." >&2
    exit 1
  fi
  if [[ ! -f "micro_profit.py" ]]; then
    echo "WARNING: micro_profit.py not found in the current working directory." >&2
    echo "Attempting to call it directly..." >&2
  fi
}

# ------------------------------------------------------------------------------
# 📡 Live Market Data Engine
# ------------------------------------------------------------------------------
fetch_live_depth() {
  local symbol="$1"
  local side="$2" # bids or asks
  local limit="5"

  python3 -c "
import urllib.request, json, sys
try:
    endpoints = [
        'https://api.binance.com',
        'https://api1.binance.com',
        'https://api2.binance.com',
        'https://api-gcp.binance.com'
    ]
    data = None
    for base in endpoints:
        try:
            url = f'{base}/api/v3/depth?symbol=${symbol}&limit=${limit}'
            req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
            with urllib.request.urlopen(req, timeout=4) as response:
                res = json.loads(response.read().decode())
                data = [[float(x[0]), float(x[1])] for x in res['${side}']]
                break
        except Exception:
            continue
    if data:
        print(json.dumps(data))
    else:
        sys.exit(1)
except Exception:
    sys.exit(1)
" 2>/dev/null
}

# ------------------------------------------------------------------------------
# 1️⃣ Basic Micro‑Scalp Macro (≈ 5 USDT target)
# ------------------------------------------------------------------------------
macro_basic_scalp() {
  local symbol="${SYMBOL:-BTCUSDT}"
  local side="${SIDE:-Buy}"
  local qty="${QTY:-0.001}"
  local target="${TARGET:-5.0}"
  local leverage="${LEVERAGE:-1.0}"
  local risk_reward="${RISK_REWARD:-2.0}"

  # Base mock data
  local bids='[[50000, 10], [49990, 15], [49980, 20]]'
  local asks='[[50010, 10], [50020, 15], [50030, 20]]'

  if [[ "$LIVE" == "true" ]]; then
    echo "Fetching live order-book depth for ${symbol} from Binance..." >&2
    local live_bids
    local live_asks
    live_bids=$(fetch_live_depth "$symbol" "bids")
    live_asks=$(fetch_live_depth "$symbol" "asks")
    if [[ -n "$live_bids" && -n "$live_asks" ]]; then
      bids="$live_bids"
      asks="$live_asks"
      echo "Successfully populated live data." >&2
    else
      echo "Failed to fetch live data. Falling back to default mock depth." >&2
    fi
  fi

  check_environment

  python3 micro_profit.py \
    --symbol "$symbol" \
    --side "$side" \
    --qty "$qty" \
    --target "$target" \
    --bids "$bids" \
    --asks "$asks" \
    --maker-fee "${MAKER_FEE:-0.0002}" \
    --taker-fee "${TAKER_FEE:-0.00055}" \
    --leverage "$leverage" \
    --risk-reward "$risk_reward" \
    --post-only
}

# ------------------------------------------------------------------------------
# 2️⃣ Leveraged Momentum Macro (≈ 10 USDT target)
# ------------------------------------------------------------------------------
macro_leverage_momentum() {
  local symbol="${SYMBOL:-ETHUSDT}"
  local side="${SIDE:-Buy}"
  local qty="${QTY:-0.01}"
  local target="${TARGET:-10.0}"
  local leverage="${LEVERAGE:-3.0}"
  local risk_reward="${RISK_REWARD:-2.5}"

  # Base mock data
  local bids='[[3000, 100], [2995, 200], [2990, 150]]'
  local asks='[[3005, 100], [3010, 200], [3015, 150]]'

  if [[ "$LIVE" == "true" ]]; then
    echo "Fetching live order-book depth for ${symbol} from Binance..." >&2
    local live_bids
    local live_asks
    live_bids=$(fetch_live_depth "$symbol" "bids")
    live_asks=$(fetch_live_depth "$symbol" "asks")
    if [[ -n "$live_bids" && -n "$live_asks" ]]; then
      bids="$live_bids"
      asks="$live_asks"
      echo "Successfully populated live data." >&2
    else
      echo "Failed to fetch live data. Falling back to default mock depth." >&2
    fi
  fi

  check_environment

  python3 micro_profit.py \
    --symbol "$symbol" \
    --side "$side" \
    --qty "$qty" \
    --target "$target" \
    --bids "$bids" \
    --asks "$asks" \
    --maker-fee "${MAKER_FEE:-0.0002}" \
    --taker-fee "${TAKER_FEE:-0.00055}" \
    --leverage "$leverage" \
    --risk-reward "$risk_reward" \
    --kelly-win "${KELLY_WIN:-0.55}"
}

# ------------------------------------------------------------------------------
# 3️⃣ Order‑Book Wall‑Surfing Macro (≈ 15 USDT target)
# ------------------------------------------------------------------------------
macro_wall_surfing() {
  local symbol="${SYMBOL:-SOLUSDT}"
  local side="${SIDE:-Sell}"
  local qty="${QTY:-0.1}"
  local target="${TARGET:-15.0}"
  local leverage="${LEVERAGE:-1.0}"

  # Base mock data
  local bids='[[150, 500], [149.5, 800], [149, 300]]'
  local asks='[[150.5, 500], [151, 800], [151.5, 300]]'

  if [[ "$LIVE" == "true" ]]; then
    echo "Fetching live order-book depth for ${symbol} from Binance..." >&2
    local live_bids
    local live_asks
    live_bids=$(fetch_live_depth "$symbol" "bids")
    live_asks=$(fetch_live_depth "$symbol" "asks")
    if [[ -n "$live_bids" && -n "$live_asks" ]]; then
      bids="$live_bids"
      asks="$live_asks"
      echo "Successfully populated live data." >&2
    else
      echo "Failed to fetch live data. Falling back to default mock depth." >&2
    fi
  fi

  check_environment

  python3 micro_profit.py \
    --symbol "$symbol" \
    --side "$side" \
    --qty "$qty" \
    --target "$target" \
    --bids "$bids" \
    --asks "$asks" \
    --maker-fee "${MAKER_FEE:-0.0002}" \
    --taker-fee "${TAKER_FEE:-0.00055}" \
    --leverage "$leverage" \
    --ignore-regime \
    --post-only \
    --reduce-only
}

# ------------------------------------------------------------------------------
# Optional helper – print usage
# ------------------------------------------------------------------------------
usage() {
  cat <<EOF
Usage: $0 [options] [macro_name]

Options:
  -l, --live           – Query real-time depth directly from Binance API
  -h, --help           – Print this usage panel

Available macros:
  basic_scalp          – Small 5 USDT scalp (BTCUSDT)
  leverage_momentum    – 10 USDT with 3× leverage (ETHUSDT)
  wall_surfing         – 15 USDT riding order‑book walls (SOLUSDT)

Examples:
  $0 basic_scalp                         # Static analysis fallback
  $0 --live basic_scalp                  # Run with real Binance order book
  SYMBOL=ADAUSDT TARGET=2.5 $0 -l basic_scalp # Overrides symbol, target & uses live feed
EOF
}

# ------------------------------------------------------------------------------
# Entry point – allow direct execution
# ------------------------------------------------------------------------------
if [[ "${BASH_SOURCE[0]}" == "${0}" ]]; then
  export LIVE="false"
  ARGS=()
  while [[ $# -gt 0 ]]; do
    case "$1" in
      -l|--live)
        export LIVE="true"
        shift
        ;;
      -h|--help)
        usage
        exit 0
        ;;
      *)
        ARGS+=("$1")
        shift
        ;;
    esac
  done

  if [[ ${#ARGS[@]} -eq 0 ]]; then
    usage
    exit 1
  fi

  case "${ARGS[0]}" in
    basic_scalp)       macro_basic_scalp ;;
    leverage_momentum) macro_leverage_momentum ;;
    wall_surfing)      macro_wall_surfing ;;
    *)                 usage; exit 1 ;;
  esac
fi