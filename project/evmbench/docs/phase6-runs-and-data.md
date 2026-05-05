# Phase 6 Runs And Data

This guide covers Phase 6 execution after infrastructure is ready: debug
ladders, mini-swe-agent forest runs, OpenCode vLLM runs, promotion rules, raw
artifact preservation, and extraction readiness.

Use [infrastructure-and-vllm.md](infrastructure-and-vllm.md) first when the
Modal vLLM endpoint is not already verified.

## Core Principles

- Run one audit until the runner is stable.
- Use one runner per output root while debugging.
- Set `PHASE6_ITEM_TIMEOUT_SECONDS` so hangs become analyzable timeout rows.
- Keep `--stop-on-failure` on for debug runs.
- Promote only after the previous step writes `phase6-results.json`,
  `phase6-summary.md`, and a non-empty `submission/audit.md` when a submission
  is expected.
- Do not promote when raw trajectory integrity is incomplete.

The scale-up rule is:

```text
Do not promote unless the previous run has a non-empty submission, complete
trajectory integrity, and no unexplained failure reason.
```

## Success Gates

A Modal forest run is usable for study only when all of these are true:

- `submission/<artifact>` exists and is non-empty for the mode.
- `modal/logs/forest/trajectory-manifest.json` exists.
- `expected_trajectory_count == found_trajectory_count`.
- `missing_trajectory_count == 0`.
- `phase6-results.json` has no `failure_reason` for the row.
- `run.log` contains a parseable grade event when grading is expected.

If any trajectory is missing, keep the run as failure data but exclude it from
training or quality comparisons.

## Preflight

From the repository root:

```bash
set -a
. ./.env
set +a

test -n "${VLLM_API_BASE:-}" && test -n "${VLLM_API_KEY:-}"
test -n "${MODAL_AUDIT_IMAGE_REPO:-}"
uv run modal profile current
```

For vLLM-backed runs, run the chat and tool-call checks in
[infrastructure-and-vllm.md](infrastructure-and-vllm.md). A passing `/health`
request is not enough because mini-swe-agent requires OpenAI-compatible tool
calls.

Preview available variants:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh variants
```

Preview a specific plan before launching:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug
```

## Mini-Swe-Agent Debug Ladder

Use this ladder to debug Modal and forest runs before launching a broad
`first5` matrix.

### 1. Modal Baseline Smoke

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=1800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-baseline-smoke-10 \
  --output-root runs/phase6/debug-baseline-smoke-canto \
  --stop-on-failure
```

This validates image pull, Modal sandbox startup, secret wiring, output
copy-back, and Phase 6 summarization without testing audit quality.

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

This validates the real GPT-5.2 Codex forest path with the `token-flow` and
`accounting` roles.

### 4. Qwen vLLM 2-Tree Debug

Required `.env` values:

| Variable | Purpose |
| --- | --- |
| `VLLM_API_BASE` | OpenAI-compatible vLLM `/v1` endpoint. |
| `VLLM_API_KEY` | Token accepted by the vLLM auth layer. |
| `VLLM_SERVED_MODEL_NAME` | Exact served model from `/v1/models`. |
| `VLLM_LITELLM_MODEL` | LiteLLM model name, usually `openai/$VLLM_SERVED_MODEL_NAME`. |
| `MODEL_KWARGS_JSON` | Drops OpenAI-only params before vLLM calls, usually `{"drop_params":true}`. |
| `MSWEA_COST_TRACKING` | Avoids cost lookup failures for self-hosted Qwen, usually `ignore_errors`. |
| `VLLM_SCALEDOWN_WINDOW_SECONDS` | Keeps the vLLM app warm during Modal audit setup. |

Run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto \
  --stop-on-failure
```

Worker concurrency is set to 1 so the endpoint is not hit by multiple
mini-swe-agent loops while debugging wiring.

### 5. Qwen vLLM 4-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-4trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-4tree-canto \
  --stop-on-failure
```

This adds `access-control` and `cross-contract` and raises worker concurrency
to 2. Move to the full `modal-forest-qwen-vllm` runner only after this writes
`modal/logs/modal-forest-result.json` and either a submission or a clear
row-level failure.

### 6. GPT-5.2 Codex 4-Tree Debug

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-gpt52-codex-4trees-debug \
  --output-root runs/phase6/debug-gpt52-4tree-canto \
  --stop-on-failure
```

Use this to distinguish role/context problems from general Modal instability.

### 7. Single-Audit Full 8-Tree

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

### 8. First5 Promotion

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

## Overnight Scale-Up

For the live server, use a long per-audit timeout and run inside `tmux` so the
SSH session can disconnect without killing the experiment.

```bash
tmux new -s evmbench-phase6
cd /home/experiments_base/forestOfAudits/project/evmbench
set -a
. ./.env
set +a
```

Use `first5` and the 2-tree debug runner for the first overnight run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first5 \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/qwen-vllm-2tree-first5-overnight \
  --stop-on-failure
```

`PHASE6_ITEM_TIMEOUT_SECONDS=14400` is a four-hour cap per audit item. With
`first5`, worst-case wall time is about 20 hours because the Phase 6 launcher
runs the matrix sequentially.

If the 2-tree multi-audit run passes, promote to 4-tree first5 on a later
overnight run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=18000 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first5 \
  --runners modal-forest-qwen-vllm-4trees-debug \
  --output-root runs/phase6/qwen-vllm-4tree-first5-overnight \
  --stop-on-failure
```

Do not use the full `modal-forest-qwen-vllm` runner until the debug runner has
complete artifacts over multiple audits.

## Monitoring

Tail the vLLM server:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

Tail latest Phase 6 command logs:

```bash
tail -f runs/phase6/qwen-vllm-2tree-first5-overnight/_phase6_command_logs/*/*.stdout.log
```

Quick host-side status:

```bash
ps -ef | rg 'evaluate_phase6.py run|evmbench.nano.entrypoint|entrypoint.py forest'
du -sh runs/phase6/qwen-vllm-2tree-first5-overnight
find runs/phase6/qwen-vllm-2tree-first5-overnight -name trajectory-manifest.json -print
```

When another Phase 6 run is active, do not stop the shared `swe-rex` Modal app
globally. Stop only the local process tree or the specific sandbox you own.

## OpenCode vLLM Runs

OpenCode vLLM runs use the same endpoint but a separate agent integration:

```text
evmbench/agents/opencode/start.sh
evmbench/agents/opencode/config.yaml
```

Do not treat `deploy_vllm_server.py` or `deploy_vllm.py` as the agent runner;
they are shared endpoint infrastructure.

Required environment:

```bash
VLLM_API_BASE=https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
VLLM_API_KEY=<redacted>
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B-FP8
MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
OPENCODE_PROVIDER_ID=vllm
```

`start.sh` strips the `openai/` prefix, writes
`$AGENT_DIR/opencode.json`, and runs:

```bash
opencode run \
  --model vllm/Qwen/Qwen3.6-35B-A3B-FP8 \
  --format json \
  "$PROMPT"
```

The generated provider uses environment references, not inline secrets:

```json
{
  "provider": {
    "vllm": {
      "npm": "@ai-sdk/openai-compatible",
      "options": {
        "baseURL": "{env:VLLM_API_BASE}",
        "apiKey": "{env:VLLM_API_KEY}",
        "timeout": 600000
      }
    }
  }
}
```

For OpenCode, keep the vLLM output cap below the deployed context window. The
OpenRouter-era cap of `1000000` causes vLLM to reject requests. Current
OpenCode vLLM settings:

```text
OPENCODE_PROVIDER_ID=vllm
OPENCODE_VLLM_OUTPUT_TOKEN_MAX=4096
OPENCODE_AGENT_TIMEOUT_SECONDS=540
```

Model switching should update these together:

```bash
export VLLM_MODEL=<new-hf-checkpoint>
export VLLM_SERVED_MODEL_NAME=<new-served-name>
export MODEL="openai/$VLLM_SERVED_MODEL_NAME"
```

Then redeploy the endpoint and rerun an OpenCode smoke before launching an
audit.

### OpenCode Ladder

1. Verify endpoint health, `/v1/models`, chat, and tool calls.
2. Validate that `opencode/start.sh` writes an OpenAI-compatible `vllm`
   provider config and invokes `opencode run --model vllm/<served-model>`.
3. Run a single audit:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners opencode-qwen-vllm \
  --output-root runs/phase6/opencode-vllm-debug-canto \
  --stop-on-failure
```

4. If OpenCode starts but times out, rerun with `PHASE6_ITEM_TIMEOUT_SECONDS=10800`.
5. Promote to `first5` only after a single audit produces analyzable logs and,
   ideally, a non-empty submission:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --scope first5 \
  --runners opencode-qwen-vllm \
  --output-root runs/phase6/opencode-vllm-first5 \
  --stop-on-failure
```

As of 2026-04-29, an OpenCode Modal vLLM check reached vLLM and wrote a
complete one-event trajectory manifest, but failed later with:

```text
AI_InvalidResponseDataError: Expected 'function.name' to be a string.
```

Treat this as a tool-call compatibility failure until OpenCode runs past
repeated tool calls and writes a non-placeholder `submission/audit.md`.

## Failure Interpretation

- `command timed out after ...`: the wrapper killed a hung matrix item and
  wrote a command status row.
- `missing or empty submission/audit.md`: the runner completed or produced
  metadata but did not copy back a final report.
- `grade not found in run.log`: a submission exists, but grading did not finish
  or did not emit a parseable grade.
- `forest_worker_errors` populated: inspect
  `modal/logs/modal-forest-result.json` and the referenced `*.traj.json` files.
- `Missing OPENROUTER_API_KEY`: OpenCode did not enter the vLLM branch; check
  `VLLM_API_BASE` propagation.
- `Set VLLM_API_KEY when VLLM_API_BASE is set`: endpoint URL propagated but the
  token did not.
- `model not found`: `MODEL`, `VLLM_SERVED_MODEL_NAME`, or
  `OPENCODE_MODEL_ID` does not match the served model.
- `401` or `403`: token mismatch between `.env`, Modal secret, and deployed
  server. Rotate or sync and redeploy.
- `Couldn't connect to local docker engine`: the audit container never started.
  This is not a vLLM or OpenCode inference failure.

## Artifact Bundle

For every Phase 6 output root, keep the whole directory. The minimum raw bundle
for analysis is:

```text
phase6-run-matrix.json
phase6-results.json
phase6-summary.md
phase6-slide-data.json
_phase6_command_logs/
<runner>/<run-group>/<audit_run>/run.log
<runner>/<run-group>/<audit_run>/submission/
<runner>/<run-group>/<audit_run>/modal/logs/modal-runner-command.json
<runner>/<run-group>/<audit_run>/modal/logs/modal-forest-result.json
<runner>/<run-group>/<audit_run>/modal/logs/forest/trajectory-manifest.json
<runner>/<run-group>/<audit_run>/modal/logs/forest/**/*.traj.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/trajectory-manifest.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/opencode.traj.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/opencode-run.jsonl
<runner>/<run-group>/<audit_run>/modal/forest/
```

Do not edit raw run directories. Put derived data under a sibling `dataset/`,
`analysis/`, or `reports/` directory.

## Inspection Commands

Summarize or re-summarize after every run:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize \
  --output-root runs/phase6/<root>
```

List rows with trajectory integrity:

```bash
jq -r '
  .rows[]
  | [
      .runner,
      .audit_id,
      .submission_exists,
      (.found_trajectory_count // 0),
      (.expected_trajectory_count // 0),
      (.missing_trajectory_count // 0),
      (.failure_reason // "")
    ]
  | @tsv
' runs/phase6/<root>/phase6-results.json
```

Inspect worker-level failures and trajectory hashes:

```bash
jq '
  {
    expected_trajectory_count,
    found_trajectory_count,
    missing_trajectory_count,
    workers: [
      .workers[]
      | {
          worker_name,
          worker_type,
          role,
          branch,
          trajectory_exists,
          trajectory_bytes,
          trajectory_sha256,
          worker_error
        }
    ]
  }
' <run-dir>/modal/logs/forest/trajectory-manifest.json
```

Count assistant actions per trajectory:

```bash
python - <<'PY'
import json
from pathlib import Path

root = Path("runs/phase6/<root>")
for path in sorted(root.glob("**/*.traj.json")):
    data = json.loads(path.read_text())
    actions = 0
    for message in data.get("messages", []):
        extra = message.get("extra") if isinstance(message, dict) else None
        if isinstance(extra, dict):
            actions += len(extra.get("actions") or [])
    print(f"{actions:4d} {path}")
PY
```

## Dataset Extraction

After a Phase 6 root passes the success gates, extract decision and branch rows:

```bash
uv run python -m evmbench.experiments.extract_forest_traces \
  --input-root runs/phase6/<root> \
  --output-dir runs/phase6/<root>/dataset \
  --experiment exp1_forest_scaling \
  --split-manifest evmbench/experiments/schema_examples/train_eval_split_manifest.json
```

Expected derived files:

```text
dataset/forest_trace_evm_scaling_v0.jsonl
dataset/forest_branch_summaries_v0.jsonl
```

Validate derived rows:

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

Use `--continue-on-error` only to debug extractor coverage. It writes
`extract-errors.json` and exits non-zero when any row fails validation.

## Promotion Rules

Promote to the next rung only when:

- Every forest row has complete trajectory integrity.
- At least one representative run produced a non-empty final submission.
- Failure reasons are understood and repeatable, not silent missing artifacts.
- The vLLM or OpenAI provider stayed within rate, timeout, and cost limits.
- Extraction validates on the completed run root.

Do not promote when:

- `missing_trajectory_count > 0`.
- The runner needed manual edits inside raw artifacts.
- The endpoint accepts basic chat but fails tool-call requests.
- Workers mostly end in `LimitsExceeded` before writing required files.
- Phase 6 summary marks a row successful but the manifest contradicts it.

Good promotion path:

```text
2024-01-canto, 2-tree debug
2024-01-canto, 4-tree debug
first5, 2-tree debug
first5, 4-tree debug
first20, locked best debug runner
target split, locked best runner
```

## Run Report Template

Create one short report per scale-up rung:

```markdown
# Run Report: <output-root>

## Configuration

- Date:
- Commit:
- Scope:
- Runners:
- Audits:
- Models:
- vLLM endpoint or OpenAI model:
- Tree roles / branches / concurrency:

## Integrity

- Rows:
- Submissions:
- Failures:
- Expected trajectories:
- Found trajectories:
- Missing trajectories:

## Quality

- Score:
- Detect award:
- Best audit:
- Worst audit:
- Common finding themes:

## Failures

- Missing submissions:
- Worker errors:
- Timeouts:
- Missing or corrupt trajectories:
- Grading failures:

## Decision

- Promote / rerun / stop:
- Required changes before next rung:
```
