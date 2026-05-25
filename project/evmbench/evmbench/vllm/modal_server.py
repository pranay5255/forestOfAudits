#!/usr/bin/env python3
"""Modal deployment for a reusable OpenAI-compatible vLLM endpoint.

The default app uses a single H100 with the FP8 Qwen checkpoint. EVMBench
Codex, OpenCode, and mini-swe-agent harnesses call this endpoint through their
OpenAI-compatible provider settings.
"""

from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
import time
import urllib.error
import urllib.request

import modal

APP_NAME = "evmbench-vllm-qwen"
WEB_LABEL = f"{APP_NAME}-serve"
DEFAULT_API_KEY_ENV = "VLLM_API_KEY"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
DEFAULT_H100_MODEL = f"{DEFAULT_MODEL}-FP8"
DEFAULT_GPU_CONFIG = "H100:1"
HF_CACHE_PATH = "/root/.cache/huggingface"
VLLM_CACHE_PATH = "/root/.cache/vllm"
PROFILE_VOLUME_PATH = "/profiles"
TORCH_PROFILER_DIR = f"{PROFILE_VOLUME_PATH}/torch"
GPU_TELEMETRY_DIR = f"{PROFILE_VOLUME_PATH}/gpu"
DEFAULT_PROFILE_VOLUME_NAME = "evmbench-vllm-profiles"
PORT = 8000


def _gpu_count(gpu_config: str) -> int:
    if ":" not in gpu_config:
        return 1
    _, count = gpu_config.rsplit(":", 1)
    return int(count)


def _gpu_family(gpu_config: str) -> str:
    return gpu_config.split(":", 1)[0].upper()


def _env_bool(name: str, default: bool) -> bool:
    value = os.getenv(name)
    if value is None or not value.strip():
        return default
    return value.strip().lower() in {"1", "true", "yes", "on"}


def _is_expensive_gpu(gpu_config: str) -> bool:
    family = _gpu_family(gpu_config)
    return family in {"B200", "H200"} or _gpu_count(gpu_config) > 1


def _require_expensive_gpu_opt_in(gpu_config: str) -> None:
    if _is_expensive_gpu(gpu_config) and not _env_bool("VLLM_ALLOW_EXPENSIVE_GPU", False):
        raise RuntimeError(
            f"Refusing to configure expensive Modal GPU {gpu_config!r}. "
            "Set VLLM_ALLOW_EXPENSIVE_GPU=1 to deploy B200/H200 or multi-GPU vLLM servers."
        )


GPU_CONFIG = os.getenv("VLLM_MODAL_GPU", DEFAULT_GPU_CONFIG)
GPU_FAMILY = _gpu_family(GPU_CONFIG)
GPU_COUNT = _gpu_count(GPU_CONFIG)
SINGLE_H100_PROFILE = GPU_FAMILY == "H100" and GPU_COUNT == 1
ALLOW_EXPENSIVE_GPU = _env_bool("VLLM_ALLOW_EXPENSIVE_GPU", False)
MODEL_NAME = os.getenv("VLLM_MODEL") or (DEFAULT_H100_MODEL if SINGLE_H100_PROFILE else DEFAULT_MODEL)
SERVED_MODEL_NAME = os.getenv("VLLM_SERVED_MODEL_NAME", MODEL_NAME)
MAX_MODEL_LEN = int(os.getenv("VLLM_MAX_MODEL_LEN", "32768"))
MAX_NUM_SEQS = int(os.getenv("VLLM_MAX_NUM_SEQS", "8" if SINGLE_H100_PROFILE else "16"))
GPU_MEMORY_UTILIZATION = os.getenv("VLLM_GPU_MEMORY_UTILIZATION", "0.90")
DTYPE = os.getenv("VLLM_DTYPE", "auto" if SINGLE_H100_PROFILE else "bfloat16")
NUM_SPECULATIVE_TOKENS = int(os.getenv("VLLM_NUM_SPECULATIVE_TOKENS", "2"))
STARTUP_TIMEOUT_SECONDS = int(os.getenv("VLLM_STARTUP_TIMEOUT_SECONDS", "600"))
SCALEDOWN_WINDOW_SECONDS = int(os.getenv("VLLM_SCALEDOWN_WINDOW_SECONDS", "60"))
ENABLE_AUTO_TOOL_CHOICE = _env_bool("VLLM_ENABLE_AUTO_TOOL_CHOICE", True)
TOOL_CALL_PARSER = os.getenv("VLLM_TOOL_CALL_PARSER", "qwen3_coder").strip()
REASONING_PARSER = os.getenv("VLLM_REASONING_PARSER", "qwen3").strip()
LANGUAGE_MODEL_ONLY = _env_bool("VLLM_LANGUAGE_MODEL_ONLY", True)
HF_OVERRIDES = os.getenv("VLLM_HF_OVERRIDES", "").strip()
GENERATION_CONFIG = os.getenv("VLLM_GENERATION_CONFIG", "auto").strip()
OVERRIDE_GENERATION_CONFIG = os.getenv("VLLM_OVERRIDE_GENERATION_CONFIG", "").strip()
DEFAULT_CHAT_TEMPLATE_KWARGS = os.getenv(
    "VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS",
    '{"enable_thinking": false}',
).strip()
LOG_STATS_INTERVAL = os.getenv("VLLM_LOG_STATS_INTERVAL", "10").strip()
ENABLE_GPU_TELEMETRY = _env_bool("VLLM_ENABLE_GPU_TELEMETRY", False)
GPU_TELEMETRY_INTERVAL_SECONDS = float(os.getenv("VLLM_GPU_TELEMETRY_INTERVAL_SECONDS", "1") or "1")
ENABLE_TORCH_PROFILER = _env_bool("VLLM_ENABLE_TORCH_PROFILER", False)
PROFILE_VOLUME_NAME = os.getenv("VLLM_PROFILE_VOLUME_NAME", DEFAULT_PROFILE_VOLUME_NAME).strip() or DEFAULT_PROFILE_VOLUME_NAME

_require_expensive_gpu_opt_in(GPU_CONFIG)


TENSOR_PARALLEL_SIZE = int(os.getenv("VLLM_TENSOR_PARALLEL_SIZE", str(GPU_COUNT)))
ENABLE_MTP = _env_bool("VLLM_ENABLE_MTP", True)
FAST_BOOT = _env_bool("VLLM_FAST_BOOT", False)
ALLOW_LONG_MAX_MODEL_LEN = _env_bool("VLLM_ALLOW_LONG_MAX_MODEL_LEN", False)


def _config_summary() -> str:
    return (
        f"model={MODEL_NAME} served_model={SERVED_MODEL_NAME} gpu={GPU_CONFIG} "
        f"tensor_parallel={TENSOR_PARALLEL_SIZE} max_model_len={MAX_MODEL_LEN} "
        f"max_num_seqs={MAX_NUM_SEQS} dtype={DTYPE} "
        f"gpu_memory_utilization={GPU_MEMORY_UTILIZATION} mtp={ENABLE_MTP} "
        f"num_speculative_tokens={NUM_SPECULATIVE_TOKENS if ENABLE_MTP else 0} "
        f"fast_boot={FAST_BOOT} startup_timeout={STARTUP_TIMEOUT_SECONDS} "
        f"scaledown_window={SCALEDOWN_WINDOW_SECONDS} "
        f"auto_tool_choice={ENABLE_AUTO_TOOL_CHOICE} tool_call_parser={TOOL_CALL_PARSER or '-'} "
        f"reasoning_parser={REASONING_PARSER or '-'} language_model_only={LANGUAGE_MODEL_ONLY} "
        f"allow_long_max_model_len={ALLOW_LONG_MAX_MODEL_LEN} "
        f"hf_overrides={'set' if HF_OVERRIDES else 'not set'} "
        f"generation_config={GENERATION_CONFIG or '-'} "
        f"override_generation_config={'set' if OVERRIDE_GENERATION_CONFIG else 'not set'} "
        f"default_chat_template_kwargs={DEFAULT_CHAT_TEMPLATE_KWARGS or '{}'} "
        f"log_stats_interval={LOG_STATS_INTERVAL or '-'} "
        f"gpu_telemetry={ENABLE_GPU_TELEMETRY} "
        f"gpu_telemetry_interval={GPU_TELEMETRY_INTERVAL_SECONDS} "
        f"torch_profiler={ENABLE_TORCH_PROFILER} "
        f"profile_volume={PROFILE_VOLUME_NAME}"
    )

app = modal.App(APP_NAME)

hf_cache = modal.Volume.from_name("evmbench-hf-cache", create_if_missing=True)
vllm_cache = modal.Volume.from_name("evmbench-vllm-cache", create_if_missing=True)
profile_volume = modal.Volume.from_name(PROFILE_VOLUME_NAME, create_if_missing=True)
vllm_secret = modal.Secret.from_name("evmbench-vllm-token", required_keys=[DEFAULT_API_KEY_ENV])

image = (
    modal.Image.from_registry("nvidia/cuda:12.9.0-devel-ubuntu22.04", add_python="3.12")
    .entrypoint([])
    .pip_install("vllm==0.19.0", "huggingface_hub[hf_transfer]>=0.35.0,<1.0")
    .env(
        {
            "HF_HOME": HF_CACHE_PATH,
            "HF_HUB_ENABLE_HF_TRANSFER": "1",
            "HUGGINGFACE_HUB_CACHE": HF_CACHE_PATH,
            "VLLM_CACHE_ROOT": VLLM_CACHE_PATH,
            "VLLM_LOGGING_LEVEL": os.getenv("VLLM_LOGGING_LEVEL", "INFO"),
            "VLLM_LOG_STATS_INTERVAL": LOG_STATS_INTERVAL,
            "VLLM_ENABLE_GPU_TELEMETRY": "1" if ENABLE_GPU_TELEMETRY else "0",
            "VLLM_GPU_TELEMETRY_INTERVAL_SECONDS": str(GPU_TELEMETRY_INTERVAL_SECONDS),
            "VLLM_ENABLE_TORCH_PROFILER": "1" if ENABLE_TORCH_PROFILER else "0",
            "VLLM_PROFILE_VOLUME_NAME": PROFILE_VOLUME_NAME,
            "VLLM_PROFILE_VOLUME_PATH": PROFILE_VOLUME_PATH,
            "VLLM_TORCH_PROFILER_DIR": TORCH_PROFILER_DIR,
            "VLLM_GPU_TELEMETRY_DIR": GPU_TELEMETRY_DIR,
            "VLLM_MODEL": MODEL_NAME,
            "VLLM_SERVED_MODEL_NAME": SERVED_MODEL_NAME,
            "VLLM_MODAL_GPU": GPU_CONFIG,
            "VLLM_TENSOR_PARALLEL_SIZE": str(TENSOR_PARALLEL_SIZE),
            "VLLM_MAX_MODEL_LEN": str(MAX_MODEL_LEN),
            "VLLM_MAX_NUM_SEQS": str(MAX_NUM_SEQS),
            "VLLM_GPU_MEMORY_UTILIZATION": GPU_MEMORY_UTILIZATION,
            "VLLM_DTYPE": DTYPE,
            "VLLM_ALLOW_EXPENSIVE_GPU": "1" if ALLOW_EXPENSIVE_GPU else "0",
            "VLLM_ENABLE_MTP": "1" if ENABLE_MTP else "0",
            "VLLM_NUM_SPECULATIVE_TOKENS": str(NUM_SPECULATIVE_TOKENS),
            "VLLM_ENABLE_AUTO_TOOL_CHOICE": "1" if ENABLE_AUTO_TOOL_CHOICE else "0",
            "VLLM_TOOL_CALL_PARSER": TOOL_CALL_PARSER,
            "VLLM_REASONING_PARSER": REASONING_PARSER,
            "VLLM_LANGUAGE_MODEL_ONLY": "1" if LANGUAGE_MODEL_ONLY else "0",
            "VLLM_HF_OVERRIDES": HF_OVERRIDES,
            "VLLM_GENERATION_CONFIG": GENERATION_CONFIG,
            "VLLM_OVERRIDE_GENERATION_CONFIG": OVERRIDE_GENERATION_CONFIG,
            "VLLM_DEFAULT_CHAT_TEMPLATE_KWARGS": DEFAULT_CHAT_TEMPLATE_KWARGS,
            "VLLM_FAST_BOOT": "1" if FAST_BOOT else "0",
            "VLLM_ALLOW_LONG_MAX_MODEL_LEN": "1" if ALLOW_LONG_MAX_MODEL_LEN else "0",
            "VLLM_STARTUP_TIMEOUT_SECONDS": str(STARTUP_TIMEOUT_SECONDS),
            "VLLM_SCALEDOWN_WINDOW_SECONDS": str(SCALEDOWN_WINDOW_SECONDS),
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

    print(f"Downloading model into Modal HF cache: {model_name}", flush=True)
    path = snapshot_download(model_name, cache_dir=HF_CACHE_PATH)
    hf_cache.commit()
    return path


def build_vllm_command(api_key: str) -> list[str]:
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
        "--enable-prefix-caching",
        "--uvicorn-log-level",
        "warning",
    ]
    # vLLM 0.19 rejects these legacy serve flags; keep the values in the
    # container env/config summary, but do not pass them to `vllm serve`.
    if REASONING_PARSER:
        command.extend(["--reasoning-parser", REASONING_PARSER])
    if DEFAULT_CHAT_TEMPLATE_KWARGS:
        command.extend(["--default-chat-template-kwargs", DEFAULT_CHAT_TEMPLATE_KWARGS])
    if GENERATION_CONFIG:
        command.extend(["--generation-config", GENERATION_CONFIG])
    if OVERRIDE_GENERATION_CONFIG:
        command.extend(["--override-generation-config", OVERRIDE_GENERATION_CONFIG])
    if HF_OVERRIDES:
        command.extend(["--hf-overrides", HF_OVERRIDES])
    if LANGUAGE_MODEL_ONLY:
        command.append("--language-model-only")
    if ENABLE_AUTO_TOOL_CHOICE:
        command.append("--enable-auto-tool-choice")
    if TOOL_CALL_PARSER:
        command.extend(["--tool-call-parser", TOOL_CALL_PARSER])
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
    if ENABLE_TORCH_PROFILER:
        command.extend(
            [
                "--profiler-config",
                json.dumps({"profiler": "torch", "torch_profiler_dir": TORCH_PROFILER_DIR}),
            ]
        )
    if FAST_BOOT:
        command.append("--enforce-eager")
    return command


def _start_gpu_telemetry_sampler() -> subprocess.Popen[str] | None:
    if not ENABLE_GPU_TELEMETRY:
        return None
    os.makedirs(GPU_TELEMETRY_DIR, exist_ok=True)
    output_path = os.path.join(
        GPU_TELEMETRY_DIR,
        f"gpu.telemetry.{int(time.time())}.{os.getpid()}.jsonl",
    )
    script = r"""
import csv
import datetime as _datetime
import json
import os
import subprocess
import time

fields = [
    "index",
    "uuid",
    "name",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "power.draw",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
]
path = os.environ["EVM_GPU_TELEMETRY_PATH"]
interval = float(os.environ.get("EVM_GPU_TELEMETRY_INTERVAL", "1") or "1")
os.makedirs(os.path.dirname(path), exist_ok=True)
with open(path, "a", encoding="utf-8", buffering=1) as handle:
    while True:
        now = time.time()
        entry = {
            "timestamp": now,
            "timestamp_iso": _datetime.datetime.fromtimestamp(now, _datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "source": "nvidia-smi",
            "fields": fields,
        }
        try:
            completed = subprocess.run(
                [
                    "nvidia-smi",
                    "--query-gpu=" + ",".join(fields),
                    "--format=csv,noheader,nounits",
                ],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                timeout=10,
            )
            if completed.returncode != 0:
                entry.update({"returncode": completed.returncode, "error": completed.stderr.strip()})
            else:
                rows = []
                for line in completed.stdout.splitlines():
                    if not line.strip():
                        continue
                    values = next(csv.reader([line], skipinitialspace=True))
                    rows.append({field: value.strip() for field, value in zip(fields, values)})
                entry["gpus"] = rows
        except Exception as exc:
            entry.update({"error": str(exc), "type": type(exc).__name__})
        handle.write(json.dumps(entry, sort_keys=True) + "\n")
        time.sleep(interval)
"""
    env = os.environ.copy()
    env["EVM_GPU_TELEMETRY_PATH"] = output_path
    env["EVM_GPU_TELEMETRY_INTERVAL"] = str(GPU_TELEMETRY_INTERVAL_SECONDS)
    print(f"Starting GPU telemetry sampler: {output_path}", flush=True)
    return subprocess.Popen([sys.executable, "-c", script], env=env, text=True)


@app.function(
    image=image,
    gpu=GPU_CONFIG,
    secrets=[vllm_secret],
    volumes={
        HF_CACHE_PATH: hf_cache,
        VLLM_CACHE_PATH: vllm_cache,
        PROFILE_VOLUME_PATH: profile_volume,
    },
    timeout=60 * 60 * 24,
    startup_timeout=STARTUP_TIMEOUT_SECONDS,
    scaledown_window=SCALEDOWN_WINDOW_SECONDS,
)
@modal.concurrent(max_inputs=50)
@modal.web_server(port=PORT, startup_timeout=STARTUP_TIMEOUT_SECONDS, label=WEB_LABEL)
def serve() -> None:
    api_key = os.environ[DEFAULT_API_KEY_ENV]
    print(f"vLLM Modal config: {_config_summary()}", flush=True)
    if ENABLE_TORCH_PROFILER:
        os.makedirs(TORCH_PROFILER_DIR, exist_ok=True)
    _start_gpu_telemetry_sampler()
    command = build_vllm_command(api_key)
    redacted = ["<redacted>" if part == api_key else part for part in command]
    print(f"Starting vLLM: {shlex.join(redacted)}", flush=True)
    process = subprocess.Popen(command)
    time.sleep(5)
    return_code = process.poll()
    if return_code is not None:
        raise RuntimeError(f"vLLM exited before the web server became ready with code {return_code}.")


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
    print(f"vLLM Modal config: {_config_summary()}", flush=True)
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
