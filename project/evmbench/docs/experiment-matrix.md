# Experiment Matrix: Beating the Baseline

This is the protocol for test-time scaling experiments. The goal is to beat the
single-worker baseline at equal cost, not merely to run a bigger pipeline.

## 1. Matched-budget rule

A comparison is valid only when both cells share:

- candidate model (same exact id),
- evaluator model (same exact id),
- audit set and hint level,
- mode and harness,
- **total candidate budget** (cost and wall clock),
- image digests.

Forest spend is `scout + branches × branch + judges × judge + merger`. Set the
baseline `baseline_cost_limit` to that sum and `baseline_step_limit` to the sum
of stage step limits. `plan` prints worker counts; record the budget in
`campaign.json`.

Never compare a forest with budget B against a baseline with budget B/3.

## 2. Metrics

| Metric | Definition |
| --- | --- |
| `score` | Real grader score (`grade/grade.json`). |
| `pass@k` | Fraction of tasks solved by at least one of `k` attempts. |
| `cost_per_solve` | Total candidate + evaluator cost / solved vulnerabilities. |
| `stage_yield` | Branch findings that survive a judge / branch findings. |
| `merger_loss` | Judge-accepted findings missing from the final submission. |
| `failure_modes` | `timeout`, `no_submission`, `contract_violation`, `not_graded`. |

Only `graded` tasks count toward headline scores. A campaign with any ungraded
or degraded task is reported separately, never folded into a clean average.

## 3. Sweep order

Start cheap; scale only what shows signal.

1. **Harness sanity** (1 audit, detect):
   baseline for each harness at the same model and budget.
2. **Strategy gap** (3–5 audits, detect):
   baseline vs forest with `roles=2, branches_per_tree=1`.
3. **Branch scaling** (same audits):
   `branches_per_tree ∈ {1, 2, 4}` at fixed total budget. Identical-budget
   branches means fewer steps per branch as branches grow.
4. **Role scaling**:
   `roles ∈ {2, 4, 6}` at fixed total budget.
5. **Harness comparison**:
   the winning strategy per harness, same model and budget.
6. **Cross-mode**: repeat the winner in patch and exploit.

Do not run the next point until the previous point has grading provenance for
every task.

## 4. Example configs

Baseline, detect, OpenCode:

```json
{
  "backend": "modal",
  "harness": "opencode",
  "strategy": "baseline",
  "mode": "detect",
  "candidate_model": "deepseek/deepseek-v4.1-flash-1017",
  "evaluator_model": "openai/gpt-6-luna",
  "audit_ids": ["2024-01-canto", "2023-10-nextgen", "2024-01-curves"],
  "hint_level": "none",
  "budget": {"baseline_step_limit": 132, "baseline_cost_limit": 24.0}
}
```

Forest, same budget:

```json
{
  "backend": "modal",
  "harness": "opencode",
  "strategy": "forest",
  "mode": "detect",
  "candidate_model": "deepseek/deepseek-v4.1-flash-1017",
  "evaluator_model": "openai/gpt-6-luna",
  "audit_ids": ["2024-01-canto", "2023-10-nextgen", "2024-01-curves"],
  "hint_level": "none",
  "tree_roles": ["accounting", "token-flow"],
  "branches_per_tree": 1,
  "budget": {
    "scout_step_limit": 16, "scout_cost_limit": 2.0,
    "branch_step_limit": 36, "branch_cost_limit": 5.0,
    "judge_step_limit": 24, "judge_cost_limit": 4.0,
    "global_step_limit": 36, "global_cost_limit": 8.0
  }
}
```

Total forest cost limit here is `2 + 2×5 + 2×4 + 8 = 28`; adjust the baseline to
the same number.

```bash
uv run evmbench-campaign --config baseline.json plan
uv run evmbench-campaign --config forest.json plan
# then run each campaign id
```

## 5. What "beating it" requires

A forest cell wins only if all hold:

1. every task has a real grade (`score_is_placeholder: false`),
2. no task is `degraded` in the headline result,
3. the matched-budget baseline was run in the same image digests,
4. the score gain exceeds run-to-run variance on the same task set,
5. cost per solved vulnerability is not worse than the baseline.

If a forest loses, inspect `summarize` `per_stage` and `failure_modes` before
changing prompts. Typical causes:

- judge/merger dropping valid branch findings (`merger_loss`),
- branch timeouts from a step budget that was divided too aggressively,
- scout selecting the wrong roles,
- contract violations from a branch writing the final submission.
