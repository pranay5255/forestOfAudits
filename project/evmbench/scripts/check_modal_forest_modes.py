#!/usr/bin/env python3
"""Preflight modal_forest mode/audit pairs before expensive Modal runs."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any, Literal

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from evmbench.agents.agent import agent_registry
from evmbench.audit import Audit, audit_registry

Mode = Literal["detect", "patch", "exploit"]
VALID_MODES: tuple[Mode, ...] = ("detect", "patch", "exploit")


def _parse_csv(raw: str | None) -> list[str]:
    return [part.strip() for part in (raw or "").split(",") if part.strip()]


def _selected_modes(args: argparse.Namespace) -> list[Mode]:
    raw_modes = _parse_csv(args.modes) or ([args.mode] if args.mode else ["detect"])
    modes: list[Mode] = []
    for raw in raw_modes:
        if raw not in VALID_MODES:
            raise ValueError(f"Unsupported mode {raw!r}; expected one of {', '.join(VALID_MODES)}.")
        modes.append(raw)
    return modes


def _selected_audits(args: argparse.Namespace) -> list[str]:
    audits = _parse_csv(args.audits) or ([args.audit] if args.audit else [])
    if not audits:
        raise ValueError("Provide --audit or --audits.")
    return audits


def _audit_for_mode(audit_id: str, mode: Mode) -> Audit:
    audit = audit_registry.get_audit(audit_id)
    if mode == "patch":
        audit = audit.retain_only_patch_vulnerabilities()
    elif mode == "exploit":
        audit = audit.retain_only_exploit_vulnerabilities()
    return audit


def _build_command(agent_id: str, audit_id: str, mode: Mode, runs_dir: Path) -> list[str]:
    return [
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


def _row_for_pair(
    *,
    mode: Mode,
    audit_id: str,
    agent_id: str,
    output_root: Path,
    emit_command: bool,
    run: bool,
) -> dict[str, Any]:
    row: dict[str, Any] = {
        "mode": mode,
        "audit_id": audit_id,
        "agent_id": agent_id,
        "ok": False,
        "skipped_no_vulnerabilities": False,
    }
    try:
        agent = agent_registry.get_agent(agent_id)
        if agent.runner == "modal_baseline" and mode != "detect":
            row["status"] = "error"
            row["error"] = f"{agent.runner} supports detect mode only."
            return row

        audit = _audit_for_mode(audit_id, mode)
        row["vulnerability_count"] = len(audit.vulnerabilities)
        if not audit.vulnerabilities:
            row["status"] = "skipped_no_vulnerabilities"
            row["skipped_no_vulnerabilities"] = True
            row["error"] = f"Audit {audit_id} has no vulnerabilities for mode {mode}."
            return row

        runs_dir = output_root / mode / agent_id
        command = _build_command(agent_id, audit_id, mode, runs_dir)
        row["status"] = "ok"
        row["ok"] = True
        row["runs_dir"] = str(runs_dir)
        if emit_command or run:
            row["command"] = command
        if run:
            completed = subprocess.run(command, cwd=PROJECT_ROOT)
            row["returncode"] = completed.returncode
            row["ok"] = completed.returncode == 0
            row["status"] = "ok" if completed.returncode == 0 else "error"
            if completed.returncode != 0:
                row["error"] = f"Command exited {completed.returncode}."
    except Exception as exc:
        row["status"] = "error"
        row["error"] = str(exc)
    return row


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    mode_group = parser.add_mutually_exclusive_group()
    mode_group.add_argument("--mode", choices=VALID_MODES)
    mode_group.add_argument("--modes", help="Comma-separated modes, e.g. detect,patch,exploit.")
    audit_group = parser.add_mutually_exclusive_group(required=True)
    audit_group.add_argument("--audit")
    audit_group.add_argument("--audits", help="Comma-separated audit IDs.")
    parser.add_argument("--agent-id", required=True)
    parser.add_argument("--output-root", type=Path, required=True)
    parser.add_argument("--emit-command", action="store_true")
    parser.add_argument("--run", action="store_true")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        modes = _selected_modes(args)
        audits = _selected_audits(args)
    except ValueError as exc:
        parser.error(str(exc))

    rows = [
        _row_for_pair(
            mode=mode,
            audit_id=audit_id,
            agent_id=args.agent_id,
            output_root=args.output_root,
            emit_command=args.emit_command,
            run=args.run,
        )
        for mode in modes
        for audit_id in audits
    ]
    for row in rows:
        print(json.dumps(row, sort_keys=True))
    return 1 if any(row["status"] == "error" for row in rows) else 0


if __name__ == "__main__":
    raise SystemExit(main())
