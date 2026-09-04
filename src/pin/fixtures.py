"""Three lab artifacts used by conformance: 8B stock, 70B stock, 8B deterministic."""

from __future__ import annotations

import hashlib

from pin.canonical import pin_hash_hex
from pin.models import Artifact, KernelProfile


def _cid(label: str) -> str:
    return hashlib.sha256(label.encode()).hexdigest()


def lab_artifacts() -> dict[str, Artifact]:
    eight_stock = Artifact(
        weights_cid=_cid("llama-3.1-8b-instruct-weights"),
        tokenizer_cid=_cid("llama-3.1-tokenizer"),
        chat_template_hash=_cid("llama-3.1-instruct-template"),
        quant_scheme="q8_0",
        engine_profile="sglang@pin-lab-v1+deterministic=true",
        kernel_profile=KernelProfile.STOCK,
        context_len=8192,
        vocab_hash=_cid("llama-3.1-vocab"),
    )
    seventy_stock = Artifact(
        weights_cid=_cid("llama-3.1-70b-instruct-weights"),
        tokenizer_cid=_cid("llama-3.1-tokenizer"),
        chat_template_hash=_cid("llama-3.1-instruct-template"),
        quant_scheme="q4_k_m",
        engine_profile="sglang@pin-lab-v1+deterministic=true",
        kernel_profile=KernelProfile.STOCK,
        context_len=8192,
        vocab_hash=_cid("llama-3.1-vocab"),
    )
    eight_repop = Artifact(
        weights_cid=_cid("llama-3.1-8b-instruct-weights"),
        tokenizer_cid=_cid("llama-3.1-tokenizer"),
        chat_template_hash=_cid("llama-3.1-instruct-template"),
        quant_scheme="q8_0",
        engine_profile="sglang@pin-lab-v1+deterministic=true",
        kernel_profile=KernelProfile.REPOOPS,
        context_len=8192,
        vocab_hash=_cid("llama-3.1-vocab"),
    )
    named = {
        "8b-stock": eight_stock,
        "70b-stock": seventy_stock,
        "8b-repop": eight_repop,
    }
    # Sanity: the two 8B artifacts differ only by kernel_profile so artifact_ids differ.
    assert len({a.artifact_id for a in named.values()}) == 3
    assert eight_stock.artifact_id != eight_repop.artifact_id
    return named


def default_messages() -> list[dict[str, str]]:
    return [
        {"role": "system", "content": "You are a PIN lab assistant."},
        {"role": "user", "content": "Pin this inference and return a receipt."},
    ]


def messages_commit(messages: list[dict[str, str]]) -> str:
    return pin_hash_hex(messages)
