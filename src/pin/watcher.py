"""PIN watcher checklist. Does not replace Flop PoUI; adds work challenges can point at.

1. Leaf 0 binds job_id and advertised caps.
2. artifact_id resolves to a complete Artifact, not weights-only.
3. Sampler in leaf 0 matches JobSpec.
4. TOPLOC recomputes against the pinned engine_profile.
5. Timing vs SLA class — late is a PIN refund, not automatically Flop fraud.
6. Optional oracle: task fail, not PoUI fail, unless task_guaranteed=true.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from pin.flop_abi import SLA_TTFB_MS
from pin.models import Artifact, Caps, JobSpec, Receipt
from pin.transcript import merkle_root, toploc_slice, verify_leaf0


@dataclass
class WatcherReport:
    job_id: str
    ok: bool
    integrity_fail: bool = False
    sla_miss: bool = False
    oracle_fail: bool = False
    challenge_invalid: bool = False
    findings: list[str] = field(default_factory=list)
    refund_bps: int = 0


def _ms_between(start_us: int, end_us: int | None) -> float | None:
    if end_us is None:
        return None
    return (end_us - start_us) / 1000.0


class Watcher:
    def check(
        self,
        spec: JobSpec,
        artifact: Artifact,
        caps: Caps,
        receipt: Receipt,
        leaves: list[dict],
        token_ids: list[int],
        miner_pubkey: str,
        oracle_ok: bool | None = None,
    ) -> WatcherReport:
        report = WatcherReport(job_id=spec.job_id, ok=True)

        if not verify_leaf0(miner_pubkey, spec, receipt.timing.t_accept, caps, receipt.leaf0_signature):
            report.ok = False
            report.integrity_fail = True
            report.findings.append("leaf0_mismatch")
            return report

        leaf0 = next(leaf for leaf in leaves if leaf.get("kind") == "leaf0")
        if leaf0["job_id"] != spec.job_id or leaf0["artifact_id"] != spec.artifact_id:
            report.ok = False
            report.integrity_fail = True
            report.findings.append("leaf0_does_not_bind_job")
            return report
        if leaf0["sampler"] != spec.sampler.canonical_dict():
            report.ok = False
            report.integrity_fail = True
            report.findings.append("sampler_mismatch")
            return report
        if leaf0["caps"] != caps.canonical_dict():
            report.ok = False
            report.integrity_fail = True
            report.findings.append("caps_mismatch")
            return report

        required = [
            artifact.weights_cid,
            artifact.tokenizer_cid,
            artifact.chat_template_hash,
            artifact.engine_profile,
            artifact.quant_scheme,
            artifact.vocab_hash,
        ]
        if not all(required) or artifact.context_len <= 0:
            report.ok = False
            report.integrity_fail = True
            report.findings.append("incomplete_artifact")
            return report

        if spec.artifact_id != artifact.artifact_id:
            report.ok = False
            report.integrity_fail = True
            report.findings.append("artifact_id_does_not_resolve")
            return report

        pinned_engine = artifact.engine_profile
        for leaf in leaves:
            if leaf.get("kind") != "toploc":
                continue
            if leaf.get("engine_profile") != pinned_engine:
                # Divergent profile: the challenge itself is invalid, not miner fraud.
                report.ok = False
                report.challenge_invalid = True
                report.findings.append("toploc_engine_profile_diverged")
                return report
            offset = leaf["slice_index"] * 32
            window = token_ids[offset : offset + 32]
            expected = toploc_slice(
                artifact.artifact_id,
                pinned_engine,
                artifact.chat_template_hash,
                artifact.tokenizer_cid,
                spec.sampler.seed,
                spec.prompt_commit,
                window,
                leaf["slice_index"],
            ).hex()
            if expected != leaf["cid"]:
                report.ok = False
                report.integrity_fail = True
                report.findings.append("toploc_mismatch")
                return report

        if merkle_root(leaves) != receipt.transcript_root:
            report.ok = False
            report.integrity_fail = True
            report.findings.append("transcript_root_mismatch")
            return report

        ttfb_ms = _ms_between(receipt.timing.t_accept, receipt.timing.t_first)
        done_ms = _ms_between(receipt.timing.t_accept, receipt.timing.t_done)
        sla_ttfb = SLA_TTFB_MS[spec.sla_class]
        if ttfb_ms is None or ttfb_ms > sla_ttfb:
            report.sla_miss = True
            report.refund_bps += 2_500
            report.findings.append("sla_ttfb_miss")
        if done_ms is None or done_ms > receipt.timing.max_latency_ms:
            report.sla_miss = True
            report.refund_bps += 5_000
            report.findings.append("sla_done_miss")
        if report.sla_miss:
            report.ok = False

        if spec.oracle is not None:
            if oracle_ok is False:
                report.oracle_fail = True
                report.findings.append("oracle_task_fail")
                if caps.task_guaranteed:
                    report.ok = False
                    report.integrity_fail = True
                else:
                    # Pay miner for execution; do not pay task bonus.
                    report.findings.append("pay_execution_no_task_bonus")

        if report.integrity_fail:
            report.ok = False
        return report
