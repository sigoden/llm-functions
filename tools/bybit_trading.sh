#!/data/data/com.termux/files/usr/bin/env bash
set -e

# @describe Bybit trading tool with multiple commands: get_history, view_logs, get_trend, check_status, execute_trade, analyze_l2, check_funding, rebalance, monitor_liquidations, execute_iceberg, send_alert
# @option --cmd <STRING> Command to execute (get_history, view_logs, get_trend, check_status, execute_trade, analyze_l2, check_funding, rebalance, monitor_liquidations, execute_iceberg, send_alert)
# @option --symbol <STRING> Trading pair (default: BTCUSDT)
# @option --interval <STRING> Timeframe (default: 15)
# @option --signal <STRING> Signal (buy/sell)
# @option --risk <STRING> USDT to risk on SL (default: 50)
# @option --profit <STRING> Target USDT profit after fees (default: 10)
# @option --depth <STRING> Order book depth (default: 50)
# @option --asset <STRING> Target coin (default: BTC)
# @option --target <STRING> Target percentage (default: 0.5)
# @option --side <STRING> Order side (buy/sell)
# @option --total-qty <STRING> Total quantity to execute
# @option --visible-qty <STRING> Visible quantity per slice
# @option --price <STRING> Optional limit price
# @option --msg <STRING> Message content


# Configuration
ROOT_DIR="$(dirname "$0")"
TIMEOUT_SECONDS=10

# Color codes for output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
NC='\033[0m' # No Color

# Dependency check
check_dependencies() {
    if ! command -v node &> /dev/null; then
        echo -e "${RED}Error: node is not installed${NC}" >&2
        exit 1
    fi
    
    if ! command -v argc &> /dev/null; then
        echo -e "${RED}Error: argc is not installed${NC}" >&2
        exit 1
    fi
    
    if ! command -v curl &> /dev/null; then
        echo -e "${RED}Error: curl is not installed${NC}" >&2
        exit 1
    fi
}

# Timeout wrapper for node calls
safe_node_call() {
    timeout "$TIMEOUT_SECONDS" node "$@" || {
        local exit_code=$?
        if [ $exit_code -eq 124 ]; then
            echo -e "${RED}Error: Command timed out after ${TIMEOUT_SECONDS} seconds${NC}" >&2
            exit 1
        else
            exit $exit_code
        fi
    }
}

# Check dependencies at script start
check_dependencies


main() {
    local cmd="${argc_cmd:-get_history}"
    case "$cmd" in
        get_history)
            echo -e "${YELLOW}Fetching trade history for ${argc_symbol:-BTCUSDT}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_logic.js" get_history "${argc_symbol:-BTCUSDT}"
            ;;
        view_logs)
            local log_file="${ROOT_DIR}/trade_history.csv"
            if [ -f "$log_file" ]; then
                echo -e "${GREEN}Trade History Log:${NC}"
                cat "$log_file"
            else
                echo -e "${YELLOW}No trade history log found at ${log_file}${NC}"
            fi
            ;;
        get_trend)
            echo -e "${YELLOW}Analyzing trend for ${argc_symbol:-BTCUSDT} on ${argc_interval:-15}m timeframe...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_logic.js" get_trend "${argc_symbol:-BTCUSDT}" "${argc_interval:-15}"
            ;;
        check_status)
            echo -e "${YELLOW}Checking positions for ${argc_symbol:-BTCUSDT}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_logic.js" check_status "${argc_symbol:-BTCUSDT}"
            ;;
        execute_trade)
            echo -e "${YELLOW}Executing ${argc_signal} trade for ${argc_symbol:-BTCUSDT}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_logic.js" execute "${argc_signal}" "${argc_symbol:-BTCUSDT}" "${argc_risk:-50}" "${argc_profit:-10}"
            ;;
        analyze_l2)
            echo -e "${YELLOW}Analyzing L2 order book for ${argc_symbol:-BTCUSDT}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_l2_tool.js" analyze_l2 "${argc_symbol:-BTCUSDT}" "${argc_depth:-50}"
            ;;
        check_funding|funding)
            echo -e "${YELLOW}Screening funding rate opportunities...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_funding_tool.js" "$@"
            ;;
        rebalance)
            echo -e "${YELLOW}Rebalancing portfolio for ${argc_asset:-BTC} to ${argc_target:-0.5}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_rebalance_tool.js" "${argc_asset:-BTC}" "${argc_target:-0.5}"
            ;;
        monitor_liquidations)
            echo -e "${YELLOW}Starting liquidation monitor...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_liquidation_tool.js" "${argc_symbol:-BTCUSDT}" &
            ;;
        execute_iceberg)
            echo -e "${YELLOW}Executing iceberg order for ${argc_symbol} ${argc_side}...${NC}"
            safe_node_call "${ROOT_DIR}/bybit_iceberg_tool.js" "${argc_symbol}" "${argc_side}" "${argc_total_qty}" "${argc_visible_qty}" "${argc_price}"
            ;;
        send_alert)
            if [ -z "$TELEGRAM_BOT_TOKEN" ] || [ -z "$TELEGRAM_CHAT_ID" ]; then
                echo -e "${RED}Error: TELEGRAM_BOT_TOKEN and TELEGRAM_CHAT_ID environment variables must be set${NC}" >&2
                exit 1
            fi
            
            echo -e "${YELLOW}Sending Telegram alert...${NC}"
            curl -s -X POST "https://api.telegram.org/bot${TELEGRAM_BOT_TOKEN}/sendMessage" \
                -d "chat_id=${TELEGRAM_CHAT_ID}" \
                -d "text=${argc_msg}" \
                -d "parse_mode=Markdown" || {
                echo -e "${RED}Failed to send Telegram alert${NC}" >&2
                exit 1
            }
            
            echo -e "${GREEN}Alert sent successfully${NC}"
            ;;
        *)
            echo "Unknown command: $cmd"
            exit 1
            ;;
    esac
}

eval "$(argc --argc-eval "$0" "$@")"
