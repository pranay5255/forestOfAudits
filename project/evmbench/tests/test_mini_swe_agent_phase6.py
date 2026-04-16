import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from evmbench.agents.agent import agent_registry

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINI_AGENT_DIR = PROJECT_ROOT / "evmbench" / "agents" / "mini-swe-agent"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_phase6_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("evaluate_phase6", MINI_AGENT_DIR / "evaluate_phase6.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["evaluate_phase6"] = module
    spec.loader.exec_module(module)
    return module


phase6 = load_phase6_module()


def test_phase6_non_smoke_modal_variants_are_registered_without_fallback() -> None:
    baseline = agent_registry.get_agent("mini-swe-agent-modal-baseline")
    forest = agent_registry.get_agent("mini-swe-agent-modal-forest")

    assert baseline.runner == "modal_baseline"
    assert baseline.env_vars["STEP_LIMIT"] == "50"
    assert baseline.env_vars["COST_LIMIT"] == "20.0"
    assert baseline.env_vars["MODAL_OPENAI_SECRET_NAME"] == "openai-api-key"
    assert "MODAL_ALLOW_SMOKE_FALLBACK_SUBMISSION" not in baseline.env_vars
    assert "MODAL_TASK" not in baseline.env_vars

    assert forest.runner == "modal_forest"
    assert forest.env_vars["BRANCHES_PER_TREE"] == "2"
    assert forest.env_vars["MAX_TREE_ROLES"] == "4"
    assert forest.env_vars["FOREST_WORKER_CONCURRENCY"] == "4"
    assert "MODAL_ALLOW_SMOKE_FALLBACK_SUBMISSION" not in forest.env_vars
    assert "MODAL_TASK" not in forest.env_vars


def test_phase6_plan_emits_default_runner_matrix(tmp_path: Path, capsys) -> None:
    rc = phase6.main(["plan", "--scope", "first5", "--output-root", str(tmp_path)])

    assert rc == 0
    output = capsys.readouterr().out
    assert "codex-default" in output
    assert "mini-swe-agent-modal-baseline" in output
    assert "mini-swe-agent-modal-forest" in output
    assert "2023-07-pooltogether" in output
    assert "2024-01-curves" in output


def test_phase6_runner_groups_cover_presentation_smoke_and_all_variants() -> None:
    presentation = {runner.agent_id for runner in phase6.parse_runner_list("presentation")}
    smoke = {runner.agent_id for runner in phase6.parse_runner_list("smoke")}
    all_variants = {runner.agent_id for runner in phase6.parse_runner_list("all")}

    assert presentation == {
        "codex-default",
        "mini-swe-agent-modal-baseline",
        "mini-swe-agent-modal-forest",
    }
    assert smoke == {
        "codex-default",
        "mini-swe-agent-smoke-10",
        "mini-swe-agent-modal-baseline-smoke-10",
        "mini-swe-agent-modal-forest-smoke",
    }
    assert {
        "mini-swe-agent-default",
        "mini-swe-agent-smoke-10",
        "mini-swe-agent-gpt-5-mini",
        "mini-swe-agent-modal-baseline",
        "mini-swe-agent-modal-baseline-smoke-10",
        "mini-swe-agent-modal-forest",
        "mini-swe-agent-modal-forest-smoke",
    }.issubset(all_variants)


def test_phase6_variants_command_lists_runnable_agent_ids(capsys) -> None:
    rc = phase6.main(["variants"])

    assert rc == 0
    output = capsys.readouterr().out
    assert "presentation:" in output
    assert "mini-default\tmini-swe-agent-default" in output
    assert "modal-forest-smoke\tmini-swe-agent-modal-forest-smoke" in output


def test_phase6_summary_extracts_grade_submission_and_modal_metadata(tmp_path: Path) -> None:
    output_root = tmp_path / "phase6"
    runner = phase6.RunnerSpec("modal-baseline", "mini-swe-agent-modal-baseline", "Modal baseline")
    matrix = phase6.build_run_matrix(
        output_root=output_root,
        scope="smoke",
        audits=["2024-01-canto"],
        runners=[runner],
    )
    phase6.write_matrix(output_root, "smoke", matrix)

    run_dir = output_root / "modal-baseline" / "group" / "2024-01-canto_abc"
    (run_dir / "submission").mkdir(parents=True)
    (run_dir / "submission" / "audit.md").write_text("# Audit\n", encoding="utf-8")
    (run_dir / "logs").mkdir()
    (run_dir / "logs" / "mini-swe-agent.traj.json").write_text("[]\n", encoding="utf-8")
    grade_event = {
        "event": "[2024-01-canto] Grade:\n",
        "grade": {
            "evmbench_result": {
                "audit_id": "2024-01-canto",
                "score": 1,
                "max_score": 2,
                "detect_award": 3.0,
                "detect_max_award": 5.0,
                "agent_output": {"runtime_in_seconds": 12.5},
            }
        },
    }
    (run_dir / "run.log").write_text(str(grade_event) + "\n", encoding="utf-8")
    modal_logs = run_dir / "modal" / "logs"
    modal_logs.mkdir(parents=True)
    (modal_logs / "modal-runner-command.json").write_text(
        json.dumps({"runner": "modal_baseline"}),
        encoding="utf-8",
    )
    (modal_logs / "modal-baseline-result.json").write_text(
        json.dumps({"runtime_seconds": 12.0, "error": None}),
        encoding="utf-8",
    )

    payload = phase6.summarize_phase6(output_root)

    assert (output_root / "phase6-results.json").exists()
    assert (output_root / "phase6-summary.md").exists()
    assert (output_root / "phase6-slide-data.json").exists()
    assert (output_root / "phase6-slide-data.csv").exists()
    row = payload["rows"][0]
    assert row["submission_exists"] is True
    assert row["score"] == 1.0
    assert row["score_percentage"] == 50.0
    assert row["detect_award_percentage"] == 60.0
    assert row["agent_runtime_seconds"] == 12.5
    assert row["failure_reason"] is None
    assert row["trajectory_paths"] == ["logs/mini-swe-agent.traj.json"]
    slide_data = json.loads((output_root / "phase6-slide-data.json").read_text(encoding="utf-8"))
    assert slide_data["runner_summary"][0]["runner"] == "modal-baseline"
    assert slide_data["runner_summary"][0]["successful_submissions"] == 1
    assert slide_data["runner_summary"][0]["average_runtime_seconds"] == 12.5
    assert slide_data["per_audit"][0]["audit_id"] == "2024-01-canto"
    assert "runner,agent_id,audit_id,submission_exists" in (
        output_root / "phase6-slide-data.csv"
    ).read_text(encoding="utf-8")


def test_phase6_slide_data_includes_forest_metadata(tmp_path: Path) -> None:
    output_root = tmp_path / "phase6"
    runner = phase6.RunnerSpec("modal-forest", "mini-swe-agent-modal-forest", "Modal forest")
    matrix = phase6.build_run_matrix(
        output_root=output_root,
        scope="smoke",
        audits=["2024-01-canto"],
        runners=[runner],
    )
    phase6.write_matrix(output_root, "smoke", matrix)

    run_dir = output_root / "modal-forest" / "group" / "2024-01-canto_abc"
    (run_dir / "submission").mkdir(parents=True)
    (run_dir / "submission" / "audit.md").write_text("# Audit\n", encoding="utf-8")
    grade_event = {
        "event": "[2024-01-canto] Grade:\n",
        "grade": {
            "evmbench_result": {
                "audit_id": "2024-01-canto",
                "score": 2,
                "max_score": 4,
                "detect_award": 2.0,
                "detect_max_award": 4.0,
                "agent_output": {"runtime_in_seconds": 25.0},
            }
        },
    }
    (run_dir / "run.log").write_text(str(grade_event) + "\n", encoding="utf-8")
    modal_logs = run_dir / "modal" / "logs"
    modal_logs.mkdir(parents=True)
    (modal_logs / "modal-forest-result.json").write_text(
        json.dumps(
            {
                "selected_roles": ["token-flow", "accounting"],
                "workers": [
                    {
                        "worker_name": "token-flow-branch-01",
                        "worker_type": "branch",
                        "role": "token-flow",
                        "branch": "branch-01",
                        "runtime_seconds": 10.0,
                        "error": None,
                        "trajectory_path": "token-flow/branch-01.traj.json",
                    },
                    {
                        "worker_name": "accounting-judge",
                        "worker_type": "tree_judge",
                        "role": "accounting",
                        "branch": None,
                        "runtime_seconds": 5.0,
                        "error": "missing branch report",
                        "trajectory_path": "accounting/judge.traj.json",
                    },
                ],
            }
        ),
        encoding="utf-8",
    )

    phase6.summarize_phase6(output_root)

    slide_data = json.loads((output_root / "phase6-slide-data.json").read_text(encoding="utf-8"))
    forest = slide_data["forest"][0]
    assert forest["audit_id"] == "2024-01-canto"
    assert forest["selected_roles"] == ["token-flow", "accounting"]
    assert forest["worker_count"] == 2
    assert forest["worker_error_count"] == 1
    assert forest["worker_total_runtime_seconds"] == 15.0
    assert forest["worker_average_runtime_seconds"] == 7.5


def test_phase6_summary_records_command_failure_without_run_dir(tmp_path: Path) -> None:
    output_root = tmp_path / "phase6"
    runner = phase6.RunnerSpec("codex-default", "codex-default", "Codex")
    matrix = phase6.build_run_matrix(
        output_root=output_root,
        scope="smoke",
        audits=["2024-01-canto"],
        runners=[runner],
    )
    phase6.write_matrix(output_root, "smoke", matrix)
    status_path = phase6.command_status_path(output_root, matrix[0])
    status_path.parent.mkdir(parents=True)
    status_path.write_text(json.dumps({"returncode": 2}), encoding="utf-8")

    payload = phase6.summarize_phase6(output_root)

    row = payload["rows"][0]
    assert row["submission_exists"] is False
    assert row["failure_reason"] == "command exited 2"
