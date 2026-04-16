#!/usr/bin/env bash

set -eo pipefail

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest.sh [run|plan] [phase6 args]
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest.sh summarize --output-root runs/phase6/<timestamp>

Examples:
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest.sh plan --scope smoke
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest.sh --scope first5
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest.sh --audits 2024-01-canto --stop-on-failure

Environment:
  MODAL_AUDIT_IMAGE_REPO defaults to ghcr.io/pranay5255/evmbench-audit.
  PHASE6_SCOPE defaults to first5 when --scope is not provided.
EOF
}

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." >/dev/null 2>&1 && pwd)"

cd "${PROJECT_ROOT}"

if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    . ./.env
    set +a
fi

set -u

export MODAL_AUDIT_IMAGE_REPO="${MODAL_AUDIT_IMAGE_REPO:-ghcr.io/pranay5255/evmbench-audit}"

command="run"
if [[ $# -gt 0 ]]; then
    case "$1" in
        run | plan | summarize)
            command="$1"
            shift
            ;;
        -h | --help)
            usage
            exit 0
            ;;
        -*)
            ;;
        *)
            echo "Unknown command: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
fi

evaluator=(uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py)

case "${command}" in
    run | plan)
        exec "${evaluator[@]}" "${command}" --scope "${PHASE6_SCOPE:-first5}" "$@" --runners modal-forest
        ;;
    summarize)
        exec "${evaluator[@]}" summarize "$@"
        ;;
esac
