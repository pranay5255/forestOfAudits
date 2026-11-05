# EVMBench Docs

Canonical docs live flat under `docs/`. Start with the guide for the workflow
you are doing.

## Guides

| Guide | Use it for |
| --- | --- |
| [campaign-runbook.md](campaign-runbook.md) | Planning, preflighting, running, observing, cancelling, and resuming baseline/forest campaigns. Configuration, harness pins, artifact contracts, status semantics, and troubleshooting. |
| [experiment-matrix.md](experiment-matrix.md) | Matched-budget comparison protocol, metrics, sweep order, and example baseline/forest configs for test-time scaling experiments. |
| [harness-validation.md](harness-validation.md) | Pinned-image recipe, provider routing, harness-specific behavior, validated OpenCode/Codex/Yudai results, and known limitations. |
| [modal-forest-repair-plan.md](modal-forest-repair-plan.md) | The design this implementation follows: shared backend, harness adapters, artifact pipeline, real grading, worker failure semantics, detached campaigns, and acceptance gates. |
| [system-architecture.md](system-architecture.md) | Nanoeval scheduling, EVMBench tasks, local/Modal containers, retry behavior, pass@k boundaries, and exploit-mode ploit/Veto flow. |
| [audit-catalog-and-modes.md](audit-catalog-and-modes.md) | Audit folder structure, split membership, detect/patch/exploit behavior, and the audit catalog. |
| [exploit-benchmark-guide.md](exploit-benchmark-guide.md) | How exploit-mode audits work and how to add a small exploit benchmark. |
| [forest-trace-extractor.md](forest-trace-extractor.md) | Extracting datasets from legacy forest trajectories. |

## Read order

For a new experiment:

1. [modal-forest-repair-plan.md](modal-forest-repair-plan.md) for the contract.
2. [audit-catalog-and-modes.md](audit-catalog-and-modes.md) to pick valid
   audits and modes.
3. [campaign-runbook.md](campaign-runbook.md) to plan, preflight, run, and
   summarize.

For exploit benchmarks:

1. [exploit-benchmark-guide.md](exploit-benchmark-guide.md).
2. [system-architecture.md](system-architecture.md) for the container, ploit,
   Veto, and grading flow.

## Removed docs

The previous Phase 6, provider-v1 (GPT-5.3/GPT-5.4), vLLM-on-Modal, and dated
archive runbooks were removed when the campaign pipeline replaced those
workflows. Their replacement is [campaign-runbook.md](campaign-runbook.md);
historical versions remain available in git history.
