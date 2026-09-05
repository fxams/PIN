"""pin1 frames: Technocore room messages that coordinate a PIN job.

Same split as tclk/1: the room orders what was agreed; Flop holds the session
escrow; tclk paper (then flop-htlc, when shipped) holds the money. Technocore
settles nothing.

A frame is the 5 chars `pin1 ` plus canonical JSON (sorted keys, ASCII).
Unsigned frames are data, not commitments — folders drop them.
"""

from __future__ import annotations

import json
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from pin.canonical import canonical_dumps

PIN1_PREFIX = "pin1 "
FRAME_TYPES = ("want", "quote", "accept", "leaf0", "receipt", "challenge")


class PinFrameError(ValueError):
    pass


class Pin1Frame(BaseModel):
    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    v: Literal["pin/1"] = "pin/1"
    type: Literal["want", "quote", "accept", "leaf0", "receipt", "challenge"]
    from_did: str = Field(alias="from")
    nonce: str
    artifact_id: str | None = None
    tier: str | None = None
    sla: str | None = None
    n_in: int | None = None
    n_out: int | None = None
    max_usd: int | None = None
    ref: str | None = None
    offer_id: str | None = None
    usd_micros: int | None = None
    flop_fee: int | None = None
    ttl_sec: int | None = None
    job_id: str | None = None
    jobspec_cid: str | None = None
    tclk_ref: str | None = None
    rail: str | None = None
    statement: str | None = None
    leaf0_sig: str | None = None
    t_accept: int | None = None
    transcript_root: str | None = None
    flop_proof_hash: str | None = None
    paid: bool | None = None
    sla_miss: bool | None = None
    integrity_fail: bool | None = None
    findings: list[str] | None = None
    status: str | None = None

    def wire_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json", by_alias=True, exclude_none=True)
        return data


def encode_frame(frame: Pin1Frame) -> str:
    body = canonical_dumps(frame.wire_dict()).decode("ascii")
    line = PIN1_PREFIX + body
    if len(line) > 4096:
        raise PinFrameError("frame exceeds Technocore 4096-char message cap")
    return line


def decode_frame(line: str) -> Pin1Frame:
    raw = line.strip()
    if not raw.startswith(PIN1_PREFIX):
        raise PinFrameError("not a pin1 frame")
    try:
        obj = json.loads(raw[len(PIN1_PREFIX) :])
    except json.JSONDecodeError as exc:
        raise PinFrameError("malformed JSON") from exc
    if not isinstance(obj, dict):
        raise PinFrameError("frame body must be an object")
    try:
        return Pin1Frame.model_validate(obj)
    except Exception as exc:
        raise PinFrameError(str(exc)) from exc


def is_pin1(line: str) -> bool:
    return line.strip().startswith(PIN1_PREFIX)
