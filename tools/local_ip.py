#!/usr/bin/env python3
# @describe Get local IP address.
import socket


def run() -> str:
    return socket.gethostbyname(socket.gethostname())


if __name__ == "__main__":
    print(run())
