"""pin CLI: serve the lab, run a hello-world job, verify a receipt."""

from __future__ import annotations

import json
from pathlib import Path

import typer
import uvicorn

from pin.lab import PinLab
from pin.models import Receipt

app = typer.Typer(no_args_is_help=True, add_completion=False, help="PIN — Pinned Inference on Flop")


@app.command()
def serve(host: str = "127.0.0.1", port: int = 8787) -> None:
    """Run the PIN lab sidecar (capabilities, quote, accept, receipt, dashboard)."""
    uvicorn.run("pin.node:app", host=host, port=port, reload=False)


@app.command()
def demo(
    attack: str = typer.Option("", help="model_swap | template_swap | seed_ignore | leaf0_lie | sla_miss"),
    out: Path | None = typer.Option(None, help="Write receipt JSON here"),
) -> None:
    """Run one pinned job on the in-process lab (no Flop L1 required)."""
    lab = PinLab()
    outcome = lab.run_job(lab.default_spec(), attack=attack)
    payload = {
        "status": outcome.status.value,
        "job_id": outcome.job_id,
        "usd_invoice_micros": outcome.usd_invoice_micros,
        "flop_session": outcome.flop_session,
        "notes": outcome.notes,
        "receipt": outcome.receipt.model_dump(mode="json") if outcome.receipt else None,
        "watcher": None
        if outcome.watcher is None
        else {
            "ok": outcome.watcher.ok,
            "integrity_fail": outcome.watcher.integrity_fail,
            "sla_miss": outcome.watcher.sla_miss,
            "findings": outcome.watcher.findings,
        },
    }
    text = json.dumps(payload, indent=2)
    typer.echo(text)
    if out and outcome.receipt:
        out.write_text(json.dumps(outcome.receipt.model_dump(mode="json"), indent=2), encoding="utf-8")
    raise typer.Exit(0 if outcome.status.value in {"paid", "oracle_fail_paid"} else 1)


@app.command()
def verify(receipt_path: Path) -> None:
    """Third-party verify CLI: leaf 0 must match the JobSpec the buyer escrowed."""
    data = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt = Receipt.model_validate(data)
    typer.echo(
        json.dumps(
            {
                "job_id": receipt.job_id,
                "artifact_id": receipt.artifact_id,
                "paid": receipt.paid,
                "sla_miss": receipt.sla_miss,
                "usd_invoice_micros": receipt.usd_invoice_micros,
                "flop_proof_hash": receipt.flop_proof_hash,
                "transcript_root": receipt.transcript_root,
                "notes": receipt.notes,
            },
            indent=2,
        )
    )
    if not receipt.paid:
        raise typer.Exit(2)


@app.command("hash-artifact")
def hash_artifact(path: Path) -> None:
    from pin.models import Artifact

    artifact = Artifact.model_validate_json(path.read_text(encoding="utf-8"))
    typer.echo(artifact.artifact_id)
