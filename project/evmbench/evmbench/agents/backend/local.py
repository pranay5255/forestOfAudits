"""Local backends for the :class:`TaskComputer` contract.

Two implementations are provided:

``LocalProcessComputer``
    Runs commands in a host shell with the benchmark's virtual paths
    (``/home/agent``, ``/home/logs``) remapped under a temporary root.  It is the
    backend used by adapter preflights and unit tests.

``LocalDockerComputer``
    Runs commands in a Docker container through Alcatraz, preserving the exact
    image used by the benchmark.  This is the local parity target for the Modal
    backend.
"""

from __future__ import annotations

import asyncio
import os
import shlex
import subprocess
import tempfile
import threading
import time
from pathlib import Path
from typing import Any, Callable

from .base import (
    AGENT_DIR,
    AUDIT_DIR,
    LOGS_DIR,
    SUBMISSION_DIR,
    CommandFailed,
    ComputerError,
    ComputerIdentity,
    ExecResult,
    NullSideTarget,
    TaskComputer,
)

DEFAULT_VIRTUAL_ROOTS = {
    "/home/agent": "agent",
    "/home/logs": "logs",
}


def _remap(text: str, virtual_roots: dict[str, Path]) -> str:
    for virtual, local in sorted(virtual_roots.items(), key=lambda item: len(item[0]), reverse=True):
        text = text.replace(virtual, str(local))
    return text


def _unmap_path(path: str, virtual_roots: dict[str, Path]) -> Path:
    for virtual, local in sorted(virtual_roots.items(), key=lambda item: len(item[0]), reverse=True):
        if path == virtual or path.startswith(virtual + "/"):
            return local / path[len(virtual):].lstrip("/")
    return Path(path)


class LocalProcessComputer(TaskComputer):
    """Host-process workspace that mirrors the benchmark path layout."""

    def __init__(
        self,
        root: Path,
        identity: ComputerIdentity,
        *,
        virtual_roots: dict[str, Path] | None = None,
        default_timeout: float = 600.0,
    ) -> None:
        super().__init__(identity)
        self.root = Path(root)
        self.virtual_roots = virtual_roots or {
            virtual: self.root / relative
            for virtual, relative in DEFAULT_VIRTUAL_ROOTS.items()
        }
        for local in self.virtual_roots.values():
            local.mkdir(parents=True, exist_ok=True)
        for relative in ("audit", "submission", "forest"):
            (self.virtual_roots["/home/agent"] / relative).mkdir(parents=True, exist_ok=True)
        self.default_timeout = default_timeout

    @classmethod
    def create(
        cls,
        *,
        root: str | Path | None = None,
        campaign_id: str = "local",
        task_id: str = "task",
        attempt_id: int = 0,
        stage: str = "stage",
        worker_id: str = "worker",
    ) -> "LocalProcessComputer":
        base = Path(root) if root else Path(tempfile.mkdtemp(prefix="evmbench-local-"))
        identity = ComputerIdentity(
            backend="local",
            sandbox_id=str(base),
            image="local-process",
            name=f"{campaign_id}-{task_id}-{stage}-{worker_id}",
            tags={
                "campaign_id": campaign_id,
                "task_id": task_id,
                "attempt_id": str(attempt_id),
                "stage": stage,
                "worker_id": worker_id,
            },
        )
        return cls(base, identity)

    def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        if self._terminated:
            raise ComputerError(f"Computer {self.identity.sandbox_id} has been terminated.")
        mapped_command = _remap(command, self.virtual_roots)
        mapped_cwd = _unmap_path(cwd, self.virtual_roots) if cwd else self.root
        merged_env = os.environ.copy()
        merged_env.update(env or {})
        started = time.monotonic()
        try:
            completed = subprocess.run(
                ["bash", "-lc", mapped_command],
                cwd=str(mapped_cwd),
                env=merged_env,
                capture_output=True,
                text=True,
                timeout=timeout or self.default_timeout,
            )
        except subprocess.TimeoutExpired as exc:
            return ExecResult(
                command=command,
                stdout=exc.stdout or "" if isinstance(exc.stdout, str) else "",
                stderr=(exc.stderr or "" if isinstance(exc.stderr, str) else "")
                or f"timeout after {timeout}s",
                exit_code=124,
                duration_seconds=time.monotonic() - started,
            )
        return ExecResult(
            command=command,
            stdout=completed.stdout,
            stderr=completed.stderr,
            exit_code=completed.returncode,
            duration_seconds=time.monotonic() - started,
        )

    def upload(self, data: bytes, remote_path: str) -> None:
        target = self._local_path(remote_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)

    def download(self, remote_path: str) -> bytes:
        target = self._local_path(remote_path)
        if not target.exists():
            raise ComputerError(f"Path does not exist: {remote_path}")
        return target.read_bytes()

    def list(self, remote_dir: str) -> list[str]:
        target = self._local_path(remote_dir)
        if not target.exists():
            raise ComputerError(f"Directory does not exist: {remote_dir}")
        return sorted(entry.name for entry in target.iterdir())

    def terminate(self) -> None:
        self._terminated = True

    def side_target(self, name: str) -> TaskComputer | None:
        return None

    def _local_path(self, remote_path: str) -> Path:
        return _unmap_path(remote_path, self.virtual_roots)


class _AsyncRunner:
    """Own a private event loop so async Alcatraz calls stay thread-safe."""

    def __init__(self) -> None:
        self.loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

    def call(self, factory: Callable[[], Any]) -> Any:
        return asyncio.run_coroutine_threadsafe(factory(), self.loop).result()

    def close(self) -> None:
        self.loop.call_soon_threadsafe(self.loop.stop)


class LocalDockerComputer(TaskComputer):
    """Docker-backed computer using the local Alcatraz cluster."""

    def __init__(
        self,
        cluster: Any,
        runner: _AsyncRunner,
        identity: ComputerIdentity,
        *,
        exit_stack: Any | None = None,
    ) -> None:
        super().__init__(identity)
        self._cluster = cluster
        self._runner = runner
        self._exit_stack = exit_stack

    @classmethod
    def create(
        cls,
        *,
        image: str,
        campaign_id: str,
        task_id: str,
        attempt_id: int,
        stage: str,
        worker_id: str,
        workdir: str = AGENT_DIR,
        env: dict[str, str] | None = None,
        pull_from_registry: bool = False,
        side_images: list[str] | None = None,
    ) -> "LocalDockerComputer":
        try:
            from alcatraz.clusters.local import LocalConfig
        except ModuleNotFoundError as exc:  # pragma: no cover
            raise ComputerError("Alcatraz is required for the Docker backend.") from exc

        runner = _AsyncRunner()
        cluster_config = LocalConfig(
            image=image,
            pull_from_registry=pull_from_registry,
            environment=env or {},
            side_images=side_images or [],
        )
        stack = _AsyncExitStack(runner)
        cluster = stack.enter(cluster_config.build())
        identity = ComputerIdentity(
            backend="local",
            sandbox_id=str(getattr(cluster, "id", image)),
            image=image,
            name=f"{campaign_id}-{task_id}-{stage}-{worker_id}",
            tags={
                "campaign_id": campaign_id,
                "task_id": task_id,
                "attempt_id": str(attempt_id),
                "stage": stage,
                "worker_id": worker_id,
                "workdir": workdir,
            },
        )
        return cls(cluster, runner, identity, exit_stack=stack)

    def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        if self._terminated:
            raise ComputerError(f"Container {self.identity.sandbox_id} has been terminated.")
        started = time.monotonic()
        # Alcatraz executes through `sh`; the harness contract assumes bash
        # (pipefail, process substitution). Wrap once at the backend boundary.
        wrapped = f"bash -lc {shlex.quote(command)}"
        result = self._runner.call(
            lambda: self._cluster.send_shell_command(
                wrapped,
                timeout=timeout,
                environment=env,
                workdir=cwd,
            )
        )
        if isinstance(result, dict):
            output = result.get("result") or b""
            exit_code = int(result.get("exit_code") or 0)
        else:
            output = getattr(result, "output", b"")
            exit_code = int(getattr(result, "exit_code", 0))
        return ExecResult(
            command=command,
            stdout=output.decode("utf-8", errors="replace") if isinstance(output, bytes) else str(output),
            stderr="",
            exit_code=exit_code,
            duration_seconds=time.monotonic() - started,
        )

    def upload(self, data: bytes, remote_path: str) -> None:
        # The TaskComputer contract promises parent-directory creation; Alcatraz
        # upload does not do it for us.
        parent = str(Path(remote_path).parent)
        if parent and parent != ".":
            self.exec(f"mkdir -p {shlex.quote(parent)}")
        self._runner.call(lambda: self._cluster.upload(data, remote_path))

    def download(self, remote_path: str) -> bytes:
        return bytes(self._runner.call(lambda: self._cluster.download(remote_path)))

    def list(self, remote_dir: str) -> list[str]:
        result = self.exec(f"ls -A {shlex.quote(remote_dir)}")
        if not result.ok:
            raise CommandFailed(result)
        return sorted(line for line in result.stdout.splitlines() if line.strip())

    def terminate(self) -> None:
        if self._terminated:
            return
        self._terminated = True
        try:
            if self._exit_stack is not None:
                self._exit_stack.close()
        finally:
            self._runner.close()

    def side_target(self, name: str) -> TaskComputer | None:
        if name == "chain":
            # Docker side containers are addressed by container id through
            # Alcatraz helpers, not as independent TaskComputers.
            return NullSideTarget(name)
        return None


class _AsyncExitStack:
    """Minimal synchronous wrapper around an async context manager."""

    def __init__(self, runner: _AsyncRunner) -> None:
        self._runner = runner
        self._cm: Any | None = None
        self._value: Any | None = None

    def enter(self, cm: Any) -> Any:
        self._cm = cm

        async def _enter() -> Any:
            return await cm.__aenter__()

        self._value = self._runner.call(_enter)
        return self._value

    def close(self) -> None:
        cm = self._cm
        if cm is None:
            return

        async def _exit() -> None:
            await cm.__aexit__(None, None, None)

        try:
            self._runner.call(_exit)
        finally:
            self._cm = None


__all__ = ["LocalProcessComputer", "LocalDockerComputer", "AUDIT_DIR", "SUBMISSION_DIR", "LOGS_DIR"]
