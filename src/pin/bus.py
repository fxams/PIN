"""Mock Flop settlement bus: session mempool, escrow, proof-hash blocks, challenges.

This is not a Flop node. It models the native primitives PIN composes:
session escrow, multi-party spend conditions, HTLC, discretionary challenge
(100 FLOP bond), full-stake fraud slash. No general VM, no PIN contract.
"""

from __future__ import annotations

import hashlib
import time
from dataclasses import dataclass, field
from enum import StrEnum

from pin.canonical import pin_hash_hex
from pin.models import FlopSessionRequest


class SessionState(StrEnum):
    POSTED = "posted"
    ACCEPTED = "accepted"
    SETTLED = "settled"
    CANCELLED = "cancelled"
    FRAUD_SLASH = "fraud_slash"
    SLA_REFUND = "sla_refund"


@dataclass
class PostedSession:
    session_id: str
    request: FlopSessionRequest
    agent: str
    miner: str | None = None
    state: SessionState = SessionState.POSTED
    proof_hash: str | None = None
    transcript_root: str | None = None
    escrow_microflop: int = 0
    challenge_upheld: bool = False
    challenge_reason: str | None = None
    sla_refund_fraction_bps: int = 0
    block_height: int | None = None


@dataclass
class FlopBus:
    block_height: int = 0
    miner_stake_flop: int = 10_000
    sessions: dict[str, PostedSession] = field(default_factory=dict)
    da: dict[str, bytes] = field(default_factory=dict)

    def post_session(self, request: FlopSessionRequest, agent: str) -> PostedSession:
        session_id = pin_hash_hex(request.model_dump(mode="json") | {"agent": agent, "t": time.time_ns()})
        posted = PostedSession(
            session_id=session_id,
            request=request,
            agent=agent,
            escrow_microflop=request.fee_microflop,
            state=SessionState.POSTED,
        )
        self.sessions[session_id] = posted
        return posted

    def accept(self, session_id: str, miner: str) -> PostedSession:
        posted = self.sessions[session_id]
        if posted.state != SessionState.POSTED:
            raise RuntimeError("session not in mempool")
        posted.miner = miner
        posted.state = SessionState.ACCEPTED
        return posted

    def cancel_unused(self, session_id: str) -> PostedSession:
        posted = self.sessions[session_id]
        if posted.state not in {SessionState.POSTED, SessionState.ACCEPTED}:
            raise RuntimeError("cannot cancel after settlement")
        posted.state = SessionState.CANCELLED
        posted.escrow_microflop = 0
        return posted

    def settle(self, session_id: str, transcript_root: str) -> PostedSession:
        posted = self.sessions[session_id]
        if posted.state != SessionState.ACCEPTED:
            raise RuntimeError("session not accepted")
        self.block_height += 1
        proof = hashlib.sha256(bytes.fromhex(transcript_root) + self.block_height.to_bytes(8, "big")).hexdigest()
        posted.transcript_root = transcript_root
        posted.proof_hash = proof
        posted.block_height = self.block_height
        posted.state = SessionState.SETTLED
        self.da[f"transcript:{transcript_root}"] = bytes.fromhex(transcript_root)
        return posted

    def challenge(self, session_id: str, reason: str, *, integrity_fail: bool) -> PostedSession:
        """Discretionary challenge. Bond is 100 FLOP. Upheld fraud refunds escrow and slashes miner."""
        posted = self.sessions[session_id]
        if posted.state not in {SessionState.SETTLED, SessionState.ACCEPTED}:
            raise RuntimeError("nothing to challenge")
        posted.challenge_reason = reason
        if integrity_fail:
            posted.challenge_upheld = True
            posted.state = SessionState.FRAUD_SLASH
            posted.escrow_microflop = 0
            self.miner_stake_flop = 0
        return posted

    def sla_refund(self, session_id: str, fraction_bps: int) -> PostedSession:
        """Late but honest work is not Flop fraud. PIN refunds a documented fraction."""
        posted = self.sessions[session_id]
        posted.sla_refund_fraction_bps = fraction_bps
        posted.state = SessionState.SLA_REFUND
        refund = posted.request.fee_microflop * fraction_bps // 10_000
        posted.escrow_microflop = max(0, posted.request.fee_microflop - refund)
        return posted

    def put_da(self, cid: str, blob: bytes) -> None:
        self.da[cid] = blob

    def get_da(self, cid: str) -> bytes:
        return self.da[cid]
