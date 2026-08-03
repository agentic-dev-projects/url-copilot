"""
Security utilities — API key generation and verification.

API keys follow the format: sk_<base62-encoded random bytes>
Only a SHA-256 hash of the raw key is stored in the database.

Usage:
    raw_key, key_hash, key_prefix = generate_api_key()
    # Store key_hash and key_prefix; return raw_key to the user once

    is_valid = verify_api_key(raw_key_from_request, stored_hash)
"""

import hashlib
import secrets


_KEY_PREFIX = "sk_"


def generate_api_key(byte_length: int = 32) -> tuple[str, str, str]:
    """
    Generate a new API key.

    Returns:
        raw_key    — full key to return to the user (shown once only)
        key_hash   — SHA-256 hex digest to store in the DB
        key_prefix — first 8 chars of raw_key for display/identification
    """
    random_bytes = secrets.token_urlsafe(byte_length)
    raw_key = f"{_KEY_PREFIX}{random_bytes}"
    key_hash = _hash_key(raw_key)
    key_prefix = raw_key[:8]
    return raw_key, key_hash, key_prefix


def hash_api_key(raw_key: str) -> str:
    """Return the SHA-256 hex digest of a raw API key for DB lookup."""
    return _hash_key(raw_key)


def verify_api_key(raw_key: str, stored_hash: str) -> bool:
    """
    Constant-time comparison of a raw key against its stored hash.

    Uses hmac.compare_digest internally (via secrets) to prevent
    timing-based attacks.
    """
    computed = _hash_key(raw_key)
    return secrets.compare_digest(computed, stored_hash)


def _hash_key(raw_key: str) -> str:
    return hashlib.sha256(raw_key.encode()).hexdigest()
