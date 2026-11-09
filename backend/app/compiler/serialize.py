"""Canonical JSON serialisation of compiled Xray configs.

The contract fixes the exact serialisation (docs/INTERFACES.md section 3):
sorted keys, no whitespace, non-ASCII kept as-is.  The same input therefore
always produces a byte-identical document and an identical SHA-256, which is
what revisions, diffs and the agent apply checksum rely on.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any


def canonical_json(obj: object) -> str:
    """Serialise obj deterministically: sorted keys, compact separators, UTF-8 text."""
    return json.dumps(obj, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_json(obj: object) -> str:
    """SHA-256 (hex) of the canonical JSON of obj."""
    return hashlib.sha256(canonical_json(obj).encode("utf-8")).hexdigest()


def loads_config(text: str) -> dict[str, Any]:
    """Parse a canonical JSON document back into a dict (helper for revisions/diffs)."""
    parsed = json.loads(text)
    if not isinstance(parsed, dict):
        raise ValueError("config JSON must be an object")
    return parsed
