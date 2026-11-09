from __future__ import annotations

from dataclasses import dataclass

from fastapi import Depends, Request
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.errors import ApiError
from app.core.db import get_session
from app.core.security import tokens_equal
from app.models.auth import AuthSession, User
from app.services.auth import find_session, touch_session


@dataclass
class AuthContext:
    user: User
    session: AuthSession
    raw_token: str


def client_ip(request: Request) -> str:
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()[:64]
    return (request.client.host if request.client else "")[:64]


def user_agent(request: Request) -> str | None:
    ua = request.headers.get("user-agent")
    return ua[:512] if ua else None


async def get_auth(
    request: Request,
    db: AsyncSession = Depends(get_session),
) -> AuthContext:
    settings = request.app.state.settings
    raw = request.cookies.get(settings.cookie_name)
    if not raw:
        raise ApiError("unauthorized", "Требуется вход в панель", 401)
    session = await find_session(db, raw)
    if session is None:
        raise ApiError("unauthorized", "Сессия недействительна или истекла", 401)
    user = await db.get(User, session.user_id)
    if user is None:
        raise ApiError("unauthorized", "Пользователь не найден", 401)
    await touch_session(db, session)
    return AuthContext(user=user, session=session, raw_token=raw)


async def require_csrf(
    request: Request,
    auth: AuthContext = Depends(get_auth),
) -> AuthContext:
    header_name = request.app.state.settings.csrf_header
    provided = request.headers.get(header_name)
    if not provided or not tokens_equal(provided, auth.session.csrf_token):
        raise ApiError("csrf_failed", "Отсутствует или неверный CSRF-токен", 403)
    return auth
