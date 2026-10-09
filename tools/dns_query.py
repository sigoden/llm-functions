#!/usr/bin/env python3
# @describe DNS query.
# @option --host! <HOSTNAME>
import os
import socket


def run(host: str) -> str:
    try:
        return socket.gethostbyname(host)
    except Exception as e:
        return f"Error: {e}"


if __name__ == "__main__":
    kwargs = {k[5:]: v for k, v in os.environ.items() if k.startswith("argc_")}
    print(run(**kwargs))
