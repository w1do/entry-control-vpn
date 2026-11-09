"""Incidents API: list, acknowledge, resolve.

All endpoints require the admin session; mutations additionally require the CSRF
header (require_csrf).  Ack/resolve write an audit Event with the actor taken
from the authenticated session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, client_ip, get_auth, require_csrf, user_agent
from app.api.errors import ApiError
from app.core.db import get_session
from app.core.timeutils import utcnow
from app.models.ops import Incident
from app.services.audit import add_event

router = APIRouter(prefix="/api/v1/incidents", tags=["incidents"])

STATUSES = ("open", "ack", "resolved")
SEVERITIES = ("info", "warning", "critical")


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def incident_dict(row: Incident) -> dict[str, Any]:
    return {
        "id": row.id,
        "severity": row.severity,
        "kind": row.kind,
        "subject": row.subject,
        "status": row.status,
        "details": row.details_json or {},
        "created_at": _iso(row.created_at),
        "acked_at": _iso(row.acked_at),
        "resolved_at": _iso(row.resolved_at),
    }


@router.get("")
async def list_incidents(
    status: str | None = Query(default=None),
    severity: str | None = Query(default=None),
    limit: int = Query(default=50, ge=1, le=200),
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise ApiError(
            "invalid_status", "status: " + ", ".join(STATUSES), 400
        )
    if severity is not None and severity not in SEVERITIES:
        raise ApiError(
            "invalid_severity", "severity: " + ", ".join(SEVERITIES), 400
        )
    stmt = select(Incident).order_by(Incident.created_at.desc(), Incident.id.desc()).limit(limit)
    if status is not None:
        stmt = stmt.where(Incident.status == status)
    if severity is not None:
        stmt = stmt.where(Incident.severity == severity)
    rows = (await db.scalars(stmt)).all()
    return [incident_dict(row) for row in rows]


async def _get_incident(db: AsyncSession, incident_id: int) -> Incident:
    row = await db.get(Incident, incident_id)
    if row is None:
        raise ApiError("incident_not_found", "Инцидент не найден", 404)
    return row


def _audit(
    db: AsyncSession,
    request: Request,
    auth: AuthContext,
    kind: str,
    incident: Incident,
) -> None:
    add_event(
        db,
        kind,
        actor=auth.user.email,
        ip=client_ip(request),
        user_agent=user_agent(request),
        payload={
            "incident_id": incident.id,
            "incident_kind": incident.kind,
            "severity": incident.severity,
            "subject": incident.subject,
        },
    )


@router.post("/{incident_id}/ack")
async def ack_incident(
    incident_id: int,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await _get_incident(db, incident_id)
    if incident.status == "resolved":
        raise ApiError("incident_resolved", "Инцидент уже закрыт", 409)
    if incident.status != "ack":
        incident.status = "ack"
        incident.acked_at = utcnow()
        _audit(db, request, auth, "incident_ack", incident)
    await db.commit()
    return incident_dict(incident)


@router.post("/{incident_id}/resolve")
async def resolve_incident(
    incident_id: int,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict[str, Any]:
    incident = await _get_incident(db, incident_id)
    if incident.status != "resolved":
        incident.status = "resolved"
        incident.resolved_at = utcnow()
        _audit(db, request, auth, "incident_resolve", incident)
    await db.commit()
    return incident_dict(incident)
