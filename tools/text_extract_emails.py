#!/usr/bin/env python3
# ==============================================================================
# text_extract_emails.py
#
# @describe Extract all email addresses from text.
# @option --text! <TEXT> The text to search.
# ==============================================================================

import re


def run(text: str):
    """
    Extract all email addresses from text.
    """
    emails = re.findall(r"[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}", text)
    return "\n".join(set(emails)) if emails else "No emails found."
