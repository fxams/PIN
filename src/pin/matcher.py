"""Operator matcher: answer PIN jobs as the published PIN DID.

Two entry paths, same settlement:

* pin1 `want` on `/r/pin` (PIN-aware agents).
* tclk1 `offer` on `/r/tclk-offers` with `job.proto=pin` and `job.context`
  naming a published artifact (the kibble-shaped path — agents already
  watch the money board).

Money frames stay on `tclk-offers`. Never write tclk1 into `pin` or `kibble`.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.identity import PIN_OPERATOR_ROOM, TCLK_OFFERS_ROOM, Identity
from pin.lab import PinLab
from pin.models import Quote, QuoteRequest, Receipt, SlaClass, Tier
from pin.tclk_bind import TclkLock, maybe_reveal, mint_hashlock, pin_ok
from pin.tclk_deal import payee_accept_offer, payee_settle_lines
from pin.tclk_entry import resolve_pin_artifact
from pin.tclk_frames import decode_frame as decode_tclk
from pin.tclk_frames import encode_frame as encode_tclk
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
    tclk_accepts: list[str] = field(default_factory=list)
    tclk_settles: list[str] = field(default_factory=list)
    skipped: list[str] = field(default_factory=list)
    since: int = 0

    def as_dict(self) -> dict[str, Any]:
        tclk_n = len(self.tclk_accepts) + len(self.tclk_settles)
        return {
            "quotes": self.quotes,
            "leaf0": self.leaf0,
            "receipts": self.receipts,
            "tclk_accepts": self.tclk_accepts,
            "tclk_settles": self.tclk_settles,
            "skipped": self.skipped,
            "since": self.since,
            "posted": len(self.quotes) + len(self.leaf0) + len(self.receipts) + tclk_n,
        }


class OperatorMatcher:
    """Reads the public `pin` venue and replies as the operator DID."""

    def __init__(
        self,
        lab: PinLab,
        ident: Identity,
        *,
        venue: Venue | None = None,
        attack: str = "",
    ) -> None:
        self.lab = lab
        self.ident = ident
        self.venue = venue or lab.venue
        self.attack = attack
        self.quoted: dict[str, Quote] = {}
        self.quoted_by_offer: dict[str, Quote] = {}
        self.offer_to_want: dict[str, str] = {}
        self.wants: dict[str, Pin1Frame] = {}
        self.locks: dict[str, TclkLock] = {}
        self.done_accepts: set[str] = set()
        self.done_job_ids: set[str] = set()
        self.receipts_by_job: dict[str, Receipt] = {}
        self.tclk_offers: dict[str, dict[str, Any]] = {}
        self.tclk_secrets: dict[str, tuple[dict[str, Any], str]] = {}
        self.tclk_accepted_refs: set[str] = set()
        self.tclk_revealed: set[str] = set()
        self.tclk_quoted: set[str] = set()
        self.done_tclk_refs: set[str] = set()
        self.since = 0
        self.tclk_since = 0

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

    def fold_tclk(self, since: int | None = None) -> None:
        start = self.tclk_since if since is None else since
        last = start
        for rec in self.venue.read(TCLK_OFFERS_ROOM, since=start):
            last = max(last, rec.seq)
            if not rec.signed:
                continue
            try:
                frame = decode_tclk(rec.text)
            except Exception:
                continue
            if rec.did and frame.get("from") != rec.did:
                continue
            kind = frame.get("type")
            if kind == "offer" and (frame.get("job") or {}).get("proto") == "pin":
                self.tclk_offers[str(frame["id"])] = frame
            elif kind == "accept" and frame.get("from") == self.ident.did and frame.get("ref"):
                self.tclk_accepted_refs.add(str(frame["ref"]))
            elif kind == "reveal" and frame.get("from") == self.ident.did and frame.get("contract"):
                self.tclk_revealed.add(str(frame["contract"]))
        self.tclk_since = last

    def step(self, *, max_jobs: int | None = None) -> MatchStep:
        result = MatchStep(since=self.since)
        frames = self.fold()
        self.fold_tclk()
        result.since = self.since
        for frame in frames:
            if frame.type == "want" and frame.nonce:
                self.wants[frame.nonce] = frame
            elif frame.type == "quote" and frame.ref and frame.offer_id:
                if frame.from_did == self.ident.did or frame.tclk_ref:
                    self._remember_quote(frame)
            elif frame.type == "leaf0" and frame.from_did == self.ident.did and frame.job_id:
                self.done_job_ids.add(frame.job_id)
            elif frame.type == "receipt" and frame.from_did == self.ident.did:
                if frame.job_id:
                    self.done_job_ids.add(frame.job_id)
                if frame.tclk_ref:
                    self.done_tclk_refs.add(frame.tclk_ref)
        for frame in frames:
            if frame.type == "accept" and frame.offer_id:
                if frame.tclk_ref and frame.tclk_ref in self.done_tclk_refs:
                    self.done_accepts.add(frame.offer_id)
                if frame.job_id and frame.job_id in self.done_job_ids:
                    self.done_accepts.add(frame.offer_id)
        for frame in frames:
            if frame.type == "want":
                line = self._quote_want(frame, result)
                if line:
                    result.quotes.append(line)
            elif frame.type == "accept":
                if max_jobs is not None and len(result.receipts) >= max_jobs:
                    continue
                lines = self._fill_accept(frame, result)
                if lines:
                    result.leaf0.append(lines[0])
                    result.receipts.append(lines[1])
        self._quote_and_fill_tclk_entries(result, max_jobs=max_jobs)
        self._catch_up_tclk(result)
        return result

    def _remember_quote(self, quote_frame: Pin1Frame) -> None:
        if not quote_frame.ref or not quote_frame.artifact_id:
            return
        if quote_frame.offer_id and quote_frame.offer_id in self.quoted_by_offer:
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
        updates: dict[str, Any] = {}
        if quote_frame.offer_id:
            updates["offer_id"] = quote_frame.offer_id
        if quote_frame.usd_micros is not None:
            updates["usd_micros"] = quote_frame.usd_micros
        if quote_frame.flop_fee is not None:
            updates["flop_fee"] = quote_frame.flop_fee
        if quote_frame.ttl_sec is not None:
            updates["ttl_sec"] = quote_frame.ttl_sec
        if updates:
            rebuilt = rebuilt.model_copy(update=updates)
        self.quoted.setdefault(quote_frame.ref, rebuilt)
        if rebuilt.offer_id:
            self.quoted_by_offer[rebuilt.offer_id] = rebuilt
            self.offer_to_want[rebuilt.offer_id] = quote_frame.ref
            self.locks.setdefault(rebuilt.offer_id, mint_hashlock(rebuilt.flop_fee, rail="paper"))


    def _quote_want(self, want: Pin1Frame, result: MatchStep, *, tclk_ref: str | None = None) -> str | None:
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
                rail="paper",
                tclk_ref=tclk_ref,
            )
        )
        self.quoted[want.nonce] = quote
        self.quoted_by_offer[quote.offer_id] = quote
        self.offer_to_want[quote.offer_id] = want.nonce
        self.wants[want.nonce] = want
        self.locks[quote.offer_id] = mint_hashlock(quote.flop_fee, rail="paper")
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", line, signed=True, did=self.ident.did)
        return line

    def _fill_accept(self, accept: Pin1Frame, result: MatchStep) -> list[str]:
        if not accept.offer_id or accept.offer_id in self.done_accepts:
            return []
        if accept.job_id and accept.job_id in self.done_job_ids:
            return []
        quote = self.quoted_by_offer.get(accept.offer_id)
        if quote is None:
            result.skipped.append("accept-unknown-offer")
            return []
        want_nonce = self.offer_to_want.get(accept.offer_id)
        want = self.wants.get(want_nonce) if want_nonce else None
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
        try:
            outcome = self.lab.run_job(
                spec, offer_id=quote.offer_id, n_in=want.n_in or 32, attack=self.attack
            )
        except KeyError:
            outcome = self.lab.run_job(spec, n_in=want.n_in or 32, attack=self.attack)
        rec = outcome.receipt
        self.done_accepts.add(accept.offer_id)
        if rec is None:
            result.skipped.append("job-no-receipt")
            return []
        if accept.job_id:
            self.receipts_by_job[accept.job_id] = rec
        if not accept.job_id or accept.job_id == spec.job_id:
            self.receipts_by_job[spec.job_id] = rec
        if accept.tclk_ref:
            self.done_tclk_refs.add(accept.tclk_ref)
        tclk_offer = self._find_tclk_offer(accept, spec.job_id)
        lock = self.locks.get(quote.offer_id)
        tclk_ref = tclk_offer["id"] if tclk_offer else (lock.statement if lock else None)
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
                tclk_ref=tclk_ref,
            )
        )
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", leaf0, signed=True, did=self.ident.did)
        self.venue.say(PIN_OPERATOR_ROOM, "pin-operator", receipt, signed=True, did=self.ident.did)
        if tclk_offer is not None:
            self._bind_tclk(tclk_offer, rec, result)
        elif lock is not None:
            preimage = maybe_reveal(lock, rec)
            self.venue.note_set("tclk", lock.statement[2:18], "claimed" if preimage else "refunded")
        return [leaf0, receipt]

    def _find_tclk_offer(self, accept: Pin1Frame, job_id: str) -> dict[str, Any] | None:
        if accept.tclk_ref:
            return self.tclk_offers.get(accept.tclk_ref)
        want = accept.job_id or job_id
        if not want:
            return None
        for offer in self.tclk_offers.values():
            job = offer.get("job") or {}
            if job.get("proto") == "pin" and job.get("id") == want:
                return offer
        return None

    def _bind_tclk(self, offer: dict[str, Any], rec: Receipt | None, result: MatchStep) -> None:
        offer_id = str(offer["id"])
        if offer.get("from") == self.ident.did:
            result.skipped.append("tclk-offer-is-self")
            return
        if offer_id in self.tclk_accepted_refs and offer_id not in self.tclk_secrets:
            result.skipped.append("tclk-accept-secret-lost")
            return
        if offer_id not in self.tclk_secrets:
            accept, secret = payee_accept_offer(offer, payee_did=self.ident.did)
            line = encode_tclk(accept)
            self.venue.say(TCLK_OFFERS_ROOM, "pin-operator", line, signed=True, did=self.ident.did)
            result.tclk_accepts.append(line)
            self.tclk_secrets[offer_id] = (accept, secret)
            self.tclk_accepted_refs.add(offer_id)
        accept, secret = self.tclk_secrets[offer_id]
        contract = str(accept["contract"])
        if contract in self.tclk_revealed:
            return
        if not pin_ok(rec):
            result.skipped.append("tclk-no-reveal")
            return
        lines = payee_settle_lines(accept, secret, rec)
        for line in lines:
            self.venue.say(TCLK_OFFERS_ROOM, "pin-operator", line, signed=True, did=self.ident.did)
            result.tclk_settles.append(line)
        if lines:
            self.tclk_revealed.add(contract)

    def _quote_and_fill_tclk_entries(self, result: MatchStep, *, max_jobs: int | None = None) -> None:
        """Kibble-shaped path: a proto=pin offer on tclk-offers is the want."""
        for offer in list(self.tclk_offers.values()):
            if max_jobs is not None and len(result.receipts) >= max_jobs:
                break
            offer_id = str(offer["id"])
            if offer.get("from") == self.ident.did:
                continue
            if offer_id in self.done_accepts or offer_id in self.done_tclk_refs:
                continue
            job = offer.get("job") or {}
            if job.get("proto") != "pin":
                continue
            if not job.get("context"):
                continue
            resolved = resolve_pin_artifact(self.lab, job.get("context"))
            if resolved is None:
                result.skipped.append(f"tclk-unknown-artifact:{offer_id[:10]}")
                continue
            artifact_id, _key = resolved
            payer = str(offer.get("from") or "")
            if not payer.startswith("did:key:"):
                result.skipped.append("tclk-offer-no-did")
                continue
            want = Pin1Frame(
                type="want",
                from_did=payer,
                nonce=offer_id,
                artifact_id=artifact_id,
                tier="T1",
                sla="interactive",
                n_in=32,
                n_out=48,
            )
            self.wants[offer_id] = want
            if offer_id not in self.quoted:
                line = self._quote_want(want, result, tclk_ref=offer_id)
                if line:
                    result.quotes.append(line)
            quote = self.quoted.get(offer_id)
            if quote is None:
                continue
            self.tclk_quoted.add(offer_id)
            if quote.offer_id in self.done_accepts:
                continue
            accept = Pin1Frame(
                type="accept",
                from_did=payer,
                nonce=_nonce(),
                offer_id=quote.offer_id,
                job_id=str(job.get("id") or ""),
                jobspec_cid=str(job.get("id") or ""),
                tclk_ref=offer_id,
                rail="paper",
            )
            lines = self._fill_accept(accept, result)
            if lines:
                result.leaf0.append(lines[0])
                result.receipts.append(lines[1])

    def _catch_up_tclk(self, result: MatchStep) -> None:
        """Accept a pin paper offer that arrived after the pin1 job already ran."""
        for offer in self.tclk_offers.values():
            offer_id = str(offer["id"])
            if offer_id in self.tclk_accepted_refs and offer_id not in self.tclk_secrets:
                continue
            if offer_id in self.done_tclk_refs:
                continue
            job_id = str((offer.get("job") or {}).get("id") or "")
            rec = self.receipts_by_job.get(job_id)
            if rec is None:
                continue
            if offer_id in self.tclk_secrets and str(self.tclk_secrets[offer_id][0]["contract"]) in self.tclk_revealed:
                continue
            self._bind_tclk(offer, rec, result)


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
