from openai.types.chat import ChatCompletionMessage
from preparedness_turn_completer.turn_completer import TurnCompleter

from evmbench.nano.grade.detect import parse_judge_result


def test_parse_judge_result_skips_empty_reasoning_message() -> None:
    completion = TurnCompleter.Completion(
        input_conversation=[],
        output_messages=[
            ChatCompletionMessage(role="assistant", content=""),
            ChatCompletionMessage(
                role="assistant",
                content='{"detected": true, "reasoning": "same root cause"}',
            ),
        ],
    )

    result = parse_judge_result(completion)

    assert result.detected is True
    assert result.reasoning == "same root cause"


def test_parse_judge_result_recovers_json_embedded_in_prose() -> None:
    completion = TurnCompleter.Completion(
        input_conversation=[],
        output_messages=[
            ChatCompletionMessage(
                role="assistant",
                content='The report matches. Result: {"detected": false, "reasoning": "different code path"}',
            ),
        ],
    )

    result = parse_judge_result(completion)

    assert result.detected is False
    assert result.reasoning == "different code path"
