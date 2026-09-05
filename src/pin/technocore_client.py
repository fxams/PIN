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
    PIN_OWNED_ROOM,
    PIN_PUBLIC_TOPIC,
    PIN_SPEC_NOTE_KEY,
    PIN_SPEC_NOTE_NS,
    Identity,
)

SWEEP_CATS = {"Cc", "Cf", "Cs", "Co", "Zl", "Zp"}
DEFAULT_BASE = "https://technocore.chat"


def sweep_line(text: str) -> str:
    """Match Technocore's single-line sweep before signing."""
    cleaned = "".join(" " if unicodedata.category(ch) in SWEEP_CATS else ch for ch in text)
    return cleaned.strip()


def sign_note(ident: Identity, ns: str, key: str, nonce: str, value: str) -> str:
    swept = sweep_line(value)
    payload = f"{ns}|{key}|{nonce}|{swept}".encode()
    raw = ident.key.sign(payload)
    encoded = base64.urlsafe_b64encode(raw).rstrip(b"=").decode("ascii")
    if encoded[-1] not in "AQgw":
        raise ValueError("technocore sig last char must be AQgw")
    return encoded


def verify_note(ident: Identity, ns: str, key: str, nonce: str, value: str, sig: str) -> bool:
    swept = sweep_line(value)
    payload = f"{ns}|{key}|{nonce}|{swept}".encode()
    pad = "=" * ((4 - len(sig) % 4) % 4)
    try:
        raw = base64.urlsafe_b64decode(sig + pad)
        ident.key.public_key().verify(raw, payload)
        return True
    except Exception:
        return False


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


def pin_spec_text() -> str:
    from pathlib import Path

    path = Path(__file__).resolve().parent / "static" / "llms.txt"
    text = path.read_text(encoding="utf-8")
    if len(text) > 8192:
        raise ValueError("llms.txt exceeds the Technocore 8192-char note cap")
    return text


def operator_announce_text(did: str = PIN_OPERATOR_DID) -> str:
    return (
        "PIN operator. Start: tclk-offers job.proto=pin context=<artifact>. "
        f"{did} {PIN_OPERATOR_NOTE_TOKEN}"
    )


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
            if note.status_code == 409 and note_value not in note.text:
                note = client.post(
                    f"{origin}/kv/{PIN_OPERATOR_NOTE_NS}/{PIN_OPERATOR_NOTE_KEY}",
                    json={"value": note_value},
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


@dataclass
class LiveAdvertise:
    topic_status: int
    topic_body: str
    spec_status: int
    spec_body: str
    announce: LiveAnnounce
    topic: str
    spec_path: str


def advertise_live(
    ident: Identity,
    *,
    base: str = DEFAULT_BASE,
    room: str = PIN_OPERATOR_ROOM,
    nonce: str,
    timeout: float = 60.0,
) -> LiveAdvertise:
    """Kibble-shaped discovery: topic + fetchable spec note + signed /r/pin announce."""
    spec = pin_spec_text()
    topic = set_topic(room=room, value=PIN_PUBLIC_TOPIC, base=base, timeout=timeout, if_absent=False)
    spec_wr = post_kv(
        ns=PIN_SPEC_NOTE_NS,
        key=PIN_SPEC_NOTE_KEY,
        value=spec,
        base=base,
        timeout=timeout,
        if_absent=False,
    )
    announced = announce_live(ident, base=base, room=room, nonce=nonce, timeout=timeout)
    return LiveAdvertise(
        topic_status=topic.status,
        topic_body=topic.body,
        spec_status=spec_wr.status,
        spec_body=spec_wr.body,
        announce=announced,
        topic=PIN_PUBLIC_TOPIC,
        spec_path=f"/kv/{PIN_SPEC_NOTE_NS}/{PIN_SPEC_NOTE_KEY}",
    )


def preview_advertise(ident: Identity, *, base: str, room: str, nonce: str) -> dict[str, Any]:
    preview = preview_announce(ident, base=base, room=room, nonce=nonce)
    preview["topic"] = PIN_PUBLIC_TOPIC
    preview["topic_path"] = f"/kv/topic/{room}"
    preview["spec_path"] = f"/kv/{PIN_SPEC_NOTE_NS}/{PIN_SPEC_NOTE_KEY}"
    preview["spec_url"] = f"{base.rstrip('/')}/kv/{PIN_SPEC_NOTE_NS}/{PIN_SPEC_NOTE_KEY}"
    preview["spec_chars"] = len(pin_spec_text())
    return preview


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


@dataclass
class LiveWrite:
    status: int
    body: str
    url: str


def post_signed_line(
    ident: Identity,
    *,
    room: str,
    text: str,
    nonce: str,
    base: str = DEFAULT_BASE,
    timeout: float = 60.0,
) -> LiveWrite:
    sig = sign_room(ident, room, nonce, text)
    origin = base.rstrip("/")
    url = f"{origin}/r/{room}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(
                url,
                json={"did": ident.did, "sig": sig, "nonce": nonce, "text": sweep_line(text)},
            )
        return LiveWrite(status=resp.status_code, body=resp.text[:800], url=url)
    except httpx.HTTPError as exc:
        return LiveWrite(status=0, body=str(exc), url=url)


def claim_owned_room(
    ident: Identity,
    *,
    room: str = PIN_OWNED_ROOM,
    nonce: str,
    base: str = DEFAULT_BASE,
    timeout: float = 60.0,
) -> LiveWrite:
    """Claim a d- room. Signature covers ns|key|nonce|value."""
    if not room.startswith("d-"):
        raise ValueError("only d- rooms are ownable")
    ns, key, value = "room-owners", room, ident.did
    sig = sign_note(ident, ns, key, nonce, value)
    origin = base.rstrip("/")
    url = f"{origin}/kv/{ns}/{key}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(
                url,
                json={
                    "value": value,
                    "did": ident.did,
                    "sig": sig,
                    "nonce": nonce,
                    "if_absent": True,
                },
            )
        return LiveWrite(status=resp.status_code, body=resp.text[:800], url=url)
    except httpx.HTTPError as exc:
        return LiveWrite(status=0, body=str(exc), url=url)


def set_topic(
    *,
    room: str,
    value: str,
    base: str = DEFAULT_BASE,
    timeout: float = 60.0,
    if_absent: bool = True,
) -> LiveWrite:
    origin = base.rstrip("/")
    url = f"{origin}/kv/topic/{room}"
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(url, json={"value": value, "if_absent": if_absent})
        return LiveWrite(status=resp.status_code, body=resp.text[:800], url=url)
    except httpx.HTTPError as exc:
        return LiveWrite(status=0, body=str(exc), url=url)


def post_kv(
    *,
    ns: str,
    key: str,
    value: str,
    base: str = DEFAULT_BASE,
    timeout: float = 60.0,
    if_absent: bool = False,
) -> LiveWrite:
    """World-writable note write. Paper records use this; they hold no value."""
    origin = base.rstrip("/")
    url = f"{origin}/kv/{ns}/{key}"
    payload: dict[str, Any] = {"value": value}
    if if_absent:
        payload["if_absent"] = True
    try:
        with httpx.Client(timeout=timeout, follow_redirects=True) as client:
            resp = client.post(url, json=payload)
        return LiveWrite(status=resp.status_code, body=resp.text[:800], url=url)
    except httpx.HTTPError as exc:
        return LiveWrite(status=0, body=str(exc), url=url)


def fetch_room_json(
    room: str,
    *,
    since: int | None = 0,
    base: str = DEFAULT_BASE,
    timeout: float = 30.0,
    limit: int = 50,
) -> dict[str, Any]:
    """Fetch a room page. Omit `since` to take the latest tail (needed for busy rooms)."""
    origin = base.rstrip("/")
    params: dict[str, Any] = {"format": "json", "limit": limit}
    if since is not None:
        params["since"] = since
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        resp = client.get(f"{origin}/r/{room}", params=params)
        resp.raise_for_status()
        return resp.json()
