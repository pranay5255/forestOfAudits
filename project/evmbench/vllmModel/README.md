# Open-Model vLLM EVMBench Experiments

This directory documents the Modal-backed `evmbench.vllm` path for replacing
closed model APIs with open Hugging Face models in EVMBench. The path deploys a
reusable OpenAI-compatible vLLM endpoint, then runs `codex`, `opencode`, or
`mini-swe-agent` directly against that endpoint while collecting per-run
inference, GPU, and Torch profiler artifacts.

For a runnable experiment worksheet, use [EXPERIMENT_REPORT.md](EXPERIMENT_REPORT.md).

## Flow

1. Configure `.env` and the Modal secret:

```bash
uv run python -m evmbench.vllm setup-env
```

2. Deploy the vLLM endpoint and write the resolved URL/API settings:

```bash
uv run python -m evmbench.vllm deploy --sync-secret --write-env
```

3. Verify an existing endpoint:

```bash
uv run python -m evmbench.vllm verify
```

4. Run one forest-free EVMBench task with metrics and Torch profiling:

```bash
uv run python -m evmbench.vllm run-harness \
  --harness codex \
  --mode detect \
  --audit-id 2024-01-canto \
  --metrics \
  --metrics-interval-seconds 5 \
  --kernel-profile torch
```

5. Inspect the run directory printed by `run-harness`. The primary files are
`run-manifest.json`, `stdout.log`, `stderr.log`, and
`metrics/metrics-manifest.json`.

## Qwen3.6-27B Long-Context Modal Command

Use this command for dense Qwen3.6-27B coding runs at a 524K token window. It
uses 8 H100s, tensor parallelism, Qwen reasoning/tool parsing, MTP speculative
decoding, Qwen coding generation defaults, GPU telemetry, and Torch profiler
support.

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
  --scaledown-window-seconds 43200 \
  --wait-timeout 2400 \
  --request-timeout 300 \
  --chat-timeout 900
```

For a full roughly 1M token context, change `--max-model-len` to `1010000` and
change the YaRN `factor` in `--hf-overrides` to `4.0`. For windows at or below
262K, prefer no YaRN unless the experiment specifically needs long-context
extrapolation.

## Model Swap Matrix

| Model family | Example flags to keep | Qwen-specific flags to revisit |
| --- | --- | --- |
| Qwen3.6 dense/coder | OpenAI-compatible endpoint flags, tensor parallelism, prefix caching, `--generation-config vllm`, telemetry/profiler flags | `--reasoning-parser qwen3`, `--tool-call-parser qwen3_coder`, `--default-chat-template-kwargs`, Qwen YaRN `--hf-overrides`, MTP method |
| DeepSeek coder/reasoner | OpenAI-compatible endpoint flags, GPU sizing, `--max-model-len`, telemetry/profiler flags | Qwen parser names, Qwen chat-template kwargs, Qwen YaRN sections |
| Llama-family instruct/coder | OpenAI-compatible endpoint flags, GPU sizing, dtype, telemetry/profiler flags | Qwen parser names, Qwen chat-template kwargs, Qwen MTP settings, Qwen YaRN sections |
| Other Hugging Face chat models | `--model`, `--served-model-name`, `--dtype`, `--tensor-parallel-size`, `--max-model-len`, telemetry/profiler flags | Any Qwen parser, template, RoPE, or speculative decoding options unless the model card says they apply |

The generic vLLM/OpenAI-compatible knobs are `--model`, `--served-model-name`,
`--gpu`, `--tensor-parallel-size`, `--max-model-len`, `--max-num-seqs`,
`--gpu-memory-utilization`, `--dtype`, timeouts, `/metrics`, telemetry, and
Torch profiler settings. Treat parser, template, YaRN, and MTP options as
model-family-specific.

## Artifact Map

Every non-dry `run-harness` invocation can write:

- `run-manifest.json`: harness, audit, mode, model, command, redacted vLLM env,
  profiling settings, return code, and artifact paths.
- `metrics/metrics-manifest.json`: links inference, GPU, kernel/profile, and
  error sections.
- `metrics/before` and `metrics/after`: raw Prometheus `.prom`, parsed samples,
  and vLLM summaries from `/metrics`.
- `metrics/poll`: periodic `/metrics` snapshots and poll summary JSON.
- `metrics/gpu/gpu.telemetry.jsonl`: per-run filtered GPU samples from the
  Modal profile volume.
- `metrics/gpu/gpu.summary.json`: per-GPU utilization, memory, power,
  temperature, and clock summaries.
- `metrics/kernel/torch/profile-index.json`: index of downloaded Torch profiler
  artifacts.
- `metrics/kernel/torch/cuda-summary.json`: best-effort top CUDA/kernel events
  from parseable Chrome trace JSON.

The runner preserves artifact errors in `metrics/metrics-manifest.json` instead
of failing the benchmark run. Torch profiler traces provide per-run model/kernel
timing. Full Nsight Compute or DCGM hardware-counter profiling is a heavier
follow-up workflow because it can require additional host tooling, privileges,
and longer profiling windows.
