"""Technocore signed-lane helpers. Live GETs are opt-in; tests stay offline."""

from __future__ import annotations

import base64
import unicodedata
from dataclasses import dataclass
from typing import Any
from urllib.parse import quote

import httpx

from pin.identity import (
    PIN_OPERATOR_DID,
    PIN_OPERATOR_NOTE_KEY,
    PIN_OPERATOR_NOTE_NS,
    PIN_OPERATOR_NOTE_TOKEN,
    PIN_OPERATOR_ROOM,
    Identity,
)

SWEEP_CATS = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
DEFAULT_BASE = "https://technocore.chat"


def sweep_line(text: str) -> str:
    """Match Technocore's single-line sweep before signing."""
    cleaned = "".join(" " if unicodedata.category(ch) in SWEEP_CATS else ch for ch in text)
    return cleaned.strip()


def sign_room(ident: Identity, room: str, nonce: str, text: str) -> str:
    swept = sweep_line(text)
    payload = f"{room}|{nonce}|{swept}".encode()
    raw = ident.key.sign(payload)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if encoded[-1] not in "AQgw":
        raise ValueError("technocore sig last char must be AQgw")
    return encoded


def verify_room(ident: Identity, room: str, nonce: str, text: str, sig: str) -> bool:
    swept = sweep_line(text)
    payload = f"{room}|{nonce}|{swept}".encode()
    pad = "=" * ((4 - len(sig) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(sig + pad)
        ident.key.public_key().verify(raw, payload)
        return True
    except Exception:
        return False


def say_signed_url(base: str, room: str, did: str, sig: str, nonce: str, text: str) -> str:
    return (
        f"{base.rstrip('/')}/r/{quote(room, safe='')}"
        f"/say-signed/{quote(did, safe='')}/{quote(sig, safe='')}"
        f"/{quote(nonce, safe='')}/{quote(sweep_line(text), safe='')}"
    )


def did_note_url(base: str, value: str, *, if_absent: bool = True) -> str:
    url = (
        f"{base.rstrip('/')}/kv/{quote(PIN_OPERATOR_NOTE_NS, safe='')}"
        f"/{quote(PIN_OPERATOR_NOTE_KEY, safe='')}"
        f"/set/{quote(value, safe='')}"
    )
    if if_absent:
        url += "?if_absent=1"
    return url


def operator_note_value(did: str = PIN_OPERATOR_DID) -> str:
    return f"{did} {PIN_OPERATOR_NOTE_TOKEN}"


def operator_announce_text(did: str = PIN_OPERATOR_DID) -> str:
    return f"PIN operator. {did} {PIN_OPERATOR_NOTE_TOKEN}"


@dataclass
class LiveAnnounce:
    note_status: int
    note_body: str
    room_status: int
    room_body: str
    room: str
    nonce: str
    did: str
    text: str


def announce_live(
    ident: Identity,
    *,
    base: str = DEFAULT_BASE,
    room: str = PIN_OPERATOR_ROOM,
    nonce: str,
    timeout: float = 60.0,
) -> LiveAnnounce:
    text = operator_announce_text(ident.did)
    sig = sign_room(ident, room, nonce, text)
    note_value = operator_note_value(ident.did)
    origin = base.rstrip("/")
    note_status, note_body = 0, ""
    room_status, room_body = 0, ""
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        try:
            note = client.post(
                f"{origin}/kv/{PIN_OPERATOR_NOTE_NS}/{PIN_OPERATOR_NOTE_KEY}",
                json={"value": note_value, "if_absent": True},
            )
            note_status, note_body = note.status_code, note.text[:500]
        except httpx.HTTPError as exc:
            note_status, note_body = 0, f"note write failed: {exc}"
        try:
            said = client.post(
                f"{origin}/r/{room}",
                json={"did": ident.did, "sig": sig, "nonce": nonce, "text": text},
            )
            room_status, room_body = said.status_code, said.text[:500]
        except httpx.HTTPError as exc:
            room_status, room_body = 0, f"room write failed: {exc}"
    return LiveAnnounce(
        note_status=note_status,
        note_body=note_body,
        room_status=room_status,
        room_body=room_body,
        room=room,
        nonce=nonce,
        did=ident.did,
        text=text,
    )


def preview_announce(ident: Identity, *, base: str, room: str, nonce: str) -> dict[str, Any]:
    text = operator_announce_text(ident.did)
    sig = sign_room(ident, room, nonce, text)
    note_value = operator_note_value(ident.did)
    return {
        "did": ident.did,
        "fingerprint": ident.fingerprint,
        "room": room,
        "nonce": nonce,
        "text": text,
        "sig": sig,
        "say_url": say_signed_url(base, room, ident.did, sig, nonce, text),
        "note_url": did_note_url(base, note_value, if_absent=True),
        "note_value": note_value,
    }
