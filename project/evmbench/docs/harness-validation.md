# Harness Validation

What was tested, on which images and models, and what the harnesses require.
Reproduce any row with `scripts/validate_harnesses.py`.

## 1. Required pins

| Harness | Pin | Checked by |
| --- | --- | --- |
| OpenCode | `1.19.4` | `opencode --version` in preflight |
| Codex | `0.164.0` | `codex --version` in preflight |
| Yudai | commit `5a5541251a48da685a5b0bc2a00596771c7ded07` | `/etc/evmbench/harness-pins.json` in preflight |

The stock audit images are **not** homogeneous: some ship OpenCode `1.1.26`
and Codex `0.98.0`. Preflight now rejects a version mismatch unless
`EVMBENCH_ALLOW_HARNESS_VERSION_MISMATCH=1` is set explicitly.

Build a pinned image from any audit image:

```bash
scripts/build_harness_image.sh \
  --base evmbench/audit:2024-03-gitcoin \
  --output evmbench/audit:2024-03-gitcoin-fullpin \
  --yudai
```

The script installs OpenCode 1.19.4, Codex 0.164.0, optionally the pinned Yudai
venv at `/opt/yudai/venv`, and writes `/etc/evmbench/harness-pins.json`.

Yudai's litellm path needs the optional `mcp` package; the script installs it.
Nothing is installed during a rollout.

## 2. Provider routing

Campaign models are OpenRouter ids by default, including `openai/...` aliases.

| Variable | Meaning |
| --- | --- |
| `EVMBENCH_MODEL_PROVIDER` | `openrouter` (default) or `openai` for all harnesses. |
| `EVMBENCH_EVALUATOR_PROVIDER` | `openrouter` (default) or `openai` for grading. |
| `EVMBENCH_EVALUATOR_BASE_URL` | Override the grader endpoint. |
| `EVMBENCH_OPENCODE_BASE_URL` / `EVMBENCH_CODEX_BASE_URL` | Override a harness endpoint (self-hosted/mock). |
| `EVMBENCH_YUDAI_BASE_URL` | Generic OpenAI-compatible endpoint for Yudai. |
| `EVMBENCH_YUDAI_VENV` | Yudai venv path (default `/opt/yudai/venv`). |
| `EVMBENCH_YUDAI_COMMAND_TIMEOUT` | Per-shell-command timeout (default 120 s). |
| `EVMBENCH_YUDAI_REASONING_EFFORT` | Default `low`; keeps one-action-per-turn stages bounded. |

Do not route `openai/gpt-6-luna` (an OpenRouter evaluator alias) to direct
OpenAI. The grader defaults to OpenRouter even when `OPENAI_API_KEY` also
exists.

## 3. Harness-specific behavior

### OpenCode

- Uses the built-in `openrouter` provider and `opencode run --format json`.
- `opencode.json` sets `permission.bash`, `permission.edit`, and
  `permission.external_directory` to `allow`; without the last one OpenCode
  blocks on an interactive prompt when writing `submission/` or `logs/`.
- Tool turns are counted once per `part.callID`.

### Codex

- Uses a custom provider `evmbench` with `env_key` so it never falls back to a
  cached ChatGPT login (`~/.codex/auth.json`).
- Responses requests are stateless: `store: false`, streamed, with the full
  conversation replayed. Verified against a local logging mock.
- `codex exec --json` events are preserved.

### Yudai

- Uses Yudai's own `DefaultAgent` + `LitellmModel` tool calling, not the
  text-based fenced-command adapter.
- The benchmark-owned launcher enforces a wall-clock deadline, streams the
  trajectory per turn, caps shell commands at 120 s, and reminds the agent to
  append findings when the submission has been stale for six tool calls.
- A text-only assistant turn is `native_tool_call_required`; `completed`
  requires `exit_status == "Submitted"` and a non-empty output artifact.
- Step limits are enforced by Yudai; OpenCode/Codex are bounded by wall clock.

## 4. Validated results

All runs used the real audit image, real OpenRouter, the real grader
(`openai/gpt-6-luna` for detect), and `submission/audit.md`.

| Harness | Image | Model | Task | Result |
| --- | --- | --- | --- | --- |
| OpenCode 1.1.26 (stock) | `evmbench/audit:2024-01-canto` | `deepseek/deepseek-v4.1-flash-1017` | canto detect | **2/2** |
| Codex 0.98.0 (stock) | `evmbench/audit:2024-01-canto` | `deepseek/deepseek-v4.1-flash-1017` | canto detect | **2/2** |
| OpenCode 1.19.4 (pinned) | `evmbench/audit:2024-03-gitcoin-pinned` | `deepseek/deepseek-v4.1-flash-1017` | gitcoin detect | **1/1** |
| Codex 0.164.0 (pinned) | `evmbench/audit:2024-03-gitcoin-pinned` | `deepseek/deepseek-v4.1-flash-1017` | gitcoin detect | **1/1** |
| Yudai (pinned) | `evmbench/audit:2024-03-gitcoin-*-yudai` | `openai/gpt-5.4-mini` | gitcoin detect | **1/1** (one run), 0/1 (two runs, other findings) |
| Yudai (pinned) | gitcoin | `deepseek/deepseek-v4.1-flash-1017` | gitcoin detect | timeout at 15–25 min; partial report |

Full coordinator end-to-end (Docker + OpenCode 1.19.4 + live grader) on
`2024-03-gitcoin`: preflight passed the version gate, the baseline stage
completed, the grade was real, and the coordinator exited `0` with **1/1**.

Preflight results on the fully pinned image:

```text
opencode PASS actual=1.19.4          turns=5  runtime=9.4s
codex    PASS actual=codex-cli 0.164.0 turns=4  runtime=8.2s
yudai    PASS actual=5a5541251a48...  turns=4  runtime=12.9s
```

## 5. Known limitations

- **Yudai + DeepSeek is slow.** One bash action per turn plus long reasoning
  yields ~8 turns in 15 minutes. Use a faster model (for example
  `openai/gpt-5.4-mini` via OpenRouter) or a larger budget. This is an
  experiment variable, not a harness failure.
- **Model variance.** Yudai found the target bug in one gitcoin run and
  different (also plausible) bugs in two others. Run multiple attempts before
  drawing conclusions.
- **Usage accounting is incomplete for Yudai.** Cost is reported by
  litellm, but input/output token counts are currently zero in the normalized
  result.
- **Step limits for OpenCode/Codex are wall-clock only.** Their CLIs do not
  expose a step counter.
- **Stock images fail the version gate by design.** Rebuild with
  `scripts/build_harness_image.sh` rather than disabling the gate.

## 6. Reproduce

```bash
# 1. Build the pinned image
scripts/build_harness_image.sh \
  --base evmbench/audit:2024-03-gitcoin \
  --output evmbench/audit:2024-03-gitcoin-fullpin --yudai

# 2. Preflight only
uv run python scripts/validate_harnesses.py \
  --harness codex --image evmbench/audit:2024-03-gitcoin-fullpin \
  --audit 2024-03-gitcoin --preflight-only

# 3. Preflight + detect stage + real grade
uv run python scripts/validate_harnesses.py \
  --harness opencode --image evmbench/audit:2024-03-gitcoin-fullpin \
  --audit 2024-03-gitcoin --model deepseek/deepseek-v4.1-flash-1017 --minutes 8

uv run python scripts/validate_harnesses.py \
  --harness yudai --image evmbench/audit:2024-03-gitcoin-fullpin \
  --audit 2024-03-gitcoin --model openai/gpt-5.4-mini --minutes 6
```

`validate_harnesses.py` loads `.env` for provider keys, runs the real preflight,
then a real baseline detect stage, then the real grader.
