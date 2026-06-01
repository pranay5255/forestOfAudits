import asyncio
import copy
import json

import pytest

from evmbench.vllm import gateway


def test_responses_second_turn_payload_converts_to_chat_without_reasoning_items() -> None:
    payload = {
        "model": "Qwen/Qwen3.6-35B-A3B-FP8",
        "instructions": "Act as an agent.",
        "input": [
            {
                "role": "developer",
                "content": [{"type": "input_text", "text": "Use tools when needed."}],
            },
            {"role": "user", "content": [{"type": "input_text", "text": "Run a command."}]},
            {"type": "reasoning", "id": "rs_1", "summary": [{"text": "hidden"}]},
            {
                "type": "function_call",
                "call_id": "call_1",
                "name": "bash",
                "arguments": json.dumps({"command": "printf ok"}),
            },
            {
                "type": "function_call_output",
                "call_id": "call_1",
                "output": "ok",
            },
        ],
        "tools": [
            {
                "type": "function",
                "name": "bash",
                "description": "Run bash.",
                "parameters": {"type": "object"},
            }
        ],
        "tool_choice": {"type": "function", "name": "bash"},
        "max_output_tokens": 128,
        "temperature": 0,
    }

    chat, stats = gateway.responses_request_to_chat(payload)

    assert chat["model"] == payload["model"]
    assert chat["max_tokens"] == 128
    assert chat["chat_template_kwargs"] == {"enable_thinking": False}
    assert chat["tool_choice"] == {"type": "function", "function": {"name": "bash"}}
    assert chat["tools"][0]["function"]["name"] == "bash"
    assert stats.dropped_reasoning_items == 1
    assert not any(message.get("type") == "reasoning" for message in chat["messages"])
    assert chat["messages"][0] == {
        "role": "system",
        "content": "Act as an agent.\n\nUse tools when needed.",
    }
    assert chat["messages"][2]["role"] == "assistant"
    assert chat["messages"][2]["tool_calls"][0]["function"]["name"] == "bash"
    assert chat["messages"][3] == {"role": "tool", "tool_call_id": "call_1", "content": "ok"}


def test_chat_tool_call_converts_to_responses_function_call() -> None:
    chat_payload = {
        "id": "chatcmpl_1",
        "created": 123,
        "model": "qwen",
        "choices": [
            {
                "message": {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": "call_abc",
                            "type": "function",
                            "function": {
                                "name": "bash",
                                "arguments": json.dumps({"command": "echo ok"}),
                            },
                        }
                    ],
                }
            }
        ],
    }

    response = gateway.chat_response_to_responses(chat_payload)

    item = response["output"][0]
    assert item["type"] == "function_call"
    assert item["id"].startswith("fc_")
    assert item["call_id"] == "call_abc"
    assert item["name"] == "bash"
    assert item["arguments"] == json.dumps({"command": "echo ok"})
    assert item["status"] == "completed"


def test_chat_text_converts_to_responses_output_text_message() -> None:
    chat_payload = {
        "id": "chatcmpl_1",
        "created": 123,
        "model": "qwen",
        "choices": [{"message": {"role": "assistant", "content": "done"}}],
    }

    response = gateway.chat_response_to_responses(chat_payload)

    item = response["output"][0]
    assert item["type"] == "message"
    assert item["id"].startswith("msg_")
    assert item["status"] == "completed"
    assert item["content"] == [{"type": "output_text", "text": "done", "annotations": []}]
    assert response["output_text"] == "done"


def test_chat_stream_tool_call_normalizer_reuses_first_function_name() -> None:
    normalizer = gateway.ToolCallStreamNormalizer()
    first = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"name": "bash", "arguments": ""}},
                    ]
                }
            }
        ]
    }
    second = {
        "choices": [
            {
                "delta": {
                    "tool_calls": [
                        {"index": 0, "function": {"name": None, "arguments": '{"command"'}},
                    ]
                }
            }
        ]
    }

    normalizer.normalize_chunk(first)
    normalized = normalizer.normalize_chunk(second)

    assert normalized["choices"][0]["delta"]["tool_calls"][0]["function"]["name"] == "bash"


def test_gateway_routes_do_not_validate_request_as_query_param() -> None:
    pytest.importorskip("fastapi")
    asgi = gateway.create_asgi_app()
    route_params = {
        route.path: [param.name for param in route.dependant.query_params]
        for route in asgi.routes
        if hasattr(route, "dependant")
    }

    for path in [
        "/health",
        "/v1/models",
        "/v1/chat/completions",
        "/v1/responses",
        "/metrics",
        "/start_profile",
        "/stop_profile",
    ]:
        assert route_params[path] == []


def test_responses_to_chat_drops_unsupported_namespace_tools() -> None:
    payload = {
        "model": "qwen",
        "input": "Use bash.",
        "tools": [
            {"type": "function", "name": "bash", "parameters": {"type": "object"}},
            {
                "type": "namespace",
                "name": "multi_agent_v1",
                "tools": [
                    {"type": "function", "name": "spawn_agent", "parameters": {"type": "object"}}
                ],
            },
        ],
    }

    chat, stats = gateway.responses_request_to_chat(payload)

    assert stats.tools == 1
    assert chat["tools"] == [
        {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    ]


def test_responses_to_chat_moves_system_messages_to_front() -> None:
    payload = {
        "model": "qwen",
        "input": [
            {"role": "user", "content": "Do work."},
            {"role": "developer", "content": "Follow policy."},
            {"role": "assistant", "content": "ok"},
        ],
    }

    chat, _stats = gateway.responses_request_to_chat(payload)

    assert [message["role"] for message in chat["messages"]] == ["system", "user", "assistant"]
    assert chat["messages"][0]["content"] == "Follow policy."


def test_normalize_chat_request_filters_tools_and_moves_system_messages() -> None:
    payload = {
        "model": "qwen",
        "messages": [
            {"role": "user", "content": "Run."},
            {"role": "developer", "content": "Policy."},
        ],
        "tools": [
            {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}},
            {"type": "namespace", "name": "multi_agent_v1", "tools": []},
        ],
    }

    chat = gateway.normalize_chat_request(payload)

    assert [message["role"] for message in chat["messages"]] == ["system", "user"]
    assert chat["tools"] == [
        {"type": "function", "function": {"name": "bash", "parameters": {"type": "object"}}}
    ]
    assert chat["chat_template_kwargs"] == {"enable_thinking": False}


def test_chat_usage_converts_to_responses_usage_shape() -> None:
    usage = gateway.chat_usage_to_responses_usage(
        {"prompt_tokens": 11, "completion_tokens": 7, "total_tokens": 18}
    )

    assert usage["input_tokens"] == 11
    assert usage["output_tokens"] == 7
    assert usage["total_tokens"] == 18
    assert usage["input_tokens_details"] == {"cached_tokens": 0}
    assert usage["output_tokens_details"] == {"reasoning_tokens": 0}


def test_responses_completed_event_contains_responses_usage_fields() -> None:
    response = gateway.chat_response_to_responses(
        {
            "id": "chatcmpl_1",
            "created": 123,
            "model": "qwen",
            "choices": [{"message": {"role": "assistant", "content": "done"}}],
            "usage": {"prompt_tokens": 3, "completion_tokens": 2, "total_tokens": 5},
        }
    )
    completed = gateway.responses_sse_events(response)[-1][1]

    assert completed["response"]["usage"]["input_tokens"] == 3
    assert completed["response"]["usage"]["output_tokens"] == 2


def test_context_retry_payload_reduces_max_tokens_for_one_token_overflow() -> None:
    payload = {"model": "qwen", "messages": [], "max_tokens": 2048}
    error = {
        "error": {
            "message": (
                "This model's maximum context length is 32768 tokens. "
                "However, you requested 2048 output tokens and your prompt "
                "contains at least 30721 input tokens, for a total of at least 32769 tokens."
            )
        }
    }

    retry = gateway._context_retry_payload(payload, 400, json.dumps(error))

    assert retry is not None
    assert retry["max_tokens"] == 1535
    assert payload["max_tokens"] == 2048


def test_context_retry_payload_ignores_unrecoverable_context_errors() -> None:
    payload = {"model": "qwen", "messages": [], "max_tokens": 2048}
    error = {
        "error": {
            "message": (
                "This model's maximum context length is 32768 tokens. "
                "However, you requested 2048 output tokens and your prompt "
                "contains at least 32768 input tokens, for a total of at least 34816 tokens."
            )
        }
    }

    assert gateway._context_retry_payload(payload, 400, json.dumps(error)) is None


def test_context_retry_payload_trims_largest_nested_tool_content() -> None:
    large_content = "a" * 20000
    payload = {
        "model": "qwen",
        "messages": [
            {
                "role": "assistant",
                "tool_calls": [
                    {
                        "type": "function",
                        "function": {"name": "bash", "arguments": large_content},
                    }
                ],
            }
        ],
        "max_tokens": 2048,
    }
    error = {
        "error": {
            "message": (
                "This model's maximum context length is 32768 tokens. "
                "However, you requested 2048 output tokens and your prompt "
                "contains at least 32768 input tokens, for a total of at least 34816 tokens."
            )
        }
    }

    retry = gateway._context_retry_payload(payload, 400, json.dumps(error))

    assert retry is not None
    arguments = retry["messages"][0]["tool_calls"][0]["function"]["arguments"]
    assert len(arguments) < len(large_content)
    assert "gateway truncated" in arguments
    assert retry["max_tokens"] == 512
    assert payload["messages"][0]["tool_calls"][0]["function"]["arguments"] == large_content


class _FakeGatewayResponse:
    def __init__(self, status_code: int, content: bytes | str = b"{}") -> None:
        self.status_code = status_code
        self.content = content.encode("utf-8") if isinstance(content, str) else content


class _FakePostClient:
    def __init__(self, responses: list[_FakeGatewayResponse]) -> None:
        self._responses = responses
        self.payloads: list[dict[str, object]] = []

    async def post(
        self, url: str, *, headers: dict[str, str], json: dict[str, object]
    ) -> _FakeGatewayResponse:
        self.payloads.append(copy.deepcopy(json))
        return self._responses.pop(0)


def _context_error(*, requested: int, input_tokens: int, context: int = 32768) -> str:
    return json.dumps(
        {
            "error": {
                "message": (
                    f"This model's maximum context length is {context} tokens. "
                    f"However, you requested {requested} output tokens and your prompt "
                    f"contains at least {input_tokens} input tokens, "
                    f"for a total of at least {requested + input_tokens} tokens."
                )
            }
        }
    )


def test_post_json_context_retry_retries_second_overflow_with_new_accounting() -> None:
    client = _FakePostClient(
        [
            _FakeGatewayResponse(400, _context_error(requested=2048, input_tokens=30721)),
            _FakeGatewayResponse(422, _context_error(requested=1535, input_tokens=31200)),
            _FakeGatewayResponse(200, b'{"ok":true}'),
        ]
    )

    upstream, payload = asyncio.run(
        gateway._post_json_with_context_retries(
            client,
            "https://vllm.example.test/v1/chat/completions",
            {"content-type": "application/json"},
            {"model": "qwen", "messages": [], "max_tokens": 2048},
        )
    )

    assert upstream.status_code == 200
    assert [item["max_tokens"] for item in client.payloads] == [2048, 1535, 1056]
    assert payload["max_tokens"] == 1056
