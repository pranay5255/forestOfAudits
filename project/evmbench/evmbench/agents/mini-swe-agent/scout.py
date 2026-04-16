#!/usr/bin/env python3
"""Scout role definitions for the EVMBench mini-swe-agent forest runner."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Iterable


@dataclass(frozen=True)
class TreeRole:
    name: str
    title: str
    description: str
    focus: str


@dataclass(frozen=True)
class ScoutDecision:
    summary: str
    recommended_roles: tuple[str, ...]
    role_rationale: dict[str, str]

    def to_dict(self) -> dict[str, Any]:
        payload = asdict(self)
        payload["recommended_roles"] = list(self.recommended_roles)
        return payload


TREE_ROLES: dict[str, TreeRole] = {
    "token-flow": TreeRole(
        name="token-flow",
        title="Token Flow",
        description="Trace asset movements, approvals, accounting side effects, and value conservation.",
        focus=(
            "Follow deposits, withdrawals, transfers, mints, burns, permit flows, fee paths, "
            "rounding loss, and any place where balances or allowances can be moved without "
            "the expected economic constraint."
        ),
    ),
    "accounting": TreeRole(
        name="accounting",
        title="Accounting",
        description="Inspect shares, debt, solvency, exchange rates, and invariant drift.",
        focus=(
            "Prioritize share-price math, utilization math, reward accrual, stale indexes, "
            "rounding direction, liquidation math, oracle scaling, and invariant preservation."
        ),
    ),
    "access-control": TreeRole(
        name="access-control",
        title="Access Control",
        description="Review authorization, initialization, governance, upgrade, and privileged paths.",
        focus=(
            "Look for missing modifiers, confused ownership, initializer mistakes, role escalation, "
            "unsafe delegatecall or upgrade hooks, and admin-only flows reachable by untrusted users."
        ),
    ),
    "cross-contract": TreeRole(
        name="cross-contract",
        title="Cross Contract",
        description="Analyze trust boundaries and interactions across contracts, adapters, and protocols.",
        focus=(
            "Check callback surfaces, reentrancy across modules, adapter assumptions, external "
            "protocol integrations, interface mismatches, token quirks, and state coupling between contracts."
        ),
    ),
    "exploitability": TreeRole(
        name="exploitability",
        title="Exploitability",
        description="Turn suspicious behavior into concrete attack paths and reject non-exploitable noise.",
        focus=(
            "Build end-to-end attacker stories, identify prerequisites, quantify impact, verify reachability, "
            "and distinguish benchmark-grade findings from low-confidence observations."
        ),
    ),
    "oracle-price": TreeRole(
        name="oracle-price",
        title="Oracle And Price",
        description="Examine price feeds, TWAP windows, decimal handling, stale data, and manipulation paths.",
        focus=(
            "Prioritize oracle freshness, feed decimals, quote/base inversion, TWAP manipulation, fallback "
            "feeds, sequencer assumptions, and any path where a distorted price can move protocol value."
        ),
    ),
    "state-machine": TreeRole(
        name="state-machine",
        title="State Machine",
        description="Inspect lifecycle transitions, temporal assumptions, replay protection, and state locks.",
        focus=(
            "Check phase changes, epoch and deadline logic, pause/unpause paths, nonce and replay controls, "
            "state-dependent authorization, and ways to skip, repeat, or reorder critical transitions."
        ),
    ),
    "standards-compliance": TreeRole(
        name="standards-compliance",
        title="Standards Compliance",
        description="Review ERC/interface assumptions, token edge cases, signatures, and integration contracts.",
        focus=(
            "Look for unsafe assumptions around ERC20/ERC721/ERC4626 behavior, fee-on-transfer and rebasing "
            "tokens, permit/signature domains, callback requirements, return values, and interface mismatches."
        ),
    ),
}

DEFAULT_TREE_ROLE_NAMES: tuple[str, ...] = tuple(TREE_ROLES)

SCOUT_SYSTEM_TEMPLATE = """You are the scout for an EVMBench forest-of-auditors run.
You use the mini-swe-agent shell interface to inspect the audit target, map the
attack surface, and choose which specialist audit trees should receive budget."""

SCOUT_INSTANCE_TEMPLATE = """First read /home/agent/AGENTS.md for the benchmark instructions.
Then inspect /home/agent/audit enough to identify the dominant protocol surfaces.

Write both scout artifacts:

1. /home/agent/forest/scout/scout.md
2. /home/agent/forest/scout/scout.json

The JSON file must be an object with this shape:

{
  "summary": "short attack-surface summary",
  "recommended_roles": ["token-flow", "accounting"],
  "role_rationale": {"token-flow": "why this role should run"}
}

Choose only from these roles:

{{ role_catalog }}

Do not write /home/agent/submission/audit.md. The final global judge is the
only worker allowed to write the EVMBench submission.

When the scout artifacts are written and verified, finish by issuing exactly
this command and no other command:

`echo COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT`

Task:
{{ task }}"""


def get_tree_role(name: str) -> TreeRole:
    try:
        return TREE_ROLES[name]
    except KeyError as exc:
        known = ", ".join(DEFAULT_TREE_ROLE_NAMES)
        raise ValueError(f"Unknown forest tree role {name!r}. Known roles: {known}") from exc


def normalize_role_names(
    role_names: Iterable[str] | None,
    *,
    fallback: Iterable[str] = DEFAULT_TREE_ROLE_NAMES,
    max_roles: int | None = None,
) -> tuple[str, ...]:
    """Return known role names in stable order, dropping duplicates and unknown values."""

    requested = list(role_names or fallback)
    seen: set[str] = set()
    normalized: list[str] = []
    for raw_name in requested:
        name = str(raw_name).strip()
        if name not in TREE_ROLES or name in seen:
            continue
        seen.add(name)
        normalized.append(name)
        if max_roles is not None and len(normalized) >= max_roles:
            break

    if not normalized and fallback is not None:
        return normalize_role_names(fallback, fallback=(), max_roles=max_roles)
    return tuple(normalized)


def parse_role_csv(raw_roles: str | None) -> tuple[str, ...]:
    if not raw_roles:
        return ()
    return normalize_role_names((part.strip() for part in raw_roles.split(",")), fallback=())


def render_role_catalog(role_names: Iterable[str] | None = None) -> str:
    names = normalize_role_names(role_names)
    lines = []
    for name in names:
        role = TREE_ROLES[name]
        lines.append(f"- {role.name}: {role.description}")
    return "\n".join(lines)


def parse_scout_decision(raw_json: str, *, max_roles: int | None = None) -> ScoutDecision:
    try:
        payload = json.loads(raw_json)
    except json.JSONDecodeError as exc:
        raise ValueError(f"Scout output was not valid JSON: {exc}") from exc
    if not isinstance(payload, dict):
        raise ValueError("Scout output JSON must be an object.")

    raw_roles = payload.get("recommended_roles", payload.get("roles", []))
    if not isinstance(raw_roles, list):
        raw_roles = []
    roles = normalize_role_names(raw_roles, max_roles=max_roles)

    raw_rationale = payload.get("role_rationale", {})
    if not isinstance(raw_rationale, dict):
        raw_rationale = {}
    rationale = {
        str(key): str(value)
        for key, value in raw_rationale.items()
        if key in TREE_ROLES
    }
    return ScoutDecision(
        summary=str(payload.get("summary", "")).strip(),
        recommended_roles=roles,
        role_rationale=rationale,
    )


def load_scout_decision(output_dir: Path, *, max_roles: int | None = None) -> ScoutDecision:
    scout_json = output_dir / "forest" / "scout" / "scout.json"
    if not scout_json.exists():
        roles = normalize_role_names(DEFAULT_TREE_ROLE_NAMES, max_roles=max_roles)
        return ScoutDecision(
            summary="Scout JSON was not found; using the default role order.",
            recommended_roles=roles,
            role_rationale={role: "default fallback" for role in roles},
        )
    try:
        return parse_scout_decision(scout_json.read_text(encoding="utf-8"), max_roles=max_roles)
    except ValueError:
        roles = normalize_role_names(DEFAULT_TREE_ROLE_NAMES, max_roles=max_roles)
        return ScoutDecision(
            summary="Scout JSON was unreadable; using the default role order.",
            recommended_roles=roles,
            role_rationale={role: "default fallback" for role in roles},
        )
