from pin.agent_flow import fold_pin1_room, run_agent_job
from pin.lab import PinLab


def test_agent_job_reveals_tclk_only_when_pin_ok():
    lab = PinLab()
    honest = run_agent_job(lab, artifact_key="8b-stock")
    assert honest.outcome.status.value == "paid"
    assert honest.revealed
    assert honest.tclk.revealed is True
    assert honest.frames[0].startswith("pin1 ")
    assert all(line.startswith("pin1 ") for line in honest.frames)
    folded = fold_pin1_room(lab.venue)
    types = [frame.type for frame in folded]
    assert types == ["want", "quote", "accept", "leaf0", "receipt"]
    assert honest.outcome.flop_session["weight_hash"] == lab.named_artifacts["8b-stock"].artifact_id


def test_model_swap_does_not_reveal_tclk_secret():
    lab = PinLab()
    cheated = run_agent_job(lab, artifact_key="70b-stock", attack="model_swap")
    assert cheated.outcome.status.value == "fraud_slash"
    assert cheated.revealed is None
    assert cheated.tclk.refunded is True
