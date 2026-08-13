"""The provider interface the agent loop is written against.

A provider owns its own conversation history in whatever shape its API wants.
That matters: some models return opaque blocks that must be echoed back
verbatim on the next turn, and flattening every provider into one neutral
message list would quietly corrupt them. So the agent drives the conversation
through this small protocol and never inspects the history itself.

One turn of the loop:

    provider.seed(history, question)      once, before the loop
    async for text in provider.stream_turn():   yields answer fragments
    provider.pending_tool_calls           what the model wants to run
    provider.add_tool_results(results)    hand the outcomes back
"""

from __future__ import annotations

import abc
from collections.abc import AsyncIterator
from dataclasses import dataclass, field
from typing import Any

from nl2sql.tools import Tool


class ProviderError(RuntimeError):
    """A provider could not complete a turn.

    The message is shown to the user, so keep it short and actionable and never
    include an API key or a raw payload.
    """


@dataclass(frozen=True)
class ToolCall:
    """A tool invocation requested by the model."""

    id: str
    name: str
    arguments: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class ToolResult:
    """The outcome of a tool invocation, ready to hand back to the model."""

    call_id: str
    name: str
    content: str


class LLMProvider(abc.ABC):
    """Streaming, tool-using chat against one LLM API."""

    #: Short provider name, used in logs and in the health endpoint.
    name: str

    def __init__(self, system_prompt: str, tools: tuple[Tool, ...]) -> None:
        """Store the static parts of every request.

        Args:
            system_prompt: The assembled schema and rules prompt.
            tools: The tools to expose to the model.
        """
        self.system_prompt = system_prompt
        self.tools = tools
        self._pending: list[ToolCall] = []

    @property
    def pending_tool_calls(self) -> list[ToolCall]:
        """Tool calls the last turn asked for, empty when the model is done."""
        return list(self._pending)

    @abc.abstractmethod
    def seed(self, history: list[dict[str, str]], question: str) -> None:
        """Initialise the conversation.

        Args:
            history: Prior turns as `{"role": ..., "content": ...}` mappings.
            question: The new user question.
        """

    @abc.abstractmethod
    def stream_turn(self) -> AsyncIterator[str]:
        """Run one assistant turn, yielding answer text as it arrives.

        Any tool calls the model makes during the turn are collected and
        exposed through `pending_tool_calls` once the iterator is exhausted.

        Yields:
            Fragments of the natural language answer.

        Raises:
            ProviderError: If the API call fails or the model declines.
        """

    @abc.abstractmethod
    def add_tool_results(self, results: list[ToolResult]) -> None:
        """Append tool outcomes to the conversation for the next turn.

        Args:
            results: One result per pending tool call, in the same order.
        """
