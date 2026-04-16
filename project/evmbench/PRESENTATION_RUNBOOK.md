# Final Presentation Runbook

This runbook is the shortest path for demoing the EVMBench `mini-swe-agent` variants and producing slide-ready result files.

## Preflight

Run these from `project/evmbench`:

```bash
gh auth status
```

```bash
uv run mini --help
```

```bash
uv run python evmbench/agents/mini-swe-agent/entrypoint.py baseline --help
```

```bash
uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest --help
```

```bash
uv run pytest tests/test_mini_swe_agent_phase5.py tests/test_mini_swe_agent_forest.py tests/test_mini_swe_agent_phase6.py
```

The Phase 6 wrapper loads `.env` when present and defaults `MODAL_AUDIT_IMAGE_REPO` to `ghcr.io/pranay5255/evmbench-audit`.

## Variant Catalog

List every runner group and variant:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh variants
```

Runnable groups:

| Group | Use |
| --- | --- |
| `presentation` | Main comparison: `codex-default`, `modal-baseline`, `modal-forest`. |
| `smoke` | Low-budget integration check for the local and Modal paths. |
| `local` | Local `mini` container variants. |
| `modal` | Full Modal baseline and forest variants. |
| `modal-smoke` | Low-budget Modal-only integration check. |
| `all` | Every registered variant. |

Runnable individual slugs:

| Slug | Agent ID |
| --- | --- |
| `codex-default` | `codex-default` |
| `mini-default` | `mini-swe-agent-default` |
| `mini-smoke-10` | `mini-swe-agent-smoke-10` |
| `mini-gpt-5-mini` | `mini-swe-agent-gpt-5-mini` |
| `modal-baseline` | `mini-swe-agent-modal-baseline` |
| `modal-baseline-smoke-10` | `mini-swe-agent-modal-baseline-smoke-10` |
| `modal-forest` | `mini-swe-agent-modal-forest` |
| `modal-forest-smoke` | `mini-swe-agent-modal-forest-smoke` |

## Smoke Run

Preview the exact commands:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan --scope smoke --runners smoke
```

Run the smoke matrix:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope smoke --runners smoke --stop-on-failure
```

## Presentation Matrix

Preview the default five-audit comparison:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh plan --scope first5 --runners presentation
```

Run the comparison:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope first5 --runners presentation --stop-on-failure
```

Run one variant at a time when debugging:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope smoke --runners mini-default --stop-on-failure
```

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope smoke --runners modal-baseline-smoke-10 --stop-on-failure
```

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run --scope smoke --runners modal-forest-smoke --stop-on-failure
```

## Modal Images

Modal workers need audit images that are reachable from Modal. The wrapper points Modal at:

```bash
ghcr.io/pranay5255/evmbench-audit:<audit-id>
```

If a tag is missing, build and push the selected audit image:

```bash
uv run docker_build.py --audit 2023-07-pooltogether --tag-prefix ghcr.io/pranay5255/evmbench-audit --build-network host
```

```bash
docker push ghcr.io/pranay5255/evmbench-audit:2023-07-pooltogether
```

For local-only variants, Modal image publishing is not required, but the local Docker audit images still need to exist.

## Outputs For Slides

Each run writes a timestamped output root under `runs/phase6/` unless `--output-root` is set. Summarize or refresh artifacts with:

```bash
evmbench/agents/mini-swe-agent/run_phase6_variants.sh summarize --output-root runs/phase6/<timestamp>
```

The important files are:

| File | Use |
| --- | --- |
| `phase6-results.json` | Canonical structured results. |
| `phase6-summary.md` | Human-readable aggregate and per-audit table. |
| `phase6-slide-data.json` | Chart-ready runner, audit, and forest worker data. |
| `phase6-slide-data.csv` | Spreadsheet-friendly per-audit rows. |
| `_phase6_command_logs/` | stdout/stderr/status for each launched command. |

The grading source of truth remains each run directory's `submission/audit.md`; Modal logs and trajectories are supporting evidence for debugging and presentation details.
