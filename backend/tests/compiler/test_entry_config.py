"""Golden tests for the entry-node config."""

from __future__ import annotations

import pytest
from helpers import (
    DEFAULT_DNS,
    PRIVATE_KEY,
    PUBLIC_KEY,
    SHORT_ID,
    UUID_ENTRY,
    UUID_EXIT,
    entry_input,
    http_exit,
    reality_inbound,
    routing_spec,
    site_rule,
    vless_exit,
)

from app.compiler import (
    ClientSpec,
    CompileError,
    EntryNodeSpec,
    canonical_json,
    compile_entry_config,
    sha256_json,
)


def _expected_vless_outbound(tag: str, address: str) -> dict[str, object]:
    return {
        "tag": tag,
        "protocol": "vless",
        "settings": {
            "vnext": [
                {
                    "address": address,
                    "port": 18443,
                    "users": [
                        {
                            "id": UUID_EXIT,
                            "encryption": "none",
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                }
            ]
        },
        "streamSettings": {
            "network": "tcp",
            "security": "reality",
            "realitySettings": {
                "serverName": "www.microsoft.com",
                "fingerprint": "chrome",
                "publicKey": PUBLIC_KEY,
                "shortId": SHORT_ID,
                "spiderX": "/",
            },
        },
    }


def _expected_config(base_dir: str = "/srv/vpn/node") -> dict[str, object]:
    return {
        "log": {
            "loglevel": "warning",
            "access": base_dir + "/log/access.log",
            "error": base_dir + "/log/xray-error.log",
        },
        "dns": {"servers": [{"address": server} for server in DEFAULT_DNS]},
        "inbounds": [
            {
                "tag": "vless-in",
                "listen": "0.0.0.0",
                "port": 443,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": UUID_ENTRY,
                            "email": "client-1@vpn",
                            "flow": "xtls-rprx-vision",
                        }
                    ],
                    "decryption": "none",
                },
                "streamSettings": {
                    "network": "tcp",
                    "security": "reality",
                    "realitySettings": {
                        "show": False,
                        "dest": "www.microsoft.com:443",
                        "target": "www.microsoft.com:443",
                        "xver": 0,
                        "serverNames": ["www.microsoft.com"],
                        "privateKey": PRIVATE_KEY,
                        "shortIds": [SHORT_ID],
                    },
                },
                "sniffing": {
                    "enabled": True,
                    "destOverride": ["http", "tls", "quic"],
                    "routeOnly": True,
                },
            }
        ],
        "outbounds": [
            {"tag": "block", "protocol": "blackhole"},
            {"tag": "direct", "protocol": "freedom"},
            _expected_vless_outbound("exit-sg-1", "203.0.113.10"),
            _expected_vless_outbound("exit-us-1", "203.0.113.11"),
            _expected_vless_outbound("rule-5-sg-1", "203.0.113.10"),
            {
                "tag": "rule-5-res-us",
                "protocol": "http",
                "settings": {
                    "servers": [
                        {
                            "address": "proxy.example.net",
                            "port": 8080,
                            "users": [{"user": "user", "pass": "pass"}],
                        }
                    ]
                },
            },
        ],
        "routing": {
            "domainStrategy": "IPIfNonMatch",
            "rules": [
                {
                    "type": "field",
                    "ip": ["geoip:private", "169.254.169.254/32"],
                    "outboundTag": "block",
                },
                {
                    "type": "field",
                    "domain": ["domain:anthropic.com", "domain:api.anthropic.com"],
                    "balancerTag": "bal-rule-5",
                },
                {
                    "type": "field",
                    "domain": ["geosite:category-ru"],
                    "outboundTag": "direct",
                },
                {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"},
                {
                    "type": "field",
                    "network": "tcp,udp",
                    "balancerTag": "bal-default",
                },
                {"type": "field", "network": "tcp,udp", "outboundTag": "block"},
            ],
            "balancers": [
                {
                    "tag": "bal-rule-5",
                    "selector": ["rule-5-"],
                    "strategy": {"type": "leastPing"},
                    "fallbackTag": "block",
                },
                {
                    "tag": "bal-default",
                    "selector": ["exit-"],
                    "strategy": {"type": "leastPing"},
                    "fallbackTag": "block",
                },
            ],
        },
        "observatory": {
            "subjectSelector": ["exit-", "rule-"],
            "probeUrl": "https://www.gstatic.com/generate_204",
            "probeInterval": "30s",
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
            "tag": "api",
            "listen": "127.0.0.1:10085",
            "services": ["HandlerService", "LoggerService", "StatsService"],
        },
        "stats": {},
        "metrics": {"listen": "127.0.0.1:11111"},
    }


def _golden_routing():  # noqa: ANN202 - test helper
    return routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11"),
            http_exit(3, "res-us"),
        ),
        default_exit_ids=(1, 2),
        site_rules=(
            site_rule(
                5,
                values=("anthropic.com", "api.anthropic.com"),
                allowed_exit_ids=(1, 3),
            ),
        ),
    )


def test_entry_config_is_byte_identical_golden(entry_base_dir: str) -> None:
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir))
    expected = _expected_config(entry_base_dir)
    assert config == expected
    assert canonical_json(config) == canonical_json(expected)


def test_entry_config_deterministic(entry_base_dir: str) -> None:
    routing = _golden_routing()
    first = compile_entry_config(entry_input(routing, base_dir=entry_base_dir))
    second = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir))
    assert canonical_json(first) == canonical_json(second)
    assert sha256_json(first) == sha256_json(second)


def test_first_outbounds_are_block_then_direct(entry_base_dir: str) -> None:
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir))
    outbounds = config["outbounds"]
    assert outbounds[0] == {"tag": "block", "protocol": "blackhole"}
    assert outbounds[1] == {"tag": "direct", "protocol": "freedom"}


def test_outbound_tags_are_unique(entry_base_dir: str) -> None:
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir))
    tags = [outbound["tag"] for outbound in config["outbounds"]]
    assert len(tags) == len(set(tags))


def test_inbound_keeps_client_order_and_flow(entry_base_dir: str) -> None:
    clients = (
        ClientSpec(uuid="aaaaaaaa-0000-0000-0000-000000000001", email="b@vpn"),
        ClientSpec(uuid="aaaaaaaa-0000-0000-0000-000000000002", email="a@vpn"),
        ClientSpec(uuid="aaaaaaaa-0000-0000-0000-000000000003", email="c@vpn", flow=""),
    )
    config = compile_entry_config(
        entry_input(_golden_routing(), base_dir=entry_base_dir, clients=clients)
    )
    inbound_clients = config["inbounds"][0]["settings"]["clients"]
    assert [client["email"] for client in inbound_clients] == ["b@vpn", "a@vpn", "c@vpn"]
    assert inbound_clients[0]["flow"] == "xtls-rprx-vision"
    assert "flow" not in inbound_clients[2]


def test_empty_client_list_is_allowed(entry_base_dir: str) -> None:
    config = compile_entry_config(
        entry_input(_golden_routing(), base_dir=entry_base_dir, clients=())
    )
    assert config["inbounds"][0]["settings"]["clients"] == []


def test_duplicate_client_email_is_rejected(entry_base_dir: str) -> None:
    clients = (
        ClientSpec(uuid=UUID_ENTRY, email="same@vpn"),
        ClientSpec(uuid="bbbbbbbb-0000-0000-0000-000000000002", email="same@vpn"),
    )
    with pytest.raises(CompileError):
        compile_entry_config(
            entry_input(_golden_routing(), base_dir=entry_base_dir, clients=clients)
        )


def test_empty_client_uuid_is_rejected(entry_base_dir: str) -> None:
    with pytest.raises(CompileError):
        compile_entry_config(
            entry_input(
                _golden_routing(),
                base_dir=entry_base_dir,
                clients=(ClientSpec(uuid="", email="x@vpn"),),
            )
        )


def test_placeholder_base_dir_is_joined_literally(entry_base_dir: str) -> None:
    # Contract default is "~/config/vpn"; Xray does not expand "~", the panel must
    # pass an absolute path (see app/compiler/README.md).
    node = EntryNodeSpec(
        server_id=1,
        public_host="198.51.100.1",
        inbound=reality_inbound(),
        base_dir="~/config/vpn",
    )
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir, node=node))
    assert config["log"]["access"] == "~/config/vpn/log/access.log"
    assert config["log"]["error"] == "~/config/vpn/log/xray-error.log"


def test_trailing_slash_in_base_dir_is_normalised(entry_base_dir: str) -> None:
    node = EntryNodeSpec(
        server_id=1,
        public_host="198.51.100.1",
        inbound=reality_inbound(),
        base_dir="/srv/vpn/node/",
    )
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=entry_base_dir, node=node))
    assert config["log"]["access"] == "/srv/vpn/node/log/access.log"


def test_non_doh_dns_is_rejected(entry_base_dir: str) -> None:
    routing = routing_spec(dns_servers=("8.8.8.8",))
    with pytest.raises(CompileError):
        compile_entry_config(entry_input(routing, base_dir=entry_base_dir))


def test_dns_servers_are_compiled_in_order(entry_base_dir: str) -> None:
    routing = routing_spec(dns_servers=("https://9.9.9.9/dns-query", "https://1.1.1.1/dns-query"))
    config = compile_entry_config(entry_input(routing, base_dir=entry_base_dir))
    assert config["dns"]["servers"] == [
        {"address": "https://9.9.9.9/dns-query"},
        {"address": "https://1.1.1.1/dns-query"},
    ]


def test_routing_disabled_block_all(entry_base_dir: str) -> None:
    routing = routing_spec(
        enabled=False,
        site_rules=(site_rule(5, values=("anthropic.com",)),),
    )
    config = compile_entry_config(entry_input(routing, base_dir=entry_base_dir))
    assert config["routing"]["rules"] == [
        {
            "type": "field",
            "ip": ["geoip:private", "169.254.169.254/32"],
            "outboundTag": "block",
        },
        {"type": "field", "network": "tcp,udp", "outboundTag": "block"},
    ]
    assert config["routing"]["balancers"] == []
    outbound_tags = [outbound["tag"] for outbound in config["outbounds"]]
    assert outbound_tags == ["block", "direct"]
    # inbound and accounting sections still exist: clients can connect, traffic is blocked
    assert config["inbounds"][0]["settings"]["clients"][0]["email"] == "client-1@vpn"
    assert config["api"]["listen"] == "127.0.0.1:10085"


def test_client_flow_omitted_when_empty() -> None:
    config_clients = (
        ClientSpec(uuid=UUID_ENTRY, email="client-1@vpn", flow=""),
    )
    from app.compiler import EntryCompileInput  # local import keeps golden section small

    config = compile_entry_config(
        EntryCompileInput(
            node=EntryNodeSpec(
                server_id=1,
                public_host="198.51.100.1",
                inbound=reality_inbound(),
                base_dir="/srv/vpn/node",
            ),
            clients=config_clients,
            routing=_golden_routing(),
        )
    )
    assert "flow" not in config["inbounds"][0]["settings"]["clients"][0]
