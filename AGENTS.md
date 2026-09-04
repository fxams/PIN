# AGENTS.md

PIN is a protocol overlay on Flop (`ask.flop.finance`). This repository implements `pin/1`
encodings, a miner sidecar, agent flow, watcher checklist, and a mock Flop bus for local lab
work. It does not fork Flop and does not deploy Flop contracts.

## Standard commands

See `README.md` and `docs/pin-1.md`. Package scripts live in `pyproject.toml`.

```bash
python3 -m pip install -e ".[dev]"
pytest
ruff check src tests
pin demo
pin serve --host 127.0.0.1 --port 8787
```

## Cursor Cloud specific instructions

- There is no Flop L1 in this environment and none is required. `PinLab` mocks session
  mempool, escrow, HTLC, proof-hash settlement, and the 100 FLOP challenge path.
- No GPU / vLLM / SGLang is required for the lab. Tokens and TOPLOC fingerprints are
  deterministic and bound to the pinned Artifact so swap attacks are detectable.
- The process to run for interactive work is `pin serve --host 0.0.0.0 --port 8787`
  (dashboard at `/`, APIs under `/pin/*`). Do not put `pin serve` in the VM update script.
- Hello-world is a paid T1 job: `pin demo` or `POST /pin/demo` with an empty attack. Success
  means `status=paid`, `flop_session.weight_hash == artifact_id`, leaf 0 verified, USD invoice
  on the receipt. Loading the dashboard alone is not a valid check.
- PIN never tells the buyer the price is “N FLOPs.” Quotes are USD micros; the Flop fee field
  is only the chain meter + escrow.
- T1 in this lab is economic-optimistic. Flop’s SOFT dispute wiring is still draft; do not
  describe lab T1 receipts as finality.
- `get_lab()` in `pin.node` is process-global. Restart the server to reset miner stake after
  a fraud-slash demo (`model_swap`).
