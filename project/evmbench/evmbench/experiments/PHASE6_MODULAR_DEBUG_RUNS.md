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

## Qwen vLLM Forest Ladder

Use this when the deployed vLLM endpoint is already healthy and you want the
mini-swe-agent forest path to call that endpoint for scouts, branch workers,
tree judges, and the global judge.

Required `.env` values:

| Variable | Purpose | Example |
| --- | --- | --- |
| `VLLM_API_BASE` | OpenAI-compatible vLLM `/v1` endpoint | `https://...modal.run/v1` |
| `VLLM_API_KEY` | Token accepted by the vLLM auth layer | local secret value |
| `VLLM_SERVED_MODEL_NAME` | Exact served model from `/v1/models` | `Qwen/Qwen3.6-35B-A3B-FP8` |
| `VLLM_LITELLM_MODEL` | LiteLLM model name used by mini-swe-agent | `openai/Qwen/Qwen3.6-35B-A3B-FP8` |
| `MODEL_KWARGS_JSON` | Drops OpenAI-only params before vLLM calls | `{"drop_params":true}` |
| `MSWEA_COST_TRACKING` | Avoids cost lookup failures for self-hosted Qwen | `ignore_errors` |
| `VLLM_SCALEDOWN_WINDOW_SECONDS` | Keeps the vLLM app warm during Modal audit setup | `1800` |

Preview the vLLM-specific runners:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh variants | rg 'qwen-vllm|modal-vllm'

evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan \
  --audits 2024-01-canto \
  --runners modal-vllm-debug
```

### Q1. Qwen vLLM 2-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto \
  --stop-on-failure
```

This validates the full forest lifecycle against vLLM with `token-flow` and
`accounting`. Worker concurrency is set to 1 so a single H100 endpoint is not
hit by multiple mini-swe-agent loops while you are debugging wiring.

Check:

```bash
jq '.rows[] | {runner, audit_id, submission_exists, failure_reason, selected_roles, forest_worker_errors}' \
  runs/phase6/debug-qwen-vllm-2tree-canto/phase6-results.json
```

### Q2. Qwen vLLM 4-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-4trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-4tree-canto \
  --stop-on-failure
```

This adds `access-control` and `cross-contract` and raises worker concurrency to
2. Move to the full `modal-forest-qwen-vllm` runner only after this writes
`modal/logs/modal-forest-result.json` and either a submission or a clear
row-level failure.

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
