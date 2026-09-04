from pin.agent_flow import fold_pin1_room, run_agent_job
from pin.identity import TCLK_OFFERS_ROOM
from pin.lab import PinLab
from pin.tclk_deal import fold_tclk_room, open_paper_deal, settle_deal
from pin.tclk_frames import (
    contract_id,
    encode_frame,
    hash_lock_from_preimage,
    make_accept,
    make_lock,
    make_offer,
    make_receipt,
    make_refund,
    make_reveal,
    pin_job,
)
from pin.tclk_paper import PaperStore, decode_paper_note, encode_paper_note, paper_note_path

PAYER = "did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2"
PAYEE = "did:key:z6MkmnyEeEpzJ3zsskp85GCf9XAMZL2jr8eGKsKK9emNxUto"
PREIMAGE = "0x1111111111111111111111111111111111111111111111111111111111111111"
JOB_ID = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
OFFER_LINE = (
    "tclk1 {\"amount\":\"100\",\"asset\":\"PAPER\",\"claimByMs\":1756800000000,"
    "\"expiresMs\":1756713600000,\"from\":\"did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2\","
    "\"id\":\"0x85f8de51c7c5e0592730c93adabba57f2896dddbb1585c71f8abfcc15e2e76bc\","
    "\"job\":{\"id\":\"aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa\",\"proto\":\"pin\"},"
    "\"lock\":\"hash\",\"nonce\":\"9f2c81d04c9e1f7a\",\"rails\":[\"paper\"],"
    "\"refundAfterMs\":1756886400000,\"role\":\"payer\",\"type\":\"offer\"}"
)
ACCEPT_LINE = (
    "tclk1 {\"contract\":\"0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982\","
    "\"from\":\"did:key:z6MkmnyEeEpzJ3zsskp85GCf9XAMZL2jr8eGKsKK9emNxUto\",\"nonce\":\"abcdefff01234567\","
    "\"ref\":\"0x85f8de51c7c5e0592730c93adabba57f2896dddbb1585c71f8abfcc15e2e76bc\","
    "\"statement\":\"0x02d449a31fbb267c8f352e9968a79e3e5fc95c1bbeaa502fd6454ebde5a4bedc\",\"type\":\"accept\"}"
)
LOCK_LINE = (
    "tclk1 {\"contract\":\"0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982\","
    "\"from\":\"did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2\",\"rail\":\"paper\","
    "\"ref\":\"0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982\",\"type\":\"lock\"}"
)
REVEAL_LINE = (
    "tclk1 {\"contract\":\"0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982\","
    "\"from\":\"did:key:z6MkmnyEeEpzJ3zsskp85GCf9XAMZL2jr8eGKsKK9emNxUto\","
    "\"secret\":\"0x1111111111111111111111111111111111111111111111111111111111111111\",\"type\":\"reveal\"}"
)


def _golden_offer():
    return make_offer(
        from_did=PAYER,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="9f2c81d04c9e1f7a",
        expires_ms=1756713600000,
        claim_by_ms=1756800000000,
        refund_after_ms=1756886400000,
        job=pin_job(JOB_ID),
    )


def test_golden_offer_and_accept_match_official_lib():
    secret, statement = hash_lock_from_preimage(PREIMAGE)
    assert secret == PREIMAGE
    assert statement == "0x02d449a31fbb267c8f352e9968a79e3e5fc95c1bbeaa502fd6454ebde5a4bedc"
    offer = _golden_offer()
    assert offer["id"] == "0x85f8de51c7c5e0592730c93adabba57f2896dddbb1585c71f8abfcc15e2e76bc"
    assert encode_frame(offer) == OFFER_LINE
    accept = make_accept(offer=offer, from_did=PAYEE, statement=statement, nonce="abcdefff01234567")
    assert accept["contract"] == "0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982"
    assert encode_frame(accept) == ACCEPT_LINE
    lock = make_lock(contract=accept["contract"], from_did=PAYER, ref=accept["contract"], rail="paper")
    assert encode_frame(lock) == LOCK_LINE
    reveal = make_reveal(contract=accept["contract"], from_did=PAYEE, secret=PREIMAGE)
    assert encode_frame(reveal) == REVEAL_LINE
    core = {key: accept[key] for key in ("from", "nonce", "ref", "statement")}
    assert contract_id(offer, core) == accept["contract"]


def test_paper_note_matches_official_lib():
    contract = "0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982"
    statement = "0x02d449a31fbb267c8f352e9968a79e3e5fc95c1bbeaa502fd6454ebde5a4bedc"
    store = PaperStore()
    locked = store.lock(contract, statement, 1756886400000, now_ms=1756713600000)
    assert paper_note_path(contract) == ("tclk-paper-d6", "3687a3e1e930ba")
    assert encode_paper_note(locked) == (
        "tclkpaper1 locked hash "
        "0x02d449a31fbb267c8f352e9968a79e3e5fc95c1bbeaa502fd6454ebde5a4bedc 1756886400000"
    )
    claimed = store.claim(contract, PREIMAGE, now_ms=1756713600001)
    assert encode_paper_note(claimed) == (
        "tclkpaper1 claimed hash "
        "0x02d449a31fbb267c8f352e9968a79e3e5fc95c1bbeaa502fd6454ebde5a4bedc 1756886400000 "
        "0x1111111111111111111111111111111111111111111111111111111111111111"
    )
    assert decode_paper_note("garbage") is None


def test_pin_ok_gates_paper_reveal():
    lab = PinLab()
    honest = lab.run_job(lab.default_spec())
    store = PaperStore()
    deal = open_paper_deal(
        payer_did=PAYER,
        payee_did=PAYEE,
        job_id=honest.job_id,
        now_ms=1_750_000_000_000,
        offer_nonce="9f2c81d04c9e1f7a",
        accept_nonce="abcdefff01234567",
        secret=bytes.fromhex(PREIMAGE[2:]),
        expires_ms=1756713600000,
        claim_by_ms=1756800000000,
        refund_after_ms=1756886400000,
        paper=store,
    )
    secret = settle_deal(deal, honest.receipt, now_ms=1_750_000_000_000, paper=store)
    assert secret == PREIMAGE
    assert deal.revealed is True
    assert deal.paper is not None
    assert deal.paper.status == "claimed"

    cheated = lab.run_job(lab.default_spec(artifact_key="70b-stock"), attack="model_swap")
    store2 = PaperStore()
    bad = open_paper_deal(
        payer_did=PAYER,
        payee_did=PAYEE,
        job_id=cheated.job_id,
        now_ms=1_750_000_000_000,
        paper=store2,
    )
    assert settle_deal(bad, cheated.receipt, now_ms=1_750_000_000_000, paper=store2) is None
    assert bad.revealed is False
    assert bad.refunded is True
    assert bad.paper is not None
    assert bad.paper.status == "locked"
    assert not any(line.startswith("tclk1 ") and '"type":"reveal"' in line for line in bad.frames)


def test_agent_job_keeps_tclk_off_pin_jobs():
    lab = PinLab()
    honest = run_agent_job(lab)
    assert honest.revealed
    assert honest.tclk.revealed is True
    assert all(line.startswith("pin1 ") for line in honest.frames)
    assert all(line.startswith("tclk1 ") for line in honest.tclk.frames)
    assert honest.tclk.offer["job"]["proto"] == "pin"
    folded = fold_pin1_room(lab.venue)
    assert [frame.type for frame in folded] == ["want", "quote", "accept", "leaf0", "receipt"]
    money = fold_tclk_room(lab.venue)
    assert [frame["type"] for frame in money] == ["offer", "accept", "lock", "reveal", "receipt"]
    assert lab.venue.read("pin-jobs")
    assert not any(rec.text.startswith("tclk1 ") for rec in lab.venue.read("pin-jobs"))
    assert not any(rec.text.startswith("pin1 ") for rec in lab.venue.read(TCLK_OFFERS_ROOM))

    cheated = run_agent_job(PinLab(), artifact_key="70b-stock", attack="model_swap")
    assert cheated.revealed is None
    assert cheated.tclk.refunded is True
    assert not any('"type":"reveal"' in line for line in cheated.tclk.frames)


def test_cli_tclk_demo_offline_never_prints_seed():
    from typer.testing import CliRunner

    from pin.cli import app

    runner = CliRunner()
    result = runner.invoke(app, ["tclk-demo"])
    assert result.exit_code == 0
    assert "seed" not in result.stdout
    assert "tclk1 " in result.stdout
    assert '"proto": "pin"' in result.stdout


def test_live_paper_demo_mocked_never_prints_seed(tmp_path, monkeypatch):
    from pin.identity import init_identity
    from pin.tclk_deal import run_live_paper_demo

    ident = init_identity(tmp_path / "id.json")
    calls: list[tuple[str, dict]] = []

    class _Resp:
        status_code = 200
        text = "ok"

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def post(self, url: str, json: dict | None = None):
            calls.append((url, json or {}))
            return _Resp()

    monkeypatch.setattr("pin.technocore_client.httpx.Client", _Client)
    result = run_live_paper_demo(ident, base="https://technocore.chat")
    assert result["tclk_revealed"] is True
    assert result["holds_value"] is False
    assert result["job"]["proto"] == "pin"
    assert result["rail"] == "paper"
    assert "seed" not in str(result)
    assert "seed" not in str(calls)
    rooms = [url for url, _ in calls if url.endswith("/r/tclk-offers")]
    notes = [url for url, _ in calls if "/kv/tclk-paper-" in url]
    assert rooms
    assert notes
    offer_posts = [payload["text"] for url, payload in calls if payload.get("text", "").startswith("tclk1 ")]
    assert any('"type":"offer"' in text and '"proto":"pin"' in text for text in offer_posts)
    assert any('"type":"reveal"' in text for text in offer_posts)
    assert not any(text.startswith("pin1 ") for text in offer_posts)


def test_refund_and_receipt_builders():
    contract = "0xd63687a3e1e930babaaf38cd671fb0038382a951f133c093355251a2c116d982"
    refund = make_refund(contract=contract, from_did=PAYER, reason="pin-not-ok")
    assert encode_frame(refund).startswith("tclk1 ")
    receipt = make_receipt(contract=contract, from_did=PAYEE, outcome="claimed", rail="paper", ref=contract)
    assert '"outcome":"claimed"' in encode_frame(receipt)
