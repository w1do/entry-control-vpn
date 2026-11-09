from __future__ import annotations

import dataclasses
import json
from pathlib import Path
from typing import Any

from fastapi import APIRouter, Depends, Request
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.api.deps import AuthContext, get_auth, require_csrf
from app.api.errors import ApiError
from app.compiler import (
    ClientSpec,
    CompileError,
    EntryCompileInput,
    EntryNodeSpec,
    ExitCompileInput,
    ExitNodeSpec,
    ExitOutboundSpec,
    ExitSpec,
    RealityInboundSpec,
    RoutingSpec,
    SiteRuleSpec,
    compile_entry_config,
    compile_exit_config,
    match_route,
    sha256_json,
    validate_routing,
)
from app.core.db import get_session
from app.models.routing import Client, Exit, RoutingProfile, RoutingRevision, SiteRule
from app.models.servers import Server, ServerConfig, ServerSecret
from app.services.jobs import create_job

router = APIRouter(prefix="/api/v1/routing", tags=["routing"])

INTERCONNECT_PORT = 18443


async def _default_profile(db: AsyncSession) -> RoutingProfile:
    profile = await db.get(RoutingProfile, 1)
    if profile is None:
        profile = RoutingProfile(id=1, default_exit_ids_json=[], enabled=False)
        db.add(profile)
        await db.commit()
    return profile


def _all_secrets(rows: list[ServerSecret]) -> dict[int, ServerSecret]:
    return {row.server_id: row for row in rows}


def _exit_specs(db_exits: list[Exit], secrets: dict[int, ServerSecret], box: Any) -> tuple[ExitSpec, ...]:
    specs: list[ExitSpec] = []
    for exit in db_exits:
        if exit.kind == "node":
            secret = secrets.get(exit.server_id or -1)
            if secret is None or not secret.reality_public_key:
                continue
            interconnect_uuid = box.decrypt(secret.interconnect_uuid_enc) if secret.interconnect_uuid_enc else ""
            outbound = ExitOutboundSpec(
                kind="vless_reality",
                host=exit.endpoint_host,
                port=exit.endpoint_port,
                uuid=interconnect_uuid,
                public_key=secret.reality_public_key,
                short_id=(secret.short_ids_json or [""])[0],
                server_name=secret.real_sni,
                fingerprint="chrome",
                spider_x="/",
            )
        else:
            credentials: dict = {}
            if exit.secret_enc:
                try:
                    credentials = json.loads(box.decrypt(exit.secret_enc))
                except ValueError:
                    credentials = {}
            outbound = ExitOutboundSpec(
                kind="http" if exit.protocol == "http" else "socks5",
                host=exit.endpoint_host,
                port=exit.endpoint_port,
                username=credentials.get("username"),
                password=credentials.get("password"),
                expected_exit_ip=exit.expected_exit_ip,
            )
        specs.append(
            ExitSpec(
                id=exit.id,
                label=exit.label,
                region=exit.region or "",
                outbound=outbound,
                enabled=exit.enabled,
                health=exit.health,
            )
        )
    return tuple(specs)


def _site_specs(rules: list[SiteRule]) -> tuple[SiteRuleSpec, ...]:
    return tuple(
        SiteRuleSpec(
            id=rule.id,
            name=rule.name,
            match_type=rule.match_type,
            values=tuple(rule.values_json or ()),
            allowed_exit_ids=tuple(rule.allowed_exit_ids),
            enabled=rule.enabled,
            position=rule.position,
        )
        for rule in rules
    )


def _routing_spec(
    profile: RoutingProfile, exits: list[Exit], rules: list[SiteRule], secrets: dict[int, ServerSecret], box: Any
) -> RoutingSpec:
    return RoutingSpec(
        exits=_exit_specs(exits, secrets, box),
        default_exit_ids=tuple(profile.default_exit_ids),
        site_rules=_site_specs(rules),
        enabled=profile.enabled,
    )


def _profile_issues(spec: RoutingSpec) -> list[dict[str, Any]]:
    issues = validate_routing(spec)
    result = [
        {"code": issue.code, "message": issue.message, "subject": issue.subject} for issue in issues
    ]
    if spec.enabled and not spec.default_exit_ids:
        result.append(
            {
                "code": "default_exit_missing",
                "message": "Не выбран выход по умолчанию",
                "subject": None,
            }
        )
    if spec.enabled:
        usable = [exit for exit in spec.exits if exit.enabled and exit.health != "down"]
        if not usable:
            result.append(
                {
                    "code": "no_healthy_exits",
                    "message": "Нет ни одного доступного выхода",
                    "subject": None,
                }
            )
    return result


@router.get("/profile")
async def get_profile(
    request: Request,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    profile = await _default_profile(db)
    exits = list((await db.scalars(select(Exit).order_by(Exit.id))).all())
    rules = list((await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all())
    secrets = _all_secrets(list((await db.scalars(select(ServerSecret))).all()))
    box = request.app.state.secret_box
    spec = _routing_spec(profile, exits, rules, secrets, box)
    issues = _profile_issues(spec)
    return {
        "enabled": profile.enabled,
        "default_exit_ids": list(profile.default_exit_ids),
        "issues": issues,
        "ready": profile.enabled and not issues,
        "exits": [
            {
                "id": exit.id,
                "label": exit.label,
                "region": exit.region,
                "kind": exit.kind,
                "enabled": exit.enabled,
                "health": exit.health,
            }
            for exit in exits
        ],
        "sites": [
            {
                "id": rule.id,
                "name": rule.name,
                "allowed_exit_ids": rule.allowed_exit_ids,
                "enabled": rule.enabled,
            }
            for rule in rules
        ],
    }


@router.put("/profile")
async def update_profile(
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    profile = await _default_profile(db)
    exits = list((await db.scalars(select(Exit).order_by(Exit.id))).all())
    rules = list((await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all())
    secrets = _all_secrets(list((await db.scalars(select(ServerSecret))).all()))
    box = request.app.state.secret_box
    if "default_exit_ids" in payload:
        try:
            ids = tuple(int(value) for value in payload["default_exit_ids"])
        except (TypeError, ValueError) as exc:
            raise ApiError("invalid_default_exits", "default_exit_ids должны быть числами", 400) from exc
        known = {exit.id for exit in exits}
        unknown = [exit_id for exit_id in ids if exit_id not in known]
        if unknown:
            raise ApiError("unknown_exit", f"Неизвестные выходы: {unknown}", 400)
        profile.default_exit_ids_json = list(ids)
    if "enabled" in payload:
        profile.enabled = bool(payload["enabled"])
    await db.flush()
    spec = _routing_spec(profile, exits, rules, secrets, box)
    issues = _profile_issues(spec)
    if profile.enabled and issues:
        await db.rollback()
        raise ApiError(
            "routing_not_ready",
            "Профиль не готов: " + "; ".join(issue["message"] for issue in issues),
            400,
            details={"issues": issues},
        )
    await db.commit()
    return {"enabled": profile.enabled, "default_exit_ids": list(profile.default_exit_ids), "issues": issues}


@router.post("/check")
async def check_route(
    payload: dict,
    request: Request,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    domain = str(payload.get("domain", "")).strip().lower()
    if not domain:
        raise ApiError("invalid_domain", "Укажите домен", 400)
    profile = await _default_profile(db)
    exits = list((await db.scalars(select(Exit).order_by(Exit.id))).all())
    rules = list((await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all())
    secrets = _all_secrets(list((await db.scalars(select(ServerSecret))).all()))
    spec = _routing_spec(profile, exits, rules, secrets, request.app.state.secret_box)
    artifacts = Path(request.app.state.settings.artifacts_dir)
    geodata_kwargs: dict[str, str] = {}
    if (artifacts / "geosite.dat").is_file():
        geodata_kwargs["geosite_path"] = str(artifacts / "geosite.dat")
    if (artifacts / "geoip.dat").is_file():
        geodata_kwargs["geoip_path"] = str(artifacts / "geoip.dat")
    decision = match_route(domain, spec, **geodata_kwargs)
    payload = dataclasses.asdict(decision)
    payload["geodata"] = "exact" if geodata_kwargs else "heuristic"
    return payload


@router.post("/apply")
async def apply_routing(
    request: Request,
    auth: AuthContext = Depends(require_csrf),
    db: AsyncSession = Depends(get_session),
) -> dict:
    profile = await _default_profile(db)
    exits = list((await db.scalars(select(Exit).order_by(Exit.id))).all())
    rules = list((await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all())
    secrets = _all_secrets(list((await db.scalars(select(ServerSecret))).all()))
    box = request.app.state.secret_box
    spec = _routing_spec(profile, exits, rules, secrets, box)
    issues = _profile_issues(spec)
    if issues:
        raise ApiError(
            "routing_not_ready",
            "Профиль не готов к применению",
            400,
            details={"issues": issues},
        )
    servers = list((await db.scalars(select(Server).order_by(Server.id))).all())
    entry_servers = [server for server in servers if server.is_entry]
    exit_servers = [server for server in servers if server.is_exit]
    clients = list((await db.scalars(select(Client).where(Client.enabled.is_(True)).order_by(Client.id))).all())
    if not entry_servers:
        raise ApiError("no_entry_servers", "Нет ни одного entry-узла", 409)

    revision = RoutingRevision(status="pending", profile_json={}, config_hashes_json={})
    db.add(revision)
    await db.flush()

    configs: dict[int, dict] = {}
    hashes: dict[str, str] = {}
    peers: list = []
    for server in entry_servers:
        secret = secrets.get(server.id)
        if secret is None or not secret.reality_private_key_enc:
            continue
        interconnect_uuid = box.decrypt(secret.interconnect_uuid_enc) if secret.interconnect_uuid_enc else ""
        from app.compiler import EntryPeerSpec

        peers.append(
            EntryPeerSpec(entry_server_id=server.id, uuid=interconnect_uuid, email=f"entry-{server.id}")
        )
        entry_input = EntryCompileInput(
            node=EntryNodeSpec(
                server_id=server.id,
                public_host=server.host,
                base_dir=server.base_dir,
                inbound=RealityInboundSpec(
                    listen="0.0.0.0",
                    port=443,
                    dest=secret.real_dest or "www.microsoft.com",
                    server_names=(secret.real_sni or "www.microsoft.com",),
                    private_key=box.decrypt(secret.reality_private_key_enc),
                    short_ids=tuple(secret.short_ids_json or ("",)),
                ),
            ),
            clients=tuple(
                ClientSpec(uuid=client.uuid, email=client.email_tag) for client in clients
            ),
            routing=spec,
        )
        try:
            configs[server.id] = compile_entry_config(entry_input)
        except CompileError as exc:
            revision.status = "failed"
            revision.error = str(exc)
            await db.commit()
            raise ApiError("compile_failed", f"Ошибка генерации конфигурации: {exc}", 400) from exc

    for server in exit_servers:
        secret = secrets.get(server.id)
        if secret is None or not secret.reality_private_key_enc:
            continue
        exit_input = ExitCompileInput(
            node=ExitNodeSpec(
                server_id=server.id,
                inbound=RealityInboundSpec(
                    listen="0.0.0.0",
                    port=INTERCONNECT_PORT,
                    dest=secret.real_dest or "www.apple.com",
                    server_names=(secret.real_sni or "www.apple.com",),
                    private_key=box.decrypt(secret.reality_private_key_enc),
                    short_ids=tuple(secret.short_ids_json or ("",)),
                ),
            ),
            peers=tuple(peers),
        )
        try:
            configs[server.id] = compile_exit_config(exit_input)
        except CompileError as exc:
            revision.status = "failed"
            revision.error = str(exc)
            await db.commit()
            raise ApiError("compile_failed", f"Ошибка генерации exit-конфигурации: {exc}", 400) from exc

    for server in servers:
        config = configs.get(server.id)
        if config is None:
            continue
        sha = sha256_json(config)
        hashes[str(server.id)] = sha
        db.add(
            ServerConfig(
                server_id=server.id,
                revision_id=revision.id,
                config_json=config,
                sha256=sha,
            )
        )
        server.revision_id = revision.id
    revision.profile_json = {
        "enabled": profile.enabled,
        "default_exit_ids": list(profile.default_exit_ids),
        "site_rule_ids": [rule.id for rule in rules],
        "client_ids": [client.id for client in clients],
    }
    revision.config_hashes_json = hashes
    await db.flush()
    job = await create_job(db, "apply", payload={"revision_id": revision.id})
    await db.commit()
    request.app.state.job_runner.start(job.id)
    return {"revision_id": revision.id, "job_id": job.id, "servers": sorted(configs), "hashes": hashes}


@router.get("/revisions")
async def list_revisions(
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> list[dict]:
    rows = (await db.scalars(select(RoutingRevision).order_by(RoutingRevision.id.desc()))).all()
    return [
        {
            "id": row.id,
            "created_at": row.created_at.isoformat() if row.created_at else None,
            "status": row.status,
            "error": row.error,
            "hashes": row.config_hashes_json,
            "profile": row.profile_json,
        }
        for row in rows
    ]


@router.get("/revisions/{revision_id}")
async def get_revision(
    revision_id: int,
    auth: AuthContext = Depends(get_auth),
    db: AsyncSession = Depends(get_session),
) -> dict:
    row = await db.get(RoutingRevision, revision_id)
    if row is None:
        raise ApiError("revision_not_found", "Ревизия не найдена", 404)
    configs = list(
        (await db.scalars(select(ServerConfig).where(ServerConfig.revision_id == revision_id))).all()
    )
    return {
        "id": row.id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "status": row.status,
        "error": row.error,
        "hashes": row.config_hashes_json,
        "profile": row.profile_json,
        "configs": {str(config.server_id): config.config_json for config in configs},
    }
