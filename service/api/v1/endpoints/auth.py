"""Auth endpoints — user registration and API key rotation."""

from fastapi import APIRouter, Depends, HTTPException, status
from sqlalchemy.orm import Session

from service.api.deps import get_current_user
from service.db.session import get_db
from service.models.user import User
from service.schemas.auth import RegisterRequest, RegisterResponse, RotateKeyResponse
from service.services.auth_service import register_user, rotate_api_key

router = APIRouter(prefix="/auth", tags=["auth"])


@router.post(
    "/register",
    response_model=RegisterResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Register a new user and receive an API key",
)
def register(body: RegisterRequest, db: Session = Depends(get_db)) -> RegisterResponse:
    """
    Create a new user account and issue an API key.

    The raw API key is returned **once** — store it securely.
    Subsequent calls with the same email return HTTP 409.
    """
    try:
        user, raw_key, _, key_prefix = register_user(db, email=str(body.email))
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))

    return RegisterResponse(
        user_id=str(user.id),
        api_key=raw_key,
        key_prefix=key_prefix,
    )


@router.post(
    "/rotate-key",
    response_model=RotateKeyResponse,
    summary="Rotate the API key for the authenticated user",
)
def rotate_key(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
) -> RotateKeyResponse:
    """
    Invalidate the current API key and issue a new one.

    The new raw key is returned **once** — store it securely.
    """
    raw_key, _, key_prefix = rotate_api_key(db, user=current_user)
    return RotateKeyResponse(api_key=raw_key, key_prefix=key_prefix)
