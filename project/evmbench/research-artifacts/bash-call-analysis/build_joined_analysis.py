#!/usr/bin/env python3
"""Build stable-schema joined datasets, reference tables, and plots."""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Any

os.environ.setdefault("MPLCONFIGDIR", "/tmp/matplotlib")

import matplotlib.pyplot as plt
import numpy as np
import pandas as pd


DEFAULT_LEGACY_INPUT = Path("research-artifacts/bash-call-analysis")
DEFAULT_STABLE_INPUT = Path("/home/experiments_base/forestOfAudits/evmbench_runs_download/bash_command_analysis")
DEFAULT_OUTPUT = Path("research-artifacts/bash-call-analysis/joined")

LEGACY_TO_STABLE_CATEGORY = {
    "blockchain_rpc_probe": ["onchain_state_query"],
    "build_or_test": ["build_test"],
    "code_or_file_inspection": ["file_read_navigation"],
    "code_search": ["text_search"],
    "file_or_repo_mutation": ["file_write_edit"],
    "other": ["other"],
    "run_orchestration": ["other"],
    "script_or_runtime": ["runtime_script"],
    "submission_output": ["report_submission", "completion_marker"],
    "text_processing": ["runtime_script"],
    "version_control": ["git_vcs"],
}

MODE_COLORS = {
    "detect": "#2563eb",
    "patch": "#b45309",
    "exploit": "#be123c",
    "unknown": "#64748b",
}

CATEGORY_COLORS = {
    "file_read_navigation": "#2563eb",
    "onchain_state_query": "#7c3aed",
    "text_search": "#0f766e",
    "file_write_edit": "#dc2626",
    "exploit_execution": "#be123c",
    "build_test": "#b45309",
    "structured_subagent": "#475569",
    "shell_control_flow": "#64748b",
    "shell_output_logging": "#0891b2",
    "git_vcs": "#4f46e5",
    "runtime_script": "#9333ea",
    "network_external": "#0e7490",
    "environment_process": "#577590",
    "report_submission": "#65a30d",
    "completion_marker": "#15803d",
    "dependency_install": "#a16207",
    "other": "#94a3b8",
}

TOOL_COLORS = {
    "bash": "#172033",
    "read": "#2563eb",
    "grep": "#0f766e",
    "glob": "#7c3aed",
    "task": "#b45309",
    "apply_patch": "#dc2626",
    "webfetch": "#0e7490",
}


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--input",
        type=Path,
        default=DEFAULT_LEGACY_INPUT,
        help="Legacy bash-call-analysis artifact directory.",
    )
    parser.add_argument(
        "--stable-input",
        type=Path,
        default=DEFAULT_STABLE_INPUT,
        help="Stable-schema bash_command_analysis artifact directory.",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help="Output directory for joined datasets and refreshed plots.",
    )
    return parser.parse_args()


def read_required_csv(path: Path) -> pd.DataFrame:
    if not path.exists():
        raise FileNotFoundError(path)
    return pd.read_csv(path, keep_default_na=False)


def as_bool(series: pd.Series) -> pd.Series:
    return series.astype(str).str.lower().eq("true")


def command_hash(value: object) -> str:
    return hashlib.sha256(str(value).encode("utf-8", "replace")).hexdigest()[:16]


def exit_bucket(value: object) -> str:
    text = str(value).strip()
    if text == "":
        return "unknown"
    try:
        return "zero" if int(float(text)) == 0 else "nonzero"
    except ValueError:
        return "unknown"


def label_text(value: object, max_len: int = 34) -> str:
    text = str(value)
    if len(text) <= max_len:
        return text
    return text[: max_len - 1] + "..."


def normalized_invocations(stable_input: Path) -> pd.DataFrame:
    inv = read_required_csv(stable_input / "command_invocations.csv")
    inv = inv.copy()
    inv["extractor_version"] = "stable_schema"
    inv["is_bash"] = as_bool(inv["is_bash"])
    inv["exit_bucket"] = inv["exit_code"].map(exit_bucket)
    inv["inner_command"] = inv["inner_command"].fillna("").astype(str)
    inv["raw_command"] = inv["raw_command"].fillna("").astype(str)
    inv["command_hash"] = inv["inner_command"].map(command_hash)
    inv["command_length"] = inv["inner_command"].str.len()
    inv["category_count"] = inv["categories"].fillna("").astype(str).map(
        lambda value: len([part for part in value.split("|") if part])
    )
    for col in ["agent", "mode", "benchmark", "role", "tool", "primary_category", "source_family", "source_format"]:
        inv[col] = inv[col].replace("", "unknown")
    inv["invocation_id"] = pd.to_numeric(inv["invocation_id"], errors="raise").astype(int)
    return inv


def normalized_segments(stable_input: Path, inv: pd.DataFrame) -> pd.DataFrame:
    seg = read_required_csv(stable_input / "command_segments.csv")
    seg = seg.copy()
    seg["extractor_version"] = "stable_schema"
    seg["is_bash"] = as_bool(seg["is_bash"])
    for col in ["agent", "mode", "benchmark", "role", "tool", "primary_category", "source_family"]:
        seg[col] = seg[col].replace("", "unknown")
    seg["segment_id"] = pd.to_numeric(seg["segment_id"], errors="raise").astype(int)
    seg["invocation_id"] = pd.to_numeric(seg["invocation_id"], errors="raise").astype(int)
    lookup = inv[
        [
            "invocation_id",
            "primary_category",
            "categories",
            "exit_bucket",
            "status",
            "command_hash",
        ]
    ].rename(
        columns={
            "primary_category": "invocation_primary_category",
            "categories": "invocation_categories",
        }
    )
    return seg.merge(lookup, on="invocation_id", how="left", validate="many_to_one")


def normalized_run_categories(stable_input: Path) -> pd.DataFrame:
    run_cat = read_required_csv(stable_input / "per_run_category_summary.csv")
    run_cat = run_cat.copy()
    run_cat["extractor_version"] = "stable_schema"
    run_cat["count"] = pd.to_numeric(run_cat["count"], errors="raise").astype(int)
    for col in ["agent", "mode", "benchmark", "role", "primary_category", "source_family"]:
        run_cat[col] = run_cat[col].replace("", "unknown")
    totals = (
        run_cat.groupby(["run_id", "role"], dropna=False)["count"]
        .sum()
        .rename("run_role_total_invocations")
        .reset_index()
    )
    run_cat = run_cat.merge(totals, on=["run_id", "role"], how="left", validate="many_to_one")
    run_cat["run_role_category_share"] = run_cat["count"] / run_cat["run_role_total_invocations"].replace(0, np.nan)
    run_cat["run_role_category_share"] = run_cat["run_role_category_share"].fillna(0)
    return run_cat


def parse_report_tables(report_text: str) -> dict[str, pd.DataFrame]:
    tables: dict[str, pd.DataFrame] = {}
    current_heading = ""
    lines = report_text.splitlines()
    index = 0
    while index < len(lines):
        heading = re.match(r"^###\s+(.+)$", lines[index])
        if heading:
            current_heading = heading.group(1)
            index += 1
            table_lines: list[str] = []
            while index < len(lines) and lines[index].startswith("|"):
                table_lines.append(lines[index])
                index += 1
            if len(table_lines) >= 3:
                headers = [cell.strip() for cell in table_lines[0].strip("|").split("|")]
                rows = []
                for raw in table_lines[2:]:
                    cells = [cell.strip() for cell in raw.strip("|").split("|")]
                    if len(cells) == len(headers):
                        rows.append(dict(zip(headers, cells)))
                tables[current_heading] = pd.DataFrame(rows)
            continue
        index += 1
    return tables


def stable_manifest(stable_input: Path, inv: pd.DataFrame, source_files: list[str]) -> pd.DataFrame:
    report_path = stable_input / "report.md"
    report_text = report_path.read_text(encoding="utf-8") if report_path.exists() else ""
    report_tables = parse_report_tables(report_text)
    sections = {
        "candidate_files": "Candidate Files By Source Family",
        "unique_files": "Unique Files By Source Family",
        "duplicate_files_skipped": "Duplicate Files Skipped By Source Family",
        "extracted_invocations": "Extracted Invocations By Source Family",
    }
    rows: list[dict[str, Any]] = []
    for metric, section in sections.items():
        table = report_tables.get(section)
        if table is None or table.empty:
            continue
        for _, row in table.iterrows():
            rows.append(
                {
                    "metric": metric,
                    "source_family": row.get("source_family", ""),
                    "count": int(str(row.get("count", "0")).replace(",", "")),
                    "extractor_version": "stable_schema",
                }
            )

    observed_sources = inv.groupby("source_family")["source_path"].nunique().reset_index(name="count")
    for _, row in observed_sources.iterrows():
        rows.append(
            {
                "metric": "counted_source_files",
                "source_family": row["source_family"],
                "count": int(row["count"]),
                "extractor_version": "stable_schema",
            }
        )

    if not rows:
        rows.append(
            {
                "metric": "counted_source_files",
                "source_family": "all",
                "count": len(source_files),
                "extractor_version": "stable_schema",
            }
        )
    return pd.DataFrame(rows)


def comparison_rows(legacy: pd.DataFrame, inv: pd.DataFrame, seg: pd.DataFrame, source_files: list[str]) -> pd.DataFrame:
    legacy = legacy.copy()
    legacy["extractor_version"] = "legacy_bash_call_analysis"
    legacy["inner_command"] = legacy["inner_command"].fillna("").astype(str)
    legacy["legacy_mapped_stable_category"] = legacy["intent_category"].map(
        lambda value: "|".join(LEGACY_TO_STABLE_CATEGORY.get(str(value), ["other"]))
    )

    stable_commands = set(inv["inner_command"].fillna("").astype(str))
    legacy_commands = set(legacy["inner_command"])
    overlap_unique = legacy_commands & stable_commands
    overlap_rows = int(legacy["inner_command"].isin(stable_commands).sum())
    rows: list[dict[str, Any]] = [
        {
            "comparison_type": "total",
            "extractor_version": "comparison",
            "legacy_category": "all",
            "stable_category": "all",
            "legacy_count": len(legacy),
            "stable_count": len(inv),
            "legacy_unique_commands": len(legacy_commands),
            "stable_unique_commands": inv["inner_command"].nunique(),
            "overlap_unique_commands": len(overlap_unique),
            "overlap_legacy_rows": overlap_rows,
            "legacy_source": "",
            "stable_source": "",
            "stable_segment_count": len(seg),
            "source_file_count": len(source_files),
            "note": "Stable is primary; legacy rows overlap stable command text and are not additive.",
        }
    ]

    stable_by_category = inv["primary_category"].value_counts().to_dict()
    legacy_by_category = legacy["intent_category"].value_counts().to_dict()
    for legacy_category in sorted(legacy_by_category):
        stable_categories = LEGACY_TO_STABLE_CATEGORY.get(legacy_category, ["other"])
        mapped_mask = legacy["intent_category"].eq(legacy_category)
        mapped_stable_mask = inv["primary_category"].isin(stable_categories)
        rows.append(
            {
                "comparison_type": "category_mapping",
                "extractor_version": "comparison",
                "legacy_category": legacy_category,
                "stable_category": "|".join(stable_categories),
                "legacy_count": int(legacy_by_category[legacy_category]),
                "stable_count": int(sum(stable_by_category.get(category, 0) for category in stable_categories)),
                "legacy_unique_commands": int(legacy.loc[mapped_mask, "inner_command"].nunique()),
                "stable_unique_commands": int(inv.loc[mapped_stable_mask, "inner_command"].nunique()),
                "overlap_unique_commands": int(len(set(legacy.loc[mapped_mask, "inner_command"]) & stable_commands)),
                "overlap_legacy_rows": int(legacy.loc[mapped_mask, "inner_command"].isin(stable_commands).sum()),
                "legacy_source": "",
                "stable_source": "",
                "stable_segment_count": int(seg["primary_category"].isin(stable_categories).sum()),
                "source_file_count": "",
                "note": "Mapped for comparison only; categories are not merged into one total.",
            }
        )

    for source_type, count in legacy["source_type"].value_counts().items():
        rows.append(
            {
                "comparison_type": "legacy_source_type",
                "extractor_version": "legacy_bash_call_analysis",
                "legacy_category": "",
                "stable_category": "",
                "legacy_count": int(count),
                "stable_count": "",
                "legacy_unique_commands": "",
                "stable_unique_commands": "",
                "overlap_unique_commands": "",
                "overlap_legacy_rows": "",
                "legacy_source": source_type,
                "stable_source": "",
                "stable_segment_count": "",
                "source_file_count": int(legacy.loc[legacy["source_type"].eq(source_type), "source_file"].nunique()),
                "note": "Legacy source type coverage.",
            }
        )

    for source_family, count in inv["source_family"].value_counts().items():
        rows.append(
            {
                "comparison_type": "stable_source_family",
                "extractor_version": "stable_schema",
                "legacy_category": "",
                "stable_category": "",
                "legacy_count": "",
                "stable_count": int(count),
                "legacy_unique_commands": "",
                "stable_unique_commands": "",
                "overlap_unique_commands": "",
                "overlap_legacy_rows": "",
                "legacy_source": "",
                "stable_source": source_family,
                "stable_segment_count": int(seg.loc[seg["source_family"].eq(source_family)].shape[0]),
                "source_file_count": int(inv.loc[inv["source_family"].eq(source_family), "source_path"].nunique()),
                "note": "Stable source family coverage.",
            }
        )

    for source_format, count in inv["source_format"].value_counts().items():
        rows.append(
            {
                "comparison_type": "stable_source_format",
                "extractor_version": "stable_schema",
                "legacy_category": "",
                "stable_category": "",
                "legacy_count": "",
                "stable_count": int(count),
                "legacy_unique_commands": "",
                "stable_unique_commands": "",
                "overlap_unique_commands": "",
                "overlap_legacy_rows": "",
                "legacy_source": "",
                "stable_source": source_format,
                "stable_segment_count": "",
                "source_file_count": int(inv.loc[inv["source_format"].eq(source_format), "source_path"].nunique()),
                "note": "Stable source format coverage.",
            }
        )
    return pd.DataFrame(rows)


def nonzero_examples(inv: pd.DataFrame) -> pd.DataFrame:
    examples = inv[inv["exit_bucket"].eq("nonzero")].copy()
    if examples.empty:
        return examples
    examples["command_preview"] = examples["inner_command"].astype(str).str.replace(r"\s+", " ", regex=True).str.slice(0, 180)
    return examples[
        [
            "invocation_id",
            "agent",
            "mode",
            "benchmark",
            "tool",
            "primary_category",
            "exit_code",
            "status",
            "command_preview",
            "source_path",
            "source_line",
        ]
    ].head(80)


def setup_style() -> None:
    plt.rcParams.update(
        {
            "figure.facecolor": "#f6f7fb",
            "axes.facecolor": "#ffffff",
            "axes.edgecolor": "#dbe3ef",
            "axes.labelcolor": "#172033",
            "xtick.color": "#536174",
            "ytick.color": "#536174",
            "text.color": "#172033",
            "font.size": 10,
            "axes.titlesize": 15,
            "axes.titleweight": "bold",
            "axes.labelsize": 10,
            "legend.frameon": False,
            "savefig.facecolor": "#f6f7fb",
            "savefig.bbox": "tight",
            "savefig.dpi": 180,
        }
    )


def save_plot(fig: plt.Figure, path: Path) -> None:
    fig.savefig(path)
    plt.close(fig)


def plot_source_manifest(manifest: pd.DataFrame, out: Path) -> None:
    if manifest.empty:
        return
    metrics = ["candidate_files", "unique_files", "duplicate_files_skipped", "extracted_invocations", "counted_source_files"]
    plot_df = manifest[manifest["metric"].isin(metrics)].copy()
    if plot_df.empty:
        return
    pivot = plot_df.pivot_table(index="source_family", columns="metric", values="count", aggfunc="sum", fill_value=0)
    pivot = pivot.reindex(columns=[metric for metric in metrics if metric in pivot.columns])
    pivot = pivot.loc[pivot.sum(axis=1).sort_values().index]
    colors = ["#94a3b8", "#2563eb", "#dc2626", "#0f766e", "#b45309"][: len(pivot.columns)]
    fig, ax = plt.subplots(figsize=(12, 7))
    pivot.plot(kind="barh", ax=ax, color=colors, width=0.78)
    ax.set_title("Stable Source Coverage and De-Dupe Manifest")
    ax.set_xlabel("Files or invocations")
    ax.set_ylabel("Source family")
    ax.grid(True, axis="x", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="lower right", fontsize=8)
    save_plot(fig, out / "stable_source_manifest.png")


def plot_invocation_category_mix(inv: pd.DataFrame, out: Path) -> None:
    plot_df = inv.copy()
    plot_df["row"] = plot_df["agent"] + " / " + plot_df["mode"]
    counts = plot_df.groupby(["row", "primary_category"], dropna=False).size().rename("count").reset_index()
    top_rows = plot_df["row"].value_counts().head(14).index
    top_categories = plot_df["primary_category"].value_counts().head(9).index
    counts = counts[counts["row"].isin(top_rows)].copy()
    counts["category_group"] = np.where(counts["primary_category"].isin(top_categories), counts["primary_category"], "other")
    pivot = counts.pivot_table(index="row", columns="category_group", values="count", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[plot_df["row"].value_counts().loc[pivot.index].sort_values().index]
    pivot = pivot[pivot.sum(axis=0).sort_values(ascending=False).index]
    share = pivot.div(pivot.sum(axis=1), axis=0) * 100
    fig, ax = plt.subplots(figsize=(13, 8))
    left = np.zeros(len(share))
    for category in share.columns:
        values = share[category].to_numpy()
        ax.barh(
            share.index,
            values,
            left=left,
            label=category,
            color=CATEGORY_COLORS.get(category, "#94a3b8"),
            edgecolor="#ffffff",
            linewidth=0.4,
        )
        left += values
    ax.set_title("Stable Invocation Category Mix by Agent and Mode")
    ax.set_xlabel("Share of invocations/tools (%)")
    ax.set_ylabel("Agent / mode")
    ax.set_xlim(0, 100)
    ax.grid(True, axis="x", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8)
    save_plot(fig, out / "stable_invocation_category_mix.png")


def plot_segment_vs_invocation_mix(inv: pd.DataFrame, seg: pd.DataFrame, out: Path) -> None:
    inv_counts = inv["primary_category"].value_counts().rename("invocation_count")
    seg_counts = seg["primary_category"].value_counts().rename("segment_count")
    joined = pd.concat([inv_counts, seg_counts], axis=1).fillna(0)
    joined["total"] = joined["invocation_count"] + joined["segment_count"]
    joined = joined.sort_values("total", ascending=False).head(16).sort_values("total")
    joined["invocation_share"] = joined["invocation_count"] / len(inv) * 100
    joined["segment_share"] = joined["segment_count"] / len(seg) * 100
    y = np.arange(len(joined))
    fig, ax = plt.subplots(figsize=(12, 8))
    ax.barh(y - 0.19, joined["invocation_share"], height=0.36, color="#2563eb", label="Invocation primary category")
    ax.barh(y + 0.19, joined["segment_share"], height=0.36, color="#0f766e", label="Segment category")
    ax.set_yticks(y)
    ax.set_yticklabels(joined.index)
    ax.set_xlabel("Share of grain (%)")
    ax.set_ylabel("Category")
    ax.set_title("Segment Category Mix vs Invocation Category Mix")
    ax.grid(True, axis="x", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="lower right")
    save_plot(fig, out / "stable_segment_vs_invocation_mix.png")


def plot_tool_ecology(inv: pd.DataFrame, out: Path) -> None:
    top_categories = inv["primary_category"].value_counts().head(12).index
    plot_df = inv[inv["primary_category"].isin(top_categories)].copy()
    pivot = plot_df.pivot_table(index="primary_category", columns="tool", values="invocation_id", aggfunc="count", fill_value=0)
    pivot = pivot.loc[inv["primary_category"].value_counts().loc[pivot.index].sort_values().index]
    columns = ["bash", "read", "grep", "glob", "task", "apply_patch", "webfetch"]
    pivot = pivot.reindex(columns=[column for column in columns if column in pivot.columns], fill_value=0)
    fig, ax = plt.subplots(figsize=(12, 8))
    left = np.zeros(len(pivot))
    for tool in pivot.columns:
        values = pivot[tool].to_numpy()
        ax.barh(
            pivot.index,
            values,
            left=left,
            color=TOOL_COLORS.get(tool, "#94a3b8"),
            label=tool,
            edgecolor="#ffffff",
            linewidth=0.4,
        )
        left += values
    ax.set_title("Tool Ecology Across Stable Invocation Categories")
    ax.set_xlabel("Invocations/tools")
    ax.set_ylabel("Primary category")
    ax.grid(True, axis="x", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="lower right", fontsize=8)
    save_plot(fig, out / "stable_tool_ecology.png")


def plot_nonzero_exit_categories(inv: pd.DataFrame, out: Path) -> None:
    nonzero = inv[inv["exit_bucket"].eq("nonzero")]
    if nonzero.empty:
        return
    counts = nonzero.groupby(["primary_category", "tool"]).size().rename("count").reset_index()
    top_categories = nonzero["primary_category"].value_counts().head(12).index
    counts = counts[counts["primary_category"].isin(top_categories)]
    pivot = counts.pivot_table(index="primary_category", columns="tool", values="count", aggfunc="sum", fill_value=0)
    pivot = pivot.loc[nonzero["primary_category"].value_counts().loc[pivot.index].sort_values().index]
    fig, ax = plt.subplots(figsize=(12, 7))
    left = np.zeros(len(pivot))
    for tool in pivot.columns:
        values = pivot[tool].to_numpy()
        ax.barh(
            pivot.index,
            values,
            left=left,
            color=TOOL_COLORS.get(tool, "#94a3b8"),
            label=tool,
            edgecolor="#ffffff",
            linewidth=0.4,
        )
        left += values
    ax.set_title("Nonzero Exit Examples by Category and Tool")
    ax.set_xlabel("Nonzero-exit invocations")
    ax.set_ylabel("Primary category")
    ax.grid(True, axis="x", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="lower right", fontsize=8)
    save_plot(fig, out / "stable_nonzero_exit_categories.png")


def plot_command_ecology_bubble(seg: pd.DataFrame, out: Path) -> None:
    plot_df = seg.copy()
    plot_df["first_token"] = plot_df["first_token"].replace("", "structured_tool").fillna("structured_tool")
    top_tokens = plot_df["first_token"].value_counts().head(18).index
    top_categories = plot_df["primary_category"].value_counts().head(12).index
    plot_df = plot_df[plot_df["first_token"].isin(top_tokens) & plot_df["primary_category"].isin(top_categories)]
    if plot_df.empty:
        return
    counts = (
        plot_df.groupby(["first_token", "primary_category"], dropna=False)
        .agg(segment_count=("segment_id", "count"), run_count=("run_id", "nunique"))
        .reset_index()
    )
    tokens = list(top_tokens)
    categories = list(top_categories)
    token_pos = {token: idx for idx, token in enumerate(tokens)}
    category_pos = {category: idx for idx, category in enumerate(categories)}
    fig, ax = plt.subplots(figsize=(14, 8))
    sizes = 42 + counts["segment_count"].to_numpy() * 4.5
    colors = [CATEGORY_COLORS.get(category, "#94a3b8") for category in counts["primary_category"]]
    ax.scatter(
        counts["first_token"].map(token_pos),
        counts["primary_category"].map(category_pos),
        s=sizes,
        c=colors,
        alpha=0.76,
        edgecolors="#ffffff",
        linewidths=0.7,
    )
    ax.set_xticks(range(len(tokens)))
    ax.set_xticklabels([label_text(token, 18) for token in tokens], rotation=35, ha="right")
    ax.set_yticks(range(len(categories)))
    ax.set_yticklabels(categories)
    ax.set_xlabel("First shell segment token")
    ax.set_ylabel("Segment category")
    ax.set_title("Command Ecology by Segment Token and Category")
    ax.grid(True, color="#e7edf5", linewidth=0.8)
    save_plot(fig, out / "command_ecology_bubble.png")


def plot_complexity_vs_outcome(inv: pd.DataFrame, seg: pd.DataFrame, out: Path) -> None:
    segment_counts = seg.groupby("invocation_id").size().rename("segment_count").reset_index()
    plot_df = inv.merge(segment_counts, on="invocation_id", how="left", validate="one_to_one")
    plot_df["segment_count"] = plot_df["segment_count"].fillna(1)
    colors = {"zero": "#2563eb", "nonzero": "#be123c", "unknown": "#64748b"}
    fig, ax = plt.subplots(figsize=(13, 8))
    for bucket, bucket_df in plot_df.groupby("exit_bucket", dropna=False):
        ax.scatter(
            bucket_df["command_length"].clip(lower=1),
            bucket_df["segment_count"],
            s=32 + bucket_df["category_count"].clip(lower=1) * 18,
            c=colors.get(str(bucket), "#94a3b8"),
            alpha=0.42 if bucket == "zero" else 0.68,
            label=str(bucket),
            edgecolors="none",
        )
    ax.set_xscale("log")
    ax.set_xlabel("Invocation command length (log scale)")
    ax.set_ylabel("Segments per invocation")
    ax.set_title("Command Complexity and Outcome")
    ax.grid(True, color="#e7edf5", linewidth=0.8)
    ax.legend(title="Exit bucket", loc="upper left")
    save_plot(fig, out / "complexity_vs_outcome.png")


def plot_intent_fingerprint_heatmap(run_cat: pd.DataFrame, out: Path) -> None:
    top_categories = run_cat.groupby("primary_category")["count"].sum().sort_values(ascending=False).head(12).index
    plot_df = run_cat[run_cat["primary_category"].isin(top_categories)].copy()
    plot_df["row"] = plot_df["agent"] + " / " + plot_df["mode"]
    pivot = plot_df.pivot_table(index="row", columns="primary_category", values="count", aggfunc="sum", fill_value=0)
    if pivot.empty:
        return
    pivot = pivot.loc[pivot.sum(axis=1).sort_values(ascending=False).head(16).index]
    pivot = pivot[pivot.sum(axis=0).sort_values(ascending=False).index]
    share = pivot.div(pivot.sum(axis=1).replace(0, np.nan), axis=0).fillna(0) * 100
    fig, ax = plt.subplots(figsize=(14, 8))
    image = ax.imshow(share.to_numpy(), aspect="auto", cmap="YlGnBu", vmin=0)
    ax.set_xticks(np.arange(len(share.columns)))
    ax.set_xticklabels([label_text(value, 22) for value in share.columns], rotation=35, ha="right")
    ax.set_yticks(np.arange(len(share.index)))
    ax.set_yticklabels([label_text(value, 34) for value in share.index])
    ax.set_title("Category Fingerprint by Agent and Mode")
    ax.set_xlabel("Primary category")
    ax.set_ylabel("Agent / mode")
    fig.colorbar(image, ax=ax, label="Share of invocations/tools (%)")
    save_plot(fig, out / "intent_fingerprint_heatmap.png")


def plot_run_behavior_map(inv: pd.DataFrame, seg: pd.DataFrame, out: Path) -> None:
    inv_run = (
        inv.groupby("run_id", dropna=False)
        .agg(
            invocations=("invocation_id", "count"),
            bash_invocations=("is_bash", "sum"),
            nonzero=("exit_bucket", lambda values: values.eq("nonzero").sum()),
            unique_tools=("tool", "nunique"),
            agent=("agent", "first"),
            mode=("mode", "first"),
        )
        .reset_index()
    )
    segment_counts = seg.groupby("run_id").size().rename("segments").reset_index()
    plot_df = inv_run.merge(segment_counts, on="run_id", how="left", validate="one_to_one")
    plot_df["segments"] = plot_df["segments"].fillna(plot_df["invocations"])
    plot_df["segments_per_invocation"] = plot_df["segments"] / plot_df["invocations"].replace(0, np.nan)
    plot_df["nonzero_share"] = plot_df["nonzero"] / plot_df["invocations"].replace(0, np.nan)
    fig, ax = plt.subplots(figsize=(13, 8))
    for mode, mode_df in plot_df.groupby("mode", dropna=False):
        ax.scatter(
            mode_df["invocations"],
            mode_df["segments_per_invocation"],
            s=60 + mode_df["nonzero_share"].fillna(0) * 900 + mode_df["unique_tools"] * 12,
            c=MODE_COLORS.get(str(mode), "#64748b"),
            alpha=0.68,
            label=str(mode),
            edgecolors="#ffffff",
            linewidths=0.6,
        )
    ax.set_xscale("log")
    ax.set_xlabel("Invocations/tools per run (log scale)")
    ax.set_ylabel("Segments per invocation")
    ax.set_title("Run Behavior Map")
    ax.grid(True, color="#e7edf5", linewidth=0.8)
    ax.legend(title="Mode", loc="upper right")
    save_plot(fig, out / "run_behavior_map.png")


def plot_run_similarity_map(run_cat: pd.DataFrame, out: Path) -> None:
    pivot = run_cat.pivot_table(index="run_id", columns="primary_category", values="count", aggfunc="sum", fill_value=0)
    if pivot.shape[0] < 2 or pivot.shape[1] < 2:
        return
    share = pivot.div(pivot.sum(axis=1).replace(0, np.nan), axis=0).fillna(0)
    centered = share.to_numpy() - share.to_numpy().mean(axis=0)
    _, _, vt = np.linalg.svd(centered, full_matrices=False)
    coords = centered @ vt[:2].T
    meta = run_cat.groupby("run_id", dropna=False).agg(mode=("mode", "first"), agent=("agent", "first")).reindex(share.index)
    totals = pivot.sum(axis=1).reindex(share.index)
    fig, ax = plt.subplots(figsize=(13, 8))
    for mode, mode_index in meta.groupby("mode").groups.items():
        positions = share.index.get_indexer(mode_index)
        ax.scatter(
            coords[positions, 0],
            coords[positions, 1],
            s=38 + np.sqrt(totals.iloc[positions].to_numpy()) * 15,
            c=MODE_COLORS.get(str(mode), "#64748b"),
            alpha=0.68,
            label=str(mode),
            edgecolors="#ffffff",
            linewidths=0.6,
        )
    ax.axhline(0, color="#dbe3ef", linewidth=1)
    ax.axvline(0, color="#dbe3ef", linewidth=1)
    ax.set_xlabel("Category mix component 1")
    ax.set_ylabel("Category mix component 2")
    ax.set_title("Run Similarity Map from Category Mix")
    ax.grid(True, color="#eef2f7", linewidth=0.7)
    ax.legend(title="Mode", loc="best")
    save_plot(fig, out / "run_similarity_map.png")


def plot_timeline_intent_stream(inv: pd.DataFrame, out: Path) -> None:
    plot_df = inv.sort_values("invocation_id").copy()
    if plot_df.empty:
        return
    top_categories = plot_df["primary_category"].value_counts().head(9).index
    plot_df["category_group"] = np.where(plot_df["primary_category"].isin(top_categories), plot_df["primary_category"], "other")
    bin_count = min(28, max(8, len(plot_df) // 140))
    plot_df["timeline_bin"] = pd.cut(
        plot_df["invocation_id"],
        bins=bin_count,
        labels=False,
        include_lowest=True,
        duplicates="drop",
    )
    counts = plot_df.pivot_table(index="timeline_bin", columns="category_group", values="invocation_id", aggfunc="count", fill_value=0)
    if counts.empty:
        return
    counts = counts.reindex(columns=counts.sum(axis=0).sort_values(ascending=False).index)
    x = np.arange(len(counts.index))
    colors = [CATEGORY_COLORS.get(category, "#94a3b8") for category in counts.columns]
    fig, ax = plt.subplots(figsize=(14, 7))
    ax.stackplot(x, [counts[column].to_numpy() for column in counts.columns], labels=counts.columns, colors=colors, alpha=0.88)
    ax.set_xlabel("Invocation order bins")
    ax.set_ylabel("Invocations/tools")
    ax.set_title("Timeline Category Stream")
    ax.grid(True, axis="y", color="#e7edf5", linewidth=0.8)
    ax.legend(loc="upper left", bbox_to_anchor=(1.01, 1), fontsize=8)
    save_plot(fig, out / "timeline_intent_stream.png")


def clean_plot_dir(plot_dir: Path) -> None:
    plot_dir.mkdir(parents=True, exist_ok=True)
    for path in plot_dir.glob("*.png"):
        path.unlink()


def make_plots(inv: pd.DataFrame, seg: pd.DataFrame, run_cat: pd.DataFrame, manifest: pd.DataFrame, plot_dir: Path) -> None:
    setup_style()
    clean_plot_dir(plot_dir)
    plot_source_manifest(manifest, plot_dir)
    plot_invocation_category_mix(inv, plot_dir)
    plot_segment_vs_invocation_mix(inv, seg, plot_dir)
    plot_tool_ecology(inv, plot_dir)
    plot_nonzero_exit_categories(inv, plot_dir)
    plot_command_ecology_bubble(seg, plot_dir)
    plot_complexity_vs_outcome(inv, seg, plot_dir)
    plot_intent_fingerprint_heatmap(run_cat, plot_dir)
    plot_run_behavior_map(inv, seg, plot_dir)
    plot_run_similarity_map(run_cat, plot_dir)
    plot_timeline_intent_stream(inv, plot_dir)


def validate_outputs(inv: pd.DataFrame, seg: pd.DataFrame, run_cat: pd.DataFrame, legacy: pd.DataFrame, source_files: list[str]) -> None:
    if len(inv) != 3840:
        raise RuntimeError(f"Expected 3,840 stable invocations, found {len(inv):,}")
    bash_count = int(inv["is_bash"].sum())
    if bash_count != 2599:
        raise RuntimeError(f"Expected 2,599 bash invocations, found {bash_count:,}")
    nonbash_count = len(inv) - bash_count
    if nonbash_count != 1241:
        raise RuntimeError(f"Expected 1,241 OpenCode non-bash tool calls, found {nonbash_count:,}")
    if len(seg) != 8233:
        raise RuntimeError(f"Expected 8,233 stable segments, found {len(seg):,}")
    if len(source_files) != 1179:
        raise RuntimeError(f"Expected 1,179 source files, found {len(source_files):,}")
    run_count = inv["run_id"].nunique()
    if run_count != 146:
        raise RuntimeError(f"Expected 146 runs, found {run_count:,}")
    missing_segments = set(seg["invocation_id"]) - set(inv["invocation_id"])
    if missing_segments:
        raise RuntimeError(f"Segments reference unknown invocation IDs: {sorted(missing_segments)[:10]}")
    if int(run_cat["count"].sum()) != len(inv):
        raise RuntimeError("Per-run category counts do not sum to stable invocation count")
    if len(legacy) != 1244:
        raise RuntimeError(f"Expected 1,244 legacy rows, found {len(legacy):,}")
    legacy_commands = legacy["inner_command"].fillna("").astype(str)
    stable_commands = set(inv["inner_command"].fillna("").astype(str))
    if not legacy_commands.isin(stable_commands).all():
        missing = legacy.loc[~legacy_commands.isin(stable_commands), "inner_command"].head(5).tolist()
        raise RuntimeError(f"Legacy commands missing from stable command text: {missing}")


def build_join_report(
    inv: pd.DataFrame,
    seg: pd.DataFrame,
    run_cat: pd.DataFrame,
    comparison: pd.DataFrame,
    manifest: pd.DataFrame,
    source_files: list[str],
) -> str:
    bash = int(inv["is_bash"].sum())
    nonbash = len(inv) - bash
    source_count = len(source_files)
    run_count = inv["run_id"].nunique()
    legacy_total = comparison[comparison["comparison_type"].eq("total")].iloc[0]
    top_categories = inv["primary_category"].value_counts().head(8)
    category_lines = "\n".join(f"- `{category}`: {count:,}" for category, count in top_categories.items())
    manifest_totals = manifest.groupby("metric")["count"].sum().to_dict()
    return f"""# Refreshed Bash Command Joined Analysis

## Primary Dataset

- Stable invocations/tools: **{len(inv):,}**
- Stable bash invocations: **{bash:,}**
- Stable OpenCode non-bash tool calls: **{nonbash:,}**
- Stable command segments: **{len(seg):,}**
- Counted source files: **{source_count:,}**
- Distinct runs: **{run_count:,}**

The stable-schema artifact is the primary dataset. The `bash_calls.csv` extractor is retained as reference data only; its {int(legacy_total["legacy_count"]):,} rows overlap stable command text and must not be added to the stable totals.

## Manifest Counts

- Candidate files inspected: **{int(manifest_totals.get("candidate_files", 0)):,}**
- Unique files after content de-duplication: **{int(manifest_totals.get("unique_files", 0)):,}**
- Duplicate files skipped: **{int(manifest_totals.get("duplicate_files_skipped", 0)):,}**
- Extracted stable invocations by source-family manifest: **{int(manifest_totals.get("extracted_invocations", 0)):,}**
- Source files with kept stable invocations: **{source_count:,}**

## Top Stable Categories

{category_lines}

## Joined Grains

- `stable_invocation_fact.csv`: one row per stable invocation/tool keyed by `invocation_id`.
- `stable_segment_fact.csv`: one row per stable shell segment or structured tool pseudo-segment keyed by `segment_id`, many-to-one to `invocation_id`.
- `stable_run_category_fact.csv`: per-run primary-category counts keyed by `run_id`, `role`, and `primary_category`.
- `extractor_comparison.csv`: extractor reference mapping, source coverage, and overlap facts with `extractor_version` carried explicitly.

## Validation

- Segment `invocation_id` values all resolve to stable invocation rows.
- Per-run category counts sum to **{int(run_cat["count"].sum()):,}**.
- Legacy `bash_calls.csv` remains **{int(legacy_total["legacy_count"]):,}** rows.
- Legacy unique command texts overlapping stable command text: **{int(legacy_total["overlap_unique_commands"]):,} / {int(legacy_total["legacy_unique_commands"]):,}**.
"""


def write_outputs(legacy_input: Path, stable_input: Path, out: Path) -> None:
    legacy = read_required_csv(legacy_input / "bash_calls.csv")
    inv = normalized_invocations(stable_input)
    seg = normalized_segments(stable_input, inv)
    run_cat = normalized_run_categories(stable_input)

    source_files_path = stable_input / "source_files.json"
    source_files = json.loads(source_files_path.read_text(encoding="utf-8")) if source_files_path.exists() else []
    manifest = stable_manifest(stable_input, inv, source_files)
    comparison = comparison_rows(legacy, inv, seg, source_files)
    examples = nonzero_examples(inv)
    validate_outputs(inv, seg, run_cat, legacy, source_files)

    out.mkdir(parents=True, exist_ok=True)
    plot_dir = out / "plots"
    inv.to_csv(out / "stable_invocation_fact.csv", index=False)
    seg.to_csv(out / "stable_segment_fact.csv", index=False)
    run_cat.to_csv(out / "stable_run_category_fact.csv", index=False)
    comparison.to_csv(out / "extractor_comparison.csv", index=False)
    manifest.to_csv(out / "stable_manifest_fact.csv", index=False)
    examples.to_csv(out / "nonzero_exit_examples.csv", index=False)
    (out / "stable_source_files.json").write_text(json.dumps(source_files, indent=2) + "\n", encoding="utf-8")
    taxonomy_path = stable_input / "category_taxonomy.json"
    if taxonomy_path.exists():
        (out / "stable_category_taxonomy.json").write_text(taxonomy_path.read_text(encoding="utf-8"), encoding="utf-8")
    make_plots(inv, seg, run_cat, manifest, plot_dir)
    (out / "join_report.md").write_text(
        build_join_report(inv, seg, run_cat, comparison, manifest, source_files),
        encoding="utf-8",
    )
    print(f"Wrote stable joined datasets and plots to {out}")


def main() -> None:
    args = parse_args()
    write_outputs(args.input, args.stable_input, args.output)


if __name__ == "__main__":
    main()
