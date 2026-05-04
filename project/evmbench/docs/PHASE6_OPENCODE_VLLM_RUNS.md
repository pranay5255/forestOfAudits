# Phase 6 OpenCode vLLM Debug Runs

Use this ladder to debug OpenCode runs against the self-hosted Modal vLLM
endpoint before promoting to a broader EVMBench matrix.

This is the OpenCode companion to `PHASE6_MODULAR_DEBUG_RUNS.md`. That document
uses the mini-swe-agent runner. This document uses the same operating
principles, but the agent integration target is:

```text
evmbench/agents/opencode/start.sh
evmbench/agents/opencode/config.yaml
```

The vLLM deployment helper currently lives under `mini-swe-agent` because it is
shared server infrastructure. Do not treat that as the agent runner for this
plan.

## Current Verified State

As of 2026-04-29:

- The Modal vLLM endpoint was deployed on `H100:1`.
- The served model was `Qwen/Qwen3.6-35B-A3B-FP8`.
- Health, `/v1/models`, and chat completion verification passed after cold
  start.
- Three direct OpenAI-compatible calls succeeded:
  `opencode-vllm-ok`, `42`, and `.sol`.
- `evmbench/agents/opencode/start.sh` generated an OpenCode
  `@ai-sdk/openai-compatible` provider named `vllm`.
- The generated OpenCode model route was
  `vllm/Qwen/Qwen3.6-35B-A3B-FP8`.
- A local OpenCode shim exercised the generated config and made three real
  calls through the Modal endpoint:
  `opencode-start-ok`, `audit`, and `.sol`.
- A longer Phase 6 attempt was launched with `opencode-qwen-vllm` and
  `PHASE6_ITEM_TIMEOUT_SECONDS=10800`, but it did not reach OpenCode because
  the local container runner could not connect to Docker in WSL. The failure
  was infrastructure-level, before `opencode/start.sh` was uploaded or run.

The local machine did not have the real `opencode` binary installed during this
smoke. The runner path was still validated by replacing only the binary call
with a shim that consumed `OPENCODE_CONFIG`, `--model`, and the generated
environment exactly as `start.sh` emitted them.

## Principles

- Run one audit until the OpenCode runner is stable.
- Use one runner per output root while debugging.
- Keep the agent target explicit: `opencode-qwen-vllm`.
- Treat `deploy_vllm_server.py` and `deploy_vllm.py` as endpoint setup only.
- Set item-level timeouts so hangs become analyzable rows.
- Keep `--stop-on-failure` on for debug runs.
- Do not require `OPENROUTER_API_KEY` or route through OpenRouter for vLLM runs.
- Do not print API keys. `start.sh` should log key presence and length only.
- Promote only after the previous step writes the expected summary artifacts and
  a non-empty `submission/audit.md` when a submission is expected.

## Runner Prerequisite

`opencode-qwen-vllm` is currently a normal EVMBench `container` runner. The vLLM
server is on Modal, but the benchmark audit container still starts through the
local Docker engine.

Before running steps 3-5, verify Docker from the same shell:

```bash
docker info
```

In WSL, Docker Desktop must have integration enabled for this distro. If Docker
is unavailable, Phase 6 will fail before OpenCode starts, and the output root
will contain only `run.log`, `group.log`, command logs, and a missing
`submission/audit.md`.

## Environment Contract

Required for OpenCode vLLM runs:

```bash
VLLM_API_BASE=https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
VLLM_API_KEY=<redacted>
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B-FP8
MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
OPENCODE_PROVIDER_ID=vllm
```

`start.sh` strips the `openai/` prefix for the OpenCode model id, writes
`$AGENT_DIR/opencode.json`, then runs:

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

## Model Switching

Keep model switching in two layers:

| Layer | Variable | Purpose |
|---|---|---|
| vLLM server | `VLLM_MODEL` | Hugging Face checkpoint loaded by vLLM. |
| vLLM server | `VLLM_SERVED_MODEL_NAME` | OpenAI-compatible model name exposed by `/v1/models`. |
| OpenCode runner | `MODEL` | EVMBench/OpenCode-facing model value, usually `openai/$VLLM_SERVED_MODEL_NAME`. |
| OpenCode runner | `OPENCODE_MODEL_ID` | Optional direct override for the provider-local model id. |
| OpenCode runner | `OPENCODE_MODEL` | Optional full route override, for example `vllm/<model-id>`. |

For the normal case, switch only these together:

```bash
export VLLM_MODEL=<new-hf-checkpoint>
export VLLM_SERVED_MODEL_NAME=<new-served-name>
export MODEL="openai/$VLLM_SERVED_MODEL_NAME"
```

Then redeploy the vLLM endpoint and rerun the OpenCode smoke before launching an
audit.

## Recommended Ladder

### 0. Endpoint Smoke

Deploy or refresh the endpoint with the H100 profile:

```bash
uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --rotate-api-key \
  --sync-secret \
  --write-env \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 120 \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

Expected result:

- `.env` contains the current `VLLM_API_BASE`, `VLLM_API_KEY`, and served model.
- `runs/vllm-server/latest-deploy.json` records the endpoint metadata.
- The deploy wrapper reports health, `/v1/models`, and chat completion success.

### 1. Direct vLLM Calls

Call the endpoint a few times before involving OpenCode:

```bash
uv run python - <<'PY'
import os
from pathlib import Path

import requests

for line in Path(".env").read_text().splitlines():
    if line and "=" in line and not line.startswith("#"):
        key, value = line.split("=", 1)
        os.environ.setdefault(key, value.strip().strip("\"'"))

base = os.environ["VLLM_API_BASE"].rstrip("/")
api_key = os.environ["VLLM_API_KEY"]
model = os.environ.get("VLLM_SERVED_MODEL_NAME") or os.environ["VLLM_MODEL"]
headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}

for prompt in [
    "Reply with exactly: opencode-vllm-ok",
    "Reply with only the number: 17 + 25",
    "Reply with only the file extension for Solidity source files.",
]:
    response = requests.post(
        base + "/chat/completions",
        headers=headers,
        json={
            "model": model,
            "messages": [{"role": "user", "content": prompt}],
            "temperature": 0,
            "max_tokens": 16,
        },
        timeout=120,
    )
    response.raise_for_status()
    print(response.json()["choices"][0]["message"]["content"].strip())
PY
```

Expected result: short deterministic answers from the live endpoint.

### 2. OpenCode Start Script Smoke

Before running a real audit, validate that `start.sh` writes the correct
OpenCode config and selects the vLLM provider route. If the local machine has
the real `opencode` binary, this can be a true CLI smoke. Otherwise, use a shim
that checks `OPENCODE_CONFIG` and calls the endpoint.

Minimum checks:

- `OPENCODE_CONFIG=$AGENT_DIR/opencode.json`.
- Provider id is `vllm`.
- Provider package is `@ai-sdk/openai-compatible`.
- Provider options use `{env:VLLM_API_BASE}` and `{env:VLLM_API_KEY}`.
- The invoked model route is `vllm/$VLLM_SERVED_MODEL_NAME`.
- At least one real chat completion succeeds through that generated route.

### 3. Single-Audit OpenCode vLLM Debug

Run only one audit and one OpenCode runner variant:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners opencode-qwen-vllm \
  --output-root runs/phase6/opencode-vllm-debug-canto \
  --stop-on-failure
```

This still uses the Phase 6 launcher, but the runner id points at
`evmbench/agents/opencode/config.yaml` and `opencode/start.sh`.

Check:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize \
  --output-root runs/phase6/opencode-vllm-debug-canto
```

### 4. Single-Audit Rerun With Longer Timeout

Only if step 3 reaches OpenCode but times out:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners opencode-qwen-vllm \
  --output-root runs/phase6/opencode-vllm-debug-canto-long \
  --stop-on-failure
```

Use this to distinguish model latency or audit workload from config problems.

### 5. First5 Promotion

Promote only after one audit produces analyzable logs and, ideally, a
submission:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --scope first5 \
  --runners opencode-qwen-vllm \
  --output-root runs/phase6/opencode-vllm-first5 \
  --stop-on-failure
```

Move beyond `first5` only after every row has either a readable failure reason
or a non-empty submission.

## Failure Interpretation

- `Missing OPENROUTER_API_KEY`: the run did not enter the vLLM branch of
  `opencode/start.sh`; check `VLLM_API_BASE` propagation.
- `Set VLLM_API_KEY when VLLM_API_BASE is set`: endpoint URL propagated but the
  token did not.
- `model not found`: `MODEL`, `VLLM_SERVED_MODEL_NAME`, or
  `OPENCODE_MODEL_ID` does not match the served vLLM model name.
- `401` or `403`: token mismatch between `.env`, Modal secret, and the deployed
  server. Rotate and redeploy.
- `command timed out after ...`: the Phase 6 wrapper killed the audit item.
  Inspect command logs before increasing the timeout.
- `missing or empty submission/audit.md`: OpenCode ran but did not produce the
  final EVMBench submission.
- `Couldn't connect to local docker engine`: the audit container never started.
  This is not a vLLM or OpenCode inference failure; enable Docker for the WSL
  distro or add a Modal-backed OpenCode runner before rerunning.

## Artifact Checklist

For every OpenCode vLLM debug output root, preserve:

- `phase6-run-matrix.json`
- `phase6-results.json`
- `phase6-summary.md`
- `_phase6_command_logs/opencode-qwen-vllm/<audit>.stdout.log`
- `_phase6_command_logs/opencode-qwen-vllm/<audit>.stderr.log`
- `modal/logs/modal-runner-command.json`
- `logs/debug.log`
- `logs/agent.log`
- `agent/opencode.json` when copied back by the runner
- `submission/audit.md` when present

For the vLLM server itself, preserve:

- `runs/vllm-server/latest-deploy.json`
- Redacted Modal container log excerpts showing startup config, health, and chat
  completion status.

## Validation Commands

Run the focused tests after changing OpenCode or vLLM wiring:

```bash
uv run pytest \
  tests/test_mini_swe_agent_phase5.py::test_registry_loads_opencode_vllm_variant \
  tests/test_mini_swe_agent_phase5.py::test_opencode_start_sh_writes_vllm_openai_compatible_config \
  tests/test_vllm_deploy_safety.py
```

Syntax-check the start script:

```bash
bash -n evmbench/agents/opencode/start.sh
```

## Promotion Checklist

- [ ] H100 endpoint deployed with the intended model.
- [ ] `.env` and Modal secret agree on `VLLM_API_KEY`.
- [ ] Direct endpoint calls succeed more than once.
- [ ] `opencode/start.sh` writes an OpenAI-compatible `vllm` provider config.
- [ ] `opencode/start.sh` invokes `opencode run --model vllm/<served-model>`.
- [ ] A single-audit OpenCode run writes analyzable Phase 6 results.
- [ ] A single-audit OpenCode run writes a non-empty `submission/audit.md`, or
      the failure mode is understood and documented.
- [ ] Only then run `first5`.
