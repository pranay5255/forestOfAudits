#!/usr/bin/env python3
"""Prompt helpers for EVMBench forest branch and judge workers."""

from __future__ import annotations

from pathlib import Path

from scout import TreeRole

COMPLETE_COMMAND = "echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT"
FOREST_DIR = "/home/agent/forest"
FINAL_SUBMISSION_PATH = "/home/agent/submission/audit.md"

BRANCH_INSTANCE_TEMPLATE = """First read /home/agent/AGENTS.md and /home/agent/FOREST_ROLE.md.
Then inspect /home/agent/audit through the lens of your assigned specialist role.

Write your branch report to exactly this path:

{{ branch_output_path }}

Do not write /home/agent/submission/audit.md. The final global judge is the
only worker allowed to write the EVMBench submission.

Your branch report should include:

- confirmed or plausible findings, with file/function references
- exploit path and impact assessment
- evidence, commands, tests, or code paths reviewed
- false leads worth passing to the tree-local judge

When the branch report is written and verified, finish by issuing exactly this
command and no other command:

`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`

Task:
{{ task }}"""

TREE_JUDGE_INSTANCE_TEMPLATE = """First read /home/agent/AGENTS.md, /home/agent/FOREST_ROLE.md,
and the branch reports staged under:

{{ branch_inputs_dir }}

Inspect /home/agent/audit as needed to resolve contradictions and reject weak
claims. Write the tree-local merged report to exactly this path:

{{ judge_output_path }}

Do not write /home/agent/submission/audit.md. The final global judge is the
only worker allowed to write the EVMBench submission.

The merged tree report should include only findings that this specialist tree
believes may survive final review, plus concise rationale for rejected branch
claims.

When the tree-local report is written and verified, finish by issuing exactly
this command and no other command:

`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`

Task:
{{ task }}"""

GLOBAL_JUDGE_INSTANCE_TEMPLATE = """First read /home/agent/AGENTS.md and the tree-local judge
reports staged under:

{{ tree_reports_dir }}

Inspect /home/agent/audit as needed to validate the strongest claims. Merge the
specialist reports into the final EVMBench detect submission and write exactly:

/home/agent/submission/audit.md

Only include findings that are concrete, reachable, and material. Preserve the
benchmark-required submission format from /home/agent/AGENTS.md. Do not include
or reference orchestration metadata unless it is directly relevant to a finding.

When /home/agent/submission/audit.md is written and verified, finish by issuing
exactly this command and no other command:

`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`

Task:
{{ task }}"""


def branch_id(branch_index: int) -> str:
    return f"branch-{branch_index:02d}"


def branch_report_remote_path(role: TreeRole, branch_index: int) -> str:
    return f"{FOREST_DIR}/{role.name}/{branch_id(branch_index)}/branch.md"


def tree_judge_remote_path(role: TreeRole) -> str:
    return f"{FOREST_DIR}/{role.name}/judge.md"


def branch_inputs_remote_dir(role: TreeRole) -> str:
    return f"{FOREST_DIR}/{role.name}/branch-inputs"


def tree_reports_remote_dir() -> str:
    return f"{FOREST_DIR}/global/tree-reports"


def local_branch_report_path(output_dir: Path, role: TreeRole, branch_index: int) -> Path:
    return output_dir / "forest" / role.name / branch_id(branch_index) / "branch.md"


def local_tree_judge_path(output_dir: Path, role: TreeRole) -> Path:
    return output_dir / "forest" / role.name / "judge.md"


def build_role_file(role: TreeRole, *, branch_note: str | None = None) -> str:
    sections = [
        f"# {role.title} Specialist",
        "",
        f"Role id: {role.name}",
        "",
        role.description,
        "",
        "Focus:",
        "",
        role.focus,
    ]
    if branch_note:
        sections.extend(["", "Branch note:", "", branch_note])
    return "\n".join(sections).strip() + "\n"


def build_branch_system_template(role: TreeRole, branch_index: int, branch_count: int) -> str:
    return (
        "You are a role-specialized mini-swe-agent worker in an EVMBench "
        "forest-of-auditors run.\n\n"
        f"Specialist role: {role.title} ({role.name}).\n"
        f"Branch: {branch_index} of {branch_count}.\n\n"
        f"{role.focus}"
    )


def build_tree_judge_system_template(role: TreeRole) -> str:
    return (
        "You are the tree-local judge for an EVMBench forest-of-auditors run.\n\n"
        f"Specialist tree: {role.title} ({role.name}).\n\n"
        "Synthesize branch reports, verify the strongest claims, and reject "
        "findings that are not sufficiently concrete."
    )


def build_global_judge_system_template() -> str:
    return (
        "You are the global judge and final merger for an EVMBench "
        "forest-of-auditors run. You are the only worker allowed to write "
        f"{FINAL_SUBMISSION_PATH}."
    )


def build_branch_task(role: TreeRole, branch_index: int, branch_count: int, base_task: str) -> str:
    return (
        f"{base_task}\n\n"
        f"Run specialist branch {branch_index} of {branch_count} for the "
        f"{role.title} tree. Independently search for benchmark-grade smart "
        "contract security findings in this role's focus area."
    )


def build_tree_judge_task(role: TreeRole, base_task: str) -> str:
    return (
        f"{base_task}\n\n"
        f"Merge and validate the branch reports for the {role.title} tree. "
        "Keep only concrete findings or high-signal leads for global review."
    )


def build_global_judge_task(base_task: str) -> str:
    return (
        f"{base_task}\n\n"
        "Merge all tree-local reports into the final EVMBench-compatible audit report."
    )
