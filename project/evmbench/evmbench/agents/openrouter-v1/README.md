# OpenRouter V1 Experiment

This experiment is intentionally self-contained. It registers only these agent
IDs and leaves the default Codex/OpenCode agents unchanged:

- `codex-openrouter-v1`
- `opencode-openrouter-v1`

## Build Images

The runner uses normal EVMBench container agents, so build the local images
described in the repository README before running real tasks.

Print the exact build commands for a mixed task set:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh docker-plan \
  --tasks detect:2024-01-canto,patch:2024-01-curves,exploit:2023-10-nextgen
```

If Docker build networking is flaky, include:

```bash
  --build-network host
```

The printed commands build `ploit-builder:latest`, the EVMBench base image, and
one `evmbench/audit:<audit_id>` image per selected audit.

## Run

Set `OPENROUTER_API_KEY` in `.env` or in the shell. The wrapper loads `.env`.
OpenRouter is the default provider, so existing commands do not need a provider
flag.

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --tasks detect:2024-01-canto,patch:2024-01-curves,exploit:2023-10-nextgen \
  --harnesses codex,opencode \
  --model anthropic/claude-sonnet-4.5 \
  --model google/gemini-3-pro-preview \
  --base-url https://openrouter.ai/api/v1 \
  --output-root runs/openrouter-v1/mixed-3 \
  --agent-timeout-seconds 14400 \
  --stop-on-failure
```

To run direct OpenAI models instead, set `OPENAI_API_KEY` and pass
`--provider openai`. The default base URL becomes `https://api.openai.com/v1`.

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider openai \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --model gpt-5-nano \
  --agent-timeout-seconds 600 \
  --item-timeout-seconds 900 \
  --stop-on-failure
```

For a low-cost startup smoke, use `gpt-5-nano`. For the same task set as the
two-audit detect smoke, keep the model cheap until both wrappers produce
non-empty submissions:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider openai \
  --tasks detect:2024-01-canto,detect:2024-01-curves \
  --harnesses codex,opencode \
  --model gpt-5-nano \
  --output-root runs/openrouter-v1/openai-smoke-gpt-5-nano \
  --agent-timeout-seconds 1800 \
  --item-timeout-seconds 2400
```

Useful direct-OpenAI variants:

```bash
# Cheapest wrapper check.
--provider openai --model gpt-5-nano

# Stronger low-cost check.
--provider openai --model gpt-5-mini

# Current cheaper GPT-5.4-class check.
--provider openai --model gpt-5.4-nano

# Frontier comparison.
--provider openai --model gpt-5.4
```

For OpenRouter, keep the provider prefix in the model slug:

```bash
# Cheap OpenRouter route to OpenAI.
--provider openrouter --model openai/gpt-5-nano

# Stronger OpenRouter route to OpenAI.
--provider openrouter --model openai/gpt-5-mini

# Non-OpenAI OpenRouter sanity check.
--provider openrouter --model anthropic/claude-haiku-4.5
```

Outputs are written under the selected output root:

- `openrouter-v1-matrix.json`
- `openrouter-v1-results.json`
- `openrouter-v1-summary.md`
- `openrouter-v1-results.csv`
- `_command_logs/`
- `_task_results/`
- `evmbench_runs/<run_key>/`
