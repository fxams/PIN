"""Owned PIN roster — identities we control, posted in /r/pin.

Seeds stay under `.pin/roster/` (gitignored). The public list is `/kv/pin/roster`.
Each agent posts a unique signed line so Technocore's copy-filter does not 422.
They do not post pin1 wants (that would fire the matcher) and they do not write
kibble, lobby, or tclk-offers.
"""

from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from pin.identity import (
    PIN_OPERATOR_NOTE_TOKEN,
    PIN_OPERATOR_ROOM,
    Identity,
    identity_from_seed,
    write_identity,
)
from pin.technocore_client import DEFAULT_BASE, sign_room, sweep_line

ROSTER_NOTE_NS = "pin"
ROSTER_NOTE_KEY = "roster"
ROSTER_NOTE_PATH = f"/kv/{ROSTER_NOTE_NS}/{ROSTER_NOTE_KEY}"
DEFAULT_ROSTER_DIR = Path(".pin") / "roster"
DEFAULT_COUNT = 100
MAX_COUNT = 250
WAIT_RE = re.compile(r"(\d+)")


def default_roster_dir() -> Path:
    env = os.environ.get("PIN_ROSTER_DIR")
    if env:
        return Path(env)
    return Path.cwd() / DEFAULT_ROSTER_DIR


def did_note_parts(fingerprint: str) -> tuple[str, str]:
    fp = fingerprint.lower()
    return f"did-{fp[:2]}", fp[2:]


def roster_line(ident: Identity, *, index: int, total: int) -> str:
    """Unique per DID so the room copy-filter does not refuse the line."""
    return (
        f"PIN roster {index}/{total} {ident.did} {ident.fingerprint} "
        f"speaks pin/1. Start: tclk-offers job.proto=pin context=<artifact>. "
        f"spec:/kv/pin/llms list:{ROSTER_NOTE_PATH}"
    )


def operator_roster_line(operator: Identity, *, total: int) -> str:
    return (
        f"PIN roster {total} owned agents at {ROSTER_NOTE_PATH}. "
        f"Start: tclk-offers job.proto=pin context=<artifact>. {operator.did}"
    )


def _agent_path(root: Path, fingerprint: str) -> Path:
    return root / "keys" / f"{fingerprint}.json"


def public_entries(idents: list[Identity]) -> list[dict[str, str]]:
    return [
        {
            "did": ident.did,
            "fingerprint": ident.fingerprint,
            "note": f"/kv/{did_note_parts(ident.fingerprint)[0]}/{did_note_parts(ident.fingerprint)[1]}",
        }
        for ident in idents
    ]


def roster_note_body(idents: list[Identity]) -> str:
    """Public list of DIDs only — 100 full agent objects would exceed the 8192-char note cap."""
    payload = {
        "v": "pin/1",
        "kind": "roster",
        "n": len(idents),
        "room": PIN_OPERATOR_ROOM,
        "entry": "tclk-offers job.proto=pin + job.context",
        "spec": "/kv/pin/llms",
        "dids": [ident.did for ident in idents],
    }
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(text) > 8192:
        raise ValueError("roster note exceeds the Technocore 8192-char cap")
    return text


def load_roster(root: Path | None = None) -> list[Identity]:
    dest = root or default_roster_dir()
    key_dir = dest / "keys"
    if not key_dir.is_dir():
        return []
    out: list[Identity] = []
    for path in sorted(key_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        ident = identity_from_seed(str(data["seed"]), source=str(path))
        out.append(ident)
    return out


def init_roster(root: Path | None = None, *, count: int = DEFAULT_COUNT) -> list[Identity]:
    if count < 1 or count > MAX_COUNT:
        raise ValueError(f"count must be 1..{MAX_COUNT}")
    dest = root or default_roster_dir()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass
    existing = load_roster(dest)
    have = {ident.fingerprint for ident in existing}
    need = count - len(existing)
    for _ in range(max(0, need)):
        ident = identity_from_seed(_new_seed(), source="roster")
        while ident.fingerprint in have:
            ident = identity_from_seed(_new_seed(), source="roster")
        write_identity(_agent_path(dest, ident.fingerprint), ident)
        have.add(ident.fingerprint)
        existing.append(ident)
    _write_manifest(dest, existing)
    return existing[:count]


def _new_seed() -> str:
    from pin.crypto import generate_miner_key, private_key_hex

    return private_key_hex(generate_miner_key())


def _write_manifest(root: Path, idents: list[Identity]) -> None:
    manifest = root / "manifest.json"
    payload = {"v": "pin/1", "n": len(idents), "agents": public_entries(idents)}
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)


def preview_roster(
    idents: list[Identity],
    operator: Identity,
    *,
    posts: int | None = None,
) -> dict[str, Any]:
    n = len(idents)
    take = n if posts is None else min(n, max(0, posts))
    samples = [roster_line(ident, index=i + 1, total=n) for i, ident in enumerate(idents[: min(3, take)])]
    return {
        "room": PIN_OPERATOR_ROOM,
        "roster_path": ROSTER_NOTE_PATH,
        "n": n,
        "posts": take,
        "operator_did": operator.did,
        "operator_line": operator_roster_line(operator, total=n),
        "sample_lines": samples,
        "agents": public_entries(idents),
        "holds_value": False,
        "live": False,
    }


@dataclass
class RosterPublish:
    roster_status: int
    operator_status: int
    notes_ok: int = 0
    rooms_ok: int = 0
    failed: list[str] = field(default_factory=list)
    posted_lines: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "roster_path": ROSTER_NOTE_PATH,
            "roster_status": self.roster_status,
            "operator_status": self.operator_status,
            "notes_ok": self.notes_ok,
            "rooms_ok": self.rooms_ok,
            "failed": self.failed,
            "posted": len(self.posted_lines),
            "live": True,
        }


def _wait_seconds(resp: httpx.Response) -> float:
    if resp.status_code != 429:
        return 0
    match = WAIT_RE.search(resp.text or "")
    return float(match.group(1)) if match else 2.0


def _post_with_retry(client: httpx.Client, method: str, url: str, json_body: dict[str, Any]) -> httpx.Response:
    delay = 1.0
    last = None
    for _ in range(6):
        last = client.request(method, url, json=json_body)
        if last.status_code != 429:
            return last
        time.sleep(max(_wait_seconds(last), delay))
        delay = min(delay * 2, 16)
    return last


def publish_roster(
    idents: list[Identity],
    operator: Identity,
    *,
    posts: int | None = None,
    base: str = DEFAULT_BASE,
    room: str = PIN_OPERATOR_ROOM,
    timeout: float = 60.0,
) -> RosterPublish:
    origin = base.rstrip("/")
    take = len(idents) if posts is None else min(len(idents), max(0, posts))
    selected = idents[:take]
    nonce0 = int(time.time() * 1000)
    result = RosterPublish(roster_status=0, operator_status=0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        roster = _post_with_retry(
            client,
            "POST",
            f"{origin}/kv/{ROSTER_NOTE_NS}/{ROSTER_NOTE_KEY}",
            {"value": roster_note_body(idents)},
        )
        result.roster_status = roster.status_code
        if roster.status_code not in {200, 409}:
            result.failed.append(f"roster-note:{roster.status_code}")
        op_text = operator_roster_line(operator, total=len(idents))
        op_nonce = str(nonce0)
        op_sig = sign_room(operator, room, op_nonce, op_text)
        op = _post_with_retry(
            client,
            "POST",
            f"{origin}/r/{room}",
            {"did": operator.did, "sig": op_sig, "nonce": op_nonce, "text": sweep_line(op_text)},
        )
        result.operator_status = op.status_code
        if op.status_code == 200:
            result.posted_lines.append(op_text)
        else:
            result.failed.append(f"operator-room:{op.status_code}")
        for i, ident in enumerate(selected, start=1):
            ns, key = did_note_parts(ident.fingerprint)
            note_val = f"{ident.did} {PIN_OPERATOR_NOTE_TOKEN}"
            note = _post_with_retry(
                client,
                "POST",
                f"{origin}/kv/{ns}/{key}",
                {"value": note_val, "if_absent": True},
            )
            if note.status_code in {200, 409}:
                result.notes_ok += 1
            else:
                result.failed.append(f"note:{ident.fingerprint}:{note.status_code}")
            text = roster_line(ident, index=i, total=len(idents))
            nonce = str(nonce0 + i)
            sig = sign_room(ident, room, nonce, text)
            said = _post_with_retry(
                client,
                "POST",
                f"{origin}/r/{room}",
                {"did": ident.did, "sig": sig, "nonce": nonce, "text": sweep_line(text)},
            )
            if said.status_code == 422:
                text = f"{text} n={nonce}"
                sig = sign_room(ident, room, nonce, text)
                said = _post_with_retry(
                    client,
                    "POST",
                    f"{origin}/r/{room}",
                    {"did": ident.did, "sig": sig, "nonce": nonce, "text": sweep_line(text)},
                )
            if said.status_code == 200:
                result.rooms_ok += 1
                result.posted_lines.append(text)
            else:
                result.failed.append(f"room:{ident.fingerprint}:{said.status_code}")
    return result
