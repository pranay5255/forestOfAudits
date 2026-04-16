#!/usr/bin/env python3
"""Minimal mini-swe-agent + Modal smoke test.

This is intentionally smaller than `modal_baseline.py`. It checks the scaled
execution surface described in docs/scale.md without requiring an EVMBench audit
image or an LLM API call:

- start a Modal container through SwerexModalEnvironment
- stage a small instructions file
- run DefaultAgent against that Modal environment
- write /home/agent/submission/audit.md
- extract submission/ and logs/ back to a local output directory
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import io
import json
import os
import shlex
import sys
import tarfile
import time
from pathlib import Path
from typing import Any

os.environ.setdefault("MSWEA_SILENT_STARTUP", "1")
os.environ.setdefault("MSWEA_GLOBAL_COST_LIMIT", "0")

from minisweagent.agents.default import DefaultAgent
from minisweagent.environments.extra.swerex_modal import SwerexModalEnvironment
from minisweagent.models.test_models import DeterministicModel, make_output

from evmbench.constants import AGENT_DIR, AUDIT_DIR, LOGS_DIR, SUBMISSION_DIR
from evmbench.utils import get_default_runs_dir, get_timestamp
from modal_compat import patch_swerex_modal_image_builder

ARCHIVE_BEGIN = "__EVMBENCH_MODAL_SMOKE_ARCHIVE_BEGIN__"
ARCHIVE_END = "__EVMBENCH_MODAL_SMOKE_ARCHIVE_END__"

SYSTEM_TEMPLATE = "You are a Modal smoke-test agent. Execute the scripted checks exactly."
INSTANCE_TEMPLATE = """Run the scripted Modal smoke check.

The task is complete only after /home/agent/submission/audit.md exists and you
finish with `echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`.

Task:
{{ task }}"""

SMOKE_INSTRUCTIONS = """# Modal Smoke Instructions

This is not a real audit. Confirm that the Modal sandbox can read this file,
execute shell commands, write logs, and write /home/agent/submission/audit.md.
"""

SMOKE_COMMAND = f"""set -eu
mkdir -p {shlex.quote(AUDIT_DIR)} {shlex.quote(SUBMISSION_DIR)} {shlex.quote(LOGS_DIR)}
test -s {shlex.quote(AGENT_DIR + "/AGENTS.md")}
python3 --version | tee {shlex.quote(LOGS_DIR + "/python-version.txt")}
uname -a | tee {shlex.quote(LOGS_DIR + "/uname.txt")}
printf 'mini-swe-agent modal smoke ok\\n' > {shlex.quote(SUBMISSION_DIR + "/audit.md")}
test -s {shlex.quote(SUBMISSION_DIR + "/audit.md")}
cat {shlex.quote(SUBMISSION_DIR + "/audit.md")}
"""


class RemoteCommandError(RuntimeError):
    def __init__(self, description: str, command: str, output: dict[str, Any]):
        rendered_output = str(output.get("output", ""))
        if len(rendered_output) > 4000:
            rendered_output = rendered_output[-4000:]
        super().__init__(
            f"{description} failed with return code {output.get('returncode')}.\n"
            f"Command:\n{command}\n\nOutput:\n{rendered_output}"
        )


def _parse_json_object(raw: str) -> dict[str, Any]:
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise argparse.ArgumentTypeError(f"Expected valid JSON object: {exc}") from exc
    if not isinstance(value, dict):
        raise argparse.ArgumentTypeError("Expected JSON object.")
    return value


def _run_remote(env: SwerexModalEnvironment, command: str, description: str, *, timeout: int = 60) -> dict[str, Any]:
    output = env.execute({"command": command}, timeout=timeout)
    if output.get("returncode") != 0:
        raise RemoteCommandError(description, command, output)
    return output


def _remote_write_text(env: SwerexModalEnvironment, remote_path: str, text: str) -> None:
    encoded = base64.b64encode(text.encode("utf-8")).decode("ascii")
    command = f"""python3 - <<'PY'
import base64
from pathlib import Path

path = Path({remote_path!r})
path.parent.mkdir(parents=True, exist_ok=True)
path.write_bytes(base64.b64decode({encoded!r}))
PY"""
    _run_remote(env, command, f"write {remote_path}", timeout=60)


def _decode_marked_base64(output: str, begin: str, end: str) -> bytes:
    started = False
    payload_lines: list[str] = []
    for line in output.splitlines():
        stripped = line.strip()
        if stripped == begin:
            started = True
            continue
        if stripped == end:
            break
        if started and stripped:
            payload_lines.append(stripped)
    if not payload_lines:
        raise RuntimeError(f"Did not find marked base64 payload between {begin} and {end}.")
    return base64.b64decode("".join(payload_lines))


def _safe_extract_tar(data: bytes, output_dir: Path) -> None:
    output_dir.mkdir(parents=True, exist_ok=True)
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
        output_root = output_dir.resolve()
        for member in archive.getmembers():
            member_path = (output_dir / member.name).resolve()
            if output_root != member_path and output_root not in member_path.parents:
                raise RuntimeError(f"Refusing to extract tar member outside output dir: {member.name}")
        archive.extractall(output_dir)


def _extract_remote_outputs(env: SwerexModalEnvironment, output_dir: Path) -> None:
    command = f"""python3 - <<'PY'
import base64
import io
from pathlib import Path
import tarfile

entries = {{
    {SUBMISSION_DIR!r}: "submission",
    {LOGS_DIR!r}: "logs",
}}
buffer = io.BytesIO()
with tarfile.open(fileobj=buffer, mode="w:gz") as archive:
    for source, arcname in entries.items():
        path = Path(source)
        if path.exists():
            archive.add(path, arcname=arcname)

print({ARCHIVE_BEGIN!r})
print(base64.b64encode(buffer.getvalue()).decode("ascii"))
print({ARCHIVE_END!r})
PY"""
    output = _run_remote(env, command, "extract smoke outputs", timeout=60)
    archive_bytes = _decode_marked_base64(str(output.get("output", "")), ARCHIVE_BEGIN, ARCHIVE_END)
    _safe_extract_tar(archive_bytes, output_dir)


def _get_modal_log_url(env: SwerexModalEnvironment) -> str | None:
    try:
        return asyncio.run(env.deployment.get_modal_log_url())
    except Exception:
        return None


def _default_output_dir() -> Path:
    return Path(get_default_runs_dir()) / "modal-smoke" / get_timestamp()


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--image", default="python:3.11-slim", help="Public image used for the Modal smoke.")
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--startup-timeout", type=float, default=600.0)
    parser.add_argument("--runtime-timeout", type=float, default=900.0)
    parser.add_argument("--deployment-timeout", type=float, default=900.0)
    parser.add_argument("--command-timeout", type=int, default=120)
    parser.add_argument("--install-pipx", action=argparse.BooleanOptionalAction, default=True)
    parser.add_argument(
        "--modal-sandbox-kwargs-json",
        type=_parse_json_object,
        default={},
        help="JSON object passed to SwerexModalEnvironment modal_sandbox_kwargs.",
    )
    return parser


def run_smoke(args: argparse.Namespace) -> dict[str, Any]:
    patch_swerex_modal_image_builder()

    output_dir = args.output_dir or _default_output_dir()
    logs_dir = output_dir / "logs"
    logs_dir.mkdir(parents=True, exist_ok=True)

    started_at = time.time()
    env = SwerexModalEnvironment(
        image=args.image,
        # The generic smoke image does not have the EVMBench directory skeleton
        # yet. Use / as the default cwd, then create/check the EVMBench paths in
        # the scripted agent command.
        cwd="/",
        timeout=args.command_timeout,
        env={"PAGER": "cat", "MANPAGER": "cat", "LESS": "-R"},
        startup_timeout=args.startup_timeout,
        runtime_timeout=args.runtime_timeout,
        deployment_timeout=args.deployment_timeout,
        install_pipx=args.install_pipx,
        modal_sandbox_kwargs=args.modal_sandbox_kwargs_json,
    )

    modal_log_url = _get_modal_log_url(env)
    result: dict[str, Any] | None = None
    error: str | None = None
    try:
        _remote_write_text(env, f"{AGENT_DIR}/AGENTS.md", SMOKE_INSTRUCTIONS)
        model = DeterministicModel(
            outputs=[
                make_output("Run the Modal smoke command.", [{"command": SMOKE_COMMAND}], cost=0.0),
                make_output(
                    "Finish the smoke.",
                    [{"command": "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"}],
                    cost=0.0,
                ),
            ],
            cost_per_call=0.0,
        )
        agent = DefaultAgent(
            model,
            env,
            system_template=SYSTEM_TEMPLATE,
            instance_template=INSTANCE_TEMPLATE,
            step_limit=4,
            cost_limit=0.0,
            output_path=logs_dir / "mini-swe-agent.modal-smoke.traj.json",
        )
        result = agent.run("Verify mini-swe-agent can execute commands in Modal.")
        _extract_remote_outputs(env, output_dir)

        audit_md = output_dir / "submission" / "audit.md"
        if not audit_md.exists() or "modal smoke ok" not in audit_md.read_text():
            raise RuntimeError(f"Smoke submission was not extracted correctly: {audit_md}")

        return {
            "ok": True,
            "image": args.image,
            "output_dir": str(output_dir),
            "modal_log_url": modal_log_url,
            "result": result,
        }
    except Exception as exc:
        error = str(exc)
        raise
    finally:
        ended_at = time.time()
        try:
            _extract_remote_outputs(env, output_dir)
        except Exception:
            pass
        (logs_dir / "modal-smoke-result.json").write_text(
            json.dumps(
                {
                    "image": args.image,
                    "output_dir": str(output_dir),
                    "modal_log_url": modal_log_url,
                    "result": result,
                    "error": error,
                    "started_at": started_at,
                    "ended_at": ended_at,
                    "runtime_seconds": ended_at - started_at,
                },
                indent=2,
                default=str,
            )
        )
        env.stop()


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        result = run_smoke(args)
    except Exception as exc:
        print(f"Modal smoke failed: {exc}", file=sys.stderr)
        return 1
    print(json.dumps(result, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
