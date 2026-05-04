# vLLM Modal Runbook

Use this when starting the self-hosted Qwen vLLM server on Modal and running the
Modal forest agent end to end. The target outcome is a completed detect run with
a non-empty `submission/audit.md` and a complete trajectory manifest.

## Full-Run Objective

For the `2024-01-canto` full forest attempt, use:

```text
endpoint: 64k vLLM context
runner: modal-forest-qwen-vllm-4trees-debug
audit: 2024-01-canto
mode: detect
required final artifact: <output-dir>/submission/audit.md
required trace artifact: <output-dir>/logs/forest/trajectory-manifest.json
```

The previous 32k run completed worker execution and captured all 10/10
trajectories, but it failed to produce `submission/audit.md` because workers and
the global judge hit the 32768 token context limit. The full-run path below
raises the endpoint to 64k and keeps endpoint concurrency aligned with forest
worker concurrency.

## Current Profiles

Recommended full-run profile:

```text
gpu=H100:2
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=2
dtype=auto
max_model_len=65536
max_num_seqs=2
forest_worker_concurrency=2
mtp=disabled
```

Cheaper 64k probe profile:

```text
gpu=H100:1
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=1
dtype=auto
max_model_len=65536
max_num_seqs=1
forest_worker_concurrency=1
mtp=disabled
```

Legacy smoke/throughput profile. Do not use this for the full forest run that is
expected to produce `audit.md`; it is the profile that hit the context limit.

```text
gpu=H100:1
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=1
dtype=auto
max_model_len=32768
max_num_seqs=8
forest_worker_concurrency=2
```

B200 requires an explicit expensive-GPU opt-in. If you want the same FP8
checkpoint, pass the model explicitly; otherwise the deploy helper may fall back
to the BF16 checkpoint on non-single-H100 profiles.

```text
gpu=B200
model=Qwen/Qwen3.6-35B-A3B-FP8
served_model=Qwen/Qwen3.6-35B-A3B-FP8
tensor_parallel_size=1
dtype=auto
max_model_len=65536
max_num_seqs=2
```

## One-Time Env And Secret Setup

Create the local `.env` values and sync the `evmbench-vllm-token` Modal secret
before deploying. The deploy command in the next section also passes
`--sync-secret --write-env`, so this setup step is mainly for first-time secret
creation or API key rotation.

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
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 600
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

Recommended full-run endpoint:

```bash
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
  --scaledown-window-seconds 600 \
  --startup-timeout-seconds 1200 \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

For the cheaper single-H100 probe, change only:

```text
--gpu H100:1
--tensor-parallel-size 1
--max-num-seqs 1
```

The deploy script now reads `.env`, uses the existing `evmbench-vllm-token`
Modal secret for the web endpoint, deploys `evmbench-vllm-qwen`, and checks
`/health`, `/v1/models`, and `/v1/chat/completions`. It does not update the
Modal secret or rewrite `.env` unless you pass explicit flags. The full-run
command above passes `--sync-secret` and `--write-env` intentionally so the
runner uses the deployed endpoint and the updated 64k profile.

After deployment, verify that `.env` matches the endpoint:

```bash
rg -n 'VLLM_API_BASE|VLLM_MODEL|VLLM_SERVED_MODEL_NAME|VLLM_MODAL_GPU|VLLM_TENSOR_PARALLEL_SIZE|VLLM_MAX_MODEL_LEN|VLLM_MAX_NUM_SEQS' .env
```

For the recommended profile, expect:

```text
VLLM_MODAL_GPU="H100:2"
VLLM_TENSOR_PARALLEL_SIZE="2"
VLLM_MAX_MODEL_LEN="65536"
VLLM_MAX_NUM_SEQS="2"
```

To intentionally rotate the local API key, Modal secret, and `.env` together,
add this flag to the full-run deploy command above:

```text
--rotate-api-key
```

To sync the current `.env` key into the Modal secret without rotating it:

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --no-write-env
```

For B200:

```bash
VLLM_ALLOW_EXPENSIVE_GPU=1 \
UV_CACHE_DIR=/tmp/uv-cache uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu B200 \
  --model Qwen/Qwen3.6-35B-A3B-FP8 \
  --served-model-name Qwen/Qwen3.6-35B-A3B-FP8 \
  --tensor-parallel-size 1 \
  --max-model-len 65536 \
  --max-num-seqs 2 \
  --dtype auto \
  --no-enable-mtp \
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

2026-05-04 32k forest attempt:

```text
runner=modal-forest-qwen-vllm-4trees-debug
audit=2024-01-canto
runtime=15m18s
exit_code=1
trajectories=10/10 captured
failure=context limit at 32768 tokens; no submission/audit.md produced
manifest=runs/phase6/vllm-4trees-2026-05-04-one-shot/modal-forest-qwen-vllm-4trees-debug/2024-01-canto/modal/logs/forest/trajectory-manifest.json
metadata=runs/phase6/vllm-4trees-2026-05-04-one-shot/modal-forest-qwen-vllm-4trees-debug/2024-01-canto/modal/logs/modal-forest-result.json
```

Use the 64k endpoint profile above before re-running this forest shape.

## Run Full Modal Forest

Use the direct `entrypoint.py forest` command for the full attempt. Avoid
`run_phase6_variants.sh` for this run unless you explicitly want matrix retry
behavior.

For the recommended 2-H100 endpoint:

```bash
set -a
. ./.env
set +a

export VLLM_LITELLM_MODEL="openai/${VLLM_SERVED_MODEL_NAME}"
export MODEL="$VLLM_LITELLM_MODEL"
export UV_CACHE_DIR=/tmp/uv-cache

RUN_ID="vllm-64k-4trees-$(date -u +%Y%m%dT%H%M%SZ)"
OUTPUT_DIR="runs/phase6/${RUN_ID}/modal-forest-qwen-vllm-4trees-debug/2024-01-canto/modal"

uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest \
  --audit-id 2024-01-canto \
  --mode detect \
  --hint-level none \
  --image ghcr.io/pranay5255/evmbench-audit:2024-01-canto \
  --output-dir "$OUTPUT_DIR" \
  --model "$MODEL" \
  --model-kwargs-json '{"drop_params":true}' \
  --cost-tracking "${MSWEA_COST_TRACKING:-ignore_errors}" \
  --scout-model "$MODEL" \
  --branch-model "$MODEL" \
  --judge-model "$MODEL" \
  --global-model "$MODEL" \
  --scout-step-limit 16 \
  --scout-cost-limit 1.0 \
  --branch-step-limit 28 \
  --branch-cost-limit 1.25 \
  --judge-step-limit 16 \
  --judge-cost-limit 0.75 \
  --global-step-limit 28 \
  --global-cost-limit 2.5 \
  --branches-per-tree 1 \
  --max-tree-roles 4 \
  --tree-roles token-flow,accounting,access-control,cross-contract \
  --worker-concurrency 2 \
  --continue-on-worker-error \
  --runtime-timeout 4200 \
  --deployment-timeout 4200
```

For the single-H100 64k probe, use the same command but set:

```text
--worker-concurrency 1
```

The registered runner settings for this shape live in
`evmbench/agents/mini-swe-agent/config.yaml` under
`mini-swe-agent-modal-forest-qwen-vllm-4trees-debug`. The direct command above
spells them out so the output directory and timeout are explicit.

## Verify Full Run Artifacts

The run is complete only if `submission/audit.md` exists and is non-empty.

```bash
test -s "$OUTPUT_DIR/submission/audit.md"
wc -c "$OUTPUT_DIR/submission/audit.md"
sed -n '1,80p' "$OUTPUT_DIR/submission/audit.md"
```

Trajectory capture should also be complete:

```bash
test -s "$OUTPUT_DIR/logs/forest/trajectory-manifest.json"
rg -n '"expected_trajectory_count"|"found_trajectory_count"|"missing_trajectory_count"|"missing_trajectory_workers"' \
  "$OUTPUT_DIR/logs/forest/trajectory-manifest.json"
```

Run-level metadata should point to the manifest and record the final error
state:

```bash
test -s "$OUTPUT_DIR/logs/modal-forest-result.json"
rg -n '"error"|"runtime_seconds"|"trajectory_manifest"|"expected_trajectory_count"|"found_trajectory_count"|"missing_trajectory_count"' \
  "$OUTPUT_DIR/logs/modal-forest-result.json"
```

If `audit.md` is missing but the manifest shows all trajectories captured, the
global judge likely failed after worker collection. Check
`$OUTPUT_DIR/logs/modal-forest-result.json` first, then inspect the global judge
trajectory listed in the manifest.

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
