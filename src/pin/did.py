"""did:key (Ed25519) for Technocore-signed pin1 frames.

Fingerprint convention matches technocore.chat /patterns.md: first 16 hex of
SHA-256 of the full did:key string.
"""

from __future__ import annotations

import hashlib

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

from pin.crypto import generate_miner_key, public_key_hex

B58 = "123456789ABCDEFGHJKLMNPQRSTUVWXYZabcdefghijkmnopqrstuvwxyz"


def b58encode(data: bytes) -> str:
    n = int.from_bytes(data, "big")
    out = ""
    while n > 0:
        n, rem = divmod(n, 58)
        out = B58[rem] + out
    pad = 0
    for byte in data:
        if byte == 0:
            pad += 1
        else:
            break
    return ("1" * pad) + (out or "1")


def did_key_from_public(public_raw: bytes) -> str:
    if len(public_raw) != 32:
        raise ValueError("Ed25519 public key must be 32 bytes")
    return "did:key:z" + b58encode(b"\xed\x01" + public_raw)


def did_from_private(key: Ed25519PrivateKey) -> str:
    raw = key.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
    return did_key_from_public(raw)


def fingerprint(did: str) -> str:
    return hashlib.sha256(did.encode("ascii")).hexdigest()[:16]


def new_agent_identity() -> tuple[Ed25519PrivateKey, str, str]:
    key = generate_miner_key()
    did = did_from_private(key)
    return key, did, fingerprint(did)


def verify_did_matches_key(did: str, public_hex: str) -> bool:
    raw = bytes.fromhex(public_hex)
    return did_key_from_public(raw) == did


def public_hex_from_key(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    return public_key_hex(key)
