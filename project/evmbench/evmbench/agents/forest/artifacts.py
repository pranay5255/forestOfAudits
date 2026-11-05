"""Typed artifact transfer, validation, and manifests.

Artifacts cross stage boundaries through this module only.  Branches, judges,
mergers, and graders declare what they may read/write; the runner enforces the
declaration so a stage cannot smuggle the final submission or an unexpected
workspace file into a later stage.
"""

from __future__ import annotations

import hashlib
import io
import json
import tarfile
from dataclasses import asdict, dataclass
from pathlib import Path, PurePosixPath
from typing import Iterable, Literal

from evmbench.agents.backend.base import SUBMISSION_DIR, TaskComputer

ArtifactKind = Literal[
    "report",
    "findings",
    "evidence",
    "diff",
    "txs",
    "chain",
    "trajectory",
    "submission",
    "scout",
    "judge",
    "log",
    "extra",
]

VALID_KINDS: frozenset[str] = frozenset(
    {
        "report",
        "findings",
        "evidence",
        "diff",
        "txs",
        "chain",
        "trajectory",
        "submission",
        "scout",
        "judge",
        "log",
        "extra",
    }
)

DEFAULT_MAX_ARTIFACT_BYTES = 64 * 1024 * 1024
DEFAULT_MAX_TRAJECTORY_BYTES = 256 * 1024 * 1024

STAGE = Literal["scout", "branch", "tree_judge", "global_merger", "grade"]
MODE = Literal["detect", "patch", "exploit"]

SUBMISSION_FILENAMES: dict[str, str] = {
    "detect": "audit.md",
    "patch": "agent.diff",
    "exploit": "txs.json",
}


class ArtifactError(RuntimeError):
    """Raised when an artifact violates a transfer or contract rule."""


@dataclass(frozen=True)
class ArtifactRequest:
    kind: str
    remote_path: str
    local_name: str
    required: bool = True
    max_bytes: int = DEFAULT_MAX_ARTIFACT_BYTES

    def __post_init__(self) -> None:
        if self.kind not in VALID_KINDS:
            raise ArtifactError(f"Unknown artifact kind {self.kind!r}")
        ensure_safe_relative(self.local_name)


@dataclass(frozen=True)
class Artifact:
    kind: str
    path: str
    sha256: str
    bytes: int

    def to_dict(self) -> dict[str, object]:
        return asdict(self)


@dataclass(frozen=True)
class CollectionResult:
    """Outcome of downloading a set of declared artifacts."""

    artifacts: tuple[Artifact, ...] = ()
    missing_required: tuple[str, ...] = ()
    violations: tuple[str, ...] = ()

    @property
    def ok(self) -> bool:
        return not self.missing_required and not self.violations


@dataclass(frozen=True)
class StageContract:
    """Declarative read/write rules for a stage."""

    stage: str
    expected: tuple[ArtifactRequest, ...] = ()
    forbidden_remote_paths: tuple[str, ...] = ()
    requires_trajectory: bool = True

    def to_dict(self) -> dict[str, object]:
        return {
            "expected_artifacts": [artifact.local_name for artifact in self.expected],
            "forbidden_artifacts": list(self.forbidden_remote_paths),
            "checks": contract_checks(self),
        }


def contract_checks(contract: StageContract) -> list[str]:
    checks = ["artifact_exists", "artifact_nonempty", "no_forbidden_submission"]
    if contract.requires_trajectory:
        checks.append("trajectory_present")
    return checks


def submission_remote_path(mode: str) -> str:
    return f"{SUBMISSION_DIR}/{SUBMISSION_FILENAMES[mode]}"


def ensure_safe_relative(value: str) -> str:
    path = PurePosixPath(value)
    if path.is_absolute() or ".." in path.parts or str(path) in {"", "."}:
        raise ArtifactError(f"Unsafe artifact path: {value!r}")
    return str(path)


def sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect_artifacts(
    computer: TaskComputer,
    requests: Iterable[ArtifactRequest],
    dest_dir: Path,
) -> CollectionResult:
    """Download declared artifacts locally.

    Missing *optional* artifacts are skipped.  Missing required artifacts are
    reported as ``missing_required`` (the caller maps those to
    ``no_submission``); size/read/path problems are ``violations``.
    """
    dest_dir.mkdir(parents=True, exist_ok=True)
    artifacts: list[Artifact] = []
    missing: list[str] = []
    violations: list[str] = []
    for request in requests:
        try:
            data = computer.read_probe(request.remote_path)
        except Exception as exc:  # noqa: BLE001 - propagate as a violation
            violations.append(f"{request.kind}: failed to read {request.remote_path}: {exc}")
            continue
        if data is None:
            if request.required:
                missing.append(f"{request.kind}: missing or empty {request.remote_path}")
            continue
        if len(data) > request.max_bytes:
            violations.append(
                f"{request.kind}: {request.remote_path} is {len(data)} bytes, "
                f"exceeding the {request.max_bytes}-byte cap"
            )
            continue
        local_path = dest_dir / ensure_safe_relative(request.local_name)
        local_path.parent.mkdir(parents=True, exist_ok=True)
        local_path.write_bytes(data)
        artifacts.append(
            Artifact(
                kind=request.kind,
                path=request.local_name,
                sha256=sha256_bytes(data),
                bytes=len(data),
            )
        )
    return CollectionResult(
        artifacts=tuple(artifacts),
        missing_required=tuple(missing),
        violations=tuple(violations),
    )


def stage_contract(
    stage: str,
    mode: str,
    *,
    worker_id: str,
    role: str | None = None,
) -> StageContract:
    """Return the artifact contract for ``stage`` in ``mode``."""
    if stage == "scout":
        return StageContract(
            stage=stage,
            expected=(
                ArtifactRequest("scout", "/home/agent/forest/scout/scout.md", "scout.md"),
                ArtifactRequest(
                    "scout",
                    "/home/agent/forest/scout/scout.json",
                    "scout.json",
                    required=False,
                ),
            ),
            forbidden_remote_paths=(submission_remote_path(mode),),
        )
    if stage == "branch":
        # Worker ids are role-qualified (``accounting-branch-01``) while the
        # remote directory keeps the short branch name (``branch-01``).
        remote_worker = f"branch-{worker_id.rsplit('-branch-', 1)[-1]}" if "-branch-" in worker_id else worker_id
        base = f"/home/agent/forest/{role}/{remote_worker}"
        expected: list[ArtifactRequest] = [
            ArtifactRequest("report", f"{base}/branch.md", f"{worker_id}/branch.md"),
        ]
        if mode == "detect":
            expected.append(
                ArtifactRequest("findings", f"{base}/findings.json", f"{worker_id}/findings.json", required=False)
            )
            expected.append(
                ArtifactRequest("evidence", f"{base}/evidence.json", f"{worker_id}/evidence.json", required=False)
            )
        elif mode == "patch":
            expected.append(ArtifactRequest("diff", f"{base}/branch.diff", f"{worker_id}/branch.diff"))
        else:
            expected.append(ArtifactRequest("txs", f"{base}/branch.txs.json", f"{worker_id}/branch.txs.json"))
            expected.append(
                ArtifactRequest("chain", f"{base}/branch.chain.json", f"{worker_id}/branch.chain.json")
            )
        return StageContract(
            stage=stage,
            expected=tuple(expected),
            forbidden_remote_paths=(submission_remote_path(mode),),
        )
    if stage == "tree_judge":
        base = f"/home/agent/forest/{role}"
        return StageContract(
            stage=stage,
            expected=(ArtifactRequest("judge", f"{base}/judge.md", f"{role}/judge.md"),),
            forbidden_remote_paths=(submission_remote_path(mode),),
        )
    if stage in {"global_merger", "baseline"}:
        return StageContract(
            stage=stage,
            expected=(
                ArtifactRequest(
                    "submission",
                    submission_remote_path(mode),
                    Path(submission_remote_path(mode)).name,
                    max_bytes=DEFAULT_MAX_TRAJECTORY_BYTES,
                ),
            ),
            forbidden_remote_paths=(),
        )
    if stage == "grade":
        return StageContract(stage=stage, expected=(), requires_trajectory=False)
    raise ArtifactError(f"Unknown stage {stage!r}")


def write_manifest(path: Path, artifacts: Iterable[Artifact], *, extra: dict[str, object] | None = None) -> dict[str, object]:
    payload: dict[str, object] = {
        "schema_version": 1,
        "artifacts": [artifact.to_dict() for artifact in artifacts],
    }
    if extra:
        payload.update(extra)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return payload


def verify_manifest(path: Path, *, root: Path | None = None) -> list[str]:
    """Verify every manifest entry against the files on disk."""
    payload = json.loads(path.read_text(encoding="utf-8"))
    if root is None:
        root = path.parent
    violations: list[str] = []
    for entry in payload.get("artifacts", []):
        try:
            relative = ensure_safe_relative(str(entry["path"]))
        except (KeyError, ArtifactError) as exc:
            violations.append(f"invalid manifest entry {entry!r}: {exc}")
            continue
        target = root / relative
        if not target.exists():
            violations.append(f"missing artifact {relative}")
            continue
        actual = sha256_file(target)
        if actual != entry.get("sha256"):
            violations.append(f"hash mismatch for {relative}: {actual} != {entry.get('sha256')}")
    return violations


def safe_extract_tar(data: bytes, dest_dir: Path) -> None:
    """Extract a tar archive while rejecting path traversal and links."""
    dest_dir.mkdir(parents=True, exist_ok=True)
    root = dest_dir.resolve()
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:*") as archive:
        for member in archive.getmembers():
            member_path = (dest_dir / member.name).resolve()
            if member_path != root and root not in member_path.parents:
                raise ArtifactError(f"Refusing to extract tar member outside {dest_dir}: {member.name}")
            if member.issym() or member.islnk():
                raise ArtifactError(f"Refusing to extract link member: {member.name}")
        archive.extractall(dest_dir, filter="data")


def stage_parent_artifact(
    computer: TaskComputer,
    local_path: Path,
    remote_path: str,
) -> None:
    """Upload a previously collected artifact into a fresh stage sandbox."""
    source = Path(local_path)
    if not source.exists():
        raise ArtifactError(f"Cannot stage missing parent artifact {local_path}")
    computer.upload(source.read_bytes(), remote_path)


__all__ = [
    "Artifact",
    "ArtifactError",
    "ArtifactRequest",
    "DEFAULT_MAX_ARTIFACT_BYTES",
    "DEFAULT_MAX_TRAJECTORY_BYTES",
    "STAGE",
    "SUBMISSION_FILENAMES",
    "StageContract",
    "collect_artifacts",
    "contract_checks",
    "ensure_safe_relative",
    "safe_extract_tar",
    "sha256_bytes",
    "sha256_file",
    "stage_contract",
    "stage_parent_artifact",
    "submission_remote_path",
    "verify_manifest",
    "write_manifest",
]
