#!/usr/bin/env python3
# @describe Measure TCP latency.
# @option --host! <HOSTNAME>
# @option --port! <PORT>
import os
import socket
import time


def run(host: str, port: int) -> str:
    try:
        t0 = time.time()
        socket.create_connection((host, int(port)), timeout=2)
        latency = (time.time() - t0) * 1000
        return f"{latency:.2f} ms"
    except Exception as e:
        return f"Failed: {e}"


if __name__ == "__main__":
    kwargs = {k[5:]: v for k, v in os.environ.items() if k.startswith("argc_")}
    print(run(**kwargs))
