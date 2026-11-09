"""Shared builders and Xray integration helpers for compiler tests."""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from app.compiler import (
    ClientSpec,
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
    canonical_json,
)

REPO_ROOT = Path(__file__).resolve().parents[3]

PRIVATE_KEY = "wIwYIPwMkN93DaXZc3FMfm1GJFoXaVQM0JeJrnbK_UU"
PUBLIC_KEY = "piBOCJze61fz6ef2sihb9oBBc_naxJTnJY0Bipb0WkA"
SHORT_ID = "0123456789abcdef"
UUID_ENTRY = "11111111-2222-3333-4444-555555555555"
UUID_EXIT = "66666666-7777-8888-9999-aaaaaaaaaaaa"
DEFAULT_DNS = ("https://1.1.1.1/dns-query", "https://8.8.8.8/dns-query")


def reality_inbound(**overrides: object) -> RealityInboundSpec:
    values: dict[str, object] = {
        "listen": "0.0.0.0",
        "port": 443,
        "dest": "www.microsoft.com:443",
        "server_names": ("www.microsoft.com",),
        "private_key": PRIVATE_KEY,
        "short_ids": (SHORT_ID,),
    }
    values.update(overrides)
    return RealityInboundSpec(**values)  # type: ignore[arg-type]


def vless_exit(
    exit_id: int,
    label: str,
    host: str,
    *,
    region: str = "sg",
    port: int = 18443,
    enabled: bool = True,
    health: str = "up",
    **overrides: object,
) -> ExitSpec:
    values: dict[str, object] = {
        "kind": "vless_reality",
        "host": host,
        "port": port,
        "uuid": UUID_EXIT,
        "public_key": PUBLIC_KEY,
        "short_id": SHORT_ID,
        "server_name": "www.microsoft.com",
    }
    values.update(overrides)
    return ExitSpec(
        id=exit_id,
        label=label,
        region=region,
        outbound=ExitOutboundSpec(**values),  # type: ignore[arg-type]
        enabled=enabled,
        health=health,
    )


def http_exit(
    exit_id: int,
    label: str,
    host: str = "proxy.example.net",
    *,
    port: int = 8080,
    username: str | None = "user",
    password: str | None = "pass",
    server_name: str | None = None,
    enabled: bool = True,
    health: str = "up",
) -> ExitSpec:
    return ExitSpec(
        id=exit_id,
        label=label,
        region="us",
        outbound=ExitOutboundSpec(
            kind="http",
            host=host,
            port=port,
            username=username,
            password=password,
            server_name=server_name,
        ),
        enabled=enabled,
        health=health,
    )


def socks_exit(
    exit_id: int,
    label: str,
    host: str = "proxy.example.net",
    *,
    port: int = 1080,
    enabled: bool = True,
) -> ExitSpec:
    return ExitSpec(
        id=exit_id,
        label=label,
        region="us",
        outbound=ExitOutboundSpec(kind="socks5", host=host, port=port),
        enabled=enabled,
    )


def site_rule(
    rule_id: int = 5,
    *,
    name: str = "Anthropic",
    match_type: str = "domain",
    values: tuple[str, ...] = ("anthropic.com", "api.anthropic.com"),
    allowed_exit_ids: tuple[int, ...] = (1, 2),
    enabled: bool = True,
    position: int = 0,
) -> SiteRuleSpec:
    return SiteRuleSpec(
        id=rule_id,
        name=name,
        match_type=match_type,
        values=values,
        allowed_exit_ids=allowed_exit_ids,
        enabled=enabled,
        position=position,
    )


def routing_spec(
    exits: tuple[ExitSpec, ...] | None = None,
    *,
    default_exit_ids: tuple[int, ...] = (1, 2),
    site_rules: tuple[SiteRuleSpec, ...] = (),
    enabled: bool = True,
    dns_servers: tuple[str, ...] = DEFAULT_DNS,
) -> RoutingSpec:
    if exits is None:
        exits = (vless_exit(1, "sg-1", "203.0.113.10"), vless_exit(2, "us-1", "203.0.113.11"))
    return RoutingSpec(
        exits=exits,
        default_exit_ids=default_exit_ids,
        site_rules=site_rules,
        enabled=enabled,
        dns_servers=dns_servers,
    )


def entry_input(
    routing: RoutingSpec,
    *,
    base_dir: str,
    clients: tuple[ClientSpec, ...] | None = None,
    node: EntryNodeSpec | None = None,
) -> EntryCompileInput:
    if clients is None:
        clients = (ClientSpec(uuid=UUID_ENTRY, email="client-1@vpn"),)
    if node is None:
        node = EntryNodeSpec(
            server_id=1,
            public_host="198.51.100.1",
            inbound=reality_inbound(),
            base_dir=base_dir,
        )
    return EntryCompileInput(node=node, clients=clients, routing=routing)


def exit_node(*, port: int = 18443) -> ExitNodeSpec:
    return ExitNodeSpec(
        server_id=3,
        inbound=reality_inbound(
            listen="0.0.0.0",
            port=port,
            dest="www.apple.com:443",
            server_names=("www.apple.com",),
        ),
    )


def exit_input(peers: tuple[EntryPeerSpec, ...] | None = None) -> ExitCompileInput:
    if peers is None:
        peers = (EntryPeerSpec(entry_server_id=1, uuid=UUID_ENTRY, email="entry-1"),)
    return ExitCompileInput(node=exit_node(), peers=peers)


def xray_binary() -> Path | None:
    explicit = os.environ.get("XRAY_BIN")
    candidates = [Path(explicit)] if explicit else []
    candidates.append(REPO_ROOT / ".tools" / "xray" / "xray")
    for candidate in candidates:
        if candidate.is_file() and os.access(candidate, os.X_OK):
            return candidate
    return None


def xray_asset_dir() -> Path | None:
    explicit = os.environ.get("XRAY_LOCATION_ASSET")
    candidates = [Path(explicit)] if explicit else []
    candidates.append(REPO_ROOT / ".tools")
    for candidate in candidates:
        if (candidate / "geosite.dat").is_file() and (candidate / "geoip.dat").is_file():
            return candidate
    return None


def require_xray() -> tuple[Path, Path]:
    binary = xray_binary()
    assets = xray_asset_dir()
    if binary is None:
        pytest.skip("Xray binary not found (set XRAY_BIN or install .tools/xray/xray)")
    if assets is None:
        pytest.skip("Xray geodata not found (set XRAY_LOCATION_ASSET or install .tools)")
    return binary, assets


def run_xray_test(
    config: dict[str, object],
    tmp_path: Path,
    *,
    name: str = "xray.json",
    base_dir: Path | None = None,
) -> subprocess.CompletedProcess[str]:
    """Write config as canonical JSON and run 'xray run -test' against it."""
    binary, assets = require_xray()
    if base_dir is None:
        base_dir = tmp_path / "node"
    (base_dir / "log").mkdir(parents=True, exist_ok=True)
    path = tmp_path / name
    path.write_text(canonical_json(config), encoding="utf-8")
    env = dict(os.environ, XRAY_LOCATION_ASSET=str(assets))
    return subprocess.run(
        [str(binary), "run", "-test", "-c", str(path)],
        capture_output=True,
        text=True,
        env=env,
        timeout=120,
        check=False,
    )


def assert_xray_ok(config: dict[str, object], tmp_path: Path, name: str = "xray.json") -> str:
    result = run_xray_test(config, tmp_path, name=name)
    output = (result.stdout + result.stderr).strip()
    assert result.returncode == 0, f"xray run -test failed for {name}:\n{output}"
    assert "Configuration OK" in output
    return output
