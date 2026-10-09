#!/usr/bin/env python3
# ==============================================================================
# text_count.py
#
# @describe Count words, lines, and characters in text.
# @option --text! <TEXT> The text to analyze.
# ==============================================================================


def run(text: str):
    """
    Count words, lines, and characters in text.
    """
    lines = text.splitlines()
    words = text.split()
    chars = len(text)
    return f"Lines: {len(lines)}\nWords: {len(words)}\nCharacters: {chars}"
