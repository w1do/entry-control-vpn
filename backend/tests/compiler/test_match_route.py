"""match_route: the routing-check API must mirror the compiled config."""

from __future__ import annotations

import pytest
from helpers import entry_input, http_exit, routing_spec, site_rule, vless_exit

from app.compiler import compile_entry_config, geodata, match_route

BASE_DIR = "/srv/vpn/node"
MISSING_GEODATA = "/nonexistent/geodata/geosite.dat"
MISSING_GEOIP = "/nonexistent/geodata/geoip.dat"


def _routing():  # noqa: ANN202 - test helper
    return routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11"),
            http_exit(3, "res-us"),
        ),
        default_exit_ids=(1, 2),
        site_rules=(
            site_rule(5, values=("anthropic.com", "api.anthropic.com"), allowed_exit_ids=(1, 2)),
            site_rule(
                6,
                name="Example",
                match_type="wildcard",
                values=("*.example.org",),
                allowed_exit_ids=(2,),
            ),
            site_rule(
                7,
                name="Claude regexp",
                match_type="regexp",
                values=(r"^.*\.claude\.com$",),
                allowed_exit_ids=(1,),
            ),
            site_rule(
                8,
                name="Anthropic geosite",
                match_type="geosite",
                values=("anthropic",),
                allowed_exit_ids=(1, 2),
            ),
        ),
    )


def _require_geosite(tag: str = "category-ru"):  # noqa: ANN202 - test helper
    matcher = geodata.geosite_matcher(tag)
    if matcher is None:
        pytest.skip("geosite.dat is not available")
    return matcher


def _require_geoip(country: str = "ru"):  # noqa: ANN202 - test helper
    matcher = geodata.geoip_matcher(country)
    if matcher is None:
        pytest.skip("geoip.dat is not available")
    return matcher


def test_explicit_domain_rule() -> None:
    decision = match_route("anthropic.com", _routing())
    assert decision.kind == "site_rule"
    assert decision.rule_id == 5
    assert decision.exit_ids == (1, 2)
    assert decision.compiled_rule == {
        "type": "field",
        "domain": ["domain:anthropic.com", "domain:api.anthropic.com"],
        "balancerTag": "bal-rule-5",
    }
    assert "Anthropic" in decision.explanation


def test_subdomain_matches_domain_rule() -> None:
    decision = match_route("api.anthropic.com", _routing())
    assert decision.kind == "site_rule"
    assert decision.rule_id == 5


def test_wildcard_rule_matches_subdomains_only() -> None:
    # "*.example.org" compiles to "domain:.example.org"; Xray treats a leading
    # dot as subdomains-only, so the apex is not matched by the wildcard.
    decision = match_route("sub.example.org", _routing())
    assert decision.rule_id == 6
    assert decision.exit_ids == (2,)
    apex = match_route("example.org", _routing())
    assert apex.kind == "default"


def test_regexp_rule() -> None:
    decision = match_route("chat.claude.com", _routing())
    assert decision.kind == "site_rule"
    assert decision.rule_id == 7


def test_geosite_site_rule() -> None:
    _require_geosite("anthropic")
    decision = match_route("claude.ai", _routing())
    assert decision.kind == "site_rule"
    assert decision.rule_id == 8


def test_ru_domain_goes_direct() -> None:
    _require_geosite("category-ru")
    decision = match_route("yandex.ru", _routing())
    assert decision.kind == "ru_direct"
    assert decision.rule_id is None
    assert decision.exit_ids == ()
    assert decision.compiled_rule == {
        "type": "field",
        "domain": ["geosite:category-ru"],
        "outboundTag": "direct",
    }
    assert "direct" in decision.explanation


def test_ru_ip_goes_direct() -> None:
    matcher = _require_geoip()
    import ipaddress

    ru_ip = next(
        candidate
        for candidate in ("77.88.8.8", "5.255.255.1", "213.180.203.1")
        if matcher.match(ipaddress.ip_address(candidate))
    )
    decision = match_route(ru_ip, _routing())
    assert decision.kind == "ru_direct"
    assert decision.compiled_rule == {
        "type": "field",
        "ip": ["geoip:ru"],
        "outboundTag": "direct",
    }


def test_private_and_metadata_ips_blocked() -> None:
    for ip in ("10.0.0.1", "172.16.5.4", "192.168.1.1", "127.0.0.1", "169.254.169.254", "::1"):
        decision = match_route(ip, _routing())
        assert decision.kind == "block", ip
        assert decision.compiled_rule == {
            "type": "field",
            "ip": ["geoip:private", "169.254.169.254/32"],
            "outboundTag": "block",
        }


def test_public_ip_goes_to_default_exit() -> None:
    decision = match_route("8.8.8.8", _routing())
    assert decision.kind == "default"
    assert decision.exit_ids == (1, 2)
    assert decision.compiled_rule == {
        "type": "field",
        "network": "tcp,udp",
        "balancerTag": "bal-default",
    }


def test_unmatched_domain_goes_to_default_exit() -> None:
    decision = match_route("example.com", _routing())
    assert decision.kind == "default"
    assert decision.exit_ids == (1, 2)
    assert "sg-1" in decision.explanation


def test_no_default_exit_blocks() -> None:
    decision = match_route("example.com", routing_spec(default_exit_ids=()))
    assert decision.kind == "block"
    assert decision.compiled_rule == {
        "type": "field",
        "network": "tcp,udp",
        "outboundTag": "block",
    }


def test_routing_disabled_blocks_everything() -> None:
    decision = match_route("example.com", routing_spec(enabled=False))
    assert decision.kind == "block"
    assert "выключена" in decision.explanation


def test_rule_without_enabled_exits_blocks() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", enabled=False),
            vless_exit(2, "us-1", "203.0.113.11"),
        ),
        default_exit_ids=(2,),
        site_rules=(site_rule(5, values=("anthropic.com",), allowed_exit_ids=(1,)),),
    )
    decision = match_route("anthropic.com", routing)
    assert decision.kind == "block"
    assert decision.rule_id == 5
    assert decision.compiled_rule == {
        "type": "field",
        "domain": ["domain:anthropic.com"],
        "outboundTag": "block",
    }


def test_allowlist_order_is_preserved() -> None:
    routing = routing_spec(
        site_rules=(site_rule(5, values=("anthropic.com",), allowed_exit_ids=(2, 1)),),
    )
    decision = match_route("anthropic.com", routing)
    assert decision.exit_ids == (2, 1)


def test_input_normalisation() -> None:
    for raw in (
        "API.ANTHROPIC.COM",
        "api.anthropic.com.",
        "https://api.anthropic.com/v1/messages?q=1",
        "api.anthropic.com:443",
    ):
        decision = match_route(raw, _routing())
        assert decision.kind == "site_rule", raw
        assert decision.rule_id == 5, raw


def test_empty_input_blocks() -> None:
    decision = match_route("   ", _routing())
    assert decision.kind == "block"
    assert "fail-closed" in decision.explanation


def test_invalid_profile_fails_closed() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "same", "203.0.113.10"),
            vless_exit(2, "same", "203.0.113.11"),
        ),
        default_exit_ids=(1, 2),
    )
    decision = match_route("anthropic.com", routing)
    assert decision.kind == "block"
    assert decision.compiled_rule is None
    assert "невалиден" in decision.explanation


def test_decision_compiled_rule_equals_compiled_config_rule() -> None:
    routing = _routing()
    config = compile_entry_config(entry_input(routing, base_dir=BASE_DIR))
    compiled_rules = config["routing"]["rules"]
    decision = match_route("anthropic.com", routing)
    assert decision.compiled_rule in compiled_rules

    default_decision = match_route("example.com", routing)
    assert default_decision.compiled_rule in compiled_rules

    ru_decision = match_route("yandex.ru", routing)
    if ru_decision.kind == "ru_direct":
        assert ru_decision.compiled_rule in compiled_rules


def test_geosite_heuristic_without_geodata() -> None:
    decision = match_route("yandex.ru", _routing(), geosite_path=MISSING_GEODATA)
    assert decision.kind == "ru_direct"
    assert "эвристика" in decision.explanation

    fallback = match_route("example.com", _routing(), geosite_path=MISSING_GEODATA)
    assert fallback.kind == "default"
    assert "geodata" in fallback.explanation


def test_geoip_check_unavailable_without_geodata() -> None:
    decision = match_route("77.88.8.8", _routing(), geoip_path=MISSING_GEOIP)
    assert decision.kind == "default"
    assert "geodata" in decision.explanation


def test_all_decisions_have_explanations() -> None:
    routing = _routing()
    for raw in ("anthropic.com", "example.com", "10.0.0.1", "yandex.ru", "8.8.8.8"):
        decision = match_route(raw, routing)
        assert decision.explanation
        assert decision.kind in {"ru_direct", "site_rule", "default", "block"}
