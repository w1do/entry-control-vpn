from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, client_ip, get_auth, require_csrf, user_agent
from app.api.errors import ApiError
from app.core.db import get_session
from app.core.security import (
    hash_password,
    new_csrf_token,
    password_is_weak,
    tokens_equal,
    verify_password,
)
from app.core.timeutils import utcnow
from app.schemas.auth import (
    LoginRequest,
    LoginResponse,
    MeResponse,
    PasswordChangeRequest,
    UserOut,
)
from app.services.audit import add_event
from app.services.auth import (
    create_session,
    delete_session,
    get_user_by_email,
    revoke_user_sessions,
)

router = APIRouter(prefix="/api/v1/auth", tags=["auth"])


def _set_session_cookie(response: Response, request: Request, raw_token: str) -> None:
    settings = request.app.state.settings
    response.set_cookie(
        key=settings.cookie_name,
        value=raw_token,
        max_age=settings.session_ttl_seconds,
        httponly=True,
        secure=settings.session_cookie_secure,
        samesite="lax",
        path="/",
    )


def _clear_session_cookie(response: Response, request: Request) -> None:
    settings = request.app.state.settings
    response.delete_cookie(
        key=settings.cookie_name,
        path="/",
        secure=settings.session_cookie_secure,
        httponly=True,
        samesite="lax",
    )


@router.post("/login", response_model=LoginResponse)
async def login(
    payload: LoginRequest,
    request: Request,
    response: Response,
    db: AsyncSession = Depends(get_session),
) -> LoginResponse:
    settings = request.app.state.settings
    ip = client_ip(request)
    limiter = request.app.state.login_limiter
    retry_after = limiter.retry_after(ip)
    if retry_after is not None:
        raise ApiError(
            "rate_limited",
            "Слишком много попыток входа. Повторите позже.",
            429,
            headers={"Retry-After": str(retry_after)},
        )

    user = await get_user_by_email(db, payload.email)
    if not verify_password(payload.password, user.password_hash if user else None):
        limiter.fail(ip)
        add_event(db, "login_failed", actor=payload.email, ip=ip, user_agent=user_agent(request))
        await db.commit()
        raise ApiError("invalid_credentials", "Неверный email или пароль", 401)

    assert user is not None
    limiter.reset(ip)
    await revoke_user_sessions(db, user.id)
    session, raw_token = await create_session(
        db, user, settings, ip=ip, user_agent=user_agent(request)
    )
    add_event(
        db,
        "login_succeeded",
        actor=user.email,
        ip=ip,
        user_agent=user_agent(request),
        payload={"session_id": session.id},
    )
    await db.commit()
    _set_session_cookie(response, request, raw_token)
    return LoginResponse(user=UserOut(email=user.email), csrf_token=session.csrf_token)


@router.post("/logout", status_code=204)
async def logout(
    request: Request,
    response: Response,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> Response:
    await delete_session(db, auth.raw_token)
    add_event(
        db,
        "logout",
        actor=auth.user.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await db.commit()
    _clear_session_cookie(response, request)
    response.status_code = 204
    return response


@router.get("/me", response_model=MeResponse)
async def me(auth: AuthContext = Depends(get_auth)) -> MeResponse:
    return MeResponse(
        email=auth.user.email,
        csrf_token=auth.session.csrf_token,
        weak_password=bool(auth.user.must_change_password),
    )


@router.post("/password", response_model=MeResponse)
async def change_password(
    payload: PasswordChangeRequest,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> MeResponse:
    if not verify_password(payload.current_password, auth.user.password_hash):
        raise ApiError("invalid_current_password", "Текущий пароль неверен", 400)
    if password_is_weak(payload.new_password):
        raise ApiError("weak_password", "Новый пароль должен быть не короче 12 символов", 400)
    if tokens_equal(payload.current_password, payload.new_password):
        raise ApiError("same_password", "Новый пароль совпадает с текущим", 400)
    auth.user.password_hash = hash_password(payload.new_password)
    auth.user.must_change_password = False
    await revoke_user_sessions(db, auth.user.id, except_id=auth.session.id)
    new_csrf = new_csrf_token()
    auth.session.csrf_token = new_csrf
    auth.session.last_seen_at = utcnow()
    add_event(
        db,
        "password_changed",
        actor=auth.user.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
    )
    await db.commit()
    return MeResponse(email=auth.user.email, csrf_token=new_csrf, weak_password=False)


@router.get("/sessions")
async def list_sessions(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    from app.models.auth import AuthSession

    rows = (
        await db.scalars(
            select(AuthSession)
            .where(AuthSession.user_id == auth.user.id, AuthSession.revoked_at.is_(None))
            .order_by(AuthSession.created_at.desc())
        )
    ).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat(),
            "last_seen_at": row.last_seen_at.isoformat(),
            "expires_at": row.expires_at.isoformat(),
            "ip": row.ip,
            "user_agent": row.user_agent,
            "current": row.id == auth.session.id,
        }
        for row in rows
    ]
