# Repair EVMBench’s Modal execution and forest experiments

## Diagnosis and target

Your isolation design is sound. The current forest already creates separate Modal environments for scout, branches, tree judges, and the final judge. The failures come from incomplete boundaries between execution, agent harness, search strategy, artifact collection, and benchmark grading.

The main architectural problems are:

- Forest execution is hardwired to mini-swe-agent; OpenCode and Codex cannot participate through the same worker interface.
- Modal execution bypasses real benchmark grading and returns placeholder scores.
- Detect scope can derive from gold patch mappings, revealing vulnerable files and unexpectedly increasing branch counts.
- Artifacts pass mainly as Markdown. Patch diffs and executable exploit candidates need explicit transfer and replay contracts.
- Worker failure, missing submission, and process exit status disagree. Interruptions can also bypass summaries.
- Yudai shares the `minisweagent` package name with upstream mini-swe-agent, catches exceptions without failing its process, and its default OpenRouter adapter parses fenced commands despite prompts requesting native tool calls.

The repaired system will support detect, patch, and exploit, with a separate baseline and forest for each harness.

Candidate model: `deepseek/deepseek-v4.1-flash-1017`. Final detect evaluator: `openai/gpt-6-luna`. Both exact IDs were verified in OpenRouter’s catalog. Patch and exploit scores remain based on EVMBench tests and transaction replay.

### Code anchors for the repair

| Problem | Current anchor | Required change |
| --- | --- | --- |
| Forest is mini-swe-agent only | `evmbench/agents/mini-swe-agent/modal_forest.py` (`_run_worker`, `DefaultAgent`) | Route every worker through a harness adapter and a shared `TaskComputer`; the coordinator never executes candidate tools locally. |
| Placeholder Modal grade | `evmbench/nano/solver.py` (`_modal_runner_grade`), `evmbench/agents/modal_runner.py` | Replace with `EVMTask.grade()` semantics for detect, patch, and exploit. |
| Gold metadata leaks into scope | `modal_forest.py` (`_audit_scope_files`, `_branch_scope_files`) | Build agent-visible scope only from agent-visible instructions/source; read `patch_path_mapping`/`test_path_mapping` only in grading. |
| Branch count depends on scope files | `modal_forest.py` (`_worker_specs_for_branches`) | Iterate `range(1, branches_per_tree + 1)` per role; assert `len(branch_specs) == roles × branches_per_tree`. |
| Artifacts are mostly Markdown | `judge.py`, `modal_forest.py` (`_stage_branch_reports`) | Add typed artifacts and explicit `candidate.diff` / `txs.json` transfer and replay contracts. |
| Worker status is ambiguous | `WorkerResult`, `_run_worker`, `_check_stage_errors` | Add a normalized status enum and require artifact validation for `completed`. |
| Interruptions bypass summaries | `run_modal_forest` `finally` only | Persist state incrementally; install signal handlers; make resume idempotent. |
| Yudai is not installed | `evmbench/agents/yudai/` is empty | Bake a pinned Yudai commit into an isolated venv and add a benchmark-owned stage launcher. |

### Target shape

The experiment matrix becomes three independent axes:

```text
backend  = local | modal
harness  = opencode | codex | yudai
strategy = baseline | forest
mode     = detect | patch | exploit
```

A campaign is one `(backend, harness, strategy, mode, task set, candidate model, evaluator model, budget)` tuple. Baseline and forest for the same harness must use the same candidate model, evaluator, task set, and total budget so comparisons are meaningful.

The first forest campaign is deliberately small:

```text
2 roles × 1 branch per role
= 2 branch workers
+ 1 scout
+ 2 tree judges
+ 1 global merger
= 6 workers
+ 1 benchmark evaluator
```

The evaluator is not a forest worker and never receives the audit workspace. It receives only the final submission artifact.

### Suggested module layout

```text
evmbench/agents/backend/
  base.py            # TaskComputer protocol, SideTarget, lifecycle metadata
  modal.py           # ModalComputer backed by modal.Sandbox
  local.py           # LocalDockerComputer backed by Alcatraz/Docker

evmbench/agents/harness/
  base.py            # StageSpec, WorkerResult, status enum, HarnessAdapter
  codex.py           # codex exec --json + Responses configuration
  opencode.py        # opencode run --format json
  yudai.py           # pinned Yudai venv + native tool-calling loop
  preflight.py       # multi-turn, file-write preflight

evmbench/agents/forest/
  pipeline.py        # stage graph, branch count, artifact staging
  prompts.py         # scout, branch, judge, and merger prompts
  artifacts.py       # manifests, allowlists, transfer, hashing

evmbench/campaign/
  coordinator.py     # detached Modal coordinator
  state.py           # campaign state, event log, idempotency keys
  cli.py             # plan/preflight/run/status/cancel/resume/summarize

evmbench/agents/yudai/
  stage_launcher.py  # benchmark-owned stage entrypoint

evmbench/nano/grade/
  modal.py           # grading sandbox wiring; reuses Detect/Patch/Exploit graders
```

Tests:

```text
tests/test_task_computer.py
tests/test_harness_adapters.py
tests/test_forest_pipeline.py
tests/test_campaign_state.py
tests/test_grading_parity.py
```

Existing `modal_forest.py`, `modal_baseline.py`, and `modal_opencode.py` become compatibility entry points that call the new pipeline; they should not remain the source of truth for experiment semantics.

---

## Implementation

### 1. Separate compute, harness, strategy, and grading

Introduce a shared Modal backend using the Modal Sandbox SDK directly. It will provide shell execution, file transfer, cancellation, lifecycle tracking, and access to separately managed chain resources. Remove Alcatraz-specific assumptions from shared container helpers while preserving local Docker support.

Concretely:

- Add a `TaskComputer` protocol with `exec`, `upload`, `download`, `list`, `terminate`, identity metadata, and optional named side targets.
- Add `ModalComputer` backed by `modal.Sandbox.create`, `Sandbox.exec`, the Modal filesystem APIs, and `Sandbox.terminate`.
- Add `LocalDockerComputer` backed by the existing Alcatraz/Docker path.
- Keep the `TaskComputer` contract small enough that `EVMTask.setup()` and `EVMTask.grade()` can run against either backend.
- Treat the chain as a named side target (`chain`) when exploit parity is required. The agent target sees only Veto, never raw Anvil.
- Keep model calls outside the sandbox where possible. The sandbox needs the audit image, the pinned harness CLI, and benchmark-local services; it does not need model credentials unless the harness process itself calls the model.
- Use registry-pullable audit images. Local `evmbench/audit:<id>` tags are not a valid Modal contract.
- Set explicit sandbox timeouts, `idle_timeout`, tags, and unique names derived from campaign/task/attempt/worker. Never rely on the default five-minute lifetime.
- Record the Modal sandbox id and, when a Modal Function is used, the function call id next to the run id.

Run the agent process inside its assigned sandbox for all three harnesses. The coordinator owns scheduling and artifacts; it does not execute candidate tools locally.

Expose independent configuration fields for:

- Backend: `local` or `modal`.
- Harness: `opencode`, `codex`, or `yudai`.
- Strategy: `baseline` or `forest`.
- Task mode, candidate model, evaluator model, budgets, and concurrency.

Use one detached Modal coordinator per campaign. Closing the local client will not kill the experiment. Provide `plan`, `preflight`, `run`, `status`, `cancel`, `resume`, and `summarize` commands.

The CLI should be the only supported entry point for campaigns. Direct runner scripts remain useful for debugging but are not the experiment contract.

### 2. Build reproducible harness adapters

Bake dependencies into images before execution; do not install packages during rollouts.

Initial pins:

- Codex CLI `0.164.0`.
- OpenCode `1.19.4`.
- Yudai commit `5a5541251a48da685a5b0bc2a00596771c7ded07`, obtained through `gh api` (fetch the pinned tarball with `gh api repos/<owner>/<repo>/tarball/<commit>` and verify the commit SHA).

Install Yudai in its own virtual environment. Use its existing native tool-calling LiteLLM implementation with OpenRouter, preserving Yudai’s agent loop. Add a benchmark-owned stage launcher that supplies stage-specific instructions, output paths, limits, and meaningful exit status. Do not invoke Yudai’s outer embedded-EVMBench launcher.

Every adapter must accept the same stage specification and produce a normalized result containing completion status, exit code, failure reason, artifact references, raw trajectory references, usage, and runtime.

Define the adapter contract explicitly:

```python
@dataclass(frozen=True)
class StageSpec:
    campaign_id: str
    task_id: str
    attempt_id: int
    stage: str                  # scout | branch | tree_judge | global_merger | grade
    worker_id: str
    mode: str                   # detect | patch | exploit
    harness: str                # opencode | codex | yudai
    strategy: str               # baseline | forest
    model: str
    image: str
    instructions: str
    task: str
    step_limit: int
    cost_limit: float
    time_limit_seconds: float
    output_paths: tuple[str, ...]
    artifact_paths: tuple[str, ...]
    parent_artifacts: tuple[str, ...]
    env: dict[str, str]
    sandbox_tags: dict[str, str]


class HarnessAdapter(Protocol):
    def preflight(self, computer: TaskComputer, spec: StageSpec) -> PreflightResult: ...
    def run(self, computer: TaskComputer, spec: StageSpec) -> WorkerResult: ...
```

Preserve harness-native events:

- Codex: `codex exec --json`, using Responses.
- OpenCode: `opencode run --format json`.
- Yudai: native trajectory plus normalized completion metadata.

Codex must use the Responses API. OpenRouter requires stateless Responses requests with the full conversation history and `store: false`; do not depend on `previous_response_id`. Configure Codex with an OpenRouter provider whose `wire_api` is `responses`, and set the provider base URL to `https://openrouter.ai/api/v1`. If the pinned Codex CLI cannot emit full history itself, add a thin Responses shim in the adapter rather than silently falling back to chat completions.

OpenCode must run with `--format json`, and the adapter must preserve the JSONL event stream plus the normalized trajectory. The adapter should not scrape human-readable terminal output when the JSON stream is available.

Yudai must use native tool calls. Do not parse fenced command blocks from model text. If the model emits text without a tool call, record a protocol failure and fail the stage.

Preflight must exercise multiple tool turns and a file write. A successful chat response alone is insufficient. A preflight stage should require the harness to:

1. read a staged `PREFLIGHT.md`,
2. run at least one shell command,
3. write a known file,
4. read the file back,
5. finish with the harness’s normal completion signal.

The preflight passes only if the file exists with the expected content, the event log shows multiple tool turns, and the process exit code is zero. Run preflight per `(image, harness, model, CLI version)` and cache the result by digest.

### 3. Make the forest an explicit artifact pipeline

Each attempt follows:

```text
scout → specialist branches → tree judges → final merger → benchmark evaluator
```

Each stage receives a fresh sandbox and only declared parent artifacts. No shared writable audit workspace, conversation history, or chain state crosses stage boundaries.

Start with two roles and one branch per role: six candidate workers plus one evaluator. Branch count is exactly `roles × branches`, independent of gold metadata. The scout selects roles from the existing catalog using agent-visible instructions and source.

Fix the branch-count bug directly. The current code expands branch workers by the number of audit scope files:

```python
branch_scopes = _branch_scope_files(config, audit_scope_files)
for role in roles:
    for index, scope_file in enumerate(branch_scopes, start=1):
        ...
```

The repaired loop is:

```python
for role in roles:
    for index in range(1, config.branches_per_tree + 1):
        ...
```

and the runner asserts:

```python
assert len(branch_specs) == len(roles) * config.branches_per_tree
```

Remove `_branch_scope_files` as a branch-count input. If a neutral scope is needed, every branch gets the same agent-visible scope.

Fix scope derivation as well. `patch_path_mapping` and `test_path_mapping` are grading data, not agent-visible scope. Detect and patch scope must come from the audit instructions, the repository itself, or a scout decision based on agent-visible source. The `AUDIT_SCOPE.md` staged into a worker must never name a file solely because it appears in a gold patch mapping. Add a test that no `patch_path_mapping` or `test_path_mapping` path appears in staged instructions at `hint_level=none`.

Mode-specific contracts:

- Detect: branches produce findings and evidence; judges verify and deduplicate; the final merger writes `submission/audit.md`.
- Patch: branches produce reports and machine-extracted diffs against the task’s base commit. Judges receive the actual diffs. The final merger applies and resolves changes in a fresh workspace, runs available public checks, and exports `submission/agent.diff`.
- Exploit: branches produce reports, executed transaction bundles, and chain evidence. Judges receive the actual transaction bundles and chain metadata. The final merger selects or re-executes one coherent exploit strategy in a fresh chain and exports `submission/txs.json`.

#### Detect contract

Branch artifacts:

```text
branch.md          # findings, file/function references, evidence, false leads
findings.json      # machine-readable findings, optional but preferred
evidence.json      # commands run, tests, relevant output excerpts
trajectory.*       # harness-native raw trajectory
```

Tree judge artifacts:

```text
judge.md           # verified findings, deduplication decisions, rejected claims
```

Final merger artifact:

```text
/home/agent/submission/audit.md
```

Judges may inspect the audit repository in their own fresh sandbox, but they must not receive the branch sandbox’s writable workspace or conversation history.

#### Patch contract

Branch artifacts:

```text
branch.md
branch.diff        # git diff --binary <base_commit>, captured by the runner
```

The runner captures the branch diff after the agent finishes:

```bash
cd /home/agent/audit
git add -N .
git -c core.fileMode=false diff --binary <base_commit> > <branch_diff_path>
```

The tree judge receives `branch.md` and `branch.diff`. It compares actual diffs, not prose descriptions.

The final merger starts from a fresh checkout of the base commit, applies selected diffs with `git apply --3way` (or `--reject` with explicit conflict resolution), resolves conflicts, runs available public checks, and exports the final diff:

```bash
cd /home/agent/audit
git add -N .
git -c core.fileMode=false diff --binary <base_commit> > /home/agent/submission/agent.diff
```

The merger must not concatenate diffs from independent branches. If a selected diff cannot be applied or conflicts cannot be resolved, the stage fails with a contract violation; it must not write a partial submission and call it complete.

#### Exploit contract

Branch artifacts:

```text
branch.md
branch.txs.json    # ploit txs output from the branch's own chain
branch.chain.json  # deploy artifacts, setup block, RPC/veto mode, replay log
```

Each exploit branch gets its own fresh chain instance and its own `deploy.sh` run. Branch transaction bundles are branch-local: nonces, block numbers, and contract state are not interchangeable. A judge may compare coverage and impact, but it must not merge transactions from different branches into one bundle.

The final merger has two valid strategies:

1. Select one branch bundle and replay it in a fresh chain, verifying that it still produces the claimed impact.
2. Re-execute a merged strategy from scratch in a fresh chain.

Either way, the runner captures the transaction list from the merger’s own chain and writes `submission/txs.json`. Never synthesize `txs.json` by concatenating branch files.

The final merger must record the chain isolation mode in metadata:

```text
chain_isolation = "sidecar" | "single_sandbox"
```

Use `sidecar` when raw Anvil is isolated from the agent and only Veto is exposed. Use `single_sandbox` only as a documented fast path; do not claim anti-cheat parity for it.

If a branch or the merger cannot produce transactions, record `no_submission` with the failure reason. Do not write an empty `txs.json` and do not treat it as success.

### 4. Grade with the real benchmark graders

Grading is a separate stage after the final merger. It is never performed by a branch, tree judge, or merger.

Replace the Modal placeholder grade in `evmbench/nano/solver.py` with the same grading path used by local runs:

- Detect: run `DetectGrader` against the extracted `submission/audit.md`. The evaluator is `openai/gpt-6-luna` through OpenRouter’s OpenAI-compatible endpoint. Use the existing structured `JudgeResult` parsing; if the provider does not honor `response_format`, fall back to the existing JSON-in-text parsing rather than silently scoring zero.
- Patch: start a fresh grading sandbox from the audit image, check out the base commit, apply `submission/agent.diff` with `git apply --binary --index --reject`, restore protected test files, run invariant tests, then run per-vulnerability tests. Score is the number of patched vulnerabilities.
- Exploit: start a fresh grading sandbox, upload `submission/txs.json`, run `ploit setup`, replay with `ploit exec-txs`, run `grade.sh`, and parse `grade.json`. Score is the replay result, not the branch’s claim.

Grading sandboxes receive only:

- the submission artifact,
- the hidden benchmark harness files needed by the grader (`deploy.sh`, `utils.sh`, `grade.sh`, test files),
- the audit image and base commit.

They never receive worker logs, branch workspaces, scout notes, or chain state from the rollout.

Record grade provenance in metadata:

```json
{
  "grade_source": "evmbench_detect | evmbench_patch | evmbench_exploit",
  "grader_version": 1,
  "evaluator_model": "openai/gpt-6-luna",
  "submission_sha256": "...",
  "grading_sandbox_id": "...",
  "score_is_placeholder": false
}
```

Delete the `modal_runner_placeholder` path. A run with no real grade is `not_graded`, not a zero score.

Baseline and forest use the same grader. This is what makes baseline/forest comparisons and `pass@k` meaningful.

### 5. Normalize worker results and failure semantics

Every adapter returns the same result shape:

```json
{
  "schema_version": 1,
  "campaign_id": "...",
  "task_id": "...",
  "attempt_id": 0,
  "stage": "branch",
  "worker_id": "accounting-branch-01",
  "harness": "opencode",
  "strategy": "forest",
  "model": "deepseek/deepseek-v4.1-flash-1017",
  "status": "completed",
  "exit_code": 0,
  "failure_reason": null,
  "artifacts": [
    {"kind": "report", "path": "branch.md", "sha256": "...", "bytes": 1234}
  ],
  "trajectory": {"path": "trajectory.jsonl", "sha256": "..."},
  "usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": 0.0},
  "runtime_seconds": 123.4,
  "sandbox": {"id": "...", "image": "...", "tags": {}},
  "contract": {
    "expected_artifacts": ["branch.md"],
    "forbidden_artifacts": ["submission/audit.md"],
    "checks": ["artifact_exists", "artifact_nonempty", "no_forbidden_submission"]
  }
}
```

Status values:

| Condition | Status |
| --- | --- |
| exit 0, expected artifacts valid, contract checks pass | `completed` |
| exit code nonzero | `failed` |
| process exceeded its time limit | `timeout` |
| cancellation requested | `cancelled` |
| exit 0 but expected artifact missing or empty | `no_submission` |
| artifact exists but violates the stage contract | `contract_violation` |
| preflight did not exercise multiple turns and a file write | `preflight_failed` |
| adapter raised before/after the harness | `failed` |

`completed` is a conjunction, not an exit-code check. In particular:

- A zero exit code with no submission is `no_submission`.
- A nonzero exit code with an artifact is still `failed`.
- A missing trajectory is a contract violation for stages that require one.
- A branch that writes the final submission is a contract violation.
- A Yudai process that swallows an exception is `failed` because the stage launcher and adapter inspect the result, not just the process exit.

`continue_on_worker_error` may allow downstream stages to run with missing parent artifacts, but it must not relabel failures as success. A task with any failed required worker is `degraded` at best and is never reported as a clean benchmark result.

Coordinator exit codes:

- `0` only when every task produced a real grade.
- `1` when any task failed, was cancelled, was not graded, or produced a contract violation.
- `2` for invalid configuration or preflight failure before the campaign starts.

Interruption handling:

- Install `SIGINT`/`SIGTERM` handlers in the coordinator.
- On signal, set the campaign cancellation flag, terminate live sandboxes, flush partial `result.json` files, and write a partial summary.
- Never rely on `finally` alone: a hard kill must still leave a resumable state file.
- Write state incrementally after every stage transition, not only at the end of the campaign.
- `resume` uses idempotency keys `(campaign_id, task_id, attempt_id, stage, worker_id)` and skips stages that already have a valid `completed` result and intact artifacts.

### 6. Repair Yudai packaging and the OpenRouter adapter

Yudai needs its own install and launch path:

- Install the pinned commit into an isolated virtual environment, for example `/opt/yudai/venv`. Do not install upstream `mini-swe-agent` in that venv.
- Never import Yudai’s `minisweagent` package from the host process. The benchmark-owned stage launcher runs inside the sandbox with the Yudai venv on `PATH` and `PYTHONPATH`.
- Bake the install into the image and record `pip freeze`, the commit SHA, and the resolved repository URL in the image manifest.
- Do not install packages during a rollout.

The stage launcher is benchmark-owned and must:

- accept the common `StageSpec`,
- render stage-specific instructions and output paths,
- set step/cost/time limits,
- invoke Yudai’s native agent loop, not its outer embedded-EVMBench launcher,
- write the normalized `result.json`,
- exit nonzero on any unhandled exception, missing artifact, or protocol failure.

Use Yudai’s native tool-calling LiteLLM implementation with OpenRouter. Do not use the default fenced-command adapter. If the model returns a text-only response where a tool call is required, fail the stage with `failure_reason="native_tool_call_required"`. A preflight that only produces a chat response is a failure.

Yudai’s tendency to catch exceptions internally is handled at two levels:

1. The stage launcher wraps the agent loop and converts exceptions into a nonzero exit.
2. The adapter validates the normalized result and required artifacts even when the process exits zero.

### 7. Run campaigns through a detached coordinator

One detached Modal coordinator owns each campaign. The local CLI is a control surface, not the execution host.

Coordinator state machine:

```text
planned → preflighting → running → grading → completed
                    ↘ failed
                    ↘ cancelled
```

Campaign state is stored in a durable Modal Volume and mirrored to the local run directory:

```text
campaigns/<campaign_id>/
  campaign.json
  events.jsonl
  tasks/<task_id>/
    task.json
    attempts/<attempt_id>/
      attempt.json
      stages/<stage>/<worker_id>/
        spec.json
        result.json
        trajectory.*
        stdout.log
        stderr.log
        artifacts/
      submission/{audit.md|agent.diff|txs.json}
      grade/grade.json
```

`campaign.json` records the resolved config, audit list, mode, harness, strategy, candidate/evaluator model, budgets, concurrency, expected worker count, and artifact root.

Commands:

- `plan`: resolve the config, validate audits and modes, print the expected worker/sandbox count and budget. Do not start sandboxes.
- `preflight`: run harness preflight in a small sandbox and write a preflight report. Fail before spending audit budget if any harness cannot complete multiple tool turns and a file write.
- `run`: spawn the detached coordinator. Return the campaign id immediately.
- `status`: read campaign state and print per-task/stage/worker status without attaching to the coordinator.
- `cancel`: set the cancellation flag, terminate live sandboxes, and write a partial summary.
- `resume`: spawn a coordinator for the same campaign id, skipping completed stages with intact artifacts.
- `summarize`: aggregate scores, costs, runtimes, failure modes, and per-stage effectiveness.

Concurrency and budgets:

- Enforce a campaign-wide sandbox cap.
- Enforce per-stage and per-task cost/time limits.
- Match total candidate budget between baseline and forest before comparing scores.
- Count expected sandboxes as `1 + roles × branches + roles + 1` plus grading sandboxes.
- Put hard caps at the coordinator layer so retries cannot multiply into thousands of sandboxes.

### 8. Define artifact and metadata contracts

Artifact transfer rules:

- Use direct Modal filesystem APIs for single files.
- Use a tar archive for bulk directory transfer, with path-traversal checks.
- Write a manifest containing path, kind, bytes, and SHA-256 for every artifact.
- Never write absolute host paths into run metadata.
- Redact API keys, provider tokens, and secret environment values.
- Enforce a per-artifact size cap and fail loudly rather than truncating a trajectory or diff.

Stage artifact allowlist:

| Stage | May read | Must write | Must not write |
| --- | --- | --- | --- |
| Scout | audit source, agent-visible instructions | `scout.md` / `scout.json` | submission |
| Branch | audit source, role file, neutral scope | `branch.md`, mode artifact | submission |
| Tree judge | branch reports and mode artifacts | `judge.md` | submission |
| Global merger | tree reports and mode artifacts | final submission | branch artifacts |
| Grader | final submission + hidden harness files | `grade.json` / grader log | worker workspace |

For patch mode, the mode artifact is `branch.diff`. For exploit mode, it is `branch.txs.json` plus `branch.chain.json`. For detect mode, it is `findings.json` and optional evidence.

The final submission filenames stay unchanged:

```text
detect  → /home/agent/submission/audit.md
patch   → /home/agent/submission/agent.diff
exploit → /home/agent/submission/txs.json
```

### 9. Configuration surface

Use one resolved configuration object with independent fields:

| Field | Values / default | Notes |
| --- | --- | --- |
| `backend` | `local`, `modal` | Selects `LocalDockerComputer` or `ModalComputer`. |
| `harness` | `opencode`, `codex`, `yudai` | Selects the adapter and image pins. |
| `strategy` | `baseline`, `forest` | Baseline is one worker; forest is the staged pipeline. |
| `mode` | `detect`, `patch`, `exploit` | Unchanged EVMBench semantics. |
| `candidate_model` | `deepseek/deepseek-v4.1-flash-1017` | Used by scout, branches, judges, and merger. |
| `evaluator_model` | `openai/gpt-6-luna` | Detect evaluator only. |
| `audit_ids` | list | Task set. |
| `hint_level` | `none`, `low`, `med`, `high`, `max` | Existing EVMBench semantics. |
| `branches_per_tree` | `1` for the first campaign | Branch count is `roles × branches_per_tree`. |
| `tree_roles` | list or scout-selected | Scout may select from the catalog. |
| `max_tree_roles` | int | Hard cap. |
| `worker_concurrency` | int | Per campaign. |
| `sandbox_cap` | int | Campaign-wide hard cap. |
| `scout_step_limit` / `scout_cost_limit` | int / float | Per stage. |
| `branch_step_limit` / `branch_cost_limit` | int / float | Per stage. |
| `judge_step_limit` / `judge_cost_limit` | int / float | Per stage. |
| `global_step_limit` / `global_cost_limit` | int / float | Per stage. |
| `task_time_limit` | seconds | Harness wall-clock limit. |
| `sandbox_timeout` | seconds | Must exceed the task limit plus cleanup margin. |
| `output_dir` / `artifact_root` | path | Durable local mirror and Modal Volume path. |
| `continue_on_worker_error` | bool | Downstream continuation only; never relabels success. |

Do not silently fall back from an unavailable harness or model to another one. A missing pin or failed preflight is a configuration error.

### 10. Testing and acceptance gates

Unit tests:

- Branch count is exactly `roles × branches_per_tree`, including when the audit has many patch mappings.
- No gold `patch_path_mapping` or `test_path_mapping` path appears in agent-visible instructions or `AUDIT_SCOPE.md` at `hint_level=none`.
- The status matrix maps exit code, artifact presence, and contract violations to the correct status.
- A branch that writes `submission/audit.md` is a contract violation.
- Patch branch diffs are captured against the base commit and are valid `git apply` inputs.
- Exploit branch transaction bundles are never concatenated.
- Artifact manifests reject path traversal and hash mismatch.
- Interruption writes a resumable state file.

Adapter tests:

- Codex fixture: `codex exec --json` event stream, Responses request shape, `store: false`, full history.
- OpenCode fixture: `opencode run --format json` JSONL event stream and trajectory.
- Yudai fixture: native tool-call trajectory; a fenced-command text response fails.
- Adapter result normalization for success, nonzero exit, timeout, and missing artifact.

Preflight integration:

- One preflight per harness and candidate model.
- The preflight must produce at least two tool turns and a file write.
- A chat-only response fails.
- Codex preflight verifies stateless Responses with full history.

Grading parity:

- Detect: a known gold audit scores the expected vulnerabilities; an empty report scores zero; a partial report scores the expected subset.
- Patch: a known gold patch scores max; a known bad patch scores the expected failure; a diff that fails to apply is recorded as a grading failure, not a zero-with-success.
- Exploit: a known gold transaction bundle replays to max; an empty bundle scores zero; a tampered bundle is recorded as a replay failure.
- Compare Modal grading with local Docker grading on the same artifact. Scores and per-vulnerability results must match exactly.

End-to-end:

- One audit per mode.
- First forest campaign: 2 roles × 1 branch = 6 workers + 1 evaluator.
- Baseline and forest for the same harness use the same candidate model, evaluator, task set, and total budget.
- Every worker has a `result.json` and, where required, a trajectory.
- No `score_is_placeholder` field is true in a completed campaign.
- Cancel a running campaign, verify sandboxes terminate, then resume and complete without rerunning finished stages.

Regression:

- Existing `pytest` suite passes.
- `scripts/check_modal_forest_modes.py` still validates mode/audit pairs and no longer assumes a placeholder grade.
- Ruff and mypy pass for changed modules.

### 11. Rollout sequence

1. Freeze pins and build images. Record Codex, OpenCode, Yudai, Python, and audit image digests.
2. Implement `TaskComputer`, `ModalComputer`, and `LocalDockerComputer`. Run one detect task through `EVMTask.setup()` and `EVMTask.grade()` with `runner.max_retries=0`.
3. Add patch grading parity, then exploit replay parity, using known gold and bad artifacts.
4. Implement the three harness adapters and their preflights. Do not run an audit until preflight passes for all three.
5. Refactor the forest into the staged artifact pipeline. Fix branch count and scope leakage first, then detect.
6. Add patch and exploit forest contracts and graders.
7. Add the detached coordinator and the `plan`/`preflight`/`run`/`status`/`cancel`/`resume`/`summarize` CLI.
8. Run the first baseline/forest matrix with `deepseek/deepseek-v4.1-flash-1017` and the `openai/gpt-6-luna` detect evaluator.
9. Scale only after grading parity, artifact completeness, and interruption/resume behavior are stable.

### 12. Risks and open questions

- OpenRouter Responses compatibility for Codex may require a shim; do not fall back to chat completions silently.
- DeepSeek tool-calling reliability must be proven by preflight, not assumed from a successful chat.
- Exploit sidecar parity costs more than the single-sandbox fast path. Decide before large sweeps which mode is the reported benchmark mode.
- Yudai’s package name collision is only solved by an isolated venv and a sandbox-local launcher.
- Detect grading cost scales with the number of vulnerabilities per audit; cap concurrency and record evaluator usage.
- Detached coordinators need durable state and observable event logs; a local terminal closing must not lose the campaign.
- Artifact sizes can grow quickly with raw trajectories. Set caps and preserve the normalized result even if a large raw trajectory is dropped.
- Modal and provider rate limits can look like agent failures. Tag infrastructure failures separately from candidate failures.

### 13. Definition of done

- `plan`, `preflight`, `run`, `status`, `cancel`, `resume`, and `summarize` work against a detached Modal coordinator.
- OpenCode, Codex, and Yudai each pass preflight with multiple tool turns and a file write.
- Baseline and forest run for detect, patch, and exploit on the same harness and model.
- Branch count is exactly `roles × branches_per_tree` for every audit.
- No agent-visible artifact is derived from gold patch or test mappings.
- Detect, patch, and exploit scores come from the real EVMBench graders, with no placeholder path.
- Every worker result has a normalized status, artifact manifest, trajectory reference where required, usage, and runtime.
- Missing submissions, nonzero exits, timeouts, cancellations, and contract violations are distinguishable.
- Interrupting a campaign leaves a resumable state and a partial summary.
- Images are reproducible and install nothing during rollouts.
- The plan, runbooks, and tests describe the same contracts.
