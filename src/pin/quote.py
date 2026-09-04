"""PIN quote sheet: tokens × artifact × tier, USD micros. FLOPs field is not the price."""

from __future__ import annotations

import math
import time

from pin.canonical import pin_hash_hex
from pin.models import Quote, QuoteRequest, SlaClass

QUOTE_TTL_SEC = {
    SlaClass.INTERACTIVE: 15,
    SlaClass.STANDARD: 60,
    SlaClass.BATCH: 300,
}


def usd_micros_for_tokens(n_in: int, n_out: int, usd_per_mtok_in: int, usd_per_mtok_out: int) -> int:
    return (n_in * usd_per_mtok_in + n_out * usd_per_mtok_out) // 1_000_000


def flop_fee_from_usd(usd_micros: int, fx_mid_usd_micros: int, buffer_bps: int) -> int:
    """Convert a USD invoice to a FLOP session fee with a short-dated buffer.

    fx_mid_usd_micros is USD micros per 1 FLOP (e.g. 50_000 = $0.05/FLOP).
    Result is microFLOP.
    """
    if fx_mid_usd_micros <= 0:
        raise ValueError("fx mid must be positive")
    buffered = usd_micros * (10_000 + buffer_bps)
    # usd_micros / (usd per FLOP) * 1e6 microFLOP, with bps buffer
    return math.ceil(buffered * 1_000_000 / (fx_mid_usd_micros * 10_000))


class QuoteBook:
    def __init__(
        self,
        prices: dict[str, tuple[int, int]],
        fx_mid_usd_micros: int,
        fx_buffer_bps: int = 200,
    ) -> None:
        self.prices = prices
        self.fx_mid_usd_micros = fx_mid_usd_micros
        self.fx_buffer_bps = fx_buffer_bps
        self._offers: dict[str, Quote] = {}

    def quote(self, request: QuoteRequest) -> Quote:
        if request.artifact_id not in self.prices:
            raise KeyError(f"no PIN price for artifact {request.artifact_id}")
        usd_in, usd_out = self.prices[request.artifact_id]
        usd_micros = usd_micros_for_tokens(request.n_in, request.n_out, usd_in, usd_out)
        flop_fee = flop_fee_from_usd(usd_micros, self.fx_mid_usd_micros, self.fx_buffer_bps)
        ttl = QUOTE_TTL_SEC[request.sla_class]
        offer_id = pin_hash_hex(
            {
                "artifact_id": request.artifact_id,
                "tier": request.tier.value,
                "sla": request.sla_class.value,
                "usd": usd_micros,
                "flop_fee": flop_fee,
                "issued_at": int(time.time()),
            }
        )
        offer = Quote(
            usd_per_mtok_in=usd_in,
            usd_per_mtok_out=usd_out,
            sla_class=request.sla_class,
            tier=request.tier,
            artifact_id=request.artifact_id,
            usd_micros=usd_micros,
            flop_fee=flop_fee,
            ttl_sec=ttl,
            offer_id=offer_id,
            fx_mid_usd_micros=self.fx_mid_usd_micros,
            fx_buffer_bps=self.fx_buffer_bps,
        )
        self._offers[offer_id] = offer
        return offer

    def take(self, offer_id: str) -> Quote:
        offer = self._offers.get(offer_id)
        if offer is None:
            raise KeyError("unknown offer_id")
        return offer
