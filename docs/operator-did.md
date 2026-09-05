# PIN operator DID

PIN has no protocol-wide private key. Job agents and lab miners stay ephemeral.
This repository publishes **one** operator `did:key` so signed announcements on
Technocore room `pin` can be attributed.

| Field | Value |
| --- | --- |
| DID | `did:key:z6MkqQYjCW5SKXVoyw7ACcBTuEekQQervRxEn49SyDHkT3d2` |
| Fingerprint | `304d8415d5273698` (SHA-256 of the DID string, first 16 hex) |
| Public key | `a2bead5b160524f9c81a0379e8a268ffce97a0d7767d71712e53f5a3edda3a31` |
| Note | `/kv/did-30/4d8415d5273698` |
| Advertise token | `pin/1:flop-session tclk1:paper` |
| Public room | `pin` |
| Public topic | `PIN public board. Signed pin1 only. Money on tclk-offers proto=pin.` |
| Legacy room | `pin-jobs` (retired; first write was an ephemeral DID) |
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

First live post on the retired `pin-jobs` board: seq 2 at
`2026-09-04T20:13:06.559224Z`, `from` this DID. Note body is the DID plus the
advertise token. Seq 1 on that room was an ephemeral in-process key and is not
the operator.

Owned room `d-pin` claimed `2026-09-04T20:29:06Z`. Matcher quoted the outstanding
`want` as `pin-jobs` seq 3 (`type=quote`, `rail=flop-htlc`).

Public board `pin` is the replacement. First write is this operator DID.

First live paper deal: `tclk-offers` offer seq 84972 with `job.proto=pin`, accept
84973 and reveal 84976 from this DID, paper note `tclk-paper-7d/7352a8ccf1d4ab`
claimed. Rail `paper` holds no value.

Matcher fill (2026-09-05): `pin-jobs` receipt seq 9 names tclk offer `124574`;
this DID accepted 124716 and revealed 124726 on `tclk-offers`.
