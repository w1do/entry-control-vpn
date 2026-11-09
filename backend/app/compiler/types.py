"""Frozen dataclasses of the Xray compiler public contract.

The types mirror docs/INTERFACES.md section 3 (owner: xray-compiler).  All specs
are frozen so that a compiled revision cannot be mutated by accident: the config
is always a pure function of the specs.

Sequence fields accept any Iterable and are normalised to tuple in
__post_init__; this keeps the objects hashable and makes the compiler output
independent from the container type the caller happens to use (list vs tuple).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any


class CompileError(ValueError):
    """Raised when a spec cannot be compiled into a fail-closed Xray config.

    Structural problems (duplicate exit labels that would produce colliding
    outbound tags, an enabled site rule without values, an invalid regexp, a
    REALITY outbound without a public key, ...) are refused instead of being
    silently dropped: a silent drop could fall through to another rule and leak
    traffic, which violates the fail-closed requirement.
    """


def _as_tuple(value: Iterable[str] | Iterable[int] | None) -> tuple[Any, ...]:
    if value is None:
        return ()
    if isinstance(value, tuple):
        return value
    return tuple(value)


@dataclass(frozen=True)
class ExitOutboundSpec:
    """One exit endpoint as the entry node reaches it."""

    kind: str  # "vless_reality" | "http" | "socks5"
    host: str
    port: int
    uuid: str | None = None
    public_key: str | None = None  # REALITY pbk
    short_id: str | None = None
    server_name: str | None = None
    fingerprint: str = "chrome"
    spider_x: str = "/"
    username: str | None = None
    password: str | None = None
    expected_exit_ip: str | None = None


@dataclass(frozen=True)
class ExitSpec:
    """A registered exit (node or external proxy)."""

    id: int
    label: str  # slug [a-z0-9-]{1,24}, unique
    region: str
    outbound: ExitOutboundSpec
    enabled: bool = True
    health: str = "up"  # up|down|unknown


@dataclass(frozen=True)
class SiteRuleSpec:
    """An ordered allowlist rule: domains -> ordered allowed exits."""

    id: int
    name: str
    match_type: str  # domain|wildcard|regexp|geosite
    values: tuple[str, ...]
    allowed_exit_ids: tuple[int, ...]  # order = priority
    enabled: bool = True
    position: int = 0

    def __post_init__(self) -> None:
        object.__setattr__(self, "values", _as_tuple(self.values))
        object.__setattr__(self, "allowed_exit_ids", _as_tuple(self.allowed_exit_ids))


@dataclass(frozen=True)
class ClientSpec:
    """A VLESS client of the entry node (also the stats identity)."""

    uuid: str
    email: str
    flow: str = "xtls-rprx-vision"


@dataclass(frozen=True)
class RealityInboundSpec:
    """VLESS+REALITY server-side settings (entry client inbound or exit interconnect)."""

    listen: str
    port: int
    dest: str
    server_names: tuple[str, ...]
    private_key: str
    short_ids: tuple[str, ...]
    flow: str = "xtls-rprx-vision"

    def __post_init__(self) -> None:
        object.__setattr__(self, "server_names", _as_tuple(self.server_names))
        object.__setattr__(self, "short_ids", _as_tuple(self.short_ids))


@dataclass(frozen=True)
class EntryNodeSpec:
    server_id: int
    public_host: str
    inbound: RealityInboundSpec
    base_dir: str = "~/config/vpn"


@dataclass(frozen=True)
class EntryPeerSpec:
    """An entry node allowed to use the exit interconnect."""

    entry_server_id: int
    uuid: str
    email: str


@dataclass(frozen=True)
class ExitNodeSpec:
    server_id: int
    inbound: RealityInboundSpec


@dataclass(frozen=True)
class RoutingSpec:
    exits: tuple[ExitSpec, ...]
    default_exit_ids: tuple[int, ...]
    site_rules: tuple[SiteRuleSpec, ...]
    enabled: bool = True
    dns_servers: tuple[str, ...] = (
        "https://1.1.1.1/dns-query",
        "https://8.8.8.8/dns-query",
    )

    def __post_init__(self) -> None:
        object.__setattr__(self, "exits", _as_tuple(self.exits))
        object.__setattr__(self, "default_exit_ids", _as_tuple(self.default_exit_ids))
        object.__setattr__(self, "site_rules", _as_tuple(self.site_rules))
        object.__setattr__(self, "dns_servers", _as_tuple(self.dns_servers))


@dataclass(frozen=True)
class EntryCompileInput:
    node: EntryNodeSpec
    clients: tuple[ClientSpec, ...]
    routing: RoutingSpec

    def __post_init__(self) -> None:
        object.__setattr__(self, "clients", _as_tuple(self.clients))


@dataclass(frozen=True)
class ExitCompileInput:
    node: ExitNodeSpec
    peers: tuple[EntryPeerSpec, ...]

    def __post_init__(self) -> None:
        object.__setattr__(self, "peers", _as_tuple(self.peers))


@dataclass(frozen=True)
class RouteDecision:
    """Result of match_route - where a destination would go."""

    kind: str  # "ru_direct" | "site_rule" | "default" | "block"
    rule_id: int | None
    exit_ids: tuple[int, ...]
    compiled_rule: dict[str, Any] | None
    explanation: str


@dataclass(frozen=True)
class ProfileIssue:
    """One validation finding of validate_routing."""

    code: str
    message: str
    subject: str | None = None
