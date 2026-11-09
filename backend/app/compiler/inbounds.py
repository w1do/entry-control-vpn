"""VLESS+REALITY inbound builder shared by entry and exit configs."""

from __future__ import annotations

from typing import Any

from .types import CompileError, RealityInboundSpec

SNIFFING = {
    "enabled": True,
    "destOverride": ["http", "tls", "quic"],
    "routeOnly": True,
}


def validate_reality_inbound(inbound: RealityInboundSpec, role: str) -> None:
    """Refuse an inbound that cannot serve REALITY (fail-closed at compile time)."""
    if not isinstance(inbound.listen, str) or not inbound.listen.strip():
        raise CompileError(f"{role} inbound has an empty listen address")
    if (
        not isinstance(inbound.port, int)
        or isinstance(inbound.port, bool)
        or not 1 <= inbound.port <= 65535
    ):
        raise CompileError(f"{role} inbound has invalid port {inbound.port!r}")
    if not isinstance(inbound.dest, str) or not inbound.dest.strip():
        raise CompileError(f"{role} inbound has an empty REALITY dest")
    if not inbound.server_names:
        raise CompileError(f"{role} inbound has no REALITY serverNames")
    if not isinstance(inbound.private_key, str) or not inbound.private_key.strip():
        raise CompileError(f"{role} inbound has an empty REALITY private key")
    if not inbound.short_ids:
        raise CompileError(f"{role} inbound has no REALITY shortIds")


def reality_inbound(
    tag: str,
    inbound: RealityInboundSpec,
    clients: list[dict[str, Any]],
    *,
    sniffing: bool,
) -> dict[str, Any]:
    """Build a VLESS+REALITY TCP inbound with deterministic client order."""
    config: dict[str, Any] = {
        "tag": tag,
        "listen": inbound.listen,
        "port": inbound.port,
        "protocol": "vless",
        "settings": {
            "clients": clients,
            "decryption": "none",
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "show": False,
                # 26.11.9 accepts both keys; target is the canonical name and
                # dest is kept because design.md section 6 shows both.
                "dest": inbound.dest,
                "target": inbound.dest,
                "xver": 0,
                "serverNames": list(inbound.server_names),
                "privateKey": inbound.private_key,
                "shortIds": list(inbound.short_ids),
            },
        },
    }
    if sniffing:
        config["sniffing"] = dict(SNIFFING, destOverride=list(SNIFFING["destOverride"]))
    return config
