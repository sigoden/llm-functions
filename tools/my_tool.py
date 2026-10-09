#!/usr/bin/env python3
"""Simple echo tool."""

import sys


def main():
    print("Hello from my_tool!")
    for arg in sys.argv[1:]:
        print(f"Got argument: {arg}")


if __name__ == "__main__":
    main()
