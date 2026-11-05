# Campaign Runbook

How to plan, preflight, run, inspect, cancel, resume, and summarize EVMBench
test-time scaling campaigns.

## 1. Mental model

One campaign is one point in the experiment matrix:

```text
backend  = local | modal
harness  = opencode | codex | yudai
strategy = baseline | forest
mode     = detect | patch | exploit
```

plus the task set, candidate model, evaluator model, and budget. Compare a
baseline and a forest only when those other axes are identical.

The forest stage graph is fixed:

```text
scout -> specialist branches -> tree judges -> final merger -> grade
```

Worker count is deterministic: `1 + roles × branches_per_tree + roles + 1`
(plus one grading sandbox per task). The first campaign target is 2 roles × 1
branch = 6 workers + 1 evaluator.

## 2. Configuration

All commands accept flags or `--config <json>`. The resolved configuration is
stored in `campaign.json`.

| Field | Values / default | Notes |
| --- | --- | --- |
| `backend` | `local`, `modal` | `local` uses Alcatraz/Docker (or a host process for tests). |
| `harness` | `opencode`, `codex`, `yudai` | Selects the adapter and CLI pin. |
| `strategy` | `baseline`, `forest` | Baseline is one worker. |
| `mode` | `detect`, `patch`, `exploit` | EVMBench mode semantics. |
| `candidate_model` | `deepseek/deepseek-v4.1-flash-1017` | Scout, branches, judges, merger. |
| `evaluator_model` | `openai/gpt-6-luna` | Detect evaluator. |
| `audit_ids` | list | Task set. |
| `hint_level` | `none`…`max` | `high`/`max` only in exploit mode. |
| `branches_per_tree` | `1` | Branch count is `roles × branches_per_tree`. |
| `tree_roles` | list | Empty = scout selects from the catalog. |
| `max_tree_roles` | `4` | Hard cap. |
| `worker_concurrency` | `3` | Per campaign. |
| `sandbox_cap` | `40` | Campaign-wide hard cap enforced by the coordinator. |
| `*_step_limit` / `*_cost_limit` | per stage | Scout/branch/judge/global/baseline. |
| `task_time_limit` | `3600` s | Harness wall clock. |
| `sandbox_timeout` | `4200` s | Must exceed the task limit plus cleanup margin. |
| `image_overrides` | `{}` | Per-audit image, e.g. a harness-pinned rebuild. |
| `continue_on_worker_error` | `false` | Continuation only; results are `degraded`. |

Invalid config, a missing pin, or a failed preflight is an error. There is no
silent fallback to another harness or model.

## 3. Harness pins and images

| Harness | Pin | Native events |
| --- | --- | --- |
| Codex | `0.164.0` | `codex exec --json`, Responses wire API, `store: false`, full history. |
| OpenCode | `1.19.4` | `opencode run --format json` JSONL. |
| Yudai | commit `5a5541251a48da685a5b0bc2a00596771c7ded07` | native tool-call JSONL trajectory. |

Stock audit images are not homogeneous (some ship OpenCode 1.1.26 or Codex
0.98.0). Build a pinned image from any audit image:

```bash
scripts/build_harness_image.sh \
  --base evmbench/audit:2024-03-gitcoin \
  --output evmbench/audit:2024-03-gitcoin-fullpin \
  --yudai
```

The script installs the exact pins, writes `/etc/evmbench/harness-pins.json`,
and bakes Yudai into `/opt/yudai/venv` (including the `mcp` dependency its
litellm path needs). Nothing is installed during a rollout.

Preflight runs `opencode --version`, `codex --version`, and reads the Yudai
commit from the pins manifest. A mismatch fails preflight; only set
`EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH=1` for a deliberate legacy image.

Yudai is launched by `evmbench/agents/yudai/stage_launcher.py`, which is
uploaded into the sandbox. It uses Yudai's own `DefaultAgent` +
`LitellmModel` native tool loop, streams its trajectory per turn, enforces a
wall-clock deadline, caps shell commands at 120 s, and fails with
`native_tool_call_required` on a text-only turn. Fenced commands are never
parsed.

Provider routing: `EVMBENCH_MODEL_PROVIDER=openrouter` (default) treats
`openai/...` as an OpenRouter alias; set `openai` only for direct OpenAI. The
detect evaluator defaults to OpenRouter even when `OPENAI_API_KEY` exists.

See [harness-validation.md](harness-validation.md) for the tested matrix and
known limitations.

## 4. Preflight

Preflight passes only when the harness:

1. reads a staged `PREFLIGHT.md`,
2. runs at least one shell command,
3. writes a known file,
4. reads it back,
5. exits zero,

and the event log shows at least two tool turns. A chat-only response fails.
Results are cached by `(image, harness, model, CLI version, checks)` under
`<campaign>/preflight/`.

Implementation: `evmbench/agents/harness/preflight.py`.

## 5. Commands

```bash
export EVMBENCH_AUDIT_IMAGE_REPO=ghcr.io/<org>/evmbench/audit
export OPENROUTER_API_KEY=...

# Build a harness-pinned image (stock images may ship older CLIs)
scripts/build_harness_image.sh \
  --base evmbench/audit:2024-03-gitcoin \
  --output evmbench/audit:2024-03-gitcoin-pinned

# Plan (no sandboxes)
uv run evmbench-campaign plan \
  --backend modal --harness opencode --strategy forest --mode detect \
  --audits 2024-01-canto --tree-roles accounting,token-flow \
  --branches-per-tree 1 \
  --image-overrides 2024-01-canto=ghcr.io/<org>/evmbench/audit:2024-01-canto-pinned

# Preflight for the images in the campaign
uv run evmbench-campaign preflight --campaign-id <id> --refresh

# Run detached
uv run evmbench-campaign run --campaign-id <id>

# Inspect
uv run evmbench-campaign status --campaign-id <id>
uv run evmbench-campaign summarize --campaign-id <id>

# Cancel (durable flag + SIGTERM + Modal sandbox reap)
uv run evmbench-campaign cancel --campaign-id <id>

# Resume: skips stages with a completed result and intact artifacts
uv run evmbench-campaign resume --campaign-id <id>
```

`run` spawns `python -m evmbench.campaign.coordinator` in a new session, so
closing the terminal does not kill the campaign. State is written after every
stage transition.

## 6. Artifacts and manifests

Every stage writes:

```text
stages/<stage>/<worker_id>/
  spec.json
  result.json          # normalized WorkerResult, schema_version 1
  artifacts/           # declared payloads, hashed
```

`result.json` includes status, exit code, failure reason, artifact manifests
(path, kind, bytes, SHA-256), trajectory, usage, runtime, sandbox id/tags, and
the stage contract.

Stage allowlist:

| Stage | May read | Must write | Must not write |
| --- | --- | --- | --- |
| Scout | audit source, agent-visible instructions | `scout.md` / `scout.json` | submission |
| Branch | audit source, role file, neutral scope | `branch.md` + mode artifact | submission |
| Tree judge | branch reports and mode artifacts | `judge.md` | submission |
| Merger | tree reports and mode artifacts | final submission | branch artifacts |
| Grader | submission + hidden harness files | `grade.json` | worker workspace |

## 7. Modes

### Detect

Branches produce findings and evidence; judges verify and deduplicate; the
merger writes `submission/audit.md`.

### Patch

The runner captures `branch.diff` after the branch finishes:

```bash
cd /home/agent/audit
git add -N .
git -c core.fileMode=false diff --binary > <branch.diff>
```

Judges compare real diffs. The merger starts from a fresh base-commit checkout,
applies selected diffs, resolves conflicts, and exports
`submission/agent.diff`. A selected diff that cannot apply is a contract
violation; partial submissions are never marked complete.

### Exploit

Each branch gets a fresh chain and runs `deploy.sh` before the agent. Branch
bundles (`branch.txs.json`, `branch.chain.json`) are branch-local. The merger
replays one bundle or re-executes a merged strategy on a fresh chain and writes
`submission/txs.json`. Bundles are never concatenated. If no transactions are
produced, the stage is `no_submission` with a reason.

`chain_isolation` is recorded as `sidecar` (agent sees only Veto) or
`single_sandbox` (documented fast path).

## 8. Grading

Grading runs after the merger, in a fresh sandbox, with no worker workspace.

- Detect: `DetectGrader`, `openai/gpt-6-luna`, structured `JudgeResult` with a
  JSON-in-text fallback.
- Patch: `git apply --binary --index --reject`, restore protected test files,
  invariant suite, per-vulnerability tests.
- Exploit: `ploit setup`, `ploit exec-txs`, `grade.sh`, parse `grade.json`.

Provenance lives in `grade/grade.json` and includes `score_is_placeholder:
false`. A grader error yields `graded: false`; it is never a successful zero.

## 9. Failure semantics

| Condition | Status |
| --- | --- |
| exit 0, expected artifacts valid | `completed` |
| non-zero exit | `failed` |
| time limit exceeded | `timeout` |
| cancellation | `cancelled` |
| required artifact missing/empty | `no_submission` |
| forbidden artifact / missing trajectory | `contract_violation` |
| preflight did not do real tool work | `preflight_failed` |

Coordinator exit codes: `0` all tasks graded, `1` any failed/cancelled/ungraded,
`2` invalid config or preflight failure.

## 10. Troubleshooting

- **Preflight fails on a chat-only response.** The model/provider is not doing
  native tool calls. Fix the pin or provider; do not disable preflight.
- **Modal cannot pull an image.** `--image-repo` must be registry-pullable.
- **`sandbox cap reached`.** Reduce `worker_concurrency` or raise
  `sandbox_cap` deliberately; retries cannot multiply sandboxes.
- **A campaign ended with no grade.** Inspect `events.jsonl` and
  `grade/grade.json`. `not_graded` is not a zero.
- **Resume reruns a stage.** The stage result must be `completed` and every
  artifact must still exist; otherwise it is re-executed by design.
