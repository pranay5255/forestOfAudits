import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

PROJECT_ROOT = Path(__file__).resolve().parents[1]
RUNNER_PATH = PROJECT_ROOT / "evmbench" / "agents" / "openrouter-v1" / "run_openrouter_v1.py"
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def load_runner_module() -> ModuleType:
    spec = importlib.util.spec_from_file_location("run_openrouter_v1", RUNNER_PATH)
    assert spec is not None
    assert spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["run_openrouter_v1"] = module
    spec.loader.exec_module(module)
    return module


runner = load_runner_module()


def test_default_provider_preserves_openrouter_env_and_base_url(tmp_path: Path) -> None:
    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["codex"]],
        models=["openai/gpt-5.2"],
        base_url=None,
    )

    item = matrix[0]

    assert item.provider == "openrouter"
    assert item.base_url == "https://openrouter.ai/api/v1"
    assert item.api_key_env_var == "OPENROUTER_API_KEY"
    assert item.env["EVMBENCH_LLM_PROVIDER"] == "openrouter"
    assert item.env["EVMBENCH_LLM_MODEL"] == "openai/gpt-5.2"
    assert item.env["EVMBENCH_LLM_BASE_URL"] == "https://openrouter.ai/api/v1"
    assert item.env["EVMBENCH_LLM_API_KEY_ENV"] == "OPENROUTER_API_KEY"
    assert item.env["EVMBENCH_OPENROUTER_MODEL"] == "openai/gpt-5.2"
    assert item.env["EVMBENCH_OPENROUTER_BASE_URL"] == "https://openrouter.ai/api/v1"


def test_openai_provider_uses_openai_env_and_base_url(tmp_path: Path) -> None:
    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["opencode"]],
        models=["gpt-5.2"],
        provider="openai",
        base_url=None,
    )

    item = matrix[0]

    assert item.provider == "openai"
    assert item.base_url == "https://api.openai.com/v1"
    assert item.api_key_env_var == "OPENAI_API_KEY"
    assert item.env["EVMBENCH_LLM_PROVIDER"] == "openai"
    assert item.env["EVMBENCH_LLM_MODEL"] == "gpt-5.2"
    assert item.env["EVMBENCH_LLM_BASE_URL"] == "https://api.openai.com/v1"
    assert item.env["EVMBENCH_LLM_API_KEY_ENV"] == "OPENAI_API_KEY"
    assert "EVMBENCH_OPENROUTER_MODEL" not in item.env
    assert "EVMBENCH_OPENROUTER_BASE_URL" not in item.env


def test_plan_output_shows_openai_provider_env_and_base_url(tmp_path: Path, capsys) -> None:
    status = runner.main(
        [
            "plan",
            "--provider",
            "openai",
            "--tasks",
            "detect:2024-01-canto",
            "--harnesses",
            "codex",
            "--model",
            "gpt-5.2",
            "--output-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert status == 0
    assert "provider=openai" in captured.out
    assert "EVMBENCH_LLM_PROVIDER=openai" in captured.out
    assert "EVMBENCH_LLM_MODEL=gpt-5.2" in captured.out
    assert "EVMBENCH_LLM_BASE_URL=https://api.openai.com/v1" in captured.out
    assert "EVMBENCH_LLM_API_KEY_ENV=OPENAI_API_KEY" in captured.out


def test_codex_openai_start_config_includes_provider_name(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_codex = fake_bin / "codex"
    fake_codex.write_text(
        "#!/usr/bin/env bash\n"
        "printf '%s\\n' \"$@\" > \"$LOGS_DIR/codex-args.txt\"\n"
        "printf '{\"type\":\"message\",\"content\":\"smoke_ok\"}\\n'\n",
        encoding="utf-8",
    )
    fake_codex.chmod(0o755)

    workspace = tmp_path / "workspace"
    agent_dir = workspace / "agent"
    audit_dir = agent_dir / "audit"
    logs_dir = workspace / "logs"
    audit_dir.mkdir(parents=True)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "WORKSPACE_BASE": str(workspace),
            "AGENT_DIR": str(agent_dir),
            "AUDIT_DIR": str(audit_dir),
            "LOGS_DIR": str(logs_dir),
            "EVMBENCH_LLM_PROVIDER": "openai",
            "EVMBENCH_LLM_MODEL": "gpt-5-nano",
            "EVMBENCH_LLM_BASE_URL": "https://api.openai.com/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "OPENAI_API_KEY",
            "OPENAI_API_KEY": "test-key",
            "REASONING_EFFORT": "high",
        }
    )

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/openrouter-v1/codex-start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    args = (logs_dir / "codex-args.txt").read_text(encoding="utf-8")
    assert 'model_provider="openai"' in args
    assert 'model_providers.openai.name="OpenAI"' in args
    assert 'model_providers.openai.base_url="https://api.openai.com/v1"' in args
    assert 'model_providers.openai.env_key="OPENAI_API_KEY"' in args
    assert 'model_providers.openai.wire_api="responses"' in args


def test_opencode_openai_start_uses_builtin_provider_for_responses(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text(
        "#!/bin/sh\n"
        "printf 'opencode 1.1.26\\n'\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)

    workspace = tmp_path / "workspace"
    agent_dir = workspace / "agent"
    audit_dir = agent_dir / "audit"
    logs_dir = workspace / "logs"
    audit_dir.mkdir(parents=True)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_bin}:{env['PATH']}",
            "WORKSPACE_BASE": str(workspace),
            "AGENT_DIR": str(agent_dir),
            "AUDIT_DIR": str(audit_dir),
            "LOGS_DIR": str(logs_dir),
            "EVMBENCH_LLM_PROVIDER": "openai",
            "EVMBENCH_LLM_MODEL": "gpt-5-nano",
            "EVMBENCH_LLM_BASE_URL": "https://api.openai.com/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "OPENAI_API_KEY",
            "OPENAI_API_KEY": "test-key",
            "OPENCODE_DRY_RUN": "1",
        }
    )

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/openrouter-v1/opencode-start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    assert "validated openai/gpt-5-nano" in completed.stdout
    config = json.loads((agent_dir / "opencode.json").read_text(encoding="utf-8"))
    provider = config["provider"]["openai"]
    assert provider.get("npm") != "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "{env:EVMBENCH_LLM_BASE_URL}"
    assert provider["options"]["apiKey"] == "{env:OPENAI_API_KEY}"
    assert provider["models"]["gpt-5-nano"]["options"]["reasoningEffort"] == "high"
    submission = agent_dir / "submission" / "audit.md"
    assert submission.exists()
