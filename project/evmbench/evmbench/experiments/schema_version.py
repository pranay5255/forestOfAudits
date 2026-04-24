"""Version constants for Forest/PRM dataset artifacts."""

from __future__ import annotations

import re
from dataclasses import dataclass

SCHEMA_NAME = "forest-prm-artifacts"
SCHEMA_VERSION = "1.0.0"
EXTRACTOR_VERSION = "trace-schema-1.0.0"

_SEMVER_RE = re.compile(r"^\d+\.\d+\.\d+$")


@dataclass(frozen=True)
class SchemaVersion:
    name: str
    version: str
    extractor_version: str


CURRENT_SCHEMA = SchemaVersion(
    name=SCHEMA_NAME,
    version=SCHEMA_VERSION,
    extractor_version=EXTRACTOR_VERSION,
)


def is_supported_schema_version(version: str) -> bool:
    return version == SCHEMA_VERSION


def require_supported_schema_version(value: object) -> str:
    if not isinstance(value, str):
        raise ValueError("schema_version must be a string")
    if not _SEMVER_RE.match(value):
        raise ValueError(f"schema_version must use semantic versioning, got {value!r}")
    if not is_supported_schema_version(value):
        raise ValueError(f"unsupported schema_version {value!r}; expected {SCHEMA_VERSION!r}")
    return value


def schema_id(row_type: str) -> str:
    return f"{SCHEMA_NAME}/{row_type}@{SCHEMA_VERSION}"
