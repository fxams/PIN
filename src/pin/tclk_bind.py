"""Bind a tclk-shaped hashlock to a PIN receipt.

tclk/1 is explicit: a bare HTLC assures the payee the money exists; it does
not assure the payer that work arrived. PIN closes that gap by revealing the
preimage only after leaf 0 + watcher pin_ok.

The live rehearsal rail is `paper` (holds no value). `flop-htlc` is reserved
until flop-labs ships it. Money frames live on `tclk-offers`, never in `pin-jobs`.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass

from pin.models import Receipt


@dataclass
class TclkLock:
    statement: str
    preimage: str
    rail: str
    flop_micro: int
    revealed: bool = False
    refunded: bool = False


def mint_hashlock(flop_micro: int, rail: str = "paper") -> TclkLock:
    preimage = secrets.token_bytes(32)
    statement = "0x" + hashlib.sha256(preimage).hexdigest()
    return TclkLock(
        statement=statement,
        preimage="0x" + preimage.hex(),
        rail=rail,
        flop_micro=flop_micro,
    )


def pin_ok(receipt: Receipt | None) -> bool:
    return bool(receipt and receipt.paid and not receipt.sla_miss and not receipt.notes)


def maybe_reveal(lock: TclkLock, receipt: Receipt | None) -> str | None:
    """Reveal the tclk secret only when the PIN JobSpec actually ran."""
    if not pin_ok(receipt):
        lock.refunded = True
        return None
    lock.revealed = True
    return lock.preimage
