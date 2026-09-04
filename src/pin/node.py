"""PIN miner sidecar HTTP API. Flop session post remains their mempool object."""

from __future__ import annotations

import json
from functools import lru_cache
from pathlib import Path
from typing import Annotated

from fastapi import Body, FastAPI, HTTPException
from fastapi.responses import HTMLResponse, PlainTextResponse
from pydantic import BaseModel, Field

from pin.agent_flow import run_agent_job
from pin.lab import PinLab
from pin.models import JobSpec, QuoteRequest, SlaClass, Tier

STATIC = Path(__file__).parent / "static"


class AcceptBody(BaseModel):
    offer_id: str
    job_id: str
    jobspec: JobSpec
    attack: str = ""


class DemoBody(BaseModel):
    attack: str = ""
    artifact_key: str = "8b-stock"
    tier: Tier = Tier.T1
    max_new_tokens: int = Field(default=48, gt=0, le=256)


@lru_cache(maxsize=1)
def get_lab() -> PinLab:
    return PinLab()


def create_app() -> FastAPI:
    app = FastAPI(title="PIN lab", version="0.1.0")

    @app.get("/", response_class=HTMLResponse)
    def dashboard() -> str:
        return (STATIC / "index.html").read_text(encoding="utf-8")

    @app.get("/pin/capabilities")
    def capabilities() -> dict:
        return get_lab().capabilities()

    @app.post("/pin/quote")
    def quote(request: QuoteRequest) -> dict:
        lab = get_lab()
        try:
            return lab.make_quote(request).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.post("/pin/accept")
    def accept(payload: Annotated[AcceptBody, Body()]) -> dict:
        lab = get_lab()
        if payload.jobspec.job_id != payload.job_id:
            raise HTTPException(400, "job_id does not match JobSpec")
        outcome = lab.run_job(payload.jobspec, offer_id=payload.offer_id, attack=payload.attack or "")
        return {
            "status": outcome.status.value,
            "job_id": outcome.job_id,
            "flop_session": outcome.flop_session,
            "usd_invoice_micros": outcome.usd_invoice_micros,
            "notes": outcome.notes,
            "leaf0_ok": outcome.status.value != "aborted" or "leaf0" not in "".join(outcome.notes),
            "receipt": outcome.receipt.model_dump(mode="json") if outcome.receipt else None,
            "watcher": None
            if outcome.watcher is None
            else {
                "ok": outcome.watcher.ok,
                "integrity_fail": outcome.watcher.integrity_fail,
                "sla_miss": outcome.watcher.sla_miss,
                "findings": outcome.watcher.findings,
                "refund_bps": outcome.watcher.refund_bps,
            },
        }

    @app.get("/pin/receipt/{job_id}")
    def receipt(job_id: str) -> dict:
        lab = get_lab()
        rec = lab.receipts.get(job_id)
        if rec is None:
            raise HTTPException(404, "unknown job_id")
        return rec.model_dump(mode="json")

    @app.post("/pin/demo")
    def demo(payload: Annotated[DemoBody, Body()]) -> dict:
        lab = get_lab()
        spec = lab.default_spec(
            artifact_key=payload.artifact_key,
            tier=payload.tier,
            max_new_tokens=payload.max_new_tokens,
        )
        outcome = lab.run_job(spec, attack=payload.attack or "")
        return {
            "status": outcome.status.value,
            "job_id": outcome.job_id,
            "flop_session": outcome.flop_session,
            "usd_invoice_micros": outcome.usd_invoice_micros,
            "quote": outcome.quote.model_dump(mode="json") if outcome.quote else None,
            "receipt": outcome.receipt.model_dump(mode="json") if outcome.receipt else None,
            "notes": outcome.notes,
            "watcher": None
            if outcome.watcher is None
            else {
                "ok": outcome.watcher.ok,
                "integrity_fail": outcome.watcher.integrity_fail,
                "sla_miss": outcome.watcher.sla_miss,
                "oracle_fail": outcome.watcher.oracle_fail,
                "findings": outcome.watcher.findings,
                "refund_bps": outcome.watcher.refund_bps,
            },
        }

    @app.post("/pin/verify")
    def verify_receipt(job_id: str) -> dict:
        lab = get_lab()
        outcome = lab.jobs.get(job_id)
        if outcome is None or outcome.receipt is None:
            raise HTTPException(404, "unknown job_id")
        rec = outcome.receipt
        return {
            "job_id": job_id,
            "paid": rec.paid,
            "sla_miss": rec.sla_miss,
            "transcript_root": rec.transcript_root,
            "flop_proof_hash": rec.flop_proof_hash,
            "usd_invoice_micros": rec.usd_invoice_micros,
            "pin_ok": rec.paid and not rec.sla_miss,
        }

    @app.get("/skill.md", response_class=PlainTextResponse)
    def skill() -> str:
        return (STATIC / "skill.md").read_text(encoding="utf-8")

    @app.get("/llms.txt", response_class=PlainTextResponse)
    def llms() -> str:
        return (STATIC / "llms.txt").read_text(encoding="utf-8")

    @app.get("/.well-known/agent.json")
    def agent_card() -> dict:
        return json.loads((STATIC / "agent.json").read_text(encoding="utf-8"))

    @app.get("/g/quote/{artifact_id}/{sla}/{tier}/{n_in}/{n_out}")
    def get_quote(artifact_id: str, sla: SlaClass, tier: Tier, n_in: int, n_out: int) -> dict:
        lab = get_lab()
        try:
            return lab.make_quote(
                QuoteRequest(
                    artifact_id=artifact_id,
                    sla_class=sla,
                    tier=tier,
                    n_in=n_in,
                    n_out=n_out,
                )
            ).model_dump(mode="json")
        except KeyError as exc:
            raise HTTPException(404, str(exc)) from exc

    @app.get("/g/agent-job/{artifact_key}")
    def agent_job(artifact_key: str, attack: str = "") -> dict:
        """Fetch-only hello-world: pin1 frames + Flop session + tclk reveal."""
        lab = get_lab()
        if artifact_key not in lab.named_artifacts:
            raise HTTPException(404, "unknown artifact_key")
        transcript = run_agent_job(lab, artifact_key=artifact_key, attack=attack, venue=lab.venue)
        return transcript.as_dict()

    @app.get("/r/{room}", response_class=PlainTextResponse)
    def read_room(room: str, since: int = 0) -> str:
        return get_lab().venue.render_room(room, since=since)

    @app.get("/r/{room}/say/{nick}/{text}", response_class=PlainTextResponse)
    def say_room(room: str, nick: str, text: str) -> str:
        rec = get_lab().venue.say(room, nick, text, signed=False)
        return f"{rec.seq}\t~{rec.nick}\t{rec.text}\n"

    return app


app = create_app()
