#!/usr/bin/env python3
# ==============================================================================
# code_format_json.py
#
# @describe Format or minify JSON data.
# @option --data! <TEXT> The JSON string.
# @option --indent <NUM> Indentation level (0 for minify).
# ==============================================================================

import json


def run(data: str, indent: int = 4):
    """
    Format or minify JSON data.
    """
    obj = json.loads(data)
    if indent == 0:
        return json.dumps(obj, separators=(",", ":"))
    return json.dumps(obj, indent=indent)
