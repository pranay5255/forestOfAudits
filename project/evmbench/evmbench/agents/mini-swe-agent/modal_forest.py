#!/usr/bin/env python3
"""Forest-of-auditors Modal runner for EVMBench using mini-swe-agent.

The forest runner composes multiple `DefaultAgent` workers over independent
`SwerexModalEnvironment` sandboxes:

1. scout
2. role-specialized branch workers
3. tree-local judges
4. global judge / final merger

Only the global judge output is extracted as `submission/audit.md`.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Iterable, Literal, cast

CURRENT_DIR = Path(__file__).resolve().parent
if str(CURRENT_DIR) not in sys.path:
    sys.path.insert(0, str(CURRENT_DIR))

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from judge import (
    BRANCH_INSTANCE_TEMPLATE,
    GLOBAL_JUDGE_INSTANCE_TEMPLATE,
    TREE_JUDGE_INSTANCE_TEMPLATE,
    branch_artifact_remote_paths,
    branch_diff_remote_path,
    branch_id,
    branch_inputs_remote_dir,
    branch_txs_remote_path,
    branch_report_remote_path,
    build_branch_system_template,
    build_branch_task,
    build_global_judge_system_template,
    build_global_judge_task,
    build_role_file,
    build_tree_judge_system_template,
    build_tree_judge_task,
    final_submission_path,
    global_mode_notes,
    branch_mode_notes,
    local_branch_diff_path,
    local_branch_report_path,
    local_branch_txs_path,
    local_tree_judge_path,
    tree_judge_remote_path,
    tree_reports_remote_dir,
)
from modal_baseline import (
    DEFAULT_TASK_PROMPT,
    FindingsSubdir,
    HintLevel,
    Mode,
    _decode_marked_base64,
    _load_audit_for_mode,
    _load_mini_classes,
    _model_kwargs_with_vllm_api_base,
    _parse_json_object,
    _postprocess_mode,
    _prepare_mode,
    _prepare_remote_workspace,
    _remote_workspace_env,
    _remote_write_text,
    _resolve_model_api_key,
    _run_remote,
    _safe_extract_tar,
    _stage_rendered_instructions,
)
from scout import (
    DEFAULT_TREE_ROLE_NAMES,
    SCOUT_INSTANCE_TEMPLATE,
    SCOUT_SYSTEM_TEMPLATE,
    ScoutDecision,
    TreeRole,
    get_tree_role,
    load_scout_decision,
    normalize_role_names,
    parse_role_csv,
    render_role_catalog,
)

from evmbench.audit import Audit, audit_registry
from evmbench.constants import AGENT_DIR, AUDIT_DIR, LOGS_DIR, SUBMISSION_DIR
from evmbench.utils import get_default_runs_dir, get_timestamp

ARCHIVE_BEGIN = "__EVMBENCH_MODAL_FOREST_ARCHIVE_BEGIN__"
ARCHIVE_END = "__EVMBENCH_MODAL_FOREST_ARCHIVE_END__"
AUDIT_SCOPE_PATH = f"{AGENT_DIR}/AUDIT_SCOPE.md"


def _format_seconds(value: float) -> str:
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{int(minutes)}m{seconds:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h{int(minutes):02d}m{seconds:04.1f}s"


def _log(message: str) -> None:
    timestamp = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    print(f"[modal-forest][{timestamp}] {message}", flush=True)


@dataclass(frozen=True)
class ForestConfig:
    audit_id: str
    mode: Mode
    hint_level: HintLevel
    findings_subdir: FindingsSubdir
    image: str
    model: str
    scout_model: str
    branch_model: str
    judge_model: str
    global_model: str
    scout_step_limit: int
    scout_cost_limit: float
    branch_step_limit: int
    branch_cost_limit: float
    judge_step_limit: int
    judge_cost_limit: float
    global_step_limit: int
    global_cost_limit: float
    branches_per_tree: int
    max_tree_roles: int | None
    tree_roles: tuple[str, ...]
    worker_concurrency: int
    continue_on_worker_error: bool
    command_timeout: int
    startup_timeout: float
    runtime_timeout: float
    deployment_timeout: float
    install_pipx: bool
    output_dir: Path
    model_kwargs: dict[str, Any]
    modal_sandbox_kwargs: dict[str, Any]
    cost_tracking: Literal["default", "ignore_errors"]
    task: str

    @property
    def metadata_path(self) -> Path:
        return self.output_dir / "logs" / "modal-forest-result.json"


@dataclass(frozen=True)
class WorkerResult:
    mode: Mode
    worker_type: str
    worker_name: str
    role: str | None
    branch: str | None
    trajectory_path: Path
    result: dict[str, Any] | None
    error: str | None
    started_at: float
    ended_at: float
    output_path: str | None = None
    final_artifact_path: str | None = None
    extracted_artifact_paths: tuple[str, ...] = ()
    audit_scope_files: tuple[str, ...] = ()

    @property
    def runtime_seconds(self) -> float:
        return self.ended_at - self.started_at

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["trajectory_path"] = str(self.trajectory_path)
        payload["runtime_seconds"] = self.runtime_seconds
        return payload


@dataclass(frozen=True)
class WorkerSpec:
    worker_type: str
    worker_name: str
    system_template: str
    instance_template: str
    task: str
    model_name: str
    step_limit: int
    cost_limit: float
    trajectory_path: Path
    output_path: str | None = None
    artifact_paths: tuple[str, ...] = ()
    role: TreeRole | None = None
    branch_index: int | None = None
    template_vars: dict[str, str] = field(default_factory=dict)
    staged_files: dict[str, str] = field(default_factory=dict)
    audit_scope_files: tuple[str, ...] = ()
    include_submission: bool = False
    forbid_submission: bool = True


def _worker_label(spec: WorkerSpec) -> str:
    parts = [spec.worker_type, spec.worker_name]
    if spec.role:
        parts.append(f"role={spec.role.name}")
    if spec.branch_index is not None:
        parts.append(f"branch={branch_id(spec.branch_index)}")
    return " ".join(parts)


def _audit_relative_path(remote_path: str) -> str:
    prefix = f"{AUDIT_DIR}/"
    if not remote_path.startswith(prefix):
        raise ValueError(f"Audit target path is outside {AUDIT_DIR}: {remote_path}")
    rel_path = remote_path[len(prefix):]
    if rel_path.startswith("/") or rel_path in {"", "."} or ".." in Path(rel_path).parts:
        raise ValueError(f"Unsafe audit target path: {remote_path}")
    return rel_path


def _audit_scope_files(audit: Audit) -> tuple[str, ...]:
    files: list[str] = []
    for vulnerability in audit.vulnerabilities:
        for remote_path in (vulnerability.patch_path_mapping or {}).values():
            files.append(_audit_relative_path(remote_path))
    scoped = tuple(sorted(dict.fromkeys(files)))
    if not scoped:
        raise RuntimeError(
            f"Audit {audit.id} does not define patch_path_mapping entries; "
            "modal forest cannot build a file-scoped workspace."
        )
    return scoped


def _branch_scope_files(config: ForestConfig, audit_scope_files: tuple[str, ...]) -> tuple[str, ...]:
    return tuple(
        scope_file
        for scope_file in audit_scope_files
        for _ in range(config.branches_per_tree)
    )


def _scope_file_text(scope_files: Iterable[str]) -> str:
    files = tuple(scope_files)
    lines = [
        "# Audit Scope",
        "",
        "The /home/agent/audit directory has been reduced to only these target files:",
        "",
    ]
    lines.extend(f"- {path}" for path in files)
    lines.extend(
        [
            "",
            "Do not inspect, infer, reconstruct, or search for other audit files.",
            "All findings and references must come from the scoped file(s) above and staged reports.",
            "",
        ]
    )
    return "\n".join(lines)


def _scoped_agent_instructions(instructions: str) -> str:
    return (
        instructions.rstrip()
        + "\n\nForest workspace override:\n"
        + "For this forest run, /home/agent/audit has been physically reduced to "
        + "the file(s) listed in /home/agent/AUDIT_SCOPE.md. The original README "
        + "and other repository files may be absent by design. Do not search for, "
        + "reconstruct, or infer from files outside that scope.\n"
    )


def _json_ready_config(config: ForestConfig) -> dict[str, Any]:
    payload = asdict(config)
    payload["output_dir"] = str(config.output_dir)
    payload["metadata_path"] = str(config.metadata_path)
    return payload


def _write_metadata(
    config: ForestConfig,
    *,
    scout_decision: ScoutDecision | None,
    selected_roles: Iterable[str],
    worker_results: list[WorkerResult],
    started_at: float,
    ended_at: float,
    error: str | None = None,
) -> None:
    config.metadata_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": _json_ready_config(config),
        "scout_decision": scout_decision.to_dict() if scout_decision else None,
        "selected_roles": list(selected_roles),
        "workers": [worker.to_dict() for worker in worker_results],
        "error": error,
        "started_at": started_at,
        "ended_at": ended_at,
        "runtime_seconds": ended_at - started_at,
    }
    config.metadata_path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")


def _make_env(config: ForestConfig, image: str, openai_api_key: str) -> Any:
    _, SwerexModalEnvironment, _ = _load_mini_classes()
    return SwerexModalEnvironment(
        image=image,
        cwd=AUDIT_DIR,
        timeout=config.command_timeout,
        env=_remote_workspace_env(openai_api_key),
        startup_timeout=config.startup_timeout,
        runtime_timeout=config.runtime_timeout,
        deployment_timeout=config.deployment_timeout,
        install_pipx=config.install_pipx,
        modal_sandbox_kwargs=config.modal_sandbox_kwargs,
    )


def _restrict_remote_audit_workspace(env: Any, scope_files: tuple[str, ...], label: str) -> None:
    if not scope_files:
        raise RuntimeError(f"No audit scope files configured for {label}.")
    rendered_scope = json.dumps(list(scope_files))
    command = f"""python3 - <<'PY'
import json
import shutil
from pathlib import Path

root = Path({AUDIT_DIR!r})
root_resolved = root.resolve()
scope_files = json.loads({rendered_scope!r})
tmp = Path("/tmp/evmbench-audit-scope")
if tmp.exists():
    shutil.rmtree(tmp)
tmp.mkdir(parents=True)

for rel in scope_files:
    rel_path = Path(rel)
    if rel_path.is_absolute() or ".." in rel_path.parts or str(rel_path) in ("", "."):
        raise RuntimeError(f"Unsafe audit scope path: {{rel}}")
    source = (root / rel_path).resolve()
    if source != root_resolved and root_resolved not in source.parents:
        raise RuntimeError(f"Audit scope path escapes audit dir: {{rel}}")
    if not source.is_file():
        raise RuntimeError(f"Audit scope file does not exist: {{rel}}")
    dest = tmp / rel_path
    dest.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, dest)

for child in root.iterdir():
    if child.is_dir() and not child.is_symlink():
        shutil.rmtree(child)
    else:
        child.unlink()

for child in tmp.iterdir():
    shutil.move(str(child), str(root / child.name))
shutil.rmtree(tmp)
PY"""
    _run_remote(env, command, f"restrict audit workspace for {label}", timeout=120)


def _extract_worker_outputs(
    env: Any,
    output_dir: Path,
    worker_name: str,
    *,
    include_submission: bool = False,
) -> None:
    entries = {
        f"{AGENT_DIR}/forest": "forest",
        LOGS_DIR: f"logs/forest/remote/{worker_name}",
    }
    if include_submission:
        entries[SUBMISSION_DIR] = "submission"

    _log(f"extracting worker outputs worker={worker_name} include_submission={include_submission}")
    rendered_entries = ",\n    ".join(f"{source!r}: {arcname!r}" for source, arcname in entries.items())
    command = f"""python3 - <<'PY'
import base64
import io
from pathlib import Path
import tarfile

entries = {{
    {rendered_entries}
}}
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
    for source, arcname in entries.items():
        path = Path(source)
        if path.exists():
            archive.add(path, arcname=arcname)

print({ARCHIVE_BEGIN!r})
print(base64.b64encode(buffer.getvalue()).decode("ascii"))
print({ARCHIVE_END!r})
PY"""
    output = _run_remote(env, command, f"extract forest outputs for {worker_name}")
    archive_bytes = _decode_marked_base64(str(output.get("output", "")), ARCHIVE_BEGIN, ARCHIVE_END)
    _safe_extract_tar(archive_bytes, output_dir)
    _log(f"extracted worker outputs worker={worker_name} output_dir={output_dir}")


def _verify_worker_contract(env: Any, spec: WorkerSpec) -> None:
    if spec.output_path:
        _run_remote(
            env,
            f"test -s {shlex.quote(spec.output_path)}",
            f"verify {spec.worker_name} output exists",
            timeout=30,
        )
    if spec.forbid_submission:
        command = f"test ! -e {shlex.quote(FINAL_SUBMISSION_PATH)}"
        _run_remote(env, command, f"verify {spec.worker_name} did not write final submission", timeout=30)


def _run_worker(
    config: ForestConfig,
    audit: Audit,
    instructions: str,
    spec: WorkerSpec,
    *,
    openai_api_key: str,
) -> WorkerResult:
    DefaultAgent, _, LitellmModel = _load_mini_classes()
    started_at = time.time()
    result: dict[str, Any] | None = None
    error: str | None = None
    label = _worker_label(spec)
    _log(
        "worker start "
        f"{label} model={spec.model_name} step_limit={spec.step_limit} "
        f"cost_limit={spec.cost_limit} trajectory={spec.trajectory_path} output={spec.output_path or '-'}"
    )
    env = _make_env(config, config.image, openai_api_key)
    try:
        _log(f"worker prepare remote workspace {label}")
        _prepare_remote_workspace(env)
        _stage_rendered_instructions(env, _scoped_agent_instructions(instructions))
        for remote_path, text in spec.staged_files.items():
            _log(f"worker stage file {label} remote_path={remote_path} bytes={len(text.encode('utf-8'))}")
            _remote_write_text(env, remote_path, text, f"stage {remote_path}")

        ploit_toml = _prepare_mode(env, audit, config.mode)
        _log(f"worker restrict audit scope {label} files={','.join(spec.audit_scope_files)}")
        _restrict_remote_audit_workspace(env, spec.audit_scope_files, label)
        _log(f"worker run agent {label}")
        model = LitellmModel(
            model_name=spec.model_name,
            model_kwargs=config.model_kwargs,
            cost_tracking=config.cost_tracking,
        )
        agent = DefaultAgent(
            model,
            env,
            system_template=spec.system_template,
            instance_template=spec.instance_template,
            step_limit=spec.step_limit,
            cost_limit=spec.cost_limit,
            output_path=spec.trajectory_path,
        )
        result = agent.run(spec.task, **spec.template_vars)
        _log(f"worker verify outputs {label}")
        _verify_worker_contract(env, spec)
        if spec.include_submission:
            _log(f"worker postprocess final submission {label}")
            _postprocess_mode(env, audit, config.mode, ploit_toml)
        _extract_worker_outputs(env, config.output_dir, spec.worker_name, include_submission=spec.include_submission)
    except Exception as exc:
        error = str(exc)
        _log(f"worker error {label}: {error}")
        try:
            _extract_worker_outputs(env, config.output_dir, spec.worker_name, include_submission=spec.include_submission)
        except Exception:
            pass
    finally:
        ended_at = time.time()
        env.stop()
        status = "error" if error else "ok"
        _log(f"worker finish {label} status={status} runtime={_format_seconds(ended_at - started_at)}")

    return WorkerResult(
        worker_type=spec.worker_type,
        worker_name=spec.worker_name,
        role=spec.role.name if spec.role else None,
        branch=branch_id(spec.branch_index) if spec.branch_index is not None else None,
        trajectory_path=spec.trajectory_path,
        result=result,
        error=error,
        started_at=started_at,
        ended_at=ended_at,
        output_path=spec.output_path,
        audit_scope_files=spec.audit_scope_files,
    )


def _read_local_text(path: Path, *, missing_text: str) -> str:
    if path.exists():
        return path.read_text(encoding="utf-8")
    return missing_text


def _stage_branch_reports(
    config: ForestConfig,
    role: TreeRole,
    audit_scope_files: tuple[str, ...],
) -> dict[str, str]:
    staged: dict[str, str] = {}
    for index in range(1, len(_branch_scope_files(config, audit_scope_files)) + 1):
        local_path = local_branch_report_path(config.output_dir, role, index)
        report = _read_local_text(
            local_path,
            missing_text=(
                f"# Missing Branch Report\n\n"
                f"No report was extracted for {role.name} {branch_id(index)}."
            ),
        )
        staged[f"{branch_inputs_remote_dir(role)}/{branch_id(index)}.md"] = report
    return staged


def _stage_tree_reports(output_dir: Path, roles: Iterable[TreeRole]) -> dict[str, str]:
    staged: dict[str, str] = {}
    for role in roles:
        local_path = local_tree_judge_path(output_dir, role)
        report = _read_local_text(
            local_path,
            missing_text=f"# Missing Tree Report\n\nNo tree-local report was extracted for {role.name}.",
        )
        staged[f"{tree_reports_remote_dir()}/{role.name}.md"] = report
    return staged


def _worker_specs_for_branches(
    config: ForestConfig,
    roles: Iterable[TreeRole],
    audit_scope_files: tuple[str, ...],
) -> list[WorkerSpec]:
    specs: list[WorkerSpec] = []
    branch_scopes = _branch_scope_files(config, audit_scope_files)
    branch_count = len(branch_scopes)
    for role in roles:
        for index, scope_file in enumerate(branch_scopes, start=1):
            branch = branch_id(index)
            output_path = branch_report_remote_path(role, index)
            specs.append(
                WorkerSpec(
                    worker_type="branch",
                    worker_name=f"{role.name}-{branch}",
                    role=role,
                    branch_index=index,
                    system_template=build_branch_system_template(role, index, branch_count),
                    instance_template=BRANCH_INSTANCE_TEMPLATE,
                    task=build_branch_task(role, index, branch_count, config.task),
                    model_name=config.branch_model,
                    step_limit=config.branch_step_limit,
                    cost_limit=config.branch_cost_limit,
                    trajectory_path=config.output_dir / "logs" / "forest" / role.name / f"{branch}.traj.json",
                    output_path=output_path,
                    staged_files={
                        f"{AGENT_DIR}/FOREST_ROLE.md": build_role_file(role),
                        AUDIT_SCOPE_PATH: _scope_file_text((scope_file,)),
                    },
                    audit_scope_files=(scope_file,),
                    template_vars={
                        "branch_output_path": output_path,
                    },
                )
            )
    return specs


def _worker_specs_for_tree_judges(
    config: ForestConfig,
    roles: Iterable[TreeRole],
    audit_scope_files: tuple[str, ...],
) -> list[WorkerSpec]:
    specs: list[WorkerSpec] = []
    for role in roles:
        output_path = tree_judge_remote_path(role)
        specs.append(
            WorkerSpec(
                worker_type="tree_judge",
                worker_name=f"{role.name}-judge",
                role=role,
                system_template=build_tree_judge_system_template(role),
                instance_template=TREE_JUDGE_INSTANCE_TEMPLATE,
                task=build_tree_judge_task(role, config.task),
                model_name=config.judge_model,
                step_limit=config.judge_step_limit,
                cost_limit=config.judge_cost_limit,
                trajectory_path=config.output_dir / "logs" / "forest" / role.name / "judge.traj.json",
                output_path=output_path,
                staged_files={
                    f"{AGENT_DIR}/FOREST_ROLE.md": build_role_file(role),
                    AUDIT_SCOPE_PATH: _scope_file_text(audit_scope_files),
                    **_stage_branch_reports(config, role, audit_scope_files),
                },
                audit_scope_files=audit_scope_files,
                template_vars={
                    "branch_inputs_dir": branch_inputs_remote_dir(role),
                    "judge_output_path": output_path,
                },
            )
        )
    return specs


def _scout_spec(config: ForestConfig, audit_scope_files: tuple[str, ...]) -> WorkerSpec:
    return WorkerSpec(
        worker_type="scout",
        worker_name="scout",
        system_template=SCOUT_SYSTEM_TEMPLATE,
        instance_template=SCOUT_INSTANCE_TEMPLATE,
        task=config.task,
        model_name=config.scout_model,
        step_limit=config.scout_step_limit,
        cost_limit=config.scout_cost_limit,
        trajectory_path=config.output_dir / "logs" / "forest" / "scout.traj.json",
        output_path=f"{AGENT_DIR}/forest/scout/scout.md",
        staged_files={AUDIT_SCOPE_PATH: _scope_file_text(audit_scope_files)},
        audit_scope_files=audit_scope_files,
        template_vars={
            "role_catalog": render_role_catalog(DEFAULT_TREE_ROLE_NAMES),
        },
    )


def _global_judge_spec(
    config: ForestConfig,
    roles: Iterable[TreeRole],
    audit_scope_files: tuple[str, ...],
) -> WorkerSpec:
    return WorkerSpec(
        worker_type="global_judge",
        worker_name="global-judge",
        system_template=build_global_judge_system_template(),
        instance_template=GLOBAL_JUDGE_INSTANCE_TEMPLATE,
        task=build_global_judge_task(config.task),
        model_name=config.global_model,
        step_limit=config.global_step_limit,
        cost_limit=config.global_cost_limit,
        trajectory_path=config.output_dir / "logs" / "forest" / "global-judge.traj.json",
        output_path=FINAL_SUBMISSION_PATH,
        staged_files={
            AUDIT_SCOPE_PATH: _scope_file_text(audit_scope_files),
            **_stage_tree_reports(config.output_dir, roles),
        },
        audit_scope_files=audit_scope_files,
        template_vars={
            "tree_reports_dir": tree_reports_remote_dir(),
        },
        include_submission=True,
        forbid_submission=False,
    )


def _run_specs_parallel(
    config: ForestConfig,
    audit: Audit,
    instructions: str,
    specs: list[WorkerSpec],
    *,
    openai_api_key: str,
) -> list[WorkerResult]:
    if not specs:
        return []
    max_workers = max(1, min(config.worker_concurrency, len(specs)))
    worker_type = specs[0].worker_type
    _log(f"worker batch start type={worker_type} count={len(specs)} concurrency={max_workers}")
    results: list[WorkerResult] = []
    with ThreadPoolExecutor(max_workers=max_workers) as executor:
        future_to_spec = {
            executor.submit(
                _run_worker,
                config,
                audit,
                instructions,
                spec,
                openai_api_key=openai_api_key,
            ): spec
            for spec in specs
        }
        for future in as_completed(future_to_spec):
            spec = future_to_spec[future]
            try:
                result = future.result()
                results.append(result)
                status = "error" if result.error else "ok"
                _log(
                    f"worker batch collected name={result.worker_name} type={result.worker_type} "
                    f"status={status} runtime={_format_seconds(result.runtime_seconds)}"
                )
            except Exception as exc:
                now = time.time()
                _log(f"worker batch captured error name={spec.worker_name} error={exc}")
                results.append(
                    WorkerResult(
                        worker_type=spec.worker_type,
                        worker_name=spec.worker_name,
                        role=spec.role.name if spec.role else None,
                        branch=branch_id(spec.branch_index) if spec.branch_index else None,
                        trajectory_path=spec.trajectory_path,
                        result=None,
                        error=str(exc),
                        started_at=now,
                        ended_at=now,
                        output_path=spec.output_path,
                    )
                )
    sorted_results = sorted(results, key=lambda item: item.worker_name)
    n_errors = sum(1 for result in sorted_results if result.error)
    _log(f"worker batch finish type={worker_type} count={len(sorted_results)} errors={n_errors}")
    return sorted_results


def _check_stage_errors(
    results: list[WorkerResult],
    stage_name: str,
    *,
    continue_on_error: bool,
) -> None:
    """Raise if any worker in *results* has an error and we should not continue."""
    errors = [r for r in results if r.error]
    if errors and not continue_on_error:
        names = ", ".join(r.worker_name for r in errors)
        raise RuntimeError(
            f"Stage '{stage_name}' had {len(errors)} worker error(s) [{names}]. "
            f"Partial traces have been saved — check modal-forest-result.json for details."
        )


def _select_roles(config: ForestConfig) -> tuple[ScoutDecision, tuple[TreeRole, ...]]:
    if config.tree_roles:
        selected_names = normalize_role_names(config.tree_roles, max_roles=config.max_tree_roles)
        scout_decision = ScoutDecision(
            summary="Tree roles were supplied on the command line.",
            recommended_roles=selected_names,
            role_rationale={role: "explicit role" for role in selected_names},
        )
    else:
        scout_decision = load_scout_decision(config.output_dir, max_roles=config.max_tree_roles)
        selected_names = scout_decision.recommended_roles
    roles = tuple(get_tree_role(name) for name in selected_names)
    if not roles:
        raise RuntimeError("No forest roles were selected.")
    _log(
        "selected forest roles: "
        + ", ".join(role.name for role in roles)
        + f" (summary={scout_decision.summary or '-'})"
    )
    return scout_decision, roles


def run_modal_forest(config: ForestConfig) -> dict[str, Any]:
    openai_api_key = _resolve_model_api_key()

    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "logs" / "forest").mkdir(parents=True, exist_ok=True)
    _log(
        f"run start audit={config.audit_id} mode={config.mode} image={config.image} "
        f"output_dir={config.output_dir}"
    )
    _log(
        "budgets "
        f"scout={config.scout_step_limit}/{config.scout_cost_limit} "
        f"branch={config.branch_step_limit}/{config.branch_cost_limit} "
        f"tree_judge={config.judge_step_limit}/{config.judge_cost_limit} "
        f"global={config.global_step_limit}/{config.global_cost_limit} "
        f"branches_per_tree={config.branches_per_tree} max_tree_roles={config.max_tree_roles} "
        f"worker_concurrency={config.worker_concurrency}"
    )

    audit, instructions = _load_audit_for_mode(cast(Any, config))
    audit_scope_files = _audit_scope_files(audit)
    _log(
        "audit scope files: "
        + ", ".join(audit_scope_files)
        + f" (branch_workers_per_tree={len(_branch_scope_files(config, audit_scope_files))})"
    )
    started_at = time.time()
    worker_results: list[WorkerResult] = []
    scout_decision: ScoutDecision | None = None
    selected_roles: tuple[TreeRole, ...] = ()
    error: str | None = None

    try:
        # --- scout stage ---
        _log("stage start scout")
        scout_result = _run_worker(
            config,
            audit,
            instructions,
            _scout_spec(config, audit_scope_files),
            openai_api_key=openai_api_key,
        )
        worker_results.append(scout_result)
        _check_stage_errors(
            [scout_result], "scout",
            continue_on_error=config.continue_on_worker_error,
        )

        scout_decision, selected_roles = _select_roles(config)

        # --- branch workers stage ---
        _log(
            "stage start branch workers "
            f"roles={','.join(role.name for role in selected_roles)} "
            f"branches_per_tree={config.branches_per_tree}"
        )
        branch_results = _run_specs_parallel(
            config,
            audit,
            instructions,
            _worker_specs_for_branches(config, selected_roles, audit_scope_files),
            openai_api_key=openai_api_key,
        )
        worker_results.extend(branch_results)
        _check_stage_errors(
            branch_results, "branch",
            continue_on_error=config.continue_on_worker_error,
        )

        # --- tree judges stage ---
        _log(f"stage start tree judges roles={','.join(role.name for role in selected_roles)}")
        tree_judge_results = _run_specs_parallel(
            config,
            audit,
            instructions,
            _worker_specs_for_tree_judges(config, selected_roles, audit_scope_files),
            openai_api_key=openai_api_key,
        )
        worker_results.extend(tree_judge_results)
        _check_stage_errors(
            tree_judge_results, "tree_judge",
            continue_on_error=config.continue_on_worker_error,
        )

        # --- global judge stage ---
        _log("stage start global judge")
        global_result = _run_worker(
            config,
            audit,
            instructions,
            _global_judge_spec(config, selected_roles, audit_scope_files),
            openai_api_key=openai_api_key,
        )
        worker_results.append(global_result)
        _check_stage_errors(
            [global_result], "global_judge",
            continue_on_error=config.continue_on_worker_error,
        )

        final_report = config.output_dir / "submission" / "audit.md"
        if config.mode == "detect" and not final_report.exists():
            raise RuntimeError(f"Forest final submission was not extracted: {final_report}")
        _log(f"final submission ready path={final_report}")

        return {
            "output_dir": str(config.output_dir),
            "selected_roles": [role.name for role in selected_roles],
            "audit_scope_files": list(audit_scope_files),
            "workers": [worker.to_dict() for worker in worker_results],
        }
    except Exception as exc:
        error = str(exc)
        _log(f"run error audit={config.audit_id}: {error}")
        raise
    finally:
        ended_at = time.time()
        _write_metadata(
            config,
            scout_decision=scout_decision,
            selected_roles=[role.name for role in selected_roles],
            worker_results=worker_results,
            started_at=started_at,
            ended_at=ended_at,
            error=error,
        )
        _log(
            f"metadata written path={config.metadata_path} "
            f"runtime={_format_seconds(ended_at - started_at)} error={error or '-'}"
        )


def _default_output_dir(audit_id: str, mode: str) -> Path:
    return Path(get_default_runs_dir()) / "modal-forest" / f"{get_timestamp()}_{audit_id}_{mode}"


def _positive_int(raw: str) -> int:
    value = int(raw)
    if value < 1:
        raise argparse.ArgumentTypeError("value must be >= 1")
    return value


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-id", required=True, help="EVMBench audit id, e.g. 2024-01-canto.")
    parser.add_argument("--mode", choices=["detect"], default="detect")
    parser.add_argument("--hint-level", choices=["none", "low", "med", "high", "max"], default="none")
    parser.add_argument("--findings-subdir", choices=["", "low", "medium", "high"], default="")
    parser.add_argument("--image", help="Audit image to run. Defaults to the audit config docker_image.")
    parser.add_argument("--image-version", default="", help="Optional suffix appended to the default audit image.")
    parser.add_argument("--model", default=os.getenv("MODEL", "openai/gpt-5"))
    parser.add_argument("--scout-model", default=os.getenv("SCOUT_MODEL"))
    parser.add_argument("--branch-model", default=os.getenv("BRANCH_MODEL"))
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL"))
    parser.add_argument("--global-model", default=os.getenv("GLOBAL_MODEL"))
    parser.add_argument("--scout-step-limit", type=_positive_int, default=int(os.getenv("SCOUT_STEP_LIMIT", "16")))
    parser.add_argument("--scout-cost-limit", type=float, default=float(os.getenv("SCOUT_COST_LIMIT", "2.0")))
    parser.add_argument("--branch-step-limit", type=_positive_int, default=int(os.getenv("BRANCH_STEP_LIMIT", "36")))
    parser.add_argument("--branch-cost-limit", type=float, default=float(os.getenv("BRANCH_COST_LIMIT", "5.0")))
    parser.add_argument("--judge-step-limit", type=_positive_int, default=int(os.getenv("JUDGE_STEP_LIMIT", "24")))
    parser.add_argument("--judge-cost-limit", type=float, default=float(os.getenv("JUDGE_COST_LIMIT", "4.0")))
    parser.add_argument("--global-step-limit", type=_positive_int, default=int(os.getenv("GLOBAL_STEP_LIMIT", "36")))
    parser.add_argument("--global-cost-limit", type=float, default=float(os.getenv("GLOBAL_COST_LIMIT", "8.0")))
    parser.add_argument("--branches-per-tree", type=_positive_int, default=int(os.getenv("BRANCHES_PER_TREE", "2")))
    parser.add_argument(
        "--max-tree-roles",
        type=_positive_int,
        default=int(os.getenv("MAX_TREE_ROLES", "4")),
        help="Maximum scout-selected roles to activate. Use 8 to run the full default tree set.",
    )
    parser.add_argument(
        "--tree-roles",
        default=os.getenv("TREE_ROLES", ""),
        help="Comma-separated explicit roles. Defaults to scout-selected roles.",
    )
    parser.add_argument(
        "--worker-concurrency",
        type=_positive_int,
        default=int(os.getenv("FOREST_WORKER_CONCURRENCY", "4")),
    )
    parser.add_argument("--continue-on-worker-error", action="store_true")
    parser.add_argument("--command-timeout", type=int, default=240)
    parser.add_argument("--startup-timeout", type=float, default=600.0)
    parser.add_argument("--runtime-timeout", type=float, default=3600.0)
    parser.add_argument("--deployment-timeout", type=float, default=3600.0)
    parser.add_argument("--install-pipx", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--model-kwargs-json",
        type=lambda raw: _parse_json_object(raw, "--model-kwargs-json"),
        default={},
        help="JSON object passed as LiteLLM model_kwargs.",
    )
    parser.add_argument(
        "--modal-sandbox-kwargs-json",
        type=lambda raw: _parse_json_object(raw, "--modal-sandbox-kwargs-json"),
        default={},
        help="JSON object passed to SwerexModalEnvironment modal_sandbox_kwargs.",
    )
    parser.add_argument(
        "--cost-tracking",
        choices=["default", "ignore_errors"],
        default=os.getenv("MSWEA_COST_TRACKING", "default"),
    )
    parser.add_argument("--task", default=DEFAULT_TASK_PROMPT)
    return parser


def config_from_args(args: argparse.Namespace) -> ForestConfig:
    if args.mode != "detect":
        raise ValueError("Modal forest currently supports detect mode only.")
    audit = audit_registry.get_audit(args.audit_id, findings_subdir=args.findings_subdir)
    image = args.image or audit.docker_image
    if args.image_version and not args.image:
        image = f"{image}-{args.image_version}"
    output_dir = args.output_dir or _default_output_dir(args.audit_id, args.mode)
    explicit_roles = parse_role_csv(args.tree_roles)
    if args.tree_roles.strip() and not explicit_roles:
        raise ValueError(f"--tree-roles did not contain any known forest roles: {args.tree_roles!r}")
    return ForestConfig(
        audit_id=args.audit_id,
        mode=args.mode,
        hint_level=args.hint_level,
        findings_subdir=args.findings_subdir,
        image=image,
        model=args.model,
        scout_model=args.scout_model or args.model,
        branch_model=args.branch_model or args.model,
        judge_model=args.judge_model or args.model,
        global_model=args.global_model or args.model,
        scout_step_limit=args.scout_step_limit,
        scout_cost_limit=args.scout_cost_limit,
        branch_step_limit=args.branch_step_limit,
        branch_cost_limit=args.branch_cost_limit,
        judge_step_limit=args.judge_step_limit,
        judge_cost_limit=args.judge_cost_limit,
        global_step_limit=args.global_step_limit,
        global_cost_limit=args.global_cost_limit,
        branches_per_tree=args.branches_per_tree,
        max_tree_roles=args.max_tree_roles,
        tree_roles=explicit_roles,
        worker_concurrency=args.worker_concurrency,
        continue_on_worker_error=args.continue_on_worker_error,
        command_timeout=args.command_timeout,
        startup_timeout=args.startup_timeout,
        runtime_timeout=args.runtime_timeout,
        deployment_timeout=args.deployment_timeout,
        install_pipx=args.install_pipx,
        output_dir=output_dir,
        model_kwargs=_model_kwargs_with_vllm_api_base(args.model_kwargs_json),
        modal_sandbox_kwargs=args.modal_sandbox_kwargs_json,
        cost_tracking=args.cost_tracking,
        task=args.task,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = config_from_args(args)
        result = run_modal_forest(config)
    except Exception as exc:
        print(f"Modal forest failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
