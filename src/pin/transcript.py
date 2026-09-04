"""Transcript leaves, leaf 0, TOPLOC slices, Merkle root.

leaf0 = Sign_miner(job_id || artifact_id || sampler || t_accept || caps)

PIN clients treat a job as paid only if leaf 0 matches the JobSpec they
escrowed against. Settlement on Flop stays "proof hash in a block."
"""

from __future__ import annotations

import hashlib
from typing import Any

from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from pin import TOPLOC_BYTES_PER_SLICE, TOPLOC_TOKENS_PER_SLICE
from pin.canonical import canonical_dumps, pin_digest, pin_hash_hex, unhex32
from pin.crypto import sign, verify
from pin.models import Caps, JobSpec, Sampler

LEAF0_DOMAIN = b"pin/1/leaf0"


def leaf0_preimage(
    job_id: str,
    artifact_id: str,
    sampler: Sampler,
    t_accept: int,
    caps: Caps,
) -> bytes:
    """Binary preimage: domain || job_id || artifact_id || canonical(sampler) || t_accept || canonical(caps)."""
    return b"".join(
        [
            LEAF0_DOMAIN,
            unhex32(job_id),
            unhex32(artifact_id),
            canonical_dumps(sampler.canonical_dict()),
            int(t_accept).to_bytes(8, "big", signed=False),
            canonical_dumps(caps.canonical_dict()),
        ]
    )


def sign_leaf0(
    miner_key: Ed25519PrivateKey,
    spec: JobSpec,
    t_accept: int,
    caps: Caps,
) -> tuple[bytes, str]:
    preimage = leaf0_preimage(spec.job_id, spec.artifact_id, spec.sampler, t_accept, caps)
    return preimage, sign(miner_key, preimage)


def verify_leaf0(
    miner_pubkey: str,
    spec: JobSpec,
    t_accept: int,
    caps: Caps,
    signature_hex: str,
) -> bool:
    preimage = leaf0_preimage(spec.job_id, spec.artifact_id, spec.sampler, t_accept, caps)
    return verify(miner_pubkey, preimage, signature_hex)


def leaf0_object(
    spec: JobSpec,
    t_accept: int,
    caps: Caps,
    signature_hex: str,
    miner_pubkey: str,
) -> dict[str, Any]:
    return {
        "index": 0,
        "kind": "leaf0",
        "job_id": spec.job_id,
        "artifact_id": spec.artifact_id,
        "sampler": spec.sampler.canonical_dict(),
        "t_accept": t_accept,
        "caps": caps.canonical_dict(),
        "miner_pubkey": miner_pubkey,
        "signature": signature_hex,
    }


def tokens_leaf(index: int, token_ids: list[int]) -> dict[str, Any]:
    return {"index": index, "kind": "tokens", "token_ids": token_ids}


def timing_leaf(index: int, t_first: int, t_done: int) -> dict[str, Any]:
    return {"index": index, "kind": "timing", "t_first": t_first, "t_done": t_done}


def _cid_bytes(value: str) -> bytes:
    try:
        raw = bytes.fromhex(value)
        if len(raw) == 32:
            return raw
    except ValueError:
        pass
    return hashlib.sha256(value.encode("utf-8")).digest()


def toploc_slice(
    artifact_id: str,
    engine_profile: str,
    chat_template_hash: str,
    tokenizer_cid: str,
    seed: int,
    prompt_commit: str,
    token_ids: list[int],
    slice_index: int,
) -> bytes:
    """Lab stand-in for TOPLOC: 258 B activation fingerprint.

    Bound to the *executed* artifact, template, tokenizer, seed, and engine.
    Watchers replay against the *pinned* Artifact + JobSpec, not whatever GPU
    they have. A miner that ran the wrong model/template/seed and honestly
    commits that execution fails replay. Forging a matching fingerprint
    without running the pin is assumed expensive, as on Flop.
    """
    h = hashlib.sha256()
    h.update(b"pin/1/toploc")
    h.update(unhex32(artifact_id))
    h.update(engine_profile.encode("utf-8"))
    h.update(_cid_bytes(chat_template_hash))
    h.update(_cid_bytes(tokenizer_cid))
    h.update(int(seed).to_bytes(8, "big", signed=True))
    h.update(unhex32(prompt_commit))
    h.update(int(slice_index).to_bytes(4, "big"))
    for token in token_ids:
        h.update(int(token).to_bytes(4, "big", signed=False))
    return hashlib.shake_256(h.digest()).digest(TOPLOC_BYTES_PER_SLICE)


def toploc_leaves(
    artifact_id: str,
    engine_profile: str,
    chat_template_hash: str,
    tokenizer_cid: str,
    seed: int,
    prompt_commit: str,
    token_ids: list[int],
    start_index: int,
) -> list[dict[str, Any]]:
    leaves: list[dict[str, Any]] = []
    for slice_index, offset in enumerate(range(0, len(token_ids), TOPLOC_TOKENS_PER_SLICE)):
        window = token_ids[offset : offset + TOPLOC_TOKENS_PER_SLICE]
        digest = toploc_slice(
            artifact_id,
            engine_profile,
            chat_template_hash,
            tokenizer_cid,
            seed,
            prompt_commit,
            window,
            slice_index,
        )
        leaves.append(
            {
                "index": start_index + slice_index,
                "kind": "toploc",
                "slice_index": slice_index,
                "engine_profile": engine_profile,
                "cid": digest.hex(),
            }
        )
    return leaves


def merkle_root(leaves: list[dict[str, Any]]) -> str:
    if not leaves:
        return pin_digest(b"").hex()
    layer = [pin_digest(canonical_dumps(leaf)) for leaf in leaves]
    while len(layer) > 1:
        if len(layer) % 2 == 1:
            layer.append(layer[-1])
        layer = [pin_digest(layer[i] + layer[i + 1]) for i in range(0, len(layer), 2)]
    return layer[0].hex()


def prompt_commit(messages: list[dict[str, str]]) -> str:
    return pin_hash_hex(messages)
