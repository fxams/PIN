import pytest

from pin.flop_abi import (
    PinAbiError,
    confidentiality_for_tier,
    encode_session,
    reject_underspecified,
)
from pin.lab import PinLab
from pin.models import Tier


def test_field1_is_artifact_id():
    lab = PinLab()
    spec = lab.default_spec()
    artifact = lab.registry.require(spec.artifact_id)
    session = encode_session(spec, artifact, flop_fee=1_000)
    assert session.weight_hash == spec.artifact_id == artifact.artifact_id
    assert session.confidentiality is False
    assert session.flops > 0


def test_t3_sets_confidentiality_true():
    assert confidentiality_for_tier(Tier.T3) is True
    assert confidentiality_for_tier(Tier.T1) is False


def test_pin_miner_refuses_bare_weights_hash():
    lab = PinLab()
    weights = lab.named_artifacts["8b-stock"].weights_cid
    with pytest.raises(PinAbiError) as exc:
        reject_underspecified(weights, lab.registry.ids())
    assert exc.value.code == "underspecified_session"


def test_t2_stock_kernel_rejected():
    lab = PinLab()
    spec = lab.default_spec(artifact_key="8b-stock", tier=Tier.T2)
    outcome = lab.run_job(spec)
    assert outcome.status.value == "aborted"
    assert "t2_rejects_stock_kernel" in outcome.notes


def test_t3_without_hard_rejected():
    lab = PinLab()
    spec = lab.default_spec(tier=Tier.T3)
    outcome = lab.run_job(spec)
    assert outcome.status.value == "aborted"
    assert "t3_requires_hard" in outcome.notes
