from __future__ import annotations

import argparse
import sys

from .dataset import generate_detect_dataset
from .forest import main as forest_main
from .verifier import main as verifier_main


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="EVMBench Harbor adapter utilities.")
    subparsers = parser.add_subparsers(dest="command", required=True)

    generate = subparsers.add_parser("generate-detect-dataset")
    generate.add_argument("--audit-id", action="append", required=True)
    generate.add_argument("--output-dir")
    generate.add_argument("--hint-level", choices=["none", "low", "med"], default="none")
    generate.add_argument("--findings-subdir", choices=["", "low", "medium", "high"], default="")
    generate.add_argument("--image-version")
    generate.add_argument("--overwrite", action="store_true")
    generate.add_argument("--include-source", action="store_true")

    subparsers.add_parser("verify-detect")
    subparsers.add_parser("forest")
    return parser


def main(argv: list[str] | None = None) -> int:
    raw_args = list(sys.argv[1:] if argv is None else argv)
    if raw_args and raw_args[0] == "verify-detect":
        return verifier_main(raw_args[1:])
    if raw_args and raw_args[0] == "forest":
        return forest_main(raw_args[1:])

    args = build_arg_parser().parse_args(raw_args)
    if args.command == "generate-detect-dataset":
        paths = generate_detect_dataset(
            args.audit_id,
            args.output_dir,
            hint_level=args.hint_level,
            findings_subdir=args.findings_subdir,
            image_version=args.image_version,
            overwrite=args.overwrite,
            include_source=args.include_source,
        )
        for path in paths:
            print(path)
        return 0
    raise RuntimeError(f"Unsupported command: {args.command}")


if __name__ == "__main__":
    raise SystemExit(main())
