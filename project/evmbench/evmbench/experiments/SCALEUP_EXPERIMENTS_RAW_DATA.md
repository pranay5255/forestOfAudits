# Scale-Up Experiments And Raw Data Study

Use this guide to move from one-audit debug runs to larger Forest-of-Thought
experiments without losing the raw evidence needed to explain results later.
The main rule is simple: do not trust a run because it produced a submission.
Trust it only after the raw trajectories, manifest, Modal metadata, logs, and
grading rows agree.

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

## Raw Artifacts To Preserve

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
<runner>/<run-group>/<audit_run>/modal/forest/
```

Do not edit raw run directories. Put derived data under a sibling `dataset/`,
`analysis/`, or `reports/` directory.

## Scale-Up Ladder

Use one output root per rung. Promote only when the previous rung passes the
success gates.

| Rung | Scope | Runner | Purpose |
| --- | --- | --- | --- |
| 0 | one audit | direct forest smoke | Validate Modal image, vLLM/OpenAI tool calls, and manifest writing. |
| 1 | one audit | `modal-forest-qwen-vllm-2trees-debug` or GPT equivalent | Validate scout, branches, judges, global judge, and final copy-back. |
| 2 | one audit | 4-tree debug | Add role diversity and moderate concurrency. |
| 3 | one audit | 8-tree or full target runner | Measure full per-audit runtime and trace volume. |
| 4 | `first5` | best debug runner | Find audit-specific instability before broad launch. |
| 5 | `first20` | locked runner | Collect study data. |
| 6 | target split | locked runner | Produce final comparison and extraction inputs. |

Recommended debug command shape:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto \
  --stop-on-failure
```

Summarize or re-summarize after every run:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto
```

## Preflight Checks

Before launching Modal forest runs:

```bash
set -a
. ./.env
set +a

test -n "${VLLM_API_BASE:-}" && test -n "${VLLM_API_KEY:-}"
test -n "${MODAL_AUDIT_IMAGE_REPO:-}"
uv run modal profile current
```

For vLLM-backed runs, verify both normal chat and tool calls. A passing
`/health` request is not enough because mini-swe-agent requires OpenAI-compatible
tool calls.

```bash
python - <<'PY' >/tmp/vllm-tool-test.json
import json, os
model = os.environ.get("VLLM_SERVED_MODEL_NAME") or os.environ["VLLM_MODEL"]
print(json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "Call bash to echo ok."}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }],
    "max_tokens": 64,
    "temperature": 0,
}))
PY

curl --fail --show-error --silent \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --header "Content-Type: application/json" \
  --data @/tmp/vllm-tool-test.json \
  "${VLLM_API_BASE%/}/chat/completions" \
  | jq '.choices[0].message.tool_calls'
```

If this fails with a tool parser error, redeploy the vLLM server with tool
calling enabled before running the forest.

## Study Queries

Run these from `project/evmbench`.

List Phase 6 rows with trajectory integrity:

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

## Result Report Template

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
- Workers mostly end in `LimitsExceeded` before writing their required files.
- Phase 6 summary marks a row successful but the manifest contradicts it.

## Practical Notes From Small Runs

- Use `${MODAL_AUDIT_IMAGE_REPO}:<audit_id>` when the default
  `evmbench/audit:<audit_id>` image is not pullable by Modal.
- For Qwen vLLM, a healthy endpoint still needs tool-call support. The server
  must be launched with auto tool choice and an appropriate Qwen tool parser.
- Small step limits are useful for trajectory capture tests, but they can cause
  every worker to inspect files until `LimitsExceeded` and never write required
  artifacts.
- `--continue-on-worker-error` is useful for collecting partial raw traces
  across later forest stages, but those runs should remain failure data unless
  the global judge writes the final submission and the manifest is complete.
