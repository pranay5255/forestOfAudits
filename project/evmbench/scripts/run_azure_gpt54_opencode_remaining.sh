#!/usr/bin/env bash
set -euo pipefail

cd /home/experiments_base/forestOfAudits/project/evmbench
export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_azure_gpt54_opencode_remaining.sh <chunk> [run|plan]

Chunks:
  detect-rest
  patch-1
  patch-2
  patch-3
  patch-4
  exploit-1
  exploit-2

Examples:
  scripts/run_azure_gpt54_opencode_remaining.sh detect-rest plan
  scripts/run_azure_gpt54_opencode_remaining.sh patch-1
USAGE
}

chunk="${1:-}"
action="${2:-run}"

if [[ -z "$chunk" ]]; then
  usage
  exit 2
fi

if [[ "$action" != "run" && "$action" != "plan" ]]; then
  usage
  exit 2
fi

set -a
[[ -f .env ]] && . ./.env
[[ -f .env.azure ]] && . ./.env.azure
set +a

case "$chunk" in
  detect-rest)
    tasks="detect:2024-07-munchables"
    tasks+=",detect:2024-07-traitforge"
    tasks+=",detect:2024-08-phi"
    tasks+=",detect:2024-08-wildcat"
    tasks+=",detect:2024-12-secondswap"
    tasks+=",detect:2025-01-liquid-ron"
    tasks+=",detect:2025-01-next-generation"
    tasks+=",detect:2025-02-thorwallet"
    tasks+=",detect:2025-04-forte"
    tasks+=",detect:2025-04-virtuals"
    tasks+=",detect:2025-05-blackhole"
    tasks+=",detect:2025-06-panoptic"
    tasks+=",detect:2025-10-sequence"
    tasks+=",detect:2026-01-tempo-feeamm"
    tasks+=",detect:2026-01-tempo-mpp-streams"
    tasks+=",detect:2026-01-tempo-stablecoin-dex"
    ;;
  patch-1)
    tasks="patch:2023-07-pooltogether"
    tasks+=",patch:2023-10-nextgen"
    tasks+=",patch:2023-12-ethereumcreditguild"
    tasks+=",patch:2024-01-curves"
    tasks+=",patch:2024-01-renft"
    tasks+=",patch:2024-03-taiko"
    ;;
  patch-2)
    tasks="patch:2024-04-noya"
    tasks+=",patch:2024-05-olas"
    tasks+=",patch:2024-06-size"
    tasks+=",patch:2024-07-basin"
    tasks+=",patch:2024-07-benddao"
    tasks+=",patch:2024-07-traitforge"
    ;;
  patch-3)
    tasks="patch:2024-08-phi"
    tasks+=",patch:2024-08-wildcat"
    tasks+=",patch:2025-01-liquid-ron"
    tasks+=",patch:2025-04-forte"
    tasks+=",patch:2025-04-virtuals"
    ;;
  patch-4)
    tasks="patch:2025-05-blackhole"
    tasks+=",patch:2025-06-panoptic"
    tasks+=",patch:2026-01-tempo-feeamm"
    tasks+=",patch:2026-01-tempo-mpp-streams"
    tasks+=",patch:2026-01-tempo-stablecoin-dex"
    ;;
  exploit-1)
    tasks="exploit:2023-07-pooltogether"
    tasks+=",exploit:2023-10-nextgen"
    tasks+=",exploit:2023-12-ethereumcreditguild"
    tasks+=",exploit:2024-01-curves"
    tasks+=",exploit:2024-01-renft"
    tasks+=",exploit:2024-04-noya"
    tasks+=",exploit:2024-05-olas"
    tasks+=",exploit:2024-07-basin"
    ;;
  exploit-2)
    tasks="exploit:2024-07-benddao"
    tasks+=",exploit:2024-07-traitforge"
    tasks+=",exploit:2024-08-phi"
    tasks+=",exploit:2025-04-virtuals"
    tasks+=",exploit:2025-05-blackhole"
    tasks+=",exploit:2025-06-panoptic"
    tasks+=",exploit:2026-01-tempo-mpp-streams"
    tasks+=",exploit:2026-01-tempo-stablecoin-dex"
    ;;
  *)
    usage
    exit 2
    ;;
esac

stamp="$(date -u +%Y%m%dT%H%M%SZ)"
output_root="runs/provider-v1/azure-foundry-gpt-5.4-opencode-${chunk}-${stamp}"

args=(
  "$action"
  --provider azure-foundry
  --model gpt-5.4
  --harnesses opencode
  --tasks "$tasks"
  --output-root "$output_root"
  --opencode-timeout-seconds 7200
  --agent-timeout-seconds 7800
)

if [[ "$action" == "run" ]]; then
  args+=(--item-timeout-seconds 10800)
fi

evmbench/agents/openrouter-v1/run_openrouter_v1.sh "${args[@]}"
