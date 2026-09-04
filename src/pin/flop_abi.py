"""Map pin/1 onto Flop's five session fields.

Flop session requests only carry: weight hash, max latency, FLOPs,
confidentiality flag, FLOP fee. The overlay encodes the missing spec
in the handshake and transcript. No Flop contracts; no new opcodes.
"""

from __future__ import annotations

from pin.models import Artifact, FlopSessionRequest, JobSpec, SlaClass, Tier

# Flop FLOPs field is a lower-bound meter, not the buyer's price.
LAB_FLOPS_PER_TOKEN = 2_000_000_000

SLA_MAX_LATENCY_MS = {
    SlaClass.INTERACTIVE: 8_000,
    SlaClass.STANDARD: 60_000,
    SlaClass.BATCH: 15 * 60_000,
}

SLA_TTFB_MS = {
    SlaClass.INTERACTIVE: 2_000,
    SlaClass.STANDARD: 10_000,
    SlaClass.BATCH: 60_000,
}


class PinAbiError(ValueError):
    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


def confidentiality_for_tier(tier: Tier) -> bool:
    """false → T0/T1/T2; true → T3/HARD only."""
    return tier == Tier.T3


def encode_session(
    spec: JobSpec,
    artifact: Artifact,
    flop_fee: int,
    *,
    flops: int | None = None,
) -> FlopSessionRequest:
    if spec.artifact_id != artifact.artifact_id:
        raise PinAbiError("artifact_id_mismatch", "JobSpec.artifact_id does not match Artifact")
    if flop_fee > spec.max_flop_fee:
        raise PinAbiError("fee_exceeds_ceiling", "FLOP fee exceeds JobSpec.max_flop_fee")
    n_out = spec.sampler.max_new_tokens
    meter = flops if flops is not None else max(1, n_out) * LAB_FLOPS_PER_TOKEN
    return FlopSessionRequest(
        weight_hash=spec.artifact_id,
        max_latency_ms=SLA_MAX_LATENCY_MS[spec.sla_class],
        flops=meter,
        confidentiality=confidentiality_for_tier(spec.tier),
        fee_microflop=flop_fee,
    )


def reject_underspecified(weight_hash: str, registry_ids: set[str]) -> None:
    """Normative rule: field 1 is artifact_id. Bare weights hashes are refused."""
    if weight_hash not in registry_ids:
        raise PinAbiError(
            "underspecified_session",
            "PIN miner refuses a session whose field-1 hash is not a published artifact_id",
        )


def validate_session_against_spec(
    session: FlopSessionRequest, spec: JobSpec, artifact: Artifact
) -> None:
    if session.weight_hash != spec.artifact_id:
        raise PinAbiError("field1_not_artifact", "Flop field 1 must be artifact_id")
    if session.weight_hash != artifact.artifact_id:
        raise PinAbiError("field1_artifact_mismatch", "Flop field 1 does not resolve to Artifact")
    expected_conf = confidentiality_for_tier(spec.tier)
    if session.confidentiality != expected_conf:
        raise PinAbiError(
            "confidentiality_tier_mismatch",
            "confidentiality=true is T3/HARD only; T0/T1/T2 must be false",
        )
    if session.fee_microflop > spec.max_flop_fee:
        raise PinAbiError("fee_exceeds_ceiling", "session fee exceeds JobSpec ceiling")
    if session.max_latency_ms > SLA_MAX_LATENCY_MS[spec.sla_class]:
        raise PinAbiError("latency_looser_than_sla", "session latency looser than PIN SLA class")
