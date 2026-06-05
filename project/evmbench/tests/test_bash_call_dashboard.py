from __future__ import annotations

import csv
import importlib.util
import json
import sys
from pathlib import Path

import pandas as pd
import pytest


PROJECT_ROOT = Path(__file__).resolve().parents[1]
BASH_ANALYSIS_DIR = PROJECT_ROOT / "research-artifacts" / "bash-call-analysis"


def load_module(module_name: str, path: Path):
    spec = importlib.util.spec_from_file_location(module_name, path)
    assert spec is not None
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


build_joined_analysis = load_module("build_joined_analysis", BASH_ANALYSIS_DIR / "build_joined_analysis.py")
build_notebook = load_module("build_notebook", BASH_ANALYSIS_DIR / "build_notebook.py")


def write_csv(path: Path, headers: list[str], rows: list[list[object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(headers)
        writer.writerows(rows)


def write_synthetic_joined_artifacts(root: Path) -> None:
    joined = root / "joined"
    write_csv(
        joined / "stable_invocation_fact.csv",
        ["invocation_id", "run_id", "is_bash", "mode", "agent", "primary_category", "exit_bucket", "exit_code"],
        [
            [1, "run-a", "True", "detect", "codex", "file_read_navigation", "zero", "0"],
            [2, "run-a", "False", "detect", "codex", "text_search", "nonzero", "1"],
            [3, "run-b", "True", "patch", "opencode", "build_test", "zero", "0"],
        ],
    )
    write_csv(
        joined / "stable_segment_fact.csv",
        ["segment_id", "invocation_id", "run_id", "primary_category"],
        [
            [1, 1, "run-a", "file_read_navigation"],
            [2, 1, "run-a", "shell_output_logging"],
            [3, 2, "run-a", "text_search"],
            [4, 3, "run-b", "build_test"],
        ],
    )
    write_csv(
        joined / "stable_run_category_fact.csv",
        ["run_id", "role", "primary_category", "count", "agent", "mode"],
        [
            ["run-a", "unknown", "file_read_navigation", 1, "codex", "detect"],
            ["run-a", "unknown", "text_search", 1, "codex", "detect"],
            ["run-b", "unknown", "build_test", 1, "opencode", "patch"],
        ],
    )
    write_csv(
        joined / "stable_manifest_fact.csv",
        ["metric", "source_family", "count"],
        [
            ["candidate_files", "synthetic", 2],
            ["unique_files", "synthetic", 2],
            ["extracted_invocations", "synthetic", 3],
            ["counted_source_files", "synthetic", 2],
        ],
    )
    (joined / "stable_source_files.json").write_text(json.dumps(["trace-a.json", "trace-b.json"]) + "\n")


def test_dashboard_overview_uses_current_joined_totals(tmp_path: Path) -> None:
    write_synthetic_joined_artifacts(tmp_path)

    html = build_notebook.build_html(tmp_path, tmp_path / "index.html")

    assert "3,840" not in html
    assert "2,599" not in html
    assert "1,241" not in html
    assert "8,233" not in html
    assert "1,179" not in html
    assert "146 runs" not in html
    assert "3 invocations/tools" in html
    assert "2 bash invocations" in html
    assert "1 non-bash tool calls" in html
    assert "4 segments" in html
    assert "2 source files" in html
    assert "2 runs" in html


def test_dashboard_renders_all_runs_descriptive_stats_from_csv(tmp_path: Path) -> None:
    write_synthetic_joined_artifacts(tmp_path)

    html = build_notebook.build_html(tmp_path, tmp_path / "index.html")

    assert "All Runs Descriptive Stats" in html
    assert "Median invocations per run" in html
    assert "P90 invocations per run" in html
    assert "Max invocations per run" in html
    assert "Top mode" in html
    assert "detect (2 / 66.7%)" in html
    assert "Top agent" in html
    assert "codex (2 / 66.7%)" in html
    assert "Top category" in html
    assert "Nonzero-exit count/share" in html
    assert "1 / 33.3%" in html


def validation_frames() -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    inv = pd.DataFrame(
        {
            "invocation_id": [1, 2, 3],
            "run_id": ["run-a", "run-a", "run-b"],
            "is_bash": [True, False, True],
            "inner_command": ["ls", "read", "forge test"],
        }
    )
    seg = pd.DataFrame({"segment_id": [1, 2, 3, 4], "invocation_id": [1, 1, 2, 3]})
    run_cat = pd.DataFrame(
        {
            "run_id": ["run-a", "run-a", "run-b"],
            "role": ["unknown", "unknown", "unknown"],
            "primary_category": ["file_read_navigation", "text_search", "build_test"],
            "count": [1, 1, 1],
        }
    )
    legacy = pd.DataFrame({"inner_command": ["legacy-only-command"]})
    return inv, seg, run_cat, legacy


def test_dynamic_validation_accepts_non_legacy_sized_dataset() -> None:
    inv, seg, run_cat, legacy = validation_frames()

    build_joined_analysis.validate_outputs(
        inv,
        seg,
        run_cat,
        legacy,
        ["trace-a.json"],
        source_files_manifest_exists=True,
    )


def test_dynamic_validation_rejects_inconsistent_joined_data() -> None:
    inv, seg, run_cat, legacy = validation_frames()

    bad_segments = seg.copy()
    bad_segments.loc[0, "invocation_id"] = 999
    with pytest.raises(RuntimeError, match="unknown invocation IDs"):
        build_joined_analysis.validate_outputs(
            inv,
            bad_segments,
            run_cat,
            legacy,
            ["trace-a.json"],
            source_files_manifest_exists=True,
        )

    bad_run_cat = run_cat.copy()
    bad_run_cat.loc[0, "count"] = 2
    with pytest.raises(RuntimeError, match="do not sum"):
        build_joined_analysis.validate_outputs(
            inv,
            seg,
            bad_run_cat,
            legacy,
            ["trace-a.json"],
            source_files_manifest_exists=True,
        )

    with pytest.raises(RuntimeError, match="source file manifest"):
        build_joined_analysis.validate_outputs(
            inv,
            seg,
            run_cat,
            legacy,
            ["trace-a.json"],
            source_files_manifest_exists=False,
        )
