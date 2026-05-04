import importlib.util
import sys
from pathlib import Path

import pytest

PROJECT_ROOT = Path(__file__).resolve().parents[1]
MINI_AGENT_DIR = PROJECT_ROOT / "evmbench" / "agents" / "mini-swe-agent"
SCRIPT_PATH = MINI_AGENT_DIR / "deploy_vllm_server.py"


def load_deploy_module():
    sys.path.insert(0, str(MINI_AGENT_DIR))
    spec = importlib.util.spec_from_file_location("deploy_vllm_server", SCRIPT_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["deploy_vllm_server"] = module
    spec.loader.exec_module(module)
    return module


deploy = load_deploy_module()


def test_default_gpu_uses_h100_fp8_profile(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in (
        "VLLM_MODAL_GPU",
        "VLLM_MODEL",
        "VLLM_SERVED_MODEL_NAME",
        "VLLM_TENSOR_PARALLEL_SIZE",
        "VLLM_MAX_NUM_SEQS",
        "VLLM_DTYPE",
        "VLLM_STARTUP_TIMEOUT_SECONDS",
        "VLLM_SCALEDOWN_WINDOW_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    parser = deploy.build_arg_parser()
    args = parser.parse_args([])
    config = deploy._resolve_server_config(args, [])

    assert config.gpu == "H100:1"
    assert config.model == deploy.FP8_MODEL
    assert config.served_model_name == deploy.FP8_MODEL
    assert config.dtype == "auto"
    assert config.max_num_seqs == "8"
    assert config.startup_timeout_seconds == 600
    assert config.scaledown_window_seconds == 60


def test_expensive_gpu_guard_blocks_b200_without_opt_in() -> None:
    with pytest.raises(RuntimeError, match="--allow-expensive-gpu"):
        deploy._require_expensive_gpu_opt_in("B200", allow_expensive_gpu=False)


def test_expensive_gpu_guard_blocks_multi_gpu_without_opt_in() -> None:
    with pytest.raises(RuntimeError, match="multi-GPU|expensive Modal GPU"):
        deploy._require_expensive_gpu_opt_in("A100-80GB:2", allow_expensive_gpu=False)


def test_deploy_env_does_not_leak_expensive_gpu_opt_in(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("VLLM_ALLOW_EXPENSIVE_GPU", "1")
    config = deploy.VLLMServerConfig(
        gpu="H100:1",
        model=deploy.FP8_MODEL,
        served_model_name=deploy.FP8_MODEL,
        tensor_parallel_size="1",
        max_model_len="32768",
        max_num_seqs="8",
        gpu_memory_utilization="0.90",
        dtype="auto",
        enable_mtp=True,
        num_speculative_tokens="2",
        tool_call_parser="qwen3_coder",
        fast_boot=False,
        startup_timeout_seconds=600,
        scaledown_window_seconds=60,
    )

    env = deploy._deploy_env(
        config,
        api_key="test-key",
        hf_token="",
        allow_expensive_gpu=False,
    )

    assert "VLLM_ALLOW_EXPENSIVE_GPU" not in env
    assert env["VLLM_STARTUP_TIMEOUT_SECONDS"] == "600"
    assert env["VLLM_SCALEDOWN_WINDOW_SECONDS"] == "60"


def test_deploy_env_sets_expensive_gpu_opt_in_when_allowed() -> None:
    config = deploy.VLLMServerConfig(
        gpu="H100:2",
        model=deploy.FP8_MODEL,
        served_model_name=deploy.FP8_MODEL,
        tensor_parallel_size="2",
        max_model_len="65536",
        max_num_seqs="2",
        gpu_memory_utilization="0.94",
        dtype="auto",
        enable_mtp=False,
        num_speculative_tokens="2",
        tool_call_parser="qwen3_coder",
        fast_boot=False,
        startup_timeout_seconds=1200,
        scaledown_window_seconds=1800,
    )

    env = deploy._deploy_env(
        config,
        api_key="test-key",
        hf_token="",
        allow_expensive_gpu=True,
    )

    assert env["VLLM_ALLOW_EXPENSIVE_GPU"] == "1"
    assert env["VLLM_MODAL_GPU"] == "H100:2"
