#!/usr/bin/env python3
"""Direct forest-free EVMBench harness runner for vLLM-backed agents."""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import threading
import time

import requests
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Literal

from evmbench.vllm import metrics
from evmbench.vllm.common import (
    clean_env_value,
    fail,
    litellm_model_name,
    load_project_env,
    modal_binary,
    project_root,
    redacted_length,
    require_env,
    server_root_from_api_base,
    write_json,
)

Harness = Literal["codex", "opencode", "mini-swe-agent"]
Mode = Literal["detect", "patch", "exploit"]
KernelProfile = Literal["torch", "off"]
DEFAULT_PROFILE_VOLUME_NAME = "evmbench-vllm-profiles"

HARNESS_AGENT_IDS: dict[Harness, str] = {
    "codex": "codex-qwen-vllm",
    "opencode": "opencode-qwen-vllm",
    "mini-swe-agent": "mini-swe-agent-qwen-vllm",
}


@dataclass(frozen=True)
class HarnessRunSpec:
    harness: Harness
    agent_id: str
    audit_id: str
    mode: Mode
    output_dir: Path
    runs_dir: Path
    command: tuple[str, ...]


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def default_output_dir(*, harness: Harness, audit_id: str, mode: Mode) -> Path:
    safe_audit = audit_id.replace("/", "_")
    return project_root() / "runs" / "vllm-harness" / f"{_timestamp()}_{harness}_{safe_audit}_{mode}"


def agent_id_for_harness(harness: Harness) -> str:
    try:
        return HARNESS_AGENT_IDS[harness]
    except KeyError as exc:
        known = ", ".join(sorted(HARNESS_AGENT_IDS))
        raise ValueError(f"Unknown vLLM harness {harness!r}. Expected one of: {known}.") from exc


def build_evmbench_command(
    *,
    harness: Harness,
    audit_id: str,
    mode: Mode,
    runs_dir: Path,
    agent_timeout_seconds: float | None = None,
) -> tuple[str, ...]:
    agent_id = agent_id_for_harness(harness)
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


def build_run_spec(
    *,
    harness: Harness,
    audit_id: str,
    mode: Mode,
    output_dir: Path | None = None,
    runs_dir: Path | None = None,
    agent_timeout_seconds: float | None = None,
) -> HarnessRunSpec:
    resolved_output_dir = output_dir or default_output_dir(harness=harness, audit_id=audit_id, mode=mode)
    resolved_runs_dir = runs_dir or resolved_output_dir / "evmbench-runs"
    command = build_evmbench_command(
        harness=harness,
        audit_id=audit_id,
        mode=mode,
        runs_dir=resolved_runs_dir,
        agent_timeout_seconds=agent_timeout_seconds,
    )
    return HarnessRunSpec(
        harness=harness,
        agent_id=agent_id_for_harness(harness),
        audit_id=audit_id,
        mode=mode,
        output_dir=resolved_output_dir,
        runs_dir=resolved_runs_dir,
        command=command,
    )


def _redacted_vllm_env(api_base: str, api_key: str, served_model_name: str) -> dict[str, str]:
    return {
        "VLLM_API_BASE": api_base,
        "VLLM_API_KEY": redacted_length(api_key),
        "VLLM_SERVED_MODEL_NAME": served_model_name,
        "VLLM_LITELLM_MODEL": litellm_model_name(served_model_name),
    }


def _run_env(api_base: str, api_key: str, served_model_name: str) -> dict[str, str]:
    env = os.environ.copy()
    env.update(
        {
            "VLLM_API_BASE": api_base,
            "VLLM_API_KEY": api_key,
            "VLLM_SERVED_MODEL_NAME": served_model_name,
            "VLLM_LITELLM_MODEL": litellm_model_name(served_model_name),
            "MODEL": litellm_model_name(served_model_name),
            "MODEL_KWARGS_JSON": env.get("MODEL_KWARGS_JSON", '{"drop_params":true}'),
            "MSWEA_COST_TRACKING": env.get("MSWEA_COST_TRACKING", "ignore_errors"),
            "PYTHONUNBUFFERED": "1",
        }
    )
    return env


def _write_initial_manifest(
    *,
    spec: HarnessRunSpec,
    manifest_path: Path,
    stdout_path: Path,
    stderr_path: Path,
    api_base: str,
    api_key: str,
    served_model_name: str,
    metrics_enabled: bool,
    kernel_profile: KernelProfile,
    profile_volume_name: str,
) -> dict[str, Any]:
    manifest: dict[str, Any] = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "harness": spec.harness,
        "agent_id": spec.agent_id,
        "audit_id": spec.audit_id,
        "mode": spec.mode,
        "output_dir": str(spec.output_dir),
        "runs_dir": str(spec.runs_dir),
        "command": list(spec.command),
        "model": served_model_name,
        "vllm_env": _redacted_vllm_env(api_base, api_key, served_model_name),
        "profiling": {
            "kernel_profile": kernel_profile,
            "profile_volume_name": profile_volume_name,
            "start_profile_endpoint": f"{server_root_from_api_base(api_base)}/start_profile" if kernel_profile == "torch" else None,
            "stop_profile_endpoint": f"{server_root_from_api_base(api_base)}/stop_profile" if kernel_profile == "torch" else None,
        },
        "artifact_paths": {
            "stdout_log": str(stdout_path),
            "stderr_log": str(stderr_path),
            "metrics_dir": str(spec.output_dir / "metrics"),
        },
        "stdout_log": str(stdout_path),
        "stderr_log": str(stderr_path),
        "return_code": None,
        "started_at": None,
        "finished_at": None,
        "runtime_seconds": None,
        "metrics_enabled": metrics_enabled,
        "metrics_manifest_path": None,
    }
    write_json(manifest_path, manifest)
    return manifest


def _collect_snapshot(
    *,
    name: str,
    api_base: str,
    api_key: str,
    output_dir: Path,
    timeout: float,
    snapshot_dirs: dict[str, Path],
    errors: dict[str, str],
) -> dict[str, Any] | None:
    try:
        summary = metrics.snapshot_metrics(api_base=api_base, api_key=api_key, output_dir=output_dir, timeout=timeout)
        snapshot_dirs[name] = output_dir
        return summary
    except Exception as exc:  # Metrics should be preserved as an artifact issue, not hidden.
        output_dir.mkdir(parents=True, exist_ok=True)
        error_path = output_dir / "metrics.error.json"
        error_path.write_text(
            json.dumps({"error": str(exc), "type": type(exc).__name__}, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )
        errors[name] = str(exc)
        return None


def _profile_request(
    *,
    api_base: str,
    api_key: str,
    action: Literal["start", "stop"],
    timeout: float,
) -> dict[str, Any]:
    endpoint = f"{server_root_from_api_base(api_base)}/{action}_profile"
    started = time.time()
    payload: dict[str, Any] = {"action": action, "endpoint": endpoint, "started_timestamp": started}
    try:
        response = requests.post(
            endpoint,
            headers={"Authorization": f"Bearer {api_key}"},
            timeout=timeout,
        )
        payload.update(
            {
                "status_code": response.status_code,
                "ok": response.ok,
                "finished_timestamp": time.time(),
                "body_preview": response.text[:1000],
            }
        )
    except Exception as exc:
        payload.update(
            {
                "ok": False,
                "finished_timestamp": time.time(),
                "error": str(exc),
                "type": type(exc).__name__,
            }
        )
    return payload


def _download_modal_volume_path(
    *,
    volume_name: str,
    remote_path: str,
    destination: Path,
) -> dict[str, Any]:
    destination.mkdir(parents=True, exist_ok=True)
    payload: dict[str, Any] = {
        "volume_name": volume_name,
        "remote_path": remote_path,
        "destination": str(destination),
    }
    try:
        command = [modal_binary(), "volume", "get", "--force", volume_name, remote_path, str(destination)]
        completed = subprocess.run(
            command,
            cwd=project_root(),
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
        )
        payload.update(
            {
                "command": command,
                "return_code": completed.returncode,
                "ok": completed.returncode == 0,
                "stdout_tail": completed.stdout[-4000:],
                "stderr_tail": completed.stderr[-4000:],
            }
        )
    except Exception as exc:
        payload.update({"ok": False, "error": str(exc), "type": type(exc).__name__})
    return payload


def _collect_profile_artifacts(
    *,
    metrics_dir: Path,
    profile_volume_name: str,
    kernel_profile: KernelProfile,
    start_timestamp: float | None,
    end_timestamp: float | None,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, str]]:
    errors: dict[str, str] = {}
    downloads: dict[str, Any] = {}
    raw_root = metrics_dir / "profile-volume"

    gpu_raw_dir = raw_root / "gpu"
    gpu_download = _download_modal_volume_path(
        volume_name=profile_volume_name,
        remote_path="/gpu",
        destination=gpu_raw_dir,
    )
    downloads["gpu"] = gpu_download
    if not gpu_download.get("ok"):
        errors["gpu_volume_get"] = str(gpu_download.get("stderr_tail") or gpu_download.get("error") or "modal volume get failed")
    gpu_artifacts = metrics.write_gpu_telemetry_artifacts(
        gpu_raw_dir,
        metrics_dir / "gpu",
        start_timestamp=start_timestamp,
        end_timestamp=end_timestamp,
    )
    gpu_artifacts["download"] = gpu_download

    kernel_artifacts: dict[str, Any] = {"kernel_profile": kernel_profile}
    if kernel_profile == "torch":
        torch_raw_dir = raw_root / "torch"
        torch_download = _download_modal_volume_path(
            volume_name=profile_volume_name,
            remote_path="/torch",
            destination=torch_raw_dir,
        )
        downloads["torch"] = torch_download
        if not torch_download.get("ok"):
            errors["torch_profile_volume_get"] = str(
                torch_download.get("stderr_tail") or torch_download.get("error") or "modal volume get failed"
            )
        kernel_artifacts = metrics.write_kernel_profile_artifacts(
            torch_raw_dir,
            metrics_dir / "kernel" / "torch",
            start_timestamp=start_timestamp,
            end_timestamp=end_timestamp,
        )
        kernel_artifacts["kernel_profile"] = kernel_profile
        kernel_artifacts["download"] = torch_download
    return gpu_artifacts, kernel_artifacts, errors


def run_harness(
    *,
    spec: HarnessRunSpec,
    api_base: str,
    api_key: str,
    served_model_name: str,
    metrics_enabled: bool = True,
    metrics_interval_seconds: float = 15.0,
    metrics_timeout: float = 30.0,
    kernel_profile: KernelProfile = "torch",
    profile_volume_name: str = DEFAULT_PROFILE_VOLUME_NAME,
    dry_run: bool = False,
) -> int:
    spec.output_dir.mkdir(parents=True, exist_ok=True)
    stdout_path = spec.output_dir / "stdout.log"
    stderr_path = spec.output_dir / "stderr.log"
    manifest_path = spec.output_dir / "run-manifest.json"
    manifest = _write_initial_manifest(
        spec=spec,
        manifest_path=manifest_path,
        stdout_path=stdout_path,
        stderr_path=stderr_path,
        api_base=api_base,
        api_key=api_key,
        served_model_name=served_model_name,
        metrics_enabled=metrics_enabled,
        kernel_profile=kernel_profile,
        profile_volume_name=profile_volume_name,
    )

    metrics_dir = spec.output_dir / "metrics"
    snapshot_dirs: dict[str, Path] = {}
    metric_errors: dict[str, str] = {}
    profiling_events: dict[str, Any] = {"kernel_profile": kernel_profile, "profile_volume_name": profile_volume_name}
    gpu_artifacts: dict[str, Any] | None = None
    kernel_artifacts: dict[str, Any] | None = None
    run_started_timestamp: float | None = None
    run_finished_timestamp: float | None = None
    before_summary: dict[str, Any] | None = None
    after_summary: dict[str, Any] | None = None
    poll_result: dict[str, Any] | None = None
    stop_event = threading.Event()
    poll_thread: threading.Thread | None = None

    if metrics_enabled and not dry_run:
        before_summary = _collect_snapshot(
            name="before",
            api_base=api_base,
            api_key=api_key,
            output_dir=metrics_dir / "before",
            timeout=metrics_timeout,
            snapshot_dirs=snapshot_dirs,
            errors=metric_errors,
        )

        def _poll() -> None:
            nonlocal poll_result
            poll_result = metrics.poll_metrics_until_stopped(
                api_base=api_base,
                api_key=api_key,
                output_dir=metrics_dir / "poll",
                interval_seconds=metrics_interval_seconds,
                timeout=metrics_timeout,
                stop_event=stop_event,
            )

        poll_thread = threading.Thread(target=_poll, name="vllm-metrics-poll", daemon=True)
        poll_thread.start()

    started = time.monotonic()
    run_started_timestamp = time.time()
    manifest["started_at"] = datetime.fromtimestamp(run_started_timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    if kernel_profile == "torch" and not dry_run:
        profiling_events["start_profile"] = _profile_request(
            api_base=api_base,
            api_key=api_key,
            action="start",
            timeout=metrics_timeout,
        )
        if not profiling_events["start_profile"].get("ok"):
            metric_errors["start_profile"] = str(profiling_events["start_profile"].get("error") or profiling_events["start_profile"].get("body_preview") or "start_profile failed")
    manifest["profiling"].update(profiling_events)
    write_json(manifest_path, manifest)

    if dry_run:
        return_code = 0
        stdout_path.write_text("", encoding="utf-8")
        stderr_path.write_text("", encoding="utf-8")
    else:
        env = _run_env(api_base, api_key, served_model_name)
        with stdout_path.open("w", encoding="utf-8") as stdout_handle, stderr_path.open("w", encoding="utf-8") as stderr_handle:
            completed = subprocess.run(
                list(spec.command),
                cwd=project_root(),
                env=env,
                stdout=stdout_handle,
                stderr=stderr_handle,
                text=True,
            )
            return_code = completed.returncode

    if kernel_profile == "torch" and not dry_run:
        profiling_events["stop_profile"] = _profile_request(
            api_base=api_base,
            api_key=api_key,
            action="stop",
            timeout=metrics_timeout,
        )
        if not profiling_events["stop_profile"].get("ok"):
            metric_errors["stop_profile"] = str(profiling_events["stop_profile"].get("error") or profiling_events["stop_profile"].get("body_preview") or "stop_profile failed")
    run_finished_timestamp = time.time()

    if poll_thread is not None:
        stop_event.set()
        poll_thread.join(timeout=metrics_timeout + 5.0)
        if poll_thread.is_alive():
            metric_errors["poll"] = "metrics polling thread did not stop before join timeout"

    should_write_metrics_manifest = not dry_run and (metrics_enabled or kernel_profile == "torch")
    delta: dict[str, Any] | None = None
    if metrics_enabled and not dry_run:
        after_summary = _collect_snapshot(
            name="after",
            api_base=api_base,
            api_key=api_key,
            output_dir=metrics_dir / "after",
            timeout=metrics_timeout,
            snapshot_dirs=snapshot_dirs,
            errors=metric_errors,
        )
        delta = metrics.diff_summaries(before_summary or {}, after_summary or {})

    if should_write_metrics_manifest:
        gpu_artifacts, kernel_artifacts, artifact_errors = _collect_profile_artifacts(
            metrics_dir=metrics_dir,
            profile_volume_name=profile_volume_name,
            kernel_profile=kernel_profile,
            start_timestamp=run_started_timestamp,
            end_timestamp=run_finished_timestamp,
        )
        metric_errors.update(artifact_errors)
        metrics_manifest = metrics.write_metrics_manifest(
            metrics_dir,
            snapshots=snapshot_dirs,
            poll=poll_result,
            delta=delta,
            gpu=gpu_artifacts,
            kernel=kernel_artifacts,
            profiling=profiling_events,
            errors=metric_errors,
        )
        manifest["metrics_manifest_path"] = metrics_manifest.get("manifest_path")
        manifest["metrics"] = metrics_manifest
        manifest["artifact_paths"].update(
            {
                "metrics_manifest": metrics_manifest.get("manifest_path"),
                "gpu_telemetry": (gpu_artifacts or {}).get("telemetry_jsonl"),
                "gpu_summary": (gpu_artifacts or {}).get("summary_json"),
                "kernel_profile_index": (kernel_artifacts or {}).get("index_json"),
                "kernel_cuda_summary": (kernel_artifacts or {}).get("cuda_summary_json"),
            }
        )

    manifest["return_code"] = return_code
    manifest["finished_at"] = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    manifest["runtime_seconds"] = time.monotonic() - started
    write_json(manifest_path, manifest)
    return return_code


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--harness", choices=sorted(HARNESS_AGENT_IDS), required=True)
    parser.add_argument("--mode", choices=["detect", "patch", "exploit"], required=True)
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--runs-dir", type=Path)
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--served-model-name", default=None)
    parser.add_argument("--agent-timeout-seconds", type=float, default=None)
    parser.add_argument("--metrics", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument("--metrics-interval-seconds", type=float, default=15.0)
    parser.add_argument("--metrics-timeout", type=float, default=30.0)
    parser.add_argument("--kernel-profile", choices=["torch", "off"], default="torch")
    parser.add_argument(
        "--profile-volume-name",
        default=clean_env_value(os.getenv("VLLM_PROFILE_VOLUME_NAME")) or DEFAULT_PROFILE_VOLUME_NAME,
    )
    parser.add_argument("--dry-run", action="store_true", help="Write the manifest and print the command without executing it.")
    return parser


def _preparse_env_file(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    args, _ = parser.parse_known_args(argv)
    return args.env_file


def main(argv: list[str] | None = None) -> int:
    load_project_env(_preparse_env_file(argv))
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        api_base = clean_env_value(args.api_base) or require_env("VLLM_API_BASE")
        api_key = clean_env_value(args.api_key) or require_env("VLLM_API_KEY")
        served_model_name = clean_env_value(args.served_model_name) or require_env("VLLM_SERVED_MODEL_NAME")
        spec = build_run_spec(
            harness=args.harness,
            audit_id=args.audit_id,
            mode=args.mode,
            output_dir=args.output_dir,
            runs_dir=args.runs_dir,
            agent_timeout_seconds=args.agent_timeout_seconds,
        )
        return_code = run_harness(
            spec=spec,
            api_base=api_base,
            api_key=api_key,
            served_model_name=served_model_name,
            metrics_enabled=args.metrics,
            metrics_interval_seconds=args.metrics_interval_seconds,
            metrics_timeout=args.metrics_timeout,
            kernel_profile=args.kernel_profile,
            profile_volume_name=args.profile_volume_name,
            dry_run=args.dry_run,
        )
        print(
            json.dumps(
                {
                    "output_dir": str(spec.output_dir),
                    "manifest_path": str(spec.output_dir / "run-manifest.json"),
                    "command": list(spec.command),
                    "return_code": return_code,
                },
                indent=2,
            )
        )
        return return_code
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
