"""Harness preflight orchestration and caching.

A preflight passes only when the harness completes multiple tool turns *and*
writes a known file.  A successful chat response is never sufficient.  Results
are cached by ``(image, harness, model, CLI version, checks)`` so campaigns do
not pay for the same probe twice.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Callable

from evmbench.agents.backend.base import TaskComputer
from evmbench.agents.harness.base import (
    HarnessAdapter,
    PreflightResult,
    StageSpec,
)


class PreflightError(RuntimeError):
    """Raised when preflight cannot establish a usable harness."""


def preflight_spec(
    *,
    campaign_id: str,
    harness: str,
    model: str,
    image: str,
    time_limit_seconds: float = 900.0,
) -> StageSpec:
    return StageSpec(
        campaign_id=campaign_id,
        task_id="preflight",
        attempt_id=0,
        stage="preflight",
        worker_id=f"{harness}-preflight",
        mode="detect",
        harness=harness,
        strategy="baseline",
        model=model,
        image=image,
        instructions="",
        task="Complete the EVMBench harness preflight.",
        time_limit_seconds=time_limit_seconds,
    )


def preflight_cache_path(cache_dir: Path, digest: str) -> Path:
    return cache_dir / f"{digest}.json"


def load_cached_preflight(cache_dir: Path, digest: str) -> PreflightResult | None:
    path = preflight_cache_path(cache_dir, digest)
    if not path.exists():
        return None
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    if not payload.get("passed"):
        return None
    return PreflightResult(**payload)


def write_preflight(cache_dir: Path, result: PreflightResult) -> Path:
    cache_dir.mkdir(parents=True, exist_ok=True)
    path = preflight_cache_path(cache_dir, result.digest)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def run_preflight(
    adapter: HarnessAdapter,
    *,
    spec: StageSpec,
    computer_factory: Callable[[], TaskComputer],
    cache_dir: Path,
    refresh: bool = False,
) -> PreflightResult:
    digest = PreflightResult.compute_digest(
        image=spec.image,
        harness=adapter.name,
        model=spec.model,
        cli_version=adapter.cli_version,
        checks=["read_file", "shell_command", "write_file", "read_back", "exit_zero"],
    )
    if not refresh:
        cached = load_cached_preflight(cache_dir, digest)
        if cached is not None:
            return cached

    computer = computer_factory()
    try:
        result = adapter.preflight(computer, spec)
    finally:
        computer.terminate()
    write_preflight(cache_dir, result)
    if not result.passed:
        raise PreflightError(
            f"{adapter.name} preflight failed for model {spec.model!r}: {result.reason}"
        )
    return result


__all__ = [
    "PreflightError",
    "load_cached_preflight",
    "preflight_cache_path",
    "preflight_spec",
    "run_preflight",
    "write_preflight",
]
