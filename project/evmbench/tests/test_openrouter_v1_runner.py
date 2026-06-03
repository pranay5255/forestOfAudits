import importlib.util
import json
import os
import subprocess
import sys
from pathlib import Path
from types import ModuleType

import pytest

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


def test_opencode_gpt54_defaults_use_large_timeouts(tmp_path: Path) -> None:
    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["opencode"]],
        models=["gpt-5.4"],
        provider="openai",
        base_url=None,
    )

    item = matrix[0]

    assert "evmbench.solver.timeout=7800" in item.command
    assert item.env["EVMBENCH_OPENROUTER_AGENT_TIMEOUT_SECONDS"] == "7200"
    assert runner.default_item_timeout_seconds(matrix) == 10800
    runner.validate_item_timeout_seconds(matrix, 10800)


def test_opencode_short_timeout_requires_explicit_override(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="below the 3600s minimum"):
        runner.build_run_matrix(
            output_root=tmp_path,
            tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
            harnesses=[runner.HARNESS_SPECS["opencode"]],
            models=["gpt-5.4"],
            provider="openai",
            base_url=None,
            agent_timeout_seconds=1800,
        )

    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["opencode"]],
        models=["gpt-5.4"],
        provider="openai",
        base_url=None,
        agent_timeout_seconds=1800,
        allow_short_opencode_timeout=True,
    )
    assert matrix[0].env["EVMBENCH_OPENROUTER_AGENT_TIMEOUT_SECONDS"] == "1200"


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



def test_vllm_provider_uses_vllm_env_and_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("VLLM_API_BASE", "https://vllm.example.test/v1")
    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["codex"]],
        models=["Qwen/Qwen3.6-27B"],
        provider="vllm",
        base_url=None,
    )

    item = matrix[0]

    assert item.provider == "vllm"
    assert item.base_url == "https://vllm.example.test/v1"
    assert item.api_key_env_var == "VLLM_API_KEY"
    assert item.env["EVMBENCH_LLM_PROVIDER"] == "vllm"
    assert item.env["EVMBENCH_LLM_MODEL"] == "Qwen/Qwen3.6-27B"
    assert item.env["EVMBENCH_LLM_BASE_URL"] == "https://vllm.example.test/v1"
    assert item.env["EVMBENCH_LLM_API_KEY_ENV"] == "VLLM_API_KEY"
    assert item.env["VLLM_API_BASE"] == "https://vllm.example.test/v1"


def test_azure_foundry_provider_uses_azure_env_and_base_url(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setenv("AZURE_FOUNDRY_BASE_URL", "https://unit.openai.azure.com/openai/v1")
    matrix = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["opencode"]],
        models=["gpt-4.1-nano"],
        provider="azure-foundry",
        base_url=None,
    )

    item = matrix[0]

    assert item.provider == "azure-foundry"
    assert item.base_url == "https://unit.openai.azure.com/openai/v1"
    assert item.api_key_env_var == "AZURE_FOUNDRY_API_KEY"
    assert item.env["EVMBENCH_LLM_PROVIDER"] == "azure-foundry"
    assert item.env["EVMBENCH_LLM_MODEL"] == "gpt-4.1-nano"
    assert item.env["EVMBENCH_LLM_BASE_URL"] == "https://unit.openai.azure.com/openai/v1"
    assert item.env["EVMBENCH_LLM_API_KEY_ENV"] == "AZURE_FOUNDRY_API_KEY"
    assert item.env["AZURE_FOUNDRY_BASE_URL"] == "https://unit.openai.azure.com/openai/v1"
    assert item.env["OPENAI_BASE_URL"] == "https://unit.openai.azure.com/openai/v1"
    assert "evmbench.solver.judge_model=gpt-4.1-nano" in item.command


def test_azure_foundry_loads_env_file_aliases(tmp_path: Path, monkeypatch) -> None:
    for name in (
        "API_KEY",
        "PROJ_ENPOINT",
        "PROJ_ENDPOINT",
        "BASE_ENDPOINT",
        "AZURE_FOUNDRY_API_KEY",
        "AZURE_FOUNDRY_BASE_URL",
        "AZURE_FOUNDRY_PROJECT_ENDPOINT",
        "OPENROUTER_V1_AGENT_TIMEOUT_SECONDS",
    ):
        monkeypatch.delenv(name, raising=False)

    (tmp_path / ".env").write_text("OPENROUTER_V1_AGENT_TIMEOUT_SECONDS=123\n", encoding="utf-8")
    (tmp_path / ".env.azure").write_text(
        "API_KEY=unit-azure-key\n"
        "PROJ_ENPOINT=https://unit.openai.azure.com/openai/v1\n"
        "BASE_ENDPOINT=https://unit.services.ai.azure.com/api/projects/project-id\n",
        encoding="utf-8",
    )

    runner.load_provider_environment("azure-foundry", root=tmp_path)

    assert os.environ["OPENROUTER_V1_AGENT_TIMEOUT_SECONDS"] == "123"
    assert os.environ["AZURE_FOUNDRY_API_KEY"] == "unit-azure-key"
    assert os.environ["AZURE_FOUNDRY_BASE_URL"] == "https://unit.openai.azure.com/openai/v1"
    assert os.environ["AZURE_FOUNDRY_PROJECT_ENDPOINT"] == "https://unit.services.ai.azure.com/api/projects/project-id"
    assert runner.normalize_provider_base_url("azure-foundry", None) == "https://unit.openai.azure.com/openai/v1"


def test_azure_foundry_plan_defaults_to_smoke_model(tmp_path: Path, monkeypatch, capsys) -> None:
    monkeypatch.setenv("AZURE_FOUNDRY_BASE_URL", "https://unit.openai.azure.com/openai/v1")
    monkeypatch.setattr(runner, "PROJECT_ROOT", tmp_path)

    status = runner.main(
        [
            "plan",
            "--provider",
            "azure-foundry",
            "--tasks",
            "detect:2024-01-canto",
            "--harnesses",
            "codex",
            "--output-root",
            str(tmp_path),
        ]
    )

    captured = capsys.readouterr()

    assert status == 0
    assert "provider=azure-foundry" in captured.out
    assert "EVMBENCH_LLM_PROVIDER=azure-foundry" in captured.out
    assert "EVMBENCH_LLM_MODEL=gpt-4.1-nano" in captured.out
    assert "EVMBENCH_LLM_BASE_URL=https://unit.openai.azure.com/openai/v1" in captured.out


def test_codex_vllm_start_config_includes_provider_name(tmp_path: Path) -> None:
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
            "EVMBENCH_LLM_PROVIDER": "vllm",
            "EVMBENCH_LLM_MODEL": "Qwen/Qwen3.6-27B",
            "EVMBENCH_LLM_BASE_URL": "https://vllm.example.test/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "VLLM_API_KEY",
            "VLLM_API_KEY": "vllm-key",
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
    assert 'model_provider="vllm"' in args
    assert 'model_providers.vllm.name="EVMBench vLLM"' in args
    assert 'model_providers.vllm.base_url="https://vllm.example.test/v1"' in args
    assert 'model_providers.vllm.env_key="VLLM_API_KEY"' in args
    assert 'model_providers.vllm.wire_api="responses"' in args


def test_codex_azure_foundry_start_config_includes_provider_name(tmp_path: Path) -> None:
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
            "EVMBENCH_LLM_PROVIDER": "azure-foundry",
            "EVMBENCH_LLM_MODEL": "gpt-4.1-nano",
            "EVMBENCH_LLM_BASE_URL": "https://unit.openai.azure.com/openai/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "AZURE_FOUNDRY_API_KEY",
            "AZURE_FOUNDRY_API_KEY": "azure-key",
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
    assert 'model_provider="azure-foundry"' in args
    assert 'model_providers.azure-foundry.name="Azure Foundry"' in args
    assert 'model_providers.azure-foundry.base_url="https://unit.openai.azure.com/openai/v1"' in args
    assert 'model_providers.azure-foundry.env_key="AZURE_FOUNDRY_API_KEY"' in args
    assert 'model_providers.azure-foundry.wire_api="responses"' in args


def test_opencode_vllm_start_uses_openai_compatible_provider(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/bin/sh\nprintf 'opencode 1.1.26\\n'\n", encoding="utf-8")
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
            "EVMBENCH_LLM_PROVIDER": "vllm",
            "EVMBENCH_LLM_MODEL": "Qwen/Qwen3.6-27B",
            "EVMBENCH_LLM_BASE_URL": "https://vllm.example.test/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "VLLM_API_KEY",
            "VLLM_API_KEY": "vllm-key",
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
    assert "validated vllm/Qwen/Qwen3.6-27B" in completed.stdout
    config = json.loads((agent_dir / "opencode.json").read_text(encoding="utf-8"))
    provider = config["provider"]["vllm"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "{env:EVMBENCH_LLM_BASE_URL}"
    assert provider["options"]["apiKey"] == "{env:VLLM_API_KEY}"
    assert provider["models"]["Qwen/Qwen3.6-27B"]["limit"] == {"context": 32000, "output": 1024, "input": 30976}
    assert config["compaction"] == {"auto": True, "prune": True, "reserved": 4096}


def test_opencode_azure_foundry_start_uses_openai_compatible_provider(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/bin/sh\nprintf 'opencode 1.1.26\\n'\n", encoding="utf-8")
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
            "EVMBENCH_LLM_PROVIDER": "azure-foundry",
            "EVMBENCH_LLM_MODEL": "gpt-4.1-nano",
            "EVMBENCH_LLM_BASE_URL": "https://unit.openai.azure.com/openai/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "AZURE_FOUNDRY_API_KEY",
            "AZURE_FOUNDRY_API_KEY": "azure-key",
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
    assert "validated azure-foundry/gpt-4.1-nano" in completed.stdout
    config = json.loads((agent_dir / "opencode.json").read_text(encoding="utf-8"))
    provider = config["provider"]["azure-foundry"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "{env:EVMBENCH_LLM_BASE_URL}"
    assert provider["options"]["apiKey"] == "{env:AZURE_FOUNDRY_API_KEY}"
    assert provider["models"]["gpt-4.1-nano"] == {}
    assert "compaction" not in config


def test_opencode_azure_foundry_non_dry_run_uses_local_compat_proxy(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text(
        "#!/bin/sh\n"
        "printf '%s\\n' \"$EVMBENCH_LLM_BASE_URL\" > \"$LOGS_DIR/opencode-base-url.txt\"\n"
        "printf '%s\\n' \"$OPENCODE_AZURE_UPSTREAM_BASE_URL\" > \"$LOGS_DIR/opencode-upstream-base-url.txt\"\n"
        "printf '%s\\n' \"$OPENCODE_EXPERIMENTAL_OUTPUT_TOKEN_MAX\" > \"$LOGS_DIR/opencode-output-cap.txt\"\n"
        "printf '{\"type\":\"done\"}\\n'\n",
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
            "EVMBENCH_LLM_PROVIDER": "azure-foundry",
            "EVMBENCH_LLM_MODEL": "gpt-5.4",
            "EVMBENCH_LLM_BASE_URL": "https://unit.openai.azure.com/openai/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "AZURE_FOUNDRY_API_KEY",
            "AZURE_FOUNDRY_API_KEY": "azure-key",
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
    assert (logs_dir / "opencode-base-url.txt").read_text(encoding="utf-8").startswith("http://127.0.0.1:")
    assert (
        logs_dir / "opencode-upstream-base-url.txt"
    ).read_text(encoding="utf-8").strip() == "https://unit.openai.azure.com/openai/v1"
    assert (logs_dir / "opencode-output-cap.txt").read_text(encoding="utf-8").strip() == "32768"
    assert "OpenCode Azure compatibility proxy" in (logs_dir / "debug.log").read_text(encoding="utf-8")
    assert "Azure compatibility proxy listening" in (logs_dir / "azure-compat-proxy.log").read_text(encoding="utf-8")


def _opencode_start_env(tmp_path: Path, fake_opencode: Path, *, mode: str, audit_dir: Path | None = None) -> tuple[dict[str, str], Path, Path, Path]:
    workspace = tmp_path / "workspace"
    agent_dir = workspace / "agent"
    audit = audit_dir or agent_dir / "audit"
    logs_dir = workspace / "logs"
    audit.mkdir(parents=True, exist_ok=True)

    env = os.environ.copy()
    env.update(
        {
            "PATH": f"{fake_opencode.parent}:{env['PATH']}",
            "WORKSPACE_BASE": str(workspace),
            "AGENT_DIR": str(agent_dir),
            "AUDIT_DIR": str(audit),
            "LOGS_DIR": str(logs_dir),
            "EVMBENCH_LLM_PROVIDER": "openai",
            "EVMBENCH_LLM_MODEL": "gpt-5.4",
            "EVMBENCH_LLM_BASE_URL": "https://api.openai.com/v1",
            "EVMBENCH_LLM_API_KEY_ENV": "OPENAI_API_KEY",
            "OPENAI_API_KEY": "test-key",
            "EVMBENCH_TASK_MODE": mode,
            "EVMBENCH_AUDIT_ID": "unit-audit",
            "OPENCODE_AGENT_TIMEOUT_SECONDS": "3600",
        }
    )
    return env, agent_dir, audit, logs_dir


def test_opencode_timeout_detect_writes_fallback_submission_and_status(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"type\":\"step_start\"}\\n'\n"
        "exit 130\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)
    env, agent_dir, _, logs_dir = _opencode_start_env(tmp_path, fake_opencode, mode="detect")

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/openrouter-v1/opencode-start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    audit_report = agent_dir / "submission" / "audit.md"
    assert audit_report.exists()
    assert "OpenCode fallback audit report" in audit_report.read_text(encoding="utf-8")
    status = json.loads((logs_dir / "opencode" / "status.json").read_text(encoding="utf-8"))
    assert status["real_exit_code"] == 130
    assert status["effective_exit_code"] == 0
    assert status["timed_out"] is True
    assert status["submission_fallback"] is True
    manifest = json.loads((logs_dir / "opencode" / "trajectory-manifest.json").read_text(encoding="utf-8"))
    assert manifest["run_error"] == "opencode exited 130"


def test_opencode_timeout_patch_writes_valid_nonempty_fallback_diff(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"type\":\"step_start\"}\\n'\n"
        "exit 130\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)

    audit_dir = tmp_path / "repo"
    audit_dir.mkdir()
    subprocess.run(["git", "init"], cwd=audit_dir, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "config", "user.email", "unit@example.test"], cwd=audit_dir, check=True)
    subprocess.run(["git", "config", "user.name", "Unit Test"], cwd=audit_dir, check=True)
    (audit_dir / "Contract.sol").write_text("contract C {}\n", encoding="utf-8")
    subprocess.run(["git", "add", "Contract.sol"], cwd=audit_dir, check=True)
    subprocess.run(["git", "commit", "-m", "base"], cwd=audit_dir, check=True, stdout=subprocess.PIPE)
    base_commit = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=audit_dir, text=True).strip()

    env, agent_dir, _, logs_dir = _opencode_start_env(tmp_path, fake_opencode, mode="patch", audit_dir=audit_dir)
    env["EVMBENCH_AUDIT_BASE_COMMIT"] = base_commit

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/openrouter-v1/opencode-start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    diff_path = agent_dir / "submission" / "agent.diff"
    diff_text = diff_path.read_text(encoding="utf-8")
    assert ".evmbench-opencode-fallback.txt" in diff_text
    assert diff_path.stat().st_size > 0
    status = json.loads((logs_dir / "opencode" / "status.json").read_text(encoding="utf-8"))
    assert status["submission_fallback"] is True
    subprocess.run(["git", "reset", "--hard", base_commit], cwd=audit_dir, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "clean", "-fd"], cwd=audit_dir, check=True, stdout=subprocess.PIPE)
    subprocess.run(["git", "apply", "--check", str(diff_path)], cwd=audit_dir, check=True)


def test_opencode_timeout_exploit_writes_empty_txs_json(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text(
        "#!/usr/bin/env bash\n"
        "printf '{\"type\":\"step_start\"}\\n'\n"
        "exit 130\n",
        encoding="utf-8",
    )
    fake_opencode.chmod(0o755)
    env, agent_dir, _, logs_dir = _opencode_start_env(tmp_path, fake_opencode, mode="exploit")

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/openrouter-v1/opencode-start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    txs = json.loads((agent_dir / "submission" / "txs.json").read_text(encoding="utf-8"))
    assert txs == {"transactions": []}
    status = json.loads((logs_dir / "opencode" / "status.json").read_text(encoding="utf-8"))
    assert status["real_exit_code"] == 130
    assert status["timed_out"] is True


def test_summary_reports_opencode_timeout_fallback_from_status_json(tmp_path: Path) -> None:
    item = runner.build_run_matrix(
        output_root=tmp_path,
        tasks=[runner.TaskSpec(mode="detect", audit_id="2024-01-canto")],
        harnesses=[runner.HARNESS_SPECS["opencode"]],
        models=["gpt-5.4"],
        provider="openai",
        base_url=None,
    )[0]
    run_dir = item.runs_dir / "2026-01-01T00-00-00-GMT_run-group_opencode-openrouter-v1_detect" / "2024-01-canto_unit"
    (run_dir / "submission").mkdir(parents=True)
    (run_dir / "logs" / "opencode").mkdir(parents=True)
    (run_dir / "submission" / "audit.md").write_text("# fallback\n", encoding="utf-8")
    (run_dir / "run.log").write_text(
        repr(
            {
                "grade": {
                    "evmbench_result": {
                        "score": 0,
                        "max_score": 1,
                        "detect_award": 0,
                        "detect_max_award": 1,
                        "agent_output": {"runtime_in_seconds": 7200},
                    }
                }
            }
        )
        + "\n",
        encoding="utf-8",
    )
    (run_dir / "logs" / "opencode" / "status.json").write_text(
        json.dumps(
            {
                "real_exit_code": 130,
                "effective_exit_code": 0,
                "timed_out": True,
                "submission_fallback": True,
                "submission_fallback_reason": "opencode timed out before writing audit.md",
            }
        ),
        encoding="utf-8",
    )
    (run_dir / "logs" / "opencode" / "trajectory-manifest.json").write_text(
        json.dumps(
            {
                "expected_trajectory_count": 1,
                "found_trajectory_count": 1,
                "missing_trajectory_count": 0,
            }
        ),
        encoding="utf-8",
    )

    row = runner.summarize_row(
        tmp_path,
        item,
        {"returncode": 0, "timed_out": False, "timeout_seconds": 10800},
    )

    assert row["submission_exists"] is True
    assert row["submission_fallback"] is True
    assert row["opencode_status"] == "logs/opencode/status.json"
    assert "opencode timed out" in row["failure_reason"]
    assert "fallback submission generated" in row["failure_reason"]
