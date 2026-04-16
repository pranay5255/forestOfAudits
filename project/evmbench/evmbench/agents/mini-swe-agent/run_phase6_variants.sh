#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_phase6_variants.sh variants
  evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan --scope smoke --runners all
  evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope smoke --runners smoke --stop-on-failure
  evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize --output-root runs/phase6/<timestamp>

Runner groups:
  presentation  codex-default, modal-baseline, modal-forest
  smoke         codex-default, mini-smoke-10, modal-baseline-smoke-10, modal-forest-smoke
  local         mini-default, mini-smoke-10, mini-gpt-5-mini
  modal         modal-baseline, modal-forest
  all           every registered variant

Environment:
  .env is loaded when present.
  MODAL_AUDIT_IMAGE_REPO defaults to ghcr.io/pranay5255/evmbench-audit.
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

export MODAL_AUDIT_IMAGE_REPO="${MODAL_AUDIT_IMAGE_REPO:-ghcr.io/pranay5255/evmbench-audit}"

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
esac

exec uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py "$@"
