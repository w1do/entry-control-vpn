"""Fail-closed routing plan for the entry node.

This module owns the fixed rule order required by docs/INTERFACES.md section 3
and design.md section 6:

1. private/metadata IPs -> block;
2. one rule per enabled site rule (sorted by position, then id): balancer
   bal-rule-<id> with the allowlist, or block when no allowed exit is enabled;
3. geosite:category-ru -> direct (locked);
4. geoip:ru -> direct;
5. catch-all tcp,udp -> balancer bal-default when default exits are available,
   otherwise block;
6. final catch-all tcp,udp -> block.

Outbound tags are fixed: default exits are exit-<label>, exits inside site
rules are rule-<rule_id>-<label>.  Balancers use prefix selectors
(exit- / rule-<id>-) with fallbackTag block, so a balancer can never fall
through to direct or to a different exit group.
"""

from __future__ import annotations

import copy
import posixpath
import re
from dataclasses import dataclass, field
from typing import Any

from .types import CompileError, ExitSpec, RoutingSpec, SiteRuleSpec

EXIT_LABEL_RE = re.compile(r"^[a-z0-9-]{1,24}$")
MATCH_TYPES = ("domain", "wildcard", "regexp", "geosite")
EXIT_KINDS = ("vless_reality", "http", "socks5")

DEFAULT_BALANCER_TAG = "bal-default"
BLOCK_TAG = "block"
DIRECT_TAG = "direct"
VLESS_FLOW = "xtls-rprx-vision"
RU_SITE_TAG = "category-ru"

PRIVATE_IP_RULE: dict[str, Any] = {
    "type": "field",
    "ip": ["geoip:private", "169.254.169.254/32"],
    "outboundTag": BLOCK_TAG,
}
RU_DOMAIN_RULE: dict[str, Any] = {
    "type": "field",
    "domain": [f"geosite:{RU_SITE_TAG}"],
    "outboundTag": DIRECT_TAG,
}
RU_IP_RULE: dict[str, Any] = {
    "type": "field",
    "ip": ["geoip:ru"],
    "outboundTag": DIRECT_TAG,
}
DEFAULT_BALANCER_RULE: dict[str, Any] = {
    "type": "field",
    "network": "tcp,udp",
    "balancerTag": DEFAULT_BALANCER_TAG,
}
CATCH_ALL_BLOCK_RULE: dict[str, Any] = {
    "type": "field",
    "network": "tcp,udp",
    "outboundTag": BLOCK_TAG,
}


@dataclass(frozen=True)
class CompiledSiteRule:
    """A site rule compiled to its Xray routing rule plus its usable exits."""

    spec: SiteRuleSpec
    rule: dict[str, Any]
    available_exit_ids: tuple[int, ...]


@dataclass(frozen=True)
class RoutingPlan:
    """Everything the entry config needs from the routing profile."""

    outbounds: tuple[dict[str, Any], ...] = ()
    rules: tuple[dict[str, Any], ...] = ()
    balancers: tuple[dict[str, Any], ...] = ()
    site_rules: tuple[CompiledSiteRule, ...] = ()
    default_exit_ids: tuple[int, ...] = ()
    exit_labels: dict[int, str] = field(default_factory=dict)


def copy_rule(rule: dict[str, Any]) -> dict[str, Any]:
    return copy.deepcopy(rule)


def is_valid_label(label: object) -> bool:
    return isinstance(label, str) and EXIT_LABEL_RE.fullmatch(label) is not None


def join_base_dir(base_dir: str, *parts: str) -> str:
    """Join node base_dir with a relative path (contract: <base_dir>/log/...)."""
    if not isinstance(base_dir, str) or not base_dir.strip():
        raise CompileError("base_dir must be a non-empty path")
    return posixpath.join(base_dir.strip(), *parts)


def geosite_tag_and_attributes(value: str) -> tuple[str, tuple[str, ...]]:
    """Split a geosite rule value like 'anthropic@ru,cn' into tag and attributes."""
    tag, _, raw_attributes = value.partition("@")
    attributes = tuple(attr for attr in raw_attributes.split(",") if attr)
    return tag, attributes


def compile_domain_values(rule: SiteRuleSpec) -> list[str]:
    """Compile site rule values into Xray domain matcher strings (order kept)."""
    if rule.match_type not in MATCH_TYPES:
        raise CompileError(
            f"site rule {rule.id} has unsupported match_type {rule.match_type!r}"
        )
    compiled: list[str] = []
    for raw in rule.values:
        if not isinstance(raw, str) or not raw.strip():
            raise CompileError(f"site rule {rule.id} contains an empty value")
        value = raw.strip()
        if rule.match_type == "domain":
            compiled.append("domain:" + value.lower())
        elif rule.match_type == "wildcard":
            stripped = value.replace("*", "").strip().lower()
            if not stripped:
                raise CompileError(
                    f"site rule {rule.id} wildcard {raw!r} has no domain after removing '*'"
                )
            compiled.append("domain:" + stripped)
        elif rule.match_type == "regexp":
            try:
                re.compile(value)
            except re.error as exc:
                raise CompileError(
                    f"site rule {rule.id} regexp {raw!r} is invalid: {exc}"
                ) from exc
            compiled.append("regexp:" + value)
        else:
            tag, _attributes = geosite_tag_and_attributes(value)
            if not tag or any(char.isspace() for char in value):
                raise CompileError(f"site rule {rule.id} geosite value {raw!r} is invalid")
            compiled.append("geosite:" + value)
    deduplicated: list[str] = []
    seen: set[str] = set()
    for value in compiled:
        if value not in seen:
            seen.add(value)
            deduplicated.append(value)
    return deduplicated


def _exit_index(routing: RoutingSpec) -> dict[int, ExitSpec]:
    index: dict[int, ExitSpec] = {}
    for exit_spec in routing.exits:
        if exit_spec.id in index:
            raise CompileError(f"duplicate exit id {exit_spec.id}")
        index[exit_spec.id] = exit_spec
    return index


def available_exit_ids(allowed: tuple[int, ...], index: dict[int, ExitSpec]) -> tuple[int, ...]:
    """Allowed exits that exist and are enabled, order preserved, duplicates removed."""
    result: list[int] = []
    for exit_id in allowed:
        exit_spec = index.get(exit_id)
        if exit_spec is None or not exit_spec.enabled:
            continue
        if exit_id not in result:
            result.append(exit_id)
    return tuple(result)


def _validate_exit_outbound(exit_spec: ExitSpec) -> None:
    outbound = exit_spec.outbound
    if outbound.kind not in EXIT_KINDS:
        raise CompileError(
            f"exit {exit_spec.label!r} has unsupported kind {outbound.kind!r}"
        )
    if not isinstance(outbound.host, str) or not outbound.host.strip():
        raise CompileError(f"exit {exit_spec.label!r} has an empty host")
    if (
        not isinstance(outbound.port, int)
        or isinstance(outbound.port, bool)
        or not 1 <= outbound.port <= 65535
    ):
        raise CompileError(f"exit {exit_spec.label!r} has invalid port {outbound.port!r}")
    if outbound.kind == "vless_reality":
        missing = [
            name
            for name in ("uuid", "public_key", "server_name")
            if not getattr(outbound, name)
        ]
        if missing:
            raise CompileError(
                f"exit {exit_spec.label!r} vless_reality outbound misses {', '.join(missing)}"
            )
    elif (outbound.username is None) != (outbound.password is None):
        raise CompileError(
            f"exit {exit_spec.label!r} proxy credentials require both username and password"
        )


def compile_outbound(exit_spec: ExitSpec, tag: str) -> dict[str, Any]:
    """Build the Xray outbound of one exit with the given deterministic tag."""
    _validate_exit_outbound(exit_spec)
    outbound = exit_spec.outbound
    if outbound.kind == "vless_reality":
        return {
            "tag": tag,
            "protocol": "vless",
            "settings": {
                "vnext": [
                    {
                        "address": outbound.host,
                        "port": outbound.port,
                        "users": [
                            {
                                "id": outbound.uuid,
                                "encryption": "none",
                                "flow": VLESS_FLOW,
                            }
                        ],
                    }
                ]
            },
            "streamSettings": {
                "network": "tcp",
                "security": "reality",
                "realitySettings": {
                    "serverName": outbound.server_name,
                    "fingerprint": outbound.fingerprint or "chrome",
                    "publicKey": outbound.public_key,
                    "shortId": outbound.short_id or "",
                    "spiderX": outbound.spider_x or "/",
                },
            },
        }
    protocol = "http" if outbound.kind == "http" else "socks"
    server: dict[str, Any] = {"address": outbound.host, "port": outbound.port}
    if outbound.username is not None and outbound.password is not None:
        server["users"] = [{"user": outbound.username, "pass": outbound.password}]
    result: dict[str, Any] = {
        "tag": tag,
        "protocol": protocol,
        "settings": {"servers": [server]},
    }
    if outbound.kind == "http" and outbound.server_name:
        result["streamSettings"] = {
            "network": "tcp",
            "security": "tls",
            "tlsSettings": {
                "serverName": outbound.server_name,
                "fingerprint": outbound.fingerprint or "chrome",
            },
        }
    return result


def _sorted_enabled_rules(routing: RoutingSpec) -> list[SiteRuleSpec]:
    rules = [rule for rule in routing.site_rules if rule.enabled]
    rules.sort(key=lambda rule: (rule.position, rule.id))
    seen_ids: set[int] = set()
    for rule in rules:
        if rule.id in seen_ids:
            raise CompileError(f"duplicate enabled site rule id {rule.id}")
        seen_ids.add(rule.id)
    return rules


def _block_all_plan() -> RoutingPlan:
    return RoutingPlan(rules=(copy_rule(PRIVATE_IP_RULE), copy_rule(CATCH_ALL_BLOCK_RULE)))


def build_plan(routing: RoutingSpec) -> RoutingPlan:
    """Compile the routing profile into outbounds, rules and balancers."""
    if not routing.enabled:
        return _block_all_plan()

    index = _exit_index(routing)
    outbounds: list[dict[str, Any]] = []
    seen_tags: set[str] = set()

    def add_outbound(exit_spec: ExitSpec, tag: str) -> None:
        if tag in seen_tags:
            raise CompileError(f"duplicate outbound tag {tag!r} (exit labels must be unique)")
        seen_tags.add(tag)
        outbounds.append(compile_outbound(exit_spec, tag))

    default_ids: list[int] = []
    for exit_id in routing.default_exit_ids:
        exit_spec = index.get(exit_id)
        if exit_spec is None or not exit_spec.enabled:
            continue
        if exit_id in default_ids:
            continue
        default_ids.append(exit_id)
        add_outbound(exit_spec, f"exit-{exit_spec.label}")

    compiled_rules: list[CompiledSiteRule] = []
    balancers: list[dict[str, Any]] = []
    rules: list[dict[str, Any]] = [copy_rule(PRIVATE_IP_RULE)]
    for site_rule in _sorted_enabled_rules(routing):
        domain_values = compile_domain_values(site_rule)
        usable = available_exit_ids(site_rule.allowed_exit_ids, index)
        rule: dict[str, Any] = {"type": "field", "domain": domain_values}
        if usable:
            tag = f"bal-rule-{site_rule.id}"
            rule["balancerTag"] = tag
            balancers.append(
                {
                    "tag": tag,
                    "selector": [f"rule-{site_rule.id}-"],
                    "strategy": {"type": "leastPing"},
                    "fallbackTag": BLOCK_TAG,
                }
            )
            for exit_id in usable:
                exit_spec = index[exit_id]
                add_outbound(exit_spec, f"rule-{site_rule.id}-{exit_spec.label}")
        else:
            # Fail-closed: the rule stays in the config but sends matched traffic
            # to the blackhole instead of falling through to the default exit.
            rule["outboundTag"] = BLOCK_TAG
        compiled_rules.append(
            CompiledSiteRule(spec=site_rule, rule=rule, available_exit_ids=usable)
        )
        rules.append(rule)

    rules.append(copy_rule(RU_DOMAIN_RULE))
    rules.append(copy_rule(RU_IP_RULE))
    if default_ids:
        rules.append(copy_rule(DEFAULT_BALANCER_RULE))
        balancers.append(
            {
                "tag": DEFAULT_BALANCER_TAG,
                "selector": ["exit-"],
                "strategy": {"type": "leastPing"},
                "fallbackTag": BLOCK_TAG,
            }
        )
    else:
        rules.append(copy_rule(CATCH_ALL_BLOCK_RULE))
    rules.append(copy_rule(CATCH_ALL_BLOCK_RULE))

    labels = {exit_spec.id: exit_spec.label for exit_spec in routing.exits}
    return RoutingPlan(
        outbounds=tuple(outbounds),
        rules=tuple(rules),
        balancers=tuple(balancers),
        site_rules=tuple(compiled_rules),
        default_exit_ids=tuple(default_ids),
        exit_labels=labels,
    )
