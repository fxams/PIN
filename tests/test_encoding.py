from pin.canonical import pin_hash_hex, unhex32
from pin.crypto import generate_miner_key, public_key_hex
from pin.fixtures import lab_artifacts
from pin.models import Caps, JobSpec, Sampler, SlaClass, Tier
from pin.transcript import sign_leaf0, verify_leaf0


def test_artifact_id_is_complete_pin_not_weights_hash():
    artifacts = lab_artifacts()
    eight = artifacts["8b-stock"]
    seventy = artifacts["70b-stock"]
    assert eight.artifact_id != eight.weights_cid
    assert eight.artifact_id != seventy.artifact_id
    assert len(unhex32(eight.artifact_id)) == 32
    # kernel_profile change is a different artifact
    assert artifacts["8b-repop"].artifact_id != eight.artifact_id


def test_canonical_hash_stable():
    a = lab_artifacts()["8b-stock"]
    assert pin_hash_hex(a.canonical_dict()) == a.artifact_id
    assert pin_hash_hex(a.canonical_dict()) == pin_hash_hex(a.canonical_dict())


def test_leaf0_binds_job_and_sampler():
    spec = JobSpec(
        artifact_id=lab_artifacts()["8b-stock"].artifact_id,
        prompt_commit="aa" * 32,
        sampler=Sampler(
            temperature=0.0, top_p=1.0, top_k=0, seed=1, max_new_tokens=8
        ),
        tier=Tier.T1,
        sla_class=SlaClass.INTERACTIVE,
        max_price_usd_micros=1_000_000,
        max_flop_fee=1_000_000_000,
        challenge_window_sec=3600,
    )
    caps = Caps(max_context=8192, artifacts=[spec.artifact_id])
    key = generate_miner_key()
    _, sig = sign_leaf0(key, spec, 123, caps)
    assert verify_leaf0(public_key_hex(key), spec, 123, caps, sig)
    other = spec.model_copy(update={"sampler": spec.sampler.model_copy(update={"seed": 2})})
    assert not verify_leaf0(public_key_hex(key), other, 123, caps, sig)
