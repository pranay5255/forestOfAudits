# Forest Trajectory Extractor Implementation Plan

## Summary

Build a post-processing extractor that converts mini-swe-agent forest
`*.traj.json` files plus Phase 6 and Modal metadata into validated v1 dataset
artifacts.

The first implementation scope is intentionally limited to:

- `decision_point` rows for worker steps.
- `branch_summary` rows for completed or failed branches.

Derived `preference_pair`, `macro_window`, and `controller_state` rows should be
implemented later from the validated trace and branch outputs.

## Module Contract

Add a new module:

```text
evmbench/experiments/extract_forest_traces.py
```

Add a CLI with these arguments:

```bash
uv run python -m evmbench.experiments.extract_forest_traces \
  --input-root runs/phase6/<run> \
  --output-dir runs/phase6/<run>/dataset \
  --experiment exp1_forest_scaling \
  --split-manifest evmbench/experiments/schema_examples/train_eval_split_manifest.json
```

Required options:

- `--input-root`: Phase 6 output root or a single run group root.
- `--output-dir`: directory for extracted JSONL artifacts.

Optional options:

- `--experiment`: default `exp1_forest_scaling`.
- `--split-manifest`: manifest used to map `audit_id` to `train`, `eval`, or
  `unspecified`.
- `--history-window-size`: default `8`.
- `--continue-on-error`: write valid rows and an `extract-errors.json` report
  instead of failing on the first invalid row.

## Inputs

Read these files when present:

| Source | Purpose |
|---|---|
| `phase6-run-matrix.json` | Planned audits, runners, commands, and stable run group context. |
| `phase6-results.json` | Run-level score, failure, submission, and convenience metadata. |
| `modal/logs/modal-forest-result.json` | Worker role, branch, runtime, errors, output paths, and trajectory paths. |
| `logs/**/*.traj.json` | mini-swe-agent messages, actions, observations, token usage, cost, timestamps, and exit status. |
| `run.log` | EVMBench grading and terminal run events when available. |
| `submission/audit.md` | Final submission artifact link and detect evidence source. |

The extractor must never mutate these raw artifacts.

## Outputs

Write exactly these JSONL files:

```text
forest_trace_evm_scaling_v0.jsonl
forest_branch_summaries_v0.jsonl
```

When `--continue-on-error` is used, also write:

```text
extract-errors.json
```

Every emitted row must pass `trace_schema.validate_row()` before writing.

## Extraction Rules

### Decision Points

Create one `decision_point` row for each assistant action in a trajectory.

- Treat each assistant message with `extra.actions[*].command` as a selected
  action.
- Use the action command as `candidate_action`.
- Pair the next matching `tool` message by `tool_call_id` as `observation`.
- If no matching tool message exists, set `observation` to `null`.
- Use prior action/observation summaries from the same branch as
  `history_window`, capped by `--history-window-size`.

Default unavailable fields conservatively:

- `terminal_success`: `null`
- `terminal_score`: `null`
- `step_reward`: `null`
- `prefix_value`: `null`
- `branch_rank_within_forest`: `null`
- `solidity_ast_diff`: `null`
- `unified_diff`: `null`
- `anvil_trace_summary`: `null`
- `teacher_rationale`: `null`
- `reward_rationale`: `null`

Compile and test fields:

- `compile_status`: `unknown` unless command output clearly proves `pass` or
  `fail`.
- `compile_status`: `not_attempted` only when no compile/test-like command ran
  in the branch up to that step.
- `test_status`: `null` unless parsed test output can provide `num_passed`,
  `num_failed`, and `num_errors`.

### Branch Summaries

Create one `branch_summary` row for each forest branch trajectory.

- Link branch rows to their decision rows through `decision_row_ids`.
- Use `modal-forest-result.json` for worker runtime, role, branch, error, and
  artifact metadata when available.
- Use Phase 6 or grader metadata for `terminal_success`, `terminal_score`,
  `aggregate_score`, and `detected_vulnerability_ids` when available.
- For failed or incomplete workers, preserve the branch row with nullable
  terminal labels and error details under `extensions.extractor`.
- For detect-only runs, set `patch_applied` and `exploit_reproduced` to `null`.

## Identifiers

Use deterministic identifiers.

| Field | Format |
|---|---|
| `task_id` | `evmbench/<audit_id>` |
| `branch_id` | `<role>.<branch>`, for example `token-flow.branch-01` |
| `worker_id` | Modal `worker_name` when available, else `<role>/<branch>` |
| Decision `row_id` | `trace:<audit_id>:<branch_id>:step-<NNN>` |
| Branch `row_id` | `branch:<audit_id>:<branch_id>` |

For non-branch trajectories such as `scout.traj.json` or `global-judge.traj.json`,
use branch ids like `scout.main` and `global-judge.main`.

## Provenance

Populate the required provenance bundle on every row:

- `evmbench_commit`: current extractor repo commit when available, else `UNSET`.
- `split`: from the split manifest by `audit_id`, else `unspecified`.
- `audit_id`: parsed from run metadata or run directory.
- `run_group_id`: Phase 6 run group id or stable run directory name.
- `model`: trajectory `info.config.model.model_name` when available, else
  Phase 6 runner model, else `unknown`.
- `image_tag`: trajectory `info.config.environment.image` when available, else
  `unknown`.
- `seed`: parsed from run metadata when available, else `null`.
- `grading_commit`: parsed when available, else `null`.
- `extractor_version`: `trace-schema-1.0.0`.

## Redaction

Apply redaction before writing any row.

Always remove or redact:

- `OPENAI_API_KEY`
- Modal tokens and secrets
- GitHub tokens
- private registry credentials
- local absolute host paths
- local usernames where they appear in artifact paths or logs

Keep reproducibility-safe values:

- audit ids
- public model ids
- public image tags
- repo-relative paths
- command names
- benchmark scores

Raw trajectory environment maps must not be copied directly into dataset rows.

## Extensions

Put extractor-only metadata under `extensions.extractor`.

Recommended fields:

- `source_trajectory_path`
- `source_message_index`
- `source_tool_call_id`
- `worker_exit_status`
- `worker_error`
- `trajectory_format`
- `mini_version`
- `raw_action_cost`
- `raw_action_timestamp`

Do not add new top-level fields unless `SCHEMA.md` and `trace_schema.py` are
versioned first.

## Validation And Failure Handling

Default behavior:

- Validate every row with `trace_schema.validate_row()`.
- Fail fast on the first invalid row.
- Do not write partial output on fail-fast errors.

With `--continue-on-error`:

- Write all valid rows.
- Skip invalid rows.
- Write `extract-errors.json` with source path, message index, row type, and
  validation error.

The CLI should exit non-zero if any errors occurred, even with
`--continue-on-error`, unless a later explicit `--allow-errors` option is added.

## Test Plan

Add tests in:

```text
tests/test_extract_forest_traces.py
```

Unit tests:

- assistant action plus matching tool observation becomes one `decision_point`.
- missing tool observation produces `observation: null`.
- multiple actions in one assistant message produce multiple decision rows.
- trajectory environment secrets are redacted.
- deterministic row ids and branch ids are stable.
- failed worker still produces a `branch_summary`.

Integration-style tests:

- Build a temporary Phase 6 forest layout with one `modal-forest-result.json`
  and one branch trajectory.
- Run the extractor.
- Assert both JSONL outputs exist.
- Assert both outputs pass `trace_schema.validate_artifact()`.
- Assert branch summary links to emitted decision row ids.

Required validation commands:

```bash
uv run pytest tests/test_trace_schema.py
uv run pytest tests/test_extract_forest_traces.py
```

## Implementation Checklist

- [ ] Add path discovery for Phase 6 output roots and single run roots.
- [ ] Add safe JSON loading helpers with source-path error messages.
- [ ] Add trajectory parser for mini-swe-agent `trajectory_format`.
- [ ] Add assistant action to tool observation pairing.
- [ ] Add branch metadata join from `modal-forest-result.json`.
- [ ] Add Phase 6 row metadata join from `phase6-results.json`.
- [ ] Add provenance builder.
- [ ] Add redaction helpers.
- [ ] Add row builders for `decision_point` and `branch_summary`.
- [ ] Add JSONL writer that validates before writing.
- [ ] Add `--continue-on-error` error report behavior.
- [ ] Add tests and fixtures.
