from pathlib import Path
from types import SimpleNamespace

from evmbench.agents.agent import Agent, agent_registry
from evmbench.agents.modal_runner import build_modal_runner_invocation, run_modal_runner


def _task() -> SimpleNamespace:
    return SimpleNamespace(
        audit=SimpleNamespace(id="2024-01-canto", findings_subdir=""),
        mode="detect",
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
    def fake_run(*args, **kwargs):
        return SimpleNamespace(stdout="", stderr="", returncode=0)

    monkeypatch.setattr("evmbench.agents.modal_runner.subprocess.run", fake_run)
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
