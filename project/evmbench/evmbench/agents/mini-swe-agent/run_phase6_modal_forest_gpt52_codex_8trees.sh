#!/usr/bin/env bash

set -eo pipefail

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest_gpt52_codex_8trees.sh [run|plan] [phase6 args]
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest_gpt52_codex_8trees.sh summarize --output-root runs/phase6/<timestamp>

Examples:
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest_gpt52_codex_8trees.sh plan --scope smoke
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest_gpt52_codex_8trees.sh --scope first5
  evmbench/agents/mini-swe-agent/run_phase6_modal_forest_gpt52_codex_8trees.sh --audits 2024-01-canto --stop-on-failure

Environment:
  MODAL_AUDIT_IMAGE_REPO defaults to ghcr.io/pranay5255/evmbench-audit.
  PHASE6_SCOPE defaults to first5 when --scope is not provided.
  PHASE6_DRIVER_LOG overrides the combined terminal transcript path.

This wrapper forces the mini-swe-agent-modal-forest-gpt-5.2-codex-8trees runner.
That runner uses eight explicit forest tree roles, one branch per tree, eight
concurrent Modal worker sandboxes for branch/tree-judge batches, and
openai/gpt-5.2-codex for scout, branch, tree judge, and global judge workers.
EOF
}

arg_value() {
    local name="$1"
    shift
    while [[ $# -gt 0 ]]; do
        case "$1" in
            "${name}")
                if [[ $# -gt 1 ]]; then
                    printf '%s\n' "$2"
                    return 0
                fi
                ;;
            "${name}="*)
                printf '%s\n' "${1#*=}"
                return 0
                ;;
        esac
        shift
    done
    return 1
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
runner="modal-forest-gpt52-codex-8trees"

case "${command}" in
    run | plan)
        scope="$(arg_value --scope "$@" || true)"
        scope="${scope:-${PHASE6_SCOPE:-first5}}"
        output_root="$(arg_value --output-root "$@" || true)"
        if [[ -z "${output_root}" ]]; then
            output_root="runs/phase6/${runner}-${scope}-$(date -u +%Y-%m-%dT%H-%M-%SZ)"
            set -- "$@" --output-root "${output_root}"
        fi
        mkdir -p "${output_root}"
        driver_log="${PHASE6_DRIVER_LOG:-${output_root}/phase6-driver.log}"
        mkdir -p "$(dirname -- "${driver_log}")"
        printf '[phase6-wrapper] combined terminal log: %s\n' "${driver_log}" | tee "${driver_log}"
        set +e
        "${evaluator[@]}" "${command}" --scope "${scope}" "$@" --runners "${runner}" 2>&1 | tee -a "${driver_log}"
        status=${PIPESTATUS[0]}
        set -e
        exit "${status}"
        ;;
    summarize)
        exec "${evaluator[@]}" summarize "$@"
        ;;
esac
