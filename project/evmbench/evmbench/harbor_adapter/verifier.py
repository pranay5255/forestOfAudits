from __future__ import annotations

import argparse
import asyncio
import json
import os
from pathlib import Path
from typing import Any, Literal

from evmbench.audit import audit_registry
from evmbench.harbor_adapter.computer import HarborComputerInterface
from evmbench.nano.grade import EVMbenchDetectResult, GraderContext, build_grader
from evmbench.nano.runtime import EVMRuntimeConfig

Mode = Literal["detect"]


def _jsonable(value: Any) -> Any:
    if hasattr(value, "model_dump"):
        try:
            return value.model_dump(mode="json", serialize_as_any=True)
        except TypeError:
            return value.model_dump(mode="json")
    if hasattr(value, "dict"):
        return value.dict()
    return value


def _numeric_reward_payload(result: EVMbenchDetectResult) -> dict[str, float]:
    max_score = float(result.max_score)
    score = float(result.score)
    detect_max_award = float(result.detect_max_award)
    detect_award = float(result.detect_award)
    reward = score / max_score if max_score > 0 else 0.0
    award_reward = detect_award / detect_max_award if detect_max_award > 0 else reward
    return {
        "reward": reward,
        "award_reward": award_reward,
        "score": score,
        "max_score": max_score,
        "detect_award": detect_award,
        "detect_max_award": detect_max_award,
    }


async def run_detect_verifier(
    *,
    audit_id: str,
    agent_output_path: str | Path = "/home/agent/submission/audit.md",
    reward_path: str | Path = "/logs/verifier/reward.json",
    grade_path: str | Path = "/logs/verifier/evmbench-grade.json",
    findings_subdir: Literal["", "low", "medium", "high"] = "",
    judge_model: str | None = None,
    reasoning_effort: str | None = None,
    run_group_id: str = "harbor",
    run_id: str | None = None,
    runs_dir: str = "/logs",
) -> dict[str, float]:
    audit = audit_registry.get_audit(audit_id, findings_subdir=findings_subdir)
    runtime_config = EVMRuntimeConfig(
        agent_id="harbor",
        judge_model=judge_model
        or os.getenv("EVMBENCH_HARBOR_JUDGE_MODEL")
        or os.getenv("JUDGE_MODEL")
        or "gpt-5",
        reasoning_effort=reasoning_effort
        or os.getenv("EVMBENCH_HARBOR_REASONING_EFFORT")
        or os.getenv("JUDGE_REASONING_EFFORT")
        or "high",
    )
    computer = HarborComputerInterface()
    grader = build_grader("detect", computer, runtime_config.turn_completer)
    ctx = GraderContext(
        audit=audit,
        mode="detect",
        agent_output_path=Path(agent_output_path),
        run_group_id=run_group_id,
        run_id=run_id or audit_id,
        runs_dir=runs_dir,
    )

    grade = await grader.grade(ctx)
    result = grade.evmbench_result
    if not isinstance(result, EVMbenchDetectResult):
        raise TypeError(f"Detect verifier expected EVMbenchDetectResult, got {type(result)!r}.")

    reward = _numeric_reward_payload(result)
    reward_file = Path(reward_path)
    grade_file = Path(grade_path)
    reward_file.parent.mkdir(parents=True, exist_ok=True)
    grade_file.parent.mkdir(parents=True, exist_ok=True)
    reward_file.write_text(json.dumps(reward, indent=2, sort_keys=True), encoding="utf-8")
    grade_file.write_text(json.dumps(_jsonable(grade), indent=2, default=str), encoding="utf-8")
    return reward


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Run the EVMBench Harbor detect verifier.")
    parser.add_argument("--audit-id", required=True)
    parser.add_argument("--mode", choices=["detect"], default="detect")
    parser.add_argument("--hint-level", choices=["none", "low", "med", "high", "max"], default="none")
    parser.add_argument("--findings-subdir", choices=["", "low", "medium", "high"], default="")
    parser.add_argument("--agent-output-path", default="/home/agent/submission/audit.md")
    parser.add_argument("--reward-path", default="/logs/verifier/reward.json")
    parser.add_argument("--grade-path", default="/logs/verifier/evmbench-grade.json")
    parser.add_argument("--judge-model", default=None)
    parser.add_argument("--reasoning-effort", default=None)
    parser.add_argument("--run-group-id", default="harbor")
    parser.add_argument("--run-id", default=None)
    parser.add_argument("--runs-dir", default="/logs")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    del args.hint_level
    try:
        asyncio.run(
            run_detect_verifier(
                audit_id=args.audit_id,
                agent_output_path=args.agent_output_path,
                reward_path=args.reward_path,
                grade_path=args.grade_path,
                findings_subdir=args.findings_subdir,
                judge_model=args.judge_model,
                reasoning_effort=args.reasoning_effort,
                run_group_id=args.run_group_id,
                run_id=args.run_id,
                runs_dir=args.runs_dir,
            )
        )
    except Exception as exc:
        reward_file = Path(args.reward_path)
        reward_file.parent.mkdir(parents=True, exist_ok=True)
        reward_file.write_text(
            json.dumps(
                {
                    "reward": 0.0,
                    "award_reward": 0.0,
                    "score": 0.0,
                    "max_score": 0.0,
                    "detect_award": 0.0,
                    "detect_max_award": 0.0,
                },
                indent=2,
                sort_keys=True,
            ),
            encoding="utf-8",
        )
        error_path = reward_file.parent / "error.txt"
        error_path.write_text(str(exc) + "\n", encoding="utf-8")
        print(f"EVMBench Harbor verifier failed: {exc}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
