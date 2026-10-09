import os
import re

files = [
    "tools/bybit_closed_pnl.sh",
]


def fix_file(file_path):
    with open(file_path) as f:
        content = f.read()

    # Debug
    pattern = re.compile(
        r'(CURL_CMD=\(curl .*? "https://api\.bybit\.com\$ENDPOINT"\))'
        r"(.*?)"
        r'(if \[\[ -n "\$QUERY" ]]; then)'
        r"(.*?)"
        r'(CURL_CMD\+=("\?\$\{QUERY\}"))'
        r"(.*?)"
        r"(fi)",
        re.DOTALL,
    )

    match = pattern.search(content)
    if match:
        print(f"Match found for {file_path}")
        print(f"Group 1: {match.group(1)}")
        return True
    else:
        print(f"No match for {file_path}")
        # Print first 200 chars
        print(f"Content start: {content[:200]}")

    return False


for f in files:
    full_path = os.path.join(
        "/data/data/com.termux/files/home/.config/aichat/llm-functions/", f
    )
    if os.path.exists(full_path):
        fix_file(full_path)
