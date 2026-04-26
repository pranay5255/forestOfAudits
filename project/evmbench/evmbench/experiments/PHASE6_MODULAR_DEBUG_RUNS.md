# Phase 6 Modular Debug Runs

Use this ladder to debug Modal and forest runs before launching a broad
`first5` matrix. Each step should produce a small output root that can be
summarized independently.

## Principles

- Run one audit until the runner is stable.
- Use one runner per output root while debugging.
- Set `PHASE6_ITEM_TIMEOUT_SECONDS` so hangs become analyzable timeout rows.
- Keep `--stop-on-failure` on for debug runs.
- Promote only after the previous step writes `phase6-results.json`,
  `phase6-summary.md`, and a non-empty `submission/audit.md` when the runner is
  expected to produce a submission.

## Recommended Ladder

### 0. List And Preview

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh variants

evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan \
  --audits 2024-01-canto \
  --runners forest-debug
```

Expected result: the plan shows only the selected audit and the debug runners
you intend to launch.

### 1. Modal Baseline Smoke

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=1800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-baseline-smoke-10 \
  --output-root runs/phase6/debug-baseline-smoke-canto \
  --stop-on-failure
```

This validates image pull, Modal sandbox startup, OpenAI secret wiring, output
copy-back, and Phase 6 summarization without testing audit quality.

Check:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize \
  --output-root runs/phase6/debug-baseline-smoke-canto
```

### 2. Forest Smoke

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=1800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-smoke \
  --output-root runs/phase6/debug-forest-smoke-canto \
  --stop-on-failure
```

This validates the forest runner path with tiny budgets and fallback smoke
submissions.

### 3. GPT-5.2 Codex 2-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-gpt52-codex-2trees-debug \
  --output-root runs/phase6/debug-gpt52-2tree-canto \
  --stop-on-failure
```

This validates the real GPT-5.2 Codex forest path with only two explicit roles:
`token-flow` and `accounting`. It runs with worker concurrency 2 and
`FOREST_CONTINUE_ON_WORKER_ERROR=1`, so one worker failure should still leave a
structured forest metadata file.

Check these artifacts first:

```bash
jq '.rows[] | {runner, audit_id, submission_exists, failure_reason, selected_roles, forest_worker_errors}' \
  runs/phase6/debug-gpt52-2tree-canto/phase6-results.json
```

### 4. GPT-5.2 Codex 4-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-gpt52-codex-4trees-debug \
  --output-root runs/phase6/debug-gpt52-4tree-canto \
  --stop-on-failure
```

This adds `access-control` and `cross-contract` while keeping concurrency at 2.
Use it to distinguish role/context problems from general Modal instability.

### 5. Single-Audit Full 8-Tree

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-gpt52-codex-8trees \
  --output-root runs/phase6/debug-gpt52-8tree-canto \
  --stop-on-failure
```

Only run this after the 2-tree and 4-tree variants finish with analyzable
metadata.

### 6. First5 Promotion

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --scope first5 \
  --runners modal-forest-gpt52-codex-4trees-debug \
  --output-root runs/phase6/debug-gpt52-4tree-first5 \
  --stop-on-failure
```

Use the 4-tree debug runner for the first `first5` promotion. Move to the
8-tree runner only after every audit in the 4-tree run produces a readable
row-level failure or a submission.

## Failure Interpretation

- `command timed out after ...`: the wrapper killed a hung matrix item and wrote
  a command status row.
- `missing or empty submission/audit.md`: the runner completed or produced
  metadata but did not copy back a final report.
- `grade not found in run.log`: a submission exists, but grading did not finish
  or did not emit a parseable grade.
- `forest_worker_errors` populated: inspect
  `modal/logs/modal-forest-result.json` and the referenced `*.traj.json` files
  before rerunning.

## Artifact Checklist

For every debug output root, preserve:

- `phase6-run-matrix.json`
- `phase6-results.json`
- `phase6-summary.md`
- `_phase6_command_logs/<runner>/<audit>.stdout.log`
- `_phase6_command_logs/<runner>/<audit>.stderr.log`
- `modal/logs/modal-runner-command.json`
- `modal/logs/modal-forest-result.json` for forest runs
- `modal/logs/forest/**/*.traj.json`
- `submission/audit.md` when present
