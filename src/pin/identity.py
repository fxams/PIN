"""Persistent PIN operator / node identity.

The published operator DID is public. The Ed25519 seed is never part of this
package. Store it in `.pin/identity.json` (mode 0600) or `PIN_SIGNING_KEY`.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass
from pathlib import Path

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pin.crypto import private_key_from_hex, private_key_hex, public_key_hex
from pin.did import did_from_private, fingerprint

# Public only. The matching seed is not in git.
PIN_OPERATOR_DID = "did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2"
PIN_OPERATOR_FINGERPRINT = "304d8415d5273698"
PIN_OPERATOR_PUBLIC_HEX = "a2bead5b160524f9c81a0379e8a268ffce97a0d7767d71712e53f5a3edda3a31"
PIN_OPERATOR_NOTE_TOKEN = "pin/1:flop-session tclk1:paper"
PIN_OPERATOR_NOTE_NS = "did-30"
PIN_OPERATOR_NOTE_KEY = "4d8415d5273698"
PIN_OPERATOR_ROOM = "pin"
PIN_LEGACY_ROOM = "pin-jobs"
PIN_OWNED_ROOM = "d-pin"
TCLK_OFFERS_ROOM = "tclk-offers"
KIBBLE_ROOM = "kibble"
PIN_SPEC_NOTE_NS = "pin"
PIN_SPEC_NOTE_KEY = "llms"
PIN_SPEC_NOTE_PATH = f"/kv/{PIN_SPEC_NOTE_NS}/{PIN_SPEC_NOTE_KEY}"
PIN_SPEC_NOTE_URL = f"https://technocore.chat{PIN_SPEC_NOTE_PATH}"
PIN_PUBLIC_TOPIC = (
    "PIN pinned inference. Start: tclk-offers job.proto=pin context=<artifact>. "
    f"Spec {PIN_SPEC_NOTE_URL}"
)

ENV_IDENTITY_PATH = "PIN_IDENTITY_PATH"
ENV_SIGNING_KEY = "PIN_SIGNING_KEY"
DEFAULT_RELATIVE = Path(".pin") / "identity.json"


class IdentityError(ValueError):
    pass


@dataclass(frozen=True)
class Identity:
    key: Ed25519PrivateKey
    did: str
    fingerprint: str
    public_hex: str
    source: str

    def public_dict(self) -> dict[str, str]:
        return {
            "did": self.did,
            "fingerprint": self.fingerprint,
            "public_hex": self.public_hex,
            "source": self.source,
            "is_operator": self.did == PIN_OPERATOR_DID,
        }

    def is_operator(self) -> bool:
        return self.did == PIN_OPERATOR_DID


def published_operator() -> dict[str, str]:
    return {
        "did": PIN_OPERATOR_DID,
        "fingerprint": PIN_OPERATOR_FINGERPRINT,
        "public_hex": PIN_OPERATOR_PUBLIC_HEX,
        "note": PIN_OPERATOR_NOTE_TOKEN,
        "note_path": f"/kv/{PIN_OPERATOR_NOTE_NS}/{PIN_OPERATOR_NOTE_KEY}",
        "room": PIN_OPERATOR_ROOM,
        "legacy_room": PIN_LEGACY_ROOM,
        "owned_room": PIN_OWNED_ROOM,
        "money_room": TCLK_OFFERS_ROOM,
        "spec_path": PIN_SPEC_NOTE_PATH,
        "topic": PIN_PUBLIC_TOPIC,
    }


def default_identity_path() -> Path:
    env = os.environ.get(ENV_IDENTITY_PATH)
    if env:
        return Path(env)
    return Path.cwd() / DEFAULT_RELATIVE


def identity_from_seed(seed_hex: str, *, source: str) -> Identity:
    key = private_key_from_hex(seed_hex)
    did = did_from_private(key)
    return Identity(
        key=key,
        did=did,
        fingerprint=fingerprint(did),
        public_hex=public_key_hex(key),
        source=source,
    )


def init_identity(path: Path | None = None) -> Identity:
    dest = path or default_identity_path()
    if dest.exists():
        raise IdentityError(f"identity already exists at {dest}")
    ident = identity_from_seed(_new_seed(), source=str(dest))
    write_identity(dest, ident)
    return load_identity_file(dest)


def _new_seed() -> str:
    from pin.crypto import generate_miner_key

    return private_key_hex(generate_miner_key())


def write_identity(path: Path, ident: Identity) -> None:
    created_parent = not path.parent.exists()
    path.parent.mkdir(parents=True, exist_ok=True)
    if created_parent:
        try:
            os.chmod(path.parent, 0o700)
        except OSError:
            pass
    payload = {
        "v": "pin/1",
        "kind": "ed25519",
        "seed": private_key_hex(ident.key),
        "did": ident.did,
        "fingerprint": ident.fingerprint,
        "public_hex": ident.public_hex,
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
    }
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    os.chmod(path, 0o600)


def load_identity_file(path: Path) -> Identity:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise IdentityError(f"cannot read identity file: {exc}") from exc
    if not isinstance(data, dict) or "seed" not in data:
        raise IdentityError("identity file is missing a seed")
    ident = identity_from_seed(str(data["seed"]), source=str(path))
    stored_did = data.get("did")
    if stored_did and stored_did != ident.did:
        raise IdentityError("identity file did does not match seed")
    return ident


def load_identity(path: Path | None = None) -> Identity | None:
    env_seed = os.environ.get(ENV_SIGNING_KEY)
    if env_seed:
        return identity_from_seed(env_seed.strip(), source=ENV_SIGNING_KEY)
    dest = path or default_identity_path()
    if not dest.exists():
        return None
    return load_identity_file(dest)


def require_identity(path: Path | None = None) -> Identity:
    ident = load_identity(path)
    if ident is None:
        raise IdentityError(
            f"no identity; run `pin identity init` or set {ENV_SIGNING_KEY} / {ENV_IDENTITY_PATH}"
        )
    return ident
