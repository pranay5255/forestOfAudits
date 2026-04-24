"""Canonical validators for Forest/PRM dataset rows.

The schema is intentionally dependency-free so exporters can validate artifacts
from lightweight collection jobs without importing training dependencies.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn, TypeAlias, cast

from evmbench.experiments.schema_version import require_supported_schema_version

JsonObject: TypeAlias = dict[str, object]
DatasetArtifact: TypeAlias = JsonObject | list[JsonObject]

ROW_TYPES = frozenset(
    {
        "decision_point",
        "branch_summary",
        "preference_pair",
        "macro_window",
        "controller_state",
    }
)
MODES = frozenset({"detect", "patch", "exploit"})
SPLITS = frozenset({"train", "eval", "test", "holdout", "unspecified"})
COMPILE_STATUSES = frozenset({"pass", "fail", "not_attempted", "unknown"})
CONTROLLER_ACTIONS = frozenset(
    {
        "STOP_AND_SUBMIT",
        "SPAWN_MORE_WORKERS",
        "DEEPEN_TOP_BRANCH",
        "DIVERSIFY_PROMPT",
        "RUN_VERIFIER",
        "SWITCH_TO_PATCH_MODE",
    }
)

COMMON_ROW_KEYS = frozenset(
    {
        "schema_version",
        "row_type",
        "row_id",
        "experiment",
        "task_id",
        "mode",
        "provenance",
        "extensions",
    }
)
DECISION_POINT_KEYS = COMMON_ROW_KEYS | frozenset(
    {
        "branch_id",
        "parent_branch_id",
        "worker_id",
        "step_idx",
        "problem_statement",
        "history_window",
        "candidate_action",
        "observation",
        "files_touched",
        "symbols_touched",
        "solidity_ast_diff",
        "unified_diff",
        "compile_status",
        "test_status",
        "anvil_trace_summary",
        "terminal_success",
        "terminal_score",
        "step_reward",
        "prefix_value",
        "branch_rank_within_forest",
        "branch_depth",
        "teacher_rationale",
        "reward_rationale",
        "cost",
        "forest_meta",
    }
)
BRANCH_SUMMARY_KEYS = COMMON_ROW_KEYS | frozenset(
    {
        "branch_id",
        "parent_branch_id",
        "worker_id",
        "branch_depth",
        "decision_row_ids",
        "terminal_success",
        "terminal_score",
        "best_prefix_value",
        "aggregate_score",
        "detected_vulnerability_ids",
        "patch_applied",
        "exploit_reproduced",
        "branch_artifacts",
        "cost",
    }
)
PREFERENCE_PAIR_KEYS = COMMON_ROW_KEYS | frozenset(
    {
        "depth",
        "same_depth",
        "chosen",
        "rejected",
        "context",
    }
)
MACRO_WINDOW_KEYS = COMMON_ROW_KEYS | frozenset(
    {
        "branch_id",
        "window_start_idx",
        "window_size",
        "state_sequence",
        "action_sequence",
        "observation_sequence",
        "macro_reward",
        "terminal_branch_reward",
        "discounted_return",
        "solidity_ast_diffs",
        "files_touched",
        "compile_status_sequence",
        "test_status_sequence",
    }
)
CONTROLLER_STATE_KEYS = COMMON_ROW_KEYS | frozenset(
    {
        "step_idx",
        "forest_state",
        "controller_action",
        "action_rationale",
        "outcome",
    }
)


class SchemaValidationError(ValueError):
    """Raised when a dataset row or manifest violates the frozen schema."""


def _field(path: str, key: str) -> str:
    if not path:
        return key
    return f"{path}.{key}"


def _fail(path: str, message: str) -> NoReturn:
    raise SchemaValidationError(f"{path}: {message}")


def _require(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        _fail(_field(path, key), "missing required field")
    return mapping[key]


def _require_mapping(mapping: Mapping[str, object], key: str, path: str) -> Mapping[str, object]:
    value = _require(mapping, key, path)
    return _as_mapping(value, _field(path, key))


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    if not all(isinstance(key, str) for key in value):
        _fail(path, "object keys must be strings")
    return cast(Mapping[str, object], value)


def _require_list(mapping: Mapping[str, object], key: str, path: str) -> list[object]:
    value = _require(mapping, key, path)
    if not isinstance(value, list):
        _fail(_field(path, key), "must be a list")
    return cast(list[object], value)


def _require_str(mapping: Mapping[str, object], key: str, path: str) -> str:
    value = _require(mapping, key, path)
    if not isinstance(value, str):
        _fail(_field(path, key), "must be a string")
    if value == "":
        _fail(_field(path, key), "must not be empty")
    return value


def _require_str_or_none(mapping: Mapping[str, object], key: str, path: str) -> str | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(_field(path, key), "must be a string or null")
    return value


def _require_bool(mapping: Mapping[str, object], key: str, path: str) -> bool:
    value = _require(mapping, key, path)
    if not isinstance(value, bool):
        _fail(_field(path, key), "must be a boolean")
    return value


def _require_bool_or_none(mapping: Mapping[str, object], key: str, path: str) -> bool | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not isinstance(value, bool):
        _fail(_field(path, key), "must be a boolean or null")
    return value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_nonnegative_int(mapping: Mapping[str, object], key: str, path: str) -> int:
    value = _require(mapping, key, path)
    if not _is_int(value) or cast(int, value) < 0:
        _fail(_field(path, key), "must be a non-negative integer")
    return cast(int, value)


def _require_positive_int(mapping: Mapping[str, object], key: str, path: str) -> int:
    value = _require(mapping, key, path)
    if not _is_int(value) or cast(int, value) <= 0:
        _fail(_field(path, key), "must be a positive integer")
    return cast(int, value)


def _require_nonnegative_int_or_none(
    mapping: Mapping[str, object], key: str, path: str
) -> int | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not _is_int(value) or cast(int, value) < 0:
        _fail(_field(path, key), "must be a non-negative integer or null")
    return cast(int, value)


def _require_positive_int_or_none(
    mapping: Mapping[str, object], key: str, path: str
) -> int | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not _is_int(value) or cast(int, value) <= 0:
        _fail(_field(path, key), "must be a positive integer or null")
    return cast(int, value)


def _require_number_or_none(mapping: Mapping[str, object], key: str, path: str) -> float | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not _is_number(value):
        _fail(_field(path, key), "must be a number or null")
    return float(cast(float | int, value))


def _require_nonnegative_number_or_none(
    mapping: Mapping[str, object], key: str, path: str
) -> float | None:
    value = _require_number_or_none(mapping, key, path)
    if value is not None and value < 0:
        _fail(_field(path, key), "must be non-negative or null")
    return value


def _require_rate_or_none(mapping: Mapping[str, object], key: str, path: str) -> float | None:
    value = _require_number_or_none(mapping, key, path)
    if value is not None and not 0.0 <= value <= 1.0:
        _fail(_field(path, key), "must be between 0 and 1 or null")
    return value


def _require_enum(
    mapping: Mapping[str, object], key: str, path: str, allowed: frozenset[str]
) -> str:
    value = _require_str(mapping, key, path)
    if value not in allowed:
        _fail(_field(path, key), f"must be one of {sorted(allowed)}")
    return value


def _reject_unknown_keys(mapping: Mapping[str, object], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _fail(path or "<row>", f"unknown fields: {', '.join(unknown)}")


def _validate_extensions(row: Mapping[str, object], path: str) -> None:
    if "extensions" not in row:
        return
    _as_mapping(row["extensions"], _field(path, "extensions"))


def _validate_str_list(mapping: Mapping[str, object], key: str, path: str) -> list[str]:
    values = _require_list(mapping, key, path)
    for index, value in enumerate(values):
        if not isinstance(value, str):
            _fail(f"{_field(path, key)}[{index}]", "must be a string")
    return cast(list[str], values)


def _validate_str_list_or_none(
    mapping: Mapping[str, object], key: str, path: str
) -> list[str] | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not isinstance(value, list):
        _fail(_field(path, key), "must be a list of strings or null")
    for index, item in enumerate(value):
        if not isinstance(item, str):
            _fail(f"{_field(path, key)}[{index}]", "must be a string")
    return cast(list[str], value)


def _validate_mapping_or_none(mapping: Mapping[str, object], key: str, path: str) -> None:
    value = _require(mapping, key, path)
    if value is None:
        return
    _as_mapping(value, _field(path, key))


def validate_provenance(provenance: Mapping[str, object], path: str = "provenance") -> JsonObject:
    allowed = frozenset(
        {
            "evmbench_commit",
            "split",
            "audit_id",
            "run_group_id",
            "model",
            "image_tag",
            "seed",
            "grading_commit",
            "extractor_version",
        }
    )
    _reject_unknown_keys(provenance, allowed, path)
    _require_str(provenance, "evmbench_commit", path)
    _require_enum(provenance, "split", path, SPLITS)
    _require_str(provenance, "audit_id", path)
    _require_str(provenance, "run_group_id", path)
    _require_str(provenance, "model", path)
    _require_str(provenance, "image_tag", path)
    _require_nonnegative_int_or_none(provenance, "seed", path)
    _require_str_or_none(provenance, "grading_commit", path)
    _require_str(provenance, "extractor_version", path)
    return dict(provenance)


def validate_test_status(
    value: object, path: str = "test_status", *, nullable: bool = True
) -> JsonObject | None:
    if value is None:
        if nullable:
            return None
        _fail(path, "must be an object")
    status = _as_mapping(value, path)
    allowed = frozenset({"num_passed", "num_failed", "num_errors"})
    _reject_unknown_keys(status, allowed, path)
    _require_nonnegative_int(status, "num_passed", path)
    _require_nonnegative_int(status, "num_failed", path)
    _require_nonnegative_int(status, "num_errors", path)
    return dict(status)


def validate_cost(cost: Mapping[str, object], path: str = "cost") -> JsonObject:
    allowed = frozenset(
        {
            "tokens_in",
            "tokens_out",
            "wallclock_sec",
            "sandbox_sec",
            "gpu_type",
            "modal_cost_usd",
        }
    )
    _reject_unknown_keys(cost, allowed, path)
    _require_nonnegative_int_or_none(cost, "tokens_in", path)
    _require_nonnegative_int_or_none(cost, "tokens_out", path)
    _require_nonnegative_number_or_none(cost, "wallclock_sec", path)
    _require_nonnegative_number_or_none(cost, "sandbox_sec", path)
    _require_str_or_none(cost, "gpu_type", path)
    _require_nonnegative_number_or_none(cost, "modal_cost_usd", path)
    return dict(cost)


def validate_anvil_trace_summary(value: object, path: str = "anvil_trace_summary") -> None:
    if value is None:
        return
    summary = _as_mapping(value, path)
    allowed = frozenset({"num_reverts", "num_events", "gas_used"})
    _reject_unknown_keys(summary, allowed, path)
    _require_nonnegative_int_or_none(summary, "num_reverts", path)
    _require_nonnegative_int_or_none(summary, "num_events", path)
    _require_nonnegative_int_or_none(summary, "gas_used", path)


def validate_reward_rationale(value: object, path: str = "reward_rationale") -> None:
    if value is None:
        return
    rationale = _as_mapping(value, path)
    allowed = frozenset({"evidence", "failure_modes"})
    _reject_unknown_keys(rationale, allowed, path)
    _validate_str_list(rationale, "evidence", path)
    _validate_str_list(rationale, "failure_modes", path)


def validate_forest_meta(value: object, path: str = "forest_meta") -> None:
    if value is None:
        return
    meta = _as_mapping(value, path)
    allowed = frozenset(
        {
            "num_workers_at_step",
            "best_branch_score",
            "score_entropy",
            "worker_disagreement",
        }
    )
    _reject_unknown_keys(meta, allowed, path)
    _require_nonnegative_int_or_none(meta, "num_workers_at_step", path)
    _require_number_or_none(meta, "best_branch_score", path)
    _require_nonnegative_number_or_none(meta, "score_entropy", path)
    _require_rate_or_none(meta, "worker_disagreement", path)


def validate_history_window(value: object, path: str = "history_window") -> list[object]:
    if not isinstance(value, list):
        _fail(path, "must be a list")
    for index, item in enumerate(value):
        _as_mapping(item, f"{path}[{index}]")
    return cast(list[object], value)


def _validate_common_row(row: Mapping[str, object]) -> str:
    require_supported_schema_version(_require(row, "schema_version", ""))
    row_type = _require_enum(row, "row_type", "", ROW_TYPES)
    _require_str(row, "row_id", "")
    _require_str(row, "experiment", "")
    _require_str(row, "task_id", "")
    _require_enum(row, "mode", "", MODES)
    validate_provenance(_require_mapping(row, "provenance", ""), "provenance")
    _validate_extensions(row, "")
    return row_type


def validate_row(row: Mapping[str, object]) -> JsonObject:
    """Validate one exported dataset row and return a shallow JSON object copy."""

    row_type = _validate_common_row(row)
    match row_type:
        case "decision_point":
            _validate_decision_point(row)
        case "branch_summary":
            _validate_branch_summary(row)
        case "preference_pair":
            _validate_preference_pair(row)
        case "macro_window":
            _validate_macro_window(row)
        case "controller_state":
            _validate_controller_state(row)
        case _:
            _fail("row_type", f"unsupported row type {row_type!r}")
    return dict(row)


def _validate_decision_point(row: Mapping[str, object]) -> None:
    _reject_unknown_keys(row, DECISION_POINT_KEYS, "")
    _require_str(row, "branch_id", "")
    _require_str_or_none(row, "parent_branch_id", "")
    _require_str(row, "worker_id", "")
    _require_nonnegative_int(row, "step_idx", "")
    _require_str(row, "problem_statement", "")
    validate_history_window(_require(row, "history_window", ""), "history_window")
    _require_str(row, "candidate_action", "")
    _require_str_or_none(row, "observation", "")
    _validate_str_list(row, "files_touched", "")
    _validate_str_list(row, "symbols_touched", "")
    _validate_mapping_or_none(row, "solidity_ast_diff", "")
    _require_str_or_none(row, "unified_diff", "")
    _require_enum(row, "compile_status", "", COMPILE_STATUSES)
    validate_test_status(_require(row, "test_status", ""), "test_status")
    validate_anvil_trace_summary(_require(row, "anvil_trace_summary", ""), "anvil_trace_summary")
    _require_bool_or_none(row, "terminal_success", "")
    _require_number_or_none(row, "terminal_score", "")
    _require_number_or_none(row, "step_reward", "")
    _require_number_or_none(row, "prefix_value", "")
    _require_positive_int_or_none(row, "branch_rank_within_forest", "")
    _require_nonnegative_int(row, "branch_depth", "")
    _require_str_or_none(row, "teacher_rationale", "")
    validate_reward_rationale(_require(row, "reward_rationale", ""), "reward_rationale")
    validate_cost(_require_mapping(row, "cost", ""), "cost")
    validate_forest_meta(_require(row, "forest_meta", ""), "forest_meta")


def _validate_branch_summary(row: Mapping[str, object]) -> None:
    _reject_unknown_keys(row, BRANCH_SUMMARY_KEYS, "")
    _require_str(row, "branch_id", "")
    _require_str_or_none(row, "parent_branch_id", "")
    _require_str(row, "worker_id", "")
    _require_nonnegative_int(row, "branch_depth", "")
    _validate_str_list(row, "decision_row_ids", "")
    _require_bool_or_none(row, "terminal_success", "")
    _require_number_or_none(row, "terminal_score", "")
    _require_number_or_none(row, "best_prefix_value", "")
    _require_number_or_none(row, "aggregate_score", "")
    _validate_str_list_or_none(row, "detected_vulnerability_ids", "")
    _require_bool_or_none(row, "patch_applied", "")
    _require_bool_or_none(row, "exploit_reproduced", "")
    _validate_branch_artifacts(_require_mapping(row, "branch_artifacts", ""), "branch_artifacts")
    validate_cost(_require_mapping(row, "cost", ""), "cost")


def _validate_branch_artifacts(artifacts: Mapping[str, object], path: str) -> None:
    allowed = frozenset({"trajectory_path", "submission_path", "diff_path", "report_path"})
    _reject_unknown_keys(artifacts, allowed, path)
    _require_str_or_none(artifacts, "trajectory_path", path)
    _require_str_or_none(artifacts, "submission_path", path)
    _require_str_or_none(artifacts, "diff_path", path)
    _require_str_or_none(artifacts, "report_path", path)


def _validate_preference_pair(row: Mapping[str, object]) -> None:
    _reject_unknown_keys(row, PREFERENCE_PAIR_KEYS, "")
    _require_nonnegative_int(row, "depth", "")
    _require_bool(row, "same_depth", "")
    _validate_pair_side(_require_mapping(row, "chosen", ""), "chosen")
    _validate_pair_side(_require_mapping(row, "rejected", ""), "rejected")
    _validate_pair_context(_require_mapping(row, "context", ""), "context")


def _validate_pair_side(side: Mapping[str, object], path: str) -> None:
    allowed = frozenset(
        {
            "branch_id",
            "trace_row_id",
            "history_window",
            "terminal_score",
            "step_reward",
            "prefix_value",
        }
    )
    _reject_unknown_keys(side, allowed, path)
    _require_str(side, "branch_id", path)
    _require_str(side, "trace_row_id", path)
    validate_history_window(_require(side, "history_window", path), _field(path, "history_window"))
    _require_number_or_none(side, "terminal_score", path)
    _require_number_or_none(side, "step_reward", path)
    _require_number_or_none(side, "prefix_value", path)


def _validate_pair_context(context: Mapping[str, object], path: str) -> None:
    allowed = frozenset(
        {
            "problem_statement",
            "files_touched",
            "num_workers_at_depth",
            "best_score_at_depth",
            "score_entropy_at_depth",
        }
    )
    _reject_unknown_keys(context, allowed, path)
    _require_str(context, "problem_statement", path)
    _validate_str_list(context, "files_touched", path)
    _require_positive_int(context, "num_workers_at_depth", path)
    _require_number_or_none(context, "best_score_at_depth", path)
    _require_nonnegative_number_or_none(context, "score_entropy_at_depth", path)


def _validate_macro_window(row: Mapping[str, object]) -> None:
    _reject_unknown_keys(row, MACRO_WINDOW_KEYS, "")
    _require_str(row, "branch_id", "")
    _require_nonnegative_int(row, "window_start_idx", "")
    window_size = _require_positive_int(row, "window_size", "")
    states = _require_list(row, "state_sequence", "")
    actions = _require_list(row, "action_sequence", "")
    observations = _require_list(row, "observation_sequence", "")
    ast_diffs = _require_list(row, "solidity_ast_diffs", "")
    compile_statuses = _require_list(row, "compile_status_sequence", "")
    test_statuses = _require_list(row, "test_status_sequence", "")
    for key, values in {
        "state_sequence": states,
        "action_sequence": actions,
        "observation_sequence": observations,
        "solidity_ast_diffs": ast_diffs,
        "compile_status_sequence": compile_statuses,
        "test_status_sequence": test_statuses,
    }.items():
        if len(values) != window_size:
            _fail(key, f"length must equal window_size={window_size}")
    for index, state in enumerate(states):
        _as_mapping(state, f"state_sequence[{index}]")
    for index, action in enumerate(actions):
        if not isinstance(action, str):
            _fail(f"action_sequence[{index}]", "must be a string")
    for index, observation in enumerate(observations):
        if not isinstance(observation, str):
            _fail(f"observation_sequence[{index}]", "must be a string")
    for index, ast_diff in enumerate(ast_diffs):
        if ast_diff is not None:
            _as_mapping(ast_diff, f"solidity_ast_diffs[{index}]")
    for index, status in enumerate(compile_statuses):
        if not isinstance(status, str) or status not in COMPILE_STATUSES:
            _fail(f"compile_status_sequence[{index}]", f"must be one of {sorted(COMPILE_STATUSES)}")
    for index, status in enumerate(test_statuses):
        validate_test_status(status, f"test_status_sequence[{index}]")
    _require_number_or_none(row, "macro_reward", "")
    _require_number_or_none(row, "terminal_branch_reward", "")
    _require_number_or_none(row, "discounted_return", "")
    _validate_str_list(row, "files_touched", "")


def _validate_controller_state(row: Mapping[str, object]) -> None:
    _reject_unknown_keys(row, CONTROLLER_STATE_KEYS, "")
    _require_nonnegative_int(row, "step_idx", "")
    _validate_forest_state(_require_mapping(row, "forest_state", ""), "forest_state")
    _require_enum(row, "controller_action", "", CONTROLLER_ACTIONS)
    _require_str_or_none(row, "action_rationale", "")
    _validate_controller_outcome(_require_mapping(row, "outcome", ""), "outcome")


def _validate_forest_state(state: Mapping[str, object], path: str) -> None:
    allowed = frozenset(
        {
            "num_workers",
            "step_budget_used",
            "best_prm_score",
            "score_entropy",
            "worker_disagreement",
            "compile_success_rate",
            "unique_files_touched",
            "duplicate_action_rate",
            "branch_depths",
            "current_best_score",
            "avg_worker_progress",
        }
    )
    _reject_unknown_keys(state, allowed, path)
    _require_positive_int(state, "num_workers", path)
    _require_nonnegative_int(state, "step_budget_used", path)
    _require_rate_or_none(state, "best_prm_score", path)
    _require_nonnegative_number_or_none(state, "score_entropy", path)
    _require_rate_or_none(state, "worker_disagreement", path)
    _require_rate_or_none(state, "compile_success_rate", path)
    _require_nonnegative_int(state, "unique_files_touched", path)
    _require_rate_or_none(state, "duplicate_action_rate", path)
    depths = _require_list(state, "branch_depths", path)
    for index, depth in enumerate(depths):
        if not _is_int(depth) or cast(int, depth) < 0:
            _fail(f"{_field(path, 'branch_depths')}[{index}]", "must be a non-negative integer")
    _require_rate_or_none(state, "current_best_score", path)
    _require_nonnegative_number_or_none(state, "avg_worker_progress", path)


def _validate_controller_outcome(outcome: Mapping[str, object], path: str) -> None:
    allowed = frozenset({"terminal_success", "terminal_score", "total_cost_usd", "workers_used"})
    _reject_unknown_keys(outcome, allowed, path)
    _require_bool_or_none(outcome, "terminal_success", path)
    _require_number_or_none(outcome, "terminal_score", path)
    _require_nonnegative_number_or_none(outcome, "total_cost_usd", path)
    _require_nonnegative_int(outcome, "workers_used", path)


def validate_artifact(path: Path) -> DatasetArtifact:
    """Load and validate JSON, JSONL, or manifest artifacts through one entrypoint."""

    if path.suffix == ".jsonl":
        rows: list[JsonObject] = []
        with path.open(encoding="utf-8") as handle:
            for line_number, line in enumerate(handle, start=1):
                if not line.strip():
                    continue
                payload = json.loads(line)
                rows.append(validate_row(_as_mapping(payload, f"{path}:{line_number}")))
        return rows

    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)

    if isinstance(payload, list):
        rows = []
        for index, item in enumerate(payload):
            rows.append(validate_row(_as_mapping(item, f"{path}[{index}]")))
        return rows

    payload_object = _as_mapping(payload, str(path))
    if payload_object.get("manifest_type") == "forest_prm_dataset_manifest":
        from evmbench.experiments.dataset_manifest import validate_dataset_manifest

        return validate_dataset_manifest(payload_object)
    return validate_row(payload_object)
