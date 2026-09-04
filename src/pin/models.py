"""pin/1 objects Flop does not have: Artifact, JobSpec, quote, caps, receipt."""

from __future__ import annotations

from enum import StrEnum
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

from pin import FLOP_CHALLENGE_WINDOW_SEC, PIN_VERSION
from pin.canonical import pin_hash_hex, unhex32


class KernelProfile(StrEnum):
    STOCK = "stock"
    BATCH_INVARIANT = "batch-invariant"
    REPOOPS = "repoops"


class Tier(StrEnum):
    T0 = "T0"
    T1 = "T1"
    T2 = "T2"
    T3 = "T3"


class SlaClass(StrEnum):
    INTERACTIVE = "interactive"
    STANDARD = "standard"
    BATCH = "batch"


class Artifact(BaseModel):
    """Complete execution pin. Flop only natively pins weights; PIN expands it."""

    model_config = ConfigDict(extra="forbid")

    weights_cid: str
    tokenizer_cid: str
    chat_template_hash: str
    quant_scheme: str
    engine_profile: str
    kernel_profile: KernelProfile
    context_len: int = Field(gt=0)
    vocab_hash: str

    @field_validator(
        "weights_cid",
        "tokenizer_cid",
        "chat_template_hash",
        "quant_scheme",
        "engine_profile",
        "vocab_hash",
    )
    @classmethod
    def _nonempty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("field must be non-empty")
        return value

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    @property
    def artifact_id(self) -> str:
        return pin_hash_hex(self.canonical_dict())


class Sampler(BaseModel):
    model_config = ConfigDict(extra="forbid")

    temperature: float = Field(ge=0)
    top_p: float = Field(ge=0, le=1)
    top_k: int = Field(ge=0)
    seed: int
    rng_alg: str = "blake2b-ctr"
    stop_ids: list[int] = Field(default_factory=list)
    max_new_tokens: int = Field(gt=0)

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")


class OracleSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    artifact_id: str
    spec_cid: str


class JobSpec(BaseModel):
    model_config = ConfigDict(extra="forbid")

    pin_version: Literal["pin/1"] = PIN_VERSION
    artifact_id: str
    prompt_commit: str
    sampler: Sampler
    tier: Tier
    sla_class: SlaClass
    max_price_usd_micros: int = Field(ge=0)
    max_flop_fee: int = Field(ge=0, description="Ceiling for the Flop session fee field, microFLOP")
    oracle: OracleSpec | None = None
    challenge_window_sec: int = Field(gt=0, le=FLOP_CHALLENGE_WINDOW_SEC)

    @field_validator("artifact_id", "prompt_commit")
    @classmethod
    def _hex32(cls, value: str) -> str:
        unhex32(value)
        return value

    def canonical_dict(self) -> dict[str, Any]:
        data = self.model_dump(mode="json")
        if data.get("oracle") is None:
            data.pop("oracle", None)
        return data

    @property
    def job_id(self) -> str:
        return pin_hash_hex(self.canonical_dict())


class Caps(BaseModel):
    """Advertised off-chain and again in leaf 0."""

    model_config = ConfigDict(extra="forbid")

    soft: bool = True
    hard: bool = False
    deterministic_kernels: bool = False
    max_context: int = Field(gt=0)
    artifacts: list[str] = Field(default_factory=list)
    task_guaranteed: bool = False

    def canonical_dict(self) -> dict[str, Any]:
        return self.model_dump(mode="json")

    def supports(self, spec: JobSpec, artifact: Artifact) -> str | None:
        """Return a rejection reason, or None if the job is valid for these caps."""
        if spec.artifact_id not in self.artifacts:
            return "artifact_not_advertised"
        if artifact.context_len > self.max_context:
            return "context_exceeds_caps"
        if spec.tier in {Tier.T0, Tier.T1} and not self.soft and not self.hard:
            return "no_soft_or_hard"
        if spec.tier == Tier.T2:
            if not self.deterministic_kernels:
                return "t2_requires_deterministic_kernels"
            if artifact.kernel_profile == KernelProfile.STOCK:
                return "t2_rejects_stock_kernel"
        if spec.tier == Tier.T3 and not self.hard:
            return "t3_requires_hard"
        return None


class QuoteRequest(BaseModel):
    artifact_id: str
    sla_class: SlaClass
    tier: Tier
    n_in: int = Field(ge=0)
    n_out: int = Field(gt=0)


class Quote(BaseModel):
    """PIN quote sheet. Never tell the agent the price is N FLOPs."""

    usd_per_mtok_in: int
    usd_per_mtok_out: int
    sla_class: SlaClass
    tier: Tier
    artifact_id: str
    usd_micros: int
    flop_fee: int
    ttl_sec: int
    offer_id: str
    fx_mid_usd_micros: int
    fx_buffer_bps: int


class Timing(BaseModel):
    t_accept: int
    t_first: int | None = None
    t_done: int | None = None
    max_latency_ms: int


class Receipt(BaseModel):
    pin_version: Literal["pin/1"] = PIN_VERSION
    job_id: str
    artifact_id: str
    transcript_root: str
    toploc_cids: list[str]
    timing: Timing
    flop_proof_hash: str | None = None
    miner_pubkey: str
    leaf0_signature: str
    usd_invoice_micros: int
    flop_fee: int
    tier: Tier
    sla_class: SlaClass
    paid: bool = False
    sla_miss: bool = False
    notes: list[str] = Field(default_factory=list)


class FlopSessionRequest(BaseModel):
    """Flop's five-field ABI. PIN uses it as a pointer + envelope."""

    weight_hash: str
    max_latency_ms: int
    flops: int
    confidentiality: bool
    fee_microflop: int

    @model_validator(mode="after")
    def _pin_confidentiality(self) -> FlopSessionRequest:
        unhex32(self.weight_hash)
        return self
