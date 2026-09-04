# pin/1 — frozen encodings

PIN is an opt-in overlay on Flop’s session + transcript. It does not rewrite consensus.
It does not deploy contracts on Flop. It does not wait for an L1 redesign.

Sources for Flop primitives (draft whitepaper, 2026-08-19):
[ask.flop.finance](https://ask.flop.finance) — session ABI, PoUI, SOFT/HARD, TOPLOC
(258 B / 32 tokens), 7-day challenge window, 100 FLOP discretionary bond, native HTLC,
spend limits, proxies, multisig, multi-party escrow.

## Why an overlay

Flop session requests carry five fields only: model-weight hash, max latency, FLOPs,
confidentiality flag, FLOP fee. That is the ABI. The missing spec (tokenizer, template,
engine, quant, sampler, USD price, SLA timing, watcher evidence) is encoded in the
handshake and in transcript leaf 0.

| VISL requirement | Flop native | PIN overlay |
| --- | --- | --- |
| Pin weights | Weight hash in session | Expand to full `artifact_id` |
| Pin tokenizer, template, engine, quant, sampler | Missing | JobSpec in handshake + transcript leaf 0 |
| Execution integrity | TOPLOC + challenge | Keep; add replay profile and watcher set |
| Quality / task success | Explicitly not PoUI | Optional oracle receipt |
| Stable invoice | Pay in FLOP only | HTLC + broker: agent pays USDC, miner receives FLOP |
| SLA | Max latency field only | Signed timing in transcript; PIN escrow refunds on miss |
| Bill tokens × artifact × tier | Bills FLOPs | PIN quote sheet; FLOPs field is Flop’s meter only |
| Assurance tiers | SOFT / HARD | T1 / T3; T0 and T2 are PIN capabilities |
| Chain ≠ forward pass | PoUI is consensus | Do not fight it. PIN never makes extra consensus rules |

What an overlay cannot fix: 1-second blocks that fold proof hashes, the 1,000-validator
cap, mandatory $FLOP fees, full-stake burn on fraud, unfinished SOFT dispute wiring.
Those need FIPs. PIN still works if they stay ugly.

## No Flop contracts

Flop cut general smart contracts on purpose. You cannot deploy an ERC-20-style PIN
contract even if you wanted to.

| Piece | On Flop? | How |
| --- | --- | --- |
| Session fee lock / release / fraud refund | Yes | Native session escrow |
| Miner slash on upheld fraud | Yes | Native staking |
| Agent budget cap | Yes | Native spend limits + proxies |
| USDC ↔ FLOP swap | FLOP leg only | Native HTLC; USDC lock is on the other chain |
| JobSpec, artifact catalog, quotes, matcher | No | Off-chain / DA + leaf 0 |
| “Was this the JobSpec we agreed?” | Social + evidence | Miner co-signs leaf 0; watcher challenges if transcript ≠ spec |
| SLA miss | Not as fraud | Spend-condition / broker refund |
| Watcher payroll, conformance list | No | Off-chain |

v0: sidecar + JobSpec + leaf 0 + native session escrow + optional HTLC + optional L2
USDC escrow. Never wait for an EVM on Flop.

## Objects

### Artifact

```
Artifact = {
  weights_cid, tokenizer_cid, chat_template_hash, quant_scheme,
  engine_profile, kernel_profile,   // stock | batch-invariant | repoops
  context_len, vocab_hash
}
artifact_id = SHA256(canonical_json(Artifact))
```

Canonical JSON: UTF-8, sorted keys, separators `(`,`:`)`, no NaN. `artifact_id` is
lowercase hex of 32 bytes.

**Normative rule:** Flop session field 1 is `artifact_id`, not a raw weights hash.
A PIN miner that sees a hash that is not a published artifact_id rejects.

### JobSpec

```
JobSpec = {
  pin_version: "pin/1",
  artifact_id,
  prompt_commit,          // SHA256(canonical_json(messages))
  sampler: { temperature, top_p, top_k, seed, rng_alg, stop_ids, max_new_tokens },
  tier: "T0"|"T1"|"T2"|"T3",
  sla_class: "interactive"|"standard"|"batch",
  max_price_usd_micros,
  max_flop_fee,           // microFLOP ceiling for the Flop fee field
  oracle?: { artifact_id, spec_cid },
  challenge_window_sec    // ≤ Flop window (~7d = 604800)
}
job_id = SHA256(canonical_json(JobSpec))  // omit null oracle
```

Default `rng_alg` is `blake2b-ctr`.

### Leaf 0

Before token 1 the miner co-signs:

```
preimage = "pin/1/leaf0" || job_id || artifact_id || canonical(sampler) || t_accept_u64be || canonical(caps)
leaf0    = Ed25519_Sign_miner(preimage)
```

`t_accept` is Unix time in microseconds. Later leaves: tokens, TOPLOC commits, timing
(`t_first`, `t_done`). Merkle root is SHA-256 of canonical leaf objects, Bitcoin-style
duplication of the last node when odd.

PIN clients treat the job as paid only if leaf 0 verifies against the JobSpec they
escrowed. If a miner produces a valid Flop PoUI for the wrong sampler or template,
PIN watchers challenge it as “not completed as given.” That uses Flop’s existing
adjudication sentence, not a new opcode.

### Caps (off-chain and again in leaf 0)

```
caps = { soft, hard, deterministic_kernels, max_context, artifacts[], task_guaranteed }
```

A T2 job sent to a stock-SOFT miner is invalid under PIN even if Flop would accept it.

## Flop five-field mapping

| Flop field | PIN meaning |
| --- | --- |
| Model-weight hash | `artifact_id` |
| Max latency | SLA deadline (PIN also records TTFB in the transcript) |
| FLOPs | Lower bound Flop requires; not the buyer’s price |
| Confidentiality | `false` → T0/T1/T2; `true` → T3/HARD only |
| Fee | FLOP escrow amount, set from the broker quote (microFLOP) |

SLA latency envelopes used by the lab: interactive 8s / TTFB 2s; standard 60s / TTFB 10s;
batch 15min / TTFB 60s.

## Session flow

1. Agent (or broker) publishes JobSpec (lab: in-process; production: DA).
2. PIN matcher returns a miner offer: FLOP fee, USD quote, caps, stake.
3. If the agent holds USDC: broker HTLC. Agent locks USDC on the other chain; broker
   locks FLOP into Flop multi-party escrow. Native HTLC is the bridge Flop already
   documented.
4. Agent or broker posts the Flop session request with the five fields filled as above.
5. Miner accepts, opens the private connection, **must co-sign leaf 0 before first token**.
   Agent aborts if leaf 0 ≠ `job_id`.
6. Stream tokens off-chain. Emit TOPLOC + timing in the transcript.
7. Flop validators fold the proof hash. PIN watchers may still challenge during the window.
8. Success: FLOP escrow releases to miner; broker releases USDC against the preimage / receipt.
9. SLA miss: PIN refund of a documented fraction (TTFB miss 25%, done miss 50%). Integrity
   fail: Flop fraud path refunds escrow and slashes miner.

Spend limits and proxies stay on the Flop agent account.

## Tiers

| Tier | Meaning |
| --- | --- |
| T0 | Marketplace only → Flop session + escrow, no PIN integrity claim |
| T1 | Default → SOFT + TOPLOC + PIN leaf 0 + PIN watchers |
| T2 | Replay-grade → SOFT + `kernel_profile` in {batch-invariant, repoops} + deterministic caps |
| T3 | Confidential → HARD (NVIDIA CC on TDX/SEV-SNP) + artifact bound in quote |

Do not invent T4 on Flop in v1.

## Money

PIN quote: `usd_per_mtok_in`, `usd_per_mtok_out`, `sla_class`, `tier`, `artifact_id`.
Broker converts to a FLOP fee using a short-dated mid + buffer, then posts the Flop
session. The agent’s budget is USD micros. The miner’s paycheck is FLOP.

Interactive jobs: quote TTL in seconds, not blocks (lab: 15 / 60 / 300).
If FLOP moves more than the buffer before accept, miner or broker may decline.
Never tell the agent the price is “N FLOPs.”

## Watcher checklist

1. Leaf 0 binds `job_id` and advertised caps.
2. `artifact_id` resolves to a complete Artifact, not weights-only.
3. Sampler in leaf 0 matches JobSpec.
4. TOPLOC slice recomputes against the pinned `engine_profile`. If profiles diverge,
   the challenge is invalid.
5. Timing leaves vs SLA class. A late interactive job is a PIN refund, not automatically
   Flop fraud.
6. Optional oracle: task fail, not PoUI fail, unless `task_guaranteed=true`.

## Failure paths

| Event | Result |
| --- | --- |
| accept timeout / no leaf 0 | cancel session, escrow unused |
| leaf 0 mismatch | hang up; no-work; optional Flop challenge |
| stream stall past SLA | PIN partial refund |
| TOPLOC / replay fail | Flop fraud: escrow refund + miner slash |
| oracle fail only | pay execution, no task bonus |
| broker insolvent | HTLC refunds USDC; session never posted |
| FLOP spike mid-quote | decline + requote |

## HTTP (same private connection Flop uses post-accept)

```
GET  /pin/capabilities
POST /pin/quote        { artifact_id, sla_class, tier, n_in, n_out }
POST /pin/accept       { offer_id, job_id, jobspec }
GET  /pin/receipt/{job_id}
```

## FIPs (optional, ordered)

PIN works without these. Each removes a foot-gun.

| ID | Change | Why |
| --- | --- | --- |
| FIP-1 | Extra 32-byte `job_id` in the mempool request | Stops binding a different JobSpec in leaf 0 |
| FIP-2 | First-class bonded watcher | Independent audit market |
| FIP-3 | Receipt status `sla_miss` distinct from fraud | Late ≠ cheat |
| FIP-4 | DA schema for tokenizer + template + engine | Makes `artifact_id` native |
| FIP-5 | Optional stable-unit fee field | Shrinks broker surface |

Do not lead with “please stop using FLOP as money” or “please stop PoUI as consensus.”

## Conformance

A node is PIN-compliant when it:

1. Rejects sessions whose field-1 hash is not a published `artifact_id`.
2. Co-signs leaf 0 before token 1.
3. Emits TOPLOC on Flop’s schedule (258 B / 32 tokens) and stores evidence for the window.
4. Serves at least one listed `engine_profile` that PIN watchers can replay.
5. Honors advertised caps; lying about HARD or deterministic kernels is incomplete work.
6. Exposes `/pin/quote` and `/pin/capabilities`.

Lab badge: 50 jobs, 3 artifacts, 2 swap attacks, 1 SLA miss, 1 leaf-0 lie (`pytest`).

## Technocore binding

PIN does not run inside Technocore. Agents meet there the same way they meet for tclk:

- Room `pin-jobs` carries signed `pin1 ` frames (want, quote, accept, leaf0, receipt).
- JobSpec bytes live in a KV note; the frame carries `jobspec_cid`.
- Money is tclk/1 on flop's `tclk-offers` room with `job.proto=pin`. Live rail is
  `paper` (holds no value). Reveal the preimage only after PIN `paid` and not
  integrity-fail. `flop-htlc` is reserved until flop-labs ships the rail.
- Flop still only sees the five-field session.

Fetch-only agents use `GET /g/agent-job/{artifact_key}` on a PIN sidecar. Live posting uses
Technocore’s signed GET lane. The operator DID is published in `docs/operator-did.md`;
job and lab miner keys stay ephemeral. See `docs/technocore.md`.

## Decision memo (T1 vs HARD)

Flop’s SOFT end-to-end settlement and dispute lane is still being specified; the
currently wired settlement path remains the attested one. PIN lab default is T1
(economic-optimistic) so the protocol can be tested. Mainnet-facing money should
use HARD (T3) or treat T1 as optimistic until Flop freezes SOFT disputes. Do not
sell T1 as finality.

Kill criteria from the build order: if TOPLOC + pinned engine cannot detect a model
swap on two SKUs, stop. The lab mock engine exists to prove that the *protocol*
catches 70B-receipt-from-8B, template swap, and seed ignore. Wire real TOPLOC before
any public matcher listing.
