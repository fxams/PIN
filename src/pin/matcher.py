"""Operator matcher: answer signed pin1 wants as the published PIN DID.

Public board is Technocore room `pin-jobs` (same shape as flop's `tclk-offers`
and the kibble work board). Money frames stay on `tclk-offers` with
`job.proto=pin` — never as tclk1 lines inside `pin-jobs` or `kibble`.
Owned control room is `d-pin`, claimed by the operator DID.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.identity import PIN_OPERATOR_ROOM, Identity
from pin.lab import PinLab
from pin.models import Quote, QuoteRequest, SlaClass, Tier
from pin.tclk_bind import TclkLock, maybe_reveal, mint_hashlock
from pin.venue import RoomRecord, Venue


def _nonce() -> str:
    return secrets.token_hex(8)


def artifact_key_for_id(lab: PinLab, artifact_id: str) -> str | None:
    for key, artifact in lab.named_artifacts.items():
        if artifact.artifact_id == artifact_id:
            return key
    return None


@dataclass
class MatchStep:
    quotes: list[str] = field(default_factory=list)
    leaf0: list[str] = field(default_factory=list)
    receipts: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    since: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "quotes": self.quotes,
            "leaf0": self.leaf0,
            "receipts": self.receipts,
            "skipped": self.skipped,
            "since": self.since,
            "posted": len(self.quotes) + len(self.leaf0) + len(self.receipts),
        }


class OperatorMatcher:
    """Reads a pin-jobs venue and replies as the operator DID."""

    def __init__(self, lab: PinLab, ident: Identity, *, venue: Venue | None = None) -> None:
        self.lab = lab
        self.ident = ident
        self.venue = venue or lab.venue
        self.quoted: dict[str, Quote] = {}
        self.wants: dict[str, Pin1Frame] = {}
        self.locks: dict[str, TclkLock] = {}
        self.done_accepts: set[str] = set()
        self.done_job_ids: set[str] = set()
        self.since = 0

    def fold(self, since: int | None = None) -> list[Pin1Frame]:
        out: list[Pin1Frame] = []
        start = self.since if since is None else since
        last = start
        for rec in self.venue.read(PIN_OPERATOR_ROOM, since=start):
            last = max(last, rec.seq)
            if not rec.signed:
                continue
            try:
                frame = decode_frame(rec.text)
            except Exception:
                continue
            if rec.did and frame.from_did != rec.did:
                continue
            out.append(frame)
        self.since = last
        return out

    def step(self) -> MatchStep:
        result = MatchStep(since=self.since)
        frames = self.fold()
        result.since = self.since
        for frame in frames:
            if frame.type == "want" and frame.nonce:
                self.wants[frame.nonce] = frame
            elif frame.type == "quote" and frame.from_did == self.ident.did and frame.ref and frame.offer_id:
                self._remember_quote(frame)
            elif frame.type == "leaf0" and frame.from_did == self.ident.did and frame.job_id:
                self.done_job_ids.add(frame.job_id)
        for frame in frames:
            if frame.type == "want":
                line = self._quote_want(frame, result)
                if line:
                    result.quotes.append(line)
            elif frame.type == "accept":
                lines = self._fill_accept(frame, result)
                if lines:
                    result.leaf0.append(lines[0])
                    result.receipts.append(lines[1])
        return result

    def _remember_quote(self, quote_frame: Pin1Frame) -> None:
        if not quote_frame.ref or quote_frame.ref in self.quoted or not quote_frame.artifact_id:
            return
        try:
            rebuilt = self.lab.make_quote(
                QuoteRequest(
                    artifact_id=quote_frame.artifact_id,
                    sla_class=SlaClass("interactive"),
                    tier=Tier("T1"),
                    n_in=32,
                    n_out=48,
                )
            )
        except KeyError:
            return
        self.quoted[quote_frame.ref] = rebuilt
        if quote_frame.offer_id:
            self.locks.setdefault(quote_frame.offer_id, mint_hashlock(rebuilt.flop_fee, rail="flop-htlc"))


    def _quote_want(self, want: Pin1Frame, result: MatchStep) -> str | None:
        if not want.nonce or want.nonce in self.quoted:
            return None
        if not want.artifact_id:
            result.skipped.append("want-missing-artifact")
            return None
        if artifact_key_for_id(self.lab, want.artifact_id) is None:
            result.skipped.append(f"unknown-artifact:{want.artifact_id[:12]}")
            return None
        try:
            tier = Tier(want.tier or "T1")
            sla = SlaClass(want.sla or "interactive")
        except ValueError:
            result.skipped.append("bad-tier-or-sla")
            return None
        quote = self.lab.make_quote(
            QuoteRequest(
                artifact_id=want.artifact_id,
                sla_class=sla,
                tier=tier,
                n_in=want.n_in or 32,
                n_out=want.n_out or 48,
            )
        )
        if want.max_usd is not None and quote.usd_micros > want.max_usd:
            result.skipped.append("over-budget")
            return None
        line = encode_frame(
            Pin1Frame(
                type="quote",
                from_did=self.ident.did,
                nonce=_nonce(),
                artifact_id=want.artifact_id,
                ref=want.nonce,
                offer_id=quote.offer_id,
                usd_micros=quote.usd_micros,
                flop_fee=quote.flop_fee,
                ttl_sec=quote.ttl_sec,
                rail="flop-htlc",
            )
        )
        self.quoted[want.nonce] = quote
        self.wants[want.nonce] = want
        self.locks[quote.offer_id] = mint_hashlock(quote.flop_fee, rail="flop-htlc")
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", line, signed=True, did=self.ident.did)
        return line

    def _fill_accept(self, accept: Pin1Frame, result: MatchStep) -> list[str]:
        if not accept.offer_id or accept.offer_id in self.done_accepts:
            return []
        if accept.job_id and accept.job_id in self.done_job_ids:
            return []
        quote = next((q for q in self.quoted.values() if q.offer_id == accept.offer_id), None)
        if quote is None:
            result.skipped.append("accept-unknown-offer")
            return []
        want = next((w for n, w in self.wants.items() if self.quoted[n].offer_id == accept.offer_id), None)
        key = artifact_key_for_id(self.lab, quote.artifact_id)
        if key is None or want is None:
            result.skipped.append("accept-missing-want")
            return []
        spec = self.lab.default_spec(
            artifact_key=key,
            tier=Tier(want.tier or "T1"),
            sla=SlaClass(want.sla or "interactive"),
            max_new_tokens=want.n_out or 48,
        )
        outcome = self.lab.run_job(spec, offer_id=quote.offer_id, n_in=want.n_in or 32)
        rec = outcome.receipt
        self.done_accepts.add(accept.offer_id)
        if rec is None:
            result.skipped.append("job-no-receipt")
            return []
        lock = self.locks.get(quote.offer_id)
        leaf0 = encode_frame(
            Pin1Frame(
                type="leaf0",
                from_did=self.ident.did,
                nonce=_nonce(),
                job_id=spec.job_id,
                artifact_id=spec.artifact_id,
                leaf0_sig=rec.leaf0_signature,
                t_accept=rec.timing.t_accept,
            )
        )
        receipt = encode_frame(
            Pin1Frame(
                type="receipt",
                from_did=self.ident.did,
                nonce=_nonce(),
                job_id=spec.job_id,
                artifact_id=spec.artifact_id,
                transcript_root=rec.transcript_root,
                flop_proof_hash=rec.flop_proof_hash,
                paid=rec.paid,
                sla_miss=rec.sla_miss,
                status=outcome.status.value,
                tclk_ref=lock.statement if lock else None,
            )
        )
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", leaf0, signed=True, did=self.ident.did)
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", receipt, signed=True, did=self.ident.did)
        if lock is not None:
            preimage = maybe_reveal(lock, rec)
            self.venue.note_set("tclk", lock.statement[2:18], "claimed" if preimage else "refunded")
        return [leaf0, receipt]


def ingest_json_messages(venue: Venue, payload: dict[str, Any], *, room: str = PIN_OPERATOR_ROOM) -> int:
    """Copy Technocore JSON messages into the lab venue. Unsigned lines stay unsigned."""
    n = 0
    for msg in payload.get("messages") or []:
        text = str(msg.get("text") or "")
        did = msg.get("from")
        signed = bool(msg.get("sig") and did)
        seq = int(msg.get("seq") or 0)
        bucket = venue.rooms.setdefault(room, [])
        if any(rec.seq == seq for rec in bucket):
            continue
        rec_seq = seq or (len(bucket) + 1)
        bucket.append(
            RoomRecord(
                seq=rec_seq,
                ts_ms=0,
                nick="live",
                text=text,
                signed=signed,
                did=str(did) if did else None,
            )
        )
        n += 1
    return n
