"""Revision building shared by the API and by automatic first-run bootstrap.

The API endpoint /api/v1/routing/apply and the provisioning job both need to
turn the stored routing profile into deterministic per-node Xray configs.  The
logic lives here so the two callers cannot drift apart.

A node with the role "both" terminates the client inbound *and* the exit
interconnect in the same process, so its two configs are merged into one:
entry inbound + interconnect inbound, entry outbounds/rules, plus a rule that
sends interconnect peers straight to the "direct" outbound.
"""

from __future__ import annotations

import json
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.compiler import (
    ClientSpec,
    CompileError,
    EntryCompileInput,
    EntryNodeSpec,
    EntryPeerSpec,
    ExitCompileInput,
    ExitNodeSpec,
    ExitOutboundSpec,
    ExitSpec,
    RealityInboundSpec,
    RoutingSpec,
    SiteRuleSpec,
    compile_entry_config,
    compile_exit_config,
    sha256_json,
    validate_routing,
)
from app.core.timeutils import utcnow
from app.models.routing import Client, Exit, RoutingProfile, RoutingRevision, SiteRule
from app.models.servers import Server, ServerConfig, ServerSecret

INTERCONNECT_PORT = 18443
INTERCONNECT_TAG = "interconnect-in"


class RoutingNotReady(Exception):
    def __init__(self, issues: list[dict[str, Any]]) -> None:
        super().__init__("routing profile is not ready")
        self.issues = issues


def reality_dest(value: str | None) -> str:
    host = (value or "www.microsoft.com").strip()
    return host if ":" in host else f"{host}:443"


def all_secrets(rows: list[ServerSecret]) -> dict[int, ServerSecret]:
    return {row.server_id: row for row in rows}


def exit_specs(db_exits: list[Exit], secrets: dict[int, ServerSecret], box: Any) -> tuple[ExitSpec, ...]:
    specs: list[ExitSpec] = []
    for exit_row in db_exits:
        if exit_row.kind == "node":
            secret = secrets.get(exit_row.server_id or -1)
            if secret is None or not secret.reality_public_key:
                continue
            interconnect_uuid = box.decrypt(secret.interconnect_uuid_enc) if secret.interconnect_uuid_enc else ""
            outbound = ExitOutboundSpec(
                kind="vless_reality",
                host=exit_row.endpoint_host,
                port=exit_row.endpoint_port,
                uuid=interconnect_uuid,
                public_key=secret.reality_public_key,
                short_id=(secret.short_ids_json or [""])[0],
                server_name=secret.real_sni,
                fingerprint="chrome",
                spider_x="/",
            )
        else:
            credentials: dict = {}
            if exit_row.secret_enc:
                try:
                    credentials = json.loads(box.decrypt(exit_row.secret_enc))
                except ValueError:
                    credentials = {}
            outbound = ExitOutboundSpec(
                kind="http" if exit_row.protocol == "http" else "socks5",
                host=exit_row.endpoint_host,
                port=exit_row.endpoint_port,
                username=credentials.get("username"),
                password=credentials.get("password"),
                expected_exit_ip=exit_row.expected_exit_ip,
            )
        specs.append(
            ExitSpec(
                id=exit_row.id,
                label=exit_row.label,
                region=exit_row.region or "",
                outbound=outbound,
                enabled=exit_row.enabled,
                health=exit_row.health,
            )
        )
    return tuple(specs)


def site_specs(rules: list[SiteRule]) -> tuple[SiteRuleSpec, ...]:
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


def routing_spec(
    profile: RoutingProfile,
    exits: list[Exit],
    rules: list[SiteRule],
    secrets: dict[int, ServerSecret],
    box: Any,
) -> RoutingSpec:
    return RoutingSpec(
        exits=exit_specs(exits, secrets, box),
        default_exit_ids=tuple(profile.default_exit_ids),
        site_rules=site_specs(rules),
        enabled=profile.enabled,
    )


def profile_issues(spec: RoutingSpec) -> list[dict[str, Any]]:
    issues = validate_routing(spec)
    result = [
        {"code": issue.code, "message": issue.message, "subject": issue.subject} for issue in issues
    ]
    if spec.enabled and not spec.default_exit_ids:
        result.append(
            {"code": "default_exit_missing", "message": "Не выбран выход по умолчанию", "subject": None}
        )
    if spec.enabled:
        usable = [exit_spec for exit_spec in spec.exits if exit_spec.enabled and exit_spec.health != "down"]
        if not usable:
            result.append(
                {"code": "no_healthy_exits", "message": "Нет ни одного доступного выхода", "subject": None}
            )
    return result


async def load_profile(db: AsyncSession) -> RoutingProfile:
    profile = await db.get(RoutingProfile, 1)
    if profile is None:
        profile = RoutingProfile(id=1, default_exit_ids_json=[], enabled=False)
        db.add(profile)
        await db.flush()
    return profile


def merge_both_configs(entry_config: dict[str, Any], exit_config: dict[str, Any]) -> dict[str, Any]:
    """Merge an entry-node config with an exit interconnect inbound (role "both")."""
    merged = {
        **entry_config,
        "inbounds": [*entry_config.get("inbounds", []), *exit_config.get("inbounds", [])],
    }
    routing = dict(entry_config.get("routing") or {})
    interconnect_rule = {
        "type": "field",
        "inboundTag": [INTERCONNECT_TAG],
        "outboundTag": "direct",
    }
    routing["rules"] = [interconnect_rule, *routing.get("rules", [])]
    merged["routing"] = routing
    if "api" not in merged and "api" in exit_config:
        merged["api"] = exit_config["api"]
    return merged


async def create_revision(db: AsyncSession, secret_box: Any) -> RoutingRevision:
    """Compile all node configs into a new revision (no commit)."""
    profile = await load_profile(db)
    exits = list((await db.scalars(select(Exit).order_by(Exit.id))).all())
    rules = list((await db.scalars(select(SiteRule).order_by(SiteRule.position, SiteRule.id))).all())
    secrets = all_secrets(list((await db.scalars(select(ServerSecret))).all()))
    spec = routing_spec(profile, exits, rules, secrets, secret_box)
    issues = profile_issues(spec)
    if issues:
        raise RoutingNotReady(issues)

    servers = list((await db.scalars(select(Server).order_by(Server.id))).all())
    entry_servers = [server for server in servers if server.is_entry]
    exit_servers = [server for server in servers if server.is_exit]
    clients = list((await db.scalars(select(Client).where(Client.enabled.is_(True)).order_by(Client.id))).all())
    if not entry_servers:
        raise RoutingNotReady(
            [{"code": "no_entry_servers", "message": "Нет ни одного entry-узла", "subject": None}]
        )

    revision = RoutingRevision(status="pending", profile_json={}, config_hashes_json={})
    db.add(revision)
    await db.flush()

    entry_configs: dict[int, dict] = {}
    exit_configs: dict[int, dict] = {}
    hashes: dict[str, str] = {}
    peers: list[EntryPeerSpec] = []
    for server in entry_servers:
        secret = secrets.get(server.id)
        if secret is None or not secret.reality_private_key_enc:
            continue
        interconnect_uuid = secret_box.decrypt(secret.interconnect_uuid_enc) if secret.interconnect_uuid_enc else ""
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
                    dest=reality_dest(secret.real_dest),
                    server_names=(secret.real_sni or "www.microsoft.com",),
                    private_key=secret_box.decrypt(secret.reality_private_key_enc),
                    short_ids=tuple(secret.short_ids_json or ("",)),
                ),
            ),
            clients=tuple(ClientSpec(uuid=client.uuid, email=client.email_tag) for client in clients),
            routing=spec,
        )
        try:
            entry_configs[server.id] = compile_entry_config(entry_input)
        except CompileError as exc:
            revision.status = "failed"
            revision.error = str(exc)
            await db.flush()
            raise

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
                    dest=reality_dest(secret.real_dest),
                    server_names=(secret.real_sni or "www.apple.com",),
                    private_key=secret_box.decrypt(secret.reality_private_key_enc),
                    short_ids=tuple(secret.short_ids_json or ("",)),
                ),
            ),
            peers=tuple(peers),
        )
        try:
            exit_configs[server.id] = compile_exit_config(exit_input)
        except CompileError as exc:
            revision.status = "failed"
            revision.error = str(exc)
            await db.flush()
            raise

    configs: dict[int, dict] = {}
    for server in servers:
        entry_config = entry_configs.get(server.id)
        exit_config = exit_configs.get(server.id)
        if entry_config is not None and exit_config is not None:
            configs[server.id] = merge_both_configs(entry_config, exit_config)
        elif entry_config is not None:
            configs[server.id] = entry_config
        elif exit_config is not None:
            configs[server.id] = exit_config

    for server_id, config in configs.items():
        sha = sha256_json(config)
        hashes[str(server_id)] = sha
        db.add(
            ServerConfig(
                server_id=server_id,
                revision_id=revision.id,
                config_json=config,
                sha256=sha,
            )
        )
        server = next(item for item in servers if item.id == server_id)
        server.revision_id = revision.id
    revision.profile_json = {
        "enabled": profile.enabled,
        "default_exit_ids": list(profile.default_exit_ids),
        "site_rule_ids": [rule.id for rule in rules],
        "client_ids": [client.id for client in clients],
    }
    revision.config_hashes_json = hashes
    await db.flush()
    return revision


async def auto_apply(db: AsyncSession, secret_box: Any) -> RoutingRevision | None:
    """Best-effort apply used after provisioning/client changes."""
    try:
        revision = await create_revision(db, secret_box)
    except RoutingNotReady:
        return None
    revision.status = "applied"
    revision.applied_at = utcnow()
    await db.commit()
    return revision


async def ensure_exit_for_server(db: AsyncSession, server: Server) -> Exit | None:
    """Register the node's own exit (kind="node") for roles exit/both."""
    if not server.is_exit:
        return None
    existing = await db.scalar(select(Exit).where(Exit.kind == "node", Exit.server_id == server.id))
    if existing is not None:
        return existing
    exit_row = Exit(
        kind="node",
        server_id=server.id,
        label=f"node-{server.id}",
        region=server.region or "",
        endpoint_host=server.host,
        endpoint_port=INTERCONNECT_PORT,
        protocol="vless_reality",
        expected_exit_ip=None,
        enabled=True,
        health="unknown",
    )
    db.add(exit_row)
    await db.flush()
    return exit_row


async def bootstrap_after_provision(db: AsyncSession, server: Server, secret_box: Any) -> RoutingRevision | None:
    """First-run convenience: register the node's exit and pick it as default.

    This runs only while routing was never configured (disabled profile with no
    default exits), so it cannot overwrite an operator's explicit choices.
    """
    exit_row = await ensure_exit_for_server(db, server)
    profile = await load_profile(db)
    if exit_row is not None and not profile.enabled and not profile.default_exit_ids:
        profile.default_exit_ids_json = [exit_row.id]
        profile.enabled = True
        await db.flush()
    await db.commit()
    return await auto_apply(db, secret_box)
