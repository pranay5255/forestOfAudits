from __future__ import annotations

import subprocess
import uuid
from pathlib import Path

from evmbench.agents.backend.local import LocalProcessComputer
from evmbench.agents.forest.artifacts import ArtifactRequest
from evmbench.agents.forest.pipeline import (
    Cancellation,
    ForestPipeline,
    TaskDefinition,
    plan_stage_counts,
)
from evmbench.agents.forest.prompts import scope_text
from evmbench.agents.harness.base import BaseHarnessAdapter, StageSpec
from evmbench.audit import Audit, Vulnerability
from evmbench.campaign.state import BudgetSpec, CampaignConfig, CampaignStore


class FakeArtifactsAdapter(BaseHarnessAdapter):
    """Writes the artifacts each stage is expected to produce."""

    name = "opencode"
    cli_version = "test"

    def __init__(self, *, fail_workers: set[str] | None = None, on_run=None) -> None:
        self.fail_workers = fail_workers or set()
        self.on_run = on_run
        self.calls: list[str] = []

    def build_command(self, spec: StageSpec) -> str:
        self.calls.append(spec.worker_id)
        if self.on_run:
            self.on_run(spec)
        lines = [
            "set -eu",
            "mkdir -p /home/logs",
            "printf '{\"type\":\"tool_use\"}\\n{\"type\":\"tool_use\"}\\n' > /home/logs/traj.jsonl",
        ]
        if spec.stage == "scout":
            lines.append("mkdir -p /home/agent/forest/scout")
            lines.append("printf '# scout\\n' > /home/agent/forest/scout/scout.md")
            lines.append(
                "printf '{\"summary\":\"s\",\"recommended_roles\":[\"accounting\",\"token-flow\"]}' "
                "> /home/agent/forest/scout/scout.json"
            )
        elif spec.stage == "branch":
            lines.append(f"mkdir -p /home/agent/forest/{spec.role}/branch-{spec.branch_index:02d}")
            lines.append(
                f"printf '# branch\\n' > /home/agent/forest/{spec.role}/branch-{spec.branch_index:02d}/branch.md"
            )
            if spec.mode == "patch":
                lines.append("printf '// branch change\\n' >> /home/agent/audit/A.sol")
                lines.append(
                    f"printf 'diff --git a/x b/x\\n' > /home/agent/forest/{spec.role}/branch-{spec.branch_index:02d}/branch.diff"
                )
            if spec.mode == "exploit":
                lines.append(
                    f"printf '{{\"transactions\":[]}}' > /home/agent/forest/{spec.role}/branch-{spec.branch_index:02d}/branch.txs.json"
                )
                lines.append(
                    f"printf '{{\"chain_isolation\":\"single_sandbox\"}}' > /home/agent/forest/{spec.role}/branch-{spec.branch_index:02d}/branch.chain.json"
                )
        elif spec.stage == "tree_judge":
            lines.append(f"mkdir -p /home/agent/forest/{spec.role}")
            lines.append(f"printf '# judge\\n' > /home/agent/forest/{spec.role}/judge.md")
        elif spec.stage == "global_merger":
            lines.append("mkdir -p /home/agent/submission")
            if spec.mode == "patch":
                lines.append("printf '// merge change\\n' >> /home/agent/audit/A.sol")
            lines.append(f"printf '# final\\n' > {spec.output_paths[0]}")
        elif spec.stage == "baseline":
            lines.append("mkdir -p /home/agent/submission")
            lines.append(f"printf '# baseline\\n' > {spec.output_paths[0]}")
        if spec.worker_id in self.fail_workers:
            lines.append("exit 9")
        return "\n".join(lines)

    def trajectory_request(self, spec: StageSpec) -> ArtifactRequest:
        return ArtifactRequest("trajectory", "/home/logs/traj.jsonl", "trajectory.jsonl")


def make_audit(audit_id: str = "audit-1", *, base_commit: str | None = None, mappings: int = 0) -> Audit:
    vulnerabilities = []
    for index in range(max(mappings, 1)):
        mapping = None
        if mappings:
            mapping = {f"patch/p{index}.sol": f"src/Secret{index}.sol"}
        vulnerabilities.append(
            Vulnerability(
                id=f"v{index}",
                audit_id=audit_id,
                title=f"v{index}",
                patch_path_mapping=mapping,
                test_path_mapping={f"test/t{index}.sol": f"test/Secret{index}.t.sol"} if mappings else None,
            )
        )
    return Audit(id=audit_id, vulnerabilities=vulnerabilities, base_commit=base_commit)


def make_config(tmp_path: Path, **overrides) -> CampaignConfig:
    data = dict(
        backend="local",
        harness="opencode",
        strategy="forest",
        mode="detect",
        audit_ids=("audit-1",),
        branches_per_tree=1,
        tree_roles=("accounting", "token-flow"),
        max_tree_roles=2,
        worker_concurrency=2,
        sandbox_cap=20,
        output_dir=str(tmp_path / "campaigns"),
        budget=BudgetSpec(task_time_limit=60, sandbox_timeout=120),
    )
    data.update(overrides)
    return CampaignConfig(**data)


def make_box_factory(tmp_path: Path, *, init_git: bool = False):
    roots: list[Path] = []

    def factory(spec: StageSpec):
        root = tmp_path / "boxes" / f"{spec.worker_id}-{uuid.uuid4().hex[:6]}"
        computer = LocalProcessComputer.create(
            root=root,
            campaign_id=spec.campaign_id,
            task_id=spec.task_id,
            attempt_id=spec.attempt_id,
            stage=spec.stage,
            worker_id=spec.worker_id,
        )
        if init_git:
            audit_dir = root / "agent" / "audit"
            audit_dir.mkdir(parents=True, exist_ok=True)
            subprocess.run(["git", "init", "-q"], cwd=audit_dir, check=True)
            subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=audit_dir, check=True)
            subprocess.run(["git", "config", "user.name", "a"], cwd=audit_dir, check=True)
            (audit_dir / "A.sol").write_text("contract A {}\n")
            subprocess.run(["git", "add", "."], cwd=audit_dir, check=True)
            subprocess.run(["git", "commit", "-qm", "init"], cwd=audit_dir, check=True)
        roots.append(root)
        return computer

    factory.roots = roots  # type: ignore[attr-defined]
    return factory


def make_pipeline(tmp_path: Path, config: CampaignConfig, adapter, audit: Audit, **kwargs):
    store = CampaignStore(Path(config.output_dir))
    campaign_id = store.create(config)
    factory = kwargs.pop("sandbox_factory", None) or make_box_factory(tmp_path)
    return (
        ForestPipeline(
            config=config,
            store=store,
            adapter=adapter,
            sandbox_factory=factory,
            task=TaskDefinition(audit=audit, instructions="INSTRUCTIONS", image="image:test"),
            campaign_id=campaign_id,
            **kwargs,
        ),
        store,
        campaign_id,
        factory,
    )


def test_branch_count_is_roles_times_branches_even_with_gold_mappings(tmp_path):
    config = make_config(tmp_path, branches_per_tree=2, mode="patch")
    audit = make_audit(base_commit="deadbeef", mappings=5)
    pipeline, *_ = make_pipeline(tmp_path, config, FakeArtifactsAdapter(), audit)
    specs = pipeline._branch_specs(0, ("accounting", "token-flow"))
    assert len(specs) == 2 * 2
    assert len({spec.worker_id for spec in specs}) == len(specs)


def test_no_gold_mapping_paths_in_agent_visible_staging(tmp_path):
    config = make_config(tmp_path, branches_per_tree=1, mode="patch")
    audit = make_audit(base_commit="deadbeef", mappings=4)
    pipeline, *_ = make_pipeline(tmp_path, config, FakeArtifactsAdapter(), audit)
    specs = pipeline._branch_specs(0, ("accounting",))
    staged_text = ""
    for spec in specs:
        staged_text += spec.instructions + spec.task
        for staged in spec.staged_artifacts:
            staged_text += staged.local_path.read_text(encoding="utf-8")
    for vulnerability in audit.vulnerabilities:
        for mapping in (vulnerability.patch_path_mapping or {}), (vulnerability.test_path_mapping or {}):
            for remote_path in mapping.values():
                relative = remote_path.split("/audit/", 1)[-1]
                assert relative not in staged_text
    assert "Secret" not in staged_text
    # The neutral scope note is the only scope input.
    assert scope_text() in staged_text or "full /home/agent/audit workspace is in scope" in staged_text


def test_full_detect_forest_produces_six_workers_and_submission(tmp_path):
    config = make_config(tmp_path)
    audit = make_audit()
    adapter = FakeArtifactsAdapter()
    pipeline, store, campaign_id, _ = make_pipeline(tmp_path, config, adapter, audit)
    outcome = pipeline.run_attempt(0)

    assert outcome.status == "completed", outcome.error
    assert outcome.selected_roles == ("accounting", "token-flow")
    stages = sorted(result.stage for result in outcome.results)
    assert stages.count("scout") == 1
    assert stages.count("branch") == 2
    assert stages.count("tree_judge") == 2
    assert stages.count("global_merger") == 1
    assert len(outcome.results) == 6
    assert outcome.submission_path is not None
    assert outcome.submission_path.read_text() == "# final\n"
    assert plan_stage_counts(config)["workers_total"] == 6

    state = store.load_campaign(campaign_id)
    assert set(state["tasks"]["audit-1"]["attempts"]["0"]["stages"]) == {
        "scout",
        "accounting-branch-01",
        "token-flow-branch-01",
        "accounting-judge",
        "token-flow-judge",
        "global-merger",
    }


def test_branch_failure_without_continue_stops_attempt(tmp_path):
    config = make_config(tmp_path)
    audit = make_audit()
    adapter = FakeArtifactsAdapter(fail_workers={"accounting-branch-01"})
    pipeline, store, campaign_id, _ = make_pipeline(tmp_path, config, adapter, audit)
    outcome = pipeline.run_attempt(0)

    assert outcome.status == "failed"
    assert "branch failures" in (outcome.error or "")
    assert outcome.submission_path is None
    events = store.read_events(campaign_id)
    assert any(event["event"] == "attempt_failed" for event in events)


def test_branch_failure_with_continue_is_degraded_not_success(tmp_path):
    config = make_config(tmp_path, continue_on_worker_error=True)
    audit = make_audit()
    adapter = FakeArtifactsAdapter(fail_workers={"accounting-branch-01"})
    pipeline, *_ = make_pipeline(tmp_path, config, adapter, audit)
    outcome = pipeline.run_attempt(0)

    assert outcome.status == "degraded"
    assert outcome.submission_path is not None
    assert any(result.worker_id == "accounting-branch-01" and not result.completed for result in outcome.results)


def test_resume_skips_completed_stages(tmp_path):
    config = make_config(tmp_path)
    audit = make_audit()
    first_adapter = FakeArtifactsAdapter()
    pipeline, store, campaign_id, factory = make_pipeline(tmp_path, config, first_adapter, audit)
    first = pipeline.run_attempt(0)
    assert first.status == "completed"
    calls_after_first = len(factory.roots)

    second_adapter = FakeArtifactsAdapter()
    pipeline2 = ForestPipeline(
        config=config,
        store=store,
        adapter=second_adapter,
        sandbox_factory=factory,
        task=TaskDefinition(audit=audit, instructions="INSTRUCTIONS", image="image:test"),
        campaign_id=campaign_id,
    )
    second = pipeline2.run_attempt(0)
    assert second.status == "completed"
    assert len(factory.roots) == calls_after_first
    assert second_adapter.calls == []


def test_cancellation_leaves_resumable_state(tmp_path):
    config = make_config(tmp_path)
    audit = make_audit()
    cancellation = Cancellation()
    adapter = FakeArtifactsAdapter(on_run=lambda spec: cancellation.cancel() if spec.stage == "branch" else None)
    pipeline, store, campaign_id, _ = make_pipeline(
        tmp_path, config, adapter, audit, cancellation=cancellation
    )
    outcome = pipeline.run_attempt(0)
    assert outcome.status == "cancelled"
    assert store.campaign_path(campaign_id).exists()
    assert store.read_events(campaign_id)


def test_patch_pipeline_captures_agent_diff(tmp_path):
    # Use an existing git repo as the audit workspace for every sandbox.
    config = make_config(tmp_path, mode="patch", tree_roles=("accounting",))
    repo = tmp_path / "repo"
    repo.mkdir()
    subprocess.run(["git", "init", "-q"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.email", "a@b.c"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "a"], cwd=repo, check=True)
    (repo / "A.sol").write_text("contract A {}\n")
    subprocess.run(["git", "add", "."], cwd=repo, check=True)
    subprocess.run(["git", "commit", "-qm", "init"], cwd=repo, check=True)
    base = subprocess.run(["git", "rev-parse", "HEAD"], cwd=repo, capture_output=True, text=True, check=True).stdout.strip()

    audit = make_audit(base_commit=base)

    def factory(spec: StageSpec):
        computer = LocalProcessComputer.create(root=tmp_path / "boxes" / f"{spec.worker_id}-{uuid.uuid4().hex[:6]}")
        computer.exec(f"git clone -q {repo} /home/agent/audit || true")
        return computer

    pipeline, *_ = make_pipeline(
        tmp_path, config, FakeArtifactsAdapter(), audit, sandbox_factory=factory
    )
    outcome = pipeline.run_attempt(0)
    assert outcome.status == "completed", outcome.error
    assert outcome.submission_path is not None
    diff = outcome.submission_path.read_text()
    assert "diff --git" in diff
    check = subprocess.run(
        ["git", "apply", "--check", str(outcome.submission_path)],
        cwd=repo,
        capture_output=True,
        text=True,
    )
    assert check.returncode == 0, check.stderr


def test_exploit_bundles_are_never_concatenated(tmp_path):
    config = make_config(
        tmp_path,
        mode="exploit",
        audit_ids=("2024-01-curves",),
        branches_per_tree=2,
        tree_roles=("accounting", "token-flow"),
    )
    audit = make_audit("2024-01-curves", base_commit="deadbeef")
    pipeline, *_ = make_pipeline(tmp_path, config, FakeArtifactsAdapter(), audit)
    branch_specs = pipeline._branch_specs(0, ("accounting", "token-flow"))
    assert len(branch_specs) == 4
    for spec in branch_specs:
        assert spec.postprocess_command
        assert "branch.txs.json" in spec.postprocess_command
        assert "exec-txs" not in spec.postprocess_command


def test_artifact_manifests_reject_traversal_and_hash_mismatch(tmp_path):
    import io
    import tarfile

    import pytest

    from evmbench.agents.forest.artifacts import (
        Artifact,
        ArtifactError,
        ArtifactRequest,
        safe_extract_tar,
        sha256_bytes,
        verify_manifest,
        write_manifest,
    )

    with pytest.raises(ArtifactError):
        ArtifactRequest("report", "/home/agent/x", "../escape.md")

    payload = b"hello"
    target = tmp_path / "a.md"
    target.write_text("hello")
    manifest = write_manifest(
        tmp_path / "manifest.json",
        [Artifact("report", "a.md", sha256_bytes(payload), len(payload))],
    )
    assert manifest["artifacts"]
    assert verify_manifest(tmp_path / "manifest.json") == []
    target.write_text("tampered")
    violations = verify_manifest(tmp_path / "manifest.json")
    assert any("hash mismatch" in violation for violation in violations)

    buffer = io.BytesIO()
    with tarfile.open(fileobj=buffer, mode="w") as archive:
        info = tarfile.TarInfo("../escape.txt")
        data = b"x"
        info.size = len(data)
        archive.addfile(info, io.BytesIO(data))
    with pytest.raises(ArtifactError):
        safe_extract_tar(buffer.getvalue(), tmp_path / "extract")


def test_merger_receives_one_bundle_per_branch(tmp_path):
    config = make_config(
        tmp_path, mode="exploit", audit_ids=("2024-01-curves",), tree_roles=("accounting",)
    )
    audit = make_audit("2024-01-curves", base_commit="deadbeef")
    adapter = FakeArtifactsAdapter()

    def factory(spec: StageSpec):
        return LocalProcessComputer.create(root=tmp_path / "boxes" / f"{spec.worker_id}-{uuid.uuid4().hex[:6]}")

    pipeline, *_ = make_pipeline(tmp_path, config, adapter, audit, sandbox_factory=factory)
    # Run only the branch stage to collect branch artifacts.  The fake adapter
    # writes the bundle itself, so skip the real ploit postprocess command.
    from dataclasses import replace

    branch_specs = [replace(spec, postprocess_command="") for spec in pipeline._branch_specs(0, ("accounting",))]
    results = pipeline._run_parallel(branch_specs, 0)
    merger = pipeline._merger_spec(0, ("accounting",), results, [])
    txs = [staged for staged in merger.staged_artifacts if staged.remote_path.endswith(".txs.json")]
    chain = [staged for staged in merger.staged_artifacts if staged.remote_path.endswith(".chain.json")]
    assert len(txs) == 1
    assert len(chain) == 1
    assert len({staged.remote_path for staged in txs}) == 1
    assert all("global/branch-inputs" in staged.remote_path for staged in txs)
