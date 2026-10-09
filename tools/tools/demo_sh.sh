#!/usr/bin/env bash
# ------------------------------------------------------------
# Demo script for the Bybit V5 Bash toolkit
# ------------------------------------------------------------
# This script demonstrates how to invoke the various Bybit tools
# that have been created in the ./tools directory.  It:
#   1. Loads credentials from a `.env` file (if present).
#   2. Executes each utility with a simple example set of arguments.
#   3. Captures the JSON summary that each tool appends to $LLM_OUTPUT.
#   4. Prints a short, human‑readable recap of the results.
# ------------------------------------------------------------

set -euo pipefail

# -----------------------------------------------------------------
# 1️⃣ Load environment variables (API keys, optional proxy, etc.)
# -----------------------------------------------------------------
if [[ -f ".env" ]]; then
    # shellcheck source=/dev/null
    source .env
fi

# -----------------------------------------------------------------
# 2️⃣ Helper: run a Bybit tool and capture its $LLM_OUTPUT snippet
# -----------------------------------------------------------------
run_tool() {
    local tool_path="$1"
    shift
    # Export a temporary LLM_OUTPUT file for this invocation only
    local tmp_output
    tmp_output=$(mktemp)
    export LLM_OUTPUT="$tmp_output"

    # Execute the tool with the supplied arguments
    "$tool_path" "$@" 2>&1 || {
        echo "⚠️  $tool_path failed – see output above"
        rm -f "$tmp_output"
        return 1
    }

    # Read the JSON summary that the tool wrote
    local summary
    summary=$(<"$tmp_output")
    rm -f "$tmp_output"

    # Pretty‑print the summary (if it is valid JSON)
    if [[ -n "$summary" ]]; then
        printf '🔹 %s\n' "$summary" | jq . 2>/dev/null || echo "$summary"
    else
        echo "(no summary produced)"
    fi
}

# -----------------------------------------------------------------
# 3️⃣ Demo invocations
# -----------------------------------------------------------------

echo "=== Bybit Toolkit Demo ==="
echo

# 3.1 Position list (linear, BTCUSD)
echo "1️⃣  Position List (linear, BTCUSD)"
run_tool "../tools/bybit_position_list.sh" --category linear --symbol BTCUSD
echo

# 3.2 Wallet balance (spot)
echo "2️⃣  Wallet Balance (spot)"
run_tool "../tools/bybit_wallet_balance.sh" --account-type spot
echo

# 3.3 Mark price (ETHUSD, spot)
echo "3️⃣  Mark Price (ETHUSD, spot)"
run_tool "../tools/bybit_mark_price.sh" --symbol ETHUSD --category spot
echo

# 3.4 (Optional) Show that the other scripts are present and executable
echo "4️⃣  Other tools in the kit (just list them):"
ls -1 ../tools/bybit_*.sh | sed 's|^../tools/||' | column -t
echo

echo "✅ Demo completed.  Each tool appended a concise JSON block to a temporary"
echo "   LLM_OUTPUT file, which has been displayed above."
