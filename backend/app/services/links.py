from __future__ import annotations

import base64
import json
from dataclasses import dataclass, replace
from urllib.parse import quote, urlencode

from app.compiler.transports import MAIN_VARIANT, VARIANTS, TransportVariant
from app.models.routing import Client
from app.models.servers import Server, ServerSecret


@dataclass(frozen=True)
class EntryEndpoint:
    server_id: int
    name: str
    host: str
    port: int
    uuid: str
    server_name: str
    public_key: str
    short_id: str
    flow: str = "xtls-rprx-vision"
    fingerprint: str = "chrome"
    spider_x: str = "/"


def build_entry_endpoint(server: Server, secret: ServerSecret, client: Client, *, port: int = 443) -> EntryEndpoint:
    if not secret.reality_public_key or not secret.real_sni:
        raise ValueError("entry server has no REALITY keys configured")
    short_ids = [str(s) for s in (secret.short_ids_json or [""])]
    short_id = next((s for s in short_ids if s), short_ids[0] if short_ids else "")
    host = server.host
    return EntryEndpoint(
        server_id=server.id,
        name=server.name,
        host=host,
        port=port,
        uuid=client.uuid,
        server_name=secret.real_sni,
        public_key=secret.reality_public_key,
        short_id=short_id,
    )


def _format_host(host: str) -> str:
    if ":" in host and not host.startswith("["):
        return f"[{host}]"
    return host


def endpoint_for_variant(endpoint: EntryEndpoint, variant: TransportVariant) -> EntryEndpoint:
    """Return a copy of the endpoint addressing one transport variant."""
    return replace(endpoint, port=variant.port, flow=variant.flow)


def build_vless_url(
    endpoint: EntryEndpoint,
    *,
    label: str | None = None,
    variant: TransportVariant | None = None,
) -> str:
    network = variant.network if variant is not None else MAIN_VARIANT.network
    query = {
        "encryption": "none",
        "type": network,
        "security": "reality",
        "sni": endpoint.server_name,
        "fp": endpoint.fingerprint,
        "pbk": endpoint.public_key,
        "sid": endpoint.short_id,
        "spx": endpoint.spider_x,
    }
    if endpoint.flow:
        query["flow"] = endpoint.flow
    if variant is not None and variant.network == "xhttp":
        query["path"] = variant.path or "/vpn-xhttp"
        query["mode"] = "auto"
    elif variant is not None and variant.network == "grpc":
        query["serviceName"] = variant.service_name or "vpn-grpc"
        query["mode"] = "gun"
    fragment = quote(label or endpoint.name, safe="")
    return (
        f"vless://{endpoint.uuid}@{_format_host(endpoint.host)}:{endpoint.port}"
        f"?{urlencode(query, quote_via=quote)}#{fragment}"
    )


def build_connection_variants(
    server: Server, secret: ServerSecret, client: Client
) -> list[dict[str, object]]:
    """All supported transport profiles for one client, ready for QR rendering."""
    base = build_entry_endpoint(server, secret, client)
    result: list[dict[str, object]] = []
    for variant in VARIANTS:
        endpoint = endpoint_for_variant(base, variant)
        result.append(
            {
                "id": variant.id,
                "label": variant.label,
                "description": variant.description,
                "type": variant.network,
                "port": variant.port,
                "flow": variant.flow or None,
                "url": build_vless_url(endpoint, label=client.name, variant=variant),
            }
        )
    return result


def _reality_outbound(endpoint: EntryEndpoint) -> dict:
    return {
        "type": "vless",
        "tag": "proxy",
        "server": endpoint.host,
        "server_port": endpoint.port,
        "uuid": endpoint.uuid,
        "flow": endpoint.flow,
        "packet_encoding": "xudp",
        "tls": {
            "enabled": True,
            "server_name": endpoint.server_name,
            "utls": {"enabled": True, "fingerprint": endpoint.fingerprint},
            "reality": {
                "enabled": True,
                "public_key": endpoint.public_key,
                "short_id": endpoint.short_id,
            },
        },
    }


SING_BOX_GEOSITE_RU = (
    "https://raw.githubusercontent.com/SagerNet/sing-geosite/rule-set/geosite-category-ru.srs"
)
SING_BOX_GEOIP_RU = (
    "https://raw.githubusercontent.com/SagerNet/sing-geoip/rule-set/geoip-ru.srs"
)


def build_smart_profile(endpoint: EntryEndpoint, *, profile_title: str | None = None) -> dict:
    """sing-box client profile with RU-bypass (RU traffic leaves the tunnel)."""
    return {
        "log": {"level": "warn", "timestamp": True},
        "dns": {
            "servers": [
                {"tag": "local", "address": "local", "detour": "direct"},
                {
                    "tag": "remote",
                    "address": "https://1.1.1.1/dns-query",
                    "detour": "proxy",
                },
            ],
            "rules": [{"rule_set": ["geosite-category-ru"], "server": "local"}],
            "final": "remote",
            "strategy": "prefer_ipv4",
        },
        "inbounds": [
            {
                "type": "tun",
                "tag": "tun-in",
                "address": ["172.19.0.1/30"],
                "auto_route": True,
                "strict_route": True,
                "stack": "mixed",
                "sniff": True,
                "sniff_override_destination": True,
            }
        ],
        "outbounds": [
            _reality_outbound(endpoint),
            {"type": "direct", "tag": "direct"},
            {"type": "block", "tag": "block"},
        ],
        "route": {
            "rules": [
                {"action": "sniff"},
                {"protocol": "dns", "action": "hijack-dns"},
                {"rule_set": ["geosite-category-ru", "geoip-ru"], "outbound": "direct"},
            ],
            "rule_set": [
                {
                    "tag": "geosite-category-ru",
                    "type": "remote",
                    "format": "binary",
                    "url": SING_BOX_GEOSITE_RU,
                    "download_detour": "direct",
                    "update_interval": "7d",
                },
                {
                    "tag": "geoip-ru",
                    "type": "remote",
                    "format": "binary",
                    "url": SING_BOX_GEOIP_RU,
                    "download_detour": "direct",
                    "update_interval": "7d",
                },
            ],
            "final": "proxy",
            "auto_detect_interface": True,
        },
        "experimental": {
            "cache_file": {"enabled": True, "store_fakeip": True},
        },
        **({"remarks": profile_title} if profile_title else {}),
    }


def build_clash_profile(endpoint: EntryEndpoint, *, profile_title: str = "VPN smart") -> str:
    """Clash Meta (mihomo) YAML profile with RU-bypass."""
    name = "VPN"
    lines = [
        f"# {profile_title}",
        "mixed-port: 7890",
        "allow-lan: false",
        "mode: rule",
        "log-level: warning",
        "ipv6: true",
        "proxies:",
        f"  - name: {name}",
        "    type: vless",
        f"    server: {endpoint.host}",
        f"    port: {endpoint.port}",
        f"    uuid: {endpoint.uuid}",
        "    network: tcp",
        "    udp: true",
        "    tls: true",
        f"    servername: {endpoint.server_name}",
        "    flow: xtls-rprx-vision",
        "    client-fingerprint: chrome",
        "    reality-opts:",
        f"      public-key: {endpoint.public_key}",
        f"      short-id: {endpoint.short_id}",
        "proxy-groups:",
        "  - name: PROXY",
        "    type: select",
        "    proxies:",
        f"      - {name}",
        "rules:",
        "  - GEOSITE,category-ru,DIRECT",
        "  - GEOIP,RU,DIRECT,no-resolve",
        "  - MATCH,PROXY",
        "dns:",
        "  enable: true",
        "  ipv6: false",
        "  nameserver:",
        "    - https://1.1.1.1/dns-query",
        "  fallback:",
        "    - tls://8.8.8.8:853",
    ]
    return "\n".join(lines) + "\n"


def build_v2ray_subscription(endpoint: EntryEndpoint) -> str:
    url = build_vless_url(endpoint)
    return base64.b64encode(url.encode()).decode()


def choose_subscription_format(user_agent: str | None) -> str:
    ua = (user_agent or "").lower()
    if "sing-box" in ua:
        return "sing-box"
    if "clash" in ua or "mihomo" in ua:
        return "clash"
    return "v2ray"


def subscription_headers(*, title: str, upload: int = 0, download: int = 0, total: int = 0) -> dict[str, str]:
    headers = {
        "Profile-Title": title,
        "Subscription-Userinfo": f"upload={upload}; download={download}; total={total}; expire=0",
        "Cache-Control": "no-store",
        "Content-Disposition": f"inline; filename=\"{title}.json\"",
    }
    return headers


def smart_profile_bytes(endpoint: EntryEndpoint, *, title: str) -> bytes:
    payload = build_smart_profile(endpoint, profile_title=title)
    return json.dumps(payload, ensure_ascii=False, sort_keys=True, indent=2).encode()
