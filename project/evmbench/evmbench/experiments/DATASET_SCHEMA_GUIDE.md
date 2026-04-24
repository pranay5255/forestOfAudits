# Forest/PRM Dataset Schema Guide

This guide explains the dataset schema for Forest-of-Audits PRM artifacts in a
reader-friendly format. The formal validator contract lives in `trace_schema.py`,
and the concise normative spec lives in `SCHEMA.md`.

## Big Picture

Phase 6 produces raw execution artifacts. The dataset schema defines the stable
ML/publication format that an extractor should build from those artifacts.

| Layer | Artifact | Purpose |
|---|---|---|
| Phase 6 raw execution | `run.log`, `*.traj.json`, `modal-forest-result.json`, `submission/audit.md` | Preserve what actually happened during a run. |
| Phase 6 summary | `phase6-results.json`, `phase6-summary.md`, `phase6-slide-data.json`, `phase6-slide-data.csv` | Summarize runs for inspection, reporting, and slides. |
| Dataset schema | JSON/JSONL rows validated by `trace_schema.validate_artifact()` | Normalize raw artifacts into stable training/eval rows. |
| Dataset manifest | `train_eval_split_manifest.json` | Describe splits, provenance, redaction rules, and dataset-card metadata. |

The schema is intentionally stricter than the raw Phase 6 output. Raw runner
artifacts may be incomplete or runner-specific; dataset rows must have explicit
types, schema versions, provenance, and documented null semantics.

## Row Types

Each JSON row has a `row_type`. The row type defines what one row represents.

| `row_type` | What One Row Means | Typical Source | Main Use |
|---|---|---|---|
| `decision_point` | One worker step: state, action, observation, optional test/code signals, and reward labels. | Worker `*.traj.json` files from mini-swe-agent. | Main PRM/process-reward training data. |
| `branch_summary` | One completed branch or worker trajectory with terminal outcome and artifact links. | `modal-forest-result.json`, `run.log`, trajectory/report paths. | Branch-level ranking, filtering, and terminal labels. |
| `preference_pair` | A chosen branch prefix compared against a rejected branch prefix. | Derived offline from decision and branch rows. | Pairwise preference/reranker training. |
| `macro_window` | A fixed-size sequence of consecutive decision steps. | Derived offline from ordered `decision_point` rows. | Macro PRM training on multi-step reward. |
| `controller_state` | Forest-level state and a budget/control action label. | Derived from forest metadata, scores, and future controller logs. | Adaptive forest controller training. |

## Common Columns

Every row type shares these columns.

| Column | Type | Required | Meaning |
|---|---|---:|---|
| `schema_version` | string | yes | Version of this schema. Current value is `1.0.0`. |
| `row_type` | enum | yes | Row shape: `decision_point`, `branch_summary`, `preference_pair`, `macro_window`, or `controller_state`. |
| `row_id` | string | yes | Stable unique row id. Prefer deterministic ids derived from audit, branch, and step. |
| `experiment` | string | yes | Experiment name, for example `exp1_forest_scaling`. |
| `task_id` | string | yes | EVMBench task id, usually `evmbench/<audit_id>`. |
| `mode` | enum | yes | Task mode: `detect`, `patch`, or `exploit`. Current Phase 6 runs are detect-focused. |
| `provenance` | object | yes | Reproducibility metadata. See the provenance table below. |
| `extensions` | object | no | Optional namespaced escape hatch for exporter-specific fields. Unknown top-level fields are rejected. |

## Provenance Bundle

Every row and manifest carries enough provenance to reproduce or compare the
artifact later.

| Provenance Field | Type | Meaning |
|---|---|---|
| `evmbench_commit` | string | EVMBench commit used for the run or extraction. |
| `split` | enum | Dataset split: `train`, `eval`, `test`, `holdout`, or `unspecified`. |
| `audit_id` | string | Audit id, for example `2024-01-canto`; use `multiple` only for manifests spanning audits. |
| `run_group_id` | string | Stable run group identifier from Phase 6 or the extractor. |
| `model` | string | Model id used by the worker or source run. |
| `image_tag` | string | Audit sandbox image tag. |
| `seed` | integer or null | Deterministic seed, or `null` if no seed was set. |
| `grading_commit` | string or null | Grader commit, or `null` if grading code is covered by `evmbench_commit`. |
| `extractor_version` | string | Extractor implementation version. Current value is `trace-schema-1.0.0`. |

## `decision_point`

Use `decision_point` rows for step-level PRM examples. One row is one agent
decision in one branch.

| Column | Type | Meaning |
|---|---|---|
| `branch_id` | string | Branch identifier, for example `token-flow.branch-01`. |
| `parent_branch_id` | string or null | Parent branch, if this branch was forked from another branch. |
| `worker_id` | string | Worker name that emitted the step. |
| `step_idx` | integer | Zero-based step index inside the branch trajectory. |
| `problem_statement` | string | Task prompt/problem visible to the worker. |
| `history_window` | list of objects | Prior action/observation context used for this decision. |
| `candidate_action` | string | Action selected by the worker at this step. |
| `observation` | string or null | Result of the action, or `null` if unavailable. |
| `files_touched` | list of strings | Files touched by the step. Empty for detect-only inspection steps. |
| `symbols_touched` | list of strings | Relevant contracts/functions/symbols touched or inspected. |
| `solidity_ast_diff` | object or null | Structured Solidity diff, if an edit occurred and extraction is available. |
| `unified_diff` | string or null | Text diff, if an edit occurred. |
| `compile_status` | enum | `pass`, `fail`, `not_attempted`, or `unknown`. |
| `test_status` | object or null | Test counts if tests ran; otherwise `null`. |
| `anvil_trace_summary` | object or null | Optional Anvil summary: reverts, events, gas. |
| `terminal_success` | bool or null | Final branch success label, backfilled after grading. |
| `terminal_score` | number or null | Final branch score, backfilled after grading. |
| `step_reward` | number or null | Offline per-step reward label. |
| `prefix_value` | number or null | Offline value label for the prefix ending at this step. |
| `branch_rank_within_forest` | integer or null | Rank of this branch among forest branches when known. |
| `branch_depth` | integer | Depth of this branch at the decision point. |
| `teacher_rationale` | string or null | Optional teacher/extractor rationale. |
| `reward_rationale` | object or null | Optional evidence and failure modes supporting the reward label. |
| `cost` | object | Token/runtime/cost fields. Individual values may be `null`. |
| `forest_meta` | object or null | Optional forest context at this step. |

### `test_status`

| Field | Meaning |
|---|---|
| `num_passed` | Number of passing tests. |
| `num_failed` | Number of failing tests. |
| `num_errors` | Number of errored tests. |

### `cost`

| Field | Meaning |
|---|---|
| `tokens_in` | Prompt/input tokens, or `null`. |
| `tokens_out` | Completion/output tokens, or `null`. |
| `wallclock_sec` | End-to-end elapsed seconds, or `null`. |
| `sandbox_sec` | Sandbox execution seconds, or `null`. |
| `gpu_type` | Modal/GPU type if relevant, or `null`. |
| `modal_cost_usd` | Estimated Modal cost, or `null`. |

## `branch_summary`

Use `branch_summary` rows for one completed worker branch. These rows connect
step-level data to terminal outcomes.

| Column | Type | Meaning |
|---|---|---|
| `branch_id` | string | Branch identifier. |
| `parent_branch_id` | string or null | Parent branch if forked. |
| `worker_id` | string | Worker that ran the branch. |
| `branch_depth` | integer | Number of decision steps in the branch. |
| `decision_row_ids` | list of strings | Links back to `decision_point.row_id` values. |
| `terminal_success` | bool or null | Whether the final branch solved the task. |
| `terminal_score` | number or null | Final benchmark score for the branch. |
| `best_prefix_value` | number or null | Best prefix value observed in the branch. |
| `aggregate_score` | number or null | Optional combined branch score. |
| `detected_vulnerability_ids` | list of strings or null | Vulnerabilities detected by this branch, if known. |
| `patch_applied` | bool or null | Patch outcome. `null` for detect-only rows. |
| `exploit_reproduced` | bool or null | Exploit outcome. `null` for detect-only rows. |
| `branch_artifacts` | object | Paths to trajectory/report/diff/submission artifacts. |
| `cost` | object | Aggregate branch cost. |

## `preference_pair`

Use `preference_pair` rows when training a model to prefer one branch prefix over
another.

| Column | Type | Meaning |
|---|---|---|
| `depth` | integer | Comparison depth. |
| `same_depth` | bool | Whether chosen and rejected prefixes are from the same depth. |
| `chosen` | object | Preferred branch prefix. |
| `rejected` | object | Less-preferred branch prefix. |
| `context` | object | Shared problem and forest context for the comparison. |

Each `chosen` and `rejected` object contains:

| Field | Meaning |
|---|---|
| `branch_id` | Branch identifier. |
| `trace_row_id` | Linked `decision_point.row_id`. |
| `history_window` | Prefix context. |
| `terminal_score` | Final score of the branch. |
| `step_reward` | Step reward at the comparison point. |
| `prefix_value` | Prefix value at the comparison point. |

## `macro_window`

Use `macro_window` rows for multi-step reward modeling.

| Column | Type | Meaning |
|---|---|---|
| `branch_id` | string | Source branch. |
| `window_start_idx` | integer | First step index in the window. |
| `window_size` | integer | Number of steps. All sequence fields must match this length. |
| `state_sequence` | list of objects | State observations for each step. |
| `action_sequence` | list of strings | Actions for each step. |
| `observation_sequence` | list of strings | Observations after each action. |
| `macro_reward` | number or null | Multi-step reward target. |
| `terminal_branch_reward` | number or null | Final branch reward. |
| `discounted_return` | number or null | Discounted return over the window. |
| `solidity_ast_diffs` | list of objects/nulls | AST diffs aligned with the window. |
| `files_touched` | list of strings | Union of files touched across the window. |
| `compile_status_sequence` | list of enums | Compile statuses aligned with the window. |
| `test_status_sequence` | list of objects/nulls | Test statuses aligned with the window. |

## `controller_state`

Use `controller_state` rows for adaptive forest budget allocation.

| Column | Type | Meaning |
|---|---|---|
| `step_idx` | integer | Forest-level decision step. |
| `forest_state` | object | Numeric state vector summarizing forest progress. |
| `controller_action` | enum | Action label for the controller. |
| `action_rationale` | string or null | Optional rationale for the label. |
| `outcome` | object | Downstream outcome after the controller decision. |

Allowed `controller_action` values:

| Action | Meaning |
|---|---|
| `STOP_AND_SUBMIT` | Stop sampling and submit the current best result. |
| `SPAWN_MORE_WORKERS` | Increase forest width. |
| `DEEPEN_TOP_BRANCH` | Spend more steps on the best current branch. |
| `DIVERSIFY_PROMPT` | Push workers toward different reasoning/search directions. |
| `RUN_VERIFIER` | Run verification or grading-oriented checks. |
| `SWITCH_TO_PATCH_MODE` | Move from detect-style work into patch generation. |

## Null Semantics

Null has a precise meaning. It means unavailable, not attempted, or not
applicable. It does not mean false.

| Field Pattern | Correct Null Meaning |
|---|---|
| `terminal_success: null` | The branch has not been graded, or no terminal label was produced. |
| `terminal_score: null` | No terminal score exists yet. |
| `step_reward: null` | Offline reward labeling has not run. |
| `prefix_value: null` | Offline value labeling has not run. |
| `test_status: null` | Tests were not run. |
| `solidity_ast_diff: null` | No code edit occurred or AST extraction was unavailable. |
| `unified_diff: null` | No diff exists for this step. |
| `patch_applied: null` | Patch mode did not run. Do not use `false` for detect-only data. |
| `exploit_reproduced: null` | Exploit mode did not run. Do not use `false` for detect-only data. |

Use explicit enum values when the state is known:

| Field | Value | Meaning |
|---|---|---|
| `compile_status` | `not_attempted` | Compilation was explicitly not run. |
| `compile_status` | `unknown` | Compilation status cannot be determined from artifacts. |
| `compile_status` | `pass` | Compilation ran and passed. |
| `compile_status` | `fail` | Compilation ran and failed. |

## Mapping From Phase 6 Output

The schema is designed to sit downstream of Phase 6. A future extractor should
read Phase 6 outputs and emit schema rows.

| Phase 6 Artifact | Where It Comes From | Dataset Fields It Can Populate |
|---|---|---|
| `phase6-run-matrix.json` | Phase 6 plan/run matrix. | `experiment`, `task_id`, `mode`, `run_group_id`, split metadata. |
| `_phase6_command_logs/<runner>/<audit>.json` | Command status written after each run. | Runtime, failure status, command provenance. |
| `run.log` grade event | EVMBench grader output. | `terminal_success`, `terminal_score`, `detected_vulnerability_ids`, branch/run outcome labels. |
| `logs/**/*.traj.json` | mini-swe-agent worker trajectories. | `decision_point` rows: step index, action, observation, history, token/cost data where available. |
| `modal/logs/modal-forest-result.json` | Modal forest runner metadata. | `branch_summary`, forest worker ids, roles, branches, runtime, errors, trajectory paths. |
| `submission/audit.md` | Final global judge or agent submission. | Branch/run artifacts, final detect evidence after extraction. |
| `phase6-results.json` | Phase 6 summarized rows. | Run-level labels and convenience metadata for branch summaries. |
| `phase6-slide-data.json` | Reporting summary. | Aggregate forest/controller features, useful for diagnostics but not a substitute for row-level traces. |

## Example Dataset Flow

```text
runs/phase6/<timestamp>/
  phase6-run-matrix.json
  phase6-results.json
  modal-forest/group/<audit>_<id>/
    run.log
    logs/**/*.traj.json
    modal/logs/modal-forest-result.json
    submission/audit.md
        |
        v
  extractor
        |
        v
  forest_trace_evm_scaling_v0.jsonl
  forest_branch_summaries_v0.jsonl
  forest_pref_evm_v0.jsonl
  macro_prm_v0.jsonl
  controller_state_v0.jsonl
```

## Canonical Split Manifest

The reduced first20 split is frozen in `dataset_manifest.py` and serialized in
`schema_examples/train_eval_split_manifest.json`.

| Split | Audits | Purpose |
|---|---:|---|
| `train` | first 15 audits from the first20 ordering | Model fitting. |
| `eval` | next 5 audits from the first20 ordering | Held-out evaluation during development. |

Rows should set `provenance.split` to the split that contains their `audit_id`.

## Public-Safe Redaction

Before publishing a dataset:

| Rule | Reason |
|---|---|
| Strip canary lines. | Avoid publishing benchmark/private canaries. |
| Remove API keys, Modal secrets, GitHub tokens, and registry credentials. | Prevent credential leaks. |
| Redact local usernames and host paths. | Avoid leaking local machine details. |
| Keep audit ids, repo-relative paths, command names, model ids, image tags, and grading scores. | Preserve reproducibility. |
| Truncate unrelated dependency logs. | Reduce noise while preserving grading evidence. |

## Validation Commands

Run the focused schema test suite:

```bash
cd project/evmbench
uv run pytest tests/test_trace_schema.py
```

Validate all checked-in examples manually:

```bash
cd project/evmbench
uv run python -c 'from pathlib import Path
from evmbench.experiments.trace_schema import validate_artifact
for path in sorted(Path("evmbench/experiments/schema_examples").glob("*.json")):
    artifact = validate_artifact(path)
    print(path.name, artifact.get("row_type", artifact.get("manifest_type")))
'
```

Validate a produced JSONL artifact:

```python
from pathlib import Path
from evmbench.experiments.trace_schema import validate_artifact

rows = validate_artifact(Path("forest_trace_evm_scaling_v0.jsonl"))
print(f"validated {len(rows)} rows")
```

## Practical Exporter Guidance

An exporter should do four things:

1. Read raw Phase 6 artifacts without mutating them.
2. Normalize them into one of the supported row types.
3. Fill unavailable detect-only patch/exploit fields with `null`, not `false`.
4. Call `validate_artifact()` or `validate_row()` before writing/publishing rows.

If a needed field cannot be produced reliably, use a documented nullable field or
put exporter-specific metadata under `extensions`. Do not invent top-level fields
without a schema version change.
