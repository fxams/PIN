import json

import httpx

from pin.frames import decode_frame
from pin.identity import TCLK_OFFERS_ROOM, init_identity
from pin.lab import PinLab
from pin.matcher import OperatorMatcher
from pin.roster import (
    ROSTER_NOTE_PATH,
    RosterAgent,
    build_pair_frames,
    did_note_parts,
    init_roster,
    load_roster,
    pair_book,
    pair_key,
    preview_roster,
    publish_roster,
    roster_note_body,
    save_publish_progress,
)
from pin.tclk_frames import decode_frame as decode_tclk


def test_init_roster_assigns_buyers_and_sellers(tmp_path):
    root = tmp_path / "roster"
    first = init_roster(root, buyers=2, sellers=2)
    assert len(first) == 4
    assert sum(1 for a in first if a.role == "buyer") == 2
    assert sum(1 for a in first if a.role == "seller") == 2
    again = init_roster(root, buyers=2, sellers=2)
    assert {a.did for a in again} == {a.did for a in first}
    grown = init_roster(root, buyers=3, sellers=3)
    assert sum(1 for a in grown if a.role == "buyer") == 3
    assert sum(1 for a in grown if a.role == "seller") == 3
    loaded = load_roster(root)
    assert "seed" not in json.dumps(json.loads((root / "manifest.json").read_text(encoding="utf-8")))
    assert all(a.role in {"buyer", "seller"} for a in loaded)


def test_pair_frames_are_tclk_offer_and_pin_quote():
    from pin.crypto import generate_miner_key, private_key_hex
    from pin.identity import identity_from_seed

    buyer = RosterAgent(identity_from_seed(private_key_hex(generate_miner_key()), source="b"), "buyer")
    seller = RosterAgent(identity_from_seed(private_key_hex(generate_miner_key()), source="s"), "seller")
    offer_line, quote_line, offer = build_pair_frames(buyer, seller, offer_nonce="cafebabedead0001")
    assert offer_line.startswith("tclk1 ")
    assert quote_line.startswith("pin1 ")
    parsed = decode_tclk(offer_line)
    assert parsed["job"]["proto"] == "pin"
    assert parsed["job"]["context"]
    assert parsed["from"] == buyer.did
    quote = decode_frame(quote_line)
    assert quote.type == "quote"
    assert quote.from_did == seller.did
    assert quote.tclk_ref == offer["id"]
    assert quote.rail == "paper"


def test_roster_note_fits_50_50():
    agents = [
        RosterAgent(
            ident=type("I", (), {"did": f"did:key:z6Mk{'A' * 43}{i:01d}", "fingerprint": f"{i:016x}"})(),
            role="buyer" if i < 50 else "seller",
        )
        for i in range(100)
    ]
    text = roster_note_body(agents)
    assert len(text) <= 8192
    body = json.loads(text)
    assert body["n_buyers"] == 50
    assert body["n_sellers"] == 50
    assert body["money_room"] == "tclk-offers"


def test_did_note_parts_match_operator_convention():
    assert did_note_parts("304d8415d5273698") == ("did-30", "4d8415d5273698")


def test_preview_and_cli_hide_seeds(tmp_path):
    from typer.testing import CliRunner

    from pin.cli import app

    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    agents = init_roster(root, buyers=1, sellers=1)
    preview = preview_roster(agents, op, pairs=1)
    assert preview["pairs"] == 1
    assert preview["n_buyers"] == 1
    assert preview["roster_path"] == ROSTER_NOTE_PATH
    assert preview["sample"][0]["tclk_line"].startswith("tclk1 ")
    assert preview["sample"][0]["pin_quote"].startswith("pin1 ")
    assert "seed" not in json.dumps(preview)
    runner = CliRunner()
    shown = runner.invoke(app, ["roster", "show", "--roster-dir", str(root)])
    assert shown.exit_code == 0
    assert "seed" not in shown.stdout
    assert "buyer" in shown.stdout
    pub = runner.invoke(
        app,
        ["roster", "publish", "--roster-dir", str(root), "--path", str(tmp_path / "op.json")],
    )
    assert pub.exit_code == 0
    assert "seed" not in pub.stdout
    assert "tclk1 " in pub.stdout


def test_publish_roster_buyers_on_tclk_sellers_on_pin(tmp_path, monkeypatch):
    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    agents = init_roster(root, buyers=1, sellers=1)
    urls: list[str] = []
    bodies: list[dict] = []

    class _Resp:
        def __init__(self, status: int = 200) -> None:
            self.status_code = status
            self.text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, method: str, url: str, json=None, **kwargs):
            urls.append(url)
            bodies.append(json or {})
            return _Resp()

    monkeypatch.setattr("pin.roster.httpx.Client", _Client)
    result = publish_roster(agents, op, pairs=1, base="https://technocore.chat", roster_dir=root)
    assert result.buyer_offers_ok == 1
    assert result.seller_quotes_ok == 1
    assert result.notes_ok == 2
    assert any(u.endswith("/kv/pin/roster") for u in urls)
    assert any(u.endswith("/r/tclk-offers") for u in urls)
    assert any(u.endswith("/r/pin") for u in urls)
    assert not any("/r/kibble" in u or "/r/lobby" in u for u in urls)
    tclk_texts = [b.get("text", "") for u, b in zip(urls, bodies, strict=False) if u.endswith("/r/tclk-offers")]
    pin_texts = [b.get("text", "") for u, b in zip(urls, bodies, strict=False) if u.endswith("/r/pin")]
    assert any(t.startswith("tclk1 ") for t in tclk_texts)
    assert any(t.startswith("pin1 ") for t in pin_texts)
    assert not any(t.startswith("pin1 ") for t in tclk_texts)
    assert not any(t.startswith("tclk1 ") for t in pin_texts)
    assert "seed" not in str(urls)
    assert not any((b.get("text") or "").startswith("PIN buyer") for b in bodies)
    assert not any((b.get("text") or "").startswith("PIN seller") for b in bodies)


def test_publish_limited_pairs_skips_leftover_cards(tmp_path, monkeypatch):
    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    agents = init_roster(root, buyers=2, sellers=2)
    texts: list[str] = []

    class _Resp:
        def __init__(self, status: int = 200) -> None:
            self.status_code = status
            self.text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, method: str, url: str, json=None, **kwargs):
            texts.append((json or {}).get("text") or (json or {}).get("value") or "")
            return _Resp()

    monkeypatch.setattr("pin.roster.httpx.Client", _Client)
    result = publish_roster(agents, op, pairs=1, base="https://technocore.chat", roster_dir=root)
    assert result.buyer_offers_ok == 1
    assert result.seller_quotes_ok == 1
    assert sum(1 for t in texts if t.startswith("tclk1 ")) == 1
    assert sum(1 for t in texts if t.startswith("pin1 ")) == 1
    assert not any(t.startswith("PIN buyer") or t.startswith("PIN seller") for t in texts)


def test_matcher_fills_seller_quote_without_requoting(tmp_path):
    lab = PinLab()
    ident = init_identity(tmp_path / "op.json")
    agents = init_roster(tmp_path / "roster", buyers=1, sellers=1)
    buyer, seller = pair_book(agents, pairs=1)[0]
    offer_line, quote_line, offer = build_pair_frames(buyer, seller)
    lab.venue.say(TCLK_OFFERS_ROOM, "buyer", offer_line, signed=True, did=buyer.did)
    lab.venue.say("pin", "seller", quote_line, signed=True, did=seller.did)
    step = OperatorMatcher(lab, ident).step()
    assert step.quotes == []
    assert len(step.receipts) == 1
    assert "paid" in step.receipts[0]
    assert step.tclk_accepts
    receipt = decode_frame(step.receipts[0])
    assert receipt.tclk_ref == offer["id"]


def test_publish_retries_timeouts_then_posts(tmp_path, monkeypatch):
    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    agents = init_roster(root, buyers=1, sellers=1)
    hits = {"n": 0}

    class _Resp:
        def __init__(self, status: int = 200) -> None:
            self.status_code = status
            self.text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, method: str, url: str, json=None, **kwargs):
            hits["n"] += 1
            if hits["n"] == 1:
                raise httpx.ReadTimeout("slow")
            if hits["n"] == 2:
                return _Resp(503)
            return _Resp()

    monkeypatch.setattr("pin.roster.httpx.Client", _Client)
    monkeypatch.setattr("pin.roster.time.sleep", lambda *_a, **_k: None)
    result = publish_roster(agents, op, pairs=1, base="https://technocore.chat", roster_dir=root)
    assert result.buyer_offers_ok == 1
    assert result.seller_quotes_ok == 1
    assert result.roster_status == 200
    assert hits["n"] >= 3


def test_publish_resumes_completed_pairs(tmp_path, monkeypatch):
    op = init_identity(tmp_path / "op.json")
    root = tmp_path / "roster"
    agents = init_roster(root, buyers=1, sellers=1)
    buyer, seller = pair_book(agents, pairs=1)[0]
    save_publish_progress(
        root,
        {"notes": [buyer.fingerprint, seller.fingerprint], "pairs": [pair_key(buyer, seller)], "offers": ["0xabc"]},
    )
    urls: list[str] = []

    class _Resp:
        def __init__(self, status: int = 200) -> None:
            self.status_code = status
            self.text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def request(self, method: str, url: str, json=None, **kwargs):
            urls.append(url)
            return _Resp()

    monkeypatch.setattr("pin.roster.httpx.Client", _Client)
    result = publish_roster(agents, op, pairs=1, base="https://technocore.chat", roster_dir=root)
    assert result.skipped_pairs == 1
    assert result.buyer_offers_ok == 1
    assert result.seller_quotes_ok == 1
    assert not any(u.endswith("/r/tclk-offers") for u in urls)
    assert not any("/kv/did-" in u for u in urls)
