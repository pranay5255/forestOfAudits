from __future__ import annotations

import json
from pathlib import Path

from evmbench.agents.backend.local import LocalProcessComputer
from evmbench.agents.forest.artifacts import ArtifactRequest
from evmbench.agents.harness.base import (
    BaseHarnessAdapter,
    StageSpec,
    WorkerStatus,
    normalize_status,
)
from evmbench.agents.harness.codex import (
    CodexAdapter,
    codex_model_route,
    count_codex_tool_turns,
)
from evmbench.agents.harness.opencode import OpenCodeAdapter, opencode_model_route
from evmbench.agents.harness.yudai import YudaiAdapter, count_yudai_tool_turns


def make_spec(tmp_path: Path, **overrides) -> StageSpec:
    data = dict(
        campaign_id="cmp-1",
        task_id="audit-1",
        attempt_id=0,
        stage="branch",
        worker_id="accounting-branch-01",
        mode="detect",
        harness="fake",
        strategy="forest",
        model="deepseek/deepseek-v4.1-flash-1017",
        image="image:latest",
        instructions="instructions",
        task="task",
        role="accounting",
        output_paths=(),
        artifact_root=str(tmp_path / "artifacts"),
    )
    data.update(overrides)
    return StageSpec(**data)


class ScriptedAdapter(BaseHarnessAdapter):
    name = "fake"
    cli_version = "1"

    def __init__(self, script: str) -> None:
        self.script = script

    def build_command(self, spec: StageSpec) -> str:
        return self.script

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest:
        return ArtifactRequest(
            kind="trajectory",
            remote_path="/home/logs/traj.jsonl",
            local_name="trajectory.jsonl",
        )


BRANCH_SCRIPT = """
set -eu
mkdir -p /home/logs /home/agent/forest/accounting/branch-01
printf '{"type":"tool_use"}\\n' > /home/logs/traj.jsonl
printf '# Branch report\\n' > /home/agent/forest/accounting/branch-01/branch.md
"""


def test_normalize_status_matrix():
    assert normalize_status(exit_code=0) is WorkerStatus.COMPLETED
    assert normalize_status(exit_code=1) is WorkerStatus.FAILED
    assert normalize_status(exit_code=1, expected_missing=True) is WorkerStatus.FAILED
    assert normalize_status(exit_code=0, expected_missing=True) is WorkerStatus.NO_SUBMISSION
    assert (
        normalize_status(exit_code=0, contract_violations=["forbidden"])
        is WorkerStatus.CONTRACT_VIOLATION
    )
    assert normalize_status(exit_code=124) is WorkerStatus.TIMEOUT
    assert normalize_status(exit_code=0, cancelled=True) is WorkerStatus.CANCELLED
    assert normalize_status(exit_code=0, preflight_failed=True) is WorkerStatus.PREFLIGHT_FAILED
    assert normalize_status(exit_code=0, adapter_error="boom") is WorkerStatus.FAILED
    assert (
        normalize_status(exit_code=0, trajectory_required=True, trajectory_present=False)
        is WorkerStatus.CONTRACT_VIOLATION
    )


def test_base_adapter_completes_and_collects_artifacts(tmp_path):
    adapter = ScriptedAdapter(BRANCH_SCRIPT)
    spec = make_spec(tmp_path)
    computer = LocalProcessComputer.create(root=tmp_path / "box")
    try:
        result = adapter.run(computer, spec)
    finally:
        computer.terminate()

    assert result.status is WorkerStatus.COMPLETED
    assert result.exit_code == 0
    assert {artifact.kind for artifact in result.artifacts} == {"report"}
    assert result.trajectory is not None
    assert (Path(spec.artifact_root) / "accounting-branch-01/branch.md").exists()
    assert (Path(spec.artifact_root) / "trajectory.jsonl").exists()
    payload = result.to_dict()
    assert payload["schema_version"] == 1
    assert payload["status"] == "completed"


def test_nonzero_exit_with_artifact_is_failed(tmp_path):
    script = BRANCH_SCRIPT + "\nexit 7\n"
    adapter = ScriptedAdapter(script)
    spec = make_spec(tmp_path)
    computer = LocalProcessComputer.create(root=tmp_path / "box")
    try:
        result = adapter.run(computer, spec)
    finally:
        computer.terminate()
    assert result.status is WorkerStatus.FAILED
    assert result.exit_code == 7


def test_zero_exit_missing_artifact_is_no_submission(tmp_path):
    adapter = ScriptedAdapter("mkdir -p /home/logs && printf '{}' > /home/logs/traj.jsonl")
    spec = make_spec(tmp_path)
    computer = LocalProcessComputer.create(root=tmp_path / "box")
    try:
        result = adapter.run(computer, spec)
    finally:
        computer.terminate()
    assert result.status is WorkerStatus.NO_SUBMISSION


def test_branch_writing_submission_is_contract_violation(tmp_path):
    script = BRANCH_SCRIPT + "\nmkdir -p /home/agent/submission && echo x > /home/agent/submission/audit.md\n"
    adapter = ScriptedAdapter(script)
    spec = make_spec(tmp_path)
    computer = LocalProcessComputer.create(root=tmp_path / "box")
    try:
        result = adapter.run(computer, spec)
    finally:
        computer.terminate()
    assert result.status is WorkerStatus.CONTRACT_VIOLATION
    assert "submission" in (result.failure_reason or "")


def test_timeout_status(tmp_path):
    adapter = ScriptedAdapter("exit 124")
    spec = make_spec(tmp_path)
    computer = LocalProcessComputer.create(root=tmp_path / "box")
    try:
        result = adapter.run(computer, spec)
    finally:
        computer.terminate()
    assert result.status is WorkerStatus.TIMEOUT


def test_adapter_commands_preserve_native_events(tmp_path):
    opencode = OpenCodeAdapter()
    codex = CodexAdapter()
    yudai = YudaiAdapter()
    spec = make_spec(tmp_path)

    opencode_cmd = opencode.build_command(spec)
    assert "opencode run" in opencode_cmd
    assert "--format json" in opencode_cmd
    assert "tee" in opencode_cmd

    codex_cmd = codex.build_command(spec)
    assert "codex exec" in codex_cmd
    assert "model_provider=evmbench" in codex_cmd
    assert "wire_api=responses" in codex_cmd
    assert "--json" in codex_cmd
    # Codex must never fall back to a cached ChatGPT login.
    assert "env_key=OPENROUTER_API_KEY" in codex_cmd

    yudai_cmd = yudai.build_command(spec)
    assert "/opt/yudai/venv/bin/python" in yudai_cmd
    assert "stage_launcher.py" in yudai_cmd


def test_opencode_config_allows_external_artifact_paths(tmp_path):
    adapter = OpenCodeAdapter()
    spec = make_spec(tmp_path, harness="opencode")
    computer = LocalProcessComputer.create(root=tmp_path / "config-box")
    try:
        adapter.extra_staging(computer, spec)
        config = json.loads(computer.download_text("/home/agent/opencode.json"))
    finally:
        computer.terminate()
    assert config["permission"]["bash"] == "allow"
    assert config["permission"]["external_directory"] == "allow"
    assert config["permission"]["edit"] == "allow"


def test_preflight_prompt_points_at_preflight_file(tmp_path):
    spec = make_spec(tmp_path, stage="preflight")
    prompt = BaseHarnessAdapter.prompt_for(spec)
    assert "/home/logs/preflight/PREFLIGHT.md" in prompt
    assert "COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT" in prompt


def test_trajectory_turn_counting():
    opencode_events = "\n".join(
        [
            json.dumps({"type": "tool_use", "part": {"type": "tool"}}),
            json.dumps({"type": "text"}),
            json.dumps({"type": "tool_use", "part": {"type": "tool"}}),
        ]
    )
    assert OpenCodeAdapter().count_tool_turns(opencode_events.encode()) == 2

    codex_events = "\n".join(
        [
            json.dumps({"type": "item.completed", "item": {"type": "command_execution"}}),
            json.dumps({"type": "item.completed", "item": {"type": "function_call"}}),
        ]
    )
    assert count_codex_tool_turns(codex_events.encode()) == 2

    yudai_events = "\n".join(
        [
            json.dumps({"type": "assistant"}),
            json.dumps({"type": "tool_result"}),
            json.dumps({"type": "tool_result"}),
        ]
    )
    assert count_yudai_tool_turns(yudai_events.encode()) == 2


def test_model_routing_defaults_to_openrouter(monkeypatch):
    monkeypatch.delenv("EVMBENCH_MODEL_PROVIDER", raising=False)
    assert codex_model_route("openai/gpt-5.4-mini") == (
        "openai/gpt-5.4-mini",
        "https://openrouter.ai/api/v1",
        "OPENROUTER_API_KEY",
        "OpenRouter",
    )
    assert opencode_model_route("openai/gpt-5.4-mini") == ("openrouter", "openai/gpt-5.4-mini")


def test_model_routing_direct_openai(monkeypatch):
    monkeypatch.setenv("EVMBENCH_MODEL_PROVIDER", "openai")
    assert codex_model_route("openai/gpt-5.4-mini") == (
        "gpt-5.4-mini",
        "https://api.openai.com/v1",
        "OPENAI_API_KEY",
        "OpenAI",
    )
    assert opencode_model_route("openai/gpt-5.4-mini") == ("openai", "gpt-5.4-mini")


def test_preflight_rejects_cli_version_mismatch(tmp_path, monkeypatch):
    monkeypatch.delenv("EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH", raising=False)

    class WrongVersion(ScriptedAdapter):
        cli_version = "1.0.0"
        version_command = "echo 9.9.9"

    spec = make_spec(tmp_path, harness="wrong")
    computer = LocalProcessComputer.create(root=tmp_path / "wrong")
    try:
        result = WrongVersion(
            """
set -eu
mkdir -p /home/logs/preflight /home/logs
printf '{"type":"tool_use"}\\n{"type":"tool_use"}\\n' > /home/logs/traj.jsonl
printf 'EVMBENCH_PREFLIGHT_OK_9f2c1b\\n' > /home/logs/preflight/proof.txt
"""
        ).preflight(computer, spec)
    finally:
        computer.terminate()
    assert not result.passed
    assert result.version_matches is False
    assert "mismatch" in (result.reason or "")

    monkeypatch.setenv("EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH", "1")
    computer = LocalProcessComputer.create(root=tmp_path / "wrong-allowed")
    try:
        allowed = WrongVersion(
            """
set -eu
mkdir -p /home/logs/preflight /home/logs
printf '{"type":"tool_use"}\\n{"type":"tool_use"}\\n' > /home/logs/traj.jsonl
printf 'EVMBENCH_PREFLIGHT_OK_9f2c1b\\n' > /home/logs/preflight/proof.txt
"""
        ).preflight(computer, spec)
    finally:
        computer.terminate()
    assert allowed.passed


def test_preflight_requires_file_write_and_two_turns(tmp_path):
    good = ScriptedAdapter(
        """
set -eu
mkdir -p /home/logs/preflight /home/logs
printf '{"type":"tool_use"}\\n{"type":"tool_use"}\\n' > /home/logs/traj.jsonl
printf 'EVMBENCH_PREFLIGHT_OK_9f2c1b\\n' > /home/logs/preflight/proof.txt
"""
    )
    spec = make_spec(tmp_path, harness="fake")
    computer = LocalProcessComputer.create(root=tmp_path / "good")
    try:
        assert good.preflight(computer, spec).passed
    finally:
        computer.terminate()

    chat_only = ScriptedAdapter("mkdir -p /home/logs && printf '{}' > /home/logs/traj.jsonl")
    computer = LocalProcessComputer.create(root=tmp_path / "bad")
    try:
        result = chat_only.preflight(computer, spec)
    finally:
        computer.terminate()
    assert not result.passed
    assert result.reason


def test_stage_spec_round_trip(tmp_path):
    spec = make_spec(tmp_path)
    restored = StageSpec.from_dict(spec.to_dict())
    assert restored.idempotency_key() == spec.idempotency_key()
    assert restored.artifact_root == spec.artifact_root
