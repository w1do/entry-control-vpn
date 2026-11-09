"""Golden tests for the exit-node config."""

from __future__ import annotations

import pytest

from app.compiler import (
    CompileError,
    EntryPeerSpec,
    canonical_json,
    compile_exit_config,
    sha256_json,
)
from helpers import (
    PRIVATE_KEY,
    SHORT_ID,
    UUID_ENTRY,
    exit_input,
    exit_node,
    reality_inbound,
)

UUID_PEER_2 = "77777777-8888-9999-aaaa-bbbbbbbbbbbb"


def _expected_exit_config() -> dict[str, object]:
    return {
        "inbounds": [
            {
                "tag": "interconnect-in",
                "listen": "0.0.0.0",
                "port": 18443,
                "protocol": "vless",
                "settings": {
                    "clients": [
                        {
                            "id": UUID_ENTRY,
                            "email": "entry-1",
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
                        "dest": "www.apple.com:443",
                        "target": "www.apple.com:443",
                        "xver": 0,
                        "serverNames": ["www.apple.com"],
                        "privateKey": PRIVATE_KEY,
                        "shortIds": [SHORT_ID],
                    },
                },
            }
        ],
        "outbounds": [{"tag": "direct", "protocol": "freedom"}],
        "routing": {"domainStrategy": "AsIs", "rules": []},
    }


def test_exit_config_golden() -> None:
    config = compile_exit_config(exit_input())
    assert config == _expected_exit_config()
    assert canonical_json(config) == canonical_json(_expected_exit_config())


def test_exit_config_is_minimal_by_contract() -> None:
    config = compile_exit_config(exit_input())
    assert set(config) == {"inbounds", "outbounds", "routing"}
    assert "sniffing" not in config["inbounds"][0]


def test_exit_config_deterministic() -> None:
    first = compile_exit_config(exit_input())
    second = compile_exit_config(exit_input())
    assert canonical_json(first) == canonical_json(second)
    assert sha256_json(first) == sha256_json(second)


def test_exit_peers_keep_order_and_flow() -> None:
    peers = (
        EntryPeerSpec(entry_server_id=2, uuid=UUID_PEER_2, email="entry-2"),
        EntryPeerSpec(entry_server_id=1, uuid=UUID_ENTRY, email="entry-1"),
    )
    config = compile_exit_config(exit_input(peers))
    clients = config["inbounds"][0]["settings"]["clients"]
    assert [client["email"] for client in clients] == ["entry-2", "entry-1"]
    assert all(client["flow"] == "xtls-rprx-vision" for client in clients)


def test_exit_accepts_no_peers() -> None:
    config = compile_exit_config(exit_input(peers=()))
    assert config["inbounds"][0]["settings"]["clients"] == []


def test_exit_port_is_honoured_from_node_spec() -> None:
    config = compile_exit_config(exit_input())
    assert config["inbounds"][0]["port"] == 18443
    node = exit_node(port=18444)
    from app.compiler import ExitCompileInput

    config = compile_exit_config(ExitCompileInput(node=node, peers=()))
    assert config["inbounds"][0]["port"] == 18444


def test_duplicate_peer_email_is_rejected() -> None:
    peers = (
        EntryPeerSpec(entry_server_id=1, uuid=UUID_ENTRY, email="entry-1"),
        EntryPeerSpec(entry_server_id=2, uuid=UUID_PEER_2, email="entry-1"),
    )
    with pytest.raises(CompileError):
        compile_exit_config(exit_input(peers))


def test_inbound_without_server_names_is_rejected() -> None:
    from app.compiler import ExitCompileInput, ExitNodeSpec

    node = ExitNodeSpec(server_id=3, inbound=reality_inbound(server_names=()))
    with pytest.raises(CompileError):
        compile_exit_config(ExitCompileInput(node=node, peers=()))


def test_inbound_without_short_ids_is_rejected() -> None:
    from app.compiler import ExitCompileInput, ExitNodeSpec

    node = ExitNodeSpec(server_id=3, inbound=reality_inbound(short_ids=()))
    with pytest.raises(CompileError):
        compile_exit_config(ExitCompileInput(node=node, peers=()))
