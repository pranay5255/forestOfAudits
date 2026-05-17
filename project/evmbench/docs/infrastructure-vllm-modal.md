# Infrastructure, vLLM, And Modal

This guide covers the infrastructure layer: how EVMBench expects remote compute
to behave, how audit images are named, and how to deploy and verify the
OpenAI-compatible vLLM endpoint on Modal.

Use [phase6-runbook.md](phase6-runbook.md) for actual Phase 6 runner
commands after the endpoint is healthy.

## Compute Scaling Contract

The default EVMBench release runs locally with Docker. Larger sweeps can run on
VMs, Kubernetes, a batch system, or any other fleet as long as the backend can
provide a small "computer" abstraction for container execution, shell commands,
and file transfer.

At a high level, a scaled backend must provide:

- A way to start a container, or a small group of containers, per task.
- A way to execute shell commands inside the main container.
- A way to upload and download small files and directories for prompts, configs,
  logs, and artifacts.
- For exploit mode, an optional sidecar container for the chain plus a way to
  run shell, upload, and download operations in that sidecar.

The required all-mode surface mirrors the methods used by
`ComputerInterface` and `evmbench/alcatraz.py`:

```text
send_shell_command(cmd: str) -> ExecutionResult
check_shell_command(cmd: str) -> ExecutionResult
upload(data: bytes, destination_path: str) -> None
download(source_path: str) -> bytes
```

`send_shell_command` and `check_shell_command` should return or expose an exit
code plus stdout/stderr bytes. Output can be capped, but failures should include
enough tail output to debug. `check_shell_command` can be a wrapper that raises
on non-zero exit.

Exploit mode with a sidecar also needs:

```text
fetch_container_names() -> list[str]
cluster.send_shell_command(cmd, ..., container_id=int)
cluster.upload(data, path, container_id=int)
cluster.download(path, container_id=int)
```

If your backend does not use numeric container IDs, adapt the helper layer to
target containers by stable names. The important invariant is that the agent
container and chain sidecar can be addressed consistently.

## Audit Images

Local audit images are tagged as:

```text
evmbench/audit:<audit_id>
```

For remote workers, set:

```bash
EVMBENCH_AUDIT_IMAGE_REPO=registry.example.com/evmbench/audit
```

The eval will then use:

```text
registry.example.com/evmbench/audit:<audit_id>
```

The build script supports this naming directly:

```bash
uv run docker_build.py \
  --tag-prefix registry.example.com/evmbench/audit \
  --split detect-tasks
```

For the current Modal-backed Phase 6 runs, the commonly used repository is:

```bash
MODAL_AUDIT_IMAGE_REPO=ghcr.io/pranay5255/evmbench-audit
```

## Networking And No-Internet Mode

`EVMbenchSolver.disable_internet=True` attempts to block general outbound
internet access while still allowing model API calls through a gateway sidecar.
The local implementation uses Docker network rewiring.

For scaled deployments, either:

- Implement equivalent isolation in the backend when strict enforcement matters.
- Run with `evmbench.solver.disable_internet=False` if the environment already
  provides isolation or if network enforcement is out of scope.

The gateway image is configurable with:

```bash
EVMBENCH_GATEWAY_IMAGE=haproxy:lts-alpine
```

## vLLM Architecture

mini-swe-agent model calls happen in the host process through
`LitellmModel -> litellm.completion()`. Modal sandboxes execute audit shell
commands. Switching inference to self-hosted vLLM therefore means:

- Deploy an OpenAI-compatible vLLM server.
- Point LiteLLM at the server with `api_base`.
- Use the vLLM API key as the OpenAI-compatible bearer token.
- Drop OpenAI-only parameters when the self-hosted endpoint cannot accept them.

The normal model-name invariant is:

```text
VLLM_SERVED_MODEL_NAME = exact model ID returned by /v1/models
VLLM_LITELLM_MODEL     = openai/$VLLM_SERVED_MODEL_NAME
MODEL                 = openai/$VLLM_SERVED_MODEL_NAME
```

## Endpoint Profiles

The current Phase 6-compatible dual-H100 profile is:

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

A smaller H100 profile has also been used for OpenCode and early smoke tests:

```text
gpu=H100
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
max_model_len=32768
tool_call_parser=qwen3_coder
```

## Configure Env And Secret

Run from the repository root:

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

To rotate the local vLLM API key:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --rotate-api-key
```

To sync the current `.env` key into the Modal secret without rewriting `.env`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --no-write-env
```

Minimum `.env` values for vLLM-backed Phase 6 runs:

```bash
VLLM_API_BASE=https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
VLLM_API_KEY=<redacted>
VLLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B-FP8
VLLM_TOOL_CALL_PARSER=qwen3_coder
VLLM_LITELLM_MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
MODEL_KWARGS_JSON={"drop_params":true}
MSWEA_COST_TRACKING=ignore_errors
MODAL_AUDIT_IMAGE_REPO=ghcr.io/pranay5255/evmbench-audit
```

Use a long scaledown window for overnight work so the endpoint does not cold
start between audit items:

```bash
VLLM_SCALEDOWN_WINDOW_SECONDS=43200
```

Do not commit `.env`; it contains the endpoint URL and API key.

## Deploy And Verify

Deploy the dual-H100 endpoint and write the resolved endpoint URL back to
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

Check the effective `.env` profile without printing secrets:

```bash
rg -n 'VLLM_API_BASE|VLLM_API_KEY|VLLM_MODEL|VLLM_SERVED_MODEL_NAME|VLLM_LITELLM_MODEL|MODEL_KWARGS_JSON|MSWEA_COST_TRACKING|VLLM_MODAL_GPU|VLLM_TENSOR_PARALLEL_SIZE|VLLM_MAX_MODEL_LEN|VLLM_MAX_NUM_SEQS|VLLM_SCALEDOWN_WINDOW_SECONDS' .env
```

Expected dual-H100 values:

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

Load settings:

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

Basic chat is not enough for mini-swe-agent. Verify OpenAI-compatible tool calls
before spending time on forest runs:

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

The response should contain a `tool_calls` entry. If it does not, fix the vLLM
server tool parser before launching Phase 6.

## Logs, Stop, And Billing

Tail endpoint logs:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

Inspect Modal state:

```bash
modal app list
modal container list
```

Stop the endpoint when done:

```bash
modal app stop evmbench-vllm-qwen
```

If Modal shows only an app ID:

```bash
modal app stop ap-xxxxxxxxxxxxxxxxxxxxxx
```

Check same-day billing:

```bash
modal billing report --for today --resolution h --tz Asia/Kolkata
```

## Known Deployment Error

If deployment fails with:

```text
RuntimeError: Refusing to configure expensive Modal GPU 'H100:2'.
Set VLLM_ALLOW_EXPENSIVE_GPU=1 to deploy B200/H200 or multi-GPU vLLM servers.
```

rerun the deploy command with both `VLLM_ALLOW_EXPENSIVE_GPU=1` and
`--allow-expensive-gpu`. The opt-in must be present when Modal imports
`/root/deploy_vllm.py`, not just when Phase 6 later calls the endpoint.
