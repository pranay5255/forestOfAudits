# Refreshed Bash Command Joined Analysis

## Primary Dataset

- Stable invocations/tools: **9,721**
- Stable bash invocations: **4,387**
- Stable OpenCode non-bash tool calls: **5,334**
- Stable command segments: **22,541**
- Counted source files: **4,602**
- Distinct runs: **341**

The stable-schema artifact is the primary dataset. The `bash_calls.csv` extractor is retained as reference data only; its 1,244 rows overlap stable command text and must not be added to the stable totals.

## Manifest Counts

- Candidate files inspected: **14,627**
- Unique files after content de-duplication: **11,203**
- Duplicate files skipped: **3,424**
- Extracted stable invocations by source-family manifest: **9,721**
- Source files with kept stable invocations: **4,602**

## Top Stable Categories

- `file_read_navigation`: 5,322
- `text_search`: 1,601
- `onchain_state_query`: 1,177
- `file_write_edit`: 393
- `build_test`: 290
- `other`: 238
- `structured_subagent`: 185
- `exploit_execution`: 164

## Joined Grains

- `stable_invocation_fact.csv`: one row per stable invocation/tool keyed by `invocation_id`.
- `stable_segment_fact.csv`: one row per stable shell segment or structured tool pseudo-segment keyed by `segment_id`, many-to-one to `invocation_id`.
- `stable_run_category_fact.csv`: per-run primary-category counts keyed by `run_id`, `role`, and `primary_category`.
- `extractor_comparison.csv`: extractor reference mapping, source coverage, and overlap facts with `extractor_version` carried explicitly.

## Validation

- Segment `invocation_id` values all resolve to stable invocation rows.
- Per-run category counts sum to **9,721**.
- Source file manifest is present and contains **4,602** counted source files.
- Legacy `bash_calls.csv` is advisory reference data with **1,244** rows.
- Legacy unique command texts overlapping stable command text: **704 / 704**.
