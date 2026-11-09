from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, Response
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth, require_csrf
from app.api.errors import ApiError
from app.core.db import get_session
from app.models.routing import Exit, SiteRule
from app.services.presets import preset_list

router = APIRouter(prefix="/api/v1/sites", tags=["sites"])

MATCH_TYPES = {"domain", "wildcard", "regexp", "geosite"}


def _rule_dict(rule: SiteRule) -> dict[str, Any]:
    return {
        "id": rule.id,
        "name": rule.name,
        "match_type": rule.match_type,
        "values": rule.values_json,
        "allowed_exit_ids": rule.allowed_exit_ids,
        "preset_key": rule.preset_key,
        "enabled": rule.enabled,
        "position": rule.position,
        "created_at": rule.created_at.isoformat() if rule.created_at else None,
    }


def _validate_values(match_type: str, values: list[str]) -> None:
    if not values:
        raise ApiError("empty_values", "Укажите хотя бы один домен/geosite", 400)
    for value in values:
        stripped = value.strip().lower()
        if not stripped:
            raise ApiError("empty_values", "Пустое значение в списке", 400)
        if stripped in {"ru", "category-ru", "geoip:ru"} or stripped.endswith(".ru") or stripped == "ru":
            raise ApiError(
                "ru_direct_locked",
                "Российские домены/geosite:category-ru обслуживаются напрямую и не могут быть перенаправлены на выходы",
                400,
            )
        if match_type == "regexp":
            import re

            try:
                re.compile(stripped)
            except re.error as exc:
                raise ApiError("invalid_regexp", f"Некорректный regexp: {value}", 400) from exc


async def _validate_exits(db: AsyncSession, exit_ids: list[int]) -> None:
    if not exit_ids:
        raise ApiError("empty_allowlist", "Выберите хотя бы один разрешённый выход", 400)
    existing = set(
        (await db.scalars(select(Exit.id).where(Exit.id.in_(exit_ids)))).all()
    )
    missing = [exit_id for exit_id in exit_ids if exit_id not in existing]
    if missing:
        raise ApiError("unknown_exit", f"Неизвестные выходы: {missing}", 400)


@router.get("/presets")
async def site_presets(auth: AuthContext = Depends(get_auth)) -> list[dict]:
    return preset_list()


@router.get("")
async def list_sites(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rules = (await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all()
    return [_rule_dict(rule) for rule in rules]


@router.post("", status_code=201)
async def create_site(
    payload: dict,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    preset = payload.get("preset_key")
    match_type = str(payload.get("match_type", "domain"))
    values = payload.get("values")
    name = str(payload.get("name", "")).strip()
    if preset and not values:
        from app.services.presets import SITE_PRESETS

        preset_data = SITE_PRESETS.get(str(preset))
        if preset_data is None:
            raise ApiError("unknown_preset", "Неизвестный пресет", 400)
        match_type = preset_data["match_type"]
        values = list(preset_data["values"])
        name = name or preset_data["name"]
    if match_type not in MATCH_TYPES:
        raise ApiError("invalid_match_type", "match_type: domain, wildcard, regexp, geosite", 400)
    values = [str(value).strip() for value in (values or [])]
    _validate_values(match_type, values)
    try:
        allowed_exit_ids = [int(value) for value in payload.get("allowed_exit_ids", [])]
    except (TypeError, ValueError) as exc:
        raise ApiError("invalid_exits", "allowed_exit_ids должны быть числами", 400) from exc
    await _validate_exits(db, allowed_exit_ids)
    if not name:
        raise ApiError("invalid_name", "Укажите название правила", 400)
    position = (await db.scalar(select(func.max(SiteRule.position)))) or 0
    rule = SiteRule(
        name=name,
        match_type=match_type,
        values_json=values,
        allowed_exit_ids_json=allowed_exit_ids,
        preset_key=str(preset) if preset else None,
        enabled=True,
        position=int(position) + 10,
    )
    db.add(rule)
    await db.commit()
    return _rule_dict(rule)


@router.patch("/{rule_id}")
async def update_site(
    rule_id: int,
    payload: dict,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    rule = await db.get(SiteRule, rule_id)
    if rule is None:
        raise ApiError("site_not_found", "Правило не найдено", 404)
    if "name" in payload:
        rule.name = str(payload["name"]).strip() or rule.name
    if "values" in payload:
        match_type = str(payload.get("match_type", rule.match_type))
        values = [str(value).strip() for value in payload["values"]]
        _validate_values(match_type, values)
        rule.match_type = match_type
        rule.values_json = values
    if "allowed_exit_ids" in payload:
        exit_ids = [int(value) for value in payload["allowed_exit_ids"]]
        await _validate_exits(db, exit_ids)
        rule.allowed_exit_ids_json = exit_ids
    if "enabled" in payload:
        rule.enabled = bool(payload["enabled"])
    if "position" in payload:
        rule.position = int(payload["position"])
    await db.commit()
    return _rule_dict(rule)


@router.delete("/{rule_id}", status_code=204)
async def delete_site(
    rule_id: int,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> Response:
    rule = await db.get(SiteRule, rule_id)
    if rule is None:
        raise ApiError("site_not_found", "Правило не найдено", 404)
    await db.delete(rule)
    await db.commit()
    return Response(status_code=204)
