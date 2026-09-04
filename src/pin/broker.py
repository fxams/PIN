"""USDC↔FLOP broker. Isolates FX so Flop can stay FLOP-denominated.

v0: broker is trusted for inventory (PIN-compliant trust trade).
FLOP leg uses native HTLC + session escrow on the mock bus.
USDC leg is recorded as an external rail lock; no Flop contract.
"""

from __future__ import annotations

import hashlib
import secrets
from dataclasses import dataclass, field

from pin.quote import flop_fee_from_usd


@dataclass
class HtlcLock:
    secret_hash: str
    flop_micro: int
    usd_micros: int
    timeout_sec: int
    released: bool = False
    refunded: bool = False
    preimage: str | None = None


@dataclass
class Broker:
    inventory_flop_micro: int
    inventory_usd_micros: int
    fx_mid_usd_micros: int
    fx_buffer_bps: int = 200
    max_fx_move_bps: int = 200
    locks: dict[str, HtlcLock] = field(default_factory=dict)

    def quote_flop_fee(self, usd_micros: int) -> int:
        return flop_fee_from_usd(usd_micros, self.fx_mid_usd_micros, self.fx_buffer_bps)

    def fx_moved(self, new_mid: int) -> bool:
        if self.fx_mid_usd_micros <= 0:
            return True
        move_bps = abs(new_mid - self.fx_mid_usd_micros) * 10_000 // self.fx_mid_usd_micros
        return move_bps > self.max_fx_move_bps

    def open_htlc(self, usd_micros: int, timeout_sec: int = 120) -> tuple[str, HtlcLock, str]:
        flop_micro = self.quote_flop_fee(usd_micros)
        if flop_micro > self.inventory_flop_micro:
            raise RuntimeError("broker insolvent: not enough FLOP inventory")
        if usd_micros > self.inventory_usd_micros:
            # Buyer brings USDC; inventory tracks the broker's float, not the lock.
            pass
        preimage = secrets.token_bytes(32)
        secret_hash = hashlib.sha256(preimage).hexdigest()
        lock = HtlcLock(
            secret_hash=secret_hash,
            flop_micro=flop_micro,
            usd_micros=usd_micros,
            timeout_sec=timeout_sec,
            preimage=preimage.hex(),
        )
        self.inventory_flop_micro -= flop_micro
        self.locks[secret_hash] = lock
        return secret_hash, lock, preimage.hex()

    def release(self, secret_hash: str, preimage_hex: str) -> None:
        lock = self.locks[secret_hash]
        if hashlib.sha256(bytes.fromhex(preimage_hex)).hexdigest() != secret_hash:
            raise ValueError("bad preimage")
        lock.released = True
        lock.preimage = preimage_hex
        self.inventory_usd_micros += lock.usd_micros

    def refund(self, secret_hash: str) -> None:
        lock = self.locks[secret_hash]
        if lock.released:
            raise RuntimeError("cannot refund a released HTLC")
        lock.refunded = True
        self.inventory_flop_micro += lock.flop_micro
