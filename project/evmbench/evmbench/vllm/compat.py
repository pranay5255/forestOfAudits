#!/usr/bin/env python3
"""Protocol and tool-call compatibility smoke tests for vLLM endpoints."""

from __future__ import annotations

import argparse
import json
import time
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import requests

from evmbench.vllm.common import (
    clean_env_value,
    fail,
    load_project_env,
    project_root,
    redacted_length,
    require_env,
    write_json,
)

BASH_TOOL_PARAMETERS: dict[str, Any] = {
    "type": "object",
    "properties": {
        "command": {
            "type": "string",
            "description": "The shell command to execute.",
        },
    },
    "required": ["command"],
    "additionalProperties": False,
}


@dataclass
class ProbeResult:
    name: str
    ok: bool
    status_code: int | None
    seconds: float
    request_path: str
    response_path: str
    error: str | None = None
    tool_name: str | None = None
    command: str | None = None
    body_preview: str | None = None


def _timestamp() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H-%M-%SZ")


def default_output_dir() -> Path:
    return project_root() / "runs" / "vllm-compat" / _timestamp()


def _headers(api_key: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json",
    }


def _chat_bash_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": "bash",
            "description": "Execute a bash command in the agent workspace.",
            "parameters": BASH_TOOL_PARAMETERS,
        },
    }


def _responses_bash_tool() -> dict[str, Any]:
    return {
        "type": "function",
        "name": "bash",
        "description": "Execute a bash command in the agent workspace.",
        "parameters": BASH_TOOL_PARAMETERS,
    }


def build_chat_tool_payload(
    *,
    model: str,
    command: str,
    forced: bool,
    chat_template_kwargs: dict[str, Any] | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "model": model,
        "messages": [
            {
                "role": "user",
                "content": f"Call the bash tool with command `{command}`. Do not answer in prose.",
            }
        ],
        "tools": [_chat_bash_tool()],
        "tool_choice": {"type": "function", "function": {"name": "bash"}} if forced else "auto",
        "max_tokens": 128,
        "temperature": 0,
    }
    if chat_template_kwargs is not None:
        payload["chat_template_kwargs"] = chat_template_kwargs
    return payload


def build_responses_basic_payload(*, model: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": "Reply with exactly: responses-smoke-ok",
        "max_output_tokens": 16,
        "temperature": 0,
    }


def build_responses_tool_payload(*, model: str, command: str) -> dict[str, Any]:
    return {
        "model": model,
        "input": f"Call the bash tool with command `{command}`. Do not answer in prose.",
        "tools": [_responses_bash_tool()],
        "tool_choice": {"type": "function", "name": "bash"},
        "max_output_tokens": 128,
        "temperature": 0,
    }


def _json_or_text(response: requests.Response) -> dict[str, Any]:
    try:
        parsed = response.json()
    except ValueError:
        return {"raw_text": response.text}
    return parsed if isinstance(parsed, dict) else {"json": parsed}


def _body_preview(payload: dict[str, Any], limit: int = 1000) -> str:
    return json.dumps(payload, sort_keys=True, default=str)[:limit]


def _post_probe(
    *,
    session: requests.Session,
    name: str,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    output_dir: Path,
    timeout: float,
) -> tuple[ProbeResult, dict[str, Any] | None]:
    request_path = output_dir / f"{name}.request.json"
    response_path = output_dir / f"{name}.response.json"
    write_json(request_path, payload)
    started = time.monotonic()
    try:
        response = session.post(url, headers=headers, json=payload, timeout=timeout, allow_redirects=False)
        seconds = time.monotonic() - started
        response_payload = _json_or_text(response)
        write_json(
            response_path,
            {
                "status_code": response.status_code,
                "headers": dict(response.headers),
                "body": response_payload,
            },
        )
        return (
            ProbeResult(
                name=name,
                ok=response.status_code == 200,
                status_code=response.status_code,
                seconds=seconds,
                request_path=str(request_path),
                response_path=str(response_path),
                error=None if response.status_code == 200 else f"HTTP {response.status_code}",
                body_preview=_body_preview(response_payload),
            ),
            response_payload,
        )
    except Exception as exc:
        seconds = time.monotonic() - started
        write_json(response_path, {"error": str(exc), "type": type(exc).__name__})
        return (
            ProbeResult(
                name=name,
                ok=False,
                status_code=None,
                seconds=seconds,
                request_path=str(request_path),
                response_path=str(response_path),
                error=str(exc),
            ),
            None,
        )


def _decode_arguments(arguments: Any) -> dict[str, Any]:
    if isinstance(arguments, dict):
        return arguments
    if isinstance(arguments, str):
        parsed = json.loads(arguments)
        if isinstance(parsed, dict):
            return parsed
    raise ValueError(f"tool arguments were not a JSON object: {arguments!r}")


def parse_chat_bash_tool_call(payload: dict[str, Any], *, expected_command: str) -> tuple[bool, str | None, str | None]:
    try:
        choices = payload["choices"]
        message = choices[0]["message"]
        tool_calls = message.get("tool_calls") or []
        if not tool_calls:
            return False, None, "no tool_calls returned"
        function = tool_calls[0].get("function") or {}
        tool_name = function.get("name")
        arguments = _decode_arguments(function.get("arguments"))
        command = arguments.get("command")
        if tool_name != "bash":
            return False, command if isinstance(command, str) else None, f"expected bash tool, got {tool_name!r}"
        if not isinstance(command, str):
            return False, None, "bash tool arguments did not contain a string command"
        if expected_command not in command:
            return False, command, f"bash command did not contain {expected_command!r}"
        return True, command, None
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        return False, None, f"could not parse chat tool call: {exc}"


def _iter_response_outputs(payload: dict[str, Any]) -> list[dict[str, Any]]:
    output = payload.get("output")
    if isinstance(output, list):
        return [item for item in output if isinstance(item, dict)]
    response = payload.get("response")
    if isinstance(response, dict) and isinstance(response.get("output"), list):
        return [item for item in response["output"] if isinstance(item, dict)]
    return []


def parse_responses_bash_tool_call(payload: dict[str, Any], *, expected_command: str) -> tuple[bool, str | None, str | None]:
    for item in _iter_response_outputs(payload):
        item_type = item.get("type")
        name = item.get("name") or item.get("tool_name")
        if item_type not in {"function_call", "tool_call"} and name != "bash":
            continue
        try:
            arguments = _decode_arguments(item.get("arguments"))
        except ValueError as exc:
            return False, None, str(exc)
        command = arguments.get("command")
        if name != "bash":
            return False, command if isinstance(command, str) else None, f"expected bash tool, got {name!r}"
        if not isinstance(command, str):
            return False, None, "bash tool arguments did not contain a string command"
        if expected_command not in command:
            return False, command, f"bash command did not contain {expected_command!r}"
        return True, command, None
    return False, None, "no Responses function_call for bash returned"


def run_compat_smoke(
    *,
    api_base: str,
    api_key: str,
    served_model_name: str,
    output_dir: Path,
    command: str = "echo tool-smoke-ok",
    request_timeout: float = 600.0,
    chat_template_kwargs: dict[str, Any] | None = None,
    skip_responses: bool = False,
    require_responses: bool = True,
) -> dict[str, Any]:
    api_base = api_base.rstrip("/")
    output_dir.mkdir(parents=True, exist_ok=True)
    results: list[ProbeResult] = []
    headers = _headers(api_key)
    with requests.Session() as session:
        for name, forced in [("chat-tool-forced", True), ("chat-tool-auto", False)]:
            result, payload = _post_probe(
                session=session,
                name=name,
                url=f"{api_base}/chat/completions",
                headers=headers,
                payload=build_chat_tool_payload(
                    model=served_model_name,
                    command=command,
                    forced=forced,
                    chat_template_kwargs=chat_template_kwargs,
                ),
                output_dir=output_dir,
                timeout=request_timeout,
            )
            if payload is not None and result.status_code == 200:
                ok, parsed_command, error = parse_chat_bash_tool_call(payload, expected_command=command)
                result.ok = ok
                result.tool_name = "bash" if ok else result.tool_name
                result.command = parsed_command
                result.error = error
            results.append(result)

        if not skip_responses:
            result, _payload = _post_probe(
                session=session,
                name="responses-basic",
                url=f"{api_base}/responses",
                headers=headers,
                payload=build_responses_basic_payload(model=served_model_name),
                output_dir=output_dir,
                timeout=request_timeout,
            )
            results.append(result)

            result, payload = _post_probe(
                session=session,
                name="responses-tool-forced",
                url=f"{api_base}/responses",
                headers=headers,
                payload=build_responses_tool_payload(model=served_model_name, command=command),
                output_dir=output_dir,
                timeout=request_timeout,
            )
            if payload is not None and result.status_code == 200:
                ok, parsed_command, error = parse_responses_bash_tool_call(payload, expected_command=command)
                result.ok = ok
                result.tool_name = "bash" if ok else result.tool_name
                result.command = parsed_command
                result.error = error
            results.append(result)

    by_name = {result.name: result for result in results}
    chat_ok = by_name["chat-tool-forced"].ok and by_name["chat-tool-auto"].ok
    responses_basic_ok = bool(by_name.get("responses-basic") and by_name["responses-basic"].ok)
    responses_tool_ok = bool(by_name.get("responses-tool-forced") and by_name["responses-tool-forced"].ok)
    responses_ok = skip_responses or (responses_basic_ok and responses_tool_ok)
    summary = {
        "summary_version": 1,
        "created_at": datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "api_base": api_base,
        "model": served_model_name,
        "vllm_env": {
            "VLLM_API_BASE": api_base,
            "VLLM_API_KEY": redacted_length(api_key),
            "VLLM_SERVED_MODEL_NAME": served_model_name,
        },
        "expected_bash_command": command,
        "chat_forced_tool_parser_ok": by_name["chat-tool-forced"].ok,
        "chat_auto_tool_parser_ok": by_name["chat-tool-auto"].ok,
        "direct_chat_tool_parser_ok": chat_ok,
        "responses_basic_ok": responses_basic_ok,
        "responses_tool_parser_ok": responses_tool_ok,
        "codex_vllm_responses_api_probe_ok": responses_ok,
        "required_ok": chat_ok and (responses_ok if require_responses else True),
        "probes": [asdict(result) for result in results],
    }
    write_json(output_dir / "compatibility-summary.json", summary)
    return summary


def _parse_json_object(value: str | None) -> dict[str, Any] | None:
    if not value:
        return None
    parsed = json.loads(value)
    if not isinstance(parsed, dict):
        raise argparse.ArgumentTypeError("expected a JSON object")
    return parsed


def build_arg_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--env-file", type=Path, default=project_root() / ".env")
    parser.add_argument("--api-base", default=None)
    parser.add_argument("--api-key", default=None)
    parser.add_argument("--served-model-name", default=None)
    parser.add_argument("--output-dir", type=Path, default=None)
    parser.add_argument("--command", default="echo tool-smoke-ok")
    parser.add_argument("--request-timeout", type=float, default=600.0)
    parser.add_argument("--chat-template-kwargs-json", default=None)
    parser.add_argument("--skip-responses", action="store_true")
    parser.add_argument(
        "--no-require-responses",
        action="store_true",
        help="Record Responses API failures but do not fail the command on them.",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_arg_parser()
    args = parser.parse_args(argv)
    try:
        load_project_env(args.env_file)
        api_base = clean_env_value(args.api_base) or require_env("VLLM_API_BASE")
        api_key = clean_env_value(args.api_key) or require_env("VLLM_API_KEY")
        served_model_name = clean_env_value(args.served_model_name) or require_env("VLLM_SERVED_MODEL_NAME")
        output_dir = args.output_dir or default_output_dir()
        summary = run_compat_smoke(
            api_base=api_base,
            api_key=api_key,
            served_model_name=served_model_name,
            output_dir=output_dir,
            command=args.command,
            request_timeout=args.request_timeout,
            chat_template_kwargs=_parse_json_object(args.chat_template_kwargs_json),
            skip_responses=args.skip_responses,
            require_responses=not args.no_require_responses,
        )
        print(
            json.dumps(
                {
                    "output_dir": str(output_dir),
                    "summary_path": str(output_dir / "compatibility-summary.json"),
                    "direct_chat_tool_parser_ok": summary["direct_chat_tool_parser_ok"],
                    "codex_vllm_responses_api_probe_ok": summary["codex_vllm_responses_api_probe_ok"],
                    "required_ok": summary["required_ok"],
                },
                indent=2,
            )
        )
        return 0 if summary["required_ok"] else 1
    except Exception as exc:
        return fail(str(exc))


if __name__ == "__main__":
    raise SystemExit(main())
