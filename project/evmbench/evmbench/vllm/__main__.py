#!/usr/bin/env python3
"""Repo-level vLLM CLI for deployment, verification, and metrics."""

from __future__ import annotations

import argparse
import sys

from evmbench.vllm import deploy, metrics, runner, setup_env
from evmbench.vllm.common import fail, load_project_env, require_env, verify_vllm_endpoint


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    subparsers.add_parser("deploy", help="Deploy and verify the Modal vLLM endpoint.")
    subparsers.add_parser("setup-env", help="Write local vLLM env values and sync Modal secret.")

    verify = subparsers.add_parser("verify", help="Verify health, models, and chat completion.")
    verify.add_argument("--env-file", default=None)
    verify.add_argument("--api-base", default=None)
    verify.add_argument("--api-key", default=None)
    verify.add_argument("--served-model-name", default=None)
    verify.add_argument("--skip-chat-check", action="store_true")
    verify.add_argument("--wait-timeout", type=float, default=1800.0)
    verify.add_argument("--request-timeout", type=float, default=300.0)
    verify.add_argument("--chat-timeout", type=float, default=600.0)

    subparsers.add_parser("metrics", help="Use `python -m evmbench.vllm metrics ...` for metrics commands.")
    subparsers.add_parser("run-harness", help="Run one direct vLLM-backed EVMBench harness task.")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args[:1] == ["deploy"]:
        return deploy.main(raw_args[1:])
    if raw_args[:1] == ["setup-env"]:
        return setup_env.main(raw_args[1:])
    if raw_args[:1] == ["metrics"]:
        return metrics.main(raw_args[1:] or ["snapshot"])
    if raw_args[:1] == ["run-harness"]:
        return runner.main(raw_args[1:])

    parser = build_arg_parser()
    args = parser.parse_args(raw_args)
    if args.command == "verify":
        try:
            load_project_env(args.env_file)
            api_base = args.api_base or require_env("VLLM_API_BASE")
            api_key = args.api_key or require_env("VLLM_API_KEY")
            served_model_name = args.served_model_name or require_env("VLLM_SERVED_MODEL_NAME")
            verification = verify_vllm_endpoint(
                api_base=api_base,
                api_key=api_key,
                served_model_name=served_model_name,
                wait_timeout=args.wait_timeout,
                request_timeout=args.request_timeout,
                chat_timeout=args.chat_timeout,
                skip_chat=args.skip_chat_check,
            )
            print(verification)
            return 0
        except Exception as exc:
            return fail(str(exc))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
