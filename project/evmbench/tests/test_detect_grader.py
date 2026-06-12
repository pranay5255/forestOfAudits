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
