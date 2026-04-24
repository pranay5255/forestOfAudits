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
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
DEFAULT_API_KEY_ENV = "VLLM_API_KEY"
HF_CACHE_PATH = "/root/.cache/huggingface"
VLLM_CACHE_PATH = "/root/.cache/vllm"
PORT = 8000

app = modal.App(APP_NAME)

hf_cache = modal.Volume.from_name("evmbench-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("evmbench-vllm-cache", create_if_missing=True)

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .pip_install("vllm==0.19.0")
    .env(
        {
            "HF_HOME": HF_CACHE_PATH,
            "HUGGINGFACE_HUB_CACHE": HF_CACHE_PATH,
            "VLLM_CACHE_ROOT": VLLM_CACHE_PATH,
        }
    )
)


@app.function(
    image=image,
    gpu="A100-80GB:2",
    secrets=[modal.Secret.from_name("evmbench-vllm-token", required_keys=[DEFAULT_API_KEY_ENV])],
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
        DEFAULT_MODEL,
        "--host",
        "0.0.0.0",
        "--port",
        str(PORT),
        "--api-key",
        api_key,
        "--tensor-parallel-size",
        "2",
        "--max-model-len",
        "32768",
        "--dtype",
        "bfloat16",
        "--reasoning-parser",
        "qwen3",
        "--language-model-only",
    ]
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
def main(prompt: str = "Reply with the single word: ok") -> None:
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
            "model": DEFAULT_MODEL,
            "messages": [{"role": "user", "content": prompt}],
            "max_tokens": 16,
            "temperature": 0,
        },
    )
    payload = json.loads(response.decode("utf-8"))
    print(json.dumps(payload, indent=2), flush=True)
