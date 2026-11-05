"""Modal-backed :class:`TaskComputer`.

The coordinator never executes candidate tools on the host: every stage runs in
its own sandbox created through ``modal.Sandbox.create``.  Sandboxes are named
deterministically from the campaign/task/attempt/stage/worker tuple so that an
interrupted campaign can be audited (and reaped) from Modal's control plane.
"""

from __future__ import annotations

import asyncio
import inspect
import os
import re
import threading
import time
from concurrent.futures import Future
from dataclasses import dataclass
from typing import Any, Callable

from .base import (
    AGENT_DIR,
    CommandFailed,
    ComputerError,
    ComputerIdentity,
    ExecResult,
    TaskComputer,
)

APP_NAME = "evmbench-campaign"
_UNSAFE_NAME = re.compile(r"[^a-z0-9]+")


def _import_modal() -> Any:
    try:
        import modal
    except ModuleNotFoundError as exc:  # pragma: no cover - depends on environment
        raise ComputerError(
            "The `modal` package is required for the Modal backend. "
            "Install the project dev dependencies (`uv sync --group dev`)."
        ) from exc
    return modal


def sandbox_name(
    campaign_id: str,
    task_id: str,
    attempt_id: int,
    stage: str,
    worker_id: str,
) -> str:
    """Deterministic Modal sandbox name for a worker execution."""
    raw = f"evmbench-{campaign_id}-{task_id}-a{attempt_id}-{stage}-{worker_id}"
    return _UNSAFE_NAME.sub("-", raw.lower()).strip("-")[:63]


def _has_modal_credentials() -> bool:
    if os.getenv("MODAL_TOKEN_ID") and os.getenv("MODAL_TOKEN_SECRET"):
        return True
    return os.path.exists(os.path.expanduser("~/.modal.toml"))


class _LoopThread:
    """Small dedicated event loop for APIs that only expose awaitables."""

    def __init__(self) -> None:
        self._loop = asyncio.new_event_loop()
        self._thread = threading.Thread(target=self._run, name="modal-computer-loop", daemon=True)
        self._thread.start()

    def _run(self) -> None:
        asyncio.set_event_loop(self._loop)
        self._loop.run_forever()

    def call(self, awaitable: Any) -> Any:
        future: Future[Any] = asyncio.run_coroutine_threadsafe(awaitable, self._loop)
        return future.result()

    def close(self) -> None:
        self._loop.call_soon_threadsafe(self._loop.stop)


def _call(fn: Callable[..., Any], *args: Any, _loop: _LoopThread | None = None, **kwargs: Any) -> Any:
    """Call ``fn`` and transparently await it when the SDK returns an awaitable."""
    result = fn(*args, **kwargs)
    if inspect.isawaitable(result):
        if _loop is None:
            _loop = _LoopThread()
        return _loop.call(result)
    return result


@dataclass(frozen=True)
class ModalImageSpec:
    """Registry image plus a digest recorded in the run manifest."""

    reference: str
    digest: str | None = None

    @property
    def pinned_reference(self) -> str:
        if self.digest and "@" not in self.reference:
            return f"{self.reference}@{self.digest}"
        return self.reference


class ModalComputer(TaskComputer):
    """A single Modal sandbox exposing the :class:`TaskComputer` contract."""

    def __init__(
        self,
        sandbox: Any,
        identity: ComputerIdentity,
        *,
        workdir: str = AGENT_DIR,
    ) -> None:
        super().__init__(identity)
        self._sandbox = sandbox
        self._workdir = workdir
        self._side_targets: dict[str, "ModalComputer"] = {}
        self._loop = _LoopThread()
        self._terminate_lock = threading.Lock()

    # -- construction --------------------------------------------------------
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
        timeout_seconds: int,
        idle_timeout_seconds: int | None = None,
        workdir: str = AGENT_DIR,
        env: dict[str, str] | None = None,
        secret_names: list[str] | None = None,
        cpu: float = 2.0,
        memory_mb: int = 4096,
        tags: dict[str, str] | None = None,
        image_digest: str | None = None,
        app_name: str = APP_NAME,
        side_targets: list[str] | None = None,
    ) -> "ModalComputer":
        if timeout_seconds <= 0:
            raise ComputerError("Modal sandbox timeout must be a positive number of seconds.")
        modal = _import_modal()
        if not _has_modal_credentials():
            raise ComputerError(
                "No Modal credentials found. Run `modal token set` or set "
                "MODAL_TOKEN_ID/MODAL_TOKEN_SECRET."
            )

        app = modal.App.lookup(app_name, create_if_missing=True)
        image_spec = ModalImageSpec(reference=image, digest=image_digest)
        modal_image = modal.Image.from_registry(image_spec.pinned_reference)
        secrets = [modal.Secret.from_name(name) for name in (secret_names or [])]
        name = sandbox_name(campaign_id, task_id, attempt_id, stage, worker_id)
        sandbox_tags = {
            "campaign_id": campaign_id,
            "task_id": task_id,
            "attempt_id": str(attempt_id),
            "stage": stage,
            "worker_id": worker_id,
            **(tags or {}),
        }
        sandbox = modal.Sandbox.create(
            "sleep",
            "infinity",
            app=app,
            name=name,
            image=modal_image,
            workdir=workdir,
            env=env or {},
            secrets=secrets,
            timeout=timeout_seconds,
            idle_timeout=idle_timeout_seconds or timeout_seconds,
            cpu=cpu,
            memory=memory_mb,
            tags=sandbox_tags,
        )
        identity = ComputerIdentity(
            backend="modal",
            sandbox_id=str(getattr(sandbox, "object_id", name)),
            image=image_spec.pinned_reference,
            name=name,
            tags=sandbox_tags,
        )
        computer = cls(sandbox, identity, workdir=workdir)

        for side in side_targets or []:
            side_name = f"{name[:52]}-{side}"
            side_sandbox = modal.Sandbox.create(
                "sleep",
                "infinity",
                app=app,
                name=side_name[:63],
                image=modal_image,
                workdir=workdir,
                env=env or {},
                secrets=secrets,
                timeout=timeout_seconds,
                idle_timeout=idle_timeout_seconds or timeout_seconds,
                cpu=cpu,
                memory=memory_mb,
                tags={**sandbox_tags, "side_target": side},
            )
            computer._side_targets[side] = cls(
                side_sandbox,
                ComputerIdentity(
                    backend="modal",
                    sandbox_id=str(getattr(side_sandbox, "object_id", side_name)),
                    image=image_spec.pinned_reference,
                    name=side_name[:63],
                    tags={**sandbox_tags, "side_target": side},
                ),
                workdir=workdir,
            )
        return computer

    # -- TaskComputer --------------------------------------------------------
    def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> ExecResult:
        self._ensure_live()
        started = time.monotonic()
        try:
            process = _call(
                self._sandbox.exec,
                "bash",
                "-lc",
                command,
                workdir=cwd or self._workdir,
                env=env or {},
                timeout=int(timeout) if timeout else None,
                _loop=self._loop,
            )
            stdout = _call(_attr(process, "stdout", "read"), _loop=self._loop)
            stderr = _call(_attr(process, "stderr", "read"), _loop=self._loop)
            exit_code = _call(_attr(process, "wait"), _loop=self._loop)
        except ComputerError:
            raise
        except Exception as exc:  # noqa: BLE001 - translate SDK errors
            raise ComputerError(f"Modal exec failed for {command!r}: {exc}") from exc
        return ExecResult(
            command=command,
            stdout=str(stdout or ""),
            stderr=str(stderr or ""),
            exit_code=int(exit_code or 0),
            duration_seconds=time.monotonic() - started,
        )

    def upload(self, data: bytes, remote_path: str) -> None:
        self._ensure_live()
        try:
            _call(
                self._sandbox.filesystem.write_bytes,
                data,
                remote_path,
                _loop=self._loop,
            )
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(f"Failed to upload {remote_path}: {exc}") from exc

    def download(self, remote_path: str) -> bytes:
        self._ensure_live()
        try:
            data = _call(
                self._sandbox.filesystem.read_bytes,
                remote_path,
                _loop=self._loop,
            )
        except Exception as exc:  # noqa: BLE001
            raise ComputerError(f"Failed to download {remote_path}: {exc}") from exc
        return bytes(data)

    def list(self, remote_dir: str) -> list[str]:
        result = self.exec(f"ls -A {remote_dir}")
        if not result.ok:
            raise CommandFailed(result)
        return sorted(line for line in result.stdout.splitlines() if line.strip())

    def terminate(self) -> None:
        with self._terminate_lock:
            if self._terminated:
                return
            self._terminated = True
        for side in list(self._side_targets.values()):
            side.terminate()
        try:
            _call(self._sandbox.terminate, _loop=self._loop)
        except Exception:  # noqa: BLE001 - terminate is best effort
            pass
        finally:
            try:
                self._loop.close()
            except Exception:  # noqa: BLE001
                pass

    def side_target(self, name: str) -> TaskComputer | None:
        return self._side_targets.get(name)

    @property
    def sandbox(self) -> Any:
        return self._sandbox

    def _ensure_live(self) -> None:
        if self._terminated:
            raise ComputerError(f"Sandbox {self.identity.sandbox_id} has been terminated.")


def _attr(obj: Any, *names: str) -> Any:
    value = obj
    for name in names:
        value = getattr(value, name)
    return value


__all__ = ["ModalComputer", "ModalImageSpec", "sandbox_name"]
