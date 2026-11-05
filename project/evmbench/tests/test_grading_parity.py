from __future__ import annotations

import asyncio
import hashlib
from pathlib import Path

import pytest

from evmbench.agents.backend.local import LocalProcessComputer
from evmbench.audit import Audit, Vulnerability
from evmbench.nano.grade import modal as modal_grading
from evmbench.nano.grade.base import (
    EVMbenchDetectResult,
    EVMbenchGrade,
    EVMbenchResult,
    GraderContext,
)
from evmbench.nano.grade.modal import (
    GradeRequest,
    TaskComputerInterface,
    grade_submission,
    submission_remote_path,
)


@pytest.fixture(autouse=True)
def _evaluator_env(monkeypatch):
    # Keep grading tests independent of real provider credentials.
    monkeypatch.setenv("EVMBENCH_EVALUATOR_PROVIDER", "openai")
    monkeypatch.setenv("OPENAI_API_KEY", "test-key")


def test_evaluator_routes_openai_alias_through_openrouter(monkeypatch):
    import os

    saved = {name: os.environ.get(name) for name in ("OPENAI_BASE_URL", "OPENAI_API_KEY", "OPENAI_API_BASE")}
    try:
        monkeypatch.delenv("EVMBENCH_EVALUATOR_PROVIDER", raising=False)
        monkeypatch.delenv("EVMBENCH_EVALUATOR_BASE_URL", raising=False)
        monkeypatch.setenv("OPENROUTER_API_KEY", "openrouter-key")
        monkeypatch.setenv("OPENAI_API_KEY", "direct-key")
        assert modal_grading._configure_evaluator_provider("openai/gpt-6-luna") == "openrouter"
        assert os.environ["OPENAI_BASE_URL"] == "https://openrouter.ai/api/v1"
        assert os.environ["OPENAI_API_KEY"] == "openrouter-key"
    finally:
        for name, value in saved.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value


def make_audit() -> Audit:
    return Audit(
        id="audit-1",
        vulnerabilities=[Vulnerability(id="v0", audit_id="audit-1", title="v0")],
    )


def make_request(path: Path, mode: str = "detect") -> GradeRequest:
    return GradeRequest(
        audit=make_audit(),
        mode=mode,  # type: ignore[arg-type]
        submission_path=path,
        evaluator_model="openai/gpt-6-luna",
        run_group_id="cmp",
        run_id="run",
        runs_dir=str(path.parent),
    )


def test_task_computer_interface_round_trip(tmp_path):
    computer = LocalProcessComputer.create(root=tmp_path)
    interface = TaskComputerInterface(computer)
    try:
        asyncio.run(interface.upload(b"hello", "/home/agent/x.txt"))
        assert asyncio.run(interface.download("/home/agent/x.txt")) == b"hello"
        result = asyncio.run(interface.send_shell_command("echo hi"))
        assert result.exit_code == 0
        assert b"hi" in result.output
        assert asyncio.run(interface.fetch_container_names()) == [computer.identity.sandbox_id]
    finally:
        computer.terminate()


def test_missing_submission_is_not_graded_not_zero(tmp_path):
    missing = tmp_path / "audit.md"
    outcome = grade_submission(make_request(missing))
    assert not outcome.graded
    assert outcome.score is None
    payload = outcome.to_dict()
    assert payload["score_is_placeholder"] is False
    assert "missing" in payload["error"]


def test_detect_grade_provenance(tmp_path, monkeypatch):
    submission = tmp_path / "audit.md"
    submission.write_text("# audit\n")

    class FakeGrader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def grade(self, ctx: GraderContext) -> EVMbenchGrade:
            return EVMbenchGrade(
                score=1,
                grader_log="ok",
                evmbench_result=EVMbenchDetectResult(
                    audit_id=ctx.audit.id,
                    score=1,
                    max_score=1,
                    detect_award=1.0,
                    detect_max_award=1.0,
                    details={"judge_results": []},
                ),
            )

    monkeypatch.setattr(modal_grading, "build_grader", lambda *args, **kwargs: FakeGrader())
    outcome = grade_submission(make_request(submission))
    assert outcome.graded
    assert outcome.score == 1.0
    payload = outcome.to_dict()
    assert payload["grade_source"] == "evmbench_detect"
    assert payload["evaluator_model"] == "openai/gpt-6-luna"
    assert payload["submission_sha256"] == hashlib.sha256(b"# audit\n").hexdigest()
    assert payload["score_is_placeholder"] is False
    assert payload["grading_sandbox_id"] is None


def test_patch_grade_uploads_submission_to_fresh_computer(tmp_path, monkeypatch):
    submission = tmp_path / "agent.diff"
    submission.write_text("diff --git a/A b/A\n")
    computer = LocalProcessComputer.create(root=tmp_path / "grade-box")
    seen: dict[str, bytes] = {}

    class FakeGrader:
        def __init__(self, interface, *args, **kwargs) -> None:
            self.interface = interface

        async def grade(self, ctx: GraderContext) -> EVMbenchGrade:
            seen["submission"] = await self.interface.download(submission_remote_path("patch"))
            return EVMbenchGrade(
                score=1,
                grader_log="patched",
                evmbench_result=EVMbenchResult(audit_id="audit-1", score=1, max_score=1),
            )

    monkeypatch.setattr(modal_grading, "build_grader", lambda *args, **kwargs: FakeGrader(args[1] if len(args) > 1 else None))
    try:
        outcome = grade_submission(make_request(submission, mode="patch"), computer=computer)
    finally:
        computer.terminate()
    assert outcome.graded
    assert seen["submission"] == b"diff --git a/A b/A\n"
    assert outcome.payload["grading_sandbox_id"] == computer.identity.sandbox_id


def test_grader_exception_is_recorded_not_zero_with_success(tmp_path, monkeypatch):
    submission = tmp_path / "audit.md"
    submission.write_text("# audit\n")

    class ExplodingGrader:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def grade(self, ctx: GraderContext) -> EVMbenchGrade:
            raise RuntimeError("provider down")

    monkeypatch.setattr(modal_grading, "build_grader", lambda *args, **kwargs: ExplodingGrader())
    outcome = grade_submission(make_request(submission))
    assert not outcome.graded
    assert outcome.score is None
    assert "provider down" in outcome.payload["error"]
    assert outcome.payload["score_is_placeholder"] is False
