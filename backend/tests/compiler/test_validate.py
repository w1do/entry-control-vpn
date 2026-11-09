"""validate_routing: stable codes for every profile problem."""

from __future__ import annotations

from helpers import http_exit, routing_spec, site_rule, vless_exit

from app.compiler import validate_routing


def _codes(issues) -> list[str]:  # noqa: ANN001 - test helper
    return [issue.code for issue in issues]


def _valid_routing():  # noqa: ANN202 - test helper
    return routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11"),
        ),
        default_exit_ids=(1, 2),
        site_rules=(site_rule(5, allowed_exit_ids=(1, 2)),),
    )


def test_valid_profile_has_no_issues() -> None:
    issues = validate_routing(_valid_routing())
    assert issues == []
    assert all(issue.message for issue in issues)


def test_disabled_profile_is_valid_block_all() -> None:
    routing = routing_spec(enabled=False, dns_servers=())
    assert validate_routing(routing) == []


def test_missing_default_exit() -> None:
    issues = validate_routing(routing_spec(default_exit_ids=()))
    assert "default_exit_missing" in _codes(issues)


def test_unknown_default_exit() -> None:
    issues = validate_routing(routing_spec(default_exit_ids=(99,)))
    assert "default_exit_unknown" in _codes(issues)


def test_all_default_exits_disabled() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", enabled=False),
            vless_exit(2, "us-1", "203.0.113.11", enabled=False),
        ),
        default_exit_ids=(1, 2),
    )
    assert "default_exit_disabled" in _codes(validate_routing(routing))


def test_all_default_exits_down() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10", health="down"),
            vless_exit(2, "us-1", "203.0.113.11", health="down"),
        ),
        default_exit_ids=(1, 2),
    )
    assert "default_exit_down" in _codes(validate_routing(routing))


def test_unknown_health_is_not_an_issue() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "sg-1", "203.0.113.10", health="unknown"),),
        default_exit_ids=(1,),
    )
    assert validate_routing(routing) == []


def test_duplicate_exit_id() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "203.0.113.10"),
            vless_exit(1, "us-1", "203.0.113.11"),
        ),
        default_exit_ids=(1,),
    )
    assert "exit_duplicate_id" in _codes(validate_routing(routing))


def test_duplicate_exit_label() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "same", "203.0.113.10"),
            vless_exit(2, "same", "203.0.113.11"),
        ),
        default_exit_ids=(1, 2),
    )
    assert "exit_duplicate_label" in _codes(validate_routing(routing))


def test_invalid_exit_labels() -> None:
    for label in ("SG-1", "with space", "Bad_Label", "x" * 25, ""):
        routing = routing_spec(
            exits=(vless_exit(1, label, "203.0.113.10"),),
            default_exit_ids=(1,),
        )
        assert "exit_label_invalid" in _codes(validate_routing(routing)), label


def test_invalid_exit_kind() -> None:
    exit_spec = vless_exit(1, "sg-1", "203.0.113.10")
    object.__setattr__(exit_spec.outbound, "kind", "wireguard")
    routing = routing_spec(exits=(exit_spec,), default_exit_ids=(1,))
    assert "exit_kind_unsupported" in _codes(validate_routing(routing))


def test_invalid_exit_host_and_port() -> None:
    routing = routing_spec(
        exits=(
            vless_exit(1, "sg-1", "https://203.0.113.10"),
            vless_exit(2, "us-1", "203.0.113.11", port=70000),
        ),
        default_exit_ids=(1, 2),
    )
    codes = _codes(validate_routing(routing))
    assert "exit_host_invalid" in codes
    assert "exit_port_invalid" in codes


def test_vless_exit_missing_fields() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "sg-1", "203.0.113.10", uuid=None, public_key=None),),
        default_exit_ids=(1,),
    )
    issues = validate_routing(routing)
    incomplete = [issue for issue in issues if issue.code == "exit_outbound_incomplete"]
    assert len(incomplete) == 1
    assert "uuid" in incomplete[0].message
    assert "public_key" in incomplete[0].message


def test_http_exit_partial_credentials() -> None:
    routing = routing_spec(
        exits=(http_exit(1, "res-us", username="user", password=None),),
        default_exit_ids=(1,),
    )
    assert "exit_credentials_incomplete" in _codes(validate_routing(routing))


def test_site_rule_empty_allowlist() -> None:
    routing = routing_spec(site_rules=(site_rule(5, allowed_exit_ids=()),))
    assert "site_rule_allowlist_empty" in _codes(validate_routing(routing))


def test_site_rule_unknown_exit() -> None:
    routing = routing_spec(site_rules=(site_rule(5, allowed_exit_ids=(99,)),))
    assert "site_rule_unknown_exit" in _codes(validate_routing(routing))


def test_site_rule_empty_values() -> None:
    routing = routing_spec(site_rules=(site_rule(5, values=()),))
    assert "site_rule_values_empty" in _codes(validate_routing(routing))


def test_site_rule_unsupported_match_type() -> None:
    routing = routing_spec(site_rules=(site_rule(5, match_type="ip"),))
    assert "site_rule_match_type_unsupported" in _codes(validate_routing(routing))


def test_site_rule_invalid_values() -> None:
    cases = [
        ("domain", ("bad domain",)),
        ("domain", ("https://example.com",)),
        ("domain", ("example.com.",)),
        ("domain", ("*.example.com",)),
        ("wildcard", ("example.com",)),
        ("wildcard", ("*",)),
        ("regexp", ("(unclosed",)),
        ("geosite", ("bad tag",)),
    ]
    for match_type, values in cases:
        routing = routing_spec(
            site_rules=(site_rule(5, match_type=match_type, values=values),)
        )
        assert "site_rule_value_invalid" in _codes(validate_routing(routing)), (match_type, values)


def test_ru_direct_locked_for_geosite_tag() -> None:
    routing = routing_spec(
        site_rules=(
            site_rule(5, match_type="geosite", values=("category-ru",), allowed_exit_ids=(1,)),
        )
    )
    assert "ru_direct_locked" in _codes(validate_routing(routing))


def test_ru_direct_locked_for_ru_domains() -> None:
    for match_type, value in (
        ("domain", "yandex.ru"),
        ("domain", "mail.ru"),
        ("domain", "example.рф"),
        ("wildcard", "*.ru"),
        ("domain", "xn--d1acpjx3f.xn--p1ai"),
    ):
        routing = routing_spec(
            site_rules=(
                site_rule(5, match_type=match_type, values=(value,), allowed_exit_ids=(1,)),
            )
        )
        assert "ru_direct_locked" in _codes(validate_routing(routing)), value


def test_foreign_domain_is_not_ru_locked() -> None:
    routing = routing_spec(
        site_rules=(site_rule(5, values=("notru.example.com",), allowed_exit_ids=(1,)),)
    )
    assert "ru_direct_locked" not in _codes(validate_routing(routing))


def test_disabled_rule_is_not_validated() -> None:
    routing = routing_spec(
        site_rules=(site_rule(5, enabled=False, allowed_exit_ids=(), values=()),)
    )
    assert validate_routing(routing) == []


def test_dns_issues() -> None:
    assert "dns_servers_empty" in _codes(validate_routing(routing_spec(dns_servers=())))
    assert "dns_server_not_doh" in _codes(
        validate_routing(routing_spec(dns_servers=("8.8.8.8",)))
    )
    assert "dns_server_not_doh" not in _codes(
        validate_routing(routing_spec(dns_servers=("https://9.9.9.9/dns-query",)))
    )


def test_issues_are_deterministic() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "BAD LABEL", "203.0.113.10"),),
        default_exit_ids=(99,),
        site_rules=(site_rule(5, allowed_exit_ids=()),),
        dns_servers=("8.8.8.8",),
    )
    assert validate_routing(routing) == validate_routing(routing)
    assert len(validate_routing(routing)) >= 4


def test_issue_messages_are_non_empty_and_subjects_for_exit_issues() -> None:
    routing = routing_spec(
        exits=(vless_exit(1, "BAD", "203.0.113.10"),),
        default_exit_ids=(1,),
    )
    issues = validate_routing(routing)
    assert issues
    for issue in issues:
        assert issue.message
    exit_issues = [issue for issue in issues if issue.code.startswith("exit_")]
    assert all(issue.subject for issue in exit_issues)
