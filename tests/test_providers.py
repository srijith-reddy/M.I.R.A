from __future__ import annotations

import pytest

from mira.runtime.llm_types import Message
from mira.runtime.providers import _AnthropicAdapter, provider_for


@pytest.mark.parametrize(
    "model,expected",
    [
        ("gpt-4o", "openai"),
        ("gpt-4o-mini", "openai"),
        ("o1-preview", "openai"),
        ("text-embedding-3-small", "openai"),
        ("claude-opus-4-7", "anthropic"),
        ("claude-sonnet-4-6", "anthropic"),
        ("llama-3.1-8b-instant", "groq"),
        ("llama-3.3-70b-versatile", "groq"),
        ("mixtral-8x7b-32768", "groq"),
        ("gemma-7b-it", "groq"),
        # Unknown families fall back to openai (safe default — openai key is
        # always required for embeddings).
        ("some-future-model", "openai"),
    ],
)
def test_provider_for_prefix_dispatch(model: str, expected: str) -> None:
    assert provider_for(model) == expected


def test_anthropic_translate_basic_messages() -> None:
    adapter = _AnthropicAdapter(api_key="x")
    sys_text, msgs = adapter._translate(
        [
            Message(role="system", content="be helpful"),
            Message(role="user", content="hi"),
            Message(role="assistant", content="hello"),
        ]
    )
    assert sys_text == "be helpful"
    assert msgs == [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]


def test_anthropic_translate_multiple_system_messages_concat() -> None:
    adapter = _AnthropicAdapter(api_key="x")
    sys_text, _ = adapter._translate(
        [
            Message(role="system", content="rule 1"),
            Message(role="system", content="rule 2"),
            Message(role="user", content="ok"),
        ]
    )
    assert sys_text == "rule 1\n\nrule 2"


def test_anthropic_translate_tool_result_shape() -> None:
    adapter = _AnthropicAdapter(api_key="x")
    _, msgs = adapter._translate(
        [
            Message(role="user", content="what's the weather"),
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "call_1",
                        "type": "function",
                        "function": {
                            "name": "weather.get",
                            "arguments": '{"city":"Austin"}',
                        },
                    }
                ],
            ),
            Message(role="tool", tool_call_id="call_1", content='{"temp":72}'),
        ]
    )
    # Assistant block should become a mixed content array with tool_use.
    assert msgs[1]["role"] == "assistant"
    blocks = msgs[1]["content"]
    tool_use_blocks = [b for b in blocks if b["type"] == "tool_use"]
    assert len(tool_use_blocks) == 1
    assert tool_use_blocks[0]["id"] == "call_1"
    assert tool_use_blocks[0]["name"] == "weather.get"
    assert tool_use_blocks[0]["input"] == {"city": "Austin"}

    # Tool message should become a user message with tool_result.
    assert msgs[2]["role"] == "user"
    result_block = msgs[2]["content"][0]
    assert result_block["type"] == "tool_result"
    assert result_block["tool_use_id"] == "call_1"
    assert result_block["content"] == '{"temp":72}'


def test_anthropic_translate_tool_calls_with_bad_json_preserves_raw() -> None:
    adapter = _AnthropicAdapter(api_key="x")
    _, msgs = adapter._translate(
        [
            Message(
                role="assistant",
                content=None,
                tool_calls=[
                    {
                        "id": "call_x",
                        "type": "function",
                        "function": {
                            "name": "noop",
                            "arguments": "not valid json",
                        },
                    }
                ],
            ),
        ]
    )
    tool_use = [b for b in msgs[0]["content"] if b["type"] == "tool_use"][0]
    # Malformed JSON must not crash — it's stashed under _raw so we can
    # debug without losing data.
    assert tool_use["input"] == {"_raw": "not valid json"}


@pytest.mark.asyncio
async def test_anthropic_stream_not_implemented() -> None:
    adapter = _AnthropicAdapter(api_key="x")
    with pytest.raises(NotImplementedError):
        async for _ in adapter.stream(
            model="claude-sonnet-4-6",
            messages=[Message(role="user", content="hi")],
            temperature=0.0,
            max_tokens=10,
        ):
            pass
