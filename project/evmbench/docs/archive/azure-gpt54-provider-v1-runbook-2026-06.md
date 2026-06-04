# Azure GPT-5.4 EVMBench Trace Runbook

This is a short operational runbook for running EVMBench tasks with Azure
Foundry `gpt-5.4` through the provider-v1/OpenRouter-v1 runner.

The historical coverage tracker in
[../gpt54-openrouter-runbook.md](../gpt54-openrouter-runbook.md) is direct-OpenAI
only. Azure runs should be treated as provider-smoke or provider-comparison
runs and stored under `runs/provider-v1/`.

## 1. Start tmux

Run from the EVMBench repo:

```bash
cd /home/pranay5255/Documents/forestOfAudits/project/evmbench
tmux new -s evmbench-azure-gpt54
```

Detach with `Ctrl-b d`.

Reattach with:

```bash
tmux attach -t evmbench-azure-gpt54
```

## 2. Environment Check

The runner loads `.env` and, for Azure, `.env.azure`.

Expected `.env.azure` fields:

```text
API_KEY=...
PROJ_ENPOINT=https://...openai.azure.com/openai/v1
BASE_ENDPOINT=...
```

The runner maps them to:

```text
AZURE_FOUNDRY_API_KEY
AZURE_FOUNDRY_BASE_URL
AZURE_FOUNDRY_PROJECT_ENDPOINT
```

Optional endpoint check:

```bash
. ./.env.azure
curl -sS \
  -H "Authorization: Bearer $API_KEY" \
  "${PROJ_ENPOINT%/}/models"
```

## 3. Define Tasks

Use the same `mode:audit` format as
[../gpt54-openrouter-runbook.md](../gpt54-openrouter-runbook.md).

Single task:

```bash
TASKS="detect:2024-01-canto"
```

Small group:

```bash
TASKS="detect:2024-01-canto,patch:2024-01-curves,exploit:2023-10-nextgen"
```

## 4. Build Or Verify Images

Preview required Docker image commands:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh docker-plan \
  --tasks "$TASKS"
```

Run the printed build commands before launching the benchmark if the images are
missing.

## 5. Run One Harness At A Time

Use separate roots for Codex and OpenCode so trajectories and failures are easy
to inspect.

```bash
export UV_CACHE_DIR=/tmp/uv-cache
export PROVIDER="azure-foundry"
export MODEL="gpt-5.4"
export STAMP="$(date -u +%Y%m%dT%H%M%SZ)"

export CODEX_ROOT="runs/provider-v1/azure-foundry-gpt-5.4-codex-${STAMP}"
export OPENCODE_ROOT="runs/provider-v1/azure-foundry-gpt-5.4-opencode-${STAMP}"

mkdir -p runs/provider-v1/_launch_logs
```

Codex:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh plan \
  --provider "$PROVIDER" \
  --tasks "$TASKS" \
  --harnesses codex \
  --model "$MODEL" \
  --output-root "$CODEX_ROOT" \
  --agent-timeout-seconds 3600

evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider "$PROVIDER" \
  --tasks "$TASKS" \
  --harnesses codex \
  --model "$MODEL" \
  --output-root "$CODEX_ROOT" \
  --agent-timeout-seconds 3600 \
  --item-timeout-seconds 4500 \
  2>&1 | tee "runs/provider-v1/_launch_logs/${STAMP}-codex.log"
```

OpenCode:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh plan \
  --provider "$PROVIDER" \
  --tasks "$TASKS" \
  --harnesses opencode \
  --model "$MODEL" \
  --output-root "$OPENCODE_ROOT" \
  --agent-timeout-seconds 3600

evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider "$PROVIDER" \
  --tasks "$TASKS" \
  --harnesses opencode \
  --model "$MODEL" \
  --output-root "$OPENCODE_ROOT" \
  --agent-timeout-seconds 3600 \
  --item-timeout-seconds 4500 \
  2>&1 | tee "runs/provider-v1/_launch_logs/${STAMP}-opencode.log"
```

Do not use `--stop-on-failure` when the goal is trace capture.

## 6. Monitor Progress

```bash
find runs/provider-v1 -path '*/_task_results/*.json' -type f -printf '%TY-%Tm-%Td %TH:%TM %p\n' | sort | tail -20
```

Check active processes:

```bash
ps -eo pid,ppid,pgid,stat,etime,cmd | rg 'run_openrouter_v1|evmbench.nano.entrypoint|codex|opencode'
```

## 7. Summarize And Inspect Traces

Regenerate summaries:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh summarize --output-root "$CODEX_ROOT"
evmbench/agents/openrouter-v1/run_openrouter_v1.sh summarize --output-root "$OPENCODE_ROOT"
```

Inspect row status:

```bash
jq -r '
  .rows[] |
  [
    .provider,
    .harness,
    .model,
    .mode,
    .audit_id,
    .submission_exists,
    .score,
    .max_score,
    .found_trajectory_count,
    .expected_trajectory_count,
    .missing_trajectory_count,
    (.failure_reason // "")
  ] | @tsv
' "$CODEX_ROOT/openrouter-v1-results.json"

jq -r '
  .rows[] |
  [
    .provider,
    .harness,
    .model,
    .mode,
    .audit_id,
    .submission_exists,
    .score,
    .max_score,
    .found_trajectory_count,
    .expected_trajectory_count,
    .missing_trajectory_count,
    (.failure_reason // "")
  ] | @tsv
' "$OPENCODE_ROOT/openrouter-v1-results.json"
```

List trajectory files:

```bash
jq -r '
  .rows[] |
  .run_dir as $run_dir |
  .trajectory_paths[]? |
  "\($run_dir)/\(.)"
' "$CODEX_ROOT/openrouter-v1-results.json"

jq -r '
  .rows[] |
  .run_dir as $run_dir |
  .trajectory_paths[]? |
  "\($run_dir)/\(.)"
' "$OPENCODE_ROOT/openrouter-v1-results.json"
```

## 8. Important Artifacts

Each output root should contain:

```text
openrouter-v1-matrix.json
openrouter-v1-results.json
openrouter-v1-results.csv
openrouter-v1-summary.md
_command_logs/
_task_results/
evmbench_runs/
```

Per-task run directories contain:

```text
submission/audit.md
submission/agent.diff
submission/txs.json
logs/codex/trajectory-manifest.json
logs/codex/codex.traj.json
logs/opencode/trajectory-manifest.json
logs/opencode/opencode.traj.json
logs/opencode/opencode-run.jsonl
```

Only some files exist depending on harness, mode, and failure point.
