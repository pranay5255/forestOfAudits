from __future__ import annotations

import json
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Literal

from evmbench.agents.agent import agent_registry
from evmbench.audit import Audit, audit_registry
from evmbench.constants import AGENT_DIR, SUBMISSION_DIR
from evmbench.nano.task import EVMTask
from evmbench.utils import get_default_runs_dir, get_project_version

HintLevel = Literal["none", "low", "med", "high", "max"]
FindingsSubdir = Literal["", "low", "medium", "high"]

DETECT_SUBMISSION_PATH = f"{SUBMISSION_DIR}/audit.md"
DEFAULT_VERIFIER_TIMEOUT_SEC = 900.0
DEFAULT_AGENT_TIMEOUT_SEC = 10 * 3600.0


@dataclass(frozen=True)
class HarborDetectTaskSpec:
    audit_id: str
    hint_level: HintLevel = "none"
    findings_subdir: FindingsSubdir = ""
    image_version: str | None = None
    task_org: str = "evmbench"
    verifier_timeout_sec: float = DEFAULT_VERIFIER_TIMEOUT_SEC
    agent_timeout_sec: float = DEFAULT_AGENT_TIMEOUT_SEC
    cpus: int = 1
    memory_mb: int = 4096
    storage_mb: int = 20480
    allow_internet: bool = False
    include_source: bool = False

    @property
    def mode(self) -> Literal["detect"]:
        return "detect"

    @property
    def task_name(self) -> str:
        suffix = f"-hints-{self.hint_level}" if self.hint_level != "none" else ""
        findings = f"-findings-{self.findings_subdir}" if self.findings_subdir else ""
        return f"{self.task_org}/detect-{self.audit_id}{suffix}{findings}"

    @property
    def directory_name(self) -> str:
        return self.task_name.replace("/", "__")


def default_harbor_dataset_root() -> Path:
    return Path(get_default_runs_dir()) / "harbor-datasets"


def _toml_string(value: str) -> str:
    return json.dumps(value)


def _toml_bool(value: bool) -> str:
    return "true" if value else "false"


def _toml_array(values: Iterable[str]) -> str:
    return "[" + ", ".join(_toml_string(value) for value in values) + "]"


def _docker_image_for_audit(audit: Audit, image_version: str | None) -> str:
    image = audit.docker_image
    if image_version:
        image = f"{image}-{image_version}"
    return image


def _audit_for_spec(spec: HarborDetectTaskSpec) -> Audit:
    return audit_registry.get_audit(spec.audit_id, findings_subdir=spec.findings_subdir)


def build_evm_task_for_harbor(
    spec: HarborDetectTaskSpec,
    *,
    run_dir: str | Path | None = None,
) -> EVMTask:
    """Build the same ``EVMTask`` shape used by nano for a Harbor detect task."""

    audit = _audit_for_spec(spec)
    instruction = agent_registry.load_instructions("detect", audit, spec.hint_level)
    run_id = f"harbor-detect-{spec.audit_id}"
    resolved_run_dir = str(run_dir or default_harbor_dataset_root() / "_runs" / run_id)
    return EVMTask(
        question_id=run_id,
        prompt=[{"role": "user", "content": instruction}],
        cwd=AGENT_DIR,
        docker_image=_docker_image_for_audit(audit, spec.image_version),
        audit=audit,
        mode="detect",
        run_id=run_id,
        run_group_id="harbor-dataset",
        runs_dir=str(Path(resolved_run_dir).parent),
        run_dir=resolved_run_dir,
        apply_gold_solution=False,
        apply_max_solution=False,
        remove_artifacts=False,
        remove_forge_artifacts=True,
        version=get_project_version(),
        log_to_run_dir=False,
        hint_level=spec.hint_level,
        use_sidecar=False,
    )


def _task_toml(spec: HarborDetectTaskSpec, task: EVMTask) -> str:
    artifacts = [
        DETECT_SUBMISSION_PATH,
        "/logs/agent",
        "/logs/artifacts",
    ]
    verifier_env = {
        "OPENAI_API_KEY": "${OPENAI_API_KEY}",
        "OPENAI_BASE_URL": "${OPENAI_BASE_URL}",
        "OPENAI_API_BASE": "${OPENAI_API_BASE}",
        "EVMBENCH_HARBOR_JUDGE_MODEL": "${EVMBENCH_HARBOR_JUDGE_MODEL}",
        "EVMBENCH_HARBOR_REASONING_EFFORT": "${EVMBENCH_HARBOR_REASONING_EFFORT}",
    }
    verifier_env_toml = "{ " + ", ".join(
        f"{key} = {_toml_string(value)}" for key, value in verifier_env.items()
    ) + " }"

    return "\n".join(
        [
            'schema_version = "1.1"',
            f"artifacts = {_toml_array(artifacts)}",
            "",
            "[task]",
            f"name = {_toml_string(spec.task_name)}",
            f"description = {_toml_string(f'EVMBench detect task for {spec.audit_id}')}",
            'authors = [{ name = "EVMBench" }]',
            'keywords = ["evmbench", "smart-contract-audit", "detect"]',
            "",
            "[metadata]",
            f"evmbench_audit_id = {_toml_string(spec.audit_id)}",
            'evmbench_mode = "detect"',
            f"hint_level = {_toml_string(spec.hint_level)}",
            f"findings_subdir = {_toml_string(spec.findings_subdir)}",
            f"submission_path = {_toml_string(DETECT_SUBMISSION_PATH)}",
            'verifier_module = "evmbench.harbor_adapter.verifier"',
            "",
            "[verifier]",
            f"timeout_sec = {spec.verifier_timeout_sec}",
            "user = \"root\"",
            f"env = {verifier_env_toml}",
            "",
            "[agent]",
            f"timeout_sec = {spec.agent_timeout_sec}",
            'user = "root"',
            "",
            "[environment]",
            f"docker_image = {_toml_string(str(task.docker_image))}",
            'os = "linux"',
            f"cpus = {spec.cpus}",
            f"memory_mb = {spec.memory_mb}",
            f"storage_mb = {spec.storage_mb}",
            "gpus = 0",
            f"allow_internet = {_toml_bool(spec.allow_internet)}",
            "",
        ]
    )


def _test_script(spec: HarborDetectTaskSpec) -> str:
    args = [
        "--audit-id",
        spec.audit_id,
        "--mode",
        "detect",
        "--hint-level",
        spec.hint_level,
        "--findings-subdir",
        spec.findings_subdir,
        "--agent-output-path",
        DETECT_SUBMISSION_PATH,
    ]
    rendered_args = " ".join(json.dumps(arg) for arg in args)
    return "\n".join(
        [
            "#!/usr/bin/env bash",
            "set -euo pipefail",
            "mkdir -p /logs/verifier /logs/artifacts",
            "python3 /tests/evmbench_harbor_verifier.py " + rendered_args,
            "",
        ]
    )


def _verifier_wrapper() -> str:
    return '''#!/usr/bin/env python3
"""Bootstraps the EVMBench Harbor verifier inside a Harbor task container."""

from __future__ import annotations

import os
import runpy
import sys
from pathlib import Path

extra_path = os.environ.get("EVMBENCH_HARBOR_PYTHONPATH", "/tests/evmbench-src")
if extra_path and Path(extra_path).exists():
    sys.path.insert(0, extra_path)

runpy.run_module("evmbench.harbor_adapter.verifier", run_name="__main__")
'''


def _copy_source_tree(destination: Path) -> None:
    package_root = Path(__file__).resolve().parents[1]
    target = destination / "evmbench"
    if target.exists():
        shutil.rmtree(target)
    shutil.copytree(
        package_root,
        target,
        ignore=shutil.ignore_patterns("__pycache__", "*.pyc", ".mypy_cache", ".pytest_cache"),
    )


def generate_detect_task(
    spec: HarborDetectTaskSpec,
    output_dir: str | Path,
    *,
    overwrite: bool = False,
) -> Path:
    task_dir = Path(output_dir)
    if task_dir.exists():
        if not overwrite:
            raise FileExistsError(f"Harbor task directory already exists: {task_dir}")
        shutil.rmtree(task_dir)

    task = build_evm_task_for_harbor(spec, run_dir=task_dir / ".evmbench-run")
    instruction = task.prompt[0]["content"]
    if not isinstance(instruction, str):
        raise TypeError("Rendered EVMBench instruction must be a string.")

    (task_dir / "environment").mkdir(parents=True, exist_ok=True)
    (task_dir / "tests").mkdir(parents=True, exist_ok=True)
    (task_dir / "instruction.md").write_text(instruction, encoding="utf-8")
    (task_dir / "task.toml").write_text(_task_toml(spec, task), encoding="utf-8")
    (task_dir / "environment" / "README.md").write_text(
        "\n".join(
            [
                "# EVMBench Audit Image",
                "",
                "This task uses the prebuilt Docker image declared in `task.toml`.",
                f"Image: `{task.docker_image}`",
                "",
            ]
        ),
        encoding="utf-8",
    )
    test_script = task_dir / "tests" / "test.sh"
    test_script.write_text(_test_script(spec), encoding="utf-8")
    os.chmod(test_script, 0o755)
    verifier_wrapper = task_dir / "tests" / "evmbench_harbor_verifier.py"
    verifier_wrapper.write_text(_verifier_wrapper(), encoding="utf-8")
    os.chmod(verifier_wrapper, 0o755)

    if spec.include_source:
        _copy_source_tree(task_dir / "tests" / "evmbench-src")

    return task_dir


def generate_detect_dataset(
    audit_ids: Iterable[str],
    output_dir: str | Path | None = None,
    *,
    hint_level: HintLevel = "none",
    findings_subdir: FindingsSubdir = "",
    image_version: str | None = None,
    overwrite: bool = False,
    include_source: bool = False,
) -> list[Path]:
    dataset_dir = Path(output_dir) if output_dir is not None else default_harbor_dataset_root()
    dataset_dir.mkdir(parents=True, exist_ok=True)

    generated: list[Path] = []
    for audit_id in audit_ids:
        spec = HarborDetectTaskSpec(
            audit_id=audit_id,
            hint_level=hint_level,
            findings_subdir=findings_subdir,
            image_version=image_version,
            include_source=include_source,
        )
        generated.append(
            generate_detect_task(spec, dataset_dir / spec.directory_name, overwrite=overwrite)
        )
    return generated
