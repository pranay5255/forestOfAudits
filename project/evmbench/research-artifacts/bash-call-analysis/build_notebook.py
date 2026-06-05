#!/usr/bin/env python3
"""Build a self-contained chart-first HTML dashboard for bash-call analysis."""

from __future__ import annotations

import argparse
import base64
import csv
import datetime as dt
import html
import json
import re
import struct
from collections import Counter
from dataclasses import dataclass
from pathlib import Path


TITLE = "Bash Command Analysis Dashboard"
EXCLUDED_PLOTS = {"extractor_category_comparison.png"}

APPENDIX_TABLES = [
    "stable_invocation_fact.csv",
    "stable_segment_fact.csv",
    "stable_run_category_fact.csv",
    "extractor_comparison.csv",
    "stable_manifest_fact.csv",
    "nonzero_exit_examples.csv",
]

BASH_CALL_TABLES = [
    "bash_calls.csv",
    "by_intent.csv",
    "run_summary.csv",
    "agent_run_summary.csv",
]

CHART_CAPTIONS = {
    "command_ecology_bubble.png": (
        "Shows split shell segments grouped by first executable token and taxonomy category. "
        "Bubble size represents the number of segments at that token/category intersection, so large marks highlight repeated command habits. "
        "Read across rows for category-specific command vocabulary and down columns for tokens reused across multiple behaviors."
    ),
    "complexity_vs_outcome.png": (
        "Plots each invocation/tool by command length and number of split segments. "
        "Marker color reports exit-code bucket and marker size reflects how many categories the invocation touches. "
        "Long, multi-segment commands with nonzero outcomes are the places to inspect for retries, fragile orchestration, or dense shell logic."
    ),
    "intent_fingerprint_heatmap.png": (
        "Aggregates run-category rows by agent and mode, then normalizes each row to a percentage mix. "
        "Darker cells mean a larger share of that agent/mode activity falls in that category. "
        "Use it to compare behavioral fingerprints without raw run volume dominating the view."
    ),
    "run_behavior_map.png": (
        "Places one point per run using invocation/tool volume on the x-axis and segment density on the y-axis. "
        "Color identifies mode, while marker size grows with nonzero-exit share and tool variety. "
        "Outliers show runs that were unusually broad, compound, or failure-heavy."
    ),
    "run_similarity_map.png": (
        "Projects each run's category-mix vector into two dimensions. "
        "Nearby dots have similar category composition even when they come from different benchmarks or agents. "
        "Clusters indicate common workflow shapes; isolated points are unusual run profiles worth checking."
    ),
    "stable_invocation_category_mix.png": (
        "Shows the primary-category share of invocations/tools for the highest-volume agent and mode combinations. "
        "The grain is one row per retained invocation or structured tool call, so bars reflect user-visible tool activity. "
        "Look for mixes dominated by reading, searching, execution, or editing to understand how each workflow spent its effort."
    ),
    "stable_nonzero_exit_categories.png": (
        "Counts nonzero-exit examples by primary category and tool. "
        "The grain is invocation/tool rows that produced a nonzero exit bucket, grouped to show where failures concentrate. "
        "High bars do not prove a defect by themselves, but they identify categories where shell errors, expected probes, or failed attempts need review."
    ),
    "stable_segment_vs_invocation_mix.png": (
        "Contrasts category share at invocation grain with category share after shell commands are split into segments. "
        "This reveals when a category is hidden inside compound commands rather than appearing as the primary invocation label. "
        "Large gaps point to behaviors that are undercounted if only the outer command is considered."
    ),
    "stable_source_manifest.png": (
        "Summarizes source-family coverage through candidate discovery, hash de-duplication, extraction, and counted source files. "
        "The grain is source family by manifest metric, making collection coverage and duplicate suppression visible together. "
        "Use this chart to separate data availability from downstream command volume."
    ),
    "stable_tool_ecology.png": (
        "Breaks primary categories down by tool family across the retained invocation/tool table. "
        "The grain is one invocation or structured tool call, including bash and OpenCode tools such as read, grep, glob, task, apply_patch, and webfetch. "
        "It shows which behaviors are shell-heavy and which are carried by structured tools."
    ),
    "timeline_intent_stream.png": (
        "Bins invocations/tools by extraction order and stacks category counts within each bin. "
        "The chart shows how activity shifts across the collected corpus rather than within a single run. "
        "Wide bands identify categories that dominate long stretches of the dataset."
    ),
    "bash_calls_timeline.png": (
        "Plots bash-call volume over the captured timeline or source order used by the command extractor. "
        "The grain is call rows grouped into chronological buckets. "
        "Use it to spot bursts, quiet regions, and imbalance in when shell activity appears across the corpus."
    ),
    "command_length_distribution.png": (
        "Shows the distribution of shell command text length. "
        "The grain is individual bash-call rows, grouped into length buckets. "
        "The tail is useful for finding dense compound commands, generated scripts, and places where summarization may hide important behavior."
    ),
    "exit_bucket_by_intent.png": (
        "Buckets command outcomes by assigned command category. "
        "The grain is bash-call rows with zero, nonzero, or unknown exit status. "
        "Look for categories where failures or missing exit status cluster instead of spreading evenly."
    ),
    "harness_counts.png": (
        "Counts extracted shell activity by harness. "
        "The grain is bash-call rows grouped by harness label. "
        "Use it to see which execution environments contribute most of the command volume and where sampling may be skewed."
    ),
    "intent_by_harness_heatmap.png": (
        "Cross-tabulates command category against harness. "
        "Each cell is a count at bash-call grain, so darker cells identify category/harness pairings that drive the corpus. "
        "This helps separate harness-specific behavior from patterns shared across environments."
    ),
    "intent_by_mode_heatmap.png": (
        "Cross-tabulates command category against audit mode. "
        "Each cell is a count at bash-call grain. "
        "The strongest cells show which behaviors distinguish detect, patch, exploit, or unknown mode activity."
    ),
    "intent_category_counts.png": (
        "Ranks command categories by total extracted bash-call volume. "
        "The grain is one bash-call row per command. "
        "Use it as the broadest category mix view before drilling into harness, mode, run, or command-level charts."
    ),
    "mode_counts.png": (
        "Counts bash-call rows by mode. "
        "The grain is extracted shell commands grouped by mode label. "
        "This gives a quick balance check for how much command evidence comes from detect, patch, exploit, or unknown activity."
    ),
    "mutation_share_by_intent.png": (
        "Shows the share of commands in each category that appear to mutate files or repository state. "
        "The grain is bash-call rows with mutation flags derived from command parsing. "
        "Categories with high mutation share are the best candidates for closer review of edits, generated files, or side effects."
    ),
    "segment_count_distribution.png": (
        "Shows how many shell segments appear inside each extracted command. "
        "The grain is bash-call rows, with compound commands contributing larger segment counts. "
        "A heavy right tail means command behavior is often packed into chained shell expressions."
    ),
    "source_type_counts.png": (
        "Counts extracted bash calls by source type. "
        "The grain is source-type label by command row. "
        "Use it to understand whether activity is mostly agent-authored, runner-authored, metadata-derived, or from another source format."
    ),
    "tool_family_by_experiment_heatmap.png": (
        "Cross-tabulates tool family by experiment. "
        "Each cell counts bash-call rows assigned to a tool family within an experiment bucket. "
        "Strong columns or rows indicate experiment-specific command ecosystems."
    ),
    "tool_family_counts.png": (
        "Ranks parsed tool families by bash-call volume. "
        "The grain is individual command rows grouped by the command parser's tool-family assignment. "
        "Use it to distinguish file reading, package management, test execution, version control, and other common command families."
    ),
    "top_exact_commands.png": (
        "Lists the most repeated full command texts. "
        "The grain is exact normalized command string, counted across bash-call rows. "
        "High-frequency entries often reveal boilerplate, runner scaffolding, common inspection commands, or repeated checks."
    ),
    "top_primary_commands.png": (
        "Ranks the leading executable or shell token for extracted commands. "
        "The grain is bash-call rows grouped by primary command. "
        "Use it to see the dominant command vocabulary without the noise of full argument strings."
    ),
    "top_runs_by_bash_calls.png": (
        "Ranks individual runs by extracted bash-call count. "
        "The grain is run-level aggregation over command rows. "
        "The largest bars identify runs that drive totals and deserve extra scrutiny when interpreting corpus-wide patterns."
    ),
}


@dataclass(frozen=True)
class CsvTable:
    path: Path
    headers: list[str]
    rows: list[list[str]]


@dataclass(frozen=True)
class PlotAsset:
    path: Path
    width: int
    height: int
    data_uri: str


def parse_args() -> argparse.Namespace:
    artifact_dir = Path(__file__).resolve().parent
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--input", type=Path, default=artifact_dir, help="bash-call-analysis artifact directory.")
    parser.add_argument("--output", type=Path, default=artifact_dir / "index.html", help="HTML file to write.")
    return parser.parse_args()


def html_escape(value: object) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def format_int(value: int | str | float) -> str:
    try:
        return f"{int(float(value)):,}"
    except (TypeError, ValueError):
        return str(value)


def format_number(value: float) -> str:
    if abs(value - round(value)) < 0.05:
        return format_int(round(value))
    return f"{value:,.1f}"


def format_percent(numerator: int, denominator: int) -> str:
    if denominator <= 0:
        return "0.0%"
    return f"{numerator / denominator * 100:.1f}%"


def slugify(value: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", value.lower()).strip("-") or "section"


def nice_title(name: str) -> str:
    return Path(name).stem.replace("_", " ").replace("-", " ").title()


def read_csv_table(path: Path) -> CsvTable:
    with path.open(newline="", encoding="utf-8") as handle:
        reader = csv.reader(handle)
        try:
            headers = next(reader)
        except StopIteration:
            return CsvTable(path=path, headers=[], rows=[])
        return CsvTable(path=path, headers=headers, rows=[row for row in reader])


def maybe_read_csv(path: Path) -> CsvTable | None:
    if not path.exists():
        return None
    return read_csv_table(path)


def read_png_size(path: Path) -> tuple[int, int]:
    with path.open("rb") as handle:
        header = handle.read(24)
    if len(header) >= 24 and header[:8] == b"\x89PNG\r\n\x1a\n":
        return struct.unpack(">II", header[16:24])
    return (0, 0)


def read_plot(path: Path) -> PlotAsset:
    width, height = read_png_size(path)
    encoded = base64.b64encode(path.read_bytes()).decode("ascii")
    return PlotAsset(path=path, width=width, height=height, data_uri=f"data:image/png;base64,{encoded}")


def col_index(table: CsvTable, column: str) -> int | None:
    try:
        return table.headers.index(column)
    except ValueError:
        return None


def column_values(table: CsvTable, column: str) -> list[str]:
    index = col_index(table, column)
    if index is None:
        return []
    return [row[index] if index < len(row) else "" for row in table.rows]


def unique_count(table: CsvTable, column: str) -> int:
    return len({value for value in column_values(table, column) if value})


def percentile(values: list[int], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return float(ordered[0])
    position = (len(ordered) - 1) * q
    lower = int(position)
    upper = min(lower + 1, len(ordered) - 1)
    fraction = position - lower
    return ordered[lower] + (ordered[upper] - ordered[lower]) * fraction


def top_value(values: list[str]) -> str:
    normalized = [value or "unknown" for value in values]
    if not normalized:
        return "unknown"
    label, count = Counter(normalized).most_common(1)[0]
    return f"{label} ({format_int(count)} / {format_percent(count, len(normalized))})"


def load_source_files(joined_dir: Path) -> list[str]:
    path = joined_dir / "stable_source_files.json"
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def read_tables(input_dir: Path) -> dict[str, CsvTable]:
    joined_dir = input_dir / "joined"
    tables: dict[str, CsvTable] = {}
    for name in APPENDIX_TABLES:
        table = maybe_read_csv(joined_dir / name)
        if table is not None:
            tables[name] = table
    for name in BASH_CALL_TABLES:
        table = maybe_read_csv(input_dir / name)
        if table is not None:
            tables[f"bash/{name}"] = table
    return tables


def discover_plot_paths(input_dir: Path) -> list[Path]:
    plot_dirs = [input_dir / "joined" / "plots", input_dir / "plots"]
    paths: list[Path] = []
    for plot_dir in plot_dirs:
        if not plot_dir.exists():
            continue
        for path in sorted(plot_dir.glob("*.png")):
            if path.name not in EXCLUDED_PLOTS:
                paths.append(path)
    return paths


def metric_cards(tables: dict[str, CsvTable], source_files: list[str]) -> list[tuple[str, str, str]]:
    inv = tables["stable_invocation_fact.csv"]
    seg = tables["stable_segment_fact.csv"]
    run_cat = tables["stable_run_category_fact.csv"]
    is_bash = column_values(inv, "is_bash")
    bash_count = sum(value.lower() == "true" for value in is_bash)
    nonbash_count = len(inv.rows) - bash_count
    run_count = unique_count(inv, "run_id")
    return [
        ("Invocations/tools", format_int(len(inv.rows)), "Primary analysis grain: one retained shell invocation or structured tool call."),
        ("Bash invocations", format_int(bash_count), "Actual shell commands after de-duplication and source normalization."),
        ("Non-bash tools", format_int(nonbash_count), "Structured non-bash tool calls retained beside shell activity."),
        ("Segments", format_int(len(seg.rows)), "Split shell segments plus structured-tool pseudo-segments."),
        ("Source files", format_int(len(source_files)), "Counted source files with retained invocations/tools."),
        ("Runs", format_int(run_count), "Distinct run_id values represented in the analysis."),
        ("Run-category rows", format_int(len(run_cat.rows)), "Per-run category summary rows used for behavior maps."),
    ]


def corpus_counts(tables: dict[str, CsvTable], source_files: list[str]) -> dict[str, int]:
    inv = tables["stable_invocation_fact.csv"]
    seg = tables["stable_segment_fact.csv"]
    bash_count = sum(value.lower() == "true" for value in column_values(inv, "is_bash"))
    return {
        "invocations": len(inv.rows),
        "bash": bash_count,
        "nonbash": len(inv.rows) - bash_count,
        "segments": len(seg.rows),
        "source_files": len(source_files),
        "runs": unique_count(inv, "run_id"),
    }


def overview_text(tables: dict[str, CsvTable], source_files: list[str]) -> str:
    counts = corpus_counts(tables, source_files)
    return (
        "This page summarizes the full retained command corpus: "
        f"{format_int(counts['invocations'])} invocations/tools, "
        f"{format_int(counts['bash'])} bash invocations, "
        f"{format_int(counts['nonbash'])} non-bash tool calls, "
        f"{format_int(counts['segments'])} segments, "
        f"{format_int(counts['source_files'])} source files, and "
        f"{format_int(counts['runs'])} runs. "
        "Charts are the primary reading path; tables remain available for reproducibility and row-level checks."
    )


def descriptive_stats_rows(tables: dict[str, CsvTable], source_files: list[str]) -> list[tuple[str, str]]:
    inv = tables["stable_invocation_fact.csv"]
    counts = corpus_counts(tables, source_files)
    run_counts = Counter(value for value in column_values(inv, "run_id") if value)
    invocations_per_run = list(run_counts.values())

    exit_buckets = column_values(inv, "exit_bucket")
    if exit_buckets:
        nonzero_count = sum(value == "nonzero" for value in exit_buckets)
    else:
        nonzero_count = sum(value not in {"", "0", "0.0"} for value in column_values(inv, "exit_code"))

    return [
        ("Distinct runs", format_int(counts["runs"])),
        ("Source files", format_int(counts["source_files"])),
        ("Invocations/tools", format_int(counts["invocations"])),
        ("Bash invocations", format_int(counts["bash"])),
        ("Non-bash tool calls", format_int(counts["nonbash"])),
        ("Segments", format_int(counts["segments"])),
        ("Median invocations per run", format_number(percentile(invocations_per_run, 0.5))),
        ("P90 invocations per run", format_number(percentile(invocations_per_run, 0.9))),
        ("Max invocations per run", format_int(max(invocations_per_run, default=0))),
        ("Top mode", top_value(column_values(inv, "mode"))),
        ("Top agent", top_value(column_values(inv, "agent"))),
        ("Top category", top_value(column_values(inv, "primary_category"))),
        (
            "Nonzero-exit count/share",
            f"{format_int(nonzero_count)} / {format_percent(nonzero_count, len(inv.rows))}",
        ),
    ]


def manifest_cards(manifest: CsvTable) -> list[tuple[str, str, str]]:
    labels = {
        "candidate_files": "Candidate files inspected before content de-duplication.",
        "unique_files": "Unique candidate files after content-hash de-duplication.",
        "duplicate_files_skipped": "Duplicate source files skipped by hash.",
        "extracted_invocations": "Invocations grouped by the source-family manifest.",
        "counted_source_files": "Source files with retained invocations/tools.",
    }
    idx_metric = col_index(manifest, "metric")
    idx_count = col_index(manifest, "count")
    totals: dict[str, int] = {}
    if idx_metric is not None and idx_count is not None:
        for row in manifest.rows:
            metric = row[idx_metric]
            totals[metric] = totals.get(metric, 0) + int(float(row[idx_count]))
    return [(metric.replace("_", " ").title(), format_int(totals.get(metric, 0)), help_text) for metric, help_text in labels.items()]


def implementation_summary(input_dir: Path) -> tuple[list[str], list[str]]:
    project_root = input_dir.parents[1]
    analyzer_path = project_root / "scripts" / "analyze_run_bash_calls.py"
    tests_path = project_root / "tests" / "test_analyze_run_bash_calls.py"
    analyzer = analyzer_path.read_text(encoding="utf-8", errors="ignore") if analyzer_path.exists() else ""
    tests = tests_path.read_text(encoding="utf-8", errors="ignore") if tests_path.exists() else ""
    test_names = re.findall(r"^def (test_[a-zA-Z0-9_]+)", tests, flags=re.MULTILINE)
    bullets = [
        "The analyzer writes command_invocations.csv, command_segments.csv, per_run_category_summary.csv, category_taxonomy.json, source_files.json, and report.md.",
        "Source discovery spans downloaded run archives, native EVMBench run traces, exploit result folders, and live-result folders, then prioritizes canonical copies during content-hash de-duplication.",
        "OpenCode structured tools are retained beside bash, so read, grep, glob, task, apply_patch, and webfetch activity appears in the same invocation fact table.",
        "Bash commands are split into segment rows and categorized with the shared taxonomy, making compound shell behavior visible at segment grain.",
    ]
    checks = [
        f"Analyzer stable-schema code present: {'yes' if 'COMPAT_INVOCATION_FIELDS' in analyzer and 'compat_write_outputs' in analyzer else 'no'}.",
        f"Regression tests discovered: {len(test_names)} analyzer tests.",
        "Covered cases include fenced mini-SWE bash, timeout observation, action-vs-code-block de-duplication, forest tool calls, and content-hash de-duplication priority.",
    ]
    return bullets, checks


def cell_value(value: str, preview: bool) -> str:
    normalized = value.replace("\r\n", "\n").replace("\r", "\n")
    if preview and len(normalized) > 180:
        normalized = normalized[:177].rstrip() + "..."
    return html_escape(normalized)


def numeric_column(values: list[str]) -> bool:
    seen = False
    for value in values:
        if value == "":
            continue
        seen = True
        if not re.fullmatch(r"-?\d+(\.\d+)?", value):
            return False
    return seen


def render_data_table(table: CsvTable, table_id: str, preview: bool, max_rows: int | None = None) -> str:
    rows = table.rows if max_rows is None else table.rows[:max_rows]
    columns = list(zip(*table.rows)) if table.rows else []
    numeric_cols = {idx for idx, values in enumerate(columns) if numeric_column(list(values))}
    wide_headers = {"inner_command", "raw_command", "segment", "source_path", "source_file", "run_id", "run_key", "note", "command_preview"}
    parts = [f'<div class="table-wrap"><table id="{table_id}" class="data-table">']
    parts.append("<thead><tr>")
    for header in table.headers:
        parts.append(f"<th>{html_escape(header)}</th>")
    parts.append("</tr></thead><tbody>")
    for row in rows:
        parts.append("<tr>")
        for idx, header in enumerate(table.headers):
            value = row[idx] if idx < len(row) else ""
            classes = []
            if idx in numeric_cols:
                classes.append("num")
            if header in wide_headers:
                classes.append("wide")
            class_attr = f' class="{" ".join(classes)}"' if classes else ""
            parts.append(f"<td{class_attr}>{cell_value(value, preview=preview)}</td>")
        parts.append("</tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_metric_cards(cards: list[tuple[str, str, str]], compact: bool = False) -> str:
    class_name = "metric compact" if compact else "metric"
    return "\n".join(
        f"""
        <div class="{class_name}">
          <span>{html_escape(label)}</span>
          <strong>{html_escape(value)}</strong>
          <p>{html_escape(help_text)}</p>
        </div>
        """
        for label, value, help_text in cards
    )


def render_descriptive_stats_table(rows: list[tuple[str, str]]) -> str:
    parts = ['<div class="table-wrap stats-wrap"><table id="all-runs-descriptive-stats" class="data-table stats-table">']
    parts.append("<thead><tr><th>Metric</th><th>Value</th></tr></thead><tbody>")
    for metric, value in rows:
        parts.append(f"<tr><td>{html_escape(metric)}</td><td>{html_escape(value)}</td></tr>")
    parts.append("</tbody></table></div>")
    return "".join(parts)


def render_plot(plot: PlotAsset, input_dir: Path) -> str:
    plot_id = f"plot-{slugify(plot.path.stem)}"
    dimensions = f"{format_int(plot.width)} x {format_int(plot.height)}" if plot.width and plot.height else "PNG"
    caption = CHART_CAPTIONS.get(
        plot.path.name,
        "Displays one generated analysis plot at PNG grain. Review the axes and legend to identify the measured aggregation, then compare it with nearby charts for supporting context.",
    )
    relative = plot.path.relative_to(input_dir)
    return f"""
      <figure class="plot-card" id="{plot_id}">
        <figcaption>
          <div>
            <h3>{html_escape(nice_title(plot.path.name))}</h3>
            <p>{html_escape(caption)}</p>
          </div>
          <span>{html_escape(str(relative))} / {dimensions}</span>
        </figcaption>
        <img src="{plot.data_uri}" width="{plot.width}" height="{plot.height}" loading="lazy" alt="{html_escape(caption)}" />
      </figure>
    """


def render_table_panel(name: str, table: CsvTable) -> str:
    safe_name = slugify(name)
    full_id = f"table-{safe_name}-full"
    preview_rows = min(14, len(table.rows))
    return f"""
      <details class="table-panel">
        <summary>
          <span>{html_escape(name)}</span>
          <em>{format_int(len(table.rows))} rows / {format_int(len(table.headers))} columns</em>
        </summary>
        {render_data_table(table, f"table-{safe_name}-preview", preview=True, max_rows=preview_rows)}
        <div class="table-tools">
          <label>
            <span>Filter rows</span>
            <input type="search" inputmode="search" placeholder="Search {html_escape(name)}" data-table-filter="{full_id}" />
          </label>
        </div>
        {render_data_table(table, full_id, preview=False)}
      </details>
    """


def render_list(items: list[str]) -> str:
    return "<ul>" + "".join(f"<li>{html_escape(item)}</li>" for item in items) + "</ul>"


def render_nav(plots: list[PlotAsset]) -> str:
    plot_links = "\n".join(
        f'<a href="#plot-{slugify(plot.path.stem)}">{html_escape(nice_title(plot.path.name))}</a>' for plot in plots
    )
    return f"""
      <nav class="toc" aria-label="Dashboard sections">
        <a class="toc-title" href="#overview">{TITLE}</a>
        <a href="#implementation">Implementation</a>
        <a href="#manifest">Manifest</a>
        <a href="#charts">Charts</a>
        <div class="toc-subgroup">{plot_links}</div>
        <a href="#appendix">Data Appendix</a>
      </nav>
    """


def css() -> str:
    return """
      :root {
        color-scheme: light;
        --bg: #f5f7fb;
        --surface: #ffffff;
        --surface-alt: #f8fafc;
        --ink: #172033;
        --muted: #536174;
        --subtle: #7a8698;
        --line: #dbe3ef;
        --line-strong: #b8c5d6;
        --primary: #2563eb;
        --primary-dark: #1d4ed8;
        --secondary: #0f766e;
        --shadow: 0 14px 36px rgba(23, 32, 51, 0.08);
        --radius: 8px;
        --mono: ui-monospace, SFMono-Regular, Menlo, Monaco, Consolas, "Liberation Mono", monospace;
        --sans: Inter, ui-sans-serif, system-ui, -apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif;
      }
      * { box-sizing: border-box; }
      html { scroll-behavior: smooth; }
      body {
        margin: 0;
        background: var(--bg);
        color: var(--ink);
        font: 16px/1.58 var(--sans);
      }
      a { color: var(--primary-dark); text-decoration-thickness: 1px; text-underline-offset: 3px; }
      a:focus-visible,
      button:focus-visible,
      summary:focus-visible,
      input:focus-visible {
        outline: 3px solid rgba(15, 118, 110, 0.35);
        outline-offset: 3px;
      }
      .skip-link {
        position: absolute;
        left: 16px;
        top: -48px;
        z-index: 100;
        padding: 10px 12px;
        border-radius: 6px;
        background: var(--ink);
        color: white;
      }
      .skip-link:focus { top: 12px; }
      .topbar {
        position: sticky;
        top: 0;
        z-index: 20;
        border-bottom: 1px solid var(--line);
        background: rgba(245, 247, 251, 0.94);
        backdrop-filter: blur(12px);
      }
      .topbar-inner {
        width: 100%;
        min-height: 62px;
        padding: 10px 28px;
        display: flex;
        justify-content: space-between;
        gap: 16px;
        align-items: center;
      }
      .brand {
        color: var(--ink);
        font-weight: 850;
        text-decoration: none;
      }
      .top-actions { display: flex; flex-wrap: wrap; gap: 8px; justify-content: flex-end; }
      button,
      .pill-link {
        min-height: 40px;
        border: 1px solid var(--line-strong);
        border-radius: 7px;
        background: var(--surface);
        color: var(--ink);
        padding: 8px 12px;
        font: 700 14px/1 var(--sans);
        cursor: pointer;
        text-decoration: none;
      }
      button:hover,
      .pill-link:hover {
        border-color: var(--primary);
        background: #f4f7ff;
        color: var(--primary-dark);
      }
      .layout {
        width: 100%;
        max-width: none;
        display: grid;
        grid-template-columns: 280px minmax(0, 1fr);
        gap: 30px;
        padding: 30px 28px 68px;
      }
      .toc {
        position: sticky;
        top: 90px;
        align-self: start;
        max-height: calc(100dvh - 116px);
        overflow: auto;
        padding: 14px;
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: var(--surface);
        box-shadow: var(--shadow);
      }
      .toc a {
        display: block;
        padding: 8px 10px;
        border-radius: 6px;
        color: var(--muted);
        font-size: 14px;
        font-weight: 650;
        text-decoration: none;
      }
      .toc a:hover { background: var(--surface-alt); color: var(--ink); }
      .toc .toc-title {
        color: var(--ink);
        font-size: 15px;
        font-weight: 850;
        margin-bottom: 8px;
      }
      .toc-subgroup {
        margin: 2px 0 10px 12px;
        padding-left: 8px;
        border-left: 1px solid var(--line);
      }
      .toc-subgroup a { font-size: 12px; padding: 5px 8px; }
      main { min-width: 0; }
      section {
        scroll-margin-top: 84px;
        margin-bottom: 34px;
      }
      .hero,
      .panel,
      .plot-card,
      .table-panel {
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: var(--surface);
        box-shadow: var(--shadow);
      }
      .hero {
        padding: 30px;
        border-top: 4px solid var(--primary);
      }
      .eyebrow {
        margin: 0 0 8px;
        color: var(--secondary);
        font-size: 13px;
        font-weight: 850;
        letter-spacing: 0;
        text-transform: uppercase;
      }
      h1, h2, h3 { margin: 0; line-height: 1.16; letter-spacing: 0; }
      h1 { max-width: 980px; font-size: 48px; }
      h2 { font-size: 30px; }
      h3 { font-size: 21px; }
      p { margin: 0; }
      code {
        border-radius: 5px;
        background: #eef4ff;
        padding: 0.1em 0.32em;
        font-family: var(--mono);
        font-size: 0.92em;
      }
      .lede {
        max-width: 88ch;
        margin-top: 14px;
        color: var(--muted);
        font-size: 18px;
      }
      .meta-line {
        margin-top: 14px;
        color: var(--subtle);
        font-size: 14px;
      }
      .metrics {
        display: grid;
        grid-template-columns: repeat(auto-fit, minmax(178px, 1fr));
        gap: 12px;
        margin-top: 24px;
      }
      .metric {
        min-height: 128px;
        padding: 16px;
        border: 1px solid var(--line);
        border-radius: var(--radius);
        background: var(--surface-alt);
      }
      .metric.compact { min-height: 106px; }
      .metric span {
        display: block;
        color: var(--muted);
        font-size: 12px;
        font-weight: 800;
        text-transform: uppercase;
      }
      .metric strong {
        display: block;
        margin-top: 8px;
        color: var(--primary-dark);
        font-size: 30px;
        line-height: 1;
        font-variant-numeric: tabular-nums;
        overflow-wrap: anywhere;
      }
      .metric p {
        margin-top: 10px;
        color: var(--muted);
        font-size: 13px;
        line-height: 1.4;
      }
      .stats-block {
        margin-top: 24px;
      }
      .stats-block h2 {
        font-size: 22px;
      }
      .stats-wrap {
        width: 100%;
        margin: 12px 0 0;
      }
      .stats-table td:first-child {
        color: var(--ink);
        font-weight: 800;
        white-space: nowrap;
      }
      .stats-table td:last-child {
        font-variant-numeric: tabular-nums;
      }
      .section-heading {
        display: flex;
        align-items: end;
        justify-content: space-between;
        gap: 20px;
        margin: 30px 0 14px;
      }
      .section-heading p {
        max-width: 86ch;
        color: var(--muted);
      }
      .section-heading .count {
        color: var(--subtle);
        font: 800 13px/1.3 var(--mono);
        white-space: nowrap;
      }
      .panel { padding: 20px; }
      .two-col {
        display: grid;
        grid-template-columns: repeat(2, minmax(0, 1fr));
        gap: 16px;
      }
      .panel h3 + ul,
      .panel h3 + p { margin-top: 12px; }
      ul { margin: 0; padding-left: 20px; color: var(--muted); }
      li + li { margin-top: 6px; }
      .plot-flow {
        display: grid;
        grid-template-columns: 1fr;
        gap: 28px;
      }
      .plot-card {
        margin: 0;
        padding: 22px;
      }
      .plot-card figcaption {
        display: grid;
        grid-template-columns: minmax(0, 1fr) auto;
        gap: 8px 18px;
        align-items: start;
      }
      .plot-card figcaption p {
        max-width: 96ch;
        margin-top: 8px;
        color: var(--muted);
        font-size: 15px;
        line-height: 1.55;
      }
      .plot-card figcaption span {
        color: var(--subtle);
        font: 700 12px/1.4 var(--mono);
        text-align: right;
      }
      .plot-card img {
        display: block;
        width: 100%;
        max-width: none;
        height: auto;
        margin-top: 18px;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: white;
      }
      .table-panel {
        margin-bottom: 12px;
        overflow: hidden;
      }
      .table-panel summary {
        min-height: 54px;
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 16px;
        padding: 14px 16px;
        cursor: pointer;
        color: var(--ink);
        font-weight: 850;
      }
      .table-panel summary em {
        color: var(--subtle);
        font: 700 12px/1.4 var(--mono);
        font-style: normal;
      }
      .table-wrap {
        width: calc(100% - 32px);
        margin: 0 16px 16px;
        overflow-x: auto;
        border: 1px solid var(--line);
        border-radius: 6px;
        background: white;
      }
      table {
        width: 100%;
        border-collapse: collapse;
        font-size: 13px;
      }
      th,
      td {
        padding: 8px 10px;
        border-bottom: 1px solid var(--line);
        border-right: 1px solid var(--line);
        text-align: left;
        vertical-align: top;
      }
      th:last-child,
      td:last-child { border-right: 0; }
      tr:last-child td { border-bottom: 0; }
      th {
        position: sticky;
        top: 0;
        z-index: 1;
        background: #f3f6fb;
        color: var(--ink);
        font-weight: 800;
        white-space: nowrap;
      }
      td {
        max-width: 340px;
        color: var(--muted);
        white-space: pre-wrap;
        overflow-wrap: anywhere;
      }
      td.num {
        text-align: right;
        font-variant-numeric: tabular-nums;
        white-space: nowrap;
      }
      td.wide {
        min-width: 280px;
        max-width: 620px;
        font-family: var(--mono);
        font-size: 12px;
      }
      .table-tools {
        margin: 0 16px 14px;
      }
      .table-tools label {
        display: grid;
        gap: 6px;
        max-width: 440px;
        color: var(--muted);
        font-size: 13px;
        font-weight: 750;
      }
      .table-tools input {
        min-height: 44px;
        width: 100%;
        border: 1px solid var(--line-strong);
        border-radius: 7px;
        padding: 9px 11px;
        font: 15px/1.4 var(--sans);
      }
      @media (max-width: 980px) {
        .layout { display: block; padding: 16px 14px 42px; }
        .toc {
          position: static;
          max-height: none;
          margin-bottom: 18px;
          box-shadow: none;
        }
        .toc-subgroup { display: none; }
        .topbar { position: static; }
        .topbar-inner { align-items: start; flex-direction: column; padding: 12px 14px; }
        .hero,
        .panel,
        .plot-card { padding: 16px; }
        h1 { font-size: 34px; }
        .two-col { grid-template-columns: 1fr; }
        .section-heading { display: block; }
        .section-heading p,
        .section-heading .count { display: block; margin-top: 8px; }
        .plot-card figcaption { display: block; }
        .plot-card figcaption span { display: block; margin-top: 8px; text-align: left; }
        .table-panel summary { display: block; }
        .table-panel summary em { display: block; margin-top: 6px; }
        td { max-width: 260px; }
      }
      @media (prefers-reduced-motion: reduce) {
        html { scroll-behavior: auto; }
      }
    """


def javascript() -> str:
    return """
      const expandAll = document.querySelector('[data-expand-all]');
      const collapseAll = document.querySelector('[data-collapse-all]');
      const details = () => Array.from(document.querySelectorAll('details'));

      expandAll?.addEventListener('click', () => {
        details().forEach((item) => { item.open = true; });
      });
      collapseAll?.addEventListener('click', () => {
        details().forEach((item) => { item.open = false; });
      });

      document.querySelectorAll('[data-table-filter]').forEach((input) => {
        const table = document.getElementById(input.dataset.tableFilter);
        if (!table) return;
        const rows = Array.from(table.tBodies[0]?.rows ?? []);
        input.addEventListener('input', () => {
          const query = input.value.trim().toLowerCase();
          rows.forEach((row) => {
            row.hidden = query !== '' && !row.textContent.toLowerCase().includes(query);
          });
        });
      });
    """


def build_html(input_dir: Path, output: Path) -> str:
    joined_dir = input_dir / "joined"
    tables = read_tables(input_dir)
    required = ["stable_invocation_fact.csv", "stable_segment_fact.csv", "stable_run_category_fact.csv", "stable_manifest_fact.csv"]
    missing = [name for name in required if name not in tables]
    if missing:
        raise SystemExit(f"Missing joined artifacts: {', '.join(missing)}")

    source_files = load_source_files(joined_dir)
    plot_paths = discover_plot_paths(input_dir)
    plots = [read_plot(path) for path in plot_paths]
    generated_at = dt.datetime.now(dt.UTC).strftime("%Y-%m-%d %H:%M:%S UTC")
    impl_bullets, impl_checks = implementation_summary(input_dir)

    metrics = metric_cards(tables, source_files)
    stats_rows = descriptive_stats_rows(tables, source_files)
    manifest = tables["stable_manifest_fact.csv"]
    appendix_tables = "\n".join(render_table_panel(name, tables[name]) for name in APPENDIX_TABLES if name in tables)
    bash_tables = "\n".join(
        render_table_panel(name, tables[f"bash/{name}"]) for name in BASH_CALL_TABLES if f"bash/{name}" in tables
    )
    chart_cells = "\n".join(render_plot(plot, input_dir) for plot in plots)

    return f"""<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{TITLE}</title>
    <meta name="description" content="Self-contained chart-first bash command analysis dashboard." />
    <style>{css()}</style>
  </head>
  <body>
    <a class="skip-link" href="#main">Skip to content</a>
    <header class="topbar">
      <div class="topbar-inner">
        <a class="brand" href="#overview">{TITLE}</a>
        <div class="top-actions" aria-label="Dashboard actions">
          <button type="button" data-expand-all>Expand appendices</button>
          <button type="button" data-collapse-all>Collapse appendices</button>
          <a class="pill-link" href="#charts">Charts</a>
          <a class="pill-link" href="#appendix">Data</a>
        </div>
      </div>
    </header>
    <div class="layout">
      {render_nav(plots)}
      <main id="main">
        <section class="hero" id="overview">
          <p class="eyebrow">Total analysis</p>
          <h1>{TITLE}</h1>
          <p class="lede">{html_escape(overview_text(tables, source_files))}</p>
          <p class="meta-line">Generated {generated_at} from <code>{html_escape(str(input_dir))}</code>.</p>
          <div class="metrics">
            {render_metric_cards(metrics)}
          </div>
          <div class="stats-block" id="all-runs-stats">
            <h2>All Runs Descriptive Stats</h2>
            {render_descriptive_stats_table(stats_rows)}
          </div>
        </section>

        <section id="implementation">
          <div class="section-heading">
            <div>
              <h2>Implementation Refresh</h2>
              <p>What the analyzer emits and what the local regression tests exercise.</p>
            </div>
          </div>
          <div class="two-col">
            <article class="panel">
              <h3>Analyzer Summary</h3>
              {render_list(impl_bullets)}
            </article>
            <article class="panel">
              <h3>Verification Surface</h3>
              {render_list(impl_checks)}
            </article>
          </div>
        </section>

        <section id="manifest">
          <div class="section-heading">
            <div>
              <h2>Manifest Counts</h2>
              <p>Source-family coverage and de-duplication counts from the analyzer report.</p>
            </div>
          </div>
          <div class="metrics">
            {render_metric_cards(manifest_cards(manifest), compact=True)}
          </div>
        </section>

        <section id="charts">
          <div class="section-heading">
            <div>
              <h2>Charts</h2>
              <p>Every non-excluded PNG in the analysis plot directories is embedded below as a large, single-column figure with context on grain, measurement, and what to inspect.</p>
            </div>
            <span class="count">{format_int(len(plots))} charts</span>
          </div>
          <div class="plot-flow">
            {chart_cells}
          </div>
        </section>

        <section id="appendix">
          <div class="section-heading">
            <div>
              <h2>Data Appendix</h2>
              <p>Compact previews are shown first; full tables can be expanded and filtered in-place.</p>
            </div>
          </div>
          {appendix_tables}
          <details class="table-panel">
            <summary><span>Bash-call tables</span><em>row-level source data</em></summary>
            {bash_tables}
          </details>
        </section>
      </main>
    </div>
    <script>{javascript()}</script>
  </body>
</html>
"""


def main() -> None:
    args = parse_args()
    input_dir = args.input
    output = args.output
    if not input_dir.exists():
        raise SystemExit(f"Input directory does not exist: {input_dir}")
    html_text = build_html(input_dir, output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(html_text, encoding="utf-8")
    print(f"Wrote {output} ({len(html_text):,} bytes)")


if __name__ == "__main__":
    main()
