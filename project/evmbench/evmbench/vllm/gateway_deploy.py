#!/usr/bin/env python3
"""Deploy the Modal vLLM compatibility gateway."""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

from evmbench.vllm.common import (
    api_base_from_server_root,
    clean_env_value,
    fail,
    load_project_env,
    modal_binary,
    project_root,
    redacted_length,
    require_env,
    upsert_dotenv_values,
    verify_vllm_endpoint,
    write_json,
)
from evmbench.vllm.gateway import GATEWAY_APP_NAME, UPSTREAM_API_BASE_ENV


def _deploy_file() -> Path:
    return Path(__file__).resolve().parent / "gateway.py"


def _normalize_api_base(value: str) -> str:
    base = value.strip().rstrip("/")
    if not base:
        raise RuntimeError("--upstream-api-base cannot be empty.")
    return base if base.endswith("/v1") else f"{base}/v1"


def _get_web_url(app_name: str) -> str:
    try:
        import modal
    except ModuleNotFoundError as exc:
        raise RuntimeError("The `modal` package is required to look up the deployed gateway URL.") from exc

    fn = modal.Function.from_name(app_name, "serve")
    url = fn.get_web_url()
    if not url:
        raise RuntimeError(f"Modal did not return a web URL for {app_name}.serve.")
    return url.rstrip("/")


def _deploy_modal_gateway(env: dict[str, str]) -> None:
    command = [modal_binary(), "deploy", str(_deploy_file())]
    print("[gateway] deploying Modal compatibility gateway", flush=True)
    completed = subprocess.run(command, cwd=project_root(), env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"Modal gateway deploy failed with exit code {completed.returncode}.")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--app-name", default=GATEWAY_APP_NAME)
    parser.add_argument(
        "--upstream-api-base",
        default=clean_env_value(os.getenv(UPSTREAM_API_BASE_ENV)) or clean_env_value(os.getenv("VLLM_API_BASE")),
        help="Raw vLLM API base. Accepts either the server root or the /v1 API base.",
    )
    parser.add_argument("--api-key", default=clean_env_value(os.getenv("VLLM_API_KEY")))
    parser.add_argument("--served-model-name", default=clean_env_value(os.getenv("VLLM_SERVED_MODEL_NAME")))
    parser.add_argument("--skip-deploy", action="store_true")
    parser.add_argument("--skip-verify", action="store_true")
    parser.add_argument("--skip-chat-check", action="store_true")
    parser.add_argument("--wait-timeout", type=float, default=1800.0)
    parser.add_argument("--request-timeout", type=float, default=300.0)
    parser.add_argument("--chat-timeout", type=float, default=600.0)
    parser.add_argument("--write-env", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--metadata-path",
        type=Path,
        default=project_root() / "runs" / "vllm-gateway" / "latest-deploy.json",
    )
    return parser


def _preparse_env_file(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    args, _ = parser.parse_known_args(argv)
    return args.env_file


def main(argv: list[str] | None = None) -> int:
    load_project_env(_preparse_env_file(argv))
    parser = build_arg_parser()
    args = parser.parse_args(argv)

    try:
        upstream_api_base = _normalize_api_base(clean_env_value(args.upstream_api_base))
        api_key = clean_env_value(args.api_key) or require_env("VLLM_API_KEY")
        served_model_name = clean_env_value(args.served_model_name) or require_env("VLLM_SERVED_MODEL_NAME")

        deploy_env = os.environ.copy()
        deploy_env[UPSTREAM_API_BASE_ENV] = upstream_api_base
        deploy_env["VLLM_API_KEY"] = api_key
        deploy_env["PYTHONUNBUFFERED"] = "1"

        print("[gateway] resolved gateway config", flush=True)
        print(f"[gateway]   upstream_api_base={upstream_api_base}", flush=True)
        print(f"[gateway]   app_name={args.app_name}", flush=True)
        print(f"[gateway]   served_model_name={served_model_name}", flush=True)
        print(f"[gateway]   VLLM_API_KEY {redacted_length(api_key)}", flush=True)

        if not args.skip_deploy:
            _deploy_modal_gateway(deploy_env)

        gateway_root = _get_web_url(args.app_name)
        gateway_api_base = api_base_from_server_root(gateway_root)
        print(f"[gateway] resolved endpoint: {gateway_api_base}", flush=True)

        verification = None
        if not args.skip_verify:
            verification = verify_vllm_endpoint(
                api_base=gateway_api_base,
                api_key=api_key,
                served_model_name=served_model_name,
                wait_timeout=args.wait_timeout,
                request_timeout=args.request_timeout,
                chat_timeout=args.chat_timeout,
                skip_chat=args.skip_chat_check,
            )

        if args.write_env:
            upsert_dotenv_values(
                args.env_file,
                {
                    UPSTREAM_API_BASE_ENV: upstream_api_base,
                    "VLLM_API_BASE": gateway_api_base,
                    "VLLM_API_KEY": api_key,
                    "VLLM_SERVED_MODEL_NAME": served_model_name,
                },
            )
            print(
                f"[gateway] wrote harness-facing VLLM_API_BASE and preserved {UPSTREAM_API_BASE_ENV} in {args.env_file}",
                flush=True,
            )

        write_json(
            args.metadata_path,
            {
                "gateway_api_base": gateway_api_base,
                "gateway_root": gateway_root,
                "upstream_api_base": upstream_api_base,
                "app_name": args.app_name,
                "served_model_name": served_model_name,
                "verification": verification.__dict__ if verification is not None else None,
            },
        )
        print(f"[gateway] wrote metadata to {args.metadata_path}", flush=True)
        return 0
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
