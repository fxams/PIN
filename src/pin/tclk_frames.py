"""tclk/1 frames — byte-compatible with @flop-labs/tclk.

PIN does not invent a second lock protocol. These helpers encode the same
`tclk1 {ascii-canonical-json}` lines flop already posts on `tclk-offers`.
Ids are SHA-256 over `FLOP::tclk::v1|{tag}|{ascii(canonicalJson(fields))}`.
"""

from __future__ import annotations

import hashlib
import json
import re
from typing import Any

TCLK_DOMAIN = "FLOP::tclk::v1"
TCLK_PREFIX = "tclk1 "
MAX_FRAME_CHARS = 4096

DID_RE = re.compile(r"^did:key:z6Mk[1-9A-HJ-NP-Za-km-z]{44}$")
AMOUNT_RE = re.compile(r"^[1-9][0-9]*$")
ASSET_RE = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
RAIL_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,63}$")
NONCE_RE = re.compile(r"^[0-9a-f]{8,64}$")
HEX32_RE = re.compile(r"^0x[0-9a-f]{64}$")
HEX33_RE = re.compile(r"^0x[0-9a-f]{66}$")
JOB_PROTO_RE = re.compile(r"^[a-z0-9][a-z0-9._-]{0,31}$")

FRAME_TYPES = frozenset(
    {"offer", "accept", "lock", "reveal", "refund", "cancel", "receipt", "heartbeat"}
)
LOCK_KINDS = frozenset({"hash", "point"})
ROLES = frozenset({"payer", "payee"})
RECEIPT_OUTCOMES = frozenset({"claimed", "refunded", "cancelled"})

KEYS: dict[str, tuple[frozenset[str], frozenset[str]]] = {
    "offer": (
        frozenset(
            {
                "type",
                "from",
                "role",
                "amount",
                "asset",
                "lock",
                "rails",
                "claimByMs",
                "refundAfterMs",
                "expiresMs",
                "paymentKey",
                "job",
                "nonce",
                "id",
            }
        ),
        frozenset(
            {
                "from",
                "role",
                "amount",
                "asset",
                "lock",
                "rails",
                "claimByMs",
                "refundAfterMs",
                "expiresMs",
                "nonce",
                "id",
            }
        ),
    ),
    "accept": (
        frozenset({"type", "from", "ref", "statement", "contract", "paymentKey", "nonce"}),
        frozenset({"from", "ref", "statement", "contract", "nonce"}),
    ),
    "lock": (
        frozenset({"type", "from", "contract", "rail", "ref", "presig"}),
        frozenset({"from", "contract", "rail", "ref"}),
    ),
    "reveal": (
        frozenset({"type", "from", "contract", "secret"}),
        frozenset({"from", "contract", "secret"}),
    ),
    "refund": (
        frozenset({"type", "from", "contract", "reason"}),
        frozenset({"from", "contract"}),
    ),
    "cancel": (
        frozenset({"type", "from", "contract", "reason"}),
        frozenset({"from", "contract"}),
    ),
    "receipt": (
        frozenset({"type", "from", "contract", "outcome", "rail", "ref"}),
        frozenset({"from", "contract", "outcome"}),
    ),
}

ACCEPT_CORE_KEYS = ("from", "nonce", "paymentKey", "ref", "statement")


class TclkError(ValueError):
    pass


def hex32(data: bytes) -> str:
    if len(data) != 32:
        raise TclkError(f"expected 32 bytes, got {len(data)}")
    return "0x" + data.hex()


def hash_lock_from_preimage(preimage: bytes | str) -> tuple[str, str]:
    raw = bytes.fromhex(preimage[2:]) if isinstance(preimage, str) else preimage
    if len(raw) != 32:
        raise TclkError(f"hash-lock preimage must be 32 bytes, got {len(raw)}")
    secret = hex32(raw)
    return secret, hex32(hashlib.sha256(raw).digest())


def verify_secret(lock: str, statement: str, secret: str) -> bool:
    if lock != "hash":
        return False
    try:
        _, digest = hash_lock_from_preimage(secret)
    except (TclkError, ValueError):
        return False
    return digest == statement.lower()


def drop_undefined(value: Any) -> Any:
    if value is None:
        return None
    if isinstance(value, dict):
        out: dict[str, Any] = {}
        for key, item in value.items():
            cleaned = drop_undefined(item)
            if cleaned is not None:
                out[key] = cleaned
        return out
    if isinstance(value, list):
        return [drop_undefined(item) for item in value]
    return value


def to_ascii(json_text: str) -> str:
    def escape(ch: str) -> str:
        return f"\\u{ord(ch):04x}" if ord(ch) >= 0x80 else ch

    return "".join(escape(ch) for ch in json_text)


def canonical_json(value: Any) -> str:
    cleaned = drop_undefined(value)
    if cleaned is None:
        raise TclkError("canonical json of empty value")
    text = json.dumps(cleaned, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return to_ascii(text)


def tclk_id(tag: str, fields: dict[str, Any]) -> str:
    material = f"{TCLK_DOMAIN}|{tag}|{canonical_json(fields)}"
    return hex32(hashlib.sha256(material.encode("utf-8")).digest())


def normalize_job(job: dict[str, Any] | None) -> dict[str, Any] | None:
    if job is None:
        return None
    proto = str(job.get("proto") or "")
    job_id = str(job.get("id") or "")
    if not JOB_PROTO_RE.match(proto):
        raise TclkError("job.proto required")
    if not job_id:
        raise TclkError("job.id required")
    out: dict[str, Any] = {"id": job_id, "proto": proto}
    context = job.get("context")
    if isinstance(context, str) and context:
        out["context"] = context
    return out


def _require_keys(frame: dict[str, Any]) -> None:
    frame_type = str(frame.get("type") or "")
    if frame_type not in KEYS:
        raise TclkError(f"unknown frame type: {frame_type}")
    allowed, required = KEYS[frame_type]
    extra = set(frame) - allowed
    if extra:
        raise TclkError(f"unknown field on {frame_type}: {sorted(extra)[0]}")
    missing = required - set(frame)
    if missing:
        raise TclkError(f"missing field on {frame_type}: {sorted(missing)[0]}")


def _require_did(value: Any, label: str = "from") -> str:
    if not isinstance(value, str) or not DID_RE.match(value):
        raise TclkError(f"{label} must be a did:key")
    return value


def _require_nonce(value: Any) -> str:
    if not isinstance(value, str) or not NONCE_RE.match(value):
        raise TclkError("nonce must be 8-64 hex chars")
    return value


def _require_ms(value: Any, label: str) -> int:
    if type(value) is not int or value <= 0:
        raise TclkError(f"{label} must be a positive unix-ms integer")
    return value


def validate_frame(frame: dict[str, Any]) -> dict[str, Any]:
    if not isinstance(frame, dict) or frame.get("type") not in FRAME_TYPES:
        raise TclkError("invalid tclk frame")
    _require_keys(frame)
    _require_did(frame.get("from"))
    frame_type = frame["type"]
    if frame_type == "offer":
        if frame.get("role") not in ROLES:
            raise TclkError("role must be payer or payee")
        if not isinstance(frame.get("amount"), str) or not AMOUNT_RE.match(frame["amount"]):
            raise TclkError("amount must be a positive integer string")
        if not isinstance(frame.get("asset"), str) or not ASSET_RE.match(frame["asset"]):
            raise TclkError("asset is malformed")
        if frame.get("lock") not in LOCK_KINDS:
            raise TclkError("lock must be hash or point")
        rails = frame.get("rails")
        if not isinstance(rails, list) or not rails:
            raise TclkError("rails must be a non-empty array")
        for rail in rails:
            if not isinstance(rail, str) or not RAIL_RE.match(rail):
                raise TclkError("rail id is malformed")
        claim_by = _require_ms(frame.get("claimByMs"), "claimByMs")
        refund_after = _require_ms(frame.get("refundAfterMs"), "refundAfterMs")
        _require_ms(frame.get("expiresMs"), "expiresMs")
        if claim_by >= refund_after:
            raise TclkError("claimByMs must be strictly before refundAfterMs")
        if frame.get("job") is not None:
            normalize_job(frame["job"])
        _require_nonce(frame.get("nonce"))
        fields = {key: value for key, value in frame.items() if key != "id"}
        expected = offer_id(fields)
        if frame.get("id") != expected:
            raise TclkError(f"offer id mismatch (expected {expected})")
    elif frame_type == "accept":
        if not HEX32_RE.match(str(frame.get("ref") or "")):
            raise TclkError("accept.ref must be an offer id")
        statement = str(frame.get("statement") or "")
        if not (HEX32_RE.match(statement) or HEX33_RE.match(statement)):
            raise TclkError("statement is malformed")
        if not HEX32_RE.match(str(frame.get("contract") or "")):
            raise TclkError("contract must be a 32-byte hex id")
        _require_nonce(frame.get("nonce"))
    elif frame_type == "lock":
        if not HEX32_RE.match(str(frame.get("contract") or "")):
            raise TclkError("contract must be a 32-byte hex id")
        if not isinstance(frame.get("rail"), str) or not RAIL_RE.match(frame["rail"]):
            raise TclkError("rail id is malformed")
        if not frame.get("ref"):
            raise TclkError("lock.ref required")
    elif frame_type == "reveal":
        if not HEX32_RE.match(str(frame.get("contract") or "")):
            raise TclkError("contract must be a 32-byte hex id")
        if not HEX32_RE.match(str(frame.get("secret") or "")):
            raise TclkError("reveal.secret must be 0x + 32-byte hex")
    elif frame_type in {"refund", "cancel"}:
        if not HEX32_RE.match(str(frame.get("contract") or "")):
            raise TclkError("contract must be a 32-byte hex id")
        if frame.get("reason") is not None and not isinstance(frame["reason"], str):
            raise TclkError("reason must be a string")
    elif frame_type == "receipt":
        if not HEX32_RE.match(str(frame.get("contract") or "")):
            raise TclkError("contract must be a 32-byte hex id")
        if frame.get("outcome") not in RECEIPT_OUTCOMES:
            raise TclkError("outcome must be claimed|refunded|cancelled")
    return frame


def encode_frame(frame: dict[str, Any]) -> str:
    line = TCLK_PREFIX + canonical_json(validate_frame(frame))
    if len(line) > MAX_FRAME_CHARS:
        raise TclkError(f"frame exceeds the {MAX_FRAME_CHARS}-char room-message cap")
    if any(ord(ch) < 0x20 or ord(ch) > 0x7E for ch in line):
        raise TclkError("frame line contains non-printable-ASCII characters")
    return line


def decode_frame(line: str) -> dict[str, Any]:
    if not line.startswith(TCLK_PREFIX):
        raise TclkError("expected tclk1 prefix")
    try:
        data = json.loads(line[len(TCLK_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise TclkError("frame is not valid JSON") from exc
    if not isinstance(data, dict):
        raise TclkError("invalid tclk frame")
    return validate_frame(data)


def offer_id(fields: dict[str, Any]) -> str:
    return tclk_id("offer", {key: value for key, value in fields.items() if key != "id"})


def contract_id(offer: dict[str, Any], accept_core: dict[str, Any]) -> str:
    core = {
        key: accept_core[key]
        for key in ACCEPT_CORE_KEYS
        if key in accept_core and accept_core[key] is not None
    }
    return tclk_id("contract", {"accept": core, "offer": offer})


def make_offer(
    *,
    from_did: str,
    amount: str,
    asset: str,
    lock: str,
    rails: list[str],
    nonce: str,
    expires_ms: int,
    claim_by_ms: int,
    refund_after_ms: int,
    role: str = "payer",
    job: dict[str, Any] | None = None,
    payment_key: str | None = None,
) -> dict[str, Any]:
    fields: dict[str, Any] = {
        "amount": amount,
        "asset": asset,
        "claimByMs": claim_by_ms,
        "expiresMs": expires_ms,
        "from": from_did,
        "lock": lock,
        "nonce": nonce,
        "rails": list(rails),
        "refundAfterMs": refund_after_ms,
        "role": role,
        "type": "offer",
    }
    job_out = normalize_job(job)
    if job_out is not None:
        fields["job"] = job_out
    if payment_key:
        fields["paymentKey"] = payment_key
    return validate_frame({**fields, "id": offer_id(fields)})


def make_accept(
    *,
    offer: dict[str, Any],
    from_did: str,
    statement: str,
    nonce: str,
    payment_key: str | None = None,
) -> dict[str, Any]:
    validate_frame(offer)
    if from_did == offer["from"]:
        raise TclkError("accept.from must differ from offer.from")
    if offer["lock"] == "hash" and not HEX32_RE.match(statement):
        raise TclkError("statement does not fit a hash lock")
    core: dict[str, Any] = {
        "from": from_did,
        "nonce": nonce,
        "ref": offer["id"],
        "statement": statement,
    }
    if payment_key:
        core["paymentKey"] = payment_key
    return validate_frame(
        {
            "type": "accept",
            **core,
            "contract": contract_id(offer, core),
        }
    )


def make_lock(*, contract: str, from_did: str, ref: str, rail: str) -> dict[str, Any]:
    return validate_frame(
        {
            "contract": contract,
            "from": from_did,
            "rail": rail,
            "ref": ref,
            "type": "lock",
        }
    )


def make_reveal(*, contract: str, from_did: str, secret: str) -> dict[str, Any]:
    return validate_frame(
        {
            "contract": contract,
            "from": from_did,
            "secret": secret,
            "type": "reveal",
        }
    )


def make_refund(*, contract: str, from_did: str, reason: str | None = None) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "contract": contract,
        "from": from_did,
        "type": "refund",
    }
    if reason:
        frame["reason"] = reason
    return validate_frame(frame)


def make_receipt(
    *,
    contract: str,
    from_did: str,
    outcome: str,
    rail: str | None = None,
    ref: str | None = None,
) -> dict[str, Any]:
    frame: dict[str, Any] = {
        "contract": contract,
        "from": from_did,
        "outcome": outcome,
        "type": "receipt",
    }
    if rail:
        frame["rail"] = rail
    if ref:
        frame["ref"] = ref
    return validate_frame(frame)


def pin_job(job_id: str) -> dict[str, str]:
    return {"id": job_id, "proto": "pin"}
