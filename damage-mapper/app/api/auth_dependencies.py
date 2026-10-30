from __future__ import annotations

from datetime import datetime, timezone
from typing import Annotated, Callable

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.dependencies import get_db
from app.models import AuthSession, User
from app.services.auth import hash_token

DatabaseSession = Annotated[Session, Depends(get_db)]


def get_current_user(
    session: DatabaseSession,
    authorization: Annotated[str | None, Header()] = None,
) -> User:
    scheme, separator, token = (authorization or "").partition(" ")
    if not separator or scheme.lower() != "bearer" or not token.strip():
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Bearer authentication is required",
            headers={"WWW-Authenticate": "Bearer"},
        )
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token.strip()))
    )
    now = datetime.now(timezone.utc)
    expires_at = auth_session.expires_at if auth_session is not None else now
    if expires_at.tzinfo is None:
        expires_at = expires_at.replace(tzinfo=timezone.utc)
    if (
        auth_session is None
        or auth_session.revoked_at is not None
        or expires_at <= now
    ):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Session is invalid or expired",
            headers={"WWW-Authenticate": "Bearer"},
        )
    user = session.get(User, auth_session.user_id)
    if user is None or not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User account is inactive",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]


def require_roles(*roles: str) -> Callable:
    def dependency(user: CurrentUser) -> User:
        if user.role not in roles:
            raise HTTPException(
                status_code=status.HTTP_403_FORBIDDEN,
                detail="Your account is not permitted to perform this action",
            )
        return user

    return dependency


ResponderOrAdmin = Annotated[
    User, Depends(require_roles("responder", "admin"))
]
AdminUser = Annotated[User, Depends(require_roles("admin"))]
