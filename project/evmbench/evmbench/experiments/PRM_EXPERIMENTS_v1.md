# ForestOfAudits — PRM & Test-Time Scaling Experiments

> **Scope:** This document defines concrete, runnable experiments for building a Process Reward Model (PRM) ecosystem on top of the Forest-of-Audits/EVMBench infrastructure. Each experiment is designed to produce a public artifact (dataset, model, or article). Do not change the experiment definition — only add concrete variables, schema details, and dependency declarations required to execute it.

**Context assumptions (binding):**
- The execution engine is `forestOfAudits` running `mini-swe-agent` workers inside Modal Sandboxes against EVMBench tasks in `detect | patch | exploit` modes.
- EVMBench provides ~120 vulnerabilities from 40 audits, with local Anvil grading and programmatic reward signals.
- Compute budget is ~$1500/month across H100/B200 on Modal.
- The goal is an incremental release strategy: dataset first, then PRM, then trained agent.

**Schema contract (binding):**
- `SCHEMA.md` is the concise normative schema spec.
- `DATASET_SCHEMA_GUIDE.md` is the reader-friendly field guide and Phase 6 mapping.
- `trace_schema.py` is the executable validator; every published JSON/JSONL row must pass `validate_artifact()`.
- This document defines experiment plans and artifact names only. Inline examples are field sketches, not alternate schemas; use `extensions` or bump `schema_version` for experiment-specific fields that are not in v1.

---

## Experiment 0 — Infrastructure Baseline

**Purpose:** Verify the execution stack works before running any ML experiment. Produces a smoke-test dataset used for all later experiment debugging.

**What to run:**
```bash
cd project/evmbench
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py plan --scope smoke
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run --scope smoke \
  --runners modal-forest \
  --output-root runs/phase6/smoke-$(date -u +%Y-%m-%dT%H-%M-%SZ)
```

**Concrete variables:**

| Variable | Value |
|---|---|
| `--scope` | `smoke` (5 tasks) |
| `--runners` | `modal-forest` |
| `--workers` | `2` |
| `--max-steps` | `10` |
| `--mode` | `detect` |
| Model | `openai/gpt-4o` (most accessible teacher) |
| Modal GPU | `H100` |

**Validated rows emitted (smoke baseline):**

Emit v1 `decision_point` rows for worker steps and v1 `branch_summary` rows for terminal branch outcomes. Use `experiment: "exp0_baseline"` and deterministic smoke ids such as `smoke.<audit_id>.w00.b00.s000`. See `SCHEMA.md` and `schema_examples/decision_point_detect.json` for the complete required fields, including `schema_version`, `row_type`, `row_id`, `provenance`, complete `cost`, and `test_status.num_errors`.

**Dependencies to verify:**
- `uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py` runs without import errors
- Modal sandbox starts and accepts API keys
- `ghcr.io/pranay5255/evmbench-audit` image is accessible
- `project/evmbench/evmbench/nano/grade/detect.py` grading runs on a known-good audit

**Acceptance gate:** `runs/phase6/smoke-*/phase6-results.json` and `runs/phase6/smoke-*/phase6-summary.md` exist, at least 4 tasks ran, and at least one exported v1 row has `terminal_success` populated.

---

## Experiment 1 — Forest Width/Depth Scaling Law

**Purpose:** Measure how EVMbench success scales with workers (forest width), max steps (forest depth), and aggregation strategy. Produces **ForestTrace-EVM-Scaling-v0** — the core public dataset.

### Concrete run variables

| Variable | Sweep values |
|---|---|
| `--scope` | `first20` |
| `--workers` | `1, 2, 4, 8, 16` |
| `--max-steps` | `10, 20, 40` |
| `--mode` | `detect`, `patch`, `exploit` |
| `--aggregation` | `best-terminal`, `majority-vote`, `verifier-selected` |
| `--seed` | `42, 123` |
| Model | `openai/gpt-4o` |

**Full sweep matrix (all combinations):**
`5 widths × 3 depths × 3 modes × 3 aggregations × 2 seeds = 270 configurations`

For the first release, use a reduced matrix:
`first20 × {1, 4, 16} workers × {10, 40} steps × detect only × best-terminal × seed 42`
= 6 configurations × ~20 tasks = ~120 runs (feasible in one week on $1500 budget)

### Decision rows

Every logged worker step is exported as a v1 `decision_point` row with `experiment: "exp1_forest_scaling"`. The canonical field list is in `SCHEMA.md`; the walkthrough table and Phase 6 mapping are in `DATASET_SCHEMA_GUIDE.md`.

Experiment-specific notes:
- Store audit/repo snapshot metadata in `provenance.evmbench_commit` when it is the EVMBench source commit, or under `extensions.exp1.repo_snapshot` when it describes the task checkout separately.
- `compile_status` must be one of `pass`, `fail`, `not_attempted`, or `unknown`.
- `test_status` is either `null` or an object with `num_passed`, `num_failed`, and `num_errors`.
- `forest_meta` is limited to `num_workers_at_step`, `best_branch_score`, `score_entropy`, and `worker_disagreement` in schema v1.

### How to derive PRM labels offline

```python
# terminal label
R_branch = 1.0 if terminal_success else 0.0

# bootstrap PRM target: prefix value = max terminal reward of all descendants
V(s_t) = max(R_branch for all descendants of s_t)
# step_reward = V(s_t) - V(s_{t-1})  (temporal difference)
# prefix_value = V(s_t)  (value estimate at this state)
```

### Outputs

| Output | Path |
|---|---|
| Extracted decision rows | `runs/phase6/exp1-scaling-<timestamp>/forest_trace_evm_scaling_v0.jsonl` |
| Extracted branch rows | `runs/phase6/exp1-scaling-<timestamp>/forest_branch_summaries_v0.jsonl` |
| Phase 6 results | `runs/phase6/exp1-scaling-<timestamp>/phase6-results.json` |
| Phase 6 summary | `runs/phase6/exp1-scaling-<timestamp>/phase6-summary.md` |
| Width/success curve | `runs/phase6/exp1-scaling-<timestamp>/plots/width_scaling.png` |
| Depth/success curve | `runs/phase6/exp1-scaling-<timestamp>/plots/depth_scaling.png` |

### Dataset artifact

**ForestTrace-EVM-Scaling-v0**
- Host on Hugging Face Datasets
- Dataset card must document: EVMBench version, Modal GPU type used, model used, grading commit
- Each row is one v1 `decision_point` (not one trajectory)
- Canonical split: `train` = first 15 audits, `eval` = last 5 audits

### Dependencies

```bash
# Environment
uv sync
pip install huggingface_hub datasets

# Execution
# Requires: ghcr.io/pranay5255/evmbench-audit:latest
# Requires: Modal API token set as MODAL_TOKEN env var

# Trace extraction
uv run python project/evmbench/evmbench/experiments/extract_forest_traces.py \
  --input-root runs/phase6/exp1-scaling-*/ \
  --output forest_trace_evm_scaling_v0.jsonl
```

### Article

**"Forest-of-Audits: Test-Time Scaling Laws for Smart Contract Security Agents"**

Main plot: success rate (y-axis) vs workers (x-axis) at fixed cost budget, faceted by mode (detect/patch/exploit).

---

## Experiment 2 — Branch Preference Dataset + PRM Reranker

**Purpose:** Train a small PRM that can select the best branch before the forest exhausts its budget. Produces **ForestPref-EVM-v0** and **EVM-PRM-1.5B-v0**.

### Concrete run variables

| Variable | Value |
|---|---|
| Base model | `Qwen/Qwen2.5-Coder-1.5B` |
| PRM head type | `scalar_reward + pairwise_ranking + rationale` |
| Training dataset | ForestTrace-EVM-Scaling-v0 (from Exp 1) |
| Learning rate | `5e-6` |
| Batch size | `4` (due to 32k context) |
| Epochs | `3` |
| Max sequence length | `16384` (truncate from 32k) |
| Loss weights | `rank: 1.0, mse: 0.2, rationale: 0.3` |

### Preference pair construction

From ForestTrace-EVM-Scaling-v0, construct pairs per task:

```python
def construct_preference_pairs(trajectories):
    """
    For each task, for each depth t:
      - Collect all K branches that have reached depth t
      - Sort by terminal_score descending
      - Pair top branch (chosen) with bottom branch (rejected)
        where chosen_terminal_score > rejected_terminal_score
    """
    pairs = []
    for task_id, branches in group_by_task(trajectories):
        for depth in range(max_depth):
            at_depth = [b for b in branches if b.depth == depth]
            if len(at_depth) < 2:
                continue
            at_depth.sort(key=lambda x: x.terminal_score, reverse=True)
            if at_depth[0].terminal_score > at_depth[-1].terminal_score:
                pairs.append({
                    "task_id": task_id,
                    "same_depth": True,
                    "depth": depth,
                    "chosen": {
                        "branch_id": at_depth[0].branch_id,
                        "trace_row_id": at_depth[0].row_id,
                        "history_window": at_depth[0].history_window,
                        "terminal_score": at_depth[0].terminal_score,
                        "step_reward": at_depth[0].step_reward,
                        "prefix_value": at_depth[0].prefix_value,
                    },
                    "rejected": {
                        "branch_id": at_depth[-1].branch_id,
                        "trace_row_id": at_depth[-1].row_id,
                        "history_window": at_depth[-1].history_window,
                        "terminal_score": at_depth[-1].terminal_score,
                        "step_reward": at_depth[-1].step_reward,
                        "prefix_value": at_depth[-1].prefix_value,
                    },
                    "context": {
                        "problem_statement": at_depth[0].problem_statement,
                        "files_touched": sorted({
                            path
                            for branch in at_depth
                            for path in branch.files_touched
                        }),
                        "num_workers_at_depth": len(at_depth),
                        "best_score_at_depth": at_depth[0].terminal_score,
                        "score_entropy_at_depth": score_entropy(at_depth),
                    },
                })
    return pairs
```

### Schema: ForestPref-EVM-v0

Export pairwise examples as v1 `preference_pair` rows with `experiment: "exp2_preference"`. Each `chosen` and `rejected` side must include `branch_id`, `trace_row_id`, `history_window`, `terminal_score`, `step_reward`, and `prefix_value`; `trace_row_id` links back to the source `decision_point.row_id`. The shared `context` object is limited to `problem_statement`, `files_touched`, `num_workers_at_depth`, `best_score_at_depth`, and `score_entropy_at_depth`.

### Model architecture (EVM-PRM-1.5B-v0)

```
Base: Qwen2.5-Coder-1.5B (RoPE, SwiGLU, RMSNorm, 32k ctx)
  ↓ (freeze base weights)
Hidden state: [B, T, 1536]  (Qwen hidden dim)
  ↓ RMSNorm
  ├─→ scalar_reward_head: Linear(1536, 1)         → reward ∈ [0, 1]
  ├─→ pairwise_ranking_head: Linear(1536, 1)       → rank_score ∈ ℝ
  └─→ rationale_head: Linear(1536, vocab_size)      → generate explanation

Loss = -log σ(rank_score_chosen - rank_score_rejected)
     + 0.2 * MSE(scalar_reward, normalized_terminal_score)
     + 0.3 * NLL(rationale_head, teacher_rationale_tokens)
```

### Inference output schema

```json
{
  "branch_id": "task001.w04.b02",
  "reward": 0.73,
  "rank_score": 2.18,
  "rationale": {
    "evidence": [
      "branch inspected vulnerable accounting path",
      "patch touched withdraw logic",
      "tests still compile"
    ],
    "risk": "branch has not checked reentrancy invariant yet",
    "confidence": "medium"
  }
}
```

### Evaluation protocol

At inference time, fix the forest budget and compare selection strategies:

| Method | Protocol |
|---|---|
| **random** | Select next branch uniformly at random |
| **final-only** | Wait for all branches to complete, pick best by terminal score |
| **prm-one-step** | At each step, score all live branches with PRM, continue top-1 |
| **prm-prefix** | At each step, score all prefixes, continue top-K |

Metric: **success rate at fixed cost** (vary cost budget from 1 worker-sec to 100 worker-seconds).

### Dataset artifact

**ForestPref-EVM-v0** — hosted on Hugging Face Datasets alongside ForestTrace-EVM-Scaling-v0.

### Model artifact

**EVM-PRM-1.5B-v0** — HuggingFace model card with:
- Apache-2.0 license
- `evmbench/eval.py` script showing how to load and run the PRM
- Example inference call:
```python
from transformers import AutoModelForCausalLM, AutoTokenizer
model = AutoModelForCausalLM.from_pretrained("pranay5255/EVM-PRM-1.5B-v0")
# Score a branch prefix
outputs = model(**inputs)
reward = scalar_reward_head(outputs.hidden_states[-1]).sigmoid()
```

### Dependencies

```bash
# Training
pip install trl transformers accelerate huggingface_hub
uv run python project/evmbench/evmbench/experiments/train_prm.py \
  --dataset huggingface.co/datasets/pranay5255/ForestPref-EVM-v0 \
  --base-model Qwen/Qwen2.5-Coder-1.5B \
  --output-dir models/evm-prm-1.5b-v0 \
  --epochs 3 \
  --learning_rate 5e-6 \
  --per_device_batch_size 4

# Serving
# vLLM required for generation; custom reward head requires PyTorch endpoint
uv run python project/evmbench/evmbench/experiments/serve_prm.py \
  --model models/evm-prm-1.5b-v0 \
  --port 8080
```

### Article section

**"Can PRMs Reduce Test-Time Compute?"**

Main plot: success/cost curve comparing random vs final-only vs prm-one-step vs prm-prefix. Show that PRM reranking achieves the same success rate with 2x fewer worker-seconds.

---

## Experiment 3 — 3-Action Macro PRM with AST/Diff Reasoning

**Purpose:** Test whether reward models perform better scoring a short action sequence (macro-step) rather than a single action. Produces **EVM-MacroPRM-v0**.

### Concrete run variables

| Variable | Value |
|---|---|
| Window size | `3` (one-step PRM baseline vs 3-step macro PRM) |
| Discount factor γ | `0.9` |
| AST diff extractor | `project/evmbench/evmbench/experiments/solidity_ast_diff.py` |
| Macro target | `R^(3)_t = r_t + γ r_{t+1} + γ² r_{t+2}` |
| Base model | `Qwen/Qwen2.5-Coder-1.5B` |

### Window construction

From ForestTrace-EVM-Scaling-v0, build 3-step windows:

```python
def build_macro_windows(trajectories, window_size=3, gamma=0.9):
    """
    For each trajectory, for each step t:
      Collect (s_t, a_t, o_t, a_{t+1}, o_{t+1}, a_{t+2}, o_{t+2})
      as a single macro input.
    Compute macro reward:
      R_macro = r_t + gamma * r_{t+1} + gamma**2 * r_{t+2}
      where r_i = step_reward at step i (terminal_score delta)
    """
    windows = []
    for traj in trajectories:
        steps = traj.steps  # ordered list of step records
        for t in range(len(steps) - window_size + 1):
            window_steps = steps[t:t+window_size]
            macro_reward = sum(
                gamma**i * window_steps[i].step_reward
                for i in range(window_size)
            )
            windows.append({
                "task_id": traj.task_id,
                "branch_id": traj.branch_id,
                "window_start_idx": t,
                "window_size": window_size,
                "state_sequence": [s.observation for s in window_steps],
                "action_sequence": [s.candidate_action for s in window_steps],
                "observation_sequence": [s.observation for s in window_steps],
                "macro_reward": macro_reward,
                "terminal_branch_reward": traj.terminal_score,
                "discounted_return": macro_reward,
                "solidity_ast_diffs": [s.solidity_ast_diff for s in window_steps],
                "files_touched": sorted({path for s in window_steps for path in s.files_touched}),
                "compile_status_sequence": [s.compile_status for s in window_steps],
                "test_status_sequence": [s.test_status for s in window_steps],
            })
    return windows
```

### Schema: EVM-MacroPRM-v0

Export macro examples as v1 `macro_window` rows with `experiment: "exp3_macro_prm"`. All sequence fields must have exactly `window_size` elements: `state_sequence`, `action_sequence`, `observation_sequence`, `solidity_ast_diffs`, `compile_status_sequence`, and `test_status_sequence`. Each non-null test status object must include `num_passed`, `num_failed`, and `num_errors`.

### Model architecture (EVM-MacroPRM-1.5B-v0)

Same base as EVM-PRM-1.5B-v0, but input is flattened:

```
state_sequence[0] + action_sequence[0] + observation_sequence[0]
+ state_sequence[1] + action_sequence[1] + observation_sequence[1]
+ state_sequence[2] + action_sequence[2] + observation_sequence[2]
+ AST_diff_tokens
```

Output heads:
- `macro_reward_head`: Linear(1536, 1) → macro reward scalar
- `per_step_reward_head`: Linear(1536, 3) → reward per sub-step (auxiliary)

### Evaluation

| Model | Input | Target | Expected |
|---|---|---|---|
| one-step PRM | state + action | immediate step reward | baseline |
| 3-step macro PRM | state + 3-action window | 3-step discounted return | better prefix value estimates |

Metric: **Correlation between predicted reward and actual terminal score** (Spearman ρ), measured on held-out eval set.

### Comparison plot

Train both models on same data, evaluate on held-out tasks. Plot: predicted reward vs actual terminal score scatter, with ρ annotated. Show macro PRM has higher ρ.

### Dependencies

```bash
# AST diff extractor
uv run python project/evmbench/evmbench/experiments/solidity_ast_diff.py \
  --trajectory-jsonl forest_trace_evm_scaling_v0.jsonl \
  --output macro_prm_v0.jsonl

# Training (same as Exp 2 but with macro dataset)
uv run python project/evmbench/evmbench/experiments/train_prm.py \
  --dataset huggingface.co/datasets/pranay5255/EVM-MacroPRM-v0 \
  --base-model Qwen/Qwen2.5-Coder-1.5B \
  --output-dir models/evm-macro-prm-1.5b-v0 \
  --epochs 3 \
  --learning_rate 5e-6 \
  --window_size 3 \
  --gamma 0.9
```

### Article section

**"One-Step Rewards Are Too Myopic for Repo Agents"**

Main plot: Spearman ρ comparison (one-step PRM vs macro PRM) across task complexity bins (easy/medium/hard), showing macro PRM advantage grows with task complexity.

---

## Experiment 4 — Adaptive Forest Controller

**Purpose:** Train a lightweight controller that decides budget allocation across workers, branches, and modes. Produces **ForestController-EVM-v0** and **forest-controller-v0**.

### Concrete run variables

| Variable | Value |
|---|---|
| Controller model | `Qwen/Qwen2.5-Coder-0.5B` (smaller for fast inference) |
| Controller input | Forest-level statistics at decision point |
| Controller actions | `STOP_AND_SUBMIT, SPAWN_MORE_WORKERS, DEEPEN_TOP_BRANCH, DIVERSIFY_PROMPT, RUN_VERIFIER, SWITCH_TO_PATCH_MODE` |
| Training dataset | ForestTrace-EVM-Scaling-v0 augmented with controller decisions |
| Learning rate | `1e-4` |
| Batch size | `16` |

### How to derive controller labels offline

```python
def derive_controller_label(trajectory_branch, all_branches_at_same_task):
    """
    For each branch at each decision point, derive the optimal controller action.
    """
    best_score = max(b.terminal_score for b in all_branches_at_same_task)
    current_score = trajectory_branch.current_prefix_value

    if best_score == 1.0:
        return "STOP_AND_SUBMIT"  # already solved
    if trajectory_branch.duplicate_action_rate > 0.4:
        return "DIVERSIFY_PROMPT"
    if current_score > 0.8 and trajectory_branch.compile_status == "fail":
        return "RUN_VERIFIER"
    if len(all_branches_at_same_task) < 4:
        return "SPAWN_MORE_WORKERS"
    return "DEEPEN_TOP_BRANCH"
```

### Schema: ForestController-EVM-v0

Export controller examples as v1 `controller_state` rows with `experiment: "exp4_controller"`. The allowed `controller_action` values are defined in `SCHEMA.md`; `forest_state` and `outcome` must use only the validator-supported fields. Additional policy diagnostics belong under `extensions.exp4`.

### Controller model architecture

```
Input: forest_state vector (14 dimensions)
  ↓
Controller: 2-layer MLP (128 → 64 → 6)
  ↓ softmax
Output: action probability distribution over 6 controller actions
```

Trained with standard cross-entropy on controller action labels derived above.

### Evaluation

Compare under equal cost budget:

| Strategy | Metric |
|---|---|
| fixed width (8 workers, 40 steps) | success rate |
| fixed depth (1 worker, 80 steps) | success rate |
| PRM reranking | success/cost |
| adaptive controller | success/cost |

### CLI integration

```bash
# Run forest with adaptive controller
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first20 \
  --runners modal-forest \
  --policy adaptive \
  --controller-model pranay5255/forest-controller-v0 \
  --output-root runs/phase6/exp4-adaptive/
```

### Article section

**"Budget Allocation Beats Brute-Force Width"**

Main plot: success/cost curve comparing all 4 strategies. Show adaptive controller matches PRM reranking success rate at lower cost.

---

## Experiment 5 — Offline-to-Online RLVR Pilot

**Purpose:** Test whether offline PRM/preference learning reduces the number of forest samples needed. Produces **EVM-RLVR-Lite-v0** and **EVM-Agent-1.5B-SFT-DPO-v0**.

### Three training stages

#### Stage A — SFT

```python
# Supervised Fine-Tuning on successful traces
# Only branches where terminal_score >= 0.8
L_SFT = -sum(log p_theta(action_t | state_t)) for successful steps
```

| Variable | Value |
|---|---|
| Base model | `Qwen/Qwen2.5-Coder-1.5B` |
| Dataset | ForestTrace-EVM-Scaling-v0 filtered to `terminal_score >= 0.8` |
| Learning rate | `1e-5` |
| Epochs | `2` |
| Max sequence length | `16384` |

#### Stage B — DPO / preference tuning

```python
# Use ForestPref-EVM-v0 pairs
L_DPO = -log sigma(
    beta * (log pi_theta(y_plus|x) - log pi_theta(y_minus|x))
    - log pi_ref(y_plus|x) + log pi_ref(y_minus|x)
)
)
```

| Variable | Value |
|---|---|
| Reference model | SFT model from Stage A |
| Beta (DPO temperature) | `0.5` |
| Epochs | `1` |
| Learning rate | `1e-6` |
| Pairs from | ForestPref-EVM-v0 |

#### Stage C — GRPO-lite (Modal + TRL)

```python
# Group Relative Policy Optimization on EVMbench-lite subset
# Reward = compile_success + test_pass_rate + exploit_detceted
# Use TRL GRPO with vLLM generation + Modal sandbox evaluation
```

| Variable | Value |
|---|---|
| Tasks | `first5` EVMbench tasks only |
| Group size | `8` |
| Generations per group | `4` |
| Reward function | `compile_success * 0.2 + test_pass_rate * 0.3 + terminal_success * 0.5` |
| Epochs | `10` |
| Learning rate | `5e-6` |

**TRL + Modal GRPO setup:**
```python
from trl import GRQOTrainer, GRPOConfig
from modal import Sandbox

config = GRPOConfig(
    output_dir="models/evm-agent-grpo-lite",
    num_epochs=10,
    per_device_batch_size=1,
    learning_rate=5e-6,
    gradient_accumulation_steps=4,
)

def grpo_reward_fn(completions, **kwargs):
    """Run each completion in Modal Anvil sandbox, return reward."""
    rewards = []
    for completion in completions:
        with Sandbox.new() as sandbox:
            result = sandbox.run(f"cd /repo && {completion}")
            reward = parse_anvil_result(result)
            rewards.append(reward)
    return rewards
```

### Schema: EVM-RLVR-Lite-v0

RLVR training metadata is not a separate v1 row type. For the v1 public artifact, encode per-step data as `decision_point` rows and branch outcomes as `branch_summary` rows with `experiment: "exp5_rlvr_lite"`, then place RLVR-only fields such as `stage`, `policy_version`, `reward_breakdown`, and `training_metadata` under namespaced `extensions.exp5`. If RLVR needs a first-class top-level schema later, introduce it with a `schema_version` bump.

### Evaluation under fixed budget

| Policy | Success Rate @ 10 worker-hours | Avg Cost per Task |
|---|---|---|
| base model | baseline | baseline |
| SFT model | measured | measured |
| DPO model | measured | measured |
| GRPO-lite model | measured | measured |
| base + PRM forest | measured | measured |
| tuned + PRM forest | measured | measured |

### Dependencies

```bash
# TRL training
pip install trl[hf] transformers accelerate

# Modal GRPO
pip install modal
uv run python project/evmbench/evmbench/experiments/train_grpo.py \
  --tasks first5 \
  --output-dir models/evm-agent-grpo-lite \
  --grpo_epochs 10

# vLLM for generation in GRPO
uv run python -m vllm.entrypoints.openai.api_server \
  --model pranay5255/EVM-Agent-1.5B-SFT-DPO-v0 \
  --port 8000 \
  --gpu 1
```

### Article section

**"Can Small Models Learn to Route EVMbench Audits?"**

Main plot: Success/cost table (6 rows × 2 columns: success rate, cost per task) comparing all 6 policies.

---

## Compute Budget Allocation

| Month budget | Allocation |
|---|---|
| $1500 | ~$500 on dataset generation (Exp 1) |
| | ~$300 on PRM training (Exp 2) |
| | ~$200 on macro PRM (Exp 3) |
| | ~$200 on controller training (Exp 4) |
| | ~$200 on RLVR-lite (Exp 5) |
| | ~$100 reserve for eval runs |

### H100 vs B200 split

- **Exp 1 (dataset generation):** H100 (cheaper, fine for many parallel I/O-bound sandboxes)
- **Exp 2-5 (training):** B200 (faster for GPU-bound training, amortized over fewer hours)

---

## Run Order

```
Week 1:  Exp 0 (smoke test) → Exp 1 (reduced matrix, first20 × {1,4,16} × {10,40} × detect)
         Release: ForestTrace-EVM-Scaling-v0 + article 1

Week 2:  Exp 2 (construct preference pairs, train EVM-PRM-1.5B-v0)
         Release: ForestPref-EVM-v0 + EVM-PRM-1.5B-v0 + article 2

Week 3:  Exp 3 (macro PRM experiment)
         Release: EVM-MacroPRM-v0 + article 3

Week 4:  Exp 4 + Exp 5 (controller + RLVR-lite)
         Release: ForestController-v0 + EVM-Agent-1.5B-SFT-DPO-v0 + article 4/5
```

---

## Reproducibility Checklist

For each experiment, the following must be pinned and documented:

- [ ] EVMBench commit hash (`git -C project/evmbench rev-parse HEAD`)
- [ ] Foundry version (e.g., `nightly-a2c7d4b`)
- [ ] Anvil version
- [ ] Modal GPU image tag (`ghcr.io/pranay5255/evmbench-audit:<tag>`)
- [ ] Model checkpoint (HuggingFace model card URL)
- [ ] Python dependencies (pip freeze or `uv pip compile pyproject.toml`)
- [ ] Seed values used
- [ ] Exact CLI flags for the run

Without this information, the dataset loses research value and benchmarks become incomparable.
