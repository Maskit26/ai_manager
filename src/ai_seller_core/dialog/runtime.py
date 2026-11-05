from dataclasses import dataclass
from typing import Protocol


@dataclass(frozen=True)
class InboundMessage:
    project_id: str
    session_id: str
    text: str
    correlation_id: str


@dataclass(frozen=True)
class ToolCall:
    name: str
    arguments: dict[str, object]
    call_id: str


@dataclass(frozen=True)
class ModelTurn:
    text: str | None
    tool_calls: tuple[ToolCall, ...] = ()


class LLMProvider(Protocol):
    async def generate(
        self,
        *,
        messages: list[dict[str, object]],
        tools: list[dict[str, object]],
    ) -> ModelTurn: ...


class ToolExecutor(Protocol):
    def available_tools(self, *, project_id: str) -> list[dict[str, object]]: ...

    async def execute(
        self, *, project_id: str, session_id: str, call: ToolCall
    ) -> dict[str, object]: ...


class MessageSanitizer(Protocol):
    def sanitize(self, text: str) -> str | None: ...


class DialogRuntime:
    def __init__(
        self,
        *,
        provider: LLMProvider,
        tools: ToolExecutor,
        sanitizer: MessageSanitizer,
        max_tool_rounds: int = 4,
    ) -> None:
        self.provider = provider
        self.tools = tools
        self.sanitizer = sanitizer
        self.max_tool_rounds = max_tool_rounds

    async def respond(self, message: InboundMessage) -> str:
        cleaned_text = self.sanitizer.sanitize(message.text)
        if cleaned_text is None:
            raise ValueError("Message could not be sanitized; do not send it to the LLM")

        model_messages: list[dict[str, object]] = [{"role": "user", "content": cleaned_text}]
        available_tools = self.tools.available_tools(project_id=message.project_id)
        for round_number in range(self.max_tool_rounds + 1):
            turn = await self.provider.generate(
                messages=model_messages,
                tools=available_tools,
            )
            if not turn.tool_calls:
                if turn.text is None:
                    raise RuntimeError("LLM returned neither text nor tool calls")
                return turn.text

            if round_number == self.max_tool_rounds:
                raise RuntimeError("LLM exceeded the configured tool-call round limit")
            model_messages.append(
                {
                    "role": "assistant",
                    "tool_calls": [
                        {
                            "id": call.call_id,
                            "name": call.name,
                            "arguments": call.arguments,
                        }
                        for call in turn.tool_calls
                    ],
                }
            )
            for call in turn.tool_calls:
                result = await self.tools.execute(
                    project_id=message.project_id,
                    session_id=message.session_id,
                    call=call,
                )
                model_messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.call_id,
                        "content": result,
                    }
                )
        raise RuntimeError("Dialog runtime exited without a model response")
