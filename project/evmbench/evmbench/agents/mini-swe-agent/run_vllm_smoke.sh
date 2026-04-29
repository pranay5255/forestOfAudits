#!/usr/bin/env bash

set -euo pipefail

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_vllm_smoke.sh

Environment:
  VLLM_API_BASE      required, e.g. https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
  VLLM_API_KEY       required, must match the evmbench-vllm-token Modal secret
  VLLM_DEPLOY_MODE   deploy, run, download, or skip (default: skip)
  VLLM_ALLOW_EXPENSIVE_GPU required as 1 for B200/H200 or multi-GPU deploy/run
  VLLM_MODEL         defaults to Qwen/Qwen3.6-35B-A3B
  VLLM_SERVED_MODEL_NAME defaults to VLLM_MODEL
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
    log "Loaded .env from ${PROJECT_ROOT}"
fi

: "${VLLM_API_KEY:?Set VLLM_API_KEY to the vLLM API token.}"

deploy_mode="${VLLM_DEPLOY_MODE:-skip}"
gpu_config="${VLLM_MODAL_GPU:-H100}"
gpu_family="${gpu_config%%:*}"
gpu_family="${gpu_family^^}"
gpu_count="1"
if [[ "${gpu_config}" == *:* ]]; then
    gpu_count="${gpu_config##*:}"
fi
if [[ "${deploy_mode}" != "skip" ]] \
    && [[ "${gpu_family}" == "B200" || "${gpu_family}" == "H200" || ( "${gpu_count}" =~ ^[0-9]+$ && "${gpu_count}" -gt 1 ) ]] \
    && [[ "${VLLM_ALLOW_EXPENSIVE_GPU:-0}" != "1" ]]; then
    log "Refusing ${deploy_mode} with expensive GPU ${gpu_config}; set VLLM_ALLOW_EXPENSIVE_GPU=1 to continue."
    exit 2
fi

log "vLLM smoke deploy_mode=${deploy_mode} gpu=${gpu_config} model=${VLLM_MODEL:-Qwen/Qwen3.6-35B-A3B}"
case "${deploy_mode}" in
    deploy)
        log "Deploying Modal vLLM app..."
        uv run modal deploy evmbench/agents/mini-swe-agent/deploy_vllm.py
        log "Modal vLLM deploy command completed."
        ;;
    run)
        log "Running Modal vLLM app locally via modal run..."
        uv run modal run evmbench/agents/mini-swe-agent/deploy_vllm.py
        log "Modal vLLM run command completed."
        ;;
    download)
        log "Downloading vLLM model into Modal volume cache..."
        uv run modal run evmbench/agents/mini-swe-agent/deploy_vllm.py --download-only
        log "Model download completed."
        exit 0
        ;;
    skip)
        log "Skipping deploy/run; using existing VLLM_API_BASE."
        ;;
    *)
        echo "Unknown VLLM_DEPLOY_MODE: ${deploy_mode}" >&2
        usage >&2
        exit 2
        ;;
esac

: "${VLLM_API_BASE:?Set VLLM_API_BASE to the Modal vLLM /v1 URL.}"

api_root="${VLLM_API_BASE%/}"
server_root="${api_root%/v1}"
vllm_model="${VLLM_MODEL:-Qwen/Qwen3.6-35B-A3B}"
served_model_name="${VLLM_SERVED_MODEL_NAME:-${vllm_model}}"

log "vLLM smoke configuration:"
log "  api_root=${api_root}"
log "  server_root=${server_root}"
log "  vllm_model=${vllm_model}"
log "  served_model_name=${served_model_name}"
log "  VLLM_API_KEY is set (redacted, length=${#VLLM_API_KEY})"

log "Checking vLLM health endpoint..."
curl --fail --silent --show-error \
    --header "Authorization: Bearer ${VLLM_API_KEY}" \
    "${server_root}/health" >/dev/null
log "vLLM health endpoint succeeded."

log "Checking vLLM chat completions endpoint..."
curl --fail --silent --show-error \
    --header "Authorization: Bearer ${VLLM_API_KEY}" \
    --header "Content-Type: application/json" \
    --data "{\"model\":\"${served_model_name}\",\"messages\":[{\"role\":\"user\",\"content\":\"Reply with ok\"}],\"max_tokens\":16,\"temperature\":0}" \
    "${api_root}/chat/completions" >/dev/null
log "vLLM chat completions endpoint succeeded."

export MODEL="${MODEL:-openai/${served_model_name}}"
export MSWEA_COST_TRACKING="${MSWEA_COST_TRACKING:-ignore_errors}"
export OUTPUT_DIR="${OUTPUT_DIR:-runs/vllm-smoke/$(date -u +%Y-%m-%dT%H-%M-%SZ)}"

log "Starting capped Modal forest smoke:"
log "  audit_id=${AUDIT_ID:-2024-01-canto}"
log "  litellm_model=${MODEL}"
log "  output_dir=${OUTPUT_DIR}"
log "  cost_tracking=${MSWEA_COST_TRACKING}"

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

submission_path="${OUTPUT_DIR}/submission/audit.md"
if [[ ! -s "${submission_path}" ]]; then
    log "Forest smoke finished but submission is missing or empty: ${submission_path}"
    exit 1
fi

submission_bytes="$(wc -c < "${submission_path}")"
log "Forest smoke completed successfully."
log "  submission=${submission_path} bytes=${submission_bytes}"
