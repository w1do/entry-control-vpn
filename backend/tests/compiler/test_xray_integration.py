"""Integration: generated configs must pass the real 'xray run -test'.

The test is skipped when the pinned Xray binary or the geodata is not installed.
Set XRAY_BIN / XRAY_LOCATION_ASSET to override the discovered paths.
"""

from __future__ import annotations

import json
from pathlib import Path

from helpers import (
    assert_xray_ok,
    entry_input,
    exit_input,
    http_exit,
    require_xray,
    routing_spec,
    site_rule,
    socks_exit,
    vless_exit,
)

from app.compiler import canonical_json, compile_entry_config, compile_exit_config

BASE_DIRS: list[Path] = []


def _base_dir(tmp_path: Path) -> str:
    base = tmp_path / "node"
    (base / "log").mkdir(parents=True, exist_ok=True)
    return str(base)


def _golden_routing():  # noqa: ANN202 - test helper
    return routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11"),
            http_exit(3, "res-us"),
            socks_exit(4, "res-socks"),
        ),
        default_exit_ids=(1, 2),
        site_rules=(
            site_rule(5, values=("anthropic.com", "claude.ai", "api.anthropic.com")),
            site_rule(
                6,
                name="OpenAI",
                match_type="geosite",
                values=("openai",),
                allowed_exit_ids=(2, 3),
                position=10,
            ),
            site_rule(
                7,
                name="Wildcard",
                match_type="wildcard",
                values=("*.example.org",),
                allowed_exit_ids=(1,),
                position=5,
            ),
            site_rule(
                8,
                name="Regexp",
                match_type="regexp",
                values=(r"^.*\.claude\.com$",),
                allowed_exit_ids=(2,),
                position=6,
            ),
        ),
    )


def test_entry_config_passes_xray(tmp_path: Path) -> None:
    require_xray()
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=_base_dir(tmp_path)))
    output = assert_xray_ok(config, tmp_path, "entry.json")
    assert "Configuration OK" in output


def test_entry_config_is_valid_json(tmp_path: Path) -> None:
    config = compile_entry_config(entry_input(_golden_routing(), base_dir=_base_dir(tmp_path)))
    assert json.loads(canonical_json(config)) == config


def test_exit_config_passes_xray(tmp_path: Path) -> None:
    require_xray()
    config = compile_exit_config(exit_input())
    assert_xray_ok(config, tmp_path, "exit.json")


def test_block_all_variants_pass_xray(tmp_path: Path) -> None:
    require_xray()
    base_dir = _base_dir(tmp_path)

    disabled = compile_entry_config(
        entry_input(routing_spec(enabled=False), base_dir=base_dir)
    )
    assert_xray_ok(disabled, tmp_path, "entry-disabled.json")

    no_default = compile_entry_config(
        entry_input(routing_spec(default_exit_ids=()), base_dir=base_dir)
    )
    assert_xray_ok(no_default, tmp_path, "entry-no-default.json")

    no_exits = compile_entry_config(
        entry_input(
            routing_spec(exits=(), default_exit_ids=()),
            base_dir=base_dir,
        )
    )
    assert_xray_ok(no_exits, tmp_path, "entry-no-exits.json")

    all_rules_down = compile_entry_config(
        entry_input(
            routing_spec(
                exits=(
                    vless_exit(1, "sg-1", "203.0.113.10", enabled=False),
                    vless_exit(2, "us-1", "203.0.113.11", enabled=False),
                ),
                default_exit_ids=(1, 2),
                site_rules=(site_rule(5, allowed_exit_ids=(1, 2)),),
            ),
            base_dir=base_dir,
        )
    )
    assert_xray_ok(all_rules_down, tmp_path, "entry-rules-down.json")


def test_multiple_clients_and_proxy_outbounds_pass_xray(tmp_path: Path) -> None:
    from app.compiler import ClientSpec

    require_xray()
    clients = (
        ClientSpec(uuid="aaaaaaaa-1111-2222-3333-444444444444", email="one@vpn"),
        ClientSpec(uuid="bbbbbbbb-1111-2222-3333-444444444444", email="two@vpn"),
        ClientSpec(uuid="cccccccc-1111-2222-3333-444444444444", email="three@vpn", flow=""),
    )
    config = compile_entry_config(
        entry_input(_golden_routing(), base_dir=_base_dir(tmp_path), clients=clients)
    )
    assert_xray_ok(config, tmp_path, "entry-clients.json")
