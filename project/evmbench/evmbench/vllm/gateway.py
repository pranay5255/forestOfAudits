#!/usr/bin/env python3
"""Compatibility gateway for Codex/OpenCode in front of a vLLM endpoint.

The pure helpers in this module translate the OpenAI Responses wire shape used
by Codex into the Chat Completions API that vLLM handles more reliably. The
Modal ASGI app at the bottom exposes those helpers as a small proxy service.
"""

from __future__ import annotations

import copy
import json
import logging
import os
import re
import time
import uuid
from dataclasses import asdict, dataclass
from typing import Any, AsyncIterator, Iterable


GATEWAY_APP_NAME = "evmbench-vllm-gateway"
DEFAULT_SECRET_NAME = "evmbench-vllm-token"
DEFAULT_API_KEY_ENV = "VLLM_API_KEY"
UPSTREAM_API_BASE_ENV = "VLLM_UPSTREAM_API_BASE"
DEFAULT_CHAT_TEMPLATE_KWARGS = {"enable_thinking": False}
CONTEXT_RETRY_TOKEN_MARGIN = 512
CONTEXT_RETRY_MAX_RETRIES = 3
CONTEXT_RETRY_FALLBACK_OUTPUT_TOKENS = 512
CONTEXT_RETRY_TRIM_CHARS = 12000
CONTEXT_RETRY_KEEP_CHARS = 1000
CONTEXT_RETRY_CHAR_BUDGET = 60000
_CONTEXT_RETRY_SKIP_STRING_KEYS = {"role", "type", "id", "name", "tool_call_id", "call_id"}
_CONTEXT_LENGTH_RE = re.compile(
    r"maximum context length is (?P<context>\d+) tokens.*?"
    r"requested (?P<requested>\d+) output tokens.*?"
    r"prompt contains at least (?P<input>\d+) input tokens",
    re.IGNORECASE | re.DOTALL,
)


def _trimmed_context_retry_text(value: str) -> tuple[str, bool]:
    if len(value) <= CONTEXT_RETRY_KEEP_CHARS * 2:
        return value, False
    removed = len(value) - (CONTEXT_RETRY_KEEP_CHARS * 2)
    replacement = (
        value[:CONTEXT_RETRY_KEEP_CHARS]
        + f"\n\n[gateway truncated {removed} chars after vLLM context overflow]\n\n"
        + value[-CONTEXT_RETRY_KEEP_CHARS:]
    )
    return replacement, True


def _collect_context_retry_strings(
    value: Any, candidates: list[tuple[int, dict[str, Any] | list[Any], str | int]]
) -> None:
    if isinstance(value, dict):
        for key, item in value.items():
            if isinstance(item, str):
                if key not in _CONTEXT_RETRY_SKIP_STRING_KEYS:
                    candidates.append((len(item), value, key))
            elif isinstance(item, (dict, list)):
                _collect_context_retry_strings(item, candidates)
    elif isinstance(value, list):
        for index, item in enumerate(value):
            if isinstance(item, str):
                candidates.append((len(item), value, index))
            elif isinstance(item, (dict, list)):
                _collect_context_retry_strings(item, candidates)


def _trim_largest_message_content(payload: dict[str, Any]) -> bool:
    messages = payload.get("messages")
    if not isinstance(messages, list):
        return False
    candidates: list[tuple[int, dict[str, Any] | list[Any], str | int]] = []
    _collect_context_retry_strings(messages, candidates)
    candidates = [candidate for candidate in candidates if candidate[0] > CONTEXT_RETRY_KEEP_CHARS * 2]
    if not candidates:
        return False

    total_chars = sum(length for length, _, _ in candidates)
    changed = False
    for _, container, key in sorted(candidates, key=lambda item: item[0], reverse=True):
        if changed and total_chars <= CONTEXT_RETRY_CHAR_BUDGET:
            break
        value = container[key]
        if not isinstance(value, str):
            continue
        trimmed, did_trim = _trimmed_context_retry_text(value)
        if did_trim:
            container[key] = trimmed
            total_chars -= len(value) - len(trimmed)
            changed = True
    return changed


def _context_retry_payload(
    payload: dict[str, Any], status_code: int, content: bytes | str
) -> dict[str, Any] | None:
    if status_code not in {400, 422}:
        return None
    body = content.decode("utf-8", errors="replace") if isinstance(content, bytes) else content
    message = body
    try:
        parsed = json.loads(body)
    except json.JSONDecodeError:
        parsed = None
    if isinstance(parsed, dict):
        error = parsed.get("error")
        if isinstance(error, dict) and isinstance(error.get("message"), str):
            message = error["message"]
    match = _CONTEXT_LENGTH_RE.search(message)
    if match is None:
        return None
    context_limit = int(match.group("context"))
    requested_output = int(match.group("requested"))
    input_tokens = int(match.group("input"))
    current_max_tokens = payload.get("max_tokens", requested_output)
    if isinstance(current_max_tokens, bool):
        return None
    try:
        current_max_tokens = int(current_max_tokens)
    except (TypeError, ValueError):
        current_max_tokens = requested_output

    retry_payload = copy.deepcopy(payload)
    reported_safe_max_tokens = context_limit - input_tokens - CONTEXT_RETRY_TOKEN_MARGIN
    if reported_safe_max_tokens >= 1:
        retry_max_tokens = min(current_max_tokens, requested_output, reported_safe_max_tokens)
        if retry_max_tokens >= current_max_tokens:
            return None
        retry_payload["max_tokens"] = retry_max_tokens
        return retry_payload

    if not _trim_largest_message_content(retry_payload):
        return None
    retry_payload["max_tokens"] = max(
        1,
        min(current_max_tokens, requested_output, CONTEXT_RETRY_FALLBACK_OUTPUT_TOKENS),
    )
    return retry_payload


def server_root_from_api_base(api_base: str) -> str:
    api_base = api_base.rstrip("/")
    return api_base[:-3] if api_base.endswith("/v1") else api_base


logger = logging.getLogger(__name__)


@dataclass
class ConversionStats:
    input_items: int = 0
    output_items: int = 0
    system_messages: int = 0
    developer_messages: int = 0
    function_calls: int = 0
    function_outputs: int = 0
    dropped_reasoning_items: int = 0
    dropped_items: int = 0
    tools: int = 0

    def as_dict(self) -> dict[str, int]:
        return asdict(self)


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:24]}"


def _json_string(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    return json.dumps(value, separators=(",", ":"), sort_keys=True)


def _content_to_text(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        parts: list[str] = []
        for part in content:
            if isinstance(part, str):
                parts.append(part)
            elif isinstance(part, dict):
                text = part.get("text")
                if isinstance(text, str):
                    parts.append(text)
                elif isinstance(part.get("output_text"), str):
                    parts.append(str(part["output_text"]))
                elif isinstance(part.get("input_text"), str):
                    parts.append(str(part["input_text"]))
            elif part is not None:
                parts.append(str(part))
        return "\n".join(part for part in parts if part)
    if isinstance(content, dict):
        text = content.get("text")
        if isinstance(text, str):
            return text
        return json.dumps(content, separators=(",", ":"), sort_keys=True)
    return str(content)


def _message_from_response_role_item(
    item: dict[str, Any], stats: ConversionStats
) -> dict[str, Any] | None:
    role = str(item.get("role") or "").strip()
    if not role:
        return None
    if role == "developer":
        role = "system"
        stats.developer_messages += 1
    if role not in {"system", "user", "assistant", "tool"}:
        stats.dropped_items += 1
        return None

    message: dict[str, Any] = {"role": role}
    if role == "tool":
        call_id = item.get("tool_call_id") or item.get("call_id")
        if call_id:
            message["tool_call_id"] = str(call_id)

    if "tool_calls" in item and isinstance(item["tool_calls"], list):
        message["tool_calls"] = item["tool_calls"]
        message["content"] = _content_to_text(item.get("content"))
    else:
        message["content"] = _content_to_text(item.get("content"))

    if role == "system":
        stats.system_messages += 1
    return message


def _response_function_call_to_chat_message(
    item: dict[str, Any], stats: ConversionStats
) -> dict[str, Any] | None:
    name = item.get("name") or item.get("tool_name")
    if not isinstance(name, str) or not name:
        stats.dropped_items += 1
        return None
    call_id = str(item.get("call_id") or item.get("id") or _new_id("call"))
    stats.function_calls += 1
    return {
        "role": "assistant",
        "content": _content_to_text(item.get("content")),
        "tool_calls": [
            {
                "id": call_id,
                "type": "function",
                "function": {
                    "name": name,
                    "arguments": _json_string(item.get("arguments")),
                },
            }
        ],
    }


def _response_function_output_to_chat_message(
    item: dict[str, Any], stats: ConversionStats
) -> dict[str, Any] | None:
    call_id = item.get("call_id") or item.get("tool_call_id")
    if not call_id:
        stats.dropped_items += 1
        return None
    stats.function_outputs += 1
    return {
        "role": "tool",
        "tool_call_id": str(call_id),
        "content": _content_to_text(item.get("output", item.get("content"))),
    }


def _append_response_input_item(
    messages: list[dict[str, Any]], item: Any, stats: ConversionStats
) -> None:
    stats.input_items += 1
    if isinstance(item, str):
        messages.append({"role": "user", "content": item})
        return
    if not isinstance(item, dict):
        stats.dropped_items += 1
        return

    item_type = str(item.get("type") or "").strip()
    if item_type == "reasoning":
        stats.dropped_reasoning_items += 1
        return
    if item_type == "function_call":
        message = _response_function_call_to_chat_message(item, stats)
    elif item_type == "function_call_output":
        message = _response_function_output_to_chat_message(item, stats)
    elif item_type == "message" or item.get("role"):
        message = _message_from_response_role_item(item, stats)
    elif item_type in {"input_text", "output_text"}:
        message = {"role": "user", "content": _content_to_text(item)}
    else:
        stats.dropped_items += 1
        message = None

    if message is not None:
        messages.append(message)


def responses_tool_to_chat_tool(tool: dict[str, Any]) -> dict[str, Any] | None:
    if tool.get("type") != "function":
        return None
    if isinstance(tool.get("function"), dict):
        return copy.deepcopy(tool)
    name = tool.get("name")
    if not isinstance(name, str) or not name:
        return None
    function: dict[str, Any] = {"name": name}
    if isinstance(tool.get("description"), str):
        function["description"] = tool["description"]
    parameters = tool.get("parameters")
    if isinstance(parameters, dict):
        function["parameters"] = parameters
    return {"type": "function", "function": function}


def responses_tool_choice_to_chat(tool_choice: Any) -> Any:
    if isinstance(tool_choice, dict) and tool_choice.get("type") == "function":
        name = tool_choice.get("name")
        if isinstance(name, str) and name:
            return {"type": "function", "function": {"name": name}}
    return tool_choice


def inject_enable_thinking_false(payload: dict[str, Any]) -> dict[str, Any]:
    converted = copy.deepcopy(payload)
    kwargs = converted.get("chat_template_kwargs")
    if not isinstance(kwargs, dict):
        kwargs = {}
    if "enable_thinking" not in kwargs:
        kwargs["enable_thinking"] = False
    converted["chat_template_kwargs"] = kwargs
    return converted


def normalize_chat_request(payload: dict[str, Any]) -> dict[str, Any]:
    converted = inject_enable_thinking_false(payload)
    messages = converted.get("messages")
    if isinstance(messages, list):
        normalized_messages: list[Any] = []
        for message in messages:
            if isinstance(message, dict):
                normalized = copy.deepcopy(message)
                if normalized.get("role") == "developer":
                    normalized["role"] = "system"
                normalized_messages.append(normalized)
            else:
                normalized_messages.append(message)
        system_messages = [
            message
            for message in normalized_messages
            if isinstance(message, dict) and message.get("role") == "system"
        ]
        non_system_messages = [
            message
            for message in normalized_messages
            if not (isinstance(message, dict) and message.get("role") == "system")
        ]
        if system_messages:
            combined_system = copy.deepcopy(system_messages[0])
            system_content = "\n\n".join(
                part
                for part in (
                    _content_to_text(message.get("content")).strip() for message in system_messages
                )
                if part
            )
            combined_system["content"] = system_content
            converted["messages"] = [combined_system] + non_system_messages
        else:
            converted["messages"] = non_system_messages
    tools = converted.get("tools")
    if isinstance(tools, list):
        chat_tools = [responses_tool_to_chat_tool(tool) for tool in tools if isinstance(tool, dict)]
        converted["tools"] = [tool for tool in chat_tools if tool is not None]
    return converted


def responses_request_to_chat(payload: dict[str, Any]) -> tuple[dict[str, Any], ConversionStats]:
    stats = ConversionStats()
    messages: list[dict[str, Any]] = []

    instructions = payload.get("instructions")
    if isinstance(instructions, str) and instructions.strip():
        messages.append({"role": "system", "content": instructions})
        stats.system_messages += 1

    response_input = payload.get("input", "")
    if isinstance(response_input, list):
        for item in response_input:
            _append_response_input_item(messages, item, stats)
    elif response_input:
        _append_response_input_item(messages, response_input, stats)

    if messages:
        system_messages = [message for message in messages if message.get("role") == "system"]
        non_system_messages = [message for message in messages if message.get("role") != "system"]
        messages = system_messages + non_system_messages

    chat_payload: dict[str, Any] = {
        "model": payload["model"],
        "messages": messages or [{"role": "user", "content": ""}],
    }

    passthrough_fields = {
        "frequency_penalty",
        "presence_penalty",
        "seed",
        "stop",
        "stream",
        "temperature",
        "top_p",
        "user",
    }
    for field in passthrough_fields:
        if field in payload:
            chat_payload[field] = payload[field]
    if "max_output_tokens" in payload:
        chat_payload["max_tokens"] = payload["max_output_tokens"]
    elif "max_tokens" in payload:
        chat_payload["max_tokens"] = payload["max_tokens"]

    tools = payload.get("tools")
    if isinstance(tools, list) and tools:
        chat_tools = [responses_tool_to_chat_tool(tool) for tool in tools if isinstance(tool, dict)]
        chat_payload["tools"] = [tool for tool in chat_tools if tool is not None]
        stats.tools = len(chat_payload["tools"])

    if "tool_choice" in payload:
        chat_payload["tool_choice"] = responses_tool_choice_to_chat(payload["tool_choice"])
    if "parallel_tool_calls" in payload:
        chat_payload["parallel_tool_calls"] = payload["parallel_tool_calls"]

    return normalize_chat_request(chat_payload), stats


def _chat_content_to_responses_message(content: Any, *, model: str) -> dict[str, Any] | None:
    text = _content_to_text(content)
    if not text:
        return None
    return {
        "id": _new_id("msg"),
        "type": "message",
        "status": "completed",
        "role": "assistant",
        "content": [
            {
                "type": "output_text",
                "text": text,
                "annotations": [],
            }
        ],
    }


def _chat_tool_call_to_response_item(tool_call: dict[str, Any]) -> dict[str, Any] | None:
    function = tool_call.get("function") if isinstance(tool_call, dict) else None
    if not isinstance(function, dict):
        return None
    name = function.get("name")
    if not isinstance(name, str) or not name:
        return None
    call_id = str(tool_call.get("id") or _new_id("call"))
    return {
        "id": _new_id("fc"),
        "type": "function_call",
        "status": "completed",
        "call_id": call_id,
        "name": name,
        "arguments": _json_string(function.get("arguments")),
    }


def chat_usage_to_responses_usage(usage: Any) -> dict[str, Any]:
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = usage.get("input_tokens", usage.get("prompt_tokens", 0))
    output_tokens = usage.get("output_tokens", usage.get("completion_tokens", 0))
    if not isinstance(input_tokens, int):
        input_tokens = 0
    if not isinstance(output_tokens, int):
        output_tokens = 0
    total_tokens = usage.get("total_tokens")
    if not isinstance(total_tokens, int):
        total_tokens = input_tokens + output_tokens
    return {
        "input_tokens": input_tokens,
        "input_tokens_details": usage.get("input_tokens_details", {"cached_tokens": 0}),
        "output_tokens": output_tokens,
        "output_tokens_details": usage.get("output_tokens_details", {"reasoning_tokens": 0}),
        "total_tokens": total_tokens,
    }


def chat_response_to_responses(
    chat_payload: dict[str, Any],
    *,
    original_request: dict[str, Any] | None = None,
) -> dict[str, Any]:
    original_request = original_request or {}
    model = str(chat_payload.get("model") or original_request.get("model") or "")
    created = chat_payload.get("created")
    if not isinstance(created, (int, float)):
        created = int(time.time())

    outputs: list[dict[str, Any]] = []
    choices = chat_payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict):
            for tool_call in message.get("tool_calls") or []:
                if isinstance(tool_call, dict):
                    item = _chat_tool_call_to_response_item(tool_call)
                    if item is not None:
                        outputs.append(item)
            message_item = _chat_content_to_responses_message(message.get("content"), model=model)
            if message_item is not None:
                outputs.append(message_item)

    output_text_parts: list[str] = []
    for item in outputs:
        if item.get("type") != "message":
            continue
        for part in item.get("content") or []:
            if (
                isinstance(part, dict)
                and part.get("type") == "output_text"
                and isinstance(part.get("text"), str)
            ):
                output_text_parts.append(part["text"])

    return {
        "id": str(chat_payload.get("id") or _new_id("resp")),
        "object": "response",
        "created_at": created,
        "status": "completed",
        "model": model,
        "output": outputs,
        "output_text": "".join(output_text_parts),
        "parallel_tool_calls": bool(original_request.get("parallel_tool_calls", True)),
        "tool_choice": original_request.get("tool_choice", "auto"),
        "tools": original_request.get("tools", []),
        "usage": chat_usage_to_responses_usage(chat_payload.get("usage")),
    }


def _item_in_progress(item: dict[str, Any]) -> dict[str, Any]:
    pending = copy.deepcopy(item)
    pending["status"] = "in_progress"
    if pending.get("type") == "function_call":
        pending["arguments"] = ""
    elif pending.get("type") == "message":
        pending["content"] = []
    return pending


def responses_sse_events(response_payload: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    events: list[tuple[str, dict[str, Any]]] = []
    sequence_number = 0

    def add(event_type: str, payload: dict[str, Any]) -> None:
        nonlocal sequence_number
        payload = dict(payload)
        payload.setdefault("type", event_type)
        payload.setdefault("sequence_number", sequence_number)
        sequence_number += 1
        events.append((event_type, payload))

    created = copy.deepcopy(response_payload)
    created["output"] = []
    add("response.created", {"response": created})

    output = response_payload.get("output")
    if isinstance(output, list):
        for output_index, item in enumerate(output):
            if not isinstance(item, dict):
                continue
            item_id = str(item.get("id") or _new_id("item"))
            item["id"] = item_id
            add(
                "response.output_item.added",
                {"output_index": output_index, "item": _item_in_progress(item)},
            )
            if item.get("type") == "function_call":
                arguments = _json_string(item.get("arguments"))
                if arguments:
                    add(
                        "response.function_call_arguments.delta",
                        {"item_id": item_id, "output_index": output_index, "delta": arguments},
                    )
                add(
                    "response.function_call_arguments.done",
                    {"item_id": item_id, "output_index": output_index, "arguments": arguments},
                )
            elif item.get("type") == "message":
                content = item.get("content") if isinstance(item.get("content"), list) else []
                for content_index, part in enumerate(content):
                    if not isinstance(part, dict):
                        continue
                    add(
                        "response.content_part.added",
                        {
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "part": {
                                "type": part.get("type", "output_text"),
                                "text": "",
                                "annotations": [],
                            },
                        },
                    )
                    text = _content_to_text(part)
                    if text:
                        add(
                            "response.output_text.delta",
                            {
                                "item_id": item_id,
                                "output_index": output_index,
                                "content_index": content_index,
                                "delta": text,
                            },
                        )
                    add(
                        "response.output_text.done",
                        {
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "text": text,
                        },
                    )
                    add(
                        "response.content_part.done",
                        {
                            "item_id": item_id,
                            "output_index": output_index,
                            "content_index": content_index,
                            "part": part,
                        },
                    )
            add("response.output_item.done", {"output_index": output_index, "item": item})

    add("response.completed", {"response": response_payload})
    return events


def encode_responses_sse(response_payload: dict[str, Any]) -> Iterable[bytes]:
    for event_name, data in responses_sse_events(response_payload):
        yield f"event: {event_name}\ndata: {json.dumps(data, separators=(',', ':'))}\n\n".encode(
            "utf-8"
        )
    yield b"data: [DONE]\n\n"


class ToolCallStreamNormalizer:
    """Fill missing streamed Chat tool-call function names after first sighting."""

    def __init__(self) -> None:
        self._names_by_index: dict[int, str] = {}

    def normalize_chunk(self, chunk: dict[str, Any]) -> dict[str, Any]:
        normalized = copy.deepcopy(chunk)
        choices = normalized.get("choices")
        if not isinstance(choices, list):
            return normalized
        for choice in choices:
            if not isinstance(choice, dict):
                continue
            delta = choice.get("delta")
            if not isinstance(delta, dict):
                continue
            tool_calls = delta.get("tool_calls")
            if not isinstance(tool_calls, list):
                continue
            for fallback_index, tool_call in enumerate(tool_calls):
                if not isinstance(tool_call, dict):
                    continue
                index = tool_call.get("index", fallback_index)
                if not isinstance(index, int):
                    index = fallback_index
                function = tool_call.get("function")
                if not isinstance(function, dict):
                    function = {}
                    tool_call["function"] = function
                name = function.get("name")
                if isinstance(name, str) and name:
                    self._names_by_index[index] = name
                    continue
                known = self._names_by_index.get(index)
                if known:
                    function["name"] = known
        return normalized

    def normalize_sse_line(self, line: str) -> str:
        if not line.startswith("data:"):
            return line
        data = line[len("data:") :].strip()
        if not data or data == "[DONE]":
            return line
        try:
            chunk = json.loads(data)
        except json.JSONDecodeError:
            return line
        normalized = self.normalize_chunk(chunk)
        return "data: " + json.dumps(normalized, separators=(",", ":"))


def _gateway_log(message: str, **fields: Any) -> None:
    logger.info("%s %s", message, json.dumps(fields, sort_keys=True, default=str))


async def _post_json_with_context_retries(
    client: Any,
    url: str,
    headers: dict[str, str],
    payload: dict[str, Any],
    *,
    route: str = "",
    request_id: str | None = None,
) -> tuple[Any, dict[str, Any]]:
    active_payload = payload
    upstream = await client.post(url, headers=headers, json=active_payload)
    for retry_attempt in range(1, CONTEXT_RETRY_MAX_RETRIES + 1):
        retry_payload = _context_retry_payload(
            active_payload, upstream.status_code, upstream.content
        )
        if retry_payload is None:
            break
        _gateway_log(
            "gateway_context_retry",
            route=route,
            request_id=request_id,
            model=active_payload.get("model"),
            original_max_tokens=active_payload.get("max_tokens"),
            retry_max_tokens=retry_payload.get("max_tokens"),
            retry_attempt=retry_attempt,
            max_retries=CONTEXT_RETRY_MAX_RETRIES,
        )
        active_payload = retry_payload
        upstream = await client.post(url, headers=headers, json=active_payload)
    return upstream, active_payload


def create_asgi_app() -> Any:
    from fastapi import FastAPI, Request
    from fastapi.responses import JSONResponse, Response, StreamingResponse

    import httpx

    # Route handlers below are defined under postponed annotations, so FastAPI
    # resolves `Request` through module globals rather than this local scope.
    globals()["Request"] = Request
    globals()["Response"] = Response

    asgi = FastAPI()

    def upstream_api_base() -> str:
        value = (os.getenv(UPSTREAM_API_BASE_ENV) or "").strip().rstrip("/")
        if not value:
            raise RuntimeError(f"{UPSTREAM_API_BASE_ENV} is required for the vLLM gateway.")
        return value

    def upstream_server_root() -> str:
        return server_root_from_api_base(upstream_api_base())

    def gateway_api_key() -> str:
        return (os.getenv(DEFAULT_API_KEY_ENV) or "").strip()

    async def read_json(request: Request) -> dict[str, Any]:
        payload = await request.json()
        if not isinstance(payload, dict):
            raise RuntimeError("expected a JSON object request body")
        return payload

    def upstream_headers(request: Request) -> dict[str, str]:
        headers: dict[str, str] = {"content-type": "application/json"}
        authorization = request.headers.get("authorization")
        if authorization:
            headers["authorization"] = authorization
        elif gateway_api_key():
            headers["authorization"] = f"Bearer {gateway_api_key()}"
        accept = request.headers.get("accept")
        if accept:
            headers["accept"] = accept
        request_id = request.headers.get("x-request-id")
        if request_id:
            headers["x-request-id"] = request_id
        return headers

    async def proxy_bytes(request: Request, url: str, *, method: str | None = None) -> Response:
        body = await request.body()
        headers = upstream_headers(request)
        started = time.monotonic()
        async with httpx.AsyncClient(timeout=None) as client:
            upstream = await client.request(
                method or request.method, url, headers=headers, content=body
            )
        _gateway_log(
            "gateway_proxy",
            route=request.url.path,
            upstream_status=upstream.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type"),
        )

    async def proxy_json(request: Request, url: str, payload: dict[str, Any]) -> Response:
        started = time.monotonic()
        headers = upstream_headers(request)
        async with httpx.AsyncClient(timeout=None) as client:
            upstream, payload = await _post_json_with_context_retries(
                client, url, headers, payload, route=request.url.path
            )
        _gateway_log(
            "gateway_proxy_json",
            route=request.url.path,
            model=payload.get("model"),
            tool_count=len(payload.get("tools") or []),
            upstream_status=upstream.status_code,
            duration_ms=round((time.monotonic() - started) * 1000),
        )
        return Response(
            content=upstream.content,
            status_code=upstream.status_code,
            media_type=upstream.headers.get("content-type", "application/json"),
        )

    async def stream_chat(request: Request, url: str, payload: dict[str, Any]) -> StreamingResponse:
        client = httpx.AsyncClient(timeout=None)
        headers = upstream_headers(request)
        preloaded_error: bytes | None = None
        retry_attempt = 0
        while True:
            upstream_request = client.build_request("POST", url, headers=headers, json=payload)
            upstream = await client.send(upstream_request, stream=True)
            media_type = upstream.headers.get("content-type", "text/event-stream")
            if upstream.status_code not in {400, 422}:
                preloaded_error = None
                break
            preloaded_error = await upstream.aread()
            if retry_attempt >= CONTEXT_RETRY_MAX_RETRIES:
                break
            retry_payload = _context_retry_payload(payload, upstream.status_code, preloaded_error)
            if retry_payload is None:
                break
            retry_attempt += 1
            _gateway_log(
                "gateway_context_retry",
                route=request.url.path,
                model=payload.get("model"),
                original_max_tokens=payload.get("max_tokens"),
                retry_max_tokens=retry_payload.get("max_tokens"),
                retry_attempt=retry_attempt,
                max_retries=CONTEXT_RETRY_MAX_RETRIES,
                stream=True,
            )
            await upstream.aclose()
            payload = retry_payload

        async def iterator() -> AsyncIterator[bytes]:
            normalizer = ToolCallStreamNormalizer()
            try:
                if preloaded_error is not None:
                    yield preloaded_error
                    return
                if upstream.status_code != 200 or "text/event-stream" not in media_type:
                    async for chunk in upstream.aiter_bytes():
                        yield chunk
                    return
                async for line in upstream.aiter_lines():
                    if line == "":
                        yield b"\n"
                        continue
                    yield (normalizer.normalize_sse_line(line) + "\n").encode("utf-8")
            finally:
                await upstream.aclose()
                await client.aclose()

        return StreamingResponse(
            iterator(), status_code=upstream.status_code, media_type=media_type
        )

    @asgi.get("/health")
    async def health(request: Request) -> Response:
        started = time.monotonic()
        try:
            async with httpx.AsyncClient(timeout=30.0) as client:
                upstream = await client.get(
                    f"{upstream_server_root()}/health", headers=upstream_headers(request)
                )
            ok = upstream.status_code == 200
            status_code = 200 if ok else 502
            payload = {
                "ok": ok,
                "gateway": GATEWAY_APP_NAME,
                "upstream_status": upstream.status_code,
                "duration_ms": round((time.monotonic() - started) * 1000),
            }
            return JSONResponse(payload, status_code=status_code)
        except Exception as exc:
            return JSONResponse({"ok": False, "error": str(exc)}, status_code=502)

    @asgi.get("/v1/models")
    async def models(request: Request) -> Response:
        return await proxy_bytes(request, f"{upstream_api_base()}/models", method="GET")

    @asgi.post("/v1/chat/completions")
    async def chat_completions(request: Request) -> Response:
        payload = normalize_chat_request(await read_json(request))
        if payload.get("stream") is True:
            return await stream_chat(request, f"{upstream_api_base()}/chat/completions", payload)
        return await proxy_json(request, f"{upstream_api_base()}/chat/completions", payload)

    @asgi.post("/v1/responses")
    async def responses(request: Request) -> Response:
        request_id = request.headers.get("x-request-id") or _new_id("req")
        responses_payload = await read_json(request)
        chat_payload, stats = responses_request_to_chat(responses_payload)
        stream = bool(chat_payload.pop("stream", False))
        started = time.monotonic()
        headers = upstream_headers(request)
        async with httpx.AsyncClient(timeout=None) as client:
            upstream, chat_payload = await _post_json_with_context_retries(
                client,
                f"{upstream_api_base()}/chat/completions",
                headers,
                chat_payload,
                route=request.url.path,
                request_id=request_id,
            )
        duration_ms = round((time.monotonic() - started) * 1000)
        if upstream.status_code != 200:
            _gateway_log(
                "gateway_responses_error",
                request_id=request_id,
                model=chat_payload.get("model"),
                upstream_status=upstream.status_code,
                duration_ms=duration_ms,
                conversion=stats.as_dict(),
            )
            return Response(
                content=upstream.content,
                status_code=upstream.status_code,
                media_type=upstream.headers.get("content-type", "application/json"),
            )
        chat_response = upstream.json()
        response_payload = chat_response_to_responses(
            chat_response, original_request=responses_payload
        )
        _gateway_log(
            "gateway_responses",
            request_id=request_id,
            model=chat_payload.get("model"),
            tool_count=len(chat_payload.get("tools") or []),
            upstream_status=upstream.status_code,
            duration_ms=duration_ms,
            conversion=stats.as_dict(),
        )
        if stream:
            return StreamingResponse(
                encode_responses_sse(response_payload), media_type="text/event-stream"
            )
        return JSONResponse(response_payload)

    @asgi.get("/metrics")
    async def metrics(request: Request) -> Response:
        return await proxy_bytes(request, f"{upstream_server_root()}/metrics", method="GET")

    @asgi.post("/start_profile")
    async def start_profile(request: Request) -> Response:
        return await proxy_bytes(request, f"{upstream_server_root()}/start_profile", method="POST")

    @asgi.post("/stop_profile")
    async def stop_profile(request: Request) -> Response:
        return await proxy_bytes(request, f"{upstream_server_root()}/stop_profile", method="POST")

    return asgi


try:
    import modal
except ModuleNotFoundError:  # pragma: no cover - depends on local dev environment.
    modal = None

if modal is not None:
    app = modal.App(GATEWAY_APP_NAME)
    image = (
        modal.Image.debian_slim(python_version="3.12")
        .pip_install("fastapi>=0.115.0", "httpx>=0.27.0")
        .env(
            {
                UPSTREAM_API_BASE_ENV: os.getenv(UPSTREAM_API_BASE_ENV, ""),
                "PYTHONUNBUFFERED": "1",
            }
        )
    )
    secret = modal.Secret.from_name(DEFAULT_SECRET_NAME, required_keys=[DEFAULT_API_KEY_ENV])

    @app.function(image=image, secrets=[secret], timeout=60 * 60, scaledown_window=60)
    @modal.asgi_app()
    def serve() -> Any:
        return create_asgi_app()
else:
    app = None

    def serve() -> Any:
        return create_asgi_app()
