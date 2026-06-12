#!/usr/bin/env bash
set -euo pipefail

cd /home/experiments_base/forestOfAudits/project/evmbench
exec scripts/run_azure_gpt54_opencode_remaining.sh detect-rest "$@"
