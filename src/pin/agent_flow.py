"""Two-agent PIN job over Technocore-shaped rooms.

Agent (payer) and miner (payee) never share a POST client. They post pin1
frames, publish the JobSpec as a KV note, lock FLOP under a tclk hash, and
only reveal after PIN says the JobSpec ran.
"""

from __future__ import annotations

import secrets
from dataclasses import dataclass, field
from typing import Any

from pin.did import did_from_private, new_agent_identity
from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.lab import JobOutcome, PinLab
from pin.models import QuoteRequest
from pin.tclk_bind import TclkLock, maybe_reveal, mint_hashlock
from pin.transcript import prompt_commit
from pin.venue import Venue

PIN_JOBS_ROOM = "pin-jobs"


@dataclass
class AgentJobTranscript:
    agent_did: str
    miner_did: str
    frames: list[str]
    job_id: str
    jobspec_cid: str
    tclk: TclkLock
    outcome: JobOutcome
    revealed: str | None
    notes: dict[str, str] = field(default_factory=dict)

    def as_dict(self) -> dict[str, Any]:
        rec = self.outcome.receipt
        return {
            "coordination": "technocore-room",
            "room": PIN_JOBS_ROOM,
            "money": "tclk1 + flop-htlc",
            "settlement": "flop-session",
            "agent_did": self.agent_did,
            "miner_did": self.miner_did,
            "frames": self.frames,
            "job_id": self.job_id,
            "jobspec_cid": self.jobspec_cid,
            "tclk_statement": self.tclk.statement,
            "tclk_revealed": bool(self.revealed),
            "status": self.outcome.status.value,
            "usd_invoice_micros": self.outcome.usd_invoice_micros,
            "flop_session": self.outcome.flop_session,
            "paid": bool(rec and rec.paid),
            "notes": self.outcome.notes,
        }


def _nonce() -> str:
    return secrets.token_hex(8)


def run_agent_job(
    lab: PinLab,
    *,
    artifact_key: str = "8b-stock",
    attack: str = "",
    venue: Venue | None = None,
) -> AgentJobTranscript:
    venue = venue or lab.venue
    _agent_key, agent_did, _fp = new_agent_identity()
    miner_did = did_from_private(lab.miner_key)

    spec = lab.default_spec(artifact_key=artifact_key)
    quote = lab.make_quote(
        QuoteRequest(
            artifact_id=spec.artifact_id,
            sla_class=spec.sla_class,
            tier=spec.tier,
            n_in=32,
            n_out=spec.sampler.max_new_tokens,
        )
    )

    want = Pin1Frame(
        type="want",
        from_did=agent_did,
        nonce=_nonce(),
        artifact_id=spec.artifact_id,
        tier=spec.tier.value,
        sla=spec.sla_class.value,
        n_in=32,
        n_out=spec.sampler.max_new_tokens,
        max_usd=spec.max_price_usd_micros,
    )
    quote_frame = Pin1Frame(
        type="quote",
        from_did=miner_did,
        nonce=_nonce(),
        artifact_id=spec.artifact_id,
        ref=want.nonce,
        offer_id=quote.offer_id,
        usd_micros=quote.usd_micros,
        flop_fee=quote.flop_fee,
        ttl_sec=quote.ttl_sec,
        rail="flop-htlc",
    )
    jobspec_cid = spec.job_id
    venue.note_set("pin-jobspec", jobspec_cid, encode_frame(want))  # pointer; full spec is hashed id
    venue.note_set("pin-jobspec", f"{jobspec_cid}-json", spec.model_dump_json())

    lock = mint_hashlock(quote.flop_fee, rail="flop-htlc")
    accept = Pin1Frame(
        type="accept",
        from_did=agent_did,
        nonce=_nonce(),
        offer_id=quote.offer_id,
        job_id=spec.job_id,
        jobspec_cid=jobspec_cid,
        tclk_ref=lock.statement,
        rail="flop-htlc",
    )

    frames = [encode_frame(want), encode_frame(quote_frame), encode_frame(accept)]
    venue.say(PIN_JOBS_ROOM, "pin-agent", frames[0], signed=True, did=agent_did)
    venue.say(PIN_JOBS_ROOM, "pin-miner", frames[1], signed=True, did=miner_did)
    venue.say(PIN_JOBS_ROOM, "pin-agent", frames[2], signed=True, did=agent_did)

    outcome = lab.run_job(spec, offer_id=quote.offer_id, attack=attack, n_in=32)
    rec = outcome.receipt
    if rec is not None:
        leaf0 = Pin1Frame(
            type="leaf0",
            from_did=miner_did,
            nonce=_nonce(),
            job_id=spec.job_id,
            artifact_id=spec.artifact_id,
            leaf0_sig=rec.leaf0_signature,
            t_accept=rec.timing.t_accept,
        )
        receipt_frame = Pin1Frame(
            type="receipt",
            from_did=miner_did,
            nonce=_nonce(),
            job_id=spec.job_id,
            artifact_id=spec.artifact_id,
            transcript_root=rec.transcript_root,
            flop_proof_hash=rec.flop_proof_hash,
            paid=rec.paid,
            sla_miss=rec.sla_miss,
            status=outcome.status.value,
            tclk_ref=lock.statement,
        )
        frames.extend([encode_frame(leaf0), encode_frame(receipt_frame)])
        venue.say(PIN_JOBS_ROOM, "pin-miner", frames[-2], signed=True, did=miner_did)
        venue.say(PIN_JOBS_ROOM, "pin-miner", frames[-1], signed=True, did=miner_did)

    preimage = maybe_reveal(lock, rec)
    if preimage:
        # tclk reveal is a separate convention; we only record that PIN authorized it.
        venue.note_set("tclk", lock.statement[2:18], "claimed")
    else:
        venue.note_set("tclk", lock.statement[2:18], "refunded")

    return AgentJobTranscript(
        agent_did=agent_did,
        miner_did=miner_did,
        frames=frames,
        job_id=spec.job_id,
        jobspec_cid=jobspec_cid,
        tclk=lock,
        outcome=outcome,
        revealed=preimage,
        notes={"prompt_commit": prompt_commit.__name__},
    )


def fold_pin1_room(venue: Venue, room: str = PIN_JOBS_ROOM) -> list[Pin1Frame]:
    """Drop unsigned / malformed lines, same fail-closed rule as tclk."""
    out: list[Pin1Frame] = []
    for rec in venue.read(room):
        if not rec.signed:
            continue
        try:
            frame = decode_frame(rec.text)
        except Exception:
            continue
        if rec.did and frame.from_did != rec.did:
            continue
        out.append(frame)
    return out
