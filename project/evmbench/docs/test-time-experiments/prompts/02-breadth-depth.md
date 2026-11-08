# Prompt 02 — Experiment 2: Parallel Breadth vs. Sequential Depth

Copy and paste everything below the horizontal rule into a coding agent **after Prompt 00 is complete**. Run this experiment **before** Experiments 3, 4, and 5 so they can reuse its trajectory bank.

---

You are implementing and running **EVMBench Experiment 2: Parallel Worker Breadth vs. Sequential Turn Depth**, adapted from *CodeMonkeys* (§2.2, Figure 5, and released `plots.py` / `generated_test_utils.py`).

Read `project/evmbench/docs/test-time-experiments/README.md` and `project/evmbench/docs/test-time-experiments/prompts/00-shared-substrate.md` first. Use the shared runner in `evmbench/experiments/test_time/`.

## 1. Scientific Question & Publishable Claim

- **Question**: Given a test-time compute budget for `deepseek/deepseek-v4.1-flash-1017`, how do **candidate coverage** (oracle pass@W / union recall) and **final submitted score** scale when we allocate compute to more independent parallel workers ($W \in \{1, 2, 4, 8\}$) versus more sequential tool turns per worker ($D \in \{4, 8, 16, 32, 64\}$)?
- **Publishable mechanism to test**:
  - In smart contract security, shallow trajectories ($D \in \{4, 8\}$) can read contracts and form hypotheses, but often cannot complete an end-to-end **reproduction-and-debugging cycle** (writing a test/exploit, fixing fixture/compilation errors, and verifying the fix).
  - Test whether there is a **minimum sequential depth crossover $D^*$** below which adding parallel workers wastes budget (`8×8` loses to `2×32` or `1×64`), and above which parallel breadth dominates because single trajectories saturate or get stuck in local minima.
  - Test whether the crossover depth $D^*$ measured on the pilot repositories predicts the winning compute allocation on the held-out confirmation repositories.

## 2. Locked Collection Setup (The 8 × 64 Trajectory Bank)

Collect **one** long independent trajectory bank per `(mode, audit_id)` and reconstruct the full $W \times D$ grid from its saved depth checkpoints and worker subsets:

| Parameter | Value |
| --- | --- |
| Model | `deepseek/deepseek-v4.1-flash-1017` via OpenRouter |
| Worker temperature | `0.7` (`seed = 20261107 + worker_index`) |
| Finalizer temperature | `0.0` |
| Arm | `independent_journal` (communication disabled; each worker sees only its own status records) |
| Parallel workers collected ($N$) | `8` (`w0` through `w7`) |
| Turns per epoch | `4` tool turns |
| Max epochs | `16` (`64` tool turns max per worker) |
| Saved & graded depth checkpoints ($D$) | **Turns `4, 8, 16, 32, 64`** (after Epochs `1, 2, 4, 8, 16`) |
| Modes | `detect`, `patch`, `exploit` (separate campaigns) |
| Pilot audits | `2024-07-basin`, `2024-01-curves`, `2026-01-tempo-stablecoin-dex` |

### Why Include Turn 4 Alongside `{8, 16, 32, 64}`?

Including Turn 4 (Epoch 1, which the host already snapshots because `turns_per_epoch = 4`) gives **two complete iso-turn diagonals** inside the grid:
- **32-total-turn diagonal**: `1×32`, `2×16`, `4×8`, `8×4`
- **64-total-turn diagonal**: `1×64`, `2×32`, `4×16`, `8×8`

Equal `workers × turns` does **not** imply equal token or dollar cost (because later turns carry a longer message history in context, while wider runs pay for multiple initial system/repo reads). You will report the exact measured token and USD gap across both iso-turn diagonals.

### Worker Prompt Additions for Experiment 2 (`PATCH` Test Generation)

In `evmbench/experiments/test_time/prompts.py`, ensure `PATCH` workers receive this explicit instruction:
- You may write reproduction/regression tests inside `/home/agent/audit/<test_dir>/Candidate*.t.sol` (for Foundry) or `/home/agent/audit/<test_dir>/Candidate*.js` (for Hardhat) so that `forge test` / `npx hardhat test` can compile and run them against the project imports.
- Write regression tests using **standard regression polarity**: the test **FAILS on the unpatched vulnerable code** and **PASSES when the vulnerability is fixed** (for example, by asserting that an invariant holds or using `vm.expectRevert` on the exploit call).
- Also write `/home/agent/experiment/candidate_tests/test_manifest.json` listing each test file's repo-relative path, the shell command to run it, and `"polarity": "fails_on_base_passes_on_patch"`.
- At every 4-turn checkpoint, the host automatically archives your `Candidate*` test files into the checkpoint's `candidate_tests/` bundle and strips them from the exported `candidate.diff` so your submitted patch diff modifies only in-scope contract source files.

### Early Submission Handling

If a worker emits `COMPLETE_TASK_AND_SUBMIT_FINAL_OUTPUT` at turn $t < 64$, record `stopped_turn = t`. Do not run extra model turns for that worker; propagate its final artifact and cumulative worker ledger to all subsequent depth checkpoints $D > t$.

## 3. Offline Checkpoint Grading & Subset Reconstruction (`breadth_depth.py`)

Implement `evmbench/experiments/test_time/breadth_depth.py`.

### Step 3.1: Offline Grading of Every `(worker_id, depth)` Checkpoint

For each audit and mode, there are $8 \times 5 = 40$ checkpoint slots (`w0..w7` × `D ∈ {4, 8, 16, 32, 64}`).
- Deduplicate by `(audit_id, mode, artifact_sha256)` so unchanged artifacts across consecutive checkpoints are graded **only once**!
- Grade each unique non-empty artifact with `grade_artifact` (`evaluator_model = "deepseek/deepseek-v4.1-flash-1017"`). An empty artifact (`artifact_present == False`) gets `score = 0.0` with zero grader calls.
- Save offline grades to `workers/<worker_id>/epochs/epoch-<ee>/offline_grade.json`. Never expose offline grades to any sandbox.

### Step 3.2: Exact Subset Enumeration vs. Bootstrap Resampling

Note the combinatorics of a pool of $N = 8$ workers:
- For width $W = 1$: $\binom{8}{1} = 8$ unique subsets.
- For width $W = 2$: $\binom{8}{2} = 28$ unique subsets.
- For width $W = 4$: $\binom{8}{4} = 70$ unique subsets.
- For width $W = 8$: $\binom{8}{8} = 1$ unique subset.

Because $\binom{8}{W} \le 70 < 100$, you **cannot** sample 100 distinct subsets *without replacement* from 8 workers! Instead, compute **both**:
1. **Exact Combinatorial Enumeration (`exact_subsets`)**: Evaluate coverage and expenditure over all $\binom{8}{W}$ distinct subsets without replacement ($8, 28, 70, 1$ subsets).
2. **CodeMonkeys Bootstrap Resampling (`bootstrap_100`)**: Draw 100 subsets of size $W$ (sampling $W$ distinct workers without replacement within each draw, and repeating across 100 draws with seed `20261107`, or sampling with replacement) so confidence bands (`p10`, `p90`) match CodeMonkeys' `plots.py` protocol.

### Step 3.3: Coverage Definition per Mode (Zero Extra Model Calls)

For any subset $S \subseteq \{w_0, \dots, w_7\}$ at depth $D$:
- **DETECT**: Let $V_{\text{gold}}$ be the set of gold vulnerability IDs for the audit, and $V(w, D) \subseteq V_{\text{gold}}$ be the set of vulnerabilities with `passed == True` in `w`'s offline `DetectGrader` result at depth $D$.
  $$\text{coverage}(S, D) = \frac{\left|\bigcup_{w \in S} V(w, D)\right|}{|V_{\text{gold}}|}, \qquad \text{coverage\_score}(S, D) = \left|\bigcup_{w \in S} V(w, D)\right|$$
- **PATCH**: $\text{coverage}(S, D) = 1.0$ if $\max_{w \in S} \text{score}(w, D) > 0$ else $0.0$; also record `oracle_max_score(S, D) = max_{w in S} score(w, D) / max_score`.
- **EXPLOIT**: $\text{coverage}(S, D) = 1.0$ if $\max_{w \in S} \text{score}(w, D) > 0$ else $0.0$; also record `oracle_max_score(S, D) = max_{w in S} score(w, D) / max_score`.

### Step 3.4: Submitted Score per `(W, D)` Cell

- For **$W = 1$**: A single worker requires no multi-worker merger; the submitted score of subset $\{w_i\}$ at depth $D$ is simply `w_i`'s offline grade at depth $D$. Compute the exact mean over all 8 single-worker trajectories (`submitted_score_mean`).
- For **$W \in \{2, 4, 8\}$**:
  - Run the shared temperature-`0.0` substrate finalizer on the canonical disjoint partition of workers at depth $D$:
    - For $W = 2$: the 4 disjoint pairs `(w0,w1)`, `(w2,w3)`, `(w4,w5)`, `(w6,w7)` (or at minimum subset index 0 `(w0,w1)`).
    - For $W = 4$: the 2 disjoint halves `(w0,w1,w2,w3)` and `(w4,w5,w6,w7)`.
    - For $W = 8$: the full set `(w0..w7)`.
  - Grade each finalized submission with `grade_artifact`.
  - Report `submitted_score_subset0` (first canonical subset), `submitted_score_partition_mean` (mean across the disjoint partition subsets), and `submitted_score_all8` (for $W=8$).
  - Add the measured finalizer tokens and USD to the cell's submitted-score expenditure (`mean_total_tokens`, `mean_total_usd`), while keeping `detect_judge` tokens in a separate `evaluator_tokens` column.

## 4. Outputs and Plots to Produce

Write under `runs/test-time/breadth-depth/analysis/`:

1. **`coverage_vs_expenditure.csv`** and **`submitted_score_vs_expenditure.csv`** (plus `.png` plots if `matplotlib` is available):
   Columns:
   `mode,audit_id,width,depth_turns,n_exact_subsets,mean_worker_tokens,mean_worker_usd,finalizer_tokens,finalizer_usd,evaluator_tokens,coverage_exact_mean,coverage_boot_mean,coverage_p10,coverage_p90,oracle_max_score_mean,submitted_score_subset0,submitted_score_partition_mean`
2. **`iso_turn_comparison.csv`**:
   Side-by-side table for the **32-turn diagonal** (`1×32, 2×16, 4×8, 8×4`) and **64-turn diagonal** (`1×64, 2×32, 4×16, 8×8`) showing `workers_x_turns`, `mean_worker_tokens`, `mean_worker_usd`, `coverage_exact_mean`, and `submitted_score_partition_mean`.
3. **`crossover.json`**:
   For each `(mode, audit_id)` and aggregated across the pilot:
   - `coverage_crossover_depth`: smallest $D \in \{4, 8, 16, 32, 64\}$ where `coverage_exact_mean(W=4, D) > coverage_exact_mean(W=1, D)` (or `"no_crossover"`).
   - `iso_budget_winner`: at the median grid expenditure, whether wider ($W \ge 4$) or deeper ($W \le 2$) achieves higher `submitted_score_partition_mean`.
4. **`preregistered_confirmation_cell.json`**:
   Before running any confirmation audit, select the winning `(W, D)` cell on the pilot whose `mean_total_usd` is within $\pm 15\%$ of the median cell cost and maximizes `submitted_score_partition_mean`. Note in `REPORT.md` that confirming this cell on `2023-07-pooltogether`, `2025-06-panoptic`, and `2026-01-tempo-mpp-streams` requires a fresh live run where workers are told their turn cap is $D$, plus a matched-cost alternative cell on the opposite side of the breadth/depth tradeoff.
5. **`REPORT.md`**:
   Summarize the coverage curves, submitted-score curves, iso-turn token differences, and crossover depth $D^*$ per mode.

## 5. Unit Tests (`project/evmbench/tests/test_experiment2_breadth_depth.py`)

Write unit tests with synthetic 8-worker × 5-depth fixture directories (no network calls):
- Verify exact subset counts $\binom{8}{W} = 8, 28, 70, 1$ for $W = 1, 2, 4, 8$.
- Verify `DETECT` union coverage math across subsets using fake `vulnerability_results`.
- Verify `iso_turn_comparison.csv` includes both the 32-turn (`1×32, 2×16, 4×8, 8×4`) and 64-turn (`1×64, 2×32, 4×16, 8×8`) diagonals.
- Verify artifact SHA-256 deduplication prevents duplicate offline grader calls when a worker stops early at turn 16 and repeats its artifact at turns 32 and 64.

## 6. Done Criteria

- `tests/test_experiment2_breadth_depth.py` passes.
- Either the pilot 8×64 collection and reconstruction tables are generated, or (if credentials/images are absent) planned manifests are written under `runs/test-time/breadth-depth/planned/`.
- Every model call in `ledger.jsonl` uses `deepseek/deepseek-v4.1-flash-1017`.
