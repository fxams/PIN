from pin.crypto import private_key_hex
from pin.did import new_agent_identity
from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.identity import PIN_OPERATOR_DID, TCLK_OFFERS_ROOM, identity_from_seed
from pin.lab import PinLab
from pin.matcher import OperatorMatcher, artifact_key_for_id
from pin.tclk_deal import fold_tclk_room
from pin.tclk_frames import encode_frame as encode_tclk
from pin.tclk_frames import make_offer, pin_job


def _operator(tmp_path):
    from pin.identity import init_identity

    return init_identity(tmp_path / "op.json")


def test_matcher_quotes_and_fills_as_operator(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    artifact = lab.named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()

    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd44",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    quoted = matcher.step()
    assert len(quoted.quotes) == 1
    assert ident.did in quoted.quotes[0]
    assert quoted.quotes[0].startswith("pin1 ")
    offer = decode_frame(quoted.quotes[0])
    assert offer.offer_id
    spec = lab.default_spec()
    accept = encode_frame(
        Pin1Frame(
            type="accept",
            from_did=agent_did,
            nonce="ee11ff22aa33bb44",
            offer_id=offer.offer_id,
            job_id=spec.job_id,
            jobspec_cid=spec.job_id,
            rail="paper",
        )
    )
    lab.venue.say("pin", "agent", accept, signed=True, did=agent_did)
    filled = matcher.step()
    assert len(filled.receipts) == 1
    assert "paid" in filled.receipts[0]
    again = matcher.step()
    assert again.quotes == []
    assert again.receipts == []


def test_matcher_fills_ingested_quote_offer_id(tmp_path):
    """A later match process must honor the offer_id already on the tape."""
    lab = PinLab()
    ident = _operator(tmp_path)
    artifact = lab.named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd77",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    first = OperatorMatcher(lab, ident)
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    quoted = first.step()
    offer = decode_frame(quoted.quotes[0])
    later = OperatorMatcher(PinLab(), ident, venue=lab.venue)
    spec = lab.default_spec()
    accept = encode_frame(
        Pin1Frame(
            type="accept",
            from_did=agent_did,
            nonce="ee11ff22aa33bb77",
            offer_id=offer.offer_id,
            job_id=spec.job_id,
            rail="paper",
        )
    )
    lab.venue.say("pin", "agent", accept, signed=True, did=agent_did)
    filled = later.step()
    assert filled.receipts
    assert "accept-unknown-offer" not in filled.skipped


def test_matcher_fills_second_quote_for_same_want(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    artifact = lab.named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd88",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    quote_a = encode_frame(
        Pin1Frame(
            type="quote",
            from_did=ident.did,
            nonce="q1q1q1q1q1q1q1q1",
            artifact_id=artifact.artifact_id,
            ref="aa11bb22cc33dd88",
            offer_id="aa" * 32,
            usd_micros=17,
            flop_fee=347,
            ttl_sec=15,
            rail="paper",
        )
    )
    quote_b = encode_frame(
        Pin1Frame(
            type="quote",
            from_did=ident.did,
            nonce="q2q2q2q2q2q2q2q2",
            artifact_id=artifact.artifact_id,
            ref="aa11bb22cc33dd88",
            offer_id="bb" * 32,
            usd_micros=17,
            flop_fee=347,
            ttl_sec=15,
            rail="paper",
        )
    )
    lab.venue.say("pin", "op", quote_a, signed=True, did=ident.did)
    lab.venue.say("pin", "op", quote_b, signed=True, did=ident.did)
    spec = lab.default_spec()
    accept = encode_frame(
        Pin1Frame(
            type="accept",
            from_did=agent_did,
            nonce="ee11ff22aa33bb88",
            offer_id="bb" * 32,
            job_id=spec.job_id,
            rail="paper",
        )
    )
    lab.venue.say("pin", "agent", accept, signed=True, did=agent_did)
    filled = OperatorMatcher(lab, ident).step()
    assert filled.receipts
    assert "accept-unknown-offer" not in filled.skipped


def test_matcher_skips_unknown_artifact(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="deadbeefdeadbeef",
            artifact_id="ab" * 32,
            tier="T1",
        )
    )
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    step = matcher.step()
    assert step.quotes == []
    assert any(s.startswith("unknown-artifact") for s in step.skipped)


def test_artifact_key_for_id():
    lab = PinLab()
    aid = lab.named_artifacts["8b-stock"].artifact_id
    assert artifact_key_for_id(lab, aid) == "8b-stock"
    assert artifact_key_for_id(lab, "00" * 32) is None


def test_sign_note_roundtrip(tmp_path):
    from pin.technocore_client import sign_note, verify_note

    ident = _operator(tmp_path)
    sig = sign_note(ident, "room-owners", "d-pin", "1757010000001", ident.did)
    assert len(sig) == 86
    assert verify_note(ident, "room-owners", "d-pin", "1757010000001", ident.did, sig)
    assert ident.did != PIN_OPERATOR_DID or ident.did.startswith("did:key:")


def test_matcher_binds_tclk_offer_and_reveals_on_pin_ok(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident)
    artifact = lab.named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd44",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    quoted = matcher.step()
    pin_quote = decode_frame(quoted.quotes[0])
    spec = lab.default_spec()
    tclk_offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbeef",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job(spec.job_id),
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(tclk_offer), signed=True, did=agent_did)
    accept = encode_frame(
        Pin1Frame(
            type="accept",
            from_did=agent_did,
            nonce="ee11ff22aa33bb44",
            offer_id=pin_quote.offer_id,
            job_id=spec.job_id,
            jobspec_cid=spec.job_id,
            tclk_ref=tclk_offer["id"],
            rail="paper",
        )
    )
    lab.venue.say("pin", "agent", accept, signed=True, did=agent_did)
    filled = matcher.step()
    assert len(filled.receipts) == 1
    assert len(filled.tclk_accepts) == 1
    assert len(filled.tclk_settles) == 2
    assert all(line.startswith("tclk1 ") for line in filled.tclk_accepts + filled.tclk_settles)
    assert not any(rec.text.startswith("tclk1 ") for rec in lab.venue.read("pin"))
    assert not any(rec.text.startswith("pin1 ") for rec in lab.venue.read(TCLK_OFFERS_ROOM))
    money = fold_tclk_room(lab.venue)
    assert [frame["type"] for frame in money] == ["offer", "accept", "reveal", "receipt"]
    receipt = decode_frame(filled.receipts[0])
    assert receipt.tclk_ref == tclk_offer["id"]


def test_matcher_does_not_reveal_tclk_on_fraud(tmp_path):
    lab = PinLab()
    ident = _operator(tmp_path)
    matcher = OperatorMatcher(lab, ident, attack="model_swap")
    artifact = lab.named_artifacts["70b-stock"]
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd55",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    lab.venue.say("pin", "agent", want, signed=True, did=agent_did)
    quoted = matcher.step()
    pin_quote = decode_frame(quoted.quotes[0])
    spec = lab.default_spec(artifact_key="70b-stock")
    tclk_offer = make_offer(
        from_did=agent_did,
        amount="100",
        asset="PAPER",
        lock="hash",
        rails=["paper"],
        nonce="cafebabedeadbee0",
        expires_ms=1_750_003_600_000,
        claim_by_ms=1_750_086_400_000,
        refund_after_ms=1_750_172_800_000,
        job=pin_job(spec.job_id),
    )
    lab.venue.say(TCLK_OFFERS_ROOM, "agent", encode_tclk(tclk_offer), signed=True, did=agent_did)
    accept = encode_frame(
        Pin1Frame(
            type="accept",
            from_did=agent_did,
            nonce="ee11ff22aa33bb55",
            offer_id=pin_quote.offer_id,
            job_id=spec.job_id,
            tclk_ref=tclk_offer["id"],
            rail="paper",
        )
    )
    lab.venue.say("pin", "agent", accept, signed=True, did=agent_did)
    filled = matcher.step()
    assert filled.tclk_accepts
    assert filled.tclk_settles == []
    assert "tclk-no-reveal" in filled.skipped
    assert not any('"type":"reveal"' in line for line in filled.tclk_accepts)


def test_cli_match_live_posts_tclk_only_to_tclk_offers(tmp_path, monkeypatch):
    from typer.testing import CliRunner

    from pin.cli import app
    from pin.identity import init_identity

    ident = init_identity(tmp_path / "op.json")
    artifact = PinLab().named_artifacts["8b-stock"]
    _, agent_did, _ = new_agent_identity()
    want = encode_frame(
        Pin1Frame(
            type="want",
            from_did=agent_did,
            nonce="aa11bb22cc33dd66",
            artifact_id=artifact.artifact_id,
            tier="T1",
            sla="interactive",
            n_in=32,
            n_out=48,
            max_usd=10_000_000,
        )
    )
    gets: list[str] = []
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
            gets.append(url)
            if url.endswith("/r/pin"):
                return _Resp(
                    {
                        "messages": [
                            {"seq": 1, "from": agent_did, "sig": "x", "text": want},
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
    assert "seed" not in result.stdout
    assert any(url.endswith("/r/pin") for url in gets)
    assert any(url.endswith("/r/tclk-offers") for url in gets)
    pin_posts = [body.get("text", "") for url, body in posts if url.endswith("/r/pin")]
    tclk_posts = [body.get("text", "") for url, body in posts if url.endswith("/r/tclk-offers")]
    assert pin_posts
    assert all(text.startswith("pin1 ") for text in pin_posts)
    assert not any(text.startswith("tclk1 ") for text in pin_posts)
    assert not any(text.startswith("pin1 ") for text in tclk_posts)
    assert ident.did in result.stdout


def test_identity_from_seed_not_used_as_operator_in_tests():
    key, did, _ = new_agent_identity()
    ident = identity_from_seed(private_key_hex(key), source="test")
    assert ident.did == did
    assert ident.is_operator() is False
