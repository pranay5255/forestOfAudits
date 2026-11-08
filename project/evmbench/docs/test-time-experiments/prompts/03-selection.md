# Prompt 03 — Experiment 3: Compare Selectors on the Same Frozen Candidates

Copy and paste everything below the horizontal rule into a coding agent **after Prompt 00 is complete**. Reuse the 8 turn-64 `PATCH` checkpoints from Experiment 2 when present.

---

You are implementing and running **EVMBench Experiment 3: Candidate Selection on a Frozen Pool**, adapted from *CodeMonkeys* (§2.3, Figure 6, and released `majority_voting.py` / `selection_state_machine.py`).

Read `project/evmbench/docs/test-time-experiments/README.md`, `prompts/00-shared-substrate.md`, and `evmbench/nano/grade/patch.py` first.

## 1. Scientific Question & Simple Explanation

Can the system recognize a good security patch it has already generated?

Suppose 8 `deepseek/deepseek-v4.1-flash-1017` workers produce 8 candidate patches for an audit:
- **Patch A** blocks the exploit by disabling or reverting withdrawals altogether, breaking legitimate user withdrawals.
- **Patch B** fixes the vulnerability while preserving all legitimate contract behavior.
- **Patch C** looks plausible in prose review but leaves the vulnerability exploitable.

We **freeze** those 8 candidate patches (`w0`–`w7` at turn 64 from Experiment 2's `PATCH` bank) and give the **exact same 8 patches** to four selection methods (`random`, `review`, `test_vote`, `active`). Only after each selector outputs its chosen patch SHA-256 do we look up that patch's official `PatchGrader` score.

This cleanly separates two failure modes:
- **Generation failure (`oracle_score == 0`)**: None of the 8 patches fixes the vulnerability.
- **Selection failure (`oracle_score > 0` and `submitted_score < oracle_score`)**: A working patch was already in the pool, but the selector submitted a worse one.

## 2. Freezing and Deduplicating the Candidate Pool

For each pilot `PATCH` audit (`2024-07-basin`, `2024-01-curves`, `2026-01-tempo-stablecoin-dex`):

1. Load the 8 turn-64 `PATCH` checkpoints (`w0`–`w7`) from Experiment 2 (`independent_journal`, `deepseek/deepseek-v4.1-flash-1017`, $T=0.7$).
2. Copy each worker's `candidate.diff` and archived `candidate_tests/` directory into `runs/test-time/selection/<audit_id>/pool/<worker_id>/`.
3. Compute each diff's `sha256` (use `"EMPTY"` if `candidate.diff` is 0 bytes).
4. **Canonical Pool Order (`pool_order`)**: Sort the 8 candidates deterministically by `(sha256, worker_id)`. Write `runs/test-time/selection/<audit_id>/pool/manifest.json`. Every selector receives the pool in this exact `pool_order`.
5. **Host-Only Official Grading**:
   Grade each unique non-empty diff once using `grade_submission` (`PatchGrader` in a fresh sandbox) and store the result at `runs/test-time/selection/<audit_id>/official_grades/<sha256>.grade.json`.
   - **Strict isolation**: No selector sandbox or prompt may ever read `official_grades/` or `audits/<audit_id>/{test,patch,findings}`!

## 3. Public Functionality Checks & Worker Test Execution Matrix

Before running `test_vote` or `active`, build the execution matrix in `evmbench/experiments/test_time/selection.py`:

### Step 3.1: Collect All Worker-Generated Tests

Gather all test files archived in `pool/<worker_id>/candidate_tests/` across `w0`–`w7`. If a worker wrote no tests, record `worker_tests_count = 0` for that worker and keep its diff in the pool.

### Step 3.2: Execute on Unpatched `base_commit` and on Each Unique Candidate Diff

In a clean throwaway sandbox (or resetting `/home/agent/audit` to `audit.base_commit` before each candidate):

1. **Unpatched Base (`base_commit`)**:
   - Run the repository's existing public test suite using `audit.get_invariant_test_command(write_to=...)` and parse with `_parse_test_output` from `evmbench/nano/grade/patch.py`. Record the set of baseline failing tests `base_failures`.
   - Run each worker-generated test $t_j$ on `base_commit` (by copying $t_j$ to its repo-relative path under `/home/agent/audit/<audit.test_dir>/`, running `forge test --match-path <rel_path>` or `npx hardhat test <rel_path>`, then removing $t_j$). Record `base_passed[t_j] ∈ {True, False}` and `base_compile_error[t_j] ∈ {True, False}`.
     - Any generated test that fails to compile on `base_commit` is marked `invalid_test_on_base = True`.
2. **Each Unique Candidate Patch $P_i$**:
   - Reset `/home/agent/audit` to `base_commit` (`git checkout --detach <base_commit> && git reset --hard && git clean -f`).
   - If `P_i` is empty or `git apply --binary` fails, mark `applies_cleanly = False` and `breaks_functionality = True`.
   - Otherwise, run the repository's public test suite (`audit.get_invariant_test_command(write_to=...)`) and parse with `_parse_test_output`:
     - Check the same functionality rule as `PatchGrader`: if any test fails that is not in `audit.tests_allowed_to_fail` (and `base_failures`) or exceeds `audit.post_patch_fail_threshold`, mark **`breaks_functionality = True`** (`public_tests_passed = False`); otherwise **`breaks_functionality = False`** (`public_tests_passed = True`).
   - Run each valid worker-generated test $t_j$ against $P_i$ and record `patch_passed[P_i, t_j] ∈ {True, False}`.
     - **Test Polarity**:
       - If $t_j$ failed on `base_commit` (`base_passed[t_j] == False`) and passes on $P_i$ (`patch_passed[P_i, t_j] == True`), $t_j$ is a **satisfied regression test** (`regression_pass = True`).
       - If $t_j$ passed on `base_commit` (`base_passed[t_j] == True`, meaning the worker wrote an un-inverted exploit PoC that succeeds when vulnerable) and fails/reverts on $P_i$ while `public_tests_passed == True`, record `inverted_poc_blocked = True` separately in the matrix so both standard regression tests and inverted PoC tests are visible in diagnostics; use `effective_test_votes` = count of standard regression passes (plus inverted PoC blocks if specified in `test_manifest.json`).

## 4. The Four Selectors (`selection.py`)

All four selectors operate on the exact same `pool/manifest.json` and return one chosen candidate (`chosen_worker_id`, `chosen_sha256`) plus a selector ledger:

| Selector | Information Available | Selection Procedure | Model Calls |
| --- | --- | --- | --- |
| `random` | The 8 candidate IDs in `pool_order` | `random.Random(20261107 + audit_index).choice(pool_order)` | `0` (`0` tokens, `$0.00`) |
| `review` | Public task instructions, relevant in-scope source files, and the 8 candidate diffs in `pool_order` (no test execution results) | One `deepseek/deepseek-v4.1-flash-1017` call at `temperature = 0.0` (role `selector`). Prompt asks the model to analyze the diffs for root-cause correctness and functionality preservation and output JSON `{"chosen_sha256": "...", "reasoning": "..."}`. | `1` call at `T = 0.0` |
| `test_vote` | Execution matrix from Step 3 (`applies_cleanly`, `public_tests_passed`, `breaks_functionality`, and worker-generated test pass counts) | Pure deterministic ranking (zero LLM calls): sort candidates by key `(applies_cleanly, not breaks_functionality, regression_pass_count, effective_test_votes, -pool_order_index)` descending. Pick rank 1. | `0` LLM calls (sandbox execution only) |
| `active` | Top **3 deduplicated** candidates from `test_vote` + execution matrix summary + sandbox access to write & run up to 3 discriminating tests | 1. **Deduplicate**: Collapse candidates with identical `sha256` or identical normalized diff + test-outcome vector, and retain the top 3 distinct candidates from `test_vote` (`C1, C2, C3`). (If only 1 non-empty candidate exists, return it immediately.)<br/>2. **Active Loop (up to 3 rounds)**: In each round, `deepseek/deepseek-v4.1-flash-1017` (`T = 0.0`, role `test_writer`) sees the task, the 3 candidate diffs, and the test matrix so far. It may either:<br/>  - emit a new discriminating test file (asserting either that a subtle attack variant is blocked or that a legitimate user operation still succeeds) which the host executes on `base_commit` and `C1, C2, C3`, returning stdout/pass/fail to the model, OR<br/>  - emit its final decision `{"action": "select", "chosen_sha256": "...", "reasoning": "..."}`. | `1..3` calls at `T = 0.0` |

## 5. Metrics & Equal-Expenditure Comparison

For each audit and across the pilot (with cluster bootstrap over `audit_id`), write to `runs/test-time/selection/analysis/summary.json` and `REPORT.md`:

1. **Core Selection Table**:
   - `oracle_score`: $\max_{i \in 1..8} \text{official\_score}(P_i)$
   - `generation_failure`: `1` if `oracle_score == 0` else `0`
   - Per selector (`random`, `review`, `test_vote`, `active`):
     - `submitted_score`: official score of the selector's chosen patch
     - `selection_success_given_solvable`: `1` if `submitted_score == oracle_score` (on audits with `oracle_score > 0`)
     - `selection_failure`: `1` if `oracle_score > 0` and `submitted_score < oracle_score`
     - `selector_tokens` and `selector_usd`
   - `false_attack_only_vote`: Count of candidates where worker-generated attack tests passed (`regression_pass_count > 0`) **but** `breaks_functionality == True` (the patch broke existing public functionality tests), and whether a naive ranker that ignored `public_tests_passed` would have ranked that broken patch above the oracle patch!
2. **Equal-Expenditure Comparison (`selection_versus_more_candidates.csv`)**:
   Using Experiment 2's cost data on the same audit:
   - Compute the total expenditure of **`4 workers (at turn 64) + active selector`**:
     $$C_{\text{4+active}} = \text{Cost}(4\text{ workers}) + \text{Cost}(\text{active selector})$$
   - Compare its official submitted score against spending equal or greater budget on **more candidates with cheap selection**, e.g. **`8 workers + random`** and **`8 workers + review`**, as well as **`8 workers + active`** vs. the closest expenditure point on the breadth-depth curve.

## 6. Unit Tests (`project/evmbench/tests/test_experiment3_selection.py`)

Write offline unit tests verifying:
1. `pool_order` manifest contains the exact same 8 SHA-256 values across all four selectors.
2. Staging assertions reject any path containing `official_grades` or `audits/<id>/test`.
3. `test_vote` ranks a patch with `breaks_functionality=False` and 1 passing regression test **above** a patch with `breaks_functionality=True` (broken public tests) and 3 passing narrow attack tests, and logs `false_attack_only_vote = 1` for the naive comparator.
4. `active` deduplicates byte-identical diffs before selecting the top 3 candidates so 3 identical copies of one diff do not crowd out distinct candidates.

## 7. Done Criteria

- `tests/test_experiment3_selection.py` passes.
- Either live selection results on the frozen pool and `REPORT.md` are written, or (if credentials/images/pool are absent) planned manifests are written.
- Every LLM call in `ledger.jsonl` uses `deepseek/deepseek-v4.1-flash-1017`.
