#!/usr/bin/env python3
"""Deploy the EVMBench vLLM Modal endpoint and verify it serves requests.

Run:
  uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py
"""

from __future__ import annotations

import argparse
import os
import secrets
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path

from vllm_common import (
    DEFAULT_APP_NAME,
    DEFAULT_MODEL,
    DEFAULT_SECRET_NAME,
    api_base_from_server_root,
    clean_env_value,
    create_or_update_modal_secret,
    env_bool,
    fail,
    load_project_env,
    modal_binary,
    project_root,
    redacted_length,
    upsert_dotenv_values,
    verify_vllm_endpoint,
    write_json,
)

FP8_MODEL = f"{DEFAULT_MODEL}-FP8"
DEFAULT_GPU_CONFIG = "H100"
DEFAULT_STARTUP_TIMEOUT_SECONDS = 600
DEFAULT_SCALEDOWN_WINDOW_SECONDS = 60


@dataclass(frozen=True)
class VLLMServerConfig:
    gpu: str
    model: str
    served_model_name: str
    tensor_parallel_size: str
    max_model_len: str
    max_num_seqs: str
    gpu_memory_utilization: str
    dtype: str
    enable_mtp: bool
    num_speculative_tokens: str
    fast_boot: bool
    startup_timeout_seconds: int
    scaledown_window_seconds: int


def _deploy_file() -> Path:
    return Path(__file__).resolve().parent / "deploy_vllm.py"


def _get_web_url(app_name: str) -> str:
    try:
        import modal
    except ModuleNotFoundError as exc:
        raise RuntimeError("The `modal` package is required to look up the deployed web URL.") from exc

    fn = modal.Function.from_name(app_name, "serve")
    url = fn.get_web_url()
    if not url:
        raise RuntimeError(f"Modal did not return a web URL for {app_name}.serve.")
    return url.rstrip("/")


def _deploy_modal_app(env: dict[str, str]) -> None:
    command = [modal_binary(), "deploy", str(_deploy_file())]
    print("[deploy] deploying Modal vLLM app", flush=True)
    completed = subprocess.run(command, cwd=project_root(), env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"Modal deploy failed with exit code {completed.returncode}.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--app-name", default=clean_env_value(os.getenv("VLLM_MODAL_APP_NAME")) or DEFAULT_APP_NAME)
    parser.add_argument("--secret-name", default=clean_env_value(os.getenv("VLLM_MODAL_SECRET_NAME")) or DEFAULT_SECRET_NAME)
    parser.add_argument("--model", default=clean_env_value(os.getenv("VLLM_MODEL")))
    parser.add_argument("--served-model-name", default=clean_env_value(os.getenv("VLLM_SERVED_MODEL_NAME")))
    parser.add_argument("--gpu", default=clean_env_value(os.getenv("VLLM_MODAL_GPU")) or DEFAULT_GPU_CONFIG)
    parser.add_argument("--tensor-parallel-size", default=clean_env_value(os.getenv("VLLM_TENSOR_PARALLEL_SIZE")))
    parser.add_argument("--max-model-len", default=clean_env_value(os.getenv("VLLM_MAX_MODEL_LEN")))
    parser.add_argument("--max-num-seqs", default=clean_env_value(os.getenv("VLLM_MAX_NUM_SEQS")))
    parser.add_argument(
        "--gpu-memory-utilization",
        default=clean_env_value(os.getenv("VLLM_GPU_MEMORY_UTILIZATION")),
    )
    parser.add_argument("--dtype", default=clean_env_value(os.getenv("VLLM_DTYPE")))
    parser.add_argument("--enable-mtp", action=argparse.BooleanOptionalAction, default=env_bool("VLLM_ENABLE_MTP", True))
    parser.add_argument("--num-speculative-tokens", default=clean_env_value(os.getenv("VLLM_NUM_SPECULATIVE_TOKENS")) or "2")
    parser.add_argument("--fast-boot", action=argparse.BooleanOptionalAction, default=env_bool("VLLM_FAST_BOOT", False))
    parser.add_argument(
        "--allow-expensive-gpu",
        action=argparse.BooleanOptionalAction,
        default=env_bool("VLLM_ALLOW_EXPENSIVE_GPU", False),
        help="Allow B200/H200 or multi-GPU Modal deployments.",
    )
    parser.add_argument(
        "--startup-timeout-seconds",
        type=int,
        default=int(clean_env_value(os.getenv("VLLM_STARTUP_TIMEOUT_SECONDS")) or DEFAULT_STARTUP_TIMEOUT_SECONDS),
    )
    parser.add_argument(
        "--scaledown-window-seconds",
        type=int,
        default=int(clean_env_value(os.getenv("VLLM_SCALEDOWN_WINDOW_SECONDS")) or DEFAULT_SCALEDOWN_WINDOW_SECONDS),
    )
    parser.add_argument("--api-key", default=clean_env_value(os.getenv("VLLM_API_KEY")))
    parser.add_argument(
        "--rotate-api-key",
        action="store_true",
        help="Generate a fresh VLLM_API_KEY, sync it to the Modal secret, and persist it to .env.",
    )
    parser.add_argument("--hf-token", default=clean_env_value(os.getenv("HF_TOKEN")))
    parser.add_argument(
        "--sync-secret",
        action="store_true",
        help="Update the Modal secret from the current .env/API key before deploy.",
    )
    parser.add_argument(
        "--skip-secret",
        action="store_true",
        help="Deprecated compatibility flag; secret sync is skipped by default.",
    )
    parser.add_argument("--skip-deploy", action="store_true", help="Only verify the currently deployed endpoint.")
    parser.add_argument("--skip-chat-check", action="store_true", help="Verify /health and /v1/models only.")
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--chat-timeout", type=float, default=600.0)
    parser.add_argument("--write-env", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=project_root() / "runs" / "vllm-server" / "latest-deploy.json",
    )
    return parser


def _preparse_env_file(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    args, _ = parser.parse_known_args(argv)
    return args.env_file


def _gpu_count(gpu_config: str) -> int:
    if ":" not in gpu_config:
        return 1
    _, count = gpu_config.rsplit(":", 1)
    return int(count)


def _gpu_family(gpu_config: str) -> str:
    return gpu_config.split(":", 1)[0].upper()


def _is_expensive_gpu(gpu_config: str) -> bool:
    family = _gpu_family(gpu_config)
    return family in {"B200", "H200"} or _gpu_count(gpu_config) > 1


def _require_expensive_gpu_opt_in(gpu_config: str, *, allow_expensive_gpu: bool) -> None:
    if _is_expensive_gpu(gpu_config) and not allow_expensive_gpu:
        raise RuntimeError(
            f"Refusing to deploy expensive Modal GPU {gpu_config!r}. "
            "Pass --allow-expensive-gpu or set VLLM_ALLOW_EXPENSIVE_GPU=1."
        )


def _canonical_gpu(raw_gpu: str) -> str:
    gpu = raw_gpu.strip()
    normalized = gpu.upper()
    if normalized == "H100":
        return "H100:1"
    if normalized == "B200":
        return "B200"
    if normalized == "H200":
        return "H200"
    if normalized.startswith("H100:"):
        return "H100:" + normalized.rsplit(":", 1)[1]
    if normalized.startswith("A100-80GB:"):
        return "A100-80GB:" + normalized.rsplit(":", 1)[1]
    if normalized.startswith("H200:"):
        return "H200:" + normalized.rsplit(":", 1)[1]
    if normalized.startswith("B200:"):
        return "B200:" + normalized.rsplit(":", 1)[1]
    return gpu


def _flag_present(argv: list[str], name: str) -> bool:
    return any(arg == name or arg.startswith(f"{name}=") for arg in argv)


def _resolve_server_config(args: argparse.Namespace, cli_args: list[str]) -> VLLMServerConfig:
    gpu = _canonical_gpu(args.gpu)
    gpu_family = gpu.split(":", 1)[0].upper()
    gpu_count = _gpu_count(gpu)
    gpu_was_explicit = _flag_present(cli_args, "--gpu")
    model_was_explicit = _flag_present(cli_args, "--model")
    served_model_was_explicit = _flag_present(cli_args, "--served-model-name")

    if gpu_family == "H100" and gpu_count == 1:
        default_model = FP8_MODEL
        default_dtype = "auto"
        default_max_num_seqs = "8"
    else:
        default_model = DEFAULT_MODEL
        default_dtype = "bfloat16"
        default_max_num_seqs = "16"

    model = args.model or default_model
    if gpu_was_explicit and not model_was_explicit and model == DEFAULT_MODEL:
        model = default_model
    served_model_name = args.served_model_name or model
    if gpu_was_explicit and not served_model_was_explicit and served_model_name == DEFAULT_MODEL:
        served_model_name = model
    tensor_parallel_size = (
        args.tensor_parallel_size
        if args.tensor_parallel_size and (not gpu_was_explicit or _flag_present(cli_args, "--tensor-parallel-size"))
        else str(gpu_count)
    )
    max_model_len = args.max_model_len or "32768"
    max_num_seqs = (
        args.max_num_seqs
        if args.max_num_seqs and (not gpu_was_explicit or _flag_present(cli_args, "--max-num-seqs"))
        else default_max_num_seqs
    )
    gpu_memory_utilization = (
        args.gpu_memory_utilization
        if args.gpu_memory_utilization
        and (not gpu_was_explicit or _flag_present(cli_args, "--gpu-memory-utilization"))
        else "0.90"
    )
    dtype = args.dtype if args.dtype and (not gpu_was_explicit or _flag_present(cli_args, "--dtype")) else default_dtype

    return VLLMServerConfig(
        gpu=gpu,
        model=model,
        served_model_name=served_model_name,
        tensor_parallel_size=tensor_parallel_size,
        max_model_len=max_model_len,
        max_num_seqs=max_num_seqs,
        gpu_memory_utilization=gpu_memory_utilization,
        dtype=dtype,
        enable_mtp=args.enable_mtp,
        num_speculative_tokens=str(args.num_speculative_tokens),
        fast_boot=args.fast_boot,
        startup_timeout_seconds=args.startup_timeout_seconds,
        scaledown_window_seconds=args.scaledown_window_seconds,
    )


def _deploy_env(
    config: VLLMServerConfig,
    api_key: str,
    hf_token: str,
    *,
    allow_expensive_gpu: bool,
) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "VLLM_API_KEY": api_key,
            "VLLM_MODEL": config.model,
            "VLLM_SERVED_MODEL_NAME": config.served_model_name,
            "VLLM_MODAL_GPU": config.gpu,
            "VLLM_TENSOR_PARALLEL_SIZE": config.tensor_parallel_size,
            "VLLM_MAX_MODEL_LEN": config.max_model_len,
            "VLLM_MAX_NUM_SEQS": config.max_num_seqs,
            "VLLM_GPU_MEMORY_UTILIZATION": config.gpu_memory_utilization,
            "VLLM_DTYPE": config.dtype,
            "VLLM_ENABLE_MTP": "1" if config.enable_mtp else "0",
            "VLLM_NUM_SPECULATIVE_TOKENS": config.num_speculative_tokens,
            "VLLM_FAST_BOOT": "1" if config.fast_boot else "0",
            "VLLM_STARTUP_TIMEOUT_SECONDS": str(config.startup_timeout_seconds),
            "VLLM_SCALEDOWN_WINDOW_SECONDS": str(config.scaledown_window_seconds),
            "PYTHONUNBUFFERED": "1",
        }
    )
    if allow_expensive_gpu:
        env["VLLM_ALLOW_EXPENSIVE_GPU"] = "1"
    else:
        env.pop("VLLM_ALLOW_EXPENSIVE_GPU", None)
    if hf_token:
        env["HF_TOKEN"] = hf_token
    return env


def main(argv: list[str] | None = None) -> int:
    cli_args = list(sys.argv[1:] if argv is None else argv)
    load_project_env(_preparse_env_file(argv))
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    config = _resolve_server_config(args, cli_args)
    api_key = secrets.token_hex(32) if args.rotate_api_key else args.api_key
    if not api_key:
        return fail(
            "VLLM_API_KEY is required in .env or via --api-key. "
            "Run setup_vllm_modal_env.py once to create and sync it."
        )

    try:
        if args.rotate_api_key:
            args.sync_secret = True
            args.write_env = True
        if args.skip_secret:
            args.sync_secret = False

        if not args.skip_deploy:
            _require_expensive_gpu_opt_in(config.gpu, allow_expensive_gpu=args.allow_expensive_gpu)

        print("[deploy] resolved vLLM server config", flush=True)
        print(f"[deploy]   gpu={config.gpu}", flush=True)
        print(f"[deploy]   model={config.model}", flush=True)
        print(f"[deploy]   served_model_name={config.served_model_name}", flush=True)
        print(f"[deploy]   tensor_parallel_size={config.tensor_parallel_size}", flush=True)
        print(f"[deploy]   max_model_len={config.max_model_len}", flush=True)
        print(f"[deploy]   max_num_seqs={config.max_num_seqs}", flush=True)
        print(f"[deploy]   dtype={config.dtype}", flush=True)
        print(f"[deploy]   startup_timeout_seconds={config.startup_timeout_seconds}", flush=True)
        print(f"[deploy]   scaledown_window_seconds={config.scaledown_window_seconds}", flush=True)
        print(f"[deploy]   allow_expensive_gpu={args.allow_expensive_gpu}", flush=True)
        if config.model == FP8_MODEL:
            print("[deploy]   H100 profile selected the FP8 checkpoint for single-GPU serving", flush=True)

        if args.sync_secret:
            secret_values = {"VLLM_API_KEY": api_key}
            if args.hf_token:
                secret_values["HF_TOKEN"] = args.hf_token
            create_or_update_modal_secret(args.secret_name, secret_values, log_prefix="deploy")
        else:
            print(
                f"[deploy] using existing Modal secret {args.secret_name!r}; "
                "pass --sync-secret to update it from .env",
                flush=True,
            )

        deploy_env = _deploy_env(
            config,
            api_key,
            args.hf_token,
            allow_expensive_gpu=args.allow_expensive_gpu,
        )
        if not args.skip_deploy:
            _deploy_modal_app(deploy_env)

        server_root = _get_web_url(args.app_name)
        api_base = api_base_from_server_root(server_root)
        print("[deploy] resolved endpoint:", api_base, flush=True)
        print("[deploy] VLLM_API_KEY", redacted_length(api_key), flush=True)

        verification = verify_vllm_endpoint(
            api_base=api_base,
            api_key=api_key,
            served_model_name=config.served_model_name,
            wait_timeout=args.wait_timeout,
            request_timeout=args.request_timeout,
            chat_timeout=args.chat_timeout,
            skip_chat=args.skip_chat_check,
        )

        if args.write_env:
            upsert_dotenv_values(
                args.env_file,
                {
                    "VLLM_API_KEY": api_key,
                    "VLLM_API_BASE": api_base,
                    "VLLM_MODEL": config.model,
                    "VLLM_SERVED_MODEL_NAME": config.served_model_name,
                    "VLLM_MODAL_GPU": config.gpu,
                    "VLLM_TENSOR_PARALLEL_SIZE": config.tensor_parallel_size,
                    "VLLM_MAX_MODEL_LEN": config.max_model_len,
                    "VLLM_MAX_NUM_SEQS": config.max_num_seqs,
                    "VLLM_DTYPE": config.dtype,
                    "VLLM_STARTUP_TIMEOUT_SECONDS": str(config.startup_timeout_seconds),
                    "VLLM_SCALEDOWN_WINDOW_SECONDS": str(config.scaledown_window_seconds),
                },
            )
            print(f"[deploy] wrote vLLM settings to {args.env_file}", flush=True)

        write_json(
            args.metadata_path,
            {
                "api_base": api_base,
                "server_root": server_root,
                "model": config.model,
                "served_model_name": config.served_model_name,
                "app_name": args.app_name,
                "secret_name": args.secret_name,
                "gpu": config.gpu,
                "tensor_parallel_size": config.tensor_parallel_size,
                "max_model_len": config.max_model_len,
                "max_num_seqs": config.max_num_seqs,
                "dtype": config.dtype,
                "startup_timeout_seconds": config.startup_timeout_seconds,
                "scaledown_window_seconds": config.scaledown_window_seconds,
                "verification": verification.__dict__,
            },
        )
        print(f"[deploy] wrote metadata to {args.metadata_path}", flush=True)
        print("[deploy] vLLM endpoint verified", flush=True)
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
