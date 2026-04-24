"""Dataset manifest helpers for Forest/PRM artifacts."""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path
from typing import NoReturn, cast

from evmbench.experiments.schema_version import EXTRACTOR_VERSION, SCHEMA_VERSION
from evmbench.experiments.trace_schema import (
    ROW_TYPES,
    SPLITS,
    JsonObject,
    SchemaValidationError,
    validate_provenance,
)

MANIFEST_TYPE = "forest_prm_dataset_manifest"
DATASET_VERSION = "v0"

DEFAULT_FIRST20_AUDITS = (
    "2023-07-pooltogether",
    "2023-10-nextgen",
    "2023-12-ethereumcreditguild",
    "2024-01-canto",
    "2024-01-curves",
    "2024-01-init-capital-invitational",
    "2024-02-althea-liquid-infrastructure",
    "2024-01-renft",
    "2024-03-abracadabra-money",
    "2024-03-canto",
    "2024-03-coinbase",
    "2024-03-gitcoin",
    "2024-03-neobase",
    "2024-03-taiko",
    "2024-04-noya",
    "2024-05-arbitrum-foundation",
    "2024-05-loop",
    "2024-05-olas",
    "2024-05-munchables",
    "2024-06-size",
)
DEFAULT_TRAIN_AUDITS = DEFAULT_FIRST20_AUDITS[:15]
DEFAULT_EVAL_AUDITS = DEFAULT_FIRST20_AUDITS[15:20]


def _fail(path: str, message: str) -> NoReturn:
    raise SchemaValidationError(f"{path}: {message}")


def _as_mapping(value: object, path: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        _fail(path, "must be an object")
    if not all(isinstance(key, str) for key in value):
        _fail(path, "object keys must be strings")
    return cast(Mapping[str, object], value)


def _require(mapping: Mapping[str, object], key: str, path: str) -> object:
    if key not in mapping:
        _fail(f"{path}.{key}" if path else key, "missing required field")
    return mapping[key]


def _require_str(mapping: Mapping[str, object], key: str, path: str) -> str:
    value = _require(mapping, key, path)
    if not isinstance(value, str) or value == "":
        _fail(f"{path}.{key}" if path else key, "must be a non-empty string")
    return value


def _require_bool(mapping: Mapping[str, object], key: str, path: str) -> bool:
    value = _require(mapping, key, path)
    if not isinstance(value, bool):
        _fail(f"{path}.{key}" if path else key, "must be a boolean")
    return value


def _is_nonnegative_int_or_none(value: object) -> bool:
    return value is None or (isinstance(value, int) and not isinstance(value, bool) and value >= 0)


def _validate_string_list(value: object, path: str) -> list[str]:
    if not isinstance(value, list):
        _fail(path, "must be a list")
    for index, item in enumerate(value):
        if not isinstance(item, str) or item == "":
            _fail(f"{path}[{index}]", "must be a non-empty string")
    return cast(list[str], value)


def _reject_unknown_keys(mapping: Mapping[str, object], allowed: frozenset[str], path: str) -> None:
    unknown = sorted(set(mapping) - allowed)
    if unknown:
        _fail(path or "<manifest>", f"unknown fields: {', '.join(unknown)}")


def default_train_eval_split_manifest(
    *,
    dataset_name: str = "ForestTrace-EVM-Scaling",
    evmbench_commit: str = "UNSET",
    run_group_id: str = "UNSET",
    model: str = "UNSET",
    image_tag: str = "UNSET",
    seed: int | None = None,
    grading_commit: str | None = None,
) -> JsonObject:
    """Return the canonical first20 split manifest from PRM_EXPERIMENTS_v1."""

    return {
        "manifest_type": MANIFEST_TYPE,
        "schema_version": SCHEMA_VERSION,
        "dataset_name": dataset_name,
        "dataset_version": DATASET_VERSION,
        "description": "Canonical Forest/PRM dataset manifest for decision, branch, preference, macro, and controller artifacts.",
        "row_types": sorted(ROW_TYPES),
        "splits": {
            "train": {"audit_ids": list(DEFAULT_TRAIN_AUDITS), "row_count": None},
            "eval": {"audit_ids": list(DEFAULT_EVAL_AUDITS), "row_count": None},
        },
        "provenance": {
            "evmbench_commit": evmbench_commit,
            "split": "unspecified",
            "audit_id": "multiple",
            "run_group_id": run_group_id,
            "model": model,
            "image_tag": image_tag,
            "seed": seed,
            "grading_commit": grading_commit,
            "extractor_version": EXTRACTOR_VERSION,
        },
        "redaction": {
            "public_safe": True,
            "canary_handling": "strip_canary_lines before publication and reject rows containing private canaries",
            "rules": [
                "Do not publish API keys, tokens, Modal secrets, GitHub tokens, or private registry credentials.",
                "Keep public audit source paths and benchmark identifiers; redact local usernames and host paths.",
                "Patch/exploit artifacts may be null in detect-only exports and must not be fabricated.",
                "Preserve enough command output for grading provenance, but truncate unrelated dependency logs.",
            ],
        },
        "dataset_card": {
            "license": "Apache-2.0",
            "intended_use": "Research on process reward models, forest routing, and smart-contract audit agents.",
            "limitations": [
                "Detect-only rows may have null patch and exploit fields.",
                "Terminal scores are benchmark rewards, not guarantees of real-world exploitability.",
                "Models trained on this data may overfit EVMBench task construction.",
            ],
            "citation": "TBD",
        },
    }


def validate_dataset_manifest(manifest: Mapping[str, object]) -> JsonObject:
    allowed = frozenset(
        {
            "manifest_type",
            "schema_version",
            "dataset_name",
            "dataset_version",
            "description",
            "row_types",
            "splits",
            "provenance",
            "redaction",
            "dataset_card",
        }
    )
    _reject_unknown_keys(manifest, allowed, "")
    if _require_str(manifest, "manifest_type", "") != MANIFEST_TYPE:
        _fail("manifest_type", f"must be {MANIFEST_TYPE!r}")
    if _require_str(manifest, "schema_version", "") != SCHEMA_VERSION:
        _fail("schema_version", f"must be {SCHEMA_VERSION!r}")
    _require_str(manifest, "dataset_name", "")
    _require_str(manifest, "dataset_version", "")
    _require_str(manifest, "description", "")

    row_types = _validate_string_list(_require(manifest, "row_types", ""), "row_types")
    invalid_row_types = sorted(set(row_types) - ROW_TYPES)
    if invalid_row_types:
        _fail("row_types", f"unknown row types: {', '.join(invalid_row_types)}")

    splits = _as_mapping(_require(manifest, "splits", ""), "splits")
    _validate_splits(splits)
    validate_provenance(_as_mapping(_require(manifest, "provenance", ""), "provenance"))
    _validate_redaction(_as_mapping(_require(manifest, "redaction", ""), "redaction"))
    _validate_dataset_card(_as_mapping(_require(manifest, "dataset_card", ""), "dataset_card"))
    return dict(manifest)


def _validate_splits(splits: Mapping[str, object]) -> None:
    if "train" not in splits or "eval" not in splits:
        _fail("splits", "must include train and eval")
    invalid_splits = sorted(set(splits) - SPLITS)
    if invalid_splits:
        _fail("splits", f"unknown splits: {', '.join(invalid_splits)}")
    for split_name, split_value in splits.items():
        split = _as_mapping(split_value, f"splits.{split_name}")
        _reject_unknown_keys(split, frozenset({"audit_ids", "row_count"}), f"splits.{split_name}")
        _validate_string_list(_require(split, "audit_ids", f"splits.{split_name}"), f"splits.{split_name}.audit_ids")
        row_count = _require(split, "row_count", f"splits.{split_name}")
        if not _is_nonnegative_int_or_none(row_count):
            _fail(f"splits.{split_name}.row_count", "must be a non-negative integer or null")


def _validate_redaction(redaction: Mapping[str, object]) -> None:
    _reject_unknown_keys(
        redaction,
        frozenset({"public_safe", "canary_handling", "rules"}),
        "redaction",
    )
    _require_bool(redaction, "public_safe", "redaction")
    _require_str(redaction, "canary_handling", "redaction")
    _validate_string_list(_require(redaction, "rules", "redaction"), "redaction.rules")


def _validate_dataset_card(dataset_card: Mapping[str, object]) -> None:
    _reject_unknown_keys(
        dataset_card,
        frozenset({"license", "intended_use", "limitations", "citation"}),
        "dataset_card",
    )
    _require_str(dataset_card, "license", "dataset_card")
    _require_str(dataset_card, "intended_use", "dataset_card")
    _validate_string_list(_require(dataset_card, "limitations", "dataset_card"), "dataset_card.limitations")
    _require_str(dataset_card, "citation", "dataset_card")


def load_dataset_manifest(path: Path) -> JsonObject:
    with path.open(encoding="utf-8") as handle:
        payload = json.load(handle)
    return validate_dataset_manifest(_as_mapping(payload, str(path)))
