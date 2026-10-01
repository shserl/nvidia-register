from __future__ import annotations

import secrets
import string


def generate_password(length: int = 12) -> str:
    if length < 3:
        raise ValueError("Password length must be at least 3")
    groups = [string.ascii_lowercase, string.ascii_uppercase, string.digits]
    required = [secrets.choice(group) for group in groups]
    remaining = [secrets.choice("".join(groups)) for _ in range(length - len(required))]
    chars = required + remaining
    secrets.SystemRandom().shuffle(chars)
    return "".join(chars)
