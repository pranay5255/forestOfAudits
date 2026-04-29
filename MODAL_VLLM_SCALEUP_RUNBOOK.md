# Modal vLLM Scale-Up Runbook

Use this runbook when the goal is to run Phase 6 Modal forest experiments
against the self-hosted Qwen vLLM endpoint.

This file lives at the workspace root so it is easy to find on a live server.
Run all commands from `project/evmbench` unless a step says otherwise.

## 0. What This Runbook Assumes

- Modal CLI is installed on the host.
- The vLLM endpoint may already be live through Modal CLI.
- Longer runs are expected to sustain overnight.
- The benchmark code is the local checkout under `project/evmbench`.
- The default serving target is:

```text
vLLM app: evmbench-vllm-qwen
Modal secret: evmbench-vllm-token
GPU profile: H100:1
served model: Qwen/Qwen3.6-35B-A3B-FP8
LiteLLM model: openai/Qwen/Qwen3.6-35B-A3B-FP8
tool-call parser: qwen3_coder
```

The scale-up rule is: do not promote to a larger run unless the previous run
has a non-empty submission, complete trajectory integrity, and no unexplained
failure reason.

## 1. Modal Account And CLI

Confirm the Modal CLI is authenticated:

```bash
modal profile current
```

If this fails, authenticate with Modal:

```bash
modal setup
modal profile current
```

Tail the vLLM app logs in a second terminal when starting or verifying the
endpoint:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

If you need to inspect deployed apps:

```bash
modal app list
```

## 2. Enter The Project

```bash
cd /home/pranay5255/forestOfAudits/project/evmbench
```

Check that the Phase 6 runner can list variants:

```bash
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py variants \
  | rg 'modal-forest-qwen-vllm|modal-vllm'
```

Expected runner slugs include:

```text
modal-baseline-qwen-vllm
modal-forest-qwen-vllm
modal-forest-qwen-vllm-2trees-debug
modal-forest-qwen-vllm-4trees-debug
```

## 3. Configure `.env`

The file that matters is:

```text
project/evmbench/.env
```

Minimum vLLM values:

```bash
VLLM_API_BASE=https://<workspace>--evmbench-vllm-qwen-serve.modal.run/v1
VLLM_API_KEY=<redacted>
VLLM_MODEL=Qwen/Qwen3.6-35B-A3B-FP8
VLLM_SERVED_MODEL_NAME=Qwen/Qwen3.6-35B-A3B-FP8
VLLM_TOOL_CALL_PARSER=qwen3_coder
VLLM_LITELLM_MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
MODEL=openai/Qwen/Qwen3.6-35B-A3B-FP8
MODEL_KWARGS_JSON={"drop_params":true}
MSWEA_COST_TRACKING=ignore_errors
MODAL_AUDIT_IMAGE_REPO=ghcr.io/pranay5255/evmbench-audit
```

Use a long scaledown window for overnight work so the endpoint does not cold
start between audit items:

```bash
VLLM_SCALEDOWN_WINDOW_SECONDS=43200
```

Load the env in the shell:

```bash
set -a
. ./.env
set +a
```

Sanity-check key variables without printing secrets:

```bash
test -n "${VLLM_API_BASE:-}" && echo "VLLM_API_BASE=$VLLM_API_BASE"
test -n "${VLLM_API_KEY:-}" && echo "VLLM_API_KEY length=${#VLLM_API_KEY}"
test -n "${VLLM_SERVED_MODEL_NAME:-}" && echo "served=$VLLM_SERVED_MODEL_NAME"
test -n "${VLLM_LITELLM_MODEL:-}" && echo "litellm=$VLLM_LITELLM_MODEL"
```

The important model-name invariant is:

```text
VLLM_SERVED_MODEL_NAME = exact model ID returned by /v1/models
VLLM_LITELLM_MODEL = openai/$VLLM_SERVED_MODEL_NAME
MODEL = openai/$VLLM_SERVED_MODEL_NAME
```

## 4. If The Endpoint Is Already Live

Do not redeploy first. Verify the current endpoint and local `.env` agree:

```bash
uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --skip-deploy \
  --wait-timeout 1800
```

Check direct `/v1/models`:

```bash
curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 300 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  "${VLLM_API_BASE%/}/models"
```

Check direct chat:

```bash
curl --fail --show-error --silent --location \
  --connect-timeout 30 \
  --max-time 600 \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --header "Content-Type: application/json" \
  --data "{
    \"model\":\"${VLLM_SERVED_MODEL_NAME}\",
    \"messages\":[{\"role\":\"user\",\"content\":\"Reply with exactly: smoke-ok\"}],
    \"max_tokens\":16,
    \"temperature\":0,
    \"chat_template_kwargs\":{\"enable_thinking\":false}
  }" \
  "${VLLM_API_BASE%/}/chat/completions"
```

For an overnight run, also make sure the live app was deployed with a long
scaledown window. If it was deployed with a short debug window, refresh it
without rotating the API key:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 43200 \
  --tool-call-parser qwen3_coder \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

This keeps the same `.env` key and Modal secret unless you pass rotate or sync
flags explicitly.

## 5. If You Need To Create Or Refresh The Endpoint

Create or refresh `.env` and the Modal secret:

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 43200
```

Deploy and verify:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 43200 \
  --tool-call-parser qwen3_coder \
  --wait-timeout 1800 \
  --request-timeout 300 \
  --chat-timeout 600
```

If the local key and Modal secret drift, sync the current `.env` key into the
Modal secret without rotating:

```bash
uv run evmbench/agents/mini-swe-agent/setup_vllm_modal_env.py --no-write-env
```

Only rotate when you intentionally want a new key:

```bash
uv run evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --rotate-api-key \
  --sync-secret \
  --write-env \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 43200 \
  --tool-call-parser qwen3_coder
```

## 6. Required Tool-Call Preflight

Basic chat is not enough for the forest runner. mini-swe-agent needs
OpenAI-compatible tool calls.

```bash
python3 - <<'PY' >/tmp/vllm-tool-test.json
import json, os
model = os.environ.get("VLLM_SERVED_MODEL_NAME") or os.environ["VLLM_MODEL"]
print(json.dumps({
    "model": model,
    "messages": [{"role": "user", "content": "Call bash to echo ok."}],
    "tools": [{
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command",
            "parameters": {
                "type": "object",
                "properties": {"command": {"type": "string"}},
                "required": ["command"],
            },
        },
    }],
    "max_tokens": 64,
    "temperature": 0,
}))
PY

curl --fail --show-error --silent \
  --header "Authorization: Bearer ${VLLM_API_KEY}" \
  --header "Content-Type: application/json" \
  --data @/tmp/vllm-tool-test.json \
  "${VLLM_API_BASE%/}/chat/completions"
```

The response should contain a `tool_calls` entry. If it does not, fix the vLLM
server tool parser before spending time on forest runs.

## 6A. OpenCode vLLM Status From 2026-04-29

OpenCode Modal runs use the same vLLM endpoint, but they are a separate
promotion track from the mini-swe-agent forest runners. Do not promote OpenCode
to longer scale-up runs until an OpenCode tool-use run completes with a real
submission and complete trajectory integrity.

The H100 endpoint was verified live with:

```text
app: evmbench-vllm-qwen
GPU: H100:1
model: Qwen/Qwen3.6-35B-A3B-FP8
served model: Qwen/Qwen3.6-35B-A3B-FP8
max model len: 32768
tool call parser: qwen3_coder
```

Redeploy with the Qwen3.6 tool parser explicitly when refreshing the endpoint:

```bash
uv run python evmbench/agents/mini-swe-agent/deploy_vllm_server.py \
  --gpu H100 \
  --max-model-len 32768 \
  --startup-timeout-seconds 1200 \
  --scaledown-window-seconds 1800 \
  --tool-call-parser qwen3_coder \
  --request-timeout 300 \
  --chat-timeout 600
```

For OpenCode, keep the vLLM output cap below the deployed context window. The
OpenRouter-era cap of `1000000` causes vLLM to reject requests. Current OpenCode
vLLM settings:

```text
OPENCODE_PROVIDER_ID=vllm
OPENCODE_VLLM_OUTPUT_TOKEN_MAX=4096
OPENCODE_AGENT_TIMEOUT_SECONDS=540
```

The small OpenCode run command used for the 2026-04-29 check was:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=900 \
evmbench/agents/mini-swe-agent/run_phase6_variants.sh run \
  --audits 2024-01-canto \
  --runners opencode-modal-qwen-vllm-10min \
  --output-root runs/phase6/opencode-modal-vllm-10min-canto-traces \
  --stop-on-failure
```

Observed result:

```text
Modal sandbox spawned: yes
OpenCode reached vLLM: yes
trajectory manifest: found 1/1, missing 0
latest event count: 9 JSON events
final audit report: no, placeholder only
exit code: 1
blocking error: AI_InvalidResponseDataError: Expected 'function.name' to be a string.
```

Latest artifact root from that check:

```text
runs/phase6/opencode-modal-vllm-10min-canto-traces/opencode-modal-qwen-vllm-10min/2026-04-29T20-57-45-GMT_run-group_opencode-modal-qwen-vllm-10min_detect/2024-01-canto_43a81b02-882b-48b9-988e-20f30ace8fed/modal
```

The important distinction is that basic chat and even some initial OpenCode
tool steps can succeed while the OpenCode AI SDK still rejects a later tool-call
delta. Treat this as a tool-call compatibility failure until OpenCode can run
past repeated tool calls and write a non-placeholder `submission/audit.md`.

When another Phase 6 run is active, do not stop the shared `swe-rex` Modal app
globally. Stop only the local OpenCode process tree or the specific sandbox you
own, otherwise unrelated Modal forest runs can be interrupted.

## 7. Plan The Debug Run

Always preview the exact matrix first:

```bash
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py plan \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto-live
```

Expected shape:

```text
one audit
one runner
runner slug: modal-forest-qwen-vllm-2trees-debug
agent id: mini-swe-agent-modal-forest-qwen-vllm-2trees-debug
```

## 8. Run One-Audit 2-Tree Debug

This is the next promotion gate before any multi-audit run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=7200 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto-live \
  --stop-on-failure
```

Summarize:

```bash
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py summarize \
  --output-root runs/phase6/debug-qwen-vllm-2tree-canto-live
```

Check the result row:

```bash
python3 - <<'PY'
import json
from pathlib import Path

path = Path("runs/phase6/debug-qwen-vllm-2tree-canto-live/phase6-results.json")
data = json.loads(path.read_text())
for row in data["rows"]:
    print("runner:", row["runner"])
    print("audit:", row["audit_id"])
    print("submission_exists:", row["submission_exists"])
    print("failure_reason:", row["failure_reason"])
    print("trajectories:", row["found_trajectory_count"], "/", row["expected_trajectory_count"])
    print("missing:", row["missing_trajectory_count"])
    print("manifest:", row["trajectory_manifest"])
    print("run_dir:", row["run_dir"])
PY
```

Promote only if:

```text
submission_exists is True
failure_reason is None or empty
found_trajectory_count == expected_trajectory_count
missing_trajectory_count == 0
```

## 9. Optional 4-Tree Debug

Run this only after the 2-tree debug passes or fails with a fully understood
reason:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=10800 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --audits 2024-01-canto \
  --runners modal-forest-qwen-vllm-4trees-debug \
  --output-root runs/phase6/debug-qwen-vllm-4tree-canto-live \
  --stop-on-failure
```

The 4-tree runner uses:

```text
roles: token-flow, accounting, access-control, cross-contract
branches per tree: 1
worker concurrency: 2
```

## 10. Overnight Multi-Audit Run

For the live server, use a long per-audit timeout and run inside `tmux` so the
SSH session can disconnect without killing the experiment.

Start a persistent terminal:

```bash
tmux new -s evmbench-phase6
cd /home/pranay5255/forestOfAudits/project/evmbench
set -a
. ./.env
set +a
```

Use `first5` and the 2-tree debug runner for the first overnight run. It keeps
worker concurrency at 1 and is the safest way to test multiple audits against a
single H100 endpoint.

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first5 \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/qwen-vllm-2tree-first5-overnight \
  --stop-on-failure
```

`PHASE6_ITEM_TIMEOUT_SECONDS=14400` is a four-hour cap per audit item. With
`first5`, worst-case wall time is about 20 hours because the Phase 6 launcher
runs the matrix sequentially. If you want a shorter overnight probe, choose two
or three explicit audits:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --audits 2024-01-canto,2023-07-pooltogether,2023-10-nextgen \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/qwen-vllm-2tree-three-audits-overnight \
  --stop-on-failure
```

If the 2-tree multi-audit run passes, promote to 4-tree first5 on a later
overnight run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=18000 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first5 \
  --runners modal-forest-qwen-vllm-4trees-debug \
  --output-root runs/phase6/qwen-vllm-4tree-first5-overnight \
  --stop-on-failure
```

Do not use the full `modal-forest-qwen-vllm` runner until the debug runner has
complete artifacts over multiple audits.

Detach from tmux without stopping the run:

```text
Ctrl-b then d
```

Reattach later:

```bash
tmux attach -t evmbench-phase6
```

## 11. Monitor During The Run

Terminal 1, Phase 6 run:

```bash
PHASE6_ITEM_TIMEOUT_SECONDS=14400 \
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py run \
  --scope first5 \
  --runners modal-forest-qwen-vllm-2trees-debug \
  --output-root runs/phase6/qwen-vllm-2tree-first5-overnight \
  --stop-on-failure
```

Terminal 2, vLLM server logs:

```bash
modal app logs --timestamps evmbench-vllm-qwen
```

Terminal 3, latest command logs:

```bash
tail -f runs/phase6/qwen-vllm-2tree-first5-overnight/_phase6_command_logs/*/*.stdout.log
```

Quick host-side status check:

```bash
ps -ef | rg 'evaluate_phase6.py run|evmbench.nano.entrypoint|entrypoint.py forest'
du -sh runs/phase6/qwen-vllm-2tree-first5-overnight
find runs/phase6/qwen-vllm-2tree-first5-overnight -name trajectory-manifest.json -print
```

## 12. Summarize And Inspect

```bash
uv run python evmbench/agents/mini-swe-agent/evaluate_phase6.py summarize \
  --output-root runs/phase6/qwen-vllm-2tree-first5-overnight
```

Read:

```text
runs/phase6/qwen-vllm-2tree-first5-overnight/phase6-summary.md
runs/phase6/qwen-vllm-2tree-first5-overnight/phase6-results.json
```

Check every row:

```bash
python3 - <<'PY'
import json
from pathlib import Path

root = Path("runs/phase6/qwen-vllm-2tree-first5-overnight")
data = json.loads((root / "phase6-results.json").read_text())
for row in data["rows"]:
    print(
        row["audit_id"],
        "submission=", row["submission_exists"],
        "failure=", row["failure_reason"],
        "traj=", f'{row["found_trajectory_count"]}/{row["expected_trajectory_count"]}',
        "missing=", row["missing_trajectory_count"],
    )
PY
```

Inspect a manifest for any failed row:

```bash
python3 - <<'PY'
import json
from pathlib import Path

run_dir = Path("<paste-run-dir-here>")
manifest = run_dir / "modal" / "logs" / "forest" / "trajectory-manifest.json"
data = json.loads(manifest.read_text())
print(json.dumps({
    "expected": data["expected_trajectory_count"],
    "found": data["found_trajectory_count"],
    "missing": data["missing_trajectory_count"],
    "workers": [
        {
            "name": worker.get("worker_name"),
            "type": worker.get("worker_type"),
            "role": worker.get("role"),
            "branch": worker.get("branch"),
            "trajectory_exists": worker.get("trajectory_exists"),
            "bytes": worker.get("trajectory_bytes"),
            "error": worker.get("worker_error"),
        }
        for worker in data.get("workers", [])
    ],
}, indent=2))
PY
```

## 13. Promotion Rules

Promote to more audits only when all rows satisfy:

```text
submission/<artifact> exists and is non-empty
phase6-results.json has no failure_reason
trajectory manifest exists
expected_trajectory_count == found_trajectory_count
missing_trajectory_count == 0
```

Good promotion path:

```text
2024-01-canto, 2-tree debug
2024-01-canto, 4-tree debug
first5, 2-tree debug
first5, 4-tree debug
first20, locked best debug runner
```

Stop and debug if:

```text
tool-call preflight fails
missing_trajectory_count > 0
submission is missing or empty
failure_reason is silent or vague
Modal logs show repeated server disconnects
vLLM logs show overload, OOM, or repeated cold starts
```

## 14. Preserve Raw Data

Do not edit raw run directories. Keep the whole output root:

```text
phase6-run-matrix.json
phase6-results.json
phase6-summary.md
phase6-slide-data.json
_phase6_command_logs/
<runner>/<run-group>/<audit_run>/run.log
<runner>/<run-group>/<audit_run>/submission/
<runner>/<run-group>/<audit_run>/modal/logs/
<runner>/<run-group>/<audit_run>/modal/logs/forest/trajectory-manifest.json
<runner>/<run-group>/<audit_run>/modal/logs/forest/**/*.traj.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/trajectory-manifest.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/opencode.traj.json
<runner>/<run-group>/<audit_run>/modal/logs/opencode/opencode-run.jsonl
```

Put derived analysis under a sibling `analysis/`, `reports/`, or `dataset/`
directory.
