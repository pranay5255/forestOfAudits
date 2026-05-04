# vLLM Modal Endpoint Runbook

This file is only for deploying, verifying, inspecting, and stopping the
OpenAI-compatible vLLM inference endpoint on Modal.

Use `docs/PHASE6_MODULAR_DEBUG_RUNS.md` for Phase 6 runner commands after the
endpoint is healthy. Use `docs/EXTRACT_FOREST_TRACES_PLAN.md` for converting
completed Phase 6 forest artifacts into dataset rows.

## Endpoint Profile

Phase 6 compatible dual-H100 profile:

```text
gpu=H100:2
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=2
max_model_len=65536
max_num_seqs=2
dtype=auto
mtp=disabled
tool_call_parser=qwen3_coder
scaledown_window_seconds=1800
```

The dual-H100 profile is intentionally guarded. Always pass both
`VLLM_ALLOW_EXPENSIVE_GPU=1` and `--allow-expensive-gpu` when deploying it.

## Create Env And Secret

Run this once, or again when rotating the local vLLM API key or changing the
endpoint profile.

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --gpu H100:2 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 \
  --served-model-name Qwen/Qwen3.6-35B-A3B-FP8 \
  --tensor-parallel-size 2 \
  --max-model-len 65536 \
  --max-num-seqs 2 \
  --gpu-memory-utilization 0.94 \
  --dtype auto \
  --no-enable-mtp \
  --tool-call-parser qwen3_coder \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 1800
```

To rotate the endpoint API key:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --rotate-api-key
```

To sync the current `.env` key into the Modal secret without rewriting `.env`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --no-write-env
```

## Deploy And Verify

Deploy the dual-H100 64k endpoint and write the resolved endpoint URL back to
`.env`:

```bash
VLLM_ALLOW_EXPENSIVE_GPU=1 \
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --sync-secret \
  --write-env \
  --gpu H100:2 \
  --allow-expensive-gpu \
  --model Qwen/Qwen3.6-35B-A3B-FP8 \
  --served-model-name Qwen/Qwen3.6-35B-A3B-FP8 \
  --tensor-parallel-size 2 \
  --max-model-len 65536 \
  --max-num-seqs 2 \
  --gpu-memory-utilization 0.94 \
  --dtype auto \
  --no-enable-mtp \
  --tool-call-parser qwen3_coder \
  --scaledown-window-seconds 1800 \
  --startup-timeout-seconds 1200 \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

Verify an existing deployment without redeploying:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --skip-deploy \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

Confirm `.env` has the values Phase 6 expects:

```bash
rg -n 'VLLM_API_BASE|VLLM_API_KEY|VLLM_MODEL|VLLM_SERVED_MODEL_NAME|VLLM_LITELLM_MODEL|MODEL_KWARGS_JSON|MSWEA_COST_TRACKING|VLLM_MODAL_GPU|VLLM_TENSOR_PARALLEL_SIZE|VLLM_MAX_MODEL_LEN|VLLM_MAX_NUM_SEQS|VLLM_SCALEDOWN_WINDOW_SECONDS' .env
```

Expected profile values:

```text
VLLM_MODAL_GPU="H100:2"
VLLM_TENSOR_PARALLEL_SIZE="2"
VLLM_MAX_MODEL_LEN="65536"
VLLM_MAX_NUM_SEQS="2"
VLLM_SCALEDOWN_WINDOW_SECONDS="1800"
MODEL_KWARGS_JSON="{\"drop_params\":true}"
MSWEA_COST_TRACKING="ignore_errors"
```

## Manual API Checks

Load the endpoint settings:

```bash
set -a
. ./.env
set +a
```

Check Modal health with one long request. Avoid repeated health polling during a
cold start because each request can create another Modal function call.

```bash
server_root="${VLLM_API_BASE%/v1}"

curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 1200 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --write-out "\nhttp_code=%{http_code} time_total=%{time_total}\n" \
  "${server_root}/health"
```

Check model listing:

```bash
curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 300 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --write-out "\nhttp_code=%{http_code} time_total=%{time_total}\n" \
  "${VLLM_API_BASE}/models"
```

Check chat completions:

```bash
curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 600 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --header "Content-Type: application/json" \
  --data "{
    \"model\":\"${VLLM_SERVED_MODEL_NAME}\",
    \"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: smoke-ok\"}],
    \"max_tokens\":16,
    \"temperature\":0,
    \"chat_template_kwargs\":{\"enable_thinking\":false}
  }" \
  --write-out "\nhttp_code=%{http_code} time_total=%{time_total}\n" \
  "${VLLM_API_BASE}/chat/completions"
```

Check OpenAI-compatible tool calls before running a forest job:

```bash
python - <<'PY' >/tmp/vllm-tool-test.json
import json
import os

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

curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 600 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --header "Content-Type: application/json" \
  --data @/tmp/vllm-tool-test.json \
  "${VLLM_API_BASE%/}/chat/completions" \
  | jq '.choices[0].message.tool_calls'
```

## Logs And Stop

Tail endpoint logs:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

Inspect running Modal state:

```bash
modal app list
modal container list
```

Stop the endpoint when done:

```bash
modal app stop evmbench-vllm-qwen
```

If Modal shows only an app id:

```bash
modal app stop ap-xxxxxxxxxxxxxxxxxxxxxx
```

Check same-day billing:

```bash
modal billing report --for today --resolution h --tz Asia/Kolkata
```

## Dual-H100 Import Error

If deployment fails with:

```text
RuntimeError: Refusing to configure expensive Modal GPU 'H100:2'.
Set VLLM_ALLOW_EXPENSIVE_GPU=1 to deploy B200/H200 or multi-GPU vLLM servers.
```

rerun the deploy command above. The opt-in must be present when Modal imports
`/root/deploy_vllm.py`, not just when Phase 6 later calls the endpoint.

Do not commit `.env`; it contains the endpoint URL and API key.
