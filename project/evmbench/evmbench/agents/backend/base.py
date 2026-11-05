"""Backend-neutral execution contract for EVMBench task compute.

A :class:`TaskComputer` is the smallest surface needed by the forest pipeline,
the harness adapters, and the benchmark graders:

* run a shell command inside an isolated workspace,
* move bytes in and out,
* enumerate files,
* expose an optional named side target (for example a private chain),
* terminate the underlying resource,
* carry identity/lifecycle metadata for run provenance.

The contract deliberately does *not* know about harnesses, model providers, or
grading semantics.  That keeps the same workspace usable by ``EVMTask.setup``
and ``EVMTask.grade`` while the search strategy above it stays replaceable.
"""

from __future__ import annotations

import abc
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Literal

BackendName = Literal["local", "modal"]

# Canonical workspace layout used by the benchmark images.
WORKSPACE_BASE = "/home"
AGENT_DIR = f"{WORKSPACE_BASE}/agent"
AUDIT_DIR = f"{AGENT_DIR}/audit"
SUBMISSION_DIR = f"{AGENT_DIR}/submission"
LOGS_DIR = f"{WORKSPACE_BASE}/logs"


class ComputerError(RuntimeError):
    """Raised when a backend operation cannot be completed."""


class CommandFailed(ComputerError):
    """Raised when a command is expected to succeed but exits non-zero."""

    def __init__(self, result: "ExecResult") -> None:
        super().__init__(
            f"Command failed with exit code {result.exit_code}: {result.command}\n"
            f"stdout:\n{result.stdout[-4000:]}\n\nstderr:\n{result.stderr[-4000:]}"
        )
        self.result = result


@dataclass(frozen=True)
class ExecResult:
    """Result of a single shell command executed inside a workspace."""

    command: str
    stdout: str
    stderr: str
    exit_code: int
    duration_seconds: float = 0.0

    @property
    def ok(self) -> bool:
        return self.exit_code == 0

    def raise_for_status(self) -> "ExecResult":
        if not self.ok:
            raise CommandFailed(self)
        return self

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class ComputerIdentity:
    """Stable, serializable identity for a workspace."""

    backend: BackendName
    sandbox_id: str
    image: str
    name: str | None = None
    tags: dict[str, str] = field(default_factory=dict)
    created_at: float = 0.0

    def __post_init__(self) -> None:
        if not self.created_at:
            object.__setattr__(self, "created_at", time.time())

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


class TaskComputer(abc.ABC):
    """Abstract workspace backed by Modal, Docker, or a local process."""

    def __init__(self, identity: ComputerIdentity) -> None:
        self.identity = identity
        self._terminated = False

    # -- required operations -------------------------------------------------
    @abc.abstractmethod
    def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> "ExecResult":
        """Run ``command`` in a fresh shell and return its result.

        Implementations must never raise for a non-zero exit status; callers
        decide whether a failure is expected.
        """

    @abc.abstractmethod
    def upload(self, data: bytes, remote_path: str) -> None:
        """Write ``data`` to ``remote_path``, creating parent directories."""

    @abc.abstractmethod
    def download(self, remote_path: str) -> bytes:
        """Read ``remote_path`` as bytes."""

    @abc.abstractmethod
    def list(self, remote_dir: str) -> list[str]:
        """List entries in ``remote_dir`` (names only, sorted)."""

    @abc.abstractmethod
    def terminate(self) -> None:
        """Release the underlying resource. Must be idempotent."""

    # -- optional side targets ----------------------------------------------
    def side_target(self, name: str) -> "TaskComputer | None":
        """Return a named side workspace, e.g. ``chain`` for exploit parity."""
        return None

    # -- convenience ---------------------------------------------------------
    def upload_text(self, text: str, remote_path: str) -> None:
        self.upload(text.encode("utf-8"), remote_path)

    def download_text(self, remote_path: str) -> str:
        return self.download(remote_path).decode("utf-8", errors="replace")

    def upload_file(self, local_path: str | Path, remote_path: str) -> None:
        self.upload(Path(local_path).read_bytes(), remote_path)

    def download_to(self, remote_path: str, local_path: str | Path) -> Path:
        target = Path(local_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(self.download(remote_path))
        return target

    def exists(self, remote_path: str) -> bool:
        result = self.exec(f"test -e {_quote(remote_path)}")
        return result.exit_code == 0

    def read_probe(self, remote_path: str) -> bytes | None:
        """Return bytes when the path exists and is non-empty, else ``None``."""
        result = self.exec(f"test -s {_quote(remote_path)}")
        if result.exit_code != 0:
            return None
        return self.download(remote_path)

    # -- lifecycle -----------------------------------------------------------
    @property
    def terminated(self) -> bool:
        return self._terminated

    def close(self) -> None:
        self.terminate()

    def __enter__(self) -> "TaskComputer":
        return self

    def __exit__(self, *_exc: object) -> None:
        self.terminate()


def _quote(value: str) -> str:
    import shlex

    return shlex.quote(value)


class NullSideTarget(TaskComputer):
    """A side target that rejects every operation.

    Used when a task does not require a chain sidecar so callers get a clear
    error instead of silently executing against the agent workspace.
    """

    def __init__(self, name: str) -> None:
        super().__init__(
            ComputerIdentity(
                backend="local",
                sandbox_id=f"unavailable:{name}",
                image="none",
                name=name,
                tags={"unavailable": "true"},
            )
        )

    def exec(self, command: str, **_kwargs: Any) -> ExecResult:
        raise ComputerError(f"Side target {self.identity.name!r} is not available: {command}")

    def upload(self, data: bytes, remote_path: str) -> None:
        raise ComputerError(f"Side target {self.identity.name!r} is not available: {remote_path}")

    def download(self, remote_path: str) -> bytes:
        raise ComputerError(f"Side target {self.identity.name!r} is not available: {remote_path}")

    def list(self, remote_dir: str) -> list[str]:
        raise ComputerError(f"Side target {self.identity.name!r} is not available: {remote_dir}")

    def terminate(self) -> None:
        self._terminated = True
