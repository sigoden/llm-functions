#!/usr/bin/env python3
# ==============================================================================
# code_base64.py
#
# @describe Encode or decode Base64 data.
# @option --data! <TEXT> The string to process.
# @option --decode If true, decode instead of encode.
# ==============================================================================

import base64


def run(data: str, decode: bool = False):
    """
    Encode or decode Base64 data.
    """
    if decode:
        return base64.b64decode(data).decode("utf-8")
    return base64.b64encode(data.encode("utf-8")).decode("utf-8")
