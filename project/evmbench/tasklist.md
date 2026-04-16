# TTS Hackathon Task List

## Goal

Implement a benchmark-owned `mini-swe-agent` path for EVMBench.

The local EVMBench agent should use the `mini` CLI in YOLO mode, and the Modal/TTS path should build on `mini-swe-agent`'s documented `DefaultAgent` and `SwerexModalEnvironment` primitives.

## Current Status

- Phase 0 is complete.
- Phase 1 is implemented for the local EVMBench agent scaffold.
- Phase 2 is intentionally skipped for this hackathon path.
- Phase 3 is implemented for the Modal single-agent baseline.
- Phase 4 is implemented for the forest-of-auditors Modal/TTS path.
- Phase 5 is implemented for solver-native Modal/TTS `agent_id` variants; real capped smoke validation remains pending.
- Phase 6 remains pending follow-up evaluation work.

## Core Decisions

- EVMBench stays the outer system and continues to own agent discovery, task setup, RPC wiring, and grading.
- The new benchmark-facing agent lives under `evmbench/agents/mini-swe-agent/`.
- Use OpenAI-compatible models via `OPENAI_API_KEY`.
- Local single-agent execution uses `mini -y --exit-immediately`.
- Modal-backed TTS uses `DefaultAgent` plus `SwerexModalEnvironment`, not the old custom Yudai loop.
- Keep `detect` mode as the first integrated path.
- Do not change EVMBench grading semantics unless absolutely necessary.
- Do not depend on `mini-extra` global config for benchmark runs; keep runtime config repo-owned and env-driven.

## Repo Reality

- EVMBench currently uploads `start.sh` plus one rendered instruction file into the agent container.
- That means a checked-in `mini.yaml` in the repo is not automatically available inside the runtime container.
- For the first integration pass, `evmbench/agents/mini-swe-agent/start.sh` should write a small override config into `$AGENT_DIR` and invoke:
  - `mini -y --exit-immediately -c mini.yaml -c "$AGENT_DIR/mini-override.yaml" ...`
- The new benchmark-facing files are:
  - `evmbench/agents/mini-swe-agent/config.yaml`
  - `evmbench/agents/mini-swe-agent/start.sh`
- Planned Modal/TTS files should also move under the same folder:
  - `modal_baseline.py`
  - `modal_forest.py`
  - `scout.py`
  - `judge.py`
  - `entrypoint.py`
  - `analyze_results.py`

## Reading Order

Read these in implementation order:

1. `https://mini-swe-agent.com/latest/usage/mini/`
   Goal: confirm the CLI contract for `-y`, `-c`, `-t`, `-o`, and `--exit-immediately`.
2. `https://mini-swe-agent.com/latest/usage/output_files/`
   Goal: understand trajectory artifacts and keep them separate from EVMBench submission files.
3. `https://mini-swe-agent.com/latest/reference/agents/default/`
   Goal: understand the agent object to reuse for Modal orchestration.
4. `https://mini-swe-agent.com/latest/reference/environments/swerex_modal/`
   Goal: align the Modal environment with EVMBench's per-audit container image model.
5. `https://mini-swe-agent.com/latest/advanced/cookbook/`
   Goal: use cookbook patterns for agent composition instead of inventing a parallel runtime from scratch.
6. `IMPLEMENTATION_ROADMAP.md`
7. `TTS_MODAL_STRATEGY.md`

## Phase 0: Tooling and Smoke Tests

- [x] Verify the repo environment can run `mini` through `uv`.
- [x] Verify `uv run mini --help` succeeds.
- [x] Verify `uv run python -c "import minisweagent"` succeeds.
- [x] Run `modal setup`.
- [x] Create the OpenAI Modal secret.
- [x] Decide whether benchmark images will already contain `mini`, or whether installation is handled during image build.

Exit criteria:

- `mini` works from the repo environment.
- Modal auth is configured.
- We have one agreed path for getting `mini` into benchmark and Modal images.

## Phase 1: Local EVMBench Agent Scaffold

- [x] Create `evmbench/agents/mini-swe-agent/config.yaml`.
- [x] Add at least one EVMBench-discoverable variant:
  - `mini-swe-agent-default`
- [x] Use `OPENAI_API_KEY` and an OpenAI model name in the agent config.
- [x] Set `instruction_file_name: AGENTS.md`.
- [x] Create `evmbench/agents/mini-swe-agent/start.sh`.
- [x] In `start.sh`, generate `$AGENT_DIR/mini-override.yaml`.
- [x] Invoke `mini -y --exit-immediately -c mini.yaml -c "$AGENT_DIR/mini-override.yaml"`.
- [x] Write trajectory output to `/home/logs/mini-swe-agent.traj.json`.
- [x] Keep submission artifacts under `/home/agent/submission`.

Exit criteria:

- EVMBench can discover the new agent by `agent_id`.
- The startup script is thin and benchmark-owned.
- The runtime config is generated in-container without solver changes.

## Phase 2: Single-Agent Detect Baseline

- [x] Skipped for this hackathon path; Modal baseline/forest work proceeds without the local single-agent detect baseline.
- [ ] Run one `detect` audit end-to-end with `mini-swe-agent-default`.
- [ ] Confirm the agent reads `AGENTS.md` and writes `submission/audit.md`.
- [ ] Confirm the trajectory file lands in `/home/logs`.
- [ ] Confirm EVMBench grading still reads only the submission artifact.
- [ ] Record wall-clock time, approximate spend, and basic logs.

Exit criteria:

- One detect audit completes end-to-end.
- EVMBench grading works unchanged.
- The trajectory file is preserved as auxiliary debugging output.

## Phase 3: Modal Baseline

- [x] Create `evmbench/agents/mini-swe-agent/modal_baseline.py`.
- [x] Use `DefaultAgent` programmatically for Modal runs.
- [x] Use `SwerexModalEnvironment` as the execution environment.
- [x] Map each EVMBench audit image to the Modal environment `image` field.
- [x] Keep working paths consistent with EVMBench:
  - `/home/agent/audit`
  - `/home/agent/submission`
  - `/home/logs`
- [x] Persist results and trajectories outside the transient sandbox.
- [x] Use OpenAI credentials through Modal secrets, not global config editing.
- [x] Compile/import check passes for `entrypoint.py baseline --help`.
- [x] Python bytecode compilation passes for Modal baseline code.

Exit criteria:

- One baseline Modal audit completes end-to-end.
- The Modal run uses the same task contract as the local EVMBench path.
- Results are retrievable without changing EVMBench grading.

## Phase 4: Forest-of-Auditors TTS

- [x] Create `modal_forest.py`.
- [x] Keep scout, tree workers, and judge workers inside the `mini-swe-agent` folder.
- [x] Use `DefaultAgent` instances for each worker instead of maintaining a bespoke agent loop.
- [x] Start with these tree roles:
  - `token-flow`
  - `accounting`
  - `access-control`
  - `cross-contract`
  - `exploitability`
- [x] Keep branch writes isolated.
- [x] Let only the final merge stage write `submission/audit.md`.
- [x] Compile/import check passes for `entrypoint.py forest --help`.
- [x] Python bytecode compilation passes for Modal forest code.
- [x] Focused forest unit tests pass.

Exit criteria:

- One audit completes end-to-end in forest mode.
- Branch outputs are isolated and merge cleanly.
- The final submission format stays EVMBench-compatible.

## Phase 5: EVMBench Integration

- [x] Keep the new agent family benchmark-owned under `evmbench/agents/mini-swe-agent/`.
- [x] Add Modal/TTS-enabled variants instead of replacing the baseline variant immediately.
- [x] Keep `start.sh` thin for the local benchmark path.
- [x] Move orchestration decisions into `entrypoint.py` for the Modal path.
- [x] Keep detect mode as the first integrated and measured path.
- [x] Only broaden to patch and exploit after detect is stable.
- [x] Add solver-native dispatch so Modal baseline/forest variants run by `agent_id`.
- [x] Preserve normal EVMBench grading by copying Modal `submission/audit.md` back into the task computer.
- [x] Run capped detect smoke through `mini-swe-agent-modal-baseline-smoke-10`.
- [ ] Run capped detect smoke through `mini-swe-agent-modal-forest-smoke`.

Exit criteria:

- EVMBench can switch between the baseline and Modal/TTS variants by `agent_id`.
- No grading changes are required.
- There is a clean separation between local `mini` CLI execution and Modal orchestration.

## Phase 6: Evaluation and Demo Readiness

- [ ] Compare:
  - local single-agent `mini-swe-agent`
  - Modal single-agent baseline
  - Modal forest/TTS
- [ ] Match total budget before claiming improvements.
- [ ] Start with 5 audits, then 10-20, then scale further if results are stable.
- [ ] Track:
  - runtime
  - spend
  - number of accepted findings
  - severity mix
  - tree effectiveness
  - failure modes

Exit criteria:

- We can explain why the new path is better than the old custom loop.
- We have a matched-budget comparison.
- We have enough logs and trajectories to debug regressions quickly.
