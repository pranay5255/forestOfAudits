"""Execution backends for EVMBench task compute."""

from .base import (
    AGENT_DIR,
    AUDIT_DIR,
    LOGS_DIR,
    SUBMISSION_DIR,
    CommandFailed,
    ComputerError,
    ComputerIdentity,
    ExecResult,
    NullSideTarget,
    TaskComputer,
)
from .local import LocalDockerComputer, LocalProcessComputer
from .modal import ModalComputer

__all__ = [
    "AGENT_DIR",
    "AUDIT_DIR",
    "CommandFailed",
    "ComputerError",
    "ComputerIdentity",
    "ExecResult",
    "LOGS_DIR",
    "LocalDockerComputer",
    "LocalProcessComputer",
    "ModalComputer",
    "NullSideTarget",
    "SUBMISSION_DIR",
    "TaskComputer",
]
