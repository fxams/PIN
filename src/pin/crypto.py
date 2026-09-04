"""Ed25519 helpers for miner leaf-0 signatures."""

from __future__ import annotations

from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import (
    Encoding,
    NoEncryption,
    PrivateFormat,
    PublicFormat,
)


def generate_miner_key() -> Ed25519PrivateKey:
    return Ed25519PrivateKey.generate()


def private_key_hex(key: Ed25519PrivateKey) -> str:
    return key.private_bytes(Encoding.Raw, PrivateFormat.Raw, NoEncryption()).hex()


def public_key_hex(key: Ed25519PrivateKey | Ed25519PublicKey) -> str:
    if isinstance(key, Ed25519PrivateKey):
        key = key.public_key()
    return key.public_bytes(Encoding.Raw, PublicFormat.Raw).hex()


def public_key_from_hex(value: str) -> Ed25519PublicKey:
    return Ed25519PublicKey.from_public_bytes(bytes.fromhex(value))


def sign(key: Ed25519PrivateKey, preimage: bytes) -> str:
    return key.sign(preimage).hex()


def verify(public_hex: str, preimage: bytes, signature_hex: str) -> bool:
    try:
        public_key_from_hex(public_hex).verify(bytes.fromhex(signature_hex), preimage)
        return True
    except Exception:
        return False
