#!/usr/bin/env python3
# @describe Check host connectivity.
# @option --host! <HOSTNAME>
import os
import socket


def run(host: str, port: int = 80) -> str:
    try:
        socket.create_connection((host, port), timeout=2)
        return "Connected"
    except Exception as e:
        return f"Failed: {e}"


if __name__ == "__main__":
    kwargs = {k[5:]: v for k, v in os.environ.items() if k.startswith("argc_")}
    if "port" in kwargs:
        kwargs["port"] = int(kwargs["port"])
    print(run(**kwargs))
