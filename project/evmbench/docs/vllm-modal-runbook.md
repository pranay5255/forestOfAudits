# vLLM Modal Runbook

Use this when starting the self-hosted Qwen vLLM server on Modal and checking it
before running the Modal baseline or forest agents.

## Current Profiles

The default safe profile is single H100 with the FP8 checkpoint:

```text
gpu=H100:1
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=1
dtype=auto
max_model_len=32768
max_num_seqs=8
```

B200 uses the BF16 checkpoint and requires an explicit expensive-GPU opt-in:

```text
gpu=B200
model=Qwen/Qwen3.6-35B-A3B
served_model=Qwen/Qwen3.6-35B-A3B
tensor_parallel_size=1
dtype=bfloat16
max_model_len=32768
max_num_seqs=16
```

Two H100s also use the BF16 checkpoint:

```text
gpu=H100:2
model=Qwen/Qwen3.6-35B-A3B
served_model=Qwen/Qwen3.6-35B-A3B
tensor_parallel_size=2
dtype=bfloat16
max_model_len=32768
max_num_seqs=16
```

## One-Time Env And Secret Setup

Create the local `.env` values and sync the `evmbench-vllm-token` Modal secret
before deploying. Re-run this only when you want to rotate or change the API key
or serving profile.

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 120
```

Raw Modal CLI equivalent:

```bash
if ! grep -q '^VLLM_API_KEY=' .env 2>/dev/null; then
  VLLM_API_KEY="$(openssl rand -hex 32)"
  printf '\nVLLM_API_KEY="%s"\n' "${VLLM_API_KEY}" >> .env
fi

set -a
. ./.env
set +a

secret_args=(VLLM_API_KEY="${VLLM_API_KEY}")
if [ -n "${HF_TOKEN:-}" ]; then
  secret_args+=(HF_TOKEN="${HF_TOKEN}")
fi

modal secret create evmbench-vllm-token --force "${secret_args[@]}"
```

If `.env` does not have `VLLM_API_BASE` yet, deploy once, then resolve the
Modal web URL back into `.env` without touching the Modal secret:

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --no-sync-secret
```

## Deploy And Verify

Recommended first attempt:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 120 \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

The deploy script now reads `.env`, uses the existing `evmbench-vllm-token`
Modal secret for the web endpoint, deploys `evmbench-vllm-qwen`, and checks
`/health`, `/v1/models`, and `/v1/chat/completions`. It does not update the
Modal secret or rewrite `.env` unless you pass explicit flags.

To intentionally rotate the local API key, Modal secret, and `.env` together:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --rotate-api-key \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 120
```

To sync the current `.env` key into the Modal secret without rotating it:

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --no-write-env
```

For B200:

```bash
VLLM_ALLOW_EXPENSIVE_GPU=1 \
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu B200 \
  --model Qwen/Qwen3.6-35B-A3B \
  --served-model-name Qwen/Qwen3.6-35B-A3B \
  --tensor-parallel-size 1 \
  --dtype bfloat16 \
  --max-num-seqs 16 \
  --allow-expensive-gpu \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 120
```

Verify an already deployed server without redeploying:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --skip-deploy \
  --wait-timeout 1800
```

## Manual Smoke

Use a single long-running health request. Do not poll `/health` in a loop while
Modal is cold-starting; repeated requests can create repeated function calls and
restart the model load/compile cycle.

```bash
set -a
. ./.env
set +a

server_root="${VLLM_API_BASE%/v1}"

curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 1200 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --write-out "\nhttp_code=%{http_code} time_total=%{time_total}\n" \
  "${server_root}/health"
```

Then check authenticated chat completions:

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

Also check model listing:

```bash
curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 300 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --write-out "\nhttp_code=%{http_code} time_total=%{time_total}\n" \
  "${VLLM_API_BASE}/models"
```

## Modal Logs

Tail logs in another terminal while the health request is waiting:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

Useful startup milestones from the H100 FP8 run:

```text
Starting vLLM: vllm serve Qwen/Qwen3.6-35B-A3B-FP8 ...
Time spent downloading weights ... ~28s when not already cached
Loading safetensors checkpoint shards ... 42/42
Loading drafter model...
Model loading took ... ~34 GiB memory
torch.compile took ... ~215s on first compile
```

If startup dies immediately with a vLLM CLI usage error, inspect
`deploy_vllm.py` flags first. One previously bad flag was
`--disable-log-requests`; vLLM 0.19.0 rejects it.

## Observed Results

Smoke logs from the 2026-04-29 comparison run are under:

```text
runs/vllm-smoke-comparison/2026-04-29T11-01-11Z/
```

B200:

```text
download-only succeeded for Qwen/Qwen3.6-35B-A3B
serving health did not reach an active container in the observed window
modal container list showed no active evmbench-vllm-qwen container at stop time
```

H100:

```text
download-only attempt was cancelled before completion
serving mode did provision H100 and start vLLM 0.19.0
FP8 weights downloaded and loaded
MTP drafter loaded
torch.compile ran successfully but exceeded the original 600s startup budget
the run was stopped before a completed chat response was recorded
```

Successful H100 retry:

```text
runs/vllm-smoke-comparison/2026-04-29T11-01-11Z/h100-retry-12-17-05/
health ok in 473.5s
/v1/models ok in 0.4s
deploy script chat verification ok in 3.9s
authenticated curl /v1/chat/completions returned HTTP 200 in 1.07s
assistant content: smoke-ok
```

Interpretation: H100 FP8 is the better first target. Use
`--startup-timeout-seconds 1200`, keep the first health check as a single
long-running request, and avoid repeated health polling during cold start.

## Run Modal Baseline

This loads `.env`, verifies the endpoint first, runs one Modal baseline detect
task, and fails unless `submission/audit.md` is non-empty.

```bash
uv run evmbench/agents/mini-swe-agent/run_vllm_modal_baseline.py \
  --audit-id 2024-01-canto \
  --skip-chat-check
```

For the older shell wrapper:

```bash
AUDIT_ID=2024-01-canto \
evmbench/agents/mini-swe-agent/run_vllm_baseline_detect.sh
```

## Run Modal Forest Smoke

Fast forest smoke against the existing endpoint. `VLLM_DEPLOY_MODE=skip` avoids
deploying another GPU server.

```bash
VLLM_DEPLOY_MODE=skip \
AUDIT_ID=2024-01-canto \
evmbench/agents/mini-swe-agent/run_vllm_smoke.sh
```

To exercise the registered Phase 6 vLLM baseline and forest variants:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan \
  --scope smoke \
  --runners vllm

evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --scope smoke \
  --runners vllm \
  --stop-on-failure
```

## Stop And Inspect

Stop the vLLM app when done:

```bash
modal app list
modal app stop evmbench-vllm-qwen
modal container list
```

If an app ID is shown instead of a name, stop that ID directly:

```bash
modal app stop ap-xxxxxxxxxxxxxxxxxxxxxx
```

Billing and state diagnostics:

```bash
modal app logs --timestamps evmbench-vllm-qwen
modal billing report --for today --resolution h --tz Asia/Kolkata
```

Do not commit `.env`; it contains the vLLM endpoint and API key.
