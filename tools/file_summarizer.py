#!/usr/bin/env python3
# @describe Summarize a file.
# @option --file-path! <PATH>
# @option --length <NUM> Max length of summary.

import os


def run(file_path: str, length: int = 500) -> str:
    if not os.path.exists(file_path):
        return f"Error: File {file_path} not found."
    with open(file_path) as f:
        content = f.read(length)
    return f"Summary (first {length} chars): {content}..."
