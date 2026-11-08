# Prompt 05 — Experiment 5: Learn Cost-Aware Stopping from Complete Trajectories

Copy and paste everything below the horizontal rule into a coding agent **after Prompt 00 is complete**. This experiment operates offline on the checkpointed trajectory bank from Experiment 2 (calling the grader only for any uncached checkpoint artifacts).

---

You are implementing and running **EVMBench Experiment 5: Learned Cost-Aware Stopping from Complete Trajectories**, adapted from *CASE: Cost-Aware Stopping for Efficient Long-Video Agents* (§3–4, Appendix C.4, and released `core.py` / `replay.py`).

Read `project/evmbench/docs/test-time-experiments/README.md` and `project/evmbench/docs/test-time-experiments/prompts/00-shared-substrate.md` first. Use `evmbench/experiments/test_time/`.

## 1. Scientific Question & Publishable Claim

- **Question**: Can a lightweight predictor trained on complete trajectories' causal progress features decide when to stop an EVMBench trajectory early—saving a substantial fraction of tokens while preserving almost all of the full-budget official score?
- **Why CASE's Finalizer Control (Appendix C.4) is critical**:
  In CASE, an apparent accuracy gain from early stopping can be an artifact of running a strong finalizer at the stopping point vs. no finalizer on the raw trajectory. To isolate the **stopping decision** from **finalization**:
  - Every checkpoint $t \in \{4, 8, 16, 32, 64\}$—including the full-budget baseline `full` at $t = 64$—must be finalized and graded using the **exact same finalizer procedure** (`finalizer_controlled = True`).
  - Furthermore, offline replay proves only **token savings for a fixed search policy**. It does **not** prove that reallocating saved tokens to new workers improves team score (that requires a separate live experiment). Your report must state this explicitly.
- **Publishable mechanism to test**:
  - **Observed execution progress vs. self-reported confidence**: Agents often report high `confidence` early on an unverified hypothesis, or low `confidence` while actively making compilation/test progress on an exploit or patch. Test whether observable progress features (`artifact_changed`, `public_test_passed`, `new_status_finding`, `negative_evidence_added`, `diff_bytes`, etc.) beat both the worker's self-reported `confidence` threshold and tuned `fixed_turn` / `fixed_token` cutoffs on held-out repositories.

## 2. Repository-Clustered Splits (`full_split` and `pilot_loo_diagnostic`)

All trajectories for a given `audit_id` belong to **one repository cluster** and must never be split across `train`, `calibration`, and `eval`. Keep `detect`, `patch`, and `exploit` as separate datasets.

Support two evaluation modes in `stopping_fit.py` depending on how many distinct repositories $N_{\text{repos}}$ exist in the Experiment 2 bank for that mode:

1. **Full Paper Mode (`evaluation_regime = "full_split"`, when $N_{\text{repos}} \ge 12$)**:
   - Sort `audit_ids` lexicographically and shuffle once with `random.Random(20261107)`.
   - Assign **50%** of repositories to `train`, **25%** to `calibration`, and **25%** to `eval`.
   - Write `splits.json` before reading any grade files and freeze it.
2. **Pilot Diagnostic Mode (`evaluation_regime = "pilot_loo_diagnostic"`, when $3 \le N_{\text{repos}} < 12$, e.g. the 3 pilot repositories)**:
   - Do **not** dead-end or refuse to run when only the 3 pilot audits (`2024-07-basin`, `2024-01-curves`, `2026-01-tempo-stablecoin-dex`) have been collected!
   - Instead, run **Leave-One-Repository-Out Cross-Validation (LOO-CV)** across the $N_{\text{repos}}$ repository clusters:
     - For fold $k \in \{0, \dots, N_{\text{repos}}-1\}$:
       - `eval_repos = [repos[k]]` (8 trajectories × 5 checkpoints = 40 checkpoint rows, strictly held out).
       - `calibration_repos = [repos[(k + 1) % N_repos]]` (used to select $\lambda$ and the stopping threshold $\tau$).
       - `train_repos = [all remaining repos]` (used to standardize features and fit the weighted Ridge coefficients).
     - Pool the out-of-fold `eval_repos` predictions across all $N_{\text{repos}}$ folds to compute the evaluation table, and label the output `"evaluation_regime": "pilot_loo_diagnostic", "not_paper_claim": true`.
   - If $N_{\text{repos}} < 3$, write `analysis/NOT_ENOUGH_REPOSITORIES.md` and run on synthetic fixtures in tests only.

## 3. Causal Checkpoint Features (`stopping_features.py`)

Implement `evmbench/experiments/test_time/stopping_features.py`.

**Strict No-Leakage Rule**: `stopping_features.py` must **never** read `offline_grade.json`, `grade.json`, or any gold vulnerability/patch/test file! Enforce an explicit allowlist of feature column names (`PROGRESS_FEATURE_COLUMNS`).

For each worker trajectory $w$ and checkpoint $t \in \{4, 8, 16, 32, 64\}$ (index $k \in \{0, 1, 2, 3, 4\}$), extract:

| Feature Column | Included in `progress_ridge`? | Definition |
| --- | --- | --- |
| `turn_fraction` | Yes | `turns / 64.0` |
| `tokens_fraction_of_cap` | Yes | `tokens_so_far / 131072.0` (causal normalization using a fixed constant, **never** `tokens_at_end` which is unknown at step $t$!) |
| `delta_tokens` | Yes | `(tokens_so_far - prev_tokens_so_far) / 32768.0` |
| `artifact_present` | Yes | `1.0` if the mode candidate artifact at $t$ is non-empty, else `0.0` |
| `artifact_changed` | Yes | `1.0` if `artifact_sha256(t) != artifact_sha256(t_prev)` and non-empty, else `0.0` |
| `unchanged_streak` | Yes | Number of consecutive checkpoints up to $t$ where `artifact_changed == 0` |
| `status_finding_nonempty` | Yes | `1.0` if `status.json` at $t$ has a non-empty `finding` string, else `0.0` |
| `new_status_finding` | Yes | `1.0` if `status.finding` at $t$ is non-empty and differs from $t_{\text{prev}}$, else `0.0` |
| `negative_evidence_added` | Yes | `1.0` if `status.negative_evidence` at $t$ is non-empty and differs from $t_{\text{prev}}$, else `0.0` |
| `tool_commands_total` | Yes | Total bash tool calls executed through turn $t$ |
| `test_command_ran` | Yes | `1.0` if any bash command in this epoch invoked `forge test`, `hardhat test`, `ploit`, or `cast`, else `0.0` |
| `last_command_exit_zero` | Yes | `1.0` if the last bash tool command in this epoch exited `0`, else `0.0` |
| `diff_bytes_log` | Yes | `math.log1p(diff_bytes)` in `PATCH` mode, else `0.0` |
| `files_touched` | Yes | Number of files modified in `candidate.diff` (`PATCH`), else `0.0` |
| `candidate_tests_count` | Yes | Number of files in `candidate_tests/` (`PATCH`), else `0.0` |
| `txs_count` | Yes | Number of transactions in `candidate_txs.json` (`EXPLOIT`), else `0.0` |
| `report_headings_count` | Yes | Count of markdown headings (`#`) in `candidate_audit.md` (`DETECT`), else `0.0` |
| `confidence` | **No** (`confidence` baseline only) | Worker's self-reported `confidence ∈ [0, 1]` from `status.json` (default `0.0` if missing). Excluded from `PROGRESS_FEATURE_COLUMNS`! |

Notice the crucial causal fix above: at deployment time at step $t$, `tokens_at_end` ($T=64$) is in the future and unknown! Therefore **features** at step $t$ must only use quantities observable at step $t$ (`tokens_so_far`, fixed constants, and history $\le t$). `tokens_at_end` may only appear in the **training target** and offline **regression sample weights**!

## 4. Target Construction, Weighted Ridge Fit, and Calibration (`stopping_fit.py`)

Implement `evmbench/experiments/test_time/stopping_fit.py` following CASE (`core.py`):

### Step 4.1: Normalized Score and Continuation Decision Gap Target

For each trajectory $i$ and checkpoint $t \in \{4, 8, 16, 32, 64\}$:
- Let $s_i(t) = \text{official\_score}(i, t) / \text{max\_score}(i) \in [0, 1]$ be the normalized official score at checkpoint $t$ (using the cached offline grades from Experiment 2, finalized with the same `finalizer_mode`).
- Let $s_i^*(>t) = \max_{t' > t} s_i(t')$ for $t < 64$, and $s_i^*(>64) = s_i(64)$.
- For a cost trade-off parameter $\lambda \in \{0.0, 0.1, 0.25, 0.5, 1.0\}$, define the continuation decision gap:
  $$\text{gap}_\lambda(i, t) = \bigl(s_i^*(>t) - s_i(t)\bigr) - \lambda \cdot \frac{\text{tokens}_i(64) - \text{tokens}_i(t)}{\max(\text{tokens}_i(64), 1)}$$
  At the final checkpoint $t = 64$, $\text{gap}_\lambda(i, 64) = 0$.
  When $\text{gap}_\lambda(i, t) \le 0$, continuing past $t$ does not improve the eventual score enough to justify the remaining token expenditure.

### Step 4.2: Closed-Form Weighted Ridge Regression (NumPy)

Implement closed-form weighted Ridge regression in pure NumPy (so it has zero fragile external solver dependencies and is 100% deterministic):
- Compute feature mean $\mu$ and standard deviation $\sigma$ (with floor `1e-8`) on **`train` rows only** ($t \in \{4, 8, 16, 32\}$).
- Standardize $X_{\text{train}} = (F_{\text{train}} - \mu) / \sigma$ and append a bias column of `1.0`s.
- Set sample weights $w_i = \text{tokens}_i(64) / \text{mean}(\text{tokens}(64))$ on `train` rows so longer/costlier trajectories receive proportional weight (matching CASE's weighted Ridge).
- For Ridge regularization $\alpha = 1.0$ (do not penalize the bias term):
  $$\hat{\beta}_\lambda = \left(X_{\text{train}}^\top W X_{\text{train}} + \alpha I_{\text{no-bias}}\right)^{-1} X_{\text{train}}^\top W y_{\text{train},\lambda}$$

### Step 4.3: Threshold Calibration on `calibration` Split

For a given trade-off weight $\lambda$ (default headline $\lambda = 0.25$, and swept across $\lambda \in \{0.0, 0.1, 0.25, 0.5, 1.0\}$ to trace the Pareto frontier):
- On the **`calibration` split only**, evaluate candidate thresholds $\tau$ over the empirical quantiles of $\hat{g}_\lambda = X_{\text{cal}} \hat{\beta}_\lambda$ (plus `[-0.1, -0.05, 0.0, 0.02, 0.05, 0.1]`).
- A trajectory stops at the earliest checkpoint $t_{\text{stop}} \in \{4, 8, 16, 32, 64\}$ where $\hat{g}_\lambda(t) \le \tau$ (and `artifact_present == 1`, or $t = 64$).
- Select $\tau^*(\lambda)$ that maximizes the calibration net utility on `calibration` trajectories:
  $$U_\lambda(\tau) = \frac{1}{|I_{\text{cal}}|} \sum_{i \in I_{\text{cal}}} \left[ s_i(t_{\text{stop}}) - \lambda \cdot \frac{\text{tokens}_i(t_{\text{stop}})}{\max(\text{tokens}_i(64), 1)} \right]$$
- Tune the baseline thresholds (`fixed_turn`, `fixed_token`, `confidence`) on the **exact same `calibration` split and utility $U_\lambda$** so every baseline has an equally fair calibration step!
- Freeze `policy.json` before evaluating on `eval`.

## 5. Baselines and Replay (`stopping_replay.py`)

Implement `evmbench/experiments/test_time/stopping_replay.py` to replay all six policies on the held-out `eval` trajectories:

| Policy Name | Stopping Rule on Held-Out `eval` Trajectories |
| --- | --- |
| `full` | Always stops at $t = 64$ (or worker's natural `stopped_turn`). Baseline for 100% token spend. |
| `fixed_turn` | Stops at the calibration-selected turn $t^* \in \{4, 8, 16, 32, 64\}$. |
| `fixed_token` | Stops at the first checkpoint where `tokens_so_far >= B^*` (with $B^*$ chosen on `calibration`). |
| `stagnation` | Stops at the first checkpoint where `artifact_present == 1` and `unchanged_streak >= 2` (artifact SHA unchanged for 2 consecutive checkpoints), else $t = 64$. |
| `confidence` | Stops at the first checkpoint where `artifact_present == 1` and worker `confidence >= c^*` (with $c^*$ chosen on `calibration`). |
| `progress_ridge` | Stops at the first checkpoint where `artifact_present == 1` and predicted gap $\hat{g}_\lambda(t) \le \tau^*(\lambda)$, else $t = 64$. |

### Required Evaluation Metrics (`analysis/summary.json` and `REPORT.md`)

On held-out `eval` trajectories (per mode and per $\lambda$, with headline $\lambda = 0.25$):
- `mean_normalized_score` and `mean_raw_score`
- `mean_worker_tokens`, `mean_finalizer_tokens`, `mean_worker_usd`
- `token_savings_fraction`: $1 - \frac{\text{mean\_tokens}(\text{policy})}{\text{mean\_tokens}(\text{full})}$
- `score_delta_vs_full`: $\text{mean\_normalized\_score}(\text{policy}) - \text{mean\_normalized\_score}(\text{full})$
- `score_delta_vs_confidence`: $\text{mean\_normalized\_score}(\text{progress\_ridge}) - \text{mean\_normalized\_score}(\text{confidence})$
- `score_delta_vs_fixed_turn`: $\text{mean\_normalized\_score}(\text{progress\_ridge}) - \text{mean\_normalized\_score}(\text{fixed\_turn})$
- **Mechanism Diagnostic Counts** (proving `progress_ridge` is not just a proxy for `confidence`):
  - `continued_when_low_confidence_and_improved`: Fraction of `eval` trajectories where at some $t < 64$, `confidence < 0.5` (or `confidence >= c^*` triggered a premature stop for the `confidence` policy), while `progress_ridge` correctly chose to continue (or stop) and achieved a higher final score than `confidence`.
  - `premature_stop_rate`: Fraction of `eval` trajectories where a policy stopped at $t_{\text{stop}} < 64$ and $\max_{t' > t_{\text{stop}}} s_i(t') > s_i(t_{\text{stop}})$.
- `finalizer_controlled`: Boolean asserting that every checkpoint grade used the identical finalizer mode/prompt hash.
- Explicit statement in `REPORT.md`: *"Replay measures token savings under a fixed search policy and does not reallocate saved budget to new live workers."*

## 6. Unit Tests (`project/evmbench/tests/test_experiment5_stopping.py`)

Write offline unit tests verifying:
1. `PROGRESS_FEATURE_COLUMNS` contains neither `score` nor `confidence` nor `tokens_at_end`.
2. On a synthetic trajectory where score jumps from `0.0` at turn 8 to `1.0` at turn 32, `gap(t=8)` is positive for $\lambda = 0.0$ and decreases monotonically as $\lambda$ increases.
3. Closed-form weighted Ridge regression in `stopping_fit.py` matches known analytical $2 \times 2$ linear-system solutions.
4. Repository-clustered splitting keeps all 8 workers and 5 checkpoints of an `audit_id` inside the same split (`train`, `calibration`, or `eval`), and `pilot_loo_diagnostic` cleanly runs 3-fold leave-one-repository-out cross-validation when given 3 repositories.
5. Replay of `full` returns the exact turn-64 cached grade and `token_savings_fraction == 0.0`.

## 7. Done Criteria

- `tests/test_experiment5_stopping.py` passes.
- When Experiment 2 trajectories exist on disk (even if only the 3 pilot repositories), `stopping_fit.py` and `stopping_replay.py` execute end-to-end (using `pilot_loo_diagnostic` if $3 \le N_{\text{repos}} < 12$, or `full_split` if $N_{\text{repos}} \ge 12$) and write `policy.json`, `summary.json`, and `REPORT.md`.
- Every LLM call (if any uncached checkpoint grades were needed) uses `deepseek/deepseek-v4.1-flash-1017`.
