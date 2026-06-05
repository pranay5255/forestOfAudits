#!/usr/bin/env python3
"""Extract normalized provider-v1 full-tool conversations from EVMBench runs."""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import subprocess
import sys
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterable, Mapping

from evmbench.experiments.provider_v1_trace_schema import (
    ProviderV1SchemaError,
    redact_json,
    redact_string,
    validate_provider_v1_row,
)
from evmbench.experiments.schema_version import EXTRACTOR_VERSION, SCHEMA_VERSION

CONVERSATIONS_JSONL = "provider_v1_conversations_v0.jsonl"
RAW_MANIFEST_JSON = "provider_v1_raw_manifest.json"
ERRORS_JSON = "extract-errors.json"


class ExtractError(RuntimeError):
    """Raised for provider-v1 extraction failures."""


@dataclass(frozen=True)
class ArtifactRef:
    path: Path
    kind: str


@dataclass(frozen=True)
class RunContext:
    input_root: Path
    run_dir: Path
    summary_row: dict[str, Any] | None


def _read_json(path: Path) -> Any | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8", errors="replace"))
    except json.JSONDecodeError as exc:
        raise ExtractError(f"{path}: invalid JSON: {exc}") from exc


def _read_json_object(path: Path) -> dict[str, Any] | None:
    value = _read_json(path)
    if value is None:
        return None
    if not isinstance(value, dict):
        raise ExtractError(f"{path}: expected JSON object")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    with path.open(encoding="utf-8", errors="replace") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            try:
                payload = json.loads(line)
            except json.JSONDecodeError as exc:
                raise ExtractError(f"{path}:{line_number}: invalid JSONL: {exc}") from exc
            if isinstance(payload, dict):
                rows.append(payload)
            else:
                raise ExtractError(f"{path}:{line_number}: expected JSON object")
    return rows


def _git_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            cwd=Path(__file__).resolve().parents[2],
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
        )
    except Exception:
        return "UNSET"
    return result.stdout.strip() or "UNSET"


def _sha256_file(path: Path) -> str | None:
    if not path.exists() or not path.is_file():
        return None
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _safe_rel(path: Path, base: Path) -> str:
    try:
        return path.resolve().relative_to(base.resolve()).as_posix()
    except ValueError:
        return redact_string(str(path))


def _artifact_dict(ref: ArtifactRef, base: Path) -> dict[str, Any]:
    exists = ref.path.exists() and ref.path.is_file()
    return {
        "kind": ref.kind,
        "path": _safe_rel(ref.path, base),
        "size_bytes": ref.path.stat().st_size if exists else 0,
        "sha256": _sha256_file(ref.path) if exists else None,
        "exists": exists,
    }


def _artifact_dicts(refs: Iterable[ArtifactRef], base: Path) -> list[dict[str, Any]]:
    artifacts: list[dict[str, Any]] = []
    seen: set[tuple[str, str]] = set()
    for ref in refs:
        key = (ref.kind, str(ref.path.resolve() if ref.path.exists() else ref.path))
        if key in seen:
            continue
        seen.add(key)
        artifacts.append(_artifact_dict(ref, base))
    return artifacts


def _summary_rows(input_root: Path) -> list[dict[str, Any]]:
    payload = _read_json_object(input_root / "openrouter-v1-results.json")
    if not payload:
        return []
    rows = payload.get("rows")
    return [row for row in rows if isinstance(row, dict)] if isinstance(rows, list) else []


def _audit_from_run_dir(run_dir: Path) -> str:
    return run_dir.name.split("_", 1)[0] or "unknown"


def _discover_from_summary(input_root: Path) -> list[RunContext]:
    contexts: list[RunContext] = []
    for row in _summary_rows(input_root):
        raw_run_dir = row.get("run_dir")
        if not raw_run_dir:
            continue
        run_dir = Path(str(raw_run_dir))
        if not run_dir.is_absolute():
            run_dir = input_root / run_dir
        if run_dir.exists():
            contexts.append(RunContext(input_root=input_root, run_dir=run_dir, summary_row=row))
    return contexts


def discover_runs(input_roots: Iterable[Path]) -> list[RunContext]:
    contexts: list[RunContext] = []
    seen: set[Path] = set()
    for raw_root in input_roots:
        input_root = raw_root.resolve()
        root_contexts = _discover_from_summary(input_root)
        if not root_contexts:
            if (input_root / "run.log").exists():
                root_contexts = [RunContext(input_root=input_root, run_dir=input_root, summary_row=None)]
            else:
                root_contexts = [
                    RunContext(input_root=input_root, run_dir=path.parent, summary_row=None)
                    for path in sorted(input_root.rglob("run.log"))
                ]
        for context in root_contexts:
            key = context.run_dir.resolve()
            if key in seen:
                continue
            seen.add(key)
            contexts.append(context)
    return contexts


def _candidate_log_dirs(run_dir: Path, agent: str) -> list[Path]:
    candidates = [run_dir / "logs" / agent, run_dir / "modal" / "logs" / agent]
    return [path for path in candidates if path.exists()]


def _trajectory_manifest(run_dir: Path, agent: str | None = None) -> tuple[Path | None, dict[str, Any] | None]:
    agents = [agent] if agent else ["codex", "opencode"]
    for candidate_agent in agents:
        for log_dir in _candidate_log_dirs(run_dir, candidate_agent):
            path = log_dir / "trajectory-manifest.json"
            if path.exists():
                return path, _read_json_object(path)
    return None, None


def _opencode_status(run_dir: Path) -> tuple[Path | None, dict[str, Any] | None]:
    for log_dir in _candidate_log_dirs(run_dir, "opencode"):
        path = log_dir / "status.json"
        if path.exists():
            return path, _read_json_object(path)
    return None, None


def _infer_agent(context: RunContext) -> str:
    row = context.summary_row or {}
    harness = row.get("harness")
    if harness in {"codex", "opencode"}:
        return str(harness)
    for agent in ("codex", "opencode"):
        if _candidate_log_dirs(context.run_dir, agent):
            return agent
    if list(context.run_dir.rglob("rollout-*.jsonl")):
        return "codex"
    return "opencode"


def _mode_from_path(run_dir: Path) -> str:
    for part in reversed(run_dir.parts):
        match = re.search(r"_(detect|patch|exploit)(?:$|/)", part)
        if match:
            return match.group(1)
    return "unknown"


def _context_value(context: RunContext, key: str) -> Any:
    if context.summary_row and key in context.summary_row:
        return context.summary_row[key]
    return None


def _metadata(context: RunContext, agent: str) -> dict[str, Any]:
    manifest_path, manifest = _trajectory_manifest(context.run_dir, agent)
    _, status = _opencode_status(context.run_dir) if agent == "opencode" else (None, None)
    manifest = manifest or {}
    status = status or {}
    return {
        "agent": agent,
        "provider": str(
            _context_value(context, "provider")
            or manifest.get("provider")
            or status.get("provider")
            or "unknown"
        ),
        "model": str(
            _context_value(context, "model")
            or manifest.get("model")
            or status.get("model")
            or "unknown"
        ),
        "audit_id": str(
            _context_value(context, "audit_id")
            or manifest.get("audit_id")
            or status.get("audit_id")
            or _audit_from_run_dir(context.run_dir)
        ),
        "mode": str(
            _context_value(context, "mode")
            or manifest.get("task_mode")
            or manifest.get("mode")
            or status.get("task_mode")
            or _mode_from_path(context.run_dir)
        ),
        "run_id": str(_context_value(context, "run_key") or context.run_dir.name),
        "manifest_path": manifest_path,
        "manifest": manifest,
        "opencode_status": status,
    }


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)) and not isinstance(value, bool):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _labels(context: RunContext, *, raw_missing: bool, extra_failure: str | None = None) -> dict[str, Any]:
    row = context.summary_row or {}
    score = _float_or_none(row.get("score"))
    max_score = _float_or_none(row.get("max_score"))
    failure_reason = row.get("failure_reason") if isinstance(row.get("failure_reason"), str) else None
    if extra_failure:
        failure_reason = f"{failure_reason}; {extra_failure}" if failure_reason else extra_failure
    command_status = row.get("command_status") if isinstance(row.get("command_status"), dict) else {}
    _, opencode_status = _opencode_status(context.run_dir)
    opencode_status = opencode_status or {}
    timeout = bool(command_status.get("timed_out")) or bool(opencode_status.get("timed_out"))
    fallback = bool(row.get("submission_fallback")) or bool(opencode_status.get("submission_fallback"))
    return {
        "score": score,
        "max_score": max_score,
        "scored_positive": (score > 0) if score is not None else None,
        "zero_score": score == 0 if score is not None else False,
        "failure": bool(failure_reason),
        "failure_reason": failure_reason,
        "fallback": fallback,
        "timeout": timeout,
        "partial": bool(raw_missing or extra_failure),
        "raw_artifacts_missing": raw_missing,
    }


def _provenance(context: RunContext, *, commit: str, raw_source: str) -> dict[str, Any]:
    return {
        "evmbench_commit": commit,
        "input_root": _safe_rel(context.input_root, Path.cwd()),
        "run_dir": _safe_rel(context.run_dir, context.input_root),
        "raw_source": raw_source,
        "extractor_version": EXTRACTOR_VERSION,
    }


def _common_row(
    context: RunContext,
    *,
    agent: str,
    session_id: str,
    parent_session_id: str | None,
    messages: list[dict[str, Any]],
    artifacts: list[dict[str, Any]],
    raw_source: str,
    commit: str,
    extensions: dict[str, Any] | None = None,
    extra_failure: str | None = None,
) -> dict[str, Any]:
    metadata = _metadata(context, agent)
    raw_missing = not artifacts
    row = {
        "schema_version": SCHEMA_VERSION,
        "row_type": "provider_v1_conversation",
        "experiment": "",
        "run_id": metadata["run_id"],
        "session_id": session_id,
        "parent_session_id": parent_session_id,
        "agent": agent,
        "model": metadata["model"],
        "provider": metadata["provider"],
        "audit_id": metadata["audit_id"],
        "mode": metadata["mode"] if metadata["mode"] in {"detect", "patch", "exploit"} else "unknown",
        "messages": messages,
        "labels": _labels(context, raw_missing=raw_missing, extra_failure=extra_failure),
        "source_artifacts": artifacts,
        "provenance": _provenance(context, commit=commit, raw_source=raw_source),
        "extensions": extensions or {},
    }
    return row


def _content_to_text(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value
    if isinstance(value, list):
        parts: list[str] = []
        for item in value:
            if isinstance(item, dict):
                for key in ("text", "input_text", "output_text", "content"):
                    if isinstance(item.get(key), str):
                        parts.append(str(item[key]))
                        break
                else:
                    parts.append(json.dumps(item, sort_keys=True, default=str))
            else:
                parts.append(str(item))
        return "\n".join(part for part in parts if part)
    if isinstance(value, dict):
        return json.dumps(value, sort_keys=True, default=str)
    return str(value)


def _decode_text_part(text: Any) -> str | None:
    if not isinstance(text, str):
        return None
    stripped = text.strip()
    if stripped.startswith('"') and stripped.endswith('"'):
        try:
            decoded = json.loads(stripped)
        except json.JSONDecodeError:
            return text
        return decoded if isinstance(decoded, str) else text
    return text


def _codex_rollout_refs(run_dir: Path) -> list[ArtifactRef]:
    return [ArtifactRef(path, "codex_rollout_jsonl") for path in sorted(run_dir.glob("sessions/**/rollout-*.jsonl"))]


def _codex_run_refs(run_dir: Path) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for log_dir in _candidate_log_dirs(run_dir, "codex"):
        for name, kind in [
            ("codex-run.jsonl", "codex_events_jsonl"),
            ("codex-stderr.log", "codex_stderr"),
            ("codex.traj.json", "trajectory_summary"),
            ("trajectory-manifest.json", "trajectory_manifest"),
        ]:
            path = log_dir / name
            if path.exists():
                refs.append(ArtifactRef(path, kind))
    return refs


def _codex_session_id(path: Path, events: list[dict[str, Any]]) -> str:
    for event in events:
        payload = event.get("payload")
        if event.get("type") == "session_meta" and isinstance(payload, dict) and isinstance(payload.get("id"), str):
            return str(payload["id"])
    return path.stem.removeprefix("rollout-")


def _codex_messages_from_events(events: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    messages: list[dict[str, Any]] = []
    token_counts: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        timestamp = event.get("timestamp")
        payload = event.get("payload")
        if not isinstance(payload, dict):
            continue
        if event.get("type") == "event_msg" and payload.get("type") == "token_count":
            token_counts.append({"timestamp": timestamp, "info": payload.get("info"), "rate_limits": payload.get("rate_limits")})
            continue
        if event.get("type") != "response_item":
            continue
        payload_type = payload.get("type")
        metadata = {"timestamp": timestamp, "source": "codex_rollout", "line_index": index}
        if payload_type == "message":
            role = payload.get("role")
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                role = "assistant"
            messages.append(
                {
                    "role": role,
                    "content": _content_to_text(payload.get("content")),
                    "metadata": metadata | {"codex_item_type": payload_type, "phase": payload.get("phase")},
                }
            )
        elif payload_type == "function_call":
            call_id = str(payload.get("call_id") or payload.get("id") or f"codex-call-{index}")
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [
                        {
                            "id": call_id,
                            "name": str(payload.get("name") or "unknown"),
                            "arguments": payload.get("arguments") if payload.get("arguments") is not None else "",
                        }
                    ],
                    "metadata": metadata | {"codex_item_type": payload_type},
                }
            )
        elif payload_type == "function_call_output":
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(payload.get("call_id") or ""),
                    "content": _content_to_text(payload.get("output")),
                    "metadata": metadata | {"codex_item_type": payload_type},
                }
            )
    return messages, {"codex_token_counts": token_counts}


def _parse_codex_run_jsonl(path: Path) -> tuple[str, list[dict[str, Any]], dict[str, Any]]:
    events = _read_jsonl(path)
    messages: list[dict[str, Any]] = []
    for index, event in enumerate(events):
        event_type = event.get("type")
        metadata = {"source": "codex_run_jsonl", "line_index": index}
        if event_type == "message":
            role = event.get("role")
            if role not in {"system", "developer", "user", "assistant", "tool"}:
                role = "assistant"
            messages.append({"role": role, "content": _content_to_text(event.get("content") or event.get("message")), "metadata": metadata})
        elif event_type in {"function_call", "tool_call"}:
            call_id = str(event.get("call_id") or event.get("id") or f"codex-debug-call-{index}")
            messages.append(
                {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"id": call_id, "name": str(event.get("name") or event.get("tool") or "unknown"), "arguments": event.get("arguments") or ""}],
                    "metadata": metadata,
                }
            )
        elif event_type in {"function_call_output", "tool_result"}:
            messages.append(
                {
                    "role": "tool",
                    "tool_call_id": str(event.get("call_id") or event.get("id") or ""),
                    "content": _content_to_text(event.get("output") or event.get("result")),
                    "metadata": metadata,
                }
            )
    return path.stem, messages, {"codex_debug_event_count": len(events)}


def extract_codex_rows(context: RunContext, *, experiment: str, commit: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    shared_refs = _codex_run_refs(context.run_dir)
    rollout_refs = _codex_rollout_refs(context.run_dir)
    for rollout_ref in rollout_refs:
        events = _read_jsonl(rollout_ref.path)
        messages, extensions = _codex_messages_from_events(events)
        artifacts = _artifact_dicts([rollout_ref, *shared_refs], context.input_root)
        row = _common_row(
            context,
            agent="codex",
            session_id=_codex_session_id(rollout_ref.path, events),
            parent_session_id=None,
            messages=messages,
            artifacts=artifacts,
            raw_source="sessions_rollout_jsonl",
            commit=commit,
            extensions=extensions,
        )
        row["experiment"] = experiment
        rows.append(_validate_redacted_row(row))
    if rows:
        return rows

    debug_paths = [ref for ref in shared_refs if ref.kind == "codex_events_jsonl"]
    for debug_ref in debug_paths:
        session_id, messages, extensions = _parse_codex_run_jsonl(debug_ref.path)
        artifacts = _artifact_dicts([debug_ref, *shared_refs], context.input_root)
        row = _common_row(
            context,
            agent="codex",
            session_id=session_id,
            parent_session_id=None,
            messages=messages,
            artifacts=artifacts,
            raw_source="codex_run_jsonl",
            commit=commit,
            extensions=extensions,
        )
        row["experiment"] = experiment
        rows.append(_validate_redacted_row(row))
    return rows


def _opencode_log_dirs(run_dir: Path) -> list[Path]:
    return _candidate_log_dirs(run_dir, "opencode")


def _opencode_run_refs(run_dir: Path) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for log_dir in _opencode_log_dirs(run_dir):
        for name, kind in [
            ("opencode-run.jsonl", "opencode_events_jsonl"),
            ("opencode-stderr.log", "opencode_stderr"),
            ("opencode.traj.json", "trajectory_summary"),
            ("trajectory-manifest.json", "trajectory_manifest"),
            ("status.json", "opencode_status"),
            ("state-index.json", "opencode_state_index"),
        ]:
            path = log_dir / name
            if path.exists():
                refs.append(ArtifactRef(path, kind))
    return refs


def _opencode_storage_roots(run_dir: Path) -> list[Path]:
    roots: list[Path] = []
    for log_dir in _opencode_log_dirs(run_dir):
        roots.extend(path for path in sorted((log_dir / "state").glob("**/storage")) if path.is_dir())
    return roots


def _opencode_storage_refs(storage_root: Path) -> list[ArtifactRef]:
    refs: list[ArtifactRef] = []
    for subdir, kind in [
        ("session", "opencode_storage_session"),
        ("message", "opencode_storage_message"),
        ("part", "opencode_storage_part"),
    ]:
        root = storage_root / subdir
        refs.extend(ArtifactRef(path, kind) for path in sorted(root.rglob("*.json")) if path.is_file())
    return refs


def _opencode_all_refs(run_dir: Path) -> list[ArtifactRef]:
    refs = _opencode_run_refs(run_dir)
    for storage_root in _opencode_storage_roots(run_dir):
        refs.extend(_opencode_storage_refs(storage_root))
    return refs


def _load_session_files(storage_root: Path) -> list[tuple[Path, dict[str, Any]]]:
    sessions: list[tuple[Path, dict[str, Any]]] = []
    for path in sorted((storage_root / "session").rglob("*.json")):
        payload = _read_json_object(path)
        if payload and isinstance(payload.get("id"), str):
            sessions.append((path, payload))
    sessions.sort(key=lambda item: (item[1].get("time", {}).get("created") if isinstance(item[1].get("time"), dict) else 0, str(item[1].get("id"))))
    return sessions


def _load_message_files(storage_root: Path, session_id: str) -> list[tuple[Path, dict[str, Any]]]:
    messages: list[tuple[Path, dict[str, Any]]] = []
    message_root = storage_root / "message" / session_id
    for path in sorted(message_root.glob("*.json")):
        payload = _read_json_object(path)
        if payload and isinstance(payload.get("id"), str):
            messages.append((path, payload))
    messages.sort(key=lambda item: (item[1].get("time", {}).get("created") if isinstance(item[1].get("time"), dict) else 0, str(item[1].get("id"))))
    return messages


def _load_part_files(storage_root: Path, message_id: str) -> list[tuple[Path, dict[str, Any]]]:
    parts: list[tuple[Path, dict[str, Any]]] = []
    part_root = storage_root / "part" / message_id
    for path in sorted(part_root.glob("*.json")):
        payload = _read_json_object(path)
        if payload and isinstance(payload.get("id"), str):
            parts.append((path, payload))
    return parts


def _opencode_message_from_storage(
    message: Mapping[str, Any],
    parts: list[tuple[Path, dict[str, Any]]],
) -> tuple[dict[str, Any] | None, list[dict[str, Any]], dict[str, Any]]:
    raw_role = message.get("role")
    role = raw_role if raw_role in {"system", "developer", "user", "assistant", "tool"} else "assistant"
    content_parts: list[str] = []
    tool_calls: list[dict[str, Any]] = []
    tool_outputs: list[dict[str, Any]] = []
    metadata: dict[str, Any] = {
        "source": "opencode_storage",
        "message_id": message.get("id"),
        "parent_message_id": message.get("parentID"),
        "time": message.get("time"),
    }
    for _, part in parts:
        part_type = part.get("type")
        if part_type == "text":
            text = _decode_text_part(part.get("text"))
            if text:
                content_parts.append(text)
        elif part_type == "tool":
            state = part.get("state") if isinstance(part.get("state"), dict) else {}
            call_id = str(part.get("callID") or part.get("id") or "")
            tool_name = str(part.get("tool") or "unknown")
            tool_calls.append({"id": call_id, "name": tool_name, "arguments": state.get("input") or {}})
            tool_outputs.append(
                {
                    "role": "tool",
                    "tool_call_id": call_id,
                    "content": _content_to_text(state.get("output")),
                    "metadata": {
                        "source": "opencode_storage",
                        "message_id": message.get("id"),
                        "part_id": part.get("id"),
                        "tool": tool_name,
                        "status": state.get("status"),
                        "title": state.get("title"),
                        "time": state.get("time"),
                        "metadata": state.get("metadata"),
                    },
                }
            )
        elif part_type == "step-finish":
            metadata["finish_reason"] = part.get("reason")
            metadata["tokens"] = part.get("tokens")
            metadata["cost"] = part.get("cost")
    content = "\n".join(part for part in content_parts if part).strip() or None
    if content is None and not tool_calls and role != "user":
        return None, [], metadata
    normalized: dict[str, Any] = {"role": role, "content": content, "metadata": metadata}
    if tool_calls:
        normalized["tool_calls"] = tool_calls
    return normalized, tool_outputs, metadata


def _extract_opencode_storage_rows_for_root(
    context: RunContext,
    storage_root: Path,
    *,
    experiment: str,
    commit: str,
    shared_refs: list[ArtifactRef],
) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for session_path, session in _load_session_files(storage_root):
        session_id = str(session["id"])
        row_messages: list[dict[str, Any]] = []
        row_refs = [ArtifactRef(session_path, "opencode_storage_session"), *shared_refs]
        message_count = 0
        part_count = 0
        for message_path, message in _load_message_files(storage_root, session_id):
            row_refs.append(ArtifactRef(message_path, "opencode_storage_message"))
            parts = _load_part_files(storage_root, str(message["id"]))
            row_refs.extend(ArtifactRef(part_path, "opencode_storage_part") for part_path, _ in parts)
            normalized, tool_outputs, _ = _opencode_message_from_storage(message, parts)
            if normalized:
                row_messages.append(normalized)
                row_messages.extend(tool_outputs)
            message_count += 1
            part_count += len(parts)
        artifacts = _artifact_dicts(row_refs, context.input_root)
        row = _common_row(
            context,
            agent="opencode",
            session_id=session_id,
            parent_session_id=str(session.get("parentID")) if session.get("parentID") else None,
            messages=row_messages,
            artifacts=artifacts,
            raw_source="opencode_storage",
            commit=commit,
            extensions={
                "opencode_session": {
                    "title": session.get("title"),
                    "time": session.get("time"),
                    "summary": session.get("summary"),
                    "storage_root": _safe_rel(storage_root, context.input_root),
                    "message_count": message_count,
                    "part_count": part_count,
                }
            },
        )
        row["experiment"] = experiment
        rows.append(_validate_redacted_row(row))
    return rows


def _extract_opencode_stream_rows(
    context: RunContext,
    event_ref: ArtifactRef,
    *,
    experiment: str,
    commit: str,
    shared_refs: list[ArtifactRef],
) -> list[dict[str, Any]]:
    events = _read_jsonl(event_ref.path)
    grouped: dict[str, list[dict[str, Any]]] = {}
    for event in events:
        session_id = str(event.get("sessionID") or "opencode-main")
        grouped.setdefault(session_id, []).append(event)
    rows: list[dict[str, Any]] = []
    for session_id, session_events in grouped.items():
        messages: list[dict[str, Any]] = []
        for index, event in enumerate(session_events):
            part = event.get("part")
            if not isinstance(part, dict):
                continue
            part_type = part.get("type")
            if part_type == "text":
                text = _decode_text_part(part.get("text"))
                if text:
                    messages.append({"role": "assistant", "content": text, "metadata": {"source": "opencode_run_jsonl", "line_index": index}})
            elif part_type == "tool":
                state = part.get("state") if isinstance(part.get("state"), dict) else {}
                call_id = str(part.get("callID") or part.get("id") or f"opencode-call-{index}")
                tool_name = str(part.get("tool") or "unknown")
                messages.append(
                    {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{"id": call_id, "name": tool_name, "arguments": state.get("input") or {}}],
                        "metadata": {"source": "opencode_run_jsonl", "line_index": index},
                    }
                )
                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call_id,
                        "content": _content_to_text(state.get("output")),
                        "metadata": {
                            "source": "opencode_run_jsonl",
                            "line_index": index,
                            "tool": tool_name,
                            "status": state.get("status"),
                            "metadata": state.get("metadata"),
                        },
                    }
                )
        row = _common_row(
            context,
            agent="opencode",
            session_id=session_id,
            parent_session_id=None,
            messages=messages,
            artifacts=_artifact_dicts([event_ref, *shared_refs], context.input_root),
            raw_source="opencode_run_jsonl",
            commit=commit,
            extensions={"opencode_event_count": len(session_events)},
        )
        row["experiment"] = experiment
        rows.append(_validate_redacted_row(row))
    return rows


def extract_opencode_rows(context: RunContext, *, experiment: str, commit: str) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    shared_refs = _opencode_run_refs(context.run_dir)
    for storage_root in _opencode_storage_roots(context.run_dir):
        rows.extend(
            _extract_opencode_storage_rows_for_root(
                context,
                storage_root,
                experiment=experiment,
                commit=commit,
                shared_refs=shared_refs,
            )
        )
    if rows:
        return rows
    for event_ref in [ref for ref in shared_refs if ref.kind == "opencode_events_jsonl"]:
        rows.extend(
            _extract_opencode_stream_rows(
                context,
                event_ref,
                experiment=experiment,
                commit=commit,
                shared_refs=shared_refs,
            )
        )
    return rows


def _validate_redacted_row(row: dict[str, Any]) -> dict[str, Any]:
    redacted = redact_json(row)
    if not isinstance(redacted, dict):
        raise ExtractError("redaction returned non-object row")
    try:
        validate_provider_v1_row(redacted)
    except (ProviderV1SchemaError, ValueError) as exc:
        raise ExtractError(f"provider-v1 row validation failed: {exc}") from exc
    return redacted


def _partial_row(context: RunContext, *, agent: str, experiment: str, commit: str, reason: str) -> dict[str, Any]:
    refs = _codex_run_refs(context.run_dir) if agent == "codex" else _opencode_all_refs(context.run_dir)
    row = _common_row(
        context,
        agent=agent,
        session_id=f"{agent}-missing-raw",
        parent_session_id=None,
        messages=[],
        artifacts=_artifact_dicts(refs, context.input_root),
        raw_source="missing_raw",
        commit=commit,
        extensions={"partial_reason": reason},
        extra_failure=reason,
    )
    row["experiment"] = experiment
    return _validate_redacted_row(row)


def _raw_refs_for_context(context: RunContext, agent: str) -> list[ArtifactRef]:
    if agent == "codex":
        return [*_codex_rollout_refs(context.run_dir), *_codex_run_refs(context.run_dir)]
    return _opencode_all_refs(context.run_dir)


def _raw_manifest_run(context: RunContext, agent: str) -> dict[str, Any]:
    metadata = _metadata(context, agent)
    refs = _raw_refs_for_context(context, agent)
    raw_missing = not refs or (
        agent == "codex"
        and not any(ref.kind in {"codex_rollout_jsonl", "codex_events_jsonl"} for ref in refs)
    ) or (
        agent == "opencode"
        and not any(ref.kind in {"opencode_events_jsonl", "opencode_storage_session"} for ref in refs)
    )
    return {
        "run_id": metadata["run_id"],
        "run_dir": _safe_rel(context.run_dir, context.input_root),
        "agent": agent,
        "provider": metadata["provider"],
        "model": metadata["model"],
        "audit_id": metadata["audit_id"],
        "mode": metadata["mode"],
        "score": _float_or_none((context.summary_row or {}).get("score")),
        "max_score": _float_or_none((context.summary_row or {}).get("max_score")),
        "labels": _labels(context, raw_missing=raw_missing),
        "raw_artifacts": _artifact_dicts(refs, context.input_root),
    }


def extract_provider_v1_traces(
    *,
    input_roots: list[Path],
    output_dir: Path,
    experiment: str,
    continue_on_error: bool = False,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    commit = _git_commit()
    contexts = discover_runs(input_roots)
    rows: list[dict[str, Any]] = []
    errors: list[dict[str, Any]] = []
    manifest_runs: list[dict[str, Any]] = []

    for context in contexts:
        agent = _infer_agent(context)
        try:
            manifest_runs.append(_raw_manifest_run(context, agent))
            extracted = (
                extract_codex_rows(context, experiment=experiment, commit=commit)
                if agent == "codex"
                else extract_opencode_rows(context, experiment=experiment, commit=commit)
            )
            if not extracted:
                extracted = [
                    _partial_row(
                        context,
                        agent=agent,
                        experiment=experiment,
                        commit=commit,
                        reason=f"missing {agent} raw conversation artifacts",
                    )
                ]
            rows.extend(extracted)
        except Exception as exc:
            error = {
                "run_dir": _safe_rel(context.run_dir, context.input_root),
                "agent": agent,
                "error": str(exc),
            }
            errors.append(error)
            if not continue_on_error:
                raise
            try:
                rows.append(_partial_row(context, agent=agent, experiment=experiment, commit=commit, reason=str(exc)))
            except Exception as partial_exc:
                errors.append(
                    {
                        "run_dir": _safe_rel(context.run_dir, context.input_root),
                        "agent": agent,
                        "error": f"failed to emit partial row: {partial_exc}",
                    }
                )

    conversations_path = output_dir / CONVERSATIONS_JSONL
    with conversations_path.open("w", encoding="utf-8") as handle:
        for row in rows:
            handle.write(json.dumps(row, sort_keys=True, separators=(",", ":")) + "\n")

    flat_artifacts = [
        artifact
        for run in manifest_runs
        for artifact in run.get("raw_artifacts", [])
        if isinstance(run.get("raw_artifacts"), list)
    ]
    raw_manifest = {
        "manifest_version": 1,
        "manifest_type": "provider_v1_raw_manifest",
        "experiment": experiment,
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "input_roots": [_safe_rel(path.resolve(), Path.cwd()) for path in input_roots],
        "run_count": len(manifest_runs),
        "conversation_count": len(rows),
        "raw_artifact_count": len(flat_artifacts),
        "runs": manifest_runs,
        "raw_artifacts": flat_artifacts,
    }
    manifest_path = output_dir / RAW_MANIFEST_JSON
    manifest_path.write_text(json.dumps(redact_json(raw_manifest), indent=2, sort_keys=True) + "\n", encoding="utf-8")

    if errors:
        (output_dir / ERRORS_JSON).write_text(json.dumps(errors, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    return {
        "output_dir": str(output_dir),
        "conversations_path": str(conversations_path),
        "raw_manifest_path": str(manifest_path),
        "run_count": len(manifest_runs),
        "conversation_count": len(rows),
        "error_count": len(errors),
    }


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input-root", action="append", type=Path, required=True, help="Provider-v1 output root or run dir. Repeatable.")
    parser.add_argument("--output-dir", type=Path, required=True)
    parser.add_argument("--experiment", default="azure_gpt54_provider_v1")
    parser.add_argument("--continue-on-error", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        payload = extract_provider_v1_traces(
            input_roots=args.input_root,
            output_dir=args.output_dir,
            experiment=args.experiment,
            continue_on_error=args.continue_on_error,
        )
    except Exception as exc:
        parser.exit(2, f"error: {exc}\n")
    print(json.dumps(payload, indent=2, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
