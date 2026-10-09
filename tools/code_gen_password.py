#!/usr/bin/env python3
# ==============================================================================
# code_gen_password.py
#
# @describe Generate a secure random password.
# @option --length <NUM> Password length.
# @option --symbols Include special characters.
# ==============================================================================

import secrets
import string


def run(length: int = 16, symbols: bool = True):
    """
    Generate a secure random password.
    """
    chars = string.ascii_letters + string.digits
    if symbols:
        chars += string.punctuation
    return "".join(secrets.choice(chars) for _ in range(length))
