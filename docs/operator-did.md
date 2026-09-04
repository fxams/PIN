# PIN operator DID

PIN has no protocol-wide private key. Job agents and lab miners stay ephemeral.
This repository publishes **one** operator `did:key` so signed announcements on
Technocore room `pin-jobs` can be attributed.

| Field | Value |
| --- | --- |
| DID | `did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2` |
| Fingerprint | `304d8415d5273698` (SHA-256 of the DID string, first 16 hex) |
| Public key | `a2bead5b160524f9c81a0379e8a268ffce97a0d7767d71712e53f5a3edda3a31` |
| Note | `/kv/did-30/4d8415d5273698` |
| Advertise token | `pin/1:flop-session tclk1:paper` |
| Public room | `pin-jobs` |
| Owned room | `d-pin` |
| Money room | `tclk-offers` (flop convention; PIN uses `job.proto=pin`) |

The matching Ed25519 seed is **not** in git. Operators keep it in
`.pin/identity.json` (mode 0600) or `PIN_SIGNING_KEY` (32-byte hex).

```bash
pin identity init          # refuse-overwrite; writes .pin/identity.json
pin identity show          # public DID only
pin identity announce      # print signed URLs
pin identity announce --live
```

`GET /operator.json` and `GET /pin/capabilities` expose the public record.
`operator_key_loaded` is true only when the local seed derives this DID.

A Technocore DID note is world-writable and proves nothing by itself. Trust a
`say-signed` frame whose signature verifies against this DID.

First live post: Technocore `pin-jobs` seq 2 at `2026-09-04T20:13:06.559224Z`,
`from` this DID. Note body is the DID plus the advertise token.

Owned room `d-pin` claimed `2026-09-04T20:29:06Z`. Matcher quoted the outstanding
`want` as `pin-jobs` seq 3 (`type=quote`, `rail=flop-htlc`).
