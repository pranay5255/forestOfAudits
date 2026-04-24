# Forest/PRM Dataset Schema

This directory freezes the v1 schema contract for Forest-of-Audits PRM artifacts.
All producers must validate rows with `trace_schema.validate_artifact` before publishing.

For a table-driven walkthrough of every row type and how the schema maps to
Phase 6 runner output, see `DATASET_SCHEMA_GUIDE.md`.

## Versioning

- `schema_version` is required on every row and manifest. Current value: `1.0.0`.
- `row_type` is required on every row. Supported values:
  - `decision_point`
  - `branch_summary`
  - `preference_pair`
  - `macro_window`
  - `controller_state`
- Additive or breaking top-level fields require a schema version bump. Exporter-specific data belongs in `extensions`.

## Common Row Fields

Every row must include:

- `schema_version`: semantic version string.
- `row_type`: one of the supported row types above.
- `row_id`: globally stable row identifier, deterministic when possible.
- `experiment`: experiment name, for example `exp1_forest_scaling`.
- `task_id`: `evmbench/<audit_id>`.
- `mode`: one of `detect`, `patch`, `exploit`.
- `provenance`: required provenance bundle.

Required provenance fields:

- `evmbench_commit`: EVMBench repository commit used for the run.
- `split`: one of `train`, `eval`, `test`, `holdout`, `unspecified`.
- `audit_id`: audit id, or `multiple` for manifests spanning audits.
- `run_group_id`: stable run group id from the exporter/evaluator.
- `model`: model identifier used for the row.
- `image_tag`: audit image tag used by the worker.
- `seed`: integer seed, or `null` if no deterministic seed was set.
- `grading_commit`: grader commit, or `null` when grading is bundled with `evmbench_commit`.
- `extractor_version`: extractor implementation version, currently `trace-schema-1.0.0`.

## Null Semantics

Null never means false. It means unavailable, not attempted, or not applicable.

- `terminal_success`, `terminal_score`, `step_reward`, and `prefix_value` are `null` until the relevant branch or offline label pass has been graded.
- `test_status` is `null` when tests were not run. If tests were run, it must include `num_passed`, `num_failed`, and `num_errors`.
- `compile_status` is never `null`; use `not_attempted` when compilation was not run and `unknown` only when the exporter cannot determine the state.
- `solidity_ast_diff` and `unified_diff` are `null` for detect-only decision rows that did not edit code.
- `patch_applied` and `exploit_reproduced` are `null` for detect-only branch summaries. Do not set them to `false` unless the patch or exploit mode actually ran and failed.
- `cost` is required, but individual cost fields may be `null` when unavailable.

## Row Types

### `decision_point`

One row per worker step. It records state, action, observation, optional code/test signals, reward labels, and forest context.

Required fields include `branch_id`, `worker_id`, `step_idx`, `problem_statement`, `history_window`, `candidate_action`, `compile_status`, `cost`, and nullable reward fields.

### `branch_summary`

One row per completed branch. It links to decision rows through `decision_row_ids` and stores terminal outcomes plus branch-level artifacts.

Detect-only exports should fill `detected_vulnerability_ids` when known and leave `patch_applied` and `exploit_reproduced` as `null`.

### `preference_pair`

One row per chosen/rejected branch-prefix comparison. `chosen` and `rejected` must each include `branch_id`, `trace_row_id`, `history_window`, `terminal_score`, `step_reward`, and `prefix_value`.

Pairs should usually compare branches at the same depth. If not, set `same_depth` to `false` and keep `depth` as the comparison depth used by the constructor.

### `macro_window`

One row per fixed-size sequence window for macro PRM training. `state_sequence`, `action_sequence`, `observation_sequence`, `solidity_ast_diffs`, `compile_status_sequence`, and `test_status_sequence` must all have length `window_size`.

### `controller_state`

One row per forest-level controller decision. `forest_state` is the numeric state vector; `controller_action` is one of:

- `STOP_AND_SUBMIT`
- `SPAWN_MORE_WORKERS`
- `DEEPEN_TOP_BRANCH`
- `DIVERSIFY_PROMPT`
- `RUN_VERIFIER`
- `SWITCH_TO_PATCH_MODE`

## Split Manifest

The canonical reduced first20 split is implemented in `dataset_manifest.py` and serialized in `schema_examples/train_eval_split_manifest.json`.

- `train`: first 15 audits from `splits/all.txt`.
- `eval`: next 5 audits from `splits/all.txt`.

Row-level `provenance.split` must agree with the manifest split for that row's `audit_id`.

## Public-Safe Redaction

Public datasets must preserve benchmark reproducibility without leaking secrets.

- Strip canary lines before release.
- Remove API keys, Modal secrets, GitHub tokens, local usernames, host paths, and private registry credentials.
- Keep public benchmark identifiers, audit ids, public repo-relative paths, model ids, image tags, command names, and grading scores.
- Truncate unrelated dependency logs. Keep enough command output to justify grading and PRM labels.

## Dataset Card Metadata

Every manifest must include:

- `license`
- `intended_use`
- `limitations`
- `citation`

The example manifest uses Apache-2.0 and marks the citation as `TBD` until a paper or dataset card exists.
