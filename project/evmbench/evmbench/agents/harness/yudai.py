"""Yudai harness adapter.

Yudai is installed into its own virtualenv (``/opt/yudai/venv``) at image build
time.  The adapter uploads the benchmark-owned stage launcher and runs it with
that interpreter.  The launcher enforces native tool calling; a text-only
response is a protocol failure and can never be parsed as a fenced command.
"""

from __future__ import annotations

import json
import os
import shlex
from pathlib import Path

from evmbench.agents.backend.base import AGENT_DIR, LOGS_DIR, TaskComputer
from evmbench.agents.forest.artifacts import DEFAULT_MAX_TRAJECTORY_BYTES, ArtifactRequest

from .base import BaseHarnessAdapter, StageSpec, Usage

YUD_AI_COMMIT = "5a5541251a48da685a5b0bc2a00596771c7ded07"
YUD_AI_VENV = os.getenv("EVMBENCH_YUDAI_VENV", "/opt/yudai/venv")
LAUNCHER_REMOTE_PATH = f"{AGENT_DIR}/yudai/stage_launcher.py"
SPEC_REMOTE_PATH = f"{AGENT_DIR}/yudai/spec.json"
YUD_AI_LOG_DIR = f"{LOGS_DIR}/yudai"
YUD_AI_TRAJECTORY_PATH = f"{YUD_AI_LOG_DIR}/trajectory.jsonl"
YUD_AI_RESULT_PATH = f"{YUD_AI_LOG_DIR}/result.json"
YUD_AI_STDERR_PATH = f"{YUD_AI_LOG_DIR}/stderr.log"


def _launcher_source() -> str:
    from evmbench.agents.yudai import stage_launcher

    return Path(stage_launcher.__file__).read_text(encoding="utf-8")


class YudaiAdapter(BaseHarnessAdapter):
    name = "yudai"
    cli_version = YUD_AI_COMMIT
    # Provenance is baked into the image by scripts/build_harness_image.sh.
    version_command = (
        "python3 -c \"import json; d=json.load(open('/etc/evmbench/harness-pins.json')); "
        "print(d['yudai_commit'] if d.get('yudai_installed') else '')\""
    )
    trajectory_local_name = "trajectory.yudai.jsonl"

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest:
        return ArtifactRequest(
            kind="trajectory",
            remote_path=YUD_AI_TRAJECTORY_PATH,
            local_name=self.trajectory_local_name,
            max_bytes=DEFAULT_MAX_TRAJECTORY_BYTES,
        )

    def extra_staging(self, computer: TaskComputer, spec: StageSpec) -> None:
        computer.upload_text(_launcher_source(), LAUNCHER_REMOTE_PATH)
        payload = {
            "model": spec.model,
            "task": self.prompt_for(spec),
            "instructions": spec.instructions,
            "step_limit": spec.step_limit,
            "cost_limit": spec.cost_limit,
            "time_limit_seconds": spec.time_limit_seconds,
            "output_paths": list(spec.output_paths),
            "staged_artifacts": [
                {"remote_path": staged.remote_path} for staged in spec.staged_artifacts
            ],
            "workdir": spec.workdir,
            "env": spec.env,
            # Yudai runs one shell command per turn.  A long `forge test` or
            # `npm install` must not consume the whole stage budget.
            "command_timeout": int(os.getenv("EVMBENCH_YUDAI_COMMAND_TIMEOUT", "120")),
        }
        computer.upload_text(json.dumps(payload, indent=2) + "\n", SPEC_REMOTE_PATH)

    def build_command(self, spec: StageSpec) -> str:
        return "\n".join(
            [
                f"mkdir -p {shlex.quote(YUD_AI_LOG_DIR)}",
                "rm -f " + shlex.quote(YUD_AI_TRAJECTORY_PATH),
                f"{shlex.quote(YUD_AI_VENV + '/bin/python')} {shlex.quote(LAUNCHER_REMOTE_PATH)}"
                f" --spec {shlex.quote(SPEC_REMOTE_PATH)}"
                f" --result {shlex.quote(YUD_AI_RESULT_PATH)}"
                f" --trajectory {shlex.quote(YUD_AI_TRAJECTORY_PATH)}"
                f" 2> {shlex.quote(YUD_AI_STDERR_PATH)}",
                "status=$?",
                f"test -s {shlex.quote(YUD_AI_RESULT_PATH)} || {{ cat {shlex.quote(YUD_AI_STDERR_PATH)} >&2; exit $status; }}",
                f"cat {shlex.quote(YUD_AI_RESULT_PATH)} >&2 || cat {shlex.quote(YUD_AI_STDERR_PATH)} >&2 || true",
                "exit $status",
            ]
        )

    def count_tool_turns(self, trajectory: bytes) -> int:
        return count_yudai_tool_turns(trajectory)

    def parse_usage(self, stdout: str, stderr: str) -> Usage:
        return Usage()


def count_yudai_tool_turns(trajectory: bytes) -> int:
    turns = 0
    for line in trajectory.decode("utf-8", errors="replace").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        # Current launcher format: assistant messages carry native tool_calls.
        if event.get("type") == "message":
            if event.get("role") == "assistant" and event.get("tool_calls"):
                turns += 1
        # Backwards compatibility with the earlier tool_result format.
        elif event.get("type") == "tool_result":
            turns += 1
    return turns


__all__ = ["YUD_AI_COMMIT", "YUD_AI_VENV", "YudaiAdapter", "count_yudai_tool_turns"]
