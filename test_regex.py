import os
import re

files = ["tools/bybit_closed_pnl.sh"]

for f in files:
    full_path = os.path.join(
        "/data/data/com.termux/files/home/.config/aichat/llm-functions/", f
    )
    with open(full_path) as f:
        content = f.read()

    # The pattern from the file:
    # CURL_CMD=(curl -s -S -X GET "https://api.bybit.com$ENDPOINT")
    # if [[ -n "$QUERY" ]]; then
    #     CURL_CMD+=("?${QUERY}")
    # fi

    # Let's try matching part by part.
    match1 = re.search(
        r'CURL_CMD=\(curl .*? "https://api\.bybit\.com\$ENDPOINT"\)', content, re.DOTALL
    )
    print(f"Match 1: {bool(match1)}")

    match2 = re.search(
        r'if \[\[ -n "\$QUERY" ]]; then.*?CURL_CMD\+=("\?\$\{QUERY\}").*?fi',
        content,
        re.DOTALL,
    )
    print(f"Match 2: {bool(match2)}")
