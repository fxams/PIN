from pin.broker import Broker
from pin.quote import flop_fee_from_usd, usd_micros_for_tokens


def test_invoice_is_usd_not_flops():
    usd = usd_micros_for_tokens(1000, 500, 100_000, 300_000)
    # 1000/1e6 * 0.10 + 500/1e6 * 0.30 = 0.0001 + 0.00015 = 0.00025 USD = 250 micros
    assert usd == 250
    fee = flop_fee_from_usd(usd, fx_mid_usd_micros=50_000, buffer_bps=200)
    assert fee > 0


def test_fx_spike_declines():
    broker = Broker(
        inventory_flop_micro=1_000_000_000,
        inventory_usd_micros=1_000_000_000,
        fx_mid_usd_micros=50_000,
        max_fx_move_bps=200,
    )
    assert broker.fx_moved(50_500) is False  # 1%
    assert broker.fx_moved(52_000) is True  # 4%


def test_htlc_refund_on_abort():
    broker = Broker(
        inventory_flop_micro=1_000_000_000,
        inventory_usd_micros=1_000_000_000,
        fx_mid_usd_micros=50_000,
    )
    before = broker.inventory_flop_micro
    secret, lock, _pre = broker.open_htlc(1_000_000)
    assert broker.inventory_flop_micro < before
    broker.refund(secret)
    assert broker.inventory_flop_micro == before
    assert lock.refunded is True
