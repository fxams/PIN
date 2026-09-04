"""PIN-gated tclk/1 paper deal.

Payer posts a `tclk1` offer on flop's `tclk-offers` room with `job.proto=pin`.
Payee mints the hash secret on accept. Payer locks the paper note. PIN reveals
that secret only after leaf 0 + watcher `pin_ok`. Paper holds no value; it is
the rehearsal rail until flop-labs ships `flop-htlc`.
"""

from __future__ import annotations

import secrets
import time
from dataclasses import dataclass, field
from typing import Any

from pin.crypto import private_key_hex
from pin.did import new_agent_identity
from pin.identity import TCLK_OFFERS_ROOM, Identity, identity_from_seed
from pin.models import Receipt
from pin.tclk_bind import pin_ok
from pin.tclk_frames import (
    decode_frame,
    encode_frame,
    hash_lock_from_preimage,
    make_accept,
    make_lock,
    make_offer,
    make_receipt,
    make_refund,
    make_reveal,
    pin_job,
)
from pin.tclk_paper import PaperNote, PaperStore, encode_paper_note, paper_note_path
from pin.venue import Venue

PAPER_RAIL = "paper"
PAPER_ASSET = "PAPER"
DEFAULT_AMOUNT = "100"


@dataclass
class TclkDeal:
    offer: dict[str, Any]
    accept: dict[str, Any]
    lock_frame: dict[str, Any]
    secret: str
    statement: str
    paper: PaperNote | None
    frames: list[str] = field(default_factory=list)
    revealed: bool = False
    refunded: bool = False
    paper_claimed: bool = False

    @property
    def contract(self) -> str:
        return str(self.accept["contract"])

    @property
    def offer_id(self) -> str:
        return str(self.offer["id"])

    def as_dict(self) -> dict[str, Any]:
        ns, key = paper_note_path(self.contract)
        return {
            "room": TCLK_OFFERS_ROOM,
            "rail": PAPER_RAIL,
            "holds_value": False,
            "job": self.offer.get("job"),
            "offer_id": self.offer_id,
            "contract": self.contract,
            "statement": self.statement,
            "revealed": self.revealed,
            "refunded": self.refunded,
            "paper_note": f"{ns}/{key}",
            "paper_status": None if self.paper is None else self.paper.status,
            "frames": self.frames,
        }


def paper_windows(now_ms: int) -> tuple[int, int, int]:
    """expires < claimBy < refundAfter, same stagger the golden vector uses (1h / 24h / 48h)."""
    expires_ms = now_ms + 3_600_000
    claim_by_ms = now_ms + 86_400_000
    refund_after_ms = now_ms + 172_800_000
    return expires_ms, claim_by_ms, refund_after_ms


def open_paper_deal(
    *,
    payer_did: str,
    payee_did: str,
    job_id: str,
    amount: str = DEFAULT_AMOUNT,
    now_ms: int,
    offer_nonce: str | None = None,
    accept_nonce: str | None = None,
    secret: bytes | None = None,
    expires_ms: int | None = None,
    claim_by_ms: int | None = None,
    refund_after_ms: int | None = None,
    venue: Venue | None = None,
    paper: PaperStore | None = None,
) -> TclkDeal:
    if expires_ms is None or claim_by_ms is None or refund_after_ms is None:
        expires_ms, claim_by_ms, refund_after_ms = paper_windows(now_ms)
    raw_secret = secret if secret is not None else secrets.token_bytes(32)
    preimage, statement = hash_lock_from_preimage(raw_secret)
    offer = make_offer(
        from_did=payer_did,
        amount=amount,
        asset=PAPER_ASSET,
        lock="hash",
        rails=[PAPER_RAIL],
        nonce=offer_nonce or secrets.token_hex(8),
        expires_ms=expires_ms,
        claim_by_ms=claim_by_ms,
        refund_after_ms=refund_after_ms,
        role="payer",
        job=pin_job(job_id),
    )
    accept = make_accept(
        offer=offer,
        from_did=payee_did,
        statement=statement,
        nonce=accept_nonce or secrets.token_hex(8),
    )
    lock_frame = make_lock(
        contract=accept["contract"],
        from_did=payer_did,
        ref=accept["contract"],
        rail=PAPER_RAIL,
    )
    store = paper or PaperStore()
    paper_note = store.lock(
        accept["contract"],
        statement,
        refund_after_ms,
        now_ms=now_ms,
        lock="hash",
    )
    frames = [encode_frame(offer), encode_frame(accept), encode_frame(lock_frame)]
    if venue is not None:
        venue.say(TCLK_OFFERS_ROOM, "pin-payer", frames[0], signed=True, did=payer_did)
        venue.say(TCLK_OFFERS_ROOM, "pin-payee", frames[1], signed=True, did=payee_did)
        venue.say(TCLK_OFFERS_ROOM, "pin-payer", frames[2], signed=True, did=payer_did)
        ns, key = paper_note_path(accept["contract"])
        venue.note_set(ns, key, encode_paper_note(paper_note))
    return TclkDeal(
        offer=offer,
        accept=accept,
        lock_frame=lock_frame,
        secret=preimage,
        statement=statement,
        paper=paper_note,
        frames=frames,
    )


def settle_deal(
    deal: TclkDeal,
    receipt: Receipt | None,
    *,
    now_ms: int,
    venue: Venue | None = None,
    paper: PaperStore | None = None,
) -> str | None:
    """Reveal the paper secret only when PIN says the JobSpec ran."""
    payer = str(deal.offer["from"])
    payee = str(deal.accept["from"])
    if pin_ok(receipt):
        reveal = make_reveal(contract=deal.contract, from_did=payee, secret=deal.secret)
        ack = make_receipt(
            contract=deal.contract,
            from_did=payee,
            outcome="claimed",
            rail=PAPER_RAIL,
            ref=deal.contract,
        )
        lines = [encode_frame(reveal), encode_frame(ack)]
        deal.frames.extend(lines)
        deal.revealed = True
        if paper is not None:
            deal.paper = paper.claim(deal.contract, deal.secret, now_ms)
            deal.paper_claimed = True
        if venue is not None:
            venue.say(TCLK_OFFERS_ROOM, "pin-payee", lines[0], signed=True, did=payee)
            venue.say(TCLK_OFFERS_ROOM, "pin-payee", lines[1], signed=True, did=payee)
            if deal.paper is not None:
                ns, key = paper_note_path(deal.contract)
                venue.note_set(ns, key, encode_paper_note(deal.paper))
        return deal.secret
    deal.refunded = True
    if now_ms >= int(deal.offer["refundAfterMs"]):
        refund = make_refund(contract=deal.contract, from_did=payer, reason="pin-not-ok")
        ack = make_receipt(
            contract=deal.contract,
            from_did=payer,
            outcome="refunded",
            rail=PAPER_RAIL,
            ref=deal.contract,
        )
        lines = [encode_frame(refund), encode_frame(ack)]
        deal.frames.extend(lines)
        if paper is not None:
            deal.paper = paper.refund(deal.contract, now_ms)
        if venue is not None:
            venue.say(TCLK_OFFERS_ROOM, "pin-payer", lines[0], signed=True, did=payer)
            venue.say(TCLK_OFFERS_ROOM, "pin-payer", lines[1], signed=True, did=payer)
    return None


def fold_tclk_room(venue: Venue, room: str = TCLK_OFFERS_ROOM) -> list[dict[str, Any]]:
    out: list[dict[str, Any]] = []
    for rec in venue.read(room):
        if not rec.signed:
            continue
        try:
            frame = decode_frame(rec.text)
        except Exception:
            continue
        if rec.did and frame.get("from") != rec.did:
            continue
        out.append(frame)
    return out


def run_live_paper_demo(
    payee: Identity,
    *,
    attack: str = "",
    base: str = "https://technocore.chat",
    artifact_key: str = "8b-stock",
) -> dict[str, Any]:
    """Opt-in rehearsal on live tclk-offers. Paper holds no value. Seed is never returned."""
    from pin.lab import PinLab
    from pin.technocore_client import post_kv, post_signed_line

    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(artifact_key=artifact_key), attack=attack)
    payer_key, payer_did, _fp = new_agent_identity()
    payer = identity_from_seed(private_key_hex(payer_key), source="ephemeral-payer")
    now_ms = int(time.time() * 1000)
    store = PaperStore()
    deal = open_paper_deal(
        payer_did=payer.did,
        payee_did=payee.did,
        job_id=outcome.job_id,
        now_ms=now_ms,
        paper=store,
    )
    nonce = now_ms
    posts: list[dict[str, Any]] = []

    def _say(ident: Identity, line: str) -> None:
        nonlocal nonce
        nonce += 1
        wr = post_signed_line(ident, room=TCLK_OFFERS_ROOM, text=line, nonce=str(nonce), base=base)
        posts.append({"kind": "room", "room": TCLK_OFFERS_ROOM, "status": wr.status, "body": wr.body[:200]})

    _say(payer, encode_frame(deal.offer))
    _say(payee, encode_frame(deal.accept))
    _say(payer, encode_frame(deal.lock_frame))
    ns, key = paper_note_path(deal.contract)
    if deal.paper is None:
        raise RuntimeError("paper deal missing lock note")
    locked = post_kv(ns=ns, key=key, value=encode_paper_note(deal.paper), base=base, if_absent=True)
    posts.append({"kind": "paper", "note": f"{ns}/{key}", "status": locked.status, "body": locked.body[:200]})

    secret = settle_deal(deal, outcome.receipt, now_ms=now_ms, paper=store)
    for line in deal.frames[3:]:
        frame = decode_frame(line)
        speaker = payee if frame.get("from") == payee.did else payer
        _say(speaker, line)
    if deal.paper is not None and deal.paper_claimed:
        claimed = post_kv(ns=ns, key=key, value=encode_paper_note(deal.paper), base=base, if_absent=False)
        posts.append({"kind": "paper", "note": f"{ns}/{key}", "status": claimed.status, "body": claimed.body[:200]})

    return {
        "coordination": "technocore-room",
        "room": TCLK_OFFERS_ROOM,
        "rail": PAPER_RAIL,
        "holds_value": False,
        "job": {"proto": "pin", "id": outcome.job_id},
        "payer_did": payer.did,
        "payee_did": payee.did,
        "offer_id": deal.offer_id,
        "contract": deal.contract,
        "statement": deal.statement,
        "tclk_revealed": bool(secret),
        "status": outcome.status.value,
        "paid": bool(outcome.receipt and outcome.receipt.paid),
        "usd_invoice_micros": outcome.usd_invoice_micros,
        "frames": deal.frames,
        "live_posts": posts,
    }
