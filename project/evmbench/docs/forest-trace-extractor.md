# Forest Trace Extractor

This guide covers the current extractor for turning mini-swe-agent forest
trajectories plus Phase 6 or Modal metadata into validated dataset JSONL files.
The archived implementation plan is in
[archive/forest-trace-extractor-plan.md](archive/forest-trace-extractor-plan.md).

Run the extractor after a Phase 6 root passes the trajectory-integrity gates in
[phase6-runbook.md](phase6-runbook.md). The extractor never mutates raw run
artifacts.

## Command

```bash
uv run python -m evmbench.experiments.extract_forest_traces \
  --input-root runs/phase6/<root> \
  --output-dir runs/phase6/<root>/dataset \
  --experiment exp1_forest_scaling \
  --split-manifest evmbench/experiments/schema_examples/train_eval_split_manifest.json
```

Required options:

- `--input-root`: a Phase 6 output root, a run group root, or a single run
  directory with forest artifacts.
- `--output-dir`: directory for derived artifacts.

Optional options:

- `--experiment`: dataset experiment label, default `exp1_forest_scaling`.
- `--split-manifest`: maps audit IDs to `train`, `eval`, `test`, `holdout`, or
  `unspecified`.
- `--history-window-size`: number of previous action/observation summaries to
  attach to decision rows, default `8`.
- `--continue-on-error`: write valid rows and an `extract-errors.json` report,
  then exit non-zero if any row failed validation.

## Inputs

The extractor discovers runs from `phase6-results.json`, then
`phase6-run-matrix.json`, then direct `run.log` scans. It reads these files
when present:

| Source | Purpose |
| --- | --- |
| `phase6-results.json` | Score, mode, audit ID, failure reason, and run directory. |
| `phase6-run-matrix.json` | Fallback matrix context when result rows are missing. |
| `modal/logs/modal-forest-result.json` | Worker roles, branches, runtime, errors, and artifact paths. |
| `modal/logs/forest/trajectory-manifest.json` | Expected/found trajectory counts and worker trajectory paths. |
| `modal/logs/forest/**/*.traj.json` | mini-swe-agent messages, actions, observations, costs, and metadata. |
| `run.log` | Fallback grading and terminal event context. |
| `submission/audit.md`, `submission/agent.diff`, `submission/txs.json` | Mode-specific final submissions. |

## Outputs

Every emitted row is validated with `trace_schema.validate_row()` before it is
written.

```text
forest_trace_evm_scaling_v0.jsonl
forest_branch_summaries_v0.jsonl
```

With `--continue-on-error`, validation or extraction failures are also written
to:

```text
extract-errors.json
```

Validate derived artifacts:

```bash
uv run python - <<'PY'
from pathlib import Path
from evmbench.experiments.trace_schema import validate_artifact

for name in (
    "forest_trace_evm_scaling_v0.jsonl",
    "forest_branch_summaries_v0.jsonl",
):
    path = Path("runs/phase6/<root>/dataset") / name
    rows = validate_artifact(path)
    print(name, len(rows))
PY
```

## Row Semantics

`decision_point` rows are emitted for assistant actions in trajectory messages
with `extra.actions[*].command`. The extractor pairs each action with the next
matching tool observation by tool-call ID, carries a bounded history window from
the same branch, and redacts secrets and local host paths before validation.

`branch_summary` rows are emitted for each discovered worker trajectory,
including failed or incomplete workers. Branch rows link back to emitted
decision rows through `decision_row_ids` and preserve extractor-only source
metadata under `extensions.extractor`.

Terminal labels are conservative. Missing Phase 6 score data, failed workers,
or incomplete trajectories leave `terminal_success` and `terminal_score` null
rather than inferring success from placeholders.

## Failure Handling

Default mode fails fast and does not write partial outputs after the first
invalid row or source-artifact error. `--continue-on-error` is for coverage
debugging: it writes all valid rows, records skipped rows in
`extract-errors.json`, and still exits non-zero when errors occurred.
