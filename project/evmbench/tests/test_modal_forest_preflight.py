import importlib.util
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT_PATH = PROJECT_ROOT / "scripts" / "check_modal_forest_modes.py"


def load_preflight_module():
    spec = importlib.util.spec_from_file_location("check_modal_forest_modes", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["check_modal_forest_modes"] = module
    spec.loader.exec_module(module)
    return module


preflight = load_preflight_module()


def test_preflight_emits_patch_command(tmp_path: Path) -> None:
    row = preflight._row_for_pair(
        mode="patch",
        audit_id="2023-07-pooltogether",
        agent_id="mini-swe-agent-modal-forest",
        output_root=tmp_path,
        emit_command=True,
        run=False,
    )

    assert row["status"] == "ok"
    assert row["ok"] is True
    assert "evmbench.mode=patch" in row["command"]
    assert "evmbench.audit_split=patch-tasks" in row["command"]


def test_preflight_skips_mode_without_vulnerabilities(tmp_path: Path) -> None:
    row = preflight._row_for_pair(
        mode="patch",
        audit_id="2024-01-canto",
        agent_id="mini-swe-agent-modal-forest",
        output_root=tmp_path,
        emit_command=False,
        run=False,
    )

    assert row["status"] == "skipped_no_vulnerabilities"
    assert row["skipped_no_vulnerabilities"] is True
    assert row["ok"] is False
