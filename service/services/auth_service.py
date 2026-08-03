"""
Authentication service — user registration and API key management.

All business logic for auth lives here. Route handlers call these
functions and handle the HTTP response; this layer has no FastAPI imports.
"""

from sqlalchemy.orm import Session

from service.core.security import generate_api_key, hash_api_key, verify_api_key
from service.models.api_key import APIKey
from service.models.user import User


def register_user(db: Session, email: str) -> tuple[User, str, str, str]:
    """
    Create a new user and issue an API key.

    Returns:
        user       — the persisted User row
        raw_key    — full API key to return to the caller (shown once)
        key_hash   — SHA-256 hash stored in the DB
        key_prefix — short prefix for display

    Raises:
        ValueError if the email is already registered.
    """
    existing = db.query(User).filter(User.email == email).first()
    if existing:
        raise ValueError(f"Email already registered: {email}")

    user = User(email=email)
    db.add(user)
    db.flush()  # get user.id before inserting APIKey

    raw_key, key_hash, key_prefix = generate_api_key()
    api_key = APIKey(user_id=user.id, key_hash=key_hash, key_prefix=key_prefix)
    db.add(api_key)
    db.commit()
    db.refresh(user)

    return user, raw_key, key_hash, key_prefix


def get_user_by_api_key(db: Session, raw_key: str) -> User | None:
    """
    Look up the active user who owns `raw_key`.

    Returns None if the key is invalid, inactive, or the user is inactive.
    Also updates last_used_at on the matching APIKey row.
    """
    key_hash = hash_api_key(raw_key)
    api_key = (
        db.query(APIKey)
        .filter(APIKey.key_hash == key_hash, APIKey.is_active == True)  # noqa: E712
        .first()
    )
    if api_key is None:
        return None

    user = db.query(User).filter(User.id == api_key.user_id, User.is_active == True).first()  # noqa: E712
    if user is None:
        return None

    # Record last use without holding a long transaction
    from datetime import datetime, timezone
    api_key.last_used_at = datetime.now(timezone.utc)
    db.commit()

    return user


def rotate_api_key(db: Session, user: User) -> tuple[str, str, str]:
    """
    Deactivate all existing keys for `user` and issue a fresh one.

    Returns:
        raw_key, key_hash, key_prefix  (same shape as register_user)
    """
    db.query(APIKey).filter(
        APIKey.user_id == user.id, APIKey.is_active == True  # noqa: E712
    ).update({"is_active": False})

    raw_key, key_hash, key_prefix = generate_api_key()
    new_key = APIKey(user_id=user.id, key_hash=key_hash, key_prefix=key_prefix)
    db.add(new_key)
    db.commit()

    return raw_key, key_hash, key_prefix
