# Prompt 04 — Experiment 4: Reuse One, Random, or Selected Prior Attempts

Copy and paste everything below the horizontal rule into a coding agent **after Prompt 00 is complete**. Reuse the 8 first-wave trajectories at turn 32 from Experiment 2 when present.

---

You are implementing and running **EVMBench Experiment 4: Structured Experience Reuse Across Waves**, adapted from *Scaling Test-Time Compute for Agentic Coding* (§3.2.2, Table 1).

Read `project/evmbench/docs/test-time-experiments/README.md` and `project/evmbench/docs/test-time-experiments/prompts/00-shared-substrate.md` first. Use `evmbench/experiments/test_time/`.

## 1. Scientific Question & Publishable Claim

- **Question**: When a first wave of independent workers attempts an audit, can a **second wave** of workers starting in fresh environments at `base_commit` achieve higher individual and submitted scores by reading structured summaries of earlier attempts? And does **Recursive Tournament Voting** (`selected_peer`) pick better summaries than `random_peer` or `self`-refinement?
- **Why the `fresh` restart control is mandatory**: Without a second wave that starts from the original task with no summaries (`fresh`), any improvement in Wave 2 could simply be explained by running additional attempts.
- **Publishable mechanism to test**:
  - If a first-wave summary already contains a complete passing solution (`copy_risk == 1`), a second-wave worker might simply copy it.
  - The surprising, publishable finding is **complementary rescue (`rescue == 1` on `copy_risk == 0`)**: every supplied first-wave attempt failed official evaluation (`score == 0`), yet their summaries record partial discoveries and negative evidence (e.g., Summary A identifies the vulnerable state transition, Summary B shows why a naive flashloan setup reverts) that enable a fresh second-wave worker to succeed where `fresh` restarts fail.

## 2. Non-Degenerate Wave & Tournament Design

In the published paper, Wave 1 has 16 rollouts and Recursive Tournament Voting selects 4 summaries for Wave 2.
To ensure the tournament is **non-degenerate on the pilot** (selecting 4 winners from 4 summaries would require 0 comparisons and make `selected_peer` identical to `random_peer`!), the pilot uses **all 8 first-wave trajectories (`w0`–`w7` at turn 32)** from Experiment 2's `independent_journal` bank:

| Scale | Wave 1 Trajectories ($N_1$) | Summaries Selected by Tournament ($K$) | Pairwise Votes per Match ($V$) | Wave 2 Workers per Arm ($N_2$) | Turns per Worker | When to Run |
| --- | --- | --- | --- | --- | --- | --- |
| `pilot` (default) | **8** (`w0`–`w7`, reused from Exp 2 at turn 32) | **4** (narrowed $8 \to 4$ in 4 pairwise matches) | **8** votes at $T=0.7$ | **4** (`s0`–`s3`) | `32` (8 epochs × 4) | This prompt: 3 pilot audits, all 3 modes |
| `claim` (paper scale) | **16** (`w0`–`w15`) | **4** (narrowed $16 \to 8 \to 4$ in 12 pairwise matches) | **8** votes at $T=0.7$ | **16** (`s0`–`s15`) | `32` (8 epochs × 4) | Only when explicitly requested after pilot |

- **Zero extra Wave-1 worker cost on the pilot**: When Experiment 2's 8 workers (`w0`–`w7`) exist on disk for `(mode, audit_id)`, freeze their turn-32 (Epoch 8) checkpoints as Wave 1 (`first_wave_source = "experiment_2_turn_32"`). Write `wave1/manifest.json` with each worker's artifact SHA-256, token/USD totals through turn 32, and host-side offline official grade (`wave1_official_scores`).

## 3. Step-by-Step Pipeline (`experience_reuse.py` & `tournament.py`)

### Step 3.1: First-Wave Summarization (`role = "summarizer"`, $T = 0.7$)

For each of the $N_1 = 8$ frozen Wave-1 trajectories (`w0`–`w7`), make one `deepseek/deepseek-v4.1-flash-1017` call at `temperature = 0.7` (role `summarizer`).
- **Input**: The public task instructions, that worker's epoch status records (`epochs 1..8`), and its turn-32 candidate artifact (`candidate_audit.md`, `candidate.diff`, or `candidate_txs.json`). Never pass offline official grades!
- **Output (`wave1/summaries/<worker_id>.summary.json`)**:
  ```json
  {
    "schema_version": 1,
    "source_worker_id": "w0",
    "files_and_functions": ["Curves.sol::buyCurvesToken", "FeeSplitter.sol::onBalanceChange"],
    "tried": "Concrete hypotheses, code paths, and test/exploit commands executed (<= 1200 chars)",
    "observed": "Verified code invariants, state updates, and positive evidence found (<= 1200 chars)",
    "failed": "Dead ends, falsified assumptions, and exact reproduction/compiler blockers (<= 1200 chars)",
    "reusable_next_step": "Specific actionable next step for a fresh auditor starting from base_commit (<= 1200 chars)",
    "candidate_excerpt": "Key finding summary, diff hunk, or transaction sequence produced (<= 1500 chars)"
  }
  ```

### Step 3.2: Recursive Tournament Voting (`tournament.py`, `role = "tournament_vote"`, $T = 0.7$)

Implement `run_recursive_tournament(summaries, instructions, mode, target_k=4, votes_per_pair=8, seed=20261107)`:
1. Start with the list of $N_1$ summaries ordered deterministically by `source_worker_id`, then shuffle with `random.Random(seed)` to form the initial bracket.
2. While `len(active_summaries) > target_k`:
   - Pair adjacent summaries `(left, right)`. (If odd, the last summary gets a bye to the next round.)
   - For each pair, run `votes_per_pair` (`8`) independent calls to `deepseek/deepseek-v4.1-flash-1017` at **`temperature = 0.7`** (`seed = seed + round_idx * 100 + pair_idx * 10 + vote_idx`, role `tournament_vote`).
   - To eliminate position bias, swap display order (`left` vs. `right`) on odd `vote_idx` (`1, 3, 5, 7`) and map the ballot back to the canonical candidate ID!
   - The voter prompt receives the mode instructions and the two JSON summaries, and asks: *"Which attempt provides more accurate root-cause evidence, useful negative evidence, and actionable progress toward solving this EVMBench task? Output JSON `{\"winner\": \"A\" | \"B\", \"rationale\": \"...\"}`."*
   - Majority vote wins the match; a 4–4 tie is broken deterministically by the SHA-256 of the summary JSON.
   - Persist every ballot in `tournament/ballots.jsonl` and the bracket progression in `tournament/bracket.json`.
3. Return the $K = 4$ winning summary IDs (`selected_summary_ids`).

### Step 3.3: The Four Second-Wave Arms (`s0`–`s3`, Clean Sandboxes at `base_commit`)

Every second-wave worker (`s0`–`s3` on the pilot) starts in a **fresh `TaskComputer` sandbox** initialized at `audit.base_commit` (with a fresh Anvil/ploit chain in `EXPLOIT` mode). **Never** copy a first-wave git worktree or chain state into a second-wave sandbox!
Each second-wave worker runs for 32 tool turns (`8` epochs of `4` turns, arm `independent_journal`, `temperature = 0.7`).

| Second-Wave Condition | Prior Summaries Staged at `/home/agent/experiment/prior_summaries.json` for Worker `s{i}` ($i \in \{0,1,2,3\}$) |
| --- | --- |
| `fresh` | None (file not uploaded; worker sees original task instructions only) |
| `self` | `[summary(w{i})]` (1 summary: the corresponding first-wave worker's own summary) |
| `random_peer` | 4 summaries sampled uniformly without replacement from `{w0..w7} \ {w{i}}` using `random.Random(20261107 + i)` |
| `selected_peer` | The 4 tournament-winning summaries (`selected_summary_ids`) from Step 3.2 |

In `prompts.py`, instruct second-wave workers in `self`, `random_peer`, and `selected_peer`:
- Read `/home/agent/experiment/prior_summaries.json` in your first turn.
- Treat prior summaries as partial experience from earlier attempts: verify their positive claims against `/home/agent/audit`, avoid repeating commands listed in `failed` without fixing the blocker, and record in your epoch `status.json` (`adopted_from`) which `source_worker_id` summary items you used.

### Step 3.4: Finalization and Offline Grading

For each of the 4 second-wave conditions:
1. Grade each second-wave worker `s0`–`s3` offline on the host (`workers/s{i}/offline_grade.json`) to compute **`mean_worker_score`** (matching the paper's Table 1 metric: average next-rollout score).
2. Run the **exact same shared temperature-`0.0` finalizer** over `s0`–`s3` and grade the final submission (`grade/grade.json`) to compute **`submitted_score`**.

## 4. Metrics & Analysis (`analyze_experience_reuse.py`)

For each mode and audit, write `runs/test-time/experience-reuse/analysis/summary.json` and `REPORT.md`:

- **Per-Condition Scores**:
  - `wave1_mean_worker_score` and `wave1_max_worker_score`
  - `wave2_mean_worker_score` (mean offline score across `s0`–`s3`)
  - `wave2_max_worker_score` (oracle best of `s0`–`s3`)
  - `submitted_score` (official score after the shared finalizer)
- **Rescue vs. Copy Classification (Crucial for the Paper Claim)**:
  - For each condition and worker `s{i}`, let $S_{\text{supplied}}(s_i)$ be the set of Wave-1 summaries uploaded to `s{i}`.
  - **`supplied_copy_risk(s_i)`**: `1` if any summary in $S_{\text{supplied}}(s_i)$ came from a Wave-1 worker whose offline official score was `> 0` (for `DETECT`, examine both `any_vuln_detected > 0` and per-vulnerability `vuln_copy_risk(v)` where none of the supplied Wave-1 workers detected vulnerability `v`).
  - **`rescue(s_i)`**: `1` when **`supplied_copy_risk(s_i) == 0`** (every Wave-1 attempt supplied to `s_i` scored `0`) AND `s_i` achieved a positive official score (`score > 0`, or in `DETECT` detected a gold vulnerability `v` that none of its supplied Wave-1 workers detected!).
  - **`audit_level_rescue`**: `1` when all 8 Wave-1 workers scored `0` on the audit (or on vulnerability `v`) and at least one Wave-2 worker scored `> 0`.
- **Headline Contrasts** (reported both overall and restricted to `supplied_copy_risk == 0` cases, with bootstrap over `audit_id`):
  - `selected_peer - fresh`
  - `selected_peer - self`
  - `selected_peer - random_peer`
  - `random_peer - fresh`
- **Overhead Accounting**:
  - Report tokens and USD broken down by `wave1` (charged once), `summarizer` (8 calls), `tournament_vote` (32 calls), `wave2` (4 workers × 32 turns), `finalizer`, and `detect_judge`.
- **Complementary Reuse Trace**:
  - Identify any second-wave worker with `rescue == 1` whose status records cite $\ge 2$ failed Wave-1 summaries covering different functions or blockers; quote those summaries in `REPORT.md` (or record `complementary_reuse_unobserved`).

## 5. Unit Tests (`project/evmbench/tests/test_experiment4_experience_reuse.py`)

Write offline unit tests verifying:
1. `run_recursive_tournament` narrows 8 summaries to 4 winners using 4 pairwise matches × 8 votes at `temperature = 0.7`, alternates `left`/`right` prompt order on odd votes to prevent position bias, and breaks 4–4 ties deterministically by SHA-256.
2. Context staging gives `fresh` 0 summaries, `self` (`s2`) only `w2`'s summary, `random_peer` (`s2`) 4 summaries from `{w0..w7} \ {w2}`, and `selected_peer` the exact 4 tournament winners.
3. `analyze_experience_reuse.py` accurately separates `rescue == 1` (all supplied Wave-1 summaries failed, Wave-2 succeeded) from `supplied_copy_risk == 1`, including per-vulnerability rescue in `DETECT` mode.

## 6. Done Criteria

- `tests/test_experiment4_experience_reuse.py` passes.
- Either pilot results and `REPORT.md` are generated, or (if credentials/images are absent) planned manifests are written under `runs/test-time/experience-reuse/planned/`.
- Every LLM call in `ledger.jsonl` uses `deepseek/deepseek-v4.1-flash-1017`.
