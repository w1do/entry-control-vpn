"""Effective route matching for the routing check API.

match_route() answers "where would this destination go?" using exactly the
rule order and the allowlist/fail-closed semantics of the compiled config
(routing.py), so the panel never shows a decision that differs from the
compiled revision.  geosite/geoip categories are resolved from the Xray
geodata assets when they are available (see geodata.py); without the assets the
match is still exact for explicit domain/wildcard/regexp rules, and only
category-ru domain matching falls back to the documented TLD heuristic (the
explanation says so explicitly).
"""

from __future__ import annotations

import ipaddress
import os
import re
from urllib.parse import urlsplit

from . import geodata
from .routing import (
    CATCH_ALL_BLOCK_RULE,
    DEFAULT_BALANCER_RULE,
    PRIVATE_IP_RULE,
    RU_DOMAIN_RULE,
    RU_IP_RULE,
    RU_SITE_TAG,
    RoutingPlan,
    build_plan,
    copy_rule,
    geosite_tag_and_attributes,
)
from .types import CompileError, RouteDecision, RoutingSpec, SiteRuleSpec

_RU_TLDS = ("ru", "su", "xn--p1ai")
_RU_DIRECT_DOMAIN = "direct (напрямую с entry-узла, без exit-выходов)"
_RU_DIRECT_IP = "direct (напрямую с entry-узла, без exit-выходов)"


def _is_private_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return bool(
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
    )


def _looks_like_ru_domain(host: str) -> bool:
    labels = host.split(".")
    return len(labels) >= 2 and labels[-1] in _RU_TLDS


def _domain_rule_matches(host: str, rule: str) -> bool:
    """Mirror Xray's domain matcher.

    "example.com" matches the apex and every subdomain; ".example.com" (what a
    wildcard "*.example.com" compiles to) matches subdomains only.  Verified
    against Xray 26.11.9: domain:.example.com blocks sub.example.com but not
    example.com.
    """
    if host == rule or host.endswith("." + rule):
        return True
    return rule.startswith(".") and host.endswith(rule)


def _normalize_target(
    raw: object,
) -> tuple[str, ipaddress.IPv4Address | ipaddress.IPv6Address | None]:
    """Normalise a user supplied domain/IP/URL into (host, ip or None)."""
    if not isinstance(raw, str):
        return "", None
    value = raw.strip()
    if not value:
        return "", None
    if "://" in value:
        try:
            value = urlsplit(value).hostname or ""
        except ValueError:
            return "", None
    elif value.startswith("["):
        closing = value.find("]")
        if closing != -1:
            value = value[1:closing]
    elif value.count(":") == 1:
        host_part, _, port_part = value.partition(":")
        if host_part and port_part.isdigit():
            value = host_part
    value = value.strip().rstrip(".").lower()
    try:
        return value, ipaddress.ip_address(value)
    except ValueError:
        return value, None


def _labels(plan: RoutingPlan, exit_ids: tuple[int, ...]) -> str:
    return ", ".join(plan.exit_labels.get(exit_id, f"exit-id-{exit_id}") for exit_id in exit_ids)


def _site_rule_matches(
    rule: SiteRuleSpec, host: str, geosite_path: str | os.PathLike[str] | None
) -> tuple[bool, bool]:
    """Return (matched, geosite_data_missing) for one site rule."""
    missing = False
    for raw in rule.values:
        value = str(raw).strip()
        if not value:
            continue
        if rule.match_type == "domain":
            if _domain_rule_matches(host, value.lower()):
                return True, missing
        elif rule.match_type == "wildcard":
            pattern = value.replace("*", "").strip().lower()
            if pattern and _domain_rule_matches(host, pattern):
                return True, missing
        elif rule.match_type == "regexp":
            try:
                if re.search(value, host):
                    return True, missing
            except re.error:
                continue
        elif rule.match_type == "geosite":
            tag, attributes = geosite_tag_and_attributes(value.lower())
            matcher = geodata.geosite_matcher(tag, attributes, path=geosite_path)
            if matcher is None:
                missing = True
                continue
            if matcher.match(host):
                return True, missing
    return False, missing


def _note(parts: list[str]) -> str:
    unique = [part for index, part in enumerate(parts) if part not in parts[:index]]
    if not unique:
        return ""
    return " Примечание: " + "; ".join(unique) + "."


def _match_ip(
    ip: ipaddress.IPv4Address | ipaddress.IPv6Address,
    plan: RoutingPlan,
    geoip_path: str | os.PathLike[str] | None,
) -> RouteDecision:
    if _is_private_ip(ip):
        return RouteDecision(
            kind="block",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(PRIVATE_IP_RULE),
            explanation=f"IP {ip} приватный или служебный (geoip:private, metadata) - блокировка.",
        )
    matcher = geodata.geoip_matcher("ru", path=geoip_path)
    if matcher is not None and matcher.match(ip):
        return RouteDecision(
            kind="ru_direct",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(RU_IP_RULE),
            explanation=f"IP {ip} входит в geoip:ru - {_RU_DIRECT_IP}.",
        )
    note = ""
    if matcher is None:
        note = _note(["geodata недоступна, принадлежность IP к geoip:ru не проверена"])
    if plan.default_exit_ids:
        return RouteDecision(
            kind="default",
            rule_id=None,
            exit_ids=plan.default_exit_ids,
            compiled_rule=copy_rule(DEFAULT_BALANCER_RULE),
            explanation=(
                f"IP {ip} не приватный и не RU - выход по умолчанию: "
                f"{_labels(plan, plan.default_exit_ids)} (balancer bal-default).{note}"
            ),
        )
    return RouteDecision(
        kind="block",
        rule_id=None,
        exit_ids=(),
        compiled_rule=copy_rule(CATCH_ALL_BLOCK_RULE),
        explanation=f"IP {ip}: выход по умолчанию не настроен - блокировка (fail-closed).{note}",
    )


def _match_domain(
    host: str,
    plan: RoutingPlan,
    geosite_path: str | os.PathLike[str] | None,
) -> RouteDecision:
    notes: list[str] = []
    for compiled in plan.site_rules:
        matched, missing = _site_rule_matches(compiled.spec, host, geosite_path)
        if missing:
            notes.append("часть geosite-правил не проверена: geodata недоступна")
        if not matched:
            continue
        name = compiled.spec.name
        rule_id = compiled.spec.id
        if compiled.available_exit_ids:
            return RouteDecision(
                kind="site_rule",
                rule_id=rule_id,
                exit_ids=compiled.available_exit_ids,
                compiled_rule=copy_rule(compiled.rule),
                explanation=(
                    f"Правило {name!r} (id={rule_id}) совпало: домен {host} разрешён только "
                    f"через {_labels(plan, compiled.available_exit_ids)} "
                    f"(balancer bal-rule-{rule_id}, leastPing, fallback block)."
                ),
            )
        return RouteDecision(
            kind="block",
            rule_id=rule_id,
            exit_ids=(),
            compiled_rule=copy_rule(compiled.rule),
            explanation=(
                f"Правило {name!r} (id={rule_id}) совпало, но ни один из разрешённых "
                "выходов не включён - блокировка (fail-closed)."
            ),
        )

    ru_matcher = geodata.geosite_matcher(RU_SITE_TAG, path=geosite_path)
    if ru_matcher is not None and ru_matcher.match(host):
        return RouteDecision(
            kind="ru_direct",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(RU_DOMAIN_RULE),
            explanation=(
                f"Домен {host} входит в geosite:{RU_SITE_TAG} - {_RU_DIRECT_DOMAIN}."
            ),
        )
    if ru_matcher is None and _looks_like_ru_domain(host):
        return RouteDecision(
            kind="ru_direct",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(RU_DOMAIN_RULE),
            explanation=(
                f"Домен {host} российский по TLD (geodata недоступна, эвристика) - "
                f"{_RU_DIRECT_DOMAIN}."
            ),
        )
    if ru_matcher is None:
        notes.append("тег geosite:category-ru не проверен: geodata недоступна")

    if plan.default_exit_ids:
        return RouteDecision(
            kind="default",
            rule_id=None,
            exit_ids=plan.default_exit_ids,
            compiled_rule=copy_rule(DEFAULT_BALANCER_RULE),
            explanation=(
                f"Домен {host} не совпал с правилами и не российский - выход по умолчанию: "
                f"{_labels(plan, plan.default_exit_ids)} (balancer bal-default)."
                + _note(notes)
            ),
        )
    return RouteDecision(
        kind="block",
        rule_id=None,
        exit_ids=(),
        compiled_rule=copy_rule(CATCH_ALL_BLOCK_RULE),
        explanation=(
            f"Домен {host} не совпал с правилами, выход по умолчанию не настроен - "
            f"блокировка (fail-closed).{_note(notes)}"
        ),
    )


def match_route(
    domain: str,
    routing: RoutingSpec,
    *,
    geosite_path: str | os.PathLike[str] | None = None,
    geoip_path: str | os.PathLike[str] | None = None,
) -> RouteDecision:
    """Decide where a domain/IP would go under the given routing profile."""
    host, ip = _normalize_target(domain)
    if not host:
        return RouteDecision(
            kind="block",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(CATCH_ALL_BLOCK_RULE),
            explanation="Пустой домен или IP - блокировка (fail-closed).",
        )
    if not routing.enabled:
        return RouteDecision(
            kind="block",
            rule_id=None,
            exit_ids=(),
            compiled_rule=copy_rule(CATCH_ALL_BLOCK_RULE),
            explanation="Маршрутизация выключена: весь трафик блокируется (fail-closed).",
        )
    try:
        plan = build_plan(routing)
    except CompileError as exc:
        return RouteDecision(
            kind="block",
            rule_id=None,
            exit_ids=(),
            compiled_rule=None,
            explanation=f"Профиль маршрутизации невалиден ({exc}) - блокировка (fail-closed).",
        )
    if ip is not None:
        return _match_ip(ip, plan, geoip_path)
    return _match_domain(host, plan, geosite_path)
