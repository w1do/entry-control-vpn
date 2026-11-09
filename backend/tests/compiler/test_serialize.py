"""Canonical serialisation: deterministic bytes and stable hashes."""

from __future__ import annotations

import hashlib

import pytest

from app.compiler import canonical_json, loads_config, sha256_json


def test_canonical_json_sorts_keys_and_is_compact() -> None:
    assert canonical_json({"b": 1, "a": {"z": [2, 1], "y": None}}) == (
        '{"a":{"y":null,"z":[2,1]},"b":1}'
    )


def test_canonical_json_keeps_utf8_text() -> None:
    assert canonical_json({"привет": "мир"}) == '{"привет":"мир"}'


def test_canonical_json_preserves_list_order() -> None:
    assert canonical_json([3, 1, 2]) == "[3,1,2]"


def test_sha256_json_matches_contract_definition() -> None:
    obj = {"tag": "exit-sg-1", "port": 18443}
    expected = hashlib.sha256(canonical_json(obj).encode()).hexdigest()
    assert sha256_json(obj) == expected
    assert len(sha256_json(obj)) == 64


def test_sha256_json_is_independent_of_insertion_order() -> None:
    first = {"b": 2, "a": 1, "c": [3]}
    second = {"c": [3], "a": 1, "b": 2}
    assert canonical_json(first) == canonical_json(second)
    assert sha256_json(first) == sha256_json(second)


def test_known_sha256_vector() -> None:
    # sha256 of the canonical JSON for the empty object "{}" (json.dumps({}, separators)).
    assert sha256_json({}) == hashlib.sha256(b"{}").hexdigest()


def test_loads_config_round_trip() -> None:
    config = {"a": [1, {"b": "кириллица"}], "c": None}
    assert loads_config(canonical_json(config)) == config


def test_loads_config_rejects_non_object() -> None:
    with pytest.raises(ValueError):
        loads_config("[1,2,3]")
