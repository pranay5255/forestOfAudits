import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINI_AGENT_DIR = PROJECT_ROOT / "evmbench" / "agents" / "mini-swe-agent"
if str(MINI_AGENT_DIR) not in sys.path:
    sys.path.insert(0, str(MINI_AGENT_DIR))
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_agent_module(name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, MINI_AGENT_DIR / f"{name}.py")
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


scout = load_agent_module("scout")
judge = load_agent_module("judge")
modal_forest = load_agent_module("modal_forest")


def make_forest_config(tmp_path: Path, *, branches_per_tree: int = 2) -> Any:
    return modal_forest.ForestConfig(
        audit_id="2026-01-tempo-stablecoin-dex",
        mode="detect",
        hint_level="none",
        findings_subdir="",
        image="evmbench/audit:2026-01-tempo-stablecoin-dex",
        model="openai/gpt-5",
        scout_model="openai/gpt-5",
        branch_model="openai/gpt-5",
        judge_model="openai/gpt-5",
        global_model="openai/gpt-5",
        scout_step_limit=1,
        scout_cost_limit=1.0,
        branch_step_limit=1,
        branch_cost_limit=1.0,
        judge_step_limit=1,
        judge_cost_limit=1.0,
        global_step_limit=1,
        global_cost_limit=1.0,
        branches_per_tree=branches_per_tree,
        max_tree_roles=4,
        tree_roles=(),
        worker_concurrency=2,
        continue_on_worker_error=False,
        command_timeout=30,
        startup_timeout=60.0,
        runtime_timeout=60.0,
        deployment_timeout=60.0,
        install_pipx=True,
        output_dir=tmp_path,
        model_kwargs={},
        modal_sandbox_kwargs={},
        cost_tracking="default",
        task="test task",
    )


def test_scout_decision_filters_unknown_roles_and_caps() -> None:
    decision = scout.parse_scout_decision(
        """
        {
          "summary": "surface",
          "recommended_roles": ["accounting", "unknown", "token-flow", "accounting"],
          "role_rationale": "not an object"
        }
        """,
        max_roles=2,
    )

    assert decision.summary == "surface"
    assert decision.recommended_roles == ("accounting", "token-flow")
    assert decision.role_rationale == {}


def test_branch_specs_use_isolated_paths_and_do_not_include_submission(tmp_path: Path) -> None:
    config = make_forest_config(tmp_path)
    roles = [scout.get_tree_role("token-flow"), scout.get_tree_role("accounting")]

    specs = modal_forest._worker_specs_for_branches(config, roles)

    assert len(specs) == 4
    assert len({spec.worker_name for spec in specs}) == 4
    assert len({spec.output_path for spec in specs}) == 4
    assert all(spec.output_path.startswith("/home/agent/forest/") for spec in specs)
    assert all(spec.forbid_submission for spec in specs)
    assert all(not spec.include_submission for spec in specs)
    assert all("/home/agent/submission/audit.md" not in spec.output_path for spec in specs)


def test_global_judge_is_only_spec_that_extracts_submission(tmp_path: Path) -> None:
    config = make_forest_config(tmp_path)
    roles = [scout.get_tree_role("token-flow")]

    global_spec = modal_forest._global_judge_spec(config, roles)

    assert global_spec.output_path == "/home/agent/submission/audit.md"
    assert global_spec.include_submission is True
    assert global_spec.forbid_submission is False
    assert "tree_reports_dir" in global_spec.template_vars


def test_tree_judge_stages_existing_and_missing_branch_reports(tmp_path: Path) -> None:
    config = make_forest_config(tmp_path)
    role = scout.get_tree_role("token-flow")
    branch_report = judge.local_branch_report_path(tmp_path, role, 1)
    branch_report.parent.mkdir(parents=True)
    branch_report.write_text("# Branch one\n", encoding="utf-8")

    staged = modal_forest._stage_branch_reports(config, role)

    assert staged["/home/agent/forest/token-flow/branch-inputs/branch-01.md"] == "# Branch one\n"
    assert "Missing Branch Report" in staged["/home/agent/forest/token-flow/branch-inputs/branch-02.md"]
