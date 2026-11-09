from __future__ import annotations

from datetime import timedelta

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.security import hash_password, new_csrf_token, new_token, password_is_weak, sha256_hex
from app.core.timeutils import ensure_utc, utcnow
from app.models.auth import AuthSession, User
from app.services.audit import add_event


async def bootstrap_admin(db: AsyncSession, settings: Settings) -> User | None:
    """Create the single admin from ADMIN/PASSWORD on first start only."""
    count = await db.scalar(select(User.id).limit(1))
    if count is not None:
        return None
    user = User(
        email=settings.admin.strip().lower(),
        password_hash=hash_password(settings.password),
        must_change_password=password_is_weak(settings.password),
    )
    db.add(user)
    add_event(
        db,
        "admin_bootstrapped",
        actor=user.email,
        payload={"weak_password": password_is_weak(settings.password)},
    )
    await db.commit()
    return user


async def get_user_by_email(db: AsyncSession, email: str) -> User | None:
    return await db.scalar(select(User).where(User.email == email.strip().lower()))


async def create_session(
    db: AsyncSession,
    user: User,
    settings: Settings,
    *,
    ip: str | None,
    user_agent: str | None,
) -> tuple[AuthSession, str]:
    raw_token = new_token(32)
    row = AuthSession(
        user_id=user.id,
        token_hash=sha256_hex(raw_token),
        csrf_token=new_csrf_token(),
        expires_at=utcnow() + timedelta(seconds=settings.session_ttl_seconds),
        ip=ip,
        user_agent=(user_agent or "")[:512] or None,
    )
    db.add(row)
    user.last_login_at = utcnow()
    await db.flush()
    return row, raw_token


async def revoke_user_sessions(db: AsyncSession, user_id: int, *, except_id: int | None = None) -> None:
    stmt = update(AuthSession).where(
        AuthSession.user_id == user_id, AuthSession.revoked_at.is_(None)
    )
    if except_id is not None:
        stmt = stmt.where(AuthSession.id != except_id)
    await db.execute(stmt.values(revoked_at=utcnow()))


async def find_session(db: AsyncSession, raw_token: str) -> AuthSession | None:
    row = await db.scalar(
        select(AuthSession).where(AuthSession.token_hash == sha256_hex(raw_token))
    )
    if row is None:
        return None
    if row.revoked_at is not None:
        return None
    if ensure_utc(row.expires_at) <= utcnow():
        return None
    return row


async def touch_session(db: AsyncSession, row: AuthSession) -> None:
    now = utcnow()
    last = ensure_utc(row.last_seen_at)
    if last is None or (now - last).total_seconds() > 60:
        row.last_seen_at = now
        await db.commit()


async def delete_session(db: AsyncSession, raw_token: str) -> None:
    await db.execute(delete(AuthSession).where(AuthSession.token_hash == sha256_hex(raw_token)))


__all__ = [
    "bootstrap_admin",
    "create_session",
    "delete_session",
    "find_session",
    "get_user_by_email",
    "revoke_user_sessions",
    "touch_session",
]
