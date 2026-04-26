#!/usr/bin/env python3
"""Modal deployment for an OpenAI-compatible vLLM endpoint.

The default app serves Qwen/Qwen3.6-35B-A3B on 2x A100-80GB GPUs. EVMBench
workers call this endpoint through LiteLLM by setting VLLM_API_BASE and
VLLM_API_KEY in the host process.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import time
import urllib.error
import urllib.request

import modal

APP_NAME = "evmbench-vllm-qwen"
WEB_LABEL = f"{APP_NAME}-serve"
DEFAULT_API_KEY_ENV = "VLLM_API_KEY"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
HF_CACHE_PATH = "/root/.cache/huggingface"
VLLM_CACHE_PATH = "/root/.cache/vllm"
PORT = 8000

MODEL_NAME = os.getenv("VLLM_MODEL", DEFAULT_MODEL)
SERVED_MODEL_NAME = os.getenv("VLLM_SERVED_MODEL_NAME", MODEL_NAME)
GPU_CONFIG = os.getenv("VLLM_MODAL_GPU", "A100-80GB:2")
MAX_MODEL_LEN = int(os.getenv("VLLM_MAX_MODEL_LEN", "32768"))
MAX_NUM_SEQS = int(os.getenv("VLLM_MAX_NUM_SEQS", "16"))
GPU_MEMORY_UTILIZATION = os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.90")
DTYPE = os.getenv("VLLM_DTYPE", "bfloat16")
NUM_SPECULATIVE_TOKENS = int(os.getenv("VLLM_NUM_SPECULATIVE_TOKENS", "2"))


def _gpu_count(gpu_config: str) -> int:
    if ":" not in gpu_config:
        return 1
    _, count = gpu_config.rsplit(":", 1)
    return int(count)


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


TENSOR_PARALLEL_SIZE = int(os.getenv("VLLM_TENSOR_PARALLEL_SIZE", str(_gpu_count(GPU_CONFIG))))
ENABLE_MTP = _env_bool("VLLM_ENABLE_MTP", True)
FAST_BOOT = _env_bool("VLLM_FAST_BOOT", False)

app = modal.App(APP_NAME)

hf_cache = modal.Volume.from_name("evmbench-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("evmbench-vllm-cache", create_if_missing=True)
vllm_secret = modal.Secret.from_name("evmbench-vllm-token", required_keys=[DEFAULT_API_KEY_ENV])

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("vllm==0.19.0", "huggingface_hub[hf_transfer]>=0.35.0")
    .env(
        {
            "HF_HOME": HF_CACHE_PATH,
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            "HUGGINGFACE_HUB_CACHE": HF_CACHE_PATH,
            "VLLM_CACHE_ROOT": VLLM_CACHE_PATH,
            "VLLM_USE_V1": "1",
        }
    )
)


@app.function(
    image=image,
    secrets=[vllm_secret],
    volumes={HF_CACHE_PATH: hf_cache},
    timeout=60 * 60 * 6,
)
def download_model(model_name: str = MODEL_NAME) -> str:
    """Populate the Modal Hugging Face cache without using local disk."""
    from huggingface_hub import snapshot_download

    path = snapshot_download(model_name, cache_dir=HF_CACHE_PATH)
    hf_cache.commit()
    return path


@app.function(
    image=image,
    gpu=GPU_CONFIG,
    secrets=[vllm_secret],
    volumes={
        HF_CACHE_PATH: hf_cache,
        VLLM_CACHE_PATH: vllm_cache,
    },
    timeout=60 * 60 * 24,
    startup_timeout=60 * 30,
    scaledown_window=300,
)
@modal.concurrent(max_inputs=50)
@modal.web_server(port=PORT, startup_timeout=60 * 30, label=WEB_LABEL)
def serve() -> None:
    api_key = os.environ[DEFAULT_API_KEY_ENV]
    command = [
        "vllm",
        "serve",
        MODEL_NAME,
        "--served-model-name",
        SERVED_MODEL_NAME,
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        "--api-key",
        api_key,
        "--tensor-parallel-size",
        str(TENSOR_PARALLEL_SIZE),
        "--max-model-len",
        str(MAX_MODEL_LEN),
        "--dtype",
        DTYPE,
        "--gpu-memory-utilization",
        GPU_MEMORY_UTILIZATION,
        "--max-num-seqs",
        str(MAX_NUM_SEQS),
        "--reasoning-parser",
        "qwen3",
        "--language-model-only",
        "--enable-prefix-caching",
        "--disable-log-requests",
        "--uvicorn-log-level",
        "warning",
    ]
    if ENABLE_MTP:
        command.extend(
            [
                "--speculative-config",
                json.dumps(
                    {
                        "method": "qwen3_next_mtp",
                        "num_speculative_tokens": NUM_SPECULATIVE_TOKENS,
                    }
                ),
            ]
        )
    if FAST_BOOT:
        command.append("--enforce-eager")
    redacted = ["<redacted>" if part == api_key else part for part in command]
    print(f"Starting vLLM: {shlex.join(redacted)}", flush=True)
    subprocess.Popen(command)


def _request(
    url: str,
    *,
    api_key: str,
    payload: dict[str, object] | None = None,
    timeout: float = 30.0,
) -> bytes:
    data = None if payload is None else json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=data,
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        method="GET" if payload is None else "POST",
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:
        return response.read()


def _wait_for_health(base_url: str, *, api_key: str, timeout_seconds: int = 900) -> None:
    deadline = time.time() + timeout_seconds
    health_url = f"{base_url}/health"
    last_error: Exception | None = None
    while time.time() < deadline:
        try:
            _request(health_url, api_key=api_key, timeout=10.0)
            return
        except (OSError, urllib.error.URLError, urllib.error.HTTPError) as exc:
            last_error = exc
            time.sleep(5)
    raise RuntimeError(f"Timed out waiting for {health_url}: {last_error}")


@app.local_entrypoint()
def main(prompt: str = "Reply with the single word: ok", download_only: bool = False) -> None:
    if download_only:
        path = download_model.remote(MODEL_NAME)
        print(f"Downloaded {MODEL_NAME} into Modal volume cache: {path}", flush=True)
        return

    api_key = os.getenv(DEFAULT_API_KEY_ENV)
    if not api_key:
        raise RuntimeError(f"{DEFAULT_API_KEY_ENV} must be set locally for the smoke request.")

    base_url = serve.get_web_url().rstrip("/")
    print(f"vLLM base URL: {base_url}/v1", flush=True)
    _wait_for_health(base_url, api_key=api_key)

    response = _request(
        f"{base_url}/v1/chat/completions",
        api_key=api_key,
        payload={
            "model": SERVED_MODEL_NAME,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
        },
    )
    payload = json.loads(response.decode("utf-8"))
    print(json.dumps(payload, indent=2), flush=True)
