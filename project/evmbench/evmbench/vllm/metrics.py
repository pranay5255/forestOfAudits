#!/usr/bin/env python3
"""Prometheus metrics collection for EVMBench vLLM endpoints."""

from __future__ import annotations

import argparse
import csv
import gzip
import json
import math
import os
import re
import time
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from evmbench.vllm.common import (
    clean_env_value,
    fail,
    load_project_env,
    project_root,
    require_env,
    server_root_from_api_base,
)

_LABEL_RE = re.compile(r'([a-zA-Z_][a-zA-Z0-9_]*)="((?:[^"\\]|\\.)*)"')
_SAMPLE_RE = re.compile(r"^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+([-+A-Za-z0-9.eE]+)(?:\s+\d+)?$")

LATENCY_HISTOGRAMS = (
    "vllm:time_to_first_token_seconds",
    "vllm:inter_token_latency_seconds",
    "vllm:time_per_output_token_seconds",
    "vllm:e2e_request_latency_seconds",
    "vllm:request_prefill_time_seconds",
    "vllm:request_decode_time_seconds",
)
GAUGE_METRICS = (
    "vllm:num_requests_running",
    "vllm:num_requests_waiting",
    "vllm:num_requests_swapped",
    "vllm:kv_cache_usage_perc",
    "vllm:gpu_cache_usage_perc",
    "vllm:cpu_cache_usage_perc",
)
COUNTER_METRICS = (
    "vllm:prefix_cache_queries",
    "vllm:prefix_cache_queries_total",
    "vllm:prefix_cache_hits",
    "vllm:prefix_cache_hits_total",
    "vllm:prompt_tokens_total",
    "vllm:generation_tokens_total",
    "vllm:request_prompt_tokens_total",
    "vllm:request_generation_tokens_total",
    "vllm:request_success_total",
)
GPU_CSV_FIELDS = (
    "index",
    "uuid",
    "name",
    "utilization.gpu",
    "utilization.memory",
    "memory.used",
    "memory.total",
    "power.draw",
    "temperature.gpu",
    "clocks.sm",
    "clocks.mem",
)
GPU_NUMERIC_FIELDS = (
    "utilization_gpu_percent",
    "utilization_memory_percent",
    "memory_used_mib",
    "memory_total_mib",
    "power_draw_watts",
    "temperature_gpu_c",
    "clocks_sm_mhz",
    "clocks_mem_mhz",
)


@dataclass(frozen=True)
class MetricSample:
    name: str
    labels: dict[str, str]
    value: float

    def to_json(self) -> dict[str, Any]:
        return {"name": self.name, "labels": self.labels, "value": self.value}


def _parse_float(raw: str) -> float:
    normalized = raw.lower()
    if normalized in {"+inf", "inf"}:
        return math.inf
    if normalized == "-inf":
        return -math.inf
    if normalized == "nan":
        return math.nan
    return float(raw)


def _parse_labels(raw: str | None) -> dict[str, str]:
    if not raw:
        return {}
    labels: dict[str, str] = {}
    for key, value in _LABEL_RE.findall(raw):
        labels[key] = bytes(value, "utf-8").decode("unicode_escape")
    return labels


def parse_prometheus_text(text: str) -> list[MetricSample]:
    samples: list[MetricSample] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        match = _SAMPLE_RE.match(stripped)
        if not match:
            continue
        name, raw_labels, raw_value = match.groups()
        samples.append(MetricSample(name=name, labels=_parse_labels(raw_labels), value=_parse_float(raw_value)))
    return samples


def fetch_metrics_text(
    *,
    api_base: str | None = None,
    server_root: str | None = None,
    api_key: str | None = None,
    timeout: float = 30.0,
) -> str:
    root = (server_root or server_root_from_api_base(api_base or "")).rstrip("/")
    if not root:
        raise RuntimeError("api_base or server_root is required to fetch vLLM metrics.")
    headers = {"Authorization": f"Bearer {api_key}"} if api_key else {}
    response = requests.get(f"{root}/metrics", headers=headers, timeout=timeout)
    response.raise_for_status()
    return response.text


def _values(samples: Iterable[MetricSample], name: str) -> list[float]:
    return [sample.value for sample in samples if sample.name == name and math.isfinite(sample.value)]


def _summary(values: list[float]) -> dict[str, float | int | None]:
    if not values:
        return {"count": 0, "sum": None, "min": None, "max": None, "mean": None}
    total = sum(values)
    return {
        "count": len(values),
        "sum": total,
        "min": min(values),
        "max": max(values),
        "mean": total / len(values),
    }


def _counter_total(samples: Iterable[MetricSample], name: str) -> float | None:
    values = _values(samples, name)
    return sum(values) if values else None


def _detected_counter_names(samples: Iterable[MetricSample]) -> list[str]:
    names = {sample.name for sample in samples if sample.name.startswith("vllm:")}
    counters = {
        name
        for name in names
        if name in COUNTER_METRICS
        or (name.endswith("_total") and not name.endswith(("_bucket_total", "_sum_total", "_count_total")))
    }
    return sorted(counters)


def _histogram_summary(samples: Iterable[MetricSample], base_name: str) -> dict[str, Any] | None:
    sample_list = list(samples)
    count = _counter_total(sample_list, f"{base_name}_count")
    total = _counter_total(sample_list, f"{base_name}_sum")
    buckets: list[tuple[float, float]] = []
    for sample in sample_list:
        if sample.name != f"{base_name}_bucket":
            continue
        le = sample.labels.get("le")
        if le is None:
            continue
        buckets.append((_parse_float(le), sample.value))
    if count is None and total is None and not buckets:
        return None
    buckets.sort(key=lambda item: item[0])
    finite_count = count if count is not None and count > 0 else None
    percentiles: dict[str, float | None] = {}
    for percentile in (0.5, 0.9, 0.95, 0.99):
        estimate = None
        if finite_count:
            threshold = finite_count * percentile
            for upper_bound, bucket_count in buckets:
                if bucket_count >= threshold:
                    estimate = upper_bound
                    break
        percentiles[f"p{int(percentile * 100)}"] = estimate
    return {
        "count": count,
        "sum": total,
        "mean": (total / count) if count and total is not None else None,
        "buckets": [{"le": upper, "count": value} for upper, value in buckets],
        "percentiles": percentiles,
    }


def _first_counter(counters: Mapping[str, float | None], names: tuple[str, ...]) -> float | None:
    for name in names:
        value = counters.get(name)
        if value is not None:
            return value
    return None


def summarize_vllm_metrics(samples: list[MetricSample]) -> dict[str, Any]:
    by_prefix = [sample for sample in samples if sample.name.startswith("vllm:")]
    metric_names = sorted({sample.name for sample in by_prefix})
    gauge_names = sorted(set(GAUGE_METRICS).intersection(metric_names))
    counter_names = _detected_counter_names(by_prefix)
    gauges = {name: _summary(_values(by_prefix, name)) for name in gauge_names}
    counters = {name: _counter_total(by_prefix, name) for name in counter_names}

    prefix_queries = _first_counter(counters, ("vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total"))
    prefix_hits = _first_counter(counters, ("vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total"))
    prefix_hit_rate = (
        (prefix_hits / prefix_queries)
        if prefix_queries not in (None, 0) and prefix_hits is not None
        else None
    )
    histograms = {
        name: summary
        for name in LATENCY_HISTOGRAMS
        if (summary := _histogram_summary(by_prefix, name)) is not None
    }
    request_queue = {
        name: gauges[name]
        for name in ("vllm:num_requests_running", "vllm:num_requests_waiting", "vllm:num_requests_swapped")
        if name in gauges
    }
    cache_usage = {
        name: gauges[name]
        for name in gauge_names
        if "cache_usage" in name or name in {"vllm:kv_cache_usage_perc", "vllm:gpu_cache_usage_perc", "vllm:cpu_cache_usage_perc"}
    }
    token_counters = {
        name: counters[name]
        for name in (
            "vllm:prompt_tokens_total",
            "vllm:generation_tokens_total",
            "vllm:request_prompt_tokens_total",
            "vllm:request_generation_tokens_total",
        )
        if name in counters
    }
    return {
        "sample_count": len(samples),
        "vllm_sample_count": len(by_prefix),
        "vllm_metric_names": metric_names,
        "vllm_samples": [sample.to_json() for sample in by_prefix],
        "gauges": gauges,
        "request_queue": request_queue,
        "cache_usage": cache_usage,
        "counters": counters,
        "token_counters": token_counters,
        "prefix_cache_hit_rate": prefix_hit_rate,
        "latency_histograms": histograms,
    }


def diff_summaries(before: dict[str, Any], after: dict[str, Any]) -> dict[str, Any]:
    before_counters = before.get("counters", {}) if isinstance(before.get("counters"), dict) else {}
    after_counters = after.get("counters", {}) if isinstance(after.get("counters"), dict) else {}
    counter_delta: dict[str, float] = {}
    for name, after_value in after_counters.items():
        before_value = before_counters.get(name)
        if isinstance(after_value, (int, float)) and isinstance(before_value, (int, float)):
            counter_delta[name] = float(after_value) - float(before_value)
    queries = _first_counter(counter_delta, ("vllm:prefix_cache_queries", "vllm:prefix_cache_queries_total"))
    hits = _first_counter(counter_delta, ("vllm:prefix_cache_hits", "vllm:prefix_cache_hits_total"))
    histogram_delta: dict[str, dict[str, float | None]] = {}
    before_histograms = before.get("latency_histograms", {}) if isinstance(before.get("latency_histograms"), dict) else {}
    after_histograms = after.get("latency_histograms", {}) if isinstance(after.get("latency_histograms"), dict) else {}
    for name, after_histogram in after_histograms.items():
        before_histogram = before_histograms.get(name, {}) if isinstance(before_histograms, dict) else {}
        if not isinstance(after_histogram, dict) or not isinstance(before_histogram, dict):
            continue
        after_count = after_histogram.get("count")
        before_count = before_histogram.get("count")
        after_sum = after_histogram.get("sum")
        before_sum = before_histogram.get("sum")
        count_delta = (
            float(after_count) - float(before_count)
            if isinstance(after_count, (int, float)) and isinstance(before_count, (int, float))
            else None
        )
        sum_delta = (
            float(after_sum) - float(before_sum)
            if isinstance(after_sum, (int, float)) and isinstance(before_sum, (int, float))
            else None
        )
        histogram_delta[name] = {
            "count_delta": count_delta,
            "sum_delta": sum_delta,
            "mean_delta_window": (sum_delta / count_delta) if count_delta not in (None, 0) and sum_delta is not None else None,
        }
    return {
        "counter_delta": counter_delta,
        "prefix_cache_hit_rate_delta_window": (hits / queries) if queries not in (None, 0) and hits is not None else None,
        "histogram_delta": histogram_delta,
    }


def snapshot_artifact_paths(output_dir: Path) -> dict[str, str]:
    return {
        "raw_prometheus": str(output_dir / "metrics.prom"),
        "samples_json": str(output_dir / "metrics.samples.json"),
        "summary_json": str(output_dir / "metrics.summary.json"),
    }


def write_snapshot(output_dir: Path, *, metrics_text: str, samples: list[MetricSample], summary: dict[str, Any]) -> dict[str, str]:
    output_dir.mkdir(parents=True, exist_ok=True)
    (output_dir / "metrics.prom").write_text(metrics_text, encoding="utf-8")
    (output_dir / "metrics.samples.json").write_text(
        json.dumps([sample.to_json() for sample in samples], indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    (output_dir / "metrics.summary.json").write_text(
        json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n",
        encoding="utf-8",
    )
    return snapshot_artifact_paths(output_dir)


def snapshot_metrics(
    *,
    api_base: str,
    api_key: str | None,
    output_dir: Path,
    timeout: float,
) -> dict[str, Any]:
    metrics_text = fetch_metrics_text(api_base=api_base, api_key=api_key, timeout=timeout)
    samples = parse_prometheus_text(metrics_text)
    summary = summarize_vllm_metrics(samples)
    write_snapshot(output_dir, metrics_text=metrics_text, samples=samples, summary=summary)
    return summary


def _write_poll_error(output_dir: Path, index: int, error: Exception) -> dict[str, Any]:
    error_dir = output_dir / "poll-snapshots" / f"{index:06d}"
    error_dir.mkdir(parents=True, exist_ok=True)
    error_path = error_dir / "metrics.error.json"
    payload = {"error": str(error), "type": type(error).__name__}
    error_path.write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return {"error_json": str(error_path), **payload}


def _poll_once(
    *,
    api_base: str,
    api_key: str | None,
    output_dir: Path,
    timeout: float,
    index: int,
) -> dict[str, Any]:
    now = time.time()
    metrics_text = fetch_metrics_text(api_base=api_base, api_key=api_key, timeout=timeout)
    samples = parse_prometheus_text(metrics_text)
    summary = summarize_vllm_metrics(samples)
    snapshot_dir = output_dir / "poll-snapshots" / f"{index:06d}"
    artifacts = write_snapshot(snapshot_dir, metrics_text=metrics_text, samples=samples, summary=summary)
    return {"timestamp": now, "index": index, "summary": summary, "artifacts": artifacts}


def _write_poll_summary(
    *,
    output_dir: Path,
    poll_path: Path,
    entries: list[dict[str, Any]],
    interval_seconds: float,
    duration_seconds: float | None,
) -> dict[str, Any]:
    successful = [entry for entry in entries if isinstance(entry.get("summary"), dict)]
    first_summary = successful[0]["summary"] if successful else None
    last_summary = successful[-1]["summary"] if successful else None
    summary_path = output_dir / "metrics.poll-summary.json"
    aggregate = {
        "poll_count": len(entries),
        "successful_poll_count": len(successful),
        "duration_seconds": duration_seconds,
        "interval_seconds": interval_seconds,
        "first_summary": first_summary,
        "last_summary": last_summary,
        "delta": diff_summaries(first_summary or {}, last_summary or {}),
        "poll_path": str(poll_path),
        "poll_summary_path": str(summary_path),
    }
    summary_path.write_text(json.dumps(aggregate, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return aggregate


def poll_metrics(
    *,
    api_base: str,
    api_key: str | None,
    output_dir: Path,
    interval_seconds: float,
    duration_seconds: float,
    timeout: float,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    poll_path = output_dir / "metrics.poll.jsonl"
    entries: list[dict[str, Any]] = []
    deadline = time.monotonic() + duration_seconds
    index = 0
    with poll_path.open("w", encoding="utf-8") as handle:
        while True:
            try:
                entry = _poll_once(
                    api_base=api_base,
                    api_key=api_key,
                    output_dir=output_dir,
                    timeout=timeout,
                    index=index,
                )
            except Exception as exc:
                entry = {"timestamp": time.time(), "index": index, **_write_poll_error(output_dir, index, exc)}
            handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
            handle.flush()
            entries.append(entry)
            index += 1
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                break
            time.sleep(min(interval_seconds, remaining))
    return _write_poll_summary(
        output_dir=output_dir,
        poll_path=poll_path,
        entries=entries,
        interval_seconds=interval_seconds,
        duration_seconds=duration_seconds,
    )


def poll_metrics_until_stopped(
    *,
    api_base: str,
    api_key: str | None,
    output_dir: Path,
    interval_seconds: float,
    timeout: float,
    stop_event: Any,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    poll_path = output_dir / "metrics.poll.jsonl"
    entries: list[dict[str, Any]] = []
    index = 0
    started = time.monotonic()
    with poll_path.open("w", encoding="utf-8") as handle:
        while True:
            try:
                entry = _poll_once(
                    api_base=api_base,
                    api_key=api_key,
                    output_dir=output_dir,
                    timeout=timeout,
                    index=index,
                )
            except Exception as exc:
                entry = {"timestamp": time.time(), "index": index, **_write_poll_error(output_dir, index, exc)}
            handle.write(json.dumps(entry, sort_keys=True, default=str) + "\n")
            handle.flush()
            entries.append(entry)
            index += 1
            if stop_event.wait(interval_seconds):
                break
    return _write_poll_summary(
        output_dir=output_dir,
        poll_path=poll_path,
        entries=entries,
        interval_seconds=interval_seconds,
        duration_seconds=time.monotonic() - started,
    )


def _float_or_none(value: object) -> float | None:
    if value is None:
        return None
    if isinstance(value, (int, float)):
        return float(value) if math.isfinite(float(value)) else None
    text = str(value).strip()
    if not text or text.lower() in {"n/a", "na", "nan", "none", "[not supported]"}:
        return None
    match = re.search(r"[-+]?\d+(?:\.\d+)?", text.replace(",", ""))
    return float(match.group(0)) if match else None


def _int_or_none(value: object) -> int | None:
    parsed = _float_or_none(value)
    return int(parsed) if parsed is not None else None


def _timestamp_iso(timestamp: float | None) -> str | None:
    if timestamp is None:
        return None
    return datetime.fromtimestamp(timestamp, timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _timestamp_or_none(value: object) -> float | None:
    if not isinstance(value, str):
        return _float_or_none(value)
    if not value.strip():
        return None
    raw_text = value.strip()
    if "T" in raw_text or "-" in raw_text:
        try:
            return datetime.fromisoformat(raw_text.replace("Z", "+00:00")).timestamp()
        except ValueError:
            return None
    return _float_or_none(raw_text)


def _get_any(mapping: Mapping[str, Any], names: tuple[str, ...]) -> Any:
    for name in names:
        if name in mapping:
            return mapping[name]
    return None


def _normalize_gpu_record(record: Mapping[str, Any]) -> dict[str, Any]:
    normalized = {
        "index": _int_or_none(_get_any(record, ("index", "gpu", "gpu_index"))),
        "uuid": _get_any(record, ("uuid", "gpu_uuid")),
        "name": _get_any(record, ("name", "gpu_name")),
        "utilization_gpu_percent": _float_or_none(
            _get_any(record, ("utilization_gpu_percent", "utilization.gpu", "utilization.gpu [%]"))
        ),
        "utilization_memory_percent": _float_or_none(
            _get_any(record, ("utilization_memory_percent", "utilization.memory", "utilization.memory [%]"))
        ),
        "memory_used_mib": _float_or_none(_get_any(record, ("memory_used_mib", "memory.used", "memory.used [MiB]"))),
        "memory_total_mib": _float_or_none(_get_any(record, ("memory_total_mib", "memory.total", "memory.total [MiB]"))),
        "power_draw_watts": _float_or_none(_get_any(record, ("power_draw_watts", "power.draw", "power.draw [W]"))),
        "temperature_gpu_c": _float_or_none(_get_any(record, ("temperature_gpu_c", "temperature.gpu", "temperature.gpu [C]"))),
        "clocks_sm_mhz": _float_or_none(_get_any(record, ("clocks_sm_mhz", "clocks.sm", "clocks.sm [MHz]"))),
        "clocks_mem_mhz": _float_or_none(_get_any(record, ("clocks_mem_mhz", "clocks.mem", "clocks.mem [MHz]"))),
    }
    return {key: value for key, value in normalized.items() if value is not None}


def parse_gpu_telemetry_jsonl(text: str) -> list[dict[str, Any]]:
    samples: list[dict[str, Any]] = []
    for line in text.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        entry = json.loads(stripped)
        if not isinstance(entry, dict):
            continue
        timestamp = _timestamp_or_none(entry.get("timestamp")) or _timestamp_or_none(entry.get("timestamp_iso"))
        raw_records = entry.get("gpus") or entry.get("records") or []
        records = raw_records if isinstance(raw_records, list) else []
        sample: dict[str, Any] = {
            "timestamp": timestamp,
            "timestamp_iso": entry.get("timestamp_iso") or _timestamp_iso(timestamp),
            "source": entry.get("source", "nvidia-smi"),
            "gpus": [_normalize_gpu_record(record) for record in records if isinstance(record, Mapping)],
        }
        if "error" in entry:
            sample["error"] = entry.get("error")
        samples.append(sample)
    return samples


def parse_gpu_telemetry_csv(text: str, *, timestamp: float | None = None) -> list[dict[str, Any]]:
    lines = [line for line in text.splitlines() if line.strip()]
    if not lines:
        return []
    reader = csv.reader(lines, skipinitialspace=True)
    rows = list(reader)
    first = [cell.strip() for cell in rows[0]]
    has_header = any(not re.fullmatch(r"[-+]?\d+(?:\.\d+)?", cell) for cell in first[:1]) and any(
        "utilization" in cell or "memory" in cell or cell == "index" for cell in first
    )
    fields = first if has_header else list(GPU_CSV_FIELDS)
    data_rows = rows[1:] if has_header else rows
    gpus = []
    for row in data_rows:
        record = {field: value.strip() for field, value in zip(fields, row)}
        gpus.append(_normalize_gpu_record(record))
    return [
        {
            "timestamp": timestamp,
            "timestamp_iso": _timestamp_iso(timestamp),
            "source": "nvidia-smi-csv",
            "gpus": gpus,
        }
    ]


def filter_gpu_telemetry_samples(
    samples: Iterable[dict[str, Any]],
    *,
    start_timestamp: float | None,
    end_timestamp: float | None,
) -> list[dict[str, Any]]:
    filtered: list[dict[str, Any]] = []
    for sample in samples:
        timestamp = _timestamp_or_none(sample.get("timestamp")) or _timestamp_or_none(sample.get("timestamp_iso"))
        if timestamp is None:
            if start_timestamp is None and end_timestamp is None:
                filtered.append(sample)
            continue
        if start_timestamp is not None and timestamp < start_timestamp:
            continue
        if end_timestamp is not None and timestamp > end_timestamp:
            continue
        copied = dict(sample)
        copied["timestamp"] = timestamp
        copied["timestamp_iso"] = copied.get("timestamp_iso") or _timestamp_iso(timestamp)
        filtered.append(copied)
    return filtered


def summarize_gpu_telemetry(samples: Iterable[dict[str, Any]]) -> dict[str, Any]:
    sample_list = list(samples)
    per_gpu: dict[str, dict[str, list[float]]] = {}
    gpu_sample_count = 0
    for sample in sample_list:
        for gpu in sample.get("gpus", []):
            if not isinstance(gpu, Mapping):
                continue
            gpu_sample_count += 1
            gpu_key = str(gpu.get("index", "unknown"))
            bucket = per_gpu.setdefault(gpu_key, {field: [] for field in GPU_NUMERIC_FIELDS})
            for field in GPU_NUMERIC_FIELDS:
                value = _float_or_none(gpu.get(field))
                if value is not None:
                    bucket[field].append(value)
    return {
        "sample_count": len(sample_list),
        "gpu_sample_count": gpu_sample_count,
        "per_gpu": {
            gpu_key: {field: _summary(values) for field, values in fields.items() if values}
            for gpu_key, fields in sorted(per_gpu.items())
        },
    }


def _load_gpu_telemetry_file(path: Path) -> list[dict[str, Any]]:
    text = path.read_text(encoding="utf-8")
    if path.suffix == ".jsonl" or text.lstrip().startswith("{"):
        samples = parse_gpu_telemetry_jsonl(text)
    else:
        samples = parse_gpu_telemetry_csv(text)
    for sample in samples:
        sample["source_path"] = str(path)
    return samples


def write_gpu_telemetry_artifacts(
    source_dir: Path,
    output_dir: Path,
    *,
    start_timestamp: float | None,
    end_timestamp: float | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    telemetry_path = output_dir / "gpu.telemetry.jsonl"
    summary_path = output_dir / "gpu.summary.json"
    source_files = [path for path in source_dir.rglob("*") if path.is_file()] if source_dir.exists() else []
    parsed: list[dict[str, Any]] = []
    errors: dict[str, str] = {}
    for path in source_files:
        if path.suffix not in {".jsonl", ".csv", ".txt"}:
            continue
        try:
            parsed.extend(_load_gpu_telemetry_file(path))
        except Exception as exc:
            errors[str(path)] = str(exc)
    filtered = filter_gpu_telemetry_samples(parsed, start_timestamp=start_timestamp, end_timestamp=end_timestamp)
    with telemetry_path.open("w", encoding="utf-8") as handle:
        for sample in filtered:
            handle.write(json.dumps(sample, sort_keys=True, default=str) + "\n")
    summary = summarize_gpu_telemetry(filtered)
    summary.update(
        {
            "source_dir": str(source_dir),
            "source_file_count": len(source_files),
            "telemetry_path": str(telemetry_path),
            "summary_path": str(summary_path),
            "errors": errors,
        }
    )
    summary_path.write_text(json.dumps(summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {
        "telemetry_jsonl": str(telemetry_path),
        "summary_json": str(summary_path),
        "source_dir": str(source_dir),
        "source_file_count": len(source_files),
        "sample_count": len(filtered),
        "summary": summary,
        "errors": errors,
    }


def _profile_file_kind(path: Path) -> str:
    name = path.name.lower()
    if "trace" in name and (name.endswith(".json") or name.endswith(".json.gz")):
        return "chrome_trace"
    if name.endswith(".sqlite") or name.endswith(".db"):
        return "sqlite"
    if name.endswith(".json") or name.endswith(".json.gz"):
        return "json"
    return "artifact"


def _read_json_or_gzip(path: Path) -> Any:
    if path.suffix == ".gz":
        with gzip.open(path, "rt", encoding="utf-8") as handle:
            return json.load(handle)
    return json.loads(path.read_text(encoding="utf-8"))


def _summarize_trace_events(path: Path) -> dict[str, Any] | None:
    if path.stat().st_size > 200 * 1024 * 1024:
        return {"path": str(path), "skipped": "file larger than 200MiB"}
    payload = _read_json_or_gzip(path)
    events = payload.get("traceEvents") if isinstance(payload, dict) else None
    if not isinstance(events, list):
        return None
    grouped: dict[str, dict[str, float | int]] = {}
    for event in events:
        if not isinstance(event, Mapping) or event.get("ph") != "X":
            continue
        duration = _float_or_none(event.get("dur"))
        if duration is None:
            continue
        name = str(event.get("name") or "unknown")
        category = str(event.get("cat") or "")
        marker = f"{category} {name}".lower()
        if "cuda" not in marker and "gpu" not in marker and "kernel" not in marker:
            continue
        current = grouped.setdefault(name, {"count": 0, "total_us": 0.0, "max_us": 0.0})
        current["count"] = int(current["count"]) + 1
        current["total_us"] = float(current["total_us"]) + duration
        current["max_us"] = max(float(current["max_us"]), duration)
    top = sorted(grouped.items(), key=lambda item: float(item[1]["total_us"]), reverse=True)[:25]
    return {
        "path": str(path),
        "top_cuda_events": [{"name": name, **values} for name, values in top],
    }


def write_kernel_profile_artifacts(
    source_dir: Path,
    output_dir: Path,
    *,
    start_timestamp: float | None,
    end_timestamp: float | None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    index_path = output_dir / "profile-index.json"
    cuda_summary_path = output_dir / "cuda-summary.json"
    files = [path for path in source_dir.rglob("*") if path.is_file()] if source_dir.exists() else []
    windowed = []
    for path in files:
        modified = path.stat().st_mtime
        if start_timestamp is not None and modified < start_timestamp - 300:
            continue
        if end_timestamp is not None and modified > end_timestamp + 600:
            continue
        windowed.append(path)
    selected = windowed or files
    entries = [
        {
            "path": str(path),
            "relative_path": str(path.relative_to(source_dir)) if source_dir.exists() else path.name,
            "size_bytes": path.stat().st_size,
            "modified_timestamp": path.stat().st_mtime,
            "kind": _profile_file_kind(path),
        }
        for path in sorted(selected)
    ]
    errors: dict[str, str] = {}
    trace_summaries = []
    for path in sorted(selected):
        if _profile_file_kind(path) not in {"chrome_trace", "json"}:
            continue
        try:
            summary = _summarize_trace_events(path)
            if summary:
                trace_summaries.append(summary)
        except Exception as exc:
            errors[str(path)] = str(exc)
    index = {
        "source_dir": str(source_dir),
        "artifact_count": len(entries),
        "artifacts": entries,
        "index_path": str(index_path),
        "cuda_summary_path": str(cuda_summary_path),
        "errors": errors,
    }
    index_path.write_text(json.dumps(index, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    cuda_summary = {"trace_file_count": len(trace_summaries), "trace_summaries": trace_summaries, "errors": errors}
    cuda_summary_path.write_text(json.dumps(cuda_summary, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    return {
        "index_json": str(index_path),
        "cuda_summary_json": str(cuda_summary_path),
        "source_dir": str(source_dir),
        "artifact_count": len(entries),
        "errors": errors,
    }


def write_metrics_manifest(
    output_dir: Path,
    *,
    snapshots: Mapping[str, Path],
    poll: dict[str, Any] | None = None,
    delta: dict[str, Any] | None = None,
    gpu: dict[str, Any] | None = None,
    kernel: dict[str, Any] | None = None,
    profiling: dict[str, Any] | None = None,
    errors: Mapping[str, str] | None = None,
) -> dict[str, Any]:
    output_dir.mkdir(parents=True, exist_ok=True)
    manifest_path = output_dir / "metrics-manifest.json"
    snapshot_artifacts = {name: snapshot_artifact_paths(path) for name, path in snapshots.items()}
    inference = {"snapshots": snapshot_artifacts, "poll": poll, "delta": delta}
    manifest = {
        "manifest_version": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "inference": inference,
        "gpu": gpu or {},
        "kernel": kernel or {},
        "profiling": profiling or {},
        "snapshots": snapshot_artifacts,
        "poll": poll,
        "delta": delta,
        "errors": dict(errors or {}),
    }
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True, default=str) + "\n", encoding="utf-8")
    manifest["manifest_path"] = str(manifest_path)
    return manifest


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--api-base", default=clean_env_value(os.getenv("VLLM_API_BASE")))
    parser.add_argument("--api-key", default=clean_env_value(os.getenv("VLLM_API_KEY")))
    parser.add_argument("--timeout", type=float, default=30.0)
    subparsers = parser.add_subparsers(dest="command", required=True)

    snapshot = subparsers.add_parser("snapshot", help="Fetch one /metrics snapshot.")
    snapshot.add_argument("--output-dir", type=Path)

    poll = subparsers.add_parser("poll", help="Poll /metrics for a fixed duration.")
    poll.add_argument("--output-dir", type=Path)
    poll.add_argument("--interval-seconds", type=float, default=15.0)
    poll.add_argument("--duration-seconds", type=float, default=300.0)
    return parser


def _preparse_env_file(argv: list[str] | None) -> Path:
    parser = argparse.ArgumentParser(add_help=False)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    args, _ = parser.parse_known_args(argv)
    return args.env_file


def main(argv: list[str] | None = None) -> int:
    load_project_env(_preparse_env_file(argv))
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        api_base = args.api_base or require_env("VLLM_API_BASE")
        api_key = args.api_key or clean_env_value(os.getenv("VLLM_API_KEY"))
        if args.command == "snapshot":
            output_dir = args.output_dir or project_root() / "runs" / "vllm-metrics" / _timestamp()
            summary = snapshot_metrics(api_base=api_base, api_key=api_key, output_dir=output_dir, timeout=args.timeout)
            write_metrics_manifest(output_dir, snapshots={"snapshot": output_dir})
            print(json.dumps({"output_dir": str(output_dir), "summary": summary}, indent=2, default=str))
            return 0
        if args.command == "poll":
            output_dir = args.output_dir or project_root() / "runs" / "vllm-metrics" / _timestamp()
            aggregate = poll_metrics(
                api_base=api_base,
                api_key=api_key,
                output_dir=output_dir,
                interval_seconds=args.interval_seconds,
                duration_seconds=args.duration_seconds,
                timeout=args.timeout,
            )
            write_metrics_manifest(output_dir, snapshots={}, poll=aggregate)
            print(json.dumps({"output_dir": str(output_dir), "poll": aggregate}, indent=2, default=str))
            return 0
    except Exception as exc:
        return fail(str(exc))
    parser.error(f"Unknown command: {args.command}")
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
