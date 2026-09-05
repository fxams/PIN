"""pin CLI: serve the lab, run a hello-world job, verify a receipt."""

from __future__ import annotations

import json
import time
from pathlib import Path

import typer
import uvicorn

from pin.identity import IdentityError, default_identity_path, init_identity, load_identity, published_operator
from pin.lab import PinLab
from pin.models import Receipt

app = typer.Typer(no_args_is_help=True, add_completion=False, help="PIN — Pinned Inference on Flop")
identity_app = typer.Typer(no_args_is_help=True, help="Persistent did:key (seed stays off git).")
app.add_typer(identity_app, name="identity")


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


@app.command("agent-demo")
def agent_demo(
    artifact_key: str = "8b-stock",
    attack: str = "",
) -> None:
    """Fetch-shaped two-agent job: pin1 frames, Flop session, tclk reveal iff PIN ok."""
    from pin.agent_flow import run_agent_job
    from pin.lab import PinLab

    lab = PinLab()
    transcript = run_agent_job(lab, artifact_key=artifact_key, attack=attack)
    typer.echo(json.dumps(transcript.as_dict(), indent=2))
    raise typer.Exit(0 if transcript.revealed else 1)


@identity_app.command("init")
def identity_init(
    path: Path | None = typer.Option(None, help="Write here instead of .pin/identity.json"),
) -> None:
    """Create a new Ed25519 identity. Refuses to overwrite."""
    dest = path or default_identity_path()
    try:
        ident = init_identity(dest)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    typer.echo(json.dumps(ident.public_dict(), indent=2))


@identity_app.command("show")
def identity_show(
    path: Path | None = typer.Option(None, help="Identity file (never prints the seed)"),
) -> None:
    """Print the public DID. Seed is never echoed."""
    try:
        ident = load_identity(path)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    payload = {"published_operator": published_operator(), "local": None if ident is None else ident.public_dict()}
    typer.echo(json.dumps(payload, indent=2))
    if ident is None:
        raise typer.Exit(1)


@identity_app.command("announce")
def identity_announce(
    live: bool = typer.Option(False, help="GET the signed lane on technocore.chat (opt-in)"),
    room: str = typer.Option("pin-jobs"),
    base: str = typer.Option("https://technocore.chat"),
    path: Path | None = typer.Option(None),
) -> None:
    """Build (or post) a signed operator announcement. Never prints the seed."""
    from pin.identity import require_identity
    from pin.technocore_client import announce_live, preview_announce

    try:
        ident = require_identity(path)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    nonce = str(int(time.time() * 1000))
    if not live:
        typer.echo(json.dumps(preview_announce(ident, base=base, room=room, nonce=nonce), indent=2))
        raise typer.Exit(0)
    result = announce_live(ident, base=base, room=room, nonce=nonce)
    typer.echo(
        json.dumps(
            {
                "did": result.did,
                "room": result.room,
                "nonce": result.nonce,
                "text": result.text,
                "note_status": result.note_status,
                "note_body": result.note_body,
                "room_status": result.room_status,
                "room_body": result.room_body,
            },
            indent=2,
        )
    )
    raise typer.Exit(0 if result.room_status == 200 else 1)


@identity_app.command("claim-room")
def identity_claim_room(
    live: bool = typer.Option(False, help="Claim a d- room on technocore.chat (opt-in)"),
    room: str = typer.Option("d-pin"),
    base: str = typer.Option("https://technocore.chat"),
    path: Path | None = typer.Option(None),
) -> None:
    """Claim an ownable d- room as the operator DID. Seed is never echoed."""
    from pin.identity import require_identity
    from pin.technocore_client import claim_owned_room, sign_note

    try:
        ident = require_identity(path)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    nonce = str(int(time.time() * 1000))
    sig = sign_note(ident, "room-owners", room, nonce, ident.did)
    if not live:
        typer.echo(
            json.dumps({"did": ident.did, "room": room, "nonce": nonce, "sig": sig, "value": ident.did}, indent=2)
        )
        raise typer.Exit(0)
    result = claim_owned_room(ident, room=room, nonce=nonce, base=base)
    typer.echo(json.dumps({"status": result.status, "body": result.body, "room": room, "did": ident.did}, indent=2))
    raise typer.Exit(0 if result.status in {200, 409} else 1)


@app.command("match")
def match_cmd(
    live: bool = typer.Option(False, help="Read/write live pin-jobs (opt-in)"),
    path: Path | None = typer.Option(None),
    base: str = typer.Option("https://technocore.chat"),
) -> None:
    """One matcher step: quote signed wants, fill accepts, bind pin paper offers."""
    from pin.identity import TCLK_OFFERS_ROOM, require_identity
    from pin.matcher import OperatorMatcher, ingest_json_messages
    from pin.technocore_client import fetch_room_json, post_signed_line

    lab = PinLab()
    try:
        ident = require_identity(path)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    matcher = OperatorMatcher(lab, ident)
    if live:
        ingest_json_messages(matcher.venue, fetch_room_json("pin-jobs", base=base))
        ingest_json_messages(
            matcher.venue,
            fetch_room_json(TCLK_OFFERS_ROOM, since=None, limit=200, base=base),
            room=TCLK_OFFERS_ROOM,
        )
    step = matcher.step()
    posted: list[dict[str, str | int]] = []
    if live:
        nonce = int(time.time() * 1000)
        pin_lines = step.quotes + step.leaf0 + step.receipts
        tclk_lines = step.tclk_accepts + step.tclk_settles
        for i, line in enumerate(pin_lines):
            wr = post_signed_line(ident, room="pin-jobs", text=line, nonce=str(nonce + i), base=base)
            posted.append({"room": "pin-jobs", "status": wr.status, "body": wr.body[:200]})
        nonce += len(pin_lines)
        for i, line in enumerate(tclk_lines):
            wr = post_signed_line(ident, room=TCLK_OFFERS_ROOM, text=line, nonce=str(nonce + i), base=base)
            posted.append({"room": TCLK_OFFERS_ROOM, "status": wr.status, "body": wr.body[:200]})
    typer.echo(
        json.dumps(
            {**step.as_dict(), "operator_did": ident.did, "live_posts": posted},
            indent=2,
        )
    )
    # JSON still must not contain a key that looks like a leak; drop seed
    raise typer.Exit(0)


@app.command("tclk-demo")
def tclk_demo(
    live: bool = typer.Option(False, help="Post a paper deal on live tclk-offers (opt-in)"),
    attack: str = typer.Option("", help="model_swap | sla_miss | …"),
    path: Path | None = typer.Option(None),
    base: str = typer.Option("https://technocore.chat"),
) -> None:
    """Run a tclk/1 paper deal gated on a PIN receipt. Paper holds no value."""
    from pin.agent_flow import run_agent_job
    from pin.identity import require_identity
    from pin.tclk_deal import run_live_paper_demo

    if not live:
        lab = PinLab()
        transcript = run_agent_job(lab, attack=attack)
        payload = transcript.as_dict()
        payload["tclk"] = transcript.tclk.as_dict()
        typer.echo(json.dumps(payload, indent=2))
        raise typer.Exit(0 if transcript.revealed else 1)
    try:
        ident = require_identity(path)
    except IdentityError as exc:
        typer.echo(str(exc), err=True)
        raise typer.Exit(2) from exc
    result = run_live_paper_demo(ident, attack=attack, base=base)
    typer.echo(json.dumps(result, indent=2))
    raise typer.Exit(0 if result.get("tclk_revealed") else 1)
