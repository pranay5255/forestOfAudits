"""Detached campaign coordinator.

One coordinator owns a campaign.  The local CLI is a control surface only: it
spawns the coordinator, then reads durable state.  Closing the terminal does not
kill the experiment.
"""

from __future__ import annotations

import argparse
import json
import os
import signal
import subprocess
import sys
import threading
import traceback
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Callable, cast

from evmbench.agents.agent import agent_registry
from evmbench.agents.backend.base import TaskComputer
from evmbench.agents.backend.local import LocalDockerComputer, LocalProcessComputer
from evmbench.agents.backend.modal import ModalComputer
from evmbench.agents.forest.pipeline import (
    AttemptOutcome,
    Cancellation,
    ForestPipeline,
    TaskDefinition,
    plan_stage_counts,
)
from evmbench.agents.harness import StageSpec, get_adapter
from evmbench.agents.harness.preflight import (
    PreflightError,
    preflight_spec,
    run_preflight,
)
from evmbench.audit import audit_registry
from evmbench.campaign.state import (
    CampaignConfig,
    CampaignStatus,
    CampaignStore,
    ConfigError,
    new_campaign_id,
)
from evmbench.nano.grade.modal import GradeRequest, grade_submission

EXIT_OK = 0
EXIT_TASK_FAILURE = 1
EXIT_CONFIG = 2


@dataclass
class SandboxPool:
    """Counting factory that enforces the campaign-wide sandbox cap."""

    factory: Callable[[StageSpec], TaskComputer]
    cap: int

    def __post_init__(self) -> None:
        self._semaphore = threading.BoundedSemaphore(self.cap)
        self._live: set[str] = set()
        self._lock = threading.Lock()

    def __call__(self, spec: StageSpec) -> TaskComputer:
        if not self._semaphore.acquire(timeout=0):
            raise RuntimeError(
                f"Sandbox cap reached ({self.cap}); refusing to create another sandbox"
            )
        try:
            computer = self.factory(spec)
        except Exception:
            self._semaphore.release()
            raise
        with self._lock:
            self._live.add(spec.worker_id)
        return _PooledComputer(computer, self)

    def release(self, worker_id: str) -> None:
        with self._lock:
            self._live.discard(worker_id)
        self._semaphore.release()


class _PooledComputer(TaskComputer):  # pragma: no cover - thin wrapper
    def __init__(self, inner: TaskComputer, pool: SandboxPool) -> None:
        super().__init__(inner.identity)
        self._inner = inner
        self._pool = pool
        self._released = False

    def exec(self, command: str, **kwargs: Any) -> Any:
        return self._inner.exec(command, **kwargs)

    def upload(self, data: bytes, remote_path: str) -> None:
        self._inner.upload(data, remote_path)

    def download(self, remote_path: str) -> bytes:
        return self._inner.download(remote_path)

    def list(self, remote_dir: str) -> list[str]:
        return self._inner.list(remote_dir)

    def side_target(self, name: str) -> TaskComputer | None:
        return self._inner.side_target(name)

    def terminate(self) -> None:
        try:
            self._inner.terminate()
        finally:
            if not self._released:
                self._released = True
                self._pool.release(self.identity.tags.get("worker_id", self.identity.sandbox_id))


# ---------------------------------------------------------------------------
# Sandbox factories
# ---------------------------------------------------------------------------


def make_sandbox_factory(config: CampaignConfig, *, root: Path | None = None) -> Callable[[StageSpec], TaskComputer]:
    if config.backend == "modal":
        def _modal(spec: StageSpec) -> TaskComputer:
            return ModalComputer.create(
                image=spec.image,
                campaign_id=spec.campaign_id,
                task_id=spec.task_id,
                attempt_id=spec.attempt_id,
                stage=spec.stage,
                worker_id=spec.worker_id,
                timeout_seconds=int(config.budget.sandbox_timeout),
                idle_timeout_seconds=int(config.budget.task_time_limit + 600),
                env=spec.env,
                secret_names=list(config.modal_secret_names),
                cpu=config.sandbox_cpu,
                memory_mb=config.sandbox_memory_mb,
                tags=spec.sandbox_tags,
            )

        return _modal

    def _local(spec: StageSpec) -> TaskComputer:
        if spec.image.startswith("local-process"):
            return LocalProcessComputer.create(
                root=root,
                campaign_id=spec.campaign_id,
                task_id=spec.task_id,
                attempt_id=spec.attempt_id,
                stage=spec.stage,
                worker_id=spec.worker_id,
            )
        # Local Docker has no Modal secret manager; forward only the known
        # provider credentials from the coordinator environment.
        env = dict(spec.env)
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "VLLM_API_KEY", "VLLM_API_BASE"):
            value = os.getenv(name)
            if value and name not in env:
                env[name] = value
        return LocalDockerComputer.create(
            image=spec.image,
            campaign_id=spec.campaign_id,
            task_id=spec.task_id,
            attempt_id=spec.attempt_id,
            stage=spec.stage,
            worker_id=spec.worker_id,
            env=env,
        )

    return _local


def make_grade_spec(config: CampaignConfig, campaign_id: str, audit_id: str, attempt_id: int) -> StageSpec:
    return StageSpec(
        campaign_id=campaign_id,
        task_id=audit_id,
        attempt_id=attempt_id,
        stage="grade",
        worker_id="grader",
        mode=config.mode,
        harness=config.harness,
        strategy=config.strategy,
        model=config.evaluator_model,
        image=config.image_for(audit_id),
        instructions="",
        task="grade",
        time_limit_seconds=config.budget.sandbox_timeout - 300,
    )


# ---------------------------------------------------------------------------
# Coordinator
# ---------------------------------------------------------------------------


class CampaignCoordinator:
    def __init__(
        self,
        *,
        campaign_id: str,
        store: CampaignStore,
        config: CampaignConfig | None = None,
        sandbox_factory: Callable[[StageSpec], TaskComputer] | None = None,
        cancellation: Cancellation | None = None,
    ) -> None:
        self.campaign_id = campaign_id
        self.store = store
        self.config = config or store.config(campaign_id)
        self.config.validate()
        base_factory = sandbox_factory or make_sandbox_factory(self.config)
        self.pool = SandboxPool(base_factory, self.config.sandbox_cap)
        self.cancellation = cancellation or Cancellation()
        self._preflights: dict[str, Any] = {}

    # -- planning ------------------------------------------------------------
    def plan(self) -> dict[str, Any]:
        audits = self._load_audits()
        counts = plan_stage_counts(self.config)
        per_task_workers = counts["workers_total"]
        return {
            "campaign_id": self.campaign_id,
            "backend": self.config.backend,
            "harness": self.config.harness,
            "strategy": self.config.strategy,
            "mode": self.config.mode,
            "candidate_model": self.config.candidate_model,
            "evaluator_model": self.config.evaluator_model,
            "audits": [audit.id for audit in audits],
            "stages": counts,
            "expected_workers_per_task": per_task_workers,
            "expected_workers_total": per_task_workers * len(audits),
            "expected_sandboxes_total": (per_task_workers + 1) * len(audits),
            "sandbox_cap": self.config.sandbox_cap,
            "worker_concurrency": self.config.worker_concurrency,
            "budget": self.config.budget.__dict__,
        }

    def _load_audits(self) -> list[Any]:
        audits: list[Any] = []
        for audit_id in self.config.audit_ids:
            try:
                audit = audit_registry.get_audit(
                    audit_id,
                    findings_subdir=cast(Any, self.config.findings_subdir),
                )
            except Exception as exc:  # noqa: BLE001
                raise ConfigError(f"Unknown audit {audit_id!r}: {exc}") from exc
            if self.config.mode == "patch":
                audit = audit.retain_only_patch_vulnerabilities()
            elif self.config.mode == "exploit":
                audit = audit.retain_only_exploit_vulnerabilities()
            if not audit.vulnerabilities:
                raise ConfigError(f"Audit {audit_id} has no vulnerabilities for mode {self.config.mode}")
            if self.config.mode in ("patch", "exploit") and not audit.base_commit:
                raise ConfigError(f"Audit {audit_id} has no base_commit for {self.config.mode} mode")
            audits.append(audit)
        return audits

    # -- preflight -----------------------------------------------------------
    def preflight(self, *, refresh: bool = False) -> dict[str, Any]:
        audits = self._load_audits()
        cache_dir = self.store.campaign_dir(self.campaign_id) / "preflight"
        adapter = get_adapter(self.config.harness)
        reports = {}
        images = sorted({self.config.image_for(audit.id) for audit in audits})
        for image in images:
            spec = preflight_spec(
                campaign_id=self.campaign_id,
                harness=self.config.harness,
                model=self.config.candidate_model,
                image=image,
            )
            result = run_preflight(
                adapter,
                spec=spec,
                computer_factory=lambda spec=spec: self.pool(spec),  # type: ignore[misc]
                cache_dir=cache_dir,
                refresh=refresh,
            )
            reports[image] = result.to_dict()
            self.store.log_event(
                self.campaign_id,
                "preflight",
                harness=self.config.harness,
                image=image,
                model=self.config.candidate_model,
                passed=result.passed,
                tool_turns=result.tool_turns,
                wrote_file=result.wrote_file,
            )
        self._preflights = reports
        return reports

    # -- run -----------------------------------------------------------------
    def run(self) -> int:
        self._start_cancel_watcher()
        self.store.clear_cancel(self.campaign_id)
        self.store.set_status(self.campaign_id, CampaignStatus.PREFLIGHTING)
        try:
            audits = self._load_audits()
            self.preflight()
        except (ConfigError, PreflightError) as exc:
            self.store.set_status(self.campaign_id, CampaignStatus.FAILED, error=str(exc), exit_code=EXIT_CONFIG)
            return EXIT_CONFIG

        self.store.update_campaign(
            self.campaign_id,
            planned_audits=[audit.id for audit in audits],
            expected_workers_per_task=plan_stage_counts(self.config)["workers_total"],
        )
        self.store.set_status(self.campaign_id, CampaignStatus.RUNNING)

        exit_code = EXIT_OK
        for audit in audits:
            if self.cancellation.cancelled:
                exit_code = EXIT_TASK_FAILURE
                break
            self.store.set_status(self.campaign_id, CampaignStatus.RUNNING)
            task_status = self._run_task(audit)
            if task_status != "graded":
                exit_code = EXIT_TASK_FAILURE

        if self.cancellation.cancelled:
            self.store.set_status(self.campaign_id, CampaignStatus.CANCELLED, exit_code=EXIT_TASK_FAILURE)
        elif exit_code == EXIT_OK:
            self.store.set_status(self.campaign_id, CampaignStatus.COMPLETED, exit_code=EXIT_OK)
        else:
            self.store.set_status(self.campaign_id, CampaignStatus.FAILED, exit_code=exit_code)
        summary = self.store.summary(self.campaign_id)
        self.store.log_event(self.campaign_id, "campaign_summary", summary=summary)
        (self.store.campaign_dir(self.campaign_id) / "summary.json").write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return exit_code

    def _start_cancel_watcher(self) -> None:
        """Poll the durable cancel flag so any control surface can stop us."""

        def _watch() -> None:
            import time

            while not self.cancellation.cancelled:
                if self.store.cancellation_requested(self.campaign_id):
                    self.cancellation.cancel()
                    self.store.log_event(self.campaign_id, "cancel_flag_observed")
                    return
                time.sleep(1.0)

        threading.Thread(target=_watch, name="campaign-cancel-watcher", daemon=True).start()

    def _run_task(self, audit: Any) -> str:
        task_id = audit.id
        attempt_id = 0
        image = self.config.image_for(audit.id)
        instructions = agent_registry.load_instructions(self.config.mode, audit, self.config.hint_level)
        adapter = get_adapter(self.config.harness)
        self.store.write_task(
            self.campaign_id,
            task_id,
            {
                "audit_id": task_id,
                "mode": self.config.mode,
                "image": image,
                "attempts": [attempt_id],
                "base_commit": audit.base_commit,
                "vulnerabilities": [vulnerability.id for vulnerability in audit.vulnerabilities],
            },
        )
        pipeline = ForestPipeline(
            config=self.config,
            store=self.store,
            adapter=adapter,
            sandbox_factory=self.pool,
            task=TaskDefinition(audit=audit, instructions=instructions, image=image),
            campaign_id=self.campaign_id,
            cancellation=self.cancellation,
        )
        outcome = pipeline.run_attempt(attempt_id)
        self._write_attempt(task_id, attempt_id, outcome)

        if outcome.submission_path is None:
            self.store.update_campaign(
                self.campaign_id,
                tasks={**self.store.load_campaign(self.campaign_id).get("tasks", {}), task_id: {
                    "audit_id": task_id,
                    "status": outcome.status,
                    "error": outcome.error,
                    "grade": {"graded": False},
                }},
            )
            return outcome.status

        self.store.set_status(self.campaign_id, CampaignStatus.GRADING)
        return self._grade_task(audit, attempt_id, outcome)

    def _grade_task(self, audit: Any, attempt_id: int, outcome: AttemptOutcome) -> str:
        assert outcome.submission_path is not None
        spec = make_grade_spec(self.config, self.campaign_id, audit.id, attempt_id)
        computer: TaskComputer | None = None
        try:
            if self.config.mode in ("patch", "exploit"):
                computer = self.pool(spec)
            grade = grade_submission(
                GradeRequest(
                    audit=audit,
                    mode=self.config.mode,
                    submission_path=outcome.submission_path,
                    evaluator_model=self.config.evaluator_model,
                    run_group_id=self.campaign_id,
                    run_id=f"{audit.id}-a{attempt_id}",
                    runs_dir=str(self.store.attempt_dir(self.campaign_id, audit.id, attempt_id)),
                ),
                computer=computer,
            )
        finally:
            if computer is not None:
                computer.terminate()

        payload = grade.to_dict()
        payload["attempt_status"] = outcome.status
        self.store.write_grade(self.campaign_id, audit.id, attempt_id, payload)
        state = self.store.load_campaign(self.campaign_id)
        task_state = state.get("tasks", {}).get(audit.id, {"audit_id": audit.id, "attempts": {}})
        task_state["status"] = outcome.status if grade.graded else "not_graded"
        task_state["grade"] = payload
        state.setdefault("tasks", {})[audit.id] = task_state
        self.store.save_campaign(self.campaign_id, state)
        if not grade.graded:
            return "not_graded"
        if outcome.status == "degraded":
            # A degraded attempt has a real grade but is not a clean result.
            return "degraded"
        return "graded" if outcome.status == "completed" else "failed"

    def _write_attempt(self, task_id: str, attempt_id: int, outcome: AttemptOutcome) -> None:
        self.store.write_attempt(
            self.campaign_id,
            task_id,
            attempt_id,
            {
                "attempt_id": attempt_id,
                "status": outcome.status,
                "error": outcome.error,
                "selected_roles": list(outcome.selected_roles),
                "submission": outcome.submission_path.name if outcome.submission_path else None,
                "chain_isolation": "single_sandbox" if self.config.mode == "exploit" else None,
                "workers": [result.to_dict() for result in outcome.results],
            },
        )


# ---------------------------------------------------------------------------
# Control surface
# ---------------------------------------------------------------------------


def store_for(config: CampaignConfig) -> CampaignStore:
    root = Path(config.output_dir)
    root.mkdir(parents=True, exist_ok=True)
    return CampaignStore(root)


def plan_campaign(config: CampaignConfig, *, campaign_id: str | None = None, store: CampaignStore | None = None) -> dict[str, Any]:
    config.validate()
    store = store or store_for(config)
    campaign_id = campaign_id or new_campaign_id()
    if not store.campaign_path(campaign_id).exists():
        store.create(config, campaign_id=campaign_id)
    coordinator = CampaignCoordinator(campaign_id=campaign_id, store=store, config=config)
    return coordinator.plan()


def run_campaign(campaign_id: str, store: CampaignStore, config: CampaignConfig | None = None) -> int:
    coordinator = CampaignCoordinator(campaign_id=campaign_id, store=store, config=config)
    return coordinator.run()


def spawn_detached(campaign_id: str, store: CampaignStore, *, log_dir: Path | None = None) -> int:
    """Spawn the coordinator in a new session so terminal closure is harmless."""
    root = log_dir or (store.campaign_dir(campaign_id))
    root.mkdir(parents=True, exist_ok=True)
    stdout = (root / "coordinator.stdout.log").open("a", encoding="utf-8")
    stderr = (root / "coordinator.stderr.log").open("a", encoding="utf-8")
    process = subprocess.Popen(
        [
            sys.executable,
            "-m",
            "evmbench.campaign.coordinator",
            "--campaign-id",
            campaign_id,
            "--store-root",
            str(store.root),
        ],
        cwd=str(Path(__file__).resolve().parents[2]),
        stdout=stdout,
        stderr=stderr,
        stdin=subprocess.DEVNULL,
        start_new_session=True,
    )
    (root / "coordinator.pid").write_text(str(process.pid) + "\n", encoding="utf-8")
    return process.pid


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--campaign-id", required=True)
    parser.add_argument("--store-root", default=None)
    args = parser.parse_args(argv)

    store = CampaignStore(Path(args.store_root) if args.store_root else Path.cwd())
    config = store.config(args.campaign_id)
    cancellation = Cancellation()

    def _handle(signum: int, _frame: object) -> None:
        cancellation.cancel()
        store.log_event(args.campaign_id, "cancellation_requested", signal=signum)

    signal.signal(signal.SIGINT, _handle)
    signal.signal(signal.SIGTERM, _handle)

    try:
        coordinator = CampaignCoordinator(
            campaign_id=args.campaign_id,
            store=store,
            config=config,
            cancellation=cancellation,
        )
        return coordinator.run()
    except Exception as exc:  # noqa: BLE001 - persist failures
        store.log_event(args.campaign_id, "coordinator_crashed", error=traceback.format_exc())
        store.set_status(args.campaign_id, CampaignStatus.FAILED, error=f"{type(exc).__name__}: {exc}")
        return EXIT_TASK_FAILURE


if __name__ == "__main__":
    raise SystemExit(main())
