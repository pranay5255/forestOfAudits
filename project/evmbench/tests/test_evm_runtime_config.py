from preparedness_turn_completer.oai_responses_turn_completer.completer import OpenAIResponsesTurnCompleter

from evmbench.nano.runtime import EVMRuntimeConfig


def test_runtime_config_supports_responses_judge_with_codex_alias() -> None:
    config = EVMRuntimeConfig(
        agent_id="codex-openrouter-v1",
        judge_model="gpt-5.3-codex",
        judge_wire_api="responses",
    )

    assert isinstance(config.turn_completer, OpenAIResponsesTurnCompleter)
    assert config.turn_completer.n_ctx == 400_000
