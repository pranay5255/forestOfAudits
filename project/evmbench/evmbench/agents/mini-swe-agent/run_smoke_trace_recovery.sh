#!/usr/bin/env bash
# Smoke test: run a minimal forest to verify trace recovery on failure.
#
# Uses 1 explicit role, 1 branch, and low step/cost limits so it finishes fast.
# After the run (pass or fail), check:
#   <output-dir>/logs/modal-forest-result.json   — metadata with all worker results
#   <output-dir>/logs/forest/                     — per-worker trajectory files
#
# Usage:
#   evmbench/agents/mini-swe-agent/run_smoke_trace_recovery.sh [--audit-id ID]
#
# Environment:
#   OPENAI_API_KEY          required
#   MODAL_AUDIT_IMAGE_REPO  defaults to ghcr.io/pranay5255/evmbench-audit

set -eo pipefail

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

AUDIT_ID="${1:-2024-01-canto}"
# Strip --audit-id flag if passed as two args
if [[ "${AUDIT_ID}" == "--audit-id" ]]; then
    AUDIT_ID="${2:?missing audit id after --audit-id}"
fi

OUTPUT_DIR="runs/smoke-trace-recovery/$(date -u +%Y-%m-%dT%H-%M-%SZ)_${AUDIT_ID}"
mkdir -p "${OUTPUT_DIR}"

echo "=== Smoke trace-recovery test ==="
echo "  audit:      ${AUDIT_ID}"
echo "  output_dir: ${OUTPUT_DIR}"
echo ""

set +e
uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest \
    --audit-id "${AUDIT_ID}" \
    --mode detect \
    --tree-roles Analyzer \
    --branches-per-tree 1 \
    --max-tree-roles 1 \
    --worker-concurrency 1 \
    --scout-step-limit 4 \
    --scout-cost-limit 0.50 \
    --branch-step-limit 6 \
    --branch-cost-limit 1.0 \
    --judge-step-limit 4 \
    --judge-cost-limit 1.0 \
    --global-step-limit 6 \
    --global-cost-limit 1.5 \
    --continue-on-worker-error \
    --output-dir "${OUTPUT_DIR}"
exit_code=$?
set -e

echo ""
echo "=== Run finished (exit code: ${exit_code}) ==="
echo ""

# --- Check what traces were salvaged ---
metadata="${OUTPUT_DIR}/logs/modal-forest-result.json"
if [[ -f "${metadata}" ]]; then
    echo "Metadata file exists: ${metadata}"
    echo ""
    echo "Worker summary:"
    python3 -c "
import json, sys
data = json.load(open('${metadata}'))
print(f\"  overall error: {data.get('error', '-')}\")
print(f\"  runtime:       {data.get('runtime_seconds', 0):.1f}s\")
print(f\"  selected roles: {data.get('selected_roles', [])}\")
print()
for w in data.get('workers', []):
    status = 'ERROR' if w.get('error') else 'ok'
    rt = w.get('runtime_seconds', 0)
    print(f\"  [{status:>5}] {w['worker_name']:30s}  {rt:6.1f}s  traj={w.get('trajectory_path', '-')}\")
"
else
    echo "WARNING: No metadata file found at ${metadata}"
    echo "  This means the run crashed before even writing partial results."
fi

echo ""
echo "Trajectory files found:"
find "${OUTPUT_DIR}" -name '*.traj.json' -type f 2>/dev/null | sort | while read -r f; do
    echo "  ${f}"
done

echo ""
echo "Log directories:"
find "${OUTPUT_DIR}/logs" -type d 2>/dev/null | sort | while read -r d; do
    echo "  ${d}/"
done

exit "${exit_code}"
