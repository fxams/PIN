"""Owned PIN market — artificial buyers and sellers we control.

Buyers post `tclk1` paper offers on `tclk-offers` (`job.proto=pin` + context).
Sellers post `pin1` quotes on `/r/pin` for those offers. Seeds stay under
`.pin/roster/` (gitignored). Public book is `/kv/pin/roster`.

They do not post `pin1 want` (the tclk offer is the want), do not fill jobs
(the operator matcher does that), and do not write kibble or lobby.
"""

from __future__ import annotations

import json
import os
import re
import secrets
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx

from pin.frames import Pin1Frame, encode_frame
from pin.identity import (
    PIN_OPERATOR_ROOM,
    TCLK_OFFERS_ROOM,
    Identity,
    identity_from_seed,
    write_identity,
)
from pin.lab import PinLab
from pin.models import QuoteRequest, SlaClass, Tier
from pin.tclk_entry import build_pin_bounty
from pin.tclk_frames import encode_frame as encode_tclk
from pin.technocore_client import DEFAULT_BASE, sign_room, sweep_line

ROSTER_NOTE_NS = "pin"
ROSTER_NOTE_KEY = "roster"
ROSTER_NOTE_PATH = f"/kv/{ROSTER_NOTE_NS}/{ROSTER_NOTE_KEY}"
DEFAULT_ROSTER_DIR = Path(".pin") / "roster"
DEFAULT_BUYERS = 50
DEFAULT_SELLERS = 50
MAX_SIDE = 125
WAIT_RE = re.compile(r"(\d+)")
ROLES = frozenset({"buyer", "seller"})
ARTIFACT_KEYS = ("8b-stock", "70b-stock")
PROGRESS_NAME = "publish.json"


@dataclass
class RosterAgent:
    ident: Identity
    role: str

    @property
    def did(self) -> str:
        return self.ident.did

    @property
    def fingerprint(self) -> str:
        return self.ident.fingerprint


def default_roster_dir() -> Path:
    env = os.environ.get("PIN_ROSTER_DIR")
    if env:
        return Path(env)
    return Path.cwd() / DEFAULT_ROSTER_DIR


def did_note_parts(fingerprint: str) -> tuple[str, str]:
    fp = fingerprint.lower()
    return f"did-{fp[:2]}", fp[2:]


def note_token(role: str) -> str:
    return f"pin/1:{role} tclk1:paper spec:/kv/pin/llms"


def operator_roster_line(operator: Identity, *, buyers: int, sellers: int) -> str:
    return (
        f"PIN market {buyers} buyers / {sellers} sellers at {ROSTER_NOTE_PATH}. "
        f"Buyers: tclk-offers job.proto=pin. Sellers: pin1 quote on /r/pin. {operator.did}"
    )


def _agent_path(root: Path, fingerprint: str) -> Path:
    return root / "keys" / f"{fingerprint}.json"


def _roles_path(root: Path) -> Path:
    return root / "roles.json"


def public_entries(agents: list[RosterAgent]) -> list[dict[str, str]]:
    return [
        {
            "did": agent.did,
            "fingerprint": agent.fingerprint,
            "role": agent.role,
            "note": f"/kv/{did_note_parts(agent.fingerprint)[0]}/{did_note_parts(agent.fingerprint)[1]}",
        }
        for agent in agents
    ]


def roster_note_body(agents: list[RosterAgent]) -> str:
    buyers = [a.did for a in agents if a.role == "buyer"]
    sellers = [a.did for a in agents if a.role == "seller"]
    payload = {
        "v": "pin/1",
        "kind": "roster",
        "buyers": buyers,
        "sellers": sellers,
        "n_buyers": len(buyers),
        "n_sellers": len(sellers),
        "room": PIN_OPERATOR_ROOM,
        "money_room": TCLK_OFFERS_ROOM,
        "entry": "tclk-offers job.proto=pin + job.context",
        "spec": "/kv/pin/llms",
    }
    text = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    if len(text) > 8192:
        raise ValueError("roster note exceeds the Technocore 8192-char cap")
    return text


def _load_roles(root: Path) -> dict[str, str]:
    path = _roles_path(root)
    if not path.exists():
        return {}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {}
    return {str(k): str(v) for k, v in data.items() if v in ROLES}


def _write_roles(root: Path, agents: list[RosterAgent]) -> None:
    payload = {agent.fingerprint: agent.role for agent in agents}
    dest = _roles_path(root)
    dest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(dest, 0o600)


def load_roster(root: Path | None = None) -> list[RosterAgent]:
    dest = root or default_roster_dir()
    key_dir = dest / "keys"
    if not key_dir.is_dir():
        return []
    roles = _load_roles(dest)
    idents: list[Identity] = []
    for path in sorted(key_dir.glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        idents.append(identity_from_seed(str(data["seed"]), source=str(path)))
    assigned = _apply_roles(idents, roles)
    return assigned


def _apply_roles(
    idents: list[Identity],
    roles: dict[str, str],
    *,
    buyers: int | None = None,
    sellers: int | None = None,
) -> list[RosterAgent]:
    agents: list[RosterAgent] = []
    unknown = [ident for ident in idents if ident.fingerprint not in roles]
    known = [ident for ident in idents if ident.fingerprint in roles]
    agents.extend(RosterAgent(ident, roles[ident.fingerprint]) for ident in known)
    n_buyers = sum(1 for a in agents if a.role == "buyer")
    n_sellers = sum(1 for a in agents if a.role == "seller")
    want_buyers = buyers if buyers is not None else max(DEFAULT_BUYERS, n_buyers)
    want_sellers = sellers if sellers is not None else max(DEFAULT_SELLERS, n_sellers)
    for ident in unknown:
        if n_buyers < want_buyers:
            agents.append(RosterAgent(ident, "buyer"))
            n_buyers += 1
        elif n_sellers < want_sellers:
            agents.append(RosterAgent(ident, "seller"))
            n_sellers += 1
        else:
            agents.append(RosterAgent(ident, "seller"))
            n_sellers += 1
    return agents


def init_roster(
    root: Path | None = None,
    *,
    buyers: int = DEFAULT_BUYERS,
    sellers: int = DEFAULT_SELLERS,
) -> list[RosterAgent]:
    if buyers < 0 or sellers < 0 or buyers > MAX_SIDE or sellers > MAX_SIDE:
        raise ValueError(f"buyers and sellers must be 0..{MAX_SIDE}")
    if buyers + sellers < 1:
        raise ValueError("need at least one agent")
    dest = root or default_roster_dir()
    dest.mkdir(parents=True, exist_ok=True)
    try:
        os.chmod(dest, 0o700)
    except OSError:
        pass
    existing = load_roster(dest)
    have = {agent.fingerprint for agent in existing}
    need = buyers + sellers - len(existing)
    for _ in range(max(0, need)):
        ident = identity_from_seed(_new_seed(), source="roster")
        while ident.fingerprint in have:
            ident = identity_from_seed(_new_seed(), source="roster")
        write_identity(_agent_path(dest, ident.fingerprint), ident)
        have.add(ident.fingerprint)
    idents = []
    for path in sorted((dest / "keys").glob("*.json")):
        data = json.loads(path.read_text(encoding="utf-8"))
        idents.append(identity_from_seed(str(data["seed"]), source=str(path)))
    roles = _load_roles(dest)
    agents = _apply_roles(idents, roles, buyers=buyers, sellers=sellers)
    _write_roles(dest, agents)
    _write_manifest(dest, agents)
    picked = [a for a in agents if a.role == "buyer"][:buyers] + [a for a in agents if a.role == "seller"][:sellers]
    return picked


def _new_seed() -> str:
    from pin.crypto import generate_miner_key, private_key_hex

    return private_key_hex(generate_miner_key())


def _write_manifest(root: Path, agents: list[RosterAgent]) -> None:
    manifest = root / "manifest.json"
    payload = {
        "v": "pin/1",
        "n": len(agents),
        "n_buyers": sum(1 for a in agents if a.role == "buyer"),
        "n_sellers": sum(1 for a in agents if a.role == "seller"),
        "agents": public_entries(agents),
    }
    manifest.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.chmod(manifest, 0o600)


def pair_book(agents: list[RosterAgent], *, pairs: int | None = None) -> list[tuple[RosterAgent, RosterAgent]]:
    buyers = [a for a in agents if a.role == "buyer"]
    sellers = [a for a in agents if a.role == "seller"]
    n = min(len(buyers), len(sellers))
    if pairs is not None:
        n = min(n, max(0, pairs))
    return list(zip(buyers[:n], sellers[:n], strict=False))


def build_pair_frames(
    buyer: RosterAgent,
    seller: RosterAgent,
    *,
    lab: PinLab | None = None,
    artifact_key: str = "8b-stock",
    now_ms: int | None = None,
    offer_nonce: str | None = None,
) -> tuple[str, str, dict[str, Any]]:
    """Return (tclk offer line, pin1 quote line, offer dict). Paper holds no value."""
    lab = lab or PinLab()
    artifact = lab.named_artifacts[artifact_key]
    offer = build_pin_bounty(
        from_did=buyer.did,
        context=artifact.artifact_id,
        amount="100",
        now_ms=now_ms,
        nonce=offer_nonce or secrets.token_hex(8),
    )
    quote = lab.make_quote(
        QuoteRequest(
            artifact_id=artifact.artifact_id,
            sla_class=SlaClass.INTERACTIVE,
            tier=Tier.T1,
            n_in=32,
            n_out=48,
        )
    )
    quote_line = encode_frame(
        Pin1Frame(
            type="quote",
            from_did=seller.did,
            nonce=secrets.token_hex(8),
            artifact_id=artifact.artifact_id,
            ref=str(offer["id"]),
            offer_id=quote.offer_id,
            usd_micros=quote.usd_micros,
            flop_fee=quote.flop_fee,
            ttl_sec=quote.ttl_sec,
            rail="paper",
            tclk_ref=str(offer["id"]),
        )
    )
    return encode_tclk(offer), quote_line, offer


def preview_roster(
    agents: list[RosterAgent],
    operator: Identity,
    *,
    pairs: int | None = None,
) -> dict[str, Any]:
    book = pair_book(agents, pairs=pairs)
    samples: list[dict[str, str]] = []
    if book:
        offer_line, quote_line, offer = build_pair_frames(*book[0], offer_nonce="cafebabedead0001")
        samples.append(
            {
                "buyer": book[0][0].did,
                "seller": book[0][1].did,
                "offer_id": str(offer["id"]),
                "tclk_line": offer_line,
                "pin_quote": quote_line,
            }
        )
    n_buyers = sum(1 for a in agents if a.role == "buyer")
    n_sellers = sum(1 for a in agents if a.role == "seller")
    return {
        "pin_room": PIN_OPERATOR_ROOM,
        "money_room": TCLK_OFFERS_ROOM,
        "roster_path": ROSTER_NOTE_PATH,
        "n": len(agents),
        "n_buyers": n_buyers,
        "n_sellers": n_sellers,
        "pairs": len(book),
        "operator_did": operator.did,
        "operator_line": operator_roster_line(operator, buyers=n_buyers, sellers=n_sellers),
        "sample": samples,
        "agents": public_entries(agents),
        "holds_value": False,
        "live": False,
    }


@dataclass
class RosterPublish:
    roster_status: int
    operator_status: int
    notes_ok: int = 0
    buyer_offers_ok: int = 0
    seller_quotes_ok: int = 0
    skipped_pairs: int = 0
    failed: list[str] = field(default_factory=list)
    offer_ids: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "roster_path": ROSTER_NOTE_PATH,
            "roster_status": self.roster_status,
            "operator_status": self.operator_status,
            "notes_ok": self.notes_ok,
            "buyer_offers_ok": self.buyer_offers_ok,
            "seller_quotes_ok": self.seller_quotes_ok,
            "skipped_pairs": self.skipped_pairs,
            "failed": self.failed,
            "offer_ids": self.offer_ids,
            "live": True,
            "holds_value": False,
        }


def _progress_path(root: Path) -> Path:
    return root / PROGRESS_NAME


def load_publish_progress(root: Path) -> dict[str, Any]:
    path = _progress_path(root)
    if not path.exists():
        return {"notes": [], "pairs": [], "offers": []}
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data, dict):
        return {"notes": [], "pairs": [], "offers": []}
    return {
        "notes": [str(x) for x in data.get("notes", [])],
        "pairs": [str(x) for x in data.get("pairs", [])],
        "offers": [str(x) for x in data.get("offers", [])],
    }


def save_publish_progress(root: Path, progress: dict[str, Any]) -> None:
    dest = _progress_path(root)
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(json.dumps(progress, indent=2) + "\n", encoding="utf-8")
    os.chmod(dest, 0o600)


def pair_key(buyer: RosterAgent, seller: RosterAgent) -> str:
    return f"{buyer.fingerprint}:{seller.fingerprint}"


def _wait_seconds(resp: httpx.Response) -> float:
    if resp.status_code != 429:
        return 0
    match = WAIT_RE.search(resp.text or "")
    return float(match.group(1)) if match else 2.0


def _retryable(resp: httpx.Response) -> bool:
    return resp.status_code in {0, 429} or resp.status_code >= 500


def _post_with_retry(
    client: httpx.Client,
    method: str,
    url: str,
    json_body: dict[str, Any],
    *,
    timeout: float = 20.0,
    attempts: int = 6,
) -> httpx.Response:
    delay = 1.0
    last: httpx.Response | None = None
    for _ in range(attempts):
        try:
            last = client.request(method, url, json=json_body, timeout=timeout)
        except httpx.HTTPError as exc:
            last = httpx.Response(0, text=str(exc))
        if last is not None and not _retryable(last):
            return last
        time.sleep(max(_wait_seconds(last) if last is not None else 0, delay))
        delay = min(delay * 2, 16)
    assert last is not None
    return last


def _say(
    client: httpx.Client,
    origin: str,
    ident: Identity,
    room: str,
    text: str,
    nonce: str,
) -> httpx.Response:
    sig = sign_room(ident, room, nonce, text)
    return _post_with_retry(
        client,
        "POST",
        f"{origin}/r/{room}",
        {"did": ident.did, "sig": sig, "nonce": nonce, "text": sweep_line(text)},
    )


def publish_roster(
    agents: list[RosterAgent],
    operator: Identity,
    *,
    pairs: int | None = None,
    base: str = DEFAULT_BASE,
    timeout: float = 90.0,
    roster_dir: Path | None = None,
) -> RosterPublish:
    origin = base.rstrip("/")
    dest = roster_dir or default_roster_dir()
    progress = load_publish_progress(dest)
    done_pairs = set(progress["pairs"])
    done_notes = set(progress["notes"])
    book = pair_book(agents, pairs=pairs)
    nonce0 = int(time.time() * 1000)
    lab = PinLab()
    keys = [k for k in ARTIFACT_KEYS if k in lab.named_artifacts] or ["8b-stock"]
    result = RosterPublish(roster_status=0, operator_status=0)
    with httpx.Client(timeout=timeout, follow_redirects=True) as client:
        roster = _post_with_retry(
            client,
            "POST",
            f"{origin}/kv/{ROSTER_NOTE_NS}/{ROSTER_NOTE_KEY}",
            {"value": roster_note_body(agents)},
        )
        result.roster_status = roster.status_code
        if roster.status_code not in {200, 409}:
            result.failed.append(f"roster-note:{roster.status_code}")
        n_buyers = sum(1 for a in agents if a.role == "buyer")
        n_sellers = sum(1 for a in agents if a.role == "seller")
        op_text = f"{operator_roster_line(operator, buyers=n_buyers, sellers=n_sellers)} t={nonce0}"
        op = _say(client, origin, operator, PIN_OPERATOR_ROOM, op_text, str(nonce0))
        result.operator_status = op.status_code
        if op.status_code not in {200, 422}:
            result.failed.append(f"operator-room:{op.status_code}")

        def _note(agent: RosterAgent) -> None:
            if agent.fingerprint in done_notes:
                result.notes_ok += 1
                return
            ns, key = did_note_parts(agent.fingerprint)
            note = _post_with_retry(
                client,
                "POST",
                f"{origin}/kv/{ns}/{key}",
                {"value": f"{agent.did} {note_token(agent.role)}", "if_absent": True},
                timeout=12.0,
                attempts=3,
            )
            if note.status_code in {200, 409}:
                result.notes_ok += 1
                done_notes.add(agent.fingerprint)
                progress["notes"] = sorted(done_notes)
                save_publish_progress(dest, progress)
            else:
                result.failed.append(f"note:{agent.fingerprint}:{note.status_code}")

        posted: set[str] = set()
        for i, (buyer, seller) in enumerate(book, start=1):
            key = pair_key(buyer, seller)
            artifact = keys[(i - 1) % len(keys)]
            if key in done_pairs:
                result.buyer_offers_ok += 1
                result.seller_quotes_ok += 1
                result.skipped_pairs += 1
                posted.add(buyer.fingerprint)
                posted.add(seller.fingerprint)
                continue
            offer_line, quote_line, offer = build_pair_frames(
                buyer, seller, lab=lab, artifact_key=artifact
            )
            bought = _say(
                client, origin, buyer.ident, TCLK_OFFERS_ROOM, offer_line, str(nonce0 + i)
            )
            if bought.status_code == 200:
                result.buyer_offers_ok += 1
                result.offer_ids.append(str(offer["id"]))
            else:
                result.failed.append(f"offer:{buyer.fingerprint}:{bought.status_code}")
            sold = _say(
                client, origin, seller.ident, PIN_OPERATOR_ROOM, quote_line, str(nonce0 + 4000 + i)
            )
            if sold.status_code == 200:
                result.seller_quotes_ok += 1
            else:
                result.failed.append(f"quote:{seller.fingerprint}:{sold.status_code}")
            if bought.status_code == 200 and sold.status_code == 200:
                done_pairs.add(key)
                progress["pairs"] = sorted(done_pairs)
                progress["offers"] = list(progress.get("offers", [])) + [str(offer["id"])]
                save_publish_progress(dest, progress)
            print(
                f"roster pair {i}/{len(book)} offer={bought.status_code} quote={sold.status_code}",
                file=sys.stderr,
                flush=True,
            )
            _note(buyer)
            _note(seller)
            posted.add(buyer.fingerprint)
            posted.add(seller.fingerprint)
            time.sleep(0.15)
        if pairs is None:
            for agent in agents:
                if agent.fingerprint in posted:
                    continue
                _note(agent)
    return result
