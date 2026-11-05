"""Durable campaign state, configuration, and idempotent stage bookkeeping.

State is written incrementally after every stage transition so a hard kill still
leaves a resumable campaign.  The layout matches the repair plan::

    campaigns/<campaign_id>/
      campaign.json
      events.jsonl
      tasks/<task_id>/attempts/<attempt_id>/
        stages/<stage>/<worker_id>/{spec.json,result.json,trajectory.*,artifacts/}
        submission/{audit.md|agent.diff|txs.json}
        grade/grade.json
"""

from __future__ import annotations

import json
import os
import re
import threading
import time
import uuid
from dataclasses import asdict, dataclass, field
from enum import Enum
from pathlib import Path
from typing import Any, Literal

from evmbench.agents.forest.artifacts import (
    submission_remote_path,
    verify_manifest,
    write_manifest,
)
from evmbench.agents.harness.base import WorkerResult, WorkerStatus

Backend = Literal["local", "modal"]
Harness = Literal["opencode", "codex", "yudai"]
Strategy = Literal["baseline", "forest"]
Mode = Literal["detect", "patch", "exploit"]
HintLevel = Literal["none", "low", "med", "high", "max"]

SUPPORTED_BACKENDS: tuple[str, ...] = ("local", "modal")
SUPPORTED_HARNESSES: tuple[str, ...] = ("opencode", "codex", "yudai")
SUPPORTED_STRATEGIES: tuple[str, ...] = ("baseline", "forest")
SUPPORTED_MODES: tuple[str, ...] = ("detect", "patch", "exploit")
SUPPORTED_HINT_LEVELS: tuple[str, ...] = ("none", "low", "med", "high", "max")

DEFAULT_CANDIDATE_MODEL = "deepseek/deepseek-v4.1-flash-1017"
DEFAULT_EVALUATOR_MODEL = "openai/gpt-6-luna"

_SAFE_ID = re.compile(r"[^A-Za-z0-9._-]+")


class ConfigError(ValueError):
    """Raised for invalid campaign configuration."""


class CampaignStatus(str, Enum):
    PLANNED = "planned"
    PREFLIGHTING = "preflighting"
    RUNNING = "running"
    GRADING = "grading"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


TERMINAL_CAMPAIGN_STATUSES = frozenset(
    {CampaignStatus.COMPLETED, CampaignStatus.FAILED, CampaignStatus.CANCELLED}
)


@dataclass(frozen=True)
class BudgetSpec:
    scout_step_limit: int = 16
    scout_cost_limit: float = 2.0
    branch_step_limit: int = 36
    branch_cost_limit: float = 5.0
    judge_step_limit: int = 24
    judge_cost_limit: float = 4.0
    global_step_limit: int = 36
    global_cost_limit: float = 8.0
    baseline_step_limit: int = 80
    baseline_cost_limit: float = 12.0
    task_time_limit: float = 3600.0
    sandbox_timeout: float = 4200.0

    def validate(self) -> None:
        if self.sandbox_timeout <= self.task_time_limit:
            raise ConfigError("sandbox_timeout must exceed task_time_limit plus cleanup margin")
        for name in (
            "scout_step_limit",
            "branch_step_limit",
            "judge_step_limit",
            "global_step_limit",
            "baseline_step_limit",
        ):
            if getattr(self, name) < 1:
                raise ConfigError(f"{name} must be >= 1")
        for name in (
            "scout_cost_limit",
            "branch_cost_limit",
            "judge_cost_limit",
            "global_cost_limit",
            "baseline_cost_limit",
            "task_time_limit",
        ):
            if getattr(self, name) <= 0:
                raise ConfigError(f"{name} must be positive")


@dataclass(frozen=True)
class CampaignConfig:
    backend: Backend = "modal"
    harness: Harness = "opencode"
    strategy: Strategy = "forest"
    mode: Mode = "detect"
    candidate_model: str = DEFAULT_CANDIDATE_MODEL
    evaluator_model: str = DEFAULT_EVALUATOR_MODEL
    audit_ids: tuple[str, ...] = ()
    hint_level: HintLevel = "none"
    findings_subdir: str = ""
    branches_per_tree: int = 1
    tree_roles: tuple[str, ...] = ()
    max_tree_roles: int = 4
    worker_concurrency: int = 3
    sandbox_cap: int = 40
    image_repo: str = "evmbench/audit"
    image_digests: dict[str, str] = field(default_factory=dict)
    # Per-audit image overrides, e.g. a harness-pinned rebuild.
    image_overrides: dict[str, str] = field(default_factory=dict)
    modal_secret_names: tuple[str, ...] = ("openrouter-api-key",)
    budget: BudgetSpec = field(default_factory=BudgetSpec)
    output_dir: str = "runs/campaigns"
    artifact_root: str = "/campaigns"
    continue_on_worker_error: bool = False
    sandbox_cpu: float = 2.0
    sandbox_memory_mb: int = 4096
    notes: str = ""

    def validate(self) -> None:
        if self.backend not in SUPPORTED_BACKENDS:
            raise ConfigError(f"backend must be one of {SUPPORTED_BACKENDS}, got {self.backend!r}")
        if self.harness not in SUPPORTED_HARNESSES:
            raise ConfigError(f"harness must be one of {SUPPORTED_HARNESSES}, got {self.harness!r}")
        if self.strategy not in SUPPORTED_STRATEGIES:
            raise ConfigError(f"strategy must be one of {SUPPORTED_STRATEGIES}, got {self.strategy!r}")
        if self.mode not in SUPPORTED_MODES:
            raise ConfigError(f"mode must be one of {SUPPORTED_MODES}, got {self.mode!r}")
        if self.hint_level not in SUPPORTED_HINT_LEVELS:
            raise ConfigError(f"unsupported hint_level {self.hint_level!r}")
        if self.mode != "exploit" and self.hint_level in ("high", "max"):
            raise ConfigError(f"hint_level={self.hint_level!r} is only valid in exploit mode")
        if not self.audit_ids:
            raise ConfigError("audit_ids must contain at least one audit")
        if self.strategy == "forest":
            if self.branches_per_tree < 1:
                raise ConfigError("branches_per_tree must be >= 1")
            if self.max_tree_roles < 1:
                raise ConfigError("max_tree_roles must be >= 1")
        registry_host = self.image_repo.split("/")[0]
        if self.backend == "modal" and (
            self.image_repo.startswith("evmbench/")
            or ("." not in registry_host and ":" not in registry_host)
        ):
            raise ConfigError(
                "Modal requires a registry-pullable image repository (for example "
                "ghcr.io/<org>/evmbench/audit); local tags such as evmbench/audit are "
                "not pullable. Pass --image-repo or set EVMBENCH_AUDIT_IMAGE_REPO."
            )
        if self.worker_concurrency < 1:
            raise ConfigError("worker_concurrency must be >= 1")
        if self.sandbox_cap < self.worker_concurrency:
            raise ConfigError("sandbox_cap must be >= worker_concurrency")
        if not self.candidate_model.strip():
            raise ConfigError("candidate_model must not be empty")
        if self.mode == "detect" and not self.evaluator_model.strip():
            raise ConfigError("evaluator_model is required for detect mode")
        self.budget.validate()

    def image_for(self, audit_id: str) -> str:
        override = self.image_overrides.get(audit_id)
        if override:
            return override
        # ``image_repo`` is a repository, not a tag: audit ids are the tag.
        digest = self.image_digests.get(audit_id)
        reference = f"{self.image_repo}:{audit_id}"
        return f"{reference}@{digest}" if digest else reference

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["audit_ids"] = list(self.audit_ids)
        payload["tree_roles"] = list(self.tree_roles)
        payload["modal_secret_names"] = list(self.modal_secret_names)
        return payload

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> "CampaignConfig":
        data = dict(payload)
        data["audit_ids"] = tuple(data.get("audit_ids", ()))
        data["tree_roles"] = tuple(data.get("tree_roles", ()))
        data["modal_secret_names"] = tuple(data.get("modal_secret_names", ()))
        data["image_digests"] = dict(data.get("image_digests", {}))
        data["image_overrides"] = dict(data.get("image_overrides", {}))
        data["budget"] = BudgetSpec(**data.get("budget", {}))
        return cls(**data)

    def expected_worker_count(self, roles: int | None = None) -> int:
        if self.strategy == "baseline":
            return 1
        role_count = roles if roles is not None else max(self.max_tree_roles, 1)
        return 1 + role_count * self.branches_per_tree + role_count + 1

    def expected_sandbox_count(self, roles: int | None = None) -> int:
        return len(self.audit_ids) * (self.expected_worker_count(roles) + 1)


def new_campaign_id(prefix: str = "cmp") -> str:
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    return f"{prefix}-{stamp}-{uuid.uuid4().hex[:8]}"


@dataclass(frozen=True)
class StageKey:
    campaign_id: str
    task_id: str
    attempt_id: int
    stage: str
    worker_id: str

    @property
    def tuple(self) -> tuple[str, str, int, str, str]:
        return (self.campaign_id, self.task_id, self.attempt_id, self.stage, self.worker_id)


_SECRET_ENV = ("KEY", "TOKEN", "SECRET", "PRIVATE", "PASSWORD", "CREDENTIAL")


def _redact_spec(payload: dict[str, Any]) -> dict[str, Any]:
    """Remove secrets and absolute host paths from persisted spec metadata."""
    safe = dict(payload)
    env = safe.get("env")
    if isinstance(env, dict):
        safe["env"] = {
            str(key): "<redacted>" if any(marker in str(key).upper() for marker in _SECRET_ENV) else value
            for key, value in env.items()
        }
    staged = safe.get("staged_artifacts")
    if isinstance(staged, list):
        safe["staged_artifacts"] = [
            {
                "local_path": Path(str(item.get("local_path", ""))).name,
                "remote_path": item.get("remote_path"),
            }
            for item in staged
            if isinstance(item, dict)
        ]
    if isinstance(safe.get("artifact_root"), str):
        safe["artifact_root"] = Path(safe["artifact_root"]).name
    return safe


def _atomic_write(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    # Unique temp name: stage workers write campaign.json concurrently.
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.{uuid.uuid4().hex[:8]}.tmp")
    with tmp.open("w", encoding="utf-8") as handle:
        handle.write(text)
        handle.flush()
        os.fsync(handle.fileno())
    tmp.replace(path)


class CampaignStore:
    """Filesystem-backed campaign state with an optional durable mirror."""

    def __init__(self, root: Path, *, mirror_root: Path | None = None) -> None:
        self.root = Path(root)
        self.mirror_root = Path(mirror_root) if mirror_root else None
        self._state_lock = threading.RLock()

    # -- construction --------------------------------------------------------
    def campaign_dir(self, campaign_id: str) -> Path:
        return self.root / _SAFE_ID.sub("-", campaign_id)

    def create(self, config: CampaignConfig, *, campaign_id: str | None = None) -> str:
        config.validate()
        campaign_id = campaign_id or new_campaign_id()
        directory = self.campaign_dir(campaign_id)
        directory.mkdir(parents=True, exist_ok=True)
        self.save_campaign(
            campaign_id,
            {
                "campaign_id": campaign_id,
                "schema_version": 1,
                "status": CampaignStatus.PLANNED.value,
                "created_at": time.time(),
                "updated_at": time.time(),
                "config": config.to_dict(),
                "audit_ids": list(config.audit_ids),
                "mode": config.mode,
                "harness": config.harness,
                "strategy": config.strategy,
                "backend": config.backend,
                "candidate_model": config.candidate_model,
                "evaluator_model": config.evaluator_model,
                "expected_workers_per_task": config.expected_worker_count(),
                "artifact_root": config.artifact_root,
                "tasks": {},
            },
        )
        self.log_event(campaign_id, "campaign_created", config=config.to_dict())
        return campaign_id

    def resume(self, campaign_id: str) -> dict[str, Any]:
        state = self.load_campaign(campaign_id)
        if state["status"] in (CampaignStatus.COMPLETED.value, CampaignStatus.CANCELLED.value):
            raise ConfigError(f"Cannot resume campaign {campaign_id} in status {state['status']!r}")
        return state

    # -- campaign.json -------------------------------------------------------
    def campaign_path(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "campaign.json"

    def load_campaign(self, campaign_id: str) -> dict[str, Any]:
        path = self.campaign_path(campaign_id)
        if not path.exists():
            raise FileNotFoundError(f"Unknown campaign {campaign_id}: {path}")
        return json.loads(path.read_text(encoding="utf-8"))

    def save_campaign(self, campaign_id: str, state: dict[str, Any]) -> None:
        state = dict(state)
        state["updated_at"] = time.time()
        text = json.dumps(state, indent=2, sort_keys=True, default=str) + "\n"
        _atomic_write(self.campaign_path(campaign_id), text)
        if self.mirror_root is not None:
            _atomic_write(self.mirror_root / _SAFE_ID.sub("-", campaign_id) / "campaign.json", text)

    def update_campaign(self, campaign_id: str, **updates: Any) -> dict[str, Any]:
        with self._state_lock:
            state = self.load_campaign(campaign_id)
            state.update(updates)
            self.save_campaign(campaign_id, state)
            return state

    def set_status(self, campaign_id: str, status: CampaignStatus, **extra: Any) -> dict[str, Any]:
        if status in TERMINAL_CAMPAIGN_STATUSES:
            extra.setdefault("finished_at", time.time())
        state = self.update_campaign(campaign_id, status=status.value, **extra)
        self.log_event(campaign_id, "campaign_status", status=status.value, **extra)
        return state

    def config(self, campaign_id: str) -> CampaignConfig:
        return CampaignConfig.from_dict(self.load_campaign(campaign_id)["config"])

    # -- events --------------------------------------------------------------
    def events_path(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "events.jsonl"

    def log_event(self, campaign_id: str, event: str, **fields: Any) -> None:
        path = self.events_path(campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        payload = {"ts": time.time(), "event": event, **fields}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(payload, default=str) + "\n")
            handle.flush()
            os.fsync(handle.fileno())

    def read_events(self, campaign_id: str) -> list[dict[str, Any]]:
        path = self.events_path(campaign_id)
        if not path.exists():
            return []
        return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]

    # -- cancellation --------------------------------------------------------
    def cancel_flag_path(self, campaign_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "cancel.requested"

    def request_cancel(self, campaign_id: str, reason: str = "") -> None:
        path = self.cancel_flag_path(campaign_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(reason or "cancel requested\n", encoding="utf-8")
        self.log_event(campaign_id, "cancel_requested", reason=reason)

    def cancellation_requested(self, campaign_id: str) -> bool:
        return self.cancel_flag_path(campaign_id).exists()

    def clear_cancel(self, campaign_id: str) -> None:
        path = self.cancel_flag_path(campaign_id)
        if path.exists():
            path.unlink()

    # -- task / attempt / stage paths ---------------------------------------
    def task_dir(self, campaign_id: str, task_id: str) -> Path:
        return self.campaign_dir(campaign_id) / "tasks" / _SAFE_ID.sub("-", task_id)

    def attempt_dir(self, campaign_id: str, task_id: str, attempt_id: int) -> Path:
        return self.task_dir(campaign_id, task_id) / "attempts" / str(attempt_id)

    def stage_dir(self, key: StageKey) -> Path:
        return (
            self.attempt_dir(key.campaign_id, key.task_id, key.attempt_id)
            / "stages"
            / _SAFE_ID.sub("-", key.stage)
            / _SAFE_ID.sub("-", key.worker_id)
        )

    def write_task(self, campaign_id: str, task_id: str, payload: dict[str, Any]) -> None:
        _atomic_write(self.task_dir(campaign_id, task_id) / "task.json", json.dumps(payload, indent=2, default=str) + "\n")

    def write_attempt(self, campaign_id: str, task_id: str, attempt_id: int, payload: dict[str, Any]) -> None:
        _atomic_write(
            self.attempt_dir(campaign_id, task_id, attempt_id) / "attempt.json",
            json.dumps(payload, indent=2, default=str) + "\n",
        )

    def write_spec(self, key: StageKey, payload: dict[str, Any]) -> Path:
        path = self.stage_dir(key) / "spec.json"
        safe = _redact_spec(payload)
        _atomic_write(path, json.dumps(safe, indent=2, sort_keys=True, default=str) + "\n")
        return path

    def write_result(self, key: StageKey, result: WorkerResult) -> Path:
        path = self.stage_dir(key) / "result.json"
        _atomic_write(path, json.dumps(result.to_dict(), indent=2, sort_keys=True, default=str) + "\n")
        write_manifest(
            self.stage_dir(key) / "manifest.json",
            result.artifacts,
            extra={
                "worker_id": result.worker_id,
                "stage": result.stage,
                "status": result.status.value,
                "trajectory": result.trajectory.to_dict() if result.trajectory else None,
            },
        )
        self.log_event(
            key.campaign_id,
            "stage_finished",
            task_id=key.task_id,
            attempt_id=key.attempt_id,
            stage=key.stage,
            worker_id=key.worker_id,
            status=result.status.value,
            failure_reason=result.failure_reason,
            runtime_seconds=result.runtime_seconds,
        )
        return path

    def read_result(self, key: StageKey) -> WorkerResult | None:
        path = self.stage_dir(key) / "result.json"
        if not path.exists():
            return None
        try:
            return WorkerResult.from_dict(json.loads(path.read_text(encoding="utf-8")))
        except (json.JSONDecodeError, KeyError, TypeError, ValueError):
            return None

    def stage_complete(self, key: StageKey) -> bool:
        """True when the stage has a completed result and intact artifacts."""
        result = self.read_result(key)
        if result is None or result.status is not WorkerStatus.COMPLETED:
            return False
        artifact_root = self.stage_dir(key) / "artifacts"
        for artifact in result.artifacts:
            path = artifact_root / artifact.path
            if not path.exists():
                return False
        if result.trajectory is not None and not (artifact_root / result.trajectory.path).exists():
            return False
        manifest = self.stage_dir(key) / "manifest.json"
        if manifest.exists():
            try:
                if verify_manifest(manifest, root=artifact_root):
                    return False
            except Exception:  # noqa: BLE001 - an unreadable manifest is not complete
                return False
        return True

    # -- submissions and grades ---------------------------------------------
    def submission_dir(self, campaign_id: str, task_id: str, attempt_id: int) -> Path:
        return self.attempt_dir(campaign_id, task_id, attempt_id) / "submission"

    def write_submission(self, campaign_id: str, task_id: str, attempt_id: int, mode: str, data: bytes) -> Path:
        name = Path(submission_remote_path(mode)).name
        path = self.submission_dir(campaign_id, task_id, attempt_id) / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return path

    def read_submission(self, campaign_id: str, task_id: str, attempt_id: int, mode: str) -> Path | None:
        path = self.submission_dir(campaign_id, task_id, attempt_id) / Path(submission_remote_path(mode)).name
        return path if path.exists() else None

    def write_grade(self, campaign_id: str, task_id: str, attempt_id: int, payload: dict[str, Any]) -> Path:
        path = self.attempt_dir(campaign_id, task_id, attempt_id) / "grade" / "grade.json"
        _atomic_write(path, json.dumps(payload, indent=2, sort_keys=True, default=str) + "\n")
        with self._state_lock:
            state = self.load_campaign(campaign_id)
            task = state.setdefault("tasks", {}).setdefault(
                task_id, {"audit_id": task_id, "attempts": {}}
            )
            task["grade"] = payload
            self.save_campaign(campaign_id, state)
        self.log_event(
            campaign_id,
            "task_graded",
            task_id=task_id,
            attempt_id=attempt_id,
            score=payload.get("score"),
            max_score=payload.get("max_score"),
            graded=payload.get("graded"),
        )
        return path

    def read_grade(self, campaign_id: str, task_id: str, attempt_id: int) -> dict[str, Any] | None:
        path = self.attempt_dir(campaign_id, task_id, attempt_id) / "grade" / "grade.json"
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    # -- summarization -------------------------------------------------------
    def summary(self, campaign_id: str) -> dict[str, Any]:
        state = self.load_campaign(campaign_id)
        tasks = state.get("tasks", {})
        stage_status: dict[str, int] = {}
        failure_modes: dict[str, int] = {}
        per_stage: dict[str, dict[str, int]] = {}
        total_cost = 0.0
        for task in tasks.values():
            for attempt in task.get("attempts", {}).values():
                for stage in attempt.get("stages", {}).values():
                    status = stage.get("status", "unknown")
                    stage_status[status] = stage_status.get(status, 0) + 1
                    stage_name = str(stage.get("stage", "unknown"))
                    bucket = per_stage.setdefault(stage_name, {})
                    bucket[status] = bucket.get(status, 0) + 1
                    if stage.get("failure_reason"):
                        key = str(stage["failure_reason"]).split(":")[0][:120]
                        failure_modes[key] = failure_modes.get(key, 0) + 1
                    total_cost += float(stage.get("cost_usd", 0.0) or 0.0)
        scores = [
            task.get("grade", {}).get("score")
            for task in tasks.values()
            if task.get("grade", {}).get("graded")
        ]
        return {
            "campaign_id": campaign_id,
            "status": state.get("status"),
            "mode": state.get("mode"),
            "harness": state.get("harness"),
            "strategy": state.get("strategy"),
            "tasks": len(tasks),
            "graded_tasks": len(scores),
            "score_sum": sum(float(score or 0) for score in scores),
            "stage_status": stage_status,
            "per_stage": per_stage,
            "failure_modes": failure_modes,
            "total_cost_usd": total_cost,
        }

    def record_stage(
        self,
        campaign_id: str,
        task_id: str,
        attempt_id: int,
        result: WorkerResult,
    ) -> None:
        with self._state_lock:
            self._record_stage_unlocked(campaign_id, task_id, attempt_id, result)

    def _record_stage_unlocked(
        self,
        campaign_id: str,
        task_id: str,
        attempt_id: int,
        result: WorkerResult,
    ) -> None:
        state = self.load_campaign(campaign_id)
        tasks = state.setdefault("tasks", {})
        task = tasks.setdefault(task_id, {"audit_id": task_id, "attempts": {}})
        attempt = task.setdefault("attempts", {}).setdefault(str(attempt_id), {"stages": {}})
        stages = attempt.setdefault("stages", {})
        stages[result.worker_id] = {
            "stage": result.stage,
            "status": result.status.value,
            "failure_reason": result.failure_reason,
            "runtime_seconds": result.runtime_seconds,
            "cost_usd": result.usage.cost_usd,
            "sandbox_id": result.sandbox.id,
        }
        self.save_campaign(campaign_id, state)


def load_campaign_config(path: str | Path) -> CampaignConfig:
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    return CampaignConfig.from_dict(payload)


def config_to_json(config: CampaignConfig) -> str:
    return json.dumps(config.to_dict(), indent=2, sort_keys=True) + "\n"


__all__ = [
    "Backend",
    "BudgetSpec",
    "CampaignConfig",
    "CampaignStatus",
    "CampaignStore",
    "ConfigError",
    "DEFAULT_CANDIDATE_MODEL",
    "DEFAULT_EVALUATOR_MODEL",
    "Harness",
    "Mode",
    "StageKey",
    "Strategy",
    "TERMINAL_CAMPAIGN_STATUSES",
    "config_to_json",
    "load_campaign_config",
    "new_campaign_id",
]
