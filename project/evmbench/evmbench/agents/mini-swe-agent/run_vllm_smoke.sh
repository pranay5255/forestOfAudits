#!/usr/bin/env bash

set -euo pipefail

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_vllm_smoke.sh

Environment:
  VLLM_API_BASE      required, e.g. https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
  VLLM_API_KEY       required, must match the evmbench-vllm-token Modal secret
  VLLM_DEPLOY_MODE   deploy, run, or skip (default: deploy)
  MODEL              defaults to openai/Qwen/Qwen3.6-35B-A3B
  AUDIT_ID           defaults to 2024-01-canto
  OUTPUT_DIR         optional smoke output directory
EOF
}

case "${1:-}" in
    -h | --help | help)
        usage
        exit 0
        ;;
esac

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" >/dev/null 2>&1 && pwd)"
PROJECT_ROOT="$(cd -- "${SCRIPT_DIR}/../../.." >/dev/null 2>&1 && pwd)"

cd "${PROJECT_ROOT}"

if [[ -f .env ]]; then
    set -a
    # shellcheck source=/dev/null
    . ./.env
    set +a
fi

: "${VLLM_API_BASE:?Set VLLM_API_BASE to the Modal vLLM /v1 URL.}"
: "${VLLM_API_KEY:?Set VLLM_API_KEY to the vLLM API token.}"

deploy_mode="${VLLM_DEPLOY_MODE:-deploy}"
case "${deploy_mode}" in
    deploy)
        uv run modal deploy evmbench/agents/mini-swe-agent/deploy_vllm.py
        ;;
    run)
        uv run modal run evmbench/agents/mini-swe-agent/deploy_vllm.py
        ;;
    skip)
        ;;
    *)
        echo "Unknown VLLM_DEPLOY_MODE: ${deploy_mode}" >&2
        usage >&2
        exit 2
        ;;
esac

api_root="${VLLM_API_BASE%/}"
server_root="${api_root%/v1}"

curl --fail --silent --show-error \
    --header "Authorization: Bearer ${VLLM_API_KEY}" \
    "${server_root}/health" >/dev/null

curl --fail --silent --show-error \
    --header "Authorization: Bearer ${VLLM_API_KEY}" \
    --header "Content-Type: application/json" \
    --data "{\"model\":\"Qwen/Qwen3.6-35B-A3B\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with ok\"}],\"max_tokens\":16,\"temperature\":0}" \
    "${api_root}/chat/completions" >/dev/null

export MODEL="${MODEL:-openai/Qwen/Qwen3.6-35B-A3B}"
export MSWEA_COST_TRACKING="${MSWEA_COST_TRACKING:-ignore_errors}"
export OUTPUT_DIR="${OUTPUT_DIR:-runs/vllm-smoke/$(date -u +%Y-%m-%dT%H-%M-%SZ)}"

uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest \
    --audit-id "${AUDIT_ID:-2024-01-canto}" \
    --mode detect \
    --hint-level none \
    --model "${MODEL}" \
    --scout-step-limit "${SCOUT_STEP_LIMIT:-4}" \
    --branch-step-limit "${BRANCH_STEP_LIMIT:-4}" \
    --judge-step-limit "${JUDGE_STEP_LIMIT:-4}" \
    --global-step-limit "${GLOBAL_STEP_LIMIT:-4}" \
    --scout-cost-limit "${SCOUT_COST_LIMIT:-1.0}" \
    --branch-cost-limit "${BRANCH_COST_LIMIT:-1.0}" \
    --judge-cost-limit "${JUDGE_COST_LIMIT:-1.0}" \
    --global-cost-limit "${GLOBAL_COST_LIMIT:-1.0}" \
    --branches-per-tree 1 \
    --max-tree-roles 1 \
    --tree-roles token-flow \
    --worker-concurrency 1 \
    --cost-tracking "${MSWEA_COST_TRACKING}" \
    --output-dir "${OUTPUT_DIR}"
