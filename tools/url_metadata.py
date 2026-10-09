#!/usr/bin/env python3
# @describe Extract URL title.
# @option --url! <URL>
import os
import re
import urllib.request


def run(url: str) -> str:
    try:
        with urllib.request.urlopen(url) as r:
            content = r.read().decode("utf-8", "ignore")
            title = re.search(r"<title>(.*?)</title>", content, re.I)
            return title.group(1) if title else "No title found"
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    kwargs = {k[5:]: v for k, v in os.environ.items() if k.startswith("argc_")}
    print(run(**kwargs))
