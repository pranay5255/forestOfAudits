#!/usr/bin/env python3
"""Shared helpers for EVMBench vLLM Modal scripts."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import requests
from dotenv import load_dotenv

DEFAULT_APP_NAME = "evmbench-vllm-qwen"
DEFAULT_SECRET_NAME = "evmbench-vllm-token"
DEFAULT_MODEL = "Qwen/Qwen3.6-35B-A3B"
DEFAULT_AUDIT_ID = "2024-01-canto"
DEFAULT_IMAGE_REPO = "ghcr.io/pranay5255/evmbench-audit"


@dataclass(frozen=True)
class EndpointVerification:
    api_base: str
    server_root: str
    model: str
    health_seconds: float
    models_seconds: float
    chat_seconds: float
    response_preview: str


def project_root() -> Path:
    return Path(__file__).resolve().parents[3]


def load_project_env(env_file: Path | None = None) -> Path:
    resolved = env_file or project_root() / ".env"
    if resolved.exists():
        load_dotenv(resolved, override=True)
    return resolved


def clean_env_value(value: str | None) -> str:
    stripped = (value or "").strip()
    if not stripped or stripped.startswith("${{"):
        return ""
    return stripped


def env_bool(name: str, default: bool) -> bool:
    value = clean_env_value(os.getenv(name))
    if not value:
        return default
    normalized = value.lower()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"0", "false", "no", "off"}:
        return False
    raise RuntimeError(f"{name} must be a boolean value, got {value!r}.")


def require_env(name: str) -> str:
    value = clean_env_value(os.getenv(name))
    if not value:
        raise RuntimeError(f"{name} is required. Set it in .env or pass the corresponding flag.")
    return value


def api_base_from_server_root(server_root: str) -> str:
    return server_root.rstrip("/") + "/v1"


def server_root_from_api_base(api_base: str) -> str:
    api_base = api_base.rstrip("/")
    return api_base[:-3] if api_base.endswith("/v1") else api_base


def litellm_model_name(served_model_name: str) -> str:
    return served_model_name if served_model_name.startswith("openai/") else f"openai/{served_model_name}"


def default_audit_image(audit_id: str, image_repo: str | None = None) -> str:
    repo = clean_env_value(image_repo) or clean_env_value(os.getenv("MODAL_AUDIT_IMAGE_REPO")) or DEFAULT_IMAGE_REPO
    return clean_env_value(os.getenv("MODAL_AUDIT_IMAGE")) or f"{repo}:{audit_id}"


def redacted_length(value: str) -> str:
    return f"set length={len(value)}" if value else "not set"


def modal_binary() -> str:
    modal = shutil.which("modal")
    if not modal:
        raise RuntimeError("Could not find `modal` on PATH. Run this script with `uv run python ...`.")
    return modal


def create_or_update_modal_secret(secret_name: str, values: dict[str, str], *, log_prefix: str = "vllm") -> None:
    if not values:
        raise RuntimeError("Modal secret values are empty.")
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", suffix=".json", delete=False) as handle:
        json.dump(values, handle)
        secret_path = Path(handle.name)
    try:
        secret_path.chmod(0o600)
        command = [
            modal_binary(),
            "secret",
            "create",
            secret_name,
            "--force",
            "--from-json",
            str(secret_path),
        ]
        print(f"[{log_prefix}] creating/updating Modal secret {secret_name!r}", flush=True)
        completed = subprocess.run(command, cwd=project_root())
        if completed.returncode != 0:
            raise RuntimeError(f"Modal secret creation failed with exit code {completed.returncode}.")
    finally:
        try:
            secret_path.unlink()
        except FileNotFoundError:
            pass


def run_command(command: list[str], *, env: dict[str, str] | None = None, quiet: bool = False) -> None:
    if not quiet:
        printable = " ".join(command)
        print(f"[cmd] {printable}", flush=True)
    completed = subprocess.run(command, cwd=project_root(), env=env)
    if completed.returncode != 0:
        raise RuntimeError(f"Command failed with exit code {completed.returncode}: {' '.join(command)}")


def _request_json(
    session: requests.Session,
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: float,
    payload: dict[str, Any] | None = None,
    allow_redirects: bool = True,
) -> requests.Response:
    return session.request(
        method,
        url,
        headers=headers,
        json=payload,
        timeout=timeout,
        allow_redirects=allow_redirects,
    )


def _body_preview(response: requests.Response, limit: int = 500) -> str:
    body = response.text.strip().replace("\n", "\\n")
    return body[:limit]


def verify_vllm_endpoint(
    *,
    api_base: str,
    api_key: str,
    served_model_name: str,
    wait_timeout: float = 1800.0,
    request_timeout: float = 300.0,
    chat_timeout: float = 600.0,
    skip_chat: bool = False,
) -> EndpointVerification:
    api_base = api_base.rstrip("/")
    server_root = server_root_from_api_base(api_base)
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }
    with requests.Session() as session:
        print(f"[verify] waiting for vLLM health at {server_root}/health", flush=True)
        health_started = time.monotonic()
        try:
            response = _request_json(
                session,
                "GET",
                f"{server_root}/health",
                headers=headers,
                timeout=wait_timeout,
                allow_redirects=True,
            )
        except requests.RequestException as exc:
            raise RuntimeError(f"Timed out waiting for vLLM health: {exc}") from exc
        if response.status_code != 200:
            raise RuntimeError(
                f"vLLM health failed with status={response.status_code}: "
                f"location={response.headers.get('location', '')!r} body={_body_preview(response)!r}"
            )
        health_seconds = time.monotonic() - health_started
        print(f"[verify] health ok in {health_seconds:.1f}s", flush=True)

        models_started = time.monotonic()
        models_response = _request_json(
            session,
            "GET",
            f"{api_base}/models",
            headers=headers,
            timeout=request_timeout,
            allow_redirects=True,
        )
        models_seconds = time.monotonic() - models_started
        if models_response.status_code != 200:
            raise RuntimeError(
                f"vLLM /v1/models failed with status={models_response.status_code}: "
                f"{_body_preview(models_response)}"
            )
        try:
            models_payload = models_response.json()
        except ValueError as exc:
            raise RuntimeError(f"vLLM /v1/models did not return JSON: {_body_preview(models_response)}") from exc
        model_ids = {str(item.get("id", "")) for item in models_payload.get("data", []) if isinstance(item, dict)}
        if model_ids and served_model_name not in model_ids:
            print(
                f"[verify] warning: served model {served_model_name!r} was not listed by /v1/models; "
                f"listed={sorted(model_ids)}",
                flush=True,
            )
        print(f"[verify] /v1/models ok in {models_seconds:.1f}s", flush=True)

        response_preview = ""
        chat_seconds = 0.0
        if not skip_chat:
            chat_started = time.monotonic()
            payload = {
                "model": served_model_name,
                "messages": [{"role": "user", "content": "Reply with exactly: ok"}],
                "max_tokens": 16,
                "temperature": 0,
                "chat_template_kwargs": {"enable_thinking": False},
            }
            chat_response = _request_json(
                session,
                "POST",
                f"{api_base}/chat/completions",
                headers=headers,
                timeout=chat_timeout,
                payload=payload,
                allow_redirects=False,
            )
            if chat_response.status_code in {400, 422}:
                payload.pop("chat_template_kwargs", None)
                chat_response = _request_json(
                    session,
                    "POST",
                    f"{api_base}/chat/completions",
                    headers=headers,
                    timeout=chat_timeout,
                    payload=payload,
                    allow_redirects=False,
                )
            chat_seconds = time.monotonic() - chat_started
            if chat_response.status_code != 200:
                raise RuntimeError(
                    f"vLLM chat completion failed with status={chat_response.status_code}: "
                    f"{_body_preview(chat_response)}"
                )
            try:
                chat_payload = chat_response.json()
                response_preview = (
                    chat_payload.get("choices", [{}])[0].get("message", {}).get("content", "") or ""
                ).strip()
            except (ValueError, AttributeError, IndexError) as exc:
                raise RuntimeError(f"vLLM chat completion returned invalid JSON: {_body_preview(chat_response)}") from exc
            if not response_preview:
                raise RuntimeError("vLLM chat completion succeeded but returned empty content.")
            print(f"[verify] chat completion ok in {chat_seconds:.1f}s: {response_preview[:120]!r}", flush=True)

    return EndpointVerification(
        api_base=api_base,
        server_root=server_root,
        model=served_model_name,
        health_seconds=health_seconds,
        models_seconds=models_seconds,
        chat_seconds=chat_seconds,
        response_preview=response_preview,
    )


def upsert_dotenv_values(env_file: Path, values: dict[str, str]) -> None:
    env_file.parent.mkdir(parents=True, exist_ok=True)
    existing_lines = env_file.read_text(encoding="utf-8").splitlines() if env_file.exists() else []
    remaining = dict(values)
    rendered: list[str] = []
    for line in existing_lines:
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in line:
            rendered.append(line)
            continue
        key = line.split("=", 1)[0].strip()
        if key in remaining:
            rendered.append(f"{key}={json.dumps(remaining.pop(key))}")
        else:
            rendered.append(line)
    if remaining and rendered and rendered[-1].strip():
        rendered.append("")
    for key, value in remaining.items():
        rendered.append(f"{key}={json.dumps(value)}")
    env_file.write_text("\n".join(rendered) + "\n", encoding="utf-8")


def write_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")


def fail(message: str) -> int:
    print(f"error: {message}", file=sys.stderr)
    return 1
