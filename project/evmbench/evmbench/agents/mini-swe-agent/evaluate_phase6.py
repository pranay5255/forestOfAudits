#!/usr/bin/env python3
"""Phase 6 evaluation harness for EVMBench mini-swe-agent experiments."""

from __future__ import annotations

import argparse
import ast
import csv
import json
import os
import shlex
import signal
import subprocess
import sys
import threading
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal, TextIO

PROJECT_ROOT = Path(__file__).resolve().parents[3]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evmbench.utils import get_timestamp

Mode = Literal["detect", "patch", "exploit"]
Scope = Literal["smoke", "first5", "first20"]


@dataclass(frozen=True)
class RunnerSpec:
    slug: str
    agent_id: str
    label: str


@dataclass(frozen=True)
class Phase6Run:
    runner: str
    agent_id: str
    audit_id: str
    mode: Mode
    runs_dir: Path
    command: tuple[str, ...]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["runs_dir"] = str(self.runs_dir)
        payload["command"] = list(self.command)
        return payload


DEFAULT_RUNNERS: tuple[RunnerSpec, ...] = (
    RunnerSpec("codex-default", "codex-default", "Codex default"),
    RunnerSpec("modal-baseline", "mini-swe-agent-modal-baseline", "Modal single-agent baseline"),
    RunnerSpec("modal-forest", "mini-swe-agent-modal-forest", "Modal forest/TTS"),
)

AVAILABLE_RUNNERS: tuple[RunnerSpec, ...] = (
    RunnerSpec("codex-default", "codex-default", "Codex default"),
    RunnerSpec("mini-default", "mini-swe-agent-default", "mini-swe-agent local default"),
    RunnerSpec("mini-smoke-10", "mini-swe-agent-smoke-10", "mini-swe-agent local smoke"),
    RunnerSpec("mini-gpt-5-mini", "mini-swe-agent-gpt-5-mini", "mini-swe-agent local GPT-5 mini"),
    RunnerSpec("modal-baseline", "mini-swe-agent-modal-baseline", "Modal single-agent baseline"),
    RunnerSpec(
        "modal-baseline-smoke-10",
        "mini-swe-agent-modal-baseline-smoke-10",
        "Modal single-agent smoke",
    ),
    RunnerSpec("modal-forest", "mini-swe-agent-modal-forest", "Modal forest/TTS"),
    RunnerSpec("modal-forest-smoke", "mini-swe-agent-modal-forest-smoke", "Modal forest/TTS smoke"),
    RunnerSpec(
        "modal-forest-gpt52-codex-8trees",
        "mini-swe-agent-modal-forest-gpt-5.2-codex-8trees",
        "Modal forest/TTS GPT-5.2 Codex 8-tree",
    ),
    RunnerSpec(
        "modal-forest-gpt52-codex-2trees-debug",
        "mini-swe-agent-modal-forest-gpt-5.2-codex-2trees-debug",
        "Modal forest/TTS GPT-5.2 Codex 2-tree debug",
    ),
    RunnerSpec(
        "modal-forest-gpt52-codex-4trees-debug",
        "mini-swe-agent-modal-forest-gpt-5.2-codex-4trees-debug",
        "Modal forest/TTS GPT-5.2 Codex 4-tree debug",
    ),
    RunnerSpec(
        "modal-baseline-qwen-vllm",
        "mini-swe-agent-modal-baseline-qwen-vllm",
        "Modal baseline with Qwen vLLM",
    ),
    RunnerSpec(
        "modal-forest-qwen-vllm",
        "mini-swe-agent-modal-forest-qwen-vllm",
        "Modal forest/TTS with Qwen vLLM",
    ),
)

RUNNER_GROUPS: dict[str, tuple[RunnerSpec, ...]] = {
    "presentation": DEFAULT_RUNNERS,
    "default": DEFAULT_RUNNERS,
    "all": AVAILABLE_RUNNERS,
    "all-variants": AVAILABLE_RUNNERS,
    "local": (
        AVAILABLE_RUNNERS[1],
        AVAILABLE_RUNNERS[2],
        AVAILABLE_RUNNERS[3],
    ),
    "modal": (
        AVAILABLE_RUNNERS[4],
        AVAILABLE_RUNNERS[6],
    ),
    "smoke": (
        AVAILABLE_RUNNERS[0],
        AVAILABLE_RUNNERS[2],
        AVAILABLE_RUNNERS[5],
        AVAILABLE_RUNNERS[7],
    ),
    "modal-smoke": (
        AVAILABLE_RUNNERS[5],
        AVAILABLE_RUNNERS[7],
    ),
    "forest-debug": (
        AVAILABLE_RUNNERS[7],
        AVAILABLE_RUNNERS[9],
        AVAILABLE_RUNNERS[10],
    ),
    "modal-debug": (
        AVAILABLE_RUNNERS[5],
        AVAILABLE_RUNNERS[7],
        AVAILABLE_RUNNERS[9],
        AVAILABLE_RUNNERS[10],
    ),
    "vllm": (
        AVAILABLE_RUNNERS[11],
        AVAILABLE_RUNNERS[12],
    ),
    "modal-vllm": (
        AVAILABLE_RUNNERS[11],
        AVAILABLE_RUNNERS[12],
    ),
}


def project_root() -> Path:
    return PROJECT_ROOT


def default_output_root() -> Path:
    return project_root() / "runs" / "phase6" / get_timestamp()


def read_audits_for_mode(mode: Mode) -> list[str]:
    split_path = project_root() / "splits" / f"{mode}-tasks.txt"
    return [line.strip() for line in split_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def audits_for_scope(scope: Scope, mode: Mode = "detect") -> list[str]:
    audits = read_audits_for_mode(mode)
    if scope == "smoke":
        return audits[:1]
    if scope == "first5":
        return audits[:5]
    if scope == "first20":
        return audits[:20]
    raise ValueError(f"Unsupported Phase 6 scope: {scope!r}")


def parse_audit_list(raw: str | None, scope: Scope, mode: Mode = "detect") -> list[str]:
    if not raw:
        return audits_for_scope(scope, mode)
    audits = [part.strip() for part in raw.split(",") if part.strip()]
    if not audits:
        raise ValueError("--audits did not contain any audit IDs.")
    return audits


def _dedupe_runners(runners: list[RunnerSpec]) -> list[RunnerSpec]:
    seen: set[str] = set()
    deduped: list[RunnerSpec] = []
    for runner in runners:
        if runner.slug in seen:
            continue
        seen.add(runner.slug)
        deduped.append(runner)
    return deduped


def parse_runner_list(raw: str | None) -> list[RunnerSpec]:
    if not raw:
        return list(DEFAULT_RUNNERS)
    by_slug = {runner.slug: runner for runner in AVAILABLE_RUNNERS}
    by_agent_id = {runner.agent_id: runner for runner in AVAILABLE_RUNNERS}
    runners: list[RunnerSpec] = []
    for part in raw.split(","):
        key = part.strip()
        if not key:
            continue
        group = RUNNER_GROUPS.get(key)
        if group:
            runners.extend(group)
            continue
        runner = by_slug.get(key) or by_agent_id.get(key)
        if not runner:
            known = ", ".join(runner.slug for runner in AVAILABLE_RUNNERS)
            groups = ", ".join(sorted(RUNNER_GROUPS))
            raise ValueError(f"Unknown runner {key!r}. Known runners: {known}. Groups: {groups}.")
        runners.append(runner)
    if not runners:
        raise ValueError("--runners did not contain any known runners.")
    return _dedupe_runners(runners)


def build_evmbench_command(
    agent_id: str,
    audit_id: str,
    runs_dir: Path,
    mode: Mode = "detect",
) -> tuple[str, ...]:
    return (
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
    )


def build_run_matrix(
    *,
    output_root: Path,
    scope: Scope = "first5",
    mode: Mode = "detect",
    audits: list[str] | None = None,
    runners: list[RunnerSpec] | None = None,
) -> list[Phase6Run]:
    selected_audits = audits or audits_for_scope(scope, mode)
    selected_runners = runners or list(DEFAULT_RUNNERS)
    matrix: list[Phase6Run] = []
    for runner in selected_runners:
        runs_dir = output_root / runner.slug
        for audit_id in selected_audits:
            matrix.append(
                Phase6Run(
                    runner=runner.slug,
                    agent_id=runner.agent_id,
                    audit_id=audit_id,
                    mode=mode,
                    runs_dir=runs_dir,
                    command=build_evmbench_command(runner.agent_id, audit_id, runs_dir, mode),
                )
            )
    return matrix


def matrix_payload(output_root: Path, scope: Scope, matrix: list[Phase6Run]) -> dict[str, Any]:
    by_slug = {runner.slug: runner for runner in AVAILABLE_RUNNERS}
    selected_runners = []
    for item in matrix:
        selected_runners.append(
            by_slug.get(item.runner)
            or RunnerSpec(item.runner, item.agent_id, item.runner)
        )
    return {
        "created_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "scope": scope,
        "mode": matrix[0].mode if matrix else "detect",
        "output_root": str(output_root),
        "runners": [asdict(runner) for runner in _dedupe_runners(selected_runners)],
        "runner_catalog": [asdict(runner) for runner in AVAILABLE_RUNNERS],
        "runs": [item.to_dict() for item in matrix],
    }


def write_matrix(output_root: Path, scope: Scope, matrix: list[Phase6Run]) -> Path:
    output_root.mkdir(parents=True, exist_ok=True)
    path = output_root / "phase6-run-matrix.json"
    path.write_text(json.dumps(matrix_payload(output_root, scope, matrix), indent=2), encoding="utf-8")
    return path


def print_plan(matrix: list[Phase6Run]) -> None:
    for item in matrix:
        print(f"\n# {item.runner} / {item.audit_id}")
        print(shlex.join(item.command))


def print_variants() -> None:
    print("# Runner groups")
    for name, runners in sorted(RUNNER_GROUPS.items()):
        print(f"{name}: {', '.join(runner.slug for runner in runners)}")
    print("\n# Runner variants")
    print("slug\tagent_id\tlabel")
    for runner in AVAILABLE_RUNNERS:
        print(f"{runner.slug}\t{runner.agent_id}\t{runner.label}")


def command_log_dir(output_root: Path, item: Phase6Run) -> Path:
    return output_root / "_phase6_command_logs" / item.runner


def command_status_path(output_root: Path, item: Phase6Run) -> Path:
    filename = f"{item.audit_id}.json" if item.mode == "detect" else f"{item.mode}-{item.audit_id}.json"
    return command_log_dir(output_root, item) / filename


def submission_filename(mode: Mode) -> str:
    if mode == "detect":
        return "audit.md"
    if mode == "patch":
        return "agent.diff"
    if mode == "exploit":
        return "txs.json"
    raise ValueError(f"Unsupported mode: {mode!r}")


def parse_mode(raw: Any) -> Mode:
    if raw in {"detect", "patch", "exploit"}:
        return raw
    raise ValueError(f"Unsupported mode: {raw!r}")


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


def _phase6_log(message: str) -> None:
    print(f"[phase6] {message}", flush=True)


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
    item: Phase6Run,
    *,
    stdout_log: Path,
    stderr_log: Path,
    timeout_seconds: float | None,
) -> int:
    env = os.environ.copy()
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
                "prefix": f"[phase6][{item.runner}/{item.audit_id}][stdout] ",
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
                "prefix": f"[phase6][{item.runner}/{item.audit_id}][stderr] ",
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
                f"[phase6] command timed out after {_format_seconds(timeout_seconds)}; "
                "terminating process group.\n"
            )
            stderr_file.write(message)
            stderr_file.flush()
            with lock:
                sys.stderr.write(f"[phase6][{item.runner}/{item.audit_id}][stderr] {message}")
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


def _print_run_summary(output_root: Path, item: Phase6Run, status: dict[str, Any]) -> None:
    row = summarize_row(output_root, item)
    _phase6_log(
        "finished "
        f"{item.runner}/{item.audit_id}: "
        f"returncode={status['returncode']} "
        f"runtime={_format_seconds(_float_or_none(status.get('runtime_seconds')))}"
    )
    _phase6_log(f"command logs: {status['stdout_log']} | {status['stderr_log']}")
    if row.get("run_dir"):
        _phase6_log(f"run dir: {row['run_dir']}")
    if row.get("failure_reason"):
        _phase6_log(f"failure: {row['failure_reason']}")
    elif row.get("score") is not None or row.get("max_score") is not None:
        _phase6_log(
            "score: "
            f"{_fmt(row.get('score'))}/{_fmt(row.get('max_score'))} "
            f"detect_award={_fmt(row.get('detect_award'))}/{_fmt(row.get('detect_max_award'))}"
        )

    selected_roles = row.get("selected_roles") or []
    forest_workers = row.get("forest_workers") or []
    if selected_roles:
        _phase6_log(f"forest roles: {', '.join(str(role) for role in selected_roles)}")
    for worker in forest_workers:
        if not isinstance(worker, dict):
            continue
        error = worker.get("error")
        worker_status = "error" if error else "ok"
        details = [
            f"name={worker.get('worker_name')}",
            f"type={worker.get('worker_type')}",
            f"role={worker.get('role') or '-'}",
            f"branch={worker.get('branch') or '-'}",
            f"runtime={_format_seconds(_float_or_none(worker.get('runtime_seconds')))}",
            f"status={worker_status}",
            f"traj={worker.get('trajectory_path')}",
        ]
        _phase6_log("forest worker: " + " ".join(details))
        if error:
            _phase6_log(f"forest worker error ({worker.get('worker_name')}): {error}")


def run_matrix(
    output_root: Path,
    scope: Scope,
    matrix: list[Phase6Run],
    *,
    stop_on_failure: bool,
    item_timeout_seconds: float | None,
) -> int:
    write_matrix(output_root, scope, matrix)
    overall_returncode = 0
    _phase6_log(f"matrix: {len(matrix)} run(s), scope={scope}, output_root={output_root}")
    for index, item in enumerate(matrix, start=1):
        log_dir = command_log_dir(output_root, item)
        log_dir.mkdir(parents=True, exist_ok=True)
        stdout_log = log_dir / f"{item.audit_id}.stdout.log"
        stderr_log = log_dir / f"{item.audit_id}.stderr.log"
        _phase6_log(
            f"starting {index}/{len(matrix)} {item.runner}/{item.audit_id} mode={item.mode} "
            f"agent={item.agent_id}"
        )
        _phase6_log(f"command: {shlex.join(item.command)}")
        _phase6_log(f"streaming logs to: {stdout_log} | {stderr_log}")
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
        command_status_path(output_root, item).write_text(json.dumps(status, indent=2), encoding="utf-8")
        _print_run_summary(output_root, item, status)
        if returncode != 0:
            overall_returncode = returncode
            if stop_on_failure:
                _phase6_log("stop-on-failure requested; stopping matrix early.")
                break
    payload = summarize_phase6(output_root, matrix)
    _phase6_log(f"summary: {output_root / 'phase6-summary.md'}")
    _phase6_log(f"results: {output_root / 'phase6-results.json'}")
    _phase6_log(f"slide data: {payload['slide_data_path']} | {payload['slide_data_csv_path']}")
    for runner, bucket in sorted(payload["aggregate"].items()):
        _phase6_log(
            f"aggregate {runner}: submissions={bucket['n_successful_submissions']}/{bucket['n_rows']} "
            f"failures={bucket['n_failures']} "
            f"score={bucket['score']:.2f}/{bucket['max_score']:.2f} "
            f"detect_award={bucket['detect_award']:.2f}/{bucket['detect_max_award']:.2f}"
        )
    return overall_returncode


def _read_json(path: Path) -> dict[str, Any] | None:
    if not path.exists():
        return None
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None
    return value if isinstance(value, dict) else None


def _load_matrix(output_root: Path) -> list[Phase6Run] | None:
    payload = _read_json(output_root / "phase6-run-matrix.json")
    if not payload:
        return None
    runs = payload.get("runs")
    if not isinstance(runs, list):
        return None
    matrix: list[Phase6Run] = []
    for raw in runs:
        if not isinstance(raw, dict):
            continue
        try:
            matrix.append(
                Phase6Run(
                    runner=str(raw["runner"]),
                    agent_id=str(raw["agent_id"]),
                    audit_id=str(raw["audit_id"]),
                    mode=parse_mode(raw.get("mode", "detect")),
                    runs_dir=Path(str(raw["runs_dir"])),
                    command=tuple(str(part) for part in raw["command"]),
                )
            )
        except (KeyError, TypeError):
            continue
    return matrix


def _audit_from_run_dir(run_dir: Path) -> str:
    return run_dir.name.split("_", 1)[0]


def discover_matrix(output_root: Path) -> list[Phase6Run]:
    matrix: list[Phase6Run] = []
    for runner_dir in sorted(output_root.iterdir() if output_root.exists() else []):
        if not runner_dir.is_dir() or runner_dir.name.startswith("_"):
            continue
        for run_log in sorted(runner_dir.rglob("run.log")):
            run_dir = run_log.parent
            audit_id = _audit_from_run_dir(run_dir)
            matrix.append(
                Phase6Run(
                    runner=runner_dir.name,
                    agent_id=runner_dir.name,
                    audit_id=audit_id,
                    mode="detect",
                    runs_dir=runner_dir,
                    command=(),
                )
            )
    return matrix


def find_run_dir(item: Phase6Run) -> Path | None:
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


def _modal_metadata(run_dir: Path | None) -> tuple[dict[str, Any] | None, dict[str, Any] | None, dict[str, Any] | None]:
    if not run_dir:
        return None, None, None
    modal_logs = run_dir / "modal" / "logs"
    return (
        _read_json(modal_logs / "modal-runner-command.json"),
        _read_json(modal_logs / "modal-baseline-result.json"),
        _read_json(modal_logs / "modal-forest-result.json"),
    )


def _command_status(output_root: Path, item: Phase6Run) -> dict[str, Any] | None:
    return _read_json(command_status_path(output_root, item))


def summarize_row(output_root: Path, item: Phase6Run) -> dict[str, Any]:
    run_dir = find_run_dir(item)
    grade = parse_run_grade(run_dir)
    command_status = _command_status(output_root, item)
    submission_path = run_dir / "submission" / submission_filename(item.mode) if run_dir else None
    submission_exists = bool(submission_path and submission_path.exists() and submission_path.stat().st_size > 0)
    modal_command, modal_baseline, modal_forest = _modal_metadata(run_dir)

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

    forest_workers = []
    forest_worker_errors = []
    selected_roles = []
    if modal_forest:
        raw_roles = modal_forest.get("selected_roles")
        if isinstance(raw_roles, list):
            selected_roles = raw_roles
        raw_workers = modal_forest.get("workers")
        if isinstance(raw_workers, list):
            for worker in raw_workers:
                if isinstance(worker, dict):
                    forest_workers.append(
                        {
                            "worker_name": worker.get("worker_name"),
                            "worker_type": worker.get("worker_type"),
                            "role": worker.get("role"),
                            "branch": worker.get("branch"),
                            "runtime_seconds": worker.get("runtime_seconds"),
                            "error": worker.get("error"),
                            "trajectory_path": worker.get("trajectory_path"),
                        }
                    )
                    if worker.get("error"):
                        forest_worker_errors.append(
                            {
                                "worker_name": worker.get("worker_name"),
                                "error": worker.get("error"),
                            }
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

    return {
        "runner": item.runner,
        "agent_id": item.agent_id,
        "audit_id": item.audit_id,
        "mode": item.mode,
        "runs_dir": str(item.runs_dir),
        "run_dir": str(run_dir) if run_dir else None,
        "command": list(item.command),
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
        "modal_command": modal_command,
        "modal_baseline": modal_baseline,
        "modal_forest": modal_forest,
        "selected_roles": selected_roles,
        "forest_workers": forest_workers,
        "forest_worker_errors": forest_worker_errors,
        "failure_reason": failure_reason,
    }


def aggregate_rows(rows: list[dict[str, Any]]) -> dict[str, Any]:
    by_runner: dict[str, dict[str, Any]] = {}
    for row in rows:
        runner = str(row["runner"])
        bucket = by_runner.setdefault(
            runner,
            {
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
        for key in ("score", "max_score", "detect_award", "detect_max_award"):
            value = _float_or_none(row.get(key))
            if value is not None:
                bucket[key] += value

    for bucket in by_runner.values():
        bucket["score_percentage"] = _percentage(bucket["score"], bucket["max_score"])
        bucket["detect_award_percentage"] = _percentage(bucket["detect_award"], bucket["detect_max_award"])
    return by_runner


def _fmt(value: Any) -> str:
    if value is None:
        return "-"
    if isinstance(value, float):
        return f"{value:.2f}"
    return str(value)


def render_markdown(output_root: Path, rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> str:
    lines = [
        "# Phase 6 Summary",
        "",
        f"Output root: `{output_root}`",
        "",
        "## Aggregate",
        "",
        "| Runner | Rows | Submissions | Failures | Score | Detect Award |",
        "| --- | ---: | ---: | ---: | ---: | ---: |",
    ]
    for runner, bucket in sorted(aggregate.items()):
        lines.append(
            "| "
            + " | ".join(
                [
                    runner,
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
            "## Per Audit",
            "",
            "| Runner | Mode | Audit | Submission | Score | Detect Award | Runtime | Failure | Run Dir |",
            "| --- | --- | --- | --- | ---: | ---: | ---: | --- | --- |",
        ]
    )
    for row in rows:
        score = (
            f"{_fmt(row.get('score'))}/{_fmt(row.get('max_score'))} ({_fmt(row.get('score_percentage'))}%)"
            if row.get("score") is not None or row.get("max_score") is not None
            else "-"
        )
        award = (
            f"{_fmt(row.get('detect_award'))}/{_fmt(row.get('detect_max_award'))} ({_fmt(row.get('detect_award_percentage'))}%)"
            if row.get("detect_award") is not None or row.get("detect_max_award") is not None
            else "-"
        )
        lines.append(
            "| "
            + " | ".join(
                [
                    str(row["runner"]),
                    str(row.get("mode", "detect")),
                    str(row["audit_id"]),
                    "yes" if row.get("submission_exists") else "no",
                    score,
                    award,
                    _fmt(row.get("agent_runtime_seconds")),
                    str(row.get("failure_reason") or ""),
                    f"`{row.get('run_dir')}`" if row.get("run_dir") else "",
                ]
            )
            + " |"
        )

    forest_rows = [row for row in rows if row.get("selected_roles") or row.get("forest_worker_errors")]
    if forest_rows:
        lines.extend(["", "## Forest Details", ""])
        for row in forest_rows:
            lines.append(f"- `{row['audit_id']}` roles: {', '.join(str(role) for role in row.get('selected_roles', [])) or '-'}")
            for error in row.get("forest_worker_errors", []):
                lines.append(f"  - `{error.get('worker_name')}`: {error.get('error')}")

    lines.append("")
    return "\n".join(lines)


def _sum_numbers(values: list[Any]) -> float:
    total = 0.0
    for value in values:
        parsed = _float_or_none(value)
        if parsed is not None:
            total += parsed
    return total


def _avg_numbers(values: list[Any]) -> float | None:
    parsed_values = [_float_or_none(value) for value in values]
    numbers = [value for value in parsed_values if value is not None]
    if not numbers:
        return None
    return sum(numbers) / len(numbers)


def _slide_runner_summary(rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> list[dict[str, Any]]:
    summary: list[dict[str, Any]] = []
    for runner, bucket in sorted(aggregate.items()):
        runner_rows = [row for row in rows if row.get("runner") == runner]
        summary.append(
            {
                "runner": runner,
                "rows": bucket["n_rows"],
                "successful_submissions": bucket["n_successful_submissions"],
                "failures": bucket["n_failures"],
                "score": bucket["score"],
                "max_score": bucket["max_score"],
                "score_percentage": bucket["score_percentage"],
                "detect_award": bucket["detect_award"],
                "detect_max_award": bucket["detect_max_award"],
                "detect_award_percentage": bucket["detect_award_percentage"],
                "total_runtime_seconds": _sum_numbers([row.get("agent_runtime_seconds") for row in runner_rows]),
                "average_runtime_seconds": _avg_numbers([row.get("agent_runtime_seconds") for row in runner_rows]),
            }
        )
    return summary


def _slide_audit_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [
        {
            "runner": row.get("runner"),
            "agent_id": row.get("agent_id"),
            "mode": row.get("mode", "detect"),
            "audit_id": row.get("audit_id"),
            "submission_exists": row.get("submission_exists"),
            "score": row.get("score"),
            "max_score": row.get("max_score"),
            "score_percentage": row.get("score_percentage"),
            "detect_award": row.get("detect_award"),
            "detect_max_award": row.get("detect_max_award"),
            "detect_award_percentage": row.get("detect_award_percentage"),
            "runtime_seconds": row.get("agent_runtime_seconds"),
            "failure_reason": row.get("failure_reason"),
            "run_dir": row.get("run_dir"),
        }
        for row in rows
    ]


def _slide_forest_rows(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    forest_rows: list[dict[str, Any]] = []
    for row in rows:
        workers = row.get("forest_workers")
        selected_roles = row.get("selected_roles")
        if not workers and not selected_roles:
            continue
        worker_list = workers if isinstance(workers, list) else []
        worker_runtimes = [worker.get("runtime_seconds") for worker in worker_list if isinstance(worker, dict)]
        worker_errors = row.get("forest_worker_errors")
        error_list = worker_errors if isinstance(worker_errors, list) else []
        forest_rows.append(
            {
                "runner": row.get("runner"),
                "audit_id": row.get("audit_id"),
                "selected_roles": selected_roles or [],
                "worker_count": len(worker_list),
                "worker_error_count": len(error_list),
                "worker_total_runtime_seconds": _sum_numbers(worker_runtimes),
                "worker_average_runtime_seconds": _avg_numbers(worker_runtimes),
                "workers": worker_list,
                "worker_errors": error_list,
            }
        )
    return forest_rows


def build_slide_data(output_root: Path, rows: list[dict[str, Any]], aggregate: dict[str, Any]) -> dict[str, Any]:
    return {
        "output_root": str(output_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "runner_summary": _slide_runner_summary(rows, aggregate),
        "per_audit": _slide_audit_rows(rows),
        "forest": _slide_forest_rows(rows),
    }


def write_slide_data(output_root: Path, slide_data: dict[str, Any]) -> None:
    (output_root / "phase6-slide-data.json").write_text(
        json.dumps(slide_data, indent=2, default=str),
        encoding="utf-8",
    )
    csv_path = output_root / "phase6-slide-data.csv"
    fieldnames = [
        "runner",
        "agent_id",
        "audit_id",
        "submission_exists",
        "mode",
        "score",
        "max_score",
        "score_percentage",
        "detect_award",
        "detect_max_award",
        "detect_award_percentage",
        "runtime_seconds",
        "failure_reason",
        "run_dir",
    ]
    with csv_path.open("w", encoding="utf-8", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fieldnames)
        writer.writeheader()
        writer.writerows(slide_data["per_audit"])


def summarize_phase6(output_root: Path, matrix: list[Phase6Run] | None = None) -> dict[str, Any]:
    output_root = output_root.resolve()
    matrix = matrix or _load_matrix(output_root) or discover_matrix(output_root)
    rows = [summarize_row(output_root, item) for item in matrix]
    aggregate = aggregate_rows(rows)
    slide_data = build_slide_data(output_root, rows, aggregate)
    payload = {
        "output_root": str(output_root),
        "generated_at": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
        "rows": rows,
        "aggregate": aggregate,
        "slide_data_path": str(output_root / "phase6-slide-data.json"),
        "slide_data_csv_path": str(output_root / "phase6-slide-data.csv"),
    }
    output_root.mkdir(parents=True, exist_ok=True)
    (output_root / "phase6-results.json").write_text(json.dumps(payload, indent=2, default=str), encoding="utf-8")
    (output_root / "phase6-summary.md").write_text(render_markdown(output_root, rows, aggregate), encoding="utf-8")
    write_slide_data(output_root, slide_data)
    return payload


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)

    def add_matrix_args(subparser: argparse.ArgumentParser) -> None:
        subparser.add_argument("--mode", choices=["detect", "patch", "exploit"], default="detect")
        subparser.add_argument("--scope", choices=["smoke", "first5", "first20"], default="first5")
        subparser.add_argument("--audits", help="Comma-separated audit IDs. Overrides --scope.")
        subparser.add_argument(
            "--runners",
            default="presentation",
            help=(
                "Comma-separated runner slugs, agent IDs, or groups. "
                "Groups: presentation, smoke, local, modal, modal-smoke, all."
            ),
        )
        subparser.add_argument("--output-root", type=Path, default=None)

    subparsers.add_parser("variants", help="List runnable Phase 6 runner variants and groups.")

    plan_parser = subparsers.add_parser("plan", help="Print the Phase 6 command matrix.")
    add_matrix_args(plan_parser)

    run_parser = subparsers.add_parser("run", help="Run the Phase 6 command matrix.")
    add_matrix_args(run_parser)
    run_parser.add_argument("--stop-on-failure", action="store_true")
    run_parser.add_argument(
        "--item-timeout-seconds",
        type=float,
        default=float(os.getenv("PHASE6_ITEM_TIMEOUT_SECONDS", "0") or "0"),
        help="Wall-clock timeout per matrix item. Use 0 to disable.",
    )

    summarize_parser = subparsers.add_parser("summarize", help="Summarize an existing Phase 6 output root.")
    summarize_parser.add_argument("--output-root", type=Path, required=True)
    return parser


def _matrix_from_args(args: argparse.Namespace) -> tuple[Path, Scope, list[Phase6Run]]:
    scope = args.scope
    mode = parse_mode(args.mode)
    output_root = (args.output_root or default_output_root()).resolve()
    audits = parse_audit_list(args.audits, scope, mode)
    runners = parse_runner_list(args.runners)
    return output_root, scope, build_run_matrix(
        output_root=output_root,
        scope=scope,
        mode=mode,
        audits=audits,
        runners=runners,
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    if args.command == "variants":
        print_variants()
        return 0
    if args.command == "plan":
        output_root, scope, matrix = _matrix_from_args(args)
        print(f"# Phase 6 output root: {output_root}")
        print(f"# Scope: {scope}")
        print(f"# Mode: {args.mode}")
        print_plan(matrix)
        return 0
    if args.command == "run":
        output_root, scope, matrix = _matrix_from_args(args)
        print(f"Writing Phase 6 outputs to {output_root}")
        item_timeout_seconds = args.item_timeout_seconds if args.item_timeout_seconds > 0 else None
        return run_matrix(
            output_root,
            scope,
            matrix,
            stop_on_failure=args.stop_on_failure,
            item_timeout_seconds=item_timeout_seconds,
        )
    if args.command == "summarize":
        payload = summarize_phase6(args.output_root)
        print(json.dumps({"output_root": payload["output_root"], "n_rows": len(payload["rows"])}, indent=2))
        return 0
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
