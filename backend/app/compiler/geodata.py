"""Read-only access to Xray geodata (geosite.dat / geoip.dat).

The routing profile references geodata categories (geosite:category-ru,
geoip:ru, ...).  The compiled config only carries the category names because
Xray resolves them itself.  The panel, however, has to answer "where would
this domain go?" before applying a revision (routing check API), so
match_route needs the same data.

The files are stock V2Ray/Xray protobuf assets (GeoSiteList / GeoIPList).  We
parse them with a tiny wire-format reader (stdlib only, no protobuf runtime) and
cache the matchers per (file, tag) pair.  If the assets are not installed on the
panel host, match_route degrades gracefully:

* domains are still matched against explicit domain/wildcard/regexp rules;
* geosite rules cannot be verified, only the documented category-ru TLD
  heuristic is applied (see match.route_decision);
* geoip checks for IP literals are unavailable.

The panel therefore keeps working without the assets, and routing check becomes
exact as soon as geodata is mounted next to the app (XRAY_LOCATION_ASSET, or
GEOSITE_PATH / GEOIP_PATH, or /usr/local/share/xray).
"""

from __future__ import annotations

import ipaddress
import os
import re
from collections.abc import Iterator
from pathlib import Path
from typing import Any

# geosite Domain.Type values (V2Ray geosite.proto).
_TYPE_PLAIN = 0
_TYPE_REGEX = 1
_TYPE_DOMAIN = 2
_TYPE_FULL = 3

_CACHE: dict[tuple[str, str, tuple[str, ...]], Any] = {}
_ASSET_BYTES: dict[str, bytes] = {}


def _varint(buf: bytes, pos: int) -> tuple[int, int]:
    result = 0
    shift = 0
    while True:
        if pos >= len(buf):
            raise ValueError("truncated varint")
        byte = buf[pos]
        pos += 1
        result |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return result, pos
        shift += 7
        if shift > 63:
            raise ValueError("varint too long")


def _skip(buf: bytes, pos: int, wire_type: int) -> int:
    if wire_type == 0:
        return _varint(buf, pos)[1]
    if wire_type == 1:
        return pos + 8
    if wire_type == 5:
        return pos + 4
    raise ValueError(f"unsupported wire type {wire_type}")


def _iter_fields(buf: bytes, start: int, end: int) -> Iterator[tuple[int, int, Any]]:
    """Yield (field_number, wire_type, value) for a protobuf message slice.

    Length-delimited values are returned as bytes, varints as int, fixed-width
    values as raw bytes.
    """
    pos = start
    while pos < end:
        key, pos = _varint(buf, pos)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type == 0:
            value, pos = _varint(buf, pos)
        elif wire_type == 1:
            value = buf[pos : pos + 8]
            pos += 8
        elif wire_type == 2:
            length, pos = _varint(buf, pos)
            value = buf[pos : pos + length]
            pos += length
        elif wire_type == 5:
            value = buf[pos : pos + 4]
            pos += 4
        else:  # pragma: no cover - geodata never uses groups
            raise ValueError(f"unsupported wire type {wire_type}")
        if pos > end:
            raise ValueError("field exceeds message boundary")
        yield field_number, wire_type, value


def _index_entries(data: bytes) -> dict[str, tuple[int, int]]:
    """Map entry name (country_code / geosite tag) to (start, end) payload offsets."""
    index: dict[str, tuple[int, int]] = {}
    pos = 0
    size = len(data)
    while pos < size:
        key, pos = _varint(data, pos)
        field_number = key >> 3
        wire_type = key & 0x07
        if wire_type != 2:
            pos = _skip(data, pos, wire_type)
            continue
        length, pos = _varint(data, pos)
        payload_start = pos
        payload_end = pos + length
        if field_number == 1:
            index[_first_string(data, payload_start, payload_end).upper()] = (
                payload_start,
                payload_end,
            )
        pos = payload_end
    return index


def _first_string(data: bytes, start: int, end: int) -> str:
    for field_number, wire_type, value in _iter_fields(data, start, end):
        if field_number == 1 and wire_type == 2:
            return bytes(value).decode("utf-8", "replace")
        break
    return ""


def _asset_candidates(name: str) -> list[Path]:
    candidates: list[Path] = []
    specific = os.environ.get("GEOSITE_PATH" if name == "geosite.dat" else "GEOIP_PATH")
    if specific:
        candidates.append(Path(specific))
    asset_dir = os.environ.get("XRAY_LOCATION_ASSET")
    if asset_dir:
        candidates.append(Path(asset_dir) / name)
    xray_bin = os.environ.get("XRAY_BIN")
    if xray_bin:
        candidates.append(Path(xray_bin).expanduser().resolve().parent / name)
    candidates.extend(
        [
            Path("/usr/local/share/xray") / name,
            Path("/usr/share/xray") / name,
            Path("/usr/local/etc/xray") / name,
        ]
    )
    # Development checkout: backend/app/compiler -> parents[3] is the repo root.
    here = Path(__file__).resolve()
    for parent in list(here.parents)[:4]:
        candidates.append(parent / ".tools" / name)
    return candidates


def asset_path(name: str, explicit: str | os.PathLike[str] | None = None) -> Path | None:
    """Locate a geodata file: explicit path, env overrides, then well-known locations."""
    if explicit is not None:
        path = Path(explicit)
        return path if path.is_file() else None
    for candidate in _asset_candidates(name):
        if candidate.is_file():
            return candidate
    return None


def _asset_bytes(path: Path) -> bytes:
    key = str(path)
    data = _ASSET_BYTES.get(key)
    if data is None:
        data = path.read_bytes()
        _ASSET_BYTES[key] = data
    return data


class GeositeMatcher:
    """Domain matcher for one geosite category (optionally filtered by attributes)."""

    def __init__(self) -> None:
        self._exact: set[str] = set()
        self._suffix: set[str] = set()
        self._regex: list[re.Pattern[str]] = []
        self._plain: list[str] = []

    def match(self, host: str) -> bool:
        if host in self._exact:
            return True
        for suffix in self._suffix:
            if host == suffix or host.endswith("." + suffix):
                return True
        for pattern in self._regex:
            if pattern.search(host):
                return True
        return any(plain in host for plain in self._plain)


def _parse_domain_attributes(attributes: bytes) -> set[str]:
    keys: set[str] = set()
    for field_number, wire_type, value in _iter_fields(attributes, 0, len(attributes)):
        if field_number == 1 and wire_type == 2:
            keys.add(bytes(value).decode("utf-8", "replace"))
    return keys


def geosite_matcher(
    tag: str,
    attributes: tuple[str, ...] = (),
    path: str | os.PathLike[str] | None = None,
) -> GeositeMatcher | None:
    """Return a matcher for a geosite category, or None when the data is unavailable."""
    asset = asset_path("geosite.dat", path)
    if asset is None:
        return None
    key = (str(asset), tag.upper(), tuple(attributes))
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    data = _asset_bytes(asset)
    entry = _index_entries(data).get(tag.upper())
    if entry is None:
        return None
    matcher = GeositeMatcher()
    required = set(attributes)
    for field_number, wire_type, value in _iter_fields(data, entry[0], entry[1]):
        if field_number != 2 or wire_type != 2:
            continue
        domain_type = _TYPE_PLAIN
        domain_value = ""
        attrs: set[str] = set()
        for inner_number, inner_wire, inner_value in _iter_fields(value, 0, len(value)):
            if inner_number == 1 and inner_wire == 0:
                domain_type = int(inner_value)
            elif inner_number == 2 and inner_wire == 2:
                domain_value = bytes(inner_value).decode("utf-8", "replace").lower()
            elif inner_number == 3 and inner_wire == 2:
                attrs |= _parse_domain_attributes(bytes(inner_value))
        if required and not required.issubset(attrs):
            continue
        if not domain_value:
            continue
        if domain_type == _TYPE_FULL:
            matcher._exact.add(domain_value)
        elif domain_type == _TYPE_DOMAIN:
            matcher._suffix.add(domain_value)
        elif domain_type == _TYPE_REGEX:
            try:
                matcher._regex.append(re.compile(domain_value))
            except re.error:
                continue
        else:
            matcher._plain.append(domain_value)
    _CACHE[key] = matcher
    return matcher


class GeoIPMatcher:
    """IPv4/IPv6 range matcher for one geoip country code."""

    def __init__(self) -> None:
        self._ranges_v4: list[tuple[int, int]] = []
        self._ranges_v6: list[tuple[int, int]] = []

    def match(self, ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
        ranges = self._ranges_v4 if isinstance(ip, ipaddress.IPv4Address) else self._ranges_v6
        value = int(ip)
        low, high = 0, len(ranges) - 1
        while low <= high:
            middle = (low + high) // 2
            start, end = ranges[middle]
            if value < start:
                high = middle - 1
            elif value > end:
                low = middle + 1
            else:
                return True
        return False


def _range_of(raw_ip: bytes, prefix: int) -> tuple[int, int]:
    if len(raw_ip) == 4:
        network: ipaddress.IPv4Network | ipaddress.IPv6Network = ipaddress.IPv4Network(
            (ipaddress.IPv4Address(raw_ip), prefix), strict=False
        )
    else:
        network = ipaddress.IPv6Network((ipaddress.IPv6Address(raw_ip), prefix), strict=False)
    return int(network.network_address), int(network.broadcast_address)


def geoip_matcher(
    country: str,
    path: str | os.PathLike[str] | None = None,
) -> GeoIPMatcher | None:
    """Return a matcher for a geoip country code (GeoIPList), or None without data."""
    asset = asset_path("geoip.dat", path)
    if asset is None:
        return None
    key = (str(asset), country.upper(), ())
    cached = _CACHE.get(key)
    if cached is not None:
        return cached
    data = _asset_bytes(asset)
    entry = _index_entries(data).get(country.upper())
    if entry is None:
        return None
    matcher = GeoIPMatcher()
    for field_number, wire_type, value in _iter_fields(data, entry[0], entry[1]):
        if field_number != 2 or wire_type != 2:
            continue
        raw_ip = b""
        prefix = 32
        for inner_number, inner_wire, inner_value in _iter_fields(value, 0, len(value)):
            if inner_number == 1 and inner_wire == 2:
                raw_ip = bytes(inner_value)
            elif inner_number == 2 and inner_wire == 0:
                prefix = int(inner_value)
        if len(raw_ip) in (4, 16):
            start, end = _range_of(raw_ip, prefix)
            if len(raw_ip) == 4:
                matcher._ranges_v4.append((start, end))
            else:
                matcher._ranges_v6.append((start, end))
    matcher._ranges_v4.sort()
    matcher._ranges_v6.sort()
    _CACHE[key] = matcher
    return matcher


def clear_cache() -> None:
    """Drop cached matchers and asset bytes (used by tests when assets change)."""
    _CACHE.clear()
    _ASSET_BYTES.clear()
