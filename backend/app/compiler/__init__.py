"""Xray config compiler - public API (docs/INTERFACES.md section 3).

Entry-node config: VLESS+REALITY client inbound, fail-closed routing
(private/metadata block, site-rule allowlists, RU direct, default exit,
catch-all block), balancers with fallbackTag block, observatory, Stats API,
policy, JSON logs inside the node base_dir, DoH DNS and metrics listener.

Exit-node config: VLESS+REALITY interconnect inbound on port 18443 with one
client per entry peer and a direct (freedom) outbound.
"""

from __future__ import annotations

from .entry import compile_entry_config
from .exit import compile_exit_config
from .match import match_route
from .serialize import canonical_json, loads_config, sha256_json
from .types import (
    ClientSpec,
    CompileError,
    EntryCompileInput,
    EntryNodeSpec,
    EntryPeerSpec,
    ExitCompileInput,
    ExitNodeSpec,
    ExitOutboundSpec,
    ExitSpec,
    ProfileIssue,
    RealityInboundSpec,
    RouteDecision,
    RoutingSpec,
    SiteRuleSpec,
)
from .validate import validate_routing

__all__ = [
    "ClientSpec",
    "CompileError",
    "EntryCompileInput",
    "EntryNodeSpec",
    "EntryPeerSpec",
    "ExitCompileInput",
    "ExitNodeSpec",
    "ExitOutboundSpec",
    "ExitSpec",
    "ProfileIssue",
    "RealityInboundSpec",
    "RouteDecision",
    "RoutingSpec",
    "SiteRuleSpec",
    "canonical_json",
    "compile_entry_config",
    "compile_exit_config",
    "loads_config",
    "match_route",
    "sha256_json",
    "validate_routing",
]
