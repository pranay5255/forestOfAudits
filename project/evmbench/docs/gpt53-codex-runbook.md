# GPT-5.3 Codex Runbook

Snapshot date: 2026-06-12.

This runbook tracks EVMBench runs for `gpt-5.3-codex` through the
provider-v1/OpenRouter-v1 wrapper. The current matrix is Codex CLI only.
OpenCode rows are intentionally excluded for this phase.

Provider and harness shape:

```text
provider: azure-foundry
model: gpt-5.3-codex
harnesses: codex
env file: .env.codex
coverage key: model + harness + mode + audit
```

`.env.codex` is expected to contain:

```text
ENDPOINT_URI=https://.../openai/v1/responses
API_KEY=...
```

The runner maps `ENDPOINT_URI` to `AZURE_FOUNDRY_BASE_URL` for provider
compatibility, while the Codex matrix launcher keeps `API_KEY` as the key env
with `EVMBENCH_LLM_API_KEY_ENV=API_KEY`. It also normalizes a `/responses`
endpoint down to the provider base URL ending in `/openai/v1`.

## Endpoint Smoke

The endpoint was checked on 2026-06-12 with a minimal Responses request:

```bash
set -a
. ./.env.codex
set +a

curl -sS \
  -o /tmp/evmbench-codex-endpoint-smoke.json \
  -w "%{http_code}\n" \
  -H "Authorization: Bearer ${API_KEY}" \
  -H "Content-Type: application/json" \
  "${ENDPOINT_URI}" \
  -d '{"model":"gpt-5.3-codex","input":"Reply with evmbench-ok."}'
```

Observed result:

```text
HTTP status: 200
response status: completed
output items: 1
```

Do not commit the response file; it is a local smoke artifact under `/tmp`.

## Codex-Only Launcher

Use the helper script for planned chunks:

```bash
scripts/run_codex_gpt53_codex_matrix.sh <chunk> [run|plan]
```

Defaults:

```text
CODEX_ENV_FILE=.env.codex
CODEX_API_KEY_ENV_VAR=API_KEY
CODEX_MODEL=gpt-5.3-codex
CODEX_PROVIDER=azure-foundry
CODEX_AGENT_TIMEOUT_SECONDS=3600
CODEX_ITEM_TIMEOUT_SECONDS=4500
CODEX_JUDGE_WIRE_API=responses
```

The `smoke` chunk uses shorter defaults: `1200` seconds for the EVMBench solver
and `1800` seconds for the wrapper item timeout.

Keep `CODEX_JUDGE_WIRE_API=responses` for this endpoint. The deployment accepts
the Responses API for `gpt-5.3-codex`; a chat-completions smoke returned
`400` with `The requested operation is unsupported.`

Always plan before spending tokens:

```bash
scripts/run_codex_gpt53_codex_matrix.sh smoke plan
scripts/run_codex_gpt53_codex_matrix.sh detect-1 plan
```

Run the first EVMBench task:

```bash
scripts/run_codex_gpt53_codex_matrix.sh smoke
```

The smoke task is:

```text
detect:2024-05-loop
```

Validated smoke run on 2026-06-12:

```text
output root: runs/provider-v1/azure-foundry-gpt-5.3-codex-codexcli-smoke-20260612T140924Z
task: detect:2024-05-loop
return code: 0
runtime: 68.8s
submission: yes, non-empty
trace: 1/1 expected trajectories captured
missing trajectories: 0
score: 0/1
failure_reason: null
```

Trace extraction for that run completed with `conversation_count=1` and
`error_count=0`:

```bash
uv run python -m evmbench.experiments.extract_provider_v1_traces \
  --input-root runs/provider-v1/azure-foundry-gpt-5.3-codex-codexcli-smoke-20260612T140924Z \
  --output-dir runs/provider-v1/azure-foundry-gpt-5.3-codex-codexcli-smoke-20260612T140924Z/provider-v1-traces \
  --experiment gpt53_codex_provider_v1
```

The initial smoke attempt reached the agent trace stage but exposed a local
Responses-judge parsing issue: reasoning models can emit an empty reasoning
message before the structured JSON answer. The detect grader now scans for the
first parseable `JudgeResult` instead of assuming `output_messages[0]`.

## Matrix

The full Codex-only matrix mirrors the GPT-5.4 task set without OpenCode:

| Chunk | Rows | Mode | Notes |
| --- | ---: | --- | --- |
| `smoke` | 1 | detect | First paid EVMBench check: `2024-05-loop`. |
| `detect-1` | 14 | detect | First detect tranche. |
| `detect-2` | 13 | detect | Middle detect tranche. |
| `detect-3` | 13 | detect | Final detect tranche. |
| `patch-1` | 11 | patch | First patch half. |
| `patch-2` | 11 | patch | Final patch half. |
| `exploit-1` | 8 | exploit | First exploit half. |
| `exploit-2` | 8 | exploit | Final exploit half. |
| **Total** | **78** |  | Codex CLI only. |

The output roots are created under:

```text
runs/provider-v1/azure-foundry-gpt-5.3-codex-codexcli-<chunk>-<timestamp>
```

Use one output root per chunk. Do not append reruns into an incomplete root;
create a fresh root and compare with `_task_results`.

## Trace And Log Capture

Each run root should contain:

```text
openrouter-v1-matrix.json
openrouter-v1-results.json
openrouter-v1-results.csv
openrouter-v1-summary.md
_command_logs/
_task_results/
evmbench_runs/
```

For each Codex task, inspect:

```text
<run_dir>/run.log
<run_dir>/submission/audit.md
<run_dir>/submission/agent.diff
<run_dir>/submission/txs.json
<run_dir>/logs/agent.log
<run_dir>/logs/debug.log
<run_dir>/logs/codex/codex-run.jsonl
<run_dir>/logs/codex/codex-stderr.log
<run_dir>/logs/codex/codex-last-message.txt
<run_dir>/logs/codex/codex.traj.json
<run_dir>/logs/codex/trajectory-manifest.json
```

The trajectory integrity gates are:

```text
trajectory-manifest.json exists
expected_trajectory_count == found_trajectory_count
missing_trajectory_count == 0
submission_* artifact exists and is non-empty for the mode
grade is present in run.log
```

Extract provider-v1 conversations after a chunk completes:

```bash
uv run python -m evmbench.experiments.extract_provider_v1_traces \
  --input-root <output-root> \
  --output-dir <output-root>/provider-v1-traces \
  --experiment gpt53_codex_provider_v1
```

This writes:

```text
provider_v1_conversations_v0.jsonl
provider_v1_raw_manifest.json
extract-errors.json    # only when extraction errors occur
```

## Monitoring

Long chunks should run in `tmux`:

```bash
tmux new -s evmbench-gpt53-codex-detect-1
scripts/run_codex_gpt53_codex_matrix.sh detect-1
```

Detach with `Ctrl-b d`, then reattach:

```bash
tmux attach -t evmbench-gpt53-codex-detect-1
```

Check progress from another shell:

```bash
find runs/provider-v1 -path '*/_task_results/*.json' -type f -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort
ps -eo pid,ppid,pgid,stat,etime,cmd | rg 'run_openrouter_v1|evmbench.nano.entrypoint|codex-openrouter'
```

Summarize an existing root:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh summarize \
  --output-root <output-root>
```

## Promotion Rule

Mark a cell complete only when a matching `_task_results/*.json` row exists and
the row has:

```text
provider=azure-foundry
model=gpt-5.3-codex
harness=codex
failure_reason=null
```

Rows with a non-empty `failure_reason` are terminal execution data, but should
be reviewed before being used as quality datapoints.
