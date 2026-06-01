import json
import os
import subprocess
from pathlib import Path

from evmbench.vllm import compat

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_chat_tool_payload_uses_bash_function_schema() -> None:
    payload = compat.build_chat_tool_payload(
        model="Qwen/Qwen3.6-27B",
        command="echo tool-smoke-ok",
        forced=True,
    )

    assert payload["tool_choice"] == {"type": "function", "function": {"name": "bash"}}
    tool = payload["tools"][0]["function"]
    assert tool["name"] == "bash"
    assert tool["parameters"]["required"] == ["command"]
    assert tool["parameters"]["properties"]["command"]["type"] == "string"


def test_parse_chat_bash_tool_call_accepts_json_arguments() -> None:
    payload = {
        "choices": [
            {
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps({"command": "echo tool-smoke-ok"}),
                            }
                        }
                    ]
                }
            }
        ]
    }

    ok, command, error = compat.parse_chat_bash_tool_call(
        payload, expected_command="echo tool-smoke-ok"
    )

    assert ok is True
    assert command == "echo tool-smoke-ok"
    assert error is None


def test_parse_responses_bash_tool_call_accepts_function_call() -> None:
    payload = {
        "output": [
            {
                "type": "function_call",
                "name": "bash",
                "arguments": json.dumps({"command": "echo tool-smoke-ok"}),
            }
        ]
    }

    ok, command, error = compat.parse_responses_bash_tool_call(
        payload, expected_command="echo tool-smoke-ok"
    )

    assert ok is True
    assert command == "echo tool-smoke-ok"
    assert error is None


def test_opencode_dry_run_config_allows_bash(tmp_path: Path) -> None:
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    fake_opencode = fake_bin / "opencode"
    fake_opencode.write_text("#!/usr/bin/env bash\nprintf 'opencode test\n'\n", encoding="utf-8")
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
            "VLLM_API_BASE": "https://vllm.example.test/v1",
            "VLLM_API_KEY": "vllm-key",
            "VLLM_SERVED_MODEL_NAME": "Qwen/Qwen3.6-27B",
            "MODEL": "openai/Qwen/Qwen3.6-27B",
            "OPENCODE_DRY_RUN": "1",
        }
    )

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/opencode/start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    config = json.loads((agent_dir / "opencode.json").read_text(encoding="utf-8"))
    assert config["permission"]["bash"] == "allow"
    provider = config["provider"]["localvllm"]
    assert provider["npm"] == "@ai-sdk/openai-compatible"
    assert provider["options"]["baseURL"] == "{env:VLLM_API_BASE}"
    assert "includeUsage" not in provider["options"]
    assert provider["models"]["Qwen/Qwen3.6-27B"]["limit"] == {"context": 32000, "output": 1024, "input": 30976}
    assert config["compaction"] == {"auto": True, "prune": True, "reserved": 4096}


def test_opencode_vllm_default_output_cap_stays_within_context_budget() -> None:
    start_sh = (PROJECT_ROOT / "evmbench/agents/opencode/start.sh").read_text(encoding="utf-8")

    assert "OPENCODE_VLLM_OUTPUT_TOKEN_MAX:-1024" in start_sh
    assert 'OPENCODE_VLLM_CONTEXT_TOKEN_MAX", "32000"' in start_sh
    assert 'OPENCODE_VLLM_INPUT_TOKEN_MAX"' in start_sh
    assert "OPENCODE_VLLM_OUTPUT_TOKEN_MAX:-2048" not in start_sh
    assert "OPENCODE_VLLM_OUTPUT_TOKEN_MAX:-4096" not in start_sh


def test_opencode_qwen_vllm_agent_config_uses_bounded_output_cap() -> None:
    config = (PROJECT_ROOT / "evmbench/agents/opencode/config.yaml").read_text(encoding="utf-8")

    assert 'OPENCODE_VLLM_OUTPUT_TOKEN_MAX: "1024"' in config
    assert 'OPENCODE_VLLM_CONTEXT_TOKEN_MAX: "32000"' in config
    assert 'OPENCODE_VLLM_INPUT_TOKEN_MAX: "30976"' in config
    assert 'OPENCODE_VLLM_COMPACTION_RESERVED: "4096"' in config
    assert 'OPENCODE_VLLM_OUTPUT_TOKEN_MAX: "2048"' not in config
    assert 'OPENCODE_VLLM_OUTPUT_TOKEN_MAX: "4096"' not in config
