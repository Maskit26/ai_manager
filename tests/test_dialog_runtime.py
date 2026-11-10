import asyncio

from ai_seller_core.dialog.runtime import (
    DialogRuntime,
    InboundMessage,
    ModelTurn,
    ToolCall,
)


class Sanitizer:
    def sanitize(self, text: str) -> str:
        return text.replace("buyer@example.com", "[email]")


class Provider:
    def __init__(self) -> None:
        self.calls = 0
        self.seen_messages: list[dict[str, object]] = []

    async def generate(self, *, messages, tools) -> ModelTurn:
        self.calls += 1
        self.seen_messages = messages
        if self.calls == 1:
            assert tools == [{"name": "search_products"}]
            return ModelTurn(
                text=None,
                tool_calls=(
                    ToolCall(
                        name="search_products",
                        arguments={"query": "shoes"},
                        call_id="call-1",
                    ),
                ),
            )
        return ModelTurn(text="Here are matching products.")


class Tools:
    def available_tools(self, *, project_id: str) -> list[dict[str, object]]:
        return [{"name": "search_products"}]

    async def execute(
        self,
        *,
        project_id: str,
        session_id: str,
        call: ToolCall,
    ) -> dict[str, object]:
        assert project_id == "project-1"
        assert session_id == "session-1"
        return {"matches": 2}


def test_runtime_sanitizes_and_executes_tools() -> None:
    provider = Provider()
    runtime = DialogRuntime(provider=provider, tools=Tools(), sanitizer=Sanitizer())
    message = InboundMessage(
        project_id="project-1",
        session_id="session-1",
        text="Find shoes, buyer@example.com",
        correlation_id="correlation-1",
    )

    answer = asyncio.run(runtime.respond(message))

    assert answer == "Here are matching products."
    assert provider.calls == 2
    assert provider.seen_messages[0]["content"] == "Find shoes, [email]"
    assert provider.seen_messages[-1] == {
        "role": "tool",
        "tool_call_id": "call-1",
        "content": {"matches": 2},
    }


def test_runtime_blocks_unsanitizable_message() -> None:
    class RejectingSanitizer:
        def sanitize(self, text: str) -> None:
            return None

    provider = Provider()
    runtime = DialogRuntime(
        provider=provider,
        tools=Tools(),
        sanitizer=RejectingSanitizer(),
    )
    message = InboundMessage(
        project_id="project-1",
        session_id="session-1",
        text="unparseable personal data",
        correlation_id="correlation-1",
    )

    try:
        asyncio.run(runtime.respond(message))
    except ValueError as error:
        assert "could not be sanitized" in str(error)
    else:
        raise AssertionError("Expected unsanitizable input to be rejected")
    assert provider.calls == 0


def test_runtime_includes_persisted_history_before_inbound_text() -> None:
    class HistoryProvider:
        async def generate(self, *, messages, tools) -> ModelTurn:
            assert messages == [
                {"role": "assistant", "content": "Earlier answer"},
                {"role": "user", "content": "Current question"},
            ]
            return ModelTurn(text="Next answer")

    runtime = DialogRuntime(
        provider=HistoryProvider(),
        tools=Tools(),
        sanitizer=Sanitizer(),
    )
    message = InboundMessage(
        project_id="project-1",
        session_id="session-1",
        text="Current question",
        correlation_id="correlation-1",
        history=({"role": "assistant", "content": "Earlier answer"},),
    )

    assert asyncio.run(runtime.respond(message)) == "Next answer"
