from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from evmbench.agents.forest.artifacts import Artifact, StageContract
from evmbench.agents.forest.pipeline import plan_stage_counts
from evmbench.agents.harness.base import WorkerResult, WorkerStatus
from evmbench.campaign.state import (
    BudgetSpec,
    CampaignConfig,
    CampaignStatus,
    CampaignStore,
    ConfigError,
    StageKey,
    load_campaign_config,
)


def make_config(**overrides) -> CampaignConfig:
    data = dict(
        backend="local",
        harness="opencode",
        strategy="forest",
        mode="detect",
        audit_ids=("2024-01-canto",),
        tree_roles=("accounting", "token-flow"),
        branches_per_tree=1,
        budget=BudgetSpec(task_time_limit=60, sandbox_timeout=120),
    )
    data.update(overrides)
    return CampaignConfig(**data)


def test_config_validation_rejects_bad_values():
    with pytest.raises(ConfigError):
        make_config(audit_ids=()).validate()
    with pytest.raises(ConfigError):
        make_config(harness="cursor").validate()
    with pytest.raises(ConfigError):
        make_config(mode="detect", hint_level="max").validate()
    with pytest.raises(ConfigError):
        make_config(branches_per_tree=0).validate()
    with pytest.raises(ConfigError):
        make_config(budget=BudgetSpec(task_time_limit=120, sandbox_timeout=120)).validate()
    with pytest.raises(ConfigError):
        make_config(backend="modal", image_repo="evmbench/audit").validate()
    make_config(backend="modal", image_repo="ghcr.io/acme/evmbench/audit").validate()
    make_config(backend="modal", image_repo="localhost:5000/evmbench/audit").validate()


def test_image_override_takes_precedence():
    config = make_config(
        image_repo="example.com/evmbench/audit",
        image_overrides={"2024-01-canto": "example.com/evmbench/audit:2024-01-canto-pinned"},
    )
    assert config.image_for("2024-01-canto") == "example.com/evmbench/audit:2024-01-canto-pinned"
    assert config.image_for("2024-02-other") == "example.com/evmbench/audit:2024-02-other"


def test_plan_stage_counts_match_contract():
    config = make_config()
    counts = plan_stage_counts(config)
    assert counts["branches"] == 2
    assert counts["tree_judges"] == 2
    assert counts["workers_total"] == 6
    assert config.expected_worker_count(2) == 6

    baseline = make_config(strategy="baseline")
    assert plan_stage_counts(baseline)["workers_total"] == 1


def test_campaign_round_trip_and_resume(tmp_path):
    store = CampaignStore(tmp_path)
    config = make_config()
    campaign_id = store.create(config)
    state = store.load_campaign(campaign_id)
    assert state["status"] == CampaignStatus.PLANNED.value
    assert store.config(campaign_id) == config

    store.set_status(campaign_id, CampaignStatus.RUNNING)
    assert store.resume(campaign_id)["status"] == CampaignStatus.RUNNING
    store.set_status(campaign_id, CampaignStatus.COMPLETED)
    with pytest.raises(ConfigError):
        store.resume(campaign_id)


def test_cancel_flag_round_trip(tmp_path):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(make_config())
    assert not store.cancellation_requested(campaign_id)
    store.request_cancel(campaign_id, "test")
    assert store.cancellation_requested(campaign_id)
    store.clear_cancel(campaign_id)
    assert not store.cancellation_requested(campaign_id)


def _write_completed_stage(store: CampaignStore, key: StageKey, artifact_name: str) -> Path:
    stage_dir = store.stage_dir(key)
    artifact_root = stage_dir / "artifacts"
    artifact_root.mkdir(parents=True, exist_ok=True)
    target = artifact_root / artifact_name
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("content")
    payload = target.read_bytes()
    result = WorkerResult(
        campaign_id=key.campaign_id,
        task_id=key.task_id,
        attempt_id=key.attempt_id,
        stage=key.stage,
        worker_id=key.worker_id,
        harness="opencode",
        strategy="forest",
        mode="detect",
        model="m",
        status=WorkerStatus.COMPLETED,
        exit_code=0,
        failure_reason=None,
        artifacts=(
            Artifact(
                kind="report",
                path=artifact_name,
                sha256=hashlib.sha256(payload).hexdigest(),
                bytes=len(payload),
            ),
        ),
        contract=StageContract(stage=key.stage),
    )
    store.write_result(key, result)
    return artifact_root / artifact_name


def test_stage_complete_requires_intact_artifacts(tmp_path):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(make_config())
    key = StageKey(campaign_id, "2024-01-canto", 0, "branch", "accounting-branch-01")
    path = _write_completed_stage(store, key, "accounting-branch-01/branch.md")
    assert store.stage_complete(key)
    path.unlink()
    assert not store.stage_complete(key)


def test_summary_aggregates_stage_statuses(tmp_path):
    store = CampaignStore(tmp_path)
    campaign_id = store.create(make_config())
    key = StageKey(campaign_id, "2024-01-canto", 0, "branch", "accounting-branch-01")
    _write_completed_stage(store, key, "accounting-branch-01/branch.md")
    completed = store.read_result(key)
    assert completed is not None
    store.record_stage(campaign_id, "2024-01-canto", 0, completed)
    failed = WorkerResult(
        campaign_id=campaign_id,
        task_id="2024-01-canto",
        attempt_id=0,
        stage="branch",
        worker_id="token-flow-branch-01",
        harness="opencode",
        strategy="forest",
        mode="detect",
        model="m",
        status=WorkerStatus.FAILED,
        exit_code=9,
        failure_reason="exit_code=9",
    )
    store.record_stage(campaign_id, "2024-01-canto", 0, failed)
    store.write_grade(campaign_id, "2024-01-canto", 0, {"graded": True, "score": 2, "max_score": 3})
    summary = store.summary(campaign_id)
    assert summary["stage_status"]["completed"] == 1
    assert summary["stage_status"]["failed"] == 1
    assert summary["graded_tasks"] == 1
    assert summary["score_sum"] == 2


def test_json_config_round_trip(tmp_path):
    config = make_config()
    path = tmp_path / "config.json"
    path.write_text(json.dumps(config.to_dict()))
    assert load_campaign_config(path) == config


def test_coordinator_runs_task_and_grades(tmp_path, monkeypatch):
    from evmbench.campaign import coordinator as coordinator_module
    from evmbench.nano.grade.modal import GradeOutcome
    from tests.test_forest_pipeline import FakeArtifactsAdapter, make_box_factory

    config = make_config(
        audit_ids=("2024-01-canto",),
        tree_roles=("accounting", "token-flow"),
        output_dir=str(tmp_path / "campaigns"),
    )
    store = CampaignStore(Path(config.output_dir))
    campaign_id = store.create(config)

    monkeypatch.setattr(coordinator_module, "get_adapter", lambda harness: FakeArtifactsAdapter())
    monkeypatch.setattr(coordinator_module.CampaignCoordinator, "preflight", lambda self, refresh=False: {})
    captured = {}

    def fake_grade(request, *, computer=None, timeout_seconds=3600.0):
        captured["submission"] = request.submission_path
        return GradeOutcome(
            graded=True,
            score=1,
            max_score=2,
            payload={"graded": True, "score": 1, "max_score": 2, "score_is_placeholder": False},
        )

    monkeypatch.setattr(coordinator_module, "grade_submission", fake_grade)
    coordinator = coordinator_module.CampaignCoordinator(
        campaign_id=campaign_id,
        store=store,
        config=config,
        sandbox_factory=make_box_factory(tmp_path),
    )
    assert coordinator.run() == 0
    state = store.load_campaign(campaign_id)
    assert state["status"] == CampaignStatus.COMPLETED.value
    grade = store.read_grade(campaign_id, "2024-01-canto", 0)
    assert grade is not None and grade["score"] == 1
    assert captured["submission"].name == "audit.md"
    attempt = json.loads(
        (store.attempt_dir(campaign_id, "2024-01-canto", 0) / "attempt.json").read_text()
    )
    assert len(attempt["workers"]) == 6


def test_coordinator_returns_task_failure_when_grading_fails(tmp_path, monkeypatch):
    from evmbench.campaign import coordinator as coordinator_module
    from evmbench.nano.grade.modal import GradeOutcome
    from tests.test_forest_pipeline import FakeArtifactsAdapter, make_box_factory

    config = make_config(
        audit_ids=("2024-01-canto",),
        strategy="baseline",
        output_dir=str(tmp_path / "campaigns"),
    )
    store = CampaignStore(Path(config.output_dir))
    campaign_id = store.create(config)
    monkeypatch.setattr(coordinator_module, "get_adapter", lambda harness: FakeArtifactsAdapter())
    monkeypatch.setattr(coordinator_module.CampaignCoordinator, "preflight", lambda self, refresh=False: {})
    monkeypatch.setattr(
        coordinator_module,
        "grade_submission",
        lambda request, **kwargs: GradeOutcome(
            graded=False, score=None, max_score=None, payload={"error": "grader down"}
        ),
    )
    coordinator = coordinator_module.CampaignCoordinator(
        campaign_id=campaign_id,
        store=store,
        config=config,
        sandbox_factory=make_box_factory(tmp_path),
    )
    assert coordinator.run() == 1
    state = store.load_campaign(campaign_id)
    assert state["status"] == CampaignStatus.FAILED.value
