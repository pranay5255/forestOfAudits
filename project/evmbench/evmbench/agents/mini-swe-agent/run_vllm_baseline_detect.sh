#!/usr/bin/env bash

set -euo pipefail

log() {
    printf '[%s] %s\n' "$(date -u +%Y-%m-%dT%H:%M:%SZ)" "$*" >&2
}

usage() {
    cat <<'EOF'
Usage:
  evmbench/agents/mini-swe-agent/run_vllm_baseline_detect.sh

Environment:
  VLLM_API_BASE      required, e.g. https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
  VLLM_API_KEY       required, must match the evmbench-vllm-token Modal secret
  VLLM_MODEL         defaults to Qwen/Qwen3.6-35B-A3B
  VLLM_SERVED_MODEL_NAME defaults to VLLM_MODEL
  MODEL              defaults to openai/$VLLM_SERVED_MODEL_NAME
  AUDIT_ID           defaults to 2024-01-canto
  HINT_LEVEL         defaults to none
  MODAL_AUDIT_IMAGE  optional full registry image override
  MODAL_AUDIT_IMAGE_REPO defaults to ghcr.io/pranay5255/evmbench-audit
  OUTPUT_DIR         optional baseline output directory
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
: "${VLLM_API_BASE:?Set VLLM_API_BASE to the Modal vLLM /v1 URL.}"

audit_id="${AUDIT_ID:-2024-01-canto}"
api_root="${VLLM_API_BASE%/}"
server_root="${api_root%/v1}"
vllm_model="${VLLM_MODEL:-Qwen/Qwen3.6-35B-A3B}"
served_model_name="${VLLM_SERVED_MODEL_NAME:-${vllm_model}}"
model="${MODEL:-openai/${served_model_name}}"
modal_audit_image_repo="${MODAL_AUDIT_IMAGE_REPO:-ghcr.io/pranay5255/evmbench-audit}"
modal_audit_image="${MODAL_AUDIT_IMAGE:-${modal_audit_image_repo}:${audit_id}}"
output_dir="${OUTPUT_DIR:-runs/vllm-baseline/$(date -u +%Y-%m-%dT%H-%M-%SZ)_${audit_id}_detect}"

log "vLLM baseline detect configuration:"
log "  api_root=${api_root}"
log "  server_root=${server_root}"
log "  vllm_model=${vllm_model}"
log "  served_model_name=${served_model_name}"
log "  litellm_model=${model}"
log "  audit_id=${audit_id}"
log "  image=${modal_audit_image}"
log "  output_dir=${output_dir}"
log "  step_limit=${STEP_LIMIT:-50} cost_limit=${COST_LIMIT:-20.0} cost_tracking=${MSWEA_COST_TRACKING:-ignore_errors}"
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

log "Starting Modal baseline detect run..."
uv run python evmbench/agents/mini-swe-agent/entrypoint.py baseline \
    --audit-id "${audit_id}" \
    --mode detect \
    --hint-level "${HINT_LEVEL:-none}" \
    --findings-subdir "${FINDINGS_SUBDIR:-}" \
    --image "${modal_audit_image}" \
    --model "${model}" \
    --step-limit "${STEP_LIMIT:-50}" \
    --cost-limit "${COST_LIMIT:-20.0}" \
    --modal-secret-name "" \
    --model-kwargs-json "${MODEL_KWARGS_JSON:-{}}" \
    --cost-tracking "${MSWEA_COST_TRACKING:-ignore_errors}" \
    --output-dir "${output_dir}" \
    --no-grade

submission_path="${output_dir}/submission/audit.md"
trajectory_path="${output_dir}/logs/mini-swe-agent.traj.json"
if [[ ! -s "${submission_path}" ]]; then
    log "Baseline detect finished but submission is missing or empty: ${submission_path}"
    exit 1
fi

submission_bytes="$(wc -c < "${submission_path}")"
log "Baseline detect completed successfully."
log "  submission=${submission_path} bytes=${submission_bytes}"
log "  trajectory=${trajectory_path}"
