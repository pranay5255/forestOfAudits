#!/usr/bin/env python3
"""Compatibility wrapper for the repo-level vLLM deploy CLI."""

from evmbench.vllm import deploy as _deploy
from evmbench.vllm.deploy import *  # noqa: F401,F403
from evmbench.vllm.deploy import main

FP8_MODEL = _deploy.FP8_MODEL
DEFAULT_GPU_CONFIG = _deploy.DEFAULT_GPU_CONFIG
DEFAULT_PROFILE_VOLUME_NAME = _deploy.DEFAULT_PROFILE_VOLUME_NAME
DEFAULT_SCALEDOWN_WINDOW_SECONDS = _deploy.DEFAULT_SCALEDOWN_WINDOW_SECONDS
DEFAULT_STARTUP_TIMEOUT_SECONDS = _deploy.DEFAULT_STARTUP_TIMEOUT_SECONDS
VLLMServerConfig = _deploy.VLLMServerConfig
build_arg_parser = _deploy.build_arg_parser
_deploy_env = _deploy._deploy_env
_require_expensive_gpu_opt_in = _deploy._require_expensive_gpu_opt_in
_resolve_server_config = _deploy._resolve_server_config

if __name__ == "__main__":
    raise SystemExit(main())
