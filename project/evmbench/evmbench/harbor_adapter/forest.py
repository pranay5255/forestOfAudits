from __future__ import annotations

import argparse
import importlib.util
import json
import shutil
import sys
from dataclasses import asdict, dataclass
from pathlib import Path
from types import ModuleType
from typing import Any, Iterable

from evmbench.audit import audit_registry
from evmbench.utils import get_default_runs_dir, get_timestamp


def _mini_agent_dir() -> Path:
    return Path(__file__).resolve().parents[1] / "agents" / "mini-swe-agent"


def _load_modal_forest_module() -> ModuleType:
    module_name = "evmbench_harbor_modal_forest"
    if module_name in sys.modules:
        return sys.modules[module_name]
    mini_agent_dir = _mini_agent_dir()
    if str(mini_agent_dir) not in sys.path:
        sys.path.insert(0, str(mini_agent_dir))
    spec = importlib.util.spec_from_file_location(module_name, mini_agent_dir / "modal_forest.py")
    if spec is None or spec.loader is None:
        raise RuntimeError("Unable to load modal_forest.py for Harbor forest adapter.")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


@dataclass(frozen=True)
class HarborForestConfig:
    audit_id: str
    mode: str = "detect"
    hint_level: str = "none"
    findings_subdir: str = ""
    image: str | None = None
    output_dir: Path | None = None
    model: str = "openai/gpt-5"
    scout_model: str | None = None
    branch_model: str | None = None
    judge_model: str | None = None
    global_model: str | None = None
    scout_step_limit: int = 16
    scout_cost_limit: float = 2.0
    branch_step_limit: int = 36
    branch_cost_limit: float = 5.0
    judge_step_limit: int = 24
    judge_cost_limit: float = 4.0
    global_step_limit: int = 36
    global_cost_limit: float = 8.0
    branches_per_tree: int = 2
    max_tree_roles: int | None = 4
    tree_roles: tuple[str, ...] = ()
    worker_concurrency: int = 4
    continue_on_worker_error: bool = False
    command_timeout: int = 240
    startup_timeout: float = 600.0
    runtime_timeout: float = 3600.0
    deployment_timeout: float = 3600.0
    install_pipx: bool = True
    model_kwargs: dict[str, Any] | None = None
    modal_sandbox_kwargs: dict[str, Any] | None = None
    cost_tracking: str = "default"
    task: str | None = None


@dataclass(frozen=True)
class HarborForestWorkerContract:
    worker_type: str
    worker_name: str
    role: str | None
    branch: str | None
    output_path: str | None
    include_submission: bool
    forbid_submission: bool


@dataclass(frozen=True)
class HarborForestResult:
    output_dir: Path
    submission_path: Path
    metadata_path: Path
    trajectory_manifest_path: Path
    artifact_paths: tuple[Path, ...]
    raw_result: dict[str, Any]


def _default_output_dir(audit_id: str, mode: str) -> Path:
    return Path(get_default_runs_dir()) / "harbor-forest" / f"{get_timestamp()}_{audit_id}_{mode}"


def build_modal_forest_config(config: HarborForestConfig) -> Any:
    modal_forest = _load_modal_forest_module()
    audit = audit_registry.get_audit(config.audit_id, findings_subdir=config.findings_subdir)
    output_dir = config.output_dir or _default_output_dir(config.audit_id, config.mode)
    task = config.task if config.task is not None else modal_forest.DEFAULT_TASK_PROMPT
    return modal_forest.ForestConfig(
        audit_id=config.audit_id,
        mode=config.mode,
        hint_level=config.hint_level,
        findings_subdir=config.findings_subdir,
        image=config.image or audit.docker_image,
        model=config.model,
        scout_model=config.scout_model or config.model,
        branch_model=config.branch_model or config.model,
        judge_model=config.judge_model or config.model,
        global_model=config.global_model or config.model,
        scout_step_limit=config.scout_step_limit,
        scout_cost_limit=config.scout_cost_limit,
        branch_step_limit=config.branch_step_limit,
        branch_cost_limit=config.branch_cost_limit,
        judge_step_limit=config.judge_step_limit,
        judge_cost_limit=config.judge_cost_limit,
        global_step_limit=config.global_step_limit,
        global_cost_limit=config.global_cost_limit,
        branches_per_tree=config.branches_per_tree,
        max_tree_roles=config.max_tree_roles,
        tree_roles=config.tree_roles,
        worker_concurrency=config.worker_concurrency,
        continue_on_worker_error=config.continue_on_worker_error,
        command_timeout=config.command_timeout,
        startup_timeout=config.startup_timeout,
        runtime_timeout=config.runtime_timeout,
        deployment_timeout=config.deployment_timeout,
        install_pipx=config.install_pipx,
        output_dir=output_dir,
        model_kwargs=dict(config.model_kwargs or {}),
        modal_sandbox_kwargs=dict(config.modal_sandbox_kwargs or {}),
        cost_tracking=config.cost_tracking,
        task=task,
    )


def preview_forest_worker_contract(
    config: HarborForestConfig,
    *,
    roles: Iterable[str],
    audit_scope_files: tuple[str, ...] = (),
) -> tuple[HarborForestWorkerContract, ...]:
    modal_forest = _load_modal_forest_module()
    forest_config = build_modal_forest_config(config)
    role_objects = tuple(modal_forest.get_tree_role(role) for role in roles)
    specs = [
        *modal_forest._worker_specs_for_branches(forest_config, role_objects, audit_scope_files),
        *modal_forest._worker_specs_for_tree_judges(forest_config, role_objects, audit_scope_files),
        modal_forest._global_judge_spec(forest_config, role_objects, audit_scope_files),
    ]
    return tuple(
        HarborForestWorkerContract(
            worker_type=spec.worker_type,
            worker_name=spec.worker_name,
            role=spec.role.name if spec.role else None,
            branch=modal_forest.branch_id(spec.branch_index) if spec.branch_index is not None else None,
            output_path=spec.output_path,
            include_submission=spec.include_submission,
            forbid_submission=spec.forbid_submission,
        )
        for spec in specs
    )


def collect_forest_artifacts(output_dir: str | Path) -> tuple[Path, ...]:
    root = Path(output_dir)
    candidates = [
        root / "submission" / "audit.md",
        root / "submission" / "agent.diff",
        root / "submission" / "txs.json",
        root / "logs" / "modal-forest-result.json",
        root / "logs" / "forest" / "trajectory-manifest.json",
    ]
    candidates.extend(sorted((root / "logs" / "forest").glob("**/*.traj.json")))
    return tuple(path for path in candidates if path.exists())


def publish_forest_artifacts(
    output_dir: str | Path,
    artifacts_dir: str | Path = "/logs/artifacts/forest",
) -> tuple[Path, ...]:
    root = Path(output_dir).resolve()
    target_root = Path(artifacts_dir)
    published: list[Path] = []
    for artifact in collect_forest_artifacts(root):
        rel = artifact.resolve().relative_to(root)
        target = target_root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(artifact, target)
        published.append(target)
    return tuple(published)


def run_harbor_forest(config: HarborForestConfig, *, publish_artifacts: bool = True) -> HarborForestResult:
    modal_forest = _load_modal_forest_module()
    forest_config = build_modal_forest_config(config)
    raw_result = modal_forest.run_modal_forest(forest_config)
    artifacts = collect_forest_artifacts(forest_config.output_dir)
    if publish_artifacts:
        publish_forest_artifacts(forest_config.output_dir)
    return HarborForestResult(
        output_dir=forest_config.output_dir,
        submission_path=modal_forest._local_final_artifact_path(forest_config),
        metadata_path=forest_config.metadata_path,
        trajectory_manifest_path=forest_config.trajectory_manifest_path,
        artifact_paths=artifacts,
        raw_result=raw_result,
    )


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the EVMBench Modal forest through the Harbor adapter.")
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--mode", choices=["detect", "patch", "exploit"], default="detect")
    parser.add_argument("--hint-level", choices=["none", "low", "med", "high", "max"], default="none")
    parser.add_argument("--findings-subdir", choices=["", "low", "medium", "high"], default="")
    parser.add_argument("--image")
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--model", default="openai/gpt-5")
    parser.add_argument("--tree-roles", default="")
    parser.add_argument("--branches-per-tree", type=int, default=2)
    parser.add_argument("--max-tree-roles", type=int, default=4)
    parser.add_argument("--worker-concurrency", type=int, default=4)
    parser.add_argument("--continue-on-worker-error", action="store_true")
    parser.add_argument("--no-publish-artifacts", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_arg_parser().parse_args(argv)
    tree_roles = tuple(role.strip() for role in args.tree_roles.split(",") if role.strip())
    result = run_harbor_forest(
        HarborForestConfig(
            audit_id=args.audit_id,
            mode=args.mode,
            hint_level=args.hint_level,
            findings_subdir=args.findings_subdir,
            image=args.image,
            output_dir=args.output_dir,
            model=args.model,
            tree_roles=tree_roles,
            branches_per_tree=args.branches_per_tree,
            max_tree_roles=args.max_tree_roles,
            worker_concurrency=args.worker_concurrency,
            continue_on_worker_error=args.continue_on_worker_error,
        ),
        publish_artifacts=not args.no_publish_artifacts,
    )
    print(json.dumps(asdict(result), indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
