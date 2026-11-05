"""Harness adapters for EVMBench stages."""

from __future__ import annotations

from .base import (
    BaseHarnessAdapter,
    HarnessAdapter,
    PreflightResult,
    StagedArtifact,
    StageSpec,
    Usage,
    WorkerResult,
    WorkerStatus,
    normalize_status,
)
from .codex import CodexAdapter
from .opencode import OpenCodeAdapter
from .yudai import YudaiAdapter

_ADAPTERS: dict[str, type[BaseHarnessAdapter]] = {
    "opencode": OpenCodeAdapter,
    "codex": CodexAdapter,
    "yudai": YudaiAdapter,
}

SUPPORTED_HARNESSES = tuple(_ADAPTERS)


def get_adapter(harness: str) -> BaseHarnessAdapter:
    try:
        return _ADAPTERS[harness]()
    except KeyError as exc:
        raise ValueError(
            f"Unknown harness {harness!r}. Supported: {', '.join(SUPPORTED_HARNESSES)}"
        ) from exc


__all__ = [
    "BaseHarnessAdapter",
    "CodexAdapter",
    "HarnessAdapter",
    "OpenCodeAdapter",
    "PreflightResult",
    "SUPPORTED_HARNESSES",
    "StageSpec",
    "StagedArtifact",
    "Usage",
    "WorkerResult",
    "WorkerStatus",
    "YudaiAdapter",
    "get_adapter",
    "normalize_status",
]
