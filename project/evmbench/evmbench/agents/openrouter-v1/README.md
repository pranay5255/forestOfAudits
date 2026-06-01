# Provider V1 Experiment

This runner keeps the historical `openrouter-v1` path and artifact filenames,
but it now runs EVMBench tasks against four provider modes:

- `openrouter`
- `openai`
- `vllm`
- `azure-foundry`

It registers only these agent IDs and leaves the default Codex/OpenCode agents
unchanged:

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

## Provider Setup

The wrapper always loads `.env` when present. For `--provider azure-foundry`,
it also loads `.env.azure` and aliases the Azure file without rewriting it:

```text
API_KEY -> AZURE_FOUNDRY_API_KEY
PROJ_ENPOINT or PROJ_ENDPOINT -> AZURE_FOUNDRY_BASE_URL
BASE_ENDPOINT -> AZURE_FOUNDRY_PROJECT_ENDPOINT
```

Provider defaults:

| Provider | Required key | Default base URL |
| --- | --- | --- |
| `openrouter` | `OPENROUTER_API_KEY` | `https://openrouter.ai/api/v1` |
| `openai` | `OPENAI_API_KEY` | `https://api.openai.com/v1` |
| `vllm` | `VLLM_API_KEY` | `VLLM_API_BASE` |
| `azure-foundry` | `AZURE_FOUNDRY_API_KEY` | `.env.azure` `PROJ_ENPOINT` |

Azure Foundry should use the `/openai/v1` endpoint in `PROJ_ENPOINT`, not the
project-scoped `BASE_ENDPOINT`.

## Examples

OpenRouter keeps provider-qualified model slugs:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider openrouter \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --model openai/gpt-5-nano \
  --agent-timeout-seconds 600 \
  --item-timeout-seconds 900
```

Direct OpenAI uses unqualified OpenAI model IDs:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider openai \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --model gpt-5-nano \
  --agent-timeout-seconds 600 \
  --item-timeout-seconds 900
```

vLLM defaults its base URL from `VLLM_API_BASE`:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider vllm \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --model Qwen/Qwen3.6-27B \
  --output-root runs/provider-v1/vllm-smoke \
  --agent-timeout-seconds 600 \
  --item-timeout-seconds 900
```

Azure Foundry defaults its smoke model to `gpt-4.1-nano` if `--model` is omitted:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh plan \
  --provider azure-foundry \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --output-root runs/provider-v1/azure-foundry-smoke-plan
```

A real Azure Foundry smoke with both harnesses:

```bash
evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider azure-foundry \
  --tasks detect:2024-01-canto \
  --harnesses codex,opencode \
  --model gpt-4.1-nano \
  --output-root runs/provider-v1/azure-foundry-smoke-gpt-4.1-nano \
  --agent-timeout-seconds 600 \
  --item-timeout-seconds 900 \
  --stop-on-failure
```

## Azure Foundry Smoke

Before running EVMBench, verify the Azure endpoint with the `.env.azure` values:

```bash
. ./.env.azure
curl -sS \
  -H "Authorization: Bearer $API_KEY" \
  "${PROJ_ENPOINT%/}/models"
```

Then verify a minimal Responses request returns non-empty text:

```bash
. ./.env.azure
curl -sS \
  -H "Authorization: Bearer $API_KEY" \
  -H "Content-Type: application/json" \
  "${PROJ_ENPOINT%/}/responses" \
  -d '{"model":"gpt-4.1-nano","input":"Reply with evmbench-ok."}'
```

## Outputs

Outputs are written under the selected output root:

- `openrouter-v1-matrix.json`
- `openrouter-v1-results.json`
- `openrouter-v1-summary.md`
- `openrouter-v1-results.csv`
- `_command_logs/`
- `_task_results/`
- `evmbench_runs/<run_key>/`
