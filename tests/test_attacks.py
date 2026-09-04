from pin.lab import JobStatus, PinLab


def test_hello_world_paid_receipt():
    lab = PinLab()
    outcome = lab.hello_world()
    assert outcome.status == JobStatus.PAID
    assert outcome.receipt is not None
    assert outcome.receipt.paid is True
    assert outcome.receipt.usd_invoice_micros > 0
    assert outcome.flop_session["weight_hash"] == outcome.receipt.artifact_id
    assert outcome.watcher is not None
    assert outcome.watcher.ok is True
    assert outcome.flop_session["confidentiality"] is False
    assert "FLOP" not in str(outcome.usd_invoice_micros)


def test_model_swap_is_flop_fraud():
    lab = PinLab()
    spec = lab.default_spec(artifact_key="70b-stock")
    outcome = lab.run_job(spec, attack="model_swap")
    assert outcome.status == JobStatus.FRAUD_SLASH
    assert outcome.watcher.integrity_fail is True
    assert "toploc_mismatch" in outcome.watcher.findings
    assert lab.bus.miner_stake_flop == 0


def test_template_swap_is_not_completed_as_given():
    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(), attack="template_swap")
    assert outcome.status == JobStatus.FRAUD_SLASH
    assert "toploc_mismatch" in outcome.watcher.findings


def test_seed_ignore_is_integrity_fail():
    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(), attack="seed_ignore")
    assert outcome.status == JobStatus.FRAUD_SLASH
    assert "toploc_mismatch" in outcome.watcher.findings


def test_leaf0_lie_aborts_unused_escrow():
    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(), attack="leaf0_lie")
    assert outcome.status == JobStatus.ABORTED
    assert any("leaf0_mismatch" in n for n in outcome.notes)
    session = lab.bus.sessions[outcome.session_id]
    assert session.state.value == "cancelled"
    assert session.escrow_microflop == 0


def test_sla_miss_refunds_not_fraud():
    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(), attack="sla_miss")
    assert outcome.status == JobStatus.SLA_REFUND
    assert outcome.receipt.sla_miss is True
    assert outcome.watcher.integrity_fail is False
    assert lab.bus.miner_stake_flop == 10_000
    assert lab.bus.sessions[outcome.session_id].state.value == "sla_refund"
