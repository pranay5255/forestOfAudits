# EVMBench mini-swe-agent Implementation Roadmap

## Overview

This roadmap replaces the old Yudai-specific implementation plan.

The new direction is:

- add a benchmark-facing `mini-swe-agent` agent family under `evmbench/agents/mini-swe-agent/`
- use the `mini` CLI for the local EVMBench path
- use `DefaultAgent` plus `SwerexModalEnvironment` for Modal-backed TTS
- use `OPENAI_API_KEY` rather than the Anthropic-specific custom loop

## Phase 0: Prerequisites

### Local Tooling

Prefer `uv` for local validation and development:

```bash
uv run mini --help
uv run python -c "import minisweagent; print(minisweagent.__version__)"
```

If the repo environment does not already provide `mini-swe-agent`, choose one of these paths:

```bash
# Repo-managed environment
uv add --dev "mini-swe-agent[modal]" modal

# Or standalone tool install
uv tool install mini-swe-agent
```

### Modal Setup

```bash
modal setup
modal secret create openai-api-key OPENAI_API_KEY=...
```

Exit criteria:

- `mini` works from the repo environment
- Modal authentication works
- the team agrees how `mini` will be made available inside benchmark and Modal images

## Phase 1: Add the Benchmark-Facing Agent Folder

### Deliverables

- `evmbench/agents/mini-swe-agent/config.yaml`
- `evmbench/agents/mini-swe-agent/start.sh`

### Config Design

Create a benchmark-discoverable default variant:

```yaml
env-config-vars: &env-config-vars
  OPENAI_API_KEY: ${{ secrets.OPENAI_API_KEY }}
  MODEL: openai/gpt-5
  STEP_LIMIT: "50"
  COST_LIMIT: "20.0"

common: &common_settings
  start: mini-swe-agent/start.sh
  instruction_file_name: AGENTS.md
  gateway_sni_hosts:
    - api.openai.com

mini-swe-agent-default:
  <<: *common_settings
  env_vars:
    <<: *env-config-vars
```

Optional second variant:

```yaml
mini-swe-agent-gpt-5-mini:
  <<: *common_settings
  env_vars:
    <<: *env-config-vars
    MODEL: openai/gpt-5-mini
    COST_LIMIT: "10.0"
```

### Startup Script Design

Keep `start.sh` thin and benchmark-owned:

1. validate `OPENAI_API_KEY`
2. validate `mini` is available
3. generate `$AGENT_DIR/mini-override.yaml`
4. call `mini -y --exit-immediately -c mini.yaml -c "$AGENT_DIR/mini-override.yaml"`
5. write trajectory output to `/home/logs/mini-swe-agent.traj.json`

Recommended command:

```bash
mini -y --exit-immediately \
  -c mini.yaml \
  -c "$AGENT_DIR/mini-override.yaml" \
  -t "$PROMPT" \
  -o "$LOGS_DIR/mini-swe-agent.traj.json"
```

The override config should set:

- `agent.mode: yolo`
- `agent.confirm_exit: false`
- `agent.step_limit`
- `agent.cost_limit`
- `environment.cwd: /home/agent/audit`
- `environment.timeout: 240`
- `model.model_class: litellm`
- `model.model_name: ${MODEL}`

Exit criteria:

- EVMBench can discover `mini-swe-agent-default`
- `start.sh` is syntactically valid
- the generated config is benchmark-owned and does not depend on global config edits

## Phase 2: Local Detect Baseline

### Goal

Run the new local `mini-swe-agent` path end-to-end in EVMBench detect mode.

### Tasks

1. Run one detect audit with `mini-swe-agent-default`.
2. Confirm the agent reads `/home/agent/AGENTS.md`.
3. Confirm the final report lands at `/home/agent/submission/audit.md`.
4. Confirm the trajectory lands at `/home/logs/mini-swe-agent.traj.json`.
5. Confirm EVMBench grading remains unchanged.

### Validation

Prefer `uv` for lightweight validation:

```bash
uv run mini --help
uv run python -c "import yaml, pathlib; yaml.safe_load(pathlib.Path('evmbench/agents/mini-swe-agent/config.yaml').read_text())"
bash -n evmbench/agents/mini-swe-agent/start.sh
```

Exit criteria:

- one detect audit completes
- the trajectory is preserved
- no solver changes are required for the first integration

## Phase 3: Modal Baseline

### Goal

Run a single-agent Modal baseline using the official `mini-swe-agent` building blocks.

### Deliverable

- `evmbench/agents/mini-swe-agent/modal_baseline.py`

### Design

Use:

- `DefaultAgent`
- `SwerexModalEnvironment`
- an OpenAI-backed LiteLLM model

The Modal worker should:

1. receive the EVMBench audit image
2. stage audit files under `/home/agent/audit`
3. stage rendered instructions under `/home/agent/AGENTS.md`
4. run the agent
5. persist `submission/` and trajectory files outside the sandbox

Recommended config shape:

```yaml
agent:
  agent_class: default
  step_limit: 50
  cost_limit: 20.0
environment:
  environment_class: swerex_modal
  image: "<task.docker_image>"
  cwd: /home/agent/audit
  timeout: 240
  startup_timeout: 600
  runtime_timeout: 3600
  deployment_timeout: 3600
model:
  model_class: litellm
  model_name: openai/gpt-5
```

Exit criteria:

- one Modal baseline audit completes
- outputs are persisted cleanly
- the benchmark submission contract is unchanged

## Phase 4: Forest-of-Auditors TTS

### Goal

Scale the Modal baseline into a structured multi-worker audit strategy.

### Deliverables

- `modal_forest.py`
- `scout.py`
- `judge.py`
- `entrypoint.py`

### Worker Types

- scout
- tree workers
- tree-local judge
- global judge / final merger

### Initial Tree Set

- `token-flow`
- `accounting`
- `access-control`
- `cross-contract`
- `exploitability`

Each worker should be a role-specialized `mini-swe-agent` worker, not a custom shell loop.

Exit criteria:

- one audit completes in forest mode
- branch writes are isolated
- only the final merge writes `submission/audit.md`

## Phase 5: EVMBench Integration Surface

### Goal

Keep EVMBench integration simple and benchmark-owned.

### Rules

- Keep the agent family under `evmbench/agents/mini-swe-agent/`.
- Keep the local CLI path and the Modal path as separate variants.
- Do not replace the baseline variant immediately.
- Add Modal/TTS variants only after the local baseline is stable.
- Keep detect mode first.

Potential future variants:

```yaml
mini-swe-agent-default:
  ...

mini-swe-agent-modal-baseline:
  ...

mini-swe-agent-modal-forest:
  ...
```

Exit criteria:

- switching between variants only changes `agent_id`
- EVMBench grading code remains unchanged

## Phase 6: Evaluation

### Compare

- local single-agent `mini-swe-agent`
- Modal single-agent baseline
- Modal forest/TTS

### Metrics

- wall-clock runtime
- spend
- accepted findings
- severity distribution
- branch effectiveness
- failure modes

### Evaluation Order

1. 5 audits
2. 10-20 audits
3. larger scale only after the reports are stable

Exit criteria:

- matched-budget comparisons are available
- the new path is demonstrably better or easier to operate than the old custom loop

## Non-Goals for the First Pass

- do not redesign EVMBench grading
- do not broaden to patch/exploit before detect is stable
- do not rely on mutable `mini-extra` global config for benchmark runs
