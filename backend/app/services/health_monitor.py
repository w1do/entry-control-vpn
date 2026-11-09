"""Health monitor: creates and resolves incidents from node/exit state.

Called periodically (see scheduler_setup.py).  The monitor is idempotent: an
active (open or acknowledged) incident is never duplicated, and it is resolved
as soon as the underlying condition is gone:

* server status offline -> Incident(critical, server_offline, subject=server:<id>);
* server back online/degraded -> resolve;
* exit health down and the exit is in the allowlist of at least one enabled
  site rule -> Incident(warning, exit_down, subject=exit:<id>, details.sites);
* exit up again or no rule uses it -> resolve.

The caller commits the session.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.timeutils import ensure_utc, utcnow
from app.models.ops import Incident
from app.models.routing import Exit, SiteRule
from app.models.servers import Server

SERVER_OFFLINE_KIND = "server_offline"
EXIT_DOWN_KIND = "exit_down"
INCIDENT_KINDS = (SERVER_OFFLINE_KIND, EXIT_DOWN_KIND)


def _active_incidents(rows: list[Incident]) -> dict[str, Incident]:
    return {row.subject: row for row in rows if row.status != "resolved"}


def _ensure_incident(
    session: AsyncSession,
    active: dict[str, Incident],
    *,
    kind: str,
    subject: str,
    severity: str,
    details: dict[str, Any],
    now: datetime,
) -> bool:
    """Create the incident unless an active one exists; returns True when created."""
    existing = active.get(subject)
    if existing is not None:
        if existing.details_json != details:
            existing.details_json = details
        return False
    incident = Incident(
        severity=severity,
        kind=kind,
        subject=subject,
        status="open",
        details_json=details,
        created_at=now,
    )
    session.add(incident)
    active[subject] = incident
    return True


def _resolve(incident: Incident, now: datetime) -> None:
    incident.status = "resolved"
    incident.resolved_at = now


def _iso(value: datetime | None) -> str | None:
    return value.isoformat() if value else None


def _rule_uses_exit(rule: SiteRule, exit_id: int) -> bool:
    for value in rule.allowed_exit_ids_json or []:
        try:
            if int(value) == exit_id:
                return True
        except (TypeError, ValueError):
            continue
    return False


async def run_health_monitor(
    session: AsyncSession, now: datetime | None = None
) -> dict[str, int]:
    """Create/resolve server_offline and exit_down incidents; caller commits."""
    moment = ensure_utc(now) or utcnow()
    rows = list(
        (
            await session.scalars(
                select(Incident).where(
                    Incident.kind.in_(INCIDENT_KINDS),
                    Incident.status != "resolved",
                )
            )
        ).all()
    )
    active = _active_incidents(rows)
    created = 0
    resolved = 0

    servers = list((await session.scalars(select(Server))).all())
    for server in servers:
        subject = f"server:{server.id}"
        if server.status == "offline":
            details = {
                "server_id": server.id,
                "name": server.name,
                "roles": server.roles,
                "region": server.region,
                "last_seen_at": _iso(ensure_utc(server.last_seen_at)),
            }
            if _ensure_incident(
                session,
                active,
                kind=SERVER_OFFLINE_KIND,
                subject=subject,
                severity="critical",
                details=details,
                now=moment,
            ):
                created += 1
        else:
            incident = active.get(subject)
            if incident is not None and incident.kind == SERVER_OFFLINE_KIND:
                _resolve(incident, moment)
                resolved += 1

    exits = list((await session.scalars(select(Exit))).all())
    rules = list(
        (await session.scalars(select(SiteRule).where(SiteRule.enabled.is_(True)))).all()
    )
    affected: dict[str, dict[str, Any]] = {}
    for exit_spec in exits:
        if exit_spec.health != "down":
            continue
        sites = [
            {"id": rule.id, "name": rule.name}
            for rule in rules
            if _rule_uses_exit(rule, exit_spec.id)
        ]
        if not sites:
            continue
        affected[f"exit:{exit_spec.id}"] = {
            "details": {
                "exit_id": exit_spec.id,
                "label": exit_spec.label,
                "region": exit_spec.region,
                "last_check_at": _iso(ensure_utc(exit_spec.last_check_at)),
                "sites": sites,
            }
        }
    for subject, payload in affected.items():
        if _ensure_incident(
            session,
            active,
            kind=EXIT_DOWN_KIND,
            subject=subject,
            severity="warning",
            details=payload["details"],
            now=moment,
        ):
            created += 1
    for subject, incident in list(active.items()):
        if incident.kind == EXIT_DOWN_KIND and subject not in affected:
            _resolve(incident, moment)
            resolved += 1

    await session.flush()
    return {
        "created": created,
        "resolved": resolved,
        "open": sum(1 for incident in active.values() if incident.status != "resolved"),
    }
