#!/usr/bin/env python3
# ==============================================================================
# text_sort.py
#
# @describe Sort lines in a text alphabetically.
# @option --text! <TEXT> The text to sort.
# @option --reverse Sort in reverse order.
# ==============================================================================


def run(text: str, reverse: bool = False):
    """
    Sort lines in a text alphabetically.
    """
    lines = text.splitlines()
    lines.sort(reverse=reverse)
    return "\n".join(lines)
