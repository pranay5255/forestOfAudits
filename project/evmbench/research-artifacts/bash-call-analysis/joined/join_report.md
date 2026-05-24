# Refreshed Bash Command Joined Analysis

## Primary Dataset

- Stable invocations/tools: **3,840**
- Stable bash invocations: **2,599**
- Stable OpenCode non-bash tool calls: **1,241**
- Stable command segments: **8,233**
- Counted source files: **1,179**
- Distinct runs: **146**

The stable-schema artifact is the primary dataset. The `bash_calls.csv` extractor is retained as reference data only; its 1,244 rows overlap stable command text and must not be added to the stable totals.

## Manifest Counts

- Candidate files inspected: **6,651**
- Unique files after content de-duplication: **3,408**
- Duplicate files skipped: **3,243**
- Extracted stable invocations by source-family manifest: **3,840**
- Source files with kept stable invocations: **1,179**

## Top Stable Categories

- `file_read_navigation`: 1,745
- `onchain_state_query`: 1,032
- `text_search`: 342
- `file_write_edit`: 202
- `exploit_execution`: 144
- `build_test`: 104
- `structured_subagent`: 60
- `shell_control_flow`: 56

## Joined Grains

- `stable_invocation_fact.csv`: one row per stable invocation/tool keyed by `invocation_id`.
- `stable_segment_fact.csv`: one row per stable shell segment or structured tool pseudo-segment keyed by `segment_id`, many-to-one to `invocation_id`.
- `stable_run_category_fact.csv`: per-run primary-category counts keyed by `run_id`, `role`, and `primary_category`.
- `extractor_comparison.csv`: extractor reference mapping, source coverage, and overlap facts with `extractor_version` carried explicitly.

## Validation

- Segment `invocation_id` values all resolve to stable invocation rows.
- Per-run category counts sum to **3,840**.
- Legacy `bash_calls.csv` remains **1,244** rows.
- Legacy unique command texts overlapping stable command text: **704 / 704**.
