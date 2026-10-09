#!/usr/bin/env bash
# ──────────────────────────────────────────────────────────────────────────────
# bybit_tor.sh — Launch bybit_trader.py transparently through proxychains4.
#
# proxychains4 intercepts all outgoing TCP connections at the libc level and
# redirects them through Tor's SOCKS5 proxy. This catches any stray
# connection that might bypass the Python-level proxy dict.
#
# Usage:
#   chmod +x bybit_tor.sh
#   BYBIT_API_KEY=xxx BYBIT_API_SECRET=yyy \
#     ./bybit_tor.sh --symbol BTCUSDT --side Buy --qty 0.001
#
# Flags passed to this script are forwarded verbatim to bybit_trader.py.
# ──────────────────────────────────────────────────────────────────────────────
set -euo pipefail

# ── 0. Locate script directory so relative paths work regardless of CWD ───────
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
TRADER="${SCRIPT_DIR}/bybit_trader.py"

# ── 1. Dependency checks ──────────────────────────────────────────────────────
for cmd in proxychains4 tor python3; do
    if ! command -v "$cmd" &>/dev/null; then
        echo "[ERROR] '$cmd' is not installed or not in PATH." >&2
        echo "        Install: sudo apt install tor proxychains4  &&  pip install requests[socks] stem" >&2
        exit 1
    fi
done

if [[ ! -f "$TRADER" ]]; then
    echo "[ERROR] bybit_trader.py not found at: $TRADER" >&2
    exit 1
fi

# ── 2. Mandatory credentials guard ───────────────────────────────────────────
# Never allow execution with placeholder values.
if [[ "${BYBIT_API_KEY:-}" == "YOUR_BYBIT_API_KEY" ]] || \
   [[ -z "${BYBIT_API_KEY:-}" ]] || \
   [[ "${BYBIT_API_SECRET:-}" == "YOUR_BYBIT_API_SECRET" ]] || \
   [[ -z "${BYBIT_API_SECRET:-}" ]]; then
    echo "[ERROR] BYBIT_API_KEY and BYBIT_API_SECRET must be set in the environment." >&2
    echo "        export BYBIT_API_KEY=your_key" >&2
    echo "        export BYBIT_API_SECRET=your_secret" >&2
    exit 1
fi

# ── 3. Verify Tor daemon is reachable on port 9050 ────────────────────────────
if ! timeout 3 bash -c 'echo >/dev/tcp/127.0.0.1/9050' 2>/dev/null; then
    echo "[ERROR] Tor SOCKS port 9050 is not reachable." >&2
    echo "        Start Tor: sudo systemctl start tor" >&2
    echo "        Or:        tor -f /path/to/torrc.bybit &" >&2
    exit 1
fi

# ── 4. Optional: verify Tor control port for circuit management ───────────────
if ! timeout 3 bash -c 'echo >/dev/tcp/127.0.0.1/9051' 2>/dev/null; then
    echo "[WARN] Tor control port 9051 is not reachable. Circuit renewal will be disabled." >&2
    echo "       Add 'ControlPort 9051' and 'CookieAuthentication 1' to your torrc." >&2
    # Not fatal — Python will skip stem-based renewal gracefully
fi

# ── 5. Announce circuit info (informational, no identity leak) ────────────────
echo "[INFO] Tor SOCKS5 proxy confirmed on 127.0.0.1:9050"
echo "[INFO] Launching bybit_trader.py via proxychains4 ..."
echo "[INFO] All TCP traffic is now transparently proxied through Tor."

# ── 6. proxychains4 invocation ────────────────────────────────────────────────
#   Forward all script arguments to bybit_trader.py unchanged.
proxychains4 \
    python3 "$TRADER" "$@"

EXIT_CODE=$?

if [[ $EXIT_CODE -ne 0 ]]; then
    echo "[ERROR] bybit_trader.py exited with code $EXIT_CODE" >&2
fi

exit $EXIT_CODE
