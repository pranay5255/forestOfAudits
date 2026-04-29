#!/usr/bin/env python3
"""Extract validated Forest-of-Thought trace rows from Phase 6 artifacts."""

from __future__ import annotations

import argparse
import ast
import json
import re
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from evmbench.experiments.schema_version import EXTRACTOR_VERSION, SCHEMA_VERSION
from evmbench.experiments.trace_schema import SchemaValidationError, validate_row

TRACE_JSONL = "forest_trace_evm_scaling_v0.jsonl"
BRANCH_JSONL = "forest_branch_summaries_v0.jsonl"
ERRORS_JSON = "extract-errors.json"


class ExtractError(RuntimeError):
    """Raised for source artifact or row extraction failures."""


@dataclass(frozen=True)
class RunContext:
    input_root: Path
    run_dir: Path
    modal_root: Path
    run_group_id: str
    audit_id: str
    mode: str
    split: str
    phase6_row: dict[str, Any] | None
    modal_forest: dict[str, Any] | None
    trajectory_manifest: dict[str, Any] | None
    manifest_path: Path | None


@dataclass(frozen=True)
class TrajectorySource:
    path: Path | None
    worker: dict[str, Any]
    branch_id: str
    worker_id: str
    trajectory: dict[str, Any] | None


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise ExtractError(f"{path}: invalid JSON: {exc}") from exc


def _read_json_object(path: Path) -> dict[str, Any] | None:
    value = _read_json(path)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ExtractError(f"{path}: expected JSON object")
    return value


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return "UNSET"
    return result.stdout.strip() or "UNSET"


def _load_split_map(path: Path | None) -> dict[str, str]:
    if path is None:
        return {}
    payload = _read_json_object(path)
    if not payload:
        return {}
    split_map: dict[str, str] = {}
    splits = payload.get("splits")
    if not isinstance(splits, dict):
        return split_map
    for split_name, split_payload in splits.items():
        if split_name not in {"train", "eval", "test", "holdout", "unspecified"}:
            continue
        if not isinstance(split_payload, dict):
            continue
        audit_ids = split_payload.get("audit_ids")
        if isinstance(audit_ids, list):
            for audit_id in audit_ids:
                if isinstance(audit_id, str):
                    split_map[audit_id] = str(split_name)
    return split_map


def _parse_audit_from_run_dir(run_dir: Path) -> str:
    return run_dir.name.split("_", 1)[0] or "unknown"


def _modal_root_for_run(run_dir: Path) -> Path:
    if (run_dir / "modal" / "logs").exists():
        return run_dir / "modal"
    return run_dir


def _read_modal_forest(modal_root: Path) -> dict[str, Any] | None:
    return _read_json_object(modal_root / "logs" / "modal-forest-result.json")


def _read_manifest(modal_root: Path) -> tuple[Path | None, dict[str, Any] | None]:
    path = modal_root / "logs" / "forest" / "trajectory-manifest.json"
    return (path, _read_json_object(path)) if path.exists() else (None, None)


def _has_forest_artifacts(run_dir: Path) -> bool:
    modal_root = _modal_root_for_run(run_dir)
    return (
        (modal_root / "logs" / "modal-forest-result.json").exists()
        or (modal_root / "logs" / "forest").exists()
        or bool(list((modal_root / "logs").glob("**/*.traj.json")))
    )


def _phase6_result_rows(input_root: Path) -> list[dict[str, Any]]:
    payload = _read_json_object(input_root / "phase6-results.json")
    if not payload:
        return []
    rows = payload.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _matrix_runs(input_root: Path) -> list[dict[str, Any]]:
    payload = _read_json_object(input_root / "phase6-run-matrix.json")
    if not payload:
        return []
    runs = payload.get("runs")
    return [run for run in runs if isinstance(run, dict)] if isinstance(runs, list) else []


def _find_run_dir_from_matrix(item: Mapping[str, Any]) -> Path | None:
    runs_dir = Path(str(item.get("runs_dir", "")))
    audit_id = str(item.get("audit_id", ""))
    if not runs_dir.exists() or not audit_id:
        return None
    candidates = [
        path.parent
        for path in runs_dir.rglob("run.log")
        if path.parent.name.split("_", 1)[0] == audit_id
    ]
    return max(candidates, key=lambda path: path.stat().st_mtime) if candidates else None


def _context_from_run_dir(
    input_root: Path,
    run_dir: Path,
    *,
    split_map: dict[str, str],
    phase6_row: dict[str, Any] | None = None,
    matrix_item: dict[str, Any] | None = None,
) -> RunContext | None:
    if not _has_forest_artifacts(run_dir):
        return None
    modal_root = _modal_root_for_run(run_dir)
    modal_forest = _read_modal_forest(modal_root)
    manifest_path, manifest = _read_manifest(modal_root)
    config = modal_forest.get("config") if isinstance(modal_forest, dict) else None
    audit_id = (
        str((phase6_row or {}).get("audit_id") or "")
        or str((matrix_item or {}).get("audit_id") or "")
        or str(config.get("audit_id") if isinstance(config, dict) else "")
        or str((manifest or {}).get("audit_id") or "")
        or _parse_audit_from_run_dir(run_dir)
    )
    mode = (
        str((phase6_row or {}).get("mode") or "")
        or str((matrix_item or {}).get("mode") or "")
        or str((modal_forest or {}).get("mode") or "")
        or str((manifest or {}).get("mode") or "")
        or "detect"
    )
    run_group_id = input_root.name if (input_root / "phase6-results.json").exists() else run_dir.name
    return RunContext(
        input_root=input_root,
        run_dir=run_dir,
        modal_root=modal_root,
        run_group_id=run_group_id,
        audit_id=audit_id,
        mode=mode,
        split=split_map.get(audit_id, "unspecified"),
        phase6_row=phase6_row,
        modal_forest=modal_forest,
        trajectory_manifest=manifest,
        manifest_path=manifest_path,
    )


def discover_runs(input_root: Path, split_map: dict[str, str]) -> list[RunContext]:
    input_root = input_root.resolve()
    contexts: list[RunContext] = []

    phase6_rows = _phase6_result_rows(input_root)
    if phase6_rows:
        for row in phase6_rows:
            raw_run_dir = row.get("run_dir")
            if not raw_run_dir:
                continue
            run_dir = Path(str(raw_run_dir))
            if not run_dir.is_absolute():
                run_dir = input_root / run_dir
            context = _context_from_run_dir(input_root, run_dir, split_map=split_map, phase6_row=row)
            if context:
                contexts.append(context)
        return contexts

    matrix_runs = _matrix_runs(input_root)
    if matrix_runs:
        for item in matrix_runs:
            run_dir = _find_run_dir_from_matrix(item)
            if not run_dir:
                continue
            context = _context_from_run_dir(input_root, run_dir, split_map=split_map, matrix_item=item)
            if context:
                contexts.append(context)
        return contexts

    single = _context_from_run_dir(input_root, input_root, split_map=split_map)
    if single:
        return [single]

    for run_log in sorted(input_root.rglob("run.log")):
        context = _context_from_run_dir(input_root, run_log.parent, split_map=split_map)
        if context:
            contexts.append(context)
    return contexts


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"OPENAI_API_KEY|VLLM_API_KEY|GITHUB_TOKEN|GH_TOKEN|MODAL_TOKEN_ID|"
    r"MODAL_TOKEN_SECRET|NPM_TOKEN|DOCKER_PASSWORD|PASSWORD|SECRET|TOKEN"
    r")\b\s*[:=]\s*([^\s'\";]+)"
)
_TOKEN_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_\-]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{12,}"
    r")\b"
)
_CREDENTIAL_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
_HOST_PATH_RE = re.compile(r"(?<!\w)(/home/(?!agent\b)[A-Za-z0-9._-]+/[^\s'\"`<>)]*)")
_USERS_PATH_RE = re.compile(r"(?<!\w)(/Users/[A-Za-z0-9._-]+/[^\s'\"`<>)]*)")


def redact_string(value: str) -> str:
    value = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", value)
    value = _TOKEN_VALUE_RE.sub("<REDACTED_TOKEN>", value)
    value = _CREDENTIAL_URL_RE.sub(r"\1<REDACTED>@", value)
    value = _HOST_PATH_RE.sub("<HOST_PATH>", value)
    value = _USERS_PATH_RE.sub("<HOST_PATH>", value)
    return value


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_json(item) for key, item in value.items()}
    return value


def _safe_rel(path: Path | None, base: Path) -> str | None:
    if path is None:
        return None
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return redact_string(path.name or ".")


def _resolve_artifact_path(modal_root: Path, run_dir: Path, raw_path: Any) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        return None
    path = Path(raw_path)
    if path.is_absolute():
        return path if path.exists() else None
    candidates = [
        modal_root / path,
        modal_root / "logs" / "forest" / path,
        run_dir / path,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


def _message_text(message: Mapping[str, Any]) -> str:
    if isinstance(message.get("content"), str):
        return str(message["content"])
    if isinstance(message.get("output"), str):
        return str(message["output"])
    content = message.get("content")
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict):
                text = item.get("text") or item.get("input_text")
                if isinstance(text, str):
                    parts.append(text)
                else:
                    parts.append(json.dumps(item, sort_keys=True))
            else:
                parts.append(str(item))
        return "\n".join(parts)
    return json.dumps(message, sort_keys=True, default=str)


def _problem_statement(messages: list[dict[str, Any]]) -> str:
    for message in messages:
        if message.get("role") == "user":
            text = redact_string(_message_text(message)).strip()
            if text:
                return text
    return "EVMBench forest worker trajectory."


def _observation_id(message: Mapping[str, Any]) -> str | None:
    raw = message.get("tool_call_id") or message.get("call_id")
    return str(raw) if raw else None


def _is_tool_observation(message: Mapping[str, Any]) -> bool:
    return message.get("role") == "tool" or message.get("type") == "function_call_output"


def _find_observation(
    messages: list[dict[str, Any]],
    start_index: int,
    tool_call_id: str | None,
) -> dict[str, Any] | None:
    fallback: dict[str, Any] | None = None
    for message in messages[start_index + 1 :]:
        if message.get("role") == "assistant":
            break
        if not _is_tool_observation(message):
            continue
        if tool_call_id and _observation_id(message) == tool_call_id:
            return message
        if tool_call_id is None and fallback is None:
            fallback = message
    return fallback


_COMPILE_OR_TEST_RE = re.compile(
    r"\b("
    r"forge\s+(test|build)|npx\s+hardhat\s+test|hardhat\s+test|npm\s+test|"
    r"pnpm\s+test|yarn\s+test|pytest|make\s+test|cargo\s+test|go\s+test"
    r")\b"
)


def _looks_like_compile_or_test(command: str) -> bool:
    return bool(_COMPILE_OR_TEST_RE.search(command))


def _returncode(observation: Mapping[str, Any] | None) -> int | None:
    if not observation:
        return None
    extra = observation.get("extra")
    if isinstance(extra, dict) and isinstance(extra.get("returncode"), int):
        return int(extra["returncode"])
    return None


def _compile_status(command: str, observation: Mapping[str, Any] | None, already_attempted: bool) -> str:
    if _looks_like_compile_or_test(command):
        returncode = _returncode(observation)
        if returncode == 0:
            return "pass"
        if returncode is not None:
            return "fail"
        return "unknown"
    return "unknown" if already_attempted else "not_attempted"


def _empty_cost(wallclock_sec: float | None = None) -> dict[str, Any]:
    return {
        "tokens_in": None,
        "tokens_out": None,
        "wallclock_sec": wallclock_sec,
        "sandbox_sec": None,
        "gpu_type": None,
        "modal_cost_usd": None,
    }


def _provenance(context: RunContext, trajectory: dict[str, Any] | None, commit: str) -> dict[str, Any]:
    info = trajectory.get("info") if isinstance(trajectory, dict) else None
    config = info.get("config") if isinstance(info, dict) else None
    model_config = config.get("model") if isinstance(config, dict) else None
    env_config = config.get("environment") if isinstance(config, dict) else None
    modal_config = context.modal_forest.get("config") if isinstance(context.modal_forest, dict) else None
    model = (
        model_config.get("model_name") if isinstance(model_config, dict) else None
    ) or (modal_config.get("model") if isinstance(modal_config, dict) else None)
    image_tag = (
        env_config.get("image") if isinstance(env_config, dict) else None
    ) or (modal_config.get("image") if isinstance(modal_config, dict) else None)
    return {
        "evmbench_commit": commit,
        "split": context.split,
        "audit_id": context.audit_id,
        "run_group_id": context.run_group_id,
        "model": str(model or "unknown"),
        "image_tag": str(image_tag or "unknown"),
        "seed": None,
        "grading_commit": None,
        "extractor_version": EXTRACTOR_VERSION,
    }


def _common_row(
    context: RunContext,
    trajectory: dict[str, Any] | None,
    *,
    row_type: str,
    row_id: str,
    experiment: str,
    commit: str,
) -> dict[str, Any]:
    return {
        "schema_version": SCHEMA_VERSION,
        "row_type": row_type,
        "row_id": row_id,
        "experiment": experiment,
        "task_id": f"evmbench/{context.audit_id}",
        "mode": context.mode if context.mode in {"detect", "patch", "exploit"} else "detect",
        "provenance": _provenance(context, trajectory, commit),
        "extensions": {"extractor": {}},
    }


def _branch_id_from_worker(worker: Mapping[str, Any], trajectory_path: Path | None) -> str:
    worker_type = str(worker.get("worker_type") or "")
    role = worker.get("role")
    branch = worker.get("branch")
    worker_name = str(worker.get("worker_name") or "")
    if worker_type == "scout" or worker_name == "scout":
        return "scout.main"
    if worker_type == "global_judge" or worker_name == "global-judge":
        return "global-judge.main"
    if worker_type == "tree_judge":
        return f"{role}.judge" if role else f"{worker_name}.main"
    if role and branch:
        return f"{role}.{branch}"
    if trajectory_path:
        if trajectory_path.name == "scout.traj.json":
            return "scout.main"
        if trajectory_path.name == "global-judge.traj.json":
            return "global-judge.main"
        if trajectory_path.name == "judge.traj.json":
            return f"{trajectory_path.parent.name}.judge"
        if trajectory_path.name.endswith(".traj.json"):
            return f"{trajectory_path.parent.name}.{trajectory_path.name.removesuffix('.traj.json')}"
    return worker_name.replace("-", ".") or "unknown.main"


def _worker_id(worker: Mapping[str, Any], branch_id: str) -> str:
    raw = worker.get("worker_name")
    return str(raw) if isinstance(raw, str) and raw else branch_id


def _trajectory_workers_from_manifest(context: RunContext) -> list[dict[str, Any]]:
    manifest = context.trajectory_manifest
    if not manifest:
        return []
    workers = manifest.get("workers")
    return [worker for worker in workers if isinstance(worker, dict)] if isinstance(workers, list) else []


def _trajectory_workers_from_modal(context: RunContext) -> list[dict[str, Any]]:
    modal_forest = context.modal_forest
    if not modal_forest:
        return []
    workers = modal_forest.get("workers")
    return [worker for worker in workers if isinstance(worker, dict)] if isinstance(workers, list) else []


def _fallback_trajectory_workers(context: RunContext) -> list[dict[str, Any]]:
    workers: list[dict[str, Any]] = []
    for path in sorted((context.modal_root / "logs" / "forest").glob("**/*.traj.json")):
        workers.append({"trajectory_path": _safe_rel(path, context.modal_root)})
    return workers


def _trajectory_sources(context: RunContext) -> list[TrajectorySource]:
    raw_workers = (
        _trajectory_workers_from_manifest(context)
        or _trajectory_workers_from_modal(context)
        or _fallback_trajectory_workers(context)
    )
    sources: list[TrajectorySource] = []
    seen: set[str] = set()
    for worker in raw_workers:
        trajectory_path = _resolve_artifact_path(context.modal_root, context.run_dir, worker.get("trajectory_path"))
        trajectory = _read_json_object(trajectory_path) if trajectory_path and trajectory_path.exists() else None
        branch_id = _branch_id_from_worker(worker, trajectory_path)
        worker_id = _worker_id(worker, branch_id)
        key = f"{worker_id}:{trajectory_path}"
        if key in seen:
            continue
        seen.add(key)
        sources.append(
            TrajectorySource(
                path=trajectory_path,
                worker=worker,
                branch_id=branch_id,
                worker_id=worker_id,
                trajectory=trajectory,
            )
        )
    return sources


def _history_item(command: str, observation: str | None) -> dict[str, Any]:
    return {"action": command, "observation": observation}


def _extract_decisions(
    context: RunContext,
    source: TrajectorySource,
    *,
    experiment: str,
    history_window_size: int,
    commit: str,
) -> list[dict[str, Any]]:
    trajectory = source.trajectory
    if not trajectory:
        return []
    messages_raw = trajectory.get("messages")
    if not isinstance(messages_raw, list):
        return []
    messages = [message for message in messages_raw if isinstance(message, dict)]
    problem_statement = _problem_statement(messages)
    rows: list[dict[str, Any]] = []
    history: list[dict[str, Any]] = []
    compile_or_test_seen = False
    step_idx = 0
    for message_index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        extra = message.get("extra")
        actions = extra.get("actions") if isinstance(extra, dict) else None
        if not isinstance(actions, list):
            continue
        for action in actions:
            if not isinstance(action, dict) or not isinstance(action.get("command"), str):
                continue
            command = redact_string(str(action["command"]))
            tool_call_id = action.get("tool_call_id")
            tool_call_id_str = str(tool_call_id) if tool_call_id else None
            observation_msg = _find_observation(messages, message_index, tool_call_id_str)
            observation = redact_string(_message_text(observation_msg)) if observation_msg else None
            status = _compile_status(command, observation_msg, compile_or_test_seen)
            compile_or_test_seen = compile_or_test_seen or _looks_like_compile_or_test(command)
            row_id = f"trace:{context.audit_id}:{source.branch_id}:step-{step_idx:03d}"
            row = _common_row(
                context,
                trajectory,
                row_type="decision_point",
                row_id=row_id,
                experiment=experiment,
                commit=commit,
            )
            row.update(
                {
                    "branch_id": source.branch_id,
                    "parent_branch_id": None,
                    "worker_id": source.worker_id,
                    "step_idx": step_idx,
                    "problem_statement": problem_statement,
                    "history_window": history[-history_window_size:],
                    "candidate_action": command,
                    "observation": observation,
                    "files_touched": [],
                    "symbols_touched": [],
                    "solidity_ast_diff": None,
                    "unified_diff": None,
                    "compile_status": status,
                    "test_status": None,
                    "anvil_trace_summary": None,
                    "terminal_success": None,
                    "terminal_score": None,
                    "step_reward": None,
                    "prefix_value": None,
                    "branch_rank_within_forest": None,
                    "branch_depth": step_idx,
                    "teacher_rationale": None,
                    "reward_rationale": None,
                    "cost": _empty_cost(),
                    "forest_meta": None,
                }
            )
            extractor_ext = row["extensions"]["extractor"]
            extractor_ext.update(
                {
                    "source_trajectory_path": _safe_rel(source.path, context.run_dir),
                    "source_message_index": message_index,
                    "source_tool_call_id": tool_call_id_str,
                    "worker_exit_status": trajectory.get("info", {}).get("exit_status")
                    if isinstance(trajectory.get("info"), dict)
                    else None,
                    "worker_error": source.worker.get("worker_error") or source.worker.get("error"),
                    "trajectory_format": trajectory.get("trajectory_format"),
                    "mini_version": trajectory.get("info", {}).get("mini_version")
                    if isinstance(trajectory.get("info"), dict)
                    else None,
                    "raw_action_cost": extra.get("cost") if isinstance(extra, dict) else None,
                    "raw_action_timestamp": extra.get("timestamp") if isinstance(extra, dict) else None,
                }
            )
            rows.append(row)
            history.append(_history_item(command, observation))
            step_idx += 1
    return rows


def _phase6_score(context: RunContext) -> float | None:
    row = context.phase6_row or {}
    value = row.get("score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _phase6_max_score(context: RunContext) -> float | None:
    row = context.phase6_row or {}
    value = row.get("max_score")
    return float(value) if isinstance(value, (int, float)) and not isinstance(value, bool) else None


def _terminal_success(context: RunContext, worker_error: Any) -> bool | None:
    if worker_error:
        return None
    score = _phase6_score(context)
    max_score = _phase6_max_score(context)
    if score is None or max_score is None:
        return None
    return score > 0


def _artifact_from_worker(
    context: RunContext,
    source: TrajectorySource,
    predicate: Iterable[str],
) -> str | None:
    raw_paths = source.worker.get("extracted_artifact_paths")
    if not isinstance(raw_paths, list):
        return None
    needles = tuple(predicate)
    for raw_path in raw_paths:
        if not isinstance(raw_path, str):
            continue
        if not all(needle in raw_path for needle in needles):
            continue
        resolved = _resolve_artifact_path(context.modal_root, context.run_dir, raw_path)
        return _safe_rel(resolved, context.run_dir)
    return None


def _submission_path(context: RunContext) -> str | None:
    candidates = [
        context.run_dir / "submission" / "audit.md",
        context.run_dir / "submission" / "agent.diff",
        context.run_dir / "submission" / "txs.json",
        context.modal_root / "submission" / "audit.md",
        context.modal_root / "submission" / "agent.diff",
        context.modal_root / "submission" / "txs.json",
    ]
    for path in candidates:
        if path.exists():
            return _safe_rel(path, context.run_dir)
    return None


def _extract_branch_summary(
    context: RunContext,
    source: TrajectorySource,
    decision_row_ids: list[str],
    *,
    experiment: str,
    commit: str,
) -> dict[str, Any]:
    worker_error = source.worker.get("worker_error") or source.worker.get("error")
    trajectory = source.trajectory
    row_id = f"branch:{context.audit_id}:{source.branch_id}"
    runtime = source.worker.get("runtime_seconds")
    wallclock = float(runtime) if isinstance(runtime, (int, float)) and not isinstance(runtime, bool) else None
    terminal_score = _phase6_score(context) if not worker_error and source.branch_id == "global-judge.main" else None
    row = _common_row(
        context,
        trajectory,
        row_type="branch_summary",
        row_id=row_id,
        experiment=experiment,
        commit=commit,
    )
    row.update(
        {
            "branch_id": source.branch_id,
            "parent_branch_id": None,
            "worker_id": source.worker_id,
            "branch_depth": len(decision_row_ids),
            "decision_row_ids": decision_row_ids,
            "terminal_success": _terminal_success(context, worker_error),
            "terminal_score": terminal_score,
            "best_prefix_value": None,
            "aggregate_score": _phase6_score(context),
            "detected_vulnerability_ids": None,
            "patch_applied": None if context.mode == "detect" else False if context.mode == "patch" else None,
            "exploit_reproduced": None if context.mode != "exploit" else False,
            "branch_artifacts": {
                "trajectory_path": _safe_rel(source.path, context.run_dir),
                "submission_path": _submission_path(context) if source.branch_id == "global-judge.main" else None,
                "diff_path": _artifact_from_worker(context, source, ("branch.diff",)),
                "report_path": _artifact_from_worker(context, source, ("branch.md",))
                or _artifact_from_worker(context, source, ("judge.md",)),
            },
            "cost": _empty_cost(wallclock),
        }
    )
    extractor_ext = row["extensions"]["extractor"]
    extractor_ext.update(
        {
            "source_trajectory_path": _safe_rel(source.path, context.run_dir),
            "worker_exit_status": trajectory.get("info", {}).get("exit_status")
            if isinstance(trajectory, dict) and isinstance(trajectory.get("info"), dict)
            else None,
            "worker_error": worker_error,
            "trajectory_format": trajectory.get("trajectory_format") if isinstance(trajectory, dict) else None,
            "missing_trajectory": source.trajectory is None,
            "trajectory_manifest_path": _safe_rel(context.manifest_path, context.run_dir),
        }
    )
    return row


def _validate_or_record(
    row: dict[str, Any],
    *,
    errors: list[dict[str, Any]],
    continue_on_error: bool,
    source_path: str | None,
) -> dict[str, Any] | None:
    redacted = redact_json(row)
    try:
        return validate_row(redacted)
    except SchemaValidationError as exc:
        errors.append(
            {
                "source_path": source_path,
                "row_type": row.get("row_type"),
                "row_id": row.get("row_id"),
                "error": str(exc),
            }
        )
        if continue_on_error:
            return None
        raise


def extract_rows(
    input_root: Path,
    *,
    experiment: str,
    split_manifest: Path | None,
    history_window_size: int,
    continue_on_error: bool,
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], list[dict[str, Any]]]:
    split_map = _load_split_map(split_manifest)
    commit = _git_commit()
    decision_rows: list[dict[str, Any]] = []
    branch_rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    contexts = discover_runs(input_root, split_map)
    for context in contexts:
        for source in _trajectory_sources(context):
            try:
                raw_decisions = _extract_decisions(
                    context,
                    source,
                    experiment=experiment,
                    history_window_size=history_window_size,
                    commit=commit,
                )
                valid_decisions: list[dict[str, Any]] = []
                for raw_row in raw_decisions:
                    valid = _validate_or_record(
                        raw_row,
                        errors=errors,
                        continue_on_error=continue_on_error,
                        source_path=_safe_rel(source.path, context.run_dir),
                    )
                    if valid:
                        valid_decisions.append(valid)
                raw_summary = _extract_branch_summary(
                    context,
                    source,
                    [str(row["row_id"]) for row in valid_decisions],
                    experiment=experiment,
                    commit=commit,
                )
                valid_summary = _validate_or_record(
                    raw_summary,
                    errors=errors,
                    continue_on_error=continue_on_error,
                    source_path=_safe_rel(source.path, context.run_dir),
                )
                decision_rows.extend(valid_decisions)
                if valid_summary:
                    branch_rows.append(valid_summary)
            except Exception as exc:
                errors.append(
                    {
                        "source_path": _safe_rel(source.path, context.run_dir),
                        "row_type": None,
                        "row_id": None,
                        "error": str(exc),
                    }
                )
                if not continue_on_error:
                    raise
    return decision_rows, branch_rows, errors


def _write_jsonl(path: Path, rows: list[dict[str, Any]]) -> None:
    with path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True) + "\n")


def write_outputs(
    output_dir: Path,
    decision_rows: list[dict[str, Any]],
    branch_rows: list[dict[str, Any]],
    errors: list[dict[str, Any]],
    *,
    write_errors: bool,
) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    _write_jsonl(output_dir / TRACE_JSONL, decision_rows)
    _write_jsonl(output_dir / BRANCH_JSONL, branch_rows)
    if write_errors:
        (output_dir / ERRORS_JSON).write_text(json.dumps(errors, indent=2), encoding="utf-8")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", type=Path, required=True)
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--experiment", default="exp1_forest_scaling")
    parser.add_argument("--split-manifest", type=Path)
    parser.add_argument("--history-window-size", type=int, default=8)
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.history_window_size < 1:
        parser.error("--history-window-size must be >= 1")
    try:
        decision_rows, branch_rows, errors = extract_rows(
            args.input_root,
            experiment=args.experiment,
            split_manifest=args.split_manifest,
            history_window_size=args.history_window_size,
            continue_on_error=args.continue_on_error,
        )
        if errors and not args.continue_on_error:
            return 1
        write_outputs(
            args.output_dir,
            decision_rows,
            branch_rows,
            errors,
            write_errors=args.continue_on_error and bool(errors),
        )
    except Exception as exc:
        print(f"extract_forest_traces failed: {exc}", file=sys.stderr)
        return 1
    return 1 if errors else 0


if __name__ == "__main__":
    raise SystemExit(main())
