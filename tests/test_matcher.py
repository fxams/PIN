from pin.crypto import private_key_hex
from pin.did import new_agent_identity
from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.identity import PIN_OPERATOR_DID, identity_from_seed
from pin.lab import PinLab
from pin.matcher import OperatorMatcher, artifact_key_for_id


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
    lab.venue.say("pin-jobs", "agent", want, signed=True, did=agent_did)
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
            rail="flop-htlc",
        )
    )
    lab.venue.say("pin-jobs", "agent", accept, signed=True, did=agent_did)
    filled = matcher.step()
    assert len(filled.receipts) == 1
    assert "paid" in filled.receipts[0]
    again = matcher.step()
    assert again.quotes == []
    assert again.receipts == []


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
    lab.venue.say("pin-jobs", "agent", want, signed=True, did=agent_did)
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


def test_identity_from_seed_not_used_as_operator_in_tests():
    key, did, _ = new_agent_identity()
    ident = identity_from_seed(private_key_hex(key), source="test")
    assert ident.did == did
    assert ident.is_operator() is False
