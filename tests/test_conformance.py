"""Conformance: 50 jobs, 3 artifacts, 2 swap attacks, 1 SLA miss, 1 leaf-0 lie."""

from pin.lab import JobStatus, PinLab
from pin.models import SlaClass, Tier

KEYS = ["8b-stock", "70b-stock", "8b-repop"]


def test_conformance_50_jobs_and_attacks():
    lab = PinLab()
    paid = 0
    for i in range(50):
        key = KEYS[i % 3]
        tier = Tier.T2 if key == "8b-repop" else Tier.T1
        sla = SlaClass.STANDARD if i % 7 == 0 else SlaClass.INTERACTIVE
        spec = lab.default_spec(
            artifact_key=key,
            tier=tier,
            sla=sla,
            max_new_tokens=32 + (i % 16),
            seed=1000 + i,
        )
        outcome = lab.run_job(spec, n_in=16 + i)
        assert outcome.status == JobStatus.PAID, outcome.notes
        assert outcome.receipt and outcome.receipt.paid
        assert outcome.flop_session["weight_hash"] == spec.artifact_id
        paid += 1
    assert paid == 50

    swap_70 = lab.run_job(lab.default_spec(artifact_key="70b-stock", seed=9), attack="model_swap")
    swap_template = lab.run_job(lab.default_spec(seed=10), attack="template_swap")
    sla = lab.run_job(lab.default_spec(seed=11), attack="sla_miss")
    lie = lab.run_job(lab.default_spec(seed=12), attack="leaf0_lie")

    assert swap_70.status == JobStatus.FRAUD_SLASH
    assert swap_template.status == JobStatus.FRAUD_SLASH
    assert sla.status == JobStatus.SLA_REFUND
    assert lie.status == JobStatus.ABORTED
    assert lab.bus.miner_stake_flop == 0
