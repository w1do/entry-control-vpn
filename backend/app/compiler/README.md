# app.compiler - Xray config compiler

Owner: xray-compiler. Contract: `docs/INTERFACES.md` section 3 (fixed),
design: `openspec/changes/add-vpn-control-panel/design.md` section 6.
Public API (import from `app.compiler`):

```python
compile_entry_config(EntryCompileInput) -> dict
compile_exit_config(ExitCompileInput) -> dict
canonical_json(obj) -> str
sha256_json(obj) -> str
match_route(domain, routing) -> RouteDecision
validate_routing(routing) -> list[ProfileIssue]
```

## Modules

| Module | Purpose |
|---|---|
| `types.py` | Frozen dataclasses exactly as in INTERFACES section 3, plus `CompileError`, `RouteDecision`, `ProfileIssue`. |
| `serialize.py` | `canonical_json` (sorted keys, compact separators, ensure_ascii=False), `sha256_json`, `loads_config`. |
| `routing.py` | Fixed fail-closed routing plan: rule order, outbound tags, balancers, outbounds. |
| `inbounds.py` | VLESS+REALITY inbound builder (entry client inbound / exit interconnect). |
| `entry.py` | Full entry-node config (log/dns/inbound/outbounds/routing/observatory/policy/api/stats/metrics). |
| `exit.py` | Exit-node config (REALITY interconnect inbound + freedom egress). |
| `validate.py` | Profile validation returning `ProfileIssue` list with stable codes. |
| `match.py` | `match_route` - the same decision logic used by the routing-check API. |
| `geodata.py` | Stdlib protobuf reader for `geosite.dat` / `geoip.dat` used by `match_route`. |

## Fail-closed routing (fixed order)

Entry rules, in this exact order (INTERFACES section 3):

1. {"type":"field","ip":["geoip:private","169.254.169.254/32"],"outboundTag":"block"}
2. One rule per **enabled** site rule, ordered by (position, id). Domain values
   compile to `domain:<v>`, wildcard to `domain:<v without *>`, geosite to
   `geosite:<v>`, regexp to `regexp:<v>`. If at least one allowed exit is
   enabled -> `balancerTag: bal-rule-<id>`; otherwise -> `outboundTag: block`
   (the rule intentionally stays in the config so matched traffic cannot fall
   through to the default exit).
   A wildcard value `*.example.com` therefore compiles to `domain:.example.com`; Xray's matcher treats a leading dot as
   "subdomains only", which is exactly the usual wildcard meaning - the apex is
   not matched (verified on 26.11.9).
3. {"type":"field","domain":["geosite:category-ru"],"outboundTag":"direct"} - locked.
4. {"type":"field","ip":["geoip:ru"],"outboundTag":"direct"}.
5. Catch-all `tcp,udp` -> `balancerTag: bal-default` if enabled default exits
   exist, otherwise `outboundTag: block`.
6. Final catch-all `tcp,udp` -> `outboundTag: block`.

Outbounds are always `[block (blackhole), direct (freedom), ...exits]`; the
first outbound is `block` so an unmatched connection can never escape directly.
Default exits get tag `exit-<label>`, exits used by a site rule get tag
`rule-<rule_id>-<label>`. Balancers: `bal-default` selector `["exit-"]`,
`bal-rule-<id>` selector `["rule-<id>-"]`, each with `strategy.leastPing` and
`fallbackTag: "block"` (Xray then never degrades to direct or to another group).

`routing.enabled == false` compiles a block-all entry config: the inbound with
clients is kept (clients can connect and are accounted for by stats), routing
contains only the private/metadata block rule and the catch-all block rule, no
exits/balancers are emitted. The `direct` outbound is still present as required
by the "first block, then direct" invariant, but no rule references it.

An exit is treated as **available** for compilation when `enabled == true` (this
is the INTERFACES wording). Runtime health is handled by Xray itself: the
observatory probes every exit, `leastPing` excludes unhealthy outbounds and
`fallbackTag: block` blocks traffic when the whole group is down - health values
are deliberately not baked into a revision, otherwise a transient probe result
would require a new revision. `validate_routing` reports `default_exit_down`
when every enabled default exit is explicitly `health == "down"`.

## Determinism

`compile_*` is a pure function of the input specs. List order is fully defined:
clients/peers keep the caller's order (the panel passes stable DB order), site
rules are sorted by (position, id), default exits and allowlists keep their
declared order, outbounds follow the rule order. `canonical_json` sorts all
object keys, so equal state produces byte-identical JSON and the same
`sha256_json`.

## Verified against real Xray 26.11.9 (xray run -test)

The generated entry and exit configs pass `xray run -test` with
`XRAY_LOCATION_ASSET=/home/dev/vpn/.tools`; see
`backend/tests/compiler/test_xray_integration.py` (skipped when the binary or
geodata is absent).

Field compatibility result: **no field required by docs/INTERFACES.md or
design.md had to be removed or rewritten for Xray 26.11.9** - every section
of design.md section 6.1 is accepted by the pinned binary and validated by
`xray run -test`. The only field-level gap found is that Xray-core's DNS
config has no per-server detour, so "resolve DoH through the default exit"
cannot be expressed inside the dns section (see the limitation above).

Fields explicitly checked on 26.11.9 and kept as-is:

* `api.listen` (127.0.0.1:10085) + services `HandlerService`, `LoggerService`,
  `StatsService` - supported (listen mode, no dokodemo inbound needed);
* `observatory` (subjectSelector, probeUrl, probeInterval "30s",
  enableConcurrency) - supported; balancers with `leastPing` require an
  observatory, so it is always emitted;
* routing `balancers[]` with `strategy: {"type": "leastPing"}` and
  `fallbackTag: "block"` - supported;
* `policy.levels."0".statsUserUplink|statsUserDownlink|statsUserOnline` and
  `policy.system.stats*` - supported (fields survive `-dump`, counters exist);
* `stats: {}` - supported;
* `metrics.listen` - supported in 26.11.9 (field survives `-dump`);
* `dns.servers[].address` with `https://...` DoH URLs - supported;
* REALITY inbound `dest` and `target` - both accepted in 26.11.9; both are
  emitted exactly as design.md section 6 shows (`target` is the canonical name);
* `sniffing.destOverride: ["http","tls","quic"]` + `routeOnly: true` - supported;
* VLESS client `flow: "xtls-rprx-vision"` on TCP+REALITY - supported;
* outbound proxies `http` / `socks` with `servers[].users[{user,pass}]` - supported.

Notes and known limitations:

* **base_dir must be an absolute path on the node** (`/root/config/vpn`,
  `/home/vpn/config/vpn`). Xray does not expand `~`: `xray run -test` fails with
  `open ~/config/vpn/log/access.log: no such file or directory`. The dataclass
  default `~/config/vpn` is a placeholder from the contract; the panel/agent must
  pass the resolved path before compiling.
* `xray run -test` opens the access/error log files, so `<base_dir>/log` must
  already exist on the node (the agent creates `log/` during provisioning).
* **DoH resolvers are dialled by Xray itself**, not through an exit outbound:
  Xray-core has no per-DNS-server detour (unlike sing-box), so the contract's
  "dns DoH" is compiled as a plain `dns.servers` list. The resolver choice still
  prevents using the node's system resolver / Russian resolvers.
* **Exit config is minimal by contract** (inbound + direct outbound + empty
  routing, design.md section 6.2): it intentionally has no api/stats/log. The
  exit node is monitored through agent heartbeats/systemd; per-user stats come
  from the entry node.
* **Exit interconnect port**: design section 6.2 fixes 18443. The compiler honours
  `ExitNodeSpec.inbound.port`; provisioning/revision code must keep it at 18443.
* **External HTTP proxies**: a non-empty `server_name` switches the `http`
  outbound to TLS (`streamSettings.tlsSettings`), which is how HTTPS CONNECT
  proxies are expressed in Xray. `socks5` outbounds have no TLS in Xray.
* **REALITY private_key/short_id format** (base64url / even-length hex) is not
  re-validated here; provisioning generates them with `xray x25519` /
  `openssl rand -hex`. Structural emptiness is rejected with `CompileError`.
* Xray 26.11.9 can also read YAML configs, but revisions are JSON by contract,
  so the compiler emits canonical JSON only.

## Validation codes (validate_routing)

Exit/profile level: `exit_duplicate_id`, `exit_duplicate_label`,
`exit_label_invalid`, `exit_kind_unsupported`, `exit_host_invalid`,
`exit_port_invalid`, `exit_outbound_incomplete`, `exit_credentials_incomplete`,
`default_exit_missing`, `default_exit_unknown`, `default_exit_disabled`,
`default_exit_down`.

Site rules: `site_rule_duplicate_id`, `site_rule_match_type_unsupported`,
`site_rule_values_empty`, `site_rule_value_invalid`,
`site_rule_allowlist_empty`, `site_rule_unknown_exit`, `ru_direct_locked`.

DNS: `dns_servers_empty`, `dns_server_not_doh`.

A disabled profile returns no issues: block-all is a valid fail-closed state.
Disabled site rules are not validated (they are not compiled). A rule whose
allowlisted exits are all disabled is **not** an issue either - it compiles to
`block`, which is exactly the "exit disabled -> fail-closed" requirement.

`CompileError` (subclass of `ValueError`) is raised by `compile_*` for profiles
that must not be applied at all (duplicate tags, enabled rule without values,
invalid regexp, non-DoH DNS, unsupported exit kind, REALITY outbound without
public key, duplicate client emails, ...). The service is expected to call
`validate_routing` first and return 4xx instead of compiling an invalid profile.

## match_route and geodata

`match_route(domain, routing)` mirrors the compiled rule order and returns
`RouteDecision(kind, rule_id, exit_ids, compiled_rule, explanation)` with
Russian `explanation` for the UI. It accepts a bare domain, a URL
(`https://example.com/path`) and IP literals; trailing dots and case are
normalised.

geosite/geoip categories are resolved from real Xray geodata when present:
`match_route` looks for `geosite.dat` / `geoip.dat` via an explicit
`GEOSITE_PATH`/`GEOIP_PATH`, `XRAY_LOCATION_ASSET`, the `XRAY_BIN` directory,
`/usr/local/share/xray`, `/usr/share/xray`, or the development checkout
`<repo>/.tools/`. Without the assets explicit domain/wildcard/regexp rules and
private-IP blocking still work; `geosite:category-ru` falls back to the
documented TLD heuristic (`.ru`, `.su`, `.рф` / `.xn--p1ai`) and the explanation
says the check was heuristic; geosite rules referencing other tags and
`geoip:ru` checks for IP literals cannot be confirmed (the decision stays
fail-safe and the explanation notes it). For exact routing checks in production,
mount the geodata next to the panel container or set `XRAY_LOCATION_ASSET`.

Matching semantics mirror Xray: `domain:` = apex + subdomains; geosite `Plain` =
substring, `Domain` = apex + subdomains, `Full` = exact, `Regex` = regexp
search; geosite attributes (`tag@attr`) are filtered by attribute key.

## Tests

```bash
cd /home/dev/vpn/backend
../.venv/bin/python -m pytest tests/compiler -q
../.venv/bin/ruff check app/compiler tests/compiler
```

`tests/compiler/test_xray_integration.py` runs the real binary found via
`XRAY_BIN` (default `<repo>/.tools/xray/xray`) with `XRAY_LOCATION_ASSET`
(default `<repo>/.tools`) and is skipped when unavailable.
