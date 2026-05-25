from __future__ import annotations

import asyncio
import json
import tomllib
from pathlib import Path

import pytest

from evmbench.agents.agent import agent_registry
from evmbench.audit import audit_registry
from evmbench.harbor_adapter import verifier
from evmbench.harbor_adapter.computer import HarborComputerInterface
from evmbench.harbor_adapter.dataset import (
    HarborDetectTaskSpec,
    default_harbor_dataset_root,
    generate_detect_dataset,
)
from evmbench.harbor_adapter.forest import (
    HarborForestConfig,
    collect_forest_artifacts,
    preview_forest_worker_contract,
)
from evmbench.nano.grade import EVMbenchDetectResult, EVMbenchGrade


def test_generated_detect_task_uses_exact_image_and_instruction(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("EVMBENCH_AUDIT_IMAGE_REPO", "registry.example.test/evmbench/audit")
    task_dir = generate_detect_dataset(["2024-03-canto"], tmp_path)[0]

    audit = audit_registry.get_audit("2024-03-canto")
    expected_instruction = agent_registry.load_instructions("detect", audit, "none")
    assert (task_dir / "instruction.md").read_text(encoding="utf-8") == expected_instruction

    config = tomllib.loads((task_dir / "task.toml").read_text(encoding="utf-8"))
    assert config["environment"]["docker_image"] == "registry.example.test/evmbench/audit:2024-03-canto"
    assert config["metadata"]["submission_path"] == "/home/agent/submission/audit.md"
    assert config["metadata"]["verifier_module"] == "evmbench.harbor_adapter.verifier"
    assert "/home/agent/submission/audit.md" in config["artifacts"]
    assert (task_dir / "environment").is_dir()
    assert (task_dir / "tests" / "test.sh").exists()


def test_default_harbor_dataset_root_is_ignored_run_output() -> None:
    assert default_harbor_dataset_root().as_posix().endswith("runs/harbor-datasets")
    assert "runs/" in Path(".gitignore").read_text(encoding="utf-8")


def test_detect_verifier_calls_existing_grader_and_writes_reward_json(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    calls: dict[str, object] = {}

    class FakeGrader:
        def __init__(self, computer: HarborComputerInterface) -> None:
            calls["computer"] = computer

        async def grade(self, ctx):
            calls["ctx"] = ctx
            return EVMbenchGrade(
                score=1,
                grader_log="stubbed",
                evmbench_result=EVMbenchDetectResult(
                    audit_id=ctx.audit.id,
                    score=1,
                    max_score=2,
                    detect_award=3.0,
                    detect_max_award=5.0,
                    details={"stubbed": True},
                ),
            )

    def fake_build_grader(mode, computer, turn_completer):
        calls["mode"] = mode
        calls["turn_completer"] = turn_completer
        return FakeGrader(computer)

    monkeypatch.setattr(verifier, "build_grader", fake_build_grader)
    submission = tmp_path / "audit.md"
    submission.write_text("# report\n", encoding="utf-8")
    reward_path = tmp_path / "verifier" / "reward.json"
    grade_path = tmp_path / "verifier" / "evmbench-grade.json"

    reward = asyncio.run(verifier.run_detect_verifier(
        audit_id="2024-03-canto",
        agent_output_path=submission,
        reward_path=reward_path,
        grade_path=grade_path,
        run_id="unit",
        runs_dir=str(tmp_path),
    ))

    assert calls["mode"] == "detect"
    assert isinstance(calls["computer"], HarborComputerInterface)
    assert calls["ctx"].agent_output_path == submission
    assert reward == {
        "reward": 0.5,
        "award_reward": 0.6,
        "score": 1.0,
        "max_score": 2.0,
        "detect_award": 3.0,
        "detect_max_award": 5.0,
    }
    assert json.loads(reward_path.read_text(encoding="utf-8")) == reward
    assert json.loads(grade_path.read_text(encoding="utf-8"))["grader_log"] == "stubbed"


def test_forest_wrapper_preserves_worker_contract() -> None:
    contracts = preview_forest_worker_contract(
        HarborForestConfig(
            audit_id="2024-01-canto",
            branches_per_tree=2,
            tree_roles=("token-flow",),
        ),
        roles=("token-flow",),
    )

    branch_contracts = [item for item in contracts if item.worker_type == "branch"]
    assert [item.worker_name for item in branch_contracts] == [
        "token-flow-branch-01",
        "token-flow-branch-02",
    ]
    assert [item.branch for item in branch_contracts] == ["branch-01", "branch-02"]
    assert all(item.role == "token-flow" for item in branch_contracts)
    assert all(not item.include_submission for item in branch_contracts)
    assert all(item.forbid_submission for item in branch_contracts)

    global_contract = next(item for item in contracts if item.worker_type == "global_judge")
    assert global_contract.worker_name == "global-judge"
    assert global_contract.output_path == "/home/agent/submission/audit.md"
    assert global_contract.include_submission is True
    assert global_contract.forbid_submission is False


def test_collect_forest_artifacts_includes_submission_metadata_and_trajectories(tmp_path: Path) -> None:
    paths = [
        tmp_path / "submission" / "audit.md",
        tmp_path / "logs" / "modal-forest-result.json",
        tmp_path / "logs" / "forest" / "trajectory-manifest.json",
        tmp_path / "logs" / "forest" / "token-flow" / "branch-01.traj.json",
    ]
    for path in paths:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")

    assert collect_forest_artifacts(tmp_path) == tuple(paths)


def test_generate_one_canto_detect_task_smoke(tmp_path: Path) -> None:
    task_dir = generate_detect_dataset(["2024-01-canto"], tmp_path)[0]

    assert task_dir.name == HarborDetectTaskSpec("2024-01-canto").directory_name
    assert (task_dir / "task.toml").exists()
    assert (task_dir / "instruction.md").exists()
    assert (task_dir / "tests" / "evmbench_harbor_verifier.py").exists()
