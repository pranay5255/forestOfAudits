from __future__ import annotations

import argparse
import csv
import hashlib
import json
import re
import shlex
from collections import Counter
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DEFAULT_RUNS_DIR = PROJECT_ROOT / "runs"
DEFAULT_OUTPUT_DIR = PROJECT_ROOT / "research-artifacts" / "bash-call-analysis"

SECRET_RE = re.compile(
    r"(?i)\b([A-Z0-9_]*(?:API[_-]?KEY|TOKEN|SECRET|PASSWORD|PRIVATE[_-]?KEY)[A-Z0-9_]*)"
    r"\s*=\s*([^\s;&|]+)"
)
URL_WITH_CREDENTIALS_RE = re.compile(r"(https?://)([^/@\s]+)@")
RUN_DIR_RE = re.compile(r"(?P<timestamp>\d{4}-\d{2}-\d{2}T[^/]+)_run-group_(?P<group>[^/]+)")
AUDIT_ID_RE = re.compile(r"(?P<audit_id>\d{4}-\d{2}-[A-Za-z0-9-]+)")
MODE_RE = re.compile(r"(?:^|[_/-])(?P<mode>detect|patch|exploit)(?:[_/-]|$)")
RUN_KEY_RE = re.compile(
    r"(?P<harness>codex|opencode|mini-swe-agent|modal-forest|modal)"
    r"--(?P<model>.+?)--(?P<mode>detect|patch|exploit)--(?P<audit_id>\d{4}-\d{2}-[^/]+)"
)

SEARCH_TOOLS = {"rg", "grep", "ripgrep", "ag", "ack"}
READ_TOOLS = {"cat", "sed", "head", "tail", "nl", "less", "more"}
LIST_TOOLS = {"ls", "find", "fd", "tree", "pwd", "wc", "du", "stat", "file"}
TEXT_PROCESS_TOOLS = {"awk", "sort", "uniq", "cut", "tr", "xargs", "jq"}
WRITE_TOOLS = {
    "apply_patch",
    "tee",
    "touch",
    "mkdir",
    "cp",
    "mv",
    "rm",
    "rmdir",
    "chmod",
    "chown",
    "install",
}
BUILD_TEST_TOOLS = {
    "forge",
    "hardhat",
    "pytest",
    "cargo",
    "go",
    "make",
    "cmake",
    "ninja",
}
CHAIN_TOOLS = {"cast", "anvil", "chisel"}
PACKAGE_TOOLS = {"npm", "npx", "pnpm", "yarn", "pip", "pip3", "uv", "poetry", "bun"}
GIT_TOOLS = {"git", "gh"}
LANGUAGE_TOOLS = {"python", "python3", "node", "ts-node", "bash", "sh"}


@dataclass
class BashCall:
    call_key: str
    source_file: str
    source_format: str
    source_type: str
    run_family: str
    experiment: str
    run_group: str
    run_dir: str
    audit_id: str
    mode: str
    harness: str
    agent: str
    model: str
    role: str
    call_id: str
    status: str
    exit_code: str
    timestamp: str
    duration_seconds: str
    workdir: str
    command: str
    inner_command: str
    command_hash: str
    primary_command: str
    executable_chain: str
    intent_category: str
    tool_family: str
    mutates_files: bool
    uses_shell_control: bool
    command_length: int
    segment_count: int
    line_count: int


def redact_command(command: str) -> str:
    command = SECRET_RE.sub(lambda match: f"{match.group(1)}=<redacted>", command)
    return URL_WITH_CREDENTIALS_RE.sub(r"\1<redacted>@", command)


def safe_json_loads(raw: str) -> Any | None:
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return None


def read_json(path: Path) -> Any | None:
    try:
        return json.loads(path.read_text(errors="ignore"))
    except (OSError, json.JSONDecodeError):
        return None


def iter_jsonl(path: Path) -> Iterable[tuple[int, dict[str, Any]]]:
    try:
        with path.open(errors="ignore") as handle:
            for line_no, raw in enumerate(handle, 1):
                raw = raw.strip()
                if not raw:
                    continue
                payload = safe_json_loads(raw)
                if isinstance(payload, dict):
                    yield line_no, payload
    except OSError:
        return


def relpath(path: Path) -> str:
    try:
        return str(path.relative_to(PROJECT_ROOT))
    except ValueError:
        return str(path)


def command_hash(command: str) -> str:
    return hashlib.sha256(command.encode("utf-8", "replace")).hexdigest()[:16]


def shell_join(command: Any) -> str:
    if isinstance(command, list):
        return " ".join(shlex.quote(str(part)) for part in command)
    return str(command)


def unwrap_bash_lc(command: str) -> str:
    try:
        parts = shlex.split(command)
    except ValueError:
        return command.strip()

    if len(parts) >= 3 and Path(parts[0]).name in {"bash", "sh"} and parts[1] in {"-lc", "-c"}:
        return parts[2].strip()
    return command.strip()


def shell_tokens(command: str) -> list[str]:
    try:
        lexer = shlex.shlex(command, posix=True, punctuation_chars="|&;()<>")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return command.replace("\n", " ").split()


def split_shell_segments(command: str) -> list[list[str]]:
    tokens = shell_tokens(command)
    segments: list[list[str]] = []
    current: list[str] = []
    separators = {";", "&&", "||", "|", "(", ")"}
    for token in tokens:
        if token in separators:
            if current:
                segments.append(current)
                current = []
            continue
        if token in {">", ">>", "<", "2>", "2>>", "&>"}:
            current.append(token)
            continue
        current.append(token)
    if current:
        segments.append(current)
    return segments


def is_env_assignment(token: str) -> bool:
    return bool(re.match(r"^[A-Za-z_][A-Za-z0-9_]*=", token))


def segment_executable(segment: list[str]) -> str:
    index = 0
    while index < len(segment) and is_env_assignment(segment[index]):
        index += 1
    if index >= len(segment):
        return ""
    if segment[index] in {"sudo", "env", "command", "time", "timeout", "nice"}:
        index += 1
        while index < len(segment) and (is_env_assignment(segment[index]) or segment[index].startswith("-")):
            index += 1
    if index >= len(segment):
        return ""
    return Path(segment[index]).name


def executable_chain(command: str) -> list[str]:
    executables = [segment_executable(segment) for segment in split_shell_segments(command)]
    return [executable for executable in executables if executable]


def command_has_redirection(command: str) -> bool:
    return bool(re.search(r"(^|\s)(>|>>|<|2>|2>>|&>)", command))


def command_uses_control(command: str) -> bool:
    return any(control in command for control in ["&&", "||", ";", "|", "$(", "`", "\n"])


def command_mutates_files(command: str, executables: list[str]) -> bool:
    lowered = command.lower()
    if command_has_redirection(command) and not re.search(r"(^|\s)<", command):
        return True
    if any(executable in WRITE_TOOLS for executable in executables):
        return True
    if "apply_patch" in lowered or "cat >" in lowered or "tee " in lowered:
        return True
    if re.search(r"\b(sed|perl)\b.*\s-i(\s|$)", lowered):
        return True
    if re.search(r"\b(npm|yarn|pnpm|bun)\s+(i|install|add|remove)\b", lowered):
        return True
    if re.search(r"\bgit\s+(add|commit|checkout|switch|merge|pull|push|reset|restore|rm|mv|clean)\b", lowered):
        return True
    if re.search(r"\b(forge|hardhat)\s+(build|test|script|compile)\b", lowered):
        return True
    return False


def classify_tool_family(primary: str, executables: list[str]) -> str:
    observed = set(executables)
    if observed & SEARCH_TOOLS:
        return "search"
    if observed & CHAIN_TOOLS:
        return "blockchain_rpc"
    if observed & BUILD_TEST_TOOLS:
        return "build_test"
    if observed & PACKAGE_TOOLS:
        return "package_manager"
    if observed & GIT_TOOLS:
        return "version_control"
    if observed & READ_TOOLS:
        return "file_reader"
    if observed & LIST_TOOLS:
        return "filesystem_inventory"
    if observed & TEXT_PROCESS_TOOLS:
        return "text_processing"
    if observed & LANGUAGE_TOOLS:
        return "language_runtime"
    if primary:
        return "other_cli"
    return "unknown"


def classify_intent(command: str, primary: str, executables: list[str], source_type: str) -> str:
    lowered = command.lower()
    observed = set(executables)
    if source_type in {"runner_command", "modal_runner_command"}:
        return "run_orchestration"
    if re.search(r"\bcomplete_task_and_submit_final_output\b|submission/audit\.md", lowered):
        return "submission_output"
    if command_mutates_files(command, executables):
        if re.search(r"\b(forge|hardhat|pytest|cargo|go|make|npm|yarn|pnpm)\s+(test|build|compile|run)\b", lowered):
            return "build_or_test"
        return "file_or_repo_mutation"
    if observed & CHAIN_TOOLS:
        return "blockchain_rpc_probe"
    if re.search(r"\b(forge|hardhat|pytest|cargo|go|make|npm|yarn|pnpm)\s+(test|build|compile|run)\b", lowered):
        return "build_or_test"
    if observed & GIT_TOOLS:
        return "version_control"
    if observed & SEARCH_TOOLS:
        return "code_search"
    if observed & READ_TOOLS or observed & LIST_TOOLS:
        return "code_or_file_inspection"
    if observed & TEXT_PROCESS_TOOLS:
        return "text_processing"
    if primary in LANGUAGE_TOOLS:
        return "script_or_runtime"
    return "other"


def infer_metadata(path: Path, payload: dict[str, Any] | None = None) -> dict[str, str]:
    path_text = relpath(path)
    parts = Path(path_text).parts
    run_family = parts[0] if parts else ""
    experiment = "/".join(parts[:2]) if len(parts) >= 2 else run_family

    run_group = ""
    run_dir = ""
    run_match = RUN_DIR_RE.search(path_text)
    if run_match:
        run_group = run_match.group("group")
        run_dir = path_text[: run_match.end()]

    audit_id = ""
    audit_matches = list(AUDIT_ID_RE.finditer(path_text))
    if audit_matches:
        audit_id = audit_matches[-1].group("audit_id")

    mode = ""
    mode_match = MODE_RE.search(path_text)
    if mode_match:
        mode = mode_match.group("mode")

    harness = ""
    if "/logs/codex/" in path_text or "/sessions/" in path_text or "codex--" in path_text:
        harness = "codex"
    elif "/logs/opencode/" in path_text or "opencode--" in path_text:
        harness = "opencode"
    elif "/logs/forest/" in path_text or "/modal/logs/" in path_text or "modal-forest" in path_text:
        harness = "mini-swe-agent-forest"

    model = ""
    run_key_match = RUN_KEY_RE.search(path_text)
    if run_key_match:
        harness = harness or run_key_match.group("harness")
        model = run_key_match.group("model")
        mode = mode or run_key_match.group("mode")
        audit_id = audit_id or run_key_match.group("audit_id")

    if payload:
        for key in ("harness", "agent", "agent_id", "model", "model_id", "mode", "audit_id", "run_key"):
            value = payload.get(key)
            if isinstance(value, str):
                if key == "agent_id" and not harness:
                    harness = value
                elif key in {"model", "model_id"} and not model:
                    model = value
                elif key == "mode" and not mode:
                    mode = value
                elif key == "audit_id" and not audit_id:
                    audit_id = value
                elif key == "run_key":
                    match = RUN_KEY_RE.search(value)
                    if match:
                        harness = harness or match.group("harness")
                        model = model or match.group("model")
                        mode = mode or match.group("mode")
                        audit_id = audit_id or match.group("audit_id")

    return {
        "run_family": run_family,
        "experiment": experiment,
        "run_group": run_group,
        "run_dir": run_dir,
        "audit_id": audit_id,
        "mode": mode,
        "harness": harness,
        "model": model,
    }


def normalize_record(
    *,
    path: Path,
    source_format: str,
    source_type: str,
    command: str,
    call_id: str = "",
    status: str = "",
    exit_code: Any = "",
    timestamp: Any = "",
    start_ms: Any = None,
    end_ms: Any = None,
    workdir: str = "",
    role: str = "",
    agent: str = "",
    payload: dict[str, Any] | None = None,
    key_hint: str = "",
) -> BashCall:
    redacted = redact_command(shell_join(command))
    inner = redact_command(unwrap_bash_lc(redacted))
    executables = executable_chain(inner)
    primary = next((executable for executable in executables if executable not in {"cd"}), "")
    if not primary and executables:
        primary = executables[0]
    metadata = infer_metadata(path, payload)
    duration = ""
    if isinstance(start_ms, (int, float)) and isinstance(end_ms, (int, float)) and end_ms >= start_ms:
        duration = f"{(end_ms - start_ms) / 1000:.3f}"
    raw_key = "|".join(
        [
            metadata["run_dir"] or relpath(path.parent),
            source_type,
            call_id,
            key_hint,
            inner,
        ]
    )
    call_key = hashlib.sha256(raw_key.encode("utf-8", "replace")).hexdigest()[:24]
    tool_family = classify_tool_family(primary, executables)
    intent_category = classify_intent(inner, primary, executables, source_type)
    return BashCall(
        call_key=call_key,
        source_file=relpath(path),
        source_format=source_format,
        source_type=source_type,
        run_family=metadata["run_family"],
        experiment=metadata["experiment"],
        run_group=metadata["run_group"],
        run_dir=metadata["run_dir"],
        audit_id=metadata["audit_id"],
        mode=metadata["mode"],
        harness=metadata["harness"],
        agent=agent or metadata["harness"],
        model=metadata["model"],
        role=role,
        call_id=call_id,
        status=str(status or ""),
        exit_code=str(exit_code if exit_code is not None else ""),
        timestamp=str(timestamp or ""),
        duration_seconds=duration,
        workdir=workdir,
        command=redacted,
        inner_command=inner,
        command_hash=command_hash(inner),
        primary_command=primary,
        executable_chain=" ".join(executables),
        intent_category=intent_category,
        tool_family=tool_family,
        mutates_files=command_mutates_files(inner, executables),
        uses_shell_control=command_uses_control(inner),
        command_length=len(inner),
        segment_count=max(len(split_shell_segments(inner)), 1 if inner else 0),
        line_count=max(inner.count("\n") + 1, 1 if inner else 0),
    )


def extract_mini_swe_traj(path: Path) -> list[BashCall]:
    payload = read_json(path)
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return []

    tool_results: dict[str, dict[str, Any]] = {}
    for message in payload["messages"]:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        tool_call_id = str(message.get("tool_call_id") or "")
        extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
        tool_results[tool_call_id] = {
            "exit_code": extra.get("returncode", ""),
            "timestamp": extra.get("timestamp", ""),
            "status": "completed" if "returncode" in extra else "",
        }

    records: list[BashCall] = []
    payload_metadata = payload.get("info", {}) if isinstance(payload.get("info"), dict) else {}
    if payload_metadata:
        payload_metadata = dict(payload_metadata)
    mode_hint = infer_mode_from_messages(payload["messages"])
    if mode_hint and isinstance(payload_metadata, dict):
        payload_metadata["mode"] = mode_hint
    for index, message in enumerate(payload["messages"]):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        role = infer_forest_role(path)
        for tool_call in message.get("tool_calls") or []:
            if not isinstance(tool_call, dict):
                continue
            function = tool_call.get("function")
            if not isinstance(function, dict) or function.get("name") != "bash":
                continue
            arguments = safe_json_loads(function.get("arguments", "{}"))
            if not isinstance(arguments, dict) or "command" not in arguments:
                continue
            call_id = str(tool_call.get("id") or "")
            result = tool_results.get(call_id, {})
            records.append(
                normalize_record(
                    path=path,
                    source_format="mini_swe_traj_json",
                    source_type="agent_bash_tool",
                    command=str(arguments["command"]),
                    call_id=call_id,
                    status=result.get("status", ""),
                    exit_code=result.get("exit_code", ""),
                    timestamp=result.get("timestamp", ""),
                    role=role,
                    payload=payload_metadata,
                    key_hint=f"message-{index}",
                )
            )
    return records


def infer_forest_role(path: Path) -> str:
    parts = list(path.parts)
    if "forest" not in parts:
        return ""
    idx = parts.index("forest")
    if idx + 1 >= len(parts):
        return ""
    next_part = parts[idx + 1]
    if next_part.endswith(".traj.json"):
        return next_part.replace(".traj.json", "")
    if idx + 2 < len(parts):
        return f"{next_part}/{parts[idx + 2].replace('.traj.json', '')}"
    return next_part


def infer_mode_from_messages(messages: list[Any]) -> str:
    for message in messages[:3]:
        if not isinstance(message, dict):
            continue
        content = message.get("content")
        if not isinstance(content, str):
            continue
        match = re.search(r"EVMBench\s+(detect|patch|exploit)\s+mode", content)
        if match:
            return match.group(1)
    return ""


def extract_codex_jsonl(path: Path) -> list[BashCall]:
    if path.name != "codex-run.jsonl":
        return []
    by_id: dict[str, BashCall] = {}
    for line_no, payload in iter_jsonl(path):
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not command:
            continue
        item_id = str(item.get("id") or f"line-{line_no}")
        existing = by_id.get(item_id)
        status = str(item.get("status") or "")
        if existing and existing.status == "completed" and status != "completed":
            continue
        by_id[item_id] = normalize_record(
            path=path,
            source_format="codex_run_jsonl",
            source_type="agent_command_execution",
            command=str(command),
            call_id=item_id,
            status=status,
            exit_code=item.get("exit_code", ""),
            timestamp=payload.get("timestamp", ""),
            payload=payload,
            key_hint=item_id,
        )
    return list(by_id.values())


def extract_opencode_jsonl(path: Path) -> list[BashCall]:
    if path.name != "opencode-run.jsonl":
        return []
    records: list[BashCall] = []
    for line_no, payload in iter_jsonl(path):
        part = payload.get("part")
        if not isinstance(part, dict) or part.get("type") != "tool" or part.get("tool") != "bash":
            continue
        state = part.get("state") if isinstance(part.get("state"), dict) else {}
        state_input = state.get("input") if isinstance(state.get("input"), dict) else {}
        command = state_input.get("command")
        if not command:
            continue
        time_info = state.get("time") if isinstance(state.get("time"), dict) else {}
        metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
        records.append(
            normalize_record(
                path=path,
                source_format="opencode_run_jsonl",
                source_type="agent_bash_tool",
                command=str(command),
                call_id=str(part.get("callID") or part.get("id") or f"line-{line_no}"),
                status=state.get("status", ""),
                exit_code=metadata.get("exit", ""),
                timestamp=payload.get("timestamp", time_info.get("start", "")),
                start_ms=time_info.get("start"),
                end_ms=time_info.get("end"),
                workdir=str(state_input.get("workdir") or ""),
                payload=payload,
                key_hint=str(part.get("id") or line_no),
            )
        )
    return records


def extract_opencode_part_json(path: Path) -> list[BashCall]:
    if "/storage/part/" not in str(path) or path.suffix != ".json":
        return []
    payload = read_json(path)
    if not isinstance(payload, dict) or payload.get("type") != "tool" or payload.get("tool") != "bash":
        return []
    state = payload.get("state") if isinstance(payload.get("state"), dict) else {}
    state_input = state.get("input") if isinstance(state.get("input"), dict) else {}
    command = state_input.get("command")
    if not command:
        return []
    time_info = state.get("time") if isinstance(state.get("time"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    return [
        normalize_record(
            path=path,
            source_format="opencode_state_part_json",
            source_type="agent_bash_tool",
            command=str(command),
            call_id=str(payload.get("callID") or payload.get("id") or ""),
            status=state.get("status", ""),
            exit_code=metadata.get("exit", ""),
            timestamp=time_info.get("start", ""),
            start_ms=time_info.get("start"),
            end_ms=time_info.get("end"),
            workdir=str(state_input.get("workdir") or ""),
            payload=payload,
            key_hint=str(payload.get("id") or ""),
        )
    ]


def extract_runner_commands(path: Path) -> list[BashCall]:
    if path.suffix != ".json":
        return []
    if not (
        path.name in {"phase6-results.json", "openrouter-v1-results.json", "modal-runner-command.json"}
        or path.name.endswith("-results.json")
        or "_task_results" in path.parts
    ):
        return []
    payload = read_json(path)
    if not isinstance(payload, dict):
        return []

    records: list[BashCall] = []
    seen: set[str] = set()

    def record(node: dict[str, Any], trail: str, source_type: str) -> None:
        command = node.get("command")
        if not (
            isinstance(command, list)
            and command
            and all(isinstance(part, (str, int, float)) for part in command)
        ):
            return
        serialized = shell_join(command)
        dedupe = "|".join(
            [
                serialized,
                str(node.get("run_key") or node.get("agent_id") or ""),
                str(node.get("started_at") or ""),
                str(node.get("returncode") if node.get("returncode") is not None else ""),
            ]
        )
        if dedupe in seen:
            return
        seen.add(dedupe)
        records.append(
            normalize_record(
                path=path,
                source_format="runner_metadata_json",
                source_type=source_type,
                command=serialized,
                call_id=str(node.get("run_key") or node.get("agent_id") or ""),
                status="completed" if node.get("returncode") == 0 else str(node.get("status") or ""),
                exit_code=node.get("returncode", ""),
                timestamp=node.get("started_at", ""),
                start_ms=(node.get("started_at") * 1000 if isinstance(node.get("started_at"), (int, float)) else None),
                end_ms=(node.get("ended_at") * 1000 if isinstance(node.get("ended_at"), (int, float)) else None),
                payload=node,
                key_hint=trail,
            )
        )

    def visit(node: Any, trail: str = "") -> None:
        if isinstance(node, dict):
            if path.name == "modal-runner-command.json" and trail == "":
                record(node, trail, "modal_runner_command")
            elif "returncode" in node or ("started_at" in node and "ended_at" in node):
                record(node, trail, "runner_command")
            for key, value in node.items():
                if key in {"env", "aggregate", "files"}:
                    continue
                visit(value, f"{trail}.{key}" if trail else key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{trail}[{index}]")

    visit(payload)
    return records


def discover_candidate_files(runs_dir: Path) -> list[Path]:
    candidates: list[Path] = []
    for path in runs_dir.rglob("*"):
        if not path.is_file():
            continue
        if path.name in {"codex-run.jsonl", "opencode-run.jsonl"}:
            candidates.append(path)
        elif path.name.endswith(".traj.json"):
            candidates.append(path)
        elif path.suffix == ".json" and (
            "/storage/part/" in str(path)
            or path.name in {"phase6-results.json", "openrouter-v1-results.json", "modal-runner-command.json"}
            or path.name.endswith("-results.json")
            or "_task_results" in path.parts
        ):
            candidates.append(path)
    return sorted(candidates)


def extract_calls(runs_dir: Path) -> tuple[list[BashCall], dict[str, int]]:
    stats = Counter()
    calls: dict[str, BashCall] = {}
    for path in discover_candidate_files(runs_dir):
        stats["candidate_files"] += 1
        extracted: list[BashCall] = []
        extracted.extend(extract_mini_swe_traj(path))
        extracted.extend(extract_codex_jsonl(path))
        extracted.extend(extract_opencode_jsonl(path))
        extracted.extend(extract_opencode_part_json(path))
        extracted.extend(extract_runner_commands(path))
        if extracted:
            stats["files_with_calls"] += 1
        for call in extracted:
            stats[f"source_format:{call.source_format}"] += 1
            if call.call_key in calls:
                stats["deduped_calls"] += 1
                existing = calls[call.call_key]
                if existing.source_format == "opencode_state_part_json" and call.source_format == "opencode_run_jsonl":
                    calls[call.call_key] = call
                continue
            calls[call.call_key] = call
    return list(calls.values()), dict(stats)


def write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def counter_rows(counter: Counter[tuple[Any, ...] | Any], columns: list[str]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for key, value in counter.most_common():
        if not isinstance(key, tuple):
            key = (key,)
        row = {column: key[index] if index < len(key) else "" for index, column in enumerate(columns)}
        row["count"] = value
        rows.append(row)
    return rows


def analysis_run_key(call: BashCall) -> str:
    if call.run_dir:
        return call.run_dir
    if call.call_id:
        return call.call_id
    return str(Path(call.source_file).parent)


def short_run_label(run_key: str) -> str:
    parts = Path(run_key).parts
    if "evmbench_runs" in parts:
        idx = parts.index("evmbench_runs")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    if len(parts) >= 4 and parts[0] == "runs" and parts[1] in {"phase6", "rca", "vllm-smoke"}:
        return "/".join(parts[1:4])
    if "modal-forest-qwen-vllm-4trees-debug" in parts:
        return "modal-forest-qwen-vllm-4trees-debug"
    if "modal-forest-qwen-vllm-2trees-debug" in parts:
        return "modal-forest-qwen-vllm-2trees-debug"
    if len(parts) >= 2:
        return "/".join(parts[-2:])
    return run_key


def run_rows(calls: list[BashCall]) -> list[dict[str, Any]]:
    grouped: dict[str, list[BashCall]] = {}
    for call in calls:
        grouped.setdefault(analysis_run_key(call), []).append(call)

    rows: list[dict[str, Any]] = []
    for run_key, run_calls in grouped.items():
        first = run_calls[0]
        intents = Counter(call.intent_category for call in run_calls)
        primary_commands = Counter(call.primary_command or "unknown" for call in run_calls)
        tool_families = Counter(call.tool_family or "unknown" for call in run_calls)
        exit_buckets = Counter(exit_bucket(call.exit_code) for call in run_calls)
        total = len(run_calls)
        row = {
            "run_key": run_key,
            "run_label": short_run_label(run_key),
            "experiment": first.experiment or "unknown",
            "harness": first.harness or "unknown",
            "agent": first.agent or "unknown",
            "model": first.model or "unknown",
            "mode": first.mode or "unknown",
            "audit_id": first.audit_id or "unknown",
            "roles": ", ".join(sorted({call.role for call in run_calls if call.role})),
            "source_types": ", ".join(sorted({call.source_type for call in run_calls})),
            "total_calls": total,
            "agent_calls": sum(1 for call in run_calls if call.source_type.startswith("agent")),
            "runner_calls": sum(1 for call in run_calls if not call.source_type.startswith("agent")),
            "unique_commands": len({call.command_hash for call in run_calls}),
            "top_intent": intents.most_common(1)[0][0] if intents else "",
            "top_intent_calls": intents.most_common(1)[0][1] if intents else 0,
            "top_primary_command": primary_commands.most_common(1)[0][0] if primary_commands else "",
            "top_primary_command_calls": primary_commands.most_common(1)[0][1] if primary_commands else 0,
            "top_tool_family": tool_families.most_common(1)[0][0] if tool_families else "",
            "top_tool_family_calls": tool_families.most_common(1)[0][1] if tool_families else 0,
            "inspection_calls": sum(
                1
                for call in run_calls
                if call.intent_category in {"code_or_file_inspection", "code_search"}
            ),
            "blockchain_rpc_calls": intents.get("blockchain_rpc_probe", 0),
            "mutation_calls": sum(
                1
                for call in run_calls
                if call.intent_category
                in {"file_or_repo_mutation", "build_or_test", "submission_output"}
            ),
            "compound_calls": sum(1 for call in run_calls if call.uses_shell_control),
            "mutating_flag_calls": sum(1 for call in run_calls if call.mutates_files),
            "nonzero_exit_calls": exit_buckets.get("nonzero", 0),
            "unknown_exit_calls": exit_buckets.get("unknown", 0),
        }
        rows.append(row)

    return sorted(rows, key=lambda row: (-int(row["total_calls"]), str(row["run_label"])))


def write_summary_tables(calls: list[BashCall], output_dir: Path) -> dict[str, Path]:
    paths: dict[str, Path] = {}
    call_dicts = [asdict(call) for call in sorted(calls, key=lambda item: (item.run_family, item.source_file, item.timestamp, item.call_id))]
    commands_csv = output_dir / "bash_calls.csv"
    write_csv(commands_csv, call_dicts, list(BashCall.__dataclass_fields__.keys()))
    paths["commands"] = commands_csv

    table_specs = {
        "by_intent": (Counter(call.intent_category for call in calls), ["intent_category"]),
        "by_tool_family": (Counter(call.tool_family for call in calls), ["tool_family"]),
        "by_primary_command": (Counter(call.primary_command or "unknown" for call in calls), ["primary_command"]),
        "by_harness": (Counter(call.harness or "unknown" for call in calls), ["harness"]),
        "by_source_type": (Counter(call.source_type for call in calls), ["source_type"]),
        "by_mode": (Counter(call.mode or "unknown" for call in calls), ["mode"]),
        "by_audit": (Counter(call.audit_id or "unknown" for call in calls), ["audit_id"]),
        "by_experiment": (Counter(call.experiment or "unknown" for call in calls), ["experiment"]),
        "intent_by_harness": (
            Counter((call.harness or "unknown", call.intent_category) for call in calls),
            ["harness", "intent_category"],
        ),
        "intent_by_mode": (
            Counter((call.mode or "unknown", call.intent_category) for call in calls),
            ["mode", "intent_category"],
        ),
        "tool_family_by_experiment": (
            Counter((call.experiment or "unknown", call.tool_family) for call in calls),
            ["experiment", "tool_family"],
        ),
        "exit_by_intent": (
            Counter((call.intent_category, exit_bucket(call.exit_code)) for call in calls),
            ["intent_category", "exit_bucket"],
        ),
        "intent_by_run": (
            Counter((analysis_run_key(call), call.intent_category) for call in calls),
            ["run_key", "intent_category"],
        ),
        "primary_command_by_run": (
            Counter((analysis_run_key(call), call.primary_command or "unknown") for call in calls),
            ["run_key", "primary_command"],
        ),
    }
    for name, (counter, columns) in table_specs.items():
        rows = counter_rows(counter, columns)
        table_path = output_dir / f"{name}.csv"
        write_csv(table_path, rows, columns + ["count"])
        paths[name] = table_path

    summary_rows = run_rows(calls)
    run_summary_path = output_dir / "run_summary.csv"
    run_summary_columns = [
        "run_key",
        "run_label",
        "experiment",
        "harness",
        "agent",
        "model",
        "mode",
        "audit_id",
        "roles",
        "source_types",
        "total_calls",
        "agent_calls",
        "runner_calls",
        "unique_commands",
        "top_intent",
        "top_intent_calls",
        "top_primary_command",
        "top_primary_command_calls",
        "top_tool_family",
        "top_tool_family_calls",
        "inspection_calls",
        "blockchain_rpc_calls",
        "mutation_calls",
        "compound_calls",
        "mutating_flag_calls",
        "nonzero_exit_calls",
        "unknown_exit_calls",
    ]
    write_csv(run_summary_path, summary_rows, run_summary_columns)
    paths["run_summary"] = run_summary_path

    intent_columns = sorted({call.intent_category for call in calls})
    run_intent_rows: list[dict[str, Any]] = []
    by_run_intent = Counter((analysis_run_key(call), call.intent_category) for call in calls)
    by_run_total = Counter(analysis_run_key(call) for call in calls)
    summary_by_run = {row["run_key"]: row for row in summary_rows}
    for run_key, summary in summary_by_run.items():
        row = {
            "run_key": run_key,
            "run_label": summary["run_label"],
            "harness": summary["harness"],
            "mode": summary["mode"],
            "audit_id": summary["audit_id"],
            "total_calls": by_run_total[run_key],
        }
        for intent in intent_columns:
            row[intent] = by_run_intent.get((run_key, intent), 0)
        run_intent_rows.append(row)
    run_intent_matrix_path = output_dir / "run_intent_matrix.csv"
    write_csv(
        run_intent_matrix_path,
        run_intent_rows,
        ["run_key", "run_label", "harness", "mode", "audit_id", "total_calls"] + intent_columns,
    )
    paths["run_intent_matrix"] = run_intent_matrix_path

    agent_only_calls = [call for call in calls if call.source_type.startswith("agent")]
    agent_summary_rows = run_rows(agent_only_calls)
    agent_run_summary_path = output_dir / "agent_run_summary.csv"
    write_csv(agent_run_summary_path, agent_summary_rows, run_summary_columns)
    paths["agent_run_summary"] = agent_run_summary_path

    agent_intent_columns = sorted({call.intent_category for call in agent_only_calls})
    agent_run_intent_rows: list[dict[str, Any]] = []
    agent_by_run_intent = Counter((analysis_run_key(call), call.intent_category) for call in agent_only_calls)
    agent_by_run_total = Counter(analysis_run_key(call) for call in agent_only_calls)
    agent_summary_by_run = {row["run_key"]: row for row in agent_summary_rows}
    for run_key, summary in agent_summary_by_run.items():
        row = {
            "run_key": run_key,
            "run_label": summary["run_label"],
            "harness": summary["harness"],
            "mode": summary["mode"],
            "audit_id": summary["audit_id"],
            "total_calls": agent_by_run_total[run_key],
        }
        for intent in agent_intent_columns:
            row[intent] = agent_by_run_intent.get((run_key, intent), 0)
        agent_run_intent_rows.append(row)
    agent_run_intent_matrix_path = output_dir / "agent_run_intent_matrix.csv"
    write_csv(
        agent_run_intent_matrix_path,
        agent_run_intent_rows,
        ["run_key", "run_label", "harness", "mode", "audit_id", "total_calls"] + agent_intent_columns,
    )
    paths["agent_run_intent_matrix"] = agent_run_intent_matrix_path
    return paths


def exit_bucket(exit_code: str) -> str:
    if exit_code == "":
        return "unknown"
    if exit_code == "0":
        return "zero"
    return "nonzero"


def require_plotting() -> tuple[Any, Any, Any]:
    try:
        import matplotlib.pyplot as plt
        import pandas as pd
        import seaborn as sns
    except ImportError as exc:
        raise SystemExit(
            "This report needs pandas, matplotlib, and seaborn. "
            "Run with: uv run --with pandas --with seaborn python scripts/analyze_run_bash_calls.py"
        ) from exc
    return pd, sns, plt


def save_barplot(
    *,
    df: Any,
    x: str,
    y: str,
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    sns: Any,
    plt: Any,
    hue: str | None = None,
    width: float = 11,
    height: float = 7,
) -> None:
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(width, height))
    if hue:
        sns.barplot(data=df, x=x, y=y, hue=hue, palette="deep", ax=ax)
    else:
        sns.barplot(data=df, x=x, y=y, color=sns.color_palette("deep")[0], ax=ax)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    max_label = int(df[y].astype(str).map(len).max()) if y in df else 0
    left_margin = min(0.56, max(0.20, max_label * 0.006))
    fig.subplots_adjust(left=left_margin, right=0.98, top=0.90, bottom=0.12)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_countplot(
    *,
    df: Any,
    x: str,
    path: Path,
    title: str,
    xlabel: str,
    ylabel: str,
    sns: Any,
    plt: Any,
    hue: str | None = None,
    width: float = 11,
    height: float = 7,
) -> None:
    if df.empty:
        return
    fig, ax = plt.subplots(figsize=(width, height))
    order = df[x].value_counts().index
    if hue:
        sns.countplot(data=df, y=x, hue=hue, order=order, palette="deep", ax=ax)
    else:
        sns.countplot(data=df, y=x, order=order, color=sns.color_palette("deep")[0], ax=ax)
    ax.set_title(title)
    ax.set_xlabel(xlabel)
    ax.set_ylabel(ylabel)
    max_label = int(df[x].astype(str).map(len).max()) if x in df else 0
    left_margin = min(0.56, max(0.20, max_label * 0.006))
    fig.subplots_adjust(left=left_margin, right=0.98, top=0.90, bottom=0.12)
    fig.savefig(path, dpi=180)
    plt.close(fig)


def save_heatmap(
    *,
    pivot: Any,
    path: Path,
    title: str,
    sns: Any,
    plt: Any,
    width: float = 12,
    height: float = 8,
) -> None:
    if pivot.empty:
        return
    plt.figure(figsize=(width, height), constrained_layout=True)
    sns.heatmap(pivot, cmap="viridis", annot=True, fmt=".0f", linewidths=0.3)
    plt.title(title)
    plt.savefig(path, dpi=180)
    plt.close()


def make_plots(calls: list[BashCall], output_dir: Path) -> list[Path]:
    pd, sns, plt = require_plotting()
    sns.set_theme(style="whitegrid", context="talk")
    plots_dir = output_dir / "plots"
    plots_dir.mkdir(parents=True, exist_ok=True)
    df = pd.DataFrame(asdict(call) for call in calls)
    if df.empty:
        return []

    for column in ["harness", "mode", "audit_id", "experiment", "primary_command"]:
        df[column] = df[column].replace("", "unknown")
    plot_paths: list[Path] = []

    top_intent = df["intent_category"].value_counts().reset_index()
    top_intent.columns = ["intent_category", "count"]
    path = plots_dir / "intent_category_counts.png"
    save_barplot(
        df=top_intent,
        x="count",
        y="intent_category",
        path=path,
        title="Bash Calls by Intent Category",
        xlabel="Calls",
        ylabel="Intent",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    top_tools = df["primary_command"].value_counts().head(25).reset_index()
    top_tools.columns = ["primary_command", "count"]
    path = plots_dir / "top_primary_commands.png"
    save_barplot(
        df=top_tools,
        x="count",
        y="primary_command",
        path=path,
        title="Top Primary Commands",
        xlabel="Calls",
        ylabel="Executable",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    path = plots_dir / "tool_family_counts.png"
    top_family = df["tool_family"].value_counts().reset_index()
    top_family.columns = ["tool_family", "count"]
    save_barplot(
        df=top_family,
        x="count",
        y="tool_family",
        path=path,
        title="Bash Calls by Tool Family",
        xlabel="Calls",
        ylabel="Tool family",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    path = plots_dir / "harness_counts.png"
    save_countplot(
        df=df,
        x="harness",
        path=path,
        title="Bash Calls by Harness",
        xlabel="Calls",
        ylabel="Harness",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    path = plots_dir / "source_type_counts.png"
    save_countplot(
        df=df,
        x="source_type",
        path=path,
        title="Bash Calls by Source Type",
        xlabel="Calls",
        ylabel="Source type",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    path = plots_dir / "mode_counts.png"
    save_countplot(
        df=df,
        x="mode",
        path=path,
        title="Bash Calls by EVMBench Mode",
        xlabel="Calls",
        ylabel="Mode",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    top_exact = df.groupby("inner_command", as_index=False).size().sort_values("size", ascending=False).head(20)
    top_exact["short_command"] = top_exact["inner_command"].str.slice(0, 100)
    path = plots_dir / "top_exact_commands.png"
    save_barplot(
        df=top_exact,
        x="size",
        y="short_command",
        path=path,
        title="Top Repeated Exact Commands",
        xlabel="Calls",
        ylabel="Command prefix",
        sns=sns,
        plt=plt,
        height=9,
    )
    plot_paths.append(path)

    path = plots_dir / "command_length_distribution.png"
    plt.figure(figsize=(11, 7))
    sns.histplot(data=df, x="command_length", bins=50, kde=True)
    plt.title("Command Length Distribution")
    plt.xlabel("Characters in normalized command")
    plt.ylabel("Calls")
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()
    plot_paths.append(path)

    path = plots_dir / "segment_count_distribution.png"
    plt.figure(figsize=(11, 7))
    sns.countplot(data=df, x="segment_count", color=sns.color_palette("deep")[0])
    plt.title("Shell Segment Count Distribution")
    plt.xlabel("Command segments")
    plt.ylabel("Calls")
    plt.tight_layout()
    plt.savefig(path, dpi=180)
    plt.close()
    plot_paths.append(path)

    path = plots_dir / "mutation_share_by_intent.png"
    mutation_df = (
        df.groupby(["intent_category", "mutates_files"], as_index=False)
        .size()
        .sort_values("size", ascending=False)
    )
    save_barplot(
        df=mutation_df,
        x="size",
        y="intent_category",
        hue="mutates_files",
        path=path,
        title="Mutation Flag by Intent Category",
        xlabel="Calls",
        ylabel="Intent",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    path = plots_dir / "exit_bucket_by_intent.png"
    exit_df = df.assign(exit_bucket=df["exit_code"].map(exit_bucket))
    exit_counts = exit_df.groupby(["intent_category", "exit_bucket"], as_index=False).size()
    save_barplot(
        df=exit_counts,
        x="size",
        y="intent_category",
        hue="exit_bucket",
        path=path,
        title="Exit-Code Availability by Intent",
        xlabel="Calls",
        ylabel="Intent",
        sns=sns,
        plt=plt,
    )
    plot_paths.append(path)

    intent_harness = df.pivot_table(
        index="harness",
        columns="intent_category",
        values="call_key",
        aggfunc="count",
        fill_value=0,
    )
    path = plots_dir / "intent_by_harness_heatmap.png"
    save_heatmap(
        pivot=intent_harness,
        path=path,
        title="Intent Category by Harness",
        sns=sns,
        plt=plt,
        width=15,
        height=max(6, 0.55 * len(intent_harness.index) + 4),
    )
    plot_paths.append(path)

    intent_mode = df.pivot_table(
        index="mode",
        columns="intent_category",
        values="call_key",
        aggfunc="count",
        fill_value=0,
    )
    path = plots_dir / "intent_by_mode_heatmap.png"
    save_heatmap(
        pivot=intent_mode,
        path=path,
        title="Intent Category by Mode",
        sns=sns,
        plt=plt,
        width=15,
        height=7,
    )
    plot_paths.append(path)

    tool_experiment = df.pivot_table(
        index="experiment",
        columns="tool_family",
        values="call_key",
        aggfunc="count",
        fill_value=0,
    )
    if len(tool_experiment.index) > 20:
        keep = df["experiment"].value_counts().head(20).index
        tool_experiment = tool_experiment.loc[tool_experiment.index.intersection(keep)]
    path = plots_dir / "tool_family_by_experiment_heatmap.png"
    save_heatmap(
        pivot=tool_experiment,
        path=path,
        title="Tool Family by Experiment",
        sns=sns,
        plt=plt,
        width=15,
        height=max(8, 0.45 * len(tool_experiment.index) + 4),
    )
    plot_paths.append(path)

    run_counts = df.groupby("run_dir", as_index=False).size()
    run_counts["run_label"] = run_counts["run_dir"].replace("", "unknown").str.slice(-95)
    run_counts = run_counts.sort_values("size", ascending=False).head(25)
    path = plots_dir / "top_runs_by_bash_calls.png"
    save_barplot(
        df=run_counts,
        x="size",
        y="run_label",
        path=path,
        title="Runs with the Most Bash Calls",
        xlabel="Calls",
        ylabel="Run directory suffix",
        sns=sns,
        plt=plt,
        height=10,
    )
    plot_paths.append(path)

    timestamp_df = df.copy()
    timestamp_df["timestamp_numeric"] = pd.to_numeric(timestamp_df["timestamp"], errors="coerce")
    timestamp_df = timestamp_df[timestamp_df["timestamp_numeric"].notna()]
    if not timestamp_df.empty:
        timestamp_df["timestamp_dt"] = pd.to_datetime(timestamp_df["timestamp_numeric"], unit="s", errors="coerce")
        timestamp_df.loc[timestamp_df["timestamp_numeric"] > 10_000_000_000, "timestamp_dt"] = pd.to_datetime(
            timestamp_df.loc[timestamp_df["timestamp_numeric"] > 10_000_000_000, "timestamp_numeric"],
            unit="ms",
            errors="coerce",
        )
        timestamp_df = timestamp_df[timestamp_df["timestamp_dt"].notna()]
        if not timestamp_df.empty:
            timeline = (
                timestamp_df.set_index("timestamp_dt")
                .groupby("intent_category")
                .resample("30min")["call_key"]
                .count()
                .reset_index()
            )
            path = plots_dir / "bash_calls_timeline.png"
            plt.figure(figsize=(13, 7))
            sns.lineplot(data=timeline, x="timestamp_dt", y="call_key", hue="intent_category", marker="o")
            plt.title("Bash Calls Over Time by Intent")
            plt.xlabel("Time")
            plt.ylabel("Calls per 30 minutes")
            plt.xticks(rotation=30, ha="right")
            plt.tight_layout()
            plt.savefig(path, dpi=180)
            plt.close()
            plot_paths.append(path)

    return [path for path in plot_paths if path.exists()]


def markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 12) -> str:
    if not rows:
        return ""
    selected = rows[:limit]
    header = "| " + " | ".join(columns) + " |"
    separator = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in selected
    ]
    return "\n".join([header, separator, *body])


def percent(part: int, total: int) -> str:
    if total == 0:
        return "0.0%"
    return f"{(part / total) * 100:.1f}%"


def write_report(
    *,
    calls: list[BashCall],
    stats: dict[str, int],
    tables: dict[str, Path],
    plots: list[Path],
    output_dir: Path,
    runs_dir: Path,
) -> Path:
    report_path = output_dir / "report.md"
    total = len(calls)
    agent_calls = sum(1 for call in calls if call.source_type.startswith("agent"))
    runner_calls = total - agent_calls
    unique_commands = len({call.command_hash for call in calls})
    run_dirs = len({call.run_dir for call in calls if call.run_dir})
    source_files = len({call.source_file for call in calls})

    by_intent = counter_rows(Counter(call.intent_category for call in calls), ["intent_category"])
    by_tool = counter_rows(Counter(call.primary_command or "unknown" for call in calls), ["primary_command"])
    by_harness = counter_rows(Counter(call.harness or "unknown" for call in calls), ["harness"])
    by_source = counter_rows(Counter(call.source_type for call in calls), ["source_type"])
    by_experiment = counter_rows(Counter(call.experiment or "unknown" for call in calls), ["experiment"])
    per_run = run_rows(calls)
    agent_per_run = run_rows([call for call in calls if call.source_type.startswith("agent")])
    repeated = counter_rows(Counter(call.inner_command for call in calls), ["command"])
    for row in repeated:
        row["command"] = str(row["command"])[:120]

    dominant_intent = by_intent[0] if by_intent else {"intent_category": "n/a", "count": 0}
    dominant_tool = by_tool[0] if by_tool else {"primary_command": "n/a", "count": 0}
    inspection_calls = sum(
        1 for call in calls if call.intent_category in {"code_or_file_inspection", "code_search"}
    )
    mutation_calls = sum(
        1
        for call in calls
        if call.intent_category in {"file_or_repo_mutation", "build_or_test", "submission_output"}
    )
    blockchain_calls = sum(1 for call in calls if call.intent_category == "blockchain_rpc_probe")
    compound_calls = sum(1 for call in calls if call.uses_shell_control)
    mutating_flag_calls = sum(1 for call in calls if call.mutates_files)
    nonzero_calls = sum(1 for call in calls if exit_bucket(call.exit_code) == "nonzero")
    unknown_exit_calls = sum(1 for call in calls if exit_bucket(call.exit_code) == "unknown")
    top_experiment = by_experiment[0] if by_experiment else {"experiment": "n/a", "count": 0}
    top_repeated = repeated[0] if repeated else {"command": "n/a", "count": 0}

    plot_lines = []
    for path in plots:
        plot_lines.append(f"![{path.stem}](plots/{path.name})")

    text = f"""# Bash Call Analysis for `runs/`

Generated from `{relpath(runs_dir)}`.

## Executive Summary

- Extracted **{total:,} normalized bash/shell command records** from **{source_files:,} structured log files** across **{run_dirs:,} run directories**.
- Agent-side shell activity accounts for **{agent_calls:,} calls**; runner/orchestration metadata accounts for **{runner_calls:,} calls**.
- The corpus contains **{unique_commands:,} unique normalized command strings** after redaction and de-duplication.
- The dominant intent is **{dominant_intent["intent_category"]}** with **{dominant_intent["count"]:,} calls**.
- The most frequent primary executable is **{dominant_tool["primary_command"]}** with **{dominant_tool["count"]:,} calls**.

## Methodology

The extractor intentionally reads structured execution artifacts rather than scraping markdown prose or README command examples. It covers:

- Mini-SWE/forest `*.traj.json` assistant tool calls where the tool/function name is `bash`.
- Codex CLI `codex-run.jsonl` `command_execution` items.
- OpenCode `opencode-run.jsonl` and state `storage/part/*.json` records where `tool == "bash"`.
- Runner metadata in `phase6-results.json`, `openrouter-v1-results.json`, `_task_results/*.json`, and `modal-runner-command.json`.

Commands are redacted for obvious key/token/password/private-key assignments, normalized by unwrapping `/bin/bash -lc`, split into shell segments, assigned primary executables, and categorized with deterministic rules. The CSV files keep the normalized command text, source path, run metadata, status, inferred intent, tool family, mutation flag, and command shape metrics.

## Coverage

- Candidate files inspected: **{stats.get("candidate_files", 0):,}**
- Files with extracted calls: **{stats.get("files_with_calls", 0):,}**
- Duplicate logical calls removed: **{stats.get("deduped_calls", 0):,}**

## Key Findings

- The runs are strongly read-heavy: inspection plus search accounts for **{inspection_calls:,} calls ({percent(inspection_calls, total)})**, while mutation, build/test, and submission-output commands account for **{mutation_calls:,} calls ({percent(mutation_calls, total)})**.
- Blockchain/RPC probing is the second major behavioral cluster with **{blockchain_calls:,} calls ({percent(blockchain_calls, total)})**, mostly from `cast`-based contract inspection in exploit-oriented runs.
- Compound shell usage appears in **{compound_calls:,} calls ({percent(compound_calls, total)})**, which is where most multi-step audit probes, pipelines, and redirections live.
- The extractor flagged **{mutating_flag_calls:,} commands ({percent(mutating_flag_calls, total)})** as file- or repo-mutating; this includes patch writes, generated reports, build/test side effects, and package/install commands.
- Exit-code coverage is good for agent logs: **{nonzero_calls:,} commands ({percent(nonzero_calls, total)})** ended nonzero and **{unknown_exit_calls:,} commands ({percent(unknown_exit_calls, total)})** had no recorded exit code.
- The largest experiment contributor is **{top_experiment["experiment"]}** with **{top_experiment["count"]:,} calls**, and the most repeated exact command is `{top_repeated["command"]}` with **{top_repeated["count"]:,} repeats**.

## Intent Categories

{markdown_table(by_intent, ["intent_category", "count"])}

## Category Glossary

- `code_or_file_inspection`: file reads and filesystem inventory (`cat`, `sed`, `ls`, `find`, `nl`, etc.).
- `code_search`: targeted text or symbol search (`rg`, `grep`, similar).
- `blockchain_rpc_probe`: chain/RPC inspection and transactions through tools such as `cast`, `anvil`, or `chisel`.
- `file_or_repo_mutation`: commands that write, patch, move, delete, chmod, or redirect output into files.
- `build_or_test`: build, compile, and test invocations (`forge`, `hardhat`, `pytest`, package scripts).
- `submission_output`: final-report or benchmark completion writes and markers.
- `run_orchestration`: benchmark harness commands that launched the run, not commands chosen inside the agent.
- `version_control`: `git`/`gh` commands.
- `script_or_runtime`: direct language/runtime execution not otherwise classified.
- `text_processing`: pure transformation commands such as `awk`, `jq`, `sort`, or `cut`.
- `other`: commands outside the deterministic rules above.

## Per-Run Summary

{markdown_table(per_run, ["run_label", "harness", "mode", "audit_id", "total_calls", "top_intent", "mutation_calls", "blockchain_rpc_calls", "nonzero_exit_calls"])}

## Agent-Only Per-Run Summary

{markdown_table(agent_per_run, ["run_label", "harness", "mode", "audit_id", "total_calls", "top_intent", "mutation_calls", "blockchain_rpc_calls", "nonzero_exit_calls"])}

## Primary Commands

{markdown_table(by_tool, ["primary_command", "count"])}

## Harnesses

{markdown_table(by_harness, ["harness", "count"])}

## Source Types

{markdown_table(by_source, ["source_type", "count"])}

## Experiments

{markdown_table(by_experiment, ["experiment", "count"])}

## Repeated Commands

{markdown_table(repeated, ["command", "count"], limit=10)}

## Plots

{chr(10).join(plot_lines)}

## Output Files

- Full command inventory: `{relpath(tables["commands"])}`
- Per-run summary: `{relpath(tables["run_summary"])}`
- Per-run intent matrix: `{relpath(tables["run_intent_matrix"])}`
- Per-run intent rows: `{relpath(tables["intent_by_run"])}`
- Per-run primary-command rows: `{relpath(tables["primary_command_by_run"])}`
- Agent-only per-run summary: `{relpath(tables["agent_run_summary"])}`
- Agent-only per-run intent matrix: `{relpath(tables["agent_run_intent_matrix"])}`
- Intent summary: `{relpath(tables["by_intent"])}`
- Tool-family summary: `{relpath(tables["by_tool_family"])}`
- Primary-command summary: `{relpath(tables["by_primary_command"])}`
- Harness x intent summary: `{relpath(tables["intent_by_harness"])}`
- Mode x intent summary: `{relpath(tables["intent_by_mode"])}`

## Notes and Limits

- Counts are based on the artifacts currently present under `runs/`; copied RCA artifacts are treated as present run artifacts unless their logical call IDs duplicate within the same run directory.
- The classifier is intentionally deterministic and inspectable. It is not an LLM classifier, so ambiguous compound shell lines are categorized by priority rules.
- Runner metadata records commands submitted by the benchmark harness; agent records represent commands requested inside the evaluated agent environments.
"""
    report_path.write_text(text)
    return report_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Aggregate and categorize bash calls from EVMBench run artifacts.")
    parser.add_argument("--runs-dir", type=Path, default=DEFAULT_RUNS_DIR)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--no-plots", action="store_true", help="Skip seaborn plot generation.")
    args = parser.parse_args()

    runs_dir = args.runs_dir.resolve()
    output_dir = args.output_dir.resolve()
    output_dir.mkdir(parents=True, exist_ok=True)

    calls, stats = extract_calls(runs_dir)
    tables = write_summary_tables(calls, output_dir)
    plots = [] if args.no_plots else make_plots(calls, output_dir)
    report_path = write_report(
        calls=calls,
        stats=stats,
        tables=tables,
        plots=plots,
        output_dir=output_dir,
        runs_dir=runs_dir,
    )
    print(f"Extracted {len(calls)} bash calls")
    print(f"Wrote {report_path}")
    print(f"Wrote {tables['commands']}")
    if plots:
        print(f"Wrote {len(plots)} plots to {output_dir / 'plots'}")


# ---------------------------------------------------------------------------
# Compatibility analyzer
#
# The original research script above writes a richer notebook-oriented report.
# The public artifact contract for the downloaded run corpus is narrower:
# command_invocations.csv, command_segments.csv, per_run_category_summary.csv,
# category_taxonomy.json, source_files.json, and report.md.  The implementation
# below intentionally keeps that schema stable while adding coverage for all
# trace roots used by the bash-command analysis refresh.

REPO_ROOT = PROJECT_ROOT.parent.parent
COMPAT_OUTPUT_DIR = REPO_ROOT / "evmbench_runs_download" / "bash_command_analysis"

COMPAT_INVOCATION_FIELDS = [
    "invocation_id",
    "run_id",
    "source_family",
    "batch",
    "agent",
    "model",
    "mode",
    "benchmark",
    "role",
    "tool",
    "is_bash",
    "primary_category",
    "categories",
    "exit_code",
    "status",
    "workdir",
    "description",
    "inner_command",
    "raw_command",
    "source_path",
    "source_line",
    "source_format",
]

COMPAT_SEGMENT_FIELDS = [
    "segment_id",
    "invocation_id",
    "run_id",
    "source_family",
    "agent",
    "mode",
    "benchmark",
    "role",
    "tool",
    "is_bash",
    "segment_position",
    "primary_category",
    "categories",
    "first_token",
    "segment",
    "source_path",
    "source_line",
]

COMPAT_RUN_SUMMARY_FIELDS = [
    "run_id",
    "source_family",
    "agent",
    "mode",
    "benchmark",
    "role",
    "primary_category",
    "count",
]

COMPAT_TAXONOMY = {
    "completion_marker": "Benchmark finalization marker, usually echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT.",
    "report_submission": "Writes, reads, or validates benchmark submission artifacts under submission/.",
    "exploit_execution": "Runs exploit transactions/scripts, especially forge script --broadcast or cast send.",
    "build_test": "Compiles or tests code, including forge test/build, hardhat tests, npm test, pytest.",
    "onchain_state_query": "Inspects fork/on-chain state with cast call/storage/balance/code/logs or selector utilities.",
    "file_write_edit": "Creates or edits files with redirection, heredocs, tee, apply_patch, cp/mv/rm, mkdir, chmod.",
    "text_search": "Searches source/log text with rg, grep, ack, ag, or similar tools.",
    "file_read_navigation": "Reads or lists files/directories with pwd, ls, cat, sed, find, head, tail, nl, wc, stat.",
    "git_vcs": "Uses git or GitHub CLI.",
    "dependency_install": "Installs or fetches project dependencies/packages.",
    "runtime_script": "Runs ad-hoc interpreters or scripts such as python, node, jq, awk, bash/sh.",
    "environment_process": "Inspects or changes runtime/container/process environment.",
    "network_external": "Attempts external network/service access such as curl, wget, gh issue view.",
    "structured_subagent": "OpenCode task/subagent tool invocation rather than a shell command.",
    "shell_output_logging": "Prints headings, diagnostics, or progress text with echo/printf.",
    "shell_control_flow": "Shell glue such as comments, conditionals, loops, function wrappers, true/false, and pure assignments.",
    "other": "No specific category matched.",
}

COMPAT_CATEGORY_PRIORITY = [
    "completion_marker",
    "report_submission",
    "exploit_execution",
    "build_test",
    "onchain_state_query",
    "file_write_edit",
    "text_search",
    "file_read_navigation",
    "git_vcs",
    "dependency_install",
    "runtime_script",
    "environment_process",
    "network_external",
    "structured_subagent",
    "shell_output_logging",
    "shell_control_flow",
    "other",
]

COMPAT_SOURCE_PRIORITIES = {
    "evmbench_runs_download": 0,
    "evmbench_native_runs": 1,
    "exploit_results": 2,
    "exploit_results_v3": 3,
    "exploit_results_live_v3": 4,
    "project_evmbench_runs": 5,
}

COMPAT_DEFAULT_SOURCES = [
    ("evmbench_runs_download", REPO_ROOT / "evmbench_runs_download"),
    ("project_evmbench_runs", PROJECT_ROOT / "runs"),
    ("exploit_results", REPO_ROOT / "exploit_results"),
    ("exploit_results_v3", REPO_ROOT / "exploit_results_v3"),
    ("exploit_results_live_v3", REPO_ROOT / "exploit_results_live_v3"),
    ("evmbench_native_runs", REPO_ROOT / "evmBench-frontier-evals" / "project" / "evmbench" / "runs"),
]

COMPAT_RUN_GROUP_RE = re.compile(r"\d{4}-\d{2}-\d{2}T[^/]+_run-group_[^/]+")
COMPAT_RUN_KEY_RE = re.compile(
    r"(?P<agent>codex|opencode|mini-swe-agent|modal-forest|modal|yudai-minisweagent)"
    r"--(?P<model>.+?)--(?P<mode>detect|patch|exploit)--(?P<benchmark>\d{4}-\d{2}-[^/]+)"
)
COMPAT_AUDIT_INSTANCE_RE = re.compile(r"(?P<benchmark>\d{4}-\d{2}-[A-Za-z0-9-]+)_[0-9a-fA-F-]{8,}")
COMPAT_EXPLOIT_STEM_RE = re.compile(r"benchmark_\d+_\d+_\d+_1_(?P<benchmark>.+)")
COMPAT_BASH_FENCE_RE = re.compile(
    r"```(?P<lang>bash|sh|mswea_bash_command)\s*\n(?P<body>.*?)(?:\n)?```",
    re.IGNORECASE | re.DOTALL,
)


@dataclass(frozen=True)
class CompatSourceSpec:
    family: str
    root: Path


@dataclass(frozen=True)
class CompatSourceFile:
    source_family: str
    root: Path
    path: Path
    rel_path: str
    rel_to_source: str
    sha256: str


@dataclass(frozen=True)
class CompatMetadata:
    run_id: str
    source_family: str
    batch: str
    agent: str
    model: str
    mode: str
    benchmark: str
    role: str
    run_group: str
    run_instance: str


@dataclass
class CompatInvocation:
    logical_key: str
    invocation_id: int
    run_id: str
    source_family: str
    batch: str
    agent: str
    model: str
    mode: str
    benchmark: str
    role: str
    tool: str
    is_bash: bool
    primary_category: str
    categories: str
    exit_code: str
    status: str
    workdir: str
    description: str
    inner_command: str
    raw_command: str
    source_path: str
    source_line: str
    source_format: str


def compat_relpath(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(REPO_ROOT))
    except ValueError:
        return str(path)


def compat_parse_source(value: str) -> CompatSourceSpec:
    if "=" in value:
        family, raw_path = value.split("=", 1)
    else:
        raw_path = value
        family = Path(value).name.replace("-", "_")
    if not family:
        raise ValueError(f"invalid source label in {value!r}")
    return CompatSourceSpec(family=family, root=Path(raw_path))


def compat_default_sources() -> list[CompatSourceSpec]:
    return [CompatSourceSpec(family=family, root=root) for family, root in COMPAT_DEFAULT_SOURCES]


def compat_file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def compat_is_candidate(path: Path) -> bool:
    if not path.is_file():
        return False
    if path.name in {"codex-run.jsonl", "opencode-run.jsonl"}:
        return True
    if path.name.endswith(".traj.json"):
        return True
    if path.suffix == ".json":
        return True
    return False


def compat_discover_source_files(
    sources: list[CompatSourceSpec],
) -> tuple[list[CompatSourceFile], dict[str, Any]]:
    stats: dict[str, Any] = {
        "candidate_files": 0,
        "unique_files": 0,
        "skipped_duplicate_files": 0,
        "missing_sources": [],
        "candidate_by_source_family": Counter(),
        "unique_by_source_family": Counter(),
        "duplicate_by_source_family": Counter(),
    }
    candidates_by_hash: dict[str, list[CompatSourceFile]] = {}
    for source in sources:
        root = source.root.resolve()
        if not root.exists():
            stats["missing_sources"].append(f"{source.family}={root}")
            continue
        for path in sorted(root.rglob("*")):
            if not compat_is_candidate(path):
                continue
            try:
                rel_to_source = str(path.relative_to(root))
            except ValueError:
                rel_to_source = str(path)
            record = CompatSourceFile(
                source_family=source.family,
                root=root,
                path=path,
                rel_path=compat_relpath(path),
                rel_to_source=rel_to_source,
                sha256=compat_file_sha256(path),
            )
            stats["candidate_files"] += 1
            stats["candidate_by_source_family"][source.family] += 1
            candidates_by_hash.setdefault(record.sha256, []).append(record)

    selected: list[CompatSourceFile] = []
    for records in candidates_by_hash.values():
        records = sorted(
            records,
            key=lambda item: (
                COMPAT_SOURCE_PRIORITIES.get(item.source_family, 100),
                len(item.rel_path),
                item.rel_path,
            ),
        )
        selected.append(records[0])
        stats["unique_by_source_family"][records[0].source_family] += 1
        for duplicate in records[1:]:
            stats["skipped_duplicate_files"] += 1
            stats["duplicate_by_source_family"][duplicate.source_family] += 1

    selected.sort(key=lambda item: item.rel_path)
    stats["unique_files"] = len(selected)
    return selected, stats


def compat_path_parts(source_file: CompatSourceFile) -> tuple[str, ...]:
    return Path(source_file.rel_to_source).parts


def compat_infer_batch(source_file: CompatSourceFile) -> str:
    if source_file.source_family.startswith("exploit_results"):
        return ""
    parts = compat_path_parts(source_file)
    if "evmbench_runs" in parts:
        idx = parts.index("evmbench_runs")
        return "/".join(parts[:idx])
    if len(parts) >= 2 and parts[0] in {"openrouter-v1", "rca", "phase6", "vllm-smoke"}:
        return "/".join(parts[:2])
    return ""


def compat_infer_run_group_and_instance(source_file: CompatSourceFile) -> tuple[str, str]:
    parts = compat_path_parts(source_file)
    for index, part in enumerate(parts):
        if COMPAT_RUN_GROUP_RE.fullmatch(part):
            run_instance = parts[index + 1] if index + 1 < len(parts) else part
            return part, run_instance
    stem = source_file.path.name
    if stem.endswith(".traj.json"):
        stem = stem[: -len(".traj.json")]
    else:
        stem = source_file.path.stem
    if source_file.source_family.startswith("exploit_results"):
        return "", stem
    return "", str(Path(source_file.rel_to_source).parent)


def compat_infer_role(source_file: CompatSourceFile) -> str:
    parts = compat_path_parts(source_file)
    if "forest" not in parts:
        return ""
    idx = parts.index("forest")
    if idx + 1 >= len(parts):
        return ""
    first = parts[idx + 1]
    if first.endswith(".traj.json"):
        return first[: -len(".traj.json")]
    if idx + 2 < len(parts):
        second = parts[idx + 2]
        if second.endswith(".traj.json"):
            second = second[: -len(".traj.json")]
        return f"{first}/{second}"
    return first


def compat_payload_model(payload: dict[str, Any] | None) -> str:
    if not isinstance(payload, dict):
        return ""
    info = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    for key in ("model", "model_name", "model_id"):
        value = info.get(key)
        if isinstance(value, str):
            return value
    config = info.get("config") if isinstance(info.get("config"), dict) else {}
    model_config = config.get("model") if isinstance(config.get("model"), dict) else {}
    value = model_config.get("model_name")
    return value if isinstance(value, str) else ""


def compat_infer_metadata(
    source_file: CompatSourceFile,
    payload: dict[str, Any] | None = None,
) -> CompatMetadata:
    rel_text = source_file.rel_path
    parts = compat_path_parts(source_file)
    batch = compat_infer_batch(source_file)
    run_group, run_instance = compat_infer_run_group_and_instance(source_file)

    agent = ""
    model = ""
    mode = ""
    benchmark = ""

    run_key_match = COMPAT_RUN_KEY_RE.search(rel_text)
    if run_key_match:
        agent = run_key_match.group("agent")
        model = run_key_match.group("model")
        mode = run_key_match.group("mode")
        benchmark = run_key_match.group("benchmark")

    if not agent:
        if "/logs/codex/" in rel_text:
            agent = "codex"
        elif "/logs/opencode/" in rel_text:
            agent = "opencode"
        elif "/logs/forest/" in rel_text or "/modal/logs/forest/" in rel_text:
            agent = "mini-swe-agent-forest"
        elif source_file.path.name == "yudai-minisweagent.traj.json":
            agent = "yudai-minisweagent"
        elif source_file.source_family.startswith("exploit_results"):
            agent = "mini-swe-agent"

    if not mode:
        mode_match = MODE_RE.search(rel_text)
        if mode_match:
            mode = mode_match.group("mode")
    if not mode and source_file.source_family.startswith("exploit_results"):
        mode = "exploit"

    if not benchmark:
        for part in reversed(parts):
            audit_match = COMPAT_AUDIT_INSTANCE_RE.match(part)
            if audit_match:
                benchmark = audit_match.group("benchmark")
                break
    if not benchmark and source_file.source_family.startswith("exploit_results"):
        stem = source_file.path.name
        if stem.endswith(".traj.json"):
            stem = stem[: -len(".traj.json")]
        exploit_match = COMPAT_EXPLOIT_STEM_RE.fullmatch(stem)
        benchmark = exploit_match.group("benchmark") if exploit_match else stem

    if not model and agent in {"codex", "opencode"}:
        model = compat_payload_model(payload)

    role = compat_infer_role(source_file)
    run_id_parts = [source_file.source_family]
    if batch:
        run_id_parts.append(batch)
    run_id_parts.extend(
        [
            agent,
            model,
            mode,
            benchmark,
        ]
    )
    if run_group:
        run_id_parts.append(run_group)
    if run_instance:
        run_id_parts.append(run_instance)
    run_id = "|".join(part for part in run_id_parts if part != "")
    return CompatMetadata(
        run_id=run_id,
        source_family=source_file.source_family,
        batch=batch,
        agent=agent,
        model=model,
        mode=mode,
        benchmark=benchmark,
        role=role,
        run_group=run_group,
        run_instance=run_instance,
    )


def compat_observation_info(message: dict[str, Any] | None) -> dict[str, str]:
    if not isinstance(message, dict):
        return {"exit_code": "", "status": ""}
    content = message.get("content")
    if not isinstance(content, str):
        content = ""
    returncode_match = re.search(r"<returncode>(.*?)</returncode>", content, flags=re.DOTALL)
    timed_out = bool(re.search(r"\b(time(?:d)? out|timeout)\b", content, flags=re.IGNORECASE))
    if returncode_match:
        return {"exit_code": returncode_match.group(1).strip(), "status": "completed"}
    if timed_out:
        return {"exit_code": "", "status": "timeout"}
    extra = message.get("extra") if isinstance(message.get("extra"), dict) else {}
    if "returncode" in extra:
        return {"exit_code": str(extra.get("returncode", "")), "status": "completed"}
    return {"exit_code": "", "status": ""}


def compat_next_observation(messages: list[Any], index: int) -> dict[str, str]:
    for next_message in messages[index + 1 : index + 4]:
        if not isinstance(next_message, dict):
            continue
        if next_message.get("role") in {"user", "tool"}:
            return compat_observation_info(next_message)
    return {"exit_code": "", "status": ""}


def compat_strip_heredocs_for_splitting(command: str) -> str:
    lines = command.splitlines()
    if not lines:
        return command
    result: list[str] = []
    delimiter: str | None = None
    marker_re = re.compile(r"<<-?\s*['\"]?([A-Za-z0-9_./-]+)['\"]?")
    for line in lines:
        if delimiter is not None:
            if line.strip() == delimiter:
                delimiter = None
            continue
        result.append(line)
        match = marker_re.search(line)
        if match:
            delimiter = match.group(1)
    return "\n".join(result)


def compat_shell_tokens(command: str) -> list[str]:
    stripped = compat_strip_heredocs_for_splitting(command).replace("\n", " ; ")
    try:
        lexer = shlex.shlex(stripped, posix=True, punctuation_chars="|&;()<>")
        lexer.whitespace_split = True
        return list(lexer)
    except ValueError:
        return stripped.split()


def compat_split_shell_segments(command: str) -> list[str]:
    tokens = compat_shell_tokens(command)
    segments: list[list[str]] = []
    current: list[str] = []
    separators = {";", "&&", "||", "|", "(", ")"}
    for token in tokens:
        if token in separators:
            if current:
                segments.append(current)
                current = []
            continue
        current.append(token)
    if current:
        segments.append(current)
    rendered = [" ".join(segment).strip() for segment in segments]
    return [segment for segment in rendered if segment]


def compat_first_token(segment: str) -> str:
    try:
        tokens = shlex.split(segment)
    except ValueError:
        tokens = segment.split()
    index = 0
    while index < len(tokens) and is_env_assignment(tokens[index]):
        index += 1
    while index < len(tokens) and tokens[index] in {"sudo", "env", "command", "time", "timeout", "nice"}:
        index += 1
        while index < len(tokens) and (tokens[index].startswith("-") or is_env_assignment(tokens[index])):
            index += 1
    if index >= len(tokens):
        return ""
    return Path(tokens[index]).name


def compat_category_for_segment(segment: str, tool: str = "bash", is_bash: bool = True) -> str:
    lowered = segment.lower().strip()
    first = compat_first_token(segment)
    if not is_bash:
        if tool in {"read", "glob", "list"}:
            return "file_read_navigation"
        if tool in {"grep", "search"}:
            return "text_search"
        if tool in {"write", "edit", "apply_patch"}:
            return "file_write_edit"
        if tool == "task":
            return "structured_subagent"
        return "structured_subagent"
    if not lowered:
        return "shell_control_flow"
    if lowered.startswith("#"):
        return "shell_control_flow"
    if "complete_task_and_submit_final_output" in lowered:
        return "completion_marker"
    if re.search(r"\bsubmission/(audit|report)\.md\b|/submission/(audit|report)\.md\b", lowered):
        return "report_submission"
    if first == "cast":
        if re.search(r"\bcast\s+send\b", lowered):
            return "exploit_execution"
        return "onchain_state_query"
    if first in {"anvil", "chisel"}:
        return "onchain_state_query"
    if first == "forge" and re.search(r"\bscript\b", lowered) and (
        "--broadcast" in lowered or "script/harness" in lowered
    ):
        return "exploit_execution"
    if re.search(r"\b(forge|hardhat|pytest|cargo|go|make|cmake|ninja)\s+(test|build|compile|check|script)\b", lowered):
        return "build_test"
    if re.search(r"\b(npm|pnpm|yarn|bun)\s+(test|run|build|compile)\b", lowered):
        return "build_test"
    if (
        "apply_patch" in lowered
        or re.search(r"(^|\s)(>|>>|2>|2>>|&>)", segment)
        or re.search(r"<<-?\s*['\"]?[A-Za-z0-9_./-]+", segment)
        or first in WRITE_TOOLS
        or re.search(r"\b(sed|perl)\b.*\s-i(\s|$)", lowered)
        or re.search(r"\bgit\s+(add|commit|checkout|switch|merge|pull|push|reset|restore|rm|mv|clean)\b", lowered)
    ):
        return "file_write_edit"
    if first in SEARCH_TOOLS:
        return "text_search"
    if first in READ_TOOLS or first in LIST_TOOLS:
        return "file_read_navigation"
    if first in GIT_TOOLS:
        return "git_vcs"
    if re.search(r"\b(npm|npx|pnpm|yarn|pip|pip3|uv|poetry|bun)\s+(install|add|remove|sync|i)\b", lowered):
        return "dependency_install"
    if first in {"curl", "wget", "ssh", "scp", "nc", "telnet"}:
        return "network_external"
    if first in {"docker", "ps", "kill", "pkill", "pgrep", "env", "printenv", "export", "set", "unset", "which", "whereis", "uname", "date", "sleep", "jobs", "tmux"}:
        return "environment_process"
    if first in {"echo", "printf"}:
        return "shell_output_logging"
    if first in {"cd", "true", "false", "test", "[", "if", "then", "else", "fi", "for", "while", "do", "done", "case", "esac", "function"}:
        return "shell_control_flow"
    if first in LANGUAGE_TOOLS or first in TEXT_PROCESS_TOOLS:
        return "runtime_script"
    return "other"


def compat_categories_for_command(command: str, tool: str, is_bash: bool) -> tuple[str, str]:
    if is_bash:
        segments = compat_split_shell_segments(command)
        if not segments and command.strip():
            segments = [command.strip()]
        categories = [compat_category_for_segment(segment, tool, True) for segment in segments]
    else:
        categories = [compat_category_for_segment(command, tool, False)]
    if not categories:
        categories = ["other"]
    seen: list[str] = []
    for category in categories:
        if category not in seen:
            seen.append(category)
    primary = min(seen, key=lambda category: COMPAT_CATEGORY_PRIORITY.index(category))
    return primary, "|".join(seen)


def compat_normalize_invocation(
    *,
    source_file: CompatSourceFile,
    source_format: str,
    source_line: int | str,
    command: Any,
    tool: str = "bash",
    is_bash: bool = True,
    call_id: str = "",
    status: Any = "",
    exit_code: Any = "",
    workdir: Any = "",
    description: str = "",
    payload: dict[str, Any] | None = None,
    key_hint: str = "",
) -> CompatInvocation:
    metadata = compat_infer_metadata(source_file, payload)
    raw_command = redact_command(shell_join(command))
    inner_command = redact_command(unwrap_bash_lc(raw_command)) if is_bash else raw_command
    primary_category, categories = compat_categories_for_command(inner_command, tool, is_bash)
    logical_basis = "|".join(
        [
            metadata.run_id,
            source_format,
            str(call_id or source_line),
            key_hint,
            tool,
            inner_command,
        ]
    )
    logical_key = hashlib.sha256(logical_basis.encode("utf-8", "replace")).hexdigest()
    return CompatInvocation(
        logical_key=logical_key,
        invocation_id=0,
        run_id=metadata.run_id,
        source_family=metadata.source_family,
        batch=metadata.batch,
        agent=metadata.agent,
        model=metadata.model,
        mode=metadata.mode,
        benchmark=metadata.benchmark,
        role=metadata.role,
        tool=tool,
        is_bash=is_bash,
        primary_category=primary_category,
        categories=categories,
        exit_code=str(exit_code if exit_code is not None else ""),
        status=str(status or ""),
        workdir=str(workdir or ""),
        description=description,
        inner_command=inner_command,
        raw_command=raw_command,
        source_path=source_file.rel_path,
        source_line=str(source_line),
        source_format=source_format,
    )


def compat_extract_traj(source_file: CompatSourceFile) -> list[CompatInvocation]:
    payload = read_json(source_file.path)
    if not isinstance(payload, dict) or not isinstance(payload.get("messages"), list):
        return []
    messages = payload["messages"]
    payload_metadata = payload.get("info") if isinstance(payload.get("info"), dict) else payload
    records: list[CompatInvocation] = []

    tool_results: dict[str, dict[str, str]] = {}
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "tool":
            continue
        tool_call_id = str(message.get("tool_call_id") or "")
        if tool_call_id:
            tool_results[tool_call_id] = compat_observation_info(message)

    for index, message in enumerate(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        observation = compat_next_observation(messages, index)
        actions = message.get("actions")
        if isinstance(actions, list) and actions:
            for action_index, action in enumerate(actions):
                if not isinstance(action, dict) or action.get("tool") != "bash":
                    continue
                command = action.get("command", action.get("action"))
                if command is None:
                    continue
                records.append(
                    compat_normalize_invocation(
                        source_file=source_file,
                        source_format="traj-action",
                        source_line=index,
                        command=command,
                        call_id=f"message-{index}-action-{action_index}",
                        status=observation.get("status", ""),
                        exit_code=observation.get("exit_code", ""),
                        payload=payload_metadata,
                    )
                )
            continue

        tool_calls = message.get("tool_calls")
        if isinstance(tool_calls, list) and tool_calls:
            for call_index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                function = tool_call.get("function")
                if not isinstance(function, dict) or function.get("name") != "bash":
                    continue
                arguments = safe_json_loads(function.get("arguments", "{}"))
                if not isinstance(arguments, dict) or "command" not in arguments:
                    continue
                call_id = str(tool_call.get("id") or f"message-{index}-tool-{call_index}")
                result = tool_results.get(call_id, observation)
                records.append(
                    compat_normalize_invocation(
                        source_file=source_file,
                        source_format="traj-tool-call",
                        source_line=index,
                        command=arguments["command"],
                        call_id=call_id,
                        status=result.get("status", ""),
                        exit_code=result.get("exit_code", ""),
                        payload=payload_metadata,
                    )
                )
            continue

        content = message.get("content")
        if not isinstance(content, str):
            continue
        for block_index, match in enumerate(COMPAT_BASH_FENCE_RE.finditer(content)):
            command = match.group("body").strip("\n")
            if not command.strip():
                continue
            records.append(
                compat_normalize_invocation(
                    source_file=source_file,
                    source_format="traj-bash-code-block",
                    source_line=index,
                    command=command,
                    call_id=f"message-{index}-block-{block_index}",
                    status=observation.get("status", ""),
                    exit_code=observation.get("exit_code", ""),
                    payload=payload_metadata,
                )
            )
    return records


def compat_extract_codex_jsonl(source_file: CompatSourceFile) -> list[CompatInvocation]:
    if source_file.path.name != "codex-run.jsonl":
        return []
    by_id: dict[str, tuple[int, CompatInvocation]] = {}
    for line_no, payload in iter_jsonl(source_file.path):
        item = payload.get("item")
        if not isinstance(item, dict) or item.get("type") != "command_execution":
            continue
        command = item.get("command")
        if not command:
            continue
        item_id = str(item.get("id") or f"line-{line_no}")
        record = compat_normalize_invocation(
            source_file=source_file,
            source_format="codex-run-jsonl",
            source_line=line_no,
            command=command,
            call_id=item_id,
            status=item.get("status", ""),
            exit_code=item.get("exit_code", ""),
            payload=payload,
            key_hint=item_id,
        )
        old = by_id.get(item_id)
        if old is None or (old[1].status != "completed" and record.status == "completed"):
            by_id[item_id] = (line_no, record)
    return [record for _, record in sorted(by_id.values(), key=lambda item: item[0])]


def compat_opencode_structured_command(tool: str, state_input: dict[str, Any]) -> tuple[str, str]:
    if tool == "read":
        value = state_input.get("filePath") or state_input.get("path") or ""
        return f"read {value}".strip(), ""
    if tool == "glob":
        path = state_input.get("path") or ""
        pattern = state_input.get("pattern") or ""
        return f"glob {path} {pattern}".strip(), ""
    if tool in {"grep", "search"}:
        pattern = state_input.get("pattern") or state_input.get("query") or ""
        path = state_input.get("path") or state_input.get("include") or ""
        return f"{tool} {pattern} {path}".strip(), ""
    if tool == "task":
        description = str(state_input.get("description") or "")
        return f"task {description}".strip(), description
    if tool == "apply_patch":
        return "apply_patch", ""
    return tool, ""


def compat_extract_opencode_payload(
    source_file: CompatSourceFile,
    payload: dict[str, Any],
    source_format: str,
    source_line: int | str,
) -> CompatInvocation | None:
    part = payload.get("part") if "part" in payload else payload
    if not isinstance(part, dict) or part.get("type") != "tool":
        return None
    tool = str(part.get("tool") or "")
    if not tool:
        return None
    state = part.get("state") if isinstance(part.get("state"), dict) else {}
    state_input = state.get("input") if isinstance(state.get("input"), dict) else {}
    metadata = state.get("metadata") if isinstance(state.get("metadata"), dict) else {}
    call_id = str(part.get("callID") or part.get("id") or f"line-{source_line}")
    if tool == "bash":
        command = state_input.get("command")
        if not command:
            return None
        return compat_normalize_invocation(
            source_file=source_file,
            source_format=source_format,
            source_line=source_line,
            command=command,
            call_id=call_id,
            status=state.get("status", ""),
            exit_code=metadata.get("exit", ""),
            workdir=state_input.get("workdir", ""),
            payload=payload,
            key_hint=call_id,
        )
    command, description = compat_opencode_structured_command(tool, state_input)
    return compat_normalize_invocation(
        source_file=source_file,
        source_format=source_format,
        source_line=source_line,
        command=command,
        tool=tool,
        is_bash=False,
        call_id=call_id,
        status=state.get("status", ""),
        exit_code=metadata.get("exit", ""),
        description=description,
        payload=payload,
        key_hint=call_id,
    )


def compat_extract_opencode_jsonl(source_file: CompatSourceFile) -> list[CompatInvocation]:
    if source_file.path.name != "opencode-run.jsonl":
        return []
    records: list[CompatInvocation] = []
    for line_no, payload in iter_jsonl(source_file.path):
        record = compat_extract_opencode_payload(source_file, payload, "opencode-run-jsonl", line_no)
        if record is not None:
            records.append(record)
    return records


def compat_extract_opencode_part_json(source_file: CompatSourceFile) -> list[CompatInvocation]:
    if source_file.path.suffix != ".json" or "/storage/part/" not in source_file.rel_path:
        return []
    payload = read_json(source_file.path)
    if not isinstance(payload, dict):
        return []
    record = compat_extract_opencode_payload(source_file, payload, "opencode-state-part-json", 1)
    return [record] if record is not None else []


def compat_extract_runner_commands(source_file: CompatSourceFile) -> list[CompatInvocation]:
    if source_file.path.suffix != ".json" or source_file.path.name.endswith(".traj.json"):
        return []
    if "/storage/part/" in source_file.rel_path:
        return []
    if not (
        source_file.path.name in {"phase6-results.json", "openrouter-v1-results.json", "modal-runner-command.json"}
        or source_file.path.name.endswith("-results.json")
        or "_task_results" in source_file.path.parts
    ):
        return []
    payload = read_json(source_file.path)
    if not isinstance(payload, dict):
        return []
    records: list[CompatInvocation] = []
    seen: set[str] = set()

    def maybe_record(node: dict[str, Any], trail: str) -> None:
        command = node.get("command")
        if isinstance(command, list):
            if not command:
                return
        elif isinstance(command, str):
            if not command.strip():
                return
        else:
            return
        marker = "|".join(
            [
                shell_join(command),
                str(node.get("run_key") or node.get("agent_id") or ""),
                str(node.get("started_at") or ""),
                str(node.get("returncode") if node.get("returncode") is not None else ""),
            ]
        )
        if marker in seen:
            return
        seen.add(marker)
        records.append(
            compat_normalize_invocation(
                source_file=source_file,
                source_format="runner-metadata-json",
                source_line=trail or 1,
                command=command,
                call_id=str(node.get("run_key") or node.get("agent_id") or trail),
                status="completed" if node.get("returncode") == 0 else str(node.get("status") or ""),
                exit_code=node.get("returncode", ""),
                payload=node,
                key_hint=trail,
            )
        )

    def visit(node: Any, trail: str = "") -> None:
        if isinstance(node, dict):
            if "command" in node and ("returncode" in node or "started_at" in node or source_file.path.name == "modal-runner-command.json"):
                maybe_record(node, trail)
            for key, value in node.items():
                if key in {"env", "files", "aggregate"}:
                    continue
                visit(value, f"{trail}.{key}" if trail else key)
        elif isinstance(node, list):
            for index, item in enumerate(node):
                visit(item, f"{trail}[{index}]")

    visit(payload)
    return records


def compat_extract_source_file(source_file: CompatSourceFile) -> list[CompatInvocation]:
    records: list[CompatInvocation] = []
    records.extend(compat_extract_traj(source_file))
    records.extend(compat_extract_codex_jsonl(source_file))
    records.extend(compat_extract_opencode_jsonl(source_file))
    records.extend(compat_extract_opencode_part_json(source_file))
    records.extend(compat_extract_runner_commands(source_file))
    return records


def compat_extract_calls(
    sources: list[CompatSourceSpec],
) -> tuple[list[CompatInvocation], dict[str, Any], list[str]]:
    source_files, stats = compat_discover_source_files(sources)
    stats["files_with_extracted_invocations"] = 0
    stats["extracted_by_source_family"] = Counter()
    stats["source_format"] = Counter()
    stats["deduped_logical_invocations"] = 0
    calls_by_key: dict[str, CompatInvocation] = {}
    source_paths_with_kept_calls: set[str] = set()
    for source_file in source_files:
        extracted = compat_extract_source_file(source_file)
        if extracted:
            stats["files_with_extracted_invocations"] += 1
        for record in extracted:
            stats["extracted_by_source_family"][source_file.source_family] += 1
            stats["source_format"][record.source_format] += 1
            existing = calls_by_key.get(record.logical_key)
            if existing is not None:
                stats["deduped_logical_invocations"] += 1
                if (
                    existing.source_format == "opencode-state-part-json"
                    and record.source_format == "opencode-run-jsonl"
                ):
                    calls_by_key[record.logical_key] = record
                continue
            calls_by_key[record.logical_key] = record
    calls = sorted(
        calls_by_key.values(),
        key=lambda item: (
            item.source_family,
            item.source_path,
            int(item.source_line) if str(item.source_line).isdigit() else 10**9,
            item.source_line,
            item.tool,
            item.inner_command,
        ),
    )
    for invocation_id, call in enumerate(calls, 1):
        call.invocation_id = invocation_id
        source_paths_with_kept_calls.add(call.source_path)
    source_files_json = sorted(source_paths_with_kept_calls)
    return calls, stats, source_files_json


def compat_invocation_row(call: CompatInvocation) -> dict[str, Any]:
    return {
        "invocation_id": call.invocation_id,
        "run_id": call.run_id,
        "source_family": call.source_family,
        "batch": call.batch,
        "agent": call.agent,
        "model": call.model,
        "mode": call.mode,
        "benchmark": call.benchmark,
        "role": call.role,
        "tool": call.tool,
        "is_bash": str(call.is_bash),
        "primary_category": call.primary_category,
        "categories": call.categories,
        "exit_code": call.exit_code,
        "status": call.status,
        "workdir": call.workdir,
        "description": call.description,
        "inner_command": call.inner_command,
        "raw_command": call.raw_command,
        "source_path": call.source_path,
        "source_line": call.source_line,
        "source_format": call.source_format,
    }


def compat_segment_rows(calls: list[CompatInvocation]) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    segment_id = 1
    for call in calls:
        if call.is_bash:
            segments = compat_split_shell_segments(call.inner_command)
            if not segments and call.inner_command.strip():
                segments = [call.inner_command.strip()]
        else:
            segments = [call.inner_command]
        for position, segment in enumerate(segments, 1):
            category = compat_category_for_segment(segment, call.tool, call.is_bash)
            rows.append(
                {
                    "segment_id": segment_id,
                    "invocation_id": call.invocation_id,
                    "run_id": call.run_id,
                    "source_family": call.source_family,
                    "agent": call.agent,
                    "mode": call.mode,
                    "benchmark": call.benchmark,
                    "role": call.role,
                    "tool": call.tool,
                    "is_bash": str(call.is_bash),
                    "segment_position": position,
                    "primary_category": category,
                    "categories": category,
                    "first_token": compat_first_token(segment) if call.is_bash else call.tool,
                    "segment": segment,
                    "source_path": call.source_path,
                    "source_line": call.source_line,
                }
            )
            segment_id += 1
    return rows


def compat_per_run_rows(calls: list[CompatInvocation]) -> list[dict[str, Any]]:
    counter = Counter(
        (
            call.run_id,
            call.source_family,
            call.agent,
            call.mode,
            call.benchmark,
            call.role,
            call.primary_category,
        )
        for call in calls
    )
    rows: list[dict[str, Any]] = []
    for key, count in sorted(counter.items(), key=lambda item: (item[0][0], item[0][6])):
        rows.append(
            {
                "run_id": key[0],
                "source_family": key[1],
                "agent": key[2],
                "mode": key[3],
                "benchmark": key[4],
                "role": key[5],
                "primary_category": key[6],
                "count": count,
            }
        )
    return rows


def compat_write_csv(path: Path, rows: list[dict[str, Any]], fieldnames: list[str]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(rows)


def compat_markdown_table(rows: list[dict[str, Any]], columns: list[str], limit: int = 20) -> str:
    if not rows:
        return ""
    selected = rows[:limit]
    header = "| " + " | ".join(columns) + " |"
    sep = "| " + " | ".join("---" for _ in columns) + " |"
    body = [
        "| " + " | ".join(str(row.get(column, "")).replace("|", "\\|") for column in columns) + " |"
        for row in selected
    ]
    return "\n".join([header, sep, *body])


def compat_counter_rows(counter: Counter[Any], column: str) -> list[dict[str, Any]]:
    return [{column: key, "count": value} for key, value in counter.most_common()]


def compat_generation_counter_table(counter: Counter[str]) -> str:
    rows = [{"source_family": key, "count": value} for key, value in counter.most_common()]
    return compat_markdown_table(rows, ["source_family", "count"], limit=50)


def compat_write_report(
    output_dir: Path,
    calls: list[CompatInvocation],
    segment_rows: list[dict[str, Any]],
    source_files_json: list[str],
    stats: dict[str, Any],
) -> Path:
    bash_calls = sum(1 for call in calls if call.is_bash)
    nonbash = len(calls) - bash_calls
    run_count = len({call.run_id for call in calls})
    by_segment_category = compat_counter_rows(Counter(row["primary_category"] for row in segment_rows), "primary_category")
    by_invocation_category = compat_counter_rows(Counter(call.primary_category for call in calls), "primary_category")
    by_agent_mode_category = [
        {"agent": agent, "mode": mode, "primary_category": category, "count": count}
        for (agent, mode, category), count in Counter(
            (call.agent, call.mode, call.primary_category) for call in calls
        ).most_common(40)
    ]
    by_source_family = compat_counter_rows(Counter(call.source_family for call in calls), "source_family")
    by_source_format = compat_counter_rows(Counter(call.source_format for call in calls), "source_format")
    top_run_category = [
        {
            "source_family": source_family,
            "agent": agent,
            "mode": mode,
            "benchmark": benchmark,
            "primary_category": category,
            "count": count,
        }
        for (source_family, agent, mode, benchmark, category), count in Counter(
            (call.source_family, call.agent, call.mode, call.benchmark, call.primary_category)
            for call in calls
        ).most_common(40)
    ]
    nonzero_examples = [
        {
            "agent": call.agent,
            "mode": call.mode,
            "benchmark": call.benchmark,
            "primary_category": call.primary_category,
            "exit_code": call.exit_code,
            "inner_command": call.inner_command[:120],
            "source_path": call.source_path,
        }
        for call in calls
        if call.exit_code not in {"", "0"}
    ][:20]

    report = f"""# Agent Bash Command Analysis
## Scope
- Trace files scanned: {len(source_files_json)}
- Total extracted invocations/tools: {len(calls)}
- Actual bash/shell invocations: {bash_calls}
- Structured OpenCode non-bash tool invocations: {nonbash}
- Command segments after splitting compound commands: {len(segment_rows)}
- Distinct runs with at least one extracted invocation: {run_count}

Generated files:
- `command_invocations.csv`: one row per bash or structured tool invocation.
- `command_segments.csv`: one row per split shell segment/pseudo-tool.
- `per_run_category_summary.csv`: complete per-run category counts.
- `category_taxonomy.json`: category definitions.
- `source_files.json`: unique source files that contributed counted invocations.

## Generation Manifest
- Candidate files inspected before hash de-duplication: {stats.get("candidate_files", 0)}
- Unique candidate files after hash de-duplication: {stats.get("unique_files", 0)}
- Skipped duplicate files by content hash: {stats.get("skipped_duplicate_files", 0)}
- Files with extracted invocations before logical call de-duplication: {stats.get("files_with_extracted_invocations", 0)}
- Logical duplicate invocations removed: {stats.get("deduped_logical_invocations", 0)}
- Counted source files with kept invocations: {len(source_files_json)}

### Candidate Files By Source Family
{compat_generation_counter_table(stats.get("candidate_by_source_family", Counter()))}

### Unique Files By Source Family
{compat_generation_counter_table(stats.get("unique_by_source_family", Counter()))}

### Duplicate Files Skipped By Source Family
{compat_generation_counter_table(stats.get("duplicate_by_source_family", Counter()))}

### Extracted Invocations By Source Family
{compat_generation_counter_table(stats.get("extracted_by_source_family", Counter()))}

## Taxonomy
{chr(10).join(f"- `{key}`: {value}" for key, value in COMPAT_TAXONOMY.items())}

## Bash Segment Categories
{compat_markdown_table(by_segment_category, ["primary_category", "count"])}

## All Invocation Categories
{compat_markdown_table(by_invocation_category, ["primary_category", "count"])}

## Source Families
{compat_markdown_table(by_source_family, ["source_family", "count"])}

## Source Formats
{compat_markdown_table(by_source_format, ["source_format", "count"])}

## Category By Agent And Mode
{compat_markdown_table(by_agent_mode_category, ["agent", "mode", "primary_category", "count"], limit=40)}

## Category By Source Family, Agent, Mode, And Benchmark
{compat_markdown_table(top_run_category, ["source_family", "agent", "mode", "benchmark", "primary_category", "count"], limit=40)}

## Nonzero Exit Examples
{compat_markdown_table(nonzero_examples, ["agent", "mode", "benchmark", "primary_category", "exit_code", "inner_command", "source_path"], limit=20)}
"""
    report_path = output_dir / "report.md"
    report_path.write_text(report)
    return report_path


def compat_write_outputs(
    output_dir: Path,
    calls: list[CompatInvocation],
    stats: dict[str, Any],
    source_files_json: list[str],
) -> dict[str, Path]:
    output_dir.mkdir(parents=True, exist_ok=True)
    invocation_rows = [compat_invocation_row(call) for call in calls]
    segment_rows = compat_segment_rows(calls)
    per_run_rows = compat_per_run_rows(calls)
    paths = {
        "invocations": output_dir / "command_invocations.csv",
        "segments": output_dir / "command_segments.csv",
        "per_run": output_dir / "per_run_category_summary.csv",
        "taxonomy": output_dir / "category_taxonomy.json",
        "source_files": output_dir / "source_files.json",
        "report": output_dir / "report.md",
    }
    compat_write_csv(paths["invocations"], invocation_rows, COMPAT_INVOCATION_FIELDS)
    compat_write_csv(paths["segments"], segment_rows, COMPAT_SEGMENT_FIELDS)
    compat_write_csv(paths["per_run"], per_run_rows, COMPAT_RUN_SUMMARY_FIELDS)
    paths["taxonomy"].write_text(json.dumps(COMPAT_TAXONOMY, indent=2) + "\n")
    paths["source_files"].write_text(json.dumps(source_files_json, indent=2) + "\n")
    compat_write_report(output_dir, calls, segment_rows, source_files_json, stats)
    return paths


def compat_main() -> None:
    parser = argparse.ArgumentParser(
        description="Aggregate and categorize bash calls from EVMBench trace artifacts."
    )
    parser.add_argument(
        "--source",
        action="append",
        default=[],
        metavar="FAMILY=PATH",
        help="Source root to scan. May be repeated. Defaults to all known trace roots.",
    )
    parser.add_argument(
        "--runs-dir",
        type=Path,
        default=None,
        help="Backward-compatible alias for a single source root labeled runs.",
    )
    parser.add_argument("--output-dir", type=Path, default=COMPAT_OUTPUT_DIR)
    parser.add_argument("--no-plots", action="store_true", help="Accepted for compatibility; plots are not generated.")
    args = parser.parse_args()

    if args.source:
        sources = [compat_parse_source(value) for value in args.source]
    elif args.runs_dir is not None:
        sources = [CompatSourceSpec(family="runs", root=args.runs_dir)]
    else:
        sources = compat_default_sources()

    calls, stats, source_files_json = compat_extract_calls(sources)
    paths = compat_write_outputs(args.output_dir.resolve(), calls, stats, source_files_json)
    print(f"Extracted {len(calls)} invocations/tools from {len(source_files_json)} source files")
    print(f"Wrote {paths['report']}")
    print(f"Wrote {paths['invocations']}")


if __name__ == "__main__":
    compat_main()
