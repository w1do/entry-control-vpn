"""Transport variants: compiler inbounds and per-variant connection links."""

from __future__ import annotations

from urllib.parse import parse_qs, urlparse

from app.compiler.transports import VARIANTS
from app.models.routing import Client
from app.models.servers import Server, ServerSecret
from app.services.links import build_connection_variants


def _server() -> Server:
    return Server(id=1, name="nl", host="203.0.113.5", roles="both")


def _secret() -> ServerSecret:
    return ServerSecret(
        server_id=1,
        reality_public_key="PUBKEY",
        short_ids_json=["abcd1234"],
        real_dest="example.com",
        real_sni="example.com",
    )


def _client() -> Client:
    return Client(
        id=1,
        name="den",
        uuid="11111111-2222-3333-4444-555555555555",
        email_tag="client-1@vpn",
        sub_token="token",
    )


def test_all_variants_have_unique_ports_and_expected_types() -> None:
    variants = build_connection_variants(_server(), _secret(), _client())
    assert len(variants) == len(VARIANTS) == 4
    assert [item["port"] for item in variants] == [443, 2096, 2053, 8443]
    assert [item["type"] for item in variants] == ["tcp", "tcp", "xhttp", "grpc"]
    assert {item["id"] for item in variants} == {"tcp-vision", "tcp", "xhttp", "grpc"}


def test_links_include_transport_specific_query() -> None:
    variants = {item["id"]: item for item in build_connection_variants(_server(), _secret(), _client())}
    for item in variants.values():
        url = str(item["url"])
        assert url.startswith("vless://11111111-2222-3333-4444-555555555555@203.0.113.5:")
        query = parse_qs(urlparse(url).query)
        assert query["type"] == [str(item["type"])]
        assert query["security"] == ["reality"]
        assert query["sni"] == ["example.com"]
        assert query["pbk"] == ["PUBKEY"]
        assert query["sid"] == ["abcd1234"]

    assert parse_qs(urlparse(str(variants["tcp-vision"]["url"])).query)["flow"] == [
        "xtls-rprx-vision"
    ]
    assert "flow" not in parse_qs(urlparse(str(variants["tcp"]["url"])).query)
    assert parse_qs(urlparse(str(variants["xhttp"]["url"])).query)["path"] == ["/vpn-xhttp"]
    assert parse_qs(urlparse(str(variants["xhttp"]["url"])).query)["mode"] == ["auto"]
    assert parse_qs(urlparse(str(variants["grpc"]["url"])).query)["serviceName"] == ["vpn-grpc"]
    assert parse_qs(urlparse(str(variants["grpc"]["url"])).query)["mode"] == ["gun"]
