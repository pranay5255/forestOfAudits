import json
import os
import subprocess
from pathlib import Path

from evmbench.agents.agent import agent_registry
from evmbench.vllm import runner

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def test_run_harness_builds_direct_non_forest_commands(tmp_path: Path) -> None:
    expected = {
        "codex": "codex-qwen-vllm",
        "opencode": "opencode-qwen-vllm",
        "mini-swe-agent": "mini-swe-agent-qwen-vllm",
    }

    for harness, agent_id in expected.items():
        spec = runner.build_run_spec(
            harness=harness,
            audit_id="2024-01-canto",
            mode="detect",
            output_dir=tmp_path / harness,
        )
        command = list(spec.command)

        assert spec.agent_id == agent_id
        assert "-m" in command
        assert "evmbench.nano.entrypoint" in command
        assert f"evmbench.solver.agent_id={agent_id}" in command
        assert "runner.concurrency=1" in command
        assert not any("forest" in part for part in command)


def test_run_harness_dry_run_writes_manifest_without_metrics(tmp_path: Path) -> None:
    status = runner.main(
        [
            "--harness",
            "opencode",
            "--mode",
            "patch",
            "--audit-id",
            "2024-01-canto",
            "--api-base",
            "https://vllm.example.test/v1",
            "--api-key",
            "vllm-key",
            "--served-model-name",
            "Qwen/Qwen3.6-35B-A3B-FP8",
            "--output-dir",
            str(tmp_path),
            "--no-metrics",
            "--dry-run",
        ]
    )

    assert status == 0
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["harness"] == "opencode"
    assert manifest["agent_id"] == "opencode-qwen-vllm"
    assert manifest["mode"] == "patch"
    assert manifest["return_code"] == 0
    assert manifest["metrics_enabled"] is False
    assert manifest["vllm_env"]["VLLM_API_KEY"] == "set length=8"
    assert f"evmbench.solver.agent_id=opencode-qwen-vllm" in manifest["command"]


def test_codex_qwen_vllm_agent_config_resolves_vllm_env(monkeypatch) -> None:
    monkeypatch.setenv("VLLM_API_BASE", "https://vllm.example.test/v1")
    monkeypatch.setenv("VLLM_API_KEY", "vllm-key")
    monkeypatch.setenv("VLLM_SERVED_MODEL_NAME", "Qwen/Qwen3.6-35B-A3B-FP8")

    agent = agent_registry.get_agent("codex-qwen-vllm")

    assert agent.runner == "container"
    assert agent.start_sh.endswith("evmbench/agents/codex/start.sh")
    assert agent.env_vars["VLLM_API_BASE"] == "https://vllm.example.test/v1"
    assert agent.env_vars["VLLM_API_KEY"] == "vllm-key"
    assert agent.env_vars["MODEL"] == "Qwen/Qwen3.6-35B-A3B-FP8"
    assert agent.env_vars["CODEX_PROVIDER_ID"] == "vllm"


def test_codex_start_sh_uses_vllm_chat_provider(tmp_path: Path) -> None:
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
            "VLLM_API_BASE": "https://vllm.example.test/v1",
            "VLLM_API_KEY": "vllm-key",
            "VLLM_SERVED_MODEL_NAME": "Qwen/Qwen3.6-35B-A3B-FP8",
            "MODEL": "openai/Qwen/Qwen3.6-35B-A3B-FP8",
        }
    )
    env.pop("OPENAI_API_KEY", None)

    completed = subprocess.run(
        ["bash", str(PROJECT_ROOT / "evmbench/agents/codex/start.sh")],
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        timeout=30,
    )

    assert completed.returncode == 0, completed.stderr + completed.stdout
    args = (logs_dir / "codex-args.txt").read_text(encoding="utf-8")
    assert "--model\nQwen/Qwen3.6-35B-A3B-FP8\n" in args
    assert 'model_provider="vllm"' in args
    assert 'model_providers.vllm.name="EVMBench vLLM"' in args
    assert 'model_providers.vllm.base_url="https://vllm.example.test/v1"' in args
    assert 'model_providers.vllm.env_key="VLLM_API_KEY"' in args
    assert 'model_providers.vllm.wire_api="responses"' in args
    assert 'wire_api="chat"' not in args



def test_run_harness_profiles_around_process_and_records_artifact_errors(tmp_path: Path, monkeypatch) -> None:
    spec = runner.build_run_spec(
        harness="codex",
        audit_id="2024-01-canto",
        mode="detect",
        output_dir=tmp_path,
    )
    events: list[str] = []

    def fake_profile_request(**kwargs):
        events.append(kwargs["action"])
        return {"ok": True, "action": kwargs["action"], "endpoint": "profile"}

    def fake_run(command, **kwargs):
        events.append("harness")
        return subprocess.CompletedProcess(command, 0)

    def fake_collect_profile_artifacts(**kwargs):
        events.append("collect")
        return (
            {
                "telemetry_jsonl": str(tmp_path / "metrics" / "gpu" / "gpu.telemetry.jsonl"),
                "summary_json": str(tmp_path / "metrics" / "gpu" / "gpu.summary.json"),
            },
            {
                "index_json": str(tmp_path / "metrics" / "kernel" / "torch" / "profile-index.json"),
                "cuda_summary_json": str(tmp_path / "metrics" / "kernel" / "torch" / "cuda-summary.json"),
            },
            {"torch_profile_volume_get": "missing remote path"},
        )

    monkeypatch.setattr(runner, "_profile_request", fake_profile_request)
    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    monkeypatch.setattr(runner, "_collect_profile_artifacts", fake_collect_profile_artifacts)

    status = runner.run_harness(
        spec=spec,
        api_base="https://vllm.example.test/v1",
        api_key="vllm-key",
        served_model_name="Qwen/Qwen3.6-27B",
        metrics_enabled=False,
        kernel_profile="torch",
        profile_volume_name="unit-vllm-profiles",
    )

    assert status == 0
    assert events == ["start", "harness", "stop", "collect"]
    manifest = json.loads((tmp_path / "run-manifest.json").read_text(encoding="utf-8"))
    assert manifest["profiling"]["kernel_profile"] == "torch"
    assert manifest["profiling"]["profile_volume_name"] == "unit-vllm-profiles"
    assert manifest["metrics"]["kernel"]["index_json"].endswith("profile-index.json")
    assert manifest["metrics"]["gpu"]["telemetry_jsonl"].endswith("gpu.telemetry.jsonl")
    assert manifest["metrics"]["errors"]["torch_profile_volume_get"] == "missing remote path"
    assert manifest["artifact_paths"]["kernel_profile_index"].endswith("profile-index.json")
