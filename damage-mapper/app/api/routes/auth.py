from __future__ import annotations

import hmac
from typing import Annotated

from fastapi import APIRouter, Header, HTTPException, Request, status
from fastapi.responses import Response
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.api.auth_dependencies import AdminUser, CurrentUser, DatabaseSession
from app.core.config import get_settings
from app.models import AuthBootstrapClaim, AuthSession, User
from app.schemas.auth import (
    AdminBootstrap,
    AdminUserCreate,
    AdminUserUpdate,
    AuthTokenRead,
    Credentials,
    CitizenRegistration,
    UserRead,
)
from app.services.audit import record_audit
from app.services.auth import (
    RateLimitExceeded,
    enforce_rate_limit,
    find_user_by_email,
    hash_password,
    hash_token,
    issue_session,
    verify_password,
)

router = APIRouter(prefix="/api/auth", tags=["Authentication"])


def _client_address(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _apply_limit(
    session: Session,
    request: Request,
    *,
    action: str,
    limit: int,
) -> None:
    try:
        enforce_rate_limit(
            session,
            action=action,
            client_address=_client_address(request),
            limit=limit,
            window_seconds=900 if action in {"login", "register"} else 60,
        )
    except RateLimitExceeded as error:
        raise HTTPException(
            status_code=429,
            detail=str(error),
            headers={"Retry-After": str(error.retry_after)},
        ) from error


def _new_user(
    session: Session,
    *,
    email: str,
    display_name: str,
    password: str,
    role: str,
    commit: bool = True,
) -> User:
    if find_user_by_email(session, email):
        raise HTTPException(status_code=409, detail="An account with this email already exists")
    user = User(
        email=email,
        display_name=display_name,
        role=role,
        password_hash=hash_password(password),
        is_active=True,
    )
    session.add(user)
    if commit:
        session.commit()
        session.refresh(user)
    else:
        session.flush()
    return user


def _token_response(session: Session, user: User) -> AuthTokenRead:
    token, expires_at = issue_session(session, user)
    return AuthTokenRead(
        access_token=token,
        expires_at=expires_at,
        user=UserRead.model_validate(user),
    )


@router.post("/register", response_model=AuthTokenRead, status_code=201)
def register_citizen(
    payload: CitizenRegistration,
    request: Request,
    session: DatabaseSession,
) -> AuthTokenRead:
    _apply_limit(
        session,
        request,
        action="register",
        limit=get_settings().auth_register_limit,
    )
    user = _new_user(
        session,
        email=payload.email,
        display_name=payload.display_name,
        password=payload.password.get_secret_value(),
        role="citizen",
        commit=False,
    )
    record_audit(
        session,
        "citizen_registered",
        {"user_id": user.id},
        actor_user_id=user.id,
    )
    session.commit()
    return _token_response(session, user)


@router.post("/login", response_model=AuthTokenRead)
def login(
    payload: Credentials,
    request: Request,
    session: DatabaseSession,
) -> AuthTokenRead:
    _apply_limit(
        session,
        request,
        action="login",
        limit=get_settings().auth_login_limit,
    )
    password = payload.password.get_secret_value()
    user = find_user_by_email(session, payload.email)
    password_valid = verify_password(
        password,
        user.password_hash if user is not None and user.is_active else None,
    )
    if user is None or not user.is_active or not password_valid:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Email or password is incorrect",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return _token_response(session, user)


@router.get("/me", response_model=UserRead)
def current_user(user: CurrentUser) -> UserRead:
    return UserRead.model_validate(user)


@router.post("/logout", status_code=204, response_class=Response)
def logout(
    user: CurrentUser,
    session: DatabaseSession,
    authorization: Annotated[str, Header()],
) -> Response:
    token = authorization.partition(" ")[2].strip()
    auth_session = session.scalar(
        select(AuthSession).where(AuthSession.token_hash == hash_token(token))
    )
    if auth_session is not None and auth_session.revoked_at is None:
        from datetime import datetime, timezone

        auth_session.revoked_at = datetime.now(timezone.utc)
        record_audit(
            session,
            "user_logged_out",
            {"user_id": user.id},
            actor_user_id=user.id,
        )
        session.commit()
    return Response(status_code=204)


@router.post("/bootstrap-admin", response_model=AuthTokenRead, status_code=201)
def bootstrap_admin(
    payload: AdminBootstrap,
    request: Request,
    session: DatabaseSession,
    x_admin_bootstrap_secret: Annotated[str | None, Header()] = None,
) -> AuthTokenRead:
    _apply_limit(
        session,
        request,
        action="bootstrap",
        limit=5,
    )
    expected = get_settings().auth_bootstrap_secret
    if len(expected) < 32 or x_admin_bootstrap_secret is None:
        raise HTTPException(
            status_code=503,
            detail="Admin bootstrap is disabled; configure a 32-character DM_ADMIN_BOOTSTRAP_SECRET",
        )
    if not hmac.compare_digest(x_admin_bootstrap_secret, expected):
        raise HTTPException(status_code=403, detail="Bootstrap secret is invalid")
    if session.get(AuthBootstrapClaim, "first-admin") is not None or session.scalar(
        select(User.id).where(
            User.role == "admin",
            User.is_active.is_(True),
            User.password_hash.is_not(None),
        ).limit(1)
    ):
        raise HTTPException(status_code=409, detail="An administrator already exists")
    user = _new_user(
        session,
        email=payload.email,
        display_name=payload.display_name,
        password=payload.password.get_secret_value(),
        role="admin",
        commit=False,
    )
    session.add(
        AuthBootstrapClaim(id="first-admin", user_id=user.id)
    )
    record_audit(
        session,
        "administrator_bootstrapped",
        {"user_id": user.id},
        actor_user_id=user.id,
    )
    session.commit()
    return _token_response(session, user)


@router.get("/users", response_model=list[UserRead])
def list_users(
    _: AdminUser,
    session: DatabaseSession,
) -> list[UserRead]:
    users = session.scalars(
        select(User).where(User.is_active.is_(True)).order_by(User.display_name)
    ).all()
    return [UserRead.model_validate(user) for user in users]


@router.post("/users", response_model=UserRead, status_code=201)
def create_elevated_user(
    payload: AdminUserCreate,
    actor: AdminUser,
    session: DatabaseSession,
) -> UserRead:
    user = _new_user(
        session,
        email=payload.email,
        display_name=payload.display_name,
        password=payload.password.get_secret_value(),
        role=payload.role,
        commit=False,
    )
    record_audit(
        session,
        "user_provisioned",
        {"user_id": user.id, "role": user.role},
        actor_user_id=actor.id,
    )
    session.commit()
    return UserRead.model_validate(user)


@router.patch("/users/{user_id}", response_model=UserRead)
def update_user(
    user_id: str,
    payload: AdminUserUpdate,
    actor: AdminUser,
    session: DatabaseSession,
) -> UserRead:
    user = session.get(User, user_id)
    if user is None:
        raise HTTPException(status_code=404, detail="User account not found")
    next_role = payload.role if "role" in payload.model_fields_set else user.role
    next_active = payload.is_active if "is_active" in payload.model_fields_set else user.is_active
    removes_admin = user.role == "admin" and user.is_active and (
        next_role != "admin" or next_active is False
    )
    if removes_admin:
        admins = session.scalars(
            select(User.id).where(
                User.role == "admin", User.is_active.is_(True)
            )
        ).all()
        if len(admins) <= 1:
            raise HTTPException(
                status_code=409,
                detail="At least one active administrator must remain",
            )
    if user.id == actor.id and (
        (payload.is_active is False)
        or (payload.role is not None and payload.role != "admin")
    ):
        raise HTTPException(
            status_code=409,
            detail="Administrators cannot disable or demote their own account",
        )
    if payload.role is not None:
        user.role = payload.role
    if payload.is_active is not None:
        user.is_active = payload.is_active
    if payload.password is not None:
        user.password_hash = hash_password(payload.password.get_secret_value())
    if payload.model_fields_set & {"role", "is_active", "password"}:
        from datetime import datetime, timezone

        for auth_session in session.scalars(
            select(AuthSession).where(
                AuthSession.user_id == user.id,
                AuthSession.revoked_at.is_(None),
            )
        ):
            auth_session.revoked_at = datetime.now(timezone.utc)
    record_audit(
        session,
        "user_account_updated",
        {
            "user_id": user.id,
            "role": user.role,
            "is_active": user.is_active,
            "password_changed": payload.password is not None,
        },
        actor_user_id=actor.id,
    )
    session.commit()
    session.refresh(user)
    return UserRead.model_validate(user)
