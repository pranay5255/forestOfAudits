import json
import math
from pathlib import Path

from evmbench.vllm import metrics


PROMETHEUS_TEXT = """# HELP vllm:num_requests_running running requests
# TYPE vllm:num_requests_running gauge
vllm:num_requests_running{model_name="Qwen/Qwen3.6-35B-A3B-FP8"} 2
vllm:num_requests_waiting 3
vllm:gpu_cache_usage_perc 0.5
vllm:prefix_cache_queries_total 10
vllm:prefix_cache_hits_total 4
vllm:request_prompt_tokens_total{model_name="Qwen/Qwen3.6-35B-A3B-FP8"} 100
vllm:request_generation_tokens_total 50
vllm:request_success_total{finished_reason="stop"} 7
vllm:time_to_first_token_seconds_bucket{le="0.1"} 1
vllm:time_to_first_token_seconds_bucket{le="0.5"} 3
vllm:time_to_first_token_seconds_bucket{le="+Inf"} 4
vllm:time_to_first_token_seconds_count 4
vllm:time_to_first_token_seconds_sum 1.0
vllm:custom_future_metric{escaped="a\\\"b"} +Inf
process_cpu_seconds_total 123
"""


def test_parse_prometheus_text_preserves_vllm_samples_and_labels() -> None:
    samples = metrics.parse_prometheus_text(PROMETHEUS_TEXT)

    assert any(sample.name == "process_cpu_seconds_total" for sample in samples)
    custom = next(sample for sample in samples if sample.name == "vllm:custom_future_metric")
    assert custom.labels == {"escaped": 'a"b'}
    assert math.isinf(custom.value)

    summary = metrics.summarize_vllm_metrics(samples)

    assert "vllm:custom_future_metric" in summary["vllm_metric_names"]
    assert any(sample["name"] == "vllm:custom_future_metric" for sample in summary["vllm_samples"])
    assert summary["request_queue"]["vllm:num_requests_running"]["max"] == 2
    assert summary["cache_usage"]["vllm:gpu_cache_usage_perc"]["mean"] == 0.5
    assert summary["token_counters"]["vllm:request_prompt_tokens_total"] == 100
    assert summary["counters"]["vllm:request_success_total"] == 7
    assert summary["prefix_cache_hit_rate"] == 0.4

    ttft = summary["latency_histograms"]["vllm:time_to_first_token_seconds"]
    assert ttft["count"] == 4
    assert ttft["sum"] == 1.0
    assert ttft["mean"] == 0.25
    assert ttft["percentiles"]["p50"] == 0.5
    assert math.isinf(ttft["percentiles"]["p90"])


def test_diff_summaries_computes_counter_and_histogram_windows() -> None:
    before = metrics.summarize_vllm_metrics(
        metrics.parse_prometheus_text(
            """
vllm:prefix_cache_queries_total 5
vllm:prefix_cache_hits_total 2
vllm:time_to_first_token_seconds_count 1
vllm:time_to_first_token_seconds_sum 0.2
"""
        )
    )
    after = metrics.summarize_vllm_metrics(metrics.parse_prometheus_text(PROMETHEUS_TEXT))

    delta = metrics.diff_summaries(before, after)

    assert delta["counter_delta"]["vllm:prefix_cache_queries_total"] == 5
    assert delta["counter_delta"]["vllm:prefix_cache_hits_total"] == 2
    assert delta["prefix_cache_hit_rate_delta_window"] == 0.4
    ttft_delta = delta["histogram_delta"]["vllm:time_to_first_token_seconds"]
    assert ttft_delta["count_delta"] == 3
    assert ttft_delta["sum_delta"] == 0.8
    assert ttft_delta["mean_delta_window"] == 0.8 / 3


def test_poll_metrics_writes_raw_prometheus_and_parsed_artifacts(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(metrics, "fetch_metrics_text", lambda **_: PROMETHEUS_TEXT)

    aggregate = metrics.poll_metrics(
        api_base="https://vllm.example.test/v1",
        api_key="test-key",
        output_dir=tmp_path,
        interval_seconds=0.01,
        duration_seconds=0,
        timeout=1,
    )

    poll_path = Path(aggregate["poll_path"])
    assert poll_path.exists()
    entries = [json.loads(line) for line in poll_path.read_text(encoding="utf-8").splitlines()]
    assert len(entries) == 1
    artifacts = entries[0]["artifacts"]
    assert Path(artifacts["raw_prometheus"]).read_text(encoding="utf-8") == PROMETHEUS_TEXT
    parsed = json.loads(Path(artifacts["samples_json"]).read_text(encoding="utf-8"))
    assert any(sample["name"] == "vllm:custom_future_metric" for sample in parsed)
    assert Path(artifacts["summary_json"]).exists()
    assert aggregate["successful_poll_count"] == 1



def test_gpu_telemetry_parses_jsonl_and_csv_samples() -> None:
    jsonl = json.dumps(
        {
            "timestamp": 100.0,
            "source": "nvidia-smi",
            "gpus": [
                {
                    "index": "0",
                    "uuid": "GPU-0",
                    "name": "H100",
                    "utilization.gpu": "55",
                    "utilization.memory": "12",
                    "memory.used": "2048",
                    "memory.total": "81559",
                    "power.draw": "320.5",
                    "temperature.gpu": "61",
                    "clocks.sm": "1500",
                    "clocks.mem": "2600",
                }
            ],
        }
    )
    samples = metrics.parse_gpu_telemetry_jsonl(jsonl + "\n")

    assert samples[0]["timestamp"] == 100.0
    assert samples[0]["gpus"][0]["utilization_gpu_percent"] == 55
    assert samples[0]["gpus"][0]["memory_used_mib"] == 2048

    csv_text = (
        "index, uuid, name, utilization.gpu [%], utilization.memory [%], memory.used [MiB], "
        "memory.total [MiB], power.draw [W], temperature.gpu, clocks.sm [MHz], clocks.mem [MHz]\n"
        "1, GPU-1, H100, 65, 18, 4096, 81559, 350.0, 64, 1515, 2600\n"
    )
    csv_samples = metrics.parse_gpu_telemetry_csv(csv_text, timestamp=101.0)
    assert csv_samples[0]["gpus"][0]["index"] == 1
    assert csv_samples[0]["gpus"][0]["power_draw_watts"] == 350.0

    combined = samples + csv_samples
    filtered = metrics.filter_gpu_telemetry_samples(combined, start_timestamp=99.0, end_timestamp=100.5)
    summary = metrics.summarize_gpu_telemetry(filtered)
    assert len(filtered) == 1
    assert summary["per_gpu"]["0"]["utilization_gpu_percent"]["max"] == 55


def test_gpu_and_kernel_artifact_writers(tmp_path: Path) -> None:
    gpu_raw = tmp_path / "raw-gpu"
    gpu_raw.mkdir()
    (gpu_raw / "gpu.telemetry.jsonl").write_text(
        json.dumps(
            {
                "timestamp": 100.0,
                "gpus": [{"index": "0", "utilization.gpu": "70", "memory.used": "1024"}],
            }
        )
        + "\n",
        encoding="utf-8",
    )

    gpu_artifacts = metrics.write_gpu_telemetry_artifacts(
        gpu_raw,
        tmp_path / "gpu-out",
        start_timestamp=99.0,
        end_timestamp=101.0,
    )
    assert Path(gpu_artifacts["telemetry_jsonl"]).exists()
    assert gpu_artifacts["summary"]["per_gpu"]["0"]["memory_used_mib"]["max"] == 1024

    torch_raw = tmp_path / "raw-torch"
    torch_raw.mkdir()
    (torch_raw / "worker.trace.json").write_text(
        json.dumps(
            {
                "traceEvents": [
                    {"ph": "X", "cat": "cuda", "name": "cudaLaunchKernel", "dur": 25},
                    {"ph": "X", "cat": "cpu", "name": "aten::add", "dur": 5},
                ]
            }
        ),
        encoding="utf-8",
    )
    kernel_artifacts = metrics.write_kernel_profile_artifacts(
        torch_raw,
        tmp_path / "kernel-out",
        start_timestamp=None,
        end_timestamp=None,
    )
    assert kernel_artifacts["artifact_count"] == 1
    cuda_summary = json.loads(Path(kernel_artifacts["cuda_summary_json"]).read_text(encoding="utf-8"))
    assert cuda_summary["trace_summaries"][0]["top_cuda_events"][0]["name"] == "cudaLaunchKernel"


def test_metrics_manifest_links_inference_gpu_and_kernel_sections(tmp_path: Path) -> None:
    manifest = metrics.write_metrics_manifest(
        tmp_path,
        snapshots={"before": tmp_path / "before"},
        poll={"poll_path": "poll.jsonl"},
        delta={"counter_delta": {"vllm:request_success_total": 2}},
        gpu={"telemetry_jsonl": "metrics/gpu/gpu.telemetry.jsonl"},
        kernel={"index_json": "metrics/kernel/torch/profile-index.json"},
        profiling={"kernel_profile": "torch"},
        errors={"torch_profile_volume_get": "missing remote path"},
    )

    assert manifest["inference"]["snapshots"]["before"]["raw_prometheus"].endswith("metrics.prom")
    assert manifest["gpu"]["telemetry_jsonl"] == "metrics/gpu/gpu.telemetry.jsonl"
    assert manifest["kernel"]["index_json"] == "metrics/kernel/torch/profile-index.json"
    assert manifest["profiling"]["kernel_profile"] == "torch"
    assert manifest["errors"]["torch_profile_volume_get"] == "missing remote path"
