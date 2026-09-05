import json

from typer.testing import CliRunner

from pin.cli import app
from pin.identity import TCLK_OFFERS_ROOM, init_identity
from pin.lab import PinLab
from pin.matcher import OperatorMatcher
from pin.roster import build_pair_frames, init_roster, pair_book
from pin.watch import run_watch


def test_watch_caps_fills_per_tick(tmp_path):
    lab = PinLab()
    ident = init_identity(tmp_path / "op.json")
    agents = init_roster(tmp_path / "roster", buyers=2, sellers=2)
    for i, (buyer, seller) in enumerate(pair_book(agents, pairs=2)):
        key = "8b-stock" if i == 0 else "70b-stock"
        offer_line, quote_line, _offer = build_pair_frames(buyer, seller, artifact_key=key)
        lab.venue.say(TCLK_OFFERS_ROOM, "buyer", offer_line, signed=True, did=buyer.did)
        lab.venue.say("pin", "seller", quote_line, signed=True, did=seller.did)
    matcher = OperatorMatcher(lab, ident)
    ticks = list(
        run_watch(
            ident,
            lab=lab,
            matcher=matcher,
            live=False,
            interval=0,
            max_jobs=1,
            ticks=3,
            sleep=lambda *_a, **_k: None,
        )
    )
    assert len(ticks) == 3
    assert ticks[0].receipts == 1
    assert ticks[1].receipts == 1
    assert ticks[2].receipts == 0
    assert ticks[2].posted == 0
    assert all(t.holds_value is False for t in ticks)
    assert all(t.as_dict()["seed"] is False for t in ticks)


def test_watch_cli_never_prints_seed(tmp_path):
    init_identity(tmp_path / "op.json")
    runner = CliRunner()
    result = runner.invoke(
        app,
        ["watch", "--ticks", "1", "--interval", "0", "--path", str(tmp_path / "op.json")],
    )
    assert result.exit_code == 0
    payload = json.loads(result.stdout)
    assert payload["tick"] == 1
    assert payload["seed"] is False
    secret = json.loads((tmp_path / "op.json").read_text(encoding="utf-8"))["seed"]
    assert secret not in result.stdout
    assert "tclk-offers" not in result.stdout or payload["live"] is False


def test_watch_survives_fetch_error(tmp_path, monkeypatch):
    import httpx

    ident = init_identity(tmp_path / "op.json")

    def _boom(*_a, **_k):
        raise httpx.ReadTimeout("slow")

    monkeypatch.setattr("pin.watch.fetch_room_json", _boom)
    ticks = list(
        run_watch(ident, live=True, interval=0, ticks=1, sleep=lambda *_a, **_k: None)
    )
    assert ticks[0].fetch_error == "ReadTimeout"
    assert ticks[0].receipts == 0
