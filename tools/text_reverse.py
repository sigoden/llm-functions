#!/usr/bin/env python3
# ==============================================================================
# text_reverse.py
#
# @describe Reverse the lines in a text.
# @option --text! <TEXT> The text to reverse.
# ==============================================================================


def run(text: str):
    """
    Reverse the lines in a text.
    """
    lines = text.splitlines()
    return "\n".join(reversed(lines))
