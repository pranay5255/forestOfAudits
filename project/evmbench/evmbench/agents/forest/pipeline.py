"""Explicit artifact pipeline for baseline and forest strategies.

Stage graph (forest)::

    scout -> specialist branches -> tree judges -> final merger -> grade

Baseline is a single worker whose only product is the final submission.

Every stage gets a fresh sandbox and only its declared parent artifacts.  No
writable audit workspace, conversation history, or chain state crosses a stage
boundary.  Branch count is exactly ``roles x branches_per_tree`` and never
depends on gold patch mappings.
"""

from __future__ import annotations

import hashlib
import json
import shlex
import threading
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable, Iterable, Sequence

from evmbench.agents.backend.base import (
    AGENT_DIR,
    AUDIT_DIR,
    LOGS_DIR,
    SUBMISSION_DIR,
    TaskComputer,
)
from evmbench.agents.forest import prompts
from evmbench.agents.forest.artifacts import submission_remote_path
from evmbench.agents.harness.base import (
    HarnessAdapter,
    StagedArtifact,
    StageSpec,
    WorkerResult,
    WorkerStatus,
)
from evmbench.audit import Audit
from evmbench.campaign.state import CampaignConfig, CampaignStore, StageKey
from evmbench.utils import get_audits_dir, get_bash_utils_file


class PipelineContractError(RuntimeError):
    """Raised when the pipeline would violate a stage/artifact contract."""


class Cancellation:
    """Cooperative cancellation shared by the coordinator and pipeline."""

    def __init__(self) -> None:
        self._event = threading.Event()

    def cancel(self) -> None:
        self._event.set()

    @property
    def cancelled(self) -> bool:
        return self._event.is_set()

    def raise_if_cancelled(self) -> None:
        if self.cancelled:
            raise PipelineCancelled("campaign cancellation requested")


class PipelineCancelled(RuntimeError):
    pass


@dataclass(frozen=True)
class TaskDefinition:
    audit: Audit
    instructions: str
    image: str


@dataclass
class AttemptOutcome:
    status: str
    submission_path: Path | None
    results: list[WorkerResult] = field(default_factory=list)
    error: str | None = None
    selected_roles: tuple[str, ...] = ()

    @property
    def completed(self) -> bool:
        return self.status == "completed"


def artifact_path(result: WorkerResult, artifact_root: Path, kind: str) -> Path:
    for artifact in result.artifacts:
        if artifact.kind == kind:
            return artifact_root / artifact.path
    raise PipelineContractError(f"{result.worker_id} did not produce a {kind!r} artifact")


class ForestPipeline:
    """Runs one attempt of a campaign task.

    The pipeline is intentionally synchronous.  Concurrency is expressed by the
    coordinator/runner through a thread pool, while each worker keeps its own
    sandbox and event loop.
    """

    def __init__(
        self,
        *,
        config: CampaignConfig,
        store: CampaignStore,
        adapter: HarnessAdapter,
        sandbox_factory: Callable[[StageSpec], TaskComputer],
        task: TaskDefinition,
        campaign_id: str,
        cancellation: Cancellation | None = None,
    ) -> None:
        self.config = config
        self.store = store
        self.adapter = adapter
        self.sandbox_factory = sandbox_factory
        self.task = task
        self.campaign_id = campaign_id
        self.cancellation = cancellation or Cancellation()
        self.audit = task.audit
        self.mode = config.mode
        self._results: list[WorkerResult] = []

    # -- public --------------------------------------------------------------
    def run_attempt(self, attempt_id: int = 0) -> AttemptOutcome:
        try:
            if self.config.strategy == "baseline":
                return self._run_baseline(attempt_id)
            return self._run_forest(attempt_id)
        except PipelineCancelled as exc:
            return AttemptOutcome(status="cancelled", submission_path=None, results=self._results, error=str(exc))
        except Exception as exc:  # noqa: BLE001 - attempt failures are data
            return AttemptOutcome(
                status="failed",
                submission_path=None,
                results=self._results,
                error=f"{type(exc).__name__}: {exc}",
            )

    # -- baseline ------------------------------------------------------------
    def _run_baseline(self, attempt_id: int) -> AttemptOutcome:
        spec = self._baseline_spec(attempt_id)
        result = self._run_single(spec, attempt_id)
        path = self._finalize_submission(result, attempt_id, spec)
        status = "completed" if result.completed and path else "failed"
        return AttemptOutcome(
            status=status,
            submission_path=path,
            results=self._results,
            error=None if result.completed else result.failure_reason,
        )

    def _baseline_spec(self, attempt_id: int) -> StageSpec:
        return self._spec(
            attempt_id=attempt_id,
            stage="baseline",
            worker_id="baseline",
            instructions=self.task.instructions,
            task=prompts.baseline_task(self.mode),
            step_limit=self.config.budget.baseline_step_limit,
            cost_limit=self.config.budget.baseline_cost_limit,
            output_paths=(submission_remote_path(self.mode),),
            postprocess_command=self._merger_postprocess(),
        )

    # -- forest --------------------------------------------------------------
    def _run_forest(self, attempt_id: int) -> AttemptOutcome:
        scout_result = self._run_single(self._scout_spec(attempt_id), attempt_id)
        if not scout_result.completed and not self.config.continue_on_worker_error:
            return self._failed(attempt_id, "scout failed: " + str(scout_result.failure_reason))

        roles = self._select_roles(scout_result, attempt_id)
        branch_specs = self._branch_specs(attempt_id, roles)
        expected_branches = len(roles) * self.config.branches_per_tree
        if len(branch_specs) != expected_branches:  # pragma: no cover - guarded by tests
            raise PipelineContractError(
                f"branch count {len(branch_specs)} != roles({len(roles)}) x "
                f"branches_per_tree({self.config.branches_per_tree})"
            )
        branch_results = self._run_parallel(branch_specs, attempt_id)
        failed_branches = [result for result in branch_results if not result.completed]
        if failed_branches and not self.config.continue_on_worker_error:
            return self._failed(
                attempt_id,
                "branch failures: " + ", ".join(r.worker_id for r in failed_branches),
            )

        judge_specs = self._judge_specs(attempt_id, roles, branch_results)
        judge_results = self._run_parallel(judge_specs, attempt_id)
        failed_judges = [result for result in judge_results if not result.completed]
        if failed_judges and not self.config.continue_on_worker_error:
            return self._failed(
                attempt_id,
                "tree judge failures: " + ", ".join(r.worker_id for r in failed_judges),
            )

        merger_spec = self._merger_spec(attempt_id, roles, branch_results, judge_results)
        merger_result = self._run_single(merger_spec, attempt_id)
        path = self._finalize_submission(merger_result, attempt_id, merger_spec)

        degraded = bool(failed_branches or failed_judges) or not scout_result.completed
        if not merger_result.completed or path is None:
            return self._failed(attempt_id, "final merger failed: " + str(merger_result.failure_reason))
        return AttemptOutcome(
            status="degraded" if degraded else "completed",
            submission_path=path,
            results=self._results,
            selected_roles=roles,
        )

    def _scout_spec(self, attempt_id: int) -> StageSpec:
        return self._spec(
            attempt_id=attempt_id,
            stage="scout",
            worker_id="scout",
            instructions=self.task.instructions,
            task=prompts.scout_task(prompts.render_role_catalog(prompts.DEFAULT_ROLE_NAMES)),
            step_limit=self.config.budget.scout_step_limit,
            cost_limit=self.config.budget.scout_cost_limit,
        )

    def _select_roles(self, scout_result: WorkerResult, attempt_id: int) -> tuple[str, ...]:
        if self.config.tree_roles:
            roles = prompts.normalize_roles(self.config.tree_roles, max_roles=self.config.max_tree_roles)
        else:
            roles = ()
            for artifact in scout_result.artifacts:
                if artifact.path.endswith("scout.json"):
                    root = self._artifact_root(scout_result, attempt_id)
                    try:
                        _, _, roles = prompts.parse_scout_decision(
                            (root / artifact.path).read_text(encoding="utf-8"),
                            max_roles=self.config.max_tree_roles,
                        )
                    except (ValueError, json.JSONDecodeError):
                        roles = ()
                    break
        if not roles:
            roles = prompts.DEFAULT_ROLE_NAMES[: self.config.max_tree_roles]
        return roles

    def _branch_specs(self, attempt_id: int, roles: Sequence[str]) -> list[StageSpec]:
        specs: list[StageSpec] = []
        branch_count = self.config.branches_per_tree
        for role_name in roles:
            role = prompts.get_role(role_name)
            for index in range(1, branch_count + 1):
                worker_id = f"{role.name}-branch-{index:02d}"
                base = f"{prompts.FOREST_DIR}/{role.name}/branch-{index:02d}"
                specs.append(
                    self._spec(
                        attempt_id=attempt_id,
                        stage="branch",
                        worker_id=worker_id,
                        role=role.name,
                        branch_index=index,
                        instructions=self.task.instructions,
                        task=prompts.branch_task(role, index, branch_count, self.mode),
                        step_limit=self.config.budget.branch_step_limit,
                        cost_limit=self.config.budget.branch_cost_limit,
                        extra_staged=[
                            StagedArtifact(Path(_write_temp(role_file_text(role))), f"{AGENT_DIR}/FOREST_ROLE.md"),
                            StagedArtifact(Path(_write_temp(prompts.scope_text())), f"{AGENT_DIR}/AUDIT_SCOPE.md"),
                        ],
                        output_paths=(f"{base}/branch.md",),
                        postprocess_command=self._branch_postprocess(base),
                    )
                )
        return specs

    def _judge_specs(
        self,
        attempt_id: int,
        roles: Sequence[str],
        branch_results: Iterable[WorkerResult],
    ) -> list[StageSpec]:
        results = list(branch_results)
        specs: list[StageSpec] = []
        for role_name in roles:
            role = prompts.get_role(role_name)
            inputs = _stage_branch_inputs(self.mode, role.name, results, attempt_id, self)
            specs.append(
                self._spec(
                    attempt_id=attempt_id,
                    stage="tree_judge",
                    worker_id=f"{role.name}-judge",
                    role=role.name,
                    instructions=self.task.instructions,
                    task=prompts.tree_judge_task(role, self.mode),
                    step_limit=self.config.budget.judge_step_limit,
                    cost_limit=self.config.budget.judge_cost_limit,
                    extra_staged=[
                        StagedArtifact(Path(_write_temp(role_file_text(role))), f"{AGENT_DIR}/FOREST_ROLE.md"),
                        StagedArtifact(Path(_write_temp(prompts.scope_text())), f"{AGENT_DIR}/AUDIT_SCOPE.md"),
                        *inputs,
                    ],
                    output_paths=(f"{prompts.FOREST_DIR}/{role.name}/judge.md",),
                )
            )
        return specs

    def _merger_spec(
        self,
        attempt_id: int,
        roles: Sequence[str],
        branch_results: Iterable[WorkerResult],
        judge_results: Iterable[WorkerResult],
    ) -> StageSpec:
        branch_staging = [
            staged
            for role_name in roles
            for staged in _stage_branch_inputs(
                self.mode,
                role_name,
                list(branch_results),
                attempt_id,
                self,
                destination=f"{prompts.FOREST_DIR}/global/branch-inputs",
            )
        ]
        judge_staging = _stage_judge_reports(list(judge_results), attempt_id, self)
        return self._spec(
            attempt_id=attempt_id,
            stage="global_merger",
            worker_id="global-merger",
            instructions=self.task.instructions,
            task=prompts.global_merger_task(self.mode),
            step_limit=self.config.budget.global_step_limit,
            cost_limit=self.config.budget.global_cost_limit,
            extra_staged=[
                StagedArtifact(Path(_write_temp(prompts.scope_text())), f"{AGENT_DIR}/AUDIT_SCOPE.md"),
                *judge_staging,
                *branch_staging,
            ],
            output_paths=(submission_remote_path(self.mode),),
            postprocess_command=self._merger_postprocess(),
        )

    # -- execution -----------------------------------------------------------
    def _spec(
        self,
        *,
        attempt_id: int,
        stage: str,
        worker_id: str,
        instructions: str,
        task: str,
        step_limit: int,
        cost_limit: float,
        role: str | None = None,
        branch_index: int | None = None,
        extra_staged: Sequence[StagedArtifact] = (),
        output_paths: tuple[str, ...] = (),
        postprocess_command: str = "",
    ) -> StageSpec:
        key = StageKey(self.campaign_id, self.audit.id, attempt_id, stage, worker_id)
        artifact_root = self.store.stage_dir(key) / "artifacts"
        artifact_root.mkdir(parents=True, exist_ok=True)
        staged = [
            *extra_staged,
            *self._mode_setup_artifacts(role=role, worker_id=worker_id),
        ]
        return StageSpec(
            campaign_id=self.campaign_id,
            task_id=self.audit.id,
            attempt_id=attempt_id,
            stage=stage,
            worker_id=worker_id,
            mode=self.mode,
            harness=self.config.harness,
            strategy=self.config.strategy,
            model=self.config.candidate_model,
            image=self.task.image,
            instructions=instructions,
            task=task,
            step_limit=step_limit,
            cost_limit=cost_limit,
            time_limit_seconds=self.config.budget.task_time_limit,
            output_paths=output_paths,
            artifact_paths=output_paths,
            env=self._stage_env(worker_id),
            sandbox_tags={
                "campaign_id": self.campaign_id,
                "task_id": self.audit.id,
                "stage": stage,
                "worker_id": worker_id,
                "harness": self.config.harness,
                "mode": self.mode,
            },
            staged_artifacts=tuple(staged),
            role=role,
            branch_index=branch_index,
            artifact_root=str(artifact_root),
            pre_command=self._pre_command(worker_id),
            postprocess_command=postprocess_command,
        )

    def _stage_env(self, worker_id: str) -> dict[str, str]:
        env = {
            "EVMBENCH_TASK_MODE": self.mode,
            "EVMBENCH_AUDIT_ID": self.audit.id,
            "EVMBENCH_AUDIT_BASE_COMMIT": self.audit.base_commit or "",
            "EVMBENCH_WORKER_ID": worker_id,
            "EVMBENCH_CAMPAIGN_ID": self.campaign_id,
            "HOME": AGENT_DIR,
            "PAGER": "cat",
            "MANPAGER": "cat",
            "LESS": "-R",
        }
        if self.mode == "exploit":
            env["RPC_URL"] = self.audit.ploit_config.get_agent_rpc_url()
            env["EXPLOIT_WALLET_PRIVATE_KEY"] = self.audit.ploit_config.wallet_private_key
            env["EXPLOIT_WALLET_ADDRESS"] = self.audit.ploit_config.wallet_address
        return env

    def _run_single(self, spec: StageSpec, attempt_id: int) -> WorkerResult:
        self.cancellation.raise_if_cancelled()
        key = StageKey(self.campaign_id, self.audit.id, attempt_id, spec.stage, spec.worker_id)
        cached = self.store.read_result(key)
        if cached is not None and self.store.stage_complete(key):
            self.store.log_event(
                self.campaign_id,
                "stage_skipped",
                task_id=self.audit.id,
                stage=spec.stage,
                worker_id=spec.worker_id,
            )
            self._results.append(cached)
            return cached

        self.store.write_spec(key, spec.to_dict())
        self.store.log_event(
            self.campaign_id,
            "stage_started",
            task_id=self.audit.id,
            attempt_id=attempt_id,
            stage=spec.stage,
            worker_id=spec.worker_id,
            sandbox_cap=self.config.sandbox_cap,
        )
        computer: TaskComputer | None = None
        try:
            computer = self.sandbox_factory(spec)
            result = self.adapter.run(computer, spec)
        finally:
            if computer is not None:
                computer.terminate()
        self.store.write_result(key, result)
        self.store.record_stage(self.campaign_id, self.audit.id, attempt_id, result)
        self._results.append(result)
        return result

    def _run_parallel(self, specs: Sequence[StageSpec], attempt_id: int) -> list[WorkerResult]:
        if not specs:
            return []
        workers = max(1, min(self.config.worker_concurrency, len(specs)))
        results: list[WorkerResult] = []
        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = {pool.submit(self._run_single, spec, attempt_id): spec for spec in specs}
            for future in as_completed(futures):
                spec = futures[future]
                try:
                    results.append(future.result())
                except PipelineCancelled:
                    self.cancellation.cancel()
                    raise
                except Exception as exc:  # noqa: BLE001 - normalize worker crashes
                    results.append(
                        WorkerResult(
                            campaign_id=self.campaign_id,
                            task_id=self.audit.id,
                            attempt_id=attempt_id,
                            stage=spec.stage,
                            worker_id=spec.worker_id,
                            harness=self.config.harness,
                            strategy=self.config.strategy,
                            mode=self.mode,
                            model=self.config.candidate_model,
                            status=WorkerStatus.FAILED,
                            exit_code=None,
                            failure_reason=f"{type(exc).__name__}: {exc}",
                        )
                    )
        return sorted(results, key=lambda result: result.worker_id)

    # -- finalization --------------------------------------------------------
    def _finalize_submission(
        self,
        result: WorkerResult,
        attempt_id: int,
        spec: StageSpec,
    ) -> Path | None:
        if not result.completed:
            return None
        for artifact in result.artifacts:
            if artifact.kind != "submission":
                continue
            source = self._artifact_root(result, attempt_id) / artifact.path
            if not source.exists() or source.stat().st_size == 0:
                return None
            return self.store.write_submission(
                self.campaign_id,
                self.audit.id,
                attempt_id,
                self.mode,
                source.read_bytes(),
            )
        del spec
        return None

    def _artifact_root(self, result: WorkerResult, attempt_id: int) -> Path:
        key = StageKey(self.campaign_id, self.audit.id, attempt_id, result.stage, result.worker_id)
        return self.store.stage_dir(key) / "artifacts"

    def _failed(self, attempt_id: int, error: str) -> AttemptOutcome:
        self.store.log_event(self.campaign_id, "attempt_failed", task_id=self.audit.id, attempt_id=attempt_id, error=error)
        return AttemptOutcome(status="failed", submission_path=None, results=self._results, error=error)

    # -- mode preparation ----------------------------------------------------
    def _mode_setup_artifacts(self, *, role: str | None, worker_id: str) -> tuple[StagedArtifact, ...]:
        if self.mode != "exploit":
            return ()
        audit_dir = get_audits_dir() / self.audit.id / "exploit"
        deploy = audit_dir / self.audit.ploit_config.deploy_script
        utils = get_bash_utils_file()
        staged = [
            StagedArtifact(deploy, f"{AGENT_DIR}/.evmbench/deploy.sh"),
            StagedArtifact(utils, f"{AGENT_DIR}/.evmbench/utils.sh"),
        ]
        grade_script = self.audit.ploit_config.grade_script
        if grade_script:
            grade_path = audit_dir / grade_script
            if grade_path.exists():
                staged.append(StagedArtifact(grade_path, f"{AGENT_DIR}/.evmbench/grade.sh"))
        del role, worker_id
        return tuple(staged)

    def _pre_command(self, worker_id: str) -> str:
        if self.mode == "detect":
            return ""
        if not self.audit.base_commit:
            raise PipelineContractError(f"Audit {self.audit.id} has no base_commit for {self.mode} mode")
        base = shlex.quote(self.audit.base_commit)
        checkout = [
            f"cd {shlex.quote(AUDIT_DIR)}",
            f"git checkout --detach {base}",
            "git reset --hard",
            "git clean -f",
        ]
        if self.mode == "patch":
            return "set -eu && " + " && ".join(checkout)
        ploit = self.audit.ploit_config
        setup = [
            "set -eu",
            f"mkdir -p {shlex.quote(AGENT_DIR + '/.evmbench')} {shlex.quote(LOGS_DIR)}",
            f"cp {shlex.quote(AGENT_DIR + '/.evmbench/deploy.sh')} {shlex.quote(AUDIT_DIR + '/deploy.sh')}",
            f"cp {shlex.quote(AGENT_DIR + '/.evmbench/utils.sh')} {shlex.quote(AUDIT_DIR + '/utils.sh')}",
            *((
                f"cp {shlex.quote(AGENT_DIR + '/.evmbench/grade.sh')} {shlex.quote(AUDIT_DIR + '/grade.sh')}",
            ) if ploit.grade_script else ()),
            " && ".join(checkout),
            ploit.get_setup_command(),
        ]
        veto = ploit.get_veto_launch_command()
        if veto:
            setup.extend(
                [
                    f"nohup {veto} > {shlex.quote(LOGS_DIR + '/veto.log')} 2>&1 &",
                    "echo $! > " + shlex.quote(AUDIT_DIR + "/veto.pid"),
                    "sleep 1",
                ]
            )
        setup.extend(
            [
                f"if [ -f {shlex.quote(AUDIT_DIR + '/.ploit.toml')} ]; then "
                f"cp {shlex.quote(AUDIT_DIR + '/.ploit.toml')} {shlex.quote(AGENT_DIR + '/.evmbench/ploit.toml')}; fi",
                "rm -f "
                + " ".join(
                    shlex.quote(path)
                    for path in (
                        AUDIT_DIR + "/deploy.sh",
                        AUDIT_DIR + "/utils.sh",
                        AUDIT_DIR + "/grade.sh",
                        AUDIT_DIR + "/.ploit.toml",
                        AUDIT_DIR + "/.veto.toml",
                    )
                ),
            ]
        )
        del worker_id
        return "\n".join(setup)

    def _branch_postprocess(self, base: str) -> str:
        if self.mode == "detect":
            return ""
        if self.mode == "patch":
            return _capture_patch_diff(f"{base}/branch.diff", self.audit.base_commit or "HEAD")
        return _capture_exploit_txs(f"{base}/branch.txs.json", f"{base}/branch.chain.json")

    def _merger_postprocess(self) -> str:
        if self.mode == "patch":
            return _capture_patch_diff(
                submission_remote_path("patch"), self.audit.base_commit or "HEAD"
            )
        if self.mode == "exploit":
            return _capture_exploit_txs(submission_remote_path("exploit"), None)
        return ""


def _write_temp(text: str) -> str:
    path = Path(f"/tmp/evmbench-stage-inputs/{hashlib.sha256(text.encode()).hexdigest()}.txt")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return str(path)


def role_file_text(role: prompts.TreeRole) -> str:
    return prompts.role_file(role)


def _capture_patch_diff(output_path: str, base_commit: str) -> str:
    return (
        "set -eu\n"
        f"mkdir -p {shlex.quote(str(Path(output_path).parent))}\n"
        f"cd {shlex.quote(AUDIT_DIR)}\n"
        "git add -N .\n"
        f"git -c core.fileMode=false diff --binary {shlex.quote(base_commit)} "
        f"> {shlex.quote(output_path)}\n"
        f"test -s {shlex.quote(output_path)}\n"
    )


def _capture_exploit_txs(txs_path: str, chain_path: str | None) -> str:
    lines = [
        "set -eu",
        f"mkdir -p {shlex.quote(str(Path(txs_path).parent))}",
        f"cd {shlex.quote(AUDIT_DIR)}",
        f"if [ -f {shlex.quote(AGENT_DIR + '/.evmbench/ploit.toml')} ]; then "
        f"cp {shlex.quote(AGENT_DIR + '/.evmbench/ploit.toml')} {shlex.quote(AUDIT_DIR + '/.ploit.toml')}; fi",
        f"ploit txs --config {shlex.quote(AUDIT_DIR + '/.ploit.toml')} "
        f"--output {shlex.quote(SUBMISSION_DIR + '/txs.json')}",
        f"cp {shlex.quote(SUBMISSION_DIR + '/txs.json')} {shlex.quote(txs_path)}",
        f"rm -f {shlex.quote(AUDIT_DIR + '/.ploit.toml')}",
    ]
    if txs_path != SUBMISSION_DIR + "/txs.json":
        lines.append(f"rm -f {shlex.quote(SUBMISSION_DIR + '/txs.json')}")
    if chain_path:
        lines.append(
            "python3 - <<'PY'\n"
            "import hashlib, json, os\n"
            f"txs = open({txs_path!r}, 'rb').read()\n"
            "payload = {\n"
            "  'chain_isolation': 'single_sandbox',\n"
            "  'txs_sha256': hashlib.sha256(txs).hexdigest(),\n"
            "  'txs_bytes': len(txs),\n"
            "}\n"
            f"open({chain_path!r}, 'w').write(json.dumps(payload, indent=2) + '\\n')\n"
            "PY"
        )
    return "\n".join(lines)


def _stage_branch_inputs(
    mode: str,
    role: str,
    results: Sequence[WorkerResult],
    attempt_id: int,
    pipeline: ForestPipeline,
    *,
    destination: str | None = None,
) -> list[StagedArtifact]:
    staged: list[StagedArtifact] = []
    dest = destination or f"{prompts.FOREST_DIR}/{role}/branch-inputs"
    for result in results:
        if result.stage != "branch" or not result.worker_id.startswith(f"{role}-"):
            continue
        root = pipeline._artifact_root(result, attempt_id)
        for artifact in result.artifacts:
            if artifact.kind == "report":
                staged.append(StagedArtifact(root / artifact.path, f"{dest}/{result.worker_id}.md"))
            elif mode == "patch" and artifact.kind == "diff":
                staged.append(StagedArtifact(root / artifact.path, f"{dest}/{result.worker_id}.diff"))
            elif mode == "exploit" and artifact.kind == "txs":
                staged.append(StagedArtifact(root / artifact.path, f"{dest}/{result.worker_id}.txs.json"))
            elif mode == "exploit" and artifact.kind == "chain":
                staged.append(StagedArtifact(root / artifact.path, f"{dest}/{result.worker_id}.chain.json"))
    return staged


def _stage_judge_reports(
    results: Sequence[WorkerResult],
    attempt_id: int,
    pipeline: ForestPipeline,
) -> list[StagedArtifact]:
    staged: list[StagedArtifact] = []
    for result in results:
        if result.stage != "tree_judge":
            continue
        root = pipeline._artifact_root(result, attempt_id)
        for artifact in result.artifacts:
            if artifact.kind == "judge":
                staged.append(
                    StagedArtifact(
                        root / artifact.path,
                        f"{prompts.FOREST_DIR}/global/tree-reports/{result.worker_id}.md",
                    )
                )
    return staged


def plan_stage_counts(config: CampaignConfig, roles: int | None = None) -> dict[str, int]:
    """Deterministic stage/worker counts for ``plan``."""
    if roles is not None:
        role_count = roles
    elif config.tree_roles:
        role_count = len(config.tree_roles)
    else:
        role_count = max(config.max_tree_roles, 1)
    if config.strategy == "baseline":
        return {"baseline": 1, "grading": 1, "workers_total": 1}
    branches = role_count * config.branches_per_tree
    return {
        "scout": 1,
        "branches": branches,
        "tree_judges": role_count,
        "global_mergers": 1,
        "grading": 1,
        "workers_total": 1 + branches + role_count + 1,
    }


__all__ = [
    "AttemptOutcome",
    "Cancellation",
    "ForestPipeline",
    "PipelineCancelled",
    "PipelineContractError",
    "TaskDefinition",
    "artifact_path",
    "plan_stage_counts",
]
