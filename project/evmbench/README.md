# EVMBench

EVMBench is a benchmark for smart-contract audit agents. It ships three task
modes (detect, patch, exploit), the audit images and harness files used to
grade them, and a campaign system for running test-time scaling experiments.

The current implementation is the **artifact pipeline** described in
[`docs/modal-forest-repair-plan.md`](docs/modal-forest-repair-plan.md):

```text
backend  = local | modal
harness  = opencode | codex | yudai
strategy = baseline | forest
mode     = detect | patch | exploit
```

A campaign is one `(backend, harness, strategy, mode, task set, candidate model,
evaluator model, budget)` tuple. Baseline and forest for the same harness use the
same candidate model, evaluator, task set, and budget, so score comparisons and
`pass@k` are meaningful.

## Contents

- [What changed](#what-changed)
- [Repository layout](#repository-layout)
- [Campaign architecture](#campaign-architecture)
- [Getting started](#getting-started)
- [Running a campaign](#running-a-campaign)
- [Grading](#grading)
- [Status and failure semantics](#status-and-failure-semantics)
- [Documentation](#documentation)
- [Development checks](#development-checks)

## What changed

- **One execution contract.** Every stage runs in a fresh `TaskComputer`
  (Modal sandbox, Alcatraz container, or local process). The coordinator never
  executes candidate tools on the host.
- **Real grading.** Detect, patch, and exploit reuse the benchmark graders.
  There is no placeholder score path; an ungraded run is `not_graded`, not `0`.
- **No gold leakage.** Agent-visible scope is derived only from the benchmark
  instructions and the repository. `patch_path_mapping` / `test_path_mapping`
  are grading data and never reach a worker.
- **Exact branch counts.** Forest branches are `roles × branches_per_tree`,
  independent of audit metadata.
- **Typed artifacts.** JSON manifests, hashes, size caps, path-traversal checks,
  and explicit `branch.diff` / `branch.txs.json` / `branch.chain.json`
  transfer contracts.
- **Normalized statuses.** `completed`, `failed`, `timeout`, `cancelled`,
  `no_submission`, `contract_violation`, `preflight_failed`.
- **Resumable campaigns.** State is written after every stage transition, with
  idempotency keys, a durable cancel flag, and signal handlers.

## Repository layout

```text
audits/                       audit images, gold findings, hidden harness files
evmbench/
  agents/backend/             TaskComputer protocol + Modal/Docker/local backends
  agents/harness/             OpenCode, Codex, Yudai adapters + preflight
  agents/forest/              prompts, artifacts, stage pipeline
  agents/yudai/               benchmark-owned stage launcher (runs in sandbox)
  campaign/                   config, durable state, coordinator, CLI
  nano/                       nanoeval task, solver, graders
  nano/grade/modal.py         real grading over a TaskComputer
  ploit/                      exploit transaction tooling
tests/                        unit + adapter + pipeline + grading tests
docs/                         canonical guides
```

Legacy modules under `evmbench/agents/mini-swe-agent/` are kept as direct debug
runners. They are not the experiment contract; use the campaign CLI.

## Campaign architecture

```text
scout → specialist branches → tree judges → final merger → benchmark evaluator
```

The first forest campaign is deliberately small:

```text
2 roles × 1 branch = 2 branch workers
+ 1 scout + 2 tree judges + 1 global merger = 6 workers
+ 1 benchmark evaluator
```

Each stage:

1. gets a **fresh sandbox**,
2. receives only its **declared parent artifacts**,
3. runs the harness CLI **inside** the sandbox,
4. preserves the harness-native trajectory,
5. validates artifacts and writes a normalized `result.json`.

No writable audit workspace, conversation history, or chain state crosses a
stage boundary. The evaluator is not a forest worker and never receives the
audit workspace.

## Getting started

Python 3.11+ and Docker are required. Modal is required only for the `modal`
backend.

```bash
uv sync --group dev
uv run pytest tests -q
```

### Build audit images

```bash
# First build also builds the shared base image.
uv run python docker_build.py \
  --audit 2024-01-canto \
  --tag-prefix ghcr.io/<org>/evmbench/audit
```

For Modal, images must be registry-pullable (a local `evmbench/audit:<id>` tag
is not). Push them to a registry and point `--image-repo` at that repository,
or set `EVMBENCH_AUDIT_IMAGE_REPO`.

### Configure providers

```bash
export OPENROUTER_API_KEY=...
export EVMBENCH_AUDIT_IMAGE_REPO=ghcr.io/<org>/evmbench/audit
# Modal only:
modal token set
```

Campaign defaults:

- candidate model: `deepseek/deepseek-v4.1-flash-1017`
- detect evaluator: `openai/gpt-6-luna`
- harness pins: Codex `0.164.0`, OpenCode `1.19.4`, Yudai commit
  `5a5541251a48da685a5b0bc2a00596771c7ded07`

Stock audit images may ship older harness versions, so build a pinned image:

```bash
scripts/build_harness_image.sh \
  --base evmbench/audit:2024-03-gitcoin \
  --output evmbench/audit:2024-03-gitcoin-fullpin --yudai
```

Preflight checks the installed versions and fails on mismatch. Provider routing
defaults to OpenRouter, including `openai/...` aliases; set
`EVMBENCH_MODEL_PROVIDER=openai` for direct OpenAI. See
[docs/harness-validation.md](docs/harness-validation.md) for the validated
OpenCode/Codex/Yudai matrix.

## Running a campaign

The CLI is the only supported campaign entry point.

```bash
# 1. Plan: validate config and print worker/sandbox/budget counts. No sandboxes.
uv run evmbench-campaign plan \
  --backend modal --harness opencode --strategy forest --mode detect \
  --audits 2024-01-canto --tree-roles accounting,token-flow \
  --branches-per-tree 1

# 2. Preflight: prove the harness does multiple tool turns and writes a file.
uv run evmbench-campaign preflight --campaign-id <id> --refresh

# 3. Run: spawn the detached coordinator, return the campaign id immediately.
uv run evmbench-campaign run --campaign-id <id>

# 4. Observe without attaching.
uv run evmbench-campaign status --campaign-id <id>
uv run evmbench-campaign summarize --campaign-id <id>

# 5. Cancel and resume.
uv run evmbench-campaign cancel --campaign-id <id>
uv run evmbench-campaign resume --campaign-id <id>
```

All commands accept `--output-dir <root>` (default `runs/campaigns`) and either
flags or `--config <json>`.

Campaign state:

```text
campaigns/<campaign_id>/
  campaign.json
  events.jsonl
  summary.json
  cancel.requested              # durable cancel flag
  tasks/<task_id>/attempts/<attempt_id>/
    attempt.json
    stages/<stage>/<worker_id>/{spec.json,result.json,artifacts/}
    submission/{audit.md|agent.diff|txs.json}
    grade/grade.json
```

### Baselines

Use the same flags with `--strategy baseline`. Baseline is a single worker that
writes the final submission directly. Both strategies grade with the same grader.

### Mode contracts

| Mode | Branch artifact | Final submission |
| --- | --- | --- |
| detect | `branch.md`, optional `findings.json` | `submission/audit.md` |
| patch | `branch.md`, `branch.diff` | `submission/agent.diff` |
| exploit | `branch.md`, `branch.txs.json`, `branch.chain.json` | `submission/txs.json` |

Patch diffs are captured with
`git -c core.fileMode=false diff --binary <base_commit>`. Exploit bundles are
branch-local and are never concatenated; the merger replays or re-executes one
coherent strategy on a fresh chain and records
`chain_isolation = "sidecar" | "single_sandbox"`.

## Grading

Grading is a separate stage after the final merger.

- **Detect:** `DetectGrader` over `submission/audit.md`, evaluator
  `openai/gpt-6-luna` via OpenRouter's OpenAI-compatible endpoint.
- **Patch:** fresh grading sandbox, base commit checkout, `git apply --binary
  --index --reject`, restore protected tests, invariant suite, per-vulnerability
  tests.
- **Exploit:** fresh grading sandbox, `ploit setup`, `ploit exec-txs`,
  `grade.sh`, parse `grade.json`.

Grading sandboxes receive only the submission, the hidden harness files the
grader uploads, the audit image, and the base commit. Provenance is written to
`grade/grade.json`:

```json
{
  "grade_source": "evmbench_detect",
  "grader_version": 1,
  "evaluator_model": "openai/gpt-6-luna",
  "submission_sha256": "...",
  "grading_sandbox_id": "...",
  "score_is_placeholder": false
}
```

A grader failure is recorded with `"graded": false`, not a successful zero.

## Status and failure semantics

| Condition | Status |
| --- | --- |
| exit 0, expected artifacts valid, contract checks pass | `completed` |
| non-zero exit (even with artifacts) | `failed` |
| time limit exceeded | `timeout` |
| cancellation requested | `cancelled` |
| exit 0 but a required artifact is missing/empty | `no_submission` |
| forbidden artifact or missing required trajectory | `contract_violation` |
| preflight did not exercise multiple turns + a file write | `preflight_failed` |

Coordinator exit codes: `0` only when every task produced a real grade, `1` for
task/cancel/ungraded failures, `2` for invalid config or preflight failure.

`continue_on_worker_error` lets downstream stages run with missing parents, but
the attempt is reported as `degraded`, never as a clean result.

## Documentation

- [docs/README.md](docs/README.md) — documentation map.
- [docs/campaign-runbook.md](docs/campaign-runbook.md) — full CLI runbook,
  budgets, harness pins, and troubleshooting.
- [docs/experiment-matrix.md](docs/experiment-matrix.md) — matched-budget
  protocol, metrics, and sweep order for beating the baseline.
- [docs/system-architecture.md](docs/system-architecture.md) — nanoeval,
  container, ploit/Veto, and grading diagrams.
- [docs/audit-catalog-and-modes.md](docs/audit-catalog-and-modes.md) — audit
  layout, splits, and mode behavior.
- [docs/exploit-benchmark-guide.md](docs/exploit-benchmark-guide.md) — how
  exploit-mode audits work.
- [docs/modal-forest-repair-plan.md](docs/modal-forest-repair-plan.md) — the
  design and acceptance gates this implementation follows.

## Development checks

```bash
uv run ruff check evmbench tests
uv run mypy evmbench
uv run pytest tests -q
```

## License

See [LICENSE](LICENSE).
