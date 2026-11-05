"""Harness adapter contract shared by OpenCode, Codex, and Yudai.

Every adapter owns the same stage lifecycle:

1. stage instructions and the declared parent artifacts into a fresh computer,
2. run the harness CLI inside that computer,
3. preserve the harness-native trajectory,
4. collect and validate declared artifacts,
5. return one normalized :class:`WorkerResult`.

Status is a conjunction, never an exit-code check.  See :func:`normalize_status`
for the precedence table.
"""

from __future__ import annotations

import hashlib
import json
import os
import time
from dataclasses import asdict, dataclass, field, replace
from enum import Enum
from pathlib import Path
from typing import Any, Protocol, runtime_checkable

from evmbench.agents.backend.base import (
    AGENT_DIR,
    AUDIT_DIR,
    LOGS_DIR,
    SUBMISSION_DIR,
    TaskComputer,
)
from evmbench.agents.forest.artifacts import (
    Artifact,
    ArtifactRequest,
    CollectionResult,
    StageContract,
    collect_artifacts,
    sha256_bytes,
    stage_contract,
    stage_parent_artifact,
)

SCHEMA_VERSION = 1
INSTRUCTIONS_PATH = f"{AGENT_DIR}/AGENTS.md"


def _allow_version_mismatch() -> bool:
    return os.getenv("EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH", "").strip().lower() in {
        "1",
        "true",
        "yes",
        "on",
    }
PREFLIGHT_MARKER = "EVMBENCH_PREFLIGHT_OK_9f2c1b"
PREFLIGHT_FILE = f"{LOGS_DIR}/preflight/proof.txt"


class WorkerStatus(str, Enum):
    PLANNED = "planned"
    RUNNING = "running"
    COMPLETED = "completed"
    FAILED = "failed"
    TIMEOUT = "timeout"
    CANCELLED = "cancelled"
    NO_SUBMISSION = "no_submission"
    CONTRACT_VIOLATION = "contract_violation"
    PREFLIGHT_FAILED = "preflight_failed"


SUCCESS_STATUSES = frozenset({WorkerStatus.COMPLETED})
TERMINAL_STATUSES = frozenset(
    {
        WorkerStatus.COMPLETED,
        WorkerStatus.FAILED,
        WorkerStatus.TIMEOUT,
        WorkerStatus.CANCELLED,
        WorkerStatus.NO_SUBMISSION,
        WorkerStatus.CONTRACT_VIOLATION,
        WorkerStatus.PREFLIGHT_FAILED,
    }
)


def normalize_status(
    *,
    exit_code: int | None,
    timed_out: bool = False,
    cancelled: bool = False,
    expected_missing: bool = False,
    contract_violations: tuple[str, ...] | list[str] = (),
    trajectory_required: bool = False,
    trajectory_present: bool = True,
    preflight_failed: bool = False,
    adapter_error: str | None = None,
) -> WorkerStatus:
    """Map execution facts to a normalized terminal status.

    Precedence (highest first): cancelled, timeout, adapter error, preflight
    failure, non-zero exit, contract violation, missing submission, completed.
    A non-zero exit is always ``failed`` even when an artifact exists.
    """
    if cancelled:
        return WorkerStatus.CANCELLED
    if timed_out or exit_code in (124, 137):
        return WorkerStatus.TIMEOUT
    if adapter_error:
        return WorkerStatus.FAILED
    if preflight_failed:
        return WorkerStatus.PREFLIGHT_FAILED
    if exit_code not in (0, None):
        return WorkerStatus.FAILED
    if contract_violations:
        return WorkerStatus.CONTRACT_VIOLATION
    if trajectory_required and not trajectory_present:
        return WorkerStatus.CONTRACT_VIOLATION
    if expected_missing:
        return WorkerStatus.NO_SUBMISSION
    return WorkerStatus.COMPLETED


@dataclass(frozen=True)
class Usage:
    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: float = 0.0

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class SandboxInfo:
    id: str
    image: str
    tags: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True)
class StagedArtifact:
    """A previously collected artifact to upload into a fresh stage sandbox."""

    local_path: Path
    remote_path: str


@dataclass(frozen=True)
class StageSpec:
    campaign_id: str
    task_id: str
    attempt_id: int
    stage: str
    worker_id: str
    mode: str
    harness: str
    strategy: str
    model: str
    image: str
    instructions: str
    task: str
    step_limit: int = 40
    cost_limit: float = 5.0
    time_limit_seconds: float = 3600.0
    output_paths: tuple[str, ...] = ()
    artifact_paths: tuple[str, ...] = ()
    parent_artifacts: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    sandbox_tags: dict[str, str] = field(default_factory=dict)
    staged_artifacts: tuple[StagedArtifact, ...] = ()
    role: str | None = None
    branch_index: int | None = None
    workdir: str = AUDIT_DIR
    artifact_root: str = ""
    pre_command: str = ""
    postprocess_command: str = ""

    def idempotency_key(self) -> tuple[str, str, int, str, str]:
        return (
            self.campaign_id,
            self.task_id,
            self.attempt_id,
            self.stage,
            self.worker_id,
        )

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["staged_artifacts"] = [
            {"local_path": str(item.local_path), "remote_path": item.remote_path}
            for item in self.staged_artifacts
        ]
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "StageSpec":
        data = dict(payload)
        data["staged_artifacts"] = tuple(
            StagedArtifact(local_path=Path(item["local_path"]), remote_path=item["remote_path"])
            for item in data.get("staged_artifacts", [])
        )
        for key in ("output_paths", "artifact_paths", "parent_artifacts"):
            data[key] = tuple(data.get(key, ()))
        return cls(**data)


@dataclass(frozen=True)
class WorkerResult:
    campaign_id: str
    task_id: str
    attempt_id: int
    stage: str
    worker_id: str
    harness: str
    strategy: str
    mode: str
    model: str
    status: WorkerStatus
    exit_code: int | None
    failure_reason: str | None
    artifacts: tuple[Artifact, ...] = ()
    trajectory: Artifact | None = None
    usage: Usage = field(default_factory=Usage)
    runtime_seconds: float = 0.0
    sandbox: SandboxInfo = field(default_factory=lambda: SandboxInfo(id="", image="", tags={}))
    contract: StageContract | None = None
    schema_version: int = SCHEMA_VERSION

    @property
    def completed(self) -> bool:
        return self.status is WorkerStatus.COMPLETED

    def to_dict(self) -> dict[str, Any]:
        return {
            "schema_version": self.schema_version,
            "campaign_id": self.campaign_id,
            "task_id": self.task_id,
            "attempt_id": self.attempt_id,
            "stage": self.stage,
            "worker_id": self.worker_id,
            "harness": self.harness,
            "strategy": self.strategy,
            "mode": self.mode,
            "model": self.model,
            "status": self.status.value,
            "exit_code": self.exit_code,
            "failure_reason": self.failure_reason,
            "artifacts": [artifact.to_dict() for artifact in self.artifacts],
            "trajectory": self.trajectory.to_dict() if self.trajectory else None,
            "usage": self.usage.to_dict(),
            "runtime_seconds": self.runtime_seconds,
            "sandbox": self.sandbox.to_dict(),
            "contract": self.contract.to_dict() if self.contract else None,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "WorkerResult":
        trajectory_payload = payload.get("trajectory")
        return cls(
            schema_version=int(payload.get("schema_version", SCHEMA_VERSION)),
            campaign_id=payload["campaign_id"],
            task_id=payload["task_id"],
            attempt_id=int(payload["attempt_id"]),
            stage=payload["stage"],
            worker_id=payload["worker_id"],
            harness=payload["harness"],
            strategy=payload["strategy"],
            mode=payload["mode"],
            model=payload["model"],
            status=WorkerStatus(payload["status"]),
            exit_code=payload.get("exit_code"),
            failure_reason=payload.get("failure_reason"),
            artifacts=tuple(Artifact(**entry) for entry in payload.get("artifacts", [])),
            trajectory=Artifact(**trajectory_payload) if trajectory_payload else None,
            usage=Usage(**payload.get("usage", {})),
            runtime_seconds=float(payload.get("runtime_seconds", 0.0)),
            sandbox=SandboxInfo(**payload.get("sandbox", {"id": "", "image": "", "tags": {}})),
        )


@dataclass(frozen=True)
class PreflightResult:
    harness: str
    model: str
    image: str
    passed: bool
    exit_code: int | None
    tool_turns: int
    wrote_file: bool
    reason: str | None = None
    digest: str = ""
    runtime_seconds: float = 0.0
    cli_version_expected: str = ""
    cli_version_actual: str | None = None
    version_matches: bool = True

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @staticmethod
    def compute_digest(
        *,
        image: str,
        harness: str,
        model: str,
        cli_version: str,
        checks: list[str],
    ) -> str:
        payload = json.dumps(
            {
                "image": image,
                "harness": harness,
                "model": model,
                "cli_version": cli_version,
                "checks": sorted(checks),
            },
            sort_keys=True,
        )
        return hashlib.sha256(payload.encode("utf-8")).hexdigest()


@runtime_checkable
class HarnessAdapter(Protocol):
    name: str
    cli_version: str

    def preflight(self, computer: TaskComputer, spec: StageSpec) -> PreflightResult: ...

    def run(self, computer: TaskComputer, spec: StageSpec) -> WorkerResult: ...


# ---------------------------------------------------------------------------
# Shared implementation
# ---------------------------------------------------------------------------


class BaseHarnessAdapter:
    """Common staging/execution/collection flow.

    Subclasses provide :meth:`build_command`, :meth:`trajectory_request`, and
    optionally :meth:`stage_environment` / :meth:`count_tool_turns`.
    """

    name: str = "base"
    cli_version: str = "unknown"
    version_command: str | None = None
    trajectory_local_name: str = "trajectory.jsonl"

    # -- hooks ---------------------------------------------------------------
    def build_command(self, spec: StageSpec) -> str:
        raise NotImplementedError

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest | None:
        raise NotImplementedError

    def stage_environment(self, spec: StageSpec) -> dict[str, str]:
        return dict(spec.env)

    def count_tool_turns(self, trajectory: bytes) -> int:
        return sum(1 for line in trajectory.decode("utf-8", errors="replace").splitlines() if line.strip())

    def parse_usage(self, stdout: str, stderr: str) -> Usage:
        return Usage()

    # -- public API ----------------------------------------------------------
    def run(self, computer: TaskComputer, spec: StageSpec) -> WorkerResult:
        started = time.monotonic()
        contract = stage_contract(spec.stage, spec.mode, worker_id=spec.worker_id, role=spec.role)
        adapter_error: str | None = None
        exit_code: int | None = None
        timed_out = False
        stdout = ""
        stderr = ""
        model_audit_path = f"{LOGS_DIR}/{spec.stage}-{spec.worker_id}-audit.json"
        events_remote = f"{LOGS_DIR}/{spec.stage}-{spec.worker_id}-events.json"
        command = ""

        try:
            self._stage_inputs(computer, spec)
            if spec.pre_command:
                pre = computer.exec(
                    spec.pre_command,
                    timeout=min(1800.0, spec.time_limit_seconds),
                    env=self.stage_environment(spec),
                    cwd=spec.workdir,
                )
                stdout += pre.stdout
                stderr += pre.stderr
                if not pre.ok:
                    raise RuntimeError(
                        f"stage preparation failed with exit code {pre.exit_code}: "
                        f"{(pre.stderr or pre.stdout)[-2000:]}"
                    )
            command = self.build_command(spec)
            result = computer.exec(
                command,
                timeout=spec.time_limit_seconds,
                env=self.stage_environment(spec),
                cwd=spec.workdir,
            )
            stdout, stderr = result.stdout, result.stderr
            exit_code = result.exit_code
            timed_out = result.exit_code in (124, 137)
            if spec.postprocess_command and exit_code == 0:
                post = computer.exec(
                    spec.postprocess_command,
                    timeout=min(600.0, spec.time_limit_seconds),
                    env=self.stage_environment(spec),
                    cwd=spec.workdir,
                )
                stdout += "\n[postprocess]\n" + post.stdout
                stderr += "\n[postprocess]\n" + post.stderr
                if not post.ok:
                    adapter_error = (
                        f"postprocess failed with exit code {post.exit_code}: "
                        f"{(post.stderr or post.stdout)[-1000:]}"
                    )
        except Exception as exc:  # noqa: BLE001 - the adapter must normalize errors
            adapter_error = f"{type(exc).__name__}: {exc}"

        dimensions = {
            "observed_tool_turns": 0,
            "step_limit": spec.step_limit,
            "cost_limit": spec.cost_limit,
            "time_limit_seconds": spec.time_limit_seconds,
            "stdout_bytes": len(stdout.encode("utf-8")),
            "stderr_bytes": len(stderr.encode("utf-8")),
        }
        try:
            computer.upload_text(json.dumps(dimensions, indent=2) + "\n", model_audit_path)
            computer.upload_text((stdout + "\n" + stderr).strip() + "\n", events_remote)
        except Exception:  # noqa: BLE001 - diagnostics are best effort
            pass

        artifact_root = Path(spec.artifact_root) if spec.artifact_root else Path(".")

        # Collect declared artifacts.
        collection = (
            collect_artifacts(computer, contract.expected, artifact_root)
            if contract.expected
            else CollectionResult()
        )

        # Preserve the harness-native trajectory.
        trajectory: Artifact | None = None
        trajectory_violations: list[str] = []
        request = self.trajectory_request(spec)
        if request is not None:
            trajectory_collection = collect_artifacts(computer, [request], artifact_root)
            trajectory = trajectory_collection.artifacts[0] if trajectory_collection.artifacts else None
            trajectory_violations.extend(trajectory_collection.violations)
            trajectory_violations.extend(trajectory_collection.missing_required)

        forbidden = [path for path in contract.forbidden_remote_paths if computer.exists(path)]
        contract_violations = list(collection.violations)
        if forbidden:
            contract_violations.append(
                "stage wrote a forbidden submission artifact: " + ", ".join(forbidden)
            )
        contract_violations.extend(trajectory_violations)

        status = normalize_status(
            exit_code=exit_code,
            timed_out=timed_out,
            expected_missing=bool(collection.missing_required),
            contract_violations=contract_violations,
            trajectory_required=contract.requires_trajectory and request is not None,
            trajectory_present=trajectory is not None,
            adapter_error=adapter_error,
        )

        failure_reason = None
        if status is WorkerStatus.TIMEOUT:
            failure_reason = f"time limit exceeded after {time.monotonic() - started:.0f}s"
        elif status is not WorkerStatus.COMPLETED:
            detail = (stderr or stdout).strip().splitlines()
            tail = detail[-1][:400] if detail else ""
            failure_reason = adapter_error or "; ".join(
                [*collection.missing_required, *contract_violations]
            ) or (f"exit_code={exit_code}: {tail}" if tail else f"exit_code={exit_code}")

        return WorkerResult(
            campaign_id=spec.campaign_id,
            task_id=spec.task_id,
            attempt_id=spec.attempt_id,
            stage=spec.stage,
            worker_id=spec.worker_id,
            harness=spec.harness,
            strategy=spec.strategy,
            mode=spec.mode,
            model=spec.model,
            status=status,
            exit_code=exit_code,
            failure_reason=failure_reason,
            artifacts=collection.artifacts,
            trajectory=trajectory,
            usage=self.parse_usage(stdout, stderr),
            runtime_seconds=time.monotonic() - started,
            sandbox=SandboxInfo(
                id=computer.identity.sandbox_id,
                image=computer.identity.image,
                tags=dict(computer.identity.tags),
            ),
            contract=contract,
        )

    def preflight(self, computer: TaskComputer, spec: StageSpec) -> PreflightResult:
        started = time.monotonic()
        digest = PreflightResult.compute_digest(
            image=spec.image,
            harness=spec.harness,
            model=spec.model,
            cli_version=self.cli_version,
            checks=["read_file", "shell_command", "write_file", "read_back", "exit_zero"],
        )
        preflight_spec = replace(
            spec,
            stage="preflight",
            worker_id="preflight",
            artifact_paths=(),
            output_paths=(PREFLIGHT_FILE,),
            staged_artifacts=(),
            parent_artifacts=(),
        )
        reason = None
        tool_turns = 0
        wrote_file = False
        exit_code: int | None = None
        actual_version: str | None = None
        version_matches = True
        try:
            actual_version = self.detect_version(computer)
            if actual_version is None:
                version_matches = False
                reason = f"could not determine {self.name} CLI version"
            elif self.cli_version not in actual_version:
                version_matches = False
                if not _allow_version_mismatch():
                    reason = (
                        f"{self.name} CLI version mismatch: image has {actual_version!r}, "
                        f"expected {self.cli_version!r}. Rebuild the image with the pin or "
                        "set EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH=1."
                    )
            if reason is not None:
                return PreflightResult(
                    harness=self.name,
                    model=spec.model,
                    image=spec.image,
                    passed=False,
                    exit_code=exit_code,
                    tool_turns=tool_turns,
                    wrote_file=wrote_file,
                    reason=reason,
                    digest=digest,
                    runtime_seconds=time.monotonic() - started,
                    cli_version_expected=self.cli_version,
                    cli_version_actual=actual_version,
                    version_matches=version_matches,
                )
            self._stage_inputs(computer, preflight_spec)
            computer.upload_text(
                "# EVMBench harness preflight\n\n"
                "Carry out this exact sequence with your bash tool, in order. "
                "Do not explore the repository, do not ask questions, and do not "
                "call any tool after step 3.\n\n"
                f"1. Run: `cat {LOGS_DIR}/preflight/PREFLIGHT.md`\n"
                "2. Run: `uname -a`\n"
                f"3. Run: `mkdir -p {Path(PREFLIGHT_FILE).parent} && "
                f"printf '{PREFLIGHT_MARKER}\\n' > {PREFLIGHT_FILE} && cat {PREFLIGHT_FILE}`\n"
                "4. Run: `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` and then stop.\n\n"
                "The harness passes only if the proof file exists and at least two "
                "bash tool calls were recorded.\n",
                f"{LOGS_DIR}/preflight/PREFLIGHT.md",
            )
            result = computer.exec(
                self.build_command(preflight_spec),
                timeout=min(spec.time_limit_seconds, 900),
                env=self.stage_environment(preflight_spec),
                cwd=spec.workdir,
            )
            exit_code = result.exit_code
            if exit_code != 0:
                reason = f"preflight command exited {exit_code}: {result.stderr[-1000:]}"
            else:
                content = computer.read_probe(PREFLIGHT_FILE) or b""
                wrote_file = PREFLIGHT_MARKER.encode() in content
                request = self.trajectory_request(preflight_spec)
                if request is not None:
                    raw = computer.read_probe(request.remote_path) or b""
                    tool_turns = self.count_tool_turns(raw)
                if not wrote_file:
                    reason = "preflight did not write the expected marker file"
                elif tool_turns < 2:
                    reason = f"preflight produced {tool_turns} tool turn(s); at least 2 are required"
        except Exception as exc:  # noqa: BLE001
            reason = f"preflight raised {type(exc).__name__}: {exc}"
        return PreflightResult(
            harness=self.name,
            model=spec.model,
            image=spec.image,
            passed=reason is None,
            exit_code=exit_code,
            tool_turns=tool_turns,
            wrote_file=wrote_file,
            reason=reason,
            digest=digest,
            runtime_seconds=time.monotonic() - started,
            cli_version_expected=self.cli_version,
            cli_version_actual=actual_version,
            version_matches=version_matches,
        )

    # -- staging -------------------------------------------------------------
    def _stage_inputs(self, computer: TaskComputer, spec: StageSpec) -> None:
        computer.upload_text(spec.instructions.rstrip() + "\n", INSTRUCTIONS_PATH)
        for staged in spec.staged_artifacts:
            stage_parent_artifact(computer, staged.local_path, staged.remote_path)
        self.extra_staging(computer, spec)

    def extra_staging(self, computer: TaskComputer, spec: StageSpec) -> None:
        """Hook for harness-specific files (config, prompt, etc.)."""

    def detect_version(self, computer: TaskComputer) -> str | None:
        """Return the CLI version string inside the sandbox, if checkable."""
        if self.version_command is None:
            return self.cli_version
        result = computer.exec(self.version_command, timeout=60)
        if not result.ok:
            return None
        lines = [
            line.strip()
            for line in (result.stdout + "\n" + result.stderr).splitlines()
            if line.strip()
        ]
        return lines[-1] if lines else None

    # -- helpers -------------------------------------------------------------
    @staticmethod
    def prompt_for(spec: StageSpec) -> str:
        if spec.stage == "preflight":
            return (
                f"Read {LOGS_DIR}/preflight/PREFLIGHT.md and carry out the exact "
                "sequence it contains, in order. Do not explore anything else. "
                "Finish by running `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` "
                "and stop."
            )
        return (
            "Read /home/agent/AGENTS.md for your full instructions, then complete the "
            f"assigned {spec.stage} stage. {spec.task}".strip()
        )


def write_result(path: Path, result: WorkerResult) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(result.to_dict(), indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return path


def read_result(path: Path) -> WorkerResult:
    return WorkerResult.from_dict(json.loads(path.read_text(encoding="utf-8")))


def trajectory_digest(data: bytes) -> str:
    return sha256_bytes(data)


__all__ = [
    "AGENT_DIR",
    "AUDIT_DIR",
    "BaseHarnessAdapter",
    "HarnessAdapter",
    "INSTRUCTIONS_PATH",
    "LOGS_DIR",
    "PREFLIGHT_FILE",
    "PREFLIGHT_MARKER",
    "PreflightResult",
    "SUCCESS_STATUSES",
    "SUBMISSION_DIR",
    "SandboxInfo",
    "StageSpec",
    "StagedArtifact",
    "TERMINAL_STATUSES",
    "Usage",
    "WorkerResult",
    "WorkerStatus",
    "normalize_status",
    "read_result",
    "trajectory_digest",
    "write_result",
]
