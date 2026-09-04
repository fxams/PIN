"""In-memory content-addressed artifact catalog (PIN Registry).

Not a token. Mirrors what validators store for weights, plus tokenizer,
template, engine, quant, sampler-relevant pins. Independent of Flop DA so
watchers are not hostage to one validator disk.
"""

from __future__ import annotations

from pin.models import Artifact


class Registry:
    def __init__(self) -> None:
        self._artifacts: dict[str, Artifact] = {}

    def put(self, artifact: Artifact) -> str:
        artifact_id = artifact.artifact_id
        self._artifacts[artifact_id] = artifact
        return artifact_id

    def get(self, artifact_id: str) -> Artifact | None:
        return self._artifacts.get(artifact_id)

    def require(self, artifact_id: str) -> Artifact:
        artifact = self.get(artifact_id)
        if artifact is None:
            raise KeyError(f"unknown artifact_id {artifact_id}")
        return artifact

    def ids(self) -> set[str]:
        return set(self._artifacts)

    def list(self) -> list[dict[str, str]]:
        return [
            {
                "artifact_id": artifact_id,
                "engine_profile": artifact.engine_profile,
                "kernel_profile": artifact.kernel_profile.value,
                "quant_scheme": artifact.quant_scheme,
                "context_len": str(artifact.context_len),
            }
            for artifact_id, artifact in self._artifacts.items()
        ]
