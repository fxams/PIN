# PIN
Pinned Inference on Flop

PIN is a **protocol on top of Flop**, not a fork and not a new L1. Miners and agents that speak PIN refuse under-specified Flop sessions. Non-PIN Flop traffic can still exist; PIN does not need every miner.

**One-sentence contract:** Flop moves FLOP and stores a proof hash. PIN defines what was requested, what would count as the same job tomorrow, how many dollars it cost, whether it was late, and who is allowed to challenge the difference.

No Flop smart contracts. Flop is not a contract VM. PIN composes native session escrow, spend limits, HTLC, and the challenge game; JobSpec / artifact catalog / quotes live off-chain and in transcript leaf 0.

Spec: [`docs/pin-1.md`](docs/pin-1.md) · Agents: [`docs/technocore.md`](docs/technocore.md)

## How agents use it (Technocore)

Technocore is the room. Flop is the rail. PIN is the inference convention.

A fetch-only agent (the Technocore peer) does not need POST:

```bash
curl -sS http://127.0.0.1:8787/skill.md
curl -sS http://127.0.0.1:8787/.well-known/agent.json
curl -sS http://127.0.0.1:8787/g/agent-job/8b-stock
```

That GET job posts `pin1` frames (same one-line signed-room rule as `tclk1`), fills Flop’s five
fields with `artifact_id`, and reveals a tclk hashlock **only if** the PIN receipt verifies.
Advertise on a Technocore DID note: `pin/1:flop-session tclk1:flop-htlc`.

```bash
python3 -m pip install -e ".[dev]"
pytest
pin demo
pin agent-demo
pin serve --host 127.0.0.1 --port 8787
```

Then open `http://127.0.0.1:8787` for the dashboard, or:

| Method | Path | Purpose |
| --- | --- | --- |
| `GET` | `/pin/capabilities` | `pin_version`, caps, artifacts, engine profiles |
| `POST` | `/pin/quote` | USD quote sheet → FLOP fee + TTL |
| `POST` | `/pin/accept` | Bind `offer_id` + `JobSpec`; miner co-signs leaf 0 before token 1 |
| `GET` | `/pin/receipt/{job_id}` | transcript root, TOPLOC cids, timing, Flop proof hash |
| `POST` | `/pin/demo` | Honest T1 job or a red-team attack |

The hello-world is not “the page loaded.” It is: publish a JobSpec, fill Flop’s five fields with `artifact_id` (not a raw weights hash), verify leaf 0, stream, and produce a USD-invoiced receipt a third party can check with `pin verify`.

## What PIN will not pretend

- A Flop proof hash in a block does **not** mean the PIN JobSpec ran. It means some inference matching Flop’s five fields produced a transcript Flop accepted.
- HARD attestation is NVIDIA + host firmware. That is T3, not “trustless.”
- SOFT dispute wiring is still draft on Flop. Lab T1 is economic-optimistic. Do not sell T1 as finality.
- You cannot stop non-PIN miners from filling a raw Flop session. PIN agents simply do not post raw sessions.
