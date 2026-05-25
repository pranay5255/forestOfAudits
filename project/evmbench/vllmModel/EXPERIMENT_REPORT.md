# EVMBench Open-Model vLLM Experiment Report

Use this report as the runbook and data log for Modal-backed open-model EVMBench
experiments. Fill in the metadata, run the commands, then use the collection
steps to build analysis-ready JSONL/CSV artifacts from each run directory.

## Experiment Metadata

| Field | Value |
| --- | --- |
| Experiment ID | `qwen36-27b-h100x8-524k-YYYYMMDD` |
| Date | `YYYY-MM-DD` |
| Operator | `name` |
| Repo commit | `git rev-parse HEAD` |
| Model | `Qwen/Qwen3.6-27B` |
| Endpoint | `VLLM_API_BASE` from `.env` |
| Modal app | `evmbench-vllm-qwen` |
| Profile volume | `evmbench-vllm-profiles` |
| Audit split | `detect-tasks` or explicit audit IDs below |
| Main question | Does an open Qwen vLLM endpoint produce usable EVMBench findings with measurable inference/GPU/kernel behavior? |

## Run Matrix

Start with one smoke audit, then expand the audit list. Keep `runner.concurrency=1`
for this vLLM profiling path because vLLM profiler state and Prometheus counters
are server-global.

| Run group | Harness | Mode | Audit IDs | Kernel profile | Metrics interval | Status |
| --- | --- | --- | --- | --- | --- | --- |
| smoke-codex | `codex` | `detect` | `2024-01-canto` | `torch` | `5s` | pending |
| smoke-opencode | `opencode` | `detect` | `2024-01-canto` | `torch` | `5s` | pending |
| smoke-mini | `mini-swe-agent` | `detect` | `2024-01-canto` | `torch` | `5s` | pending |
| full-codex | `codex` | `detect` | fill in | `torch` | `5s` | pending |
| full-opencode | `opencode` | `detect` | fill in | `torch` | `5s` | pending |
| full-mini | `mini-swe-agent` | `detect` | fill in | `torch` | `5s` | pending |

Suggested first audit set:

```bash
AUDITS=(
  2024-01-canto
  2024-03-canto
  2024-05-munchables
)
```

## Setup Checklist

```bash
set -a
. ./.env
set +a

uv run python -m evmbench.vllm setup-env \
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
  --scaledown-window-seconds 3600
```

Record the effective config without printing secrets:

```bash
rg -n 'VLLM_API_BASE|VLLM_MODEL|VLLM_SERVED_MODEL_NAME|VLLM_MODAL_GPU|VLLM_TENSOR_PARALLEL_SIZE|VLLM_MAX_MODEL_LEN|VLLM_MAX_NUM_SEQS|VLLM_ENABLE_GPU_TELEMETRY|VLLM_ENABLE_TORCH_PROFILER|VLLM_PROFILE_VOLUME_NAME|VLLM_SCALEDOWN_WINDOW_SECONDS' .env
```

## Deploy Endpoint

```bash
VLLM_ALLOW_EXPENSIVE_GPU=1 \
uv run python -m evmbench.vllm deploy \
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
  --scaledown-window-seconds 3600 \
  --wait-timeout 2400 \
  --request-timeout 300 \
  --chat-timeout 900
```

Verify without redeploying:

```bash
uv run python -m evmbench.vllm verify \
  --wait-timeout 2400 \
  --request-timeout 300 \
  --chat-timeout 900
```

## Run One Smoke Task

```bash
EXPERIMENT_ID=qwen36-27b-h100x8-524k-$(date -u +%Y%m%dT%H%M%SZ)
EXPERIMENT_ROOT="runs/vllm-experiments/${EXPERIMENT_ID}"
mkdir -p "${EXPERIMENT_ROOT}"

uv run python -m evmbench.vllm run-harness \
  --harness codex \
  --mode detect \
  --audit-id 2024-01-canto \
  --output-dir "${EXPERIMENT_ROOT}/codex/2024-01-canto_detect" \
  --metrics \
  --metrics-interval-seconds 5 \
  --kernel-profile torch \
  --profile-volume-name evmbench-vllm-profiles
```

After the run, check:

```bash
jq '{harness, agent_id, audit_id, mode, model, return_code, runtime_seconds, metrics_manifest_path, artifact_paths}' \
  "${EXPERIMENT_ROOT}/codex/2024-01-canto_detect/run-manifest.json"

jq '{inference: .inference.delta.counter_delta, gpu: .gpu.summary.per_gpu, kernel_artifacts: .kernel.artifact_count, errors}' \
  "${EXPERIMENT_ROOT}/codex/2024-01-canto_detect/metrics/metrics-manifest.json"
```

## Run A Small Matrix

```bash
EXPERIMENT_ID=qwen36-27b-h100x8-524k-$(date -u +%Y%m%dT%H%M%SZ)
EXPERIMENT_ROOT="runs/vllm-experiments/${EXPERIMENT_ID}"
mkdir -p "${EXPERIMENT_ROOT}"

HARNESSES=(codex opencode mini-swe-agent)
AUDITS=(2024-01-canto 2024-03-canto 2024-05-munchables)

for harness in "${HARNESSES[@]}"; do
  for audit in "${AUDITS[@]}"; do
    run_dir="${EXPERIMENT_ROOT}/${harness}/${audit}_detect"
    uv run python -m evmbench.vllm run-harness \
      --harness "${harness}" \
      --mode detect \
      --audit-id "${audit}" \
      --output-dir "${run_dir}" \
      --metrics \
      --metrics-interval-seconds 5 \
      --kernel-profile torch \
      --profile-volume-name evmbench-vllm-profiles
  done
done
```

## Expected Artifact Layout

```text
runs/vllm-experiments/<experiment-id>/<harness>/<audit>_detect/
  run-manifest.json
  stdout.log
  stderr.log
  evmbench-runs/
  metrics/
    metrics-manifest.json
    before/metrics.prom
    before/metrics.samples.json
    before/metrics.summary.json
    poll/metrics.poll.jsonl
    poll/metrics.poll-summary.json
    after/metrics.prom
    after/metrics.samples.json
    after/metrics.summary.json
    gpu/gpu.telemetry.jsonl
    gpu/gpu.summary.json
    kernel/torch/profile-index.json
    kernel/torch/cuda-summary.json
```

## Build Analysis Inputs

Create one JSONL row per run:

```bash
find "${EXPERIMENT_ROOT}" -name run-manifest.json -print0 \
  | sort -z \
  | while IFS= read -r -d '' manifest; do
      jq -c --arg manifest_path "${manifest}" '
        {
          manifest_path: $manifest_path,
          run_dir: (.output_dir // ""),
          harness,
          agent_id,
          audit_id,
          mode,
          model,
          return_code,
          runtime_seconds,
          started_at,
          finished_at,
          metrics_manifest_path,
          gpu_telemetry: .artifact_paths.gpu_telemetry,
          gpu_summary: .artifact_paths.gpu_summary,
          kernel_profile_index: .artifact_paths.kernel_profile_index,
          kernel_cuda_summary: .artifact_paths.kernel_cuda_summary
        }
      ' "${manifest}"
    done > "${EXPERIMENT_ROOT}/run-summary.jsonl"
```

Create one JSONL row per metrics manifest:

```bash
find "${EXPERIMENT_ROOT}" -name metrics-manifest.json -print0 \
  | sort -z \
  | while IFS= read -r -d '' manifest; do
      jq -c --arg manifest_path "${manifest}" '
        {
          manifest_path: $manifest_path,
          prompt_tokens_delta: (.delta.counter_delta["vllm:request_prompt_tokens_total"] // .delta.counter_delta["vllm:prompt_tokens_total"]),
          generation_tokens_delta: (.delta.counter_delta["vllm:request_generation_tokens_total"] // .delta.counter_delta["vllm:generation_tokens_total"]),
          request_success_delta: .delta.counter_delta["vllm:request_success_total"],
          prefix_hit_rate_window: .delta.prefix_cache_hit_rate_delta_window,
          gpu_sample_count: .gpu.sample_count,
          gpu_0_util_max: .gpu.summary.per_gpu["0"].utilization_gpu_percent.max,
          gpu_0_memory_max_mib: .gpu.summary.per_gpu["0"].memory_used_mib.max,
          gpu_0_power_max_watts: .gpu.summary.per_gpu["0"].power_draw_watts.max,
          kernel_artifact_count: .kernel.artifact_count,
          error_keys: (.errors | keys)
        }
      ' "${manifest}"
    done > "${EXPERIMENT_ROOT}/metrics-summary.jsonl"
```

Convert to CSV for spreadsheet analysis:

```bash
jq -rs '
  (["harness","audit_id","mode","model","return_code","runtime_seconds","metrics_manifest_path"] | @csv),
  (.[] | [.harness,.audit_id,.mode,.model,.return_code,.runtime_seconds,.metrics_manifest_path] | @csv)
' "${EXPERIMENT_ROOT}/run-summary.jsonl" > "${EXPERIMENT_ROOT}/run-summary.csv"

jq -rs '
  (["prompt_tokens_delta","generation_tokens_delta","request_success_delta","prefix_hit_rate_window","gpu_sample_count","gpu_0_util_max","gpu_0_memory_max_mib","gpu_0_power_max_watts","kernel_artifact_count","error_keys"] | @csv),
  (.[] | [.prompt_tokens_delta,.generation_tokens_delta,.request_success_delta,.prefix_hit_rate_window,.gpu_sample_count,.gpu_0_util_max,.gpu_0_memory_max_mib,.gpu_0_power_max_watts,.kernel_artifact_count,(.error_keys | join(";"))] | @csv)
' "${EXPERIMENT_ROOT}/metrics-summary.jsonl" > "${EXPERIMENT_ROOT}/metrics-summary.csv"
```

## Analysis Questions

Use these questions to structure the final analysis:

- Completion: Which runs completed with `return_code=0`? Which failed before producing a submission?
- Effectiveness: Did the generated `audit.md` identify real vulnerabilities for the audit/mode?
- Cost proxy: How many prompt and generation tokens did each run consume?
- Throughput: How long did each run take, and how did runtime vary by harness?
- Cache behavior: Did prefix cache hit rate improve across repeated audits or harnesses?
- GPU behavior: Were GPUs saturated, memory-bound, power-bound, or underutilized?
- Kernel/model behavior: Which CUDA events dominated parseable Torch profiler traces?
- Reliability: Which runs have artifact errors in `metrics/metrics-manifest.json`, and did those errors affect benchmark correctness or only profiling completeness?

## Manual Notes

| Run | Observation | Follow-up |
| --- | --- | --- |
| `codex/2024-01-canto_detect` | fill in | fill in |
| `opencode/2024-01-canto_detect` | fill in | fill in |
| `mini-swe-agent/2024-01-canto_detect` | fill in | fill in |

## Final Result Summary

| Harness | Runs | Completed | Avg runtime seconds | Avg prompt tokens | Avg generation tokens | Notes |
| --- | ---: | ---: | ---: | ---: | ---: | --- |
| `codex` | fill in | fill in | fill in | fill in | fill in | fill in |
| `opencode` | fill in | fill in | fill in | fill in | fill in | fill in |
| `mini-swe-agent` | fill in | fill in | fill in | fill in | fill in | fill in |

## Caveats

- Run one profiled harness task at a time. vLLM profiler state and Prometheus
  counters are process-global.
- `metrics/metrics-manifest.json` can contain artifact errors even when the
  benchmark run itself succeeds. Treat those as profiling completeness issues.
- Torch profiler traces give model/kernel timing. Nsight Compute and DCGM
  hardware counters remain a separate, heavier workflow.
- Long context settings are model-specific. Do not reuse Qwen YaRN, parser, or
  MTP flags for another model family without checking that model's vLLM support.
