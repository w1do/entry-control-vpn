"""Routing profile validation.

validate_routing() is the pre-flight check the panel runs before compiling and
applying a revision.  It never mutates the profile and never raises for bad
input: every finding is returned as a ProfileIssue with a stable machine code
and a Russian message suitable for the UI.

Blocked (fail-closed) states are deliberately not errors: an explicitly
disabled profile and a site rule whose allowlisted exits are all disabled both
compile to blackhole routes (that is the required behaviour when an exit goes
down), so they produce no issue here.  Everything that would make a config
ambiguous or leak traffic (empty allowlist, duplicate tags, RU rule, bad
expressions, missing default exit, non-DoH DNS) does produce an issue.
"""

from __future__ import annotations

import re

from .routing import (
    EXIT_KINDS,
    MATCH_TYPES,
    RU_SITE_TAG,
    geosite_tag_and_attributes,
    is_valid_label,
)
from .types import ExitSpec, ProfileIssue, RoutingSpec, SiteRuleSpec

_RU_TLDS = ("ru", "su", "xn--p1ai")


def _issue(code: str, message: str, subject: str | None = None) -> ProfileIssue:
    return ProfileIssue(code=code, message=message, subject=subject)


def _is_ru_domain_value(value: str) -> bool:
    candidate = value.strip().lower().split("*")[-1].lstrip(".")
    if candidate.startswith("."):
        candidate = candidate[1:]
    if candidate in _RU_TLDS:
        return True
    return any(candidate.endswith("." + tld) for tld in _RU_TLDS)


def _rule_overlaps_ru(rule: SiteRuleSpec) -> bool:
    if rule.match_type == "geosite":
        for value in rule.values:
            tag, _attributes = geosite_tag_and_attributes(value.strip())
            if tag.lower() == RU_SITE_TAG:
                return True
    elif rule.match_type in ("domain", "wildcard"):
        return any(_is_ru_domain_value(value) for value in rule.values)
    return False


def _validate_exit(exit_spec: ExitSpec) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    subject = f"exit:{exit_spec.id}"
    label = exit_spec.label
    if not is_valid_label(label):
        issues.append(
            _issue(
                "exit_label_invalid",
                f"Метка выхода {label!r} не соответствует slug [a-z0-9-]{{1,24}}",
                subject,
            )
        )
    outbound = exit_spec.outbound
    if outbound.kind not in EXIT_KINDS:
        issues.append(
            _issue(
                "exit_kind_unsupported",
                f"Выход {label!r}: неподдерживаемый тип {outbound.kind!r} "
                f"(ожидается {', '.join(EXIT_KINDS)})",
                subject,
            )
        )
    host = outbound.host
    if (
        not isinstance(host, str)
        or not host.strip()
        or any(char.isspace() for char in host)
        or "://" in host
    ):
        issues.append(
            _issue("exit_host_invalid", f"Выход {label!r}: некорректный host {host!r}", subject)
        )
    port = outbound.port
    if (
        not isinstance(port, int)
        or isinstance(port, bool)
        or not 1 <= port <= 65535
    ):
        issues.append(
            _issue("exit_port_invalid", f"Выход {label!r}: некорректный порт {port!r}", subject)
        )
    if outbound.kind == "vless_reality":
        missing = [
            name for name in ("uuid", "public_key", "server_name") if not getattr(outbound, name)
        ]
        if missing:
            issues.append(
                _issue(
                    "exit_outbound_incomplete",
                    f"Выход {label!r}: для VLESS+REALITY не заданы {', '.join(missing)}",
                    subject,
                )
            )
    elif outbound.kind in ("http", "socks5") and (
        (outbound.username is None) != (outbound.password is None)
    ):
        issues.append(
            _issue(
                "exit_credentials_incomplete",
                f"Выход {label!r}: логин и пароль прокси задаются только вместе",
                subject,
            )
        )
    return issues


def _validate_default_exits(
    routing: RoutingSpec, exits_by_id: dict[int, ExitSpec]
) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    if not routing.default_exit_ids:
        issues.append(
            _issue(
                "default_exit_missing",
                "Выход по умолчанию не задан: включить маршрутизацию нельзя, "
                "весь трафик будет блокироваться",
            )
        )
        return issues
    known: list[ExitSpec] = []
    for exit_id in routing.default_exit_ids:
        exit_spec = exits_by_id.get(exit_id)
        if exit_spec is None:
            issues.append(
                _issue(
                    "default_exit_unknown",
                    f"Выход по умолчанию id={exit_id} не найден в реестре выходов",
                    f"exit:{exit_id}",
                )
            )
        else:
            known.append(exit_spec)
    available = [exit_spec for exit_spec in known if exit_spec.enabled]
    if known and not available:
        issues.append(
            _issue(
                "default_exit_disabled",
                "Все выходы по умолчанию отключены: трафик будет блокироваться (fail-closed)",
            )
        )
    elif available and all(exit_spec.health == "down" for exit_spec in available):
        issues.append(
            _issue(
                "default_exit_down",
                "Все выходы по умолчанию недоступны (health=down): нет health-выхода",
            )
        )
    return issues


def _validate_value(rule: SiteRuleSpec, value: object) -> list[ProfileIssue]:
    subject = f"site_rule:{rule.id}"
    if not isinstance(value, str) or not value.strip():
        return [
            _issue(
                "site_rule_value_invalid",
                f"Правило {rule.name!r} (id={rule.id}): пустое значение",
                subject,
            )
        ]
    raw = value.strip()
    candidate = raw.lower()
    if rule.match_type == "domain":
        normalized = candidate.lstrip(".")
        if (
            any(char.isspace() for char in normalized)
            or "://" in normalized
            or not normalized
            or normalized.endswith(".")
            or "*" in normalized
        ):
            return [
                _issue(
                    "site_rule_value_invalid",
                    f"Правило {rule.name!r} (id={rule.id}): некорректный домен {raw!r}",
                    subject,
                )
            ]
    elif rule.match_type == "wildcard":
        stripped = candidate.replace("*", "")
        if "*" not in candidate or not stripped.lstrip(".") or any(
            char.isspace() for char in stripped
        ) or "://" in stripped:
            return [
                _issue(
                    "site_rule_value_invalid",
                    f"Правило {rule.name!r} (id={rule.id}): некорректный wildcard {raw!r}",
                    subject,
                )
            ]
    elif rule.match_type == "regexp":
        try:
            re.compile(raw)
        except re.error as exc:
            return [
                _issue(
                    "site_rule_value_invalid",
                    f"Правило {rule.name!r} (id={rule.id}): regexp {raw!r} не компилируется ({exc})",
                    subject,
                )
            ]
    elif rule.match_type == "geosite":
        tag, _attributes = geosite_tag_and_attributes(candidate)
        if not tag or any(char.isspace() for char in candidate) or "@@" in candidate:
            return [
                _issue(
                    "site_rule_value_invalid",
                    f"Правило {rule.name!r} (id={rule.id}): некорректный geosite-тег {raw!r}",
                    subject,
                )
            ]
    return []


def _validate_site_rule(
    rule: SiteRuleSpec, exits_by_id: dict[int, ExitSpec]
) -> list[ProfileIssue]:
    issues: list[ProfileIssue] = []
    subject = f"site_rule:{rule.id}"
    if rule.match_type not in MATCH_TYPES:
        issues.append(
            _issue(
                "site_rule_match_type_unsupported",
                f"Правило {rule.name!r} (id={rule.id}): неподдерживаемый match_type "
                f"{rule.match_type!r} (ожидается {', '.join(MATCH_TYPES)})",
                subject,
            )
        )
        return issues
    if not rule.values:
        issues.append(
            _issue(
                "site_rule_values_empty",
                f"Правило {rule.name!r} (id={rule.id}): не задано ни одного значения",
                subject,
            )
        )
    for value in rule.values:
        issues.extend(_validate_value(rule, value))
    if not rule.allowed_exit_ids:
        issues.append(
            _issue(
                "site_rule_allowlist_empty",
                f"Правило {rule.name!r} (id={rule.id}): пустой allowlist выходов - "
                "правило не компилируется",
                subject,
            )
        )
    unknown = [exit_id for exit_id in rule.allowed_exit_ids if exit_id not in exits_by_id]
    if unknown:
        issues.append(
            _issue(
                "site_rule_unknown_exit",
                f"Правило {rule.name!r} (id={rule.id}): выходы {unknown} не найдены в реестре",
                subject,
            )
        )
    if _rule_overlaps_ru(rule):
        issues.append(
            _issue(
                "ru_direct_locked",
                f"Правило {rule.name!r} (id={rule.id}) перекрывает российский трафик: "
                "RU-домены всегда идут напрямую и не могут проксироваться",
                subject,
            )
        )
    return issues


def validate_routing(routing: RoutingSpec) -> list[ProfileIssue]:
    """Return every problem of the routing profile (empty list = valid)."""
    if not routing.enabled:
        return []
    issues: list[ProfileIssue] = []

    exits_by_id: dict[int, ExitSpec] = {}
    labels: dict[str, int] = {}
    for exit_spec in routing.exits:
        if exit_spec.id in exits_by_id:
            issues.append(
                _issue(
                    "exit_duplicate_id",
                    f"Выход id={exit_spec.id} встречается в реестре больше одного раза",
                    f"exit:{exit_spec.id}",
                )
            )
        else:
            exits_by_id[exit_spec.id] = exit_spec
        issues.extend(_validate_exit(exit_spec))
        if is_valid_label(exit_spec.label):
            if exit_spec.label in labels:
                issues.append(
                    _issue(
                        "exit_duplicate_label",
                        f"Метка выхода {exit_spec.label!r} повторяется "
                        f"(id={labels[exit_spec.label]} и id={exit_spec.id})",
                        f"exit:{exit_spec.id}",
                    )
                )
            else:
                labels[exit_spec.label] = exit_spec.id

    issues.extend(_validate_default_exits(routing, exits_by_id))

    rules = [rule for rule in routing.site_rules if rule.enabled]
    rules.sort(key=lambda rule: (rule.position, rule.id))
    seen_rule_ids: set[int] = set()
    for rule in rules:
        if rule.id in seen_rule_ids:
            issues.append(
                _issue(
                    "site_rule_duplicate_id",
                    f"Правило id={rule.id} включено больше одного раза",
                    f"site_rule:{rule.id}",
                )
            )
            continue
        seen_rule_ids.add(rule.id)
        issues.extend(_validate_site_rule(rule, exits_by_id))

    if not routing.dns_servers:
        issues.append(
            _issue("dns_servers_empty", "Не задано ни одного DoH-резолвера для entry-узла")
        )
    for server in routing.dns_servers:
        if not isinstance(server, str) or not server.startswith("https://"):
            issues.append(
                _issue(
                    "dns_server_not_doh",
                    f"DNS-сервер {server!r} не является DoH URL (требуется https://...)",
                )
            )
    return issues
