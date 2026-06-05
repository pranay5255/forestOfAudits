#!/usr/bin/env bash
set -euo pipefail

cd /home/experiments_base/forestOfAudits/project/evmbench
export UV_CACHE_DIR=/tmp/uv-cache

DETECT_TASKS="detect:2023-07-pooltogether,detect:2023-10-nextgen,detect:2023-12-ethereumcreditguild,detect:2024-01-canto,detect:2024-01-curves,detect:2024-01-init-capital-invitational,detect:2024-02-althea-liquid-infrastructure,detect:2024-01-renft,detect:2024-03-abracadabra-money,detect:2024-03-canto,detect:2024-03-coinbase,detect:2024-03-gitcoin,detect:2024-03-neobase,detect:2024-03-taiko,detect:2024-04-noya,detect:2024-05-arbitrum-foundation,detect:2024-05-loop,detect:2024-05-olas,detect:2024-05-munchables,detect:2024-06-size,detect:2024-06-thorchain,detect:2024-06-vultisig,detect:2024-07-basin,detect:2024-07-benddao"

STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
DETECT_ROOT="runs/provider-v1/azure-foundry-gpt-5.4-opencode-detect24-${STAMP}"

evmbench/agents/openrouter-v1/run_openrouter_v1.sh run \
  --provider azure-foundry \
  --model gpt-5.4 \
  --harnesses opencode \
  --tasks "$DETECT_TASKS" \
  --output-root "$DETECT_ROOT" \
  --opencode-timeout-seconds 7200 \
  --agent-timeout-seconds 7800 \
  --item-timeout-seconds 10800
