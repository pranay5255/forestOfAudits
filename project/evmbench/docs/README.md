# EVMBench Docs

This directory is organized around the main workflows in this repository. Start
here, then open the one guide that matches the task you are doing. Main docs
stay flat under `docs/`; dated snapshots and superseded commands live under
`docs/archive/`.

## Canonical Guides

| Guide | Use It For |
| --- | --- |
| [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) | Remote compute requirements, audit image naming, networking assumptions, and Modal vLLM endpoint setup/verification. |
| [phase6-runbook.md](phase6-runbook.md) | Phase 6 debug ladders, mini-swe-agent and OpenCode vLLM runs, promotion gates, raw artifact preservation, and result inspection. |
| [gpt54-openrouter-runbook.md](gpt54-openrouter-runbook.md) | Direct-OpenAI GPT-5.4 runs through the OpenRouter-v1 wrapper, current coverage tracking, launch commands, setup, summarization, and troubleshooting. |
| [audit-catalog-and-modes.md](audit-catalog-and-modes.md) | Audit folder structure, split membership, detect/patch/exploit mode behavior, and the audit catalog. |
| [system-architecture.md](system-architecture.md) | Mermaid diagrams for nanoeval scheduling, EVMBench nano tasks, local/Modal containers, retry behavior, and exploit-mode ploit/veto flow. |
| [forest-trace-extractor-plan.md](forest-trace-extractor-plan.md) | Planned extractor contract for turning forest trajectories into validated dataset JSONL files. |
| [exploit-benchmark-guide.md](exploit-benchmark-guide.md) | How exploit-mode audits work and how to add a small exploit benchmark in parity with classic EVMBench audits. |
| [archive/gpt54-run-snapshots-2026-05.md](archive/gpt54-run-snapshots-2026-05.md) | Dated May 2026 GPT-5.4 result tables, usage notes, caveats, and old candidate-set context. |

## Recommended Read Order

For Modal/vLLM scale-up work:

1. [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) to configure and
   verify the endpoint.
2. [phase6-runbook.md](phase6-runbook.md) to choose a debug ladder,
   run Phase 6, and decide whether to promote.
3. [forest-trace-extractor-plan.md](forest-trace-extractor-plan.md) after a run has
   complete raw trajectories and you want derived dataset rows.

For adding exploit benchmarks:

1. [exploit-benchmark-guide.md](exploit-benchmark-guide.md).
2. [system-architecture.md](system-architecture.md) if you need to understand
   the container, ploit, Veto, or grading flow.
3. [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) only if you need to
   run the benchmark on remote compute.

For direct GPT-5.4 wrapper experiments:

1. [audit-catalog-and-modes.md](audit-catalog-and-modes.md) to pick valid
   audits and modes.
2. [gpt54-openrouter-runbook.md](gpt54-openrouter-runbook.md) to plan, launch,
   monitor, and summarize the run.
3. [archive/gpt54-run-snapshots-2026-05.md](archive/gpt54-run-snapshots-2026-05.md)
   only when comparing against dated May 2026 results.

## Deleted Or Folded Docs

The previous docs were consolidated as follows:

| Old Doc | Replacement |
| --- | --- |
| `scale.md` | [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) |
| `vllm-modal-runbook.md` | [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) |
| `PLAN_vllm_modal.md` | [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md), as historical design context |
| `MODAL_VLLM_SCALEUP_RUNBOOK.md` | [infrastructure-vllm-modal.md](infrastructure-vllm-modal.md) and [phase6-runbook.md](phase6-runbook.md) |
| `PHASE6_MODULAR_DEBUG_RUNS.md` | [phase6-runbook.md](phase6-runbook.md) |
| `PHASE6_OPENCODE_VLLM_RUNS.md` | [phase6-runbook.md](phase6-runbook.md) |
| `SCALEUP_EXPERIMENTS_RAW_DATA.md` | [phase6-runbook.md](phase6-runbook.md) |
| `EXTRACT_FOREST_TRACES_PLAN.md` | [forest-trace-extractor-plan.md](forest-trace-extractor-plan.md) |
| `micro-exploit-benchmark-study.md` | [exploit-benchmark-guide.md](exploit-benchmark-guide.md) |
| `openai-gpt54-cli-audit-study.md` | [audit-catalog-and-modes.md](audit-catalog-and-modes.md), [gpt54-openrouter-runbook.md](gpt54-openrouter-runbook.md), and [archive/gpt54-run-snapshots-2026-05.md](archive/gpt54-run-snapshots-2026-05.md) |
| `openrouter-v1-patch-exploit-runbook.md` | [gpt54-openrouter-runbook.md](gpt54-openrouter-runbook.md) and [archive/gpt54-run-snapshots-2026-05.md](archive/gpt54-run-snapshots-2026-05.md) |
