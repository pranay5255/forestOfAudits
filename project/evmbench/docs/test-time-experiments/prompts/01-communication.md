# Prompt 01 — Experiment 1: Communicating Workers vs. Independent Workers

Copy and paste everything below the horizontal rule into a coding agent **after Prompt 00 is complete**.

---

You are implementing and running **EVMBench Experiment 1: Communicating Workers vs. Independent Workers**, adapted from *Scaling Discovery through Test-Time Communication* (§2, §3.4, Appendix A, and released `antiherding.py`).

Read `project/evmbench/docs/test-time-experiments/README.md` and `project/evmbench/docs/test-time-experiments/prompts/00-shared-substrate.md` first. Use the shared runner in `evmbench/experiments/test_time/`. If that package is missing, stop and instruct the user to run Prompt 00 first.

## 1. Scientific Question & Publishable Claim

- **Question**: Does a team of 4 `deepseek/deepseek-v4.1-flash-1017` workers that can read each other's periodic status records outperform 4 independent workers that write the exact same status records on the same schedule but cannot read peers' records?
- **Why both conditions write status records**: Writing a structured summary every 4 turns can itself improve an agent's reasoning (self-reflection). Requiring `/home/agent/experiment/status.json` in both `communicating` and `independent_journal` isolates **peer communication** from **periodic self-summarization**. We also run `independent_silent` (no status file required, no peer view) to measure the practical baseline and the standalone effect of journaling.
- **Publishable mechanism to test**:
  1. **Complementary composition**: Worker A identifies an invariant/accounting inconsistency; Worker B identifies a reachable transaction path or falsifies a dead end (`negative_evidence`); Worker C combines them into a working reproduction, patch, or exploit (`adopted_from`).
  2. **Causal withhold check**: If `communicating` beats `independent_journal`, selectively withhold the cited peer status records in a live rerun (`communicating_withhold`) and test whether the gain disappears.

## 2. Locked Experimental Design

- **Model**: `deepseek/deepseek-v4.1-flash-1017` via OpenRouter for all workers (`T = 0.7`), the shared finalizer (`T = 0.0`), and the `DETECT` grader (`T = 0.0`, `reasoning_effort = "low"`).
- **Population**: Fixed at **4 workers** (`w0`, `w1`, `w2`, `w3`). No dynamic spawning or despawning so gains cannot be attributed to a different population policy.
- **Cadence & Budget**:
  - `turns_per_epoch = 4` tool turns
  - `max_epochs = 8` (32 tool turns total per worker)
  - Note: If Experiment 2's collection bank already exists for an audit and mode, reuse `w0`–`w3` truncated at turn 32 (epoch 8) for the `independent_journal` arm so you do not pay twice for the same trajectories!
- **Modes**: Run separate campaigns for `detect`, `patch`, and `exploit`.
- **Pilot Audits**: `2024-07-basin`, `2024-01-curves`, `2026-01-tempo-stablecoin-dex`.

### The Three Primary Arms (+ Live Intervention Arm)

| Arm | Writes `/home/agent/experiment/status.json` every 4 turns? | Receives `/home/agent/experiment/peer_view.json`? | What `peer_view.json` contains at start of Epoch $e$ |
| --- | --- | --- | --- |
| `independent_silent` | No | No | Nothing (no file uploaded) |
| `independent_journal` | Yes (required) | Yes | Only this worker's own records from epochs $1 \dots e-1$ |
| `communicating` | Yes (required) | Yes | All 4 workers' records (`w0`–`w3`) from epochs $1 \dots e-1$ |
| `communicating_withhold` | Yes (required) | Yes (conditional run) | All 4 workers' records *except* the specific `(worker_id, epoch)` records cited in the winning composition chain |

### Anti-Herding and Search-Slot Assignment (Adapted from `antiherding.py`)

In `evmbench/experiments/test_time/prompts.py`, implement `build_communication_worker_prompt(mode, worker_id, worker_count, arm)`:

1. **Deterministic Initial Search Slot (`slot_id`)**:
   Assign each worker `w0`–`w3` a distinct initial search slot (`slot-0` through `slot-3`) in Epoch 1 so the 4 workers do not all start by running the exact same command on the exact same contract:
   - `slot-0` (`w0`): Entry-point asset flows, deposits/withdrawals, and balance/share accounting.
   - `slot-1` (`w1`): State transitions, callbacks, reentrancy surfaces, and external contract calls.
   - `slot-2` (`w2`): Access control, initialization, privileged hooks, and parameter validation.
   - `slot-3` (`w3`): Edge-case math, rounding/precision, fee/reward distribution, and liquidation/order matching.
   (All workers remain generalists and may move across scopes as evidence dictates; the slot only diversifies initial exploration, matching `antiherding.py`.)
2. **Periodic Status Record (`independent_journal` and `communicating`)**:
   Every 4 tool turns, the worker must write `/home/agent/experiment/status.json` with:
   - `slot_id`: current investigation slot/scope
   - `checked`: concrete `File.sol::function` paths and invariants inspected this epoch
   - `finding`: any candidate vulnerability mechanism discovered (or `""` if none)
   - `negative_evidence`: assumptions or attack paths checked and ruled out (or why a reproduction failed)
   - `evidence`: commands/tests run and their concrete outputs
   - `adopted_from`: list of `{"worker_id": ..., "epoch": ..., "verification_performed": ..., "preserved_variation": ...}` whenever the worker builds on a record from `peer_view.json` (`[]` when none)
   - `next`: concrete plan for the next 4 tool turns
   - `confidence`: float in `[0.0, 1.0]` estimating the probability that the worker's current candidate artifact solves the task
3. **Evidence-Before-Adoption & Variation-Preservation Rules (`communicating` and `communicating_withhold` only)**:
   - Treat peer records in `/home/agent/experiment/peer_view.json` as unverified hypotheses, never as ground truth.
   - **Rule 1 (Verify before adopting)**: Before adopting a peer's finding or approach, run at least one independent code check or test command to verify its premise, and record that check in `adopted_from[].verification_performed`.
   - **Rule 2 (Use negative evidence)**: If a peer's `negative_evidence` shows a specific fixture error or falsified path, avoid repeating the identical failed command; either fix the concrete blocker or investigate an uncovered path.
   - **Rule 3 (Preserve variation / anti-herding)**: After adopting a peer's finding, preserve at least one distinct variation (e.g., an alternative trigger function, a second vulnerability check, or an independent patch/test formulation) and record it in `adopted_from[].preserved_variation`. Never copy a peer's status record verbatim.
4. **Prompt Parity Rule**:
   - `independent_journal` and `communicating` share the exact same task instructions, slot assignment, candidate artifact rules, and `status.json` schema instructions. Only the paragraph describing Peer Records in `peer_view.json` and `adopted_from` is added for `communicating`.
   - `independent_silent` has the exact same task instructions, slot assignment, and candidate artifact rules, omitting the `status.json` and `peer_view.json` paragraphs.

## 3. Shared Finalizer & Offline Grading

- At the end of Epoch 8 (turn 32), run the **exact same shared substrate finalizer** (`T = 0.0`, fresh sandbox, `step_limit = 16`) in every arm:
  - In `DETECT`, it merges the 4 workers' `/home/agent/experiment/candidate_audit.md` files into `submission/audit.md`.
  - In `PATCH`, it inspects the 4 workers' `candidate.diff` files (and local test outputs), applies the chosen patch on a clean checkout at `base_commit`, and exports `submission/agent.diff`.
  - In `EXPLOIT`, it selects one coherent worker bundle `candidate_txs.json` (or replays it on a fresh clean chain) to write `submission/txs.json`.
- Also grade **every worker's final Epoch 8 candidate artifact offline** on the host using `grade_artifact` (cached by SHA-256) and save to `workers/<worker_id>/offline_grade.json`. This separates:
  - **Team submitted score**: Official grade of `submission/`.
  - **Team oracle coverage (`any_worker_solved` / union recall)**: Whether *any* of the 4 workers produced a passing candidate (for `PATCH`/`EXPLOIT`) or the union of detected gold vulnerabilities across the 4 workers (for `DETECT`).

## 4. Matched-Turn vs. Matched-Expenditure Evaluation

Reading `peer_view.json` consumes input tokens, so 8 epochs of `communicating` costs slightly more tokens than 8 epochs of `independent_journal`. Your analysis script (`evmbench/experiments/test_time/analyze_communication.py`) must compute **both**:

1. **Turn-Matched Comparison (Epoch 8 / 32 turns)**:
   All arms compared after 8 epochs (32 tool turns per worker).
2. **Expenditure-Matched (Token-Matched) Comparison**:
   For each `(mode, audit_id)`, let $B_{\text{journal}}$ be the total worker tokens spent by `independent_journal` at Epoch 8. Find the latest epoch $e^* \le 8$ of `communicating` whose cumulative worker tokens (including `peer_view.json` reads) are $\le B_{\text{journal}}$. Finalize and grade the `communicating` workers' snapshots at epoch $e^*$ (using the cached artifact SHA-256 if unchanged) and report that score alongside the full Epoch-8 score.

## 5. Implementation Steps

1. Implement `build_communication_worker_prompt` in `evmbench/experiments/test_time/prompts.py`.
2. Implement `evmbench/experiments/test_time/analyze_communication.py` to compute:
   - Official `submitted_score` and `max_score` per arm (`independent_silent`, `independent_journal`, `communicating`, and `communicating_withhold` if run).
   - Offline `oracle_coverage` across the 4 workers per arm.
   - Paired contrasts:
     - `communicating - independent_journal` (pure effect of peer communication)
     - `independent_journal - independent_silent` (pure effect of periodic status writing)
     - `communicating - independent_silent` (combined effect)
   - Both **turn-matched** (Epoch 8) and **token-matched** ($e^*$ where `tokens <= B_journal`) tables.
   - Token and USD breakdown by role (`worker`, `finalizer`, `detect_judge`).
   - **Duplicate Investigation Rate**: Fraction of `(worker_i, worker_j, epoch)` pairs ($i \neq j$) whose `checked` field references the same normalized `Contract.sol::function` identifier.
   - **Composition & Adoption Trace Table**: Every instance where a worker in `communicating` populated `adopted_from` or cited a peer (`w0`–`w3`), tracing whether that worker's final artifact achieved a higher offline grade or contributed to a higher submitted grade.
   - **Withhold Filter Generator**: For any `(mode, audit_id)` where `communicating` achieved a higher submitted score than `independent_journal`, output `withhold_spec.json` listing the exact `(worker_id, epoch)` records cited in `adopted_from` by the contributing worker(s).
3. Add unit tests in `project/evmbench/tests/test_experiment1_communication.py` verifying:
   - Prompt parity across `independent_silent`, `independent_journal`, and `communicating`.
   - Slot assignment (`slot-0`..`slot-3`) for `w0`..`w3`.
   - `analyze_communication.py` correctly computes turn-matched, token-matched ($e^*$), duplicate rate, and `withhold_spec.json` on synthetic campaign directories without network calls.

## 6. Execution Steps

1. **Run unit tests first**:
   ```bash
   uv run pytest tests/test_test_time_substrate.py tests/test_experiment1_communication.py
   ```
2. **Check live environment**:
   Verify `OPENROUTER_API_KEY` is set and the Yudai-pinned audit images pass preflight. If either is missing, generate all planned campaign configs under `runs/test-time/communication/planned/` via `cli.py plan` and stop cleanly without inventing scores.
3. **Smoke Run**:
   Run `2024-07-basin`, `detect`, `independent_journal`, 1 worker, 1 epoch (4 turns). Verify `status.json`, non-zero ledger tokens, and `grade/grade.json`.
4. **Pilot Runs**:
   For each mode (`detect`, `patch`, `exploit`) and each pilot audit (`2024-07-basin`, `2024-01-curves`, `2026-01-tempo-stablecoin-dex`), run `independent_silent`, `independent_journal` (reusing `w0`–`w3` at turn 32 from Experiment 2 if already on disk), and `communicating`.
5. **Withhold Intervention (Conditional Live Run)**:
   For any `(mode, audit_id)` where `communicating > independent_journal` and `withhold_spec.json` is non-empty, run `communicating_withhold` with those `(worker_id, epoch)` records filtered out by `board.view`. Record `withhold_supports_composition` if the score drops back toward `independent_journal`, or `withhold_does_not_explain_gain` if the gain persists.
6. **Generate Report**:
   Run `analyze_communication.py` to write `runs/test-time/communication/analysis/summary.json` and `runs/test-time/communication/analysis/REPORT.md`.

## 7. Done Criteria

- `tests/test_experiment1_communication.py` passes.
- Either live pilot results and `REPORT.md` are on disk, or (if credentials/images are absent) planned manifests for all 3 modes × 3 audits × 3 arms are written under `runs/test-time/communication/planned/`.
- Every model call in `ledger.jsonl` uses `deepseek/deepseek-v4.1-flash-1017`.
