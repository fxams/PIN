from pin.did import did_from_private, fingerprint, new_agent_identity
from pin.frames import Pin1Frame, decode_frame, encode_frame
from pin.venue import Venue


def test_did_key_roundtrip():
    key, did, fp = new_agent_identity()
    assert did.startswith("did:key:z6Mk")
    assert fingerprint(did) == fp
    assert len(fp) == 16
    assert did_from_private(key) == did


def test_pin1_frame_fits_technocore_cap():
    frame = Pin1Frame(
        type="want",
        from_did="did:key:z6MkjPlaceholder00000000000000000000000000000",
        nonce="9f2c81d0a4e6b357",
        artifact_id="aa" * 32,
        tier="T1",
        sla="interactive",
        n_in=32,
        n_out=48,
        max_usd=1_000_000,
    )
    line = encode_frame(frame)
    assert line.startswith("pin1 ")
    assert len(line) < 4096
    again = decode_frame(line)
    assert again.type == "want"
    assert again.from_did == frame.from_did


def test_unsigned_room_lines_are_dropped_on_fold():
    from pin.agent_flow import fold_pin1_room
    from pin.frames import encode_frame

    venue = Venue()
    key, did, _ = new_agent_identity()
    line = encode_frame(
        Pin1Frame(type="want", from_did=did, nonce="abc", artifact_id="bb" * 32, tier="T1")
    )
    venue.say("pin", "spoof", line, signed=False)
    venue.say("pin", "agent", line, signed=True, did=did)
    folded = fold_pin1_room(venue)
    assert len(folded) == 1
    assert folded[0].from_did == did
