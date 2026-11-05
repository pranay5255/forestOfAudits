#!/usr/bin/env python3
"""Validate a harness end-to-end against a real audit image.

Runs the real preflight (version gate + multi-turn + file write), then a real
baseline detect stage, then the real EVMBench grader.  This is the script used
to validate OpenCode, Codex, and Yudai.

Examples::

    uv run python scripts/validate_harnesses.py \
      --harness opencode --image evmbench/audit:2024-03-gitcoin-pinned \
      --audit 2024-01-canto --model deepseek/deepseek-v4.1-flash-1017

    # Pinned image with Yudai baked in:
    scripts/build_harness_image.sh --base evmbench/audit:2024-03-gitcoin \
      --output evmbench/audit:2024-03-gitcoin-fullpin --yudai
    uv run python scripts/validate_harnesses.py \
      --harness yudai --image evmbench/audit:2024-03-gitcoin-fullpin \
      --audit 2024-03-gitcoin --model openai/gpt-5.4-mini --minutes 10
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import tempfile
import time
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _load_env() -> None:
    env_path = PROJECT_ROOT / ".env"
    if not env_path.exists():
        return
    try:
        from dotenv import dotenv_values
    except ModuleNotFoundError:
        return
    for key, value in dotenv_values(env_path).items():
        if key in ("OPENROUTER_API_KEY", "OPENAI_API_KEY", "EVMBENCH_MODEL_PROVIDER"):
            if value:
                os.environ[key] = value


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--harness", required=True, choices=["opencode", "codex", "yudai"])
    parser.add_argument("--image", required=True)
    parser.add_argument("--audit", default="2024-01-canto")
    parser.add_argument("--model", default="deepseek/deepseek-v4.1-flash-1017")
    parser.add_argument("--minutes", type=float, default=20.0)
    parser.add_argument("--preflight-only", action="store_true")
    parser.add_argument("--skip-grade", action="store_true")
    parser.add_argument("--allow-version-mismatch", action="store_true")
    args = parser.parse_args(argv)

    _load_env()
    if args.allow_version_mismatch:
        os.environ["EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH"] = "1"

    from evmbench.agents.agent import agent_registry
    from evmbench.agents.backend.local import LocalDockerComputer
    from evmbench.agents.forest.prompts import baseline_task
    from evmbench.agents.harness import get_adapter
    from evmbench.agents.harness.base import StageSpec
    from evmbench.agents.harness.preflight import preflight_spec, run_preflight
    from evmbench.audit import audit_registry
    from evmbench.nano.grade.modal import GradeRequest, grade_submission

    adapter = get_adapter(args.harness)
    root = Path(tempfile.mkdtemp(prefix=f"evmbench-{args.harness}-validate-"))
    provider_env = {
        name: os.environ[name]
        for name in ("OPENROUTER_API_KEY", "OPENAI_API_KEY")
        if os.environ.get(name)
    }

    def factory(stage: str):
        return LocalDockerComputer.create(
            image=args.image,
            campaign_id="validate",
            task_id=args.audit,
            attempt_id=0,
            stage=stage,
            worker_id=args.harness,
            env=provider_env,
        )

    spec = preflight_spec(
        campaign_id="validate",
        harness=args.harness,
        model=args.model,
        image=args.image,
        time_limit_seconds=min(args.minutes * 60, 600),
    )
    preflight = run_preflight(
        adapter,
        spec=spec,
        computer_factory=lambda: factory("preflight"),
        cache_dir=root / "preflight-cache",
        refresh=True,
    )
    print("PREFLIGHT", json.dumps(preflight.to_dict(), indent=2))
    if args.preflight_only:
        return 0

    audit = audit_registry.get_audit(args.audit)
    instructions = agent_registry.load_instructions("detect", audit, "none")
    artifact_root = root / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    stage_spec = StageSpec(
        campaign_id="validate",
        task_id=args.audit,
        attempt_id=0,
        stage="baseline",
        worker_id="baseline",
        mode="detect",
        harness=args.harness,
        strategy="baseline",
        model=args.model,
        image=args.image,
        instructions=instructions,
        task=baseline_task("detect"),
        step_limit=60,
        cost_limit=5.0,
        time_limit_seconds=args.minutes * 60,
        output_paths=("/home/agent/submission/audit.md",),
        artifact_root=str(artifact_root),
    )
    computer = factory("baseline")
    started = time.time()
    try:
        result = adapter.run(computer, stage_spec)
    finally:
        computer.terminate()
    print(
        "STAGE",
        json.dumps(
            {
                "status": result.status.value,
                "exit_code": result.exit_code,
                "failure_reason": result.failure_reason,
                "runtime_seconds": result.runtime_seconds,
                "artifacts": [artifact.to_dict() for artifact in result.artifacts],
            },
            indent=2,
        ),
    )

    submission = artifact_root / "audit.md"
    if not args.skip_grade and submission.exists() and submission.stat().st_size:
        outcome = grade_submission(
            GradeRequest(
                audit=audit,
                mode="detect",
                submission_path=submission,
                evaluator_model="openai/gpt-6-luna",
                run_group_id="validate",
                run_id=args.harness,
                runs_dir=str(root),
            )
        )
        print("GRADE", json.dumps(outcome.to_dict(), indent=2))
    print("ELAPSED", round(time.time() - started, 1))
    return 0 if result.completed else 1


if __name__ == "__main__":
    raise SystemExit(main())
