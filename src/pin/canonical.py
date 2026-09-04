"""Canonical encoding and content addressing for pin/1.

artifact_id and job_id are SHA-256 of RFC-8785-ish canonical JSON
(sorted keys, no insignificant whitespace, UTF-8). Field 1 of a Flop
session request carries artifact_id, not a raw weights hash.
"""

from __future__ import annotations

import hashlib
import json
from typing import Any

HASH_BYTES = 32


def canonical_dumps(obj: Any) -> bytes:
    """Deterministic UTF-8 JSON. Dicts are sorted; no extra whitespace."""
    return json.dumps(
        obj,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def pin_digest(data: bytes) -> bytes:
    return hashlib.sha256(data).digest()


def pin_hash_hex(obj: Any) -> str:
    return pin_digest(canonical_dumps(obj)).hex()


def hex32(data: bytes) -> str:
    if len(data) != HASH_BYTES:
        raise ValueError(f"expected {HASH_BYTES} bytes, got {len(data)}")
    return data.hex()


def unhex32(value: str) -> bytes:
    raw = bytes.fromhex(value)
    if len(raw) != HASH_BYTES:
        raise ValueError(f"expected {HASH_BYTES} bytes, got {len(raw)}")
    return raw
