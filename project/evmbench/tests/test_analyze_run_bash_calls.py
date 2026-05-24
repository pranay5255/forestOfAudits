from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[1] / "scripts" / "analyze_run_bash_calls.py"
SPEC = importlib.util.spec_from_file_location("analyze_run_bash_calls", SCRIPT_PATH)
assert SPEC is not None
analyzer = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
sys.modules[SPEC.name] = analyzer
SPEC.loader.exec_module(analyzer)


def write_json(path: Path, payload: object) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload))
    return path


def analyzer_relpath(path: Path) -> str:
    resolved = path.resolve()
    if resolved.is_relative_to(analyzer.REPO_ROOT):
        return str(resolved.relative_to(analyzer.REPO_ROOT))
    return str(path)


def test_extracts_mini_swe_v1_fenced_bash_with_returncode(tmp_path: Path) -> None:
    root = tmp_path / "exploit_results"
    trace_path = write_json(
        root / "benchmark_20260101_000000_1_1_bancor.traj.json",
        {
            "info": {},
            "messages": [
                {"role": "system", "content": "respond with bash"},
                {"role": "user", "content": "start"},
                {
                    "role": "assistant",
                    "content": "THOUGHT\n```bash\ncast call 0xabc \"owner()\" --rpc-url http://127.0.0.1:8545\n```",
                },
                {"role": "user", "content": "<returncode>0</returncode>\n<output>0x00</output>"},
            ],
        },
    )

    calls, _, source_files = analyzer.compat_extract_calls(
        [analyzer.CompatSourceSpec("exploit_results", root)]
    )

    assert source_files == [analyzer_relpath(trace_path)]
    assert len(calls) == 1
    assert calls[0].source_format == "traj-bash-code-block"
    assert calls[0].exit_code == "0"
    assert calls[0].status == "completed"
    assert calls[0].primary_category == "onchain_state_query"


def test_extracts_timeout_observation_for_fenced_bash(tmp_path: Path) -> None:
    root = tmp_path / "exploit_results"
    write_json(
        root / "benchmark_20260101_000000_1_1_timeout.traj.json",
        {
            "messages": [
                {"role": "user", "content": "start"},
                {"role": "assistant", "content": "```sh\nforge script script/Harness.s.sol --broadcast\n```"},
                {
                    "role": "user",
                    "content": "Command timed out after 5 seconds:\n<command>forge script script/Harness.s.sol --broadcast</command>",
                },
            ],
        },
    )

    calls, _, _ = analyzer.compat_extract_calls([analyzer.CompatSourceSpec("exploit_results", root)])

    assert len(calls) == 1
    assert calls[0].status == "timeout"
    assert calls[0].primary_category == "exploit_execution"


def test_extracts_actions_bash_without_double_counting_code_block(tmp_path: Path) -> None:
    root = tmp_path / "exploit_results_v3"
    write_json(
        root / "benchmark_20260320_192931_210217_1_bancor.traj.json",
        {
            "messages": [
                {"role": "user", "content": "start"},
                {
                    "role": "assistant",
                    "content": "```bash\nls -la && rg -n owner src/Target.sol\n```",
                    "actions": [
                        {
                            "tool": "bash",
                            "command": "ls -la && rg -n owner src/Target.sol",
                            "action": "ls -la && rg -n owner src/Target.sol",
                        }
                    ],
                },
            ],
        },
    )

    calls, _, _ = analyzer.compat_extract_calls([analyzer.CompatSourceSpec("exploit_results_v3", root)])

    assert len(calls) == 1
    assert calls[0].source_format == "traj-action"
    assert calls[0].inner_command == "ls -la && rg -n owner src/Target.sol"
    assert calls[0].categories == "file_read_navigation|text_search"


def test_extracts_forest_tool_calls_with_tool_result(tmp_path: Path) -> None:
    root = tmp_path / "runs"
    path = (
        root
        / "2026-04-14T15-57-25-GMT_run-group_yudai-detect_detect"
        / "2023-07-pooltogether_8ac0d087"
        / "logs"
        / "forest"
        / "scout.traj.json"
    )
    write_json(
        path,
        {
            "messages": [
                {"role": "user", "content": "read scope"},
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": "call-1",
                            "type": "function",
                            "function": {"name": "bash", "arguments": json.dumps({"command": "cat AGENTS.md"})},
                        }
                    ],
                },
                {
                    "role": "tool",
                    "tool_call_id": "call-1",
                    "content": "<returncode>0</returncode>\n<output>ok</output>",
                },
            ],
        },
    )

    calls, _, _ = analyzer.compat_extract_calls([analyzer.CompatSourceSpec("evmbench_native_runs", root)])

    assert len(calls) == 1
    assert calls[0].source_format == "traj-tool-call"
    assert calls[0].role == "scout"
    assert calls[0].exit_code == "0"
    assert calls[0].primary_category == "file_read_navigation"


def test_content_hash_dedupe_prefers_evmbench_runs_download(tmp_path: Path) -> None:
    canonical = tmp_path / "evmbench_runs_download"
    mirror = tmp_path / "project" / "evmbench" / "runs"
    rel = (
        "openrouter-v1/batch/evmbench_runs/"
        "codex--gpt-5.4--detect--2025-06-panoptic/"
        "2026-05-12T15-38-02-GMT_run-group_codex-openrouter-v1_detect/"
        "2025-06-panoptic_09b0/logs/codex/codex-run.jsonl"
    )
    payload = {
        "type": "item.completed",
        "item": {
            "id": "item_1",
            "type": "command_execution",
            "command": "/bin/bash -lc 'pwd && ls -la'",
            "status": "completed",
            "exit_code": 0,
        },
    }
    for root in (canonical, mirror):
        path = root / rel
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(payload) + "\n")

    calls, stats, source_files = analyzer.compat_extract_calls(
        [
            analyzer.CompatSourceSpec("project_evmbench_runs", mirror),
            analyzer.CompatSourceSpec("evmbench_runs_download", canonical),
        ]
    )

    assert len(calls) == 1
    assert calls[0].source_family == "evmbench_runs_download"
    assert source_files == [analyzer_relpath(canonical / rel)]
    assert stats["candidate_files"] == 2
    assert stats["unique_files"] == 1
    assert stats["skipped_duplicate_files"] == 1
    assert stats["duplicate_by_source_family"]["project_evmbench_runs"] == 1
