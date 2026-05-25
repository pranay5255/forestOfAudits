from __future__ import annotations

import asyncio
import subprocess
from pathlib import Path

from typing_extensions import override

from nanoeval.solvers.computer_tasks.code_execution_interface import (
    ComputerInterface,
    ExecutionResult,
)


class HarborComputerInterface(ComputerInterface):
    """Local process-backed computer interface for Harbor verifier scripts.

    Harbor runs verifier scripts inside the task container. From the grader's
    point of view, that container filesystem is already local, so this adapter
    maps the nano ``ComputerInterface`` methods onto local filesystem and shell
    operations.
    """

    def __init__(self, cwd: str | Path = "/") -> None:
        self.cwd = Path(cwd)

    @override
    async def disable_internet(self) -> None:
        return None

    @override
    async def upload(self, file: bytes, destination: str) -> None:
        path = Path(destination)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(file)

    @override
    async def download(self, file: str) -> bytes:
        return Path(file).read_bytes()

    @override
    async def send_shell_command(self, cmd: str, *, idempotent: bool = False) -> ExecutionResult:
        del idempotent

        def _run() -> subprocess.CompletedProcess[bytes]:
            return subprocess.run(
                ["bash", "-lc", cmd],
                cwd=self.cwd,
                stdout=subprocess.PIPE,
                stderr=subprocess.STDOUT,
                check=False,
            )

        completed = await asyncio.to_thread(_run)
        return ExecutionResult(output=completed.stdout, exit_code=completed.returncode)

    @override
    async def fetch_container_names(self) -> list[str]:
        return ["harbor"]

    @override
    async def stop(self) -> None:
        return None
