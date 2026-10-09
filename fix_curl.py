import os
import re

files = [
    "tools/bybit_closed_pnl.sh",
    "tools/bybit_fee_rate.sh",
    "tools/bybit_funding_rate.sh",
    "tools/bybit_kline_trend.sh",
    "tools/bybit_limit_order.sh",
    "tools/bybit_mark_price.sh",
    "tools/bybit_market_order.sh",
    "tools/bybit_order_amend.sh",
    "tools/bybit_order_book_depth.sh",
    "tools/bybit_order_cancel.sh",
    "tools/bybit_order_create.sh",
    "tools/bybit_order_history.sh",
    "tools/bybit_orderbook_depth.sh",
    "tools/bybit_position_info.sh",
    "tools/bybit_position_liquidation.sh",
    "tools/bybit_position_transactions.sh",
    "tools/bybit_stop_loss.sh",
    "tools/bybit_symbol_info.sh",
    "tools/bybit_take_profit.sh",
    "tools/bybit_trading.sh",
    "tools/bybit_transaction_log.sh",
    "tools/bybit_wallet_balance.sh",
]


def fix_file(file_path):
    with open(file_path) as f:
        content = f.read()

    pattern = re.compile(
        r'(CURL_CMD=\(curl .*? "https://api\.bybit\.com\$ENDPOINT"\))'
        r"\s*"
        r'(if \[\[ -n "\$QUERY" ]]; then)'
        r"\s*"
        r'(CURL_CMD\+=("\?\$\{QUERY\}"))'
        r"\s*"
        r"(fi)",
        re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        print(f"Fixing {file_path}")

        curl_line = match.group(1)
        new_curl_line = curl_line.replace('"https://api.bybit.com$ENDPOINT"', '"$URL"')

        new_block_lines = [
            'URL="https://api.bybit.com$ENDPOINT"',
            'if [[ -n "$QUERY" ]]; then',
            '    URL="${URL}?${QUERY}"',
            "fi",
            new_curl_line,
        ]
        new_block = chr(10).join(new_block_lines)

        new_content = content.replace(match.group(0), new_block)

        with open(file_path, "w") as f:
            f.write(new_content)
        return True

    return False


for f in files:
    full_path = os.path.join(
        "/data/data/com.termux/files/home/.config/aichat/llm-functions/", f
    )
    if os.path.exists(full_path):
        fix_file(full_path)
