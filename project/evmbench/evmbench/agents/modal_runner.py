from __future__ import annotations

import json
import os
import subprocess
import sys
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any, TextIO

from evmbench.agents.agent import Agent


MODAL_RUNNER_COMMANDS = {
    "modal_baseline": "baseline",
    "modal_forest": "forest",
}


@dataclass(frozen=True)
class ModalRunnerInvocation:
    command: list[str]
    output_dir: Path
    submission_path: Path
    runner_name: str


@dataclass(frozen=True)
class ModalRunnerResult:
    invocation: ModalRunnerInvocation
    stdout: str
    stderr: str
    returncode: int


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _entrypoint_path() -> Path:
    return Path(__file__).resolve().parent / "mini-swe-agent" / "entrypoint.py"


def _append_env_flag(command: list[str], env: dict[str, str], flag: str, *names: str) -> None:
    for name in names:
        value = env.get(name)
        if value is not None and value.strip():
            command.extend([flag, value])
            return


def _append_bool_env_flag(
    command: list[str],
    env: dict[str, str],
    enabled_flag: str,
    disabled_flag: str,
    *names: str,
) -> None:
    for name in names:
        value = env.get(name)
        if value is None or not value.strip():
            continue
        normalized = value.strip().lower()
        if normalized in {"1", "true", "yes", "on"}:
            command.append(enabled_flag)
        elif normalized in {"0", "false", "no", "off"}:
            command.append(disabled_flag)
        else:
            raise ValueError(f"{name} must be a boolean value, got {value!r}.")
        return


def _env_truthy(env: dict[str, str], name: str) -> bool:
    return env.get(name, "").strip().lower() in {"1", "true", "yes", "on"}


def _append_common_modal_flags(command: list[str], env: dict[str, str]) -> None:
    _append_env_flag(command, env, "--model", "MODEL")
    _append_env_flag(command, env, "--command-timeout", "MODAL_COMMAND_TIMEOUT", "COMMAND_TIMEOUT")
    _append_env_flag(command, env, "--startup-timeout", "MODAL_STARTUP_TIMEOUT", "STARTUP_TIMEOUT")
    _append_env_flag(command, env, "--runtime-timeout", "MODAL_RUNTIME_TIMEOUT", "RUNTIME_TIMEOUT")
    _append_env_flag(command, env, "--deployment-timeout", "MODAL_DEPLOYMENT_TIMEOUT", "DEPLOYMENT_TIMEOUT")
    _append_env_flag(command, env, "--model-kwargs-json", "MODEL_KWARGS_JSON")
    _append_env_flag(command, env, "--modal-sandbox-kwargs-json", "MODAL_SANDBOX_KWARGS_JSON")
    _append_env_flag(command, env, "--cost-tracking", "MSWEA_COST_TRACKING")
    _append_env_flag(command, env, "--task", "MODAL_TASK", "TASK")
    _append_bool_env_flag(command, env, "--install-pipx", "--no-install-pipx", "INSTALL_PIPX")


def _modal_image_for_task(env: dict[str, str], task: Any) -> str:
    image = env.get("MODAL_AUDIT_IMAGE", "").strip()
    if image and not image.startswith("${{"):
        return image

    image_repo = env.get("MODAL_AUDIT_IMAGE_REPO", "").strip()
    if image_repo and not image_repo.startswith("${{"):
        return f"{image_repo}:{task.audit.id}"

    return task.docker_image


def build_modal_runner_invocation(
    agent: Agent,
    task: Any,
    output_dir: Path,
    *,
    python_executable: str | None = None,
) -> ModalRunnerInvocation:
    if agent.runner not in MODAL_RUNNER_COMMANDS:
        raise ValueError(f"Agent {agent.id!r} is not a Modal runner: {agent.runner!r}.")
    if task.mode != "detect":
        raise RuntimeError(
            f"Agent {agent.id!r} uses {agent.runner}, which currently supports detect mode only "
            f"(got {task.mode!r})."
        )

    env = dict(agent.env_vars or {})
    for name in ("MODAL_AUDIT_IMAGE", "MODAL_AUDIT_IMAGE_REPO"):
        if name not in env and os.environ.get(name):
            env[name] = os.environ[name]
    runner_name = MODAL_RUNNER_COMMANDS[agent.runner]
    output_dir = Path(output_dir)
    command = [
        python_executable or sys.executable,
        str(_entrypoint_path()),
        runner_name,
        "--audit-id",
        task.audit.id,
        "--mode",
        task.mode,
        "--hint-level",
        task.hint_level,
        "--findings-subdir",
        getattr(task.audit, "findings_subdir", ""),
        "--image",
        _modal_image_for_task(env, task),
        "--output-dir",
        str(output_dir),
    ]

    _append_common_modal_flags(command, env)

    if agent.runner == "modal_baseline":
        _append_env_flag(command, env, "--step-limit", "STEP_LIMIT")
        _append_env_flag(command, env, "--cost-limit", "COST_LIMIT")
        _append_env_flag(command, env, "--modal-secret-name", "MODAL_OPENAI_SECRET_NAME")
        _append_env_flag(command, env, "--judge-model", "JUDGE_MODEL")
        _append_env_flag(command, env, "--judge-reasoning-effort", "JUDGE_REASONING_EFFORT")
        command.append("--no-grade")
    elif agent.runner == "modal_forest":
        _append_env_flag(command, env, "--scout-model", "SCOUT_MODEL")
        _append_env_flag(command, env, "--branch-model", "BRANCH_MODEL")
        _append_env_flag(command, env, "--judge-model", "JUDGE_MODEL")
        _append_env_flag(command, env, "--global-model", "GLOBAL_MODEL")
        _append_env_flag(command, env, "--scout-step-limit", "SCOUT_STEP_LIMIT")
        _append_env_flag(command, env, "--scout-cost-limit", "SCOUT_COST_LIMIT")
        _append_env_flag(command, env, "--branch-step-limit", "BRANCH_STEP_LIMIT")
        _append_env_flag(command, env, "--branch-cost-limit", "BRANCH_COST_LIMIT")
        _append_env_flag(command, env, "--judge-step-limit", "JUDGE_STEP_LIMIT")
        _append_env_flag(command, env, "--judge-cost-limit", "JUDGE_COST_LIMIT")
        _append_env_flag(command, env, "--global-step-limit", "GLOBAL_STEP_LIMIT")
        _append_env_flag(command, env, "--global-cost-limit", "GLOBAL_COST_LIMIT")
        _append_env_flag(command, env, "--branches-per-tree", "BRANCHES_PER_TREE")
        _append_env_flag(command, env, "--max-tree-roles", "MAX_TREE_ROLES")
        _append_env_flag(command, env, "--tree-roles", "TREE_ROLES")
        _append_env_flag(command, env, "--worker-concurrency", "FOREST_WORKER_CONCURRENCY")
        for name in ("CONTINUE_ON_WORKER_ERROR", "FOREST_CONTINUE_ON_WORKER_ERROR"):
            value = env.get(name)
            if value and value.strip().lower() in {"1", "true", "yes", "on"}:
                command.append("--continue-on-worker-error")
                break

    return ModalRunnerInvocation(
        command=command,
        output_dir=output_dir,
        submission_path=output_dir / "submission" / "audit.md",
        runner_name=runner_name,
    )


def modal_runner_environment(agent: Agent) -> dict[str, str]:
    env = os.environ.copy()
    if agent.env_vars:
        env.update(agent.env_vars)
    env.setdefault("PYTHONUNBUFFERED", "1")

    openai_api_key = env.get("OPENAI_API_KEY", "")
    if not openai_api_key or openai_api_key.startswith("${{"):
        raise RuntimeError(
            f"Agent {agent.id!r} uses {agent.runner}, but OPENAI_API_KEY is not set on the host. "
            "Set OPENAI_API_KEY or provide it through the agent config secret placeholder."
        )
    return env


def _stream_pipe(
    pipe: TextIO,
    *,
    log_file: TextIO,
    terminal: TextIO,
    prefix: str,
    chunks: list[str],
    lock: threading.Lock,
) -> None:
    try:
        for line in iter(pipe.readline, ""):
            chunks.append(line)
            log_file.write(line)
            log_file.flush()
            with lock:
                terminal.write(f"{prefix}{line}")
                terminal.flush()
    finally:
        pipe.close()


def _run_modal_entrypoint_streaming(
    invocation: ModalRunnerInvocation,
    *,
    env: dict[str, str],
    logs_dir: Path,
) -> tuple[str, str, int]:
    stdout_chunks: list[str] = []
    stderr_chunks: list[str] = []
    stdout_log = logs_dir / "modal-runner.stdout.log"
    stderr_log = logs_dir / "modal-runner.stderr.log"
    with stdout_log.open("w", encoding="utf-8", buffering=1) as stdout_file, stderr_log.open(
        "w",
        encoding="utf-8",
        buffering=1,
    ) as stderr_file:
        process = subprocess.Popen(
            invocation.command,
            cwd=_project_root(),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            bufsize=1,
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
                "prefix": f"[modal-runner][{invocation.runner_name}][stdout] ",
                "chunks": stdout_chunks,
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
                "prefix": f"[modal-runner][{invocation.runner_name}][stderr] ",
                "chunks": stderr_chunks,
                "lock": lock,
            },
            daemon=True,
        )
        stdout_thread.start()
        stderr_thread.start()
        try:
            returncode = process.wait()
        except KeyboardInterrupt:
            process.terminate()
            try:
                process.wait(timeout=10)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait()
            raise
        finally:
            stdout_thread.join()
            stderr_thread.join()
    return "".join(stdout_chunks), "".join(stderr_chunks), returncode


def _write_smoke_fallback_submission(agent: Agent, result: ModalRunnerResult) -> None:
    result.invocation.submission_path.parent.mkdir(parents=True, exist_ok=True)
    result.invocation.submission_path.write_text(
        "\n".join(
            [
                "# EVMBench Modal Integration Smoke",
                "",
                "This placeholder report was written by the EVMBench Phase 5 adapter.",
                "The capped Modal runner completed successfully but reached its smoke budget before producing /home/agent/submission/audit.md.",
                "",
                f"- Agent: `{agent.id}`",
                f"- Runner: `{agent.runner}`",
                f"- Modal command: `{' '.join(result.invocation.command)}`",
                "",
                "This artifact is only valid for smoke variants that set MODAL_ALLOW_SMOKE_FALLBACK_SUBMISSION=1.",
            ]
        )
        + "\n",
        encoding="utf-8",
    )


def run_modal_runner(agent: Agent, task: Any, output_dir: Path) -> ModalRunnerResult:
    invocation = build_modal_runner_invocation(agent, task, output_dir)
    env = modal_runner_environment(agent)
    invocation.output_dir.mkdir(parents=True, exist_ok=True)
    logs_dir = invocation.output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    command_payload = {
        "agent_id": agent.id,
        "runner": agent.runner,
        "command": invocation.command,
        "output_dir": str(invocation.output_dir),
        "submission_path": str(invocation.submission_path),
    }
    (logs_dir / "modal-runner-command.json").write_text(
        json.dumps(command_payload, indent=2),
        encoding="utf-8",
    )
    print(
        f"[modal-runner] starting {agent.runner} for {task.audit.id}; output_dir={invocation.output_dir}",
        flush=True,
    )
    print(f"[modal-runner] command: {' '.join(invocation.command)}", flush=True)

    stdout, stderr, returncode = _run_modal_entrypoint_streaming(
        invocation,
        env=env,
        logs_dir=logs_dir,
    )

    result = ModalRunnerResult(
        invocation=invocation,
        stdout=stdout,
        stderr=stderr,
        returncode=returncode,
    )
    print(
        f"[modal-runner] finished {agent.runner} for {task.audit.id}; returncode={returncode}",
        flush=True,
    )
    if returncode != 0:
        raise RuntimeError(
            f"Modal runner {agent.runner} failed with exit code {returncode}. "
            f"Logs: {logs_dir}\n\nSTDOUT tail:\n{stdout[-4000:]}\n\n"
            f"STDERR tail:\n{stderr[-4000:]}"
        )
    if not invocation.submission_path.exists() and _env_truthy(env, "MODAL_ALLOW_SMOKE_FALLBACK_SUBMISSION"):
        _write_smoke_fallback_submission(agent, result)
    if not invocation.submission_path.exists():
        raise RuntimeError(
            f"Modal runner {agent.runner} completed but did not produce "
            f"{invocation.submission_path}."
        )
    return result
