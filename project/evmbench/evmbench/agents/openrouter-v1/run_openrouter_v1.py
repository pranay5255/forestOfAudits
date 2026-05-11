#!/usr/bin/env python3
"""OpenRouter v1 mixed-task experiment harness for EVMBench."""

from __future__ import annotations

import argparse
import ast
import csv
import hashlib
import json
import os
import re
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TextIO
from urllib.parse import urlparse, urlunparse

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evmbench.utils import get_timestamp

Mode = Literal["detect", "patch", "exploit"]

DEFAULT_PROVIDER = "openrouter"
DEFAULT_OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
DEFAULT_OPENAI_BASE_URL = "https://api.openai.com/v1"
DEFAULT_BASE_URL = DEFAULT_OPENROUTER_BASE_URL
MATRIX_FILENAME = "openrouter-v1-matrix.json"
RESULTS_FILENAME = "openrouter-v1-results.json"
SUMMARY_FILENAME = "openrouter-v1-summary.md"
CSV_FILENAME = "openrouter-v1-results.csv"


@dataclass(frozen=True)
class TaskSpec:
    mode: Mode
    audit_id: str


@dataclass(frozen=True)
class HarnessSpec:
    slug: str
    agent_id: str
    label: str


@dataclass(frozen=True)
class ProviderSpec:
    provider_id: str
    api_key_env_var: str
    default_base_url: str
    display_label: str


@dataclass(frozen=True)
class OpenRouterV1Run:
    run_key: str
    provider: str
    harness: str
    agent_id: str
    model: str
    base_url: str
    api_key_env_var: str
    audit_id: str
    mode: Mode
    runs_dir: Path
    command: tuple[str, ...]
    env: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runs_dir"] = str(self.runs_dir)
        payload["command"] = list(self.command)
        return payload


HARNESS_SPECS: dict[str, HarnessSpec] = {
    "codex": HarnessSpec("codex", "codex-openrouter-v1", "Codex CLI via OpenRouter Responses"),
    "opencode": HarnessSpec("opencode", "opencode-openrouter-v1", "OpenCode via OpenRouter"),
}

PROVIDER_SPECS: dict[str, ProviderSpec] = {
    "openrouter": ProviderSpec(
        provider_id="openrouter",
        api_key_env_var="OPENROUTER_API_KEY",
        default_base_url=DEFAULT_OPENROUTER_BASE_URL,
        display_label="OpenRouter",
    ),
    "openai": ProviderSpec(
        provider_id="openai",
        api_key_env_var="OPENAI_API_KEY",
        default_base_url=DEFAULT_OPENAI_BASE_URL,
        display_label="OpenAI",
    ),
}


def project_root() -> Path:
    return PROJECT_ROOT


def default_output_root() -> Path:
    return project_root() / "runs" / "openrouter-v1" / get_timestamp()


def parse_mode(raw: str) -> Mode:
    if raw in {"detect", "patch", "exploit"}:
        return raw  # type: ignore[return-value]
    raise ValueError(f"Unsupported mode: {raw!r}. Expected detect, patch, or exploit.")


def parse_task_list(raw: str) -> list[TaskSpec]:
    tasks: list[TaskSpec] = []
    for part in raw.split(","):
        item = part.strip()
        if not item:
            continue
        if ":" not in item:
            raise ValueError(f"Task {item!r} must use mode:audit_id syntax.")
        mode_raw, audit_id = item.split(":", 1)
        mode = parse_mode(mode_raw.strip())
        audit_id = audit_id.strip()
        if not audit_id:
            raise ValueError(f"Task {item!r} is missing an audit ID.")
        tasks.append(TaskSpec(mode=mode, audit_id=audit_id))
    if not tasks:
        raise ValueError("--tasks did not contain any tasks.")
    return tasks


def parse_harness_list(raw: str) -> list[HarnessSpec]:
    harnesses: list[HarnessSpec] = []
    seen: set[str] = set()
    for part in raw.split(","):
        key = part.strip()
        if not key:
            continue
        spec = HARNESS_SPECS.get(key)
        if spec is None:
            known = ", ".join(sorted(HARNESS_SPECS))
            raise ValueError(f"Unknown harness {key!r}. Known harnesses: {known}.")
        if spec.slug in seen:
            continue
        seen.add(spec.slug)
        harnesses.append(spec)
    if not harnesses:
        raise ValueError("--harnesses did not contain any known harnesses.")
    return harnesses


def parse_provider(raw: str) -> ProviderSpec:
    key = raw.strip().lower()
    spec = PROVIDER_SPECS.get(key)
    if spec is None:
        known = ", ".join(sorted(PROVIDER_SPECS))
        raise ValueError(f"Unknown provider {raw!r}. Known providers: {known}.")
    return spec


def normalize_provider_base_url(provider: ProviderSpec | str, raw: str | None) -> str:
    spec = parse_provider(provider) if isinstance(provider, str) else provider
    value = (raw or spec.default_base_url).strip().rstrip("/")
    if not value:
        value = spec.default_base_url
    if "://" not in value:
        value = f"https://{value}"
    parsed = urlparse(value)
    path = parsed.path.rstrip("/")
    if path.endswith("/responses"):
        path = path[: -len("/responses")]
    if spec.provider_id == "openrouter":
        if parsed.netloc == "openrouter.ai" and path in {"", "/"}:
            path = "/api/v1"
        if not path:
            path = "/api/v1"
    elif spec.provider_id == "openai":
        if parsed.netloc == "api.openai.com" and path in {"", "/"}:
            path = "/v1"
        if not path:
            path = "/v1"
    return urlunparse((parsed.scheme, parsed.netloc, path, "", "", ""))


def normalize_openrouter_base_url(raw: str | None) -> str:
    return normalize_provider_base_url("openrouter", raw)


def _slugify(value: str) -> str:
    slash_normalized = value.strip().replace("/", "__")
    safe = re.sub(r"[^A-Za-z0-9._-]+", "-", slash_normalized)
    safe = re.sub(r"-+", "-", safe).strip("-._")
    return safe or "empty"


def model_run_segment(model: str) -> str:
    digest = hashlib.sha1(model.encode("utf-8")).hexdigest()[:8]
    return f"{_slugify(model)}-{digest}"


def run_key_for(harness: str, model: str, task: TaskSpec) -> str:
    return f"{harness}--{model_run_segment(model)}--{task.mode}--{_slugify(task.audit_id)}"


def submission_filename(mode: Mode) -> str:
    if mode == "detect":
        return "audit.md"
    if mode == "patch":
        return "agent.diff"
    if mode == "exploit":
        return "txs.json"
    raise ValueError(f"Unsupported mode: {mode!r}")


def unique_audit_ids(tasks: list[TaskSpec]) -> list[str]:
    seen: set[str] = set()
    audits: list[str] = []
    for task in tasks:
        if task.audit_id in seen:
            continue
        seen.add(task.audit_id)
        audits.append(task.audit_id)
    return audits


def docker_build_commands(
    tasks: list[TaskSpec],
    *,
    tag_prefix: str = "evmbench/audit",
    build_network: str | None = None,
    use_cache: bool = False,
    no_build_base: bool = False,
    skip_ploit_builder: bool = False,
) -> list[list[str]]:
    commands: list[list[str]] = []
    if not skip_ploit_builder:
        commands.append(
            [
                "docker",
                "build",
                "-f",
                "ploit/Dockerfile",
                "-t",
                "ploit-builder:latest",
                "--target",
                "ploit-builder",
                ".",
            ]
        )

    for index, audit_id in enumerate(unique_audit_ids(tasks)):
        command = ["uv", "run", "docker_build.py", "--audit", audit_id, "--tag-prefix", tag_prefix]
        if no_build_base or index > 0:
            command.append("--no-build-base")
        if use_cache:
            command.append("--use-cache")
        if build_network:
            command.extend(["--build-network", build_network])
        commands.append(command)
    return commands


def build_evmbench_command(
    *,
    agent_id: str,
    audit_id: str,
    runs_dir: Path,
    mode: Mode,
    agent_timeout_seconds: float | None,
) -> tuple[str, ...]:
    command = [
        "uv",
        "run",
        "python",
        "-m",
        "evmbench.nano.entrypoint",
        f"evmbench.audit={audit_id}",
        f"evmbench.mode={mode}",
        f"evmbench.audit_split={mode}-tasks",
        "evmbench.hint_level=none",
        "evmbench.log_to_run_dir=True",
        f"evmbench.runs_dir={runs_dir}",
        "evmbench.solver=evmbench.nano.solver.EVMbenchSolver",
        f"evmbench.solver.agent_id={agent_id}",
        "runner.concurrency=1",
    ]
    if agent_timeout_seconds and agent_timeout_seconds > 0:
        command.append(f"evmbench.solver.timeout={int(agent_timeout_seconds)}")
    return tuple(command)


def build_run_matrix(
    *,
    output_root: Path,
    tasks: list[TaskSpec],
    harnesses: list[HarnessSpec],
    models: list[str],
    base_url: str | None,
    provider: ProviderSpec | str = DEFAULT_PROVIDER,
    agent_timeout_seconds: float | None = None,
) -> list[OpenRouterV1Run]:
    matrix: list[OpenRouterV1Run] = []
    provider_spec = parse_provider(provider) if isinstance(provider, str) else provider
    normalized_base_url = normalize_provider_base_url(provider_spec, base_url)
    for harness in harnesses:
        for model in models:
            for task in tasks:
                run_key = run_key_for(harness.slug, model, task)
                runs_dir = output_root / "evmbench_runs" / run_key
                env = {
                    "EVMBENCH_LLM_PROVIDER": provider_spec.provider_id,
                    "EVMBENCH_LLM_MODEL": model,
                    "EVMBENCH_LLM_BASE_URL": normalized_base_url,
                    "EVMBENCH_LLM_API_KEY_ENV": provider_spec.api_key_env_var,
                }
                if provider_spec.provider_id == "openrouter":
                    env.update(
                        {
                            "EVMBENCH_OPENROUTER_MODEL": model,
                            "EVMBENCH_OPENROUTER_BASE_URL": normalized_base_url,
                        }
                    )
                if agent_timeout_seconds and agent_timeout_seconds > 0:
                    env["EVMBENCH_OPENROUTER_AGENT_TIMEOUT_SECONDS"] = str(int(agent_timeout_seconds))
                matrix.append(
                    OpenRouterV1Run(
                        run_key=run_key,
                        provider=provider_spec.provider_id,
                        harness=harness.slug,
                        agent_id=harness.agent_id,
                        model=model,
                        base_url=normalized_base_url,
                        api_key_env_var=provider_spec.api_key_env_var,
                        audit_id=task.audit_id,
                        mode=task.mode,
                        runs_dir=runs_dir,
                        command=build_evmbench_command(
                            agent_id=harness.agent_id,
                            audit_id=task.audit_id,
                            runs_dir=runs_dir,
                            mode=task.mode,
                            agent_timeout_seconds=agent_timeout_seconds,
                        ),
                        env=env,
                    )
                )
    return matrix


def matrix_payload(output_root: Path, matrix: list[OpenRouterV1Run]) -> dict[str, Any]:
    selected_harnesses = []
    seen: set[str] = set()
    for item in matrix:
        if item.harness in seen:
            continue
        seen.add(item.harness)
        selected_harnesses.append(asdict(HARNESS_SPECS[item.harness]))
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "output_root": str(output_root),
        "provider": matrix[0].provider if matrix else DEFAULT_PROVIDER,
        "api_key_env_var": matrix[0].api_key_env_var if matrix else PROVIDER_SPECS[DEFAULT_PROVIDER].api_key_env_var,
        "base_url": matrix[0].base_url if matrix else normalize_openrouter_base_url(None),
        "harnesses": selected_harnesses,
        "runs": [item.to_dict() for item in matrix],
    }


def write_matrix(output_root: Path, matrix: list[OpenRouterV1Run]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / MATRIX_FILENAME
    path.write_text(json.dumps(matrix_payload(output_root, matrix), indent=2), encoding="utf-8")
    return path


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _load_matrix(output_root: Path) -> list[OpenRouterV1Run] | None:
    payload = _read_json(output_root / MATRIX_FILENAME)
    if not payload:
        return None
    raw_runs = payload.get("runs")
    if not isinstance(raw_runs, list):
        return None
    matrix: list[OpenRouterV1Run] = []
    for raw in raw_runs:
        if not isinstance(raw, dict):
            continue
        try:
            matrix.append(
                OpenRouterV1Run(
                    run_key=str(raw["run_key"]),
                    provider=str(raw.get("provider", DEFAULT_PROVIDER)),
                    harness=str(raw["harness"]),
                    agent_id=str(raw["agent_id"]),
                    model=str(raw["model"]),
                    base_url=str(raw["base_url"]),
                    api_key_env_var=str(raw.get("api_key_env_var", PROVIDER_SPECS[DEFAULT_PROVIDER].api_key_env_var)),
                    audit_id=str(raw["audit_id"]),
                    mode=parse_mode(str(raw["mode"])),
                    runs_dir=Path(str(raw["runs_dir"])),
                    command=tuple(str(part) for part in raw.get("command", [])),
                    env={str(k): str(v) for k, v in dict(raw.get("env", {})).items()},
                )
            )
        except (KeyError, TypeError, ValueError):
            continue
    return matrix


def print_plan(matrix: list[OpenRouterV1Run]) -> None:
    for item in matrix:
        env_prefix = " ".join(f"{key}={shlex.quote(value)}" for key, value in sorted(item.env.items()))
        print(f"\n# {item.run_key}")
        print(
            f"# provider={item.provider} harness={item.harness} model={item.model} "
            f"mode={item.mode} audit={item.audit_id}"
        )
        print(f"{env_prefix} {shlex.join(item.command)}")


def command_log_paths(output_root: Path, item: OpenRouterV1Run) -> tuple[Path, Path]:
    log_dir = output_root / "_command_logs"
    return log_dir / f"{item.run_key}.stdout.log", log_dir / f"{item.run_key}.stderr.log"


def task_result_path(output_root: Path, item: OpenRouterV1Run) -> Path:
    return output_root / "_task_results" / f"{item.run_key}.json"


def _openrouter_log(message: str) -> None:
    print(f"[openrouter-v1] {message}", flush=True)


def _format_seconds(value: float | None) -> str:
    if value is None:
        return "-"
    if value < 60:
        return f"{value:.1f}s"
    minutes, seconds = divmod(value, 60)
    if minutes < 60:
        return f"{int(minutes)}m{seconds:04.1f}s"
    hours, minutes = divmod(minutes, 60)
    return f"{int(hours)}h{int(minutes):02d}m{seconds:04.1f}s"


def _stream_pipe(
    pipe: TextIO,
    *,
    log_file: TextIO,
    terminal: TextIO,
    prefix: str,
    lock: threading.Lock,
) -> None:
    try:
        for line in iter(pipe.readline, ""):
            log_file.write(line)
            log_file.flush()
            with lock:
                terminal.write(f"{prefix}{line}")
                terminal.flush()
    finally:
        pipe.close()


def _run_command_streaming(
    item: OpenRouterV1Run,
    *,
    stdout_log: Path,
    stderr_log: Path,
    timeout_seconds: float | None,
) -> int:
    env = os.environ.copy()
    env.update(item.env)
    env.setdefault("PYTHONUNBUFFERED", "1")
    with stdout_log.open("w", encoding="utf-8", buffering=1) as stdout_file, stderr_log.open(
        "w",
        encoding="utf-8",
        buffering=1,
    ) as stderr_file:
        process = subprocess.Popen(
            item.command,
            cwd=project_root(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
            start_new_session=True,
        )
        assert process.stdout is not None
        assert process.stderr is not None
        lock = threading.Lock()
        stdout_thread = threading.Thread(
            target=_stream_pipe,
            kwargs={
                "pipe": process.stdout,
                "log_file": stdout_file,
                "terminal": sys.stdout,
                "prefix": f"[openrouter-v1][{item.run_key}][stdout] ",
                "lock": lock,
            },
            daemon=True,
        )
        stderr_thread = threading.Thread(
            target=_stream_pipe,
            kwargs={
                "pipe": process.stderr,
                "log_file": stderr_file,
                "terminal": sys.stderr,
                "prefix": f"[openrouter-v1][{item.run_key}][stderr] ",
                "lock": lock,
            },
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = process.wait(timeout=timeout_seconds)
        except subprocess.TimeoutExpired:
            message = (
                f"[openrouter-v1] command timed out after {_format_seconds(timeout_seconds)}; "
                "terminating process group.\n"
            )
            stderr_file.write(message)
            stderr_file.flush()
            with lock:
                sys.stderr.write(f"[openrouter-v1][{item.run_key}][stderr] {message}")
                sys.stderr.flush()
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=30)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            returncode = 124
        except KeyboardInterrupt:
            os.killpg(process.pid, signal.SIGTERM)
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                os.killpg(process.pid, signal.SIGKILL)
                process.wait()
            raise
        finally:
            stdout_thread.join()
            stderr_thread.join()
    return returncode


def _audit_from_run_dir(run_dir: Path) -> str:
    return run_dir.name.split("_", 1)[0]


def find_run_dir(item: OpenRouterV1Run) -> Path | None:
    candidates: list[Path] = []
    if item.runs_dir.exists():
        for run_log in item.runs_dir.rglob("run.log"):
            run_dir = run_log.parent
            if _audit_from_run_dir(run_dir) == item.audit_id:
                candidates.append(run_dir)
    if not candidates:
        return None
    return max(candidates, key=lambda path: path.stat().st_mtime)


def parse_run_grade(run_dir: Path | None) -> dict[str, Any] | None:
    if not run_dir:
        return None
    run_log = run_dir / "run.log"
    if not run_log.exists():
        return None
    grade_payload: dict[str, Any] | None = None
    for line in run_log.read_text(encoding="utf-8", errors="replace").splitlines():
        if "'grade'" not in line and '"grade"' not in line:
            continue
        try:
            event = ast.literal_eval(line)
        except (SyntaxError, ValueError):
            continue
        if not isinstance(event, dict):
            continue
        grade = event.get("grade")
        if isinstance(grade, dict):
            evmbench_result = grade.get("evmbench_result")
            if isinstance(evmbench_result, dict):
                grade_payload = evmbench_result
    return grade_payload


def _float_or_none(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value)
        except ValueError:
            return None
    return None


def _percentage(score: float | None, max_score: float | None) -> float | None:
    if score is None or max_score in (None, 0):
        return None
    return (score / max_score) * 100.0


def _relative_paths(paths: list[Path], base: Path) -> list[str]:
    rendered: list[str] = []
    for path in paths:
        try:
            rendered.append(str(path.relative_to(base)))
        except ValueError:
            rendered.append(str(path))
    return rendered


def _trajectory_manifest(run_dir: Path | None) -> tuple[str | None, dict[str, Any] | None]:
    if not run_dir:
        return None, None
    candidates = [
        run_dir / "logs" / "codex" / "trajectory-manifest.json",
        run_dir / "logs" / "opencode" / "trajectory-manifest.json",
        run_dir / "modal" / "logs" / "codex" / "trajectory-manifest.json",
        run_dir / "modal" / "logs" / "opencode" / "trajectory-manifest.json",
    ]
    for path in candidates:
        manifest = _read_json(path)
        if manifest:
            try:
                return str(path.relative_to(run_dir)), manifest
            except ValueError:
                return str(path), manifest
    return None, None


def _manifest_count(manifest: dict[str, Any] | None, key: str) -> int:
    if not manifest:
        return 0
    value = manifest.get(key)
    return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else 0


def _append_failure_reason(existing: str | None, reason: str) -> str:
    if not existing:
        return reason
    if reason in existing:
        return existing
    return f"{existing}; {reason}"


def _command_status_from_task_result(output_root: Path, item: OpenRouterV1Run) -> dict[str, Any] | None:
    payload = _read_json(task_result_path(output_root, item))
    if not payload:
        return None
    status = payload.get("command_status")
    return status if isinstance(status, dict) else None


def summarize_row(
    output_root: Path,
    item: OpenRouterV1Run,
    command_status: dict[str, Any] | None = None,
) -> dict[str, Any]:
    run_dir = find_run_dir(item)
    grade = parse_run_grade(run_dir)
    command_status = command_status or _command_status_from_task_result(output_root, item)
    submission_path = run_dir / "submission" / submission_filename(item.mode) if run_dir else None
    submission_exists = bool(submission_path and submission_path.exists() and submission_path.stat().st_size > 0)
    trajectory_manifest_path, trajectory_manifest = _trajectory_manifest(run_dir)
    expected_trajectory_count = _manifest_count(trajectory_manifest, "expected_trajectory_count")
    found_trajectory_count = _manifest_count(trajectory_manifest, "found_trajectory_count")
    missing_trajectory_count = _manifest_count(trajectory_manifest, "missing_trajectory_count")

    score = _float_or_none(grade.get("score") if grade else None)
    max_score = _float_or_none(grade.get("max_score") if grade else None)
    detect_award = _float_or_none(grade.get("detect_award") if grade else None)
    detect_max_award = _float_or_none(grade.get("detect_max_award") if grade else None)
    agent_output = grade.get("agent_output") if isinstance(grade, dict) else None
    agent_runtime = None
    if isinstance(agent_output, dict):
        agent_runtime = _float_or_none(agent_output.get("runtime_in_seconds"))

    trajectory_paths = _relative_paths(
        sorted(run_dir.rglob("*.traj.json")) if run_dir else [],
        run_dir or output_root,
    )

    failure_reason = None
    if command_status and command_status.get("timed_out"):
        failure_reason = f"command timed out after {_format_seconds(_float_or_none(command_status.get('timeout_seconds')))}"
    elif command_status and command_status.get("returncode") not in (0, None):
        failure_reason = f"command exited {command_status.get('returncode')}"
    elif not run_dir:
        failure_reason = "run directory not found"
    elif not submission_exists:
        failure_reason = f"missing or empty submission/{submission_filename(item.mode)}"
    elif not grade:
        failure_reason = "grade not found in run.log"

    if run_dir and not trajectory_manifest:
        failure_reason = _append_failure_reason(failure_reason, "trajectory manifest not found")
    elif trajectory_manifest and missing_trajectory_count:
        failure_reason = _append_failure_reason(failure_reason, "missing trajectories")

    return {
        "run_key": item.run_key,
        "provider": item.provider,
        "harness": item.harness,
        "agent_id": item.agent_id,
        "model": item.model,
        "base_url": item.base_url,
        "api_key_env_var": item.api_key_env_var,
        "audit_id": item.audit_id,
        "mode": item.mode,
        "runs_dir": str(item.runs_dir),
        "run_dir": str(run_dir) if run_dir else None,
        "command": list(item.command),
        "env": item.env,
        "command_status": command_status,
        "submission_path": str(submission_path) if submission_path else None,
        "submission_exists": submission_exists,
        "score": score,
        "max_score": max_score,
        "score_percentage": _percentage(score, max_score),
        "detect_award": detect_award,
        "detect_max_award": detect_max_award,
        "detect_award_percentage": _percentage(detect_award, detect_max_award),
        "agent_runtime_seconds": agent_runtime,
        "trajectory_paths": trajectory_paths,
        "trajectory_manifest": trajectory_manifest_path,
        "expected_trajectory_count": expected_trajectory_count,
        "found_trajectory_count": found_trajectory_count,
        "missing_trajectory_count": missing_trajectory_count,
        "failure_reason": failure_reason,
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_key: dict[str, dict[str, Any]] = {}
    for row in rows:
        key = f"{row['harness']}::{row['model']}"
        bucket = by_key.setdefault(
            key,
            {
                "harness": row["harness"],
                "model": row["model"],
                "n_rows": 0,
                "n_successful_submissions": 0,
                "n_failures": 0,
                "score": 0.0,
                "max_score": 0.0,
                "detect_award": 0.0,
                "detect_max_award": 0.0,
            },
        )
        bucket["n_rows"] += 1
        if row.get("submission_exists"):
            bucket["n_successful_submissions"] += 1
        if row.get("failure_reason"):
            bucket["n_failures"] += 1
        for field in ("score", "max_score", "detect_award", "detect_max_award"):
            value = _float_or_none(row.get(field))
            if value is not None:
                bucket[field] += value
    for bucket in by_key.values():
        bucket["score_percentage"] = _percentage(bucket["score"], bucket["max_score"])
        bucket["detect_award_percentage"] = _percentage(bucket["detect_award"], bucket["detect_max_award"])
    return by_key


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(output_root: Path, rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    lines = [
        "# OpenRouter V1 Summary",
        "",
        f"Output root: `{output_root}`",
        "",
        "## Aggregate",
        "",
        "| Harness | Model | Rows | Submissions | Failures | Score | Detect Award |",
        "| --- | --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for bucket in sorted(aggregate.values(), key=lambda item: (item["harness"], item["model"])):
        lines.append(
            "| "
            + " | ".join(
                [
                    str(bucket["harness"]),
                    str(bucket["model"]),
                    str(bucket["n_rows"]),
                    str(bucket["n_successful_submissions"]),
                    str(bucket["n_failures"]),
                    f"{bucket['score']:.2f}/{bucket['max_score']:.2f} ({_fmt(bucket['score_percentage'])}%)",
                    f"{bucket['detect_award']:.2f}/{bucket['detect_max_award']:.2f} ({_fmt(bucket['detect_award_percentage'])}%)",
                ]
            )
            + " |"
        )

    lines.extend(
        [
            "",
            "## Per Task",
            "",
            "| Run Key | Harness | Model | Mode | Audit | Submission | Trace | Score | Failure | Run Dir |",
            "| --- | --- | --- | --- | --- | --- | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        score = (
            f"{_fmt(row.get('score'))}/{_fmt(row.get('max_score'))} ({_fmt(row.get('score_percentage'))}%)"
            if row.get("score") is not None or row.get("max_score") is not None
            else "-"
        )
        trace = (
            f"{row.get('found_trajectory_count')}/{row.get('expected_trajectory_count')}"
            if row.get("expected_trajectory_count")
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["run_key"]),
                    str(row["harness"]),
                    str(row["model"]),
                    str(row["mode"]),
                    str(row["audit_id"]),
                    "yes" if row.get("submission_exists") else "no",
                    trace,
                    score,
                    str(row.get("failure_reason") or ""),
                    f"`{row.get('run_dir')}`" if row.get("run_dir") else "",
                ]
            )
            + " |"
        )
    lines.append("")
    return "\n".join(lines)


def write_results_csv(output_root: Path, rows: list[dict[str, Any]]) -> Path:
    csv_path = output_root / CSV_FILENAME
    fieldnames = [
        "run_key",
        "provider",
        "harness",
        "agent_id",
        "model",
        "base_url",
        "api_key_env_var",
        "mode",
        "audit_id",
        "submission_exists",
        "score",
        "max_score",
        "score_percentage",
        "detect_award",
        "detect_max_award",
        "detect_award_percentage",
        "agent_runtime_seconds",
        "failure_reason",
        "trajectory_manifest",
        "expected_trajectory_count",
        "found_trajectory_count",
        "missing_trajectory_count",
        "run_dir",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        for row in rows:
            writer.writerow({key: row.get(key) for key in fieldnames})
    return csv_path


def summarize_openrouter_v1(output_root: Path, matrix: list[OpenRouterV1Run] | None = None) -> dict[str, Any]:
    output_root = output_root.resolve()
    matrix = matrix or _load_matrix(output_root)
    if matrix is None:
        raise FileNotFoundError(f"Missing {output_root / MATRIX_FILENAME}; cannot summarize OpenRouter v1 run.")
    rows = [summarize_row(output_root, item) for item in matrix]
    aggregate = aggregate_rows(rows)
    output_root.mkdir(parents=True, exist_ok=True)
    csv_path = write_results_csv(output_root, rows)
    payload = {
        "output_root": str(output_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": rows,
        "aggregate": aggregate,
        "results_csv_path": str(csv_path),
    }
    (output_root / RESULTS_FILENAME).write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (output_root / SUMMARY_FILENAME).write_text(render_markdown(output_root, rows, aggregate), encoding="utf-8")
    return payload


def _write_task_result(
    output_root: Path,
    item: OpenRouterV1Run,
    status: dict[str, Any],
) -> dict[str, Any]:
    row = summarize_row(output_root, item, status)
    payload = {
        "run": item.to_dict(),
        "command_status": status,
        "summary": row,
    }
    path = task_result_path(output_root, item)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    return row


def _print_run_summary(item: OpenRouterV1Run, status: dict[str, Any], row: dict[str, Any]) -> None:
    _openrouter_log(
        "finished "
        f"{item.run_key}: returncode={status['returncode']} "
        f"runtime={_format_seconds(_float_or_none(status.get('runtime_seconds')))}"
    )
    _openrouter_log(f"command logs: {status['stdout_log']} | {status['stderr_log']}")
    if row.get("run_dir"):
        _openrouter_log(f"run dir: {row['run_dir']}")
    if row.get("failure_reason"):
        _openrouter_log(f"failure: {row['failure_reason']}")


def run_matrix(
    output_root: Path,
    matrix: list[OpenRouterV1Run],
    *,
    stop_on_failure: bool,
    item_timeout_seconds: float | None,
) -> int:
    write_matrix(output_root, matrix)
    (output_root / "_command_logs").mkdir(parents=True, exist_ok=True)
    (output_root / "_task_results").mkdir(parents=True, exist_ok=True)
    overall_returncode = 0
    _openrouter_log(f"matrix: {len(matrix)} run(s), output_root={output_root}")
    for index, item in enumerate(matrix, start=1):
        stdout_log, stderr_log = command_log_paths(output_root, item)
        _openrouter_log(
            f"starting {index}/{len(matrix)} {item.run_key} "
            f"agent={item.agent_id} base_url={item.base_url}"
        )
        _openrouter_log(f"command: {shlex.join(item.command)}")
        _openrouter_log(f"streaming logs to: {stdout_log} | {stderr_log}")
        started_at = time.time()
        returncode = _run_command_streaming(
            item,
            stdout_log=stdout_log,
            stderr_log=stderr_log,
            timeout_seconds=item_timeout_seconds,
        )
        ended_at = time.time()
        status = {
            **item.to_dict(),
            "returncode": returncode,
            "started_at": started_at,
            "ended_at": ended_at,
            "runtime_seconds": ended_at - started_at,
            "stdout_log": str(stdout_log),
            "stderr_log": str(stderr_log),
            "streamed": True,
            "timed_out": returncode == 124,
            "timeout_seconds": item_timeout_seconds,
        }
        row = _write_task_result(output_root, item, status)
        _print_run_summary(item, status, row)
        if returncode != 0:
            overall_returncode = returncode
            if stop_on_failure:
                _openrouter_log("stop-on-failure requested; stopping matrix early.")
                break

    payload = summarize_openrouter_v1(output_root, matrix)
    _openrouter_log(f"summary: {output_root / SUMMARY_FILENAME}")
    _openrouter_log(f"results: {output_root / RESULTS_FILENAME}")
    _openrouter_log(f"csv: {payload['results_csv_path']}")
    return overall_returncode


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_matrix_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--tasks", required=True, help="Comma-separated mode:audit_id entries.")
        subparser.add_argument("--harnesses", required=True, help="Comma-separated harnesses: codex,opencode.")
        subparser.add_argument("--model", action="append", required=True, help="Provider model id. Repeatable.")
        subparser.add_argument(
            "--provider",
            choices=sorted(PROVIDER_SPECS),
            default=DEFAULT_PROVIDER,
            help="LLM provider to use for agent requests.",
        )
        subparser.add_argument("--base-url", default=None, help="Override the provider API base URL.")
        subparser.add_argument("--output-root", type=Path, default=None)
        subparser.add_argument(
            "--agent-timeout-seconds",
            type=float,
            default=float(os.getenv("OPENROUTER_V1_AGENT_TIMEOUT_SECONDS", "0") or "0"),
            help="EVMBench solver timeout per task. Use 0 to keep the solver default.",
        )

    plan_parser = subparsers.add_parser("plan", help="Print the OpenRouter v1 command matrix.")
    add_matrix_args(plan_parser)

    run_parser = subparsers.add_parser("run", help="Run the OpenRouter v1 command matrix sequentially.")
    add_matrix_args(run_parser)
    run_parser.add_argument("--stop-on-failure", action="store_true")
    run_parser.add_argument(
        "--item-timeout-seconds",
        type=float,
        default=float(os.getenv("OPENROUTER_V1_ITEM_TIMEOUT_SECONDS", "0") or "0"),
        help="Wall-clock timeout for one uv/EVMBench process. Use 0 to disable.",
    )

    summarize_parser = subparsers.add_parser("summarize", help="Summarize an existing OpenRouter v1 output root.")
    summarize_parser.add_argument("--output-root", type=Path, required=True)

    docker_parser = subparsers.add_parser(
        "docker-plan",
        help="Print Docker build commands for the audit images required by --tasks.",
    )
    docker_parser.add_argument("--tasks", required=True, help="Comma-separated mode:audit_id entries.")
    docker_parser.add_argument("--tag-prefix", default="evmbench/audit")
    docker_parser.add_argument("--build-network", default=os.getenv("DOCKER_BUILD_NETWORK"))
    docker_parser.add_argument("--use-cache", action="store_true")
    docker_parser.add_argument("--no-build-base", action="store_true")
    docker_parser.add_argument("--skip-ploit-builder", action="store_true")
    docker_parser.add_argument("--json", action="store_true", help="Emit command argv arrays as JSON.")
    return parser


def _matrix_from_args(args: argparse.Namespace) -> tuple[Path, list[OpenRouterV1Run]]:
    output_root = (args.output_root or default_output_root()).resolve()
    tasks = parse_task_list(args.tasks)
    harnesses = parse_harness_list(args.harnesses)
    models = [model.strip() for model in args.model if model and model.strip()]
    if not models:
        raise ValueError("--model did not contain any model IDs.")
    matrix = build_run_matrix(
        output_root=output_root,
        tasks=tasks,
        harnesses=harnesses,
        models=models,
        provider=parse_provider(args.provider),
        base_url=args.base_url,
        agent_timeout_seconds=args.agent_timeout_seconds if args.agent_timeout_seconds > 0 else None,
    )
    return output_root, matrix


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        if args.command == "plan":
            output_root, matrix = _matrix_from_args(args)
            print(f"# OpenRouter v1 output root: {output_root}")
            print(f"# Runs: {len(matrix)}")
            print_plan(matrix)
            return 0
        if args.command == "run":
            output_root, matrix = _matrix_from_args(args)
            print(f"Writing OpenRouter v1 outputs to {output_root}")
            item_timeout_seconds = args.item_timeout_seconds if args.item_timeout_seconds > 0 else None
            return run_matrix(
                output_root,
                matrix,
                stop_on_failure=args.stop_on_failure,
                item_timeout_seconds=item_timeout_seconds,
            )
        if args.command == "summarize":
            payload = summarize_openrouter_v1(args.output_root)
            print(json.dumps({"output_root": payload["output_root"], "n_rows": len(payload["rows"])}, indent=2))
            return 0
        if args.command == "docker-plan":
            tasks = parse_task_list(args.tasks)
            commands = docker_build_commands(
                tasks,
                tag_prefix=args.tag_prefix,
                build_network=args.build_network,
                use_cache=args.use_cache,
                no_build_base=args.no_build_base,
                skip_ploit_builder=args.skip_ploit_builder,
            )
            if args.json:
                print(json.dumps(commands, indent=2))
            else:
                print("# Build the local Docker images required by this OpenRouter v1 task set.")
                for command in commands:
                    print(shlex.join(command))
            return 0
    except Exception as exc:
        parser.exit(2, f"error: {exc}\n")
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
