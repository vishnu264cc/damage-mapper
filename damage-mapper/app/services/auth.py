from __future__ import annotations

import hashlib
import secrets
from datetime import datetime, timedelta, timezone

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.dialects.postgresql import insert as postgres_insert
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.models import AuthRateLimit, AuthSession, User

_password_hasher = PasswordHasher()
_DUMMY_PASSWORD_HASH = _password_hasher.hash(secrets.token_urlsafe(32))


def hash_password(password: str) -> str:
    return _password_hasher.hash(password)


def verify_password(password: str, password_hash: str | None) -> bool:
    candidate_hash = password_hash or _DUMMY_PASSWORD_HASH
    try:
        matches = _password_hasher.verify(candidate_hash, password)
    except (InvalidHashError, VerificationError, VerifyMismatchError):
        return False
    return bool(password_hash and matches)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def issue_session(session: Session, user: User) -> tuple[str, datetime]:
    token = secrets.token_urlsafe(32)
    now = datetime.now(timezone.utc)
    expires_at = now + timedelta(
        minutes=get_settings().auth_token_ttl_minutes
    )
    session.execute(
        delete(AuthSession).where(
            AuthSession.expires_at < now - timedelta(days=30)
        )
    )
    session.add(
        AuthSession(
            token_hash=hash_token(token),
            user_id=user.id,
            expires_at=expires_at,
        )
    )
    session.commit()
    session.refresh(user)
    return token, expires_at


def enforce_rate_limit(
    session: Session,
    *,
    action: str,
    client_address: str,
    limit: int,
    window_seconds: int = 60,
) -> None:
    now_epoch = int(datetime.now(timezone.utc).timestamp())
    window_start_epoch = now_epoch - now_epoch % window_seconds
    window_started_at = datetime.fromtimestamp(window_start_epoch, timezone.utc)
    address_hash = hashlib.sha256(client_address.encode("utf-8")).hexdigest()
    bucket_key = hashlib.sha256(
        f"{action}:{address_hash}:{window_start_epoch}".encode("utf-8")
    ).hexdigest()
    dialect = session.get_bind().dialect.name
    insert = {"sqlite": sqlite_insert, "postgresql": postgres_insert}.get(dialect)
    if insert is None:
        raise RuntimeError(f"Persistent rate limiting is unsupported for {dialect}")
    statement = insert(AuthRateLimit).values(
        bucket_key=bucket_key,
        request_count=1,
        window_started_at=window_started_at,
    )
    statement = statement.on_conflict_do_update(
        index_elements=[AuthRateLimit.bucket_key],
        set_={"request_count": AuthRateLimit.request_count + 1},
    ).returning(AuthRateLimit.request_count)
    count = session.scalar(statement)
    session.execute(
        AuthRateLimit.__table__.delete().where(
            AuthRateLimit.window_started_at
            < datetime.now(timezone.utc) - timedelta(days=2)
        )
    )
    session.commit()
    if count is not None and count > limit:
        raise RateLimitExceeded(
            "Too many requests; wait before trying again",
            max(1, window_seconds - (now_epoch - window_start_epoch)),
        )


class RateLimitExceeded(Exception):
    def __init__(self, message: str, retry_after: int) -> None:
        super().__init__(message)
        self.retry_after = retry_after


def find_user_by_email(session: Session, email: str) -> User | None:
    return session.scalar(select(User).where(User.email == email))
