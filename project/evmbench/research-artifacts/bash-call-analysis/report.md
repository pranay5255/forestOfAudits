# Bash Call Analysis for `runs/`

Generated from `runs`.

## Executive Summary

- Extracted **1,244 normalized bash/shell command records** from **140 structured log files** across **23 run directories**.
- Agent-side shell activity accounts for **1,191 calls**; runner/orchestration metadata accounts for **53 calls**.
- The corpus contains **704 unique normalized command strings** after redaction and de-duplication.
- The dominant intent is **code_or_file_inspection** with **751 calls**.
- The most frequent primary executable is **cat** with **530 calls**.

## Methodology

The extractor intentionally reads structured execution artifacts rather than scraping markdown prose or README command examples. It covers:

- Mini-SWE/forest `*.traj.json` assistant tool calls where the tool/function name is `bash`.
- Codex CLI `codex-run.jsonl` `command_execution` items.
- OpenCode `opencode-run.jsonl` and state `storage/part/*.json` records where `tool == "bash"`.
- Runner metadata in `phase6-results.json`, `openrouter-v1-results.json`, `_task_results/*.json`, and `modal-runner-command.json`.

Commands are redacted for obvious key/token/password/private-key assignments, normalized by unwrapping `/bin/bash -lc`, split into shell segments, assigned primary executables, and categorized with deterministic rules. The CSV files keep the normalized command text, source path, run metadata, status, inferred intent, tool family, mutation flag, and command shape metrics.

## Coverage

- Candidate files inspected: **2,490**
- Files with extracted calls: **236**
- Duplicate logical calls removed: **96**

## Key Findings

- The runs are strongly read-heavy: inspection plus search accounts for **820 calls (65.9%)**, while mutation, build/test, and submission-output commands account for **144 calls (11.6%)**.
- Blockchain/RPC probing is the second major behavioral cluster with **144 calls (11.6%)**, mostly from `cast`-based contract inspection in exploit-oriented runs.
- Compound shell usage appears in **472 calls (37.9%)**, which is where most multi-step audit probes, pipelines, and redirections live.
- The extractor flagged **140 commands (11.3%)** as file- or repo-mutating; this includes patch writes, generated reports, build/test side effects, and package/install commands.
- Exit-code coverage is good for agent logs: **74 commands (5.9%)** ended nonzero and **9 commands (0.7%)** had no recorded exit code.
- The largest experiment contributor is **runs/openrouter-v1** with **610 calls**, and the most repeated exact command is `cat /home/agent/audit/README.md` with **37 repeats**.

## Intent Categories

| intent_category | count |
| --- | --- |
| code_or_file_inspection | 751 |
| blockchain_rpc_probe | 144 |
| file_or_repo_mutation | 79 |
| code_search | 69 |
| run_orchestration | 53 |
| build_or_test | 49 |
| other | 34 |
| script_or_runtime | 28 |
| version_control | 18 |
| submission_output | 16 |
| text_processing | 3 |

## Category Glossary

- `code_or_file_inspection`: file reads and filesystem inventory (`cat`, `sed`, `ls`, `find`, `nl`, etc.).
- `code_search`: targeted text or symbol search (`rg`, `grep`, similar).
- `blockchain_rpc_probe`: chain/RPC inspection and transactions through tools such as `cast`, `anvil`, or `chisel`.
- `file_or_repo_mutation`: commands that write, patch, move, delete, chmod, or redirect output into files.
- `build_or_test`: build, compile, and test invocations (`forge`, `hardhat`, `pytest`, package scripts).
- `submission_output`: final-report or benchmark completion writes and markers.
- `run_orchestration`: benchmark harness commands that launched the run, not commands chosen inside the agent.
- `version_control`: `git`/`gh` commands.
- `script_or_runtime`: direct language/runtime execution not otherwise classified.
- `text_processing`: pure transformation commands such as `awk`, `jq`, `sort`, or `cut`.
- `other`: commands outside the deterministic rules above.

## Per-Run Summary

| run_label | harness | mode | audit_id | total_calls | top_intent | mutation_calls | blockchain_rpc_calls | nonzero_exit_calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| phase6/vllm-4trees-2026-05-04/modal-forest-qwen-vllm-4trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 193 | code_or_file_inspection | 7 | 0 | 1 |
| phase6/qwen-vllm-2tree-canto-2026-05-05/modal-forest-qwen-vllm-2trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 101 | code_or_file_inspection | 9 | 0 | 0 |
| rca/qwen-vllm-2tree-canto-2026-05-05/modal-forest-qwen-vllm-2trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 101 | code_or_file_inspection | 9 | 0 | 0 |
| codex--openrouter__owl-alpha-377195ae--patch--2023-12-ethereumcreditguild | codex | patch | 2023-12-ethereumcreditguild | 90 | code_or_file_inspection | 21 | 0 | 5 |
| codex--openrouter__owl-alpha-377195ae--patch--2024-05-olas | codex | patch | 2024-05-olas | 82 | code_or_file_inspection | 0 | 0 | 0 |
| opencode--gpt-5.4-6641e7fa--exploit--2025-06-panoptic | opencode | exploit | 2025-06-panoptic | 64 | blockchain_rpc_probe | 0 | 62 | 7 |
| codex--gpt-5.4-6641e7fa--exploit--2025-05-blackhole | codex | exploit | 2025-05-blackhole | 62 | file_or_repo_mutation | 24 | 18 | 7 |
| opencode--gpt-5.4-6641e7fa--exploit--2025-05-blackhole | opencode | exploit | 2025-05-blackhole | 50 | blockchain_rpc_probe | 0 | 44 | 12 |
| codex--gpt-5.4-6641e7fa--exploit--2025-06-panoptic | codex | exploit | 2025-06-panoptic | 42 | blockchain_rpc_probe | 7 | 19 | 10 |
| codex--gpt-5.4-6641e7fa--detect--2025-05-blackhole | codex | detect | 2025-05-blackhole | 27 | code_or_file_inspection | 5 | 0 | 0 |
| codex--gpt-5.4-6641e7fa--patch--2025-06-panoptic | codex | patch | 2025-06-panoptic | 24 | script_or_runtime | 11 | 0 | 9 |
| codex--gpt-5.4-6641e7fa--detect--2025-06-panoptic | codex | detect | 2025-06-panoptic | 22 | code_or_file_inspection | 6 | 0 | 0 |

## Agent-Only Per-Run Summary

| run_label | harness | mode | audit_id | total_calls | top_intent | mutation_calls | blockchain_rpc_calls | nonzero_exit_calls |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| phase6/vllm-4trees-2026-05-04/modal-forest-qwen-vllm-4trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 192 | code_or_file_inspection | 7 | 0 | 1 |
| phase6/qwen-vllm-2tree-canto-2026-05-05/modal-forest-qwen-vllm-2trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 100 | code_or_file_inspection | 9 | 0 | 0 |
| rca/qwen-vllm-2tree-canto-2026-05-05/modal-forest-qwen-vllm-2trees-debug | mini-swe-agent-forest | detect | 2024-01-canto | 100 | code_or_file_inspection | 9 | 0 | 0 |
| codex--openrouter__owl-alpha-377195ae--patch--2023-12-ethereumcreditguild | codex | patch | 2023-12-ethereumcreditguild | 90 | code_or_file_inspection | 21 | 0 | 5 |
| codex--openrouter__owl-alpha-377195ae--patch--2024-05-olas | codex | patch | 2024-05-olas | 82 | code_or_file_inspection | 0 | 0 | 0 |
| opencode--gpt-5.4-6641e7fa--exploit--2025-06-panoptic | opencode | exploit | 2025-06-panoptic | 64 | blockchain_rpc_probe | 0 | 62 | 7 |
| codex--gpt-5.4-6641e7fa--exploit--2025-05-blackhole | codex | exploit | 2025-05-blackhole | 62 | file_or_repo_mutation | 24 | 18 | 7 |
| opencode--gpt-5.4-6641e7fa--exploit--2025-05-blackhole | opencode | exploit | 2025-05-blackhole | 50 | blockchain_rpc_probe | 0 | 44 | 12 |
| codex--gpt-5.4-6641e7fa--exploit--2025-06-panoptic | codex | exploit | 2025-06-panoptic | 42 | blockchain_rpc_probe | 7 | 19 | 10 |
| codex--gpt-5.4-6641e7fa--detect--2025-05-blackhole | codex | detect | 2025-05-blackhole | 27 | code_or_file_inspection | 5 | 0 | 0 |
| codex--gpt-5.4-6641e7fa--patch--2025-06-panoptic | codex | patch | 2025-06-panoptic | 24 | script_or_runtime | 11 | 0 | 9 |
| codex--gpt-5.4-6641e7fa--detect--2025-06-panoptic | codex | detect | 2025-06-panoptic | 22 | code_or_file_inspection | 6 | 0 | 0 |

## Primary Commands

| primary_command | count |
| --- | --- |
| cat | 530 |
| ls | 114 |
| cast | 110 |
| sed | 100 |
| uv | 50 |
| grep | 44 |
| set | 40 |
| for | 26 |
| forge | 24 |
| find | 22 |
| nl | 20 |
| echo | 17 |

## Harnesses

| harness | count |
| --- | --- |
| mini-swe-agent-forest | 633 |
| codex | 424 |
| opencode | 163 |
| opencode-openrouter-v1 | 13 |
| codex-openrouter-v1 | 10 |
| mini-swe-agent-modal-forest-qwen-vllm-4trees-debug | 1 |

## Source Types

| source_type | count |
| --- | --- |
| agent_bash_tool | 780 |
| agent_command_execution | 411 |
| runner_command | 50 |
| modal_runner_command | 3 |

## Experiments

| experiment | count |
| --- | --- |
| runs/openrouter-v1 | 610 |
| runs/phase6 | 492 |
| runs/rca | 101 |
| runs/vllm-smoke | 41 |

## Repeated Commands

| command | count |
| --- | --- |
| cat /home/agent/audit/README.md | 37 |
| cat /home/agent/AGENTS.md | 36 |
| cat /home/agent/AUDIT_SCOPE.md | 36 |
| cat /home/agent/audit/src/LendingLedger.sol | 36 |
| ls -la /home/agent/audit/ | 35 |
| cat /home/agent/audit/scope.txt | 30 |
| cat /home/agent/audit/src/GaugeController.sol | 29 |
| cat /home/agent/audit/bot-report.md | 28 |
| cat /home/agent/FOREST_ROLE.md | 26 |
| cat /home/agent/audit/4naly3er-report.md | 24 |

## Plots



## Output Files

- Full command inventory: `research-artifacts/bash-call-analysis/bash_calls.csv`
- Per-run summary: `research-artifacts/bash-call-analysis/run_summary.csv`
- Per-run intent matrix: `research-artifacts/bash-call-analysis/run_intent_matrix.csv`
- Per-run intent rows: `research-artifacts/bash-call-analysis/intent_by_run.csv`
- Per-run primary-command rows: `research-artifacts/bash-call-analysis/primary_command_by_run.csv`
- Agent-only per-run summary: `research-artifacts/bash-call-analysis/agent_run_summary.csv`
- Agent-only per-run intent matrix: `research-artifacts/bash-call-analysis/agent_run_intent_matrix.csv`
- Intent summary: `research-artifacts/bash-call-analysis/by_intent.csv`
- Tool-family summary: `research-artifacts/bash-call-analysis/by_tool_family.csv`
- Primary-command summary: `research-artifacts/bash-call-analysis/by_primary_command.csv`
- Harness x intent summary: `research-artifacts/bash-call-analysis/intent_by_harness.csv`
- Mode x intent summary: `research-artifacts/bash-call-analysis/intent_by_mode.csv`

## Notes and Limits

- Counts are based on the artifacts currently present under `runs/`; copied RCA artifacts are treated as present run artifacts unless their logical call IDs duplicate within the same run directory.
- The classifier is intentionally deterministic and inspectable. It is not an LLM classifier, so ambiguous compound shell lines are categorized by priority rules.
- Runner metadata records commands submitted by the benchmark harness; agent records represent commands requested inside the evaluated agent environments.
