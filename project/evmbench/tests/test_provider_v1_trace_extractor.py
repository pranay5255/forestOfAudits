from __future__ import annotations

import json
from pathlib import Path

import pytest

from evmbench.experiments.extract_provider_v1_traces import (
    CONVERSATIONS_JSONL,
    RAW_MANIFEST_JSON,
    extract_provider_v1_traces,
)
from evmbench.experiments.provider_v1_trace_schema import (
    ProviderV1SchemaError,
    redact_json,
    validate_provider_v1_jsonl,
    validate_provider_v1_row,
)
from evmbench.experiments.schema_version import SCHEMA_VERSION


def _write_json(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")


def _write_jsonl(path: Path, rows: list[dict[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(json.dumps(row) for row in rows) + "\n", encoding="utf-8")


def _write_results(root: Path, row: dict[str, object]) -> None:
    _write_json(root / "openrouter-v1-results.json", {"rows": [row], "aggregate": {}})


def _base_summary_row(run_dir: Path, *, harness: str) -> dict[str, object]:
    return {
        "run_key": f"{harness}--gpt-5.4--detect--2024-01-canto",
        "provider": "azure-foundry",
        "harness": harness,
        "agent_id": f"{harness}-openrouter-v1",
        "model": "gpt-5.4",
        "audit_id": "2024-01-canto",
        "mode": "detect",
        "run_dir": str(run_dir),
        "score": 0,
        "max_score": 1,
        "failure_reason": None,
        "submission_fallback": False,
        "command_status": {"returncode": 0, "timed_out": False},
    }


def test_codex_rollout_extraction_preserves_tool_trace_and_redacts(tmp_path: Path) -> None:
    root = tmp_path / "provider-root"
    run_dir = root / "evmbench_runs" / "codex-run" / "group" / "2024-01-canto_unit"
    _write_results(root, _base_summary_row(run_dir, harness="codex"))
    _write_json(run_dir / "logs" / "codex" / "trajectory-manifest.json", {"agent": "codex"})
    _write_jsonl(
        run_dir / "sessions" / "2026" / "06" / "05" / "rollout-unit.jsonl",
        [
            {"timestamp": "2026-06-05T00:00:00Z", "type": "session_meta", "payload": {"id": "codex-session"}},
            {"timestamp": "2026-06-05T00:00:01Z", "type": "response_item", "payload": {"type": "message", "role": "developer", "content": "rules"}},
            {"timestamp": "2026-06-05T00:00:02Z", "type": "response_item", "payload": {"type": "message", "role": "user", "content": "audit task"}},
            {"timestamp": "2026-06-05T00:00:03Z", "type": "response_item", "payload": {"type": "message", "role": "assistant", "content": "I will inspect."}},
            {
                "timestamp": "2026-06-05T00:00:04Z",
                "type": "response_item",
                "payload": {"type": "function_call", "call_id": "call_1", "name": "exec_command", "arguments": "{\"cmd\":\"pwd\"}"},
            },
            {
                "timestamp": "2026-06-05T00:00:05Z",
                "type": "response_item",
                "payload": {
                    "type": "function_call_output",
                    "call_id": "call_1",
                    "output": "OPENAI_API_KEY=sk-unitsecret123456 /home/experiments_base/private/path",
                },
            },
            {"timestamp": "2026-06-05T00:00:06Z", "type": "event_msg", "payload": {"type": "token_count", "info": {"total_token_usage": 10}}},
        ],
    )

    payload = extract_provider_v1_traces(
        input_roots=[root],
        output_dir=tmp_path / "out",
        experiment="unit_provider_v1",
    )

    assert payload["conversation_count"] == 1
    rows = validate_provider_v1_jsonl(tmp_path / "out" / CONVERSATIONS_JSONL)
    row = rows[0]
    assert row["session_id"] == "codex-session"
    assert [message["role"] for message in row["messages"]] == ["developer", "user", "assistant", "assistant", "tool"]
    tool_message = row["messages"][-1]
    assert "OPENAI_API_KEY=<REDACTED>" in str(tool_message["content"])
    assert "<HOST_PATH>" in str(tool_message["content"])
    assert row["labels"]["zero_score"] is True
    assert row["extensions"]["codex_token_counts"]

    manifest = json.loads((tmp_path / "out" / RAW_MANIFEST_JSON).read_text(encoding="utf-8"))
    kinds = {artifact["kind"] for artifact in manifest["runs"][0]["raw_artifacts"]}
    assert "codex_rollout_jsonl" in kinds
    assert manifest["conversation_count"] == 1


def test_opencode_storage_extraction_links_child_sessions(tmp_path: Path) -> None:
    root = tmp_path / "provider-root"
    run_dir = root / "evmbench_runs" / "opencode-run" / "group" / "2024-01-canto_unit"
    storage = run_dir / "logs" / "opencode" / "state" / "home__agent__.local__share__opencode" / "storage"
    _write_results(root, _base_summary_row(run_dir, harness="opencode"))
    _write_json(run_dir / "logs" / "opencode" / "trajectory-manifest.json", {"agent": "opencode", "audit_id": "2024-01-canto"})
    _write_json(run_dir / "logs" / "opencode" / "status.json", {"timed_out": False, "submission_fallback": False})
    _write_jsonl(
        run_dir / "logs" / "opencode" / "opencode-run.jsonl",
        [{"type": "text", "timestamp": 1, "sessionID": "main-session", "part": {"type": "text", "text": "stream fallback"}}],
    )

    _write_json(storage / "session" / "global" / "main-session.json", {"id": "main-session", "parentID": None, "title": "Main", "time": {"created": 1}})
    _write_json(storage / "session" / "global" / "child-session.json", {"id": "child-session", "parentID": "main-session", "title": "Child", "time": {"created": 2}})
    _write_json(storage / "message" / "main-session" / "msg_user.json", {"id": "msg_user", "sessionID": "main-session", "role": "user", "time": {"created": 1}})
    _write_json(storage / "part" / "msg_user" / "prt_text.json", {"id": "prt_text", "sessionID": "main-session", "messageID": "msg_user", "type": "text", "text": json.dumps("Please audit")})
    _write_json(storage / "message" / "main-session" / "msg_assistant.json", {"id": "msg_assistant", "sessionID": "main-session", "role": "assistant", "parentID": "msg_user", "time": {"created": 2}, "modelID": "gpt-5.4"})
    _write_json(
        storage / "part" / "msg_assistant" / "prt_tool.json",
        {
            "id": "prt_tool",
            "sessionID": "main-session",
            "messageID": "msg_assistant",
            "type": "tool",
            "callID": "call_bash",
            "tool": "bash",
            "state": {
                "status": "completed",
                "input": {"command": "echo ok"},
                "output": "PRIVATE_KEY=0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef",
                "metadata": {"exit": 0},
            },
        },
    )
    _write_json(storage / "message" / "child-session" / "msg_child.json", {"id": "msg_child", "sessionID": "child-session", "role": "user", "time": {"created": 3}})
    _write_json(storage / "part" / "msg_child" / "prt_child_text.json", {"id": "prt_child_text", "sessionID": "child-session", "messageID": "msg_child", "type": "text", "text": "child prompt"})

    payload = extract_provider_v1_traces(
        input_roots=[root],
        output_dir=tmp_path / "out",
        experiment="unit_provider_v1",
    )

    assert payload["conversation_count"] == 2
    rows = validate_provider_v1_jsonl(tmp_path / "out" / CONVERSATIONS_JSONL)
    by_session = {row["session_id"]: row for row in rows}
    assert by_session["child-session"]["parent_session_id"] == "main-session"
    main_messages = by_session["main-session"]["messages"]
    assert main_messages[1]["tool_calls"][0]["name"] == "bash"
    assert "<REDACTED>" in str(main_messages[2]["content"])
    assert {artifact["kind"] for artifact in by_session["main-session"]["source_artifacts"]} >= {
        "opencode_events_jsonl",
        "opencode_storage_session",
        "opencode_storage_message",
        "opencode_storage_part",
    }


def test_provider_v1_schema_rejects_missing_required_field() -> None:
    row = {
        "schema_version": SCHEMA_VERSION,
        "row_type": "provider_v1_conversation",
        "experiment": "unit",
        "run_id": "run",
        "session_id": "session",
        "parent_session_id": None,
        "agent": "codex",
        "model": "gpt-5.4",
        "provider": "azure-foundry",
        "audit_id": "2024-01-canto",
        "mode": "detect",
        "messages": [],
        "labels": {
            "score": None,
            "max_score": None,
            "scored_positive": None,
            "zero_score": False,
            "failure": False,
            "failure_reason": None,
            "fallback": False,
            "timeout": False,
            "partial": False,
            "raw_artifacts_missing": False,
        },
        "source_artifacts": [],
        "provenance": {},
        "extensions": {},
    }
    del row["messages"]

    with pytest.raises(ProviderV1SchemaError, match="messages"):
        validate_provider_v1_row(row)


def test_provider_v1_redaction_handles_tokens_private_keys_and_host_paths() -> None:
    payload = {
        "text": (
            "OPENROUTER_API_KEY=sk-or-secret123456 "
            "Wallet Private Key: 0123456789abcdef0123456789abcdef0123456789abcdef0123456789abcdef "
            "/home/experiments_base/forestOfAudits/file"
        )
    }

    redacted = redact_json(payload)

    assert redacted == {
        "text": (
            "OPENROUTER_API_KEY=<REDACTED> "
            "Wallet Private Key: <REDACTED_PRIVATE_KEY> "
            "<HOST_PATH>"
        )
    }
