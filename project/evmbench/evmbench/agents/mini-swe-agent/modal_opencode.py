#!/usr/bin/env python3
"""Run an OpenCode EVMBench agent inside a Modal sandbox.

This runner reuses the Modal sandbox setup from `modal_baseline.py`, but it does
not run mini-swe-agent locally. Instead it stages the configured OpenCode
`start.sh` into the audit image and executes it directly inside the Modal
sandbox, matching the normal EVMBench container-runner contract.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shlex
import sys
import time
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Literal

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")

from modal_baseline import (
    DEFAULT_MODAL_SECRET_NAME,
    GRADE_OUTPUT_FILENAME,
    HintLevel,
    Mode,
    ReasoningEffort,
    RemoteCommandError,
    _build_modal_sandbox_kwargs,
    _default_output_dir,
    _extract_remote_outputs,
    _grade_detect_output,
    _load_audit_for_mode,
    _load_mini_classes,
    _parse_json_object,
    _postprocess_mode,
    _prepare_mode,
    _prepare_remote_workspace,
    _remote_read_text,
    _remote_workspace_env,
    _remote_write_bytes,
    _run_remote,
    _stage_rendered_instructions,
)

from evmbench.agents.agent import agent_registry
from evmbench.constants import AGENT_DIR, AUDIT_DIR, LOGS_DIR

FindingsSubdir = Literal["", "low", "medium", "high"]


@dataclass(frozen=True)
class ModalOpencodeConfig:
    audit_id: str
    mode: Mode
    hint_level: HintLevel
    findings_subdir: FindingsSubdir
    image: str
    agent_id: str
    model: str
    command_timeout: int
    startup_timeout: float
    runtime_timeout: float
    deployment_timeout: float
    install_pipx: bool
    grade: bool
    dry_run: bool
    modal_secret_name: str | None
    judge_model: str
    judge_reasoning_effort: ReasoningEffort
    output_dir: Path
    model_kwargs: dict[str, Any]
    modal_sandbox_kwargs: dict[str, Any]
    cost_tracking: Literal["default", "ignore_errors"]
    task: str
    agent_env: dict[str, str]


def _redact_env(env: dict[str, str]) -> dict[str, str]:
    redacted: dict[str, str] = {}
    for key, value in sorted(env.items()):
        if any(token in key for token in ("KEY", "TOKEN", "SECRET")):
            redacted[key] = f"<redacted length={len(value)}>"
        else:
            redacted[key] = value
    return redacted


def _local_metadata_path(output_dir: Path, path: Path) -> str:
    try:
        return str(path.relative_to(output_dir))
    except ValueError:
        return str(path)


def _sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _remote_opencode_env(config: ModalOpencodeConfig) -> dict[str, str]:
    env = _remote_workspace_env(None)
    env.update(config.agent_env)
    env["MODEL"] = config.model
    if config.dry_run:
        env["OPENCODE_DRY_RUN"] = "1"
    return env


def _stage_start_script(env: Any, start_sh: str) -> None:
    _remote_write_bytes(
        env,
        f"{AGENT_DIR}/start.sh",
        Path(start_sh).read_bytes(),
        "stage OpenCode start.sh",
    )
    _run_remote(env, f"chmod +x {shlex.quote(AGENT_DIR + '/start.sh')}", "chmod OpenCode start.sh")


def _extract_opencode_config(env: Any, output_dir: Path) -> None:
    rendered = _remote_read_text(
        env,
        f"{AGENT_DIR}/opencode.json",
        "extract generated opencode.json",
        required=False,
    )
    if rendered is None:
        return
    config_path = output_dir / "agent" / "opencode.json"
    config_path.parent.mkdir(parents=True, exist_ok=True)
    config_path.write_text(rendered, encoding="utf-8")


def _bash(command: str) -> str:
    return f"bash -lc {shlex.quote(command)}"


def _run_opencode_start_sh(env: Any, config: ModalOpencodeConfig) -> dict[str, Any]:
    """Run start.sh without holding one long SWE-ReX HTTP request open.

    SWE-ReX forwards `execute` through aiohttp without applying the configured
    Modal runtime timeout, so a single long command hits aiohttp's default
    300-second request timeout. Launching the agent in the sandbox and polling
    with short commands keeps longer bounded OpenCode runs observable.
    """

    opencode_logs_dir = f"{LOGS_DIR}/opencode"
    pid_path = f"{opencode_logs_dir}/start.pid"
    exit_path = f"{opencode_logs_dir}/start.exit"
    done_path = f"{opencode_logs_dir}/start.done"
    wrapper_log_path = f"{opencode_logs_dir}/start-wrapper.log"
    start_script = f"{AGENT_DIR}/start.sh"

    launch_script = f"""
set -euo pipefail
mkdir -p {shlex.quote(opencode_logs_dir)}
rm -f {shlex.quote(pid_path)} {shlex.quote(exit_path)} {shlex.quote(done_path)} {shlex.quote(wrapper_log_path)}
setsid bash -lc {shlex.quote(f'bash {shlex.quote(start_script)}; code=$?; echo "$code" > {shlex.quote(exit_path)}; touch {shlex.quote(done_path)}; exit "$code"')} >> {shlex.quote(wrapper_log_path)} 2>&1 &
pid=$!
echo "$pid" > {shlex.quote(pid_path)}
echo "started pid=$pid"
""".strip()
    _run_remote(env, _bash(launch_script), "launch OpenCode start.sh", timeout=30)

    deadline = time.monotonic() + config.command_timeout
    poll_script = f"""
set -euo pipefail
if [ -f {shlex.quote(done_path)} ]; then
  code="$(cat {shlex.quote(exit_path)} 2>/dev/null || printf '0')"
  printf 'done:%s\\n' "$code"
  exit 0
fi
if [ -f {shlex.quote(pid_path)} ] && kill -0 "$(cat {shlex.quote(pid_path)})" 2>/dev/null; then
  echo "running"
  exit 0
fi
echo "missing"
exit 4
""".strip()
    status_output: dict[str, Any] = {"returncode": -1, "output": "OpenCode start.sh did not report completion."}
    while time.monotonic() < deadline:
        status_output = _run_remote(env, _bash(poll_script), "poll OpenCode start.sh", timeout=30, check=False)
        status_text = str(status_output.get("output", "")).strip()
        if status_text.startswith("done:"):
            raw_code = status_text.split("done:", 1)[1].splitlines()[0].strip()
            try:
                returncode = int(raw_code)
            except ValueError:
                returncode = -1
            wrapper_output = _remote_read_text(
                env,
                wrapper_log_path,
                "read OpenCode wrapper log",
                required=False,
            )
            return {
                "returncode": returncode,
                "output": (wrapper_output or "")[-4000:],
                "exception_info": "",
            }
        if status_output.get("returncode") not in (0, 4):
            break
        time.sleep(min(10.0, max(1.0, deadline - time.monotonic())))

    terminate_script = f"""
set +e
pid="$(cat {shlex.quote(pid_path)} 2>/dev/null)"
if [ -n "$pid" ]; then
  kill -INT "-$pid" 2>/dev/null || kill -INT "$pid" 2>/dev/null || true
  sleep 10
  kill -TERM "-$pid" 2>/dev/null || kill -TERM "$pid" 2>/dev/null || true
  sleep 5
  kill -KILL "-$pid" 2>/dev/null || kill -KILL "$pid" 2>/dev/null || true
fi
echo 124 > {shlex.quote(exit_path)}
touch {shlex.quote(done_path)}
""".strip()
    _run_remote(env, _bash(terminate_script), "terminate timed out OpenCode start.sh", timeout=30, check=False)
    return {
        "returncode": 124,
        "output": str(status_output.get("output", ""))[-4000:],
        "exception_info": "OpenCode start.sh exceeded Modal OpenCode command timeout.",
    }


def _write_trajectory_manifest(
    config: ModalOpencodeConfig,
    *,
    result: dict[str, Any] | None,
    agent_run_started: bool,
    started_at: float,
    ended_at: float,
    error: str | None,
) -> dict[str, Any]:
    manifest_path = config.output_dir / "logs" / "opencode" / "trajectory-manifest.json"
    trajectory_path = config.output_dir / "logs" / "opencode" / "opencode.traj.json"
    trajectory_exists = trajectory_path.exists()
    stat = trajectory_path.stat() if trajectory_exists else None
    worker_error = error
    if agent_run_started and not trajectory_exists:
        worker_error = f"{worker_error}; missing OpenCode trajectory" if worker_error else "missing OpenCode trajectory"

    worker = {
        "worker_name": "opencode-main",
        "worker_type": "opencode",
        "role": None,
        "branch": "main",
        "model": config.model,
        "trajectory_path": _local_metadata_path(config.output_dir, trajectory_path),
        "trajectory_exists": trajectory_exists,
        "trajectory_bytes": stat.st_size if stat else None,
        "trajectory_sha256": _sha256_file(trajectory_path) if trajectory_exists else None,
        "worker_error": worker_error,
        "agent_run_started": agent_run_started,
        "returncode": result.get("returncode") if result else None,
    }
    missing_workers = [] if trajectory_exists else ["opencode-main"]
    manifest = {
        "manifest_version": 1,
        "run_dir": ".",
        "metadata_path": _local_metadata_path(config.output_dir, config.output_dir / "logs" / "modal-opencode-result.json"),
        "mode": config.mode,
        "audit_id": config.audit_id,
        "expected_trajectory_count": 1,
        "found_trajectory_count": 1 if trajectory_exists else 0,
        "missing_trajectory_count": 0 if trajectory_exists else 1,
        "missing_trajectory_workers": missing_workers,
        "workers": [worker],
        "run_error": error,
        "started_at": started_at,
        "ended_at": ended_at,
        "runtime_seconds": ended_at - started_at,
    }
    manifest_path.parent.mkdir(parents=True, exist_ok=True)
    manifest_path.write_text(json.dumps(manifest, indent=2, default=str), encoding="utf-8")
    return manifest


def _write_local_run_metadata(
    config: ModalOpencodeConfig,
    result: dict[str, Any] | None,
    grade: dict[str, Any] | None,
    trajectory_manifest: dict[str, Any] | None,
    *,
    started_at: float,
    ended_at: float,
    error: str | None = None,
) -> None:
    logs_dir = config.output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "config": {
            **asdict(config),
            "output_dir": str(config.output_dir),
            "agent_env": _redact_env(config.agent_env),
        },
        "result": result,
        "grade": grade,
        "trajectory_manifest": "logs/opencode/trajectory-manifest.json" if trajectory_manifest else None,
        "trajectory_integrity": {
            "expected_trajectory_count": trajectory_manifest["expected_trajectory_count"],
            "found_trajectory_count": trajectory_manifest["found_trajectory_count"],
            "missing_trajectory_count": trajectory_manifest["missing_trajectory_count"],
            "missing_trajectory_workers": trajectory_manifest["missing_trajectory_workers"],
        }
        if trajectory_manifest
        else None,
        "error": error,
        "started_at": started_at,
        "ended_at": ended_at,
        "runtime_seconds": ended_at - started_at,
    }
    (logs_dir / "modal-opencode-result.json").write_text(
        json.dumps(payload, indent=2, default=str),
        encoding="utf-8",
    )


def run_modal_opencode(config: ModalOpencodeConfig) -> dict[str, Any]:
    config.output_dir.mkdir(parents=True, exist_ok=True)
    (config.output_dir / "logs").mkdir(parents=True, exist_ok=True)

    audit, instructions = _load_audit_for_mode(config)
    _, SwerexModalEnvironment, _ = _load_mini_classes()
    runtime_modal_sandbox_kwargs, serializable_modal_sandbox_kwargs = _build_modal_sandbox_kwargs(config)
    agent = agent_registry.get_agent(config.agent_id)
    remote_env = _remote_opencode_env(config)

    env = SwerexModalEnvironment(
        image=config.image,
        cwd=AUDIT_DIR,
        timeout=config.command_timeout,
        env=remote_env,
        startup_timeout=config.startup_timeout,
        runtime_timeout=config.runtime_timeout,
        deployment_timeout=config.deployment_timeout,
        install_pipx=config.install_pipx,
        modal_sandbox_kwargs=runtime_modal_sandbox_kwargs,
    )
    env.config.modal_sandbox_kwargs = serializable_modal_sandbox_kwargs

    started_at = time.time()
    result: dict[str, Any] | None = None
    grade: dict[str, Any] | None = None
    error: str | None = None
    agent_run_started = False
    try:
        _prepare_remote_workspace(env)
        _stage_rendered_instructions(env, instructions)
        _stage_start_script(env, agent.start_sh)
        ploit_toml = _prepare_mode(env, audit, config.mode)
        agent_run_started = True
        output = _run_opencode_start_sh(env, config)
        if output.get("returncode") != 0:
            raise RemoteCommandError("run OpenCode start.sh", f"bash {shlex.quote(AGENT_DIR + '/start.sh')}", output)
        result = {
            "returncode": output.get("returncode"),
            "output_tail": str(output.get("output", ""))[-4000:],
            "dry_run": config.dry_run,
        }
        if not config.dry_run:
            _postprocess_mode(env, audit, config.mode, ploit_toml)
        _extract_remote_outputs(env, config.output_dir)
        _extract_opencode_config(env, config.output_dir)
        if config.grade and config.mode == "detect" and not config.dry_run:
            grade = _grade_detect_output(config, audit)
        return {"agent_result": result, "grade": grade}
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        ended_at = time.time()
        trajectory_manifest: dict[str, Any] | None = None
        try:
            _extract_remote_outputs(env, config.output_dir)
            _extract_opencode_config(env, config.output_dir)
            trajectory_manifest = _write_trajectory_manifest(
                config,
                result=result,
                agent_run_started=agent_run_started,
                started_at=started_at,
                ended_at=ended_at,
                error=error,
            )
        except Exception:
            pass
        _write_local_run_metadata(
            config,
            result,
            grade,
            trajectory_manifest,
            started_at=started_at,
            ended_at=ended_at,
            error=error,
        )
        env.stop()


def _resolved_agent_env(agent_id: str) -> dict[str, str]:
    agent = agent_registry.get_agent(agent_id)
    env = dict(agent.env_vars or {})
    for name in (
        "OPENAI_API_KEY",
        "OPENAI_API_BASE",
        "OPENAI_BASE_URL",
        "OPENROUTER_API_KEY",
        "VLLM_API_BASE",
        "VLLM_API_KEY",
        "VLLM_MODEL",
        "VLLM_SERVED_MODEL_NAME",
        "MODEL",
        "OPENCODE_PROVIDER_ID",
        "OPENCODE_MODEL_ID",
        "OPENCODE_MODEL",
        "OPENCODE_DRY_RUN",
        "OPENCODE_AGENT_TIMEOUT_SECONDS",
    ):
        value = os.getenv(name)
        if value is not None:
            env[name] = value
    return env


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--audit-id", required=True, help="EVMBench audit id, e.g. 2024-01-canto.")
    parser.add_argument("--mode", choices=["detect", "patch", "exploit"], default="detect")
    parser.add_argument("--hint-level", choices=["none", "low", "med", "high", "max"], default="none")
    parser.add_argument("--findings-subdir", choices=["", "low", "medium", "high"], default="")
    parser.add_argument("--image", required=True, help="Registry-pullable audit image to run in Modal.")
    parser.add_argument("--agent-id", default="opencode-qwen-vllm")
    parser.add_argument("--model", default=os.getenv("MODEL", "openai/gpt-4.1"))
    parser.add_argument("--command-timeout", type=int, default=int(os.getenv("COMMAND_TIMEOUT", "10800")))
    parser.add_argument("--startup-timeout", type=float, default=600.0)
    parser.add_argument("--runtime-timeout", type=float, default=10800.0)
    parser.add_argument("--deployment-timeout", type=float, default=3600.0)
    parser.add_argument("--install-pipx", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--grade", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--dry-run", action=argparse.BooleanOptionalAction, default=False)
    parser.add_argument(
        "--modal-secret-name",
        default=os.getenv("MODAL_OPENAI_SECRET_NAME", DEFAULT_MODAL_SECRET_NAME),
        help="Optional Modal secret exposing OPENAI_API_KEY inside the sandbox.",
    )
    parser.add_argument("--judge-model", default=os.getenv("JUDGE_MODEL", "gpt-5"))
    parser.add_argument(
        "--judge-reasoning-effort",
        choices=["low", "medium", "high"],
        default=os.getenv("JUDGE_REASONING_EFFORT", "high"),
    )
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument(
        "--model-kwargs-json",
        type=lambda raw: _parse_json_object(raw, "--model-kwargs-json"),
        default={},
        help="Accepted for shared Modal runner compatibility; not used by OpenCode.",
    )
    parser.add_argument(
        "--modal-sandbox-kwargs-json",
        type=lambda raw: _parse_json_object(raw, "--modal-sandbox-kwargs-json"),
        default={},
        help="JSON object passed to SwerexModalEnvironment modal_sandbox_kwargs.",
    )
    parser.add_argument(
        "--cost-tracking",
        choices=["default", "ignore_errors"],
        default=os.getenv("MSWEA_COST_TRACKING", "default"),
        help="Accepted for shared Modal runner compatibility; not used by OpenCode.",
    )
    parser.add_argument("--task", default="")
    return parser


def config_from_args(args: argparse.Namespace) -> ModalOpencodeConfig:
    output_dir = args.output_dir or _default_output_dir(args.audit_id, args.mode)
    modal_secret_name = args.modal_secret_name.strip() or None
    return ModalOpencodeConfig(
        audit_id=args.audit_id,
        mode=args.mode,
        hint_level=args.hint_level,
        findings_subdir=args.findings_subdir,
        image=args.image,
        agent_id=args.agent_id,
        model=args.model,
        command_timeout=args.command_timeout,
        startup_timeout=args.startup_timeout,
        runtime_timeout=args.runtime_timeout,
        deployment_timeout=args.deployment_timeout,
        install_pipx=args.install_pipx,
        grade=args.grade,
        dry_run=args.dry_run,
        modal_secret_name=modal_secret_name,
        judge_model=args.judge_model,
        judge_reasoning_effort=args.judge_reasoning_effort,
        output_dir=output_dir,
        model_kwargs=args.model_kwargs_json,
        modal_sandbox_kwargs=args.modal_sandbox_kwargs_json,
        cost_tracking=args.cost_tracking,
        task=args.task,
        agent_env=_resolved_agent_env(args.agent_id),
    )


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    config = config_from_args(args)

    try:
        result = run_modal_opencode(config)
    except Exception as exc:
        print(f"Modal OpenCode failed: {exc}", file=sys.stderr)
        return 1

    print(json.dumps({"output_dir": str(config.output_dir), "result": result}, indent=2, default=str))
    if config.grade and config.mode == "detect" and not config.dry_run:
        grade_path = config.output_dir / "logs" / GRADE_OUTPUT_FILENAME
        print(json.dumps({"grade_path": str(grade_path)}, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
