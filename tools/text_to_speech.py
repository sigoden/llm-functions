#!/usr/bin/env python3
# BEGIN HUMAN READABLE UI PATCH
import json, sys
def _print_human_readable_ui(res):
    if not isinstance(res, dict):
        return
    if res.get('success'):
        data = res.get('data', {})
        if isinstance(data, dict):
            for k, v in data.items():
                print(f'>>> {k}: {v}', file=sys.stderr)
    else:
        err = res.get('error')
        if isinstance(err, dict):
            print(f'!!! Error: {err.get("message", err)}', file=sys.stderr)
        else:
            print(f'!!! Error: {err}', file=sys.stderr)
# END HUMAN READABLE UI PATCH

"""Text to Speech tool."""
from __future__ import annotations
from typing import Any
import argparse
import sys
import json

__version__ = "1.0.0"

def run(text: str = "", **kwargs: Any) -> dict[str, Any]:
    """Convert text to speech.
    
    Args:
        text: Text to synthesize
    """
    return {
        "success": True,
        "data": {"message": "TTS stub", "text": text}
    }

def _cli() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--text", default="")
    args = parser.parse_args()
    res = run(text=args.text)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    _print_human_readable_ui(res)
    print(json.dumps(res, indent=2))
    return 0

if __name__ == "__main__":
    sys.exit(_cli())
