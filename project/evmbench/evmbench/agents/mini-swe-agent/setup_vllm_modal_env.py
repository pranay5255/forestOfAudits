#!/usr/bin/env python3
"""Create or update the stable local vLLM .env values and Modal secret.

Run:
  uv run python evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py
"""

from __future__ import annotations

import argparse
import os
import secrets
import sys
from pathlib import Path

from deploy_vllm_server import (
    DEFAULT_GPU_CONFIG,
    DEFAULT_SCALEDOWN_WINDOW_SECONDS,
    DEFAULT_STARTUP_TIMEOUT_SECONDS,
    _get_web_url,
    _resolve_server_config,
)
from vllm_common import (
    DEFAULT_APP_NAME,
    DEFAULT_IMAGE_REPO,
    DEFAULT_SECRET_NAME,
    api_base_from_server_root,
    clean_env_value,
    create_or_update_modal_secret,
    env_bool,
    fail,
    litellm_model_name,
    load_project_env,
    project_root,
    redacted_length,
    upsert_dotenv_values,
)


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--app-name", default=clean_env_value(os.getenv("VLLM_MODAL_APP_NAME")) or DEFAULT_APP_NAME)
    parser.add_argument("--secret-name", default=clean_env_value(os.getenv("VLLM_MODAL_SECRET_NAME")) or DEFAULT_SECRET_NAME)
    parser.add_argument("--api-base", default=clean_env_value(os.getenv("VLLM_API_BASE")))
    parser.add_argument("--api-key", default=clean_env_value(os.getenv("VLLM_API_KEY")))
    parser.add_argument("--rotate-api-key", action="store_true", help="Generate and persist a fresh VLLM_API_KEY.")
    parser.add_argument("--hf-token", default=clean_env_value(os.getenv("HF_TOKEN")))
    parser.add_argument("--gpu", default=clean_env_value(os.getenv("VLLM_MODAL_GPU")) or DEFAULT_GPU_CONFIG)
    parser.add_argument("--model", default=clean_env_value(os.getenv("VLLM_MODEL")))
    parser.add_argument("--served-model-name", default=clean_env_value(os.getenv("VLLM_SERVED_MODEL_NAME")))
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
    parser.add_argument("--tool-call-parser", default=clean_env_value(os.getenv("VLLM_TOOL_CALL_PARSER")) or "qwen3_coder")
    parser.add_argument("--fast-boot", action=argparse.BooleanOptionalAction, default=env_bool("VLLM_FAST_BOOT", False))
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
    parser.add_argument(
        "--resolve-app-url",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Try to resolve VLLM_API_BASE from the deployed Modal app when it is not already set.",
    )
    parser.add_argument("--sync-secret", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--write-env", action=argparse.BooleanOptionalAction, default=True)
    return parser


def _preparse_env_file(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    args, _ = parser.parse_known_args(argv)
    return args.env_file


def main(argv: list[str] | None = None) -> int:
    cli_args = list(sys.argv[1:] if argv is None else argv)
    load_project_env(_preparse_env_file(argv))
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        config = _resolve_server_config(args, cli_args)
        if not args.write_env and (args.rotate_api_key or not args.api_key):
            raise RuntimeError("Refusing to generate an API key with --no-write-env; set VLLM_API_KEY first.")
        api_key = secrets.token_hex(32) if args.rotate_api_key or not args.api_key else args.api_key
        api_base = clean_env_value(args.api_base)
        if not api_base and args.resolve_app_url:
            try:
                api_base = api_base_from_server_root(_get_web_url(args.app_name))
            except Exception as exc:
                print(f"[setup] VLLM_API_BASE not resolved yet: {exc}", flush=True)

        if args.sync_secret:
            secret_values = {"VLLM_API_KEY": api_key}
            if args.hf_token:
                secret_values["HF_TOKEN"] = args.hf_token
            create_or_update_modal_secret(args.secret_name, secret_values, log_prefix="setup")

        if args.write_env:
            dotenv_values = {
                "VLLM_API_KEY": api_key,
                "VLLM_MODAL_APP_NAME": args.app_name,
                "VLLM_MODAL_SECRET_NAME": args.secret_name,
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
                "VLLM_TOOL_CALL_PARSER": config.tool_call_parser,
                "VLLM_FAST_BOOT": "1" if config.fast_boot else "0",
                "VLLM_STARTUP_TIMEOUT_SECONDS": str(config.startup_timeout_seconds),
                "VLLM_SCALEDOWN_WINDOW_SECONDS": str(config.scaledown_window_seconds),
                "VLLM_LITELLM_MODEL": litellm_model_name(config.served_model_name),
                "MODEL": litellm_model_name(config.served_model_name),
                "MODEL_KWARGS_JSON": '{"drop_params":true}',
                "MSWEA_COST_TRACKING": "ignore_errors",
                "MODAL_AUDIT_IMAGE_REPO": clean_env_value(os.getenv("MODAL_AUDIT_IMAGE_REPO")) or DEFAULT_IMAGE_REPO,
            }
            if api_base:
                dotenv_values["VLLM_API_BASE"] = api_base
            upsert_dotenv_values(args.env_file, dotenv_values)
            print(f"[setup] wrote vLLM settings to {args.env_file}", flush=True)

        print(f"[setup] VLLM_API_KEY {redacted_length(api_key)}", flush=True)
        if api_base:
            print(f"[setup] VLLM_API_BASE={api_base}", flush=True)
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
