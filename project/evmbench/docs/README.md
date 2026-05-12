# EVMBench Docs

This directory is organized around the main workflows in this repository. Start
here, then open the one guide that matches the task you are doing.

## Canonical Guides

| Guide | Use It For |
| --- | --- |
| [infrastructure-and-vllm.md](infrastructure-and-vllm.md) | Remote compute requirements, audit image naming, networking assumptions, and Modal vLLM endpoint setup/verification. |
| [phase6-runs-and-data.md](phase6-runs-and-data.md) | Phase 6 debug ladders, mini-swe-agent and OpenCode vLLM runs, promotion gates, raw artifact preservation, and result inspection. |
| [openai-gpt54-cli-audit-study.md](openai-gpt54-cli-audit-study.md) | Direct-OpenAI GPT-5.4 Codex CLI vs OpenCode results, audit folder structure, audit catalog, and longer-run commands. |
| [nanoeval-system-and-containers.md](nanoeval-system-and-containers.md) | Mermaid diagrams for nanoeval scheduling, EVMBench nano tasks, local/Modal containers, retry behavior, and exploit-mode ploit/veto flow. |
| [forest-trace-extraction.md](forest-trace-extraction.md) | Planned extractor contract for turning forest trajectories into validated dataset JSONL files. |
| [exploit-benchmark-extension.md](exploit-benchmark-extension.md) | How exploit-mode audits work and how to add a small exploit benchmark in parity with classic EVMBench audits. |

## Recommended Read Order

For Modal/vLLM scale-up work:

1. [infrastructure-and-vllm.md](infrastructure-and-vllm.md) to configure and
   verify the endpoint.
2. [phase6-runs-and-data.md](phase6-runs-and-data.md) to choose a debug ladder,
   run Phase 6, and decide whether to promote.
3. [forest-trace-extraction.md](forest-trace-extraction.md) after a run has
   complete raw trajectories and you want derived dataset rows.

For adding exploit benchmarks:

1. [exploit-benchmark-extension.md](exploit-benchmark-extension.md).
2. [infrastructure-and-vllm.md](infrastructure-and-vllm.md) only if you need to
   run the benchmark on remote compute.

## Deleted Or Folded Docs

The previous docs were consolidated as follows:

| Old Doc | Replacement |
| --- | --- |
| `scale.md` | [infrastructure-and-vllm.md](infrastructure-and-vllm.md) |
| `vllm-modal-runbook.md` | [infrastructure-and-vllm.md](infrastructure-and-vllm.md) |
| `PLAN_vllm_modal.md` | [infrastructure-and-vllm.md](infrastructure-and-vllm.md), as historical design context |
| `MODAL_VLLM_SCALEUP_RUNBOOK.md` | [infrastructure-and-vllm.md](infrastructure-and-vllm.md) and [phase6-runs-and-data.md](phase6-runs-and-data.md) |
| `PHASE6_MODULAR_DEBUG_RUNS.md` | [phase6-runs-and-data.md](phase6-runs-and-data.md) |
| `PHASE6_OPENCODE_VLLM_RUNS.md` | [phase6-runs-and-data.md](phase6-runs-and-data.md) |
| `SCALEUP_EXPERIMENTS_RAW_DATA.md` | [phase6-runs-and-data.md](phase6-runs-and-data.md) |
| `EXTRACT_FOREST_TRACES_PLAN.md` | [forest-trace-extraction.md](forest-trace-extraction.md) |
| `micro-exploit-benchmark-study.md` | [exploit-benchmark-extension.md](exploit-benchmark-extension.md) |
