"""Entry-node Xray config compiler (VLESS+REALITY client inbound + routing)."""

from __future__ import annotations

from typing import Any

from .inbounds import reality_inbound, transport_inbound, validate_reality_inbound
from .routing import build_plan, join_base_dir
from .transports import EXTRA_VARIANTS
from .types import ClientSpec, CompileError, EntryCompileInput

API_TAG = "api"
API_LISTEN = "127.0.0.1:10085"
API_SERVICES = ("HandlerService", "LoggerService", "StatsService")
METRICS_LISTEN = "127.0.0.1:11111"
PROBE_URL = "https://www.gstatic.com/generate_204"
PROBE_INTERVAL = "30s"


def _client_config(client: ClientSpec) -> dict[str, Any]:
    if not isinstance(client.uuid, str) or not client.uuid.strip():
        raise CompileError("client uuid must be a non-empty string")
    if not isinstance(client.email, str) or not client.email.strip():
        raise CompileError("client email must be a non-empty string")
    config: dict[str, Any] = {"id": client.uuid, "email": client.email}
    if client.flow:
        config["flow"] = client.flow
    return config


def _dns_servers(servers: tuple[str, ...]) -> list[dict[str, str]]:
    if not servers:
        raise CompileError("routing.dns_servers must contain at least one DoH server")
    result: list[dict[str, str]] = []
    for server in servers:
        if not isinstance(server, str) or not server.startswith("https://"):
            raise CompileError(
                f"DNS server {server!r} is not a DoH URL (https://... required by the profile)"
            )
        result.append({"address": server})
    return result


def compile_entry_config(inp: EntryCompileInput) -> dict[str, Any]:
    """Compile the full entry-node config; deterministic for identical input."""
    node = inp.node
    routing = inp.routing
    validate_reality_inbound(node.inbound, "entry")

    clients: list[dict[str, Any]] = []
    seen_emails: set[str] = set()
    for client in inp.clients:
        config = _client_config(client)
        if client.email in seen_emails:
            raise CompileError(f"duplicate client email {client.email!r}")
        seen_emails.add(client.email)
        clients.append(config)

    plan = build_plan(routing)
    base_dir = node.base_dir
    clients_no_flow = [{"id": client["id"], "email": client["email"]} for client in clients]
    inbounds: list[dict[str, Any]] = [
        reality_inbound("vless-in", node.inbound, clients, sniffing=True)
    ]
    for variant in EXTRA_VARIANTS:
        inbounds.append(
            transport_inbound(
                f"vless-{variant.id}", node.inbound, clients_no_flow, variant
            )
        )
    config: dict[str, Any] = {
        "log": {
            "loglevel": "warning",
            "access": join_base_dir(base_dir, "log", "access.log"),
            "error": join_base_dir(base_dir, "log", "xray-error.log"),
        },
        "dns": {"servers": _dns_servers(routing.dns_servers)},
        "inbounds": inbounds,
        "outbounds": [
            {"tag": "block", "protocol": "blackhole"},
            {"tag": "direct", "protocol": "freedom"},
            *plan.outbounds,
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": list(plan.rules),
            "balancers": list(plan.balancers),
        },
        "observatory": {
            "subjectSelector": ["exit-", "rule-"],
            "probeUrl": PROBE_URL,
            "probeInterval": PROBE_INTERVAL,
            "enableConcurrency": True,
        },
        "policy": {
            "levels": {
                "0": {
                    "statsUserUplink": True,
                    "statsUserDownlink": True,
                    "statsUserOnline": True,
                }
            },
            "system": {
                "statsInboundUplink": True,
                "statsInboundDownlink": True,
                "statsOutboundUplink": True,
                "statsOutboundDownlink": True,
            },
        },
        "api": {
            "tag": API_TAG,
            "listen": API_LISTEN,
            "services": list(API_SERVICES),
        },
        "stats": {},
        "metrics": {"listen": METRICS_LISTEN},
    }
    return config
