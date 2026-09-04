"""Deterministic lab engine. No GPU. Tokens and TOPLOC bind to the artifact.

This is the P0 replay lab: honest receipts verify; a 70B receipt produced
by an 8B artifact, a template swap, or a seed ignore do not.
"""

from __future__ import annotations

import hashlib

from pin.canonical import unhex32
from pin.models import Artifact, Sampler


def _u32(data: bytes, offset: int) -> int:
    return int.from_bytes(data[offset : offset + 4], "big")


def generate_tokens(
    artifact: Artifact,
    prompt_commit: str,
    sampler: Sampler,
    *,
    chat_template_hash: str | None = None,
    seed_override: int | None = None,
) -> list[int]:
    """Produce a deterministic token stream bound to artifact + sampler + prompt."""
    template = chat_template_hash or artifact.chat_template_hash
    seed = sampler.seed if seed_override is None else seed_override
    material = b"".join(
        [
            b"pin/1/engine",
            unhex32(artifact.artifact_id),
            unhex32(artifact.tokenizer_cid)
            if _is_hex32(artifact.tokenizer_cid)
            else hashlib.sha256(artifact.tokenizer_cid.encode()).digest(),
            hashlib.sha256(template.encode() if not _is_hex32(template) else bytes.fromhex(template)).digest(),
            hashlib.sha256(artifact.engine_profile.encode()).digest(),
            hashlib.sha256(artifact.quant_scheme.encode()).digest(),
            unhex32(prompt_commit),
            sampler.rng_alg.encode(),
            int(seed).to_bytes(8, "big", signed=True),
            str(sampler.temperature).encode(),
            str(sampler.top_p).encode(),
            int(sampler.top_k).to_bytes(4, "big"),
        ]
    )
    tokens: list[int] = []
    block = hashlib.sha256(material).digest()
    vocab = 32000
    while len(tokens) < sampler.max_new_tokens:
        need = sampler.max_new_tokens - len(tokens)
        expanded = hashlib.shake_256(block + len(tokens).to_bytes(4, "big")).digest(max(64, need * 4))
        for i in range(0, len(expanded) - 3, 4):
            token_id = _u32(expanded, i) % vocab
            if token_id in sampler.stop_ids:
                return tokens
            tokens.append(token_id)
            if len(tokens) >= sampler.max_new_tokens:
                break
        block = hashlib.sha256(block).digest()
    return tokens


def _is_hex32(value: str) -> bool:
    try:
        return len(bytes.fromhex(value)) == 32
    except ValueError:
        return False
