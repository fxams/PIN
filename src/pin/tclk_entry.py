"""tclk-first PIN entry — same discovery path as kibble.

Agents already watch `/r/tclk-offers`. A signed `tclk1` offer with
`job.proto=pin` and `job.context` naming a published artifact is a PIN want.
The matcher quotes and fills on `/r/pin` and settles on `tclk-offers`.
The agent does not have to know `/r/pin` exists.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from pin.lab import PinLab

ARTIFACT_HEX_RE = re.compile(r"^(?:0x)?([0-9a-f]{64})$", re.IGNORECASE)
KEY_RE = re.compile(r"^(?:key:)?([a-z0-9][a-z0-9._-]{0,31})$", re.IGNORECASE)


def parse_pin_context(context: str | None) -> tuple[str | None, str | None]:
    """Split job.context into (artifact_id, artifact_key). Either may be None."""
    raw = (context or "").strip()
    if not raw:
        return None, None
    hex_match = ARTIFACT_HEX_RE.match(raw)
    if hex_match:
        return hex_match.group(1).lower(), None
    if raw.lower().startswith("artifact:"):
        rest = raw.split(":", 1)[1].strip()
        hex_match = ARTIFACT_HEX_RE.match(rest)
        if hex_match:
            return hex_match.group(1).lower(), None
    key_match = KEY_RE.match(raw)
    if key_match:
        return None, key_match.group(1)
    return None, None


def resolve_pin_artifact(lab: PinLab, context: str | None) -> tuple[str, str] | None:
    """Return (artifact_id, artifact_key) if the lab lists that artifact."""
    artifact_id, key = parse_pin_context(context)
    if key and key in lab.named_artifacts:
        return lab.named_artifacts[key].artifact_id, key
    if artifact_id:
        for name, artifact in lab.named_artifacts.items():
            if artifact.artifact_id == artifact_id:
                return artifact_id, name
    return None


def pin_job_context(*, artifact_id: str | None = None, artifact_key: str | None = None) -> str:
    if artifact_id:
        return artifact_id
    if artifact_key:
        return f"key:{artifact_key}"
    raise ValueError("artifact_id or artifact_key required")


def build_pin_bounty(
    *,
    from_did: str,
    context: str,
    job_id: str | None = None,
    amount: str = "100",
    now_ms: int | None = None,
    nonce: str | None = None,
) -> dict[str, Any]:
    """Payer offer on tclk-offers. `context` is the want; matcher fills /r/pin."""
    import secrets
    import time

    from pin.tclk_deal import paper_windows
    from pin.tclk_frames import make_offer, pin_job

    now = now_ms if now_ms is not None else int(time.time() * 1000)
    expires_ms, claim_by_ms, refund_after_ms = paper_windows(now)
    bounty_id = job_id or secrets.token_hex(32)
    return make_offer(
        from_did=from_did,
        amount=amount,
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce=nonce or secrets.token_hex(8),
        expires_ms=expires_ms,
        claim_by_ms=claim_by_ms,
        refund_after_ms=refund_after_ms,
        role="payer",
        job=pin_job(bounty_id, context=context),
    )
