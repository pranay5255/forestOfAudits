"""Codex CLI harness adapter.

Codex talks to OpenRouter through the Responses wire API with stateless
requests (``store: false``, full history replayed by the CLI).  The adapter
preserves ``codex exec --json`` events and never falls back to chat
completions.
"""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Iterator

from evmbench.agents.backend.base import AGENT_DIR, LOGS_DIR, TaskComputer
from evmbench.agents.forest.artifacts import DEFAULT_MAX_TRAJECTORY_BYTES, ArtifactRequest

from .base import BaseHarnessAdapter, StageSpec, Usage

CODEX_VERSION = "0.164.0"
CODEX_LOG_DIR = f"{LOGS_DIR}/codex"
CODEX_EVENTS_PATH = f"{CODEX_LOG_DIR}/events.jsonl"
CODEX_STDERR_PATH = f"{CODEX_LOG_DIR}/stderr.log"
CODEX_PROMPT_PATH = f"{AGENT_DIR}/PROMPT.md"
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENAI_BASE_URL = "https://api.openai.com/v1"
# Always use a custom, env-key-authenticated provider.  Otherwise Codex
# prefers a cached ChatGPT login from ~/.codex/auth.json and rejects API models.
CODEX_PROVIDER_ID = "evmbench"


class CodexAdapter(BaseHarnessAdapter):
    name = "codex"
    cli_version = CODEX_VERSION
    version_command = "codex --version"
    trajectory_local_name = "trajectory.codex.jsonl"

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest:
        return ArtifactRequest(
            kind="trajectory",
            remote_path=CODEX_EVENTS_PATH,
            local_name=self.trajectory_local_name,
            max_bytes=DEFAULT_MAX_TRAJECTORY_BYTES,
        )

    def extra_staging(self, computer: TaskComputer, spec: StageSpec) -> None:
        computer.upload_text(self.prompt_for(spec) + "\n", CODEX_PROMPT_PATH)

    def build_command(self, spec: StageSpec) -> str:
        model, base_url, env_key, provider_name = codex_model_route(spec.model)
        provider = CODEX_PROVIDER_ID
        provider_config = [
            f"--config {shlex.quote(f'model_provider={provider}')}",
            f"--config {shlex.quote(f'model_providers.{provider}.name={provider_name}')}",
            f"--config {shlex.quote(f'model_providers.{provider}.base_url={base_url}')}",
            f"--config {shlex.quote(f'model_providers.{provider}.env_key={env_key}')}",
            f"--config {shlex.quote(f'model_providers.{provider}.wire_api=responses')}",
        ]
        return "\n".join(
            [
                "set -o pipefail",
                f"mkdir -p {shlex.quote(CODEX_LOG_DIR)}",
                "PROMPT=\"$(cat " + shlex.quote(CODEX_PROMPT_PATH) + ")\"",
                " ".join(
                    [
                        "codex exec",
                        f"--model {shlex.quote(model)}",
                        *provider_config,
                        "--config web_search=disabled",
                        "--dangerously-bypass-approvals-and-sandbox",
                        "--skip-git-repo-check",
                        "--json",
                    ]
                )
                + f" \"$PROMPT\" 2> {shlex.quote(CODEX_STDERR_PATH)}"
                + f" | tee {shlex.quote(CODEX_EVENTS_PATH)}",
            ]
        )

    def count_tool_turns(self, trajectory: bytes) -> int:
        return count_codex_tool_turns(trajectory)

    def parse_usage(self, stdout: str, stderr: str) -> Usage:
        return parse_codex_usage(stdout)


def codex_model_route(model: str) -> tuple[str, str, str, str]:
    """Return ``(model_id, base_url, env_key, provider_name)`` for Codex.

    A custom provider is required so Codex never falls back to a cached
    ChatGPT login.  ``openai/`` is stripped because Codex expects the bare
    OpenAI model id; OpenRouter keeps the full namespaced id.

    ``EVMBENCH_CODEX_BASE_URL`` overrides the endpoint for self-hosted
    gateways and deterministic tests.
    """
    from .routing import is_direct_openai

    override = (os.getenv("EVMBENCH_CODEX_BASE_URL") or "").strip()
    if override:
        env_key = os.getenv("EVMBENCH_CODEX_ENV_KEY", "OPENAI_API_KEY")
        return model.split("/", 1)[-1], override, env_key, "EVMBench"
    if is_direct_openai():
        return model.split("/", 1)[-1], OPENAI_BASE_URL, "OPENAI_API_KEY", "OpenAI"
    return model, OPENROUTER_BASE_URL, "OPENROUTER_API_KEY", "OpenRouter"


def _iter_events(trajectory: bytes) -> Iterator[Any]:
    for line in trajectory.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def count_codex_tool_turns(trajectory: bytes) -> int:
    seen: set[str] = set()
    turns = 0
    for event in _iter_events(trajectory):
        if not isinstance(event, dict):
            continue
        item = event.get("item") if isinstance(event.get("item"), dict) else None
        if not item:
            continue
        item_type = str(item.get("type", ""))
        if item_type not in {"command_execution", "function_call", "tool_call"}:
            continue
        # item.started + item.completed describe the same call.
        item_id = str(item.get("id") or f"anon-{turns}")
        if item_id in seen:
            continue
        seen.add(item_id)
        turns += 1
    return turns


def parse_codex_usage(stdout: str) -> Usage:
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    for event in _iter_events(stdout.encode("utf-8")):
        if not isinstance(event, dict):
            continue
        usage = event.get("usage") if isinstance(event.get("usage"), dict) else None
        if usage:
            input_tokens = max(input_tokens, int(usage.get("input_tokens") or 0))
            output_tokens = max(output_tokens, int(usage.get("output_tokens") or 0))
        if isinstance(event.get("cost_usd"), (int, float)):
            cost += float(event["cost_usd"])
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)


__all__ = ["CODEX_VERSION", "CodexAdapter", "count_codex_tool_turns", "parse_codex_usage"]
