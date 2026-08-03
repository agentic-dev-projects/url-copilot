"""
Short code generation.

Generates collision-resistant short codes used as the slug in redirect URLs.
Supports both auto-generated codes and custom aliases.

Auto-generated codes use ShortUUID (base57, URL-safe alphabet) trimmed to
`length` characters. At 6 characters with base57 this gives 57^6 ≈ 34 billion
possible codes — sufficient for tens of millions of links without collision.

Usage:
    code = generate_short_code()          # e.g. "aB3xQ9"
    ok   = is_valid_custom_alias("my-campaign")
"""

import re

import shortuuid


# Allowed pattern for user-supplied custom aliases
_ALIAS_PATTERN = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9\-]{1,30}[a-zA-Z0-9]$")


def generate_short_code(length: int = 6) -> str:
    """
    Generate a URL-safe random short code of the given length.

    Uses ShortUUID's base57 alphabet (no ambiguous chars like 0/O, 1/l/I).
    """
    return shortuuid.ShortUUID().random(length=length)


def is_valid_custom_alias(alias: str) -> bool:
    """
    Return True if `alias` is an acceptable custom short code.

    Rules:
    - 3–32 characters total
    - Only alphanumeric characters and hyphens
    - Cannot start or end with a hyphen
    """
    if not (3 <= len(alias) <= 32):
        return False
    return bool(_ALIAS_PATTERN.match(alias))
