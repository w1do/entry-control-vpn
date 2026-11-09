"""Exit-node Xray config compiler (VLESS+REALITY interconnect inbound)."""

from __future__ import annotations

from typing import Any

from .inbounds import reality_inbound, validate_reality_inbound
from .types import CompileError, ExitCompileInput

INTERCONNECT_INBOUND_TAG = "interconnect-in"


def _peer_config(uuid: str, email: str, flow: str) -> dict[str, Any]:
    if not isinstance(uuid, str) or not uuid.strip():
        raise CompileError("interconnect peer uuid must be a non-empty string")
    if not isinstance(email, str) or not email.strip():
        raise CompileError("interconnect peer email must be a non-empty string")
    config: dict[str, Any] = {"id": uuid, "email": email}
    if flow:
        config["flow"] = flow
    return config


def compile_exit_config(inp: ExitCompileInput) -> dict[str, Any]:
    """Compile the exit-node config: REALITY interconnect inbound, freedom egress."""
    node = inp.node
    validate_reality_inbound(node.inbound, "exit")

    clients: list[dict[str, Any]] = []
    seen_emails: set[str] = set()
    for peer in inp.peers:
        config = _peer_config(peer.uuid, peer.email, node.inbound.flow)
        if peer.email in seen_emails:
            raise CompileError(f"duplicate interconnect peer email {peer.email!r}")
        seen_emails.add(peer.email)
        clients.append(config)

    return {
        "inbounds": [
            reality_inbound(
                INTERCONNECT_INBOUND_TAG,
                node.inbound,
                clients,
                sniffing=False,
            )
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"domainStrategy": "AsIs", "rules": []},
    }
