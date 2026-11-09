"""Fail-closed routing rules: order, tags, balancers, allowlists, leaks."""

from __future__ import annotations

import pytest

from app.compiler import CompileError, compile_entry_config
from helpers import (
    entry_input,
    http_exit,
    routing_spec,
    site_rule,
    socks_exit,
    vless_exit,
)

BASE_DIR = "/srv/vpn/node"
PRIVATE_RULE = {
    "type": "field",
    "ip": ["geoip:private", "169.254.169.254/32"],
    "outboundTag": "block",
}
RU_DOMAIN_RULE = {
    "type": "field",
    "domain": ["geosite:category-ru"],
    "outboundTag": "direct",
}
RU_IP_RULE = {"type": "field", "ip": ["geoip:ru"], "outboundTag": "direct"}
CATCH_ALL_BLOCK = {"type": "field", "network": "tcp,udp", "outboundTag": "block"}
DEFAULT_RULE = {"type": "field", "network": "tcp,udp", "balancerTag": "bal-default"}


def _compile(routing):  # noqa: ANN001, ANN202 - test helper
    return compile_entry_config(entry_input(routing, base_dir=BASE_DIR))


def test_private_and_metadata_rule_is_first() -> None:
    config = _compile(routing_spec(site_rules=(site_rule(5),)))
    assert config["routing"]["rules"][0] == PRIVATE_RULE


def test_ru_direct_rules_are_locked() -> None:
    config = _compile(routing_spec(site_rules=(site_rule(5),)))
    rules = config["routing"]["rules"]
    assert rules[-3] == RU_DOMAIN_RULE
    assert rules[-2] == RU_IP_RULE
    assert rules[-1] == CATCH_ALL_BLOCK


def test_rule_order_is_position_then_id() -> None:
    rules = (
        site_rule(7, name="pos10", values=("ten.example",), position=10),
        site_rule(9, name="pos0-b", values=("zero-b.example",), position=0),
        site_rule(5, name="pos0-a", values=("zero-a.example",), position=0),
    )
    config = _compile(routing_spec(site_rules=rules))
    compiled = config["routing"]["rules"]
    assert compiled[0] == PRIVATE_RULE
    assert compiled[1]["domain"] == ["domain:zero-a.example"]
    assert compiled[2]["domain"] == ["domain:zero-b.example"]
    assert compiled[3]["domain"] == ["domain:ten.example"]
    assert compiled[1]["balancerTag"] == "bal-rule-5"
    assert compiled[2]["balancerTag"] == "bal-rule-9"
    assert compiled[3]["balancerTag"] == "bal-rule-7"
    assert compiled[4] == RU_DOMAIN_RULE


def test_disabled_site_rule_is_not_compiled() -> None:
    config = _compile(
        routing_spec(site_rules=(site_rule(5, enabled=False, values=("disabled.example",)),))
    )
    domains = [
        rule.get("domain") for rule in config["routing"]["rules"] if "domain" in rule
    ]
    assert ["disabled.example"] not in [
        [value.replace("domain:", "") for value in values] for values in domains if values
    ]
    assert not any("bal-rule-5" in rule.get("balancerTag", "") for rule in config["routing"]["rules"])


def test_site_rule_allowlist_compiles_to_balancer() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11"),
            vless_exit(3, "de-1", "203.0.113.12"),
        ),
        default_exit_ids=(1,),
        site_rules=(site_rule(5, values=("anthropic.com",), allowed_exit_ids=(1, 2)),),
    )
    config = _compile(routing)
    rule = config["routing"]["rules"][1]
    assert rule == {
        "type": "field",
        "domain": ["domain:anthropic.com"],
        "balancerTag": "bal-rule-5",
    }
    balancer = config["routing"]["balancers"][0]
    assert balancer == {
        "tag": "bal-rule-5",
        "selector": ["rule-5-"],
        "strategy": {"type": "leastPing"},
        "fallbackTag": "block",
    }
    rule_outbounds = [
        outbound["tag"] for outbound in config["outbounds"] if outbound["tag"].startswith("rule-5-")
    ]
    assert rule_outbounds == ["rule-5-sg-1", "rule-5-us-1"]
    assert "rule-5-de-1" not in [outbound["tag"] for outbound in config["outbounds"]]


def test_disabled_exit_is_dropped_from_rule_allowlist() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11", enabled=False),
        ),
        default_exit_ids=(1,),
        site_rules=(site_rule(5, allowed_exit_ids=(1, 2)),),
    )
    config = _compile(routing)
    tags = [outbound["tag"] for outbound in config["outbounds"]]
    assert "rule-5-sg-1" in tags
    assert "rule-5-us-1" not in tags


def test_rule_with_all_exits_disabled_blocks_fail_closed() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", enabled=False),
            vless_exit(2, "us-1", "203.0.113.11", enabled=False),
        ),
        default_exit_ids=(1,),
        site_rules=(site_rule(5, allowed_exit_ids=(1, 2)),),
    )
    config = _compile(routing)
    rule = config["routing"]["rules"][1]
    assert rule == {
        "type": "field",
        "domain": ["domain:anthropic.com", "domain:api.anthropic.com"],
        "outboundTag": "block",
    }
    assert not any(balancer["tag"] == "bal-rule-5" for balancer in config["routing"]["balancers"])
    assert not any(outbound["tag"].startswith("rule-5-") for outbound in config["outbounds"])


def test_unknown_allowed_exit_ids_are_ignored_fail_closed() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "sg-1", "203.0.113.10"),),
        default_exit_ids=(1,),
        site_rules=(site_rule(5, allowed_exit_ids=(99,)),),
    )
    config = _compile(routing)
    assert config["routing"]["rules"][1]["outboundTag"] == "block"

    routing = routing_spec(
        exits=(vless_exit(1, "sg-1", "203.0.113.10"),),
        default_exit_ids=(1,),
        site_rules=(site_rule(5, allowed_exit_ids=(99, 1)),),
    )
    config = _compile(routing)
    assert config["routing"]["rules"][1]["balancerTag"] == "bal-rule-5"
    assert [outbound["tag"] for outbound in config["outbounds"]].count("rule-5-sg-1") == 1


def test_no_default_exit_blocks_unmatched_traffic() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "sg-1", "203.0.113.10"),),
        default_exit_ids=(),
        site_rules=(site_rule(5, values=("anthropic.com",), allowed_exit_ids=(1,)),),
    )
    config = _compile(routing)
    rules = config["routing"]["rules"]
    assert rules[-1] == CATCH_ALL_BLOCK
    assert rules[-2] == CATCH_ALL_BLOCK
    assert not any(rule.get("balancerTag") == "bal-default" for rule in rules)
    assert not any(balancer["tag"] == "bal-default" for balancer in config["routing"]["balancers"])
    assert not any(outbound["tag"].startswith("exit-") for outbound in config["outbounds"])
    # observatory stays in the config: it selects by prefix and simply sees nothing
    assert config["observatory"]["subjectSelector"] == ["exit-", "rule-"]


def test_default_exit_disabled_is_skipped() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", enabled=False),
            vless_exit(2, "us-1", "203.0.113.11"),
        ),
        default_exit_ids=(1, 2),
    )
    config = _compile(routing)
    tags = [outbound["tag"] for outbound in config["outbounds"]]
    assert "exit-sg-1" not in tags
    assert "exit-us-1" in tags
    balancer = next(b for b in config["routing"]["balancers"] if b["tag"] == "bal-default")
    assert balancer["selector"] == ["exit-"]
    assert config["routing"]["rules"][-2] == DEFAULT_RULE


def test_health_down_exits_stay_compiled_for_runtime_failover() -> None:
    # health is dynamic runtime state: the observatory excludes the outbound and
    # fallbackTag block fails closed without a new revision.
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", health="down"),
            vless_exit(2, "us-1", "203.0.113.11", health="down"),
        ),
        default_exit_ids=(1, 2),
        site_rules=(site_rule(5, allowed_exit_ids=(1,)),),
    )
    config = _compile(routing)
    assert config["routing"]["rules"][1]["balancerTag"] == "bal-rule-5"
    assert config["routing"]["rules"][-2] == DEFAULT_RULE


def test_wildcard_geosite_regexp_value_compilation() -> None:
    rules = (
        site_rule(1, name="wild", match_type="wildcard", values=("*.example.com",), allowed_exit_ids=(1,)),
        site_rule(2, name="geo", match_type="geosite", values=("anthropic", "openai@us"), allowed_exit_ids=(1,)),
        site_rule(3, name="re", match_type="regexp", values=(r"^.*\.claude\.com$",), allowed_exit_ids=(1,)),
    )
    config = _compile(routing_spec(site_rules=rules))
    compiled = config["routing"]["rules"]
    assert compiled[1]["domain"] == ["domain:example.com"]
    assert compiled[2]["domain"] == ["geosite:anthropic", "geosite:openai@us"]
    assert compiled[3]["domain"] == [r"regexp:^.*\.claude\.com$"]


def test_domain_values_are_lowercased_and_deduplicated() -> None:
    config = _compile(
        routing_spec(site_rules=(site_rule(5, values=("Example.COM", "example.com")),))
    )
    assert config["routing"]["rules"][1]["domain"] == ["domain:example.com"]


def test_no_direct_leaks() -> None:
    routing = routing_spec(
        site_rules=(site_rule(5, values=("anthropic.com",)), site_rule(6, values=("openai.com",))),
    )
    config = _compile(routing)
    direct_users = [
        rule for rule in config["routing"]["rules"] if rule.get("outboundTag") == "direct"
    ]
    assert direct_users == [RU_DOMAIN_RULE, RU_IP_RULE]
    for balancer in config["routing"]["balancers"]:
        assert balancer["fallbackTag"] == "block"
        assert balancer["strategy"] == {"type": "leastPing"}
    assert config["outbounds"][0] == {"tag": "block", "protocol": "blackhole"}
    assert config["routing"]["rules"][-1] == CATCH_ALL_BLOCK
    # only two outbounds can be reached by a direct rule: the locked RU rules above
    direct_outbound = next(o for o in config["outbounds"] if o["tag"] == "direct")
    assert direct_outbound == {"tag": "direct", "protocol": "freedom"}


def test_external_http_proxy_outbound_structure() -> None:
    config = _compile(
        routing_spec(
            exits=(http_exit(1, "res-us"),),
            default_exit_ids=(1,),
        )
    )
    outbound = next(o for o in config["outbounds"] if o["tag"] == "exit-res-us")
    assert outbound == {
        "tag": "exit-res-us",
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
    }


def test_external_https_proxy_uses_tls() -> None:
    config = _compile(
        routing_spec(
            exits=(http_exit(1, "res-us", server_name="proxy.example.net"),),
            default_exit_ids=(1,),
        )
    )
    outbound = next(o for o in config["outbounds"] if o["tag"] == "exit-res-us")
    assert outbound["streamSettings"] == {
        "network": "tcp",
        "security": "tls",
        "tlsSettings": {"serverName": "proxy.example.net", "fingerprint": "chrome"},
    }


def test_socks_outbound_structure() -> None:
    config = _compile(
        routing_spec(exits=(socks_exit(1, "res-socks"),), default_exit_ids=(1,))
    )
    outbound = next(o for o in config["outbounds"] if o["tag"] == "exit-res-socks")
    assert outbound == {
        "tag": "exit-res-socks",
        "protocol": "socks",
        "settings": {"servers": [{"address": "proxy.example.net", "port": 1080}]},
    }


def test_duplicate_exit_id_is_rejected() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(1, "us-1", "203.0.113.11"),
        ),
        default_exit_ids=(1,),
    )
    with pytest.raises(CompileError):
        _compile(routing)


def test_duplicate_exit_label_is_rejected() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "sg-1", "203.0.113.11"),
        ),
        default_exit_ids=(1, 2),
    )
    with pytest.raises(CompileError):
        _compile(routing)


def test_enabled_rule_without_values_is_rejected() -> None:
    with pytest.raises(CompileError):
        _compile(routing_spec(site_rules=(site_rule(5, values=()),)))


def test_unsupported_match_type_is_rejected() -> None:
    with pytest.raises(CompileError):
        _compile(routing_spec(site_rules=(site_rule(5, match_type="ipset"),)))


def test_invalid_regexp_is_rejected() -> None:
    with pytest.raises(CompileError):
        _compile(
            routing_spec(
                site_rules=(site_rule(5, match_type="regexp", values=("(unclosed",)),)
            )
        )


def test_wildcard_without_star_is_rejected() -> None:
    with pytest.raises(CompileError):
        _compile(
            routing_spec(
                site_rules=(site_rule(5, match_type="wildcard", values=("example.com",)),)
            )
        )


def test_invalid_exit_kind_is_rejected() -> None:
    exit_spec = vless_exit(1, "sg-1", "203.0.113.10")
    object.__setattr__(exit_spec.outbound, "kind", "ss2022")
    with pytest.raises(CompileError):
        _compile(routing_spec(exits=(exit_spec,), default_exit_ids=(1,)))


def test_vless_exit_without_public_key_is_rejected() -> None:
    exit_spec = vless_exit(1, "sg-1", "203.0.113.10", public_key=None)
    with pytest.raises(CompileError):
        _compile(routing_spec(exits=(exit_spec,), default_exit_ids=(1,)))
