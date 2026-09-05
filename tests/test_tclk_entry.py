from pin.did import new_agent_identity
from pin.frames import decode_frame
from pin.identity import TCLK_OFFERS_ROOM
from pin.lab import PinLab
from pin.matcher import OperatorMatcher
from pin.tclk_deal import fold_tclk_room
from pin.tclk_entry import build_pin_bounty, parse_pin_context, pin_job_context, resolve_pin_artifact
from pin.tclk_frames import encode_frame as encode_tclk
from pin.tclk_frames import make_offer, pin_job


def _operator(tmp_path):
    from pin.identity import init_identity

    return init_identity(tmp_path / "op.json")


def test_parse_pin_context_hex_key_and_garbage():
    aid = "ab" * 32
    assert parse_pin_context(aid) == (aid, None)
    assert parse_pin_context("0x" + aid) == (aid, None)
    assert parse_pin_context(f"artifact:{aid}") == (aid, None)
    assert parse_pin_context("8b-stock") == (None, "8b-stock")
    assert parse_pin_context("key:8b-stock") == (None, "8b-stock")
    assert parse_pin_context("") == (None, None)
    assert parse_pin_context("not a thing") == (None, None)


def test_resolve_pin_artifact_named_and_hex():
    lab = PinLab()
    artifact = lab.named_artifacts["8b-stock"]
    assert resolve_pin_artifact(lab, "8b-stock") == (artifact.artifact_id, "8b-stock")
    assert resolve_pin_artifact(lab, "key:8b-stock") == (artifact.artifact_id, "8b-stock")
    assert resolve_pin_artifact(lab, artifact.artifact_id) == (artifact.artifact_id, "8b-stock")
    assert resolve_pin_artifact(lab, "ab" * 32) is None
    assert resolve_pin_artifact(lab, None) is None


def test_pin_job_context_and_bounty_include_context():
    lab = PinLab()
    artifact = lab.named_artifacts["8b-stock"]
    assert pin_job_context(artifact_id=artifact.artifact_id) == artifact.artifact_id
    assert pin_job_context(artifact_key="8b-stock") == "key:8b-stock"
    job = pin_job("aa" * 32, artifact_key="8b-stock")
    assert job == {"id": "aa" * 32, "proto": "pin", "context": "key:8b-stock"}
    _, did, _ = new_agent_identity()
    offer = build_pin_bounty(
        from_did=did,
        context=artifact.artifact_id,
        job_id="bb" * 32,
        now_ms=1_750_003_600_000,
        nonce="cafebabedeadbeef",
    )
    assert offer["job"] == {"id": "bb" * 32, "proto": "pin", "context": artifact.artifact_id}
    assert offer["rails"] == ["paper"]


def test_matcher_fills_tclk_offer_without_pin1_want(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    artifact = lab.named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbeef",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job("cc" * 32, artifact_id=artifact.artifact_id),
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(offer), signed=True, did=agent_did)
    filled = matcher.step()
    assert len(filled.quotes) == 1
    assert filled.quotes[0].startswith("pin1 ")
    quote = decode_frame(filled.quotes[0])
    assert quote.tclk_ref == offer["id"]
    assert quote.artifact_id == artifact.artifact_id
    assert len(filled.leaf0) == 1
    assert len(filled.receipts) == 1
    assert "paid" in filled.receipts[0]
    assert len(filled.tclk_accepts) == 1
    assert len(filled.tclk_settles) == 2
    assert not any(rec.text.startswith("tclk1 ") for rec in lab.venue.read("pin"))
    assert not any(rec.text.startswith("pin1 ") for rec in lab.venue.read(TCLK_OFFERS_ROOM))
    money = fold_tclk_room(lab.venue)
    assert [frame["type"] for frame in money] == ["offer", "accept", "reveal", "receipt"]
    again = matcher.step()
    assert again.quotes == []
    assert again.receipts == []
    assert again.tclk_accepts == []


def test_matcher_ignores_kibble_proto_on_tclk_offers(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    _, agent_did, _ = new_agent_identity()
    offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbee1",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job={"proto": "kibble", "id": "dd" * 32, "context": "8b-stock"},
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(offer), signed=True, did=agent_did)
    step = matcher.step()
    assert step.quotes == []
    assert step.receipts == []
    assert step.tclk_accepts == []


def test_matcher_skips_pin_offer_without_context(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    _, agent_did, _ = new_agent_identity()
    offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbee2",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job("ee" * 32),
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(offer), signed=True, did=agent_did)
    step = matcher.step()
    assert step.quotes == []
    assert step.receipts == []
    assert not any(s.startswith("tclk-unknown-artifact") for s in step.skipped)


def test_matcher_skips_unknown_tclk_context(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    _, agent_did, _ = new_agent_identity()
    offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbee3",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job("ff" * 32, context="ab" * 32),
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(offer), signed=True, did=agent_did)
    step = matcher.step()
    assert step.quotes == []
    assert any(s.startswith("tclk-unknown-artifact") for s in step.skipped)


def test_cli_offer_preview(tmp_path):
    from typer.testing import CliRunner

    from pin.cli import app
    from pin.identity import init_identity

    init_identity(tmp_path / "buyer.json")
    runner = CliRunner()
    result = runner.invoke(app, ["offer", "--path", str(tmp_path / "buyer.json")])
    assert result.exit_code == 0
    assert "seed" not in result.stdout
    assert '"proto": "pin"' in result.stdout
    assert '"context"' in result.stdout
    assert "tclk1 " in result.stdout
    assert '"live": false' in result.stdout
    bad = runner.invoke(app, ["offer", "--artifact", "nope", "--path", str(tmp_path / "buyer.json")])
    assert bad.exit_code == 2


def test_cli_match_live_fills_tclk_offer_with_context(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pin.cli import app
    from pin.identity import init_identity

    ident = init_identity(tmp_path / "op.json")
    artifact = PinLab().named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbee4",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job("11" * 32, artifact_id=artifact.artifact_id),
    )
    line = encode_tclk(offer)
    posts: list[tuple[str, dict]] = []

    class _Resp:
        def __init__(self, payload):
            self.status_code = 200
            self.text = "ok"
            self._payload = payload

        def json(self):
            return self._payload

        def raise_for_status(self):
            return None

    class _Client:
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __enter__(self):
            return self

        def __exit__(self, *args) -> None:
            return None

        def get(self, url: str, params=None):
            if url.endswith("/r/tclk-offers"):
                return _Resp(
                    {
                        "messages": [
                            {"seq": 1, "from": agent_did, "sig": "x", "text": line},
                        ]
                    }
                )
            return _Resp({"messages": []})

        def post(self, url: str, json=None):
            posts.append((url, json or {}))
            return _Resp({})

    monkeypatch.setattr("pin.technocore_client.httpx.Client", _Client)
    runner = CliRunner()
    result = runner.invoke(app, ["match", "--live", "--path", str(tmp_path / "op.json")])
    assert result.exit_code == 0
    pin_posts = [body.get("text", "") for url, body in posts if url.endswith("/r/pin")]
    tclk_posts = [body.get("text", "") for url, body in posts if url.endswith("/r/tclk-offers")]
    assert any(text.startswith("pin1 ") and "quote" in text for text in pin_posts)
    assert any("leaf0" in text for text in pin_posts)
    assert any("receipt" in text for text in pin_posts)
    assert any(text.startswith("tclk1 ") and "accept" in text for text in tclk_posts)
    assert any('"type":"reveal"' in text for text in tclk_posts)
    assert not any(text.startswith("tclk1 ") for text in pin_posts)
    assert not any(text.startswith("pin1 ") for text in tclk_posts)
    assert ident.did in result.stdout
