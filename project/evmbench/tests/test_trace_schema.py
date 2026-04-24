from __future__ import annotations

import json
from pathlib import Path

import pytest

from evmbench.experiments.dataset_manifest import default_train_eval_split_manifest
from evmbench.experiments.trace_schema import (
    SchemaValidationError,
    validate_artifact,
    validate_row,
)

EXAMPLES_DIR = Path(__file__).resolve().parents[1] / "evmbench" / "experiments" / "schema_examples"


def _load_example(name: str) -> dict[str, object]:
    payload = json.loads((EXAMPLES_DIR / name).read_text(encoding="utf-8"))
    assert isinstance(payload, dict)
    return payload


def test_all_schema_examples_validate_through_single_artifact_loader() -> None:
    for path in sorted(EXAMPLES_DIR.glob("*.json")):
        validate_artifact(path)


def test_default_split_manifest_matches_checked_in_fixture() -> None:
    expected = _load_example("train_eval_split_manifest.json")

    assert default_train_eval_split_manifest(
        run_group_id="phase6-example",
        model="openai/gpt-5",
        image_tag="evmbench/audit:<audit_id>",
        seed=42,
    ) == expected


def test_validator_rejects_missing_schema_version() -> None:
    row = _load_example("decision_point_detect.json")
    del row["schema_version"]

    with pytest.raises(SchemaValidationError, match="schema_version"):
        validate_row(row)


def test_macro_window_sequence_lengths_must_match_window_size() -> None:
    row = _load_example("macro_window.json")
    actions = row["action_sequence"]
    assert isinstance(actions, list)
    actions.pop()

    with pytest.raises(SchemaValidationError, match="length must equal window_size"):
        validate_row(row)
