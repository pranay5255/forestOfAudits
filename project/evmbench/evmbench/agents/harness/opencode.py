"""OpenCode harness adapter.

The adapter runs ``opencode run --format json`` inside the stage sandbox and
preserves the JSONL event stream.  It never scrapes human-readable terminal
output when the structured stream is available.
"""

from __future__ import annotations

import json
import os
import shlex
from typing import Any, Iterator

from evmbench.agents.backend.base import AGENT_DIR, LOGS_DIR, TaskComputer
from evmbench.agents.forest.artifacts import DEFAULT_MAX_TRAJECTORY_BYTES, ArtifactRequest

from .base import BaseHarnessAdapter, StageSpec, Usage

OPENCODE_VERSION = "1.19.4"
OPENCODE_TRACE_DIR = f"{LOGS_DIR}/opencode"
OPENCODE_EVENTS_PATH = f"{OPENCODE_TRACE_DIR}/events.jsonl"
OPENCODE_STDERR_PATH = f"{OPENCODE_TRACE_DIR}/stderr.log"
OPENCODE_CONFIG_PATH = f"{AGENT_DIR}/opencode.json"
OPENCODE_PROMPT_PATH = f"{AGENT_DIR}/PROMPT.md"


class OpenCodeAdapter(BaseHarnessAdapter):
    name = "opencode"
    cli_version = OPENCODE_VERSION
    version_command = "opencode --version"
    trajectory_local_name = "trajectory.opencode.jsonl"

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest:
        return ArtifactRequest(
            kind="trajectory",
            remote_path=OPENCODE_EVENTS_PATH,
            local_name=self.trajectory_local_name,
            max_bytes=DEFAULT_MAX_TRAJECTORY_BYTES,
        )

    def extra_staging(self, computer: TaskComputer, spec: StageSpec) -> None:
        computer.upload_text(self.prompt_for(spec) + "\n", OPENCODE_PROMPT_PATH)
        provider_id, model_id = opencode_model_route(spec.model)
        override = (os.getenv("EVMBENCH_OPENCODE_BASE_URL") or "").strip()
        config: dict[str, object] = {
            "$schema": "https://opencode.ai/config.json",
            # EVMBench stages write submission/log artifacts outside the audit
            # working directory; without this OpenCode blocks on an interactive
            # permission prompt and the stage times out.
            "permission": {
                "bash": "allow",
                "edit": "allow",
                "external_directory": "allow",
            },
        }
        if override:
            # Self-hosted gateway or deterministic test endpoint.
            key_env = os.getenv(
                "EVMBENCH_OPENCODE_API_KEY_ENV",
                "OPENAI_API_KEY" if provider_id == "openai" else "OPENROUTER_API_KEY",
            )
            config["provider"] = {
                provider_id: {
                    "name": "EVMBench",
                    "npm": "@ai-sdk/openai-compatible",
                    "options": {"baseURL": override, "apiKey": f"{{env:{key_env}}}"},
                    "models": {model_id: {}},
                }
            }
        computer.upload_text(json.dumps(config, indent=2) + "\n", OPENCODE_CONFIG_PATH)

    def stage_environment(self, spec: StageSpec) -> dict[str, str]:
        env = super().stage_environment(spec)
        env.setdefault("OPENCODE_CONFIG", OPENCODE_CONFIG_PATH)
        env.setdefault("OPENCODE_TRACE_DIR", OPENCODE_TRACE_DIR)
        return env

    def build_command(self, spec: StageSpec) -> str:
        provider_id, model_id = opencode_model_route(spec.model)
        run_model = f"{provider_id}/{model_id}"
        return "\n".join(
            [
                "set -o pipefail",
                f"mkdir -p {shlex.quote(OPENCODE_TRACE_DIR)}",
                "PROMPT=\"$(cat " + shlex.quote(OPENCODE_PROMPT_PATH) + ")\"",
                "opencode run"
                f" --model {shlex.quote(run_model)}"
                " --format json"
                f" \"$PROMPT\" 2> {shlex.quote(OPENCODE_STDERR_PATH)}"
                f" | tee {shlex.quote(OPENCODE_EVENTS_PATH)}",
            ]
        )

    def count_tool_turns(self, trajectory: bytes) -> int:
        return _count_opencode_tool_turns(trajectory)

    def parse_usage(self, stdout: str, stderr: str) -> Usage:
        return _parse_opencode_usage(stdout)


def opencode_model_route(model: str) -> tuple[str, str]:
    """Return ``(provider_id, model_id)`` for OpenCode.

    OpenRouter keeps the full namespaced model id; direct OpenAI strips the
    ``openai/`` prefix so OpenCode's built-in providers are used.  Using the
    built-in providers avoids stale credentials in OpenCode's auth store taking
    precedence over a custom provider.
    """
    from .routing import is_direct_openai

    if is_direct_openai():
        return "openai", model.split("/", 1)[-1]
    return "openrouter", model


def _iter_events(trajectory: bytes) -> Iterator[Any]:
    for line in trajectory.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            yield json.loads(line)
        except json.JSONDecodeError:
            continue


def _count_opencode_tool_turns(trajectory: bytes) -> int:
    seen: set[str] = set()
    turns = 0
    for event in _iter_events(trajectory):
        if not isinstance(event, dict):
            continue
        part = event.get("part") if isinstance(event.get("part"), dict) else None
        if not part or part.get("type") != "tool":
            continue
        # Count each tool call once; OpenCode emits start/completed updates for
        # the same callID.
        call_id = str(part.get("callID") or part.get("id") or f"anon-{turns}")
        if call_id in seen:
            continue
        seen.add(call_id)
        turns += 1
    return turns


def _parse_opencode_usage(stdout: str) -> Usage:
    input_tokens = 0
    output_tokens = 0
    cost = 0.0
    for event in _iter_events(stdout.encode("utf-8")):
        if not isinstance(event, dict):
            continue
        tokens = event.get("tokens") or event.get("usage")
        if isinstance(tokens, dict):
            input_tokens = max(input_tokens, int(tokens.get("input") or 0))
            output_tokens = max(output_tokens, int(tokens.get("output") or 0))
        if isinstance(event.get("cost"), (int, float)):
            cost += float(event["cost"])
    return Usage(input_tokens=input_tokens, output_tokens=output_tokens, cost_usd=cost)


__all__ = ["OpenCodeAdapter", "OPENCODE_VERSION"]
