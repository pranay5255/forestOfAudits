# Prompt 00 — Shared Experiment Substrate (`evmbench/experiments/test_time/`)

Copy and paste everything below the horizontal rule into a coding agent. Run this prompt **once** before running Prompts 01–05.

---

You are implementing the shared execution, checkpointing, board, accounting, and grading substrate for five EVMBench test-time scaling experiments (communication, breadth vs. depth, candidate selection, experience reuse, and learned stopping).

Your job in this prompt is to build and unit-test the shared package `evmbench/experiments/test_time/` and the necessary hooks in `stage_launcher.py` and `detect.py`. **Do not launch paid benchmark campaigns in this prompt.**

Work inside `project/evmbench` (the Python package root). Run all Python and pytest commands from `project/evmbench` using `uv run`.

## 1. Non-Negotiable Locks

1. **Single Model Lock**: Every LLM call across all experiments—workers, status writers, finalizers, selectors, test writers, summarizers, tournament voters, and the `DETECT` grading judge—must use:
   - Model ID: `deepseek/deepseek-v4.1-flash-1017`
   - Provider: OpenRouter (`EVMBENCH_MODEL_PROVIDER=openrouter`, `EVMBENCH_EVALUATOR_PROVIDER=openrouter`, `OPENROUTER_API_KEY`)
   - Yudai `LitellmModel` string: `openrouter/deepseek/deepseek-v4.1-flash-1017`
   - Never call `openai/gpt-6-luna` or any other model.
2. **Harness Lock**: Use the benchmark-owned Yudai loop (`evmbench/agents/yudai/stage_launcher.py`) for all agent stages. Do not use `opencode` or `codex` CLIs, and do not build inside legacy `evmbench/agents/mini-swe-agent/`.
3. **Decoding Lock** (enforced in `evmbench/experiments/test_time/decode.py` and written into every `manifest.json`):
   - `worker`, `summarizer`, `tournament_vote`: `temperature = 0.7`
   - `finalizer`, `selector`, `test_writer`, `detect_judge`: `temperature = 0.0`
   - `reasoning_effort = "low"`
   - `max_tokens = 4096`
   - `seed_base = 20261107`
   - `hint_level = "none"`
4. **No Gold Leakage**: Never upload host files from `audits/<audit_id>/{findings,patch,test,exploit}` into a worker, summarizer, selector, or finalizer sandbox. Only `grade_submission` (and the one-time exploit chain deployment setup via `_mode_setup_artifacts` / `_pre_command` which deletes setup scripts before the agent runs) may touch `audits/<audit_id>/`.
5. **Leave Existing Campaign Strategies Intact**: Do not modify `Strategy = Literal["baseline", "forest"]` in `evmbench/campaign/state.py`. The test-time experiment suite has its own CLI entrypoint: `python -m evmbench.experiments.test_time.cli`.

## 2. Files to Read First

Read these files carefully before writing code:

- `project/evmbench/docs/test-time-experiments/README.md`
- `project/evmbench/evmbench/agents/yudai/stage_launcher.py`
- `project/evmbench/evmbench/agents/harness/yudai.py`
- `project/evmbench/evmbench/agents/harness/base.py`
- `project/evmbench/evmbench/agents/forest/pipeline.py` (specifically `_mode_setup_artifacts`, `_pre_command`, `_capture_patch_diff`, `_capture_exploit_txs`)
- `project/evmbench/evmbench/agents/forest/artifacts.py`
- `project/evmbench/evmbench/campaign/coordinator.py` (`SandboxPool`, `make_sandbox_factory`, `make_grade_spec`, `_grade_task`)
- `project/evmbench/evmbench/nano/grade/modal.py` and `project/evmbench/evmbench/nano/grade/detect.py`
- `project/evmbench/evmbench/audit.py`

## 3. Step-by-Step Fixes to Existing Hooks

### Step 3.1: Fix `evmbench/agents/yudai/stage_launcher.py`

Inspect `stage_launcher.py` and fix four concrete issues while maintaining 100% backwards compatibility for existing `ForestPipeline` and `YudaiAdapter` callers:

1. **Temperature and Seed Forwarding in `_model_route(spec)`**:
   - Currently `_model_route` hardcodes `"temperature": 0.0` *after* `**spec.get("model_kwargs", {})`, which overwrites any temperature in `spec["model_kwargs"]`.
   - Fix `_model_route(spec)` so that `temperature` defaults to `float(spec.get("temperature", spec.get("model_kwargs", {}).get("temperature", os.getenv("EVMBENCH_YUDAI_TEMPERATURE", "0.0"))))`.
   - If `spec.get("seed")` is not `None`, pass `"seed": int(spec["seed"])` in `model_kwargs`.
2. **Real Per-Call Token & Cost Accounting**:
   - Currently `run_yudai_loop` returns `"usage": {"input_tokens": 0, "output_tokens": 0, "cost_usd": float(getattr(agent, "cost", 0.0) or 0.0), "model_calls": ...}`.
   - Furthermore, `minisweagent.models.litellm_model.LitellmModel.query()` stores the raw LiteLLM completion dict in `message["extra"]["response"]` and `message["extra"]["cost"]`. For OpenRouter models like `openrouter/deepseek/deepseek-v4.1-flash-1017`, `litellm.cost_calculator.completion_cost` often raises a lookup error that `cost_tracking="ignore_errors"` turns into `0.0`, even when `response["usage"]` contains `prompt_tokens`, `completion_tokens`, and `cost`!
   - Write a helper `_extract_call_usage(messages: list[dict[str, Any]], start_index: int = 0) -> tuple[list[dict[str, Any]], dict[str, Any]]` that inspects every assistant message in `messages[start_index:]`:
     - Extract `extra = message.get("extra") or {}` and `resp = extra.get("response") or {}` and `usage = resp.get("usage") or {}`.
     - Read `input_tokens = int(usage.get("prompt_tokens") or usage.get("input_tokens") or 0)` and `output_tokens = int(usage.get("completion_tokens") or usage.get("output_tokens") or 0)`.
     - Read `cost_usd` from `extra.get("cost")` if `> 0`, else `usage.get("cost")`, else `(resp.get("_hidden_params") or {}).get("response_cost")`, else fallback pricing if configured via `EVMBENCH_INPUT_COST_PER_MTOK` / `EVMBENCH_OUTPUT_COST_PER_MTOK` (defaulting to `0.0` if neither is present, while setting `usage_missing = (input_tokens == 0 and output_tokens == 0)`).
     - Return both a `call_records` list (one dict per assistant turn with `turn_index`, `input_tokens`, `output_tokens`, `cost_usd`, `usage_missing`, `timestamp`) and the summed `usage` dict (`input_tokens`, `output_tokens`, `cost_usd`, `model_calls`, `usage_missing`).
   - Include `"call_records": call_records` and the summed `"usage"` in the `result.json` payload.
3. **Epoch Checkpoint and Resume Mode (`max_turns`, `resume_messages_path`, `save_messages_path`, `inject_user_message`)**:
   - Understand how `minisweagent.agents.default.DefaultAgent` works:
     - `DefaultAgent.run(task)` resets `self.messages = []`, adds `system` and `user` messages, and loops `self.step()` until `self.messages[-1].get("role") == "exit"`.
     - When `self.n_calls >= self.config.step_limit`, `DefaultAgent.query()` raises `LimitsExceeded`, which `InterruptAgentFlow` catches and turns into a trailing message `{"role": "exit", "content": "LimitsExceeded", "extra": {"exit_status": "LimitsExceeded", "submission": ""}}`.
   - Support epoch execution in `run_yudai_loop(spec, trajectory_path)`:
     - If `spec.get("resume_messages_path")` is set and exists:
       - Load the saved JSON list of messages.
       - Strip any trailing messages where `msg.get("role") == "exit"` so OpenRouter never receives an invalid `"exit"` role!
       - Set `agent.messages = loaded_messages`.
       - Recompute `agent.n_calls` as the count of `role == "assistant"` messages already in `agent.messages`, and `agent.cost` as the sum of their costs.
       - If `spec.get("inject_user_message")` is non-empty, append `agent.model.format_message(role="user", content=spec["inject_user_message"])` to `agent.messages` and log it to `trajectory_path`.
     - If `spec.get("resume_messages_path")` is not set, initialize `agent.messages` with the `system` and `user` (`instance_template`) messages just like `DefaultAgent.run()`.
     - If `spec.get("max_turns")` is set (e.g. `4`), set `agent.config.step_limit = agent.n_calls + int(spec["max_turns"])` (capped by `int(spec["step_limit"])` if a global `step_limit > 0` is also set).
     - Run the `while True: agent.step()` loop (handling `InterruptAgentFlow`, `_DeadlineExceeded`, and uncaught exceptions exactly as `DefaultAgent.run` does) while recording only the **new** messages added during this epoch (`start_index = msg_count_before_loop`).
     - If `spec.get("save_messages_path")` is set, atomically write `agent.messages` (with `"extra"` preserved so usage and tool actions remain inspectable) to `save_messages_path`.
     - In `main()`: when `spec.get("epoch_mode")` is `True`:
       - If `loop.get("exit_status") == "Submitted"`, set `status = "completed"`, `exit_code = 0`.
       - If `loop.get("exit_status") == "LimitsExceeded"` and `not loop["timed_out"]` and `not loop["protocol_failure"]`, set `status = "checkpoint"`, `exit_code = 0` (stopping at the 4-turn epoch boundary is normal operation, **not** a failure, and does not require `output_paths` to exist yet).
4. **Mode-Aware System Prompt Override**:
   - Allow `spec.get("system_template")` to override `DEFAULT_SYSTEM_TEMPLATE` when provided, and allow `spec.get("reminder_every")` (default `6`, or `0` to disable automatic submission-file reminders) to configure `StageAgent`.

### Step 3.2: Record `DetectGrader` Token Usage in `evmbench/nano/grade/detect.py` and `modal.py`

1. In `evmbench/nano/grade/detect.py`:
   - In `DetectGrader.check_audit_for_vulnerability`, `completion = await self.turn_completer.async_completion(conversation=messages)` returns a `Completion` object that has `completion.usage` (`prompt_tokens`, `completion_tokens`, `total_tokens`).
   - Do **not** alter `JUDGE_PROMPT` or `parse_judge_result`.
   - Collect the per-vulnerability usage (`prompt_tokens`, `completion_tokens`) from each `completion` in `_grade`, and store `"judge_usage": {"input_tokens": ..., "output_tokens": ..., "model_calls": len(results), "per_vulnerability": [...]}` inside `EVMbenchDetectResult.details`.
2. In `evmbench/nano/grade/modal.py`:
   - Ensure `_configure_evaluator_provider(model)` routes `deepseek/deepseek-v4.1-flash-1017` to OpenRouter (`https://openrouter.ai/api/v1` with `OPENROUTER_API_KEY`) unless `EVMBENCH_EVALUATOR_PROVIDER=openai` is explicitly set.

## 4. Create Package `evmbench/experiments/test_time/`

Create the following modules under `project/evmbench/evmbench/experiments/test_time/`:

| Module | Responsibility |
| --- | --- |
| `__init__.py` | Package exports. |
| `decode.py` | Locked constants (`LOCKED_MODEL_ID = "deepseek/deepseek-v4.1-flash-1017"`, role temperatures, `REASONING_EFFORT = "low"`, `MAX_TOKENS = 4096`, `SEED_BASE = 20261107`, `HINT_LEVEL = "none"`) and `validate_model_lock(model)`. |
| `schema.py` | Dataclasses with `schema_version = 1` and JSON serialization: `StatusRecord`, `BoardEvent`, `LedgerRow`, `EpochSnapshot`, `RunManifest`, `ExperimentConfig`. |
| `board.py` | Append-only `board.jsonl` manager and deterministic `view(worker_id, through_epoch, arm, withheld_records=())` filter. |
| `prompts.py` | Mode-aware worker instructions adapter, epoch status prompt, and shared finalizer prompt for `detect`, `patch`, and `exploit`. |
| `workspace.py` | Sandbox initialization (running `_pre_command` **once** before Epoch 1) and non-destructive per-epoch artifact + `candidate_tests/` capture. |
| `epoch_runner.py` | Multi-worker, multi-epoch execution engine over persistent per-worker `TaskComputer` sandboxes. |
| `finalize.py` | Shared temperature-`0.0` finalizer in a fresh sandbox, offline worker artifact grading, and official `grade_submission` wrapper that logs `detect_judge` rows to `ledger.jsonl`. |
| `usage.py` | Ledger reader/writer, token and USD aggregation by role, and bootstrap confidence interval helper over audit IDs. |
| `cli.py` | CLI commands: `plan`, `run`, `grade-offline`, `analyze`. |

### Detailed Contract for Each Module

#### 4.1 `schema.py` and `board.py` — Status Record and Board Views

Each worker writes `/home/agent/experiment/status.json` at the end of every 4-turn epoch (in `independent_journal` and `communicating` arms). The JSON schema must support the fields from *Scaling Discovery through Test-Time Communication* (`antiherding.py`):

```json
{
  "schema_version": 1,
  "worker_id": "w0",
  "epoch": 2,
  "through_turn": 8,
  "slot_id": "slot-0",
  "checked": "Curves.sol::buyCurvesToken and fee distribution in _transferFees",
  "finding": "External call to feeRedistributor before state update allows reentrancy or stale balance read",
  "negative_evidence": "Checked Curves.sol::setMaxFeePercent; onlyOwner guard is intact and non-bypassable",
  "evidence": "Wrote PoC test in test/CandidateReentrancy.t.sol; failed on fixture setup due to missing ERC20 approval",
  "adopted_from": [
    {
      "worker_id": "w1",
      "epoch": 1,
      "verification_performed": "Confirmed via git grep that onBalanceChange is called before protocolFee transfer",
      "preserved_variation": "Testing exploit via sellCurvesToken path instead of w1's referral fee path"
    }
  ],
  "next": "Fix ERC20 approval in test fixture and run forge test --match-test testCandidateReentrancy",
  "confidence": 0.45,
  "artifact_sha256": "<filled by host after downloading epoch candidate artifact>"
}
```

- The host downloads `/home/agent/experiment/status.json` at the end of each epoch, validates/normalizes the fields, attaches the host-computed `artifact_sha256`, `epoch`, `worker_id`, and `through_turn`, and appends the record to `board.jsonl`.
- If an arm requires journaling and `/home/agent/experiment/status.json` is missing or invalid JSON, the host appends a record with `"journal_missing": true` and empty strings—never calling an LLM on the host to fabricate a status record.
- `board.view(worker_id, through_epoch, arm, withheld_keys=set())`:
  - `arm == "independent_silent"`: returns `[]` (and the host does **not** upload `peer_view.json`).
  - `arm == "independent_journal"`: returns records in `board.jsonl` with `r.epoch <= through_epoch and r.worker_id == worker_id`.
  - `arm == "communicating"`: returns all records in `board.jsonl` with `r.epoch <= through_epoch` (in stable `(epoch, worker_id)` order).
  - `arm == "communicating_withhold"`: same as `communicating`, except any record whose `(r.worker_id, r.epoch)` is in `withheld_keys` is omitted (unless `r.worker_id == worker_id`).

#### 4.2 `workspace.py` — One-Time Sandbox Init & Non-Destructive Epoch Snapshot

1. **One-Time Worker Sandbox Setup (`init_worker_sandbox`)**:
   - Creates the worker's `TaskComputer` using `SandboxPool` / `make_sandbox_factory`.
   - Uploads `AGENTS.md` (the mode instructions adapted so intermediate workers write candidate artifacts rather than `/home/agent/submission/`).
   - Creates `/home/agent/experiment/`.
   - For `PATCH` and `EXPLOIT`: runs the exact one-time setup from `ForestPipeline._mode_setup_artifacts` and `ForestPipeline._pre_command` **once before Epoch 1** (checking out `audit.base_commit`, and in `EXPLOIT` mode deploying contracts with `ploit setup` and starting `veto` if configured).
   - **Never** re-run `_pre_command` between epochs `1..max_epochs` of the same worker!
2. **Non-Destructive Per-Epoch Candidate Capture (`capture_epoch_artifact`)**:
   - **DETECT**: Reads `/home/agent/experiment/candidate_audit.md` from the worker sandbox. If missing or empty, `artifact_present = False`, `artifact_bytes = b""`, `artifact_sha256 = ""`.
   - **PATCH**:
     - First, archive any worker-created or modified test files under `/home/agent/audit/<audit.test_dir>` (or `/home/agent/experiment/candidate_tests/`) into the local epoch directory `epochs/epoch-XX/candidate_tests/` while recording their repo-relative paths in `candidate_tests/manifest.json`.
     - Next, capture a clean source-only diff against `audit.base_commit` **without mutating the worker's git index or working tree** and **excluding untracked/modified test files** (unless listed in `audit.test_files_allowed_to_change`):
       ```bash
       set -eu
       cd /home/agent/audit
       TMP_IDX="$(mktemp /tmp/evmbench-git-index.XXXXXX)"
       trap 'rm -f "$TMP_IDX"' EXIT
       cp .git/index "$TMP_IDX"
       GIT_INDEX_FILE="$TMP_IDX" git add -N -- . ':!'"$TEST_DIR"
       GIT_INDEX_FILE="$TMP_IDX" git -c core.fileMode=false diff --binary "$BASE_COMMIT" -- . ':!'"$TEST_DIR" > /home/agent/experiment/candidate.diff
       ```
     - Do **not** run `test -s /home/agent/experiment/candidate.diff` during epoch capture: an empty diff at an early epoch is valid (`artifact_present = False`).
   - **EXPLOIT**:
     - Run `ploit txs` using `/home/agent/.evmbench/ploit.toml` to export `/home/agent/experiment/candidate_txs.json` (cleaning up any temporary `.ploit.toml` in `/home/agent/audit` afterward). If no transactions exist or `ploit txs` produces an empty transaction list, record `artifact_present = False` without failing the epoch.

#### 4.3 `finalize.py` — Shared Finalizer & Offline Grader

- **`run_shared_finalizer`**:
  - Runs in a **fresh** `TaskComputer` sandbox (initialized once with `init_worker_sandbox`), using `deepseek/deepseek-v4.1-flash-1017`, `temperature = 0.0`, `step_limit = 16`, role `finalizer`.
  - Receives the workers' candidate artifacts staged under `/home/agent/finalizer_inputs/<worker_id>/`. It never receives `peer_view.json` or any host `audits/` files.
  - **Shortcut for single-candidate finalization** (`len(non_empty_candidates) == 1` and `finalizer_mode == "passthrough_single"`): when grading a single worker's checkpoint offline (or width-1 in Experiment 2), directly materialize that candidate's artifact as the submission without spending an LLM merger call, OR when `finalizer_mode == "llm"` run the temperature-`0.0` finalizer. Keep this mode explicit in `manifest.json` (`finalizer_mode`). For multi-worker submissions (`W >= 2`), always run the temperature-`0.0` LLM finalizer so all multi-worker arms share the exact same finalization procedure:
    - **DETECT**: Merges and deduplicates `/home/agent/finalizer_inputs/*/candidate_audit.md` into `/home/agent/submission/audit.md`.
    - **PATCH**: Inspects `/home/agent/finalizer_inputs/*/candidate.diff`, applies the selected (or cleanly integrated) patch in `/home/agent/audit` at `base_commit`, and exports `/home/agent/submission/agent.diff` via `_capture_patch_diff`.
    - **EXPLOIT**: Selects one coherent worker transaction bundle `/home/agent/finalizer_inputs/<worker_id>/candidate_txs.json` (or replays its strategy on the finalizer's fresh chain) to produce `/home/agent/submission/txs.json`. Never concatenate transaction arrays from different workers.
- **`grade_artifact`**:
  - Calls `grade_submission` in a fresh grading sandbox (for `patch` and `exploit`) or via `DetectGrader` (for `detect`) with `evaluator_model = "deepseek/deepseek-v4.1-flash-1017"` and `reasoning_effort = "low"`.
  - If `mode == "detect"`, extracts `details["judge_usage"]` from the grade outcome and appends a `role="detect_judge"` row to `ledger.jsonl`.
  - Caches offline grades by `(audit_id, mode, artifact_sha256)` on the host so identical artifacts (e.g., unchanged checkpoints across epochs) are never graded twice.

## 5. Required Unit Tests (`project/evmbench/tests/test_test_time_substrate.py`)

Write comprehensive unit tests (running offline with `LocalProcessComputer` and fake/mocked model responses—no Modal and no live OpenRouter calls):

1. `test_model_lock_rejects_other_models`: `validate_model_lock` accepts `deepseek/deepseek-v4.1-flash-1017` and raises `ValueError` on `openai/gpt-6-luna` or any other string.
2. `test_stage_launcher_temperature_and_seed_forwarding`: `_model_route` respects `spec["temperature"] = 0.7` and `spec["seed"] = 20261107`, and defaults to `0.0` when omitted.
3. `test_stage_launcher_extracts_tokens_and_cost_from_messages`: Given fake `DefaultAgent.messages` containing `extra.response.usage = {"prompt_tokens": 120, "completion_tokens": 45, "cost": 0.0012}`, `_extract_call_usage` returns non-zero `input_tokens=120`, `output_tokens=45`, `cost_usd=0.0012`, `usage_missing=False`.
4. `test_stage_launcher_epoch_checkpoint_and_resume_strips_exit_role`: Running `run_yudai_loop` with `epoch_mode=True, max_turns=2` stops after 2 tool turns with `status="checkpoint"` and `exit_code=0`. Resuming from `save_messages_path` strips the trailing `role="exit"` message, appends `inject_user_message`, advances turns `3..4`, and preserves the full 4-turn history.
5. `test_board_views_isolate_silent_journal_communicating_and_withhold`: With 3 workers (`w0`, `w1`, `w2`) across 2 epochs:
   - `independent_silent` returns `[]`.
   - `independent_journal` for `w0` returns only `w0`'s records.
   - `communicating` for `w0` returns all records from `w0`, `w1`, `w2`.
   - `communicating_withhold` with `withheld_keys={("w1", 1)}` omits `("w1", 1)` for `w0` while keeping all other records.
6. `test_patch_checkpoint_captures_source_diff_and_separates_candidate_tests`: In a local git repo initialized at `base_commit`, when a worker edits `src/A.sol` and creates `test/Candidate.t.sol`, `capture_epoch_artifact` archives `test/Candidate.t.sol` under `candidate_tests/`, produces a `candidate.diff` that touches `src/A.sol` and **not** `test/Candidate.t.sol`, and leaves `test/Candidate.t.sol` intact in the worker's working tree for the next epoch. Also verify that an epoch with zero code edits succeeds with `artifact_present=False`.
7. `test_usage_ledger_fails_summary_on_missing_usage`: `usage.summarize_ledger` raises an error if any row has `usage_missing=True`.
8. `test_detect_evaluator_routes_deepseek_to_openrouter_and_records_usage`: `_configure_evaluator_provider("deepseek/deepseek-v4.1-flash-1017")` sets `OPENAI_BASE_URL="https://openrouter.ai/api/v1"` and `DetectGrader` records `judge_usage` tokens.

## 6. Done Criteria

- All existing tests (`uv run pytest tests/test_harness_adapters.py tests/test_forest_pipeline.py tests/test_grading_parity.py tests/test_detect_grader.py`) and the new `tests/test_test_time_substrate.py` pass.
- Running `uv run python -m evmbench.experiments.test_time.cli plan` with a smoke config (`experiment="smoke"`, `arm="independent_journal"`, `mode="detect"`, `audit_ids=["2024-07-basin"]`, `workers=1`, `turns_per_epoch=4`, `max_epochs=2`) writes `manifest.json` and opens zero sandboxes.
- Stop after unit tests and `plan` verification.
