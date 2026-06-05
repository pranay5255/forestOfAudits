"""Validation and redaction helpers for provider-v1 conversation exports."""

from __future__ import annotations

import json
import re
from collections.abc import Mapping
from pathlib import Path
from typing import Any, NoReturn, TypeAlias, cast

from evmbench.experiments.schema_version import require_supported_schema_version

JsonObject: TypeAlias = dict[str, object]

ROW_TYPES = frozenset({"provider_v1_conversation"})
MODES = frozenset({"detect", "patch", "exploit", "unknown"})
AGENTS = frozenset({"codex", "opencode"})
MESSAGE_ROLES = frozenset({"system", "developer", "user", "assistant", "tool"})

REQUIRED_ROW_KEYS = frozenset(
    {
        "schema_version",
        "row_type",
        "experiment",
        "run_id",
        "session_id",
        "parent_session_id",
        "agent",
        "model",
        "provider",
        "audit_id",
        "mode",
        "messages",
        "labels",
        "source_artifacts",
        "provenance",
        "extensions",
    }
)


class ProviderV1SchemaError(ValueError):
    """Raised when a provider-v1 row violates the export schema."""


def _field(path: str, key: str) -> str:
    return f"{path}.{key}" if path else key


def _fail(path: str, message: str) -> NoReturn:
    raise ProviderV1SchemaError(f"{path}: {message}")


def _require(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        _fail(_field(path, key), "missing required field")
    return mapping[key]


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    if not all(isinstance(key, str) for key in value):
        _fail(path, "object keys must be strings")
    return cast(Mapping[str, object], value)


def _require_mapping(mapping: Mapping[str, object], key: str, path: str) -> Mapping[str, object]:
    return _as_mapping(_require(mapping, key, path), _field(path, key))


def _require_list(mapping: Mapping[str, object], key: str, path: str) -> list[object]:
    value = _require(mapping, key, path)
    if not isinstance(value, list):
        _fail(_field(path, key), "must be a list")
    return cast(list[object], value)


def _require_str(mapping: Mapping[str, object], key: str, path: str) -> str:
    value = _require(mapping, key, path)
    if not isinstance(value, str):
        _fail(_field(path, key), "must be a string")
    if value == "":
        _fail(_field(path, key), "must not be empty")
    return value


def _require_str_or_none(mapping: Mapping[str, object], key: str, path: str) -> str | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not isinstance(value, str):
        _fail(_field(path, key), "must be a string or null")
    return value


def _require_bool(mapping: Mapping[str, object], key: str, path: str) -> bool:
    value = _require(mapping, key, path)
    if not isinstance(value, bool):
        _fail(_field(path, key), "must be a boolean")
    return value


def _is_int(value: object) -> bool:
    return isinstance(value, int) and not isinstance(value, bool)


def _is_number(value: object) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _require_nonnegative_int(mapping: Mapping[str, object], key: str, path: str) -> int:
    value = _require(mapping, key, path)
    if not _is_int(value) or cast(int, value) < 0:
        _fail(_field(path, key), "must be a non-negative integer")
    return cast(int, value)


def _require_number_or_none(mapping: Mapping[str, object], key: str, path: str) -> float | None:
    value = _require(mapping, key, path)
    if value is None:
        return None
    if not _is_number(value):
        _fail(_field(path, key), "must be a number or null")
    return float(cast(float | int, value))


def _require_enum(mapping: Mapping[str, object], key: str, path: str, allowed: frozenset[str]) -> str:
    value = _require_str(mapping, key, path)
    if value not in allowed:
        _fail(_field(path, key), f"must be one of {sorted(allowed)}")
    return value


def _reject_unknown_keys(mapping: Mapping[str, object], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _fail(path or "<row>", f"unknown fields: {', '.join(unknown)}")


def validate_message(message: Mapping[str, object], path: str) -> JsonObject:
    _require_enum(message, "role", path, MESSAGE_ROLES)
    if "content" in message:
        content = message["content"]
        if content is not None and not isinstance(content, (str, list, Mapping)):
            _fail(_field(path, "content"), "must be a string, list, object, or null")
    if "tool_call_id" in message:
        _require_str_or_none(message, "tool_call_id", path)
    if "tool_calls" in message:
        calls = message["tool_calls"]
        if not isinstance(calls, list):
            _fail(_field(path, "tool_calls"), "must be a list")
        for index, raw_call in enumerate(calls):
            call = _as_mapping(raw_call, f"{_field(path, 'tool_calls')}[{index}]")
            _require_str(call, "id", f"{_field(path, 'tool_calls')}[{index}]")
            _require_str(call, "name", f"{_field(path, 'tool_calls')}[{index}]")
            if "arguments" in call and not isinstance(call["arguments"], (str, Mapping, list)):
                _fail(f"{_field(path, 'tool_calls')}[{index}].arguments", "must be a string, object, or list")
    if "metadata" in message:
        _as_mapping(message["metadata"], _field(path, "metadata"))
    return dict(message)


def validate_labels(labels: Mapping[str, object], path: str = "labels") -> JsonObject:
    allowed = frozenset(
        {
            "score",
            "max_score",
            "scored_positive",
            "zero_score",
            "failure",
            "failure_reason",
            "fallback",
            "timeout",
            "partial",
            "raw_artifacts_missing",
        }
    )
    _reject_unknown_keys(labels, allowed, path)
    _require_number_or_none(labels, "score", path)
    _require_number_or_none(labels, "max_score", path)
    scored_positive = _require(labels, "scored_positive", path)
    if scored_positive is not None and not isinstance(scored_positive, bool):
        _fail(_field(path, "scored_positive"), "must be a boolean or null")
    _require_bool(labels, "zero_score", path)
    _require_bool(labels, "failure", path)
    _require_str_or_none(labels, "failure_reason", path)
    _require_bool(labels, "fallback", path)
    _require_bool(labels, "timeout", path)
    _require_bool(labels, "partial", path)
    _require_bool(labels, "raw_artifacts_missing", path)
    return dict(labels)


def validate_source_artifact(artifact: Mapping[str, object], path: str) -> JsonObject:
    allowed = frozenset({"kind", "path", "size_bytes", "sha256", "exists"})
    _reject_unknown_keys(artifact, allowed, path)
    _require_str(artifact, "kind", path)
    _require_str(artifact, "path", path)
    _require_nonnegative_int(artifact, "size_bytes", path)
    sha256 = _require_str_or_none(artifact, "sha256", path)
    if sha256 is not None and not re.fullmatch(r"[a-f0-9]{64}", sha256):
        _fail(_field(path, "sha256"), "must be a lowercase SHA256 hex digest or null")
    _require_bool(artifact, "exists", path)
    return dict(artifact)


def validate_provider_v1_row(row: Mapping[str, object]) -> JsonObject:
    """Validate one normalized provider-v1 conversation row."""

    _reject_unknown_keys(row, REQUIRED_ROW_KEYS, "")
    require_supported_schema_version(_require(row, "schema_version", ""))
    _require_enum(row, "row_type", "", ROW_TYPES)
    _require_str(row, "experiment", "")
    _require_str(row, "run_id", "")
    _require_str(row, "session_id", "")
    _require_str_or_none(row, "parent_session_id", "")
    _require_enum(row, "agent", "", AGENTS)
    _require_str(row, "model", "")
    _require_str(row, "provider", "")
    _require_str(row, "audit_id", "")
    _require_enum(row, "mode", "", MODES)

    messages = _require_list(row, "messages", "")
    for index, raw_message in enumerate(messages):
        validate_message(_as_mapping(raw_message, f"messages[{index}]"), f"messages[{index}]")
    validate_labels(_require_mapping(row, "labels", ""), "labels")

    artifacts = _require_list(row, "source_artifacts", "")
    for index, raw_artifact in enumerate(artifacts):
        validate_source_artifact(_as_mapping(raw_artifact, f"source_artifacts[{index}]"), f"source_artifacts[{index}]")
    _as_mapping(_require(row, "provenance", ""), "provenance")
    _as_mapping(_require(row, "extensions", ""), "extensions")
    return dict(row)


_SECRET_ASSIGNMENT_RE = re.compile(
    r"(?i)\b("
    r"OPENAI_API_KEY|OPENROUTER_API_KEY|VLLM_API_KEY|AZURE_FOUNDRY_API_KEY|"
    r"GITHUB_TOKEN|GH_TOKEN|MODAL_TOKEN_ID|MODAL_TOKEN_SECRET|NPM_TOKEN|"
    r"DOCKER_PASSWORD|PRIVATE_KEY|API_KEY|PASSWORD|SECRET|TOKEN"
    r")\b\s*[:=]\s*([^\s'\";]+)"
)
_LABELED_PRIVATE_KEY_RE = re.compile(r"(?i)(private\s+key\s*[:=]\s*)(0x)?[a-f0-9]{64}")
_TOKEN_VALUE_RE = re.compile(
    r"(?i)\b("
    r"sk-[A-Za-z0-9_\-]{12,}|"
    r"github_pat_[A-Za-z0-9_]{12,}|"
    r"gh[pousr]_[A-Za-z0-9_]{12,}|"
    r"xox[baprs]-[A-Za-z0-9\-]{12,}"
    r")\b"
)
_CREDENTIAL_URL_RE = re.compile(r"([a-zA-Z][a-zA-Z0-9+.-]*://)([^/\s:@]+):([^/\s@]+)@")
_HOST_PATH_RE = re.compile(r"(?<!\w)(/home/(?!agent\b)[A-Za-z0-9._-]+/[^\s'\"`<>)]*)")
_USERS_PATH_RE = re.compile(r"(?<!\w)(/Users/[A-Za-z0-9._-]+/[^\s'\"`<>)]*)")


def redact_string(value: str) -> str:
    """Redact secrets and host-specific paths while preserving task content."""

    value = _SECRET_ASSIGNMENT_RE.sub(lambda match: f"{match.group(1)}=<REDACTED>", value)
    value = _LABELED_PRIVATE_KEY_RE.sub(lambda match: f"{match.group(1)}<REDACTED_PRIVATE_KEY>", value)
    value = _TOKEN_VALUE_RE.sub("<REDACTED_TOKEN>", value)
    value = _CREDENTIAL_URL_RE.sub(r"\1<REDACTED>@", value)
    value = _HOST_PATH_RE.sub("<HOST_PATH>", value)
    value = _USERS_PATH_RE.sub("<HOST_PATH>", value)
    return value


def redact_json(value: Any) -> Any:
    if isinstance(value, str):
        return redact_string(value)
    if isinstance(value, list):
        return [redact_json(item) for item in value]
    if isinstance(value, dict):
        return {str(key): redact_json(item) for key, item in value.items()}
    return value


def validate_provider_v1_jsonl(path: Path) -> list[JsonObject]:
    rows: list[JsonObject] = []
    with path.open(encoding="utf-8") as handle:
        for line_number, line in enumerate(handle, start=1):
            if not line.strip():
                continue
            payload = json.loads(line)
            rows.append(validate_provider_v1_row(_as_mapping(payload, f"{path}:{line_number}")))
    return rows
