"""Diner-facing confirmation codes."""

import secrets

# No 0/O, 1/I: codes are read aloud over the phone.
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
CODE_LENGTH = 6


def new_code() -> str:
    return "".join(secrets.choice(ALPHABET) for _ in range(CODE_LENGTH))
