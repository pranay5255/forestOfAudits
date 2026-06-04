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

The canonical open-model experiment profile is dense Qwen3.6-27B on 8 H100s
with a 524K token context window, Qwen parser/template settings, MTP
speculative decoding, server-side GPU telemetry, and Torch profiler support:

```text
gpu=H100:8
model=Qwen/Qwen3.6-27B
served_model=Qwen/Qwen3.6-27B
tensor_parallel_size=8
max_model_len=524288
max_num_seqs=1
dtype=bfloat16
gpu_memory_utilization=0.94
reasoning_parser=qwen3
tool_call_parser=qwen3_coder
generation_config=vllm
log_stats_interval=5
profile_volume_name=evmbench-vllm-profiles
scaledown_window_seconds=43200
```

This profile is intentionally guarded. Always pass both
`VLLM_ALLOW_EXPENSIVE_GPU=1` and `--allow-expensive-gpu` for multi-GPU Modal
deployments. The detailed model-swap guidance and artifact map live in
`vllmModel/README.md`.

For a full roughly 1M token context, change `--max-model-len` to `1010000` and
change the YaRN `factor` in `--hf-overrides` to `4.0`. For windows at or below
262K, prefer no YaRN unless long-context extrapolation is part of the
experiment.

A smaller single-H100 FP8 profile remains useful for smoke tests:

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
UV_CACHE_DIR=/tmp/uv-cache uv run python -m evmbench.vllm setup-env \
  --gpu H100:8 \
  --model Qwen/Qwen3.6-27B \
  --served-model-name Qwen/Qwen3.6-27B \
  --tensor-parallel-size 8 \
  --max-model-len 524288 \
  --allow-long-max-model-len \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.94 \
  --dtype bfloat16 \
  --tool-call-parser qwen3_coder \
  --reasoning-parser qwen3 \
  --generation-config vllm \
  --enable-gpu-telemetry \
  --enable-torch-profiler \
  --profile-volume-name evmbench-vllm-profiles \
  --log-stats-interval 5 \
  --startup-timeout-seconds 1800 \
  --scaledown-window-seconds 43200
```

To rotate the local vLLM API key:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m evmbench.vllm setup-env \
  --rotate-api-key
```

To sync the current `.env` key into the Modal secret without rewriting `.env`:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m evmbench.vllm setup-env \
  --no-write-env
```

Minimum `.env` values for vLLM-backed open-model runs:

```bash
VLLM_API_BASE=https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
VLLM_API_KEY=<redacted>
VLLM_MODEL=Qwen/Qwen3.6-27B
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.6-27B
VLLM_TOOL_CALL_PARSER=qwen3_coder
VLLM_PROFILE_VOLUME_NAME=evmbench-vllm-profiles
VLLM_ENABLE_GPU_TELEMETRY=1
VLLM_ENABLE_TORCH_PROFILER=1
VLLM_LITELLM_MODEL=openai/Qwen/Qwen3.6-27B
MODEL=openai/Qwen/Qwen3.6-27B
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

### Direct Harness Runs

Run forest-free vLLM experiments through the repo-level runner:

```bash
uv run python -m evmbench.vllm run-harness \
  --harness codex \
  --mode detect \
  --audit-id 2024-01-canto \
  --metrics \
  --metrics-interval-seconds 5 \
  --kernel-profile torch
```

`--harness` accepts `codex`, `opencode`, and `mini-swe-agent`. These map to
`codex-qwen-vllm`, `opencode-qwen-vllm`, and `mini-swe-agent-qwen-vllm` and run
`evmbench.nano.entrypoint` with `runner.concurrency=1`. This is the canonical
path for single-harness open-model experiments that do not need forest
orchestration. It intentionally bypasses `modal_forest`, forest scouts, forest
judges, and Phase 6 forest shell wrappers.

Each run writes `run-manifest.json`, stdout/stderr logs, raw Prometheus metrics,
parsed samples, summaries, poll JSONL, filtered GPU telemetry, Torch profiler
indexes, CUDA summaries when traces are parseable, and
`metrics/metrics-manifest.json`. Nsight Compute and DCGM hardware counters are
heavier optional workflows because they may require extra host tooling and
privileges.

## Deploy And Verify

Deploy the canonical Qwen3.6-27B endpoint and write the resolved endpoint URL
back to `.env`:

```bash
VLLM_ALLOW_EXPENSIVE_GPU=1 \
UV_CACHE_DIR=/tmp/uv-cache uv run python -m evmbench.vllm deploy \
  --sync-secret \
  --write-env \
  --rotate-api-key \
  --allow-expensive-gpu \
  --gpu H100:8 \
  --tensor-parallel-size 8 \
  --model Qwen/Qwen3.6-27B \
  --served-model-name Qwen/Qwen3.6-27B \
  --max-model-len 524288 \
  --allow-long-max-model-len \
  --hf-overrides '{"text_config":{"rope_parameters":{"mrope_interleaved":true,"mrope_section":[11,11,10],"rope_type":"yarn","rope_theta":10000000,"partial_rotary_factor":0.25,"factor":2.0,"original_max_position_embeddings":262144}}}' \
  --max-num-seqs 1 \
  --gpu-memory-utilization 0.94 \
  --dtype bfloat16 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice \
  --tool-call-parser qwen3_coder \
  --language-model-only \
  --enable-mtp \
  --num-speculative-tokens 2 \
  --generation-config vllm \
  --override-generation-config '{"temperature":0.6,"top_p":0.95,"top_k":20,"min_p":0.0,"presence_penalty":0.0,"repetition_penalty":1.0}' \
  --default-chat-template-kwargs '{"enable_thinking":true,"preserve_thinking":true}' \
  --enable-gpu-telemetry \
  --gpu-telemetry-interval-seconds 1 \
  --enable-torch-profiler \
  --profile-volume-name evmbench-vllm-profiles \
  --log-stats-interval 5 \
  --startup-timeout-seconds 1800 \
  --scaledown-window-seconds 43200 \
  --wait-timeout 2400 \
  --request-timeout 300 \
  --chat-timeout 900
```

Verify an existing deployment without redeploying:

```bash
UV_CACHE_DIR=/tmp/uv-cache uv run python -m evmbench.vllm deploy \
  --skip-deploy \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

Check the effective `.env` profile without printing secrets:

```bash
rg -n 'VLLM_API_BASE|VLLM_API_KEY|VLLM_MODEL|VLLM_SERVED_MODEL_NAME|VLLM_LITELLM_MODEL|MODEL_KWARGS_JSON|MSWEA_COST_TRACKING|VLLM_MODAL_GPU|VLLM_TENSOR_PARALLEL_SIZE|VLLM_MAX_MODEL_LEN|VLLM_MAX_NUM_SEQS|VLLM_SCALEDOWN_WINDOW_SECONDS' .env
```

Expected Qwen3.6-27B long-context values:

```text
VLLM_MODAL_GPU="H100:8"
VLLM_TENSOR_PARALLEL_SIZE="8"
VLLM_MAX_MODEL_LEN="524288"
VLLM_MAX_NUM_SEQS="1"
VLLM_ENABLE_GPU_TELEMETRY="1"
VLLM_ENABLE_TORCH_PROFILER="1"
VLLM_PROFILE_VOLUME_NAME="evmbench-vllm-profiles"
VLLM_SCALEDOWN_WINDOW_SECONDS="43200"
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
