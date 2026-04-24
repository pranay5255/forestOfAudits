# Plan: Switch Inference to Self-Hosted vLLM on Modal (Qwen3.6-35B-A3B)

## Architecture Overview

LLM calls happen **locally** (host process) via `LitellmModel` → `litellm.completion()`.
Modal sandboxes only execute shell commands. So switching inference = changing what
`litellm.completion()` points at, plus deploying the vLLM server.

LiteLLM already accepts `api_base` through `model_kwargs` (spread into `litellm.completion()`
at `litellm_model.py:65-69`). The change is mostly config + a deployment script.

## GPU Sizing

- **Qwen3.6-35B-A3B**: 35B total (MoE), ~70GB in BF16
- **2× A100-80GB with TP=2**: 160GB combined, fits weights + KV cache for 30 concurrent seqs
- **Cost**: ~$7.40/hr → $1000 budget = ~135 hours (35-65 full forest runs)
- `container_idle_timeout=300` stops billing between forest stages

---

## Changes

### 1. NEW: `deploy_vllm.py` — Modal vLLM deployment

**Path**: `project/evmbench/evmbench/agents/mini-swe-agent/deploy_vllm.py`

Modal app deploying Qwen3.6-35B-A3B on 2× A100-80GB:
- `modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04")` + `vllm==0.19.0`
- `modal.Volume` for HF cache (download once) and vLLM JIT cache
- `@modal.web_server(port=8000)` for stable HTTPS URL
- `@modal.concurrent(max_inputs=50)` for concurrent requests
- Token auth via Modal secret `evmbench-vllm-token` → vLLM `--api-key`
- `--tensor-parallel-size 2`, `--max-model-len 32768`, `--dtype bfloat16`
- `container_idle_timeout=300` (5 min idle shutdown)
- Includes `@app.local_entrypoint()` for `modal run deploy_vllm.py` health/smoke test

### 2. MODIFY: `modal_forest.py` — API key check + VLLM_API_BASE injection

**Lines 609-611**: Accept `VLLM_API_KEY` as fallback for `OPENAI_API_KEY`:
```python
openai_api_key = os.getenv("OPENAI_API_KEY") or os.getenv("VLLM_API_KEY")
if not openai_api_key:
    raise RuntimeError(
        "OPENAI_API_KEY (or VLLM_API_KEY for self-hosted vLLM) must be set."
    )
```

**`config_from_args()` (line 852)**: Inject `VLLM_API_BASE` into `model_kwargs`:
```python
model_kwargs = dict(args.model_kwargs_json)
vllm_api_base = os.getenv("VLLM_API_BASE")
if vllm_api_base and "api_base" not in model_kwargs:
    model_kwargs["api_base"] = vllm_api_base
# ... use model_kwargs instead of args.model_kwargs_json in ForestConfig
```

### 3. MODIFY: `modal_baseline.py` — same two changes

**Lines 533-538**: Accept `VLLM_API_KEY` fallback (same pattern as forest).

**`config_from_args()` (line 687)**: Inject `VLLM_API_BASE` into `model_kwargs` (same pattern).

### 4. MODIFY: `modal_runner.py` — API key validation

**`modal_runner_environment()` (lines 188-193)**: Accept `VLLM_API_KEY`:
```python
openai_api_key = env.get("OPENAI_API_KEY", "") or env.get("VLLM_API_KEY", "")
if not openai_api_key or openai_api_key.startswith("${{"):
    raise RuntimeError(...)
if not env.get("OPENAI_API_KEY"):
    env["OPENAI_API_KEY"] = openai_api_key
```

### 5. NEW: `run_vllm_smoke.sh` — smoke test script

**Path**: `project/evmbench/evmbench/agents/mini-swe-agent/run_vllm_smoke.sh`

Steps: deploy vLLM → health check loop → test completion → mini forest run (1 role, 1 branch).

---

## Usage

```bash
# 1. Create Modal secret
modal secret create evmbench-vllm-token VLLM_API_TOKEN=my-secret-token

# 2. Deploy vLLM
modal deploy evmbench/agents/mini-swe-agent/deploy_vllm.py

# 3. Set env vars
export VLLM_API_BASE="https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1"
export OPENAI_API_KEY="my-secret-token"   # or VLLM_API_KEY

# 4. Run forest
uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest \
    --audit-id 2024-01-canto \
    --model "openai/Qwen/Qwen3.6-35B-A3B" \
    --cost-tracking ignore_errors \
    --worker-concurrency 10
```

## Verification

1. `modal run deploy_vllm.py` — runs built-in health check + test completion
2. `run_vllm_smoke.sh` — end-to-end: deploy → health → mini forest run
3. Check `<output-dir>/logs/modal-forest-result.json` has worker results with non-null trajectories
4. Confirm no `OPENAI_API_KEY` errors when only `VLLM_API_KEY` is set

## Files Summary

| File | Action | Lines |
|------|--------|-------|
| `mini-swe-agent/deploy_vllm.py` | NEW | ~120 lines |
| `mini-swe-agent/modal_forest.py` | MODIFY | 609-611 (key check), 852 (model_kwargs injection) |
| `mini-swe-agent/modal_baseline.py` | MODIFY | 533-538 (key check), 687 (model_kwargs injection) |
| `agents/modal_runner.py` | MODIFY | 188-193 (key validation) |
| `mini-swe-agent/run_vllm_smoke.sh` | NEW | ~60 lines |
