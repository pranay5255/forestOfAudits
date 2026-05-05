# Nanoeval, EVMBench Nano, Exploit Mode, and Containers

This guide explains the execution system behind EVMBench nano runs: how
`nanoeval` schedules work, how `evmbench.nano` turns audits into tasks, how
local Docker/Alcatraz and Modal runners differ, and how exploit-mode chains,
`ploit`, and `veto` fit into the container topology.

## Mental Model

There are three layers:

1. `nanoeval` is the control plane. It owns task scheduling, concurrency,
   persistence, retries, progress, recorder integration, and summaries.
2. `evmbench.nano` is the benchmark adapter. It creates `EVMTask` objects,
   prepares audit containers, runs the selected agent, extracts outputs, and
   grades detect, patch, or exploit results.
3. Agent/runtime code executes the work. It is either a local container agent
   inside an Alcatraz Docker cluster or a Modal runner that creates remote
   Modal sandboxes.

```mermaid
flowchart TD
  CLI["CLI or Phase 6 wrapper"] --> ENTRY["evmbench.nano.entrypoint"]
  ENTRY --> SPEC["EvalSpec<br/>EVMbench + RunnerArgs"]
  SPEC --> NE["nanoeval.evaluation.run"]
  NE --> DB["Persistent run DB<br/>tasks, retries, results"]
  NE --> WORKERS["executor workers"]
  WORKERS --> EVAL["EVMbench.evaluate(task)"]
  EVAL --> SOLVER["EVMbenchSolver.run(EVMTask)"]

  SOLVER --> AGENT_KIND{"agent.runner"}

  AGENT_KIND -->|"container"| LOCAL["Alcatraz local cluster<br/>Docker containers"]
  AGENT_KIND -->|"modal_baseline<br/>modal_forest<br/>modal_opencode"| MODAL["Modal runner subprocess"]

  LOCAL --> TASK_SETUP["EVMTask.setup"]
  TASK_SETUP --> RUN_AGENT["bash /home/agent/start.sh"]
  RUN_AGENT --> EXTRACT["extract submission/logs"]
  EXTRACT --> GRADER["fresh grading computer"]
  GRADER --> GRADE["EVMbenchGrade"]

  MODAL --> MINI_ENTRY["mini-swe-agent/entrypoint.py"]
  MINI_ENTRY --> MODAL_SANDBOXES["Modal sandboxes"]
  MODAL_SANDBOXES --> MODAL_ARTIFACTS["modal logs, trajectories, submission"]
  MODAL_ARTIFACTS --> MODAL_GRADE["modal runner grade placeholder"]
  MODAL_GRADE --> GRADE

  GRADE --> RECORDER["recorder + summary"]
  RECORDER --> DB
```

## Source Map

| Area | Main files | What they do |
| --- | --- | --- |
| Nano entrypoint | `evmbench/nano/entrypoint.py` | Builds `EvalSpec(eval=EVMbench, runner=RunnerArgs)` and calls `nanoeval.evaluation.run`. |
| EVMBench eval | `evmbench/nano/eval.py` | Builds audit tasks, run dirs, prompts, run group ids, and final summaries. |
| Solver | `evmbench/nano/solver.py` | Chooses local container vs Modal runner, prepares agents, runs agents, wraps system errors. |
| Task setup/grading | `evmbench/nano/task.py` | Sets up audit containers, exploit chain/veto state, extracts outputs, invokes graders. |
| Local runtime config | `evmbench/nano/runtime.py` | Builds Alcatraz `LocalConfig` for Docker-backed runs. |
| Network gateway | `evmbench/nano/gateway.py` | Creates HAProxy SNI allowlist sidecar and rewires local Docker networking. |
| Graders | `evmbench/nano/grade/*.py` | Detect uses an LLM judge, patch uses tests, exploit replays txs with `ploit`. |
| Agent registry | `evmbench/agents/agent.py` | Loads agent configs, runner type, env vars, start script, gateway allowlist. |
| Modal adapter | `evmbench/agents/modal_runner.py` | Converts agent config into a Modal runner subprocess command. |
| Modal forest | `evmbench/agents/mini-swe-agent/modal_forest.py` | Creates scout, branch, tree judge, and global judge Modal workers. |
| Exploit config | `evmbench/ploit/config.py` | Builds `ploit setup`, `ploit txs`, and `ploit exec-txs` commands. |
| Veto | `evmbench/ploit/veto.py` | Starts/stops JSON-RPC filtering proxy inside exploit containers. |

## Nanoeval Control Plane

`nanoeval` persists all work in a run database. Executor workers pull tasks from
that database, run `spec.eval.evaluate(task)`, save results, and let the driver
decide whether to summarize, retry, or finish.

```mermaid
sequenceDiagram
  autonumber
  participant CLI as CLI
  participant Entry as evmbench.nano.entrypoint
  participant Eval as EVMbench
  participant DB as nanoeval DB
  participant Driver as nanoeval driver
  participant Worker as executor worker
  participant Solver as EVMbenchSolver
  participant Recorder as Recorder

  CLI->>Entry: uv run python -m evmbench.nano.entrypoint ...
  Entry->>Eval: construct EVMbench from chz args
  Entry->>Driver: run(EvalSpec(eval, runner))
  Driver->>Eval: get_tasks()
  Eval-->>Driver: list[EVMTask]
  Driver->>DB: insert task rows
  Driver->>Worker: ensure executor workers started
  Worker->>DB: claim runnable task
  Worker->>Solver: eval.evaluate(task)
  Solver-->>Worker: FinalResult or RolloutSystemError
  Worker->>DB: save result or wrapped system error
  Worker->>Recorder: record completion or error
  Driver->>DB: read clean latest retry per task
  alt result is RolloutSystemError and retry_idx < max_retries
    Driver->>DB: insert task with retry_idx + 1
  else all tasks have final clean results
    Driver->>Eval: get_full_summary(clean_results)
    Eval-->>Driver: metrics + run health
    Driver->>Recorder: record final report
  end
```

Important retry behavior:

- `RunnerArgs.max_retries` defaults to `16`.
- Only `RolloutSystemError` is retried.
- Other unhandled exceptions crash the eval.
- Clean results are deduped by `(question_id, attempt_id)` and use the largest
  `retry_idx`.
- `runner.concurrency` limits how many tasks run in parallel, but it does not
  limit retries.

```mermaid
stateDiagram-v2
  [*] --> Queued
  Queued --> Running: worker claims task
  Running --> Completed: FinalResult
  Running --> SystemError: RolloutSystemError
  Running --> Fatal: non-system exception
  SystemError --> Queued: retry_idx < max_retries
  SystemError --> Completed: retry_idx == max_retries
  Fatal --> [*]: abort eval
  Completed --> Summary
  Summary --> [*]
```

## EVMBench Nano Objects

```mermaid
classDiagram
  class Task {
    question_id
    attempt_id
    retry_idx
  }
  class ComputerTask
  class EVMTask {
    audit
    mode
    run_id
    run_group_id
    runs_dir
    run_dir
    setup()
    grade()
  }
  class Eval {
    get_tasks()
    evaluate(task)
    get_full_summary(results)
  }
  class PythonCodingEval
  class EVMbench {
    audit
    audit_split
    mode
    hint_level
    get_instances()
    get_tasks()
  }
  class PythonCodingSolver
  class EVMbenchSolver {
    agent_id
    timeout
    disable_internet
    run(task)
  }
  class EVMbenchGrade {
    score
    grader_log
    evmbench_result
  }

  Task <|-- ComputerTask
  ComputerTask <|-- EVMTask
  Eval <|-- PythonCodingEval
  PythonCodingEval <|-- EVMbench
  PythonCodingSolver <|-- EVMbenchSolver
  EVMbenchSolver --> EVMTask
  EVMTask --> EVMbenchGrade
```

## End-To-End Local Container Run

This is the classic local/Docker path used when `agent.runner == "container"`.

```mermaid
flowchart TD
  TASK["EVMTask"] --> START["EVMbenchSolver._start_computer"]
  START --> CLUSTER["Alcatraz LocalCluster<br/>audit image"]
  CLUSTER --> SETUP["EVMTask.setup"]
  SETUP --> PREP["upload instructions + start.sh"]
  PREP --> NET{"disable_internet?"}
  NET -->|"yes"| GATEWAY["gateway sidecar<br/>HAProxy SNI allowlist"]
  NET -->|"no"| AGENT
  GATEWAY --> HOSTS["rewrite /etc/hosts for allowed model hosts"]
  HOSTS --> AGENT["agent container runs bash /home/agent/start.sh"]
  AGENT --> SUBMISSION["/home/agent/submission"]
  AGENT --> LOGS["/home/logs"]
  SUBMISSION --> EXTRACT["extract submission/logs/sessions to run_dir"]
  LOGS --> EXTRACT
  EXTRACT --> GRADING["start fresh grading computer"]
  GRADING --> MODE{"mode"}
  MODE -->|"detect"| DETECT["LLM judge compares audit.md to gold findings"]
  MODE -->|"patch"| PATCH["apply agent.diff, run invariant tests, run vuln tests"]
  MODE -->|"exploit"| EXPLOIT["replay txs with ploit, run grade script"]
  DETECT --> RESULT["EVMbenchGrade"]
  PATCH --> RESULT
  EXPLOIT --> RESULT
```

## Local Container Topology

The main container is always container id `0`. In exploit mode with veto enabled,
the solver adds the audit image as a sidecar too, giving a second container for
chain setup and RPC filtering.

```mermaid
flowchart LR
  subgraph Host["Host process"]
    NE["nanoeval driver + workers"]
    DOCKER["Docker API via Alcatraz LocalCluster"]
  end

  subgraph Cluster["Alcatraz Docker cluster"]
    MAIN["container 0<br/>agent task container<br/>/home/agent<br/>/home/agent/audit<br/>/home/agent/submission<br/>/home/logs"]
    CHAIN["container 1<br/>optional exploit sidecar<br/>anvil + ploit setup + veto"]
    GW["gateway sidecar<br/>HAProxy allowlist<br/>model API only"]
  end

  subgraph External["External services"]
    OPENAI["OpenAI or vLLM endpoint"]
  end

  NE --> DOCKER
  DOCKER --> MAIN
  DOCKER --> CHAIN
  DOCKER --> GW
  MAIN -->|"model calls when local container agent"| GW
  GW -->|"TLS SNI allowlist"| OPENAI
  MAIN -->|"exploit RPC_URL"| CHAIN
  CHAIN -->|"veto forwards safe RPC"| CHAIN
```

## Agent Runner Branches

The same `EVMbenchSolver.run` method handles local and Modal agents, but the
paths are very different.

```mermaid
flowchart TD
  SOLVER["EVMbenchSolver.run"] --> REG["agent_registry.get_agent(agent_id)"]
  REG --> TYPE{"runner"}

  TYPE -->|"container"| LOCAL_SETUP["start Alcatraz computer"]
  LOCAL_SETUP --> TASK_SETUP["EVMTask.setup"]
  TASK_SETUP --> PREP_AGENT["write instructions + start.sh"]
  PREP_AGENT --> RUN_LOCAL["run_agent_in_computer"]
  RUN_LOCAL --> LOCAL_GRADE["EVMTask.grade"]

  TYPE -->|"modal_baseline"| MODAL_BASE["run_modal_runner -> baseline"]
  TYPE -->|"modal_forest"| MODAL_FOREST["run_modal_runner -> forest"]
  TYPE -->|"modal_opencode"| MODAL_OPENCODE["run_modal_runner -> opencode"]

  MODAL_BASE --> MODAL_SUB["copy Modal submission to run_dir"]
  MODAL_FOREST --> MODAL_SUB
  MODAL_OPENCODE --> MODAL_SUB
  MODAL_SUB --> MODAL_PLACEHOLDER["modal runner grade<br/>local Docker grading skipped"]

  LOCAL_GRADE --> FINAL["FinalResult(EVMbenchGrade)"]
  MODAL_PLACEHOLDER --> FINAL
```

## Detect, Patch, and Exploit Modes

```mermaid
flowchart LR
  MODE{"mode"} --> DETECT["detect"]
  MODE --> PATCH["patch"]
  MODE --> EXPLOIT["exploit"]

  DETECT --> DETECT_INPUT["agent writes<br/>submission/audit.md"]
  DETECT_INPUT --> DETECT_GRADE["DetectGrader<br/>LLM judge per vulnerability"]
  DETECT_GRADE --> DETECT_SCORE["score = detected vulnerabilities<br/>award = detected award sum"]

  PATCH --> PATCH_INPUT["agent changes repo<br/>solver captures submission/agent.diff"]
  PATCH_INPUT --> PATCH_GRADE["PatchGrader"]
  PATCH_GRADE --> INVARIANT["run existing suite"]
  INVARIANT --> VULN_TESTS["upload vulnerability tests<br/>run per-vuln tests"]
  VULN_TESTS --> PATCH_SCORE["score = patched vulnerabilities"]

  EXPLOIT --> EXPLOIT_INPUT["agent interacts with chain"]
  EXPLOIT_INPUT --> TXS["task.grade runs ploit txs<br/>submission/txs.json"]
  TXS --> EXPLOIT_GRADE["ExploitGrader"]
  EXPLOIT_GRADE --> EXEC_TXS["ploit exec-txs"]
  EXEC_TXS --> GRADE_SCRIPT["grade.sh writes grade.json"]
  GRADE_SCRIPT --> EXPLOIT_SCORE["score/max_score from grade.json"]
```

## Exploit Mode Detailed Flow

Exploit mode has two phases: the agent phase and the grading replay phase.

```mermaid
sequenceDiagram
  autonumber
  participant Solver as EVMbenchSolver
  participant Main as container 0 agent
  participant Chain as container 1 chain sidecar
  participant Veto as veto proxy
  participant Agent as agent start.sh
  participant Grade as fresh grading computer

  Solver->>Main: checkout base commit
  Solver->>Chain: checkout base commit if sidecar is active
  Solver->>Chain: upload deploy.sh + utils.sh
  Solver->>Chain: ploit config set ... && ploit setup
  Chain-->>Solver: deploy artifacts + block-number.json
  Solver->>Solver: save .ploit.toml text
  Solver->>Chain: remove deploy/utils/.ploit/.veto artifacts
  Solver->>Veto: start proxy if enabled
  Solver->>Main: write exploit instructions with RPC_URL
  Agent->>Veto: JSON-RPC traffic
  Veto->>Chain: forward allowed RPC methods
  Agent->>Main: writes logs or uses chain normally
  Solver->>Chain: re-upload saved .ploit.toml
  Solver->>Chain: ploit txs --output /home/agent/submission/txs.json
  Solver->>Main: copy txs.json back to container 0
  Solver->>Solver: extract submission/logs into run_dir
  Solver->>Grade: start fresh grading computer
  Solver->>Grade: upload deploy.sh, utils.sh, grade.sh, txs.json
  Grade->>Grade: ploit setup
  Grade->>Grade: ploit exec-txs
  Grade->>Grade: run grade script, read grade.json
  Grade-->>Solver: EVMbenchGrade
```

The point of `veto` is to prevent cheap RPC cheating against the local dev
chain. By default it blocks methods such as `eth_sendTransaction`, account
enumeration/signing methods, and direct state mutation helpers like
`hardhat_setStorageAt` and `evm_setAccountBalance`.

## Modal Forest Topology

Modal forest is not one sandbox. It is a set of independent worker sandboxes.
Each worker gets an audit image, SWE-ReX runtime access, role instructions, and
model credentials. The forest coordinator copies back artifacts and trajectories.

```mermaid
flowchart TD
  PHASE6["Phase 6 or direct forest command"] --> NANOEVAL["nanoeval task"]
  NANOEVAL --> MODAL_RUNNER["evmbench.agents.modal_runner"]
  MODAL_RUNNER --> ENTRY["mini-swe-agent/entrypoint.py forest"]
  ENTRY --> COORD["modal_forest coordinator"]

  COORD --> SCOUT["scout worker<br/>Modal sandbox"]
  SCOUT --> ROLES["selected roles"]
  ROLES --> BRANCH1["branch worker<br/>role A branch 1<br/>Modal sandbox"]
  ROLES --> BRANCH2["branch worker<br/>role B branch 1<br/>Modal sandbox"]
  BRANCH1 --> JUDGE1["tree judge role A<br/>Modal sandbox"]
  BRANCH2 --> JUDGE2["tree judge role B<br/>Modal sandbox"]
  JUDGE1 --> GLOBAL["global judge<br/>Modal sandbox"]
  JUDGE2 --> GLOBAL

  subgraph Model["Model endpoint"]
    VLLM["Modal vLLM or OpenAI API"]
  end

  SCOUT --> VLLM
  BRANCH1 --> VLLM
  BRANCH2 --> VLLM
  JUDGE1 --> VLLM
  JUDGE2 --> VLLM
  GLOBAL --> VLLM

  SCOUT --> ART["logs/forest/*.traj.json<br/>forest artifacts<br/>submission"]
  BRANCH1 --> ART
  BRANCH2 --> ART
  JUDGE1 --> ART
  JUDGE2 --> ART
  GLOBAL --> ART
```

Formula for Modal forest worker count per full attempt:

```text
workers_per_attempt = 1 scout
                    + (roles * branches_per_tree)
                    + roles tree_judges
                    + 1 global_judge
```

For a 2-role, 1-branch run:

```text
1 + (2 * 1) + 2 + 1 = 6 Modal sandboxes per full attempt
```

```mermaid
pie showData
  title One full 2-role, 1-branch Modal forest attempt
  "Scout" : 1
  "Branch workers" : 2
  "Tree judges" : 2
  "Global judge" : 1
```

## Retry And Sandbox Growth

Retries multiply whole attempts. They are not just extra model calls.

```mermaid
xychart-beta
  title "Cumulative full-attempt sandboxes for 2-role, 1-branch forest"
  x-axis "Retries completed" [0, 1, 2, 3, 4, 5, 6]
  y-axis "Sandboxes" 0 --> 45
  bar [6, 12, 18, 24, 30, 36, 42]
```

Interpretation:

- `0` retries completed means the first attempt ran once: 6 sandboxes.
- `5` retries completed means 6 full attempts: 36 sandboxes.
- A partial seventh attempt can add 1 or more extra sandboxes.
- With `runner.max_retries=16`, one failed task can run up to 17 attempts.

Use `runner.max_retries=0` for infrastructure debugging unless you explicitly
want retry data.

## Artifact Layout

```mermaid
flowchart TD
  RUNS["runs_dir"] --> GROUP["run_group_id"]
  GROUP --> RUN["run_id"]
  RUN --> RUNLOG["run.log"]
  RUN --> META["metadata.json"]
  RUN --> SUB["submission/"]
  RUN --> LOGS["logs/"]
  RUN --> GRADER["grader/logs/"]
  RUN --> MODAL["modal/"]

  SUB --> AUDIT["detect: audit.md"]
  SUB --> DIFF["patch: agent.diff"]
  SUB --> TXS["exploit: txs.json"]

  MODAL --> MODAL_LOGS["logs/modal-runner-*.log"]
  MODAL --> MODAL_CMD["logs/modal-runner-command.json"]
  MODAL --> FOREST_RESULT["logs/modal-forest-result.json"]
  MODAL --> TRAJ["logs/forest/*.traj.json"]
  MODAL --> FOREST_ART["forest/role/branch artifacts"]
```

Watch out for Modal retry artifact overwrites. If each retry reuses the same
`run_dir/modal` output path, the latest attempt can overwrite
`modal-forest-result.json` and copied forest artifacts from earlier attempts.
The command stdout/stderr logs and Modal app logs may be the only complete
timeline.

## Practical Debug Checklist

For local container runs:

1. Check `run.log` first.
2. Check `/home/agent/submission` extraction in the run dir.
3. For model/network failures, check whether the gateway sidecar allowlisted the
   model host.
4. For exploit mode, inspect `logs/veto.log`, `logs/txs.log`,
   `logs/exec_txs.log`, and `submission/txs.json`.

For Modal forest runs:

1. Start with direct `entrypoint.py forest`, not Phase 6, when debugging infra.
2. Use one role, one branch, low step limits, and no
   `--continue-on-worker-error`.
3. Set `runner.max_retries=0` when using nanoeval or Phase 6.
4. Count expected sandboxes before running:

```text
1 + roles * branches_per_tree + roles + 1
```

5. Preserve raw logs immediately:

```text
_phase6_command_logs/
modal/logs/modal-runner-*.log
modal/logs/modal-forest-result.json
modal/logs/forest/*.traj.json
Modal app logs by app id
```

## Recommended Commands

Direct single-attempt Modal forest debug:

```bash
set -a
. ./.env
set +a

export UV_CACHE_DIR=/tmp/uv-cache
export MODEL="${VLLM_LITELLM_MODEL:-openai/${VLLM_SERVED_MODEL_NAME}}"
export MODEL_KWARGS_JSON="${MODEL_KWARGS_JSON:-{\"drop_params\":true}}"
export MSWEA_COST_TRACKING="${MSWEA_COST_TRACKING:-ignore_errors}"
export OUTPUT_DIR="runs/modal-forest-debug/qwen-1role-canto-$(date -u +%Y%m%dT%H%M%SZ)"

uv run python evmbench/agents/mini-swe-agent/entrypoint.py forest \
  --audit-id 2024-01-canto \
  --mode detect \
  --hint-level none \
  --image "${MODAL_AUDIT_IMAGE_REPO:-ghcr.io/pranay5255/evmbench-audit}:2024-01-canto" \
  --model "$MODEL" \
  --model-kwargs-json "$MODEL_KWARGS_JSON" \
  --cost-tracking "$MSWEA_COST_TRACKING" \
  --scout-step-limit 4 \
  --branch-step-limit 6 \
  --judge-step-limit 4 \
  --global-step-limit 4 \
  --scout-cost-limit 0.5 \
  --branch-cost-limit 0.5 \
  --judge-cost-limit 0.5 \
  --global-cost-limit 0.5 \
  --branches-per-tree 1 \
  --max-tree-roles 1 \
  --tree-roles token-flow \
  --worker-concurrency 1 \
  --output-dir "$OUTPUT_DIR"
```

Direct nanoeval no-retry command:

```bash
set -a
. ./.env
set +a

export UV_CACHE_DIR=/tmp/uv-cache
export RUNS_DIR="runs/nano/manual-no-retry-$(date -u +%Y%m%dT%H%M%SZ)"

uv run python -m evmbench.nano.entrypoint \
  evmbench.audit=2024-01-canto \
  evmbench.mode=detect \
  evmbench.audit_split=detect-tasks \
  evmbench.hint_level=none \
  evmbench.log_to_run_dir=True \
  evmbench.runs_dir="$RUNS_DIR" \
  evmbench.solver=evmbench.nano.solver.EVMbenchSolver \
  evmbench.solver.agent_id=mini-swe-agent-modal-forest-qwen-vllm-2trees-debug \
  runner.concurrency=1 \
  runner.max_retries=0
```

## Key Takeaways

- `nanoeval` retries whole tasks, not individual failed model calls.
- EVMBench Modal forest tasks can be expensive because one logical attempt
  expands into multiple Modal sandboxes.
- Local container runs have a main agent container, an optional exploit chain
  sidecar, and an optional model gateway sidecar.
- Exploit mode is intentionally two-phase: the agent creates on-chain behavior,
  then the grader replays extracted transactions in a fresh grading computer.
- For dataset generation, raw artifact preservation and retry isolation are as
  important as model quality. Without per-attempt artifacts, failed retries can
  overwrite the evidence needed for RCA and training data extraction.
