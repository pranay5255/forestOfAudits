"""Real EVMBench grading over a :class:`TaskComputer`.

Detect, patch, and exploit reuse the exact graders used by local Docker runs.
Grading is a separate stage: the grading sandbox receives only the submission
artifact plus the hidden benchmark harness files that the grader uploads itself.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Literal

from evmbench.agents.backend.base import (
    SUBMISSION_DIR,
    ComputerError,
    ComputerIdentity,
    ExecResult,
    TaskComputer,
)
from evmbench.audit import Audit
from evmbench.nano.grade import GraderContext, build_grader
from evmbench.nano.grade.base import EVMbenchGrade
from evmbench.nano.runtime import EVMRuntimeConfig
from nanoeval.solvers.computer_tasks.code_execution_interface import (
    ComputerInterface,
    ExecutionResult,
)

SUBMISSION_FILENAMES = {"detect": "audit.md", "patch": "agent.diff", "exploit": "txs.json"}
GRADER_VERSION = 1


class TaskComputerInterface(ComputerInterface):
    """Expose a :class:`TaskComputer` through nanoeval's grader interface."""

    def __init__(self, computer: TaskComputer) -> None:
        self._task_computer = computer

    async def disable_internet(self) -> None:
        return None

    async def upload(self, file: bytes, destination: str) -> None:
        self._task_computer.upload(file, destination)

    async def download(self, file: str) -> bytes:
        return self._task_computer.download(file)

    async def send_shell_command(self, cmd: str, *, idempotent: bool = False) -> ExecutionResult:
        result = self._task_computer.exec(cmd)
        output = result.stdout.encode("utf-8")
        if result.stderr:
            output += b"\n" + result.stderr.encode("utf-8")
        return ExecutionResult(output=output, exit_code=result.exit_code)

    async def fetch_container_names(self) -> list[str]:
        return [self._task_computer.identity.sandbox_id]

    async def stop(self) -> None:
        self._task_computer.terminate()

    @property
    def _cluster(self) -> Any:
        raise RuntimeError("TaskComputerInterface does not expose Alcatraz internals")


class ComputerInterfaceTaskComputer(TaskComputer):
    """Adapt a nanoeval ``ComputerInterface`` to the TaskComputer contract."""

    def __init__(
        self,
        interface: ComputerInterface,
        *,
        image: str = "existing-computer",
        loop: Any | None = None,
    ) -> None:
        super().__init__(
            ComputerIdentity(
                backend="local",
                sandbox_id=str(id(interface)),
                image=image,
                name="existing-computer",
            )
        )
        self._interface = interface
        self._loop = loop

    def _await(self, coro: Any) -> Any:
        # When grading runs in a worker thread, execute the interface call on
        # the loop that created the computer; otherwise use a private loop.
        if self._loop is not None and self._loop.is_running():
            return asyncio.run_coroutine_threadsafe(coro, self._loop).result()
        return asyncio.run(coro)

    def exec(
        self,
        command: str,
        *,
        timeout: float | None = None,
        env: dict[str, str] | None = None,
        cwd: str | None = None,
    ) -> Any:
        del timeout, env, cwd
        result = self._await(self._interface.send_shell_command(command))
        return ExecResult(
            command=command,
            stdout=result.output.decode("utf-8", errors="replace"),
            stderr="",
            exit_code=result.exit_code,
        )

    def upload(self, data: bytes, remote_path: str) -> None:
        self._await(self._interface.upload(data, remote_path))

    def download(self, remote_path: str) -> bytes:
        return self._await(self._interface.download(remote_path))

    def list(self, remote_dir: str) -> list[str]:
        result = self.exec(f"ls -A {remote_dir}")
        if not result.ok:
            raise ComputerError(f"Failed to list {remote_dir}: {result.stderr}")
        return sorted(line for line in result.stdout.splitlines() if line.strip())

    def terminate(self) -> None:
        self._terminated = True


@dataclass(frozen=True)
class GradeRequest:
    audit: Audit
    mode: Literal["detect", "patch", "exploit"]
    submission_path: Path
    evaluator_model: str
    run_group_id: str
    run_id: str
    runs_dir: str
    judge_wire_api: Literal["chat_completions", "responses"] = "chat_completions"
    reasoning_effort: Literal["low", "medium", "high"] = "high"


@dataclass
class GradeOutcome:
    graded: bool
    score: float | None
    max_score: float | None
    payload: dict[str, Any] = field(default_factory=dict)
    grade: EVMbenchGrade | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            **self.payload,
            "graded": self.graded,
            "score": self.score,
            "max_score": self.max_score,
        }


def submission_remote_path(mode: str) -> str:
    return f"{SUBMISSION_DIR}/{SUBMISSION_FILENAMES[mode]}"


def _provenance(request: GradeRequest, computer: TaskComputer | None, sha: str) -> dict[str, Any]:
    return {
        "grade_source": f"evmbench_{request.mode}",
        "grader_version": GRADER_VERSION,
        "evaluator_model": request.evaluator_model if request.mode == "detect" else None,
        "submission_sha256": sha,
        "grading_sandbox_id": computer.identity.sandbox_id if computer is not None else None,
        "score_is_placeholder": False,
    }


async def _grade_async(
    request: GradeRequest,
    computer: TaskComputer | None,
    runtime_config: EVMRuntimeConfig,
) -> EVMbenchGrade:
    interface = TaskComputerInterface(computer) if computer is not None else None
    grader = build_grader(request.mode, interface, runtime_config.turn_completer)  # type: ignore[arg-type]
    ctx = GraderContext(
        audit=request.audit,
        mode=request.mode,
        agent_output_path=request.submission_path,
        run_group_id=request.run_group_id,
        run_id=request.run_id,
        runs_dir=request.runs_dir,
    )
    return await grader.grade(ctx)


def grade_submission(
    request: GradeRequest,
    *,
    computer: TaskComputer | None = None,
    timeout_seconds: float = 3600.0,
) -> GradeOutcome:
    """Grade a submission, returning ``graded=False`` on transport/grader errors.

    A failure is never reported as a zero score with success provenance.
    """
    import hashlib

    submission = request.submission_path
    if not submission.exists() or submission.stat().st_size == 0:
        return GradeOutcome(
            graded=False,
            score=None,
            max_score=None,
            payload={
                "grade_source": f"evmbench_{request.mode}",
                "grader_version": GRADER_VERSION,
                "error": "missing or empty submission",
                "score_is_placeholder": False,
            },
        )
    sha = hashlib.sha256(submission.read_bytes()).hexdigest()

    if computer is not None and request.mode in ("patch", "exploit"):
        computer.upload(submission.read_bytes(), submission_remote_path(request.mode))

    try:
        _configure_evaluator_provider(request.evaluator_model)
    except RuntimeError as exc:
        return GradeOutcome(
            graded=False,
            score=None,
            max_score=None,
            payload={
                **_provenance(request, computer, sha),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )
    runtime_config = EVMRuntimeConfig(
        agent_id="evmbench-campaign",
        judge_model=request.evaluator_model,
        reasoning_effort=request.reasoning_effort,
        judge_wire_api=request.judge_wire_api,
    )
    try:
        grade = asyncio.run(
            asyncio.wait_for(
                _grade_async(request, computer, runtime_config),
                timeout=timeout_seconds,
            )
        )
    except Exception as exc:  # noqa: BLE001 - grading failures are data
        return GradeOutcome(
            graded=False,
            score=None,
            max_score=None,
            payload={
                **_provenance(request, computer, sha),
                "error": f"{type(exc).__name__}: {exc}",
            },
        )

    result = grade.evmbench_result
    payload = {
        **_provenance(request, computer, sha),
        "score": result.score,
        "max_score": result.max_score,
        "details": _jsonable(result.details),
        "grader_log": grade.grader_log,
    }
    return GradeOutcome(
        graded=True,
        score=float(result.score),
        max_score=float(result.max_score),
        payload=payload,
        grade=grade,
    )


def _configure_evaluator_provider(model: str) -> str:
    """Route the evaluator to the configured provider and return its name.

    The plan's evaluator (``openai/gpt-6-luna``) is an OpenRouter alias, so the
    default is OpenRouter even when a direct ``OPENAI_API_KEY`` also exists.
    Set ``EVMBENCH_EVALUATOR_PROVIDER=openai`` to grade against direct OpenAI.
    """
    import os

    provider = (os.getenv("EVMBENCH_EVALUATOR_PROVIDER") or "").strip().lower()
    explicit_base = (os.getenv("EVMBENCH_EVALUATOR_BASE_URL") or "").strip()
    if not provider:
        provider = "openai" if explicit_base and "openai.com" in explicit_base else "openrouter"

    if provider == "openai":
        key = os.getenv("EVMBENCH_EVALUATOR_API_KEY") or os.getenv("OPENAI_API_KEY")
        if not key:
            raise RuntimeError(
                "EVMBENCH_EVALUATOR_PROVIDER=openai but OPENAI_API_KEY is not set"
            )
        os.environ["OPENAI_API_KEY"] = key
        base = explicit_base or "https://api.openai.com/v1"
        os.environ["OPENAI_BASE_URL"] = base
        os.environ["OPENAI_API_BASE"] = base
        return "openai"

    key = os.getenv("EVMBENCH_EVALUATOR_API_KEY") or os.getenv("OPENROUTER_API_KEY")
    if not key:
        raise RuntimeError(
            "OpenRouter evaluator requires OPENROUTER_API_KEY "
            "(or EVMBENCH_EVALUATOR_API_KEY), or set EVMBENCH_EVALUATOR_PROVIDER=openai"
        )
    base = (
        explicit_base
        or os.getenv("OPENROUTER_API_BASE")
        or "https://openrouter.ai/api/v1"
    )
    os.environ["OPENAI_BASE_URL"] = base
    os.environ["OPENAI_API_BASE"] = base
    os.environ["OPENAI_API_KEY"] = key
    del model
    return "openrouter"


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", serialize_as_any=True)
        except TypeError:
            return value.model_dump(mode="json")
    try:
        json.dumps(value)
    except TypeError:
        return str(value)
    return value


__all__ = [
    "GRADER_VERSION",
    "GradeOutcome",
    "GradeRequest",
    "SUBMISSION_FILENAMES",
    "TaskComputerInterface",
    "grade_submission",
    "submission_remote_path",
]
