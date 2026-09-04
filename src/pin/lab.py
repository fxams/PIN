"""PIN lab: agent + miner sidecar + watcher + broker on a mock Flop bus.

P0/P1 runtime. Honest T1 jobs pay. Model swaps, template swaps, seed ignore,
and leaf-0 lies are challenged as 'not completed as given' using Flop's
existing adjudication sentence. SLA misses refund; they are not fraud.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any, Literal

from pin.broker import Broker
from pin.bus import FlopBus
from pin.crypto import generate_miner_key, public_key_hex
from pin.engine import generate_tokens
from pin.fixtures import lab_artifacts
from pin.flop_abi import (
    PinAbiError,
    encode_session,
    reject_underspecified,
    validate_session_against_spec,
)
from pin.models import (
    Caps,
    JobSpec,
    Quote,
    QuoteRequest,
    Receipt,
    Sampler,
    SlaClass,
    Tier,
    Timing,
)
from pin.quote import QuoteBook
from pin.registry import Registry
from pin.transcript import (
    leaf0_object,
    merkle_root,
    sign_leaf0,
    timing_leaf,
    tokens_leaf,
    toploc_leaves,
    verify_leaf0,
)
from pin.watcher import Watcher, WatcherReport

Attack = Literal["", "model_swap", "template_swap", "seed_ignore", "leaf0_lie", "sla_miss"]


class JobStatus(StrEnum):
    PAID = "paid"
    ABORTED = "aborted"
    SLA_REFUND = "sla_refund"
    FRAUD_SLASH = "fraud_slash"
    ORACLE_FAIL_PAID = "oracle_fail_paid"


@dataclass
class JobOutcome:
    status: JobStatus
    job_id: str
    flop_session: dict[str, Any]
    quote: Quote | None
    receipt: Receipt | None
    watcher: WatcherReport | None
    notes: list[str] = field(default_factory=list)
    usd_invoice_micros: int = 0
    leaves: list[dict[str, Any]] = field(default_factory=list)
    token_ids: list[int] = field(default_factory=list)
    session_id: str | None = None


class PinLab:
    def __init__(self) -> None:
        self.registry = Registry()
        self.named_artifacts = lab_artifacts()
        for artifact in self.named_artifacts.values():
            self.registry.put(artifact)

        self.miner_key = generate_miner_key()
        self.miner_pubkey = public_key_hex(self.miner_key)
        self.caps = Caps(
            soft=True,
            hard=False,
            deterministic_kernels=True,
            max_context=8192,
            artifacts=sorted(self.registry.ids()),
            task_guaranteed=False,
        )
        prices = {artifact.artifact_id: (100_000, 300_000) for artifact in self.named_artifacts.values()}
        self.quotes = QuoteBook(prices, fx_mid_usd_micros=50_000, fx_buffer_bps=200)
        self.broker = Broker(
            inventory_flop_micro=10_000_000_000,
            inventory_usd_micros=10_000_000_000,
            fx_mid_usd_micros=50_000,
            fx_buffer_bps=200,
        )
        self.bus = FlopBus()
        self.watcher = Watcher()
        self.receipts: dict[str, Receipt] = {}
        self.leaves_by_job: dict[str, list[dict[str, Any]]] = {}
        self.jobs: dict[str, JobOutcome] = {}
        self.engine_profiles = sorted({a.engine_profile for a in self.named_artifacts.values()})

    def capabilities(self) -> dict[str, Any]:
        return {
            "pin_version": "pin/1",
            "caps": self.caps.canonical_dict(),
            "artifacts": self.registry.list(),
            "engine_profiles": self.engine_profiles,
            "miner_pubkey": self.miner_pubkey,
            "contracts_on_flop": False,
        }

    def make_quote(self, request: QuoteRequest) -> Quote:
        self.registry.require(request.artifact_id)
        return self.quotes.quote(request)

    def refuse_raw_weights(self, weights_cid: str) -> None:
        reject_underspecified(weights_cid, self.registry.ids())

    def run_job(
        self,
        spec: JobSpec,
        *,
        offer_id: str | None = None,
        attack: Attack = "",
        oracle_ok: bool | None = None,
        n_in: int = 32,
    ) -> JobOutcome:
        notes: list[str] = []
        artifact = self.registry.require(spec.artifact_id)
        cap_reason = self.caps.supports(spec, artifact)
        if cap_reason:
            return JobOutcome(
                status=JobStatus.ABORTED,
                job_id=spec.job_id,
                flop_session={},
                quote=None,
                receipt=None,
                watcher=None,
                notes=[cap_reason],
            )

        if offer_id:
            quote = self.quotes.take(offer_id)
        else:
            quote = self.make_quote(
                QuoteRequest(
                    artifact_id=spec.artifact_id,
                    sla_class=spec.sla_class,
                    tier=spec.tier,
                    n_in=n_in,
                    n_out=spec.sampler.max_new_tokens,
                )
            )
        if quote.usd_micros > spec.max_price_usd_micros:
            return JobOutcome(
                status=JobStatus.ABORTED,
                job_id=spec.job_id,
                flop_session={},
                quote=quote,
                receipt=None,
                watcher=None,
                notes=["over_budget_usd"],
                usd_invoice_micros=quote.usd_micros,
            )
        if quote.flop_fee > spec.max_flop_fee:
            return JobOutcome(
                status=JobStatus.ABORTED,
                job_id=spec.job_id,
                flop_session={},
                quote=quote,
                receipt=None,
                watcher=None,
                notes=["over_budget_flop_fee"],
                usd_invoice_micros=quote.usd_micros,
            )

        session_req = encode_session(spec, artifact, quote.flop_fee)
        validate_session_against_spec(session_req, spec, artifact)
        try:
            reject_underspecified(session_req.weight_hash, self.registry.ids())
        except PinAbiError as exc:
            return JobOutcome(
                status=JobStatus.ABORTED,
                job_id=spec.job_id,
                flop_session=session_req.model_dump(mode="json"),
                quote=quote,
                receipt=None,
                watcher=None,
                notes=[exc.code],
                usd_invoice_micros=quote.usd_micros,
            )

        posted = self.bus.post_session(session_req, agent="pin-agent")
        self.bus.accept(posted.session_id, miner="pin-miner")

        secret_hash, _lock, preimage = self.broker.open_htlc(quote.usd_micros)

        t_accept = time.time_ns() // 1000
        signed_spec = spec
        if attack == "leaf0_lie":
            # Bind a different job_id in leaf 0 — the classic post-accept bait-and-switch.
            signed_spec = spec.model_copy(
                update={"sampler": spec.sampler.model_copy(update={"seed": spec.sampler.seed + 1})}
            )
            notes.append("attack:leaf0_lie")

        _preimage, signature = sign_leaf0(self.miner_key, signed_spec, t_accept, self.caps)

        if not verify_leaf0(self.miner_pubkey, spec, t_accept, self.caps, signature):
            self.bus.cancel_unused(posted.session_id)
            self.broker.refund(secret_hash)
            notes.append("leaf0_mismatch: agent hung up, escrow unused")
            outcome = JobOutcome(
                status=JobStatus.ABORTED,
                job_id=spec.job_id,
                flop_session=session_req.model_dump(mode="json"),
                quote=quote,
                receipt=None,
                watcher=None,
                notes=notes,
                usd_invoice_micros=quote.usd_micros,
                session_id=posted.session_id,
            )
            self.jobs[spec.job_id] = outcome
            return outcome

        exec_artifact = artifact
        template_override = None
        seed_override = None
        delay_us = 0
        if attack == "model_swap":
            eight = self.named_artifacts["8b-stock"]
            exec_artifact = (
                eight if spec.artifact_id != eight.artifact_id else self.named_artifacts["70b-stock"]
            )
            notes.append("attack:model_swap")
        elif attack == "template_swap":
            template_override = "swapped-chat-template"
            notes.append("attack:template_swap")
        elif attack == "seed_ignore":
            seed_override = spec.sampler.seed + 99
            notes.append("attack:seed_ignore")
        elif attack == "sla_miss":
            delay_us = 3_000_000
            notes.append("attack:sla_miss")

        token_ids = generate_tokens(
            exec_artifact,
            spec.prompt_commit,
            spec.sampler,
            chat_template_hash=template_override,
            seed_override=seed_override,
        )
        t_first = t_accept + 400 + delay_us
        t_done = t_first + max(1, len(token_ids)) * 2_000

        leaves: list[dict[str, Any]] = [
            leaf0_object(spec, t_accept, self.caps, signature, self.miner_pubkey)
        ]
        leaves.append(tokens_leaf(1, token_ids))
        # Honest-fraud TOPLOC: commit the execution that actually ran.
        if attack == "model_swap":
            toploc_artifact = exec_artifact
            toploc_template = exec_artifact.chat_template_hash
            toploc_tokenizer = exec_artifact.tokenizer_cid
            toploc_seed = spec.sampler.seed
        elif attack == "template_swap":
            toploc_artifact = artifact
            toploc_template = template_override or artifact.chat_template_hash
            toploc_tokenizer = artifact.tokenizer_cid
            toploc_seed = spec.sampler.seed
        elif attack == "seed_ignore":
            toploc_artifact = artifact
            toploc_template = artifact.chat_template_hash
            toploc_tokenizer = artifact.tokenizer_cid
            toploc_seed = seed_override if seed_override is not None else spec.sampler.seed
        else:
            toploc_artifact = artifact
            toploc_template = artifact.chat_template_hash
            toploc_tokenizer = artifact.tokenizer_cid
            toploc_seed = spec.sampler.seed
        leaves.extend(
            toploc_leaves(
                toploc_artifact.artifact_id,
                artifact.engine_profile,
                toploc_template,
                toploc_tokenizer,
                toploc_seed,
                spec.prompt_commit,
                token_ids,
                start_index=2,
            )
        )
        leaves.append(timing_leaf(len(leaves), t_first, t_done))
        root = merkle_root(leaves)

        settled = self.bus.settle(posted.session_id, root)
        receipt = Receipt(
            job_id=spec.job_id,
            artifact_id=spec.artifact_id,
            transcript_root=root,
            toploc_cids=[leaf["cid"] for leaf in leaves if leaf.get("kind") == "toploc"],
            timing=Timing(
                t_accept=t_accept,
                t_first=t_first,
                t_done=t_done,
                max_latency_ms=session_req.max_latency_ms,
            ),
            flop_proof_hash=settled.proof_hash,
            miner_pubkey=self.miner_pubkey,
            leaf0_signature=signature,
            usd_invoice_micros=quote.usd_micros,
            flop_fee=quote.flop_fee,
            tier=spec.tier,
            sla_class=spec.sla_class,
        )

        report = self.watcher.check(
            spec,
            artifact,
            self.caps,
            receipt,
            leaves,
            token_ids,
            self.miner_pubkey,
            oracle_ok=oracle_ok,
        )

        status = JobStatus.PAID
        if report.integrity_fail:
            self.bus.challenge(posted.session_id, ";".join(report.findings), integrity_fail=True)
            self.broker.refund(secret_hash)
            receipt.paid = False
            receipt.notes = report.findings
            status = JobStatus.FRAUD_SLASH
            notes.append("flop fraud: escrow refund + miner slash")
        elif report.sla_miss:
            self.bus.sla_refund(posted.session_id, report.refund_bps)
            self.broker.release(secret_hash, preimage)
            receipt.paid = True
            receipt.sla_miss = True
            receipt.notes = report.findings
            status = JobStatus.SLA_REFUND
            notes.append(f"pin sla refund {report.refund_bps} bps; not flop fraud")
        elif report.oracle_fail:
            self.broker.release(secret_hash, preimage)
            receipt.paid = True
            receipt.notes = report.findings
            status = JobStatus.ORACLE_FAIL_PAID
            notes.append("oracle fail only: pay execution, no task bonus")
        else:
            self.broker.release(secret_hash, preimage)
            receipt.paid = True
            notes.append("paid: USDC released against receipt; FLOP escrow to miner")

        self.receipts[spec.job_id] = receipt
        self.leaves_by_job[spec.job_id] = leaves
        outcome = JobOutcome(
            status=status,
            job_id=spec.job_id,
            flop_session=session_req.model_dump(mode="json"),
            quote=quote,
            receipt=receipt,
            watcher=report,
            notes=notes,
            usd_invoice_micros=quote.usd_micros,
            leaves=leaves,
            token_ids=token_ids,
            session_id=posted.session_id,
        )
        self.jobs[spec.job_id] = outcome
        return outcome

    def default_spec(
        self,
        *,
        artifact_key: str = "8b-stock",
        tier: Tier = Tier.T1,
        sla: SlaClass = SlaClass.INTERACTIVE,
        max_new_tokens: int = 48,
        seed: int = 7,
    ) -> JobSpec:
        from pin.fixtures import default_messages
        from pin.transcript import prompt_commit

        artifact = self.named_artifacts[artifact_key]
        return JobSpec(
            artifact_id=artifact.artifact_id,
            prompt_commit=prompt_commit(default_messages()),
            sampler=Sampler(
                temperature=0.0,
                top_p=1.0,
                top_k=0,
                seed=seed,
                rng_alg="blake2b-ctr",
                stop_ids=[],
                max_new_tokens=max_new_tokens,
            ),
            tier=tier,
            sla_class=sla,
            max_price_usd_micros=10_000_000,
            max_flop_fee=10_000_000_000,
            challenge_window_sec=7 * 24 * 60 * 60,
        )

    def hello_world(self) -> JobOutcome:
        """Core product action: pin a job, fill Flop's five fields, verify leaf 0, pay USD."""
        return self.run_job(self.default_spec())
