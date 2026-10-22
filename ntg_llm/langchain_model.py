"""LangChain chat-model interface over NTG's existing structured text client."""

from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.outputs import ChatGeneration, ChatResult
from langsmith import tracing_context
from pydantic import Field

from .agent_trace import AgentTrace
from .errors import RoutingError
from .schemas import Answer


class NTGChatModel(BaseChatModel):
    """Translate text messages only; provider selection/recovery belongs to LLMClient.

    The router validates an Answer envelope. Its answer is LangChain ReAct text,
    not the final application schema. Native tool calling is intentionally absent.
    """

    client: Any = Field(exclude=True, repr=False)
    trace: AgentTrace = Field(default_factory=AgentTrace, exclude=True, repr=False)
    cache: bool = False
    disable_streaming: bool = True

    @property
    def _llm_type(self):
        return "ntg-router"

    def _generate(self, messages: list[BaseMessage], stop=None, run_manager=None, **kwargs):
        if stop or kwargs:
            raise RoutingError(400, "NTGChatModel supports nonstreaming text without extra options")
        roles = {"system": "system", "human": "user", "ai": "assistant"}
        converted = []
        for message in messages:
            if message.type not in roles or not isinstance(message.content, str):
                raise RoutingError(400, "NTGChatModel supports system, human, and AI text messages")
            converted.append({"role": roles[message.type], "content": message.content})
        try:
            with tracing_context(enabled=False):
                response = self.client.chat.completions.create(
                    model="router", messages=converted, response_model=Answer
                )
        except RoutingError as error:
            self.trace.record_model(error.diagnostics)
            raise
        self.trace.record_model(response.diagnostics, response.model)
        return ChatResult(
            generations=[
                ChatGeneration(message=AIMessage(content=response.choices[0].message.parsed.answer))
            ],
            llm_output={"model_name": response.model},
        )
