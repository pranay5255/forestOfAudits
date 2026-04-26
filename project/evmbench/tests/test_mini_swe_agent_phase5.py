from pathlib import Path
from types import SimpleNamespace

import pytest

from evmbench.agents.agent import Agent, agent_registry
from evmbench.agents.modal_runner import (
    build_modal_runner_invocation,
    modal_runner_environment,
    run_modal_runner,
)


def _task(mode: str = "detect") -> SimpleNamespace:
    return SimpleNamespace(
        audit=SimpleNamespace(id="2024-01-canto", findings_subdir=""),
        mode=mode,
        hint_level="none",
        docker_image="ghcr.io/pranay5255/evmbench-audit:2024-01-canto",
    )


def test_registry_defaults_to_container_runner_and_resolves_start_path() -> None:
    agent = agent_registry.get_agent("mini-swe-agent-default")

    assert agent.runner == "container"
    assert agent.start_sh.endswith("evmbench/agents/mini-swe-agent/start.sh")
    assert Path(agent.start_sh).exists()


def test_registry_loads_modal_runner_variants() -> None:
    baseline = agent_registry.get_agent("mini-swe-agent-modal-baseline-smoke-10")
    forest = agent_registry.get_agent("mini-swe-agent-modal-forest-smoke")

    assert baseline.runner == "modal_baseline"
    assert baseline.env_vars["STEP_LIMIT"] == "10"
    assert baseline.env_vars["COST_LIMIT"] == "5.0"
    assert baseline.env_vars["MODAL_OPENAI_SECRET_NAME"] == "openai-api-key"
    assert forest.runner == "modal_forest"
    assert forest.env_vars["BRANCHES_PER_TREE"] == "1"
    assert forest.env_vars["FOREST_WORKER_CONCURRENCY"] == "2"


def test_modal_baseline_invocation_uses_entrypoint_and_skips_runner_grading(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.delenv("MODAL_AUDIT_IMAGE", raising=False)
    monkeypatch.delenv("MODAL_AUDIT_IMAGE_REPO", raising=False)

    agent = Agent(
        id="mini-swe-agent-modal-baseline-smoke-10",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={
            "MODEL": "openai/gpt-5",
            "STEP_LIMIT": "10",
            "COST_LIMIT": "5.0",
            "MODAL_OPENAI_SECRET_NAME": "openai-api-key",
            "MODAL_TASK": "write a smoke report",
        },
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task(),
        tmp_path / "modal",
        python_executable="python",
    )

    assert invocation.runner_name == "baseline"
    assert invocation.command[:3] == [
        "python",
        str(Path("evmbench/agents/mini-swe-agent/entrypoint.py").resolve()),
        "baseline",
    ]
    assert "--audit-id" in invocation.command
    assert "2024-01-canto" in invocation.command
    assert "--image" in invocation.command
    assert "ghcr.io/pranay5255/evmbench-audit:2024-01-canto" in invocation.command
    assert "--no-grade" in invocation.command
    assert "--step-limit" in invocation.command
    assert "10" in invocation.command
    assert invocation.submission_path == tmp_path / "modal" / "submission" / "audit.md"


def test_modal_forest_invocation_maps_budget_env(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-forest-smoke",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={
            "MODEL": "openai/gpt-5",
            "SCOUT_STEP_LIMIT": "8",
            "BRANCH_STEP_LIMIT": "10",
            "GLOBAL_COST_LIMIT": "2.0",
            "BRANCHES_PER_TREE": "1",
            "MAX_TREE_ROLES": "2",
            "FOREST_WORKER_CONCURRENCY": "2",
        },
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task(),
        tmp_path / "modal",
        python_executable="python",
    )

    assert invocation.runner_name == "forest"
    assert invocation.command[:3] == [
        "python",
        str(Path("evmbench/agents/mini-swe-agent/entrypoint.py").resolve()),
        "forest",
    ]
    assert "--branch-step-limit" in invocation.command
    assert "10" in invocation.command
    assert "--global-cost-limit" in invocation.command
    assert "2.0" in invocation.command
    assert "--worker-concurrency" in invocation.command
    assert "2" in invocation.command


def test_modal_forest_invocation_forwards_patch_mode_and_submission_path(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-forest",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={"MODEL": "openai/gpt-5"},
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task("patch"),
        tmp_path / "modal",
        python_executable="python",
    )

    assert invocation.runner_name == "forest"
    assert invocation.command[invocation.command.index("--mode") + 1] == "patch"
    assert invocation.submission_path == tmp_path / "modal" / "submission" / "agent.diff"


def test_modal_forest_invocation_forwards_exploit_mode_and_submission_path(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-forest",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={"MODEL": "openai/gpt-5"},
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task("exploit"),
        tmp_path / "modal",
        python_executable="python",
    )

    assert invocation.runner_name == "forest"
    assert invocation.command[invocation.command.index("--mode") + 1] == "exploit"
    assert invocation.submission_path == tmp_path / "modal" / "submission" / "txs.json"


def test_modal_baseline_remains_detect_only(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-baseline",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={"MODEL": "openai/gpt-5"},
    )

    with pytest.raises(RuntimeError, match="detect mode only"):
        build_modal_runner_invocation(agent, _task("patch"), tmp_path / "modal")


def test_modal_invocation_forwards_model_kwargs_json(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-forest-smoke",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={
            "MODEL": "openai/gpt-5",
            "MODEL_KWARGS_JSON": '{"api_base":"https://example.test/v1"}',
        },
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task(),
        tmp_path / "modal",
        python_executable="python",
    )

    assert "--model-kwargs-json" in invocation.command
    assert invocation.command[invocation.command.index("--model-kwargs-json") + 1] == (
        '{"api_base":"https://example.test/v1"}'
    )


def test_modal_forest_invocation_maps_8tree_gpt52_codex_env(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-forest-gpt-5.2-codex-8trees",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={
            "MODEL": "openai/gpt-5.2-codex",
            "SCOUT_MODEL": "openai/gpt-5.2-codex",
            "BRANCH_MODEL": "openai/gpt-5.2-codex",
            "JUDGE_MODEL": "openai/gpt-5.2-codex",
            "GLOBAL_MODEL": "openai/gpt-5.2-codex",
            "BRANCHES_PER_TREE": "1",
            "MAX_TREE_ROLES": "8",
            "TREE_ROLES": "token-flow,accounting,access-control,cross-contract,exploitability,oracle-price,state-machine,standards-compliance",
            "FOREST_WORKER_CONCURRENCY": "8",
            "MSWEA_COST_TRACKING": "ignore_errors",
        },
    )

    invocation = build_modal_runner_invocation(
        agent,
        _task(),
        tmp_path / "modal",
        python_executable="python",
    )

    assert "--model" in invocation.command
    assert "openai/gpt-5.2-codex" in invocation.command
    assert invocation.command.count("openai/gpt-5.2-codex") == 5
    assert "--branches-per-tree" in invocation.command
    assert "1" in invocation.command
    assert "--max-tree-roles" in invocation.command
    assert "8" in invocation.command
    assert "--tree-roles" in invocation.command
    tree_roles = invocation.command[invocation.command.index("--tree-roles") + 1]
    assert tree_roles.split(",") == [
        "token-flow",
        "accounting",
        "access-control",
        "cross-contract",
        "exploitability",
        "oracle-price",
        "state-machine",
        "standards-compliance",
    ]
    assert "--worker-concurrency" in invocation.command
    assert "--cost-tracking" in invocation.command
    assert "ignore_errors" in invocation.command


def test_modal_invocation_can_use_registry_image_repo_without_changing_task_image(tmp_path: Path) -> None:
    agent = Agent(
        id="mini-swe-agent-modal-baseline-smoke-10",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={
            "MODEL": "openai/gpt-5",
            "MODAL_AUDIT_IMAGE_REPO": "ghcr.io/pranay5255/evmbench-audit",
        },
    )
    task = _task()
    task.docker_image = "evmbench/audit:2024-01-canto"

    invocation = build_modal_runner_invocation(
        agent,
        task,
        tmp_path / "modal",
        python_executable="python",
    )

    image_index = invocation.command.index("--image") + 1
    assert invocation.command[image_index] == "ghcr.io/pranay5255/evmbench-audit:2024-01-canto"


def test_modal_runner_smoke_fallback_writes_submission(tmp_path: Path, monkeypatch) -> None:
    def fake_stream(*args, **kwargs):
        return "", "", 0

    monkeypatch.setattr("evmbench.agents.modal_runner._run_modal_entrypoint_streaming", fake_stream)
    agent = Agent(
        id="mini-swe-agent-modal-baseline-smoke-10",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={
            "OPENAI_API_KEY": "test-key",
            "MODEL": "openai/gpt-5",
            "STEP_LIMIT": "10",
            "MODAL_ALLOW_SMOKE_FALLBACK_SUBMISSION": "1",
        },
    )

    result = run_modal_runner(agent, _task(), tmp_path / "modal")

    assert result.invocation.submission_path.exists()
    assert "EVMBench Modal Integration Smoke" in result.invocation.submission_path.read_text()


def test_modal_runner_environment_accepts_only_vllm_api_key(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("VLLM_API_KEY", "vllm-key")
    agent = Agent(
        id="mini-swe-agent-modal-forest",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_forest",
        env_vars={"OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}"},
    )

    env = modal_runner_environment(agent)

    assert env["VLLM_API_KEY"] == "vllm-key"
    assert env["OPENAI_API_KEY"] == "vllm-key"


def test_modal_runner_environment_preserves_openai_api_key(monkeypatch) -> None:
    monkeypatch.setenv("OPENAI_API_KEY", "openai-key")
    monkeypatch.setenv("VLLM_API_KEY", "vllm-key")
    agent = Agent(
        id="mini-swe-agent-modal-baseline",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={"MODEL": "openai/gpt-5"},
    )

    env = modal_runner_environment(agent)

    assert env["OPENAI_API_KEY"] == "openai-key"
    assert env["VLLM_API_KEY"] == "vllm-key"


def test_modal_runner_environment_rejects_unresolved_secret_placeholders(monkeypatch) -> None:
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.delenv("VLLM_API_KEY", raising=False)
    agent = Agent(
        id="mini-swe-agent-modal-baseline",
        name="mini-swe-agent",
        start_sh="unused",
        instruction_file_name="AGENTS.md",
        runner="modal_baseline",
        env_vars={
            "OPENAI_API_KEY": "${{ secrets.OPENAI_API_KEY }}",
            "VLLM_API_KEY": "${{ secrets.VLLM_API_KEY }}",
        },
    )

    with pytest.raises(RuntimeError, match="neither OPENAI_API_KEY nor VLLM_API_KEY"):
        modal_runner_environment(agent)
