"""Harbor adapter for EVMBench tasks.

This package intentionally keeps Harbor-specific glue thin. Dataset generation,
verification, and forest orchestration delegate to the existing EVMBench audit,
agent instruction, nano grader, and Modal forest modules.
"""

from .computer import HarborComputerInterface
from .dataset import (
    HarborDetectTaskSpec,
    build_evm_task_for_harbor,
    default_harbor_dataset_root,
    generate_detect_dataset,
    generate_detect_task,
)
from .verifier import run_detect_verifier

__all__ = [
    "HarborComputerInterface",
    "HarborDetectTaskSpec",
    "build_evm_task_for_harbor",
    "default_harbor_dataset_root",
    "generate_detect_dataset",
    "generate_detect_task",
    "run_detect_verifier",
]
