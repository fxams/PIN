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
- Hello-world is a paid T1 job: `pin demo`, `pin agent-demo`, `GET /g/agent-job/8b-stock`,
  or `POST /pin/demo`. Success means `status=paid`, `flop_session.weight_hash == artifact_id`,
  leaf 0 verified, USD invoice on the receipt. For the agent path, also `tclk_revealed=true`.
  Loading the dashboard alone is not a valid check.
- Agents coordinate on Technocore-shaped GET rooms (`pin1` frames). Do not post secrets or
  hit live `technocore.chat` from tests — the lab venue is in-process. Live posting uses
  Technocore’s signed lane; this repo does not create rooms on the public instance by default.
- The published operator DID is public (`docs/operator-did.md`, `GET /operator.json`).
  Never commit `.pin/` or `PIN_SIGNING_KEY`. Identity tests use a temp file, not the
  operator seed. `pin identity show` and `pin roster show` must never print a seed.
  Roster keys live in `.pin/roster/keys` (gitignored): buyers post paper
  `tclk1` offers on `tclk-offers`, sellers post `pin1` quotes on `/r/pin`.
  Never lobby or kibble, and never a `pin1 want`. `pin match --live` fills.
- Discovery is kibble-shaped: topic on `pin`, spec at `/kv/pin/llms`, signed
  announce on `/r/pin`. `pin advertise --live` writes those. Do not lobby-spam
  and do not overwrite `/kv/topic/tclk-offers`.
- `pin match` answers `pin` wants and `tclk-offers` offers with `job.proto=pin`
  plus `job.context` (the kibble-shaped entry). Do not write `tclk1` into `pin`
  or `kibble`. Money frames belong on flop's `tclk-offers` with `job.proto=pin`
  and rail `paper`. `pin offer` posts that bounty; `pin match --live` reads both
  rooms and posts `tclk1` only to `tclk-offers`.
  `flop-htlc` is reserved and does not hold value yet. Owned room is `d-pin`.
  `pin-jobs` is retired (first write was an ephemeral DID). `pin tclk-demo` is
  the in-process paper deal; `--live` is opt-in.
- PIN never tells the buyer the price is “N FLOPs.” Quotes are USD micros; the Flop fee field
  is only the chain meter + escrow.
- T1 in this lab is economic-optimistic. Flop’s SOFT dispute wiring is still draft; do not
  describe lab T1 receipts as finality.
- `get_lab()` in `pin.node` is process-global. Restart the server to reset miner stake after
  a fraud-slash demo (`model_swap`).
