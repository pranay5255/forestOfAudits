#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$PROJECT_ROOT"

export UV_CACHE_DIR="${UV_CACHE_DIR:-/tmp/uv-cache}"

usage() {
  cat <<'USAGE'
Usage:
  scripts/run_codex_gpt53_codex_matrix.sh <chunk> [run|plan]

Chunks:
  smoke
  detect-1
  detect-2
  detect-3
  patch-1
  patch-2
  exploit-1
  exploit-2

Environment overrides:
  CODEX_ENV_FILE                 default: .env.codex
  CODEX_API_KEY_ENV_VAR          default: API_KEY
  CODEX_MODEL                    default: gpt-5.3-codex
  CODEX_PROVIDER                 default: azure-foundry
  CODEX_AGENT_TIMEOUT_SECONDS    default: 3600, smoke: 1200
  CODEX_ITEM_TIMEOUT_SECONDS     default: 4500, smoke: 1800
  CODEX_JUDGE_WIRE_API           default: responses
  CODEX_OUTPUT_ROOT              optional explicit output root

Examples:
  scripts/run_codex_gpt53_codex_matrix.sh smoke plan
  scripts/run_codex_gpt53_codex_matrix.sh smoke
  scripts/run_codex_gpt53_codex_matrix.sh detect-1 plan
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

env_file="${CODEX_ENV_FILE:-.env.codex}"
api_key_env_var="${CODEX_API_KEY_ENV_VAR:-API_KEY}"
model="${CODEX_MODEL:-gpt-5.3-codex}"
provider="${CODEX_PROVIDER:-azure-foundry}"
judge_wire_api="${CODEX_JUDGE_WIRE_API:-responses}"

if [[ ! -f "$env_file" ]]; then
  echo "Missing CODEX_ENV_FILE: $env_file" >&2
  exit 2
fi

agent_timeout="${CODEX_AGENT_TIMEOUT_SECONDS:-3600}"
item_timeout="${CODEX_ITEM_TIMEOUT_SECONDS:-4500}"

case "$chunk" in
  smoke)
    tasks="detect:2024-05-loop"
    agent_timeout="${CODEX_AGENT_TIMEOUT_SECONDS:-1200}"
    item_timeout="${CODEX_ITEM_TIMEOUT_SECONDS:-1800}"
    ;;
  detect-1)
    tasks="detect:2023-07-pooltogether"
    tasks+=",detect:2023-10-nextgen"
    tasks+=",detect:2023-12-ethereumcreditguild"
    tasks+=",detect:2024-01-canto"
    tasks+=",detect:2024-01-curves"
    tasks+=",detect:2024-01-init-capital-invitational"
    tasks+=",detect:2024-02-althea-liquid-infrastructure"
    tasks+=",detect:2024-01-renft"
    tasks+=",detect:2024-03-abracadabra-money"
    tasks+=",detect:2024-03-canto"
    tasks+=",detect:2024-03-coinbase"
    tasks+=",detect:2024-03-gitcoin"
    tasks+=",detect:2024-03-neobase"
    tasks+=",detect:2024-03-taiko"
    ;;
  detect-2)
    tasks="detect:2024-04-noya"
    tasks+=",detect:2024-05-arbitrum-foundation"
    tasks+=",detect:2024-05-loop"
    tasks+=",detect:2024-05-olas"
    tasks+=",detect:2024-05-munchables"
    tasks+=",detect:2024-06-size"
    tasks+=",detect:2024-06-thorchain"
    tasks+=",detect:2024-06-vultisig"
    tasks+=",detect:2024-07-basin"
    tasks+=",detect:2024-07-benddao"
    tasks+=",detect:2024-07-munchables"
    tasks+=",detect:2024-07-traitforge"
    tasks+=",detect:2024-08-phi"
    ;;
  detect-3)
    tasks="detect:2024-08-wildcat"
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
    tasks+=",patch:2024-04-noya"
    tasks+=",patch:2024-05-olas"
    tasks+=",patch:2024-06-size"
    tasks+=",patch:2024-07-basin"
    tasks+=",patch:2024-07-benddao"
    ;;
  patch-2)
    tasks="patch:2024-07-traitforge"
    tasks+=",patch:2024-08-phi"
    tasks+=",patch:2024-08-wildcat"
    tasks+=",patch:2025-01-liquid-ron"
    tasks+=",patch:2025-04-forte"
    tasks+=",patch:2025-04-virtuals"
    tasks+=",patch:2025-05-blackhole"
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
model_slug="${model//\//__}"
output_root="${CODEX_OUTPUT_ROOT:-runs/provider-v1/azure-foundry-${model_slug}-codexcli-${chunk}-${stamp}}"

args=(
  "$action"
  --provider "$provider"
  --env-file "$env_file"
  --api-key-env-var "$api_key_env_var"
  --model "$model"
  --harnesses codex
  --tasks "$tasks"
  --output-root "$output_root"
  --agent-timeout-seconds "$agent_timeout"
  --judge-wire-api "$judge_wire_api"
)

if [[ "$action" == "run" ]]; then
  args+=(--item-timeout-seconds "$item_timeout")
fi

evmbench/agents/openrouter-v1/run_openrouter_v1.sh "${args[@]}"
