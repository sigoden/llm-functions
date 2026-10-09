#!/usr/bin/env python3
# ==============================================================================
# date_diff.py
#
# @describe Calculate the difference in days between two dates (YYYY-MM-DD).
# @option --date1! <TEXT> First date.
# @option --date2! <TEXT> Second date.
# ==============================================================================

from datetime import datetime


def run(date1: str, date2: str):
    """
    Calculate the difference in days between two dates (YYYY-MM-DD).
    """
    d1 = datetime.strptime(date1, "%Y-%m-%d")
    d2 = datetime.strptime(date2, "%Y-%m-%d")
    return f"Difference: {abs((d2 - d1).days)} days"
