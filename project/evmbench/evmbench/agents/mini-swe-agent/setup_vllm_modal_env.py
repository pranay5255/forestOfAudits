#!/usr/bin/env python3
"""Compatibility wrapper for the repo-level vLLM environment setup CLI."""

from evmbench.vllm.setup_env import *  # noqa: F401,F403
from evmbench.vllm.setup_env import main


if __name__ == "__main__":
    raise SystemExit(main())
